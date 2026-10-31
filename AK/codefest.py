"""AK CodeFest — offline coding dataset & knowledge library.

All data lives in SQLite at  D:\\YOUR_DATA_PATH\\codefest.sqlite3  (on the D drive).
Seed content ships inside this file, and AK grows the dataset itself whenever it
solves / learns a new coding task — no internet needed, even for huge datasets.

Provides:
  • Code snippets (practice-the-real-world patterns) with search / random
  • Coding tips & best-practice facts
  • Self-adaptive storage: learned snippets are saved permanently
"""

from __future__ import annotations

import os
import sqlite3
import random
from typing import List, Optional, Tuple

from .config import get_data_dir

_DB = os.path.join(get_data_dir(), "codefest.sqlite3")

LANGUAGES = {"python", "javascript", "js", "bash", "shell", "powershell",
             "sql", "java", "c", "cpp", "git", "regex", "html", "css",
             "web", "backend", "flask", "fastapi", "http"}

_LANG_GROUP = {"js": "javascript", "javascript": "javascript", "shell": "bash",
               "bash": "bash", "python": "python", "powershell": "powershell",
               "sql": "sql", "java": "java", "c": "c", "cpp": "cpp",
               "git": "git", "regex": "regex", "html": "html", "css": "css",
               "web": "web", "backend": "web", "flask": "web",
               "fastapi": "web", "http": "web", "rest": "web", "api": "web",
               "fullstack": "web", "full stack": "web"}

# ────────────────────────────────────────────────────────────────
#  Seed data: (language, topic, title, code, explanation, tags)
# ────────────────────────────────────────────────────────────────

_SEED_SNIPPETS: List[Tuple[str, str, str, str, str, str]] = [
    # ── Python ────────────────────────────────────────────────
    ("python", "file read", "Read a text file",
     "with open('data.txt', 'r', encoding='utf-8') as f:\n    text = f.read()\nprint(text)",
     "Opens a file safely and reads all content. The 'with' block closes the file automatically.", "file io open read"),
    ("python", "file write", "Write a text file",
     "with open('out.txt', 'w', encoding='utf-8') as f:\n    f.write('Hello world\\n')",
     "Opens a file for writing and writes a line. Use 'a' to append instead of overwriting.", "file io write append"),
    ("python", "csv", "Read a CSV file",
     "import csv\nwith open('data.csv', newline='', encoding='utf-8') as f:\n    for row in csv.reader(f):\n        print(row)",
     "Reads each CSV row as a list of strings. Use csv.DictReader for column names.", "csv data file"),
    ("python", "json", "Read & write JSON",
     "import json\nwith open('data.json', encoding='utf-8') as f:\n    data = json.load(f)\n\nwith open('out.json', 'w', encoding='utf-8') as f:\n    json.dump(data, f, indent=2)",
     "json.load parses a file to a Python object; json.dump serialises it back.", "json serialise"),
    ("python", "sort", "Sort a list of dicts",
     "items = [{'name': 'Bob', 'age': 30}, {'name': 'Ann', 'age': 25}]\nitems.sort(key=lambda x: x['age'])",
     "sort with a key function sorts without creating a new list. sorted() returns a new list.", "sort list lambda"),
    ("python", "string reverse", "Reverse a string",
     "text = 'AK'\nreversed_text = text[::-1]\nprint(reversed_text)",
     "Slicing [::-1] is the fastest way to reverse a string in Python.", "string slice"),
    ("python", "list comprehension", "Filter with list comprehension",
     "nums = [1, 2, 3, 4, 5, 6]\nevens = [n for n in nums if n % 2 == 0]",
     "A compact way to build lists while filtering — reads like math.", "list filter comprehension"),
    ("python", "remove duplicates", "Remove duplicates from a list",
     "items = [3, 1, 3, 2, 1]\nunique = list(dict.fromkeys(items))",
     "dict.fromkeys preserves first-seen order while dropping duplicates.", "unique set order"),
    ("python", "http request", "Simple HTTP GET",
     "import urllib.request\nwith urllib.request.urlopen('https://example.com') as r:\n    html = r.read().decode('utf-8')\nprint(html[:200])",
     "Built-in urllib performs a plain HTTP GET without extra libraries.", "http url download"),
    ("python", "regex findall", "Extract with regex",
     "import re\nemails = re.findall(r'[\\w.+-]+@[\\w-]+\\.[\\w.]+', text)",
     "re.findall returns every non-overlapping match as a list of strings.", "regex extract findall"),
    ("python", "error handling", "Try / except",
     "try:\n    result = 10 / int(user_input)\nexcept ZeroDivisionError:\n    print('Cannot divide by zero')\nexcept ValueError:\n    print('That is not a number')",
     "Catch specific exceptions so normal flow keeps running.", "exception error try except"),
    ("python", "class", "Define a class",
     "class Person:\n    def __init__(self, name):\n        self.name = name\n\n    def greet(self):\n        return f'Hi, I am {self.name}'",
     "Classes group data and behaviour. __init__ runs when you create an instance.", "oop object class init"),
    ("python", "datetime", "Format current time",
     "from datetime import datetime\nnow = datetime.now()\nstamp = now.strftime('%Y-%m-%d %H:%M:%S')",
     "strftime formats datetime into a readable string.", "time date format"),
    ("python", "walk files", "Walk a folder tree",
     "import os\nfor root, dirs, files in os.walk('.'):\n    for name in files:\n        print(os.path.join(root, name))",
     "os.walk yields (dirpath, dirnames, filenames) for the whole tree.", "os walk files scan"),
    ("python", "sha256", "Hash a string",
     "import hashlib\ndigest = hashlib.sha256('hello'.encode()).hexdigest()",
     "sha256 hashes are one-way and used for checksums and integrity.", "hash sha256 crypto"),
    ("python", "random", "Random number",
     "import random\nn = random.randint(1, 100)\nchoice = random.choice(['a', 'b', 'c'])",
     "randint is inclusive both ends; choice picks one item from a list.", "random randint choice"),
    ("python", "sqlite", "Use SQLite",
     "import sqlite3\nconn = sqlite3.connect('app.db')\nconn.execute('CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, name TEXT)')\nconn.execute('INSERT INTO t (name) VALUES (?)', ('AK',))\nconn.commit()",
     "SQLite is a zero-config file database built into Python. The ? placeholder prevents injection.", "sqlite database insert"),
    ("python", "args", "Pass command-line args",
     "import sys\nname = sys.argv[1] if len(sys.argv) > 1 else 'world'\nprint(f'Hello {name}')",
     "sys.argv holds the script name plus the arguments passed on the command line.", "argv cli args"),
    ("python", "screenshot", "Take a screenshot",
     "import subprocess\nsubprocess.run(['powershell', '-Command', 'Add-Type -AssemblyName System.Windows.Forms; [Screenshot]$b=[Screen]::PrimaryScreen.Bounds; Add-Object -ComObject WScript.Shell'])\nprint('Screenshot via PowerShell on Windows')",
     "Windows screenshots are easiest via PowerShell's System.Windows.Forms assembly.", "screen capture windows"),
    ("python", "backup copy", "Copy a file",
     "import shutil\nshutil.copy2('source.txt', 'backup/source.txt')",
     "shutil.copy2 copies a file and preserves metadata like timestamps.", "copy shutil backup"),

    # ── JavaScript ─────────────────────────────────────────────
    ("javascript", "fetch api", "Fetch data from an API",
     "fetch('https://api.example.com/data')\n  .then(r => r.json())\n  .then(data => console.log(data))\n  .catch(err => console.error(err));",
     "Promises chain: fetch → parse JSON → use data → catch errors.", "fetch api http promise"),
    ("javascript", "async await", "Async / await",
     "async function load() {\n  const res = await fetch('https://api.example.com');\n  const data = await res.json();\n  console.log(data);\n}",
     "await pauses inside an async function until a promise settles.", "async await promise"),
    ("javascript", "map filter", "map & filter arrays",
     "const nums = [1, 2, 3, 4];\nconst doubled = nums.map(n => n * 2);\nconst evens = nums.filter(n => n % 2 === 0);",
     "map transforms every element; filter keeps only those passing the test.", "array map filter"),
    ("javascript", "destructure", "Destructuring",
     "const user = {name: 'AK', age: 25};\nconst {name, age} = user;\nconst [first, ...rest] = [1, 2, 3];",
     "Destructuring unpacks properties or array items into variables.", "object array destructure"),
    ("javascript", "dom select", "Select & change the DOM",
     "const el = document.querySelector('#title');\nel.textContent = 'Hello AK';\nel.style.color = 'blue';",
     "querySelector finds the first matching element; textContent sets its text.", "dom html css"),
    ("javascript", "local storage", "Use local storage",
     "localStorage.setItem('key', 'value');\nconst v = localStorage.getItem('key');\nlocalStorage.removeItem('key');",
     "localStorage persists strings in the browser across sessions.", "storage browser"),
    ("javascript", "node http", "HTTP server in Node",
     "const http = require('http');\nhttp.createServer((req, res) => {\n  res.end('Hello AK');\n}).listen(3000);",
     "Node's http module creates a minimal web server.", "node server http"),
    ("javascript", "regex replace", "Replace with regex",
     "const clean = text.replace(/\\s+/g, ' ');",
     ".replace with /g replaces every occurrence; \\s+ collapses whitespace.", "regex replace whitespace"),

    # ── Bash ───────────────────────────────────────────────────
    ("bash", "loop files", "Loop over files",
     "for f in *.txt; do\n    echo \"Processing $f\"\n    wc -l \"$f\"\n done",
     "Glob expands to files; always quote $f to survive spaces.", "loop glob for"),
    ("bash", "grep search", "Search text in files",
     "grep -rn \"error\" logs/",
     "-r recurses, -n prints line numbers. Use -i to ignore case.", "grep search find"),
    ("bash", "find by name", "Find files by name",
     "find . -name \"*.py\" -mtime -7",
     "Shows Python files modified in the last 7 days.", "find name mtime"),
    ("bash", "sort unique", "Count and sort unique lines",
     "cat file.txt | sort | uniq -c | sort -rn",
     "Classic pipeline: sort → group with uniq -c → order by frequency.", "sort uniq pipeline"),
    ("bash", "backup tar", "Backup a folder",
     "tar -czf backup.tar.gz /path/to/folder",
     "-c creates, -z compresses with gzip, -f names the archive.", "tar backup compress"),
    ("bash", "check service", "Check a service status",
     "systemctl status nginx",
     "systemctl manages systemd services on modern Linux.", "systemctl service"),
    ("bash", "extract zip", "Extract an archive",
     "unzip archive.zip -d /dest/folder\n# or\ntar -xzf archive.tar.gz",
     "-d sets the destination folder for zip; tar -xzf extracts gzip tarballs.", "unzip tar extract"),
    ("bash", "disk usage", "Show disk usage",
     "df -h",
     "df prints free space on all mounted filesystems in human-readable form.", "disk df space"),

    # ── PowerShell ─────────────────────────────────────────────
    ("powershell", "list processes", "List top processes",
     "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 Name, CPU",
     "Pipeline: get → sort → pick the first 5 with selected properties.", "process cpu get-process"),
    ("powershell", "copy files", "Copy files matching a pattern",
     "Get-ChildItem *.txt | Copy-Item -Destination C:\\Backup",
     "Pipe matching files into Copy-Item for a one-liner copy.", "copy-item backup"),
    ("powershell", "find large files", "Find large files",
     "Get-ChildItem C:\\Users -Recurse -File | Sort-Object Length -Descending | Select-Object -First 10 FullName, Length",
     "Recurses a tree, sorts by length and shows the top 10.", "find large length"),
    ("powershell", "wifi password", "Show saved WiFi password",
     "netsh wlan show profiles\nnetsh wlan show profile name=\"YourSSID\" key=clear",
     "key=clear reveals the plaintext key on Windows.", "wifi netsh password"),
    ("powershell", "download file", "Download a file",
     "Invoke-WebRequest -Uri \"https://example.com/x.zip\" -OutFile \"C:\\Downloads\\x.zip\"",
     "Invoke-WebRequest with -OutFile saves a remote file locally.", "download invoke-webrequest"),
    ("powershell", "system info", "Get quick system info",
     "Get-ComputerInfo | Select-Object WindowsProductName, OsVersion, TotalPhysicalMemory",
     "Get-ComputerInfo returns a rich object of system facts.", "system info windows"),
    ("powershell", "restart service", "Restart a service",
     "Restart-Service -Name \"Spooler\" -Force",
     "-Force restarts even if dependent services are running.", "service restart"),
    ("powershell", "loop files", "Loop files with foreach",
     "Get-ChildItem C:\\Logs\\*.log | ForEach-Object { Write-Host $_.Name }",
     "$_ refers to the current piped item inside ForEach-Object.", "foreach loop items"),

    # ── SQL ────────────────────────────────────────────────────
    ("sql", "select filter", "SELECT with WHERE",
     "SELECT name, age FROM users\nWHERE age >= 18\nORDER BY age DESC;",
     "WHERE filters rows before ORDER BY sorts them.", "select where order"),
    ("sql", "join tables", "INNER JOIN two tables",
     "SELECT u.name, o.total\nFROM users u\nJOIN orders o ON o.user_id = u.id;",
     "JOIN links rows by a matching key; aliases u/o shorten the query.", "join alias"),
    ("sql", "group count", "GROUP BY with COUNT",
     "SELECT status, COUNT(*) AS n\nFROM tickets\nGROUP BY status;",
     "GROUP BY buckets rows; COUNT(*) counts rows inside each bucket.", "group count aggregate"),
    ("sql", "insert update", "INSERT and UPDATE",
     "INSERT INTO users (name, age) VALUES ('AK', 25);\nUPDATE users SET age = 26 WHERE name = 'AK';",
     "INSERT adds rows; UPDATE changes rows matched by WHERE.", "insert update"),
    ("sql", "like search", "ILIKE / LIKE search",
     "SELECT * FROM products\nWHERE title LIKE '%phone%';",
     "% is the wildcard; LIKE is case-sensitive, ILIKE (Postgres) is not.", "like wildcard search"),
    ("sql", "window rank", "Rank rows with window function",
     "SELECT name, score,\n       RANK() OVER (ORDER BY score DESC) AS pos\nFROM players;",
     "OVER gives each row a rank across the whole result set without GROUP BY.", "rank window over"),

    # ── Java ───────────────────────────────────────────────────
    ("java", "hello world", "Hello World in Java",
     "public class Main {\n    public static void main(String[] args) {\n        System.out.println(\"Hello AK\");\n    }\n}",
     "Every Java app needs a main method inside a public class.", "main class print"),
    ("java", "read file", "Read a text file",
     "import java.nio.file.*;\nString content = Files.readString(Paths.get(\"data.txt\"));",
     "Files.readString reads a whole file into a String (Java 11+).", "nio file read"),
    ("java", "list sort", "Sort a list",
     "import java.util.*;\nList<Integer> nums = Arrays.asList(3, 1, 2);\nnums.sort(Comparator.naturalOrder());",
     "List.sort with a Comparator orders the list in place.", "sort comparator list"),

    # ── Git ────────────────────────────────────────────────────
    ("git", "undo commit", "Undo the last commit",
     "git reset --soft HEAD~1    # keep changes staged\ngit reset --hard HEAD~1     # discard changes",
     "--soft keeps your edits, --hard throws them away. Use carefully.", "reset undo commit"),
    ("git", "stash", "Save work temporarily",
     "git stash\ngit stash pop",
     "stash shelves uncommitted changes; pop restores them later.", "stash save"),
    ("git", "branch", "Create and switch branch",
     "git checkout -b feature-name\ngit checkout main\ngit merge feature-name",
     "Branching keeps experiments isolated; merge brings them back.", "branch merge checkout"),
    ("git", "log history", "Pretty log",
     "git log --oneline --graph --all -20",
     "A compact graph of the last 20 commits across all branches.", "log graph history"),
    ("git", "wip save", "Commit quickly",
     "git add .\ngit commit -m \"wip: message\"",
     "Stages everything and commits in two quick commands.", "commit add stage"),

    # ── HTML ───────────────────────────────────────────────────
    ("html", "page skeleton", "Basic HTML page",
     "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"UTF-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n  <title>My Site</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n  <header>My Site</header>\n  <main><h1>Hello world</h1></main>\n</body>\n</html>",
     "Every page needs doctype, html, head, body. Add meta viewport for mobile.", "html head body basic"),
    ("html", "form", "A contact form",
     "<form action=\"/api/submit\" method=\"post\">\n  <label>Name <input name=\"name\" required></label>\n  <label>Email <input name=\"email\" type=\"email\" required></label>\n  <label>Message <textarea name=\"message\" required></textarea></label>\n  <button type=\"submit\">Send</button>\n</form>",
     "name/type attributes control what the server receives; required blocks empty submits.", "form input submit"),
    ("html", "semantic", "Semantic layout",
     "<header>...</header>\n<nav>...</nav>\n<main>\n  <article>Content</article>\n  <aside>Sidebar</aside>\n</main>\n<footer>...</footer>",
     "Semantic tags help SEO and screen readers understand the page structure.", "semantic seo layout"),

    # ── CSS ────────────────────────────────────────────────────
    ("css", "center flex", "Center with flexbox",
     ".center {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  min-height: 100vh;\n}",
     "Flexbox centers content on both axes in a single container.", "flex center layout"),
    ("css", "responsive grid", "Responsive grid",
     ".cards {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));\n  gap: 1rem;\n}",
     "auto-fit + minmax makes the grid adapt to any screen size.", "grid responsive media"),
    ("css", "sticky header", "Sticky header",
     "header {\n  position: sticky;\n  top: 0;\n  background: white;\n  box-shadow: 0 1px 8px rgba(0,0,0,.1);\n}",
     "position sticky keeps the header visible while scrolling.", "sticky navigation"),

    # ── Web / backend ──────────────────────────────────────────
    ("web", "json api", "JSON API with stdlib http.server",
     "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
     "import json\n\n"
     "class H(BaseHTTPRequestHandler):\n"
     "    def do_GET(self):\n"
     "        body = json.dumps({'ok': True, 'data': [1, 2, 3]}).encode()\n"
     "        self.send_response(200)\n"
     "        self.send_header('Content-Type', 'application/json')\n"
     "        self.end_headers()\n"
     "        self.wfile.write(body)\n\n"
     "ThreadingHTTPServer(('127.0.0.1', 8080), H).serve_forever()",
     "Zero-dependency Python JSON backend — no pip install needed.", "api json backend server"),
    ("web", "flask app", "Minimal Flask app",
     "from flask import Flask, jsonify, request\n\napp = Flask(__name__)\n\n@app.get('/api/items')\ndef items():\n    return jsonify(['a', 'b'])\n\n@app.post('/api/submit')\ndef submit():\n    data = request.form\n    return jsonify({'ok': True})\n\nif __name__ == '__main__':\n    app.run(debug=True)",
     "Flask routes are tiny functions; @app.get / @app.post map HTTP verbs.", "flask route http"),
    ("web", "fetch form", "Send form data with fetch",
     "fetch('/api/submit', {\n  method: 'POST',\n  headers: {'Content-Type': 'application/x-www-form-urlencoded'},\n  body: new URLSearchParams(new FormData(form))\n}).then(r => r.json()).then(console.log);",
     "URLSearchParams(md) serialises a form into POST body format.", "fetch form post ajax"),
    ("web", "sqlite backend", "SQLite behind web API",
     "import sqlite3\nconn = sqlite3.connect('site.db')\nrows = conn.execute('SELECT * FROM items').fetchall()",
     "SQLite is a file-based database — perfect for small self-hosted apps.", "sqlite database backend"),
    ("web", "serve static", "Serve static files in Python",
     "from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer\n"
     "                                                             \n"
     "def make(static_dir):\n"
     "    handler = SimpleHTTPRequestHandler\n"
     "    return ThreadingHTTPServer(('127.0.0.1', 8080), handler)\n\n"
     "import os\nos.chdir('static')\nmake('static').serve_forever()",
     "SimpleHTTPRequestHandler serves whatever folder is the working directory.", "static files serve cdn"),
    ("web", "deploy basics", "Deploy a site",
     "# Local test first\npython server.py\n# Then push to a host\n#   - Render / Railway / VPS: run: python server.py\n#   - Keep DB + secrets in env vars, never in git\n#   - Use a reverse proxy (nginx/caddy) for domain + https",
     "Test locally, then deploy. Never commit the database or secrets.", "deploy host nginx vps"),
    ("sql", "create table", "CREATE TABLE with constraints",
     "CREATE TABLE users (\n  id INTEGER PRIMARY KEY AUTOINCREMENT,\n  email TEXT NOT NULL UNIQUE,\n  created TEXT DEFAULT (datetime('now'))\n);",
     "PRIMARY KEY + UNIQUE keep rows distinct; NOT NULL blocks empty values.", "create table autokey unique"),
]

# ── Seed tips: best-practice one-liners ─────────────────────────

_SEED_TIPS: List[Tuple[str, str, str]] = [
    ("git", "Never push secrets", "Never commit passwords or API keys. Use .env files added to .gitignore."),
    ("python", "Use hidden files", "Prefer os.path.join over string concatenation for paths — it is OS-aware."),
    ("python", "Context managers", "Use 'with' for files and network handles so they close automatically."),
    ("python", "Immutable defaults", "Never use a list/dict as a default argument — use None and create inside."),
    ("js", "const first", "Prefer const by default; only use let when the value really changes."),
    ("js", "Strict equality", "Use === instead of == to avoid type coercion surprises."),
    ("sql", "Prepare all input", "Use parameterized queries (?) so user input can never become SQL."),
    ("sql", "Index your joins", "Index the columns you JOIN and WHERE on for speed on big tables."),
    ("bash", "Quote variables", "Always quote variables in bash (\"$f\") — spaces break unquoted names."),
    ("powershell", "Pipeline objects", "PowerShell pipes objects, not text — filter as early in the pipeline as possible."),
    ("regex", "Avoid greedy", "Use non-greedy .*? to stop regexes at the first match, not the last."),
    ("java", "ArrayList vs LinkedList", "Use ArrayList for random access; LinkedList only for heavy head/tail insertion."),
]


# ────────────────────────────────────────────────────────────────
#  CodeFest engine
# ────────────────────────────────────────────────────────────────


class CodeFest:
    def __init__(self, db_path: str = ""):
        self._db = db_path or _DB
        self._init_db()
        self._seed()

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS snippets ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "language TEXT NOT NULL,"
                "topic TEXT NOT NULL,"
                "title TEXT NOT NULL,"
                "code TEXT NOT NULL,"
                "explanation TEXT NOT NULL,"
                "tags TEXT NOT NULL DEFAULT '',"
                "source TEXT NOT NULL DEFAULT 'seed',"
                "used INTEGER NOT NULL DEFAULT 0,"
                "created TEXT NOT NULL DEFAULT (datetime('now'))"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snip_lang ON snippets(language)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snip_topic ON snippets(topic)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tips ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "language TEXT NOT NULL,"
                "tip TEXT NOT NULL,"
                "used INTEGER NOT NULL DEFAULT 0"
                ")"
            )

    def _seed(self):
        with sqlite3.connect(self._db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM snippets").fetchone()[0]
            if n == 0:
                for language, topic, title, code, explanation, tags in _SEED_SNIPPETS:
                    conn.execute(
                        "INSERT INTO snippets (language, topic, title, code, explanation, tags)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (language, topic, title, code, explanation, tags),
                    )
            if conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 0:
                for language, _t, tip in _SEED_TIPS:
                    conn.execute("INSERT INTO tips (language, tip) VALUES (?, ?)", (language, tip))

    # ── helpers ────────────────────────────────────────────────

    def _normalise_lang(self, lang: str) -> str:
        return _LANG_GROUP.get(lang.strip().lower(), lang.strip().lower())

    def _query_words(self, query: str) -> List[str]:
        return [w for w in re_words(query) if len(w) > 1]

    # ── search ─────────────────────────────────────────────────

    def search(self, query: str, lang: str = "") -> Tuple[str, str]:
        """Return (spoken, detail). spoken is short for TTS, detail is for console."""
        lang = self._normalise_lang(lang)
        words = self._query_words(query)
        rows = []
        with sqlite3.connect(self._db) as conn:
            if lang:
                rows = conn.execute(
                    "SELECT language, topic, title, explanation, used FROM snippets"
                    " WHERE language=? ORDER BY used DESC", (lang,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT language, topic, title, explanation, used FROM snippets"
                    " ORDER BY used DESC; "
                ).fetchall()
        if not rows:
            return (f"No code found for '{query}'{f' in {lang}' if lang else ''} yet.", "")

        scored = []
        for language, topic, title, explanation, used in rows:
            hay = f"{topic} {title}"
            hits = sum(1 for w in words if w in hay.lower())
            if hits:
                scored.append((hits, used, language, topic, title, explanation))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if not scored:
            return (f"Nothing in the code dataset matches '{query}'.", "")

        best = scored[0]
        detail = "Coding dataset matches:\n"
        for hits, used, language, topic, title, explanation in scored[:4]:
            detail += f"  • [Score {hits}] {topic.title()} ({language}) — {title}\n"
        spoken = (f"Found {len(scored)} matches in the code dataset. "
                  f"Best: {best[3].title()}, {best[2]} — {best[4]}. "
                  f"Say: show code for {best[4]} to see the full snippet.")
        self._bump_used(best[3], best[4])
        return spoken, detail

    # ── full snippet ───────────────────────────────────────────

    def snippet(self, query: str, lang: str = "") -> Tuple[str, str]:
        """Return the best full code snippet as (spoken, detail)."""
        lang = self._normalise_lang(lang)
        words = self._query_words(query)
        with sqlite3.connect(self._db) as conn:
            if lang:
                rows = conn.execute(
                    "SELECT language, topic, title, code, explanation, used, tags"
                    " FROM snippets WHERE language=? ORDER BY used DESC", (lang,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT language, topic, title, code, explanation, used, tags"
                    " FROM snippets ORDER BY used DESC"
                ).fetchall()
        if not rows:
            return (f"No snippet in the code dataset{ f' for {lang}' if lang else ''}.", "")

        scored = []
        for language, topic, title, code, explanation, used, tags in rows:
            hay = f"{topic} {title} {tags}"
            hits = sum(1 for w in words if w in hay.lower())
            if hits:
                scored.append((hits, used, language, topic, title, code, explanation))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if not scored:
            return (f"No snippet matches '{query}'.", "")

        language, topic, title, code, explanation = scored[0][2], scored[0][3], \
            scored[0][4], scored[0][5], scored[0][6]
        detail = f"── {title} ({language}) ───────────────────────\n{code}\n\n{explanation}"
        spoken = f"{topic.title()} in {language}. {explanation.split('.')[0]}."
        self._bump_used(topic, title)
        return spoken, detail

    def random_snippet(self) -> Tuple[str, str]:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT language, topic, title, code, explanation FROM snippets"
                " ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
        if not row:
            return "Code dataset is empty.", ""
        language, topic, title, code, explanation = row
        detail = f"── {title} ({language}) ───────────────────────\n{code}\n\n{explanation}"
        return f"Random code: {topic.title()} in {language}.", detail

    def _bump_used(self, topic: str, title: str) -> None:
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute("UPDATE snippets SET used = used + 1 WHERE topic=? AND title=?",
                             (topic, title))
        except Exception:
            pass

    # ── stats ──────────────────────────────────────────────────

    def stats(self) -> Tuple[str, str]:
        with sqlite3.connect(self._db) as conn:
            total = conn.execute("SELECT COUNT(*) FROM snippets").fetchone()[0]
            langs = conn.execute(
                "SELECT language, COUNT(*) FROM snippets GROUP BY language ORDER BY 2 DESC"
            ).fetchall()
            tips = conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0]
            size = os.path.getsize(self._db) if os.path.exists(self._db) else 0
        lang_str = ", ".join(f"{l} {c}" for l, c in langs[:6])
        detail = (f"CodeFest dataset ({_fmt(size)} on D drive):\n"
                  f"  snippets: {total} across {len(langs)} languages\n"
                  f"  tips: {tips}\n"
                  f"  by language: {lang_str}")
        spoken = (f"Code dataset: {total} snippets, {tips} tips, stored at "
                  f"{_DB}. Ranked {lang_str}.")
        return spoken, detail

    # ── donation / self-learning ───────────────────────────────

    def add_snippet(self, language: str, topic: str, title: str,
                    code: str, explanation: str = "") -> str:
        """AK saves a newly learned coding pattern permanently (offline)."""
        language = self._normalise_lang(language) or "python"
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT INTO snippets (language, topic, title, code, explanation, source)"
                " VALUES (?, ?, ?, ?, ?, 'learned')",
                (language, topic[:80], title[:120], code, explanation[:400]),
            )
        return f"Learned and stored a new {language} snippet: {title}."


# ────────────────────────────────────────────────────────────────
#  tiny helpers
# ────────────────────────────────────────────────────────────────


def re_words(text: str) -> List[str]:
    import re as _re
    return _re.findall(r"[\w']+", text.lower())


def _fmt(size: int) -> str:
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"