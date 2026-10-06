"""Smart home control through Google Home.

Google Home has no PC API, but the Google Assistant API accepts the same text commands you would say
to a Nest speaker ("turn on the AC", "set the bedroom AC to 24 degrees", "turn off all the lights"),
and Google Home carries them out on every device linked to the account.

It's a gRPC service. Windows Smart App Control blocks the grpcio DLLs on this PC, so this module speaks
gRPC itself: a tiny HTTP/2 client (pure-Python `h2`) plus hand-encoded protobuf messages.

One-time setup (the user does this, in their own Google account) - see docs/smarthome.md:
  1. Google Cloud console: create a project, enable "Google Assistant API",
     configure the OAuth consent screen (External, add yourself as a test user),
     create an OAuth client ID of type "Desktop app" and download it as  google_client.json  (into this folder).
  2. Say "connect Google Home": a browser opens, you allow access once; the token is saved
     to google_token.json (never committed).
"""

import json
import logging
import socket
import ssl
import struct
import threading
from pathlib import Path

log = logging.getLogger("jarvis.smarthome")
HERE = Path(__file__).resolve().parent
CLIENT_FILE = HERE / "google_client.json"
TOKEN_FILE = HERE / "google_token.json"
DEVICES_FILE = HERE / "devices.json"
SCOPES = ["https://www.googleapis.com/auth/assistant-sdk-prototype"]
HOST = "embeddedassistant.googleapis.com"
PATH = "/google.assistant.embedded.v1alpha2.EmbeddedAssistant/Assist"
_lock = threading.Lock()
_conversation = {"state": b""}
publish = lambda event: None

SETUP_HELP = ("Google Home isn't connected yet. One-time setup: in the Google Cloud console create a project, "
              "enable the Google Assistant API, set up the OAuth consent screen with yourself as a test user, create "
              "an OAuth client of type Desktop app, download it as google_client.json into the Atomo folder, then say "
              "'connect Google Home'. Full steps are in docs/smarthome.md.")


# ------------------------------------------------------------------ protobuf (just what we need)
def _varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _field(num, value):
    if isinstance(value, bool) or isinstance(value, int):
        return _varint(num << 3) + _varint(int(value))
    if isinstance(value, str):
        value = value.encode()
    return _varint(num << 3 | 2) + _varint(len(value)) + value


def _parse(buf):
    """Decode one protobuf message into {field: [values]} (bytes for length-delimited, ints for varints)."""
    out, i = {}, 0
    while i < len(buf):
        key = shift = 0
        while True:
            b = buf[i]; i += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            val = shift = 0
            while True:
                b = buf[i]; i += 1
                val |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
        elif wt == 2:
            ln = shift = 0
            while True:
                b = buf[i]; i += 1
                ln |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            val = buf[i:i + ln]; i += ln
        elif wt == 1:
            val = buf[i:i + 8]; i += 8
        elif wt == 5:
            val = buf[i:i + 4]; i += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        out.setdefault(num, []).append(val)
    return out


def _request(text, language="en-US"):
    audio_out = _field(1, 2) + _field(2, 16000) + _field(3, 100)              # MP3 (we only use the text)
    dialog = _field(1, _conversation["state"]) + _field(2, language) + _field(7, not _conversation["state"])
    device = _field(1, "atomo") + _field(3, "default")
    config = _field(6, text) + _field(2, audio_out) + _field(3, dialog) + _field(4, device)
    msg = _field(1, config)
    return b"\x00" + struct.pack(">I", len(msg)) + msg


# ------------------------------------------------------------------ auth
def _credentials(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return creds
    if not interactive:
        return None
    if not CLIENT_FILE.exists():
        raise FileNotFoundError("google_client.json is missing")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent",
                                  success_message="Atomo is connected to Google Home. You can close this tab.")
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def connect() -> str:
    try:
        _credentials(interactive=True)
    except FileNotFoundError:
        return "FAILED: " + SETUP_HELP
    except Exception as e:
        return f"FAILED: Google sign-in did not finish ({e})."
    return "OK: Google Home is connected. Smart devices can now be controlled by voice."


# ------------------------------------------------------------------ gRPC over HTTP/2
def _assist(text, token, language):
    import h2.config
    import h2.connection
    import h2.events
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2"])
    sock = ctx.wrap_socket(socket.create_connection((HOST, 443), timeout=20), server_hostname=HOST)
    try:
        conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True))
        conn.initiate_connection()
        body = _request(text, language)
        sid = conn.get_next_available_stream_id()
        conn.send_headers(sid, [(":method", "POST"), (":scheme", "https"), (":authority", HOST), (":path", PATH),
                                ("content-type", "application/grpc"), ("te", "trailers"),
                                ("authorization", f"Bearer {token}"), ("grpc-timeout", "20S")])
        conn.send_data(sid, body, end_stream=True)
        sock.sendall(conn.data_to_send())
        data, status, message, done = bytearray(), None, "", False
        while not done:
            chunk = sock.recv(65536)
            if not chunk:
                break
            for ev in conn.receive_data(chunk):
                if isinstance(ev, h2.events.DataReceived):
                    data += ev.data
                    conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                elif isinstance(ev, (h2.events.TrailersReceived, h2.events.ResponseReceived)):
                    hdr = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                           for k, v in ev.headers}
                    if "grpc-status" in hdr:
                        status, message = int(hdr["grpc-status"]), hdr.get("grpc-message", "")
                elif isinstance(ev, (h2.events.StreamEnded, h2.events.StreamReset, h2.events.ConnectionTerminated)):
                    done = True
            sock.sendall(conn.data_to_send())
    finally:
        sock.close()
    if status not in (None, 0):
        raise RuntimeError(f"Google Assistant error {status}: {message}")
    reply, i = "", 0
    while i + 5 <= len(data):                         # length-prefixed AssistResponse messages
        ln = struct.unpack(">I", data[i + 1:i + 5])[0]
        resp = _parse(bytes(data[i + 5:i + 5 + ln]))
        i += 5 + ln
        for dso in resp.get(5, []):                   # dialog_state_out
            d = _parse(dso)
            if d.get(1):
                reply = d[1][-1].decode("utf-8", "replace")
            if d.get(2):
                _conversation["state"] = d[2][-1]
    return reply


def command(text: str, device: str = "", language: str = "en-US") -> str:
    """Send a Google Home command ('turn on the AC') and return what Google answered."""
    try:
        creds = _credentials()
    except Exception as e:
        log.warning("Google credentials problem: %s", e)
        creds = None
    if creds is None:
        return "FAILED: " + SETUP_HELP
    with _lock:
        try:
            reply = _assist(text, creds.token, language)
        except Exception as e:
            log.warning("Google Home command failed: %s", e)
            return f"FAILED: Google Home did not respond ({e})."
    if device:
        remember_device(device)
    log.info("google home: %r -> %r", text, reply)
    return f"OK: Google Home says: {reply}" if reply else f"OK: sent '{text}' to Google Home."


# ------------------------------------------------------------------ devices shown in the command center
def devices():
    try:
        return json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def remember_device(name):
    name = " ".join(name.split())[:40]
    items = devices()
    if name and name.lower() not in (d.lower() for d in items):
        items.append(name)
        DEVICES_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        publish({"type": "devices", "devices": items, "connected": TOKEN_FILE.exists()})


def forget_device(name):
    items = [d for d in devices() if d.lower() != name.lower()]
    DEVICES_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    publish({"type": "devices", "devices": items, "connected": TOKEN_FILE.exists()})
