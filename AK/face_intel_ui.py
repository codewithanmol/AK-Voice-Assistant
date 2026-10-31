"""Face Intel GUI — profile window jo report.json live render karti hai.

Usage:
  python AK/face_intel_ui.py --session <dir> [--data-dir <path>]
  python AK/face_intel_ui.py --session <dir> --image <face.png> --name <n>

Window components:
  * left  : face picture + identity panel (name / wikipedia bio / github)
  * right : live status log + reverse-search + web results + profiles
  * clickable links open in the default browser.

Sab kuch local (Tkinter + Pillow), koi web-server nahi.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

if sys.version_info[0] == 3 and sys.version_info[1] >= 11:
    from PIL import Image as PilImage, ImageTk as PilTk

try:
    from .face_intel import (read_report, run_session_cfg, investigate,
                             save_markdown_report)
except ImportError:  # run as a plain script (python AK/face_intel_ui.py)
    _AK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _AK_DIR not in sys.path:
        sys.path.insert(0, _AK_DIR)
    from AK.face_intel import (read_report, run_session_cfg, investigate,
                               save_markdown_report)

_BG = "#0f1115"
_CARD = "#171b23"
_TEXT = "#e8e8ea"
_SUB = "#9aa0ab"
_ACC = "#ffb454"
_GREEN = "#7ee0a3"
_RED = "#ff7b72"


class FaceIntelUI:
    def __init__(self, session_dir: str, data_dir: str = "",
                 image: str = "", name: str = "",
                 reverse_cooldown: float = 5.0,
                 reverse_attempts: int = 2):
        self.session_dir = session_dir
        self.data_dir = data_dir
        self.image = image
        self.name = name
        self.reverse_cooldown = reverse_cooldown
        self.reverse_attempts = reverse_attempts
        self.root = tk.Tk()
        self.root.title("AK — Face Intel")
        self.root.configure(bg=_BG)
        self.root.geometry("1160x760")
        self.root.minsize(940, 600)
        self.proc = None
        self.build_ui()
        self.last_mtime = 0.0

    # ── UI skeleton ────────────────────────────────────────────────────
    def build_ui(self):
        wrap = tk.Frame(self.root, bg=_BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        # header
        head = tk.Frame(wrap, bg=_BG)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="AK — Face Intel", bg=_BG, fg=_ACC,
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        self.status_label = tk.Label(head, text="starting…", bg=_BG, fg=_SUB,
                                     font=("Segoe UI", 10))
        self.status_label.pack(side="right")

        body = tk.Frame(wrap, bg=_BG)
        body.pack(fill="both", expand=True)

        # left column: identity card
        left = tk.Frame(body, bg=_BG, width=430)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        self.photo_lbl = tk.Label(left, bg=_CARD, text="face png yahan",
                                  fg=_SUB, width=42, height=14,
                                  font=("Segoe UI", 9))
        self.photo_lbl.pack(fill="x", padx=2, pady=(2, 6))

        card = tk.Frame(left, bg=_CARD, bd=0)
        card.pack(fill="x", padx=0)
        self.name_var = tk.StringVar(value="…")
        tk.Label(card, textvariable=self.name_var, bg=_CARD, fg=_GREEN,
                 font=("Segoe UI", 15, "bold"), wraplength=410,
                 justify="left").pack(anchor="w", padx=14, pady=(12, 2))
        self.match_var = tk.StringVar(value="")
        tk.Label(card, textvariable=self.match_var, bg=_CARD, fg=_SUB,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14)

        tk.Label(card, text="WIKIPEDIA", bg=_CARD, fg=_ACC,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        self.wiki_text = tk.Text(card, bg=_CARD, fg=_TEXT, wrap="word",
                                 height=7, bd=0, font=("Segoe UI", 10),
                                 relief="flat", cursor="arrow")
        self.wiki_text.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.wiki_text.tag_configure("link", foreground=_ACC, underline=1)
        self.wiki_text.bind("<Button-1>", self._on_wiki_click)

        self.wd_label = tk.Label(card, text="", bg=_CARD, fg=_SUB,
                                 font=("Segoe UI", 9), wraplength=410,
                                 justify="left", anchor="w")
        self.wd_label.pack(fill="x", padx=14, pady=(2, 4))

        tk.Label(card, text="GITHUB", bg=_CARD, fg=_ACC,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(6, 2))
        self.gh_list = tk.Listbox(card, bg=_CARD, fg=_TEXT, bd=0, height=4,
                                  font=("Segoe UI", 9), relief="flat",
                                  activestyle="dotbox")
        self.gh_list.pack(fill="x", padx=10, pady=(0, 12))
        self.gh_list.bind("<Double-Button-1>", lambda e: self._open_gh())

        # right column: tabs
        right = tk.Frame(body, bg=_BG)
        right.pack(side="right", fill="both", expand=True)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        self.log_tab = self._make_tab(right)
        self.notebook.add(self.log_tab, text="Live Log")
        self.log_text = self._make_text(self.log_tab)

        self.rev_tab = self._make_tab(right)
        self.notebook.add(self.rev_tab, text="Reverse Search")
        self.rev_text = self._make_text(self.rev_tab, size=10)

        self.web_tab = self._make_tab(right)
        self.notebook.add(self.web_tab, text="Web Results")
        self.web_list = tk.Listbox(self.web_tab, bg=_BG, fg=_TEXT, bd=0,
                                   font=("Segoe UI", 10), relief="flat")
        self.web_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.web_list.bind("<Double-Button-1>", lambda e: self._open_web())

        self.prof_tab = self._make_tab(right)
        self.notebook.add(self.prof_tab, text="Profiles")
        self.prof_list = tk.Listbox(self.prof_tab, bg=_BG, fg=_TEXT, bd=0,
                                    font=("Segoe UI", 10), relief="flat")
        self.prof_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.prof_list.bind("<Double-Button-1>", lambda e: self._open_prof())

        self.sites_tab = self._make_tab(right)
        self.notebook.add(self.sites_tab, text="Platforms")
        self.sites_list = tk.Listbox(self.sites_tab, bg=_BG, fg=_TEXT, bd=0,
                                     font=("Segoe UI", 9), relief="flat")
        self.sites_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.sites_list.bind("<Double-Button-1>", lambda e: self._open_sites())

        self.news_tab = self._make_tab(right)
        self.notebook.add(self.news_tab, text="News")
        self.news_list = tk.Listbox(self.news_tab, bg=_BG, fg=_TEXT, bd=0,
                                    font=("Segoe UI", 9), relief="flat")
        self.news_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.news_list.bind("<Double-Button-1>", lambda e: self._open_news())

        self.reddit_tab = self._make_tab(right)
        self.notebook.add(self.reddit_tab, text="Reddit")
        self.reddit_list = tk.Listbox(self.reddit_tab, bg=_BG, fg=_TEXT, bd=0,
                                      font=("Segoe UI", 9), relief="flat")
        self.reddit_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.reddit_list.bind("<Double-Button-1>", lambda e: self._open_reddit())

        # footer buttons
        foot = tk.Frame(wrap, bg=_BG)
        foot.pack(fill="x", pady=(10, 0))
        tk.Button(foot, text="Save Report (.md)", command=self.save_report,
                  bg=_CARD, fg=_TEXT, relief="flat", font=("Segoe UI", 9),
                  padx=10, pady=4).pack(side="left")
        tk.Button(foot, text="Google Lens", command=self._open_lens,
                  bg=_CARD, fg=_TEXT, relief="flat", font=("Segoe UI", 9),
                  padx=10, pady=4).pack(side="left", padx=6)
        tk.Button(foot, text="Yandex", command=self._open_yandex,
                  bg=_CARD, fg=_TEXT, relief="flat", font=("Segoe UI", 9),
                  padx=10, pady=4).pack(side="left", padx=6)
        tk.Button(foot, text="Bing Visual", command=self._open_bing,
                  bg=_CARD, fg=_TEXT, relief="flat", font=("Segoe UI", 9),
                  padx=10, pady=4).pack(side="left", padx=6)
        tk.Button(foot, text="Person Lookup", command=self._open_person_lookup,
                  bg=_CARD, fg=_SUB, relief="flat", font=("Segoe UI", 9),
                  padx=10, pady=4).pack(side="left")
        tk.Button(foot, text="Re-run Search", command=self.rerun,
                  bg=_CARD, fg=_ACC, relief="flat", font=("Segoe UI", 9, "bold"),
                  padx=10, pady=4).pack(side="right")

    # ── helpers ────────────────────────────────────────────────────────
    def _make_tab(self, parent=None):
        f = tk.Frame(parent or self.root, bg=_BG)
        f.configure(bg=_BG)
        return f

    def _make_text(self, tab, size=10):
        t = tk.Text(tab, bg=_BG, fg=_TEXT, wrap="word", bd=0,
                    font=("Segoe UI", size), relief="flat", cursor="xterm")
        t.pack(fill="both", expand=True, padx=6, pady=6)
        return t

    # ── render loop (poll report.json) ────────────────────────────────
    def start(self):
        self.root.after(0, self._poll)
        if self.image:
            threading.Thread(target=self._run_explicit, daemon=True).start()
        else:
            threading.Thread(target=self._run_capture, daemon=True).start()
        self.root.mainloop()

    def _run_capture(self):
        try:
            run_session_cfg(self.session_dir, data_dir=self.data_dir,
                            progress=lambda m: self._log("… " + m),
                            reverse_cooldown=self.reverse_cooldown,
                            reverse_attempts=self.reverse_attempts)
        except Exception as exc:
            self._log(f"[error] {exc}")

    def _run_explicit(self):
        try:
            investigate(self.image, self.name or None, self.session_dir,
                        progress=lambda m: self._log("… " + m),
                        data_dir=self.data_dir,
                        reverse_cooldown=self.reverse_cooldown,
                        reverse_attempts=self.reverse_attempts)
        except Exception as exc:
            self._log(f"[error] {exc}")

    def rerun(self):
        self._log("… dobara investigate karta hun")
        rep = read_report(self.session_dir)
        img = rep.get("face_path") or self.image
        name = rep.get("name")
        threading.Thread(target=investigate, args=(img, name, self.session_dir),
                         kwargs={"progress": lambda m: self._log("… " + m),
                                 "data_dir": self.data_dir,
                                 "reverse_cooldown": self.reverse_cooldown,
                                 "reverse_attempts": self.reverse_attempts},
                         daemon=True).start()

    def _poll(self):
        try:
            rep = read_report(self.session_dir)
            self._render(rep)
        except Exception as exc:
            self._log(f"[render error] {exc}")
        self.root.after(1200, self._poll)

    # ── rendering ──────────────────────────────────────────────────────
    def _render(self, rep: dict):
        status = rep.get("status", "…")
        self.status_label.configure(text=status.upper())

        if status == "capturing":
            self.status_label.configure(text="camera par hoja — capturing")
        elif status == "error":
            self.status_label.configure(text="chehra nahi mila", fg=_RED)

        name = rep.get("name")
        face = rep.get("face_path")
        if not name and rep.get("face_name"):
            name = rep["face_name"]
        if name:
            conf = rep.get("face_conf")
            self.name_var.set(name)
            self.match_var.set(
                f"face_match: khud ke database se mila (conf {int(conf)}/100)"
                if conf is not None else "face_match: database se mila")
        else:
            self.name_var.set("Unknown person (camera pehchi nahi kar paya)")
            self.match_var.set("play to pehchane ki kosish — reverse search")

        self._render_photo(face)
        self._render_wiki(rep.get("wiki"))
        self._render_wikidata(rep.get("wikidata"))
        self._render_gh(rep.get("github") or [])
        self._render_web(rep.get("web") or [])
        self._render_prof(rep.get("profiles") or [])
        self._render_sites(rep.get("sites") or [])
        self._render_news(rep.get("news") or [])
        self._render_reddit(rep.get("reddit") or [])

        rev = rep.get("reverse") or {}
        self._set_text(self.rev_text, self._rev_block(rev))

        # only append new log lines
        steps = rep.get("steps", [])
        body = "\n".join(steps)
        if self.log_text.get("1.0", "end-1c") != body:
            self._set_text(self.log_text, body)
            self.log_text.see("end")

    def _render_photo(self, face_path):
        if not face_path or not os.path.isfile(face_path):
            return
        try:
            im = PilImage.open(face_path).convert("RGB")
            w, h = im.size
            maxw, maxh = 420, 300
            if w > maxw or h > maxh:
                r = min(maxw / w, maxh / h, 1.0)
                im = im.resize((int(w * r), int(h * r)), PilImage.LANCZOS)
            self._photo = PilTk.PhotoImage(im)
            self.photo_lbl.configure(image=self._photo, text="", height=self._photo.height(),
                                     width=self._photo.width())
        except Exception:
            pass

    def _render_wiki(self, wiki):
        self.wiki_text.configure(state="normal")
        self.wiki_text.delete("1.0", "end")
        self._wiki_url = None
        if wiki and wiki.get("summary"):
            self.wiki_text.insert("end", wiki.get("summary", ""), "")
            self._wiki_url = wiki.get("url")
            if self._wiki_url:
                self.wiki_text.insert("end", "\n\n[Source: Wikipedia] ", "")
                s = self.wiki_text.index("end-1c")
                self.wiki_text.insert("end", "open →", "link")
                self.wiki_text.tag_add("link", s, "end-1c")
        else:
            self.wiki_text.insert("end", "koi wikipedia bio nahi mili.")
        self.wiki_text.configure(state="disabled")

    def _render_gh(self, users):
        self.gh_list.delete(0, "end")
        self._gh_urls = []
        for u in users:
            self._gh_urls.append(u.get("url"))
            name = u.get("name") or u.get("login", "")
            bio = u.get("bio")
            line = f"@{u.get('login')}  —  {u.get('url')}"
            if bio:
                line += f"\n  {bio[:100]}"
            self.gh_list.insert("end", line)

    def _render_sites(self, sites):
        self.sites_list.delete(0, "end")
        self._sites_urls = []
        for s in sites:
            self._sites_urls.append(s.get("url"))
            self.sites_list.insert(
                "end", f"[{s.get('site')}] {s.get('title','')}\n  {s.get('url','')}")

    def _render_news(self, news):
        self.news_list.delete(0, "end")
        self._news_urls = []
        for n in news:
            self._news_urls.append(n.get("url"))
            self.news_list.insert(
                "end", f"[{n.get('source','')}] {n.get('title','')}\n  {n.get('url','')}")

    def _render_reddit(self, posts):
        self.reddit_list.delete(0, "end")
        self._reddit_urls = []
        for p in posts:
            self._reddit_urls.append(p.get("url"))
            self.reddit_list.insert(
                "end", f"r/{p.get('sub')} ({p.get('score')}) {p.get('title','')}\n"
                       f"  /u/{p.get('author','')}  {p.get('url','')}")

    def _render_wikidata(self, wd):
        if not wd or not (wd.get("occupation") or wd.get("born")
                          or wd.get("website")):
            self.wd_label.configure(text="")
            return
        bits = []
        if wd.get("occupation"):
            bits.append("Occupation: " + ", ".join(wd["occupation"][:3]))
        if wd.get("born"):
            bits.append("Born: " + wd["born"])
        if wd.get("website"):
            bits.append("Site: " + wd["website"])
        self.wd_label.configure(text="WIKIDATA\n" + "\n".join(bits),
                                fg=_ACC)

    def _render_web(self, results):
        self.web_list.delete(0, "end")
        self._web_urls = []
        for r in results:
            self._web_urls.append((r.get("title", ""), r.get("url")))
            self.web_list.insert("end",
                                 f"{r.get('title','(no title)')}\n  {r.get('url','')}\n "
                                 f"{r.get('snippet','')[:120]}")

    def _render_prof(self, profiles):
        self.prof_list.delete(0, "end")
        self._prof_urls = []
        for p in profiles:
            self._prof_urls.append(p.get("url"))
            self.prof_list.insert("end", f"{p.get('site')}  —  HTTP {p.get('code')}\n  {p.get('url')}")
        for p in profiles:
            self._prof_urls.append((p.get("site"), p.get("url")))
            self.prof_list.insert("end", f"{p.get('site')}: {p.get('url')}")

    @staticmethod
    def _rev_block(rev: dict) -> str:
        lines = []
        if not rev:
            return "reverse search chal raha hai…"
        y = rev.get("yandex")
        b = rev.get("bing")
        if y:
            lines.append("YANDEX RESULT FOUND")
            lines.append(y)
            lines.append("")
        if b:
            lines.append("BING VISUAL RESULT FOUND")
            lines.append(b)
            lines.append("")
        for site, url in (rev.get("manual") or {}).items():
            lines.append(f"{site}: {url}")
        if not (y or b):
            lines.append("(is baar engine se koi link nahi — browser buttons "
                         "se manually upload karo)")
        if rev.get("error"):
            lines.append("")
            lines.append(f"error: {rev['error']}")
        return "\n".join(lines)

    # ── click handlers ────────────────────────────────────────────────
    def _on_wiki_click(self, event):
        if self._wiki_url:
            webbrowser.open(self._wiki_url)

    def _open_web(self):
        idx = self.web_list.curselection()
        if idx and self._web_urls:
            _url = self._web_urls[idx[0]]
            webbrowser.open(_url[1])

    def _open_prof(self):
        idx = self.prof_list.curselection()
        if idx and self._prof_urls:
            webbrowser.open(self._prof_urls[idx[0]])

    def _open_news(self):
        idx = self.news_list.curselection()
        if idx and self._news_urls:
            webbrowser.open(self._news_urls[idx[0]])

    def _open_reddit(self):
        idx = self.reddit_list.curselection()
        if idx and self._reddit_urls:
            webbrowser.open(self._reddit_urls[idx[0]])

    def _open_person_lookup(self):
        rep = read_report(self.session_dir)
        name = rep.get("name") or self.name
        who = "+".join(name.split()) if name else ""
        base = ("https://www.google.com/search?q="
                + f'"{who}"' + "+person" if who else "https://www.google.com/search?q=person")
        choice = messagebox.askyesno(
            "Person Lookup",
            "Public aggregators (PeekYou, Pipl, Spokeo) free OSINT dete hain par "
            "bot-protected hain — script unhe nahi khol sakta.\n\n"
            "Google ke andar search kholun jisme PeekYou + Pipl + Spokeo + "
            "Whitepages sab results ek saath milenge?\n\n(Haan = Google, "
            "Nahi = sirf generic search)")
        webbrowser.open(base if choice else "https://www.google.com/search?q=peekyou+pipl+spokeo+whitepages")

    def _open_gh(self):
        idx = self.gh_list.curselection()
        if idx and self._gh_urls:
            webbrowser.open(self._gh_urls[idx[0]])

    def _open_sites(self):
        idx = self.sites_list.curselection()
        if idx and hasattr(self, "_sites_urls") and self._sites_urls:
            webbrowser.open(self._sites_urls[idx[0]])

    def _open_yandex(self):
        rep = read_report(self.session_dir)
        rev = rep.get("reverse") or {}
        webbrowser.open(rev.get("yandex") or rev.get("best") or "https://yandex.com/images")

    def _open_lens(self):
        webbrowser.open("https://lens.google.com")

    def _open_bing(self):
        img = (read_report(self.session_dir).get("face_path") or self.image)
        if img:
            messagebox.showinfo(
                "Bing Visual", f"Bing ko FLYING image path chahiye hota hai.\n"
                               f"Face saved file: {img}\n\nAb browser khulega, "
                               f"wahan upload karna hoga.")
        webbrowser.open("https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIHMP")

    def save_report(self):
        try:
            p = save_markdown_report(self.session_dir)
            messagebox.showinfo("Saved", f"Report saved:\n{p}")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    @staticmethod
    def _set_text(widget, text):
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _log(self, msg):
        try:
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="AK Face Intel GUI")
    ap.add_argument("--session", required=True)
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--image", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--reverse-cooldown", type=float, default=5.0)
    ap.add_argument("--reverse-attempts", type=int, default=2)
    a = ap.parse_args()
    os.makedirs(a.session, exist_ok=True)
    try:
        ui = FaceIntelUI(a.session, data_dir=a.data_dir,
                         image=a.image, name=a.name,
                         reverse_cooldown=a.reverse_cooldown,
                         reverse_attempts=a.reverse_attempts)
    except Exception as exc:
        print("GUI init fail:", exc)
        return 1
    ui.start()
    return 0


if __name__ == "__main__":
    main()