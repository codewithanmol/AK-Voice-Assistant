"""
╔══════════════════════════════════════════════════════╗
║              AK VOICE ASSISTANT                      ║
║         Created by Anmol Tyagi                       ║
║   Ansecrate Database Set                             ║
╚══════════════════════════════════════════════════════╝

Voice-controlled AI assistant with wake word, natural conversation,
system skills, and a local-first task engine (AK Local / AK Fast).
"""

from __future__ import annotations

import os
import re
import sys
import glob
import shutil
import json
import datetime
import subprocess
import zipfile
import tarfile
import urllib.request
import urllib.parse
import threading
import time
# cv2 (OpenCV) is ~4.5 s to import and is only needed for the rare "face
# intel" feature. Import it lazily there instead of at startup.

# Ensure the project root is on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Make console output UTF-8-safe even when redirected to a file/pipe
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from AK import Speaker, Listener, Brain, Skills, TaskEngine, Automations, load_config, abspath
from AK.voice import WAKE_PHRASES, SLEEP_PHRASES, _wake_hit, strip_wake
from AK.voiceprint import Voiceprint
from AK.face_lock import FaceLock
from AK.gita import list_moods, should_offer_mood_shloka
from AK.local_features import ReminderManager
from AK.memory import Memory
from AK.dictionary import console_detail, is_known_unit
from AK.learning import LearningEngine
from AK.self_learn import SelfLearner
from AK.smalltalk import SmallTalk
from AK import stt_fix
from AK.ws_broadcast import start as _ws_start, broadcast as _ws_broadcast_raw
from AK import island as _island
from AK.mailer import Mailer


def _ws_broadcast(sender: str, message) -> None:
    """Mirror one line to the WebSocket layer and the Dynamic Island."""
    try:
        _ws_broadcast_raw(sender, message)
    except Exception:
        pass
    if sender == "user":
        try:
            _island.notify(str(message), "Boss", "user")
        except Exception:
            pass


def _make_coding_agent(cfg, force: str = ""):
    """Pick the coding/task engine.

    config: brain.agent_backend = "auto" | "local" | "engine"
      - local    -> AK's own local engine (own model + tools), offline
      - engine -> the fast external engine
      - auto     -> local if ready, else the fast engine
    `force` overrides the config for the current session ("local" | "engine").
    """
    backend = str(force or (cfg.get("brain", {}) or {}).get("agent_backend", "auto")).lower()

    def _engine():
        return TaskEngine(cfg)

    def _try_local():
        try:
            from AK import LocalAgent
            la = LocalAgent(cfg)
            if la.available:
                print(f"[AK] task engine: local ({la.version})")
                return la
            print("[AK] local engine not ready; using the fast engine")
        except Exception as exc:  # noqa: BLE001
            print(f"[AK] local engine failed ({exc}); using the fast engine")
        return None

    if backend == "engine":
        return _engine()
    if backend in ("local", "none"):
        return _try_local() or _engine()
    # auto
    return _try_local() or _engine()


# Collapsed keys for multi-word shape names, e.g. "ice cream" -> "icecream".
_PHRASE_WORDS = {"icecream", "hotdog", "bellpepper", "friedchicken"}

# Spoken form -> collapsed key, so "ice cream banao" reaches the visualizer's
# "icecream" entry instead of dying on the space.
_PHRASE_COLLAPSE = {"ice cream": "icecream", "hot dog": "hotdog",
                    "bell pepper": "bellpepper",
                    "fried chicken": "friedchicken"}

# Sleep-mode prompts, kept in one place so every path stays in sync.
_SLEEP_PROMPT_SNAP = "[AK] Sleeping... Snap fingers, or say 'ak'"
_SLEEP_PROMPT_PLAIN = "[AK] Sleeping... Say 'ak' to wake me"


# Single-token build verbs + two-token phrases, both token-based (never a
# loose substring — otherwise "banana khaya" would match inside "banana").
_SHAPE_VERBS = {"banao", "bana", "ban", "banakar", "banake", "banaa",
                "dikhao", "dikha", "dikho", "dikhaao", "dikhayo",
                "show", "make", "draw", "create", "karo", "dekho",
                "lao", "lana"}
_SHAPE_VERB_PHRASES = ("bana do", "ban do", "dikha do", "dikha ke",
                       "kar do", "de do", "dekh ke", "bana ke",
                       "dikha de", "la ke", "show me")

# Single-token play verbs (regex, consumed inline).
_PLAY_VERBS = '\\b(?:play|listen to|play karo|play kro|chala do|chalao|chla do|chlao|chala|baja do|bajao|baja|laga do|dikha do)\\b'

# Topics that need no media playback.
_NO_MEDIA = ('alarm', 'timer', 'remind', 'reminder', 'wallpaper', 'theme', 'background', 'patch', 'fantasy', 'ringtone set')

# ── Paint generator gate ───────────────────────────────────────────────────
# AK/paintgen.py draws real PNG artwork offline ("ak sunset painting banao").
# Two guards keep it from hijacking other commands:
#   * a drawing word must be present (PAINT_WORDS holds them) — so a bare
#     mention like "star" or "ball" in chat never paints anything,
#   * the sentence must not START with a task keyword — "note banao",
#     "todo add", "bookmark save" are real commands, not drawing requests.
_PAINT_SKIP_HEADS = frozenset({
    "note", "notes", "reminder", "remind", "todo", "task", "tasks",
    "expense", "kharcha", "bookmark", "alarm", "timer", "event",
    "appointment", "translate", "dictionary", "meaning", "wiki",
    "wikipedia", "search", "google", "news", "weather", "mausam",
    "git", "commit", "volume", "screenshot", "download", "dataset",
    "website", "site", "app", "game", "khel", "apk", "script", "code",
    "file", "folder", "open", "close", "run", "install", "delete",
    "expense", "total", "add", "save", "yaad", "likh", "likho", "bhej",
})

# ── Two drawing lanes, kept strictly apart ──────────────────────────────────
# AK can "draw" in two very different ways and the user picks one by wording:
#
#   * say "paint" (or draw/sketch/picture/poster/logo/art…) → the PAINT lane:
#     AK/paintgen.py renders a real PNG file and opens it in MS Paint.
#   * anything else ("kutta banao", "pizza dikhao", "krishna bana") → the
#     SHAPE lane: the stardust visualizer stamps that word in its window.
#
# So the art word is the ONLY thing that switches lanes. No art word = the
# visualizer owns the request, exactly as the user asked.
_PAINT_ART_RE = re.compile(
    r"\b(paint\w*|drawing|draw\w*|sketch\w*|picture|pictures|pic|image|"
    r"images|art|arts|artwork|poster|logo|banner|canvas|mural\w*|"
    r"tasveer|chitra|illustrat\w*|illustration|portrait|scenery)\b")


# Hinglish/English grammar glue that must never be treated as a shape name.
# These are the words that appear in almost every spoken sentence, so a single
# one of them in the vocabulary turns the visualizer into a hijacker: it runs
# BEFORE the skill handlers, so "8 digit ka password banao" would render a
# "ka" and never reach the password generator.
_VIZ_STOPWORDS = frozenset({
    "ka", "ki", "ke", "ko", "se", "me", "mein", "par", "pe", "aur", "hai",
    "hain", "ho", "tha", "thi", "the", "kar", "karo", "karte", "karta",
    "bhai", "bhaiya", "ya", "phir", "tak", "vala", "wali", "waala", "liye",
    "sab", "kuch", "koi", "na", "mat", "hi", "bhi", "ab", "jab", "tab",
    "mera", "meri", "tera", "apna", "mujhe", "tumko", "aapko", "kya",
    "kaun", "kahan", "kyun", "kaise", "kyu", "itna", "utna", "zyada", "kam",
    "mera", "tera", "is", "in", "on", "at", "by", "for", "with", "from",
})


# Every word the stardust visualizer can stamp (hand-drawn shapes + the emoji
# library in AK/visualizer_py.py), English and Hinglish.
_VIZ_SHAPES = frozenset(w.strip('"') for w in """
    "indradhanush" "bajrangbali" "brokenheart" "shimlamirch" "vighnaharta" "bellpepper" "sbsandwich"
    "strawberry" "sudarshana" "watermelon" "aeroplane" "butterfly" "chhipkali" "chocolate" "crocodile"
    "kharghosh" "lightbulb" "lightning" "magarmach" "pineapple" "raghunath" "saraswati" "sudarshan"
    "broccoli" "choklate" "chowmein" "computer" "cucumber" "eggplant" "elephant" "ganeshji" "gauraiya"
    "icecream" "kabootar" "khargosh" "maadurga" "madhukhi" "mountain" "mushroom" "nashpati" "rechchha"
    "samundar" "sandwich" "sayaikal" "scorpion" "umbrella" "anguthi" "baarish" "baingan" "balloon"
    "bansuri" "battakh" "battery" "bhediya" "biscuit" "buffalo" "chaawal" "chhatri" "chicken" "chidiya"
    "coconut" "cupcake" "dhanush" "dolphin" "elaachi" "gamepad" "ganpati" "ghongha" "glasses" "gularma"
    "hanuman" "kachhua" "krishan" "krishna" "lakshmi" "mahadev" "maindak" "makkhan" "narayan" "nariyal"
    "noodles" "octopus" "peacock" "penguin" "popcorn" "rainbow" "rooster" "santara" "shankar" "shriram"
    "sparkle" "sparrow" "suar ka" "tamatar" "tarbooz" "trishul" "tutadil" "volcano" "ananas" "angoor"
    "baadal" "bajana" "banana" "bandar" "bastar" "bhains" "bhallu" "bichoo" "bunder" "burger" "butter"
    "cactus" "camera" "carrot" "chaabi" "chaand" "chakra" "chamak" "chawal" "cheese" "chehra" "cherry"
    "chilli" "chinti" "chuaha" "coffee" "cookie" "donkey" "double" "duniya" "flower" "ganesh" "garlic"
    "grapes" "guitar" "gulaab" "gusssa" "haathi" "hotdog" "jahaaz" "jhinga" "kachua" "kaffee" "kaktus"
    "kheera" "krishn" "lahsun" "laptop" "lizard" "machli" "makaan" "maruti" "medhak" "mendak" "mirchi"
    "mobile" "monkey" "murali" "murghi" "naagin" "narial" "orange" "pahaad" "panchi" "paneer" "pankhi"
    "parrot" "peanut" "pencil" "pigeon" "potato" "rabbit" "rocket" "santra" "shrimp" "sitara" "spider"
    "toffee" "tomato" "trophy" "turtle" "vishnu" "angry" "apple" "badal" "bakra" "bakri" "bappa" "bhole"
    "bijli" "billi" "bread" "camel" "candy" "chand" "chips" "chuha" "chuhe" "cloud" "crown" "curry"
    "cycle" "diyas" "donut" "doodh" "durga" "eagle" "earli" "earth" "flute" "fries" "gaadi" "gaana"
    "gadha" "gajar" "garud" "ghadi" "ghoda" "ghora" "globe" "hansi" "happy" "heart" "hiran" "horse"
    "house" "human" "juice" "kalam" "kamal" "kekra" "kelaa" "kitab" "krsna" "kulfi" "kumbh" "kutta"
    "kutte" "lahar" "laugh" "laxmi" "lemon" "lomdi" "lotus" "makai" "makdi" "mango" "mawri" "mirch"
    "money" "moong" "mouse" "murga" "murgi" "music" "nasta" "nauka" "nimbu" "omkar" "onion" "paani"
    "paisa" "paise" "panda" "patta" "pawan" "peach" "phaln" "phone" "phool" "pizza" "plane" "pyaaz"
    "raghu" "saand" "sabzi" "shark" "sheep" "shiva" "shock" "simba" "singh" "smile" "snail" "snake"
    "suraj" "sushi" "taara" "tiger" "titli" "tohfa" "train" "trens" "truck" "tulip" "udaas" "veena"
    "wahil" "watch" "water" "whale" "aalu" "aloo" "anda" "arhu" "baaz" "bael" "bagh" "barf" "bear"
    "bhau" "bhed" "bike" "bird" "boat" "book" "bulb" "bull" "cake" "chai" "chay" "cool" "corn" "crab"
    "deer" "diya" "duck" "face" "fire" "fish" "fool" "frog" "gaay" "gada" "gadi" "game" "ghar" "gift"
    "goat" "hans" "hanu" "haso" "ishq" "kafi" "kela" "kiss" "kiwi" "lamp" "leaf" "lion" "lock" "love"
    "mata" "milk" "moon" "more" "murg" "naav" "palm" "pani" "pear" "rail" "rain" "rama" "rice" "ring"
    "rona" "rose" "roti" "saap" "sebl" "sher" "ship" "shiv" "snow" "song" "star" "suar" "swan" "taco"
    "tala" "tara" "toad" "tota" "tree" "ullu" "waqu" "wave" "wink" "wolf" "aag" "aam" "amo" "ant"
    "aum" "bee" "bow" "bus" "car" "cat" "chu" "cow" "cry" "dil" "dog" "egg" "fox" "fry" "gai" "har"
    "hen" "key" "mor" "owl" "ped" "pen" "pet" "pig" "ram" "ras" "rat" "sad" "seb" "sun" "taj" "tea"
    "unt" "wow" "om" "ru"
""".split())

# Postpositions and grammatical glue are NOT shapes. "ka" ("of") crept into the
# list above, which made the visualizer steal any Hinglish sentence containing
# it — "8 digit ka password banao" was rendering a "ka" instead of making a
# password. Stripped here rather than deleted from the list so the original
# vocabulary above stays readable.
_VIZ_SHAPES = frozenset(w for w in _VIZ_SHAPES if w not in _VIZ_STOPWORDS)

# Longest word first, so "icecream" wins over a stray "ice" and "suar ka"
# is matched before "suar".
_VIZ_SHAPE_ORDER = tuple(sorted(_VIZ_SHAPES, key=lambda w: (-len(w), w)))

# Words that turn a shape noun into something else entirely — a health check,
# a price query or a question is never a request to stamp stardust.
_SHAPE_BLOCK_WORDS = frozenset({
    "rate", "check", "test", "testing", "how", "what", "why", "when", "which",
    "who", "price", "cost", "prices", "market", "kab", "kitna", "kitni",
    "kitne", "status", "count", "score", "temperature", "bp", "sugar",
    "report", "reports", "symptom", "symptoms", "doctor", "hospital",
    "medicine", "tablet", "pills", "diagnosis", "report card", "marks",
    "result", "results", "difference", "meaning", "kya", "kyun", "kaise",
    "kitne", "samay", "time", "date", "news", "update", "updates",
})






# Extra build verbs the shared shape verb set lacks — the English words
# "build / develop / write / code" and the common Hinglish spellings.
# Kept separate from _SHAPE_VERBS so the shape-lane vocabulary stays exactly
# as tuned; the build lane ALSO needs a CODE_WORDS hit, so "write a letter"
# never turns into a build request.
_EXTRA_BUILD_VERBS = frozenset({
    "build", "built", "develop", "write", "code", "design",
    "bnao", "banaao", "bna", "bnanao", "bnana", "bno",
})


def _has_build_verb(toks) -> bool:
    for i, tok in enumerate(toks):
        if tok in _SHAPE_VERBS or tok in _EXTRA_BUILD_VERBS:
            return True
        if i + 1 < len(toks) and " ".join(toks[i:i + 2]) in _SHAPE_VERB_PHRASES:
            return True
    return False


# ── Reply politeness filter ────────────────────────────────────────────────
# AK never speaks abusive words. Hard expletives are softened so the sentence
# keeps flowing. Animal words (kutta, suar…) are NOT in this map — they are
# valid visualizer shape names ("kutta banao") and must survive replies.
_PROFANITY_MAP = {
    "bhenchod": "dost", "behenchod": "dost",
    "madarchod": "dost", "madar": "dost",
    "chutiya": "bhai", "chutiye": "bhai", "cutiye": "bhai",
    "gandu": "bhai", "gandhoo": "bhai", "gaand": "kandhe",
    "kamina": "dost", "kamini": "dost", "harami": "beta",
    "haramkhor": "beta", "badtameez": "bhai", "badmash": "bhai",
    "buddhu": "bhai", "bakwas": "meh",
    "randi": "mahila", "bhonsdi": "dost", "bhosdi": "dost",
    "bitch": "friend", "fuck": "darn", "shit": "oops",
    "asshole": "bhai", "idiot": "bhaisaab", "stupid": "bhaisaab",
    "dumb": "bhaisaab", "damn": "hay", "hell": "hay",
}

# Hard expletives: even ONE mention of these means the user is abusing.
_HARD_ABUSE = frozenset({
    "bhenchod", "behenchod", "madarchod", "madar",
    "chutiya", "chutiye", "cutiye", "gandu", "gandhoo",
    "randi", "bhonsdi", "bhosdi", "fuck", "bitch", "asshole",
})

# word-boundary regex so "class", "suarez" etc. are never touched
_PROFANITY_RE = None



_PROFANITY_MAP = {
    'bhenchod': 'dost', 'behenchod': 'dost', 'madarchod': 'dost', 'madar': 'dost',
    'chutiya': 'bhai', 'chutiye': 'bhai', 'cutiye': 'bhai', 'gandu': 'bhai',
    'gandhoo': 'bhai', 'gaand': 'kandhe', 'kamina': 'dost', 'kamini': 'dost',
    'harami': 'beta', 'haramkhor': 'beta', 'badtameez': 'bhai', 'badmash': 'bhai',
    'buddhu': 'bhai', 'bakwas': 'meh', 'randi': 'mahila', 'bhonsdi': 'dost',
    'bhosdi': 'dost', 'bitch': 'friend', 'fuck': 'darn', 'shit': 'oops',
    'asshole': 'bhai', 'idiot': 'bhaisaab', 'stupid': 'bhaisaab', 'dumb': 'bhaisaab',
    'damn': 'hay', 'hell': 'hay',
}

_HARD_ABUSE = frozenset({'gandu', 'madarchod', 'madar', 'bitch', 'gandhoo', 'asshole', 'fuck', 'randi', 'chutiye', 'cutiye', 'behenchod', 'bhosdi', 'bhenchod', 'chutiya', 'bhonsdi'})

_PROFANITY_RE = None

def _build_profanity_re():
    global _PROFANITY_RE
    if _PROFANITY_RE is not None:
        return _PROFANITY_RE
    escaped = [w.strip() for w in _PROFANITY_MAP]
    escaped = [re.escape(w) for w in escaped if w]
    _PROFANITY_RE = re.compile(
        r"(?<![a-zA-Z])(?:" + "|".join(escaped) + r")(?![a-zA-Z])",
        re.IGNORECASE)
    return _PROFANITY_RE


def _clean_reply(text: str) -> str:
    """Sanitize profanities in a spoken reply (polite in-place replace)."""
    profanity_re = _build_profanity_re()
    if profanity_re is None:
        return text

    def rep(m):
        return _PROFANITY_MAP.get(m.group(1), m.group(0))

    return profanity_re.sub(rep, text)

def _is_abusive(text: str) -> bool:
    """Pure abuse from the user → AK gently declines instead of echoing it."""
    if not text:
        return False
    low = text.lower()
    words = [w.strip("!?.,;:'\" ") for w in low.replace("-", " ").split()
             if w.strip()]
    hits = [w for w in words if w in _HARD_ABUSE]
    if len(hits) >= 2:
        return True
    # a single hard expletive in a very short message is pure abuse
    if hits and len(text.strip()) <= 14:
        return True
    return False


# Trailing Hinglish command words that belong to the request, not the title:
# "play gaana chalao" must search for "gaana", not for "gaana chalao".
_SONG_TRAIL_VERBS = re.compile(
    r"\s+(?:chala\s+do|chalao|chla\s+do|chlao|baja\s+do|bajao|laga\s+do|"
    r"kar\s+do|karo|kar)\s*$", re.IGNORECASE)


def _clean_song_query(song: str) -> str:
    """Normalise the text that becomes a YouTube/Spotify search query.

    Whisper's hotword bias occasionally leaks a comma into the transcript
    ("arijit singh, song"), and the capture loop keeps the trailing period,
    so the raw match is not always what should be searched. Returns '' when
    nothing usable is left, so the caller can skip the search entirely
    instead of opening a blank one.
    """
    s = (song or "").strip()
    s = re.sub(r"\s*,\s*", " ", s)                 # hotwords can leak commas
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .!?;:~\"'")
    s = re.sub(r"\s+(?:please|plz|yaar|bhai|dost)\s*$", "", s, flags=re.IGNORECASE)
    s = _SONG_TRAIL_VERBS.sub("", s)
    return s.strip(" .!?;:~\"'") or ""


def workflow_learn_tokens(text: str) -> set:
    """Keyword set used to match a request against learned workflows."""
    try:
        from AK.workflow_learn import _tokens
        return _tokens(text)
    except Exception:
        return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
                if len(w) > 1}


class AK:
    def __init__(self, config_path: str = ""):
        cfg_path = config_path.strip() or os.path.join(_HERE, "config.json")
        self.cfg = load_config(cfg_path)

        self.name = self.cfg.get("assistant_name", "AK")
        self.wake_word = (self.cfg.get("wake_word", "ak") or "ak").lower()
        self.require_wake = self.cfg.get("require_wake_word", True)
        # Ask the user permission BEFORE sending an unmatched task to engine.
        self.ask_before_engine = self.cfg.get("ask_before_engine", True)
        self.local_agent_log = self.cfg.get("local_agent_log", True)

        self.speaker = Speaker(self.cfg.get("voice", {}), cfg_path=cfg_path)
        self.listener = Listener(self.cfg.get("voice", {}), abspath)
        self.brain = Brain(self.cfg.get("brain", {}))
        self.skills = Skills(self.cfg)
        self.smalltalk = SmallTalk(
            data_dir=os.path.join(self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"), "smalltalk"),
            conversations_dir=os.path.join(self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"), "conversations"))
        self.engine = _make_coding_agent(self.cfg)
        # Which coding/task engine the user picked this session ("local" or
        # "engine"). "" = not chosen yet. When the user picks the fast engine,
        # `_task_local_allowed` goes False so AK's own local (Ollama) engine is
        # never started for tasks.
        self.task_backend = ""
        self._task_local_allowed = True
        # Last task handed to the fast engine (set in _route_to_engine).
        # Must exist from the start — the background learner reads it.
        self._last_engine_task = ""
        self.learning = LearningEngine()
        self.self_learn = SelfLearner()
        self._last_self_learn_announce = datetime.datetime.now()

        from AK.config import get_data_dir
        data_path = get_data_dir()
        self.auto_learn = self.cfg.get("auto_learn_automations", True)
        self.automations = Automations(data_path)
        self._chat_learned_this_run = set()   # avoid repeating the same fact reply

        self.running = True
        self.typing_mode = False
        self.chat_mode = False
        self._asleep = True          # start in background (sleeping) mode
        self._awaiting_mood = False  # mood-based shloka flow
        self._mood_offered = False
        self._voice_fails = 0        # consecutive voice listen failures
        self._interrupted_spoken = False  # user barged in during last reply
        self._viz_phrase = ""             # last spoken phrase (visualizer colour)
        self._viz_phrase_seq = 0          # bumps on every new spoken phrase
        self.coach = None                 # workflow learner (lazy, see _wf_coach)
        self._wf_pending = None           # workflow waiting for a "haan"
        self._email_pending = None        # composed email waiting for confirmation
        try:
            self.mailer = Mailer(self.cfg)
        except Exception:
            self.mailer = None

        # ── WebSocket broadcast layer (Windhawk Dynamic Island) ──
        try:
            _ws_start()
            print("[WS-BROADCAST] Integration: OK | Broadcasting user/assistant to ws://127.0.0.1:8080")
        except Exception as _wse:
            print(f"[WS-BROADCAST][SKIP] {_wse}")

        # ── Dynamic Island notification bridge (native toast cards) ──
        try:
            _island.start()
            print("[ISLAND] Notification bridge: OK | Replies appear in Dynamic Island")
        except Exception as _ise:
            print(f"[ISLAND][SKIP] {_ise}")

        # Reminder callback → speak when due
        ReminderManager.set_callback(self._say)
    def _say(self, text: str) -> None:
        if not text:
            return
        text = _clean_reply(text)
        try:
            _island.notify(text, "AK", "assistant")
        except Exception:
            pass
        custom_k = getattr(self, "_viz_custom_k", None)
        if self.typing_mode:
            if custom_k:
                self._viz_state(f"custom:{custom_k}")
            else:
                self._viz_state("responding", text)
            try:
                self.speaker.show(text)
            except Exception:
                try:
                    print(f"[AK] {text}")
                except UnicodeEncodeError:
                    print(f"[AK] {text.encode('ascii','replace').decode('ascii')}")
            return

        def _on_audio_start(dur: float = 0.0):
            if custom_k:
                self._viz_state(f"custom:{custom_k}")
            else:
                self._viz_state("responding", text, dur)

        voice_cfg = self.cfg.get("voice", {}) or {}
        barge = voice_cfg.get("interrupt", True)
        if barge and not self.chat_mode:
            self.listener.start_interrupt_monitor(self._on_interrupt_spoken)
        try:
            self.speaker.say(text, on_audio_start=_on_audio_start)
        finally:
            self.listener.stop_interrupt_monitor()

    def _on_interrupt_spoken(self) -> None:
        """Fired from the monitor thread when the user says wait/stop while
        AK is speaking — cut the speech and let them take over."""
        self._interrupted_spoken = True
        self.speaker.interrupt()

    def _pick_voice_interactive(self):
        print("\n" + self.speaker.list_voices())
        choice = input("Choose a voice (name or number, Enter for default): ").strip()
        if choice:
            msg = self.speaker.switch_voice(choice)
            print(f"[voice] {msg}")
    def _handle_switch_voice(self, text: str) -> str | None:
        rest = text.lower().strip()
        m = re.search(r"(?:switch|change)\s+voice\s+(?:to\s+)?(\w+)", rest)
        if m:
            return self.speaker.switch_voice(m.group(1).title())
        m = re.search(r"(?:switch|change)\s+to\s+(\w+)", rest)
        if m:
            return self.speaker.switch_voice(m.group(1).title())
        return ("Say 'switch to' followed by a voice name or number.")
        """Is this natural conversation (small talk, feelings, opinions —
        the ONLY case the API brain is used)? Everything factual/task-like
        is deliberately NOT chat so it goes to engine instead."""

    _WIKI_AUTO = None

































    def _wiki_auto_answer(self, text: str) -> str | None:
        """Fast Wikipedia answer for basic factual questions, or None if the
        question is not a clear 'look this up' style query (→ engine)."""
        if not text:
            return None
        t = re.sub(r"[\?\.\!\s]+$", "", (text or "").strip().lower())
        if not t or len(t) > 200:
            return None

        patterns = [
            r"what\s+(?:is|are|was)\s+(?:the\s+)?capital\s+of\s+(.+)$",
            r"capital\s+of\s+(.+)$",
            r"what\s+(?:is|are|was)\s+(?:the\s+)?population\s+of\s+(.+)$",
            r"population\s+of\s+(.+)$",
            r"what\s+(?:is|are|was)\s+(?:the\s+)?currency\s+of\s+(.+)$",
            r"currency\s+of\s+(.+)$",
            r"who\s+(?:is|was)\s+(?:the\s+)?(?:prime\s+minister|president|pm)\s+of\s+(.+)$",
            r"pm\s+of\s+(.+)$",
            r"who\s+(?:is|was|are)\s+(.+)$",
            r"what\s+(?:is|are|was)\s+(?:a\s+|an\s+|the\s+)?(.+)$",
            r"who\s+(?:created|made|invented)\s+(.+)$",
            r"what\s+(?:is|are)\s+(.+)$",
            r"what'?s?\s+(.+)$",
            r"when\s+(?:was|were)\s+(.+?)\s+(?:founded|born|created|built|established)$",
        ]
        for pat in patterns:
            m = re.search(pat, t)
            if not m:
                continue
            q = re.sub(r"\b(please|plz|bhai|bhaiya|ji|batao|bata)\s*$", "", (m.group(1) or "").strip("'\"")).strip()
            # Skip tiny or obviously conversational fragments.
            if len(q) < 2 or len(q.split()) > 6:
                continue
            if re.search(r"\b(news|headline|weather|mausam|score|time|date|"
                         r"today|tonight|tomorrow|khabar|samachar|happy|sad|"
                         r"name|you|your|me|my)\b", q):
                continue
            try:
                import wikipedia
                wikipedia.set_lang("en")
                summary = wikipedia.summary(q, sentences=self.skills.wikipedia_sentences)
            except wikipedia.DisambiguationError as e:
                if not e.options:
                    return None
                try:
                    summary = wikipedia.summary(e.options[0],
                                                sentences=self.skills.wikipedia_sentences)
                except Exception:
                    return None
            except Exception:
                return None
            if not summary or len(summary) < 15:
                return None
            return summary.strip()
        return None

    def _route_to_engine(self, text: str) -> str:
        # Smart routing:  local skills/rules run earlier in respond(); this is
        # reached only for requests with no local answer.
        #   1. Casual chat → the API brain (Groq), never Ollama automatically.
        #   2. Everything else (tasks, news, facts, lookups) → engine, which
        #      has web tools and can actually DO things. Never refuse.
        if self._is_casual_chat(text):
            memory_ctx = self.skills.memory.context_text()
            enriched = f"{memory_ctx}\n\nUser: {text}" if memory_ctx else text
            # Rule-mode brain (no Ollama/Groq) sirf canned lines deta hai — wo
            # answer nahi hai. Jab local brain ke paas real LLM nahi hai, engine
            # hi baat karega. Karra LLM ho toh brain pe hi chhodo.
            if self.brain.backend != "rule":
                reply = self.brain.think(enriched)
                if reply and "hit a snag" not in reply:
                    return reply
        if self.engine.available:
            memory_ctx = self.skills.memory.context_text()
            enriched = f"{memory_ctx}\n\nUser: {text}" if memory_ctx else text
            self._last_engine_task = enriched
            return self._handle_engine(enriched)
        # No engine installed → fall back to whatever brain is available.
        return self.brain.think(text)


    def _handle_skill(self, text: str) -> tuple | None:
        """Match a built-in skill and return (response_text, speak: bool).
        Returns None if no skill matched."""
        t = text.lower().strip()

        # ── Teach custom rules (checked FIRST so teach commands aren't intercepted) ──
        m = re.search(r"(?:teach|learn)\s+(?:ai|ak|you)\s+(.+?)\s+to\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.custom_rule_add(m.group(1).strip(), m.group(2).strip())
            return (msg, True)

        # ── Everyday Hinglish first ─────────────────────────────────
        # The handlers below are English-first, so spoken Hinglish used to
        # miss all of them and get shipped to engine (15-35 s, CPU pegged).
        # hindi_routes understands the phrasings actually spoken and keeps the
        # answer local and instant. Returns None when it has no match.
        try:
            from AK import hindi_routes

            routed = hindi_routes.match(self, text)
            if routed:
                return routed
        except Exception:  # noqa: BLE001
            pass

        # ── Custom rules (taught by user) ──
        custom_reply = self.skills.custom_rule_find(t)
        if custom_reply is not None:
            return (custom_reply, True)

        # ── Email (compose -> confirm -> send) ──
        _em = self._handle_email_cmd(text)
        if _em is not None:
            return _em

        # ── AK HUD: gesture control, holograms, vision intel ──
        try:
            from AK import ak_commands

            routed = ak_commands.match(self, text)
            if routed:
                return routed
        except Exception as exc:  # noqa: BLE001
            print(f"[ak] handler error: {exc}")

        # -- AK Finding (OSINT investigation) --
        try:
            if getattr(self, "osint", None) is None:
                from AK.osint_finding import OsintFinding
                self.osint = OsintFinding(self.cfg, say=self._say)
            o_target = self.osint.detect(text)
            if o_target:
                return self.osint.start(o_target)
        except Exception as _osint_exc:  # noqa: BLE001
            print(f"[osint] handler error: {_osint_exc}")

        # ── Automations (learned custom automations) ──
        m = re.search(r"(?:forget|remove|delete)\s+(?:the\s+)?automation\s+(?:for\s+)?(.+?)$", t)
        if m:
            if self.automations.remove(m.group(1).strip()):
                return (f"Forgot the automation for '{m.group(1).strip()}'.", True)
            return ("No such automation found.", True)
        if re.search(r"\b(show|list)\s+(?:my\s+)?automations?\b", t):
            items = self.automations.list_all()
            if not items:
                return ("You have no custom automations yet. Ask me to do a task and I'll remember it.", True)
            names = "; ".join(a.get("match", "?") for a in items[:20])
            return (f"Your {len(items)} automations: {names}.", True)

        # ── Chat mode toggle ──
        if re.search(r"\b(open|close)\s+chat\b", t):
            self.chat_mode = "open" in t
            return (None, False)

        # ── Typing / Voice mode ──
        if re.search(r"\b(open|enable|start)\s+typing\b", t) or re.search(r"\btype\s+mode\b", t):
            self.typing_mode = True
            return ("Typing mode on. I will only type responses.", True)
        if re.search(r"\b(voice mode|speak mode|close typing|disable typing)\b", t):
            self.typing_mode = False
            return ("Voice mode on. I will speak responses.", True)

# ── Voice list (how many / which voices) ──
        if re.search(r"\b(kya|kitni|kaun|kaunsa|kaunsi|kaun si|kon si|name|naam|list)\b.*\b(voice|speaker)\b", t) \
                or re.search(r"\b(voice|speaker)\b.*\b(list|kitni|kaun|name|naam)\b", t):
            return (self.speaker.list_voices(), True)

        # ── Voice switch (only with voice/speaker context or a known voice
        #    name — a bare "change/switch to X" is NOT a voice command, e.g.
        #    "change to python" must not hijack the voice engine) ──
        if re.search(r"\b(switch|change)\b", t) and re.search(
                r"\b(voice|speaker|madhur|default)\b", t):
            return (self._handle_switch_voice(t), True)

        # ── Spell (early, so greedy rules below can't capture the word) ──
        m = re.search(r"(?:how\s+(?:do\s+you\s+|to\s+)?spell|spell)\s+(?:the\s+word\s+)?(.+?)$", t)
        if m and len(m.group(1).strip()) >= 2:
            reply, _ = self.skills.spell(m.group(1).strip().strip("?"))
            return (reply, True)

        # ── Offline Hindi <-> English translation ──────────────────
        m = re.search(r"\b(?:can\s+you\s+)?(?:please\s+)?translate\s+['\"]?(.+?)['\"]?\s+(?:to|into|in)\s+(hindi|english)\b", t)
        if m:
            reply, _ = self.skills.translate_text(m.group(1).strip().strip("?\"'"), m.group(2))
            return (reply, True)
        m = re.search(r"\b(?:what\s+is|what[']?s|how\s+do\s+(?:you|i)\s+say|how\s+to\s+say|tell\s+me)\s+(.+?)\s+(?:in|into)\s+(hindi|english)\b", t)
        if m and len(m.group(1).strip()) >= 2:
            reply, _ = self.skills.translate_text(m.group(1).strip().strip("?\"'"), m.group(2))
        m = re.search(r"\b([a-zA-Z][a-zA-Z\' .-]{0,40}?)\s+in\s+(hindi|english)\s*$", t)
        if m and 1 <= len(m.group(1).split()) <= 4:
            reply, _ = self.skills.translate_text(m.group(1).strip().strip("?\"'"), m.group(2))
            return (reply, True)
        if re.search(r"\btranslate\b", t):
            return ("Say it like: translate 'good morning' to hindi, or apple ka hindi kya hai, "
                    "or नमस्ते ka english kya hai. All translation is offline.", True)
        m = re.search(r"\b(translation|translate)\s+(?:status|info|count|words|history|where|path|saved)\b|(how\s+many\s+words)\s+(?:in\s+)?(?:your\s+)?(?:translation|hindi|english)", t)
        if m:
            reply, _ = self.skills.translation_info()
            return (reply, True)

        # ── Weather (live, with an offline disk cache as fallback) ──
        if re.search(r"\b(mausam|weather)\b", t) and not re.search(r"\bnews\b", t):
            wcity = ""
            mw = re.search(r"^(?:what(?:'s| is)\s+|tell\s+me\s+|check\s+|batao\s+|bata\s+)?"
                           r"(.+?)\s+(?:ka|kika|kaunsa|ki)\s+(?:mausam|weather)\b", t)
            if mw:
                wcity = mw.group(1).strip(" ?.,")
            if not wcity:
                # Longest alternative first: with "bata|batao" the engine takes
                # "bata" out of "batao" and the city comes out as "o delhi".
                mw = re.search(r"\b(?:mausam|weather)\b\s*"
                               r"(?:batao|bataiye|bataye|bata|kya|karo|check|dekho|"
                               r"status|update|bata\s*do)?\s*"
                               r"(?:in|at|for|of|ka|kaunsa)?\s*(.+?)$", t)
                if mw:
                    wcity = mw.group(1).strip(" ?.,")
            wcity = re.sub(r"^(?:aaj|abhi|current|latest)\s+", "", wcity).strip()
            # Trailing postposition belongs to the sentence, not the city:
            # "mausam batao delhi ka" -> "delhi", not "delhi ka".
            wcity = re.sub(r"\s+(?:ka|ki|ke|kika|kaunsa|kaunsi|me|mein|ke\w*)$",
                           "", wcity).strip(" ?.,'\"")
            reply, _ = self.skills.power.weather(wcity)
            return (reply, True)

        # ── System doctor (one-shot full health check) ───────────────
        if re.search(r"\b(system doctor|health check|pc health|system health|checkup|doctor karo|diagnose)\b", t):
            reply, _ = self.skills.power.system_doctor()
            return (reply, True)

        # ── Git helper (local repos) ─────────────────────────────────
        mg = re.search(r"\bgit\s+(status|log|diff|branch|commit)\b(.*)$", text, re.I)
        if mg:
            cmd = mg.group(1).lower()
            rest = mg.group(2).strip()
            msg = ""
            mq = re.search(r"['\"](.+?)['\"]", rest)
            if mq:
                msg = mq.group(1)
                rest = (rest[:mq.start()] + " " + rest[mq.end():]).strip()
            else:
                mm = re.search(r"\b(?:message|msg)\b\s+(.+)$", rest, re.I)
                if mm:
                    msg = mm.group(1).strip()
                    rest = rest[:mm.start()].strip()
            repo = re.sub(r"^(?:in|for|on|inside|repo|repository|folder|dir)\s+",
                          "", rest, flags=re.I).strip(" ?.,:'\"") or "."
            pw = self.skills.power
            if cmd == "status":
                reply, _ = pw.git_status(repo)
                return (reply, True)
            if cmd == "log":
                reply, _ = pw.git_log(repo, 5)
                return (reply, True)
            if cmd == "diff":
                reply, _ = pw.git_diff(repo)
                return (reply, True)
            if cmd == "branch":
                reply, _ = pw.git_branch(repo)
                return (reply, True)
            if cmd == "commit":
                if not msg:
                    return ("Commit message kya hai? Jaise: git commit in "
                            "Documents\\myproject 'fixed login bug'.", True)
                reply, _ = pw.git_commit(repo, msg)
                return (reply, True)

        # ── Expenses (SQLite ledger) ─────────────────────────────────
        m = re.search(r"\b(expense|kharch|kharche|kharcha|spending)\s+"
                      r"(?:add|daal|dal|record|likho)\b\s*(.+?)$", t)
        if m:
            body = m.group(2).strip()
            amt = re.search(r"-?[\d,]+(?:\.\d+)?", body)
            if not amt:
                return ("Amount batao — jaise: expense add 250 chai ke liye.", True)
            tail = (body[amt.end():] + " " + body[:amt.start()]).strip(" ,-")
            parts = tail.split(None, 1)
            cat = parts[0] if parts else "other"
            note = parts[1] if len(parts) > 1 else ""
            reply, _ = self.skills.power.expense_add(amt.group(0), cat, note)
            return (reply, True)
        m = re.search(r"\b(expense|kharch|kharche|kharcha|spending)\s+"
                      r"(?:total|kitna|kitne|kitni|sum)\b\s*"
                      r"(today|aaj|week|hafte|month|mahine|all|sab)?", t)
        if m:
            reply, _ = self.skills.power.expense_total(m.group(2) or "month")
            return (reply, True)
        if re.search(r"\b(show|list)\s+(?:my\s+)?(expenses?|kharche|kharcha)\b", t):
            reply, _ = self.skills.power.expense_list(10)
            return (reply, True)
        m = re.search(r"\b(?:remove|delete)\s+(?:the\s+)?expense\s+(\d+)", t)
        if m:
            reply, _ = self.skills.power.expense_remove(int(m.group(1)))
            return (reply, True)

        # ── Bookmarks ────────────────────────────────────────────────
        # Both word orders are spoken: "add bookmark X as Y" and the more
        # natural Indian-English "bookmark add X as Y".
        m = re.search(r"\b(?:add|save)\s+bookmark\s+(.+?)\s+(?:as|for|with title|named)\s+(.+?)$", t) or \
            re.search(r"\bbookmark\s+(?:add|save)\s+(.+?)\s+(?:as|for|with title|named)\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.power.bookmark_add(m.group(2), m.group(1))
            return (reply, True)
        m = re.search(r"\b(?:add|save)\s+bookmark\s+(?:for\s+)?(.+?)$", t) or \
            re.search(r"\bbookmark\s+(?:add|save)\s+(?:for\s+)?(.+?)$", t)
        if m:
            reply, _ = self.skills.power.bookmark_add("", m.group(1))
            return (reply, True)
        if re.search(r"\b(show|list)\s+(?:my\s+)?bookmarks?\b", t) or \
           re.search(r"\bbookmarks?\s+(?:dikhao|dikhawao|list|batao|show)\b", t):
            reply, _ = self.skills.power.bookmark_list("")
            return (reply, True)
        m = re.search(r"\b(?:open|open up)\s+bookmark\s+(.+?)$", t) or \
            re.search(r"\bbookmark\s+(?:open|kholo)\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.power.bookmark_open(m.group(1))
            return (reply, True)
        m = re.search(r"\b(?:remove|delete)\s+bookmark\s+(\d+)", t) or \
            re.search(r"\bbookmark\s+(?:remove|delete|hatado)\s+(\d+)", t)
        if m:
            reply, _ = self.skills.power.bookmark_remove(m.group(1))
            return (reply, True)

        # ── Text tools (JSON / URL / case / count / extract) ────────
        m = re.search(r"\bformat\s+(?:the\s+)?json\b\s*(.*)$", t)
        if m and m.group(1).strip():
            reply, _ = self.skills.power.json_format(m.group(1).strip().strip(":"), "pretty")
            return (reply, True)
        m = re.search(r"\bminify\s+json\b\s*(.*)$", t)
        if m and m.group(1).strip():
            reply, _ = self.skills.power.json_format(m.group(1).strip().strip(":"), "minify")
            return (reply, True)
        m = re.search(r"\b(?:url\s+)?encode\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.power.url_encode(m.group(1), decode=False)
            if reply and "Text do" not in reply:
                return (reply, True)
        if re.search(r"\b(?:url\s+)?decode\b", t):
            arg = re.sub(r"^.*?\b(?:url\s+)?decode\b\s*", "", t).strip()
            reply, _ = self.skills.power.url_encode(arg, decode=True)
            if reply and "Text do" not in reply:
                return (reply, True)
        m = re.search(r"\b(?:convert|change|badal)\s+(?:this\s+)?(?:text|word)\s+"
                      r"(?:to|in)\s+(upper|lower|title|capital|sentence)\b\s*(.*)$", t)
        if m:
            mode = {"capital": "title", "upper": "upper", "lower": "lower",
                    "title": "title", "sentence": "sentence"}[m.group(1)]
            reply, _ = self.skills.power.text_case(m.group(2), mode)
            if reply and "Text do" not in reply:
                return (reply, True)
        m = re.search(r"\b(?:palindrome|ulta palindrome)\b", t)
        if m:
            reply, _ = self.skills.power.text_case(t[:m.start()].strip(), "palindrome")
            if reply and "Text do" not in reply:
                return (reply, True)
        m = re.search(r"\b(?:count|words?|characters?)\s+(?:in|of)\s+(?:this|the|my)\s+text\b\s*(.*)$", t)
        if m and m.group(1).strip():
            reply, _ = self.skills.power.text_count(m.group(1))
            return (reply, True)
        m = re.search(r"\b(?:find|extract|nikaal|nikal)\s+(?:all\s+)?"
                      r"(emails?|urls?|phone\s*numbers?|hashtags?)\b\s*(.*)$", t)
        if m:
            what = {"email": "email", "url": "url", "phone number": "phone",
                    "hashtag": "hashtag"}[re.sub(r"e?s$", "", m.group(1).lower())]
            reply, _ = self.skills.power.text_extract(m.group(2), what)
            return (reply, True)

        # ── Capabilities ──
        if re.search(r"\bwhat can (you|u) do\b", t) or \
           re.search(r"\b(kya|tum|aap|you)\s+(kya\s+)?(kar|karte|karta)\s+(sakte|sakta|pata)\b", t) or \
           re.search(r"\bfeatures?\s+(batao|list|dikhao|bata)\b", t) or \
           (re.search(r"\b(?:all\s+)?(your\s+)?(capabilities?|features)\b", t) and len(t.split()) <= 5) or \
           t.strip() == "help":
            msg, _ = self.skills.capabilities()
            print(msg)
            short = ("Main locally hi aapke liye kaafi kuch kar sakta hun — files, "
                     "websites, security scanning, reminders, search, sab. "
                     "Poori list console pe dekh lo, aur koi bhi task boliye.")
            return (short, True)

        # ── Data dir ──
        if re.search(r"\b(where.*(data|saved|storage|notes|tasks)|data.*(path|dir|folder|location))", t):
            msg, _ = self.skills.data_dir()
            return (msg, True)

        # ── Engine status (only when the sentence is genuinely about engine) ──
        if re.search(r"(engine|open.code).*(status|connect|available|running|work|online|present|check)", t) or \
           re.search(r"(status|connect|available|running|check).*(engine|open.code)", t):
            if self.engine.available:
                return (f"AK task engine v{self.engine.version} is connected and ready.", True)
            return ("AK task engine is not installed.", True)

        # ─────────────────── FULLY OFFLINE SKILLS ───────────────────

        # Time
        if re.search(r"\b(time|what.*time|current time|the time)\b", t) and \
           not re.search(r"for some|sometime|sometimes|long time", t):
            msg, _ = self.skills.time()
            return (msg, True)

        # Date
        if re.search(r"\b(date|what.*date|today.*date|current date)\b", t):
            msg, _ = self.skills.date()
            return (msg, True)

        # System info. NOTE: word boundaries are required — a bare "ram" used to
        # match inside "program"/"grammar", so "write a python program" returned
        # CPU stats instead of the code.
        if re.search(r"(system|computer|pc).*(info|status|stats|specs)", t) or \
           re.search(r"\b(cpu|ram|memory|disk|battery|usage)\b", t) or \
           re.search(r"\b(specs|system info)\b", t):
            msg, _ = self.skills.system_info()
            return (msg, True)

        # Wikipedia
        m = re.search(r"(wikipedia|wiki)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.wikipedia(m.group(2).strip())
            return (msg, True)

        # ── News headlines (free Google News RSS; offline → engine) ──
        if re.search(r"\b(news|headlines|khabar|samachar|samaachar|taza\s*khabar|"
                     r"aaj\s*ki\s*khabar|news\s*me|news\s*bata)\b", t):
            m = re.search(r"(news|headlines|khabar|samachar|samaachar|"
                          r"taza\s*khabar)\s+(?:about\s+|for\s+)?(.+?)$", t)
            topic = m.group(2).strip().rstrip("?") if m else "india"
            topic = re.sub(r"\b(bata|batao|sunao|lao|dedo|dikhao|kya|hai|hain|"
                           r"ho|chal\s*raha|bolo|bhai|bhaiya|re|ji|aap|bataiye|"
                           r"me|about|for|on|the|news|headlines|khabar|samachar|"
                           r"samaachar|taza|aaj|ke|ki)\b", "", topic).strip()
            if not topic or len(topic.split()) > 4:
                topic = "india"
            msg, _ = self.skills.headlines(topic)
            if not msg.lower().startswith(("could not fetch", "news unavailable",
                                           "no headlines")):
                return (msg, True)
            # RSS failed → fall through to engine for live news.

        # ── Document search ──
        m = re.search(r"\bsearch\s+(files|documents|docs)\s+for\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.search_docs(m.group(2).strip())
            return (reply, True)
        m = re.search(r"\b(find|locate)\s+(a\s+)?file\s+(called\s+|named\s+)?(.+?)$", t)
        if m:
            reply, _ = self.skills.find_file(m.group(4).strip())
            return (reply, True)

        # ─────────────────── MEMORY & REMINDERS ───────────────────

        # Remember
        m = re.search(r"\bremember\s+(?:that\s+|this\s+|about\s+me\s+)?(.+?)$", t)
        if m:
            msg, _ = self.skills.memory_add(m.group(1).strip())
            return (msg, True)

        # Introduce yourself ("my name is X" / "call me X"; bare "I am/I'm X"
        # only when X is clearly a name, not a mood/state — otherwise "I'm
        # tired" used to be stored as the user's name).
        m = re.search(r"(?:my\s+name\s+is|call\s+me)\s+(.+?)$", t)
        if not m:
            m = re.search(r"(?:i\s+am|i'm)\s+(.+?)$", t)
        if m and len(m.group(1).strip().split()) <= 4:
            myname = m.group(1).strip(" .,!?")
            _nwords = myname.lower().split()
            _not_name = {"good", "fine", "ok", "okay", "great", "well", "tired",
                         "hungry", "bored", "sad", "happy", "angry", "busy", "back",
                         "here", "ready", "sorry", "late", "done", "free", "sick",
                         "sleepy", "thirsty", "confused", "not", "sure", "feeling",
                         "going", "trying", "just", "still", "very", "so", "a", "an",
                         "the", "your", "my", "his", "her", "in", "at", "on", "from",
                         "with", "a", "bit", "much", "too"}
            if myname and not any(w in _not_name for w in _nwords) and \
               not re.search(r"\b(anmol tyagi is a cyber)", myname.lower()):
                msg, _ = self.skills.memory_add(f"user's name is {myname}")
                return (msg, True)
        # ── About / identity questions (local, no engine needed) ──
        if re.search(r"\b(who am i|what is my name|tell me my name|who is the user|apna naam)\b", t):
            try:
                from AK.profile import answer_who_am_i
                return (answer_who_am_i(), True)
            except Exception:
                pass
            name = next((f.split("is ", 1)[1].strip() for f in self.skills.memory.facts
                         if f.lower().startswith("user's name is")), "")
            if name:
                return (f"Of course, sir. You are {name}.", True)
            if self.skills.memory.facts:
                return ("You are " + ", ".join(self.skills.memory.facts[:3]) + ".", True)
            return ("I don't remember that yet, sir. Say: remember that my name is <your name>.", True)
        if re.search(r"\b(who are you|who r u|who is u|what are you|who (?:made|makes|created|creates|built|builds|developed|develops|wrote) you|who made ak|your (?:creator|maker|developer)|who is your master|kaun ho|kaun hai|tu kaun|tum kaun|aap kaun|ak kaun|introduce yourself|apna (?:parichay|introduction))\b", t):
            return ("Main AK hoon — aapka personal AI assistant, isi PC pe "
                    "locally chalta hun. Painting, visualizer, coding, cybersecurity, "
                    "automation, files aur apps — sab main sambhalta hun. Bolo boss, "
                    "kya karna hai?", True)
        if re.search(r"\b(who is anmol|who is anmol tyagi|about anmol|anmol tyagi kaun)\b", t):
            from AK.profile import answer_who_is
            return (answer_who_is(), True)

        if re.search(r"\b(what do you know about me|my memory|show memory|show what you know)\b", t):
            msg, _ = self.skills.memory_list()
            return (msg, True)
        if re.search(r"\b(clear memory|forget everything|reset memory)\b", t):
            msg, _ = self.skills.memory_clear()
            return (msg, True)

        # Reminders
        m = re.search(
            r"remind\s+me\s+(?:to\s+|that\s+)?(.+?)\s+in\s+(\d+)\s*(sec|secs?|second|seconds?|min|mins?|minute|minutes?|hour|hours?)",
            t)
        if m:
            task, amount, unit = m.group(1), int(m.group(2)), m.group(3)
            secs = amount * {"sec": 1, "secs": 1, "second": 1, "seconds": 1,
                             "min": 60, "mins": 60, "minute": 60, "minutes": 60,
                             "hour": 3600, "hours": 3600}.get(unit, 60)
            msg, _ = self.skills.reminder_add(task, secs)
            return (msg, True)
        m = re.search(
            r"remind\s+me\s+in\s+(\d+)\s*(sec|secs?|second|seconds?|min|mins?|minute|minutes?|hour|hours?)\s+(?:to\s+|that\s+)?(.+?)$",
            t)
        if m:
            amount, unit, task = int(m.group(1)), m.group(2), m.group(3)
            secs = amount * {"sec": 1, "secs": 1, "second": 1, "seconds": 1,
                             "min": 60, "mins": 60, "minute": 60, "minutes": 60,
                             "hour": 3600, "hours": 3600}.get(unit, 60)
            msg, _ = self.skills.reminder_add(task, secs)
            return (msg, True)
        if re.search(r"\b(reminders?|pending\s+reminders?|upcoming)\b", t):
            msg, _ = self.skills.reminder_status()
            return (msg, True)

        # ── Custom rules list (always accessible) ──
        if re.search(r"\b(my\s+)?custom\s*(commands?|rules?)\s+(list|show|all)", t):
            msg, _ = self.skills.custom_rule_list()
            return (msg, True)

        # ─────────────────── CLIPBOARD ───────────────────────────
        m = re.search(r"\b(?:set|copy)\s+clipboard\s+(?:to\s+)?(.+?)$", t)
        if m:
            msg, _ = self.skills.clipboard_set(m.group(1).strip())
            return (msg, True)
        if re.search(r"\b(what.*clipboard|show\s+clipboard|clipboard\s+content|paste\s+clipboard|read\s+clipboard)\b", t):
            msg, _ = self.skills.clipboard_get()
            return (msg, True)
        if re.search(r"\b(clear\s+clipboard|empty\s+clipboard)\b", t):
            msg, _ = self.skills.clipboard_set("")
            return ("Clipboard cleared.", True)

        # ─────────────────── FILE OPERATIONS ─────────────────────
        m = re.search(
            r"\b(?:create|make|write|new)\s+(?:a\s+)?(?:file|doc(?:ument)?)\s+(?:called\s+|named\s+)?(.+?)(?:\s+(?:with|containing|saying|content)\s+(.+))?$",
            t)
        if m:
            name = m.group(1).strip()
            content = (m.group(2) or "").strip()
            msg, _ = self.skills.file_create(name, content)
            return (msg, True)
        m = re.search(r"\b(?:create|make|new)\s+(?:a\s+)?(?:folder|dir|directory)\s+(?:called\s+|named\s+)?(.+?)$", t)
        if m:
            names = [n.strip().strip('"') for n in re.split(r"\band\b|,", m.group(1)) if n.strip()]
            if names:
                for nm in names:
                    os.makedirs(os.path.expanduser(nm), exist_ok=True)
                return (f"Created folder{'s' if len(names) > 1 else ''} {', '.join(names)}.", True)
        m = re.search(r"\b(?:read|show|cat)\s+(?:the\s+)?(?:file|doc(?:ument)?)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.file_read(m.group(1).strip())
            return (msg, True)
        m = re.search(r"\b(?:delete|remove|erase)\s+(?:the\s+)?(?:file|doc(?:ument)?)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.file_delete(m.group(1).strip())
            return (msg, True)
        m = re.search(r"\brename\s+(?:the\s+)?(?:file|folder)?\s*(.+?)\s+to\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.file_rename(m.group(1).strip(), m.group(2).strip())
            return (msg, True)
        m = re.search(r"\b(?:move|cut)\s+(?:the\s+)?(.+?)\s+(?:to|into)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.file_move(m.group(1).strip(), m.group(2).strip())
            return (msg, True)
        # Bulk file ops (all <ext> files) — before the generic copy/move skills, so phrasings
        # like "copy all .txt files from A to B" do not collide.
        got_msg = self._local_bulk_ext(text)
        if got_msg:
            return (got_msg, True)

        m = re.search(r"\b(?:copy|duplicate)\s+(?:the\s+)?(.+?)\s+(?:to|into)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.file_copy(m.group(1).strip(), m.group(2).strip())
            return (msg, True)

        # ── Organize / Backup ────────────────────────────────────
        m = re.search(r"\b(?:organize|sort)\s+(?:files?\s+)?(?:in\s+|of\s+)?(.+?)$", t)
        if m and not re.search(r"\bsort\s+of\b", t):
            msg, _ = self.skills.organize(m.group(1).strip())
            return (msg, True)
        m = re.search(r"\bbackup\s+(?:of\s+|for\s+|folder\s+)?(.+?)$", t)
        if m:
            msg, _ = self.skills.backup(m.group(1).strip())
            return (msg, True)

        # ── Open file / folder (late to avoid false matches) ──────
        m = re.search(r"\b(?:open|launch)\s+(?:the\s+)?(?:file|folder)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.open_file(m.group(1).strip())
            return (msg, True)

        # ── Extra local automations ───────────────────────────────────
        # Multi-app open: "open notepad and calculator"
        m = re.search(r"\b(?:open|launch|start)\s+(.+\band\b.+)$", t)
        if m:
            parts = [p.strip() for p in re.split(r"\band\b|,", m.group(1)) if p.strip()]
            if len(parts) > 1:
                apps = {"notepad", "calculator", "calc", "cmd", "command prompt", "powershell",
                        "explorer", "chrome", "edge", "browser", "vscode", "visual studio code",
                        "task manager", "control panel", "settings", "spotify"}
                sites = {"youtube", "google", "gmail", "github", "reddit", "twitter", "facebook",
                         "instagram", "linkedin", "amazon", "flipkart", "spotify", "netflix",
                         "hotstar", "chatgpt", "stackoverflow", "whatsapp", "telegram"}
                opened = []
                for p in parts:
                    first = p.split()[0]
                    if p in apps:
                        self.skills.launch_app(p); opened.append(p)
                        self._wf_step(f"Open app: {p}", kind="app", detail=p)
                    elif first in apps:
                        self.skills.launch_app(first); opened.append(first)
                        self._wf_step(f"Open app: {first}", kind="app", detail=first)
                    else:
                        site = first if first in sites else (p if "." in p else first)
                        self.skills.open_site(site)
                        self._wf_step(f"Open site: {site}", kind="url", detail=site)
                        opened.append(p)
                return (f"Opening {', '.join(opened)}.", True)

        # Extract archive (.zip)
        m = re.search(r"\b(?:extract|unzip)\s+(.+?\.zip)\s*(?:to|into)\s*(.*?)$", t)
        if m:
            arc = os.path.expanduser(m.group(1).strip().strip('"'))
            dst = os.path.expanduser(m.group(2).strip().strip('"')) or arc[:-4]
            if not os.path.isfile(arc):
                return (f"File not found: {arc}", True)
            try:
                with zipfile.ZipFile(arc) as zf:
                    zf.extractall(dst)
                return (f"Extracted {os.path.basename(arc)} into {dst}.", True)
            except Exception as exc:
                return (f"Could not extract the zip: {exc}", True)

        # Extract archive (.tar.gz / .tgz)
        m = re.search(r"\b(?:extract|untar)\s+(.+?\.(?:tar\.gz|tgz))\s*(?:to|into)\s*(.*?)$", t)
        if m:
            arc = os.path.expanduser(m.group(1).strip().strip('"'))
            dst = os.path.expanduser(m.group(2).strip().strip('"')) or os.path.splitext(arc)[0]
            if not os.path.isfile(arc):
                return (f"File not found: {arc}", True)
            try:
                with tarfile.open(arc) as tf:
                    tf.extractall(dst)
                return (f"Extracted {os.path.basename(arc)} into {dst}.", True)
            except Exception as exc:
                return (f"Could not extract the archive: {exc}", True)

        # Zip / compress
        m = re.search(r"\b(?:zip|compress)\s+(.+?)\s+(?:into|to)\s+(.+?\.zip)$", t)
        if m:
            src = os.path.expanduser(m.group(1).strip().strip('"'))
            dst = os.path.expanduser(m.group(2).strip().strip('"'))
            if not os.path.exists(src):
                return (f"Not found: {src}", True)
            base = dst[:-4] if dst.lower().endswith(".zip") else dst
            try:
                if os.path.isdir(src):
                    shutil.make_archive(base, "zip", root_dir=src)
                else:
                    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(src, os.path.basename(src))
                return (f"Zipped {src} into {dst}.", True)
            except Exception as exc:
                return (f"Could not zip: {exc}", True)

        # Kill / close a process
        m = re.search(r"\b(?:kill|close|quit|end[-\s]?task)\b\s+(?:the\s+)?(?:app|application|program|process|window)?\s*(.{1,40})$", t)
        if m and len(m.group(1).split()) <= 4 and not any(
                w in m.group(1) for w in ("door", "light", "call", "window")):
            target = re.sub(r"^the\s+", "", m.group(1)).strip()
            try:
                r = subprocess.run(["taskkill", "/f", "/im", f"{target}.exe"],
                                   capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    return (f"Closed {target}.", True)
                out = self._ps(f"Get-Process | Where-Object {{ $_.Name -like '*{target}*' }} | Stop-Process -Force -ErrorAction SilentlyContinue; 'ok'")
                return (f"Closed {target}.", True)
            except Exception as exc:
                return (f"Could not close {target}: {exc}", True)

        # Mute / unmute
        m = re.search(r"\b(mute|unmute)\b(?:\s+the)?(?:\s*(?:volume|sound|audio|speaker))?\s*$", t)
        if m:
            msg, _ = self.skills.volume(m.group(1))
            return (msg, True)

        # My IP address
        if re.search(r"\b(my\s+)?(ip|ip address|network ip)\b", t) and len(t.split()) <= 6:
            ip = self._ps("$ip = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' -and $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } | Sort-Object InterfaceMetric | Select-Object -First 1; $ip.IPAddress")
            return ((f"Your IP address is {ip}." if ip and "error" not in ip.lower() else "Could not get your IP address."), True)

        # WiFi password
        if re.search(r"\b(wifi|wi-fi|wlan)\b.*\b(password|pass|key)\b", t):
            ssid = self._ps("(netsh wlan show interfaces | Select-String 'SSID' | Select-Object -First 1) -replace '.*:\\s*',''").strip()
            if not ssid or "error" in ssid.lower():
                return ("No active WiFi connection found.", True)
            outp = self._ps(f"netsh wlan show profile name=\"{ssid}\" key=clear")
            keyline = ""
            for line in outp.splitlines():
                line = line.strip()
                if line.lower().startswith(("key content", "password")) and ":" in line:
                    keyline = line.split(":", 1)[1].strip()
                    break
            if keyline:
                return (f"Your WiFi '{ssid}' password is {keyline}.", True)
            return (f"Connected to '{ssid}' but the password is hidden.", True)

        # Lock PC
        if re.search(r"\block\s+(?:the\s+)?(?:pc|computer|laptop|system|screen)?\s*$", t):
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return ("Locking your PC.", True)

        # Shutdown / restart (confirmed — can interrupt work)
        m = re.search(r"(?:shut\s*down|shutdown|restart|reboot)\s+(?:the\s+)?(?:pc|computer|laptop|system)?\s*$", t)
        if m:
            if not self._confirm("Sir, PC band karun? Haan ya nahi?"):
                return ("OK, PC chhod raha hoon.", True)
            is_restart = bool(re.search(r"(?:restart|reboot)", t))
            subprocess.Popen("shutdown /r /t 5" if is_restart else "shutdown /s /t 5", shell=True)
            return ("Restarting in 5 seconds. Milte hain!" if is_restart
                    else "Shutting down in 5 seconds. Goodbye!", True)

        # Running processes
        if re.search(r"\b(?:show|list)\s+(?:the\s+)?(?:running\s+)?(?:processes|apps|programs)\b", t) and len(t.split()) <= 8:
            out = self._ps("Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 -Property Name | ForEach-Object { $_.Name }")
            names = " ".join(out.split()[:8]) if out else "none"
            return (f"Top running processes: {names}.", True)

        # Download from a URL
        m = re.search(r"\bdownload\s+(https?://\S+)", t)
        if m:
            url = m.group(1).strip()
            try:
                fname = os.path.basename(urllib.parse.urlparse(url).path) or "download"
                dest = os.path.join(os.path.expanduser("~/Downloads"), fname)
                urllib.request.urlretrieve(url, dest)
                return (f"Downloaded {fname} to Downloads.", True)
            except Exception as exc:
                return (f"Download failed: {exc}", True)

        # Empty recycle bin
        if re.search(r"\b(empty|clear)\s+(?:the\s+)?(?:recycle\s*bin|trash)\b", t):
            self._ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
            return ("Recycle bin emptied.", True)

        # Clean temp files
        if re.search(r"\b(clean|clear)\s+(?:the\s+)?(?:temp|temporary)\s+(?:files?\s+)?\b", t) or \
           re.search(r"\b(clean|clear)\s+temp\b", t):
            if not self._confirm("Sir, temp files saaf karun? Haan ya nahi?"):
                return ("Cleaning cancelled.", True)
            n = 0
            for base in ("TEMP", "TMP"):
                p = os.environ.get(base, "")
                if os.path.isdir(p):
                    cutoff = datetime.datetime.now() - datetime.timedelta(days=1)
                    for root, _d, fnames in os.walk(p):
                        for fn in fnames:
                            try:
                                fp = os.path.join(root, fn)
                                if datetime.datetime.fromtimestamp(os.path.getmtime(fp)) < cutoff:
                                    os.remove(fp)
                                    n += 1
                            except OSError:
                                pass
            return (f"Cleaned {n} old temp file(s).", True)

        # ── Open sites / apps ──
        m = re.search(r"\b(?:open|go to|launch|start)\b\s+(.+?)(?:\s+in\s+chrome|\s+on\s+chrome|\s+using\s+\w+)?\s*$", t)
        if m:
            target = m.group(1).strip().lower()
            sites = {"youtube","google","gmail","github","reddit","twitter","facebook",
                     "instagram","linkedin","amazon","flipkart","spotify","netflix",
                     "hotstar","chatgpt","stackoverflow","whatsapp","telegram","engine"}
            apps = {"notepad","calculator","calc","cmd","command prompt","powershell",
                    "explorer","chrome","edge","browser","vscode","visual studio code",
                    "task manager","control panel","settings","spotify"}
            first = target.split()[0] if target else ""
            if first in sites:
                self.skills.open_site(first)
                self._wf_step(f"Open site: {first}", kind="url", detail=first)
                return (f"Opening {first} in Chrome.", True)
            if target in apps or first in apps:
                app = target if target in apps else first
                self.skills.launch_app(app)
                self._wf_step(f"Open app: {app}", kind="app", detail=app)
                return (f"Opening {target}.", True)
            if any(c in first for c in (".com",".org",".net",".io",".ai")) or "." in target:
                site = first if "." in first else target
                self.skills.open_site(site)
                self._wf_step(f"Open site: {site}", kind="url", detail=site)
                return (f"Opening {target} in Chrome.", True)
            # Nothing recognisable to open (e.g. "open this website, thank
            # you for your portfolio"): ask for a concrete name/URL instead
            # of guessing or silently falling through to smalltalk.
            if re.search(r"\b(website|web\s?site|web\s?page|link|portal|url)\b", target):
                return ("Kaunsi website kholni hai, Boss? Naam ya URL bata do — "
                        "jaise 'open youtube' ya 'open example.com'.", True)

        # Play music / media (defaults to YouTube; also Spotify / Gaana)
        m = re.search(r"\b(?:play|listen to|search)\s+(.+?)\s+(?:on\s+|in\s+)?(youtube|yt|spotify|gaana)\s*$", t)
        if m:
            song = _clean_song_query(m.group(1))
            eng = m.group(2).lower()
            if song:
                direct = self._open_music(song, eng)
                return (self._music_msg(song, eng, direct), True)
        m = re.search(r"\b(?:play|listen to)\s+(.+?)$", t)
        if m:
            song = _clean_song_query(m.group(1))
            if song and not any(w in t for w in ("code", "game ", "match", "record", "video call")):
                direct = self._open_music(song, "youtube")
                return (self._music_msg(song, "youtube", direct), True)

        # Web search
        m = re.search(r"(?:google|look up)\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.web_search(m.group(1).strip())
            return (msg, True)

        # Volume
        m = re.search(r"\bvolume\s+(.+?)$", t)
        if m:
            level = m.group(1).strip()
            # spoken form is "50 percent", the skill wants "50%"
            level = re.sub(r"^(\d{1,3})\s*(?:percent|per\s*cent|%|pct).*$",
                           r"\1%", level)
            # a bare number is a percentage too: "volume 30"
            if re.fullmatch(r"\d{1,3}", level):
                level += "%"
            msg, _ = self.skills.volume(level)
            return (msg, True)

        # Screenshot
        if re.search(r"\bscreenshot\b", t) or re.search(r"\btake\b.*(?:screenshot|snap|picture)", t):
            msg, _ = self.skills.screenshot()
            return (msg, True)

        # Note
        m = re.search(r"\b(note|write this down)\s+(?!add\b|list\b|search\b|tag\b|remove\b|(?:an\s+|my\s+)?improvement\b)(.+?)$", t)
        if m:
            msg, _ = self.skills.note(m.group(2).strip())
            return (msg, True)

        # ── Local task / todo ───────────────────────────────────────
        m = re.search(r"(todo|task)\s+(add|new|create)\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.task_add(m.group(3).strip())
            return (reply, True)
        m = re.search(r"(todo|task|tasks)\s+(list|show|all|pending)", t)
        if m:
            reply, _ = self.skills.task_list()
            return (reply, True)
        m = re.search(r"(todo|task)\s+(done|complete|finish)\s+(\d+)", t)
        if m:
            reply, _ = self.skills.task_done(int(m.group(3)))
            return (reply, True)
        m = re.search(r"(todo|task)\s+(remove|delete|rm)\s+(\d+)", t)
        if m:
            reply, _ = self.skills.task_remove(int(m.group(3)))
            return (reply, True)

        # ── Local calculator ────────────────────────────────────────
        m = re.search(r"what\s+is\s+(?:the\s+)?(md5|sha1|sha256|sha512)\s+hash\s+of\s+(.+?)\??$", t)
        if m:
            reply, _ = self.skills.hash_text(m.group(2).strip().strip("."), m.group(1).lower())
            return (reply, True)
        m = re.search(r"\b(calculate|calc|compute|evaluate)\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.calculate(m.group(2).strip())
            return (reply, True)
        m = re.search(r"what\s+is\s+([\d\s\+\-\*\/\(\)\^\%\.\,\w]+)\s*$", t)
        if m and re.search(r"\d", m.group(1)):
            expr = m.group(1).strip().replace("^", "**")
            reply, _ = self.skills.calculate(expr)
            return (reply, True)

        # ── Enhanced notes with tags ──────────────────────────────────
        m = re.search(r"\bnote\s+add\s+(.+?)(?:\s+tag\s+(.+?))?$", t)
        if m:
            text = m.group(1).strip()
            tags = [t.strip() for t in m.group(2).split(",")] if m.group(2) else None
            reply, _ = self.skills.note_add(text, tags)
            return (reply, True)
        m = re.search(r"(notes|my notes)\s+(list|show|all)", t)
        if m:
            reply, _ = self.skills.note_list()
            return (reply, True)
        m = re.search(r"(notes|my notes)\s+search\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.note_search(m.group(2).strip())
            return (reply, True)
        m = re.search(r"(notes|my notes)\s+tag\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.note_list(m.group(2).strip())
            return (reply, True)
        m = re.search(r"\bnote\s+remove\s+(\d+)", t)
        if m:
            reply, _ = self.skills.note_remove(int(m.group(1)))
            return (reply, True)

        # ── Network info ─────────────────────────────────────────────
        if re.search(r"\bmy\s+(?:ip|network|internet|wifi|connection)\b", t) or \
           re.search(r"\b(?:ip|network|internet|wifi|connection)\s+(?:info|status|details|address)\b", t) or \
           re.search(r"\b(?:what\s+is|check|show)\s+(?:my\s+)?(?:ip|network|internet|wifi)\b", t):
            reply, _ = self.skills.network_info()
            return (reply, True)
        m = re.search(r"(ping|check)\s+(.+?)(\.com|\.org|\.net|\.io|\.ai|\.in)", t)
        if m:
            host = m.group(2).strip() + m.group(3).strip()
            reply, _ = self.skills.ping_host(host)
            return (reply, True)

        # ── Process manager ──────────────────────────────────────────
        if re.search(r"\b(list|show)\s+(processes|tasks|programs)", t) or \
           re.search(r"\b(task\s+manager|running\s+processes)\b", t):
            sort_by = "memory" if "memory" in t or "ram" in t else "cpu"
            reply, _ = self.skills.list_processes(sort_by)
            return (reply, True)
        m = re.search(r"\b(kill|stop|end|terminate)\s+(process|task|program)\s+(.+?)$", t)
        if m:
            reply, _ = self.skills.kill_process(m.group(3).strip())
            return (reply, True)

        # ── Pomodoro timer ───────────────────────────────────────────
        if re.search(r"\b(pomodoro|focus timer|start timer)\b", t) and \
           not re.search(r"(stop|cancel|end|status)", t):
            m = re.search(r"(\d+)\s*(min|minute|minutes)", t)
            mins = int(m.group(1)) if m else 25
            reply, _ = self.skills.pomodoro_start(mins)
            return (reply, True)
        if re.search(r"(stop|cancel|end)\s+(pomodoro|timer)", t):
            reply, _ = self.skills.pomodoro_stop()
            return (reply, True)
        if re.search(r"(pomodoro|timer)\s+(status|remaining|left)", t):
            reply, _ = self.skills.pomodoro_status()
            return (reply, True)

        # ── DICTIONARY (offline-first, cached on D drive) ────────────
        m = re.search(
            r"(?:what\s+is\s+the\s+meaning\s+of|what\s+does\s+the\s+word\s+"
            r"|meaning\s+of|definition\s+of|define|word\s+meaning|"
            r"\bdictionary\b|\bdict\b)\s+(?:the\s+word\s+|a\s+word\s+|"
            r"the\s+)?([a-zA-Z][a-zA-Z' -]*)$", t)
        if m:
            word = m.group(1).strip().strip("?")
            # "dictionary meaning of resilient" matches \bdictionary\b first, so
            # the rest of the question lands inside the word. Peel those lead-ins
            # off or the lookup is done for the literal string "meaning of ...".
            word = re.sub(r"^(?:meaning|definition|matlab|arth|translate|"
                          r"bhav|sansar)\s+(?:of|for|ka|ki|ke)\s+", "", word, flags=re.I)
            word = word.strip(" ?.'\"-")
            if not word:
                return ("Kis shabd ka matlab chahiye? Jaise: "
                        "dictionary meaning of resilient.", True)
            reply, entry = self.skills.dictionary_lookup(word)
            if entry:
                print(console_detail(entry))
            return (reply, True)
        m = re.search(r"what\s+does\s+([a-zA-Z][a-zA-Z' -]*?)\s+mean\b", t)
        if m:
            word = m.group(1).strip()
            reply, entry = self.skills.dictionary_lookup(word)
            if entry:
                print(console_detail(entry))
            return (reply, True)
        m = re.search(r"(?:synonyms?|another\s+word\s+(?:for|of))\s+(?:the\s+word\s+|a\s+word\s+)?(.+?)$", t)
        if m:
            word = re.sub(r"^of\s+", "", m.group(1).strip()).strip().strip("?")
            reply, _ = self.skills.dictionary_synonyms(word)
            return (reply, True)
        m = re.search(r"(?:antonyms?|opposite\s+(?:of|word\s+for))\s+(?:the\s+word\s+|a\s+word\s+)?(.+?)$", t)
        if m:
            word = re.sub(r"^of\s+", "", m.group(1).strip()).strip().strip("?")
            reply, _ = self.skills.dictionary_antonyms(word)
            return (reply, True)
        if re.search(r"\bword\s+of\s+(?:the\s+)?day\b", t):
            reply, entry = self.skills.dictionary_word_of_day()
            if entry:
                print(console_detail(entry))
            return (reply, True)
        if re.search(r"\brandom\s+word\b", t):
            reply, entry = self.skills.dictionary_random()
            if entry:
                print(console_detail(entry))
            return (reply, True)
        if re.search(r"(how\s+many\s+words|words\s+stored|dictionary\s+(?:status|size|count|location|path|where)|where\s+.*?dictionary)", t):
            reply, entry = self.skills.dictionary_info()
            if entry:
                print(console_detail(entry))
            return (reply, True)

        # ── CodeFest: offline coding dataset ─────────────────────
        m = re.search(r"\b(coding dataset|code dataset|code knowledge|code library|show code)\b", t)
        if m:
            msg, detail = self.skills.code_stats()
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\b(codefest|code fest|my code)\b", t):
            msg, detail = self.skills.code_stats()
            if detail:
                print(detail)
            return (msg, True)
        m = re.search(r"(?:how do i|how to|how can i|show me|give me|find|search for)\s+(.+?)\s+in\s+(python|javascript|js|bash|shell|powershell|sql|java|c|html|css|flask|fastapi|web|backend)(?:\s*)$", t)
        if m:
            topic, lang = m.group(1).strip(), m.group(2)
            msg, detail = self.skills.code_snippet(topic, lang)
            if detail:
                print(detail)
            return (msg, True)
        m = re.search(r"\b(?:show|give|open|get)\s+(?:me\s+)?(?:the\s+)?(?:code|script|snippet|example)\s+(?:for\s+|of\s+)?(.+?)(?:\s+in\s+(python|javascript|js|bash|shell|powershell|sql|java|c|html|css|flask|web|backend))?\s*$", t)
        if m:
            topic, lang = m.group(1).strip().strip("?."), (m.group(2) or "")
            msg, detail = self.skills.code_snippet(topic, lang)
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\brandom\s+(code|snippet|example)\b", t):
            msg, detail = self.skills.code_random()
            if detail:
                print(detail)
            return (msg, True)

        # ── Self-improvement engine ──────────────────────────────
        m = re.search(r"\bnote\s+(?:an\s+|my\s+)?improvement\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.learning_improvement(m.group(1).strip())
            return (msg, True)
        if re.search(r"\blearning\s+(stats|database|status)\b", t):
            msg, detail = self.skills.learning_stats()
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\b(self improve|improve yourself|self improvement|self review|what.*(?:learned|learning)|learning|did you learn|improvement(?:s)?)\b", t):
            msg, detail = self.skills.learning_review()
            if detail:
                print(detail)
            return (msg, True)

        # ── Dataset registry ─────────────────────────────────────
        if re.search(r"\b(show|list)\s+(the\s+)?dataset\s+catalog\b", t) or \
           re.search(r"\bdatasets?\s+catalog\b", t) or \
           re.search(r"\bshow\s+catalog\b", t):
            msg, detail = self.skills.datasets_catalog()
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\b(list|show)\s+(my\s+)?datasets?\b", t) or \
           re.search(r"\bregistered\s+datasets?\b", t):
            msg, detail = self.skills.datasets_list()
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\b(dataset|catalog)\s+info\s+(.+?)$", t) or \
           re.search(r"\binfo\s+(?:about\s+)?(?:the\s+)?dataset\s+(.+?)$", t):
            name = re.search(r"(?:dataset|catalog)\s+info\s+(.+?)$", t) or \
                   re.search(r"\binfo\s+(?:about\s+)?(?:the\s+)?dataset\s+(.+?)$", t)
            msg, detail = self.skills.datasets_info(name.group(1).strip())
            if detail:
                print(detail)
            return (msg, True)
        m = re.search(r"\b(add|download|install)\s+dataset\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.datasets_add(m.group(2).strip())
            return (msg, True)
        m = re.search(r"\b(register)\s+dataset\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.datasets_register(m.group(2).strip())
            return (msg, True)
        m = re.search(r"\b(remove|delete|unregister)\s+dataset\s+(.+?)$", t)
        if m:
            msg, _ = self.skills.datasets_remove(m.group(2).strip())
            return (msg, True)
        m = re.search(r"\bsearch\s+(?:my\s+)?(?:local\s+)?datasets?\s+(?:for\s+)?(.+?)$", t)
        if m:
            msg, detail = self.skills.datasets_search(m.group(1).strip())
            if detail and "Dataset search:" in detail:
                print(detail)
            return (msg, True)
        if re.search(r"\b(dataset|catalog)\s+(stats|storage|size)\b", t):
            msg, detail = self.skills.datasets_stats()
            if detail:
                print(detail)
            return (msg, True)
        m = re.search(r"\b(analyze|preview|inspect|peek|look\s+(?:at|inside))\s+(?:the\s+)?(?:dataset\s+|into\s+)?(.+?)$", t)
        if m:
            msg, detail = self.skills.datasets_analyze(m.group(2).strip())
            if detail:
                print(detail)
            return (msg, True)
        m = re.search(r"\b(show|give|print)\s+(?:me\s+)?(?:a\s+)?(?:sample|preview|peek|head|first\s+rows)\s+(?:of|from|in)?\s+(?:the\s+)?(?:dataset\s+)?(.+?)$", t)
        if m:
            msg, detail = self.skills.datasets_analyze(m.group(2).strip())
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\bdownload\s+(?:all|everything)\s+(?:datasets?)?\b", t) or \
           re.search(r"\b(datasets?)\s+download\b", t):
            msg, detail = self.skills.datasets_download_all()
            if detail:
                print(detail)
            return (msg, True)

        # ── Website builder (full-stack, offline) ────────────────
        m = re.search(r"\b(build|create|make|generate)\s+(?:me\s+)?(?:a\s+)?(?:complete\s+|full\s+|full-stack\s+|fullstack\s+)?website\s*(?:for\s+|about\s+|called\s+|named\s+)?(.+?)$", t)
        if m and "website" in t:
            desc = m.group(2).strip() if m.group(2) else m.group(1).strip()
            if not desc or len(desc.split()) < 2:
                return ("Say something like: build me a website for a restaurant, or build a portfolio website for a photographer.", True)
            msg, detail = self.skills.build_website(desc)
            if detail:
                print(detail)
            return (msg, True)
        if re.search(r"\b(my\s+)?websites?\b", t) and re.search(r"(list|show|built|previous|old|all)", t):
            msg, detail = self.skills.list_websites()
            if detail:
                print(detail)
            return (msg, True)

        # ── Engine — task delegation (route via None, so respond() runs it
        #    through _route_to_engine → correct engine label + auto-learn) ──
        m = re.search(r"\b(engine|open\.code|code agent|ai agent)\b\s+(.+?)$", t)
        if m:
            return (None, True)
        m = re.search(r"\b(write|generate|create|build|make)\s+"
                      r"(?:(?:me|us)\s+)?(?:a\s+|an\s+)?"
                      r"(?:(?:python|java|javascript|js|typescript|ts|c\+\+|cpp|c|"
                      r"html|css|sql|bash|shell|powershell|node|go|rust|ruby|php|"
                      r"swift|kotlin|react|flask|django|fastapi)\s+)?"
                      r"(?:script|code|program|programme|function|app|website|"
                      r"web\s?page|html|page|class|module|api|snippet|example|"
                      r"test|tests|component|microservice)\b(?:\s+(.+))?$", t)
        if m:
            return (None, True)
        m = re.search(r"\bexplain\s+(?:the\s+)?(?:code|this|script|file)\b\s*(.+?)$", t)
        if m:
            return (None, True)

        return None

    def respond(self, text: str, can_ask: bool = False) -> str | None:
        """Automatic reply, LOCAL-FIRST:
          0. A learned automation matches → replay it instantly.
          1. Any built-in local skill → answer locally.
          2. Otherwise, if enabled, ASK the user: 'implement locally or use engine?'
          3. Local if possible; otherwise engine; local brain as last resort.
          New tasks are auto-learned as reusable automations."""
        self._viz_state("thinking")
        # 0. Repair the transcript before anything else looks at it. Whisper
        #    returns sound, not spelling, so "Radhe Radhe" arrives as
        #    "Radera de" and matches no greeting rule at all - it then falls
        #    through every local handler and ships to engine. Normalise first
        #    so the rest of the pipeline sees what was actually said.
        if text:
            _heard = text
            text = stt_fix.correct(text)
            # Music second: "play arijit singh song" arrives as "adjit sing
            # songs" (artist wrong, "play" dropped). Needs music intent, so it
            # runs after the plain spelling rules above.
            text = stt_fix.repair_music(text)
            if stt_fix.changed(_heard, text):
                print(f"[stt-fix] '{_heard.strip()}' -> '{text.strip()}'")

        # 0. Pure abuse → AK never repeats the swear, just gently declines.
        if _is_abusive(text):
            polite = ("Aapko gussa lag raha hai. Main hamesha aapke kaam ki "
                      "madad ke liye yahan hoon — shanti se bataiye, sab ho "
                      "jaayega.")
            self._say(polite)
            self._viz_state("idle")
            return polite
        # 0a. "What can you do" / "kya kar sakte ho" / "your skills" is a
        #     capability question, NOT a task and never a learned workflow.
        #     Print the full list in the terminal, speak a short summary.
        if self._is_capability_question(text):
            return self._capabilities_reply()
        # 0a2. Drawing lanes, in strict priority — the user's wording picks one:
        #       * art word present ("sunset painting banao", "logo banao")
        #         -> PAINT: a real PNG from AK/paintgen.py, opened in MS Paint.
        #       * a build word + build verb ("snake game banao", "website banao")
        #         -> full engine build, asked about details first.
        #       * otherwise a shape the visualizer knows ("kutta banao",
        #         "pizza dikhao", "krishna bana") -> SHAPE: stardust stamp.
        if _PAINT_ART_RE.search(text or ""):
            paint = self._handle_paint_cmd(text)
            if paint:
                return paint
        bkind = self._detect_build_request(text)
        if bkind:
            self._viz_custom_k = None
            return self._handle_build_request(text, bkind)
        sw = self._detect_shape_request(text)
        if sw:
            self._viz_custom(sw)
            show = sw.replace("icecream", "ice cream").replace("hotdog", "hot dog")
            self._say(f"{show} bana diya — screen pe dekho!")
            return f"{show} bana diya."
        self._viz_custom_k = None
        # 0b. Voice command to switch chat brain between local (Ollama) and
        #     fast cloud API (Groq). Say "api mode" / "switch to api" /
        #     "local mode" while listening and it flips the backend live.
        t = (text or "").strip().lower()
        # 0b-1. "ollama band karo" / "local model off" — stop it and go back
        #       to the instant path. Ollama only ever runs when asked for.
        if re.search(r"\b(ollama|local model|local brain|khud ka model)\b", t) and \
                re.search(r"\b(band|band karo|stop|off|rok do|shut down)\b", t):
            note = self._stop_ollama()
            self._say(note)
            return note
        # 0b-2. Switch the coding/task engine mid-session ("engine local karo",
        #       "use fast engine", "coding engine engine"). Kept distinct from
        #       the chat-brain "local mode" switch that follows.
        if re.search(r"\b(engine|agent)\b", t) and re.search(
                r"\b(switch|change|use|lagao|chalao|chalu|set|karo|badlo|mode|on)\b", t):
            if any(w in t for w in ("local", "lokal", "offline", "apna", "desi", "ollama")):
                note = self._switch_task_backend("local")
                self._say(note)
                self._viz_state("idle")
                return note
            if any(w in t for w in ("engine", "engine", "cloud", "fast",
                                    "tez", "online", "internet")):
                note = self._switch_task_backend("engine")
                self._say(note)
                self._viz_state("idle")
                return note
        target = None
        if 0 < len(t) < 60:
            tl = t.rstrip("?!. .,").strip()
            head = tl.split()[0] if tl.split() else ""
            switches = head.startswith(("switch", "change", "go", "put", "set", "wapis", "mode"))
            if switches or ("mode" in tl and len(tl.split()) <= 4):
                if any(w in tl for w in ("api", "groq", "cloud", "online", "internet")):
                    target = "groq"
                elif any(w in tl for w in ("local", "ollama", "offline", "khud", "model", "desi")):
                    target = "ollama"
        if target:
            self._viz_state("processing")
            if target == "ollama" and not self._ensure_ollama_running():
                # Ollama no longer autostarts, so it has to be launched on
                # demand. If that fails, stay on the fast path and say so.
                self.brain.set_backend("rule")
                note = ("Ollama start nahi hua. Is CPU pe woh 7 se 30 second "
                        "leti hai, isliye main fast mode mein hi hoon. Bolo "
                        "'api mode' ya koi bhi kaam karwa do.")
            else:
                note = self.brain.set_backend(target)
            self._say(note)
            self._viz_state("idle")
            return note
        # 0-WF. Learned workflow (screen-recorded playbook) → AK replays the
        #       very same steps on its own. This sits ABOVE the old automation
        #       replay because a workflow carries the real recorded steps.
        # 0-EM. A composed email waiting for a "haan" / "nahi".
        _mail = getattr(self, "_email_pending", None)
        if _mail:
            if re.search(r"\b(haan|ha|yes|bhej|bhej\s*do|kar\s*do|karo|ok|okay|"
                         r"sure|send)\b", (text or "").lower()):
                self._email_pending = None
                self._say("Boss, email bhej raha hun...")
                ok, msg = self.mailer.send(
                    _mail.get("to"), _mail.get("subject"), _mail.get("body"))
                self._log_action("email", text, msg)
                self._viz_state("idle")
                return msg
            if re.search(r"\b(nahi|na|no|cancel|mat|band)\b", (text or "").lower()):
                self._email_pending = None
                self._say("Theek hai boss, email cancel kar diya.")
                return "Email cancel kar diya."
        pending = getattr(self, "_wf_pending", None)
        if pending and re.search(
                r"\b(haan|ha|yes|kar\s*do|karo|chalo|bilkul|ok|okay|sure)\b",
                (text or "").lower()):
            self._wf_pending = None
            self._say("Sir, wahi seekhe hue steps follow kar raha hun.")
            report = self._wf_run(pending)
            failed = report.get("failed") or []
            reply = (f"Sir, {pending.get('title')} ho gaya." if report.get("ok")
                     else f"Sir, {len(failed)} step fail hue.")
            self._log_action("workflow", text, reply)
            return reply
        if self._is_task_request(text) and not self._is_questionish(text):
            wf = self._wf_match(text)
            if wf:
                if (self.cfg.get("workflow", {}) or {}).get(
                        "confirm_before_replay", False):
                    self._wf_pending = wf
                    q = (f"Sir, yeh kaam maine pehle seekha hai "
                         f"({wf.get('title')}). Wahi steps follow karun? "
                         f"'haan' bol do.")
                    self._say(q)
                    return q
                print(f"[engine: workflow ({wf.get('name')})]")
                self._say("Sir, yeh kaam maine pehle seekha hai. Wahi steps "
                          "follow kar raha hun.")
                report = self._wf_run(wf)
                ran = len(report.get("ran") or [])
                failed = report.get("failed") or []
                if report.get("ok"):
                    reply = (f"Sir, seekhe hue steps follow karke "
                             f"{wf.get('title')} ho gaya ({ran} steps).")
                else:
                    reply = (f"Sir, {len(failed)} step fail hue: "
                             f"{failed[0].get('title') if failed else 'unknown'}. "
                             f"Baaki steps chal gaye.")
                self._log_action("workflow", text, reply)
                self.learning.record_usage("workflow")
                return reply

        # 0. Learned automations replay instantly — no questions, no waits.
        #    Auto-learn new self-facts from casual chat (offline, silent).
        #    LOCAL-FIRST: every learned recipe tries the local implementation
        #    first; engine only as fallback.
        auto = self.automations.find(text)
        if auto:
            auto_task = auto.get("task") or text
            auto_eng = auto.get("engine", "AK")
            print(f"[engine: automation ({'AK' if auto_eng == 'engine' else auto_eng})]")
            reply = self._try_local(auto_task)
            if not (reply and self._is_success(reply)):
                reply = self._local_task(auto_task)
            if reply and self._is_success(reply):
                self._log_action("automation", text, reply)
                self.learning.record_usage("automation")
                return reply
            print("[automation] local replay failed, switching to AK fast engine")
            reply = self._fast_task(auto_task)
            if reply:
                self._log_action("automation", text, reply)
                self.learning.record_usage("automation")
                return reply

        # ── task lane: record what AK is about to do so it can learn it ──
        self._wf_task_begin(text)
        r = self._handle_skill(text)
        if r is not None:
            reply, route = r
            used_engine = False
            if reply is None and route:
                print("[engine: AK]")
                reply = self._route_to_engine(text)
                used_engine = True
            else:
                print("[engine: local]")
            if reply:
                # Reply turant jaayega — logging/lesson/memory sab background.
                self._learn_in_background(
                    text, reply, "engine" if used_engine else "local",
                    task=(getattr(self, "_last_engine_task", "") or text),
                    announce=False)
            self._wf_task_end(text, reply or "")
            return reply

        # Daily-life baatein LOCALLY — no Ollama/Groq/engine zaroorat.
        # SmallTalk kabhi task/questions ke saamne nahi aata — sirf choti
        # rozana-ki baatein. Baaki sab niche wale engines ko.
        # A command dressed up with pleasantries ("...open this website,
        # thank you...") must NOT be hijacked by a thanks/greeting phrase:
        # only short casual lines reach smalltalk; any longer action request
        # falls through to the local skill / engine lanes below.
        _is_task = self._is_task_request(text) and len(text.split()) >= 5
        st = None if _is_task else self.smalltalk.answer(
            text, context=self.skills.memory.context_text())
        if st:
            print("[engine: smalltalk-local]")
            self._viz_state("processing")
            self._log_action("smalltalk", text, st)
            self.learning.record_usage("smalltalk")
            return st

        # Self-learn cache: if AK learned this answer before, return it
        # instantly (no model, no network, no engine) — checked BEFORE
        # wikipedia so repeated questions are answered from local memory.
        cached = self.self_learn.lookup(text)
        if cached:
            print("[engine: self-learn cache]")
            self._viz_state("processing")
            self._log_action("self-learn", text, cached)
            self.learning.record_usage("self-learn")
            return cached

        # Fast online lane: basic factual questions answered instantly from
        # Wikipedia (no model, no engine) — sits between local skills and
        # engine. Only fires on clear "look this up" questions.
        if self.cfg.get("skills", {}).get("wikipedia_auto", True):
            wiki = self._wiki_auto_answer(text)
            if wiki:
                print("[engine: wikipedia]")
                self._viz_state("processing")
                self._log_action("wikipedia", text, wiki)
                self.learning.record_usage("wikipedia")
                self.learning.record_lesson(text, "wikipedia", wiki)
                self.self_learn.cache_answer(text, wiki, "wikipedia", 0.85)
                return wiki

        # No local skill matched → hand off to the chosen task engine.
        #  * Fast engine chosen this session → never ask and never touch the
        #    local engine; go straight to the fast path.
        #  * Otherwise ask once, then fall back to the fast engine if local
        #    cannot do it.
        if (can_ask and self.ask_before_engine and self._is_task_request(text)
                and self._task_local_allowed):
            choice = self._ask_local_or_engine()
            if choice == "exit":
                self._say("Thik hai, sir. Chhoda.")
                return None
            if choice == "local":
                reply = self._try_local(text) or self._local_task(text)
                if reply and self._is_success(reply):
                    print("[engine: local (user-requested)]")
                    self._log_action("local", text, reply)
                    self._learn_local(text, reply)
                    self._wf_task_end(text, reply)
                    return reply
                # Local engine could not do it → escalate to the fast engine.
                self._say("Sir, local engine se ye poora nahi bana — AK fast "
                          "engine se kar deta hoon.")
                fast = self._fast_task(text)
                if fast:
                    print("[engine: AK]")
                    self._log_action("engine", text, fast)
                    self.learning.log_unknown(text)
                    self.learning.record_lesson(text, "engine", fast)
                    self._wf_task_end(text, fast)
                    return fast
                reply = reply or "Sir, ye kaam abhi poora nahi ho paya."
                self._wf_task_end(text, reply)
                return reply

        print("[engine: AK]")
        reply = self._route_to_engine(text)
        if reply:
            # Kaam PEHLE ho chuka (engine ne kar diya) aur reply seedha
            # user tak jaata hai. Poora learning/memory ka kaam background
            # thread me: cache, lessons, automation aur log file — isliye
            # answer par koi wait nahi lagta.
            self._learn_in_background(
                text, reply, "engine",
                task=(getattr(self, "_last_engine_task", "") or text),
                announce=True)
            # Auto-learn new facts about the user from casual chat (offline,
            # instant regex + memory add) — kept in-line because it can
            # extend the reply itself.
            if not self._is_task_request(text):
                note = self._auto_learn_from_chat(text)
                if note and len(reply) < 160:
                    reply = f"{reply} {note}"
        self._wf_task_end(text, reply or "")
        return reply

    # ── Ask-before-external + local implementation ──────────────────────────





























    # ── Email composer / sender ──────────────────────────────────
    _EMAIL_INTENT = re.compile(
        r"\b(send|bhej\w*|compose|draft|likh\w*)\b.*\b(email|mail|e-mail)\b|"
        r"\b(email|mail|e-mail)\b.*\b(send|bhej\w*|compose|draft|likh\w*)\b|"
        r"\b(email|mail)\s+(status|setup|configured|config|ready)\b|"
        r"\b(email|mail)\s+bhej\w*\b", re.IGNORECASE)

    _EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}")

    def _handle_email_cmd(self, text: str):
        """Compose an email locally and stage it for confirmation."""
        s = (text or "").strip()
        if not s or not self._EMAIL_INTENT.search(s):
            return None

        mailer = getattr(self, "mailer", None)
        if mailer is None:
            try:
                self.mailer = mailer = Mailer(self.cfg)
            except Exception:
                mailer = None

        # Status / configuration help.
        if re.search(r"\b(status|setup|configured|config|ready)\b", s, re.I):
            state = mailer.status() if mailer else "Email module load nahi hua."
            hint = ("Config.json me email section bharo: smtp_user aur smtp_pass "
                    "(Gmail ke liye App Password), phir 'enabled' true kar do.")
            return (f"{state} {hint}", True)

        addr = self._EMAIL_RE.search(s)
        if not addr:
            return ("Boss, kisko bhejna hai? Format: email bhejo to "
                    "name@example.com subject Report body Yeh report hai.", True)
        to = addr.group(0)

        subj_m = re.search(
            r"(?:subject|vishay|about)\s*[:\-]?\s*(.+?)(?=\b(?:body|message|"
            r"matn|text|likho)\b|$)", s, re.I)
        body_m = re.search(
            r"(?:body|message|matn|text|likho|likh\s*do|and\s+say)\s*[:\-]?\s*(.+)$",
            s, re.I | re.S)

        subject = subj_m.group(1).strip(" .:-") if subj_m else ""
        body = body_m.group(1).strip() if body_m else ""
        if not body and not subject:
            return ("Boss, email ka content bhi batao. Format: email bhejo to "
                    f"{to} subject Report body Yeh report hai.", True)
        if not body:
            body = subject
        if not subject:
            subject = "Message from AK"

        self._email_pending = {"to": to, "subject": subject, "body": body}
        self._viz_state("processing")
        preview = body if len(body) <= 160 else body[:157] + "..."
        return (f"Boss, ye email bhejun? To: {to} | Subject: {subject} | "
                f"Body: {preview} — 'haan' bolo to bhej dun, 'nahi' to cancel.", True)

    # ── Capability questions ─────────────────────────────────────
    # "what can you do", "kya kar sakte ho", "your skills" — ye AK ke
    # baare mein sawaal hain. Inpe koi task, workflow ya recording nahi.
    _CAPABILITY_RE = re.compile(
        r"\b(what\s+(can|could)\s+(you|u)\s+do|what\s+do\s+(you|u)\s+do|"
        r"what\s+are\s+your\s+(skills|capabilities|features)|"
        r"your\s+(skills|capabilities|features)|"
        r"(list|show)\s+(me\s+)?(your\s+)?(skills|capabilities|features)|"
        r"kya\s+(kya\s+)?kar\s+sak(te|ta|ti|te\s+ho|te\s+hain|ta\s+hai)|"
        r"(tum|aap)\s+(kya\s+)?(kya\s+)?kar\s+sakte|"
        r"capabilities|abilities)\b",
        re.IGNORECASE)

    def _is_capability_question(self, t: str) -> bool:
        s = (t or "").strip()
        if not s or len(s.split()) > 8:
            return False
        return bool(self._CAPABILITY_RE.search(s))

    def _capabilities_reply(self) -> str:
        """Print the full capability list, return a short spoken summary."""
        try:
            full, _ = self.skills.capabilities()
        except Exception:
            full = ""
        if full:
            print("\n" + full)
        summary = ("Boss, main locally bahut kuch kar sakta hun — time aur date, "
                   "web search, apps launch, files aur folders, notes aur tasks, "
                   "reminders, dictionary aur translate, coding aur websites, "
                   "security scans, Gita, music, email aur file drop. Poori list "
                   "terminal pe print kar di hai.")
        print(f"[engine: capabilities]")
        self._log_action("capabilities", "what can you do", summary)
        self.learning.record_usage("capabilities")
        return summary

    def _is_task_request(self, t: str) -> bool:
        """Is this a task/action request (worth asking 'local or engine')?."""
        action = (r"\b(create|make|write|delete|remove|move|copy|rename|open|launch|start|run|do|"
                  r"set up|setup|install|update|organize|sort|backup|clean|clear|empty|find|locate|"
                  r"search|download|extract|convert|merge|split|save|build|configure|set|change|"
                  r"show me|generate|karo|karo na|banao|bana do|kholo|download|fetch)\b")
        if re.search(action, t, re.IGNORECASE):
            return True
        if re.search(r"[\\/]", t):            # mentions a path → local file/folder work
            return True
        return False

    # ── question vs. real task ────────────────────────────────────────────
    # "what can you do", "how are you", "kya kar sakte ho" - ye normal
    # sawal hain, inpe koi recording/workflow seekhna nahi chahiye.
    _QUESTION_HEAD = re.compile(
        r"^\s*(what|which|who|whom|whose|when|where|why|how|can|could|would|"
        r"should|shall|is|are|was|were|am|do|does|did|have|has|had|will|may|"
        r"might|tell|explain|describe|list|show|define|suggest|help|guide|"
        r"mean|bat(a|ao|aye)|samjha(o|na)|kya|kyun|kaise|kahan|kaha|kab|kaun|"
        r"kaunsa|kitna|kiski|kis|batao|bata|bataiye|samjhao)\b")
    _QUESTION_MARK = re.compile(
        r"\b(can you|could you|would you|do you|did you|are you|is your|"
        r"what are|what is|what can|how do|how can|how much|how many|"
        r"tell me about|explain me|what's|whats|kya kar|kya kar sakte|"
        r"kaise kar|kyun kiya)\b")
    # A specific, concrete action - generic verbs (do, run, open, set) nahi.
    _RECORD_VERB = re.compile(
        r"\b(create|creates|creating|make|makes|making|write|writes|writing|"
        r"delete|deletes|deleting|remove|removes|removing|move|moves|moving|"
        r"copy|copies|copying|rename|renames|renaming|install|installs|"
        r"uninstall|uninstalls|update|updates|updating|organize|organises|"
        r"organizing|sort|sorts|backup|backups|clean|cleans|clearing|"
        r"clear|clears|empty|empties|download|downloads|extract|extracts|"
        r"convert|converts|merge|merges|split|splits|build|builds|"
        r"configure|configures|generate|generates|deploy|deploys|"
        r"upload|uploads|banao|bana do|bana de|bana dena|kholo|khol do|"
        r"likho|likh do|bhejo|bhej do|install karo|download karo|"
        r"delete karo|remove karo|band karo|khatam karo|set karo|"
        r"banana|banani|banaye|khatam karna)\b")
    # Weaker verbs - record ONLY if the object is a concrete thing.
    _RECORD_SOFT_VERB = re.compile(
        r"\b(open|opens|launch|launches|run|runs|start|starts|close|closes|"
        r"save|saves|send|sends|print|prints)\b")
    _VAGUE_OBJECT = {
        "it", "that", "this", "them", "those", "app", "application", "program",
        "something", "stuff", "thing", "things", "everything", "anything",
        "all", "same", "one", "file", "files", "folder", "ka", "kuch", "sab",
        "wahi", "isi", "yhi",
    }
    _TASK_STOP = {
        "a", "an", "the", "this", "that", "it", "my", "our", "your", "new",
        "ek", "mera", "meri", "apna", "apni", "ko", "ka", "ki", "ke", "se",
        "me", "mein", "par", "aur", "karke", "karta", "karti", "karte",
        "kar", "karna", "karo", "kar do", "do", "de", "deno", "please",
        "now", "abhi", "for", "on", "in", "to", "of", "and", "then", "us",
        "is", "ka", "wala", "wali", "wala",
    }

    def _is_questionish(self, t: str) -> bool:
        """Is this a question / chat about AK (not a concrete task)?"""
        s = (t or "").strip()
        if not s:
            return True
        if s.endswith("?"):
            return True
        if self._QUESTION_HEAD.match(s):
            return True
        if self._QUESTION_MARK.search(s.lower()):
            return True
        return False

    def _specific_task(self, t: str) -> bool:
        """A concrete task with a real action + a real object.

        'what can you do' -> False (question, no action)
        'install chrome'   -> True  (action + object)
        'make a folder'    -> True
        'open chrome'      -> True  (soft verb, concrete object)
        'run the app'      -> False (object is vague)
        'do it'            -> False (nothing to work on)
        """
        s = (t or "").strip()
        if not s or self._is_questionish(s):
            return False
        if len(s.split()) < 2:
            return False

        def _words(chunk):
            return [w for w in re.findall(r"[A-Za-z0-9_.:\\/-]+", chunk)
                    if w.lower() not in self._TASK_STOP]

        strong = self._RECORD_VERB.search(s)
        if strong:
            after = _words(s[strong.end():])
            before = _words(s[:strong.start()])
            return bool(after or before)

        soft = self._RECORD_SOFT_VERB.search(s)
        if soft:
            pool = _words(s[soft.end():]) or _words(s[:soft.start()])
            for w in pool:
                base = w.lower().strip("._\\:-/")
                if base and base not in self._VAGUE_OBJECT:
                    return True
            return False
        return False

    def _worth_announcing(self, t: str) -> bool:
        """Only announce a new 'naya seekha' for a SUBSTANTIVE learning.

        Casual chatter ('hello', 'bye', 'switch') and garbled STT lines used to
        get announced too, so AK kept blurting noisy 'naya seekha: <junk>'
        lines. A learning must be a real sentence with enough alphabetic
        content to be worth repeating."""
        s = (t or "").strip()
        words = s.split()
        if len(words) < 4:
            return False
        if sum(c.isalpha() for c in s) < 12:
            return False
        # A question about AK ("what can you do") is not a learning.
        if self._is_questionish(s):
            return False
        # Skip lines that are obviously mis-heard STT noise (very few real words).
        real = [w for w in words if len(w) >= 3 and w.isalpha()]
        return len(real) >= 3


    # ── Coding/task engine choice (local vs fast) ───────────────────────────

    def _local_engine_available(self) -> bool:
        """True if AK's own local engine code is present on this machine."""
        try:
            from AK import LocalAgent
            LocalAgent(self.cfg)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _fast_engine_available(self) -> bool:
        try:
            return TaskEngine(self.cfg).available
        except Exception:  # noqa: BLE001
            return False

    def _choose_task_backend(self) -> None:
        """On startup, let the user pick the coding/task engine.

        [1] AK Local - AK's own model, offline/private.
        [2] AK Fast  - the fast external engine (needs internet).
        The choice lasts for the session; the other engine can still be pulled
        in mid-session when it is genuinely needed.
        """
        if not bool((self.cfg.get("brain", {}) or {}).get("ask_backend_on_start", True)):
            cur = str((self.cfg.get("brain", {}) or {}).get("agent_backend", "auto")).lower()
            if cur in ("local", "engine"):
                self._set_task_backend(cur, announce=True)
            return

        has_local = self._local_engine_available()
        has_fast = self._fast_engine_available()
        if not (has_local and has_fast):
            # Only one engine on this machine -> use it, no question.
            self._set_task_backend("local" if has_local else "engine",
                                   announce=False)
            return

        print("  [1] AK Local  - apna model, offline aur private")
        print("  [2] AK Fast   - tez jawab, internet chahiye")
        self._say("Boss, coding aur kaam ke liye kaunsa engine chalu karun? "
                  "One boliye AK Local ke liye, ya two AK Fast ke liye.")
        self._set_task_backend("engine" if self._read_engine_choice() == "2"
                               else "local")

    def _read_engine_choice(self) -> str:
        one = {"1", "one", "won", "wan", "van", "un", "ek", "aik", "eyk",
               "ik", "yek", "rek", "wreck", "first", "pehla", "pehle"}
        two = {"2", "two", "too", "to", "tu", "do", "du", "doo", "due",
               "second", "dusra", "dusre"}
        for _ in range(3):
            try:
                if self.typing_mode:
                    raw = input("Engine [1=Local / 2=Fast]: ").strip()
                else:
                    self._viz_state("listening")
                    raw = (self.listener.listen("One ya two, Boss?") or "").strip()
                    if not raw:
                        raw = input("Engine [1=Local / 2=Fast]: ").strip()
            except (EOFError, KeyboardInterrupt):
                return "1"
            r = re.sub(r"[^a-z0-9 ]", " ", raw.lower())
            words = set(r.split())
            # Explicit engine name wins.
            if words & {"local", "lokal", "offline", "apna"}:
                return "1"
            if words & {"fast", "cloud", "internet", "tez"} \
                    or "engine" in r or "engine" in r:
                return "2"
            # Numbers / common phonetic variants of "one" / "two".
            if words & one or "1" in r:
                return "1"
            if words & two or "2" in r:
                return "2"
        return "1"

    def _set_task_backend(self, name: str, announce: bool = True) -> None:
        name = "engine" if str(name).lower() == "engine" else "local"
        if name == "local":
            if not self._ensure_ollama_running():
                self._say("AK Local engine start nahi hua — fast engine se kaam karunga.")
                name = "engine"
        self.task_backend = name
        self._task_local_allowed = (name == "local")
        self.engine = _make_coding_agent(self.cfg, force=name)
        if announce and name == "local":
            self._say("AK Local engine ready — kaam offline hoga.")
        if os.environ.get("AK_DEBUG_ENGINE"):
            print(f"[AK] task engine = {name}")

    def _switch_task_backend(self, name: str) -> str:
        """Mid-session engine switch (voice/typed). Returns a spoken note."""
        name = "engine" if str(name).lower() == "engine" else "local"
        if name == "local":
            if not self._ensure_ollama_running():
                return "AK Local engine start nahi hua — fast engine par hi hoon."
            self.task_backend = "local"
            self._task_local_allowed = True
            self.engine = _make_coding_agent(self.cfg, force="local")
            return "Ab AK Local engine chalu hai — kaam offline hoga."
        self.task_backend = "engine"
        self._task_local_allowed = False
        self.engine = _make_coding_agent(self.cfg, force="engine")
        return "Ab fast engine chalu hai."

    def _ask_local_or_engine(self) -> str:
        """Ask the user: implement locally, use the fast engine, or cancel.
        Returns 'local' | 'engine' | 'exit'."""
        q = ("Sir, isko lokal implement karu, ya AK fast engine use karun? "
             "Boliye: lokal, fast, ya exit.")
        self._say(q)
        for _ in range(3):
            if self.typing_mode:
                try:
                    heard = input("Sir (local / fast / exit): ").strip()
                except (EOFError, KeyboardInterrupt):
                    return "exit"
            else:
                self._viz_state("listening")
                heard = self.listener.listen("Sir, lokal ya fast?")
            if not heard:
                continue
            h = heard.lower().strip()
            if h in ("local", "lokal", "ha local", "haan local", "yes local", "local kar do", "ha",
                     "haan", "yes", "lokal kar do", "pakka", "confirm"):
                return "local"
            if h in ("engine", "open", "engine", "fast", "ak fast", "no local", "nai local",
                     "no", "engine se", "nahi local", "engine kar do", "fast kar do"):
                return "engine"
            if h in ("exit", "cancel", "quit", "chhodo", "band", "stop", "nahi", "mat kar"):
                return "exit"

    def _local_task(self, text: str) -> str | None:
        """Run a task on AK's own local (Ollama) engine. None if unavailable.

        This is what actually invokes the local agent — `_try_local` is only the
        small offline (temp-cleanup) helper and never touches the model.
        """
        try:
            from AK import LocalAgent
            la = self.engine
            if not isinstance(la, LocalAgent):
                la = _make_coding_agent(self.cfg, force="local")
            if not isinstance(la, LocalAgent) or not la.available:
                return None
            return la.execute_task(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[AK:local] task error: {exc}")
            return None

    def _fast_task(self, text: str) -> str | None:
        """Run a task on the fast external engine. None if unavailable."""
        try:
            fast = _make_coding_agent(self.cfg, force="engine")
            if not getattr(fast, "available", False):
                return None
            return fast.execute_task(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[AK:fast] task error: {exc}")
            return None

    def _confirm(self, question: str) -> bool:
        """Yes/no confirmation via voice or typing."""
        self._say(question)
        for _ in range(3):
            if self.typing_mode:
                try:
                    heard = input("> ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return False
            else:
                self._viz_state("listening")
                heard = self.listener.listen("Haan ya nahi?")
            if not heard:
                continue
            h = heard.lower().strip()
            if any(w in h for w in ("haan", "yes", "ha", "yeah", "hmm", "ok", "okay", "theek",
                                    "thik", "sure", "pakka", "confirm", "kar do", "do it", "hain")):
                return True
            if any(w in h for w in ("nahi", "no", "cancel", "chhodo", "band", "mat karo", "stop",
                                    "na", "nhi")):
                return False
            self._say("Sir, haan ya nahi?")
        return False

    def _local_bulk(self, ext: str, src: str, dst: str, op: str) -> str | None:
        """Offline bulk file op: copy / move / delete all <ext> in <src>."""
        src = os.path.expandvars(os.path.expanduser(src.strip().strip('"')))
        if not os.path.isdir(src):
            return f"Folder not found: {src}"
        if not any(c in ext for c in "*?["):
            ext = "*" + ext
        files = glob.glob(os.path.join(src, ext))
        if not files:
            return f"No {ext.lstrip('*')} files found in {src}."
        if op == "delete" and not self._confirm(f"Sir, {len(files)} {ext} file delete karun? Haan ya nahi?"):
            return "Delete cancelled."
        if op in ("copy", "move") and not os.path.isdir(dst):
            os.makedirs(dst, exist_ok=True)
        if op == "copy":
            for f in files:
                shutil.copy2(f, dst)
        elif op == "move":
            for f in files:
                shutil.move(f, dst)
        elif op == "delete":
            for f in files:
                os.remove(f)
        verbs = {"copy": "Copied", "move": "Moved", "delete": "Deleted"}
        ext_display = ext.lstrip('*')
        if op == "delete":
            return f"{verbs[op]} {len(files)} {ext_display} file(s) from {src}."
        return f"{verbs[op]} {len(files)} {ext_display} file(s) to {dst}."

    def _local_bulk_ext(self, text: str) -> str | None:
        """Match bulk file-op phrasing. Returns a reply or None."""
        t = text.lower().strip()
        m = re.search(r"\b(?:copy|move)\s+all\s+([\w*.]+)\s+files?\s+(?:from\s+)?(.+?)\s+(?:to|into)\s+(.+?)$", t)
        if m:
            op = "copy" if m.group(0).startswith("copy") else "move"
            return self._local_bulk(m.group(1), m.group(2).strip(), m.group(3).strip(), op)
        m = re.search(r"\b(?:delete|remove|clear)\s+(?:all\s+)?([\w*.]+)\s+files?\s+in\s+(.+?)$", t)
        if m:
            return self._local_bulk(m.group(1), m.group(2).strip(), "", "delete")
        return None

    def _try_local(self, text: str) -> str | None:
        """Best-effort OFFLINE implementation. None → not implementable locally."""
        t = text.lower().strip()

        # Temp / recycle-bin cleanup (requires explicit confirmation)
        if re.search(r"(clean|clear|empty)\s+(temp|temporary|recycle|recycle bin)", t) or \
           re.search(r"\b(temp|temporary|recycle)\b.*\b(clean|clear|empty)\b", t):
            if not self._confirm("Sir, temp aur recycle bin saaf karun? Haan ya nahi?"):
                return "Cleaning cancelled."
            n_temp = 0
            n_temp = 0
            for base in ("TEMP", "TMP"):
                p = os.environ.get(base, "")
                if os.path.isdir(p):
                    cutoff = datetime.datetime.now() - datetime.timedelta(days=1)
                    for root, _dirs, fnames in os.walk(p):
                        for fn in fnames:
                            try:
                                fp = os.path.join(root, fn)
                                if datetime.datetime.fromtimestamp(os.path.getmtime(fp)) < cutoff:
                                    os.remove(fp)
                                    n_temp += 1
                            except OSError:
                                pass
            bin_msg = ""
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                    capture_output=True, text=True, timeout=30)
                bin_msg = " Recycle bin emptied."
            except Exception:
                pass
            return f"Cleaned {n_temp} old temp file(s).{bin_msg}"

        return None

    def _learn_automation(self, user_text: str, engine: str, task: str) -> None:
        """Auto-learn: save as a custom automation so next time it replays instantly."""
        if not self.auto_learn:
            return
        if not user_text or not task:
            return
        self.automations.add(user_text, task, engine)
        self.learning.record_lesson(f"learned automation: {user_text}", engine, task)

    @staticmethod
    def _is_success(reply: str) -> bool:
        """Does a reply indicate a successful (non-cancelled, non-error) local implementation?."""
        lower = reply.lower()
        return not any(w in lower for w in (
            "cancelled", "not found", "error", "failed", "not possible",
            "not available", "folder not found", "no .", "no files"))


    def _looks_performed(self, reply):
        """Does an engine reply indicate a task was actually performed
        (rather than a conversational answer)? Used for auto-learning."""
        low = reply.lower()
        performed = False
        if any(w for w in ("created", "downloaded", "installed", "opened", "started",
                           "saved", "wrote", "closed", "removed", "deleted", "moved",
                           "completed", "done.", "done\n", "ready", "is now", "has been",
                           "have set up", "set up", "generated", "built a", "made a",
                           "extracted", "zipped", "renamed", "updated", "configured")
                if w in low):
            performed = True
        return performed

    def _log_action(self, kind: str, text: str, result: str) -> None:
        """Write every handled request to the local agent log (D:\\YOUR_DATA_PATH\\LocalAgent)."""
        if not self.local_agent_log:
            return
        try:
            from AK.config import get_data_dir
            log_dir = os.path.join(get_data_dir(), "LocalAgent")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "actions.log"), "a", encoding="utf-8") as fh:
                fh.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] [{kind}]\n")
                fh.write(f"   User : {text}\n   Reply: {result}\n\n")
        except Exception:
            pass

    def _learn_in_background(self, text: str, reply: str, engine: str,
                             task: str = "", announce: bool = False) -> None:
        """Store a learned answer AFTER the reply — never before it.

        Kaam pehle (answer ready), memory baad me: cache write, lesson log,
        automation learn aur log file sab ek daemon thread me chalta hai, isliye
        reply par koi ruk nahi lagti. `_speak_learn_notes()` uske baad
        'naya seekha' bolega — jab jawab bol chuka ho.
        """
        def _worker() -> None:
            try:
                self._log_action(engine, text, reply)
                self.learning.record_lesson(text, engine, reply)
                if engine == "engine":
                    self.learning.log_unknown(text)
                    if (self.auto_learn and self._is_task_request(text)
                            and self._looks_performed(reply)):
                        self._learn_automation(text, "engine", task or text)
                        print("[automation] learned '" + text.strip() + "'")
                    is_new = self.self_learn.cache_answer(
                        text, reply, "engine", 0.9)
                    if (is_new and announce and not self._is_task_request(text)
                            and self._worth_announcing(text)):
                        self.self_learn.announce(
                            f"naya seekha: {text.strip()[:60]}")
                elif engine == "local":
                    self.learning.record_usage("local")
            except Exception as exc:  # noqa: BLE001
                print(f"[learn] background store failed ({exc}).")

        threading.Thread(target=_worker, daemon=True,
                         name="ak-learn-bg").start()

    def _speak_learn_notes(self) -> None:
        """Say any 'naya seekha' notes queued by the background learner.

        Called ONLY right after a reply has been spoken, so an announcement
        never delays the actual answer.
        """
        try:
            anns = self.self_learn.pending_announcements()
        except Exception:  # noqa: BLE001
            return
        if anns:
            self._say("Sir, " + "; ".join(anns[:2]) + ".")

    def _ps(self, command: str, timeout: int = 30) -> str:
        """Run a PowerShell one-liner and return stdout/stderr."""
        self._wf_step(f"Run: {command[:70]}", kind="command", command=command)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True, text=True, timeout=timeout,
            )
            return (r.stdout or r.stderr or "").strip()
        except Exception as exc:
            return f"PowerShell error: {exc}"

    def _youtube_first_video(self, query):
        """Resolve a YouTube search to the FIRST ACTUAL organic video URL.

        The raw HTML contains many videoId occurrences (ads, shelves,
        carousels, promos) that appear BEFORE the real results — grabbing
        the first regex match plays an unrelated video (e.g. a promoted
        "Fitoor" clip instead of "Tum Hi Ho"). So parse the ytInitialData
        JSON and walk it in document order for videoRenderer objects, which
        are exactly the search results. Falls back to regex only if the
        JSON cannot be parsed at all; returns '' so the caller can open the
        search page when nothing at all works."""
        import json as _json
        encoded = urllib.parse.quote(query)
        variants = [
            "https://www.youtube.com/results?search_query=" + encoded,
            "https://www.youtube.com/results?search_query=" + encoded
            + "&sp=EgIQAQ%3D%3D",
            "https://m.youtube.com/results?search_query=" + encoded,
        ]
        html = ""
        for _ in range(2):
            for url in variants:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/120.0 Safari/537.36"})
                    html = urllib.request.urlopen(req, timeout=15) \
                        .read().decode("utf-8", "replace")
                except Exception:
                    continue
                vid = self._yt_first_from_json(html, _json)
                if vid:
                    return ("https://www.youtube.com/watch?v=" + vid)
        if html:
            return self._yt_first_from_html(html)
        return ""

    @staticmethod
    def _yt_first_from_json(html, json_mod):
        """Walk ytInitialData in document order and return the first
        videoRenderer's videoId (the top organic search result)."""
        m = (re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.DOTALL)
             or re.search(r'ytInitialData"\]\s*=\s*(\{.*?\});', html, re.DOTALL)
             or re.search(r"ytInitialData\s*=\s*(\{.*?\});", html, re.DOTALL))
        if not m:
            return ""
        try:
            data = json_mod.loads(m.group(1))
        except Exception:
            return ""
        # BFS keeps results in document order; stop at the very first match.
        stack, seen = [data], 0
        while stack:
            node = stack.pop(0)
            seen += 1
            if seen > 400000:
                break
            if isinstance(node, dict):
                vr = node.get("videoRenderer")
                if isinstance(vr, dict):
                    vid = vr.get("videoId")
                    if vid:
                        return vid
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                for value in node:
                    if isinstance(value, (dict, list)):
                        stack.append(value)
        return ""

    @staticmethod
    def _yt_first_from_html(html):
        """Fallback: first videoId that appears near a videoRenderer block
        (skips ad/promo videoIds that sit outside any renderer)."""
        m = re.search(
            r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"', html)
        if m:
            return m.group(1)
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        if m:
            return m.group(1)
        return ""

    def _open_music(self, song: str, engine: str) -> bool:
        """Actually play/launch the requested music.

        YouTube: resolve the FIRST real video URL (so the song really plays),
        falling back to the search page only if every resolution attempt
        failed.  Spotify/Gaana: open their search results.  The URL is
        genuinely opened in the browser.  Returns True when a direct video
        was opened (so the reply can stay honest otherwise)."""
        if engine in ("yt", "youtube"):
            url = self._youtube_first_video(song)
            if url:
                self.skills._chrome_open(url)
                return True
            self.skills._chrome_open("https://www.youtube.com/results?search_query="
                                     + urllib.parse.quote(song))
            return False
        if engine == "gaana":
            self.skills._chrome_open("https://gaana.com/search/"
                                     + urllib.parse.quote(song))
            return False
        self.skills._chrome_open("https://open.spotify.com/search/"
                                 + urllib.parse.quote(song))
        return False

    def _music_msg(self, song: str, engine: str, direct: bool = True) -> str:
        name = {"youtube": "YouTube", "yt": "YouTube", "spotify": "Spotify",
                "gaana": "Gaana"}.get(engine, engine.title())
        if engine in ("yt", "youtube") and not direct:
            return (f"{song} ki direct video nahi mil paayi (net slow) — "
                    f"YouTube par search page khol diya hai.")
        return f"Playing {song} on {name}."

    _UNLOCK_PIPE = r"\\\\.\\pipe\\AKUnlock"

    def _unlock_blob(self):
        base = self.cfg.get("data_path", "D:\\YOUR_DATA_PATH")
        return os.path.join(base, "secure", "winlogon.bin")

    def _dpapi(self, data, encrypt):
        import ctypes
        from ctypes import wintypes
        adv = ctypes.windll.crypt32
        k32 = ctypes.windll.kernel32

        class DATA(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(wintypes.BYTE))]

        buf = ctypes.create_string_buffer(data, len(data))
        ind = DATA(len(data), ctypes.cast(buf, ctypes.POINTER(wintypes.BYTE)))
        outd = DATA()
        fn = adv.CryptProtectData if encrypt else adv.CryptUnprotectData
        ok = fn(ctypes.byref(ind), "AK unlock", None, None, None, 1,
                ctypes.byref(outd))
        if not ok:
            raise RuntimeError("DPAPI failed: %d" % k32.GetLastError())
        try:
            return ctypes.string_at(outd.pbData, outd.cbData)
        finally:
            k32.LocalFree(outd.pbData)

    def _save_unlock_password(self, pw):
        blob = self._unlock_blob()
        try:
            os.makedirs(os.path.dirname(blob), exist_ok=True)
            enc = self._dpapi(pw.encode("utf-8"), encrypt=True)
            with open(blob, "wb") as fh:
                fh.write(enc)
            subprocess.run(["icacls", blob, "/inheritance:r", "/grant:r",
                            ("%s:F" % os.environ.get("USERNAME", "Everyone")),
                            "SYSTEM:F"], capture_output=True, timeout=20)
        except Exception as exc:
            return (f"Unlock password save nahi hua: {exc}", True)
        return ("Unlock password encrypted save ho gaya (sirf tumhare account ke liye). "
                "Ab laptop lock karke 'unlock' bolo.", True)




    def _remove_unlock_password(self):
        blob = self._unlock_blob()
        try:
            if os.path.isfile(blob):
                os.remove(blob)
                return ("Unlock password hata diya. Ab 'unlock' kaam nahi karega.", True)
        except Exception as exc:
            return (f"Hata nahi payi: {exc}", True)
        return ("Pehle koi unlock password set hi nahi tha.", True)

    def _can_manage_unlock_pw(self):
        if getattr(self, "_from_server", False):
            return bool(self.cfg.get("voice_unlock", {}).get("allow_phone", False))
        return True

    def _do_unlock_now(self):
        blob = self._unlock_blob()
        if not os.path.isfile(blob):
            return ("Pehle bolo: 'set unlock password <tumhara windows password>'. "
                    "Encrypted save hoga, phir 'unlock' pe khud khol dungi.", True)
        try:
            with open(blob, "rb") as fh:
                pw = self._dpapi(fh.read(), encrypt=False).decode("utf-8", "replace")
        except Exception as exc:
            return (f"Unlock password padh nahi payi: {exc}", True)
        if not pw:
            return ("Stored unlock password khali hai - 'set unlock password ...' bolo.", True)
        try:
            import win32file
            import win32con
            import pywintypes
            try:
                pipe = win32file.CreateFile(self._UNLOCK_PIPE,
                                            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                            0, None, win32con.OPEN_EXISTING, 0, None)
            except pywintypes.error:
                pipe = None
            if pipe is None:
                return ("Unlock service chal nahi rahi. 'unlock\\install_service.py' "
                        "ko ADMIN se ek baar chalao, phir pakka kaam karega.", True)
            try:
                win32file.WriteFile(pipe, (pw + "\n").encode("utf-8"))
            except Exception as exc:
                return (f"Unlock service se baat nahi hui: {exc}", True)
            finally:
                win32file.CloseHandle(pipe)
        except Exception:
            raise
        return "Unlock kar diya - screen khul rahi hai."

    def _unlock_pc_by_voice(self):
        vu = self.cfg.get("voice_unlock", {})
        if not vu.get("enabled", True):
            return ("Voice unlock band hai - config.json me voice_unlock.enabled true karo.", True)
        from_phone = getattr(self, "_from_server", False)
        if from_phone:
            if not vu.get("allow_phone", False):
                return ("Security ke liye PHONE se unlock BAND hai "
                        "(config me voice_unlock.allow_phone=false). "
                        "Laptop ke paas jakar muh se 'unlock' bolo.", True)
            return self._do_unlock_now()
        if self.typing_mode:
            return ("Tum keyboard pe ho - Windows password seedha daal do. "
                    "Voice unlock sirf bolne se hota hai.", True)
        if self._verifier is None:
            return ("Mujhe sirf TUMHARI awaaz pe unlock karna hai, par voiceprint enrolled "
                    "nahi hai. Pehle 'train my voice' bolo, phir 'unlock'.", True)
        self._say("Awaaz check kar rahi hoon - phir se 'unlock' bolo.")
        heard = self.listener.wait_for_wake(

            "[AK] Awaaz verify - bolo 'unlock'",
            ("unlock", "khul do", "khula do", "open karo"), self._verifier)
        if not heard:
            return ("Awaaz match nahi huyi - sirf asli peculiar awaaz pe unlock hoga. "
                    "Chalo, phir try karo.", True)
        return self._do_unlock_now()

    OLLAMA_EXE = r"C:\Users\YOUR_USERNAME\AppData\Local\Programs\Ollama\ollama.exe"
    OLLAMA_URL = "http://localhost:11434/api/tags"

    def _ollama_alive(self, timeout: float = 2.0) -> bool:
        try:
            import requests

            requests.get(self.OLLAMA_URL, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _ensure_ollama_running(self, wait: float = 45.0) -> bool:
        """Start `ollama serve` — only ever when the user asks for local mode.

        Ollama does not autostart any more. On this machine (i5-6200U, 2 cores)
        a 4B model needs 7-30 s per reply, so AK stays on the instant local
        rule/engine path unless local mode is requested by name.
        """
        if self._ollama_alive():
            return True
        if not os.path.isfile(self.OLLAMA_EXE):
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([self.OLLAMA_EXE, "serve"], creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            return False
        print("[ollama] starting on request ...")
        deadline = time.time() + wait
        while time.time() < deadline:
            if self._ollama_alive():
                print("[ollama] ready.")
                return True
            time.sleep(2)
        return False

    def _stop_ollama(self) -> str:
        """Kill Ollama (tray app + server) and return to the fast path."""
        killed = []
        for image in ("ollama app.exe", "ollama.exe"):
            try:
                r = subprocess.run(["taskkill", "/F", "/IM", image],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    killed.append(image)
            except Exception:  # noqa: BLE001
                pass
        self.brain.set_backend("rule")
        if killed:
            return "Ollama band kar diya. Ab fast mode mein hoon, turant jawab dunga."
        return "Ollama toh band hi tha boss. Fast mode mein hi hoon."

    def _handle_engine(self, task: str) -> str:
        """Route a request to the task engine — chat via ask_AK, tasks via execute_task."""
        if not self.engine.available:
            return "AK task engine is not available."
        t = task.lower().strip()
        # Coding / task keywords → use execute_task (actually performs the task)
        is_task = any(kw in t for kw in ("write code", "write a", "generate", "create script",
                                   "create file", "create a file", "create folder",
                                   "create a folder", "explain code", "debug", "fix bug",
                                   "download", "install", "set up", "setup", "convert",
                                   "organize", "backup", "clean up", "clean ", "search for",
                                   "find file", "find files", "rename", "move file",
                                   "move files", "copy file", "copy files", "extract",
                                   "merge", "split", "update", "build", "deploy",
                                   "open the", "open a", "open ", "run the", "run a",
                                   "start the", "start a", "send", "change the",
                                   "make a", "make an", "make the"))
        if is_task:
            # the routed task can carry the memory preamble - the workflow
            # must store/compare the user's own words only
            raw_task = task.split("User:")[-1].strip() if "User:" in task else task
            # ── learned workflow? → replay it instead of re-learning ──
            wf = self._wf_match(raw_task)
            if wf:
                self._say(f"Sir, yeh kaam maine pehle seekha hai. Wahi steps "
                          f"follow kar raha hun.")
                report = self._wf_run(wf)
                ran = len(report.get("ran") or [])
                failed = report.get("failed") or []
                if report.get("ok"):
                    return (f"Sir, seekhe hue steps follow karke {wf.get('title')} "
                            f"ho gaya ({ran} steps).")
                return (f"Sir, {len(failed)} step fail hue: "
                        f"{failed[0].get('title') if failed else 'unknown'}. "
                        f"Baaki steps chal gaye.")

            # ── naya kaam → record + seekho ──
            # A session may already be running (the task lane opened one) -
            # in that case AK just adds steps to it instead of nesting.
            owns_session = False
            coach = self._wf_coach()
            if self._wf_auto_record() and coach and not coach.active():
                owns_session = bool(self._wf_begin(raw_task))
                if owns_session:
                    self._wf_step(f"Task start: {raw_task[:60]}", kind="action")
            result = self.engine.execute_task(task)
            if self._wf_auto_record():
                self._wf_step("Task engine finished", kind="action",
                              detail=str(result)[:200])
                saved = (self._wf_end(outcome="done", reply=str(result)[:400])
                         if owns_session else None)
            else:
                saved = None
            if saved and saved.get("ok"):
                result = (f"{result}\n\n[seekha] '{raw_task[:60]}' ka workflow ban "
                          f"gaya ({saved['steps']} steps) — agli baar main yeh "
                          f"khud steps follow karke kar dunga.")

        else:
            # Everything else → conversational AK brain
            print("[AK:brain] thinking...")
            result = self.engine.ask_AK(task)
        if len(result) > 500:
            cut = result.rfind(". ", 0, 500)
            if cut == -1:
                cut = result.rfind("! ", 0, 500)
            if cut == -1:
                cut = result.rfind("? ", 0, 500)
            if cut == -1:
                cut = 497
            result = result[:cut+1] + ".."
        return result
    def _wake_match(self, text: str, phrases: tuple) -> bool:
        """Wake-phrase match with word boundaries, so the short word 'ak'
        can't spuriously match inside 'background' / 'black' etc."""
        return _wake_hit(text, phrases)

    def _command_after_wake(self, heard: str, phrases: tuple) -> str:
        """Text spoken after the wake word in the same breath ('' if none).

        Uses voice.strip_wake so Whisper's spellings ('aka', 'a.k.', 'ak.')
        are handled too — before this, a command said right after 'ak' was
        dropped and AK had to be told all over again.
        """
        return strip_wake(heard, phrases)

    def _hud_wake_command(self, rest: str) -> tuple | None:
        """'ak chalao' -> start the holographic gesture control.

        The wake word is stripped from the transcript before the command is
        routed, so the HUD router only sees 'chalao'. This puts the wake
        context back and asks the HUD router whether it wants the sentence.

        Returns (reply, speak) when the HUD took the command, None when it is
        something else and the normal assistant should have it.
        """
        try:
            from AK import ak_commands

            return ak_commands.match_wake(self, rest, wake=self.wake_word)
        except Exception as exc:  # noqa: BLE001
            print(f"[ak] hud wake error: {exc}")
            return None

    def _quit_command(self, text: str) -> str:
        """Is this a deliberate "shut down"?

        Returns "yes", "no" or "needs_wake".

        A bare "exit" is a very common false transcript in always-listening
        mode (a cough, a fan, a door closing) and it used to kill AK - and
        with it the holographic gesture control. In always-listening mode the
        wake word is required, so only a deliberate "ak exit" shuts AK down.
        The match is word-bounded on purpose: "textile" is not an exit.
        """
        t = (text or "").lower().strip()
        if not t:
            return "no"
        if not re.search(r"\b(exit|quit|goodbye|bye\s+bye)\b", t):
            return "no"
        if self.require_wake:
            return "yes"
        if self._wake_match(t, WAKE_PHRASES):
            return "yes"
        return "needs_wake"

    # ── Face Lock (only the registered face can use AK) ─────────────
    def _setup_face_lock(self) -> None:
        """Load the enrolled face model (if any) so only that face can use AK."""
        fl = self.cfg.get("face_lock", {})
        self._face_lock_enabled = bool(fl.get("enabled", True))
        self._face_lock_thr = float(fl.get("threshold", 91.0) or 91.0)
        self._face_lock_path = os.path.join(
            self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"), "face_lock")
        self._face_locker = None
        if not self._face_lock_enabled:
            print("[face lock] OFFLI hai config mein — koi bhi chehra AK use "
                  "kar sakta hai. 'register my face' bolo lock ON karne ke liye.")
            return
        try:
            self._face_locker = FaceLock(self._face_lock_path,
                                         threshold=self._face_lock_thr)
        except Exception as exc:
            print(f"[face lock] init error: {exc}")
            return
        try:
            users = self._face_locker.list_users()
        except Exception:
            users = ""
        if users:
            print(f"[face lock] ON — sirf registered chehre: {users.strip()}")
        else:
            print("[face lock] ON but koi face enroll nahi hai — "
                  "'register my face' bolo apna chehra add karne ke liye.")

    def _train_face(self) -> str:
        """Enroll the user's face (camera open → captures → offline LBPH)."""
        if self._face_locker is None:
            return ("Face lock is not available — OpenCV camera init nahi hua.")
        owner = self.cfg.get("assistant_name", "Owner") or "Owner"
        self._say("Camera khol raha hun. Camera ke saamne apna chehra rakho, "
                  "main 12 clear captures lena chahta hun.")
        try:
            msg, ok = self._face_locker.enroll(owner, samples=12, cam_index=0)
        except Exception as exc:
            return f"Face enroll fail ho gaya: {exc}"
        if not ok:
            return msg or ("Camera ne clear chehra nahi pakda. Roshni sahi "
                           "rakho aur chehra seedha camera pe rakho.")
        self._face_lock_enabled = True
        return (f"{msg} Ab sirf aapka chehra AK use kar sakta hai. "
                f"Camera window band ho gayi.")

    def _face_lock_status(self) -> str:
        if not self._face_lock_enabled:
            return ("Face lock is OFF — koi bhi chehra AK use kar sakta hai. "
                    "Say 'register my face' ise ON karne ke liye.")
        if self._face_locker is not None:
            try:
                users = self._face_locker.list_users()
            except Exception:
                users = ""
            if users:
                return (f"Face lock is ON — sirf registered chehre: "
                        f"{users.strip()}. Wahi AK use kar sakte hain.")
            return ("Face lock is ON but koi face enroll nahi hai. "
                    "Say 'register my face'.");
        return ("Face lock is ON in config, but camera engine init nahi hua.")

    def _handle_face_lock_cmd(self, hl: str) -> Optional[str]:
        m = re.search(
            r"\b(register my face|register face|train my face|train face"
            r"|enroll my face|enroll face|add my face|add face"
            r"|enable face lock|disable face lock|face lock status"
            r"|face lock on|face lock off|remove my face|remove face"
            r"|delete my face|clear my face|lock the screen with face|face lock)\b", hl)
        if not m:
            return None
        cmd = m.group(1)
        if "status" in cmd:
            return self._face_lock_status()
        if "disable" in cmd or "off" in cmd:
            self._face_lock_enabled = False
            return ("Face lock disabled — koi bhi chehra AK use kar sakta hai. "
                    "Say 'register my face' to lock it again.")
        if "remove" in cmd or "delete" in cmd or "clear" in cmd:
            if self._face_locker is None:
                return "Face lock is not available right now."
            try:
                msg, ok = self._face_locker.remove(owner)
            except Exception as exc:
                return f"Face remove fail ho gaya: {exc}"
            self._face_lock_enabled = False
            return (f"{msg} Face lock ab OFF hai. 'register my face' bolo "
                    f"dobara lock karne ke liye.")
        if "enable" in cmd or "on" in cmd:
            self._face_lock_enabled = True
            return self._face_lock_status()
        return self._train_face()

    def _handle_myface_cmd(self, hl: str) -> Optional[str]:
        m = re.search(
            r"\b(register my face|train my face|enroll my face|add my face"
            r"|remove my face|delete my face|clear my face|my face status)\b", hl)
        if not m:
            return None
        cmd = m.group(1)
        if "status" in cmd:
            return self._face_lock_status()
        if "remove" in cmd or "delete" in cmd or "clear" in cmd:
            if self._face_locker is None:
                return "Face lock is not available right now."
            owner = self.cfg.get("assistant_name", "Owner") or "Owner"
            try:
                msg, ok = self._face_locker.remove(owner)
            except Exception as exc:
                return f"Face remove fail ho gaya: {exc}"
            self._face_lock_enabled = False
            return (f"{msg} Face lock ab OFF — camera window band na ho toh "
                    f"band ho gayi. 'register my face' bolo dobara lock karne "
                    f"ke liye.")
        return self._train_face()

    def _handle_camera_watch_cmd(self, hl: str) -> Optional[str]:
        """Wahan kaun hai — camera ke saamne aane wale logon ki info batao."""
        m = re.search(
            r"\b(camera watch|watch the camera|watch camera|watch mode"
            r"|camera dekh|camera monitor|camera check"
            r"|kis kis|kis kiska|camera ke saamne"
            r"|who is there|who is in front|who came|who is outside)\b", hl)
        if not m:
            return None
        if self._face_locker is None:
            return "Camera watch ke liye face engine ready nahi hai."
        self._say("Camera dekh raha hun — jo bhi saamne aayega, bataunga.")
        try:
            return self._face_locker.watch(on_face=self._announce_face)
        except Exception as exc:
            return f"Camera watch fail ho gaya: {exc}"

    def _announce_face(self, name: str) -> None:
        try:
            self._say(f"{name} camera ke saamne aaya.")
        except Exception:
            pass

    def _handle_face_intel_cmd(self, hl: str) -> Optional[str]:
        """Face Intel — camera se chehra lo aur online public info dikhao."""
        if not re.search(
                r"\b(face intel|face info|info on this person|who is this"
                r"|who is this person|identify this person|find this person"
                r"|yeh kon hai|ye kaun hai|ye kon hai|is kaun|kaun hai yeh"
                r"|yah kon hai|yah kaun hai|yaah kon hai|yah ka ho"
                r"|chehra dhundh|chehra intel|face search"
                r"|person intel|person ka data|batao yeh kaun)\b", hl):
            return None
        try:
            import cv2  # noqa: F401  (lazy: 4.5s, only for face intel)
        except Exception:
            return "Camera engine (OpenCV) available nahi hai."
        return self._start_face_intel()

    def _start_face_intel(self) -> str:
        """Launch the Face Intel GUI and monitor it; speak the summary."""
        session = os.path.join(
            self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"),
            "face_intel", "session_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(session, exist_ok=True)
        gui = os.path.join(_HERE, "AK", "face_intel_ui.py")
        if not os.path.isfile(gui):
            return "Face Intel GUI file nahi mili — AK/face_intel_ui.py check karo."

        self._say("Chehra intel shuru. Camera khol raha hun — camera ke "
                  "saamne chehra rakho, main info fetch karunga.")

        fi_cfg = (self.cfg.get("face_intel", {}) or {})
        gui_log = os.path.join(session, "gui.log")
        try:
            log_fh = open(gui_log, "w", encoding="utf-8",
                          errors="replace")
            subprocess.Popen(
                [sys.executable, gui, "--session", session,
                 "--data-dir", self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"),
                 "--reverse-cooldown", str(fi_cfg.get("reverse_cooldown", 5.0)),
                 "--reverse-attempts", str(fi_cfg.get("reverse_attempts", 2))],
                cwd=_HERE, stdout=log_fh, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            log_fh.close()
        except Exception:
            print("GUI launch fail — session check karo: " + session)

        threading.Thread(
            target=self._monitor_face_intel, args=(session,),
            daemon=True).start()
        return (f"Face Intel shuru ho gaya — session {os.path.basename(session)}. "
                f"Results live aate rahenge, profile window khul rahi hai.")

    def _monitor_face_intel(self, session: str) -> None:
        """Poll report.json; announce when done using the spoken summary."""
        report_p = os.path.join(session, "report.json")
        waited = 0
        announced_search = False
        try:
            while waited < 420:        # 7 min hard cap
                time.sleep(2)
                waited += 2
                if not os.path.isfile(report_p):
                    continue
                try:
                    with open(report_p, "r", encoding="utf-8") as fh:
                        rep = json.load(fh)
                except Exception:
                    continue
                status = rep.get("status", "")
                if status in ("searching", "capturing") and not announced_search:
                    announced_search = True
                    self._say("Chehra mil gaya, info fetch ho rahi hai...")
                if status == "error":
                    err = rep.get("error", "chehra nahi mila")
                    self._say(f"Face intel fail ho gaya: {err}")
                    return
                if status == "done":
                    summary = rep.get("spoken_summary")
                    if summary:
                        self._say(summary)
                    else:
                        self._say("Face intel complete — report window me dekho.")
                    return
            self._say("Face intel ka time khatam ho gaya.")
        except Exception as exc:
            try:
                self._say(f"Face intel monitor me error: {exc}")
            except Exception:
                pass

    def _setup_voice_lock(self) -> None:
        """Load the enrolled voiceprint (if any) so only that voice can wake AK."""
        vl = self.cfg.get("voice_lock", {})
        self._voice_lock_enabled = bool(vl.get("enabled", True))
        self._voice_lock_thr = float(vl.get("threshold", 0.85) or 0.85)
        self._voiceprint_path = os.path.join(
            self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"), "voiceprint.json")
        self._verifier = None
        if not self._voice_lock_enabled:
            return
        try:
            vp = Voiceprint(self._voiceprint_path)
            if vp.load():
                self._verifier = vp.matches
                print(f"[voice lock] ON — {vp.samples} enrolled sample(s), "
                      f"threshold {vp.threshold:.2f} (set from your voice)")
            elif os.path.exists(self._voiceprint_path):
                print("[voice lock] voiceprint is from an older version — "
                      "say 'train my voice' to re-enroll with the new engine.")
            else:
                print("[voice lock] enabled but no voiceprint yet — say "
                      "'train my voice' to lock, or 'unlock voice' to disable.")
        except Exception as exc:
            print(f"[voice lock] init error: {exc}")
    def _train_voice(self) -> str:
        """Enroll the user's voice (3 utterances) into an offline voiceprint."""
        if getattr(self, "listener", None) is None:
            return "No microphone available. Run with a mic to train the voice lock."
        vp = Voiceprint(self._voiceprint_path)
        explicit = float(self.cfg.get("voice_lock", {}).get("threshold", 0.85) or 0.85)
        tuned = abs(explicit - 0.85) > 1e-9
        need = 3
        i = 0
        tries = 0
        while i < need and tries < 9:
            tries += 1
            self._say(f"Trial {i + 1} of {need}. Say the wake phrase now.")
            self._viz_state("listening")
            buf = self.listener.capture_phrase()
            if not buf or len(buf) < 8000:
                self._say("I didn't catch that. Try again.")
                continue
            vec = vp.add_sample(buf)
            if vec is None:
                self._say("That recording was too quiet. Please speak up.")
                continue
            i += 1
        if vp.samples < 2:
            return "Could not train the voice — need at least 2 clear samples."
        # Threshold comes from the USER'S OWN voice variability — a fixed
        # 0.85 was rejecting the owner; auto-threshold adapts to them.
        # A manually-tuned config value (anything != default 0.85) wins.
        if tuned:
            vp.threshold = explicit
        else:
            vp.auto_threshold()
        if not vp.save():
            return "Could not save the voiceprint file."
        self._verifier = vp.matches
        self._voice_lock_enabled = True
        return (f"Voice lock trained with {vp.samples} samples and saved to "
                f"{self._voiceprint_path}. Wake threshold set to {vp.threshold:.2f} "
                f"from your own voice. Now only you can wake me.")
    def _voice_lock_status(self):
        if not self._voice_lock_enabled:
            return "Voice lock is OFF. Anyone's voice can wake me. Say 'train my voice' to enable."
        if self._verifier is not None:
            return f'Voice lock is ON for your voice. Enrolled samples stored at {self._voiceprint_path}.'
        return "Voice lock is ON in config, but no voiceprint yet. Say 'train my voice' to enroll, or 'unlock voice' to disable."




    def _handle_voice_lock_cmd(self, hl: str) -> Optional[str]:
        m = re.search(
            r"\b(train my voice|train voice|enroll voice|enroll my voice"
            r"|register my voice|save my voice|lock voice|enable voice lock"
            r"|unlock voice|disable voice lock|voice lock status)\b", hl)
        if not m:
            return None
        cmd = m.group(1)
        if "status" in cmd:
            return self._voice_lock_status()
        if any(k in cmd for k in ("train", "enroll", "register", "save", "lock")):
            return self._train_voice()
        self._voice_lock_enabled = False
        self._verifier = None
        return "Voice lock disabled. Say 'train my voice' to re-enable it."


    def _handle_myvoice_cmd(self, hl: str) -> Optional[str]:
        m = re.search(
            r"\b(record my voice|my voice record|record voice"
            r"|my voice status|clear my voice|delete my voice"
            r"|switch to my voice|use my voice|use my own voice)\b", hl)
        if not m:
            return None
        cmd = m.group(1)
        if "switch" in cmd or "use" in cmd:
            return self.speaker.switch_voice("MY VOICE")
        if "status" in cmd:
            from AK.myvoice import MyVoiceClient
            client = MyVoiceClient(self.cfg)
            if not client.available:
                return ("My Voice engine is not installed. Run the D-drive "
                        "setup first.")
            if not client.enrolled:
                return ("My Voice is ready but no voice recorded yet. Say "
                        "'record my voice' and speak for a good half minute.")
            return (f"My Voice is ready ({os.path.getsize(client.ref_wav)}"
                    f" bytes reference saved). Say 'switch to my voice' to "
                    f"start using it.")
        if "clear" in cmd or "delete" in cmd:
            path = os.path.join(self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"),
                                "myvoice_ref.wav")
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    return "Your recorded voice has been cleared."
            except Exception as exc:
                return f"Could not clear your voice: {exc}"
            return "No recorded voice to clear."
        # record
        if getattr(self, "listener", None) is None or self.listener.active == "text":
            return "No microphone available. Run with a mic to record your voice."
        secs = int(self.cfg.get("myvoice", {}).get("record_seconds", 40))
        from AK.myvoice import MyVoiceClient
        client = MyVoiceClient(self.cfg)
        if not client.available:
            return ("My Voice engine is not installed. The D-drive setup "
                    "(venv312) is missing.")
        self._say(f"Recording your voice now. Speak naturally in Hindi or "
                  f"English for about {secs} seconds.")
        self._viz_state("listening")
        buf = self.listener.capture_reference(
            secs,
            f"[AK] Recording your voice (~{secs}s) ... talk naturally ...")
        if not buf or len(buf) < 4 * 16000:
            return "That recording was too short. Please try again and speak longer."
        path = client.ref_wav

    def _detect_shape_request(self, text: str) -> Optional[str]:
        """Visualizer shape lane: 'kutta banao', 'pizza dikhao', 'krishna bana'.

        Returns the canonical shape key the stardust visualizer knows, or None.
        A sentence carrying an art word belongs to the paint lane instead, so
        the two never fight: "kutta banao" is a shape, "kutta painting banao" is
        a picture.
        """
        t = (text or "").lower()
        t = t.replace("?", " ").replace("!", " ").replace(",", " ").replace(".", " ")
        for spaced, collapsed in _PHRASE_COLLAPSE.items():
            t = t.replace(spaced, collapsed)
        t = re.sub(r"\s+", " ", t).strip()
        if not t or _PAINT_ART_RE.search(t):
            return None
        toks = t.split()
        if len(toks) > 6 or not _has_build_verb(toks):
            return None
        if any(tok in _SHAPE_BLOCK_WORDS for tok in toks):
            return None
        for word in _VIZ_SHAPE_ORDER:
            if re.search(rf"\b{re.escape(word)}\b", t):
                return word
        return None


    def _detect_build_request(self, text: str) -> Optional[str]:
        """'"'"'snake game banao'"'"', '"'"'restaurant website banao'"'"' -> kind for the builder.

        Returns "game" / "website" / "apk" / "app", or None. Vocabulary lives in
        AK/paintgen.py (CODE_WORDS) so the build lane and the paint lane can
        never both claim the same sentence, and a build verb is always required
        so a plain question ("what is a game") is never read as a request.
        """
        t = (text or "").strip().lower()
        if not t or len(t) > 220:
            return None
        try:
            from AK.paintgen import CODE_WORDS
        except Exception:
            return None
        toks = [w for w in re.split(r"[^a-z0-9+#.]+", t) if w]
        if not _has_build_verb(toks):
            return None
        blob = " ".join(toks)
        for kind in ("game", "website", "apk", "app"):
            for word in CODE_WORDS[kind]:
                if re.search(rf"\b{re.escape(word)}\b", blob):
                    return kind
        return None

    def _ask_build_details(self, kind: str) -> str:
        """Ask the user WHAT kind of content/game/app they want, before build."""
        prompts = {
            "apk": "Accha, detail batao - kaunsi Android app banani hai? Kya naam, kya kaam karegi, koi specific feature?",
            "game": "Batao, kaisa game chahiye? Jaise snake, maze, tic-tac-toe, quiz, racing - naam aur style batao.",
            "website": "Website kiske liye banani hai? Kaunse pages chahiye (home, about, contact), aur koi theme/color?",
        }
        q = prompts.get(kind, "Accha, detail batao!")
        self._say(q)
        self._viz_state("listening")
        if self.typing_mode:
            try:
                return input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                return ""
        heard = self.listener.listen("details")
        return (heard or "").strip()
    def _build_with_engine(self, kind: str, request: str, details: str) -> tuple[str, str]:
        """Build the requested game/app/website/apk with engine (default),
        return (reply, launch_target)."""
        self._viz_state("processing")
        data_dir = self.cfg.get("data_path", r"D:\YOUR_DATA_PATH")
        builds = os.path.join(data_dir, "Builds")
        try:
            os.makedirs(builds, exist_ok=True)
        except Exception:
            builds = os.path.join(_HERE, "Builds")
            try:
                os.makedirs(builds, exist_ok=True)
            except Exception:
                builds = _HERE
        project = os.path.join(builds, f"{kind}_{datetime.datetime.now():%Y%m%d_%H%M%S}")
        task = (
            f"Build a complete, working {kind}. The user asked: '{request}'.\n"
            f"Extra details from the user: '{details or 'no extra detail - use good defaults'}'.\n"
            f"Create the full project inside this exact folder: {project}\n"
            f"Make it real and runnable: for a website write index.html (+css/js), "
            f"for a game/app write a runnable main script (e.g. .py, .html, or .exe).\n"
            f"Keep it self-contained (no external installs). After building, briefly "
            f"tell me the exact main file path so I can open it for the user."
        )
        reply = self.engine.execute_task(task, workdir=project)
        # If the chosen engine produced nothing (the small local model can
        # stall on a big site), escalate to the fast engine once so the user
        # still gets a result instead of an empty folder.
        try:
            created = [f for f in glob.glob(os.path.join(project, "**", "*"),
                                            recursive=True) if os.path.isfile(f)]
        except Exception:
            created = []
        if not created:
            try:
                from AK import LocalAgent
                is_local = isinstance(self.engine, LocalAgent)
            except Exception:
                is_local = False
            if is_local:
                self._say("Local engine se files nahi bani — AK fast engine se "
                          "bana raha hoon.")
                try:
                    fast = _make_coding_agent(self.cfg, force="engine")
                    if getattr(fast, "available", False):
                        reply = fast.execute_task(task, workdir=project) or reply
                except Exception as exc:  # noqa: BLE001
                    print(f"[build] fast fallback failed: {exc}")
        return reply, project
    def _handle_build_request(self, text, kind):
        """Full game/app/website/apk build: ask details -> engine (default) ->
        open the result."""
        self._say(f"Accha, {kind} banana hai. Pehle ek do baat puchta hoon.")
        details = self._ask_build_details(kind)
        self._viz_state("processing")
        self._say(f"AK se {kind} bana raha hoon... thodi der lag sakti hai.")
        coach = self._wf_coach()
        owns_session = False
        if coach and not coach.active():
            owns_session = bool(self._wf_begin(f"build {kind}: {text}"))
        self._wf_step(f"Build start ({kind})", kind="action",
                      detail=str(details)[:200])
        reply, project = self._build_with_engine(kind, text, details)
        opened = self._open_build_result(project, reply)
        self._wf_step(f"Build done ({kind})", kind="action",
                      detail=str(reply)[:200])
        saved = (self._wf_end(outcome="done", reply=str(reply)[:400])
                 if owns_session else None)
        base = reply.strip().replace("\\n", " ")
        if opened:
            base = f"{base} {opened}"
        if saved and saved.get("ok"):
            base = f"{base} [seekha] {saved['steps']} steps ka workflow save ho gaya."
        self.learning.record_lesson(text, "engine", base)
        return base
    def _viz_state(self, mode: str, label: str = "", dur: float = 0) -> None:
        """Feed the current state to the visualizer window (cheap, optional)."""
        try:
            if mode == "thinking":
                _island.thinking(True)
        except Exception:
            pass
        try:
            viz = getattr(self, "_viz", None)
            if viz is None or viz.poll() is not None:
                return
            # the last spoken phrase sticks around: the sand window colours
            # itself by whatever was just said, even after the state moves on
            if label and mode in ("listening", "responding"):
                self._viz_phrase = label
                self._viz_phrase_seq += 1
            path = os.path.join(self.cfg.get("data_path", r"D:\YOUR_DATA_PATH"),
                                "ak_vis_state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"mode": mode,
                           "label": label,
                           "text": label,
                           "phrase": getattr(self, "_viz_phrase", ""),
                           "phrase_seq": getattr(self, "_viz_phrase_seq", 0),
                           "dur": dur,
                           "ts": datetime.datetime.now().timestamp()}, fh)
        except Exception:
            pass
    def _viz_custom(self, key: str) -> None:
        """Make the visualizer morph into a custom shape."""
        self._viz_custom_k = key
        self._viz_state(f"custom:{key}")
    def _viz_listening(self, text: str = "") -> None:
        """Broadcast 'listening' — unless a custom shape is on screen, in
        which case the shape stays until the user says something normal."""
        if getattr(self, "_viz_custom_k", None):
            self._viz_state(f"custom:{self._viz_custom_k}")
        else:
            self._viz_state("listening", text)
    def _is_casual_chat(self, text: str) -> bool:
        """Lightweight check: is this everyday chat (no task/command intent)?"""
        t = (text or "").strip().lower()
        if not t:
            return False
        if any(s in t for s in ("? ", "?", "what is", "how to", "banao", "karo",
                                "dikha", "play", "open", "start", "stop")):
            return False
        casual = ("how are you", "kaise ho", "kya haal", "hi", "hello", "hey",
                  "namaste", "good morning", "good evening", "good afternoon",
                  "good night", "bye", "goodbye", "thanks", "thank you",
                  "shukriya", "ok", "okay", "nice", "great", "cool", "wow",
                  "achha", "theek", "haan", "hmm", "sure", "yes", "no", "nahi",
                  "radhe radhe", "namaskar", "hiya")
        return any(w in t for w in casual) and len(t) < 40
    def _auto_learn_from_chat(self, text: str) -> str:
        """Silently turn conversational self-statements into lasting memory."""
        t = (text or "").strip().lower()
        m = re.search(r"\bmy (?:name is|name'?s)\s+(.+?)(?:[.,!?]|$)", t)
        if m:
            name = m.group(1).strip()
            if name:
                self.skills.memory_add(f"user's name is {name}")
                return f"Got it — your name is {name}."
        return ""
    def _learn_local(self, text: str, reply: str) -> None:
        """Remember that we implemented this locally (persistent, offline)."""
        if self.auto_learn:
            self.automations.add(text, text, "local")
        self.learning.record_usage("local_learn")
        self.learning.record_lesson(f"learned locally: {text}", "local", reply)
        try:
            self.skills.memory_add(f"I implemented locally: '{text.strip()}' -> {reply}")
        except Exception:
            pass

    def _open_build_result(self, project: str, reply: str) -> str | None:
        """Find the entry file the builder produced and open it for the user.

        Preference order: a file literally named like a project entry point
        (index.html / main.py / ...), then any path engine mentioned in its
        reply. Returns a short confirmation for the reply, or None.
        """
        main_names = ("index.html", "home.html", "main.py", "app.py", "game.py",
                      "run.py", "main.js", "index.js", "app.exe", "main.exe")
        found = None
        try:
            files = sorted(glob.glob(os.path.join(project, "**", "*"),
                                     recursive=True))
        except Exception:
            files = []
        for f in files:
            if os.path.isfile(f) and f.lower().endswith(main_names):
                found = f
                break
        if not found:
            for m in re.findall(r"[A-Za-z]:\\[^\s\"']+\.(?:html|py|js|exe)",
                                reply or ""):
                if os.path.isfile(m):
                    found = m
                    break
        if not found:
            return None
        try:
            if found.lower().endswith((".html", ".htm")):
                os.startfile(found)  # noqa: S606
            else:
                subprocess.Popen([found], cwd=os.path.dirname(found))
            return f"Open kar diya: {found}"
        except Exception as exc:
            return f"Result yahan hai: {found} (khud se open nahi hua: {exc})"


    def _handle_shape_cmd(self, hl: str) -> Optional[str]:
        """'convert to heart', 'shape mein aa jao', 'star bana', 'reset'."""
        act = re.search(r"\b(shape|convert|morph|change|bana|ban do|kar do)\b", hl)
        shape_words = {
            "heart": ("heart", "dil"), "star": ("star", "tara", "sitaara"),
            "circle": ("circle", "gol", "gola", "round"),
            "square": ("square", "varg", "box"),
            "triangle": ("triangle", "trikona"),
            "diamond": ("diamond", "heera", "hira"),
            "flower": ("flower", "phool"),
        }
        reset = re.search(r"\b(reset|normal|wapis|face mein|original|hata)\b", hl)
        if reset and (act or re.search(r"\b(face|shape)\b", hl)):
            self._viz_custom_k = None
            self._viz_state("idle")
            return "Shape reset kiya — visualizer wapas face mein hai."
        if not act:
            return None
        for canon, words in shape_words.items():
            if any(w in hl for w in words):
                self._viz_custom(canon)
                return (f"Shape set: {canon}. Visualizer ab {canon} mein hai.")
        return "Kaunsi shape? Heart, star, circle, square, triangle, diamond, ya flower."

    # ── Paint generator (AK/paintgen.py) ───────────────────────────────────
    def _paint_dir(self) -> str:
        try:
            from AK import paintgen
            return paintgen.paint_dir(self.cfg.get("data_path") or r"D:\YOUR_DATA_PATH")
        except Exception:
            return os.path.join(self.cfg.get("data_path") or r"D:\YOUR_DATA_PATH",
                                "Builds", "paint")

    def _open_in_paint(self, path: str) -> bool:
        """Open a PNG in MS Paint (falls back to the default image viewer)."""
        for cmd in (["mspaint.exe", path], [os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                                        "System32", "mspaint.exe"), path]):
            try:
                subprocess.Popen(cmd)
                return True
            except Exception:
                continue
        try:
            os.startfile(path)  # noqa: S606
            return True
        except Exception:
            return False

    def _last_painting(self) -> Optional[str]:
        out = self._paint_dir()
        try:
            files = [os.path.join(out, f) for f in os.listdir(out)
                     if f.lower().endswith(".png")]
        except Exception:
            return None
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def _handle_paint_cmd(self, text: str) -> Optional[str]:
        """'ak sunset painting banao' -> real PNG artwork, opened in MS Paint.

        Pure PIL, fully offline: no GPU, no API key, no network. Files land in
        <data_path>/Builds/paint/ as timestamped PNGs and open in Paint so the
        user can keep editing them by hand.
        """
        t = (text or "").strip().lower()
        if not t:
            return None
        toks = t.split()
        if not toks:
            return None

        # Re-open / show commands (no new drawing).
        if re.search(r"\b(last|prev|previous|recent)\s+(painting|paintings|art|artwork|picture|image)\b", t) or \
           re.match(r"^(?:open\s+)?(?:last\s+)?(?:painting|paintings|art|artwork)\s*(?:kholo|open|dikha|dikhao|show)\b", t):
            last = self._last_painting()
            if not last:
                return ("Abhi koi painting nahi hai. Kuch aisa bolo jaise "
                        "'sunset painting banao'.")
            self._open_in_paint(last)
            return f"{os.path.basename(last)} Paint me khol diya."
        if re.search(r"\b(paintings?|artwork)\s+(folder|dir|directory)\b", t) or \
           re.search(r"\b(painting|paintings)\s+(list|dikha)\b", t):
            out = self._paint_dir()
            try:
                names = sorted(os.listdir(out))
            except Exception:
                names = []
            if not names:
                return f"Paintings folder khaali hai: {out}"
            if re.search(r"\b(folder|dir|directory)\b", t):
                try:
                    os.startfile(out)  # noqa: S606
                except Exception:
                    pass
                return f"Paintings folder: {out}"
            return ("Paintings: " + ", ".join(names[-6:]))

        # Drawing commands — this lane only exists when an ART word is present
        # ("sunset painting banao", "logo banao"). Without one, the request
        # belongs to the visualizer's shape lane, so "kutta banao" stays a
        # stardust shape and never becomes a PNG.
        if toks[0] in _PAINT_SKIP_HEADS:
            return None
        try:
            from AK import paintgen
        except Exception:
            return None
        if not _PAINT_ART_RE.search(t):
            return None
        # paintgen falls back to a mandala whenever it sees a paint word but no
        # subject, so "kya painting bana sakte ho" and a bare "painting" both
        # used to silently render one. Paint only when a subject is actually
        # named, or when a build verb asks for it and it is not a question.
        subj_hit = any(re.search(rf"\b{re.escape(w)}\b", t)
                       for w in paintgen.SUBJECT_WORDS)
        looks_like_question = bool(re.search(
            r"\b(kya|kyun|kaise|kab|kaun|kitna|kitni|can|could|do\s+you|does|"
            r"sak\w*|hota|hoti|ban\s*sakta|milk\s*sakta|pata)\b", t))
        if not subj_hit and (looks_like_question or not _has_build_verb(toks)):
            return ("Kya banau, boss? Jaise: dog painting banao, "
                    "sunset painting banao, ya logo banao.", True)
        spec = paintgen.detect_paint(t)
        if not spec:
            return None
        subject = spec.get("subject", "mandala")

        label = spec.get("nice", "art")
        self._viz_state("processing", f"painting {label}")
        print(f"[engine: paintgen] subject={subject} mono={spec.get('mono')}")
        try:
            path = paintgen.render_paint(spec, self._paint_dir())
        except Exception as exc:
            self._viz_state("idle")
            print(f"[paintgen] failed: {exc}")
            return f"Painting nahi ban payi: {exc}"
        self._viz_state("idle")
        opened = self._open_in_paint(path)
        shade = "monochrome" if spec.get("mono") else spec.get("palette", "colour")
        msg = f"{label} painting ban gayi ({shade}) — {os.path.basename(path)}"
        return msg + (" Paint me khol diya." if opened else " (open nahi hua).")

    def _vis_pid_alive(self) -> bool:
        """Is a visualizer already running (pid file + process still up)?

        The Popen handle alone is not enough: if the visualizer was started
        outside AK (or AK was restarted), AK would happily open a SECOND
        sand window. The visualizer drops a pid file, so we can check it.
        """
        try:
            path = os.path.join(self.cfg.get("data_path") or r"D:\YOUR_DATA_PATH",
                                "ak_vis.pid")
            if not os.path.isfile(path):
                return False
            with open(path, encoding="ascii") as fh:
                pid = int((fh.read() or "0").strip() or 0)
            if pid <= 0:
                return False
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259          # STILL_ACTIVE
        except Exception:
            return False

    def _start_visualizer(self) -> str:
        viz = getattr(self, "_viz", None)
        if viz is not None and viz.poll() is None:
            return "Visualizer is already running."
        if self._vis_pid_alive():
            return "Visualizer is already running."
        script = os.path.join(_HERE, "AK", "visualizer_py.py")
        if not os.path.isfile(script):
            return "Visualizer script (AK/visualizer_py.py) is missing."
        py = (self.cfg.get("visualizer", {}).get("python")
              or self.cfg.get("myvoice", {}).get("python")
              or sys.executable)
        if not os.path.isfile(py):
            return f"Visualizer python not found: {py}"
        try:
            self._viz = subprocess.Popen(
                [py, "-u", script],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            return f"Could not open visualizer: {exc}"
        self._viz_state("idle")
        return ("Visualizer opened - a small sand window (CPU/GPU-light). "
                "It reacts to your voice; click the ANIM button (or TAB) to "
                "switch the animation. Keys: 0/1-5 states, T theme, Q quit.")

    def _check_gesture_off(self) -> None:
        """The HUD's AK OFF button writes a flag - honour it: close the
        visualizer too so the whole visual stack is down."""
        flag = self._gesture_off_flag()
        try:
            if not os.path.isfile(flag):
                return
            os.remove(flag)
        except Exception:
            return
        viz = getattr(self, "_viz", None)
        if viz is not None and viz.poll() is None:
            try:
                self._stop_visualizer()
            except Exception:
                pass

    def _gesture_off_flag(self) -> str:
        try:
            from AK.config import get_data_dir
            base = get_data_dir()
        except Exception:
            base = (self.cfg.get("data_path")
                    or os.environ.get("AK_DATA_PATH")
                    or r"D:\YOUR_DATA_PATH")
        return os.path.join(base, "ak", "gesture_off.flag")

    def _gesture_off_watcher(self) -> None:
        """Background thread: the main loop can sit for minutes waiting for
        the wake word, so the AK OFF button is honoured by a watcher."""
        while self.running:
            time.sleep(0.4)
            try:
                self._check_gesture_off()
            except Exception:
                pass

    def _stop_visualizer(self) -> str:
        viz = getattr(self, "_viz", None)
        if viz is None or viz.poll() is not None:
            return "Visualizer is not running."
        try:
            viz.terminate()
        except Exception:
            pass
        self._viz = None
        return "Visualizer window closed."

    def _handle_visualizer_cmd(self, hl: str) -> Optional[str]:
        m = re.search(
            r"\b(start visualizer|open visualizer|visualizer on"
            r"|stop visualizer|close visualizer|visualizer off"
            r"|visualizer status)\b", hl)
        if not m:
            return None
        cmd = m.group(1)
        if cmd.startswith("stop") or cmd == "visualizer off":
            return self._stop_visualizer()
        if "status" in cmd:
            viz = getattr(self, "_viz", None)
            running = viz is not None and viz.poll() is None
            if running:
                return "Visualizer is running (sand window)."
            return ("Visualizer is not running. Say 'start visualizer' to "
                    "open the sand window.")
        if not m:
            return None

    # ── workflow learning: record -> .md playbook -> smart replay ──────
    _WF_START = re.compile(
        r"\b(seekho|sikhao|seekh\s*lo|sikha\s*do|record\s*karo|record\s*kar\s*do|"
        r"screen\s*record|task\s*record|yaad\s*rakh\w*)\b")
    _WF_STOP = re.compile(
        r"\b(record\s*(?:band|stop|khatam)|seekhna\s*(?:band|khatam)|"
        r"playbook\s*(?:bana|banao|save)|workflow\s*save|md\s*file\s*bana|"
        r"learning\s*(?:band|stop))\b")
    _WF_LIST = re.compile(
        r"\b(workflows?|playbooks?)\b.*\b(dikha|dikhao|batao|show|list|kya)\b|"
        r"\b(kya\s*kya\s*seekha|kya\s*seekha|seekhi\s*hui)\b")
    _WF_DELETE = re.compile(
        r"\b(workflow|playbook)\b.*\b(delete|hata|remove|khatam)\b")
    _WF_RUN = re.compile(
        r"\b(workflow|playbook)\b.*\b(chalao|chala\s*do|run|chalao\s*please)\b|"
        r"\b(kaam\s*kar\s*do|phir\s*se\s*kar\s*do|dobara\s*kar\s*do|"
        r"repeat\s*kar\s*do|usi\s*tareeke\s*se)\b")

    def _wf_coach(self):
        """Lazy workflow coach (record -> .md -> replay)."""
        if getattr(self, "coach", None) is None:
            try:
                from AK.workflow_coach import WorkflowCoach
                self.coach = WorkflowCoach(self)
            except Exception as exc:  # noqa: BLE001
                print(f"[wf] coach unavailable: {exc}")
                self.coach = False
        coach = self.coach or None
        if coach is not None:
            wcfg = self.cfg.get("workflow", {}) or {}
            coach.enabled = bool(wcfg.get("enabled", True))
            coach.region = wcfg.get("region", "focus")
            coach.fps = float(wcfg.get("fps", 1.5) or 1.5)
        return coach

    def _wf_auto_record(self) -> bool:
        return bool((self.cfg.get("workflow", {}) or {}).get("auto_record", True))

    def _wf_begin(self, task: str) -> bool:
        coach = self._wf_coach()
        if not coach:
            return False
        try:
            return bool(coach.begin(task))
        except Exception as exc:  # noqa: BLE001
            print(f"[wf] begin failed: {exc}")
            return False

    def _wf_step(self, title, kind="action", command="", detail=""):
        coach = self._wf_coach()
        if not coach or not coach.active():
            return
        try:
            coach.step(title, kind=kind, command=command, detail=detail)
        except Exception:
            pass

    def _wf_end(self, outcome="", reply="", min_steps=1):
        coach = self._wf_coach()
        if not coach:
            return None
        try:
            return coach.end(outcome=outcome, reply=reply, min_steps=min_steps)
        except Exception as exc:  # noqa: BLE001
            print(f"[wf] end failed: {exc}")
            return None

    def _wf_task_begin(self, text: str) -> bool:
        """Start recording a task lane so AK can learn what it just did.

        Only SPECIFIC, concrete task requests are recorded (never a plain
        chat, never a normal question like "what can you do"), and never
        while another session is already running.
        """
        if not self._wf_auto_record():
            return False
        try:
            if not self._specific_task(text):
                return False
        except Exception:
            return False
        t = (text or "").strip()
        if t.endswith("?") or len(t.split()) < 2:
            return False          # a question is not a task
        coach = self._wf_coach()
        if not coach or coach.active():
            return False
        ok = self._wf_begin(t[:120])
        if ok:
            self._wf_step(f"Task start: {(text or '')[:70]}", kind="action")
        return ok

    def _wf_task_end(self, text: str, reply: str):
        """Close an auto-recorded task lane and store the playbook."""
        coach = self._wf_coach()
        if not coach or not coach.active():
            return None
        # a one-liner is not worth a playbook - keep the store clean
        return self._wf_end(outcome="done", reply=str(reply)[:400],
                            min_steps=2)

    def _wf_run(self, wf, quiet=False):
        """Replay a learned playbook step by step."""
        from AK import workflow_learn
        say = (lambda t: None) if quiet else self._say
        try:
            report = workflow_learn.run_workflow(
                wf, log=lambda t: say(str(t)),
                stop_flag=lambda: (not self.running
                                   or getattr(self, "_interrupted_spoken", False)),
                launch_app=getattr(self.skills, "launch_app", None),
                open_site=getattr(self.skills, "open_site", None))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return report

    def _wf_match(self, task: str, threshold=0.6):
        """Find a learned workflow that matches this task (None = no match)."""
        coach = self._wf_coach()
        if not coach:
            return None
        try:
            hits = coach.store.search(task, limit=3)
        except Exception:
            return None
        if not hits:
            return None
        best = hits[0]
        q = workflow_learn_tokens(task)
        hay = workflow_learn_tokens(" ".join(
            [best.get("title", ""), best.get("task", ""),
             " ".join(best.get("tags") or [])]))
        if not q:
            return None
        overlap = q & hay
        score = len(overlap) / max(1, len(q))
        if best.get("task") and task.strip().lower() == best["task"].strip().lower():
            score += 1.0
        # a single loose word is not enough to hijack a normal request
        if len(overlap) < 2 and score < 1.0:
            return None
        return best if score >= threshold else None

    def _handle_workflow_cmd(self, hl: str) -> Optional[str]:
        """Voice commands for the self-teaching workflow feature."""
        t = (hl or "").lower().strip()
        if not t:
            return None
        coach = self._wf_coach()
        if not coach:
            return None
        store = coach.store

        # ── list learned workflows ──
        if self._WF_LIST.search(t):
            rows = store.list()
            if not rows:
                return ("Abhi koi workflow seekha hua nahi hai. Koi bhi kaam "
                        "karwao, main screen record karke steps seekh lunga.")
            lines = [f"{r.get('title')} ({r.get('steps')} steps, "
                     f"{r.get('created','')})" for r in rows[:6]]
            return f"Sir, seekhe hue workflows: " + "; ".join(lines) + "."

        # ── delete a workflow ──
        m = self._WF_DELETE.search(t)
        if m:
            name = re.sub(r"\b(workflow|playbook)\b", " ", t)
            name = re.sub(r"\b(delete|hata|remove|khatam|kar\s*do|do)\b", " ", name)
            name = re.sub(r"\s+", " ", name).strip()
            if not name:
                return "Kaunsa workflow delete karna hai? Naam bata do sir."
            ok = store.delete(name)
            return (f"{name} delete ho gaya." if ok else
                    f"{name} kahin store nahi mila sir.")

        # ── stop recording + save the playbook ──
        if self._WF_STOP.search(t):
            if not coach.active():
                return "Sir, abhi koi record chal nahi raha."
            res = coach.end(outcome="done")
            if not res or not res.get("ok"):
                return "Sir, playbook save nahi hua. Dobara try karte hain."
            return (f"Sir, seekh liya. {res['steps']} steps ka workflow ban gaya "
                    f"({res['duration']} second). Agli baar main yeh kaam khud "
                    f"steps follow karke kar dunga. File: {res['path']}")

        # ── start recording a task ──
        if self._WF_START.search(t) and not self._WF_LIST.search(t):
            task = re.sub(self._WF_START, " ", t)
            task = re.sub(r"\b(ak|please|sir|ab|abhi|ko)\b", " ", task)
            task = re.sub(r"\s+", " ", task).strip() or "general task"
            if coach.active():
                return "Sir, pehla record chal raha hai. Pehle use band kar do."
            if self._wf_begin(task):
                return (f"Sir, record shuru. Screen record kar raha hun, steps "
                        f"bhi note kar raha hun. Kaam karne ke baad 'record band' "
                        f"bol dena.")
            return "Sir, record start nahi hua."

        # ── run a learned workflow ──
        if self._WF_RUN.search(t):
            query = re.sub(self._WF_RUN, " ", t)
            query = re.sub(r"\b(ak|please|sir|ab|abhi|ko|kaam|kar|do|phir|se|"
                           r"dobara|repeat|usi|tareeke|wala|ka|ki|ko)\b", " ",
                           query)
            query = re.sub(r"\s+", " ", query).strip()
            wf = None
            if query:
                hits = store.search(query, limit=1)
                if hits:
                    wf = self._wf_match(query, threshold=0.3) or hits[0]
            if not wf:
                return ("Sir, koi seekha hua workflow nahi mila. Pehle wo kaam "
                        "ek baar karwao, main record karke seekh lunga.")
            self._say(f"Sir, {wf.get('title')} ka seekha hua workflow chala raha hun.")
            report = self._wf_run(wf)
            if report.get("ok"):
                return (f"Sir, {wf.get('title')} ho gaya. {len(report.get('ran') or [])} "
                        f"steps follow kiye.")
            failed = report.get("failed") or []
            detail = failed[0].get("title") if failed else "kuch step"
            return (f"Sir, {len(failed)} step fail hue ({detail}). Baaki steps "
                    f"chhod diye.")

        return None

    def greet(self):
        engine = getattr(self.engine, "version", "") or "AK"
        print(f"[AK] Online. Chat brain: {self.brain.backend} | Task engine: {engine}")
        preferred = self.cfg.get("voice", {}).get("preferred_voice", "")
        if not preferred:
            if not self._asleep:
                self._pick_voice_interactive()
        if os.environ.get("AK_DEBUG_VOICE"):
            print(f"[voice] Using: {self.speaker.current_voice_name}")

    def run(self):
        self.greet()
        # Let the user pick the coding/task engine for this session (local vs
        # fast). The other engine can still be pulled in mid-session if needed.
        try:
            self._choose_task_backend()
        except Exception as exc:  # noqa: BLE001
            print(f"[AK] task engine choice skipped ({exc})")
        # Report anything learned since the last session.
        try:
            anns = self.self_learn.pending_announcements()
            if anns:
                self._viz_state("processing")
                self._say("Sir, " + "; ".join(anns[:3]) + ".")
        except Exception:
            pass
        if self.cfg.get("visualizer", {}).get("enabled"):
            self._start_visualizer()
        # the AK OFF button must work even while we wait for the wake word
        self._off_watcher = threading.Thread(target=self._gesture_off_watcher,
                                             daemon=True)
        self._off_watcher.start()
        self._setup_voice_lock()
        self._setup_face_lock()
        wake_phrases = WAKE_PHRASES
        sleep_phrases = SLEEP_PHRASES

        if not self.require_wake:
            self._asleep = False
            self._say("Always listening. Just speak.")
        else:
            print("[AK] Running in background. Say 'ak' to activate.")
            self._say("")  # silent indicator that I'm here

        while self.running:
            try:
                # ── the HUD's AK OFF button asks for a full visual shutdown ─
                self._check_gesture_off()
                # ── Get input ──────────────────────────────────────────
                self._viz_state(getattr(self, "_viz_custom_k", None) or "idle")
                text = ""
                if self.chat_mode:
                    heard = input("\nYou: ")
                    if not heard:
                        continue
                    text = heard.strip()
                    try:
                        if text:
                            _ws_broadcast("user", text)
                    except Exception:
                        pass
                elif self._asleep:
                    # Background mode — wake word OR finger-snap (snap disabled
                    # if the user turned it off, since it auto-woke before)
                    snap_cfg = self.cfg.get("snap_detection", self.cfg.get("slap_detection", {}))
                    snap_enabled = snap_cfg.get("enabled", False)
                    snap_threshold = snap_cfg.get("peak", snap_cfg.get("threshold", 18000))
                    snap_ratio = snap_cfg.get("ratio", 18)
                    if snap_enabled:
                        prompt = _SLEEP_PROMPT_SNAP
                    else:
                        prompt = _SLEEP_PROMPT_PLAIN
                    self._viz_listening()
                    heard = self.listener.wait_for_wake(
                        prompt, snap_threshold, WAKE_PHRASES, snap_enabled,
                        snap_ratio, self._verifier)
                    if heard is None:
                        continue
                    # ── Slap detected ──
                    if heard == "__SLAP__":
                        self._asleep = False
                        hl = ""  # no spoken text
                        self._say("Radhe Radhe! I'm awake and ready.")
                        should, reason = should_offer_mood_shloka()
                        if should:
                            self._mood_offered = True
                            self._say("Would you like to listen to a shloka of Bhagavad Gita according to your mood?")
                        continue
                    hl = heard.lower().strip()
                    quit_state = self._quit_command(hl)
                    if quit_state == "yes":
                        self._say(f"{self.name} signing off. Goodbye!")
                        self.running = False
                        break
                    if quit_state == "needs_wake":
                        print("[AK] 'exit' bina wake word ke ignore hua - band "
                              "karne ke liye 'ak exit' bolo.")
                        continue
                    # Check for wake phrase
                    if self._wake_match(hl, wake_phrases):
                        self._asleep = False
                        rest = self._command_after_wake(heard, wake_phrases)
                        if rest and not self._wake_match(rest.lower(), wake_phrases):
                            hud = self._hud_wake_command(rest)
                            if hud is not None:
                                reply, speak = hud
                                if reply and speak:
                                    try:
                                        _ws_broadcast("assistant", reply)
                                    except Exception:
                                        pass
                                    self._say(reply)
                                continue
                            text = rest
                            try:
                                if text:
                                    _ws_broadcast("user", text)
                            except Exception:
                                pass
                        if not text:
                            self._say("Radhe Radhe! I'm awake and ready.")
                            should, reason = should_offer_mood_shloka()
                            if should:
                                self._mood_offered = True
                                self._say("Would you like to listen to a shloka of Bhagavad Gita according to your mood?")
                            continue
                else:
                    # Awake mode — listen for commands
                    prompt = "[AK] Listening..."
                if self._voice_fails >= 3:
                    prompt = "[AK] Voice not detected. Press Enter to type, or keep speaking"
                self._viz_listening()
                heard = self.listener.listen(prompt)
                if heard is None:
                    self._voice_fails += 1
                    if self._voice_fails >= 5:
                        self._voice_fails = 0
                        try:
                            typed = input("[AK] Type your command (or Enter to skip): ").strip()
                            if typed:
                                text = typed
                        except (EOFError, KeyboardInterrupt):
                            pass
                    continue
                self._voice_fails = 0
                text = heard.strip()
                self._viz_listening(text)
                try:
                    if text:
                        _ws_broadcast("user", text)
                except Exception:
                    pass

                if not text:
                    continue

                # ── Check for sleep / exit ─────────────────────────────
                hl = text.lower().strip()

                quit_state = self._quit_command(hl)
                if quit_state == "yes":
                    self._say(f"{self.name} signing off. Goodbye!")
                    self.running = False
                    break
                if quit_state == "needs_wake":
                    # a bare "exit" in always-listening mode is usually a false
                    # transcript (a cough, a fan) - it must not kill AK
                    print("[AK] 'exit' bina wake word ke ignore hua - band karne "
                          "ke liye 'ak exit' bolo.")
                    continue

                # Sleep / background commands (only when awake)
                if not self._asleep:
                    if any(hl == p or hl.startswith(p) for p in sleep_phrases):
                        self._asleep = True
                        print("[AK] Going to background. Say 'ak' to wake me.")
                        continue

                if "close chat" in hl and self.chat_mode:
                    self.chat_mode = False
                    continue

                # ── Voice lock commands (train / enable / disable) ────
                vlock = self._handle_voice_lock_cmd(hl)
                if vlock:
                    self._say(vlock)
                    continue

                # ── Face lock commands (register face / enable / disable / status) ──
                flock = self._handle_face_lock_cmd(hl)
                if flock:
                    self._say(flock)
                    continue

                # ── My Voice commands (record / status / use / clear) ──
                myv = self._handle_myvoice_cmd(hl)
                if myv:
                    self._say(myv)
                    continue

                # ── My Face commands (register / remove / status) ──
                myf = self._handle_myface_cmd(hl)
                if myf:
                    self._say(myf)
                    continue

                # ── Camera watch commands (kaun saamne hai) ─────────
                camwatch = self._handle_camera_watch_cmd(hl)
                if camwatch:
                    self._say(camwatch)
                    continue

                # ── Face Intel commands (chehra -> online profile) ──
                fintel = self._handle_face_intel_cmd(hl)
                if fintel:
                    self._say(fintel)
                    continue

                # ── Visualizer commands (start / stop / status) ─────────
                viz = self._handle_visualizer_cmd(hl)
                if viz:
                    self._say(viz)
                    continue

                # ── Visualizer shape commands (convert / reset) ──────
                sh = self._handle_shape_cmd(hl)
                if sh:
                    self._say(sh)
                    continue

                # ── Workflow learning (record / list / run / delete) ──
                wf = self._handle_workflow_cmd(hl)
                if wf:
                    self._say(wf)
                    continue

                # ── Process command (local-first, ask before engine) ────
                reply = self.respond(text, can_ask=True)
                if reply:
                    try:
                        _ws_broadcast("assistant", reply)
                    except Exception:
                        pass
                    self._say(reply)
                # 'Naya seekha' notes sirf JAWAB KE BAAD boli jaati hain —
                # pehle nahi, taaki reply ka koi wait na ho.
                self._speak_learn_notes()
                continue

            except KeyboardInterrupt:
                self._say("Goodbye!")
                self.running = False
                break

            except Exception as exc:  # noqa: BLE001
                # Never let a single command bug stop AK.
                print(f"[AK] recovered from an error: {exc}")
                try:
                    self._viz_state("idle")
                except Exception:
                    pass
                continue


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="AK Voice Assistant - Created by Anmol Tyagi")
    parser.add_argument("--config", "-c", default="config.json",
                        help="Path to config.json")
    parser.add_argument("--no-wake", action="store_true",
                        help="Disable wake word; listen continuously")
    parser.add_argument("--text", "--typing", action="store_true",
                        help="Force keyboard-only mode (no microphone needed)")
    parser.add_argument("--once", type=str, nargs="?", const="stdin",
                        help="Process one command and exit. Reads from arg or stdin.")
    parser.add_argument("--voice-once", action="store_true",
                        help="Capture one voice command, respond, and exit.")
    parser.add_argument("--ak-task", "--engine", dest="engine", default="",
                        help="Send a task to the AK engine and exit. Use --ak-task 'task description'")
    parser.add_argument("--gesture", action="store_true",
                        help="Start the holographic gesture control together with AK")
    args = parser.parse_args()

    ak = AK(str(args.config) if args.config else os.path.join(_HERE, "config.json"))
    cfg = ak.cfg
    # ── holographic gesture control ─────────────────────────────────────
    # The default launcher brings both up together: the voice assistant AND
    # the gesture control. Without this the assistant used to start alone and
    # the HUD had to be asked for separately ("ak chalao").
    gesture = bool(args.gesture) or bool(
        (cfg.get("gesture_control") or {}).get("autostart"))
    if gesture:
        try:
            from AK import ak_commands

            print("[ak] holographic gesture control starting...")
            if ak_commands.start_hud(ak):
                print("[ak] gesture control live - point to aim, pinch to click.")
            else:
                print("[ak] gesture control could not start (camera / models).")
        except Exception as exc:  # noqa: BLE001
            print(f"[ak] gesture control error: {exc}")
    if args.no_wake:
        cfg["require_wake_word"] = False
        ak.require_wake = False
    if args.text:
        ak.typing_mode = True
        ak.listener.set_engine("text")
    if args.engine:
        task = args.engine.strip()
        if ak.engine.available:
            print(ak.engine.execute_task(task))
        else:
            print("AK task engine is not installed.")
        return
    if args.once is not None:
        text = args.once
        if text == "stdin":
            text = sys.stdin.read().strip()
        if not text:
            print("No task provided.")
            return
        reply = ak.respond(text)
        print(reply)
        return
    if args.voice_once:
        print("Listening (Ctrl+Alt+J)...")
        heard = False
        for attempt in range(3):
            text = ak.listener.listen()
            if not text:
                print("No voice heard (attempt %d/3)." % (attempt + 1))
                continue
            heard = True
            reply = ak.respond(text)
            ak._say(reply)
            break
        if not heard:
            print("Type your command (or press Enter to exit): ", end="")
            text = input().strip()
            if text:
                ak.respond(text)
            else:
                print("\nPress Enter to exit...", end="")
                input()
        return
    try:
        ak.run()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    finally:
        # the gesture control is AK's child - it goes down with it
        if gesture:
            try:
                from AK import ak_commands

                ak_commands.stop_hud(ak)
                print("[ak] gesture control stopped.")
            except Exception:
                pass


if __name__ == "__main__":
    main()
