"""AK — Local Agent backend.

The self-hosted brain that powers AK. It runs entirely on the machine using
Ollama (local models) plus a small tool set — no API key, no cloud.

Built by Anmol Tyagi. Same public surface as ``TaskEngine`` so AK can use it
interchangeably: ``available``, ``version``, ``ask_AK``, ``execute_task``.

Tuned for a low-spec laptop: a compact persona prompt (fast prefill), CPU-only
inference, small context, and automatic model unload when idle.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

AGENT_NAME = "AK"

# Where the local agent core lives (kept on D:).
_CORE = r"D:\YOUR_DATA_PATH\LocalAgent\core"
_PERSONA_FILE = r"D:\YOUR_DATA_PATH\LocalAgent\AK_PERSONA.md"
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

# Fallback persona if the file is missing.
_FALLBACK_PERSONA = (
    "You are AK, Anmol Tyagi's personal AI assistant, running locally on his PC. "
    "Always address the user as 'Sir' or 'Boss' — never by name (never say "
    "'Anmol Tyagi' or 'Anmol Tyagi bhai'). Reply in short, natural Hinglish. You are not "
    "engine or any other product — you are AK, built by Anmol Tyagi. Never "
    "mention the engines that power you."
)


def _load_persona() -> str:
    try:
        with open(_PERSONA_FILE, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        if text:
            return text
    except OSError:
        pass
    return _FALLBACK_PERSONA


def _brief_args(args, limit: int = 90) -> str:
    """One-line, human summary of a tool call for the progress log."""
    if not isinstance(args, dict):
        return str(args)[:limit]
    for key in ("path", "cmd", "command", "query", "url", "pattern", "name"):
        if args.get(key):
            return f"{key}={str(args[key]).strip()[:limit]}"
    try:
        s = json.dumps(args, ensure_ascii=False)
    except Exception:
        s = str(args)
    return s[:limit]


class _TaskUI:
    """Live task progress, drawn inside a box in the terminal.

    AK speaks the final answer itself, so the agent UI used to be silenced
    entirely — the user saw "working in background..." and then nothing, with
    no way to know how much (or whether) work was happening. This surfaces the
    *process* instead: every tool step and a one-line result inside a bordered
    box, plus a start / done line with step count and elapsed time.
    Silence it with ``brain.show_progress = false``.
    """

    WIDTH = 72

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.steps = 0
        self._open = False

    # -- low level -------------------------------------------------------

    def _emit(self, line: str) -> None:
        if not self.enabled:
            return
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            # Windows consoles are often cp1252 — never let a stray glyph
            # (bullet, arrow, Hindi path) crash the progress box.
            try:
                print(line.encode("ascii", "replace").decode("ascii"), flush=True)
            except Exception:
                pass
        except Exception:
            pass

    def _border(self) -> None:
        self._emit("+" + "-" * (self.WIDTH - 2) + "+")

    def _title(self, text: str) -> None:
        inner = self.WIDTH - 2
        label = (" " + str(text) + " ")[:inner]
        left = (inner - len(label)) // 2
        right = inner - len(label) - left
        self._emit("+" + "-" * left + label + "-" * right + "+")

    def _row(self, text: str) -> None:
        inner = self.WIDTH - 4
        s = str(text).replace("\t", " ")
        if len(s) > inner:
            s = s[:inner - 3] + "..."
        self._emit("| " + s.ljust(inner) + " |")

    def _append(self, text: str) -> None:
        if not self.enabled:
            return
        if self._open:
            for ln in (str(text).splitlines() or [""]):
                self._row(ln)
        else:
            self._emit(f"[AK:task] {text}")

    # -- box lifecycle ---------------------------------------------------

    def start(self, title: str) -> None:
        if not self.enabled:
            return
        self.steps = 0
        self._title("AK task")
        self._row(f"task: {title}")
        self._border()
        self._open = True

    def finish(self, summary: str) -> None:
        if not self.enabled:
            return
        self._row(summary)
        self._border()
        self._open = False

    def summary(self) -> str:
        return f"{self.steps} step(s)"

    # -- agent callback surface (matches core ui.py) ---------------------

    def banner(self, *a, **k):
        pass

    def info(self, text="", *a, **k):
        if text:
            self._append(text)

    def warn(self, text="", *a, **k):
        if text:
            self._append(f"! {text}")

    def error(self, text="", *a, **k):
        if text:
            self._append(f"error: {text}")

    def assistant(self, text="", *a, **k):
        # The final answer is shown once as "[AK] ..." after the run — echoing
        # it here as well displayed every reply twice. Progress (tools, results,
        # warnings, summary) still streams live.
        return

    def stream_start(self, *a, **k):
        pass

    def stream_delta(self, chunk="", *a, **k):
        pass

    def stream_end(self, *a, **k):
        pass

    def user_prompt(self):
        return ""

    def tool(self, name="", args=None, *a, **k):
        self.steps += 1
        self._append(f"{self.steps}. {name} {_brief_args(args or {})}")

    def tool_result(self, name="", out="", *a, **k):
        lines = str(out or "").strip().splitlines()
        line = lines[0] if lines else ""
        self._append(f"   -> {line[:200]}" if line else "   -> (no output)")

    def confirm(self, question="", *a, **k):
        self._append(f"auto-allow: {question}")
        return True


class LocalAgent:
    """AK's local brain — Ollama-backed, tool-capable, offline-first."""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self._err = ""
        self._agent = None
        # Live progress in the terminal (brain.show_progress=false to silence).
        self._show_progress = bool(
            (self.cfg.get("brain", {}) or {}).get("show_progress", True))
        self._ui = _TaskUI(self._show_progress)
        self._init_agent()

    # -- setup ------------------------------------------------------------

    def _init_agent(self):
        try:
            import config as la_config
            import providers
            from agent import Agent

            la_cfg = la_config.load_config()

            # AK's brain config overrides the local agent's model / URL.
            brain = self.cfg.get("brain", {}) or {}
            la_cfg["provider"] = "ollama"
            if brain.get("ollama_model"):
                la_cfg["model"] = brain["ollama_model"]
            if brain.get("ollama_url"):
                la_cfg["ollama_host"] = brain["ollama_url"]
            # Tasks (writing a whole page/script) need more room than chat:
            # a 1024-token cap truncates files mid-way.
            la_cfg["num_predict"] = max(int(la_cfg.get("num_predict", 1024) or 1024), 2048)
            la_cfg["num_ctx"] = max(int(la_cfg.get("num_ctx", 2048) or 2048), 4096)

            provider = providers.get_provider(la_cfg)
            self._agent = Agent(la_cfg, provider, self._ui)

            # Compact persona only — keep the prompt short so prefill is fast
            # on low-spec hardware. (The generic core prompt is intentionally
            # not appended.)
            self._agent.system = _load_persona()
        except Exception as exc:  # noqa: BLE001
            self._err = str(exc)
            self._agent = None

    # -- TaskEngine-compatible surface ---------------------------------

    @property
    def available(self) -> bool:
        return self._agent is not None and self._ollama_alive()

    @property
    def version(self) -> str:
        return AGENT_NAME

    def _ollama_alive(self) -> bool:
        url = (self._agent.cfg.get("ollama_host", "http://localhost:11434")
               + "/api/version")
        try:
            import requests
            requests.get(url, timeout=3)
            return True
        except Exception:
            return False

    def reload_persona(self):
        """Re-read the persona file (e.g. after an edit)."""
        if self._agent:
            self._agent.system = _load_persona()

    def ask_AK(self, user_text: str, workdir: str = "") -> str:
        """Conversational turn — no tool schemas (fast on weak hardware)."""
        if not self._agent:
            return f"{AGENT_NAME} unavailable: {self._err}"
        try:
            if workdir and os.path.isdir(workdir):
                os.chdir(workdir)
            reply = self._agent.ask(user_text, use_tools=False)
            return (reply or "").strip() or "Done."
        except Exception as exc:  # noqa: BLE001
            return f"{AGENT_NAME} error: {exc}"

    def _project_dir(self, task: str) -> str:
        """A stable folder for a task's output (so it doesn't litter AK's dir).

        D:\\YOUR_DATA_PATH\\LocalAgent\\projects\\<task-slug>. Re-running the same
        task updates the same project instead of creating duplicates.
        """
        base = os.path.join(os.path.dirname(_CORE), "projects")
        slug = re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-")[:40]
        slug = slug or "task"
        target = os.path.join(base, slug)
        try:
            os.makedirs(target, exist_ok=True)
            return target
        except Exception:
            return ""

    def execute_task(self, task: str, workdir: str = "") -> str:
        """Actually perform a coding/automation task with the agent's tools."""
        if not self._agent:
            return f"{AGENT_NAME} unavailable: {self._err}"
        target = (workdir or "").strip()
        if not target or not os.path.isdir(target):
            target = self._project_dir(task)
        where = (f"Create everything inside this folder:\n{target}\n"
                 "Use a full path under that folder for every file you write.\n"
                 if target else "")
        prompt = (
            "Perform this task now using your tools (write_file, run_bash, "
            "read_file, list_dir, glob, grep). Do the work yourself — create or "
            "edit the files, run the commands. Use the exact tool names above. "
            "For a web page, write one complete index.html with inline CSS and "
            "JS. " + where +
            "Finish with a one-line summary of what you did.\n\n"
            f"Task: {task}"
        )
        t0 = time.time()
        self._ui.start(task.strip()[:160])
        try:
            if workdir and os.path.isdir(workdir):
                os.chdir(workdir)
            reply = self._agent.ask(prompt, use_tools=True, max_steps=15)
            self._ui.finish(f"done - {self._ui.summary()} in "
                            f"{time.time() - t0:.0f}s.")
            return (reply or "").strip() or "Done."
        except Exception as exc:  # noqa: BLE001
            self._ui.finish(f"failed after {time.time() - t0:.0f}s: {exc}")
            return f"{AGENT_NAME} error: {exc}"
