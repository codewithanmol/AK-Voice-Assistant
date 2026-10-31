"""AK Finding ↔ AK voice-assistant bridge.

Lets the AK assistant trigger an OSINT ("AK Finding") investigation by voice or
chat, run it end-to-end without blocking the assistant loop, then announce the
result verbally and open the HTML report.

Usage inside the assistant::

    from AK.osint_finding import OsintFinding
    self.osint = OsintFinding(self.cfg, say=self._say)
    if self.osint.detect(text):
        return self.osint.start(self.osint.detect(text))

Everything is best-effort and never raises into the assistant loop.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from typing import Optional, Tuple

from .config import get_data_dir

# Where the AK Finding tool lives. Prefer the renamed folder, fall back to the
# original, so the bridge works before/after the D:\khoj -> D:\ak rename.
_TOOL_CANDIDATES = [r"D:\ak", r"D:\khoj"]

_TRIGGER_RE = re.compile(
    r"(?:\bak\s*finding\b|\bakfinding\b|\bosint\b|open[\s-]?source\s+intel"
    r"|\brecon\b|\binvestigate\b|\bresearch\s+kar|\bpata\s+lagao\b"
    r"|\bjankari\s+(?:nikalo|kholo|kar(?:o)?))",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s.\-]{7,}\d)")
_USER_RE = re.compile(r"@([A-Za-z0-9._-]{2,30})")
_QUOTED_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]")

# words that are part of the command, not the target
_STOP = {
    "ka", "ki", "ke", "ko", "kar", "karo", "karke", "report", "banao", "bana",
    "do", "about", "on", "of", "for", "the", "investigate", "research", "osint",
    "recon", "akfinding", "finding", "ak", "pata", "lagao", "jankari", "nikalo",
    "kholo", "open", "source", "intel", "opensource", "aur", "and",
}


def _tool_dir() -> Optional[str]:
    for cand in _TOOL_CANDIDATES:
        if os.path.isfile(os.path.join(cand, "akfinding.py")):
            return cand
    return None


class OsintFinding:
    def __init__(self, config: dict, say=None, open_report: bool = True):
        self.cfg = config
        self.say = say or (lambda _m: None)
        self.open_report = open_report
        self._busy = threading.Event()

    # -- target detection ------------------------------------------------
    def detect(self, text: str) -> Optional[str]:
        """Return the investigation target if `text` is an OSINT command."""
        if not text or not _TRIGGER_RE.search(text):
            return None
        return self._extract_target(text)

    def _extract_target(self, text: str) -> Optional[str]:
        m = _EMAIL_RE.search(text)
        if m:
            return m.group(0)
        m = _PHONE_RE.search(text)
        if m:
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) >= 10:
                return m.group(0).strip()
        m = _USER_RE.search(text)
        if m:
            return "@" + m.group(1)
        m = _QUOTED_RE.search(text)
        if m:
            return m.group(1).strip()
        # else: strip the command words and use what's left as a name/username
        words = [w for w in re.split(r"\s+", text.strip()) if w.lower() not in _STOP]
        words = [w for w in words if not _TRIGGER_RE.search(w)]
        cand = " ".join(words).strip(" .,-")
        return cand or None

    # -- execution -------------------------------------------------------
    def start(self, target: str) -> Tuple[str, bool]:
        """Kick off an investigation in the background; return an ack message."""
        if self._busy.is_set():
            return ("Sir, ek investigation abhi chal rahi hai — uske khatam hone "
                    "par agla shuru karunga.", True)
        self._busy.set()
        threading.Thread(target=self._worker, args=(target,), daemon=True).start()
        return (f"Sir, {target} par research shuru kar diya hai — report taiyaar "
                f"hote hi bata dunga.", True)

    def _worker(self, target: str) -> None:
        try:
            path, summary = self.run_sync(target)
        except Exception as exc:  # noqa: BLE001
            print(f"[osint] investigation failed: {exc}")
            self._busy.clear()
            self.say("Sir, research complete nahi ho paaya — kuch technical "
                     "dikkat aa gayi.")
            return
        self._busy.clear()
        if not path:
            self.say(f"Sir, {target} par kuch verified information nahi mili.")
            return
        n = summary.get("verified", 0)
        cats = summary.get("top_categories") or []
        extra = (" Main sections: " + ", ".join(cats[:3]) + ".") if cats else ""
        spoken = (f"Sir, {target} ki report taiyaar hai. {n} verified findings "
                  f"mile.{extra} Report khol raha hoon.")
        self.say(spoken)
        if self.open_report:
            try:
                os.startfile(path)  # noqa: S606  (Windows shell open)
            except Exception:  # noqa: BLE001
                pass

    def run_sync(self, target: str) -> Tuple[Optional[str], dict]:
        """Run AK Finding synchronously. Returns (html_path, summary)."""
        tool = _tool_dir()
        if not tool:
            raise RuntimeError("AK Finding tool not found (D:\\ak or D:\\khoj)")
        out_dir = os.path.join(get_data_dir(), "OSINT")
        os.makedirs(out_dir, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", target)[:40] or "target"
        html_path = os.path.join(out_dir, f"AKFinding Report - {slug}.html")

        exe = sys.executable or "python"
        cmd = [exe, "-X", "utf8", "akfinding.py", target,
               "-o", html_path, "--json", "--quiet"]
        proc = subprocess.run(
            cmd, cwd=tool, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
        print(f"[osint] exit={proc.returncode} target={target}")
        if proc.stdout:
            print(proc.stdout[-1500:])
        if proc.returncode != 0:
            print(f"[osint] stderr: {proc.stderr[-800:]}")

        if not os.path.isfile(html_path):
            return None, {}

        summary: dict = {}
        json_path = os.path.splitext(html_path)[0] + ".json"
        if os.path.isfile(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                stats = data.get("stats", {}) or {}
                summary["verified"] = stats.get("verified_findings", 0)
                by_cat = stats.get("by_category", {}) or {}
                summary["top_categories"] = [
                    c for c, _ in sorted(by_cat.items(), key=lambda kv: -kv[1])
                ]
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[osint] could not read json: {exc}")
        return html_path, summary
