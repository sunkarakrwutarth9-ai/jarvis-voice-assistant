"""J.A.R.V.I.S. - voice-controlled desktop assistant with a Dynamic Island.

    pythonw jarvis.py          # normal: wake word + island, no console
    python jarvis.py           # same, with a console log
    python jarvis.py --text    # type to Jarvis in the console (debugging)
    python jarvis.py --mute    # start with the voice muted
    python jarvis.py --no-wake # no wake word; click the island to talk

Say "Hey Jarvis", then your command. After Jarvis answers you can speak again
without the wake word for a few seconds. Say "Hey Jarvis" or click the island
at any time to interrupt.
"""

import argparse
import ctypes
import datetime
import getpass
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv, set_key

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
LOG_FILE = HERE / "jarvis.log"

load_dotenv(ENV_FILE)
# AI provider. Pick one with JARVIS_PROVIDER in .env; otherwise the first provider with a key wins.
PROVIDERS = {
    # name: (base URL, key variable, default model, backup models tried when the main one is overloaded)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY",
               "gemini-3.6-flash", ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest",
                                    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                   "anthropic/claude-sonnet-5", ["anthropic/claude-haiku-4.5"]),
    "xpl": ("https://api.experientiallabs.ai/v1", "XPL_API_KEY", "claude-sonnet-5", ["claude-haiku-4.5"]),
}
PROVIDER = os.environ.get("JARVIS_PROVIDER") or next(
    (name for name, p in PROVIDERS.items() if os.environ.get(p[1])), "gemini")   # new users: free Gemini key
BASE_URL, KEY_VAR, _default_model, BACKUP_MODELS = PROVIDERS[PROVIDER]
BASE_URL = os.environ.get("JARVIS_BASE_URL", BASE_URL)
MODEL = os.environ.get("JARVIS_MODEL", _default_model)
# Extra providers that race alongside the main one (used when its models are slow or out of quota).
EXTRA_PROVIDERS = []
if PROVIDER != "xpl" and os.environ.get("XPL_API_KEY"):
    EXTRA_PROVIDERS.append(("xpl", PROVIDERS["xpl"][0], os.environ["XPL_API_KEY"],
                            os.environ.get("JARVIS_XPL_MODELS", "deepseek-v4.1-flash,deepseek-v4-flash").split(",")))
# A wake-word score below this is only trusted if the transcript actually contains "Jarvis".
WAKE_VERIFY_BELOW = 0.6
VOICE = os.environ.get("JARVIS_VOICE")                # optional override for the English Jarvis voice
# Speech is recognised in all of these at once; the model works out which one was spoken.
LANGUAGES = [l.strip() for l in os.environ.get("JARVIS_LANGUAGES", "en-IN,te-IN,hi-IN").split(",") if l.strip()]
ME_MODE = os.environ.get("JARVIS_ME_MODE", "0") == "1"
WAKE_THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.35"))
FOLLOW_UP_SECONDS = 4.0

WAKE_WORD = re.compile(r"\b(jarvis|jarvi|jervis|jarvish|travis|atomo|atomu|atom o|a tomo|atom oh|automo|attomo|atamo|adamo)\b|జార్విస్|జార్విస|जार्विस|जारविस|అటోమో|ఆటోమో|అటామో|ఏటమో|एटमो|ऐटमो|आटोमो|अटोमो|एटोमो", re.I)
WAKE_PREFIX = re.compile(r"^\s*((hey|hi|ok|okay|o\.k\.|yo)[\s,]+)?(jarvis|jarvi|jervis|jarvish|travis|atomo|atomu|atom o|a tomo|atom oh|automo|attomo|atamo|adamo|అటోమో|ఆటోమో|एटमो|आटोमो|अटोमो)\b[\s,.!?]*", re.I)
STOP_PHRASES = {"stop", "cancel", "never mind", "nevermind", "nothing", "shut up", "be quiet", "quiet",
                # filler that shouldn't start another round
                "ok", "okay", "ok ok", "okay okay", "k", "hmm", "hm", "mm", "fine", "good",
                "cool", "nice", "thanks", "thank you", "thank you jarvis", "thanks jarvis", "alright", "all right"}
QUIT_PHRASES = {"shut down jarvis", "exit jarvis", "quit jarvis", "turn off jarvis", "jarvis shut down",
                "close jarvis app"}
# Ends conversation mode: Jarvis goes back to waiting for "OK Jarvis".
DEACTIVATE = re.compile(r"\b(de-?activate|deactivated|go to sleep|sleep mode|stop listening|goodbye|good bye|bye bye|"
                        r"that'?s all for now|you can rest)\b|డీయాక్టివేట్|డియాక్టివేట్|డీ ?ఆక్టివేట్|डीएक्टिवेट|डिएक्टिवेट",
                        re.I)
# Replies meaning "I couldn't make that out" - in conversation mode they're dropped silently (it was noise).
UNHEARD = re.compile(r"(did ?n.?t|did not|could ?n.?t|could not|unable to|can.?t) (quite )?(catch|hear|make out|understand)"
                     r"|(repeat that|speak again|say (that|it) again|no (clear )?speech)", re.I)
ACTIVE_IDLE_LIMIT = 10 * 60        # conversation mode ends by itself after 10 minutes of silence

log = logging.getLogger("jarvis")

# Read aloud during "learn my voice": varied sounds, about 25 seconds at a normal pace.
VOICE_PASSAGE = ("The quick brown fox jumps over the lazy dog. Jarvis, open YouTube and play my favourite song. "
                 "What's the weather like in Hyderabad today? Set a timer for ten minutes, and remind me to call home. "
                 "I really enjoy building cool projects on my computer.")


def setup_logging():
    # Under pythonw there is no console; some libraries (Kokoro's loguru) crash writing to None.
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    console = sys.stdout is not None
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", handlers=handlers)
    for noisy in ("httpx", "openai", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def single_instance(wait_seconds=8):
    """Only one Jarvis may own the microphone. Waits briefly in case the old copy is still shutting down."""
    k32 = ctypes.windll.kernel32
    deadline = time.monotonic() + wait_seconds
    while True:
        handle = k32.CreateMutexW(None, False, "Global\\JarvisAssistantMutex")
        if k32.GetLastError() != 183:          # ERROR_ALREADY_EXISTS
            return True
        k32.CloseHandle(handle)
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)


KEY_HELP = {   # key variable -> (prompt shown on first run, short name)
    "GEMINI_API_KEY": ("Paste your Google Gemini API key.\nGet one free at https://aistudio.google.com/apikey",
                       "Gemini API key"),
    "OPENROUTER_API_KEY": ("Paste your OpenRouter API key (from https://openrouter.ai/keys)", "OpenRouter API key"),
    "XPL_API_KEY": ("Paste your Experiential Labs API key (xpl_...)", "Experiential Labs API key"),
}


def get_api_key(gui=False) -> str:
    key = os.environ.get(KEY_VAR, "").strip()
    if key:
        return key
    if gui:
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        key, ok = QInputDialog.getText(None, "Atomo - first-time setup", KEY_HELP[KEY_VAR][0],
                                       QLineEdit.Password)
        if not ok:
            sys.exit(0)
    else:
        print(KEY_HELP[KEY_VAR][0] + "  (paste it ONCE - input is hidden)")
        key = getpass.getpass(KEY_HELP[KEY_VAR][1] + ": ")
    key = key.strip()
    prefix = "sk-or-v1-"
    if key.count(prefix) > 1:          # hidden input makes repeated pastes easy
        key = prefix + key.split(prefix)[1]
    if not key:
        sys.exit("No key entered.")
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), "OPENROUTER_API_KEY", key)
    return key


def greeting():
    h = datetime.datetime.now().hour
    part = "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening"
    return f"Good {part}, Sir. All systems online."


class Ducker(threading.Thread):
    """Lowers the PC volume while Jarvis is listening, so music doesn't drown out your voice."""

    def __init__(self):
        super().__init__(daemon=True, name="ducker")
        self.q = queue.Queue()
        self._saved = None

    def run(self):
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities
        vol = AudioUtilities.GetSpeakers().EndpointVolume
        while True:
            cmd = self.q.get()
            try:
                if cmd == "duck" and self._saved is None:
                    self._saved = vol.GetMasterVolumeLevelScalar()
                    vol.SetMasterVolumeLevelScalar(self._saved * 0.35, None)
                elif cmd == "restore" and self._saved is not None:
                    vol.SetMasterVolumeLevelScalar(self._saved, None)
                    self._saved = None
            except Exception:
                log.exception("volume ducking failed")

    def duck(self):
        self.q.put("duck")

    def restore(self):
        self.q.put("restore")


class Assistant(threading.Thread):
    """Runs every conversation turn: transcribe, think, act, speak, follow up."""

    def __init__(self, brain, speaker, bridge):
        super().__init__(daemon=True, name="assistant")
        self.brain, self.speaker, self.bridge = brain, speaker, bridge
        self.events = queue.Queue()
        self.turn = 0
        self.listener = None
        self.ducker = Ducker()
        self.ducker.start()
        self.choice_active = False       # an option list (e.g. Chrome accounts) is showing on the island
        self.explicit_wake = False       # current recording was started by the wake word or a click
        self.hub = None                  # dashboard event hub (server.Hub), if running
        self.active = False              # conversation mode: keep listening after every reply until "deactivate"
        self.awaiting_answer = False     # Jarvis's last reply was a question
        self.last_heard = 0.0            # when the user last said something in conversation mode
        self.pending_pick = None         # callback(value) -> result string, for a clicked option

    # ---------------------------------------------- called from other threads
    def on_wake(self):
        """Wake word heard (listener thread) - interrupt whatever is happening."""
        self.turn += 1
        self.explicit_wake = True
        self.activate()
        __import__("tools").cancel_task.set()         # stop a running autopilot task
        self.speaker.stop()
        self.speaker.chime(True)
        self.ducker.duck()
        self.bridge.state.emit("listening", "Listening…", "", "")

    def on_keyword(self, audio, text):
        """Backup wake (listener thread): speech recognition heard 'Jarvis' in a burst of speech."""
        rest = WAKE_PREFIX.sub("", text).strip()
        if len(rest.split()) >= 2:
            # "OK Jarvis, open YouTube" in one breath: the command is already in this audio - run it.
            self.turn += 1
            self.explicit_wake = True
            self.activate()
            __import__("tools").cancel_task.set()
            self.speaker.stop()
            self.speaker.chime(True)
            self.listener.set_busy()
            self.listener.wake_score = None          # recognised words, not a borderline model score
            self.bridge.state.emit("thinking", "Thinking", f"“{rest}”", "")
            self.events.put(("utterance", audio, self.turn))
        else:
            # Just "OK Jarvis": start listening for the command.
            self.listener.start_recording()
            self.on_wake()

    def activate(self):
        if not self.active:
            log.info("conversation mode ON")
            self._pub({"type": "active", "on": True})
        self.active = True
        self.last_heard = time.monotonic()

    def deactivate(self):
        if self.active:
            log.info("conversation mode OFF")
            self._pub({"type": "active", "on": False})
        self.active = False

    def _keep_listening(self):
        """In conversation mode: listen for the next request without needing the wake word."""
        if self.active and time.monotonic() - self.last_heard > ACTIVE_IDLE_LIMIT:
            self.deactivate()
            self._speak_standalone("I'll stand by, Sir. Say OK Atomo when you need me.", "speaking")
            return False
        if not self.active:
            return False
        self.explicit_wake = False
        self.listener.start_recording(no_speech_timeout=15)
        self.bridge.state.emit("followup", "Active", "", "")
        return True

    def on_click(self):
        if self.listener is not None and self.listener.mode == "record":
            self.turn += 1                       # clicking while listening cancels (and ends conversation mode)
            self.deactivate()
            self.listener.set_idle()
            self.ducker.restore()
            self.speaker.chime(False)
            self.bridge.state.emit("idle", "", "", "")
            return
        if self.listener is not None:
            self.listener.start_recording()
        self.on_wake()

    def on_utterance(self, audio):
        self.ducker.restore()
        self.events.put(("utterance", audio, self.turn))

    def on_timeout(self):
        self.ducker.restore()
        self.events.put(("timeout", None, self.turn))

    def on_pick(self, value):
        """An option on the island was clicked (GUI thread)."""
        self.turn += 1
        self.speaker.stop()
        self.events.put(("pick", value, self.turn))

    def notify(self, text):
        self.events.put(("notify", text, None))

    # ---------------------------------------------------------------- thread
    def run(self):
        import comtypes
        comtypes.CoInitialize()          # tools like set_volume use COM on this thread
        while True:
            kind, payload, turn = self.events.get()
            try:
                if kind == "quit":
                    self._shutdown()
                    return
                if kind == "say":
                    self._speak_standalone(payload, "speaking")
                elif kind == "notify":
                    self.turn += 1
                    self.speaker.chime(True)
                    self._speak_standalone(payload, "speaking")
                elif turn != self.turn:
                    continue                          # stale event from an interrupted turn
                elif kind == "pick":
                    self._handle_pick(payload)
                elif kind == "timeout":
                    self.choice_active = False
                    if self._keep_listening():           # conversation mode: just keep listening
                        continue
                    self.speaker.chime(False)
                    self.bridge.state.emit("idle", "", "", "")
                elif kind == "utterance":
                    self._handle_utterance(payload, turn)
            except Exception:
                log.exception("turn failed")
                self._error("Something went wrong", "Check jarvis.log for details")

    def _handle_utterance(self, audio, turn):
        cancelled = lambda: turn != self.turn
        self.choice_active = False
        self.bridge.state.emit("thinking", "Thinking", "", "")
        t0 = time.monotonic()
        typed = isinstance(audio, str)               # a command typed on the dashboard
        if typed:
            heard, audio = {"typed": audio}, b""
        else:
            try:
                heard = self._transcribe(audio)          # {language: transcript}
                log.info("timing: speech recognition %.1fs", time.monotonic() - t0)
            except Exception as e:
                log.warning("speech recognition failed: %s", e)
                self._error("Speech service unreachable", "Check your internet connection")
                return
        if cancelled():
            return
        score = None if typed else self.listener.wake_score
        if score is not None and score < WAKE_VERIFY_BELOW and not any(WAKE_WORD.search(t) for t in heard.values()):
            log.info("ignored borderline wake (%.2f): %r", score, heard)
            self.deactivate()                         # it was a false wake: don't stay in conversation mode
            self.bridge.state.emit("idle", "", "", "")
            self.listener.set_idle()
            return
        heard = {lang: WAKE_PREFIX.sub("", t).strip() for lang, t in heard.items()}
        heard = {lang: t for lang, t in heard.items() if t}
        explicit = self.explicit_wake
        if not heard and explicit and self.brain.accepts_audio and len(audio) > 16000 * 2 * 1.2:
            # The recognisers caught nothing, but the user did call Jarvis: let Gemini listen to the audio.
            heard = {"audio-only": "(the recognisers could not make it out - listen to the recording)"}
        elif not heard and self.active and self.brain.accepts_audio and len(audio) > 16000 * 2 * 1.5:
            # Conversation mode: a long recording the recognisers missed (accent, mixed language, quiet mic)
            # may still be a request - let Gemini listen, but stay silent if it's only noise.
            heard = {"audio-only": "(the recognisers could not make it out - listen to the recording. If it is not "
                                   "clear speech addressed to you - noise, music, a video, other people talking - "
                                   "reply with exactly NOREPLY and nothing else)"}
        if not heard:
            if self._keep_listening():
                return
            self.speaker.chime(False)
            self.bridge.state.emit("idle", "", "", "")
            self.listener.set_idle()
            return
        log.info("YOU: %s", heard)
        self.last_heard = time.monotonic()
        if any(DEACTIVATE.search(t) for t in heard.values()):
            self.deactivate()
            self._speak_standalone("Deactivating, Sir. Say OK Atomo when you need me.", "speaking")
            return
        # What to show on the island (English recogniser if it heard anything, else the first).
        text = heard.get("en-IN") or heard.get("en-US") or next(iter(heard.values()))
        if "audio-only" in heard:
            text = "…"
        # What the model gets: every transcript, so it can tell which language was spoken.
        prompt = text if len(heard) == 1 else "[speech] " + " | ".join(f"{l}: {t}" for l, t in heard.items())
        low = text.lower().strip(" .!?")
        if low in QUIT_PHRASES:
            self.speaker.say("Powering down. Good day, Sir.")
            self.bridge.state.emit("speaking", "", "Powering down. Good day, Sir.", "")
            self.speaker.wait()
            self.bridge.quit.emit()
            return
        answering = self.awaiting_answer            # Jarvis just asked something: "ok" / "sure" ARE answers
        self.awaiting_answer = False
        if not answering and (low in STOP_PHRASES or re.fullmatch(r"(ok(ay)?|hmm+|thanks?( you)?)[\s,.!]*(sir|jarvis)?", low)):
            log.info("filler/stop - %s", "still listening" if self.active else "going idle")
            if self._keep_listening():
                return
            self.speaker.chime(False)
            self.bridge.state.emit("idle", "", "", "")
            self.listener.set_idle()
            return

        self.bridge.state.emit("thinking", "Thinking", f"“{text}”", "")
        quiet_check = "audio-only" in heard and not explicit     # may turn out to be noise: don't show it yet
        if not quiet_check:
            self._pub({"type": "user", "text": text if text != "…" else "(voice)", "heard": heard})
        shown = []

        def on_sentence(s):
            if cancelled() or "NOREPLY" in s or (quiet_check and UNHEARD.search(s)):
                return
            if not shown:
                log.info("timing: first reply sentence %.1fs after speech ended", time.monotonic() - t0)
            self.speaker.say(s)
            shown.append(s)
            if self.choice_active:
                return                    # keep the option list on screen while Jarvis asks
            body = " ".join(shown)
            if len(body) > 260:
                body = "…" + body[-258:]
            self.bridge.state.emit("speaking", "", body, "")

        used_tools = []

        def on_tool_start(name, args):
            used_tools.append(name)
            icon, label = __import__("tools").describe(name, args)
            self._pub({"type": "tool", "id": f"{turn}-{len(used_tools)}", "name": name, "label": label, "result": None})
            if not cancelled():
                self.bridge.state.emit("action", label, "", icon)

        def on_tool_end(name, args, result):
            _, label = __import__("tools").describe(name, args)
            self._pub({"type": "tool", "id": f"{turn}-{len(used_tools)}", "name": name, "label": label,
                       "result": result[:200]})
            if not cancelled() and result.startswith("FAILED"):
                self.bridge.state.emit("error", "Couldn't do that", result[8:], "")

        try:
            reply = self.brain.ask(prompt, on_sentence, on_tool_start, on_tool_end, cancelled,
                                   audio_wav_b64=self._wav_b64(audio) if audio else None)
        except Exception as e:
            log.exception("LLM request failed")
            if cancelled():
                return
            msg = "I'm afraid I've lost contact with my servers, Sir."
            if "401" in str(e):
                msg = "My API key was rejected, Sir. Please check the key in the settings file."
            elif "card_required" in str(e):
                msg = "The Experiential Labs account needs its one dollar card verification before I can think, Sir."
            elif "402" in str(e) or "insufficient_quota" in str(e):
                msg = "The AI account is out of credit, Sir."
            elif "429" in str(e):
                msg = "I'm being rate limited, Sir. Give me a moment and try again."
            self._speak_standalone(msg, "error")
            return
        log.info("JARVIS: %s", reply)
        if (("NOREPLY" in (reply or "") or (quiet_check and UNHEARD.search(reply or "")))
                and not shown and not used_tools):
            log.info("unclear recording was not a request - still listening")
            if not self._keep_listening():
                self.bridge.state.emit("idle", "", "", "")
                self.listener.set_idle()
            return
        if quiet_check:
            self._pub({"type": "user", "text": "(voice)", "heard": heard})
        self._pub({"type": "reply", "text": reply or ("Done, Sir." if used_tools else ""),
                   "model": self.brain.current_model, "secs": round(time.monotonic() - t0, 1)})
        self._pub({"type": "ranking", "models": self.brain.health.table()})
        if cancelled():
            return
        if not shown and used_tools:
            self.bridge.state.emit("speaking", "", "Done, Sir.", "")
            self.speaker.say("Done, Sir.")
        self._wait_for_speech(cancelled, " ".join(shown))
        if cancelled():
            return
        self.awaiting_answer = bool(shown) and shown[-1].rstrip().endswith(("?", "？"))
        self.explicit_wake = False
        if self.choice_active:
            # Options are on screen: wait a little longer for a spoken pick (or a click).
            self.listener.start_recording(no_speech_timeout=9)
        elif self._keep_listening():
            pass                                        # conversation mode: straight back to listening
        elif shown and shown[-1].rstrip().endswith(("?", "？")):
            # Jarvis asked something: listen briefly for the answer without needing the wake word.
            self.listener.start_recording(no_speech_timeout=FOLLOW_UP_SECONDS)
            self.bridge.state.emit("followup", "", "", "")
        else:
            self.bridge.state.emit("idle", "", "", "")
            self.listener.set_idle()

    def _pub(self, event):
        if self.hub is not None:
            self.hub.publish(event)

    def on_text(self, text):
        """A command typed on the dashboard (server thread)."""
        self.turn += 1
        self.explicit_wake = True
        self.speaker.stop()
        if self.listener is not None:
            self.listener.set_busy()
        self.events.put(("utterance", text, self.turn))

    @staticmethod
    def _wav_b64(pcm: bytes) -> str:
        import base64
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        return base64.b64encode(buf.getvalue()).decode()

    def _handle_pick(self, value):
        self.choice_active = False
        self.listener.set_idle()
        pick, self.pending_pick = self.pending_pick, None
        if pick is None:
            self.bridge.state.emit("idle", "", "", "")
            return
        result = pick(value)
        log.info("picked %s -> %s", value, result)
        if result.startswith("OK"):
            name = result.split(" as ", 1)[-1].rstrip(".").split(" at ")[0] if " as " in result else ""
            msg = f"Opening Chrome as {name}, Sir." if name else "Done, Sir."
            self.brain.history.append({"role": "assistant", "content": msg})
            self._speak_standalone(msg, "speaking")
        else:
            self._speak_standalone(result.removeprefix("FAILED: "), "error")

    def _transcribe(self, audio):
        from listener import transcribe
        return transcribe(audio, LANGUAGES)

    def _wait_for_speech(self, cancelled, text):
        if self.speaker.muted:
            # Nothing is spoken; leave the text up long enough to read.
            end = time.monotonic() + min(8, 1.5 + len(text) / 16)
            while time.monotonic() < end and not cancelled():
                time.sleep(0.05)
        else:
            self.speaker.wait(cancelled)

    def _speak_standalone(self, text, state):
        turn = self.turn
        self.bridge.state.emit(state, "Connection problem" if state == "error" else "", text, "")
        self.speaker.say(text)
        self._wait_for_speech(lambda: turn != self.turn, text)
        if turn == self.turn and not (self.listener is not None and self._keep_listening()):
            self.bridge.state.emit("idle", "", "", "")
            if self.listener is not None:
                self.listener.set_idle()

    def _error(self, title, detail):
        self.bridge.state.emit("error", title, detail, "")
        time.sleep(2.5)
        if self.listener is not None and self._keep_listening():
            return
        self.bridge.state.emit("idle", "", "", "")
        if self.listener is not None:
            self.listener.set_idle()

    def _shutdown(self):
        import tools
        self.ducker.restore()
        self.speaker.stop()
        tools.YT.close()


def run_gui(args):
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    import tools
    from brain import Brain
    from island import Island
    from listener import Listener
    from speech import Speaker
    from systemaudio import SystemAudio

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    class Bridge(QObject):
        state = Signal(str, str, str, str)
        level = Signal(float)
        me = Signal(bool)
        choice = Signal(str, list)
        dashboard = Signal(bool)
        theme = Signal(str)
        appearance = Signal(str)
        robot = Signal(str)
        quit = Signal()

    bridge = Bridge()
    island = Island()
    bridge.state.connect(island.set_state)
    bridge.choice.connect(island.set_choice)
    bridge.dashboard.connect(island.set_dashboard_visible)
    bridge.theme.connect(island.set_theme)
    bridge.appearance.connect(island.set_appearance)
    island.set_theme(tools._state("theme", "ios"))
    island.set_appearance(tools._state("appearance", "light"))
    bridge.level.connect(island.set_level)
    bridge.me.connect(island.set_me_mode)

    brain = Brain(get_api_key(gui=True), MODEL, BASE_URL, BACKUP_MODELS, EXTRA_PROVIDERS)
    # Rank the models by live speed now, and refresh the ranking every 10 minutes.
    live = {}

    def probe_and_publish():
        brain.warm_up()
        if live.get("hub") is not None:
            live["hub"].publish({"type": "ranking", "models": brain.health.table()})

    threading.Thread(target=probe_and_publish, daemon=True, name="model-probe").start()

    # No periodic re-probing: free Gemini keys have small daily quotas, and real requests keep the ranking fresh.
    speaker = Speaker(voice_override=VOICE, muted=args.mute)
    island.muted = args.mute
    island.speech_level = speaker.level
    assistant = Assistant(brain, speaker, bridge)
    tools.notify = assistant.notify
    tools.everyday.notify = assistant.notify

    def ring_alarm():
        for _ in range(4):
            speaker.chime(True)
            time.sleep(0.7)

    tools.everyday.alarm = ring_alarm
    tools.everyday.start()

    def show_choice(title, options, on_pick):
        assistant.pending_pick = on_pick
        assistant.choice_active = True
        bridge.choice.emit(title, [list(o) for o in options])

    tools.show_choice = show_choice
    island.choice_picked.connect(assistant.on_pick)

    # ---- "Me mode": Jarvis talks in the user's own voice, with their face beside the island.
    me_pending = {"on": ME_MODE}

    def set_me(on: bool, announce=True) -> str:
        if on and speaker.cloner is None:
            if speaker.cloner_error:
                return f"FAILED: your voice isn't available ({speaker.cloner_error})."
            me_pending["on"] = True
            bridge.me.emit(True)
            bridge.state.emit("action", "Preparing your voice…", "", "")
            return "OK: your voice is still loading; switching automatically in a few seconds."
        me_pending["on"] = on
        speaker.me_mode = on
        bridge.me.emit(on)
        set_key(str(ENV_FILE), "JARVIS_ME_MODE", "1" if on else "0")
        if announce:
            assistant.events.put(("say", "Speaking in your voice now, Sir." if on else "Atomo voice restored, Sir.", None))
        return f"OK: now speaking in {'the user' if on else 'the Jarvis'} voice."

    def cloner_ready(ok):
        if ok and me_pending["on"]:
            set_me(True, announce=not ME_MODE or speaker.me_mode)
        elif not ok and me_pending["on"]:
            me_pending["on"] = False
            bridge.me.emit(False)

    tools.set_me_mode = lambda mine: set_me(bool(mine), announce=False)

    def learn_voice() -> str:
        """Runs on the assistant thread: record the user reading aloud, rebuild their voice."""
        import librosa
        import numpy as np
        import soundfile
        from voiceclone import REF_WAV
        speaker.say("I'll learn your voice now, Sir. Please read the text on the island aloud, clearly, after the beep.")
        speaker.wait()
        bridge.state.emit("speaking", "", "Read aloud: " + VOICE_PASSAGE, "")
        speaker.chime(True)
        time.sleep(0.3)
        raw = listener.capture(25)
        speaker.chime(False)
        audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        voiced = librosa.effects.split(audio, top_db=30)                   # drop the pauses
        audio = np.concatenate([audio[a:b] for a, b in voiced]) if len(voiced) else audio
        if len(audio) < 16000 * 8:
            return "FAILED: I heard less than 8 seconds of speech; the user should try again closer to the microphone."
        audio = librosa.resample(audio, orig_sr=16000, target_sr=22050)
        audio = audio / (np.abs(audio).max() + 1e-6) * 0.9
        if REF_WAV.exists():
            backup = REF_WAV.with_name("voice_ref_previous.wav")
            backup.unlink(missing_ok=True)
            REF_WAV.rename(backup)
        soundfile.write(REF_WAV, audio, 22050)
        speaker.reload_voice()
        if not speaker.me_mode:
            set_me(True, announce=False)
        return f"OK: learned the user's voice from {len(audio) / 22050:.0f} seconds of speech; now speaking in it."

    tools.learn_voice = learn_voice
    island.avatar_clicked.connect(lambda: set_me(not me_pending["on"]))
    island.set_me_mode(ME_MODE)
    speaker.load_cloner_async(cloner_ready)

    mic_level = {"v": 0.0}

    def on_level(v):
        mic_level["v"] = v
        bridge.level.emit(v)

    system_audio = SystemAudio()
    listener = Listener(on_wake=assistant.on_wake, on_utterance=assistant.on_utterance,
                        on_timeout=assistant.on_timeout, on_level=on_level,
                        wake_threshold=WAKE_THRESHOLD, wake_enabled=not args.no_wake,
                        on_keyword=assistant.on_keyword, system_audio=system_audio)
    assistant.listener = listener

    # ---- the dancing robot: appears in a screen corner whenever music plays (click-through, never in the way)
    from robot import DancingRobot
    dancer = DancingRobot(system_audio, speaker.busy, enabled=tools._state("robot", True),
                          side=tools._state("robot_side", "right"))

    def robot_command(action):
        if action in ("on", "off"):
            dancer.set_enabled(action == "on")
            tools._save_state("robot", action == "on")
        elif action in ("left", "right"):
            dancer.set_side(action)
            tools._save_state("robot_side", action)
        elif action == "dance":
            dancer.dance(12)

    bridge.robot.connect(robot_command)
    tools.robot_command = bridge.robot.emit

    # ---- Local dashboard: http://localhost:7777
    import server
    hub = server.Hub()
    # The island hides only while the command center is really in front. (Chrome reports a page as "visible"
    # even when other windows cover it, so ask Windows which window is in the foreground.)
    def _front_title():
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    dash_shown = {"v": False}

    def _check_dashboard():
        shown = hub.any_visible() and _front_title().startswith("A.T.O.M.O. Command Center")
        if shown != dash_shown["v"]:
            dash_shown["v"] = shown
            island.set_dashboard_visible(shown)

    dash_timer = QTimer(interval=600, timeout=_check_dashboard)
    dash_timer.start()
    assistant.hub = hub
    live["hub"] = hub
    hub.publish({"type": "ranking", "models": brain.health.table()})
    bridge.state.connect(lambda s, t, b, i: hub.publish({"type": "state", "state": s, "title": t, "body": b}))
    bridge.me.connect(lambda on: hub.publish({"type": "me", "on": on}))

    def set_muted(m):
        speaker.muted = m
        island.muted = m
        if m:
            speaker.stop()
        hub.publish({"type": "muted", "on": m})

    # ---- system-wide hand gestures (webcam + MediaPipe inside Jarvis, works in every app)
    import gestures

    def on_gesture(name):
        if name == "talk":
            assistant.on_click()
        elif name == "stop":
            if speaker.busy():
                dashboard_action("stop")
            else:
                tools.media_key("play_pause")
        elif name == "yes":
            assistant.on_text("yes")
        elif name == "fullscreen":
            if hub.any_visible():
                hub.publish({"type": "canvas_cmd", "action": "toggle_fullscreen"})
            else:
                tools.press_keys("f11")
        elif name in ("next", "prev"):
            tools.press_keys("right" if name == "next" else "left")

    gesture_engine = gestures.GestureEngine(on_gesture, hub.publish, hub.any_visible)

    def set_gestures(on: bool, mouse: bool = False) -> str:
        gesture_engine.mouse = bool(on and mouse)
        if not on:
            gesture_engine.stop()
            return ""
        err = gesture_engine.start()
        if err:
            hub.publish({"type": "gesture_error", "text": err})
        return err

    tools.set_gestures = set_gestures

    def dashboard_action(action, data=None):
        data = data or {}
        if action == "canvas_vscode":
            threading.Thread(target=tools.open_in_editor, args=(data.get("id"),), daemon=True).start()
            return
        if action == "canvas_save":
            threading.Thread(target=tools.save_creation, kwargs={"cid": data.get("id")}, daemon=True).start()
            return
        if action == "canvas_run":
            threading.Thread(target=tools.run_creation, args=(data.get("id"),), daemon=True).start()
            return
        if action == "open_screen":
            threading.Thread(target=tools.ensure_screen, daemon=True).start()
            return
        if action == "everyday":
            ev = tools.everyday
            op, lst, item = data.get("op"), str(data.get("list", ""))[:60], str(data.get("item", ""))[:200]
            if op == "cancel":
                ev.cancel_id(data.get("id"))
            elif op == "list_add" and item:
                ev.list_add([item], lst or "to-do")
            elif op == "list_remove" and item:
                ev.list_remove([item], lst or "to-do")
            elif op == "list_clear":
                ev.list_clear(lst)
            elif op == "delete_routine":
                ev.delete_routine(str(data.get("trigger", "")))
            elif op == "home" and data.get("command"):
                cmd = str(data["command"])[:160]
                def run_home():
                    result = tools.smart_home(cmd, str(data.get("device", ""))[:40])
                    hub.publish({"type": "home_result", "text": result})
                threading.Thread(target=run_home, daemon=True).start()
            elif op == "forget_device":
                tools.smarthome.forget_device(str(data.get("device", "")))
            elif op == "connect_home":
                threading.Thread(target=lambda: hub.publish({"type": "home_result", "text": tools.connect_google_home()}),
                                 daemon=True).start()
            elif op == "run_routine":
                assistant.on_text(str(data.get("trigger", ""))[:120])
            return
        if action == "gestures":
            threading.Thread(target=set_gestures, args=(bool(data.get("on")),), daemon=True).start()
            return
        if action == "talk":
            assistant.on_click()
        elif action == "me":
            set_me(not me_pending["on"])
        elif action == "mute":
            set_muted(not speaker.muted)
        elif action == "new_chat":
            brain.reset()
            hub.history.clear()
        elif action == "stop":
            assistant.turn += 1
            speaker.stop()
            bridge.state.emit("idle", "", "", "")
            listener.set_idle()

    server.start(hub, assistant.on_text, dashboard_action, lambda: (mic_level["v"], speaker.level()))
    hub.publish({"type": "me", "on": ME_MODE})

    def open_dashboard() -> str:
        exe = tools._chrome_exe()
        url = f"http://localhost:{server.PORT}"
        if exe:
            # Chrome "app" window: no tabs or address bar - looks like a native control centre.
            subprocess.Popen([exe, f"--app={url}", "--start-maximized"], creationflags=subprocess.DETACHED_PROCESS)
        else:
            import webbrowser
            webbrowser.open(url)
        return "OK: opened the Atomo command center dashboard."

    tools.open_dashboard = open_dashboard

    def ensure_dashboard():
        """Make sure the command center is on screen (for the creation canvas)."""
        if hub.any_visible():
            return
        if hub.listeners:                          # open but minimised / behind: bring it back
            try:
                import pygetwindow as gw
                for w in gw.getWindowsWithTitle("A.T.O.M.O. Command Center"):
                    if w.isMinimized:
                        w.restore()
                    w.maximize()
                    try:
                        w.activate()
                    except Exception:
                        pass
                    return
            except Exception:
                log.exception("could not restore the command center")
        open_dashboard()
        time.sleep(2.5)                            # let the page connect before content streams in

    tools.ensure_dashboard = ensure_dashboard

    # ---- Jarvis Screen: creations open in their own window (on a second monitor when there is one),
    # so the command center is never covered.
    SCREEN_TITLE = "A.T.O.M.O. Screen"

    def open_screen():
        exe = tools._chrome_exe()
        url = f"http://localhost:{server.PORT}/screen"
        if not exe:
            import webbrowser
            webbrowser.open(url)
            return
        args = [exe, f"--app={url}"]
        screens = app.screens()
        others = [sc for sc in screens if sc is not app.primaryScreen()]
        if others:
            g = others[0].availableGeometry()
            args += [f"--window-position={g.x()},{g.y()}", f"--window-size={g.width()},{g.height()}"]
        args.append("--start-maximized")
        subprocess.Popen(args, creationflags=subprocess.DETACHED_PROCESS)

    def ensure_screen():
        """Show the Jarvis Screen window (open it, or bring it back if minimised / behind)."""
        if hub.screen_open():
            try:
                import pygetwindow as gw
                for w in gw.getWindowsWithTitle(SCREEN_TITLE):
                    if w.isMinimized:
                        w.restore()
                    if not w.isMaximized:
                        w.maximize()
                    try:
                        w.activate()
                    except Exception:
                        pass
                    return
            except Exception:
                log.exception("could not raise the Jarvis Screen")
            return
        open_screen()
        for _ in range(40):                         # let the page connect before content streams in
            if hub.screen_open():
                time.sleep(0.3)
                return
            time.sleep(0.1)

    tools.ensure_screen = ensure_screen

    # ---- the command center's Daily panel (reminders, lists, routines) + weather
    hub.everyday = lambda: dict(tools.everyday.snapshot(), devices=tools.smarthome.devices(),
                                connected=tools.smarthome.TOKEN_FILE.exists())
    tools.everyday.publish = hub.publish
    tools.smarthome.publish = hub.publish

    def weather_loop():
        from urllib.request import Request, urlopen
        while True:
            try:
                req = Request("https://wttr.in/?format=%c%t|%C|%l", headers={"User-Agent": "curl/8"})
                text = urlopen(req, timeout=10).read().decode("utf-8", "replace").strip()
                if text and "<" not in text and len(text) < 160:
                    now, cond, place = (text.split("|") + ["", ""])[:3]
                    hub.publish({"type": "weather", "text": " ".join(now.split()), "cond": cond.strip(),
                                 "place": place.split(",")[0].strip().title()})
            except Exception as e:
                log.info("weather update failed: %s", e)
            time.sleep(20 * 60)

    threading.Thread(target=weather_loop, name="weather", daemon=True).start()

    # ---- Themes: "ios" (default) or "ironman", for both the island and the dashboard.
    def set_theme(theme=None, appearance=None):
        theme = theme or tools._state("theme", "ios")
        if theme not in ("ios", "ironman", "cinema"):
            return f"FAILED: unknown theme '{theme}'."
        appearance = appearance or tools._state("appearance", "light")
        if theme == "cinema":
            appearance = "dark"                         # the cinematic command center is always dark
        tools._save_state("theme", theme)
        tools._save_state("appearance", appearance)
        server.THEME["name"] = theme
        bridge.theme.emit(theme)
        bridge.appearance.emit(appearance)
        hub.publish({"type": "theme", "theme": theme, "appearance": appearance})
        name = {"ios": "iOS", "ironman": "Iron Man", "cinema": "Cinematic"}[theme]
        return f"OK: switched to the {name} theme ({appearance} mode)."

    server.THEME["name"] = tools._state("theme", "ios")
    hub.publish({"type": "theme", "theme": server.THEME["name"], "appearance": tools._state("appearance", "light")})
    tools.set_theme = set_theme

    # ---- Autopilot progress shows on the island and the dashboard.
    def task_progress(text):
        bridge.state.emit("action", text, "", "")
        hub.publish({"type": "progress", "text": text})

    tools.progress = task_progress
    tools.publish = hub.publish                    # generated code shows up in the dashboard feed

    # ---- Proactive alerts: battery low / fully charged.
    def battery_watch():
        warned_low = warned_full = False
        while True:
            time.sleep(60)
            b = psutil.sensors_battery()
            if b is None:
                return
            if not b.power_plugged and b.percent <= 20 and not warned_low:
                warned_low = True
                assistant.notify(f"Sir, the battery is at {round(b.percent)} percent. You may want to plug in.")
            if b.power_plugged:
                warned_low = False
                if b.percent >= 98 and not warned_full:
                    warned_full = True
                    assistant.notify("Sir, the battery is fully charged. You can unplug now.")
            else:
                warned_full = False

    import psutil
    threading.Thread(target=battery_watch, daemon=True, name="battery").start()

    def quit_app():
        assistant.events.put(("quit", None, None))
        listener.stop()
        QTimer.singleShot(1500, app.quit)

    bridge.quit.connect(quit_app)
    island.clicked.connect(assistant.on_click)
    island.quit_requested.connect(quit_app)
    island.mute_toggled.connect(set_muted)
    island.new_chat.connect(brain.reset)

    # Tray icon: a small arc-reactor dot.
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(0, 0, 0))
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(QColor(64, 200, 255))
    p.drawEllipse(20, 20, 24, 24)
    p.end()
    tray = QSystemTrayIcon(QIcon(pm))
    tray.setToolTip("Atomo - say \"OK Atomo\" or \"Hey Jarvis\"")
    menu = QMenu()
    for text, fn in (("Talk to Atomo", assistant.on_click),
                     ("Open command center", open_dashboard),
                     ("Toggle my voice and face", lambda: set_me(not me_pending["on"])),
                     ("New conversation", brain.reset), ("Quit Atomo", quit_app)):
        a = QAction(text, menu)
        a.triggered.connect(fn)
        menu.addAction(a)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and assistant.on_click())
    tray.show()

    island.show()
    assistant.start()
    listener.start()

    def check_mic():
        if listener.error:
            bridge.state.emit("error", "Microphone unavailable", listener.error[:80], "")

    QTimer.singleShot(2500, check_mic)
    assistant.events.put(("say", greeting(), None))

    # Let Ctrl+C in the console close the app.
    import signal
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(300)

    sys.exit(app.exec())


def run_text(args):
    from brain import Brain
    import tools

    brain = Brain(get_api_key(), MODEL, BASE_URL, BACKUP_MODELS, EXTRA_PROVIDERS)
    speaker = None
    if not args.mute:
        from speech import Speaker
        speaker = Speaker(voice_override=VOICE)
    tools.notify = lambda t: print(f"\n[reminder] {t}")
    print(f"Model: {MODEL} via {BASE_URL}. Type 'goodbye' to exit.")
    while True:
        try:
            text = input("YOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"goodbye", "exit", "quit"} | QUIT_PHRASES:
            break
        print("JARVIS: ", end="", flush=True)

        def on_sentence(s):
            print(s, end=" ", flush=True)
            if speaker:
                speaker.say(s)

        def on_tool_start(name, a):
            print(f"\n  [{name} {a}]", end="\n        ", flush=True)

        def on_tool_end(name, a, result):
            print(f"  -> {result[:160]}", end="\n        ", flush=True)

        try:
            brain.ask(text, on_sentence, on_tool_start, on_tool_end)
        except Exception as e:
            print(f"[error: {type(e).__name__}: {e}]")
        print()
        if speaker:
            speaker.wait()
    tools.YT.close()


def main():
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. desktop assistant")
    parser.add_argument("--text", action="store_true", help="type to Jarvis in the console")
    parser.add_argument("--mute", action="store_true", help="start with the voice muted")
    parser.add_argument("--no-wake", action="store_true", help="disable the wake word; click the island to talk")
    args = parser.parse_args()
    setup_logging()
    if args.text:
        run_text(args)
        return
    if not single_instance():
        log.info("Jarvis is already running.")
        return
    run_gui(args)


if __name__ == "__main__":
    main()
