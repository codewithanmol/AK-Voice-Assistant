"""Hinglish command router - the local features, in the language you speak.

Most of AK's skill handlers were written English-first ("notes list",
"calculate 12*8", "convert 5 km to miles"). Spoken Hinglish falls through all
of them and lands on engine, which takes 15-35 s and pegs the CPU. This
module sits in front of those handlers and understands the everyday phrasings,
so the local answer wins and nothing leaves the machine.

Returns (reply, True) when it handled the command, None to let the normal
handlers try.
"""

import re

# ── Hinglish maths ────────────────────────────────────────────────────────
_MATH_WORDS = [
    (r"\bplus\b|\bjod(?:o|do|na|ne)?\b|\bjodh\b|\badd\b|\bad(?:d|i)\b", "+"),
    (r"\bminus\b|\bminus\s*karke\b|\bghata(?:o|do)?\b|\bsubtract\b|\bhat(?:o|ao)\b", "-"),
    (r"\bmulti(?:ply)?\b|\btimes\b|\bstar\b|\b guna\b|\bzaroor\b", "*"),
    (r"\bdivi(?:de|ded)\b|\bdivide\b|\bbhag(?:o|ta)\b", "/"),
    (r"\bmod(?:ulo)?\b|\bmodulus\b", "%"),
    (r"\bpower\b|\bexponent\b", "**"),
]

_NUM = r"\d+(?:\.\d+)?"


def _to_expr(text: str):
    """'12*8 karo' / '50 plus 20' / '100 mein se 30 minus' -> '100-30'."""
    t = text.lower()
    # "100 mein se 30" is subtraction with the operands in reverse order, so it
    # has to be rewritten BEFORE the verb pass — by then the number words are
    # already gone and only two loose digits are left.
    t = re.sub(rf"({_NUM})\s*(?:mein|me|in)?\s*se\s*({_NUM})", r"\1 - \2", t)
    # drop the polite wrappers so they don't confuse the patterns
    t = re.sub(r"\b(mein se|me se|se)\b", " ", t)
    for pattern, sym in _MATH_WORDS:
        t = re.sub(pattern, f" {sym} ", t)
    t = re.sub(r"\b(aur)\b", " + ", t)
    t = t.replace("×", "*").replace("÷", "/").replace("^", "**")
    if not re.search(r"[\+\-\*/%]|\*\*", t):
        return None
    if not re.search(r"\d", t):
        return None
    # keep only numbers, operators, dots and spaces
    expr = re.sub(r"[^0-9\.\+\-\*\/\(\)\s%]", " ", t)
    expr = re.sub(r"\s+", " ", expr).strip()
    expr = re.sub(r"\s*([\+\-\*\/%])\s*", r"\1", expr)
    # drop a dangling operator ("12 +")
    expr = re.sub(r"[\+\-\*\/%]+$", "", expr).strip()
    if not re.search(r"\d", expr) or not re.search(r"[\+\-\*/%]", expr):
        return None
    return expr


# ── units ────────────────────────────────────────────────────────────────
# NB: the trailing (?![a-z]) on every unit is load-bearing. Without it "5 km ko
# miles me" matches the bare "m" of "miles" and answers "5 km is 5000 m."
_UNIT_WORDS = (r"(?:km|kilometers?|kilometres?|meters?|metres?|m|cm|mm|inch|"
               r"inches|in|ft|feet|foot|yards?|yd|miles?|mi|kg|kilos?|kilograms?|"
               r"grams?|g|pounds?|lbs|oz|ounces?|tonnes?|tons?|kb|mb|gb|tb|bytes?|"
               r"celsius|centigrade|fahrenheit|kelvin|deg\s*c|deg\s*f|°c|°f|"
               r"\bc\b|\bf\b|\bk\b)(?![a-z])")
_UNIT_JOIN = r"(?:ko|in|into|to|me|mein|mein\s*me|se|badlo|badal|convert|kar|kar\s*do)"


def _units(text: str):
    """'5 km ko miles me' / '70 kg in pounds' -> (5.0, 'km', 'miles')."""
    m = re.search(rf"({_NUM})\s*({_UNIT_WORDS})\s*{_UNIT_JOIN}\s*({_UNIT_WORDS})",
                  text, re.I)
    if not m:
        return None
    src, dst = m.group(2).strip().lower(), m.group(3).strip().lower()
    if src == dst:
        return None
    return float(m.group(1)), src, dst


# ── percentage ───────────────────────────────────────────────────────────
def _percent(text: str):
    """'500 ka 15 percent' / '15 percent of 500' / '500 par 20%' -> 75.0.

    Percentage has no arithmetic symbol to key off, so it gets its own parser
    instead of being crammed into the + - * / expression builder.
    """
    t = text.lower()
    # "<base> ka/ki/ke/par/pe <pct> percent"  (Hinglish word order)
    m = re.search(rf"({_NUM})\s*(?:ka|ki|ke|par|pe|of|out\s+of)\s*"
                  rf"({_NUM})\s*(?:%|percent|per\s*cent|pr\s*shat)", t)
    if m:
        base, pct = float(m.group(1)), float(m.group(2))
        return base * pct / 100.0, pct, base
    # "<pct> percent of <base>"  (English word order)
    m = re.search(rf"({_NUM})\s*(?:%|percent|per\s*cent)\s*(?:of|ka|ki|ke)\s*"
                  rf"({_NUM})", t)
    if m:
        pct, base = float(m.group(1)), float(m.group(2))
        return base * pct / 100.0, pct, base
    return None


# ── password / pin ───────────────────────────────────────────────────────
def _password(text: str):
    """'8 digit ka password' / '4 digit pin' -> ('password', 8) / ('pin', 4).

    Returns (kind, length) or None. A PIN is numeric, so asking for one must
    never fall through to the password generator — "4 digit pin" used to come
    back as a 6-character password with symbols in it.
    """
    if not re.search(r"\b(password|passwd|pass\s*word|pin)\b", text, re.I):
        return None
    if not re.search(r"\b(bana|banao|banado|banaye|generate|g|banao|do|kar|suggest|"
                     r"banaye|chahiye)\b", text, re.I):
        return None
    m = re.search(rf"({_NUM})\s*(?:character|char|length|digit|number|ank)", text, re.I)
    n = int(float(m.group(1))) if m else None
    if re.search(r"\bpin\b", text, re.I):
        return ("pin", n if n is not None else 6)
    return ("password", n)


# ── reminders ────────────────────────────────────────────────────────────
_SEC_UNITS = {
    "sec": 1, "secs": 1, "second": 1, "seconds": 1, "s": 1,
    "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "ghanta": 3600, "ghante": 3600, "ghantey": 3600, "hour": 3600, "hours": 3600,
    "din": 86400, "day": 86400, "days": 86400,
}


def _clean_task(task: str) -> str:
    """Drop the filler that trails a spoken reminder task.

    "5 din baad exam yaad dilana" -> the task is "exam", not "exam yaad dilana".
    """
    t = (task or "").strip(" ,.?!")
    t = re.sub(r"\s*\b(?:yaad\s*dila\w*|dila\w*|dena|dijaiye|batana|"
               r"lagao|laga\s*d\w*|set\s*karo|kar\s*d\w*)\s*$", "", t, flags=re.I)
    return t.strip(" ,.!?")


def _reminder(text: str):
    """'10 minute baad chai peene ko reminder lagao' -> (task, seconds).

    The English handler only knows "remind me to X in N minutes", which nobody
    actually says out loud. Spoken Hinglish puts the duration first and marks
    the boundary with "baad", so it gets its own pattern.
    """
    if not re.search(r"\b(reminder|remind|yaad|yaad dila|reminder set|alarm)\b", text, re.I):
        return None
    # a bare "reminder list / yaad dikha" is a status question, not an add
    if re.search(r"\b(dikhao|dikha|batao|bata|list|show|kya hai|status|"
                 r"pending|kya kya)\b", text, re.I):
        return None

    units = "|".join(sorted(_SEC_UNITS, key=len, reverse=True))
    # "<N> <unit> baad <task>"  — duration first, boundary word after it
    m = re.search(rf"({_NUM})\s*({units})\s*(?:mein\s*)?(?:baad|bad|after|ke\s*baad)\s+(.+)$",
                  text, re.I)
    if m:
        secs = int(float(m.group(1)) * _SEC_UNITS[m.group(2).lower()])
        return _clean_task(m.group(3)), secs
    # "baad <N> <unit> <task>" — boundary word before the duration
    m = re.search(rf"baad\s+({_NUM})\s*({units})\s+(.+)$", text, re.I)
    if m:
        secs = int(float(m.group(1)) * _SEC_UNITS[m.group(2).lower()])
        return _clean_task(m.group(3)), secs
    return None


# ── entry point ──────────────────────────────────────────────────────────
def match(ak, text: str):
    """Handle everyday Hinglish locally. Returns (reply, True) or None."""
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()

    # --- identity: "mai kaun hun", "mera naam kya hai" ---
    if re.search(r"\b(mai|main|hum|humme|humko)\s+(kaun|kis)\b", low) or \
            re.search(r"\b(mera|meri|hamara)\s+(naam|name|identity)\b", low):
        try:
            from AK.profile import answer_who_am_i
            return (answer_who_am_i(), True)
        except Exception:  # noqa: BLE001
            return None

    # --- memory: "mujhe kya yaad hai", "meri yaad dikha" ---
    if re.search(r"\b(kya\s+yaad|yaad\s+(hai|dikha|bata)|mujhe\s+yaad|meri\s+yaad|"
                 r"mera\s+memory|memory\s+(dikha|bata|show)|kya\s+yaad\s+hai)\b", low):
        msg, _ = ak.skills.memory_list()
        return (msg, True)

    # --- notes: "notes dikhao", "note list karo" ---
    if re.search(r"\bnotes?\b", low) and re.search(
            r"\b(dikhao|dikhawao|dikha|dekhao|list|batao|show|kholo|open)\b", low) and \
            not re.search(r"\b(add|likh|delete|remove|search|tag)\b", low):
        msg, _ = ak.skills.note_list()
        return (msg, True)

    # --- voices (plural too) ---
    if re.search(r"\b(voices?|speakers?)\b", low) and re.search(
            r"\b(kaun|kitni|kitne|list|naam|name|dikhao|batao|show|available)\b", low):
        return (ak.speaker.list_voices(), True)

    # --- shayari / fact / motivation ---
    if re.search(r"\b(shayari|sher|poem|kavita|ghazal|couplet)\b", low):
        try:
            out = ak.smalltalk.answer(t)
            if out:
                return (out, True)
        except Exception:  # noqa: BLE001
            pass
    if re.search(r"\b(fact|trivia|interesting)\b", low):
        try:
            out = ak.smalltalk.answer(t)
            if out:
                return (out, True)
        except Exception:  # noqa: BLE001
            pass
    if re.search(r"\b(motivation|motivat\w*|encouragement|hosla)\b", low):
        try:
            out = ak.smalltalk.answer(t)
            if out:
                return (out, True)
        except Exception:  # noqa: BLE001
            pass

    # --- units ---
    got = _units(low)
    if got:
        value, fu, tu = got
        try:
            from AK.dictionary import convert_units
            reply, _data = convert_units(value, fu, tu)
            return (reply, True)
        except Exception:  # noqa: BLE001
            pass

    # --- password / pin ---
    want = _password(low)
    if want:
        kind, n = want
        if kind == "pin":
            # pin_generate() is the numeric one; password_pin() never existed,
            # so the old "pin" branch died on an AttributeError that the caller
            # swallowed into a silent fall-through.
            gen = getattr(ak.skills, "pin_generate", None)
            if gen is None:
                return None
            msg, _ = gen(max(4, min(10, n or 6)))
            return (msg, True)
        msg, _ = ak.skills.password_generate(max(6, min(64, n or 16)))
        return (msg, True)

    # --- reminders ---
    rem = _reminder(low)
    if rem:
        task, secs = rem
        if task:
            msg, _ = ak.skills.reminder_add(task, secs)
            return (msg, True)

    # --- percentage ---
    pct = _percent(low)
    if pct:
        value, pct_n, base = pct
        # %.4g already trims noise; only strip a trailing "0" when it is
        # trailing a decimal point, or 70 would become 7.
        shown = f"{value:.4g}"
        if "." in shown:
            shown = shown.rstrip("0").rstrip(".")
        return (f"{base:g} ka {pct_n:g}% = {shown}.", True)

    # --- maths, Hinglish style ---
    if re.search(r"\b(calculate|calc|hisaab|hisab|ganana|ganana)\b", low) or \
            re.search(r"[\d]\s*[\+\-\*\/]\s*[\d]", low) or \
            re.search(r"({_NUM})\s*({_MATH_GATE})".format(
                _NUM=_NUM,
                _MATH_GATE="|".join(p for p, _s in _MATH_WORDS)), low):
        expr = _to_expr(t)
        if expr:
            msg, _ = ak.skills.calculate(expr)
            return (msg, True)

    return None
