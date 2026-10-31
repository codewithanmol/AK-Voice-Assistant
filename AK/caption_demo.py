"""AK Caption Demo — mic test for the local word-by-word STT pipeline.

NOTE: the AK visualizer runs live captions automatically at AK start
(main.py -> _start_visualizer -> in-process vosk). This demo is just a
console check that the microphone + Vosk engine produce interim words.

Usage:
    python AK\\caption_demo.py                 # console only (diagnostic)
    python AK\\caption_demo.py --udp           # also publish to an open visualizer
    python AK\\caption_demo.py --model <dir>   # custom vosk model
    python AK\\caption_demo.py --ui            # also open a visualizer window
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time


def _ak_import(name):
    """Import an AK submodule whether we run as a script or as a package."""
    try:
        return __import__("AK." + name, fromlist=[name])
    except ImportError:
        try:
            return __import__(name)
        except ImportError:
            raise


def _model_default() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "models", "vosk-model-small-en-us-0.15")


def main() -> None:
    ap = argparse.ArgumentParser(description="AK caption demo (local STT)")
    ap.add_argument("--ui", action="store_true",
                    help="also open the visualizer window")
    ap.add_argument("--model", default=_model_default(),
                    help="path to the vosk model directory")
    ap.add_argument("--udp", action="store_true",
                    help="publish interim words to the caption bus (an open "
                         "visualizer will show them)")
    ap.add_argument("--port", type=int, default=0,
                    help="UDP port (default: caption_bus default)")
    args = ap.parse_args()

    if not os.path.isdir(args.model):
        print(f"[caption_demo] model not found: {args.model}")
        sys.exit(2)

    viz_proc = None
    if args.ui:
        here = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(here, "visualizer_py.py")
        # AK_CAP=0 -> visualizer does NOT open its own vosk here; this demo
        # drives the captions over UDP so there is a single clean engine.
        env = dict(os.environ, AK_CAP="0")
        try:
            viz_proc = subprocess.Popen(
                [sys.executable, "-u", script],
                creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        except Exception as exc:  # noqa: BLE001
            print(f"[caption_demo] could not open visualizer: {exc}")
        time.sleep(2.0)

    _stt_mod = _ak_import("streaming_stt")
    StreamingSTT = _stt_mod.StreamingSTT

    print(f"[caption_demo] model : {args.model}")
    print("[caption_demo] speak — words appear the moment you say them (Ctrl+C to stop)\n")

    last_t = time.time()

    def on_word(words):
        nonlocal last_t
        now = time.time()
        print(f"  + {now - last_t:6.0f}ms  -  " + " ".join(words), flush=True)
        last_t = now

    def on_partial(text):
        print(f"    ~ {text}", flush=True)

    def on_final(text):
        print(f"  >> FINAL: {text}", flush=True)

    engine = StreamingSTT(
        args.model, on_word=on_word, on_partial=on_partial, on_final=on_final,
        publish_udp=args.udp, udp_port=args.port,
    )
    engine.start()
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        engine.stop()
    finally:
        if viz_proc is not None:
            viz_proc.terminate()
    print("\n[caption_demo] done. Radhe Radhe boss.")


if __name__ == "__main__":
    main()