"""Local web dashboard for Jarvis: http://localhost:7777

- GET  /            the dashboard page (dashboard.html)
- GET  /events      live Server-Sent Events: state changes, conversation, tool runs, vitals, voice levels
- GET  /api/state   snapshot for a freshly opened page
- POST /api/command {"text": "..."}   run a typed command as if spoken
- POST /api/action  {"action": "talk|me|mute|new_chat|stop"}

Only listens on 127.0.0.1. Requests must carry a localhost Host header (blocks DNS-rebinding), and
POSTs need the custom header X-Jarvis: 1, which other websites can't send without a CORS preflight
(which this server never approves).
"""

import json
import logging
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

log = logging.getLogger("jarvis.server")
HERE = Path(__file__).resolve().parent
PORT = 7777
THEME = {"name": "ios"}                # set by the app; picks which dashboard page is served
PAGES = {"ios": "dashboard_ios.html", "ironman": "dashboard.html", "cinema": "dashboard_cinema.html"}
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


class Hub:
    """Fan-out of live events to every open dashboard tab."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()
        self._views = {}             # dashboard tab id -> is it visible (not minimised / hidden)?
        self.on_visibility = lambda any_visible: None
        self.history = []            # recent conversation events, replayed to new tabs
        self.snapshot = {"state": "idle", "title": "", "body": "", "me": False, "muted": False, "ranking": [],
                         "appearance": "light"}

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @property
    def listeners(self):
        return len(self._subs)

    def any_visible(self):
        with self._lock:
            return any(self._views.values())

    def set_view(self, view_id, visible):
        """Track whether any dashboard is on screen (visible=None removes a closed tab)."""
        with self._lock:
            before = any(self._views.values())
            if visible is None:
                self._views.pop(view_id, None)
            else:
                self._views[view_id] = bool(visible)
            after = any(self._views.values())
        if before != after:
            self.on_visibility(after)

    def publish(self, event: dict):
        event.setdefault("ts", time.time())
        kind = event.get("type")
        if kind == "state":
            self.snapshot.update({k: event.get(k, "") for k in ("state", "title", "body")})
        elif kind in ("me", "muted"):
            self.snapshot[kind] = event["on"]
        elif kind == "ranking":
            self.snapshot["ranking"] = event["models"]
        elif kind == "theme":
            self.snapshot["appearance"] = event.get("appearance", "light")
        if kind in ("user", "reply", "tool"):
            self.history = (self.history + [event])[-60:]
        data = json.dumps(event, ensure_ascii=False)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis"
    hub: Hub = None
    on_command = None
    on_action = None

    def log_message(self, *args):          # keep the console quiet
        pass

    def _host_ok(self):
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self.send_error(403)
            return False
        return True

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return
        if self.path in ("/", "/index.html"):
            page = HERE / PAGES.get(THEME["name"], "dashboard.html")
            if not page.exists():
                page = HERE / "dashboard.html"
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/avatar.jpg":
            pic = HERE / "assets" / "avatar_photo" / "p00.jpg"
            if pic.exists():
                self._send(200, pic.read_bytes(), "image/jpeg")
            else:
                self.send_error(404)
        elif self.path == "/api/state":
            body = dict(self.hub.snapshot, history=self.hub.history)
            self._send(200, json.dumps(body, ensure_ascii=False).encode())
        elif self.path.startswith("/events"):
            view_id = self.path.partition("id=")[2][:40] or None
            self._stream(view_id)
        elif self.path.startswith("/vendor/"):
            self._static(self.path[len("/vendor/"):])
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    STATIC_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm",
                    ".task": "application/octet-stream"}

    def _static(self, rel):
        """Serve a file from web/vendor (3D + hand-tracking libraries) - nothing outside that folder."""
        root = (HERE / "web" / "vendor").resolve()
        target = (root / rel.split("?")[0]).resolve()
        ctype = self.STATIC_TYPES.get(target.suffix)
        if root not in target.parents or ctype is None or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._host_ok():
            return
        if self.headers.get("X-Jarvis") != "1":
            self.send_error(403)
            return
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 10000)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        if self.path == "/api/command" and isinstance(data.get("text"), str) and data["text"].strip():
            self.on_command(data["text"].strip()[:500])
            self._send(200, b'{"ok":true}')
        elif self.path == "/api/action" and data.get("action") in ("talk", "me", "mute", "new_chat", "stop",
                                                                   "canvas_vscode", "canvas_run", "canvas_save"):
            self.on_action(data["action"], data)
            self._send(200, b'{"ok":true}')
        elif self.path == "/api/visibility" and isinstance(data.get("id"), str):
            self.hub.set_view(data["id"][:40], bool(data.get("visible")))
            self._send(200, b'{"ok":true}')
        else:
            self.send_error(400)

    def _stream(self, view_id=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.hub.subscribe()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {data}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.hub.unsubscribe(q)
            if view_id:
                self.hub.set_view(view_id, None)   # tab closed: the island comes back


def _vitals_loop(hub: Hub, level_fns):
    """Publish system vitals every 2 s and voice levels ~12 times a second (only while a tab is open)."""
    last_net = psutil.net_io_counters()
    last_t = time.monotonic()
    next_vitals = next_procs = 0.0
    top_procs = []
    psutil.cpu_percent(None)
    quiet = False
    while True:
        time.sleep(0.1)
        if hub.listeners == 0 or not hub.any_visible():
            continue                               # nobody is looking: do no work at all
        mic, speech = level_fns()
        silent = mic < 0.01 and speech < 0.01
        if not (silent and quiet):                 # one zero after sound, then nothing until sound returns
            hub.publish({"type": "lv", "mic": round(mic, 3), "speech": round(speech, 3)})
        quiet = silent
        now = time.monotonic()
        if now < next_vitals:
            continue
        next_vitals = now + 2.0
        net = psutil.net_io_counters()
        dt = max(0.001, now - last_t)
        down = (net.bytes_recv - last_net.bytes_recv) / dt
        up = (net.bytes_sent - last_net.bytes_sent) / dt
        last_net, last_t = net, now
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        bat = psutil.sensors_battery()
        # Listing every process's memory is expensive on Windows (~80% of Jarvis's idle CPU when done
        # every 2 s), so refresh the "Active processes" list only every 15 s, and only if a tab is on screen.
        if now >= next_procs and hub.any_visible():
            next_procs = now + 15.0
            procs = []
            for p in psutil.process_iter(["name", "memory_info"]):
                if p.info["memory_info"]:
                    procs.append((p.info["memory_info"].rss, p.info["name"]))
            procs.sort(reverse=True)
            top_procs = [{"name": n, "gb": round(r / 1e9, 2)} for r, n in procs[:5]]
        hub.publish({
            "type": "vitals",
            "cpu": psutil.cpu_percent(None), "ram": mem.percent,
            "ram_used": round(mem.used / 1e9, 1), "ram_total": round(mem.total / 1e9, 1),
            "disk": disk.percent, "disk_free": round(disk.free / 1e9),
            "battery": round(bat.percent) if bat else None, "plugged": bat.power_plugged if bat else None,
            "down": down, "up": up, "uptime": time.time() - psutil.boot_time(),
            "procs": top_procs,
        })


def start(hub: Hub, on_command, on_action, level_fns):
    _Handler.hub, _Handler.on_command, _Handler.on_action = hub, staticmethod(on_command), staticmethod(on_action)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    except OSError as e:
        log.warning("dashboard unavailable: %s", e)
        return None
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="dashboard").start()
    threading.Thread(target=_vitals_loop, args=(hub, level_fns), daemon=True, name="vitals").start()
    log.info("dashboard on http://localhost:%d", PORT)
    return httpd
