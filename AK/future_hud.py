"""The AK voice core - a futuristic instrument layer for the visualizer.

Design notes (motion-design grade, not a debug overlay):

* one accent hue, driven by the voice; everything else is cool neutral
  glass, so the accent always carries the meaning
* plates are *cut* (chamfered corners), never plain rounded rectangles,
  and they are joined to the orb by hairline reticles
* every value moves on a spring - nothing snaps, nothing blinks
* the whole cluster reveals itself in a staggered, eased entrance and
  reacts to the voice with a short onset flash
* type is hierarchical: a wordmark, letterspaced micro labels, and large
  mono readouts for the numbers that matter

Beyond the flat-glass version this build adds:

* **real frosted glass** - every plate samples the scene behind it and
  blurs it, so the panels feel like glass and not like stickers
* **a cinematic post pass** - bloom on the bright parts, a soft vignette,
  fine film grain and a faint chromatic edge
* **kinetic typography** - the ticker types itself in with a per-letter
  stagger instead of a plain typewriter
* **parallax** - the panels drift a few pixels with the mouse
* **adaptive layout** - the cluster reflows for small windows
"""
import colorsys
import os
import math

import numpy as np
import pygame

_UI_STACK = ("segoeui", "tahoma", "arial")
_MONO_STACK = ("consolas", "courriernew", "dejavusansmono", "couriernew")
_fonts = {}


def _font(size, mono=False):
    """A cached system font - UI face for labels, mono for the numbers."""
    key = (int(size), bool(mono))
    f = _fonts.get(key)
    if f is not None:
        return f
    if not pygame.font.get_init():
        pygame.font.init()
    stack = _MONO_STACK if mono else _UI_STACK
    for name in stack:
        try:
            f = pygame.font.SysFont(name, int(size))
            break
        except Exception:
            continue
    if f is None:
        f = pygame.font.SysFont(None, int(size))
    _fonts[key] = f
    return f


_glyphs = {}


def _glyph(f, ch, colour):
    """A cached glyph - the ticker used to render every letter every frame."""
    key = (id(f), ch, colour)
    g = _glyphs.get(key)
    if g is None:
        g = f.render(ch, True, colour)
        if len(_glyphs) > 800:
            _glyphs.clear()
        _glyphs[key] = g
    return g


def hsv(h, s, v, a=255):
    """HSV -> RGBA (0-255 channels)."""
    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0,
                                  max(0.0, min(1.0, s)),
                                  max(0.0, min(1.0, v)))
    return (int(r * 255), int(g * 255), int(b * 255), int(a))


def _ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _ease_out_back(t):
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


def _cut_path(rect, cut=14, corners=(1, 1, 1, 1)):
    """A chamfered rectangle - the classic HUD plate."""
    x, y = rect.x, rect.y
    w, h = rect.w, rect.h
    c = min(cut, w // 2, h // 2)
    tl, tr, br, bl = corners
    pts = []
    if tl:
        pts.append((x + c, y))
    else:
        pts.append((x, y))
    if tr:
        pts.append((x + w - c, y))
        pts.append((x + w, y + c))
    else:
        pts.append((x + w, y))
    if br:
        pts.append((x + w, y + h - c))
        pts.append((x + w - c, y + h))
    else:
        pts.append((x + w, y + h))
    if bl:
        pts.append((x + c, y + h))
        pts.append((x, y + h - c))
    else:
        pts.append((x, y + h))
    if tl:
        pts.append((x, y + c))
    return pts


class Spring:
    """A tiny critically-damped spring - the reason nothing ever snaps."""

    def __init__(self, value=0.0, stiffness=90.0, damping=16.0):
        self.v = value
        self.target = value
        self.vel = 0.0
        self.k = stiffness
        self.d = damping

    def step(self, dt):
        dt = max(0.0005, min(0.05, dt))
        a = self.k * (self.target - self.v) - self.d * self.vel
        self.vel += a * dt
        self.v += self.vel * dt
        return self.v


class FutureHUD:
    """The instrument layer drawn on top of the AK scene."""

    N_BANDS = 24

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.t = 0.0
        self.level = 0.0
        self.peak = 0.0
        self.bands = np.zeros(self.N_BANDS)
        self.peaks = np.zeros(self.N_BANDS)
        self.hue = 40.0
        self.centroid = 0.0
        self.state = "idle"
        self.phrase = ""
        self.fps = 0.0
        self.enabled = True
        self.post = True                 # bloom + vignette + grain
        self.glass = True                # frosted backdrop

        self._lvl = Spring(0.0, 120.0, 19.0)
        self._flash = Spring(0.0, 240.0, 22.0)
        self._reveal = 0.0
        self._db = Spring(-60.0, 40.0, 12.0)
        self._wave = np.zeros(120)
        self._scan = 0.0
        self._ring = 0.0
        self._sweep = 0.0
        self._prev_peak = 0.0
        self._text = ""
        self._typed = 0.0
        self._plate = None
        self._ring_surf = None
        self._labels = {}
        self._vignette = None
        self._grain = None
        self._grain_age = 0
        self._bloom = None
        self._bloom_age = 0
        self._pulses = []                # onset sonar rings
        self._mouse = (0, 0)
        self._px = 0.0
        self._py = 0.0
        self._boot = 0.0
        # adaptive quality - the layer gives up its luxuries before the
        # animation is allowed to stutter
        self._perf = 1.0
        try:
            self._perf = float(os.environ.get("AK_VIS_HUD_QUALITY", "1.0"))
        except ValueError:
            self._perf = 1.0
        self._cost = 0.004

        self._perf_locked = bool(os.environ.get("AK_VIS_HUD_QUALITY"))

    # ── state ───────────────────────────────────────────────────────────
    def resize(self, w, h):
        self.w, self.h = w, h
        self._labels.clear()
        self._plate = None
        self._ring_surf = None
        self._vignette = None

    def set_enabled(self, on):
        self.enabled = bool(on)
        if self.enabled:
            self._reveal = 0.0

    def set_mouse(self, pos):
        if pos:
            self._mouse = pos

    def update(self, dt, level=0.0, peak=0.0, bands=None, centroid=0.0,
               hue=40.0, state="idle", phrase="", fps=0.0):
        dt = max(0.001, min(0.1, dt))
        self.t += dt
        # the quality follows the HUD's own cost - not the whole frame, or
        # a background window (which is deliberately slow) would strip the
        # layer down for no reason
        if not self._perf_locked:
            # The band must straddle the real cost curve: a stripped layer
            # still costs a few ms, a full one ~15ms. The band sits low so
            # the layer gives up its luxuries before the animation stutters.
            if self._cost > 0.008:
                self._perf = max(0.0, self._perf - dt * 1.4)
            elif self._cost < 0.005:
                self._perf = min(1.0, self._perf + dt * 0.35)
        self.level = level
        self.peak = peak
        self.hue = hue
        self.centroid = centroid
        self.state = state
        self.fps = fps

        if self.enabled and self._reveal < 1.0:
            self._reveal = min(1.0, self._reveal + dt * 1.25)
        self._boot = min(1.0, self._boot + dt * 0.8)

        # the voice onset - a flash of light through the whole cluster and
        # a sonar ring that runs out across the glass
        if peak > 0.30 and self._prev_peak < 0.22:
            self._flash.target = 1.0
            self._pulses.append([0.0, min(1.0, peak)])
        self._prev_peak = max(self._prev_peak * 0.94, peak)

        self._lvl.target = level
        self._lvl.step(dt)
        self._flash.target = 0.0
        self._flash.step(dt)

        db = 20.0 * math.log10(max(1e-4, level)) if level > 1e-4 else -60.0
        self._db.target = max(-60.0, min(0.0, db))
        self._db.step(dt)

        if bands is not None and len(bands):
            b = np.asarray(bands, dtype=np.float64)
            if b.size != self.N_BANDS:
                b = np.interp(np.linspace(0, 1, self.N_BANDS),
                              np.linspace(0, 1, b.size), b)
            m = max(1e-6, float(b.max()))
            b = np.clip(b / m, 0.0, 1.0)
            up = b > self.bands
            self.bands = np.where(up, b, self.bands + (b - self.bands) * 0.14)
            self.peaks = np.where(self.bands >= self.peaks, self.bands,
                                  np.maximum(0.0, self.peaks - dt * 0.5))

        # the ring waveform - a smooth loop of the live bands
        n = self._wave.size
        src = np.interp(np.linspace(0, 1, n),
                        np.linspace(0, 1, self.N_BANDS), self.bands)
        wob = 0.12 * np.sin(self.t * 3.1 + np.linspace(0, 1, n) * 12.0)
        tgt = src * (0.30 + 0.9 * self._lvl.v) + wob * (0.20 + self._lvl.v)
        self._wave += (tgt - self._wave) * min(1.0, dt * 14.0)

        # the ticker types itself in
        if phrase and phrase != self._text:
            self._text = phrase
            self._typed = 0.0
        if self._text:
            self._typed = min(float(len(self._text)),
                              self._typed + dt * 42.0)

        # the sonar rings run out and die
        for p in self._pulses:
            p[0] += dt * 0.55
        self._pulses = [p for p in self._pulses if p[0] < 1.0]

        # the mouse parallax eases
        mx = (self._mouse[0] / max(1, self.w) - 0.5) if self._mouse else 0.0
        my = (self._mouse[1] / max(1, self.h) - 0.5) if self._mouse else 0.0
        self._px += (mx - self._px) * min(1.0, dt * 3.0)
        self._py += (my - self._py) * min(1.0, dt * 3.0)

        self._scan = (self._scan + dt * 0.085) % 1.0
        self._ring = (self._ring + dt * (0.14 + 0.85 * self._lvl.v)) % (2 * math.pi)
        self._sweep = (self._sweep + dt * 0.55) % (2 * math.pi)

    # ── type helpers ───────────────────────────────────────────────────
    def _label(self, surf, text, pos, colour, size=9, mono=False, alpha=255,
               track=1.6):
        """Letterspaced micro type - drawn by hand, pygame has no tracking."""
        key = (text, size, mono, colour, alpha, track)
        s = self._labels.get(key)
        if s is None:
            f = _font(size, mono)
            glyphs = [f.render(ch, True, colour[:3]) for ch in text]
            w = sum(g.get_width() for g in glyphs) + int(track * (len(glyphs) - 1))
            h = max((g.get_height() for g in glyphs), default=size)
            s = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
            x = 0
            for g in glyphs:
                s.blit(g, (x, 0))
                x += g.get_width() + track
            if alpha < 255:
                s = s.copy()
                s.set_alpha(alpha)
            if len(self._labels) > 500:
                self._labels.clear()          # the live numbers keep it small
            self._labels[key] = s
        surf.blit(s, pos)
        return s.get_width()

    def _big(self, surf, text, pos, colour, size=20, mono=True, alpha=255):
        key = ("big", text, size, mono, colour, alpha)
        s = self._labels.get(key)
        if s is None:
            s = _font(size, mono).render(text, True, colour[:3])
            if alpha < 255:
                s = s.copy()
                s.set_alpha(alpha)
            self._labels[key] = s
        surf.blit(s, pos)

    # ── frosted glass ──────────────────────────────────────────────────
    def _frost(self, screen, rect):
        """Sample the scene behind a plate and blur it - real frosted glass."""
        w, h = rect.size
        if w < 10 or h < 10:
            return None
        raw = pygame.Surface((w, h), pygame.SRCALPHA)
        raw.blit(screen, (0, 0), rect)
        w2 = max(2, w // 6)
        h2 = max(2, h // 6)
        small = pygame.transform.smoothscale(raw, (w2, h2))
        blurred = pygame.transform.smoothscale(small, (w, h))
        blurred = pygame.transform.smoothscale(
            pygame.transform.smoothscale(blurred, (max(2, w // 3),
                                                   max(2, h // 3))), (w, h))
        return blurred

    # ── plates ─────────────────────────────────────────────────────────
    def _panel(self, surf, rect, accent, delay=0.0, alpha=1.0, frost=None):
        """A chamfered glass plate that slides in and fades on the reveal."""
        rev = _ease_out((self._reveal - delay) / 0.55)
        if rev <= 0.0:
            return
        a = int(255 * alpha * rev)
        if a <= 0:
            return
        off = int((1.0 - rev) * 22)
        r = rect.move(0, off)
        pts = _cut_path(r, 14)
        if frost is not None:
            frost.set_alpha(int(58 * alpha * rev))
            surf.blit(frost, r.topleft)
        body = (9, 11, 17, int(118 * alpha * rev))
        edge = hsv(self.hue, 0.35, 0.85, int(62 * alpha * rev))
        pygame.draw.polygon(surf, body, pts)
        pygame.draw.polygon(surf, edge, pts, 1)
        pygame.draw.line(surf, hsv(self.hue, 0.60, 1.0, int(150 * alpha * rev)),
                         pts[0], pts[1], 2)
        sheen = pygame.Surface((r.w, max(1, r.h // 3)), pygame.SRCALPHA)
        sheen.fill((255, 255, 255, int(7 * alpha * rev)))
        mask = pygame.Surface((r.w, max(1, r.h // 3)), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(),
                         border_radius=4)
        sheen.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(sheen, (r.x, r.y))

    def _reticle(self, surf, rect, cx, cy):
        """Hairlines that tie a panel to the orb - the 'targeting' feel."""
        rev = _ease_out(self._reveal / 0.7)
        if rev <= 0.0:
            return
        a = int(38 * rev)
        for (px, py) in ((rect.x, rect.y), (rect.right, rect.y),
                         (rect.x, rect.bottom), (rect.right, rect.bottom)):
            dx, dy = cx - px, cy - py
            d = math.hypot(dx, dy)
            if d < 40:
                continue
            ex = px + dx / d * min(d - 26, 120)
            ey = py + dy / d * min(d - 26, 120)
            pygame.draw.line(surf, hsv(self.hue, 0.40, 0.90, a), (px, py),
                             (ex, ey), 1)

    # ── layout ─────────────────────────────────────────────────────────
    def _layout(self, W, H):
        """The cluster reflows for small windows."""
        m = 36 if W >= 980 else 20
        compact = W < 900 or H < 520
        brand_w = 226 if not compact else 178
        brand = pygame.Rect(m, m, brand_w, 66 if not compact else 58)
        col_w = 178 if not compact else 150
        tele = pygame.Rect(W - col_w - m, 80 if not compact else 66,
                           col_w, 118 if not compact else 104)
        meter = pygame.Rect(m, brand.bottom + 24, 92 if not compact else 78,
                            int(H * (0.66 if not compact else 0.70)) -
                            brand.bottom - 24)
        tw = 416 if W >= 1000 else 320
        ticker = pygame.Rect(W - tw - m, H - 68, tw, 34)
        return brand, tele, meter, ticker

    # ── main draw ──────────────────────────────────────────────────────
    def draw(self, screen, cx=None, cy=None, r=0.0):
        if not self.enabled:
            return
        import time as _time
        _t0 = _time.perf_counter()
        W, H = screen.get_size()
        self.w, self.h = W, H
        if self._plate is None or self._plate.get_size() != (W, H):
            self._plate = pygame.Surface((W, H), pygame.SRCALPHA)
            self._ring_surf = pygame.Surface((W, H), pygame.SRCALPHA)
        surf = self._plate
        surf.fill((0, 0, 0, 0))
        ring = self._ring_surf
        ring.fill((0, 0, 0, 0))

        brand, tele, meter, ticker = self._layout(W, H)

        # the parallax - the panels sit on different depths
        def shift(rect, depth):
            return rect.move(int(self._px * depth * 9), int(self._py * depth * 6))

        brand = shift(brand, 1.0)
        tele = shift(tele, -0.85)
        meter = shift(meter, 0.65)
        ticker = shift(ticker, -0.5)

        # the frosted backdrops are sampled from the scene, before we draw
        frosts = {}
        if self.glass and self._perf > 0.18:
            for key, rect in (("brand", brand), ("tele", tele),
                              ("meter", meter), ("ticker", ticker)):
                f = self._frost(screen, rect)
                if f is not None:
                    frosts[key] = f

        flash = max(0.0, self._flash.v)
        lvl = max(0.0, self._lvl.v)

        self._draw_grid(surf, cx, cy)
        self._draw_pulses(surf, cx, cy, r)
        self._draw_brackets(surf, lvl, flash)

        if cx is not None and cy is not None and r > 8:
            self._draw_ring(ring, cx, cy, r, flash)
            surf.blit(ring, (0, 0))

        self._draw_brand(surf, brand, lvl, flash, frosts.get("brand"))
        self._draw_telemetry(surf, tele, lvl, flash, frosts.get("tele"))
        self._draw_meter(surf, meter, lvl, flash, frosts.get("meter"))
        self._draw_ticker(surf, ticker, lvl, flash, frosts.get("ticker"))

        if cx is not None and cy is not None:
            self._reticle(surf, brand, cx, cy)
            self._reticle(surf, tele, cx, cy)
            self._reticle(surf, meter, cx, cy)
            self._reticle(surf, ticker, cx, cy)

        self._draw_scan(surf)
        self._draw_boot(surf)
        screen.blit(surf, (0, 0))

        if self.post:
            self._post_process(screen)

        # how long did the layer take? the quality control uses this
        self._cost = self._cost * 0.85 + (_time.perf_counter() - _t0) * 0.15

    # ── pieces ─────────────────────────────────────────────────────────
    def _draw_grid(self, surf, cx, cy):
        """A whisper-thin perspective floor - pure atmosphere."""
        W, H = self.w, self.h
        rev = _ease_out(self._reveal / 0.8)
        if rev <= 0.01:
            return
        horizon = H * 0.62
        vx = W * 0.5 if cx is None else cx
        a = int(11 * rev)
        col = hsv(self.hue, 0.35, 0.95, a)
        for i in range(1, 13):
            k = (i / 12.0) ** 2.2
            y = horizon + k * (H - horizon)
            pygame.draw.line(surf, col, (0, int(y)), (W, int(y)), 1)
        for i in range(-7, 8):
            x_far = vx + i * (W / 7.0)
            x_near = vx + i * (W / 2.2)
            pygame.draw.line(surf, col, (x_far, int(horizon)),
                             (x_near, H), 1)

    def _draw_pulses(self, surf, cx, cy, r):
        """Sonar rings that run out of the orb on every loud syllable."""
        if cx is None or cy is None:
            return
        rev = _ease_out(self._reveal / 0.7)
        for age, amp in self._pulses:
            e = _ease_out(age)
            rr = r * 0.55 + e * max(self.w, self.h) * 0.42
            a = int((1.0 - age) ** 2 * 90 * amp * rev)
            if a <= 1:
                continue
            pygame.draw.circle(surf, hsv(self.hue, 0.40, 1.0, a),
                               (int(cx), int(cy)), int(rr), 1)

    def _draw_brackets(self, surf, lvl, flash):
        m = 18
        L = 32
        rev = _ease_out(self._reveal / 0.6)
        a = int((70 + 90 * lvl + 90 * flash) * rev)
        col = hsv(self.hue, 0.50, 0.95, a)
        for (x, y, dx, dy) in ((m, m, 1, 1), (self.w - m, m, -1, 1),
                               (m, self.h - m, 1, -1),
                               (self.w - m, self.h - m, -1, -1)):
            pygame.draw.line(surf, col, (x, y), (x + dx * L, y), 2)
            pygame.draw.line(surf, col, (x, y), (x, y + dy * L), 2)
            pygame.draw.line(surf, col, (x + dx * L, y),
                             (x + dx * (L - 8), y + dy * 5), 1)

    def _draw_brand(self, surf, rect, lvl, flash, frost=None):
        rev = _ease_out((self._reveal - 0.05) / 0.55)
        if rev <= 0:
            return
        a = int(255 * rev)
        self._panel(surf, rect, None, delay=0.05, frost=frost)

        self._label(surf, "AK", (rect.x + 18, rect.y + 12), (238, 243, 255),
                    size=23, alpha=a)
        self._label(surf, "VOICE CORE", (rect.x + 58, rect.y + 20),
                    (126, 138, 166), size=9, alpha=int(200 * rev), track=2.4)

        state = (self.state or "idle").upper()
        chip_col = {"SPEAKING": hsv(142, 0.60, 0.95, a),
                    "LISTENING": hsv(198, 0.55, 0.95, a),
                    "THINKING": hsv(38, 0.70, 0.98, a),
                    "RESPONDING": hsv(278, 0.55, 0.95, a)}.get(
                        state, (150, 158, 180, a))
        cw = 34 + int(len(state) * 7.2)
        chip = pygame.Rect(rect.x + 18, rect.y + 40, cw, 19)
        pygame.draw.rect(surf, (chip_col[0], chip_col[1], chip_col[2],
                                int(30 * rev)), chip, border_radius=9)
        pygame.draw.rect(surf, (chip_col[0], chip_col[1], chip_col[2],
                                int(120 * rev)), chip, width=1, border_radius=9)
        dot = int(140 + 115 * (0.5 + 0.5 * math.sin(self.t * 5.2)))
        pygame.draw.circle(surf, (chip_col[0], chip_col[1], chip_col[2],
                                  int(dot * rev)),
                           (chip.x + 12, chip.centery), 3)
        self._label(surf, state, (chip.x + 21, chip.y + 5), chip_col, size=9,
                    alpha=a, track=1.4)

        bar = pygame.Rect(rect.x + 18, rect.bottom - 12, rect.w - 36, 2)
        pygame.draw.rect(surf, (70, 80, 105, int(80 * rev)), bar)
        fill = pygame.Rect(bar.x, bar.y,
                           max(1, int(bar.w * min(1.0, lvl * 1.5))), 2)
        pygame.draw.rect(surf, hsv(self.hue, 0.60, 1.0, int(220 * rev)), fill)

    def _draw_telemetry(self, surf, rect, lvl, flash, frost=None):
        rev = _ease_out((self._reveal - 0.14) / 0.55)
        if rev <= 0:
            return
        a = int(255 * rev)
        self._panel(surf, rect, None, delay=0.14, frost=frost)
        self._label(surf, "TELEMETRY", (rect.x + 16, rect.y + 11),
                    (120, 132, 160), size=9, alpha=int(200 * rev), track=2.6)
        pygame.draw.line(surf, (120, 140, 180, int(55 * rev)),
                         (rect.x + 14, rect.y + 26), (rect.right - 14, rect.y + 26), 1)

        db = self._db.v
        rows = (
            ("LEVEL", "%+.1f" % db, "dB"),
            ("CENTER", ("%.2fk" % (self.centroid / 1000.0))
             if self.centroid >= 1000 else ("%d" % int(self.centroid)), "Hz"),
            ("FPS", "%d" % int(self.fps), ""),
        )
        y = rect.y + 34
        for name, val, unit in rows:
            self._label(surf, name, (rect.x + 16, y + 3), (120, 132, 160),
                        size=9, mono=True, alpha=int(210 * rev), track=1.2)
            self._big(surf, val, (rect.x + 16, y + 14), (222, 230, 246),
                      size=19, alpha=a)
            if unit:
                vw = _font(19, True).size(val)[0]
                self._label(surf, unit, (rect.x + 20 + vw, y + 22),
                            (120, 132, 160), size=9, mono=True,
                            alpha=int(190 * rev), track=1.0)
            y += 26

    def _draw_meter(self, surf, rect, lvl, flash, frost=None):
        """A vertical spectrum column on the left edge - out of the way of
        the spoken caption, and it reads like a real instrument."""
        rev = _ease_out((self._reveal - 0.22) / 0.55)
        if rev <= 0:
            return
        self._panel(surf, rect, None, delay=0.22, frost=frost)
        self._label(surf, "SPECTRUM", (rect.x + 14, rect.y + 11),
                    (120, 132, 160), size=9, alpha=int(200 * rev), track=2.4)

        base_x = rect.x + 34
        span = rect.w - 48
        n = self.N_BANDS
        step = (rect.h - 44) / n
        for i in range(n):
            v = float(self.bands[i])
            y = rect.y + 34 + i * step
            bh = max(2, int(step - 3))
            w = 2 + v * span
            col = hsv(self.hue + 150.0 * (i / max(1, n - 1)), 0.50,
                      0.40 + 0.60 * v, int((120 + 110 * v) * rev))
            pygame.draw.rect(surf, col, (base_x, int(y), max(1, int(w)), bh),
                             border_radius=1)
            pk = float(self.peaks[i])
            if pk > 0.03:
                pygame.draw.rect(surf, hsv(self.hue, 0.30, 1.0, int(170 * rev)),
                                 (base_x + int(pk * span), int(y), 2, bh))
        pygame.draw.line(surf, (120, 140, 180, int(60 * rev)),
                         (base_x, rect.y + 30),
                         (base_x, rect.bottom - 12), 1)
        self._label(surf, "20", (rect.x + 12, rect.bottom - 22),
                    (110, 122, 150), size=8, mono=True,
                    alpha=int(170 * rev), track=1.0)
        self._label(surf, "16K", (rect.x + 12, rect.y + 24),
                    (110, 122, 150), size=8, mono=True,
                    alpha=int(170 * rev), track=1.0)

    def _draw_ticker(self, surf, rect, lvl, flash, frost=None):
        """Kinetic type - every letter rises into place on its own beat."""
        rev = _ease_out((self._reveal - 0.30) / 0.55)
        if rev <= 0:
            return
        self._panel(surf, rect, None, delay=0.30, frost=frost)
        text = self._text or "Always listening. Just speak."
        f = _font(12)
        base_y = rect.centery - 8
        x = rect.x + 14
        shown = int(self._typed)
        for i, ch in enumerate(text[:shown]):
            p = _ease_out(max(0.0, min(1.0, self._typed - i)))
            rise = (1.0 - p) * 9.0
            g = _glyph(f, ch, (214, 222, 242))
            g.set_alpha(int(255 * p))
            surf.blit(g, (x, int(base_y + rise)))
            x += g.get_width()
            if x > rect.right - 26:
                break
        if shown < len(text) and int(self.t * 3.0) % 2 == 0:
            caret = _glyph(f, "|", hsv(self.hue, 0.60, 1.0))
            surf.blit(caret, (min(x, rect.right - 20), int(base_y)))

        uy = rect.bottom - 5
        pygame.draw.rect(surf, (120, 140, 180, int(45 * rev)),
                         (rect.x + 12, uy, rect.w - 24, 1))
        pos = (self.t * 0.20) % 1.0
        px = rect.x + 12 + int((rect.w - 24) * pos)
        pygame.draw.rect(surf, hsv(self.hue, 0.65, 1.0, int(210 * rev)),
                         (px, uy - 1, 30, 2), border_radius=1)

    def _draw_ring(self, surf, cx, cy, r, flash):
        """The instrument ring hugging the orb."""
        R = r + 28
        rev = _ease_out(self._reveal / 0.75)
        if rev <= 0:
            return
        pygame.draw.circle(surf, (140, 165, 210, int(46 * rev)),
                           (int(cx), int(cy)), int(R), 1)

        ticks = 90
        for i in range(ticks):
            ang = self._ring + i * 2 * math.pi / ticks
            major = (i % 5 == 0)
            l = 11 if major else 4
            x1 = cx + math.cos(ang) * R
            y1 = cy + math.sin(ang) * R
            x2 = cx + math.cos(ang) * (R + l)
            y2 = cy + math.sin(ang) * (R + l)
            al = int((30 + 120 * self._lvl.v) * rev) if major else int(22 * rev)
            pygame.draw.line(surf, hsv(self.hue, 0.45, 0.95, al), (x1, y1),
                             (x2, y2), 2 if major else 1)

        # a radar sweep - a soft wedge that goes round once every ~11 s
        steps = 26
        for i in range(steps):
            f = i / steps
            ang = self._sweep - f * 1.15
            rr = R - 1
            x = cx + math.cos(ang) * rr
            y = cy + math.sin(ang) * rr
            rr2 = R - 1 - f * 26
            x2 = cx + math.cos(ang) * rr2
            y2 = cy + math.sin(ang) * rr2
            al = int((26 * (1.0 - f)) * rev)
            if al > 1:
                pygame.draw.line(surf, hsv(self.hue, 0.55, 1.0, al), (x, y),
                                 (x2, y2), 1)

        # the voice waveform riding on the ring
        n = self._wave.size
        pts = []
        for i in range(n):
            ang = i * 2 * math.pi / n + self._ring * 0.4
            rr = R + self._wave[i] * 18.0
            pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
        if len(pts) > 2:
            pygame.draw.lines(surf, hsv(self.hue, 0.60, 0.98,
                                        int((110 + 120 * self._lvl.v) * rev)),
                              False, pts, 2)

        if self._lvl.v > 0.005:
            box = pygame.Rect(int(cx - R - 11), int(cy - R - 11),
                              int(R * 2 + 22), int(R * 2 + 22))
            pygame.draw.arc(surf, hsv(self.hue + 18, 0.75, 1.0,
                                      int((110 + 120 * self._lvl.v +
                                           90 * flash) * rev)),
                            box, -math.pi / 2,
                            -math.pi / 2 + 2 * math.pi * min(1.0, self._lvl.v * 1.15), 3)

        if flash > 0.01:
            fr = R + (1.0 - flash) * 42.0
            pygame.draw.circle(surf, hsv(self.hue, 0.35, 1.0,
                                         int(150 * flash * rev)),
                               (int(cx), int(cy)), int(fr), 2)

    def _draw_scan(self, surf):
        """A slow scan line drifting down the glass."""
        W, H = self.w, self.h
        rev = _ease_out(self._reveal / 0.8)
        if rev <= 0.01:
            return
        sy = int(self._scan * H)
        line = pygame.Surface((W, 2), pygame.SRCALPHA)
        line.fill(hsv(self.hue, 0.40, 0.9, int(24 * rev)))
        surf.blit(line, (0, sy))
        glow = pygame.Surface((W, 46), pygame.SRCALPHA)
        for i in range(23):
            a = int(11 * (1.0 - i / 23.0) * rev)
            if a > 0:
                pygame.draw.rect(glow, hsv(self.hue, 0.40, 0.9, a),
                                 (0, i * 2, W, 2))
        surf.blit(glow, (0, max(0, sy - 46)))

    def _draw_boot(self, surf):
        """The first second: a light sweep runs across the whole window."""
        if self._boot >= 1.0:
            return
        e = _ease_out(self._boot)
        W = self.w
        x = int((e * 1.25 - 0.12) * W)
        band = pygame.Surface((W, 3), pygame.SRCALPHA)
        band.fill(hsv(self.hue, 0.45, 1.0, int(60 * (1.0 - e))))
        surf.blit(band, (0, int(self.h * 0.5)))
        glow = pygame.Surface((W, 120), pygame.SRCALPHA)
        for i in range(60):
            a = int(6 * (1.0 - abs(i - 30) / 30.0) * (1.0 - e))
            if a > 0:
                pygame.draw.rect(glow, hsv(self.hue, 0.45, 1.0, a),
                                 (0, i * 2, W, 2))
        surf.blit(glow, (0, int(self.h * 0.5) - 120))

    # ── the cinematic post pass ────────────────────────────────────────
    def _vignette_mask(self, W, H):
        """A cached soft vignette - multiplied over the frame."""
        if self._vignette is not None and self._vignette.get_size() == (W, H):
            return self._vignette
        w, h = max(2, W // 4), max(2, H // 4)
        yy, xx = np.mgrid[0:h, 0:w]
        nx = (xx / w - 0.5) * 2.0
        ny = (yy / h - 0.5) * 2.0
        d = np.sqrt(nx * nx * 0.82 + ny * ny)
        v = np.clip(1.0 - 0.62 * np.clip(d - 0.42, 0, None) ** 1.6, 0.30, 1.0)
        arr = (v * 255).astype(np.uint8)
        rgb = np.dstack([arr, arr, arr])
        surf = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        surf = pygame.transform.smoothscale(surf, (W, H))
        self._vignette = surf
        return surf

    def _grain_tile(self, W, H):
        """Fine film grain - a small tile, refreshed a few times a second."""
        self._grain_age += 1
        if self._grain is None or self._grain_age > 5:
            self._grain_age = 0
            n = 512
            rng = np.random.default_rng(int((self.t * 1000) % 100000))
            noise = rng.integers(0, 7, size=(n, n), dtype=np.uint8)
            rgb = np.dstack([noise, noise, noise])
            self._grain = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        return self._grain

    def _post_process(self, screen):
        """Bloom, vignette and grain - the cinematic finish.

        Each layer steps aside when the frame time goes up, so the
        animation always stays smooth.
        """
        W, H = screen.get_size()
        q = self._perf
        try:
            # ── bloom on the bright parts (every third frame, it changes
            #    too slowly for anyone to notice) ───────────────────
            if q > 0.22:
                self._bloom_age = getattr(self, "_bloom_age", 0) + 1
                if self._bloom_age % 3 == 0 or self._bloom is None:
                    self._bloom_age = 0
                    # downscale first, threshold, then blur - reading the
                    # full frame back into numpy cost 29ms a frame, this
                    # whole pass costs about one
                    small = pygame.transform.smoothscale(
                        screen, (max(2, W // 8), max(2, H // 8)))
                    small.fill((58, 58, 58), special_flags=pygame.BLEND_RGBA_SUB)
                    self._bloom = pygame.transform.smoothscale(
                        small, (max(2, W // 4), max(2, H // 4)))
                if self._bloom is not None:
                    screen.blit(self._bloom, (0, 0, W, H),
                                special_flags=pygame.BLEND_RGB_ADD)

            # ── vignette ────────────────────────────────────────────
            if q > 0.45:
                vig = self._vignette_mask(W, H)
                screen.blit(vig, (0, 0), special_flags=pygame.BLEND_RGB_MULT)

            # ── film grain ──────────────────────────────────────────
            if q > 0.72:
                tile = self._grain_tile(W, H)
                for y in range(0, H, 256):
                    for x in range(0, W, 256):
                        screen.blit(tile, (x, y),
                                    special_flags=pygame.BLEND_RGB_ADD)
        except Exception:
            pass
