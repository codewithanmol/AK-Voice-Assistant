"""System control layer for AK's holographic mode.

Everything here talks to Windows directly (ctypes / pycaw / pyautogui) so
gestures can drive the laptop: volume, brightness, media keys, screenshots,
window management and plain mouse control.

No external service, no admin rights needed (except the brightness path on
some odd OEM drivers).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from typing import Optional

try:  # optional
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.04
except Exception:  # pragma: no cover
    pyautogui = None


# ── mouse / keyboard helpers ───────────────────────────────────────────────
def screen_size():
    if pyautogui is None:
        return (1920, 1080)
    return pyautogui.size()


def cursor_pos() -> tuple:
    """Current mouse position on screen."""
    try:
        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _PT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except Exception:
        pass
    return (0, 0)


def move_cursor(x: float, y: float, duration: float = 0.12) -> None:
    if pyautogui is None:
        return
    try:
        pyautogui.moveTo(int(x), int(y), duration=duration)
    except Exception:
        pass


def click(x: Optional[float] = None, y: Optional[float] = None,
          button: str = "left") -> None:
    if pyautogui is None:
        return
    try:
        if x is not None and y is not None:
            pyautogui.click(int(x), int(y), button=button)
        else:
            pyautogui.click(button=button)
    except Exception:
        pass


def double_click(x: Optional[float] = None, y: Optional[float] = None) -> None:
    if pyautogui is None:
        return
    try:
        if x is not None and y is not None:
            pyautogui.doubleClick(int(x), int(y))
        else:
            pyautogui.doubleClick()
    except Exception:
        pass


def scroll(clicks: int) -> None:
    if pyautogui is None:
        return
    try:
        pyautogui.scroll(clicks)
    except Exception:
        pass


def zoom_scroll(clicks: int) -> None:
    """Ctrl + wheel - the zoom every app understands."""
    if pyautogui is None:
        return
    try:
        pyautogui.keyDown("ctrl")
        pyautogui.scroll(clicks)
        pyautogui.keyUp("ctrl")
    except Exception:
        pass


def mouse_down(x: Optional[float] = None, y: Optional[float] = None,
               button: str = "left") -> None:
    if pyautogui is None:
        return
    try:
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y), duration=0.05)
        pyautogui.mouseDown(button=button)
    except Exception:
        pass


def mouse_up(button: str = "left") -> None:
    if pyautogui is None:
        return
    try:
        pyautogui.mouseUp(button=button)
    except Exception:
        pass


def right_click(x: Optional[float] = None, y: Optional[float] = None) -> None:
    if pyautogui is None:
        return
    try:
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y), duration=0.06)
        pyautogui.click(button="right")
    except Exception:
        pass


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """Move / resize a window."""
    user32 = ctypes.windll.user32
    try:
        flags = 0x0004 | 0x0010        # NOZORDER | NOACTIVATE
        return bool(user32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h),
                                        flags))
    except Exception:
        return False


def drag(from_x: float, from_y: float, to_x: float, to_y: float,
         duration: float = 0.35) -> None:
    """Press at one point, move, release - a real drag on the desktop."""
    if pyautogui is None:
        return
    try:
        pyautogui.moveTo(int(from_x), int(from_y), duration=0.08)
        pyautogui.dragTo(int(to_x), int(to_y), duration=duration, button="left")
    except Exception:
        pass


def hotkey(*keys: str) -> None:
    if pyautogui is None:
        return
    try:
        pyautogui.hotkey(*keys)
    except Exception:
        pass


# ── media keys (no external app needed) ────────────────────────────────────
VK = {
    "media_play_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1,
    "media_stop": 0xB2, "volume_mute": 0xAD, "volume_down": 0xAE,
    "volume_up": 0xAF,
}


def media_key(name: str) -> None:
    code = VK.get(name.lower())
    if code is None:
        return
    user32 = ctypes.windll.user32
    user32.keybd_event(code, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(code, 0, 2, 0)


# ── volume via pycaw ───────────────────────────────────────────────────────
def _volume_endpoint():
    try:
        from ctypes import POINTER, cast
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import HRESULT
    except Exception:
        return None
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def get_volume() -> int:
    ep = _volume_endpoint()
    if ep is None:
        return -1
    try:
        return int(round(ep.GetMasterVolumeLevelScalar() * 100))
    except Exception:
        return -1


def set_volume(level: int) -> int:
    level = max(0, min(100, int(level)))
    ep = _volume_endpoint()
    if ep is None:
        return -1
    try:
        ep.SetMasterVolumeLevelScalar(level / 100.0, None)
        return level
    except Exception:
        return -1


def volume_up(step: int = 5) -> int:
    cur = get_volume()
    return set_volume(max(0, cur + step)) if cur >= 0 else _media_vol("up")


def volume_down(step: int = 5) -> int:
    cur = get_volume()
    return set_volume(max(0, cur - step)) if cur >= 0 else _media_vol("down")


def toggle_mute() -> str:
    ep = _volume_endpoint()
    if ep is not None:
        try:
            muted = bool(ep.GetMute())
            ep.SetMute(0 if muted else 1, None)
            return "unmuted" if muted else "muted"
        except Exception:
            pass
    media_key("volume_mute")
    return "toggled"


def _media_vol(direction: str) -> int:
    media_key("volume_up" if direction == "up" else "volume_down")
    return -1


# ── brightness through the Dxva2 VCP interface ─────────────────────────────
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class _MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


def _enum_monitors():
    monitors = []
    user32 = ctypes.windll.user32
    monitor_enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                           wintypes.HDC, wintypes.LPRECT,
                                           wintypes.LPARAM)

    def _callback(hmon, hdc, lprect, lparam):
        info = _MONITORINFOEX()
        info.cbSize = ctypes.sizeof(_MONITORINFOEX)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            monitors.append((hmon, info.szDevice))
        return True

    user32.EnumDisplayMonitors(None, None, monitor_enum_proc(_callback), None)
    return monitors


def get_brightness() -> int:
    user32 = ctypes.windll.user32
    dxva2 = ctypes.windll.dxva2
    for hmon, _name in _enum_monitors():
        count = wintypes.DWORD(0)
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
            continue
        handles = (wintypes.HANDLE * count.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, handles):
            continue
        for h in handles:
            current = wintypes.DWORD(0)
            maximum = wintypes.DWORD(0)
            code = wintypes.DWORD(0)
            if dxva2.GetVCPFeatureAndVCPFeatureReply(h, 0x10, ctypes.byref(code),
                                                      ctypes.byref(current),
                                                      ctypes.byref(maximum)):
                if maximum.value:
                    return int(current.value * 100 // maximum.value)
    return -1


def set_brightness(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    user32 = ctypes.windll.user32
    dxva2 = ctypes.windll.dxva2
    for hmon, _name in _enum_monitors():
        count = wintypes.DWORD(0)
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
            continue
        handles = (wintypes.HANDLE * count.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, handles):
            continue
        for h in handles:
            code = wintypes.DWORD(0)
            current = wintypes.DWORD(0)
            maximum = wintypes.DWORD(0)
            if dxva2.GetVCPFeatureAndVCPFeatureReply(h, 0x10, ctypes.byref(code),
                                                      ctypes.byref(current),
                                                      ctypes.byref(maximum)):
                if maximum.value:
                    value = int(percent * maximum.value // 100)
                    dxva2.SetVCPFeature(h, 0x10, value)
                    return percent
    return -1


def brightness_up(step: int = 10) -> int:
    cur = get_brightness()
    return set_brightness(cur + step) if cur >= 0 else -1


def brightness_down(step: int = 10) -> int:
    cur = get_brightness()
    return set_brightness(cur - step) if cur >= 0 else -1


# ── screenshots / windows ───────────────────────────────────────────────────
def screenshot(folder: Optional[str] = None) -> str:
    if pyautogui is None:
        return ""
    try:
        if folder is None:
            folder = os.path.join(os.path.expanduser("~"), "Pictures",
                                  "AK Screenshots")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"ak_{time.strftime('%Y%m%d_%H%M%S')}.png")
        pyautogui.screenshot(path)
        return path
    except Exception:
        return ""


def list_windows() -> list:
    """Visible top level windows: (handle, title)."""
    wins = []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        wins.append((hwnd, buf.value))
        return True

    user32.EnumWindows(enum_proc(_cb), None)
    return wins


def focus_window(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def close_window(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    try:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        return True
    except Exception:
        return False


def alt_tab() -> None:
    hotkey("alt", "tab")


# ── window management ──────────────────────────────────────────────────────
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
SW_SHOW = 5
GA_ROOT = 2
SC_MINIMIZEALL = 0xF020


def get_foreground_window() -> int:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    user32 = ctypes.windll.user32
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def foreground_title() -> str:
    return window_title(get_foreground_window())


def _show(hwnd: int, cmd: int) -> bool:
    if not hwnd:
        return False
    try:
        ctypes.windll.user32.ShowWindow(hwnd, cmd)
        return True
    except Exception:
        return False


def minimize_window(hwnd: int = 0) -> bool:
    return _show(hwnd or get_foreground_window(), SW_MINIMIZE)


def maximize_window(hwnd: int = 0) -> bool:
    return _show(hwnd or get_foreground_window(), SW_MAXIMIZE)


def restore_window(hwnd: int = 0) -> bool:
    return _show(hwnd or get_foreground_window(), SW_RESTORE)


def is_maximized(hwnd: int = 0) -> bool:
    hwnd = hwnd or get_foreground_window()
    if not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.IsZoomed(hwnd))
    except Exception:
        return False


def toggle_maximize(hwnd: int = 0) -> str:
    """Maximise a normal window, restore a maximised one."""
    hwnd = hwnd or get_foreground_window()
    if not hwnd:
        return "no window"
    if is_maximized(hwnd):
        restore_window(hwnd)
        return "restored"
    maximize_window(hwnd)
    return "maximized"


def close_foreground() -> bool:
    hwnd = get_foreground_window()
    return close_window(hwnd)


def minimize_all() -> None:
    """Show the desktop - every window minimised at once."""
    user32 = ctypes.windll.user32
    shell = user32.FindWindowW("Shell_TrayWnd", None)
    if shell:
        try:
            user32.PostMessageW(shell, 0x0112, SC_MINIMIZEALL, 0)   # WM_SYSCOMMAND
            return
        except Exception:
            pass
    hotkey("win", "d")


def show_desktop() -> None:
    """Toggle between the desktop and the last window set (Win+D)."""
    hotkey("win", "d")


def window_at_point(x: float, y: float) -> int:
    """Top level window under a screen point."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not hwnd:
            return 0
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return int(root or hwnd)
    except Exception:
        return 0


def list_foreground_chain() -> list:
    """Foreground window plus the visible windows behind it (recent first)."""
    user32 = ctypes.windll.user32
    fg = get_foreground_window()
    out = []
    for hwnd, title in list_windows():
        if title and hwnd != fg:
            out.append((hwnd, title))
        if len(out) >= 6:
            break
    return out


def lock_workstation() -> None:
    try:
        ctypes.windll.user32.LockWorkStation()
    except Exception:
        pass


def run_app(name: str) -> bool:
    """Launch an app by name via the Run dialog (fast, no index needed)."""
    try:
        hotkey("win", "r")
        time.sleep(0.45)
        if pyautogui is not None:
            pyautogui.typewrite(name, interval=0.01)
            pyautogui.press("enter")
        return True
    except Exception:
        return False


def open_website(url: str) -> bool:
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        subprocess.Popen(["cmd", "/c", "start", "", url],
                         shell=False, creationflags=getattr(subprocess,
                                                          "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False
