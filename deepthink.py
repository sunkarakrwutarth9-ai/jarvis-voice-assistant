"""Deep Think: Ultron's answer to hard questions - several AI models solve it independently, then a judge
compares them, catches mistakes, checks the maths and writes one best answer.

A panel of different models (Gemini and DeepSeek families) makes fewer mistakes than any one of them:
errors are rarely shared, and the judge sees every line of reasoning side by side. Optional live web
facts ground the panel, and `calculate` runs real Python so numbers are computed, not guessed.
"""

import datetime
import logging
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger("jarvis.deepthink")

generate_on = None       # set by brain: generate_on(model_key, prompt, max_tokens) -> text
model_pool = None        # set by brain: healthiest models first, e.g. ['gemini-3.5-flash-lite', 'xpl:deepseek-v4.1-flash']

SOLVER = """You are one expert on a panel answering a hard question. Today is {today}.
Think it through carefully and rigorously: restate what is really being asked, work step by step, check every
calculation twice, consider edge cases and alternative interpretations, and say plainly if something is uncertain
or unknowable. Do not invent facts, names, numbers or sources.
{facts}
QUESTION:
{question}

End with a line starting with "FINAL ANSWER:" followed by the concise answer."""

JUDGE = """You are the chief judge of an expert panel. Today is {today}. Several experts answered the same question
independently. Your job is to produce the single most correct answer - not the most popular one.

1. Compare the answers. Where they disagree, work out who is right by re-deriving it yourself.
2. Check every calculation and factual claim; fix any mistake, even if all experts made it.
3. Write the final answer for the user.
{facts}
QUESTION:
{question}

{answers}

Reply with ONLY one fenced block starting with ```markdown, containing:
# a short title
## Answer
the direct, correct answer first (clear and complete; steps / code / table if useful)
## Why
the key reasoning in a few bullet points
## Where the experts disagreed
(only if they did - who was wrong and why; otherwise omit this section)
## Confidence
High / Medium / Low - one line explaining why"""


def _panel(n=3):
    pool = list(model_pool() if model_pool else [])
    # mix model families: the best Gemini models plus a DeepSeek one if available
    xpl = [m for m in pool if m.startswith("xpl:")]
    gem = [m for m in pool if not m.startswith("xpl:")]
    picks = gem[:n - 1] + xpl[:1]
    for m in pool:
        if len(picks) >= n:
            break
        if m not in picks:
            picks.append(m)
    return picks[:n]


def _facts(question, use_web):
    if not use_web:
        return ""
    try:
        import tools
        snippets = tools._search_snippets(question, n=8)
    except Exception as e:
        log.warning("web facts failed: %s", e)
        return ""
    return f"\nLIVE WEB SEARCH RESULTS (may help; may be noisy - verify against each other):\n{snippets[:6000]}\n"


def deep_think(question: str, use_web: bool = False) -> str:
    import tools
    if generate_on is None:
        return "FAILED: deep thinking is not available."
    today = f"{datetime.date.today():%d %B %Y}"
    facts = _facts(question, use_web)
    panel = _panel()
    tools.progress(f"Deep Think: asking {len(panel)} expert models")
    prompt = SOLVER.format(today=today, facts=facts, question=question)
    answers = []
    with ThreadPoolExecutor(len(panel)) as ex:
        futures = {ex.submit(generate_on, m, prompt, 6000): m for m in panel}
        for f in as_completed(futures, timeout=150):
            m = futures[f]
            try:
                text = (f.result() or "").strip()
                if text:
                    answers.append((m, text))
                    tools.progress(f"Deep Think: {len(answers)}/{len(panel)} experts answered")
            except Exception as e:
                log.warning("deep think expert %s failed: %s", m, str(e)[:120])
    if not answers:
        return "FAILED: none of the expert models could answer right now (rate limits); try again in a minute."
    log.info("deep think: %d experts answered (%s)", len(answers), ", ".join(m for m, _ in answers))
    tools.progress("Deep Think: judging the answers")
    block = "\n\n".join(f"=== EXPERT {chr(65 + i)} ===\n{t[:9000]}" for i, (_, t) in enumerate(answers))
    result = tools._canvas_generate("document", f"Deep Think: {question[:50]}", "md", "md",
                                    JUDGE.format(today=today, facts=facts, question=question, answers=block),
                                    note="")
    if result.startswith("FAILED"):
        return result
    c = tools.CREATIONS.get(tools._last_creation[0]) or {}
    text = c.get("content", "")
    ans = re.search(r"##\s*Answer\s*\n(.*?)(?:\n##\s|\Z)", text, re.S)
    conf = re.search(r"##\s*Confidence\s*\n(.*?)(?:\n##\s|\Z)", text, re.S)
    summary = (ans.group(1).strip() if ans else text[:600])[:900]
    return (f"OK: {len(answers)} expert models answered and a judge verified them; the full answer is on the Ultron "
            f"Screen (not saved). Tell the user the answer in 1-3 short spoken sentences (no markdown, no tables): "
            f"{summary}\nConfidence: {conf.group(1).strip()[:200] if conf else 'not stated'}")


# ------------------------------------------------------------------ exact computation
BLOCKED = re.compile(r"\b(subprocess|socket|shutil|ctypes|winreg|requests|urllib|http\.client|ftplib|smtplib|"
                     r"os\.(system|remove|unlink|rmdir|removedirs|rename|replace|popen|spawn\w*|exec\w*|kill|chmod)|"
                     r"__import__|eval|exec|open\s*\(|pathlib|Path\s*\(|input\s*\()", re.I)


def calculate(code: str) -> str:
    """Run a short pure-computation Python snippet and return what it prints (maths, dates, statistics, units)."""
    code = (code or "").strip()
    if not code:
        return "FAILED: no code."
    bad = BLOCKED.search(code)
    if bad:
        return f"FAILED: calculate is for pure computation only ('{bad.group(0)}' is not allowed)."
    if "print(" not in code:
        lines = code.rstrip().splitlines()
        lines[-1] = f"print({lines[-1]})"
        code = "\n".join(lines)
    prelude = "import math, statistics, fractions, decimal, datetime, itertools, functools, random, cmath\n"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", prelude + code], cwd=tmp, capture_output=True, text=True,
                               timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return "FAILED: the calculation took longer than 20 seconds."
    out = (r.stdout or "").strip()
    if r.returncode:
        return "FAILED: " + (r.stderr or "error").strip().splitlines()[-1][:300]
    return "OK: " + (out[:3000] or "(no output)")
