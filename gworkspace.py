"""Google Calendar + Google Tasks - so Ultron can read your agenda, add events and manage tasks.

Uses the same Google Cloud project as Gmail / Google Home (google_client.json). One-time: enable the
"Google Calendar API" and "Google Tasks API" in that project, then say "connect Google Calendar".
Token: google_workspace_token.json (never committed).
"""

import datetime
import logging
from pathlib import Path

log = logging.getLogger("jarvis.gworkspace")
HERE = Path(__file__).resolve().parent
CLIENT = HERE / "google_client.json"
TOKEN = HERE / "google_workspace_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/tasks"]
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TASKS = "https://tasks.googleapis.com/tasks/v1"
SETUP = ("Google Calendar isn't connected yet. One-time: in the Google Cloud project used for Gmail/Google Home, enable "
         "'Google Calendar API' and 'Google Tasks API' (APIs & Services > Library), keep google_client.json in the "
         "Ultron folder, then say 'connect Google Calendar'.")


def _creds(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    c = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if c and c.valid:
        return c
    if c and c.expired and c.refresh_token:
        try:
            c.refresh(Request())
            TOKEN.write_text(c.to_json(), encoding="utf-8")
            return c
        except Exception as e:
            log.warning("calendar token refresh failed: %s", e)
    if not interactive:
        return None
    if not CLIENT.exists():
        raise FileNotFoundError("google_client.json missing")
    from google_auth_oauthlib.flow import InstalledAppFlow
    c = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES).run_local_server(
        port=0, prompt="consent", success_message="Ultron is connected to Google Calendar and Tasks. You can close this tab.")
    TOKEN.write_text(c.to_json(), encoding="utf-8")
    return c


def connected():
    try:
        return _creds() is not None
    except Exception:
        return False


def _s():
    from google.auth.transport.requests import AuthorizedSession
    c = _creds()
    if not c:
        raise PermissionError(SETUP)
    return AuthorizedSession(c)


def connect_google_calendar() -> str:
    try:
        _creds(interactive=True)
    except FileNotFoundError:
        return "FAILED: " + SETUP
    except Exception as e:
        return f"FAILED: Google sign-in didn't finish ({e})."
    return "OK: Google Calendar and Tasks connected."


def _tz():
    return datetime.datetime.now().astimezone().tzinfo


def calendar_agenda(days: int = 1) -> str:
    try:
        s = _s()
    except PermissionError as e:
        return "FAILED: " + str(e)
    now = datetime.datetime.now(_tz())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=max(1, min(int(days or 1), 14)))
    r = s.get(CAL, params={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                           "orderBy": "startTime", "maxResults": 30}).json()
    items = r.get("items", [])
    if not items:
        return f"OK: nothing on the calendar for the next {days} day(s)."
    out = []
    for e in items:
        st = e.get("start", {})
        when = st.get("dateTime") or st.get("date", "")
        try:
            d = datetime.datetime.fromisoformat(when)
            when = d.strftime("%a %d %b %I:%M %p").replace(" 0", " ") if "T" in st.get("dateTime", "T") else d.strftime("%a %d %b (all day)")
        except ValueError:
            pass
        out.append(f"{when}: {e.get('summary', '(no title)')}" + (f" @ {e['location']}" if e.get("location") else ""))
    return "OK: " + " | ".join(out)


def calendar_add(title: str, start: str, duration_minutes: int = 60, description: str = "") -> str:
    """start: 'YYYY-MM-DD HH:MM' (24h) local time."""
    try:
        s = _s()
    except PermissionError as e:
        return "FAILED: " + str(e)
    try:
        st = datetime.datetime.fromisoformat(start.strip().replace(" ", "T")).replace(tzinfo=_tz())
    except ValueError:
        return "FAILED: give the start as 'YYYY-MM-DD HH:MM'."
    en = st + datetime.timedelta(minutes=int(duration_minutes or 60))
    r = s.post(CAL, json={"summary": title, "description": description,
                          "start": {"dateTime": st.isoformat()}, "end": {"dateTime": en.isoformat()}}).json()
    if "id" not in r:
        return f"FAILED: Google Calendar didn't accept it ({str(r)[:150]})."
    return f"OK: added '{title}' on {st:%a %d %b at %I:%M %p} to Google Calendar."


def _tasklist(s):
    r = s.get(f"{TASKS}/users/@me/lists").json()
    return r.get("items", [{}])[0].get("id", "@default")


def tasks_list() -> str:
    try:
        s = _s()
    except PermissionError as e:
        return "FAILED: " + str(e)
    r = s.get(f"{TASKS}/lists/{_tasklist(s)}/tasks", params={"showCompleted": "false", "maxResults": 30}).json()
    items = r.get("items", [])
    if not items:
        return "OK: no open Google Tasks."
    return "OK: open tasks: " + " | ".join(t.get("title", "") + (f" (due {t['due'][:10]})" if t.get("due") else "") for t in items)


def task_add(title: str, due: str = "") -> str:
    try:
        s = _s()
    except PermissionError as e:
        return "FAILED: " + str(e)
    body = {"title": title}
    if due:
        body["due"] = due.strip()[:10] + "T00:00:00.000Z"
    r = s.post(f"{TASKS}/lists/{_tasklist(s)}/tasks", json=body).json()
    return f"OK: added the task '{title}' to Google Tasks." if "id" in r else f"FAILED: {str(r)[:150]}"
