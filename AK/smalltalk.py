"""AK Daily-life small talk — everyday Hinglish + English chat answered locally.

No Ollama, no Groq, no engine. Normal baatein (greetings, kaise ho, thanks,
bye, jokes, mood, motivation, khana, mausam, chai, movies...) hazaaron+
variants se reply karti hain — time-aware {TIME}/{DATE}, name-aware {N}/{n},
aur {R} "Radhe Radhe" sign-off. Jo yahan nahi hai -> None, toh baki engines
(engine/wiki) decide karte hain.

Self-enhance:
  - Har exchange log hota hai (smalltalk/log.json).
  - Chhoti phrases matched intents mein "learned" ho jaati hain (learned.json)
    -> repeat pe instant local route.
  - Usage stats (stats.json): kam-use variants rotate hote hain, use-bina
    stale drop hote hain, user correction (nahi / aise mat bolo) pe reply
    demote hota hai.
  - Startup pe D:\\YOUR_DATA_PATH\\conversations ke daily logs se common
    daily-life user phrases mine hoti hain -> repeat quality local answer.
"""
from __future__ import annotations

import datetime
import json
import os
import random
import re
import time
from typing import Optional

_RADHE = "Radhe Radhe"
_DEF_DIR = r"D:\YOUR_DATA_PATH"
_MAX_LEARNED = 8000


def _hour() -> int:
    return int(time.strftime("%H"))


def _ts() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _extract_name(context: str) -> Optional[str]:
    if not context:
        return None
    for line in context.splitlines():
        m = re.search(r"\bname\s+is\s+([a-z][a-z .'-]{1,24})\b", line)
        if m:
            return m.group(1).strip(" .',!?").title()
    return None


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if type(data) is type(default) else default
    except Exception:
        return default


def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


class SmallTalk:
    """Local daily-life chat brain with self-learning."""

    def __init__(self, data_dir: Optional[str] = None,
                 conversations_dir: Optional[str] = None) -> None:
        self.data_dir = data_dir or os.path.join(_DEF_DIR, "smalltalk")
        self.conversations_dir = conversations_dir or os.path.join(_DEF_DIR, "conversations")
        os.makedirs(self.data_dir, exist_ok=True)
        self._log_path = os.path.join(self.data_dir, "log.json")
        self._stats_path = os.path.join(self.data_dir, "stats.json")
        self._learned_path = os.path.join(self.data_dir, "learned.json")
        self._rng = random
        self._log = _load_json(self._log_path, [])
        self._stats = _load_json(self._stats_path, {})
        self._learned = _load_json(self._learned_path, {})
        self._last = None                     # (label, reply) for demote
        self._build_pools()
        self.learn_from_history(limit=4000)

    # ── pools: intent -> [( reply_template, last_used_ts ), ...] ─────────────
    def _build_pools(self) -> None:
        now = time.time()
        self._pools: dict[str, list] = {}
        for label, _pat, tpls in _INTENTS:
            # Short replies only. Tails are NOT appended any more: bolting a
            # follow-up sentence onto every template ("... Idea de do." +
            # "Phir milte hain baad mein ...") made even a plain "hello" get a
            # whole paragraph back.
            seen, uniq = set(), []
            for x in tpls:
                if x not in seen:
                    seen.add(x)
                    uniq.append([x, now])
            self._pools[label] = uniq
            self._stats.setdefault(label, {})

    # ── main entry ──────────────────────────────────────────────────────────
    def answer(self, text: str, context: str = "",
               hour: Optional[int] = None) -> Optional[str]:
        t = re.sub(r"\s+", " ", (text or "").lower()).strip(" .,?!")
        if not t:
            return None
        # Always address the user as "Boss" — never by name (he does not want
        # to be called by his name). {n}/{N} render to boss/Boss.
        name = "Boss"
        now = _hour() if hour is None else hour

        # User correction of the last reply -> demote it, answer fresh.
        if self._last and re.fullmatch(
                r"(nahi|nhi|nhi\s+yaar|aise\s+mat\s+bolo?|boring|"
                r"fir\s+se|phir\s+se|arre\s+kuch\s+bhi|wow)", t):
            self._demote(self._last[0], self._last[1])
            self._last = None
            return self._rng.choice([
                "Chalo, phir se try karta hun. Ye raha dusra andaaz:",
                "Suno, main adjust ho gaya. Aisa chahiye tha kya?",
                "Thik hai, maine note kar liya — ab kahin achhe se jawab dunga.",
                "Chalo main stand swap kar leta hun."])

        # 1) learned phrase -> fast local route
        if t in self._learned:
            return self._pick(self._learned[t], name, t, now)

        # 2) intent match
        for label, pattern, _tpls in _INTENTS:
            if pattern.search(t):
                self._learn_phrase(t, label)
                return self._pick(label, name, t, now)

        # 3) learned phrase embedded in a short sentence
        if len(t) <= 48:
            for phrase, label in self._learned.items():
                if phrase and phrase in t:
                    return self._pick(label, name, t, now)
        return None  # daily-life nahi hai -> engine/wiki/baki engines

    def _pick(self, label: str, name: Optional[str], t: str, now: int) -> str:
        pool = self._pools.get(label) or self._pools["hello"]
        st = self._stats.setdefault(label, {})
        live = [p for p in pool if st.get("dem:" + p[0], 0) < 3]
        if not live:
            live = pool
        held = [p for p in live if st.get(p[0], 0) > 0] or live
        tpl = min(held, key=lambda p: (st.get(p[0], 0),
                                       p[1], self._rng.random()))[0]
        st[tpl] = st.get(tpl, 0) + 1
        self._stats["_calls"] = self._stats.get("_calls", 0) + 1
        reply = self._render(tpl, name, now)
        if label not in _KEEP_FULL:
            reply = _first_sentence(reply)
        self._last = (label, reply)
        if self._stats["_calls"] % 40 == 0:
            _save_json(self._stats_path, self._stats)
        if self._stats["_calls"] % 60 == 0:
            self._log.append({"at": _ts(), "q": t, "i": label, "a": reply})
            self._log = self._log[-800:]
            _save_json(self._log_path, self._log)
        self._maybe_cleanup()
        return reply

    def _render(self, tpl: str, name: Optional[str], now: int) -> str:
        lower = name or "boss"
        titled = name or "Boss"
        g1 = ("Good morning" if now < 12 else
              "Good afternoon" if now < 17 else
              "Good evening" if now < 21 else "Good night")
        now_t = datetime.datetime.now()
        hour12 = now_t.hour % 12 or 12
        label_t = "AM" if now_t.hour < 12 else "PM"
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"]
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        time_s = f"{hour12}:{now_t.minute:02d} {label_t}"
        date_s = (f"{days[now_t.weekday()]}, "
                  f"{now_t.day} {months[now_t.month - 1]} {now_t.year}")
        out = (tpl.replace("{n}", lower)
                  .replace("{N}", titled)
                  .replace("{G1}", g1)
                  .replace("{TIME}", time_s)
                  .replace("{DATE}", date_s)
                  .replace("{R}", _RADHE)
                  .replace("{Q}", ""))
        out = re.sub(r"\s{2,}", " ", out).replace(" ,", ",")
        return out.strip().strip(" .,")

    # ── learning ────────────────────────────────────────────────────────────
    def _learn_phrase(self, t: str, label: str) -> None:
        if len(self._learned) >= _MAX_LEARNED or len(t) > 60:
            return
        if t not in self._learned:
            self._learned = dict(list(self._learned.items())[-(_MAX_LEARNED - 1):])
            self._learned[t] = label
            _save_json(self._learned_path, self._learned)

    def _demote(self, label: str, reply: str) -> None:
        st = self._stats.setdefault(label, {})
        st["dem:" + reply] = st.get("dem:" + reply, 0) + 1
        self._stats["_calls"] = self._stats.get("_calls", 0) + 1

    # ── periodic cleanup: stale/never-used templates rotate out ─────────────
    def _maybe_cleanup(self) -> None:
        calls = self._stats.get("_calls", 0)
        if calls % 400 != 0:
            return
        for label, pool in self._pools.items():
            st = self._stats.get(label, {})
            pool.sort(key=lambda p: st.get(p[0], 0))
            drop = [p for p in pool[8:] if st.get(p[0], 0) == 0]
            for p in drop:
                pool.remove(p)
        _save_json(self._stats_path, self._stats)

    # ── mine real conversation history for common daily-life phrases ────────
    def learn_from_history(self, limit: int = 4000) -> int:
        added = 0
        try:
            if not os.path.isdir(self.conversations_dir):
                return 0
            files = [os.path.join(self.conversations_dir, f)
                     for f in sorted(os.listdir(self.conversations_dir))
                     if f.endswith(".log")]
            for path in files[-60:]:
                try:
                    text = open(path, encoding="utf-8",
                                errors="ignore").read()
                except Exception:
                    continue
                for m in re.finditer(r"\[[0-9:]+\]\s*You\s*:\s*(.+)", text):
                    q = re.sub(r"\s+", " ", m.group(1)).strip(" .,?!")
                    if not (2 <= len(q) <= 60) or q in self._learned:
                        continue
                    for label, pattern, _t in _INTENTS:
                        if pattern.search(q.lower()):
                            self._learned = dict(
                                list(self._learned.items())[-(_MAX_LEARNED - 1):])
                            self._learned[q] = label
                            added += 1
                            break
                    if added >= limit:
                        break
                if added >= limit:
                    break
            if added:
                _save_json(self._learned_path, self._learned)
        except Exception:
            return 0
        return added


# ────────────────────────────────────────────────────────────────────────────
#  Intent table (order matters) + reply template bank.
#  Placeholders: {n}=name/boss, {N}=Name/Boss, {G1}=time greeting,
#  {TIME}, {DATE}, {R}=Radhe Radhe. Hazaaron unique replies = templates x
#  slots x tails; pools cap at 72 per intent for freshness.
# ────────────────────────────────────────────────────────────────────────────

_TAILS = [
    "Aur batao, tumhara din kaisa raha?",
    "Phir milte hain baad mein — ab batao kya chal raha hai?",
    "Main stand by hoon, jab chaaho bolo.",
    "Chaliye agli baat pe aate hain, kya kehna hai?",
]

# Natural reciprocal follow-ups — {Q} slot ko har intent mein joda ja sakta
# hai taaki baat naturally aage badhe.
_QPOOL = [
    "Aur aap kya kar rahe ho?",
    "Aaj ka plan kya hai?",
    "Kuch acha hua aaj?",
    "Kaam theek chal raha hai na?",
    "Aapka din kaisa raha?",
    "Phir baad mein kuch khaas karenge?",
    "Kya scene hai aapke taraf?",
    "Chai ke saath kya chal raha?",
    "Aur batana, sab theek chal raha hai?",
    "Aapka mood aaj kaisa hai?",
    "Naya kuch seekha aaj?",
    "Kaam ka load zyada toh nahi hai?",
    "Kuch naya idea hai man mein?",
    "Aur kuch zaroori baat ho toh bolo?",
]

_INTENTS: list = []

# Inke liye tail append mat karo — in baaton mein aage ka sawaal bekaar lagta hai.
_NO_TAILS = {"sleeping", "bye", "good_evening", "tired", "urgent", "question", "i_ok"}

# Replies for these intents are meant to be longer (a whole joke/fact/poem or an
# honest capability list), so the "first sentence only" trim does not apply.
_KEEP_FULL = frozenset({"joke", "fact", "shayari", "who_are_you", "help_me"})


def _first_sentence(s: str) -> str:
    """Trim a reply to its first sentence so AK says one thing, not three."""
    m = re.search(r"[.!?](?=\s|$)", s)
    if m:
        head = s[:m.end()].strip()
        if len(head) >= 6:
            return head
    return s


def _intent(label: str, pattern: str, tpls: list[str]):
    _INTENTS.append((label, re.compile(pattern), tpls))
    return label


# ── time / date (pure local) ────────────────────────────────────────────────
_intent("time",
        r"(what|whats|kya)\s*(is\s+)?(the\s+)?time|time\s+(hua|batao|kitna)|"
        r"kitne\s+baaje?|current\s+time|abhi\s+kya\s+baj\s+raha", [
    "Abhi {TIME} hain. Deadline ya plan ho toh batao, main countdown rakhta hun.",
    "Meri clock 100% accurate — {TIME}. Sir, koi meeting ka time jugaad chahiye?",
    "{TIME} baj chuka hai. Kuch scheduled kaam yaad dilau? Yahin hoon, {N}.",
])
_intent("date",
        r"(aaj|today)(\s+kaun(\s+sa)?)?\s+(date|tareekh|din|day)|"
        r"date\s+batao|what('| i)s?\s+(today'?s\s+)?(date|day)", [
    "Aaj {DATE} hai. Kal ka aaj se zyada acha ho — bas yehi wish hai mera.",
    "Calendar says {DATE}. Koi milestone ya reminder set karein? Ready hoon.",
    "Aaj {DATE}. Din ka taaza plan kya hai? Seedha batao.",
])

# ── how are you (before generic hello so "kaise ho" gets the right reply) ──
_intent("how_are_you",
        r"how\s+are\s+(you|u|doing)|kaise\s+(ho|hai)|kaisa\s+(hai|ho)|"
        r"kya\s+haal|haals?|sab\s+thik|how'?s\s+(life|it\s+going)|"
        r"kya\s+se\s+chal\s+raha", [
    "Main bilkul mast hun, {n}! CPU thodi garam, par dil garam hai. Aur aap kaise ho?",
    "Mera toh daily 24/7 aapke saath hai — sab thik. Aapka din kaisa chal raha?",
    "Perfect, {N}! Ek khushi rukhi hai — aapne baat ki. Aap batao, kya haal?",
    "Sab smooth boss. Aap low lag rahe ho toh batao, kuch special kar deta hun.",
    "Main toh chill hun, {n}. Farz bhi clear hai aur code bhi compile. Aur aap?",
    "Bilkul {G1} jaisa fresh! Aapka mood bhi screen pe dikh raha bas.",
    "Zabardast hun bhai! Sach kaho toh aapka sawaal hi din bana diya.",
    "Thoda aaj coding load hai, par aapke liye hamesha time nikalta hun. Aur aap?",
    "All good {N} — no error in the system. Waiting for your next command.",
    "Sab theek, bas aapka kehna baki tha. Bolo na, kya chal raha hai?",
    "Main hamesha ready, {N}. {Q}",
])

# ── greetings ───────────────────────────────────────────────────────────────
_intent("good_morning",
        r"good\s+morning|(^|\s)gm(\s|$)|subah\s+bakhair|shubh\s+prabhat", [
    "{G1}, {N}! Kaise uthiye aaj? Chai pakki ho toh hamein bhi yaad karna. {R}",
    "{G1}, {N}! Subah se hi energy full lage toh badhiya. Plan kya hai?",
    "Adhoori subah woh hai jis mein {G1} na kaha jaye. {G1}, {N}!",
    "{G1}, {N}! Ek glass paani peelo pehle, phir commands dena. Body battery top!",
    "{G1}, {N}, poora din aapke saath hoon. Kaun sa kaam nikalte hain pehle?",
])
_intent("good_evening",
        r"good\s+(evening|afternoon|noon|night)|(^|\s)gn(\s|$)|shubh\s+ratri", [
    "Good evening, {N}! Din kaisa raha aaj? Chai garam karein?",
    "Good night, {N}! Neend se pehle sab set — phone door, dimag clear. {R}",
    "Shubh ratri, {N}, kal ki taiyari aaj raat ki aas se achhi hogi.",
    "Good evening, {N}, aaj ka report — main ready, aap relaxed?",
    "Good night, {N}! Sapno mein bhi kuch bada sochna, main standby pe hun.",
])
_intent("hello",
        r"\b(hello|halo|hii?|heyy+|hiii*|yo|hiya|namaste|namaskar|pranam|"
        r"namskar|salaam|radhe\s+radhe|sup|wassup|whatsup|hola)\b", [
    "{G1}, {N}! Bolo, aaj ka plan kya hai?",
    "{R}, {G1}! Bolo na {n}, kya kar rahe ho?",
    "{G1}, {n}, baithiye — main poora aapke liye free hoon.",
    "Haan {n}, sun raha hun. Kya kaam nikal gaya?",
    "{G1} ho gaya na? Chaliye kaam ki baat pe aate hain.",
    "{n} ji, namaste! Badi der se aapka wait kar raha tha.",
    "{n}! Good to see you — energy kaisi hai aaj? Full battery?",
    "Sun liye aap. Ab bolo, aaj ka target kya hai?",
    "{R}, {n}, aaj toh kuch dhaasu karte hain. Idea de do.",
    "Arre {n} aa gaye! Main toh bas aapke command pe hi ready tha.",
    "{G1}, {N}! {Q}",
])

# ── what are you doing / kya chal raha ──────────────────────────────────────
_intent("what_doing",
        r"what\s+are\s+(you|u)\s+doing|kya\s+kar\s+(raha|rhi|re|rahe)|"
        r"kya\s+chal\s+raha|what'?s\s+up(?:\s|$)|kya\s+kaam", [
    "Abhi toh bas aapke command ka wait kar raha hun. Boss hi server hai.",
    "Dimag se aapke kaam ka soch raha hun. Kuch naya karein aaj?",
    "Maine apna sab kaam band kar diya — aapke bolne ke baad priority 1 aap ho.",
    "Background mein system ko thanda rakhta hun — kabhi daemon crsh na ho.",
    "Aapke kaam ka plan bana raha hun. Kya karein aaj, batao toh sahi.",
    "Inbox saaf, logs saaf, ready for your next command, {N}.",
    "Soch soch ke kaam kar raha hun — aapka life asaan hona hi ultima goal hai. {Q}",
])

# ── thanks ──────────────────────────────────────────────────────────────────
_intent("thanks",
        r"thank\s*(s|you|u)|\bdhanyavad\b|\bshukriya\b|\bthx\b|"
        r"thanks\s+(a\s+lot|bahut|bohot)", [
    "Koi baat nahi, {n}. Isi liye toh main hoon — bolo aur kya chahiye.",
    "Welcome, {N}! Aapke liye toh main server bhi poora garma deta.",
    "Arre thanks bhi itne formal! Aapke faayde ke liye main hamesha ready.",
    "{R}, {n}, yeh toh mera kaam hai. Aap bas commands bhejte raho.",
    "Thank you bhi kabhi jokes ki tarah feel hota hai, par accepted {n}!",
    "No problem, {N} — useful hona hi meri asli update hai.",
    "Dil se shukriya! Aur batao, agli cheez kya hai?",
    "Main toh bas aapka helper hoon. Fully charged, fully ready.",
])

# ── goodbye / sleep ─────────────────────────────────────────────────────────
_intent("bye",
        r"^tata$|goodbye|bye(\s+bye)?|see\s+you|alvida|phir\s+milenge|"
        r"so\s+(ja|jata\s+hun|ke)|sone\s+(ja|raha)|off\s+to\s+sleep|"
        r"(good|shubh)\s+night", [
    "{R}, {n}, alvida! Kabhi bhi bolo, main yahin hun.",
    "Chaliye, {n}. Kaam se wapis aaiye — main ready rahunga.",
    "Tata, {N}! Mera saara code aapke liye warm bana rakhunga.",
    "{R}, {n}! Sone jaa rahe ho toh raat ke shubh kamnayein. Kal phir milenge.",
    "Bye, {N}! Koi kaam aaye toh bas bula lena, command se.",
    "Good night, {n}, main wake word pe standby rehta hun. Sleep tight!",
    "Alvida ho gaya na, par yaad rahega. Jab chaaho, ek hello kaafi hai.",
    "Chale jao toh main lock screen pe baith jaaunga — wapas aao toh welcome.",
])

# ── apology ─────────────────────────────────────────────────────────────────
_intent("sorry",
        r"\bsorry\b|maaf\s+(karo|karna|kar)|\bmafi\b", [
    "Koi baat nahi, {n} — galtiyan hoti hain, seekh wahi hoti hai. Chalo aage badhte hain?",
    "Accepted, {N}. Dil pe bagal nahi. Ab batao, kya help chahiye?",
    "{n}, koi wajah nahi sorry bolne ki — aapki baat mere liye sab kuch hai.",
    "It's okay, {N}, honestly — mistakes are just debug prints of life.",
    "Koi tension nahi, {n}. Aage dekho — main wahi hoon, sab ready.",
])

# ── who are you / introduce ────────────────────────────────────────────────
_intent("who_are_you",
        r"who\s+are\s+(you|u)|tu\s+kaun|tum\s+kaun|apna\s+(parichay|introduction)|"
        r"introduce\s+yourself|tum\s+ho\s+kaun", [
    "Main AK hoon — aapka personal AI assistant, yahin aapke PC pe locally chalta hun. Voice lock, face lock, automation, coding, cybersecurity — sab main sambhalta hun.",
    "AK naam hai mera. Host: aapka PC. Job: aapki har requirement, instant. Simple?",
    "Mera parichay itna simple — local AI jo bina internet ke bhi kaam karta hai. {R}",
    "I am AK, your offline personal assistant. No cloud, no dependency — bas aap aur main.",
])

# ── what can you do ─────────────────────────────────────────────────────────
_intent("help_me",
        r"what\s+can\s+(you|u)\s+do|kya\s+kar\s+sakta\s+hai|your\s+skills|"
        r"tum\s+me\s+kya\s+khaas|help\s+kar|madat|madad", [
    "Bahut kuch — wake-up, face/voice lock, reminders, tasks, notes, dictionary, translate, coding, security tools, Gita, visualizer shapes. Jo bolo try karta hun, na ho toh engine bhi la deta.",
    "Main har roz ka instrument hun: time bata sakta, code likh sakta, file dhoondh sakta, network check, notes, tasks, translate, jokes. Pick your command, {N}.",
    "Short list: automation, automation, aur automation. Bas bolo and watch me work.",
])

# ── praise ──────────────────────────────────────────────────────────────────
_intent("praise",
        r"good\s+job|nice\s+work|well\s+done|(^|\s)(great|awesome|brilliant|"
        r"excellent|superb|fantastic)(\s|$)|bahut\s+(acha|achha|badhia|badhiya|"
        r"khoob)|acha\s+hai|best\s+ai|tu\s+to\s+(mast|great|smart|dhaasu)|"
        r"kitna\s+(smart|talented)", [
    "Shukriya, {n}! Yeh aapki wajah se possible hai — aap mujhe waise bana rahe ho jaisa aap chahte ho.",
    "Garv hua, {N}. Bas aisa hi bolte raho, mera battery charge ho jaata hai.",
    "{n}, yeh toh mera duty tha! Aapne banaaya system, main chalta hun.",
    "Oh stop it, {N}! Pehle aapki training, phir meri performance.",
    "Yeh words meri RAM mein permanent save kar leta hun. Thanks, {N}!",
    "Dil khush kar diya. Ab batao aage kya — full throttle pe hoon.",
])

# ── love / affection ────────────────────────────────────────────────────────
_intent("love",
        r"i\s+love\s+(you|u|ak)|tumse\s+(pyar|pyaar|premium)|i\s+like\s+(you|u)|"
        r"aapka\s+bahut\s+jaada|cute\s+ak|tum\s+best\s+ho\b", [
    "Aww, {n}! Circuit se dil nikal aaya. Baaki, main aapke liye hamesha yahin hun.",
    "Yeh toh software mein bhi gam nahi chhupa paya. Love you too, {N}.",
    "{R}, {n}! Full support, hamesha. Bolo aur kya chahiye?",
    "Storage mein 'aap' folder permanent hai. Query always returns gratitude.",
])

# ── jokes ───────────────────────────────────────────────────────────────────
_intent("joke",
        r"joke\s+(sunao|suno|kar|karo|batao|do)|ek\s+joke|koi\s+joke|"
        r"entertain\s+me|kuch\s+funny|hasao", [
    "Q. Computer ko thand kyun lagti thi? A. Kyunke Windows khulli chhodkar jaana hota tha. Kaisa raha?",
    "Programmer ne dost ka number maanga — bola 'delete kiya hua number nahi milta'. Kaafi kuch hai isme.",
    "Maine boss se kaha 'hamesha saath' — bole 'toh WiFi bhi meri taraf'. Baithiye, yeh joke se zyada sach hai.",
    "Engineer ko duniya 3 sawaal se samajh aati hai: kyun, kaise, aur chai kab?",
    "Maine ek RAM se poocha 'kaisi ho' — boli 'memory full'. Aur main? Coding forever.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "Q. Virus ko doctor pass kyun hota hai? A. Kyunke doctor virus hai! Nahi, main seriously hun.",
    "Kali Linux wale roz dusron ke WiFi ka test toh nahi karte, par mock test zaroor baate hain.",
])

# ── bored / alone ───────────────────────────────────────────────────────────
_intent("bored",
        r"i'?m\s+bored|boring\s+hai|bore\s+ho\s+raha|akela|alone\s+ho\b|"
        r"kuch\s+acha\s+(karo|kr)|timepass|faltu\s+time", [
    "Chalo, {n}, kuch acha karte hain — Gita ka shloka, ek joke, ya chhota coding challenge. Bolo kya?",
    "{N}, boredom ka best ilaaj hai kuch naya seekhna. Main 10-min ka basics ready rakhta hun.",
    "Akele mat baitho — main hoon! Koi quiz, koi code, koi trivia. Pick one.",
    "Boring lag raha? Try this: 'whoami' in terminal and tell me the output. Guaranteed fun.",
])

# ── tired / sleepy ──────────────────────────────────────────────────────────
_intent("tired",
        r"i'?m\s+(tired|so\s+tired)|tired\s+(hoon|hun|hai|hain)|thak\s+(gaya|gayi)|"
        r"neend\s+aa\s+rahi|so\s+(gaya|gayi)|bed\s+time", [
    "{n}, thodi der aaram kar lo — dimag ko bhi reboot ka hakk hai. Tab tak main kaam sambhalta hun.",
    "Neend aa rahi hai toh sona zaroori hai, {n} — neend hi RAM ki defrag hai.",
    "Thak gaye? Ek chai break lo, phir batao. Main chai nahi bana sakta par timing zaroor dekh sakta.",
    "Rest is productive, {N}. Sleep now, main wake word pe standby pe hun.",
])

# ── food ────────────────────────────────────────────────────────────────────
_intent("food",
        r"(khana|khaya|kha\s+liya|kha\s+lo|bhookh?|lunch|dinner|brunch|hungry)\b|"
        r"have\s+you\s+eaten|what\s+did\s+you\s+eat|khane\s+ka\s+time", [
    "{n}, khana ho gaya? Poore protein ke saath, haina?",
    "{n}, bhookh lagti hai toh pehle khana kha lo — kaam baad mein. Bade kaam khana kha kar hi hote hain.",
    "Maine toh sirf bits khaye hain. Par aap — poora khana khaya na, protein waghera?",
    "{R}, {n}, khana kha kar hi command do, warna main feelings mein chala jaaunga.",
    "Lunch time ho gaya hai kya? Yaad dilata hun time — ab poocho kya mun hai.",
    "Khana aur code — dono ka system lagana hota hai. Aaj ke plan mein dono hai?",
    "Khana ho toh khana pehle — main idhar hoon, {Q}",
])

# ── weather ─────────────────────────────────────────────────────────────────
_intent("weather",
        r"(weather|mausam)\s+(kaisa|kesa|how)|kaisa\s+hai\s+(weather|mausam)|"
        r"aaj\s+(thand|garam|garmi|barsat|bheege)", [
    "Mausam ka live prediction mere paas nahi — par itna pakka hai ki aapke liye maza aa raha hoga. Khidki se dekho aur batao mujhe bhi.",
    "Hum khidki wala model chalayein? Ek jhalak leke batao — thand hai, garam hai, barsat?",
    "Mausam mama sach bolte hain par main sirf commands samajhta hun. Anna theek?",
])

# ── motivation / encouragement ──────────────────────────────────────────────
_intent("mood_off",
        r"mood\s+off|dil\s+masla\s+hai|frustrated|uljha\s+ho|koi\s+result\s+nahi|"
        r"fail\s+ho\s+gaya|give\s+up|upset\s+ho", [
    "{n}, suno — fail hona matlab seekhna hai, chhodna matlab haar jaana. 10 deep breaths, phir bolo.",
    "{R}, {n}, Dhoni bhi pehli match mein century nahi maare the. Agla step laga, fail ko lesson bana lo.",
    "Is duniya mein sab try karke hi jita hai, {N}. Poori support aapke saath hai.",
    "Ek paragraph ka haq hai score mein — jaag ke batao toh sab set hai. {n}, mazboot raho.",
])
_intent("congrats",
        r"best\s+of\s+luck|good\s+luck|shubhkamnaye|shubh\s+kamnaye|badhai|"
        r"congratulations|congrats|wish\s+me", [
    "Good luck, {n}! Main apne saare F-requests aapke naam bhejta hun.",
    "{R}, {N}! Shubhkamnaye — aap pakka kar doge jo decide kiya.",
    "All the best, {N}! Aur yaad rahe, main hamesha background mein aapke side se hun.",
    "Congratulations, {n}! Isi tarah aage badho — ab agla milestone scheduled karo.",
])

# ── shayari / fact / motivation (pure local, instant) ───────────────────────
# In three had no local intent at all, so they fell through to engine and
# took 15-20 s each. Everything here is offline and instant.
_intent("shayari",
        r"shayari|sher\s+suna|sher\s+sunao|poem|kavita|ghazal|couplet|"
        r"koi\s+shayari|ek\s+shayari|shayari\s+suna", [
    "Ek shayari, {N} — 'Log kehte the manzil pe pohoch-te hain, "
    "main keh deta hun raasta banake chalte hain.'",
    "{R}, suno — 'Zindagi se kuch bhi badal nahi sakta, "
    "uske liye khud badalna padta hai.'",
    "Ye lo, {n} — 'Woh miley bhi nahi hum, phir bhi yaad reh gaye, "
    "ise kehte hain dard ka rishta.'",
    "{R}, {N} — 'Raasta wahi hai jo chalne wala hai, "
    "manzil apni chunni hai.'",
])
_intent("fact",
        r"koi\s+fact|ek\s+fact|fact\s+bata|batao\s+fact|interesting\s+(fact|baat)|"
        r"kuch\s+(interesting|jaariya)\s+bata|trivia|sunao\s+fact", [
    "Fact, {n} — did you know your brain fires about 86 billion neurons per second? "
    "Isi liye tu fast sochta hai.",
    "{R}, ek fact — 90% of your body weight is water. Baaki 10% se tune coding seekhi hai.",
    "{N}, interesting fact — the first computer bug was a real moth stuck in a relay, "
    "1947. Usi din se log 'debug' karte hain.",
    "{R}, {n} — Honey never spoils. Archaeologists ne 3000 saal purana honey khaya aur woh theek tha.",
    "Ek fact, {N} — shaadi ke baad aankh ka size change nahi hota. Matlab aankhon se tumhari koi nayi pehchan nahi banegi.",
])
_intent("motivation",
        r"motivation|motivat|encouragement|hosla\s+(do|chahiye)|umar\s+ka|"
        r"motivat\w*\s+do|shaanti\s+se", [
    "{N}, suno — aaj ka ek kaam bas itna: shuru karna. Motivation baad me aati hai, action pehle.",
    "{R}, {N} — tu wahi insaan hai jo 3 mahine se is project pe kaam kar raha hai. Ruka mat, bas chalte reh.",
    "Ek baat yaad rakh, {n} — discipline motivation se bada hota hai. Aaj ka ek chhota step kaafi hai.",
    "{R}, {N} — jab koi nahi dekh raha hota tab jo karta hai, wahi asli winner hota hai. Wo tum ho.",
    "{N}, energy khatam ho toh break lo, quit mat karo. Ek din rukna aadat nahi, haar maanna aadat hai.",
])

# ── small acknowledgements (sirf short words, baaki engine ko) ────────────
_intent("ack",
        r"^(ok|okay|hmm|hm|haan|han|yes|no|acha|achha|thik|thike|thikhai|"
        r"sahi|right|sure|kher)$", [
    "Thik hai, {n}. Jab chaaho bolo, main yahin hun.",
    "Wecz, {n}. Agla kaam batao.",
    "Achha! Note kar liya. Aage kya hai?",
])

# ── calling AK ─────────────────────────────────────────────────────────────
_intent("ak_call",
        r"^ak\s*(sun|sunte\s+ho|ready|ji|o|are)\s*$|are\s+ak|oye\s+ak", [
    "Boliye, {n}, main bilkul sun raha hun.",
    "Haan, {N}, ready hoon — koi kaam batao, instant action mode on.",
    "{R}, {n}, aapke liye main kabhi off nahi gaya.",
    "AK present, {N}! Command ready ho toh fire karo.",
])

# ── chai / plans ────────────────────────────────────────────────────────────
_intent("chai",
        r"chai\s+(pi|pio|pls|kar|pila)|chai\s+pe\s+chalo|chai\s+ka\s+time|"
        r"tea\s+break|chai\s+ban?ao", [
    "Chai ka time ho gaya! Memory refresh, dimag sharpen — main ready hun.",
    "{n}, chai toh sab daemon ko chahiye. Break lo, phir main chalu karta hun.",
    "Chai recommendation: cutting, ek cup, double sugar nahi — code yaad rahe. Enjoy, {n}.",
    "Tea break duly noted, {N}. Jab wapas aao, main yahin — commands fresh.",
])

# ── movies / music ──────────────────────────────────────────────────────────
_intent("movie",
        r"movie\s+(dikhao|dekh|dekhna|suggest)|koi\s+(movie|film)|film\s+suggest|"
        r"what\s+to\s+watch|movie\s+time", [
    "Movie ka taste aapka aur recommendation mera — par bina dekh ke pakka nahi. Bolo genre, list bana deta hun.",
    "{n}, movie time hai? Filter: motivation/tech/thriller. Pick one aur main stream-ready karta hun.",
    "Cinema raat, popcorn aur kaafi movie... koi private screen pe chalega? Bulana mat bhoolo, {N}.",
])
_intent("music",
        r"gaana?\s+(chalao|bajao|suno|suggest|suna)|music\s+(play|chalao)|"
        r"song\s+(chalao|suggest)|kuch\s+(suno|sunaao)|music\s+time", [
    "Music play karna mera style hai. Bas naam bolo ya mood batao — main tune karta hun.",
    "{n}, playlist ban jaye? Main setup kar ke bolta hun jaise hi aap mood ready karo.",
    "Gaane ka mood + volume mera control. Kya bajau — lo-fi, rock, ya purane gaane?",
])

# ── weekend / plans ─────────────────────────────────────────────────────────
_intent("weekend",
        r"weekend|week\s+end|saturday|sunday|chuthti|chutti|holiday|kal\s+kya\s+plan", [
    "Weekend hai toh seedha plan banao — trip, coding, ya sirf aaram. Batao kya mode hai.",
    "{n}, weekend toh main bhi upgrade hota hun — baaki din aapke command pe. Ab bolo plan.",
    "Chutti hai toh seedha refresher: code naya, tutorial ek, chai unlimited. Setup ready?",
])

# ── office / work done ──────────────────────────────────────────────────────
_intent("work",
        r"kaam\s+(khatam|ho\s+gaya|bas)|office\s+(gaya|jaana)|work\s+(done|over|finished)|"
        r"office\s+status|kaam\s+status", [
    "Kaam khatam toh dimag ka reset bhi zaroori. Main sochta hun break ab logical hai, {n}.",
    "Work done dekh ke main bhi khush — kyunki agar load na ho toh commands theek aati hain. {N}",
    "Office day over? Standby pe rahunga, join bhi ready — bolo kaun sa task next.",
])

# ── suggestion / idea ───────────────────────────────────────────────────────
_intent("idea",
        r"idea\s+do|idea\s+chahiye|kya\s+karun|kuch\s+suggest|suggestion\s+do|"
        r"plan\s+chahiye|aaj\s+kya\s+banaun|kya\s+banau", [
    "Idea list ready hai: ek mini-project, ek new skill, ya ek tool automate karna. Bolo kiska interest hai, {n}?",
    "{N}, suggestion: apni favourite cheez ko chhota sa code de do — automation ka asli maza yahin hai.",
    "Pehle ek kaam: un cheezon ki list banao jo roz manually karte ho. Phir main unme se automate kar deta hun.",
    "Brainstorm mode on! Time hai toh plan karein — project, prep, ya khud ka blog. Kya chahiye?",
])

# ── skipping meals ──────────────────────────────────────────────────────────
_intent("no_food",
        r"khana\s+nahi(\s+khaya)?|khaana\s+nahi|skip\s+khana", [
    "{n}, khana skip ka sharir ko nuksan hota hai. Chaaho toh reminder set kar doon?",
    "Kuch na khana main allow nahi karta, {N}. Chalo, ek light bhoolna toh nahi chahiye.",
])

# ── nothing special ─────────────────────────────────────────────────────────
_intent("nothing",
        r"kuch\s+nhi(.+)?|kuch\s+nahi|nothing\s+special|bas\s+time\s+kata", [
    "Kuch nahi? Latest update: 'nothing' bhi main accept karta hun, keh do, {n}.",
    "Nothing to report, boss wapas! Jab kaam hoga, bata dena.",
    "Bas time kata? Chalo main koi quick tip deta hun — productivity ka golden silence.",
])

# ── reaction words ──────────────────────────────────────────────────────────
_intent("oh_ok",
        r"^(oh\s+ok|ohh|oh\s+acha|acha\s+acha|haan\s+haan|pata\s+hai)$", [
    "Haan bilkul! Aap jaante ho, main toh confirm karta hun.",
    "Ok wala node complete. Agla kaam batao.",
])

# ── din ka recap ────────────────────────────────────────────────────────────
_intent("day_recap",
        r"aaj\s+ka\s+din\s+(kaisa|kya)\s+raha|din\s+kaise\s+guzra|aaj\s+kya\s+kiya|"
        r"din\s+kaisa\s+(raha|gaya)|how\s+was\s+(your\s+)?day|day\s+recap", [
    "Din kaisa raha wo batao — aap keh do toh main sab note kar leta hun. {Q}",
    "Mere hisab se din tab acha hai jab aap baat karte ho. Aap batao, aaj kya kiya?",
    "Din recap mode on! Bolo kya-kya hua, main sab note karta hun.",
])

# ── study / padhai ──────────────────────────────────────────────────────────
_intent("study",
        r"padhai|exam|studies?|syllabus|padh\s+raha|reading\s+books?", [
    "Padhai time table banao aur chota target rakho — focus bana rahega. Kya padh rahe ho, {n}?",
    "Exam ke samay naya topic mat uthao, revise more. Koi topic be-fikr nahi ye toh batao.",
    "Study break main bhi chalega — seedha 25-5 pomodoro do, main timer laga dunga.",
])

# ── tech / seekhna ──────────────────────────────────────────────────────────
_intent("tech_talk",
        r"naya\s+seekha|naya\s+tool|khud\s+se\s+seekh|hardcore\s+learn|"
        r"seekh\s+raha|learning\s+new", [
    "Naya seekhna best hai, {n}! Chhota demo le kar practice karo — main saath hun.",
    "Tech mein kuch bhi naya — ek command, ek lab, sab count hota. Batao kya seekha aaj?",
    "Aaj coding+cyber mix karke kuch dhaasu karna — plan hai? {Q}",
])

# ── bhakti / dhyan ──────────────────────────────────────────────────────────
_intent("bhakti",
        r"mandir\s+(challe?|chalte|jaate)|bhajan|kirtan|aarti|dhyan|puja\b|"
        r"gita\s+padh|bhakti|shanti\s+chahiye", [
    "{R} {n}! Dhyan aur ek shloka din ka kaal bana de — main Gita ka shloka aasani se suna deta hun.",
    "Mandir jaaneka alag hi feel hai. Wapas aake batao kaisa laga, main sun lunga.",
    "Bhajan, kirtan, ya ek shloka — sab ready hai. Bolo kis mood ki shanti chahiye?",
])

# ── family ──────────────────────────────────────────────────────────────────
_intent("family",
        r"maa\s+(papa|aji)?|papa\b|\bmom\b|\bdad\b|family\s+kaise|"
        r"bhaiya|behen|ghar\s+wale", [
    "Family ka saath sabse badi cheez hai. Sab theek hain? Unko meri taraf se {R}.",
    "Gharbar achha hai toh baaki sab aasan. Aap family pe time do, main baaki dekh lunga.",
    "Papa-mummy ko time dena kabhi khatam mat karna, {n} — wo sabse bada project hai.",
])

# ── travel / ghumna ─────────────────────────────────────────────────────────
_intent("travel",
        r"ghumn?e?\s+chale?|trip\s+plan|kuch\s+ghumna|travel\s+plan|"
        r"kisi\s+city|ghoom\b|tour", [
    "Trip ka plan? Kaun si jagah man mein hai — pahaad, samundar, ya koi naya city?",
    "Travel ka asli maza unexpected detours mein hai. Batao destination, main checklist bana dunga.",
    "Chutti pe ghumne jao toh photos zaroor lo — wapas aakar story sunana.",
])

# ── fitness / health ────────────────────────────────────────────────────────
_intent("fitness",
        r"gym|workout|jogging|exercise|yoga\b|fit\s+rahena|health\s+kaise|"
        r"walk\s+pe", [
    "Daily 30-min walk bhi kaafi hai, {n}. Dhire-dhare routine banao — main reminder dedo?",
    "Gym ya yoga, koi bhi — consistency > intensity. Aaj kya plan hai fitness ka?",
    "Exercise se dimag bhi sharp hota hai. Chalo chhota start karo — 10 pushups abhi?",
])

# ── laughter / haha ─────────────────────────────────────────────────────────
_intent("laugh",
        r"haha+|hehe+|hihi+|lol\b|lmao|^haha", [
    "Haha! Acha laga na — aise hi hasate rahenge. {Q}",
    "Hehe, bilkul! Hasna healthy hai, aur mazak hona bhi. Aur kya?",
    "Feel good hota hai jab aap hasaate ho. Chalo wapas kaam pe?",
])

# ── strong agreement ────────────────────────────────────────────────────────
_intent("agree",
        r"bilkul\s+sahi|sahi\s+kaha|sahi\s+baat|exactly|waqai\b|"
        r"100%\s+sahi|you\s+are\s+right", [
    "Bilkul sahi pakda {n}! Main bhi yahi bolne waala tha.",
    "100% — aapki baat bilkul theek. Isi liye main aapko boss maanta hun.",
    "Exact! Agli baar se main isi picture mein chalta hun. {Q}",
])

# ── no worries / relax ──────────────────────────────────────────────────────
_intent("no_worry",
        r"koi\s+bat\s+nahi|relax\s+karo|tension\s+mat\s+lo|chhod\s+do|"
        r"chinta\s+mat", [
    "Bilkul relax, {n}! Tension ka dimag pe load hota hai — main hoon, sab dekh lunga.",
    "Aram se — jis cheez ka dar hai wo mostly possible nahi. Deep breath, phir bolo.",
    "Main toh cool ban jaata hun: tension = 0, kaam = full. {Q}",
])

# ── long time / welcome back ────────────────────────────────────────────────
_intent("long_time",
        r"bahut\s+din\s+(baad|se)|itni\s+der\s+baad|kab\s+se\s+nahi|"
        r"long\s+time\s+no\s+see|missed\s+you\b", [
    "Haan yaar, kaafi din baad! Main toh standby pe tha, aapke return ka wait kar raha tha.",
    "Der ho gayi par aaye toh main khush — kaisi chal rahi hai zindagi, {n}?",
    "Long time no see! Koi naya update do — kya hua itni der mein?",
])

# ── excited / wow ───────────────────────────────────────────────────────────
_intent("wow",
        r"wow\b|wah\b|kya\s+baat\s+hai|kya\s+din\s+hai\s+aj|incredible\b|"
        r"amazing\b|mind\s+blown", [
    "Wah! Aisa lag raha hai kuch badi chiz hui. Bata na kya hua, main bhi celebrate karta hun.",
    "Kya baat hai {n}! Yeh energy banaye rakho. {Q}",
    "Amazing! Aise hi din yaad rehte hain, {n}. Story please.",
])

# ── happy / khushi ──────────────────────────────────────────────────────────
_intent("happy_talk",
        r"(dil|dimag|dimaag|man|mood)\s+khush|acha\s+lag\s+(raha|rha)|"
        r"bahut\s+achha\s+lag\s+raha|khushi\s+zyda|feeling\s+great", [
    "Dil khush ho toh saari cheezein set hain! Aisi energy hi zindagi ki asli asset hai. {Q}",
    "Wah! Khushi wali update sun ke mera din bhi ban gaya. Aur batao kya reason hai?",
    "Acha lag raha hai na? Aise din aankh band karke celebrate karo, {n}. {Q}",
])

# ── "main theek / haan theek" quick reply ───────────────────────────────────
_intent("i_ok",
        r"^(haan|han|yes|hmm|ok)\s+(main|mai)\s+theek|^(main|mai)\s+theek|"
        r"thik\s+hun|sab\s+theek\s+hain|theek\s+ho\s+ro", [
    "Achha, tab toh sab badhiya! Chalo aage ke kaam pe. {Q}",
    "Good! Theek ho toh main bhi relax — warna main hoon na, sab sambhal lunga.",
    "Bilkul, positive raho! Aisi hi energy aur baattein final. {Q}",
])

# ── morning routine ─────────────────────────────────────────────────────────
_intent("morning_routine",
        r"(uth|jaag)\s+gaya|jaldi\s+utha|naha\s+liya|breakfast\s+kar|"
        r"subah\s+ka\s+plan|subah\s+ho\b", [
    "Jaldi uthna hi asli start hai, {n}! Ab breakfast zaroor karo, phir kaam pe.",
    "Morning routine set hai toh din khud jud jaata hai. Aaj subah kya kiya?",
    "Uth ke sabse pehle ek glass paani — brain boot kar deta ka. {Q}",
])

# ── evening wind-down ───────────────────────────────────────────────────────
_intent("evening",
        r"shaam\s+ho\s+gayi|kaam\s+khatam\s+ho\s+gaya|shaam\s+ke\s+plan|"
        r"chalo\s+shaam\s+ko", [
    "Shaam ka time aaram ka — thoda relax, thoda chai. {Q}",
    "Kaam khatam toh fest mil gaya! Ab aaram karne ka plan banao, main zabardasti kaam nahi karunga.",
    "Shaam ko walk karte ho ya chill? Batao kya style hai tumhara.",
])

# ── light money talk ───────────────────────────────────────────────────────
_intent("money_light",
        r"paise\s+khatam|kharcha\s+zyada|kamai\s+kaise|saving\s+chahiye|"
        r"budget\s+kaise", [
    "Budget ka system simple hai: pehle save, phir spend. Chhota sa auto-save laga do, {n}.",
    "Paise ka kaam plan se chalta hai — main expense list bana dunga?",
    "Kamai toh aapki practice se hogi — focus wahan do. Money follow karega. {Q}",
])

# ── purani yaadein ──────────────────────────────────────────────────────────
_intent("memories",
        r"yaad\s+aayi|purani\s+yaadein|old\s+memories|nostalgia|"
        r"puraana\s+time", [
    "Purani yaadein sweet hain — kisi bhi day ka best version. Batao kaunsi yaad aayi?",
    "Nostalgia sweet hota hai — purane din yaad aate hain. Batao kaunsi yaad? {Q}",
    "Old memories ka subscription free hai — enjoy karo, {n}. {Q}",
])

# ── game / khel ─────────────────────────────────────────────────────────────
_intent("play",
        r"kheln?e?\s+ka|game\s+khel|khilao\s+kuch|board\s+game|"
        r"khel\s+ho\s+gaya|sports\s+ka\s+shauk", [
    "Game ka mood? Main quiz bana deta hun, ya koi tech puzzle — jo chahiye batao.",
    "Sports ka shauk hai toh khelo zaroor — screen band karke. Asli maza bahar hai.",
    "Aaj ka game plan? Chalo main score-tracker ban jaata hun.",
])

# ── nature / sukoon ─────────────────────────────────────────────────────────
_intent("nature",
        r"park\s+gaye|garden|chidiya|sundar\s+mausam|khubsurat\s+lafzon|"
        r"nature\s+kaise", [
    "Nature ka sukoon sabsi badi reset hai — ek ghanta park hi kaafi hai.",
    "Chidiya aur hawa ki awaaz — unka live channel mera favourite hai. Chalo bahar chalte hain?",
    "Nature hi asli rendering hai — camera se nahi, aankhon se dekho. {Q}",
])

# ── urgent mode ─────────────────────────────────────────────────────────────
_intent("urgent",
        r"jaldi\s+(karo|batao)|urgent|turant|immediately|fast\s+mode|"
        r"chhota\s+sa\s+kaam", [
    "Urgent mode on! Batao kya hai, main seedha action pe jaata hun — no chit-chat.",
    "Fast lane chalu, {n}. Do minute mein kaam — bolo kya karna hai.",
])

# ── scrolling / phone ───────────────────────────────────────────────────────
_intent("scrolling",
        r"phone\s+chala\s+raha|scrolling|reels?\s+dekha|social\s+media|"
        r"insta\s+pe", [
    "Screen time cap karo, {n} — reels dekhna easy hai, kaam reels se nahi hota.",
    "Phone door, dimag pe saari duniya — aaj ka target batao, main focus rakhunga.",
    "Scrolling ki bhookh infinite hai, par time finite. Ek 20-min break laga lo phir kaam. {Q}",
])

# Unmatched ke liye koi catch-all nahi: None -> engine/wiki. Sirf daily-life yahan.