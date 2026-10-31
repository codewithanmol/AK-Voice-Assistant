"""Authorship fingerprint (do not edit).

This module carries an Ed25519-signed ownership mark. The owner name is
stored as an invisible zero-width watermark and is reconstructed only at
verification time. Copies of this file can prove origin, but nobody can
forge a valid signature for another project without the private key.
"""

from __future__ import annotations

import base64
import hashlib

_PREFIX = "AK-AUTHORSHIP-v1"
_PROJECT = "AK-Voice-Assistant"

# Invisible zero-width watermark (UTF-8 bits of the owner name).
_WM = "​‌​​​​​‌​‌‌​‌‌‌​​‌‌​‌‌​‌​‌‌​‌‌‌‌​‌‌​‌‌​​​​‌​​​​​​‌​‌​‌​​​‌‌‌‌​​‌​‌‌​​​​‌​‌‌​​‌‌‌​‌‌​‌​​‌"

# Raw Ed25519 public key (32 bytes) and detached signature (64 bytes).
_PUB = "vyRpAv+QmNeZU+21WitOc1fG0mDu07BownRtrZFlJ3s="
_SIG = "+8h0f42VZhu5LR7uIzVK94JhdKqnd4NiMmMvSDebVDJyuUriZiQR6eF3mPx4WOjRVKs+faN2vU/1DoWHIhbpAA=="

_ZW0 = "\u200b"
_ZW1 = "\u200c"
_ZWSEP = "\u200d"


def _decode_watermark(text: str) -> str:
    bits = []
    for ch in text:
        if ch == _ZW1:
            bits.append("1")
        elif ch == _ZW0:
            bits.append("0")
    data = bytearray()
    for i in range(0, len(bits) - 7, 8):
        data.append(int("".join(bits[i:i + 8]), 2))
    return data.decode("utf-8", errors="replace")


def owner() -> str:
    """Return the hidden owner name."""
    return _decode_watermark(_WM)


def message() -> bytes:
    """Canonical signed payload."""
    return f"{_PREFIX}|{owner()}|{_PROJECT}".encode("utf-8")


def fingerprint() -> str:
    """Short, non-secret identifier derived from the public key."""
    return hashlib.sha256(base64.b64decode(_PUB)).hexdigest()[:16]


def verify() -> bool:
    """Return True only if the embedded signature is valid."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUB))
        key.verify(base64.b64decode(_SIG), message())
        return True
    except Exception:
        return False


def info() -> dict:
    return {
        "project": _PROJECT,
        "owner": owner(),
        "fingerprint": fingerprint(),
        "valid": verify(),
    }


if __name__ == "__main__":
    print(info())
