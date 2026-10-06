"""System-wide hand-gesture control: the webcam + MediaPipe hand tracking run inside Jarvis (not in the
browser), so gestures keep working in every app, even with the command center minimised.

  ☝️ point      move the mouse cursor          🤏 pinch (while pointing)  click
                (only when the user explicitly turns on mouse control - off by default)
  ✋ open palm  talk to Jarvis                  ✊ fist                    stop Jarvis / pause-play media
  👍 thumbs up  "yes"                           ✌️ victory                 full screen
  👋 swipe      next / previous (arrow keys: slides, photos, video seek)

The camera is used only while gesture control is on, and frames never leave this PC.
"""

import ctypes
import logging
import math
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.gestures")
MODEL = Path(__file__).resolve().parent / "web" / "vendor" / "hand_landmarker.task"
HOLD_FRAMES = 7          # a pose must be held this many frames (~0.3 s) to fire
COOLDOWN = 1.2           # seconds between pose actions


def _d(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def classify(l):
    """(gesture, pinch) from 21 hand landmarks."""
    ext = lambda tip, pip: _d(l[tip], l[0]) > _d(l[pip], l[0]) * 1.12
    idx, mid, rng, pnk = ext(8, 6), ext(12, 10), ext(16, 14), ext(20, 18)
    thumb_out = _d(l[4], l[5]) > _d(l[3], l[5]) * 1.25 and _d(l[4], l[9]) > _d(l[0], l[9]) * 0.55
    pinch = _d(l[4], l[8]) < _d(l[5], l[0]) * 0.28
    if idx and mid and rng and pnk and thumb_out:
        return "palm", pinch
    if not (idx or mid or rng or pnk) and thumb_out and l[4].y < l[3].y and l[4].y < l[5].y - 0.04:
        return "thumbs", pinch
    if not (idx or mid or rng or pnk):
        return "fist", pinch
    if idx and mid and not rng and not pnk:
        return "victory", pinch
    if idx and not mid and not rng and not pnk:
        return "point", pinch
    return "other", pinch


class _Mouse:
    user32 = ctypes.windll.user32

    def __init__(self):
        self.w, self.h = self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)

    def move(self, x, y):
        self.user32.SetCursorPos(int(x), int(y))

    def click(self):
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)      # left down
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)      # left up


class GestureEngine:
    def __init__(self, on_gesture, publish=lambda e: None, wants_preview=lambda: False):
        self.on_gesture = on_gesture          # callback(name): talk / stop / yes / fullscreen / next / prev
        self.publish = publish                # command-center events (hand skeleton for the HUD)
        self.wants_preview = wants_preview    # only stream the skeleton while the dashboard is visible
        self._stop = threading.Event()
        self.mouse = False    # moving / clicking the real mouse only when the user explicitly asked for it
        self._thread = None
        self.error = ""

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout=8.0):
        """Start; waits until the camera delivers frames. Returns '' or an error message."""
        if self.running:
            return ""
        self._stop.clear()
        self.error = ""
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gestures", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.error

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        self._thread = None
        self.publish({"type": "gestures", "on": False})

    def _run(self):
        cap = None
        try:
            import cv2
            from mediapipe import Image, ImageFormat
            from mediapipe.tasks.python import BaseOptions, vision
            landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(MODEL)), running_mode=vision.RunningMode.VIDEO,
                num_hands=1, min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5))
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("the camera is not available (in use by another app, or blocked in Windows "
                                   "Settings → Privacy → Camera)")
        except Exception as e:
            self.error = str(e)
            log.warning("gesture control failed to start: %s", e)
            if cap is not None:
                cap.release()
            self._ready.set()
            return
        log.info("gesture control on")
        self.publish({"type": "gestures", "on": True})
        self._ready.set()
        mouse = _Mouse()
        held, count, last_fire, xs = "", 0, 0.0, []
        cur = [mouse.w / 2, mouse.h / 2]
        was_pinch, last_pub, t0 = False, 0.0, time.monotonic()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                now = time.monotonic()
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                res = landmarker.detect_for_video(Image(image_format=ImageFormat.SRGB, data=rgb), int((now - t0) * 1000))
                l = res.hand_landmarks[0] if res.hand_landmarks else None
                g, pinch = classify(l) if l else ("none", False)
                if now - last_pub > 0.05 and self.wants_preview():      # ~20 fps for the atom
                    last_pub = now
                    self.publish({"type": "hand", "g": g, "pinch": pinch,
                                  "pts": [[round(p.x, 3), round(p.y, 3)] for p in l] if l else []})
                if not l:
                    held, count, xs = "", 0, []
                    continue
                # pointing: the index fingertip drives the real mouse; a pinch clicks
                if (g == "point" or (pinch and held == "point")) and not self.mouse:
                    held, was_pinch = "point", pinch       # pointing does nothing unless mouse control is on
                    continue
                if g == "point" or (pinch and held == "point"):
                    tx = ((1 - l[8].x) * 1.4 - 0.2) * mouse.w           # mirrored, edges reachable
                    ty = (l[8].y * 1.4 - 0.2) * mouse.h
                    k = 0.35 if math.hypot(tx - cur[0], ty - cur[1]) > 40 else 0.15   # smooth out jitter
                    cur[0] += (tx - cur[0]) * k
                    cur[1] += (ty - cur[1]) * k
                    mouse.move(min(max(cur[0], 0), mouse.w - 1), min(max(cur[1], 0), mouse.h - 1))
                    if pinch and not was_pinch and now - last_fire > 0.6:
                        last_fire = now
                        mouse.click()
                    was_pinch = pinch
                    held = "point"
                    continue
                was_pinch = False
                if self.wants_preview():
                    # the command center is on screen: the hand is sculpting the 3D atom there, so only the
                    # deliberate thumbs-up / victory poses act; palm, fist and swipes stay with the atom
                    count = count + 1 if g == held else 0
                    held = g
                    if count == HOLD_FRAMES and g == "thumbs" and now - last_fire > COOLDOWN:
                        last_fire = now
                        self._fire("yes")
                    continue
                # swipe: a fast sideways hand movement
                xs = [(t, x) for t, x in xs if now - t < 0.35] + [(now, l[0].x)]
                if len(xs) > 4 and now - last_fire > 0.9:
                    dx = xs[-1][1] - xs[0][1]
                    if abs(dx) > 0.28:
                        last_fire, xs = now, []
                        self._fire("next" if dx < 0 else "prev")      # camera is mirrored
                        continue
                count = count + 1 if g == held else 0
                held = g
                if count == HOLD_FRAMES and g in ("palm", "fist", "thumbs", "victory") and now - last_fire > COOLDOWN:
                    last_fire = now
                    self._fire({"palm": "talk", "fist": "stop", "thumbs": "yes", "victory": "fullscreen"}[g])
        except Exception:
            log.exception("gesture loop crashed")
        finally:
            cap.release()
            landmarker.close()
            log.info("gesture control off")

    def _fire(self, name):
        log.info("gesture: %s", name)
        self.publish({"type": "gesture_fired", "name": name})
        try:
            self.on_gesture(name)
        except Exception:
            log.exception("gesture action %s failed", name)
