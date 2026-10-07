"""Your day timeline: a private, on-PC record of which apps / sites were in front and for how long,
so "what did I do today?" gets a real answer with a visual timeline, focus blocks and totals.

Stored only on this PC in activity/YYYY-MM-DD.json (never uploaded, never committed), kept 30 days.
Windows that look private (passwords, banking, payments, incognito) are recorded by app name only.
Tracking can be paused any time ("stop tracking my day").
"""

import ctypes
import ctypes.wintypes as wt
import datetime
import html
import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.daylog")
DIR = Path(__file__).resolve().parent / "activity"
SAMPLE = 5            # seconds between samples
AWAY_AFTER = 120      # no keyboard / mouse for this long = away
PRIVATE = re.compile(r"password|bank|upi|payment|checkout|incognito|inprivate|otp|wallet|login|sign ?in", re.I)
BROWSERS = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave", "opera": "Opera"}
_state = {"on": True}
_lock = threading.Lock()


class _LASTINPUT(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds():
    li = _LASTINPUT(ctypes.sizeof(_LASTINPUT), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return max(0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)


def _front():
    """(app name, title) of the foreground window."""
    import psutil
    u32 = ctypes.windll.user32
    hwnd = u32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(300)
    u32.GetWindowTextW(hwnd, buf, 300)
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name().rsplit(".", 1)[0]
    except Exception:
        exe = "unknown"
    title = buf.value.strip()
    low = exe.lower()
    if low in BROWSERS:
        site = re.sub(r"\s*[-—|]\s*(Google Chrome|Microsoft​? Edge|Mozilla Firefox|Brave|Opera)\s*$", "", title)
        app = BROWSERS[low]
        title = site
    elif low in ("explorer", "applicationframehost", "searchhost", "lockapp", "shellexperiencehost"):
        app = "Windows" if low != "lockapp" else "Locked"
    else:
        app = {"code": "VS Code", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "whatsapp": "WhatsApp",
               "spotify": "Spotify", "pythonw": "Atomo", "python": "Python", "windowsterminal": "Terminal",
               "claude": "Claude", "teams": "Teams", "zoom": "Zoom", "vlc": "VLC"}.get(low, exe.capitalize())
    if PRIVATE.search(title):
        title = ""
    if "A.T.O.M.O" in title:
        app, title = "Atomo", "Command center"
    return app, title[:90]


def _path(day):
    return DIR / f"{day:%Y-%m-%d}.json"


def _load(day):
    try:
        return json.loads(_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"apps": {}, "segments": []}


def _loop():
    DIR.mkdir(exist_ok=True)
    for old in DIR.glob("*.json"):                     # keep 30 days
        try:
            if (datetime.date.today() - datetime.date.fromisoformat(old.stem)).days > 30:
                old.unlink()
        except ValueError:
            pass
    day, data, dirty, last_save = datetime.date.today(), None, False, time.time()
    data = _load(day)
    while True:
        time.sleep(SAMPLE)
        if not _state["on"]:
            continue
        try:
            now = time.time()
            if datetime.date.today() != day:
                _path(day).write_text(json.dumps(data), encoding="utf-8")
                day, data = datetime.date.today(), _load(datetime.date.today())
            if idle_seconds() > AWAY_AFTER:
                app, title = "Away", ""
            else:
                app, title = _front()
            with _lock:
                if app != "Away":
                    data["apps"][app] = data["apps"].get(app, 0) + SAMPLE
                segs = data["segments"]
                if segs and segs[-1][2] == app and now - segs[-1][1] < SAMPLE * 3:
                    segs[-1][1] = now
                    if title and title not in segs[-1][3]:
                        segs[-1][3] = (segs[-1][3] + " · " + title)[-200:] if segs[-1][3] else title
                else:
                    segs.append([now - SAMPLE, now, app, title])
                dirty = True
            if dirty and now - last_save > 60:
                with _lock:
                    _path(day).write_text(json.dumps(data), encoding="utf-8")
                dirty, last_save = False, now
        except Exception:
            log.exception("activity sample failed")
            time.sleep(30)


def start():
    threading.Thread(target=_loop, name="daylog", daemon=True).start()


def set_tracking(on: bool) -> str:
    _state["on"] = bool(on)
    return "OK: day tracking " + ("resumed." if on else "paused - nothing is recorded until you turn it back on.")


# ------------------------------------------------------------------ the report
COLORS = ["#7fe6ff", "#f4ba42", "#ff5a63", "#9b7bff", "#3ee08b", "#ff9f43", "#5ea8ff", "#ff6fd8", "#c0f25c", "#8aa0c8"]


def _fmt(sec):
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def my_day(day: str = "today") -> str:
    import tools
    d = datetime.date.today()
    if day and day.lower() == "yesterday":
        d -= datetime.timedelta(days=1)
    elif day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.strip()):
        d = datetime.date.fromisoformat(day.strip())
    if d == datetime.date.today():
        with _lock:
            data = json.loads(json.dumps(_load(d)))
            try:
                _path(d).write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass
    data = _load(d)
    apps = sorted(((a, s) for a, s in data["apps"].items() if a not in ("Away", "Locked")), key=lambda x: -x[1])
    segs = [s for s in data["segments"] if s[2] not in ("Away", "Locked")]
    if not apps:
        return f"FAILED: nothing was recorded for {d:%A %d %B} (tracking started today, or it was paused)."
    total = sum(s for _, s in apps)
    first, last = segs[0][0], segs[-1][1]
    # focus blocks: the same app for 20+ minutes with only short interruptions
    blocks, cur = [], None
    for st, en, app, title in segs:
        if cur and app == cur[2] and st - cur[1] < 180:
            cur[1] = en
        else:
            if cur and cur[1] - cur[0] >= 1200:
                blocks.append(cur)
            cur = [st, en, app]
    if cur and cur[1] - cur[0] >= 1200:
        blocks.append(cur)
    color = {a: COLORS[i % len(COLORS)] for i, (a, _) in enumerate(apps)}
    t = lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")
    span = max(1, last - first)
    bars = "".join(f'<div class="row"><span class="nm">{html.escape(a)}</span><div class="bar"><i style="width:{s / apps[0][1] * 100:.1f}%;background:{color[a]}"></i></div><b>{_fmt(s)}</b></div>' for a, s in apps[:10])
    strip = "".join(f'<i title="{html.escape(app)} {t(st)}–{t(en)}{(" · " + html.escape(title)) if title else ""}" style="left:{(st - first) / span * 100:.2f}%;width:{max(0.15, (en - st) / span * 100):.2f}%;background:{color.get(app, "#555")}"></i>' for st, en, app, title in segs)
    sites = {}
    for st, en, app, title in segs:
        if app in BROWSERS.values() and title:
            site = title.split(" · ")[-1][:50]
            sites[site] = sites.get(site, 0) + (en - st)
    top_sites = sorted(sites.items(), key=lambda x: -x[1])[:8]
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Your day</title><style>
body{{margin:0;background:#05070f;color:#eef4ff;font:15px "Segoe UI",system-ui,sans-serif;padding:28px}}
h1{{font:600 26px Bahnschrift,"Segoe UI";letter-spacing:3px;margin:0 0 4px;color:#7fe6ff}} .sub{{color:#8b97b5;margin-bottom:22px}}
.kp{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}} .k{{background:#0d1222;border:1px solid #1d2742;border-radius:16px;padding:14px 18px;min-width:150px}}
.k b{{display:block;font:600 24px Bahnschrift;color:#f4ba42}} .k span{{color:#8b97b5;font-size:12px;letter-spacing:1px}}
h2{{font:600 12px Bahnschrift;letter-spacing:4px;color:#7fe6ff;margin:24px 0 10px}}
.strip{{position:relative;height:38px;background:#0d1222;border-radius:10px;overflow:hidden;border:1px solid #1d2742}} .strip i{{position:absolute;top:0;bottom:0}}
.ax{{display:flex;justify-content:space-between;color:#8b97b5;font-size:11px;margin-top:4px}}
.row{{display:flex;align-items:center;gap:12px;margin:7px 0}} .nm{{width:130px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bar{{flex:1;height:12px;background:#0d1222;border-radius:6px;overflow:hidden}} .bar i{{display:block;height:100%;border-radius:6px}} .row b{{width:80px;text-align:right;color:#cfd8f0}}
li{{margin:5px 0}} .muted{{color:#8b97b5}}</style></head><body>
<h1>YOUR DAY</h1><div class="sub">{d:%A, %d %B %Y} · recorded privately on this PC</div>
<div class="kp"><div class="k"><b>{_fmt(total)}</b><span>ACTIVE ON PC</span></div><div class="k"><b>{t(first)}</b><span>FIRST ACTIVITY</span></div>
<div class="k"><b>{t(last)}</b><span>LAST ACTIVITY</span></div><div class="k"><b>{len(blocks)}</b><span>FOCUS BLOCKS (20+ MIN)</span></div>
<div class="k"><b>{html.escape(apps[0][0])}</b><span>TOP APP</span></div></div>
<h2>TIMELINE</h2><div class="strip">{strip}</div><div class="ax"><span>{t(first)}</span><span>{t(first + span / 2)}</span><span>{t(last)}</span></div>
<h2>WHERE THE TIME WENT</h2>{bars}
<h2>FOCUS BLOCKS</h2><ul>{"".join(f"<li><b>{html.escape(b[2])}</b> — {t(b[0])} to {t(b[1])} <span class='muted'>({_fmt(b[1] - b[0])})</span></li>" for b in blocks) or "<li class='muted'>No uninterrupted 20-minute stretch yet.</li>"}</ul>
<h2>TOP SITES</h2><ul>{"".join(f"<li>{html.escape(s)} <span class='muted'>{_fmt(v)}</span></li>" for s, v in top_sites) or "<li class='muted'>No browsing recorded.</li>"}</ul>
</body></html>"""
    tools.show_content("chart", f"Your day · {d:%d %b}", "html", page)
    summary = (f"{_fmt(total)} active between {t(first)} and {t(last)}; top apps: "
               + ", ".join(f"{a} {_fmt(s)}" for a, s in apps[:5])
               + f"; {len(blocks)} focus blocks" + (f" (longest: {max(blocks, key=lambda b: b[1] - b[0])[2]} {_fmt(max(b[1] - b[0] for b in blocks))})" if blocks else "")
               + (f"; top sites: {', '.join(s for s, _ in top_sites[:3])}" if top_sites else ""))
    return f"OK: the day report for {d:%A %d %B} is on the Atomo Screen. Tell the user the highlights in 2-3 sentences: {summary}"
