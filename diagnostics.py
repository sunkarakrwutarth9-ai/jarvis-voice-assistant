"""Self-diagnostic: 'Ultron, run diagnostics' - checks every system, fixes what it safely can, shows a report.

Checks: internet, each AI brain (live ping), Groq hearing, live voice, microphone + wake word, speakers,
the command center, API keys, Google connections, disk / memory / CPU / battery, Ultron's own files.
Auto-fixes: clears stuck brain failure marks (so a recovered model is used again), rotates a huge log,
re-creates missing memory folders, wakes up a stuck microphone, reloads .env keys.
"""
import datetime
import html
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("jarvis.diagnostics")
HERE = Path(__file__).resolve().parent
ctx = {"brain": None, "listener": None, "port": 7777}


def _timed(fn):
    t = time.monotonic()
    try:
        out = fn()
        return True, out, time.monotonic() - t
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}", time.monotonic() - t


def run(fix=True):
    import httpx
    rows, fixes = [], []          # (group, name, status ok|warn|bad, detail)

    def add(group, name, status, detail):
        rows.append((group, name, status, detail))

    # ---- network
    ok, _o, dt = _timed(lambda: httpx.get("https://www.google.com/generate_204", timeout=6).raise_for_status())
    add("Network", "Internet", "ok" if ok and dt < 1.5 else "warn" if ok else "bad",
        f"{dt * 1000:.0f} ms" if ok else "offline - AI answers, hearing and search need internet")

    # ---- brains
    if fix:
        try:
            from dotenv import load_dotenv
            load_dotenv(HERE / ".env", override=False)
        except Exception:
            pass
    brain = ctx["brain"]
    if brain is not None:
        if fix:
            cleared = 0
            with brain.health._lock:
                for s in brain.health._stats.values():
                    if s["fails"] >= 1:
                        s["fails"], cleared = 0.0, cleared + 1
            if cleared:
                fixes.append(f"Cleared failure marks on {cleared} AI model(s) so they are tried again")
        import tools
        if tools.generate_text:
            ok, out, dt = _timed(lambda: tools.generate_text("Reply with exactly: OK", 5))
            add("Brain", "Thinking (best model)", "ok" if ok and dt < 4 else "warn" if ok else "bad",
                f"answered in {dt:.1f} s" if ok else out)
        table = brain.health.table()
        fast = [r for r in table if r["fails"] < 1][:4]
        add("Brain", "Models in the race", "ok" if len(table) >= 3 else "warn",
            f"{len(table)} models; fastest: " + ", ".join(f"{r['name'].split(':')[-1]} {r['lat']}s" for r in fast))
    for env, name in (("GEMINI_API_KEY", "Gemini"), ("GROQ_API_KEY", "Groq"), ("XPL_API_KEY", "DeepSeek")):
        key = os.environ.get(env, "")
        if not key:
            add("Brain", f"{name} key", "warn" if env != "GEMINI_API_KEY" else "bad", "missing - add it with 🔑 API keys")
            continue
        url = {"GEMINI_API_KEY": "https://generativelanguage.googleapis.com/v1beta/openai/models",
               "GROQ_API_KEY": "https://api.groq.com/openai/v1/models",
               "XPL_API_KEY": "https://api.experientiallabs.ai/v1/models"}[env]
        ok, out, dt = _timed(lambda: httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=8).raise_for_status())
        bad_key = not ok and ("401" in str(out) or "403" in str(out))
        add("Brain", f"{name} key", "ok" if ok else "bad" if bad_key else "warn",
            f"valid ({dt * 1000:.0f} ms)" if ok else ("rejected - replace it with 🔑 API keys" if bad_key else out))

    # ---- hearing / voice
    lst = ctx["listener"]
    if lst is not None and not getattr(lst, "ready", True) and not lst.error:
        add("Hearing", "Microphone + wake word", "ok", "still starting up (takes ~30 s after launch)")
    elif lst is not None:
        alive = lst.is_alive() and not lst.error
        add("Hearing", "Microphone", "ok" if alive else "bad",
            f"listening · background {lst._raw_noise:.0f} · gain x{lst.gain:.1f}" if alive else (lst.error or "stopped - restart Ultron"))
        if alive and lst.gain >= 5.5:
            add("Hearing", "Mic volume", "warn", "very quiet mic - raise it in Windows Sound settings → Input")
        add("Hearing", "Wake word ('Ultron' / 'Jarvis')", "ok" if lst._model is not None else "warn",
            "offline wake model loaded" if lst._model is not None else "backup speech wake only (slower)")
    add("Hearing", "Groq Whisper (fast hearing)", "ok" if os.environ.get("GROQ_API_KEY") else "warn",
        "on" if os.environ.get("GROQ_API_KEY") else "off - add a Groq key")
    try:
        import websockets  # noqa: F401
        add("Voice", "Live voice", "ok" if os.environ.get("GEMINI_API_KEY") else "warn", "ready - say 'live mode'")
    except ImportError:
        add("Voice", "Live voice", "warn", "needs: pip install websockets")
    try:
        import sounddevice as sd
        out = sd.query_devices(kind="output")
        add("Voice", "Speakers", "ok", out["name"])
    except Exception as e:
        add("Voice", "Speakers", "bad", f"no output device ({str(e)[:60]})")

    # ---- Ultron itself
    ok, _o, dt = _timed(lambda: httpx.get(f"http://127.0.0.1:{ctx['port']}/api/state", timeout=3).raise_for_status())
    add("Ultron", "Command center", "ok" if ok else "bad", f"http://localhost:{ctx['port']}" if ok else "not responding")
    try:
        import apikeys
        miss = apikeys.missing()
        add("Ultron", "API keys", "ok", "all set" if not miss else "optional missing: " + ", ".join(miss))
    except Exception:
        pass
    for f, name in (("google_workspace_token.json", "Google Calendar"), ("gmail_token.json", "Gmail")):
        add("Ultron", name, "ok" if (HERE / f).exists() else "warn", "connected" if (HERE / f).exists() else "not connected (optional)")
    vault = HERE / "vault"
    if fix and not (vault / "Journal").exists():
        try:
            import vault as v
            v.init()
            fixes.append("Re-created the memory vault folders")
        except Exception:
            pass
    notes = len(list(vault.rglob("*.md"))) if vault.exists() else 0
    add("Ultron", "Memory vault", "ok" if notes else "warn", f"{notes} notes")
    lessons = vault / "Lessons.md"
    n_less = sum(1 for l in lessons.read_text(encoding="utf-8").splitlines() if l.startswith("- ")) if lessons.exists() else 0
    add("Ultron", "Lessons learned", "ok", f"{n_less} lessons from past mistakes")
    logf = HERE / "jarvis.log"
    if logf.exists():
        mb = logf.stat().st_size / 1e6
        if fix and mb > 25:
            try:
                shutil.copyfile(logf, HERE / "jarvis.old.log")
                logf.write_text("", encoding="utf-8")
                fixes.append(f"Rotated the {mb:.0f} MB log file")
                mb = 0
            except OSError:
                pass
        add("Ultron", "Log file", "ok" if mb < 25 else "warn", f"{mb:.1f} MB")

    # ---- PC
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.4)
        mem = psutil.virtual_memory()
        add("PC", "CPU", "ok" if cpu < 80 else "warn", f"{cpu:.0f}%")
        add("PC", "Memory", "ok" if mem.percent < 85 else "warn", f"{mem.percent:.0f}% used of {mem.total / 1e9:.0f} GB")
        for drive in ("C:\\", str(HERE.anchor)):
            du = shutil.disk_usage(drive)
            free = du.free / 1e9
            add("PC", f"Disk {drive[:2]}", "ok" if free > 10 else "warn" if free > 3 else "bad", f"{free:.0f} GB free")
            if drive == str(HERE.anchor):
                break
        bat = psutil.sensors_battery()
        if bat:
            add("PC", "Battery", "ok" if bat.percent > 20 or bat.power_plugged else "warn",
                f"{bat.percent:.0f}%{' · charging' if bat.power_plugged else ''}")
    except Exception:
        pass

    bad = sum(1 for r in rows if r[2] == "bad")
    warn = sum(1 for r in rows if r[2] == "warn")
    return rows, fixes, bad, warn


def report_html(rows, fixes, bad, warn):
    color = {"ok": "#3ddc84", "warn": "#ffb84d", "bad": "#ff4d5a"}
    icon = {"ok": "✓", "warn": "!", "bad": "✕"}
    groups = {}
    for g, n, s, d in rows:
        groups.setdefault(g, []).append((n, s, d))
    score = max(0, 100 - bad * 15 - warn * 4)
    cards = ""
    for g, items in groups.items():
        cards += f'<section><h2>{html.escape(g)}</h2>' + "".join(
            f'<div class="row" style="animation-delay:{i * 60}ms"><span class="dot" style="background:{color[s]};'
            f'box-shadow:0 0 12px {color[s]}">{icon[s]}</span><b>{html.escape(n)}</b><small>{html.escape(str(d))}</small></div>'
            for i, (n, s, d) in enumerate(items)) + "</section>"
    fx = "".join(f"<li>🔧 {html.escape(f)}</li>" for f in fixes) or "<li>Nothing needed fixing.</li>"
    verdict = "ALL SYSTEMS NOMINAL" if not bad and not warn else "ATTENTION NEEDED" if bad else "MINOR ISSUES"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:radial-gradient(circle at 50% 0,#1a0d14,#05060c 70%);color:#e9eef7;font:15px 'Segoe UI',system-ui;padding:28px}}
h1{{font:600 22px 'Segoe UI';letter-spacing:6px;margin:0;color:#ff4d5a}} .sub{{color:#8a93a6;letter-spacing:2px;font-size:12px}}
.top{{display:flex;align-items:center;gap:26px;margin-bottom:22px}}
.ring{{width:110px;height:110px;border-radius:50%;display:grid;place-items:center;font:700 30px 'Segoe UI';
 background:conic-gradient({'#3ddc84' if score > 85 else '#ffb84d' if score > 60 else '#ff4d5a'} {score * 3.6}deg,#1d2230 0);position:relative}}
.ring:after{{content:"";position:absolute;inset:9px;border-radius:50%;background:#090b13}} .ring span{{position:relative;z-index:1}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}}
section{{background:rgba(255,255,255,.035);border:1px solid rgba(255,77,90,.25);border-radius:16px;padding:14px 16px}}
h2{{font:600 12px 'Segoe UI';letter-spacing:4px;color:#f4ba42;margin:0 0 10px;text-transform:uppercase}}
.row{{display:grid;grid-template-columns:26px 1fr;column-gap:10px;padding:7px 0;border-top:1px solid rgba(255,255,255,.05);opacity:0;animation:in .4s forwards}}
.row:first-of-type{{border-top:0}} .row small{{grid-column:2;color:#8a93a6;font-size:12.5px}}
.dot{{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;color:#05060c;grid-row:span 2}}
ul{{margin:8px 0 0;padding-left:18px;color:#cfd6e4}} @keyframes in{{from{{opacity:0;transform:translateX(-8px)}}to{{opacity:1}}}}
</style></head><body><div class="top"><div class="ring"><span>{score}</span></div><div><h1>SYSTEM DIAGNOSTIC</h1>
<div class="sub">{verdict} · {bad} critical · {warn} warnings · {datetime.datetime.now():%d %b %Y %I:%M %p}</div>
<ul>{fx}</ul></div></div><div class="grid">{cards}</div></body></html>"""


def diagnostics(fix: bool = True) -> str:
    """Tool: run the full system check, fix what is safe, show the report on the Ultron Screen."""
    import tools
    rows, fixes, bad, warn = run(fix)
    try:
        tools.show_content("webpage", "System diagnostic", "html", report_html(rows, fixes, bad, warn))
    except Exception:
        log.exception("could not show the report")
    problems = [f"{n}: {d}" for _g, n, s, d in rows if s == "bad"] + [f"{n}: {d}" for _g, n, s, d in rows if s == "warn"]
    head = "All systems nominal." if not problems else f"{bad} critical, {warn} warnings."
    return (f"OK: diagnostic done - {head} " + ("Fixed: " + "; ".join(fixes) + ". " if fixes else "")
            + ("Issues: " + " | ".join(problems[:8]) if problems else "")
            + " The full report is on the Ultron Screen. Summarise it in one or two spoken sentences.")
