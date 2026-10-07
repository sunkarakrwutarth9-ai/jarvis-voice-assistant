"""The Dynamic Island - Iron Man HUD edition.

A floating pill at the top of the screen that morphs with Jarvis's state:
hot-rod red and gold armour plating, an arc reactor that spins while thinking,
HUD corner brackets, a scanning sweep and equaliser bars. Beside it, a round
"Me" avatar (the user's own face, lip-synced to the speech).

- Idle: shrinks to a thin gold line at the top edge so it doesn't cover browser tabs.
  Hover to expand; the avatar circle appears beside it.
- Choice: a list of options (e.g. Chrome accounts) you can click or answer by voice.
- Left-click the pill: talk / interrupt. Right-click: menu. Click the circle: Me mode.
"""

import ctypes
import json
import math
import random
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QMenu, QWidget

# ---- Mark-armour palette
RED = QColor(206, 30, 38)
RED_DEEP = QColor(96, 8, 14)
GOLD = QColor(244, 186, 66)
GOLD_DIM = QColor(150, 108, 38)
ARC = QColor(130, 232, 255)        # arc-reactor core
ARC_THINK = QColor(255, 196, 90)   # reactor runs hot while thinking
ERROR = QColor(255, 70, 60)
TEXT = QColor(246, 242, 236)
SUBTEXT = QColor(190, 168, 140)

GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
_ASSETS = Path(__file__).resolve().parent / "assets"
# Prefer the steady talking photo; fall back to the raw video frames.
AVATAR_DIR = _ASSETS / "avatar_photo" if (_ASSETS / "avatar_photo" / "index.json").exists() else _ASSETS / "avatar"

WIN_W, WIN_H = 820, 330
TOP = 4
GAP = 10
ROW_H = 48

SIZES = {                 # (width, height) of the pill per state
    "sliver": (104, 8),
    "idle": (190, 36),
    "followup": (260, 36),
    "listening": (330, 62),
    "thinking": (420, 70),
    "action": (420, 62),
    "speaking": (500, 70),     # height grows with the text
    "error": (440, 70),
    "choice": (470, 70),       # height grows with the options
}
HEADERS = {
    "listening": "AUDIO INPUT", "thinking": "PROCESSING", "action": "EXECUTING PROTOCOL",
    "speaking": "RESPONSE", "error": "SYSTEM ALERT", "choice": "AWAITING SELECTION",
}


class Spring:
    """Slightly under-damped spring: overshoots a touch, like iOS."""

    def __init__(self, value, stiffness=200.0, damping_ratio=0.66):
        self.value = self.target = float(value)
        self.velocity = 0.0
        self.k = stiffness
        self.c = 2 * math.sqrt(stiffness) * damping_ratio

    def step(self, dt):
        accel = self.k * (self.target - self.value) - self.c * self.velocity
        self.velocity += accel * dt
        self.value += self.velocity * dt


class Avatar:
    """The user's face; picks the frame whose mouth opening matches the speech loudness."""

    def __init__(self, dpr):
        self.frames = []
        self.ok = False
        try:
            idx = json.loads((AVATAR_DIR / "index.json").read_text())
        except (OSError, ValueError):
            return
        px = int(180 * dpr)
        for f in idx["frames"]:
            pm = QPixmap(str(AVATAR_DIR / f["file"]))
            if pm.isNull():
                continue
            pm = pm.scaled(px, px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self.frames.append((f["jaw"], f["t"], pm))
        if not self.frames:
            return
        self.rest = min(max(idx.get("rest", 0), 0), len(self.frames) - 1)
        self.max_jaw = max(j for j, _, _ in self.frames) or 0.5
        self.cur = self.prev = self.rest
        self.fade = 1.0
        self._last_switch = 0.0
        self.ok = True

    def update(self, level, speaking, now):
        if now - self._last_switch < 0.05:
            return
        if not speaking:
            target = self.rest
        else:
            want = min(self.max_jaw, level * self.max_jaw * 0.9)
            prev_t = self.frames[self.cur][1]
            ranked = sorted(range(len(self.frames)), key=lambda i: abs(self.frames[i][0] - want))[:6]
            target = min(ranked, key=lambda i: abs(self.frames[i][1] - prev_t - 1) + 40 * abs(self.frames[i][0] - want))
        if target != self.cur:
            self.prev, self.cur, self.fade = self.cur, target, 0.0
            self._last_switch = now

    def paint(self, p: QPainter, rect: QRectF, dt):
        self.fade = min(1.0, self.fade + dt / 0.06)
        base = p.opacity()
        for idx, alpha in ((self.prev, 1.0), (self.cur, self.fade)):
            pm = self.frames[idx][2]
            p.setOpacity(base * alpha)
            p.drawPixmap(rect, pm, QRectF(0, 0, pm.width(), pm.height()))
        p.setOpacity(base)


class Island(QWidget):
    clicked = Signal()
    avatar_clicked = Signal()
    choice_picked = Signal(str)
    quit_requested = Signal()
    mute_toggled = Signal(bool)
    new_chat = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(WIN_W, WIN_H)
        self._place()

        self.state = "idle"
        self.theme = "ios"
        self.appearance = "light"
        self.title = self.body = self.icon = ""
        self.choice_title = ""
        self.choices = []                     # [(label, sublabel, value)]
        self._hover_row = -1
        self.muted = False
        self.me_mode = False
        self.speech_level = lambda: 0.0      # set by the app (Speaker.level)
        self.w = Spring(SIZES["sliver"][0])
        self.h = Spring(SIZES["sliver"][1])
        self.circle = Spring(0, stiffness=220)
        self.content_alpha = 1.0
        self.mic_level = 0.0
        self._level_smooth = 0.0
        self._speech_smooth = 0.0
        self._bars = [0.15] * 7
        self._phase = 0.0
        self._spin = 0.0
        self._last = time.monotonic()
        self._hover = False
        self._hover_until = 0.0
        self._interactive = True
        self._flash_until = 0.0
        self._dt = 0.016
        self.avatar = Avatar(self.devicePixelRatioF())

        self.f_header = QFont("Bahnschrift SemiBold SemiConden", 7)
        self.f_header.setLetterSpacing(QFont.AbsoluteSpacing, 2.2)
        self.f_title = QFont("Bahnschrift SemiBold", 11)
        self.f_title.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        self.f_body_latin = QFont("Segoe UI Variable Text", 11)
        self.f_body_indic = QFont("Nirmala UI", 11)      # proper line height for Telugu/Hindi/Tamil...
        self.f_body = self.f_body_latin
        self.f_row = QFont("Bahnschrift SemiBold", 11)
        self.f_row_sub = QFont("Bahnschrift Light", 8)
        self.f_idx = QFont("Bahnschrift SemiBold Condensed", 10)
        self.f_icon = QFont("Segoe Fluent Icons", 15)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # ------------------------------------------------------------------ API
    def set_state(self, state, title="", body="", icon=""):
        if state != "choice":
            self.choices = []
        changed = state != self.state or title != self.title
        self.state, self.title, self.body, self.icon = state, title, body, icon
        indic = any(0x0900 <= ord(ch) <= 0x0DFF for ch in body)
        self.f_body = self.f_body_indic if indic else self.f_body_latin
        if changed:
            self.content_alpha = 0.0
            self.w.velocity += 70     # a little kick so every change "breathes"

    def set_choice(self, title, options):
        self.set_state("choice", title)
        self.choice_title = title
        self.choices = [tuple(o) for o in options]

    def set_level(self, level):
        self.mic_level = level

    def set_theme(self, theme):
        # the cinematic command center uses the iOS-style island (in dark mode)
        self.theme = theme if theme in ("ios", "ironman") else "ios"
        self.flash()

    def set_dashboard_visible(self, visible):
        """The command center is on screen: get out of the way (and stop drawing)."""
        if visible:
            self._timer.stop()
            self.hide()
        else:
            self.show()
            self._last = time.monotonic()
            self._timer.start(16)

    def set_me_mode(self, on):
        self.me_mode = on
        self.flash()

    def flash(self, seconds=0.35):
        self._flash_until = time.monotonic() + seconds

    # --------------------------------------------------------------- layout
    def _place(self):
        screen = QGuiApplication.primaryScreen().geometry()
        self.move(screen.x() + (screen.width() - WIN_W) // 2, screen.y())

    def _target_size(self):
        if self.state == "idle":
            return SIZES["idle"] if self._hover_idle() else SIZES["sliver"]
        w, h = SIZES.get(self.state, SIZES["action"])
        if self.state == "speaking" and self.body:
            h = max(h, self._text_height(self.body, w) + 44)
        elif self.state == "choice":
            h = 46 + ROW_H * len(self.choices) + 10
        return w, h

    def _hover_idle(self):
        return self._hover or time.monotonic() < self._hover_until

    def _circle_target(self):
        if self.state == "choice":
            return 0
        if self.state == "idle":
            show = self._hover_idle()
        else:
            show = self.me_mode or self._hover
        if not show or (not self.avatar.ok and not self.me_mode and self.state != "idle"):
            return 0
        return 36 if self.state in ("idle", "followup") else min(78, max(62, self.h.value))

    def _pill_rect(self):
        w, h = self.w.value, self.h.value
        return QRectF((WIN_W - w) / 2, TOP, w, h)

    def _circle_rect(self):
        d = max(0.0, self.circle.value)
        pill = self._pill_rect()
        return QRectF(pill.right() + GAP * min(1, d / 20), TOP, d, d)

    def _content_rect(self, pill: QRectF):
        left = pill.left() + (44 if self.state == "choice" else 66)
        right = pill.right() - (74 if self.state in ("listening", "speaking") else 24)
        return QRectF(left, pill.top() + 8, max(10, right - left), pill.height() - 16)

    def _row_rect(self, pill, i):
        return QRectF(pill.left() + 16, pill.top() + 44 + i * ROW_H, pill.width() - 32, ROW_H - 6)

    def _text_height(self, text, pill_w):
        fm = QFontMetricsF(self.f_body)
        r = fm.boundingRect(QRectF(0, 0, pill_w - 140, 1000), Qt.TextWordWrap, text)
        return min(max(r.height(), fm.height()), WIN_H - 80)

    def _radius(self, pill):
        return min(pill.height() / 2, 22 if self.state == "choice" else 30)

    # ------------------------------------------------------------ animation
    def _tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        self._dt = dt
        self._phase += dt
        spin_speed = {"thinking": 5.0, "action": 3.5, "listening": 1.6, "speaking": 1.2}.get(self.state, 0.5)
        self._spin += dt * spin_speed
        self._update_hover(now)
        self.w.target, self.h.target = self._target_size()
        self.circle.target = self._circle_target()
        self.w.step(dt)
        self.h.step(dt)
        self.circle.step(dt)
        near = abs(self.w.target - self.w.value) < 25 and abs(self.h.target - self.h.value) < 12
        self.content_alpha = min(1.0, self.content_alpha + dt * 5) if near else max(0.0, self.content_alpha - dt * 8)
        self._level_smooth += (self.mic_level - self._level_smooth) * min(1, dt * 18)
        speech = self.speech_level()
        self._speech_smooth += (speech - self._speech_smooth) * min(1, dt * 25)
        if self.avatar.ok:
            self.avatar.update(self._speech_smooth, self.state == "speaking" and speech > 0.02, now)
        self._update_bars(dt)
        # Full 60 fps only while something moves; a resting sliver just needs a slow "breathe".
        settled = (abs(self.w.velocity) < 1 and abs(self.h.velocity) < 1 and abs(self.circle.velocity) < 1
                   and abs(self.w.target - self.w.value) < 0.5 and abs(self.h.target - self.h.value) < 0.5)
        resting = self.state == "idle" and settled and not self._hover and self.content_alpha >= 1
        interval = 100 if resting else 16
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        self.update()

    def _update_hover(self, now):
        pos = QPointF(self.mapFromGlobal(QCursor.pos()))
        pill = self._pill_rect()
        hit = pill.adjusted(-24, -6, 24, 14) if self.state == "idle" else pill
        over = hit.contains(pos) or (self.circle.value > 4 and self._circle_rect().adjusted(-6, -6, 6, 6).contains(pos))
        if over:
            self._hover_until = now + 0.9
        self._hover = over
        self._hover_row = -1
        if self.state == "choice" and over:
            for i in range(len(self.choices)):
                if self._row_rect(pill, i).contains(pos):
                    self._hover_row = i
        if over != self._interactive:
            self._interactive = over
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TRANSPARENT) if over else (style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def _update_bars(self, dt):
        n = len(self._bars)
        for i in range(n):
            mid = abs(i - (n - 1) / 2)
            if self.state == "listening":
                target = 0.1 + self._level_smooth * (0.6 + 0.4 * random.random()) * (1.35 - mid * 0.18)
            elif self.state == "speaking":
                t = self._phase * 7 + i * 1.3
                target = 0.12 + self._speech_smooth * (0.5 + 0.5 * abs(math.sin(t))) * (1.3 - mid * 0.14)
            else:
                target = 0.1
            self._bars[i] += (min(target, 1.0) - self._bars[i]) * min(1, dt * 16)

    # -------------------------------------------------------------- painting
    def _accent(self):
        if self.state == "error":
            return ERROR
        return GOLD if self.me_mode or self.state in ("thinking", "choice") else RED

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.theme == "ios":
            self._paint_ios(p)
            p.end()
            return
        pill = self._pill_rect()
        radius = self._radius(pill)

        # outer glow in armour red
        for i in range(7, 0, -1):
            g = QColor(RED if self.state != "idle" or pill.height() > 12 else RED_DEEP)
            g.setAlpha(int(7 * (8 - i) / 7) + 3)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(pill.adjusted(-i, -i + 1, i, i + 1), radius + i, radius + i)

        path = QPainterPath()
        path.addRoundedRect(pill, radius, radius)
        bg = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        bg.setColorAt(0, QColor(30, 10, 14, 248))
        bg.setColorAt(0.55, QColor(12, 5, 8, 250))
        bg.setColorAt(1, QColor(6, 3, 5, 252))
        p.fillPath(path, bg)

        # gold armour trim
        trim = QLinearGradient(pill.topLeft(), pill.topRight())
        a = 230 if time.monotonic() < self._flash_until else (170 if self._hover else 120)
        for stop, c in ((0, GOLD_DIM), (0.5, GOLD), (1, GOLD_DIM)):
            cc = QColor(c)
            cc.setAlpha(a)
            trim.setColorAt(stop, cc)
        p.setPen(QPen(trim, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        p.save()
        p.setClipPath(path)
        if pill.height() > 20:
            self._paint_scanlines(p, pill)
        if self.state in ("idle", "followup"):
            self._paint_idle(p, pill)
        else:
            if self.state == "choice":         # small reactor in the header, out of the way of the rows
                self._paint_reactor(p, QPointF(pill.left() + 24, pill.top() + 20), 8, small=True)
            else:
                self._paint_reactor(p, QPointF(pill.left() + 34, pill.top() + min(pill.height(), 70) / 2), 15)
            p.setOpacity(self.content_alpha)
            self._paint_header(p, pill)
            if self.state == "choice":
                self._paint_choices(p, pill)
            else:
                self._paint_content(p, pill)
            if self.state in ("listening", "speaking"):
                self._paint_bars(p, pill)
            self._paint_brackets(p, pill)
            p.setOpacity(1)
        p.restore()

        if self.circle.value > 3:
            self._paint_circle(p)
        p.end()

    def _paint_scanlines(self, p, pill):
        # faint horizontal HUD raster
        p.setPen(QPen(QColor(255, 180, 120, 7), 1))
        y = pill.top() + 2
        while y < pill.bottom():
            p.drawLine(QPointF(pill.left(), y), QPointF(pill.right(), y))
            y += 3
        # sweeping scan beam while busy
        if self.state in ("thinking", "action", "listening"):
            period = 1.6 if self.state == "thinking" else 2.4
            x = pill.left() + ((self._phase % period) / period) * (pill.width() + 120) - 60
            beam = QLinearGradient(QPointF(x - 60, 0), QPointF(x + 4, 0))
            beam.setColorAt(0, QColor(255, 190, 90, 0))
            beam.setColorAt(1, QColor(255, 200, 110, 38))
            p.fillRect(QRectF(x - 60, pill.top(), 64, pill.height()), beam)
            p.setPen(QPen(QColor(255, 215, 140, 90), 1))
            p.drawLine(QPointF(x + 4, pill.top()), QPointF(x + 4, pill.bottom()))

    def _paint_brackets(self, p, pill):
        """HUD corner brackets just inside the rounded ends."""
        c = QColor(GOLD)
        c.setAlpha(150)
        p.setPen(QPen(c, 1.4, Qt.SolidLine, Qt.FlatCap))
        r = self._radius(pill)
        inset, L = r * 0.55 + 4, 9
        x0, x1 = pill.left() + inset + 22, pill.right() - inset - 6
        y0, y1 = pill.top() + 5, pill.bottom() - 5
        for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * L * 0.6))

    def _paint_idle(self, p, pill):
        cy = pill.center().y()
        followup = self.state == "followup"
        breathe = 0.55 + 0.45 * math.sin(self._phase * (6.0 if followup else 2.0))
        if pill.height() < 16:               # sliver: a thin gold filament with a red-hot centre
            line = QLinearGradient(QPointF(pill.left(), 0), QPointF(pill.right(), 0))
            edge = QColor(GOLD_DIM)
            edge.setAlpha(0)
            mid = QColor(ERROR if self.muted else RED)
            mid.setAlphaF(0.55 + 0.4 * breathe)
            line.setColorAt(0, edge)
            line.setColorAt(0.35, QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 150))
            line.setColorAt(0.5, mid)
            line.setColorAt(0.65, QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 150))
            line.setColorAt(1, edge)
            p.setPen(Qt.NoPen)
            p.setBrush(line)
            p.drawRoundedRect(QRectF(pill.left() + 10, cy - 1.1, pill.width() - 20, 2.2), 1.1, 1.1)
            return
        self._paint_reactor(p, QPointF(pill.left() + 22, cy), 9, small=True)
        p.setOpacity(self.content_alpha)
        label = "LISTENING" if followup else "U.L.T.R.O.N"
        status = "MUTED" if self.muted else ("YOU" if self.me_mode else "ONLINE")
        if followup:
            status = "GO AHEAD"
        text_rect = QRectF(pill.left() + 40, pill.top(), pill.width() - 50, pill.height())
        p.setFont(self.f_header)
        p.setPen(GOLD)
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, label)
        x = text_rect.left() + QFontMetricsF(self.f_header).horizontalAdvance(label + "  ")
        p.setPen(QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 110))
        p.drawText(QRectF(x, pill.top(), 20, pill.height()), Qt.AlignVCenter | Qt.AlignLeft, "//")
        p.setPen(ERROR if self.muted else SUBTEXT)
        p.drawText(QRectF(x + 18, pill.top(), pill.width(), pill.height()), Qt.AlignVCenter | Qt.AlignLeft, status)
        p.setOpacity(1)

    def _paint_reactor(self, p, c, r, small=False):
        """Arc reactor: gold tick ring, red segment ring, triangular core, glowing cyan heart."""
        core_col = ERROR if self.state == "error" else (ARC_THINK if self.state == "thinking" else ARC)
        pulse = 1.0
        if self.state == "listening":
            pulse = 1 + self._level_smooth * 0.45
        elif self.state == "speaking":
            pulse = 1 + 0.22 * self._speech_smooth
        elif small:
            pulse = 1 + 0.06 * math.sin(self._phase * 2.2)

        glow = QRadialGradient(c, r * 2.2 * pulse)
        gc = QColor(core_col)
        gc.setAlphaF(0.55)
        glow.setColorAt(0, gc)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, r * 2.2 * pulse, r * 2.2 * pulse)

        # outer ring of ticks (rotating)
        p.save()
        p.translate(c)
        p.rotate(math.degrees(self._spin))
        ticks = 12 if small else 16
        tc = QColor(GOLD)
        tc.setAlpha(210)
        p.setPen(QPen(tc, 1.3 if small else 1.6, Qt.SolidLine, Qt.RoundCap))
        for i in range(ticks):
            ang = i * 2 * math.pi / ticks
            r0, r1 = r * 1.02, r * (1.2 if i % 4 == 0 else 1.12)
            p.drawLine(QPointF(math.cos(ang) * r0, math.sin(ang) * r0), QPointF(math.cos(ang) * r1, math.sin(ang) * r1))
        p.restore()

        # middle ring: three red armour segments (counter-rotating)
        p.save()
        p.translate(c)
        p.rotate(-math.degrees(self._spin * 1.6))
        seg = QPen(QColor(RED.red(), RED.green(), RED.blue(), 235), 2.4 if not small else 1.8, Qt.SolidLine, Qt.FlatCap)
        p.setPen(seg)
        p.setBrush(Qt.NoBrush)
        rr = r * 0.8 * pulse
        for i in range(3):
            p.drawArc(QRectF(-rr, -rr, rr * 2, rr * 2), int((i * 120 + 12) * 16), int(92 * 16))
        p.restore()

        # triangular core (Mark VI style)
        p.save()
        p.translate(c)
        p.rotate(math.degrees(self._spin * 0.4))
        tri = QPolygonF([QPointF(math.cos(a) * r * 0.5, math.sin(a) * r * 0.5)
                         for a in (-math.pi / 2, math.pi / 6, 5 * math.pi / 6)])
        p.setPen(QPen(QColor(core_col.red(), core_col.green(), core_col.blue(), 220), 1.3))
        p.setBrush(QColor(core_col.red(), core_col.green(), core_col.blue(), 60))
        p.drawPolygon(tri)
        p.restore()

        heart = QRadialGradient(c, r * 0.36 * pulse)
        heart.setColorAt(0, QColor(255, 255, 255))
        heart.setColorAt(0.6, core_col)
        heart.setColorAt(1, QColor(core_col.red(), core_col.green(), core_col.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(heart)
        p.drawEllipse(c, r * 0.36 * pulse, r * 0.36 * pulse)

    def _paint_header(self, p, pill):
        header = HEADERS.get(self.state, "")
        if self.state == "choice" and self.choice_title:
            header = self.choice_title.upper()
        p.setFont(self.f_header)
        c = QColor(ERROR if self.state == "error" else GOLD)
        p.setPen(c)
        rect = self._content_rect(pill)
        dots = "." * (int(self._phase * 3) % 4) if self.state in ("thinking", "action") else ""
        p.drawText(QRectF(rect.left(), pill.top() + 7, rect.width(), 12), Qt.AlignLeft | Qt.AlignVCenter,
                   f"U.L.T.R.O.N  //  {header}{dots}")
        # thin gold rule under the header
        fm = QFontMetricsF(self.f_header)
        x = rect.left() + fm.horizontalAdvance(f"U.L.T.R.O.N  //  {header}...") + 8
        line = QLinearGradient(QPointF(x, 0), QPointF(rect.right(), 0))
        line.setColorAt(0, QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 120))
        line.setColorAt(1, QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 0))
        p.setPen(QPen(line, 1))
        p.drawLine(QPointF(x, pill.top() + 13), QPointF(rect.right(), pill.top() + 13))

    def _paint_content(self, p, pill):
        rect = self._content_rect(pill)
        body_rect = QRectF(rect.left(), pill.top() + 22, rect.width(), pill.height() - 30)
        if self.state == "speaking":
            p.setFont(self.f_body)
            p.setPen(TEXT)
            p.drawText(body_rect, Qt.TextWordWrap | Qt.AlignVCenter | Qt.AlignLeft, self.body)
            return
        if self.state == "action" and self.icon:
            p.setFont(self.f_icon)
            p.setPen(GOLD)
            p.drawText(QRectF(pill.right() - 50, pill.top() + 14, 30, pill.height() - 20), Qt.AlignCenter, self.icon)
            body_rect.setRight(pill.right() - 56)
        fm_t = QFontMetricsF(self.f_title)
        title = self.title or ("Thinking" if self.state == "thinking" else "")
        p.setFont(self.f_title)
        p.setPen(TEXT if self.state != "error" else ERROR)
        if self.body:
            p.drawText(QRectF(body_rect.left(), body_rect.top(), body_rect.width(), fm_t.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, fm_t.elidedText(title, Qt.ElideRight, body_rect.width()))
            p.setFont(self.f_body)
            p.setPen(SUBTEXT)
            fm_b = QFontMetricsF(self.f_body)
            p.drawText(QRectF(body_rect.left(), body_rect.top() + fm_t.height(), body_rect.width(), fm_b.height() + 2),
                       Qt.AlignLeft | Qt.AlignVCenter, fm_b.elidedText(self.body, Qt.ElideRight, body_rect.width()))
        else:
            p.drawText(body_rect, Qt.AlignLeft | Qt.AlignVCenter, fm_t.elidedText(title, Qt.ElideRight, body_rect.width()))

    def _paint_choices(self, p, pill):
        for i, (label, sub, _value) in enumerate(self.choices):
            row = self._row_rect(pill, i)
            hovered = i == self._hover_row
            bg = QColor(RED.red(), RED.green(), RED.blue(), 70 if hovered else 22)
            p.setPen(QPen(QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 150 if hovered else 50), 1))
            p.setBrush(bg)
            p.drawRoundedRect(row, 7, 7)
            if hovered:                                   # gold active bar
                p.setPen(Qt.NoPen)
                p.setBrush(GOLD)
                p.drawRoundedRect(QRectF(row.left() + 2, row.top() + 7, 3, row.height() - 14), 1.5, 1.5)
            # index plate
            plate = QRectF(row.left() + 12, row.top() + 7, 30, row.height() - 14)
            p.setPen(QPen(GOLD, 1))
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawRoundedRect(plate, 4, 4)
            p.setFont(self.f_idx)
            p.setPen(GOLD)
            p.drawText(plate, Qt.AlignCenter, f"{i + 1:02d}")
            # initials medallion
            med = QPointF(plate.right() + 22, row.center().y())
            p.setPen(QPen(QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 160), 1))
            p.setBrush(QColor(RED_DEEP.red(), RED_DEEP.green(), RED_DEEP.blue(), 220))
            p.drawEllipse(med, 12, 12)
            initials = "".join(w[0] for w in label.split()[:2]).upper() or "?"
            p.setFont(self.f_idx)
            p.setPen(TEXT)
            p.drawText(QRectF(med.x() - 12, med.y() - 12, 24, 24), Qt.AlignCenter, initials)
            # name + sublabel
            tx = med.x() + 22
            p.setFont(self.f_row)
            p.setPen(TEXT)
            if sub:
                fm_n = QFontMetricsF(self.f_row)
                fm_s = QFontMetricsF(self.f_row_sub)
                top = row.center().y() - (fm_n.height() + fm_s.height() - 2) / 2
                p.drawText(QRectF(tx, top, row.right() - tx - 130, fm_n.height()), Qt.AlignLeft | Qt.AlignVCenter, label)
                p.setFont(self.f_row_sub)
                p.setPen(SUBTEXT)
                p.drawText(QRectF(tx, top + fm_n.height() - 2, row.right() - tx - 130, fm_s.height()),
                           Qt.AlignLeft | Qt.AlignVCenter, sub.upper())
            else:
                p.drawText(QRectF(tx, row.top(), row.right() - tx - 10, row.height()), Qt.AlignLeft | Qt.AlignVCenter, label)
            # say-hint on the right
            p.setFont(self.f_row_sub)
            p.setPen(QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 170 if hovered else 90))
            p.drawText(QRectF(row.right() - 120, row.top(), 108, row.height()), Qt.AlignRight | Qt.AlignVCenter,
                       "CLICK" if hovered else f'SAY "{label.split()[0].upper()}"')

    def _paint_bars(self, p, pill):
        n = len(self._bars)
        bw, gap = 3.2, 3.4
        x0 = pill.right() - 30 - (n * bw + (n - 1) * gap)
        cy = pill.top() + 22 + (pill.height() - 30) / 2
        grad = QLinearGradient(QPointF(0, cy - 14), QPointF(0, cy + 14))
        grad.setColorAt(0, GOLD)
        grad.setColorAt(0.5, QColor(255, 120, 60) if not self.me_mode else GOLD)
        grad.setColorAt(1, RED)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        for i, v in enumerate(self._bars):
            h = max(3, v * 28)
            p.drawRoundedRect(QRectF(x0 + i * (bw + gap), cy - h / 2, bw, h), 1.2, 1.2)
        # baseline ticks
        p.setPen(QPen(QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 60), 1))
        p.drawLine(QPointF(x0 - 4, cy + 16), QPointF(x0 + n * (bw + gap), cy + 16))

    def _paint_circle(self, p):
        rect = self._circle_rect()
        d = rect.width()
        c = rect.center()
        speaking = self.state == "speaking" and self._speech_smooth > 0.02
        if self.me_mode:
            glow = QRadialGradient(c, d * (0.62 + 0.12 * self._speech_smooth))
            gc = QColor(RED if speaking else GOLD)
            gc.setAlphaF(0.5 if speaking else 0.25)
            glow.setColorAt(0.75, gc)
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, d * 0.68, d * 0.68)
        clip = QPainterPath()
        clip.addEllipse(rect)
        p.save()
        p.setClipPath(clip)
        p.fillPath(clip, QColor(10, 4, 6))
        if self.avatar.ok:
            p.setOpacity(1.0 if self.me_mode else 0.5)
            zoom = rect.adjusted(-d * 0.08, -d * 0.06, d * 0.08, d * 0.10)    # hide crop edges
            self.avatar.paint(p, zoom, self._dt)
        else:
            p.setFont(QFont("Segoe Fluent Icons", max(8, int(d * 0.36))))
            p.setPen(GOLD)
            p.drawText(rect, Qt.AlignCenter, "")
        p.restore()
        # gold bezel + rotating HUD ticks
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(GOLD if self.me_mode else GOLD_DIM, 2 if self.me_mode else 1.2))
        p.drawEllipse(rect.adjusted(1, 1, -1, -1))
        if d > 30:
            p.save()
            p.translate(c)
            p.rotate(math.degrees(self._spin * 0.7))
            p.setPen(QPen(QColor(RED.red(), RED.green(), RED.blue(), 200), 2, Qt.SolidLine, Qt.FlatCap))
            rr = d / 2 + 3
            for i in range(4):
                p.drawArc(QRectF(-rr, -rr, rr * 2, rr * 2), int((i * 90 + 10) * 16), int(40 * 16))
            p.restore()

    # ------------------------------------------------------------ iOS theme
    # Apple-style Dynamic Island: pure black pill, a living Siri orb, clean white type.
    # Iron Man accents on iOS: hot-rod red, gold, arc-reactor cyan, ember orange.
    SIRI = [QColor(224, 38, 47), QColor(244, 186, 66), QColor(110, 220, 255), QColor(255, 122, 47)]
    IOS_BLUE = QColor(214, 40, 48)               # accent (icons, highlights) - armour red

    @property
    def IOS_GRAY(self):
        return QColor(110, 110, 115) if self.light else QColor(152, 152, 159)

    @property
    def light(self):
        return self.appearance == "light"

    def _fg(self):
        return QColor(28, 28, 30) if self.light else QColor(255, 255, 255)

    def set_appearance(self, appearance):
        self.appearance = "dark" if appearance == "dark" else "light"
        self.flash()

    def _paint_ios(self, p):
        pill = self._pill_rect()
        radius = min(pill.height() / 2, 20 if self.state == "choice" else 32)
        for i in range(6, 0, -1):                       # soft drop shadow
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 18 if self.light else 14))
            p.drawRoundedRect(pill.adjusted(-i, -i + 3, i, i + 3), radius + i, radius + i)
        path = QPainterPath()
        path.addRoundedRect(pill, radius, radius)
        if self.light:                                  # frosted white glass with a hint of gold at the rim
            bg = QLinearGradient(pill.topLeft(), pill.bottomLeft())
            bg.setColorAt(0, QColor(255, 255, 255, 250))
            bg.setColorAt(1, QColor(244, 244, 248, 250))
            p.fillPath(path, bg)
            rim = QColor(244, 186, 66, 150 if time.monotonic() < self._flash_until else 70)
            p.setPen(QPen(rim, 1))
            p.drawPath(path)
        else:
            p.fillPath(path, QColor(0, 0, 0))
            p.setPen(QPen(QColor(244, 186, 66, 120 if time.monotonic() < self._flash_until else 38), 1))
            p.drawPath(path)

        p.save()
        p.setClipPath(path)
        if self.state in ("idle", "followup"):
            self._ios_idle(p, pill)
        elif self.state == "choice":
            p.setOpacity(self.content_alpha)
            self._ios_choices(p, pill)
        else:
            cy = pill.top() + min(pill.height(), 64) / 2
            self._ios_orb(p, QPointF(pill.left() + 32, cy), 15)
            p.setOpacity(self.content_alpha)
            self._ios_content(p, pill)
            if self.state in ("listening", "speaking"):
                self._ios_bars(p, pill)
        p.setOpacity(1)
        p.restore()
        if self.circle.value > 3:
            self._ios_circle(p)

    def _ios_orb(self, p, c, r):
        """Siri-style orb: coloured light blobs drifting around inside a sphere, swelling with the voice."""
        lvl = self._level_smooth if self.state == "listening" else self._speech_smooth
        if self.state == "thinking":
            lvl = 0.35 + 0.15 * math.sin(self._phase * 6)
        if self.state == "error":
            colors = [QColor(255, 69, 58), QColor(255, 120, 90), QColor(200, 40, 40), QColor(255, 159, 10)]
        else:
            colors = self.SIRI
        rr = r * (1 + lvl * 0.35)
        sphere = QPainterPath()
        sphere.addEllipse(c, rr, rr)
        p.save()
        p.setClipPath(sphere, Qt.IntersectClip)
        p.fillPath(sphere, QColor(30, 6, 10))           # deep armour red-black inside
        speed = 2.6 if self.state == "thinking" else 1.2
        for i, col in enumerate(colors):
            a = self._phase * speed * (1 + i * 0.27) + i * 1.7
            off = QPointF(math.cos(a) * rr * 0.45, math.sin(a * 1.3) * rr * 0.45)
            g = QRadialGradient(c + off, rr * (0.95 + 0.15 * math.sin(a * 0.7)))
            cc = QColor(col)
            cc.setAlpha(230)
            g.setColorAt(0, cc)
            cc.setAlpha(0)
            g.setColorAt(1, cc)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c + off, rr * 1.1, rr * 1.1)
        core = QRadialGradient(c, rr * 0.38)            # arc-reactor heart
        core.setColorAt(0, QColor(255, 255, 255, 230))
        core.setColorAt(0.5, QColor(140, 230, 255, 140))
        core.setColorAt(1, QColor(140, 230, 255, 0))
        p.setBrush(core)
        p.drawEllipse(c, rr * 0.38, rr * 0.38)
        shine = QRadialGradient(c + QPointF(-rr * 0.35, -rr * 0.45), rr * 0.7)
        shine.setColorAt(0, QColor(255, 255, 255, 110))
        shine.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(shine)
        p.drawEllipse(c, rr, rr)
        p.restore()
        p.setPen(QPen(QColor(244, 186, 66, 170), 1))      # thin gold bezel
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, rr, rr)

    def _ios_idle(self, p, pill):
        cy = pill.center().y()
        if pill.height() < 16:                          # sliver: a tiny colourful glint
            g = QLinearGradient(QPointF(pill.center().x() - 16, 0), QPointF(pill.center().x() + 16, 0))
            for i, col in enumerate(self.SIRI):
                cc = QColor(col)
                cc.setAlpha(200 if not self.muted else 60)
                g.setColorAt(i / 3, cc)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(QRectF(pill.center().x() - 16, cy - 1.1, 32, 2.2), 1.1, 1.1)
            return
        self._ios_orb(p, QPointF(pill.left() + 20, cy), 9)
        p.setOpacity(self.content_alpha)
        f = QFont("Segoe UI Variable Display Semibold", 10)
        p.setFont(f)
        p.setPen(self._fg())
        label = "Listening…" if self.state == "followup" else "Ultron"
        p.drawText(QRectF(pill.left() + 36, pill.top(), pill.width() - 40, pill.height()), Qt.AlignVCenter | Qt.AlignLeft, label)
        status = "Muted" if self.muted else ("Your voice" if self.me_mode else "Ready")
        if self.state == "followup":
            status = "Active · say deactivate" if self.title == "Active" else "Go ahead"
        p.setFont(QFont("Segoe UI Variable Text", 9))
        p.setPen(QColor(255, 69, 58) if self.muted else self.IOS_GRAY)
        p.drawText(QRectF(pill.left(), pill.top(), pill.width() - 14, pill.height()), Qt.AlignVCenter | Qt.AlignRight, status)
        p.setOpacity(1)

    def _ios_content(self, p, pill):
        left = pill.left() + 60
        right = pill.right() - (70 if self.state in ("listening", "speaking") else 20)
        rect = QRectF(left, pill.top() + 10, right - left, pill.height() - 20)
        if self.state == "speaking":
            p.setFont(self.f_body)
            p.setPen(self._fg())
            p.drawText(rect, Qt.TextWordWrap | Qt.AlignVCenter | Qt.AlignLeft, self.body)
            return
        if self.state == "action" and self.icon:
            ic = QRectF(pill.right() - 48, pill.top() + (min(pill.height(), 64) - 32) / 2, 32, 32)
            p.setPen(Qt.NoPen)
            p.setBrush((QColor(242, 242, 247) if self.light else QColor(44, 44, 46)))
            p.drawEllipse(ic)
            p.setFont(QFont("Segoe Fluent Icons", 12))
            p.setPen(self.IOS_BLUE)
            p.drawText(ic, Qt.AlignCenter, self.icon)
            rect.setRight(ic.left() - 8)
        title = self.title or ("Thinking…" if self.state == "thinking" else "")
        f_t = QFont("Segoe UI Variable Display Semibold", 11)
        fm_t = QFontMetricsF(f_t)
        p.setFont(f_t)
        p.setPen(QColor(255, 69, 58) if self.state == "error" else self._fg())
        if self.body:
            p.drawText(QRectF(rect.left(), rect.top() + 2, rect.width(), fm_t.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       fm_t.elidedText(title, Qt.ElideRight, rect.width()))
            p.setFont(self.f_body)
            p.setPen(self.IOS_GRAY)
            fm_b = QFontMetricsF(self.f_body)
            p.drawText(QRectF(rect.left(), rect.top() + fm_t.height() + 2, rect.width(), fm_b.height() + 2),
                       Qt.AlignLeft | Qt.AlignVCenter, fm_b.elidedText(self.body, Qt.ElideRight, rect.width()))
        else:
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, fm_t.elidedText(title, Qt.ElideRight, rect.width()))

    def _ios_bars(self, p, pill):
        n = len(self._bars)
        bw, gap = 3.4, 3.6
        x0 = pill.right() - 26 - (n * bw + (n - 1) * gap)
        cy = pill.center().y() if self.state == "speaking" else pill.top() + min(pill.height(), 64) / 2
        g = QLinearGradient(QPointF(x0, 0), QPointF(x0 + n * (bw + gap), 0))
        for i, col in enumerate(self.SIRI):
            g.setColorAt(i / 3, col)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        for i, v in enumerate(self._bars):
            h = max(3.4, v * 26)
            p.drawRoundedRect(QRectF(x0 + i * (bw + gap), cy - h / 2, bw, h), bw / 2, bw / 2)

    def _ios_choices(self, p, pill):
        p.setFont(QFont("Segoe UI Variable Display Semibold", 11))
        p.setPen(self._fg())
        p.drawText(QRectF(pill.left() + 20, pill.top() + 10, pill.width() - 40, 26), Qt.AlignVCenter | Qt.AlignLeft,
                   self.choice_title or "Choose")
        group = QRectF(pill.left() + 12, pill.top() + 42, pill.width() - 24, ROW_H * len(self.choices) - 4)
        p.setPen(Qt.NoPen)
        p.setBrush((QColor(242, 242, 247) if self.light else QColor(28, 28, 30)))
        p.drawRoundedRect(group, 14, 14)
        for i, (label, sub, _v) in enumerate(self.choices):
            row = self._row_rect(pill, i)
            if i == self._hover_row:
                hp = QPainterPath()
                hp.addRoundedRect(row.adjusted(-4, -2, 4, 4), 12, 12)
                p.fillPath(hp, (QColor(229, 229, 234) if self.light else QColor(58, 58, 60)))
            med = QPointF(row.left() + 22, row.center().y())
            g = QLinearGradient(med - QPointF(14, 14), med + QPointF(14, 14))
            g.setColorAt(0, self.SIRI[(i * 2) % 4])
            g.setColorAt(1, self.SIRI[(i * 2 + 1) % 4])
            p.setBrush(g)
            p.drawEllipse(med, 15, 15)
            p.setFont(QFont("Segoe UI Variable Display Semibold", 9))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(med.x() - 15, med.y() - 15, 30, 30), Qt.AlignCenter,
                       "".join(w[0] for w in label.split()[:2]).upper())
            tx = med.x() + 26
            p.setFont(QFont("Segoe UI Variable Text Semibold", 11))
            p.setPen(self._fg())
            if sub:
                p.drawText(QRectF(tx, row.top() + 2, row.right() - tx - 40, row.height() / 2), Qt.AlignLeft | Qt.AlignBottom, label)
                p.setFont(QFont("Segoe UI Variable Text", 9))
                p.setPen(self.IOS_GRAY)
                p.drawText(QRectF(tx, row.center().y() + 2, row.right() - tx - 40, row.height() / 2 - 2),
                           Qt.AlignLeft | Qt.AlignTop, sub)
            else:
                p.drawText(QRectF(tx, row.top(), row.right() - tx - 40, row.height()), Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setFont(QFont("Segoe Fluent Icons", 10))
            p.setPen(self.IOS_GRAY)
            p.drawText(QRectF(row.right() - 30, row.top(), 20, row.height()), Qt.AlignCenter, "")   # chevron
            if i < len(self.choices) - 1:
                p.setPen(QPen((QColor(209, 209, 214) if self.light else QColor(56, 56, 58)), 1))
                p.drawLine(QPointF(tx, row.bottom() + 2), QPointF(row.right(), row.bottom() + 2))

    def _ios_circle(self, p):
        rect = self._circle_rect()
        d = rect.width()
        clip = QPainterPath()
        clip.addEllipse(rect)
        p.save()
        p.setClipPath(clip)
        p.fillPath(clip, QColor(0, 0, 0))
        if self.avatar.ok:
            p.setOpacity(1.0 if self.me_mode else 0.6)
            self.avatar.paint(p, rect.adjusted(-d * 0.08, -d * 0.06, d * 0.08, d * 0.10), self._dt)
        p.restore()
        if self.me_mode and d > 20:                     # rotating Siri-colour ring
            g = QLinearGradient(rect.topLeft(), rect.bottomRight())
            a = self._phase * 0.8
            for i, col in enumerate(self.SIRI):
                g.setColorAt((i / 4 + a) % 1.0, col)
            p.setPen(QPen(g, 2.5))
        else:
            p.setPen(QPen(QColor(244, 186, 66, 140), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect.adjusted(1, 1, -1, -1))

    # ----------------------------------------------------------------- input
    def mousePressEvent(self, e):
        pos = e.position()
        on_circle = self.circle.value > 4 and self._circle_rect().contains(pos)
        pill = self._pill_rect()
        on_pill = (pill.adjusted(-24, -6, 24, 14) if self.state == "idle" else pill).contains(pos)
        if not (on_circle or on_pill):
            e.ignore()
            return
        if e.button() == Qt.LeftButton:
            self.flash()
            if self.state == "choice" and on_pill:
                for i, (_l, _s, value) in enumerate(self.choices):
                    if self._row_rect(pill, i).contains(pos):
                        self.choice_picked.emit(value)
                        return
            (self.avatar_clicked if on_circle else self.clicked).emit()
        elif e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())

    def _menu(self, pos):
        m = QMenu()
        m.setStyleSheet(
            "QMenu{background:#120609;color:#f4eee6;border:1px solid #8a6424;border-radius:10px;padding:6px;"
            "font:10pt 'Bahnschrift'}"
            "QMenu::item{padding:6px 18px;border-radius:6px}QMenu::item:selected{background:#5a0c12;color:#f4ba42}"
            "QMenu::separator{height:1px;background:#5a4220;margin:4px 8px}")
        items = [
            ("Talk to Ultron", self.clicked.emit),
            ("Use Ultron voice" if self.me_mode else "Use my voice and face", self.avatar_clicked.emit),
            ("Unmute voice" if self.muted else "Mute voice", self._toggle_mute),
            ("New conversation", self.new_chat.emit),
        ]
        for text, fn in items:
            a = QAction(text, m)
            a.triggered.connect(fn)
            m.addAction(a)
        m.addSeparator()
        q = QAction("Quit Ultron", m)
        q.triggered.connect(self.quit_requested.emit)
        m.addAction(q)
        m.exec(pos)

    def _toggle_mute(self):
        self.muted = not self.muted
        self.mute_toggled.emit(self.muted)
