"""Skills — system control, web search, file ops, app launching, and info.

Every method returns a (speak_output: str, data: any) tuple so the caller
can decide whether to speak the result, log it, or pass it elsewhere.
"""

from __future__ import annotations

import datetime
import glob as globmod
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

from .gita import (
    get_daily_gita,
    get_shloka_by_mood,
    get_daily_bhagavatam_story,
    build_gita_delivery,
    build_gita_voice_text,
    list_moods,
    should_offer_mood_shloka,
    record_mood_accept,
    gita_stats,
    PDF_SOURCE,
    pdf_available,
    BHAGAVATAM_STORIES,
    CHAPTER_NAMES_ENGLISH,
    CHAPTER_NAMES_HINDI,
)
from .local_features import (
    TaskManager,
    Calculator,
    DocumentSearch,
    EnhancedNotes,
    NetworkInfo,
    ProcessManager,
    PomodoroTimer,
    ReminderManager,
    PasswordTools,
    HashTools,
    NumberTools,
    TextTools,
    RandomTools,
    Stopwatch,
    HabitTracker,
    StorageAnalyzer,
)
from .codefest import CodeFest
from .powertools import PowerTools
from .learning import LearningEngine
from .datasets import DatasetRegistry
from .website import WebsiteBuilder
from .memory import Memory, CustomRules
from .config import get_data_dir
from .dictionary import (
    Dictionary,
    spoken_summary,
    console_detail,
    spell_word,
    convert_units,
    age_from_birth_year,
    bmi_calc,
)
from .translate import Translator


class Skills:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.search_url = cfg.get("skills", {}).get(
            "default_search_engine", "https://www.google.com/search?q="
        )
        self.wikipedia_sentences = cfg.get("skills", {}).get("wikipedia_sentences", 2)
        self._home = os.path.expanduser("~")
        self._data_dir = get_data_dir()
        self.memory = Memory()
        self.custom_rules = CustomRules()
        self.dictionary = Dictionary()
        self.translator = Translator()
        self.codefest = CodeFest()
        self.power = PowerTools(cfg)
        self.learning = LearningEngine()
        self.datasets = DatasetRegistry()

    # ── Time / Date ─────────────────────────────────────────────────────
    def time(self) -> Tuple[str, str]:
        now = datetime.datetime.now()
        t = now.strftime("%I:%M %p").lstrip("0")
        return f"The time is {t}.", t

    def date(self) -> Tuple[str, str]:
        now = datetime.datetime.now()
        d = now.strftime("%A, %B %d, %Y")
        return f"Today is {d}.", d

    # ── Chrome path detection ───────────────────────────────────────────
    @staticmethod
    def _chrome_path() -> str:
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return "chrome"

    def _chrome_open(self, url: str):
        subprocess.Popen([self._chrome_path(), url], shell=False)

    # ── Web search ──────────────────────────────────────────────────────
    def web_search(self, query: str) -> Tuple[str, str]:
        url = f"{self.search_url}{query.replace(' ', '+')}"
        self._chrome_open(url)
        return f"Searching the web for {query}.", url

    def open_site(self, site: str) -> Tuple[str, str]:
        site_map = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "gmail": "https://mail.google.com",
            "github": "https://github.com",
            "stackoverflow": "https://stackoverflow.com",
            "reddit": "https://www.reddit.com",
            "twitter": "https://x.com",
            "facebook": "https://www.facebook.com",
            "instagram": "https://www.instagram.com",
            "linkedin": "https://www.linkedin.com",
            "amazon": "https://www.amazon.com",
            "flipkart": "https://www.flipkart.com",
            "spotify": "https://open.spotify.com",
            "netflix": "https://www.netflix.com",
            "hotstar": "https://www.hotstar.com",
            "chatgpt": "https://chat.openai.com",
            "whatsapp": "https://web.whatsapp.com",
            "telegram": "https://web.telegram.org",
            "chatgpt": "https://chat.openai.com",
        }
        s = site.lower().strip()
        if s in site_map:
            url = site_map[s]
            self._chrome_open(url)
            return f"Opening {site} in Chrome.", url
        if "." in s and " " not in s:
            url = f"https://www.{s}" if not s.startswith("http") else s
            self._chrome_open(url)
            return f"Opening {site} in Chrome.", url
        return self.web_search(site)

    def wikipedia(self, query: str) -> Tuple[str, str]:
        try:
            import wikipedia
            wikipedia.set_lang("en")
            summary = wikipedia.summary(query, sentences=self.wikipedia_sentences)
            return summary, summary
        except wikipedia.DisambiguationError as e:
            options = e.options[:5]
            msg = f"Multiple results: {', '.join(options)}. Please be more specific."
            return msg, str(options)
        except wikipedia.PageError:
            return f"Could not find a page about {query}.", ""
        except Exception as exc:
            return f"Wikipedia lookup failed: {exc}", ""

    # ── System info ─────────────────────────────────────────────────────
    def system_info(self) -> Tuple[str, str]:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            battery = psutil.sensors_battery()
            bat_str = f", battery at {battery.percent}%" if battery else ""
            msg = (
                f"CPU at {cpu}%, RAM at {ram.percent}% "
                f"({ram.used // (1024**3)}GB of {ram.total // (1024**3)}GB used), "
                f"disk at {disk.percent}% ({disk.used // (1024**3)}GB of "
                f"{disk.total // (1024**3)}GB used){bat_str}."
            )
            return msg, {"cpu": cpu, "ram": ram.percent, "disk": disk.percent}
        except Exception as exc:
            return f"System info unavailable: {exc}", ""

    # ── Apps / Launch ───────────────────────────────────────────────────
    def launch_app(self, app_name: str) -> Tuple[str, str]:
        app_map = {
            "notepad": ("cmd", "notepad"),
            "calculator": ("cmd", "calc"),
            "calc": ("cmd", "calc"),
            "cmd": ("cmd", "cmd"),
            "command prompt": ("cmd", "cmd"),
            "powershell": ("cmd", "powershell"),
            "explorer": ("cmd", "explorer"),
            "chrome": ("chrome", None),
            "edge": ("cmd", "start msedge"),
            "browser": ("chrome", None),
            "vscode": ("cmd", "code"),
            "visual studio code": ("cmd", "code"),
            "task manager": ("cmd", "taskmgr"),
            "control panel": ("cmd", "control"),
            "settings": ("cmd", "start ms-settings:"),
            "spotify": ("cmd", "start spotify:"),
        }
        entry = app_map.get(app_name.lower())
        if entry:
            kind, val = entry
            try:
                if kind == "chrome":
                    self._chrome_open("https://www.google.com")
                else:
                    subprocess.Popen(val, shell=True)
                return f"Opening {app_name}.", str(entry)
            except Exception as exc:
                return f"Failed to open {app_name}: {exc}", ""
        return f"I don't know how to open {app_name}.", ""

    # ── File operations ─────────────────────────────────────────────────
    def list_files(self, path: str = "") -> Tuple[str, str]:
        target = path.strip() or self._home
        if not os.path.isdir(target):
            return f"{target} is not a valid directory.", ""
        try:
            entries = os.listdir(target)
            if not entries:
                return f"The folder {target} is empty.", ""
            sample = entries[:10]
            more = f" and {len(entries) - 10} more items" if len(entries) > 10 else ""
            msg = f"In {target}: {', '.join(sample)}{more}."
            return msg, ", ".join(entries)
        except PermissionError:
            return f"Permission denied reading {target}.", ""

    def open_folder(self, path: str) -> Tuple[str, str]:
        target = os.path.expanduser(path.strip() or self._home)
        if os.path.isdir(target):
            os.startfile(target)
            return f"Opening {target}.", target
        return f"Folder {target} not found.", ""

    # ── Volume control (Windows) ────────────────────────────────────────
    def volume(self, level: str) -> Tuple[str, str]:
        level = level.strip().lower()
        try:
            from pycaw.pycaw import AudioUtilities

            # pycaw >= 2024 hands back an AudioDevice that already carries the
            # endpoint-volume interface on .EndpointVolume. Older builds return
            # a raw IMMDevice and still need .Activate(_iid_, ...). Support both
            # so the skill does not die on a pycaw upgrade.
            device = AudioUtilities.GetSpeakers()
            if hasattr(device, "EndpointVolume"):
                volume_obj = device.EndpointVolume
            else:
                from ctypes import cast, POINTER
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume_obj = cast(interface, POINTER(IAudioEndpointVolume))

            if level in ("mute", "unmute"):
                muted = volume_obj.GetMute()
                volume_obj.SetMute(not muted, None)
                state = "muted" if not muted else "unmuted"
                return f"Volume {state}.", state
            if level.endswith("%"):
                vol = max(0, min(100, int(level[:-1])))
                volume_obj.SetMasterVolumeLevelScalar(vol / 100.0, None)
                return f"Volume set to {vol}%.", str(vol)
            if level in ("up", "increase", "raise"):
                current = volume_obj.GetMasterVolumeLevelScalar()
                new_vol = min(1.0, current + 0.1)
                volume_obj.SetMasterVolumeLevelScalar(new_vol, None)
                return (f"Volume increased.", f"{new_vol*100:.0f}%")
            if level in ("down", "decrease", "lower"):
                current = volume_obj.GetMasterVolumeLevelScalar()
                new_vol = max(0.0, current - 0.1)
                volume_obj.SetMasterVolumeLevelScalar(new_vol, None)
                return (f"Volume decreased.", f"{new_vol*100:.0f}%")
            if level in ("max", "full", "100"):
                volume_obj.SetMasterVolumeLevelScalar(1.0, None)
                return "Volume set to maximum.", "100%"
            return f"Unrecognised volume command: {level}.", ""
        except Exception as exc:
            return f"Volume control unavailable ({exc}).", ""

    # ── Screenshot ──────────────────────────────────────────────────────
    def screenshot(self, path: str = "") -> Tuple[str, str]:
        try:
            import pyautogui
            dest = path.strip() or os.path.join(self._home, "Desktop",
                                                f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png")
            img = pyautogui.screenshot()
            img.save(dest)
            return f"Screenshot saved to {dest}.", dest
        except Exception as exc:
            return f"Screenshot failed: {exc}.", ""

    # ── Spider analysis (bug bounty scan) ──────────────────────────────
    def run_analysis(self, target: str) -> Tuple[str, str]:
        spider_dir = self.cfg.get("spider_path", "")
        if not spider_dir:
            return ("Spider scanner is not configured. Set 'spider_path' in config.json.", "")
        spider_script = os.path.join(spider_dir, "spider.py")
        if not os.path.isfile(spider_script):
            return "Spider scanner not found. Check the path.", ""
        clean = target.strip().lower()
        clean = re.sub(r"^https?://(www\.)?", "", clean).rstrip("/")
        try:
            proc = subprocess.run(
                [sys.executable, spider_script, clean, "--skip-dirs"],
                cwd=spider_dir,
                capture_output=True, text=True, timeout=600
            )
            output = proc.stdout + proc.stderr
            report_dir = os.path.join(spider_dir, "reports")
            folders = [d for d in os.listdir(report_dir) if d.startswith(clean.replace(".", "_")[:20])] if os.path.isdir(report_dir) else []
            folder = max(folders) if folders else ""
            if proc.returncode == 0:
                msg = f"Done bhai! Spider scan for {clean} is complete. Report is in your file."
            else:
                msg = f"Scan done but could use a look. Report is in your file."
            return msg, folder
        except subprocess.TimeoutExpired:
            return "The scan took too long. Might still be running, report should be in your file.", ""
        except Exception as exc:
            return f"Could not run analysis: {exc}", ""

    # ── News headlines (free, no key — Google News RSS) ─────────────────
    def headlines(self, topic: str = "technology") -> Tuple[str, str]:
        try:
            import re as _re
            import requests
            import xml.etree.ElementTree as ET
            url = ("https://news.google.com/rss/search?"
                   f"q={requests.utils.quote(topic)}&hl=en-IN&gl=IN&ceid=IN:en")
            resp = requests.get(url, timeout=12,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return f"Could not fetch news headlines.", ""
            try:
                root = ET.fromstring(resp.text)
            except Exception:
                return f"Could not fetch news headlines.", ""
            items = [it for it in root.iter("item")]
            if not items:
                return f"No headlines found for {topic}.", ""
            headlines = []
            for it in items[:5]:
                title = it.findtext("title", "").strip()
                title = _re.sub(r"\s*-\s*[^-]*$", "", title).strip()
                if title:
                    headlines.append(title)
            if not headlines:
                return f"No headlines found for {topic}.", ""
            msg = f"Top {topic} headlines: " + ". ".join(
                f"{i+1}. {h}" for i, h in enumerate(headlines)
            )
            return msg, " | ".join(headlines)
        except Exception as exc:
            return f"News unavailable: {exc}.", ""

    # ── Bhagavad Gita ──────────────────────────────────────────────────
    def gita_sloka(self, detail: str = "full") -> Tuple[str, str]:
        try:
            text = build_gita_delivery(detail=detail)
            voice = build_gita_voice_text(detail=detail)
            return text, voice
        except Exception as exc:
            return f"Gita shloka unavailable: {exc}", "Could not fetch today's Gita shloka."

    def gita_daily_voice(self, detail: str = "full") -> str:
        try:
            return build_gita_voice_text(detail=detail)
        except Exception:
            return "Radhe Radhe! before go and work on or in Maya, lets take some guid from Krishn ji."

    def bhagavatam_story(self) -> Tuple[str, str]:
        try:
            story = get_daily_bhagavatam_story()
            from .gita import _format_story_for_console, _format_story_for_voice
            console = _format_story_for_console(story)
            voice_lines = _format_story_for_voice(story)
            voice = " ".join(voice_lines)
            return console, voice
        except Exception as exc:
            return f"Bhagavatam story unavailable: {exc}", "Could not fetch today's story."

    def mood_shloka(self, mood: str) -> Tuple[str, str]:
        try:
            mood = mood.strip().lower()
            valid_moods = list(_mood_names())
            if mood not in valid_moods:
                msg = f"Mood '{mood}' not recognized. {list_moods()}"
                return msg, msg
            result = get_shloka_by_mood(mood)
            if not result:
                return "Could not find a shloka for your mood.", ""
            lines = result.get("lines", [])
            voice = " ".join(f"{l}" for l in lines)
            ch, vs = result["chapter"], result["verse"]
            console = (
                f"\n{'─'*50}\n"
                f"  🕉  Shloka for your mood: {mood}\n"
                f"  📖  Chapter {ch}, Verse {vs}\n"
                f"{'─'*50}\n"
            )
            if result["sanskrit"]:
                console += f"\n  {result['sanskrit']}\n"
            if result["translation"]:
                console += f"\n  📖  {result['translation']}\n"
            if result.get("purport"):
                console += f"\n  💡  {result['purport']}\n"
            console += f"\n{'─'*50}\n  🙏  Hare Krishna!\n"
            return console, voice
        except Exception as exc:
            return f"Mood shloka error: {exc}", "Could not fetch a mood-based shloka."

    def should_offer_gita(self) -> Tuple[bool, str]:
        return should_offer_mood_shloka()

    def record_accept(self) -> None:
        record_mood_accept()

    def gita_stats(self) -> Tuple[str, str]:
        stats = gita_stats()
        return stats, stats

    def pdf_info(self) -> Tuple[str, str]:
        if pdf_available():
            return f"IGNCA PDF is available locally at {PDF_SOURCE}.", PDF_SOURCE
        return "IGNCA PDF not found locally. It can be downloaded from https://ignca.gov.in/Asi_data/279.pdf", ""


    # ── System commands (shutdown, restart, lock) ───────────────────────
    def system_command(self, cmd: str) -> Tuple[str, str]:
        cmd = cmd.strip().lower()
        if cmd in ("shutdown", "shut down", "shutdown pc"):
            os.system("shutdown /s /t 10")
            return "Shutting down in 10 seconds.", "shutdown"
        if cmd in ("restart", "reboot"):
            os.system("shutdown /r /t 10")
            return "Restarting in 10 seconds.", "restart"
        if cmd in ("lock", "lock pc", "lock screen"):
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return "Locking the workstation.", "lock"
        if cmd in ("sleep", "hibernate"):
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            return "Putting the system to sleep.", "sleep"
        if cmd in ("logoff", "log off", "sign out"):
            os.system("shutdown /l")
            return "Signing out.", "logoff"
        return f"Unknown system command: {cmd}.", ""

    # ── Note taking ─────────────────────────────────────────────────────
    def note(self, text: str) -> Tuple[str, str]:
        notes_dir = os.path.join(self._home, "Documents", "AKNotes")
        os.makedirs(notes_dir, exist_ok=True)
        path = os.path.join(notes_dir, f"note_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return f"Note saved to {path}.", path
        except Exception as exc:
            return f"Could not save note: {exc}.", ""

    # ── Local, low-memory features ──────────────────────────────────────
    # Task / Todo manager
    def task_add(self, text: str) -> Tuple[str, str]:
        tm = TaskManager()
        msg = tm.add(text)
        return msg, msg

    def task_list(self) -> Tuple[str, str]:
        tm = TaskManager()
        msg = tm.list()
        return msg, msg

    def task_done(self, task_id: int) -> Tuple[str, str]:
        tm = TaskManager()
        msg = tm.done(task_id)
        return msg, msg

    def task_remove(self, task_id: int) -> Tuple[str, str]:
        tm = TaskManager()
        msg = tm.remove(task_id)
        return msg, msg

    def task_count(self) -> Tuple[str, str]:
        tm = TaskManager()
        msg, _ = tm.count()
        return msg, msg

    # Calculator
    def calculate(self, expr: str) -> Tuple[str, str]:
        msg = Calculator.calculate(expr)
        return msg, msg

    # Document search
    def search_docs(self, query: str) -> Tuple[str, str]:
        ds = DocumentSearch()
        msg = ds.search_content(query)
        return msg, msg

    def find_file(self, name: str) -> Tuple[str, str]:
        ds = DocumentSearch()
        msg = ds.find_file(name)
        return msg, msg

    # Enhanced notes with tags
    def note_add(self, text: str, tags: Optional[List[str]] = None) -> Tuple[str, str]:
        en = EnhancedNotes()
        msg = en.add(text, tags)
        return msg, msg

    def note_list(self, tag: str = "") -> Tuple[str, str]:
        en = EnhancedNotes()
        msg = en.list(tag)
        return msg, msg

    def note_search(self, query: str) -> Tuple[str, str]:
        en = EnhancedNotes()
        msg = en.search(query)
        return msg, msg

    def note_remove(self, note_id: int) -> Tuple[str, str]:
        en = EnhancedNotes()
        msg = en.remove(note_id)
        return msg, msg

    # Network info
    def network_info(self) -> Tuple[str, str]:
        msg = NetworkInfo.get_info()
        return msg, msg

    def ping_host(self, host: str = "8.8.8.8") -> Tuple[str, str]:
        msg = NetworkInfo.ping(host)
        return msg, msg

    # Process manager
    def list_processes(self, sort_by: str = "cpu", count: int = 5) -> Tuple[str, str]:
        msg = ProcessManager.list_processes(sort_by, count)
        return msg, msg

    def kill_process(self, target: str) -> Tuple[str, str]:
        msg = ProcessManager.kill(target)
        return msg, msg

    # Pomodoro timer
    def pomodoro_start(self, minutes: int = 25) -> Tuple[str, str]:
        msg = PomodoroTimer.start(minutes)
        return msg, msg

    def pomodoro_stop(self) -> Tuple[str, str]:
        msg = PomodoroTimer.stop()
        return msg, msg

    def pomodoro_status(self) -> Tuple[str, str]:
        msg = PomodoroTimer.status()
        return msg, msg

    # Password / PIN generator
    def password_generate(self, length: int = 16) -> Tuple[str, str]:
        msg = PasswordTools.password(length)
        return msg, msg

    def pin_generate(self, digits: int = 6) -> Tuple[str, str]:
        msg = PasswordTools.pin(digits)
        return msg, msg

    # Hash & base64 tools
    def hash_text(self, text: str, algo: str = "sha256") -> Tuple[str, str]:
        msg = HashTools.hash_text(text, algo)
        return msg, msg

    def base64_encode(self, text: str) -> Tuple[str, str]:
        msg = HashTools.base64_encode(text)
        return msg, msg

    def base64_decode(self, data: str) -> Tuple[str, str]:
        msg = HashTools.base64_decode(data)
        return msg, msg

    # Number base converter
    def num_convert(self, value: str, from_base: str, to_base: str) -> Tuple[str, str]:
        msg = NumberTools.convert(value, from_base, to_base)
        return msg, msg

    # Text stats
    def text_stats(self, text: str) -> Tuple[str, str]:
        msg = TextTools.analyze(text)
        return msg, msg

    # Random tools
    def flip_coin(self) -> Tuple[str, str]:
        msg = RandomTools.coin()
        return msg, msg

    def roll_dice(self, sides: int = 6) -> Tuple[str, str]:
        msg = RandomTools.dice(sides)
        return msg, msg

    def random_number(self, low: int, high: int) -> Tuple[str, str]:
        msg = RandomTools.number(low, high)
        return msg, msg

    # Stopwatch
    def stopwatch_start(self) -> Tuple[str, str]:
        msg = Stopwatch.start()
        return msg, msg

    def stopwatch_stop(self) -> Tuple[str, str]:
        msg = Stopwatch.stop()
        return msg, msg

    def stopwatch_status(self) -> Tuple[str, str]:
        msg = Stopwatch.status()
        return msg, msg

    # Habit tracker
    def habit_add(self, name: str) -> Tuple[str, str]:
        msg = HabitTracker().add(name)
        return msg, msg

    def habit_log(self, name: str) -> Tuple[str, str]:
        msg = HabitTracker().log(name)
        return msg, msg

    def habit_summary(self) -> Tuple[str, str]:
        msg = HabitTracker().summary()
        return msg, msg

    # Storage analyzer
    def storage_top_folders(self, count: int = 5) -> Tuple[str, str]:
        msg = StorageAnalyzer.top_folders(count)
        return msg, msg

    def storage_big_files(self, count: int = 5) -> Tuple[str, str]:
        msg = StorageAnalyzer.big_files(count)
        return msg, msg

    # ── CodeFest: offline coding dataset ───────────────────────
    def code_search(self, query: str, lang: str = "") -> Tuple[str, str]:
        spoken, detail = self.codefest.search(query, lang)
        self.learning.record_usage("code_search")
        return spoken, detail

    def code_snippet(self, query: str, lang: str = "") -> Tuple[str, str]:
        spoken, detail = self.codefest.snippet(query, lang)
        self.learning.record_usage("code_snippet")
        return spoken, detail

    def code_random(self) -> Tuple[str, str]:
        return self.codefest.random_snippet()

    def code_stats(self) -> Tuple[str, str]:
        return self.codefest.stats()

    def code_learn(self, language: str, topic: str, title: str,
                   code: str, explanation: str = "") -> Tuple[str, str]:
        msg = self.codefest.add_snippet(language, topic, title, code, explanation)
        self.learning.record_improvement(f"Stored new {language} snippet: {title}")
        self.learning.record_lesson(f"learn code: {topic}", "local", msg)
        return msg, msg

    # ── Learning engine: self-improvement ──────────────────────
    def learning_review(self) -> Tuple[str, str]:
        return self.learning.review()

    def self_learn_stats(self) -> Tuple[str, str]:
        from .self_learn import SelfLearner
        spoken, detail = SelfLearner().stats()
        top = SelfLearner().top_cached()
        if top:
            spoken += " Top learnt: " + "; ".join(
                f"{q} ({h}x)" for q, _a, h in top[:4]
            ) + "."
            detail += "\nLearnt Q&A (most reused):\n" + "\n".join(
                f"  • {q} — {a}" for q, a, _h in top
            )
        return spoken, detail

    def learning_stats(self) -> Tuple[str, str]:
        return self.learning.stats()

    def learning_improvement(self, note: str) -> Tuple[str, str]:
        msg = self.learning.record_improvement(note)
        return msg, msg

    # ── Dataset registry ───────────────────────────────────────
    def datasets_catalog(self) -> Tuple[str, str]:
        return self.datasets.catalog()

    def datasets_list(self) -> Tuple[str, str]:
        return self.datasets.list_registered()

    def datasets_info(self, name: str) -> Tuple[str, str]:
        return self.datasets.info(name)

    def datasets_add(self, name: str) -> Tuple[str, str]:
        msg = self.datasets.download(name)
        self.learning.record_improvement(f"Added dataset '{name}'")
        return msg, msg

    def datasets_register(self, path: str, name: str = "") -> Tuple[str, str]:
        msg = self.datasets.register(path, name)
        return msg, msg

    def datasets_remove(self, name: str) -> Tuple[str, str]:
        msg = self.datasets.remove(name)
        return msg, msg

    def datasets_search(self, query: str) -> Tuple[str, str]:
        return self.datasets.search(query)

    def datasets_stats(self) -> Tuple[str, str]:
        return self.datasets.stats()

    def datasets_analyze(self, name: str) -> Tuple[str, str]:
        return self.datasets.analyze(name)

    def datasets_download_all(self) -> Tuple[str, str]:
        return self.datasets.download_all()

    # ── Website builder ────────────────────────────────────────
    def build_website(self, description: str) -> Tuple[str, str]:
        spoken, detail = WebsiteBuilder().build(description)
        self.learning.record_usage("build_website")
        return spoken, detail

    def list_websites(self) -> Tuple[str, str]:
        return WebsiteBuilder().projects()

    # ── Data directory info ───────────────────────────────────────
    def data_dir(self) -> Tuple[str, str]:
        return f"All local data is stored in: {self._data_dir}", self._data_dir

    # ── Custom rules (teach AK) ──────────────────────────────────
    def custom_rule_add(self, trigger: str, response: str) -> Tuple[str, str]:
        msg = self.custom_rules.add(trigger, response)
        return msg, msg

    def custom_rule_list(self) -> Tuple[str, str]:
        msg = self.custom_rules.list()
        return msg, msg

    def custom_rule_remove(self, index: int) -> Tuple[str, str]:
        msg = self.custom_rules.remove(index)
        return msg, msg

    def custom_rule_find(self, text: str) -> Optional[str]:
        return self.custom_rules.find(text)

    # ── User memory ──────────────────────────────────────────────
    def memory_add(self, text: str) -> Tuple[str, str]:
        msg = self.memory.add_fact(text)
        return msg, msg

    def memory_list(self) -> Tuple[str, str]:
        msg = self.memory.summary()
        return msg, msg

    def memory_clear(self) -> Tuple[str, str]:
        msg = self.memory.clear()
        return msg, msg

    # ── Reminders ────────────────────────────────────────────────
    def reminder_add(self, message: str, seconds: int) -> Tuple[str, str]:
        msg = ReminderManager.add(message, seconds)
        return msg, msg

    def reminder_status(self) -> Tuple[str, str]:
        msg = ReminderManager.status()
        return msg, msg

    # ── File operations ──────────────────────────────────────────
    def file_create(self, path: str, content: str = "") -> Tuple[str, str]:
        target = os.path.expanduser(path.strip())
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Created {target} ({len(content)} chars).", target
        except Exception as exc:
            return f"Could not create file: {exc}", ""

    def file_read(self, path: str, max_lines: int = 20) -> Tuple[str, str]:
        target = os.path.expanduser(path.strip())
        if not os.path.isfile(target):
            return f"File not found: {target}", ""
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            preview = "".join(lines[:max_lines])
            total = len(lines)
            char_count = sum(len(l) for l in lines)
            msg = f"{target} — {total} lines, {char_count} chars.\n{preview}"
            if total > max_lines:
                msg += f"\n... ({total - max_lines} more lines)"
            return msg, preview
        except Exception as exc:
            return f"Could not read file: {exc}", ""

    def file_delete(self, path: str) -> Tuple[str, str]:
        target = os.path.expanduser(path.strip())
        if not os.path.isfile(target):
            return f"File not found: {target}", ""
        try:
            os.remove(target)
            return f"Deleted {target}.", target
        except Exception as exc:
            return f"Could not delete: {exc}", ""

    def file_rename(self, old: str, new: str) -> Tuple[str, str]:
        old = os.path.expanduser(old.strip())
        new = os.path.expanduser(new.strip())
        if not os.path.exists(old):
            return f"File not found: {old}", ""
        try:
            os.rename(old, new)
            return f"Renamed to {new}.", new
        except Exception as exc:
            return f"Could not rename: {exc}", ""

    def file_move(self, src: str, dst: str) -> Tuple[str, str]:
        src = os.path.expanduser(src.strip())
        dst = os.path.expanduser(dst.strip())
        if not os.path.exists(src):
            return f"Source not found: {src}", ""
        try:
            shutil.move(src, dst)
            return f"Moved to {dst}.", dst
        except Exception as exc:
            return f"Could not move: {exc}", ""

    def file_copy(self, src: str, dst: str) -> Tuple[str, str]:
        src = os.path.expanduser(src.strip())
        dst = os.path.expanduser(dst.strip())
        if not os.path.exists(src):
            return f"Source not found: {src}", ""
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                shutil.copy2(src, dst)
            return f"Copied to {dst}.", dst
        except Exception as exc:
            return f"Could not copy: {exc}", ""

    def open_file(self, path: str) -> Tuple[str, str]:
        target = os.path.expanduser(path.strip())
        if not os.path.exists(target):
            return f"File not found: {target}", ""
        try:
            os.startfile(target)
            return f"Opened {target}.", target
        except Exception as exc:
            return f"Could not open: {exc}", ""

    # ── Folder organize (by extension) ───────────────────────────
    def organize(self, folder: str) -> Tuple[str, str]:
        folder = os.path.expanduser(folder.strip())
        if not os.path.isdir(folder):
            return f"Folder not found: {folder}", ""
        ext_map = {
            ".jpg": "Images", ".jpeg": "Images", ".png": "Images",
            ".gif": "Images", ".bmp": "Images", ".svg": "Images", ".webp": "Images",
            ".mp4": "Videos", ".avi": "Videos", ".mkv": "Videos", ".mov": "Videos",
            ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".ogg": "Audio",
            ".pdf": "Documents", ".docx": "Documents", ".doc": "Documents",
            ".txt": "Documents", ".xlsx": "Documents", ".pptx": "Documents",
            ".csv": "Documents", ".md": "Documents",
            ".zip": "Archives", ".rar": "Archives", ".7z": "Archives", ".tar": "Archives",
            ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code",
            ".css": "Code", ".json": "Code", ".xml": "Code",
            ".exe": "Installers", ".msi": "Installers",
        }
        moved = 0
        errors = 0
        for fname in os.listdir(folder):
            src = os.path.join(folder, fname)
            if os.path.isdir(src):
                continue
            ext = os.path.splitext(fname)[1].lower()
            subfolder = ext_map.get(ext, "Other")
            dest_dir = os.path.join(folder, subfolder)
            try:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(src, os.path.join(dest_dir, fname))
                moved += 1
            except Exception:
                errors += 1
        msg = f"Organized {moved} files in {folder}."
        if errors:
            msg += f" ({errors} errors)"
        return msg, f"{moved} files moved"

    # ── Backup ───────────────────────────────────────────────────
    def backup(self, source_folder: str) -> Tuple[str, str]:
        source = os.path.expanduser(source_folder.strip())
        if not os.path.isdir(source):
            return f"Folder not found: {source}", ""
        backup_root = os.path.join(self._data_dir, "Backups")
        os.makedirs(backup_root, exist_ok=True)
        name = os.path.basename(source.rstrip("\\/"))
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(backup_root, f"{name}_{ts}")
        try:
            shutil.copytree(source, dest)
            size_mb = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(dest) for f in fns) / (1024*1024)
            return f"Backed up {source} to {dest} ({size_mb:.1f} MB).", dest
        except Exception as exc:
            return f"Backup failed: {exc}", ""

    # ── Clipboard ────────────────────────────────────────────────
    def clipboard_set(self, text: str) -> Tuple[str, str]:
        try:
            import pyperclip
            pyperclip.copy(text)
            return f"Clipboard set: {text[:100]}...", text
        except Exception:
            try:
                import subprocess
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Set-Clipboard -Value '{text.replace(chr(39), chr(39)+chr(39))}'"],
                    capture_output=True, timeout=10,
                )
                return f"Clipboard set.", text
            except Exception as exc:
                return f"Clipboard unavailable: {exc}", ""

    def clipboard_get(self) -> Tuple[str, str]:
        try:
            import pyperclip
            text = pyperclip.paste()
            return f"Clipboard: {text}", text
        except Exception:
            return "Clipboard unavailable.", ""

    # ── Quick notes (data dir) ───────────────────────────────────
    def note(self, text: str) -> Tuple[str, str]:
        notes_dir = os.path.join(self._data_dir, "QuickNotes")
        os.makedirs(notes_dir, exist_ok=True)
        path = os.path.join(notes_dir, f"note_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return f"Note saved to {path}.", path
        except Exception as exc:
            return f"Could not save note: {exc}.", ""

    # ── Capabilities list ────────────────────────────────────────
    def capabilities(self) -> Tuple[str, str]:
        caps = (
            "Here's what I can do locally:\n"
            "• Time, date, system stats, battery\n"
            "• Web search, Wikipedia, news headlines\n"
            "• Open sites/apps, launch Chrome, VSCode, etc.\n"
            "• Volume control, screenshot, system commands\n"
            "• Calculator, todo list, notes with tags\n"
            "• Dictionary, synonyms, antonyms, word of the day\n"
            "• Offline dictionary — any English word (WordNet on D drive)\n"
            "• Offline Hindi <-> English translation (D drive cache)\n"
            "• Unit converter (temperature, length, weight, data)\n"
            "• Age calculator, BMI calculator\n"
            "• File create/read/delete/rename/move/copy\n"
            "• Folder organize, backup\n"
            "• Network info, ping\n"
            "• Process list/kill, pomodoro timer, stopwatch\n"
            "• Reminders (threaded, offline)\n"
            "• Clipboard read/write\n"
            "• Strong password & PIN generator (offline)\n"
            "• Hash generator (MD5/SHA) and Base64 encode/decode\n"
            "• Binary/hex/octal/decimal converter\n"
            "• Word, character & reading-time counter\n"
            "• Coin flip, dice roll, random number generator\n"
            "• Habit tracker with streaks\n"
            "• Disk/storage analyzer (biggest folders & files)\n"
            "• Document search, find files\n"
            "• Offline CODE dataset — snippets in Python/JS/Bash/SQL/HTML/CSS…\n"
            "• Build complete WEBSITES with a Python + SQLite backend\n"
            "   → say: 'build me a website for a restaurant'\n"
            "• BUG-BOUNTY / security scanner (spider tool) — authorized targets\n"
            "   → say: 'bug bounty <domain>', 'spider scan <domain>', 'recon <domain>'\n"
            "• Dataset registry on the D drive — add, register, search datasets\n"
            "   → 'show dataset catalog', 'add dataset <name>', 'preview dataset <name>'\n"
            "• Security intel datasets offline: CISA known-exploited CVEs, MalwareBazaar\n"
            "• Self-improvement engine — I learn, log, and review myself\n"
            "• I store every dataset and lesson on the D drive, big or small\n"
            "• Bhagavad Gita & Bhagavatam daily\n"
            "• Weather — live, with an offline cache ('Delhi ka mausam')\n"
            "• Git helper — status, log, diff, branch, commit on a local repo\n"
            "• Expense tracker (SQLite) — add, list, daily/monthly total\n"
            "• Bookmarks — add, list, open, remove\n"
            "• System doctor — one-shot CPU/RAM/disk/battery health check\n"
            "• Text tools — JSON format/minify, URL encode/decode, upper/lower/\n"
            "  title case, palindrome check, word count, extract emails/URLs\n"
            "• Custom commands (teach me anything)\n"
            "• Email sending (SMTP) — compose & send locally\n"
            "• File drag & drop aware (drop files for smart analysis)\n"
            "• Persistent memory (I remember you)\n"
            f"All data stored in: {self._data_dir}"
        )
        return caps, caps

    # ── Dictionary ──────────────────────────────────────────────
    def dictionary_lookup(self, word: str) -> Tuple[str, Optional[dict]]:
        """Look up a word locally (fast) or fetch from Datamuse API once."""
        entry = self.dictionary.lookup(word)
        if entry:
            spoken = spoken_summary(entry)
            return spoken, entry
        # Word not found — try suggestions.
        suggestions = self.dictionary.suggest(word)
        if suggestions:
            msg = (f"I don't have a definition for '{word}'. "
                   f"Did you mean: {', '.join(suggestions[:5])}?")
        else:
            msg = (f"I don't have a definition for '{word}' in my "
                   f"local dictionary ({self.dictionary.count()} words stored "
                   f"on D drive).")
        return msg, None

    def dictionary_synonyms(self, word: str) -> Tuple[str, str]:
        entry = self.dictionary.lookup(word)
        if entry and entry.get("synonyms"):
            return (f"Synonyms of {entry.get('word', word)}: "
                    + ", ".join(entry["synonyms"][:12]) + ".", "")
        return f"No synonyms found for '{word}' locally.", ""

    def dictionary_antonyms(self, word: str) -> Tuple[str, str]:
        entry = self.dictionary.lookup(word)
        if entry and entry.get("antonyms"):
            return (f"Antonyms of {entry.get('word', word)}: "
                    + ", ".join(entry["antonyms"][:12]) + ".", "")
        return f"No antonyms found for '{word}' locally.", ""

    def dictionary_word_of_day(self) -> Tuple[str, dict]:
        data = self.dictionary.word_of_day()
        entry = data.get("entry") or {}
        if entry:
            spoken = spoken_summary(entry, deep=True)
        else:
            spoken = (f"Today's featured word: {data.get('word','?')}. "
                      f"Lookup it for the full definition.")
        return spoken, entry

    def dictionary_random(self) -> Tuple[str, dict]:
        data = self.dictionary.random_word()
        entry = data.get("entry") or {}
        if entry:
            spoken = spoken_summary(entry, deep=True)
        else:
            spoken = f"Random word: {data.get('word','?')}. Look it up for the full definition."
        return spoken, entry

    def spell(self, word: str) -> Tuple[str, str]:
        return spell_word(word), word

    def convert(self, value: float, from_unit: str, to_unit: str) -> Tuple[str, str]:
        return convert_units(value, from_unit, to_unit)

    def age(self, birth_year: int) -> Tuple[str, str]:
        return age_from_birth_year(birth_year)

    def bmi(self, weight_kg: float, height_m: float) -> Tuple[str, str]:
        return bmi_calc(weight_kg, height_m)

    def dictionary_info(self) -> Tuple[str, str]:
        return (f"Local dictionary: {self.dictionary.count()} words stored "
                f"at {self.dictionary.dictionary_path()}.",
                self.dictionary.dictionary_path())

    # ── Offline Hindi <-> English translation ──────────────────────
    def translate_text(self, text: str, target: str) -> Tuple[str, str]:
        result, info = self.translator.translate(text, target)
        cached = " (from offline cache)" if info.get("cached") else ""
        return f"In {target.lower()}: {result}{cached}", result

    def translation_info(self) -> Tuple[str, str]:
        n = self.translator.count()
        return (f"Offline translation: {n} english-hindi words and phrases, "
                f"stored locally at {self.translator.data_path}.", self.translator.data_path)

    def translation_history(self) -> Tuple[str, str]:
        rows = self.translator.history()
        if not rows:
            return "No translations saved yet.", ""
        lines = []
        for src, tgt, result, ts in rows:
            lines.append(f"{ts} | {src} -> {result}")
        return ("Recent translations:\n" + "\n".join(lines), "")


def _mood_names() -> list:
    from .gita import _MOOD_NAMES
    return list(_MOOD_NAMES.keys())
