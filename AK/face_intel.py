"""Face Intel — camera se chehra lo, uski public online info dhundho.

Best-effort OSINT pipeline, sirf PUBLIC data (no login/password scraping):
  1) capture_face()  — kamera khole, chehra crop karke save kare
  2) investigate()   — reverse image search + name OSINT (if naam mila):
       * DuckDuckGo web search (bina API-key)
       * Wikipedia bio + profile photo
       * GitHub user search (public API)
       * Social profile probe (github / x / instagram / linkedin)
  3) run_session()   — capture + investigate ek saath, report.json me likhta hai
  4) build_spoken_summary() — Hinglish voice summary for AK

Har result public report.json me hota hai taaki GUI (face_intel_ui.py) live
render kar sake. Reverse-image engines bina API ne offline host nahi karte,
isliye best-effort Yandex callback try + browser buttons fallback.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Callable, Optional

try:
    import cv2
    import numpy as np
except Exception:                      # pragma: no cover
    cv2 = None

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_lk = threading.Lock()


# ── report writers / readers ────────────────────────────────────────────
def read_report(session_dir: str) -> dict:
    """Read latest report.json (missing/corrupt → bare skeleton)."""
    p = os.path.join(session_dir, "report.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"status": "starting", "steps": []}


def _write_report(session_dir: str, report: dict) -> None:
    with _lk:
        report["updated"] = time.strftime("%H:%M:%S")
        p = os.path.join(session_dir, "report.json")
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _log(report: dict, step: str) -> None:
    steps = report.setdefault("steps", [])
    if not steps or steps[-1] != step:
        steps.append(step)
        if len(steps) > 40:
            report["steps"] = steps[-40:]


# ── camera capture ──────────────────────────────────────────────────────
def _cascade_path() -> Optional[str]:
    if cv2 is None:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(here, "face", "haarcascade_frontalface_default.xml"),
        os.path.join(here, "face", "haarcascade_frontalface_alt2.xml"),
    ]
    if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
        cands.append(os.path.join(cv2.data.haarcascades,
                                  "haarcascade_frontalface_default.xml"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _identify(data_dir: str, gray_patch_200: "np.ndarray") -> tuple:
    """Check face against face_lock registered users → (name, conf) or (None, None)."""
    try:
        from .face_lock import FaceLock
        fl = FaceLock(os.path.join(data_dir, "face_lock"))
    except Exception:
        return None, None
    if fl._recognizer is None or not fl._labels:
        return None, None
    try:
        label, conf = fl._recognizer.predict(gray_patch_200)
        if conf <= fl.threshold:
            return fl._labels.get(int(label)), float(conf)
    except Exception:
        pass
    return None, None


def capture_face(session_dir: str, data_dir: str = "",
                 cam_index: int = 0, timeout: float = 25.0) -> dict:
    """Kamera khole, best chehra pakde, frame + crop save kare.

    Returns dict: face_path, frame_path, name, conf, found (bool).
    """
    out = {"face_path": None, "frame_path": None,
           "name": None, "conf": None, "found": False}
    if cv2 is None:
        return out
    cascade_file = _cascade_path()
    if not cascade_file:
        return out
    os.makedirs(session_dir, exist_ok=True)
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        return out
    cascade = cv2.CascadeClassifier(cascade_file)
    if cascade.empty():
        cap.release()
        return out
    best_patch, best_frame, best_conf = None, None, None
    deadline = time.time() + timeout
    cv2.namedWindow("AK — Face Intel", cv2.WINDOW_NORMAL)
    cv2.moveWindow("AK — Face Intel", 160, 100)
    cv2.resizeWindow("AK — Face Intel", 520, 400)
    try:
        while time.time() < deadline:
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(90, 90))
            for (x, y, w, h) in faces[:2]:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 200, 255), 2)
                cx, cy = max(x - 8, 0), max(y - 8, 0)
                if cx + w + 16 > gray.shape[1] or cy + h + 16 > gray.shape[0]:
                    continue
                patch = cv2.resize(
                    gray[cy:cy + h + 16, cx:cx + w + 16],
                    (200, 200)).astype(np.uint8)
                if patch.std() < 12:
                    continue
                # keep the sharpest patch (max Laplacian variance = best sharpness)
                lap = cv2.Laplacian(patch, cv2.CV_64F).var()
                if best_conf is None or lap > best_conf:
                    best_patch, best_frame, best_conf = patch, frame, lap
            frame[12:44, 12:520] = (18, 18, 18)
            cv2.putText(frame, "Camera ke saamne chehra dikhao...",
                        (18, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)
            cv2.imshow("AK — Face Intel", frame)
            cv2.waitKey(1)
            if best_patch is not None and time.time() - deadline < -6:
                break
            if best_conf is not None and best_conf > 120:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if best_patch is None:
        return out
    frame_path = os.path.join(session_dir, "frame.png")
    face_path = os.path.join(session_dir, "face.png")
    try:
        cv2.imwrite(frame_path, best_frame)
        cv2.imwrite(face_path, best_patch)
    except Exception:
        return out
    name, conf = (None, None)
    if data_dir:
        name, conf = _identify(data_dir, best_patch)
    out.update({"face_path": face_path, "frame_path": frame_path,
                "name": name, "conf": conf, "found": True})
    return out


# ── online OSINT (public data only) ─────────────────────────────────────
def _get(url: str, timeout: float = 9.0, **kw):
    kw.setdefault("headers", {"User-Agent": _UA})
    kw.setdefault("timeout", timeout)
    return requests.get(url, **kw)


# throttling: don't hammer the free engines and get IP-blocked.
_last_req_at = 0.0


def _throttle(cooldown: float) -> None:
    """Sleep enough so consecutive network calls respect a cooldown."""
    global _last_req_at
    now = time.monotonic()
    wait = cooldown - (now - _last_req_at)
    if wait > 0:
        time.sleep(wait)
    _last_req_at = time.monotonic()


def _yandex_reverse(face_path: str) -> Optional[str]:
    with open(face_path, "rb") as fh:
        files = {"upfile": ("face.png", fh, "image/png")}
        r = requests.post(
            "https://yandex.com/images-search?rpt=imageview", files=files,
            headers={"User-Agent": _UA}, timeout=12.0, allow_redirects=True)
    if r.status_code != 200:
        return None
    m = re.search(r'cbir-id\s*=\s*["\']?([0-9A-Za-z/=\-_]+)', r.text)
    if m:
        return ("https://yandex.com/images/search?cbir_id="
                + m.group(1).replace("/", "%2F"))
    m = re.search(r'https://yandex\.com/images/search\?[^"\'&> ]+', r.text)
    if m:
        return m.group(0)
    # last hop: sometimes it 302s to the result page
    if r.url and "cbir" in r.url:
        return r.url
    return None


def _bing_reverse(face_path: str) -> Optional[str]:
    """Bing Visual upload — best-effort; returns a result page URL or None."""
    with open(face_path, "rb") as fh:
        files = {"image": ("face.png", fh, "image/png")}
        r = requests.post(
            "https://www.bing.com/images/search?view=detailv2&iss=sbi"
            "&form=SBIHMP&q=imgurl:",
            files=files, headers={"User-Agent": _UA},
            timeout=12.0, allow_redirects=True)
    if r.status_code != 200:
        return None
    m = re.search(r'action="([^"]*images/search\?[^"]*)"', r.text)
    if m:
        return "https://www.bing.com" + m.group(1) if m.group(1).startswith("/") else m.group(1)
    if r.url and "images/search" in r.url:
        return r.url
    return None


def reverse_image_search(face_path: str, report: dict,
                         cooldown: float = 4.0,
                         attempts: int = 2) -> dict:
    """Best-effort multi-engine reverse image search with throttling.

    - Tries Yandex first, then Bing Visual, then always exposes manual
      browser buttons (Google Lens / Yandex / Bing / TinEye) as fallback.
    - `cooldown` seconds between engines, max `attempts` network calls —
      so AK never spams the free engines and stays un-blocked.
    """
    _log(report, "reverse image search — engines ke beech cooldown ke saath")
    engines = [("Yandex", _yandex_reverse), ("Bing Visual", _bing_reverse)]
    results = {}
    made = 0
    for name, fn in engines:
        if made >= attempts:
            break
        made += 1
        try:
            _throttle(cooldown)
            url = fn(face_path)
        except Exception:
            url = None
        results[name] = url
        if url:
            _log(report, f"{name} se reverse-search link mil gaya")

    found = next((u for u in results.values() if u), None)
    _log(report, (f"{sum(1 for u in results.values() if u)}/" 
                  f"{made} engine se result mila"
                  if found else
                  "engines ne bina key ke auto link nahi diya — "
                  "browser buttons se manual upload karo (throttle se block bach gaya)"))
    return {
        "yandex": results.get("yandex"),
        "bing": results.get("bing"),
        "best": found,
        "manual": {
            "Google Lens": "https://lens.google.com",
            "Yandex": "https://yandex.com/images",
            "Bing Visual": "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIHMP",
            "TinEye": "https://tineye.com/",
        },
    }


_JUNK_HOSTS = {
    "www.bing.com", "duckduckgo.com", "yandex.com", "yandex.ru",
    "www.ups.com", "www.theupsstore.com", "locations.theupsstore.com",
    "www.ordertracker.com", "www.17track.net", "www.calculator.net",
    "www.bing.com/ck", "answers.microsoft.com", "forum.lowyat.net",
    "play.google.com",
}


def _clean_results(results: list, query_words, n: int = 8) -> list:
    """Junk hosts drop karo + relevance score se sort karo."""
    scored = []
    for r in results:
        url = (r.get("url") or "").lower()
        host = url.split("//")[-1].split("/")[0]
        if any(h in host for h in _JUNK_HOSTS) or "/ck/a" in url:
            continue
        hay = ((r.get("title") or "") + " " + (r.get("snippet") or "")).lower()
        score = 0
        for w in query_words:
            if len(w) >= 3 and w.lower() in hay:
                score += 2
            if w.lower() in host:
                score += 3
        scored.append((score, r))
    # keep only results with at least one relevant token (or top if none)
    relevant = [s for s in scored if s[0] > 0]
    if not relevant:
        relevant = scored
    relevant.sort(key=lambda kv: -kv[0])
    return [r for _, r in relevant[:n]]


def web_search(query: str, n: int = 8) -> list:
    """Multi-engine web search (DuckDuckGo HTML, Bing fallback). Real data."""
    query_words = [w for w in re.split(r"\W+", query) if w and w.lower() not in
                   ("the", "and", "for", "who", "what", "this", "that")]
    results = _ddg_search(query, n)
    if not results:
        results = _bing_search(query, n)
    return _clean_results(results, query_words, n)


def _ddg_search(query: str, n: int = 8) -> list:
    results = []
    try:
        r = _get("https://html.duckduckgo.com/html/?q=" +
                 requests.utils.quote(query), timeout=5.0)
        if r.status_code != 200:
            return results
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select(".result__a")[:n]:
            href = a.get("href", "")
            m = re.search(r"uddg=([^&]+)", href)
            url = requests.utils.unquote(m.group(1)) if m else href
            title = a.get_text(" ", strip=True)
            snip = ""
            sn = a.find_next("a", class_="result__snippet")
            if sn:
                snip = sn.get_text(" ", strip=True)
            results.append({"title": title, "url": url, "snippet": snip})
    except Exception:
        pass
    return results


def _bing_search(query: str, n: int = 8) -> list:
    """Bing HTML scraped results — fallback when DDG empty."""
    results = []
    try:
        r = requests.get(
            "https://www.bing.com/search?q=" + requests.utils.quote(query),
            headers={"User-Agent": _UA}, timeout=15.0)
        if r.status_code != 200:
            return results
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("li.b_algo")[:n]:
            a = li.select_one("h2 a")
            if not a:
                continue
            url = _bing_real_url(a.get("href", ""))
            title = a.get_text(" ", strip=True)
            p = li.select_one(".b_caption p")
            snip = p.get_text(" ", strip=True) if p else ""
            results.append({"title": title, "url": url, "snippet": snip})
    except Exception:
        pass
    return results


def _bing_real_url(ck_url: str) -> str:
    """Decode Bing redirect (ck/a?u=a1:...) ko asli destination URL me."""
    m = re.search(r"[?&]u=a1[:]?([^&]+)", ck_url)
    if m:
        try:
            encoded = m.group(1)
            if encoded.startswith("%"):
                return requests.utils.unquote(encoded)
            decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4))
            return decoded.decode("utf-8", "replace")
        except Exception:
            return ck_url
    return ck_url


def _looks_profile(url: str, site: str) -> bool:
    """path short ho (profile jaisa), forum/answers/posts drop karo."""
    path = url.split("?")[0].rstrip("/")
    segs = [s for s in path.split("/") if s]
    if not segs:
        return False
    if any(x in path for x in ("#", "search?", "/posts/", "/topic/", "answers.",
                               ".net/", "/m-p/", "/all/")):
        return False
    return True


def _site_lookup(site: str, name: str, n: int = 2) -> list:
    """Search one platform X name — DDG first, Bing fallback. Real links."""
    from bs4 import BeautifulSoup
    q = f'site:{site} "{name}"'
    try:
        r = _get("https://html.duckduckgo.com/html/?q=" +
                 requests.utils.quote(q), timeout=5.0)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            vals = []
            for a in soup.select(".result__a")[:n]:
                href = a.get("href", "")
                m = re.search(r"uddg=([^&]+)", href)
                url = requests.utils.unquote(m.group(1)) if m else href
                vals.append((a.get_text(" ", strip=True), url))
            if vals:
                return vals
    except Exception:
        pass
    try:
        r = requests.get(
            "https://www.bing.com/search?q=" + requests.utils.quote(q),
            headers={"User-Agent": _UA}, timeout=5.0)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            vals = []
            for li in soup.select("li.b_algo")[:n]:
                a = li.select_one("h2 a")
                if a:
                    vals.append((a.get_text(" ", strip=True),
                                 _bing_real_url(a.get("href", ""))))
            return vals
    except Exception:
        pass
    return []


def _known_social_host(host: str):
    """host ke liye social platform name return karo, warna None."""
    host = host.lower()
    for name, keys in (
            ("Instagram", ("instagram.com",)),
            ("LinkedIn", ("linkedin.com",)),
            ("Facebook", ("facebook.com", "fb.com")),
            ("X", ("x.com", "twitter.com")),
            ("YouTube", ("youtube.com", "youtu.be")),
            ("TikTok", ("tiktok.com",)),
            ("Reddit", ("reddit.com", "redd.it")),
            ("GitHub", ("github.com",)),
            ("Pinterest", ("pinterest.com",)),
            ("Medium", ("medium.com",)),
            ("Twitch", ("twitch.tv",)),
            ("Telegram", ("telegram.org", "t.me")),
            ("Snapchat", ("snapchat.com",)),
            ("Behance", ("behance.net",)),
            ("Flickr", ("flickr.com",))):
        if any(k in host for k in keys):
            return name
    return None


def cross_site_search(name: str, sites=None,
                      web_results=None) -> list:
    """Social profiles dhundho — multi-engine site: search + real web results
    me se social links extract. Sirf REAL links, no fake guess."""
    found = []
    seen = set()
    # 1) real web/search results se social links (sabse reliable)
    for w in (web_results or []):
        u = w.get("url") or ""
        host = u.split("//")[-1].split("/")[0].lower()
        plat = _known_social_host(host)
        if plat and u not in seen:
            seen.add(u)
            found.append({"site": plat, "url": u, "title": w.get("title", "")})
    # 2) platform-specific site: queries — PARALLEL, taaki fast ho
    if sites is None:
        sites = ["instagram.com", "linkedin.com", "x.com",
                 "github.com", "youtube.com"]
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _probe(site):
        hits = _site_lookup(site, name)
        out = []
        for title, url in hits:
            if not url or url in seen:
                continue
            host = url.split("//")[-1].split("/")[0].lower()
            if host != site and not host.endswith("." + site):
                continue
            if not _looks_profile(url, site):
                continue
            seen.add(url)
            out.append({"site": site.split(".")[0],
                        "url": url, "title": title})
        return out

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_probe, site) for site in sites]
        for f in as_completed(futs, timeout=20):
            try:
                found.extend(f.result())
            except Exception:
                pass
    # dedupe final
    uniq, seenu = [], set()
    for x in found:
        if x["url"] not in seenu:
            seenu.add(x["url"])
            uniq.append(x)
    return uniq


def news_search(name: str) -> list:
    """Google News RSS (free, no key) — real headlines about the name."""
    out = []
    try:
        r = requests.get(
            "https://news.google.com/rss/search?q=" +
            requests.utils.quote(name) +
            "&hl=en-IN&gl=IN&ceid=IN:en",
            headers={"User-Agent": _UA}, timeout=15.0)
        if r.status_code != 200:
            return out
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        for item in root.iter("item"):
            title = (item.findtext("title") or "")[:160]
            link = item.findtext("link")
            src = item.findtext("source") or ""
            if title and link:
                out.append({"title": title, "url": link, "source": src})
            if len(out) >= 5:
                break
    except Exception:
        pass
    return out


def reddit_search(name: str) -> list:
    """Reddit real data — try JSON API, else fallback site:reddit search."""
    out = []
    try:
        r = requests.get(
            "https://www.reddit.com/search.json?q=" +
            requests.utils.quote(name) + "&limit=8",
            headers={"User-Agent": "windows:ak-assistant:v1 (face-intel) "
                     "(contact: your_email@example.com)"},
            timeout=15.0)
        if r.status_code == 200:
            for post in r.json().get("data", {}).get("children", []):
                d = post.get("data") or {}
                title = (d.get("title") or "")[:160]
                permalink = d.get("permalink") or ""
                if title and permalink:
                    out.append({"title": title,
                                "url": "https://www.reddit.com" + permalink,
                                "sub": d.get("subreddit"),
                                "author": d.get("author"),
                                "score": d.get("score")})
    except Exception:
        pass
    if not out:  # API blocked → real reddit links via search engine
        hits = _site_lookup("reddit.com", name, n=8)
        for title, url in hits:
            if url:
                out.append({"title": title, "url": url,
                            "sub": "", "author": "", "score": None})
    return out


def github_detail(login: str) -> Optional[dict]:
    """Enrich one GitHub user with public profile fields."""
    try:
        r = _get("https://api.github.com/users/" + login)
        if r.status_code != 200:
            return None
        j = r.json()
        return {"login": j.get("login"), "name": j.get("name"),
                "bio": j.get("bio"), "url": j.get("html_url"),
                "followers": j.get("followers"),
                "repos": j.get("public_repos")}
    except Exception:
        return None


def wikipedia_lookup(name: str) -> Optional[dict]:
    """Wikipedia bio + thumbnail via public API."""
    try:
        q = requests.utils.quote(name)
        s = _get("https://en.wikipedia.org/w/api.php?action=query&list=search"
                 "&srsearch=" + q + "&format=json&srlimit=1")
        if s.status_code != 200:
            return None
        hits = s.json().get("query", {}).get("search", [])
        if not hits:
            return None
        page = hits[0]["title"]
        ext = _get("https://en.wikipedia.org/w/api.php?action=query&prop=extracts"
                   "|pageimages&exintro&explaintext&exsentences=4"
                   "&piprop=thumbnail&pithumbsize=320&format=json&titles=" +
                   requests.utils.quote(page))
        if ext.status_code != 200:
            return None
        pages = ext.json().get("query", {}).get("pages", {})
        if not pages:
            return None
        pg = next(iter(pages.values()))
        thumb = (pg.get("thumbnail") or {}).get("source")
        url = "https://en.wikipedia.org/wiki/" + requests.utils.quote(page.replace(" ", "_"))
        return {"title": page, "summary": (pg.get("extract") or "")[:1400],
                "image": thumb, "url": url}
    except Exception:
        return None


_ENTITY_PROP_NAMES = None


def _entity_label(entity_id: str) -> str:
    """Resolve one Wikidata entity ID → English label (cached)."""
    global _ENTITY_PROP_NAMES
    if _ENTITY_PROP_NAMES is None:
        _ENTITY_PROP_NAMES = {}
    if entity_id in _ENTITY_PROP_NAMES:
        return _ENTITY_PROP_NAMES[entity_id]
    try:
        r = _get("https://www.wikidata.org/w/api.php?action=wbgetentities"
                 "&ids=" + entity_id + "&props=labels&languages=en&format=json")
        if r.status_code == 200:
            data = r.json().get("entities", {}).get(entity_id, {})
            lbl = data.get("labels", {}).get("en", {}).get("value")
            if lbl:
                _ENTITY_PROP_NAMES[entity_id] = lbl
                return lbl
    except Exception:
        pass
    return entity_id


def wikidata_entity(name: str) -> Optional[dict]:
    """Wikidata structured person — REAL data (occupation, image, site, birth)."""
    try:
        q = requests.utils.quote(name)
        r = _get("https://www.wikidata.org/w/api.php?action=wbsearchentities"
                 "&search=" + q + "&language=en&limit=5&format=json")
        if r.status_code != 200:
            return None
        hits = r.json().get("search", [])
        person = None
        for h in hits:
            d = (h.get("description") or "").lower()
            if d and ("person" in d or "politician" in d or "actor" in d
                      or "singer" in d or "athlete" in d or "writer" in d
                      or "model" in d or "director" in d or "player" in d
                      or "minister" in d or "businessman" in d):
                person = h
                break
        if person is None and hits:
            person = hits[0]
        eid = person["id"]
        r2 = _get("https://www.wikidata.org/wiki/Special:EntityData/" + eid + ".json")
        if r2.status_code != 200:
            return None
        ent = (r2.json().get("entities", {}) or {}).get(eid, {})
        claims = ent.get("claims", {})
        # occupation labels
        occ = []
        for o in claims.get("P106", [])[:4]:
            try:
                oid = o["mainsnak"]["datavalue"]["value"]["id"]
                occ.append(_entity_label(oid))
            except Exception:
                continue
        # image
        img_file = None
        for p in claims.get("P18", [])[:1]:
            try:
                img_file = p["mainsnak"]["datavalue"]["value"]
            except Exception:
                pass
        # official website
        site = None
        for p in claims.get("P856", [])[:1]:
            try:
                site = p["mainsnak"]["datavalue"]["value"]
            except Exception:
                pass
        # birth date
        born = None
        for p in claims.get("P569", [])[:1]:
            try:
                t = p["mainsnak"]["datavalue"]["value"].get("time", "")
                born = t.lstrip("+")[:10]
            except Exception:
                pass
        image_url = None
        if img_file:
            image_url = ("https://commons.wikimedia.org/wiki/Special:FilePath/" +
                         requests.utils.quote(img_file.replace(" ", "_")) + "?width=320")
        return {
            "entity": eid,
            "title": ent.get("labels", {}).get("en", {}).get("value", name),
            "description": person.get("description"),
            "occupation": occ,
            "image": image_url,
            "website": site,
            "born": born,
            "url": "https://www.wikidata.org/wiki/" + eid,
        }
    except Exception:
        return None


def github_search(name: str) -> list:
    """GitHub public user search (unauthenticated, rate limited)."""
    out = []
    try:
        r = _get("https://api.github.com/search/users?q=" +
                 requests.utils.quote(name) + "&per_page=3")
        if r.status_code != 200:
            return out
        for u in r.json().get("items", []):
            out.append({"login": u.get("login"),
                        "url": u.get("html_url"),
                        "avatar": u.get("avatar_url")})
    except Exception:
        pass
    return out


def probe_profiles(name: str) -> list:
    """Check if candidate handles exist — REAL 404 semantics wale platforms.

    Empirically verified (time test): GitHub aur YouTube hi real 404 return
    karte hain. Twitch/Medium/Reddit/TikTok 200-for-anything dete hain
    (login-walled ya bot-wall) — unse fake hit nahi dikhayega.
    """
    handle = re.sub(r"[^a-z0-9_.]", "", (name or "").lower()).strip()
    if not handle:
        return []
    cands = [
        ("GitHub", f"https://github.com/{handle}"),
        ("YouTube", f"https://www.youtube.com/@{handle}"),
    ]
    found = []
    for site, url in cands:
        try:
            r = requests.get(url, headers={"User-Agent": _UA},
                             timeout=6.0, allow_redirects=False)
            code = r.status_code
        except Exception:
            code = None
        if code is not None and code < 400:
            found.append({"site": site, "url": url, "code": code})
    return found


# ── main pipeline ───────────────────────────────────────────────────────
def investigate(face_path: str, name: Optional[str], session_dir: str,
                progress: Optional[Callable[[str], None]] = None,
                data_dir: str = "", reverse_cooldown: float = 4.0,
                reverse_attempts: int = 2) -> dict:
    """Run the whole OSINT pass and write report.json.

    name=None → sirf reverse image search + manual browser buttons.
    name set  → full name-OSINT (wiki + web + github + profiles).

    reverse_cooldown / reverse_attempts limit how hard AK hits the free
    reverse-image engines so they don't IP-block us.
    """
    report = read_report(session_dir)
    report.update({"status": "searching", "face_path": face_path,
                   "name": name or None, "web": [], "wiki": None,
                   "wikidata": None, "github": [], "profiles": [],
                   "reverse": None, "sites": [], "news": [], "reddit": [],
                   "spoken_summary": None})

    def tell(msg: str):
        _log(report, msg)
        if progress:
            progress(msg)

    if name:
        tell(f"naam mila: {name} — online data dhundh raha hun")
    else:
        tell("chehra asli naami se match nahi hua — reverse search karta hun")

    # 1) reverse image search (multi-engine, throttled)
    try:
        report["reverse"] = reverse_image_search(
            face_path, report, cooldown=reverse_cooldown,
            attempts=reverse_attempts)
    except Exception as exc:
        report["reverse"] = {"error": str(exc)}
        _log(report, "reverse search fail")

    # 2) name-based OSINT — SAB SOURCES PARALLEL (fast!)
    if name:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        lookup = re.sub(r"[,\s]+", " ", name).strip()

        def task_web():
            return {"web": web_search(f'"{lookup}"')}

        def task_news():
            return {"news": news_search(lookup)}

        def task_reddit():
            return {"reddit": reddit_search(lookup)}

        def task_github():
            gh = github_search(lookup)
            with ThreadPoolExecutor(max_workers=4) as det:
                futs = [det.submit(github_detail, g.get("login", ""))
                        for g in gh]
                for f, g in zip(futs, gh):
                    d = f.result(15)
                    if d:
                        g.update(d)
            return {"github": gh}

        def task_profiles():
            return {"profiles": probe_profiles(lookup)}

        def task_wiki():
            return {"wiki": wikipedia_lookup(lookup)}

        def task_wikidata():
            return {"wikidata": wikidata_entity(lookup)}

        jobs = {
            "web": task_web, "news": task_news, "reddit": task_reddit,
            "github": task_github, "profiles": task_profiles,
            "wiki": task_wiki, "wikidata": task_wikidata,
        }
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(fn): key for key, fn in jobs.items()}
            for f in as_completed(futs, timeout=60):
                key = futs[f]
                try:
                    report.update(f.result())
                    if key == "web":
                        _log(report, f"{len(report['web'])} web results mile")
                    elif key == "news":
                        _log(report, f"{len(report['news'])} news headline mile")
                    elif key == "reddit":
                        _log(report, f"{len(report['reddit'])} reddit post mile")
                    elif key == "github":
                        _log(report, f"{len(report['github'])} github users mile (profiles with detail)")
                    elif key == "profiles":
                        _log(report, f"{len(report['profiles'])} profile(s) mili")
                    elif key == "wiki" and report.get("wiki"):
                        _log(report, "wikipedia bio mili")
                    elif key == "wikidata" and report.get("wikidata"):
                        _log(report, "wikidata structured data mila (occupation/image)")
                except Exception:
                    pass

        # sites — web results milne ke baad (sirf extraction + quick site: queries)
        try:
            report["sites"] = cross_site_search(lookup, web_results=report["web"])
            _log(report, f"{len(report['sites'])} social/web profile links mile")
        except Exception:
            pass

    report["spoken_summary"] = build_spoken_summary(report)
    report["status"] = "done"
    report["finished"] = time.strftime("%H:%M:%S")
    _log(report, "kaam khatam")
    _write_report(session_dir, report)
    return report


def _read_config() -> dict:
    """Read face_intel throttle options from config.json (defaults safe)."""
    try:
        from .config import load_config
        return (load_config().get("face_intel", {}) or {})
    except Exception:
        return {}


def run_session(session_dir: str, data_dir: str = "",
                progress: Optional[Callable[[str], None]] = None) -> dict:
    """capture_face + investigate — full pipeline, live report wala."""
    fi = _read_config()
    return run_session_cfg(session_dir, data_dir=data_dir, progress=progress,
                           reverse_cooldown=float(fi.get("reverse_cooldown", 4.0)),
                           reverse_attempts=int(fi.get("reverse_attempts", 2)))


def run_session_cfg(session_dir: str, data_dir: str = "",
                    progress: Optional[Callable[[str], None]] = None,
                    reverse_cooldown: float = 4.0,
                    reverse_attempts: int = 2) -> dict:
    """capture_face + investigate — full pipeline, live report wala."""
    os.makedirs(session_dir, exist_ok=True)
    report = read_report(session_dir)
    report.update({"status": "capturing", "steps": []})
    _write_report(session_dir, report)

    cap = capture_face(session_dir, data_dir=data_dir)
    if not cap["found"]:
        report.update({"status": "error", "error": "chehra nahi mila",
                       "spoken_summary": None})
        _write_report(session_dir, report)
        return report
    report["face_name"] = cap["name"]
    report["face_conf"] = cap["conf"]
    report["face_path"] = cap["face_path"]
    report["frame_path"] = cap["frame_path"]
    _write_report(session_dir, report)
    try:
        if progress:
            progress("chehra mil gaya")
    except Exception:
        pass
    return investigate(cap["face_path"], cap["name"], session_dir,
                       progress=progress, data_dir=data_dir,
                       reverse_cooldown=reverse_cooldown,
                       reverse_attempts=reverse_attempts)


# ── spoken summary (Hinglish, for AK) ───────────────────────────────────
def build_spoken_summary(report: dict) -> str:
    """Sirf cheez baat karo — 'info fetch hui, window me dekh lo'.
    Source counts / sab detail GUI me hai, speech me nahi dhoot. """
    name = report.get("name")
    wiki = report.get("wiki") or {}
    wd = report.get("wikidata") or {}
    parts = []

    if name:
        parts.append(f"Yeh chehra {name} ka lagega.")
        # 1-2 line hi — baqi sab window me hai
        if wiki.get("summary"):
            s = wiki["summary"].replace("\n", " ")
            parts.append(s[:400])
        elif wd.get("description"):
            parts.append(wd["description"])
    else:
        parts.append("Is chehre ko kisi registered identity se match nahi "
                     "kar paya.")

    # faltu counts/methods bilkul nahi — bas ek halka sa bandwidth
    if name:
        parts.append("Uski online info fetch kar li hai — profile window me "
                     "saari details aur photos dekh sakte ho.")
    else:
        parts.append("Chehre se reverse search link mila hai — use window me "
                     "open karke details dekho.")
    res = " ".join(parts)
    return res or "Kuch public information nahi mili is chehre ke baare me."


def save_markdown_report(session_dir: str) -> str:
    """Human-readable report (also saves .txt next to report.json)."""
    r = read_report(session_dir)
    name = r.get("name") or "Unknown"
    md = [f"# Face Intel — {name}", ""]
    md.append(f"Status: {r.get('status')}")
    md.append(f"Updated: {r.get('updated')}")
    md.append("")
    if r.get("wiki"):
        md.append("## Wikipedia")
        md.append(wikihdr(r["wiki"]))
        md.append("")
    if r.get("wikidata"):
        wd = r["wikidata"]
        md.append("## Wikidata (structured)")
        md.append(f"- [{wd.get('title')}]({wd.get('url')}) — {wd.get('description')}")
        if wd.get("occupation"):
            md.append(f"- Occupation: {', '.join(wd['occupation'])}")
        if wd.get("born"):
            md.append(f"- Born: {wd['born']}")
        if wd.get("website"):
            md.append(f"- Official: {wd['website']}")
        if wd.get("image"):
            md.append(f"- Image: {wd['image']}")
        md.append("")
    if r.get("web"):
        md.append("## Web results")
        for w in r["web"]:
            md.append(f"- [{w.get('title')}]({w.get('url')})")
        md.append("")
    if r.get("github"):
        md.append("## GitHub")
        for g in r["github"]:
            extra = ""
            if g.get("name"):
                extra = f" ({g.get('name')})"
            if g.get("bio"):
                extra += f" — {g['bio'][:120]}"
            md.append(f"- {g.get('login')}{extra}: {g.get('url')}")
        md.append("")
    if r.get("sites"):
        md.append("## Social / Web profiles (site: search)")
        for s in r["sites"]:
            md.append(f"- [{s.get('site')}] {s.get('title')}: {s.get('url')}")
        md.append("")
    if r.get("news"):
        md.append("## News")
        for n in r["news"]:
            md.append(f"- ({n.get('source')}) {n.get('title')}: {n.get('url')}")
        md.append("")
    if r.get("reddit"):
        md.append("## Reddit")
        for x in r["reddit"]:
            md.append(f"- r/{x.get('sub')} ({x.get('score')}) {x.get('title')}: "
                      f"{x.get('url')}")
        md.append("")
    if r.get("profiles"):
        md.append("## Profiles (handle probe)")
        for p in r["profiles"]:
            md.append(f"- {p.get('site')}: {p.get('url')} (HTTP {p.get('code')})")
        md.append("")
    rev = r.get("reverse") or {}
    best = rev.get("best")
    if best:
        md.append("## Reverse image search")
        md.append(best)
        md.append("")
    md.append("## Log")
    for s in r.get("steps", []):
        md.append(f"- {s}")
    txt = "\n".join(md)
    out = os.path.join(session_dir, "report.md")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(txt)
    except Exception:
        pass
    return out


def wikihdr(w: dict) -> str:
    s = w.get("summary", "")
    return f"[{w.get('title')}]({w.get('url')}) — {s[:400]}"


# ── CLI (standalone) ────────────────────────────────────────────────────
def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="AK Face Intel (CLI)")
    ap.add_argument("--session", required=True, help="session dir")
    ap.add_argument("--data-dir", default="", help="data_path (for face match)")
    ap.add_argument("--image", default=None, help="skip camera, use saved face")
    ap.add_argument("--name", default=None, help="person name (optional)")
    a = ap.parse_args(argv)

    def progress(msg: str):
        print("[face-intel]", msg)

    os.makedirs(a.session, exist_ok=True)
    if a.image:
        rep = investigate(a.image, a.name or None, a.session,
                          progress=progress, data_dir=a.data_dir)
    else:
        rep = run_session(a.session, data_dir=a.data_dir, progress=progress)
    print("STATUS:", rep.get("status"))
    print("DONE:", save_markdown_report(a.session))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())