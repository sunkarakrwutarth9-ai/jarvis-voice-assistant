"""Microphone thread: offline "Hey Jarvis" / "OK Jarvis" wake word, then records one command.

Modes:
  wake   - idle; waiting for the wake word
  record - capturing a command until the speaker goes quiet
  busy   - Jarvis is thinking/speaking; the wake word still works so you can interrupt
"""

import collections
import logging
import os
import threading
import time

import numpy as np
import pyaudio

log = logging.getLogger("jarvis.listener")

import re

# How speech recognition tends to spell "Jarvis" in Indian English.
KEYWORD = re.compile(r"\b(jarvis|jarvish|jervis|jarves|jarwis|charvis|javis|jaarvis|ultron|ultran|ultra on|altron|alton|ultrun|ultr0n|ultroon|atomo|atomu|atom o|a tomo|atom oh|automo|attomo|atamo|adamo)\b|అల్ట్రాన్|ఆల్ట్రాన్|अल्ट्रॉन|अल्ट्रोन|అటోమో|ఆటోమో|అటామో|एटमो|ऐटमो|आटोमो|अटोमो|एटोमो", re.I)

RATE = 16000
FRAME = 1280            # 80 ms, what openWakeWord expects
FRAME_S = FRAME / RATE


class Listener(threading.Thread):
    def __init__(self, on_wake, on_utterance, on_timeout, on_level, wake_threshold=0.35,
                 wake_enabled=True, on_keyword=None, system_audio=None):
        super().__init__(daemon=True, name="listener")
        # What the PC itself is playing (systemaudio.SystemAudio): used to ignore YouTube/music in the mic.
        self.system_audio = system_audio
        self._env_mic, self._env_sys = [], []        # loudness of mic vs speakers over the current recording
        self._benv_mic, self._benv_sys = [], []      # ... and over the current speech burst
        self._sys_drops, self._sys_drop_t = 0, 0.0   # recent recordings that turned out to be the PC's audio
        self.on_wake, self.on_utterance, self.on_timeout, self.on_level = on_wake, on_utterance, on_timeout, on_level
        # Backup wake path: short speech bursts are transcribed and checked for the word "Jarvis".
        self.on_keyword = on_keyword
        self._burst = None
        self._b_start = self._b_last = 0.0
        self._kw_busy = False
        self.wake_threshold = wake_threshold
        self.wake_enabled = wake_enabled
        self.mode = "wake"
        self._frames = []
        self._record_started = 0.0
        self._speech_started = False
        self._last_voice = 0.0
        self._no_speech_timeout = 6.0
        self._noise = 250.0            # running estimate of background RMS (after gain)
        self._raw_noise = 60.0         # background RMS straight from the mic (before gain)
        self.gain = 1.0                # automatic digital gain for quiet microphones
        self._peak = 0.0               # highest wake score in the current burst (diagnostics)
        self._peak_t = 0.0
        self._last_wake = 0.0
        # ~1.9 s of audio before the wake word fired, so the transcript includes "OK Jarvis".
        self._preroll = collections.deque(maxlen=24)
        self.wake_score = None           # score that opened the current recording (None = click/follow-up)
        self._stop = threading.Event()
        self.ready = False               # wake model loaded and the mic is open
        self.tap = None                  # live voice: gets every mic frame (data, rms, noise) while mode == "tap"
        self._model = None
        self.error = None

    # ------------------------------------------------------------- control
    def start_recording(self, no_speech_timeout=6.0, include_preroll=False, wake_score=None):
        if self.tap is not None:
            return                       # live voice owns the microphone
        self.wake_score = wake_score
        self._frames = list(self._preroll) if include_preroll else []
        self._env_mic, self._env_sys = [], []
        self._speech_started = False
        self._record_started = time.monotonic()
        self._last_voice = self._record_started
        self._no_speech_timeout = no_speech_timeout
        self.mode = "record"

    def capture(self, seconds: float) -> bytes:
        """Record the microphone for a fixed time (blocking) - used to learn the user's voice."""
        self._capture_frames = []
        self._capture_until = time.monotonic() + seconds
        self._capture_done = threading.Event()
        self.mode = "capture"
        self._capture_done.wait(seconds + 5)
        self.mode = "busy"
        return b"".join(self._capture_frames)

    def set_idle(self):
        self.mode = "tap" if self.tap is not None else "wake"

    def set_busy(self):
        self.mode = "tap" if self.tap is not None else "busy"

    def stop(self):
        self._stop.set()

    # ----------------------------------------------------------------- loop
    @staticmethod
    def _stub_sklearn():
        """openwakeword imports scikit-learn only to *train* custom verifiers, which Jarvis never does.
        If Windows Smart App Control blocks scikit-learn's DLLs, give it a harmless placeholder."""
        import sys
        import types
        try:
            import sklearn.linear_model  # noqa: F401
            return
        except Exception:
            pass
        for name in [m for m in sys.modules if m == "sklearn" or m.startswith("sklearn.")]:
            del sys.modules[name]                      # drop the half-imported real package
        for name in ("sklearn", "sklearn.linear_model", "sklearn.pipeline", "sklearn.preprocessing"):
            sys.modules[name] = types.ModuleType(name)
        sys.modules["sklearn.linear_model"].LogisticRegression = object
        sys.modules["sklearn.pipeline"].make_pipeline = lambda *a, **k: None
        sys.modules["sklearn.preprocessing"].FunctionTransformer = object
        sys.modules["sklearn.preprocessing"].StandardScaler = object
        log.warning("scikit-learn is blocked (Smart App Control); using a placeholder for the wake-word library")

    def _load_wake_model(self):
        if not self.wake_enabled:
            return
        self._stub_sklearn()
        import openwakeword
        from openwakeword.model import Model
        try:
            self._model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception:
            openwakeword.utils.download_models(["hey_jarvis"])
            self._model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    def run(self):
        try:
            self._load_wake_model()
        except Exception:
            # The microphone must never depend on the wake-word model: "Jarvis" is still caught by the
            # speech-recognition backup path, and clicking / typing keep working.
            log.exception("offline wake word unavailable; listening with the speech-recognition wake instead")
            self._model = None
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                             frames_per_buffer=FRAME)
        except Exception as e:
            log.exception("microphone/wake word init failed")
            self.error = str(e)
            return
        log.info("listening (wake word %s)", "on" if self._model else "off")
        self.ready = True
        last_gain_log = 0.0
        while not self._stop.is_set():
            try:
                data = stream.read(FRAME, exception_on_overflow=False)
            except Exception:
                # The mic was unplugged / switched / the driver hiccuped: reopen instead of going deaf.
                log.exception("microphone read failed; reopening")
                try:
                    stream.close()
                except Exception:
                    pass
                time.sleep(1)
                try:
                    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True, frames_per_buffer=FRAME)
                except Exception:
                    log.exception("could not reopen the microphone")
                    time.sleep(4)
                continue
            raw = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            raw_rms = float(np.sqrt(np.mean(raw ** 2)))
            # Automatic gain: a quiet mic (low Windows input level) gets boosted so the wake word still fires.
            if self.mode == "wake" and raw_rms < self._raw_noise * 2.5:
                self._raw_noise = 0.98 * self._raw_noise + 0.02 * max(raw_rms, 2.0)
                self.gain = float(np.clip(150.0 / self._raw_noise, 1.0, 6.0))
            if self.gain > 1.01:
                raw = np.clip(raw * self.gain, -32768, 32767)
            pcm = raw.astype(np.int16)
            data = pcm.tobytes()
            rms = raw_rms * self.gain
            now_t = time.monotonic()
            if now_t - last_gain_log > 300:
                last_gain_log = now_t
                log.info("mic: background level %.0f, automatic gain x%.1f", self._raw_noise, self.gain)
            self.on_level(min(1.0, rms / 2500.0))
            now = time.monotonic()

            if self.mode == "tap":
                if self.tap is not None:
                    self.tap(data, rms, self._noise)
                continue
            if self.mode == "record":
                self._record(data, rms, now)
            elif self.mode == "capture":
                self._capture_frames.append(data)
                if now >= self._capture_until:
                    self.mode = "busy"
                    self._capture_done.set()
            else:
                if self.mode == "wake" and rms < self._noise * 2.5:
                    self._noise = 0.97 * self._noise + 0.03 * max(rms, 60.0)
                self._preroll.append(data)
                if self._model is not None:
                    score = self._model.predict(pcm).get("hey_jarvis", 0.0)
                    # Diagnostics: remember near-misses so "it doesn't hear me" can be tuned from the log.
                    if score > self._peak:
                        self._peak, self._peak_t = score, now
                    elif self._peak > 0.10 and now - self._peak_t > 1.0:
                        if self._peak < self.wake_threshold:
                            log.info("wake near-miss %.2f (threshold %.2f, gain x%.1f)", self._peak,
                                     self.wake_threshold, self.gain)
                        self._peak = 0.0
                    if score >= self.wake_threshold and now - self._last_wake > 1.5:
                        self._last_wake = now
                        self._model.reset()
                        self._burst = None
                        log.info("wake word (%.2f)", score)
                        self.start_recording(include_preroll=True, wake_score=score)
                        self.on_wake()
                if self.mode == "wake" and self.on_keyword is not None:
                    self._track_burst(data, rms, now)
        stream.stop_stream()
        stream.close()
        pa.terminate()

    def _track_burst(self, data, rms, now):
        """Collect each short burst of speech; when it ends, check it for the word 'Jarvis' in the background."""
        speechy = rms > max(self._noise * 3.0, 260.0)
        if self._burst is None:
            if speechy:
                self._burst = list(self._preroll)[-4:]
                self._b_start = self._b_last = now
                self._benv_mic, self._benv_sys = [], []
            return
        self._burst.append(data)
        self._benv_mic.append(rms)
        self._benv_sys.append(self._sys_level())
        if speechy:
            self._b_last = now
        if now - self._b_last > 0.7 or now - self._b_start > 7:
            frames, dur = self._burst, self._b_last - self._b_start
            self._burst = None
            if self._is_system(self._benv_mic, self._benv_sys, "speech burst"):
                return
            if 0.3 < dur < 7 and not self._kw_busy:
                self._kw_busy = True
                threading.Thread(target=self._check_keyword, args=(b"".join(frames),), daemon=True,
                                 name="keyword").start()

    def _check_keyword(self, audio):
        try:
            text = transcribe(audio, ("en-IN",)).get("en-IN", "")
            if text and KEYWORD.search(text) and self.mode == "wake":
                log.info("keyword wake: %r", text)
                self._last_wake = time.monotonic()
                if self._model is not None:
                    self._model.reset()
                self.on_keyword(audio, text)
        except Exception as e:
            log.debug("keyword check failed: %s", e)
        finally:
            self._kw_busy = False

    def _sys_level(self):
        return self.system_audio.level() if self.system_audio is not None else 0.0

    def _is_system(self, mic_env, sys_env, what):
        if self.system_audio is None:
            return False
        from systemaudio import is_system_audio
        system, corr = is_system_audio(mic_env, sys_env)
        if system:
            log.info("ignored %s: it was the PC's own audio (match %.2f)", what, corr)
        return system

    def _record(self, data, rms, now):
        self._frames.append(data)
        self._env_mic.append(rms)
        self._env_sys.append(self._sys_level())
        threshold = max(self._noise * 2.8, 450.0)
        if rms > threshold:
            if not self._speech_started:
                self._speech_started = True
            self._last_voice = now
        if not self._speech_started:
            if now - self._record_started > self._no_speech_timeout:
                self.mode = "wake"
                self.on_timeout()
            return
        silence = now - self._last_voice
        too_long = now - self._record_started > 15
        # 0.9 s of silence ends the command: long enough for a natural pause mid-sentence.
        if silence > 0.9 or too_long:
            if self._is_system(self._env_mic, self._env_sys, "recording"):
                # Just the speakers (YouTube, music...): keep waiting for the user - but if that's all we keep
                # hearing, treat it like silence so Jarvis doesn't sit in "listening" for ever.
                self._sys_drops = self._sys_drops + 1 if now - self._sys_drop_t < 30 else 1
                self._sys_drop_t = now
                if self._sys_drops >= 2:
                    self._sys_drops = 0
                    self.mode = "wake"
                    self.on_timeout()
                else:
                    self.start_recording(no_speech_timeout=self._no_speech_timeout, wake_score=self.wake_score)
                return
            self._sys_drops = 0
            audio = b"".join(self._frames)
            self._frames = []
            self.mode = "busy"
            if self._model is not None:
                self._model.reset()
            self.on_utterance(audio)


_pool = None


def transcribe(audio: bytes, languages=("en-IN", "te-IN", "hi-IN")) -> dict:
    """Google speech-to-text in several languages at once -> {language: transcript}.

    Each recogniser forces its own language, so only the one matching what was actually
    said reads naturally; the model picks it. Running them in parallel costs no extra time.
    """
    import speech_recognition as sr
    from concurrent.futures import ThreadPoolExecutor
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="stt")
    data = sr.AudioData(audio, RATE, 2)

    def one(lang):
        try:
            return lang, sr.Recognizer().recognize_google(data, language=lang)
        except sr.UnknownValueError:
            return lang, ""

    jobs = [_pool.submit(one, lang) for lang in languages]
    whisper = _pool.submit(_groq_whisper, audio) if os.environ.get("GROQ_API_KEY") else None
    results, errors = {}, []
    for j in jobs:
        try:
            lang, text = j.result()
            results[lang] = text
        except Exception as e:
            errors.append(e)
    if whisper is not None:
        try:
            w = whisper.result(timeout=8)
            if w:
                results["whisper"] = w
        except Exception as e:
            log.info("groq whisper failed: %s", str(e)[:100])
    if errors and not any(results.values()):
        raise errors[0]                          # offline: let the caller say so
    return {lang: text for lang, text in results.items() if text}


def _groq_whisper(audio: bytes) -> str:
    """Groq's Whisper large-v3 turbo: understands English, Telugu, Hindi and mixed speech in ~0.3 s."""
    import io
    import wave
    import httpx
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio)
    r = httpx.post("https://api.groq.com/openai/v1/audio/transcriptions",
                   headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                   files={"file": ("speech.wav", buf.getvalue(), "audio/wav")},
                   data={"model": "whisper-large-v3-turbo", "response_format": "json", "temperature": "0"}, timeout=8)
    r.raise_for_status()
    text = (r.json().get("text") or "").strip()
    # Whisper invents these on silence / noise
    if text.lower().strip(" .!") in ("", "thank you", "thanks for watching", "you", "bye", "thank you for watching"):
        return ""
    return text
