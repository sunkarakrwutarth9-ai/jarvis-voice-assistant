"""Memory vault: Atomo's long-term memory as plain Markdown notes - an Obsidian-compatible vault on this PC.

    vault/
      Me.md                  who the user is, preferences, routines
      People/<name>.md       people in the user's life
      Projects/<name>.md     ongoing work, goals, plans
      Facts.md               anything else worth remembering
      Journal/YYYY-MM-DD.md  automatic daily journal (what was asked / done)
      Agents/<name>.md       each Stark-team agent's own memory
      Reports/YYYY-MM-DD.md  overnight-shift morning reports

How it learns: every exchange is journaled; every few minutes the new journal lines are distilled by the
AI into lasting facts and filed into the right note. How it recalls: before each answer the notes most
relevant to what the user just said are added to Atomo's context. Never uploaded, never committed.
"""

import datetime
import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.vault")
ROOT = Path(__file__).resolve().parent / "vault"
_lock = threading.Lock()
_pending = []                    # journal lines not yet distilled
STOP = set("the a an and or but to of in on at for with is are was were be been it this that i me my you your "
           "what who how when where why do does did can could should would will please atomo jarvis sir".split())


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', " ", name).strip()[:60] or "Note"


def init():
    for d in ("People", "Projects", "Journal", "Agents", "Reports"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    me = ROOT / "Me.md"
    if not me.exists():
        me.write_text("# Me\n\nWhat Atomo knows about you. Edit freely - Atomo reads this before answering.\n\n"
                      "## Preferences\n\n## Routines\n\n## Goals\n", encoding="utf-8")
    facts = ROOT / "Facts.md"
    if not facts.exists():
        facts.write_text("# Facts\n\n", encoding="utf-8")
        old = ROOT.parent / "memory.json"                      # bring over the old memory list once
        try:
            for m in json.loads(old.read_text(encoding="utf-8")):
                _append(facts, f"- {m['fact']} _(saved {m.get('date', '')})_")
        except (OSError, ValueError, KeyError, TypeError):
            pass
    obs = ROOT / ".obsidian"
    obs.mkdir(exist_ok=True)


def _append(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        text = path.read_text(encoding="utf-8") if path.exists() else f"# {path.stem}\n\n"
        if line.strip() and line.strip() not in text:
            path.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")


# ------------------------------------------------------------------ journal (every exchange)
def journal(user_text, reply, tools_used=()):
    if not user_text:
        return
    now = datetime.datetime.now()
    day = ROOT / "Journal" / f"{now:%Y-%m-%d}.md"
    if not day.exists():
        day.parent.mkdir(parents=True, exist_ok=True)
        day.write_text(f"# {now:%A %d %B %Y}\n\n", encoding="utf-8")
    t = " ".join(str(user_text).split())[:300]
    r = " ".join(str(reply or "").split())[:300]
    line = f"- **{now:%H:%M}** You: {t}" + (f" → Atomo: {r}" if r else "") + (f" _[{', '.join(tools_used)}]_" if tools_used else "")
    with _lock:
        with day.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        _pending.append(line)


# ------------------------------------------------------------------ learning (distil journal -> notes)
def _learn_loop():
    import tools
    while True:
        time.sleep(240)
        with _lock:
            batch = _pending[:]
            _pending.clear()
        if len(batch) < 1 or tools.generate_text is None:
            continue
        try:
            raw = tools.generate_text(
                "You maintain a personal knowledge vault for the user of a voice assistant. From these new "
                "conversation lines, extract ONLY durable, useful facts about the user's life: people (name + "
                "relation/details), projects/goals/plans (with dates), preferences, routines, important facts "
                "(exams, birthdays, addresses of places they mention, favourite things). Ignore commands like "
                "'open YouTube', small talk, and anything already obvious. Never store passwords, OTPs, card or "
                "ID numbers. Reply with ONLY a JSON array, possibly empty: "
                '[{"note": "Me" | "Facts" | "People/<Name>" | "Projects/<Name>", "fact": "short sentence"}]\n\n'
                + "\n".join(batch), 1500) or "[]"
            items = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
        except Exception as e:
            log.info("vault learning skipped: %s", str(e)[:120])
            continue
        today = f"{datetime.date.today():%d %b %Y}"
        for it in items[:15]:
            note, fact = str(it.get("note", "Facts")), " ".join(str(it.get("fact", "")).split())[:300]
            if not fact or re.search(r"password|otp|\b\d{12,16}\b|cvv", fact, re.I):
                continue
            parts = [_safe(p) for p in note.split("/") if p.strip()][:2]
            if parts and parts[0] in ("People", "Projects") and len(parts) == 2:
                path = ROOT / parts[0] / f"{parts[1]}.md"
            elif parts and parts[0] == "Me":
                path = ROOT / "Me.md"
            else:
                path = ROOT / "Facts.md"
            _append(path, f"- {fact} _({today})_")
            log.info("vault learned -> %s: %s", path.relative_to(ROOT), fact)


def start():
    init()
    threading.Thread(target=_learn_loop, name="vault", daemon=True).start()


# ------------------------------------------------------------------ recall (context for every answer)
def _words(text):
    return {w for w in re.findall(r"[\w']{3,}", (text or "").lower()) if w not in STOP}


def _notes():
    if not ROOT.exists():
        return []
    return [p for p in ROOT.rglob("*.md") if "Journal" not in p.parts and ".obsidian" not in p.parts]


def context_for(user_text, budget=2200):
    """The vault notes most relevant to what the user just said (plus Me.md), for the system prompt."""
    try:
        me = (ROOT / "Me.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    words = _words(user_text)
    scored = []
    for p in _notes():
        if p.name == "Me.md":
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        name_hit = 3 * len(words & _words(p.stem))
        score = name_hit + len(words & _words(body))
        if score:
            scored.append((score, p, body))
    scored.sort(key=lambda x: -x[0])
    out = [me.strip()[:900]] if len(me.strip()) > 80 else []
    used = sum(map(len, out))
    for _, p, body in scored[:4]:
        chunk = f"### {p.relative_to(ROOT).with_suffix('').as_posix()}\n{body.strip()[:700]}"
        if used + len(chunk) > budget:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n\n".join(out)


# ------------------------------------------------------------------ tools
def recall(query: str) -> str:
    """Search the whole vault, journal included."""
    words = _words(query)
    if not words:
        return "FAILED: what should I look for?"
    hits = []
    for p in ROOT.rglob("*.md"):
        if ".obsidian" in p.parts:
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                s = len(words & _words(line))
                if s:
                    hits.append((s, p.relative_to(ROOT).with_suffix("").as_posix(), line.strip()))
        except OSError:
            pass
    hits.sort(key=lambda h: -h[0])
    if not hits:
        return f"OK: nothing in the memory vault about '{query}'."
    return "OK: from the memory vault: " + " | ".join(f"[{n}] {l[:200]}" for _, n, l in hits[:10])


def remember_note(fact: str, note: str = "Facts") -> str:
    parts = [_safe(p) for p in (note or "Facts").split("/") if p.strip()][:2]
    if parts and parts[0] in ("People", "Projects") and len(parts) == 2:
        path = ROOT / parts[0] / f"{parts[1]}.md"
    elif parts and parts[0] == "Me":
        path = ROOT / "Me.md"
    else:
        path = ROOT / "Facts.md"
    _append(path, f"- {' '.join(fact.split())[:400]} _({datetime.date.today():%d %b %Y})_")
    return f"OK: saved to the memory vault ({path.relative_to(ROOT).with_suffix('').as_posix()})."


def open_vault() -> str:
    import os
    import shutil
    init()
    obsidian = shutil.which("obsidian") or next((str(p) for p in [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Obsidian" / "Obsidian.exe"] if p.exists()), None)
    if obsidian:
        os.startfile(f"obsidian://open?path={ROOT}")
        return "OK: opened the memory vault in Obsidian."
    os.startfile(str(ROOT))
    return ("OK: opened the memory vault folder (plain Markdown notes). Tip: install Obsidian and open this folder as "
            "a vault for linked notes and a graph view.")


def journal_for(day: str = "today") -> str:
    d = datetime.date.today() - datetime.timedelta(days=1 if day == "yesterday" else 0)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        d = datetime.date.fromisoformat(day)
    p = ROOT / "Journal" / f"{d:%Y-%m-%d}.md"
    if not p.exists():
        return f"OK: no journal for {d:%A %d %B}."
    return f"OK: journal for {d:%A %d %B}:\n" + p.read_text(encoding="utf-8")[-5000:]


# ------------------------------------------------------------------ the memory graph (command center view)
def graph(max_facts=12):
    """Nodes = notes + their facts; links = note->fact and note<->note when one mentions the other."""
    init()
    nodes, links, notes = [], [], []
    for p in sorted(_notes()):
        rel = p.relative_to(ROOT).with_suffix("").as_posix()
        if rel.startswith("Reports/"):
            continue
        group = rel.split("/")[0] if "/" in rel else rel
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        facts = [re.sub(r"\s*_\(.*?\)_\s*$", "", l[2:]).strip() for l in body.splitlines() if l.startswith("- ")]
        notes.append((rel, p.stem, body))
        nodes.append({"id": rel, "label": p.stem, "group": group, "kind": "note", "size": 6 + min(len(facts), 12)})
        for i, f in enumerate(facts[-max_facts:]):
            fid = f"{rel}#{i}"
            nodes.append({"id": fid, "label": f[:140], "group": group, "kind": "fact", "size": 2.5})
            links.append({"s": rel, "t": fid})
    for rel, stem, _ in notes:
        for rel2, _, body2 in notes:
            if rel != rel2 and len(stem) > 2 and re.search(r"\b" + re.escape(stem) + r"\b", body2, re.I):
                links.append({"s": rel2, "t": rel, "x": 1})
    return {"nodes": nodes, "links": links}
