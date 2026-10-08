"""API keys: which ones Ultron has, which are missing, and adding / changing one safely.

The key is typed only into Ultron's own password dialog on this PC (never into a web page, never by the AI),
checked against the provider, saved to .env and switched on straight away where possible.
"""
import json
import logging
import os
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"
log = logging.getLogger("jarvis.apikeys")

# env, name, what it does, where to get it, OpenAI-compatible base URL (None = not a brain), models for the pool
KEYS = [
    ("GEMINI_API_KEY", "Google Gemini", "Main brain + hearing audio + vision (free)", "https://aistudio.google.com/apikey",
     "https://generativelanguage.googleapis.com/v1beta/openai/", None),
    ("GROQ_API_KEY", "Groq", "Ultra-fast replies + Whisper hearing (free)", "https://console.groq.com/keys",
     "https://api.groq.com/openai/v1", "qwen/qwen3.8-27b,openai/gpt-oss-120b"),
    ("XPL_API_KEY", "DeepSeek (Experiential Labs)", "Backup brain for big jobs", "https://experientiallabs.ai",
     "https://api.experientiallabs.ai/v1", "deepseek-v4.1-flash,deepseek-v4-flash"),
    ("OPENROUTER_API_KEY", "OpenRouter", "Hundreds of models with one key", "https://openrouter.ai/keys",
     "https://openrouter.ai/api/v1", "anthropic/claude-sonnet-5"),
    ("ANTHROPIC_API_KEY", "Claude (Anthropic)", "Best for writing and code", "https://console.anthropic.com/settings/keys",
     "https://api.anthropic.com/v1/", "claude-sonnet-5-5,claude-haiku-4-5-20251001"),
    ("OPENAI_API_KEY", "ChatGPT (OpenAI)", "GPT models", "https://platform.openai.com/api-keys",
     "https://api.openai.com/v1", "gpt-5-mini,gpt-5"),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot", "Control Ultron from your phone anywhere", "https://t.me/BotFather",
     None, None),
]
PREFIX = {"GROQ_API_KEY": "groq", "XPL_API_KEY": "xpl", "OPENROUTER_API_KEY": "openrouter",
          "ANTHROPIC_API_KEY": "claude", "OPENAI_API_KEY": "openai"}
hooks = {"brain": None, "ask_secret": None, "publish": None}


def status():
    """For the command center: never the key itself, only whether it is there."""
    return [{"env": env, "name": name, "what": what, "get": get, "set": bool(os.environ.get(env, "").strip())}
            for env, name, what, get, _url, _models in KEYS]


def missing():
    return [k["name"] for k in status() if not k["set"]]


def _check(env, url, key):
    """Ask the provider whether the key works. Returns an error text or ''."""
    try:
        if env == "TELEGRAM_BOT_TOKEN":
            with urllib.request.urlopen(f"https://api.telegram.org/bot{key}/getMe", timeout=10) as r:
                return "" if json.load(r).get("ok") else "Telegram rejected the token"
        from openai import OpenAI
        OpenAI(base_url=url, api_key=key, max_retries=0, timeout=15).models.list()
        return ""
    except Exception as e:
        msg = str(e)
        if any(w in msg for w in ("401", "403", "nvalid", "nauthor", "Not Found")):
            return "the provider rejected that key"
        log.warning("key check for %s inconclusive: %s", env, msg[:120])
        return ""                                      # network hiccup: keep the key, it is checked on use


def set_key(env: str) -> str:
    row = next((k for k in KEYS if k[0] == env), None)
    if not row:
        return "FAILED: unknown key."
    _env, name, _what, get, url, models = row
    ask = hooks["ask_secret"]
    if not ask:
        return "FAILED: not available."
    key = (ask(f"Paste your {name} key.\nGet one at {get}\nIt is stored only on this PC, in .env.") or "").strip()
    if not key:
        return "Cancelled - no key given."
    err = _check(env, url, key)
    if err:
        return f"FAILED: {name}: {err}. Nothing was saved."
    from dotenv import set_key as _save
    _save(str(ENV_FILE), env, key)
    os.environ[env] = key
    note = ""
    brain = hooks["brain"]
    if env in PREFIX and brain is not None and models:
        brain.add_provider(PREFIX[env], url, key, [m for m in models.split(",") if m])
        note = " It is already part of Ultron's brain."
    elif env == "GEMINI_API_KEY":
        note = " Restart Ultron to use it as the main brain."
    elif env == "TELEGRAM_BOT_TOKEN":
        note = " Say 'connect Telegram' to pair your phone."
    if hooks["publish"]:
        hooks["publish"]({"type": "api_keys", "keys": status()})
    log.info("API key saved: %s", env)
    return f"OK: {name} key saved.{note}"
