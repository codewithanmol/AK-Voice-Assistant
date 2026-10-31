"""OFFLINE-FIRST dictionary for AK — stored on D:\\YOUR_DATA_PATH\\Dictionary.

Lookup order:
  1. local SQLite cache (D:\\YOUR_DATA_PATH\\Dictionary\\dict.db)
  2. WordNet (offline, bundled at D:\\YOUR_DATA_PATH\\nltk_data) — covers ~155k
     English words, so almost ANY word works fully offline
  3. Datamuse web API (free, no key) as a final fallback — cached forever

Also provides:
  • synonyms / antonyms
  • word of the day / random word
  • spelling ("spell serenity")
  • offline unit converter, age & BMI helpers (daily-life stuff)

No API keys, no paid services.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .config import get_data_dir

# ───── offline WordNet ─────────────────────────────────────────
_WN = None  # lazy-loaded nltk.corpus.wordnet (data lives on the D drive)


def _get_wordnet():
    global _WN
    if _WN is None:
        try:
            managed = os.path.join(get_data_dir(), "nltk_data")
            os.environ.setdefault("NLTK_DATA", managed)
            import nltk.data as nltk_data_mod
            for cand in (managed, r"D:\YOUR_DATA_PATH\nltk_data"):
                if os.path.isdir(cand) and cand not in nltk_data_mod.path:
                    nltk_data_mod.path.append(cand)
            from nltk.corpus import wordnet as wn
            try:
                wn.synsets("a")  # force lazy load side-effect
            except Exception:
                pass
            _WN = wn
        except Exception:
            _WN = False
    return _WN or None


_WN_POS = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}


def _wordnet_offline(key: str) -> Optional[dict]:
    """Best-effort full offline definition lookup via local WordNet."""
    wn = _get_wordnet()
    if not wn:
        return None
    try:
        synsets = wn.synsets(key)
    except Exception:
        return None
    if not synsets:
        return None
    entries: List[dict] = []
    for s in synsets[:6]:
        entries.append({
            "pos": _WN_POS.get(s.pos() or "", "other"),
            "definition": s.definition().strip(),
        })
        ex = s.examples()
        if ex:
            entries[-1]["example"] = ex[0].strip()
    if not entries:
        return None
    synonyms = []
    antonyms = []
    seen_syn, seen_ant = set(), set()
    for s in synsets:
        for l in s.lemmas():
            name = (getattr(l, "lemma_name", None) or getattr(l, "name"))().lower().replace("_", " ")
            if name and " " not in name and name != key and name not in seen_syn:
                seen_syn.add(name)
                synonyms.append(name)
            for a in l.antonyms():
                aname = (getattr(a, "lemma_name", None) or getattr(a, "name"))().lower().replace("_", " ")
                if aname and " " not in aname and aname not in seen_ant:
                    seen_ant.add(aname)
                    antonyms.append(aname)
    return {
        "word": key,
        "phonetic": "",
        "entries": entries,
        "synonyms": synonyms[:12],
        "antonyms": antonyms[:12],
        "source": "wordnet",
    }

# ────────────────────────────────────────────────────────────────
#  Built-in offline seed — common daily words, ready even with no
#  internet.  These are written into the DB on first run.
# ────────────────────────────────────────────────────────────────

_SEED: Dict[str, List[dict]] = {
    "time": [{"pos": "noun", "definition": "the ongoing progress of existence and events, measured in minutes, hours and days."}],
    "day": [{"pos": "noun", "definition": "a period of 24 hours, or the time between sunrise and sunset."}],
    "night": [{"pos": "noun", "definition": "the time of darkness between sunset and sunrise."}],
    "morning": [{"pos": "noun", "definition": "the early part of the day, from sunrise until noon."}],
    "evening": [{"pos": "noun", "definition": "the latter part of the day, from late afternoon until night."}],
    "water": [{"pos": "noun", "definition": "a clear liquid without colour or taste, needed by all living things."}],
    "food": [{"pos": "noun", "definition": "anything people or animals eat or drink to stay alive and grow."}],
    "house": [{"pos": "noun", "definition": "a building where people live."}],
    "home": [{"pos": "noun", "definition": "the place where one lives, feels comfortable and belongs."}],
    "car": [{"pos": "noun", "definition": "a four-wheeled road vehicle powered by an engine, used to carry people."}],
    "road": [{"pos": "noun", "definition": "a wide, hard surface built for vehicles to travel on."}],
    "school": [{"pos": "noun", "definition": "a place where children and young people are taught."}],
    "book": [{"pos": "noun", "definition": "a set of printed pages bound together for reading."}],
    "pen": [{"pos": "noun", "definition": "an instrument used for writing with ink."}],
    "paper": [{"pos": "noun", "definition": "thin material used for writing, printing or wrapping."}],
    "friend": [{"pos": "noun", "definition": "a person whom you know well and like, other than family."}],
    "family": [{"pos": "noun", "definition": "a group of people related to one another, such as parents and children."}],
    "mother": [{"pos": "noun", "definition": "a female parent."}],
    "father": [{"pos": "noun", "definition": "a male parent."}],
    "brother": [{"pos": "noun", "definition": "a male sibling, someone who shares the same parents."}],
    "sister": [{"pos": "noun", "definition": "a female sibling, someone who shares the same parents."}],
    "child": [{"pos": "noun", "definition": "a young human being, a son or daughter."}],
    "boy": [{"pos": "noun", "definition": "a young male child or man."}],
    "girl": [{"pos": "noun", "definition": "a young female child or woman."}],
    "man": [{"pos": "noun", "definition": "an adult male human."}],
    "woman": [{"pos": "noun", "definition": "an adult female human."}],
    "city": [{"pos": "noun", "definition": "a large and important town, where many people live and work."}],
    "village": [{"pos": "noun", "definition": "a small community of houses in a country area."}],
    "country": [{"pos": "noun", "definition": "a nation with its own land, government and people."}],
    "king": [{"pos": "noun", "definition": "the male ruler of a kingdom."}],
    "queen": [{"pos": "noun", "definition": "the female ruler of a kingdom."}],
    "world": [{"pos": "noun", "definition": "the earth and all the life on it."}],
    "earth": [{"pos": "noun", "definition": "the planet we live on."}],
    "sky": [{"pos": "noun", "definition": "the space above the earth where clouds and the sun appear."}],
    "sun": [{"pos": "noun", "definition": "the star at the centre of our solar system that gives us light and heat."}],
    "moon": [{"pos": "noun", "definition": "the natural satellite that circles the earth and shines at night."}],
    "star": [{"pos": "noun", "definition": "a bright ball of gas seen as a point of light in the night sky."}],
    "cloud": [{"pos": "noun", "definition": "a visible mass of water vapour floating in the sky."}],
    "rain": [{"pos": "noun", "definition": "water falling in drops from clouds."}],
    "wind": [{"pos": "noun", "definition": "the natural movement of air."}],
    "fire": [{"pos": "noun", "definition": "the bright, hot flame produced when something burns."}],
    "tree": [{"pos": "noun", "definition": "a tall plant with a trunk, branches and leaves."}],
    "flower": [{"pos": "noun", "definition": "the coloured part of a plant that produces seeds, often scented."}],
    "bird": [{"pos": "noun", "definition": "an animal with feathers, wings and a beak, most of which can fly."}],
    "animal": [{"pos": "noun", "definition": "a living creature that can move and feel, as opposed to a plant."}],
    "dog": [{"pos": "noun", "definition": "a common four-legged animal that many people keep as a pet."}],
    "cat": [{"pos": "noun", "definition": "a small furry animal kept as a pet and which likes to hunt mice."}],
    "fish": [{"pos": "noun", "definition": "a cold-blooded animal that lives in water and breathes with gills."}],
    "horse": [{"pos": "noun", "definition": "a large four-legged animal used for riding and carrying loads."}],
    "cow": [{"pos": "noun", "definition": "a large farm animal kept for its milk and meat."}],
    "happy": [{"pos": "adjective", "definition": "feeling or showing pleasure and joy."}],
    "sad": [{"pos": "adjective", "definition": "feeling unhappiness or sorrow."}],
    "angry": [{"pos": "adjective", "definition": "feeling strong displeasure or annoyance."}],
    "tired": [{"pos": "adjective", "definition": "needing rest or sleep."}],
    "hungry": [{"pos": "adjective", "definition": "feeling a need for food."}],
    "thirsty": [{"pos": "adjective", "definition": "feeling a need for drink."}],
    "good": [{"pos": "adjective", "definition": "of high quality; morally right; pleasing."}],
    "bad": [{"pos": "adjective", "definition": "of poor quality; unpleasant or harmful."}],
    "big": [{"pos": "adjective", "definition": "large in size, amount or degree."}],
    "small": [{"pos": "adjective", "definition": "little in size, amount or degree."}],
    "fast": [{"pos": "adjective", "definition": "moving or able to move quickly."}],
    "slow": [{"pos": "adjective", "definition": "moving or done at a low speed."}],
    "hot": [{"pos": "adjective", "definition": "having a high temperature; very warm."}],
    "cold": [{"pos": "adjective", "definition": "having a low temperature; not warm."}],
    "warm": [{"pos": "adjective", "definition": "comfortably hot; slightly hot."}],
    "cool": [{"pos": "adjective", "definition": "neither warm nor cold; slightly cold."}],
    "new": [{"pos": "adjective", "definition": "recently made, bought or discovered; not old."}],
    "old": [{"pos": "adjective", "definition": "having lived or existed for a long time."}],
    "young": [{"pos": "adjective", "definition": "having lived or existed for only a short time."}],
    "long": [{"pos": "adjective", "definition": "measuring a great distance or length."}],
    "short": [{"pos": "adjective", "definition": "measuring little distance or length."}],
    "high": [{"pos": "adjective", "definition": "extending far upward; great in height."}],
    "low": [{"pos": "adjective", "definition": "close to the ground; not high."}],
    "open": [{"pos": "verb", "definition": "to move something so it no longer blocks a path or entrance."}],
    "close": [{"pos": "verb", "definition": "to shut something so it blocks a path or entrance."}],
    "run": [{"pos": "verb", "definition": "to move quickly on foot, faster than walking."}],
    "walk": [{"pos": "verb", "definition": "to move forward on foot at a normal speed."}],
    "eat": [{"pos": "verb", "definition": "to put food in your mouth, chew and swallow it."}],
    "drink": [{"pos": "verb", "definition": "to take liquid into your mouth and swallow it."}],
    "sleep": [{"pos": "verb", "definition": "to rest with closed eyes while unconscious."}],
    "talk": [{"pos": "verb", "definition": "to speak to someone in conversation."}],
    "speak": [{"pos": "verb", "definition": "to say words; to use your voice to communicate."}],
    "listen": [{"pos": "verb", "definition": "to pay attention to a sound."}],
    "hear": [{"pos": "verb", "definition": "to perceive sound with your ears."}],
    "see": [{"pos": "verb", "definition": "to notice something with your eyes."}],
    "look": [{"pos": "verb", "definition": "to direct your eyes towards something to see it."}],
    "go": [{"pos": "verb", "definition": "to move or travel from one place to another."}],
    "come": [{"pos": "verb", "definition": "to move towards the speaker or a place."}],
    "give": [{"pos": "verb", "definition": "to hand something to someone; to donate."}],
    "take": [{"pos": "verb", "definition": "to pick up or carry something away; to accept."}],
    "help": [{"pos": "verb", "definition": "to make it easier for someone to do something."}],
    "work": [{"pos": "verb", "definition": "to do a job or activity, especially for money."}],
    "play": [{"pos": "verb", "definition": "to take part in a game or enjoyable activity."}],
    "read": [{"pos": "verb", "definition": "to look at and understand written words."}],
    "write": [{"pos": "verb", "definition": "to form letters or words on a surface."}],
    "think": [{"pos": "verb", "definition": "to use your mind to consider or have ideas."}],
    "know": [{"pos": "verb", "definition": "to have information in your mind about something."}],
    "like": [{"pos": "verb", "definition": "to find something pleasant or enjoyable."}],
    "love": [{"pos": "verb", "definition": "to feel deep affection for someone or something."}],
    "hate": [{"pos": "verb", "definition": "to feel strong dislike for something."}],
    "want": [{"pos": "verb", "definition": "to have a desire for something."}],
    "need": [{"pos": "verb", "definition": "to require something because it is essential."}],
    "make": [{"pos": "verb", "definition": "to create, build or produce something."}],
    "beautiful": [{"pos": "adjective", "definition": "very pleasing to look at or listen to; lovely."}],
    "smart": [{"pos": "adjective", "definition": "intelligent and quick at learning things."}],
    "strong": [{"pos": "adjective", "definition": "having great physical power; not weak."}],
    "weak": [{"pos": "adjective", "definition": "lacking strength or power."}],
    "easy": [{"pos": "adjective", "definition": "not difficult; requiring little effort."}],
    "hard": [{"pos": "adjective", "definition": "difficult; firm and solid, not soft."}],
    "light": [{"pos": "noun", "definition": "the brightness that lets you see things, produced by the sun or a lamp."},
             {"pos": "adjective", "definition": "not heavy; easy to carry."}],
    "dark": [{"pos": "adjective", "definition": "with little or no light."}],
    "money": [{"pos": "noun", "definition": "coins and banknotes used to buy things."}],
    "doctor": [{"pos": "noun", "definition": "a person trained to treat sick or injured people."}],
    "teacher": [{"pos": "noun", "definition": "a person whose job is to help others learn."}],
    "music": [{"pos": "noun", "definition": "sounds arranged in patterns using melody, rhythm and harmony."}],
    "song": [{"pos": "noun", "definition": "a short piece of music with words that are sung."}],
    "truth": [{"pos": "noun", "definition": "the actual facts about something; what is real."}],
    "question": [{"pos": "noun", "definition": "a sentence or phrase used to ask for information."}],
    "answer": [{"pos": "noun", "definition": "something said or written in reply to a question."}],
    "dream": [{"pos": "noun", "definition": "a series of thoughts and images seen during sleep."}],
    "hope": [{"pos": "noun", "definition": "a feeling that something good will happen."}],
}

_POS_LABEL = {"n": "noun", "v": "verb", "adj": "adjective", "adv": "adverb", "u": "unknown"}


class Dictionary:
    """Offline-first dictionary backed by SQLite on the AK data drive."""

    def __init__(self):
        root = os.path.join(get_data_dir(), "Dictionary")
        os.makedirs(root, exist_ok=True)
        self.db_path = os.path.join(root, "dict.db")
        self._init_db()
        self._seed_db()

    # ── storage ------------------------------------------------------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS words ("
                " word TEXT PRIMARY KEY,"
                " data TEXT NOT NULL,"
                " fetched_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )

    def _seed_db(self):
        with sqlite3.connect(self.db_path) as conn:
            for word, entries in _SEED.items():
                conn.execute(
                    "INSERT OR IGNORE INTO words (word, data, fetched_at) VALUES (?, ?, ?)",
                    (word, json.dumps({
                        "word": word,
                        "phonetic": "",
                        "entries": entries,
                        "synonyms": [],
                        "antonyms": [],
                        "source": "builtin",
                    }, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
                )

    # ── core lookup ---------------------------------------------------
    def lookup(self, word: str) -> Optional[dict]:
        """Find a word.  Local store → offline WordNet → web API (cached to D drive)."""
        key = word.strip().lower()
        if not key:
            return None
        cached = self._local(key)
        if cached:
            cached["cached"] = True
            # Built-in seed words have definitions but no synonyms/antonyms.
            # Enrich them once (offline via WordNet, else web) — kept forever.
            if cached.get("source") == "builtin" and (
                    not cached.get("synonyms") or not cached.get("antonyms")):
                enriched = self._enrich_related(cached)
                if enriched:
                    cached = enriched
            return cached
        # Full offline WordNet coverage (no internet needed).
        entry = _wordnet_offline(key)
        if entry:
            self._store(key, entry)
            entry["cached"] = False
            return entry
        # Final fallback: free web API, cached forever.
        entry = self._fetch_online(key)
        if entry:
            self._store(key, entry)
            entry["cached"] = False
            return entry
        return None

    def _enrich_related(self, entry: dict) -> Optional[dict]:
        """Merge synonyms/antonyms into an existing (built-in) entry."""
        key = entry.get("word")
        if not key:
            return None
        syn = (entry.get("synonyms")
               or self._related(key, "rel_syn")
               or (_wordnet_offline(key) or {}).get("synonyms") or [])
        ant = (entry.get("antonyms")
               or self._related(key, "rel_ant")
               or (_wordnet_offline(key) or {}).get("antonyms") or [])
        if not syn and not ant:
            return None
        updated = dict(entry)
        updated["synonyms"] = syn
        updated["antonyms"] = ant
        self._store(key, updated)
        return updated

    def _local(self, key: str) -> Optional[dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT data FROM words WHERE word=?", (key,)
                ).fetchone()
            if row:
                return json.loads(row[0])
        except Exception:
            pass
        return None

    def _store(self, key: str, entry: dict) -> None:
        entry["fetched_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO words (word, data, fetched_at) VALUES (?, ?, ?)",
                    (key, json.dumps(entry, ensure_ascii=False), entry["fetched_at"]),
                )
        except Exception:
            pass

    # ── web fetch (Datamuse — free, no key) ----------------------------
    def _fetch_online(self, key: str) -> Optional[dict]:
        q = urllib.parse.quote(key)
        try:
            data = self._get_json(f"https://api.datamuse.com/words?sp={q}&md=dp&max=1")
            if not data or "defs" not in data[0]:
                return None
            top = data[0]
            entries = []
            for d in top.get("defs", []):
                pos, _, definition = d.partition("\t")
                entries.append({
                    "pos": _POS_LABEL.get(pos.strip(), pos.strip() or "unknown"),
                    "definition": definition.strip(),
                })
            if not entries:
                return None
            syn = self._related(key, "rel_syn")
            ant = self._related(key, "rel_ant")
            entry = {
                "word": key,
                "phonetic": "",
                "entries": entries,
                "synonyms": syn,
                "antonyms": ant,
                "source": "datamuse",
            }
            # Make sure the fetched word itself is captured correctly.
            entry["word"] = top.get("word") or key
            return entry
        except Exception:
            return None

    def _related(self, key: str, rel: str, limit: int = 6) -> List[str]:
        q = urllib.parse.quote(key)
        try:
            data = self._get_json(
                f"https://api.datamuse.com/words?{rel}={q}&max={limit}"
            )
            return [w.get("word") for w in data if w.get("word")][:limit]
        except Exception:
            return []

    @staticmethod
    def _get_json(url: str) -> Any:
        req = urllib.request.Request(
            url, headers={"User-Agent": "AK-Voice-Assistant/2.0 (Windows)"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    # ── suggestions (did-you-mean) ------------------------------------
    def suggest(self, word: str, limit: int = 5) -> List[str]:
        q = urllib.parse.quote(word.strip().lower())
        try:
            data = self._get_json(f"https://api.datamuse.com/sug?s={q}&max={limit}")
            return [w.get("word") for w in data if w.get("word")][:limit]
        except Exception:
            # Offline fallback: words in the local store sharing a prefix.
            try:
                with sqlite3.connect(self.db_path) as conn:
                    rows = conn.execute(
                        "SELECT word FROM words WHERE word LIKE ? ORDER BY word LIMIT ?",
                        (word.strip().lower()[:3] + "%", limit),
                    ).fetchall()
                return [r[0] for r in rows]
            except Exception:
                return []

    # ── daily features --------------------------------------------------
    def word_of_day(self) -> dict:
        """Pick a featured word — local, offline, rotates through the store."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT value FROM meta WHERE key='wotd'"
                ).fetchone()
                words = conn.execute(
                    "SELECT word FROM words ORDER BY word"
                ).fetchall()
                last = (rows[0] if rows else "") or ""
            pool = [w[0] for w in words]
            if not pool:
                pool = list(_SEED.keys())
            candidates = [w for w in pool if w != last] or pool
            pick = random.choice(candidates)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('wotd', ?)",
                    (pick,),
                )
            entry = self._local(pick)
            return {"word": pick, "entry": entry}
        except Exception:
            pick = random.choice(list(_SEED.keys()))
            return {"word": pick, "entry": self._local(pick)}

    def random_word(self) -> dict:
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT word, data FROM words ORDER BY RANDOM() LIMIT 1"
                ).fetchone()
            if row:
                return {"word": row[0], "entry": json.loads(row[1])}
        except Exception:
            pass
        pick = random.choice(list(_SEED.keys()))
        return {"word": pick, "entry": self._local(pick)}

    def count(self) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                return conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        except Exception:
            return 0

    def dictionary_path(self) -> str:
        return self.db_path


# ────────────────────────────────────────────────────────────────
#  Pretty formatting helpers
# ────────────────────────────────────────────────────────────────

def spoken_summary(entry: dict, deep: bool = False) -> str:
    """Concise reply that TTS reads aloud."""
    word = entry.get("word", "it")
    entries = entry.get("entries") or []
    lines = []
    for e in entries[:2]:
        pos = e.get("pos") or "word"
        lines.append(f"as a {pos}, {e.get('definition', '').strip()}")
    body = " ".join(lines) or "No definition found."
    parts = [f"{word.capitalize()}: {body}"]
    if entry.get("synonyms"):
        parts.append("Similar words include " + ", ".join(entry["synonyms"][:4]) + ".")
    if entry.get("antonyms"):
        parts.append("The opposite is " + ", ".join(entry["antonyms"][:3]) + ".")
    return " ".join(parts)


def console_detail(entry: dict) -> str:
    """Rich multi-line view printed to the console (ASCII-safe)."""
    word = entry.get("word", "?")
    out = [f"\n{'-'*52}", f"  WORD: {word.capitalize()}"]
    for e in entry.get("entries") or []:
        pos = e.get("pos") or ""
        out.append(f"      {pos:<10} {e.get('definition','').strip()}")
        if e.get("example"):
            out.append(f"      Example: {e['example']}")
    if entry.get("synonyms"):
        out.append("      Synonyms: " + ", ".join(entry["synonyms"][:8]))
    if entry.get("antonyms"):
        out.append("      Antonyms:  " + ", ".join(entry["antonyms"][:8]))
    out.append(f"      source: {entry.get('source')}"
               f" | cached locally: {'yes' if entry.get('cached') else 'no, saved now'}"
               f" | {entry.get('fetched_at','')}")
    out.append(f"{'-'*52}")
    return "\n".join(out)


def spell_word(word: str) -> str:
    clean = "".join(ch for ch in word if ch.isalnum() or ch in "-' ")
    if not clean:
        return "Nothing to spell."
    spoken = " ".join(clean.upper())
    return f"{clean.upper()} - that spells {spoken}."


# ────────────────────────────────────────────────────────────────
#  Daily-life helpers (fully offline)
# ────────────────────────────────────────────────────────────────

_LENGTH = {
    "mm": 0.001, "millimeter": 0.001, "millimeters": 0.001,
    "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01,
    "m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0,
    "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0,
    "inch": 0.0254, "inches": 0.0254, "in": 0.0254,
    "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
    "yd": 0.9144, "yard": 0.9144, "yards": 0.9144,
    "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
}
_MASS = {
    "mg": 1e-6, "milligram": 1e-6, "milligrams": 1e-6,
    "g": 1e-3, "gram": 1e-3, "grams": 1e-3,
    "kg": 1.0, "kilogram": 1.0, "kilograms": 1.0, "kilo": 1.0,
    "t": 1000.0, "tonne": 1000.0, "tonnes": 1000.0, "ton": 1000.0,
    "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "pounds": 0.45359237,
    "oz": 0.028349523125, "ounce": 0.028349523125, "ounces": 0.028349523125,
}
_DATA = {
    "b": 1.0, "byte": 1.0, "bytes": 1.0,
    "kb": 1024.0, "kilobyte": 1024.0, "kilobytes": 1024.0,
    "mb": 1024.0 ** 2, "megabyte": 1024.0 ** 2, "megabytes": 1024.0 ** 2,
    "gb": 1024.0 ** 3, "gigabyte": 1024.0 ** 3, "gigabytes": 1024.0 ** 3,
    "tb": 1024.0 ** 4, "terabyte": 1024.0 ** 4, "terabytes": 1024.0 ** 4,
}
_TEMP = {"c": "celsius", "celsius": "celsius", "centigrade": "celsius",
         "f": "fahrenheit", "fahrenheit": "fahrenheit",
         "k": "kelvin", "kelvin": "kelvin"}

_UNIT_LABEL = {"celsius": "C", "fahrenheit": "F", "kelvin": "K"}


def is_known_unit(name: str) -> bool:
    """True if the given string is a unit we can convert (offline)."""
    return name.lower().strip() in KNOWN_UNITS


KNOWN_UNITS = set(_LENGTH) | set(_MASS) | set(_DATA) | set(_TEMP)


def convert_units(value: float, from_unit: str, to_unit: str) -> Tuple[str, dict]:
    fu = from_unit.lower().strip()
    tu = to_unit.lower().strip()
    # temperature family
    if fu in _TEMP and tu in _TEMP:
        celsius = {"celsius": lambda c: c, "fahrenheit": lambda f: (f - 32) * 5 / 9,
                   "kelvin": lambda k: k - 273.15}[_TEMP[fu]](value)
        out = {"celsius": celsius, "fahrenheit": celsius * 9 / 5 + 32,
               "kelvin": celsius + 273.15}[_TEMP[tu]]
        msg = f"{value:g} {fu} is {round(out, 2)} {tu}."
        return msg, {"value": out, "unit": _TEMP[tu]}

    def _convert(table: dict, table_name: str):
        if fu not in table or tu not in table:
            return None
        out = value * table[fu] / table[tu]
        msg = f"{value:g} {fu} is {out:g} {tu}."
        return msg, {"value": out, "unit": tu}

    for table, name in ((_LENGTH, "length"), (_MASS, "weight"), (_DATA, "data")):
        res = _convert(table, name)
        if res:
            return res
    return ("I don't recognise those units. Try e.g.: convert 10 km to miles, "
            "convert 100 celsius to fahrenheit, or convert 5 gb to mb.", {})


def age_from_birth_year(year: int) -> Tuple[str, int]:
    today = date.today()
    age = today.year - year
    return (f"You are {age} years old.", age)


def bmi_calc(weight_kg: float, height_m: float) -> Tuple[str, float]:
    if height_m <= 0 or weight_kg <= 0:
        return "Height and weight must be positive numbers.", 0.0
    bmi = weight_kg / (height_m ** 2)
    if bmi < 18.5:
        cat = "underweight"
    elif bmi < 25:
        cat = "a healthy weight"
    elif bmi < 30:
        cat = "overweight"
    else:
        cat = "obese"
    return (f"Your BMI is {bmi:.1f}, which is {cat}.", round(bmi, 1))