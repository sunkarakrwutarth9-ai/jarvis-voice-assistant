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


def _slide_html(lesson, i):
    import html as H
    s = lesson["slides"][i]
    img = s.get("_img") or {}
    n = len(lesson["slides"])
    bullets = "".join(f"<li>{H.escape(b)}</li>" for b in s.get("bullets", []))
    board = s.get("board", "").strip()
    q = (s.get("question") or {}).get("q", "")
    bg = (f"background:linear-gradient(90deg,rgba(4,6,14,.94) 0%,rgba(4,6,14,.82) 42%,rgba(4,6,14,.25) 75%,rgba(4,6,14,.05) 100%),"
          f"url('{img['url']}') center/cover no-repeat;") if img.get("url") else \
        "background:radial-gradient(ellipse at 70% 40%,#1a2a4a,#04060e 70%);"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0}}html,body{{height:100%;background:#04060e;font-family:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;overflow:hidden}}
.s{{position:absolute;inset:0;{bg}color:#fff;padding:5.5vh 6vw;display:flex;flex-direction:column;animation:in .9s ease}}
@keyframes in{{from{{opacity:0;transform:scale(1.03)}}to{{opacity:1;transform:none}}}}
.top{{display:flex;justify-content:space-between;font:600 1.6vh Bahnschrift,sans-serif;letter-spacing:.5em;color:#7fe6ff;text-transform:uppercase}}
h1{{font-size:5.4vh;line-height:1.12;margin:5vh 0 3vh;max-width:52vw;font-weight:700;text-shadow:0 2px 24px rgba(0,0,0,.6)}}
ul{{max-width:46vw;padding-left:1.2em}}li{{font-size:2.6vh;line-height:1.5;margin:1.1vh 0;color:#e8eefc}}
li::marker{{color:#f4ba42}}
.board{{margin-top:3vh;max-width:44vw;background:rgba(255,255,255,.08);border:1px solid rgba(244,186,66,.45);border-radius:1.4vh;
  padding:1.6vh 2vh;font:2.2vh/1.45 "Cascadia Code",Consolas,monospace;color:#ffe2a8;white-space:pre-wrap;backdrop-filter:blur(6px)}}
.board b{{display:block;font:600 1.4vh Bahnschrift,sans-serif;letter-spacing:.3em;color:#f4ba42;margin-bottom:.8vh}}
.q{{margin-top:auto;max-width:50vw;font-size:2.2vh;color:#cfe9ff;border-left:.4vh solid #7fe6ff;padding:.6vh 1.4vh}}
.bar{{position:absolute;left:0;bottom:0;height:.6vh;background:linear-gradient(90deg,#e0262f,#f4ba42);width:{(i + 1) / n * 100:.1f}%;transition:width 1s}}
.credit{{position:absolute;right:2vw;bottom:1.6vh;font-size:1.25vh;color:rgba(255,255,255,.55)}}
</style></head><body><div class="s"><div class="top"><span>🎓 {H.escape(lesson['title'])}</span><span>{i + 1} / {n}</span></div>
<h1>{H.escape(s.get('title', ''))}</h1><ul>{bullets}</ul>
{f'<div class="board"><b>WHITEBOARD</b>{H.escape(board)}</div>' if board else ''}
{f'<div class="q">🤔 {H.escape(q)}</div>' if q else ''}</div><div class="bar"></div>
{f'<div class="credit">Photo: {H.escape(img.get("credit", ""))}</div>' if img.get("url") else ''}</body></html>"""


def _fetch_images(lesson):
    from concurrent.futures import ThreadPoolExecutor
    import images

    def one(sl):
        q = sl.get("image") or f"{lesson['title']} {sl.get('title', '')}"
        found = images.find(q, 1) or images.find(sl.get("title", ""), 1)
        sl["_img"] = found[0] if found else {}

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, lesson["slides"]))


def _run(lesson, cid):
    import tools
    import tutor
    say, busy = hooks["say"], hooks["busy"]
    for i, s in enumerate(lesson["slides"]):
        if not _class["run"]:
            break
        cid = tools.show_content("presentation", f"Class: {lesson['title']}"[:60], "html", _slide_html(lesson, i),
                                 cid=cid, final=True)
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
          "no markdown, explaining - not reading - the bullets), a check 'question' with a short answer, and 'image' (2-5 English words to search a real photo for this slide, e.g. 'cumulus clouds sky'). Use the "
          "language the user asked in. Reply with ONLY JSON: {\"title\": \"...\", \"slides\": [{\"title\": \"\", "
          "\"bullets\": [], \"board\": \"\", \"say\": \"\", \"question\": {\"q\": \"\", \"a\": \"\"}, \"image\": \"\"}]}", 9000) or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        lesson = json.loads(m.group(0))
        assert lesson.get("slides")
    except Exception:
        return "FAILED: couldn't prepare the lesson this time - try again."
    lesson["title"] = lesson.get("title") or title
    tools.progress("Finding real photos for the slides")
    _fetch_images(lesson)
    _class["run"] = True
    cid = tools.show_content("presentation", f"Class: {lesson['title']}"[:60], "html", _slide_html(lesson, 0), final=True)
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
