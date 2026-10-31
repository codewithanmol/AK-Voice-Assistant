"""PyQt5 HUD for AK's holographic layer.

Two windows:
  * ``HoloOverlay`` - a full screen, click-through holographic layer that
    draws the 3D structure, the aiming reticle and the telemetry chrome.
  * ``IntelPanel``  - a small styled popup with the live camera feed, the
    wardrobe / expression / held-object annotations (with leader lines that
    point at the exact region) and the spoken narration line.

Both are frameless, always-on-top and animated - the panel slides + fades
in, the leader lines draw themselves, the reticle pulses on a pinch.
"""

from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np
import cv2

from PyQt5.QtCore import (QPoint, QPointF, QRect, QRectF, Qt, QTimer,
                          QVariantAnimation, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QFont, QFontMetrics, QGuiApplication,
                         QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient, QTextDocument)
from PyQt5.QtWidgets import QApplication, QWidget

# ── Theme (AK house style: charcoal glass, steel cyan, maroon accent) ──────
INK = QColor(11, 12, 17)          # deep charcoal, never pure black
INK_SOFT = QColor(20, 21, 28)
GLASS = QColor(18, 19, 26, 228)
CYAN = QColor(120, 226, 255)
CYAN_DIM = QColor(120, 226, 255, 90)
AMBER = QColor(255, 138, 96)
AMBER_DIM = QColor(255, 138, 96, 110)
WHITE = QColor(238, 242, 248)
MUTED = QColor(158, 168, 186)
GREEN = QColor(120, 230, 170)
RED = QColor(255, 110, 110)

FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"


def _qcolor(c, alpha=None):
    q = QColor(c)
    if alpha is not None:
        q.setAlpha(alpha)
    return q


def numpy_to_qimage(arr: np.ndarray, mirror: bool = False) -> QImage:
    """BGR uint8 numpy -> QImage (copy keeps the buffer alive)."""
    if arr is None or arr.size == 0:
        return QImage()
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if mirror:
        arr = arr[:, ::-1]
    rgb = np.ascontiguousarray(arr[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _lift_frame(arr):
    """Make the camera preview readable in any light.

    A dim room gives a nearly black preview, a bright window blows it out to
    a flat white blur. Dark frames get a gamma lift (a linear gain would
    only amplify the noise and destroy the detail), bright frames get
    pulled down and the highlights are rolled off.
    """
    if arr is None or arr.size == 0:
        return arr
    try:
        import cv2
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        med = float(np.median(gray))
        hi = float(np.percentile(gray, 97))
        if 45.0 <= med <= 150.0 and hi <= 236.0:
            return arr                       # already well exposed
        if med < 45.0:
            # dark scene - lift the midtones with a gamma curve
            gamma = 0.45
            if med > 4.0:
                gamma = max(0.45, min(1.0, math.log(0.42) / math.log(med / 255.0)))
            lut = np.clip(((np.arange(256) / 255.0) ** gamma) * 255.0,
                          0, 255).astype(np.uint8)
            out = cv2.LUT(arr, lut)
        else:
            gain = max(0.5, min(1.0, 212.0 / max(60.0, hi)))
            out = cv2.convertScaleAbs(arr, alpha=gain, beta=0)
        # highlight roll-off: only the very top end is compressed
        out = out.astype(np.int16)
        rolled = np.where(out > 205, 205 + (out - 205) * 12 // 50, out)
        return rolled.astype(np.uint8)
    except Exception:
        return arr


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


# ── Full-screen holographic overlay ────────────────────────────────────────
class HoloOverlay(QWidget):
    """Click-through holographic layer: 3D scene + reticle + telemetry."""

    def __init__(self, scene=None, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)

        self.cursor_pos = (0.5, 0.5)
        self.cursor_px = (0, 0)
        self.cursor_speed = 0.0
        self.gesture = "none"
        self.pinch = 0.0
        self.pinching = False
        self.hands = 0
        self.status = "BOOTING"
        self.telemetry = []
        self.hint = "Point to aim  ·  Pinch to click  ·  Fist to zoom  ·  Swipe to switch"
        self.command_wheel = None          # (items, selected_index)
        self.reticle_flash = 0.0
        self.show_scene = True
        # virtual window bar - the touch surface for PC control
        self.window_title = ""
        self.hover_button = ""            # id of the button under the cursor
        self.touch_mode = False
        self.last_touch = ""
        self.touch_ripple = None          # (x, y, age)

    # -- window bar layout ---------------------------------------------------
    def window_bar_rect(self, W: int, H: int):
        bw, bh = 236, 36
        return QRectF(W - bw - 30, 26, bw, bh)

    def power_button_rect(self, W: int, H: int):
        """The AK master switch - turns the whole gesture stack off."""
        bar = self.window_bar_rect(W, H)
        return QRectF(bar.left() - 44, bar.top(), 36, bar.height())

    def window_button_rects(self) -> list:
        """[(id, QRectF), ...] for the virtual window buttons."""
        W, H = self.width(), self.height()
        bar = self.window_bar_rect(W, H)
        title_w = 104
        ids = ["min", "max", "desk", "close"]
        bw = (bar.width() - title_w) / len(ids)
        out = []
        for i, bid in enumerate(ids):
            out.append((bid, QRectF(bar.left() + title_w + i * bw, bar.top(),
                                    bw, bar.height())))
        return out

    def hit_window_button(self, x: float, y: float) -> str:
        if self.power_button_rect(self.width(), self.height()).contains(
                QPointF(x, y)):
            return "power"
        for bid, rect in self.window_button_rects():
            if rect.contains(QPointF(x, y)):
                return bid
        return ""

    # -- windows flags -------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self._make_click_through()

    def _make_click_through(self):
        try:
            import ctypes
            from ctypes import wintypes
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x20
            WS_EX_LAYERED = 0x80000
            WS_EX_NOACTIVATE = 0x08000000
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)

        # ---- 3D structure (right of centre)
        if self.show_scene and self.scene is not None:
            try:
                frame = getattr(self.scene, "frame", None)
                img = numpy_to_qimage(frame) if frame is not None else QImage()
                if not img.isNull():
                    sx, sy, sw, sh = self._scene_rect(W, H)
                    p.setOpacity(0.96)
                    p.drawImage(QRect(int(sx), int(sy), int(sw), int(sh)), img)
                    p.setOpacity(1.0)
                    self._draw_frame_bracket(p, QRectF(sx, sy, sw, sh))
            except Exception:
                pass

        # ---- chrome
        self._draw_corners(p, W, H)
        self._draw_left_panel(p, W, H)
        self._draw_window_bar(p, W, H)
        self._draw_hint(p, W, H)
        self._draw_reticle(p)
        if self.command_wheel:
            self._draw_wheel(p, W, H)
        p.end()

    def _draw_left_panel(self, p: QPainter, W: int, H: int):
        """Telemetry card - glass, so the text stays readable on any wallpaper."""
        x = int(W * 0.045)
        y = int(H * 0.055)
        card_w = 276
        lines = list(self.telemetry[:11])
        line_h = 15
        card_h = 62 + line_h * len(lines)

        p.setBrush(QBrush(_qcolor(INK, 238)))
        p.setPen(QPen(_qcolor(CYAN_DIM, 1)))
        p.drawRoundedRect(QRectF(x, y, card_w, card_h), 14, 14)
        # glass sheen along the top edge
        p.setPen(QPen(_qcolor(CYAN, 46), 1))
        p.drawLine(int(x + 14), int(y + 1), int(x + card_w - 14), int(y + 1))

        f = QFont(FONT_UI, 12, QFont.Bold)
        p.setFont(f)
        p.setBrush(Qt.NoBrush)
        p.setPen(WHITE)
        p.drawText(x + 16, y + 26, "A  K")

        col = GREEN if self.hands else AMBER
        p.setBrush(QBrush(col))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(x + 64, y + 21), 4, 4)

        p.setBrush(Qt.NoBrush)
        f2 = QFont(FONT_MONO, 8)
        p.setFont(f2)
        p.setPen(MUTED)
        p.drawText(x + 78, y + 25, self.status.upper())

        p.setPen(QPen(_qcolor(CYAN, 90), 1))
        p.drawLine(x + 16, y + 41, x + card_w - 16, y + 41)

        ft = QFont(FONT_MONO, 8)
        p.setFont(ft)
        fm = QFontMetrics(ft)
        ty = y + 57
        for line in lines:
            p.setPen(QColor(198, 206, 220))
            p.drawText(x + 16, ty, fm.elidedText(line, Qt.ElideRight, card_w - 30))
            ty += line_h

    def _draw_window_bar(self, p: QPainter, W: int, H: int):
        """Virtual title bar: tap it like a touchscreen to control windows."""
        bar = self.window_bar_rect(W, H)
        p.setBrush(QBrush(_qcolor(INK, 236)))
        p.setPen(QPen(_qcolor(CYAN_DIM, 1)))
        p.drawRoundedRect(bar, 10, 10)

        # title
        f = QFont(FONT_UI, 8)
        p.setFont(f)
        p.setBrush(Qt.NoBrush)
        p.setPen(_qcolor(MUTED, 220))
        title = self.window_title or "no window"
        fm = QFontMetrics(f)
        p.drawText(QRectF(bar.left() + 12, bar.top(), 92, bar.height()),
                   Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(title, Qt.ElideRight, 88))

        glyphs = {"min": "–", "max": "□", "desk": "▤", "close": "✕"}
        for bid, rect in self.window_button_rects():
            hovered = (bid == self.hover_button)
            if hovered:
                p.setBrush(QBrush(_qcolor(AMBER, 60)))
                p.setPen(QPen(_qcolor(AMBER, 190), 1))
                p.drawRoundedRect(rect.adjusted(2, 5, -2, -5), 6, 6)
            else:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(_qcolor(CYAN_DIM, 1)))
            p.setPen(_qcolor(AMBER if hovered else WHITE, 230 if hovered else 170))
            fg = QFont(FONT_UI, 11)
            p.setFont(fg)
            p.drawText(rect, Qt.AlignCenter, glyphs.get(bid, "?"))

        self._draw_power_button(p, W, H)

    def _draw_power_button(self, p: QPainter, W: int, H: int):
        """The AK master switch - one tap turns the whole stack off."""
        rect = self.power_button_rect(W, H)
        hovered = (self.hover_button == "power")
        p.setBrush(QBrush(_qcolor(RED if hovered else INK,
                                  90 if hovered else 236)))
        p.setPen(QPen(_qcolor(RED, 220 if hovered else 150), 1))
        p.drawRoundedRect(rect, 10, 10)
        # power glyph: a ring with a gap on top and a stem
        cx = rect.center().x() + 0.5
        cy = rect.center().y() + 0.5
        r = 8
        p.setPen(QPen(_qcolor(RED if hovered else WHITE,
                              255 if hovered else 200), 2))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 60 * 16, 280 * 16)
        p.drawLine(int(cx), int(cy - r - 2), int(cx), int(cy - 2))
        if hovered:
            p.setFont(QFont(FONT_UI, 8, QFont.Bold))
            p.setPen(_qcolor(RED, 240))
            p.drawText(QRectF(rect.left() - 90, rect.top(), 84, rect.height()),
                       Qt.AlignRight | Qt.AlignVCenter, "AK OFF")

    def _scene_rect(self, W, H):
        sw = W * 0.44
        sh = H * 0.62
        sx = W - sw - W * 0.045
        sy = (H - sh) / 2
        return sx, sy, sw, sh

    def _draw_frame_bracket(self, p, rect: QRectF):
        p.setPen(QPen(CYAN_DIM, 1))
        L = 22
        for (x, y, dx, dy) in ((rect.left(), rect.top(), 1, 1),
                               (rect.right(), rect.top(), -1, 1),
                               (rect.left(), rect.bottom(), 1, -1),
                               (rect.right(), rect.bottom(), -1, -1)):
            p.drawLine(int(x), int(y), int(x + dx * L), int(y))
            p.drawLine(int(x), int(y), int(x), int(y + dy * L))

    def _draw_corners(self, p, W, H):
        p.setPen(QPen(CYAN_DIM, 1))
        m = 26
        L = 54
        for (x, y, dx, dy) in ((m, m, 1, 1), (W - m, m, -1, 1),
                               (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
            p.drawLine(x, y, x + dx * L, y)
            p.drawLine(x, y, x, y + dy * L)

    def _draw_hint(self, p, W, H):
        f = QFont(FONT_UI, 9)
        p.setFont(f)
        fm = QFontMetrics(f)
        text = self.hint
        tw = fm.horizontalAdvance(text) + 34
        th = 26
        x = int((W - tw) / 2)
        y = H - 52
        p.setBrush(QBrush(_qcolor(INK, 225)))
        p.setPen(QPen(_qcolor(CYAN_DIM, 1)))
        p.drawRoundedRect(QRectF(x, y, tw, th), 13, 13)
        p.setBrush(Qt.NoBrush)
        p.setPen(_qcolor(MUTED, 220))
        p.drawText(QRectF(x, y, tw, th), Qt.AlignCenter, text)

    def _draw_reticle(self, p):
        if not self.hands:
            return
        x, y = self.cursor_px
        pulse = 1.0 + 0.12 * math.sin(time.perf_counter() * 6)
        if self.pinching:
            pulse *= 1.35
        r = 15 * pulse
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(AMBER if self.pinching else CYAN, 1.6))
        p.drawEllipse(QPointF(x, y), r, r)
        p.setPen(QPen(_qcolor(CYAN, 120), 1))
        p.drawEllipse(QPointF(x, y), r * 1.9, r * 1.9)
        p.setPen(QPen(WHITE, 1))
        p.drawLine(int(x - r - 7), int(y), int(x - r + 2), int(y))
        p.drawLine(int(x + r - 2), int(y), int(x + r + 7), int(y))
        p.drawLine(int(x), int(y - r - 7), int(x), int(y - r + 2))
        p.drawLine(int(x), int(y + r - 2), int(x), int(y + r + 7))
        f = QFont(FONT_MONO, 8)
        p.setFont(f)
        p.setPen(_qcolor(MUTED, 190))
        label = self.gesture.upper()
        if self.touch_mode:
            label = "TOUCH " + label
        p.drawText(int(x + r + 12), int(y + 4), label)
        if self.touch_ripple:
            rx, ry, rt = self.touch_ripple
            k = max(0.0, min(1.0, 1.0 - rt / 0.45))
            if k > 0:
                p.setPen(QPen(_qcolor(AMBER, int(210 * k)), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(rx, ry), 12 + (1 - k) * 34,
                              12 + (1 - k) * 34)

    def _draw_wheel(self, p, W, H):
        items, sel = self.command_wheel
        cx, cy = W * 0.5, H * 0.52
        radius = min(W, H) * 0.22
        p.setBrush(QBrush(_qcolor(INK, 190)))
        p.setPen(QPen(CYAN_DIM, 1))
        p.drawEllipse(QPointF(cx, cy), radius, radius)
        n = len(items)
        for i, item in enumerate(items):
            a = -math.pi / 2 + 2 * math.pi * i / n
            ix = cx + math.cos(a) * radius
            iy = cy + math.sin(a) * radius
            active = (i == sel)
            p.setBrush(QBrush(_qcolor(AMBER if active else INK_SOFT, 220)))
            p.setPen(QPen(AMBER if active else CYAN_DIM, 1.4))
            p.drawEllipse(QPointF(ix, iy), 46, 46)
            f = QFont(FONT_UI, 9, QFont.Bold if active else QFont.Normal)
            p.setFont(f)
            p.setBrush(Qt.NoBrush)   # translucent brush kills the text below
            p.setPen(WHITE if active else MUTED)
            p.drawText(QRectF(ix - 60, iy - 12, 120, 24), Qt.AlignCenter, item)


# ── Vision intel popup ─────────────────────────────────────────────────────
