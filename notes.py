"""Meeting & lecture notes: Atomo listens to a class, a YouTube lecture or a meeting and writes notes.

Sources: the PC's own sound (WASAPI loopback - YouTube, Zoom/Meet, online classes) and/or the microphone
(a real classroom, the user's side of a meeting). Every ~40 s the audio is transcribed by Gemini and the
live transcript streams onto the Atomo Screen. On stop: summary, key points, action items and a quiz.
Audio is only kept in memory while recording and never written to disk.
"""

import base64
import datetime
import io
import logging
import threading
import time
import wave

import numpy as np

log = logging.getLogger("jarvis.notes")
SR = 16000
CHUNK_SECONDS = 40

system_audio = None       # set by jarvis.py (SystemAudio with raw-audio sinks)

_state = {"on": False}


def _wav_b64(pcm16: np.ndarray) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


class _Recorder:
    def __init__(self, source, title):
        self.source, self.title = source, title
        self.lock = threading.Lock()
        self.sys_buf, self.mic_buf = [], []
        self.transcript = []                      # (clock time, text)
        self.started = time.time()
        self.stop_ev = threading.Event()
        self.cid = None
        self.mic = None
        self.worker = threading.Thread(target=self._loop, name="notes", daemon=True)

    # -- audio in
    def _on_system(self, data, rate, ch):
        x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if ch > 1:
            x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
        if rate != SR and len(x):
            n = int(len(x) * SR / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        with self.lock:
            self.sys_buf.append(x)

    def _on_mic(self, indata, frames, t, status):
        with self.lock:
            self.mic_buf.append(indata[:, 0].astype(np.float32) * 32767)

    def start(self):
        if self.source in ("speakers", "both", "auto") and system_audio is not None:
            system_audio.sinks.append(self._on_system)
        if self.source in ("mic", "both", "auto"):
            try:
                import sounddevice as sd
                self.mic = sd.InputStream(samplerate=SR, channels=1, dtype="float32", callback=self._on_mic, blocksize=1600)
                self.mic.start()
            except Exception as e:
                log.warning("notes: microphone unavailable (%s); recording the PC's sound only", e)
        self.worker.start()

    def _take(self):
        with self.lock:
            a = np.concatenate(self.sys_buf) if self.sys_buf else np.zeros(0, np.float32)
            b = np.concatenate(self.mic_buf) if self.mic_buf else np.zeros(0, np.float32)
            self.sys_buf, self.mic_buf = [], []
        n = max(len(a), len(b))
        mix = np.zeros(n, np.float32)
        mix[:len(a)] += a
        mix[:len(b)] += b * 1.6                    # the mic is usually quieter than the speakers
        return np.clip(mix, -32767, 32767)

    # -- transcription loop
    def _loop(self):
        import tools
        self._render(final=False)
        last = time.time()
        while not self.stop_ev.wait(1.0):
            if time.time() - last >= CHUNK_SECONDS:
                last = time.time()
                self._transcribe(self._take())
            else:
                self._render(final=False, only_clock=True)
        self._transcribe(self._take())

    def _transcribe(self, audio):
        import tools
        if len(audio) < SR * 2 or float(np.sqrt(np.mean(audio ** 2))) < 60:
            return                                  # silence
        clock = datetime.datetime.now().strftime("%H:%M")
        try:
            text = tools.transcribe_audio(_wav_b64(audio), (
                "Transcribe this audio from a lecture, class or meeting. Write what is said, in the language spoken "
                "(English, Telugu, Hindi or mixed), as clean readable sentences with punctuation; mark a new speaker "
                "with '- ' at the start of a line when the voice clearly changes. Skip music, noise and silence. "
                "If nothing intelligible is said, reply exactly: (silence)"))
        except Exception as e:
            log.warning("notes: transcription failed: %s", e)
            text = "_(this part could not be transcribed: " + str(e)[:80] + ")_"
        text = (text or "").strip()
        if text and text != "(silence)":
            self.transcript.append((clock, text))
            self._render(final=False)

    def _elapsed(self):
        s = int(time.time() - self.started)
        return f"{s // 60}:{s % 60:02d}"

    def _render(self, final, only_clock=False):
        import tools
        if only_clock and int(time.time()) % 5:
            return
        body = "\n\n".join(f"**{c}** {t}" for c, t in self.transcript) or "_Listening… the first notes appear in about 40 seconds._"
        md = (f"# 🎙 {self.title}\n\n> ● **Recording** · {self._elapsed()} · say “stop notes” when it's over\n\n"
              f"## Live transcript\n\n{body}\n")
        self.cid = tools.show_content("document", f"Notes: {self.title}"[:60], "md", md, cid=self.cid, final=False)

    def stop(self):
        self.stop_ev.set()
        if system_audio is not None and self._on_system in system_audio.sinks:
            system_audio.sinks.remove(self._on_system)
        if self.mic:
            try:
                self.mic.stop()
                self.mic.close()
            except Exception:
                pass
        self.worker.join(90)


def notes(action: str = "start", source: str = "both", title: str = "") -> str:
    import tools
    rec = _state.get("rec")
    if action == "start":
        if rec:
            return "OK: notes are already recording."
        title = title.strip() or f"Notes {datetime.datetime.now():%d %b, %I:%M %p}".replace(" 0", " ")
        rec = _Recorder(source if source in ("speakers", "mic", "both") else "both", title)
        _state["rec"] = rec
        rec.start()
        tools.publish({"type": "notes", "on": True, "title": title})
        return (f"OK: taking notes ({'PC sound and microphone' if rec.source == 'both' else rec.source}); the live "
                f"transcript is on the Atomo Screen. Say 'stop notes' to get the summary.")
    if action == "status":
        return f"OK: recording for {rec._elapsed()}, {len(rec.transcript)} parts so far." if rec else "OK: not recording."
    if not rec:
        return "FAILED: no notes are being recorded."
    tools.progress("Finishing the notes")
    rec.stop()
    _state.pop("rec", None)
    tools.publish({"type": "notes", "on": False})
    full = "\n\n".join(f"[{c}] {t}" for c, t in rec.transcript)
    if not full.strip():
        tools.show_content("document", f"Notes: {rec.title}"[:60], "md",
                           f"# {rec.title}\n\nNothing intelligible was heard during {rec._elapsed()}.", cid=rec.cid)
        return "OK: recording stopped, but nothing intelligible was heard."
    prompt = (f"These are timestamped transcripts of a lecture / class / meeting titled '{rec.title}' "
              f"({rec._elapsed()} long):\n\n{full[:60000]}\n\n"
              "Write excellent study / meeting notes in Markdown, in the main language of the transcript:\n"
              "## Summary (5-8 sentences)\n## Key points (bullets, grouped under short headings)\n"
              "## Important terms (term - meaning)\n## Action items / homework (with who/when if mentioned; or 'None')\n"
              "## Quick quiz (5 questions; answers at the end in a collapsed '### Answers' list)\n"
              "Do not invent anything that was not said. Reply with ONLY the Markdown (no code fence).")
    try:
        notes_md = tools.generate_text(prompt, 6000)
    except Exception as e:
        notes_md = f"_The summary could not be written ({e}); the transcript is below._"
    notes_md = notes_md.strip().removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
    doc = (f"# 📝 {rec.title}\n\n_{datetime.date.today():%d %B %Y} · {rec._elapsed()}_\n\n{notes_md}\n\n---\n\n"
           f"## Full transcript\n\n" + "\n\n".join(f"**{c}** {t}" for c, t in rec.transcript) + "\n")
    tools.show_content("document", f"Notes: {rec.title}"[:60], "md", doc, cid=rec.cid)
    first = notes_md.split("## Key points")[0].replace("## Summary", "").strip()[:600]
    return ("OK: the notes are ready on the Atomo Screen (summary, key points, action items, quiz, transcript) and NOT "
            f"saved yet - ask if the user wants to save them. Summary to mention briefly: {first}")
