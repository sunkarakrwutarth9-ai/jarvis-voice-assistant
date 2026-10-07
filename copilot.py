"""Screen copilot: while the user allows it, Atomo glances at the screen when it changes and speaks up
only when it can help - an error dialog, a failed build, an exception, a broken page, a bug in code.

Privacy: it's off until asked, shows a visible indicator, switches itself off after the chosen time,
never looks at windows that look like passwords / banking / payments, and keeps no screenshots.
"""

import ctypes
import logging
import re
import threading
import time

log = logging.getLogger("jarvis.copilot")
MIN_GAP = 20          # seconds between two looks
PRIVATE = re.compile(r"password|passcode|sign ?in|log ?in|bank|upi|payment|checkout|paytm|phonepe|gpay|otp|"
                     r"credit card|wallet|incognito|inprivate|private browsing|1password|bitwarden|keepass", re.I)
_state = {"on": False, "until": 0.0, "thread": None, "last_tip": ""}


def _front_title():
    u32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(300)
    u32.GetWindowTextW(u32.GetForegroundWindow(), buf, 300)
    return buf.value


def _thumb():
    from PIL import ImageGrab
    return ImageGrab.grab().convert("L").resize((96, 54))


def _diff(a, b):
    import numpy as np
    return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())


def _loop():
    import tools
    last_look, prev, prev_title, stable_since = 0.0, None, "", time.time()
    analysed = None
    while _state["on"] and time.time() < _state["until"]:
        time.sleep(3)
        try:
            title = _front_title()
            if PRIVATE.search(title) or "A.T.O.M.O" in title:
                prev = None
                continue
            th = _thumb()
            if prev is not None and _diff(th, prev) > 2.5:
                stable_since = time.time()             # still changing (scrolling / typing): wait
            prev = th
            changed = analysed is None or _diff(th, analysed) > 6 or title != prev_title
            if not changed or time.time() - stable_since < 2 or time.time() - last_look < MIN_GAP:
                continue
            last_look, analysed, prev_title = time.time(), th, title
            b64, _ = tools._screenshot_b64(1400)
            reply = tools._vision(
                "You are a screen copilot quietly watching the user's PC (window: '" + title[:80] + "'). "
                "If the screen shows an error message, exception or stack trace, a failed build/test, a warning dialog, "
                "a broken web page, a form the user seems stuck on, or code with an obvious bug, reply with ONE short "
                "practical tip (max 2 sentences, what it means and how to fix it) starting with 'TIP:'. "
                "Otherwise - normal browsing, videos, documents, chats - reply exactly NONE. "
                "Never read out personal data, passwords or messages.", b64, max_tokens=160).strip()
            m = re.search(r"TIP:\s*(.+)", reply, re.S)
            if not m:
                continue
            tip = " ".join(m.group(1).split())[:300]
            if tip.lower()[:60] == _state["last_tip"].lower()[:60]:
                continue
            _state["last_tip"] = tip
            log.info("copilot tip: %s", tip)
            tools.publish({"type": "copilot_tip", "text": tip, "window": title[:80]})
            tools.notify("Sir, " + tip)
        except Exception as e:
            log.warning("copilot look failed: %s", str(e)[:150])
            time.sleep(10)
    _state["on"] = False
    tools.publish({"type": "copilot", "on": False})
    log.info("screen copilot off")


def screen_copilot(on: bool = True, minutes: int = 60) -> str:
    import tools
    if not on:
        _state["on"] = False
        tools.publish({"type": "copilot", "on": False})
        return "OK: screen copilot off; Atomo is no longer looking at the screen."
    minutes = max(5, min(int(minutes or 60), 240))
    _state["until"] = time.time() + minutes * 60
    if not _state["on"]:
        _state["on"] = True
        _state["thread"] = threading.Thread(target=_loop, name="copilot", daemon=True)
        _state["thread"].start()
    tools.publish({"type": "copilot", "on": True, "until": _state["until"]})
    return (f"OK: screen copilot on for {minutes} minutes - Atomo glances at the screen when it changes and speaks up "
            f"only to help with errors or problems (never on password, banking or payment windows).")
