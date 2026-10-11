"""ULTRON's 8 AI agent systems - one of each classic agent type, all working, all visible on /agents.

  ⚡ Reflex agent         instant "when X then Y" rules on live sensors (battery, CPU, RAM, internet, idle, time)
  🌍 Model-based agent    keeps a model of the world (app in front, meeting, away, battery, online) and adapts -
                          goes quiet in meetings / presentations, holds news while you're away, briefs you when back
  🎯 Goal-based agent     turns a goal into dated milestones, tracks progress, nudges you on the day
  ⚖️ Utility-based agent  decides by maths: weighted criteria x scores -> the option with the highest utility
  📚 Learning agent       learns a lesson from every correction (lessons.py) and uses it from then on
  🗓 Planning agent       builds a time-blocked plan for your day from tasks, calendar, reminders and goals
  👥 Multi-agent system   a council of 3 agents on 3 different AI brains debate, rebut, and a judge decides
  🏛 Hierarchical agent   a manager splits a big job, delegates to specialist workers in parallel, merges

  🎬 Ad Studio            all of them together: research -> 3 copywriters (3 brains) -> critic -> utility pick ->
                          polish -> storyboard with real photos -> posting plan -> voice-over preview
"""
import concurrent.futures as cf
import datetime
import html
import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.agents")
STORE = Path(__file__).resolve().parent / "vault" / "Agents8.json"
_lock = threading.RLock()
hooks = {"publish": lambda e: None, "notify": lambda t: None, "show": None, "is_off": lambda: False,
         "set_hold": lambda on: None, "command": None, "say": lambda t, v=None: None}

AGENTS = [
    {"id": "reflex", "icon": "⚡", "name": "Reflex Agent", "type": "Simple reflex",
     "how": "Condition → action, instantly, no thinking. Watches live sensors every 15 s.",
     "try": "when battery below 20 tell me to plug in the charger", "op": "rule"},
    {"id": "model", "icon": "🌍", "name": "World-Model Agent", "type": "Model-based reflex",
     "how": "Keeps an internal model of your world (app in front, meeting, away, battery, internet) and adapts.",
     "try": "", "op": "world"},
    {"id": "goal", "icon": "🎯", "name": "Goal Agent", "type": "Goal-based",
     "how": "Turns a goal into dated milestones, tracks progress, nudges you on the right day.",
     "try": "crack my physics exam by 25 October", "op": "goal"},
    {"id": "utility", "icon": "⚖️", "name": "Utility Agent", "type": "Utility-based",
     "how": "Weighs every option against your criteria with real maths and picks the highest utility.",
     "try": "which laptop: MacBook Air M4, ASUS Zenbook 14, Dell XPS 13 - for coding and battery life under 1 lakh", "op": "decide"},
    {"id": "learning", "icon": "📚", "name": "Learning Agent", "type": "Learning",
     "how": "Learns a rule from every correction and every night's review - never repeats a mistake.",
     "try": "always reply in Telugu when I speak Telugu", "op": "lesson"},
    {"id": "planning", "icon": "🗓", "name": "Planning Agent", "type": "Planning",
     "how": "Builds a time-blocked plan from your tasks, calendar, reminders and goals.",
     "try": "study physics 3 hours, gym, finish the hospital pitch deck, call mom", "op": "plan"},
    {"id": "council", "icon": "👥", "name": "Agent Council", "type": "Multi-agent system",
     "how": "3 agents on 3 different AI brains answer, debate each other, and a judge decides.",
     "try": "should I sell Ultron to hospitals first or to supermarkets first?", "op": "council"},
    {"id": "manager", "icon": "🏛", "name": "Manager Agent", "type": "Hierarchical",
     "how": "Splits a big job into parts, delegates to specialist workers in parallel, merges the result.",
     "try": "make a complete launch plan for Ultron in India", "op": "delegate"},
]
_status = {a["id"]: {"status": "idle", "text": "", "t": 0} for a in AGENTS + [{"id": "studio"}]}


# ------------------------------------------------------------------ plumbing
def _load():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rules": [], "goals": [], "log": [], "results": []}


def _save(d):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _set(aid, status, text="", to=None):
    """Live status for the agents page (graph + cards + activity feed)."""
    _status[aid] = {"status": status, "text": text[:220], "t": time.time()}
    hooks["publish"]({"type": "agent", "id": aid, "status": status, "text": text[:220], "to": to})
    with _lock:
        d = _load()
        d["log"] = (d.get("log", []) + [{"t": datetime.datetime.now().strftime("%H:%M:%S"), "id": aid,
                                           "status": status, "text": text[:220]}])[-120:]
        _save(d)


def _llm(prompt, max_tokens=3000, model=None):
    import deepthink
    import tools
    tried = []
    for m in [model] + [p for p in _panel(3) if p != model]:      # the asked brain, then the other brains
        if not m or not deepthink.generate_on or m in tried:
            continue
        tried.append(m)
        try:
            out = deepthink.generate_on(m, prompt, max_tokens) or ""
            if out.strip():
                return out
        except Exception as e:
            log.info("model %s failed (%s) - trying another brain", m, str(e)[:80])
    for attempt in range(3):                                      # the best available model, waiting out rate limits
        try:
            return tools.generate_text(prompt, max_tokens) or ""
        except Exception as e:
            wait = re.search(r"retry in ([\d.]+)s", str(e))
            if attempt == 2:
                raise
            time.sleep(min(40.0, float(wait.group(1)) + 1) if wait else 6)


def _json(text, kind="{"):
    text = re.sub(r"```(?:json)?", "", text or "")
    m = re.search(r"\{.*\}" if kind == "{" else r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    return json.loads(m.group(0))


def _panel(n=3):
    import deepthink
    try:
        picks = list(deepthink._panel(n) or [])
    except Exception:
        picks = []
    return (picks + [None] * n)[:n]                  # fewer brains available: the best model fills in


def _show(title, page):
    hooks["publish"]({"type": "agent_result", "title": title, "html": page[:600000]})
    if hooks["show"]:
        try:
            hooks["show"]("webpage", title, "html", page)
        except Exception:
            log.exception("show failed")
    with _lock:
        d = _load()
        d["results"] = (d.get("results", []) + [{"title": title, "t": time.time()}])[-30:]
        _save(d)


def _bg(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True, name=f"agent-{fn.__name__}").start()


PAGE_CSS = """body{margin:0;font:15px 'Segoe UI',system-ui;background:radial-gradient(circle at 20% 0,#14213d,#06080f 70%);color:#e8eefc;padding:28px}
h1{font:700 26px 'Segoe UI';margin:0 0 4px;letter-spacing:.5px} .sub{color:#8b97b5;margin-bottom:22px}
.card{background:rgba(255,255,255,.045);border:1px solid rgba(140,170,255,.18);border-radius:18px;padding:18px;margin-bottom:16px}
h2{font:600 12px 'Segoe UI';letter-spacing:3px;text-transform:uppercase;color:#7fe6ff;margin:0 0 12px}
table{width:100%;border-collapse:collapse} th,td{padding:9px 10px;text-align:left;border-bottom:1px solid rgba(255,255,255,.07);vertical-align:top}
th{color:#8b97b5;font-weight:600;font-size:12px;letter-spacing:1px;text-transform:uppercase}
.bar{height:10px;border-radius:5px;background:linear-gradient(90deg,#22d3ee,#a78bfa)} .win{color:#3ddc84;font-weight:700}
.tag{display:inline-block;padding:3px 10px;border-radius:99px;background:rgba(127,230,255,.12);margin:2px;font-size:12.5px}
.b{border-left:3px solid;padding:10px 14px;margin:10px 0;border-radius:0 12px 12px 0;background:rgba(255,255,255,.035)}"""


# ================================================================== 1. REFLEX AGENT
def _sensors():
    import psutil
    s = {"cpu": psutil.cpu_percent(interval=None), "ram": psutil.virtual_memory().percent,
         "disk_free_gb": round(psutil.disk_usage("C:\\").free / 1e9, 1), "hour": datetime.datetime.now().hour,
         "time": datetime.datetime.now().strftime("%H:%M")}
    b = psutil.sensors_battery()
    if b:
        s.update(battery=round(b.percent), charging=bool(b.power_plugged))
    try:
        from daylog import idle_seconds
        s["idle_min"] = round(idle_seconds() / 60, 1)
    except Exception:
        pass
    s["internet"] = _online()
    return s


_net = {"ok": True, "t": 0}


def _online():
    if time.time() - _net["t"] > 60:
        import socket
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=2).close()
            _net["ok"] = True
        except OSError:
            _net["ok"] = False
        _net["t"] = time.time()
    return _net["ok"]


OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
       "=": lambda a, b: a == b, "!=": lambda a, b: a != b}


def reflex_rule(action: str = "add", rule: str = "", id: int = 0) -> str:
    with _lock:
        d = _load()
        if action == "list":
            r = d.get("rules", [])
            return ("OK: " + " | ".join(f"#{x['id']} when {x['sensor']} {x['op']} {x['value']} → {x['action']}: {x['text']}"
                                        for x in r)) if r else "OK: no reflex rules yet."
        if action == "delete":
            before = len(d.get("rules", []))
            d["rules"] = [x for x in d.get("rules", []) if x["id"] != int(id) and (not rule or rule.lower() not in x["text"].lower())]
            _save(d)
            return f"OK: removed {before - len(d['rules'])} rule(s)."
    _set("reflex", "working", f"Understanding: {rule}")
    raw = _llm('Turn this into ONE reflex rule as JSON: {"sensor": one of battery|charging|cpu|ram|disk_free_gb|internet|'
               'idle_min|hour|time, "op": one of < <= > >= = !=, "value": number or true/false or "HH:MM", "action": '
               'one of say|telegram|command, "text": what to say / send, or the command to run (e.g. "play lofi on '
               'youtube")}. Rule: ' + rule, 800)
    try:
        r = _json(raw)
        assert r.get("sensor") and r.get("op") in OPS
    except Exception:
        _set("reflex", "idle", "Couldn't understand that rule")
        return "FAILED: I couldn't turn that into a rule. Try 'when battery below 20 tell me to plug in'."
    with _lock:
        d = _load()
        r["id"] = max([x["id"] for x in d.get("rules", [])] + [0]) + 1
        r["fired"] = False
        d.setdefault("rules", []).append(r)
        _save(d)
    _set("reflex", "done", f"Rule #{r['id']}: when {r['sensor']} {r['op']} {r['value']} → {r['action']}")
    return f"OK: reflex rule #{r['id']} armed - when {r['sensor']} {r['op']} {r['value']}, I'll {r['action']}: {r['text']}."


def _reflex_tick(sens):
    with _lock:
        d = _load()
        changed = False
        for r in d.get("rules", []):
            v = sens.get(r["sensor"])
            if v is None:
                continue
            target = r["value"]
            try:
                if isinstance(v, bool):
                    target = str(target).lower() in ("true", "1", "yes", "on")
                elif r["sensor"] == "time":
                    target = str(target)
                else:
                    target = float(target)
                hit = OPS[r["op"]](v, target)
            except (TypeError, ValueError):
                continue
            if hit and not r.get("fired"):
                r["fired"], changed = True, True
                threading.Thread(target=_fire, args=(r, v), daemon=True).start()
            elif not hit and r.get("fired"):
                r["fired"], changed = False, True          # re-arm once the condition clears
        if changed:
            _save(d)


def _fire(r, v):
    _set("reflex", "working", f"Rule #{r['id']} fired ({r['sensor']} = {v})")
    if r["action"] == "command" and hooks["command"]:
        hooks["command"](r["text"])
    elif r["action"] == "telegram":
        try:
            import telegrambot
            telegrambot.send("⚡ " + r["text"])
        except Exception:
            pass
    elif not hooks["is_off"]():
        hooks["notify"](r["text"])
    _set("reflex", "done", f"Rule #{r['id']}: {r['text']}")


# ================================================================== 2. MODEL-BASED AGENT
WORLD = {"app": "", "title": "", "meeting": False, "presenting": False, "away": False, "battery": None,
         "online": True, "held": [], "since": time.time()}
MEETING = re.compile(r"\b(zoom meeting|zoom|microsoft teams|teams meeting|google meet|meet -|webex|skype|discord call|"
                     r"whatsapp call|video call)\b", re.I)
PRESENT = re.compile(r"powerpoint slide show|slide show|presenter view|- presenting", re.I)


def _foreground():
    import ctypes
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, b, n + 1)
    title = b.value
    app = title.rsplit(" - ", 1)[-1] if " - " in title else title
    return app[:60], title[:160]


def _world_tick(sens):
    try:
        app, title = _foreground()
    except Exception:
        app, title = "", ""
    was = dict(WORLD)
    WORLD.update(app=app, title=title, battery=sens.get("battery"), online=sens.get("internet", True),
                 meeting=bool(MEETING.search(title)), presenting=bool(PRESENT.search(title)),
                 away=sens.get("idle_min", 0) >= 10)
    quiet = WORLD["meeting"] or WORLD["presenting"]
    if quiet != (was["meeting"] or was["presenting"]):
        hooks["set_hold"](quiet)
        _set("model", "working" if quiet else "done",
             ("In a meeting/presentation - I'm holding everything quietly" if quiet else "Meeting over - back to normal"))
    if was["away"] and not WORLD["away"]:
        held = list(WORLD["held"])
        WORLD["held"].clear()
        _set("model", "done", "You're back" + (f" - {len(held)} update(s) waiting" if held else ""))
        if held and not hooks["is_off"]():
            hooks["notify"]("Welcome back, Sir. While you were away: " + " ".join(held[-3:]))
    elif WORLD["away"] and not was["away"]:
        _set("model", "idle", "You stepped away - I'll keep updates for when you're back")
    if not was["online"] and WORLD["online"]:
        _set("model", "done", "Internet is back")
    elif was["online"] and not WORLD["online"]:
        _set("model", "working", "Internet lost - I'll use what I have offline")


def world_model() -> str:
    w = WORLD
    return (f"OK: world model - in front: {w['app'] or 'desktop'}; meeting: {'yes' if w['meeting'] else 'no'}; "
            f"presenting: {'yes' if w['presenting'] else 'no'}; you are {'away' if w['away'] else 'here'}; battery "
            f"{w['battery'] if w['battery'] is not None else '-'}%; internet {'on' if w['online'] else 'OFF'}. "
            f"In meetings and presentations I hold all spoken alerts automatically.")


def hold_if_away(text):
    """Other parts of Ultron can route announcements through here: kept while the user is away."""
    if WORLD["away"]:
        WORLD["held"].append(text)
        return True
    return False


# ================================================================== 3. GOAL AGENT
def goal(action: str = "add", goal: str = "", step: int = 0, id: int = 0) -> str:
    with _lock:
        d = _load()
        goals = d.setdefault("goals", [])
        if action == "list":
            if not goals:
                return "OK: no goals yet."
            return "OK: " + " | ".join(f"#{g['id']} {g['goal']} - {_pct(g)}% (next: {_next(g)})" for g in goals)
        if action in ("done", "delete"):
            g = next((x for x in goals if x["id"] == int(id)), None) or \
                next((x for x in goals if goal and goal.lower() in x["goal"].lower()), None) or (goals[-1] if goals else None)
            if not g:
                return "FAILED: no such goal."
            if action == "delete":
                goals.remove(g)
                _save(d)
                return f"OK: goal '{g['goal']}' removed."
            idx = int(step) - 1 if step else next((i for i, s in enumerate(g["steps"]) if not s["done"]), -1)
            if 0 <= idx < len(g["steps"]):
                g["steps"][idx]["done"] = True
            _save(d)
            _set("goal", "done", f"{g['goal']}: {_pct(g)}% done")
            return f"OK: marked done. '{g['goal']}' is {_pct(g)}% complete. Next: {_next(g)}."
    _set("goal", "working", f"Planning milestones for: {goal}")
    today = datetime.date.today()
    raw = _llm(f"Today is {today:%A %d %B %Y}. Break this goal into 4-8 concrete, dated milestones (realistic, in order), "
               f'JSON: {{"goal": "...", "deadline": "YYYY-MM-DD", "steps": [{{"text": "...", "due": "YYYY-MM-DD"}}]}}. Goal: {goal}', 1500)
    try:
        g = _json(raw)
        steps = [{"text": s["text"], "due": s.get("due", ""), "done": False} for s in g["steps"]][:10]
    except Exception:
        _set("goal", "idle", "Couldn't plan that goal")
        return "FAILED: I couldn't plan that goal - say it again with a deadline."
    with _lock:
        d = _load()
        gid = max([x["id"] for x in d.get("goals", [])] + [0]) + 1
        d.setdefault("goals", []).append({"id": gid, "goal": g.get("goal", goal), "deadline": g.get("deadline", ""),
                                          "steps": steps, "created": f"{today}"})
        _save(d)
    rows = "".join(f"<tr><td>{i + 1}</td><td>{html.escape(s['text'])}</td><td>{html.escape(s['due'])}</td></tr>" for i, s in enumerate(steps))
    _show(f"🎯 {g.get('goal', goal)[:40]}", f"<!doctype html><html><head><meta charset='utf-8'><style>{PAGE_CSS}</style></head><body>"
          f"<h1>🎯 {html.escape(g.get('goal', goal))}</h1><div class='sub'>Goal agent · deadline {html.escape(g.get('deadline', ''))}</div>"
          f"<div class='card'><table><tr><th>#</th><th>Milestone</th><th>Due</th></tr>{rows}</table></div></body></html>")
    _set("goal", "done", f"Goal #{gid}: {len(steps)} milestones until {g.get('deadline', '')}")
    return (f"OK: goal #{gid} set with {len(steps)} milestones (deadline {g.get('deadline', '')}). First: {steps[0]['text']} "
            f"by {steps[0]['due']}. I'll nudge you on due days; say 'milestone done' as you go.")


def _pct(g):
    return round(100 * sum(s["done"] for s in g["steps"]) / max(1, len(g["steps"])))


def _next(g):
    s = next((s for s in g["steps"] if not s["done"]), None)
    return f"{s['text']} (due {s['due']})" if s else "all done 🎉"


def _goal_nudges():
    today = f"{datetime.date.today()}"
    with _lock:
        d = _load()
        if d.get("nudged") == today:
            return
        due = [(g, s) for g in d.get("goals", []) for s in g["steps"] if not s["done"] and s.get("due", "") <= today]
        d["nudged"] = today
        _save(d)
    if due and not hooks["is_off"]():
        g, s = due[0]
        _set("goal", "working", f"Due today: {s['text']}")
        hooks["notify"](f"Goal check, Sir: '{s['text']}' for {g['goal']} is due today.")


# ================================================================== 4. UTILITY AGENT
def decide(question: str, options: str = "", criteria: str = "") -> str:
    _set("utility", "working", f"Scoring options: {question[:80]}")
    raw = _llm("You are a utility-based decision agent. For the decision below, list the options (use the given ones, "
               "or the 3-5 best real options), 4-6 criteria that matter for THIS user with weights summing to 100, and "
               "score every option 1-10 on every criterion honestly (real facts, no invented specs). JSON: "
               '{"options": ["..."], "criteria": [{"name": "...", "weight": 25}], "scores": {"<option>": {"<criterion>": 7}}, '
               '"notes": {"<option>": "one line"}}\n\n'
               f"Decision: {question}\nOptions: {options or '(find them)'}\nCriteria / priorities: {criteria or '(infer)'}", 2500)
    try:
        d = _json(raw)
        crit = d["criteria"]
        tot_w = sum(float(c["weight"]) for c in crit) or 1
        util = {o: sum(float(d["scores"][o].get(c["name"], 0)) * float(c["weight"]) for c in crit) / tot_w * 10
                for o in d["options"]}
    except Exception:
        _set("utility", "idle", "Couldn't score that")
        return "FAILED: I couldn't score that decision - give me the options."
    ranked = sorted(util.items(), key=lambda x: -x[1])
    best = ranked[0][0]
    head = "".join(f"<th>{html.escape(c['name'])}<br><small>{float(c['weight']):g}%</small></th>" for c in crit)
    rows = "".join(f"<tr><td class='{'win' if o == best else ''}'>{'🏆 ' if o == best else ''}{html.escape(o)}</td>"
                   + "".join(f"<td>{d['scores'][o].get(c['name'], '-')}</td>" for c in crit)
                   + f"<td><b>{u:.0f}</b>/100<div class='bar' style='width:{u:.0f}%'></div></td></tr>" for o, u in ranked)
    notes = "".join(f"<div class='b' style='border-color:#a78bfa'><b>{html.escape(o)}</b> - {html.escape(n)}</div>"
                    for o, n in (d.get("notes") or {}).items())
    _show(f"⚖️ {question[:40]}", f"<!doctype html><html><head><meta charset='utf-8'><style>{PAGE_CSS}</style></head><body>"
          f"<h1>⚖️ {html.escape(question)}</h1><div class='sub'>Utility agent · utility = Σ (weight × score)</div>"
          f"<div class='card'><table><tr><th>Option</th>{head}<th>Utility</th></tr>{rows}</table></div>{notes}</body></html>")
    _set("utility", "done", f"Best: {best} ({ranked[0][1]:.0f}/100)")
    return (f"OK: highest utility: {best} ({ranked[0][1]:.0f}/100), then " +
            ", ".join(f"{o} ({u:.0f})" for o, u in ranked[1:]) + ". The full scoring matrix is on the screen.")


# ================================================================== 5. LEARNING AGENT
def lesson(text: str) -> str:
    import lessons
    _set("learning", "working", "Learning: " + text[:100])
    r = lessons.add(text, "taught")
    _set("learning", "done", r.replace("OK: ", ""))
    return r


# ================================================================== 6. PLANNING AGENT
def plan_day(tasks: str = "", until: str = "22:00") -> str:
    import tools
    _set("planning", "working", "Gathering your calendar, reminders and goals")
    ctxt = []
    for name, args in (("calendar_agenda", {"days": 1}), ("list_reminders", {})):
        fn = tools.FUNCS.get(name)
        if fn:
            try:
                ctxt.append(f"{name}: {fn(**args)}")
            except Exception:
                pass
    with _lock:
        gs = _load().get("goals", [])
    ctxt += [f"goal next step: {_next(g)} ({g['goal']})" for g in gs if _pct(g) < 100]
    now = datetime.datetime.now()
    _set("planning", "working", "Building time blocks")
    raw = _llm(f"Now is {now:%A %d %B %Y %H:%M}. Plan the rest of the day until {until} as realistic time blocks: hard events "
               "(calendar/reminders) stay fixed; hardest work in the next fresh slot; 5-10 min breaks; meals; nothing "
               "overlaps. JSON: {\"blocks\": [{\"start\": \"HH:MM\", \"end\": \"HH:MM\", \"task\": \"...\", \"type\": "
               "\"focus|meeting|break|meal|personal|admin\"}], \"tip\": \"one line\"}\n\n"
               f"Tasks the user wants to do: {tasks or '(use goals and reminders)'}\nKnown: {' | '.join(ctxt) or 'nothing else'}", 2500)
    try:
        p = _json(raw)
        blocks = p["blocks"]
    except Exception:
        _set("planning", "idle", "Couldn't build the plan")
        return "FAILED: I couldn't build a plan - tell me your tasks."
    col = {"focus": "#22d3ee", "meeting": "#f43f5e", "break": "#3ddc84", "meal": "#f59e0b", "personal": "#a78bfa", "admin": "#94a3b8"}
    rows = "".join(f"<div class='b' style='border-color:{col.get(b.get('type'), '#94a3b8')}'><b>{html.escape(b['start'])}–{html.escape(b['end'])}</b>"
                   f" &nbsp; {html.escape(b['task'])} <span class='tag'>{html.escape(b.get('type', ''))}</span></div>" for b in blocks)
    _show("🗓 Today's plan", f"<!doctype html><html><head><meta charset='utf-8'><style>{PAGE_CSS}</style></head><body>"
          f"<h1>🗓 Your plan · {now:%A %d %b}</h1><div class='sub'>Planning agent · {html.escape(p.get('tip', ''))}</div>{rows}</body></html>")
    _set("planning", "done", f"{len(blocks)} blocks until {until}")
    first = blocks[0]
    return (f"OK: {len(blocks)} time blocks planned until {until}. First: {first['start']} {first['task']}. "
            f"The full plan is on the screen. Tip: {p.get('tip', '')}")


# ================================================================== 7. MULTI-AGENT COUNCIL
ROLES = [("Visionary", "#22d3ee", "bold, opportunity-focused, thinks 5 years ahead"),
         ("Expert", "#a78bfa", "practical, evidence-based, knows the Indian market and real constraints"),
         ("Critic", "#f43f5e", "sceptical, finds risks, hidden costs and flaws")]


def council(question: str) -> str:
    _bg(_council_run, question)
    return ("OK: the Agent Council is meeting - 3 agents on 3 different AI brains are debating. The verdict will appear "
            "on the screen in about half a minute; I'll tell you when it's ready.")


def _council_run(question):
    models = _panel(3)
    _set("council", "working", "Round 1: three agents answer independently")
    with cf.ThreadPoolExecutor(3) as ex:
        r1 = list(ex.map(lambda i: _llm(f"You are the {ROLES[i][0]} on an AI council ({ROLES[i][2]}). Answer in under 140 "
                                        f"words with your clear position and 3 reasons.\n\nQuestion: {question}", 900, models[i]), range(3)))
    for i, r in enumerate(r1):
        hooks["publish"]({"type": "agent", "id": "council", "status": "working", "text": f"{ROLES[i][0]}: {r[:150]}"})
    _set("council", "working", "Round 2: they challenge each other")
    others = lambda i: "\n\n".join(f"{ROLES[j][0]}: {r1[j]}" for j in range(3) if j != i)
    with cf.ThreadPoolExecutor(3) as ex:
        r2 = list(ex.map(lambda i: _llm(f"You are the {ROLES[i][0]}. Here is what the others said:\n{others(i)}\n\nIn under 90 "
                                        f"words: where are they wrong or right, and has your view changed?", 700, models[i]), range(3)))
    _set("council", "working", "The judge weighs the debate")
    verdict = _llm("You are the impartial judge of an AI council. Read the debate and give: VERDICT (one clear decision), "
                   "WHY (3 bullets), RISKS TO WATCH (2 bullets), FIRST STEP (one action), CONFIDENCE (low/medium/high). "
                   f"Under 180 words.\n\nQuestion: {question}\n\n" +
                   "\n\n".join(f"{ROLES[i][0]} round 1: {r1[i]}\n{ROLES[i][0]} round 2: {r2[i]}" for i in range(3)), 1500)
    bub = lambda i, t, rnd: (f"<div class='b' style='border-color:{ROLES[i][1]}'><b style='color:{ROLES[i][1]}'>{ROLES[i][0]}</b> "
                             f"<small style='color:#8b97b5'>· round {rnd} · {html.escape(str(models[i] or 'best model'))}</small>"
                             f"<div>{html.escape(t).replace(chr(10), '<br>')}</div></div>")
    page = (f"<!doctype html><html><head><meta charset='utf-8'><style>{PAGE_CSS}</style></head><body><h1>👥 {html.escape(question)}</h1>"
            f"<div class='sub'>Agent council · 3 agents · 3 AI brains · 2 rounds + judge</div>"
            f"<div class='card' style='border-color:#3ddc84'><h2>⚖️ Verdict</h2>{html.escape(verdict).replace(chr(10), '<br>')}</div>"
            f"<div class='card'><h2>Round 1</h2>{''.join(bub(i, r1[i], 1) for i in range(3))}</div>"
            f"<div class='card'><h2>Round 2 - rebuttals</h2>{''.join(bub(i, r2[i], 2) for i in range(3))}</div></body></html>")
    _show(f"👥 Council: {question[:36]}", page)
    first = re.sub(r"\s+", " ", verdict)[:260]
    _set("council", "done", "Verdict: " + first[:160])
    hooks["notify"]("The council has decided, Sir. " + first.split("WHY")[0][:220])


# ================================================================== 8. MANAGER (hierarchical)
WORKERS = {"Researcher": "finds current, real facts (with the web)", "Strategist": "plans, priorities, positioning",
           "Writer": "clear persuasive writing", "Analyst": "numbers, costs, risks, comparisons",
           "Designer": "visual and UX ideas", "Engineer": "technical design and code"}


def delegate(task: str) -> str:
    _bg(_delegate_run, task)
    return ("OK: the Manager agent is splitting the job and delegating it to specialist workers in parallel - the "
            "merged result will be on the screen shortly; I'll tell you when it's done.")


def _delegate_run(task):
    import tools
    _set("manager", "working", "Breaking the job into parts")
    raw = _llm(f"You are a manager agent. Split this job into 3-5 parallel subtasks, each for one worker from: "
               f"{', '.join(f'{k} ({v})' for k, v in WORKERS.items())}. JSON: {{\"subtasks\": [{{\"worker\": \"...\", "
               f"\"task\": \"...\", \"needs_web\": true}}]}}\n\nJob: {task}", 1200)
    try:
        subs = _json(raw)["subtasks"][:5]
    except Exception:
        subs = [{"worker": "Researcher", "task": task, "needs_web": True}, {"worker": "Writer", "task": task, "needs_web": False}]
    models = _panel(3)

    def work(i_s):
        i, s = i_s
        _set("manager", "working", f"→ {s['worker']}: {s['task'][:90]}", to=s["worker"])
        facts = ""
        if s.get("needs_web") and tools.FUNCS.get("web_lookup"):
            try:
                facts = tools.FUNCS["web_lookup"](question=s["task"])[:2500]
            except Exception:
                facts = ""
        out = _llm(f"You are the {s['worker']} ({WORKERS.get(s['worker'], 'specialist')}) on a team. Do this part of the job "
                   f"well and concretely (under 220 words, use headings/bullets).\nWhole job: {task}\nYour part: {s['task']}"
                   + (f"\nFresh facts from the web:\n{facts}" if facts else ""), 1800, models[i % len(models)])
        _set("manager", "working", f"✓ {s['worker']} delivered", to=s["worker"])
        return s, out

    with cf.ThreadPoolExecutor(5) as ex:
        results = list(ex.map(work, enumerate(subs)))
    _set("manager", "working", "Merging the team's work")
    merged = _llm("You are the manager. Merge your team's work into one excellent final deliverable for the user: an "
                  "executive summary first, then sections, then a clear next-steps list. Remove repetition, fix conflicts. "
                  f"Markdown.\n\nJob: {task}\n\n" + "\n\n".join(f"## {s['worker']}: {s['task']}\n{o}" for s, o in results), 4000)
    team = "".join(f"<span class='tag'>{html.escape(s['worker'])}: {html.escape(s['task'][:70])}</span>" for s, _o in results)
    body = html.escape(merged)
    body = re.sub(r"^### (.*)$", r"<h3>\1</h3>", body, flags=re.M)
    body = re.sub(r"^## (.*)$", r"<h2>\1</h2>", body, flags=re.M)
    body = re.sub(r"^# (.*)$", r"<h1>\1</h1>", body, flags=re.M)
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body).replace("\n", "<br>")
    _show(f"🏛 {task[:40]}", f"<!doctype html><html><head><meta charset='utf-8'><style>{PAGE_CSS}</style></head><body>"
          f"<h1>🏛 {html.escape(task)}</h1><div class='sub'>Manager agent · {len(results)} workers in parallel</div>"
          f"<div class='card'><h2>The team</h2>{team}</div><div class='card'>{body}</div></body></html>")
    _set("manager", "done", f"Delivered: {task[:120]}")
    hooks["notify"](f"Your team has finished, Sir: {task[:80]}. It's on the screen.")


# ================================================================== 🎬 AD STUDIO (all agents together)
HOOK_STYLES = [("Problem → solution", "open on the viewer's painful problem, agitate it, reveal the product as relief"),
               ("Story", "a tiny relatable story with a twist in the first 2 seconds"),
               ("Bold claim / curiosity", "a surprising claim or question that makes scrolling impossible")]
SCORE = [("Hook strength", 30), ("Clarity", 20), ("Emotion", 20), ("Call to action", 15), ("Platform fit", 15)]


def ad_script(product: str, audience: str = "", platform: str = "Instagram Reels", seconds: int = 30,
              language: str = "English", tone: str = "") -> str:
    _bg(_ad_run, product, audience, platform, int(seconds or 30), language, tone)
    return (f"OK: 🎬 Ad Studio started for '{product}' - research agent → 3 copywriters on 3 AI brains → critic → utility "
            f"pick → polish → storyboard with real photos → posting plan. Watch it live on the Agents page; I'll tell "
            f"you when the script is ready (about a minute).")


def _ad_run(product, audience, platform, seconds, language, tone):
    import images
    import tools
    brief = f"Product: {product}\nAudience: {audience or 'infer the best one'}\nPlatform: {platform}\nLength: {seconds} s\n" \
            f"Language: {language}\nTone: {tone or 'best for the audience'}"
    _set("studio", "working", "Manager: planning the ad campaign")
    _set("manager", "working", f"Ad Studio: {product}")
    # 1) research
    _set("manager", "working", "→ Researcher: audience & market insight", to="Researcher")
    facts = ""
    if tools.FUNCS.get("web_lookup"):
        try:
            facts = tools.FUNCS["web_lookup"](question=f"{product} customers pain points what they want reviews {audience}")[:2500]
        except Exception:
            pass
    insight = _llm("You are an ad researcher. Give a tight insight brief: the audience in one line, their 3 biggest pains, "
                   "3 desires, the single most powerful benefit, 3 angle ideas, and clichés competitors overuse (avoid). "
                   f"Under 200 words.\n\n{brief}\n\nWeb facts:\n{facts or '(none)'}", 1500)
    _set("studio", "working", "Research done - 3 copywriters start writing")
    # 2) three copywriters on three brains (multi-agent)
    models = _panel(3)
    n_scenes = max(3, min(8, seconds // 5))

    def write(i):
        name, how = HOOK_STYLES[i]
        _set("council", "working", f"Copywriter {i + 1} ({name}) writing on {models[i] or 'best model'}")
        raw = _llm(f"You are a top {platform} ad copywriter. Write ONE {seconds}-second ad script in {language}, hook style: "
                   f"{name} - {how}. Exactly {n_scenes} scenes, timed to add up to {seconds} s. Speakable voice-over, "
                   "punchy on-screen text, specific visuals a phone can shoot. JSON: {\"title\": \"...\", \"hook\": "
                   "\"first line\", \"scenes\": [{\"time\": \"0-3s\", \"visual\": \"...\", \"voiceover\": \"...\", "
                   "\"on_screen\": \"...\", \"photo_search\": \"2-4 words for a stock photo of this scene\"}], \"cta\": "
                   "\"...\", \"caption\": \"...\", \"hashtags\": [\"#...\"]}\n"
                   "HONESTY RULES: never invent links, phone numbers, prices, statistics, awards, certifications or "
                   "guarantees (no '100%', 'No.1', 'guaranteed') - write [your link] / [your number] placeholders and "
                   "claim only what the brief says.\n\n"
                   f"{brief}\n\nResearch brief:\n{insight}", 3000, models[i])
        v = _json(raw)
        v["style"], v["model"] = name, str(models[i] or "best model")
        return v

    variants = []
    with cf.ThreadPoolExecutor(3) as ex:
        for fut in cf.as_completed([ex.submit(write, i) for i in range(3)]):
            try:
                variants.append(fut.result())
            except Exception as e:
                log.info("copywriter failed: %s", str(e)[:100])
    if not variants:
        _set("studio", "idle", "The copywriters failed - try again")
        hooks["notify"]("Sorry Sir, the Ad Studio couldn't write the script this time.")
        return
    _set("council", "done", f"{len(variants)} scripts written")
    # 3) critic scores every variant
    _set("studio", "working", "Critic agent reviewing every script")
    raw = _llm("You are a ruthless ad critic. Score each script 1-10 on: " + ", ".join(c for c, _w in SCORE) +
               ". Give one concrete fix per script. Any invented link, number, statistic or guarantee = Clarity 3 and "
               "the fix must remove it. JSON: {\"reviews\": [{\"index\": 0, \"scores\": {\"Hook strength\": 8}, "
               "\"fix\": \"...\"}]}\n\n" + "\n\n".join(f"SCRIPT {i}: {json.dumps(v, ensure_ascii=False)[:2500]}" for i, v in enumerate(variants)), 2000)
    try:
        reviews = {int(r["index"]): r for r in _json(raw)["reviews"]}
    except Exception:
        reviews = {}
    # 4) utility agent picks the winner by weighted maths
    _set("utility", "working", "Weighing the scripts (utility = Σ weight × score)")
    for i, v in enumerate(variants):
        sc = (reviews.get(i) or {}).get("scores", {})
        v["scores"] = {c: float(sc.get(c, 6)) for c, _w in SCORE}
        v["utility"] = sum(v["scores"][c] * w for c, w in SCORE) / 10
        v["fix"] = (reviews.get(i) or {}).get("fix", "")
    variants.sort(key=lambda v: -v["utility"])
    win = variants[0]
    _set("utility", "done", f"Winner: '{win.get('title', '')}' ({win['utility']:.0f}/100)")
    # 5) polish the winner with the critic's fix
    _set("studio", "working", "Writer polishing the winning script")
    try:
        pol = _json(_llm("Improve this ad script using the critic's fix, keep the same JSON shape, same timing, sharper words. "
                         "Remove any invented link, number, statistic or guarantee (use [your link]). "
                         f"Fix: {win['fix']}\n\nScript: {json.dumps({k: win[k] for k in ('title', 'hook', 'scenes', 'cta', 'caption', 'hashtags') if k in win}, ensure_ascii=False)}", 3000))
        if pol.get("scenes"):
            win.update({k: pol[k] for k in ('title', 'hook', 'scenes', 'cta', 'caption', 'hashtags') if k in pol})
    except Exception:
        pass
    # 6) mood board: real photos for the scenes
    _set("studio", "working", "Finding real photos for the storyboard")
    for s in win.get("scenes", [])[:8]:
        s["photo"] = None
        try:
            q = s.get("photo_search") or product
            words = re.findall(r"[a-z]{4,}", q.lower())
            for ph in images.find(q, 4, 640):           # only a photo that is really of this scene
                if words and any(w in (ph.get("title") or "").lower() for w in words):
                    s["photo"] = ph
                    break
        except Exception:
            pass
    # 7) planner: posting plan + A/B test of the hooks
    _set("planning", "working", "Planning when to post and how to A/B test")
    plan = _llm(f"You are a social media planner in India. For a {platform} ad, give a 7-day posting & A/B test plan: best "
                f"days/times (IST), which hook to test against which, budget split if boosted, and 3 metrics to watch. "
                f"Under 140 words, bullets.\nHooks: " + " | ".join(v.get('hook', '') for v in variants), 900)
    _set("planning", "done", "Posting plan ready")
    # 8) learning: remember the brief for next time
    try:
        import vault
        vault.remember_note(f"Made a {platform} ad for {product} ({audience or 'auto audience'}); winning hook style: {win['style']}", "Projects/Ads")
    except Exception:
        pass
    _set("learning", "done", f"Remembered: {win['style']} hooks won for {product[:40]}")
    page = _ad_page(product, platform, seconds, language, insight, win, variants, plan)
    _show(f"🎬 Ad: {product[:40]}", page)
    with _lock:
        d = _load()
        d["last_ad"] = {"product": product, "voiceover": " ".join(s.get("voiceover", "") for s in win.get("scenes", [])) + " " + win.get("cta", "")}
        _save(d)
    _set("studio", "done", f"Ad ready: '{win.get('title', '')}' - {win['utility']:.0f}/100")
    _set("manager", "done", f"Ad Studio delivered: {product[:80]}")
    hooks["notify"](f"Your ad script is ready, Sir: '{win.get('title', '')}'. The winning hook: {win.get('hook', '')}")


def _ad_page(product, platform, seconds, language, insight, win, variants, plan):
    e = html.escape
    scenes = "".join(
        f"<tr><td><b>{e(s.get('time', ''))}</b></td><td>"
        + (f"<img src='{e(s['photo']['url'])}' style='width:150px;height:100px;object-fit:cover;border-radius:10px;display:block'>"
           f"<small style='color:#8b97b5'>{e(s['photo'].get('credit', '')[:40])}</small><br>" if s.get("photo") else "")
        + f"{e(s.get('visual', ''))}</td><td>🎙 {e(s.get('voiceover', ''))}</td><td><b>{e(s.get('on_screen', ''))}</b></td></tr>"
        for s in win.get("scenes", []))
    bars = "".join(
        f"<tr><td class='{'win' if v is win else ''}'>{'🏆 ' if v is win else ''}{e(v.get('title', ''))}<br><small style='color:#8b97b5'>"
        f"{e(v['style'])} · {e(v['model'])}</small></td>" + "".join(f"<td>{v['scores'][c]:g}</td>" for c, _w in SCORE)
        + f"<td><b>{v['utility']:.0f}</b><div class='bar' style='width:{v['utility']:.0f}%'></div></td></tr>" for v in variants)
    alts = "".join(f"<div class='b' style='border-color:#a78bfa'><b>{e(v.get('title', ''))}</b> ({e(v['style'])}) - hook: "
                   f"“{e(v.get('hook', ''))}”<br><small>Critic: {e(v.get('fix', ''))}</small></div>" for v in variants[1:])
    tags = " ".join(win.get("hashtags", []))
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>{PAGE_CSS}
.hook{{font:700 24px 'Segoe UI';background:linear-gradient(90deg,#22d3ee,#a78bfa,#f43f5e);-webkit-background-clip:text;color:transparent;margin:6px 0 14px}}
button{{border:0;border-radius:10px;padding:8px 14px;background:#22d3ee;color:#04121a;font-weight:700;cursor:pointer;margin-right:6px}}
.flow{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px}} .flow span{{padding:5px 11px;border-radius:99px;background:rgba(34,211,238,.12);font-size:12.5px}}</style></head><body>
<h1>🎬 {e(win.get('title', product))}</h1><div class='sub'>{e(platform)} · {seconds}s · {e(language)} · made by Ultron's 8-agent Ad Studio</div>
<div class='flow'><span>🏛 Manager</span><span>🔎 Researcher</span><span>👥 3 copywriters · 3 AI brains</span><span>🧐 Critic</span><span>⚖️ Utility pick</span><span>✍️ Polish</span><span>🖼 Real photos</span><span>🗓 Posting plan</span><span>📚 Remembered</span></div>
<div class='card'><h2>The hook (first 2 seconds)</h2><div class='hook'>“{e(win.get('hook', ''))}”</div>
<button onclick="parent.postMessage({{ultron:'speak_ad'}},'*')">▶ Hear the voice-over</button>
<button onclick="navigator.clipboard.writeText(document.getElementById('cap').innerText)">Copy caption</button></div>
<div class='card'><h2>Storyboard</h2><table><tr><th>Time</th><th>Visual</th><th>Voice-over</th><th>On screen</th></tr>{scenes}</table>
<div class='b' style='border-color:#3ddc84'><b>Call to action:</b> {e(win.get('cta', ''))}</div></div>
<div class='card'><h2>Caption & hashtags</h2><div id='cap'>{e(win.get('caption', ''))}<br><br>{e(tags)}</div></div>
<div class='card'><h2>Why this one won - utility = Σ weight × critic score</h2><table><tr><th>Script</th>{''.join(f'<th>{e(c)}<br><small>{w}%</small></th>' for c, w in SCORE)}<th>Utility</th></tr>{bars}</table></div>
<div class='card'><h2>Other versions (for A/B tests)</h2>{alts or '<i>-</i>'}</div>
<div class='card'><h2>7-day posting plan</h2>{e(plan).replace(chr(10), '<br>')}</div>
<div class='card'><h2>Research brief</h2>{e(insight).replace(chr(10), '<br>')}</div>
<p style='color:#8b97b5;font-size:12px'>Photos: Wikimedia Commons (free licences) - replace with your own footage when shooting.</p></body></html>"""


def speak_ad():
    with _lock:
        ad = _load().get("last_ad")
    if not ad:
        return "FAILED: no ad yet."
    hooks["say"](ad["voiceover"])
    return "OK: playing the voice-over."


# ================================================================== the loop + status for the page
def start():
    def loop():
        time.sleep(20)
        while True:
            try:
                sens = _sensors()
                _world_tick(sens)
                _reflex_tick(sens)
                if datetime.datetime.now().hour >= 9:
                    _goal_nudges()
            except Exception:
                log.exception("agents loop")
            time.sleep(15)
    threading.Thread(target=loop, daemon=True, name="agents8").start()


def overview():
    import lessons
    with _lock:
        d = _load()
    try:
        n_lessons = len(lessons._all())
    except Exception:
        n_lessons = 0
    facts = {"reflex": f"{len(d.get('rules', []))} rules armed",
             "model": ("in a meeting - holding alerts" if WORLD["meeting"] or WORLD["presenting"] else
                       f"{'away' if WORLD['away'] else 'here'} · {WORLD['app'][:30] or 'desktop'}"),
             "goal": f"{len(d.get('goals', []))} goals" + (f" · {_pct(d['goals'][-1])}% on the latest" if d.get("goals") else ""),
             "utility": "decides with maths", "learning": f"{n_lessons} lessons learned",
             "planning": "plans your day", "council": "3 brains · debate + judge", "manager": "delegates in parallel"}
    return {"agents": [dict(a, **_status[a["id"]], fact=facts.get(a["id"], "")) for a in AGENTS],
            "studio": _status["studio"], "log": d.get("log", [])[-40:], "rules": d.get("rules", []),
            "goals": [dict(g, pct=_pct(g)) for g in d.get("goals", [])], "world": {k: v for k, v in WORLD.items() if k != "held"}}


def ai_agents() -> str:
    if hooks.get("open"):
        hooks["open"]()
    return ("OK: the AI Agents page is open - 8 agent systems: reflex, world-model, goal, utility, learning, planning, "
            "multi-agent council and hierarchical manager, plus the Ad Studio that uses them all.")


def handle(op, data):
    t = str(data.get("text", ""))[:2000]
    if op == "rule":
        return reflex_rule("add", t)
    if op == "rule_del":
        return reflex_rule("delete", id=int(data.get("id") or 0))
    if op == "world":
        return world_model()
    if op == "goal":
        return goal("add", t)
    if op == "goal_done":
        return goal("done", id=int(data.get("id") or 0))
    if op == "decide":
        return decide(t)
    if op == "lesson":
        return lesson(t)
    if op == "plan":
        return plan_day(t)
    if op == "council":
        return council(t)
    if op == "delegate":
        return delegate(t)
    if op == "ad":
        return ad_script(t, data.get("audience", ""), data.get("platform", "Instagram Reels"), int(data.get("seconds") or 30),
                         data.get("language", "English"), data.get("tone", ""))
    if op == "speak_ad":
        return speak_ad()
    return "FAILED: unknown op"
