# AK face portrait
# The photo is taken apart: the pixels become a cloud of particles, the
# facial landmarks become a vector skeleton, and both are rebuilt every
# single frame. The face is not a picture here - it is alive. It keeps the
# shape of the photo exactly: no emotions, no expressions, no mood warps.
# Only the mouth moves while he talks and the eyes blink on their own.
import hashlib
import json
import math
import os

import numpy as np
import pygame

try:
    from .vis_core import Emitter, Glow, _hsv
except ImportError:      # run as a plain script
    from vis_core import Emitter, Glow, _hsv

# ── the regions of a face ────────────────────────────────────────────────
SKIN, EYE_L, EYE_R, MOUTH, BROW, OTHER = 0, 1, 2, 3, 4, 5

_IMAGE = "ak_portrait.png"
_MODEL = "face_landmarker.task"
_CACHE_VERSION = "v4"          # bump it when the anchors change

# the standard media-pipe skeleton, kept as plain index pairs
_OVAL = [(10, 338), (338, 297), (297, 332), (332, 284), (284, 251),
         (251, 389), (389, 356), (356, 454), (454, 323), (323, 361),
         (361, 288), (288, 397), (397, 365), (365, 379), (379, 378),
         (378, 400), (400, 377), (377, 152), (152, 148), (148, 176),
         (176, 149), (149, 150), (150, 136), (136, 172), (172, 58),
         (58, 132), (132, 93), (93, 234), (234, 127), (127, 162),
         (162, 21), (21, 54), (54, 103), (103, 67), (67, 109), (109, 10)]

_LIPS_OUT = [(61, 146), (146, 91), (91, 181), (181, 84), (84, 17),
             (17, 314), (314, 405), (405, 321), (321, 375), (375, 291),
             (291, 409), (409, 270), (270, 269), (269, 267), (267, 0),
             (0, 37), (37, 39), (39, 40), (40, 185), (185, 61)]

_LIPS_IN = [(78, 191), (191, 80), (80, 81), (81, 82), (82, 13), (13, 312),
            (312, 311), (311, 310), (310, 415), (415, 308), (308, 324),
            (324, 318), (318, 402), (402, 317), (317, 14), (14, 87),
            (87, 178), (178, 88), (88, 95), (95, 78)]

_EYE_L = [(362, 382), (382, 381), (381, 380), (380, 374), (374, 373),
          (373, 390), (390, 249), (249, 263), (263, 466), (466, 388),
          (388, 387), (387, 386), (386, 385), (385, 384), (384, 398),
          (398, 362)]

_EYE_R = [(33, 7), (7, 163), (163, 144), (144, 145), (145, 153),
          (153, 154), (154, 155), (155, 133), (133, 173), (173, 157),
          (157, 158), (158, 159), (159, 160), (160, 161), (161, 246),
          (246, 33)]

_BROW_L = [(336, 296), (296, 334), (334, 293), (293, 300), (300, 276),
           (276, 283), (283, 282), (282, 295), (295, 285), (285, 336)]

_BROW_R = [(70, 63), (63, 105), (105, 66), (66, 107), (107, 55),
           (55, 65), (65, 52), (52, 53), (53, 46), (46, 70)]

_CONTOURS = [("oval", _OVAL), ("lips", _LIPS_OUT), ("lips_in", _LIPS_IN),
             ("eye_l", _EYE_L), ("eye_r", _EYE_R),
             ("brow_l", _BROW_L), ("brow_r", _BROW_R)]


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def portrait_path():
    """Where the photo of AK lives."""
    env = os.environ.get("AK_VIS_PORTRAIT")
    if env and os.path.exists(env):
        return env
    p = os.path.join(_here(), _IMAGE)
    return p if os.path.exists(p) else None


def _ids(pairs):
    return sorted({i for p in pairs for i in p})


def _landmark_regions():
    """landmark index -> region id"""
    reg = np.full(478, OTHER, np.int8)
    for pairs, rid in ((_OVAL, SKIN), (_LIPS_OUT, MOUTH), (_LIPS_IN, MOUTH),
                       (_EYE_L, EYE_L), (_EYE_R, EYE_R),
                       (_BROW_L, BROW), (_BROW_R, BROW)):
        for a, b in pairs:
            reg[a] = rid
            reg[b] = rid
    for i in range(468, 478):          # the irises
        reg[i] = EYE_L if i < 473 else EYE_R
    return reg


_LM_REG = _landmark_regions()


def _detect(rgb):
    """Run the face landmarker over the photo -> (points, blendshapes)."""
    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    model = os.path.join(_here(), _MODEL)
    if not os.path.exists(model):
        raise RuntimeError("no face model")
    base = mpp.BaseOptions(model_asset_path=model)
    opts = vision.FaceLandmarkerOptions(
        base_options=base, num_faces=1, output_face_blendshapes=True,
        output_facial_transformation_matrixes=False)
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        img = mp.Image(image_format=mp.ImageFormat.SRGB,
                       data=np.ascontiguousarray(rgb))
        res = lm.detect(img)
        if not res.face_landmarks:
            raise RuntimeError("no face found")
        pts = np.array([(l.x * rgb.shape[1], l.y * rgb.shape[0])
                        for l in res.face_landmarks[0]], np.float64)
        shapes = {}
        if res.face_blendshapes:
            for b in res.face_blendshapes[0]:
                shapes[b.category_name] = float(b.score)
        return pts, shapes


class SoftGlow:
    """A premultiplied radial glow.

    An additive blit ignores the alpha of the source, so the light has to
    be baked into the colour itself - otherwise the halo burns a hole in
    the picture.
    """

    def __init__(self, size=256, gain=0.34):
        self.size = size
        base = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size / 2.0
        for r in range(size // 2, 0, -1):
            a = int(255 * (1.0 - (r / (size / 2.0)) ** 2.1) ** 1.7 * gain)
            pygame.draw.circle(base, (a, a, a, a), (int(c), int(c)), r)
        self.base = base
        self.out = base.copy()
        self.tint = pygame.Surface((size, size))
        self._col = None

    def get(self, col):
        if self._col != col:
            out = self.base.copy()
            self.tint.fill(col)
            out.blit(self.tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
            self.out = out
            self._col = col
        return self.out


class Portrait:

    """The photo, reduced to particles and a skeleton."""

    def __init__(self, pts, rgb, reg, contours, anchors, shapes=None,
                 detail=None, shade=None):
        self.pts = pts            # (N,2) normalised 0..1 inside the crop
        self.rgb = rgb            # (N,3) 0..1
        self.reg = reg            # (N,) region id
        self.contours = contours  # list of (name, (M,2) normalised)
        self.anchors = anchors    # dict of named points / radii
        self.shapes = shapes or {}
        self.detail = detail if detail is not None else np.zeros(len(pts))
        self.shade = shade if shade is not None else np.zeros(len(pts))

    # ── building ─────────────────────────────────────────────────────
    @classmethod
    def build(cls, path, n=5200, seed=7):
        from PIL import Image
        img = Image.open(path).convert("RGB")
        rgb = np.asarray(img, np.float32) / 255.0
        H, W = rgb.shape[:2]

        lm, shapes = _detect((rgb * 255).astype(np.uint8))

        # the head crop: a square around the face, a little higher so the
        # forehead and hair come along
        oval = lm[[a for a, _ in _OVAL]]
        x0, x1 = oval[:, 0].min(), oval[:, 0].max()
        y0, y1 = oval[:, 1].min(), oval[:, 1].max()
        fh = max(8.0, y1 - y0)
        fw = max(8.0, x1 - x0)
        side = max(fh, fw) * 2.05
        cx = (x0 + x1) * 0.5
        cy = (y0 + y1) * 0.5 - fh * 0.10
        sx, sy = cx - side * 0.5, cy - side * 0.5

        # the background is the colour of the whole photo (the face is small)
        bg = np.median(rgb.reshape(-1, 3), axis=0)

        # a sample grid over the crop
        step = max(1, int(side / 150))
        gx = np.arange(sx, sx + side, step)
        gy = np.arange(sy, sy + side, step)
        gn = len(gx)
        gx, gy = np.meshgrid(gx, gy)
        gx = gx.ravel()
        gy = gy.ravel()

        def sample(arr, ix, iy):
            ix = np.clip(ix, 0, W - 1).astype(np.int32)
            iy = np.clip(iy, 0, H - 1).astype(np.int32)
            return arr[iy, ix]

        lum = (rgb * np.array([0.299, 0.587, 0.114], np.float32)).sum(axis=2)
        gyy, gxx = np.gradient(lum)
        detail = np.sqrt(gxx ** 2 + gyy ** 2)

        dmap = sample(detail, gx, gy)
        lmap = sample(lum, gx, gy)
        cmap = sample(rgb, gx, gy)
        bdist = np.linalg.norm(cmap - bg[None, :], axis=1)

        inside = ((gx >= 0) & (gx < W) & (gy >= 0) & (gy < H)).astype(np.float64)
        subject = (bdist > 0.055).astype(np.float64)
        scale = max(1e-5, float(np.percentile(dmap, 97)))
        det = np.clip(dmap / scale, 0.0, 1.0)

        # a fake normal out of the luminance of the grid - it gives the face
        # a real light on it instead of a flat print
        lg = lmap.reshape(gn, gn)
        gy_g, gx_g = np.gradient(lg)
        mag = np.sqrt(gx_g ** 2 + gy_g ** 2)
        safe = mag > 1e-5
        nxv = np.where(safe, -gx_g / np.maximum(mag, 1e-9), 0.0)
        nyv = np.where(safe, -gy_g / np.maximum(mag, 1e-9), 0.0)
        lam = np.clip(nxv * -0.52 + nyv * -0.85, 0.0, 1.0)
        shade = np.where(safe, lam, 0.55).ravel()


        # the eyes, the mouth and the brows are small - they would get only
        # a handful of points, so they are pulled in on purpose
        def dmin(ids):
            d = np.sqrt((gx[:, None] - lm[ids][None, :, 0]) ** 2 +
                        (gy[:, None] - lm[ids][None, :, 1]) ** 2)
            return d.min(axis=1)

        feat = (2.8 * np.exp(-(dmin(_ids(_EYE_L) + _ids(_EYE_R)) /
                                (side * 0.055)) ** 2) +
                3.4 * np.exp(-(dmin(_ids(_LIPS_OUT) + _ids(_LIPS_IN)) /
                                (side * 0.050)) ** 2) +
                2.4 * np.exp(-(dmin(_ids(_BROW_L) + _ids(_BROW_R)) /
                                (side * 0.045)) ** 2))

        weight = (0.18 + 1.70 * det + feat) * subject * inside

        keep = weight > 1e-6
        gx, gy, weight = gx[keep], gy[keep], weight[keep]
        cmap = cmap[keep]
        lmap = lmap[keep]
        shade = shade[keep]

        if len(gx) == 0:
            raise RuntimeError("nothing to sample")

        rng = np.random.default_rng(seed)
        cdf = np.cumsum(weight)
        cdf /= cdf[-1]
        pick = np.searchsorted(cdf, rng.random(n))
        pick = np.clip(pick, 0, len(gx) - 1)

        px = gx[pick]
        py = gy[pick]
        col = np.clip(cmap[pick], 0.0, 1.0)
        detp = np.clip(det[keep][pick], 0.0, 1.0)
        shadep = np.clip(shade[pick], 0.0, 1.0)

        # which part of the face is every point on?
        best = np.zeros(n, np.int64)
        for i in range(0, n, 3000):
            chunk = np.stack([px[i:i + 3000], py[i:i + 3000]], axis=1)
            d = ((chunk[:, None, :] - lm[None, :, :]) ** 2).sum(axis=2)
            best[i:i + 3000] = d.argmin(axis=1)
        reg = _LM_REG[best]

        # normalised inside the crop
        nx = (px - sx) / side
        ny = (py - sy) / side

        # the skeleton, in the same normalised space
        contours = []
        for name, pairs in _CONTOURS:
            idx = sorted({i for p in pairs for i in p})
            m = {v: k for k, v in enumerate(idx)}
            poly = np.array([((lm[a][0] - sx) / side, (lm[a][1] - sy) / side)
                             for a, _ in pairs], np.float64)
            contours.append((name, poly))

        def cen(pairs):
            ids = sorted({i for p in pairs for i in p})
            return ((lm[ids][:, 0] - sx) / side).mean(), \
                   ((lm[ids][:, 1] - sy) / side).mean()

        def rad(pairs, c):
            ids = sorted({i for p in pairs for i in p})
            d = np.linalg.norm(lm[ids] - np.array(c), axis=1)
            return float(d.mean() / side)

        mouth_c = cen(_LIPS_OUT)
        eye_l_c = cen(_EYE_L)
        eye_r_c = cen(_EYE_R)
        brow_l_c = cen(_BROW_L)
        brow_r_c = cen(_BROW_R)

        # the geometry of the head itself - the WALK stage needs it to put
        # the real face on a body: where the chin is, how tall the face is
        # and what the person is wearing.
        oval_n = np.array([[(lm[a][0] - sx) / side, (lm[a][1] - sy) / side]
                           for a, _ in _OVAL], np.float64)
        chin_y = float(oval_n[:, 1].max())
        top_y = float(oval_n[:, 1].min())
        face_h = max(1e-4, chin_y - top_y)
        face_cx = float(oval_n[:, 0].mean())

        # the clothes: the median colour of the chest a little below the chin
        ch0 = int(min(max(0, H - 2), (chin_y * side + sy) + 1.3 * fh))
        ch1 = int(min(H, (chin_y * side + sy) + 3.4 * fh))
        if ch1 > ch0 + 1:
            shirt = np.median(rgb[ch0:ch1].reshape(-1, 3), axis=0)
        else:
            shirt = np.array([0.28, 0.30, 0.36], np.float32)
        # the skin: the median colour of the face box
        fx0 = int(max(0, min(W - 2, x0)))
        fx1 = int(max(fx0 + 1, min(W, x1)))
        fy0 = int(max(0, min(H - 2, y0)))
        fy1 = int(max(fy0 + 1, min(H, y1)))
        skin = np.median(rgb[fy0:fy1, fx0:fx1].reshape(-1, 3), axis=0)

        anchors = {
            "mouth": np.array(mouth_c),
            "mouth_r": rad(_LIPS_OUT, (mouth_c[0] * side + sx,
                                       mouth_c[1] * side + sy)),
            "eye_l": np.array(eye_l_c),
            "eye_r": np.array(eye_r_c),
            "eye_r_r": rad(_EYE_L, (eye_l_c[0] * side + sx,
                                    eye_l_c[1] * side + sy)),
            "brow_l": np.array(brow_l_c),
            "brow_r": np.array(brow_r_c),
            "brow_r_r": rad(_BROW_L, (brow_l_c[0] * side + sx,
                                      brow_l_c[1] * side + sy)),
            "face": np.array([(nx[reg == SKIN].mean() if (reg == SKIN).any()
                               else 0.5),
                              (ny[reg == SKIN].mean() if (reg == SKIN).any()
                               else 0.5)]),
            "chin": np.array([face_cx, chin_y]),
            "face_top": np.array([face_cx, top_y]),
            "face_h": face_h,
            "face_cx": face_cx,
            "shirt": np.asarray(shirt, np.float64),
            "skin": np.asarray(skin, np.float64),
        }
        return cls(np.stack([nx, ny], axis=1), col, reg, contours, anchors,
                   shapes, detp, shadep)

    # ── cache ────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path=None, n=5200):
        path = path or portrait_path()
        if not path:
            return None
        # the version is part of the key - when the anchors change the old
        # cache must not be reused (it would miss the new geometry)
        key = hashlib.md5(("%s|%.0f|%d|%s" % (os.path.abspath(path),
                                               os.path.getmtime(path), n,
                                               _CACHE_VERSION))
                          .encode()).hexdigest()[:12]
        cache = os.path.join(_here(), ".cache")
        cfile = os.path.join(cache, "portrait_%s.npz" % key)
        if os.path.exists(cfile):
            try:
                z = np.load(cfile, allow_pickle=False)
                conts = json.loads(str(z["contours"]))
                return cls(z["pts"], z["rgb"], z["reg"],
                           [(c[0], np.array(c[1])) for c in conts],
                           {k: np.array(v) for k, v in
                            json.loads(str(z["anchors"])).items()},
                           json.loads(str(z["shapes"])),
                           z["detail"] if "detail" in z.files else None,
                           z["shade"] if "shade" in z.files else None)
            except Exception:
                pass
        try:
            p = cls.build(path, n)
        except Exception:
            return None
        try:
            os.makedirs(cache, exist_ok=True)
            np.savez(cfile,
                     pts=p.pts, rgb=p.rgb, reg=p.reg, detail=p.detail,
                     shade=p.shade,
                     contours=np.array(json.dumps([[c[0], c[1].tolist()]
                                                   for c in p.contours])),
                     anchors=np.array(json.dumps(
                         {k: (v.tolist() if hasattr(v, "tolist") else v)
                          for k, v in p.anchors.items()})),
                     shapes=np.array(json.dumps(p.shapes)))
        except Exception:
            pass
        return p


class FaceStage:
    """The portrait, alive but expressionless.

    The particles keep the shape of the photo - there is no emotion warp
    any more. The mouth opens while he talks, the eyes blink on their own
    and the head breathes. Nothing else moves the face.
    """

    def __init__(self, w, h, n=8000):
        self.w, self.h = w, h
        self.portrait = Portrait.load(n=n)
        self.ok = self.portrait is not None
        self.t = 0.0
        self.level = 0.0
        self.peak = 0.0
        self.hue = 40.0
        self.em = Emitter(w, h)
        self.em.fade.fill((170, 170, 170, 255))
        self.glow = SoftGlow(256, gain=0.46)

        self.pt = pygame.Surface((max(2, w), max(2, h)), 0, 24)
        self.tint = 0
        self._peak_prev = 0.0
        self._blink_at = 2.0
        self._blink = 0.0
        self._say = 0.0
        self._bg = None
        self._bg_key = -1
        self._mouse = (0.0, 0.0)
        self._px = 0.0
        self._py = 0.0

        self.e_mouth = 0.0
        self.p = np.zeros((0, 2))
        self.vel = np.zeros((0, 2))
        self.phase = np.zeros(0)
        if self.ok:
            p = self.portrait
            n = len(p.pts)
            self.p = p.pts.copy() + np.random.default_rng(3).normal(
                0.0, 0.006, (n, 2))
            self.vel = np.zeros((n, 2))
            self.phase = np.random.default_rng(5).random(n) * 6.283

    def resize(self, w, h):
        self.w, self.h = w, h
        self.em.resize(w, h)
        self.em.fade.fill((170, 170, 170, 255))
        self.pt = pygame.Surface((max(2, w), max(2, h)), 0, 24)
        self._bg = None

    def set_mouse(self, pos):
        """The head follows the cursor a little."""
        try:
            self._mouse = (float(pos[0]), float(pos[1]))
        except Exception:
            pass


    # ── the movement: only the mouth and the eyes ──────────────────
    def update(self, dt, level, peak, bands, centroid, hue, state, speaking,
               phrase=""):
        self.t += dt
        self.level = level
        self.peak = peak
        self.hue = hue
        st = (state or "").lower()

        # blinking, on his own rhythm
        self._blink_at -= dt
        if self._blink_at <= 0.0:
            self._blink = 1.0
            self._blink_at = 2.2 + np.random.default_rng(
                int(self.t * 100) % 9973).random() * 3.8
        self._blink = max(0.0, self._blink - dt * 7.0)

        # the mouth moves with the voice while he is talking
        self._say = self._say * 0.80 + level * 0.20
        talking = bool(speaking or st.startswith("respond") or
                        st.startswith("speak"))
        target = 1.0 if talking else 0.0
        k = 1.0 - math.exp(-dt * 6.0)
        self.e_mouth += (target - self.e_mouth) * k

    # ── the shape of the photo, kept ───────────────────────────────
    def _warp(self, q, reg):
        a = self.portrait.anchors
        out = q.copy()
        mr = max(1e-4, float(a["mouth_r"]))
        mc = a["mouth"]

        # the mouth opens while he speaks
        m = reg == MOUTH
        if m.any():
            v = (q[m] - mc[None, :]) / mr
            open_ = self.e_mouth * (0.22 + 0.78 * min(1.0, self._say * 3.4))
            open_ = open_ * (0.55 + 0.45 * np.sin(self.t * 9.0 + v[:, 0] * 2.0))
            out[m, 1] += np.sign(v[:, 1]) * open_ * mr * 0.95
            out[m, 0] += np.sign(v[:, 0]) * open_ * mr * 0.16 * np.abs(v[:, 0])

        # the eyes blink - nothing else
        er = max(1e-4, float(a["eye_r_r"]))
        for key, rid in (("eye_l", EYE_L), ("eye_r", EYE_R)):
            e = reg == rid
            if not e.any():
                continue
            c = a[key]
            v = (q[e] - c[None, :]) / er
            out[e, 1] -= v[:, 1] * er * self._blink * 0.95

        # a slow breath - the photo stays, only the air moves
        s = 1.0 + 0.012 * math.sin(self.t * 0.9)
        return (out - 0.5) * s + 0.5

    # ── drawing ─────────────────────────────────────────────────────
    def draw(self, screen):
        if not self.ok:
            return False
        w, h = self.w, self.h
        t = self.t
        hue = self.hue
        lvl = self.level

        # the room behind the portrait (it only changes with the colour,
        # so it is painted once and kept)
        key = int(hue / 4.0)
        if self._bg is None or self._bg_key != key:
            bg = pygame.Surface((w, h))
            bg.fill((9, 10, 16))
            for i in range(14):
                k = i / 13.0
                col = _hsv(hue + 200.0 + k * 16.0, 0.34,
                           0.026 + 0.040 * (1.0 - abs(k - 0.45)))
                y = int(h * (k * 0.9 - 0.05))
                pygame.draw.rect(bg, col, (0, y, w, int(h * 0.15) + 2))
            self._bg = bg
            self._bg_key = key
        screen.blit(self._bg, (0, 0))


        self.em.fade_step()

        # where the head sits
        ph = min(w, h) * 0.66
        pw = ph
        cx = w * 0.5
        cy = h * 0.47
        # the head follows the cursor, a little
        mx = (self._mouse[0] - w * 0.5) / max(1.0, w * 0.5)
        my = (self._mouse[1] - h * 0.5) / max(1.0, h * 0.5)
        self._px += (mx - self._px) * 0.06
        self._py += (my - self._py) * 0.06
        cx += self._px * ph * 0.035
        cy += self._py * ph * 0.030


        q = self.portrait.pts
        tgt = self._warp(q, self.portrait.reg)

        # every particle keeps a little life of its own
        wob = np.stack([np.sin(t * 1.6 + self.phase * 2.7),
                        np.cos(t * 1.35 + self.phase * 3.1)], axis=1)
        tgt = tgt + wob * (0.0025 + 0.010 * lvl)

        dt = 1.0 / 50.0
        self.vel += (tgt - self.p) * dt * 26.0 - self.vel * dt * 7.0
        self.p += self.vel * dt * 6.0

        xs = cx + (self.p[:, 0] - 0.5) * pw
        ys = cy + (self.p[:, 1] - 0.5) * ph

        # the colour: the accent, with a little of the photo's own tone
        tint = np.array(_hsv(hue, 0.52, 1.0), np.float64) / 255.0
        base = self.portrait.rgb
        lum = base.mean(axis=1)
        inv = 1.0 - lum
        det = self.portrait.detail

        # the skin keeps a quiet glow and its shading, the eyes, the brows
        # and the lips are the ones that burn
        tone = np.clip((lum - 0.02) / 0.55, 0.0, 1.0)
        shd = self.portrait.shade
        if len(shd) != len(lum):
            shd = np.full(len(lum), 0.55)
        # the light from the top left gives the face its depth
        bright = (0.09 + 0.40 * tone + 0.13 * det ** 1.15) * (0.58 + 0.74 * shd)
        hot = ((self.portrait.reg == MOUTH) | (self.portrait.reg == EYE_L) |
               (self.portrait.reg == EYE_R) | (self.portrait.reg == BROW))
        bright[hot] = np.maximum(bright[hot], 0.70 + 0.30 * inv[hot] ** 0.8)
        mix = 0.72 * tint[None, :] + 0.28 * base
        v = bright * (1.30 + 0.45 * lvl)
        col = np.clip(mix * v[:, None], 0.0, 1.0)


        self.pt.fill((0, 0, 0))
        ix = xs.astype(np.int32)[:, None] + np.array([0, 1, 0, 1])[None, :]
        iy = ys.astype(np.int32)[:, None] + np.array([0, 0, 1, 1])[None, :]
        ix = np.clip(ix, 0, w - 1).ravel()
        iy = np.clip(iy, 0, h - 1).ravel()
        cols = np.repeat(col * 0.30, 4, axis=0)

        arr = pygame.surfarray.pixels3d(self.pt)
        arr[ix, iy] = (cols * 255).astype(np.uint8)
        del arr
        self.em.surf.blit(self.pt, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        self.em.present(screen)

        # ── the vector skeleton ──────────────────────────────────────
        # the face drawn as light, on top of the particles
        vsurf = pygame.Surface((w, h), pygame.SRCALPHA)
        base_a = 0.34 + 0.34 * lvl
        for name, poly in self.portrait.contours:
            reg = np.full(len(poly), OTHER, np.int8)
            if name == "lips" or name == "lips_in":
                reg[:] = MOUTH
            elif name == "eye_l":
                reg[:] = EYE_L
            elif name == "eye_r":
                reg[:] = EYE_R
            elif name == "brow_l":
                reg[:] = BROW
            elif name == "brow_r":
                reg[:] = BROW
            wq = self._warp(poly, reg)
            px = (cx + (wq[:, 0] - 0.5) * pw).astype(np.int32)
            py = (cy + (wq[:, 1] - 0.5) * ph).astype(np.int32)
            if name == "lips_in":
                a = int(base_a * 130)
                c = _hsv(hue + 6.0, 0.60, 1.0)
            elif name in ("eye_l", "eye_r"):
                a = int(base_a * 190)
                c = _hsv(hue + 14.0, 0.45, 1.0)
            elif name.startswith("brow"):
                a = int(base_a * 120)
                c = _hsv(hue - 12.0, 0.40, 0.95)
            else:
                # the outline of the head stays a whisper - the face should
                # not look boxed in
                a = int(base_a * 56)
                c = _hsv(hue + 196.0, 0.35, 1.0)
            if len(px) > 2:
                # an additive blit drops the alpha, so the light of the
                # line has to live in its colour
                cc = (c[0] * a // 255, c[1] * a // 255, c[2] * a // 255)
                pygame.draw.lines(vsurf, cc + (a,), False,
                                  list(zip(px.tolist(), py.tolist())), 1)

        screen.blit(vsurf, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        # a soft halo behind the head
        gsz = int(ph * 1.15)
        g = self.glow.get(_hsv(hue + 12.0, 0.45, 1.0))
        screen.blit(g, (int(cx - gsz / 2), int(cy - gsz / 2)),
                    special_flags=pygame.BLEND_RGBA_ADD)
        return True






