"""Anmol Tyagi's profile — the default context AK answers from.

One source of truth, read by every path that needs to know who he is:

  * ``AK/profile.py``      (this file) — Python API
  * ``USER_PROFILE.md``   full human-readable reference in the project root
  * ``AGENTS.md``          points at the profile so any agent working on this
                           project picks it up automatically

Nothing here is a task list — it is background data. Apply it silently: it
decides answer length, which examples fit, and how much detail is welcome.
"""

from __future__ import annotations

import os
from typing import List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_MD = os.path.join(_ROOT, "USER_PROFILE.md")

_cache: Optional[str] = None

# ── The parts that must survive even if USER_PROFILE.md goes missing ──
NAME = "Anmol Tyagi"

IDENTITY = (
    "a technical creator/developer focused on cybersecurity, digital "
    "forensics, OSINT, AI and experimental software/hardware systems"
)

# Internal guidance only — never spoken back to the user.
NOTES = (
    "He builds and designs systems himself; never reduce him to an "
    "'AI automation engineer'."
)

EDUCATION = "Education details are not configured."

DOMAINS = (
    "Cybersecurity, digital forensics, OSINT, AI/ML, software development, "
    "browser development, Windows customization, hardware/IoT, VR, automation."
)

STACK = (
    "C, C++, Python, Java, JavaScript, TypeScript, PHP, Objective-C, HTML5, CSS, "
    "Bash, SQL, React, Next.js, Node.js, React Native, Expo, PyQt5, Qt/Qt "
    "WebEngine, MySQL, MongoDB, NumPy, Pandas, Git, GitHub, Docker, "
    "PowerShell, Windows/macOS/Linux/Android/iOS."
)

PROJECTS = (
    "ANcore Browser (Python/PyQt5/Qt WebEngine AI browser — not Electron, "
    "not React), AncoreSearch, DIY 3DOF PC-VR virtual desktop (ESP32 + "
    "MPU6050 + 5-6 inch HDMI LCD + Fresnel lenses, voice control, no "
    "controllers or hand gestures, ~Rs 10k budget), Personal AI Assistant "
    "(this project), AI Face Visualizer, StegVault, Dairy Ledger Pro, Windows "
    "Dynamic Island/customization systems."
)

TAGLINE = ("Decoding organizational structure and logic through a technical "
           "lens.")

PUBLIC = ""

# The original one-paragraph description, kept so nothing is lost. New
# positioning is *added* to this, never substituted for it.
LEGACY_IDENTITY = (
    "Anmol Tyagi is a cybersecurity enthusiast and software developer. His work "
    "spans cybersecurity, ethical hacking, OSINT, malware analysis, digital "
    "forensics, threat intelligence, reverse engineering, AI automation "
    "engineering, and AI agent development. He builds practical, intelligent "
    "security solutions and custom software from the ground up."
)


def profile_text() -> str:
    """Full profile markdown, cached. Empty string if the file is gone."""
    global _cache
    if _cache is None:
        try:
            with open(PROFILE_MD, "r", encoding="utf-8",
                      errors="replace") as fh:
                _cache = fh.read()
        except OSError:
            _cache = ""
    return _cache


def brief() -> str:
    """Compact block for a system prompt — who he is and how to answer."""
    return "\n".join([
        "About the user you are talking to (default context, apply silently):",
        "Address him only as 'Sir' or 'Boss' — never by his name (never say "
        "'Anmol Tyagi' or 'Anmol Tyagi bhai'). He is " + IDENTITY + ". " + NOTES,
        EDUCATION, "Domains: " + DOMAINS, "Stack: " + STACK,
        "Projects: " + PROJECTS,
        "How to answer him: casual Hinglish is fine, keep it natural; speak "
        "decently and clearly, and if a Hindi/Hinglish word doesn't sound right "
        "or is awkward, use the English word instead; simple "
        "question -> very short answer; technical problem -> exact steps, "
        "commands and expected output, no filler theory; difficult concepts -> "
        "explain in simple Hinglish with English terms; some features may be "
        "planned or experimental, so check the files or ask instead of "
        "assuming.",
        "Engineering lens: Goal -> Requirements -> Architecture -> Components "
        "-> Implementation -> Testing -> Security -> UI/UX -> Deployment. "
        "Never force AI or an external API into a project; prefer local or "
        "Ollama when it makes sense.",
        "Design taste: Apple-inspired, liquid glass, glassmorphism, premium, "
        "cinematic, smooth animation, gray/maroon dark palette; avoid pure "
        "black; keep facial identity intact in image edits.",
        "Always think about security: privacy, attack surface, permissions, "
        "authentication, data leakage, malicious input, phishing, tracking, "
        "sandboxing, domain reputation.",
        "Never ask for or reveal passwords, API keys, tokens or credentials.",
    ])


def facts() -> List[str]:
    """Short one-liners, in the shape AK's memory store uses."""
    return [
        "call the user 'Boss' or 'Sir' — never by his name",
        "user is a technical creator/developer focused on cybersecurity, "
        "digital forensics, OSINT, AI and experimental software/hardware "
        "systems",
        "user's domains are cybersecurity, digital forensics, OSINT, AI/ML, "
        "software development, browser development, Windows customization, "
        "hardware/IoT, VR and automation",
        "user's stack is " + STACK,
        "user's projects include " + PROJECTS,
        "user prefers casual Hinglish replies, short answers for simple "
        "questions and exact steps plus commands for technical problems",
        "user prefers local/offline processing (Ollama) when practical, and "
        "does not want AI or an external API forced into a project",
        "user's design taste is Apple-inspired liquid glass and glassmorphism "
        "with a gray/maroon dark palette, not pure black",
    ]


def answer_who_am_i() -> str:
    """Spoken-length answer for 'who am I'."""
    return f"Of course, sir. You are {NAME} — {IDENTITY}."


def answer_who_is() -> str:
    """Spoken-length answer for 'who is Anmol Tyagi' — original wording first, then
    the fuller picture."""
    return (LEGACY_IDENTITY + " " + NAME + " is " + IDENTITY + ". " + EDUCATION +
            " Tagline: " + TAGLINE)
