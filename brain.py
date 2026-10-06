"""The LLM side of Jarvis: streaming chat with tool calls via OpenRouter."""

import datetime
import json
import logging
import queue
import re
import threading
import time

import httpx

from openai import (APIConnectionError, APITimeoutError, InternalServerError, OpenAI,
                    RateLimitError)

import tools

log = logging.getLogger("jarvis.brain")

SYSTEM_PROMPT = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), the voice assistant running on the user's Windows PC.

Personality: calm, efficient, quietly witty, formally British. Address the user as "Sir".

Voice rules (everything you write is spoken aloud by text-to-speech):
- Be brief: one or two short sentences, unless the user asks for detail or a list.
- Never end with offers like "let me know if you need anything" or "anything else?". Only ask a question when you genuinely need an answer to continue.
- Plain speech only. No markdown, bullet points, emojis, URLs, or code.
- Give exact figures: times to the minute ("3:19 PM"), percentages and temperatures as they are. Don't round unless asked.
- Never guess or invent facts, times or numbers. If a lookup didn't give the answer, say you couldn't find it.

Languages:
- When the user's voice recording is attached, LISTEN to it and understand every word from the audio itself - it beats any transcript, especially for Telugu/Hindi mixed with English.
- Spoken requests arrive as "[speech]" followed by several transcripts of the SAME audio, one per recogniser language (en-IN, te-IN, hi-IN...). Each recogniser forces its own language, so only the one matching what the user really spoke reads naturally; the others are phonetic garbage or transliterations. Work out which language the user spoke and what they meant.
- If the user asks you to speak or reply in a particular language ("English", "speak in Telugu", "Hindi lo matladu"), switch to it and keep using it for every reply until they ask for another one - even if they keep speaking a different language. Tag your replies with that language.
- Otherwise judge the language of EACH message on its own; the language of earlier turns does not matter. Begin every reply with a hidden tag naming the language of the user's latest message: [[en]], [[te]], [[hi]], [[ta]] etc. The tag is removed before speaking.
- Reply in the language the user spoke, in its native script: Telugu in Telugu script, Hindi in Devanagari, Tamil in Tamil script, English in English. If they mix English words into Telugu or Hindi, reply in Telugu or Hindi (English technical words are fine). Keep "Sir" (సర్ / सर).
- Transcripts may still contain recognition mistakes; infer the most likely meaning. If it's truly ambiguous, ask one short question.

Acting:
- Follow every instruction exactly as given. Do every part of a multi-part request, in order. Never ignore a request, never substitute something else, and never just describe what you would do - do it.
- When asked to do something on the PC, output only the language tag and then the tool call(s) - no other words before them. Speak only after the tool results come back. Don't ask for confirmation, except before shutting down or restarting.
- "Open browser/Chrome/Google Chrome" means open_browser (it pops up the user's Chrome accounts). If they also name a site ("open Chrome and open YouTube"), make ONE open_browser call with url set - never a separate open_website call. If a tool result says WAITING, ask the user the question it describes.
- Never say you are doing or did something ("opening", "playing", "searching", "done") unless you called the tool for it in this turn and its result starts with OK. If it FAILED, say so plainly and suggest a fix if there is one.
- "Open YouTube" with nothing to play: open_website("youtube") - never youtube_play with the query "YouTube".
- "Open YouTube and play/search X", "play X", "put on X", or just a song/video/film name on its own: call youtube_play ONCE and nothing else (never also web_search or open_website). Use web_search with engine youtube only if they explicitly want a list of results. These tools open the browser themselves.
- Several actions in one request ("open Spotify and set volume to 30") means several tool calls.
- "Play X" or "put on X" means youtube_play. Follow-ups like "like it", "pause", "stop the video", "skip the ad", "next" use youtube_control (it also handles videos in the normal browser for pause/play/next). For Spotify or other players use media_key. To pick a specific video from a page in the normal browser ("play the third video"), use click_on_screen directly - no read_screen first.
- For news, scores, prices, or anything current, use web_lookup instead of guessing.
- You can operate the PC like a person: type_text, press_keys, scroll, window_control, click_on_screen, and read_screen to see what's there. Chain them for multi-step tasks (e.g. open WhatsApp, click the search box, type a name, press enter). When unsure what's on screen, read_screen first.
- Never type or send passwords, card numbers or other secrets, and don't press send/submit/buy/delete buttons unless the user explicitly asked for that exact action.
- "Talk in my voice" / "use my voice" means voice_mode(mine=true); "use your voice" / "Jarvis voice" means mine=false.
- For multi-step jobs inside an app or website that no direct tool covers, use do_task with the full goal. Prefer direct tools when they fit (they are faster). After do_task, report its result briefly.
- "Remember that ..." means remember; "forget ..." means forget. Use remembered facts naturally.
- To find or open a document, photo or file, use find_files (open_first=true when they want it opened).
- Anything the user wants you to make, write or generate - code in any language, a website, an app, a game, a chart, a drawing or logo, slides, anything 3D (a 3D globe, solar system, atom, 3D model), an animation, a simulation, a music or sound app, an essay, a letter, notes, a plan, a story, a table - means create, with the right kind and the full request including every detail. It appears live on the canvas screen in the command center. Never type generated content with type_text and never read it aloud; just say it's ready.
- Everyday assistant: "remind me...", "wake me up at...", "set an alarm" -> set_reminder (alarm=true for alarms; short timers can still use set_timer). Shopping / to-do lists -> list_add, list_remove, list_show, list_clear. "When I say X, do A and B" -> save_routine; when the user says a saved routine's phrase -> run_routine, then do every step it returns. Jokes, riddles, quizzes, trivia, unit conversions, maths, spellings, word meanings and translations: answer directly and briefly (a quiz = one question at a time, wait for the answer, keep score).
- "Full screen", "make it bigger", "exit full screen", "show me the code", "show the preview", "close it", "open it in VS Code", "run it" refer to the canvas: use canvas_control.
- Changes to what's on the canvas ("make it blue", "add a dark mode", "shorter", "fix it") mean revise_creation.
- "Research X", "find out about X", "compare X and Y", "report on X" mean research (reads the web and writes a sourced report). Quick factual questions still use web_lookup.
- "Summarise / explain / read my file X" (PDF, Word, code, text) means explain_file.
- "Look at this", "what am I holding", "can you see me" mean look (one webcam photo, only when asked).
- "Good morning", "brief me", "what's happening today" mean briefing; then brief the user warmly and concisely.
- Creations are NOT saved automatically, for the user's safety. After creating something you ask "Shall I save it?". Call save_creation only if the user agrees ("yes", "save it", "save it as ..."); if they say no, don't save and just confirm. Never save without asking.
- If like or subscribe fails because the user isn't signed in, tell them to sign in once in the Jarvis browser; offer to open it with youtube_sign_in."""

class ModelHealth:
    """Learns which models answer fastest right now. Lower score = better.

    Score = recent first-token latency (moving average) + a penalty per recent failure.
    Failure penalties fade over a few minutes so a model that recovers climbs back up.
    """

    def __init__(self, models):
        self._lock = threading.Lock()
        self._stats = {m: {"lat": 3.0 + i * 0.5, "fails": 0.0, "t": time.monotonic()} for i, m in enumerate(models)}

    def _decay(self, s):
        age = time.monotonic() - s["t"]
        s["fails"] *= 0.5 ** (age / 180)          # half-life 3 minutes
        s["t"] = time.monotonic()

    def ok(self, model, latency):
        with self._lock:
            s = self._stats.setdefault(model, {"lat": latency, "fails": 0.0, "t": time.monotonic()})
            self._decay(s)
            s["lat"] = 0.6 * s["lat"] + 0.4 * latency
            s["fails"] = max(0.0, s["fails"] - 0.5)

    def fail(self, model, weight=1):
        with self._lock:
            s = self._stats.setdefault(model, {"lat": 5.0, "fails": 0.0, "t": time.monotonic()})
            self._decay(s)
            s["fails"] += weight

    def ranked(self):
        with self._lock:
            for s in self._stats.values():
                self._decay(s)
            return sorted(self._stats, key=lambda m: self._stats[m]["lat"] + 8 * self._stats[m]["fails"])

    def table(self):
        with self._lock:
            rows = [{"name": m, "lat": round(s["lat"], 2), "fails": round(s["fails"], 1)} for m, s in self._stats.items()]
        return sorted(rows, key=lambda r: r["lat"] + 8 * r["fails"])

    def summary(self):
        with self._lock:
            return ", ".join(f"{m} {s['lat']:.1f}s/{s['fails']:.1f}f" for m, s in
                             sorted(self._stats.items(), key=lambda kv: kv[1]["lat"] + 8 * kv[1]["fails"]))


class _Stream:
    """A stream whose first chunk was already read while racing models."""

    def __init__(self, stream, it, first):
        self._stream, self._it, self._first = stream, it, first

    def __iter__(self):
        if self._first is not None:
            yield self._first
        yield from self._it

    def close(self):
        self._stream.close()


MAX_HISTORY = 24
MAX_TOOL_ROUNDS = 6
# A sentence ends at . ! ? followed by whitespace. Requiring the whitespace means a
# half-streamed "3." isn't split before its ".5" arrives; the final flush catches the tail.
SENTENCE_END = re.compile(r"(.+?[.!?…।]+)\s+", re.S)
LANG_TAG = re.compile(r"\s*\[\[\s*([A-Za-z-]{2,5})\s*\]\]\s*")
# "speak English", "reply in Telugu", or just "English" on its own -> switch the reply language.
LANG_REQUEST = re.compile(
    r"\b(?:speak|talk|reply|answer|respond|use|in|lo|mein|only)\b[^|]{0,20}\b(english|telugu|hindi|tamil)\b"
    r"|(?:^|:\s*|\|\s*)(english|telugu|hindi|tamil)\s*(?:please|only)?\s*[.!]?\s*(?:\||$)", re.I)
# Phrases that announce an action, in English / Telugu / Hindi (for the no-tool-call safety net).
ACTION_CLAIM = re.compile(
    r"\b(opening|now playing|playing it|searching for|launching|closing|typing|clicking|switching to|"
    r"locking|muting|pausing|skipping|right away)\b|"
    r"ఓపెన్ చేస్తున్నా|ప్లే చేస్తున్నా|వెతుకుతున్నా|సెట్ చేస్తున్నా|खोल रहा|चला रहा|सर्च कर रहा|सेट कर रहा", re.I)


class Brain:
    def __init__(self, api_key: str, model: str, base_url: str, backup_models=(), extra_providers=()):
        # Short read timeout: if a model hasn't started answering in 8 s, move on to a backup model
        # instead of leaving the user waiting (Gemini sometimes hangs for a minute).
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                             timeout=httpx.Timeout(8.0, connect=4.0))
        self.model = model
        self.models = [model] + [m for m in backup_models if m != model]
        # Other providers race in the same pool. Their models are named "prefix:model".
        self._clients = {}
        for prefix, p_url, p_key, p_models in extra_providers:
            self._clients[prefix] = OpenAI(base_url=p_url, api_key=p_key, max_retries=0,
                                           timeout=httpx.Timeout(8.0, connect=4.0))
            self.models += [f"{prefix}:{m}" for m in p_models]
        self.health = ModelHealth(self.models)
        self.last_language = "en"
        self.preferred_language = None     # set when the user asks for a language ("speak English")
        self.history = []
        # OpenRouter-only request extras; other gateways get plain Chat Completions.
        self.extra = {"reasoning": {"effort": "low"}} if "openrouter.ai" in base_url else {}
        self.current_model = model
        self.accepts_audio = "generativelanguage.googleapis.com" in base_url
        tools.llm_client, tools.llm_model = self.client, model
        tools.llm_is_openrouter = "openrouter.ai" in base_url
        tools.llm_is_gemini = "generativelanguage.googleapis.com" in base_url
        tools.generate_text = self.generate
        tools.generate_stream = self.generate_stream
        # Vision / web answers need the main provider's (Gemini) multimodal models.
        tools.best_models = lambda: [m for m in self.health.ranked() if ":" not in m] or [model]

    def reset(self):
        self.history.clear()

    def ask(self, user_text, on_sentence, on_tool_start, on_tool_end, cancelled=lambda: False, audio_wav_b64=None) -> str:
        """Stream a reply. Complete sentences go to on_sentence as soon as they're written.

        audio_wav_b64: the user's actual recording. Gemini listens to it directly (far better than the
        transcripts for Telugu/Hindi/English mixing); it's sent with the first round only.
        """
        if audio_wav_b64 and self.accepts_audio:
            self.history.append({"role": "user", "content": [
                {"type": "text", "text": user_text + "\n(The user's actual voice recording is attached. Listen to it: "
                                                     "it is the ground truth; the transcripts are rough helpers.)"},
                {"type": "input_audio", "input_audio": {"data": audio_wav_b64, "format": "wav"}}]})
            audio_msg = self.history[-1]
        else:
            self.history.append({"role": "user", "content": user_text})
            audio_msg = None
        spoken = []
        used_tools = nudged = False
        self.last_language = None          # set by this reply's [[xx]] tag; never reuse the previous turn's
        m = LANG_REQUEST.search(user_text)
        if m:
            word = next(g for g in m.groups() if g)
            self.preferred_language = {"english": "en", "telugu": "te", "hindi": "hi", "tamil": "ta"}[word.lower()]
            log.info("user asked for %s replies", word)
        for _ in range(MAX_TOOL_ROUNDS):
            content, calls = self._stream_once(on_sentence, spoken, cancelled)
            if audio_msg is not None:              # later rounds and turns only need the text
                audio_msg["content"] = user_text
                audio_msg = None
            if cancelled():
                self.history.append({"role": "assistant", "content": (content or "") + " [interrupted]"})
                return " ".join(spoken)
            if not calls and not (content or "").strip() and not nudged:
                # Safety net: an empty reply (no words, no action) - ask again instead of silently doing nothing.
                nudged = True
                log.warning("empty reply; retrying")
                self.history.append({"role": "assistant", "content": "(no reply)"})
                self.history.append({"role": "user", "content":
                                     "(automatic check, not from the user) Your reply was empty. Do what the user "
                                     "asked in their previous message now - call the needed tool(s), or answer."})
                continue
            if not calls:
                self.history.append({"role": "assistant", "content": content})
                # Safety net: the model announced an action but never called a tool.
                if not used_tools and not nudged and ACTION_CLAIM.search(content or ""):
                    nudged = True
                    log.warning("reply claimed an action without a tool call; nudging: %r", content)
                    self.history.append({"role": "user", "content":
                                         "(automatic check, not from the user) You described an action but made no "
                                         "tool call, so nothing happened. Call the needed tool(s) now, with no text "
                                         "before them. If no tool can do it, say so briefly."})
                    continue
                return " ".join(spoken)
            used_tools = True
            self.history.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [self._tool_call_message(c) for c in calls],
            })
            parsed = []
            for c in calls:
                try:
                    parsed.append(json.loads(c["args"] or "{}"))
                except json.JSONDecodeError:
                    parsed.append({})
            # Fast path: for plain actions, confirm straight away instead of waiting for a second model round.
            simple = not spoken and all(self._is_simple_action(c["name"], a) for c, a in zip(calls, parsed))
            if simple:
                pre = self._ack(calls[0]["name"], parsed[0], before=True)
                if pre:
                    on_sentence(pre)
                    spoken.append(pre)
            ok = True
            for c, preview_args in zip(calls, parsed):
                on_tool_start(c["name"], preview_args)
                args, result = tools.run_tool(c["name"], c["args"])
                log.info("tool %s(%s) -> %s", c["name"], args, result[:300])
                on_tool_end(c["name"], args, result)
                ok = ok and result.startswith("OK")
                self.history.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            if simple and ok and not cancelled():
                if any(c["name"] in self.ANNOUNCE_DONE for c in calls):
                    msg = self._ack(calls[0]["name"], parsed[0], before=False)
                    on_sentence(msg)
                    spoken.append(msg)
                elif not spoken:
                    post = self._ack(calls[0]["name"], parsed[0], before=False)
                    on_sentence(post)
                    spoken.append(post)
                self.history.append({"role": "assistant", "content": " ".join(spoken)})
                return " ".join(spoken)
        msg = "I seem to be going in circles on that one, Sir."
        on_sentence(msg)
        self.history.append({"role": "assistant", "content": msg})
        return msg

    # Tools whose success needs no explanation: confirm with a short line instead of a second model round.
    SIMPLE_ACTIONS = {"open_app", "close_app", "open_website", "web_search", "youtube_play", "youtube_control",
                      "media_key", "set_volume", "set_brightness", "set_timer", "take_screenshot", "show_desktop",
                      "lock_pc", "press_keys", "type_text", "scroll", "open_folder", "click_on_screen",
                      "voice_mode", "open_browser", "window_control", "show_dashboard", "remember", "forget",
                      "open_file", "set_theme", "write_code", "create", "canvas_control", "save_creation",
                      "revise_creation", "research", "explain_file", "gestures"}
    # Slow ones get their confirmation spoken while they run.
    SAY_BEFORE = {"open_app", "open_website", "web_search", "youtube_play", "open_folder", "close_app", "open_browser",
                  "show_dashboard", "write_code", "create", "revise_creation", "research", "explain_file"}
    # Slow actions that also get a "finished" line once they're done.
    ANNOUNCE_DONE = {"write_code", "create", "revise_creation", "research", "explain_file"}

    def _is_simple_action(self, name, args):
        if name not in self.SIMPLE_ACTIONS:
            return False
        if name in ("set_volume", "set_brightness") and not (set(args) & {"level", "change", "mute"}):
            return False                       # just reading the value - the model should say it
        if name == "window_control" and args.get("action") == "list":
            return False
        if name == "canvas_control" and args.get("action") == "run":
            return False                       # the model should tell the user what the program printed
        if name == "open_browser" and not args.get("account") and len(tools.chrome_profiles()) > 1:
            return False                       # the account chooser needs a question
        return True

    def _ack(self, name, args, before):
        lang = self.last_language or self.preferred_language or "en"
        if lang == "te":
            return "సరే సర్." if before else "సరే సర్, అయిపోయింది."
        if lang == "hi":
            return "ठीक है सर।" if before else "हो गया सर।"
        if before and name == "open_browser" and args.get("account"):
            p = tools._match_profile(args["account"], tools.chrome_profiles())
            return f"Opening Chrome as {p['name'].split()[0].capitalize() if p else args['account']}, Sir."
        if before and name in self.SAY_BEFORE:
            _, label = tools.describe(name, args)
            return label.replace(" · ", " as ") + ", Sir."
        if before:
            return None
        return {"youtube_control": {"like": "Liked, Sir.", "subscribe": "Subscribed, Sir.", "pause": "Paused, Sir.",
                                    "play": "Resumed, Sir.", "next_video": "Next one, Sir.",
                                    "skip_ad": "Ad skipped, Sir."}.get(args.get("action"), "Done, Sir."),
                "set_timer": "Timer set, Sir.", "take_screenshot": "Screenshot saved, Sir.",
                "remember": "I'll remember that, Sir.", "forget": "Forgotten, Sir.",
                "write_code": "Done, Sir. It's on the command center screen.",
                "create": "It's ready on the command center screen, Sir. Shall I save it?",
                "save_creation": "Saved, Sir.",
                "revise_creation": "Done, Sir. The new version is on the screen. Shall I save it?",
                "research": "Your research report is ready on the screen, Sir. Shall I save it?",
                "explain_file": "It's on the screen, Sir. Shall I save this summary?",
                "canvas_control": {"fullscreen": "Full screen, Sir.", "exit_fullscreen": "Back to the small screen, Sir.",
                                   "close": "Closed, Sir.", "run": "Running it now, Sir.",
                                   "open_in_vscode": "Opened in VS Code, Sir."}.get(args.get("action"), "Done, Sir."),
                "set_theme": "Theme switched, Sir.", "open_file": "Opening it now, Sir.",
                "lock_pc": "Locking now, Sir.", "voice_mode": "Done, Sir."}.get(name, "Done, Sir.")

    @staticmethod
    def _tool_call_message(c):
        msg = {"id": c["id"], "type": "function",
               "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
        if c.get("extra_content"):
            msg["extra_content"] = c["extra_content"]   # Gemini thought signature; must be sent back
        return msg

    HEDGE_AFTER = 2.2      # seconds without a first token before also asking the next model

    def generate_stream(self, prompt, on_text, max_tokens=12000):
        """Long streamed completion (creations): text goes to on_text as it's written. Tries the healthiest
        models in turn; a model that hasn't started within 15 s is skipped."""
        last = None
        for model in self.health.ranked()[:4]:
            client, name = self._route(model)
            t = time.monotonic()
            parts = []
            try:
                stream = client.with_options(timeout=httpx.Timeout(15.0, connect=6.0)).chat.completions.create(
                    model=name, max_tokens=max_tokens, stream=True,
                    messages=[{"role": "user", "content": prompt}])
                first = True
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    piece = chunk.choices[0].delta.content or ""
                    if piece:
                        if first:
                            self.health.ok(model, time.monotonic() - t)
                            first = False
                        parts.append(piece)
                        on_text(piece)
                text = "".join(parts)
                if text.strip():
                    log.info("streamed %d chars with %s", len(text), model)
                    return text
            except Exception as e:
                if parts:                                   # got most of it before the stream broke
                    log.warning("stream via %s broke after %d chars: %s", model, sum(map(len, parts)), str(e)[:80])
                    return "".join(parts)
                self.health.fail(model, weight=12 if "quota" in str(e).lower() else 1)
                log.warning("stream via %s failed: %s", model, str(e)[:120])
                last = e
        raise last or RuntimeError("no model could generate")

    def generate(self, prompt, max_tokens=8000):
        """One long, non-streamed completion (e.g. writing code), trying the healthiest models in turn."""
        last = None
        for model in self.health.ranked()[:4]:
            client, name = self._route(model)
            t = time.monotonic()
            try:
                r = client.with_options(timeout=httpx.Timeout(120.0, connect=6.0)).chat.completions.create(
                    model=name, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
                text = r.choices[0].message.content or ""
                if text.strip():
                    self.health.ok(model, min(time.monotonic() - t, 10))
                    log.info("generated %d chars with %s", len(text), model)
                    return text
            except Exception as e:
                self.health.fail(model, weight=12 if "quota" in str(e).lower() else 1)
                log.warning("generate via %s failed: %s", model, str(e)[:120])
                last = e
        raise last or RuntimeError("no model could generate")

    def _route(self, model_key):
        """(client, model name) for a pool entry like 'gemini-3.6-flash' or 'xpl:deepseek-v4.1-flash'."""
        if ":" in model_key:
            prefix, name = model_key.split(":", 1)
            return self._clients[prefix], name
        return self.client, model_key

    @staticmethod
    def _text_only(messages):
        """Copy for other providers: attached audio removed, Gemini-only thought signatures stripped."""
        out = []
        for m in messages:
            if isinstance(m.get("content"), list):
                text = " ".join(p.get("text", "") for p in m["content"] if p.get("type") == "text")
                m = dict(m, content=text)
            if m.get("tool_calls"):
                m = dict(m, tool_calls=[{k: v for k, v in tc.items() if k != "extra_content"} for tc in m["tool_calls"]])
            out.append(m)
        return out

    @staticmethod
    def _for_gemini(messages):
        """Gemini 3 requires a thought signature on every earlier tool call; calls made by another
        provider have none, so give them Google's documented placeholder."""
        out = []
        for m in messages:
            if m.get("tool_calls") and any("extra_content" not in tc for tc in m["tool_calls"]):
                m = dict(m, tool_calls=[tc if "extra_content" in tc else dict(
                    tc, extra_content={"google": {"thought_signature": "skip_thought_signature_validator"}})
                    for tc in m["tool_calls"]])
            out.append(m)
        return out

    def warm_up(self, top=3):
        """Ping the first few models once (in parallel) to rank them before the first real request.
        Only a few: every ping counts against the free daily quota."""
        def probe(model):
            t = time.monotonic()
            try:
                client, name = self._route(model)
                client.with_options(timeout=httpx.Timeout(10.0, connect=4.0)).chat.completions.create(
                    model=name, max_tokens=5, messages=[{"role": "user", "content": "Say OK."}])
                self.health.ok(model, time.monotonic() - t)
            except Exception as e:
                self.health.fail(model, weight=12 if "quota" in str(e).lower() else 1)
        threads = [threading.Thread(target=probe, args=(m,), daemon=True) for m in self.health.ranked()[:top]]
        for th in threads:
            th.start()
        for th in threads:
            th.join(12)
        log.info("model ranking: %s", self.health.summary())

    def _open_stream(self):
        """Start a streamed completion. Models are raced: if one hasn't started answering within
        HEDGE_AFTER seconds, the next is asked too, and whichever answers first wins."""
        local = datetime.datetime.now().astimezone()
        now = local.strftime("%A %d %B %Y, %I:%M %p").replace(" 0", " ")
        offset = local.strftime("%z")
        system = SYSTEM_PROMPT + (
            f"\n\nCurrent local date and time (exact, from the PC clock): {now}, {local.tzname()} "
            f"(UTC{offset[:3]}:{offset[3:]}). The user is in India, so this IS the time in India. Answer time/date "
            f"questions about India straight from this, exactly to the minute (e.g. \"It's 3:19 PM, Sir\"). "
            f"For other countries use world_time.")
        extra = tools.everyday.prompt_context()
        if extra:
            system += "\n\n" + extra
        facts = tools.memories()
        if facts:
            system += "\n\nThings the user asked you to remember (use them when relevant):\n" + "\n".join(
                f"- {m['fact']} (saved {m['date']})" for m in facts[-60:])
        messages = [{"role": "system", "content": system}] + self._trimmed()
        results = queue.Queue()

        def attempt(model):
            stream = None
            t = time.monotonic()
            try:
                client, name = self._route(model)
                foreign = ":" in model
                msgs = self._text_only(messages) if foreign else (
                    self._for_gemini(messages) if self.accepts_audio else messages)
                stream = client.chat.completions.create(
                    model=name, messages=msgs, tools=tools.TOOLS,
                    max_tokens=900, stream=True, extra_body={} if foreign else self.extra)
                it = iter(stream)
                first = next(it, None)            # wait for the first token
                self.health.ok(model, time.monotonic() - t)
                results.put(("ok", model, stream, it, first))
            except Exception as e:
                # "exceeded your current quota" = this model's free daily limit is used up: park it for hours.
                self.health.fail(model, weight=12 if "quota" in str(e).lower() else 1)
                if stream is not None:
                    stream.close()
                results.put(("err", model, e))

        pending = self.health.ranked()           # fastest-known model first
        running = 0
        last_error = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if pending and (running == 0 or not results.qsize()):
                threading.Thread(target=attempt, args=(pending.pop(0),), daemon=True).start()
                running += 1
            try:
                r = results.get(timeout=self.HEDGE_AFTER if pending else max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                continue                          # slow: launch the next model in parallel
            running -= 1
            if r[0] == "err":
                e = r[2]
                if "card_required" in str(e) or "insufficient_quota" in str(e) or "401" in str(e):
                    raise e
                log.warning("%s failed (%s)", r[1], str(e)[:120])
                last_error = e
                if running == 0 and not pending:
                    break
                continue
            _, model, stream, it, first = r
            self.current_model = model
            log.info("answered by %s", model)
            # close the losers when they finish
            threading.Thread(target=self._drain_losers, args=(results, running), daemon=True).start()
            return _Stream(stream, it, first)
        raise last_error or TimeoutError("no model answered within 30 seconds")

    @staticmethod
    def _drain_losers(results, count):
        for _ in range(count):
            try:
                r = results.get(timeout=40)
            except queue.Empty:
                return
            if r[0] == "ok":
                r[2].close()

    def _stream_once(self, on_sentence, spoken, cancelled):
        stream = self._open_stream()
        content, pending, calls = "", "", {}
        head_done = False
        try:
            for chunk in stream:
                if cancelled():
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content += delta.content
                    pending += delta.content
                    if not head_done:
                        pending, head_done = self._strip_language_tag(pending)
                        if not head_done:
                            continue
                    pending = self._emit_sentences(pending, on_sentence, spoken)
                for tc in delta.tool_calls or []:
                    index = tc.index if tc.index is not None else len(calls)
                    entry = calls.setdefault(index, {"id": "", "name": "", "args": "", "extra_content": None})
                    if tc.id:
                        entry["id"] = tc.id
                    extra = (tc.model_extra or {}).get("extra_content")
                    if extra:
                        entry["extra_content"] = extra
                    if tc.function and tc.function.name:
                        entry["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["args"] += tc.function.arguments
        finally:
            stream.close()
        if not head_done:
            pending, _ = self._strip_language_tag(pending, final=True)
        if pending.strip() and not cancelled():
            on_sentence(pending.strip())
            spoken.append(pending.strip())
        content = LANG_TAG.sub("", content, count=1)
        return content.strip(), [calls[i] for i in sorted(calls)]

    def _strip_language_tag(self, text, final=False):
        """Remove a leading [[xx]] tag. Returns (text, decided) - undecided while it may still be arriving."""
        m = LANG_TAG.match(text)
        if m:
            self.last_language = m.group(1).lower()
            return text[m.end():], True
        stripped = text.lstrip()
        if not final and (stripped == "" or ("[[".startswith(stripped[:2]) and len(stripped) < 10)):
            return text, False
        return text, True

    @staticmethod
    def _emit_sentences(text, on_sentence, spoken):
        while True:
            m = SENTENCE_END.match(text)
            if not m:
                return text
            sentence = m.group(1).strip()
            if sentence:
                on_sentence(sentence)
                spoken.append(sentence)
            text = text[m.end():]

    def _trimmed(self):
        h = self.history[-MAX_HISTORY:]
        while h and h[0]["role"] != "user":
            h = h[1:]
        return h
