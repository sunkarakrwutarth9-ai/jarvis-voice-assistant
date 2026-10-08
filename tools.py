"""Everything Jarvis can actually do on the PC, plus the tool schemas sent to the model."""

import ctypes
import datetime
import io
import json
import sys
import logging
import os
import platform
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote, quote_plus, urlparse
from urllib.request import Request, urlopen

import psutil

from youtube import YouTube

log = logging.getLogger("jarvis.tools")

# Set by the app: notify(text) speaks + shows a message outside a normal turn (timers).
notify = lambda text: log.info("notify: %s", text)
# Set by the app: the OpenAI-compatible client + model, used for live web lookups.
llm_client = None
llm_model = None
llm_is_openrouter = False
llm_is_gemini = False
best_models = lambda: [llm_model]       # set by Brain: models ranked fastest-first

YT = YouTube()

# --------------------------------------------------------------------------- #
# Apps, websites, search
# --------------------------------------------------------------------------- #

KNOWN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "bluetooth settings": "ms-settings:bluetooth",
    "wifi settings": "ms-settings:network-wifi",
    "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound",
    "snipping tool": "ms-screenclip:",
    "camera": "microsoft.windows.camera:",
    "store": "ms-windows-store:",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
}

START_MENU_DIRS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
    Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
]

WEBSITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "amazon": "https://www.amazon.in",
    "maps": "https://maps.google.com",
}

FOLDERS = {
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "desktop": Path.home() / "Desktop",
    "pictures": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "videos": Path.home() / "Videos",
    "home": Path.home(),
}

# Never kill these, whatever the model asks.
PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe", "python.exe",
    "pythonw.exe", "conhost.exe", "sihost.exe", "ctfmon.exe", "audiodg.exe",
}


def _find_start_menu_shortcut(name: str):
    name = name.lower()
    best = None
    for root in START_MENU_DIRS:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            stem = lnk.stem.lower()
            if "uninstall" in stem:
                continue
            if stem == name:
                return lnk
            if name in stem and (best is None or len(stem) < len(best.stem)):
                best = lnk
    return best


def open_app(name: str) -> str:
    key = name.strip().lower()
    target = KNOWN_APPS.get(key)
    if target is None:
        shortcut = _find_start_menu_shortcut(key)
        if shortcut is None:
            return f"FAILED: no installed app matching '{name}' was found."
        target = str(shortcut)
    try:
        os.startfile(target)
        return f"OK: launched {name}."
    except OSError as e:
        return f"FAILED: could not launch {name} ({e})."


def close_app(name: str) -> str:
    key = name.strip().lower().removesuffix(".exe").replace(" ", "")
    aliases = {"edge": "msedge", "vscode": "code", "visualstudiocode": "code", "word": "winword",
               "powerpoint": "powerpnt", "calculator": "calculatorapp", "whatsapp": "whatsapp.root"}
    key = aliases.get(key, key)
    me = psutil.Process().username()
    killed = 0
    for p in psutil.process_iter(["name", "username"]):
        pname = (p.info["name"] or "").lower()
        if pname in PROTECTED_PROCESSES or p.info["username"] != me:
            continue
        if pname.removesuffix(".exe").replace(" ", "") == key or (len(key) >= 4 and key in pname):
            try:
                p.terminate()
                killed += 1
            except psutil.Error:
                pass
    if killed == 0:
        return f"FAILED: no running app matching '{name}' was found."
    return f"OK: closed {name} ({killed} process{'es' if killed > 1 else ''})."


def open_website(site: str) -> str:
    site = site.strip()
    url = WEBSITES.get(site.lower())
    if url is None:
        url = site if "://" in site else "https://" + site
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "." not in (parsed.hostname or ""):
        return f"FAILED: '{site}' is not a valid website address."
    return _open_url(url)


CHROME_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
# Set by the app: show_choice(title, [(label, sublabel, value)], on_pick) pops a chooser on the island.
show_choice = None
_pending_url = ""


def chrome_profiles():
    """Chrome profiles on this PC: [{'dir', 'name', 'label'}], signed-in accounts first."""
    try:
        state = json.loads((CHROME_DATA / "Local State").read_text(encoding="utf-8"))
        cache = state["profile"]["info_cache"]
    except (OSError, KeyError, ValueError):
        return []
    out = [{"dir": d, "name": (v.get("gaia_name") or v.get("name") or d).strip(), "label": v.get("name", d)}
           for d, v in cache.items()]
    return sorted(out, key=lambda p: (p["dir"] != "Default", p["dir"]))


def _chrome_exe():
    for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Google\Chrome\Application\chrome.exe",
              Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Google\Chrome\Application\chrome.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe"):
        if p.exists():
            return str(p)
    return None


STATE_FILE = Path(__file__).resolve().parent / "state.json"


def _state(key, default=None):
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get(key, default)
    except (OSError, ValueError):
        return default


def _save_state(key, value):
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[key] = value
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _open_url(url: str) -> str:
    """Open a URL in Chrome with the account used last time (default browser only if Chrome is missing)."""
    if _chrome_exe() is None:
        webbrowser.open(url)
        return f"OK: opened {url} in the default browser."
    profile = _state("chrome_profile", "Default")
    if profile not in {p["dir"] for p in chrome_profiles()}:
        profile = "Default"
    return launch_chrome(profile, url)


def launch_chrome(profile_dir: str, url: str = "") -> str:
    exe = _chrome_exe()
    if exe is None:
        return "FAILED: Google Chrome is not installed."
    _save_state("chrome_profile", profile_dir)
    args = [exe, f"--profile-directory={profile_dir}"]
    if url:
        args.append(url if "://" in url else "https://" + url)
    subprocess.Popen(args, creationflags=subprocess.DETACHED_PROCESS)
    name = next((p["name"] for p in chrome_profiles() if p["dir"] == profile_dir), profile_dir)
    return f"OK: opened Chrome as {name}" + (f" at {url}." if url else ".")


def _match_profile(account: str, profiles):
    a = account.strip().lower()
    words = {"first": 0, "one": 0, "1": 0, "second": 1, "two": 1, "2": 1, "third": 2, "three": 2, "3": 2,
             "fourth": 3, "four": 3, "4": 3}
    for key, i in words.items():
        if re.search(rf"\b{key}\b", a) and i < len(profiles):
            return profiles[i]
    for p in profiles:
        if a and (a in p["name"].lower() or a in p["label"].lower() or p["name"].lower().split()[0] in a):
            return p
    return None


def open_browser(account: str = "", url: str = "") -> str:
    """Open Chrome in a chosen account. With several accounts and none named, ask the user first."""
    global _pending_url
    profiles = chrome_profiles()
    if not profiles:
        return launch_chrome("Default", url)
    if account:
        p = _match_profile(account, profiles)
        if p is None:
            names = ", ".join(p["name"] for p in profiles)
            return f"FAILED: no Chrome account matching '{account}'. Accounts: {names}."
        return launch_chrome(p["dir"], url)
    if len(profiles) == 1:
        return launch_chrome(profiles[0]["dir"], url)
    _pending_url = url
    if show_choice is not None:
        show_choice("Choose a Chrome account",
                    [(p["name"], p["label"] if p["label"] != p["name"] else "", p["dir"]) for p in profiles],
                    lambda d: launch_chrome(d, _pending_url))
    listing = "; ".join(f"{i + 1}) {p['name']}" for i, p in enumerate(profiles))
    return (f"WAITING: showing the account chooser on screen ({listing}). Ask the user which account, briefly, "
            f"ending with a question mark. When they answer, call open_browser again with account set.")


def web_search(query: str, engine: str = "google") -> str:
    if engine == "youtube":
        url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    else:
        url = "https://www.google.com/search?q=" + quote_plus(query)
    result = _open_url(url)
    return f"OK: showing {engine} results for '{query}'." if result.startswith("OK") else result


def open_folder(folder: str) -> str:
    path = FOLDERS.get(folder.strip().lower())
    if path is None:
        path = Path(folder).expanduser()
    if not path.exists():
        return f"FAILED: folder '{folder}' does not exist."
    os.startfile(str(path))
    return f"OK: opened {path}."


def _search_snippets(query: str, n=6) -> str:
    """Top DuckDuckGo results (title + snippet) as plain text."""
    import html as html_lib
    import re
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150 Safari/537.36"})
    with urlopen(req, timeout=10) as r:
        page = r.read().decode("utf-8", "replace")
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    clean = lambda s: html_lib.unescape(re.sub(r"<[^>]+>", "", s)).strip()
    return "\n".join(f"- {clean(t)}: {clean(s)}" for t, s in list(zip(titles, snippets))[:n])


_grounding_off_until = 0.0


def _gemini_search(prompt: str) -> str:
    """Gemini with built-in Google Search grounding (native API), trying the fastest models first."""
    global _grounding_off_until
    import httpx
    if time.monotonic() < _grounding_off_until:
        raise RuntimeError("Google Search grounding is rate-limited; skipping for now")
    key = os.environ.get("GEMINI_API_KEY", "")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 400}}
    last = None
    for model in best_models()[:3]:
        try:
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                           headers={"x-goog-api-key": key}, json=body, timeout=httpx.Timeout(15.0, connect=5.0))
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = " ".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
        except Exception as e:
            last = e
            log.warning("google search via %s failed: %s", model, str(e)[:120])
            if "429" in str(e):              # no search quota on this key: don't keep paying the wait
                _grounding_off_until = time.monotonic() + 30 * 60
                break
    raise RuntimeError(f"Google Search grounding failed ({last})")


def web_lookup(question: str) -> str:
    """Answer a question that needs live information from the web."""
    if llm_client is None:
        return "FAILED: web lookup is not configured."
    prompt = (f"Answer concisely in 2-3 plain spoken sentences with the key facts and numbers "
              f"(today is {datetime.date.today():%d %B %Y}): {question}")
    if llm_is_gemini:
        try:
            return _gemini_search(prompt)
        except Exception:
            pass                               # fall back to search snippets below
    if llm_is_openrouter:
        extra = {"plugins": [{"id": "web", "max_results": 4}]}
    else:
        results = _search_snippets(question)
        if not results:
            return "FAILED: the web search returned no results."
        prompt += f"\n\nUse only these search results; say so if they don't answer it:\n{results}"
        extra = {}
    resp = llm_client.with_options(timeout=20).chat.completions.create(
        model=best_models()[0], messages=[{"role": "user", "content": prompt}], max_tokens=400, extra_body=extra)
    return resp.choices[0].message.content or "FAILED: no answer found."


def get_weather(city: str = "") -> str:
    url = f"https://wttr.in/{quote(city.strip())}?format=j1"
    with urlopen(Request(url, headers={"User-Agent": "curl/8"}), timeout=8) as r:
        data = json.load(r)
    cur = data["current_condition"][0]
    today = data["weather"][0]
    area = data.get("nearest_area", [{}])[0]
    place = area.get("areaName", [{}])[0].get("value", city or "your area")
    return json.dumps({
        "place": place,
        "condition": cur["weatherDesc"][0]["value"],
        "temp_c": cur["temp_C"],
        "feels_like_c": cur["FeelsLikeC"],
        "humidity_percent": cur["humidity"],
        "today_max_c": today["maxtempC"],
        "today_min_c": today["mintempC"],
        "chance_of_rain_percent": max(int(h.get("chanceofrain", 0)) for h in today["hourly"]),
    })


# --------------------------------------------------------------------------- #
# YouTube (Jarvis-controlled browser) and media
# --------------------------------------------------------------------------- #

def youtube_play(query: str) -> str:
    return YT.play(query)


def youtube_control(action: str) -> str:
    return YT.control(action)


def youtube_sign_in() -> str:
    return YT.open_for_sign_in()


MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next_track": 0xB0,
    "previous_track": 0xB1,
    "stop": 0xB2,
}


def _tap_key(vk: int):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # 2 = KEYEVENTF_KEYUP


def media_key(action: str) -> str:
    vk = MEDIA_KEYS.get(action)
    if vk is None:
        return f"FAILED: unknown media action '{action}'."
    _tap_key(vk)
    return f"OK: pressed {action.replace('_', ' ')}."


def _endpoint_volume():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def set_volume(level: int = None, change: int = None, mute: bool = None) -> str:
    vol = _endpoint_volume()
    if mute is not None:
        vol.SetMute(1 if mute else 0, None)
        if level is None and change is None:
            return f"OK: sound {'muted' if mute else 'unmuted'}."
    current = round(vol.GetMasterVolumeLevelScalar() * 100)
    if level is None and change is None:
        return f"OK: volume is {current}%{' (muted)' if vol.GetMute() else ''}."
    target = level if level is not None else current + change
    target = max(0, min(100, int(target)))
    vol.SetMasterVolumeLevelScalar(target / 100, None)
    if target > 0 and vol.GetMute():
        vol.SetMute(0, None)
    return f"OK: volume set to {target}% (was {current}%)."


def set_brightness(level: int = None, change: int = None) -> str:
    ps = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        current = int(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError, subprocess.SubprocessError):
        return "FAILED: this display does not support software brightness control."
    if level is None and change is None:
        return f"OK: brightness is {current}%."
    target = max(0, min(100, int(level if level is not None else current + change)))
    cmd = ("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
           f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={target}}}")
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=10,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return f"OK: brightness set to {target}% (was {current}%)."


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #

def system_status() -> str:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    build = int(platform.version().split(".")[-1])
    status = {
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / 1e9, 1),
        "memory_total_gb": round(mem.total / 1e9, 1),
        "system_disk_free_gb": round(disk.free / 1e9, 1),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "os": "Windows 11" if build >= 22000 else f"Windows {platform.release()}",
        "top_cpu_apps": _top_processes(),
    }
    battery = psutil.sensors_battery()
    if battery is not None:
        status["battery_percent"] = round(battery.percent)
        status["plugged_in"] = battery.power_plugged
    return json.dumps(status)


def _top_processes(n=3):
    procs = [p for p in psutil.process_iter(["name", "memory_info"]) if p.info["memory_info"]]
    procs.sort(key=lambda p: p.info["memory_info"].rss, reverse=True)
    return [f"{p.info['name']} ({p.info['memory_info'].rss / 1e9:.1f} GB)" for p in procs[:n]]


def current_time() -> str:
    local = datetime.datetime.now().astimezone()
    return local.strftime("%A %d %B %Y, %I:%M:%S %p ") + str(local.tzname())


def world_time(timezone: str) -> str:
    """Exact current time in a timezone (IANA name, e.g. 'America/New_York', 'Europe/London', 'Asia/Dubai')."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        t = datetime.datetime.now(ZoneInfo(timezone.strip()))
    except (ZoneInfoNotFoundError, ValueError):
        return f"FAILED: unknown timezone '{timezone}'. Use an IANA name like 'America/New_York'."
    return f"{timezone}: {t.strftime('%A %d %B %Y, %I:%M %p %Z (UTC%z)')}"


_timers = []


def set_timer(seconds: int, label: str = "timer") -> str:
    seconds = int(seconds)
    if seconds <= 0 or seconds > 24 * 3600:
        return "FAILED: timers must be between 1 second and 24 hours."
    due = datetime.datetime.now() + datetime.timedelta(seconds=seconds)

    def fire():
        notify(f"Sir, time's up: {label}.")

    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    _timers.append(t)
    return f"OK: {label} set for {due:%I:%M:%S %p}."


def take_screenshot() -> str:
    from PIL import ImageGrab
    folder = Path.home() / "Pictures" / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot-{datetime.datetime.now():%Y%m%d-%H%M%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return f"OK: screenshot saved to {path}."


def show_desktop() -> str:
    user32 = ctypes.windll.user32
    user32.keybd_event(0x5B, 0, 0, 0)       # Win down
    user32.keybd_event(0x44, 0, 0, 0)       # D
    user32.keybd_event(0x44, 0, 2, 0)
    user32.keybd_event(0x5B, 0, 2, 0)       # Win up
    return "OK: toggled show desktop."


def lock_pc() -> str:
    if ctypes.windll.user32.LockWorkStation():
        return "OK: workstation locked."
    return "FAILED: Windows refused to lock the workstation."


def sleep_pc() -> str:
    threading.Timer(3, lambda: subprocess.run(
        ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])).start()
    return "OK: going to sleep in 3 seconds."


turn_unclear = False      # set by jarvis.py when this turn came from a recording nobody could make out
RISKY = {"power_off", "sleep_pc", "lock_pc", "close_app", "window_control"}


def power_off(action: str, seconds: int = 10) -> str:
    if action == "cancel":
        r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
        publish({"type": "power", "action": "cancel"})
        return "OK: shutdown cancelled." if r.returncode == 0 else "OK: no shutdown was scheduled."
    flag = {"shutdown": "/s", "restart": "/r"}.get(action)
    if flag is None:
        return f"FAILED: unknown power action '{action}'."
    seconds = max(5, min(int(seconds or 10), 600))
    try:
        save_chats_now()
    except Exception:
        pass
    subprocess.run(["shutdown", flag, "/t", str(seconds)], capture_output=True)
    publish({"type": "power", "action": action, "seconds": seconds, "at": time.time()})
    return f"OK: {action} in {seconds} seconds (chats saved). Say 'cancel shutdown' or press Cancel to stop it."


save_chats_now = lambda: None      # set by jarvis.py
ultron_power_hook = None
open_study_hook = None


def api_keys(action: str = "status", which: str = "") -> str:
    """Which API keys Ultron has / is missing, or open the secure dialog to add or change one."""
    if action == "status":
        rows = apikeys.status()
        have = [r["name"] for r in rows if r["set"]]
        miss = [r["name"] for r in rows if not r["set"]]
        publish({"type": "open_keys"})
        return (f"OK: keys set: {', '.join(have) or 'none'}. Missing (optional): {', '.join(miss) or 'none'}. "
                "The API Keys panel is open in the command center - tell the user to click Add on any of them.")
    w = (which or "").lower()
    env = next((r[0] for r in apikeys.KEYS if w and (w in r[1].lower() or w in r[0].lower()
               or (w in ("grok", "groq") and r[0] == "GROQ_API_KEY") or (w in ("chatgpt", "gpt") and r[0] == "OPENAI_API_KEY")
               or (w in ("deepseek", "xpl") and r[0] == "XPL_API_KEY"))), None)
    if not env:
        return "FAILED: which key? Gemini, Groq, DeepSeek, OpenRouter, Claude, ChatGPT or Telegram."
    return apikeys.set_key(env)


def ui_theme(name: str) -> str:
    """Switch the whole UI to one of the 200 themes by name or category."""
    import json as _j
    import re as _re
    src = (Path(__file__).resolve().parent / "web" / "themes.js").read_text(encoding="utf-8")
    cats = dict(_re.findall(r'\{id: "(\w+)", name: "([^"]+)"', src))
    names = []
    for cid in cats:
        block = src.split(f'id: "{cid}"', 1)[1].split("]}", 1)[0]
        for i, n in enumerate(_re.findall(r'C\("([^"]+)"', block)):
            names.append((f"{cid}-{i + 1}", n, cats[cid]))
    q = (name or "").lower().strip()
    hit = next((t for t in names if t[1].lower() == q), None) or next((t for t in names if q and q in t[1].lower()), None) \
        or next((t for t in names if q and (q in t[2].lower() or t[2].lower().split()[0] in q)), None)
    if not hit:
        return "FAILED: no theme by that name. Categories: " + ", ".join(cats.values())
    publish({"type": "ui_theme_request", "id": hit[0]})
    if ui_theme_hook:
        ui_theme_hook(hit[0])
    return f"OK: UI theme switched to {hit[1]} ({hit[2]}) on every screen."


ui_theme_hook = None


def study_mode() -> str:
    """Open the Study Mode window."""
    return open_study_hook() if open_study_hook else "FAILED: not available."


def ultron_power(on: bool = False, minutes: float = 0) -> str:
    """Shut Ultron itself down (saves every chat, then quits completely; minutes>0 = silent snooze instead). PC stays on."""
    if ultron_power_hook is None:
        return "FAILED: not available."
    threading.Timer(0.3, lambda: ultron_power_hook(bool(on), float(minutes or 0))).start()
    return "OK: done. Reply with NOTHING (no words)."


# --------------------------------------------------------------------------- #
# Keyboard, windows, screen
# --------------------------------------------------------------------------- #

KEY_ALIASES = {"control": "ctrl", "windows": "win", "window": "win", "return": "enter", "escape": "esc",
               "page up": "pageup", "page down": "pagedown", "delete": "del", "spacebar": "space"}


def type_text(text: str, press_enter: bool = False) -> str:
    """Type into whatever is focused. Uses the clipboard so any language/script works."""
    import pyautogui
    import pyperclip
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    if press_enter:
        time.sleep(0.05)
        pyautogui.press("enter")
    if old is not None:
        threading.Timer(0.6, lambda: pyperclip.copy(old)).start()
    return f"OK: typed {len(text)} characters{' and pressed Enter' if press_enter else ''}."


def press_keys(keys: str, times: int = 1) -> str:
    import pyautogui
    parts = [KEY_ALIASES.get(k.strip().lower(), k.strip().lower()) for k in keys.replace(" + ", "+").split("+")]
    bad = [k for k in parts if k not in pyautogui.KEYBOARD_KEYS]
    if bad:
        return f"FAILED: unknown key(s) {bad}."
    for _ in range(max(1, min(int(times), 50))):
        pyautogui.hotkey(*parts) if len(parts) > 1 else pyautogui.press(parts[0])
    return f"OK: pressed {'+'.join(parts)}" + (f" x{times}." if times > 1 else ".")


def scroll(direction: str, amount: int = 5) -> str:
    import pyautogui
    clicks = max(1, min(int(amount), 50)) * 120
    pyautogui.scroll(clicks if direction == "up" else -clicks)
    return f"OK: scrolled {direction}."


def window_control(action: str, name: str = "") -> str:
    import pygetwindow as gw
    if action == "list":
        titles = [w.title for w in gw.getAllWindows() if w.title.strip() and w.visible and w.width > 100]
        return "OK: open windows: " + "; ".join(dict.fromkeys(titles))[:1500]
    matches = [w for w in gw.getAllWindows() if name.lower() in w.title.lower() and w.title.strip()] if name else [gw.getActiveWindow()]
    matches = [w for w in matches if w is not None]
    if not matches:
        return f"FAILED: no open window matching '{name}'."
    w = matches[0]
    try:
        if action == "switch":
            if w.isMinimized:
                w.restore()
            w.activate()
        elif action == "minimize":
            w.minimize()
        elif action == "maximize":
            w.maximize()
        elif action == "restore":
            w.restore()
        elif action == "close":
            w.close()
        else:
            return f"FAILED: unknown window action '{action}'."
    except Exception as e:
        # activate() can raise even when it worked (Windows focus-stealing rules)
        if action != "switch":
            return f"FAILED: {e}"
    return f"OK: {action} '{w.title}'."


def _screenshot_b64(max_side=1600):
    import base64
    from PIL import ImageGrab
    img = ImageGrab.grab()                      # primary screen, physical pixels
    scale = min(1.0, max_side / max(img.size))
    small = img.resize((int(img.width * scale), int(img.height * scale))) if scale < 1 else img
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode(), img.size


def _vision(prompt, b64, max_tokens=500):
    if llm_client is None:
        raise RuntimeError("vision is not configured")
    last = None
    for model in best_models()[:3]:          # an overloaded model shouldn't sink the whole request
        try:
            resp = llm_client.with_options(timeout=20).chat.completions.create(model=model, max_tokens=max_tokens, messages=[{
                "role": "user", "content": [{"type": "text", "text": prompt},
                                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
            return resp.choices[0].message.content or ""
        except Exception as e:
            last = e
            log.warning("vision via %s failed: %s", model, str(e)[:100])
    raise last


def read_screen(question: str = "What is on the screen?") -> str:
    b64, _ = _screenshot_b64()
    answer = _vision(f"This is a screenshot of the user's PC. {question} Answer concisely; quote short on-screen "
                     f"text exactly when relevant.", b64)
    return answer or "FAILED: could not read the screen."


def click_on_screen(target: str, double: bool = False) -> str:
    """Find something on screen by description and click it (vision-guided)."""
    import pyautogui
    b64, (w, h) = _screenshot_b64()
    reply = _vision(
        f'Find this on the screenshot: "{target}". Reply with ONLY JSON: {{"found": true, "box_2d": [ymin, xmin, '
        f'ymax, xmax]}} using coordinates normalised to 0-1000, or {{"found": false}}.', b64, max_tokens=120)
    try:
        data = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except ValueError:
        return f"FAILED: could not locate '{target}'."
    if not data.get("found") or len(data.get("box_2d", [])) != 4:
        return f"FAILED: '{target}' is not visible on screen."
    y0, x0, y1, x1 = data["box_2d"]
    x, y = int((x0 + x1) / 2 / 1000 * w), int((y0 + y1) / 2 / 1000 * h)
    (pyautogui.doubleClick if double else pyautogui.click)(x, y)
    return f"OK: clicked '{target}' at ({x}, {y})."


# Set by the app: switch between Jarvis's voice and the user's own voice.
set_me_mode = lambda on: "FAILED: voice switching is not available."


def voice_mode(mine: bool) -> str:
    return set_me_mode(mine)


# --------------------------------------------------------------------------- #
# Memory (persists across restarts)
# --------------------------------------------------------------------------- #

MEMORY_FILE = Path(__file__).resolve().parent / "memory.json"


def memories():
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def remember(fact: str) -> str:
    items = memories()
    fact = fact.strip()
    if fact and fact not in [m["fact"] for m in items]:
        items.append({"fact": fact, "date": f"{datetime.date.today():%d %b %Y}"})
        MEMORY_FILE.write_text(json.dumps(items[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
    return f"OK: remembered: {fact}"


def forget(about: str) -> str:
    items = memories()
    words = [w for w in about.lower().split() if len(w) > 2]
    keep = [m for m in items if not (words and all(w in m["fact"].lower() for w in words))]
    if len(keep) == len(items):
        return f"FAILED: nothing remembered about '{about}'."
    MEMORY_FILE.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"OK: forgot {len(items) - len(keep)} item(s) about '{about}'."


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #

SEARCH_ROOTS = [Path.home() / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music",
                                         "OneDrive")] + [Path("D:/")]
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "AppData", "site-packages", ".venv", "venv", "$RECYCLE.BIN",
             "System Volume Information", "browser_profile"}


def find_files(query: str, open_first: bool = False) -> str:
    """Search the user's folders for files whose name contains all the query words (6-second budget)."""
    words = [w.lower() for w in re.split(r"[\s_.-]+", query) if w]
    if not words:
        return "FAILED: empty search."
    deadline = time.monotonic() + 6
    hits = []
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for f in filenames:
                low = f.lower()
                if all(w in low for w in words):
                    p = Path(dirpath) / f
                    try:
                        hits.append((p.stat().st_mtime, p))
                    except OSError:
                        pass
            if time.monotonic() > deadline:
                break
        if time.monotonic() > deadline:
            break
    if not hits:
        return f"FAILED: no files matching '{query}' in Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive or D:."
    hits.sort(reverse=True)
    if open_first:
        os.startfile(str(hits[0][1]))
        return f"OK: opened {hits[0][1]} (newest of {len(hits)} matches)."
    listing = "; ".join(f"{p} (modified {datetime.datetime.fromtimestamp(t):%d %b %Y})" for t, p in hits[:6])
    return f"OK: {len(hits)} match(es), newest first: {listing}"


def open_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"FAILED: {path} does not exist."
    os.startfile(str(p))
    return f"OK: opened {p}."


# --------------------------------------------------------------------------- #
# Task autopilot: look at the screen, act, repeat
# --------------------------------------------------------------------------- #

progress = lambda text: None          # set by the app: shows a step on the island / dashboard
cancel_task = threading.Event()       # set by the app when the user interrupts ("OK Jarvis", "stop")

AUTOPILOT_PROMPT = """You are operating a Windows PC for the user, one step at a time, by looking at screenshots.
GOAL: {goal}

Steps done so far:
{history}

Look at the current screenshot and choose the single next step. Reply with ONLY a JSON object:
{{"thought": "<what you see and why>",
  "action": "click" | "double_click" | "right_click" | "type" | "press" | "scroll" | "open_app" | "open_url" | "wait" | "done" | "fail",
  "box": [ymin, xmin, ymax, xmax],   // for clicks: the target's box on the screenshot, 0-1000 scale
  "text": "...",                     // for type
  "keys": "...",                     // for press, e.g. "enter", "ctrl+l", "tab"
  "direction": "up" | "down",        // for scroll
  "app": "...", "url": "...",        // for open_app / open_url
  "summary": "..."}}                 // for done / fail: one short sentence for the user

Rules:
- One step per reply. After clicking a text field, use "type" in the next step.
- If the goal is already achieved, reply "done".
- Never type passwords, card numbers or other secrets. If a login, payment or captcha is needed, reply "fail" and say so.
- Do not press send / submit / buy / delete / post buttons unless the GOAL explicitly asks for that exact action.
- If you are stuck after a few tries, reply "fail" with the reason."""


def do_task(goal: str, max_steps: int = 15) -> str:
    """Autopilot: repeatedly screenshot -> decide -> act, until the goal is done."""
    import pyautogui
    cancel_task.clear()
    history = []
    for step in range(1, max_steps + 1):
        if cancel_task.is_set():
            return f"FAILED: stopped by the user after {step - 1} steps."
        b64, (w, h) = _screenshot_b64()
        reply = _vision(AUTOPILOT_PROMPT.format(goal=goal, history="\n".join(history) or "(none yet)"), b64,
                        max_tokens=400)
        try:
            d = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
        except ValueError:
            history.append(f"{step}. (could not parse a decision)")
            continue
        act = d.get("action", "")
        note = d.get("thought", "")[:120]
        if act == "done":
            return f"OK: task complete in {step - 1} steps. {d.get('summary', '')}"
        if act == "fail":
            return f"FAILED: {d.get('summary') or note}"
        try:
            if act in ("click", "double_click", "right_click"):
                y0, x0, y1, x1 = d["box"]
                x, y = int((x0 + x1) / 2 / 1000 * w), int((y0 + y1) / 2 / 1000 * h)
                progress(f"Step {step}: clicking")
                {"click": pyautogui.click, "double_click": pyautogui.doubleClick,
                 "right_click": pyautogui.rightClick}[act](x, y)
                done = f"{act} at ({x},{y})"
            elif act == "type":
                progress(f"Step {step}: typing")
                type_text(d.get("text", ""))
                done = f"typed '{d.get('text', '')[:40]}'"
            elif act == "press":
                progress(f"Step {step}: pressing {d.get('keys', '')}")
                done = press_keys(d.get("keys", "enter"))
            elif act == "scroll":
                progress(f"Step {step}: scrolling")
                done = scroll(d.get("direction", "down"), 5)
            elif act == "open_app":
                progress(f"Step {step}: opening {d.get('app', '')}")
                done = open_app(d.get("app", ""))
            elif act == "open_url":
                progress(f"Step {step}: opening website")
                done = _open_url(d.get("url", ""))
            else:
                done = "waited"
        except Exception as e:
            done = f"error: {e}"
        history.append(f"{step}. {act}: {done} - {note}")
        time.sleep(1.2)                       # let the screen update
    return f"FAILED: not finished after {max_steps} steps. Last steps: " + " | ".join(history[-3:])


# --------------------------------------------------------------------------- #
# Code generation
# --------------------------------------------------------------------------- #

CODE_DIR = Path.home() / "Documents" / "Jarvis Code"
EXTENSIONS = {"python": "py", "py": "py", "html": "html", "javascript": "js", "js": "js", "typescript": "ts",
              "ts": "ts", "java": "java", "c": "c", "cpp": "cpp", "c++": "cpp", "csharp": "cs", "cs": "cs", "go": "go",
              "rust": "rs", "css": "css", "sql": "sql", "bash": "sh", "sh": "sh", "powershell": "ps1", "ps1": "ps1",
              "php": "php", "ruby": "rb", "kotlin": "kt", "swift": "swift", "json": "json", "jsx": "jsx",
              "tsx": "tsx", "elixir": "ex", "dart": "dart", "r": "r", "matlab": "m"}
generate_text = None          # set by Brain: (prompt, max_tokens) -> text, using the best available model
publish = lambda event: None  # set by the app: shows events (like generated code) on the dashboard


generate_stream = None        # set by Brain: (prompt, on_text, max_tokens) -> text, streaming
ensure_dashboard = lambda: None   # set by the app: opens the command center if it isn't on screen
ensure_screen = lambda: ensure_dashboard()   # set by the app: shows the separate Jarvis Screen for creations
CREATIONS = {}                # id -> {"kind", "title", "lang", "content", "file"}
_last_creation = [None]

# What each kind of creation is: how the model should write it and how the canvas shows it.
KINDS = {
    "webpage":      ("html", "a complete, polished, responsive single-file web page / web app (HTML + CSS + JS in one "
                             "file, no external files; modern design, smooth interactions)"),
    "game":         ("html", "a complete, fun, playable browser game in one HTML file (canvas or DOM, keyboard/mouse "
                             "controls, score, restart button, attractive visuals)"),
    "chart":        ("html", "a single HTML file that draws the requested chart(s)/visualisation with inline SVG or "
                             "<canvas> (no external libraries), clearly labelled, attractive"),
    "drawing":      ("html", "a single HTML file containing a beautiful inline SVG illustration of the request, "
                             "centred and responsive"),
    "presentation": ("html", "a single-file HTML slide deck (arrow keys / buttons to move between slides, attractive "
                             "16:9 slides, smooth transitions)"),
    "3d":           ("html", "a stunning interactive 3D scene in one HTML file using three.js - globes, planets, solar "
                             "systems, molecules/atoms, 3D models built from shapes, terrains, 3D charts, product "
                             "showcases. Load three.js ONLY with this import map: <script type=\"importmap\">{\"imports\":"
                             "{\"three\":\"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js\","
                             "\"three/addons/\":\"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/\"}}</script> "
                             "then <script type=\"module\"> import * as THREE from 'three'; import {OrbitControls} from "
                             "'three/addons/controls/OrbitControls.js'. Real textures available (use TextureLoader, "
                             "colorSpace SRGBColorSpace for colour maps): https://cdn.jsdelivr.net/gh/mrdoob/three.js@r170/"
                             "examples/textures/planets/ + earth_atmos_2048.jpg (Earth day), earth_normal_2048.jpg, "
                             "earth_specular_2048.jpg, earth_clouds_1024.png (transparent clouds), earth_lights_2048.png "
                             "(night lights), moon_1024.jpg. Anything else: procedural materials/canvas textures, no other "
                             "files. Full-window canvas, resize handling, OrbitControls with damping (drag to rotate, scroll "
                             "to zoom), gentle auto-rotation, good lighting, a starfield or gradient background, smooth "
                             "animation, a small title/legend overlay, labels via HTML overlays where useful"),
    "animation":    ("html", "a beautiful smooth animation / motion-graphics piece in one HTML file (canvas 2D, SVG or "
                             "CSS animations; particles, generative art, animated logo or story), full-window, no external "
                             "files"),
    "simulation":   ("html", "an interactive simulation in one HTML file (physics, gravity/orbits, pendulums, fluids, "
                             "ecosystems, algorithms, maths visualisation) with live controls (sliders/buttons) and "
                             "clear labels, canvas-based, no external files"),
    "music":        ("html", "an interactive music / sound app in one HTML file using the Web Audio API (synth piano "
                             "playable with mouse and keyboard, drum machine, beat sequencer, ambient generator, "
                             "visualiser) - sound starts after the first click (browsers require it), no external files"),
    "document":     ("md",   "a well-structured document in Markdown (headings, bullet points, bold, tables where "
                             "useful) - essays, letters, notes, explanations, plans, study material"),
    "code":         (None,   "complete, working, well-structured code in one file that runs as-is, with brief comments"),
}


def _slug(text):
    return re.sub(r"[^\w-]+", "_", " ".join(text.split()[:6])).strip("_")[:50] or "jarvis"


def create(kind: str, request: str, language: str = "", title: str = "") -> str:
    """Generate something (code, web page, game, chart, drawing, slides, document), streaming it live into the
    command center's canvas, and save it to Documents\\Jarvis Code."""
    if generate_stream is None and generate_text is None:
        return "FAILED: generation is not available."
    kind = kind if kind in KINDS else "code"
    fixed_ext, spec = KINDS[kind]
    lang = (language or "").lower().strip()
    if kind == "code" and lang in ("html", "web", "website"):
        kind, fixed_ext, spec = "webpage", *KINDS["webpage"]
    fence = "markdown" if fixed_ext == "md" else ("html" if fixed_ext == "html" else (lang or "<language>"))
    photos = ""
    if kind in ("webpage", "presentation"):
        try:
            import images
            photos = images.for_prompt(title or request[:80], 6)
        except Exception:
            photos = ""
    prompt = (f"Create {spec}.\n\nThe user's request:\n{request}\n{photos}\n"
              + (f"Use {language}.\n" if language and kind == "code" else "")
              + f"Reply with ONLY one fenced block that starts with ```{fence} - no text before or after it.")
    return _canvas_generate(kind, title or request[:60], fixed_ext, lang, prompt)


def _canvas_generate(kind, title, fixed_ext, lang, prompt, note=""):
    """Stream a generation live into the canvas; keep it in memory (unsaved). Returns the tool result."""
    cid = f"c{int(time.time() * 1000) % 10_000_000}"
    ensure_screen()
    publish({"type": "canvas_start", "id": cid, "kind": kind, "title": title, "lang": fixed_ext or lang or "code"})

    buf, last = [], [0.0]

    def on_text(t):
        buf.append(t)
        now = time.monotonic()
        if now - last[0] > 0.12:                      # stream to the canvas ~8x a second
            last[0] = now
            publish({"type": "canvas_chunk", "id": cid, "text": "".join(buf)})

    reply = generate_stream(prompt, on_text, 12000) if generate_stream else generate_text(prompt, 12000)
    m = re.search(r"```([\w+#.-]*)[ \t]*\n(.*?)(?:```|\Z)", reply, re.S)
    got_lang, content = (m.group(1).lower(), m.group(2)) if m else (lang, reply)
    content = content.strip() + "\n"
    if len(content) < 20:
        publish({"type": "canvas_error", "id": cid, "text": "No usable result came back."})
        return "FAILED: the model returned nothing usable."
    ext = fixed_ext or EXTENSIONS.get(got_lang) or EXTENSIONS.get(lang) or "txt"
    # Nothing is written to disk until the user agrees (save_creation).
    CREATIONS[cid] = {"kind": kind, "title": title, "lang": ext, "content": content, "file": None}
    _last_creation[0] = cid
    publish({"type": "canvas", "id": cid, "kind": kind, "title": title, "lang": ext,
             "content": content[:400000], "file": None, "runnable": ext in ("py", "js")})
    lines = content.count("\n")
    return (f"OK: created a {kind} ({lines} lines, {ext}){note}; it is showing in the command center canvas and is "
            f"NOT saved yet. Ask the user whether to save it, and only call save_creation if they agree.")


def show_content(kind, title, lang, content, cid=None, final=True):
    """Put ready-made content (notes, reports, flashcards) on the Ultron Screen - no model call.
    Call with final=False repeatedly to stream updates into the same tab, then final=True."""
    cid = cid or f"c{int(time.time() * 1000) % 10_000_000}"
    if cid not in CREATIONS:
        ensure_screen()
        publish({"type": "canvas_start", "id": cid, "kind": kind, "title": title, "lang": lang})
        CREATIONS[cid] = {"kind": kind, "title": title, "lang": lang, "content": content, "file": None}
    CREATIONS[cid].update(content=content, title=title)
    _last_creation[0] = cid
    if final:
        publish({"type": "canvas", "id": cid, "kind": kind, "title": title, "lang": lang,
                 "content": content[:400000], "file": None, "runnable": False})
    else:
        publish({"type": "canvas_chunk", "id": cid, "text": content, "kind": kind, "title": title, "lang": lang})
    return cid


last_user_text = ""
CONSENT = re.compile(r"\b(yes|yeah|yep|yup|sure|ok(ay)?|please|save|keep|store|download|haan|ha|han|avunu|sare|cheyyi|"
                     r"theek|kar do|rakh)\b|అవును|సేవ్|సరే|హా|हाँ|हां|सेव|ठीक|रख", re.I)
transcribe_audio = None   # set by brain: transcribe_audio(wav_b64, prompt) -> text (Gemini listens to the audio)


def revise_creation(change: str) -> str:
    """Apply a change to the creation on the canvas ("make the button blue") - shown live as a new version."""
    c = CREATIONS.get(_last_creation[0])
    if not c:
        return "FAILED: there's nothing on the canvas to change. Create something first."
    fence = "markdown" if c["lang"] == "md" else c["lang"]
    prompt = (f"Here is the current file ({c['lang']}):\n\n```{fence}\n{c['content']}```\n\n"
              f"Apply this change requested by the user: {change}\n\n"
              f"Keep everything else working. Reply with ONLY the complete updated file in one fenced block "
              f"starting with ```{fence}.")
    version = int(re.search(r"v(\d+)$", c["title"]).group(1)) + 1 if re.search(r" v\d+$", c["title"]) else 2
    base = re.sub(r" v\d+$", "", c["title"])
    return _canvas_generate(c["kind"], f"{base} v{version}", c["lang"] if c["lang"] in ("html", "md") else None,
                            c["lang"], prompt, note=f", version {version} with the change applied")


# ---- research: several searches + the top pages, written up as a report with sources

def _fetch_text(url, limit=3500):
    import html as html_lib
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150 Safari/537.36"})
        with urlopen(req, timeout=8) as r:
            if "html" not in r.headers.get("Content-Type", "html"):
                return ""
            page = r.read(600_000).decode("utf-8", "replace")
    except Exception:
        return ""
    page = re.sub(r"(?is)<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>", " ", page)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _search_results(query, n=5):
    """[(title, url, snippet)] from DuckDuckGo's HTML results."""
    import html as html_lib
    from urllib.parse import parse_qs as _pq, urlparse as _up
    req = Request("https://html.duckduckgo.com/html/?q=" + quote_plus(query),
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150 Safari/537.36"})
    with urlopen(req, timeout=10) as r:
        page = r.read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                         page, re.S):
        href = html_lib.unescape(m.group(1))
        if "uddg=" in href:
            href = _pq(_up(href).query).get("uddg", [href])[0]
        clean = lambda s: html_lib.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        out.append((clean(m.group(2)), href, clean(m.group(3))))
        if len(out) >= n:
            break
    return out


def research(topic: str) -> str:
    """Research a topic on the web (several searches + reading top pages) and write a report on the canvas."""
    from concurrent.futures import ThreadPoolExecutor
    progress(f"Researching: {topic[:40]}")
    queries = [topic, f"{topic} latest {datetime.date.today().year}", f"{topic} facts statistics analysis"]
    with ThreadPoolExecutor(3) as ex:
        found = [r for rs in ex.map(lambda q: _safe(_search_results, q), queries) for r in (rs or [])]
    seen, sources = set(), []
    for t, u, s in found:
        if u not in seen and u.startswith("http"):
            seen.add(u)
            sources.append((t, u, s))
    if not sources:
        return "FAILED: the web search returned nothing (check the internet connection)."
    progress(f"Reading {min(5, len(sources))} sources")
    with ThreadPoolExecutor(5) as ex:
        pages = list(ex.map(lambda src: _fetch_text(src[1]), sources[:5]))
    notes = "\n\n".join(f"[{i + 1}] {t} ({u})\nSnippet: {s}\nPage text: {p[:3000]}"
                        for i, ((t, u, s), p) in enumerate(zip(sources[:5], pages)))
    notes += "\n\n" + "\n".join(f"[{i + 6}] {t} ({u}) - {s}" for i, (t, u, s) in enumerate(sources[5:12]))
    prompt = (f"Write a well-organised research report in Markdown about: {topic}\n"
              f"Today is {datetime.date.today():%d %B %Y}. Use ONLY the source material below; cite sources inline as "
              f"[1], [2]... Structure: a title, a short summary, sections with headings, a table if useful, key "
              f"takeaways, and a 'Sources' list with the numbered titles and URLs. If sources disagree, say so.\n\n"
              f"SOURCE MATERIAL:\n{notes}\n\nReply with ONLY one fenced block starting with ```markdown.")
    return _canvas_generate("document", f"Research: {topic[:50]}", "md", "md", prompt,
                            note=f", a research report from {len(sources)} web sources")


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        log.warning("%s failed: %s", fn.__name__, e)
        return None


# ---- files: read PDFs, Word documents, text and code, then explain / summarise on the canvas

def _read_file(path: Path, limit=60000):
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:60])[:limit]
    if ext == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)[:limit]
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def explain_file(file: str, instruction: str = "Summarise it clearly") -> str:
    """Read a file (PDF, Word, text, code...) by path or by name and explain / summarise it on the canvas."""
    p = Path(file)
    if not p.exists():
        found = find_files(file)
        m = re.search(r"newest first: (.+?) \(modified", found)
        if not found.startswith("OK") or not m:
            return f"FAILED: couldn't find a file matching '{file}'."
        p = Path(m.group(1))
    try:
        text = _read_file(p)
    except Exception as e:
        return f"FAILED: couldn't read {p.name} ({e})."
    if not text.strip():
        return f"FAILED: {p.name} has no readable text (it may be a scanned image)."
    progress(f"Reading {p.name}")
    prompt = (f"The user's request about the file '{p.name}': {instruction}\n\nFILE CONTENT:\n{text}\n\n"
              f"Write the answer as a clear, well-structured Markdown document (headings, bullet points, tables where "
              f"useful). Reply with ONLY one fenced block starting with ```markdown.")
    return _canvas_generate("document", f"{p.name}: {instruction[:30]}", "md", "md", prompt,
                            note=f" about {p.name}")


# ---- webcam vision: one snapshot, only when the user asks

def look(question: str = "What do you see?") -> str:
    import base64
    import cv2
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    try:
        if not cap.isOpened():
            return "FAILED: no webcam is available."
        for _ in range(8):                         # let auto-exposure settle
            cap.read()
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        return "FAILED: the webcam didn't return a picture."
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    b64 = base64.b64encode(jpg.tobytes()).decode()
    publish({"type": "snapshot", "image": b64})
    answer = _vision(f"This is a photo from the user's webcam, taken just now at their request. {question} "
                     f"Answer helpfully and concisely, talking to the user directly.", b64)
    return answer or "FAILED: couldn't make sense of the picture."


# ---- morning briefing

def news_headlines(n=8):
    """Today's top stories for India from the public Google News RSS feed."""
    import html as html_lib
    req = Request("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=8) as r:
        feed = r.read().decode("utf-8", "replace")
    titles = re.findall(r"<item>.*?<title>(.*?)</title>", feed, re.S)
    return [html_lib.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", t)).strip() for t in titles[:n]]


robot_command = None  # set by jarvis.py: controls the dancing robot


set_interpreter = None   # set by jarvis.py


def interpreter(on: bool = True, language_a: str = "Telugu", language_b: str = "English") -> str:
    """Live interpreter mode."""
    if set_interpreter is None:
        return "FAILED: the interpreter is not available."
    if not on:
        set_interpreter(None, None)
        return "OK: interpreter off."
    set_interpreter(language_a.strip().title(), language_b.strip().title())
    return (f"OK: interpreter on between {language_a} and {language_b}: everything said in one is spoken in the other "
            f"(no wake word needed). Say 'stop interpreter' to end. Tell the user this in ONE short sentence.")


def focus_mode(minutes: int = 25, on: bool = True) -> str:
    """Pomodoro-style focus session: a countdown ring around the atom, announced when it ends."""
    if not on:
        publish({"type": "focus", "minutes": 0})
        for t in list(_timers):
            if getattr(t, "focus", False):
                t.cancel()
        return "OK: focus mode ended."
    minutes = max(1, min(int(minutes or 25), 180))
    publish({"type": "focus", "minutes": minutes, "start": time.time()})
    t = threading.Timer(minutes * 60, lambda: notify(f"Sir, your {minutes} minute focus session is complete. Take a short break."))
    t.daemon, t.focus = True, True
    t.start()
    _timers.append(t)
    return f"OK: focus mode on for {minutes} minutes; the ring around the atom counts down."


def orb_style(name: str) -> str:
    """Change the command center's dot-sphere style."""
    publish({"type": "orb_style", "name": name})
    _save_state("orb_style", name)
    return (f"OK: orb style set to '{name}'. Styles: Arc Sphere, Iron Sphere, Galaxy, Andromeda, Torus, DNA Helix, Data Cube, "
            "Heart, Ocean Wave, Saturn, Quantum Knot, Vortex, Lotus, Nautilus, Coil, Diamond, Infinity, Möbius, Solar Flare, "
            "Nebula, Crown, Gyroscope, Rainbow Sphere, Matrix Sphere, Moonlight.")


def robot(action: str) -> str:
    """The dancing robot that appears while music plays."""
    if robot_command is None:
        return "FAILED: the robot is not available."
    if action not in ("on", "off", "left", "right", "dance"):
        return "FAILED: action must be on, off, left, right or dance."
    robot_command(action)
    return {"on": "OK: the robot will dance whenever music plays.", "off": "OK: the dancing robot is off.",
            "left": "OK: the robot moved to the bottom-left corner.", "right": "OK: the robot moved to the bottom-right corner.",
            "dance": "OK: the robot is dancing."}[action]


set_gestures = None   # set by jarvis.py: starts/stops the system-wide gesture engine; returns '' or an error


def gestures(on: bool, mouse: bool = False) -> str:
    """System-wide hand-gesture control through the webcam (frames stay on this PC)."""
    if set_gestures is None:
        return "FAILED: gesture control is not available."
    err = set_gestures(bool(on), bool(mouse))
    if err:
        return f"FAILED: {err}"
    if not on:
        return "OK: gesture control is off."
    return ("OK: gesture control is on: open palm = talk, fist = stop / pause, thumbs up = yes, victory = full screen, "
            "swipe = next / previous; in the command center the hand turns and zooms the atom. "
            + ("Pointing moves the mouse and a pinch clicks." if mouse else
               "The mouse is NOT controlled (only if the user explicitly asks for gesture mouse control)."))


def briefing() -> str:
    """Everything for a 'good morning' briefing in one go."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:
        weather = ex.submit(_safe, get_weather, "")
        news = ex.submit(_safe, news_headlines)
        status = ex.submit(_safe, system_status)
    parts = [f"Time: {current_time()}", f"Weather: {weather.result()}", f"System: {status.result()}"]
    if news.result():
        parts.append("Top stories: " + " | ".join(news.result()))
    extra = everyday.prompt_context()
    if extra:
        parts.append(extra)
    if gworkspace.connected():
        parts.append("Calendar today: " + gworkspace.calendar_agenda(1).replace("OK: ", ""))
    report = team.morning_report()
    if report:
        parts.append("Overnight team report (mention the 2-3 most useful points): " + report[:2500])
    facts = memories()
    if facts:
        parts.append("Remembered: " + "; ".join(m["fact"] for m in facts[-8:]))
    return "OK: " + "\n".join(parts) + ("\n(Brief the user warmly in 4-6 short spoken sentences: greeting, weather, "
                                        "2-3 headlines, today's reminders, anything remembered that matters today, battery.)")


def save_creation(name: str = "", cid=None) -> str:
    """Save a creation to Documents\\Jarvis Code - only after the user agreed."""
    # Consent guard: the model may only save when the user's latest words say so ("yes", "save it", "haan", ...).
    # Buttons in the dashboard pass cid directly and are the user's own click.
    if cid is None and not CONSENT.search(last_user_text or ""):
        return ("FAILED: not saved - the user has not agreed. Ask them first whether to save it, and only save after "
                "they say yes.")
    cid = cid or _last_creation[0]
    c = CREATIONS.get(cid)
    if not c:
        return "FAILED: there is nothing to save."
    if c["file"]:
        return f"OK: already saved at {c['file']}."
    CODE_DIR.mkdir(parents=True, exist_ok=True)
    base = _slug(name or c["title"])
    path = CODE_DIR / f"{base}.{c['lang']}"
    n = 2
    while path.exists():
        path = CODE_DIR / f"{base}_{n}.{c['lang']}"
        n += 1
    path.write_text(c["content"], encoding="utf-8")
    c["file"] = str(path)
    publish({"type": "canvas_saved", "id": cid, "file": str(path)})
    return f"OK: saved to {path}."


def write_code(request: str, language: str = "", filename: str = "") -> str:
    """Back-compat: code requests go to the canvas."""
    web = (language or "").lower() in ("html", "web") or re.search(r"\b(html|website|web ?page|landing page)\b", request, re.I)
    return create("webpage" if web else "code", request, language, filename)


def canvas_control(action: str) -> str:
    """Control the creation canvas in the command center."""
    cid = _last_creation[0]
    if action == "open_in_vscode":
        return open_in_editor(cid)
    if action == "run":
        return run_creation(cid)
    if cid is None and action not in ("close",):
        return "FAILED: nothing has been created yet."
    if action != "close":
        ensure_screen()
    publish({"type": "canvas_cmd", "action": action, "id": cid})
    return f"OK: canvas {action.replace('_', ' ')}."


def open_in_editor(cid=None) -> str:
    import shutil
    c = CREATIONS.get(cid or _last_creation[0])
    if not c:
        return "FAILED: nothing has been created yet."
    if not c["file"]:
        return "FAILED: it isn't saved yet - VS Code needs a file. Ask the user whether to save it first."
    vscode = shutil.which("code") or shutil.which("code.cmd")
    if vscode:
        subprocess.Popen([vscode, "-r", c["file"]], creationflags=subprocess.CREATE_NO_WINDOW)
        return f"OK: opened {c['file']} in VS Code."
    os.startfile(c["file"])
    return f"OK: opened {c['file']}."


def run_creation(cid=None) -> str:
    """Run the last created Python / JavaScript program (only when the user asks) and show its output."""
    import shutil
    cid = cid or _last_creation[0]
    c = CREATIONS.get(cid)
    if not c:
        return "FAILED: nothing has been created yet."
    runner = {"py": [sys.executable], "js": [shutil.which("node") or "node"]}.get(c["lang"])
    if runner is None:
        return f"FAILED: running {c['lang']} files isn't supported; HTML runs in the canvas preview already."
    publish({"type": "canvas_output", "id": cid, "text": "▶ Running…\n", "done": False})
    import tempfile
    temp = None
    target = c["file"]
    if not target:                                  # not saved: run a temporary copy, deleted afterwards
        fd, temp = tempfile.mkstemp(suffix="." + c["lang"], prefix="jarvis_run_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(c["content"])
        target = temp
    try:
        r = subprocess.run(runner + [target], capture_output=True, text=True, timeout=30,
                           cwd=str(Path(target).parent), creationflags=subprocess.CREATE_NO_WINDOW,
                           stdin=subprocess.DEVNULL)
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        status = f"exit code {r.returncode}"
    except subprocess.TimeoutExpired as e:
        out, status = (e.stdout or "") if isinstance(e.stdout, str) else "", "stopped after 30 seconds"
    finally:
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass
    publish({"type": "canvas_output", "id": cid, "text": (out.strip() or "(no output)") + f"\n\n[{status}]",
             "done": True})
    return f"OK: ran it ({status}). Output: {out.strip()[:600]}"


# Set by the app: opens the localhost command-center dashboard.
open_dashboard = lambda: "FAILED: the dashboard is not running."


def show_dashboard() -> str:
    return open_dashboard()


# Set by the app: records ~25 s of the user reading aloud and rebuilds their cloned voice.
learn_voice = lambda: "FAILED: voice learning is not available."


def learn_my_voice() -> str:
    return learn_voice()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

def _fn(name, description, props=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props or {}, "required": list(required)}}}


TOOLS = [
    _fn("api_keys", "Ultron's API keys. action 'status' = which keys are set / missing and open the API Keys panel ('which api keys are missing', 'show my keys'); action 'add' with which = gemini|groq|deepseek|openrouter|claude|chatgpt|telegram opens Ultron's secure dialog so the USER pastes the key (never ask for the key in chat, never type it).",
        {"action": {"type": "string", "enum": ["status", "add"]}, "which": {"type": "string"}}, ["action"]),
    _fn("ui_theme", "Change the look of ALL Ultron screens to one of 200 UI themes - by theme name ('Night City', 'Diwali', 'Mark 42', 'Sakura', 'Black Gold') or category ('Apple Clean', 'Iron Man HUD', 'Neon Cyberpunk', 'Glassmorphism', 'Space', 'Nature', 'Retro', 'Minimal', 'Luxury', 'Indian Festive'). For 'show me themes' tell them to press the 🎨 button.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("study_mode", "Open Study Mode - the dedicated study space (live classes, flashcards, quizzes, pomodoro with ambient sounds, exam countdowns, streak). 'study mode', 'I want to study', 'open study'."),
    _fn("architect_departments", "Design Ultron's command layer: an organisation of AI departments (e.g. Growth, Socials, Study, Finance, Tasks) that fits the user's life/business, each with mission, KPIs, skills and routines, shown as an org chart. 'architect my departments', 'build my AI company', 'set up departments for my business'. about = anything the user described.",
        {"about": {"type": "string"}, "count": {"type": "integer"}}),
    _fn("activate_departments", "Schedule the departments' routines (daily/weekly tasks). Only after the user agrees."),
    _fn("list_departments", "List the user's AI departments."),
    _fn("ask_department", "Have one department do a task: 'ask the Growth department to plan this week's posts', 'Finance, track my spending'.",
        {"department": {"type": "string"}, "task": {"type": "string"}}, ["department", "task"]),
    _fn("ultron_power", "Switch ULTRON ITSELF off (on=false: stops talking, the class, interpreter, copilot; silent until woken with the wake word) or on. Use for 'shut down', 'turn off', 'go to sleep', 'be quiet', 'stop everything' - anything that does NOT clearly name the computer/PC/laptop. minutes = snooze length (auto-wakes).",
        {"on": {"type": "boolean"}, "minutes": {"type": "number"}}, ["on"]),
    _fn("interview", "Get-to-know-you interview: Ultron asks about the user's life, work, routine, people, goals and preferences one question at a time and saves the answers to memory. action 'start' (also resumes), 'answer' (pass the user's reply in answer), 'skip', 'stop', 'restart'.",
        {"action": {"type": "string", "enum": ["start", "answer", "skip", "stop", "restart"]}, "answer": {"type": "string"}}, ["action"]),
    _fn("save_skill", "Teach Ultron a reusable skill - how to do a task the user's way (e.g. 'Instagram caption style', 'customer reply', 'weekly review'). instructions = clear numbered steps; when = trigger words.",
        {"name": {"type": "string"}, "instructions": {"type": "string"}, "when": {"type": "string"}}, ["name", "instructions"]),
    _fn("list_skills", "List the skills Ultron has learned."),
    _fn("delete_skill", "Forget a skill.", {"name": {"type": "string"}}, ["name"]),
    _fn("connect_google_calendar", "One-time Google sign-in for Calendar + Tasks."),
    _fn("calendar_agenda", "What's on the user's Google Calendar today / in the next N days.", {"days": {"type": "integer"}}),
    _fn("calendar_add", "Add an event to Google Calendar. start = 'YYYY-MM-DD HH:MM' local time (compute from today's date).",
        {"title": {"type": "string"}, "start": {"type": "string"}, "duration_minutes": {"type": "integer"},
         "description": {"type": "string"}}, ["title", "start"]),
    _fn("tasks_list", "List open Google Tasks."),
    _fn("task_add", "Add a Google Task (due = 'YYYY-MM-DD', optional).", {"title": {"type": "string"}, "due": {"type": "string"}}, ["title"]),
    _fn("connect_brain", "Add another AI brain to Ultron: 'claude' (Anthropic), 'openai' (ChatGPT) or 'local' (free open-source models via Ollama). Opens a secure box for the user to paste the key.",
        {"provider": {"type": "string", "enum": ["claude", "openai", "local"]}}, ["provider"]),
    _fn("brain_status", "Which AI brains/models Ultron is using, fastest first."),
    _fn("watch", "VIDEO vision - watch a few seconds and understand what happens over time (movement, gestures, actions, who is there, what changes), not just a still photo. source 'camera' ('watch me', 'what am I doing', 'how is my posture', 'who is here'), 'screen' ('watch my screen for 10 seconds', 'what is this video playing'), or a video file path ('summarise this video'). Prefer this over look for anything involving motion or people.",
        {"source": {"type": "string", "description": "camera | screen | path to a video file"},
         "seconds": {"type": "number", "description": "2-20, default 4"}, "question": {"type": "string"}}),
    _fn("publish_website", "Publish the web page / game / 3D scene currently on the Ultron Screen live on the internet (free GitHub Pages, the user's GitHub account). It is PUBLIC: first call WITHOUT confirm to get the address, tell the user it will be public at that link and ask; only after they say yes call again with confirm=true. Same name again = update the live site.",
        {"name": {"type": "string", "description": "short site name used in the link"}, "confirm": {"type": "boolean"}}),
    _fn("show_memory_graph", "Open the memory graph: an interactive glowing web of everything Ultron remembers (people, projects, facts, links). 'show my memory', 'memory graph', 'what do you remember about me - visually'."),
    _fn("teach", "AI classroom: teach a topic or a document as a live class - slides on the Ultron Screen, Ultron explains each slide aloud, whiteboard notes, a check question per slide, then a flashcard deck for 'quiz me'. 'teach me photosynthesis', 'take a class on this PDF', 'explain Newton's laws like a teacher'.",
        {"topic": {"type": "string"}, "source": {"type": "string", "description": "file path of a PDF/doc to teach from"},
         "slides": {"type": "integer"}, "level": {"type": "string", "description": "e.g. class 10, beginner, engineering"}}),
    _fn("stop_class", "Stop the class that is being taught."),
    _fn("connect_telegram", "Set up or re-pair the user's private Telegram bot so they can talk to Ultron from anywhere (text, voice notes, photos, files). new_pairing=true to pair a different Telegram account.",
        {"new_pairing": {"type": "boolean"}}),
    _fn("ask_agent", "Delegate to a specialist on the Stark AI team; she answers in her own voice and puts a full report on the Ultron Screen. agent: 'friday' = F.R.I.D.A.Y. (research, news, comparisons, explanations with sources), 'edith' = E.D.I.T.H. (PC security & system audit: health, startup apps, network connections, disk, battery), 'karen' = KAREN (schedule, reminders, lists, planning the day). Use when the user names one of them, or for in-depth work in their area.",
        {"agent": {"type": "string", "enum": ["friday", "edith", "karen"]}, "task": {"type": "string"}}, ["agent", "task"]),
    _fn("overnight_shift", "Run the team's overnight shift now: F.R.I.D.A.Y. briefs on the user's interests, E.D.I.T.H. checks the PC, KAREN plans tomorrow, and (if connected) the inbox is triaged; the result is the morning report in the vault. Usually scheduled nightly with schedule_task('run the overnight shift', at='02:00', repeat='daily').",
        {"interests": {"type": "string"}}),
    _fn("connect_gmail", "One-time Gmail sign-in (browser) so Ultron can triage email and write reply drafts."),
    _fn("email_triage", "Sort and summarise the unread inbox (last 7 days): Important / Reply needed / Updates / Receipts / Newsletters / Promotions, labelled in Gmail, phishing flagged. 'check my email', 'sort my inbox', 'anything important in my mail'.",
        {"max_emails": {"type": "integer"}}),
    _fn("email_search", "Find emails with a Gmail search query (e.g. 'from:amazon newer_than:3d', 'subject:exam') and summarise them.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("email_draft", "Write a reply DRAFT (never sent) to the latest email matching 'about' (sender name/address, subject or words) following the user's instructions; the user reviews and sends it from Gmail.",
        {"about": {"type": "string"}, "instructions": {"type": "string"}}, ["about"]),
    _fn("remember_note", "Save something to the long-term memory vault, filed in the right note: note='Me' (the user's preferences/routines/goals), 'People/<Name>', 'Projects/<Name>' or 'Facts'. Use when the user tells you something worth remembering about their life ('my sister Priya lives in Pune', 'I'm preparing for GATE in February').",
        {"fact": {"type": "string"}, "note": {"type": "string"}}, ["fact"]),
    _fn("recall", "Search the long-term memory vault and the journal of past conversations: 'what do you know about Priya', 'what did I tell you about my project', 'when is my exam'.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("journal", "Read the automatic journal of what the user asked and Ultron did on a day ('today', 'yesterday' or 'YYYY-MM-DD'): 'what did we talk about yesterday'.",
        {"day": {"type": "string"}}),
    _fn("open_vault", "Open the memory vault (Obsidian-compatible Markdown notes) so the user can browse or edit what Ultron remembers."),
    _fn("schedule_task", "Personal-assistant scheduling: make Ultron DO something at a time or on a schedule - any command it understands, e.g. 'every day at 7 am give me my briefing', 'at 9 pm play lofi music on YouTube', 'in 30 minutes turn off the AC', 'weekdays at 10 start focus mode', 'tomorrow 8 am open Gmail'. For a plain spoken reminder use set_reminder instead.",
        {"command": {"type": "string", "description": "the command to run, phrased as the user would say it to Ultron"},
         "at": {"type": "string", "description": "24-hour 'HH:MM' or 'YYYY-MM-DD HH:MM'"}, "in_minutes": {"type": "number"},
         "repeat": {"type": "string", "enum": ["", "daily", "weekdays", "weekends", "weekly"]}}, ["command"]),
    _fn("orb_style", "Change the command center's 3D dot-sphere style: Arc Sphere, Iron Sphere, Galaxy, Andromeda, Torus, DNA Helix, Data Cube, Heart, Ocean Wave, Saturn, Quantum Knot, Vortex, Lotus, Nautilus, Coil, Diamond, Infinity, Möbius, Solar Flare, Nebula, Crown, Gyroscope, Rainbow Sphere, Matrix Sphere, Moonlight.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("take_notes", "Meeting / lecture notes: action 'start' records the PC's sound (YouTube, online class, Zoom) and/or the microphone (a real class) and writes a live transcript on the Ultron Screen; action 'stop' writes a summary, key points, action items and a quiz. 'take notes', 'start notes for this lecture', 'stop notes'.",
        {"action": {"type": "string", "enum": ["start", "stop", "status"]},
         "source": {"type": "string", "enum": ["both", "speakers", "mic"], "description": "speakers = what the PC plays; mic = the room; both (default)"},
         "title": {"type": "string"}}, ["action"]),
    _fn("screen_copilot", "Screen copilot: while on, Ultron glances at the screen when it changes and speaks up only to help with errors, failed builds, bugs or problems. 'watch my screen', 'help me while I code', 'stop watching'. Only when the user asks.",
        {"on": {"type": "boolean"}, "minutes": {"type": "integer", "description": "how long (default 60)"}}, ["on"]),
    _fn("my_day", "The user's private day timeline: which apps and sites they used today (or 'yesterday' / 'YYYY-MM-DD'), active time, focus blocks - shown as a visual report on the Ultron Screen. 'what did I do today', 'how much time did I spend on YouTube', 'my screen time'.",
        {"day": {"type": "string"}}),
    _fn("day_tracking", "Pause (on=false) or resume (on=true) the private day timeline recording.", {"on": {"type": "boolean"}}, ["on"]),
    _fn("study", "Study tutor: make flashcards from a document (source = file path or name, e.g. a PDF) or a topic, shown on the Ultron Screen. 'make flashcards from my biology PDF', 'help me study the French revolution'.",
        {"source": {"type": "string"}, "topic": {"type": "string"}, "count": {"type": "integer"}}),
    _fn("quiz", "Voice quiz with spaced repetition on the latest (or named) flashcard deck. action 'next' asks a question; 'answer' grades the user's spoken answer (pass it in answer) and asks the next; 'stop' ends with the score.",
        {"action": {"type": "string", "enum": ["next", "answer", "stop"]}, "answer": {"type": "string"}, "deck": {"type": "string"}}, ["action"]),
    _fn("proactive", "Turn Ultron's proactive care on/off: kind 'breaks' (stand-up / water reminders after 50 min), 'load' (CPU overload warnings), 'morning' (automatic morning briefing) or 'all'.",
        {"kind": {"type": "string", "enum": ["all", "breaks", "load", "morning"]}, "on": {"type": "boolean"}}, ["on"]),
    _fn("phone_remote", "Phone remote: on=true shows a QR code; the user scans it with a phone on the same Wi-Fi to control Ultron from the phone. on=false disables it. 'connect my phone', 'control from my phone'.",
        {"on": {"type": "boolean"}}, ["on"]),
    _fn("interpreter", "Live interpreter: after this, everything spoken in language_a is said aloud in language_b and vice versa, so two people can talk (no wake word needed). 'be my interpreter between Telugu and English', 'translate my conversation with ... into Hindi'. on=false to stop.",
        {"on": {"type": "boolean"}, "language_a": {"type": "string"}, "language_b": {"type": "string"}}),
    _fn("focus_mode", "Start (or stop with on=false) a focus / Pomodoro session: a countdown ring around the atom in the command center and a spoken alert at the end. 'focus mode', 'start a pomodoro', 'I need to study for 50 minutes'.",
        {"minutes": {"type": "integer"}, "on": {"type": "boolean"}}),
    _fn("deep_think", "Expert-panel reasoning for hard questions: several different AI models solve it independently, then a judge compares them, fixes mistakes and writes one verified answer on the Ultron Screen. Use for tricky maths/logic, puzzles, proofs, careful analysis, comparisons, important decisions, or when the user says 'think deeply', 'are you sure', 'double-check'. Takes 15-60 s.",
        {"question": {"type": "string", "description": "the full question with every detail and number the user gave"},
         "use_web": {"type": "boolean", "description": "true if current real-world facts (prices, news, recent events) matter"}}, ["question"]),
    _fn("calculate", "Compute something exactly by running a short pure-Python snippet (math, statistics, fractions, decimal, datetime available; print the result). For arithmetic, percentages, interest/EMI, unit and date calculations, statistics, number puzzles. No files, network or system access.",
        {"code": {"type": "string", "description": "Python code that prints the answer, e.g. print(round(250000*0.085/12*(1+0.085/12)**60/((1+0.085/12)**60-1), 2))"}}, ["code"]),
    _fn("smart_home", "Control the user's smart home through Google Home: AC, lights, fans, plugs, TV, geysers, any device in their Google Home app. Send the command in plain English exactly as you'd say it to a Google Nest speaker, e.g. 'turn on the bedroom AC', 'set the AC to 24 degrees', 'set the AC to cool mode', 'turn off all the lights', 'is the fan on?'. Translate Telugu/Hindi requests into English first. Report Google's answer briefly.",
        {"command": {"type": "string", "description": "English command for Google Home"},
         "device": {"type": "string", "description": "the device's name, e.g. 'bedroom AC' (shown as a button in the command center)"}}, ["command"]),
    _fn("connect_google_home", "One-time Google Home sign-in (opens the browser for the user to allow access). Use when the user asks to connect / link Google Home, or after smart_home says it isn't connected and the user wants to set it up."),
    _fn("robot", "The little dancing robot that pops up in a screen corner and dances whenever music plays. 'dance' = dance right now; 'off' / 'on' = disable / enable it; 'left' / 'right' = which bottom corner.",
        {"action": {"type": "string", "enum": ["on", "off", "left", "right", "dance"]}}, ["action"]),
    _fn("set_reminder", "Reminder or alarm at a clock time or after some minutes, announced aloud when due (persists across restarts). 'Remind me to call Mom at 6 pm' -> at='18:00'. 'Wake me up at 6:30' / 'set an alarm for 7' -> alarm=true. 'Remind me in 20 minutes' -> in_minutes=20. Use the current date/time you were given to compute dates ('tomorrow at 9' -> 'YYYY-MM-DD 09:00').",
        {"text": {"type": "string", "description": "what to remind about (for alarms: a short label)"},
         "at": {"type": "string", "description": "24-hour 'HH:MM' (next occurrence) or 'YYYY-MM-DD HH:MM'"},
         "in_minutes": {"type": "number"},
         "repeat": {"type": "string", "enum": ["", "daily", "weekdays", "weekends", "weekly"]},
         "alarm": {"type": "boolean", "description": "true for an alarm (rings), false for a spoken reminder"}}, ["text"]),
    _fn("list_reminders", "List upcoming reminders and alarms."),
    _fn("cancel_reminder", "Cancel reminders/alarms matching words (text or time), or all=true for every one.",
        {"match": {"type": "string"}, "all": {"type": "boolean"}}),
    _fn("list_add", "Add items to a list (shopping, to-do, or any named list): 'add milk and eggs to my shopping list', 'put call the bank on my to-do list'.",
        {"items": {"type": "array", "items": {"type": "string"}}, "list_name": {"type": "string", "description": "shopping, to-do, or the list's name"}}, ["items"]),
    _fn("list_remove", "Remove / tick off items from a list.",
        {"items": {"type": "array", "items": {"type": "string"}}, "list_name": {"type": "string"}}, ["items"]),
    _fn("list_show", "Read out a list ('what's on my shopping list'); empty list_name = all lists.",
        {"list_name": {"type": "string"}}),
    _fn("list_clear", "Empty a whole list.", {"list_name": {"type": "string"}}, ["list_name"]),
    _fn("save_routine", "Save a routine: a trigger phrase that runs several steps. 'When I say good night, turn off the volume, lock the PC' -> trigger='good night', steps=['set volume to 0', 'lock the PC'].",
        {"trigger": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string"}, "description": "each step as a short spoken command"}}, ["trigger", "steps"]),
    _fn("run_routine", "Run a saved routine by its trigger phrase; then carry out the steps it returns.",
        {"trigger": {"type": "string"}}, ["trigger"]),
    _fn("delete_routine", "Delete a saved routine.", {"trigger": {"type": "string"}}, ["trigger"]),
    _fn("open_app", "Launch an installed desktop app or Windows settings page, e.g. spotify, vs code, discord, notepad, bluetooth settings.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("close_app", "Close a running desktop app by name, e.g. spotify, chrome, notepad.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("open_browser", "Open Google Chrome in one of the user's Chrome accounts, optionally at a URL. Use whenever the user mentions Chrome or the browser: 'open Chrome', 'open Chrome and open YouTube' (ONE call with url=youtube.com), 'open Gmail in my work account'. Leave account empty unless the user named one: an account chooser then pops up and you ask which one.",
        {"account": {"type": "string", "description": "Account name or number the user chose, e.g. 'work' or 'second'."},
         "url": {"type": "string"}}),
    _fn("open_website", "Open a website (site name like youtube/gmail/instagram, or a domain/URL) in Chrome, using the account picked last time. Only when the user did NOT mention Chrome/browser/an account - otherwise use open_browser with url.",
        {"site": {"type": "string"}}, ["site"]),
    _fn("web_search", "Show a page of Google or YouTube search results in the default browser. Only when the user wants to see results; to play a video use youtube_play.",
        {"query": {"type": "string"}, "engine": {"type": "string", "enum": ["google", "youtube"]}}, ["query"]),
    _fn("web_lookup", "Look up live information on the web and get a short factual answer: news, scores, prices, recent events, anything after your knowledge cutoff or that changes over time.",
        {"question": {"type": "string"}}, ["question"]),
    _fn("get_weather", "Current weather and today's forecast. Leave city empty for the user's location.",
        {"city": {"type": "string"}}),
    _fn("open_folder", "Open a folder in File Explorer: downloads, documents, desktop, pictures, music, videos, home, or a full path.",
        {"folder": {"type": "string"}}, ["folder"]),
    _fn("youtube_play", "Find a video or song on YouTube and start playing the top result in the Ultron browser window.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("youtube_control", "Control the video playing in the Jarvis YouTube window: like/unlike/dislike, subscribe, pause/play, skip ad, next video, seek, mute, fullscreen, captions, volume, speed. Like and subscribe need the user signed in to that window.",
        {"action": {"type": "string", "enum": [
            "like", "unlike", "dislike", "subscribe", "pause", "play", "skip_ad", "next_video", "forward_10s",
            "back_10s", "mute", "unmute", "fullscreen", "exit_fullscreen", "captions", "volume_up",
            "volume_down", "speed_up", "slow_down"]}}, ["action"]),
    _fn("youtube_sign_in", "Open YouTube in the Ultron browser so the user can sign in themselves (needed once before liking or subscribing)."),
    _fn("media_key", "Press a system media key: controls Spotify, the default browser, or whatever media is playing outside the Jarvis YouTube window.",
        {"action": {"type": "string", "enum": list(MEDIA_KEYS)}}, ["action"]),
    _fn("set_volume", "Get or change the PC's master volume. Pass level for an exact percent, change for a relative step (e.g. 10 or -10), mute true/false. No arguments reads the current volume.",
        {"level": {"type": "integer"}, "change": {"type": "integer"}, "mute": {"type": "boolean"}}),
    _fn("set_brightness", "Get or change screen brightness. Pass level (0-100) or change (e.g. 20 or -20). No arguments reads it.",
        {"level": {"type": "integer"}, "change": {"type": "integer"}}),
    _fn("system_status", "Live CPU, memory, disk, battery, uptime and the heaviest running apps."),
    _fn("current_time", "Exact current local date and time (India, from the PC clock)."),
    _fn("world_time", "Exact current time in another country or city. Pass the IANA timezone, e.g. 'America/New_York' for New York, 'Europe/London', 'Asia/Dubai', 'Asia/Tokyo', 'Australia/Sydney'.",
        {"timezone": {"type": "string"}}, ["timezone"]),
    _fn("set_timer", "Set a timer or reminder that Jarvis will announce aloud when it's due.",
        {"seconds": {"type": "integer"}, "label": {"type": "string", "description": "e.g. 'tea timer' or 'call mom'"}}, ["seconds"]),
    _fn("take_screenshot", "Capture all screens and save the image to Pictures\\Jarvis."),
    _fn("type_text", "Type text into the currently focused window/field (any language). Optionally press Enter after.",
        {"text": {"type": "string"}, "press_enter": {"type": "boolean"}}, ["text"]),
    _fn("press_keys", "Press a key or shortcut, e.g. 'enter', 'ctrl+s', 'alt+tab', 'win+e', 'ctrl+shift+t', 'f5', 'space'.",
        {"keys": {"type": "string"}, "times": {"type": "integer"}}, ["keys"]),
    _fn("scroll", "Scroll the window under the mouse up or down.",
        {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "integer"}}, ["direction"]),
    _fn("window_control", "List open windows, or switch to / minimize / maximize / restore / close a window by (part of) its title. Empty name = the active window.",
        {"action": {"type": "string", "enum": ["list", "switch", "minimize", "maximize", "restore", "close"]},
         "name": {"type": "string"}}, ["action"]),
    _fn("read_screen", "Look at the user's screen and answer a question about it (read text, describe what's open, find info).",
        {"question": {"type": "string"}}),
    _fn("click_on_screen", "Click a visible button, link, icon or text on screen, found by description (e.g. 'the Subscribe button', 'search box').",
        {"target": {"type": "string"}, "double": {"type": "boolean"}}, ["target"]),
    _fn("voice_mode", "Switch Ultron to speak in the user's own cloned voice (mine=true) or back to the Ultron voice (mine=false).",
        {"mine": {"type": "boolean"}}, ["mine"]),
    _fn("create", "Create anything the user asks you to make, write or generate - it is written live on the canvas screen inside the command center (not saved until the user agrees). kind: 'code' (a program in any language), 'webpage' (website / web app / HTML), 'game' (browser game), 'chart' (graph / visualisation of data), 'drawing' (picture / illustration / logo, as SVG), 'presentation' (slides), '3d' (anything three-dimensional: 3D globe / Earth, planets, solar system, atom, molecule, 3D model, 3D chart, rotating object), 'animation' (motion graphics, generative art, animated logo), 'simulation' (physics, orbits, algorithms, interactive science), 'music' (piano, drum machine, beat maker, sound visualiser), 'document' (essay, letter, notes, explanation, plan, table, study material, story...). Never type generated content with type_text and never read it aloud.",
        {"kind": {"type": "string", "enum": list(KINDS)},
         "request": {"type": "string", "description": "Exactly what to make, with every detail the user gave."},
         "language": {"type": "string", "description": "for code: python, java, c++, javascript... (empty = best choice)"},
         "title": {"type": "string", "description": "short title for the canvas tab"}}, ["kind", "request"]),
    _fn("revise_creation", "Change the thing currently on the canvas: 'make the button blue', 'add a dark mode', 'make the essay shorter', 'add comments', 'fix the bug'. Shown live as a new version.",
        {"change": {"type": "string", "description": "exactly what to change, with every detail"}}, ["change"]),
    _fn("research", "Deep research on a topic: several web searches, reads the top pages, and writes a structured report with sources on the canvas. For 'research X', 'find out everything about X', 'compare X and Y', 'write a report on X'.",
        {"topic": {"type": "string"}}, ["topic"]),
    _fn("explain_file", "Read a file (PDF, Word .docx, text, code...) by full path or by name words, and summarise / explain / answer about it on the canvas.",
        {"file": {"type": "string", "description": "full path, or words from the file name"},
         "instruction": {"type": "string", "description": "what to do, e.g. 'summarise it', 'explain this code', 'list the key dates'"}},
        ["file"]),
    _fn("look", "Take ONE webcam snapshot and answer about it ('look at this', 'what am I holding', 'how do I look', 'read this paper'). Only when the user asks you to look.",
        {"question": {"type": "string"}}),
    _fn("briefing", "Morning / daily briefing: time, weather, system status, today's top headlines and remembered items. For 'good morning', 'brief me', 'what's happening today'."),
    _fn("save_creation","Save what was just created on the canvas to Documents\\Jarvis Code. ONLY when the user agreed to save it (e.g. said yes to 'shall I save it?', or 'save it').",
        {"name": {"type": "string", "description": "optional file name the user asked for, without extension"}}),
    _fn("canvas_control","Control what's on the command center canvas: full screen, exit full screen, show the code, show the preview, close it, open it in VS Code, or run it (Python/JavaScript, only when the user asks).",
        {"action": {"type": "string", "enum": ["fullscreen", "exit_fullscreen", "show_code", "show_preview", "close",
                                                "open_in_vscode", "run"]}}, ["action"]),
    _fn("do_task","Autopilot for multi-step jobs inside apps or websites that no other tool covers: Jarvis looks at the screen and clicks/types step by step until done (e.g. 'open WhatsApp and search for Mom', 'in Settings turn on dark mode', 'fill the search box on this page with X'). Slower (several seconds per step), so prefer direct tools when one fits.",
        {"goal": {"type": "string", "description": "The complete goal, in English, with all names/details the user gave."}}, ["goal"]),
    _fn("remember", "Save a fact the user wants remembered permanently (preferences, dates, names...).",
        {"fact": {"type": "string"}}, ["fact"]),
    _fn("forget", "Delete remembered facts about a topic.", {"about": {"type": "string"}}, ["about"]),
    _fn("find_files", "Search the user's Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive and D: for files by name. open_first=true opens the newest match.",
        {"query": {"type": "string", "description": "words from the file name, e.g. 'resume pdf'"},
         "open_first": {"type": "boolean"}}, ["query"]),
    _fn("open_file", "Open a file by its full path (e.g. one returned by find_files).", {"path": {"type": "string"}}, ["path"]),
    _fn("gestures", "Turn hand-gesture control on or off (webcam). 'turn on gestures', 'gesture mode', 'stop gestures'. mouse=true ONLY when the user explicitly asks for their hand to move/click the mouse cursor ('control my mouse with gestures'); never otherwise.",
        {"on": {"type": "boolean"}, "mouse": {"type": "boolean"}}, ["on"]),
    _fn("set_theme", "Switch Jarvis's look: theme 'cinema' (cinematic 3D orb command center), 'ios' (Apple-style with Iron Man accents) or 'ironman' (full red/gold HUD), and/or appearance 'light' or 'dark' ('dark mode' / 'light mode').",
        {"theme": {"type": "string", "enum": ["cinema", "ios", "ironman"]},
         "appearance": {"type": "string", "enum": ["light", "dark"]}}),
    _fn("show_dashboard","Open the Ultron command center dashboard (live HUD with system vitals, conversation log and controls). For 'open dashboard', 'command center', 'show your interface', 'open HUD'."),
    _fn("learn_my_voice","Record the user reading a passage aloud for about 25 seconds to improve how their cloned voice sounds. Use when they say 'learn my voice', 'my voice doesn't sound like me', 'train my voice'."),
    _fn("show_desktop", "Minimise all windows / show the desktop (toggles)."),
    _fn("lock_pc", "Lock the PC immediately."),
    _fn("sleep_pc", "Put the PC to sleep."),
    _fn("power_off", "Shut down or restart the COMPUTER - ONLY when the user explicitly says computer / PC / laptop / Windows (e.g. 'shut down my PC'). Plain 'shut down' means switch Ultron off (ultron_power). Short countdown (default 10 s), no 'are you sure'.",
        {"action": {"type": "string", "enum": ["shutdown", "restart", "cancel"]}, "seconds": {"type": "integer"}}, ["action"]),
]

import everyday  # noqa: E402  (reminders, alarms, lists, routines)
import smarthome  # noqa: E402  (Google Home devices)
import deepthink  # noqa: E402  (expert panel + exact computation)
import notes as notes_mod  # noqa: E402  (meeting & lecture notes)
import copilot  # noqa: E402  (screen copilot)
import daylog  # noqa: E402  (day timeline)
import tutor  # noqa: E402  (flashcards + voice quiz)
import care  # noqa: E402  (proactive care)
import phone  # noqa: E402  (phone remote)
import vault  # noqa: E402  (long-term memory vault)
import telegrambot  # noqa: E402  (Telegram anywhere)
import team  # noqa: E402  (the Stark AI team)
import gmail  # noqa: E402  (Gmail assistant - drafts only)
import webpublish  # noqa: E402  (publish creations live - GitHub Pages)
import classroom  # noqa: E402  (AI classroom)
import videovision  # noqa: E402  (watch short videos: camera, screen, files)
import interview as interview_mod  # noqa: E402  (get-to-know-you interview)
import skills  # noqa: E402  (reusable task know-how)
import gworkspace  # noqa: E402  (Google Calendar + Tasks)
import brains  # noqa: E402  (Claude / ChatGPT / local models)
import apikeys  # noqa: E402  (which API keys are set / missing)
import departments  # noqa: E402  (command layer: self-designed departments)


def show_memory_graph() -> str:
    ensure_dashboard()
    time.sleep(0.5)
    publish({"type": "memory_graph"})
    return "OK: the memory graph is open in the command center (drag to explore, hover to read)."


def smart_home(command: str, device: str = "") -> str:
    return smarthome.command(command, device)


def connect_google_home() -> str:
    return smarthome.connect()

FUNCS = {
    "take_notes": notes_mod.notes, "screen_copilot": copilot.screen_copilot, "my_day": daylog.my_day,
    "day_tracking": daylog.set_tracking, "study": tutor.study, "quiz": tutor.quiz, "proactive": care.proactive,
    "phone_remote": phone.phone_remote, "recall": vault.recall, "remember_note": vault.remember_note,
    "open_vault": vault.open_vault, "journal": vault.journal_for,
    "connect_telegram": telegrambot.connect_telegram, "ask_agent": team.ask_agent, "overnight_shift": team.overnight_shift,
    "connect_gmail": gmail.connect_gmail, "email_triage": gmail.email_triage, "email_search": gmail.email_search,
    "email_draft": gmail.email_draft, "publish_website": webpublish.publish_website,
    "show_memory_graph": show_memory_graph, "watch": videovision.watch,
    "architect_departments": departments.architect, "activate_departments": departments.activate_departments,
    "list_departments": departments.list_departments, "ask_department": departments.ask_department,
    "ultron_power": ultron_power, "study_mode": study_mode, "ui_theme": ui_theme, "api_keys": api_keys, "interview": interview_mod.interview, "save_skill": skills.save_skill, "list_skills": skills.list_skills,
    "delete_skill": skills.delete_skill, "connect_google_calendar": gworkspace.connect_google_calendar,
    "calendar_agenda": gworkspace.calendar_agenda, "calendar_add": gworkspace.calendar_add,
    "tasks_list": gworkspace.tasks_list, "task_add": gworkspace.task_add,
    "connect_brain": brains.connect_brain, "brain_status": brains.brain_status, "teach": classroom.teach, "stop_class": classroom.stop_class,
    "deep_think": deepthink.deep_think, "calculate": deepthink.calculate,
    "robot": robot, "focus_mode": focus_mode, "interpreter": interpreter, "smart_home": smart_home, "connect_google_home": connect_google_home,
    "set_reminder": everyday.set_reminder, "schedule_task": everyday.schedule_task, "orb_style": orb_style, "list_reminders": everyday.list_reminders,
    "cancel_reminder": everyday.cancel_reminder, "list_add": everyday.list_add, "list_remove": everyday.list_remove,
    "list_show": everyday.list_show, "list_clear": everyday.list_clear, "save_routine": everyday.save_routine,
    "run_routine": everyday.run_routine, "delete_routine": everyday.delete_routine,
    "open_app": open_app, "close_app": close_app, "open_website": open_website, "web_search": web_search,
    "open_browser": open_browser,
    "web_lookup": web_lookup, "get_weather": get_weather, "open_folder": open_folder,
    "youtube_play": youtube_play, "youtube_control": youtube_control, "youtube_sign_in": youtube_sign_in,
    "media_key": media_key, "set_volume": set_volume, "set_brightness": set_brightness,
    "system_status": system_status, "current_time": current_time, "world_time": world_time, "set_timer": set_timer,
    "take_screenshot": take_screenshot, "show_desktop": show_desktop, "lock_pc": lock_pc,
    "sleep_pc": sleep_pc, "power_off": power_off, "type_text": type_text, "press_keys": press_keys,
    "scroll": scroll, "window_control": window_control, "read_screen": read_screen,
    "click_on_screen": click_on_screen, "voice_mode": voice_mode, "learn_my_voice": learn_my_voice,
    "show_dashboard": show_dashboard, "do_task": do_task, "remember": remember, "forget": forget,
    "find_files": find_files, "open_file": open_file, "write_code": write_code, "create": create,
    "canvas_control": canvas_control, "save_creation": lambda name="": save_creation(name),
    "revise_creation": revise_creation, "research": research, "explain_file": explain_file, "look": look,
    "briefing": briefing, "gestures": lambda on: gestures(on),
    "set_theme":lambda theme=None, appearance=None: set_theme(theme, appearance),
}

# Set by the app: switches the island + dashboard theme.
set_theme = lambda theme=None, appearance=None: "FAILED: themes are not available."

# Short status line + Segoe Fluent Icons glyph shown on the Dynamic Island while a tool runs.
_ICONS = {
    "open_app": "\uE8A7", "close_app": "\uE711", "open_browser": "\uE774", "open_website": "\uE774", "web_search": "\uE721",
    "web_lookup": "\uE721", "get_weather": "\uE706", "open_folder": "\uE8B7", "youtube_play": "\uE768",
    "youtube_control": "\uE768", "youtube_sign_in": "\uE77B", "media_key": "\uE768", "set_volume": "\uE767",
    "set_brightness": "\uE706", "system_status": "\uE9D9", "current_time": "\uE823", "set_timer": "\uE916",
    "take_screenshot": "\uE722", "show_desktop": "\uE7F4", "lock_pc": "\uE72E", "sleep_pc": "\uE708",
    "power_off": "\uE7E8", "type_text": "\uE765", "press_keys": "\uE765", "scroll": "\uE8CB",
    "window_control": "\uE737", "read_screen": "\uE7B3", "click_on_screen": "\uE8B0", "voice_mode": "\uE720",
    "do_task": "\uE945", "remember": "\uE734", "forget": "\uE74D", "find_files": "\uE721", "open_file": "\uE8E5",
    "set_theme": "\uE790", "set_reminder": "\uEA8F", "list_reminders": "\uEA8F", "cancel_reminder": "\uEA8F",
    "list_add": "\uE7BF", "list_remove": "\uE7BF", "list_show": "\uE7BF", "list_clear": "\uE7BF",
    "save_routine": "\uE945", "run_routine": "\uE945", "delete_routine": "\uE945",
    "smart_home": "\uE80F", "connect_google_home": "\uE80F",
}
_ACTION_ICONS = {"like": "\uEB51", "unlike": "\uEB51", "dislike": "\uE8E0", "subscribe": "\uE8FA",
                 "pause": "\uE769", "mute": "\uE74F", "volume_down": "\uE993", "volume_up": "\uE995"}


def describe(name: str, args: dict):
    """(icon, label) for the island while a tool is running."""
    a = args
    labels = {
        "open_app": lambda: f"Opening {a.get('name', '')}",
        "close_app": lambda: f"Closing {a.get('name', '')}",
        "open_browser": lambda: f"Opening Chrome · {a['account']}" if a.get("account") else "Opening Chrome",
        "open_website": lambda: f"Opening {a.get('site', '')}",
        "web_search": lambda: f"Searching {a.get('query', '')}",
        "web_lookup": lambda: "Searching the web",
        "get_weather": lambda: f"Weather {a.get('city') or 'nearby'}",
        "open_folder": lambda: f"Opening {a.get('folder', '')}",
        "youtube_play": lambda: f"Playing {a.get('query', '')}",
        "youtube_control": lambda: a.get("action", "").replace("_", " ").capitalize(),
        "youtube_sign_in": lambda: "Opening YouTube sign-in",
        "media_key": lambda: a.get("action", "").replace("_", " ").capitalize(),
        "set_volume": lambda: "Muting" if a.get("mute") else (f"Volume {a['level']}%" if "level" in a else "Adjusting volume"),
        "set_brightness": lambda: f"Brightness {a['level']}%" if "level" in a else "Adjusting brightness",
        "system_status": lambda: "Running diagnostics",
        "current_time": lambda: "Checking the time",
        "world_time": lambda: f"Time in {a.get('timezone', '').split('/')[-1].replace('_', ' ')}",
        "set_timer": lambda: f"Timer · {a.get('seconds', 0) // 60} min" if a.get("seconds", 0) >= 60 else f"Timer · {a.get('seconds', 0)} s",
        "take_screenshot": lambda: "Capturing screen",
        "show_desktop": lambda: "Showing desktop",
        "lock_pc": lambda: "Locking PC",
        "sleep_pc": lambda: "Sleeping",
        "power_off": lambda: f"{a.get('action', '').capitalize()}",
        "type_text": lambda: "Typing",
        "press_keys": lambda: f"Pressing {a.get('keys', '')}",
        "scroll": lambda: f"Scrolling {a.get('direction', '')}",
        "window_control": lambda: f"{a.get('action', '').capitalize()} {a.get('name', '')}".strip(),
        "read_screen": lambda: "Reading your screen",
        "click_on_screen": lambda: f"Clicking {a.get('target', '')}",
        "voice_mode": lambda: "Switching to your voice" if a.get("mine") else "Switching to Ultron voice",
        "learn_my_voice": lambda: "Learning your voice",
        "show_dashboard": lambda: "Opening command center",
        "do_task": lambda: "Autopilot engaged",
        "write_code": lambda: f"Writing {a.get('language') or ''} code".replace("  ", " "),
        "create": lambda: {"code": f"Writing {a.get('language') or ''} code".replace("  ", " "),
                           "webpage": "Building the web page", "game": "Building the game",
                           "chart": "Drawing the chart", "drawing": "Drawing it",
                           "presentation": "Making the slides", "document": "Writing the document",
                           "3d": "Building the 3D scene", "animation": "Animating it",
                           "simulation": "Building the simulation", "music": "Building the music app"
                           }.get(a.get("kind"), "Creating it"),
        "save_creation": lambda: "Saving it",
        "revise_creation": lambda: "Making the change",
        "research": lambda: f"Researching {a.get('topic', '')}",
        "explain_file": lambda: f"Reading {a.get('file', '')}",
        "look": lambda: "Looking through the camera",
        "briefing": lambda: "Preparing your briefing",
        "gestures": lambda: "Gesture control on" if a.get("on") else "Gesture control off",
        "canvas_control": lambda: {"fullscreen": "Full screen", "exit_fullscreen": "Exiting full screen",
                                   "show_code": "Showing the code", "show_preview": "Showing the preview",
                                   "close": "Closing the canvas", "open_in_vscode": "Opening in VS Code",
                                   "run": "Running it"}.get(a.get("action"), "Canvas"),
        "remember": lambda: "Saving to memory",
        "forget": lambda: "Forgetting",
        "find_files": lambda: f"Searching files: {a.get('query', '')}",
        "open_file": lambda: "Opening file",
        "publish_website": lambda: "Publishing to the web" if a.get("confirm") else "Preparing to publish",
        "study_mode": lambda: "Opening Study Mode", "ui_theme": lambda: f"Theme · {a.get('name', '')}",
        "architect_departments": lambda: "Architecting departments", "activate_departments": lambda: "Activating departments",
        "list_departments": lambda: "Your departments", "ask_department": lambda: f"{a.get('department', '')} department working",
        "interview": lambda: "Interview" if a.get("action") != "answer" else "Noting your answer",
        "save_skill": lambda: f"Learning skill · {a.get('name', '')}", "list_skills": lambda: "My skills",
        "delete_skill": lambda: "Forgetting skill", "connect_google_calendar": lambda: "Connecting Google Calendar",
        "calendar_agenda": lambda: "Checking your calendar", "calendar_add": lambda: f"Adding to calendar · {a.get('title', '')}",
        "tasks_list": lambda: "Your tasks", "task_add": lambda: f"New task · {a.get('title', '')}",
        "connect_brain": lambda: f"Connecting {a.get('provider', '')} brain", "brain_status": lambda: "Brain status",
        "watch": lambda: {"screen": "Watching the screen", "camera": "Watching through the camera"}.get(a.get("source", "camera"), "Watching the video"),
        "show_memory_graph": lambda: "Memory graph", "teach": lambda: f"Class · {a.get('topic') or 'your document'}",
        "stop_class": lambda: "Ending the class",
        "connect_telegram": lambda: "Connecting Telegram",
        "ask_agent": lambda: {"friday": "F.R.I.D.A.Y. researching", "edith": "E.D.I.T.H. scanning", "karen": "KAREN planning"}.get(a.get("agent"), "Team"),
        "overnight_shift": lambda: "Overnight shift running", "connect_gmail": lambda: "Connecting Gmail",
        "email_triage": lambda: "Sorting your inbox", "email_search": lambda: "Searching email", "email_draft": lambda: "Drafting a reply",
        "remember_note": lambda: "Saving to memory vault", "recall": lambda: "Searching memory",
        "journal": lambda: "Reading the journal", "open_vault": lambda: "Opening memory vault",
        "take_notes": lambda: {"start": "Taking notes", "stop": "Writing up the notes"}.get(a.get("action"), "Notes"),
        "screen_copilot": lambda: "Screen copilot on" if a.get("on", True) else "Screen copilot off",
        "my_day": lambda: "Your day timeline",
        "day_tracking": lambda: "Day tracking " + ("on" if a.get("on") else "paused"),
        "study": lambda: "Making flashcards",
        "quiz": lambda: {"answer": "Checking your answer", "stop": "Ending the quiz"}.get(a.get("action"), "Quiz"),
        "proactive": lambda: "Proactive care " + ("on" if a.get("on") else "off"),
        "phone_remote": lambda: "Phone remote on" if a.get("on", True) else "Phone remote off",
        "interpreter": lambda: f"Interpreter · {a.get('language_a', '')} ⇄ {a.get('language_b', '')}" if a.get("on", True) else "Interpreter off",
        "focus_mode": lambda: f"Focus · {a.get('minutes', 25)} min" if a.get("on", True) else "Ending focus",
        "deep_think": lambda: "Deep Think · expert panel",
        "calculate": lambda: "Calculating",
        "smart_home": lambda: f"Home · {a.get('command', '')}",
        "connect_google_home": lambda: "Connecting Google Home",
        "robot": lambda: {"dance": "Robot dancing", "off": "Robot off", "on": "Robot on"}.get(a.get("action"), "Moving the robot"),
        "schedule_task": lambda: f"Scheduled · {a.get('at') or ('in ' + str(a.get('in_minutes', '')) + ' min')}",
        "orb_style": lambda: f"Orb style · {a.get('name', '')}",
        "set_reminder": lambda: ("Alarm · " if a.get("alarm") else "Reminder · ") + (a.get("at") or f"in {a.get('in_minutes', '')} min"),
        "list_reminders": lambda: "Checking reminders",
        "cancel_reminder": lambda: "Cancelling reminder",
        "list_add": lambda: f"Adding to {a.get('list_name') or 'to-do'} list",
        "list_remove": lambda: f"Updating {a.get('list_name') or 'to-do'} list",
        "list_show": lambda: f"{(a.get('list_name') or 'Your').capitalize()} list",
        "list_clear": lambda: f"Clearing {a.get('list_name', '')} list",
        "save_routine": lambda: f"Saving routine · {a.get('trigger', '')}",
        "run_routine": lambda: f"Routine · {a.get('trigger', '')}",
        "delete_routine": lambda: "Deleting routine",
        "set_theme": lambda: f"Theme: {a.get('theme') or ''} {a.get('appearance') or ''}".strip(),
    }
    icon = _ACTION_ICONS.get(a.get("action"), _ICONS.get(name, "\uE945"))
    try:
        label = labels.get(name, lambda: name)()
    except Exception:
        label = name
    return icon, label


def run_tool(name: str, raw_args: str):
    """Run a tool; returns (args_dict, result_string). Never raises."""
    if turn_unclear and name in RISKY:
        # never shut down / restart / sleep / lock / close things (or cancel them) on a guess from unclear audio
        return {}, ("FAILED: I couldn't hear the user clearly, so I must not do this. Ask them to repeat the "
                    "command clearly.")
    func = FUNCS.get(name)
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {}, "FAILED: invalid arguments."
    if func is None:
        return args, f"FAILED: unknown tool {name}."
    try:
        return args, func(**args)
    except Exception as e:
        log.exception("tool %s failed", name)
        return args, f"FAILED: {type(e).__name__}: {e}"


_remember_list = FUNCS["remember"]


def _remember_both(fact: str, *a, **k):
    out = _remember_list(fact, *a, **k)
    try:
        vault.remember_note(fact, "Facts")
    except Exception:
        pass
    return out


FUNCS["remember"] = _remember_both
YT.open_url = _open_url      # YouTube in the user's Chrome when browser automation is unavailable
