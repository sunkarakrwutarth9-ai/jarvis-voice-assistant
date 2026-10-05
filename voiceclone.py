"""Re-voice Jarvis's speech in the user's own voice (OpenVoice V2 tone-colour converter, MIT).

Any edge-tts voice, in any language, is converted to the timbre captured from
assets/voice_ref.wav. Each base voice gets its own source embedding, computed
once from a short sample and cached in assets/embeddings/.
"""

import asyncio
import io
import logging
import threading
from pathlib import Path

import librosa
import numpy as np
import soundfile
import torch

from openvoice import utils
from openvoice.mel_processing import spectrogram_torch
from openvoice.models import SynthesizerTrn

log = logging.getLogger("jarvis.voiceclone")

HERE = Path(__file__).resolve().parent
CKPT_DIR = HERE / "openvoice" / "checkpoints"
ASSETS = HERE / "assets"
REF_WAV = ASSETS / "voice_ref.wav"
EMB_DIR = ASSETS / "embeddings"

SAMPLE_TEXT = {
    "en": "Good evening. All systems are running normally, and I am ready for your next instruction.",
    "hi": "नमस्ते। सभी सिस्टम सामान्य रूप से चल रहे हैं, और मैं आपके अगले निर्देश के लिए तैयार हूँ।",
    "te": "నమస్కారం. అన్ని వ్యవస్థలు సాధారణంగా పనిచేస్తున్నాయి, మీ తదుపరి ఆదేశం కోసం నేను సిద్ధంగా ఉన్నాను.",
}


class VoiceCloner:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        torch.set_num_threads(8)
        self.hps = utils.get_hparams_from_file(str(CKPT_DIR / "config.json"))
        self.sr = self.hps.data.sampling_rate
        self.model = SynthesizerTrn(len(getattr(self.hps, "symbols", [])), self.hps.data.filter_length // 2 + 1,
                                    n_speakers=self.hps.data.n_speakers, **self.hps.model)
        state = torch.load(CKPT_DIR / "checkpoint.pth", map_location="cpu")
        self.model.load_state_dict(state["model"], strict=False)
        self.model.to(self.device).eval()
        log.info("voice cloner on %s", self.device)
        self._lock = threading.Lock()
        self._src_cache = {}
        self.target = self._embedding_of_files([REF_WAV])
        try:
            import wavmark
            self.watermark = wavmark.load_model().to(self.device)
        except Exception as e:     # watermark marks audio as AI-generated; best effort
            log.warning("watermark model unavailable (%s)", e)
            self.watermark = None

    @staticmethod
    def available() -> bool:
        return REF_WAV.exists() and (CKPT_DIR / "checkpoint.pth").exists()

    # ------------------------------------------------------------ embeddings
    def _spec(self, audio: np.ndarray):
        y = torch.FloatTensor(audio).unsqueeze(0).to(self.device)
        h = self.hps.data
        return spectrogram_torch(y, h.filter_length, h.sampling_rate, h.hop_length, h.win_length, center=False)

    def _embedding(self, audio: np.ndarray):
        with torch.no_grad():
            return self.model.ref_enc(self._spec(audio).transpose(1, 2)).unsqueeze(-1)

    def _embedding_of_files(self, paths):
        return torch.stack([self._embedding(librosa.load(str(p), sr=self.sr)[0]) for p in paths]).mean(0)

    def source_embedding(self, voice: str, synth_fn=None, lang="en", make_audio=None):
        """Embedding of a base TTS voice, cached on disk.

        Either synth_fn(text, voice) -> mp3 bytes (edge-tts), or make_audio(text) -> float audio at self.sr.
        """
        if voice in self._src_cache:
            return self._src_cache[voice]
        path = EMB_DIR / f"{voice.replace(':', '_')}.pt"
        if path.exists():
            emb = torch.load(path, map_location=self.device)
        else:
            if "-" in voice:
                lang = voice.split("-")[0]
            text = SAMPLE_TEXT.get(lang, SAMPLE_TEXT["en"])
            audio = make_audio(text + " " + text) if make_audio else self.decode(synth_fn(text + " " + text, voice))
            emb = self._embedding(audio)
            EMB_DIR.mkdir(parents=True, exist_ok=True)
            torch.save(emb, path)
        self._src_cache[voice] = emb
        return emb

    # ------------------------------------------------------------ conversion
    def decode(self, mp3: bytes) -> np.ndarray:
        audio, _ = librosa.load(io.BytesIO(mp3), sr=self.sr)
        return audio

    def convert(self, audio: np.ndarray, src_emb, tau=0.3) -> np.ndarray:
        with self._lock, torch.no_grad():
            spec = self._spec(audio)
            lengths = torch.LongTensor([spec.size(-1)]).to(self.device)
            out = self.model.voice_conversion(spec, lengths, sid_src=src_emb, sid_tgt=self.target, tau=tau)[0][0, 0]
            out = out.float().cpu().numpy()
        return self._add_watermark(out)

    def _add_watermark(self, audio):
        if self.watermark is None:
            return audio
        # Same scheme as OpenVoice: a fixed 32-bit pattern in each 1 s chunk that is long enough.
        bits = np.array([1, 0, 1, 1, 0, 0, 1, 0] * 4)
        k = 16000
        out = audio.copy()
        msg = torch.FloatTensor(bits).unsqueeze(0).to(self.device)
        with torch.no_grad():
            for i in range(len(audio) // (k * 2)):
                seg = torch.FloatTensor(audio[k * 2 * i: k * (2 * i + 1)]).unsqueeze(0).to(self.device)
                marked = self.watermark.encode(seg, msg)
                out[k * 2 * i: k * (2 * i + 1)] = marked.squeeze().cpu().numpy()
        return out

    def to_wav_bytes(self, audio: np.ndarray) -> bytes:
        buf = io.BytesIO()
        soundfile.write(buf, audio, self.sr, format="WAV")
        return buf.getvalue()


def synth_mp3(text: str, voice: str, rate="+0%") -> bytes:
    """Blocking edge-tts synthesis (helper for building embeddings and tests)."""
    import edge_tts

    async def run():
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    return asyncio.run(run())
