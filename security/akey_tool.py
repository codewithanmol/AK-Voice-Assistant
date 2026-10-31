"""AK Key Tool — manage the encryption key & encrypted data.

Usage:
  python security\\akey_tool.py show             -> print master key (base64)
  python security\\akey_tool.py status          -> vault/backup status (no secrets)
  python security\\akey_tool.py set <name> <value>   -> write one vault secret
  python security\\akey_tool.py get <name>      -> print one vault secret
  python security\\akey_tool.py del <name>      -> remove one vault secret
  python security\\akey_tool.py re-encrypt NEWBASE64KEY  -> rotate key (re-encrypts vault)
  python security\\akey_tool.py backup          -> D: personal data -> security/ak_data_backup.enc
  python security\\akey_tool.py restore --force -> restore D: data from that backup

Key file: security/ak.key (owner-only ACL). Backup copy: your Desktop
"AK_Recovery_Passport.txt" (include security\\akey_tool.py re-encrypt to swap).
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from AK import crypto, vault  # noqa: E402
from AK.crypto import DATA_BACKUP_PATH, KEY_PATH  # noqa: E402

BACKUP_SOURCES = (
    "memory.json", "notes.json", "automations.json", "custom_rules.json",
    "gita_state.json", "voiceprint.json", "myvoice_ref.wav", "test_myvoice.wav",
    "tts_last.wav", "_warmup.wav", "codefest.sqlite3", "learning.sqlite3",
    "self_learn.sqlite3", "tasks.db",
)
BACKUP_DIRS = ("conversations",)


def _data_dir() -> str:
    from AK.config import get_data_dir
    return get_data_dir()


def _panic(msg: str) -> None:
    sys.stderr.write("[akey_tool] " + msg + "\n")
    sys.exit(1)


def cmd_show() -> None:
    try:
        key = crypto.load_key()
    except FileNotFoundError:
        _panic("Key file missing. Ise khona mat — decrypt karna namumkin ho jayega.")
    print(base64.b64encode(key).decode("ascii"))


def cmd_status() -> None:
    try:
        crypto.load_key()
        key_ok = "present"
    except FileNotFoundError:
        key_ok = "MISSING!"
    print("key            : {}".format(key_ok))
    print("vault secrets  : {}".format(vault.names() or "(empty)"))
    if os.path.isfile(DATA_BACKUP_PATH):
        print("data backup    : {} bytes (encrypted)".format(os.path.getsize(DATA_BACKUP_PATH)))
    else:
        print("data backup    : (none)")


def cmd_set(name: str, value: str) -> None:
    vault.set(name, value)
    print("vault updated: {}".format(name))


def cmd_get(name: str) -> None:
    val = vault.get(name)
    print(val if val else "")


def cmd_del(name: str) -> None:
    print("removed: {}".format(vault.remove(name)))


def cmd_reencrypt(new_b64: str) -> None:
    try:
        raw = new_b64.strip()
        new_key = base64.b64decode(raw)
        if len(new_key) != 32:
            _panic("Key must be exactly 32 bytes (base64 of AES-256).")
    except Exception:
        _panic("Invalid base64 key.")
    old_key = crypto.load_key()
    items = vault._load_items(old_key)
    backup = b""
    if os.path.isfile(DATA_BACKUP_PATH):
        with open(DATA_BACKUP_PATH, "rb") as fh:
            backup = crypto.decrypt(fh.read(), old_key)
    vault._save_items(items, new_key)
    if backup:
        with open(DATA_BACKUP_PATH, "wb") as fh:
            fh.write(crypto.encrypt(backup, new_key))
        crypto._restrict(DATA_BACKUP_PATH)
    crypto.save_key(new_key)
    print("Key rotated. Nayi key vault/backup pe laga di. Old key ab bekaar.")
    print("Naya master key (base64): " + new_b64.strip())


def _snapshot() -> bytes:
    """Zip the sensitive D: files -> bytes (before encryption)."""
    import io
    data_dir = _data_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = set()
        for rel in BACKUP_SOURCES:
            p = os.path.join(data_dir, rel)
            if os.path.isfile(p):
                zf.write(p, rel)
                seen.add(rel)
        for dname in BACKUP_DIRS:
            folder = os.path.join(data_dir, dname)
            if not os.path.isdir(folder):
                continue
            for root, _, files in os.walk(folder):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, data_dir)
                    zf.write(full, arc)
    return buf.getvalue()


def cmd_backup() -> None:
    payload = _snapshot()
    key = crypto.load_key()
    os.makedirs(os.path.dirname(DATA_BACKUP_PATH), exist_ok=True)
    with open(DATA_BACKUP_PATH, "wb") as fh:
        fh.write(crypto.encrypt(payload, key))
    crypto._restrict(DATA_BACKUP_PATH)
    print("Backup encrypted -> security/ak_data_backup.enc ({} bytes)".format(len(payload)))


def cmd_restore(force: bool) -> None:
    if not os.path.isfile(DATA_BACKUP_PATH):
        _panic("Backup nahi hai — pehle 'backup' chalao.")
    key = crypto.load_key()
    with open(DATA_BACKUP_PATH, "rb") as fh:
        payload = crypto.decrypt(fh.read(), key)
    data_dir = _data_dir()
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "akdata.zip")
        with open(zip_path, "wb") as fh:
            fh.write(payload)
        with zipfile.ZipFile(zip_path) as zf:
            for m in zf.infolist():
                if m.is_dir():
                    continue
                target = os.path.join(data_dir, m.filename)
                if os.path.exists(target) and not force:
                    _panic("{} already exists — restore matlab overwrite. '--force' lagao.".format(m.filename))
            zf.extractall(data_dir)
    print("Restore done -> {}".format(data_dir))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd == "show":
        cmd_show()
    elif cmd == "status":
        cmd_status()
    elif cmd == "set" and len(args) >= 3:
        cmd_set(args[1], " ".join(args[2:]))
    elif cmd == "get" and len(args) == 2:
        cmd_get(args[1])
    elif cmd == "del" and len(args) == 2:
        cmd_del(args[1])
    elif cmd == "re-encrypt" and len(args) == 2:
        cmd_reencrypt(args[1])
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "restore":
        cmd_restore(force="--force" in args)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()