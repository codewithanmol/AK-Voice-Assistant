"""AK Crypto — AES-256-GCM encryption layer.

Best-in-class authenticated encryption (AES-256-GCM):
  • 256-bit master key → 12-byte random nonce per encryption
  • authenticated: wrong key or any tamper => decrypt fails hard
  • auto-unlock via security/ak.key  ->  zero-friction on every run

Layout (inside this project):
  security/            -> restricted folder (owner-only ACL)
    ak.key             -> base64(32-byte master key)  (auto-unlock)
    ak_vault.enc       -> AES-256-GCM encrypted JSON of secrets
    ak_data_backup.enc -> AES-256-GCM encrypted snapshot of D: data
"""

from __future__ import annotations

import base64
import os
import subprocess

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECURITY_DIR = os.path.join(ROOT, "security")
KEY_PATH = os.path.join(SECURITY_DIR, "ak.key")
VAULT_PATH = os.path.join(SECURITY_DIR, "ak_vault.enc")
DATA_BACKUP_PATH = os.path.join(SECURITY_DIR, "ak_data_backup.enc")

_NONCE_LEN = 12


def generate_key() -> bytes:
    """Return a fresh 256-bit (32-byte) AES key."""
    return AESGCM.generate_key(bit_length=256)


def _restrict(path: str) -> None:
    """Lock an NTFS file to the current user + SYSTEM only (no inheritance)."""
    if not os.name == "nt":
        return
    try:
        user = "{}".format(
            (os.environ.get("USERDOMAIN") + "\\" if os.environ.get("USERDOMAIN") else "")
            + os.environ.get("USERNAME", "")
        )
        if not user or user == "\\":
            user = os.environ.get("USERNAME", "")
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r",
             "{}:(R,W)".format(user), "/grant:r", "SYSTEM:(F)"],
            capture_output=True, check=False,
        )
    except Exception:  # noqa: BLE001  (ACL lock is best-effort)
        pass


def save_key(key: bytes, path: str = KEY_PATH) -> str:
    """Write the master key (base64) and lock the file ACL to owner."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(base64.b64encode(key))
    _restrict(path)
    return path


def load_key(path: str = KEY_PATH) -> bytes:
    """Read the master key. Raises FileNotFoundError if it's missing."""
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "AK key file missing: {}".format(path))
    with open(path, "rb") as fh:
        data = fh.read().strip()
    return base64.b64decode(data)


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """nonce + ciphertext, one fresh random 96-bit nonce per call."""
    nonce = os.urandom(_NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(data: bytes, key: bytes) -> bytes:
    """Reverse of encrypt(); raises on wrong key or corrupted/tampered data."""
    if len(data) < _NONCE_LEN + 16:
        raise ValueError("Ciphertext too short or corrupted")
    return AESGCM(key).decrypt(data[:_NONCE_LEN], data[_NONCE_LEN:], None)


def encrypt_auto(plaintext: bytes, key_path: str = KEY_PATH) -> bytes:
    return encrypt(plaintext, load_key(key_path))


def decrypt_auto(data: bytes, key_path: str = KEY_PATH) -> bytes:
    return decrypt(data, load_key(key_path))


def encrypt_file(src: str, dst: str, key: bytes) -> None:
    """Encrypt src into an .enc file (data itself stays untouched)."""
    with open(src, "rb") as fh:
        payload = fh.read()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(encrypt(payload, key))
    _restrict(dst)


def decrypt_file_to_bytes(path: str, key: bytes) -> bytes:
    with open(path, "rb") as fh:
        return decrypt(fh.read(), key)