# AK Voice Reactive Orb
# One simple, elegant stage: a cloud of particles arranged on a sphere that
# turns slowly in 3D and answers the voice - it breathes with the level and
# takes its colour from how the voice sounds (spectral centre), not from a
# random hash. Nothing else, no cartoon shapes, no bars, no rings.
import math

import numpy as np
import pygame

LIVE_NAMES = ["ORB", "FACE", "WALK"]

N_BANDS = 24


# ── the colour of a voice ──────────────────────────────────────────────────
# Deep bass sits in indigo, a mid voice in cyan/teal, a bright vowel in
# gold, a sharp sibilant in rose. That is what the orb wears.
_CENTROID_HUE = ((70.0, 254.0), (170.0, 216.0), (380.0, 190.0),
                 (850.0, 152.0), (1700.0, 90.0), (3000.0, 46.0),
                 (5200.0, 24.0), (9000.0, 350.0))


try:
    from .vis_core import Emitter, Glow, _hsv
except ImportError:          # run as a plain script
    from vis_core import Emitter, Glow, _hsv


def voice_hue(centroid: float) -> float:
    """Map the spectral centre of the voice (Hz) to a hue."""
    c = float(centroid or 0.0)
    if c <= 1.0:
        return 40.0
    pts = _CENTROID_HUE
    if c <= pts[0][0]:
        return pts[0][1]
    for (h0, hue0), (h1, hue1) in zip(pts, pts[1:]):
        if c <= h1:
            k = (c - h0) / max(1e-6, h1 - h0)
            return hue0 + (hue1 - hue0) * k
    return pts[-1][1]


def smart_hue(centroid: float, text: str, base: float) -> float:
    """Mostly the voice, a little of the words."""
    vh = voice_hue(centroid)
    return (vh * 0.78 + float(base) * 0.22) % 360.0





# ── the orb ────────────────────────────────────────────────────────────────
class OrbStage:
    """A round cloud of free particles.

    The particles are not stuck to a rigid shell - every one of them drifts
    on its own (a slow swirl plus a personal noise wobble) while staying in
    a round volume, so the whole thing reads as a breathing sphere of light.
    The voice pushes it open, speeds the swirl up and flairs the front.
    """

    def __init__(self, w, h, n=1600):
        self.w, self.h = w, h
        self.n = n
        rng = np.random.default_rng(17)
        d = rng.normal(size=(n, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        self.dir = d                       # the home direction of each particle
        self.rad = 0.30 + rng.random(n) ** 0.75 * 0.70
        self.phase = rng.random(n) * 6.283
        self.spin = 0.55 + rng.random(n) * 0.9
        self.size = rng.random(n) ** 2.2 * 1.8 + 0.5
        self.hue_j = rng.random(n)
        self.p = d * self.rad[:, None]      # where they are right now
        self.vel = np.zeros((n, 3), np.float64)
        self.em = Emitter(w, h)
        self.glow = Glow(256)
        self.t = 0.0
        self.level = 0.0
        self.peak = 0.0
        self.hue = 40.0
        self._peak_prev = 0.0

    def resize(self, w, h):
        self.w, self.h = w, h
        self.em.resize(w, h)

    def update(self, dt, level, peak, bands, centroid, hue, state, speaking,
               phrase=""):
        self.t += dt
        self.level = level
        self.peak = peak
        self.hue = hue
        # a loud syllable throws every particle outwards a little
        if peak > self._peak_prev + 0.12 and peak > 0.15:
            push = (peak - self._peak_prev) * 0.55
            self.vel += self.dir * push
        self._peak_prev = max(self._peak_prev * 0.97, peak)

    def draw(self, screen):
        w, h = self.w, self.h
        t = self.t
        lvl = self.level
        peak = self.peak
        hue = self.hue
        dt = 1.0 / 50.0

        # backdrop - a soft dark room, never pure black
        bg = pygame.Surface((w, h))
        bg.fill((10, 11, 17))
        for i in range(16):
            k = i / 15.0
            col = _hsv(hue + 195.0 + k * 18.0, 0.36,
                       0.030 + 0.045 * (1.0 - abs(k - 0.4)))
            y = int(h * (k * 0.85 - 0.05))
            pygame.draw.rect(bg, col, (0, y, w, int(h * 0.16) + 2))
        screen.blit(bg, (0, 0))

        self.em.fade_step()

        # ── the cloud moves ────────────────────────────────────────
        # a slow swirl of the whole cloud (this is what makes it round and
        # alive), plus a personal wobble for every single particle
        ang = t * (0.26 + 0.55 * lvl) * 0.9
        ca, sa = math.cos(ang), math.sin(ang)
        dx = self.dir[:, 0] * ca - self.dir[:, 2] * sa
        dz = self.dir[:, 0] * sa + self.dir[:, 2] * ca
        home = np.stack([dx, self.dir[:, 1], dz], axis=1)

        swell = 1.0 + 0.26 * lvl + 0.10 * peak
        wob = 0.05 + 0.05 * lvl
        nx = np.sin(t * 1.7 + self.phase * 3.1 + self.p[:, 1] * 2.2)
        ny = np.cos(t * 1.3 + self.phase * 2.3 + self.p[:, 0] * 1.9)
        nz = np.sin(t * 0.9 + self.phase * 1.7 + self.p[:, 2] * 2.5)
        target = (home * (self.rad * swell)[:, None]
                  + np.stack([nx, ny, nz], axis=1) * wob)

        # soft spring - the particles chase their place, they never snap
        self.vel += (target - self.p) * dt * 9.0 - self.vel * dt * 3.4
        self.p += self.vel * dt * 3.2

        # ── project and draw ──────────────────────────────────────
        f = 2.9
        z = self.p[:, 2]
        scale = f / np.maximum(0.75, f - z)
        R = min(w, h) * 0.29
        xs = w * 0.5 + self.p[:, 0] * R * scale
        ys = h * 0.5 + self.p[:, 1] * R * scale

        # the front of the cloud is lit, the back fades away
        depth = (z + 1.0) * 0.5
        front = np.clip((z + 0.85) * 1.2, 0.0, 1.0)

        s = self.em.surf
        for i in range(self.n):
            x = int(xs[i])
            y = int(ys[i])
            if x < 0 or y < 0 or x >= w or y >= h:
                continue
            v = (0.16 + 0.66 * front[i] + 0.30 * depth[i]) * (0.66 + 0.62 * lvl)
            v = min(1.0, v)
            hh = hue + self.hue_j[i] * 26.0 - 13.0
            col = _hsv(hh, 0.42 - 0.18 * v, v)
            r = self.size[i] * (0.75 + 0.75 * front[i] + 0.5 * lvl)
            if r > 1.6:
                pygame.draw.circle(s, col, (x, y), 2)
            else:
                pygame.draw.circle(s, col, (x, y), 1)
        self.em.present(screen)

        # one soft bloom behind the cloud
        gsz = int(R * (1.30 + 0.35 * lvl))
        g = self.glow.get(_hsv(hue + 10.0, 0.48, 1.0))
        screen.blit(g, (int(w * 0.5 - gsz / 2), int(h * 0.5 - gsz / 2)),
                    special_flags=pygame.BLEND_RGBA_ADD)



try:
    from .face_portrait import FaceStage
except ImportError:          # run as a plain script
    from face_portrait import FaceStage

try:
    from .face_motion import MotionStage
except ImportError:          # run as a plain script
    from face_motion import MotionStage


_STAGES = {"ORB": OrbStage, "FACE": FaceStage, "WALK": MotionStage}


class LiveStage:
    """Holds the live stage and draws it."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.active = "ORB"
        self.stages = {k: v(w, h) for k, v in _STAGES.items()}

    def resize(self, w, h):
        self.w, self.h = w, h
        for st in self.stages.values():
            st.resize(w, h)

    def set_mouse(self, pos):
        """Let the stage look at the cursor, if it wants to."""
        st = self.stages.get(self.active)
        if st is None:
            return
        try:
            if hasattr(st, "set_mouse"):
                st.set_mouse(pos)
        except Exception:
            pass


    def update(self, dt, level, peak, bands, centroid, hue, state, speaking,
               phrase=""):
        st = self.stages.get(self.active)
        if st is None:
            return
        try:
            st.update(dt, level, peak, bands, centroid, hue, state, speaking,
                      phrase)
        except Exception:
            pass

    def draw(self, screen):
        st = self.stages.get(self.active)
        if st is None:
            return False
        try:
            st.draw(screen)
            return True
        except Exception:
            return False


