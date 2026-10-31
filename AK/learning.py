"""AK Learning Engine — continuous self-improvement, fully offline.

Everything is stored on the D drive in  D:\\YOUR_DATA_PATH\\learning.sqlite3.

What AK tracks:
  • lessons      — every delegated / learned task (input → engine → outcome)
  • usage        — how often each skill/brain is used (strengths)
  • unknowns     — phrases with no local routine yet (things to learn next)
  • improvements — a journal of self-improvements AK has made

review() reads all of this and tells the user what AK has learned, what its
strengths are, and what it recommends improving next — smart, data-driven,
100% local. Works fine even when the learning history is huge (10GB+): it
queries the SQLite file directly instead of loading everything into memory.
"""

from __future__ import annotations

import os
import sqlite3
import datetime
from typing import Dict, List, Optional, Tuple

from .config import get_data_dir

_DB = os.path.join(get_data_dir(), "learning.sqlite3")


class LearningEngine:
    def __init__(self, db_path: str = ""):
        self._db = db_path or _DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS lessons ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "ts TEXT NOT NULL,"
                "input_text TEXT NOT NULL,"
                "engine TEXT NOT NULL,"
                "outcome TEXT NOT NULL DEFAULT '',"
                "engine_hint INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lessons_ts ON lessons(ts)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS usage ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "skill TEXT NOT NULL UNIQUE,"
                "count INTEGER NOT NULL DEFAULT 0,"
                "last_ts TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS unknowns ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "text TEXT NOT NULL UNIQUE,"
                "count INTEGER NOT NULL DEFAULT 0,"
                "first_seen TEXT NOT NULL,"
                "last_seen TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS improvements ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "ts TEXT NOT NULL,"
                "note TEXT NOT NULL"
                ")"
            )

    # ────────────────────────────────────────────────────────────
    #  Recording (called automatically by the assistant)
    # ────────────────────────────────────────────────────────────

    def record_lesson(self, input_text: str, engine: str, outcome: str = "") -> None:
        """Record that a task was performed and how (local / engine / rule)."""
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO lessons (ts, input_text, engine, outcome) VALUES (?, ?, ?, ?)",
                    (datetime.datetime.now().isoformat(timespec="seconds"),
                     input_text[:500], engine, outcome[:2000]),
                )
        except Exception:
            pass

    def record_usage(self, skill: str) -> None:
        """Increment the usage counter for a skill / engine."""
        skill = (skill or "unknown")[:80]
        try:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO usage (skill, count, last_ts) VALUES (?, 1, ?)"
                    " ON CONFLICT(skill) DO UPDATE SET"
                    " count = count + 1, last_ts = excluded.last_ts",
                    (skill, now),
                )
        except Exception:
            pass

    def log_unknown(self, text: str) -> None:
        """A phrase AK couldn't handle locally — queued to learn later."""
        text = text.strip().lower()[:300]
        if not text:
            return
        try:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO unknowns (text, count, first_seen, last_seen)"
                    " VALUES (?, 1, ?, ?)"
                    " ON CONFLICT(text) DO UPDATE SET"
                    " count = count + 1, last_seen = excluded.last_seen",
                    (text, now, now),
                )
        except Exception:
            pass

    def record_improvement(self, note: str) -> None:
        """Log a self-improvement AK made (a new skill, model, dataset, fix…)."""
        note = note.strip()
        if not note:
            return "What improvement? Say: note improvement <thing I improved>"
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO improvements (ts, note) VALUES (?, ?)",
                    (datetime.datetime.now().isoformat(timespec="seconds"), note[:500]),
                )
            return "Improvement noted and saved to my learning journal."
        except Exception as exc:
            return f"Could not save improvement: {exc}"

    # ────────────────────────────────────────────────────────────
    #  Insight / review (self-improvement engine)
    # ────────────────────────────────────────────────────────────

    def stats(self) -> Tuple[str, str]:
        with sqlite3.connect(self._db) as conn:
            lessons = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
            used = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
            unk = conn.execute("SELECT COUNT(*) FROM unknowns").fetchone()[0]
            imp = conn.execute("SELECT COUNT(*) FROM improvements").fetchone()[0]
            size = os.path.getsize(self._db) if os.path.exists(self._db) else 0
        spoken = (f"Learning database: {lessons} lessons learned, {used} skills tracked, "
                  f"{unk} things still to learn, and {imp} recorded improvements.")
        detail = spoken + f"\nStored at: {self._db} ({_fmt(size)})"
        return spoken, detail

    def _top_usage(self, limit: int = 6) -> List[Tuple[str, int]]:
        with sqlite3.connect(self._db) as conn:
            return conn.execute(
                "SELECT skill, count FROM usage ORDER BY count DESC LIMIT ?", (limit,)
            ).fetchall()

    def _recent_lessons(self, limit: int = 5) -> List[Tuple[str, str, str]]:
        with sqlite3.connect(self._db) as conn:
            return conn.execute(
                "SELECT ts, engine, input_text FROM lessons ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def _top_unknowns(self, limit: int = 5) -> List[Tuple[str, int]]:
        with sqlite3.connect(self._db) as conn:
            return conn.execute(
                "SELECT text, count FROM unknowns ORDER BY count DESC LIMIT ?", (limit,)
            ).fetchall()

    def _recent_improvements(self, limit: int = 5) -> List[Tuple[str, str]]:
        with sqlite3.connect(self._db) as conn:
            return conn.execute(
                "SELECT ts, note FROM improvements ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def review(self) -> Tuple[str, str]:
        """Full self-review: strengths, lessons learned, unknowns, suggestions."""
        top = self._top_usage(6)
        lessons = self._recent_lessons(5)
        unknowns = self._top_unknowns(5)
        imps = self._recent_improvements(5)
        stats_spoken, stats_detail = self.stats()

        strength = (f"My strongest abilities are: "
                    + ", ".join(f"{s} ({c}x)" for s, c in top[:4])
                    if top else "I am still building my skill profile.")

        if lessons:
            learn_line = "Recently I learned: " + "; ".join(
                f"{eng}: {txt[:50]}" for _t, eng, txt in lessons[:3]
            )
        else:
            learn_line = "I keep a journal of every task I learn."

        if unknowns:
            unk_line = ("Things I want to learn next: "
                        + ", ".join(f"'{t[:40]}' ({c}x)" for t, c in unknowns[:3])
                        + ". Say: teach me to handle '<phrase>' so I remember it.")
        else:
            unk_line = "No unknown requests queued — I am keeping up with you."

        if imps:
            imp_line = "My latest improvements: " + "; ".join(note[:40] for _t, note in imps[:3])
        else:
            imp_line = "Improvement journal ready — as I evolve, I log it here."

        spoken = (f"Self review complete. {strength}. {learn_line}. {unk_line}. {imp_line}")
        detail = (
            f"── AK SELF REVIEW ─────────────────────────\n{stats_detail}\n\n"
            f"Strengths:\n" + "\n".join(f"  • {s} — used {c} times" for s, c in top) + "\n\n"
            f"Lessons learned (recent):\n"
            + "\n".join(f"  • [{eng}] {txt}" for _t, eng, txt in lessons) + "\n\n"
            f"To-learn queue:\n"
            + "\n".join(f"  • '{t}' ({c}x)" for t, c in unknowns) + "\n\n"
            f"Improvements journal:\n"
            + "\n".join(f"  • {n}" for _t, n in imps)
        )
        return spoken, detail

    def teach_response(self, phrase: str, reply: str) -> str:
        """User teaches AK a new rule → stored + logged as an improvement."""
        from .memory import CustomRules
        msg = CustomRules().add(phrase, reply)
        self.record_improvement(f"Learned custom rule: '{phrase}'")
        return msg


def _fmt(size: int) -> str:
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"