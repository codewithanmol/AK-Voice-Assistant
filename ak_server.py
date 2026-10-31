"""
AK Mobile Server — phone ka bridge jo laptop AK se baat karta hai.

REST API:
  GET  /health           → { "ok": true, "name": "AK", "status": "online" }
  POST /chat  {"message":"..."}  → { "reply": "..." }

Auth: HTTP Basic (username: ak, password: <config/brain/mobile_token ya aapka password>)

Run:  python ak_server.py  [--port 4097] [--password SECRET]
Phone: open the app settings and enter this machine's LAN IP, e.g.
       http://192.168.1.5:4097, plus the server password.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import secrets
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Ensure project root (with AK package + config.json) is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
for _root in (_HERE, os.path.dirname(_HERE)):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from AK import load_config, TaskEngine, Brain  # noqa: E402
from AK.myvoice import MyVoiceClient  # noqa: E402
from ak_reminders import REMINDERS  # noqa: E402

# WebRTC call support
try:
    import asyncio
    import ssl
    from aiohttp import web
    from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
    WEBRTC_AVAILABLE = True
except Exception as e:
    print(f"[AK server] WebRTC not available: {e}")
    WEBRTC_AVAILABLE = False

PORT = 4097
PASSWORD = ""

_TTS_CLIENT = None

# Full local-first AK engine (skills → memory → wiki → chat → engine).
# Built lazily so a failure never kills the HTTP server.
_ENGINE = None


def _make_engine():
    """Return a headless, text-only AK engine, or None on failure."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(
            "ak_main_engine", os.path.join(_HERE, "main.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        engine = mod.AK()
    except Exception as exc:  # noqa: BLE001
        print(f"[AK server] engine init failed: {exc}")
        return None
    # Text-only mode: koi speaker/visualizer GUI/GUI cleanup nahi chahiye
    # server ko — phone pehle local AK reply de, phir engine pe.
    engine.typing_mode = True
    engine._from_server = True          # phone unlock = gate via voice_unlock.allow_phone
    engine._viz_state = lambda *a, **k: None
    engine._viz_custom = lambda *a, **k: None
    engine._maybe_cleanup_background = lambda *a, **k: None
    print("[AK server] Local-first AK engine ON: skills→memory→wiki→chat→engine")
    return engine

# --- App auto-pairing (phone) -------------------------------------------
# Phone per-install token (Bearer) — password phone pe kabhi nahi dikhta.
# Files live under security/ (owner-restricted folder).
_SEC_HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "security")
_APP_ALLOW = os.path.join(_SEC_HERE, "app_allow.json")
_PAIR_ALLOW = os.path.join(_SEC_HERE, "pair.allow")
_allow: dict = {}
_pairing_until = 0.0


def _in_tailnet(addr) -> bool:
    try:
        ip = ipaddress.ip_address(addr[0])
        return ip in ipaddress.ip_network("100.64.0.0/10")
    except Exception:
        return False


def _load_allow() -> None:
    global _allow  # noqa: PLW0603
    try:
        if os.path.isfile(_APP_ALLOW):
            with open(_APP_ALLOW, "r", encoding="utf-8") as fh:
                _allow = json.load(fh) or {}
    except Exception:
        _allow = {}


def _save_allow() -> None:
    try:
        tmp = _APP_ALLOW + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_allow, fh)
        os.replace(tmp, _APP_ALLOW)
    except Exception:
        pass


def _bearer_token(hdrs) -> str:
    header = hdrs.get("Authorization", "")
    if not header.startswith("Bearer "):
        return ""
    return header[7:].strip()


def _get_tts_client(cfg: dict) -> MyVoiceClient:
    global _TTS_CLIENT  # noqa: PLW0603
    if _TTS_CLIENT is None:
        _TTS_CLIENT = MyVoiceClient(cfg)
    return _TTS_CLIENT


def _warm_tts(cfg: dict) -> None:
    """Background: load the XTTS clone model once at boot so the phone's
    first TTS request is fast (no 2-4 min cold start)."""
    import time
    time.sleep(2)
    try:
        data_dir = cfg.get("data_path") or r"D:\YOUR_DATA_PATH"
        warm = os.path.join(data_dir, "_warmup.wav")
        client = _get_tts_client(cfg)
        if client.available and client.enrolled:
            client.speak("Radhe Radhe", warm)
            print("[AK server] My Voice model warm ho gaya — phone TTS fast.")
    except Exception as exc:  # noqa: BLE001
        print(f"[AK server] warm-up skip: {exc}")


def _get_password(cfg: dict) -> str:
    """Password order: CLI flag > config brain (vault-injected) > vault server_secret."""
    global PASSWORD  # noqa: PLW0603
    if PASSWORD:
        return PASSWORD
    b = cfg.get("brain", {})
    for key in ("mobile_token", "password", "token", "server_secret"):
        val = (b.get(key) or "").strip()
        if val:
            return val
    try:
        from AK import vault as _vault
        val = (_vault.get("server_secret") or "").strip()
        if val:
            return val
    except Exception:  # noqa: BLE001
        pass
    secret = os.path.join(_SEC_HERE, "server_secret.txt")
    try:
        if os.path.isfile(secret):
            with open(secret, "r", encoding="utf-8-sig") as fh:
                val = (fh.read() or "").strip()
            if val and not val.lstrip().startswith("#"):
                return val
    except Exception:  # noqa: BLE001
        pass
    # First run: generate a strong password and persist it (no hardcoded
    # default). The file is git-ignored and readable only by the owner.
    try:
        os.makedirs(_SEC_HERE, exist_ok=True)
        val = secrets.token_urlsafe(12)
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write(val + "\n")
        print(f"[AK server] Naya password ban gaya (yahan padho): {secret}")
        print(f"[AK server] Password: {val}")
        return val
    except Exception:  # noqa: BLE001
        return ""


class AKHandler(BaseHTTPRequestHandler):
    agent: TaskEngine = None
    brain: Brain = None
    password: str = ""
    engine = None
    lock = threading.Lock()

    # ---- helpers -------------------------------------------------------
    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if not self.password:
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Bearer "):
            token = hdr[7:].strip()
            return token != "" and token in _allow.values()
        if not hdr.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(hdr[6:]).decode("utf-8", "replace")
        except Exception:
            return False
        username, _, pw = decoded.partition(":")
        return pw == self.password

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def log_message(self, *args):  # keep console clean
        pass

    # ---- routes --------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "name": "AK Mobile Server", "status": "online"})
        elif path == "/api/reminders":
            if not self._authed():
                self._send(401, {"ok": False, "error": "unauthorized"})
                return
            self._send(200, {"ok": True, "reminders": REMINDERS.upcoming()})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/pair":
            self._handle_pair()
            return
        if p == "/api/tts":
            self._handle_tts()
            return
        if p != "/chat":
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._authed():
            self._send(401, {"ok": False, "error": "unauthorized - password sahi daalo"})
            return
        data = self._read_json()
        message = (data.get("message") or "").strip()
        if not message:
            self._send(400, {"ok": False, "error": "message empty"})
            return

        workdir = (data.get("workdir") or "").strip()
        extra: dict = {}
        try:
            rem_reply, rem_payload = REMINDERS.handle(message, key="chat")
            if rem_payload:
                if rem_payload.get("reminder"):
                    extra["reminder"] = rem_payload["reminder"]
                if "cancelled" in rem_payload:
                    extra["cancelled"] = rem_payload["cancelled"]
            with AKHandler.lock:
                reply = rem_reply
                # LOCAL-FIRST: full AK engine — skills, memory, wiki, casual
                # chat phle local/groq se; real tasks phir engine pe.
                if not reply and AKHandler.engine is not None:
                    try:
                        reply = AKHandler.engine.respond(message)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[AK server] engine error: {exc}")
                        reply = None
                # Fallback (sirf agar engine unavailable ho): brain, to engine
                if not reply:
                    if AKHandler.brain:
                        reply = AKHandler.brain.think(message)
                    if not reply or "I don't have a language model" in reply or "I'm not sure" in reply:
                        if AKHandler.agent and AKHandler.agent.available:
                            reply = AKHandler.agent.execute_task(message, workdir=workdir)
                if not reply:
                    reply = "AK abhi kuch samajh nahi paayi."
            payload = {"ok": True, "reply": reply}
            payload.update(extra)
            self._send(200, payload)
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "reply": f"AK error: {exc}"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    # ---- /api/pair (phone auto-setup) --------------------------------
    def _handle_pair(self):
        if not self.password:
            self._send(409, {"ok": False, "error": "pairing needs a server password"})
            return
        data = self._read_json()
        src = self.client_address[0]
        supplied = (data.get("password") or "").strip()
        trusted = _in_tailnet(self.client_address) or src in ("127.0.0.1", "::1")
        if not trusted and not secrets.compare_digest(supplied, self.password):
            self._send(403, {"ok": False, "error": "pairing needs the server password"})
            return
        install_id = (data.get("install_id") or "").strip()
        if not install_id or len(install_id) > 128:
            self._send(400, {"ok": False, "error": "install_id missing"})
            return
        if install_id in _allow:
            token = _allow[install_id]
        else:
            token = "ak_" + secrets.token_hex(16)
            _allow[install_id] = token
            _save_allow()
        print(f"[AK server] Pairing: {install_id[:24]}... <- {src} (token issued)")
        self._send(200, {"ok": True, "token": token})

    # ---- /api/tts ---------------------------------------------------
    def _handle_tts(self):
        if not self._authed():
            self._send(401, {"ok": False, "error": "unauthorized - password sahi daalo"})
            return
        data = self._read_json()
        text = (data.get("text") or "").strip()
        if not text:
            self._send(400, {"ok": False, "error": "text empty"})
            return
        cfg = load_config()
        client = _get_tts_client(cfg)
        if not client.available:
            self._send(503, {"ok": False,
                             "error": "My Voice engine (venv312) missing on laptop"})
            return
        if not client.enrolled:
            self._send(404, {"ok": False,
                             "error": "No voice recorded yet — laptop pe "
                                      "'record my voice' kaho"})
            return
        data_dir = cfg.get("data_path") or r"D:\YOUR_DATA_PATH"
        out = os.path.join(data_dir, "tts_last.wav")
        try:
            ok, err = client.speak(text, out)
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": "tts: %s" % exc})
            return
        if not ok or not os.path.isfile(out):
            self._send(500, {"ok": False, "error": err or "tts failed"})
            return
        try:
            with open(out, "rb") as fh:
                body = fh.read()
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(exc)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    global PASSWORD, _pairing_until  # noqa: PLW0603
    parser = argparse.ArgumentParser(description="AK Mobile bridge server")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--password", default="")
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    cfg = load_config()
    PASSWORD = args.password or _get_password(cfg)

    if not args.password:
        if AKHandler.password:
            print("[AK server] Password: set (secret file se — console pe nahi dikhaya)")
        else:
            print("[AK server] Password: (none - anyone can ask AK)")

    _load_allow()
    if _allow:
        print(f"[AK server] Paired phones: {len(_allow)} (app_allow.json)")
    _pairing_until = time.time() + 6 * 3600
    if os.path.isfile(_PAIR_ALLOW):
        print("[AK server] Pairing: pair.allow file mila — pairing hamesha khola hua hai")
    else:
        print("[AK server] Pairing window: 6 hrs (boot se). Phone install/first-run yahi auto-pair hoga.")

    agent = TaskEngine(cfg)
    if not agent.available:
        print("[AK server] WARNING: engine binary nahi mila — AK chat kaam nahi karegi.")
    else:
        print(f"[AK server] Engine: {agent.version}")

    # Local brain (rule-based) — pehle try, phir engine fallback
    brain = Brain(cfg)
    print(f"[AK server] Brain: {brain.backend} mode")

    AKHandler.agent = agent
    AKHandler.brain = brain
    AKHandler.password = PASSWORD

    # Local-first full AK engine (optional wrapper around main.py's AK class).
    AKHandler.engine = _make_engine()

    # Phone ke liye: model ko abhi background me load karwa lo (warm) —
    # taaki pehli TTS bhi turant aaye, kabhi 2-4 min wait na karna pade.
    threading.Thread(target=_warm_tts, args=(cfg,), daemon=True).start()

    # Start WebRTC call server if available
    if WEBRTC_AVAILABLE:
        threading.Thread(target=_start_call_server, args=(args.port + 1, cfg), daemon=True).start()

    host_ip = _lan_ip()
    ts_ip = _tailscale_ip()
    print(f"[AK server] Apna server ready: http://{host_ip}:{args.port}")
    print("[AK server] Call: GET /health | POST /chat {\"message\":\"...\"} | POST /api/tts {\"text\":\"...\"}")
    if ts_ip:
        print(f"[AK server] Tailscale (remote / college): http://{ts_ip}:{args.port}")
    if WEBRTC_AVAILABLE:
        print(f"[AK server] WebRTC Call (same Wi-Fi): https://{host_ip}:{args.port+1}/call")
        if ts_ip:
            print(f"[AK server] WebRTC Call (remote / college): https://{ts_ip}:{args.port+1}/call")
    print("[AK server] Phone app me yehi URL daalo aur password bhi. Ctrl+C cancel.")

    server = ThreadingHTTPServer((args.host, args.port), AKHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[AK server] Band kiya. Alvida boss! 👋")


def _lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _tailscale_ip() -> str:
    """Laptop ka Tailscale 100.x IP (college/remote access ke liye)."""
    import subprocess
    try:
        exe = r"C:\Program Files\Tailscale\tailscale.exe"
        if not os.path.isfile(exe):
            exe = "tailscale"
        out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                             text=True, timeout=4)
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("100."):
                return line
    except Exception:
        pass
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            if "tailscale" not in name.lower():
                continue
            for addr in addrs:
                if getattr(addr.family, "name", "") == "AF_INET" and addr.address.startswith("100."):
                    return addr.address
    except Exception:
        pass
    return ""


# WebRTC Call Server (phone <-> AK two-way voice)
if WEBRTC_AVAILABLE:
    from ak_call_audio import CallSession

    _call_pcs = {}
    _call_sessions = {}
    _call_tokens = {}
    _call_reminders = {}
    _CALL_CERT = os.path.join(_HERE, "certs", "https_cert.pem")
    _CALL_KEY = os.path.join(_HERE, "certs", "https_key.pem")

    def _token_ok(token: str) -> bool:
        if not PASSWORD:
            return True
        if not token:
            return False
        return (token == PASSWORD
                or token in _allow.values()
                or _call_tokens.get(token, 0) > time.time())

    def _new_call_token() -> str:
        token = secrets.token_urlsafe(18)
        now = time.time()
        for old, exp in list(_call_tokens.items()):
            if exp <= now:
                _call_tokens.pop(old, None)
        _call_tokens[token] = now + 3600
        return token

    async def _wait_ice(pc, timeout: float = 3.0) -> None:
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while pc.iceGatheringState != "complete" and loop.time() < end:
            await asyncio.sleep(0.05)

    async def _drop_call(session_id):
        session = _call_sessions.pop(session_id, None)
        pc = _call_pcs.pop(session_id, None)
        if session is not None:
            session.closed = True
            for attr in ("task", "greet_task"):
                task = getattr(session, attr, None)
                if task is not None:
                    task.cancel()
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass

    async def _call_index(request):
        try:
            with open(os.path.join(_HERE, 'call', 'index.html'), 'r', encoding='utf-8') as f:
                html = f.read()
            return web.Response(text=html, content_type='text/html')
        except Exception as e:
            return web.Response(text=f'Error: {e}', status=500)

    async def _call_offer(request):
        try:
            auth = request.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.startswith("Bearer ") else ""
            if not _token_ok(token):
                return web.json_response({'error': 'unauthorized call token'}, status=401)

            data = await request.json()
            session_id = data.get('session_id') or str(uuid.uuid4())
            offer = RTCSessionDescription(sdp=data['sdp']['sdp'], type=data['sdp']['type'])
            print(f"[AK Call] offer {session_id[:8]} from {request.remote} "
                  f"({len(offer.sdp)} bytes)")

            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            reminder_id = _call_reminders.pop(session_id, "")
            reminder = REMINDERS.due_for_call(reminder_id) if reminder_id else None
            greeting = None
            if reminder:
                greeting = (reminder.get("speak")
                            or f"Boss, yaad dilana tha: {reminder.get('title', '')}.")
                print(f"[AK Call] reminder call: {reminder.get('title', '')}")
            session = CallSession(
                AKHandler.engine, AKHandler.lock,
                greeting=greeting,
                reminders=REMINDERS,
                session_key=f"call:{session_id}",
            )
            _call_pcs[session_id] = pc
            _call_sessions[session_id] = session

            @pc.on("datachannel")
            def on_datachannel(channel):
                print(f"[AK Call] datachannel: {channel.label}")
                session.channel = channel

            @pc.on("connectionstatechange")
            async def on_connectionstatechange():
                print(f"[AK Call] {session_id} state: {pc.connectionState}")
                if pc.connectionState == "connected" and not getattr(session, "greeted", False):
                    session.greeted = True
                    session.greet_task = asyncio.ensure_future(session.greet())
                if pc.connectionState in ("failed", "closed"):
                    await _drop_call(session_id)

            @pc.on("track")
            def on_track(track):
                print(f"[AK Call] Track received: {track.kind}")
                if track.kind == "audio":
                    session.task = asyncio.ensure_future(
                        session.consume(track, load_config()))

            await pc.setRemoteDescription(offer)
            print(f"[AK Call] {session_id[:8]} remote description set")

            # AK -> phone: ride AK's voice on the audio transceiver the phone
            # already offered (an extra m-line would make the answer invalid).
            attached = False
            for transceiver in pc.getTransceivers():
                if getattr(transceiver, "kind", None) != "audio":
                    continue
                try:
                    transceiver.sender.replaceTrack(session.out)
                    transceiver.direction = "sendrecv"
                    attached = True
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"[AK Call] replaceTrack failed: {exc}")
            if not attached:
                pc.addTrack(session.out)
            print(f"[AK Call] {session_id[:8]} outgoing audio attached={attached}")

            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            print(f"[AK Call] {session_id[:8]} answer ready")
            await _wait_ice(pc)

            return web.json_response({
                'session_id': session_id,
                'answer': {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}
            })
        except Exception as e:
            print(f"[AK Call] Error: {e}")
            traceback.print_exc()
            return web.json_response({'error': str(e)}, status=500)

    async def _call_trigger(request):
        try:
            auth = request.headers.get("Authorization", "")
            token_hdr = auth[7:].strip() if auth.startswith("Bearer ") else ""
            src = request.remote or ""
            if not (src in ("127.0.0.1", "::1") or _token_ok(token_hdr)):
                return web.json_response({'error': 'unauthorized'}, status=401)
            try:
                data = await request.json()
            except Exception:
                data = {}
            reminder_id = str((data or {}).get("reminder_id") or "").strip()
            reminder_title = ""
            if reminder_id:
                rec = REMINDERS.store.get(reminder_id)
                reminder_title = rec.get("title", "")
            session_id = str(uuid.uuid4())
            if reminder_id:
                _call_reminders[session_id] = reminder_id
            token = _new_call_token()
            host = request.host.split(':')[0]
            port = request.url.port or 4098
            url = f"https://{host}:{port}/call?session={session_id}&token={token}"
            return web.json_response({
                'ok': True,
                'session_id': session_id,
                'call_url': url,
                'reminder_id': reminder_id,
                'reminder_title': reminder_title,
                'message': 'Open this URL on phone to answer call'
            })
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    def _start_call_server(port, cfg):
        try:
            app = web.Application()
            app.router.add_get('/call', _call_index)
            app.router.add_post('/api/call/offer', _call_offer)
            app.router.add_post('/api/call/trigger', _call_trigger)
            app.router.add_get('/', _call_index)

            ssl_ctx = None
            if os.path.isfile(_CALL_CERT) and os.path.isfile(_CALL_KEY):
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(_CALL_CERT, _CALL_KEY)
                scheme = "https"
            else:
                scheme = "http"
                print("[AK Call] WARNING: cert missing - HTTP mode, phone mic "
                      "may be blocked (run scripts/gen_https_cert.py)")

            print(f"[AK Call] WebRTC server: {scheme}://0.0.0.0:{port}/call")
            web.run_app(app, host='0.0.0.0', port=port,
                        ssl_context=ssl_ctx, print=None)
        except Exception as e:
            print(f"[AK Call] Failed to start: {e}")


if __name__ == "__main__":
    main()