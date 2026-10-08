"""Phone remote: your phone becomes Ultron's remote control over your home Wi-Fi.

Scan the QR code on the Ultron Screen -> a mobile page: live status, the conversation, type (or use the
keyboard's mic to dictate) commands, TALK (the PC listens), STOP, media and volume buttons, your
reminders and lists, and replies spoken on the phone.

Security: off until switched on; listens on the local network only and refuses every non-private
address; every request must carry a random 128-bit key (inside the QR code), which changes each
time the remote is switched on. Switch it off any time ("turn off phone remote").
"""

import base64
import io
import ipaddress
import json
import logging
import queue
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("jarvis.phone")
PORT = 7778
FORWARD = {"state", "user", "reply", "tool", "everyday", "weather", "focus", "canvas", "notes", "interp", "copilot", "ui_theme"}
ctx = {"hub": None, "on_command": None, "on_action": None, "state": None, "save": None}
_srv = {"server": None, "token": None}


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _H(BaseHTTPRequestHandler):
    server_version = "Ultron"

    def log_message(self, *a):
        pass

    def _ok_client(self):
        ip = ipaddress.ip_address(self.client_address[0])
        if not (ip.is_private or ip.is_loopback):
            self.send_error(403)
            return False
        qs = parse_qs(urlparse(self.path).query)
        tok = self.headers.get("X-Ultron-Key") or (qs.get("k") or [""])[0]
        if not _srv["token"] or not secrets.compare_digest(tok, _srv["token"]):
            self.send_error(403, "wrong or missing key - scan the QR code again")
            return False
        return True

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._ok_client():
            return
        path = urlparse(self.path).path
        hub = ctx["hub"]
        if path == "/themes.js":
            from pathlib import Path as _P
            self._send(200, (_P(__file__).resolve().parent / "web" / "themes.js").read_bytes(), "text/javascript")
            return
        if path == "/api/ui_theme":
            self._send(200, json.dumps({"id": hub.snapshot.get("ui_theme") or "ironman-13"}).encode())
            return
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            body = dict(hub.snapshot, history=[e for e in hub.history if e.get("type") in ("user", "reply")][-30:],
                        everyday=hub.everyday())
            self._send(200, json.dumps(body, ensure_ascii=False).encode())
        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q = hub.subscribe()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while _srv["server"] is not None:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if json.loads(data).get("type") in FORWARD:
                        if '"type": "canvas"' in data or '"type":"canvas"' in data:
                            e = json.loads(data)
                            data = json.dumps({"type": "canvas", "title": e.get("title"), "kind": e.get("kind")})
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
            except OSError:
                pass
            finally:
                hub.unsubscribe(q)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._ok_client():
            return
        try:
            data = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 8000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        path = urlparse(self.path).path
        if path == "/api/command" and str(data.get("text", "")).strip():
            ctx["on_command"](str(data["text"]).strip()[:2000])
        elif path == "/api/action" and data.get("action") in ("talk", "stop", "mute", "everyday", "open_screen"):
            ctx["on_action"](data["action"], data)
        else:
            self.send_error(400)
            return
        self._send(200, b'{"ok":true}')


def _qr_png_b64(text):
    import qrcode
    img = qrcode.make(text, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def phone_remote(on: bool = True, _show: bool = True) -> str:
    import tools
    if not on:
        srv = _srv["server"]
        _srv.update(server=None, token=None)
        if srv:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        ctx["save"]("phone_remote", False)
        return "OK: phone remote off - the phone link no longer works."
    if _srv["server"] is None:
        _srv["token"] = secrets.token_urlsafe(16)
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", PORT), _H)
        except OSError as e:
            return f"FAILED: couldn't open port {PORT} ({e})."
        srv.daemon_threads = True
        _srv["server"] = srv
        threading.Thread(target=srv.serve_forever, name="phone", daemon=True).start()
        ctx["save"]("phone_remote", True)
    url = f"http://{lan_ip()}:{PORT}/?k={_srv['token']}"
    if not _show:
        return "OK: phone remote resumed."
    qr = _qr_png_b64(url)
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Phone remote</title><style>
body{{margin:0;background:#05070f;color:#eef4ff;font:16px "Segoe UI",system-ui,sans-serif;display:flex;gap:40px;align-items:center;
justify-content:center;min-height:100vh;padding:24px;box-sizing:border-box;flex-wrap:wrap}}
.qr{{background:#fff;padding:18px;border-radius:22px;box-shadow:0 0 60px rgba(127,230,255,.35)}} .qr img{{display:block;width:300px;height:300px;image-rendering:pixelated}}
h1{{font:600 28px Bahnschrift,"Segoe UI";letter-spacing:4px;color:#7fe6ff;margin:0 0 12px}} ol{{line-height:1.9;color:#cfd8f0;padding-left:20px}}
.u{{font:13px Consolas,monospace;color:#8b97b5;word-break:break-all;max-width:420px;margin-top:14px}} .w{{color:#f4ba42;font-size:13px;margin-top:14px;max-width:420px}}
</style></head><body><div class="qr"><img src="data:image/png;base64,{qr}" alt="QR"></div><div><h1>📱 PHONE REMOTE</h1><ol>
<li>Connect your phone to the <b>same Wi-Fi</b> as this PC.</li><li>Open the phone camera and scan the code.</li>
<li>Tap the link: Ultron's remote opens. Add it to your home screen.</li></ol>
<div class="u">{url}</div><div class="w">If the page doesn't load: Windows may ask to allow Python on <b>private networks</b>, so click Allow.
The key in the link is secret; anyone on your Wi-Fi with it can control Ultron. Say “turn off phone remote” to disable it.</div></div></body></html>"""
    tools.show_content("webpage", "Phone remote", "html", page)
    return ("OK: phone remote is on; the QR code is on the Ultron Screen. Tell the user to scan it with their phone on the "
            "same Wi-Fi (and to click Allow if Windows asks about the firewall).")


def resume():
    """Re-open the remote at startup if it was on (with a fresh key - the old QR stops working)."""
    if ctx["state"] and ctx["state"]("phone_remote", False):
        try:
            phone_remote(True, _show=False)
        except Exception:
            log.exception("could not resume the phone remote")


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#05070f"><meta name="apple-mobile-web-app-capable" content="yes">
<title>Ultron</title><script>window.UTHEME_KEY=new URLSearchParams(location.search).get("k")||"";</script>
<script src="/themes.js?k=" id="thjs"></script><style>
:root{--arc:#7fe6ff;--gold:#f4ba42;--red:#e0262f;--sub:#8b97b5;--card:#0d1222;--line:#1d2742}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overflow-x:hidden;background:#05070f;color:#eef4ff;font:15px -apple-system,"Segoe UI",Roboto,system-ui,sans-serif}
body{display:flex;flex-direction:column;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
header{display:flex;align-items:center;gap:12px;padding:14px 16px 6px}
.logo{font:600 15px Bahnschrift,"Segoe UI";letter-spacing:6px;color:var(--arc)}
.pill{margin-left:auto;font:600 11px Bahnschrift,sans-serif;letter-spacing:2px;padding:6px 12px;border-radius:14px;background:var(--card);border:1px solid var(--line)}
.pill i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--arc);margin-right:6px;box-shadow:0 0 8px var(--arc)}
.atom{position:relative;width:150px;height:150px;margin:6px auto 0}
.atom .n{position:absolute;inset:58px;border-radius:50%;background:radial-gradient(circle at 40% 35%,#fff,#7fe6ff 35%,#f4ba42 70%,#e0262f);
  box-shadow:0 0 30px #7fe6ff;animation:pulse 2s ease-in-out infinite}
.atom .o{position:absolute;inset:8px 0;border:2px solid;border-radius:50%;animation:spin 3s linear infinite}
.atom .o:nth-child(2){border-color:rgba(127,230,255,.7);transform:rotate(0deg) scaleY(.38)}
.atom .o:nth-child(3){border-color:rgba(244,186,66,.7);transform:rotate(60deg) scaleY(.38)}
.atom .o:nth-child(4){border-color:rgba(255,77,90,.7);transform:rotate(-60deg) scaleY(.38)}
body[data-s="listening"] .atom .n{background:radial-gradient(circle,#fff,#ff4d5a 60%);box-shadow:0 0 40px #ff4d5a}
body[data-s="thinking"] .atom .n,body[data-s="action"] .atom .n{background:radial-gradient(circle,#fff,#9b7bff 60%);box-shadow:0 0 40px #9b7bff;animation-duration:.5s}
body[data-s="speaking"] .atom .n{background:radial-gradient(circle,#fff,#f4ba42 60%);box-shadow:0 0 40px #f4ba42;animation-duration:.6s}
@keyframes pulse{50%{transform:scale(1.18)}}
.st{text-align:center;font:600 12px Bahnschrift,sans-serif;letter-spacing:4px;color:var(--arc);margin-top:4px}
.say{text-align:center;padding:4px 20px 8px;color:#cfd8f0;min-height:22px;font-size:14px}
.tabs{display:flex;gap:6px;padding:0 12px}
.tabs button{flex:1;min-width:0;border:0;border-radius:12px;padding:9px;background:var(--card);color:var(--sub);font:600 12px system-ui}
.tabs button.on{background:rgba(127,230,255,.15);color:var(--arc)}
main{flex:1;min-height:0;overflow-y:auto;padding:10px 12px}
.b{max-width:85%;padding:9px 13px;border-radius:16px;margin:5px 0;line-height:1.35;word-wrap:break-word}
.b.u{margin-left:auto;background:linear-gradient(135deg,#e0262f,#f08a24 55%,#f4ba42);color:#fff;border-bottom-right-radius:5px}
.b.j{background:var(--card);border:1px solid var(--line);border-bottom-left-radius:5px}
.t{font-size:12px;color:var(--sub);margin:4px 0 4px 6px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.k{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 6px;text-align:center;font-size:12px;color:#cfd8f0}
.k b{display:block;font-size:24px;margin-bottom:4px}
.k:active{transform:scale(.96);border-color:var(--arc)}
.r{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 13px;margin:7px 0}
.r small{color:var(--sub);display:block}.h{font:600 11px Bahnschrift;letter-spacing:3px;color:var(--arc);margin:14px 4px 4px}
footer{display:flex;gap:8px;padding:10px 12px 12px;align-items:center}
footer input{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);border-radius:22px;padding:13px 16px;color:#fff;font:16px system-ui;outline:none}
.btn{border:0;border-radius:50%;width:48px;height:48px;font-size:20px;color:#fff;background:linear-gradient(135deg,#e0262f,#f08a24 55%,#f4ba42);flex:0 0 auto}
.btn.talk{width:56px;height:56px;background:radial-gradient(circle,#ff4d5a,#a3121b);box-shadow:0 0 18px rgba(255,77,90,.6)}
.off{position:fixed;inset:auto 0 0 0;background:#3a0d12;color:#ffb3b8;text-align:center;padding:8px;font-size:12px;display:none}
</style></head><body data-s="idle">
<header><div class="logo">U.L.T.R.O.N</div><div class="pill"><i></i><span id="st2">STANDING BY</span></div></header>
<div class="atom"><div class="n"></div><div class="o"></div><div class="o"></div><div class="o"></div></div>
<div class="st" id="st">STANDING BY</div><div class="say" id="say">Tap 🎙 so the PC listens, or type below.</div>
<div class="tabs"><button class="on" data-t="chat">Chat</button><button data-t="keys">Controls</button><button data-t="day">Daily</button><button id="holoBtn">🔺 Holo</button></div>
<main id="chat"></main>
<main id="keys" hidden><div class="grid">
<div class="k" data-c="pause or play the music"><b>⏯</b>Play / pause</div><div class="k" data-c="next song"><b>⏭</b>Next</div><div class="k" data-c="what's playing"><b>🎵</b>Now playing</div>
<div class="k" data-c="volume up by 10"><b>🔊</b>Volume +</div><div class="k" data-c="volume down by 10"><b>🔉</b>Volume −</div><div class="k" data-c="mute the volume"><b>🔇</b>Mute PC</div>
<div class="k" data-c="good morning, give me my briefing"><b>☀️</b>Briefing</div><div class="k" data-c="what did I do today?"><b>📊</b>My day</div><div class="k" data-c="system status"><b>🖥</b>PC status</div>
<div class="k" data-c="take a screenshot"><b>📸</b>Screenshot</div><div class="k" data-c="show desktop"><b>🗔</b>Desktop</div><div class="k" data-confirm="lock the PC"><b>🔒</b>Lock PC</div>
<div class="k" data-c="start focus mode for 25 minutes"><b>🎯</b>Focus 25</div><div class="k" data-c="make the robot dance"><b>🤖</b>Dance</div><div class="k" id="spk"><b>🗣</b>Phone voice: off</div>
</div></main>
<main id="day" hidden></main>
<footer><button class="btn talk" id="talk">🎙</button><input id="cmd" placeholder="Ask Ultron… (or dictate)" enterkeyhint="send"><button class="btn" id="send">➤</button></footer>
<div class="off" id="off">Reconnecting to your PC…</div>
<div id="holo" style="position:fixed;inset:0;background:#000;z-index:50;display:none"><canvas id="hcv" style="width:100%;height:100%;display:block"></canvas>
<div style="position:absolute;left:0;right:0;bottom:14px;text-align:center;color:#333;font:12px system-ui">Place the hologram pyramid on the centre · tap to exit</div></div>
<script>
const K = new URLSearchParams(location.search).get("k") || "", $ = id => document.getElementById(id);
const H = {"Content-Type": "application/json", "X-Ultron-Key": K};
const post = (u, b) => fetch(u, {method: "POST", headers: H, body: JSON.stringify(b)});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const ST = {idle: "STANDING BY", followup: "LISTENING", listening: "LISTENING", thinking: "THINKING", action: "WORKING", speaking: "SPEAKING", error: "ALERT", choice: "CHOOSE"};
let speak = false, DAY = {};
try { speak = localStorage.getItem("atomoSpeak") === "1"; } catch (e) {}
function spkLabel() { $("spk").innerHTML = `<b>🗣</b>Phone voice: ${speak ? "on" : "off"}`; }
spkLabel();
function bub(k, t) { const d = document.createElement("div"); d.className = "b " + k; d.textContent = t; $("chat").appendChild(d); $("chat").scrollTop = 1e9; }
function note(t) { const d = document.createElement("div"); d.className = "t"; d.textContent = t; $("chat").appendChild(d); $("chat").scrollTop = 1e9; }
function setS(s, title, body) { document.body.dataset.s = s; $("st").textContent = $("st2").textContent = ST[s] || s.toUpperCase();
  if (s === "speaking" && body) $("say").textContent = body; else if (s === "action" && title) $("say").textContent = title; }
function renderDay() {
  const R = DAY.reminders || [], L = DAY.lists || {}, T = DAY.routines || {};
  let h = `<div class="h">ALERTS</div>` + (R.map(r => `<div class="r">${r.alarm ? "⏰" : "🔔"}<div>${esc(r.text)}<small>${esc(r.when)}</small></div></div>`).join("") || `<div class="t">No reminders.</div>`);
  for (const [n, items] of Object.entries(L)) h += `<div class="h">${esc(n.toUpperCase())} LIST</div>` + (items.map(i => `<div class="r" data-l="${esc(n)}" data-i="${esc(i)}">☐ <div>${esc(i)}</div></div>`).join("") || `<div class="t">Empty</div>`);
  if (Object.keys(T).length) h += `<div class="h">ROUTINES</div>` + Object.keys(T).map(k => `<div class="r" data-run="${esc(k)}">⚡<div>${esc(k)}<small>tap to run</small></div></div>`).join("");
  $("day").innerHTML = h;
  $("day").querySelectorAll("[data-i]").forEach(el => el.onclick = () => { el.style.opacity = .3; post("/api/action", {action: "everyday", op: "list_remove", list: el.dataset.l, item: el.dataset.i}); });
  $("day").querySelectorAll("[data-run]").forEach(el => el.onclick = () => post("/api/action", {action: "everyday", op: "run_routine", trigger: el.dataset.run}));
}
function on(e) {
  if (e.type === "state") setS(e.state, e.title, e.body);
  else if (e.type === "user") bub("u", e.text);
  else if (e.type === "reply" && e.text) { bub("j", e.text); if (speak && "speechSynthesis" in window) { const u = new SpeechSynthesisUtterance(e.text); speechSynthesis.speak(u); } }
  else if (e.type === "tool" && e.result == null) note("⚙ " + (e.label || e.name));
  else if (e.type === "canvas") note("🧩 Created “" + (e.title || e.kind) + "” — open on the PC's Ultron Screen");
  else if (e.type === "everyday") { DAY = e; renderDay(); }
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => { document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x === b));
  ["chat", "keys", "day"].forEach(id => $(id).hidden = id !== b.dataset.t); });
document.querySelectorAll("[data-c]").forEach(k => k.onclick = () => post("/api/command", {text: k.dataset.c}));
document.querySelectorAll("[data-confirm]").forEach(k => k.onclick = () => { if (confirm(k.dataset.confirm + "?")) post("/api/command", {text: k.dataset.confirm}); });
$("spk").onclick = () => { speak = !speak; try { localStorage.setItem("atomoSpeak", speak ? "1" : "0"); } catch (e) {} spkLabel();
  if (speak && "speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance("Phone voice on")); };
function send() { const t = $("cmd").value.trim(); if (!t) return; $("cmd").value = ""; post("/api/command", {text: t}); }
$("send").onclick = send; $("cmd").onkeydown = e => { if (e.key === "Enter") send(); };
$("talk").onclick = () => post("/api/action", {action: "talk"});
fetch("/api/state", {headers: H}).then(r => r.json()).then(s => { setS(s.state, s.title, s.body); (s.history || []).forEach(on); DAY = s.everyday || {}; renderDay(); });
// ---- hologram pyramid mode: 4 mirrored views of the dot sphere around the centre
const HP = []; for (let i = 0; i < 700; i++) { const y = 1 - i / 699 * 2, r = Math.sqrt(1 - y * y), t = i * 2.399963; HP.push([Math.cos(t) * r, y, Math.sin(t) * r, Math.random()]); }
let holoRaf = 0, holoLv = 0;
function holoFrame() {
  const cv = $("hcv"), d = Math.min(devicePixelRatio || 1, 2), W = innerWidth, H = innerHeight;
  if (cv.width !== W * d) { cv.width = W * d; cv.height = H * d; }
  const x = cv.getContext("2d"); x.setTransform(d, 0, 0, d, 0, 0); x.fillStyle = "#000"; x.fillRect(0, 0, W, H);
  const s = document.body.dataset.s, t = performance.now() / 1000;
  const target = s === "speaking" ? .8 : s === "listening" || s === "followup" ? .5 : s === "thinking" || s === "action" ? .35 : .05;
  holoLv += (target - holoLv) * .08;
  const col = s === "listening" || s === "followup" ? [255, 77, 90] : s === "thinking" || s === "action" ? [155, 123, 255] : s === "speaking" ? [244, 186, 66] : [127, 230, 255];
  const R = Math.min(W, H) * .16, size = R * 2.6;
  const off = holoFrame.off || (holoFrame.off = document.createElement("canvas"));
  if (off.width !== Math.round(size)) off.width = off.height = Math.round(size);
  const o = off.getContext("2d"); o.clearRect(0, 0, off.width, off.height);
  const ry = t * (.6 + holoLv * 2), cy = Math.cos(ry), sy = Math.sin(ry), puls = 1 + holoLv * .25 * Math.sin(t * 9);
  for (const [px, py, pz, sd] of HP) {
    const X = px * cy - pz * sy, Z = px * sy + pz * cy, k = 2.6 / (3.2 - Z), rr = R * puls * (1 + .05 * Math.sin(t * 3 + sd * 9));
    const a = .25 + .75 * (Z + 1) / 2;
    o.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${a.toFixed(2)})`;
    o.beginPath(); o.arc(size / 2 + X * rr * k, size / 2 + py * rr * k, .6 + (Z + 1) * .55, 0, 7); o.fill();
  }
  const gap = size * .72;                                        // distance of each view from the centre
  [[0, gap, 0], [0, -gap, Math.PI], [-gap, 0, Math.PI / 2], [gap, 0, -Math.PI / 2]].forEach(([dx, dy, rot]) => {
    x.save(); x.translate(W / 2 + dx, H / 2 + dy); x.rotate(rot); x.drawImage(off, -size / 2, -size / 2); x.restore(); });
  holoRaf = requestAnimationFrame(holoFrame);
}
$("holoBtn").onclick = async () => { $("holo").style.display = "block"; holoFrame();
  try { await document.documentElement.requestFullscreen(); } catch (e) {}
  try { window._wl = await navigator.wakeLock.request("screen"); } catch (e) {} };
$("holo").onclick = () => { $("holo").style.display = "none"; cancelAnimationFrame(holoRaf);
  try { document.exitFullscreen(); } catch (e) {} try { window._wl && window._wl.release(); } catch (e) {} };
const es = new EventSource("/events?k=" + encodeURIComponent(K));
es.onmessage = m => { const e = JSON.parse(m.data); if (e.type === "ui_theme" && window.ULTRON_THEME) ULTRON_THEME.apply(e.id); on(e); };
if (window.ULTRON_THEME) ULTRON_THEME.init("phone"); es.onerror = () => $("off").style.display = "block"; es.onopen = () => $("off").style.display = "none";
</script></body></html>"""
