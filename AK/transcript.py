"""Shared conversation-transcript writer.

Every chat lane AK runs through — Brain (Ollama/Groq), the engine agent
and the local agent — appends its exchanges to ONE daily log so nothing is
lost when the app restarts:

    D:\\YOUR_DATA_PATH\\conversations\\conversation_YYYY-MM-DD.log

The line format is fixed and is what ``smalltalk.learn_from_history()``
parses when it mines the logs for common phrases:

    [HH:MM:SS] You : <what the user said>
    [HH:MM:SS] AK  : <AK's reply>
"""

from __future__ import annotations

import datetime
import os

from .config import get_data_dir


def conversations_dir() -> str:
    """Folder holding the daily transcript logs (created if missing)."""
    folder = os.path.join(get_data_dir(), "conversations")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return folder


def strip_preamble(text: str) -> str:
    """Drop the memory preamble AK prepends, keeping only the user's words.

    main.py sends ``"<memory context>\\n\\nUser: <real question>"`` down the
    agent lane; the transcript should store the real question, not the facts
    block that came with it.
    """
    t = (text or "").strip()
    marker = "\n\nUser: "
    idx = t.rfind(marker)
    if idx != -1:
        return t[idx + len(marker):].strip()
    if t.startswith("User: "):
        return t[6:].strip()
    return t


def save_transcript(user_text: str, reply: str, folder: str = "") -> str:
    """Append one exchange to today's log. Returns the log path ('' on failure)."""
    try:
        folder = folder or conversations_dir()
        os.makedirs(folder, exist_ok=True)
        now = datetime.datetime.now()
        stamp = now.strftime("%Y-%m-%d")
        path = os.path.join(folder, f"conversation_{stamp}.log")
        t = now.strftime("%H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{t}] You : {user_text}\n")
            fh.write(f"[{t}] AK  : {reply}\n\n")
        return path
    except Exception:  # noqa: BLE001  (logging must never break a reply)
        return ""
