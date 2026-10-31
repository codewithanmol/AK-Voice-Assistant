"""AK - gesture driven holographic laptop control.

Runs as its own process (so AK's blocking voice loop stays free) and owns:

  * the camera (MediaPipe hand landmarker) + the vision analyzer,
  * the holographic 3D scene and its HUD overlay,
  * the vision intel popup (clothes / expression / held object),
  * the system control layer (volume, brightness, media, screenshot, windows).

AK (main.py) talks to it through two JSON files in <data>/ak/:
  cmd.json    - {"id": n, "cmd": "...", "args": {...}}
  status.json - {"running": true, "structure": "...", "gesture": "...", ...}
"""

from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import time
import traceback
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QApplication

# flat imports on purpose: they keep the AK HUD process light and avoid
# booting the whole AK package (brain, engine, automations...) just to
# draw a HUD.
import gesture_core
import holo_render
import holo_ui
import sysctl
import touch_layer
import vision_analyzer
from gesture_core import (G_FIST, G_NONE, G_PALM, G_PEACE, G_PINCH, G_POINT,
                          G_POINT1, G_THUMB_DOWN, G_THUMB_UP, HandTracker,
                          SwipeTracker)
from holo_render import PALETTES, STRUCTURE_ALIASES, STRUCTURES, Scene
from holo_ui import HoloOverlay
from camera_window import CameraWindow as IntelPanel
from touch_layer import TouchRecognizer

WHEEL_ITEMS = ["VOLUME", "BRIGHT", "MEDIA", "SHOT", "LOCK", "APPS", "FOLLOW",
               "TOUCH"]

# The holographic 3D structure is parked for now - the HUD runs vision,
# touch and window control only. Flip this to True to bring it back.
SHOW_STRUCTURE = False

# the live camera + face/wardrobe intel window on startup
SHOW_PANEL = True

HOTKEYS = {
    "ctrl+alt+j": "panel",     # toggle the vision intel popup
    "ctrl+alt+h": "scene",     # toggle the holographic structure
    "ctrl+alt+t": "touch",     # toggle the touchscreen mode
    "ctrl+alt+w": "windows",   # minimise / maximise / close the active window
    "ctrl+alt+k": "quit",      # stop AK
}


class _HotkeyBridge(QObject):
    """Marshals the global hotkey thread onto the Qt GUI thread."""
    triggered = pyqtSignal(str)


def _ipc_dir() -> str:
    try:
        import config as ak_config
        base = ak_config.get_data_dir()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), "AK Assistant")
    d = os.path.join(base, "ak")
    os.makedirs(d, exist_ok=True)
    return d


class _Worker(threading.Thread):
    """Camera + hand tracking thread. Vision analysis runs beside it so the
    reticle never stutters."""

    # how many times a second the hand landmarker is allowed to run
    tracker_fps = 8.0

    def __init__(self, cam_index: int = 0, vision: bool = True):
        super().__init__(daemon=True)
        self.cam_index = cam_index
        self.vision = vision
        self.out: "queue.Queue" = queue.Queue(maxsize=1)
        self.vision_in: "queue.Queue" = queue.Queue(maxsize=1)
        self.stop_flag = threading.Event()
        self.error = ""
        self.running = False
        self.fps = 0.0
        self.vision_fps = 0.0
        self.tracker = None
        self.report = None
        self.ready = False
        self.vision_ready = False
        self._report_lock = threading.Lock()
        self._vision_thread = None

    def _push(self, payload, queue_obj=None):
        q = queue_obj or self.out
        try:
            q.put_nowait(payload)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    # -- vision thread -------------------------------------------------------
    def _vision_loop(self):
        import traceback
        from vision_analyzer import VisionAnalyzer
        analyzer = VisionAnalyzer()
        if not analyzer.start():
            print(f"[ak] vision disabled: {analyzer.error}")
            return
        self.vision_ready = True
        last = time.perf_counter()
        ts = int(time.monotonic() * 1000)
        # the four vision models are heavy - they refresh a few times per
        # second instead of chewing a whole core
        interval = 0.55
        while not self.stop_flag.is_set():
            try:
                frame, hand_point = self.vision_in.get(timeout=0.4)
            except queue.Empty:
                continue
            # keep only the newest frame, drop whatever piled up behind it
            while True:
                try:
                    frame, hand_point = self.vision_in.get_nowait()
                except queue.Empty:
                    break
            # throttle: sleep so the models refresh ~4 times a second
            wait = interval - (time.perf_counter() - last)
            if wait > 0:
                time.sleep(wait)
            ts += 60
            try:
                report = analyzer.analyze(frame, ts, hand_point=hand_point)
            except Exception:
                traceback.print_exc()
                continue
            with self._report_lock:
                self.report = report
            self.vision_fps = 1.0 / max(1e-3, time.perf_counter() - last)
            last = time.perf_counter()
        analyzer.stop()

    def run(self):
        # warm the mediapipe import lock BEFORE the vision thread starts,
        # otherwise both threads import it at once and deadlock
        try:
            import mediapipe  # noqa: F401
        except Exception:
            pass
        tracker = HandTracker(camera_index=self.cam_index, max_hands=2)
        self.tracker = tracker
        if self.vision:
            self._vision_thread = threading.Thread(target=self._vision_loop,
                                                  daemon=True)
            self._vision_thread.start()
        if not tracker.start():
            self.error = tracker.error or "camera/model unavailable"
            print(f"[ak] {self.error}")
            self._push({"error": self.error})
            return
        self.running = True
        self.ready = True
        last = time.perf_counter()
        # the hand model is the heaviest thing in AK - it is capped at a
        # fixed rate so the rest of the PC stays responsive
        frame_interval = 1.0 / self.tracker_fps
        while not self.stop_flag.is_set():
            try:
                fh = tracker.read()
            except Exception:
                # mediapipe shuts its dispatcher down at process exit
                time.sleep(0.05)
                continue
            if fh is None:
                time.sleep(0.01)
                continue
            now = time.perf_counter()
            hand = fh.hands[0] if fh.hands else None
            hp = (float(hand.cursor[0]), float(hand.cursor[1])) if hand else None
            # keep the tracking rate under the cap
            now = time.perf_counter()
            gap = frame_interval - (now - last)
            if gap > 0:
                time.sleep(gap)
            self.fps = 1.0 / max(1e-3, time.perf_counter() - last)
            last = time.perf_counter()
            if self.vision:
                self._push((fh.frame, hp), self.vision_in)
            with self._report_lock:
                report = self.report
            self._push({"frame": fh.frame, "hands": fh.hands, "report": report,
                        "ts": time.perf_counter()})
        tracker.stop()
        # let the vision thread close its landmarkers properly, otherwise
        # mediapipe complains while the interpreter tears down
        vt = getattr(self, "_vision_thread", None)
        if vt is not None and vt.is_alive():
            vt.join(timeout=3.0)
        self.running = False


class AKController:
    """The brain: ties the camera, the scene, the HUD and the system actions."""

    def __init__(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.scene = Scene(width=900, height=560, palette="stark")
        self.scene.set_structure("icosahedron")
        self.overlay = HoloOverlay(scene=self.scene)
        self.overlay.show_scene = SHOW_STRUCTURE
        self.panel = IntelPanel()
        self.overlay.resize(QGuiApplication.primaryScreen().size())
        self.overlay.show()
        if SHOW_PANEL:
            self.panel.show_panel()

        self.swipe = SwipeTracker()
        self.state = {"frame": None, "hands": [], "report": None}
        self.lock = threading.Lock()
        self.cmd_id = 0
        self.last_cmd_id = -1
        self.ipc = _ipc_dir()
        # ignore commands left over from a previous session
        try:
            with open(os.path.join(self.ipc, "cmd.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list) and data:
                self.last_cmd_id = max(int(c.get("id", 0)) for c in data
                                       if isinstance(c, dict))
        except Exception:
            self.last_cmd_id = -1
        self.spin = 0.0
        self.base_scale = 1.0
        self.pinch_down = False
        self.wheel_open = False
        self.wheel_sel = 0
        self.adjust_mode = None
        self.adjust_last = 0.0
        self.follow_face = True      # panel slides with the face
        self.last_gesture = G_NONE
        self.gesture_since = time.perf_counter()
        self.thumb_fire = 0.0
        self.palm_since = 0.0
        self.status_text = "BOOTING"
        self.speaker = None
        self.talking = False
        self.render_acc = 0.0
        self.frame_count = 0
        self.fps = 0.0
        # touch layer - the hand behaves like a finger on glass
        self.touch = TouchRecognizer()
        self.touch_mode = True
        self.mouse_down = False
        self.pending_button = ""
        self.ripple = None
        self.click_suppress_until = 0.0
        self.manual_until = 0.0
        self.manual_mode = False
        self._cmd_cursor = None

        self.worker = _Worker()
        # mediapipe is imported inside the worker thread (before the vision
        # thread starts) - importing it in two threads at once deadlocks
        self.worker.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        # 30 fps is plenty for a HUD and keeps the CPU/GPU load low
        self.timer.start(33)

        self.ipc_timer = QTimer()
        self.ipc_timer.timeout.connect(self._ipc_poll)
        self.ipc_timer.start(150)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._write_status)
        self.status_timer.start(500)

        self._hk_bridge = _HotkeyBridge()
        self._hk_bridge.triggered.connect(self._on_hotkey, Qt.QueuedConnection)
        self._start_hotkeys()

        self._last_render = 0.0

    # -- global hotkeys ------------------------------------------------------
    def _start_hotkeys(self):
        try:
            import keyboard
        except Exception as exc:  # noqa: BLE001
            print(f"[ak] global hotkeys unavailable: {exc}")
            self._keyboard = None
            return
        self._keyboard = keyboard

        def _make(action):
            def _cb():
                try:
                    self._hk_bridge.triggered.emit(action)
                except Exception:
                    pass
            return _cb

        for combo, action in HOTKEYS.items():
            try:
                keyboard.add_hotkey(combo, _make(action))
            except Exception as exc:  # noqa: BLE001
                print(f"[ak] hotkey {combo} failed: {exc}")
        print("[ak] hotkeys: " + ", ".join(HOTKEYS))

    def _on_hotkey(self, action: str):
        if action == "panel":
            if self.panel.target_visible:
                self.panel.hide_panel()
            else:
                self.panel.show_panel()
            self.status_text = "VISION PANEL " + ("OPEN" if self.panel.target_visible
                                                   else "CLOSED")
        elif action == "scene":
            if not SHOW_STRUCTURE:
                self.overlay.show_scene = False
                self.status_text = "SCENE PARKED"
            else:
                self.overlay.show_scene = not self.overlay.show_scene
                self.status_text = "SCENE " + ("ON" if self.overlay.show_scene
                                              else "OFF")
        elif action == "touch":
            self._toggle_touch()
        elif action == "windows":
            hwnd = sysctl.get_foreground_window()
            sysctl.toggle_maximize(hwnd)
            self.status_text = "WINDOW TOGGLE"
        elif action == "quit":
            self.shutdown()

    def _toggle_touch(self):
        self.touch_mode = not self.touch_mode
        self.touch.reset()
        self.status_text = "TOUCH MODE " + ("ON" if self.touch_mode else "OFF")
        self.say("Touch mode " + ("on - pinch karke tap karo, hold karo to "
                                  "right click." if self.touch_mode else "off."))
        if not self.touch_mode and self.mouse_down:
            sysctl.mouse_up()
            self.mouse_down = False

    # -- speech --------------------------------------------------------------
    def _init_speaker(self):
        try:
            import config as ak_config
            from voice import Speaker
            cfg = ak_config.load_config()
            self.speaker = Speaker(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[ak] TTS unavailable: {exc}")
            self.speaker = None

    def say(self, text: str) -> None:
        if not text:
            return
        if self.speaker is None:
            self._init_speaker()
        if self.speaker is None:
            print(f"[ak] {text}")
            return
        try:
            self.speaker.say(text)
        except Exception:
            print(f"[ak] {text}")

    # -- main loop -----------------------------------------------------------
    def _tick(self):
        # pull the newest camera packet
        try:
            packet = self.worker.out.get_nowait()
            with self.lock:
                self.state = packet
        except queue.Empty:
            packet = None

        now = time.perf_counter()
        dt = min(0.1, now - getattr(self, "_last_tick", now))
        self._last_tick = now

        hands = self.state.get("hands") or []
        report = self.state.get("report")
        frame = self.state.get("frame")

        if frame is not None:
            try:
                self.panel.update_frame(frame, report, self.state.get("ts"),
                                        hands=hands)
            except Exception as exc:  # never let the panel kill the HUD
                self.panel_error = str(exc)
            self.panel.set_fps(self.worker.vision_fps)
            self.panel.follow_enabled = self.follow_face
            # the panel keeps up with the face in every direction - the video
            # is flipped, so the face moves the way it does in the real world
            if report is not None and report.face.detected:
                bx, by, bw, bh = report.face.bbox
                self.panel.follow_face(bx + bw / 2.0, by + bh / 2.0)

        gesture = G_NONE
        if hands:
            gesture = hands[0].gesture
        self._handle_gestures(hands, gesture, now)
        self._handle_touch(hands, now)

        # scene animation + render (cap ~30 fps)
        self.spin += dt * 0.35
        st = self.scene.structure
        if st is not None and self.overlay.show_scene:
            hand = hands[0] if hands else None
            if hand is not None and hand.gesture in (G_POINT, G_POINT1):
                st.yaw = self.spin + hand.yaw * 1.9
                st.pitch = max(-1.2, min(1.2, hand.pitch * 1.5))
                st.roll = hand.roll * 0.7
            else:
                st.yaw = self.spin
            st.tick(dt)
        if now - self._last_render > 0.033:
            self._last_render = now
            if self.overlay.show_scene:
                try:
                    self.scene.render(dt)
                except Exception:
                    pass

        # overlay state
        ov = self.overlay
        ov.gesture = gesture
        ov.hands = len(hands)
        ov.status = self.status_text
        if hands:
            h = hands[0]
            sw, sh = sysctl.screen_size()
            ov.cursor_px = (int(h.cursor[0] * sw), int(h.cursor[1] * sh))
            ov.pinch = h.pinch
            ov.pinching = h.pinch_active
        else:
            ov.pinching = False
        ov.command_wheel = (WHEEL_ITEMS, self.wheel_sel) if self.wheel_open else None
        ov.telemetry = self._telemetry(hands, report)
        ov.touch_mode = self.touch_mode
        ov.window_title = sysctl.foreground_title()
        if self.touch_mode:
            ov.hint = ("Pinch = tap  ·  hold = right click  ·  drag  ·  "
                       "two hands = scroll / zoom  ·  swipe = structure")
        else:
            ov.hint = ("Point to aim  ·  Pinch to click  ·  Fist to zoom  ·  "
                       "Swipe to switch")
        self.overlay.update()

    def _telemetry(self, hands, report) -> list:
        lines = [
            f"TRACK  {'%d HAND' % len(hands) if hands else 'NO HAND'}",
        ]
        if SHOW_STRUCTURE and self.scene.structure:
            st = self.scene.structure
            lines.append(f"SCENE  {st.key.upper()}")
            lines.append(f"ROT    {math.degrees(st.yaw) % 360:6.1f}  "
                         f"{math.degrees(st.pitch):6.1f}  {math.degrees(st.roll):6.1f}")
            lines.append(f"SCALE  {st.scale:5.2f}  EXPL {st.explode:4.2f}")
        else:
            lines.append("SCENE  PARKED")
        lines.append(f"SCAN   {self.worker.vision_fps:4.1f} FPS  TRK {self.worker.fps:4.1f} FPS")
        lines.append(f"INPUT  {'MANUAL' if self.manual_mode else 'HAND  '}   "
                     f"TOUCH {'ON ' if self.touch_mode else 'OFF'}   "
                     f"FOLLOW {'ON ' if self.follow_face else 'OFF'}")
        if report is not None:
            if report.face.detected:
                lines.append(f"FACE   {report.face.expression.upper()} "
                             f"{int(report.face.confidence * 100)}%"
                             + (f"  GLASSES" if report.face.glasses else ""))
                if report.face.gaze and report.face.gaze != "centre":
                    lines.append(f"LOOK   {report.face.gaze.upper()}  "
                                 f"YAW {report.face.yaw:+.0f}  "
                                 f"PITCH {report.face.pitch:+.0f}")
            if report.cloth.detected:
                lines.append(f"WARDROBE {report.cloth.primary.upper()} "
                             f"{report.cloth.garment.upper()}")
                if report.cloth.sleeve or report.cloth.bottom:
                    lines.append(f"GARMENT {(report.cloth.sleeve or '-').upper()} "
                                 f"{(report.cloth.bottom or '-').upper()}")
            if report.obj.detected:
                lines.append(f"OBJECT {report.obj.name.upper()} "
                             f"{'HELD' if report.obj.held else 'SEEN'}")
        return lines

    # -- touch layer ---------------------------------------------------------
    def _handle_touch(self, hands, now):
        """The hand acts as a finger on glass.

        pinch = press/release (Windows does the double-click detection),
        hold = right click, pinch + move = drag, two hands = scroll / zoom.
        """
        sw, sh = sysctl.screen_size()
        cursor = (0.5, 0.5)
        pinch = False
        aiming = False
        if hands:
            h = hands[0]
            cursor = (float(h.cursor[0]), float(h.cursor[1]))
            pinch = bool(h.pinch_active)
            aiming = h.gesture in (G_POINT, G_POINT1, G_PINCH)

        self.touch.update(now, cursor, pinch, hands)
        events = self.touch.take()

        px = cursor[0] * sw
        py = cursor[1] * sh
        bid = self.overlay.hit_window_button(px, py) if hands else ""
        self.overlay.hover_button = bid

        # ── manual override ───────────────────────────────────────────────
        # If the physical mouse moved on its own, the human is in charge -
        # AK steps back for a few seconds instead of fighting for the cursor.
        real = sysctl.cursor_pos()
        if self._cmd_cursor is not None and not self.mouse_down:
            if (abs(real[0] - self._cmd_cursor[0]) > 6 or
                    abs(real[1] - self._cmd_cursor[1]) > 6):
                self.manual_until = now + 4.0
                self.status_text = "MANUAL MOUSE"
        self._cmd_cursor = None
        manual = now < self.manual_until
        self.manual_mode = manual

        # the pointer follows the hand, but only while the hand is actually
        # aiming - a resting fist should never drag the mouse around
        if hands and not self.mouse_down and not manual and aiming:
            sysctl.move_cursor(px, py, 0.04)
            self._cmd_cursor = (px, py)

        for ev in events:
            if ev.kind == "press":
                if self.click_suppress_until > now:
                    continue
                if bid:
                    self.pending_button = bid
                    self.status_text = "PRESS " + bid.upper()
                    continue
                sysctl.move_cursor(px, py, 0.05)
                sysctl.mouse_down(px, py)
                self._cmd_cursor = (px, py)
                self.mouse_down = True
                self.status_text = "TOUCH"
                self.overlay.reticle_flash = 1.0
            elif ev.kind == "release":
                if self.pending_button:
                    self._run_window_button(self.pending_button)
                    self.pending_button = ""
                    self.ripple = (px, py, now)
                    continue
                if self.mouse_down:
                    sysctl.mouse_up()
                    self.mouse_down = False
                self.status_text = "RELEASED"
            elif ev.kind == "long_press":
                if self.mouse_down:
                    sysctl.mouse_up()
                    self.mouse_down = False
                if not self.pending_button:
                    sysctl.right_click(px, py)
                    self.status_text = "CONTEXT MENU"
            elif ev.kind == "drag_move":
                if self.mouse_down:
                    sysctl.move_cursor(px, py, 0.03)
                    self._cmd_cursor = (px, py)
                    self.status_text = "DRAG"
            elif ev.kind == "scroll":
                sysctl.scroll(int(ev.amount))
                self.status_text = "SCROLL"
            elif ev.kind == "zoom":
                steps = int(ev.amount * 260)
                if steps:
                    sysctl.zoom_scroll(steps)
                    self.status_text = "ZOOM"
            elif ev.kind == "tap":
                self.overlay.reticle_flash = 1.0
            elif ev.kind == "double_tap":
                self.overlay.reticle_flash = 1.0
                self.status_text = "DOUBLE TAP"

        if self.ripple is not None:
            rx, ry, t0 = self.ripple
            age = now - t0
            if age > 0.45:
                self.ripple = None
                self.overlay.touch_ripple = None
            else:
                self.overlay.touch_ripple = (rx, ry, age)

    def _run_window_button(self, bid: str):
        hwnd = sysctl.get_foreground_window()
        if bid == "min":
            sysctl.minimize_window(hwnd)
            self.say("Window minimized.")
        elif bid == "max":
            state = sysctl.toggle_maximize(hwnd)
            self.say(f"Window {state}.")
        elif bid == "desk":
            sysctl.minimize_all()
            self.say("Showing the desktop.")
        elif bid == "close":
            sysctl.close_window(hwnd)
            self.say("Window closed.")
        elif bid == "power":
            self._power_off()
        self.status_text = bid.upper()

    def _power_off(self):
        """AK OFF button - the whole visual stack goes down in one tap.

        Stops the holographic gesture control, the camera panel and asks the
        voice assistant to close the visualizer as well. Say "ak chalao"
        later to bring the gesture control back.
        """
        try:
            self.panel.hide_panel()
        except Exception:
            pass
        try:
            with open(os.path.join(self.ipc, "gesture_off.flag"), "w",
                      encoding="utf-8") as fh:
                fh.write(str(time.time()))
        except Exception:
            pass
        try:
            self._write_status(extra={"running": False, "gesture_off": True})
        except Exception:
            pass
        self.say("AK gesture control band. Jab chahiye bolo - ak chalao.")
        self.status_text = "AK OFF"
        self.shutdown()

    # -- gesture handling ----------------------------------------------------
    def _handle_gestures(self, hands, gesture, now):
        if not hands:
            self.pinch_down = False
            self.wheel_open = False
            self.status_text = "SCANNING FOR HANDS"
            return
        h = hands[0]

        # continuous level control (volume / brightness) picked from the wheel
        if self.adjust_mode:
            if h.pinch_active and not self.pinch_down:
                self.adjust_mode = None
                self.pinch_down = True
                self.wheel_open = False
                self.status_text = "ADJUST DONE"
                return
            lvl = int(max(0.0, min(1.0, (h.cursor[0] - 0.5) * 2.0)) * 100)
            if now - self.adjust_last > 0.08:
                self.adjust_last = now
                if self.adjust_mode == "VOLUME":
                    v = sysctl.set_volume(lvl)
                    self.status_text = f"VOLUME {v}"
                else:
                    v = sysctl.set_brightness(lvl)
                    self.status_text = f"BRIGHTNESS {v}"
            return

        if gesture != self.last_gesture:
            self.gesture_since = now
            self.last_gesture = gesture

        # pinch -> the touch layer owns press / release / drag / hold
        if h.pinch_active and not self.pinch_down:
            self.pinch_down = True
            if self.wheel_open:
                self._wheel_activate()
        elif (not h.pinch_active) and self.pinch_down:
            self.pinch_down = False
            self.status_text = "RELEASED"


        # fist -> depth (zoom)
        if gesture == G_FIST:
            self.base_scale = max(0.35, min(3.0, 0.6 + h.size * 4.2))
            if self.scene.structure:
                self.scene.structure.target_scale = self.base_scale
            self.status_text = "DEPTH"
        elif gesture in (G_POINT, G_POINT1):
            self.status_text = "AIMING"
        elif gesture == G_PALM:
            self.palm_since = now
            self.status_text = "OPEN PALM"
        elif gesture == G_PEACE:
            self.wheel_open = True
            self._wheel_select(h)
            self.status_text = "COMMAND WHEEL"
        elif gesture == G_THUMB_UP:
            if now - self.thumb_fire > 0.9:
                self.thumb_fire = now
                v = sysctl.volume_up(6)
                self.status_text = f"VOLUME {v if v >= 0 else 'UP'}"
        elif gesture == G_THUMB_DOWN:
            if now - self.thumb_fire > 0.9:
                self.thumb_fire = now
                v = sysctl.volume_down(6)
                self.status_text = f"VOLUME {v if v >= 0 else 'DOWN'}"

        if gesture != G_PEACE:
            self.wheel_open = False

        # swipes
        velocity = getattr(getattr(self.worker, "tracker", None), "engine", None)
        vel = velocity.velocity if velocity is not None else (0.0, 0.0)
        direction = self.swipe.update(h.cursor, vel, now)
        if direction == "right":
            self.scene.next_structure(1)
            self.status_text = "NEXT STRUCTURE"
        elif direction == "left":
            self.scene.next_structure(-1)
            self.status_text = "PREV STRUCTURE"
        elif direction == "up":
            self.panel.toggle()
            self.status_text = "VISION PANEL"
        elif direction == "down":
            self.panel.hide_panel()
            self.status_text = "PANEL HIDDEN"

    def _wheel_select(self, hand):
        sw, sh = sysctl.screen_size()
        cx, cy = sw * 0.5, sh * 0.52
        r = min(sw, sh) * 0.22
        ang = math.atan2(hand.cursor[1] * sh - cy, hand.cursor[0] * sw - cx)
        n = len(WHEEL_ITEMS)
        idx = int(round((ang + math.pi / 2) / (2 * math.pi) * n)) % n
        self.wheel_sel = idx

    def _wheel_activate(self):
        item = WHEEL_ITEMS[self.wheel_sel]
        # the pinch that opened the wheel must not also click the desktop
        self.click_suppress_until = time.perf_counter() + 0.7
        if item in ("VOLUME", "BRIGHT"):
            # stay in the wheel and turn the hand into a slider
            self.adjust_mode = item
            self.adjust_last = 0.0
            self.wheel_open = True
            self.status_text = f"{item} ADJUST"
            self.say(f"{item.lower()} adjust mode - move your hand left or right, "
                     f"pinch to confirm.")
            return
        self.wheel_open = False
        self.adjust_mode = None
        if item == "MEDIA":
            sysctl.media_key("media_play_pause")
            self.say("Media play pause.")
        elif item == "SHOT":
            path = sysctl.screenshot()
            self.say(f"Screenshot saved." if path else "Screenshot failed.")
        elif item == "LOCK":
            self.say("Locking the workstation.")
            time.sleep(0.4)
            sysctl.lock_workstation()
        elif item == "APPS":
            self.say("Say an app name to open it.")
        elif item == "FOLLOW":
            self.follow_face = not self.follow_face
            self.panel.follow_enabled = self.follow_face
            self.status_text = "FACE FOLLOW " + ("ON" if self.follow_face else "OFF")
            self.say("Face follow " + ("on - camera panel ab aapke face ke saath "
                                       "move karega." if self.follow_face else "off."))
        elif item == "TOUCH":
            self._toggle_touch()

    # -- IPC -----------------------------------------------------------------
    def _ipc_poll(self):
        path = os.path.join(self.ipc, "cmd.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return
        for cmd in data:
            if not isinstance(cmd, dict):
                continue
            try:
                cid = int(cmd.get("id", 0))
            except (TypeError, ValueError):
                continue
            if cid <= self.last_cmd_id:
                continue
            self.last_cmd_id = cid
            try:
                self._exec_cmd(str(cmd.get("cmd", "")).lower(), cmd.get("args") or {})
            except Exception:
                traceback.print_exc()

    def _exec_cmd(self, cmd: str, args: dict):
        scene = self.scene
        if cmd in ("structure", "set_structure", "next", "prev", "rotate", "zoom",
                   "explode", "material", "palette", "wireframe", "solid"):
            if not SHOW_STRUCTURE:
                self.status_text = "SCENE PARKED"
                return
        if cmd in ("structure", "set_structure"):
            key = str(args.get("key", "")).lower()
            key = STRUCTURE_ALIASES.get(key, key)
            if key in STRUCTURES:
                scene.set_structure(key)
            self.status_text = f"STRUCTURE {scene.structure.key.upper()}"
        elif cmd == "next":
            scene.next_structure(int(args.get("step", 1)))
            self.status_text = f"STRUCTURE {scene.structure.key.upper()}"
        elif cmd == "rotate":
            st = scene.structure
            if st:
                if args.get("axis") == "pitch":
                    st.pitch += float(args.get("amount", 0.3))
                elif args.get("axis") == "roll":
                    st.roll += float(args.get("amount", 0.3))
                else:
                    st.yaw += float(args.get("amount", 0.5))
                    self.spin = st.yaw
        elif cmd == "zoom":
            st = scene.structure
            if st:
                st.target_scale = max(0.3, min(3.5,
                                               st.target_scale * float(args.get("factor", 1.2))))
        elif cmd == "explode":
            st = scene.structure
            if st:
                st.explode = max(0.0, min(2.5, st.explode + float(args.get("amount", 0.5))))
        elif cmd == "material":
            st = scene.structure
            if st:
                st.material = str(args.get("mode", "wire"))
        elif cmd == "palette":
            name = str(args.get("name", "stark")).lower()
            if name in PALETTES:
                scene.palette_name = name
                scene.palette = PALETTES[name]
        elif cmd == "vision":
            self.panel.show_panel()
            self.status_text = "VISION SCAN"
            if args.get("speak", True):
                self._speak_report(self.state.get("report"))
        elif cmd == "panel":
            if args.get("visible", True):
                self.panel.show_panel()
            else:
                self.panel.hide_panel()
        elif cmd == "follow":
            if "enabled" in args:
                self.follow_face = bool(args.get("enabled"))
            else:
                self.follow_face = not self.follow_face
            self.panel.follow_enabled = self.follow_face
            self.status_text = "FACE FOLLOW " + ("ON" if self.follow_face else "OFF")
            self.say("Face follow " + ("on." if self.follow_face else "off."))
        elif cmd == "speak":
            self.say(str(args.get("text", "")))
        elif cmd == "volume":
            act = str(args.get("action", "up"))
            if act == "up":
                v = sysctl.volume_up(int(args.get("step", 5)))
            elif act == "down":
                v = sysctl.volume_down(int(args.get("step", 5)))
            else:
                v = sysctl.toggle_mute()
            self.status_text = f"VOLUME {v}"
        elif cmd == "brightness":
            act = str(args.get("action", "up"))
            if act == "up":
                v = sysctl.brightness_up(int(args.get("step", 10)))
            elif act == "down":
                v = sysctl.brightness_down(int(args.get("step", 10)))
            else:
                v = -1
            self.status_text = f"BRIGHTNESS {v}"
        elif cmd == "media":
            sysctl.media_key(str(args.get("key", "media_play_pause")))
        elif cmd == "screenshot":
            path = sysctl.screenshot()
            self.say("Screenshot saved." if path else "Screenshot failed.")
        elif cmd == "lock":
            self.say("Locking the workstation.")
            time.sleep(0.4)
            sysctl.lock_workstation()
        elif cmd == "app":
            name = str(args.get("name", "")).strip()
            if name:
                sysctl.run_app(name)
        elif cmd == "website":
            sysctl.open_website(str(args.get("url", "")))
        elif cmd == "alt_tab":
            sysctl.alt_tab()
        elif cmd == "touch":
            enabled = args.get("enabled")
            if enabled is None:
                self._toggle_touch()
            else:
                want = bool(enabled)
                if want != self.touch_mode:
                    self._toggle_touch()
        elif cmd == "window":
            action = str(args.get("action", "")).lower()
            hwnd = sysctl.get_foreground_window()
            if action == "min":
                sysctl.minimize_window(hwnd)
                self.say("Window minimized.")
            elif action == "max":
                state = sysctl.toggle_maximize(hwnd)
                self.say(f"Window {state}.")
            elif action == "restore":
                sysctl.restore_window(hwnd)
                self.say("Window restored.")
            elif action == "close":
                sysctl.close_window(hwnd)
                self.say("Window closed.")
            elif action in ("desk", "desktop"):
                sysctl.minimize_all()
                self.say("Showing the desktop.")
            self.status_text = "WINDOW " + action.upper()
        elif cmd == "hide_scene":
            if not SHOW_STRUCTURE:
                self.overlay.show_scene = False
                self.status_text = "SCENE PARKED"
            else:
                self.overlay.show_scene = not self.overlay.show_scene
        elif cmd == "quit":
            self.shutdown()
        elif cmd == "power":
            self._power_off()

    def _speak_report(self, report):
        if report is None:
            return
        if report.narration:
            self.say(report.narration)

    def _write_status(self, extra: Optional[dict] = None):
        st = self.scene.structure
        payload = {
            "running": True,
            "ready": bool(self.worker.ready),
            "vision_ready": bool(self.worker.vision_ready),
            "pid": os.getpid(),
            "structure": (st.key if st else "") if SHOW_STRUCTURE else "",
            "gesture": self.last_gesture,
            "hands": len(self.state.get("hands") or []),
            "tracking_fps": round(self.worker.fps, 1),
            "vision_fps": round(self.worker.vision_fps, 1),
            "scale": round(st.scale, 2) if st else 1.0,
            "explode": round(st.explode, 2) if st else 0.0,
            "panel": self.panel.target_visible,
            "panel_anim": round(float(self.panel.visible_anim), 2),
            "panel_frame": self.panel.frame is not None,
            "panel_qframe": bool(self.panel._qframe is not None
                                 and not self.panel._qframe.isNull()),
            "panel_error": getattr(self, "panel_error", ""),
            "follow": bool(self.follow_face),
            "touch": bool(self.touch_mode),
            "window": sysctl.foreground_title(),
            "vision": self._status_vision(),
            "updated": time.time(),
        }
        if extra:
            payload.update(extra)
        tmp = os.path.join(self.ipc, "status.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, os.path.join(self.ipc, "status.json"))
        except Exception:
            pass

    def _status_vision(self):
        report = self.state.get("report")
        if report is None:
            return {}
        out = {}
        if report.face.detected:
            out["face"] = {"expression": report.face.expression,
                           "confidence": round(report.face.confidence, 3),
                           "traits": [t[0] for t in report.face.traits[:3]],
                           "glasses": report.face.glasses,
                           "gaze": report.face.gaze,
                           "yaw": report.face.yaw,
                           "pitch": report.face.pitch,
                           "roll": report.face.roll,
                           "speaking": report.face.speaking}
        if report.cloth.detected:
            out["cloth"] = {"primary": report.cloth.primary,
                            "primary_hex": report.cloth.primary_hex,
                            "secondary": report.cloth.secondary,
                            "garment": report.cloth.garment,
                            "style": report.cloth.style,
                            "pattern": report.cloth.pattern,
                            "sleeve": report.cloth.sleeve,
                            "neckline": report.cloth.neckline,
                            "layers": report.cloth.layers,
                            "bottom": report.cloth.bottom,
                            "contrast": report.cloth.contrast,
                            "coverage": report.cloth.coverage,
                            "swatches": report.cloth.swatches[:3]}
        if report.obj.detected:
            out["object"] = {"name": report.obj.name,
                             "confidence": round(report.obj.confidence, 3),
                             "held": report.obj.held}
        out["narration"] = report.narration
        return out

    def shutdown(self):
        self.worker.stop_flag.set()
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            kb = getattr(self, "_keyboard", None)
            if kb is not None:
                for combo in HOTKEYS:
                    try:
                        kb.remove_hotkey(combo)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self.worker.join(timeout=2.0)
        except Exception:
            pass
        self.app.quit()


def main():
    ak = AKController()
    ak._write_status()
    print("[ak] running. Ctrl+C to stop.")
    try:
        ak.app.exec_()
    except KeyboardInterrupt:
        pass
    finally:
        ak.shutdown()


if __name__ == "__main__":
    main()

