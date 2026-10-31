"""screen_recorder.py — AK's eye while it works.

Captures the screen (or the focused window) during a task so AK can look at
what it did afterwards, split it into steps and turn it into a playbook.

What it stores in the session folder:
    frames/step-01.png   one frame per detected step (the evidence)
    frames/auto-001.png  activity frames (screen changed a lot)
    recording.gif        short animated replay of the session
    timeline.json        machine readable session (steps, windows, timings)

No ffmpeg needed - the GIF is written with Pillow.
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import threading
import time

try:
    from PIL import Image, ImageGrab
    _PIL = True
except Exception:                                   # pragma: no cover
    Image = ImageGrab = None
    _PIL = False

try:
    import numpy as np
    _NP = True
except Exception:                                   # pragma: no cover
    _NP = False

_USER32 = None
try:
    _USER32 = ctypes.windll.user32
except Exception:                                   # pragma: no cover
    _USER32 = None

try:
    from ctypes import wintypes as _wintypes
except Exception:                                   # pragma: no cover
    _wintypes = None


def _win_rect():
    """Rect of the focused window (l,t,r,b) - None when it cannot be read."""
    if _USER32 is None:
        return None
    try:
        hwnd = _USER32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = _wintypes.RECT() if _wintypes is not None else None
        if rect is None:
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rect = RECT()
        if not _USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 120 or h < 90:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def _win_title():
    """Title of the focused window ('' when unknown)."""
    if _USER32 is None:
        return ""
    try:
        hwnd = _USER32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = _USER32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _USER32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def _virtual_screen():
    """Full virtual desktop rect (handles multi-monitor)."""
    if _USER32 is None:
        return None
    try:
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        x = _USER32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = _USER32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = _USER32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = _USER32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return (x, y, x + w, y + h)
    except Exception:
        pass
    return None


class ScreenRecorder:
    """Records a session: frames per step + an animated replay.

    region: "focus" (the focused window) | "screen" (whole desktop)
    fps:    how often the background thread samples the screen
    diff:   0..1 - frame difference that counts as "something happened"
    """

    def __init__(self, out_dir, region="focus", fps=2.0, diff=0.012,
                 max_seconds=900, gif_width=520, auto_steps=True):
        self.out_dir = out_dir
        self.region = region
        self.fps = max(0.5, float(fps))
        self.diff = float(diff)
        self.max_seconds = max_seconds
        self.gif_width = gif_width
        self.auto_steps = auto_steps
        self.frames_dir = os.path.join(out_dir, "frames")
        self.steps = []
        self.auto = []
        self.t0 = time.time()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._gif = []            # downscaled PIL frames for the replay
        self._last_small = None
        self._last_title = ""
        self._active = False

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self):
        if not _PIL:
            return False
        os.makedirs(self.frames_dir, exist_ok=True)
        self._active = True
        # the window we are already in is the starting point, not a step
        self._last_title = _win_title()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        th = self._thread
        if th is not None:
            self._thread = None
            th.join(timeout=3.0)
        self._active = False
        self._write_gif()
        self._write_timeline()
        return self.summary()

    # ── capture ─────────────────────────────────────────────────────────
    def _grab(self):
        """Grab the screen -> PIL image (None on failure)."""
        bbox = None
        if self.region == "focus":
            bbox = _win_rect()
        if bbox is None:
            bbox = _virtual_screen()
        try:
            if bbox:
                return ImageGrab.grab(bbox=bbox, all_screens=True)
            return ImageGrab.grab(all_screens=True)
        except Exception:
            try:
                return ImageGrab.grab()
            except Exception:
                return None

    def _small(self, img, size=(192, 108)):
        if not _NP:
            return img.resize(size).convert("L")
        return np.asarray(img.convert("L").resize(size), dtype=np.float32)

    def _loop(self):
        """Background sampler: keeps the replay buffer, spots activity."""
        period = 1.0 / self.fps
        while not self._stop.is_set():
            try:
                img = self._grab()
                if img is not None:
                    small = self._small(img)
                    changed = 0.0
                    with self._lock:
                        if self._last_small is not None:
                            diff = abs(small - self._last_small).mean() / 255.0
                            changed = diff
                        self._last_small = small
                        # keep a rolling replay buffer
                        if len(self._gif) < 260:
                            w = self.gif_width
                            h = max(1, int(img.height * w / max(1, img.width)))
                            self._gif.append(img.convert("RGB").resize((w, h)))
                    if changed >= self.diff:
                        self._save_auto(img)
                    self._auto_step(img)
            except Exception:
                pass
            if time.time() - self.t0 > self.max_seconds:
                break
            time.sleep(period)

    def _auto_step(self, img):
        """A window change IS a step - that is how AK learns what the user
        did by hand (which app, in which order) without a vision model."""
        if not self.auto_steps:
            return
        title = _win_title()
        if not title or title == self._last_title:
            return
        self._last_title = title
        if len(self.steps) >= 40:
            return
        # the previous step already happened in this very window
        if self.steps and (self.steps[-1].get("app") or "") == title:
            return
        self.mark(f"Working in {title[:60]}", kind="action", app=title)

    def _save_auto(self, img):
        path = os.path.join(self.frames_dir, f"auto-{len(self.auto) + 1:03d}.png")
        try:
            img.save(path, "PNG")
        except Exception:
            return
        self.auto.append({"file": os.path.basename(path),
                          "t": round(time.time() - self.t0, 2)})

    # ── steps ───────────────────────────────────────────────────────────
    def mark(self, title, kind="action", command="", detail="",
             shot=True, app=""):
        """Record a step and (optionally) grab its evidence frame."""
        img = self._grab() if shot else None
        with self._lock:
            n = len(self.steps) + 1
            shot_name = f"step-{n:02d}.png"
            if img is not None:
                try:
                    img.save(os.path.join(self.frames_dir, shot_name), "PNG")
                except Exception:
                    shot_name = ""
            step = {
                "n": n,
                "t": round(time.time() - self.t0, 2),
                "title": title,
                "kind": kind,
                "command": command or "",
                "detail": detail or "",
                "app": app or _win_title(),
                "shot": shot_name,
            }
            self.steps.append(step)
        return step

    # ── output ──────────────────────────────────────────────────────────
    def _write_gif(self):
        if not _PIL or not self._gif:
            return ""
        path = os.path.join(self.out_dir, "recording.gif")
        try:
            frames = self._gif[::max(1, len(self._gif) // 90)][:90]
            if not frames:
                return ""
            frames[0].save(path, save_all=True, append_images=frames[1:],
                           duration=int(1000 / max(1, self.fps)),
                           loop=0, optimize=True)
            return os.path.basename(path)
        except Exception:
            return ""

    def _write_timeline(self):
        data = {
            "started": self.t0,
            "ended": time.time(),
            "duration": round(time.time() - self.t0, 1),
            "steps": self.steps,
            "activity": self.auto,
            "region": self.region,
        }
        try:
            with open(os.path.join(self.out_dir, "timeline.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def summary(self):
        return {
            "dir": self.out_dir,
            "frames_dir": self.frames_dir,
            "steps": list(self.steps),
            "activity": len(self.auto),
            "duration": round(time.time() - self.t0, 1),
        }


def capture_once(path, region="focus", quality=80):
    """One-shot screenshot helper (returns the saved path or '')."""
    if not _PIL:
        return ""
    rec = ScreenRecorder(os.path.dirname(path) or ".", region=region)
    img = rec._grab()
    if img is None:
        return ""
    try:
        if str(path).lower().endswith((".jpg", ".jpeg")):
            img.convert("RGB").save(path, "JPEG", quality=quality)
        else:
            img.save(path, "PNG")
    except Exception:
        return ""
    return path
