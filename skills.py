"""Skills: reusable know-how that teaches Ultron to do a task YOUR way - "how I write Instagram captions",
"how to reply to customers", "my weekly review". Each skill is a Markdown note in vault/Skills/; when a
request matches a skill, its instructions are given to Ultron before it answers, so the task is done
the same high-quality way every time. Teach new ones by voice ("learn this as a skill: ...").
"""

import datetime
import logging
import re
from pathlib import Path

log = logging.getLogger("jarvis.skills")

STARTERS = {
    "Instagram reel script": ("reel, reels, instagram script, video script, hook, content idea",
        "1. Hook in the first 1.5 seconds: a bold claim, a POV, or a surprising result (max 10 words, on-screen text).\n"
        "2. Show, don't tell: list the 4-6 shots in order with what's on screen.\n"
        "3. Voice-over lines short and punchy; one idea per line.\n"
        "4. End with a clear call to action: 'Comment <WORD> and I'll send it to you'.\n"
        "5. Give a caption (2-3 lines) and 8-10 hashtags mixing big and niche tags.\n"
        "6. Offer an English version and a Telugu/Hindi version of the hook."),
    "Email reply": ("reply, email, mail, respond to, draft",
        "1. Match the sender's language and formality.\n2. First line answers their main question directly.\n"
        "3. Keep it under 120 words unless asked.\n4. End with a clear next step or question.\n"
        "5. Never promise money, dates or commitments the user didn't state - leave [brackets] for them to fill."),
    "Study plan": ("study plan, exam, revise, preparation, syllabus, timetable",
        "1. Ask (or use memory for) the exam date and subjects.\n2. Split the days into learn / practise / revise blocks, "
        "hardest topics at the user's sharpest time of day (check memory).\n3. Add spaced revision (1, 3, 7 days later).\n"
        "4. Put the plan on the Ultron Screen as a table, and offer to schedule daily reminders with schedule_task."),
    "Daily planning": ("plan my day, plan today, schedule my day, to do today, plan my evening",
        "1. Check reminders, lists, calendar (if connected) and memory goals.\n2. Pick the top 3 must-dos.\n"
        "3. Time-block them around the user's routine from memory; add breaks.\n"
        "4. Offer to set reminders for each block."),
}


def _dir():
    import vault
    vault.init()
    d = vault.ROOT / "Skills"
    d.mkdir(exist_ok=True)
    if not any(d.glob("*.md")):
        for name, (when, steps) in STARTERS.items():
            _write(d, name, when, steps)
    return d


def _write(d, name, when, steps):
    safe = re.sub(r"[^\w -]+", "", name).strip()[:50] or "Skill"
    path = d / f"{safe}.md"
    path.write_text(f"# Skill: {name}\n\n**Use when:** {when}\n\n## How to do it\n{steps.strip()}\n\n"
                    f"_Saved {datetime.date.today():%d %b %Y}_\n", encoding="utf-8")
    return path


def _all():
    out = []
    for p in sorted(_dir().glob("*.md")):
        text = p.read_text(encoding="utf-8")
        m = re.search(r"\*\*Use when:\*\*\s*(.+)", text)
        out.append((p.stem, (m.group(1) if m else p.stem).lower(), text))
    return out


def for_request(user_text, limit=2):
    """The skills that match what the user just asked (for the system prompt)."""
    t = (user_text or "").lower()
    if not t:
        return ""
    hits = []
    for name, when, text in _all():
        triggers = [w.strip() for w in re.split(r"[,;]", when) if w.strip()]
        score = sum(2 for w in triggers if w and w in t) + (3 if name.lower() in t else 0)
        if score:
            hits.append((score, text))
    hits.sort(key=lambda x: -x[0])
    return "\n\n".join(h[1][:1500] for h in hits[:limit])


def save_skill(name: str, instructions: str, when: str = "") -> str:
    if not (name and instructions):
        return "FAILED: a skill needs a name and the instructions."
    p = _write(_dir(), name, when or name.lower(), instructions)
    return f"OK: learned the skill '{name}' (saved in the memory vault, Skills/{p.name}). I'll use it whenever it fits."


def list_skills() -> str:
    s = _all()
    return "OK: my skills: " + "; ".join(f"{n} (when: {w[:60]})" for n, w, _ in s) if s else "OK: no skills yet."


def delete_skill(name: str) -> str:
    for p in _dir().glob("*.md"):
        if name.lower() in p.stem.lower():
            p.unlink()
            return f"OK: forgot the skill '{p.stem}'."
    return "FAILED: no skill with that name."
