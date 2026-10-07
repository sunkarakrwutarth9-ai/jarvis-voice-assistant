"""Everyday assistant features (the Alexa side of Jarvis): reminders and alarms at a clock time,
to-do / shopping lists and routines. Everything is stored in small JSON files next to this one
(reminders.json, lists.json, routines.json) and stays on this PC.
"""

import datetime
import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.everyday")
HERE = Path(__file__).resolve().parent
REMINDERS = HERE / "reminders.json"
LISTS = HERE / "lists.json"
ROUTINES = HERE / "routines.json"
_lock = threading.Lock()

notify = lambda text: log.info("reminder: %s", text)       # set by jarvis.py (speaks + shows on the island)
alarm = None                                               # optional hook: ring an alarm sound
run_command = None                                         # set by jarvis.py: execute a scheduled command
publish = lambda event: None                               # command-center events


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=_announce, daemon=True).start()     # outside the lock


def snapshot() -> dict:
    """Everything the command center's Daily panel shows."""
    items = sorted(_load(REMINDERS, []), key=lambda r: r["due"])
    return {"reminders": [{**r, "when": _say_time(datetime.datetime.fromisoformat(r["due"]))} for r in items],
            "lists": _load(LISTS, {}), "routines": _load(ROUTINES, {})}


def _announce():
    try:
        publish({"type": "everyday", **snapshot()})
    except Exception:
        log.exception("could not publish the daily panel")


# ------------------------------------------------------------------ reminders & alarms
REPEATS = {"", "daily", "weekdays", "weekends", "weekly"}


def _parse_when(at: str, in_minutes: float, now: datetime.datetime):
    if in_minutes:
        return now + datetime.timedelta(minutes=float(in_minutes))
    at = (at or "").strip().lower().replace(".", ":")
    if not at:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p", "%d-%m-%Y %H:%M"):
        try:
            return datetime.datetime.strptime(at.upper() if "%p" in fmt else at, fmt)
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", at)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    return due if due > now else due + datetime.timedelta(days=1)


def _next(due: datetime.datetime, repeat: str):
    step = datetime.timedelta(days=7 if repeat == "weekly" else 1)
    due += step
    while (repeat == "weekdays" and due.weekday() >= 5) or (repeat == "weekends" and due.weekday() < 5):
        due += datetime.timedelta(days=1)
    return due


def _say_time(d: datetime.datetime):
    now = datetime.datetime.now()
    day = "today" if d.date() == now.date() else (
        "tomorrow" if d.date() == now.date() + datetime.timedelta(days=1) else d.strftime("%A %d %B"))
    return f"{d:%I:%M %p}".lstrip("0") + f" {day}"


def schedule_task(command: str, at: str = "", in_minutes: float = 0, repeat: str = "") -> str:
    """Run any Atomo command later / every day: 'every day at 7 give me my briefing', 'at 9 pm play lofi'."""
    command = (command or "").strip()
    if not command:
        return "FAILED: what should I do at that time?"
    out = set_reminder(command, at, in_minutes, repeat, False, command=command)
    return out.replace("OK: Reminder set", "OK: Task scheduled", 1)


def set_reminder(text: str, at: str = "", in_minutes: float = 0, repeat: str = "", alarm: bool = False, command: str = "") -> str:
    """Reminder / alarm at a clock time ('18:30', '7 am', '2026-10-07 09:00') or in N minutes."""
    now = datetime.datetime.now()
    due = _parse_when(at, in_minutes, now)
    if due is None:
        return "FAILED: give the time as HH:MM (24-hour), like '18:30', or a date and time 'YYYY-MM-DD HH:MM', or in_minutes."
    if due <= now:
        return "FAILED: that time has already passed."
    repeat = (repeat or "").lower().strip()
    repeat = repeat if repeat in REPEATS else ""
    with _lock:
        items = _load(REMINDERS, [])
        items.append({"id": int(time.time() * 1000), "text": (text or "Alarm").strip(), "due": due.isoformat(timespec="seconds"),
                      "repeat": repeat, "alarm": bool(alarm), "command": command})
        _save(REMINDERS, items)
    what = "Alarm" if alarm else "Reminder"
    return f"OK: {what} set for {_say_time(due)}" + (f", repeating {repeat}" if repeat else "") + f": {text}."


def list_reminders() -> str:
    items = sorted(_load(REMINDERS, []), key=lambda r: r["due"])
    if not items:
        return "OK: no reminders or alarms are set."
    return "OK: " + "; ".join(
        f"{'task' if r.get('command') else 'alarm' if r['alarm'] else 'reminder'} {_say_time(datetime.datetime.fromisoformat(r['due']))}"
        + (f" ({r['repeat']})" if r["repeat"] else "") + f": {r['text']}" for r in items)


def cancel_reminder(match: str = "", all: bool = False) -> str:
    with _lock:
        items = _load(REMINDERS, [])
        if all:
            _save(REMINDERS, [])
            return f"OK: cancelled all {len(items)} reminders and alarms."
        words = [w for w in re.findall(r"\w+", (match or "").lower()) if len(w) > 2]
        keep = [r for r in items if not (words and all_words(words, r))]
        if len(keep) == len(items):
            return "FAILED: no reminder matches that. " + list_reminders()[4:]
        _save(REMINDERS, keep)
    return f"OK: cancelled {len(items) - len(keep)} reminder(s)."


def cancel_id(rid) -> str:
    with _lock:
        items = _load(REMINDERS, [])
        keep = [r for r in items if str(r["id"]) != str(rid)]
        _save(REMINDERS, keep)
    return f"OK: cancelled {len(items) - len(keep)} reminder(s)."


def all_words(words, r):
    hay = (r["text"] + " " + r["due"] + " " + ("alarm" if r["alarm"] else "reminder")).lower()
    hay += " " + datetime.datetime.fromisoformat(r["due"]).strftime("%I:%M %p %H:%M").lower()
    return any(w in hay for w in words)


def _ring(r):
    if r.get("command") and run_command:
        log.info("scheduled task: %s", r["command"])
        run_command(r["command"])
        return
    if r["alarm"] and alarm:
        try:
            alarm()
        except Exception:
            log.exception("alarm sound failed")
    notify(("Sir, it's " + f"{datetime.datetime.now():%I:%M %p}".lstrip("0") + f". Your alarm: {r['text']}.")
           if r["alarm"] else f"Sir, a reminder: {r['text']}.")


def _scheduler():
    while True:
        time.sleep(5)
        try:
            now = datetime.datetime.now()
            due = []
            with _lock:
                items = _load(REMINDERS, [])
                keep = []
                for r in items:
                    d = datetime.datetime.fromisoformat(r["due"])
                    if d <= now:
                        if now - d < datetime.timedelta(hours=12):    # missed while the PC slept: still say it
                            due.append(r)
                        if r["repeat"]:
                            while d <= now:
                                d = _next(d, r["repeat"])
                            keep.append({**r, "due": d.isoformat(timespec="seconds")})
                    else:
                        keep.append(r)
                if due or len(keep) != len(items) or keep != items:
                    _save(REMINDERS, keep)
            for r in due:
                log.info("reminder due: %s", r["text"])
                _ring(r)
        except Exception:
            log.exception("reminder scheduler error")


def start():
    threading.Thread(target=_scheduler, name="reminders", daemon=True).start()


# ------------------------------------------------------------------ lists
def _list_name(name):
    name = re.sub(r"\b(list|my|the)\b", "", (name or "to-do").lower()).strip() or "to-do"
    return {"todo": "to-do", "to do": "to-do", "tasks": "to-do", "grocery": "shopping", "groceries": "shopping"}.get(name, name)


def _show_list(name, items):
    publish({"type": "everyday_focus", "tab": "lists", "list": name})


def list_add(items: list, list_name: str = "to-do") -> str:
    name = _list_name(list_name)
    if isinstance(items, str):
        items = [items]
    with _lock:
        lists = _load(LISTS, {})
        cur = lists.setdefault(name, [])
        added = []
        for i in (i.strip() for i in items if i and i.strip()):
            if i.lower() not in {c.lower() for c in cur}:
                cur.append(i)
                added.append(i)
        _save(LISTS, lists)
    _show_list(name, cur)
    return f"OK: added {', '.join(added) or 'nothing new'} to the {name} list ({len(cur)} items)."


def list_remove(items: list, list_name: str = "to-do") -> str:
    name = _list_name(list_name)
    if isinstance(items, str):
        items = [items]
    with _lock:
        lists = _load(LISTS, {})
        cur = lists.get(name, [])
        low = [i.lower().strip() for i in items]
        keep = [c for c in cur if not any(x and (x in c.lower() or c.lower() in x) for x in low)]
        lists[name] = keep
        _save(LISTS, lists)
    _show_list(name, keep)
    return f"OK: removed {len(cur) - len(keep)} item(s) from the {name} list; {len(keep)} left."


def list_show(list_name: str = "") -> str:
    lists = _load(LISTS, {})
    if not list_name:
        if not lists:
            return "OK: there are no lists yet."
        return "OK: " + "; ".join(f"{n} list: {', '.join(v) or 'empty'}" for n, v in lists.items())
    name = _list_name(list_name)
    items = lists.get(name, [])
    _show_list(name, items)
    return f"OK: the {name} list has {len(items)} items: " + (", ".join(items) or "nothing") + "."


def list_clear(list_name: str = "to-do") -> str:
    name = _list_name(list_name)
    with _lock:
        lists = _load(LISTS, {})
        n = len(lists.pop(name, []))
        _save(LISTS, lists)
    return f"OK: cleared the {name} list ({n} items)."


# ------------------------------------------------------------------ routines
def save_routine(trigger: str, steps: list) -> str:
    trigger = " ".join((trigger or "").lower().split()).strip(" .!?")
    if isinstance(steps, str):
        steps = [s.strip() for s in re.split(r"[;\n]|, then | then ", steps) if s.strip()]
    if not trigger or not steps:
        return "FAILED: a routine needs a trigger phrase and at least one step."
    with _lock:
        routines = _load(ROUTINES, {})
        routines[trigger] = steps
        _save(ROUTINES, routines)
    return f"OK: routine '{trigger}' saved with {len(steps)} steps: " + "; ".join(steps) + "."


def run_routine(trigger: str) -> str:
    routines = _load(ROUTINES, {})
    key = " ".join((trigger or "").lower().split()).strip(" .!?")
    steps = routines.get(key) or next((v for k, v in routines.items() if k in key or key in k), None)
    if not steps:
        return "FAILED: no routine called that. Saved routines: " + (", ".join(routines) or "none") + "."
    return ("OK: now carry out these steps in order with your tools, without asking, then confirm in one short "
            "sentence: " + " | ".join(f"{i + 1}. {s}" for i, s in enumerate(steps)))


def delete_routine(trigger: str) -> str:
    with _lock:
        routines = _load(ROUTINES, {})
        key = " ".join((trigger or "").lower().split()).strip(" .!?")
        hit = next((k for k in routines if k == key or k in key or key in k), None)
        if not hit:
            return "FAILED: no routine called that."
        routines.pop(hit)
        _save(ROUTINES, routines)
    return f"OK: deleted the routine '{hit}'."


def prompt_context() -> str:
    """Extra system-prompt lines: saved routines and what's coming up."""
    out = []
    routines = _load(ROUTINES, {})
    if routines:
        out.append("Saved routines (when the user says one of these phrases, call run_routine with it): "
                   + "; ".join(f"'{k}' = {' → '.join(v)}" for k, v in routines.items()))
    items = sorted(_load(REMINDERS, []), key=lambda r: r["due"])[:6]
    if items:
        out.append("Upcoming reminders/alarms: " + "; ".join(
            f"{_say_time(datetime.datetime.fromisoformat(r['due']))}: {r['text']}" for r in items))
    lists = _load(LISTS, {})
    if lists:
        out.append("Lists: " + "; ".join(f"{n} ({len(v)} items)" for n, v in lists.items()))
    return "\n".join(out)
