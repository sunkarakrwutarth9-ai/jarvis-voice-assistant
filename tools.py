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


def power_off(action: str) -> str:
    if action == "cancel":
        r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
        return "OK: shutdown cancelled." if r.returncode == 0 else "OK: no shutdown was scheduled."
    flag = {"shutdown": "/s", "restart": "/r"}.get(action)
    if flag is None:
        return f"FAILED: unknown power action '{action}'."
    subprocess.run(["shutdown", flag, "/t", "60"], capture_output=True)
    return f"OK: {action} scheduled in 60 seconds. Say 'cancel shutdown' to stop it."


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
    prompt = (f"Create {spec}.\n\nThe user's request:\n{request}\n\n"
              + (f"Use {language}.\n" if language and kind == "code" else "")
              + f"Reply with ONLY one fenced block that starts with ```{fence} - no text before or after it.")
    return _canvas_generate(kind, title or request[:60], fixed_ext, lang, prompt)


def _canvas_generate(kind, title, fixed_ext, lang, prompt, note=""):
    """Stream a generation live into the canvas; keep it in memory (unsaved). Returns the tool result."""
    cid = f"c{int(time.time() * 1000) % 10_000_000}"
    ensure_dashboard()
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


set_gestures = None   # set by jarvis.py: starts/stops the system-wide gesture engine; returns '' or an error


def gestures(on: bool) -> str:
    """System-wide hand-gesture control through the webcam (frames stay on this PC)."""
    if set_gestures is None:
        return "FAILED: gesture control is not available."
    err = set_gestures(bool(on))
    if err:
        return f"FAILED: {err}"
    return ("OK: gesture control is on, in every app: point to move the mouse and pinch to click, open palm = talk, "
            "fist = stop / pause, thumbs up = yes, victory = full screen, swipe = next / previous." if on
            else "OK: gesture control is off.")


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
    facts = memories()
    if facts:
        parts.append("Remembered: " + "; ".join(m["fact"] for m in facts[-8:]))
    return "OK: " + "\n".join(parts) + ("\n(Brief the user warmly in 4-6 short spoken sentences: greeting, weather, "
                                        "2-3 headlines, today's reminders, anything remembered that matters today, battery.)")


def save_creation(name: str = "", cid=None) -> str:
    """Save a creation to Documents\\Jarvis Code - only after the user agreed."""
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
    ensure_dashboard()
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
    _fn("youtube_play", "Find a video or song on YouTube and start playing the top result in the Jarvis browser window.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("youtube_control", "Control the video playing in the Jarvis YouTube window: like/unlike/dislike, subscribe, pause/play, skip ad, next video, seek, mute, fullscreen, captions, volume, speed. Like and subscribe need the user signed in to that window.",
        {"action": {"type": "string", "enum": [
            "like", "unlike", "dislike", "subscribe", "pause", "play", "skip_ad", "next_video", "forward_10s",
            "back_10s", "mute", "unmute", "fullscreen", "exit_fullscreen", "captions", "volume_up",
            "volume_down", "speed_up", "slow_down"]}}, ["action"]),
    _fn("youtube_sign_in", "Open YouTube in the Jarvis browser so the user can sign in themselves (needed once before liking or subscribing)."),
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
    _fn("voice_mode", "Switch Jarvis to speak in the user's own cloned voice (mine=true) or back to the Jarvis voice (mine=false).",
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
    _fn("gestures", "Turn hand-gesture control of the command center on or off (webcam). 'turn on gestures', 'gesture mode', 'stop gestures'.",
        {"on": {"type": "boolean"}}, ["on"]),
    _fn("set_theme", "Switch Jarvis's look: theme 'cinema' (cinematic 3D orb command center), 'ios' (Apple-style with Iron Man accents) or 'ironman' (full red/gold HUD), and/or appearance 'light' or 'dark' ('dark mode' / 'light mode').",
        {"theme": {"type": "string", "enum": ["cinema", "ios", "ironman"]},
         "appearance": {"type": "string", "enum": ["light", "dark"]}}),
    _fn("show_dashboard","Open the J.A.R.V.I.S. command center dashboard (live HUD with system vitals, conversation log and controls). For 'open dashboard', 'command center', 'show your interface', 'open HUD'."),
    _fn("learn_my_voice","Record the user reading a passage aloud for about 25 seconds to improve how their cloned voice sounds. Use when they say 'learn my voice', 'my voice doesn't sound like me', 'train my voice'."),
    _fn("show_desktop", "Minimise all windows / show the desktop (toggles)."),
    _fn("lock_pc", "Lock the PC immediately."),
    _fn("sleep_pc", "Put the PC to sleep."),
    _fn("power_off", "Shut down or restart the PC with a 60-second delay, or cancel a pending shutdown. Only when the user clearly asks.",
        {"action": {"type": "string", "enum": ["shutdown", "restart", "cancel"]}}, ["action"]),
]

import everyday  # noqa: E402  (reminders, alarms, lists, routines)

FUNCS = {
    "set_reminder": everyday.set_reminder, "list_reminders": everyday.list_reminders,
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
        "voice_mode": lambda: "Switching to your voice" if a.get("mine") else "Switching to Jarvis voice",
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
