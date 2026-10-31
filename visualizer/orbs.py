"""orbs.py — The 4 Orb Style renderers + seamless style cross-fade.

Rendering strategy (keeps it tiny + 60 FPS):
  * Every style draws into a small square Surface (e.g. 256x256). The visualizer
    then upscales it with bilinear `smoothscale` — that acts as a FREE bloom/glow
    pass (soft pixels) at a fraction of the cost of fullscreen supersampling.
  * Active style draws to `self.surf`. The next style (during a transition) draws to
    `self.blend_surf` which gets an alpha channel and is blitted on top. Alpha grows
    0->255 over `duration` seconds -> smooth 1–2 s lerp morph between styles.

Styles
  0 NEON_FLUID_MESH   concentric displaced rings, sine-noise wobble = "fluid sphere"
  1 QUANTUM_PARTICLES orbiting particle cloud that contracts/expands with sound
  2 PLASMA_CORE       layered auras + rotating vortex arcs (concentric arcs)
  3 SONIC_RINGS       radiating geometric polygon rings driven by FFT bands
"""

import math

import numpy as np
import pygame

STYLE_NAMES = ["NEON_FLUID_MESH", "QUANTUM_PARTICLES", "PLASMA_CORE", "SONIC_RINGS"]


def _hsv(h, s, v):
    """Quick HSV->pygame color. h in cycles (0..1), s/v 0..1."""
    col = pygame.Color(0, 0, 0)
    col.hsva = (h * 360.0 % 360.0, max(0, min(100, s * 100)), max(0, min(100, v * 100)), 100)
    return col


class Energy:
    """Per-frame lighting model — everything a style needs to react to audio."""

    def __init__(self, params, audio, t):
        self.p = params              # state vector (scale, halo, rot, ..., mix)
        self.a = audio               # {mic:{...}, spk:{...}, volume}
        self.t = t                   # wall time seconds
        vol = audio["volume"]
        # cheap continuously-recomputed rhythm terms
        base = params["rot"] + 0.6
        self.pulse = math.sin(t * (2.0 + 6.0 * params["pulse"]))        # fast throb
        self.breath = 0.5 + 0.5 * math.sin(t * 0.6)                     # idle breathing
        self.spin = t * base                                            # rotation total
        # core scale = state scale * (breath for idle) * audio drive
        audio_drive = 1.0 + params["mix"] * vol * 0.6 + params["mix"] * self.pulse * 0.15
        idle_drive = 0.9 + 0.2 * self.breath
        self.scale = params["scale"] * idle_drive * audio_drive
        self.volume = vol
        self.spec = audio["spk"]["spec"] if audio["spk"]["rms"] > audio["mic"]["rms"] \
            else audio["mic"]["spec"]                                   # dominant channel
        self.power = max(0, min(1.5, vol * (1.2 + self.pulse * 0.25)))


class OrbRenderer:
    def __init__(self, size=256):
        self.size = size
        self.surf = pygame.Surface((size, size), pygame.SRCALPHA)
        self.blend_surf = pygame.Surface((size, size), pygame.SRCALPHA)
        # fixed orbital particles for style 1 (positions are cheap trig, computed live)
        rng = np.random.default_rng(7)
        n = 130
        self.p_ang = rng.uniform(0, 2 * math.pi, n)          # current orbit angle
        self.p_rad = rng.uniform(0.18, 0.95, n)              # orbit radius fraction
        self.p_spd = rng.uniform(0.6, 2.4, n) * (1 - 2 * rng.integers(0, 2, n))
        self.p_flat = rng.uniform(0.45, 1.0, n)              # vertical flattening
        self.p_sz = rng.uniform(1.0, 3.0, n)                 # dot radius mm
        self.p_hue = rng.uniform(0.0, 1.0, n)                # per-particle color shift

        # style switching -----------------------------------------------------
        self.idx = 0
        self.next_idx = -1
        self.blend = 0.0             # 0..1 cross-fade progress
        self.blend_rate = 1.0 / 1.5  # completed in ~1.5 s as specified

    # ---- public hooks --------------------------------------------------------
    def switch(self, style, duration=1.5):
        """style: index or name. Beginns smooth alpha cross-fade toward it."""
        if isinstance(style, str):
            style = STYLE_NAMES.index(style) if style in STYLE_NAMES else int(style)
        style = style % len(STYLE_NAMES)
        if style == self.idx or self.next_idx == style:
            return
        self.next_idx = style
        self.blend = 0.0
        self.blend_rate = 1.0 / max(0.2, duration)

    def update(self):
        """Advance the cross-fade; returns True the frame a morph ends."""
        ended = False
        if self.next_idx >= 0:
            self.blend += self.blend_rate * (1.0 / 60.0)
            if self.blend >= 1.0:
                self.idx, self.next_idx, self.blend = self.next_idx, -1, 0.0
                ended = True
        return ended

    # ---- dispatch ------------------------------------------------------------
    def draw(self, eng: Energy):
        """Render active style (+ blended next style if morphing) into self.surf."""
        self.surf.fill((0, 0, 0, 0))
        self._styles[self.idx](eng, self.surf)
        if self.next_idx >= 0:
            self.blend_surf.fill((0, 0, 0, 0))
            self._styles[self.next_idx](eng, self.blend_surf)
            self.blend_surf.set_alpha(int(self.blend * 255))  # alpha lerp
            self.surf.blit(self.blend_surf, (0, 0))
            self.blend_surf.set_alpha(255)

    # ---- helpers shared by styles ---------------------------------------------
    def _center(self, r):
        c = self.size / 2.0
        return c, c, c * r

    # ---- STYLE 0 : NEON FLUID MESH -------------------------------------------
    def _fluid_mesh(self, e, srf):
        cx, cy, R = self._center(e.scale * 0.40)
        hue = 0.58 + e.p["hue"]
        layers = 18
        for i in range(layers):                       # back-to-front shells
            k = i / (layers - 1)                      # 0 outer -> 1 centre
            rr = R * (1.0 - k * 0.92)
            # sine-noise displacement (sum of two phases) wobbles the shell edge
            wob = e.volume * math.sin(e.t * 2.1 + i * 0.7) * 0.10 \
                + e.volume * math.sin(e.t * 3.5 + i * 1.9) * 0.05
            rr += rr * wob
            v = 0.12 + 0.88 * k ** 0.85                # brighter toward core
            col = _hsv(hue + k * 0.12 + self.blend * e.p["hue"], 0.85, v)
            col.a = 30 + int(120 * k)
            pygame.draw.circle(srf, col, (int(cx), int(cy)), int(rr))
        # glint highlight
        hl = _hsv(hue + 0.1, 0.6, 1.0)
        hl.a = 120
        pygame.draw.circle(srf, hl, (int(cx - R * 0.35), int(cy - R * 0.4)),
                           int(R * 0.12))

    # ---- STYLE 1 : QUANTUM PARTICLE FIELD ------------------------------------
    def _particles(self, e, srf):
        cx, cy, R = self._center(e.scale * 0.42)
        hue = 0.92 + e.p["hue"]
        # contraction/expansion driven by sound (the ~0.4 factor is the "breathe")
        breathe = 1.0 - 0.30 * e.volume + 0.18 * math.sin(e.t * 2.6)
        hue_shift = 0.02 * e.volume
        for i in range(len(self.p_ang)):
            # orbit angle advances at each particle's own speed + global spin
            ang = self.p_ang[i] + e.spin * (self.p_spd[i] * 0.4)
            rad = self.p_rad[i] * breathe * R
            x = cx + math.cos(ang) * rad
            y = cy + math.sin(ang) * rad * self.p_flat[i]
            depth = 0.5 + 0.5 * math.cos(self.p_ang[i] * 2 + e.t * (self.p_spd[i] + 1))
            col = _hsv(hue + self.p_hue[i] * 0.4 + hue_shift, 0.9, 0.35 + 0.75 * depth)
            pygame.draw.circle(srf, col, (int(x), int(y)), max(1, int(self.p_sz[i] * depth)))

    # ---- STYLE 2 : PLASMA CORE ------------------------------------------------
    def _plasma(self, e, srf):
        cx, cy, R = self._center(e.scale * 0.38)
        hue = 0.85 + e.p["hue"]
        # layered aura (always-on glow disc) — bigger + hotter with volume
        glow = pygame.Surface((self.size, self.size), pygame.SRCALPHA)
        for k in range(6, 0, -1):
            rr = R * (0.35 + (6 - k) * 0.11 + e.volume * 0.10)
            col = _hsv(hue - 0.08 * (6 - k), 0.9, 0.35 + 0.28 * k)
            col.a = 18 * k
            pygame.draw.circle(glow, col, (int(cx), int(cy)), int(rr))
        # inner vortex disc (opaque core)
        core_col = _hsv(hue + 0.05, 0.8, 1.0)
        pygame.draw.circle(glow, core_col, (int(cx), int(cy)), int(R * 0.30))
        # rotating vortex arcs — sweeping blades pumped by audio pulse
        blades = 5
        swing = 0.6 + 1.2 * e.power
        base = e.spin
        for b in range(blades):
            start = base + b * (2 * math.pi / blades)
            for seg in range(3):                          # 3 segments per blade
                a0 = start + seg * swing / 3.0
                a1 = start + (seg + 1) * swing / 3.0
                rr = R * (0.30 + seg * 0.18)
                col = _hsv(hue - 0.12 + seg * 0.05, 0.8, 0.9 - 0.18 * seg)
                col.a = 160
                pygame.draw.arc(glow, col,
                                (int(cx - rr), int(cy - rr), int(2 * rr), int(2 * rr)),
                                a0, a1, max(2, int(4.5 * (1 - seg * 0.2))))
        srf.blit(glow, (0, 0))

    # ---- STYLE 3 : SONIC WAVES RING ------------------------------------------
    def _sonic(self, e, srf):
        cx, cy, R = self._center(e.scale * 0.40)
        hue = 0.15 + e.p["hue"]
        # 7 geometric rings, radius = FFT-band energy * state "edge"
        rings = 7
        for i in range(rings):
            band = e.spec[i % len(e.spec)]
            energy = 0.25 + band * 0.75
            # ring pushes outward on loud bass, gravitational pull-back otherwise
            rr = R * (0.10 + 0.13 * i + e.volume * 0.6 * energy
                      + 0.05 * math.sin(e.t * 1.5 + i))
            rr = min(rr, R * 1.02)
            sides = 6 + i % 3                     # hexagon / heptagon / octagon
            rot = e.spin * 0.15 * (i + 1) * 0.5 + i * 0.6
            col = _hsv(hue + i * 0.03, 0.85, 0.55 + 0.45 * band)
            pts = []
            for s in range(sides):
                a = rot + s * 2 * math.pi / sides
                pts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
            pygame.draw.polygon(srf, col, pts, max(1, int(2 + band * 3)))
        # central energy nodule
        pygame.draw.circle(srf, _hsv(hue, 0.9, 1.0), (int(cx), int(cy)), int(R * 0.08 * (1 + e.volume)))

    _styles = [_fluid_mesh, _particles, _plasma, _sonic]