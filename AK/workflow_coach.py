"""workflow_coach.py — AK records what it does, then writes the playbook.

Usage from main.py:
    coach = WorkflowCoach(self)
    coach.begin("install obs studio")
    coach.step("winget search obs", kind="command", command="winget search obs")
    ...
    result = coach.end(outcome="done")
"""

from __future__ import annotations

import datetime
import os
import re

from . import workflow_learn
from . import screen_recorder


class WorkflowCoach:
    """Drives one recording session and turns it into a stored playbook."""

    def __init__(self, ak):
        self.ak = ak
        self.store = workflow_learn.WorkflowStore(_data_path(ak))
        self.rec = None
        self.task = ""
        self.title = ""
        self.t0 = 0.0
        self.steps = []
        self.session_dir = ""
        self.enabled = True
        self.region = "focus"
        self.fps = 1.5

    # ── session ─────────────────────────────────────────────────────────
    def begin(self, task, title=""):
        if not self.enabled:
            return False
        if self.rec is not None:
            return False              # a session is already running
        task = (task or "").strip()
        if not task:
            return False
        self.task = task
        self.title = title or task
        self.steps = []
        self.t0 = datetime.datetime.now().timestamp()
        try:
            self.store.ensure()
        except Exception:
            pass
        name = workflow_learn._slug(task)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = os.path.join(self.store.sessions_dir, f"{name}_{stamp}")
        try:
            os.makedirs(self.session_dir, exist_ok=True)
        except Exception:
            self.session_dir = ""
        self.rec = screen_recorder.ScreenRecorder(self.session_dir or
                                                  self.store.dir,
                                                  region=self.region,
                                                  fps=self.fps)
        try:
            self.rec.start()
        except Exception:
            self.rec = None
        # the very first frame - proof of where the work started
        self.step("Started the task", kind="action")
        return True

    def step(self, title, kind="action", command="", detail=""):
        st = {"n": len(self.steps) + 1,
              "t": round(datetime.datetime.now().timestamp() - self.t0, 2),
              "title": title,
              "kind": kind,
              "command": command or "",
              "detail": detail or "",
              "app": "",
              "shot": ""}
        if self.rec is not None:
            try:
                shot = self.rec.mark(title, kind=kind, command=command,
                                      detail=detail)
                st["app"] = shot.get("app", "")
                st["shot"] = shot.get("shot", "")
            except Exception:
                pass
        else:
            st["app"] = screen_recorder._win_title()
        self.steps.append(st)
        return st

    def end(self, outcome="", reply="", min_steps=1):
        """Finish the session, write the .md playbook, return the summary."""
        rec_steps = []
        if self.rec is not None:
            try:
                self.rec.stop()
                rec_steps = list(self.rec.steps or [])
            except Exception:
                pass
            self.rec = None
        # The recorder's list is the full truth: AK's own semantic steps PLUS
        # the window switches that were detected while the work happened.
        steps = rec_steps or self.steps
        dur = round(datetime.datetime.now().timestamp() - self.t0, 1)
        if len(steps) < max(1, int(min_steps)):
            # Nothing worth remembering - do not leave noise behind.
            self._cleanup_session()
            self.steps = []
            return {"ok": False, "skipped": True, "steps": len(steps),
                    "duration": dur}
        tags = self._tags(steps)
        wf = {
            "name": workflow_learn._slug(self.task),
            "title": self.title,
            "task": self.task,
            "tags": tags,
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "ak",
            "duration": dur,
            "outcome": outcome or "",
            "steps": steps,
            "session_dir": self.session_dir,
            "session_rel": os.path.join("sessions", os.path.basename(self.session_dir))
            .replace("\\", "/") if self.session_dir else "",
            "reply": (reply or "")[:400],
            "verify": self._verify(steps),
        }
        try:
            path = self.store.save(wf)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "steps": len(steps)}
        return {"ok": True, "path": path, "name": wf["name"],
                "steps": len(steps), "duration": dur,
                "session": wf["session_dir"], "tags": tags}

    def cancel(self):
        if self.rec is not None:
            try:
                self.rec.stop()
            except Exception:
                pass
            self.rec = None
        self.steps = []
        self._cleanup_session()

    def _cleanup_session(self):
        """Throw the raw session folder away (noisy auto-record runs)."""
        path = self.session_dir
        self.session_dir = ""
        if not path:
            return
        try:
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass

    # ── helpers ─────────────────────────────────────────────────────────
    def active(self):
        return self.rec is not None

    def _tags(self, steps=None):
        steps = steps if steps is not None else self.steps
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#._-]{2,}", self.task.lower())
        stop = {"how", "the", "and", "for", "with", "please", "make", "create",
                "build", "karo", "kar", "hai", "ka", "ki", "ko", "bana", "banao"}
        tags = []
        for w in words:
            if w in stop or w in tags:
                continue
            tags.append(w)
            if len(tags) >= 8:
                break
        for st in steps:
            app = (st.get("app") or "").strip()
            if not app or len(app) > 40:
                continue
            first = app.split(" - ")[0].split(" | ")[0].strip()
            if not first or len(first) < 3:
                continue
            if first.lower() in [t.lower() for t in tags]:
                continue
            tags.append(first)
        return tags[:10]

    def _verify(self, steps=None):
        steps = steps if steps is not None else self.steps
        out = []
        for st in steps:
            cmd = (st.get("command") or "").strip()
            if cmd and ("check" in cmd.lower() or "list" in cmd.lower()
                        or "verify" in cmd.lower() or "--version" in cmd.lower()):
                out.append(cmd)
        return out[:5]


def _data_path(ak):
    try:
        cfg = getattr(ak, "cfg", None) or {}
        return cfg.get("data_path") or r"D:\YOUR_DATA_PATH"
    except Exception:
        return r"D:\YOUR_DATA_PATH"
