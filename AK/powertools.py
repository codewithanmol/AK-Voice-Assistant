"""Power Tools — extra everyday skills for AK (local-first, no API keys).

Adds six feature groups that the core Skills class does not cover:

  1. Weather      — wttr.in (free, no signup) with a disk cache so a cached
                    city still answers when the internet is down.
  2. Git helper   — status / log / diff / branch / commit for a local repo.
  3. Expenses     — SQLite ledger: add, list, monthly total, remove.
  4. Bookmarks    — SQLite URL store: add, list, open, remove.
  5. Text tools   — JSON pretty/minify, URL encode/decode, case transforms,
                    counts, and email/URL/phone extraction.
  6. System doctor— one-shot health verdict across CPU, RAM, disk, battery,
                    uptime and the hottest processes.

Every public method returns a (speak_output, data) tuple so the caller can
speak the first element and log or serialise the second.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Optional, Tuple

from .config import get_data_dir

_UA = {"User-Agent": "curl/8.0 AK-assistant"}


class PowerTools:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._data_dir = get_data_dir()
        self._expense_db = os.path.join(self._data_dir, "expenses.sqlite3")
        self._bookmark_db = os.path.join(self._data_dir, "bookmarks.sqlite3")
        self._weather_cache = os.path.join(self._data_dir, "weather_cache.json")
        self._expense_ready = False
        self._bookmark_ready = False

    # ── 1. Weather ───────────────────────────────────────────────────
    def _read_cache(self) -> dict:
        try:
            with open(self._weather_cache, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_cache(self, city: str, payload: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._weather_cache), exist_ok=True)
            with open(self._weather_cache, "w", encoding="utf-8") as fh:
                json.dump({"city": city, "ts": time.time(), "data": payload}, fh)
        except OSError:
            pass

    @staticmethod
    def _describe(payload: dict) -> str:
        cur = (payload.get("current_condition") or [{}])[0]
        area = ((payload.get("nearest_area") or [{}])[0] or {})
        name = ((area.get("areaName") or [{}])[0] or {}).get("value", "?")
        desc = (cur.get("weatherDesc") or [{}])[0].get("value", "?")
        try:
            temp = round(float(cur.get("temp_C", 0)))
            feels = round(float(cur.get("FeelsLikeC", temp)))
            hum = cur.get("humidity", "?")
            wind = cur.get("windspeedKmph", "?")
        except (TypeError, ValueError):
            return f"{name}: {desc}."
        line = (f"{name}: {desc}, {temp} degrees, feels like {feels}, "
                f"humidity {hum} percent, wind {wind} km/h.")
        forecast = payload.get("weather") or []
        if forecast:
            day = forecast[0]
            hi = (day.get("maxtempC") or "?")
            lo = (day.get("mintempC") or "?")
            chance = max([int(h.get("chanceofrain", 0) or 0)
                          for h in (day.get("hourly") or [])] or [0])
            line += f" Aaj {lo} se {hi} tak, rain ka chance {chance} percent."
        return line

    def weather(self, city: str = "") -> Tuple[str, str]:
        """Current weather for a city (offline from cache when offline)."""
        city = (city or "").strip()
        asked = city
        if not city:
            cached = self._read_cache()
            if not cached:
                return ("City toh batao — jaise 'Delhi ka mausam batao'.", "")
            asked = city = cached.get("city", "")

        url = ("https://wttr.in/" + urllib.parse.quote(city) +
               "?format=j1&lang=hi")
        payload = None
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            if not (payload.get("current_condition")):
                payload = None
        except Exception:
            payload = None

        if payload is not None:
            self._write_cache(city, payload)
            line = self._prefix_city(self._describe(payload), asked)
            return (line + " (live)", line)

        cached = self._read_cache()
        if cached.get("data"):
            age = int(time.time() - float(cached.get("ts", 0)))
            when = "abhi" if age < 3600 else f"{age // 3600} ghante pehle"
            line = self._prefix_city(self._describe(cached["data"]), asked)
            return (line + f" (offline cache, {when})", line)
        return ("Mausam fetch nahi hua — internet check karo, ya thodi der "
                "baad try karo.", "")

    @staticmethod
    def _prefix_city(line: str, asked: str) -> str:
        """wttr.in geocodes to the nearest locality, so re-attach the city the
        user actually asked for: 'Delhi (Karol Bagh): ...'."""
        asked = (asked or "").strip()
        if not asked or ":" not in line:
            return line
        head, rest = line.split(":", 1)
        if asked.lower() in head.lower() or len(asked) < 2:
            return line
        return f"{asked} ({head.strip()}):{rest}"

    # ── 2. Git helper ────────────────────────────────────────────────
    @staticmethod
    def _git_exe() -> Optional[str]:
        return shutil.which("git")

    @classmethod
    def _git(cls, repo: str, *args: str, timeout: int = 20) -> Tuple[int, str]:
        exe = cls._git_exe()
        if not exe:
            return (1, "git iste PC par install nahi hai — "
                       "https://git-scm.com/download/win se install kar lo.")
        repo = os.path.expanduser(repo or ".")
        if not os.path.isdir(repo):
            return (1, f"Path nahi mila: {repo}")
        try:
            p = subprocess.run([exe, *args], cwd=repo, capture_output=True,
                               text=True, timeout=timeout, errors="replace")
        except subprocess.TimeoutExpired:
            return (1, "git command timeout ho gaya.")
        except OSError as exc:
            return (1, f"git launch nahi hua: {exc}")
        out = (p.stdout or "").strip() or (p.stderr or "").strip()
        return (p.returncode, out)

    def _repo_root(self, repo: str) -> Tuple[Optional[str], str]:
        if not self._git_exe():
            return (None, "git iste PC par install nahi hai — "
                          "https://git-scm.com/download/win se install kar lo.")
        if not os.path.isdir(os.path.expanduser(repo or ".")):
            return (None, f"Path nahi mila: {repo}")
        code, out = self._git(repo, "rev-parse", "--show-toplevel")
        if code != 0:
            return (None, f"{repo} koi git repo nahi hai.")
        return (out.strip(), "")

    def _need_repo(self, repo: str) -> Tuple[Optional[str], Optional[str]]:
        """Returns (root, None) when usable, or (None, spoken_error)."""
        root, err = self._repo_root(repo)
        if err:
            return (None, err)
        return (root, None)

    def git_status(self, repo: str = ".") -> Tuple[str, str]:
        """Working-tree status: current branch plus dirty/clean counts."""
        _, err = self._need_repo(repo)
        if err:
            return (err, "")
        _, branch = self._git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        _, st = self._git(repo, "status", "--porcelain")
        lines = [ln for ln in st.splitlines() if ln.strip()]
        if not lines:
            return (f"Repo {repo} clean hai, branch {branch}.", "clean")
        changed = sum(1 for ln in lines if ln[:2].strip() == "M")
        untracked = sum(1 for ln in lines if ln[:2] == "??")
        return (f"Branch {branch}: {len(lines)} files change hain "
                f"({changed} modified, {untracked} untracked).",
                f"{branch}|{len(lines)}|{changed}|{untracked}")

    def git_log(self, repo: str = ".", count: int = 5) -> Tuple[str, str]:
        """Last N commits, one line each."""
        _, err = self._need_repo(repo)
        if err:
            return (err, "")
        _, out = self._git(repo, "log", f"-{max(1, min(count, 20))}",
                           "--pretty=format:%h %an %s")
        if not out:
            return ("Is repo me abhi koi commit nahi hai.", "")
        return ("Latest commits: " + "; ".join(out.splitlines()[:count]), out)

    def git_diff(self, repo: str = ".") -> Tuple[str, str]:
        """Uncommitted diff stat (files + insertions/deletions)."""
        _, err = self._need_repo(repo)
        if err:
            return (err, "")
        _, out = self._git(repo, "diff", "--stat")
        if not out.strip():
            _, staged = self._git(repo, "diff", "--cached", "--stat")
            out = staged
        if not out.strip():
            return (f"{repo} me koi uncommitted change nahi hai.", "")
        tail = out.strip().splitlines()[-1]
        return (f"{repo} ka diff: {tail}", out)

    def git_branch(self, repo: str = ".") -> Tuple[str, str]:
        """Current branch, and how many commits it is ahead/behind upstream."""
        _, err = self._need_repo(repo)
        if err:
            return (err, "")
        _, branch = self._git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        _, counts = self._git(repo, "rev-list", "--left-right", "--count",
                              "@{upstream}...HEAD")
        if counts:
            behind, ahead = (counts.split() + ["?", "?"])[:2]
            return (f"Branch {branch} — {ahead} ahead, {behind} behind "
                    f"upstream.", counts)
        return (f"Branch {branch} (no upstream set).", branch)

    def git_commit(self, repo: str, message: str) -> Tuple[str, str]:
        """Stage everything and commit with the given message."""
        _, err = self._need_repo(repo)
        if err:
            return (err, "")
        if not message.strip():
            return ("Commit message khaali hai — kuch toh likho.", "")
        code, out = self._git(repo, "add", "-A")
        if code != 0:
            return (f"git add fail hua: {out}", "")
        code, out = self._git(repo, "commit", "-m", message.strip())
        if code != 0:
            return (f"Commit nahi hua: {out}", "")
        return (f"Commit ho gaya: {out.splitlines()[0] if out else message}",
                out)

    # ── 3. Expenses ──────────────────────────────────────────────────
    def _exp_conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._expense_db), exist_ok=True)
        conn = sqlite3.connect(self._expense_db)
        conn.row_factory = sqlite3.Row
        if not self._expense_ready:
            conn.execute("CREATE TABLE IF NOT EXISTS expenses ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                         "amount REAL NOT NULL,"
                         "category TEXT DEFAULT 'other',"
                         "note TEXT DEFAULT '',"
                         "ts TEXT NOT NULL)")
            conn.commit()
            self._expense_ready = True
        return conn

    def expense_add(self, amount: Any, category: str = "other",
                    note: str = "") -> Tuple[str, str]:
        """Record one expense. Amount can be '250', '250.50' or '1,200'."""
        try:
            amt = float(re.sub(r"[^\d.\-]", "", str(amount)))
        except (TypeError, ValueError):
            return ("Amount samajh nahi aaya — number do, jaise 250.", "")
        if amt <= 0:
            return ("Amount zero se bada hona chahiye.", "")
        cat = (category or "other").strip().lower()[:24] or "other"
        conn = self._exp_conn()
        cur = conn.execute(
            "INSERT INTO expenses (amount, category, note, ts) "
            "VALUES (?,?,?,?)",
            (amt, cat, (note or "").strip()[:120],
             datetime.datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        nid = cur.lastrowid
        conn.close()
        return (f"{amt:g} rupees {cat} me add ho gaya, entry {nid}.", str(nid))

    def expense_list(self, limit: int = 10) -> Tuple[str, str]:
        """Most recent expenses."""
        conn = self._exp_conn()
        rows = conn.execute(
            "SELECT id, amount, category, note, ts FROM expenses "
            "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 50)),)).fetchall()
        conn.close()
        if not rows:
            return ("Abhi koi expense entry nahi hai.", "")
        parts = [f"{r['amount']:g} {r['category']}"
                 + (f" ({r['note']})" if r["note"] else "")
                 for r in rows]
        return (f"{len(rows)} latest: " + "; ".join(parts), str(len(rows)))

    def expense_total(self, period: str = "month") -> Tuple[str, str]:
        """Total spend for 'today', 'week', 'month' or 'all'."""
        now = datetime.datetime.now()
        p = (period or "month").strip().lower()
        if p in ("today", "aaj"):
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            label = "aaj"
        elif p in ("week", "hafte", "week"):
            start = (now - datetime.timedelta(days=7))
            label = "pichle 7 din"
        elif p in ("all", "total", "sab"):
            start = None
            label = "overall"
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0,
                                microsecond=0)
            label = "is mahine"
        conn = self._exp_conn()
        if start is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount),0), COUNT(*) FROM expenses"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount),0), COUNT(*) FROM expenses "
                "WHERE ts >= ?", (start.isoformat(timespec="seconds"),)
            ).fetchone()
        by_cat = conn.execute(
            "SELECT category, SUM(amount) t FROM expenses "
            + ("" if start is None else "WHERE ts >= ? ") +
            "GROUP BY category ORDER BY t DESC LIMIT 4",
            () if start is None else (start.isoformat(timespec="seconds"),)
        ).fetchall()
        conn.close()
        total, count = float(row[0]), int(row[1])
        if not count:
            return (f"{label} koi expense nahi hai.", "0")
        line = f"{label} total {total:g} rupees, {count} entries."
        if by_cat:
            line += " Top: " + ", ".join(
                f"{r['category']} {float(r['t']):g}" for r in by_cat) + "."
        return (line, f"{total:g}")

    def expense_remove(self, entry_id: int) -> Tuple[str, str]:
        """Delete one expense by its entry number."""
        conn = self._exp_conn()
        cur = conn.execute("DELETE FROM expenses WHERE id = ?", (entry_id,))
        conn.commit()
        conn.close()
        if cur.rowcount:
            return (f"Expense {entry_id} delete ho gaya.", "")
        return (f"Expense {entry_id} nahi mila.", "")

    # ── 4. Bookmarks ─────────────────────────────────────────────────
    def _bm_conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._bookmark_db), exist_ok=True)
        conn = sqlite3.connect(self._bookmark_db)
        conn.row_factory = sqlite3.Row
        if not self._bookmark_ready:
            conn.execute("CREATE TABLE IF NOT EXISTS bookmarks ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                         "title TEXT NOT NULL,"
                         "url TEXT NOT NULL,"
                         "ts TEXT NOT NULL)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS bm_url "
                         "ON bookmarks(url)")
            conn.commit()
            self._bookmark_ready = True
        return conn

    def _normalise_url(self, url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
            u = "https://" + u
        return u

    def bookmark_add(self, title: str, url: str) -> Tuple[str, str]:
        """Store a bookmark. Title is optional if the URL is clear."""
        u = self._normalise_url(url)
        if not u:
            return ("URL toh batao — jaise 'bookmark github.com'.", "")
        t = (title or "").strip() or re.sub(r"^https?://(www\.)?", "",
                                            u).rstrip("/")[:60]
        conn = self._bm_conn()
        try:
            cur = conn.execute(
                "INSERT INTO bookmarks (title, url, ts) VALUES (?,?,?)",
                (t[:80], u, datetime.datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            nid = cur.lastrowid
        except sqlite3.IntegrityError:
            return (f"{u} already bookmarked hai.", "")
        finally:
            conn.close()
        return (f"Bookmark save: {t}, entry {nid}.", str(nid))

    def bookmark_list(self, query: str = "") -> Tuple[str, str]:
        """List bookmarks, optionally filtered by a search term."""
        conn = self._bm_conn()
        if query.strip():
            rows = conn.execute(
                "SELECT id, title, url FROM bookmarks "
                "WHERE title LIKE ? OR url LIKE ? ORDER BY id DESC LIMIT 30",
                (f"%{query.strip()}%", f"%{query.strip()}%")).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, url FROM bookmarks "
                "ORDER BY id DESC LIMIT 30").fetchall()
        conn.close()
        if not rows:
            return (f"'{query}' se koi bookmark nahi mila." if query.strip()
                    else "Abhi koi bookmark nahi hai.", "")
        parts = [f"{r['id']}. {r['title']}" for r in rows]
        return (f"{len(rows)} bookmarks: " + "; ".join(parts[:12]),
                str(len(rows)))

    def bookmark_open(self, query: str) -> Tuple[str, str]:
        """Open a bookmark in the default browser by id or fuzzy title."""
        q = (query or "").strip()
        conn = self._bm_conn()
        row = None
        if q.isdigit():
            row = conn.execute("SELECT title, url FROM bookmarks WHERE id = ?",
                               (int(q),)).fetchone()
        if row is None and q:
            row = conn.execute(
                "SELECT title, url FROM bookmarks WHERE title LIKE ? "
                "OR url LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{q}%", f"%{q}%")).fetchone()
        conn.close()
        if row is None:
            return (f"'{q}' ka koi bookmark nahi mila.", "")
        try:
            import webbrowser
            webbrowser.open(row["url"])
        except Exception as exc:
            return (f"Browser nahi khul paaya: {exc}", "")
        return (f"{row['title']} khol diya — {row['url']}", row["url"])

    def bookmark_remove(self, ref: Any) -> Tuple[str, str]:
        """Remove a bookmark by id."""
        if not str(ref).strip().isdigit():
            return ("Bookmark number (id) do, jaise 'remove bookmark 3'.", "")
        conn = self._bm_conn()
        cur = conn.execute("DELETE FROM bookmarks WHERE id = ?",
                           (int(str(ref).strip()),))
        conn.commit()
        conn.close()
        return ((f"Bookmark {ref} hat gaya." if cur.rowcount
                 else f"Bookmark {ref} nahi mila."), "")

    # ── 5. Text tools ────────────────────────────────────────────────
    def json_format(self, raw: str, mode: str = "pretty") -> Tuple[str, str]:
        """Pretty-print or minify a JSON string."""
        raw = (raw or "").strip()
        if not raw:
            return ("JSON do, tab format kar dunga.", "")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return (f"JSON valid nahi hai — {exc.msg} (line {exc.lineno}).", "")
        out = (json.dumps(data, indent=2, ensure_ascii=False) if mode == "pretty"
               else json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        head = out if len(out) <= 300 else out[:300] + " ..."
        return (f"JSON {'format' if mode == 'pretty' else 'minify'} ho gaya: "
                f"{head}", out)

    def url_encode(self, text: str, decode: bool = False) -> Tuple[str, str]:
        """Percent-encode or decode a URL / query string."""
        if not (text or "").strip():
            return ("Text do, encode ya decode kar dunga.", "")
        if decode:
            out = urllib.parse.unquote_plus(text)
            verb = "decode"
        else:
            out = urllib.parse.quote_plus(text)
            verb = "encode"
        head = out if len(out) <= 200 else out[:200] + " ..."
        return (f"URL {verb}: {head}", out)

    def text_case(self, text: str, mode: str) -> Tuple[str, str]:
        """upper / lower / title / sentence / reverse / palindrome."""
        t = (text or "").strip()
        if not t:
            return ("Text do, badal dunga.", "")
        if mode == "upper":
            out = t.upper()
        elif mode == "lower":
            out = t.lower()
        elif mode == "title":
            out = t.title()
        elif mode == "sentence":
            out = (t[:1].upper() + t[1:]).rstrip(".") + "."
        elif mode == "reverse":
            out = t[::-1]
        elif mode == "palindrome":
            norm = re.sub(r"[^a-z0-9]", "", t.lower())
            out = ("Haan, palindrome hai." if norm == norm[::-1]
                   else "Nahi, palindrome nahi hai.")
        else:
            return ("Mode nahi pata — upper, lower, title, sentence, reverse "
                    "ya palindrome.", "")
        head = out if len(out) <= 250 else out[:250] + " ..."
        return (head, out)

    def text_count(self, text: str) -> Tuple[str, str]:
        """Word / character / line / sentence counts for a blob of text."""
        t = text or ""
        if not t.strip():
            return ("Text khaali hai — kuch toh do count karne ke liye.", "")
        words = len(t.split())
        chars = len(t)
        lines = len(t.splitlines()) or 1
        sents = len([s for s in re.split(r"[.!?]+", t) if s.strip()])
        return (f"{words} words, {chars} characters, {lines} lines, "
                f"{sents} sentences.",
                f"{words}|{chars}|{lines}|{sents}")

    def text_extract(self, text: str, what: str) -> Tuple[str, str]:
        """Pull emails, urls, phone numbers or hashtags out of text."""
        t = text or ""
        pats = {
            "email": r"[\w.+-]+@[\w-]+\.[\w.]{2,}",
            "url": r"https?://[^\s<>\"')]+",
            "phone": r"(?:\+?\d{1,3}[\s.-]?)?(?:\d{5}[\s.-]?\d{5})",
            "hashtag": r"#\w+",
        }
        pat = pats.get((what or "").strip().lower())
        if pat is None:
            return ("Kya nikalun — email, url, phone ya hashtag?", "")
        found = re.findall(pat, t)
        uniq = list(dict.fromkeys(found))
        if not uniq:
            return (f"Koi {what} nahi mila is text me.", "")
        return (f"{len(uniq)} {what} mile: " + "; ".join(uniq[:10]),
                "; ".join(uniq))

    # ── 6. System doctor ─────────────────────────────────────────────
    def system_doctor(self) -> Tuple[str, str]:
        """One-shot health check across CPU, RAM, disk, battery and uptime."""
        try:
            import psutil
        except ImportError:
            return ("psutil install nahi hai, doctor run nahi ho sakta.", "")

        problems: list = []
        report: dict = {}

        report["cpu"] = psutil.cpu_percent(interval=0.4)
        if report["cpu"] > 85:
            problems.append(f"CPU {report['cpu']:.0f}% pe hai, thoda heavy")

        vm = psutil.virtual_memory()
        report["ram"] = vm.percent
        if vm.percent > 88:
            problems.append(f"RAM {vm.percent:.0f}% used")

        disks = []
        for part in ("C:\\", "D:\\"):
            if os.path.exists(part):
                try:
                    u = psutil.disk_usage(part)
                    disks.append((part, u))
                    if u.percent > 90:
                        problems.append(
                            f"Disk {part} {u.percent:.0f}% full hai")
                except OSError:
                    pass
        report["disks"] = [(p, round(u.percent)) for p, u in disks]

        try:
            report["battery"] = psutil.sensors_battery()
        except Exception:
            report["battery"] = None

        report["uptime_h"] = round(
            (datetime.datetime.now() -
             datetime.datetime.fromtimestamp(psutil.boot_time())).total_seconds()
            / 3600, 1)

        try:
            procs = psutil.process_iter(["name", "cpu_percent"])
            top = sorted(procs, key=lambda p: (p.info.get("cpu_percent") or 0),
                         reverse=True)[:3]
            report["top"] = [(p.info.get("name") or "?", p.info.get("cpu_percent"))
                             for p in top]
        except Exception:
            report["top"] = []

        say = (f"CPU {report['cpu']:.0f} percent, RAM {report['ram']:.0f} "
               f"percent, uptime {report['uptime_h']} ghante.")
        for part, pct in report["disks"]:
            say += f" Disk {part[0]} {pct} percent full."
        if report["battery"] is not None:
            say += f" Battery {round(report['battery'].percent)} percent."
        if report["top"]:
            say += " Top process: " + ", ".join(
                f"{n} {c or 0:.0f} percent" for n, c in report["top"]) + "."

        if problems:
            say += " Problem: " + "; ".join(problems) + "."
        else:
            say += " Overall sab theek hai, koi dikkat nahi."

        if problems:
            say += " Chahein to light mode chala sakta hoon — bolo 'light mode'."
        return (say, json.dumps(report, default=str))
