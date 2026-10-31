"""AK Unlock Service (LocalSystem) — SECURE.

- Named pipe `\\.\pipe\AKUnlock` par sunta hai feedback YELLOW-ONLY:
    pipe DACL = SYSTEM + "Authenticated Users" ko sirf FULL; baaki sab DENY.
- AK (user session, voiceprint-verified) password line pipe me likhta hai.
- Service winlogon_type.py ko ACTIVE console session ke Winlogon (secure)
  desktop par spawn karta hai, jo password type karke Enter dabata hai.
- Machine locked nahi hai to helper ko kuch nahi milega → koi asar nahi.

Service khud kabhi kisi ko unlock nahi karta — sirf locally pipe ke through.

Install:  python install_service.py   (ADMIN)
Log:      service.log (is folder me)
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

import win32con
import win32event
import win32file
import win32pipe
import win32security
import win32service
import win32serviceutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPE = r"\\.\pipe\AKUnlock"
_LOG = os.path.join(_HERE, "service.log")
_HELPER = os.path.join(_HERE, "winlogon_type.py")
_TMP_PW = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                       "AKUnlock_pw.tmp")


def log(msg: str) -> None:
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ── Winlogon injection (ctypes) ────────────────────────────────────────
class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", LUID_AND_ATTRIBUTES * 1),
    ]


kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

PH = ctypes.POINTER(wintypes.HANDLE)
PDWORD = ctypes.POINTER(wintypes.DWORD)

kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetCurrentProcess.argtypes = []
kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
kernel32.WTSGetActiveConsoleSessionId.argtypes = []
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetLastError.restype = wintypes.DWORD

advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, PH]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                      ctypes.c_void_p, ctypes.c_int, ctypes.c_int, PH]
advapi32.DuplicateTokenEx.restype = wintypes.BOOL
advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                         ctypes.c_void_p, wintypes.DWORD]
advapi32.SetTokenInformation.restype = wintypes.BOOL
advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                           ctypes.POINTER(LUID)]
advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL,
                                           ctypes.POINTER(TOKEN_PRIVILEGES), wintypes.DWORD,
                                           ctypes.c_void_p, ctypes.c_void_p]
advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
advapi32.CreateProcessAsUserW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
    ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
    wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)]
advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_ASSIGN_PRIMARY = 0x0001
MAXIMUM_ALLOWED = 0x02000000
SecurityImpersonation = 2
TokenPrimary = 1
TokenSessionId = 12
SE_PRIVILEGE_ENABLED = 0x00000002
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x0001


def _enable_privilege(h_token, name: str) -> None:
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp),
                                       ctypes.sizeof(tp), None, None)
    except Exception:
        pass


def spawn_on_winlogon(cmd: str, env: dict) -> bool:
    """Spawn `cmd` as SYSTEM inside the active console session, on the
    Winlogon (secure) desktop. Returns True/False."""
    try:
        sess = kernel32.WTSGetActiveConsoleSessionId()

        h_proc = kernel32.GetCurrentProcess()
        h_tok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h_proc,
                                         TOKEN_DUPLICATE | TOKEN_QUERY
                                         | TOKEN_ASSIGN_PRIMARY
                                         | TOKEN_ADJUST_PRIVILEGES,
                                         ctypes.byref(h_tok)):
            log("OpenProcessToken err=%d" % kernel32.GetLastError())
            return False
        h_new = wintypes.HANDLE()
        if not advapi32.DuplicateTokenEx(h_tok, MAXIMUM_ALLOWED, None,
                                         SecurityImpersonation, TokenPrimary,
                                         ctypes.byref(h_new)):
            log("DuplicateTokenEx err=%d" % kernel32.GetLastError())
            return False

        sess_val = wintypes.DWORD(sess)
        if not advapi32.SetTokenInformation(h_new, TokenSessionId,
                                            ctypes.byref(sess_val), 4):
            log("SetTokenInformation sess err=%d" % kernel32.GetLastError())
            return False

        for priv in ("SeIncreaseQuotaPrivilege",
                     "SeAssignPrimaryTokenPrivilege"):
            _enable_privilege(h_new, priv)

        items = ["%s=%s" % (k, v) for k, v in env.items()]
        env_block = ("\0".join(items) + "\0\0")
        env_c = ctypes.create_unicode_buffer(env_block)
        cmd_buf = ctypes.create_unicode_buffer(cmd)

        si = STARTUPINFO()
        si.cb = ctypes.sizeof(STARTUPINFO)
        si.lpDesktop = "WinSta0\\Winlogon"
        si.dwFlags = STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        pi = PROCESS_INFORMATION()

        ok = advapi32.CreateProcessAsUserW(
            h_new, None, cmd_buf,
            None, None, False,
            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            ctypes.cast(env_c, ctypes.c_void_p),
            None, ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            log("CreateProcessAsUserW err=%d" % kernel32.GetLastError())
            return False

        kernel32.CloseHandle(pi.hThread)
        kernel32.CloseHandle(pi.hProcess)
        log("spawned on Winlogon desktop (sess %d)" % sess)
        return True
    except Exception as exc:  # noqa: BLE001
        log("spawn error: %r" % exc)
        return False


def do_unlock(password: str) -> bool:
    """Write password to a SYSTEM-only temp file, then type it on Winlogon."""
    try:
        with open(_TMP_PW, "w", encoding="utf-8") as fh:
            fh.write(password)
    except Exception as exc:  # noqa: BLE001
        log("write pw tmp failed: %r" % exc)
        return False

    py = sys.executable
    cmd = '"%s" "%s"' % (py, _HELPER)
    env = dict(os.environ)
    env["AK_PW_FILE"] = _TMP_PW
    return spawn_on_winlogon(cmd, env)


def _pipe_security_attributes():
    """Pipe DACL: sirf SYSTEM + 'Authenticated Users' ko access, baaki DENY."""
    sd = win32security.SECURITY_DESCRIPTOR()
    dacl = win32security.ACL()
    sys_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    auth_sid = win32security.CreateWellKnownSid(
        win32security.WinAuthenticatedUserSid)
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION,
                             win32con.GENERIC_ALL, sys_sid)
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION,
                             win32con.GENERIC_ALL, auth_sid)
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    return sa


# ── Service ─────────────────────────────────────────────────────────────
class AKUnlockService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AKUnlock"
    _svc_display_name_ = "AK Voice Unlock"
    _svc_description_ = "Types the Windows password on the lock screen when (voiceprint-verified) AK says 'unlock'."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._stop = False

    def _pipe_loop(self):
        log("pipe loop started")
        while not self._stop:
            pipe = None
            try:
                pipe = win32pipe.CreateNamedPipe(
                    _PIPE, win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    1, 4096, 4096, 3000, _pipe_security_attributes())
                win32pipe.ConnectNamedPipe(pipe, None)
                _, data = win32file.ReadFile(pipe, 4096)
                pw = data.strip().decode("utf-8", "replace")
                if pw:
                    log("trigger: %d chars (len only)" % len(pw))
                    if not do_unlock(pw):
                        log("do_unlock failed")
                else:
                    log("empty trigger ignored")
            except Exception as exc:  # noqa: BLE001
                if not self._stop:
                    log("pipe error: %r" % exc)
                time.sleep(0.3)
            finally:
                if pipe:
                    try:
                        win32pipe.DisconnectNamedPipe(pipe)
                    except Exception:
                        pass
                    try:
                        pipe.Close()
                    except Exception:
                        pass
        log("pipe loop stopped")

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop = True
        win32event.SetEvent(self.hWaitStop)
        log("stopping")

    def SvcDoRun(self):
        log("service running")
        t = threading.Thread(target=self._pipe_loop, daemon=True)
        t.start()
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
        log("service exiting")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-spawn":
        do_unlock("pw-test-only-window-not-found")
        print("spawn attempt done — log dekh lo.")
    else:
        win32serviceutil.HandleCommandLine(AKUnlockService)