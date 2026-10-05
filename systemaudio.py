"""Hears what the PC itself is playing (WASAPI loopback), so Jarvis can ignore it.

The microphone also picks up YouTube, music and videos from the speakers. While recording,
the listener keeps the loudness of the mic and of the speaker output side by side; if the two
rise and fall together, the "speech" was really the PC's own audio and is dropped. A person
talking over music doesn't follow the music's loudness, so real commands still get through.
"""

import logging
import threading
import time

import numpy as np

log = logging.getLogger("jarvis.systemaudio")


class SystemAudio:
    def __init__(self):
        self._level = 0.0
        self._t = 0.0
        self.ok = False
        threading.Thread(target=self._run, daemon=True, name="loopback").start()

    def level(self) -> float:
        """Loudness (RMS, int16 scale) of what the speakers are playing right now; 0 when silent."""
        return self._level if time.monotonic() - self._t < 0.3 else 0.0

    def _run(self):
        try:
            import pyaudiowpatch as pyaudio
            p = pyaudio.PyAudio()
            dev = p.get_default_wasapi_loopback()
            rate, ch = int(dev["defaultSampleRate"]), dev["maxInputChannels"]

            def cb(data, frames, _info, _status):
                x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                self._level = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
                self._t = time.monotonic()
                return (None, pyaudio.paContinue)

            # Callback mode: a loopback stream delivers nothing while the PC is silent, so a blocking read would hang.
            stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=int(rate * 0.04), stream_callback=cb)
            stream.start_stream()
            self.ok = True
            log.info("listening to system audio via %s", dev["name"])
            while True:
                time.sleep(5)
                if not stream.is_active():
                    raise RuntimeError("loopback stream stopped")
        except Exception as e:
            log.warning("system-audio monitor unavailable (%s); Jarvis may react to audio the PC plays", e)
            self.ok = False


def is_system_audio(mic_env, sys_env, min_level=250.0, threshold=0.6):
    """True if a recording is mostly the PC's own audio: the mic's loudness follows the speakers'.

    mic_env / sys_env: per-80 ms loudness of the mic and of the speaker output over the recording.
    The speakers lead the mic a little (buffering + air), so a few frames of lag are tried.
    Returns (is_system, best_correlation).
    """
    mic = np.asarray(mic_env, dtype=np.float32)
    sysa = np.asarray(sys_env, dtype=np.float32)
    n = min(len(mic), len(sysa))
    if n < 6 or float(np.mean(sysa[:n])) < min_level:
        return False, 0.0                       # nothing (much) was playing
    mic, sysa = mic[:n], sysa[:n]
    best = 0.0
    for lag in range(0, 5):
        a, b = mic[lag:], sysa[:n - lag]
        if len(a) < 6 or a.std() < 1e-3 or b.std() < 1e-3:
            continue
        best = max(best, float(np.corrcoef(a, b)[0, 1]))
    return best >= threshold, best
