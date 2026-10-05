"""Jarvis's voice.

- Microsoft neural voices (edge-tts), picked per language from the script of the text.
- "Me mode": the same speech re-voiced in the user's own voice (voiceclone.py, GPU).
- Sentences are synthesised on one thread and played on another, so the next one is
  ready while the current one is still playing. stop() cuts speech off instantly.
- level() gives the loudness of what's playing right now (drives the lip-sync and bars).
"""

import asyncio
import io
import logging
import queue
import threading
import time

import collections

import numpy as np
import pygame
import sounddevice as sd
import soundfile

OUT_SR = 44100        # the speakers' native rate: no resampling in the driver

log = logging.getLogger("jarvis.speech")

# Unicode script ranges -> language code.
SCRIPTS = [
    ("te", 0x0C00, 0x0C7F), ("hi", 0x0900, 0x097F), ("ta", 0x0B80, 0x0BFF), ("kn", 0x0C80, 0x0CFF),
    ("ml", 0x0D00, 0x0D7F), ("bn", 0x0980, 0x09FF), ("gu", 0x0A80, 0x0AFF), ("ur", 0x0600, 0x06FF),
]

JARVIS_VOICES = {
    "en": "kokoro:bm_george", "te": "te-IN-MohanNeural", "hi": "hi-IN-MadhurNeural",
    "ta": "ta-IN-ValluvarNeural", "kn": "kn-IN-GaganNeural", "ml": "ml-IN-MidhunNeural",
    "bn": "bn-IN-BashkarNeural", "gu": "gu-IN-NiranjanNeural", "ur": "ur-IN-SalmanNeural",
}
# Base voices re-voiced into the user's voice.
ME_VOICES = dict(JARVIS_VOICES, en="kokoro:am_michael")
# Online voices used for English until the local Kokoro engine has loaded (or if it can't).
EDGE_FALLBACK = {"kokoro:bm_george": "en-GB-RyanNeural", "kokoro:am_michael": "en-IN-PrabhatNeural"}


def language_of(text: str) -> str:
    counts = {}
    for ch in text:
        o = ord(ch)
        for lang, lo, hi in SCRIPTS:
            if lo <= o <= hi:
                counts[lang] = counts.get(lang, 0) + 1
                break
    letters = sum(ch.isalpha() for ch in text) or 1
    if counts:
        lang, n = max(counts.items(), key=lambda kv: kv[1])
        if n / letters > 0.3:
            return lang
    return "en"


async def _edge_tts(text, voice, rate):
    import edge_tts
    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


class Speaker:
    def __init__(self, voice_override=None, rate="+6%", muted=False, me_mode=False):
        self.voice_override = voice_override
        self.rate = rate
        self.muted = muted
        self.me_mode = me_mode
        self.cloner = None               # loaded in the background (GPU model)
        self.cloner_error = None
        self.kokoro = None               # fast local English TTS (GPU), loaded in the background
        self._kokoro_lock = threading.Lock()
        self._loop = None
        self._gen = 0
        self._text_q = queue.Queue()
        self._audio_q = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        # One continuous, well-buffered output stream at the device's native rate: sentences are
        # queued into it back-to-back, so there are no gaps or crackles between or within them.
        self._play = collections.deque()   # [gen, samples, position]
        self._play_lock = threading.Lock()
        self._level = 0.0
        self.underflows = 0                 # audio-buffer underruns (each one is an audible glitch)
        self._stream = sd.OutputStream(samplerate=OUT_SR, channels=1, dtype="float32", latency="high",
                                       callback=self._callback)
        self._stream.start()
        pygame.mixer.pre_init(buffer=2048)          # only used for the short chimes
        pygame.mixer.init()
        self._chime = self._make_chime([880, 1320], 0.07)
        self._chime_off = self._make_chime([990, 660], 0.06)
        threading.Thread(target=self._synth_worker, daemon=True, name="tts-synth").start()
        threading.Thread(target=self._play_worker, daemon=True, name="tts-play").start()

    # ------------------------------------------------------------------ public
    def load_cloner_async(self, on_ready=lambda ok: None):
        """Load the local English voice (Kokoro) and then the voice cloner, both on the GPU."""
        def load():
            try:
                import torch
                from kokoro import KPipeline
                kp = KPipeline(lang_code="b", device="cuda" if torch.cuda.is_available() else "cpu")
                for v in ("bm_george", "am_michael"):     # load + warm up both voices
                    list(kp("Ready.", voice=v))
                self.kokoro = kp
                log.info("kokoro ready")
            except Exception:
                log.exception("kokoro unavailable; using online English voices")
            try:
                from voiceclone import VoiceCloner, synth_mp3
                if not VoiceCloner.available():
                    raise RuntimeError("no voice sample; run build_avatar.py")
                cloner = VoiceCloner()
                for lang in ("en", "te", "hi"):          # precompute + warm up the GPU
                    self._source_embedding(ME_VOICES[lang], cloner)
                cloner.convert(np.zeros(cloner.sr, dtype=np.float32), self._source_embedding(ME_VOICES["en"], cloner))
                self.cloner = cloner
                log.info("voice clone ready")
                on_ready(True)
            except Exception as e:
                log.exception("voice clone unavailable")
                self.cloner = None
                self.cloner_error = str(e)
                on_ready(False)
        threading.Thread(target=load, daemon=True, name="voice-load").start()

    def reload_voice(self):
        """Re-read assets/voice_ref.wav (after 'learn my voice')."""
        if self.cloner is not None:
            from voiceclone import REF_WAV
            self.cloner.target = self.cloner._embedding_of_files([REF_WAV])

    def say(self, text: str):
        text = text.strip()
        if not text or self.muted:
            return
        with self._lock:
            self._pending += 1
        self._text_q.put((self._gen, text))

    def stop(self):
        with self._lock:
            self._gen += 1
            self._pending = 0
        for q in (self._text_q, self._audio_q):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        with self._play_lock:
            self._play.clear()

    def busy(self) -> bool:
        return self._pending > 0 or bool(self._play)

    def wait(self, cancelled=lambda: False):
        while self.busy() and not cancelled():
            time.sleep(0.04)

    def level(self) -> float:
        return self._level

    def _callback(self, outdata, frames, _time, _status):
        """Audio thread: copy queued speech into the device buffer (silence when there's none)."""
        if _status.output_underflow:
            self.underflows += 1
        out = outdata[:, 0]
        filled = finished = 0
        with self._play_lock:
            while filled < frames and self._play:
                item = self._play[0]
                samples, pos = item[1], item[2]
                take = min(frames - filled, len(samples) - pos)
                out[filled:filled + take] = samples[pos:pos + take]
                item[2] = pos + take
                filled += take
                if item[2] >= len(samples):
                    self._play.popleft()
                    finished += 1
        out[filled:] = 0
        rms = float(np.sqrt(np.mean(out[:filled] ** 2))) if filled else 0.0
        self._level = 0.6 * self._level + 0.4 * min(1.0, rms * 7)
        if finished:
            with self._lock:
                self._pending = max(0, self._pending - finished)

    def chime(self, on=True):
        (self._chime if on else self._chime_off).play()

    # ----------------------------------------------------------------- workers
    def _voice_for(self, text, me):
        lang = language_of(text)
        if self.voice_override and lang == "en" and not me:
            return self.voice_override
        return (ME_VOICES if me else JARVIS_VOICES).get(lang, JARVIS_VOICES["en"])

    def _render(self, text, voice, sr, rate="+0%"):
        """Synthesise text with a voice -> float audio at sample rate sr."""
        if voice.startswith("kokoro:"):
            if self.kokoro is not None:
                with self._kokoro_lock:
                    chunks = [a.numpy() if hasattr(a, "numpy") else np.asarray(a)
                              for _, _, a in self.kokoro(text, voice=voice.split(":", 1)[1], speed=1.05)]
                audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(1, np.float32)
                if sr != 24000:
                    import librosa
                    audio = librosa.resample(audio, orig_sr=24000, target_sr=sr)
                return audio
            voice = EDGE_FALLBACK.get(voice, "en-GB-RyanNeural")
        if threading.current_thread().name == "tts-synth":
            mp3 = self._loop.run_until_complete(_edge_tts(text, voice, rate))
        else:
            mp3 = asyncio.run(_edge_tts(text, voice, rate))
        return self._decode(mp3, sr)[0]

    def _source_embedding(self, voice, cloner=None):
        """Cloner embedding for a base voice (Kokoro or edge-tts)."""
        cloner = cloner or self.cloner
        if voice.startswith("kokoro:") and self.kokoro is None:
            voice = EDGE_FALLBACK.get(voice, voice)
        return cloner.source_embedding(voice, make_audio=lambda t: self._render(t, voice, cloner.sr))

    def _synth_worker(self):
        self._loop = asyncio.new_event_loop()
        while True:
            gen, text = self._text_q.get()
            if gen != self._gen:
                continue
            me = self.me_mode and self.cloner is not None
            voice = self._voice_for(text, me)
            try:
                sr = self.cloner.sr if me else 24000
                audio = self._render(text, voice, sr, "+0%" if me else self.rate)
                if me:
                    audio = self.cloner.convert(audio, self._source_embedding(voice))
                item = (gen, text, audio, sr)
            except Exception as e:
                log.warning("speech synthesis failed (%s); using offline voice", e)
                item = (gen, text, None, None)
            if gen == self._gen:
                self._audio_q.put(item)

    def _play_worker(self):
        """Resample finished sentences to the device rate and queue them on the stream."""
        import librosa
        while True:
            gen, text, audio, sr = self._audio_q.get()
            if gen != self._gen:
                continue
            if audio is None:
                try:
                    self._offline_say(text)
                finally:
                    with self._lock:
                        if gen == self._gen and self._pending > 0:
                            self._pending -= 1
                continue
            try:
                if sr != OUT_SR:
                    audio = librosa.resample(audio, orig_sr=sr, target_sr=OUT_SR)
                audio = np.clip(audio, -1, 1).astype(np.float32)
                fade = min(len(audio) // 4, int(OUT_SR * 0.006))       # 6 ms fades: no clicks at the joins
                if fade:
                    ramp = np.linspace(0, 1, fade, dtype=np.float32)
                    audio[:fade] *= ramp
                    audio[-fade:] *= ramp[::-1]
                pause = np.zeros(int(OUT_SR * 0.09), np.float32)      # a natural breath between sentences
                with self._play_lock:
                    if gen == self._gen:
                        self._play.append([gen, np.concatenate([audio, pause]), 0])
            except Exception:
                log.exception("playback failed")
                with self._lock:
                    if gen == self._gen and self._pending > 0:
                        self._pending -= 1

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _decode(mp3: bytes, sr: int):
        import librosa
        audio, _ = librosa.load(io.BytesIO(mp3), sr=sr)
        return audio.astype(np.float32), sr

    @staticmethod
    def _to_wav(audio, sr) -> bytes:
        buf = io.BytesIO()
        soundfile.write(buf, np.clip(audio, -1, 1), sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    @staticmethod
    def _envelope(audio, sr):
        hop = sr // 50
        n = len(audio) // hop
        if n == 0:
            return np.zeros(1)
        rms = np.sqrt(np.mean(audio[: n * hop].reshape(n, hop) ** 2, axis=1))
        peak = np.percentile(rms, 95) or 1.0
        return np.clip(rms / peak, 0, 1)

    @staticmethod
    def _offline_say(text):
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 185)
        engine.say(text)
        engine.runAndWait()
        engine.stop()

    @staticmethod
    def _make_chime(freqs, dur):
        sr, _, channels = pygame.mixer.get_init()   # the device may force its own rate/channels
        parts = []
        for f in freqs:
            t = np.arange(int(sr * dur)) / sr
            env = np.minimum(1, t / 0.005) * np.exp(-t * 18)
            parts.append(np.sin(2 * np.pi * f * t) * env)
        wave = (np.concatenate(parts) * 0.22 * 32767).astype(np.int16)
        if channels > 1:
            wave = np.ascontiguousarray(np.repeat(wave[:, None], channels, axis=1))
        return pygame.sndarray.make_sound(wave)
