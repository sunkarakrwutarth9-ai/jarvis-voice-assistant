"""Study tutor: turn a PDF / document / topic into flashcards, then quiz the user by voice with spaced
repetition (Leitner boxes) until they know it.

Decks live in study/<name>.json on this PC.
"""

import html
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("jarvis.tutor")
DIR = Path(__file__).resolve().parent / "study"
INTERVALS = {1: 0, 2: 6 * 3600, 3: 24 * 3600, 4: 3 * 86400, 5: 7 * 86400, 6: 21 * 86400}
_cur = {"deck": None, "card": None, "right": 0, "asked": 0}


def _slug(t):
    return re.sub(r"[^\w-]+", "_", t.strip().lower())[:40].strip("_") or "deck"


def _load(name):
    return json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))


def _save(name, deck):
    DIR.mkdir(exist_ok=True)
    (DIR / f"{name}.json").write_text(json.dumps(deck, indent=1, ensure_ascii=False), encoding="utf-8")


def _decks():
    return sorted((p.stem for p in DIR.glob("*.json")), key=lambda n: -(DIR / f"{n}.json").stat().st_mtime) if DIR.exists() else []


def _cards_page(title, cards):
    items = "".join(f'<div class="c" onclick="this.classList.toggle(\'f\')"><div class="q">{html.escape(c["q"])}</div>'
                    f'<div class="a">{html.escape(c["a"])}</div><span>{i + 1}</span></div>' for i, c in enumerate(cards))
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>
body{{margin:0;background:#05070f;color:#eef4ff;font:15px "Segoe UI",system-ui,sans-serif;padding:26px}}
h1{{font:600 24px Bahnschrift,"Segoe UI";letter-spacing:3px;color:#7fe6ff;margin:0 0 4px}} p{{color:#8b97b5;margin:0 0 20px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}}
.c{{position:relative;min-height:150px;border-radius:18px;padding:22px 20px;cursor:pointer;background:linear-gradient(160deg,#101830,#0a0f1e);
  border:1px solid #22305a;box-shadow:0 10px 30px rgba(0,0,0,.4);transition:transform .35s,border-color .3s}}
.c:hover{{transform:translateY(-3px);border-color:#7fe6ff}} .c span{{position:absolute;right:14px;top:10px;color:#3a4a70;font:600 12px Bahnschrift}}
.q{{font-weight:600;font-size:16px;line-height:1.4}} .a{{display:none;color:#f4ba42;line-height:1.45}}
.c.f{{border-color:#f4ba42;background:linear-gradient(160deg,#1d1608,#0f0c05)}} .c.f .q{{display:none}} .c.f .a{{display:block}}
</style></head><body><h1>📚 {html.escape(title)}</h1><p>{len(cards)} flashcards · click a card to flip it · say “quiz me” to practise by voice</p>
<div class="g">{items}</div></body></html>"""


def study(source: str = "", topic: str = "", count: int = 15) -> str:
    """Make a flashcard deck from a file (path or name) or a topic."""
    import tools
    count = max(5, min(int(count or 15), 40))
    text, name = "", topic or Path(source).stem if source else topic
    if source:
        p = Path(source.strip('"'))
        if not p.exists():
            found = tools.find_files(source) if hasattr(tools, "find_files") else ""
            m = re.search(r"([A-Za-z]:\\[^\n;|]+\.(pdf|docx|txt|md))", found or "", re.I)
            p = Path(m.group(1)) if m else p
        if p.exists():
            text = tools._read_file(p, 50000)
            name = p.stem
        else:
            return f"FAILED: couldn't find the file '{source}'."
    if not (text or topic):
        return "FAILED: give a topic or a document to study."
    tools.progress(f"Making flashcards: {name[:30]}")
    src = f"MATERIAL:\n{text[:45000]}" if text else f"TOPIC: {topic}"
    raw = tools.generate_text(
        f"Create {count} high-quality study flashcards for a student from the material below. Cover the most important "
        f"facts, definitions, causes/effects, formulas and examples; one idea per card; answers short (max 2 sentences). "
        f"Use the material's language. Reply with ONLY a JSON array: [{{\"q\": \"question\", \"a\": \"answer\"}}, ...]\n\n{src}", 6000)
    m = re.search(r"\[.*\]", raw or "", re.S)
    try:
        cards = [c for c in json.loads(m.group(0)) if c.get("q") and c.get("a")]
    except Exception:
        return "FAILED: the flashcards could not be generated; try again."
    slug = _slug(name)
    deck = {"title": name, "created": time.time(), "cards": [{"q": c["q"], "a": c["a"], "box": 1, "due": 0} for c in cards]}
    _save(slug, deck)
    _cur.update(deck=slug, card=None, right=0, asked=0)
    tools.show_content("webpage", f"Flashcards: {name}"[:60], "html", _cards_page(name, deck["cards"]))
    return (f"OK: made {len(cards)} flashcards on '{name}' (on the Ultron Screen). Ask the user if they want to be "
            f"quizzed now; if yes call quiz(action='next').")


def _grade(card, answer):
    import tools
    out = tools.generate_text(
        f"Question: {card['q']}\nCorrect answer: {card['a']}\nStudent's spoken answer: {answer}\n"
        f"Is the student's answer essentially correct (same meaning; ignore wording, accent and transcription errors)? "
        f"Reply 'CORRECT' or 'WRONG', then ' - ' and one short encouraging sentence.", 200) or ""
    return out.strip().upper().startswith("CORRECT"), out.split("-", 1)[-1].strip()


def quiz(action: str = "next", answer: str = "", deck: str = "") -> str:
    name = _slug(deck) if deck else (_cur["deck"] or (_decks()[0] if _decks() else None))
    if not name or not (DIR / f"{name}.json").exists():
        return "FAILED: there's no flashcard deck yet. Use study first (a topic or a PDF)."
    d = _load(name)
    if action == "stop":
        score = f"{_cur['right']} out of {_cur['asked']}"
        _cur.update(card=None, right=0, asked=0)
        return f"OK: quiz ended. Score this session: {score}."
    feedback = ""
    if action == "answer" and _cur.get("card") is not None:
        card = d["cards"][_cur["card"]]
        ok, note = _grade(card, answer)
        _cur["asked"] += 1
        if ok:
            _cur["right"] += 1
            card["box"] = min(6, card["box"] + 1)
        else:
            card["box"] = 1
        card["due"] = time.time() + INTERVALS[card["box"]]
        _save(name, d)
        feedback = ("Correct! " if ok else f"Not quite - the answer is: {card['a']}. ") + note + " "
    now = time.time()
    due = [i for i, c in enumerate(d["cards"]) if c["due"] <= now and i != _cur.get("card")]
    if not due:
        _cur["card"] = None
        mastered = sum(c["box"] >= 4 for c in d["cards"])
        return (f"OK: {feedback}No more cards due right now - {mastered} of {len(d['cards'])} are well learned. "
                f"Session score {_cur['right']}/{_cur['asked']}. Come back later for spaced review.")
    i = min(due, key=lambda k: (d["cards"][k]["box"], d["cards"][k]["due"]))
    _cur.update(deck=name, card=i)
    return (f"OK: {feedback}Next question (card {i + 1}/{len(d['cards'])}): {d['cards'][i]['q']} "
            f"(Say the feedback, then ask exactly this question and wait; pass the user's reply to quiz(action='answer').)")
