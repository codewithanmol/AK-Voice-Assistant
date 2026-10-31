"""AK Automations — learned custom automations (persistent).

When a brand-new task is performed for the first time (locally or via
engine), AK remembers the trigger phrase and how it was done.  Next time
that phrase (or a close match) is heard, the automation is replayed
instantly — locally if it was a local recipe, otherwise re-delegated to
engine.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


class Automations:
    """Stores persistent, learned automation recipes."""

    def __init__(self, data_path: str, max_entries: int = 300):
        self.file = os.path.join(data_path, "automations.json")
        self.data_path = data_path
        self.max_entries = max_entries
        self.items: List[Dict] = []
        self.load()

    # ── persistence ──────────────────────────────────────────────

    def load(self):
        try:
            if os.path.isfile(self.file):
                with open(self.file, "r", encoding="utf-8") as fh:
                    self.items = json.load(fh)
        except Exception:
            self.items = []

    def save(self):
        try:
            os.makedirs(self.data_path, exist_ok=True)
            with open(self.file, "w", encoding="utf-8") as fh:
                json.dump(self.items, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ── matching ─────────────────────────────────────────────────

    def find(self, user_text: str) -> Optional[Dict]:
        """Best fuzzy keyword match: the longest trigger whose words
        appear in user_text (at least half the trigger words required)."""
        ut = user_text.lower().strip()
        best, best_score = None, 0
        for a in self.items:
            trig = (a.get("match") or "").lower().strip()
            if not trig:
                continue
            words = trig.split()
            hits = sum(1 for w in words if w in ut)
            # require a majority of trigger words (at least 1)
            required = max(1, (len(words) + 1) // 2)
            if hits >= required and hits > best_score:
                best, best_score = a, hits
        return best

    # ── CRUD ─────────────────────────────────────────────────────

    def has(self, match_text: str) -> bool:
        return self.find(match_text) is not None

    def add(self, match: str, task: str, engine: str) -> None:
        """Add an automation if one with a similar match phrase does not
        already exist.  engine is 'local' or 'engine'."""
        # Skip near-duplicates
        if self.find(match):
            return
        self.items.append({
            "match": match.strip(),
            "task": task.strip(),
            "engine": engine,
        })
        if len(self.items) > self.max_entries:
            self.items = self.items[-self.max_entries:]
        self.save()

    def remove(self, match_text: str) -> bool:
        trig = match_text.lower().strip()
        for i, a in enumerate(self.items):
            if trig in (a.get("match") or "").lower():
                del self.items[i]
                self.save()
                return True
        return False

    def list_all(self) -> List[Dict]:
        return list(self.items)
