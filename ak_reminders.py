"""Reminder calls for AK Mobile.

Flow:
  user: "5 baje call karke yaad dilana ki meeting hai"
  AK:   "Theek hai, main aaj shaam 5:00 baje call karke yaad dilaunga:
         'meeting hai'. Confirm karne ke liye haan bolo."
  user: "haan"
  AK:   "Done boss. ..."  + reminder payload for the phone (alarm set)

The server stores reminders under security/reminders.json. The phone sets an
AlarmManager alarm and rings itself at due time; the call then triggers
/api/call/trigger with reminder_id and AK speaks the reminder.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_STORE = os.path.join(_HERE, "security", "reminders.json")

_WORD_NUM = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5,
    "panch": 5, "chhe": 6, "chhah": 6, "che": 6, "saat": 7, "aath": 8,
    "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "aadha": 0.5,
}

_MORNING = ("subah", "savere", "subha", "morning", "nashte")
_AFTERNOON = ("dopahar", "afternoon")
_EVENING = ("shaam", "sham", "evening", "sandhya")
_NIGHT = ("raat", "night", "tonight", "raat ko")

_PENDING_TTL = 15 * 60

INTENT_RE = re.compile(
    r"\b(?:yaad\s*dil\w*|"
    r"remind(?:er)?(?:\s+me)?(?:\s+to)?|"
    r"call\s*karke\s*(?:bata\w*|yaad\s*dil\w*))",
    re.I,
)
LIST_RE = re.compile(
    r"^(?:(?:mere|mera|sab|all|upcoming|aaj\s+ke)\s+)?"
    r"(?:reminders?|yaad\s*dilane\s+wale)\s*"
    r"(?:batao|bata|dikhao|dikha|list|show|check|kya\s+hai|kaunse)?$|"
    r"\b(?:reminders?|yaad\s*dilane\s+wale)\b\s*(?:batao|bata|dikhao|dikha|list|show|check)\b|"
    r"\b(?:batao|dikhao|list|show|check)\b.*\b(?:reminders?)\b",
    re.I,
)
CANCEL_RE = re.compile(
    r"\b(?:cancel|hatao|delete|band)\b.*\b(?:reminder|yaad)\b|"
    r"\b(?:reminder|yaad)\b.*\b(?:cancel|hatao|delete|band)\b",
    re.I,
)
YES_RE = re.compile(
    r"^(?:haan|han|haa|ha|yes|yep|ok|okay|theek|thik|sahi|confirm|"
    r"kar\s*do|kardo|karo|done|bilkul|pakka|acha|accha)\b",
    re.I,
)
NO_RE = re.compile(
    r"^(?:nahi|nahin|na|no|nope|cancel|mat|rehne|skip|abhi nahi)\b",
    re.I,
)


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("â€™", "'").replace("â€˜", "'")
    t = re.sub(r"[?!,;]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fmt(due: datetime) -> str:
    now = datetime.now()
    days = (due.date() - now.date()).days
    day = {0: "aaj", 1: "kal", 2: "parso"}.get(days)
    if day is None:
        day = due.strftime("%d %B")
    hour = due.hour
    if 5 <= hour < 12:
        part = "subah"
    elif 12 <= hour < 16:
        part = "dopahar"
    elif 16 <= hour < 20:
        part = "shaam"
    else:
        part = "raat"
    h12 = due.hour % 12 or 12
    return f"{day} {part} {h12}:{due.strftime('%M')}"


def _parse_relative(t: str):
    """Return (due, span) for '10 minute baad' / 'do ghante baad'."""
    now = datetime.now()
    pat = re.compile(
        r"(?:(?:in|after)\s+)?"
        r"(\d{1,3}|" + "|".join(_WORD_NUM) + r")"
        r"\s*(?:minutes?|mins?|minit|mint)\s*"
        r"(?:baad|bad|later|mein|me|after)?",
        re.I,
    )
    m = pat.search(t)
    if m:
        raw = m.group(1)
        num = _WORD_NUM.get(raw, None)
        if num is None:
            num = int(raw)
        minutes = float(num)
        if minutes > 0:
            return now + timedelta(minutes=minutes), m.span()
    pat_h = re.compile(
        r"(?:(?:in|after)\s+)?"
        r"(\d{1,2}(?:\.\d)?|aadha|half|" + "|".join(_WORD_NUM) + r")"
        r"\s*(?:ghant[ae]|ghanta|hours?|hrs?|hr)\s*"
        r"(?:baad|bad|later|mein|me|after)?",
        re.I,
    )
    m = pat_h.search(t)
    if m:
        raw = m.group(1)
        if raw in ("aadha", "half"):
            hours = 0.5
        else:
            hours = _WORD_NUM.get(raw)
            if hours is None:
                hours = float(raw)
        if hours > 0:
            return now + timedelta(hours=hours), m.span()
    return None


def _parse_absolute(t: str):
    now = datetime.now()
    m = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(?:baje|bje|o'?clock)\b", t, re.I
    )
    span = None
    ampm = ""
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        span = m.span()
    else:
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            span = m.span()
        else:
            m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t, re.I)
            if not m:
                return None
            hour = int(m.group(1))
            minute = 0
            ampm = m.group(2).lower()
            span = m.span()
    if hour > 23 or minute > 59:
        return None

    tail = t[m.end():m.end() + 4] if m else ""
    if re.match(r"\s*(am|pm)\b", tail, re.I):
        ampm = re.match(r"\s*(am|pm)\b", tail, re.I).group(1).lower()

    period = ""
    for word in _MORNING + _AFTERNOON + _EVENING + _NIGHT:
        if re.search(r"\b" + re.escape(word) + r"\b", t):
            period = word
            break

    if ampm == "pm":
        hour = hour % 12 + 12
    elif ampm == "am":
        hour = hour % 12
    elif period in _MORNING:
        hour = hour % 12
    elif period in _AFTERNOON or period in _EVENING:
        hour = hour % 12 + 12
    elif period in _NIGHT:
        if hour == 12:
            hour = 0
        elif 6 <= hour < 12:
            hour += 12
    else:
        if 1 <= hour <= 6:
            if 18 <= now.hour or now.hour <= 4:
                pass
            else:
                hour += 12
        elif hour == 12:
            hour = 12

    day_off = 0
    if re.search(r"\b(?:parso)\b", t):
        day_off = 2
    elif re.search(r"\b(?:kal|tomorrow)\b", t):
        day_off = 1
    elif re.search(r"\b(?:aaj|today)\b", t):
        day_off = 0
    if period in _NIGHT and hour == 0 and day_off == 0:
        day_off = 1

    due = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    due += timedelta(days=day_off)
    if day_off == 0 and due <= now + timedelta(seconds=20):
        due += timedelta(days=1)
    return due, span


def _parse_when(text: str):
    """Return (due, span) or None. Relative first, then clock time."""
    t = _norm(text)
    rel = _parse_relative(t)
    if rel:
        due, span = rel
        if due > datetime.now() + timedelta(seconds=15):
            return due, span
    absr = _parse_absolute(t)
    if absr:
        return absr
    return None


def _extract_title(text: str, span=None) -> str:
    t = _norm(text)
    if span:
        t = (t[:span[0]] + " " + t[span[1]:]).strip()
    t = INTENT_RE.sub(" ", t)
    t = re.sub(r"\b(?:please|plz|remind|reminder)\b", " ", t, flags=re.I)
    t = re.sub(
        r"^\s*(?:me\s+to|to|that|ki|at|for|:|-)\s+",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"\b(?:aaj|kal|parso|today|tomorrow|subah|savere|subha|morning|"
        r"dopahar|afternoon|shaam|sham|evening|raat|night|tonight|"
        r"baje|bje|am|pm)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"\b(?:mujhe|mujhko|mera|meri|boss|yaar|bhai|ak)\b", " ", t, flags=re.I
    )
    t = re.sub(
        r"\b(?:reminder|set|lagao|lagana|schedule|kardo|kar\s*do|"
        r"kar\s*dena)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"\b(?:karo|kar\s*do|kar\s*dena|bolo|batao)\s*$", " ", t, flags=re.I
    )
    t = re.sub(
        r"\s+(?:at|on|for|to|se|tak|ki|ka|ko|me|mein)\s*$", " ", t, flags=re.I
    )
    t = re.sub(
        r"^\s*(?:(?:karo|kar\s*do|kar\s*dena|set|reminder|ki|that|to|me|mein|"
        r"at|for|of)\s+)+",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"[^0-9a-zA-Z\u0900-\u097F\s'.-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-")
    if not t or t.lower() in ("hai", "h", "kuch", "baat"):
        t = "kuch zaroori baat"
    return t[:160]


class ReminderStore:
    def __init__(self, path: str = _STORE):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                self._data = raw
            elif isinstance(raw, list):
                self._data = {r.get("id"): r for r in raw if r.get("id")}
        except Exception:
            self._data = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            print(f"[AK Reminder] save failed: {exc}")

    def create(self, title: str, due: datetime, speak: str = "",
               device: str = "") -> dict:
        rid = "r_" + secrets.token_hex(4)
        rec = {
            "id": rid,
            "title": title,
            "speak": speak or f"Boss, yaad dilana tha: {title}.",
            "due_ts": int(due.timestamp()),
            "due_text": _fmt(due),
            "created_ts": int(time.time()),
            "status": "scheduled",
            "device": device,
        }
        with self._lock:
            self._prune()
            self._data[rid] = rec
            self._save()
        return rec

    def _prune(self):
        cutoff = time.time() - 30 * 86400
        for rid, rec in list(self._data.items()):
            if rec.get("status") != "scheduled" and rec.get("created_ts", 0) < cutoff:
                self._data.pop(rid, None)

    def upcoming(self) -> list:
        now = time.time()
        with self._lock:
            items = [r for r in self._data.values()
                     if r.get("status") == "scheduled" and r.get("due_ts", 0) > now - 120]
        return sorted(items, key=lambda r: r.get("due_ts", 0))

    def all_reminders(self) -> list:
        with self._lock:
            items = list(self._data.values())
        return sorted(items, key=lambda r: r.get("due_ts", 0))

    def get(self, rid: str) -> dict:
        with self._lock:
            return dict(self._data.get(rid or "") or {})

    def due(self, rid: str) -> dict | None:
        with self._lock:
            rec = self._data.get(rid or "")
            if not rec:
                return None
            rec["status"] = "done"
            rec["done_ts"] = int(time.time())
            self._save()
            return dict(rec)

    def cancel(self, rid: str) -> bool:
        with self._lock:
            rec = self._data.get(rid or "")
            if not rec or rec.get("status") != "scheduled":
                return False
            rec["status"] = "cancelled"
            self._save()
            return True

    def cancel_all(self) -> int:
        n = 0
        with self._lock:
            for rec in self._data.values():
                if rec.get("status") == "scheduled":
                    rec["status"] = "cancelled"
                    n += 1
            if n:
                self._save()
        return n


class ReminderService:
    def __init__(self, path: str = _STORE):
        self.store = ReminderStore(path)
        self._pending: dict = {}
        self._lock = threading.Lock()

    # â”€â”€ public API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def handle(self, text: str, key: str = "chat") -> tuple[str | None, dict | None]:
        """Return (reply, payload).

        payload: {"reminder": {...}} when scheduled, or {"cancelled": n}.
        reply is None when the message is not reminder related.
        """
        text = (text or "").strip()
        if not text:
            return None, None
        norm = _norm(text)
        with self._lock:
            pending = self._pending.get(key)
            if pending and time.time() - pending["at"] > _PENDING_TTL:
                self._pending.pop(key, None)
                pending = None

        if pending and (LIST_RE.search(norm) or CANCEL_RE.search(norm)):
            with self._lock:
                self._pending.pop(key, None)
            pending = None

        if pending:
            result = self._continue_pending(key, norm, text, pending)
            if result is not None:
                return result

        if LIST_RE.search(norm):
            return self._list_reply()

        if CANCEL_RE.search(norm):
            return self._cancel_reply(norm)

        if self._is_create_intent(norm):
            return self._start_create(key, norm, text)

        return None, None

    def due_for_call(self, rid: str) -> dict | None:
        return self.store.due(rid)

    def upcoming(self) -> list:
        return self.store.upcoming()

    # â”€â”€ internals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @staticmethod
    def _is_create_intent(t: str) -> bool:
        if INTENT_RE.search(t):
            return True
        return bool(re.search(r"\breminder\b", t))

    def _continue_pending(self, key: str, norm: str, text: str,
                          pending: dict):
        if NO_RE.search(norm):
            with self._lock:
                self._pending.pop(key, None)
            return "Theek hai boss, reminder cancel kar diya.", None

        when = _parse_when(text)
        if pending.get("missing") == "what":
            if NO_RE.search(norm):
                with self._lock:
                    self._pending.pop(key, None)
                return "Theek hai boss, cancel kar diya.", None
            title = _extract_title(text)
            if title == "kuch zaroori baat":
                return ("Kya yaad dilana hai? Jaise 'meeting hai' ya "
                        "'dawai leni hai'."), None
            pending.update({"title": title, "missing": "", "at": time.time()})
            due = datetime.fromtimestamp(pending["due_ts"])
            return self._ask_confirm(due, title), None

        if pending.get("missing") == "time":
            if not when:
                return ("Kitne baje ya kitne der baad? Jaise '5 baje' "
                        "ya '10 minute baad'."), None
            due, span = when
            title = pending.get("title") or _extract_title(text, span)
            if title == "kuch zaroori baat":
                pending.update({"due": due, "due_ts": int(due.timestamp()),
                                "missing": "what", "at": time.time()})
                return ("Theek hai. Ab batao kya yaad dilana hai? "
                        "Jaise 'meeting hai'."), None
            pending.update({"due": due, "due_ts": int(due.timestamp()),
                            "title": title, "missing": "",
                            "at": time.time()})
            return self._ask_confirm(due, title), None

        if YES_RE.search(norm):
            due = datetime.fromtimestamp(pending["due_ts"])
            title = pending.get("title") or "kuch zaroori baat"
            speak = f"Boss, yaad dilana tha: {title}."
            rec = self.store.create(title, due, speak)
            with self._lock:
                self._pending.pop(key, None)
            payload = {"reminder": rec}
            reply = (f"Done boss. Main {rec['due_text']} aapko call karke "
                     f"yaad dilaunga: {title}.")
            return reply, payload

        if when:
            due, span = when
            title = _extract_title(text, span)
            if not title or title == "kuch zaroori baat":
                title = pending.get("title") or title
            pending.update({"due": due, "title": title, "missing": "",
                            "at": time.time()})
            return self._ask_confirm(due, title), None

        if INTENT_RE.search(norm):
            with self._lock:
                self._pending.pop(key, None)
        else:
            return ("Pehle confirm karo : haan ya nahi? "
                    "Ya naya time bolo, jaise '6 baje'."), None
        return None

    def _start_create(self, key: str, norm: str, text: str):
        when = _parse_when(text)
        if not when:
            title = _extract_title(text)
            with self._lock:
                self._pending[key] = {
                    "missing": "time", "title": title, "at": time.time(),
                }
            return ("Zaroor boss. Kis waqt call karke yaad dilaun? "
                    "Jaise '5 baje', 'kal subah 9 baje' ya '10 minute baad'."), None
        due, span = when
        title = _extract_title(text, span)
        if title == "kuch zaroori baat":
            with self._lock:
                self._pending[key] = {
                    "missing": "what", "title": "",
                    "due": due, "due_ts": int(due.timestamp()),
                    "at": time.time(),
                }
            return ("Theek hai, waqt note kar liya. Ab batao kya yaad "
                    "dilana hai? Jaise 'meeting hai'."), None
        with self._lock:
            self._pending[key] = {
                "missing": "", "title": title,
                "due": due, "due_ts": int(due.timestamp()),
                "at": time.time(),
            }
        return self._ask_confirm(due, title), None

    @staticmethod
    def _ask_confirm(due: datetime, title: str) -> str:
        return (f"Theek hai, main {_fmt(due)} aapko call karke yaad "
                f"dilaunga: {title}. Confirm karne ke liye haan bolo, "
                f"ya nahi.")

    def _list_reply(self):
        items = self.store.upcoming()
        if not items:
            return "Abhi koi reminder set nahi hai boss.", None
        lines = [f"- {it['due_text']}: {it['title']}" for it in items[:8]]
        return ("Aapke reminders:\n" + "\n".join(lines)), None

    def _cancel_reply(self, norm: str):
        if re.search(r"\b(?:sab|all|saare)\b", norm):
            n = self.store.cancel_all()
            if n:
                return f"Theek hai, {n} reminder cancel kar diye.", {"cancelled": n}
            return "Koi active reminder nahi tha boss.", None
        items = self.store.upcoming()
        if not items:
            return "Koi active reminder nahi hai boss.", None
        target = items[0]
        rid = None
        m = re.search(r"\b(r_[0-9a-f]{6,})\b", norm)
        if m:
            rid = m.group(1)
        for it in items:
            if it["id"] == rid or it["title"].lower() in norm:
                target = it
                rid = it["id"]
                break
        if rid is None:
            rid = target["id"]
        if self.store.cancel(rid):
            return (f"Cancel kar diya: {target['due_text']} : "
                    f"{target['title']}."), {"cancelled": rid}
        return "Cancel nahi ho paya boss.", None


REMINDERS = ReminderService()

