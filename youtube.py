"""A Chrome window that Jarvis controls, for playing and interacting with YouTube.

Uses its own browser profile (jarvis/browser_profile), so you sign in to
YouTube once in that window and Jarvis stays signed in afterwards.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except Exception as _e:          # e.g. Windows Smart App Control blocks greenlet's DLL - work without Playwright
    class PlaywrightError(Exception):
        pass
    sync_playwright = None
    PLAYWRIGHT_MISSING = str(_e)
else:
    PLAYWRIGHT_MISSING = ""

PROFILE_DIR = Path(__file__).resolve().parent / "browser_profile"

# YouTube's own keyboard shortcuts (sent to the player page).
PLAYER_KEYS = {
    "pause": "k",
    "play": "k",
    "mute": "m",
    "unmute": "m",
    "fullscreen": "f",
    "exit_fullscreen": "f",
    "forward_10s": "l",
    "back_10s": "j",
    "captions": "c",
    "next_video": "Shift+N",
    "volume_up": "ArrowUp",
    "volume_down": "ArrowDown",
    "speed_up": "Shift+Period",
    "slow_down": "Shift+Comma",
}

# Actions that work on any browser tab through Windows media keys (virtual-key codes).
MEDIA_FALLBACK = {"pause": 0xB3, "play": 0xB3, "next_video": 0xB0}

VISIBLE_LIKE ='button[aria-label^="like this video" i] >> visible=true'
VISIBLE_DISLIKE = 'button[aria-label^="dislike this video" i] >> visible=true'
VISIBLE_SUBSCRIBE = '#subscribe-button button >> visible=true'
SIGN_IN_LINK = 'a[aria-label="Sign in"]'


class YouTube:
    def __init__(self):
        self._pw = None
        self._ctx = None
        self._page = None

    # ---------------------------------------------------------------- browser
    def _ensure_page(self):
        """Launch (or re-launch, if the user closed it) the Jarvis browser."""
        if self._page is not None and not self._page.is_closed() and self._alive():
            return self._page
        self._page = None
        if self._ctx is not None:
            try:
                self._ctx.close()
            except PlaywrightError:
                pass
            self._ctx = None
        if sync_playwright is None:
            raise RuntimeError(f"browser automation is unavailable ({PLAYWRIGHT_MISSING})")
        if self._pw is None:
            self._pw = sync_playwright().start()
        last_error = None
        for channel in ("chrome", "msedge"):
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(PROFILE_DIR),
                    channel=channel,
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--autoplay-policy=no-user-gesture-required"],
                    ignore_default_args=["--enable-automation"],
                )
                break
            except PlaywrightError as e:
                last_error = e
        else:
            raise RuntimeError(f"could not start Chrome or Edge: {last_error}")
        pages = self._ctx.pages
        self._page = pages[0] if pages else self._ctx.new_page()
        return self._page

    def _alive(self) -> bool:
        """True if the browser window is still open (the user may have closed it)."""
        try:
            self._page.evaluate("1")
            return True
        except PlaywrightError:
            return False

    def _watch_page(self):
        page = self._ensure_page()
        if "youtube.com/watch" not in page.url:
            return None
        return page

    def _signed_in(self, page) -> bool:
        return page.locator(SIGN_IN_LINK).count() == 0

    def _title(self, page) -> str:
        return page.evaluate(
            "() => document.querySelector('h1.ytd-watch-metadata, #title h1')?.textContent?.trim() || ''"
        )

    # ---------------------------------------------------------------- actions
    @staticmethod
    def _search_top(query: str):
        """Top search result as (video_id, title), fetched over HTTP - much faster than loading the results page."""
        req = Request("https://www.youtube.com/results?search_query=" + quote_plus(query) + "&sp=EgIQAQ%253D%253D",
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150 Safari/537.36",
                               "Accept-Language": "en-US,en;q=0.9"})
        with urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"', html)
        if not m:
            return None, None
        return m.group(1), json.loads(f'"{m.group(2)}"')

    open_url = None    # set by tools: opens a URL in the user's Chrome (used when Playwright is unavailable)

    def play(self, query: str) -> str:
        try:
            video_id, title = self._search_top(query)
        except Exception:
            video_id, title = None, None
        if sync_playwright is None and self.open_url:
            url = (f"https://www.youtube.com/watch?v={video_id}" if video_id
                   else "https://www.youtube.com/results?search_query=" + quote_plus(query))
            self.open_url(url)
            return (f"OK: now playing '{title or query}' on YouTube." if video_id
                    else f"OK: showing YouTube results for '{query}'.")
        page = self._ensure_page()
        if video_id is None:            # fall back to the search page in the browser
            page.goto("https://www.youtube.com/results?search_query=" + quote_plus(query), wait_until="domcontentloaded")
            try:
                page.wait_for_selector("ytd-video-renderer a#video-title", timeout=15000)
            except PlaywrightError:
                return f"FAILED: no YouTube results appeared for '{query}'."
            href = page.evaluate("() => document.querySelector('ytd-video-renderer a#video-title')?.getAttribute('href')")
            video_id = parse_qs(urlparse(href or "").query).get("v", [None])[0]
            if not video_id:
                return f"FAILED: could not find a playable video for '{query}'."
        page.goto(f"https://www.youtube.com/watch?v={video_id}", wait_until="domcontentloaded")
        page.bring_to_front()
        try:
            # YouTube is a single-page app: wait until the player really shows the NEW video.
            page.wait_for_function(
                "id => document.querySelector('ytd-watch-flexy')?.getAttribute('video-id') === id"
                " && document.querySelector('video')", arg=video_id, timeout=15000)
        except PlaywrightError:
            return f"FAILED: the video for '{query}' did not load."
        shown = self._title(page)
        return f"OK: now playing '{title or shown or query}' on YouTube."

    def control(self, action: str) -> str:
        # Only use the Jarvis window if it's already open on a video - never launch a browser just to check.
        page = self._page
        if page is None or page.is_closed() or not self._alive() or "youtube.com/watch" not in page.url:
            page = None
        if page is None:
            # The video is probably in the user's normal Chrome: basic controls work through media keys.
            key = MEDIA_FALLBACK.get(action)
            if key is not None:
                import ctypes
                ctypes.windll.user32.keybd_event(key, 0, 0, 0)
                ctypes.windll.user32.keybd_event(key, 0, 2, 0)
                return f"OK: sent {action.replace('_', ' ')} to the video playing in the browser."
            return ("FAILED: no video is open in the Jarvis YouTube window, and that action needs it. "
                    "Use click_on_screen for videos in the normal browser.")
        page.bring_to_front()

        if action in ("like", "unlike", "dislike"):
            return self._rate(page, action)
        if action == "subscribe":
            return self._subscribe(page)
        if action == "skip_ad":
            skip = page.locator(".ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button")
            if skip.count() and skip.first.is_visible():
                skip.first.click()
                return "OK: ad skipped."
            return "FAILED: there is no skippable ad showing right now."

        key = PLAYER_KEYS.get(action)
        if key is None:
            return f"FAILED: unknown YouTube action '{action}'."
        paused = page.evaluate("() => document.querySelector('video')?.paused")
        if action == "pause" and paused:
            return "OK: video was already paused."
        if action == "play" and paused is False:
            return "OK: video is already playing."
        page.locator("#movie_player").focus()
        page.keyboard.press(key)
        if action == "next_video":
            page.wait_for_timeout(2500)
            return f"OK: skipped to '{self._title(page)}'."
        return f"OK: {action.replace('_', ' ')} done."

    def _rate(self, page, action: str) -> str:
        if not self._signed_in(page):
            return ("FAILED: not signed in to YouTube in the Jarvis browser. "
                    "The user must sign in once in that window.")
        selector = VISIBLE_DISLIKE if action == "dislike" else VISIBLE_LIKE
        button = page.locator(selector).first
        try:
            button.wait_for(timeout=8000)
        except PlaywrightError:
            return "FAILED: could not find the like button on this page."
        pressed = button.get_attribute("aria-pressed") == "true"
        title = self._title(page)
        if action == "unlike":
            if not pressed:
                return f"OK: '{title}' was not liked."
            button.click()
            return f"OK: removed the like from '{title}'."
        if pressed:
            return f"OK: '{title}' was already {action}d."
        button.click()
        page.wait_for_timeout(800)
        if button.get_attribute("aria-pressed") == "true":
            return f"OK: {action}d '{title}'."
        return f"FAILED: clicked {action} but YouTube did not register it."

    def _subscribe(self, page) -> str:
        if not self._signed_in(page):
            return ("FAILED: not signed in to YouTube in the Jarvis browser. "
                    "The user must sign in once in that window.")
        button = page.locator(VISIBLE_SUBSCRIBE).first
        try:
            button.wait_for(timeout=8000)
        except PlaywrightError:
            return "FAILED: could not find the subscribe button."
        label = (button.get_attribute("aria-label") or button.inner_text()).lower()
        if "unsubscribe" in label or "subscribed" in button.inner_text().lower():
            return "OK: already subscribed to this channel."
        button.click()
        return "OK: subscribed to the channel."

    def open_for_sign_in(self) -> str:
        page = self._ensure_page()
        page.bring_to_front()
        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        if self._signed_in(page):
            return "OK: already signed in to YouTube in the Jarvis browser."
        return ("OK: opened YouTube in the Jarvis browser. The user must click Sign in "
                "and log in themselves; Jarvis stays signed in afterwards.")

    def close(self):
        try:
            if self._ctx is not None:
                self._ctx.close()
            if self._pw is not None:
                self._pw.stop()
        except PlaywrightError:
            pass
