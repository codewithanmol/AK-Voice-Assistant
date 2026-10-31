r"""Record your voice (~40s) -> D:\YOUR_DATA_PATH\myvoice_ref.wav  (XTTS clone ref)."""
import os
import sys
import time
import wave

import sounddevice as sd

SR = 16000
SECS = int(os.environ.get("AK_REC_SECS", "40"))
OUT = r"D:\YOUR_DATA_PATH\myvoice_ref.wav"


def main() -> None:
    print("=" * 50)
    print("  VOICE RECORDING START - BOLNA SHURU KARO!")
    print(f"  {SECS} seconds tak naturally bolo (Hindi/Hinglish)")
    print("=" * 50, flush=True)
    data = sd.rec(int(SECS * SR), samplerate=SR, channels=1, dtype="int16")
    # countdown display
    for i in range(SECS, 0, -1):
        time.sleep(1)
        if i % 5 == 0 or i <= 3:
            print(f"  ...{i}s", flush=True)
    sd.wait()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with wave.open(OUT, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print(f"SAVED -> {OUT}  ({os.path.getsize(OUT)} bytes)")
    print("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc)
        sys.exit(1)