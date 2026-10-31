#!/usr/bin/env python3
"""Verify the hidden authorship mark embedded in this project."""

import importlib.util
import json
import os

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "_signature", os.path.join(_here, "_signature.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    print(json.dumps(_mod.info(), indent=2, ensure_ascii=False))
