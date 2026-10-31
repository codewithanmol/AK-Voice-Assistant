"""The 'brain' — conversational intelligence with pluggable backends.

Backends (config `brain.backend`):
  "auto"   -> use Ollama if it's running locally, else Groq if GROQ_API_KEY is
              set, else a small built-in rule-based responder.
  "ollama" -> local LLM via Ollama (free, private, offline). https://ollama.com
  "groq"   -> Groq free cloud tier. Set env var GROQ_API_KEY. Very fast & free.
  "rule"   -> no LLM; canned conversational replies only.

Keeps a rolling conversation history so the assistant has context.
Every exchange is ALSO saved (if `brain.save_conversations`) as a plain-text
transcript under  D:\\YOUR_DATA_PATH\\conversations\\  so AK's chats live on the
D drive and can be re-learned from later.
"""

from __future__ import annotations

import datetime
import json
import os
from collections import deque
from typing import Deque, Dict, List

from .config import get_data_dir
from .transcript import save_transcript


class Brain:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.system_prompt = cfg.get("system_prompt", "You are a helpful assistant.")
        self.max_history = cfg.get("max_history", 12)
        self.history: Deque[Dict[str, str]] = deque(maxlen=self.max_history)

        self.ollama_url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/")
        self.ollama_model = cfg.get("ollama_model", "llama3.2")
        # cap placeholder for _ask_ollama (set from cfg if provided)
        self.cfg_threads = int(cfg.get("num_threads", 0) or 0) or None
        self.groq_model = cfg.get("groq_model", "qwen/qwen3.8-27b")
        self.groq_key = (cfg.get("groq_key") or
                         os.environ.get("GROQ_API_KEY", "")).strip()
        self.keep_transcripts = bool(cfg.get("save_conversations", True))

        self.backend = self._resolve_backend(cfg.get("backend", "auto"))
        print(f"[brain] Using backend: {self.backend}")

    # -- selection ------------------------------------------------------
    def _resolve_backend(self, requested: str) -> str:
        if requested == "ollama":
            return "ollama"
        if requested == "groq":
            return "groq" if self.groq_key else "rule"
        if requested == "rule":
            return "rule"
        # auto -> NEVER silently pick Ollama. The local model only runs when the
        # user explicitly says so ("local mode" / "ollama"); everything else
        # prefers the fast cloud API, else the tiny rule responder.
        if self.groq_key:
            return "groq"
        return "rule"

    def _ollama_alive(self) -> bool:
        try:
            import requests

            requests.get(f"{self.ollama_url}/api/tags", timeout=1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- public ---------------------------------------------------------
    def set_backend(self, requested: str) -> str:
        """Switch brain backend at runtime ('ollama' | 'groq' | 'auto' | 'rule').

        Returns a short Hinglish confirmation string for TTS.
        """
        req = (requested or "").strip().lower()
        if req in ("ollama", "local", "lok", "model", "offline"):
            chosen = "ollama"
            note = "Main ab local mode mein hoon — khud ke model se baat karungi, bilkul free aur offline."
        elif req in ("groq", "api", "cloud", "online", "fast"):
            if not self.groq_key:
                chosen = self.backend
                return ("API mode ke liye Groq key set nahi hai. Pehle key daal do, "
                        "phir bolo 'api mode'. Abhi local mode hi chal raha hai.")
            chosen = "groq"
            note = "Main ab API mode mein hoon — fast cloud reply milega."
        else:
            chosen = self._resolve_backend("auto")
            note = f"Ho gaya, main ab {chosen} mode mein hoon."
        self.backend = chosen
        self._persist_backend(chosen)
        return note

    def _persist_backend(self, chosen: str) -> None:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("brain", {})["backend"] = chosen
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def think(self, user_text: str) -> str:
        """Return a reply to `user_text`, updating conversation history."""
        self.history.append({"role": "user", "content": user_text})
        try:
            if self.backend == "ollama":
                reply = self._ask_ollama()
            elif self.backend == "groq":
                reply = self._ask_groq()
            else:
                reply = self._ask_rule(user_text)
        except Exception as exc:  # noqa: BLE001
            reply = f"My reasoning module hit a snag: {exc}"
        reply = (reply or "").strip() or "I'm not sure how to answer that."
        self.history.append({"role": "assistant", "content": reply})
        self._save_transcript(user_text, reply)
        return reply

    def _save_transcript(self, user_text: str, reply: str) -> None:
        """Append this exchange to a D-drive transcript file (per day)."""
        if not self.keep_transcripts:
            return
        try:
            folder = os.path.join(get_data_dir(), "conversations")
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d")
            path = os.path.join(folder, f"conversation_{stamp}.log")
            t = datetime.datetime.now().strftime("%H:%M:%S")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"[{t}] You : {user_text}\n")
                fh.write(f"[{t}] AK  : {reply}\n\n")
        except Exception:
            pass

    def _messages(self) -> List[Dict[str, str]]:
        # The user's profile is appended to whatever system prompt is
        # configured, so every backend (Ollama / Groq) knows who it is
        # talking to and how much detail that person wants.
        system = self.system_prompt
        try:
            from .profile import brief
            system = f"{system}\n\n{brief()}"
        except Exception:
            pass
        return [{"role": "system", "content": system}, *self.history]

    # -- backends -------------------------------------------------------
    def _ask_ollama(self) -> str:
        import requests

        resp = requests.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.ollama_model,
                "messages": self._messages(),
                "stream": False,
                # low temperature = stable, grammatically clean Hinglish;
                # fewer threads ease CPU load on this small local model.
                "options": {
                    "temperature": 0.35,
                    "num_predict": 140,
                    "repeat_penalty": 1.15,
                    "num_threads": self.cfg_threads,
                    "num_ctx": 512,
                    "stop": ["\n\n\n\n", "\n---"],
                },
                # unload the model right after replying so it doesn't sit
                # in RAM (2 GB) idle — big win for everyday CPU/RAM usage.
                "keep_alive": 0,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")

    def _ask_groq(self) -> str:
        import requests

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json={
                "model": self.groq_model,
                "messages": self._messages(),
                "temperature": 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _ask_rule(self, text: str) -> str:
        """Tiny offline fallback so the assistant still 'talks' with no LLM."""
        t = text.lower()
        if any(g in t for g in ("hello", "hi ", "hey", "greetings")):
            return "Hello! I'm online and ready."
        if "how are you" in t:
            return "Operating at full capacity, thank you."
        if "your name" in t:
            return "I'm AK, your assistant."
        if "thank" in t:
            return "Anytime."
        if any(w in t for w in ("who made", "who created", "who built")):
            return "I'm a free, open AK-style assistant you can run yourself."
        return (
            "I don't have a language model connected right now, so I can only "
            "handle direct commands. Install Ollama or set GROQ_API_KEY for full "
            "conversation."
        )
