#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Face Lock cluster sync — main.py (canonical) ↔ main_build.py (mirror).

Requirement: both files' "Face Lock" method cluster MUST be byte-identical
so that every command AK supports (register my face / register my voice /
remove / status / lock on|off) behaves the same in both entrypoints.

Canonical source = main.py's face-lock cluster (the clearly complete one
with `_handle_myface_cmd`). We re-write main_build.py's face-lock cluster
to byte-match the canonical, verifying anchors before touching anything.
"""

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.normpath(os.path.join(HERE, "..", "main.py"))
BUILD = os.path.normpath(os.path.join(HERE, "..", "backups", "main_build.py"))

FACE_HEADER = re.compile(r"^\s*# ── Face Lock")
NEXT_DEF = re.compile(r"^\s*def _setup_voice_lock\b")


def face_cluster(fn: str) -> tuple:
    """Return (start_line, end_line_exclusive, cluster_text) for the Face Lock
    cluster in `fn`. start_line is 1-indexed of the '# ── Face Lock' header."""
    with open(fn, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    starts = [i for i, l in enumerate(lines, 1) if FACE_HEADER.search(l)]
    if not starts:
        return None
    st = min(starts)
    en = next((i for i in range(st + 1, len(lines) + 1)
               if NEXT_DEF.match(lines[i - 1])), len(lines) + 1)
    cluster = "\n".join(lines[st - 1:en - 1]) + "\n"
    return st, en, cluster


canonical = face_cluster(MAIN)
build = face_cluster(BUILD)
if canonical is None or build is None:
    print("!! Face Lock cluster dono files mein nahi mila — abort, kuch nahi likha.")
    raise SystemExit(1)

c_st, c_en, c_text = canonical
b_st, b_en, b_text = build

if c_text == b_text:
    print("[ok] face-lock cluster palready byte-identical — kuch nahi badla.")
    raise SystemExit(0)

print(f"[sync] main.py face cluster: lines {c_st}..{c_en - 1} "
      f"({len(c_text.splitlines())} lines)")
print(f"[sync] main_build face cluster: lines {b_st}..{b_en - 1} "
      f"({len(b_text.splitlines())} lines)")
print("[sync] clusters differ — main_build ko canonical se overwrite karta hun.")

with open(BUILD, encoding="utf-8") as fh:
    raw = fh.read()

head_blob = open(BUILD, encoding="utf-8").read().splitlines()
before = head_blob[:b_st - 1]
after = head_blob[b_en - 1:]

new_lines = before + c_text.splitlines() + after
with open(BUILD, "w", encoding="utf-8", newline="") as fh:
    fh.write("\n".join(new_lines) + "\n")

verify = face_cluster(BUILD)
print("[sync] done. verify:", "OK — IDENTICAL" if verify[2] == c_text else "FAIL")
