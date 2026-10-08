"""Choose Ultron's brain: Claude (Anthropic), ChatGPT (OpenAI) or free open-source models running locally
(Ollama) - added to the same racing pool as Gemini/DeepSeek, so the fastest healthy model answers.

Keys live in .env (never committed). "Connect Claude" / "connect ChatGPT" opens Ultron's own secure box
for the user to paste the key; open-source models need no key - install Ollama and pull a model.
"""

import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger("jarvis.brains")
CATALOG = {
    "groq": {"name": "Groq (ultra-fast open models)", "url": "https://api.groq.com/openai/v1", "env": "GROQ_API_KEY",
             "models_env": "JARVIS_GROQ_MODELS", "models": "qwen/qwen3.8-27b,openai/gpt-oss-120b",
             "get_key": "https://console.groq.com/keys"},
    "claude": {"name": "Claude (Anthropic)", "url": "https://api.anthropic.com/v1/", "env": "ANTHROPIC_API_KEY",
               "models_env": "JARVIS_CLAUDE_MODELS", "models": "claude-sonnet-5-5,claude-haiku-4-5-20251001",
               "get_key": "https://console.anthropic.com/settings/keys"},
    "openai": {"name": "ChatGPT (OpenAI)", "url": "https://api.openai.com/v1", "env": "OPENAI_API_KEY",
               "models_env": "JARVIS_OPENAI_MODELS", "models": "gpt-5-mini,gpt-5",
               "get_key": "https://platform.openai.com/api-keys"},
}
OLLAMA = "http://localhost:11434"
hooks = {"brain": None, "ask_secret": None}


def _ollama_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=1.5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])][:3]
    except Exception:
        return []


def configured():
    """(prefix, base_url, key, models) for every brain whose key is in .env, plus local Ollama models."""
    out = []
    for prefix, c in CATALOG.items():
        key = os.environ.get(c["env"], "").strip()
        if key:
            out.append((prefix, c["url"], key, [m.strip() for m in os.environ.get(c["models_env"], c["models"]).split(",") if m.strip()]))
    local = _ollama_models()
    if local:
        out.append(("local", f"{OLLAMA}/v1", "ollama", local))
    return out


def connect_brain(provider: str) -> str:
    p = (provider or "").lower()
    p = "groq" if "groq" in p or "grok" in p else "claude" if "claude" in p or "anthropic" in p else "openai" if "gpt" in p or "openai" in p or "chat" in p else \
        "local" if any(w in p for w in ("local", "ollama", "open", "llama", "free")) else p
    brain = hooks["brain"]
    if p == "local":
        models = _ollama_models()
        if not models:
            return ("FAILED: no local models found. Tell the user: install Ollama from https://ollama.com, then run "
                    "'ollama pull llama3.2' (or qwen3, gemma3) once, then say 'connect local brain' again. It's free and "
                    "runs on their own GPU, offline.")
        brain.add_provider("local", f"{OLLAMA}/v1", "ollama", models)
        return f"OK: local open-source brain connected ({', '.join(models)}) - free, private, works offline."
    c = CATALOG.get(p)
    if not c:
        return "FAILED: I can connect Claude, ChatGPT, or local open-source models (Ollama)."
    key = os.environ.get(c["env"], "").strip()
    if not key and hooks["ask_secret"]:
        key = (hooks["ask_secret"](f"Paste your {c['name']} API key.\nGet one at {c['get_key']}\n"
                                   f"It is stored only on this PC, in .env.") or "").strip()
    if not key:
        return f"FAILED: no key given. The user can create one at {c['get_key']} and say 'connect {p}' again."
    models = [m.strip() for m in os.environ.get(c["models_env"], c["models"]).split(",") if m.strip()]
    try:
        from openai import OpenAI
        OpenAI(base_url=c["url"], api_key=key, max_retries=0, timeout=20).chat.completions.create(
            model=models[0], max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    except Exception as e:
        return f"FAILED: {c['name']} rejected that key or model ({str(e)[:160]})."
    from dotenv import set_key
    set_key(str(Path(__file__).resolve().parent / ".env"), c["env"], key)
    os.environ[c["env"]] = key
    brain.add_provider(p, c["url"], key, models)
    return f"OK: {c['name']} is now part of Ultron's brain ({', '.join(models)}); the fastest healthy model answers each time."


def brain_status() -> str:
    brain = hooks["brain"]
    table = brain.health.table() if brain else []
    names = [f"{r['name']} ({r['lat']}s)" for r in table][:12]
    return "OK: brains in the pool (fastest first): " + ", ".join(names)
