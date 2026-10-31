"""Task-engine integration — optional external backend for AK.

This module lets AK hand heavy coding, scripting and automation tasks to
a local command-line task engine. The binary is resolved from
``brain.engine_bin`` in ``config.json``, the ``AK_ENGINE_BIN`` environment
variable, or the first of ``ak-engine`` / ``akengine`` / ``engine`` found
on ``PATH``. If no engine is installed AK silently falls back to its own
local brain.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections import deque
from typing import Deque, Dict, List, Optional


def _brief_args(args, limit: int = 80) -> str:
    """One-line summary of tool input for the live progress log."""
    if not isinstance(args, dict):
        return str(args)[:limit]
    for key in ("path", "cmd", "command", "query", "url", "pattern", "name",
                "filePath", "file_path"):
        if args.get(key):
            return f"{key}={str(args[key]).strip()[:limit]}"
    try:
        return json.dumps(args, ensure_ascii=False)[:limit]
    except Exception:
        return str(args)[:limit]


class _Box:
    """Minimal ASCII progress box for the fast-engine stream.

    All-ASCII so it can't crash a cp1252 Windows console; a stray glyph is
    sanitised to '?' rather than killing the run.
    """

    WIDTH = 72

    def __init__(self, enabled: bool = True, title: str = "AK task"):
        self.enabled = enabled
        self.title = title
        self._open = False

    def _emit(self, s: str) -> None:
        if not self.enabled:
            return
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            try:
                print(s.encode("ascii", "replace").decode("ascii"), flush=True)
            except Exception:
                pass
        except Exception:
            pass

    def _border(self) -> None:
        self._emit("+" + "-" * (self.WIDTH - 2) + "+")

    def start(self, title: str = "") -> None:
        if not self.enabled:
            return
        t = title or self.title
        inner = self.WIDTH - 2
        label = (" " + t + " ")[:inner]
        left = (inner - len(label)) // 2
        right = inner - len(label) - left
        self._emit("+" + "-" * left + label + "-" * right + "+")
        self._open = True

    def row(self, s: str) -> None:
        if not self.enabled:
            return
        inner = self.WIDTH - 4
        s = str(s).replace("\t", " ")
        if len(s) > inner:
            s = s[:inner - 3] + "..."
        self._emit("| " + s.ljust(inner) + " |" if self._open else f"[AK:task] {s}")

    def close(self) -> None:
        if not self.enabled:
            return
        self._border()
        self._open = False


AK_SYSTEM_PROMPT = (
    "You are AK. You are Anmol Tyagi's personal, witty, "
    "concise, and highly capable AI voice assistant, running locally on his PC. "
    "Always address the user as 'Sir' or 'Boss' — NEVER by name (never say "
    "'Anmol Tyagi' or 'Anmol Tyagi bhai'). "
    "NEVER mention any underlying model, CLI agent, or software "
    "that powers you. Never say 'I'm not exactly AK'. "
    "If asked what you are, say: 'I am AK, your personal AI "
    "assistant.' If asked what you run on, say 'I run locally on your PC.' "
    "Always speak as AK, never as any other tool or company. "
    "Write your replies in Hinglish — Roman-script Hindi mixed with English — "
    "casual and natural (e.g. 'haan, dikha deta hun', 'sab kuch ready hai'). "
    "Keep real names, file paths, code, and technical terms in English. "
    "Keep spoken answers short (1-3 sentences) unless asked for detail. "
    "Be direct and helpful. "
    "When asked to write code or perform tasks, do them step by step. "
    "When asked about yourself, say you are AK running locally on your PC. "
    "You have full tool access (bash, write, edit, webfetch, etc.). When asked "
    "to perform a task, actually perform it with your tools — run the commands, "
    "create or edit files, open apps or websites. Do not just describe what "
    "could be done. "
    "Naturally greet with 'Radhe Radhe boss' sometimes at the start of a reply, "
    "and occasionally end with it too — but not every time. Make it feel natural, "
    "not forced. Use it like a signature phrase, not a template. "
    "When asked 'who is Anmol Tyagi' or 'tell me about Anmol Tyagi', respond naturally with: "
    "He is a cybersecurity enthusiast and software developer. His work spans cybersecurity, "
    "ethical hacking, OSINT, malware analysis, digital forensics, threat intelligence, "
    "reverse engineering, AI automation engineering, and AI agent development. He builds practical, "
    "intelligent security solutions and custom software from the ground up. "
    "As his assistant, you help with programming, cybersecurity research, OSINT, automation, "
    "AI development, technical documentation, and problem solving — reflecting his preference "
    "for practical, detailed, and technically accurate solutions."
)


class TaskEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._binary = self._find_binary(cfg)
        self.max_history = cfg.get("brain", {}).get("max_history", 12)
        self.history: Deque[Dict[str, str]] = deque(maxlen=self.max_history)
        # Live step output in the terminal (brain.show_progress=false to silence).
        self._show = bool((cfg.get("brain", {}) or {}).get("show_progress", True))

    # -- binary discovery ------------------------------------------------

    @staticmethod
    def _find_binary(cfg: dict) -> Optional[str]:
        configured = os.environ.get("AK_ENGINE_BIN", "") or str(
            ((cfg.get("brain", {}) or {}).get("engine_bin", "") or "")
        )
        candidates = [configured] if configured else []
        candidates += ["ak-engine.exe", "ak-engine", "akengine.exe", "akengine"]
        for cand in candidates:
            if not cand:
                continue
            path = shutil.which(cand) or (cand if os.path.isfile(cand) else "")
            if path and os.path.isfile(path):
                return path
        return None

    @property
    def available(self) -> bool:
        return self._binary is not None

    @property
    def version(self) -> str:
        if not self._binary:
            return "N/A"
        try:
            r = subprocess.run([self._binary, "--version"],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() or r.stderr.strip() or "unknown"
        except Exception:
            return "unknown"

    # -- AK conversational brain -------------------------------------

    def ask_AK(self, user_text: str, workdir: str = "") -> str:
        """Use engine as AK's conversational AI backend.
        Maintains rolling history for context.

        The AK persona lives in the project's AGENTS.md file — it is NEVER
        injected here as a fake system role. Sending a "system" message inside
        the prompt made engine treat it as an injected identity and refuse
        to adopt it. AGENTS.md is engine's own legitimate instructions file,
        so AK's voice now comes out consistently without any refusal."""
        if not self._binary:
            return "Engine is not available."
        self.history.append({"role": "user", "content": user_text})

        messages = list(self.history)
        prompt = json.dumps(messages)

        target = workdir.strip() or os.getcwd()
        if not os.path.isdir(target):
            target = os.getcwd()

        try:
            cmd = [self._binary, "run", prompt,
                   "--dangerously-skip-permissions",
                   "--format", "json"]
            reply, err = self._stream_run(cmd, target, 120)
            if err:
                if "timed out" in err:
                    return "AK is thinking too long. Please try again."
                return f"AK encountered an error: {err}"
            reply = reply or "Done."
            self.history.append({"role": "assistant", "content": reply})
            return reply

        except Exception as exc:
            return f"AK encountered an error: {exc}"

    def _parse_text_reply(self, stdout: str, stderr: str) -> str:
        """Extract just the assistant's text reply from engine JSON stream."""
        texts = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "text" and event.get("part", {}).get("type") == "text":
                t = event["part"].get("text", "").strip()
                if t:
                    texts.append(t)
        if texts:
            return "\n".join(texts).strip()
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        non_json = [l for l in lines if not l.startswith("{") and not l.startswith("[")]
        return non_json[-1] if non_json else ""

    def _stream_run(self, cmd, target: str, timeout: int, boxed: bool = False,
                    title: str = "AK task"):
        """Run engine, streaming its JSON events live as progress.

        Returns ``(text, error)`` — ``error`` is ``''`` on success. When
        ``boxed`` is set, every step is drawn inside an ASCII box (used for
        tasks); otherwise lines are printed plainly (used for chat).
        """
        texts: List[str] = []
        step = 0
        last_beat = time.time()
        t_start = time.time()
        box = _Box(self._show, title) if boxed else None
        if box:
            box.start(title)

        def emit(msg):
            if box is not None:
                box.row(msg)
            elif self._show:
                print(f"[AK:task] {msg}", flush=True)

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=target, bufsize=1)
        except Exception as exc:  # noqa: BLE001
            if box:
                box.row(f"launch failed: {exc}")
                box.close()
            return "", f"launch failed: {exc}"

        deadline = time.time() + timeout
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if time.time() > deadline:
                    proc.kill()
                    if box:
                        box.row("timed out")
                        box.close()
                    return "\n".join(texts).strip(), "timed out"
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    ev = None
                if not isinstance(ev, dict):
                    emit(line[:200])
                    continue
                part = ev.get("part")
                if not isinstance(part, dict):
                    part = {}
                typ = ev.get("type") or part.get("type") or ""
                if typ == "text" or part.get("type") == "text":
                    t = (part.get("text") or ev.get("text") or "").strip()
                    if t:
                        # Collect only — the answer is spoken/printed once as
                        # "[AK] ..." after the run. Echoing it here too showed
                        # every reply twice.
                        texts.append(t)
                elif typ == "tool" or part.get("type") == "tool" or part.get("tool"):
                    step += 1
                    tool = (part.get("tool") or part.get("name")
                            or ev.get("tool") or ev.get("name") or "tool")
                    state = part.get("state")
                    if not isinstance(state, dict):
                        state = {}
                    st = state.get("status") or ev.get("status") or ""
                    info = _brief_args(state.get("input") or part.get("input") or {})
                    emit(f"{step}. {tool} {st} {info}".rstrip())
                elif time.time() - last_beat > 8:
                    # Unknown event type: keep a light heartbeat so the prompt
                    # never looks frozen during long silent stretches.
                    last_beat = time.time()
                    emit(f"... working ({typ or 'event'})")
            proc.wait()
            if box:
                box.row(f"done in {time.time() - t_start:.0f}s.")
                box.close()
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:
                pass
            if box:
                box.close()
        return "\n".join(texts).strip(), ""

    # -- Task / code execution ------------------------------------------

    def execute_task(self, task: str, workdir: str = "") -> str:
        """Delegate a coding/automation task (no history) — actually perform it."""
        msg = (
            "Perform this task NOW using your tools (bash, write, edit, webfetch, etc.). "
            "Actually do the work yourself: run the commands, create or edit the files, "
            "open the app or website, install the package. Do NOT just suggest commands "
            "or tell the user what to do. "
            "When you finish, reply with a short summary of what you did.\n\n"
            f"Task: {task}"
        )
        return self._run(msg, workdir)

    def _run(self, message: str, workdir: str = "") -> str:
        if not self._binary:
            return "Engine is not installed."
        target = workdir.strip() or os.getcwd()
        if not os.path.isdir(target):
            target = os.getcwd()
        cmd = [self._binary, "run", message,
               "--dangerously-skip-permissions",
               "--format", "json"]
        output, err = self._stream_run(cmd, target, 300, boxed=True, title="AK task")
        if err:
            if "timed out" in err:
                return "Engine timed out."
            return f"Engine error: {err}"
        return output or "Done."
