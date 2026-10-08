"""Departments - Ultron's command layer (inspired by Forgemind's ForgeCommand).

"Architect my departments": Ultron reads what it knows about you (memory vault, interview answers, your
description) and DESIGNS an agent organisation for your life/business - e.g. Growth, Socials, Study,
Finance, Tasks - each with a mission, KPIs, its own memory, its own skills and suggested routines.
Then "Ask the Growth department to plan this week's posts" runs that department's agent. Routines are
only scheduled when you say "activate my departments".
"""

import datetime
import html
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("jarvis.departments")


def _dir():
    import vault
    vault.init()
    d = vault.ROOT / "Departments"
    d.mkdir(exist_ok=True)
    return d


def _load():
    out = {}
    for p in sorted(_dir().glob("*.json")):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return out


def _safe(name):
    return re.sub(r"[^\w -]+", "", name).strip()[:40] or "Department"


def _org_page(depts):
    cards = "".join(
        f"""<div class="c"><h2>{html.escape(d.get('icon', '🏢'))} {html.escape(d['name'])}</h2><p>{html.escape(d.get('mission', ''))}</p>
        <h3>RESPONSIBILITIES</h3><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in d.get('responsibilities', [])[:5])}</ul>
        <h3>KPIs</h3><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in d.get('kpis', [])[:4])}</ul>
        <h3>ROUTINES</h3><ul>{''.join(f"<li><b>{html.escape(r.get('when', ''))}</b> — {html.escape(r.get('task', ''))}</li>" for r in d.get('routines', [])[:4])}</ul></div>"""
        for d in depts.values())
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:radial-gradient(ellipse at top,#13203a,#04060e 70%);color:#eef4ff;font:14px "Segoe UI",system-ui;padding:28px}}
.hd{{text-align:center;margin-bottom:26px}}.hd b{{display:inline-block;padding:14px 26px;border-radius:18px;border:1px solid #ff5a63;
font:600 18px Bahnschrift;letter-spacing:6px;color:#ff5a63;box-shadow:0 0 30px rgba(255,90,99,.3)}}.hd small{{display:block;color:#8b97b5;margin-top:10px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:16px}}
.c{{background:rgba(13,18,34,.92);border:1px solid #22305a;border-radius:18px;padding:18px;position:relative}}
.c::before{{content:"";position:absolute;left:50%;top:-14px;height:14px;border-left:1px dashed #ff5a63}}
h2{{font-size:17px;margin:0 0 6px}}p{{color:#cfd8f0;margin:0 0 10px;line-height:1.4}}h3{{font:600 10px Bahnschrift;letter-spacing:3px;color:#7fe6ff;margin:12px 0 4px}}
ul{{margin:0;padding-left:18px;color:#cfd8f0;line-height:1.5}}b{{color:#f4ba42}}</style></head><body>
<div class="hd"><b>ULTRON · COMMAND LAYER</b><small>{len(depts)} departments · say “ask the &lt;name&gt; department to …”</small></div>
<div class="g">{cards}</div></body></html>"""


def architect(about: str = "", count: int = 5) -> str:
    """Design the department/agent organisation for the user's life or business."""
    import skills
    import tools
    import vault
    count = max(2, min(int(count or 5), 8))
    known = ""
    for name in ("Me.md", "Interview.md"):
        try:
            known += (vault.ROOT / name).read_text(encoding="utf-8")[:3000] + "\n"
        except OSError:
            pass
    for sub in ("Projects",):
        for p in list((vault.ROOT / sub).glob("*.md"))[:8]:
            known += p.read_text(encoding="utf-8")[:500] + "\n"
    tools.progress("Architecting your departments")
    raw = tools.generate_text(
        "You are a world-class operations architect. Design an AI agent organisation ('departments') that runs this "
        f"person's life/business through one command layer. Make exactly {count} departments that truly fit THEM "
        "(e.g. a student needs Study/Exams/Health; a creator needs Content/Growth/Socials; a business needs Sales/Ops/"
        "Finance). For each: name (1-2 words), icon (one emoji), mission (1 sentence), 3-5 responsibilities, 2-4 "
        "measurable KPIs, 1-3 routines (when = 'daily HH:MM' or 'weekly <weekday> HH:MM', task = what Ultron should do, "
        "phrased as a command), and 1-2 skills (name, when = trigger words, instructions = numbered steps).\n"
        f"WHAT THE USER SAID: {about or '(nothing extra)'}\nWHAT ULTRON KNOWS ABOUT THEM:\n{known[:6000] or '(little - keep it general)'}\n"
        'Reply ONLY JSON: [{"name":"","icon":"","mission":"","responsibilities":[],"kpis":[],"routines":[{"when":"","task":""}],'
        '"skills":[{"name":"","when":"","instructions":""}]}]', 7000) or ""
    try:
        depts = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
        assert depts
    except Exception:
        return "FAILED: couldn't design the departments this time - try again."
    d = _dir()
    for old in d.glob("*.json"):
        old.unlink()
    saved = {}
    for dep in depts[:count]:
        dep["name"] = _safe(dep.get("name", "Department"))
        (d / f"{dep['name']}.json").write_text(json.dumps(dep, indent=1, ensure_ascii=False), encoding="utf-8")
        for sk in dep.get("skills", [])[:2]:
            if sk.get("name") and sk.get("instructions"):
                skills.save_skill(f"{dep['name']}: {sk['name']}", sk["instructions"], sk.get("when", dep["name"].lower()))
        saved[dep["name"]] = dep
    tools.show_content("chart", "Command layer · departments", "html", _org_page(saved))
    return (f"OK: designed {len(saved)} departments: " + ", ".join(f"{v.get('icon', '')} {k}" for k, v in saved.items())
            + ". The org chart is on the Ultron Screen; their skills are learned. Routines are NOT scheduled yet - ask the "
              "user if they want to 'activate the departments' (that schedules the routines).")


def activate_departments() -> str:
    import everyday
    n = 0
    for name, dep in _load().items():
        for r in dep.get("routines", [])[:3]:
            m = re.search(r"(daily|weekly)?\s*(?:\w+day\s+)?(\d{1,2}:\d{2})", r.get("when", ""), re.I)
            if not m or not r.get("task"):
                continue
            rep = "weekly" if (m.group(1) or "").lower() == "weekly" else "daily"
            out = everyday.schedule_task(f"Ask the {name} department to {r['task']}", at=m.group(2), repeat=rep)
            n += out.startswith("OK")
    return f"OK: scheduled {n} department routines (see the Daily panel → Alerts). Say 'cancel all reminders' to undo."


def list_departments() -> str:
    d = _load()
    if not d:
        return "OK: no departments yet - say 'architect my departments'."
    return "OK: departments: " + "; ".join(f"{v.get('icon', '')} {k} - {v.get('mission', '')}" for k, v in d.items())


def ask_department(department: str, task: str) -> str:
    import tools
    import vault
    depts = _load()
    key = next((k for k in depts if k.lower() in (department or "").lower() or (department or "").lower() in k.lower()), None)
    if not key:
        return "FAILED: no such department. " + list_departments()
    dep = depts[key]
    mem_path = vault.ROOT / "Departments" / f"{key}.md"
    try:
        memory = mem_path.read_text(encoding="utf-8")[-2500:]
    except OSError:
        memory = ""
    try:
        import skills
        sk = skills.for_request(f"{key} {task}")
    except Exception:
        sk = ""
    tools.progress(f"{dep.get('icon', '')} {key} department working")
    out = tools.generate_text(
        f"You are the {key} department of the user's AI organisation (led by Ultron). Mission: {dep.get('mission')}. "
        f"Responsibilities: {dep.get('responsibilities')}. KPIs: {dep.get('kpis')}. Today is {datetime.date.today():%A %d %B %Y}.\n"
        f"YOUR MEMORY:\n{memory or '(new)'}\nYOUR SKILLS:\n{sk or '(none)'}\nWHAT ULTRON KNOWS ABOUT THE USER:\n"
        f"{vault.context_for(task + ' ' + key)[:2500]}\n\nTASK: {task}\n\nDo the work concretely (plans, drafts, numbers, "
        "next actions - not generic advice). Reply in two parts separated by a line '---': 1) 2-3 spoken sentences "
        "summarising what you did, no markdown; 2) the full Markdown deliverable.", 6000) or ""
    spoken, _, report = out.partition("---")
    tools.show_content("document", f"{key}: {task[:40]}", "md", f"# {dep.get('icon', '')} {key} department\n\n_{task}_\n\n{report.strip() or out}\n")
    vault._append(mem_path, f"- {datetime.date.today():%d %b}: {task[:120]} -> {spoken.strip()[:160]}")
    return f"OK: the {key} department finished; deliverable on the Ultron Screen. Say this briefly: {spoken.strip()[:500]}"
