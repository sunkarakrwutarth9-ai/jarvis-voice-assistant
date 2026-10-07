"""Telegram anywhere: talk to Ultron and control the PC from anywhere in the world through your own private
Telegram bot - text, voice notes (any language), photos ("what's this?"), files and quick commands.

How it connects: Ultron polls Telegram's servers over HTTPS (no ports opened on this PC, works behind any
router). Who can use it: only the account that pairs with a one-time 6-digit code shown on the PC;
everyone else gets "private assistant" and is ignored. The bot token is kept in .env (never committed).

Extras: when you're away from the PC (idle > 10 min), Ultron's reminders and alerts are also sent to
Telegram; creations (pages, notes, reports) arrive as files; /screen sends a screenshot.
"""

import base64
import io
import json
import logging
import os
import random
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("jarvis.telegram")
API = "https://api.telegram.org"
ctx = {"hub": None, "on_command": None, "on_action": None, "state": None, "save": None, "ask_token": None}
_st = {"token": None, "thread": None, "owner": None, "pair": None, "pair_until": 0, "active_until": 0, "stop": False}
HELP = ("🛰 *Ultron is connected.* Just talk to me like on the PC:\n"
        "• type or send a *voice note* (English / Telugu / Hindi)\n• send a *photo* with a question\n"
        "• send a *file* (PDF, doc) with what to do\n\nQuick commands: /screen /status /briefing /day /stop /help")


def _call(method, **data):
    files = data.pop("_files", None)
    r = httpx.post(f"{API}/bot{_st['token']}/{method}", data=data if files else None, json=None if files else data,
                   files=files, timeout=60)
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(out.get("description", "telegram error"))
    return out["result"]


def send(text, chat=None):
    chat = chat or _st["owner"]
    if not (_st["token"] and chat and text):
        return
    for i in range(0, len(text), 3900):
        try:
            _call("sendMessage", chat_id=chat, text=text[i:i + 3900], parse_mode="Markdown")
        except Exception:
            try:
                _call("sendMessage", chat_id=chat, text=text[i:i + 3900])
            except Exception as e:
                log.warning("telegram send failed: %s", e)


def push(text):
    """Proactive alerts while the user is away from the PC."""
    try:
        from daylog import idle_seconds
        if _st["owner"] and idle_seconds() > 600:
            send("🔔 " + text)
    except Exception:
        pass


# ------------------------------------------------------------------ forward Ultron's answers to the chat
def _forwarder():
    q = ctx["hub"].subscribe()
    while not _st["stop"]:
        try:
            e = json.loads(q.get(timeout=5))
        except Exception:
            continue
        if time.time() > _st["active_until"] or not _st["owner"]:
            continue
        kind = e.get("type")
        try:
            if kind == "reply" and e.get("text"):
                send(e["text"])
                _st["active_until"] = time.time() + 20       # a little longer for follow-ups (notes, files)
            elif kind == "tool" and e.get("result") is None:
                _call("sendChatAction", chat_id=_st["owner"], action="typing")
            elif kind == "canvas" and e.get("content"):
                ext = e.get("lang") or "txt"
                name = (e.get("title") or "atomo").replace("/", "-")[:50] + "." + ext
                _call("sendDocument", chat_id=_st["owner"], caption="🧩 " + (e.get("title") or ""),
                      _files={"document": (name, e["content"].encode("utf-8"))})
            elif kind == "copilot_tip":
                send("👁 " + e.get("text", ""))
        except Exception as ex:
            log.warning("telegram forward failed: %s", ex)


# ------------------------------------------------------------------ incoming messages
def _download(file_id):
    f = _call("getFile", file_id=file_id)
    return httpx.get(f"{API}/file/bot{_st['token']}/{f['file_path']}", timeout=60).content


def _voice_to_text(data):
    import numpy as np
    import soundfile as sf
    import tools
    audio, sr = sf.read(io.BytesIO(data), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    buf = io.BytesIO()
    sf.write(buf, audio, 16000, format="WAV", subtype="PCM_16")
    return tools.transcribe_audio(base64.b64encode(buf.getvalue()).decode(),
                                  "Transcribe exactly what is said, in the language spoken (native script). "
                                  "Reply with only the words.").strip()


def _handle(m):
    import tools
    chat = m["chat"]["id"]
    text = (m.get("text") or m.get("caption") or "").strip()
    # pairing
    if _st["owner"] != chat:
        code = text.replace("/pair", "").replace("/start", "").strip()
        if _st["pair"] and time.time() < _st["pair_until"] and code == _st["pair"]:
            _st["owner"], _st["pair"] = chat, None
            ctx["save"]("telegram_owner", chat)
            send(HELP, chat)
            ctx["hub"].publish({"type": "reply", "text": "📲 Telegram paired with " + m["chat"].get("first_name", "you")})
            log.info("telegram paired with chat %s", chat)
        else:
            try:
                _call("sendMessage", chat_id=chat, text="🔒 This is a private assistant.")
            except Exception:
                pass
            log.warning("telegram: ignored a message from an unpaired chat")
        return
    _st["active_until"] = time.time() + 120
    if text in ("/start", "/help"):
        return send(HELP)
    if text == "/screen":
        b64, _ = tools._screenshot_b64(1600)
        _call("sendPhoto", chat_id=chat, caption="🖥 Your screen right now",
              _files={"photo": ("screen.jpg", base64.b64decode(b64))})
        return
    if text == "/status":
        return send("🖥 " + tools.system_status())
    if text == "/stop":
        ctx["on_action"]("stop", {})
        return send("⏹ Stopped.")
    shortcuts = {"/briefing": "good morning, give me my briefing", "/day": "what did I do today?"}
    if text in shortcuts:
        return ctx["on_command"](shortcuts[text])
    if m.get("voice") or m.get("audio"):
        _call("sendChatAction", chat_id=chat, action="typing")
        heard = _voice_to_text(_download((m.get("voice") or m.get("audio"))["file_id"]))
        if not heard:
            return send("🎙 I couldn't make that out - try again?")
        send(f"🎙 _{heard}_")
        return ctx["on_command"](heard)
    if m.get("photo"):
        _call("sendChatAction", chat_id=chat, action="typing")
        img = _download(m["photo"][-1]["file_id"])
        answer = tools._vision((text or "What is this? Describe it and anything useful about it.") +
                               " Answer concisely for a phone chat.", base64.b64encode(img).decode(), 700)
        ctx["hub"].publish({"type": "user", "text": "📷 " + (text or "photo from Telegram")})
        return send(answer or "I couldn't analyse that photo.")
    if m.get("document"):
        d = m["document"]
        folder = Path.home() / "Downloads" / "Ultron Telegram"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / Path(d.get("file_name") or "file").name
        path.write_bytes(_download(d["file_id"]))
        send(f"📎 Saved to {path}")
        return ctx["on_command"]((text or "Explain this file briefly") + f": {path}")
    if text:
        ctx["on_command"](text)


def _poll():
    offset = None
    while not _st["stop"]:
        try:
            ups = _call("getUpdates", timeout=25, offset=offset, allowed_updates=["message"])
        except Exception as e:
            log.warning("telegram poll failed: %s", str(e)[:120])
            time.sleep(10)
            continue
        for u in ups:
            offset = u["update_id"] + 1
            if "message" in u:
                try:
                    _handle(u["message"])
                except Exception:
                    log.exception("telegram message failed")


def _start(token):
    _st.update(token=token, stop=False, owner=ctx["state"]("telegram_owner", None))
    me = _call("getMe")
    threading.Thread(target=_poll, name="telegram", daemon=True).start()
    threading.Thread(target=_forwarder, name="telegram-out", daemon=True).start()
    log.info("telegram bot @%s online (paired: %s)", me.get("username"), bool(_st["owner"]))
    return me


def resume():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        try:
            _start(token)
        except Exception as e:
            log.warning("telegram bot could not start: %s", e)


def connect_telegram(new_pairing: bool = False) -> str:
    """Set up / pair the private Telegram bot."""
    import tools
    if not _st["token"]:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token and ctx["ask_token"]:
            token = (ctx["ask_token"]() or "").strip()          # the user pastes it into Ultron's own dialog
        if not token:
            return ("FAILED: no bot token yet. Tell the user: open Telegram, message @BotFather, send /newbot, pick a "
                    "name, copy the token it gives, then say 'connect Telegram' again and paste it in the box.")
        try:
            me = _start(token)
        except Exception as e:
            return f"FAILED: Telegram rejected that token ({e})."
        os.environ["TELEGRAM_BOT_TOKEN"] = token
        from dotenv import set_key
        set_key(str(Path(__file__).resolve().parent / ".env"), "TELEGRAM_BOT_TOKEN", token)
    else:
        me = _call("getMe")
    if _st["owner"] and not new_pairing:
        send("👋 Ultron here - connected and ready.")
        return f"OK: Telegram is already paired (bot @{me.get('username')}); I just sent you a hello there."
    _st["pair"], _st["pair_until"] = f"{random.randint(0, 999999):06d}", time.time() + 600
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{{margin:0;background:#05070f;color:#eef4ff;
font:17px "Segoe UI",system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}}
h1{{font:600 26px Bahnschrift;letter-spacing:4px;color:#7fe6ff}}.c{{font:600 64px Bahnschrift;letter-spacing:14px;color:#f4ba42;margin:16px 0}}
p{{color:#8b97b5}}</style></head><body><div><h1>📲 PAIR TELEGRAM</h1><p>In Telegram open <b>@{me.get('username')}</b> and send:</p>
<div class="c">/pair {_st['pair']}</div><p>The code works once and expires in 10 minutes. Only the account that pairs can use Ultron.</p></div></body></html>"""
    tools.show_content("webpage", "Pair Telegram", "html", page)
    return (f"OK: bot @{me.get('username')} is online. The pairing code is on the Ultron Screen: the user must open "
            f"@{me.get('username')} in Telegram and send /pair followed by the code within 10 minutes.")
