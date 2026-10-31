"""Global hotkey listener — press Ctrl+J to trigger one voice command."""

import os
import subprocess
import sys

import keyboard

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAIN = os.path.join(_HERE, "..", "main.py")


def _run_once():
    try:
        subprocess.run(
            [sys.executable, _MAIN, "--voice-once"],
            cwd=os.path.dirname(_MAIN),
            timeout=120,
        )
    except Exception:
        pass


def main():
    print("[hotkey] Ctrl+J registered. Press Ctrl+J for voice command.")
    print("[hotkey] Press Ctrl+C to exit.")
    keyboard.add_hotkey("ctrl+j", _run_once)
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\n[hotkey] Exiting.")


if __name__ == "__main__":
    main()
