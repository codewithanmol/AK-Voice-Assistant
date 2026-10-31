"""workflow_learn.py — AK ki seekh-ne wali memory.

A recorded session (screen_recorder) is turned into a human readable
playbook (`<slug>.md`) that is ALSO machine readable, so AK can replay the
same steps on its own the next time the same work comes back.

Store layout (inside the AK data folder):
    workflows/<slug>.md          the playbook (front matter + steps + json block)
    workflows/index.json         fast list/search index
    workflows/sessions/<slug>_<ts>/   frames + recording.gif + timeline.json
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import time

WF_DIRNAME = "workflows"

# tiny synonym map so "setup" finds "install", "delete" finds "uninstall"...
_SYNONYMS = {
    "setup": {"install", "installing", "configure"},
    "install": {"setup", "installing", "download"},
    "remove": {"delete", "uninstall", "erase"},
    "uninstall": {"remove", "delete"},
    "delete": {"remove", "uninstall"},
    "open": {"launch", "start", "run"},
    "launch": {"open", "start"},
    "make": {"create", "build"},
    "create": {"make", "build"},
    "build": {"make", "create"},
    "show": {"open", "display"},
    "website": {"site", "webpage", "web"},
    "app": {"application", "software", "program"},
}

_STOP = {"the", "a", "an", "to", "for", "of", "in", "on", "my", "me", "please",
         "what", "how", "why", "who", "when", "where", "which", "is", "are",
         "was", "do", "does", "did", "can", "will", "would", "should", "tell",
         "explain", "show", "give", "help", "need", "want", "just", "also",
         "kar", "karo", "kar", "do", "please", "hai", "ko", "ka", "ki", "ke",
         "aur", "or", "bhi", "ab", "abhi", "sir", "kya", "kaise", "batao",
         "bataye", "kro", "krd", "karke", "karna", "karte"}


def _tokens(text):
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in _STOP or len(w) < 2:
            continue
        out.add(w)
        for s in _SYNONYMS.get(w, ()):  # expand synonyms
            out.add(s)
    return out


def _slug(text, maxlen=48):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if not s:
        s = "workflow"
    return s[:maxlen].strip("-")


def workflows_dir(data_path):
    return os.path.join(data_path or r"D:\YOUR_DATA_PATH", WF_DIRNAME)


def _steps_of(wf):
    """Steps of a workflow - always a list (index entries only carry a count)."""
    st = (wf or {}).get("steps")
    return st if isinstance(st, list) else []


# ── the playbook (.md) ───────────────────────────────────────────────────
def build_markdown(wf: dict) -> str:
    """Render a workflow dict as the .md playbook."""
    steps = _steps_of(wf)
    name = wf.get("name") or _slug(wf.get("title") or wf.get("task") or "workflow")
    title = wf.get("title") or wf.get("task") or name
    created = wf.get("created") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    tags = wf.get("tags") or []
    dur = wf.get("duration") or 0
    session = wf.get("session_dir") or ""
    rel_session = wf.get("session_rel") or ""

    L = []
    L.append("---")
    L.append(f"name: {name}")
    L.append(f"title: {title}")
    L.append(f"task: {wf.get('task') or title}")
    if tags:
        L.append("tags: " + ", ".join(tags))
    L.append(f"created: {created}")
    L.append(f"source: {wf.get('source') or 'ak'}")
    L.append(f"duration: {dur}s")
    L.append(f"steps: {len(steps)}")
    if rel_session:
        L.append(f"session: {rel_session}")
    L.append("---")
    L.append("")
    L.append(f"# {title}")
    L.append("")
    L.append(f"**Goal:** {wf.get('task') or title}")
    L.append(f"**Learned:** {created} · {len(steps)} steps · {dur}s · "
             f"source: {wf.get('source') or 'ak'}")
    if wf.get("outcome"):
        L.append(f"**Outcome:** {wf['outcome']}")
    L.append("")
    L.append("> Replay: *\"ak kaam kar do \\\"\" + the goal + \"\\\"\"* - AK finds "
             "this playbook and follows the same steps on its own.")
    L.append("")

    L.append("## When to use")
    L.append(f"- The user asks for: *{wf.get('task') or title}*")
    if tags:
        L.append(f"- Keywords: {', '.join(tags)}")
    L.append("")

    L.append("## Steps")
    L.append("")
    if not steps:
        L.append("_No steps were captured._")
        L.append("")
    for st in steps:
        L.append(f"{st.get('n', 0)}. **{st.get('title') or 'step'}**")
        if st.get("kind"):
            L.append(f"   - kind: `{st['kind']}`")
        if st.get("command"):
            L.append(f"   - command: `{st['command']}`")
        if st.get("app"):
            L.append(f"   - app: `{st['app']}`")
        if st.get("detail"):
            L.append(f"   - note: {st['detail']}")
        if st.get("shot"):
            L.append(f"   - shot: [{st['shot']}](frames/{st['shot']})")
        L.append("")

    ver = wf.get("verify") or []
    L.append("## Verification")
    L.append("")
    if ver:
        for v in ver:
            L.append(f"- `{v}`")
    else:
        L.append("- Re-run the last command and check the exit code is 0.")
    L.append("")

    L.append("## Files")
    L.append("")
    if rel_session:
        L.append(f"- session folder: `{rel_session}`")
    L.append("- screen replay: `recording.gif` (in the session folder)")
    L.append("- raw timeline: `timeline.json` (in the session folder)")
    L.append("")

    payload = dict(wf)
    payload["name"] = name
    payload["title"] = title
    payload["created"] = created
    payload["tags"] = tags
    L.append("<!-- ak-workflow-data")
    L.append(json.dumps(payload, indent=2, ensure_ascii=False))
    L.append("ak-workflow-data -->")
    L.append("")
    return "\n".join(L)


def parse_markdown(md: str) -> dict:
    """Read a playbook back (front matter + json block, human steps fallback)."""
    wf: dict = {}
    m = re.search(r"<!--\s*ak-workflow-data\s*\n(.*?)\n\s*ak-workflow-data\s*-->",
                  md or "", re.S)
    if m:
        try:
            wf = json.loads(m.group(1))
        except Exception:
            wf = {}
    fm = re.search(r"^---\n(.*?)\n---", md or "", re.S)
    if fm and not wf:
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k == "tags":
                    wf["tags"] = [t.strip() for t in v.split(",") if t.strip()]
                else:
                    wf[k] = v
    if "steps" not in wf:
        steps = []
        for mm in re.finditer(r"^\s*\d+\.\s+\*\*(.+?)\*\*", md or "", re.M):
            steps.append({"n": len(steps) + 1, "title": mm.group(1).strip(),
                          "kind": "manual", "command": "", "detail": "",
                          "app": "", "shot": ""})
        wf["steps"] = steps
    return wf


# ── the store ────────────────────────────────────────────────────────────
class WorkflowStore:
    def __init__(self, data_path):
        self.data_path = data_path or r"D:\YOUR_DATA_PATH"
        self.dir = workflows_dir(self.data_path)
        self.sessions_dir = os.path.join(self.dir, "sessions")
        self.index_path = os.path.join(self.dir, "index.json")

    def ensure(self):
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(self.sessions_dir, exist_ok=True)
        return self.dir

    # ── save ────────────────────────────────────────────────────────────
    def save(self, wf: dict) -> str:
        self.ensure()
        wf = dict(wf)
        wf.setdefault("name", _slug(wf.get("title") or wf.get("task") or "workflow"))
        wf["name"] = _slug(wf["name"])
        md = build_markdown(wf)
        path = os.path.join(self.dir, f"{wf['name']}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(md)
        wf["path"] = path
        self._index(wf)
        return path

    def _index(self, wf):
        idx = self._read_index()
        idx[wf["name"]] = {
            "name": wf["name"],
            "title": wf.get("title") or wf["name"],
            "task": wf.get("task") or "",
            "tags": wf.get("tags") or [],
            "created": wf.get("created") or "",
            "steps": len(wf.get("steps") or []),
            "path": wf.get("path") or os.path.join(self.dir, f"{wf['name']}.md"),
        }
        try:
            with open(self.index_path, "w", encoding="utf-8") as fh:
                json.dump(idx, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _read_index(self):
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ── read ────────────────────────────────────────────────────────────
    def list(self):
        idx = self._read_index()
        if not idx:
            self._reindex()
            idx = self._read_index()
        return sorted(idx.values(), key=lambda w: w.get("created") or "")

    def _reindex(self):
        try:
            names = [f[:-3] for f in os.listdir(self.dir) if f.endswith(".md")]
        except Exception:
            return
        for n in names:
            try:
                with open(os.path.join(self.dir, n + ".md"), encoding="utf-8") as fh:
                    wf = parse_markdown(fh.read())
            except Exception:
                continue
            wf["name"] = n
            self._index(wf)

    def load(self, name):
        name = _slug(name)
        path = os.path.join(self.dir, f"{name}.md")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                wf = parse_markdown(fh.read())
        except Exception:
            return None
        wf["name"] = name
        wf["path"] = path
        return wf

    def delete(self, name):
        name = _slug(name)
        path = os.path.join(self.dir, f"{name}.md")
        removed = False
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed = True
            except Exception:
                pass
        idx = self._read_index()
        if idx.pop(name, None) is not None:
            removed = True
            try:
                with open(self.index_path, "w", encoding="utf-8") as fh:
                    json.dump(idx, fh, indent=2, ensure_ascii=False)
            except Exception:
                pass
        return removed

    # ── search ──────────────────────────────────────────────────────────
    def search(self, query, limit=5):
        """Rank the playbooks against a free-text request (full workflows)."""
        q = _tokens(query)
        if not q:
            return []
        out = []
        for w in self.list():
            hay = _tokens(" ".join([w.get("title", ""), w.get("task", ""),
                                    " ".join(w.get("tags") or []),
                                    w.get("name", "")]))
            score = len(q & hay) / max(1, len(q))
            if w.get("task") and query and query.strip().lower() == \
                    w["task"].strip().lower():
                score += 1.0
            if score > 0:
                full = self.load(w.get("name")) or w
                full["_score"] = score
                out.append((score, full))
        out.sort(key=lambda t: -t[0])
        return [w for s, w in out[:limit]]


# ── the runner (AK follows the playbook on its own) ──────────────────────
def _run_command(cmd, timeout=180, cwd=None):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "out": out[-1500:],
            "err": err[-800:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": "timeout"}
    except Exception as exc:
        return {"ok": False, "code": -1, "out": "", "err": str(exc)}


def run_workflow(wf: dict, dry_run=False, timeout=180, log=None,
                 stop_flag=None, launch_app=None, open_site=None):
    """Replay a playbook step by step.

    Step kinds:
        command   -> the shell command is executed
        app       -> the app is launched  (needs `launch_app`)
        url/site  -> the page is opened    (needs `open_site`)
        anything else without a command -> reported as a manual action
    A failing step does not abort the whole run - the report shows what broke.
    """
    say = log or (lambda *a, **k: None)
    steps = wf.get("steps")
    if not isinstance(steps, list):
        steps = []
    report = {"ok": True, "ran": [], "failed": [], "skipped": [], "name":
              wf.get("name") or ""}
    for st in steps:
        if stop_flag is not None and stop_flag():
            report["ok"] = False
            report["skipped"].append({"title": st.get("title"), "why": "stopped"})
            break
        title = st.get("title") or "step"
        cmd = (st.get("command") or "").strip()
        kind = (st.get("kind") or "").strip().lower()

        # ── app / url steps ────────────────────────────────────────────
        if not cmd and kind in ("app", "application", "launch", "open_app"):
            target = (st.get("detail") or st.get("app") or "").strip()
            if not target:
                report["skipped"].append({"title": title, "why": "no-target"})
                continue
            if launch_app is None:
                say(f"step {st.get('n')}: {title} (app - no handler)")
                report["skipped"].append({"title": title, "why": "no-handler"})
                continue
            if dry_run:
                say(f"step {st.get('n')}: would open app -> {target}")
                report["skipped"].append({"title": title, "why": "dry-run"})
                continue
            say(f"step {st.get('n')}: {title}")
            try:
                launch_app(target)
                report["ran"].append({"title": title, "app": target, "ok": True})
            except Exception as exc:  # noqa: BLE001
                report["ok"] = False
                report["failed"].append({"title": title, "app": target,
                                         "err": str(exc)})
                say(f"step {st.get('n')} failed: {exc}")
            continue

        if not cmd and kind in ("url", "site", "webpage", "browser"):
            target = (st.get("detail") or st.get("app") or "").strip()
            if not target:
                report["skipped"].append({"title": title, "why": "no-target"})
                continue
            if open_site is None:
                say(f"step {st.get('n')}: {title} (site - no handler)")
                report["skipped"].append({"title": title, "why": "no-handler"})
                continue
            if dry_run:
                say(f"step {st.get('n')}: would open site -> {target}")
                report["skipped"].append({"title": title, "why": "dry-run"})
                continue
            say(f"step {st.get('n')}: {title}")
            try:
                open_site(target)
                report["ran"].append({"title": title, "url": target, "ok": True})
            except Exception as exc:  # noqa: BLE001
                report["ok"] = False
                report["failed"].append({"title": title, "url": target,
                                         "err": str(exc)})
                say(f"step {st.get('n')} failed: {exc}")
            continue

        if not cmd:
            say(f"step {st.get('n')}: {title} (manual)")
            report["skipped"].append({"title": title, "why": "manual"})
            continue
        if dry_run:
            say(f"step {st.get('n')}: would run -> {cmd}")
            report["skipped"].append({"title": title, "why": "dry-run"})
            continue
        say(f"step {st.get('n')}: {title}")
        res = _run_command(cmd, timeout=timeout,
                           cwd=wf.get("workdir") or None)
        report["ran"].append({"title": title, "command": cmd, **res})
        if not res["ok"]:
            report["failed"].append({"title": title, "command": cmd,
                                     "err": res.get("err")})
            report["ok"] = False
            say(f"step {st.get('n')} failed: {res.get('err') or res.get('code')}")
    return report
