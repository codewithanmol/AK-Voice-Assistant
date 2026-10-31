"""Live wardrobe + facial-expression + held-object analysis for AK's HUD.

Fully offline, on this machine:
  * MediaPipe ImageSegmenter (selfie multiclass) -> per-pixel semantic map
    (background / hair / body-skin / clothes / accessories) so the garment is
    a real mask, not a guess.
  * MediaPipe FaceLandmarker -> face box + 52 blendshape scores -> a grounded
    expression reading (smile, jaw open, brow furrow, ...).
  * MediaPipe PoseLandmarker -> shoulders / elbows / wrists / hips -> garment
    geometry (sleeves, neckline, hem, layering) and the "held object" spot.
  * MediaPipe ObjectDetector (EfficientDet-Lite0) -> what is in the frame and,
    when a hand position is supplied, what is being HELD.

Everything is returned as plain dataclasses so the HUD can point at things and
AK can speak the details out loud.
"""

from __future__ import annotations

import colorsys
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_MODEL = os.path.join(ROOT, "models", "face_landmarker.task")
SEG_MODEL = os.path.join(ROOT, "models", "selfie_multiclass_256x256.tflite")
POSE_MODEL = os.path.join(ROOT, "models", "pose_landmarker_lite.task")
OBJ_MODEL = os.path.join(ROOT, "models", "efficientdet_lite0.tflite")
PERSON_MODEL = os.path.join(ROOT, "models", "selfie_segmenter.tflite")

SEG_BACKGROUND = 0
SEG_HAIR = 1
SEG_BODY_SKIN = 2
SEG_CLOTHES = 3
SEG_OTHERS = 4

# COCO class names for the EfficientDet-lite0 object detector.
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


# ── Colour naming ──────────────────────────────────────────────────────────
COLOR_NAMES = [
    ("black", 0, 0, 22), ("charcoal", 0, 0, 48), ("dark grey", 0, 0, 92),
    ("grey", 0, 0, 150), ("silver", 0, 0, 205), ("white", 0, 0, 255),
    ("cream", 30, 8, 240), ("beige", 35, 22, 225), ("tan", 28, 38, 205),
    ("brown", 22, 45, 120), ("dark brown", 20, 60, 78), ("maroon", 350, 55, 90),
    ("burgundy", 345, 60, 55), ("red", 0, 72, 185), ("crimson", 350, 78, 165),
    ("pink", 330, 45, 225), ("magenta", 305, 70, 200), ("purple", 280, 45, 160),
    ("violet", 265, 55, 175), ("indigo", 245, 55, 130), ("navy blue", 225, 60, 120),
    ("blue", 215, 70, 195), ("sky blue", 200, 55, 215), ("cyan", 185, 60, 210),
    ("teal", 175, 55, 145), ("turquoise", 170, 60, 185), ("green", 140, 55, 140),
    ("emerald", 150, 60, 130), ("olive", 75, 50, 120), ("mustard", 48, 70, 175),
    ("gold", 45, 75, 195), ("amber", 38, 85, 200), ("orange", 25, 80, 215),
    ("rust", 18, 70, 150),
]


def describe_color(rgb) -> str:
    """Human name for an RGB triple, e.g. (28, 42, 96) -> 'navy blue'."""
    r, g, b = [float(v) / 255.0 for v in rgb[:3]]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue, sat, val = h * 360.0, s * 100.0, v * 100.0
    best, best_d = "grey", 1e9
    for name, h_lo, s_lo, v_lo in COLOR_NAMES:
        dh = min(abs(hue - h_lo), 360 - abs(hue - h_lo))
        d = dh * 1.4 + max(0, s_lo - sat) * 0.6 + max(0, v_lo - val) * 0.5
        if d < best_d:
            best, best_d = name, d
    if val < 22:
        return f"very dark {best}" if s < 45 else f"deep {best}"
    if val > 88 and s < 22:
        return "off-white"
    if val > 78 and s < 30:
        return "light grey"
    return best


def rgb_to_hex(rgb) -> str:
    r, g, b = [int(max(0, min(255, v))) for v in rgb[:3]]
    return f"#{r:02X}{g:02X}{b:02X}"


def _color_distance(a, b) -> float:
    """Perceptual-ish distance between two BGR colours (0 = identical)."""
    try:
        h1 = np.asarray(cv2.cvtColor(np.uint8([[list(a[:3])]]), cv2.COLOR_BGR2HSV),
                       dtype=np.float32)[0][0]
        h2 = np.asarray(cv2.cvtColor(np.uint8([[list(b[:3])]]), cv2.COLOR_BGR2HSV),
                       dtype=np.float32)[0][0]
        dh = min(abs(float(h1[0]) - float(h2[0])), 180 - abs(float(h1[0]) - float(h2[0])))
        ds = abs(float(h1[1]) - float(h2[1]))
        dv = abs(float(h1[2]) - float(h2[2]))
        return dh * 1.4 + ds * 0.6 + dv * 0.5
    except Exception:
        return 0.0


# ── Reports ────────────────────────────────────────────────────────────────
@dataclass
class ObjectReport:
    detected: bool = False
    name: str = ""
    confidence: float = 0.0
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    held: bool = False
    others: list = field(default_factory=list)   # [(name, conf, bbox), ...]


@dataclass
class ClothReport:
    detected: bool = False
    primary: str = ""
    primary_hex: str = ""
    secondary: str = ""
    secondary_hex: str = ""
    coverage: float = 0.0
    garment: str = ""
    pattern: str = "plain"
    neckline: str = ""
    sleeve: str = ""
    layers: str = ""
    style: str = ""
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    swatches: list = field(default_factory=list)   # [(hex, share%), ...]
    bottom: str = ""            # trousers / jeans / skirt / shorts
    contrast: str = ""          # how well the garment separates from the room
    source: str = ""            # "deep" (6-class mask) or "fast" (person mask)


@dataclass
class FaceReport:
    detected: bool = False
    expression: str = "No face"
    confidence: float = 0.0
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    speaking: bool = False
    traits: list = field(default_factory=list)      # [(name, score), ...]
    glasses: bool = False
    glasses_score: float = 0.0
    yaw: float = 0.0            # degrees, + = turned to their right
    pitch: float = 0.0          # degrees, + = chin up
    roll: float = 0.0           # degrees, + = head tilted
    gaze: str = ""              # centre / left / right / up / down
    eyes_open: bool = True
    blink: bool = False


@dataclass
class VisionReport:
    face: FaceReport = field(default_factory=FaceReport)
    cloth: ClothReport = field(default_factory=ClothReport)
    obj: ObjectReport = field(default_factory=ObjectReport)
    narration: str = ""
    details: list = field(default_factory=list)     # bullet lines for the HUD
    ts: float = 0.0
    glare: bool = False        # blown-out window / sunlight in the frame
    glare_pct: float = 0.0     # % of pixels that are pure white
    glare_bbox: tuple = (0.0, 0.0, 0.0, 0.0)

    @property
    def interesting(self) -> bool:
        return self.face.detected or self.cloth.detected or self.obj.detected


# ── Backlight / glare (window with sun rays) ────────────────────────────────
def detect_glare(frame_bgr: np.ndarray) -> tuple:
    """Find a blown-out bright area (window / sun rays).

    Returns ``(pct_of_frame, bbox_or_None)`` where bbox is normalised.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return 0.0, None
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    blown = gray >= 246
    pct = float(blown.mean()) * 100.0
    if pct < 0.35:
        return pct, None
    H, W = frame_bgr.shape[:2]
    mask = (blown.astype(np.uint8)) * 255
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
    best, best_area = None, 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area > best_area:
            best_area, best = area, stats[i]
    if best is None or best_area < 0.004 * H * W:
        return pct, None
    x, y, w, h = (int(best[0]), int(best[1]), int(best[2]), int(best[3]))
    return pct, (x / float(W), y / float(H), w / float(W), h / float(H))


def enhance_backlit(frame_bgr: np.ndarray, glare_pct: float) -> np.ndarray:
    """Tame a blown-out window so the person in front of it stays readable.

    - rolls the highlights off (the window keeps its shape instead of
      turning into a white blob)
    - CLAHE on the L channel brings back the subject's local contrast
    """
    if frame_bgr is None or glare_pct < 0.8:
        return frame_bgr
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lf = l.astype(np.float32)
    # soft highlight roll-off: 255 -> ~205, everything below 200 untouched
    lf = np.where(lf > 200.0, 200.0 + (lf - 200.0) * 0.18, lf)
    l2 = np.clip(lf, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=1.7, tileGridSize=(8, 8))
    l2 = clahe.apply(l2)
    return cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)


# ── Expression mapping ─────────────────────────────────────────────────────
def _expression_from_blendshapes(bs: dict, talking: bool) -> tuple:
    def g(name):
        return float(bs.get(name, 0.0))

    smile = g("mouthSmileLeft") + g("mouthSmileRight")
    frown = g("mouthFrownLeft") + g("mouthFrownRight")
    jaw = g("jawOpen")
    brow_down = g("browDownLeft") + g("browDownRight")
    brow_up = g("browInnerUp") + g("browOuterUpLeft") + g("browOuterUpRight")
    squint = g("eyeSquintLeft") + g("eyeSquintRight")
    pucker = g("mouthPucker")
    press = g("mouthPressLeft") + g("mouthPressRight")
    wide = g("eyeWideLeft") + g("eyeWideRight")

    traits = [
        ("smiling", smile / 2), ("mouth open", jaw), ("brows raised", brow_up / 3),
        ("brows furrowed", brow_down / 2), ("squinting", squint / 2),
        ("eyes wide", wide / 2), ("pursed lips", pucker), ("lips pressed", press / 2),
    ]
    traits = [(n, round(v, 3)) for n, v in traits if v > 0.18]
    traits.sort(key=lambda x: -x[1])

    if smile > 0.45 and squint < 0.35:
        return "Happy", min(1.0, smile / 2 + 0.25), traits
    if jaw > 0.42 and brow_up > 0.28:
        return "Surprised", min(1.0, jaw + brow_up / 2), traits
    if wide > 0.35 and brow_up > 0.25:
        return "Alert", min(1.0, wide / 2 + brow_up / 3), traits
    if brow_down > 0.34 and (press > 0.22 or frown > 0.2):
        return "Annoyed", min(1.0, brow_down / 2 + press / 3), traits
    if frown > 0.3 and brow_up > 0.2:
        return "Worried", min(1.0, frown / 2 + brow_up / 3), traits
    if squint > 0.32:
        return "Focused", min(1.0, squint / 2 + 0.2), traits
    if smile > 0.2:
        return "Slight smile", min(1.0, smile / 2 + 0.15), traits
    if talking:
        return "Talking", 0.6, traits
    return "Neutral", 0.45, traits


# ── Face geometry: head pose, gaze, glasses ────────────────────────────────
# MediaPipe canonical face mesh indices we lean on.
LM = {
    "l_eye_outer": 33, "l_eye_inner": 133, "l_eye_top": 159, "l_eye_bottom": 145,
    "r_eye_inner": 362, "r_eye_outer": 263, "r_eye_top": 386, "r_eye_bottom": 374,
    "nose_bridge": 168, "chin": 152, "forehead": 10,
    "l_iris": 468, "r_iris": 473,
}


def _head_pose(matrix) -> tuple:
    """Yaw / pitch / roll in degrees from the facial transformation matrix."""
    if matrix is None:
        return (0.0, 0.0, 0.0)
    try:
        m = np.asarray(matrix, dtype=np.float64)
        if m.shape == (4, 4):
            m = m[:3, :3]
        if m.shape != (3, 3):
            return (0.0, 0.0, 0.0)
        sy = math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
        if sy > 1e-6:
            pitch = math.atan2(m[2, 1], m[2, 2])
            yaw = math.atan2(-m[2, 0], sy)
            roll = math.atan2(m[1, 0], m[0, 0])
        else:
            pitch = math.atan2(-m[1, 2], m[1, 1])
            yaw = math.atan2(-m[2, 0], sy)
            roll = 0.0
        return (round(math.degrees(yaw), 1), round(math.degrees(pitch), 1),
                round(math.degrees(roll), 1))
    except Exception:
        return (0.0, 0.0, 0.0)


def _gaze_direction(lms) -> str:
    """Where the eyes are pointing, from the iris inside each eye socket."""
    try:
        n = len(lms)
        if n < 478:
            return ""
        lo, li = lms[LM["l_eye_outer"]], lms[LM["l_eye_inner"]]
        ro, ri = lms[LM["r_eye_outer"]], lms[LM["r_eye_inner"]]
        li_c, ri_c = lms[LM["l_iris"]], lms[LM["r_iris"]]
        lw = abs(lo.x - li.x) or 1e-4
        rw = abs(ro.x - ri.x) or 1e-4
        lh = abs(lms[LM["l_eye_top"]].y - lms[LM["l_eye_bottom"]].y) or 1e-4
        rh = abs(lms[LM["r_eye_top"]].y - lms[LM["r_eye_bottom"]].y) or 1e-4
        # +x means the iris drifted towards the nose: for the subject's LEFT
        # eye that is looking right, for the RIGHT eye it is looking left.
        d_left = (li_c.x - (lo.x + li.x) / 2.0) / lw
        d_right = (ri_c.x - (ro.x + ri.x) / 2.0) / rw
        gx = (d_left - d_right) / 2.0
        gy = -(((li_c.y - (lms[LM["l_eye_top"]].y + lms[LM["l_eye_bottom"]].y) / 2.0) / lh)
               + ((ri_c.y - (lms[LM["r_eye_top"]].y + lms[LM["r_eye_bottom"]].y) / 2.0) / rh)) / 2.0
        if abs(gx) > 0.22 and abs(gx) >= abs(gy) * 1.15:
            return "right" if gx > 0 else "left"
        if abs(gy) > 0.16:
            return "up" if gy > 0 else "down"
        return "centre"
    except Exception:
        return ""


def _longest_run(row: np.ndarray) -> int:
    """Length of the longest continuous True run in a 1-D bool row."""
    if not row.any():
        return 0
    edges = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    if len(starts) == 0:
        return 0
    return int((ends - starts).max())


def _glasses_score(frame_bgr: np.ndarray, lms) -> float:
    """0..1 confidence that the face wears glasses.

    Eyebrows sit above the eyes and stop at the nose bridge; a frame rim
    crosses it. So the eye band is scanned for horizontal edges that run
    unbroken across the bridge - two of them (top + bottom rim) means a
    frame. Dark lenses get an extra push.
    """
    try:
        H, W = frame_bgr.shape[:2]
        lo = lms[LM["l_eye_outer"]]
        ro = lms[LM["r_eye_outer"]]
        lt = lms[LM["l_eye_top"]]
        rb = lms[LM["r_eye_bottom"]]
        rt = lms[LM["r_eye_top"]]
        rb2 = lms[LM["r_eye_bottom"]]
        x0 = min(lo.x, ro.x) * W
        x1 = max(lo.x, ro.x) * W
        y0 = min(lt.y, rt.y) * H
        y1 = max(rb.y, rb2.y) * H
        eye_h = max(8.0, y1 - y0)
        pad_x = (x1 - x0) * 0.10
        pad_y = eye_h * 0.75
        xi0 = max(0, int(x0 - pad_x))
        xi1 = min(W, int(x1 + pad_x))
        yi0 = max(0, int(y0 - pad_y))
        yi1 = min(H, int(y1 + pad_y))
        if xi1 - xi0 < 20 or yi1 - yi0 < 12:
            return 0.0
        crop = frame_bgr[yi0:yi1, xi0:xi1]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(gray, 45, 130)
        if edges.sum() < 300:
            return 0.0
        h, w = edges.shape
        long_h = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 7), 1))
        hlines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, long_h) > 0
        bridge_x = lms[LM["nose_bridge"]].x * W - xi0
        if not (0 <= bridge_x < w):
            bridge_x = w / 2.0
        rim_rows = []
        for y in range(h):
            row = hlines[y]
            if _longest_run(row) < w * 0.40:
                continue
            idx = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
            starts, ends = idx[0::2], idx[1::2]
            crosses = np.any((starts <= bridge_x) & (ends >= bridge_x))
            if crosses:
                rim_rows.append(y)
        if len(rim_rows) < 2:
            return 0.0
        groups = []
        for y in rim_rows:
            if groups and y - groups[-1][-1] <= 2:
                groups[-1].append(y)
            else:
                groups.append([y])
        pair = 0
        for i in range(len(groups) - 1):
            gap = groups[i + 1][0] - groups[i][-1]
            if gap >= eye_h * 0.22:
                pair = 1
                break
        if not pair:
            return 0.0
        score = 0.55
        # dark lens between the rims -> sunglasses
        if len(groups) >= 2:
            ya, yb = groups[-2][-1], groups[-1][0]
            if yb - ya > 3:
                inner = gray[ya:yb, :]
                outer = gray[:max(1, ya), :]
                if inner.size and outer.size:
                    if float(inner.mean()) < float(outer.mean()) * 0.72:
                        score += 0.35
        return round(min(1.0, score), 2)
    except Exception:
        return 0.0


# ── Analyzer ───────────────────────────────────────────────────────────────
class VisionAnalyzer:
    """Clothes + expression + held-object analysis from a webcam frame."""

    def __init__(self, face_model: str = FACE_MODEL, seg_model: str = SEG_MODEL,
                 pose_model: str = POSE_MODEL, obj_model: str = OBJ_MODEL,
                 person_model: str = PERSON_MODEL,
                 object_threshold: float = 0.4):
        self.face_model = face_model
        self.seg_model = seg_model
        self.pose_model = pose_model
        self.obj_model = obj_model
        self.person_model = person_model
        self.object_threshold = object_threshold
        self._face = None
        self._seg = None
        self._pose = None
        self._obj = None
        self._person_seg = None
        self._mp = None
        self.error = ""
        self._jaw_history: list = []
        self._blink_state: Optional[float] = None
        self.last_report = VisionReport()
        # Round-robin work queue: the models take turns so the HUD stays
        # responsive instead of freezing for half a second every frame.
        self._queue = ["face", "person", "pose", "object", "cloth"]
        self._state: dict = {}
        self._hand_point = None
        self._frame_size = (480, 640)
        self._cycle = 0
        self._job_ms = {}
        # The 6-class segmentation model is accurate but slow (~280 ms), so it
        # only refreshes every few seconds - clothes do not change fast.
        self._deep_every = 20
        self._deep_ticks = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> bool:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            FaceLandmarker, FaceLandmarkerOptions, ImageSegmenter,
            ImageSegmenterOptions, ObjectDetector, ObjectDetectorOptions,
            PoseLandmarker, PoseLandmarkerOptions, RunningMode,
        )

        self._mp = mp
        try:
            self._face = FaceLandmarker.create_from_options(
                FaceLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=self.face_model),
                    running_mode=RunningMode.VIDEO,
                    num_faces=1,
                    output_face_blendshapes=True,
                    output_facial_transformation_matrixes=True,
                    min_face_detection_confidence=0.4,
                    min_tracking_confidence=0.4,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.error = f"face model failed: {exc}"
            return False

        try:
            self._seg = ImageSegmenter.create_from_options(
                ImageSegmenterOptions(
                    base_options=BaseOptions(model_asset_path=self.seg_model),
                    running_mode=RunningMode.VIDEO,
                    output_category_mask=True,
                    output_confidence_masks=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.error = f"segmenter failed: {exc}"
            self._seg = None

        # Fast binary person mask - used every frame for the live pointer.
        try:
            self._person_seg = ImageSegmenter.create_from_options(
                ImageSegmenterOptions(
                    base_options=BaseOptions(model_asset_path=self.person_model),
                    running_mode=RunningMode.VIDEO,
                    output_category_mask=True,
                    output_confidence_masks=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.error = f"person segmenter failed: {exc}"
            self._person_seg = None
            return False

        try:
            self._pose = PoseLandmarker.create_from_options(
                PoseLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=self.pose_model),
                    running_mode=RunningMode.VIDEO,
                    num_poses=1,
                    min_pose_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.error = f"pose model failed: {exc}"
            self._pose = None

        try:
            self._obj = ObjectDetector.create_from_options(
                ObjectDetectorOptions(
                    base_options=BaseOptions(model_asset_path=self.obj_model),
                    running_mode=RunningMode.VIDEO,
                    score_threshold=self.object_threshold,
                    max_results=10,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.error = f"object model failed: {exc}"
            self._obj = None

        self.error = ""
        return True

    def stop(self) -> None:
        for attr in ("_face", "_seg", "_pose", "_obj"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _dominant_colors(mask_bool: np.ndarray, frame: np.ndarray,
                         k: int = 4) -> list:
        px = frame[mask_bool]
        if len(px) < 40:
            return []
        if len(px) > 24000:
            idx = np.linspace(0, len(px) - 1, 24000).astype(np.int32)
            px = px[idx]
        # a flat integer key is ~8x faster than np.unique(axis=0) and this
        # runs on every camera frame
        q = (px.astype(np.int32) // 24 * 24)
        flat = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
        keys, counts = np.unique(flat, return_counts=True)
        order = np.argsort(-counts)[:k]
        total = float(counts.sum())
        out = []
        for i in order:
            f = int(keys[i])
            key = np.array([(f >> 16) & 255, (f >> 8) & 255, f & 255], np.int32)
            near = np.abs(px.astype(np.int32) - key).sum(axis=1) < 60
            refined = px[near].mean(axis=0) if near.any() else key.astype(np.float64)
            out.append((refined, float(counts[i]) / total))
        return out

    @staticmethod
    def _edge_density(mask_bool: np.ndarray, frame: np.ndarray) -> float:
        import cv2
        ys, xs = np.where(mask_bool)
        if len(ys) < 200:
            return 0.0
        crop = frame[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]
        if crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 160)
        return float((edges > 0).mean())

    @staticmethod
    def _band_width(mask_bool: np.ndarray, y0: int, y1: int) -> float:
        H = mask_bool.shape[0]
        a = max(0, min(H - 1, y0))
        b = max(a + 1, min(H, y1))
        band = mask_bool[a:b]
        if not band.any():
            return 0.0
        cols = np.where(band.any(axis=0))[0]
        return float(cols.max() - cols.min() + 1) / mask_bool.shape[1]

    @staticmethod
    def _vertical_bands(mask_bool: np.ndarray, frame: np.ndarray,
                        bands: int = 4) -> list:
        """Dominant colour per horizontal band -> layering detection."""
        ys = np.where(mask_bool.any(axis=1))[0]
        if len(ys) < 20:
            return []
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        step = max(4, (y1 - y0) // bands)
        out = []
        for b in range(bands):
            a = y0 + b * step
            c = min(y1, a + step)
            if c <= a:
                continue
            band_mask = np.zeros_like(mask_bool)
            band_mask[a:c] = mask_bool[a:c]
            if band_mask.sum() < 200:
                continue
            cols = VisionAnalyzer._dominant_colors(band_mask, frame, k=1)
            if cols:
                out.append((cols[0][0], cols[0][1]))
        return out

    # -- main ---------------------------------------------------------------
    def analyze(self, frame_bgr: np.ndarray, timestamp_ms: int = 0,
                hand_point: Optional[tuple] = None,
                budget: float = 0.11) -> VisionReport:
        """Analyse one frame WITHOUT melting the CPU.

        Four vision models (face, clothes-segmentation, pose, objects) cost
        ~450 ms when run back to back, which would freeze the HUD. So they
        run round-robin under a time budget: each model refreshes a few
        times per second, the total stays responsive, and the report is
        assembled from the freshest result of every model.
        """
        mp = self._mp
        if self._face is None:
            report = VisionReport(ts=timestamp_ms / 1000.0)
            report.narration = "Vision models are not loaded."
            return report

        glare_pct, glare_bbox = detect_glare(frame_bgr)
        # a window with sun rays blows the exposure out -> compensate before
        # the models look at the frame, otherwise the person is a dark blob
        frame_bgr = enhance_backlit(frame_bgr, glare_pct)
        self._glare = (glare_pct, glare_bbox)

        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        H, W = frame_bgr.shape[:2]
        self._frame_size = (H, W)
        self._hand_point = hand_point
        self._state["last_frame"] = frame_bgr

        # round-robin jobs under a wall-clock budget
        t_start = time.perf_counter()
        did = []
        while True:
            job = self._queue.pop(0)
            self._queue.append(job)
            t_job = time.perf_counter()
            try:
                self._run_job(job, mp_image, frame_bgr, timestamp_ms, H, W)
            except Exception as exc:  # keep the HUD alive, but do not hide it
                self.error = f"{job}: {exc}"
            self._job_ms[job] = round((time.perf_counter() - t_job) * 1000, 1)
            did.append(job)
            self._cycle += 1
            # at most two models per call - the queue is long, running all of
            # them in one go would freeze everything for half a second
            if time.perf_counter() - t_start >= budget or len(did) >= 2:
                break

        report = self._assemble(timestamp_ms)
        self.last_report = report
        return report

    def _run_job(self, job: str, mp_image, frame_bgr, timestamp_ms: int,
                 H: int, W: int) -> None:
        if job == "face":
            self._job_face(mp_image, timestamp_ms, frame_bgr)
        elif job == "person":
            self._job_person(mp_image, timestamp_ms, H, W)
        elif job == "pose":
            self._job_pose(mp_image, timestamp_ms)
        elif job == "object":
            self._job_object(mp_image, timestamp_ms, H, W)
        elif job == "cloth":
            # deep 6-class scan - slow, so it only runs every few cycles
            self._job_cloth(mp_image, frame_bgr, timestamp_ms, H, W)

    def _job_person(self, mp_image, timestamp_ms: int, H: int, W: int) -> None:
        """Fast binary person mask -> live clothing silhouette."""
        if self._person_seg is None:
            return
        # the silhouette only needs to be roughly right - every other cycle
        # is plenty and keeps the segmentation cost down
        if self._cycle % 2 != 0:
            return
        sres = self._person_seg.segment_for_video(mp_image, timestamp_ms)
        cat_mask = getattr(sres, "category_mask", None)
        if cat_mask is None:
            return
        mask = cat_mask.numpy_view() if hasattr(cat_mask, "numpy_view") \
            else np.asarray(cat_mask)
        mask = np.asarray(mask).astype(np.int32)
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        if mask.shape[:2] != (H, W):
            import cv2
            mask = cv2.resize(mask.astype(np.uint8), (W, H),
                              interpolation=cv2.INTER_NEAREST).astype(np.int32)
        person = mask > 127
        self._state["person"] = person

        # Fast clothing region: the torso polygon from pose, clipped to the
        # person mask. Good enough to aim the HUD pointer at.
        pose = self._state.get("pose")
        region = None
        if pose is not None:
            torso = self._torso_polygon(pose, H, W)
            if torso is not None:
                region = torso & person
        if region is None:
            region = person
        if region.sum() > 900:
            rep = ClothReport()
            frame = self._state.get("last_frame")
            if frame is None:
                frame = np.zeros((H, W, 3), np.uint8)
            self._fill_cloth(rep, frame, region, None, pose,
                             self._state.get("face"), source="fast", quick=True)
            self._state["cloth_fast"] = rep

    # -- individual jobs -----------------------------------------------------
    def _job_face(self, mp_image, timestamp_ms: int, frame_bgr=None) -> None:
        fres = self._face.detect_for_video(mp_image, timestamp_ms)
        if fres is None or not getattr(fres, "face_landmarks", None):
            self._state["face"] = FaceReport()
            return
        lms = fres.face_landmarks[0]
        xs = [p.x for p in lms]
        ys = [p.y for p in lms]
        rep = FaceReport()
        rep.detected = True
        rep.bbox = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        bs = {}
        blends = getattr(fres, "face_blendshapes", None)
        if blends:
            for cat in blends[0]:
                bs[cat.category_name] = float(cat.score)
        jaw = float(bs.get("jawOpen", 0.0))
        self._jaw_history.append(jaw)
        if len(self._jaw_history) > 24:
            self._jaw_history.pop(0)
        talking = False
        if len(self._jaw_history) >= 8:
            arr = np.asarray(self._jaw_history[-12:])
            talking = float(arr.std()) > 0.07 and float(arr.max()) > 0.22
        label, conf, traits = _expression_from_blendshapes(bs, talking)
        rep.expression = label
        rep.confidence = round(float(conf), 2)
        rep.traits = traits
        rep.speaking = talking

        # eyes + blink
        blink = max(float(bs.get("eyeBlinkLeft", 0.0)),
                    float(bs.get("eyeBlinkRight", 0.0)))
        rep.eyes_open = blink < 0.55
        was = self._blink_state
        self._blink_state = blink
        rep.blink = bool(was is not None and was < 0.45 and blink >= 0.55)

        # head pose + gaze
        mats = getattr(fres, "facial_transformation_matrixes", None)
        matrix = None
        if mats:
            try:
                matrix = np.asarray(mats[0])
            except Exception:
                matrix = None
        yaw, pitch, roll = _head_pose(matrix)
        rep.yaw, rep.pitch, rep.roll = yaw, pitch, roll
        rep.gaze = _gaze_direction(lms)

        # glasses
        if frame_bgr is not None:
            rep.glasses_score = _glasses_score(frame_bgr, lms)
            rep.glasses = rep.glasses_score >= 0.5
        self._state["face"] = rep

    @staticmethod
    def _torso_polygon(pose, H: int, W: int):
        """Rough torso mask from pose landmarks (shoulders -> hips)."""
        import cv2
        try:
            ls, rs = pose[11], pose[12]
            lh, rh = pose[23], pose[24]
            if min(getattr(ls, "visibility", 1.0), getattr(rs, "visibility", 1.0),
                   getattr(lh, "visibility", 1.0), getattr(rh, "visibility", 1.0)) < 0.4:
                return None
            lx, ly = ls.x * W, ls.y * H
            rx, ry = rs.x * W, rs.y * H
            hx, hy = lh.x * W, lh.y * H
            h2x, h2y = rh.x * W, rh.y * H
            shoulder_y = (ly + ry) / 2
            hip_y = max(hy, h2y)
            if hip_y - shoulder_y < H * 0.04:
                return None
            pad = W * 0.03
            pts = np.array([
                [min(lx, rx) - pad, shoulder_y - H * 0.01],
                [max(lx, rx) + pad, shoulder_y - H * 0.01],
                [max(hx, h2x) + pad, hip_y + H * 0.04],
                [min(hx, h2x) - pad, hip_y + H * 0.04],
            ], dtype=np.int32)
            mask = np.zeros((H, W), dtype=np.uint8)
            cv2.fillPoly(mask, [pts], 255)
            return mask > 127
        except Exception:
            return None

    def _job_cloth(self, mp_image, frame_bgr, timestamp_ms: int, H: int, W: int) -> None:
        if self._seg is None:
            return
        # The deep 6-class scan costs ~280 ms, so it only runs every few
        # opportunities - clothes do not change fast. The counter is on the
        # job itself (a modulo on the global cycle would skip it forever).
        self._deep_ticks += 1
        if self._deep_ticks % max(1, self._deep_every) != 0:
            return
        sres = self._seg.segment_for_video(mp_image, timestamp_ms)
        cat_mask = getattr(sres, "category_mask", None)
        if cat_mask is None:
            return
        mask = cat_mask.numpy_view() if hasattr(cat_mask, "numpy_view") else np.asarray(cat_mask)
        mask = np.asarray(mask).astype(np.int32)
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        if mask.shape[:2] != (H, W):
            import cv2
            mask = cv2.resize(mask.astype(np.uint8), (W, H),
                              interpolation=cv2.INTER_NEAREST).astype(np.int32)
        cloth = (mask == SEG_CLOTHES)
        skin = (mask == SEG_BODY_SKIN)
        self._state["cloth_mask"] = cloth
        self._state["skin_mask"] = skin
        if cloth.sum() > 900:
            rep = ClothReport()
            self._fill_cloth(rep, frame_bgr, cloth, skin, self._state.get("pose"),
                             self._state.get("face"), source="deep")
            self._state["cloth"] = rep

    def _job_pose(self, mp_image, timestamp_ms: int) -> None:
        if self._pose is None:
            return
        # body pose changes slowly - no need to run it on every cycle
        if self._cycle % 3 != 0:
            return
        pres = self._pose.detect_for_video(mp_image, timestamp_ms)
        if pres is not None and getattr(pres, "pose_landmarks", None):
            self._state["pose"] = pres.pose_landmarks[0]
            # re-derive garment geometry now that we know the body
            cloth = self._state.get("cloth_mask")
            if cloth is not None and cloth.sum() > 900:
                rep = ClothReport()
                self._fill_cloth(rep, self._state["last_frame"], cloth,
                                 self._state.get("skin_mask"), self._state["pose"],
                                 self._state.get("face"), source="deep")
                self._state["cloth"] = rep
            person = self._state.get("person")
            frame = self._state.get("last_frame")
            if person is not None and frame is not None:
                region = self._torso_polygon(self._state["pose"], *self._frame_size)
                if region is not None:
                    region = region & person
                if region is not None and region.sum() > 900:
                    rep = ClothReport()
                    self._fill_cloth(rep, frame, region, None, self._state["pose"],
                                     self._state.get("face"), source="fast")
                    self._state["cloth_fast"] = rep

    def _job_object(self, mp_image, timestamp_ms: int, H: int, W: int) -> None:
        if self._obj is None:
            return
        # the detector is the most expensive model - it only needs to know
        # what is on the table, so it runs a few times less often
        if self._cycle % 10 != 0:
            return
        ores = self._obj.detect_for_video(mp_image, timestamp_ms)
        dets = getattr(ores, "detections", None) or []
        cands = []
        for d in dets:
            bb = d.bounding_box
            cats = getattr(d, "categories", None)
            if cats:
                cat = cats[0]
                name = getattr(cat, "category_name", "") or ""
                score = float(getattr(cat, "score", 0.0) or 0.0)
                if not name:
                    try:
                        name = COCO_LABELS[int(cat.index)]
                    except Exception:
                        name = "object"
            else:
                name, score = "object", 0.0
            x, y = bb.origin_x / W, bb.origin_y / H
            w, h = bb.width / W, bb.height / H
            if name == "person":
                continue
            cands.append((name, score, (x, y, w, h)))
        cands.sort(key=lambda c: -c[1])
        rep = ObjectReport()
        rep.others = [(n, round(s, 3), b) for n, s, b in cands[:5]]
        if cands:
            name, score, bbox = cands[0]
            rep.detected = True
            rep.name = name
            rep.confidence = round(score, 3)
            rep.bbox = bbox
            hand = self._hand_point
            if hand is not None:
                cx, cy = bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2
                d = ((cx - hand[0]) ** 2 + (cy - hand[1]) ** 2) ** 0.5
                inside = (bbox[0] - 0.07 <= hand[0] <= bbox[0] + bbox[2] + 0.07
                          and bbox[1] - 0.07 <= hand[1] <= bbox[1] + bbox[3] + 0.07)
                if inside or d < 0.22:
                    rep.held = True
        self._state["object"] = rep

    # -- assembly ------------------------------------------------------------
    def _assemble(self, timestamp_ms: int) -> VisionReport:
        report = VisionReport(ts=timestamp_ms / 1000.0)
        report.face = self._state.get("face", FaceReport())
        report.cloth = (self._state.get("cloth")
                        or self._state.get("cloth_fast")
                        or ClothReport())
        report.obj = self._state.get("object", ObjectReport())
        glare_pct, glare_bbox = getattr(self, "_glare", (0.0, None))
        report.glare_pct = round(glare_pct, 2)
        report.glare = bool(glare_bbox) and glare_pct >= 0.8
        if report.glare:
            report.glare_bbox = glare_bbox
        report.details = self._details(report)
        report.narration = self._narrate(report)
        return report

    # -- clothes -------------------------------------------------------------
    def _fill_cloth(self, rep: ClothReport, frame: np.ndarray,
                    cloth: np.ndarray, skin: Optional[np.ndarray],
                    pose_pts, face_rep: Optional[FaceReport] = None,
                    source: str = "", quick: bool = False) -> None:
        H, W = cloth.shape
        ys, xs = np.where(cloth)
        if len(ys) == 0:
            return
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        rep.bbox = (x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H)
        rep.coverage = round(float(cloth.mean()) * 100, 1)
        rep.source = source
        rep.detected = True

        cols = self._dominant_colors(cloth, frame, k=4)
        if cols:
            rgb1, share1 = cols[0]
            rep.primary = describe_color(rgb1[::-1])
            rep.primary_hex = rgb_to_hex(rgb1[::-1])
            rep.swatches = [(rgb_to_hex(c[::-1]), round(s * 100, 1))
                            for c, s in cols]
            if len(cols) > 1 and cols[1][1] > 0.16:
                rgb2 = cols[1][0]
                rep.secondary = describe_color(rgb2[::-1])
                rep.secondary_hex = rgb_to_hex(rgb2[::-1])

        # how well the garment separates from the room behind it
        bg = ~cloth
        if bg.sum() > 500:
            gy = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            gm = float(gy[cloth].mean()) if cloth.any() else 0.0
            gb = float(gy[bg].mean())
            rep.contrast = "high" if abs(gm - gb) > 48 else "low"

        density = self._edge_density(cloth, frame)
        if density > 0.17:
            rep.pattern = "graphic print"
        elif density > 0.07:
            rep.pattern = "patterned"
        else:
            rep.pattern = "plain"

        # vertical colour bands -> layering (shirt over tee, jacket over shirt).
        # the reference is the dominant garment colour, not the topmost band -
        # otherwise plain fabric with a shadow reads as a fake "layer".
        # the band scan is the slowest part, so the quick path skips it.
        if not quick and cols:
            base = cols[0][0]
            bands = self._vertical_bands(cloth, frame, bands=4)
            for col, share in bands[1:]:
                if share < 0.12:
                    continue
                if _color_distance(base, col) > 55:
                    rep.layers = describe_color(col[::-1])
                    break

        face = face_rep
        if face is not None and face.detected:
            fb = face.bbox
            face_bottom = (fb[1] + fb[3]) * H
            cloth_h = (y1 - y0) / H
            if y1 >= H * 0.97 or cloth_h > 0.5:
                rep.garment = "full outfit"
            else:
                rep.garment = "upper garment"

            # neckline
            gap = (y0 - face_bottom) / H
            if gap < 0.015:
                rep.neckline = "high neck"
            elif gap < 0.055:
                rep.neckline = "crew neck"
            else:
                rep.neckline = "open neckline"

            # sleeves from pose + mask
            if pose_pts is not None:
                try:
                    l_sh, r_sh = pose_pts[11], pose_pts[12]
                    l_el, r_el = pose_pts[13], pose_pts[14]
                    l_wr, r_wr = pose_pts[15], pose_pts[16]
                    arm_y = int((l_el.y + r_el.y) * 0.5 * H)
                    band = cloth[max(0, arm_y - int(0.06 * H)):arm_y + int(0.02 * H)]
                    if band.size and band.any():
                        rep.sleeve = "long sleeves"
                    else:
                        w_sh = self._band_width(cloth, int(l_sh.y * H) - 4, int(l_sh.y * H) + 8)
                        w_ch = self._band_width(cloth, arm_y + 6, arm_y + int(0.1 * H))
                        if w_sh > 0 and w_ch > 0 and w_sh > w_ch * 1.2:
                            rep.sleeve = "short sleeves"
                        else:
                            rep.sleeve = "sleeveless"
                except Exception:
                    rep.sleeve = ""
            if not rep.sleeve:
                w_shoulder = self._band_width(cloth, int(face_bottom), int(face_bottom + 0.05 * H))
                w_chest = self._band_width(cloth, int(face_bottom + 0.2 * H),
                                           int(face_bottom + 0.28 * H))
                if w_shoulder > 0 and w_chest > 0:
                    rep.sleeve = ("short sleeves" if w_shoulder > w_chest * 1.2
                                  else "sleeveless")
        else:
            rep.garment = "outfit"

        # bottom garment (trousers / skirt) from the leg area of the mask
        rep.bottom = self._bottom_garment(cloth, pose_pts, H, W)
        if rep.bottom == "trousers" and any(k in rep.primary.lower()
                                            for k in ("blue", "navy", "indigo")):
            rep.bottom = "jeans"

        # style read
        p = rep.primary.lower()
        dark = any(k in p for k in ("black", "navy", "charcoal", "dark", "maroon",
                                    "burgundy", "olive", "brown"))
        formal = any(k in p for k in ("black", "navy", "charcoal", "white", "grey"))
        if rep.pattern == "graphic print":
            rep.style = "streetwear"
        elif dark and formal:
            rep.style = "smart formal"
        elif dark:
            rep.style = "smart casual"
        else:
            rep.style = "casual"

    @staticmethod
    def _bottom_garment(cloth: np.ndarray, pose_pts, H: int, W: int) -> str:
        """Read the leg area: two separate legs = trousers, one block = skirt."""
        if pose_pts is None:
            return ""
        try:
            lk, rk = pose_pts[25], pose_pts[26]      # knees
            la, ra = pose_pts[27], pose_pts[28]      # ankles
            knee_y = int(((lk.y + rk.y) / 2.0) * H)
            if knee_y <= 0 or knee_y >= H:
                return ""
            row = cloth[knee_y]
            if not row.any():
                return ""
            cols = np.flatnonzero(row)
            span = cols.max() - cols.min() + 1
            if span < W * 0.05:
                return ""
            gaps = np.diff(cols)
            middle_gap = int(gaps.max()) if len(gaps) else 0
            if middle_gap > span * 0.16:
                return "trousers"
            return "skirt"
        except Exception:
            return ""

    # -- text ----------------------------------------------------------------
    @staticmethod
    def _details(report: VisionReport) -> list:
        out = []
        c = report.cloth
        if c.detected:
            bits = []
            if c.primary:
                bits.append(c.primary)
            if c.secondary:
                bits.append(f"+ {c.secondary}")
            bits.append(c.garment or "garment")
            if c.sleeve:
                bits.append(c.sleeve)
            if c.neckline:
                bits.append(c.neckline)
            if c.layers:
                bits.append(f"layered over {c.layers}")
            if c.bottom:
                bits.append(c.bottom)
            if c.pattern and c.pattern != "plain":
                bits.append(c.pattern)
            bits.append(c.style)
            out.append("WARDROBE  " + " · ".join(bits))
            sw = "  ".join(f"{h} {s}%" for h, s in c.swatches[:3])
            extra = f"swatches {sw}" if sw else ""
            if c.contrast:
                extra += f"  ·  {c.contrast} contrast"
            out.append(f"coverage {c.coverage}% of frame" + (f"  ·  {extra}" if extra else ""))
        f = report.face
        if f.detected:
            traits = ", ".join(f"{n} {int(v * 100)}%" for n, v in f.traits[:3])
            out.append(f"EXPRESSION  {f.expression} ({int(f.confidence * 100)}%)")
            if traits:
                out.append(f"traits  {traits}")
            look = []
            if f.gaze:
                look.append(f"gaze {f.gaze}")
            if abs(f.yaw) > 6 or abs(f.pitch) > 6 or abs(f.roll) > 8:
                look.append(f"head yaw {f.yaw}° pitch {f.pitch}° roll {f.roll}°")
            if f.glasses:
                look.append(f"glasses ({int(f.glasses_score * 100)}%)")
            if f.speaking:
                look.append("speaking")
            elif not f.eyes_open:
                look.append("eyes closed")
            if look:
                out.append("look  " + "  ·  ".join(look))
        o = report.obj
        if o.detected:
            tag = "HELD" if o.held else "IN SCENE"
            out.append(f"OBJECT  {o.name} · {tag} · {int(o.confidence * 100)}%")
        if report.glare:
            out.append(f"BACKLIGHT  window/sun rays ({report.glare_pct}% blown)")
        return out

    @staticmethod
    def _narrate(report: VisionReport) -> str:
        parts = []
        c = report.cloth
        if c.detected:
            bits = []
            if c.primary:
                bits.append(c.primary)
            if c.secondary:
                bits.append(f"aur {c.secondary} accents")
            garment = c.garment or "garment"
            detail = f"{' '.join(bits)} {garment}" if bits else garment
            if c.sleeve:
                detail += f", {c.sleeve}"
            if c.neckline and c.neckline != "open neckline":
                detail += f", {c.neckline}"
            if c.layers:
                detail += f", layered over {c.layers}"
            if c.bottom:
                detail += f", {c.bottom}"
            if c.pattern and c.pattern != "plain":
                detail += f", {c.pattern}"
            parts.append(detail)
        o = report.obj
        # only speak about objects that are actually in the user's hand -
        # background clutter (a window read as a "tv", a chair, ...) is noise
        if o.detected and o.held:
            parts.append(f"aur haath mein {o.name} pakda hai")
        f = report.face
        if f.detected:
            expr = f.expression.lower()
            parts.append(f"aap {expr} dikh rahe ho")
            if f.glasses:
                parts.append("chashma lagaya hai")
            if f.speaking:
                parts.append("aur bol rahe ho")
        if report.glare:
            parts.append("peeche se dhoop aa rahi hai, thoda saamne aaiye")
        if not parts:
            return ("sir, camera mein aap theek se nahi dikh rahe - "
                    "thoda peeche aaiye ya lighting theek kijiye")
        return ", ".join(parts)
