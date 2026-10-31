"""Local, low-memory features for AK — all offline, no API keys required.

Features:
  1. Task/Todo Manager    – SQLite-backed task list
  2. Local Calculator     – Safe math expression evaluator
  3. Local Document Search – Recursive file content search
  4. Enhanced Notes       – Tagged notes with list/search
  5. Network Info         – IP config, WiFi, public IP
  6. Process Manager      – List/kill processes via psutil
  7. Pomodoro Timer       – Simple focus timer
  8. Reminder / Timer     – Threaded offline reminders
  9. Password Tools       – Strong password & PIN generator (secrets)
 10. Hash Tools           – MD5 / SHA hashes, Base64 encode/decode
 11. Number Tools         – Binary / hex / octal / decimal converter
 12. Text Tools           – Word, character & reading-time stats
 13. Random Tools         – Coin flip, dice roll, random numbers
 14. Stopwatch            – Threaded elapsed-time stopwatch
 15. Habit Tracker        – SQLite habits with daily log & streaks
 16. Storage Analyzer     – Biggest folders & largest files (offline)
"""

from __future__ import annotations

import ast
import base64
import datetime
import hashlib
import json
import math
import operator
import os
import random
import re
import secrets
import socket
import sqlite3
import string
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from .config import get_data_dir

_HOME = Path.home()
_DATA_DIR = Path(get_data_dir())
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ────────────────────────────────────────────────────────────────
#  1. TASK / TODO MANAGER (SQLite)
# ────────────────────────────────────────────────────────────────


class TaskManager:
    def __init__(self, db_path: str = ""):
        self._db = db_path or str(_DATA_DIR / "tasks.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tasks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "text TEXT NOT NULL,"
                "done INTEGER DEFAULT 0,"
                "created TEXT NOT NULL"
                ")"
            )

    def add(self, text: str) -> str:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT INTO tasks (text, created) VALUES (?, ?)",
                (text, datetime.datetime.now().isoformat()),
            )
        return f"Task added: {text}"

    def list(self) -> str:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                "SELECT id, text, done FROM tasks ORDER BY done, id"
            ).fetchall()
        if not rows:
            return "No tasks found."
        lines = []
        for tid, text, done in rows:
            mark = "[x]" if done else "[ ]"
            lines.append(f"  {tid}. {mark} {text}")
        return "Tasks:\n" + "\n".join(lines)

    def done(self, task_id: int) -> str:
        with sqlite3.connect(self._db) as conn:
            cur = conn.execute("UPDATE tasks SET done=1 WHERE id=?", (task_id,))
            if cur.rowcount == 0:
                return f"Task {task_id} not found."
        return f"Task {task_id} marked done."

    def remove(self, task_id: int) -> str:
        with sqlite3.connect(self._db) as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            if cur.rowcount == 0:
                return f"Task {task_id} not found."
        return f"Task {task_id} removed."

    def count(self) -> Tuple[str, int]:
        with sqlite3.connect(self._db) as conn:
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE done=0"
            ).fetchone()[0]
        return f"You have {pending} pending tasks out of {total} total.", pending


# ────────────────────────────────────────────────────────────────
#  2. LOCAL CALCULATOR (safe ast-based eval)
# ────────────────────────────────────────────────────────────────

_CALC_OPERATORS: dict = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_CALC_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "pi": math.pi,
    "e": math.e,
}


_WORD_FUNC = {"sqrt": "sqrt", "log": "log", "log10": "log10", "sin": "sin",
              "cos": "cos", "tan": "tan", "abs": "abs", "ceil": "ceil",
              "floor": "floor", "round": "round"}

_WORD_OPS = (
    ("multiplied by", "*"),
    ("divided by", "/"),
    ("to the power", "**"),
    ("times", "*"),
    ("plus", "+"),
    ("minus", "-"),
)


def _rewrite_word_ops(expr: str) -> str:
    """Rewrite plain-English operator words into symbols, e.g.
    '25 times 4' → '25 * 4'.  Only ever touches operator phrases, never
    numbers/functions, so valid expressions are unchanged."""
    for word, op in _WORD_OPS:
        expr = re.sub(rf"(?<!\w){word}(?!\w)", op, expr, flags=re.IGNORECASE)
    expr = re.sub(r"(?i)(?<=\d)(\s*)x(\s*)(?=\d)", r" * ", expr)
    return expr


def _normalize_expr(expr: str) -> str:
    """Convert human math phrasing like 'sqrt 144' or '144 sqrt' into
    a call-style expression like 'sqrt(144)'."""
    expr = expr.strip()
    expr = _rewrite_word_ops(expr)
    for word, func in _WORD_FUNC.items():
        # "sqrt 144"  →  sqrt(144)
        m = re.match(rf"^({word})\s+([\d.].*)$", expr, re.IGNORECASE)
        if m:
            expr = f"{func}({m.group(2).strip()})"
            break
        # "144 sqrt"  →  sqrt(144)
        m = re.match(rf"^([\d.].*)\s+({word})$", expr, re.IGNORECASE)
        if m:
            expr = f"{func}({m.group(1).strip()})"
            break
        # "sqrt of 144"  →  sqrt(144)
        m = re.match(rf"^({word})\s+of\s+([\d.].*)$", expr, re.IGNORECASE)
        if m:
            expr = f"{func}({m.group(2).strip()})"
            break
    return expr


def _safe_eval(expr: str) -> float:
    """Evaluate a math expression safely using AST."""
    tree = ast.parse(expr.strip(), mode="eval")

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value) if isinstance(node.value, (int, float)) else 0.0
        if isinstance(node, ast.BinOp):
            op = _CALC_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op = _CALC_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(_eval(node.operand))
        if isinstance(node, ast.Call):
            func_name = node.func.id if isinstance(node.func, ast.Name) else ""
            func = _CALC_FUNCTIONS.get(func_name)
            if func is None:
                raise ValueError(f"Unknown function: {func_name}")
            args = [_eval(a) for a in node.args]
            return func(*args)
        if isinstance(node, ast.Name):
            val = _CALC_FUNCTIONS.get(node.id)
            if val is None:
                raise ValueError(f"Unknown constant: {node.id}")
            return val if isinstance(val, (int, float)) else 0.0
        raise ValueError(f"Unsupported expression: {type(node).__name__}")

    return _eval(tree)


class Calculator:
    @staticmethod
    def calculate(expr: str) -> str:
        expr = _normalize_expr(expr).strip().strip("=").strip()
        try:
            result = _safe_eval(expr)
            if result == int(result):
                return f"{expr} = {int(result)}"
            return f"{expr} = {result:.6f}".rstrip("0").rstrip(".")
        except Exception as exc:
            return f"Could not calculate: {exc}"


# ────────────────────────────────────────────────────────────────
#  3. LOCAL DOCUMENT SEARCH (offline file content search)
# ────────────────────────────────────────────────────────────────

_SEARCHABLE_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".log",
    ".csv", ".bat", ".ps1", ".sh", ".env", ".gitignore",
}


class DocumentSearch:
    def __init__(self, search_root: str = ""):
        self._root = search_root or str(_HOME / "Documents")

    def search_content(self, query: str, max_results: int = 5) -> str:
        """Search file contents for a query string."""
        query = query.lower()
        results = []
        try:
            for dirpath, _, filenames in os.walk(self._root):
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in _SEARCHABLE_EXTENSIONS:
                        continue
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                            for lineno, line in enumerate(fh, 1):
                                if query in line.lower():
                                    short = line.strip()[:120]
                                    results.append(
                                        f"{os.path.relpath(fpath, self._root)}:{lineno} - {short}"
                                    )
                                    if len(results) >= max_results:
                                        break
                    except Exception:
                        continue
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break
        except Exception as exc:
            return f"Search error: {exc}"
        if not results:
            return f"No matches found for '{query}' in Documents."
        return f"Found in Documents:\n" + "\n".join(results)

    def find_file(self, name: str, max_results: int = 5) -> str:
        """Find files by name (case-insensitive partial match)."""
        name = name.lower()
        results = []
        roots = [
            str(_HOME / "Documents"),
            str(_HOME / "Desktop"),
            str(_HOME),
        ]
        try:
            for root in roots:
                if not os.path.isdir(root):
                    continue
                for dirpath, _, filenames in os.walk(root):
                    for fname in filenames:
                        if name in fname.lower():
                            results.append(os.path.join(dirpath, fname))
                            if len(results) >= max_results:
                                break
                    if len(results) >= max_results:
                        break
        except Exception as exc:
            return f"Find error: {exc}"
        if not results:
            return f"No files matching '{name}' found."
        return "Found files:\n" + "\n".join(results)


# ────────────────────────────────────────────────────────────────
#  4. ENHANCED NOTES (JSON-based with tags)
# ────────────────────────────────────────────────────────────────


class EnhancedNotes:
    def __init__(self, store_path: str = ""):
        self._path = store_path or str(_DATA_DIR / "notes.json")
        self._notes: list = []
        self._load()

    def _load(self):
        try:
            if os.path.isfile(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    self._notes = json.load(f)
        except Exception:
            self._notes = []

    def _save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._notes, f, indent=2, ensure_ascii=False)

    def add(self, text: str, tags: Optional[List[str]] = None) -> str:
        entry = {
            "id": len(self._notes) + 1,
            "text": text,
            "tags": tags or [],
            "created": datetime.datetime.now().isoformat(),
        }
        self._notes.append(entry)
        self._save()
        tag_str = f" with tags: {', '.join(tags)}" if tags else ""
        return f"Note saved{tag_str}. (ID {entry['id']})"

    def list(self, tag: str = "") -> str:
        notes = self._notes
        if tag:
            notes = [n for n in notes if tag in n.get("tags", [])]
        if not notes:
            return "No notes found." if not tag else f"No notes with tag '{tag}'."
        lines = []
        for n in notes[-10:]:
            tid = n["id"]
            text = n["text"][:80]
            tags = f" [{', '.join(n['tags'])}]" if n.get("tags") else ""
            lines.append(f"  {tid}. {text}{tags}")
        return "Recent notes:\n" + "\n".join(lines)

    def search(self, query: str) -> str:
        query = query.lower()
        matches = [n for n in self._notes if query in n["text"].lower()]
        if not matches:
            return f"No notes matching '{query}'."
        lines = []
        for n in matches[-5:]:
            tid = n["id"]
            text = n["text"][:100]
            lines.append(f"  {tid}. {text}")
        return f"Notes matching '{query}':\n" + "\n".join(lines)

    def remove(self, note_id: int) -> str:
        for i, n in enumerate(self._notes):
            if n["id"] == note_id:
                self._notes.pop(i)
                self._save()
                return f"Note {note_id} removed."
        return f"Note {note_id} not found."


# ────────────────────────────────────────────────────────────────
#  5. NETWORK INFO (local commands)
# ────────────────────────────────────────────────────────────────


class NetworkInfo:
    @staticmethod
    def get_info() -> str:
        parts = []
        try:
            hostname = socket.gethostname()
            parts.append(f"Hostname: {hostname}")
        except Exception:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            parts.append(f"Local IP: {local_ip}")
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-NetAdapter -Name '*Wi-Fi*','*Wireless*','*Ethernet*' | Get-NetIPAddress -AddressFamily IPv4).IPAddress"],
                capture_output=True, text=True, timeout=10,
            )
            ip = result.stdout.strip()
            if ip:
                parts.append(f"Network IP: {ip}")
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-NetConnectionProfile).Name"],
                capture_output=True, text=True, timeout=10,
            )
            wifi = result.stdout.strip()
            if wifi:
                parts.append(f"Connected to: {wifi}")
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=10,
            )
            adapters = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if adapters:
                parts.append(f"Active adapters: {', '.join(adapters)}")
        except Exception:
            pass
        return "\n".join(parts) if parts else "Network info unavailable."

    @staticmethod
    def ping(host: str = "8.8.8.8") -> str:
        try:
            result = subprocess.run(
                ["ping", "-n", "1", host],
                capture_output=True, text=True, timeout=10,
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            relevant = [l for l in lines if "time" in l.lower() or "TTL" in l]
            if relevant:
                return f"Ping {host}: {relevant[0]}"
            if result.returncode == 0:
                return f"{host} is reachable."
            return f"{host} is not reachable."
        except Exception as exc:
            return f"Ping failed: {exc}"


# ────────────────────────────────────────────────────────────────
#  6. PROCESS MANAGER (psutil)
# ────────────────────────────────────────────────────────────────


class ProcessManager:
    @staticmethod
    def list_processes(sort_by: str = "cpu", count: int = 5) -> str:
        try:
            import psutil
        except ImportError:
            return "psutil not installed. Run: pip install psutil"
        try:
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    procs.append(p.info)
                except Exception:
                    continue
            if sort_by == "memory":
                key = lambda x: x.get("memory_percent", 0) or 0  # noqa: E731
            else:
                key = lambda x: x.get("cpu_percent", 0) or 0  # noqa: E731
            procs.sort(key=key, reverse=True)
            lines = [f"Top {count} processes by {sort_by}:"]
            for p in procs[:count]:
                name = p["name"] or "?"
                pid = p["pid"]
                cpu = p.get("cpu_percent") or 0
                mem = p.get("memory_percent") or 0
                lines.append(f"  {pid:>6}  {cpu:>5.1f}% CPU {mem:>5.1f}% MEM  {name}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Process list failed: {exc}"

    @staticmethod
    def kill(target: str) -> str:
        try:
            import psutil
        except ImportError:
            return "psutil not installed."
        try:
            # Try as PID
            if target.isdigit():
                pid = int(target)
                p = psutil.Process(pid)
                name = p.name()
                p.terminate()
                return f"Process {name} (PID {pid}) terminated."
            # Try as name
            killed = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if target.lower() in (p.info["name"] or "").lower():
                        p.terminate()
                        killed.append(f"{p.info['name']} (PID {p.info['pid']})")
                except Exception:
                    continue
            if killed:
                return f"Killed: {', '.join(killed)}"
            return f"No running process matching '{target}'."
        except psutil.NoSuchProcess:
            return f"Process {target} not found."
        except Exception as exc:
            return f"Could not kill process: {exc}"


# ────────────────────────────────────────────────────────────────
#  7. POMODORO TIMER
# ────────────────────────────────────────────────────────────────


@dataclass
class PomodoroState:
    running: bool = False
    minutes: int = 25
    remaining: int = 0
    thread: Optional[threading.Thread] = None
    callback: Any = None
    _timer: Any = None


_pomodoro = PomodoroState()


class PomodoroTimer:
    @staticmethod
    def start(minutes: int = 25) -> str:
        if _pomodoro.running:
            return f"Timer already running with {_pomodoro.remaining // 60} minutes left."
        _pomodoro.minutes = minutes
        _pomodoro.remaining = minutes * 60
        _pomodoro.running = True
        _pomodoro.thread = threading.Thread(target=_timer_loop, daemon=True)
        _pomodoro.thread.start()
        return f"Pomodoro timer started for {minutes} minutes."

    @staticmethod
    def stop() -> str:
        if not _pomodoro.running:
            return "No timer running."
        _pomodoro.running = False
        _pomodoro.remaining = 0
        return "Timer stopped."

    @staticmethod
    def status() -> str:
        if not _pomodoro.running:
            return "No timer running."
        mins = _pomodoro.remaining // 60
        secs = _pomodoro.remaining % 60
        return f"Timer: {mins}:{secs:02d} remaining."

    @staticmethod
    def set_callback(cb) -> None:
        _pomodoro.callback = cb


def _timer_loop():
    while _pomodoro.running and _pomodoro.remaining > 0:
        time.sleep(1)
        _pomodoro.remaining -= 1
    if _pomodoro.running:
        _pomodoro.running = False
        if _pomodoro.callback:
            _pomodoro.callback("Pomodoro complete! Time for a break.")


# ── Shortcut instance ───────────────────────────────────────────

def _get_tasks() -> TaskManager:
    return TaskManager()


def _get_notes() -> EnhancedNotes:
    return EnhancedNotes()


# ────────────────────────────────────────────────────────────────
#  8. REMINDER / TIMER (threaded, fully offline)
# ────────────────────────────────────────────────────────────────


@dataclass
class _Reminder:
    trigger_time: float
    message: str
    seconds: int


_reminders: List[_Reminder] = []
_reminder_callback: Optional[Callable[[str], None]] = None
_reminder_lock = threading.Lock()


class ReminderManager:
    @staticmethod
    def set_callback(cb: Optional[Callable[[str], None]]) -> None:
        global _reminder_callback
        _reminder_callback = cb

    @staticmethod
    def add(message: str, seconds: int) -> str:
        seconds = max(1, int(seconds))
        with _reminder_lock:
            _reminders.append(_Reminder(time.time() + seconds, message, seconds))
            threading.Thread(target=_reminder_loop, daemon=True).start()
        return f"Reminder set: '{message}' in {seconds} seconds."

    @staticmethod
    def pending_count() -> int:
        with _reminder_lock:
            t = time.time()
            return sum(1 for r in _reminders if r.trigger_time > t)

    @staticmethod
    def status() -> str:
        n = ReminderManager.pending_count()
        if n == 0:
            return "No pending reminders."
        return f"{n} pending reminder{'s' if n != 1 else ''}."


def _reminder_loop() -> None:
    while True:
        due = None
        with _reminder_lock:
            now = time.time()
            if not _reminders:
                return
            due = sorted(_reminders, key=lambda r: r.trigger_time)[0]
            if due.trigger_time <= now:
                _reminders.remove(due)
        if due and due.trigger_time <= time.time():
            if _reminder_callback:
                try:
                    _reminder_callback(f"Reminder: {due.message}")
                except Exception:
                    print(f"[reminder] {due.message}")
            else:
                print(f"[reminder] {due.message}")
            continue
        time.sleep(1)


# ────────────────────────────────────────────────────────────────
#  9. PASSWORD TOOLS (offline, cryptographically strong)
# ────────────────────────────────────────────────────────────────


class PasswordTools:
    _CHARS = string.ascii_letters + string.digits + "!@#$%^&*_-+=?"

    @staticmethod
    def password(length: int = 16) -> str:
        length = max(6, min(int(length), 128))
        pw = "".join(secrets.choice(PasswordTools._CHARS) for _ in range(length))
        return f"Generated password ({length} characters): {pw}"

    @staticmethod
    def pin(digits: int = 6) -> str:
        digits = max(4, min(int(digits), 16))
        pin = "".join(secrets.choice(string.digits) for _ in range(digits))
        return f"Generated PIN ({digits} digits): {pin}"


# ────────────────────────────────────────────────────────────────
#  10. HASH TOOLS (offline md5 / sha / base64)
# ────────────────────────────────────────────────────────────────


class HashTools:
    _ALGOS = {"md5": "md5", "sha1": "sha1", "sha256": "sha256", "sha512": "sha512"}

    @staticmethod
    def hash_text(text: str, algo: str = "sha256") -> str:
        algo = algo.lower().replace("-", "").replace(" ", "")
        if algo not in HashTools._ALGOS:
            return f"Unsupported hash: {algo}. Use md5, sha1, sha256 or sha512."
        digest = hashlib.new(algo, text.encode("utf-8")).hexdigest()
        return f"{algo.upper()} hash of '{text}' is {digest}"

    @staticmethod
    def base64_encode(text: str) -> str:
        return f"Base64 of '{text}' is {base64.b64encode(text.encode('utf-8')).decode()}"

    @staticmethod
    def base64_decode(data: str) -> str:
        try:
            decoded = base64.b64decode(data.strip()).decode("utf-8", errors="replace")
            return f"Decoded: {decoded}"
        except Exception as exc:
            return f"Could not decode base64: {exc}"


# ────────────────────────────────────────────────────────────────
#  11. NUMBER TOOLS (binary / hex / octal / decimal offline)
# ────────────────────────────────────────────────────────────────


class NumberTools:
    _BASES = {
        "binary": 2, "bin": 2, "base2": 2, "base 2": 2,
        "octal": 8, "oct": 8, "base8": 8, "base 8": 8,
        "decimal": 10, "dec": 10, "base10": 10, "base 10": 10,
        "hex": 16, "hexadecimal": 16, "hexa": 16, "base16": 16, "base 16": 16,
    }

    @staticmethod
    def _fmt(value: int, base: int) -> str:
        if base == 2:
            return bin(value)[2:]
        if base == 8:
            return oct(value)[2:]
        if base == 16:
            return hex(value)[2:]
        return str(value)

    @staticmethod
    def convert(value: str, from_base: str, to_base: str) -> str:
        fb = NumberTools._BASES.get(from_base.lower().strip())
        tb = NumberTools._BASES.get(to_base.lower().strip())
        if fb is None:
            return f"Don't know base '{from_base}'. Try binary, octal, decimal or hex."
        if tb is None:
            return f"Don't know base '{to_base}'. Try binary, octal, decimal or hex."
        raw = value.strip().lower()
        try:
            if raw.startswith("0x"):
                raw = raw[2:]
            elif raw.startswith("0b"):
                raw = raw[2:]
            elif raw.startswith("0o"):
                raw = raw[2:]
            num = int(raw, fb)
        except ValueError:
            return f"'{value}' is not a valid {from_base} number."
        return f"{value} in {from_base} is {NumberTools._fmt(num, tb)} in {to_base}."


# ────────────────────────────────────────────────────────────────
#  12. TEXT TOOLS (offline word / char / reading-time stats)
# ────────────────────────────────────────────────────────────────


class TextTools:
    @staticmethod
    def analyze(text: str) -> str:
        words = len(re.findall(r"\b[\w'-]+\b", text))
        chars = len(text)
        letters = sum(c.isalpha() for c in text)
        digits = sum(c.isdigit() for c in text)
        spaces = text.count(" ")
        lines = text.count("\n") + (1 if text else 0)
        minutes = round(words / 200.0, 1)
        return (f"Text stats: {words} words, {chars} characters, "
                f"{letters} letters, {digits} digits, {spaces} spaces, "
                f"{lines} lines. Reading time ~{minutes} minute{'s' if minutes != 1 else ''}.")


# ────────────────────────────────────────────────────────────────
#  13. RANDOM TOOLS (coin flip, dice, random numbers — offline)
# ────────────────────────────────────────────────────────────────


class RandomTools:
    @staticmethod
    def coin() -> str:
        return f"Coin flipped: {random.choice(['Heads', 'Tails'])}."

    @staticmethod
    def dice(sides: int = 6) -> str:
        sides = max(2, min(int(sides), 1000))
        return f"Dice rolled: {random.randint(1, sides)} out of {sides}."

    @staticmethod
    def number(low: int = 1, high: int = 100) -> str:
        low, high = min(low, high), max(low, high)
        return f"Your random number: {random.randint(low, high)}."


# ────────────────────────────────────────────────────────────────
#  14. STOPWATCH (threaded elapsed-time, offline)
# ────────────────────────────────────────────────────────────────


_stopwatch = {"running": False, "start": 0.0}


class Stopwatch:
    @staticmethod
    def start() -> str:
        if _stopwatch["running"]:
            return "Stopwatch is already running."
        _stopwatch["running"] = True
        _stopwatch["start"] = time.monotonic()
        return "Stopwatch started."

    @staticmethod
    def stop() -> str:
        if not _stopwatch["running"]:
            return "No stopwatch running."
        elapsed = time.monotonic() - _stopwatch["start"]
        _stopwatch["running"] = False
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        return f"Stopwatch stopped at {h:02d}:{m:02d}:{s:02d}."

    @staticmethod
    def status() -> str:
        if not _stopwatch["running"]:
            return "No stopwatch running."
        elapsed = time.monotonic() - _stopwatch["start"]
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        return f"Stopwatch: {h:02d}:{m:02d}:{s:02d} elapsed."


# ────────────────────────────────────────────────────────────────
#  15. HABIT TRACKER (SQLite, offline daily log + streaks)
# ────────────────────────────────────────────────────────────────


class HabitTracker:
    def __init__(self, db_path: str = ""):
        self._db = db_path or str(_DATA_DIR / "habits.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS habits ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "name TEXT NOT NULL UNIQUE,"
                "created TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS habit_log ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "habit_id INTEGER NOT NULL,"
                "day TEXT NOT NULL,"
                "UNIQUE(habit_id, day)"
                ")"
            )

    def _day(self) -> str:
        return datetime.date.today().isoformat()

    def add(self, name: str) -> str:
        name = name.strip().strip(".!?")
        with sqlite3.connect(self._db) as conn:
            try:
                conn.execute(
                    "INSERT INTO habits (name, created) VALUES (?, ?)",
                    (name, datetime.datetime.now().isoformat()),
                )
                return f"Habit added: {name}. Say 'log habit {name}' to check it off."
            except sqlite3.IntegrityError:
                return f"Habit '{name}' already exists."

    def log(self, name: str) -> str:
        today = self._day()
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT id, name FROM habits WHERE lower(name)=lower(?)", (name,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO habits (name, created) VALUES (?, ?)",
                    (name, datetime.datetime.now().isoformat()),
                )
                hid, hname = conn.execute(
                    "SELECT id, name FROM habits WHERE lower(name)=lower(?)", (name,)
                ).fetchone()
            else:
                hid, hname = row
            try:
                conn.execute(
                    "INSERT INTO habit_log (habit_id, day) VALUES (?, ?)", (hid, today)
                )
                return f"Habit logged: {hname} for today. Keep going!"
            except sqlite3.IntegrityError:
                return f"Habit '{hname}' is already logged for today."

    def _streak(self, hid: int) -> int:
        with sqlite3.connect(self._db) as conn:
            days = sorted(
                r[0] for r in conn.execute(
                    "SELECT day FROM habit_log WHERE habit_id=? ORDER BY day DESC", (hid,)
                ).fetchall()
            )
        streak = 0
        cursor = datetime.date.today()
        logged = set(days)
        while cursor.isoformat() in logged:
            streak += 1
            cursor -= datetime.timedelta(days=1)
        return streak

    def summary(self) -> str:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute("SELECT id, name FROM habits ORDER BY id").fetchall()
        if not rows:
            return "No habits tracked yet. Say: add habit reading, or log habit exercise."
        today = self._day()
        lines = []
        for hid, name in rows:
            streak = self._streak(hid)
            done = f" • done today" if self._checked(hid, today) else ""
            lines.append(f"  {name}: streak {streak} day{'s' if streak != 1 else ''}{done}")
        return "Habits:\n" + "\n".join(lines)

    def _checked(self, hid: int, day: str) -> bool:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT 1 FROM habit_log WHERE habit_id=? AND day=?", (hid, day)
            ).fetchone()
        return row is not None


# ────────────────────────────────────────────────────────────────
#  16. STORAGE ANALYZER (biggest folders & largest files, offline)
# ────────────────────────────────────────────────────────────────


def _fmt_bytes(size: int) -> str:
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


class StorageAnalyzer:
    _ROOTS = [
        str(_HOME / "Desktop"),
        str(_HOME / "Documents"),
        str(_HOME / "Downloads"),
        str(_HOME / "Pictures"),
        str(_HOME / "Videos"),
        str(_HOME / "Music"),
    ]

    @staticmethod
    def _folder_sizes(max_depth: int = 2) -> List[Tuple[str, int]]:
        sizes: dict = {}
        try:
            for root in StorageAnalyzer._ROOTS:
                if not os.path.isdir(root):
                    continue
                base = os.path.basename(root)
                for dirpath, dirnames, filenames in os.walk(root):
                    rel = os.path.relpath(dirpath, root)
                    depth = 0 if rel == "." else rel.count(os.sep) + 1
                    if depth > max_depth:
                        dirnames[:] = []
                        continue
                    total = 0
                    for fn in filenames:
                        try:
                            total += os.path.getsize(os.path.join(dirpath, fn))
                        except OSError:
                            continue
                    key = os.path.basename(dirpath) or base
                    if key:
                        sizes[key] = sizes.get(key, 0) + total
        except Exception:
            pass
        return sorted(sizes.items(), key=lambda x: x[1], reverse=True)

    @staticmethod
    def top_folders(count: int = 5) -> str:
        items = [it for it in StorageAnalyzer._folder_sizes() if it[1] > 0][:count]
        if not items:
            return "Could not scan folders."
        lines = [f"Top {len(items)} folders by size:"]
        for name, size in items:
            lines.append(f"  {name}: {_fmt_bytes(size)}")
        return "\n".join(lines)

    @staticmethod
    def big_files(count: int = 5) -> str:
        files: List[Tuple[int, str]] = []
        try:
            for root in StorageAnalyzer._ROOTS:
                if not os.path.isdir(root):
                    continue
                for dirpath, _dn, filenames in os.walk(root):
                    if len(filenames) > 3000 and _dn:
                        _dn[:] = []
                    for fn in filenames:
                        try:
                            files.append(
                                (os.path.getsize(os.path.join(dirpath, fn)), os.path.join(dirpath, fn))
                            )
                        except OSError:
                            continue
        except Exception:
            pass
        files.sort(reverse=True)
        if not files:
            return "Could not scan files."
        lines = [f"Largest {min(count, len(files))} files:"]
        for size, path in files[:count]:
            lines.append(f"  {_fmt_bytes(size)}  {os.path.basename(path)}")
        return "\n".join(lines)
