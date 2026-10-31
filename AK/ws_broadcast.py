"""
AK WebSocket Broadcast Layer
----------------------------
Lightweight, non-blocking WebSocket server for Windhawk Dynamic Island frontend.
Runs on ws://127.0.0.1:8080 alongside the main terminal assistant.
Purely outward broadcaster - never touches terminal/UI logic.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, Set

try:
    from websockets.server import serve, WebSocketServerProtocol
except Exception as e:
    raise RuntimeError(
        "Missing dependency: 'websockets'. Install with: pip install websockets"
    ) from e


_HOST = "127.0.0.1"
_PORT = 8080

_clients: Set[WebSocketServerProtocol] = set()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()


def _now_iso() -> str:
    try:
        return datetime.now().isoformat()
    except Exception:
        return time.strftime("%Y-%m-%dT%H:%M:%S")


async def _handler(ws: WebSocketServerProtocol):
    _clients.add(ws)
    print(f"[WS-BROADCAST] Client connected | total: {len(_clients)}")
    try:
        # Keep connection alive; we only broadcast out
        async for _ in ws:
            pass
    except Exception:
        pass
    finally:
        _clients.discard(ws)
        print(f"[WS-BROADCAST] Client disconnected | total: {len(_clients)}")


async def _serve():
    print(f"[WS-BROADCAST] Starting WebSocket server on ws://{_HOST}:{_PORT}")
    async with serve(_handler, _HOST, _PORT, ping_interval=20, ping_timeout=20):
        print(f"[WS-BROADCAST] LIVE | ws://{_HOST}:{_PORT} | awaiting Windhawk clients...")
        await asyncio.Future()  # run forever


def start() -> bool:
    global _loop, _thread, _started

    with _lock:
        if _started and _thread and _thread.is_alive():
            print("[WS-BROADCAST] Already running on ws://127.0.0.1:8080")
            return True
        _started = True

    def _runner():
        global _loop
        try:
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
            _loop.run_until_complete(_serve())
        except Exception as e:
            print(f"[WS-BROADCAST][ERR] Server crashed: {e}")
        finally:
            try:
                if _loop and not _loop.is_closed():
                    _loop.close()
            except Exception:
                pass
            print("[WS-BROADCAST] Server loop ended")

    _thread = threading.Thread(target=_runner, name="AK-WS-Broadcast", daemon=True)
    _thread.start()
    time.sleep(0.4)
    print("[WS-BROADCAST] Background thread started | Non-blocking | Terminal untouched")
    return True


def is_running() -> bool:
    return bool(_thread and _thread.is_alive() and _loop is not None)


def client_count() -> int:
    return len(_clients)


def broadcast(sender: str = "system", message: Any = "") -> None:
    if message is None:
        return
    text = str(message)
    if not text.strip():
        return
    if _loop is None or not is_running():
        return

    payload: Dict[str, Any] = {
        "sender": sender or "system",
        "message": text,
        "timestamp": _now_iso(),
    }
    data = json.dumps(payload, ensure_ascii=False)

    async def _broadcast_all():
        if not _clients:
            return
        dead = []
        for ws in list(_clients):
            try:
                await ws.send(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)

    try:
        asyncio.run_coroutine_threadsafe(_broadcast_all(), _loop)
    except Exception as e:
        print(f"[WS-BROADCAST][WARN] broadcast skipped: {e}")
