"""Whisper mishearing corrector — the words AK cares about most.

Whisper transcribes sound, not spelling, so a handful of high-value words come
back as phonetic garbage. "Radhe Radhe" lands as "Radera de", and since no
greeting regex can match that, the utterance fell past every local handler and
shipped to engine (15-35 s) instead of answering instantly. Voice names break
the same way — a spoken voice name comes back slightly wrong and the switch fails.

This module rewrites the known-bad transcripts back to the spelling AK actually
matches, BEFORE routing. Every rule here is word-bounded and paired with a
blocklist of real English words it must never touch, so a normal sentence is
left alone.

Deliberately small: only phrases that (a) are said often, (b) are short, and
(c) are matched literally downstream. Broad fuzzy rewriting does more harm than
good — it silently changes what the user said.
"""

from __future__ import annotations

import re

# Real English words that begin like the patterns below. If one of these shows
# up, the rule is skipped so "radio" never becomes a greeting.
_GUARD = (
    "radio|radi|radius|radian|radians|radar|radish|rabri|rada|radiology|"
    "radically|gradient|graded|grade|trade|credit|debit|ready|radar"
)


def _rule(pattern: str, replacement: str) -> tuple[re.Pattern, str]:
    """Compile one correction rule, keeping its replacement alongside it."""
    return re.compile(pattern, re.IGNORECASE), replacement


# ── "Radhe Radhe" ─────────────────────────────────────────────────────────
# Whisper splits the two-syllable phrase and lands on /rædərə de/, /rædhərə de/,
# /rædheɪ rædheɪ/ and friends. The first token is always "rad" + a short tail
# that varies with the accent, so match the tail loosely and let the second
# token be either another "rad…" or the way "radhe" collapses to "de".
_GREET_RAD = r"rad[a-z]{0,6}"
_RULES: list[tuple[re.Pattern, str]] = [
    # "radera de", "radhera de", "radhara de", "rader radhe", "radhey radhey"
    _rule(rf"\b(?!{_GUARD}\b){_GREET_RAD}\s+(?:{_GREET_RAD}|de|dee|dh|dhe|d)\b",
          "radhe radhe"),
]

# ── everyday Hinglish Whisper gets wrong ──────────────────────────────────
# Voice names are deliberately NOT corrected here. A near-miss is right when
# you are switching voices and wrong when you are talking about a person, so
# that fix lives in Speaker.switch_voice() where the intent is already known.
_MISC_RULES: list[tuple[re.Pattern, str]] = [
    # "kya" is the single most common Hinglish token; it comes back as
    # "kiya"/"kyaa"/"kyah" far more often than it should.
    _rule(r"\bkiya\b|\bkyaa\b|\bkyah\b", "kya"),
    # "banao" -> "bano"/"banau"; the build verb the drawing lanes key on.
    _rule(r"\bbanau\b|\bbanoo\b|\bbano\b", "banao"),
    # "kaise" -> "kaisay"/"kaiseh"
    _rule(r"\bkaisay\b|\bkaiseh\b", "kaise"),
    # "dhanyavad" spelling drift
    _rule(r"\bdhanywaad\b", "dhanyavad"),
    # "write" is a classic homophone miss ("white"/"right"/"rite"). Only
    # rewritten when it is clearly the writing verb — i.e. followed by an
    # optional article and a writing/coding noun — so "right now" and "white
    # colour" are never touched.
    _rule(r"\b(?:white|right|rite)\b(?=\s+(?:(?:a|an|the|me|us|this|that)\s+)?"
          r"(?:code|script|program|programme|function|app|website|web\s?page|"
          r"email|letter|essay|poem|story|report|note|test|file|number|"
          r"algorithm|python|java|javascript|html|css)\b)", "write"),
    # ── tech/product homophones STT mangles (all word-bounded) ──
    _rule(r"\bpie\s?thon\b|\bpi\s?thon\b|\bpy\s?thon\b", "python"),
    _rule(r"\b(?:vs|v\.?\s?s\.?)\s*(?:code|cord|quote)\b", "vscode"),
    _rule(r"\b(?:git|get)\s*hub\b", "github"),
    _rule(r"\bjava\s+script\b", "javascript"),
    _rule(r"\btype\s+script\b", "typescript"),
    _rule(r"\bnode\s*(?:js|j\.?\s?s|gs)\b", "node.js"),
    # only in an obvious data/coding context, so the name "Jason" is safe
    _rule(r"\bjason\b(?=\s+(?:file|data|format|object|array|response|api|"
          r"config|string|parse))", "json"),
    # only before "notebook", so the planet Jupiter is safe
    _rule(r"\bjup[iy]t?er\b(?=\s+notebook)", "jupyter"),
    # "get status/log/..." at a git-command verb is a mis-heard "git"
    _rule(r"\bget\b(?=\s+(?:status|log|diff|branch|commit|push|pull|clone|"
          r"add|checkout|merge|rebase)\b)", "git"),
]


# ── Music: a garbled artist name is the difference between a song and
#    silence ────────────────────────────────────────────────────────────────
# Two separate failures were killing every "play <artist> song" command:
#
#   1. the ARTIST came back wrong — "argyth", "adjit", "rejeet" — so YouTube
#      was searched for nonsense and found nothing;
#   2. the word PLAY itself was dropped ("adjit sing songs"), the music regex
#      (which needs play/listen to/search) matched nothing, and the utterance
#      fell all the way through to engine instead of starting a song.
#
# The first one is fixed at the source in AK/stt.py (hotwords bias the decoder
# toward the real names). What is left is repaired here, gated on MUSIC INTENT
# so a normal sentence is never touched:
#   * every alias below is a NON-WORD — "argyth" cannot be a real word, so a
#     word-bounded rewrite has no false positives. Deliberately NOT fuzzy
#     matching: difflib maps "song" -> "sonu" and "single" -> "singh", which
#     would rewrite the one word that must never change;
#   * "play" is put back when a song was clearly requested but the verb was
#     lost, and only for short, non-question utterances.
_ARTIST_RULES: list[tuple[re.Pattern, str]] = [
    _rule(r"\b(?:argyth|argit|argeet|arigee|arjeet|adjeet|arejet|rejeet|"
          r"regipe|aridji|adjit|arjit|arijith|arjith)\b", "arijit"),
    # "arijit single songs" / "arijit sing songs" — the surname collapsed into
    # a real English word, so it is only safe next to the artist himself.
    _rule(r"\b(arijit)\s+(?:single|sing)\b", r"\1 singh"),
]

# Words that say "this is about a song".
_MUSIC_NOUN = re.compile(
    r"\b(songs?|gaana|gaane|gaano|music|album|playlist)\b", re.IGNORECASE)

# An English play verb already present -> the request is intact, nothing to do.
_EN_PLAY_VERB = re.compile(r"\b(play|listen\s+to|search)\b", re.IGNORECASE)

# Hinglish play verbs. These do NOT match the music regex in main.py, which
# only knows play/listen to/search — so "gaana chalao" used to sail past every
# handler and land in smalltalk's canned reply. They are stripped off the
# query and replaced by a real "play".
_HINDI_PLAY_VERB = re.compile(
    r"\s+(?:chalao|chala\s+do|chla\s+do|chlao|bajao|baja\s+do|laga\s+do|"
    r"sunaao|sunao|suno|suna|kar\s+do|karo)\s*$", re.IGNORECASE)

# A question is never a play request, however musical it sounds: "arijit singh
# song kaunsa hai" must stay a question and not turn into a YouTube search.
_MUSIC_QUESTION = re.compile(
    r"\b(kya|kaun|kaunsa|kaunsi|kaun\s*si|kon|konsa|konsi|kaise|kyun|kyu|kab|"
    r"kahan|kitna|kitni|kitne|meaning|matlab|batao|bata|who|what|which|why|"
    r"when|where|how|whose|whom)\b", re.IGNORECASE)


def repair_music(text: str) -> str:
    """Repair a music request Whisper mangled — returns the rewritten text.

    Runs AFTER `correct()` and only ever changes the text; no other state.
    All three jobs are gated on music intent, so a normal sentence is never
    touched:

      * rewrite known-bad artist spellings (word-bounded, non-words only),
      * put a dropped "play" back so the music handler can see the request,
      * turn a trailing Hinglish play verb into a real "play" prefix.

    Returns `text` unchanged when it is not a music request at all.
    """
    if not text:
        return text
    out = text
    for rx, repl in _ARTIST_RULES:
        out = rx.sub(repl, out)

    low = out.lower()
    # Nothing musical in it, or the verb is already there -> leave it alone.
    if not _MUSIC_NOUN.search(low) or _EN_PLAY_VERB.search(low):
        return out
    # "what/kaun … song" is a question, not a command.
    if "?" in out or _MUSIC_QUESTION.search(low):
        return out
    # Long sentences are conversation, not commands; only short utterances
    # like "arijit singh song" or "adjit sing songs" get the verb restored.
    if len(low.split()) > 8:
        return out
    return "play " + _HINDI_PLAY_VERB.sub("", out.strip()).strip()




def correct(text: str) -> str:
    """Return `text` with known Whisper mishearings rewritten.

    Only the text is changed; no other state. Safe to call on every utterance.
    """
    if not text:
        return text
    out = text
    for rx, repl in _RULES + _MISC_RULES:
        out = rx.sub(repl, out)
    return out


def changed(original: str, fixed: str) -> bool:
    """True when correction actually rewrote something (for logging)."""
    return (original or "").strip() != (fixed or "").strip()
