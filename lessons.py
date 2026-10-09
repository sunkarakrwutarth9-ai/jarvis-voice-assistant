"""Learning from mistakes: when the user corrects Ultron, the lesson is written down and used from then on.

    vault/Lessons.md     one line per lesson, e.g.
                         - When the user says "charge beauty" or "chat GPT", open chatgpt.com. _(09 Oct 2026)_

Where lessons come from:
  * corrections - "no, I meant...", "that's wrong", "not that", "అది కాదు", "नहीं, मेरा मतलब..." right after a reply
  * failures followed by a retry - a tool FAILED and the user immediately asked again in other words
  * "remember this lesson: ..." / the lessons tool
  * a nightly review of the day's journal (mistakes the user had to repeat or fix)
Before every answer the lessons that match the request are added to Ultron's instructions.
"""
import datetime
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger("jarvis.lessons")
FILE = Path(__file__).resolve().parent / "vault" / "Lessons.md"
STATE = Path(__file__).resolve().parent / "vault" / ".lessons_review"
_lock = threading.Lock()
_last = {"user": "", "reply": "", "tools": (), "failed": ()}

CORRECTION = re.compile(
    r"^(no+|nope|wrong|not that|that'?s (not|wrong)|you (got it|are) wrong|i (said|meant|asked)|i didn'?t (say|ask|mean))\b"
    r"|\b(no,? i (meant|said|asked)|not this one|that'?s not what i|wrong (one|app|song|video|answer)|i told you|"
    r"how many times|again wrong|you misheard|listen properly)\b"
    r"|అది కాదు|కాదు,|తప్పు|నేను చెప్పింది|नहीं,? मेरा मतलब|गलत|मैंने कहा था|ये नहीं", re.I)
STOP = set("the a an and or but to of in on at for with is are was were be it this that i me my you your what "
           "please sir ultron jarvis can could open play".split())


def _words(t):
    return {w for w in re.findall(r"[\w']{3,}", (t or "").lower()) if w not in STOP}


def _all():
    try:
        return [l[2:].strip() for l in FILE.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    except OSError:
        return []


def add(lesson: str, source="") -> str:
    lesson = " ".join(str(lesson).split())[:300].rstrip(".")
    if len(lesson) < 8 or len(lesson.split()) < 5:
        return "FAILED: lesson too short."
    if re.search(r"password|otp|cvv|\b\d{12,16}\b", lesson, re.I):
        return "FAILED: I don't store secrets."
    with _lock:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        text = FILE.read_text(encoding="utf-8") if FILE.exists() else (
            "# Lessons\n\nMistakes Ultron made and what to do instead. Edit or delete lines freely.\n\n")
        key = lesson.lower()[:60]
        if any(key in l.lower() for l in text.splitlines()):
            return "OK: I already know that lesson."
        FILE.write_text(text.rstrip("\n") + f"\n- {lesson}. _({datetime.date.today():%d %b %Y}{', ' + source if source else ''})_\n",
                        encoding="utf-8")
    log.info("lesson learned (%s): %s", source or "manual", lesson)
    return f"OK: lesson saved - {lesson}."


def for_prompt(user_text, limit=8):
    """Lessons that fit this request (word overlap), newest first, plus the newest general ones."""
    all_ = _all()
    if not all_:
        return ""
    w = _words(user_text)
    scored = sorted(((len(w & _words(l)), i, l) for i, l in enumerate(all_)), reverse=True)
    picked = [l for s, _i, l in scored if s > 0][:limit]
    for l in reversed(all_[-3:]):
        if l not in picked and len(picked) < limit:
            picked.append(l)
    return "\n".join(f"- {l}" for l in picked)


def _distil(prev_user, prev_reply, prev_failed, correction):
    import tools
    if tools.generate_text is None:
        return
    raw = tools.generate_text(
        "A voice assistant (Ultron) made a mistake and the user corrected it. Write ONE short, general, reusable "
        "rule Ultron should follow next time (start with 'When' or 'If' or 'Always'/'Never'), in English, max 30 "
        "words, quoting the misheard words - e.g. 'When the user says \"charge beauty\" they mean ChatGPT - open "
        "chatgpt.com.' A correction or rephrase right after a reply means Ultron misunderstood (often the speech "
        "was misheard) - that IS a mistake. Reply NONE only if the second message is about something unrelated.\n\n"
        f"User asked: {prev_user}\nUltron replied: {prev_reply}\n"
        + (f"Failed actions: {'; '.join(prev_failed)}\n" if prev_failed else "")
        + f"User then said: {correction}", 1000) or ""
    raw = raw.strip().strip('"')
    log.info("correction noticed -> %s", raw[:160] or "(no lesson)")
    if raw and "NONE" not in raw.upper()[:8]:
        add(raw.splitlines()[0], "correction")


def observe(user_text, reply, tools_used=(), failed=()):
    """Called after every exchange."""
    prev = dict(_last)
    _last.update(user=user_text or "", reply=reply or "", tools=tuple(tools_used), failed=tuple(failed))
    if not prev["user"] or not user_text:
        return
    corrected = bool(CORRECTION.search(user_text.strip()))
    retried = bool(prev["failed"]) and len(_words(user_text) & _words(prev["user"])) >= 1
    if corrected or retried:
        threading.Thread(target=_safe_distil, args=(prev["user"], prev["reply"], prev["failed"], user_text),
                         daemon=True, name="lesson").start()


def _safe_distil(*a):
    try:
        _distil(*a)
    except Exception as e:
        log.info("lesson skipped: %s", str(e)[:120])


def nightly_review():
    """Once a day: read yesterday's journal and file lessons from mistakes the user had to fix."""
    import tools
    day = datetime.date.today() - datetime.timedelta(days=1)
    try:
        if STATE.read_text(encoding="utf-8").strip() == str(day):
            return
    except OSError:
        pass
    journal = FILE.parent / "Journal" / f"{day:%Y-%m-%d}.md"
    if not journal.exists() or tools.generate_text is None:
        if STATE.parent.exists():
            STATE.write_text(str(day), encoding="utf-8")
        return
    lines = journal.read_text(encoding="utf-8").splitlines()[-250:]
    raw = tools.generate_text(
        "Here is a day of conversation between the user and their voice assistant Ultron. Find the MISTAKES Ultron "
        "made (misheard names, wrong app/song/site opened, the user had to repeat or correct, failed actions, "
        "annoying behaviour like talking when not asked). For each, write one short reusable rule for next time "
        "(start with When/If/Always/Never, max 30 words). Reply with ONLY the rules, one per line, max 6. If there "
        "were no real mistakes reply NONE.\n\n" + "\n".join(lines), 2000) or ""
    n = 0
    for line in raw.splitlines():
        line = line.strip(" -*•\t").strip()
        if line and "NONE" not in line.upper()[:6] and len(line) > 15:
            if add(line, "nightly review").startswith("OK: lesson saved"):
                n += 1
    STATE.write_text(str(day), encoding="utf-8")
    log.info("nightly review of %s: %d new lesson(s)", day, n)


def lessons(action: str = "list", lesson: str = "") -> str:
    """Tool: list / add / forget lessons."""
    if action == "add":
        return add(lesson, "told")
    if action == "forget":
        q = (lesson or "").lower()
        with _lock:
            lines = FILE.read_text(encoding="utf-8").splitlines() if FILE.exists() else []
            keep = [l for l in lines if not (l.startswith("- ") and q and q in l.lower())]
            if len(keep) == len(lines):
                return "FAILED: no lesson matches that."
            FILE.write_text("\n".join(keep) + "\n", encoding="utf-8")
        return f"OK: forgot {len(lines) - len(keep)} lesson(s)."
    all_ = _all()
    if not all_:
        return "OK: no lessons yet - I learn one every time you correct me."
    return f"OK: {len(all_)} lessons learned. Latest: " + " | ".join(all_[-8:])
