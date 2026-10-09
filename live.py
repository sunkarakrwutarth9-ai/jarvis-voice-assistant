"""Live voice: real-time, interruptible conversation with Gemini Live (like talking to a person).

The microphone streams straight to Gemini; Ultron's voice streams back while it is still thinking. You can cut it
off mid-sentence just by talking. It can still use Ultron's tools (open apps, YouTube, weather, reminders, the
screen...). Started with "live mode" / "talk to me live", stopped with "stop live mode", Ultron off, or by itself
after two quiet minutes (so it never sits there listening while you work).
"""
import asyncio
import base64
import datetime
import json
import logging
import os
import queue
import threading
import time

import numpy as np
import pyaudio

log = logging.getLogger("jarvis.live")
URL = ("wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService."
       "BidiGenerateContent?key=")
MODEL = os.environ.get("JARVIS_LIVE_MODEL", "models/gemini-3.8-live")
VOICE = os.environ.get("JARVIS_LIVE_VOICE", "Charon")
OUT_RATE = 24000
IDLE_STOP = 120                    # seconds without the user speaking -> live mode ends quietly

# Tools Live may use (kept small so the session starts fast).
LIVE_TOOLS = ["open_app", "close_app", "open_website", "web_search", "web_lookup", "youtube_play", "youtube_control",
              "media_key", "set_volume", "set_brightness", "get_weather", "world_time", "set_reminder", "set_timer",
              "list_add", "list_show", "remember", "recall", "take_screenshot", "read_screen", "look", "system_status",
              "calculate", "type_text", "press_keys", "show_dashboard", "study_mode", "focus_mode", "create",
              "deep_think", "briefing", "calendar_agenda", "calendar_add", "email_search", "email_draft",
              "search_my_files", "diagnostics", "smart_home", "ui_theme", "lessons", "live_mode", "ultron_power"]

PROMPT = """You are ULTRON, the user's personal AI assistant on their Windows PC, talking LIVE by voice (the user may
call you 'Jarvis' - your name is Ultron). Address the user as Sir. Speak naturally and briefly, like a sharp,
loyal, witty butler - one or two sentences unless they ask for more. Reply in the language the user speaks
(English, Telugu or Hindi; mixing is fine). Use your tools whenever the user wants something DONE on the PC and
never claim you did something without the tool. If the user says stop / that's all / exit live mode, call
live_mode with on=false. If they want you off or quiet, call ultron_power. If you hear background noise, a video,
music or other people (not the user talking to you), stay silent."""


def _gemini_schema(s):
    """OpenAI/JSON-schema parameters -> the Gemini schema dialect."""
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k in ("additionalProperties", "default", "$schema", "title"):
            continue
        if k == "type" and isinstance(v, str):
            out[k] = v.upper()
        elif k == "properties":
            out[k] = {n: _gemini_schema(p) for n, p in v.items()}
        elif k == "items":
            out[k] = _gemini_schema(v)
        elif k == "enum":
            vals = [str(x) for x in v if str(x).strip()]      # Gemini rejects empty / non-string choices
            if vals:
                out[k] = vals
        else:
            out[k] = v
    return out


class Live:
    def __init__(self, listener, publish, on_state, run_tool, tool_defs, context, on_turn=None, on_heard=None):
        self.listener, self.publish, self.on_state = listener, publish, on_state
        self.run_tool, self.tool_defs, self.context, self.on_turn = run_tool, tool_defs, context, on_turn
        self.on_heard = on_heard                # tells the safety checks what the user just said
        self.on = False
        self._thread = None
        self._loop = None
        self._mic = None                        # asyncio.Queue of PCM chunks
        self._play = queue.Queue()
        self._play_until = 0.0
        self._last_user = 0.0
        self._handle = None                     # session resumption handle (reconnect keeps the conversation)
        self._user_txt, self._reply_txt = [], []

    # ------------------------------------------------------------------ control
    def start(self):
        if self.on:
            return "OK: live voice is already on."
        if not os.environ.get("GEMINI_API_KEY"):
            return "FAILED: live voice needs a Gemini key (🔑 API keys in the command center)."
        self.on = True
        self._last_user = time.monotonic()
        self._handle = None
        self._thread = threading.Thread(target=self._run, name="live", daemon=True)
        self._thread.start()
        threading.Thread(target=self._player, name="live-audio", daemon=True).start()
        return "OK: live voice is on - just talk; interrupt me any time."

    def stop(self, reason=""):
        if not self.on:
            return "OK: live voice is already off."
        self.on = False
        self._flush()
        self.listener.tap = None
        self.listener.set_idle()
        self.publish({"type": "live", "on": False})
        self.on_state("idle", "")
        log.info("live voice OFF %s", reason)
        if self._loop and self._mic:
            self._loop.call_soon_threadsafe(self._mic.put_nowait, None)
        return "OK: live voice off."

    # ------------------------------------------------------------------ microphone (listener thread)
    def _tap(self, data, rms, noise):
        if not self.on or self._loop is None or self._mic is None:
            return
        if time.monotonic() < self._play_until:
            # Ultron is talking: only a clearly louder voice (the user interrupting) gets through, so the
            # speakers' own sound isn't mistaken for the user.
            if rms < max(1800.0, noise * 6.0):
                data = bytes(len(data))
        self._loop.call_soon_threadsafe(self._mic.put_nowait, data)

    # ------------------------------------------------------------------ speaker
    def _player(self):
        pa = pyaudio.PyAudio()
        out = pa.open(format=pyaudio.paInt16, channels=1, rate=OUT_RATE, output=True, frames_per_buffer=1024)
        try:
            while self.on or not self._play.empty():
                try:
                    chunk = self._play.get(timeout=0.3)
                except queue.Empty:
                    continue
                if chunk is None:
                    continue
                out.write(chunk)
        finally:
            out.stop_stream()
            out.close()
            pa.terminate()

    def _queue_audio(self, pcm):
        now = time.monotonic()
        self._play_until = max(self._play_until, now) + len(pcm) / (2 * OUT_RATE)
        self._play.put(pcm)

    def _flush(self):
        try:
            while True:
                self._play.get_nowait()
        except queue.Empty:
            pass
        self._play_until = 0.0

    # ------------------------------------------------------------------ session
    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_loop())
        except Exception:
            log.exception("live voice crashed")
        finally:
            if self.on:
                self.on = False
                self.listener.tap = None
                self.listener.set_idle()
                self.publish({"type": "live", "on": False})
                self.on_state("idle", "")
            self._loop.close()
            self._loop = None

    async def _session_loop(self):
        import websockets
        self._mic = asyncio.Queue()
        self.listener.tap = self._tap
        self.listener.mode = "tap"
        self.publish({"type": "live", "on": True})
        self.on_state("listening", "Live - just talk")
        log.info("live voice ON (%s)", MODEL)
        tries = 0
        while self.on:
            try:
                async with websockets.connect(URL + os.environ["GEMINI_API_KEY"], max_size=None,
                                              ping_interval=20) as ws:
                    await ws.send(json.dumps({"setup": self._setup()}))
                    await asyncio.wait_for(ws.recv(), 15)          # setupComplete
                    tries = 0
                    sender = asyncio.create_task(self._send_mic(ws))
                    try:
                        await self._receive(ws)
                    finally:
                        sender.cancel()
            except Exception as e:
                if not self.on:
                    break
                tries += 1
                log.warning("live connection dropped (%s); reconnecting", str(e)[:120])
                if tries > 3:
                    self.publish({"type": "reply", "text": "Live voice lost its connection, Sir.", "model": "live"})
                    self.stop("connection failed")
                    break
                await asyncio.sleep(1.5 * tries)

    def _setup(self):
        now = datetime.datetime.now().astimezone()
        system = (PROMPT + f"\n\nNow: {now:%A %d %B %Y, %I:%M %p} (India).")
        try:
            extra = self.context()
            if extra:
                system += "\n\n" + extra
        except Exception:
            pass
        decls = []
        for t in self.tool_defs():
            f = t["function"]
            if f["name"] not in LIVE_TOOLS:
                continue
            d = {"name": f["name"], "description": f.get("description", "")[:900]}
            params = f.get("parameters") or {}
            if params.get("properties"):
                d["parameters"] = _gemini_schema(params)
            decls.append(d)
        setup = {
            "model": MODEL,
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE}}}},
            "systemInstruction": {"parts": [{"text": system}]},
            "tools": [{"functionDeclarations": decls}],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "contextWindowCompression": {"slidingWindow": {}},
            "sessionResumption": {"handle": self._handle} if self._handle else {},
        }
        return setup

    async def _send_mic(self, ws):
        while self.on:
            data = await self._mic.get()
            if data is None:
                break
            # batch whatever is waiting (keeps the socket calm)
            while not self._mic.empty() and len(data) < 6400:
                more = self._mic.get_nowait()
                if more is None:
                    break
                data += more
            await ws.send(json.dumps({"realtimeInput": {"audio": {
                "data": base64.b64encode(data).decode(), "mimeType": "audio/pcm;rate=16000"}}}))
            if time.monotonic() - self._last_user > IDLE_STOP and time.monotonic() > self._play_until:
                self.publish({"type": "reply", "text": "Live voice ended (quiet for 2 minutes).", "model": "live"})
                self.stop("idle")
                break

    async def _receive(self, ws):
        async for raw in ws:
            if not self.on:
                return
            msg = json.loads(raw)
            if "sessionResumptionUpdate" in msg:
                h = msg["sessionResumptionUpdate"].get("newHandle")
                if h:
                    self._handle = h
            if "goAway" in msg:
                log.info("live: server asked to reconnect")
                return
            if "toolCall" in msg:
                await self._tools(ws, msg["toolCall"].get("functionCalls", []))
                continue
            sc = msg.get("serverContent")
            if not sc:
                continue
            if sc.get("interrupted"):
                self._flush()                              # the user started talking: stop speaking at once
                self.on_state("listening", "Live - listening")
            if "inputTranscription" in sc:
                t = sc["inputTranscription"].get("text", "")
                if t.strip():
                    self._user_txt.append(t)
                    self._last_user = time.monotonic()
                    self.on_state("listening", "".join(self._user_txt)[-120:])
            for p in sc.get("modelTurn", {}).get("parts", []):
                if "inlineData" in p:
                    self._queue_audio(base64.b64decode(p["inlineData"]["data"]))
                    self.on_state("speaking", "".join(self._reply_txt)[-120:])
            if "outputTranscription" in sc:
                self._reply_txt.append(sc["outputTranscription"].get("text", ""))
            if sc.get("turnComplete") or sc.get("generationComplete"):
                self._finish_turn()

    def _finish_turn(self):
        user, reply = "".join(self._user_txt).strip(), "".join(self._reply_txt).strip()
        self._user_txt, self._reply_txt = [], []
        if user:
            self.publish({"type": "user", "text": user, "heard": {"live": user}})
        if reply:
            self.publish({"type": "reply", "text": reply, "model": "live"})
        if (user or reply) and self.on_turn:
            try:
                self.on_turn(user, reply)
            except Exception:
                log.exception("live on_turn failed")

    async def _tools(self, ws, calls):
        loop = asyncio.get_running_loop()
        responses = []
        for fc in calls:
            name, args = fc.get("name", ""), fc.get("args") or {}
            if self.on_heard:
                self.on_heard("".join(self._user_txt))
            self.on_state("thinking", f"{name.replace('_', ' ')}…")
            if name == "live_mode" and not args.get("on", False):
                result = "OK: live voice turning off. Say a short goodbye."
                loop.call_later(4.0, lambda: self.stop("asked"))
            else:
                _a, result = await loop.run_in_executor(None, self.run_tool, name, json.dumps(args))
            log.info("live tool %s(%s) -> %s", name, args, str(result)[:160])
            self.publish({"type": "tool", "name": name, "args": args, "result": str(result)[:500]})
            responses.append({"id": fc.get("id"), "name": name, "response": {"result": str(result)[:4000]}})
        await ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
