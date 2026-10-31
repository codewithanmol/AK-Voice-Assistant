"""Voice commands that drive the AK holographic HUD layer.

Called from main.py's skill dispatch. Every handler returns
``(response_text, speak)`` or ``None`` when nothing matched.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from .holo_render import STRUCTURE_ALIASES, STRUCTURES

# words that mean "the 3D structure"
_STRUCTURE_WORDS = r"(?:structure|model|shape|object|projection|holo(?:gram)?|mesh)"

# the holographic gesture control, addressed by its own name as well
_HOLO_WORDS = (r"(?:holo(?:gram)?|holographic|hologram|gesture|gestures|"
               r"gesture\s+control|hud)")

# "start it" - every spelling that actually reaches the mic:
# chalao / chalu / chalo / on karo / start kar do ...
_ON = (r"(?:on|on\s+kar(?:o|do)|start|start\s+kar(?:o|do)|activate|enable|begin|"
       r"chalu|chalu\s+kar(?:o|do)|chalao|chala\s+do|chalo|chal\s+do|"
       r"shuru|shuru\s+kar(?:o|do)|suru|suru\s+kar(?:o|do)|"
       r"lag\s+do|laga\s+do)")
_OFF = (r"(?:off|off\s+kar(?:o|do)|stop|stop\s+kar(?:o|do)|kill|exit|close|"
        r"band|band\s+kar(?:o|do)|baja|bajao|baje|rok\s+do|shut\s+down)")

# filler words allowed between the wake word and the intent
_FILLER = r"(?:\s+(?:please|pls|ab|now|bhi|yaar|jarvis))*"

# trailing words that do not change the intent ("ak chalao kar do",
# "ak on the gesture control") - the intent has to end the sentence so a
# music request ("ak gaana chalao") is never read as a HUD command.
_TAIL = (r"(?:\s+(?:the|this|that|gesture|gestures|control|holographic|holo|"
         r"hologram|hud|system|mode|and|now|ab|please|pls|bhi|yaar|kar|do|"
         r"karo|kardo))*\s*$")

# a music request is never a HUD command
_MEDIA_WORDS = (r"(?:song|songs|gaana|gana|gaane|music|video|playlist|album|"
                r"track|film|movie|ringtone|band\s+play)")


def _bridge(ak):
    br = getattr(ak, "_ak_hud", None)
    if br is None:
        from .ak import AKBridge
        br = AKBridge()
        try:
            ak._ak_hud = br
        except Exception:
            pass
    return br


def _running(ak) -> bool:
    try:
        return _bridge(ak).is_running()
    except Exception:
        return False


def _find_structure(t: str) -> Optional[str]:
    """Find a structure name (alias or key) inside a sentence."""
    # aliases first (longest wins) so "torus knot" beats "torus"
    for alias in sorted(STRUCTURE_ALIASES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", t):
            return STRUCTURE_ALIASES[alias]
    for key in STRUCTURES:
        if re.search(r"\b" + re.escape(key) + r"\b", t):
            return key
    return None


def _ensure(ak) -> bool:
    """Start the AK HUD if it is not up yet."""
    br = _bridge(ak)
    if br.is_running():
        return True
    try:
        return br.start()
    except Exception:
        return False


def _toggle_intent(t: str) -> Optional[str]:
    """What the user wants the holographic gesture control to do.

    Returns "on", "off" or "toggle" (a bare "ak") - None when the sentence
    is not about the HUD at all.

    The wake word is optional in the text: the main loop strips it off
    before the command is routed, so "ak chalao" arrives here as "chalao".
    That is why the HUD is also addressed by its own name ("holo",
    "gesture control").
    """
    if not t:
        return None
    # a music request is never a HUD command ("ak gaana chalao")
    if re.search(_MEDIA_WORDS, t):
        return None
    # a bare "ak" toggles it
    if re.search(r"^\s*(?:ak|jarvis)\b\s*$", t):
        return "toggle"
    # "ak on" / "ak chalao" / "ak gesture control chalao"
    if re.search(r"\b(?:ak|jarvis)\b" + _FILLER + r"\s+(?:" + _ON + r")\b"
                 + _TAIL, t):
        return "on"
    if re.search(r"\b(?:ak|jarvis)\b" + _FILLER + r"\s+(?:" + _OFF + r")\b"
                 + _TAIL, t):
        return "off"
    # the HUD by its own name - "holo mode", "gesture control on"
    if re.search(_HOLO_WORDS + r"\b.*\b(?:" + _OFF + r")\b", t):
        return "off"
    if re.search(_HOLO_WORDS + r"\b.*\b(?:" + _ON + r")\b", t):
        return "on"
    if re.search(r"\bholo(?:gram)?\s*mode\b", t):
        return "on"
    return None


def _sysctl():
    """The Windows control layer (window management, mouse, media keys)."""
    try:
        from . import sysctl
    except Exception:  # pragma: no cover - flat import fallback
        import sysctl
    return sysctl


# Websites and domains - these belong to the assistant lane (it opens them in
# Chrome and records the step for the workflow learner), NOT to the HUD app
# lane which would try to run them as a program.
KNOWN_SITES = {
    "youtube", "google", "gmail", "github", "reddit", "twitter", "facebook",
    "instagram", "linkedin", "amazon", "flipkart", "spotify", "netflix",
    "hotstar", "chatgpt", "stackoverflow", "whatsapp", "telegram", "engine",
    "wikipedia", "maps", "drive", "notion", "figma", "canva", "udemy",
}


def _looks_like_site(name: str) -> bool:
    """True when 'open <name>' means a website, not a desktop app."""
    n = (name or "").strip().lower()
    if not n:
        return False
    first = n.split()[0]
    if first in KNOWN_SITES:
        return True
    if "." in n:
        head = first.split(".")[0]
        if head in KNOWN_SITES or first.endswith(
                (".com", ".org", ".net", ".io", ".ai", ".co", ".in", ".dev")):
            return True
        if re.search(r"\.[a-z]{2,6}$", first):
            return True
    return False


def _wf_step(ak, title: str, kind: str = "action", detail: str = "") -> None:
    """Record a step for the workflow learner (if the assistant supports it)."""
    fn = getattr(ak, "_wf_step", None)
    if fn is None:
        return
    try:
        fn(title, kind=kind, detail=detail)
    except Exception:
        pass


def _stop_visual(ak) -> None:
    """Close the visualizer window too - 'ak band' means everything visual."""
    fn = getattr(ak, "_stop_visualizer", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass


def _start_visual(ak) -> None:
    """Bring the visualizer back when the gesture control is called up."""
    try:
        if not (getattr(ak, "cfg", None) or {}).get("visualizer", {}) \
                .get("enabled"):
            return
    except Exception:
        return
    fn = getattr(ak, "_start_visualizer", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass


def match(ak, text: str) -> Optional[tuple]:
    t = text.lower().strip()
    if not t:
        return None

    # ── start / stop ────────────────────────────────────────────────────
    # The wake word is "ak" (the old "jarvis" still works as an alias), but
    # it only counts when it is used as a command - "ak" alone, "ak on",
    # "ak chalao" - so a normal sentence is never hijacked.
    intent = _toggle_intent(t)
    if intent == "off":
        _stop_visual(ak)
        if not _running(ak):
            return ("AK is already off, sir.", True)
        # the HUD's own AK OFF path - clean shutdown of the whole visual stack
        try:
            _bridge(ak).send("power")
            time.sleep(0.8)
        except Exception:
            pass
        _bridge(ak).stop()
        return ("AK is off, sir. Gesture control stopped.", True)
    if intent == "on":
        if _running(ak):
            _start_visual(ak)
            return ("AK is already running, sir.", True)
        ok = _ensure(ak)
        if not ok:
            return ("AK could not start. Check the camera and models.", True)
        try:
            ready = _bridge(ak).status().get("ready")
        except Exception:
            ready = None
        if ready is False:
            return ("AK is starting, sir - camera warm up ho raha hai.", True)
        _start_visual(ak)
        return ("AK is live, sir. Gesture control on - point to aim, "
                "pinch to click, fist to zoom.", True)
    if intent == "toggle":
        # bare "ak" -> toggle
        if _running(ak):
            _bridge(ak).stop()
            _stop_visual(ak)
            return ("AK is off, sir.", True)
        ok = _ensure(ak)
        _start_visual(ak)
        return (("AK is live, sir. Gesture control on - point to aim, "
                 "pinch to click.") if ok
                else ("AK could not start, sir."), True)

    # ── structure selection ─────────────────────────────────────────────
    if re.search(r"\b(hide|remove|off)\b.*\b" + _STRUCTURE_WORDS + r"\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir. Say ak on.", True)
        _bridge(ak).send("hide_scene")
        return ("Hiding the structure.", True)

    key = _find_structure(t)
    if key and (re.search(_STRUCTURE_WORDS, t) or
                re.search(r"\b(show|set|switch|change|display|project|load|"
                          r"activate|banana|bana do|give me|make me|render)\b", t)):
        if not _ensure(ak):
            return ("AK is not running, sir. Say ak on.", True)
        _bridge(ak).send("structure", key=key)
        return (f"Projecting the {key}.", True)

    # ── next / previous structure ───────────────────────────────────────
    if re.search(r"\bnext\s+" + _STRUCTURE_WORDS + r"\b", t) or \
            re.search(r"\bnext\b", t) and re.search(_STRUCTURE_WORDS, t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("next", step=1)
        return ("Switching to the next structure.", True)
    if re.search(r"\b(previous|prev|last|back)\s+" + _STRUCTURE_WORDS + r"\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("next", step=-1)
        return ("Going back to the previous structure.", True)

    # ── rotate / zoom / explode ─────────────────────────────────────────
    if re.search(r"\b(rotate|spin|turn)\b", t):
        amount = 0.9
        axis = "yaw"
        if re.search(r"\b(up|upward|above)\b", t):
            axis, amount = "pitch", 0.5
        elif re.search(r"\b(down|downward|below)\b", t):
            axis, amount = "pitch", -0.5
        elif re.search(r"\b(tilt|roll|side)\b", t):
            axis, amount = "roll", 0.5
        elif re.search(r"\bleft\b", t):
            amount = -0.9
        elif re.search(r"\bright\b", t):
            amount = 0.9
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("rotate", axis=axis, amount=amount)
        return ("Rotating.", True)

    if re.search(r"\b(zoom|scale)\s+(in|closer|bigger|up)\b", t) or \
            re.search(r"\b(more|too)\s+big\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("zoom", factor=1.25)
        return ("Zooming in.", True)
    if re.search(r"\b(zoom|scale)\s+(out|further|smaller|down)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("zoom", factor=0.8)
        return ("Zooming out.", True)

    if re.search(r"\b(explode|expand|scatter|bloom)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("explode", amount=0.6)
        return ("Exploding the structure.", True)
    if re.search(r"\b(collapse|gather|assemble|shrink structure)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("explode", amount=-0.6)
        return ("Collapsing the structure.", True)

    if re.search(r"\bwireframe\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("material", mode="wire")
        return ("Wireframe mode.", True)
    if re.search(r"\b(solid|shaded|material)\b", t) and "structure" in t:
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("material", mode="solid")
        return ("Solid mode.", True)

    if re.search(r"\b(hide|remove|off)\b.*\b" + _STRUCTURE_WORDS + r"\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("hide_scene")
        return ("Hiding the structure.", True)
    if re.search(r"\b(show|bring back|display)\b.*\b" + _STRUCTURE_WORDS + r"\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("hide_scene")
        return ("Showing the structure.", True)

    if re.search(r"\b(palette|theme)\b", t):
        for name in ("stark", "ember", "ion"):
            if name in t:
                if not _ensure(ak):
                    return ("AK is not running, sir.", True)
                _bridge(ak).send("palette", name=name)
                return (f"{name.capitalize()} palette.", True)

    # ── vision: clothes / expression / held object ──────────────────────
    vision_q = re.search(
        r"\b(what am i wearing|what's i am wearing|kya pehna|kya main pehn|"
        r"kya pehna hai|my outfit|my clothes|wardrobe|dress|outfit|"
        r"analy[sz]e my (?:clothes|outfit|face)|scan me|look at me|"
        r"describe me|vision|intel)\b", t)
    obj_q = re.search(
        r"\b(what am i holding|what is in my hand|in my hands|held object|"
        r"identify the object|object in my hand|kya pakda|kya pakda hai|"
        r"what am i holding in my hand)\b", t)
    if vision_q or obj_q:
        if not _ensure(ak):
            return ("AK is not running, sir. Say ak on.", True)
        br = _bridge(ak)
        br.send("vision", speak=False)
        br.send("panel", visible=True)
        # give the analyzer a moment, then read the narration back
        narration = ""
        deadline = time.time() + 6.0
        while time.time() < deadline:
            time.sleep(0.6)
            st = br.status().get("vision") or {}
            narration = st.get("narration") or ""
            if narration:
                break
        if narration:
            return (narration, True)
        return ("Scanning... give me a second, sir.", True)

    if re.search(r"\b(hide|close|off)\b.*\b(panel|popup|intel)\b", t):
        if not _running(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("panel", visible=False)
        return ("Panel hidden.", True)

    # ── face follow (camera panel slides with the face) ─────────────────
    if re.search(r"\b(follow|track|follow\s+face|face\s+follow|"
                 r"camera\s+follow|move\s+with\s+face)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir. Say ak on.", True)
        off = bool(re.search(r"\b(off|stop|band|disable|rok)\b", t))
        on = bool(re.search(r"\b(on|start|enable|chalu|shuru)\b", t))
        enabled = False if off else (True if on else None)
        _bridge(ak).send("follow", **({"enabled": enabled} if enabled is not None else {}))
        return ("Face follow " + ("on - panel aapke face ke saath move karega."
                                 if not off else "off."), True)

    # ── PC control: windows + touch mode ─────────────────────────────────
    if re.search(r"\btouch\s*(mode)?\b", t) and \
            re.search(r"\b(on|off|chalu|band|start|stop|enable|disable)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir. Say ak on.", True)
        off = bool(re.search(r"\b(off|stop|band|disable|rok)\b", t))
        _bridge(ak).send("touch", enabled=not off)
        return ("Touch mode " + ("off - pinch ab seedha click karega."
                                 if off else
                                 "on - pinch se tap, hold se right click, "
                                 "do haath se scroll aur zoom."), True)

    if re.search(r"\b(show|hide|dikha|la)\b.*\bdesktop\b", t) or \
            re.search(r"\bminimi[sz]e\s+(all|everything|sab)\b", t):
        _sysctl().minimize_all()
        return ("Desktop dikha raha hoon.", True)

    if re.search(r"\bminimi[sz]e\b", t):
        _sysctl().minimize_window()
        return ("Window minimize kar di.", True)

    if re.search(r"\b(maximi[sz]e|full\s*screen)\b", t):
        _sysctl().maximize_window()
        return ("Window maximize kar di.", True)

    if re.search(r"\b(restore|normal\s*size)\b", t):
        _sysctl().restore_window()
        return ("Window restore kar di.", True)

    if (re.search(r"\b(close|band)\b", t) and
            re.search(r"\b(window|ye|is|yeh|it)\b", t)) or \
            re.search(r"\bclose\s+(it|this)\b", t):
        _sysctl().close_foreground()
        return ("Window close kar di.", True)

    # ── system control ──────────────────────────────────────────────────
    if re.search(r"\bvolume\s+(up|increase|higher|badhao)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("volume", action="up", step=5)
        return ("Volume up.", True)
    if re.search(r"\bvolume\s+(down|decrease|lower|kam)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("volume", action="down", step=5)
        return ("Volume down.", True)
    if re.search(r"\b(mute|unmute|silence)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("volume", action="mute")
        return ("Toggling mute.", True)

    if re.search(r"\bbrightness\s+(up|increase|higher|brighten)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("brightness", action="up", step=10)
        return ("Brightness up.", True)
    if re.search(r"\bbrightness\s+(down|decrease|lower|dim)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("brightness", action="down", step=10)
        return ("Brightness down.", True)

    if re.search(r"\b(screenshot|screen shot|grab the screen|capture the screen)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("screenshot")
        return ("Taking a screenshot.", True)

    if re.search(r"\b(lock|secure)\b.*\b(pc|screen|workstation|computer|laptop)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("lock")
        return ("Locking the workstation.", True)

    if re.search(r"\b(alt tab|switch window|next window)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        _bridge(ak).send("alt_tab")
        return ("Switching windows.", True)

    if re.search(r"\b(play|pause|resume|stop)\b.*\b(music|song|media|track)\b", t):
        if not _ensure(ak):
            return ("AK is not running, sir.", True)
        key = "media_play_pause"
        if re.search(r"\b(stop)\b", t):
            key = "media_stop"
        _bridge(ak).send("media", key=key)
        return ("Media control sent.", True)

    m = re.search(r"\bopen\s+(?:the\s+)?([a-z0-9 ._-]{2,30})$", t)
    if m:
        name = m.group(1).strip()
        if name and name not in ("ak", "jarvis", "eyes", "eyes on", "file"):
            # websites go to the assistant lane - it knows how to open them in
            # Chrome and it records the step for the workflow learner
            if _looks_like_site(name):
                return None
            if not _ensure(ak):
                return ("AK is not running, sir.", True)
            _bridge(ak).send("app", name=name)
            _wf_step(ak, f"Open app: {name}", kind="app", detail=name)
            return (f"Opening {name}.", True)

    return None


def match_wake(ak, text: str, wake: str = "ak") -> Optional[tuple]:
    """A command spoken right after the wake word.

    The main loop strips the wake word off the transcript before the command
    is routed, so "ak chalao" reaches this function as just "chalao". Without
    putting the wake context back the holographic gesture control never
    starts and the normal assistant answers instead.

    Only the on/off intent is handled here - every other command keeps going
    through the normal assistant.
    """
    t = (text or "").strip()
    if not t:
        return None
    # the HUD router only knows "ak" / "jarvis" as the wake word
    w = wake if (wake or "").lower() in ("ak", "jarvis") else "ak"
    joined = f"{w} {t}"
    if _toggle_intent(joined) is None:
        return None
    return match(ak, joined)


def start_hud(ak) -> bool:
    """Bring the holographic gesture control up next to the voice assistant.

    Used by the default launcher so AK comes up with the gesture control
    already live - no need to say "ak chalao" first.
    """
    try:
        br = _bridge(ak)
        if br.is_running():
            return True
        return bool(br.start())
    except Exception:
        return False


def stop_hud(ak) -> None:
    """Stop the holographic gesture control."""
    try:
        _bridge(ak).stop()
    except Exception:
        pass
