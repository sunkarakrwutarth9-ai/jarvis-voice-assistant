"""Publish a web creation live on the internet - free, through GitHub Pages and the GitHub account that is
signed in on this PC (the GitHub CLI, `gh`).

  "build a website for my shop ... and put it online"  ->  https://<user>.github.io/<name>/

Publishing is public and outward-facing, so it ALWAYS needs the user's explicit yes in their latest words
(the same kind of guard as saving). Re-publishing the same name updates the live site.
"""

import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger("jarvis.webpublish")
YES = re.compile(r"\b(yes|yeah|yep|sure|ok(ay)?|publish|go ahead|do it|put it (online|live)|haan|avunu|sare)\b|అవును|సరే|हाँ|हां", re.I)


def _gh():
    return shutil.which("gh") or next((p for p in (r"C:\Program Files\GitHub CLI\gh.exe",) if Path(p).exists()), None)


def _run(args, cwd=None, timeout=90):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def publish_website(name: str = "", confirm: bool = False) -> str:
    import tools
    c = tools.CREATIONS.get(tools._last_creation[0])
    if not c or c["lang"] != "html":
        return "FAILED: there's no web page / game / 3D scene on the Ultron Screen to publish. Create one first."
    gh = _gh()
    if not gh:
        return "FAILED: the GitHub CLI isn't installed (https://cli.github.com), so I can't publish."
    try:
        owner = _run([gh, "api", "user", "--jq", ".login"], timeout=20)
    except Exception as e:
        return f"FAILED: GitHub isn't signed in on this PC ({e}). Run 'gh auth login' once."
    slug = re.sub(r"[^a-z0-9-]+", "-", (name or c["title"]).lower()).strip("-")[:40] or "atomo-site"
    url = f"https://{owner}.github.io/{slug}/"
    if not (confirm and YES.search(tools.last_user_text or "")):
        return (f"NOT PUBLISHED YET - it would be PUBLIC on the internet at {url} (repository {owner}/{slug}). Ask the "
                f"user to confirm; only if they say yes call publish_website(name='{slug}', confirm=true).")
    tools.progress("Publishing to the web")
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / slug
        try:
            exists = subprocess.run([gh, "repo", "view", f"{owner}/{slug}"], capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW).returncode == 0
            if exists:
                _run([gh, "repo", "clone", f"{owner}/{slug}", str(site)], timeout=120)
            else:
                site.mkdir()
                _run(["git", "init", "-b", "main"], cwd=site)
            (site / "index.html").write_text(c["content"], encoding="utf-8")
            (site / ".nojekyll").write_text("", encoding="utf-8")
            _run(["git", "add", "-A"], cwd=site)
            _run(["git", "-c", "user.name=Ultron", "-c", f"user.email={owner}@users.noreply.github.com",
                  "commit", "-m", f"Publish {c['title']} with Ultron"], cwd=site)
            if exists:
                _run(["git", "push", "origin", "main"], cwd=site, timeout=120)
            else:
                _run([gh, "repo", "create", f"{owner}/{slug}", "--public", "--source", str(site), "--push",
                      "--description", f"{c['title']} - made with Ultron"], cwd=site, timeout=120)
                time.sleep(2)
                _run([gh, "api", "-X", "POST", f"repos/{owner}/{slug}/pages",
                      "-f", "source[branch]=main", "-f", "source[path]=/"], timeout=60)
        except Exception as e:
            log.warning("publish failed: %s", e)
            return f"FAILED: publishing didn't work ({e})."
    tools.publish({"type": "reply", "text": f"🌍 Live soon at {url}", "model": "publish", "secs": 0})
    return (f"OK: published! It will be live at {url} in about a minute (GitHub Pages builds it). Repository: "
            f"https://github.com/{owner}/{slug}. Saying 'publish it again' later updates the same site.")
