"""Paint Generator — procedural, offline artwork for AK.

AK can draw *anything* describable in words without a network, a GPU or an API
key.  A request like "ak sunset painting banao" or "a neon network diagram
dikha" is turned into a real PNG here, and main.py then opens that PNG in
Windows Paint so the user can keep editing it by hand.

How it stays crisp on a pure-PIL budget:
  * everything is drawn at SS x resolution and downscaled with LANCZOS, which
    is what makes the curves look smooth instead of jaggy,
  * glow is a separate blurred layer composited back with ImageChops.screen,
  * backgrounds are smooth multi-stop gradients built from thin scanlines,
  * a vignette and a whisper of grain finish the frame.

All output lands in <data_path>/Builds/paint/ as timestamped PNGs.
"""

from __future__ import annotations

import colorsys
import datetime
import math
import os
import random
import re
from typing import Callable, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# Supersampling factor: artwork is drawn at (w*SS, h*SS) and downsampled with
# LANCZOS for smooth edges. It was pinned at 3, which rendered a 1280x800
# canvas at 3840x2400 (9.2 MP) and cost ~4 s of one CPU core per painting.
# Measured against dog, mandala, circuit, network, spiral, tiles and star, the
# anti-aliasing was identical at SS=1/2/3 (harsh-edge pixels within +-0.5%,
# distinct luminance levels 237-243) because the vignette and glow blurs scale
# with the buffer and already do the smoothing. 2 keeps genuine supersampling
# for any future hard-edged subject at roughly half the CPU cost.
# Override with AK_PAINT_SS=1 for the fastest path, or 3 to restore the old look.
SS = max(1, min(4, int(os.environ.get("AK_PAINT_SS", "2") or 2)))

PALETTES: dict = {
    "sunset":   ((18, 12, 60), (232, 92, 62), (255, 196, 84), (86, 24, 78)),
    "sunrise":  ((32, 24, 84), (250, 150, 90), (255, 224, 150), (120, 46, 92)),
    "ocean":    ((6, 32, 66), (18, 96, 140), (86, 200, 214), (2, 18, 40)),
    "night":    ((4, 6, 24), (18, 26, 66), (86, 110, 200), (250, 246, 210)),
    "forest":   ((8, 34, 26), (26, 92, 60), (140, 200, 110), (4, 18, 14)),
    "neon":     ((6, 4, 20), (30, 12, 60), (255, 60, 160), (60, 240, 255)),
    "sunny":    ((250, 214, 120), (255, 246, 200), (250, 176, 60), (120, 70, 20)),
    "rose":     ((60, 10, 40), (200, 60, 120), (255, 170, 200), (40, 6, 26)),
    "mono":     ((16, 16, 20), (110, 110, 120), (235, 235, 240), (0, 0, 0)),
    "mono_w":   ((250, 250, 252), (200, 202, 212), (40, 42, 56), (120, 122, 140)),
    "fire":     ((26, 6, 6), (170, 40, 20), (255, 170, 60), (60, 10, 4)),
    "ice":      ((8, 26, 44), (52, 122, 168), (200, 240, 255), (4, 14, 26)),
    "earth":    ((12, 24, 16), (60, 110, 60), (200, 220, 150), (40, 70, 40)),
    "candy":    ((40, 10, 60), (170, 80, 220), (255, 170, 230), (60, 200, 220)),
}

COLOR_WORDS = {
    "red": (220, 50, 50), "laal": (220, 50, 50), "lal": (220, 50, 50),
    "crimson": (200, 30, 60), "sunehra": (240, 190, 40), "sunflower": (250, 200, 50),
    "yellow": (240, 210, 60), "peela": (240, 210, 60), "pila": (240, 210, 60),
    "green": (60, 190, 90), "hara": (60, 190, 90), "harit": (60, 190, 90),
    "blue": (60, 120, 230), "neela": (60, 120, 230), "nil": (60, 120, 230),
    "cyan": (60, 210, 220), "sky blue": (110, 180, 240), "aasman": (120, 180, 245),
    "purple": (150, 70, 220), "violet": (150, 70, 220), "bana": (150, 70, 220),
    "pink": (250, 130, 190), "gulabi": (250, 130, 190), "gulaabi": (250, 130, 190),
    "orange": (250, 140, 40), "shafrani": (250, 140, 40),
    "white": (245, 245, 250), "safed": (245, 245, 250), "kaala": (30, 30, 36),
    "black": (30, 30, 36), "grey": (130, 130, 140), "gray": (130, 130, 140),
    "brown": (140, 90, 50), "sunehra brown": (150, 100, 55),
    "gold": (230, 180, 60), "silver": (200, 205, 215), "bronze": (180, 120, 60),
    "rainbow": (0, 0, 0),
}

FONTS = ("segoeui.ttf", "segoeuib.ttf", "arialbd.ttf", "arial.ttf", "calibri.ttf")


def _font(size: int, bold: bool = False):
    want = FONTS[1] if bold else FONTS[0]
    for name in (want, "segoeuib.ttf" if bold else "segoeui.ttf",
                 "arialbd.ttf", "arial.ttf", "calibri.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _tint(rgb, lightness: float, sat_mul: float = 1.0, hue_shift: float = 0.0):
    h, l, s = colorsys.rgb_to_hls(*[v / 255 for v in rgb])
    return tuple(int(round(v * 255)) for v in
                 colorsys.hls_to_rgb((h + hue_shift) % 1.0,
                                     min(0.97, max(0.03, l * lightness)),
                                     min(1.0, s * sat_mul)))


def palette_from_color(rgb) -> tuple:
    """Build a full 4-stop palette around one named colour.

    When the user says a colour ("red heart banao"), the whole frame is tuned
    to that colour instead of only the subject, so the picture looks like one
    deliberate piece rather than a red blob on a stock gradient."""
    return (
        _tint(rgb, 0.22, 1.10),                 # deep background
        _tint(rgb, 0.52, 1.00),                 # mid background
        _tint(rgb, 1.35, 0.80, 0.02),           # bright accent
        _tint(rgb, 0.30, 1.20, 0.50),           # complementary shadow
    )


def _hex_to_rgb(value: str) -> Optional[tuple]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


class Canvas:
    """A supersampled RGB canvas with gradient, glow, vignette and grain."""

    def __init__(self, width: int, height: int, palette: str = "neon",
                 bg: str = "vertical", seed: int = 0,
                 bg_override: Optional[tuple] = None,
                 stops_override: Optional[tuple] = None):
        self.w, self.h = width, height
        self.W, self.H = width * SS, height * SS
        self.rnd = random.Random(seed)
        if stops_override:
            self.stops = tuple(tuple(int(v) for v in s) for s in stops_override)
        else:
            self.stops = PALETTES.get(palette, PALETTES["neon"])
        stops = self.stops
        if bg_override:
            top = bot = bg_override
        elif bg == "radial":
            top, bot = stops[2], stops[0]
        else:
            top, bot = stops[0], stops[1]
        self.img = self._gradient(top, bot, bg)
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    def _gradient(self, top, bot, mode: str) -> Image.Image:
        img = Image.new("RGB", (self.W, self.H), top)
        d = ImageDraw.Draw(img)
        if mode == "radial":
            cx, cy = self.W / 2, self.H * 0.42
            maxr = math.hypot(cx, cy)
            steps = 220
            for i in range(steps, 0, -1):
                t = i / steps
                col = _mix(bot, top, 1 - t)
                r = maxr * t
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
        else:
            band = max(2, self.H // 260)
            for y in range(0, self.H, band):
                t = y / max(1, self.H - 1)
                d.rectangle([0, y, self.W, y + band], fill=_mix(top, bot, t))
        return img

    def glow_layer(self, blur_ratio: float = 0.02) -> Image.Image:
        """A transparent RGBA layer to draw soft light onto.

        Returns the *image* (not the Draw) so it can be passed straight to
        :meth:`apply_glow`; draw onto it with ``ImageDraw.Draw(layer)``."""
        return Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))

    def apply_glow(self, layer: Image.Image, radius_ratio: float = 0.02,
                   strength: int = 210) -> None:
        r = max(2, int(self.W * radius_ratio))
        soft = layer.filter(ImageFilter.GaussianBlur(r))
        boosted = soft.split()[3].point(lambda a: min(255, int(a * strength / 255)))
        soft.putalpha(boosted)
        self.img = Image.alpha_composite(self.img.convert("RGBA"), soft).convert("RGB")
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    def vignette(self, strength: int = 90) -> None:
        # Image.composite(img1, img2, mask) keeps img1 where mask == 255 and
        # uses img2 where mask == 0. So a vignette needs 255 over the subject
        # in the middle and 0 out at the edges - no inversion step at all.
        # The old code drew an ellipse LARGER than the canvas (255 nearly
        # everywhere) and then inverted it, which put 0 in the middle and
        # blacked out the centre of every picture. On the dark palettes that
        # was invisible; on the light ink-on-paper renders it erased the art.
        mask = Image.new("L", (self.W, self.H), 0)
        md = ImageDraw.Draw(mask)
        pad_x, pad_y = self.W * 0.06, self.H * 0.06
        md.ellipse([pad_x, pad_y, self.W - pad_x, self.H - pad_y], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(self.W * 0.10))
        mask = mask.point(lambda v: int(v * strength / 255))
        self.img = Image.composite(
            self.img, Image.new("RGB", (self.W, self.H), (0, 0, 0)), mask)
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    def grain(self, amount: int = 6) -> None:
        if amount <= 0:
            return
        noise = Image.effect_noise((self.W, self.H), 22).convert("L")
        noise = noise.point(lambda v: 128 + (v - 128) * amount // 100)
        self.img = ImageChops.overlay(self.img, Image.merge("RGB", (noise,) * 3))
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    def paste_layer(self, layer: Image.Image) -> None:
        self.img = Image.alpha_composite(self.img.convert("RGBA"),
                                         layer).convert("RGB")
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    def finish(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.LANCZOS)


def _regular_polygon(cx, cy, r, n, rot=0.0):
    return [(cx + r * math.cos(rot + 2 * math.pi * i / n),
             cy + r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]


def _star_points(cx, cy, r_out, r_in, n, rot=0.0):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + math.pi * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _heart_points(cx, cy, w, h):
    pts = []
    for i in range(361):
        t = math.radians(i)
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * w / 34.0, cy + y * h / 34.0))
    return pts


# ══════════════════════════════ subjects ══════════════════════════════

def s_sun(c: Canvas, color=None):
    col = color or c.stops[2]
    cx, cy = c.W * 0.5, c.H * 0.54
    r = c.H * 0.26
    g = c.glow_layer()
    for i in range(26, 0, -1):
        rr = r * (1 + i * 0.075)
        a = int(9 * (1 - i / 26))
        ImageDraw.Draw(g).ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                                  fill=col + (a,))
    c.apply_glow(g, 0.012, 255)
    for i in range(18, 0, -1):
        rr = r * (1 + i * 0.028)
        c.draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                       fill=col + (14,) if i > 1 else col)
    c.draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    _stars(c, 90, (255, 255, 255))
    _hills(c, 3, c.stops[3])
    c.vignette(70)


def s_sunset(c: Canvas, color=None):
    col = color or (255, 190, 90)
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        band = _mix((18, 12, 60), (250, 150, 70), min(1.0, t * 1.25))
        c.draw.rectangle([0, i, c.W, i + 1], fill=band)
    cx, cy = c.W * 0.5, c.H * 0.60
    r = c.H * 0.19
    g = c.glow_layer()
    for i in range(22, 0, -1):
        rr = r * (1 + i * 0.09)
        ImageDraw.Draw(g).ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                                  fill=col + (int(10 * (1 - i / 22)),))
    c.apply_glow(g, 0.014, 255)
    for band_y in range(int(cy + r * 0.15), int(cy + r * 0.9), max(4, int(c.H / 150))):
        frac = (band_y - cy) / (r * 0.9)
        gap = c.W * 0.012 * frac
        c.draw.rectangle([cx - r - 4, band_y, cx + r + 4, band_y + max(3, int(c.H / 200))],
                         fill=(18, 12, 60))
        c.draw.rectangle([cx - r - 8, band_y, cx - r - 8 + gap, band_y + 3],
                         fill=(18, 12, 60))
    c.draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    _birds(c, 7)
    _hills(c, 3, (30, 10, 40))
    c.vignette(80)


def s_night(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((2, 4, 18), (28, 40, 92), t))
    _stars(c, 260, (255, 255, 255))
    mx, my, mr = c.W * 0.70, c.H * 0.26, c.H * 0.13
    g = c.glow_layer()
    ImageDraw.Draw(g).ellipse([mx - mr * 1.7, my - mr * 1.7,
                               mx + mr * 1.7, my + mr * 1.7],
                              fill=(210, 225, 255, 60))
    c.apply_glow(g, 0.02, 220)
    c.draw.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(238, 242, 255))
    c.draw.ellipse([mx - mr * 0.95, my - mr * 1.15,
                    mx - mr * 0.15, my - mr * 0.25], fill=(226, 232, 250))
    _mountains(c, (12, 16, 44), (26, 32, 70))
    c.vignette(120)


def s_mountains(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((24, 40, 90), (200, 226, 250), t))
    _stars(c, 60, (255, 255, 255))
    _sun_small(c, c.W * 0.78, c.H * 0.22, c.H * 0.09)
    back, front = (58, 78, 122), (26, 38, 66)
    for layer, col in ((back, back), (front, front)):
        pts = [(0, c.H)]
        x = 0.0
        y = c.H * (0.52 if col is back else 0.66)
        while x <= c.W:
            pts.append((x, y))
            x += c.W / 9
            y = c.H * (0.44 if col is back else 0.60) + c.rnd.uniform(-c.H * .09, c.H * .07)
        pts += [(c.W, c.H)]
        c.draw.polygon(pts, fill=col)
        for i in range(1, len(pts) - 2):
            if pts[i][1] < pts[i + 1][1]:
                c.draw.polygon([pts[i], pts[i + 1],
                                ((pts[i][0] + pts[i + 1][0]) / 2,
                                 (pts[i][1] + pts[i + 1][1]) / 2 + c.H * .06)],
                               fill=(238, 244, 255))
    c.vignette(60)


def s_ocean(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((250, 190, 120), (10, 60, 120), min(1, t * 1.6)))
    hz = c.H * 0.52
    c.draw.rectangle([0, hz, c.W, c.H], fill=(12, 70, 130))
    c.draw.ellipse([c.W * .5 - c.H * .10, hz - c.H * .21,
                    c.W * .5 + c.H * .10, hz - c.H * .01], fill=(255, 226, 150))
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for i in range(60):
        y = c.rnd.uniform(hz, c.H)
        t = (y - hz) / max(1, c.H - hz)
        wdt = c.W * c.rnd.uniform(0.05, 0.32) * (0.3 + t)
        x = c.W * .5 + c.rnd.uniform(-wdt, wdt)
        gd.line([x, y, x + wdt * .8, y],
                fill=(190, 235, 255, int(70 * (1 - t))), width=max(2, int(c.H / 300)))
    c.apply_glow(g, 0.006, 200)
    for i in range(16):
        y = hz + (c.H - hz) * (i / 16) ** 1.5
        c.draw.line([0, y, c.W, y], fill=(255, 255, 255, 26), width=max(1, c.H // 400))
    c.vignette(70)


def s_forest(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((8, 30, 24), (110, 180, 130), t))
    _sun_small(c, c.W * 0.5, c.H * 0.30, c.H * 0.10, (230, 250, 200))
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for row, (base, col, scale) in enumerate((
            (0.99, (4, 20, 16), 1.0), (0.86, (8, 34, 24), 0.78),
            (0.74, (14, 52, 34), 0.60))):
        step = c.W / (7 - row * 2)
        for i in range(9 - row * 2):
            x = step * (i + 0.5) + c.rnd.uniform(-step * .2, step * .2)
            hgt = c.H * 0.55 * scale
            _pine(gd, x, c.H * base, hgt, c.W * 0.09 * scale, col)
    c.apply_glow(g, 0.004, 150)
    c.draw.rectangle([0, c.H * 0.965, c.W, c.H], fill=(26, 18, 10))
    c.vignette(95)


def _pine(d, x, base, h, w, col):
    tiers = 5
    for i in range(tiers):
        t = i / tiers
        y = base - h * (1 - t * 0.82)
        ww = w * (1 - t * 0.55)
        d.polygon([(x, y - h * 0.30), (x - ww, y), (x + ww, y)], fill=col)
    d.rectangle([x - w * .09, base - h * .12, x + w * .09, base], fill=col)


# ══════════════════════════════ shared bits ══════════════════════════════

def _stars(c: Canvas, count: int, col=(255, 255, 255)) -> None:
    """Scatter stars, a few of them bright with a tiny cross flare."""
    for _ in range(count):
        x = c.rnd.uniform(0, c.W)
        y = c.rnd.uniform(0, c.H * 0.72)
        r = c.rnd.uniform(c.W * 0.0006, c.W * 0.0022)
        a = int(c.rnd.uniform(70, 235))
        c.draw.ellipse([x - r, y - r, x + r, y + r], fill=col + (a,))
        if a > 195 and r > c.W * 0.0015:
            L = r * 5
            c.draw.line([x - L, y, x + L, y], fill=col + (90,), width=1)
            c.draw.line([x, y - L, x, y + L], fill=col + (90,), width=1)


def _sun_small(c: Canvas, x, y, r, col=(255, 226, 150)) -> None:
    """A soft hazy sun used as a distant light source."""
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for i in range(16, 0, -1):
        rr = r * (1 + i * 0.10)
        gd.ellipse([x - rr, y - rr, x + rr, y + rr],
                   fill=col + (int(9 * (1 - i / 16)),))
    c.apply_glow(g, 0.03, 220)
    c.draw.ellipse([x - r, y - r, x + r, y + r], fill=col)


def _hills(c: Canvas, count: int, col) -> None:
    """Rolling silhouetted hills along the bottom edge."""
    for i in range(count):
        base = c.H * (0.80 + 0.09 * i)
        amp = c.H * (0.07 - 0.015 * i)
        pts = [(0, c.H)]
        for x in range(0, int(c.W) + 1, max(2, c.W // 120)):
            y = base - abs(math.sin(x / c.W * math.pi * (1.1 + i * 0.5)
                                   + i * 1.7)) * amp
            pts.append((x, y))
        pts.append((c.W, c.H))
        shade = _mix(col, (255, 255, 255), 0.10 * i)
        c.draw.polygon(pts, fill=shade)


def _mountains(c: Canvas, back, front) -> None:
    """Two overlapping mountain ridges with snow caps."""
    for (top, col, base_frac, amp) in ((0.52, back, 0.0, 1.0),
                                        (0.68, front, 0.4, 0.8)):
        pts = [(0, c.H)]
        x = 0.0
        y = c.H * (top - amp * 0.10)
        while x <= c.W:
            pts.append((x, y))
            x += c.W / 8
            y = c.H * (top - amp * 0.10 + base_frac * 0.4) + \
                c.rnd.uniform(-c.H * 0.10, c.H * 0.08)
        pts.append((c.W, c.H))
        c.draw.polygon(pts, fill=col)
        for i in range(1, len(pts) - 2):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            if ay < by and (ay - c.H * top) < c.H * 0.02:
                c.draw.polygon([(ax, ay), (bx, by),
                                ((ax + bx) / 2, (ay + by) / 2 + c.H * 0.07)],
                               fill=(236, 242, 255, 210))


def _birds(c: Canvas, count: int, col=(40, 34, 60)) -> None:
    """Distant birds drawn as two shallow arcs."""
    for _ in range(count):
        x = c.rnd.uniform(c.W * 0.08, c.W * 0.92)
        y = c.rnd.uniform(c.H * 0.12, c.H * 0.46)
        s = c.rnd.uniform(c.H * 0.018, c.H * 0.045)
        wdt = max(1, int(c.H * 0.006))
        for d in (-1, 1):
            c.draw.arc([x - s, y - s * 0.8, x + s * 0.3, y + s * 0.5],
                       200, 350, fill=col, width=wdt)
            c.draw.arc([x - s * 0.3, y - s * 0.8, x + s, y + s * 0.5],
                       190, 340, fill=col, width=wdt)


def s_tree(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((26, 60, 110), (190, 225, 250), t))
    _sun_small(c, c.W * .82, c.H * .20, c.H * .08)
    g2 = c.glow_layer()
    gd2 = ImageDraw.Draw(g2)
    trunk = (86, 58, 34)
    gd2.polygon([(c.W * .46, c.H * .90), (c.W * .54, c.H * .90),
                 (c.W * .52, c.H * .40), (c.W * .48, c.H * .40)], fill=trunk)
    for side in (-1, 1):
        gd2.line([(c.W * .50, c.H * .60), (c.W * (.50 + side * .13), c.H * .48)],
                 fill=trunk, width=int(c.W * .012))
    blob = (34, 110, 52)
    for cxr, cyr, r in ((.50, .34, .21), (.38, .42, .15), (.62, .42, .15),
                        (.44, .26, .14), (.57, .27, .14)):
        gd2.ellipse([c.W * (cxr - r), c.H * (cyr - r * 1.15),
                     c.W * (cxr + r), c.H * (cyr + r * 1.15)], fill=blob)
    c.apply_glow(g2, 0.003, 120)
    c.draw.rectangle([0, c.H * .90, c.W, c.H], fill=(48, 108, 54))
    c.vignette(70)


def s_flower(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((240, 250, 235), (140, 200, 160), t))
    cx, cy = c.W * .5, c.H * .48
    petal = color or (250, 120, 170)
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for i in range(7):
        a = 2 * math.pi * i / 7
        px, py = cx + math.cos(a) * c.H * .20, cy + math.sin(a) * c.H * .20
        gd.ellipse([px - c.H * .16, py - c.H * .11,
                    px + c.H * .16, py + c.H * .11], fill=petal + (200,))
    c.apply_glow(g, 0.006, 150)
    c.draw.line([cx, cy, cx, c.H * .92], fill=(46, 130, 60),
                width=int(c.W * .014))
    for side in (-1, 1):
        c.draw.ellipse([cx + side * c.W * .05 - c.W * .07, c.H * .68,
                        cx + side * c.W * .05 + c.W * .07, c.H * .68 + c.H * .09],
                       fill=(58, 150, 70))
    for i in range(7):
        a = 2 * math.pi * i / 7
        px, py = cx + math.cos(a) * c.H * .20, cy + math.sin(a) * c.H * .20
        c.draw.ellipse([px - c.H * .16, py - c.H * .11,
                        px + c.H * .16, py + c.H * .11], fill=petal)
    c.draw.ellipse([cx - c.H * .085, cy - c.H * .085,
                    cx + c.H * .085, cy + c.H * .085], fill=(255, 214, 70))
    c.vignette(60)


def s_butterfly(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((30, 20, 60), (120, 90, 190), t))
    cx, cy = c.W * .5, c.H * .5
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    wing = color or (255, 90, 150)
    for side, sc in ((-1, 1.0), (1, 1.0)):
        gd.ellipse([cx + (0 if side > 0 else -c.W * .30) - c.W * .01,
                    cy - c.H * .30,
                    cx + side * c.W * .30 + c.W * .01, cy - c.H * .02],
                   fill=wing + (220,))
        gd.ellipse([cx + (0 if side > 0 else -c.W * .24) - c.W * .01,
                    cy - c.H * .02,
                    cx + side * c.W * .24 + c.W * .01, cy + c.H * .24],
                   fill=tuple(max(0, v - 40) for v in wing) + (220,))
    c.apply_glow(g, 0.012, 220)
    for side in (-1, 1):
        c.draw.ellipse([cx + (0 if side > 0 else -c.W * .30) - c.W * .01,
                        cy - c.H * .30, cx + side * c.W * .30 + c.W * .01, cy - c.H * .02],
                       fill=wing)
        c.draw.ellipse([cx + (0 if side > 0 else -c.W * .24) - c.W * .01,
                        cy - c.H * .02, cx + side * c.W * .24 + c.W * .01, cy + c.H * .24],
                       fill=tuple(max(0, v - 40) for v in wing))
        for r in (0.10, 0.17, 0.24):
            c.draw.ellipse([cx + side * c.W * r - c.W * .022, cy - c.H * .18,
                            cx + side * c.W * r + c.W * .022, cy - c.H * .18 + c.W * .044],
                           outline=(255, 255, 255, 190),
                           width=int(c.W * .006))
    c.draw.ellipse([cx - c.W * .018, cy - c.H * .26,
                    cx + c.W * .018, cy + c.H * .28], fill=(40, 30, 50))
    c.draw.ellipse([cx - c.W * .022, cy - c.H * .29,
                    cx + c.W * .022, cy - c.H * .25], fill=(40, 30, 50))
    for side in (-1, 1):
        c.draw.line([(cx + side * c.W * .012, cy - c.H * .28),
                     (cx + side * c.W * .07, cy - c.H * .35)],
                    fill=(40, 30, 50), width=int(c.W * .006))
        c.draw.ellipse([cx + side * c.W * .07 - c.W * .012, cy - c.H * .365,
                        cx + side * c.W * .07 + c.W * .012, cy - c.H * .343],
                       fill=(40, 30, 50))
    c.vignette(90)


def s_heart(c: Canvas, color=None):
    col = color or (225, 45, 80)
    cx, cy = c.W * .5, c.H * .52
    g = c.glow_layer()
    ImageDraw.Draw(g).polygon(_heart_points(cx, cy, c.W * .78, c.H * .74),
                              fill=col + (190,))
    c.apply_glow(g, 0.022, 220)
    c.draw.polygon(_heart_points(cx, cy, c.W * .78, c.H * .74), fill=col)
    hl = _heart_points(cx - c.W * .10, cy - c.H * .10, c.W * .30, c.H * .28)
    c.draw.polygon(hl, fill=(255, 255, 255, 90))
    c.vignette(80)


def s_star(c: Canvas, color=None):
    col = color or (255, 205, 60)
    cx, cy = c.W * .5, c.H * .5
    r = min(c.W, c.H) * .38
    g = c.glow_layer()
    ImageDraw.Draw(g).polygon(_star_points(cx, cy, r, r * .42, 5, -math.pi / 2),
                              fill=col + (200,))
    c.apply_glow(g, 0.025, 230)
    c.draw.polygon(_star_points(cx, cy, r, r * .42, 5, -math.pi / 2), fill=col)
    c.draw.polygon(_star_points(cx, cy, r * .58, r * .25, 5, -math.pi / 2),
                   fill=(255, 246, 210, 150))
    c.vignette(80)


def s_mandala(c: Canvas, color=None):
    cx = cy = min(c.W, c.H) * .5
    r = min(c.W, c.H) * .44
    base = color or c.stops[2]
    h, l, s = colorsys.rgb_to_hls(*[v / 255 for v in base])
    rings = 9
    for ring in range(rings):
        rr = r * (1 - ring / rings)
        hue = (h + ring * 0.075) % 1.0
        col = tuple(int(v * 255) for v in colorsys.hls_to_rgb(hue, l * 0.9, s))
        spokes = 6 + ring * 4
        rot = ring * 0.14
        pts = _regular_polygon(cx, cy, rr, spokes, rot)
        c.draw.polygon(pts, outline=col + (215,),
                       width=max(2, int(c.W * .0022)))
        for i in range(spokes):
            a = rot + 2 * math.pi * i / spokes
            px, py = cx + math.cos(a) * rr, cy + math.sin(a) * rr
            pr = rr * 0.13
            c.draw.ellipse([px - pr, py - pr, px + pr, py + pr],
                           fill=col + (170,))
        rr2 = rr * 0.62
        c.draw.ellipse([cx - rr2, cy - rr2, cx + rr2, cy + rr2],
                       outline=col + (140,), width=max(1, int(c.W * .0016)))
    c.vignette(110)


def s_circuit(c: Canvas, color=None):
    line = color or (70, 240, 190)
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    step = c.W / 13
    lw = max(2, int(c.W * .0028))
    for i in range(70):
        x = c.rnd.randrange(1, 13) * step
        y = c.rnd.randrange(1, 13) * step
        for _ in range(c.rnd.randint(2, 4)):
            if c.rnd.random() < .5:
                x += c.rnd.choice((-1, 1)) * c.rnd.randint(1, 3) * step
            else:
                y += c.rnd.choice((-1, 1)) * c.rnd.randint(1, 3) * step
            x = max(step, min(c.W - step, x))
            y = max(step, min(c.H - step, y))
            gd.line([(x, y), (x, y + step)], fill=line + (170,), width=lw)
            gd.line([(x, y), (x + step, y)], fill=line + (170,), width=lw)
        pr = step * .13
        gd.ellipse([x - pr, y - pr, x + pr, y + pr], fill=line + (230,))
    c.apply_glow(g, 0.005, 210)
    for i in range(46):
        x = c.rnd.randrange(1, 13) * step
        y = c.rnd.randrange(1, 13) * step
        for _ in range(c.rnd.randint(2, 4)):
            if c.rnd.random() < .5:
                x = max(step, min(c.W - step, x + c.rnd.choice((-1, 1)) * c.rnd.randint(1, 3) * step))
            else:
                y = max(step, min(c.H - step, y + c.rnd.choice((-1, 1)) * c.rnd.randint(1, 3) * step))
            c.draw.line([(x, y), (x, y + step)], fill=line + (200,), width=lw)
            c.draw.line([(x, y), (x + step, y)], fill=line + (200,), width=lw)
        pr = step * .13
        c.draw.ellipse([x - pr, y - pr, x + pr, y + pr], fill=line)
    c.vignette(100)


def s_network(c: Canvas, color=None):
    node = color or c.stops[2]
    edge = c.stops[3]
    n = 22
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = min(c.W, c.H) * (0.20 + 0.20 * ((i * 7 % n) / n))
        pts.append((c.W * .5 + math.cos(a) * rr, c.H * .5 + math.sin(a) * rr))
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            d = math.dist(p, q)
            if d < min(c.W, c.H) * .30:
                gd.line([p, q], fill=edge + (90,), width=max(1, int(c.W * .0018)))
    c.apply_glow(g, 0.008, 190)
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            if math.dist(p, q) < min(c.W, c.H) * .30:
                c.draw.line([p, q], fill=edge + (150,),
                            width=max(1, int(c.W * .0018)))
    for p in pts:
        pr = min(c.W, c.H) * .016
        c.draw.ellipse([p[0] - pr, p[1] - pr, p[0] + pr, p[1] + pr], fill=node)
        c.draw.ellipse([p[0] - pr * 2, p[1] - pr * 2,
                        p[0] + pr * 2, p[1] + pr * 2],
                       outline=node + (110,), width=max(1, int(c.W * .0014)))
    c.vignette(100)


def s_waves(c: Canvas, color=None):
    base = color or c.stops[2]
    layers = 7
    for L in range(layers):
        t = L / (layers - 1)
        col = _mix(c.stops[0], base, 0.25 + t * 0.75)
        amp = c.H * (0.05 + t * 0.05)
        y0 = c.H * (0.34 + t * 0.085)
        pts = [(0, c.H)]
        for x in range(0, int(c.W) + 1, max(2, c.W // 220)):
            y = y0 + math.sin(x / c.W * math.pi * 3 + L * 0.9) * amp
            pts.append((x, y))
        pts.append((c.W, c.H))
        c.draw.polygon(pts, fill=col)
    c.vignette(85)


def s_cube(c: Canvas, color=None):
    cx, cy = c.W * .5, c.H * .5
    s = min(c.W, c.H) * .26
    top = (230, 240, 255)
    left = color or c.stops[2]
    right = tuple(max(0, v - 60) for v in left)
    c.draw.polygon([(cx, cy - s), (cx + s, cy - s * .5),
                    (cx, cy), (cx - s, cy - s * .5)], fill=top)
    c.draw.polygon([(cx - s, cy - s * .5), (cx, cy),
                    (cx, cy + s), (cx - s, cy + s * .5)], fill=left)
    c.draw.polygon([(cx + s, cy - s * .5), (cx, cy),
                    (cx, cy + s), (cx + s, cy + s * .5)], fill=right)
    c.vignette(70)


def s_orb(c: Canvas, color=None):
    cx, cy = c.W * .5, c.H * .5
    r = min(c.W, c.H) * .30
    col = color or c.stops[2]
    g = c.glow_layer()
    for i in range(20, 0, -1):
        rr = r * (1 + i * .07)
        ImageDraw.Draw(g).ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                                  fill=col + (int(8 * (1 - i / 20)),))
    c.apply_glow(g, 0.03, 255)
    for i in range(70, 0, -1):
        t = i / 70
        rr = r * t
        shade = _mix(tuple(max(0, v - 70) for v in col), col, t ** .6)
        off = (1 - t) * r * .30
        c.draw.ellipse([cx - rr - off * .4, cy - rr - off, cx + rr - off * .4, cy + rr - off],
                       fill=shade)
    c.draw.ellipse([cx - r * .30, cy - r * .55, cx - r * .02, cy - r * .22],
                   fill=(255, 255, 255, 120))
    c.vignette(90)


def s_sphere(c: Canvas, color=None):
    cx, cy = c.W * .5, c.H * .5
    r = min(c.W, c.H) * .32
    col = color or c.stops[2]
    for i in range(r * 2):
        t = i / (r * 2)
        xx = cx - r + i
        k = math.sqrt(max(0.0, 1 - ((xx - cx) / r) ** 2))
        c.draw.line([(xx, cy - r * k), (xx, cy + r * k)], fill=_mix(col, (255, 255, 255), t * .25))
    c.draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 60),
                   width=max(1, int(c.W * .002)))
    c.draw.ellipse([cx - r * .34, cy - r * .52, cx - r * .06, cy - r * .24],
                   fill=(255, 255, 255, 150))
    c.vignette(70)


def s_poster(c: Canvas, color=None, text: str = "AK"):
    col = color or c.stops[2]
    c.draw.rectangle([c.W * .10, c.H * .10, c.W * .90, c.H * .90],
                     fill=(255, 255, 255, 16), outline=col + (220,),
                     width=max(2, int(c.W * .0035)))
    for corner in ((.10, .10), (.90, .10), (.10, .90), (.90, .90)):
        x, y = c.W * corner[0], c.H * corner[1]
        d = c.W * .05
        sgn_x = 1 if corner[0] < .5 else -1
        sgn_y = 1 if corner[1] < .5 else -1
        c.draw.line([x, y, x + d * sgn_x, y], fill=col, width=int(c.W * .006))
        c.draw.line([x, y, x, y + d * sgn_y], fill=col, width=int(c.W * .006))
    txt = (text or "AK").upper()[:18]
    f = _font(int(c.H * .17), bold=True)
    bb = c.draw.textbbox((0, 0), txt, font=f)
    c.draw.text((c.W / 2 - (bb[2] - bb[0]) / 2, c.H / 2 - (bb[3] - bb[1]) / 2),
                txt, font=f, fill=col)
    c.vignette(70)


def s_rainbow(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((150, 200, 245), (250, 250, 235), t))
    cols = [(225, 60, 60), (250, 150, 40), (250, 220, 60), (90, 200, 90),
            (60, 140, 230), (120, 80, 200), (200, 70, 180)]
    cx, cy = c.W * .5, c.H * .95
    r = min(c.W, c.H) * .78
    for i, col in enumerate(cols):
        rr = r * (1 - i * 0.075)
        wdt = r * 0.075
        c.draw.arc([cx - rr, cy - rr, cx + rr, cy + rr], 180, 360,
                   fill=col, width=int(wdt))
    c.draw.rectangle([0, c.H * .80, c.W, c.H], fill=(96, 176, 92))
    for i in range(26):
        x = c.W * i / 26
        c.draw.ellipse([x, c.H * .80, x + c.W / 26, c.H * .86],
                       fill=(70 + (i * 7) % 40, 150, 70))
    c.vignette(50)


def s_bird(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((250, 196, 120), (255, 250, 235), t))
    _sun_small(c, c.W * .22, c.H * .30, c.H * .13, (255, 210, 110))
    col = color or (40, 40, 60)
    for (bx, by, s) in ((.52, .34, 1.0), (.70, .24, .68), (.80, .42, .52),
                        (.38, .22, .44)):
        x, y, k = c.W * bx, c.H * by, c.H * .07 * s
        for d in (-1, 1):
            c.draw.arc([x - k, y - k * .8, x + k * .3, y + k * .5],
                       200, 350, fill=col, width=max(2, int(c.H * .012 * s)))
            c.draw.arc([x - k * .3, y - k * .8, x + k, y + k * .5],
                       190, 340, fill=col, width=max(2, int(c.H * .012 * s)))
    c.vignette(50)


def s_fish(c: Canvas, color=None):
    for i in range(int(c.H * .62)):
        t = i / max(1, c.H * .62)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((10, 60, 110), (30, 130, 180), t))
    for i in range(int(c.H * .62), c.H):
        t = (i - c.H * .62) / max(1, c.H * .38)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((26, 120, 170), (10, 50, 96), t))
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for i in range(30):
        y = c.rnd.uniform(c.H * .64, c.H)
        x = c.rnd.uniform(0, c.W - c.W * .01)
        gd.ellipse([x, y, x + c.W * .01, y + c.W * .01],
                   fill=(220, 245, 255, 130))
    c.apply_glow(g, 0.004, 200)
    cx, cy = c.W * .46, c.H * .38
    body = color or (255, 140, 60)
    L, Hh = c.W * .26, c.H * .22
    c.draw.polygon([(cx - L, cy), (cx, cy - Hh * .62), (cx + L, cy),
                    (cx, cy + Hh * .62)], fill=body)
    c.draw.polygon([(cx - L, cy), (cx - L * 1.5, cy - Hh * .55),
                    (cx - L * 1.5, cy + Hh * .55)], fill=tuple(max(0, v - 40) for v in body))
    c.draw.polygon([(cx + L * .05, cy - Hh * .30), (cx + L * .55, cy - Hh * 1.15),
                    (cx + L * .70, cy - Hh * .18)], fill=tuple(max(0, v - 30) for v in body))
    c.draw.ellipse([cx + L * .40, cy - Hh * .16,
                    cx + L * .58, cy + Hh * .04], fill=(250, 250, 255))
    c.draw.ellipse([cx + L * .47, cy - Hh * .10,
                    cx + L * .55, cy - Hh * .01], fill=(20, 20, 30))
    c.vignette(80)


def s_leaf(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((240, 248, 235), (170, 210, 165), t))
    col = color or (58, 150, 70)
    cx, cy = c.W * .5, c.H * .5
    L, Wd = c.H * .38, c.W * .17
    pts = []
    for i in range(101):
        t = i / 100
        x = cx - L / 2 + L * t
        y = cy - math.sin(t * math.pi) * Wd
        pts.append((x, y))
    for i in range(100, -1, -1):
        t = i / 100
        x = cx - L / 2 + L * t
        y = cy + math.sin(t * math.pi) * Wd
        pts.append((x, y))
    g = c.glow_layer()
    ImageDraw.Draw(g).polygon(pts, fill=col + (170,))
    c.apply_glow(g, 0.012, 190)
    c.draw.polygon(pts, fill=col)
    c.draw.line([cx - L / 2, cy, cx + L / 2, cy], fill=(30, 90, 40),
                width=int(c.W * .005))
    for i in range(1, 10):
        t = i / 10
        x = cx - L / 2 + L * t
        half = math.sin(t * math.pi) * Wd
        for d in (-1, 1):
            c.draw.line([(x, cy), (x + L * .09, cy + d * half * .92)],
                        fill=(30, 90, 40, 170), width=int(c.W * .0032))
    c.vignette(60)


def s_cloud(c: Canvas, color=None):
    for i in range(c.H):
        t = i / max(1, c.H - 1)
        c.draw.rectangle([0, i, c.W, i + 1],
                         fill=_mix((70, 140, 220), (215, 238, 252), t))
    col = color or (255, 255, 255)
    g = c.glow_layer()
    gd = ImageDraw.Draw(g)
    for cxr, cyr, r in ((.36, .48, .20), (.52, .40, .25), (.66, .50, .18),
                        (.46, .54, .17)):
        gd.ellipse([c.W * (cxr - r), c.H * (cyr - r * 1.3),
                    c.W * (cxr + r), c.H * (cyr + r * .7)], fill=col + (215,))
    c.apply_glow(g, 0.014, 190)
    for cxr, cyr, r in ((.36, .48, .20), (.52, .40, .25), (.66, .50, .18),
                        (.46, .54, .17)):
        c.draw.ellipse([c.W * (cxr - r), c.H * (cyr - r * 1.3),
                        c.W * (cxr + r), c.H * (cyr + r * .7)], fill=col)
    _sun_small(c, c.W * .84, c.H * .18, c.H * .10)
    c.vignette(55)


def s_tiles(c: Canvas, color=None):
    n = 10
    s = c.W / n
    h, l, sat = colorsys.rgb_to_hls(*[v / 255 for v in (color or c.stops[2])])
    for r_ in range(int(c.H / s) + 1):
        for col_ in range(n + 1):
            hue = (h + (r_ + col_) * 0.055) % 1.0
            fill = tuple(int(v * 255) for v in colorsys.hls_to_rgb(hue, l, sat))
            x0, y0 = col_ * s, r_ * s
            inset = s * 0.06
            if (r_ + col_) % 2 == 0:
                pts = _regular_polygon(x0 + s / 2, y0 + s / 2, (s - inset * 2) / 2, 6,
                                       math.pi / 6)
                c.draw.polygon(pts, fill=fill)
            else:
                c.draw.rectangle([x0 + inset, y0 + inset,
                                  x0 + s - inset, y0 + s - inset], fill=fill)
    c.vignette(90)


def s_spiral(c: Canvas, color=None):
    cx = cy = min(c.W, c.H) * .5
    col = color or c.stops[2]
    h, l, s = colorsys.rgb_to_hls(*[v / 255 for v in col])
    rmax = min(c.W, c.H) * .44
    prev = None
    for i in range(1400):
        t = i / 1400
        a = t * math.pi * 9
        r = rmax * t
        hue = (h + t * .8) % 1.0
        cc = tuple(int(v * 255) for v in colorsys.hls_to_rgb(hue, l, s))
        p = (cx + math.cos(a) * r, cy + math.sin(a) * r)
        if prev:
            c.draw.line([prev, p], fill=cc, width=max(2, int(c.W * .0035)))
        prev = p
    c.vignette(95)


# --- silhouette subjects -------------------------------------------------
# Every icon is a small list of parts in a 0..1 box: "p" polygon, "r" rect,
# "e" ellipse. "eye" places one highlight dot, "hole" a dark cut-out.
ICONS: dict = {
    "dog": {"eye": (0.845, 0.355), "parts": [
        ("p", [(0.08, 0.56), (0.12, 0.48), (0.24, 0.43), (0.46, 0.41), (0.62, 0.42),
               (0.70, 0.40), (0.72, 0.52), (0.70, 0.62), (0.72, 0.64), (0.70, 0.85),
               (0.62, 0.85), (0.62, 0.62), (0.46, 0.61), (0.44, 0.85), (0.36, 0.85),
               (0.36, 0.61), (0.26, 0.61), (0.24, 0.85), (0.16, 0.85), (0.16, 0.62),
               (0.10, 0.62)]),
        ("p", [(0.70, 0.36), (0.76, 0.25), (0.86, 0.26), (0.90, 0.34), (0.97, 0.42),
               (0.92, 0.49), (0.85, 0.51), (0.78, 0.50), (0.70, 0.46)]),
        ("p", [(0.71, 0.30), (0.69, 0.17), (0.79, 0.27)]),
        ("p", [(0.12, 0.48), (0.02, 0.37), (0.00, 0.46), (0.07, 0.56)]),
    ]},
    "cat": {"eye": (0.83, 0.40), "parts": [
        ("p", [(0.30, 0.88), (0.27, 0.62), (0.33, 0.47), (0.46, 0.44), (0.60, 0.48),
               (0.72, 0.46), (0.80, 0.40), (0.84, 0.28), (0.90, 0.35), (0.88, 0.48),
               (0.78, 0.55), (0.66, 0.61), (0.70, 0.88)]),
        ("p", [(0.61, 0.31), (0.67, 0.17), (0.75, 0.30)]),
        ("p", [(0.79, 0.29), (0.86, 0.15), (0.91, 0.31)]),
        ("p", [(0.30, 0.86), (0.15, 0.78), (0.11, 0.88), (0.24, 0.92)]),
    ]},
    "lion": {"eye": (0.40, 0.37), "parts": [
        ("e", 0.45, 0.37, 0.30, 0.29),
        ("e", 0.45, 0.39, 0.18, 0.18),
        ("p", [(0.30, 0.60), (0.60, 0.60), (0.68, 0.88), (0.24, 0.88)]),
        ("p", [(0.30, 0.64), (0.16, 0.62), (0.16, 0.72), (0.30, 0.74)]),
        ("p", [(0.60, 0.64), (0.74, 0.62), (0.74, 0.72), (0.60, 0.74)]),
    ]},
    "horse": {"eye": (0.845, 0.20), "parts": [
        ("p", [(0.16, 0.48), (0.40, 0.40), (0.62, 0.42), (0.70, 0.30), (0.76, 0.11),
               (0.88, 0.13), (0.86, 0.36), (0.89, 0.46), (0.79, 0.50), (0.77, 0.72),
               (0.74, 0.88), (0.67, 0.88), (0.67, 0.62), (0.40, 0.60), (0.38, 0.88),
               (0.31, 0.88), (0.31, 0.58), (0.18, 0.56)]),
        ("p", [(0.16, 0.48), (0.05, 0.55), (0.03, 0.69), (0.10, 0.67), (0.12, 0.54)]),
    ]},
    "house": {"parts": [
        ("p", [(0.14, 0.52), (0.50, 0.22), (0.86, 0.52), (0.86, 0.88), (0.14, 0.88)]),
        ("hole", (0.50, 0.76, 0.07, 0.12)),
        ("hole", (0.30, 0.64, 0.065, 0.06)),
        ("hole", (0.70, 0.64, 0.065, 0.06)),
    ]},
    "car": {"parts": [
        ("p", [(0.08, 0.66), (0.18, 0.52), (0.33, 0.49), (0.45, 0.33), (0.66, 0.33),
               (0.79, 0.49), (0.90, 0.51), (0.95, 0.62), (0.95, 0.72), (0.08, 0.72)]),
        ("e", 0.27, 0.75, 0.10, 0.10),
        ("e", 0.73, 0.75, 0.10, 0.10),
        ("hole", (0.50, 0.42, 0.09, 0.06)),
        ("hole", (0.68, 0.42, 0.06, 0.05)),
    ]},
    "plane": {"parts": [
        ("p", [(0.04, 0.55), (0.34, 0.47), (0.88, 0.22), (0.96, 0.29), (0.53, 0.53),
               (0.94, 0.73), (0.86, 0.80), (0.42, 0.61), (0.06, 0.63)]),
        ("hole", (0.30, 0.545, 0.05, 0.03)),
    ]},
    "rocket": {"parts": [
        ("p", [(0.44, 0.06), (0.56, 0.06), (0.61, 0.42), (0.61, 0.70), (0.39, 0.70),
               (0.39, 0.42)]),
        ("p", [(0.39, 0.48), (0.24, 0.74), (0.39, 0.70)]),
        ("p", [(0.61, 0.48), (0.76, 0.74), (0.61, 0.70)]),
        ("hole", (0.50, 0.33, 0.055, 0.055)),
        ("p", [(0.38, 0.70), (0.62, 0.70), (0.50, 0.92)]),
    ]},
    "cup": {"parts": [
        ("p", [(0.30, 0.28), (0.70, 0.28), (0.64, 0.86), (0.36, 0.86)]),
        ("p", [(0.70, 0.36), (0.82, 0.38), (0.82, 0.64), (0.70, 0.66), (0.72, 0.58),
               (0.75, 0.57), (0.75, 0.47), (0.72, 0.46)]),
        ("e", 0.50, 0.24, 0.22, 0.05),
    ]},
    "cake": {"parts": [
        ("r", 0.16, 0.62, 0.84, 0.88),
        ("r", 0.27, 0.44, 0.73, 0.62),
        ("r", 0.43, 0.26, 0.49, 0.44),
        ("r", 0.55, 0.22, 0.61, 0.44),
        ("e", 0.46, 0.20, 0.035, 0.055),
        ("e", 0.58, 0.16, 0.035, 0.055),
    ]},
    "icecream": {"parts": [
        ("p", [(0.40, 0.56), (0.60, 0.56), (0.50, 0.94)]),
        ("e", 0.44, 0.44, 0.13, 0.13),
        ("e", 0.57, 0.45, 0.12, 0.12),
        ("e", 0.50, 0.33, 0.10, 0.10),
    ]},
    "pizza": {"parts": [
        ("p", [(0.12, 0.88), (0.88, 0.88), (0.50, 0.12)]),
        ("hole", (0.50, 0.62, 0.065, 0.065)),
        ("hole", (0.38, 0.76, 0.05, 0.05)),
        ("hole", (0.62, 0.76, 0.05, 0.05)),
    ]},
    "burger": {"parts": [
        ("e", 0.50, 0.34, 0.30, 0.16),
        ("r", 0.19, 0.50, 0.81, 0.60),
        ("e", 0.50, 0.72, 0.30, 0.13),
        ("r", 0.24, 0.60, 0.76, 0.64),
    ]},
    "apple": {"parts": [
        ("e", 0.50, 0.58, 0.25, 0.27),
        ("p", [(0.51, 0.30), (0.69, 0.21), (0.62, 0.35)]),
        ("r", 0.47, 0.24, 0.52, 0.34),
    ]},
    "book": {"parts": [
        ("p", [(0.18, 0.20), (0.50, 0.27), (0.50, 0.86), (0.18, 0.79)]),
        ("p", [(0.82, 0.20), (0.50, 0.27), (0.50, 0.86), (0.82, 0.79)]),
        ("r", 0.47, 0.25, 0.53, 0.88),
        ("hole", (0.33, 0.50, 0.11, 0.012)),
        ("hole", (0.33, 0.58, 0.11, 0.012)),
        ("hole", (0.67, 0.50, 0.11, 0.012)),
        ("hole", (0.67, 0.58, 0.11, 0.012)),
    ]},
    "phone": {"parts": [
        ("r", 0.35, 0.06, 0.65, 0.94),
        ("hole", (0.50, 0.46, 0.10, 0.33)),
        ("e", 0.50, 0.87, 0.045, 0.045),
    ]},
    "laptop": {"parts": [
        ("r", 0.22, 0.20, 0.78, 0.62),
        ("hole", (0.50, 0.41, 0.24, 0.15)),
        ("p", [(0.10, 0.72), (0.90, 0.72), (0.96, 0.84), (0.04, 0.84)]),
    ]},
    "crown": {"parts": [
        ("p", [(0.14, 0.76), (0.18, 0.22), (0.35, 0.50), (0.50, 0.18), (0.65, 0.50),
               (0.82, 0.22), (0.86, 0.76)]),
        ("r", 0.14, 0.76, 0.86, 0.87),
        ("hole", (0.50, 0.60, 0.045, 0.045)),
        ("hole", (0.18, 0.22, 0.035, 0.035)),
        ("hole", (0.82, 0.22, 0.035, 0.035)),
    ]},
    "skull": {"parts": [
        ("e", 0.50, 0.42, 0.27, 0.28),
        ("r", 0.38, 0.62, 0.62, 0.86),
        ("hole", (0.41, 0.40, 0.075, 0.085)),
        ("hole", (0.59, 0.40, 0.075, 0.085)),
        ("hole", (0.50, 0.58, 0.045, 0.06)),
    ]},
    "ghost": {"eye": (0.41, 0.46), "parts": [
        ("p", [(0.26, 0.88), (0.26, 0.44), (0.33, 0.24), (0.50, 0.14), (0.67, 0.24),
               (0.74, 0.44), (0.74, 0.88), (0.66, 0.77), (0.58, 0.88), (0.50, 0.77),
               (0.42, 0.88), (0.34, 0.77)]),
        ("hole", (0.59, 0.46, 0.05, 0.06)),
    ]},
    "umbrella": {"parts": [
        ("p", [(0.12, 0.54), (0.25, 0.26), (0.50, 0.14), (0.75, 0.26), (0.88, 0.54),
               (0.75, 0.45), (0.63, 0.56), (0.50, 0.45), (0.37, 0.56), (0.25, 0.45)]),
        ("r", 0.47, 0.54, 0.53, 0.82),
        ("p", [(0.53, 0.82), (0.44, 0.82), (0.44, 0.74), (0.49, 0.74)]),
    ]},
    "candle": {"parts": [
        ("r", 0.41, 0.36, 0.59, 0.88),
        ("e", 0.50, 0.24, 0.065, 0.11),
        ("r", 0.47, 0.32, 0.53, 0.40),
    ]},
    "guitar": {"parts": [
        ("e", 0.50, 0.72, 0.23, 0.20),
        ("e", 0.50, 0.53, 0.16, 0.15),
        ("r", 0.47, 0.08, 0.53, 0.48),
        ("e", 0.50, 0.07, 0.075, 0.05),
        ("hole", (0.50, 0.66, 0.065, 0.065)),
    ]},
    "camera": {"parts": [
        ("r", 0.09, 0.34, 0.91, 0.80),
        ("r", 0.32, 0.24, 0.68, 0.36),
        ("hole", (0.50, 0.57, 0.17, 0.17)),
        ("hole", (0.80, 0.42, 0.035, 0.035)),
    ]},
    "key": {"parts": [
        ("e", 0.24, 0.38, 0.15, 0.15),
        ("hole", (0.24, 0.38, 0.07, 0.07)),
        ("r", 0.34, 0.34, 0.90, 0.43),
        ("r", 0.70, 0.43, 0.77, 0.58),
        ("r", 0.81, 0.43, 0.88, 0.55),
    ]},
    "lock": {"parts": [
        ("p", [(0.32, 0.44), (0.32, 0.28), (0.39, 0.18), (0.61, 0.18), (0.68, 0.28),
               (0.68, 0.44), (0.59, 0.44), (0.59, 0.30), (0.55, 0.25), (0.45, 0.25),
               (0.41, 0.30), (0.41, 0.44)]),
        ("r", 0.24, 0.44, 0.76, 0.88),
        ("hole", (0.50, 0.62, 0.055, 0.075)),
    ]},
    "watch": {"parts": [
        ("r", 0.42, 0.08, 0.58, 0.30),
        ("r", 0.42, 0.74, 0.58, 0.94),
        ("e", 0.50, 0.52, 0.25, 0.25),
        ("hole", (0.50, 0.52, 0.20, 0.20)),
    ], "over": [
        ("p", [(0.48, 0.50), (0.52, 0.50), (0.52, 0.38)]),
        ("p", [(0.48, 0.50), (0.60, 0.56), (0.48, 0.55)]),
    ]},
}


def _ic_pts(pts, box):
    x0, y0, size = box
    return [(x0 + px * size, y0 + py * size) for px, py in pts]


def s_icon(c: Canvas, color=None, key: str = "dog"):
    """Draw one of the ICONS silhouettes, glow-backed like the other subjects."""
    spec = ICONS.get(key) or ICONS["dog"]
    col = color or c.stops[2]
    size = min(c.W, c.H) * 0.74
    box = (c.W / 2 - size / 2, c.H / 2 - size / 2, size)

    def paint(target, fill, parts):
        for part in parts:
            kind = part[0]
            if kind == "p":
                target.polygon(_ic_pts(part[1], box), fill=fill)
            elif kind == "r":
                x0, y0 = _ic_pts([(part[1], part[2])], box)[0]
                x1, y1 = _ic_pts([(part[3], part[4])], box)[0]
                target.rectangle([x0, y0, x1, y1], fill=fill)
            elif kind == "e":
                cx, cy = _ic_pts([(part[1], part[2])], box)[0]
                rx, ry = part[3] * size, part[4] * size
                target.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill)

    parts = spec["parts"]
    glow = c.glow_layer()
    paint(ImageDraw.Draw(glow), col + (185,), parts)
    c.apply_glow(glow, 0.022, 225)
    paint(c.draw, col, parts)

    dark = (22, 18, 32)
    ex, ey = spec.get("eye", (None, None))
    if ex is not None:
        x, y = _ic_pts([(ex, ey)], box)[0]
        r = size * 0.03
        c.draw.ellipse([x - r, y - r, x + r, y + r], fill=dark)
    for part in parts:
        if part[0] == "hole":
            hx, hy, rx, ry = (part[1] if len(part) == 2 else part[1:5])
            cx, cy = _ic_pts([(hx, hy)], box)[0]
            rx, ry = rx * size, ry * size
            c.draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=dark)
    if spec.get("over"):
        paint(c.draw, col, spec["over"])
    c.vignette(85)


def s_logo(c: Canvas, color=None, text: str = ""):
    """A round emblem with the brand initials inside."""
    col = color or c.stops[2]
    cx, cy = c.W * .5, c.H * .48
    r = min(c.W, c.H) * .32
    ring = c.glow_layer()
    ImageDraw.Draw(ring).ellipse([cx - r, cy - r, cx + r, cy + r],
                                  outline=col + (215,),
                                  width=max(2, int(c.W * .012)))
    c.apply_glow(ring, 0.02, 230)
    c.draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col,
                   width=max(2, int(c.W * .012)))
    inner = r * 0.80
    c.draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner],
                   outline=col + (135,), width=max(1, int(c.W * .0035)))
    letters = re.sub(r"[^A-Za-z0-9]", "", (text or "AK"))[:3].upper() or "AK"
    f = _font(int(r * 0.92), bold=True)
    bb = c.draw.textbbox((0, 0), letters, font=f)
    c.draw.text((cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - r * .08),
                letters, font=f, fill=col)
    bar_y = cy + r * 0.56
    c.draw.rectangle([cx - r * .52, bar_y, cx + r * .52, bar_y + c.W * .006],
                     fill=col + (190,))
    c.vignette(75)


SUBJECTS: dict = {
    "sun": s_sun, "sooraj": s_sun, "suraj": s_sun, "sunset": s_sunset,
    "sooraj doobne": s_sunset, "suryast": s_sunset, "sunrise": s_sun,
    "night": s_night, "raat": s_night,
    "moon": s_night, "taare": s_night, "stars": s_night,
    "mountain": s_mountains, "pahaad": s_mountains, "pahad": s_mountains,
    "himalaya": s_mountains,
    "ocean": s_ocean, "samundar": s_ocean, "beach": s_ocean,
    "forest": s_forest, "jangal": s_forest, "jungle": s_forest,
    "tree": s_tree, "ped": s_tree, "palm": s_tree,
    "flower": s_flower, "phool": s_flower, "gulab": s_flower,
    "butterfly": s_butterfly, "titli": s_butterfly,
    "bird": s_bird, "pakshi": s_bird, "chidiya": s_bird, "eagle": s_bird,
    "fish": s_fish, "machli": s_fish, "matsya": s_fish,
    "leaf": s_leaf, "patti": s_leaf, "pada": s_leaf,
    "cloud": s_cloud, "badal": s_cloud,
    "heart": s_heart, "dil": s_heart, "pyaar": s_heart, "love": s_heart,
    "star": s_star, "sitara": s_star, "taara": s_star,
    "mandala": s_mandala, "rangeen": s_mandala,
    "circuit": s_circuit, "circuit board": s_circuit,
    "network": s_network, "network graph": s_network, "graph": s_network,
    "waves": s_waves, "aliran": s_waves,
    "cube": s_cube, "kubba": s_cube, "box": s_cube,
    "orb": s_orb, "sphere": s_sphere, "gol": s_sphere, "ball": s_sphere,
    "poster": s_poster, "banner": s_poster,
    "rainbow": s_rainbow, "indradhanush": s_rainbow,
    "tiles": s_tiles, "pattern": s_tiles, "bricks": s_tiles,
    "spiral": s_spiral, "ghrenti": s_spiral,
}


def _icon(key: str) -> Callable:
    return lambda c, color=None: s_icon(c, color, key)


ICON_ALIASES = {
    "dog": ("kutta", "kuta", "kuttaa", "dog", "puppy", "sher kutta"),
    "cat": ("billi", "billiyan", "cat", "kitten", "pussy"),
    "lion": ("sher", "shera", "lion", "singa"),
    "horse": ("ghoda", "horse", "ghoda painting"),
    "house": ("ghar", "makaan", "house", "home", "hathhi"),
    "car": ("gaadi", "gadi", "car", "taxi", "auto rickshaw", "truck", "gaari"),
    "plane": ("jahaz", "plane", "aircraft", "heli", "helicopter"),
    "rocket": ("rocket", "raket", "space ship"),
    "cup": ("cup", "mug", "coffee", "chai", "tea", "glass of tea"),
    "cake": ("cake", "keek", "birthday cake"),
    "icecream": ("ice cream", "icecream", "ice-cream", "kulfi", "gelato"),
    "pizza": ("pizza",),
    "burger": ("burger", "hamburger"),
    "apple": ("apple", "seb", "sant"),
    "book": ("book", "kitaab", "pustak", "novel", "copy"),
    "phone": ("phone", "mobile", "smartphone", "fon", "iphone"),
    "laptop": ("laptop", "computer", "pc", "screen"),
    "crown": ("crown", "taaj", "taj", "sultan"),
    "skull": ("skull", "haddi", "kandha"),
    "ghost": ("ghost", "bhoot", "bhootna", "preti"),
    "umbrella": ("umbrella", "chhatri", "chata"),
    "candle": ("candle", "diya", "mombatti", "deepak"),
    "guitar": ("guitar", "gitar", "sitar", "veena"),
    "camera": ("camera", "dslr", "photo camera"),
    "key": ("key", "chaabi", "chabi"),
    "lock": ("lock", "tala", "pankha"),
    "watch": ("watch", "ghadi", "wrist watch"),
}
for _key, _names in ICON_ALIASES.items():
    for _name in _names:
        SUBJECTS.setdefault(_name, _icon(_key))
SUBJECTS["logo"] = s_logo
SUBJECTS["brand"] = s_logo
SUBJECTS["monogram"] = s_logo

SUBJECT_WORDS = sorted(SUBJECTS, key=len, reverse=True)

PAINT_WORDS = ("draw", "draws", "drew", "paint", "paints", "painting", "painted",
               "sketch", "picture", "pic", "image", "art", "artwork", "poster",
               "logo", "banner", "canvas", "murals", "mural", "banao", "bana",
               "banana", "banaiye", "dikha", "dikhao", "dikhaya", "bana do",
               "design", "design karo", "illustrate", "kalam", "chitra", "tasveer")

CODE_WORDS = {
    "game": ("game", "games", "khel", "khelna", "snake", "maze", "sudoku",
             "tic-tac-toe", "tictactoe", "quiz", "racing", "shooter", "platformer"),
    "website": ("website", "web site", "site", "webpage", "web page", "blog",
                "portfolio", "landing page", "website banao"),
    "apk": ("apk", "android app", "mobile app", "app for android"),
    "app": ("app", "application", "software", "tool", "utility", "script",
             "program", "bot", "dashboard", "gui", "exe", "program banao",
             "app banao", "software banao", "python program"),
}


FILLER = ("please", "kro", "karo", "kar do", "kardo", "bana do", "bana",
          "banana", "banaiye", "banaye", "banao", "dikhawao", "dikhao",
          "dikhaya", "dikhaye", "banake", "banana", "ka", "ki", "ke", "a",
          "ek", "mujhe", "mere", "ko", "se", "create", "make", "build",
          "generate", "ak", "sir", "yaar", "abhi", "for", "me", "my", "aan",
          "wali", "wala", "kaisa", "kaisi", "accha", "acha", "jaldi", "ekdam")

MONO_WORDS = ("black and white", "black & white", "bina rang", "bina colour",
              "bina color", "monochrome", "monochrome me", "greyscale",
              "grayscale", "grey scale", "gray scale", "sketch", "line art",
              "outline", "bw", "b/w", "pencil", "pencil sketch", "doodh se",
              "safed ka", "kaala pen")


def _clean(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.replace("?", " ").replace("!", " ")
    for w in FILLER:
        t = re.sub(rf"\b{re.escape(w)}\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _find_color(text: str) -> Optional[tuple]:
    for word in sorted(COLOR_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", text):
            return COLOR_WORDS[word]
    m = re.search(r"#([0-9a-f]{6}|[0-9a-f]{3})\b", text)
    if m:
        return _hex_to_rgb("#" + m.group(1))
    return None


def detect_paint(text: str) -> Optional[dict]:
    """Return a paint spec if this utterance is a drawable request.

    Colour rule (explicit user requirement):
      * a colour mentioned  -> the artwork is built around that colour,
      * no colour mentioned -> the artwork is colourless (monochrome ink).

    Returns a spec dict, or None when this is not a drawing request."""
    raw = (text or "").lower()
    t = _clean(raw)
    if not t:
        return None

    has_paint_word = any(w in raw for w in PAINT_WORDS)
    subject = None
    for word in SUBJECT_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", t):
            subject = word
            break

    if not subject:
        if not has_paint_word:
            return None
        subject = "mandala"

    color = _find_color(t)
    forced_mono = any(re.search(rf"\b{re.escape(w)}\b", t) for w in MONO_WORDS)
    mono = forced_mono or color is None

    big = bool(re.search(r"\b(bada|badhiya|big|large|huge|badi|full)\b", raw))
    small = bool(re.search(r"\b(chhota|chota|small|mini)\b", raw))
    if big:
        w, h = 1600, 1000
    elif small:
        w, h = 720, 720
    else:
        w, h = 1280, 800

    m = re.search(r"\b(?:text|likh|likho|words?|title|naam|name)\s+"
                  r"['\"]?([a-z0-9 \-]{1,24})['\"]?", t)
    label = m.group(1).strip() if m else ""

    # "ak logo banao" means an AK logo, not a logo reading "log". When a logo
    # or poster is asked for with no explicit text, a short leading token is
    # the brand/initials the user actually wants shown.
    #
    # This reads `raw`, not `t`: _clean() strips FILLER, and "ak" is the wake
    # word, so by this point the brand name is already gone. Searching the
    # original utterance is what keeps "ak logo banao" coming out as AK.
    if not label and subject in ("logo", "brand", "monogram", "poster",
                                 "banner"):
        # "ak" is deliberately NOT skipped: it is both AK's wake word and the
        # brand name in "ak logo banao". The wake-word strip in _clean() only
        # touches the cleaned text, so reading `raw` keeps it available here.
        skip = {
            "logo", "brand", "monogram", "poster", "banner", "banao", "banao",
            "bana", "banai", "banawo", "banae", "banau", "bnao", "bna",
            "draw", "drawing", "draws", "paint", "painting", "painted",
            "paints", "kar", "karo", "kardo", "karna", "ka", "ki",
            "ke", "me", "mein", "se", "aur", "for", "of", "the", "ek", "a",
            "an", "sir", "please", "plz", "mujhe", "mere",
            "ko", "par", "pe", "vala", "wali", "wala", "hey", "hello",
            "ya", "yaar", "bhai", "boss", "sun", "suno", "de", "dena", "do",
            "and", "or", "with", "in", "on", "at", "to", "is", "my", "this",
            "that", "it", "be", "ka", "ki", "ke", "hai", "ho", "hona",
        }
        # Never let a colour or a subject name become the logo text.
        skip |= set(COLOR_WORDS) | set(SUBJECTS) | set(MONO_WORDS)
        for tok in re.findall(r"[a-z0-9]+", raw):
            if tok in skip:
                continue
            if 2 <= len(tok) <= 8:
                label = tok.upper()
                break

    pal = None
    for key in PALETTES:
        if re.search(rf"\b{re.escape(key)}\b", t):
            pal = key
            break
    if pal is None:
        pal = {"sun": "sunny", "sunset": "sunset", "night": "night",
               "ocean": "ocean", "forest": "forest", "rose": "rose",
               "fire": "fire", "ice": "ice", "earth": "earth",
               "candy": "candy"}.get(subject, "neon")

    nice = {"sooraj": "sun", "suraj": "sun", "chand": "moon", "taare": "stars",
            "pahaad": "mountains", "pahad": "mountains", "samundar": "ocean",
            "jangal": "forest", "jungle": "forest", "ped": "tree",
            "phool": "flower", "titli": "butterfly", "pakshi": "bird",
            "chidiya": "bird", "machli": "fish", "patti": "leaf",
            "badal": "cloud", "dil": "heart", "sitara": "star",
            "taara": "star", "indradhanush": "rainbow", "kubba": "cube",
            "gol": "sphere", "ghrenti": "spiral", "aliran": "waves",
            "rangeen": "mandala", "chitra": "mandala",
            "kutta": "dog", "kuta": "dog", "billi": "cat", "sher": "lion",
            "ghoda": "horse", "ghar": "house", "makaan": "house",
            "gaadi": "car", "gadi": "car", "jahaz": "plane", "taaj": "crown",
            "taj": "crown", "kitaab": "book", "pustak": "book",
            "mobile": "phone", "fon": "phone", "laptop": "laptop",
            "bhoot": "ghost", "chhatri": "umbrella", "diya": "candle",
            "mombatti": "candle", "sitar": "guitar", "chaabi": "key",
            "chabi": "key", "tala": "lock", "ghadi": "watch",
            "seb": "apple", "chai": "cup", "coffee": "cup",
            "kulfi": "icecream", "cake": "cake", "haddi": "skull"}.get(
                subject, subject)

    return {"subject": subject, "nice": nice, "palette": pal, "color": color,
            "mono": mono, "forced_mono": forced_mono,
            "width": w, "height": h, "text": label,
            "seed": abs(hash(t)) % 99991}


# ── ink-on-paper palette ────────────────────────────────────────────────
# Mono mode has to start from a LIGHT canvas. It used to draw dark ink onto
# the dark neon gradient and only afterwards try to "develop" the result into
# ink-on-paper, which converted the background into ink as well: every mono
# request came out ~95% dark and unreadable. Paper first, ink second.
_MONO_PAPER = (250, 247, 241)
_MONO_STOPS = ((252, 250, 246), (240, 236, 228), (32, 30, 38), (78, 74, 90))
_MONO_INK = (32, 30, 38)


def render_paint(spec: dict, out_dir: str) -> str:
    """Render the spec to a PNG inside out_dir. Returns the file path."""
    os.makedirs(out_dir, exist_ok=True)
    subject = spec.get("subject", "mandala")
    fn: Optional[Callable] = SUBJECTS.get(subject, s_mandala)
    color = spec.get("color")
    mono = bool(spec.get("mono")) or color is None
    # Default the text to AK: a logo with no brand in the request is still AK's
    # logo, and truncating the subject word used to stamp a literal "log" on it.
    label = spec.get("text") or ("AK" if fn in (s_poster, s_logo)
                                 else subject[:3])

    if mono:
        c = Canvas(spec["width"], spec["height"], spec.get("palette", "neon"),
                   bg="vertical", seed=spec.get("seed", 0),
                   stops_override=_MONO_STOPS, bg_override=_MONO_PAPER)
        if fn in (s_poster, s_logo):
            fn(c, _MONO_INK, label)
        else:
            fn(c, _MONO_INK)
    else:
        stops = palette_from_color(color)
        c = Canvas(spec["width"], spec["height"], spec.get("palette", "neon"),
                   bg="radial" if subject in ("orb", "sphere", "mandala",
                                              "poster", "logo", "brand",
                                              "monogram") else "vertical",
                   seed=spec.get("seed", 0), stops_override=stops)
        if fn in (s_poster, s_logo):
            fn(c, color, label)
        else:
            fn(c, color)

    img = c.finish()
    if mono:
        img = to_monochrome(img)
    elif color is not None:
        img = color_grade(img, palette_from_color(color))
    if spec.get("grain", True):
        img = _add_grain(img)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch for ch in spec.get("nice", "art") if ch.isalnum() or ch == "_")
    path = os.path.join(out_dir, f"{safe}_{stamp}.png")
    img.save(path, "PNG")
    return path


def to_monochrome(img: Image.Image) -> Image.Image:
    """Flatten any artwork to colourless ink on paper.

    One shared code path for every subject, so 'no colour mentioned' always
    gives the same paper-and-ink look no matter what was drawn."""
    from PIL import ImageOps
    grey = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    paper = Image.new("L", img.size, 247)
    inked = ImageChops.multiply(grey, Image.new("L", img.size, 246))
    return ImageChops.blend(paper, inked, 0.92).convert("RGB")


def color_grade(img: Image.Image, stops) -> Image.Image:
    """Tone the whole frame onto one named colour (dark -> colour -> light).

    Subjects that paint their own sky or sea would otherwise ignore the colour
    the user asked for, so the named colour is applied as a tritone map over
    the finished luminance.  Detail is preserved, but the picture can only be
    read as the colour that was requested."""
    from PIL import ImageOps
    grey = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    dark, mid, lite = stops[0], stops[1], stops[2]
    luts = ([], [], [])
    for i in range(256):
        t = i / 255.0
        col = _mix(dark, mid, t * 2) if t < 0.5 else _mix(mid, lite, (t - 0.5) * 2)
        for ch in range(3):
            luts[ch].append(col[ch])
    return Image.merge("RGB", tuple(grey.point(lut) for lut in luts))


def _add_grain(img: Image.Image) -> Image.Image:
    noise = Image.effect_noise(img.size, 18).convert("L")
    return ImageChops.overlay(img, Image.merge("RGB", (noise,) * 3))


def paint_dir(data_path: str) -> str:
    return os.path.join(data_path, "Builds", "paint")
