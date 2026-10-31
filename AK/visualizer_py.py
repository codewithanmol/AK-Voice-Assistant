# AK Cosmos Tide Visualizer
# A full-window field of soft stardust that drifts slowly and breathes,
# leaving dreamy light-trails. Sound arrives as a gentle rising tide; every
# so often the dust slowly blossoms into universe shapes (galaxy / nebula /
# star-field / black hole / burst / ring / ripple / spiral / planets / wave /
# comet) with eased, calm motion, then dissolves back into the tide. Spoken
# words rise out of the dusk in vivid, readable colours, kept in a small
# central block. Everything deliberately moves SLOW.
import os
import re
import math
import json
import time
import random
import ctypes
import queue
import threading
import zlib

if os.environ.get("AK_VIS_TEST_FRAMES"):
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
import numpy as np  # noqa: E402


def _ak_import(name):
    """Import an AK submodule whether we run as a script or as a package."""
    try:
        return __import__("AK." + name, fromlist=[name])
    except ImportError:
        try:
            return __import__(name)
        except ImportError:
            raise


try:
    import sounddevice as sd  # noqa: E402
    _SD = True
except Exception:
    _SD = False

try:
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
except Exception:
    BASE = os.getcwd()

# ── Palette ───────────────────────────────────────────────────────────────
N_BANDS = 24


def _band_spectrum(arr, n=N_BANDS):
    """24 log-ish bands from one mic block + the spectral centre (Hz).

    The bands drive the shapes, the centre drives the colour - that is what
    makes the colour follow the voice instead of a random hash.
    """
    try:
        win = np.hanning(arr.shape[0])
        spec = np.abs(np.fft.rfft(arr * win))
        m = spec.shape[0]
        if m < 4:
            return np.zeros(n, np.float64), 0.0
        sr = 16000.0
        freqs = np.fft.rfftfreq(arr.shape[0], 1.0 / sr)
        power = spec ** 2
        tot = float(power.sum())
        centroid = float((freqs * power).sum() / tot) if tot > 1e-9 else 0.0
        src = np.interp(np.linspace(0, m - 1, n * 4), np.arange(m), spec)
        bands = src.reshape(n, 4).mean(axis=1)
        mx = float(bands.max())
        if mx > 1e-7:
            bands = bands / mx
        return np.clip(bands ** 0.75, 0.0, 1.0), centroid
    except Exception:
        return np.zeros(n, np.float64), 0.0


STATE_LIGHT = {
    "idle": (255, 217, 102), "listening": (120, 214, 252),
    "thinking": (196, 138, 252), "processing": (88, 236, 216),
    "responding": (255, 138, 101),
}
THEME_HUE = {
    "sand": (240, 196, 116), "neon": (112, 182, 255), "fire": (255, 124, 72),
    "forest": (110, 222, 142), "grape": (192, 142, 255), "ice": (150, 216, 255),
}
BG_DIM = (8, 9, 15)

# ── Animation styles (switchable from the button in this window) ──────────
# The first six are the calm sand field. The live ones (AUTO + the voice
# reactive orb) are the modern family: they answer the mic in real time,
# the way Gemini's live voice orb does.
try:
    from .vis_live import LIVE_NAMES, LiveStage, smart_hue
except ImportError:
    from vis_live import LIVE_NAMES, LiveStage, smart_hue

try:
    from .future_hud import FutureHUD, _cut_path, hsv
except ImportError:
    from future_hud import FutureHUD, _cut_path, hsv

ANIM_NAMES = ["AUTO", "ORB", "FACE", "WALK", "TIDE", "VORTEX", "BLOOM",
              "RIVER", "STORM", "PRISM"]
SAND_INDEX = {"TIDE": 0, "VORTEX": 1, "BLOOM": 2, "RIVER": 3, "STORM": 4,
              "PRISM": 5}


def _hsv_rgb(h, s, v):
    """HSV -> RGB. h in degrees, s/v in 0..1."""
    h = float(h) % 360.0
    s = max(0.0, min(1.0, s))
    v = max(0.0, min(1.0, v))
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    i = int(h // 60.0) % 6
    r, g, b = [(c, x, 0.0), (x, c, 0.0), (0.0, c, x),
               (0.0, x, c), (x, 0.0, c), (c, 0.0, x)][i]
    return (int(round((r + m) * 255)), int(round((g + m) * 255)),
            int(round((b + m) * 255)))


def _text_hue(text: str) -> float:
    """Stable hue for a spoken phrase - same words -> same colour."""
    t = (text or "").strip().lower()
    if not t:
        return 40.0
    h = zlib.crc32(t.encode("utf-8", "ignore")) & 0xFFFFFFFF
    return float(h % 360)


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _shade(c, h):
    return max(0, min(255, int(round(c[0] * (0.90 + 0.22 * h))))), \
        max(0, min(255, int(round(c[1] * (0.90 + 0.22 * h))))), \
        max(0, min(255, int(round(c[2] * (0.90 + 0.22 * h)))))


# ── Cosmic tide particle field (full window) ──────────────────────────────
FN = 8200
px = np.zeros(FN, np.float64)
py = np.zeros(FN, np.float64)
vx = np.zeros(FN, np.float64)
vy = np.zeros(FN, np.float64)
tx = np.zeros(FN, np.float64)   # event morph targets
ty_ = np.zeros(FN, np.float64)
h1 = np.zeros(FN, np.float64)   # radius / phase hash
h2 = np.zeros(FN, np.float64)   # angle / depth hash
h3 = np.zeros(FN, np.float64)   # hue / arm hash
col = np.zeros((FN, 3), np.uint8)


def init_particles():
    rng = np.random.default_rng(42)
    h1[:] = rng.random(FN)
    h2[:] = rng.random(FN)
    h3[:] = rng.random(FN)
    px[:] = rng.random(FN) * 1100
    py[:] = rng.random(FN) * 620
    vx[:] = 0.0
    vy[:] = 0.0


def make_colors(theme):
    dim = _lerp((26, 34, 58), THEME_HUE.get(theme, THEME_HUE["sand"]), 0.30)
    for i in range(FN):
        col[i] = _shade(dim, h1[i])


def step_drift(dt, t, W, H, level):
    """Slow tide: gentle fall with deep, low-frequency currents. Sound adds a
    soft upward surge and a wide slow wave — never jerky, always calm."""
    g = min(1.6, dt * 60.0)
    fall = 0.55 + 1.5 * h2                                # slow, depth-sorted fall
    wind = math.sin(t * 0.10) * 0.22 + math.sin(t * 0.055 + 1.7) * 0.13
    gust = math.sin(t * 0.16 + 2.0) * 0.34 + math.sin(t * 0.08) * 0.18
    sway = np.sin(t * 0.32 + h1 * 6.283) * 0.48 + np.sin(t * 0.9 + h3 * 6.283) * 0.20
    flow = np.sin(t * 1.1 - py * 0.016 + h3 * 6.283) * 1.5 * level
    vx[:] += (wind + gust + sway + flow) * g
    vy[:] += (fall - 3.4 * level) * g                     # sound: gentle lift
    vx[:] *= 0.90
    vy[:] *= 0.92
    px[:] += vx[:] * g
    py[:] += vy[:] * g
    gnd = H * 0.88
    bo = py[:] > gnd
    if bo.any():
        py[bo] = 2.0 * gnd - py[bo]
        vy[bo] = -abs(vy[bo]) * (0.20 + 0.50 * level)
    up = py[:] < -10.0
    if up.any():
        py[up] = -8.0 - 10.0 * h1[up]
        px[up] = W * (0.03 + 0.94 * h2[up])
        vy[up] = h1[up] * 0.5
    left = px[:] < -6.0
    if left.any():
        px[left] = W - 4.0
    right = px[:] > W + 6.0
    if right.any():
        px[right] = 4.0
    np.clip(vx, -24, 24, out=vx)
    np.clip(vy, -24, 24, out=vy)
    np.clip(px, -6, W + 6, out=px)
    py[:] = np.clip(py, -10.0, H)


def step_morph(dt):
    """Slow soft spring toward the shape targets (eased momentum)."""
    g = min(1.6, dt * 60.0)
    k = (0.055 * g) * (0.70 + 0.50 * h3)
    d = np.clip(0.86 * (0.86 + 0.22 * h2), 0.70, 0.95)
    vx[:] = (vx[:] + (tx[:] - px[:]) * k) * d
    vy[:] = (vy[:] + (ty_[:] - py[:]) * k) * d
    vx[:] += (h3[:] - 0.5) * 0.08 * g
    vy[:] += (h2[:] - 0.5) * 0.06 * g
    px[:] += vx[:] * g
    py[:] += vy[:] * g
    np.clip(vx, -24, 24, out=vx)
    np.clip(vy, -24, 24, out=vy)


# ── Animation style force fields ─────────────────────────────────────────
# Each style adds its own force on top of the base tide, so the same dust
# field can drift, whirl, bloom, stream or storm. Two styles can be mixed
# while one cross-fades into the other.
def step_style(dt, t, W, H, level, style, weight, pulse):
    """Apply the force field of one animation style.

    level = live audio level, pulse = speech onset energy (0..1),
    weight = 0..1 mix (used for the cross-fade between two styles).
    """
    if weight <= 0.002:
        return
    g = min(1.6, dt * 60.0)
    if style == 1:                     # VORTEX - slow whirlpool
        cx, cy = W * 0.5, H * 1.04
        dx = px - cx
        dy = py - cy
        d = np.hypot(dx, dy) + 1e-3
        tang = 0.85 + 0.85 * level + 0.75 * pulse
        vx[:] += (-dy / d) * tang * g * weight
        vy[:] += (dx / d) * tang * g * weight
        vx[:] -= (dx / d) * 0.38 * g * weight
        vy[:] -= (dy / d) * 0.38 * g * weight
    elif style == 2:                   # BLOOM - waves breathing outward
        cx, cy = W * 0.5, H * 0.56
        dx = px - cx
        dy = py - cy
        d = np.hypot(dx, dy) + 1e-3
        target = ((t * 0.17) % 1.0) * max(W, H) * 0.78
        band = np.abs(d - target)
        hit = band < 46.0
        if hit.any():
            push = (1.0 - band[hit] / 46.0) * (0.75 + 1.5 * level + 1.4 * pulse)
            vx[hit] += (dx[hit] / d[hit]) * push * g * weight
            vy[hit] += (dy[hit] / d[hit]) * push * g * weight
    elif style == 3:                   # RIVER - sideways current
        s = np.sin(py * 0.017 + t * 0.85 + h3 * 6.283)
        vx[:] += (1.15 + 1.0 * level + 0.9 * pulse) * g * weight
        vy[:] += s * 0.95 * g * weight
        vy[:] -= 0.30 * g * weight
    elif style == 4:                   # STORM - turbulence, angry on sound
        turb = np.sin(t * 2.05 + h1 * 12.0) + np.cos(t * 1.65 + h2 * 9.0)
        vx[:] += turb * (0.85 + 1.7 * level + 1.5 * pulse) * g * weight
        vy[:] += (np.cos(t * 1.25 + h2 * 11.0) * 0.85 - 0.55 * level) * g * weight
    elif style == 5:                   # PRISM - lazy orbit, colour does the work
        cx, cy = W * 0.5, H * 0.55
        dx = px - cx
        dy = py - cy
        d = np.hypot(dx, dy) + 1e-3
        vx[:] += (-dy / d) * 0.55 * g * weight
        vy[:] += (dx / d) * 0.55 * g * weight
        vx[:] += (h1 - 0.5) * 0.55 * g * weight
        vy[:] += (h3 - 0.5) * 0.55 * g * weight


# ── Universe-shape event targets (slow, eased) ────────────────────────────
def compute_event_targets(t, ev, W, H):
    cx, cy, size = ev["cx"], ev["cy"], ev["size"]
    p0 = float(np.clip((t - ev["t0"]) / max(0.01, ev["dur"]), 0.0, 1.0))
    p = p0 * p0 * (3.0 - 2.0 * p0)                        # smoothstep ease
    typ = ev["type"]
    if typ == "galaxy":
        r = size * (0.05 + 0.95 * np.sqrt(h1))
        arm = np.where(h3 < 0.5, 0.0, np.pi)
        ang0 = h2 * 6.283 + np.log(1.0 + r / (0.28 * size)) * 3.1 + arm
        ang = ang0 + t * (0.08 + 0.16 * p)
        tx[:] = cx + np.cos(ang) * r + (h2 - 0.5) * 0.10 * r
        ty_[:] = cy + np.sin(ang) * r * 0.52 + (h1 - 0.5) * 0.06 * size
    elif typ == "nebula":
        o1x = cx - 0.30 * size + np.cos(t * 0.10) * 0.25 * size
        o1y = cy - 0.10 * size + np.sin(t * 0.08) * 0.16 * size
        o2x = cx + 0.30 * size + np.cos(t * 0.08 + 2.0) * 0.25 * size
        o2y = cy + 0.08 * size + np.sin(t * 0.065 + 1.2) * 0.16 * size
        bx = np.where(h3 < 0.5, o1x, o2x)
        by = np.where(h3 < 0.5, o1y, o2y)
        rr = size * (0.18 + 0.55 * np.sqrt(h1))
        ang = h2 * 6.283 + t * 0.012
        tx[:] = bx + np.cos(ang) * rr * (0.15 + 0.85 * np.sqrt(h1))
        ty_[:] = by + np.sin(ang) * rr * 0.65 * (0.15 + 0.85 * np.sqrt(h1))
    elif typ == "cluster":
        tx[:] = W * (0.04 + 0.92 * h1)
        ty_[:] = H * (0.10 + 0.72 * h2)
    elif typ == "blackhole":
        rr = size * (0.30 + 1.15 * np.sqrt(h1)) * (1.0 - 0.78 * p)
        ang = h2 * 6.283 + (t * 0.28 + p * 4.0)
        tx[:] = cx + np.cos(ang) * rr
        ty_[:] = cy + np.sin(ang) * rr * 0.60
    elif typ == "burst":
        rr = size * (0.15 + 1.05 * np.sqrt(h1)) * (0.30 + 0.85 * p)
        ang = h2 * 6.283
        tx[:] = cx + np.cos(ang) * rr * 1.6
        ty_[:] = cy + np.sin(ang) * rr + (h3 - 0.5) * 0.08 * size
    elif typ == "ring":
        rr = size * (0.75 + 0.35 * np.sqrt(h1))
        ang = h2 * 6.283 + t * 0.16
        tx[:] = cx + np.cos(ang) * rr
        ty_[:] = cy + np.sin(ang) * rr * 0.30 + (h1 - 0.5) * 0.05 * size
        core = h3 > 0.80
        cr = np.sqrt(h1[core]) * 0.18 * size
        ca = h2[core] * 6.283 + t * 0.32
        tx[core] = cx + np.cos(ca) * cr
        ty_[core] = cy + np.sin(ca) * cr
    elif typ == "ripple":
        rr0 = size * (0.10 + 0.80 * h1)
        rr = rr0 + np.sin(p * 12.56 - rr0 * 0.09) * 0.06 * size
        ang = h2 * 6.283
        tx[:] = cx + np.cos(ang) * rr
        ty_[:] = cy + np.sin(ang) * rr * 0.55
    elif typ == "spiral":
        rr = size * (0.10 + 1.05 * np.sqrt(h1))
        ang = h2 * 6.283 + (t * (0.18 + 0.30 * p) + rr * 0.045)
        tx[:] = cx + np.cos(ang) * rr * 1.35
        ty_[:] = cy + np.sin(ang) * rr * 1.35 * 0.62
    elif typ == "planets":
        core = np.floor(h3 * 4.0).astype(np.int32)
        angc = core * (6.283 / 4.0) + t * 0.12 + 0.4
        pcx = cx + np.cos(angc) * 0.20 * size
        pcy = cy + np.sin(angc) * 0.15 * size
        rr = size * (0.06 + 0.42 * np.sqrt(h1))
        orbit = h2 * 6.283 + t * (0.28 + 0.14 * np.sign(h3 - 0.5))
        tx[:] = pcx + np.cos(orbit) * rr
        ty_[:] = pcy + np.sin(orbit) * rr * 0.62
    elif typ == "wave":
        xx = W * (0.04 + 0.92 * h1)
        yy = cy + size * 0.62 * np.sin(xx * 0.010 + t * 0.8) * (0.45 + 0.55 * h2)
        tx[:] = xx
        ty_[:] = yy
    else:  # comet — a slow streak sweeping the sky
        ph = (t * 0.22) % 1.0
        head = W * (0.06 + 0.88 * ph)
        tx[:] = head - W * 0.55 * h1 - (h2 - 0.5) * 0.06 * W
        ty_[:] = H * (0.20 + 0.60 * h3) + (h1 - 0.5) * 0.10 * H


# ── Caption (particle-foam text; words rise slowly out of the dusk) ───────
CM = 5200
cpx = np.zeros(CM, np.float64)
cpy = np.zeros(CM, np.float64)
cvx = np.zeros(CM, np.float64)
cvy = np.zeros(CM, np.float64)
ctx = np.zeros(CM, np.float64)
cty = np.zeros(CM, np.float64)
ch1 = np.zeros(CM, np.float64)
ch2 = np.zeros(CM, np.float64)
ch3 = np.zeros(CM, np.float64)
ccol = np.zeros((CM, 3), np.uint8)
csrc = np.zeros((CM, 3), np.float64)


def _word_dur(word):
    return 0.26 + 0.10 * len(word)


def _raster_pts(word, size, budget):
    # Thick, readable glyphs: render the word BOLD in a clean sans font, then
    # add all its 8-shifted neighbours so the strokes become dense and solid
    # (readable from across the room, not a thin wireframe).
    font = pygame.font.SysFont("verdana", size, bold=True)
    try:
        surface = font.render(word, True, (255, 255, 255))
        surface = surface.convert_alpha()
        alpha = pygame.surfarray.array_alpha(surface)
    except Exception:
        return None
    mask = np.asarray(alpha, dtype=np.int16) > 55
    xs, ys = np.nonzero(mask)
    if xs.size == 0:
        return None
    h, w = mask.shape
    pad_mask = np.zeros((h + 2, w + 2), dtype=bool)
    pad_mask[1:-1, 1:-1] = mask
    # union of core + 8 neighbours = ~2.5x denser glyph, still cheap
    pts = [np.stack((xs + 1, ys + 1), axis=-1)]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            xs2, ys2 = np.nonzero(pad_mask[1 + dx:1 + dx + h, 1 + dy:1 + dy + w])
            pts.append(np.stack((xs2 + 1, ys2 + 1), axis=-1))
    all_pts = np.concatenate(pts, axis=0)
    n = all_pts.shape[0]
    if n > budget:
        idx = np.linspace(0, n - 1, budget).astype(int)
        idx = np.unique(idx)
        all_pts = all_pts[idx]
    return all_pts


# Full-window logo: a single big letter (like AK's 'K') that fills the ENTIRE
# window as stardust — not the small caption box. Rendered huge, densified
# with 8-neighbour strokes, then scaled so the glyph covers ~92% of the screen.
_LOGO_WORDS = frozenset({"k", "ak"})


def _big_shape_pts(key, W, H, budget):
    """Stamp a shape word, scaled to a LARGE centred display (for custom /
    showcase mode) so it reads clearly with the universe hidden behind it."""
    stamp = _shape_stamp(key)
    if stamp is None or stamp.size == 0:
        return None
    pts = stamp.astype(np.float64)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    gw, gh = hi[0] - lo[0], hi[1] - lo[1]
    if gw <= 0 or gh <= 0:
        return None
    scale = min(W * 0.82 / gw, H * 0.76 / gh)
    out = (pts - lo) * scale
    out[:, 0] += (W - gw * scale) / 2.0
    out[:, 1] += (H - gh * scale) / 2.0
    if out.shape[0] > budget:
        idx = np.linspace(0, out.shape[0] - 1, budget).astype(int)
        idx = np.unique(idx)
        out = out[idx]
    return out


def _logo_pts(word, W, H, budget):
    size = max(120, int(min(W, H) * 1.6))
    font = pygame.font.SysFont("verdana", size, bold=True)
    try:
        surface = font.render(word, True, (255, 255, 255))
        surface = surface.convert_alpha()
        alpha = pygame.surfarray.array_alpha(surface)
    except Exception:
        return None
    mask = np.asarray(alpha, dtype=np.int16) > 55
    xs, ys = np.nonzero(mask)
    if xs.size == 0:
        return None
    h, w = mask.shape
    pad_mask = np.zeros((h + 2, w + 2), dtype=bool)
    pad_mask[1:-1, 1:-1] = mask
    pts = [np.stack((xs + 1, ys + 1), axis=-1)]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            xs2, ys2 = np.nonzero(pad_mask[1 + dx:1 + dx + h, 1 + dy:1 + dy + w])
            pts.append(np.stack((xs2 + 1, ys2 + 1), axis=-1))
    all_pts = np.concatenate(pts, axis=0)
    # measure the raw glyph, then rescale so it FILLS the whole window
    lo = all_pts.min(axis=0).astype(np.float64)
    hi = all_pts.max(axis=0).astype(np.float64)
    gw, gh = hi[0] - lo[0], hi[1] - lo[1]
    if gw <= 0 or gh <= 0:
        return None
    # stretch BOTH axes so the glyph FILLS the whole window — a huge K wall
    sx = (W * 0.96) / gw
    sy = (H * 0.94) / gh
    scaled = (all_pts.astype(np.float64) - lo)
    scaled[:, 0] *= sx
    scaled[:, 1] *= sy
    scaled[:, 0] += (W - (gw * sx)) / 2.0
    scaled[:, 1] += (H - (gh * sy)) / 2.0
    n = scaled.shape[0]
    if n > budget:
        idx = np.linspace(0, n - 1, budget).astype(int)
        idx = np.unique(idx)
        scaled = scaled[idx]
    return scaled


# ── Shape stamping ─────────────────────────────────────────────────────────
# When a spoken word names a thing (heart, face, animal, food, vegetable,
# fruit, star, moon…), AK stamps that shape out of stardust instead of the
# literal word. Points are generated inside a W*H box, y grows DOWN.
SHAPE_W, SHAPE_H = 170, 130
_shape_w = SHAPE_W                 # layout width used for a shape stamp
_RNG = np.random.default_rng(21)


def _stamp(pts, w=SHAPE_W, h=SHAPE_H):
    """Normalise a raw point cloud into the shape box and sample to budget."""
    pts = np.asarray(pts, np.float64)
    if pts.size == 0:
        return np.zeros((0, 2), np.int32)
    mn = pts.min(axis=0)
    mx = pts.max(axis=0)
    span = np.maximum(mx - mn, 1e-9)
    scale = min(w / span[0], h / span[1])
    pts = (pts - mn) * scale
    cx = (w - pts[:, 0].max()) / 2
    cy = (h - pts[:, 1].max()) / 2
    pts[:, 0] += cx
    pts[:, 1] += cy
    img = np.zeros((int(h), int(w)), np.float32)
    xi = np.clip(np.floor(pts[:, 0]).astype(int), 0, int(w) - 1)
    yi = np.clip(np.floor(pts[:, 1]).astype(int), 0, int(h) - 1)
    img[yi, xi] += 1.0
    rows, cols = np.nonzero(img)                    # nonzero -> (y=row, x=col)
    out = np.column_stack((cols, rows))             # (x=col, y=row)
    n = out.shape[0]
    if n > 4200:                                    # keep whole shape, evenly sampled
        idx = np.unique(np.linspace(0, n - 1, 4200).astype(int))
        out = out[idx]
    return out.astype(np.float64)


def _heart_pts():
    t = np.linspace(0, 2 * np.pi, 900)
    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
    pts = np.column_stack((x, y))
    return pts


def _face_pts():
    # head outline + eyes + smile
    a = np.linspace(0, 2 * np.pi, 780)
    head = np.column_stack((44 * np.cos(a), 52 * np.sin(a)))
    eyes = []
    for ex in (-16, 16):
        e = np.linspace(0, 2 * np.pi, 90)
        eyes.append(np.column_stack((ex + 6 * np.cos(e), -8 + 5 * np.sin(e))))
    smile = np.linspace(-0.5 * np.pi, 0.5 * np.pi, 280)
    sx = 22 * np.cos(smile)
    sy = 18 * np.abs(np.sin(smile))     # lower half of an ellipse → smile arc
    s = np.column_stack(((sx * -1) - 2, 16 + sy))
    return np.vstack((head, *eyes, s))


def _dog_pts():
    a = np.linspace(0, 2 * np.pi, 700)
    head = np.column_stack((40 * np.cos(a), 38 * np.sin(a)))
    ears = []
    for cx, fl in ((-30, 1), (30, -1)):
        tip = (cx, -52)
        b1 = (cx + 30 * fl, -2)
        b2 = (cx - 22 * fl * 0.7, -2)
        pts = []
        for u in np.linspace(0, 1, 22):
            for v in np.linspace(0, 1, 22):
                if u + v <= 1.0:
                    pts.append([b1[0] + (tip[0] - b1[0]) * u + (b2[0] - b1[0]) * v,
                                b1[1] + (tip[1] - b1[1]) * u + (b2[1] - b1[1]) * v])
        ears.append(np.array(pts))
    eyes = []
    for ex in (-15, 15):
        e = np.linspace(0, 2 * np.pi, 60)
        eyes.append(np.column_stack((ex + 4 * np.cos(e), -10 + 4 * np.sin(e))))
    nose = np.linspace(0, 2 * np.pi, 70)
    n = np.column_stack((0 + 8 * np.cos(nose), 4 + 6 * np.sin(nose)))
    return np.vstack((head, ears[0], ears[1], eyes[0], eyes[1], n))


def _cat_pts():
    a = np.linspace(0, 2 * np.pi, 700)
    head = np.column_stack((36 * np.cos(a), 36 * np.sin(a)))
    ears = []
    for cx, flip in ((-26, -1), (26, 1)):
        top = (cx, -46)
        base = (cx + 30 * flip, -4)
        base2 = (cx - 18 * flip * 0.7, -4)
        pts = []
        for u in np.linspace(0, 1, 26):
            for v in np.linspace(0, 1, 26):
                if u + v <= 1.0:
                    pts.append([base[0] + (top[0] - base[0]) * u + (base2[0] - base[0]) * v,
                                base[1] + (top[1] - base[1]) * u + (base2[1] - base[1]) * v])
        ears.append(np.array(pts))
    eyes = []
    for ex in (-14, 14):
        e = np.linspace(0, 2 * np.pi, 60)
        eyes.append(np.column_stack((ex + 5 * np.cos(e), -9 + 5 * np.sin(e))))
    sn = np.linspace(0, 2 * np.pi, 80)
    snout = np.column_stack((0 + 7 * np.cos(sn), 6 + 5 * np.sin(sn)))
    return np.vstack((head, ears[0], ears[1], eyes[0], eyes[1], snout))


def _bird_pts():
    a = np.linspace(0, 2 * np.pi, 600)
    body = np.column_stack((34 * np.cos(a), 24 * np.sin(a) - 4))
    hd = np.linspace(0, 2 * np.pi, 260)
    head = np.column_stack((46 + 16 * np.cos(hd), -22 + 16 * np.sin(hd)))
    eye = np.linspace(0, 2 * np.pi, 50)
    eye_pts = np.column_stack((48 + 4 * np.cos(eye), -24 + 4 * np.sin(eye)))
    # beak: cone pointing right
    beak = np.array([[58, -28], [84, -18], [52, -10]])
    bpts = np.array([[beak[0, 0] + (beak[1, 0] - beak[0, 0]) * u + (beak[2, 0] - beak[0, 0]) * v,
                      beak[0, 1] + (beak[1, 1] - beak[0, 1]) * u + (beak[2, 1] - beak[0, 1]) * v]
                     for u in np.linspace(0, 1, 20) for v in np.linspace(0, 1, 20)
                     if u + v <= 1.0])
    wing = np.array([[-38, 2], [2, -6], [-10, 22]])
    wpts = np.array([[wing[0, 0] + (wing[1, 0] - wing[0, 0]) * u + (wing[2, 0] - wing[0, 0]) * v,
                      wing[0, 1] + (wing[1, 1] - wing[0, 1]) * u + (wing[2, 1] - wing[0, 1]) * v]
                     for u in np.linspace(0, 1, 22) for v in np.linspace(0, 1, 22)
                     if u + v <= 1.0])
    return np.vstack((body, head, eye_pts, bpts, wpts))


def _fish_pts():
    a = np.linspace(0, 2 * np.pi, 720)
    body = np.column_stack((40 * np.cos(a), 24 * np.sin(a)))
    tail = np.array([[-36, 0], [-58, -26], [-58, 26]])
    tpts = np.array([[tail[0, 0] + (tail[1, 0] - tail[0, 0]) * u + (tail[2, 0] - tail[0, 0]) * v,
                      tail[0, 1] + (tail[1, 1] - tail[0, 1]) * u + (tail[2, 1] - tail[0, 1]) * v]
                     for u in np.linspace(0, 1, 24) for v in np.linspace(0, 1, 24)
                     if u + v <= 1.0])
    eye = np.linspace(0, 2 * np.pi, 60)
    eye_pts = np.column_stack((30 + 5 * np.cos(eye), 0 + 5 * np.sin(eye)))
    return np.vstack((body, tpts, eye_pts))


def _pizza_pts():
    a = np.linspace(0, 2 * np.pi, 800)
    body = np.column_stack((44 * np.cos(a), 44 * np.sin(a)))
    crust = np.linspace(0, 2 * np.pi, 360)
    c = np.column_stack((44 * np.cos(crust) * 0.82, 44 * np.sin(crust) * 0.82))
    # slice cut lines
    cut = []
    for ang in (-0.6, 0.6):
        t = np.linspace(0, 1, 90)
        cut.append(np.column_stack((ang * 60 * (1 - t) + t * 34 * np.cos(ang),
                                    t * 34 * np.sin(ang) + ang * 0)))
    return np.vstack((body, c, cut[0], cut[1]))


def _burger_pts():
    a = np.linspace(0, 2 * np.pi, 420)
    top = np.column_stack((46 * np.cos(a) * 1.0, 16 * np.sqrt(np.cos(np.arccos(np.cos(a)) * 0)) + 16 * np.sin(a) * -1 + 4))
    # simpler: top bun (upper ellipse), patty (rect band), bottom bun (lower ellipse)
    bun_up = np.vstack((np.column_stack((46 * np.cos(a), 22 * (np.sin(a) * -1) + -18)),
                        np.column_stack((46 * np.cos(a), 22 * np.sin(a) + -30))))
    patty = np.column_stack((np.linspace(-46, 46, 500), np.linspace(-46, 46, 500) * 0))
    py = np.repeat(6.0, 500)
    px = np.linspace(-44, 44, 500)
    m = (py >= 6) & (py <= 22)
    patty = np.column_stack((px, py))
    bun_lo = np.column_stack((44 * np.cos(a), 16 * np.sin(a) + 26))
    return np.vstack((bun_up[::2], patty, bun_lo))


def _apple_pts():
    a = np.linspace(0, 2 * np.pi, 850)
    fruit = np.column_stack((36 * np.cos(a), 44 * np.sin(a) - 4))
    stem = np.column_stack((np.linspace(0, 0, 90), np.linspace(-40, -58, 90)))
    leaf = np.linspace(-0.9 * np.pi, 0.9 * np.pi, 120)
    lx = 14 + 14 * np.cos(leaf)
    ly = -46 + 8 * np.sin(leaf)
    leaf_pts = np.column_stack((lx, ly))
    return np.vstack((fruit, stem, leaf_pts))


def _banana_pts():
    # crescent band (outer + inner arcs)
    t0 = np.linspace(-2.4, 0.9, 620)
    cx, cy, r1, r2 = 0, 10, 46, 30
    out = np.column_stack((cx + r1 * np.cos(t0), cy + r1 * np.sin(t0)))
    inn = np.column_stack((cx + r2 * np.cos(t0), cy + r2 * np.sin(t0)))
    caps = np.column_stack((np.linspace(0, 0, 60), np.linspace(-14, 14, 60)))
    return np.vstack((out, inn))


def _carrot_pts():
    body = []
    for u in np.linspace(0, 1, 34):
        w2 = (1 - u) * 46
        for v in np.linspace(-w2, w2, 12):
            body.append([u * 100 - 36, v])
    leaves = []
    for a0 in (-0.9, 0.0, 0.9):
        t = np.linspace(0, 1, 90)
        leaves.append(np.column_stack((a0 * 40 * t - 0, -44 - 60 * t)))
        leaves.append(np.column_stack((a0 * 40 * t, -44 - 60 * t)))
    return np.vstack((np.array(body), leaves[0], leaves[1], leaves[2]))


def _tomato_pts():
    a = np.linspace(0, 2 * np.pi, 850)
    fruit = np.column_stack((44 * np.cos(a), 44 * np.sin(a)))
    top = []
    for a0 in (-0.8, 0, 0.8):
        t = np.linspace(0, 1, 80)
        top.append(np.column_stack((a0 * 56 * (1 - t) - 0, -40 * (1 - t))))
    return np.vstack((fruit, top[0], top[1], top[2]))


def _mango_pts():
    a = np.linspace(0, 2 * np.pi, 700)
    fruit = np.column_stack((40 * np.cos(a), 56 * np.sin(a)))
    hook = np.column_stack((np.linspace(0, 12, 90), np.linspace(-50, -62, 90)))
    return np.vstack((fruit, hook))


def _star_pts():
    # 5-pointed star outline
    n = 5
    angles = [i * 2 * np.pi / n - np.pi / 2 for i in range(n)]
    inner = [a + np.pi / n for a in angles]
    R, r = 58, 24
    verts = []
    for i in range(n):
        verts.append((R * np.cos(angles[i]), R * np.sin(angles[i])))
        verts.append((r * np.cos(inner[i]), r * np.sin(inner[i])))
    ring = []
    for i in range(len(verts)):
        a, b = verts[i], verts[(i + 1) % len(verts)]
        t = np.linspace(0, 1, 74)
        ring.append(np.column_stack((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)))
    return np.vstack(ring)


def _moon_pts():
    a = np.linspace(0, 2 * np.pi, 800)
    big = np.column_stack((46 * np.cos(a), 46 * np.sin(a)))
    small = np.column_stack((20 + 44 * np.cos(a) * 0.9, 6 + 44 * np.sin(a) * 0.9))
    # keep points that are in big but NOT in small → crescent
    d1 = big[:, 0] ** 2 + big[:, 1] ** 2
    d2 = (big[:, 0] - 20) ** 2 + (big[:, 1] - 6) ** 2
    keep = d2 > (38 ** 2)
    return big[keep]


def _sun_pts():
    a = np.linspace(0, 2 * np.pi, 760)
    disc = np.column_stack((42 * np.cos(a), 42 * np.sin(a)))
    rays = []
    for ang in np.linspace(0, 2 * np.pi, 24, endpoint=False):
        t = np.linspace(0, 1, 26)
        rays.append(np.column_stack((np.cos(ang) * (48 + 20 * t), np.sin(ang) * (48 + 20 * t))))
    return np.vstack((disc, *rays))


def _flower_pts():
    pts = []
    for k in range(6):
        a0 = k * np.pi / 3
        p = np.linspace(0, 2 * np.pi, 180)
        cx, cy = 34 * np.cos(a0), 34 * np.sin(a0)
        pts.append(np.column_stack((cx + 20 * np.cos(p), cy + 20 * np.sin(p))))
    core = np.linspace(0, 2 * np.pi, 360)
    pts.append(np.column_stack((14 * np.cos(core), 14 * np.sin(core))))
    return np.vstack(pts)


# ── Devine shapes (pure stardust, no emoji) ───────────────────────────────
def _peacock_pts():
    """Krishna's mor-pankh — a feather vane with a central 'eye'."""
    y_top, y_bot = -50.0, 52.0
    rows = []
    for i in range(210):
        y = y_top + (y_bot - y_top) * i / 209.0
        t = (y - y_top) / (y_bot - y_top)
        w = 23.0 * np.sin(np.pi * t) ** 0.72
        w = max(1.6, min(w, 24.0))
        rows.append([y, w])
    pts = []
    for y, w in rows:
        n = max(2, int(w * 1.1))
        for x in np.linspace(-w, w, n):
            pts.append([x, y])
    d = np.array(pts)
    # central eye (dark) -> carved out as a ring
    eye_c = np.array([6.0, -10.0])
    dist = np.hypot(d[:, 0] - eye_c[0], (d[:, 1] - eye_c[1]) * 0.5)
    ring = np.hypot(d[:, 0] - eye_c[0], (d[:, 1] - eye_c[1]) * 0.5)
    core = ring < 12.0
    outer = ring > 19.0
    d = d[~((core) | outer)]
    # shaft + base
    sh = []
    for i in range(130):
        y = -50 + (y_bot + 22 - -50) * i / 129.0
        for x in np.linspace(-1.6, 1.6, 4):
            sh.append([x, y])
    return np.vstack((d, np.array(sh)))


def _flute_pts():
    """Krishna murali — a horizontal flute with tuning holes."""
    pts = []
    for i in range(190):
        y = -14 * np.sin(np.arange(1))[0] if False else 0
    body = []
    for i in range(230):
        x = -58 + 116.0 * i / 229.0
        for y in np.linspace(-11, 11, 22):
            body.append([x, y])
    body = np.array(body)
    # round the ends
    for cx, b in ((58, 1), (-58, 1)):
        a = np.linspace(0, 2 * np.pi, 120)
        body = np.vstack((body, np.column_stack((cx + 11 * np.cos(a), 11 * np.sin(a)))))
        _ = b
    # holes
    holes = []
    for hx in (-16, -2, 12, 26):
        a = np.linspace(0, 2 * np.pi, 70)
        holes.append(np.column_stack((hx + 7 * np.cos(a), -4 * 0 + 0 + 7 * np.sin(a))))
    return np.vstack((body, holes[0], holes[1], holes[2], holes[3]))


def _trishul_pts():
    """Shiv's trishul — three upturned blades over a long staff."""
    pts = []
    # staff
    for i in range(150):
        y = 6 + (118.0 - 6) * i / 149.0
        for x in np.linspace(-3.2, 3.2, 5):
            pts.append([x, y])
    # centre blade (long)
    for y in np.linspace(-52, 6, 46):
        w = 1.2 + (10.0 - y / 12.0)
        w = max(1.2, 10.0 - (y + 20) * 0.18)
        for x in np.linspace(-w, w, max(2, int(w * 1.5))):
            pts.append([x, y])
    # two curved side blades
    for sgn in (-1, 1):
        for i in range(120):
            t = i / 119.0
            x = sgn * (14 + 26 * t - 8 * np.sin(np.pi * t) * 6)
            y = -8 - 26 * t + 6 * np.sin(np.pi * t * 0.9)
            w = max(1.2, 4.0 * np.sin(np.pi * t))
            for dx in np.linspace(-w, w, max(2, int(w * 1.6))):
                pts.append([x + dx, y])
    return np.array(pts)


def _gada_pts():
    """Hanuman's gada — a sturdy mace, round head on a straight staff."""
    pts = []
    for i in range(160):
        y = -20 + (116.0 - -20) * i / 159.0
        for x in np.linspace(-3.0, 3.0, 5):
            pts.append([x, y])
    a = np.linspace(0, 2 * np.pi, 480)
    head = np.column_stack((22 * np.cos(a), -34 + 24 * np.sin(a) * -1))
    head2 = np.column_stack((22 * np.cos(a), -34 + 24 * np.sin(a)))
    return np.vstack((np.array(pts), head2))


def _om_pts():
    """ॐ — a stylised Om glyph: top curl + two arcs + a dot."""
    pts = []
    a = np.linspace(0.15, np.pi - 0.15, 230)
    pts.append(np.column_stack((40 * np.cos(a), 52 * np.sin(a))))           # left dome
    b = np.linspace(0.0, np.pi, 220)
    pts.append(np.column_stack((0 + 40 * np.cos(b), -4 + 52 * np.sin(b))))  # base dome
    c = np.linspace(np.pi * 1.05, np.pi * 1.95, 200)
    pts.append(np.column_stack((-46 + 26 * np.cos(c), 30 + 34 * np.sin(c))))  # tail curl
    d = np.linspace(0, 2 * np.pi, 60)
    pts.append(np.column_stack((2 + 6 * np.cos(d), -44 + 6 * np.sin(d))))   # bindu dot
    return np.vstack(pts)


def _ganesh_pts():
    """Ganpati — elephant head: big ears, curved trunk, small tusks."""
    a = np.linspace(0, 2 * np.pi, 700)
    head = np.column_stack((34 * np.cos(a), 38 * np.sin(a)))
    ears = []
    for cx, fl in ((-36, -1), (36, 1)):
        e = np.linspace(0, 2 * np.pi, 300)
        ears.append(np.column_stack((cx + fl * 4 + 22 * np.cos(e), 6 + 26 * np.sin(e))))
    # trunk curled down-left
    trunk = []
    tt = np.linspace(0, 1, 220)
    tx = -8 - 34 * tt + 14 * np.sin(tt * np.pi)
    ty = 14 + 52 * tt ** 1.35
    for i in range(len(tt) - 1):
        x0, y0 = tx[i], ty[i]
        x1, y1 = tx[i + 1], ty[i + 1]
        w = max(1.4, 6.0 * (1 - tt[i] * 0.55))
        for k in range(max(2, int(w * 1.8))):
            f = k / (max(2, int(w * 1.8)) - 1 or 1)
            trunk.append([x0 + (x1 - x0) * f, y0 + (y1 - y0) * f])
            if x1 != x0 or y1 != y0:
                nx, ny = -(y1 - y0), (x1 - x0)
                ln = np.hypot(nx, ny) + 1e-9
                trunk.append([x0 + (x1 - x0) * f + nx / ln * w * 0.5,
                              y0 + (y1 - y0) * f + ny / ln * w * 0.5])
    eyes = []
    for ex in (-13, 13):
        e = np.linspace(0, 2 * np.pi, 70)
        eyes.append(np.column_stack((ex + 6 * np.cos(e), -8 + 6 * np.sin(e))))
    return np.vstack((head, ears[0], ears[1], np.array(trunk), eyes[0], eyes[1]))


def _chakra_pts():
    """Vishnu's Sudarshana chakra — a spinning disc with spokes."""
    pts = []
    a = np.linspace(0, 2 * np.pi, 640)
    pts.append(np.column_stack((44 * np.cos(a), 44 * np.sin(a))))
    for k in range(8):
        ang = k * 2 * np.pi / 8
        t = np.linspace(0, 1, 60)
        pts.append(np.column_stack((np.cos(ang) * 6 * (1 - t) + np.cos(ang) * 40 * t,
                                    np.sin(ang) * 6 * (1 - t) + np.sin(ang) * 40 * t)))
    core = np.linspace(0, 2 * np.pi, 90)
    pts.append(np.column_stack((7 * np.cos(core), 7 * np.sin(core))))
    return np.vstack(pts)


def _bow_pts():
    """Raghupati Raam — a strung bow with a nocked arrow."""
    parts = []
    a = np.linspace(-np.pi / 2, np.pi / 2, 420)
    for r in (52, 44):
        parts.append(np.column_stack((r * np.cos(a), r * np.sin(a))))
    t = np.linspace(0, 1, 90)
    parts.append(np.column_stack((-44 * (1 - t) + 44 * t, np.full(90, -46.0))))
    parts.append(np.column_stack((np.linspace(-46, 58, 200), np.full(200, -18.0))))
    a_head = np.array([[-46, -18], [-58, -12], [-58, -24]])
    for u in np.linspace(0, 1, 18):
        for v in np.linspace(0, 1, 18):
            if u + v <= 1.0:
                parts.append(np.array([[a_head[0, 0] + (a_head[1, 0] - a_head[0, 0]) * u
                                        + (a_head[2, 0] - a_head[0, 0]) * v,
                                        a_head[0, 1] + (a_head[1, 1] - a_head[0, 1]) * u
                                        + (a_head[2, 1] - a_head[0, 1]) * v]]))
    return np.vstack(parts)


def _kamal_pts():
    """Lakshmi ka kamal — layered lotus blossom."""
    pts = []
    for k, r in ((0, 44), (1, 34), (2, 24)):
        base_a = k * 2.2
        for pet in range(7):
            a0 = -np.pi / 2 + base_a + pet * 0.9
            p = np.linspace(a0, a0 + 1.1, 90)
            px = r * np.cos(p) + 8 * np.sin(p * 2)
            py = r * np.sin(p) + 14 * np.abs(np.cos(p))
            pts.extend(np.column_stack((px, py)).tolist())
    core = np.linspace(0, 2 * np.pi, 120)
    pts.extend(np.column_stack((10 * np.cos(core), 6 * np.sin(core))).tolist())
    return np.array(pts)


def _singh_pts():
    """Maa Durga ka vahan — a lion's face (mane ring + muzzle)."""
    a = np.linspace(0, 2 * np.pi, 620)
    mane = np.column_stack((48 * np.cos(a), 48 * np.sin(a)))
    for r in (36, 24):
        pts_r = r * np.cos(a)
        pty = r * np.sin(a)
    face = np.column_stack((34 * np.cos(a), 34 * np.sin(a)))
    sn = np.linspace(0, 2 * np.pi, 260)
    snout = np.column_stack((0 + 16 * np.cos(sn), 10 + 14 * np.sin(sn)))
    eyes = []
    for ex in (-14, 14):
        e = np.linspace(0, 2 * np.pi, 70)
        eyes.append(np.column_stack((ex + 5 * np.cos(e), -8 + 5 * np.sin(e))))
    return np.vstack((mane, face, snout, eyes[0], eyes[1]))


def _veena_pts():
    """Maa Saraswati ki veena — a sloping zither with a gourd head."""
    pts = []
    for i in range(240):
        x = -46 + 100.0 * i / 239.0
        y = 30 - 20 * x / 100.0
        for dy in np.linspace(-7, 7, 15):
            pts.append([x, y + dy])
    g = np.linspace(0, 2 * np.pi, 240)
    pts.extend(np.column_stack((-42 + 20 * np.cos(g), 46 + 18 * np.sin(g))).tolist())
    return np.array(pts)


def _diya_pts():
    """Shubh diya — a small oil lamp: flame over a saucer-shaped holder."""
    pts = []
    # flame (teardrop, y down so taller flame means lower points)
    tt = np.linspace(0, 1, 46)
    fx = 8 * np.sin(np.pi * tt)
    fy = -30 + 56 * tt
    for i in range(len(tt) - 1):
        w = max(0.8, 7.0 * np.sin(np.pi * tt[i]))
        pts.extend([[fx[i] + dx, fy[i]] for dx in np.linspace(-w, w, max(2, int(w * 1.6)))])
    # flame inner glow ring
    for r in (4,):
        a = np.linspace(0, 2 * np.pi, 90)
        pts.extend(np.column_stack((r * np.cos(a), -26 + r * np.sin(a))).tolist())
    # saucer body (deep dish)
    for yv in np.linspace(0, 18, 24):
        w = 30 * (1 - yv / 26.0) + 8
        pts.extend([[x, yv] for x in np.linspace(-w, w, max(2, int(w * 1.4)))])
    # rim + stand
    a = np.linspace(0, 2 * np.pi, 260)
    pts.extend(np.column_stack((38 * np.cos(a), 20 + 8 * np.sin(a))).tolist())
    for i in range(40):
        x = -6 + 12 * i / 39.0
        pts.extend([[x, 26 + (x + 6) * 0.4]])
    return np.array(pts)


# word → shape stamp: fans, faces, animals, food, veg, fruit, sky
_SHAPE_MAP = {
    "heart": _heart_pts, "dil": _heart_pts,
    "face": _face_pts, "chehra": _face_pts, "human": _face_pts,
    "dog": _dog_pts, "kutta": _dog_pts, "pet": _dog_pts,
    "cat": _cat_pts, "billi": _cat_pts,
    "bird": _bird_pts, "panchi": _bird_pts, "chidiya": _bird_pts,
    "fish": _fish_pts, "machli": _fish_pts,
    "pizza": _pizza_pts, "burger": _burger_pts, "apple": _apple_pts,
    "seb": _apple_pts, "banana": _banana_pts, "kela": _banana_pts,
    "carrot": _carrot_pts, "gajar": _carrot_pts,
    "tomato": _tomato_pts, "tamatar": _tomato_pts, "mango": _mango_pts,
    "aam": _mango_pts, "star": _star_pts, "tara": _star_pts,
    "moon": _moon_pts, "chand": _moon_pts, "chaand": _moon_pts,
    "sun": _sun_pts, "suraj": _sun_pts,
    "flower": _flower_pts, "phool": _flower_pts,
    # ── divine (pure param shapes) ──
    "krishna": _peacock_pts, "krishn": _peacock_pts, "krishan": _peacock_pts,
    "mor": _peacock_pts, "peacock": _peacock_pts, "krsna": _peacock_pts,
    "murali": _flute_pts, "flute": _flute_pts, "bansuri": _flute_pts,
    "shiva": _trishul_pts, "shiv": _trishul_pts, "mahadev": _trishul_pts,
    "bhole": _trishul_pts, "shankar": _trishul_pts, "trishul": _trishul_pts,
    "hanuman": _gada_pts, "bajrangbali": _gada_pts, "maruti": _gada_pts,
    "gada": _gada_pts, "pawan": _gada_pts, "hanu": _gada_pts,
    "ganesh": _ganesh_pts, "ganpati": _ganesh_pts, "bappa": _ganesh_pts,
    "ganeshji": _ganesh_pts, "vighnaharta": _ganesh_pts,
    "vishnu": _chakra_pts, "narayan": _chakra_pts, "chakra": _chakra_pts,
    "sudarshan": _chakra_pts, "sudarshana": _chakra_pts, "har": _chakra_pts,
    "ram": _bow_pts, "rama": _bow_pts, "shriram": _bow_pts, "raghu": _bow_pts,
    "raghunath": _bow_pts, "dhanush": _bow_pts, "bow": _bow_pts,
    "lakshmi": _kamal_pts, "laxmi": _kamal_pts, "kamal": _kamal_pts,
    "lotus": _kamal_pts, "mata": _kamal_pts,
    "durga": _singh_pts, "maadurga": _singh_pts, "singh": _singh_pts,
    "saraswati": _veena_pts, "veena": _veena_pts, "bajana": _veena_pts,
    "diya": _diya_pts, "diyas": _diya_pts, "lamp": _diya_pts,
    "om": _om_pts, "omkar": _om_pts, "aum": _om_pts,
}

# ── Big emoji shape library ────────────────────────────────────────────────
# Windows' Segoe UI Emoji gives us ~150 ready-made outline drawings (animals,
# food, fruit, veg, nature, things). We raster the glyph, turn its alpha into
# stardust points, cache it, and stamp it in the galaxy field exactly like the
# hand-drawn shapes above. Word → emoji (English + Hinglish keys, plus
# no-space phrases like "icecream" / "friedchicken").
_EMOJI_FONT_NAME = "segoeuiemoji"
_emoji_cache = {}          # emoji char -> (numpy points) | None   (rendered once)
_EMOJI_MAP = {
    # ── emotions / feelings
    "heart": "\u2764", "dil": "\u2764",
    "gularma": "\U0001F496",
    "love": "\U0001F496", "ishq": "\U0001F496", "glasses": "\U0001F495",
    "brokenheart": "\U0001F494", "tutadil": "\U0001F494",
    "smile": "\U0001F60A", "haso": "\U0001F60A", "happy": "\U0001F600",
    "laugh": "\U0001F602", "hansi": "\U0001F602", "sad": "\U0001F622",
    "udaas": "\U0001F622", "cry": "\U0001F62D", "rona": "\U0001F62D",
    "angry": "\U0001F621", "gusssa": "\U0001F621", "cool": "\U0001F60E",
    "wow": "\U0001F62E", "shock": "\U0001F62E", "wink": "\U0001F609",
    "kiss": "\U0001F618", "chu": "\U0001F618",
    # ── animals (wild + domestic)
    "dog": "\U0001F436", "kutta": "\U0001F436", "kutte": "\U0001F436",
    "cat": "\U0001F431", "billi": "\U0001F431",
    "tiger": "\U0001F42F", "bagh": "\U0001F42F",
    "lion": "\U0001F981", "sher": "\U0001F981", "simba": "\U0001F981",
    "elephant": "\U0001F418", "haathi": "\U0001F418",
    "monkey": "\U0001F435", "bandar": "\U0001F435", "bunder": "\U0001F435",
    "cow": "\U0001F42E", "gaay": "\U0001F42E", "gai": "\U0001F42E",
    "bull": "\U0001F402", "saand": "\U0001F402", "bael": "\U0001F402",
    "buffalo": "\U0001F403", "bhains": "\U0001F403",
    "horse": "\U0001F434", "ghoda": "\U0001F434", "ghora": "\U0001F434",
    "donkey": "\U0001F40F", "gadha": "\U0001F40F",
    "camel": "\U0001F42A", "unt": "\U0001F42A",
    "pig": "\U0001F437", "suar": "\U0001F437", "suar ka": "\U0001F437",
    "rabbit": "\U0001F430", "khargosh": "\U0001F430", "kharghosh": "\U0001F430",
    "mouse": "\U0001F42D", "chuha": "\U0001F42D", "chuaha": "\U0001F42D",
    "rat": "\U0001F401", "ru": "\U0001F401", "chuhe": "\U0001F401",
    "bear": "\U0001F43B", "rechchha": "\U0001F43B", "bhallu": "\U0001F43B",
    "panda": "\U0001F43C", "panda": "\U0001F43C",
    "fox": "\U0001F98A", "lomdi": "\U0001F98A",
    "wolf": "\U0001F43A", "bhediya": "\U0001F43A",
    "deer": "\U0001F98C", "hiran": "\U0001F98C",
    "sheep": "\U0001F411", "bhed": "\U0001F411",
    "goat": "\U0001F410", "bakri": "\U0001F410", "bakra": "\U0001F410",
    "rooster": "\U0001F413", "murga": "\U0001F413",
    "hen": "\U0001F414", "murghi": "\U0001F414", "murgi": "\U0001F414",
    "chicken": "\U0001F414", "murg": "\U0001F414",
    "bird": "\U0001F426", "panchi": "\U0001F426", "pankhi": "\U0001F426",
    "sparrow": "\U0001F424", "gauraiya": "\U0001F424",
    "parrot": "\U0001F99C", "tota": "\U0001F99C", "tota": "\U0001F99C",
    "owl": "\U0001F989", "ullu": "\U0001F989",
    "eagle": "\U0001F985", "garud": "\U0001F985", "baaz": "\U0001F985",
    "peacock": "\U0001F422", "more": "\U0001F422", "mor": "\U0001F422",
    "pigeon": "\U0001F426", "kabootar": "\U0001F426",
    "duck": "\U0001F986", "battakh": "\U0001F986",
    "swan": "\U0001F9A2", "hans": "\U0001F9A2",
    "penguin": "\U0001F427", "penguin": "\U0001F427",
    "frog": "\U0001F438", "medhak": "\U0001F438", "mendak": "\U0001F438",
    "toad": "\U0001F438", "maindak": "\U0001F438",
    "snake": "\U0001F40D", "saap": "\U0001F40D", "naagin": "\U0001F40D",
    "lizard": "\U0001F98E", "chhipkali": "\U0001F98E",
    "crocodile": "\U0001F40A", "magarmach": "\U0001F40A",
    "turtle": "\U0001F422", "kachhua": "\U0001F422", "kachua": "\U0001F422",
    "fish": "\U0001F41F", "machli": "\U0001F41F",
    "dolphin": "\U0001F42C", "dolphin": "\U0001F42C",
    "whale": "\U0001F433", "wahil": "\U0001F433",
    "shark": "\U0001F988", "shark": "\U0001F988",
    "octopus": "\U0001F419", "waqu": "\U0001F419",
    "crab": "\U0001F980", "kekra": "\U0001F980",
    "shrimp": "\U0001F990", "jhinga": "\U0001F990",
    "snail": "\U0001F40C", "ghongha": "\U0001F40C",
    "butterfly": "\U0001F98B", "titli": "\U0001F98B",
    "bee": "\U0001F41D", "madhukhi": "\U0001F41D", "bhau": "\U0001F41D",
    "ant": "\U0001F41C", "chinti": "\U0001F41C",
    "spider": "\U0001F577", "makdi": "\U0001F577", "mawri": "\U0001F577",
    "scorpion": "\U0001F982", "bichoo": "\U0001F982",
    # ── food
    "pizza": "\U0001F355", "pizza": "\U0001F355",
    "burger": "\U0001F354", "burger": "\U0001F354",
    "hotdog": "\U0001F32D", "hotdog": "\U0001F32D",
    "sandwich": "\U0001F96A", "sbsandwich": "\U0001F96A",
    "fries": "\U0001F35F", "fry": "\U0001F35F", "chips": "\U0001F35F",
    "noodles": "\U0001F35C", "chowmein": "\U0001F35C",
    "rice": "\U0001F35A", "chaawal": "\U0001F35A", "chawal": "\U0001F35A",
    "curry": "\U0001F35B", "sabzi": "\U0001F35B",
    "bread": "\U0001F35E", "roti": "\U0001F35E", "double": "\U0001F35E",
    "egg": "\U0001F373", "anda": "\U0001F373", "nasta": "\U0001F373",
    "cheese": "\U0001F9C0", "paneer": "\U0001F9C0",
    "butter": "\U0001F9C8", "makkhan": "\U0001F9C8",
    "milk": "\U0001F95B", "doodh": "\U0001F95B",
    "tea": "\U0001F375", "chai": "\U0001F375", "chay": "\U0001F375",
    "coffee": "\u2615", "kafi": "\u2615", "kaffee": "\u2615",
    "juice": "\U0001F9C3", "juice": "\U0001F9C3", "ras": "\U0001F9C3",
    "water": "\U0001F4A7", "paani": "\U0001F4A7", "pani": "\U0001F4A7",
    "icecream": "\U0001F368", "kulfi": "\U0001F368", "icecream": "\U0001F368",
    "cake": "\U0001F370", "cake": "\U0001F370",
    "cupcake": "\U0001F9C1", "cupcake": "\U0001F9C1",
    "donut": "\U0001F369", "donut": "\U0001F369",
    "cookie": "\U0001F36A", "biscuit": "\U0001F36A",
    "chocolate": "\U0001F36B", "choklate": "\U0001F36B",
    "candy": "\U0001F36C", "toffee": "\U0001F36C",
    "popcorn": "\U0001F37F", "popcorn": "\U0001F37F",
    "taco": "\U0001F32E", "taco": "\U0001F32E",
    "sushi": "\U0001F363", "sushi": "\U0001F363",
    # ── fruit
    "apple": "\U0001F34E", "seb": "\U0001F34E", "sebl": "\U0001F34E",
    "banana": "\U0001F34C", "kela": "\U0001F34C", "kelaa": "\U0001F34C",
    "mango": "\U0001F96D", "aam": "\U0001F96D", "amo": "\U0001F96D",
    "orange": "\U0001F34A", "santra": "\U0001F34A", "santara": "\U0001F34A",
    "grapes": "\U0001F347", "angoor": "\U0001F347",
    "watermelon": "\U0001F349", "tarbooz": "\U0001F349",
    "strawberry": "\U0001F353", "strawberry": "\U0001F353",
    "cherry": "\U0001F352", "cherry": "\U0001F352",
    "peach": "\U0001F351", "arhu": "\U0001F351",
    "pear": "\U0001F350", "nashpati": "\U0001F350",
    "pineapple": "\U0001F34D", "ananas": "\U0001F34D",
    "lemon": "\U0001F34B", "nimbu": "\U0001F34B",
    "coconut": "\U0001F965", "nariyal": "\U0001F965", "narial": "\U0001F965",
    "kiwi": "\U0001F95D", "kiwi": "\U0001F95D",
    # ── vegetables
    "tomato": "\U0001F345", "tamatar": "\U0001F345",
    "carrot": "\U0001F955", "gajar": "\U0001F955",
    "potato": "\U0001F954", "aloo": "\U0001F954", "aalu": "\U0001F954",
    "corn": "\U0001F33D", "makai": "\U0001F33D", "earli": "\U0001F33D",
    "cucumber": "\U0001F952", "kheera": "\U0001F952",
    "broccoli": "\U0001F966", "broccoli": "\U0001F966",
    "mushroom": "\U0001F344", "kumbh": "\U0001F344",
    "eggplant": "\U0001F346", "baingan": "\U0001F346",
    "chilli": "\U0001F336", "mirch": "\U0001F336", "mirchi": "\U0001F336",
    "bellpepper": "\U0001FAD1", "shimlamirch": "\U0001FAD1",
    "garlic": "\U0001F9C4", "lahsun": "\U0001F9C4",
    "onion": "\U0001F9C5", "pyaaz": "\U0001F9C5",
    "peanut": "\U0001F95C", "moong": "\U0001F95C", "elaachi": "\U0001F95C",
    # ── nature
    "sun": "\u2600", "suraj": "\u2600",
    "moon": "\U0001F319", "chand": "\U0001F319", "chaand": "\U0001F319",
    "star": "\u2b50", "sitara": "\u2b50", "taara": "\u2b50",
    "sparkle": "\u2728", "chamak": "\u2728",
    "cloud": "\u2601", "baadal": "\u2601", "badal": "\u2601",
    "rain": "\U0001F327", "baarish": "\U0001F327",
    "rainbow": "\U0001F308", "indradhanush": "\U0001F308",
    "snow": "\u2744", "barf": "\u2744",
    "lightning": "\u26a1", "bijli": "\u26a1",
    "wave": "\U0001F30A", "samundar": "\U0001F30A", "lahar": "\U0001F30A",
    "earth": "\U0001F30D", "duniya": "\U0001F30D", "globe": "\U0001F30D",
    "volcano": "\U0001F30B", "volcano": "\U0001F30B",
    "mountain": "\u26f0", "pahaad": "\u26f0",
    "cactus": "\U0001F335", "kaktus": "\U0001F335",
    "leaf": "\U0001F343", "patta": "\U0001F343",
    "palm": "\U0001F334", "ped": "\U0001F334",
    "tree": "\U0001F333", "ped": "\U0001F333",
    "rose": "\U0001F339", "gulaab": "\U0001F339",
    "tulip": "\U0001F337", "tulip": "\U0001F337",
    "flower": "\U0001F33B", "phool": "\U0001F33B", "fool": "\U0001F33B",
    # ── things
    "house": "\U0001F3E0", "ghar": "\U0001F3E0", "makaan": "\U0001F3E0",
    "car": "\U0001F697", "gaadi": "\U0001F697", "gadi": "\U0001F697",
    "bus": "\U0001F68C", "bastar": "\U0001F68C", "bus": "\U0001F68C",
    "truck": "\U0001F69A", "truck": "\U0001F69A",
    "bike": "\U0001F6B2", "cycle": "\U0001F6B2", "sayaikal": "\U0001F6B2",
    "plane": "\u2708", "aeroplane": "\u2708", "jahaaz": "\u2708",
    "rocket": "\U0001F680", "rocket": "\U0001F680",
    "ship": "\U0001F6A2", "jahaaz": "\U0001F6A2",
    "boat": "\u26f5", "naav": "\u26f5", "nauka": "\u26f5",
    "train": "\U0001F682", "rail": "\U0001F682", "trens": "\U0001F682",
    "phone": "\U0001F4F1", "mobile": "\U0001F4F1", "phaln": "\U0001F4F1",
    "laptop": "\U0001F4BB", "computer": "\U0001F4BB",
    "book": "\U0001F4DA", "kitab": "\U0001F4DA",
    "pencil": "\u270f", "pen": "\U0001F58A", "kalam": "\u270f",
    "key": "\U0001F511", "chaabi": "\U0001F511",
    "lock": "\U0001F512", "tala": "\U0001F512",
    "bulb": "\U0001F4A1", "lightbulb": "\U0001F4A1",
    "battery": "\U0001F50B", "battery": "\U0001F50B",
    "paisa": "\U0001F4B0", "money": "\U0001F4B0", "paise": "\U0001F4B0",
    "gift": "\U0001F381", "tohfa": "\U0001F381",
    "crown": "\U0001F451", "taj": "\U0001F451",
    "ring": "\U0001F48D", "anguthi": "\U0001F48D",
    "watch": "\u231a", "ghadi": "\u231a",
    "camera": "\U0001F4F7", "camera": "\U0001F4F7",
    "game": "\U0001F3AE", "gamepad": "\U0001F3AE",
    "music": "\U0001F3B5", "song": "\U0001F3B5", "gaana": "\U0001F3B5",
    "guitar": "\U0001F3B8", "guitar": "\U0001F3B8",
    "balloon": "\U0001F388", "balloon": "\U0001F388",
    "crown": "\U0001F451", "trophy": "\U0001F3C6",
    "umbrella": "\u2602", "chhatri": "\u2602",
    "fire": "\U0001F525", "aag": "\U0001F525",
}

# phrase fallback: some foods are really two words in Hinglish ("ice cream",
# "hot dog", "bell pepper") — collapsed below into single keys already; this
# extra pass catches a few more spoken combos after whitespace remooval.
_PHRASE_FIX = {
    "icecream": "\U0001F368", "hotdog": "\U0001F32D",
    "bellpepper": "\U0001FAD1", "friedchicken": "\U0001F357",
}


def _emoji_stamp(emoji):
    """Raster a Segoe UI Emoji glyph into stardust points (cached)."""
    got = _emoji_cache.get(emoji)
    if got is not None:
        return None if got is None else got.copy()
    try:
        font = pygame.font.SysFont(_EMOJI_FONT_NAME, 44)
        surf = font.render(emoji, True, (255, 255, 255))
        alpha = pygame.surfarray.array_alpha(surf)
        mask = np.asarray(alpha, np.int16) > 55
        xs, ys = np.nonzero(mask)
        if xs.size == 0:
            _emoji_cache[emoji] = None
            return None
        h, w = mask.shape
        pad_mask = np.zeros((h + 2, w + 2), dtype=bool)
        pad_mask[1:-1, 1:-1] = mask
        pts = [np.stack((xs + 1, ys + 1), axis=-1)]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                x2, y2 = np.nonzero(pad_mask[1 + dx:1 + dx + h, 1 + dy:1 + dy + w])
                pts.append(np.stack((x2 + 1, y2 + 1), axis=-1))
        out = np.concatenate(pts, axis=0).astype(np.float64)
        out = _stamp(out)          # normalise into the shape box, sample <=4200
        _emoji_cache[emoji] = out if out is not None else None
        return None if out is None else out.copy()
    except Exception:
        _emoji_cache[emoji] = None
        return None


def _shape_stamp(word):
    """Return stardust points for a word — hand-drawn parametric shapes first,
    then the big emoji library (animals, food, fruit, veg, nature, things)."""
    w = word.replace("_", " ").strip().lower()
    fn = _SHAPE_MAP.get(w)
    if fn is not None:
        try:
            return _stamp(fn())
        except Exception:
            return None
    emoji = _EMOJI_MAP.get(w) or _PHRASE_FIX.get(w.replace(" ", ""))
    if emoji:
        return _emoji_stamp(emoji)
    return None


# ── Ambient dust-motes (top band, slow drift) ─────────────────────────────
AM = 60
ax = np.zeros(AM, np.float64)
ay = np.zeros(AM, np.float64)
avx = np.zeros(AM, np.float64)
avy = np.zeros(AM, np.float64)
ah = np.zeros(AM, np.float64)
aph = np.zeros(AM, np.float64)
acol = np.zeros((AM, 3), np.uint8)


def mote_init(W, H):
    rng = np.random.default_rng(11)
    ah[:] = rng.random(AM)
    aph[:] = rng.random(AM)
    ax[:] = rng.random(AM) * W
    ay[:] = rng.random(AM) * H * 0.40
    avx[:] = 0.0
    for i in range(AM):
        acol[i] = _shade((52, 62, 92), ah[i])


def mote_update(t, dt, W, H, level):
    g = min(1.6, dt * 60.0)
    avx[:] = np.sin(t * 0.22 + aph[:] * 6.283) * 0.020 * g
    ay[:] += (0.010 + 0.03 * ah) * (0.7 + 0.8 * level) * g
    ax[:] += avx[:]
    m = ay[:] > H * 0.42
    if m.any():
        ay[m] = 0.0
        ax[m] = aph[m] * W
    np.clip(ax, 0, W - 1, out=ax)
    np.clip(ay, 0, H - 1, out=ay)


def mote_draw(screen):
    W, H = screen.get_width(), screen.get_height()
    pa = pygame.surfarray.pixels3d(screen)
    xi = np.rint(ax).astype(np.int32)
    yi = np.rint(ay).astype(np.int32)
    np.clip(xi, 0, W - 1, out=xi)
    np.clip(yi, 0, H - 1, out=yi)
    pa[xi, yi] = acol
    del pa


# ── Shooting stars (rare, slow) ───────────────────────────────────────────
ME = 10
mx = np.zeros(ME, np.float64)
my = np.zeros(ME, np.float64)
mvx = np.zeros(ME, np.float64)
mvy = np.zeros(ME, np.float64)
m_vis = np.zeros(ME, np.bool_)
m_next = 6.0


def meteors_init():
    global m_next
    m_vis[:] = False
    m_next = 5.0


def meteors_update(t, dt, W, H, level):
    global m_next
    g = min(1.6, dt * 60.0)
    if t >= m_next:
        cnt = 1 + (1 if level > 0.5 else 0)
        for _ in range(cnt):
            for k in range(ME):
                if not m_vis[k]:
                    m_vis[k] = True
                    mx[k] = W * random.uniform(0.10, 0.90)
                    my[k] = H * random.uniform(0.03, 0.36)
                    direc = 1.0 if random.random() < 0.5 else -1.0
                    mvx[k] = direc * random.uniform(2.6, 5.5)
                    mvy[k] = random.uniform(1.0, 2.6)
                    break
        m_next = t + random.uniform(7.0, 14.0)
    for k in range(ME):
        if not m_vis[k]:
            continue
        mx[k] += mvx[k] * g
        my[k] += mvy[k] * g
        if mx[k] < -12 or mx[k] > W + 12 or my[k] < -12 or my[k] > H + 12:
            m_vis[k] = False


def meteors_draw(screen):
    W, H = screen.get_width(), screen.get_height()
    pa = pygame.surfarray.pixels3d(screen)
    for k in range(ME):
        if not m_vis[k]:
            continue
        for l in range(6):
            xi = int(round(mx[k] - mvx[k] * l * 0.7))
            yi = int(round(my[k] - mvy[k] * l * 0.7))
            if 0 <= xi < W and 0 <= yi < H:
                f = 1.0 - l * 0.17
                pa[xi, yi] = (int(255 * f), int(250 * f), int(238 * f))
    del pa


# ── Window / app ──────────────────────────────────────────────────────────
def _load_cfg():
    try:
        with open(os.path.join(BASE, "config.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def glow_surf(col, size):
    c = math.ceil(size)
    s = pygame.Surface((c * 2 + 1,) * 2, pygame.SRCALPHA)
    for r in range(int(size), 0, -1):
        pygame.draw.circle(s, (*col, int(88 * (1 - r / size) ** 2.2)), (c, c), r)
    return s


class Visual:
    def __init__(self, state_file=None):
        cfg = _load_cfg()
        v = cfg.get("visualizer", {})
        self.STATE_FILE = state_file or os.path.join(
            cfg.get("data_path", r"D:\YOUR_DATA_PATH"), "ak_vis_state.json")
        self.theme = v.get("theme", "sand")
        if self.theme not in THEME_HUE:
            self.theme = "sand"
        self.fps = max(20, int(v.get("fps", 50)))
        self.mic_enabled = bool(v.get("mic", True))
        self.state = "idle"
        self.custom_key = None
        self.forced = False
        self.frame = 0
        self.W, self.H = 1100, 620
        self.screen = pygame.display.set_mode((self.W, self.H), pygame.RESIZABLE)
        pygame.display.set_caption("AK · Cosmos Tide Visualizer")
        self.clock = pygame.time.Clock()
        self._bg = None
        self._glowc = None
        self._glow = None
        self._field_surf = None
        self._lag = None
        self._last_poll = 0.0
        self._tag = None
        self._tag_surf = None
        self._layout_dirty = True
        self.level = 0.0
        self._rms = 0.0
        self._lvl_lock = threading.Lock()
        self._mic_stop = threading.Event()
        self._mic_stream = None
        self._mic_thread = None
        self._prev_state = None
        self._t_now = 0.0
        self.ev = None
        self.cap_on = False
        self.cap_text = ""
        self.cap_words = []
        self.cap_t0 = 0.0
        self.cap_dur = 0.0
        self.cap_on = False
        self.cap_k = -1
        self.cap_text = ""
        self.cap_plan = []
        self.cap_queue = []
        self.cap_i = 0
        self.cap_roll = None
        self.cap_phase = None
        self.cap_pts = None
        self.cap_src = None
        self.cap_style = "live"
        # ── live caption channels (UDP bus) ───────────────────────
        cc = cfg.get("visualizer", {}).get("captions", {})
        self.cap_stt_on = bool(cc.get("enabled", True))
        self.cap_udp = bool(cc.get("udp", True))
        self.cap_udp_port = int(cc.get("udp_port", 46321))
        self._cap_queue = queue.Queue()
        self._cap_sub = None
        # ── animation styles + speech reaction ───────────────────
        self.anim_idx = 0
        self.anim_next = -1
        self.anim_blend = 0.0
        self.anim_rate = 1.0 / 1.1
        # AK_VIS_ANIM=ORB|TIDE|VORTEX|BLOOM|RIVER|STORM|PRISM starts on a look
        _pick = (os.environ.get("AK_VIS_ANIM") or "").strip().upper()
        if _pick in ANIM_NAMES:
            self.anim_idx = ANIM_NAMES.index(_pick)
        # ── live voice styles (Gemini-style orb, universe, dots, ...) ──
        self.live = LiveStage(self.W, self.H)
        self.future = FutureHUD(self.W, self.H)
        if os.environ.get("AK_VIS_NO_HUD") == "1":
            self.future.set_enabled(False)
        self._live_style = ""
        self._fade = 1.0
        self._peak = 0.0
        self._bands = np.zeros(N_BANDS, np.float64)
        self._bands_s = np.zeros(N_BANDS, np.float64)
        self._centroid = 0.0
        self._phrase = ""
        # phrase -> colour (whatever is spoken recolours the whole field)
        self.accent = np.array(_hsv_rgb(40.0, 0.55, 1.0), np.float64)
        self._accent_target = self.accent.copy()
        self._phrase_hue = 40.0
        self._hue_target = 40.0
        self._last_phrase = ""
        self._phrase_seq = -1
        self._phrase_t0 = 0.0
        # speech reaction
        self._prev_level = 0.0
        self._pulse = 0.0
        self._speaking = False
        self.waves = []
        # in-window button
        self._btn_font = None
        self._btn_surf = None
        self._btn_key = None
        self._btn_body = None
        self._btn_body_key = None
        self._btn_name_key = None
        self._btn_name_surf = None
        self._fps_acc = 0.0
        self._fps_n = 0
        self._last_bg = 0.0
        self._btn_rect = None
        self._btn_hot = False
        self._hint_surf = None
        self._hint_key = None

    def is_foreground(self):
        if os.environ.get("AK_VIS_FORCE_FG"):
            return True
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            hw = pygame.display.get_wm_info().get("window")
            return hw is None or fg == hw
        except Exception:
            return True

    def start_mic(self):
        if not _SD or self._mic_stop.is_set():
            return
        self._mic_stop.clear()
        self._mic_stream = None

        def _loop():
            try:
                stream = sd.InputStream(samplerate=16000, channels=1,
                                        dtype="float32", blocksize=1024)
                self._mic_stream = stream
                with stream:
                    while not self._mic_stop.is_set():
                        try:
                            data, _ = stream.read(1024, blocking=False)
                        except sd.PortAudioError:
                            time.sleep(0.003)
                            continue
                        arr = np.asarray(data, dtype=np.float32)
                        if arr.size < 8:
                            continue
                        r = float(np.sqrt(np.mean(arr ** 2)))
                        bands, centroid = _band_spectrum(arr)
                        with self._lvl_lock:
                            self._rms += (r - self._rms) * 0.3
                            self._bands = bands
                            self._centroid = centroid
            except Exception:
                pass
            finally:
                self._mic_stream = None

        self._mic_thread = threading.Thread(target=_loop, daemon=True)
        self._mic_thread.start()

    def stop_mic(self):
        """Cleanly tear down the level-meter mic thread so sounddevice's
        atexit handler never races a still-open stream (caused random
        heap-corruption exits on Windows). The stream is closed by the
        thread that owns it, never from another thread."""
        self._mic_stop.set()
        th = getattr(self, "_mic_thread", None)
        if th is not None:
            self._mic_thread = None
            th.join(timeout=2.0)

    def layout(self):
        self._layout_dirty = True

    def _build_bg(self):
        bg = pygame.Surface((self.W, self.H))
        bg.fill(BG_DIM)
        rng = random.Random(9)
        for _ in range(220):
            sx = rng.randrange(0, self.W)
            sy = rng.randrange(0, self.H - 2)
            b = rng.randrange(20, 56)
            bg.set_at((sx, sy), (b, b, max(30, b + 5)))
        self._bg = bg.convert()

    def read_state(self):
        try:
            # utf-8-sig strips a UTF-8 BOM (some tools add one) so json.load
            # never chokes — with BOM it used to silently fall back to "idle"
            # and captions never appeared.
            with open(self.STATE_FILE, encoding="utf-8-sig") as fh:
                d = json.load(fh)
            return (d.get("mode") or "idle", d.get("text") or "",
                    float(d.get("dur") or 0.0), d.get("phrase") or "",
                    int(d.get("phrase_seq") or 0))
        except Exception:
            return "idle", "", 0.0, "", 0

    def set_state(self, st):
        st = "idle" if st == "completed" else st
        if st.startswith("custom:"):
            key = st.split(":", 1)[1] or ""
            self.custom_key = key or self.custom_key
            st = "custom"
        elif st != "custom":
            self.custom_key = None
        if st == self.state and not self.forced:
            return
        if st != self.state:
            self._prev_state = self.state
        self.state = st
        pygame.display.set_caption("AK · Cosmos Tide Visualizer — " + st.upper())

    @property
    def in_custom(self):
        return self.state == "custom" and bool(self.custom_key)

    def color_for(self, st):
        return STATE_LIGHT.get(st, STATE_LIGHT["idle"])

    def update_level(self, dt):
        """Live audio level + speech-onset detection.

        The level now reacts to BOTH sides of the conversation: the user
        (state "listening") and AK (state "responding"). The first frame of
        an utterance fires a shockwave through the dust.
        """
        if self.state in ("responding", "listening"):
            with self._lvl_lock:
                target = min(1.0, self._rms * 6.0)
                raw = self._bands
            if target > 0.04:
                self.level += (target - self.level) * min(1.0, dt * 9.0)
            else:
                self.level += (0.0 - self.level) * min(1.0, dt * 2.2)
            self._bands_s += (np.asarray(raw, np.float64) - self._bands_s) \
                * min(1.0, dt * 13.0)
        else:
            self.level += (0.0 - self.level) * min(1.0, dt * 2.0)
            self._bands_s += (np.zeros(N_BANDS, np.float64) - self._bands_s) \
                * min(1.0, dt * 4.0)

        # peak envelope: snaps up on a syllable, falls away slowly - this is
        # what makes the live styles punch instead of just wobble
        p = self.level
        if p > self._peak:
            self._peak += (p - self._peak) * min(1.0, dt * 26.0)
        else:
            self._peak += (p - self._peak) * min(1.0, dt * 3.4)

        # hysteresis: one wave per utterance, not one per frame
        if self.level > 0.11 and not self._speaking:
            self._speaking = True
            self._pulse = min(1.0, self._pulse + 0.55 + 0.5 * self.level)
            self.spawn_wave(self.W * 0.5, self.H * 1.02,
                            amp=0.40 + 0.55 * self.level, speed=520.0)
        elif self.level < 0.055 and self._speaking:
            self._speaking = False
        self._pulse *= max(0.0, 1.0 - dt * 1.6)

    # ── speech reaction: colour, waves, pulses ────────────────────────────
    def spawn_wave(self, cx, cy, amp=0.6, speed=520.0, col=None):
        self.waves.append({
            "t0": self._t_now, "cx": cx, "cy": cy, "amp": amp,
            "speed": speed, "r": 0.0,
            "col": np.array(self.accent if col is None else col, np.float64),
        })
        if len(self.waves) > 6:
            self.waves.pop(0)

    def update_waves(self, dt):
        """Grow the shockwaves and shove the dust outward where they pass."""
        g = min(1.6, dt * 60.0)
        keep = []
        for w in self.waves:
            w["r"] = (self._t_now - w["t0"]) * w["speed"]
            if w["r"] >= max(self.W, self.H) * 1.3:
                continue
            dx = px - w["cx"]
            dy = py - w["cy"]
            d = np.hypot(dx, dy) + 1e-3
            band = np.abs(d - w["r"])
            hit = band < 30.0
            if hit.any():
                push = (1.0 - band[hit] / 30.0) * w["amp"] * 1.6
                vx[hit] += (dx[hit] / d[hit]) * push * g
                vy[hit] += (dy[hit] / d[hit]) * push * g
            keep.append(w)
        self.waves = keep

    def on_phrase(self, text, mode="listening"):
        """A new phrase was spoken -> recolour the whole field and bloom it."""
        t = (text or "").strip()
        if not t:
            return
        # the same words can arrive from two channels (live caption bus +
        # state file) - don't fire twice for one utterance
        if t == self._last_phrase and (self._t_now - self._phrase_t0) < 1.5:
            return
        self._last_phrase = t
        self._phrase_t0 = self._t_now
        self._phrase = t
        self._phrase_hue = _text_hue(t)
        self._hue_target = self._phrase_hue
        base = np.array(self.color_for(mode), np.float64)
        tgt = np.array(_hsv_rgb(self._phrase_hue, 0.66, 1.0), np.float64)
        self._accent_target = base * 0.28 + tgt * 0.72
        self.spawn_wave(self.W * 0.5, self.H * 1.02, amp=0.55, speed=600.0)
        self._pulse = min(1.0, self._pulse + 0.8)

    def update_accent(self, dt):
        """Ease the field colour toward the current phrase colour."""
        names = [ANIM_NAMES[i] for i in (self.anim_idx, self.anim_next)
                 if i >= 0]
        if "PRISM" in names:                       # PRISM keeps rotating
            rot = _hsv_rgb(self._hue_target + self._t_now * 14.0, 0.66, 1.0)
            base = np.array(self.color_for(self.state), np.float64)
            self._accent_target = np.array(rot, np.float64) * 0.75 + base * 0.25
        elif "ORB" in names:
            # the live styles keep a slow colour drift so they never look dead
            drift = self._hue_target + self._t_now * 4.0
            rot = _hsv_rgb(drift, 0.58, 1.0)
            base = np.array(self.color_for(self.state), np.float64)
            self._accent_target = np.array(rot, np.float64) * 0.72 + base * 0.28
        k = min(1.0, dt * 3.4)
        self.accent += (self._accent_target - self.accent) * k

    # ── animation style switching ─────────────────────────────────────────
    def set_anim(self, idx):
        idx = int(idx) % len(ANIM_NAMES)
        if idx == self.anim_idx or idx == self.anim_next:
            return
        self.anim_next = idx
        self.anim_blend = 0.0
        self.anim_rate = 1.0 / 1.1
        self._fade = 1.0

    def _style_kind(self, idx):
        """(kind, payload) for an ANIM_NAMES index.

        kind = 'live' -> payload is the live stage name (the voice orb)
        kind = 'sand' -> payload is the old sand-style number (0..5)
        kind = 'auto' -> AUTO: the universe when quiet, the orb when talking
        """
        if idx < 0:
            return ("none", None)
        name = ANIM_NAMES[idx]
        if name == "AUTO":
            return ("auto", None)
        if name in LIVE_NAMES:
            return ("live", name)
        return ("sand", SAND_INDEX.get(name, 0))

    def _effective_live(self):
        """AUTO: the universe (the sand field) when nothing happens, the
        voice orb as soon as somebody talks."""
        if self.level > 0.06 or self._peak > 0.12:
            return "ORB"
        return ""

    def _live_hue(self):
        """The colour of the live styles: mostly the voice itself, a little
        the meaning of the words, a touch of the phrase hash."""
        try:
            with self._lvl_lock:
                cent = self._centroid
            return smart_hue(cent, self._phrase, self._hue_target)
        except Exception:
            return self._hue_target

    def cycle_anim(self):
        nxt = (self.anim_idx + 1) % len(ANIM_NAMES)
        if self.anim_next >= 0:
            nxt = (self.anim_next + 1) % len(ANIM_NAMES)
        self.set_anim(nxt)

    def update_anim(self, dt):
        if self.anim_next >= 0:
            self.anim_blend = min(1.0, self.anim_blend + self.anim_rate * dt)
            if self.anim_blend >= 1.0:
                self.anim_idx, self.anim_next, self.anim_blend = self.anim_next, -1, 0.0

    def anim_mix(self):
        """(current_style, next_style, blend) - blend is smoothstepped."""
        if self.anim_next < 0:
            return self.anim_idx, -1, 0.0
        b = self.anim_blend
        b = b * b * (3.0 - 2.0 * b)
        return self.anim_idx, self.anim_next, b

    # ── in-window UI: the ANIM switch button ─────────────────────────────
    def _ui_font(self, size=15):
        if self._btn_font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            for name in ("segoeui", "tahoma", "arial"):
                try:
                    self._btn_font = pygame.font.SysFont(name, size)
                    break
                except Exception:
                    continue
            if self._btn_font is None:
                self._btn_font = pygame.font.SysFont(None, size)
        return self._btn_font

    def _btn_label(self):
        name = ANIM_NAMES[self.anim_next] if self.anim_next >= 0 \
            else ANIM_NAMES[self.anim_idx]
        if self._btn_key != name:
            self._btn_key = name
            self._btn_surf = self._ui_font().render("ANIM", True,
                                                    (236, 241, 255))
        return self._btn_surf

    def _btn_geometry(self):
        """The animation switch plate - aligned with the HUD column."""
        surf = self._btn_label()
        w = 178
        h = 34
        self._btn_rect = pygame.Rect(self.W - w - 36, 36, w, h)
        return surf

    def draw_ui(self):
        """The animation switch plate (top-right) + the key hints.

        Same design language as the HUD: chamfered dark glass, a hairline
        edge, an accent bar and letterspaced micro type.
        """
        self._btn_geometry()
        r = self._btn_rect
        hue = self._live_hue()
        hot = self._btn_hot
        body = pygame.Surface(r.size, pygame.SRCALPHA)
        pts = _cut_path(pygame.Rect(0, 0, r.w, r.h), 10)
        pygame.draw.polygon(body, (9, 11, 17, 150 if not hot else 185), pts)
        edge_a = 70 if not hot else 150
        pygame.draw.polygon(body, hsv(hue, 0.35, 0.90, edge_a), pts, 1)
        pygame.draw.line(body, hsv(hue, 0.60, 1.0, 150 if not hot else 230),
                         pts[0], pts[1], 2)
        # the switch icon - a small arc that keeps turning
        cx, cy = 19, r.h // 2
        ang = (self._t_now % 2.4) / 2.4 * 6.283
        pygame.draw.arc(body, hsv(hue, 0.45, 0.95, 220),
                        pygame.Rect(cx - 9, cy - 9, 18, 18),
                        ang, ang + 4.2, 2)
        pygame.draw.polygon(body, hsv(hue, 0.45, 0.95, 220),
                            [(cx + 9, cy - 8), (cx + 9, cy - 1), (cx + 2, cy - 5)])
        self.screen.blit(body, r.topleft)

        name = ANIM_NAMES[self.anim_next] if self.anim_next >= 0 \
            else ANIM_NAMES[self.anim_idx]
        self.screen.blit(self._btn_label(), (r.x + 38, r.centery - 8))
        if self._btn_name_key != name:
            self._btn_name_key = name
            self._btn_name_surf = pygame.font.SysFont("consolas", 13).render(
                name, True, (150, 200, 255) if name == "ORB" else (206, 216, 238))
        self.screen.blit(self._btn_name_surf,
                         (r.right - 12 - self._btn_name_surf.get_width(),
                          r.centery - self._btn_name_surf.get_height() // 2))

        hint = "TAB ANIM   T THEME   E BURST   H HUD   ESC CLOSE"
        if self._hint_key != hint:
            self._hint_key = hint
            self._hint_surf = self._ui_font(11).render(hint, True,
                                                        (104, 114, 138))
        self.screen.blit(self._hint_surf, (36, self.H - 22))

    def new_event(self, at_t):
        types = ["galaxy"] * 3 + ["nebula"] * 2 + ["cluster"] * 2 + \
                ["ring"] * 2 + ["spiral", "planets", "wave"] + \
                ["blackhole", "burst", "ripple", "comet"]
        tint = {
            "galaxy": (255, 208, 120), "nebula": (186, 148, 255),
            "cluster": (208, 224, 255), "blackhole": (255, 138, 90),
            "burst": (255, 160, 74), "ring": (158, 224, 255),
            "ripple": (148, 224, 255), "comet": (255, 228, 160),
            "spiral": (255, 190, 110), "planets": (170, 236, 255),
            "wave": (150, 226, 234),
        }
        typ = random.choice(types)
        return {
            "type": typ,
            "t0": at_t,
            "dur": random.uniform(6.0, 9.5),
            "cx": self.W * random.uniform(0.36, 0.64),
            "cy": self.H * random.uniform(0.40, 0.50),
            "size": min(self.W, self.H) * random.uniform(0.26, 0.36),
            "tint": tint[typ],
        }

    # ── Soft field renderer: slow glowing trails in a float buffer ──────
    def _ensure_field(self):
        if (self._field_surf is None or self._lag is None
                or self._field_surf.get_size() != (self.W, self.H)):
            self._field_surf = pygame.Surface((self.W, self.H))
            self._lag = np.zeros((self.W, self.H, 3), np.float32)

    def draw_field(self, t, level, ev, acc):
        W, H = self.W, self.H
        self._ensure_field()
        if self._field_surf.get_size() != (W, H) or self._lag.shape[:2] != (W, H):
            self._field_surf = pygame.Surface((W, H))
            self._lag = np.zeros((W, H, 3), np.float32)
        lag = self._lag
        lag *= 0.955
        acc2 = np.asarray(acc, np.float64)
        if ev is not None and ev["t0"] <= t < ev["t0"] + ev["dur"]:
            acc2 = acc2 * 0.60 + np.asarray(ev.get("tint", acc), np.float64) * 0.40
        depth = np.clip((H - py) / max(1.0, H), 0.0, 1.0)
        tw = 0.5 + 0.5 * np.sin(t * 1.2 + h3 * 6.283)
        eg = np.clip(0.16 + 0.48 * level + 0.16 * depth + 0.07 * tw, 0.0, 1.0)
        spark = (level > 0.3) & (h3 > 0.955) & (tw > 0.6)
        if spark.any():
            eg[spark] = np.maximum(eg[spark], 1.0)
        if ev is not None and ev["t0"] <= t < ev["t0"] + ev["dur"]:
            r = np.hypot((px - ev["cx"]) / (1.2 * ev["size"]),
                         (py - ev["cy"]) / (1.2 * ev["size"]))
            eg += np.clip(1.0 - r, 0.0, 1.0) * 0.22
        # ── speech shockwaves: a bright ring of light sweeping the dust ──
        rings = []
        for w in self.waves:
            if w["r"] < 1.0:
                continue
            dd = np.hypot(px - w["cx"], py - w["cy"])
            ring = np.exp(-((dd - w["r"]) ** 2) / (2.0 * 22.0 ** 2))
            rings.append((ring, w))
            eg += ring * w["amp"] * 0.46
        np.clip(eg, 0.0, 1.0, out=eg)
        cf = col * (0.45 + 0.55 * eg[:, None]) + acc2 * eg[:, None]
        for ring, w in rings:
            cf += w["col"][None, :] * ring[:, None] * (w["amp"] * 0.55)
        np.clip(cf, 0.0, 255.0, out=cf)
        okm = (px >= 0.0) & (px <= W - 1) & (py >= 0.0) & (py <= H - 1)
        xi = np.rint(px).astype(np.int32)
        yi = np.rint(py).astype(np.int32)
        np.clip(xi, 0, W - 1, out=xi)
        np.clip(yi, 0, H - 1, out=yi)
        pos = (xi[okm], yi[okm])
        np.add.at(lag, pos, cf[okm])
        xr = xi[okm] + 1
        xr = np.clip(xr, 0, W - 1)
        np.add.at(lag, (xr, yi[okm]), cf[okm] * 0.28)
        u8 = np.clip(lag, 0.0, 255.0).astype(np.uint8)
        # the spoken phrase owns the colour: a per-channel tint over the
        # trails keeps the structure but moves the whole field to the mood
        # of whatever was just said
        tint = (np.clip(self.accent, 0, 255) / 255.0).astype(np.float32)
        u8f = u8.astype(np.float32)
        u8f *= (0.30 + 0.85 * tint).astype(np.float32)
        u8 = np.clip(u8f, 0.0, 255.0).astype(np.uint8)
        pygame.surfarray.blit_array(self._field_surf, u8)
        self.screen.blit(self._field_surf, (0, 0))

    # ── Caption engine (particle-foam text; words rise slowly) ──────────
    def caption_init(self):
        rng = np.random.default_rng(7)
        ch1[:] = rng.random(CM)
        ch2[:] = rng.random(CM)
        ch3[:] = rng.random(CM)
        cpx[:] = self.W * (0.04 + 0.92 * ch1)
        cpy[:] = self.H + 120.0 + 0.06 * ch2 * 100.0
        cvx[:] = 0.0
        cvy[:] = 0.1 + 0.6 * ch3
        self.caption_rest()

    def caption_rest(self):
        # no resting pile: idle text particles sink below the screen so the
        # field stays clean and the word design has no lingering background
        ctx[:] = self.W * (0.30 + 0.40 * ch1)
        cty[:] = self.H + 120.0 + ch2 * 90.0
        cvx[:] = 0.0
        cvy[:] = 0.2 + 0.8 * ch3
        self.caption_tint((26, 30, 48))

    def caption_tint(self, base):
        for i in range(CM):
            csrc[i, 0] = base[0] * (0.90 + 0.22 * ch1[i])
            csrc[i, 1] = base[1] * (0.90 + 0.22 * ch1[i])
            csrc[i, 2] = base[2] * (0.90 + 0.22 * ch1[i])

    def caption_base(self):
        # vivid word colours chosen to stand apart from BOTH the background
        # and the particle-field accent, so speech is instantly readable
        if self.state == "responding":
            base = (255, 236, 72)       # electric yellow while talking
        elif self.state == "listening":
            base = (215, 252, 255)      # ice-white while hearing you
        elif self.state == "thinking":
            base = (255, 128, 216)      # hot pink while thinking
        elif self.state == "processing":
            base = (186, 255, 118)      # sour lime while working
        else:
            base = (234, 240, 255)
        # 25% of the live phrase colour keeps the words in the same mood
        # as the dust field
        acc = tuple(int(v) for v in self.accent)
        return tuple(int(base[i] * 0.74 + acc[i] * 0.26) for i in range(3))

    def caption_pulse(self):
        # very gentle breathing — brightness stays ~constant while the word
        # is on screen so the letters remain crisp and easy to read
        pulse = 0.94 + 0.05 * np.sin(self._t_now * 1.4 + ch3[:] * 6.283)
        pulse = pulse + 0.12 * self.level            # brighter while speaking
        lit = np.clip(csrc * pulse[:, None], 0, 255)
        # live phrase colour mixed into the words (so the caption always
        # matches the mood of the dust)
        acc = (np.clip(self.accent, 0, 255) * 0.32).astype(np.float32)
        ccol[:] = np.clip(lit * 0.86 + acc[None, :], 0, 255).astype(np.uint8)

    def caption_layout(self, text):
        words = re.findall(r"\S+", text)
        if not words:
            self.cap_plan = []
            return
        # Custom shape showcase: when AK gets a shape request it stamps that
        # word big and centred (universe hidden) — shown until the next phrase.
        if self.in_custom and self.custom_key:
            pts = None
            if self.custom_key in _LOGO_WORDS:
                pts = _logo_pts(self.custom_key, self.W, self.H, CM)
            else:
                pts = _big_shape_pts(self.custom_key, self.W, self.H, CM)
            if pts is not None and pts.size:
                self.cap_plan = [{
                    "pts": pts, "x": 0, "y": 0,
                    "w": self.W, "h": self.H, "shape": True, "logo": True,
                    "custom": True,
                }]
                return
        # Full-window logo: a single big letter (e.g. 'K') fills the ENTIRE
        # window as stardust — AK's signature, shown big, not in the caption box.
        plain = re.sub(r"[^a-zA-Z]", "", text).lower()
        if len(words) == 1 and plain in _LOGO_WORDS and len(plain) <= 2:
            pts = _logo_pts(plain, self.W, self.H, CM)
            if pts is not None and pts.size:
                self.cap_plan = [{
                    "pts": pts, "x": 0, "y": 0,
                    "w": self.W, "h": self.H, "shape": True, "logo": True,
                }]
                return
        fs = 44
        maxw = int(self.W * 0.60)               # text stays in a compact centre
        budget = int(self.H * 0.22)             # the caption stays clear of the HUD plates
        line_h = lambda f: max(10, int(52 * f / 44.0))

        def wrap_at(f):
            bl = []
            ln = []
            lw = 0.0
            for w in words:
                key = re.sub(r"[^a-zA-Z]", "", w).lower()
                ww = (_shape_w if _shape_stamp(key) is not None
                      else max(16, int(f * 0.62 * max(2, len(w)))))
                if ln and lw + ww + 14 > maxw:
                    bl.append(ln)
                    ln = []
                    lw = 0.0
                ln.append(w)
                lw += ww + 14
            if ln:
                bl.append(ln)
            return bl

        # POORA paragraph: agar zyada lines hain toh font automatically chhota
        # hokar sabko ek saath fit kar deta hai — koi bhi line nahi kategi.
        baselines = wrap_at(fs)
        while len(baselines) * line_h(fs) > budget and fs > 14:
            fs = max(14, int((budget / max(1, len(baselines))) / (52 / 44.0)))
            baselines = wrap_at(fs)
        plan = []
        ycur = int(self.H * 0.70)
        lh = line_h(fs)
        for ln in baselines:
            width = sum((_shape_w if _shape_stamp(re.sub(r"[^a-zA-Z]", "", w).lower()) is not None
                         else max(16, int(fs * 0.62 * max(2, len(w))))) + 14
                        for w in ln) - 14
            x0 = int((self.W - width) / 2)
            for w in ln:
                key = re.sub(r"[^a-zA-Z]", "", w).lower()
                pts = _shape_stamp(key)
                if pts is not None:
                    pts = pts.astype(np.float64)
                    rows = SHAPE_H
                    plan.append({
                        "pts": pts, "x": x0, "y": ycur + rows - 30,
                        "w": SHAPE_W, "h": rows, "shape": True,
                    })
                    x0 += SHAPE_W + 14
                    continue
                pts = _raster_pts(w, fs, CM)
                if pts is None or pts.size == 0:
                    continue
                rows = int(fs * 1.1)
                plan.append({
                    "pts": pts, "x": x0, "y": ycur + rows - 30,
                    "w": fs * 0.62 * len(w), "h": rows,
                })
                x0 += int(fs * 0.62 * len(w)) + 14
            ycur -= lh
        self.cap_plan = plan

    def caption_set(self, text, dur=0.0, style=None):
        """Particle-only caption (foam). Bade text ko sentences me tod kar EK
        EK karke flow karta hai: sentence reveal hota hai, phir door drift
        hota hai, phir agla sentence flow-in. Sirf particles — koi text nahi."""
        text = text or ""
        self.cap_text = text
        self.cap_plan = []
        self.cap_phase = None
        self.cap_i = 0
        if style == "live":
            # paragraph -> sentence queue (rolling)
            parts = [p.strip()
                     for p in re.split(r"(?<=[.!?\u0964\u2026;:])\s+|[\n\r]+", text)
                     if p.strip()]
            self.cap_queue = parts or ([text] if text else [])
            total = sum(len(s) for s in self.cap_queue) or 1
            base = (dur if (dur and dur > 0.5)
                    else sum(_word_dur(w) for w in self.cap_words) + 1.2)
            # per-sentence SLOTS jo total base (= voice length) ke barabar sum
            # karein: hold(0.35s)+out(0.55s) bhi reveal ke 'end_at' se isi slot
            # ke andar rehta hai, toh para ka poora flow voice ke saath-saath
            # khatam hota hai.
            slots = []
            for s in self.cap_queue:
                slots.append(max(0.5, base * len(s) / total))
            self.cap_roll = slots
            cur = self.cap_queue[0]
            self.cap_words = re.findall(r"\S+", cur)
            if self.cap_words:
                self.caption_layout(cur)
            self.cap_dur = self.cap_roll[0]
            self.cap_phase = "reveal"
        else:
            # shapes/custom: whole thing at once, poori baat on screen forever
            self.cap_words = re.findall(r"\S+", text)
            self.cap_queue = [text]
            self.cap_roll = None
            if self.cap_words:
                self.caption_layout(text)
            self.cap_dur = 10 ** 9
        self.cap_t0 = self._t_now
        self.cap_style = "live"
        self.cap_on = bool(self.cap_words)
        self.cap_k = -1
        self.cap_pts = None

    def caption_index(self, elapsed):
        """-> index of the last word to reveal at `elapsed` seconds into the
        sentence (voice-synced across the caption duration)."""
        acc = 0.0
        natural = sum(_word_dur(w) for w in self.cap_words) or 1.0
        scale = self.cap_dur / natural if self.cap_dur > 0 else 1.0
        for i, w in enumerate(self.cap_words):
            acc += _word_dur(w) * scale
            if elapsed < acc:
                return max(i - 1, 0)
        return max(len(self.cap_words) - 1, 0)

    def caption_full(self, max_items=None):
        """Show the caption as particles. With max_items = the number of words
        to reveal, phrases write themselves word-by-word (left→right) in sync
        with the voice; None shows everything at once. Snaps instantly."""
        plan = self.cap_plan
        if max_items is not None and max_items < len(plan):
            plan = plan[:max_items]
        if not plan:
            self.cap_pts = None
            return
        pieces = []
        total = 0
        for item in plan:
            pts = np.asarray(item["pts"], dtype=np.float64)
            if pts.ndim != 2 or pts.shape[1] < 2:
                continue
            pts = pts[:, :2].copy()
            pts[:, 0] += item["x"]
            pts[:, 1] += item["y"]
            pieces.append(pts)
            total += pts.shape[0]
        if total == 0:
            self.cap_pts = None
            return
        merged = np.concatenate(pieces, axis=0)
        # Whole phrase must FIT in the particle pool (CM). Each word's glyph
        # raster can be bigger than its fair share — downsample globally so
        # EVERY word of the phrase survives instead of only the first one.
        if merged.shape[0] > CM:
            idx = np.linspace(0, merged.shape[0] - 1, CM)
            idx = np.unique(np.rint(idx).astype(np.int64))
            merged = merged[idx]
        n = min(CM, merged.shape[0])
        ctx[:n] = merged[:n, 0]
        cty[:n] = merged[:n, 1]
        if n < CM:
            ctx[n:] = self.W * (0.30 + 0.40 * ch1[n:])
            cty[n:] = self.H + 120.0 + ch2[n:] * 90.0
        self.cap_pts = n
        # Snap particle positions straight onto the phrase glyphs — no slow
        # rise, no drifting spring. Full phrase is instantly readable as
        # particles and just shimmers in place.
        cpx[:] = ctx[:]
        cpy[:] = cty[:]
        cvx[:] = 0.0
        cvy[:] = 0.0
        self.caption_tint(self.caption_base())
        ymax = max(1.0, float(np.max(merged[:n, 1])))
        top = np.clip(1.0 - merged[:n, 1] / ymax, 0.0, 1.0)
        kk = (0.30 + 0.70 * top)[:, None]
        csrc[:n] += (255.0 - csrc[:n]) * kk

    def caption_update(self, dt):
        if self.cap_on and self.cap_plan:
            nw = len(self.cap_plan)
            if self.cap_phase == "reveal":
                # VOICE-SYNCED REVEAL: words write themselves left→right across
                # this sentence's talk-time, like a flowing stream of particles.
                el = max(0.0, self._t_now - self.cap_t0)
                end_at = self.cap_dur
                if (self.cap_roll is not None
                        and self.cap_i < len(self.cap_queue) - 1
                        and end_at > 1.1):
                    end_at -= 0.9          # hold+out isi sentence ke slot me
                k_show = nw if (self.cap_roll is None or el >= end_at) \
                    else self.caption_index(el) + 1
                if k_show > self.cap_k:
                    self.cap_k = k_show
                    self.caption_full(k_show)
                if self.cap_roll is not None and k_show >= nw:
                    if self.cap_i >= len(self.cap_queue) - 1:
                        self.cap_phase = "final"     # last sentence: hamesha rahe
                        self.cap_dur = 10 ** 9
                    else:
                        self.cap_phase = "hold"      # thoda ruke, phir nikal jaaye
                        self.cap_t0 = self._t_now
            elif self.cap_phase == "hold":
                if self._t_now - self.cap_t0 >= 0.35:
                    self.cap_phase = "out"           # old sentence drift-away
                    self.cap_t0 = self._t_now
            elif self.cap_phase == "out":
                el = self._t_now - self.cap_t0
                dim = 1.0 - 0.94 * min(1.0, el / 0.55)
                csrc[:] *= dim                       # fade out
                cty[:] -= self.H * 1.1 * dt          # whole sentence floats up
                cpx[:] = ctx[:]
                cpy[:] = cty[:]
                cvx[:] = 0.0
                cvy[:] = 0.0
                if el >= 0.55:
                    self.cap_i += 1
                    s = self.cap_queue[self.cap_i]
                    self.cap_words = re.findall(r"\S+", s)
                    self.cap_dur = self.cap_roll[self.cap_i]
                    self.caption_layout(s)
                    self.cap_k = -1
                    self.cap_pts = None
                    self.cap_t0 = self._t_now
                    self.cap_phase = "reveal"        # agla sentence flow-in
            else:
                # 'final' ya non-live (shapes): poori baat on screen, no fade
                if self.cap_dur >= 10 ** 8 and self.cap_k != nw:
                    self.cap_k = nw
                    self.caption_full()
        elif self.cap_k != -1 or (self.cap_on and not self.cap_plan):
            self.cap_k = -1
            self.cap_on = False
            self.cap_pts = None
            self.caption_rest()

        g = dt * 60.0
        if g > 1.6:
            g = 1.6
        if getattr(self, "cap_k", -1) <= 0 and not self.cap_on:
            return                       # no caption - no physics needed
        k = 0.22 * g
        cvx[:] = (cvx[:] + (ctx[:] - cpx[:]) * k) * 0.88
        cvy[:] = (cvy[:] + (cty[:] - cpy[:]) * k * 0.85) * 0.88
        a = self._t_now * 0.9 + ch3[:] * 6.283
        b = self._t_now * 0.7 + ch2[:] * 6.283
        # tiny controlled shimmer, not a visible wobble
        cvx[:] += np.sin(a) * 0.010 * g
        cvy[:] += np.cos(b) * 0.010 * g
        cpx[:] += cvx[:] * g
        cpy[:] += cvy[:] * g
        np.clip(cvx, -6, 6, out=cvx)
        np.clip(cvy, -6, 6, out=cvy)
        np.clip(cpx, 0, self.W - 1, out=cpx)
        np.clip(cpy, -40, self.H + 300, out=cpy)

    def draw_caption(self, screen):
        # nothing on screen - the whole pixels3d pass would be wasted
        if getattr(self, "cap_k", -1) <= 0:
            return
        # clean neon-light text: only the word's own particles on-screen, with
        # an even halo outline and an accent pass that keeps letters readable
        W, H = screen.get_width(), screen.get_height()
        pa = pygame.surfarray.pixels3d(screen)
        xi = np.rint(cpx).astype(np.int32)
        yi = np.rint(cpy).astype(np.int32)
        np.clip(xi, 0, W - 1, out=xi)
        np.clip(yi, 0, H - 1, out=yi)
        okm = (cpx >= 0) & (cpx <= W - 1) & (cpy >= 0) & (cpy <= H - 1)
        dw = (ccol[okm] * 0.22).astype(np.uint8)      # dark rim colour
        bright = ccol[okm]
        # strong dark outline ring (4 directions) → letters pop off the field
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)):
            xr = xi[okm] + dx
            yr = yi[okm] + dy
            np.clip(xr, 0, W - 1, out=xr)
            np.clip(yr, 0, H - 1, out=yr)
            pa[xr, yr] = dw
        # bright core stroke
        pa[xi[okm], yi[okm]] = bright
        # a brighter accent on every 6th particle thickens the strokes cleanly
        m = okm & ((xi + yi) % 6 == 0)
        x2 = xi[m]
        y2 = yi[m] + 1
        np.clip(x2, 0, W - 1, out=x2)
        np.clip(y2, 0, H - 1, out=y2)
        pa[x2, y2] = (ccol[m] * 0.70).astype(np.uint8)
        del pa

    # ── Live caption channels (UDP bus) ─────────────────────────────────
    def _push_cap(self, ev):
        self._cap_queue.put_nowait(ev)

    def start_captions(self):
        """Open the caption channel: loopback UDP bus so external producers
        (main.py / demo) can stream words. Captions appear as particles."""
        if not self.cap_stt_on:
            return
        if self._cap_sub is None and self.cap_udp:
            try:
                _bus = _ak_import("caption_bus")
                self._cap_sub = _bus.CaptionSubscriber(port=self.cap_udp_port)
            except Exception:  # noqa: BLE001
                self._cap_sub = None

    def stop_captions(self):
        if self._cap_sub is not None:
            try:
                self._cap_sub.close()
            except Exception:  # noqa: BLE001
                pass
        self._cap_sub = None

    def _apply_cap_event(self, ev):
        """Apply one caption event (UDP bus or in-process STT). Runs on the
        visualizer's main thread while draining — no cross-thread rendering.

        Heard words feed ONLY the particle-foam caption, live."""
        e = ev.get("e")
        if e == "partial":
            t = (ev.get("text") or "").strip()
            if t and t != self.cap_text:
                self.caption_set(t, style="live")
                self.cap_src = "bus"
                # the very first fragment of an utterance already wakes the
                # field up (colour + shockwave) - the final phrase recolours
                if len(t.split()) >= 2 and (self._t_now - self._phrase_t0) > 1.5:
                    self.on_phrase(t, "listening")
        elif e == "final":
            t = (ev.get("text") or "").strip()
            if t:
                self.caption_set(t, style="live")
                self.cap_src = "bus"
                self.on_phrase(t, "listening")
        elif e == "reset":
            self.caption_rest()
            self.cap_src = None
        self._last_phrase = ""
        self._phrase_seq = -1
        self._phrase_t0 = 0.0

    def _drain_caption_bus(self):
        """Called every frame — cheap: UDP poll + thread-safe queue drain."""
        if self._cap_sub is not None:
            while True:
                ev = self._cap_sub.poll()
                if ev is None:
                    break
                self._apply_cap_event(ev)
        while True:
            try:
                ev = self._cap_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_cap_event(ev)

    # ── Main loop ──────────────────────────────────────────────────────
    def _handle_events(self):
        """Window events. Returns True when the window must close."""
        for evp in pygame.event.get():
            if evp.type == pygame.QUIT:
                return True
            elif evp.type == pygame.VIDEORESIZE:
                if evp.size[0] < 640 or evp.size[1] < 400:
                    continue
                self.W, self.H = evp.size
                self.screen = pygame.display.set_mode((self.W, self.H),
                                                      pygame.RESIZABLE)
                try:
                    self.live.resize(self.W, self.H)
                except Exception:
                    pass
                self.layout()
                self._bg = None
                self._field_surf = None
                self._lag = None
                if self.cap_on or self.cap_k != -1:
                    self.cap_plan = []
                    self.cap_k = -1
                    self.cap_pts = None
                    if self.cap_text:
                        self.caption_layout(self.cap_text)
                else:
                    self.caption_rest()
            elif evp.type == pygame.MOUSEBUTTONDOWN and evp.button == 1:
                if self._btn_rect and self._btn_rect.collidepoint(evp.pos):
                    self.cycle_anim()
            elif evp.type == pygame.MOUSEMOTION:
                hot = bool(self._btn_rect and
                           self._btn_rect.collidepoint(evp.pos))
                if hot != self._btn_hot:
                    self._btn_hot = hot
                    try:
                        pygame.mouse.set_cursor(
                            pygame.SYSTEM_CURSOR_HAND if hot
                            else pygame.SYSTEM_CURSOR_ARROW)
                    except Exception:
                        pass
            elif evp.type == pygame.KEYDOWN:
                if evp.key in (pygame.K_q, pygame.K_ESCAPE):
                    return True
                elif evp.key in (pygame.K_0, pygame.K_a):
                    self.forced = False
                    self.set_anim(0)          # AUTO: universe + live orb
                elif evp.key == pygame.K_l:
                    # cycle only the live voice styles
                    names = [i for i, n in enumerate(ANIM_NAMES)
                             if n in LIVE_NAMES]
                    cur = self.anim_next if self.anim_next >= 0 else self.anim_idx
                    if cur in names:
                        nxt = names[(names.index(cur) + 1) % len(names)]
                    else:
                        nxt = names[0]
                    self.set_anim(nxt)
                elif evp.key == pygame.K_e:
                    self.ev = self.new_event(self._t_now)
                elif evp.key == pygame.K_TAB:
                    self.cycle_anim()
                elif pygame.K_1 <= evp.key <= pygame.K_5:
                    self.forced = True
                    self.set_state(("idle", "listening", "thinking",
                                    "processing", "responding")[
                                   evp.key - pygame.K_1])
                elif evp.key == pygame.K_h:
                    self.future.set_enabled(not self.future.enabled)
                elif evp.key == pygame.K_t:
                    ks = list(THEME_HUE)
                    self.theme = ks[(ks.index(self.theme) + 1) % len(ks)]
                    make_colors(self.theme)
        return False

    def run(self, test_frames=0):
        self.layout()
        font = pygame.font.SysFont("consolas", 15)
        if self.mic_enabled and os.environ.get("AK_VIS_NO_MIC") != "1":
            self.start_mic()
        init_particles()
        make_colors(self.theme)
        self.caption_init()
        self.start_captions()
        self._shot_done = False
        self._run_t0 = time.time()
        mote_init(self.W, self.H)
        meteors_init()
        t0 = time.time()
        last_t = t0
        frames = 0

        while True:
            if self._handle_events():
                return

            now = time.time()
            self._t_now = now
            if now - self._last_poll >= 0.18:
                self._last_poll = now
                if not self.forced:
                    mode, txt, aud_dur, phrase, seq = self.read_state()
                    self.set_state(mode)
                    if mode.startswith("custom:"):
                        key = mode.split(":", 1)[1] or ""
                        if key and (self.cap_text != key or self.cap_k == -1):
                            self.caption_set(key)
                            self.cap_src = "custom"
                            self.cap_dur = 10 ** 9     # hold until next phrase
                    elif txt and mode in ("responding", "listening"):
                        if txt != self.cap_text:
                            self.caption_set(txt, aud_dur, style="live")
                            self.cap_src = "state"
                    elif not txt and (self.cap_on or self.cap_k != -1):
                        if self.cap_src == "state":
                            self.cap_on = False
                            self.cap_k = -1
                            self.cap_pts = None
                            self.cap_text = ""
                            self.cap_src = None
                            self.caption_rest()
                    # the sticky phrase: whatever was spoken last owns the
                    # colour of the whole field (seq = new utterance, even
                    # when the same words are repeated)
                    if phrase and seq != self._phrase_seq:
                        self._phrase_seq = seq
                        self.on_phrase(phrase, mode)
            if not self.is_foreground():
                # The window is behind something else - but it must never
                # look frozen, so keep drawing, just at a calmer pace.
                self.clock.tick(40)
                if now - self._last_bg < (1.0 / 12.0):
                    continue
                self._last_bg = now
                dt = min(0.09, max(0.0001, now - last_t))
                last_t = now
                self.frame += 1
            else:
                # the real time this frame took - not the lag against the
                # target, or the whole scene would drift when the machine
                # cannot keep up
                dt = min(0.09, max(0.0001, now - last_t))
                last_t = now
                self.frame += 1
                self.clock.tick(self.fps)

            self.update_level(dt)
            self.update_anim(dt)
            self.update_accent(dt)
            self.update_waves(dt)

            if self.ev is None:
                self.ev = self.new_event(now + random.uniform(3.0, 5.0))
            elif now >= self.ev["t0"] + self.ev["dur"]:
                self.ev = self.new_event(now + random.uniform(6.0, 11.0))

            cur, nxt, blend = self.anim_mix()
            cur_kind = self._style_kind(cur)
            nxt_kind = self._style_kind(nxt)

            # ── which renderer runs this frame? ───────────────────
            # AUTO: the sand universe while nothing happens, the voice orb
            # as soon as somebody talks.
            live_name = ""
            if cur_kind[0] == "live":
                live_name = cur_kind[1]
            elif cur_kind[0] == "auto":
                live_name = self._effective_live()
            elif nxt_kind[0] in ("live", "auto"):
                live_name = (nxt_kind[1] if nxt_kind[0] == "live"
                             else self._effective_live())
            mode_key = live_name or ("sand%d" % (cur_kind[1] or 0)
                                     if cur_kind[0] == "sand" else "auto")
            if mode_key != self._live_style:
                self._live_style = mode_key
                if live_name:
                    self.live.active = live_name
                self._fade = 1.0

            if live_name:
                self.live.update(dt, self.level, self._peak, self._bands_s,
                                 self._centroid, self._live_hue(), self.state,
                                 self._speaking, self._phrase)
                if not self.live.draw(self.screen):
                    live_name = ""
                    self._live_style = ""

            if not live_name:
                if self.ev["t0"] <= now < self.ev["t0"] + self.ev["dur"]:
                    compute_event_targets(now, self.ev, self.W, self.H)
                    step_morph(dt)
                else:
                    step_drift(dt, now, self.W, self.H, self.level)
                # AUTO without a voice falls back to the first sand style
                cur_sand = cur_kind[1] if cur_kind[0] == "sand" else (
                    0 if cur_kind[0] == "auto" else None)
                nxt_sand = nxt_kind[1] if nxt_kind[0] == "sand" else (
                    0 if nxt_kind[0] == "auto" else None)
                if cur_sand is not None:
                    step_style(dt, now, self.W, self.H, self.level,
                               cur_sand, 1.0 - blend, self._pulse)
                if nxt_sand is not None:
                    step_style(dt, now, self.W, self.H, self.level,
                               nxt_sand, blend, self._pulse)

            self.caption_update(dt)
            self.caption_pulse()
            if not self.in_custom:
                mote_update(now - t0, dt, self.W, self.H, self.level)
                meteors_update(now - t0, dt, self.W, self.H, self.level)

            if self._bg is None or self._layout_dirty:
                self._build_bg()
                self._layout_dirty = False

            if not live_name:
                self.screen.blit(self._bg, (0, 0))
                col = tuple(int(v) for v in self.accent)
                if self._glowc != col or self._glow is None:
                    self._glowc = col
                    self._glow = glow_surf(_lerp(col, (255, 255, 255), 0.22),
                                           int(self.W * 0.42))
                g = int(self.W * 0.42)
                self.screen.blit(self._glow,
                                 (self.W // 2 - g, int(self.H * 0.86) - g))

            if not self.in_custom and not live_name:
                acc = np.array(
                    self.accent * 0.86 + np.array((255, 255, 255), np.float64) * 0.14,
                    np.float64)
                self.draw_field(now, self.level, self.ev, acc)
                mote_draw(self.screen)
                meteors_draw(self.screen)
            self.draw_caption(self.screen)
            self.draw_ui()
            # ── the futuristic instrument layer ─────────────────────
            try:
                self.future.set_mouse(pygame.mouse.get_pos())
                self.live.set_mouse(pygame.mouse.get_pos())
            except Exception:
                pass
            self.future.update(dt, self.level, self._peak, self._bands_s,
                               self._centroid, self._live_hue(), self.state,
                               self._phrase, 1.0 / max(dt, 1e-4))
            if live_name:
                self.future.draw(self.screen, self.W * 0.5, self.H * 0.5,
                                 min(self.W, self.H) * 0.30)
            else:
                self.future.draw(self.screen)
            self._drain_caption_bus()
            # cinematic fade-in whenever the look changes
            if self._fade > 0.001:
                self._fade = max(0.0, self._fade - dt * 1.7)
                veil = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
                veil.fill((0, 0, 0, int(255 * self._fade)))
                self.screen.blit(veil, (0, 0))
            if not getattr(self, "_shot_done", False):
                _shot = os.environ.get("AK_VIS_SHOT")
                if _shot:
                    _p, _sep, _t = _shot.partition(";")
                    if _sep and now >= self._run_t0 + float(_t or 0):
                        try:
                            pygame.image.save(self.screen, _p)
                        except Exception:
                            pass
                        self._shot_done = True

            if os.environ.get("AK_VIS_DEBUG"):
                evn = self.ev["type"] if self.ev["t0"] <= now else "rain"
                tag = (f"{self.state.upper()} · {self.theme} · {evn} · "
                       f"{int(1.0/max(dt,1e-4))}fps")
                if tag != self._tag:
                    self._tag = tag
                    self._tag_surf = font.render(tag, True, (120, 130, 156))
                self.screen.blit(self._tag_surf, (14, self.H - 24))
            pygame.display.flip()

            # a tiny live health file - handy when the window feels slow
            self._fps_acc += dt
            self._fps_n += 1
            if self._fps_acc >= 2.0:
                live_fps = self._fps_n / max(1e-3, self._fps_acc)
                self._fps_acc = 0.0
                self._fps_n = 0
                try:
                    out = os.path.join(os.path.dirname(self.STATE_FILE),
                                       "ak_vis_fps.json")
                    with open(out, "w", encoding="utf-8") as fh:
                        json.dump({"fps": round(live_fps, 1),
                                   "quality": round(self.future._perf, 2),
                                   "hud_ms": round(self.future._cost * 1000, 1),
                                   "ts": time.time()}, fh)
                except Exception:
                    pass

            frames += 1
            if test_frames and frames >= test_frames:
                return


def main():
    pygame.init()
    vis = Visual()
    pid_file = os.path.join(os.path.dirname(vis.STATE_FILE), "ak_vis.pid")
    try:
        with open(pid_file, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pid_file = ""
    try:
        vis.run(int(os.environ.get("AK_VIS_TEST_FRAMES", "0")))
    finally:
        vis.stop_mic()
        vis.stop_captions()
        pygame.quit()
        if pid_file:
            try:
                os.remove(pid_file)
            except Exception:
                pass


if __name__ == "__main__":
    main()