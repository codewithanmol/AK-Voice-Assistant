"""AK camera window - a cinematic, glassy live-video popup.

Design notes (why it looks the way it does):
  * The VIDEO AREA IS OPAQUE. A translucent top level window on Windows gets
    the desktop acrylic/blur treatment applied to its own surface, which washes
    the video out into a flat colour. So the picture itself is painted solid;
    only the CHROME around it is glassy.
  * The video is drawn 1:1 - the numpy frame is resized with OpenCV to the
    exact video size and painted without any Qt pixmap scaling, so what the
    camera sees is exactly what is on screen.
  * The picture can be flipped left/right (MIRROR, persisted). The webcam
    driver already hands us a mirrored stream, so the default is the flipped
    "photo" view - turn your head right, the picture moves right. Press M (or
    the MIRROR button) to switch back instantly.
  * The palette is deep charcoal + maroon (never pure black) with cyan/amber
    accents - Apple-ish glass, cinematic, calm.

What the window shows:
  * header   - privacy dot, clock, LIVE, fps, help/compact buttons
  * entities - smart strip above the picture: your name, HUMAN, FACE,
    OBJECT, WEARING, LOOKING, GLARE, HANDS
  * video    - the camera picture (opaque) + hand landmark overlay
  * tiles    - FACE / HANDS / GLARE / FPS stat tiles
  * chips    - the live gesture chips (POINT, PINCH, FIST, ...)
  * narration- what the vision brain just said (typed out, always visible)
  * footer   - SNAPSHOT / COMPACT / FOLLOW / MIRROR / CLOSE

Settings persist to `camera_window.json` (next to the AK config).
"""

from __future__ import annotations

import json
import os
import time

import cv2
import numpy as np

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PyQt5.QtGui import (QBrush, QColor, QFont, QFontMetrics, QGuiApplication,
                         QImage, QPainter, QPen, QLinearGradient, QRadialGradient)
from PyQt5.QtWidgets import QWidget

FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"

# ── palette (deep charcoal + maroon, never pure black) ──────────────────
BG = QColor(17, 18, 23)           # window body
BAR = QColor(26, 27, 34)          # header / footer bars
GLASS = QColor(38, 39, 48)        # glass tile fill
GLASS_HI = QColor(52, 54, 66)     # glass tile highlight
LINE = QColor(58, 60, 72)         # separators / borders
WHITE = QColor(238, 242, 248)
MUTED = QColor(158, 168, 186)
CYAN = QColor(120, 226, 255)
AMBER = QColor(255, 168, 96)
GREEN = QColor(120, 230, 170)
RED = QColor(255, 96, 96)
MAROON = QColor(138, 44, 58)      # AK accent
MAROON_HI = QColor(196, 74, 92)

VIDEO_W = 640
VIDEO_H = 480
PAD = 10
WIN_W = VIDEO_W + PAD * 2

HEAD_H = 46
ENT_H = 30                  # smart entity strip (above the picture)
TILES_H = 50
CHIPS_H = 34
NARR_H = 40
FOOT_H = 44
GAP = 8

TARGET_MEDIAN = 118.0            # how bright the picture should look

# gesture chip vocabulary (matches AK/gesture_core.py)
GESTURES = [
    ("point", "POINT", CYAN),
    ("point1", "POINT1", CYAN),
    ("pinch", "PINCH", AMBER),
    ("fist", "FIST", MAROON_HI),
    ("palm", "PALM", GREEN),
    ("peace", "PEACE", QColor(178, 150, 255)),
    ("thumb_up", "THUMB+", QColor(255, 208, 120)),
    ("thumb_down", "THUMB-", QColor(255, 208, 120)),
    ("three", "THREE", QColor(140, 220, 235)),
]

# hand skeleton (MediaPipe order)
HAND_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def enhance(frame: np.ndarray) -> np.ndarray:
    """Make the picture pleasant to read: exposure, contrast, sharpness.

    * a dim room gives a nearly black preview -> gamma lift of the midtones
    * local contrast (CLAHE) so the face pops out of a flat wall
    * a small unsharp mask so the picture is not mushy
    Frames that are already well exposed are left alone.
    """
    if frame is None or frame.size == 0:
        return frame
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        med = float(np.median(gray))
        hi = float(np.percentile(gray, 97))
        if 78.0 <= med <= 170.0 and hi <= 240.0:
            return frame                       # already good, do not touch

        out = frame
        if med < 78.0:
            # gamma curve towards the target brightness
            if med > 6.0:
                gamma = float(np.log(TARGET_MEDIAN / 255.0)
                              / np.log(med / 255.0))
            else:
                gamma = 0.45
            gamma = max(0.40, min(1.0, gamma))
            lut = np.clip(((np.arange(256) / 255.0) ** gamma) * 255.0,
                          0, 255).astype(np.uint8)
            out = cv2.LUT(out, lut)
        elif hi > 240.0:
            # blown out window -> pull the highlights back
            out = cv2.convertScaleAbs(out, alpha=0.75, beta=0)

        # local contrast on the L channel
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        lch, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        lch = clahe.apply(lch)
        out = cv2.cvtColor(cv2.merge([lch, a, b]), cv2.COLOR_LAB2BGR)

        # mild sharpening
        blur = cv2.GaussianBlur(out, (0, 0), 1.1)
        out = cv2.addWeighted(out, 1.30, blur, -0.30, 0)
        return out
    except Exception:
        return frame


def _to_qimage(frame_bgr: np.ndarray) -> QImage:
    """BGR uint8 numpy -> QImage (own copy, no scaling, no alpha)."""
    if frame_bgr is None or frame_bgr.size == 0:
        return QImage()
    if frame_bgr.shape[1] != VIDEO_W or frame_bgr.shape[0] != VIDEO_H:
        frame_bgr = cv2.resize(frame_bgr, (VIDEO_W, VIDEO_H),
                               interpolation=cv2.INTER_LINEAR)
    rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class CameraWindow(QWidget):
    """Live camera + expression / wardrobe / hand summary.

    Opaque video, glassy chrome, hand overlay, stat tiles, gesture chips and
    persistent settings.
    """

    def __init__(self, parent=None, settings_path=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self.frame = None              # last BGR numpy frame
        self.report = None             # last vision report
        self.hands = []                # HandFeatures list (for the overlay)
        self.visible_anim = 0.0
        self.target_visible = False
        self.follow_enabled = True
        self.follow_x = 0.5
        self.follow_y = 0.5
        self.pinned = False            # user parked the window by hand

        self._qframe = None
        self._fps_value = 0
        self._last_build = 0.0
        self._last_frame_ts = 0.0
        self._shot_dir = os.path.join(
            os.path.expanduser("~"), "Pictures", "AK")
        self._drag_from = None
        self._hover = ""
        self._pressed = ""
        self.toast = ""
        self.toast_until = 0.0
        self._help_open = False

        # persisted settings
        self._settings_path = settings_path or self._default_settings_path()
        self.settings = {"compact": False, "follow": True, "hands": True,
                         "tiles": True, "chips": True, "mirror": True}
        self._load_settings()

        # narration typewriter
        self._narr_full = ""
        self._narr_shown = 0
        self._narr_ts = 0.0
        self._type_timer = QTimer(self)
        self._type_timer.setInterval(28)
        self._type_timer.timeout.connect(self._type_step)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_anim)
        self._anim.finished.connect(self._on_anim_done)
        self._apply_size()

    # -- settings ------------------------------------------------------------
    @staticmethod
    def _default_settings_path() -> str:
        base = os.environ.get("AK_DATA_PATH") or r"D:\YOUR_DATA_PATH"
        return os.path.join(base, "camera_window.json")

    def _load_settings(self) -> None:
        try:
            with open(self._settings_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k in self.settings:
                    if k in data:
                        self.settings[k] = bool(data[k])
        except Exception:
            pass
        self.follow_enabled = bool(self.settings.get("follow", True))
        self.pinned = not self.follow_enabled

    def _save_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._settings_path) or ".", exist_ok=True)
            with open(self._settings_path, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, indent=2)
        except Exception:
            pass

    def _apply_size(self) -> None:
        h = HEAD_H + GAP + ENT_H + GAP + VIDEO_H
        if not self.settings.get("compact"):
            h += GAP + TILES_H + CHIPS_H
        h += GAP + NARR_H + FOOT_H + GAP
        self.setFixedSize(WIN_W, h)

    # -- mirror --------------------------------------------------------------
    @property
    def mirror(self) -> bool:
        """True = the picture is flipped left/right (selfie / photo view)."""
        return bool(self.settings.get("mirror", True))

    def set_mirror(self, on: bool) -> None:
        self.settings["mirror"] = bool(on)
        self._save_settings()
        self._rebuild_frame()
        self.update()

    def _rebuild_frame(self) -> None:
        """Re-render the picture (after a mirror flip or a settings change)."""
        if self.frame is None:
            return
        self._last_build = 0.0
        try:
            pic = enhance(self.frame)
            if self.mirror:
                pic = cv2.flip(pic, 1)
            self._qframe = _to_qimage(pic)
        except Exception:
            self._qframe = None

    def toggle_mirror(self) -> None:
        self.set_mirror(not self.mirror)
        self._toast("MIRROR " + ("ON" if self.mirror else "OFF"))

    def set_compact(self, on: bool) -> None:
        self.settings["compact"] = bool(on)
        self._apply_size()
        self._save_settings()
        self.update()

    def toggle_compact(self) -> None:
        self.set_compact(not self.settings.get("compact"))

    def set_show_hands(self, on: bool) -> None:
        self.settings["hands"] = bool(on)
        self._save_settings()
        self.update()

    # -- show / hide ---------------------------------------------------------
    def _on_anim(self, value):
        self.visible_anim = float(value)
        self.update()

    def _on_anim_done(self):
        if self.target_visible:
            self.visible_anim = 1.0
            self.update()
        else:
            self.hide()

    def show_panel(self):
        self.target_visible = True
        self._place_on_screen()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self.visible_anim)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_panel(self):
        self.target_visible = False
        self._anim.stop()
        self._anim.setStartValue(self.visible_anim)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def toggle(self):
        if self.target_visible:
            self.hide_panel()
        else:
            self.show_panel()

    def _place_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        x = max(g.left() + 8, g.right() - self.width() - 32)
        y = max(g.top() + 8, g.bottom() - self.height() - 32)
        self.move(x, y)

    # -- mouse ---------------------------------------------------------------
    def _buttons(self):
        """(key, rect, label) of every clickable control, in paint order."""
        W = self.width()
        H = self.height()
        out = []
        # header buttons (help, compact, close)
        out.append(("help", QRect(W - 98, 10, 26, 26), "?"))
        out.append(("compact", QRect(W - 68, 10, 26, 26), "-"))
        out.append(("close", QRect(W - 38, 10, 26, 26), "X"))
        # footer buttons
        fy = H - FOOT_H + 6
        out.append(("shot", QRect(PAD, fy, 96, 30), "SNAPSHOT"))
        out.append(("compact2", QRect(PAD + 104, fy, 80, 30), "COMPACT"))
        out.append(("follow", QRect(PAD + 192, fy, 80, 30), "FOLLOW"))
        out.append(("mirror", QRect(PAD + 280, fy, 84, 30), "MIRROR"))
        out.append(("close2", QRect(W - PAD - 76, fy, 76, 30), "CLOSE"))
        return out

    def _button_at(self, pos):
        for key, rect, _lbl in self._buttons():
            if rect.contains(pos):
                return key, rect
        return None, None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            key, _rect = self._button_at(event.pos())
            if key:
                self._pressed = key
                return
            self._drag_from = (event.globalPos(), self.pos())

    def mouseMoveEvent(self, event):
        key, _rect = self._button_at(event.pos())
        self._hover = key or ""
        if self._drag_from and (event.buttons() & Qt.LeftButton):
            gp, start = self._drag_from
            delta = event.globalPos() - gp
            self.move(start + delta)
            self.pinned = True            # a manual move detaches it
            self.follow_enabled = False
            self.settings["follow"] = False
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_from = None
        if self._pressed:
            key, _rect = self._button_at(event.pos())
            if key == self._pressed:
                self._click(key)
            self._pressed = ""

    def leaveEvent(self, event):
        self._hover = ""
        self.update()

    def mouseDoubleClickEvent(self, event):
        # double click -> re-attach to the face / detach again
        self.pinned = not self.pinned
        self.follow_enabled = not self.pinned
        self.settings["follow"] = self.follow_enabled
        self._save_settings()
        self.update()

    def _click(self, key) -> None:
        if key == "shot":
            self.save_shot()
        elif key in ("compact", "compact2"):
            self.toggle_compact()
        elif key == "help":
            self._help_open = not self._help_open
            self.update()
        elif key == "follow":
            self.follow_enabled = not self.follow_enabled
            self.pinned = not self.follow_enabled
            self.settings["follow"] = self.follow_enabled
            self._save_settings()
            self._toast("FACE FOLLOW " + ("ON" if self.follow_enabled else "OFF"))
        elif key == "mirror":
            self.toggle_mirror()
        elif key in ("close", "close2"):
            self.hide_panel()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            if self._help_open:
                self._help_open = False
                self.update()
            else:
                self.hide_panel()
        elif key == Qt.Key_F:
            self._click("follow")
        elif key == Qt.Key_S:
            self.save_shot()
        elif key == Qt.Key_C:
            self.toggle_compact()
        elif key == Qt.Key_M:
            self.toggle_mirror()
        elif key == Qt.Key_H or key == Qt.Key_Slash:
            self._help_open = not self._help_open
            self.update()

    # -- features ------------------------------------------------------------
    def _toast(self, text: str) -> None:
        self.toast = text
        self.toast_until = time.perf_counter() + 2.2
        self.update()

    def save_shot(self):
        """Save the current frame as a PNG (S key / SNAPSHOT button)."""
        if self.frame is None:
            self._toast("NO FRAME TO SAVE")
            return
        try:
            os.makedirs(self._shot_dir, exist_ok=True)
            name = time.strftime("ak_shot_%Y%m%d_%H%M%S.png")
            path = os.path.join(self._shot_dir, name)
            pic = self.frame
            if self.mirror:
                pic = cv2.flip(pic, 1)
            cv2.imwrite(path, pic)
            self._toast("SAVED " + name)
        except Exception:
            self._toast("SAVE FAILED")

    def set_fps(self, value):
        self._fps_value = int(value or 0)

    def follow_face(self, fx: float, fy: float = None):
        """Glide the window with the face - any direction (left/right/up/down).

        The face position is normalised inside the camera picture. The window
        sits in the bottom-right corner when the face is centred and then
        slides freely in every direction while you move around.
        """
        if not self.target_visible or not self.follow_enabled:
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        self.follow_x = max(0.0, min(1.0, float(fx)))
        if fy is not None:
            self.follow_y = max(0.0, min(1.0, float(fy)))
        base_x = g.right() - self.width() - 32
        base_y = g.bottom() - self.height() - 32
        travel_x = int(g.width() * 0.18)
        travel_y = int(g.height() * 0.28)
        # face left of centre -> window slides left, face up -> window slides up
        target_x = base_x - int((1.0 - self.follow_x) * travel_x)
        target_y = base_y - int((1.0 - self.follow_y) * travel_y)
        target_x = max(g.left() + 8, min(target_x, g.right() - self.width() - 8))
        target_y = max(g.top() + 8, min(target_y, g.bottom() - self.height() - 8))
        cur = self.pos()
        nx = int(cur.x() + (target_x - cur.x()) * 0.20)
        ny = int(cur.y() + (target_y - cur.y()) * 0.20)
        nx = max(g.left() + 8, min(nx, g.right() - self.width() - 8))
        ny = max(g.top() + 8, min(ny, g.bottom() - self.height() - 8))
        if nx != cur.x() or ny != cur.y():
            self.move(nx, ny)

    # -- data ----------------------------------------------------------------
    def update_frame(self, frame_bgr, report=None, ts=None, hands=None):
        if frame_bgr is not None:
            self.frame = frame_bgr
            self._last_frame_ts = (ts if ts is not None
                                   else time.perf_counter())
            # the enhancement is not free - keep it to ~11 fps, the picture
            # still looks live and the CPU stays sane
            now = time.perf_counter()
            if self._qframe is None or (now - self._last_build) > 0.09:
                self._last_build = now
                try:
                    pic = enhance(frame_bgr)
                    if self.mirror:
                        pic = cv2.flip(pic, 1)
                    self._qframe = _to_qimage(pic)
                except Exception:
                    self._qframe = None
        if report is not None:
            self.report = report
        if hands is not None:
            self.hands = list(hands) or []
        if self.target_visible:
            self.update()

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event):
        try:
            self._paint()
        except Exception:
            pass

    def _glass(self, p, rect, radius=12, tint=GLASS):
        """A soft glass plate: vertical gradient + a bright top edge."""
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, QColor(tint.red() + 16, tint.green() + 16,
                                    tint.blue() + 18))
        grad.setColorAt(1.0, tint)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)

    def _paint(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        t = ease_out_cubic(self.visible_anim)

        # body ----------------------------------------------------------------
        p.fillRect(0, 0, W, H, BG)
        p.setOpacity(t)

        compact = bool(self.settings.get("compact"))
        vx, vy = PAD, HEAD_H + GAP

        # header --------------------------------------------------------------
        self._paint_header(p, W)

        # entity strip - sits right above the picture ------------------------
        vy = self._paint_entities(p, HEAD_H + GAP, W)
        p.fillRect(QRect(vx - 1, vy - 1, VIDEO_W + 2, VIDEO_H + 2), LINE)
        img = self._qframe
        stale = (self._last_frame_ts and
                 (time.perf_counter() - self._last_frame_ts) > 2.5)
        flat = False
        if self.frame is not None:
            try:
                flat = float(self.frame.std()) < 2.0
            except Exception:
                flat = False
        if img is not None and not img.isNull():
            p.drawImage(QPoint(vx, vy), img)
            if stale or flat:
                p.fillRect(QRect(vx, vy, VIDEO_W, VIDEO_H),
                           QColor(10, 11, 15, 185))
                p.setFont(QFont(FONT_MONO, 12))
                p.setPen(AMBER)
                p.drawText(QRect(vx, vy, VIDEO_W, VIDEO_H), Qt.AlignCenter,
                           "CAMERA RE-ACQUIRING" if flat else "CAMERA IDLE")
            else:
                self._draw_boxes(p, vx, vy)
                self._draw_hands(p, vx, vy)
        else:
            p.fillRect(QRect(vx, vy, VIDEO_W, VIDEO_H), QColor(26, 27, 34))
            p.setFont(QFont(FONT_MONO, 10))
            p.setPen(MUTED)
            p.drawText(QRect(vx, vy, VIDEO_W, VIDEO_H), Qt.AlignCenter,
                       "NO SIGNAL")
        # inner frame over the picture
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(vx - 0.5, vy - 0.5, VIDEO_W + 1, VIDEO_H + 1))

        y = vy + VIDEO_H + GAP

        if not compact:
            y = self._paint_tiles(p, y, W)
            y = self._paint_chips(p, y, W)

        # the narration line is always visible - compact only drops the tiles
        y = self._paint_narration(p, y, W)

        # footer --------------------------------------------------------------
        self._paint_footer(p, W, H)

        # help overlay --------------------------------------------------------
        if self._help_open:
            self._paint_help(p, W, H)

        # toast ---------------------------------------------------------------
        toast = getattr(self, "toast", "")
        if toast and time.perf_counter() < getattr(self, "toast_until", 0):
            p.setFont(QFont(FONT_MONO, 9))
            p.setPen(GREEN)
            p.drawText(QRect(0, H - FOOT_H - 18, W, 16),
                       Qt.AlignCenter, toast)

        # border
        p.setPen(QPen(LINE, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, W - 1, H - 1))
        p.end()

    # -- header --------------------------------------------------------------
    def _paint_header(self, p, W):
        grad = QLinearGradient(0, 0, 0, HEAD_H)
        grad.setColorAt(0.0, QColor(38, 39, 50))
        grad.setColorAt(1.0, BAR)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.fillRect(QRect(0, 0, W, HEAD_H), BAR)
        p.fillRect(QRect(0, 0, W, HEAD_H), QBrush(grad))
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, HEAD_H, W, HEAD_H)
        # privacy dot
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(RED))
        p.drawEllipse(QPoint(16, HEAD_H // 2), 5, 5)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 120, 120, 70), 1))
        p.drawEllipse(QPoint(16, HEAD_H // 2), 8, 8)

        p.setFont(QFont(FONT_UI, 11, QFont.Bold))
        p.setPen(WHITE)
        p.drawText(QRect(32, 0, 200, HEAD_H),
                   Qt.AlignVCenter | Qt.AlignLeft, "AK CAMERA")

        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QColor(190, 200, 215))
        p.drawText(QRect(0, 0, W - 198, HEAD_H),
                   Qt.AlignVCenter | Qt.AlignRight,
                   time.strftime("%H:%M:%S"))

        p.setFont(QFont(FONT_UI, 9, QFont.Bold))
        p.setPen(WHITE)
        p.drawText(QRect(W - 194, 0, 34, HEAD_H),
                   Qt.AlignVCenter | Qt.AlignLeft, "LIVE")
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(MUTED)
        p.drawText(QRect(W - 160, 0, 56, HEAD_H),
                   Qt.AlignVCenter | Qt.AlignRight, "%d FPS" % self._fps_value)

        for key, rect, lbl in self._buttons():
            if rect.top() > HEAD_H:
                continue
            self._paint_button(p, key, rect, lbl)

    # -- entity strip (above the picture) ------------------------------------
    def _user_name(self) -> str:
        """The name AK should call the person in front of the camera."""
        if getattr(self, "_name_cache", None) is not None:
            return self._name_cache
        name = "SIR"
        base = os.environ.get("AK_DATA_PATH") or r"D:\YOUR_DATA_PATH"
        try:
            path = os.path.join(base, "face_lock", "users.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh) or {}
            if isinstance(data, dict) and data:
                first = sorted(data.keys())[0]
                name = str(first).strip().upper() or "SIR"
        except Exception:
            pass
        self._name_cache = name
        return name

    def _entity_chips(self):
        """(label, value, colour) for everything the vision brain can see."""
        rep = self.report
        out = [(self._user_name(), "", CYAN)]
        face = getattr(rep, "face", None)
        if face is not None and getattr(face, "detected", False):
            out.append(("HUMAN", "%d%%" % int(getattr(face, "confidence", 0) * 100),
                        GREEN))
            expr = str(getattr(face, "expression", "") or "")
            if expr:
                out.append(("FACE", expr.upper(), AMBER))
            gaze = str(getattr(face, "gaze", "") or "")
            if gaze and gaze != "centre":
                out.append(("LOOKING", gaze.upper(), CYAN))
            if getattr(face, "speaking", False):
                out.append(("VOICE", "SPEAKING", GREEN))
            if getattr(face, "glasses", False):
                out.append(("FACE", "GLASSES", MUTED))
        else:
            out.append(("HUMAN", "NONE", MUTED))
        obj = getattr(rep, "obj", None)
        if obj is not None and getattr(obj, "detected", False):
            tag = "HELD" if getattr(obj, "held", False) else "SEEN"
            out.append(("OBJECT", ("%s %s" % (str(obj.name).upper(), tag)).strip(),
                        GREEN))
        cloth = getattr(rep, "cloth", None)
        if cloth is not None and getattr(cloth, "detected", False):
            wear = str(getattr(cloth, "garment", "") or getattr(cloth, "primary", "") or "")
            if wear:
                out.append(("WEARING", wear.upper(), MAROON_HI))
        glare = int(getattr(rep, "glare_pct", 0) or 0)
        if glare > 25:
            out.append(("GLARE", "%d%%" % glare, AMBER))
        if self.hands:
            out.append(("HANDS", str(len(self.hands)), CYAN))
        return out

    def _paint_entities(self, p, y, W):
        chips = self._entity_chips()
        x = PAD
        p.setFont(QFont(FONT_UI, 8))
        for label, value, colour in chips:
            lw = p.fontMetrics().horizontalAdvance(label) + 10
            vw = (p.fontMetrics().horizontalAdvance(value) + 10) if value else 0
            w = lw + vw
            if x + w > W - PAD:
                rest = len(chips) - chips.index((label, value, colour))
                p.setPen(MUTED)
                p.drawText(QRect(x, y, 40, ENT_H),
                           Qt.AlignLeft | Qt.AlignVCenter, "+%d" % max(0, rest - 1))
                break
            rect = QRect(x, y + 3, w, ENT_H - 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(colour.red(), colour.green(), colour.blue(), 40)))
            p.drawRoundedRect(rect, 7, 7)
            p.setPen(QPen(QColor(colour.red(), colour.green(), colour.blue(), 120), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 7, 7)
            p.setFont(QFont(FONT_UI, 7, QFont.Bold))
            p.setPen(MUTED)
            p.drawText(QRect(x + 5, y, lw, ENT_H),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            if value:
                p.setFont(QFont(FONT_UI, 8, QFont.Bold))
                p.setPen(colour)
                p.drawText(QRect(x + 5 + lw, y, vw, ENT_H),
                           Qt.AlignLeft | Qt.AlignVCenter, value)
            x += w + 6
        return y + ENT_H + GAP

    # -- stat tiles ----------------------------------------------------------
    def _paint_tiles(self, p, y, W):
        if not self.settings.get("tiles", True):
            return y
        rep = self.report
        face_ok = bool(rep is not None and getattr(rep.face, "detected", False))
        face_conf = int(getattr(getattr(rep, "face", None), "confidence", 0) * 100)
        hands = len(self.hands or [])
        glare = int(getattr(rep, "glare_pct", 0) or 0)
        gesture = ""
        if hands:
            gesture = str(self.hands[0].gesture)

        tiles = [
            ("FACE", ("%d%%" % face_conf) if face_ok else "no",
             GREEN if face_ok else MUTED),
            ("HANDS", str(hands), CYAN if hands else MUTED),
            ("GLARE", "%d%%" % glare, AMBER if glare > 25 else MUTED),
            ("GESTURE", gesture.upper() or "none",
             AMBER if gesture else MUTED),
            ("FPS", str(self._fps_value),
             GREEN if self._fps_value >= 20 else (AMBER if self._fps_value else MUTED)),
        ]
        n = len(tiles)
        gap = 8
        tw = int((W - PAD * 2 - gap * (n - 1)) / n)
        for i, (label, value, colour) in enumerate(tiles):
            x = PAD + i * (tw + gap)
            rect = QRect(x, y, tw, TILES_H)
            self._glass(p, rect, 10)
            p.setFont(QFont(FONT_UI, 7))
            p.setPen(MUTED)
            p.drawText(QRect(x + 10, y + 7, tw - 20, 12),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setFont(QFont(FONT_UI, 11, QFont.Bold))
            p.setPen(colour)
            p.drawText(QRect(x + 10, y + 21, tw - 20, 22),
                       Qt.AlignLeft | Qt.AlignVCenter, value)
        return y + TILES_H + GAP

    # -- gesture chips -------------------------------------------------------
    def _paint_chips(self, p, y, W):
        if not self.settings.get("chips", True):
            return y
        live = {}
        for h in (self.hands or []):
            live[str(h.gesture)] = live.get(str(h.gesture), 0) + 1
        p.setFont(QFont(FONT_UI, 7))
        p.setPen(MUTED)
        p.drawText(QRect(PAD, y, 60, CHIPS_H),
                   Qt.AlignLeft | Qt.AlignVCenter, "GESTURES")
        x = PAD + 62
        for key, label, colour in GESTURES:
            count = live.get(key, 0)
            on = count > 0
            w = 8 + len(label) * 6 + (14 if on else 0)
            if x + w > W - PAD:
                break
            rect = QRect(x, y + 4, w, CHIPS_H - 10)
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                        colour.blue(), 52)))
                p.drawRoundedRect(rect, 8, 8)
                p.setPen(QPen(QColor(colour.red(), colour.green(),
                                     colour.blue(), 150), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect, 8, 8)
                p.setPen(colour)
                p.setFont(QFont(FONT_UI, 8, QFont.Bold))
                p.drawText(rect, Qt.AlignCenter, "%s x%d" % (label, count))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 16), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect, 8, 8)
                p.setPen(QColor(120, 128, 145))
                p.setFont(QFont(FONT_UI, 8))
                p.drawText(rect, Qt.AlignCenter, label)
            x += w + 6
        return y + CHIPS_H + GAP

    # -- narration (typewriter) ----------------------------------------------
    def _set_narration(self, text: str) -> None:
        """New text -> restart the typing animation."""
        text = text or ""
        if text == self._narr_full:
            return
        self._narr_full = text
        self._narr_shown = 0
        self._narr_ts = time.perf_counter()
        if text:
            self._type_timer.start()
        else:
            self._type_timer.stop()

    def _type_step(self) -> None:
        """Reveal a few more characters (fast enough for long sentences)."""
        if not self._narr_full:
            self._type_timer.stop()
            return
        remaining = len(self._narr_full) - self._narr_shown
        step = 1 if remaining > 40 else (2 if remaining > 16 else 3)
        self._narr_shown = min(len(self._narr_full),
                               self._narr_shown + step)
        if self._narr_shown >= len(self._narr_full):
            self._type_timer.stop()
        self.update()

    def _paint_narration(self, p, y, W):
        rep = self.report
        text = ""
        if rep is not None:
            text = getattr(rep, "narration", "") or ""
        if not text:
            for det in (getattr(rep, "details", None) or []):
                text = getattr(det, "text", "") or str(det)
                if text:
                    break
        self._set_narration(text)
        shown = self._narr_full[:self._narr_shown]
        typing = self._type_timer.isActive() and self._narr_shown < len(self._narr_full)

        rect = QRect(PAD, y, W - PAD * 2, NARR_H)
        self._glass(p, rect, 10)
        p.setFont(QFont(FONT_UI, 9))
        p.setPen(QColor(206, 214, 228) if self._narr_full else MUTED)
        if not self._narr_full:
            p.drawText(rect.adjusted(12, 0, -12, 0),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                       "Vision brain is listening...")
            return y + NARR_H + GAP

        body = self._elide(shown, p.font(), W - PAD * 2 - 34)
        p.drawText(rect.adjusted(12, 0, -20, 0),
                   Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, body)
        # blinking caret while the sentence is still being written
        if typing and int(time.perf_counter() * 2) % 2 == 0:
            fm = QFontMetrics(p.font())
            caret_x = rect.left() + 12 + fm.horizontalAdvance(body)
            p.setPen(CYAN)
            p.drawRect(QRect(caret_x, rect.top() + 11, 2, 18))
        return y + NARR_H + GAP

    # -- footer --------------------------------------------------------------
    def _paint_footer(self, p, W, H):
        fy = H - FOOT_H
        grad = QLinearGradient(0, fy, 0, H)
        grad.setColorAt(0.0, BAR)
        grad.setColorAt(1.0, QColor(22, 23, 29))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.fillRect(QRect(0, fy, W, FOOT_H), BAR)
        p.fillRect(QRect(0, fy, W, FOOT_H), QBrush(grad))
        p.setPen(QPen(LINE, 1))
        p.drawLine(0, fy, W, fy)

        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(MUTED)
        p.drawText(QRect(382, fy, W - 382 - 86, FOOT_H),
                   Qt.AlignVCenter | Qt.AlignRight,
                   "S shot  C compact  F follow  M mirror  H help")

        for key, rect, lbl in self._buttons():
            if rect.top() < HEAD_H:
                continue
            if key == "follow":
                self._paint_button(p, key, rect,
                                   "FOLLOW" if self.follow_enabled else "PINNED")
            elif key == "mirror":
                self._paint_button(p, key, rect,
                                   "MIRROR ON" if self.mirror else "MIRROR OFF")
            else:
                self._paint_button(p, key, rect, lbl)

    def _paint_button(self, p, key, rect, label):
        hot = (self._hover == key) or (self._pressed == key)
        if key in ("close", "close2"):
            base, fg = QColor(76, 32, 40), QColor(255, 176, 176)
        elif key == "shot":
            base, fg = GLASS, WHITE
        elif key == "follow" and self.follow_enabled:
            base, fg = QColor(30, 58, 50), GREEN
        elif key == "mirror" and self.mirror:
            base, fg = QColor(30, 52, 66), CYAN
        else:
            base, fg = GLASS, MUTED
        if hot:
            base = QColor(min(255, base.red() + 18), min(255, base.green() + 18),
                          min(255, base.blue() + 18))
            fg = WHITE
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(QBrush(base))
        p.drawRoundedRect(rect, 8, 8)
        p.setFont(QFont(FONT_UI, 8, QFont.Bold))
        p.setPen(fg)
        p.drawText(rect, Qt.AlignCenter, label)

    # -- help ----------------------------------------------------------------
    def _paint_help(self, p, W, H):
        p.fillRect(QRect(0, 0, W, H), QColor(10, 11, 15, 225))
        rect = QRect(28, 26, W - 56, H - 52)
        self._glass(p, rect, 16, QColor(30, 31, 40))
        p.setFont(QFont(FONT_UI, 12, QFont.Bold))
        p.setPen(WHITE)
        p.drawText(QRect(rect.left() + 22, rect.top() + 18, rect.width() - 44, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, "AK CAMERA - SHORTCUTS")
        p.setPen(QPen(LINE, 1))
        p.drawLine(rect.left() + 22, rect.top() + 48,
                   rect.right() - 22, rect.top() + 48)
        rows = [
            ("S  /  SNAPSHOT", "Save the current frame as a PNG"),
            ("C  /  COMPACT", "Hide the tiles and gesture chips"),
            ("F  /  FOLLOW", "Window follows your face in any direction"),
            ("M  /  MIRROR", "Flip the picture left / right"),
            ("H  /  ?", "Open / close this help"),
            ("CLOSE / ESC", "Close the camera window"),
            ("drag", "Move the window (pins it)"),
            ("double click", "Re-attach to the face / pin again"),
        ]

        y = rect.top() + 62
        for k, v in rows:
            p.setFont(QFont(FONT_MONO, 9))
            p.setPen(CYAN)
            p.drawText(QRect(rect.left() + 22, y, 150, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, k)
            p.setFont(QFont(FONT_UI, 9))
            p.setPen(QColor(200, 208, 222))
            p.drawText(QRect(rect.left() + 178, y, rect.width() - 200, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, v)
            y += 24
        p.setFont(QFont(FONT_UI, 8))
        p.setPen(MUTED)
        p.drawText(QRect(rect.left() + 22, rect.bottom() - 30,
                         rect.width() - 44, 18),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   "Video area stays opaque on purpose - acrylic blur washes "
                   "the picture out.")

    # -- overlays ------------------------------------------------------------
    def _draw_boxes(self, p, vx, vy):
        """Thin detection boxes - face, garment, held object."""
        rep = self.report
        if rep is None or self.frame is None:
            return
        fh, fw = self.frame.shape[0], self.frame.shape[1]

        def box(bbox, colour, width=2):
            x, y, w, h = bbox
            if w <= 0 or h <= 0:
                return
            if self.mirror:
                x = 1.0 - x - w
            rx = vx + int(x * fw)
            ry = vy + int(y * fh)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(colour, width))
            p.drawRect(QRectF(rx, ry, w * fw, h * fh))

        if getattr(rep.face, "detected", False):
            box(rep.face.bbox, AMBER)
        if getattr(rep.cloth, "detected", False):
            box(rep.cloth.bbox, CYAN, 2)
        if getattr(rep.obj, "detected", False):
            box(rep.obj.bbox, GREEN, 2)

    def _draw_hands(self, p, vx, vy):
        """Hand skeleton overlay - the picture stays clean, the hands glow."""
        if not self.settings.get("hands", True):
            return
        hands = self.hands or []
        if not hands or self.frame is None:
            return
        fh, fw = self.frame.shape[0], self.frame.shape[1]
        colours = {g[0]: g[2] for g in GESTURES}
        for hand in hands:
            lm = getattr(hand, "lm", None)
            if lm is None:
                continue
            try:
                pts = np.asarray(lm, dtype=np.float32)
                if pts.ndim != 2 or pts.shape[0] < 21:
                    continue
            except Exception:
                continue
            colour = colours.get(str(getattr(hand, "gesture", "")), CYAN)
            mapped = []
            for i in range(21):
                px = float(pts[i][0])
                if self.mirror:
                    px = 1.0 - px
                x = vx + px * fw
                y = vy + float(pts[i][1]) * fh
                mapped.append(QPoint(int(x), int(y)))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(colour.red(), colour.green(), colour.blue(), 170), 2))
            for a, b in HAND_EDGES:
                p.drawLine(mapped[a], mapped[b])
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(colour))
            for i, pt in enumerate(mapped):
                r = 4 if i in (4, 8, 12, 16, 20) else 2
                p.drawEllipse(pt, r, r)
            # pinch glow
            if getattr(hand, "pinch_active", False):
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 190, 120, 200), 2))
                p.drawLine(mapped[4], mapped[8])

    def _elide(self, text: str, font: QFont, width: int) -> str:
        fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) <= width:
            return text
        while text and fm.horizontalAdvance(text + "...") > width:
            text = text[:-1]
        return text + "..."
