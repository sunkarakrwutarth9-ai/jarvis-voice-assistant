"""Every chat is saved: each thing you say, every reply and every action, one file per day in chats/
(JSON lines, on this PC only, never committed). The command center's CHATS tab browses them, and after a
restart the recent conversation is shown again and Ultron remembers the last few exchanges.
"""

import datetime
import json
import threading
from pathlib import Path

DIR = Path(__file__).resolve().parent / "chats"
_lock = threading.Lock()
KEEP = ("user", "reply", "tool", "canvas")


def _path(d):
    return DIR / f"{d:%Y-%m-%d}.jsonl"


def record(event):
    kind = event.get("type")
    if kind not in KEEP:
        return
    if kind == "tool" and event.get("result") is None:
        return                                            # only finished actions
    row = {"t": event.get("ts"), "type": kind}
    if kind == "user":
        row["text"] = event.get("text", "")
    elif kind == "reply":
        if not event.get("text"):
            return
        row.update(text=event["text"], model=event.get("model", ""))
    elif kind == "tool":
        row.update(text=event.get("label") or event.get("name"), result=str(event.get("result", ""))[:300])
    elif kind == "canvas":
        row.update(text=f"Created: {event.get('title', '')}", kind=event.get("kind", ""))
    DIR.mkdir(exist_ok=True)
    line = json.dumps(row, ensure_ascii=False)
    with _lock:
        with _path(datetime.date.today()).open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def flush():
    """Writes are immediate (append per line); kept for callers that want to be sure before shutdown."""
    return True


def _read(path):
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def days():
    if not DIR.exists():
        return []
    res = []
    for p in sorted(DIR.glob("*.jsonl"), reverse=True)[:90]:
        rows = _read(p)
        first = next((r["text"] for r in rows if r.get("type") == "user" and r.get("text")), "")
        res.append({"day": p.stem, "messages": sum(r.get("type") in ("user", "reply") for r in rows), "first": first[:80]})
    return res


def day(d):
    return _read(DIR / f"{d}.jsonl")


def recent(n=40):
    """Recent events (newest days first) shaped like live dashboard events, for replay after a restart."""
    rows = []
    for p in sorted(DIR.glob("*.jsonl"), reverse=True)[:3] if DIR.exists() else []:
        rows = _read(p) + rows
        if len(rows) >= n:
            break
    out = []
    for r in rows[-n:]:
        if r.get("type") == "user":
            out.append({"type": "user", "text": r.get("text", ""), "ts": r.get("t")})
        elif r.get("type") == "reply":
            out.append({"type": "reply", "text": r.get("text", ""), "ts": r.get("t"), "model": r.get("model", "")})
    return out


def brain_turns(n=8):
    """The last few user/assistant exchanges as chat messages, so Ultron remembers them after a restart."""
    msgs = [{"role": "user" if e["type"] == "user" else "assistant", "content": e["text"]}
            for e in recent(n * 2) if e.get("text")]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs[-n * 2:]
