"""ULTRON Software Company: one sentence in, a working, tested software project out.

A full AI team, each role its own agent (and the engineers spread over different AI brains, in parallel):

  👔 CEO / Product manager  idea -> product spec: features, user stories, acceptance criteria
  📐 Architect              spec -> file plan, interfaces, tech stack that runs on this PC, test plan
  👩‍💻 Engineers (parallel)   one engineer per file, all at once, sharing the architect's interfaces
  🔍 Code reviewer          reads everything together, finds bugs and mismatches -> engineers fix them
  🧪 QA engineer            writes real automated tests and RUNS them (pytest / node), reads the failures
  🛠 Debugger               fixes the code from the failing output, re-runs - up to 4 rounds until green
  📝 Tech writer            README: what it is, how to run it, how it works
  🚀 Release                project folder in Documents/Ultron Projects, opens in VS Code, live preview

Everything is shown live on /company: org chart, Kanban board, test terminal, final report.
Safety: generated code is only run (tests) inside its own project folder, with a time limit, and never if it
contains dangerous operations (deleting outside the project, shell commands, registry, credentials).
"""
import concurrent.futures as cf
import datetime
import html
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.softco")
ROOT = Path.home() / "Documents" / "Ultron Projects"
hooks = {"publish": lambda e: None, "notify": lambda t: None, "show": None, "open": None}
ROLES = [("ceo", "👔", "CEO / Product"), ("architect", "📐", "Architect"), ("eng", "👩‍💻", "Engineers"),
         ("review", "🔍", "Code reviewer"), ("qa", "🧪", "QA engineer"), ("debug", "🛠", "Debugger"),
         ("writer", "📝", "Tech writer"), ("release", "🚀", "Release")]
JOB = {"running": False, "id": "", "idea": "", "board": {}, "roles": {}, "log": [], "terminal": "", "result": None}
_lock = threading.RLock()
DANGER = re.compile(r"shutil\.rmtree\s*\(\s*['\"]?[A-Za-z]:|os\.system|subprocess|winreg|ctypes\.windll|"
                    r"rm\s+-rf|format\s+c:|del\s+/[sq]|Remove-Item|keyring|getpass|\.ssh|AppData|"
                    r"requests\.(post|put|delete)|child_process|execSync|fs\.rm(Sync)?\(\s*['\"]/", re.I)


# ------------------------------------------------------------------ live state for /company
def _pub(kind, **kw):
    hooks["publish"](dict({"type": "company", "kind": kind, "job": JOB["id"]}, **kw))


def _role(rid, status, text=""):
    with _lock:
        JOB["roles"][rid] = {"status": status, "text": text[:200]}
        JOB["log"].append({"t": datetime.datetime.now().strftime("%H:%M:%S"), "role": rid, "text": text[:200]})
        JOB["log"] = JOB["log"][-200:]
    _pub("role", role=rid, status=status, text=text[:200])


def _card(path, col, who="", note=""):
    with _lock:
        JOB["board"][path] = {"col": col, "who": who, "note": note[:120]}
    _pub("card", path=path, col=col, who=who, note=note[:120])


def _term(text):
    with _lock:
        JOB["terminal"] = (JOB["terminal"] + text)[-12000:]
    _pub("term", text=text[-4000:])


def status():
    with _lock:
        return {"job": dict(JOB), "roles": [{"id": r, "icon": i, "name": n, **JOB["roles"].get(r, {"status": "idle", "text": ""})}
                                            for r, i, n in ROLES],
                "projects": [{"name": p.name, "t": p.stat().st_mtime} for p in sorted(ROOT.glob("*"), key=lambda p: -p.stat().st_mtime)[:12]]
                if ROOT.exists() else []}


# ------------------------------------------------------------------ LLM helpers (spread over brains)
def _brains():
    import deepthink
    try:
        p = list(deepthink._panel(3) or [])
    except Exception:
        p = []
    return p or [None]


def _llm(prompt, max_tokens=6000, model=None):
    import agents
    return agents._llm(prompt, max_tokens, model)


def _json(text):
    text = re.sub(r"```(?:json)?", "", text or "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON")
    return json.loads(m.group(0))


def _code(text):
    """The file body from a model answer: the code block, even if the answer was cut off before the closing ```."""
    text = text or ""
    m = re.findall(r"```[\w+.-]*\n(.*?)```", text, re.S)
    if m:
        return max(m, key=len).strip("\n") + "\n"
    m = re.search(r"```[\w+.-]*\n(.*)", text, re.S)          # opened but never closed (answer cut off)
    return (m.group(1) if m else text).strip("\n") + "\n"


def _files_from(text):
    """'FILE: path' + code block pairs from a debugger answer -> {path: body}."""
    out = {}
    for path, body in re.findall(r"FILE:\s*`?([\w./\\-]+)`?\s*\n```[\w+.-]*\n(.*?)```", text or "", re.S):
        out[path.replace("\\", "/").strip()] = body.strip("\n") + "\n"
    return out


def _broken(path, body):
    """A syntax error in a Python file (cut-off / mangled answer), else ''."""
    if not path.endswith(".py"):
        return ""
    try:
        compile(body, path, "exec")
        return ""
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"


def _solid(path, body, regenerate):
    """Rewrite a file that came back broken (once or twice) instead of shipping a syntax error."""
    for _ in range(2):
        err = _broken(path, body)
        if not err:
            break
        log.info("%s came back broken (%s) - rewriting", path, err)
        body = _code(regenerate(err))
    return body


# ------------------------------------------------------------------ the pipeline
def build_software(idea: str, stack: str = "auto") -> str:
    """Tool: the AI software company builds a working project from one sentence."""
    with _lock:
        if JOB["running"]:
            return f"FAILED: the team is still building '{JOB['idea'][:60]}' - watch it on the Company page."
        JOB.update(running=True, id=f"{int(time.time())}", idea=idea, board={}, roles={}, log=[], terminal="", result=None)
    threading.Thread(target=_run, args=(idea, stack), daemon=True, name="softco").start()
    if hooks.get("open"):
        try:
            hooks["open"]()
        except Exception:
            pass
    return ("OK: 🏢 the Ultron Software Company has started on it - CEO → architect → engineers in parallel → code "
            "review → QA runs real tests → debugging until green → docs → release. Watch it live on the Company page; "
            "I'll tell you when it ships (a few minutes).")


def _run(idea, stack):
    t0 = time.time()
    try:
        _pipeline(idea, stack, t0)
    except Exception as e:
        log.exception("software company failed")
        _role("release", "failed", f"Stopped: {str(e)[:150]}")
        hooks["notify"](f"Sorry Sir, the software team hit a problem: {str(e)[:120]}")
    finally:
        with _lock:
            JOB["running"] = False
        _pub("done")


def _pipeline(idea, stack, t0):
    # 👔 CEO / product manager
    _role("ceo", "working", "Turning the idea into a product spec")
    spec = _json(_llm(
        "You are the CEO/product manager of a small elite software company. Turn the idea into a crisp product spec "
        "for a FIRST working version that can be built today (no paid services, no API keys, no database server). "
        'JSON: {"name": "kebab-case-name", "title": "...", "pitch": "one line", "features": ["..."], '
        '"stories": ["As a ... I want ... so that ..."], "acceptance": ["testable criterion"]}\n\nIdea: ' + idea, 2500))
    name = re.sub(r"[^a-z0-9-]+", "-", spec.get("name", "app").lower()).strip("-")[:40] or "app"
    _role("ceo", "done", f"Spec: {spec.get('title', name)} - {len(spec.get('features', []))} features")

    # 📐 architect
    _role("architect", "working", "Designing the architecture and files")
    arch = _json(_llm(
        "You are the software architect. Design the smallest clean architecture that fully delivers the spec and runs "
        "on a Windows PC with only Python 3.11 standard library + pytest, or a static web app (HTML/CSS/JS, no build "
        f"step, no CDN needed){'; the user asked for: ' + stack if stack and stack != 'auto' else ''}. 3-9 source files "
        "(not counting tests). Put pure, testable logic in modules separate from UI. Give each file a precise "
        "public interface (function/class signatures) so engineers can work in parallel. "
        'JSON: {"stack": "python" | "web", "run": "how to run it (one command)", "files": [{"path": "relative/path.ext", '
        '"purpose": "...", "interface": "exact signatures / exports"}], "test_plan": ["what the tests check"]}\n\n'
        f"Spec: {json.dumps(spec, ensure_ascii=False)}", 4000))
    files = [f for f in arch.get("files", []) if f.get("path") and not str(f["path"]).startswith(("..", "/", "\\"))][:10]
    kind = "web" if str(arch.get("stack", "")).lower().startswith("web") else "python"
    proj = ROOT / f"{name}"
    if proj.exists():
        proj = ROOT / f"{name}-{datetime.datetime.now():%m%d-%H%M}"
    proj.mkdir(parents=True, exist_ok=True)
    for f in files:
        _card(f["path"], "todo", note=f.get("purpose", ""))
    _role("architect", "done", f"{kind} · {len(files)} files · run: {arch.get('run', '')}")
    contract = (f"PRODUCT SPEC:\n{json.dumps(spec, ensure_ascii=False)}\n\nARCHITECTURE ({kind}):\n" +
                "\n".join(f"- {f['path']}: {f.get('purpose', '')}\n  interface: {f.get('interface', '')}" for f in files))

    # 👩‍💻 engineers, in parallel, on different brains
    brains = _brains()
    _role("eng", "working", f"{len(files)} engineers writing code in parallel on {len(set(map(str, brains)))} AI brains")
    code = {}

    def engineer(i_f):
        i, f = i_f
        who = f"Engineer {i + 1}"
        _card(f["path"], "doing", who, f"writing on {brains[i % len(brains)] or 'best model'}")
        body = _code(_llm(
            f"You are {who}, a senior engineer. Write the COMPLETE file `{f['path']}` - production quality, no "
            "placeholders, no TODOs, handles edge cases, follows the interface EXACTLY so the other files work with it. "
            "Python: standard library only, type hints, docstrings, `if __name__ == '__main__':` entry where it makes "
            "sense. Web: semantic HTML, modern responsive CSS, vanilla JS (ES modules ok), beautiful UI. "
            "Never touch files outside the project, no network calls, no shell commands. Reply with only the file in "
            f"one code block.\n\n{contract}", 16000, brains[i % len(brains)]))
        body = _solid(f["path"], body, lambda err: _llm(
            f"Your file `{f['path']}` has a syntax error ({err}) - it was probably cut off. Write the COMPLETE file again, "
            f"more compact, in one code block.\n\n{contract}", 16000))
        return f["path"], body

    with cf.ThreadPoolExecutor(4) as ex:
        for path, body in ex.map(engineer, enumerate(files)):
            code[path] = body
            _write(proj, path, body)
            _card(path, "review", note=f"{body.count(chr(10))} lines")
    _role("eng", "done", f"{sum(b.count(chr(10)) for b in code.values())} lines written")

    # 🔍 code review -> fixes
    _role("review", "working", "Reviewing all files together")
    bundle = lambda: "\n\n".join(f"=== {p} ===\n{b[:14000]}" for p, b in code.items())
    try:
        rev = _json(_llm("You are a strict senior code reviewer. Find REAL bugs: interface mismatches between files, "
                         "wrong imports/paths, crashes, missing functions, broken UI wiring, security problems. Ignore "
                         'style. JSON: {"issues": [{"file": "path", "problem": "...", "fix": "..."}]} (empty list if '
                         f"clean).\n\n{contract}\n\nCODE:\n{bundle()}", 4000))
        issues = [i for i in rev.get("issues", []) if i.get("file") in code][:12]
    except Exception:
        issues = []
    _role("review", "done", f"{len(issues)} issue(s) found")
    if issues:
        by_file = {}
        for it in issues:
            by_file.setdefault(it["file"], []).append(it)
        _role("eng", "working", f"Fixing {len(issues)} review issue(s)")

        def fix(item):
            path, its = item
            _card(path, "doing", "Engineer", f"fixing {len(its)} review note(s)")
            body = _code(_llm(f"Fix these review issues in `{path}` and return the COMPLETE corrected file in one code "
                              f"block.\nIssues: {json.dumps(its, ensure_ascii=False)}\n\n{contract}\n\nOTHER FILES:\n"
                              + "\n\n".join(f"=== {p} ===\n{b[:6000]}" for p, b in code.items() if p != path)
                              + f"\n\nCURRENT `{path}`:\n{code[path]}", 16000))
            if _broken(path, body):
                body = code[path]                       # a broken "fix" is worse than the original
            return path, body

        with cf.ThreadPoolExecutor(4) as ex:
            for path, body in ex.map(fix, by_file.items()):
                code[path] = body
                _write(proj, path, body)
                _card(path, "testing", note="review fixed")
        _role("eng", "done", "Review fixes applied")

    # 🧪 QA: write tests and run them; 🛠 debug until green
    _role("qa", "working", "Writing automated tests")
    test_path = "tests/test_app.py" if kind == "python" else "tests/check.mjs"
    tests = _code(_llm(
        ("You are the QA engineer. Write a pytest file `tests/test_app.py` that imports the project's pure-logic "
         "modules (the project root is on sys.path - add `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`) "
         "and checks every acceptance criterion and edge case. No GUI, no network, no sleeping, fast. ") if kind == "python" else
        ("You are the QA engineer. Write `tests/check.mjs`, a Node 20+ script with no dependencies that reads the "
         "project's HTML/CSS/JS files (paths relative to the project root, which is the parent of tests/) and checks: "
         "every file referenced by index.html exists, every element id used by the JS with getElementById/querySelector('#') "
         "exists in the HTML, and imports pure-logic JS modules (export functions) with dynamic import() to test their "
         "behaviour against the acceptance criteria. Print each check, exit code 1 if any fails. ")
        + "Keep it focused: 10-25 clear tests. Reply with only the file in one code block, no explanation."
        + f"\n\n{contract}\n\nCODE:\n{bundle()}", 14000))
    tests = _solid(test_path, tests, lambda err: _llm(
        f"Your test file has a syntax error ({err}). Write the COMPLETE `{test_path}` again, shorter (10-15 tests), in one "
        f"code block.\n\n{contract}\n\nCODE:\n{bundle()}", 14000))
    _write(proj, test_path, tests)
    _card(test_path, "testing", "QA", "tests written")
    passed, out = False, ""
    for rnd in range(1, 6):
        danger = [p for p, b in list(code.items()) + [(test_path, tests)] if DANGER.search(b)]
        if danger:
            out = f"Not run automatically: {', '.join(danger)} contain operations that need your review first."
            _term(f"\n⚠ {out}\n")
            break
        _role("qa", "working", f"Running the tests (round {rnd})")
        passed, out = _run_tests(proj, kind, test_path)
        _term(f"\n$ {'pytest -q' if kind == 'python' else 'node tests/check.mjs'}   (round {rnd})\n{out[-3500:]}\n")
        if passed:
            _role("qa", "done", f"All tests pass (round {rnd})")
            break
        if rnd == 5:
            _role("qa", "failed", "Still failing after 4 debug rounds")
            break
        _role("debug", "working", f"Debugging the failures (round {rnd})")
        answer = _llm(
            "You are the debugger. The tests failed. Find the root cause, then fix the CODE (or the TEST only if the test "
            "itself is wrong). Reply with:\nDIAGNOSIS: one line\nthen for EVERY file you change:\nFILE: path\n```\n<the "
            "COMPLETE new file>\n```\nChange as few files as possible.\n\n"
            f"FAILING OUTPUT:\n{out[-5000:]}\n\n{contract}\n\nCODE:\n{bundle()}\n\n=== {test_path} ===\n{tests}", 20000)
        diag = re.search(r"DIAGNOSIS:\s*(.+)", answer or "")
        fixes = {"diagnosis": diag.group(1).strip() if diag else "", "files": {p: b for p, b in _files_from(answer).items()
                                                                               if not _broken(p, b)}}
        changed = 0
        for p, body in (fixes.get("files") or {}).items():
            if p == test_path:
                tests = body if body.endswith("\n") else body + "\n"
                _write(proj, p, tests)
                changed += 1
            elif p in code:
                code[p] = body if body.endswith("\n") else body + "\n"
                _write(proj, p, code[p])
                _card(p, "testing", "Debugger", "patched")
                changed += 1
        _role("debug", "done", f"Round {rnd}: {fixes.get('diagnosis', '')[:140]} ({changed} file(s) patched)")
        if not changed:
            break
    for p in code:
        _card(p, "done" if passed else "testing", note="✓ tested" if passed else "needs attention")

    # 📝 docs
    _role("writer", "working", "Writing the README")
    readme = _llm(f"Write README.md for this project: title, one-line pitch, features, how to run ({arch.get('run', '')}), "
                  "how to run the tests, project structure (each file, one line), how it works. Markdown, concise.\n\n"
                  f"{contract}", 2500)
    _write(proj, "README.md", re.sub(r"^```(?:markdown|md)?\n|```$", "", readme.strip()) + "\n")
    _role("writer", "done", "README.md written")

    # 🚀 release
    _role("release", "working", "Packaging and opening the project")
    lines = sum(b.count("\n") for b in code.values()) + tests.count("\n")
    secs = time.time() - t0
    vscode = shutil.which("code") or shutil.which("code.cmd")
    if vscode:
        try:
            subprocess.Popen([vscode, str(proj)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except Exception:
            pass
    preview = ""
    if kind == "web" and (proj / "index.html").exists():
        preview = _inline_web(proj)
    result = {"name": spec.get("title", name), "folder": str(proj), "kind": kind, "files": len(code) + 2,
              "lines": lines, "passed": passed, "seconds": round(secs), "run": arch.get("run", ""),
              "issues": len(issues)}
    with _lock:
        JOB["result"] = result
    _show_report(spec, arch, result, out, preview)
    _role("release", "done", f"Shipped {result['name']} in {secs / 60:.1f} min - tests {'✓ passing' if passed else '✗ need attention'}")
    hooks["notify"](f"Your software is ready, Sir: {result['name']}. {result['files']} files, {lines} lines, tests "
                    f"{'all passing' if passed else 'still failing - see the report'}. It's open in VS Code"
                    + (" and on the screen." if preview else "."))


def _write(proj, rel, body):
    p = (proj / rel).resolve()
    if proj.resolve() not in p.parents:
        raise ValueError(f"refusing to write outside the project: {rel}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _run_tests(proj, kind, test_path):
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "tests"] if kind == "python" \
        else [shutil.which("node") or "node", test_path]
    try:
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout + "\n" + r.stderr).strip()
        return r.returncode == 0, out or "(no output)"
    except subprocess.TimeoutExpired:
        return False, "Tests timed out after 90 s (probably an endless loop or waiting for input)."
    except Exception as e:
        return False, f"Could not run the tests: {e}"


def _inline_web(proj):
    """index.html with its local CSS/JS inlined, for a live preview on the Ultron Screen."""
    page = (proj / "index.html").read_text(encoding="utf-8", errors="ignore")

    def css(m):
        f = proj / m.group(1)
        return f"<style>{f.read_text(encoding='utf-8', errors='ignore')}</style>" if f.exists() else m.group(0)

    def js(m):
        f = proj / m.group(2)
        if not f.exists() or "type=\"module\"" in m.group(0) or "type='module'" in m.group(0):
            return m.group(0)
        return f"<script>{f.read_text(encoding='utf-8', errors='ignore')}</script>"
    page = re.sub(r'<link[^>]+href="([^"]+\.css)"[^>]*>', css, page)
    page = re.sub(r'<script([^>]*)src="([^"]+\.js)"[^>]*>\s*</script>', js, page)
    return page


def _show_report(spec, arch, res, test_out, preview):
    e = html.escape
    with _lock:
        timeline = "".join(f"<div class='b'><small>{l['t']}</small> <b>{e(next((i + ' ' + n for r, i, n in ROLES if r == l['role']), l['role']))}</b> "
                           f"{e(l['text'])}</div>" for l in JOB["log"])
    files = "".join(f"<tr><td><code>{e(f['path'])}</code></td><td>{e(f.get('purpose', ''))}</td></tr>" for f in arch.get("files", []))
    page = f"""<!doctype html><html><head><meta charset='utf-8'><style>
body{{margin:0;font:15px 'Segoe UI',system-ui;background:radial-gradient(circle at 20% 0,#11203f,#05070e 70%);color:#e8eefc;padding:28px}}
h1{{margin:0;font:700 28px 'Segoe UI'}} .sub{{color:#8b97b5;margin:4px 0 20px}}
.k{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}}
.k div{{background:rgba(255,255,255,.05);border:1px solid rgba(140,170,255,.2);border-radius:14px;padding:14px}} .k b{{display:block;font-size:26px}}
.card{{background:rgba(255,255,255,.04);border:1px solid rgba(140,170,255,.18);border-radius:16px;padding:16px;margin-bottom:14px}}
h2{{font:600 12px 'Segoe UI';letter-spacing:3px;text-transform:uppercase;color:#7fe6ff;margin:0 0 10px}}
td{{padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.06)}} code{{color:#a78bfa}}
pre{{background:#03050a;border-radius:12px;padding:12px;white-space:pre-wrap;max-height:320px;overflow:auto;font-size:12.5px;color:#b9f6ca}}
.b{{padding:5px 0;border-bottom:1px solid rgba(255,255,255,.05);font-size:13px}} .b small{{color:#8b97b5}}
.ok{{color:#3ddc84}} .bad{{color:#ff6b6b}}</style></head><body>
<h1>🚀 {e(res['name'])}</h1><div class='sub'>{e(spec.get('pitch', ''))} · built by the Ultron Software Company</div>
<div class='k'><div>Status<b class='{"ok" if res["passed"] else "bad"}'>{"✓ Tests pass" if res["passed"] else "✗ Tests fail"}</b></div>
<div>Files<b>{res['files']}</b></div><div>Lines of code<b>{res['lines']}</b></div><div>Review issues fixed<b>{res['issues']}</b></div>
<div>Built in<b>{res['seconds'] // 60}m {res['seconds'] % 60}s</b></div></div>
<div class='card'><h2>Run it</h2><code>{e(res['run'])}</code><br><small style='color:#8b97b5'>Folder: {e(res['folder'])}</small></div>
<div class='card'><h2>Features</h2>{''.join(f'<div>✓ {e(f)}</div>' for f in spec.get('features', []))}</div>
<div class='card'><h2>Architecture</h2><table>{files}</table></div>
<div class='card'><h2>Last test run</h2><pre>{e(test_out[-4000:])}</pre></div>
<div class='card'><h2>How the team built it</h2>{timeline}</div></body></html>"""
    _pub("result", html=page[:500000])
    if hooks["show"]:
        try:
            hooks["show"]("webpage", f"🚀 {res['name'][:40]}", "html", page)
            if preview:
                hooks["show"]("webpage", f"▶ {res['name'][:40]} (live)", "html", preview)
        except Exception:
            log.exception("show failed")


def company() -> str:
    if hooks.get("open"):
        hooks["open"]()
    with _lock:
        running, idea = JOB["running"], JOB["idea"]
    return "OK: the Software Company page is open" + (f" - the team is building '{idea[:60]}'." if running else ".")
