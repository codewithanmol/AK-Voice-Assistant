"""AK Caption Bus — ultra-low-latency LOCAL caption IPC.

Pushes interim STT words to the visualizer with sub-millisecond transport
overhead. No cloud, no websockets dependency — a tiny loopback UDP bus plus a
JSON-file fallback (fire-and-forget). One subscriber (visualizer) binds;
producers (STT workers / main.py / demo) just publish.

Protocol (JSON datagrams / file rows):
  {"e": "partial", "text": "radhe radhe boss"}   # provisional utterance text
  {"e": "final",   "text": "radhe radhe boss"}   # committed utterance
  {"e": "reset"}                                 # utterance boundary -> fade
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 46321
_MAX_DATAGRAM = 8192


def publish(msg: dict, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """Fire-and-forget UDP publish. Safe to call from any thread."""
    try:
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8", "replace")
        if len(payload) > _MAX_DATAGRAM:
            payload = payload[:_MAX_DATAGRAM]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(payload, (host, port))
        finally:
            s.close()
        return True
    except Exception:  # noqa: BLE001
        return False


class CaptionSubscriber:
    """Binds loopback UDP and turns caption datagrams into a thread-safe queue."""

    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1"):
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._port

    def _loop(self) -> None:
        self._sock.settimeout(0.4)
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(_MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8", "replace"))
                if isinstance(msg, dict):
                    self._q.put_nowait(msg)
            except Exception:  # noqa: BLE001
                continue

    def poll(self) -> "dict | None":
        """Return the next caption event, or None (non-blocking)."""
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass