"""AI classroom: Ultron teaches a topic or a document as a live class - slides on the Ultron Screen, a voice
teacher explaining each one, whiteboard notes, a check question per slide, and a flashcard deck at the
end so "quiz me" continues the lesson with spaced repetition.
"""

import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.classroom")
hooks = {"say": None, "busy": None, "stop": None}
_class = {"run": False}


def _slide_md(lesson, i):
    s = lesson["slides"][i]
    bar = "▰" * (i + 1) + "▱" * (len(lesson["slides"]) - i - 1)
    bullets = "\n".join(f"- {b}" for b in s.get("bullets", []))
    board = s.get("board", "").strip()
    board_md = ("\n\n> 🧑‍🏫 **Whiteboard**\n>\n" + "\n".join("> " + l for l in board.splitlines())) if board else ""
    q = s.get("question", {})
    question = f"\n\n---\n**🤔 Think:** {q.get('q')}" if q.get("q") else ""
    return (f"# 🎓 {lesson['title']}\n\n`{bar}` · slide {i + 1} of {len(lesson['slides'])}\n\n"
            f"## {s.get('title', '')}\n\n{bullets}{board_md}{question}\n")


def _run(lesson, cid):
    import tools
    import tutor
    say, busy = hooks["say"], hooks["busy"]
    for i, s in enumerate(lesson["slides"]):
        if not _class["run"]:
            break
        cid = tools.show_content("document", f"Class: {lesson['title']}"[:60], "md", _slide_md(lesson, i), cid=cid,
                                 final=(i == len(lesson["slides"]) - 1))
        tools.publish({"type": "progress", "text": f"🎓 Slide {i + 1}/{len(lesson['slides'])}: {s.get('title', '')}"})
        say(s.get("say") or s.get("title", ""))
        time.sleep(1.0)
        while _class["run"] and busy():
            time.sleep(0.3)
        time.sleep(1.2)                                   # a breath between slides
    if _class["run"]:
        cards = [{"q": s["question"]["q"], "a": s["question"]["a"]} for s in lesson["slides"]
                 if s.get("question", {}).get("q") and s["question"].get("a")]
        if cards:
            slug = tutor._slug(lesson["title"])
            tutor._save(slug, {"title": lesson["title"], "created": time.time(),
                               "cards": [dict(c, box=1, due=0) for c in cards]})
            tutor._cur.update(deck=slug, card=None, right=0, asked=0)
        say("That's the end of the class. Say quiz me, and I'll test you on what we covered.")
    _class["run"] = False


def teach(topic: str = "", source: str = "", slides: int = 8, level: str = "") -> str:
    import tools
    slides = max(4, min(int(slides or 8), 14))
    text, title = "", topic
    if source:
        p = Path(source.strip('"'))
        if p.exists():
            text, title = tools._read_file(p, 45000), topic or p.stem
        else:
            return f"FAILED: couldn't find '{source}'."
    if not (topic or text):
        return "FAILED: what should I teach?"
    if _class["run"]:
        return "OK: a class is already running - say 'stop class' first."
    tools.progress(f"Preparing the class: {title[:40]}")
    raw = tools.generate_text(
        f"You are a brilliant, friendly teacher. Prepare a {slides}-slide lesson"
        f"{' for a ' + level + ' learner' if level else ''} on: {title}.\n"
        + (f"Base it ONLY on this material:\n{text[:40000]}\n" if text else "")
        + "Build from basics to deeper ideas with a real-life example; the last slide is a recap. For each slide give: "
          "a short title, 3-4 crisp bullets, a 'board' (a key formula, definition, tiny diagram in text, or worked "
          "example - may be empty), 'say' (what the teacher says aloud: 3-5 natural spoken sentences, conversational, "
          "no markdown, explaining - not reading - the bullets), and a check 'question' with a short answer. Use the "
          "language the user asked in. Reply with ONLY JSON: {\"title\": \"...\", \"slides\": [{\"title\": \"\", "
          "\"bullets\": [], \"board\": \"\", \"say\": \"\", \"question\": {\"q\": \"\", \"a\": \"\"}}]}", 9000) or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        lesson = json.loads(m.group(0))
        assert lesson.get("slides")
    except Exception:
        return "FAILED: couldn't prepare the lesson this time - try again."
    lesson["title"] = lesson.get("title") or title
    _class["run"] = True
    cid = tools.show_content("document", f"Class: {lesson['title']}"[:60], "md", _slide_md(lesson, 0), final=False)
    threading.Thread(target=_run, args=(lesson, cid), name="classroom", daemon=True).start()
    return (f"OK: the class '{lesson['title']}' ({len(lesson['slides'])} slides) is starting on the Ultron Screen and I'm "
            f"teaching it aloud now. Reply with ONE short line only (e.g. 'Class is starting, Sir.') - do not "
            f"explain the topic yourself.")


def stop_class() -> str:
    if not _class["run"]:
        return "OK: no class is running."
    _class["run"] = False
    if hooks["stop"]:
        hooks["stop"]()
    return "OK: class stopped. The slides stay on the Ultron Screen."
