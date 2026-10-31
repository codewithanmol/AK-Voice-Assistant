"""AK phone-call audio bridge.

Phone (WebRTC) -> 16 kHz PCM -> Whisper/Vosk STT -> AK engine -> edge-tts
-> 48 kHz PCM -> phone (WebRTC).

Everything here is best-effort: if edge-tts is offline it falls back to
pyttsx3, and if no STT engine loads the call still stays connected so the
user hears AK's greeting instead of silence.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
import time
from fractions import Fraction

import numpy as np

import av
from aiortc import MediaStreamTrack

SAMPLE_RATE = 48000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

STT_RATE = 16000
STT_RMS_THRESHOLD = 250.0
MIN_SPEECH_MS = 250.0
END_SILENCE_MS = 750.0
MAX_UTTERANCE_SEC = 15

DEFAULT_VOICE = "en-IN-PrabhatNeural"
GREETING = "Haan boss, boliye."
DEBUG = bool(os.environ.get("AK_CALL_DEBUG"))

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _speech_text(text: str, limit: int = 600) -> str:
    t = str(text or "")
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[*_#>|~]+", " ", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."
    return t


def _silence(samples: int = FRAME_SAMPLES) -> av.AudioFrame:
    frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
    frame.sample_rate = SAMPLE_RATE
    frame.planes[0].update(b"\x00" * (samples * 2))
    return frame


def _decode_file(path: str) -> list:
    frames = []
    container = av.open(path)
    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            return frames
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for packet in container.demux(stream):
            for frame in packet.decode():
                frames.extend(resampler.resample(frame))
        try:
            frames.extend(resampler.resample(None))
        except Exception:
            pass
    finally:
        container.close()
    return frames


def _pyttsx3_frames(text: str) -> list:
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="ak_call_")
    os.close(fd)
    pythoncom = None
    try:
        try:
            import pythoncom as _pc
            pythoncom = _pc
            pythoncom.CoInitialize()
        except Exception:
            pythoncom = None
        import pyttsx3
        engine = pyttsx3.init()
        engine.save_to_file(text, wav)
        engine.runAndWait()
        try:
            engine.stop()
        except Exception:
            pass
        return _decode_file(wav)
    except Exception as exc:  # noqa: BLE001
        print(f"[AK Call] pyttsx3 failed: {exc}")
        return []
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


async def synth_frames(text: str, voice: str = DEFAULT_VOICE) -> list:
    text = (text or "").strip()
    if not text:
        return []
    mp3 = None
    try:
        import edge_tts
        fd, mp3 = tempfile.mkstemp(suffix=".mp3", prefix="ak_call_")
        os.close(fd)
        await asyncio.wait_for(
            edge_tts.Communicate(text, voice or DEFAULT_VOICE).save(mp3), timeout=25)
        frames = _decode_file(mp3)
        if frames:
            if DEBUG:
                import numpy as _np
                mx = max(float(_np.abs(f.to_ndarray().astype(_np.int32)).max())
                         for f in frames if f.samples) if frames else 0
                print(f"[AK Call] tts '{text[:24]}' frames={len(frames)} max={mx:.0f}")
            return frames
    except Exception as exc:  # noqa: BLE001
        print(f"[AK Call] edge-tts failed: {exc}")
    finally:
        if mp3 and os.path.isfile(mp3):
            try:
                os.remove(mp3)
            except OSError:
                pass
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _pyttsx3_frames, text)


class TTSAudioTrack(MediaStreamTrack):
    """Outgoing audio track fed by a queue of decoded TTS frames."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._ts = 0
        self.speaking_until = 0.0
        self._due = 0.0
        self._late = 0.0
        self._sent = 0
        self._sent_at = 0.0

    def push(self, frames) -> None:
        total = 0
        for frame in frames:
            if frame is None or not frame.samples:
                continue
            self._queue.put_nowait(frame)
            total += frame.samples
        if total:
            self.speaking_until = max(
                self.speaking_until,
                time.monotonic() + total / float(SAMPLE_RATE))
        if DEBUG:
            print(f"[AK Call] out push frames={len(frames)} samples={total} "
                  f"queue={self._queue.qsize()}")

    @property
    def busy(self) -> bool:
        return (not self._queue.empty()) or time.monotonic() < self.speaking_until

    async def recv(self):
        try:
            frame = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            frame = _silence()
        frame.pts = self._ts
        frame.time_base = Fraction(1, SAMPLE_RATE)
        self._ts += frame.samples

        dur = frame.samples / float(SAMPLE_RATE)
        now = time.monotonic()
        if self._due == 0.0 or now - self._due > 0.25:
            self._due = now
            self._late = 0.0
        wait = self._due - now - self._late
        if wait > 0:
            await asyncio.sleep(wait)
        over = time.monotonic() - self._due
        if 0.0 <= over < 0.05:
            self._late = min(0.004, self._late * 0.9 + over * 0.1)
        self._due += dur

        self._sent += 1
        if DEBUG:
            if self._sent_at == 0.0:
                self._sent_at = now
            elif self._sent % 240 == 0:
                elapsed = time.monotonic() - self._sent_at
                audio = (self._ts / float(SAMPLE_RATE))
                if elapsed > 0:
                    print(f"[AK Call] out rate frames={self._sent} "
                          f"audio={audio:.1f}s wall={elapsed:.1f}s "
                          f"ratio={audio / elapsed:.3f} queue={self._queue.qsize()}")
        return frame


class CallSession:
    """One phone call: receives phone audio, replies with AK's voice."""

    def __init__(self, engine, chat_lock: threading.Lock | None = None,
                 voice: str = DEFAULT_VOICE, greeting: str | None = None,
                 reminders=None, session_key: str = "call"):
        self.engine = engine
        self.voice = voice or DEFAULT_VOICE
        self.out = TTSAudioTrack()
        self.closed = False
        self._lock = chat_lock or threading.Lock()
        self._stt = None
        self._stt_tried = False
        self.greeting = greeting or GREETING
        self.reminders = reminders
        self.session_key = session_key
        self.channel = None

    # ── STT ────────────────────────────────────────────────────────
    def _get_stt(self, cfg: dict):
        if self._stt_tried:
            return self._stt
        self._stt_tried = True
        voice = (cfg or {}).get("voice", {}) or {}
        try:
            from AK.stt import LocalSTT
            hotwords = voice.get("whisper_hotwords_extra") or None
            self._stt = LocalSTT(
                model_size=voice.get("whisper_model") or "tiny",
                language=voice.get("language") or "en-IN",
                cpu_threads=int(voice.get("whisper_cpu_threads") or 2),
                compute_type=voice.get("whisper_compute_type") or "int8",
                vad=bool(voice.get("whisper_vad", True)),
                beam_size=int(voice.get("whisper_beam_size") or 1),
                hotwords=hotwords,
            )
            self._stt.warmup()
            print(f"[AK Call] STT: {self._stt.name}")
            return self._stt
        except Exception as exc:  # noqa: BLE001
            print(f"[AK Call] faster-whisper init failed: {exc}; trying Vosk")
        try:
            import vosk
            from AK.stt import VoskSTT
            path = voice.get("vosk_model_path") or "models/vosk-model-small-en-us-0.15"
            if not os.path.isabs(path):
                path = os.path.join(_ROOT, path)
            self._stt = VoskSTT(vosk.Model(path))
            print("[AK Call] STT: vosk")
        except Exception as exc:  # noqa: BLE001
            print(f"[AK Call] STT unavailable: {exc}")
            self._stt = None
        return self._stt

    # ── reply ──────────────────────────────────────────────────────
    def _answer(self, text: str) -> str:
        if self.engine is None:
            return "AK engine abhi load nahi hui boss."
        try:
            with self._lock:
                reply = self.engine.respond(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[AK Call] engine error: {exc}")
            return "Sorry boss, jawab nahi mila."
        return str(reply).strip() if reply else ""

    async def greet(self) -> None:
        frames = await synth_frames(_speech_text(self.greeting), self.voice)
        if frames:
            self.out.push(frames)

    def send_event(self, payload: dict) -> None:
        channel = self.channel
        if channel is None:
            return
        try:
            channel.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print(f"[AK Call] datachannel send failed: {exc}")

    # ── phone audio -> text -> reply -> TTS ───────────────────────
    async def consume(self, track, cfg: dict) -> None:
        loop = asyncio.get_event_loop()
        stt = await loop.run_in_executor(None, self._get_stt, cfg)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=STT_RATE)
        buf = bytearray()
        trail_ms = 0.0
        speech_ms = 0.0
        max_bytes = STT_RATE * 2 * MAX_UTTERANCE_SEC
        seen = 0
        peak = 0.0
        raw_peak = 0.0

        while not self.closed:
            try:
                frame = await track.recv()
            except Exception:
                break
            seen += 1
            if DEBUG:
                raw = frame.to_ndarray()
                if raw.size:
                    raw_peak = max(raw_peak, float(np.abs(raw.astype(np.int32)).max()))
                if seen == 1:
                    print(f"[AK Call] incoming fmt={frame.format.name}/"
                          f"{frame.layout.name}@{frame.sample_rate} "
                          f"samples={frame.samples}")
            if self.out.busy:
                buf.clear()
                trail_ms = 0.0
                speech_ms = 0.0
                continue
            try:
                pieces = resampler.resample(frame)
            except Exception:
                continue
            for rf in pieces:
                if not rf.samples:
                    continue
                arr = rf.to_ndarray()
                if arr.size:
                    rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
                else:
                    rms = 0.0
                buf.extend(arr.tobytes())
                peak = max(peak, rms)
                ms = rf.samples * 1000.0 / STT_RATE
                if rms >= STT_RMS_THRESHOLD:
                    speech_ms += ms
                    trail_ms = 0.0
                else:
                    trail_ms += ms
                if len(buf) > max_bytes:
                    del buf[: len(buf) - max_bytes]
                if DEBUG and seen % 50 == 0:
                    print(f"[AK Call] mic frames={seen} raw_peak={raw_peak:.0f} "
                          f"peak_rms={peak:.0f} speech_ms={speech_ms:.0f} "
                          f"trail_ms={trail_ms:.0f} busy={self.out.busy}")
                    peak = 0.0
                    raw_peak = 0.0
                if speech_ms < MIN_SPEECH_MS or trail_ms < END_SILENCE_MS:
                    continue
                audio = bytes(buf)
                buf.clear()
                speech_ms = 0.0
                trail_ms = 0.0
                text = ""
                if stt is not None:
                    try:
                        text = await loop.run_in_executor(
                            None, stt.transcribe, audio, STT_RATE)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[AK Call] STT error: {exc}")
                text = (text or "").strip()
                if DEBUG:
                    print(f"[AK Call] STT raw='{text}' "
                          f"audio_ms={len(audio) / 2.0 / STT_RATE * 1000:.0f}")
                if not text:
                    continue
                print(f"[AK Call] Heard: {text}")
                reply = None
                event = None
                if self.reminders is not None:
                    try:
                        reply, payload = self.reminders.handle(
                            text, key=self.session_key)
                        event = payload
                    except Exception as exc:  # noqa: BLE001
                        print(f"[AK Call] reminder error: {exc}")
                if not reply:
                    reply = await loop.run_in_executor(None, self._answer, text)
                if not reply:
                    continue
                print(f"[AK Call] Reply: {reply}")
                spoken = _speech_text(reply)
                if not spoken:
                    continue
                if event:
                    rec = event.get("reminder")
                    if rec:
                        self.send_event({"type": "reminder_created",
                                         "reminder": rec})
                    elif "cancelled" in event:
                        self.send_event({"type": "reminder_cancelled",
                                         "id": event["cancelled"]})
                if DEBUG and spoken != reply:
                    print(f"[AK Call] Speak: {spoken}")
                frames = await synth_frames(spoken, self.voice)
                if frames and not self.closed:
                    self.out.push(frames)
