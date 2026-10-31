# The small pieces the live stages share: the colour helper, the light
# layer that leaves trails and the glow that can be re-tinted cheaply.
import pygame


def _hsv(h, s, v):
    """HSV -> RGB tuple (h in degrees)."""
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


class Emitter:
    """A light layer that fades every frame - gives the orb its soft trail."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.surf = pygame.Surface((w, h), pygame.SRCALPHA)
        self.fade = pygame.Surface((w, h))
        self.fade.fill((236, 236, 236, 255))

    def resize(self, w, h):
        self.w, self.h = w, h
        self.surf = pygame.Surface((w, h), pygame.SRCALPHA)
        self.fade = pygame.Surface((w, h))
        self.fade.fill((236, 236, 236, 255))

    def fade_step(self):
        self.surf.blit(self.fade, (0, 0),
                       special_flags=pygame.BLEND_RGBA_MULT)

    def present(self, screen):
        screen.blit(self.surf, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)


class Glow:
    """A cached radial glow that can be re-tinted cheaply."""

    def __init__(self, size=192, gain=1.0):
        self.size = size
        self.gain = float(gain)
        base = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size / 2.0
        for r in range(size // 2, 0, -1):
            a = int(255 * (1.0 - (r / (size / 2.0)) ** 2.1) ** 1.7 * self.gain)
            pygame.draw.circle(base, (255, 255, 255, a),
                               (int(c), int(c)), r)
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
