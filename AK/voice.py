"""Voice I/O: text-to-speech (TTS) and speech-to-text (STT).

TTS supports switchable Hindi/Hinglish-capable voice profiles via
edge-tts (Microsoft neural voices, i.e. hi-IN + en-IN) and Windows SAPI.
You can switch voices at startup or during conversation by saying
"switch voice" / "change voice".

STT -> Google web API is the DEFAULT listener (stt_engine: "google") via
      sounddevice + SpeechRecognition. Local faster-whisper is only a
      non-blocking safety net: it is never loaded on the hot path and
      Google is never silently downgraded away. Typed input is last resort.

No paid services and no API keys are required for any of this.
"""

from __future__ import annotations

import asyncio
import builtins
import difflib
import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import time
import threading
from typing import Optional

import sounddevice as sd

# How similar a spoken voice name must be to a real profile name before
# switch_voice() accepts it. Tuned so a near-miss still reaches the intended
# voice while genuinely unknown words ("zorp") still report as unknown.
_VOICE_FUZZY_MIN = 0.68


# ═══════════════════════════ Terminal typing ═══════════════════════════

# One lock for the whole console. The typing effect writes a line one
# character at a time from a background thread; without this, any other
# thread's print() lands in the MIDDLE of it and the line comes out mangled
# (e.g. "[[voice] Speaking via edge: Anmol Tyagi\nAK] Always listening."). A
# re-entrant lock keeps same-thread nested prints safe, and `print` itself is
# patched once so every print in the app takes the lock.
_OUT_LOCK = threading.RLock()
# typewrite() holds _OUT_LOCK for a whole line, so every other print waits
# behind it. Cap how long one line may hold the console — a long reply still
# types smoothly but never stalls the rest of the output for many seconds.
_TYPE_MAX_SECONDS = 4.0
_print_orig = builtins.print
if getattr(_print_orig, "_ak_locked", False):
    # Already patched (module reloaded) — keep the original, never stack.
    _print_orig = getattr(_print_orig, "__wrapped__", _print_orig)


def safe_print(*args, **kwargs) -> None:
    """print() that never interleaves with an in-progress typed line."""
    with _OUT_LOCK:
        _print_orig(*args, **kwargs)


safe_print._ak_locked = True
safe_print.__wrapped__ = _print_orig
builtins.print = safe_print


def _stdout_safe(s: str) -> str:
    """Text made safe for the current console encoding (cp1252 crashes on
    Devanagari) — same idea as the STT-safe helper lower down."""
    enc = (getattr(sys.stdout, "encoding", None) or "ascii")
    try:
        return s.encode(enc, "replace").decode(enc, "replace")
    except Exception:
        return s.encode("ascii", "replace").decode("ascii")


def _console_can_type() -> bool:
    """A typing effect only makes sense on a real interactive console, never
    on a pipe/redirect — logs stay instant and copyable there."""
    try:
        return bool(getattr(sys.stdout, "isatty", lambda: False)())
    except Exception:
        return False


def typewrite(text: str, prefix: str = "", delay: float = 0.012,
              end: str = "\n", interrupted=None) -> None:
    """Print `text` with a smooth typing effect; `prefix` shows at once.

    The WHOLE line is written while holding _OUT_LOCK, so no other thread's
    print() can land in the middle of it (that was mangling lines into
    "[[voice] ...\\nAK] ..."). Other console output therefore appears right
    after this line instead of inside it.

    Falls back to a plain print when the effect is off (delay <= 0), stdout
    is not a console, or anything goes wrong — output is never lost. If
    `interrupted()` returns True mid-way, the rest is flushed instantly so
    the whole line still appears.
    """
    safe = _stdout_safe(f"{prefix}{text}")
    if delay > 0 and safe:
        # Never hold the console longer than _TYPE_MAX_SECONDS for one line.
        cap = _TYPE_MAX_SECONDS / max(1, len(safe))
        if delay > cap:
            delay = cap
    if delay <= 0 or not _console_can_type():
        try:
            print(safe, end=end, flush=True)
        except Exception:
            pass
        return
    try:
        with _OUT_LOCK:
            for i, ch in enumerate(safe):
                if interrupted is not None and interrupted():
                    sys.stdout.write(safe[i:])
                    break
                sys.stdout.write(ch)
                sys.stdout.flush()
                time.sleep(delay)
            sys.stdout.write(end)
            sys.stdout.flush()
    except Exception:
        try:
            print(_stdout_safe(f"{prefix}{text}"), end=end, flush=True)
        except Exception:
            pass

# ═══════════════════════════════ Wake word ═══════════════════════════════

WAKE_PHRASES = ("ak", "hey ak", "hello ak", "wake up ak", "wakeup ak",
                "ak wake up", "wake ak", "hey ak are you there",
                "ak ji", "hello ak ji")

# Ordered longest-first so a specific phrase always wins over a bare "ak"
# that may sit inside it (e.g. "wake up ak" must strip before "ak").
WAKE_PHRASES = tuple(sorted(set(WAKE_PHRASES), key=lambda p: -len(p)))

_SCANNED_WAKES = tuple(p for p in WAKE_PHRASES if " " in p)

# Whisper rarely writes the bare "ak" as "ak" - it comes out as "aka", "a.k.",
# "ak." or "a k" depending on accent and room noise. Without this, a correct
# utterance is thrown away and AK has to be asked all over again.
_WAKE_LOOSE = re.compile(r"\b(?:a[\s.\-]*k|aka|aq|ack|ark|okay|ok)\b")
_WAKE_VARIANTS = ("aka", "a k", "ak", "aq", "ack", "ark", "hey ak", "hi ak",
                  "ak ji", "okay", "ok")

SLEEP_PHRASES = ("stop", "hold on", "hold", "go to sleep", "sleep now",
                 "background", "take a break", "so ja", "sone ja",
                 "good night", "band karo", "chup ho jao")

# Phrases the user can say WHILE AK is speaking to cut it off instantly
# (barge-in / interrupt).  Kept deliberately wide: Hindi, Hinglish and English
# spellings, plus the common STT spelling variants (jao / jaa / ja / jaye).
# Matching is word-bounded, so short words are safe (see _wake_hit).
INTERRUPT_PHRASES = (
    # ── English ──
    "wait", "wait wait", "wait ak", "waiting", "stop", "stop it", "stop ak",
    "pause", "pause it", "hold on", "hold up", "hold it", "shut up",
    "quiet", "be quiet", "silence", "silence karo", "enough", "thats enough",
    "thats enough", "never mind", "nevermind", "forget it", "cancel",
    "skip", "skip this", "abort", "mute", "mute karo", "silence please",
    # ── ruk / roko family ──
    "ruk", "ruko", "ruku", "rukoo", "ruk jao", "ruk jaa", "ruk ja",
    "ruk jaye", "ruk jaye na", "ruk jao na", "ruk jao please", "ruk na",
    "ruk ke ruko", "ruk ke", "rukna", "ruk gaye", "ruk gaya",
    "please ruko", "thoda ruk", "thodi der ruk", "thodi der",
    # ── rok family ──
    "rok", "roko", "rok do", "rok de", "rok de do", "rok na",
    # ── bas ──
    "bas", "bas karo", "bas kar", "bas ho jao", "bas ho ja",
    "bas ho gaya", "bas yaar", "bas please", "enough ho jao",
    # ── chup ──
    "chup", "chup karo", "chup ho jao", "chup ho ja", "chup raho",
    "chup ho", "chup kar do",
    # ── shaant ──
    "shant", "shaant", "shant ho jao", "shant ho ja", "shant karo",
    "shaant ho jao", "shaant ho ja", "shant ho", "shant ho jao please",
    # ── rehne do ──
    "rehne do", "rehne dena", "reh jao", "reh jaye", "rehna",
    "reh jao please", "rehne do please", "ruk jao yahan",
    # ── chor do ──
    "chor do", "chhod do", "chod do", "choro", "chhor do", "chor de",
    "chor diya", "chhod diya",
    # ── band / awaaz ──
    "band karo", "band kar do", "band kar", "awaaz band", "awaz band",
    "awaaz band karo", "band it", "awaaz rakh",
    # ── khatam / ho gaya ──
    "khatam", "khatam karo", "ho gaya", "ho gaya na", "ho gaya to",
    "khatam ho gaya", "bas ho gaya na",
    # ── jaldi ──
    "jaldi", "jaldi karo", "jaldi kar", "jaldi kar do", "foran ruko",
    # ── ek second ──
    "ek second", "ek second ruko", "ek moment", "ek pal",
    "one second", "one moment", "just a second", "a second",
)


_HIT_CACHE: dict = {}


def _hit_re(phrases: tuple):
    """Compiled word-bounded matcher for a phrase tuple (cached).

    Every phrase is anchored with \\b on both sides, so a bare 'bas' can never
    fire inside 'basketball' and 'ruk' never fires inside 'rukhsana'.  This
    matters a lot now that the interrupt list contains many short words."""
    rx = _HIT_CACHE.get(phrases)
    if rx is None:
        parts = [rf"\b{re.escape(p.strip())}\b"
                 for p in phrases if p and p.strip()]
        rx = re.compile("|".join(parts), re.IGNORECASE) if parts else None
        _HIT_CACHE[phrases] = rx
    return rx


def _wake_hit(text: str, phrases: tuple = WAKE_PHRASES) -> bool:
    """True if the spoken text contains a real wake/interrupt phrase.

    Word-bounded for every phrase (single or multi-word), so short names like
    'ak' can't spuriously match inside 'background' / 'black'.

    Whisper spells the bare 'ak' several ways depending on accent and noise
    ('aka', 'a.k.', 'ak.', 'a k'), and a missed wake word is what made AK look
    broken, so those spellings count as the same word.
    """
    tl = (text or "").lower().strip()
    if not tl:
        return False
    rx = _hit_re(tuple(phrases))
    if rx and rx.search(tl):
        return True
    return bool(_WAKE_LOOSE.search(tl))


def strip_wake(text: str, phrases: tuple = WAKE_PHRASES) -> str:
    """Everything said after the wake phrase, or '' if there is no wake word.

    Uses the same loose spellings as _wake_hit, so a command captured in the
    same breath as the wake word is never dropped - that used to force the
    user to repeat the whole sentence.
    """
    if not text:
        return ""
    tl = text.lower()
    for p in phrases:
        m = re.search(rf"\b{re.escape(p)}\b", tl)
        if m:
            return re.sub(r"^[\s\W_]+", "", text[m.end():]).strip()
    m = _WAKE_LOOSE.search(tl)
    if m:
        return re.sub(r"^[\s\W_]+", "", text[m.end():]).strip()
    return ""


# ═══════════════════════════════ TTS ═══════════════════════════════

VOICE_PROFILES = [
    # Every profile gets a UNIQUE voice that handles Hinglish/Hindi well:
    # hi-IN = pure Hindi neural, en-IN = Indian English, *Multilingual =
    # code-switch Hinglish voices, Indian-regional = distinct accents.
    # Anmol Tyagi stays exactly as before.
    {"name": "Anmol",   "gender": "Male",   "engine": "edge", "id": "hi-IN-MadhurNeural"},
    {"name": "Madhur",  "gender": "Male",   "engine": "edge", "id": "en-US-AndrewMultilingualNeural"},
    {"name": "Male Voice 2",  "gender": "Male",   "engine": "edge", "id": "en-US-BrianMultilingualNeural"},
    {"name": "Male Voice 3",   "gender": "Male",   "engine": "edge", "id": "en-IN-PrabhatNeural"},
    {"name": "Male Voice 4",   "gender": "Male",   "engine": "edge", "id": "mr-IN-ManoharNeural"},
    {"name": "Male Voice 5", "gender": "Male",   "engine": "edge", "id": "gu-IN-NiranjanNeural"},
    # Females
    {"name": "Female Voice 2",   "gender": "Female", "engine": "edge", "id": "hi-IN-SwaraNeural"},
    {"name": "Female Voice 3",  "gender": "Female", "engine": "edge", "id": "en-US-EmmaMultilingualNeural"},
    {"name": "Female Voice 4",    "gender": "Female", "engine": "edge", "id": "en-IN-NeerjaNeural"},
    {"name": "Female Voice 5", "gender": "Female", "engine": "edge", "id": "en-US-AvaMultilingualNeural"},
    {"name": "Female Voice 6",  "gender": "Female", "engine": "edge", "id": "en-IN-NeerjaExpressiveNeural"},
    # A custom cloned voice profile — speaks Hindi/Hinglish with a local voice model.
    {"name": "MY VOICE", "gender": "Your own voice", "engine": "xtts", "id": "myvoice"},
]


class Speaker:
    """Text-to-speech with 7 switchable voices (David, Zira, Guy, Brian,
    Ryan, Jenny, Aria).  Falls back gracefully to console output."""

    def __init__(self, cfg: dict, cfg_path: str = ""):
        self._method = "print"
        self._sapi = None
        self._cfg = cfg
        self._cfg_path = cfg_path
        self._rate = cfg.get("tts_rate", 180)
        self._volume = cfg.get("tts_volume", 1.0)
        # Terminal typing effect for the conversation (voice.type_effect).
        self._type_effect = bool(cfg.get("type_effect", True))
        self._type_speed = max(0.0, float(cfg.get("type_speed", 12)))
        self._type_max = max(0, int(cfg.get("type_max_chars", 600)))
        self._typing_thread = None
        self._edge_ok = False
        self._edge_offline = False  # internet failed → use local SAPI
        self._on_audio_start = None
        self._on_audio_end = None
        self.interrupted = False
        self._active_stream = None

        # Try loading edge-tts
        try:
            import edge_tts  # noqa: F401
            self._edge_ok = True
        except ImportError:
            pass

        # Try SAPI
        try:
            import win32com.client
            self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
            self._sapi.Volume = 100
            self._sapi.Rate = max(-10, min(10, (self._rate - 100) // 10))
            self._method = "sapi"
            print("[voice] TTS: SAPI.SpVoice (COM)")
        except Exception:
            pass

        # Try pyttsx3 fallback
        if self._method == "print":
            try:
                import pyttsx3
                self._sapi = pyttsx3.init()
                self._sapi.setProperty("rate", self._rate)
                self._sapi.setProperty("volume", self._volume)
                self._method = "pyttsx3"
                print("[voice] TTS: pyttsx3")
            except Exception:
                pass

        if self._method == "print":
            print("[voice] TTS unavailable; text output only.")

        # Any voice pack installed on Windows (SAPI) becomes selectable too.
        self._profiles = list(VOICE_PROFILES)
        self._extend_sapi_profiles()

        # Current voice index (default 0 = Anmol Tyagi)
        pref = (cfg.get("preferred_voice") or "").strip().lower()
        self._current = 0
        if pref:
            for i, v in enumerate(self._profiles):
                if v["name"].lower() == pref or v["id"].lower() == pref:
                    self._current = i
                    break

    # -- voice management -----------------------------------------------

    def _extend_sapi_profiles(self) -> None:
        """Auto-discover voice packs installed on Windows (SAPI voices) and
        add them as selectable profiles, so any installed/custom voice works."""
        if self._method != "sapi":
            return
        try:
            voices = self._sapi.GetVoices()
        except Exception:
            return
        if voices is None or getattr(voices, "Count", 0) == 0:
            return
        known = {v["name"].lower() for v in self._profiles}
        for i in range(voices.Count):
            try:
                v = voices.Item(i)
                name = (v.GetAttribute("Name") or "").strip()
                if not name or name.lower() in known:
                    continue
                gender = (v.GetAttribute("Gender") or "").strip()
                if gender not in ("Male", "Female"):
                    gender = "Male" if "david" in name.lower() else "Female"
                known.add(name.lower())
                self._profiles.append({
                    "name": name, "gender": gender,
                    "engine": "sapi", "id": name, "custom": True,
                })
                print(f"[voice] Custom SAPI voice added: {name} ({gender})")
            except Exception:
                continue

    @property
    def current_voice(self) -> dict:
        return self._profiles[self._current]

    @property
    def current_voice_name(self) -> str:
        return self.current_voice["name"]

    def list_voices(self) -> str:
        lines = []
        for i, v in enumerate(self._profiles):
            mark = " <-- current" if i == self._current else ""
            extra = " (installed)" if v.get("custom") else ""
            lines.append(f"  {i+1}. {v['name']} ({v['gender']}){extra}{mark}")
        return "Available voices:\n" + "\n".join(lines)

    def switch_voice(self, identifier: str) -> str:
        """Switch by name (case-insensitive) or 1-based number.
        Returns a confirmation message.  The choice is persisted to the
        config file so it survives a restart.

        Names arrive from speech recognition, so exact matching is not enough:
        Whisper turns a spoken voice name into a near-miss often enough that a
        hard failure here is a routine annoyance. Matches therefore fall back
        from exact, to substring, to edit-distance, before giving up.
        """
        t = self._clean_voice_query(identifier)
        chosen = None
        # Try 1-based index
        if t.isdigit():
            idx = int(t) - 1
            if 0 <= idx < len(self._profiles):
                chosen = idx
        if chosen is None:
            for i, v in enumerate(self._profiles):
                if v["name"].lower() == t:
                    chosen = i
                    break
        if chosen is None:
            for i, v in enumerate(self._profiles):
                if t in v["name"].lower():
                    chosen = i
                    break
        if chosen is None:
            # Fuzzy: closest name wins, but only when it is genuinely close.
            # The margin guard stops a weak match on one letter from stealing a
            # request that should have been reported as unknown.
            best, best_score = None, 0.0
            for i, v in enumerate(self._profiles):
                score = difflib.SequenceMatcher(
                    None, t, v["name"].lower()).ratio()
                if score > best_score:
                    best, best_score = i, score
            if best is not None and best_score >= _VOICE_FUZZY_MIN:
                chosen = best
        if chosen is not None:
            self._current = chosen
            self._persist_preferred(self.current_voice_name)
            return f"Switched to {self.current_voice_name}."
        return (f"Voice '{identifier}' not found. Choose from: "
                + ", ".join(v["name"] for v in self._profiles))

    @staticmethod
    def _clean_voice_query(identifier: str) -> str:
        """Strip the filler people wrap around a voice name while speaking."""
        t = (identifier or "").strip().lower()
        t = re.sub(r"\b(?:awaaz|voice|ka|ki|kaun|kaun\s*si|ko|se|par|on|"
                   r"switch|change|set|kar|do|ji|yaar|bhai)\b", " ", t)
        t = re.sub(r"[^a-z0-9\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    def _persist_preferred(self, voice_name: str) -> None:
        """Save the chosen voice into config.json (voice.preferred_voice)."""
        if not self._cfg_path or not os.path.isfile(self._cfg_path):
            return
        try:
            with open(self._cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            voice_cfg = cfg.setdefault("voice", {})
            if voice_cfg.get("preferred_voice") != voice_name:
                voice_cfg["preferred_voice"] = voice_name
                with open(self._cfg_path, "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # -- speak ----------------------------------------------------------

    def show(self, text: str) -> None:
        """Print the reply in the terminal.

        With voice.type_effect on (default) the line is typed out smoothly —
        in a background thread, so speech starts right away and the text
        appears while AK talks. Long task outputs (over type_max_chars) print
        instantly so the console never crawls.
        """
        if not text:
            return
        delay = (self._type_speed / 1000.0
                 if self._type_effect and len(text) <= self._type_max else 0.0)
        if delay <= 0:
            try:
                print(f"[AK] {text}")
            except UnicodeEncodeError:
                print(f"[AK] {_stdout_safe(text)}")
            return
        t = threading.Thread(
            target=typewrite,
            args=(text, "[AK] ", delay, "\n", lambda: self.interrupted),
            daemon=True, name="ak-typewrite")
        self._typing_thread = t
        t.start()

    def say(self, text: str, on_audio_start=None, on_audio_end=None) -> None:
        if not text:
            return
        self._on_audio_start = on_audio_start
        self._on_audio_end = on_audio_end
        self.interrupted = False

        voice = self.current_voice
        if voice["engine"] == "edge" and self._edge_ok and not self._edge_offline:
            engine = "edge"
        elif voice["engine"] == "xtts":
            engine = "xtts"
        else:
            engine = self._method
        # Engine line FIRST: the typing thread grabs _OUT_LOCK for the whole
        # line, so printing this afterwards would block speech until typing
        # finished. Order keeps the console clean without delaying audio.
        print(f"[voice] Speaking via {engine}: {voice['name']}")
        self.show(text)
        spoken = self._gendered(text)
        if voice["engine"] == "xtts":
            self._say_xtts(spoken)
        elif voice["engine"] == "edge" and self._edge_ok and not self._edge_offline:
            self._say_edge(spoken, voice["id"])
        elif self._method == "sapi":
            self._say_sapi(spoken, voice)
        elif self._method == "pyttsx3":
            self._say_pyttsx3(spoken, voice)
        cb = getattr(self, "_on_audio_end", None)
        self._on_audio_start = None
        self._on_audio_end = None
        if cb:
            try:
                cb()
            except Exception:
                pass
        # Let the typing effect finish the line (it flushes instantly once the
        # speech is interrupted, so this never hangs the turn).
        t = getattr(self, "_typing_thread", None)
        if t is not None:
            t.join(timeout=_TYPE_MAX_SECONDS + 2.0)
            self._typing_thread = None

    def interrupt(self) -> None:
        """Stop any speech playing right now (called from the barge-in
        monitor thread).  Every playback engine is stopped as best it can:
        sounddevice streams are cut instantly, SAPI purges its queue, and
        pyttsx3 calls engine.stop()."""
        self.interrupted = True
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        try:
            st = getattr(self, "_active_stream", None)
            if st is not None:
                st.abort()
        except Exception:
            pass
        try:
            if self._method == "sapi" and self._sapi is not None:
                # Purge mode: abort the async speak queue immediately.
                self._sapi.Speak("", 3)
        except Exception:
            pass
        try:
            if self._method == "pyttsx3" and self._sapi is not None:
                self._sapi.stop()
        except Exception:
            pass

    def _gendered(self, text: str) -> str:
        """Give Hinglish first-person verbs the right gender so a female
        voice says 'main bata deti hun' and a male voice 'main bata deta hun'.
        Only female voices are rewritten (masculine is the default form)."""
        if self.current_voice.get("gender") != "Female" or not text:
            return text
        pairs = [
            (r"\bbata deti hoon\b", "bata deti hoon"),     # already feminine
            (r"\bbata deta hoon\b", "bata deti hoon"),
            (r"\bbata deta hun\b", "bata deti hun"),
            (r"\bbata deta\b", "bata deti"),
            (r"\bbt?a deta hoon\b", "bta deti hoon"),
            (r"\bbt?a deta\b", "bta deti"),
            (r"\bkar deta hoon\b", "kar deti hoon"),
            (r"\bkar deta\b", "kar deti"),
            (r"\bde deta\b", "de deti"),
            (r"\bho gaya\b", "ho gayi"),
            (r"\bho jaata\b", "ho jaati"),
            (r"\bho jata\b", "ho jaati"),
            (r"\bho sakta\b", "ho sakti"),
            (r"\bde sakta\b", "de sakti"),
            (r"\bkar sakta\b", "kar sakti"),
            (r"\bkar doonga\b", "kar doongi"),
            (r"\bkar dunga\b", "kar dungi"),
            (r"\bkarunga\b", "karungi"),
            (r"\bdoonga\b", "doongi"),
            (r"\bdunga\b", "dungi"),
            (r"\bchala gaya\b", "chali gayi"),
            (r"\bchala jaata\b", "chali jaati"),
            (r"\bbana diya\b", "bana di"),
            (r"\bbana liya\b", "bana li"),
            (r"\bgaya hoon\b", "gayi hoon"),
            (r"\bgaya hai\b", "gayi hai"),
            (r"\bgaya tha\b", "gayi thi"),
            (r"\bmila hai\b", "mili hai"),
            (r"\bmila tha\b", "mili thi"),
            (r"\bmila\b", "mili"),
            (r"\baaya hoon\b", "aayi hoon"),
            (r"\baaya\b", "aayi"),
            (r"\blag raha\b", "lag rahi"),
            (r"\bchal raha\b", "chal rahi"),
            (r"\bho raha\b", "ho rahi"),
            (r"\bkar raha\b", "kar rahi"),
            (r"\bban raha\b", "ban rahi"),
            (r"\braha hoon\b", "rahi hoon"),
            (r"\braha hai\b", "rahi hai"),
            (r"\braha tha\b", "rahi thi"),
            (r"\bkarta hoon\b", "karti hoon"),
            (r"\bkarta hai\b", "karti hai"),
            (r"\bkarta\b", "karti"),
            (r"\bpata chal gaya\b", "pata chal gayi"),
            (r"\bsamajh gaya\b", "samajh gayi"),
            (r"\bsamajh aaya\b", "samajh aayi"),
            (r"\bpata tha\b", "pata thi"),
            (r"\bgayi tha\b", "gayi thi"),
        ]
        for pat, rep in pairs:
            text = re.sub(pat, rep, text, flags=re.IGNORECASE)
        return text

    def _audio_start(self, dur: float = 0.0) -> None:
        """Fire the audio-start callback (caption sync) right before the
        actual sound plays, with an estimated/measured duration in seconds."""
        cb = getattr(self, "_on_audio_start", None)
        if not cb:
            return
        try:
            cb(dur)
        except Exception:
            pass

    @staticmethod
    def _wav_duration(path: str) -> float:
        import struct
        import wave
        try:
            with wave.open(path, "rb") as w:
                sr = w.getframerate()
                frames = w.getnframes()
            return frames / float(sr or 1)
        except Exception:
            return 0.0

    def _est_duration(self, text: str) -> float:
        words = len(text.split())
        rate = self._rate or 180
        return max(1.0, words / max(120.0, rate) * 60.0)

    def _say_xtts(self, text: str) -> None:
        """Speak with the user's OWN cloned voice (XTTS in a helper process).
        Falls back to the default SAPI voice if the model/ref isn't ready."""
        try:
            from AK.myvoice import MyVoiceClient
        except Exception as exc:
            print(f"[voice] My Voice not available ({exc}); using David.")
            self._say_sapi(text, VOICE_PROFILES[4])
            return
        client = getattr(self, "_myvoice_client", None)
        if not isinstance(client, MyVoiceClient):
            try:
                client = MyVoiceClient(self._cfg)
                self._myvoice_client = client
            except Exception as exc:
                print(f"[voice] MyVoice init error ({exc}); using David.")
                self._say_sapi(text, VOICE_PROFILES[4])
                return
        if not client.available:
            print("[voice] My Voice engine not installed — using David.")
            self._say_sapi(text, VOICE_PROFILES[4])
            return
        if not client.enrolled:
            print("[voice] My Voice selected but not enrolled — "
                  "say 'record my voice'.")
            self._say_sapi("My voice is selected, but I do not have your "
                           "voice recorded yet. Say, record my voice, first.",
                           VOICE_PROFILES[4])
            return
        import tempfile
        out = os.path.join(tempfile.gettempdir(), "ak_myvoice_tmp.wav")
        ok, err = client.speak(text, out)
        if not ok or not os.path.isfile(out):
            print(f"[voice] My Voice failed ({err}); using David.")
            self._say_sapi(text, VOICE_PROFILES[4])
            return
        if self.interrupted:
            return
        self._audio_start(self._wav_duration(out) or self._est_duration(text))
        self._play_wav(out)

    def _play_wav(self, path: str) -> bool:
        """Play a WAV via sounddevice (non-blocking start, blocking wait).
        Returns True if playback was launched, False otherwise.
        sounddevice.play + sd.stop() lets the barge-in monitor cut speech."""
        if self.interrupted:
            return False
        import sounddevice as sd
        import wave
        try:
            with wave.open(path, "rb") as w:
                sr = w.getframerate()
                raw = w.readframes(w.getnframes())
            if not raw:
                return False
            import numpy as np
            samples = np.frombuffer(raw, dtype=np.int16)
            sd.play(samples, sr)
            sd.wait()
            return True
        except Exception as exc:
            print(f"[voice] playback error ({exc}).")
            return False

    def _sapi_voice_for(self, voice: dict):
        """Best-match an installed SAPI voice for a profile (id, then name,
        then gender).  Returns None if no SAPI voices are available."""
        try:
            voices = self._sapi.GetVoices()
        except Exception:
            return None
        if voices is None or voices.Count == 0:
            return None
        want_id = (voice.get("id") or "").lower()
        want_name = (voice.get("name") or "").lower() if voice.get("engine") == "sapi" else ""
        # Exact id match first (e.g. "David", "Zira")
        for i in range(voices.Count):
            desc = voices.Item(i).GetDescription().lower()
            attr = voices.Item(i).GetAttribute("Name").lower()
            if want_id and (want_id in desc or want_id in attr):
                return voices.Item(i)
        # Profile name match (e.g. "Madhur")
        if want_name:
            for i in range(voices.Count):
                desc = voices.Item(i).GetDescription().lower()
                if want_name in desc:
                    return voices.Item(i)
        # Fall back by gender when the profile expects a neural voice that
        # this PC doesn't have (edge unavailable): male -> David, female -> Zira.
        want_gender = (voice.get("gender") or "").lower()
        for i in range(voices.Count):
            desc = voices.Item(i).GetDescription().lower()
            if want_gender == "male" and "david" in desc:
                return voices.Item(i)
            if want_gender == "female" and "zira" in desc:
                return voices.Item(i)
        return voices.Item(0)

    def _say_sapi(self, text: str, voice: dict) -> None:
        try:
            sapi_voice = self._sapi_voice_for(voice)
            if sapi_voice is not None:
                self._sapi.Voice = sapi_voice
            self._audio_start(self._est_duration(text))
            self._sapi.Speak(text, 1)  # async so barge-in can purge it
            try:
                while getattr(self._sapi.Status, "RunningState", 1) == 1:
                    if self.interrupted:
                        try:
                            self._sapi.Speak("", 3)  # purge
                        except Exception:
                            pass
                        break
                    time.sleep(0.05)
            except Exception:
                pass
            try:
                self._sapi.Speak("", 3)  # flush anything queued
            except Exception:
                pass
        except Exception as exc:
            print(f"[voice] SAPI TTS error ({exc}).")

    def _say_pyttsx3(self, text: str, voice: dict) -> None:
        try:
            want_id = (voice.get("id") or "").lower()
            gender = (voice.get("gender") or "").lower()
            avail = self._sapi.getProperty("voices")
            picked = None
            # Prefer an id match (David / Zira), else map by gender.
            for v in avail:
                vx = str(getattr(v, "id", "")).lower() + " " + str(getattr(v, "name", "")).lower()
                if want_id in vx:
                    picked = v
                    break
            if picked is None:
                for v in avail:
                    name = (str(getattr(v, "name", "")) + " " + str(getattr(v, "id", ""))).lower()
                    if gender == "male" and "david" in name:
                        picked = v
                        break
                    if gender == "female" and "zira" in name:
                        picked = v
                        break
            if picked is not None:
                self._sapi.setProperty("voice", picked.id)
            self._audio_start(self._est_duration(text))
            self._sapi.say(text)
            self._sapi.runAndWait()
        except Exception as exc:
            print(f"[voice] TTS error ({exc}).")

    def _stream_edge(self, text: str, voice_id: str) -> bool:
        """Stream edge-tts audio straight to the speaker so the voice starts
        in ~1s instead of waiting for the full download + ffmpeg conversion.

        Returns True if audio played at all (or was deliberately cut by a
        barge-in), False if this machine can't stream here (caller retries
        the full download path), and raises on real network failures so the
        caller can fall back to local SAPI."""
        import asyncio
        import threading

        import edge_tts
        import imageio_ffmpeg
        import subprocess
        import numpy as np
        import sounddevice as sd

        class _StopNow(Exception):
            pass

        proc = None
        stream = None
        played = False
        state = {"err": None}

        async def _chunks():
            c = edge_tts.Communicate(text, voice_id)
            async for chunk in c.stream():
                if self.interrupted:
                    raise _StopNow()
                if chunk.get("type") == "audio":
                    yield chunk["data"]

        def _feed():
            try:
                async def _run():
                    async for data in _chunks():
                        if self.interrupted:
                            break
                        try:
                            proc.stdin.write(data)
                        except Exception:
                            break
                    if not self.interrupted:
                        try:
                            proc.stdin.close()
                        except Exception:
                            pass
                asyncio.run(_run())
            except _StopNow:
                pass
            except Exception as exc:
                if not self.interrupted:
                    state["err"] = exc
                    try:
                        proc.kill()
                    except Exception:
                        pass

        try:
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [ff, "-y", "-loglevel", "error", "-i", "pipe:0",
                   "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1"]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)
        except Exception:
            return False

        try:
            self._audio_start(self._est_duration(text))
            threading.Thread(target=_feed, daemon=True,
                             name="ak-edge-feed").start()
            stream = sd.OutputStream(samplerate=24000, channels=1,
                                     dtype="int16")
            self._active_stream = stream
            stream.start()
            while True:
                if self.interrupted:
                    raise _StopNow()
                data = proc.stdout.read(9600)      # 100 ms @ 24 kHz mono
                if not data:
                    break
                if state["err"] is not None and not played:
                    raise state["err"]
                samples = np.frombuffer(data, dtype=np.int16)
                if samples.size:
                    stream.write(samples)
                    played = True
            if self.interrupted:
                raise _StopNow()
            # Nothing played and the feed thread recorded an error (e.g. the
            # edge endpoint answered 403). Surface it so the caller falls back
            # to the local SAPI voice instead of ending in silence.
            if state["err"] is not None and not played:
                raise state["err"]
            return True
        except _StopNow:
            return True
        except Exception as exc:
            if played:
                return True
            if state["err"] is not None:
                raise state["err"]
            return False
        finally:
            self._active_stream = None
            if proc is not None:
                try: proc.kill()
                except Exception: pass
                try: proc.stdin.close()
                except Exception: pass
            if stream is not None:
                try: stream.close()
                except Exception: pass

    def _say_edge(self, text: str, voice_id: str) -> None:
        webm_path = None
        wav_path = None
        try:
            # Step 0: preferred — stream edge-tts straight to the speaker so
            # the voice starts in ~1s instead of a full download + convert.
            if self._stream_edge(text, voice_id):
                return

            # Step 1: generate audio via edge-tts (saves as WebM Opus)
            async def _gen():
                import edge_tts
                communicate = edge_tts.Communicate(text, voice_id)
                tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
                tmp.close()
                await communicate.save(tmp.name)
                return tmp.name
            webm_path = asyncio.run(_gen())

            # Step 2: convert WebM -> WAV using bundled ffmpeg
            try:
                import importlib
                img_ff = importlib.import_module("imageio_ffmpeg")
                ffmpeg = img_ff.get_ffmpeg_exe()
                wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                wav_tmp.close()
                wav_path = wav_tmp.name
                import subprocess
                subprocess.run(
                    [ffmpeg, "-y", "-i", webm_path,
                     "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                     wav_path],
                    capture_output=True, timeout=60, check=True,
                )
            except Exception:
                self._play_audio_file(webm_path)
                return
            finally:
                if webm_path:
                    try: os.unlink(webm_path)
                    except Exception: pass
                webm_path = None

            # Step 3: play WAV via sounddevice (stoppable for barge-in).
            # Winsound is kept as a fallback if sd can't launch the stream.
            if wav_path:
                if self.interrupted:
                    try: os.unlink(wav_path)
                    except Exception: pass
                    wav_path = None
                else:
                    try:
                        self._audio_start(self._wav_duration(wav_path) or
                                         self._est_duration(text))
                        if not self._play_wav(wav_path):
                            import winsound
                            winsound.PlaySound(wav_path, winsound.SND_FILENAME)
                    except Exception:
                        self._play_audio_file(wav_path)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            msg = str(exc).lower()
            # Any failure here (blocked endpoint, no internet, ffmpeg issue)
            # must still produce sound. A 403 / handshake error means Microsoft
            # rejected this edge-tts build, which won't fix itself mid-session —
            # remember it so the rest of the run goes straight to local SAPI.
            if any(k in msg for k in (
                    "connect", "getaddrinfo", "resolve", "timeout",
                    "timed out", "ssl", "network", "dns", "hostname",
                    "403", "forbidden", "unauthorized", "handshake",
                    "invalid response", "connection")):
                self._edge_offline = True
                print("[voice] Edge TTS blocked/unreachable — using the local "
                      "SAPI voice for this session.")
            else:
                print(f"[voice] edge-tts error ({exc}); using local SAPI voice.")
            try:
                self._say_sapi(text, self.current_voice)
            except Exception:
                pass
        finally:
            if webm_path:
                try: os.unlink(webm_path)
                except Exception: pass
            if wav_path:
                try: os.unlink(wav_path)
                except Exception: pass

    def _play_audio_file(self, path: str) -> None:
        """Fallback: launch the default media player for the file."""
        try:
            import subprocess
            subprocess.run(
                ["cmd", "/c", "start", "/wait", "", path],
                shell=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            pass
        except Exception as exc:
            print(f"[voice] playback error ({exc})")


# ═══════════════════════════════ STT ═══════════════════════════════

def _safe_stt(s: str) -> str:
    """Console-safe text: the Windows console (cp1252) crashes on Devanagari,
    which made Google's Hindi transcripts fail silently and fall back to the
    weak local model. Replace unencodable chars so a transcript never bombs."""
    enc = (sys.stdout.encoding or "ascii")
    try:
        return s.encode(enc, "replace").decode(enc, "replace")
    except Exception:
        return s.encode("ascii", "replace").decode("ascii")


class Listener:
    """Speech-to-text with multiple backends and a typed fallback.

    Backend selection (`stt_engine` in config):
        "google" -> free Google web API (DEFAULT — most accurate for
                    Hinglish; local whisper is only a non-blocking backup)
        "auto"   -> local faster-whisper, else Vosk, else Google, else keyboard
        "whisper"-> local faster-whisper (no network, Hinglish capable)
        "vosk"   -> local Vosk (fast, weaker on Hindi)
        "text"   -> always read from the keyboard
    """

    def __init__(self, cfg: dict, resolve_path):
        self.cfg = cfg
        self._resolve_path = resolve_path
        self.language = cfg.get("language", "en-US")
        self.device_index = cfg.get("input_device_index")
        self.engine = cfg.get("stt_engine", "auto")
        self._vosk_model = None
        self._recognizer = None
        self._mic = None
        self._samplerate = 16000
        self._fell_offline = False
        self._google_fail_streak = 0
        self._local_stt = None
        # Terminal typing effect for the chat (same knobs as the speaker).
        self._type_effect = bool(cfg.get("type_effect", True))
        self._type_speed = max(0.0, float(cfg.get("type_speed", 12)))

        # ── Latency knobs ────────────────────────────────────────────
        # Both capture loops work in 100 ms blocks, so silence counters
        # below are just "blocks * 100 ms". These used to be hard-coded to
        # 30 blocks = 3.0 s of trailing silence, which meant every command
        # sat waiting 3 s before it was even sent for recognition — the
        # reason AK felt slow and people repeated themselves 3-4 times.
        self._endpoint_silence_blocks = max(
            2, int(round(float(cfg.get("endpoint_silence_ms", 400)) / 100.0)))
        self._wake_speech_blocks = max(
            2, int(round(float(cfg.get("wake_speech_ms", 250)) / 100.0)))
        self._wake_min_audio_blocks = max(
            2, int(round(float(cfg.get("wake_min_audio_ms", 300)) / 100.0)))

        # Local model: only an OFFLINE safety net behind Google. With
        # stt_engine="google" it is deliberately NOT loaded at startup (no
        # disk/CPU hit, no wait) — _ensure_local_warm() spins it up in the
        # background the first time Google actually fails.
        self._local_stt = self._make_local_stt()
        self._warm_started = False
        if self._local_stt is not None and self.engine != "google":
            self._local_stt.warmup()
            self._warm_started = True

        chosen = self._init_backend(self.engine)
        self.active = chosen
        print(f"[voice] STT backend: {chosen}")

    def _make_local_stt(self):
        """Build the faster-whisper engine, or None if it is not installed."""
        if not self.cfg.get("use_local_stt", True):
            return None
        try:
            from AK.stt import DEFAULT_HOTWORDS, LocalSTT
            # Optional extras from config are APPENDED, never a replacement —
            # so adding a favourite artist does not silently drop the built-in
            # music vocabulary that keeps "Arijit Singh" from coming back as
            # "a rigid scene".
            extra = str(self.cfg.get("whisper_hotwords_extra", "") or "").strip()
            hotwords = f"{DEFAULT_HOTWORDS} {extra}".strip() if extra else DEFAULT_HOTWORDS
            return LocalSTT(
                model_size=self.cfg.get("whisper_model", "base"),
                language=self.language,
                cpu_threads=self.cfg.get("whisper_cpu_threads", 2),
                compute_type=self.cfg.get("whisper_compute_type", "int8"),
                vad=bool(self.cfg.get("whisper_vad", True)),
                hotwords=hotwords,
                beam_size=self.cfg.get("whisper_beam_size", 5))


        except Exception as exc:  # noqa: BLE001
            print(f"[voice] faster-whisper unavailable ({exc}).")
            return None

    # -- backend setup --------------------------------------------------
    def set_engine(self, engine: str) -> None:
        """Switch the STT backend at runtime (re-runs backend setup).

        Lets CLI flags like --text force keyboard mode even after the
        Listener has already been constructed from config."""
        self.engine = engine
        chosen = self._init_backend(engine)
        self.active = chosen
        print(f"[voice] STT backend: {chosen}")

    def _init_backend(self, requested: str) -> str:
        if requested == "text":
            return "text"
        if requested == "vosk":
            return "vosk" if self._try_vosk() else "text"
        if requested == "google":
            return "google" if self._try_google() else "text"
        # "auto" / "whisper" / anything else: local first, it is the only
        # backend that reliably works, and it keeps audio on this machine.
        if self._local_stt is not None:
            return "whisper"
        if requested in ("auto", "whisper") and self._try_vosk():
            return "vosk"
        if self._try_google():
            return "google"
        print("[voice] No speech backend available; using keyboard input.")
        return "text"

    def _try_vosk(self) -> bool:
        try:
            import os

            from vosk import KaldiRecognizer, Model  # noqa: F401

            model_path = self._find_vosk_model()
            if not model_path:
                return False
            self._vosk_model = Model(model_path)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _find_vosk_model(self) -> str:
        """Locate a usable Vosk model on disk.  Checks the configured path,
        the project models folder, and the D-drive data models folder, so
        AK can keep understanding the user even with zero internet."""
        cands = [
            self._resolve_path(self.cfg.get("vosk_model_path", "")),
            self._resolve_path("models/vosk-model-small-en-us-0.15"),
            self._resolve_path("models/vosk-model-small-en-in-0.4"),
            self._resolve_path("models/vosk-model-small-hi-0.22"),
        ]
        try:
            from AK.config import get_data_dir
            data_dir = get_data_dir()
            cands.append(os.path.join(data_dir, "models", "vosk-model-small-en-us-0.15"))
            cands.append(os.path.join(data_dir, "models", "vosk-model-small-en-in-0.4"))
            cands.append(os.path.join(data_dir, "models", "vosk-model-small-hi-0.22"))
        except Exception:
            pass
        seen = set()
        for p in cands:
            if not p or p in seen:
                continue
            seen.add(p)
            if os.path.isdir(p):
                return p
        return ""

    def _fallback_offline(self) -> str:
        """Google is unusable — keep listening locally instead of going quiet."""
        if self._local_stt is not None:
            self.active = "whisper"
            self._fell_offline = True
            print("[voice] Google STT unavailable — staying on local "
                  f"{self._local_stt.name}.")
            return "whisper"
        if self._try_vosk():
            self.set_engine("vosk")
            self._fell_offline = True
            print("[voice] Offline mode ON — using local Vosk STT.")
            return "vosk"
        self.active = "text"
        self.engine = "text"
        self._fell_offline = True
        print("[voice] No working STT backend — using keyboard input.")
        return "text"

    def _local_ready(self):
        """The local STT engine ONLY if its model is already in memory.

        Returns None instead of ever triggering the model load — the
        multi-second `_ensure()` disk read on the hot path was the "fallback
        wait" that stalled wake/command handling."""
        lst = self._local_stt
        if lst is None or not getattr(lst, "ready", False):
            return None
        return lst

    def _ensure_local_warm(self) -> None:
        """Start loading the offline fallback in the BACKGROUND (Google stays
        primary).  Safe to call often; never blocks the caller."""
        if self._warm_started:
            return
        self._warm_started = True
        if self._local_stt is None:
            self._local_stt = self._make_local_stt()
        if self._local_stt is not None:
            print("[voice] Warming the offline fallback in the background "
                  "(Google stays primary).")
            self._local_stt.warmup()

    def _try_google(self) -> bool:
        try:
            import speech_recognition as sr

            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = 50
            self._recognizer.dynamic_energy_threshold = False
            # Was 2.0 s: the recogniser waited two seconds of silence before
            # it would hand the phrase over, on top of the endpoint silence in
            # the capture loop. 1.0 s keeps whole phrases intact, much snappier.
            self._recognizer.pause_threshold = 1.0

            import sounddevice as sd
            info = sd.query_devices()
            mics = [d["name"] for d in info if d["max_input_channels"] > 0]
            if not mics:
                print("[voice] No microphone detected. Use --text or 'enable typing' for keyboard mode.")
                return False
            print(f"[voice] Microphones ({len(mics)}):")
            for i, name in enumerate(mics):
                sel = self.device_index
                mark = " <-- selected" if (sel is None and i == 0) or sel == i else ""
                print(f"  [{i}] {name}{mark}")

            self._samplerate = 16000
            print("[voice] Using sounddevice capture + Google STT")
            return True
        except Exception as exc:
            print(f"[voice] Mic setup failed: {exc}")
            print("[voice] Use --text or 'enable typing' for keyboard mode.")
            return False

    # -- public API -----------------------------------------------------
    def listen(self, prompt: str = "[MIC] Listening...") -> Optional[str]:
        """Capture one utterance and return lowercased text (or None)."""
        if self.active == "vosk":
            return self._listen_vosk(prompt)
        if self.active in ("whisper", "google", "auto"):
            return self._listen_google(prompt)
        return self._listen_text(prompt)

    def wait_for_wake(self, prompt: str = "[AK] Sleeping... Say 'ak' to wake me",
                      snap_threshold: int = 18000,
                      wake_phrases: Optional[tuple] = None,
                      snap_enabled: bool = False,
                      snap_ratio: float = 18.0,
                      verifier=None) -> Optional[str]:
        """Monitor mic for the WAKE WORD; optional finger-snap wake.

        Returns:
            "__SLAP__"  if snap_enabled=True and a sharp transient (snap/clap)
                        was heard while otherwise quiet,
            str         only if a real wake phrase was spoken — and, when a
                        voiceprint verifier is attached, only if that speaker
                        matches (voice lock),
            None        on timeout / error.
        """
        phrases = wake_phrases or WAKE_PHRASES
        if self.active == "text":
            return self._listen_text(prompt)

        import struct

        import sounddevice as sd

        print(prompt)
        samplerate = 16000
        blocksize = 1600          # 100 ms
        speech_threshold = 80
        reject_streak = 0
        q: "queue.Queue[bytes]" = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status and os.environ.get("AK_DEBUG_AUDIO"):
                print(status, file=sys.stderr)
            q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=callback,
            ):
                audio_buffer = bytearray()
                speech_blocks = 0
                silence_blocks = 0
                recording = False

                while True:
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    samples = struct.unpack(f"<{len(data)//2}h", data)
                    peak = 0
                    sq = 0
                    for s in samples:
                        if s < 0:
                            s = -s
                        if s > peak:
                            peak = s
                        sq += s * s
                    rms = (sq / len(samples)) ** 0.5

                    # ── Finger-snap / sharp transient (only while quiet) ──
                    if snap_enabled and not recording and peak > snap_threshold \
                            and peak > snap_ratio * rms:
                        print(f"[snap] Snap detected! peak={peak}")
                        return "__SLAP__"

                    # ── Normal speech detection ──
                    if rms > speech_threshold:
                        if not recording:
                            recording = True
                        speech_blocks += 1
                        silence_blocks = 0
                    else:
                        silence_blocks += 1

                    if recording:
                        audio_buffer.extend(data)
                        # Try wake word after enough speech + trailing silence
                        if (speech_blocks >= self._wake_speech_blocks
                                and silence_blocks >= self._endpoint_silence_blocks):
                            if len(audio_buffer) < blocksize * self._wake_min_audio_blocks:
                                audio_buffer = bytearray()
                                recording = False
                                speech_blocks = 0
                                silence_blocks = 0
                                continue
                            # One local pass: same text also carries any
                            # command said after 'ak', so a one-sentence
                            # command costs a single transcription.
                            text = self._transcribe_active(bytes(audio_buffer),
                                                           samplerate)
                            if text:
                                tl = text.lower()
                                if _wake_hit(tl, phrases):
                                    if verifier is not None:
                                        ok = False
                                        try:
                                            ok = verifier(bytes(audio_buffer))
                                        except Exception:
                                            ok = False
                                        if not ok:
                                            reject_streak += 1
                                            print("[voice lock] wake rejected — not the enrolled voice")
                                            if reject_streak >= 3:
                                                print("[voice lock] rejected " +
                                                      "3+ times in a row — retrain with 'train my voice', "
                                                      "be nearer the mic, or say 'unlock voice' to disable.")
                                            audio_buffer = bytearray()
                                            recording = False
                                            speech_blocks = 0
                                            silence_blocks = 0
                                            continue
                                    reject_streak = 0
                                    print(f"[STT] Wake heard: {text}")
                                    return tl
                                print(f"[STT] Ignored (no wake word): {text}")
                            # Reset for next attempt
                            audio_buffer = bytearray()
                            recording = False
                            speech_blocks = 0
                            silence_blocks = 0

                        # Safety cap — flush every 100 speech blocks
                        if speech_blocks > 100:
                            audio_buffer = bytearray()
                            recording = False
                            speech_blocks = 0
                            silence_blocks = 0

        except Exception as exc:
            print(f"[voice] Wake detection error ({exc}).")
            return None

    # ── Barge-in / interrupt monitor ────────────────────────────────

    def _preload_wake_model(self) -> None:
        """Kept for compatibility: the local model is warmed up in __init__."""
        if self._local_stt is not None:
            self._local_stt.warmup()

    def _transcribe_active(self, audio: bytes, samplerate: int) -> str:
        """One transcription with whatever backend is active.

        Local faster-whisper first: no network, understands Hinglish, and it
        also gives back the command spoken after 'ak' in the same pass, so a
        one-sentence command costs exactly one transcription.
        """
        if self._local_stt is not None and self.active in ("whisper", "auto", None):
            try:
                return self._local_stt.transcribe(audio, samplerate)
            except Exception as exc:  # noqa: BLE001
                print(f"[voice] Local STT failed ({exc}); trying next backend.")
        if self.active == "vosk" and self._vosk_model is not None:
            try:
                import json
                from vosk import KaldiRecognizer
                rec = KaldiRecognizer(self._vosk_model, samplerate)
                rec.AcceptWaveform(audio)
                return (json.loads(rec.Result()).get("text") or "").strip()
            except Exception:  # noqa: BLE001
                return ""
        if self._recognizer is not None:
            import speech_recognition as sr
            try:
                text = self._recognizer.recognize_google(
                    sr.AudioData(audio, samplerate, 2), language=self.language)
                return (text or "").strip()
            except sr.UnknownValueError:
                # Google accepted the request but could not read this phrase.
                # ONE bad phrase must never downgrade the whole session away
                # from Google (engine="google") — local is used for THIS line
                # only, and only if its model is already warm (no wait).
                return self._transcribe_local(audio, samplerate)
            except sr.RequestError:
                self._google_fail_streak = getattr(self, "_google_fail_streak", 0) + 1
                print(f"[voice] Google unreachable (streak "
                      f"{self._google_fail_streak}) — offline net for this line.")
                if self.engine != "google":
                    self._fallback_offline()
                self._ensure_local_warm()
                return self._transcribe_local(audio, samplerate)
            except Exception:  # noqa: BLE001
                return ""
        return ""

    def _transcribe_local(self, audio: bytes, samplerate: int) -> str:
        """Transcribe with the local model for THIS utterance only, without
        changing the active backend. Lets AK keep Google as primary while still
        answering when a single Google call fails.

        Never waits for the model to load: if it is cold, the caller gets ''
        instantly and the warmup finishes in the background for next time."""
        local = self._local_ready()
        if local is None:
            return ""
        try:
            return local.transcribe(audio, samplerate)
        except Exception:  # noqa: BLE001
            return ""

    def start_interrupt_monitor(self, on_interrupt,
                                phrases=INTERRUPT_PHRASES) -> None:
        """Watch the mic in a background thread WHILE AK is speaking.
        When the user says a wait/stop phrase, call `on_interrupt()` so the
        speech is cut and the user can take over with a new command."""
        if self.active in ("text",):
            return
        self._stop_interrupt = threading.Event()
        self._on_interrupt = on_interrupt
        t = threading.Thread(
            target=self._interrupt_loop,
            args=(list(phrases), self._stop_interrupt),
            daemon=True,
            name="ak-interrupt-monitor",
        )
        self._interrupt_thread = t
        t.start()

    def stop_interrupt_monitor(self) -> None:
        """Stop the barge-in monitor (safe to call even if never started)."""
        ev = getattr(self, "_stop_interrupt", None)
        if ev is not None:
            ev.set()
        t = getattr(self, "_interrupt_thread", None)
        if t is not None:
            t.join(timeout=2.0)
        self._interrupt_thread = None
        self._stop_interrupt = None
        self._on_interrupt = None

    def _interrupt_loop(self, phrases, stop_ev) -> None:
        import speech_recognition as sr
        import struct
        import sounddevice as sd

        samplerate = 16000
        blocksize = 1600          # 100 ms
        speech_threshold = 80
        q: "queue.Queue[bytes]" = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status and os.environ.get("AK_DEBUG_AUDIO"):
                print(status, file=sys.stderr)
            q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=callback,
            ):
                audio_buffer = bytearray()
                speech_blocks = 0
                silence_blocks = 0
                recording = False
                while not stop_ev.is_set():
                    try:
                        data = q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    samples = struct.unpack(f"<{len(data)//2}h", data)
                    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                    if rms > speech_threshold:
                        if not recording:
                            recording = True
                        speech_blocks += 1
                        silence_blocks = 0
                    else:
                        silence_blocks += 1
                    if recording:
                        audio_buffer.extend(data)
                        # Quick verdict: a bit of speech + a short silence gap.
                        if speech_blocks >= 5 and silence_blocks >= 8:
                            try:
                                # Same local engine as the wake path - Google's
                                # endpoint rejects everything, so asking it here
                                # meant AK could never be interrupted.
                                text = self._transcribe_active(bytes(audio_buffer),
                                                               samplerate)
                                if text and _wake_hit(text, tuple(phrases)):
                                    print(f"[voice] Interrupt heard: {text}")
                                    cb = getattr(self, "_on_interrupt", None)
                                    if cb:
                                        try:
                                            cb()
                                        except Exception:
                                            pass
                                    break
                            except Exception:
                                pass
                            audio_buffer = bytearray()
                            recording = False
                            speech_blocks = 0
                            silence_blocks = 0
                        # Safety cap if the monitor never hears silence
                        if speech_blocks > 200:
                            audio_buffer = bytearray()
                            recording = False
                            speech_blocks = 0
                            silence_blocks = 0
        except Exception as exc:
            print(f"[voice] interrupt monitor error ({exc}).")

    def capture_phrase(self, prompt: str = "[AK] Speak now (voice lock enrollment)") -> Optional[bytes]:
        """Record one utterance and return raw 16 kHz int16 PCM (no STT).
        Used to train the voiceprint — works fully offline."""
        if self.active == "text":
            self._listen_text(prompt)
            return None

        import struct

        import sounddevice as sd

        print(prompt)
        samplerate = 16000
        blocksize = 1600          # 100 ms
        speech_threshold = 80
        q: "queue.Queue[bytes]" = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status and os.environ.get("AK_DEBUG_AUDIO"):
                print(status, file=sys.stderr)
            q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=callback,
            ):
                audio_buffer = bytearray()
                speech_blocks = 0
                silence_blocks = 0
                recording = False
                while True:
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    samples = struct.unpack(f"<{len(data)//2}h", data)
                    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                    if rms > speech_threshold:
                        if not recording:
                            recording = True
                        speech_blocks += 1
                        silence_blocks = 0
                    else:
                        silence_blocks += 1
                    if recording:
                        audio_buffer.extend(data)
                        if speech_blocks >= 5 and silence_blocks >= 30:
                            return bytes(audio_buffer)
                        if speech_blocks > 120:
                            return bytes(audio_buffer)
        except Exception as exc:
            print(f"[voice] capture error ({exc}).")
            return None

    def capture_reference(self, seconds: int = 40,
                          prompt: Optional[str] = None) -> Optional[bytes]:
        """Record ~`seconds` seconds of continuous speech for the cloned-voice
        reference.  Stops early after 3 s of trailing silence (with at least
        4 s of speech collected).  Returns raw 16 kHz int16 PCM or None."""
        if self.active == "text":
            self._listen_text(prompt or "[AK] Speak now (reference recording)")
            return None

        import struct

        import sounddevice as sd

        print(prompt or f"[AK] Speak naturally for ~{seconds} seconds...")
        samplerate = 16000
        blocksize = 1600          # 100 ms
        speech_threshold = 80
        target_blocks = seconds * 10
        q: "queue.Queue[bytes]" = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status and os.environ.get("AK_DEBUG_AUDIO"):
                print(status, file=sys.stderr)
            q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=samplerate, blocksize=blocksize, dtype="int16",
                channels=1, device=self.device_index, callback=callback,
            ):
                audio_buffer = bytearray()
                speech_blocks = 0
                silence_blocks = 0
                while True:
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    samples = struct.unpack(f"<{len(data)//2}h", data)
                    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                    if rms > speech_threshold:
                        speech_blocks += 1
                        silence_blocks = 0
                    else:
                        silence_blocks += 1
                    audio_buffer.extend(data)
                    if speech_blocks >= 40 and silence_blocks >= 30:
                        return bytes(audio_buffer)
                    if speech_blocks >= target_blocks + 20:
                        return bytes(audio_buffer)
        except Exception as exc:
            print(f"[voice] reference capture error ({exc}).")
            return None

    # -- backends -------------------------------------------------------
    def _say_heard(self, text: str, extra: str = "") -> None:
        """Show the user's transcript — typed out like the rest of the chat."""
        delay = (self._type_speed / 1000.0) if self._type_effect else 0.0
        typewrite(f"{text}{extra}", "[STT] You: ", delay)

    def _listen_text(self, prompt: str) -> Optional[str]:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        except Exception:
            return None
        return text.lower() or None

    def _listen_google(self, prompt: str) -> Optional[str]:
        import speech_recognition as sr
        import sounddevice as sd
        import struct

        print(prompt)
        samplerate = 16000
        blocksize = 1600  # 100ms at 16kHz
        q: "queue.Queue[bytes]" = queue.Queue()
        start_time = time.time()

        def callback(indata, frames, time_info, status):
            if status and os.environ.get("AK_DEBUG_AUDIO"):
                print(status, file=sys.stderr)
            q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=callback,
            ):
                threshold = 60
                min_blocks = 3
                silence_blocks = 0
                speech_blocks = 0
                audio_data = bytearray()
                recording = False

                while True:
                    if (time.time() - start_time) > 10:
                        break
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    samples = struct.unpack(f"<{len(data)//2}h", data)
                    rms = (sum(s*s for s in samples) / len(samples)) ** 0.5

                    if rms > threshold:
                        if not recording:
                            recording = True
                        speech_blocks += 1
                        silence_blocks = 0
                    else:
                        silence_blocks += 1

                    if recording:
                        audio_data.extend(data)
                        if silence_blocks >= self._endpoint_silence_blocks:
                            break

                if len(audio_data) < blocksize * min_blocks:
                    return None

                if self._local_stt is not None and self.active in ("whisper", "auto", None):
                    t0 = time.time()
                    text = self._transcribe_active(bytes(audio_data), samplerate)
                    if text:
                        self._say_heard(text, f"   ({time.time() - t0:.2f}s)")
                        return text.lower()
                    return None

                audio = sr.AudioData(bytes(audio_data), samplerate, 2)
                try:
                    text = self._recognizer.recognize_google(audio, language=self.language)
                    self._google_fail_streak = 0
                except sr.UnknownValueError:
                    # Google heard audio but made no words of it — almost always
                    # silence or background noise, NOT a broken key. Google is
                    # retried next time (session never downgrades); the offline
                    # net is tried quietly for this one line only, so the
                    # console stays clean instead of logging every noise blip.
                    loc = self._transcribe_local(
                        bytes(audio_data), samplerate).lower()
                    if loc:
                        self._say_heard(loc)
                        return loc
                    return None
                if text:
                    self._say_heard(text)
                    return text.lower()
                return None
        except sr.RequestError:
            # Google unreachable (no internet/DNS). Answer from the offline net
            # for THIS line — without waiting for it to load: the warmup starts
            # in the background now, so the model is ready next time. With
            # stt_engine="google" the session NEVER permanently downgrades, so
            # listening returns to Google the moment the network does.
            self._google_fail_streak = getattr(self, "_google_fail_streak", 0) + 1
            print("[voice] Google STT unreachable "
                  f"(streak {self._google_fail_streak}) — offline net for now.")
            if self._google_fail_streak >= 3 and self.engine != "google":
                self._fallback_offline()
                if self.active == "vosk":
                    return self._listen_vosk(prompt)
            self._ensure_local_warm()
            return self._transcribe_local(bytes(audio_data), samplerate).lower() or None
        except sr.UnknownValueError:
            return None
        except Exception as exc:
            print(f"[voice] STT error ({exc}).")
            return None

    def _listen_vosk(self, prompt: str) -> Optional[str]:
        import sounddevice as sd
        from vosk import KaldiRecognizer

        print(prompt)
        samplerate = 16000
        q: "queue.Queue[bytes]" = queue.Queue()

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status and os.environ.get("AK_DEBUG_AUDIO"):
                print(status, file=sys.stderr)
            q.put(bytes(indata))

        rec = KaldiRecognizer(self._vosk_model, samplerate)
        try:
            with sd.RawInputStream(
                samplerate=samplerate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=callback,
            ):
                silence_blocks = 0
                while True:
                    data = q.get()
                    if rec.AcceptWaveform(data):
                        result = json.loads(rec.Result())
                        text = result.get("text", "").strip()
                        if text:
                            print(f"🗣️  You: {text}")
                            return text.lower()
                        silence_blocks += 1
                        if silence_blocks >= 3:
                            return None
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] Vosk error ({exc}).")
            return None
