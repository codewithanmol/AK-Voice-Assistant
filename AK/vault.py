"""AK Secret Vault — AES-256-GCM encrypted secrets, auto-unlocked at runtime.

Stores one JSON object, encrypted as a single authenticated blob:
  security/ak_vault.enc   (key in security/ak.key, owner ACL locked)

Zero-friction: load_config() injects whatever the vault holds, so main.py
and ak_server.py keep working without any password prompts.
"""

from __future__ import annotations

import json
import os

from .crypto import (
    SECURITY_DIR,
    VAULT_PATH,
    _restrict,
    decrypt,
    encrypt,
    load_key,
)


def _load_items(key: bytes = None) -> dict:
    key = key or load_key()
    if not os.path.isfile(VAULT_PATH):
        return {}
    with open(VAULT_PATH, "rb") as fh:
        raw = decrypt(fh.read(), key)
    try:
        items = json.loads(raw.decode("utf-8"))
        return items if isinstance(items, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _save_items(items: dict, key: bytes = None) -> None:
    key = key or load_key()
    payload = json.dumps(items, ensure_ascii=False).encode("utf-8")
    os.makedirs(SECURITY_DIR, exist_ok=True)
    tmp = VAULT_PATH + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(encrypt(payload, key))
    os.replace(tmp, VAULT_PATH)
    _restrict(VAULT_PATH)


def load_items() -> dict:
    """Return decrypted secrets as a dict. Empty if vault missing."""
    try:
        return _load_items()
    except Exception:  # noqa: BLE001  (callers decide how to react)
        return {}


def get(name: str, default: str = "") -> str:
    return str(_load_items().get(name, default))


def set(name: str, value: str, key: bytes = None) -> None:
    items = _load_items(key) if key else load_items()
    items[name] = value
    _save_items(items, key)


def remove(name: str, key: bytes = None) -> bool:
    items = _load_items(key) if key else load_items()
    if name not in items:
        return False
    del items[name]
    _save_items(items, key)
    return True


def names() -> list:
    return sorted(_load_items().keys())


def inject(cfg: dict) -> dict:
    """Merge vault secrets into a loaded config dict (under cfg['brain']).

    Covers groq key + any mobile token fields so main.py / ak_server.py
    read them exactly as before — no code paths need to change.
    """
    items = load_items()
    if not items:
        return cfg
    brain = cfg.setdefault("brain", {})
    if items.get("groq_key"):
        brain["groq_key"] = items["groq_key"]
    for field in ("mobile_token", "password", "token"):
        if items.get(field):
            brain[field] = items[field]
    if items.get("server_secret"):
        brain.setdefault("server_secret", items["server_secret"])
    return cfg