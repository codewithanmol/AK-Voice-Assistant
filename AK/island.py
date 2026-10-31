"""
AK Island Bridge
----------------
Shows AK's text replies inside the "AK Dynamic Island" (Windhawk mod).

Two delivery paths:
  1. Named pipe (preferred) - the mod hosts a pipe at
     ``\\\\.\\pipe\\ak_dynamic_island`` and paints the message directly on the
     island as an "AK" card. One UTF-16LE ``sender|text`` frame per message.
  2. Silent Windows toast (fallback) - used if the mod's pipe is not open.
     The island's notification module listens through UserNotificationListener,
     so each toast still appears as a native island card.

Design:
  * The toast fallback uses one long-lived hidden PowerShell host process that
    is spawned on first use and kept warm.
  * Everything stays exception-safe: a missing island can never break a reply.
  * Purely outward. Nothing in the terminal/UI changes.
"""

from __future__ import annotations

import atexit
import base64
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_PS_SCRIPT = r'''
$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::InputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$appId = 'APP_ID_PLACEHOLDER'
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if ($line.Trim().Length -eq 0) { continue }
    try {
        $raw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line))
        $idx = $raw.IndexOf([char]0x1F)
        if ($idx -lt 0) { $title = 'AK'; $body = $raw }
        else { $title = $raw.Substring(0, $idx); $body = $raw.Substring($idx + 1) }
        $ets = [System.Security.SecurityElement]::Escape($title)
        $ebs = [System.Security.SecurityElement]::Escape($body)
        $xmlString = '<toast><visual><binding template="ToastGeneric"><text>' + $ets + '</text><text>' + $ebs + '</text></binding></visual><audio silent="true"/></toast>'
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($xmlString)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        try { $toast.Tag = 'ak-island'; $toast.Group = 'ak-island' } catch {}
        $notifier.Show($toast)
    } catch {}
}
'''.replace("APP_ID_PLACEHOLDER", _APP_ID)

_PIPE_NAME = r"\\.\pipe\ak_dynamic_island"
_PIPE_TIMEOUT_MS = 60
_PIPE_MAX_CHARS = 200


def _send_pipe(label: str, text: str) -> bool:
    """Send one card to the Dynamic Island mod's named pipe.

    Returns True only when the mod accepted the full UTF-16LE frame.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.WaitNamedPipeW.restype = wintypes.BOOL
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        safe_label = (label or "AK").replace("|", "/").strip() or "AK"
        body = text if len(text) <= _PIPE_MAX_CHARS else text[:197] + "..."
        payload = f"{safe_label}|{body}".encode("utf-16-le")

        if not kernel32.WaitNamedPipeW(_PIPE_NAME, _PIPE_TIMEOUT_MS):
            return False

        handle = kernel32.CreateFileW(
            _PIPE_NAME, 0x40000000, 0, None, 3, 0, None
        )
        invalid = ctypes.c_void_p(-1).value
        if not handle or handle == invalid:
            return False
        try:
            written = wintypes.DWORD(0)
            ok = kernel32.WriteFile(
                handle, payload, len(payload), ctypes.byref(written), None
            )
            return bool(ok) and written.value == len(payload)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_queue: "queue.Queue[str]" = queue.Queue(maxsize=64)
_worker: threading.Thread | None = None
_started = False
_script_path: str | None = None
_enabled = True


def _script_file() -> str:
    global _script_path
    if _script_path and os.path.isfile(_script_path):
        return _script_path
    path = os.path.join(tempfile.gettempdir(), "ak_island_toast.ps1")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_PS_SCRIPT)
    _script_path = path
    return path


def _spawn() -> subprocess.Popen | None:
    if sys.platform != "win32":
        return None
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", _script_file()],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as e:
        print(f"[ISLAND][WARN] toast helper start failed: {e}")
        return None


def _ensure_proc() -> subprocess.Popen | None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        return _proc
    _proc = _spawn()
    return _proc


def _worker_loop() -> None:
    while True:
        item = _queue.get()
        if item is None:
            return
        try:
            proc = _ensure_proc()
            if proc is None or proc.stdin is None:
                continue
            proc.stdin.write(item + "\n")
            proc.stdin.flush()
        except Exception:
            try:
                if _proc is not None:
                    _proc.kill()
            except Exception:
                pass
            globals()["_proc"] = None


def start() -> bool:
    """Warm the helper in the background. Safe to call more than once."""
    global _worker, _started
    if not _enabled or sys.platform != "win32":
        return False
    with _lock:
        if _started:
            return True
        _started = True
    _worker = threading.Thread(target=_worker_loop, name="AK-Island-Toast",
                               daemon=True)
    _worker.start()
    threading.Thread(target=_ensure_proc, name="AK-Island-Warm", daemon=True).start()
    return True


def notify(message: str, title: str = "AK", sender: str = "assistant") -> None:
    """Show one island card. Tries the mod pipe, then falls back to a toast."""
    if not _enabled or not message:
        return
    text = str(message).strip()
    if not text:
        return
    if len(text) > 1600:
        text = text[:1597] + "..."
    label = (title or "AK").strip() or "AK"
    if sender == "user":
        label = title or "Boss"
    if sender == "thinking":
        label = "AK"
    if _send_pipe(label, text):
        return
    raw = f"{label}\x1f{text}".encode("utf-8", "replace")
    try:
        _queue.put_nowait(base64.b64encode(raw).decode("ascii"))
    except Exception:
        pass


def thinking(active: bool = True) -> None:
    """Show 'Thinking...' on the island while the model is thinking."""
    if not _enabled or not active:
        return
    try:
        notify("Thinking...", "AK", "thinking")
    except Exception:
        pass


def stop() -> None:
    global _proc, _started
    try:
        _queue.put_nowait(None)
    except Exception:
        pass
    try:
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()
    except Exception:
        pass
    _proc = None
    _started = False


atexit.register(stop)
