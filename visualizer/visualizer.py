"""visualizer.py — Pygame window + main 60 FPS loop for the Orb Visualizer.

Owns the realtime Energy model (state params x audio snapshot x time) and renders
Meta-Orb onto the screen. Glow trick: the orb is drawn small then `smoothscale`d up —
bilinear upsampling acts as a built-in bloom pass. Auto-demo mode (SPACE) cycles
styles + states so you can judge the whole thing hands-free.
"""

import math
import random

import pygame

from audio_capture import AudioEngine
from orbs import OrbRenderer, Energy, STYLE_NAMES
from state_machine import StateMachine, STATES_ORDER

_KEYS = {
    pygame.K_1: "1", pygame.K_2: "2", pygame.K_3: "3", pygame.K_4: "4",
    pygame.K_f: "STATES",
}
_STATE_KEYS = {pygame.K_F1: "IDLE", pygame.K_F2: "USER_LISTENING",
               pygame.K_F3: "THINKING", pygame.K_F4: "ASSISTANT_SPEAKING",
               pygame.K_F5: "ERROR"}


class Visualizer:
    """The windowed app. Hooks: set_state(), switch_orb_style(), push_audio(), quit()."""

    def __init__(self, engine=None, w=960, h=640, fps=60, style_size=256):
        pygame.init()
        pygame.display.set_caption("AK Orb Visualizer - [F1-F5] state | [1-4] style | SPACE auto")
        self.screen = pygame.display.set_mode((w, h))
        self.clock = pygame.time.Clock()
        self.fps = fps
        self.engine = engine or AudioEngine()
        self.orbs = OrbRenderer(style_size)
        self.sm = StateMachine()
        self.style_size = style_size
        # draw-region geometry (centered square)
        self.area = min(w, h) - 60
        self.ox = (w - self.area) // 2
        self.oy = 40
        self.t = 0.0
        self.running = True
        self.auto = False            # auto-demo mode
        self.last_switch = 0.0
        self.font = pygame.font.SysFont(None, 20)

    # ========================= public hooks (for AK main.py) ==================
    def set_state(self, state_name: str):
        self.sm.set(state_name)

    def switch_orb_style(self, style, duration: float = 1.5):
        self.orbs.switch(style, duration)

    def push_audio(self, chunk, channel="spk"):
        self.engine.push(chunk, channel)

    def quit(self):
        self.running = False

    # ========================= internal helpers ==============================
    def _state_by_audio(self):
        """Auto state suggestion from audio activity — used only when demo is off
        direction hint is convenient, the caller owns final state semantics."""
        snap = self.engine.snapshot()
        if snap["spk"]["rms"] > 0.05:
            return "ASSISTANT_SPEAKING"
        if snap["mic"]["rms"] > 0.03:
            return "USER_LISTENING"
        return "IDLE"

    def _auto_run(self):
        """Hands-free showcase: rotate style every 9 s, states every few seconds."""
        if self.t - self.last_switch > 9.0:
            self.last_switch = self.t
            self.orbs.switch(self.orbs.idx + 1, 1.8)
            idx = int(self.t // 4.0) % len(STATES_ORDER)
            self.sm.set(STATES_ORDER[idx])

    def _overlay(self, txt, y, color=(200, 200, 210)):
        surf = self.font.render(txt, True, color)
        self.screen.blit(surf, (self.ox, y))

    def _draw_ui(self):
        self._overlay(f"STATE: {self.sm.name}   STYLE: {STYLE_NAMES[self.orbs.idx]}", 8, (150, 255, 255))
        self._overlay("[1-4] style  [F1-F5] state  [SPACE] auto  [H] help  [ESC] exit", self.h - 24, (120, 120, 130))

    def _handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif ev.key == pygame.K_SPACE:
                    self.auto = not self.auto
                elif ev.key in _KEYS:
                    self.orbs.switch(int(ev.unicode) - 1, 1.5)
                elif ev.key in _STATE_KEYS:
                    self.sm.set(_STATE_KEYS[ev.key])
                elif ev.key == pygame.K_h:
                    self.show_help = not self.show_help

    # ========================= main loop ======================================
    def run(self):
        self.show_help = False
        try:
            self.engine.start()
        except Exception as e:
            print(f"[viz] audio start issue: {e}")
        while self.running:
            dt = self.clock.tick(self.fps) / 1000.0
            self.t += dt
            self._handle_events()
            if self.auto:
                self._auto_run()
            else:
                self.sm.set(self._state_by_audio())       # gentle nudge keeps life in orb
            self.sm.advance()

            # ---- build the Energy model ---------------------------------------
            audio = self.engine.snapshot()
            e = Energy(self.sm.cur, audio, self.t)

            # draw orb at low res then bloom-upscale (the free glow pass)
            self.orbs.update()
            self.orbs.draw(e)
            orb = pygame.transform.smoothscale(self.orbs.surf, (self.area, self.area))

            # ERROR-state glitch shake: jitter + horizontal tear
            screen = self.screen
            screen.fill((5, 4, 10))
            if self.sm.cur["glitch"] > 0.02:
                jx = int(random.uniform(-7, 7) * self.sm.cur["glitch"])
                jy = int(random.uniform(-4, 4) * self.sm.cur["glitch"])
                screen.blit(orb, (self.ox + jx, self.oy + jy))
                # tear strip
                ty = self.oy + int(random.uniform(0, self.area - 12))
                strip = orb.subsurface((0, ty, self.area, 6)).copy()
                screen.blit(strip, (self.ox + int(random.uniform(-14, 14) * self.sm.cur["glitch"]), ty))
            else:
                screen.blit(orb, (self.ox, self.oy))

            # soft halo breathing around the orb (huge + cheap)
            col = pygame.Color(0, 0, 0)
            col.hsva = (((0.55 + self.t * 0.02) * 360) % 360, 60, 15 * (0.5 + e.volume), 100)
            pygame.draw.circle(screen, col, (self.ox + self.area // 2, self.oy + self.area // 2),
                               int(self.area * (0.52 + 0.06 * e.volume)))

            self._draw_ui()
            pygame.display.flip()

        self._shutdown()

    def _shutdown(self):
        self.engine.close()
        pygame.quit()