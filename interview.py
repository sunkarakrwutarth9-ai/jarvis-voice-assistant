"""Get-to-know-you interview: Ultron asks about the user's life, work, routine, people and goals - one
question at a time, by voice - and files every answer into the memory vault, so from then on it knows
who it's working for. Skip any question; stop any time; resume later where you left off.
"""

import datetime
import json
import logging
import re
import threading

log = logging.getLogger("jarvis.interview")

QUESTIONS = [
    ("Me", "First things first - what should I call you, and what's your full name?"),
    ("Me", "Which city do you live in, and which languages do you like to talk in?"),
    ("Me", "What do you do - are you studying, working, or running a business? Tell me a little about it."),
    ("Projects", "What are the main projects or subjects you're working on right now?"),
    ("Me", "What's your biggest goal for the next three months?"),
    ("Me", "And the big one - where do you want to be in a year?"),
    ("Me", "What does a normal day look like for you - when do you wake up, work, and sleep?"),
    ("Me", "When are you at your sharpest - morning, afternoon, or late night?"),
    ("People", "Who are the most important people in your life? Family, friends, team - and any birthdays I should remember?"),
    ("Me", "What slows you down or stresses you most in a normal week?"),
    ("Me", "Which apps and websites do you use most for work or study?"),
    ("Me", "What music, food and hobbies do you enjoy?"),
    ("Me", "How do you like my answers - short and quick, or detailed?"),
    ("Me", "What should I take off your plate first - what do you want me to handle for you?"),
    ("Me", "Anything else I should know about you, Sir?"),
]
_st = {"i": None}


def _state(get=True, value=None):
    import tools
    if get:
        return tools._state("interview_next", 0)
    tools._save_state("interview_next", value)


def _file_answer(section, question, answer):
    """Write the raw answer + extracted facts into the vault (in the background, so the next question is instant)."""
    import tools
    import vault
    vault.init()
    vault._append(vault.ROOT / "Interview.md", f"- **{question}**  \n  {answer.strip()} _({datetime.date.today():%d %b %Y})_")
    try:
        raw = tools.generate_text(
            "Turn this interview answer into short, lasting facts about the user for their personal knowledge vault. "
            "Use note 'Me' for the user's own facts/preferences/routine/goals, 'People/<Name>' for each person "
            "mentioned (relation, birthday...), 'Projects/<Name>' for each project/subject. Never store passwords or ID "
            'numbers. Reply ONLY JSON: [{"note": "...", "fact": "..."}]\n\n'
            f"QUESTION: {question}\nANSWER: {answer}", 1200) or "[]"
        for it in json.loads(re.search(r"\[.*\]", raw, re.S).group(0))[:10]:
            if it.get("fact"):
                vault.remember_note(it["fact"], it.get("note") or section)
    except Exception as e:
        log.info("interview facts not extracted: %s", e)


def interview(action: str = "start", answer: str = "") -> str:
    i = _st["i"] if _st["i"] is not None else _state()
    if action == "restart":
        i = 0
        action = "start"
    if action == "stop":
        _st["i"] = None
        _state(False, i)
        return (f"OK: interview paused after {i} of {len(QUESTIONS)} questions; everything so far is saved to memory. "
                f"Say 'continue the interview' any time.")
    if action == "answer" and _st["i"] is not None and answer.strip():
        section, q = QUESTIONS[_st["i"]]
        threading.Thread(target=_file_answer, args=(section, q, answer), daemon=True).start()
        i = _st["i"] + 1
    elif action == "skip" and _st["i"] is not None:
        i = _st["i"] + 1
    if i >= len(QUESTIONS):
        _st["i"] = None
        _state(False, len(QUESTIONS))
        return ("OK: interview complete - everything is filed in the memory vault (Me, People, Projects, Interview). "
                "Thank the user warmly in one sentence and say you now know how to help them better.")
    _st["i"] = i
    _state(False, i)
    intro = "This is the get-to-know-you interview. " if action == "start" and i == 0 else ""
    return (f"OK: {intro}Question {i + 1} of {len(QUESTIONS)}: {QUESTIONS[i][1]} (Ask exactly this question warmly, "
            f"at most one short acknowledging phrase before it, then wait. Pass the user's reply to "
            f"interview(action='answer', answer=...); 'skip' skips; 'stop' pauses.)")
