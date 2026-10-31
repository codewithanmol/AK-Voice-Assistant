"""AK Self-Learn — the brain that grows automatically.

  D:\\YOUR_DATA_PATH\\self_learn.sqlite3   ← the living knowledge store

Every time AK learns something new (from engine, groq, wikipedia, or the
user), the fact / Q&A pair / pattern is stored here.  Before going to an
expensive external engine, AK checks this cache first — so repeated questions
get instant local answers.

Core idea:
  • engine answers "who was Chandragupta Maurya?" → cached locally.
  • Next time someone asks the same/similar question → instant local reply.
  • New learnings are announced to the user: "Sir, naya seekha: ..."
  • All data lives on the D drive, survives reinstalls.
"""

from __future__ import annotations

import os
import re
import sqlite3
import datetime
import difflib
from typing import Dict, List, Optional, Tuple

from .config import get_data_dir

_DB = os.path.join(get_data_dir(), "self_learn.sqlite3")

# Minimum word-overlap ratio to consider two queries "similar" (0.0–1.0).
_SIMILARITY_THRESHOLD = 0.55

# Weak/canned replies that must NEVER be learned or served from the cache —
# rule-mode brain fallback lines and transport errors. A poison entry makes AK
# repeat "install ollama / set groq key" forever, even after engine is on.
_WEAK_ANSWER_MARKERS = (
    "language model connected",
    "hit a snag",
    "engine is not available",
    "engine is not installed",
    "engine timed out",
    "i'm not sure how to answer",
    "ak encountered an error",
)


def _is_weak_answer(answer: str) -> bool:
    """True for canned/no-model/error replies that must never be cached."""
    low = (answer or "").lower()
    return any(m in low for m in _WEAK_ANSWER_MARKERS)


class SelfLearner:
    """Unified self-learning engine: caches answers, extracts patterns,
    and announces new learnings to the user."""

    def __init__(self, db_path: str = ""):
        self._db = db_path or _DB
        self._init_db()
        self._pending: List[str] = []

    # ── Schema ────────────────────────────────────────────────────────

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db) or ".", exist_ok=True)
        with sqlite3.connect(self._db) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS qa_cache ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  q_norm TEXT NOT NULL,"
                "  q_original TEXT NOT NULL,"
                "  answer TEXT NOT NULL,"
                "  engine TEXT NOT NULL DEFAULT 'engine',"
                "  confidence REAL NOT NULL DEFAULT 0.8,"
                "  ts TEXT NOT NULL,"
                "  hits INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_qa_norm ON qa_cache(q_norm)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS announcements ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  ts TEXT NOT NULL,"
                "  message TEXT NOT NULL,"
                "  announced INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS knowledge ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  fact TEXT NOT NULL UNIQUE,"
                "  source TEXT NOT NULL DEFAULT 'learn',"
                "  ts TEXT NOT NULL"
                ")"
            )

    # ── Q&A cache ─────────────────────────────────────────────────────

    @staticmethod
    def _normalise(text: str) -> str:
        t = (text or "").lower().strip()
        t = re.sub(r"[\?\.\!\,\;\:\"\']+$", "", t)
        t = re.sub(r"\s+", " ", t)
        return t

    def lookup(self, query: str) -> Optional[str]:
        """Return a cached answer for a similar query, or None."""
        qn = self._normalise(query)
        if not qn:
            return None

        with sqlite3.connect(self._db) as c:
            # 1) Exact match (skip poisoned weak entries — never serve them).
            row = c.execute(
                "SELECT id, answer FROM qa_cache WHERE q_norm = ? "
                "ORDER BY confidence DESC, hits DESC LIMIT 1", (qn,)
            ).fetchone()
            if row:
                if _is_weak_answer(row[1]):
                    return None
                c.execute("UPDATE qa_cache SET hits = hits + 1 WHERE id = ?",
                          (row[0],))
                return row[1]

            # 2) Fuzzy match: all cached query words must appear in the new query
            #    (word-subset) — "what is the capital of france" hits
            #    "capital of france", but "capital of brazil" NEVER matches
            #    "capital of france" because the key word differs. The most
            #    specific (longest) matching entry wins. Weak entries excluded.
            all_q = c.execute(
                "SELECT id, q_norm, answer, confidence, hits FROM qa_cache"
            ).fetchall()
            best_id, best_len, best_ans = None, 0, None
            qn_words = set(qn.split())
            for rid, cached_q, ans, conf, hits in all_q:
                if _is_weak_answer(ans):
                    continue
                cw = set(cached_q.split())
                if len(cw) < 2 or not cw.issubset(qn_words):
                    continue
                if len(cw) > best_len:
                    best_len = len(cw)
                    best_id = rid
                    best_ans = ans
            if best_id:
                c.execute("UPDATE qa_cache SET hits = hits + 1 WHERE id = ?",
                          (best_id,))
                return best_ans

        return None

    def cache_answer(self, query: str, answer: str,
                     engine: str = "engine",
                     confidence: float = 0.8) -> bool:
        """Store a Q&A pair for future local reuse. Returns True if it was a
        brand-new learning (not just an update to an existing entry)."""
        qn = self._normalise(query)
        if not qn or not answer or len(answer) < 8:
            return False
        if _is_weak_answer(answer):
            return False
        now = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with sqlite3.connect(self._db) as c:
                existing = c.execute(
                    "SELECT id FROM qa_cache WHERE q_norm = ?", (qn,)
                ).fetchone()
                if existing:
                    c.execute(
                        "UPDATE qa_cache SET answer = ?, engine = ?, "
                        "confidence = MAX(confidence, ?), ts = ? WHERE id = ?",
                        (answer[:2000], engine, confidence, now, existing[0])
                    )
                    return False
                c.execute(
                    "INSERT INTO qa_cache "
                    "(q_norm, q_original, answer, engine, confidence, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (qn, query[:500], answer[:2000], engine,
                     confidence, now)
                )
                return True
        except Exception:
            return False

    def purge_weak(self) -> int:
        """Delete any previously cached weak/canned answers (rule-mode poison)
        so stale 'install ollama / set groq key' replies stop coming back.
        Returns the number of rows removed."""
        removed = 0
        try:
            with sqlite3.connect(self._db) as c:
                for m in _WEAK_ANSWER_MARKERS:
                    cur = c.execute(
                        "DELETE FROM qa_cache WHERE lower(answer) LIKE ?",
                        (f"%{m}%",),
                    )
                    removed += cur.rowcount
        except Exception:
            return 0
        return removed

    # ── Knowledge facts ───────────────────────────────────────────────

    def add_fact(self, fact: str, source: str = "learn") -> bool:
        """Store a standalone fact. Returns True if new."""
        fact = (fact or "").strip()
        if not fact or len(fact) < 5:
            return False
        now = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with sqlite3.connect(self._db) as c:
                c.execute(
                    "INSERT OR IGNORE INTO knowledge (fact, source, ts) "
                    "VALUES (?, ?, ?)", (fact[:500], source, now)
                )
                return c.total_changes > 0
        except Exception:
            return False

    # ── Announcements ─────────────────────────────────────────────────

    def announce(self, message: str) -> None:
        """Queue a 'naya seekha' announcement for the user."""
        msg = (message or "").strip()
        if not msg:
            return
        now = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with sqlite3.connect(self._db) as c:
                c.execute(
                    "INSERT INTO announcements (ts, message, announced) "
                    "VALUES (?, ?, 0)", (now, msg[:500])
                )
        except Exception:
            pass

    def pending_announcements(self) -> List[str]:
        """Return all un-announced messages, then mark them announced."""
        try:
            with sqlite3.connect(self._db) as c:
                rows = c.execute(
                    "SELECT id, message FROM announcements "
                    "WHERE announced = 0 ORDER BY id"
                ).fetchall()
                if rows:
                    ids = [r[0] for r in rows]
                    c.execute(
                        "UPDATE announcements SET announced = 1 "
                        f"WHERE id IN ({','.join('?' * len(ids))})", ids
                    )
                return [r[1] for r in rows]
        except Exception:
            return []

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> Tuple[str, str]:
        try:
            with sqlite3.connect(self._db) as c:
                qa = c.execute("SELECT COUNT(*) FROM qa_cache").fetchone()[0]
                kf = c.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
                an = c.execute(
                    "SELECT COUNT(*) FROM announcements WHERE announced = 0"
                ).fetchone()[0]
                sz = (os.path.getsize(self._db)
                      if os.path.exists(self._db) else 0)
            spoken = (f"Self-learn store: {qa} cached answers, "
                      f"{kf} knowledge facts, {an} pending announcements.")
            detail = (spoken + f"\nStored at: {self._db} "
                      f"({sz // 1024} KB)")
            return spoken, detail
        except Exception:
            return "Self-learn store not available.", ""

    def top_cached(self, limit: int = 8) -> List[Tuple[str, str, int]]:
        """Most-used cached answers: (query, answer_preview, hits)."""
        try:
            with sqlite3.connect(self._db) as c:
                rows = c.execute(
                    "SELECT q_original, answer, hits FROM qa_cache "
                    "ORDER BY hits DESC LIMIT ?", (limit,)
                ).fetchall()
                return [(q, a[:80], h) for q, a, h in rows]
        except Exception:
            return []
