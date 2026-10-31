"""Print the decrypted mobile-server password (safe for tools/scripts).

Uses security/ak.key automatically — koi password prompt nahi hota.
Usage:  python security\\showpwd.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from AK import vault  # noqa: E402

try:
    sys.stdout.write(vault.get("server_secret", "").strip())
except Exception:  # noqa: BLE001
    sys.stderr.write("[showpwd] vault unlock failed — key missing or wrong.")
    sys.exit(1)