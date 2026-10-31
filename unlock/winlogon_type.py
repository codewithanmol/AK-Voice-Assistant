"""AK Unlock helper — Winlogon desktop pe password type karta hai.

Ye script SERVICE dwara Winlogon (secure desktop) par spawn hoti hai, jab
Windows LOCKED hota hai. Password temp file se padh ke LogonUI ke password
field me daalti hai aur Enter dabati hai. Machine unlocked ho to kuch
nahi karti (window nahi milti) — bilkul safe.

Run:  python winlogon_type.py
Env:   AK_PW_FILE = temp file path containing the password
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                 wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, ctypes.c_void_p]
user32.SendMessageW.restype = wintypes.LPARAM
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
kernel32.ExitProcess.argtypes = [wintypes.UINT]

WM_SETTEXT = 0x000C
VK_RETURN = 0x0D
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


def _send_key(vk: int, down: bool) -> None:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = 0
    inp.union.ki.dwFlags = 0 if down else KEYEVENTF_KEYUP
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    time.sleep(0.04)


def find_password_field(timeout: float = 30.0):
    """Find the LogonUI password edit (title '[SecureDesktop] Password')."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, "[SecureDesktop] Password")
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return None


def main() -> int:
    pw_file = os.environ.get("AK_PW_FILE", "")
    if not os.path.isfile(pw_file):
        return 2
    try:
        with open(pw_file, "r", encoding="utf-8") as fh:
            password = fh.read().strip()
    except Exception:
        password = ""
    finally:
        try:
            os.remove(pw_file)
        except Exception:
            pass
    if not password:
        return 3

    hwnd = find_password_field()
    if not hwnd:
        return 4  # locked nahi hai — window nahi mili

    # focus + set password + enter
    edit = user32.FindWindowExW(hwnd, None, "Edit", None)
    if not edit:
        edit = hwnd
    user32.SetFocus(edit)
    time.sleep(0.1)
    buf = ctypes.create_unicode_buffer(password)
    user32.SendMessageW(edit, WM_SETTEXT, 0, buf)
    time.sleep(0.2)
    _send_key(VK_RETURN, down=True)
    _send_key(VK_RETURN, down=False)
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    try:
        sys_exit = main()
    except Exception:
        sys_exit = 1
    kernel32.ExitProcess(sys_exit)