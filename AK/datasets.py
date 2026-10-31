"""AK Dataset Registry — every dataset lives on the D drive, big or small.

  D:\\YOUR_DATA_PATH\\datasets\\          ← actual dataset files / folders
  D:\\YOUR_DATA_PATH\\datasets\\registry.sqlite3  ← the catalog

AK ships with a curated catalog of well-known public datasets (Kaggle /
GitHub / HuggingFace / datahub). You can register any local folder/file as a
dataset, browse the catalog, pull metadata, search inside registered datasets,
and even download a catalog entry straight to the D drive.

Designed for scale: registration, listing and search query the SQLite catalog
directly — a 10 GB dataset is handled exactly like a 10 KB one, because AK
never copies its contents, it indexes the location.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import tarfile
import urllib.request
import zipfile
import gzip
import bz2
import io
import shutil
from typing import Dict, List, Optional, Tuple

from .config import get_data_dir

_DATASETS_DIR = os.path.join(get_data_dir(), "datasets")
_REGISTRY = os.path.join(_DATASETS_DIR, "registry.sqlite3")

# Curated public dataset catalog: name, kind, source/url, description, tags.
# Every URL is a DIRECT download (raw GitHub / UCI / datahub) — no login pages.
# kind 'hub' = a portal to browse, not a single file.
_CATALOG: List[Dict[str, str]] = [
    # ── Classic ML / stats (around 1 MB) ────────────────────────
    {"name": "iris", "kind": "csv", "url": "https://raw.githubusercontent.com/selva86/datasets/master/Iris.csv",
     "desc": "150 flowers, 4 features, 3 species — the classic ML hello-world.", "tags": "machine learning csv iris"},
    {"name": "titanic", "kind": "csv", "url": "https://raw.githubusercontent.com/datasciencedojo/datasets/master/titanic.csv",
     "desc": "Passenger survival prediction dataset used in the Kaggle competition.", "tags": "survival kaggle csv"},
    {"name": "advertising", "kind": "csv", "url": "https://raw.githubusercontent.com/selva86/datasets/master/Advertising.csv",
     "desc": "TV/radio/newspaper ad spend vs sales — classic regression dataset.", "tags": "regression marketing csv"},
    {"name": "boston-housing", "kind": "csv", "url": "https://raw.githubusercontent.com/selva86/datasets/master/BostonHousing.csv",
     "desc": "Boston house prices with 13 features. Standard regression benchmark.", "tags": "housing regression csv"},
    {"name": "mtcars", "kind": "csv", "url": "https://raw.githubusercontent.com/selva86/datasets/master/mtcars.csv",
     "desc": "Motor Trend car road tests — mileage and 10 car attributes.", "tags": "cars csv r"},
    {"name": "wine-quality-red", "kind": "csv", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/wine-quality/winequality-red.csv",
     "desc": "Red wine physicochemical tests + quality score (1599 bottles).", "tags": "wine quality ml csv"},
    {"name": "penguins", "kind": "csv", "url": "https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv",
     "desc": "Palmer Archipelago penguin measurements — 344 penguins, 3 species.", "tags": "penguins biology ml csv"},
    {"name": "heart-disease", "kind": "csv", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/heart-disease/processed.cleveland.data",
     "desc": "Heart disease diagnosis — 303 patient records (UCI Cleveland).", "tags": "heart health ml csv"},
    {"name": "breast-cancer", "kind": "csv", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data",
     "desc": "Breast cancer diagnosis — 699 samples, 10 features (UCI).", "tags": "cancer health ml csv"},
    {"name": "forest-fires", "kind": "csv", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/forest-fires/forestfires.csv",
     "desc": "Forest fire area burned — 517 records, Montesinho park.", "tags": "fires weather ml csv"},
    {"name": "wine-quality-white", "kind": "csv", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/wine-quality/winequality-white.csv",
     "desc": "White wine physicochemical tests + quality score (4898 bottles).", "tags": "wine quality ml csv"},
    {"name": "auto-mpg", "kind": "data", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/auto-mpg/auto-mpg.data",
     "desc": "Fuel consumption & car attributes, 398 cars (UCI).", "tags": "cars mpg ml csv"},
    {"name": "adult-income", "kind": "csv", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/adult/adult.data",
     "desc": "Census income prediction — 32,561 US adults (UCI).", "tags": "income census ml csv"},
    {"name": "german-credit", "kind": "data", "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/statlog/german/german.data",
     "desc": "1000 bank loan applications — credit risk (UCI statlog).", "tags": "credit risk finance ml"},
    # ── World / geo / economy (datahub datasets.org) ────────────
    {"name": "airport-codes", "kind": "csv", "url": "https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv",
     "desc": "Global airport codes, locations and metadata.", "tags": "airports geo csv"},
    {"name": "country-codes", "kind": "csv", "url": "https://raw.githubusercontent.com/datasets/country-codes/master/data/country-codes.csv",
     "desc": "ISO country codes: alpha-2/3, numeric, dial, FIFA, continent.", "tags": "countries iso geo csv"},
    {"name": "world-cities", "kind": "csv", "url": "https://raw.githubusercontent.com/datasets/world-cities/master/data/world-cities.csv",
     "desc": "World cities with country & sub-country (grouped by geonameid).", "tags": "cities geo csv"},
    {"name": "population", "kind": "csv", "url": "https://raw.githubusercontent.com/datasets/population/master/data/population.csv",
     "desc": "Country population by year (1960–2017), World Bank.", "tags": "population worldbank csv"},
    {"name": "gdp", "kind": "csv", "url": "https://raw.githubusercontent.com/datasets/gdp/master/data/gdp.csv",
     "desc": "Country GDP in current US dollars by year.", "tags": "gdp economy worldbank csv"},
    {"name": "geo-countries", "kind": "geojson", "url": "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson",
     "desc": "All countries as GeoJSON polygons (14 MB) for maps.", "tags": "geo countries map geojson"},
    # ── Text / NLP / social (a few MB) ──────────────────────────
    {"name": "sarcasm-news", "kind": "json", "url": "https://raw.githubusercontent.com/rishabhmisra/News-Headlines-Dataset-For-Sarcasm-Detection/master/Sarcasm_Headlines_Dataset.json",
     "desc": "26,709 news headlines labelled sarcastic or not.", "tags": "nlp sarcasm classification json"},
    {"name": "football-premier-league", "kind": "json", "url": "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json",
     "desc": "English Premier League 2025-26 matches, teams & scores.", "tags": "football sports json"},
    # ── Big data (100 MB – 500 MB, fine for offline) ────────────
    {"name": "covid-states", "kind": "csv", "url": "https://raw.githubusercontent.com/nytimes/covid-19-data/master/us-states.csv",
     "desc": "NYT COVID-19 cases & deaths by US state, daily.", "tags": "covid health time series csv"},
    {"name": "covid-counties", "kind": "csv", "url": "https://raw.githubusercontent.com/nytimes/covid-19-data/master/us-counties.csv",
     "desc": "NYT COVID-19 cases & deaths by US county, daily (104 MB).", "tags": "covid health time series big csv"},
    # ── Cybersecurity / bug-bounty intel (legal research) ───────
    {"name": "cisa-kev", "kind": "json", "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
     "desc": "CISA catalog of known-exploited vulnerabilities — the #1 bug-bounty reference feed.", "tags": "cve vulnerabilities security kev"},
    {"name": "malwarebazaar-recent", "kind": "csv", "url": "https://bazaar.abuse.ch/export/csv/recent/",
     "desc": "Last 60 min of malware hashes & intel from Abuse.ch MalwareBazaar.", "tags": "malware hashes security intel csv"},
    {"name": "malwarebazaar-full", "kind": "zip", "url": "https://bazaar.abuse.ch/export/csv/full/",
     "desc": "Full MalwareBazaar intel export — 200K+ samples (hashes/metadata, 221 MB zip).", "tags": "malware hashes security big zip csv"},
    {"name": "mitre-attack", "kind": "json", "url": "https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json",
     "desc": "MITRE ATT&CK enterprise STIX 2.1 — every tactic/technique/procedure (the advanced attacker playbook).", "tags": "security mitre attack stix advanced techniques"},
    {"name": "owasp-asvs", "kind": "csv", "url": "https://raw.githubusercontent.com/OWASP/ASVS/master/4.0/OWASP%20ASVS%204.0%20checklist.csv",
     "desc": "OWASP ASVS 4.0 checklist — the full app-security verification standard (L1/L2/L3).", "tags": "security owasp asvs checklist auditing"},
    {"name": "nuclei-templates", "kind": "tar.gz", "url": "https://codeload.github.com/projectdiscovery/nuclei-templates/tar.gz/refs/heads/main",
     "desc": "Thousands of ready-to-run detection templates (authorized scans) — big catalog, needs time to download.", "tags": "security nuclei templates scanning"},
    # ── Hub pointers (browse, then register) ────────────────────
    {"name": "olist-ecommerce", "kind": "hub", "url": "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
     "desc": "100k Brazilian e-commerce orders across 9 CSV files.", "tags": "hub ecommerce analytics"},
    {"name": "movies-2026", "kind": "hub", "url": "https://www.kaggle.com/datasets/rewantbhriguvanshi/recent-movies-and-entertainment-dataset-20242026",
     "desc": "Recent movies & entertainment metadata (2024–2026).", "tags": "hub movies entertainment"},
    {"name": "student-performance", "kind": "hub", "url": "https://www.kaggle.com/datasets/harshadapatil31/student-performance-and-study-habits-dataset",
     "desc": "Student exam performance & study habits survey data.", "tags": "hub education exam"},
    {"name": "fake-real-news", "kind": "hub", "url": "https://www.kaggle.com/datasets/ayushmansatapathy/fake-vs-real-news-dataset",
     "desc": "Fake vs real news headlines for text classification.", "tags": "hub nlp news classification"},
    {"name": "energy-consumption", "kind": "hub", "url": "https://www.kaggle.com/datasets/vitthalmadane/energy-consumption-time-series-dataset",
     "desc": "Time-series electric power consumption samples.", "tags": "hub energy time series"},
    {"name": "steam-games", "kind": "hub", "url": "https://www.kaggle.com/datasets/ktsagarj/steam-games-dataset-v2-20000-rows",
     "desc": "20,000+ Steam game rows with metadata and reviews.", "tags": "hub games steam"},
    {"name": "huggingface-datasets-hub", "kind": "hub", "url": "https://huggingface.co/datasets",
     "desc": "Hugging Face hub — thousands of ready-to-use datasets on demand.", "tags": "hub huggingface any"},
    {"name": "kaggle-open-data", "kind": "hub", "url": "https://www.kaggle.com/datasets",
     "desc": "Kaggle's open-data catalogue — search and grab any dataset.", "tags": "hub kaggle any"},
    {"name": "datahub-core", "kind": "hub", "url": "https://datahub.io/core",
     "desc": "datahub curated core datasets (finance, geo, climate, social).", "tags": "hub datahub core"},
]

_SEARCHABLE_EXT = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js",
                   ".html", ".xml", ".sql", ".yaml", ".yml", ".ini"}


class DatasetRegistry:
    def __init__(self, registry_path: str = ""):
        self._db = registry_path or _REGISTRY
        os.makedirs(_DATASETS_DIR, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS datasets ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "name TEXT NOT NULL UNIQUE,"
                "kind TEXT NOT NULL DEFAULT 'file',"
                "path TEXT NOT NULL DEFAULT '',"
                "source TEXT NOT NULL DEFAULT '',"
                "desc TEXT NOT NULL DEFAULT '',"
                "tags TEXT NOT NULL DEFAULT '',"
                "bytes INTEGER NOT NULL DEFAULT 0,"
                "records INTEGER NOT NULL DEFAULT 0,"
                "added TEXT NOT NULL,"
                "updated TEXT NOT NULL"
                ")"
            )

    # ────────────────────────────────────────────────────────────
    #  Catalog (built-in "best of" public datasets)
    # ────────────────────────────────────────────────────────────

    def catalog(self) -> Tuple[str, str]:
        lines = ["Curated public dataset catalog:"]
        for i, d in enumerate(_CATALOG, 1):
            lines.append(f"  {i}. {d['name']} ({d['kind']}) — {d['desc']}")
        spoken = (f"Catalog has {len(_CATALOG)} curated datasets. "
                  "Say: add dataset <name>, or search dataset <topic>.")
        return spoken, "\n".join(lines)

    def catalog_info(self, name: str) -> Optional[Dict[str, str]]:
        n = name.strip().lower()
        for d in _CATALOG:  # exact name first
            if d["name"].lower() == n:
                return dict(d)
        for d in _CATALOG:  # then name substring
            if n in d["name"].lower():
                return dict(d)
        for d in _CATALOG:  # tag fuzz only as a last resort
            if any(n in w for w in d["tags"].lower().split()):
                return dict(d)
        return None

    # ────────────────────────────────────────────────────────────
    #  Register / manage local datasets (any size — location only)
    # ────────────────────────────────────────────────────────────

    def _measure(self, path: str) -> Tuple[int, int]:
        """Estimate (total_bytes, records) — records with a cheap heuristic
        for huge datasets (capped scan), so 10 GB is fine."""
        bytes_total, records = 0, 0
        try:
            if os.path.isfile(path):
                bytes_total = os.path.getsize(path)
                records = _count_records(path)
                return bytes_total, records
        except Exception:
            pass
        seen_files = 0
        for root, _dn, files in os.walk(path):
            for fn in files:
                try:
                    fp = os.path.join(root, fn)
                    size = os.path.getsize(fp)
                    bytes_total += size
                    if size > 0 and seen_files < 20 and _is_text_file(fp):
                        records += _count_records(fp)
                        seen_files += 1
                except OSError:
                    continue
            if seen_files >= 40:
                break
        return bytes_total, records

    def register(self, path: str, name: str = "", source: str = "") -> str:
        path = os.path.expandvars(os.path.expanduser((path or "").strip().strip('"')))
        if not os.path.exists(path):
            return f"Path not found: {path}"
        name = (name or os.path.basename(os.path.normpath(path))).strip()
        kind = "file" if os.path.isfile(path) else "folder"
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            kind = ext or "file"
        size, records = self._measure(path)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT INTO datasets (name, kind, path, source, desc, tags, bytes, records, added, updated)"
                " VALUES (?, ?, ?, ?, '', '', ?, ?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, path=excluded.path,"
                " bytes=excluded.bytes, records=excluded.records, updated=excluded.updated",
                (name, kind, path, source, size, records, now, now),
            )
        return f"Dataset '{name}' registered locally at {path} ({_fmt(size)})."

    def _row(self, name: str) -> Optional[tuple]:
        with sqlite3.connect(self._db) as conn:
            return conn.execute(
                "SELECT name, kind, path, source, bytes, records, added, updated"
                " FROM datasets WHERE lower(name)=lower(?)", (name.strip(),)
            ).fetchone()

    def list_registered(self) -> Tuple[str, str]:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                "SELECT name, kind, bytes, records FROM datasets ORDER BY name"
            ).fetchall()
        if not rows:
            return ("No registered datasets yet. Say: add dataset <name> to install "
                    "a curated one, or: register dataset <folder path>.", "")
        total = sum(r[2] for r in rows)
        lines = [f"Registered datasets ({_fmt(total)} on D drive):"]
        for name, kind, size, records in rows:
            lines.append(f"  • {name} ({kind}) — {_fmt(size)}"
                         + (f", ~{records} records" if records else ""))
        lines.append(f"Total: {len(rows)} datasets.")
        return ("I have " + " and ".join(r[0] for r in rows[:3])
                + (f", plus {len(rows)-3} more" if len(rows) > 3 else "") + ".", "\n".join(lines))

    def info(self, name: str) -> Tuple[str, str]:
        # Local first, then curated catalog.
        row = self._row(name)
        if row:
            rname, kind, rpath, source, size, records, added, updated = row
            detail = (f"Dataset: {rname}\n  kind: {kind}\n  path: {rpath}\n"
                      f"  size: {_fmt(size)}\n  records: ~{records}\n"
                      f"  added: {added}\n  updated: {updated}")
            return (f"{rname}, {kind}, {_fmt(size)} of data"
                    + (f", about {records} records" if records else "") + ".", detail)
        c = self.catalog_info(name)
        if c:
            detail = (f"Catalog entry: {c['name']}\n  kind: {c['kind']}\n  source: {c['url']}\n"
                      f"  description: {c['desc']}\n  tags: {c['tags']}")
            return (f"{c['name']} is in my catalog — {c['desc']}. Say: add dataset {c['name']} to install it.", detail)
        return f"Dataset '{name}' not found locally or in the catalog.", ""

    def remove(self, name: str) -> str:
        with sqlite3.connect(self._db) as conn:
            cur = conn.execute("DELETE FROM datasets WHERE lower(name)=lower(?)", (name.strip(),))
            if cur.rowcount:
                return f"Dataset '{name}' unregistered."
        return f"Dataset '{name}' not found."

    # ────────────────────────────────────────────────────────────
    #  Download a curated dataset to the D drive
    # ────────────────────────────────────────────────────────────

    def download(self, name: str) -> str:
        c = self.catalog_info(name)
        if not c:
            return f"'{name}' is not in the catalog. See: show dataset catalog."
        if c["kind"] == "hub":
            return (f"{c['name']} is a data hub, not a single file — open {c['url']} "
                    "and say: register dataset <path> once downloaded.")
        dest_dir = os.path.join(_DATASETS_DIR, c["name"])
        os.makedirs(dest_dir, exist_ok=True)
        fname = _url_filename(c["url"], f"{c['name']}.csv")
        dest = os.path.join(dest_dir, fname)
        try:
            print(f"[datasets] downloading {c['url']} ...")
            with urllib.request.urlopen(c["url"], timeout=180) as r, \
                    open(dest, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
        except Exception as exc:
            return f"Download failed: {exc}. Check the source URL at {c['url']}"
        archive = _is_archive(dest)
        target = dest_dir if archive else dest
        if archive:
            _extract_archive(dest, dest_dir)
        size, records = self._measure(target)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        kind = "file" if os.path.isfile(target) else "folder"
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT INTO datasets (name, kind, path, source, desc, tags, bytes, records, added, updated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, path=excluded.path,"
                " source=excluded.source, bytes=excluded.bytes, records=excluded.records,"
                " updated=excluded.updated",
                (c["name"], kind, target, c["url"], c["desc"], c["tags"],
                 size, records, now, now),
            )
        note = f" (extracted to a folder)" if archive else ""
        return f"Downloaded '{c['name']}' to {target} ({_fmt(size)}){note}."

    def download_all(self) -> Tuple[str, str]:
        """Download every catalog entry that is a direct file (skips hubs)."""
        todo = [d for d in _CATALOG if d["kind"] != "hub"]
        done, skipped, failed = 0, 0, 0
        lines = []
        for d in todo:
            try:
                if self.catalog_info(d["name"]) and any(
                        r[0].lower() == d["name"].lower()
                        for r in self._all_rows()):
                    skipped += 1
                    lines.append(f"  • {d['name']}: already present, skipped")
                    continue
                msg = self.download(d["name"])
                if msg.startswith("Download failed"):
                    failed += 1
                    lines.append(f"  ✗ {d['name']}: {msg}")
                else:
                    done += 1
                    lines.append(f"  ✓ {d['name']}: {msg}")
            except Exception as e:
                failed += 1
                lines.append(f"  ✗ {d['name']}: {e}")
        spoken = (f"Dataset sweep complete: {done} downloaded, {skipped} already there, "
                  f"{failed} failed.")
        return spoken, "\n".join(lines) or "Nothing to do."

    def _all_rows(self) -> List[tuple]:
        with sqlite3.connect(self._db) as conn:
            return conn.execute(
                "SELECT name, kind, path, source, bytes, records, added, updated"
                " FROM datasets").fetchall()

    # ────────────────────────────────────────────────────────────
    #  Offline analytics: preview / head / describe a dataset
    # ────────────────────────────────────────────────────────────

    def analyze(self, name: str, n: int = 5) -> Tuple[str, str]:
        """Preview a registered dataset offline — columns + first n rows.
        For CSV/JSON/GeoJSON it prints a friendly table; for big files it
        only reads the first chunk, so a 10 GB dataset is still instant."""
        row = self._row(name)
        if not row:
            return f"Dataset '{name}' is not registered. Say: add dataset {name}.", ""
        _n, kind, path, _src, size, records, _a, _u = row
        if not path or not os.path.exists(path):
            return f"Dataset '{name}' is missing from its path ({path}).", ""
        preview = _pick_preview_file(path)
        if not preview:
            return f"Dataset '{name}' has no readable text files to preview.", ""
        if preview != path:
            size = os.path.getsize(preview)
        try:
            head = _read_head(preview, n)
        except Exception as exc:
            return f"Could not read '{name}': {exc}", ""
        if not head:
            return f"Dataset '{name}' has no readable rows.", ""
        lines = [f"{name} preview ({_fmt(size)}"
                 + (f", ~{records} records" if records else "") + "):"]
        lines.append("  columns: " + ", ".join(head["columns"][:12]))
        for rowvals in head["rows"]:
            lines.append("  · " + " | ".join(str(v) for v in rowvals))
        spoken = (f"{name} is ready offline — {len(head['columns'])} columns, "
                  f"shown first {len(head['rows'])} rows.")
        return spoken, "\n".join(lines)

    # ────────────────────────────────────────────────────────────
    #  Search inside registered datasets (bounded, offline)
    # ────────────────────────────────────────────────────────────

    def search(self, query: str) -> Tuple[str, str]:
        """Case-insensitive keyword search across registered text datasets.
        Bounded per file (lines + bytes) so a 10 GB dataset never hangs AK,
        and bounded per file count so big datasets can't starve the scan."""
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                "SELECT name, path, kind FROM datasets WHERE path != ''"
            ).fetchall()
        if not rows:
            return "No registered datasets to search. Say: list datasets.", ""
        q = query.lower().strip()
        if not q:
            return "Say a word to search for, like: search dataset sql injection.", ""
        bytes_q = q.encode("utf-8")
        hits: list = []
        line_budget = 20000    # first lines read per file
        byte_budget = 8 << 20  # 8 MB text read per file
        max_files = 40         # files scanned per dataset
        max_hits = 40          # stop once we have plenty of results
        for name, path, kind in rows:
            if kind in ("hub", "file") and not path:
                continue
            files = [path] if os.path.isfile(path) else _walk_text(path)
            local_hits = []
            for fp in files[:max_files]:
                if len(hits) >= max_hits:
                    break
                if os.path.getsize(fp) >= (512 << 20):  # skip >512 MB files
                    continue
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        scanned_bytes = 0
                        for i, line in enumerate(fh, 1):
                            scanned_bytes += len(line)
                            if q in line.lower():
                                local_hits.append(f"{os.path.basename(fp)}:{i} - {line.strip()[:90]}")
                                if len(local_hits) >= 3:
                                    break
                            if i >= line_budget or scanned_bytes >= byte_budget:
                                break
                except Exception:
                    continue
            for h in local_hits[:2]:
                hits.append(f"  [{name}] {h}")
        if not hits:
            return f"No matches for '{query}' across registered datasets.", ""
        spoken = f"Found {len(hits)} matches for '{query}' across my local datasets."
        return spoken, "Dataset search:\n" + "\n".join(hits)

    def stats(self) -> Tuple[str, str]:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute("SELECT kind, bytes FROM datasets").fetchall()
            total = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        by_kind: Dict[str, int] = {}
        total_bytes = 0
        for kind, size in rows:
            by_kind[kind] = by_kind.get(kind, 0) + (size or 0)
            total_bytes += size or 0
        detail = (f"Dataset stats:\n  registered: {total}\n  total size: {_fmt(total_bytes)}\n"
                  + "\n".join(f"  {k}: {_fmt(v)}" for k, v in sorted(by_kind.items())))
        spoken = (f"Dataset storage: {total} registered, {_fmt(total_bytes)} total on the D drive.")
        return spoken, detail


# ────────────────────────────────────────────────────────────────
#  helpers
# ────────────────────────────────────────────────────────────────

def _fmt(size: int) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


def _is_text_file(fp: str) -> bool:
    return os.path.splitext(fp)[1].lower() in _SEARCHABLE_EXT


def _count_records(fp: str) -> int:
    """Heuristic record count: CSV/JSON-lines = lines; other text = lines too.
    Capped to the first 20k lines for speed."""
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
            n = 0
            for n, _ in enumerate(fh, 1):
                if n >= 20000:
                    return 20000
            return n
    except Exception:
        return 0


def _walk_text(path: str) -> List[str]:
    out = []
    try:
        for root, _dn, files in os.walk(path):
            for fn in files:
                fp = os.path.join(root, fn)
                if _is_text_file(fp) and os.path.getsize(fp) > 0:
                    out.append(fp)
                    if len(out) >= 200:
                        return out
    except Exception:
        pass
    return out


# ────────────────────────────────────────────────────────────────
#  archive + preview helpers (stdlib only)
# ────────────────────────────────────────────────────────────────

def _url_filename(url: str, fallback: str) -> str:
    name = url.split("?")[0].rsplit("/", 1)[-1].strip()
    return name if name else fallback


def _is_archive(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except Exception:
        return False
    if magic[:2] in (b"PK", b"\x1f\x8b", b"BZh"):
        return True
    low = os.path.basename(path).lower()
    return low.endswith((".zip", ".tgz", ".tar.gz", ".tar.bz2", ".tar"))


def _extract_archive(path: str, dest_dir: str) -> str:
    """Extract by magic bytes (not just extension) — handles zips, tars,
    tar.gz/tar.bz2 and single-file gzip/bzip2 for any name."""
    low = os.path.basename(path).lower()
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            z.extractall(dest_dir)
    elif magic[:2] == b"\x1f\x8b":  # gzip
        if low.endswith((".tar.gz", ".tgz")):
            with tarfile.open(path, "r:gz") as t:
                t.extractall(dest_dir)
        else:  # single-file gzip (e.g. a .csv.gz)
            dest = path[:-3] if low.endswith(".gz") else path
            if dest == path:
                dest = os.path.splitext(path)[0]
            if os.path.exists(dest):
                for i in range(1, 100):
                    cand = f"{dest}.{i}"
                    if not os.path.exists(cand):
                        dest = cand
                        break
            with gzip.open(path, "rb") as fi, open(dest, "wb") as fo:
                shutil.copyfileobj(fi, fo)
    elif magic[:2] == b"BZh":  # bzip2
        if low.endswith(".tar.bz2"):
            with tarfile.open(path, "r:bz2") as t:
                t.extractall(dest_dir)
        else:
            dest = path[:-4] if low.endswith(".bz2") else path
            if os.path.exists(dest):
                dest = f"{os.path.splitext(path)[0]}.1"
            with bz2.open(path, "rb") as fi, open(dest, "wb") as fo:
                shutil.copyfileobj(fi, fo)
    elif magic[:5] == b"ustar" or low.endswith(".tar"):
        with tarfile.open(path, "r:") as t:
            t.extractall(dest_dir)
    return dest_dir


def _read_head(path: str, n: int = 5) -> Optional[Dict]:
    """Cheap preview: columns + first n rows for csv/json/geojson/text."""
    import csv
    low = os.path.basename(path).lower()
    if low.endswith((".json", ".geojson")):
        size = os.path.getsize(path)
        full = size <= 40 * 1024 * 1024
        raw = _read_first_chunk(path, 40 * 1024 * 1024 if full else 4 * 1024 * 1024)
        if not raw:
            return None
        data = None
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if data is None:  # JSON-lines (one object per line)
            objs = []
            for line in raw.splitlines():
                s = line.strip()
                if not s or s in ("[", "]"):
                    continue
                try:
                    o = json.loads(s.rstrip(","))
                    objs.append(o)
                    if len(objs) >= n:
                        break
                except Exception:
                    continue
            if not objs:
                return None
            columns = list(objs[0].keys()) if isinstance(objs[0], dict) else ["value"]
            rows = [[o.get(c) for c in columns] for o in objs[:n]] if columns != ["value"] else [[o] for o in objs[:n]]
            return {"columns": columns, "rows": rows}
        if isinstance(data, dict) and "features" in data:  # GeoJSON
            feats = data["features"][:n]
            columns = sorted({k for f in feats for k in f.get("properties", {})})
            rows = [[f.get("properties", {}).get(c) for c in columns] for f in feats]
        elif isinstance(data, list):
            columns = list(data[0].keys()) if data and isinstance(data[0], dict) else ["value"]
            rows = [[d.get(c) for c in columns] for d in data[:n]] if columns != ["value"] else [[d] for d in data[:n]]
        else:
            columns = list(data.keys())
            rows = [list(data.values())]
        return {"columns": columns, "rows": rows}
    if low.endswith((".csv", ".data", ".txt")):
        text_chunk = _read_first_chunk(path, 2 * 1024 * 1024)
        if not text_chunk:
            return None
        lines = text_chunk.splitlines()
        lines = [ln for ln in lines if ln and not ln.lstrip().startswith("#")]
        if not lines:
            return None
        delim = _sniff_delimiter(lines[0])
        rows = []
        columns: List[str] = []
        try:
            reader = csv.reader(io.StringIO("\n".join(lines[: n + 1])), delimiter=delim)
            all_rows = [r for r in reader if r]
            if all_rows:
                columns = all_rows[0]
                rows = all_rows[1: n + 1]
        except Exception:
            pass
        if not columns:
            columns = [f"col{i + 1}" for i in range(1)]
            rows = [lines[:n]]
        return {"columns": columns, "rows": rows}
    return None


def _sniff_delimiter(line: str) -> str:
    for d in (",", ";", "\t", "|", " "):
        if line.count(d) >= 3:
            return d
    return ","


def _read_first_chunk(path: str, limit: int) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read(limit)


def _pick_preview_file(path: str) -> Optional[str]:
    """For a folder dataset, pick the juiciest text file (largest tabular
    file first, else any of the first few text files)."""
    if os.path.isfile(path):
        return path
    best, best_score = None, -1
    for root, _dn, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            low = fn.lower()
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            if low.endswith(".csv") or low.endswith(".json"):
                score = size * 2
            elif low.endswith((".txt", ".log", ".data")):
                score = size // 2
            else:
                score = -1
            if score > best_score:
                best_score, best = score, fp
        if best and best_score > 0:
            break
    return best