"""Configuration loading with sane defaults.

Reads config.json from the project root (next to this package). Any missing
key falls back to the baked-in defaults below, so a partial config still works.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy

# Project root = parent of the `AK` package directory.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")

# Default location for ALL local data (tasks, notes, memory, gita state…).
DEFAULT_DATA_DIR = r"D:\YOUR_DATA_PATH"

# The resolved data folder is cached - probing it on every call is wasteful
# and, on a watched drive, it used to throw a scary warning every few seconds.
_DATA_DIR_CACHE = ""
_DATA_DIR_CFG_MTIME = None
_DATA_DIR_WARNED = False

DEFAULTS = {
    "assistant_name": "AK",
    "wake_word": "ak",
    "require_wake_word": False,
    "data_path": DEFAULT_DATA_DIR,
    "voice": {
        "tts_rate": 100,
        "tts_volume": 1.0,
        "preferred_voice": "",
        # Google's free web STT (recognize_google) is the most accurate for
        # Hinglish and works again (verified on this machine). Local
        # faster-whisper stays as an automatic fallback when Google is
        # unreachable, so AK still hears something with no internet.
        "stt_engine": "google",
        "language": "en-IN",
        "input_device_index": None,
        "interrupt": True,
        "vosk_model_path": "models/vosk-model-small-en-us-0.15",
        "use_local_stt": True,
        "whisper_model": "base",
        "whisper_cpu_threads": 2,
        "whisper_compute_type": "int8",
        "whisper_vad": True,
        "endpoint_silence_ms": 600,
        "wake_speech_ms": 250,
        "wake_min_audio_ms": 400,
        # Terminal typing effect (conversation looks "typed out", nice)
        "type_effect": True,
        "type_speed": 15,
        "type_max_chars": 600,
    },
    "brain": {
        "backend": "auto",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.2",
        "groq_model": "llama-3.3-70b-versatile",
        "max_history": 12,
        # Coding/task engine: "local" (own Ollama model), "engine" (CLI) or
        # "auto" (local if ready, else engine). When `ask_backend_on_start`
        # is true, AK asks which one to use every time it starts.
        "agent_backend": "auto",
        "engine_bin": "",
        "ask_backend_on_start": False,
        "system_prompt": (
            "You are AK, a witty, concise, and highly capable voice "
            "assistant. Keep spoken answers short (1-3 sentences) unless asked "
            "for detail. Be direct and helpful."
        ),
    },
    "skills": {
        "default_search_engine": "https://www.google.com/search?q=",
        "wikipedia_sentences": 2,
        "wikipedia_auto": True,
    },
    "face_intel": {
        "reverse_cooldown": 5.0,
        "reverse_attempts": 2,
        "auto_open_browser": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into a copy of `base`."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str = CONFIG_PATH) -> dict:
    """Load config.json merged over defaults. Missing file -> pure defaults."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
            cfg = _deep_merge(DEFAULTS, user_cfg)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[config] Could not read {path} ({exc}); using defaults.")
            cfg = deepcopy(DEFAULTS)
    else:
        cfg = deepcopy(DEFAULTS)
    try:
        from . import vault as _vault
        cfg = _vault.inject(cfg)
    except Exception as exc:  # noqa: BLE001  (vault failure must never break AK)
        print(f"[config] vault auto-unlock not available ({exc})")
    return cfg


def abspath(relative: str) -> str:
    """Resolve a path that may be relative to the project root."""
    if os.path.isabs(relative):
        return relative
    return os.path.join(ROOT, relative)


def get_data_dir() -> str:
    """Return the folder where ALL local data is saved (falls back to D:\\, then project data).

    Reads `data_path` from config.json. Creates the folder if missing.
    The probe is cached - the folder is checked once per process, not on
    every single call.
    """
    global _DATA_DIR_CACHE, _DATA_DIR_CFG_MTIME
    cfg_path = os.environ.get("AK_CONFIG", CONFIG_PATH)
    try:
        mtime = os.path.getmtime(cfg_path)
    except OSError:
        mtime = 0.0
    if (_DATA_DIR_CACHE and os.path.isdir(_DATA_DIR_CACHE)
            and mtime == _DATA_DIR_CFG_MTIME):
        return _DATA_DIR_CACHE

    data_path = ""
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data_path = (json.load(fh).get("data_path") or "").strip()
        except (json.JSONDecodeError, OSError):
            data_path = ""
    if not data_path:
        loaded = load_config(cfg_path)
        data_path = loaded.get("data_path") or DEFAULT_DATA_DIR
    try:
        os.makedirs(data_path, exist_ok=True)
        # A private probe name - the folder can be watched by a scanner or
        # another AK process, and a shared name makes them fight over it.
        probe = os.path.join(data_path, ".ak_probe_%d" % os.getpid())
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        # the write is what proves the folder works - removing the probe can
        # fail when something else holds the file open, and that is not an
        # error worth falling back for
        for _ in range(3):
            try:
                os.remove(probe)
                break
            except OSError:
                time.sleep(0.03)
        _DATA_DIR_CACHE = data_path
        _DATA_DIR_CFG_MTIME = mtime
        return data_path
    except OSError:
        fallback = os.path.join(ROOT, "data")
        os.makedirs(fallback, exist_ok=True)
        if not _DATA_DIR_WARNED:
            _DATA_DIR_WARNED = True
            print(f"[config] {data_path} not writable; using {fallback}")
        _DATA_DIR_CACHE = fallback
        _DATA_DIR_CFG_MTIME = mtime
        return fallback
