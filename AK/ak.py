"""Bridge between AK (main.py) and the AK holographic HUD process.

AK stays a fast, blocking voice loop; the HUD runs as a separate process with
its own PyQt event loop. They talk through two small JSON files inside
<data_path>/ak/ so no sockets or firewall prompts are involved.

    cmd.json    AK  -> HUD    {"id": n, "cmd": "next", "args": {...}}
    status.json HUD   -> AK    {"running": true, "structure": "...", ...}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AK_UI_SCRIPT = os.path.join(ROOT, "AK", "ak_ui.py")


def _data_dir() -> str:
    try:
        from .config import get_data_dir
        return get_data_dir()
    except Exception:
        pass
    # standalone import (script / tests) - AK/ is not a package here
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
            sys.path.insert(0, os.path.join(ROOT, "AK"))
        from config import get_data_dir
        return get_data_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "AK Assistant")


def ipc_dir() -> str:
    d = os.path.join(_data_dir(), "ak")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


class AKBridge:
    """Start / stop / command the AK holographic HUD process."""

    def __init__(self, python: Optional[str] = None):
        self.proc: Optional[subprocess.Popen] = None
        self.python = python or sys.executable
        self.cmd_id = 0
        self.dir = ipc_dir()
        self.log_path = os.path.join(self.dir, "ak.log")
        # keep the command counter monotonic across restarts, otherwise the
        # HUD process would treat new commands as already-seen ones
        try:
            queue = self._read_queue(os.path.join(self.dir, "cmd.json"))
            if queue:
                self.cmd_id = max(int(c.get("id", 0)) for c in queue)
        except Exception:
            self.cmd_id = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self, wait: float = 25.0) -> bool:
        if self.is_running():
            return True
        if not os.path.isfile(AK_UI_SCRIPT):
            return False
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            log = open(self.log_path, "a", encoding="utf-8")
        except OSError:
            log = subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(
                [self.python, AK_UI_SCRIPT],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            self.proc = None
            return False
        # wait until THIS process reports itself ready (models + camera)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.proc = None
                return False
            st = self.status()
            if st.get("running") and st.get("pid") == self.proc.pid \
                    and st.get("ready"):
                return True
            time.sleep(0.2)
        return True

    def stop(self) -> None:
        self.send("quit")
        time.sleep(0.4)
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None

    def is_running(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        st = self.status()
        if st.get("running"):
            pid = st.get("pid")
            if isinstance(pid, int):
                return _pid_alive(pid)
        return False

    # -- messaging -----------------------------------------------------------
    def send(self, cmd: str, **args) -> bool:
        self.cmd_id += 1
        payload = {"id": self.cmd_id, "cmd": cmd, "args": args,
                   "ts": time.time()}
        path = os.path.join(self.dir, "cmd.json")
        tmp = path + ".tmp"
        try:
            queue = self._read_queue(path)
            queue.append(payload)
            if len(queue) > 64:
                queue = queue[-64:]
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(queue, fh)
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    @staticmethod
    def _read_queue(path: str) -> list:
        """The command file is a list so quick commands are never lost."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return []
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    def status(self) -> dict:
        path = os.path.join(self.dir, "status.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        updated = data.get("updated") or 0
        if updated and (time.time() - updated) > 20:
            return {}          # stale -> the process died
        return data


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        alive = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(alive) and code.value == 259
    except Exception:
        return False
