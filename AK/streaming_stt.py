"""StreamingSTT — 100% local, offline, word-by-word Vosk speech-to-text.

Captures audio in ~64ms chunks on a dedicated thread, feeds KaldiRecognizer as
fast as audio arrives (no "record-then-transcribe" gaps), and emits:
    on_word(words)      -> list of newly appeared provisional words
    on_partial(text)    -> full provisional utterance text
    on_final(text)      -> committed utterance (after silence/VAD final)
    on_reset()          -> utterance boundary (pending words should snap/fade)
    on_level(rms)       -> input level 0..1 (for mic meters)

Fully offline. Optional UDP publish so other processes (the visualizer) can
subscribe via AK.caption_bus while it runs in another thread/process.
"""

from __future__ import annotations

import io
import queue
import threading
import time
import wave

_SAMPLE_RATE = 16000
_CHANNELS = 1
_DTYPE = "int16"
_DEFAULT_BLOCK = 1024  # 64ms at 16 kHz


def _ak_import(name):
    """Import an AK submodule whether we run as a script or as a package."""
    try:
        return __import__("AK." + name, fromlist=[name])
    except ImportError:
        try:
            return __import__(name)
        except ImportError:
            raise


class StreamingSTT:
    def __init__(self, model_path: str, block: int = _DEFAULT_BLOCK,
                 device: "int | None" = None,
                 on_word=None, on_partial=None, on_final=None,
                 on_reset=None, on_level=None,
                 publish_udp: bool = False, udp_port: int = 0):
        import numpy as np
        from vosk import KaldiRecognizer, Model

        self._np = np
        self._model = Model(model_path)
        self._rec = KaldiRecognizer(self._model, _SAMPLE_RATE)
        self._rec.SetWords(True)
        self._block = block
        self._device = device
        self._on_word = on_word
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_reset = on_reset
        self._on_level = on_level

        self._publish = publish_udp
        self._port = udp_port
        self._last_partial = ""
        self._running = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._stream = None
        self._started = time.time()

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        """Open the mic and begin streaming (returns immediately)."""
        import sounddevice as sd

        if self._running.is_set():
            return
        self._running.set()
        self._stream = sd.RawInputStream(
            samplerate=_SAMPLE_RATE, channels=_CHANNELS, dtype=_DTYPE,
            blocksize=self._block, device=self._device,
            callback=self._feed,
        )
        self._stream.start()

    def stop(self) -> None:
        self._running.clear()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self._stream = None

    def reset(self) -> None:
        """Force an utterance boundary (call before dictating fresh text)."""
        self._emit_reset()

    # ------------------------------------------------------------ callbacks
    def _feed(self, indata, frames, time_info, status) -> None:
        """Called by sounddevice on the audio thread. Keep this tight."""
        if not self._running.is_set():
            return
        if status:
            return
        data = bytes(indata)
        arr = self._np.frombuffer(data, dtype=self._np.int16)
        try:
            rms = float(self._np.sqrt(self._np.mean((arr.astype(self._np.float32) / 32768.0) ** 2)))
        except Exception:  # noqa: BLE001
            rms = 0.0
        if self._on_level:
            self._on_level(min(rms * 4.0, 1.0))
        try:
            if self._rec.AcceptWaveform(data):
                self._handle_final(self._rec.Result())
            else:
                text = self._text_of(self._rec.PartialResult())
                self._handle_partial(text)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ internals
    @staticmethod
    def _text_of(result: str) -> str:
        import json

        try:
            return (json.loads(result).get("text") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _handle_partial(self, text: str) -> None:
        if not text:
            return
        old = self._last_partial.split()
        new = text.split()
        # Emit only the tokens that are new relative to the previous partial.
        added: "list[str]" = []
        for i, tok in enumerate(new):
            if i < len(old) and tok == old[i]:
                continue
            added.append(tok)
        if self._on_word and added:
            self._on_word(added)
        if self._on_partial:
            self._on_partial(text)
        # Publish the FULL provisional text on the bus — receivers replace
        # their rolling window with it (word-by-word subtitle behaviour).
        if self._publish:
            self._udp({"e": "partial", "text": text})
        self._last_partial = text

    def _handle_final(self, result: str) -> None:
        text = self._text_of(result)
        self._last_partial = ""
        if self._on_final and text:
            self._on_final(text)
        if self._publish:
            self._udp({"e": "final", "text": text} if text else {"e": "reset"})
        if self._on_reset:
            self._on_reset()
        if self._publish and not text:
            pass

    def _emit_reset(self) -> None:
        self._last_partial = ""
        if self._on_reset:
            self._on_reset()
        if self._publish:
            self._udp({"e": "reset"})

    def _udp(self, msg: dict) -> None:
        try:
            _bus = _ak_import("caption_bus")
            _bus.publish(msg, port=self._port or _bus.DEFAULT_PORT)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------- wav playback
    @classmethod
    def from_wav(cls, wav_path: str, model_path: str, **kw) -> tuple:
        """Feed an existing WAV (16k mono) instead of the mic — for testing.

        Returns (engine_started_ok_bool, events_list) where events_list is a
        list of ("partial"|"final"|"reset", text). Stops automatically at EOF.
        """
        import os
        import sys

        wf = wave.open(wav_path, "rb")
        sr = wf.getframerate()
        ch = wf.getnchannels()
        events: "list[tuple]" = []

        def on_partial(t):
            events.append(("partial", t))

        def on_final(t):
            events.append(("final", t))

        def on_reset():
            events.append(("reset", ""))

        engine = cls(model_path, publish_udp=False, on_partial=on_partial,
                     on_final=on_final, on_reset=on_reset)
        if sr != _SAMPLE_RATE or ch != _CHANNELS:
            wf.close()
            return False, [("error", "WAV must be 16 kHz mono")]

        chunk = engine._block * 2 * _CHANNELS  # 16-bit frames
        while True:
            data = wf.readframes(engine._block)
            if not data:
                break
            arr = engine._np.frombuffer(data, dtype=engine._np.int16)
            rms = float(engine._np.sqrt(engine._np.mean((arr.astype(engine._np.float32) / 32768.0) ** 2)))
            if engine._on_level:
                engine._on_level(min(rms * 4.0, 1.0))
            if engine._rec.AcceptWaveform(bytes(data)):
                engine._handle_final(engine._rec.Result())
            else:
                engine._handle_partial(engine._text_of(engine._rec.PartialResult()))
        engine._handle_final(engine._rec.FinalResult())
        wf.close()
        return True, events


def _live(args) -> None:
    """CLI: AK/streaming_stt.py --mic [model] — print word-by-word output."""
    import argparse
    import os
    import sys

    ap = argparse.ArgumentParser("ak-stream-stt")
    ap.add_argument("model", nargs="?", help="path to vosk model dir")
    args = ap.parse_args()
    model = args.model or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "models",
        "vosk-model-small-en-us-0.15")
    if not os.path.isdir(model):
        print(f"model not found: {model}")
        sys.exit(2)
    print(f"[streaming_stt] model: {model}")
    print("[streaming_stt] speaking... (Ctrl+C to stop)")

    def on_word(words):
        print("+", " ".join(words), flush=True)

    def on_final(text):
        print(f"FINAL: {text}", flush=True)

    engine = StreamingSTT(model, on_word=on_word, on_final=on_final,
                          publish_udp=True)
    engine.start()
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        engine.stop()
        print("\\n[streaming_stt] stopped.")


if __name__ == "__main__":
    _live(None)