"""Persistent memory for AK — everything saved to the local data dir (D:\\YOUR_DATA_PATH).

Stores:
  • Facts about the user   (data/memory.json) — "remember that I like X"
  • Custom voice commands  (data/custom_rules.json) — "teach ai <phrase> to <reply>"

All offline, no external help.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from .config import get_data_dir

_MEMORY_FILE = "memory.json"
_CUSTOM_FILE = "custom_rules.json"


def _store(name: str) -> Path:
    return Path(get_data_dir()) / name


def _load_json(path: Path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, type(default)):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ────────────────────────────────────────────────────────────────
#  User memory (facts)
# ────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────
#  Auto-learn facts from casual chat (offline pattern extraction)
# ────────────────────────────────────────────────────────────────

# Simple "user said something about themselves" patterns → a memory fact.
# These are deliberately conservative: they only fire on clear first-person
# statements, so AK never guesses wrong things from small talk.
_FACT_PATTERNS = [
    # ── Name ───────────────────────────────────────────────────────
    (r"\bmy\s+name\s+is\s+([A-Z][A-Za-z .'-]{1,40})", "user's name is {0}"),
    (r"\b(?:i'?m|i am)\s+called\s+([A-Z][A-Za-z .'-]{1,40})", "user's name is {0}"),
    (r"\bmera\s+naam\s+([A-Za-z][A-Za-z '-]{1,25}?)\s+hai\b", "user's name is {0}"),
    # ── Location ────────────────────────────────────────────────────
    (r"\bi\s+live\s+in\s+([A-Za-z][A-Za-z .'-]{1,30}?)(?:[,.]|$)", "user lives in {0}"),
    (r"\bi\s+am\s+from\s+([A-Za-z][A-Za-z .'-]{1,30}?)(?:[,.]|$)", "user is from {0}"),
    (r"\bmain\s+([A-Za-z][A-Za-z .'-]{1,30}?)\s+(?:me(?:n)?)\s+rehta\s+hun\b", "user lives in {0}"),
    # ── Work / study ───────────────────────────────────────────────
    (r"\bi\s+work\s+as\s+(?:an?\s+)?([A-Za-z][A-Za-z .'-]{1,35}?)(?:[,.]|$)", "user works as a {0}"),
    (r"\bi\s+study\s+([A-Za-z][A-Za-z .'-]{1,35}?)(?:[,.]|$)", "user studies {0}"),
    (r"\bmain\s+([A-Za-z][A-Za-z .'-]{1,25}?)\s+padhta\s+hun\b", "user studies {0}"),
    # ── Preferences / likes ────────────────────────────────────────
    (r"\bi\s+(?:really\s+)?(?:like|love)\s+([A-Za-z][A-Za-z .'-]{2,35}?)(?:\s+and\b|$|[,.])", "user likes {0}"),
    (r"\bmujhe\s+([A-Za-z][A-Za-z .'-]{2,35}?)\s+(?:(?:bahut\s+)?pasand)\s+(?:hai)?\b", "user likes {0}"),
    (r"\bmujhe\s+([A-Za-z][A-Za-z .'-]{2,20}?)\s+dekhna\s+(?:accha|laga|pasand)\s+(?:lagta|hai)?", "user likes watching {0}"),
    (r"\bmy\s+favourite\s+([a-z]{1,12})\s+is\s+([A-Za-z][A-Za-z .'-]{1,35}?)(?:[,.]|$)", "user's favourite {0} is {1}"),
]


def extract_facts(user_text: str) -> List[str]:
    """Pull clear self-describing facts out of a user message (first-person
    statements only). Returns a list of facts AK can safely memorize.
    Pure offline — regex based, so it costs nothing and never calls an API."""
    t = user_text.strip()
    if not t or len(t) > 200:
        return []
    facts: List[str] = []
    for pattern, template in _FACT_PATTERNS:
        if template is None:
            continue
        m = re.search(pattern, t, re.IGNORECASE)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if not groups:
            continue
        value = groups[-1].strip(" .,!?")
        if not value or len(value) < 2:
            continue
        # Don't memorize vague small-talk answers.
        if re.search(r"^\s*(ok|okay|a lot|it|this|that|you|me|very much|nahi|haan|yes|no)\s*$", value, re.I):
            continue
        # Skip values that trail a Hindi verb (dekhna/khana/karna/padhna…).
        if re.search(r"\b(dekhna|khana|karna|padhna|sikna|bolna|chalna)\s*$", value, re.I):
            continue
        fact = template.format(*groups).strip()
        fact = re.sub(r"\s+", " ", fact)
        if fact and fact not in facts:
            facts.append(fact)
    return facts[:3]


class Memory:
    FILE = _MEMORY_FILE

    def __init__(self):
        self.path = _store(self.FILE)
        self.facts: List[str] = _load_json(self.path, [])

    def add_fact(self, text: str) -> str:
        text = text.strip(" .,;:!?").strip()
        if not text:
            return "Nothing to remember."
        # case-insensitive de-dup, so "i live in delhi" and "user lives in Delhi"
        # never both get stored.
        tl = text.lower()
        if any(f.lower() == tl for f in self.facts):
            return f"I already remember that: {text}"
        self.facts.append(text)
        _save_json(self.path, self.facts)
        return f"Got it. I'll remember: {text}"

    def remove_fact(self, index: int) -> str:
        if 1 <= index <= len(self.facts):
            removed = self.facts.pop(index - 1)
            _save_json(self.path, self.facts)
            return f"Forgot: {removed}"
        return "No memory with that number."

    def clear(self) -> str:
        self.facts = []
        _save_json(self.path, self.facts)
        return "Memory cleared."

    def summary(self) -> str:
        if not self.facts:
            return "I don't remember anything about you yet. Say: remember that I like X"
        lines = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(self.facts))
        return f"Things I remember about you:\n{lines}"

    def context_text(self) -> str:
        """Short block used to bias engine/AK replies (empty if nothing known)."""
        if not self.facts:
            return ""
        return "Known facts about the user:\n" + "\n".join(
            f"- {f}" for f in self.facts
        )


# ────────────────────────────────────────────────────────────────
#  Custom voice commands (teachable behaviour)
# ────────────────────────────────────────────────────────────────

class CustomRules:
    FILE = _CUSTOM_FILE

    def __init__(self):
        self.path = _store(self.FILE)
        raw = _load_json(self.path, [])
        self.rules: List[dict] = [
            r for r in raw if isinstance(r, dict) and r.get("trigger") and r.get("response")
        ]

    def add(self, trigger: str, response: str) -> str:
        trigger = trigger.strip().lower()
        response = response.strip()
        if not trigger or not response:
            return "Use the form: teach ai <phrase> to <reply>"
        for r in self.rules:
            if r["trigger"] == trigger:
                r["response"] = response
                _save_json(self.path, self.rules)
                return f"Updated rule for '{trigger}'."
        self.rules.append({"trigger": trigger, "response": response})
        _save_json(self.path, self.rules)
        return f"Learned! When you say '{trigger}', I'll answer with: {response}"

    def remove(self, index: int) -> str:
        if 1 <= index <= len(self.rules):
            r = self.rules.pop(index - 1)
            _save_json(self.path, self.rules)
            return f"Forgot rule '{r['trigger']}'."
        return "No rule with that number."

    def find(self, text: str) -> Optional[str]:
        t = text.lower()
        for r in self.rules:
            if r["trigger"] in t:
                return r["response"]
        return None

    def list(self) -> str:
        if not self.rules:
            return "No custom commands yet. Say: teach ai <phrase> to <reply>"
        lines = "\n".join(f"  {i+1}. '{r['trigger']}' -> {r['response']}" for i, r in enumerate(self.rules))
        return f"Custom commands I know:\n{lines}"