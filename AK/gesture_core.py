"""Hand tracking + gesture semantics for AK's holographic control layer.

Fully local: MediaPipe Tasks hand landmarker (21 landmarks, up to 2 hands) on
top of an OpenCV camera feed. No network, no cloud, no API keys.

What this module gives the rest of AK:
  * ``HandTracker``  — camera + landmarker, yields landmarks per frame.
  * ``GestureEngine``— landmarks -> semantic gestures (point / pinch / fist /
    palm / peace / swipe) plus a smoothed screen cursor and a 3D aim vector.
  * ``OneEuroFilter``— the smoothing that makes a hand-driven cursor feel
    like glass instead of jelly (low jitter when still, low lag when moving).

Design notes
------------
* Every geometric decision is scale-invariant: distances are normalised by the
  hand span (wrist -> middle knuckle), so it works the same whether the hand
  is near the lens or far away.
* Pinch / grab use hysteresis (different on/off thresholds) so a trembling
  hand does not machine-gun clicks.
* The cursor is mirrored, because the camera shows you mirrored - moving your
  hand right must move the reticle right.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

import numpy as np

# ── Landmark indices (MediaPipe hand model) ────────────────────────────────
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

FINGER_TIPS = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
FINGER_PIPS = (THUMB_IP, INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP)
FINGER_MCPS = (THUMB_MCP, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)

# Gesture names used across the codebase.
G_NONE = "none"
G_POINT = "point"          # index + middle up, ring + pinky down  (2-finger aim)
G_POINT1 = "point1"        # index only (also an aim pointer)
G_PINCH = "pinch"          # thumb + index tips together (grab / click)
G_FIST = "fist"            # everything curled (depth push / pull)
G_PALM = "palm"            # open hand (release / reset)
G_PEACE = "peace"          # index + middle + ring + pinky up
G_THUMB_UP = "thumb_up"
G_THUMB_DOWN = "thumb_down"
G_THREE = "three"

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "hand_landmarker.task",
)


# ── Smoothing ──────────────────────────────────────────────────────────────
class OneEuroFilter:
    """Adaptive low-pass filter (Casiez et al.).

    Small smoothing while the hand is still, almost none while it moves - the
    difference between "jittery dot" and "glass cursor".
    """

    def __init__(self, min_cutoff: float = 1.4, beta: float = 0.02,
                 d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x: Optional[float] = None
        self._dx: float = 0.0
        self._t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def reset(self) -> None:
        self._x = None
        self._dx = 0.0
        self._t = None

    def __call__(self, value: float, t: Optional[float] = None) -> float:
        now = time.perf_counter() if t is None else t
        if self._x is None:
            self._x = float(value)
            self._t = now
            return self._x
        dt = max(now - (self._t or now), 1e-3)
        self._t = now
        dx = (value - self._x) / dt
        ad = self._alpha(self.d_cutoff, dt)
        self._dx = ad * dx + (1 - ad) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * value + (1 - a) * self._x
        return self._x


class SmoothedVec3:
    """One-Euro smoothing for a 3D vector (used for the aim ray)."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.015):
        self._f = [OneEuroFilter(min_cutoff, beta) for _ in range(3)]

    def reset(self) -> None:
        for f in self._f:
            f.reset()

    def __call__(self, v: Iterable[float], t: Optional[float] = None) -> np.ndarray:
        vals = list(v)
        return np.array([self._f[i](vals[i], t) for i in range(min(3, len(vals)))],
                        dtype=np.float64)


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        return np.array([0.0, 0.0, 0.0])
    return np.asarray(v, dtype=np.float64) / n


# ── Per-hand reading ───────────────────────────────────────────────────────
@dataclass
class HandFeatures:
    """Everything the control layer needs to know about one hand this frame."""

    lm: np.ndarray                     # (21,3) image landmarks, normalised
    world: np.ndarray                  # (21,3) metric landmarks
    handedness: str = "Right"
    score: float = 0.0
    extended: tuple = (False, False, False, False, False)  # thumb..pinky
    gesture: str = G_NONE
    pinch: float = 1.0                 # 0 = touching, 1 = far apart
    pinch_active: bool = False
    size: float = 0.0                  # hand span (depth proxy)
    cursor: np.ndarray = field(default_factory=lambda: np.array([0.5, 0.5]))
    aim: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, -1.0]))
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    pinch_mid: np.ndarray = field(default_factory=lambda: np.array([0.5, 0.5]))

    @property
    def pointing(self) -> bool:
        return self.gesture in (G_POINT, G_POINT1)

    @property
    def index_tip(self) -> np.ndarray:
        return self.lm[INDEX_TIP]

    @property
    def middle_tip(self) -> np.ndarray:
        return self.lm[MIDDLE_TIP]


@dataclass
class FrameHands:
    """One camera frame worth of hands."""

    hands: list = field(default_factory=list)
    timestamp: float = 0.0
    frame: Optional[np.ndarray] = None   # BGR frame (raw, not mirrored)


# ── Gesture engine ─────────────────────────────────────────────────────────
class GestureEngine:
    """Turns raw landmarks into gestures, a cursor and an aim ray."""

    def __init__(self, cursor_gain: float = 1.28, cursor_curve: float = 1.35,
                 pinch_on: float = 0.32, pinch_off: float = 0.46,
                 smoothing: bool = True):
        self.cursor_gain = cursor_gain
        self.cursor_curve = cursor_curve
        self.pinch_on = pinch_on
        self.pinch_off = pinch_off
        self.smoothing = smoothing

        self._filt_x = OneEuroFilter(min_cutoff=1.5, beta=0.012)
        self._filt_y = OneEuroFilter(min_cutoff=1.5, beta=0.012)
        self._aim_filt = SmoothedVec3(min_cutoff=1.1, beta=0.02)
        self._pinch_latched = False
        self._prev_cursor = np.array([0.5, 0.5])
        self._prev_time = 0.0
        self.velocity = np.array([0.0, 0.0])

    # -- geometry helpers ---------------------------------------------------
    def _hand_size(self, lm: np.ndarray) -> float:
        """Wrist -> middle MCP distance (scale reference)."""
        return max(_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-5)

    def _finger_extended(self, lm: np.ndarray, tip: int, pip: int,
                         mcp: int, size: float) -> bool:
        """A finger is extended when tip is farther from the wrist than PIP."""
        d_tip = _dist(lm[tip], lm[WRIST])
        d_pip = _dist(lm[pip], lm[WRIST])
        return d_tip > d_pip * 1.06 and d_tip > size * 0.55

    def _thumb_extended(self, lm: np.ndarray, size: float) -> bool:
        """Thumb sticks out sideways -> away from the pinky knuckle."""
        d_out = _dist(lm[THUMB_TIP], lm[PINKY_MCP])
        d_in = _dist(lm[THUMB_TIP], lm[INDEX_MCP])
        return d_out > d_in * 1.12

    def _pinch_distance(self, lm: np.ndarray, size: float) -> float:
        return _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / size

    def _hand_basis(self, lm: np.ndarray) -> tuple:
        """Return (forward, right, up) unit vectors of the hand in camera space."""
        forward = _norm(lm[MIDDLE_MCP] - lm[WRIST])
        across = lm[INDEX_MCP] - lm[PINKY_MCP]
        right = _norm(across)
        up = _norm(np.cross(right, forward))
        return forward, right, up

    # -- main entry ----------------------------------------------------------
    def analyze(self, lm: np.ndarray, world: Optional[np.ndarray] = None,
                handedness: str = "Right", score: float = 0.0,
                timestamp: Optional[float] = None) -> HandFeatures:
        lm = np.asarray(lm, dtype=np.float64)
        if lm.shape != (21, 3):
            raise ValueError(f"expected 21x3 landmarks, got {lm.shape}")
        world = np.asarray(world, dtype=np.float64) if world is not None else lm.copy()
        now = time.perf_counter() if timestamp is None else timestamp

        size = self._hand_size(lm)
        ext = (
            self._thumb_extended(lm, size),
            self._finger_extended(lm, INDEX_TIP, INDEX_PIP, INDEX_MCP, size),
            self._finger_extended(lm, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP, size),
            self._finger_extended(lm, RING_TIP, RING_PIP, RING_MCP, size),
            self._finger_extended(lm, PINKY_TIP, PINKY_PIP, PINKY_MCP, size),
        )
        pinch = self._pinch_distance(lm, size)

        # pinch hysteresis
        if self._pinch_latched:
            if pinch > self.pinch_off:
                self._pinch_latched = False
        else:
            if pinch < self.pinch_on:
                self._pinch_latched = True

        forward, right, up = self._hand_basis(lm)
        index_dir = _norm(lm[INDEX_TIP] - lm[INDEX_PIP])
        aim = _norm(index_dir * 0.68 + forward * 0.32)
        if self.smoothing:
            aim = self._aim_filt(aim, now)

        yaw = math.atan2(forward[0], -forward[2]) if abs(forward[2]) > 1e-6 else 0.0
        pitch = math.asin(max(-1.0, min(1.0, forward[1])))
        roll = math.atan2(index_dir[1], index_dir[0]) if (index_dir[0] or index_dir[1]) else 0.0

        cursor = self._cursor(lm, now)
        pinch_mid = (lm[THUMB_TIP] + lm[INDEX_TIP]) * 0.5

        feat = HandFeatures(
            lm=lm, world=world, handedness=handedness, score=score,
            extended=ext, pinch=pinch, pinch_active=self._pinch_latched,
            size=size, cursor=cursor, aim=aim, yaw=yaw, pitch=pitch, roll=roll,
            pinch_mid=pinch_mid,
        )
        feat.gesture = self._classify(feat)
        return feat

    def _cursor(self, lm: np.ndarray, now: float) -> np.ndarray:
        """Map the pointing hand to a mirrored screen position in 0..1."""
        mcp = lm[INDEX_MCP]
        pip = lm[INDEX_PIP]
        tip = lm[INDEX_TIP]
        # Blend knuckle and fingertip: the knuckle gives authority, the tip
        # gives precision, the blend keeps both stable.
        base = mcp[:2] * 0.55 + pip[:2] * 0.20 + tip[:2] * 0.25
        x = 1.0 - float(base[0])       # mirror: camera is mirrored for you
        y = float(base[1])

        # Shaped gain so the full screen is reachable without contorting.
        ux, uy = x - 0.5, y - 0.5
        ux = math.copysign(abs(ux) ** self.cursor_curve, ux) * self.cursor_gain
        uy = math.copysign(abs(uy) ** self.cursor_curve, uy) * self.cursor_gain
        x = min(1.0, max(0.0, 0.5 + ux))
        y = min(1.0, max(0.0, 0.5 + uy))

        if self.smoothing:
            x = self._filt_x(x, now)
            y = self._filt_y(y, now)
        cur = np.array([x, y])
        dt = max(now - self._prev_time, 1e-3) if self._prev_time else 1.0
        self.velocity = (cur - self._prev_cursor) / dt
        self._prev_cursor = cur.copy()
        self._prev_time = now
        return cur

    def _classify(self, f: HandFeatures) -> str:
        thumb, index, middle, ring, pinky = f.extended
        # Pinch wins over everything (it can happen while pointing).
        if f.pinch_active:
            return G_PINCH
        if not (index or middle or ring or pinky):
            if thumb:
                # Thumb up / down (compare tip height against the knuckles).
                if f.lm[THUMB_TIP][1] < f.lm[PINKY_MCP][1] - 0.02:
                    return G_THUMB_UP
                if f.lm[THUMB_TIP][1] > f.lm[PINKY_MCP][1] + 0.02:
                    return G_THUMB_DOWN
            return G_FIST
        if index and middle and ring and pinky:
            return G_PALM
        if index and middle and not ring and not pinky:
            return G_POINT
        if index and middle and ring and not pinky:
            return G_PEACE
        if index and middle and ring and pinky:
            return G_PALM
        if index and not middle and not ring and not pinky:
            return G_POINT1
        if index and middle and not ring and pinky:
            return G_POINT
        if middle and ring and pinky and not index:
            return G_THREE
        if index or middle:
            return G_POINT1
        return G_NONE


# ── Swipe detection ────────────────────────────────────────────────────────
class SwipeTracker:
    """Detects fast directional flicks of the cursor."""

    def __init__(self, min_speed: float = 1.6, min_distance: float = 0.14,
                 cooldown: float = 0.7):
        self.min_speed = min_speed
        self.min_distance = min_distance
        self.cooldown = cooldown
        self._last: Optional[tuple] = None   # (dir, time)
        self._anchor = None
        self._anchor_time = 0.0

    def reset(self) -> None:
        self._last = None
        self._anchor = None

    def update(self, cursor: np.ndarray, velocity: np.ndarray,
               now: float) -> Optional[str]:
        """Return 'left' | 'right' | 'up' | 'down' when a swipe completes."""
        if self._anchor is None:
            self._anchor = cursor.copy()
            self._anchor_time = now
            return None
        delta = cursor - self._anchor
        dist = float(np.linalg.norm(delta))
        speed = float(np.linalg.norm(velocity))
        if dist < 0.04 and speed < self.min_speed:
            # Hand settled -> re-arm.
            self._anchor = cursor.copy()
            self._anchor_time = now
            return None
        if dist < self.min_distance or speed < self.min_speed:
            if now - self._anchor_time > 1.2:
                self._anchor = cursor.copy()
                self._anchor_time = now
            return None
        if abs(delta[0]) > abs(delta[1]):
            direction = "right" if delta[0] > 0 else "left"
        else:
            direction = "down" if delta[1] > 0 else "up"
        if self._last and self._last[0] == direction and now - self._last[1] < self.cooldown:
            return None
        self._last = (direction, now)
        self._anchor = cursor.copy()
        self._anchor_time = now
        return direction


# ── Camera + MediaPipe ─────────────────────────────────────────────────────
class HandTracker:
    """Camera + MediaPipe hand landmarker. Runs entirely on this machine."""

    def __init__(self, camera_index: int = 0, max_hands: int = 2,
                 model_path: str = MODEL_PATH, width: int = 640, height: int = 480,
                 min_confidence: float = 0.5, engine: Optional[GestureEngine] = None):
        self.camera_index = camera_index
        self.max_hands = max_hands
        self.model_path = model_path
        self.width = width
        self.height = height
        self.min_confidence = min_confidence
        self.engine = engine or GestureEngine()
        self._cap = None
        self._landmarker = None
        self._lock = threading.Lock()
        self._last_frame_ts = -1
        self._fails = 0
        self._last_reopen = 0.0
        self.available = False
        self.error = ""

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> bool:
        """Open camera + model. Returns True when both are ready."""
        import cv2  # local import: keeps AK startable without a camera

        if not os.path.isfile(self.model_path):
            self.error = (f"Hand model missing: {self.model_path} - run "
                          f"'python AK/fetch_models.py' once.")
            return False
        try:
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.vision import (
                HandLandmarker, HandLandmarkerOptions, RunningMode,
            )
        except Exception as exc:  # noqa: BLE001
            self.error = f"mediapipe not available ({exc}). pip install mediapipe"
            return False

        try:
            options = HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=self.model_path),
                running_mode=RunningMode.VIDEO,
                num_hands=self.max_hands,
                min_hand_detection_confidence=self.min_confidence,
                min_hand_presence_confidence=self.min_confidence,
                min_tracking_confidence=self.min_confidence,
            )
            self._landmarker = HandLandmarker.create_from_options(options)
        except Exception as exc:  # noqa: BLE001
            self.error = f"Hand model load failed: {exc}"
            return False

        for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            cap = cv2.VideoCapture(self.camera_index, backend)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, 30)
                # default webcam brightness: a dim room otherwise gives a
                # nearly black preview (the camera's own auto exposure is
                # too slow / too dark for a face cam)
                try:
                    cap.set(cv2.CAP_PROP_BRIGHTNESS, 40)
                except Exception:
                    pass
                self._cap = cap
                break
            cap.release()
        if self._cap is None:
            self.error = f"Camera {self.camera_index} could not be opened."
            return False

        self.available = True
        self.error = ""
        return True

    def _reopen_camera(self) -> bool:
        """Windows cameras die for no reason (the graph breaks, another app
        grabs the device). Release, wait a moment and grab it again."""
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
        time.sleep(0.4)
        for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            cap = cv2.VideoCapture(self.camera_index, backend)
            if cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_BRIGHTNESS, 40)
                except Exception:
                    pass
                self._cap = cap
                return True
            cap.release()
        return False

    def _maybe_reopen(self) -> None:
        if self._fails < 6:
            return
        now = time.monotonic()
        if now - self._last_reopen < 2.5:
            return
        self._last_reopen = now
        self._fails = 0
        self._reopen_camera()

    def stop(self) -> None:
        with self._lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            if self._landmarker is not None:
                try:
                    self._landmarker.close()
                except Exception:
                    pass
                self._landmarker = None
            self.available = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- per frame -----------------------------------------------------------
    def read(self) -> Optional[FrameHands]:
        """Grab one frame and run the landmarker. None when the camera stalls."""
        if self._cap is None or self._landmarker is None:
            return None
        import cv2
        import mediapipe as mp

        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._fails += 1
            self._maybe_reopen()
            return None
        # a perfectly flat picture means the capture graph is dead and only
        # hands out a solid colour frame - never publish that
        try:
            if frame.size and float(frame.std()) < 2.0:
                self._fails += 1
                self._maybe_reopen()
                return None
        except Exception:
            pass
        self._fails = 0
        ts = int(time.monotonic() * 1000)
        if ts <= self._last_frame_ts:      # MediaPipe wants strictly increasing ts
            ts = self._last_frame_ts + 1
        self._last_frame_ts = ts

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(mp_image, ts)

        hands: list = []
        lms = getattr(result, "landmarks", None) or []
        worlds = getattr(result, "world_landmarks", None) or []
        handed = getattr(result, "handedness", None) or []
        for i, lm in enumerate(lms):
            world = worlds[i] if i < len(worlds) else None
            label = "Right"
            score = 0.0
            if i < len(handed) and handed[i]:
                cat = handed[i][0]
                label = getattr(cat, "category_name", None) or getattr(cat, "display_name", "Right")
                score = float(getattr(cat, "score", 0.0) or 0.0)
            try:
                feat = self.engine.analyze(
                    np.asarray(lm, dtype=np.float64),
                    np.asarray(world, dtype=np.float64) if world is not None else None,
                    handedness=label, score=score, timestamp=time.perf_counter(),
                )
            except Exception:
                continue
            hands.append(feat)
        return FrameHands(hands=hands, timestamp=time.perf_counter(), frame=frame)


def draw_hand_skeleton(frame, hands: Iterable[HandFeatures], mirrored: bool = True):
    """Draw the landmark skeleton over a BGR frame (for the camera preview)."""
    import cv2

    out = frame
    if mirrored:
        out = cv2.flip(frame, 1)
    h, w = out.shape[:2]
    for feat in hands:
        lm = feat.lm.copy()
        if mirrored:
            lm[:, 0] = 1.0 - lm[:, 0]
        pts = np.array([[int(x * w), int(y * h)] for x, y in lm[:, :2]], dtype=np.int32)
        colour = (60, 220, 255) if feat.gesture != G_PINCH else (80, 255, 120)
        for a, b in HAND_CONNECTIONS:
            cv2.line(out, tuple(pts[a]), tuple(pts[b]), colour, 1, cv2.LINE_AA)
        for p in pts:
            cv2.circle(out, tuple(p), 3, colour, -1, cv2.LINE_AA)
        tip = pts[INDEX_TIP]
        cv2.circle(out, tuple(tip), 7, (255, 255, 255), 1, cv2.LINE_AA)
    return out
