"""Touchscreen-style interaction for AK's holographic HUD.

The hand stops being a "pinch = click" switch and becomes a finger on glass:

    quick pinch            -> tap          (single left click)
    two quick pinches      -> double tap   (double click)
    pinch and hold         -> long press   (right click / context menu)
    pinch + move           -> drag
    two hands pinching     -> zoom         (ctrl + wheel)
    two hands sliding      -> scroll

The recogniser is pure logic - it never touches the mouse itself. The HUD
turns the events into real desktop actions, so the same events can be
replayed, logged or ignored.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TouchEvent:
    kind: str                      # tap | double_tap | long_press | drag_start |
                                   # drag_move | drag_end | scroll | zoom
    x: float = 0.0                 # normalised screen position 0..1
    y: float = 0.0
    dx: float = 0.0                # drag delta since the last event
    dy: float = 0.0
    amount: float = 0.0            # scroll steps / zoom factor
    button: str = "left"
    ts: float = 0.0


class TouchRecognizer:
    """Turns hand state into touch events.

    Timings follow a real touchscreen: a tap is short, a double tap has to
    land inside the double-click window, and a long press only counts when
    the finger stayed still.
    """

    TAP_MAX = 0.32            # longer than this is a hold, not a tap
    LONG_PRESS = 0.65         # hold this long -> context menu
    DOUBLE_MAX = 0.38         # second tap must land inside this window
    DOUBLE_SLOP = 0.05        # ... and this close to the first tap
    MOVE_EPS = 0.016          # movement that turns a pinch into a drag
    ZOOM_EPS = 0.01

    def __init__(self):
        self.events: list = []
        self._down_at: Optional[float] = None
        self._down_pos: tuple = (0.5, 0.5)
        self._cur_pos: tuple = (0.5, 0.5)
        self._prev_pos: tuple = (0.5, 0.5)
        self._dragging = False
        self._held_fired = False
        self._last_tap: Optional[tuple] = None      # (time, x, y)
        self._pending_tap: Optional[tuple] = None   # (time, x, y)
        self._zoom_base: Optional[float] = None
        self._scroll_base: Optional[float] = None
        self._two_hand = False

    # -- helpers -------------------------------------------------------------
    def _emit(self, kind: str, x: float = 0.0, y: float = 0.0, dx: float = 0.0,
              dy: float = 0.0, amount: float = 0.0, button: str = "left") -> None:
        self.events.append(TouchEvent(kind=kind, x=x, y=y, dx=dx, dy=dy,
                                       amount=amount, button=button,
                                       ts=time.perf_counter()))

    def take(self) -> list:
        """Return the events queued since the last call."""
        out, self.events = self.events, []
        return out

    @staticmethod
    def _dist(a: tuple, b: tuple) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    # -- main ----------------------------------------------------------------
    def update(self, now: float, cursor: tuple, pinch_active: bool,
               hands: Optional[list] = None) -> list:
        """Feed one frame of hand state. Returns the events of this frame."""
        hands = hands or []
        cursor = (float(cursor[0]), float(cursor[1]))
        dx_frame = cursor[0] - self._prev_pos[0]
        dy_frame = cursor[1] - self._prev_pos[1]
        self._prev_pos = cursor
        self._cur_pos = cursor

        pinchers = [h for h in hands if getattr(h, "pinch_active", False)]

        # ---- two hand zoom (both hands pinching) ---------------------------
        if len(pinchers) >= 2:
            a = pinchers[0].pinch_mid
            b = pinchers[1].pinch_mid
            dist = math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
            if self._zoom_base is None:
                self._zoom_base = dist
                self._two_hand = True
            elif self._zoom_base > 1e-4:
                factor = dist / self._zoom_base
                if abs(factor - 1.0) > self.ZOOM_EPS:
                    self._emit("zoom", cursor[0], cursor[1], amount=factor - 1.0)
            self._cancel_single()
            self._down_at = None
            self._dragging = False
            self._held_fired = False
            return self.take()

        # ---- two hand scroll (both hands open, sliding) --------------------
        if len(hands) >= 2 and not pinchers:
            mid = sum(float(h.pinch_mid[1]) for h in hands[:2]) / 2.0
            if self._scroll_base is None:
                self._scroll_base = mid
            else:
                delta = mid - self._scroll_base
                if abs(delta) > 0.012:
                    self._scroll_base = mid
                    self._emit("scroll", cursor[0], cursor[1], amount=-delta * 26.0)
            self._cancel_single()
            return self.take()
        self._scroll_base = None

        # ---- single hand ----------------------------------------------------
        if pinch_active and self._down_at is None:
            self._down_at = now
            self._down_pos = cursor
            self._dragging = False
            self._held_fired = False
            self._zoom_base = None
            self._emit("press", cursor[0], cursor[1])
            return self.take()

        if pinch_active and self._down_at is not None:
            held = now - self._down_at
            moved = self._dist(cursor, self._down_pos)

            # long press fires while the finger is still down
            if not self._held_fired and not self._dragging and \
                    held >= self.LONG_PRESS and moved < self.MOVE_EPS:
                self._held_fired = True
                self._emit("long_press", cursor[0], cursor[1], button="right")

            if moved > self.MOVE_EPS:
                if not self._dragging:
                    self._dragging = True
                    self._emit("drag_start", cursor[0], cursor[1])
                else:
                    self._emit("drag_move", cursor[0], cursor[1],
                               dx=dx_frame, dy=dy_frame)
                self._down_pos = cursor
            return self.take()

        # pinch released
        if not pinch_active and self._down_at is not None:
            held = now - self._down_at
            moved = self._dist(cursor, self._down_pos)
            pos = cursor
            self._down_at = None
            self._emit("release", pos[0], pos[1])

            if self._dragging:
                self._dragging = False
                self._emit("drag_end", pos[0], pos[1])
                return self.take()

            if self._held_fired:
                self._held_fired = False
                return self.take()

            if held <= self.TAP_MAX and moved < self.MOVE_EPS:
                self._register_tap(now, pos)
            return self.take()

        # still waiting for a possible second tap of a double tap
        self._flush_pending(now)
        return self.take()

    # -- tap bookkeeping -----------------------------------------------------
    def _register_tap(self, now: float, pos: tuple) -> None:
        last = self._last_tap
        if last and (now - last[0]) <= self.DOUBLE_MAX and \
                self._dist((last[1], last[2]), pos) <= self.DOUBLE_SLOP:
            self._pending_tap = None
            self._last_tap = None
            self._emit("double_tap", pos[0], pos[1])
            return
        self._last_tap = (now, pos[0], pos[1])
        self._pending_tap = (now, pos[0], pos[1])

    def _flush_pending(self, now: float) -> None:
        pending = self._pending_tap
        if pending is None:
            return
        if (now - pending[0]) > self.DOUBLE_MAX:
            self._pending_tap = None
            self._emit("tap", pending[1], pending[2])

    def _cancel_single(self) -> None:
        self._pending_tap = None
        self._last_tap = None

    def reset(self) -> None:
        self.events = []
        self._down_at = None
        self._dragging = False
        self._held_fired = False
        self._pending_tap = None
        self._last_tap = None
        self._zoom_base = None
        self._scroll_base = None
