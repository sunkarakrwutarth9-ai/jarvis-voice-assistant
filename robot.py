"""A little Iron-Man-styled robot that dances whenever the PC plays music.

It listens to the speaker output (WASAPI loopback, see systemaudio.py), so it reacts to any music:
YouTube, Spotify, a local file. It finds the beat in the bass band and changes dance moves every
8 beats. The window is tiny, click-through and never takes focus, so it can't get in the way. It also
hides itself during full-screen video, games and presentations, and while Jarvis itself is speaking.
"""

import collections
import ctypes
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

W, H = 230, 250          # drawing units
SCALE = 0.68             # on-screen size: small enough to stay out of the way
GOLD, RED, ARC = QColor(244, 186, 66), QColor(224, 38, 47), QColor(127, 230, 255)


def _fullscreen_app():
    """True while a full-screen app (video, game, slideshow) is in front."""
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except Exception:
        return False
    return state.value in (2, 3, 4)          # busy (full screen), D3D full screen, presentation mode


class DancingRobot(QWidget):
    def __init__(self, system_audio, jarvis_speaking=lambda: False, enabled=True, side="right"):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(int(W * SCALE), int(H * SCALE))
        self.audio = system_audio
        self.jarvis_speaking = jarvis_speaking
        self.enabled = enabled
        self.side = side
        self.loud = collections.deque(maxlen=40)       # last 4 s of "is something playing" (100 ms steps)
        self.beats = collections.deque(maxlen=12)      # recent beat times
        self.bass_avg = 0.0
        self.last_beat = 0.0
        self.n_beats = 0
        self.period = 0.5
        self.alpha = 0.0
        self.showing = False
        self.demo_until = 0.0
        self.t0 = time.monotonic()
        self.poll = QTimer(self, interval=100, timeout=self._poll)      # cheap check: is music playing?
        self.poll.start()
        self.anim = QTimer(self, interval=33, timeout=self._tick)       # ~30 fps, only while visible

    # ------------------------------------------------------------------ control
    def set_enabled(self, on: bool):
        self.enabled = on
        if not on:
            self.demo_until = 0.0

    def set_side(self, side: str):
        self.side = "left" if side == "left" else "right"
        self._place()

    def dance(self, seconds=10.0):
        """Dance now, even without music (a built-in 120 bpm beat)."""
        self.enabled = True
        self.demo_until = time.monotonic() + seconds

    def _place(self):
        geo = QApplication.primaryScreen().availableGeometry()
        x = geo.left() + 16 if self.side == "left" else geo.right() - int(W * SCALE) - 16
        self.move(x, geo.bottom() - int(H * SCALE) - 6)

    # ------------------------------------------------------------------ music detection
    def _poll(self):
        now = time.monotonic()
        level = self.audio.level() if self.audio else 0.0
        speaking = self.jarvis_speaking()
        self.loud.append(level > 150 and not speaking)
        recent = list(self.loud)[-25:]
        music = len(recent) >= 25 and sum(recent) >= 21               # ~2.5 s of steady sound
        quiet = sum(self.loud) <= len(self.loud) * 0.3               # mostly silent for 4 s
        demo = now < self.demo_until
        want = self.enabled and (demo or (self.showing and not quiet) or music) and not _fullscreen_app()
        if want and not self.showing:
            self.showing = True
            self._place()
            self.show()
            self.anim.start()
        elif not want and self.showing:
            self.showing = False                  # fade out in _tick, then hide

    def _beat_detect(self, now):
        if now < self.demo_until and (not self.audio or self.audio.level() < 150):
            if now - self.last_beat >= 0.5:      # demo: 120 bpm
                self._on_beat(now)
            return
        b = self.audio.bass() if self.audio else 0.0
        self.bass_avg = self.bass_avg * 0.92 + b * 0.08
        if b > self.bass_avg * 1.35 and b > 8 and now - self.last_beat > 0.28:
            self._on_beat(now)
        elif now - self.last_beat > self.period * 1.6 and now - self.last_beat > 0.4:
            self._on_beat(now)                    # keep dancing through quiet bars

    def _on_beat(self, now):
        if self.last_beat and 0.28 < now - self.last_beat < 1.2:
            self.beats.append(now - self.last_beat)
            gaps = sorted(self.beats)
            self.period = gaps[len(gaps) // 2]
        self.last_beat = now
        self.n_beats += 1

    def _tick(self):
        now = time.monotonic()
        self._beat_detect(now)
        target = 1.0 if self.showing else 0.0
        self.alpha += (target - self.alpha) * 0.15
        if not self.showing and self.alpha < 0.03:
            self.alpha = 0.0
            self.anim.stop()
            self.hide()
            return
        self.update()

    # ------------------------------------------------------------------ the dance
    def _pose(self, now):
        p = min(1.0, (now - self.last_beat) / max(self.period, 0.25))   # 0 at the beat -> 1 at the next
        n = self.n_beats
        th = (n + p) * math.pi                                         # half a sway per beat
        hit = (1 - p) ** 3                                            # punch right on the beat
        s = math.sin(th)
        pose = dict(bob=abs(math.sin(th)) * 10 + hit * 4, shift=0.0, lean=0.0, head=0.0,
                    lA=20.0, lE=10.0, rA=20.0, rE=10.0, stepL=0.0, stepR=0.0, glow=hit)
        move = (n // 8) % 5
        if move == 0:      # bounce: pumping arms
            pose.update(lA=45 + 35 * s, rA=45 - 35 * s, lE=70, rE=70)
        elif move == 1:    # hands in the air, swaying
            pose.update(lA=160 + 15 * s, rA=160 - 15 * s, lE=-10, rE=-10, lean=8 * s, shift=10 * s)
        elif move == 2:    # the robot: sharp poses that snap on every beat
            up = n % 2 == 0
            pose.update(lA=90, rA=90, lE=90 if up else -90, rE=-90 if up else 90, head=14 if up else -14,
                        bob=hit * 6)
        elif move == 3:    # side steps
            pose.update(shift=16 * s, lean=-6 * s, lA=30 - 25 * s, rA=30 + 25 * s, lE=40, rE=40,
                        stepL=10 * s, stepR=10 * s)
        else:              # disco point
            up = (n // 2) % 2 == 0
            pose.update(rA=150 if up else 40, rE=0, lA=60, lE=110, lean=-8 if up else 6, head=-10 if up else 8)
        return pose

    # ------------------------------------------------------------------ drawing
    def paintEvent(self, _):
        now = time.monotonic()
        q = QPainter(self)
        q.setRenderHint(QPainter.Antialiasing)
        q.setOpacity(self.alpha * 0.92)
        q.scale(SCALE, SCALE)
        P = self._pose(now)
        cx, ground = W / 2, H - 12

        # floor glow
        g = QRadialGradient(cx, ground, 70)
        g.setColorAt(0, QColor(127, 230, 255, int(70 + 90 * P["glow"])))
        g.setColorAt(1, QColor(127, 230, 255, 0))
        q.setBrush(g)
        q.setPen(Qt.NoPen)
        q.drawEllipse(QRectF(cx - 70, ground - 12, 140, 24))

        hip_y = ground - 66 + P["bob"]
        hx = cx + P["shift"]
        metal = QLinearGradient(0, 0, 0, H)
        metal.setColorAt(0, QColor(70, 78, 96))
        metal.setColorAt(1, QColor(24, 28, 38))

        # legs (hip -> knee -> foot; the knee folds outward as the robot bobs)
        for side, step in ((-1, P["stepL"]), (1, P["stepR"])):
            hip = QPointF(hx + side * 14, hip_y)
            foot = QPointF(cx + side * 18 + step, ground - 6)
            self._leg(q, hip, foot, side, metal)

        # torso
        q.save()
        q.translate(hx, hip_y)
        q.rotate(P["lean"])
        body = QRectF(-32, -74, 64, 74)
        q.setBrush(QBrush(metal))
        q.setPen(QPen(GOLD, 2))
        q.drawRoundedRect(body, 16, 16)
        q.setPen(QPen(RED, 3))
        q.drawLine(QPointF(-20, -10), QPointF(20, -10))
        # chest reactor pulses on the beat
        r = 9 + 3 * P["glow"]
        rg = QRadialGradient(0, -46, r * 2.2)
        rg.setColorAt(0, QColor(255, 255, 255))
        rg.setColorAt(0.35, ARC)
        rg.setColorAt(1, QColor(127, 230, 255, 0))
        q.setPen(Qt.NoPen)
        q.setBrush(rg)
        q.drawEllipse(QPointF(0, -46), r * 2.2, r * 2.2)

        # arms
        for side, a, e in ((-1, P["lA"], P["lE"]), (1, P["rA"], P["rE"])):
            self._arm(q, QPointF(side * 34, -66), side, a, e, metal)

        # head
        q.save()
        q.translate(0, -80)
        q.rotate(P["head"])
        q.setPen(QPen(GOLD, 2))
        q.setBrush(QBrush(metal))
        q.drawRect(QRectF(-6, -2, 12, 8))                        # neck
        q.drawRoundedRect(QRectF(-29, -44, 58, 42), 14, 14)
        q.setPen(QPen(GOLD, 2))
        q.drawLine(QPointF(0, -44), QPointF(0, -58))              # antenna
        q.setPen(Qt.NoPen)
        q.setBrush(RED if P["glow"] > 0.4 else GOLD)
        q.drawEllipse(QPointF(0, -61), 4 + 2 * P["glow"], 4 + 2 * P["glow"])
        blink = (now - self.t0) % 4.0 < 0.12
        for ex in (-12, 12):
            eg = QRadialGradient(ex, -24, 10)
            eg.setColorAt(0, QColor(255, 255, 255))
            eg.setColorAt(0.4, ARC)
            eg.setColorAt(1, QColor(127, 230, 255, 0))
            q.setBrush(eg)
            q.drawEllipse(QPointF(ex, -24), 9, 1.5 if blink else 7)
        q.setPen(QPen(ARC, 2))                                     # smile
        q.drawArc(QRectF(-9, -18, 18, 10), 200 * 16, 140 * 16)
        q.restore()
        q.restore()
        q.end()

    def _leg(self, q, hip, foot, side, metal):
        L = 34.0
        dx, dy = foot.x() - hip.x(), foot.y() - hip.y()
        d = min(math.hypot(dx, dy), 2 * L - 0.1)
        off = math.sqrt(max(0.0, L * L - (d / 2) ** 2))
        mx, my = hip.x() + dx / 2, hip.y() + dy / 2
        nx, ny = -dy / max(d, 1), dx / max(d, 1)
        knee = QPointF(mx + nx * off * -side, my + ny * off * -side)
        q.setPen(QPen(QColor(40, 46, 60), 13, Qt.SolidLine, Qt.RoundCap))
        q.drawLine(hip, knee)
        q.drawLine(knee, foot)
        q.setPen(QPen(GOLD, 2))
        q.setBrush(QBrush(metal))
        q.drawEllipse(knee, 6, 6)
        q.drawRoundedRect(QRectF(foot.x() - 12, foot.y() - 4, 24, 10), 5, 5)

    def _arm(self, q, shoulder, side, angle, elbow, metal):
        q.save()
        q.translate(shoulder)
        q.rotate(-side * angle)                 # angle 0 = hanging down, + = raised outward
        q.setPen(QPen(QColor(40, 46, 60), 11, Qt.SolidLine, Qt.RoundCap))
        q.drawLine(QPointF(0, 0), QPointF(0, 28))
        q.setPen(QPen(GOLD, 2))
        q.setBrush(QBrush(metal))
        q.drawEllipse(QPointF(0, 0), 8, 8)
        q.translate(0, 28)
        q.rotate(-side * elbow)
        q.setPen(QPen(QColor(40, 46, 60), 10, Qt.SolidLine, Qt.RoundCap))
        q.drawLine(QPointF(0, 0), QPointF(0, 24))
        q.setPen(QPen(GOLD, 2))
        q.setBrush(RED)
        q.drawEllipse(QPointF(0, 28), 7, 7)
        q.restore()
