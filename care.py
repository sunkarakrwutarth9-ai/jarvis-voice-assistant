"""Proactive care: Ultron speaks up on its own when it matters.

- Break reminders: after ~50 minutes of continuous keyboard/mouse activity -> stand up, stretch, drink water.
- Heavy load: CPU above 90% for 3 minutes -> says which app is responsible.
- Morning briefing: the first time you're at the PC each morning (5-11 AM), a short briefing - once a day.
Never during full-screen video / games / presentations, never at night (11 PM - 6 AM).
Each kind can be switched off by voice ("stop break reminders").
"""

import ctypes
import datetime
import logging
import random
import threading
import time

log = logging.getLogger("jarvis.care")
BREAK_AFTER = 50 * 60
_cfg = {"breaks": True, "load": True, "morning": True}
hooks = {"say": None, "briefing": None, "state": None, "save": None}

BREAK_LINES = [
    "Sir, you've been at it for {m} minutes. Stand up, stretch, and drink some water.",
    "{m} minutes straight, Sir. A two-minute break will sharpen your focus. Look away from the screen for a moment.",
    "Time for a quick break, Sir. {m} minutes of screen time. Your eyes and back will thank you.",
]


def _fullscreen():
    st = ctypes.c_int(0)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st))
    except Exception:
        return False
    return st.value in (2, 3, 4)


def _loop():
    import psutil
    from daylog import idle_seconds
    streak_start, last_break, hot_since, last_hot = time.time(), 0.0, None, 0.0
    psutil.cpu_percent(None)
    while True:
        time.sleep(30)
        try:
            now, hour = time.time(), datetime.datetime.now().hour
            idle = idle_seconds()
            night = hour >= 23 or hour < 6
            if idle > 300:
                streak_start = now
            # morning briefing: first activity of the day, 5-11 AM
            today = datetime.date.today().isoformat()
            if (_cfg["morning"] and 5 <= hour < 12 and idle < 60 and hooks["state"] and
                    hooks["state"]("care_briefing", "") != today and now - _started > 90):
                hooks["save"]("care_briefing", today)
                if not _fullscreen():
                    log.info("care: morning briefing")
                    hooks["briefing"]()
                    continue
            if night or _fullscreen():
                continue
            if _cfg["breaks"] and now - streak_start >= BREAK_AFTER and now - last_break >= BREAK_AFTER:
                last_break = now
                m = int((now - streak_start) // 60)
                hooks["say"](random.choice(BREAK_LINES).format(m=m))
            if _cfg["load"]:
                cpu = psutil.cpu_percent(None)
                if cpu > 90:
                    hot_since = hot_since or now
                    if now - hot_since > 180 and now - last_hot > 1800:
                        last_hot = now
                        procs = sorted(psutil.process_iter(["name", "cpu_percent"]),
                                       key=lambda p: p.info["cpu_percent"] or 0, reverse=True)
                        top = next((p.info["name"] for p in procs if p.info["name"] not in ("System Idle Process",)), "an app")
                        hooks["say"](f"Sir, the CPU has been above 90 percent for three minutes. {top.rsplit('.', 1)[0]} is the "
                                     f"heaviest. Shall I close it?")
                else:
                    hot_since = None
        except Exception:
            log.exception("care check failed")


_started = time.time()


def start(cfg):
    _cfg.update(cfg or {})
    threading.Thread(target=_loop, name="care", daemon=True).start()


def proactive(kind: str = "all", on: bool = True) -> str:
    kinds = ["breaks", "load", "morning"] if kind in ("all", "", None) else [k for k in ("breaks", "load", "morning") if k in kind]
    if not kinds:
        return "FAILED: kind must be breaks, load, morning or all."
    for k in kinds:
        _cfg[k] = bool(on)
    if hooks["save"]:
        hooks["save"]("care", dict(_cfg))
    names = {"breaks": "break reminders", "load": "heavy-load warnings", "morning": "morning briefings"}
    return f"OK: {', '.join(names[k] for k in kinds)} {'on' if on else 'off'}."
