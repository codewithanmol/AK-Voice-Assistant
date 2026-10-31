"""My Voice client — pairs AK (main process) with the XTTS helper server.

The heavy clone model runs in a SEPARATE helper process (the D-drive
venv312 python), so the main AK stays light and the server frees RAM on
its own when idle.  When nothing is listening the client just restarts it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Optional

_SRV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "myvoice_srv.py")


class MyVoiceClient:
    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        myv = cfg.get("myvoice", {}) or {}
        self.data_dir = cfg.get("data_path") or r"D:\YOUR_DATA_PATH"
        self.ref_wav = os.path.join(self.data_dir, "myvoice_ref.wav")
        self.python = (myv.get("python")
                       or r"D:\YOUR_DATA_PATH\venv312\Scripts\python.exe")
        self.threads = int(myv.get("threads", 2))
        self.idle = int(myv.get("unload_after", 180))
        self.language = myv.get("language", "hi")
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return os.path.isfile(self.python) and os.path.isfile(_SRV)

    @property
    def enrolled(self) -> bool:
        return os.path.isfile(self.ref_wav)

    def _start(self) -> None:
        env = dict(os.environ)
        env["HF_HOME"] = os.path.join(self.data_dir, "models", "hf")
        env["AK_MYVOICE_IDLE"] = str(self.idle)
        env["AK_MYVOICE_THREADS"] = str(self.threads)
        self._proc = subprocess.Popen(
            [self.python, "-u", _SRV],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", env=env, creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _req(self, req: dict, timeout: float = 900.0) -> dict:
        with self._lock:
            for _retry in (0, 1):
                if self._proc is None or self._proc.poll() is not None:
                    self._start()
                try:
                    self._proc.stdin.write(json.dumps(req) + "\n")
                    self._proc.stdin.flush()
                    line = self._proc.stdout.readline()
                    resp = json.loads(line) if line else {"ok": False,
                                                          "err": "no response"}
                    if resp.get("ok") or _retry:
                        return resp
                except Exception as exc:  # noqa: BLE001
                    resp = {"ok": False, "err": str(exc)}
                self._kill()
            return resp

    def ping(self) -> bool:
        try:
            return bool(self._req({"cmd": "ping"}, timeout=30).get("pong"))
        except Exception:
            return False

    def speak(self, text: str, out_path: str):
        if not self.available:
            return False, "My-voice model not installed at %s" % self.python
        if not self.enrolled:
            return False, "Record your voice first — say 'record my voice'."
        resp = self._req({"cmd": "tts", "text": text, "out": out_path,
                          "ref": self.ref_wav, "lang": self.language})
        if resp.get("ok") and os.path.isfile(out_path):
            return True, ""
        return False, resp.get("err", "unknown TTS error")

    def kill(self) -> None:
        with self._lock:
            self._kill()

    def _kill(self) -> None:
        try:
            if self._proc is not None:
                if self._proc.poll() is None:
                    try:
                        self._proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                        self._proc.stdin.flush()
                    except Exception:
                        pass
                    self._proc.terminate()
        except Exception:
            pass
        self._proc = None


_client = None


def get_client(cfg: Optional[dict] = None) -> MyVoiceClient:
    global _client
    if _client is None:
        _client = MyVoiceClient(cfg)
    return _client