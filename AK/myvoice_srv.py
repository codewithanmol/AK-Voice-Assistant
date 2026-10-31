"""My Voice TTS server — runs under the D-drive venv312 python.

Cloned-voice speech (XTTS via Coqui) in Hindi/Hinglish. Designed to be kind
to RAM/CPU:
  * threads are capped (default 2),
  * the model is loaded lazily on first request,
  * the server exits itself after `AK_MYVOICE_IDLE` seconds of quiet, which
    frees all of its RAM.

Protocol: newline-delimited JSON on stdin/stdout.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

IDLE = float(os.environ.get("AK_MYVOICE_IDLE", "180"))
THREADS = int(os.environ.get("AK_MYVOICE_THREADS", "2"))


def log(*a) -> None:
    print("[my voice]", *a, file=sys.stderr, flush=True)


_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "hindi_dict.json")
_DICT = {}


def _load_dict() -> None:
    if _DICT:
        return
    try:
        with open(_DICT_PATH, "r", encoding="utf-8") as fh:
            _DICT.update(json.load(fh))
    except Exception:
        pass


def to_devanagari(text: str) -> str:
    """Turn Hinglish (Roman) into Devanagari so XTTS speaks proper Hindi.

    Word-by-word: exact chunks first (longest match) from a conversational
    Hinglish dictionary, then a phonetic fallback via sanscript 'itrans' with
    the trailing halant cleaned off.  Already-Devanagari text passes through.
    """
    if not text:
        return text
    if any("\u0900" <= c <= "\u097f" for c in text):
        return text
    text = text.replace("http://", "").replace("https://", "")
    text = text.replace("www.", "").replace(".com", "").replace(".in", "")
    _load_dict()
    if not _DICT:
        return text

    import re
    tokens = re.findall(r"[A-Za-z\u0900-\u097f]+|[^A-Za-z\u0900-\u097f]+", text)

    # Try a phonetic fallback once (only used for words missing from the dict).
    fallback = {}

    def phon(word: str) -> str:
        if word.lower() not in fallback:
            try:
                from indic_transliteration import sanscript
                d = sanscript.transliterate(word, "itrans", "devanagari")
                d = d.replace("\u094d", "")  # drop hanging halants
                d = re.sub(r"(^\u0938|\u0938)म$", "सम", d)
            except Exception:
                d = word
            fallback[word.lower()] = d
        return fallback[word.lower()]

    out = []
    for tok in tokens:
        low = tok.lower()
        if low in _DICT:
            out.append(_DICT[low])
        elif low and re.fullmatch(r"[a-z]+", low) and len(low) <= 24:
            out.append(phon(low))
        else:
            out.append(tok)
    return "".join(out)


_tts = None
_lock = threading.Lock()
_last_use = [time.time()]
_loading = [False]


def get_tts():
    global _tts
    if _tts is not None:
        return _tts
    import contextlib
    import torch
    torch.set_num_threads(THREADS)
    os.environ.setdefault("HF_HOME", r"D:\YOUR_DATA_PATH\models\hf")
    os.environ.setdefault("TTS_HOME", r"D:\YOUR_DATA_PATH\models")
    os.environ["COQUI_TOS_AGREED"] = "1"
    _loading[0] = True
    try:
        log("loading model...")
        # Keep the JSON stdout channel clean: Coqui/progress prints to stdout.
        with contextlib.redirect_stdout(sys.stderr):
            from TTS.api import TTS
            m = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                    gpu=False)
            tts = m
        log("model ready")
    finally:
        _loading[0] = False
    _tts = tts
    return _tts


def _idle_watch():
    while True:
        time.sleep(20)
        if _loading[0]:
            continue  # never kill mid-load (model load can exceed IDLE)
        if time.time() - _last_use[0] > IDLE:
            log("idle for %.0fs — stopping to free RAM" % IDLE)
            os._exit(0)


def _reply(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    threading.Thread(target=_idle_watch, daemon=True).start()
    log(f"server ready (idle {IDLE:.0f}s, threads {THREADS})")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        cmd = req.get("cmd", "")
        try:
            if cmd == "ping":
                _last_use[0] = time.time()
                _reply({"ok": True, "pong": True})
            elif cmd == "tts":
                _last_use[0] = time.time()
                text = to_devanagari(req.get("text", ""))
                out = req.get("out", "")
                ref = req.get("ref", "")
                lang = req.get("lang", "hi")
                if not text or not out:
                    _reply({"ok": False, "err": "empty text/out"})
                    continue
                tts = get_tts()
                with _lock:
                    if not os.path.isfile(ref):
                        _reply({"ok": False, "err": "reference voice file missing"})
                        continue
                    tts.tts_to_file(text=text, speaker_wav=ref, language=lang,
                                    file_path=out, split_sentences=True)
                _reply({"ok": True, "wav": out})
            elif cmd == "quit":
                _reply({"ok": True, "bye": True})
                break
            else:
                _reply({"ok": False, "err": "unknown cmd"})
        except Exception as exc:  # noqa: BLE001
            _reply({"ok": False, "err": str(exc)})
    log("shutting down")


if __name__ == "__main__":
    main()