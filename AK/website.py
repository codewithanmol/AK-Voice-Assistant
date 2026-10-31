"""AK Forge — build a COMPLETE website with backend, fully offline.

From a one-line description AK generates a real, runnable project:

  D:\\YOUR_DATA_PATH\\Projects\\<site-name>\\
    ├─ index.html        main page (home)
    ├─ about.html        about / info page
    ├─ style.css         responsive theme (picked smartly by site type)
    ├─ script.js         frontend logic (fetch the API, show data)
    ├─ server.py         python backend: routing, static files,
    │                    JSON API + contact/leads form → SQLite
    ├─ init_db.py        seeds the SQLite database
    ├─ run.bat           start the server & open the browser
    └─ README.md         how to run & extend

The backend needs NO third-party packages (Python stdlib http.server +
sqlite3). Site kinds auto-detected from your words: business, portfolio,
blog, restaurant / cafe, shop, landing page.

Built on the D drive, works even with zero internet, uses the offline
CodeFest dataset for the templates it generates.
"""

from __future__ import annotations

import os
import re
import sqlite3
import datetime
from typing import Dict, List, Tuple

from .config import get_data_dir

_PROJECTS_DIR = os.path.join(get_data_dir(), "Projects")

_THEMES = {
    "business":  ("#2563eb", "Business",   "Professional services, built to deliver."),
    "portfolio": ("#7c3aed", "Portfolio",  "My work, my craft, my story."),
    "blog":      ("#059669", "Journal",    "Thoughts, stories and ideas."),
    "restaurant":("#ea580c", "Kitchen",    "Fresh food, good vibes."),
    "shop":      ("#0891b2", "Store",      "Hand-picked goods, delivered."),
    "landing":   ("#0f172a", "Coming Soon", "Something exciting is on the way."),
}

_LANG = "en"

_WORDS = re.compile(r"[a-z]+")


def _detect_kind(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("restaur", "cafe", "café", "food", "dhaba", "pizza")):
        return "restaurant"
    if any(w in t for w in ("shop", "store", "ecommerce", "sell", "commerce", "baazar")):
        return "shop"
    if any(w in t for w in ("portfolio", "resume", "cv", "personal profile")):
        return "portfolio"
    if any(w in t for w in ("blog", "articles", "journal", "news")):
        return "blog"
    if any(w in t for w in ("landing", "coming soon", "launch")):
        return "landing"
    return "business"


def _slug(text: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return raw[:40] or "my-site"


def _title(text: str) -> str:
    """Pick a clean site name out of a sentence like
    'build a website for a restaurant called spice garden'."""
    kw = (re.search(r"\b(?:called|named)\s+(.+)$", text, re.IGNORECASE)
          or re.search(r"\b(?:for|about)\s+(.+)$", text, re.IGNORECASE))
    if kw:
        seg = re.sub(r"\b(?:a|an|the)\b", " ", kw.group(1))
        words = [w.capitalize() for w in _WORDS.findall(seg.lower()) if len(w) > 1]
        if words:
            return " ".join(words[:3])
    stop = {"build", "make", "create", "generate", "code", "coding", "me", "my", "a",
            "an", "the", "website", "site", "web", "page", "with", "and", "full",
            "stack", "backend", "frontend", "for", "about", "i", "want", "please",
            "of", "in", "using", "called", "named"}
    words = [w.capitalize() for w in _WORDS.findall(text.lower())
             if len(w) > 1 and w not in stop]
    return " ".join(words[:3]) or "My Site"


class WebsiteBuilder:
    """Turns a natural-language request into a complete website + backend."""

    def build(self, description: str) -> Tuple[str, str]:
        """Returns (spoken, detail) where detail lists every created file."""
        kind = _detect_kind(description)
        accent, kind_title, tagline = _THEMES[kind]
        name = _title(description)
        slug = _slug(name) or _slug(kind)
        proj = os.path.join(_PROJECTS_DIR, slug)
        os.makedirs(os.path.join(proj, "static"), exist_ok=True)

        files = {
            "index.html": self._index(name, kind, accent, tagline, kind_title),
            "about.html": self._about(name, accent, kind_title, tagline),
            "items.html": self._items(name, accent),
            "style.css": self._css(accent, tagline),
            "script.js": self._js(),
            "server.py": self._server(accent, name),
            "init_db.py": self._init_db(),
            "run.bat": self._runbat(),
            "README.md": self._readme(name, kind, proj),
        }
        for fname, content in files.items():
            with open(os.path.join(proj, fname), "w", encoding="utf-8") as fh:
                fh.write(content)

        self._bump_projects(kind, name, proj)
        detail = (f"Website forged at {proj}\n"
                  + "\n".join(f"  • {f}" for f in files)
                  + "\n\nRun: run.bat  (or: python server.py)  →  http://localhost:8080")
        spoken = (f"Website '{name}' built with a full backend at {proj}. "
                  f"It has a home page, about page, styles, JavaScript, "
                  f"a Python server, and a SQLite database. Double-click run.bat to start it.")
        # Log the improvement in the learning engine (offline, D drive).
        try:
            from .learning import LearningEngine
            LearningEngine().record_improvement(f"Forged website '{name}' ({kind}) with backend")
        except Exception:
            pass
        return spoken, detail

    def _bump_projects(self, kind: str, name: str, path: str) -> None:
        try:
            db = os.path.join(_PROJECTS_DIR, "projects.sqlite3")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS projects ("
                             "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT, "
                             "path TEXT, created TEXT)")
                conn.execute("INSERT INTO projects (name, kind, path, created) VALUES (?,?,?,?)",
                             (name, kind, path, datetime.datetime.now().isoformat(timespec="seconds")))
        except Exception:
            pass

    def projects(self) -> Tuple[str, str]:
        db = os.path.join(_PROJECTS_DIR, "projects.sqlite3")
        if not os.path.exists(db):
            return "No websites built yet. Say: build me a website for a restaurant.", ""
        try:
            with sqlite3.connect(db) as conn:
                rows = conn.execute("SELECT name, kind, path, created FROM projects ORDER BY id DESC").fetchall()
        except Exception:
            return "No websites built yet.", ""
        lines = [f"Websites I built ({len(rows)}):"]
        for nm, kd, pth, ts in rows:
            lines.append(f"  • {nm} ({kd}) — {pth}  [{ts}]")
        spoken = "I have built " + " and ".join(r[0] for r in rows[:3]) \
            + (f", plus {len(rows)-3} more" if len(rows) > 3 else "") + "."
        return spoken, "\n".join(lines)

    # ────────────────────────────────────────────────────────────
    #  Templates
    # ────────────────────────────────────────────────────────────

    def _index(self, name: str, kind: str, accent: str, tagline: str, kind_title: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="{_LANG}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <div class="brand">{name}</div>
    <nav><a href="/">Home</a><a href="/about.html">About</a>
         <a href="/items.html" id="itemsLink">Items</a><a href="/#contact">Contact</a></nav>
  </header>

  <section class="hero">
    <h1>Welcome to {name}</h1>
    <p>{tagline}</p>
    <a class="btn" href="#contact">Get in touch</a>
  </section>

  <section class="features">
    <div class="card"><h3>Fast</h3><p>Everything serves from a local Python backend.</p></div>
    <div class="card"><h3>Real</h3><p>Data lives in a SQLite database.</p></div>
    <div class="card"><h3>Yours</h3><p>100% yours — edit, host, extend.</p></div>
  </section>

  <section class="items" id="items">
    <h2>From the database</h2>
    <div id="itemList" class="itemList"></div>
  </section>

  <section id="contact" class="contact">
    <h2>Contact {name}</h2>
    <form id="contactForm">
      <input name="name" placeholder="Your name" required>
      <input name="email" type="email" placeholder="Your email" required>
      <textarea name="message" placeholder="Message" required></textarea>
      <button class="btn" type="submit">Send</button>
    </form>
    <p id="formNote"></p>
  </section>

  <footer>© <span id="year"></span> {name}. Built by AK.</footer>
  <script src="/static/script.js"></script>
</body>
</html>
"""

    def _about(self, name: str, accent: str, kind_title: str, tagline: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="{_LANG}">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name} — About</title><link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header><div class="brand">{name}</div>
    <nav><a href="/">Home</a><a href="/about.html">About</a>
         <a href="/items.html">Items</a><a href="/#contact">Contact</a></nav>
  </header>
  <section class="hero"><h1>About {name}</h1><p>{tagline}</p></section>
  <section class="features">
    <div class="card"><h3>Story</h3><p>This site grew out of a single voice command.</p></div>
    <div class="card"><h3>Tech</h3><p>HTML, CSS, JavaScript, Python, SQLite.</p></div>
    <div class="card"><h3>Roadmap</h3><p>Add pages, forms, logins — it is all yours.</p></div>
  </section>
  <footer>© <span id="year"></span> {name}. Built by AK.</footer>
  <script>document.getElementById('year').textContent = new Date().getFullYear();</script>
</body>
</html>
"""

    def _items(self, name: str, accent: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="{_LANG}">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name} — Items</title><link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header><div class="brand">{name}</div>
    <nav><a href="/">Home</a><a href="/about.html">About</a>
         <a href="/items.html">Items</a><a href="/#contact">Contact</a></nav>
  </header>
  <section class="hero"><h1>Items</h1><p>Live from the SQLite database via /api/items.</p></section>
  <section class="items">
    <div id="itemList" class="itemList"></div>
  </section>
  <footer>© <span id="year"></span> {name}. Built by AK.</footer>
  <script src="/static/script.js"></script>
</body>
</html>
"""

    def _css(self, accent: str, tagline: str) -> str:
        return f""":root {{
  --accent: {accent};
  --dark: #0f172a;
  --muted: #64748b;
  --bg: #f8fafc;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--dark); line-height: 1.6; }}
header {{ display: flex; justify-content: space-between; align-items: center; padding: 1rem 5vw; background: white; box-shadow: 0 1px 8px rgba(0,0,0,.08); position: sticky; top: 0; }}
.brand {{ font-weight: 800; font-size: 1.25rem; color: var(--accent); }}
nav a {{ text-decoration: none; color: var(--dark); margin-left: 1.5rem; }}
nav a:hover {{ color: var(--accent); }}
.hero {{ text-align: center; padding: 12vh 5vw 8vh; background: linear-gradient(135deg, var(--dark), #1e293b); color: white; }}
.hero h1 {{ font-size: clamp(2rem, 6vw, 3.5rem); margin-bottom: .5rem; }}
.hero p {{ color: #cbd5e1; max-width: 46rem; margin: 0 auto 1.5rem; }}
.btn {{ display: inline-block; background: var(--accent); color: white; padding: .7rem 1.6rem; border-radius: 8px; text-decoration: none; border: 0; cursor: pointer; font-size: 1rem; }}
.features, .items, .contact {{ padding: 5rem 5vw; max-width: 70rem; margin: 0 auto; }}
h2 {{ margin-bottom: 1.5rem; }}
.card {{ background: white; padding: 1.5rem; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.06); }}
.features {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
.itemList {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 1rem; }}
.item, .card p {{ color: var(--muted); }}
.contact input, .contact textarea {{ width: 100%; padding: .8rem; margin-bottom: .8rem; border: 1px solid #e2e8f0; border-radius: 8px; font: inherit; }}
.contact textarea {{ min-height: 120px; }}
footer {{ text-align: center; padding: 2rem; color: var(--muted); }}
"""
        # trailing triple-quote is closed above; avoid accidental template brace errors

    def _js(self) -> str:
        return """async function loadItems() {
  const list = document.getElementById('itemList');
  if (!list) return;
  try {
    const res = await fetch('/api/items');
    const items = await res.json();
    list.innerHTML = items.length
      ? items.map(i => `<div class="card"><h3>${escapeHtml(i.name)}</h3><p>${escapeHtml(i.detail)}</p></div>`).join('')
      : '<p class="item">No items yet — seed them in init_db.py.</p>';
  } catch (e) {
    list.innerHTML = '<p class="item">Could not load items.</p>';
  }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
loadItems();

const form = document.getElementById('contactForm');
if (form) {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const note = document.getElementById('formNote');
    try {
      const res = await fetch('/api/submit', { method: 'POST', body: new URLSearchParams(fd) });
      const data = await res.json();
      note.textContent = data.ok ? 'Message saved, thank you!' : 'Something went wrong.';
      if (data.ok) form.reset();
    } catch (err) {
      note.textContent = 'Could not reach the server.';
    }
  });
}
document.addEventListener('DOMContentLoaded', () => {
  const y = document.getElementById('year');
  if (y) y.textContent = new Date().getFullYear();
});
"""

    def _server(self, accent: str, name: str) -> str:
        return f'''"""Backend for {name} — Python stdlib only (no pip install).
Run:  python server.py   →   http://localhost:8080
"""
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "site.db")
PORT = 8080
STATIC = os.path.join(BASE, "static")


def db():
    sqlite3.register_adapter(bool, lambda b: int(b))
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS items ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, detail TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS messages ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,"
                     "message TEXT, ts TEXT DEFAULT (datetime('now')))")
        if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
            conn.executemany("INSERT INTO items (name, detail) VALUES (?, ?)", [
                ("Hello World", "The first step of any journey."),
                ("Powered by AK", "Built offline with a Python + SQLite backend."),
                ("Your data", "Stored right here in site.db on the D drive."),
            ])


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_file(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ("/", "/index.html"):
            self._send(200, self._read_file(os.path.join(BASE, "index.html")))
        elif p.path in ("/about.html", "/items.html"):
            fp = os.path.join(BASE, p.path.lstrip("/"))
            self._send(200, self._read_file(fp) if os.path.exists(fp)
                       else b"404 - page not found", "text/html; charset=utf-8")
        elif p.path == "/api/items":
            with db() as conn:
                rows = [dict(r) for r in conn.execute("SELECT id, name, detail FROM items")]
            self._send(200, json.dumps(rows).encode(), "application/json")
        elif p.path == "/api/status":
            self._send(200, json.dumps({{"ok": True, "site": "{{name}}"}}).encode(),
                       "application/json")
        elif p.path.startswith("/static/"):
            fp = os.path.normpath(os.path.join(STATIC, p.path[len("/static/"):]))
            if fp.startswith(STATIC) and os.path.isfile(fp):
                ctype, body = "text/css; charset=utf-8", self._read_file(fp)
                if fp.endswith(".js"):
                    ctype = "application/javascript; charset=utf-8"
                self._send(200, body, ctype)
            else:
                self._send(404, b"not found")
        else:
            self._send(404, b"404 - not found")

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        data = parse_qs(raw)
        def v(k):
            return (data.get(k) or [""])[0].strip()
        if p.path == "/api/submit":
            name, email, message = v("name"), v("email"), v("message")
            if name and message:
                with db() as conn:
                    conn.execute("INSERT INTO messages (name, email, message) VALUES (?,?,?)",
                                 (name, email, message))
                self._send(200, json.dumps({{"ok": True}}).encode(), "application/json")
            else:
                self._send(200, json.dumps({{"ok": False}}).encode(), "application/json")
        else:
            self._send(404, json.dumps({{"ok": False}}).encode(), "application/json")

    def log_message(self, *args):
        pass  # keep console clean


if __name__ == "__main__":
    os.makedirs(BASE, exist_ok=True)
    init_db()
    print("Site running at http://localhost:8080")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''

    def _init_db(self) -> str:
        return """# Seeds site.db with a few demo items. Run once: python init_db.py
import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site.db")

with sqlite3.connect(DB) as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS items ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, detail TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS messages ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,"
                 "message TEXT, ts TEXT DEFAULT (datetime('now')))")
    if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
        conn.executemany("INSERT INTO items (name, detail) VALUES (?, ?)", [
            ("Welcome", "This row came straight from SQLite."),
            ("API ready", "Fetch /api/items to see JSON data."),
            ("Contact form", "POST /api/submit saves messages to the database."),
        ])
print("Database seeded: site.db")
"""

    def _runbat(self) -> str:
        return '@echo off\r\n' \
               'start "" http://localhost:8080\r\n' \
               'python server.py\r\n'

    def _readme(self, name: str, kind: str, proj: str) -> str:
        return f"""# {name}

**{kind}** website generated by AK with a full backend.

## Run it
    double-click run.bat     (or:  python server.py)

Then open http://localhost:8080

## Structure
    index.html      home page
    about.html      about page
    items.html      dynamic items page (rendered by /api/items)
    static/style.css   styles & theme
    static/script.js   frontend logic
    server.py       Python backend (stdlib only: http.server + sqlite3)
    init_db.py      seeds site.db
    site.db         SQLite database (created on first run)

## API
    GET  /api/items     → JSON list of items
    POST /api/submit    → saves contact form to the messages table

## Generated at
    {proj}
"""


# shortcut instance
def build(description: str) -> Tuple[str, str]:
    return WebsiteBuilder().build(description)