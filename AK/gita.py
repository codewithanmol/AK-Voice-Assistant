"""Bhagavad Gita + Shrimad Bhagavatam — daily wisdom with style.

Features:
  • Daily Gita shloka (deterministic, tracked, never repeats)
  • Mood-based shloka recommendation (happy / sad / angry …)
  • Daily Bhagavatam story (sequential, picks up where you left off)
  • State persisted to data/gita_state.json
  • Auto-fetches from vedicscriptures.github.io API, falls back to curated
  • Smart console + voice formatting
"""

from __future__ import annotations

import datetime
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ── Paths ──────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
_PROJECT = _HERE.parent


def _state_file() -> Path:
    from .config import get_data_dir
    return Path(get_data_dir()) / "gita_state.json"

# ── Gita API ───────────────────────────────────────────────────────
GITA_API = "https://vedicscriptures.github.io/slok"

# Verse count per chapter (total = 700)
VERSES_PER_CHAPTER = [47, 72, 43, 42, 29, 47, 30, 28, 34, 42, 55, 20, 35, 27, 20, 24, 28, 78]

CHAPTER_NAMES_HINDI = [
    "अर्जुनविषादयोग", "सांख्ययोग", "कर्मयोग", "ज्ञानकर्मसंन्यासयोग",
    "कर्मसंन्यासयोग", "ध्यानयोग", "ज्ञानविज्ञानयोग", "अक्षरब्रह्मयोग",
    "राजविद्याराजगुह्ययोग", "विभूतियोग", "विश्वरूपदर्शनयोग",
    "भक्तियोग", "क्षेत्रक्षेत्रज्ञविभागयोग", "गुणत्रयविभागयोग",
    "पुरुषोत्तमयोग", "दैवासुरसंपद्विभागयोग", "श्रद्धात्रयविभागयोग",
    "मोक्षसंन्यासयोग"
]
CHAPTER_NAMES_ENGLISH = [
    "Arjuna Vishada Yoga", "Sankhya Yoga", "Karma Yoga", "Jnana Karma Sannyasa Yoga",
    "Karma Sannyasa Yoga", "Dhyana Yoga", "Jnana Vijnana Yoga", "Akshara Brahma Yoga",
    "Raja Vidya Raja Guhya Yoga", "Vibhuti Yoga", "Vishvarupa Darshana Yoga",
    "Bhakti Yoga", "Kshetra Kshetrajna Vibhaga Yoga", "Guna Traya Vibhaga Yoga",
    "Purushottama Yoga", "Daivasura Sampad Vibhaga Yoga", "Shraddha Traya Vibhaga Yoga",
    "Moksha Sannyasa Yoga"
]

# ── Mood‑based shloka mapping ─────────────────────────────────────
# Each mood maps to (chapter, verse, brief_context) so we can fetch from API
MOOD_SHLOKAS: Dict[str, List[Tuple[int, int, str]]] = {
    "sad": [
        (2, 14, "The temporary nature of pleasure and pain — stay balanced"),
        (2, 47, "Your right is to action alone, not the fruits — do your duty"),
        (6, 5, "Elevate yourself by yourself; do not let the mind drag you down"),
        (18, 73, "Arjuna's transformation — delusion destroyed, faith restored"),
    ],
    "angry": [
        (16, 21, "Three gates to hell: lust, anger, greed — abandon them"),
        (2, 63, "From anger comes delusion, from delusion loss of memory"),
        (3, 37, "Anger is lust transformed — know your true enemy"),
        (5, 29, "Knowing Me as the supreme enjoyer — one attains peace"),
    ],
    "confused": [
        (4, 18, "See action in inaction, inaction in action — that is wisdom"),
        (2, 40, "Even a little practice saves one from great fear"),
        (2, 47, "Do your duty without worrying about results"),
        (3, 30, "Surrender all actions to Me — fight free from anxiety"),
    ],
    "happy": [
        (9, 26, "Whoever offers Me a leaf, fruit, or water with love — I accept"),
        (10, 8, "I am the source of everything — the wise worship Me with joy"),
        (9, 22, "To those who worship Me with devotion — I carry what they lack"),
        (12, 13, "One who is not hateful, friendly, compassionate — is dear to Me"),
    ],
    "anxious": [
        (2, 70, "Calm like the ocean — undisturbed by desires, that is peace"),
        (6, 26, "Gently bring the mind back whenever it wanders"),
        (12, 15, "One who does not agitate the world — he is dear to Me"),
        (18, 58, "Fix your mind on Me — you shall overcome all obstacles"),
    ],
    "motivated": [
        (2, 47, "Your right is to action alone — never to the fruits"),
        (3, 8, "Perform your duty — action is superior to inaction"),
        (3, 19, "Act without attachment — do your duty"),
        (11, 33, "Be an instrument — the enemy is already slain"),
    ],
    "peaceful": [
        (2, 70, "Unshaken like the ocean — that person attains peace"),
        (5, 29, "Knowing Me — one attains peace and freedom"),
        (12, 12, "Better than knowledge is meditation, better is renunciation"),
        (6, 15, "With mind controlled — attains peace supreme"),
    ],
    "devotional": [
        (9, 34, "Fix your mind on Me — be devoted to Me"),
        (18, 65, "Always think of Me — become My devotee"),
        (12, 13, "Free from malice, friendly, compassionate — very dear to Me"),
        (10, 9, "They take great pleasure in serving Me — I give them wisdom"),
    ],
}


# ═══════════════════════════════════════════════════════════════════
# State persistence
# ═══════════════════════════════════════════════════════════════════

_DEFAULT_STATE = {
    "gita": {
        "visited_verses": [],
        "last_date": None,
    },
    "bhagavatam": {
        "visited_indices": [],
        "last_date": None,
    },
    "mood": {
        "last_offer_date": None,
        "total_offers": 0,
        "total_accepts": 0,
    },
    "stats": {
        "first_date": datetime.date.today().isoformat(),
        "total_gita_told": 0,
        "total_stories_told": 0,
        "last_active_date": None,
    },
}


def _load_state() -> dict:
    try:
        state_file = _state_file()
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            for k, v in _DEFAULT_STATE.items():
                if k not in data:
                    data[k] = v
                elif isinstance(v, dict):
                    for sk, sv in v.items():
                        data[k].setdefault(sk, sv)
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return dict(_DEFAULT_STATE)


def _save_state(state: dict) -> None:
    state_file = _state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _today() -> str:
    return datetime.date.today().isoformat()


def _is_new_day() -> bool:
    today = _today()
    state = _load_state()
    last = state.get("stats", {}).get("last_active_date")
    return last != today


# ═══════════════════════════════════════════════════════════════════
# Gita API helpers
# ═══════════════════════════════════════════════════════════════════

def verse_chapter_str(ch: int, verse: int) -> str:
    return f"Chapter {ch}: {CHAPTER_NAMES_ENGLISH[ch-1]} — verse {verse}"


def _index_to_chapter_verse(index: int) -> Tuple[int, int]:
    i = index % sum(VERSES_PER_CHAPTER)
    for ch, count in enumerate(VERSES_PER_CHAPTER, 1):
        if i < count:
            return ch, i + 1
        i -= count
    return 18, 78


def _chapter_verse_to_index(ch: int, verse: int) -> int:
    return sum(VERSES_PER_CHAPTER[:ch - 1]) + verse - 1


# ── Fetch from API ─────────────────────────────────────────────────

def fetch_verse(ch: int, verse: int) -> Optional[dict]:
    try:
        resp = requests.get(f"{GITA_API}/{ch}/{verse}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _pick_translation(data: dict) -> str:
    """Pick the best English translation available."""
    for key in ("prabhu", "siva", "purohit", "san", "gambir"):
        if key in data and "et" in data[key]:
            return data[key]["et"]
    return ""


def _pick_purport(data: dict) -> str:
    """Pick the best commentary/purport available."""
    for key in ("prabhu", "siva", "chinmay"):
        if key in data and "ec" in data[key]:
            return data[key]["ec"]
    return ""


# ── Voice‑ready text formatter ─────────────────────────────────────

def _clean_for_speech(text: str) -> str:
    """Strip characters that hurt TTS but keep useful content."""
    # Remove verse-number markers like ||1-1|| ||१-१||
    text = re.sub(r"\|\|[\d\-\u0966-\u096F]+\|\|", "", text)
    # Remove standalone vertical bars (Devanagari danda / verse separators)
    text = text.replace("|", "")
    # Remove Devanagari double danda
    text = text.replace("॥", "")
    # Remove Devanagari single danda
    text = text.replace("।", "")
    # Remove leading/trailing standalone dots from transliteration lines
    text = re.sub(r" \. ", " ", text)
    text = re.sub(r"^\. ", "", text)
    text = re.sub(r" \.$", "", text)
    # Collapse multiple spaces
    text = re.sub(r" +", " ", text).strip()
    return text


def _api_to_reading(data: dict, ch: int, verse: int, detail: str = "full") -> List[str]:
    """Convert API response into a list of spoken segments.

    `detail` can be "brief" or "full".
    """
    lines: List[str] = []

    sanskrit = _clean_for_speech(data.get("slok", "").replace("\n", " ").strip())
    translit = _clean_for_speech(data.get("transliteration", "").replace("\n", " ").strip())

    lines.append(f"This is from chapter {ch}, verse {verse} of the Bhagavad Gita.")
    lines.append(f"The chapter is called {CHAPTER_NAMES_ENGLISH[ch-1]}.")

    if sanskrit:
        lines.append(f"The Sanskrit shloka: {sanskrit}")
    if translit:
        lines.append(f"Its transliteration: {translit}")

    translation = _pick_translation(data)
    if translation:
        lines.append(f"Translation: {translation}")

    if detail == "full":
        purport = _pick_purport(data)
        if purport:
            purport = _clean_for_speech(purport)
            sentences = [s.strip() for s in purport.replace(".", ".|").replace("?", "?|").split("|") if s.strip()]
            concise = " ".join(sentences[:6])
            lines.append(f"Detailed explanation: {concise}")

    return lines


# ── Daily Gita shloka ──────────────────────────────────────────────

def get_daily_gita(detail: str = "full") -> Dict[str, Any]:
    """Pick a random unvisited shloka every time — fresh each ask.

    Returns dict with keys: chapter, verse, sanskrit, transliteration,
    translation, purport, lines (list of spoken segments)
    """
    total_verses = sum(VERSES_PER_CHAPTER)
    state = _load_state()
    gs = state["gita"]

    visited = {_chapter_verse_to_index(c, v) for c, v in gs.get("visited_verses", [])}

    if len(visited) >= total_verses:
        visited = set()
        gs["visited_verses"] = []

    pool = sorted(set(range(total_verses)) - visited)
    idx = random.choice(pool)
    ch, verse = _index_to_chapter_verse(idx)

    gs["visited_verses"].append([ch, verse])
    gs["last_date"] = _today()

    st = state["stats"]
    st["total_gita_told"] = st.get("total_gita_told", 0) + 1
    _save_state(state)

    data = fetch_verse(ch, verse)
    if data:
        lines = _api_to_reading(data, ch, verse, detail)
        return {
            "chapter": ch,
            "verse": verse,
            "sanskrit": data.get("slok", ""),
            "transliteration": data.get("transliteration", ""),
            "translation": _pick_translation(data),
            "purport": _pick_purport(data),
            "lines": lines,
            "source": "api",
        }

    return _get_curated_fallback(ch, verse)


# ── Mood‑based shloka ──────────────────────────────────────────────

_MOOD_NAMES = {
    "sad": "Sad or low",
    "angry": "Angry or frustrated",
    "confused": "Confused or uncertain",
    "happy": "Happy and grateful",
    "anxious": "Anxious or worried",
    "motivated": "Seeking motivation",
    "peaceful": "Seeking peace and calm",
    "devotional": "Feeling devotional",
}

def get_shloka_by_mood(mood: str, detail: str = "full") -> Optional[Dict[str, Any]]:
    """Get a shloka based on user's mood."""
    mood = mood.lower().strip()
    if mood not in MOOD_SHLOKAS:
        return None

    candidates = MOOD_SHLOKAS[mood]
    ch, verse, context = random.choice(candidates)

    data = fetch_verse(ch, verse)

    if data:
        lines = _api_to_reading(data, ch, verse, detail)
        lines.insert(0, f"Showing a shloka for the {mood} mood.")
        lines.insert(1, context)
        return {
            "chapter": ch,
            "verse": verse,
            "sanskrit": data.get("slok", ""),
            "transliteration": data.get("transliteration", ""),
            "translation": _pick_translation(data),
            "purport": _pick_purport(data),
            "lines": lines,
            "source": "api",
            "mood": mood,
            "context": context,
        }

    return _get_curated_fallback(ch, verse, mood=mood, context=context)


def list_moods() -> str:
    """Return a human-readable list of available moods."""
    return "You can choose from: " + ", ".join(
        f"{k} ({v})" for k, v in _MOOD_NAMES.items()
    )


# ── Curated fallback shlokas ───────────────────────────────────────

_CURATED = {
    (2, 47): {
        "sanskrit": "कर्मण्येवाधिकारस्ते मा फलेषु कदाचन । मा कर्मफलहेतुर्भूर्मा ते सङ्गोऽस्त्वकर्मणि ॥",
        "transliteration": "karmaṇy-evādhikāras te mā phaleṣu kadācana | mā karma-phala-hetur bhūr mā te saṅgo 'stv akarmaṇi ||",
        "translation": "You have a right to perform your prescribed duties, but you are not entitled to the fruits of your actions. Never consider yourself the cause of the results, and never be attached to inaction.",
        "purport": "This is the essence of Karma Yoga. Lord Krishna tells Arjuna to fight not for victory or kingdom, but simply because it is his duty. When we work without attachment to results, we are freed from the bondage of karma. This teaching transforms work into worship."
    },
    (2, 14): {
        "sanskrit": "मात्रास्पर्शास्तु कौन्तेय शीतोष्णसुखदुःखदाः । आगमापायिनोऽनित्यास्तांस्तितिक्षस्व भारत ॥",
        "transliteration": "mātrā-sparśās tu kaunteya śītoṣṇa-sukha-duḥkha-dāḥ | āgamāpāyino 'nityās tāṁs titikṣasva bhārata ||",
        "translation": "O son of Kunti, the contacts of the senses with their objects give rise to feelings of heat and cold, pleasure and pain. They come and go and are impermanent. O scion of Bharata, endure them patiently.",
        "purport": "Sri Krishna teaches us that pleasure and pain are temporary like seasons. A wise person tolerates them without being disturbed. This equanimity is the first step toward spiritual growth."
    },
    (6, 5): {
        "sanskrit": "उद्धरेदात्मनात्मानं नात्मानमवसादयेत् । आत्मैव ह्यात्मनो बन्धुरात्मैव रिपुरात्मनः ॥",
        "transliteration": "uddhared ātmanātmānaṁ nātmānam avasādayet | ātmaiva hy ātmano bandhur ātmaiva ripur ātmanaḥ ||",
        "translation": "One should elevate oneself by one's own mind, not degrade oneself. The mind is the best friend of the self, and also its worst enemy.",
        "purport": "Lord Krishna explains that we have the power to lift ourselves up through our own efforts. The same mind that can be our greatest ally can also become our worst enemy if not controlled."
    },
    (9, 26): {
        "sanskrit": "पत्रं पुष्पं फलं तोयं यो मे भक्त्या प्रयच्छति । तदहं भक्त्युपहृतमश्नामि प्रयतात्मनः ॥",
        "transliteration": "patraṁ puṣpaṁ phalaṁ toyaṁ yo me bhaktyā prayacchati | tad ahaṁ bhakty-upahṛtam aśnāmi prayatātmanaḥ ||",
        "translation": "Whoever offers Me with love and devotion a leaf, a flower, a fruit, or even water, I accept that offering made with a pure heart.",
        "purport": "This beautiful verse reveals God's unconditional love. He does not demand grand offerings — even the simplest thing offered with love is accepted. What matters is the devotion, not the material value."
    },
    (10, 8): {
        "sanskrit": "अहं सर्वस्य प्रभवो मत्तः सर्वं प्रवर्तते । इति मत्वा भजन्ते मां बुधा भावसमन्विताः ॥",
        "transliteration": "ahaṁ sarvasya prabhavo mattaḥ sarvaṁ pravartate | iti matvā bhajante māṁ budhā bhāva-samanvitāḥ ||",
        "translation": "I am the source of all spiritual and material worlds. Everything emanates from Me. The wise who perfectly know this engage in My devotional service and worship Me with all their hearts.",
        "purport": "Sri Krishna reveals His supreme position as the origin of everything. Those who truly understand this are filled with natural devotion and love for the Divine."
    },
    (18, 73): {
        "sanskrit": "अर्जुन उवाच नष्टो मोहः स्मृतिर्लब्धा त्वत्प्रसादान्मयाच्युत । स्थितोऽस्मि गतसन्देहः करिष्ये वचनं तव ॥",
        "transliteration": "arjuna uvāca | naṣṭo mohaḥ smṛtir labdhā tvat-prasādān mayācyuta | sthito 'smi gata-sandehaḥ kariṣye vacanaṁ tava ||",
        "translation": "Arjuna said: My delusion is destroyed. By Your grace, O Achyuta, I have regained my memory. I am now firm, free from doubt, and I will act according to Your instruction.",
        "purport": "At the end of the Gita, Arjuna's transformation is complete. From confusion and despair, he moves to clarity and resolve. This verse shows that divine guidance dispels all delusion."
    },
    (2, 20): {
        "sanskrit": "न जायते म्रियते वा कदाचिन्नायं भूत्वा भविता वा न भूयः । अजो नित्यः शाश्वतोऽयं पुराणो न हन्यते हन्यमाने शरीरे ॥",
        "transliteration": "na jāyate mriyate vā kadācin nāyaṁ bhūtvā bhavitā vā na bhūyaḥ | ajo nityaḥ śāśvato 'yaṁ purāṇo na hanyate hanyamāne śarīre ||",
        "translation": "The soul is never born nor does it ever die. It has not come into being, will not cease to be, and is unborn, eternal, ever-existing, and primeval. It is not slain when the body is slain.",
        "purport": "Sri Krishna reveals the immortal nature of the soul. Death is only for the body, not for the eternal soul within. This knowledge liberates us from the fear of death."
    },
}


def _get_curated_fallback(ch: int, verse: int, **extra) -> Dict[str, Any]:
    key = (ch, verse)
    if key in _CURATED:
        c = _CURATED[key]
        sanskrit = _clean_for_speech(c["sanskrit"])
        translit = _clean_for_speech(c["transliteration"])
        lines = [
            f"This shloka is from chapter {ch}, verse {verse} of the Bhagavad Gita.",
            f"Sanskrit: {sanskrit}",
            f"Transliteration: {translit}",
            f"Translation: {c['translation']}",
            f"Explanation: {c['purport']}",
        ]
        # Insert context if provided
        if "context" in extra and extra["context"]:
            lines.insert(1, extra["context"])
        if "mood" in extra:
            lines.insert(0, f"Showing a shloka for the {extra['mood']} mood.")
        return {
            "chapter": ch,
            "verse": verse,
            "sanskrit": c["sanskrit"],
            "transliteration": c["transliteration"],
            "translation": c["translation"],
            "purport": c["purport"],
            "lines": lines,
            "source": "curated",
            **extra,
        }

    # Ultra fallback — generic message
    return {
        "chapter": ch,
        "verse": verse,
        "sanskrit": "",
        "transliteration": "",
        "translation": "May the wisdom of the Gita guide you on your path.",
        "purport": "The Bhagavad Gita is Lord Krishna's eternal message of wisdom, duty, and devotion.",
        "lines": [
            f"Chapter {ch}, verse {verse} of the Bhagavad Gita.",
            "May the wisdom of the Gita guide you on your path.",
            "The Bhagavad Gita is Lord Krishna's eternal message of wisdom, duty, and devotion.",
        ],
        "source": "fallback",
        **extra,
    }


# ═══════════════════════════════════════════════════════════════════
# Shrimad Bhagavatam — daily stories
# ═══════════════════════════════════════════════════════════════════

BHAGAVATAM_STORIES = [
    {
        "title": "The Birth of Lord Krishna",
        "canto": "10",
        "chapter": "1-3",
        "summary": "The story of Krishna's divine appearance in the prison of Kamsa.",
        "narrative": (
            "In the city of Mathura, the wicked King Kamsa ruled with cruelty. "
            "A divine voice announced that Devaki's eighth son would kill him. "
            "Enraged, Kamsa imprisoned his sister Devaki and her husband Vasudeva, "
            "killing each infant as they were born. "
            "When the eighth child, Krishna, appeared, the prison doors miraculously opened, "
            "the guards fell asleep, and Vasudeva carried the baby across the Yamuna river "
            "to Gokul, where he exchanged him with Nanda and Yashoda's newborn daughter. "
            "Thus the Supreme Lord took birth to protect the righteous and destroy the wicked."
        ),
        "teaching": "The Lord always protects His devotees. Even in the darkest prison, divine light shines."
    },
    {
        "title": "Putana — The Demoness Who Sucked Death",
        "canto": "10",
        "chapter": "6",
        "summary": "Kamsa sent the demoness Putana to kill baby Krishna.",
        "narrative": (
            "Kamsa, unable to kill Krishna himself, sent the demoness Putana to Gokul. "
            "She disguised herself as a beautiful woman and entered Nanda's house. "
            "Pretending to love babies, she picked up little Krishna and began to nurse Him "
            "with her poisoned breast. But baby Krishna squeezed so hard that He sucked "
            "her life force away, killing the demoness. "
            "Her demonic form was revealed, her body fell to the ground, "
            "and the cowherd men were astonished. "
            "Thus Krishna saved the village from the demoness."
        ),
        "teaching": "Even when danger comes in a beautiful disguise, the Lord protects those who trust in Him."
    },
    {
        "title": "Krishna Steals Butter",
        "canto": "10",
        "chapter": "8-9",
        "summary": "The childhood pastimes of Krishna stealing butter with His friends.",
        "narrative": (
            "Young Krishna was notorious for stealing butter and yogurt with His friends. "
            "He would climb on the shoulders of His friends to reach the hanging pots, "
            "break them open, and distribute the contents to the monkeys and His friends. "
            "Mother Yashoda would often chase Him, but He was too quick. "
            "One day, after being caught, Yashoda tried to tie Him to a grinding mortar, "
            "but the rope was always two fingers too short. "
            "Finally, she gave up and understood that her son was no ordinary child. "
            "These playful pastimes show that God is not just majestic — He is also adorable."
        ),
        "teaching": "The Lord is affectionate and playful with His devotees. Divine love is personal and sweet."
    },
    {
        "title": "Kaliya — The Serpent Subdued",
        "canto": "10",
        "chapter": "16-17",
        "summary": "Krishna dances on the serpent Kaliya's hoods and purifies the Yamuna.",
        "narrative": (
            "The Yamuna river had become poisonous due to the serpent Kaliya who lived in it. "
            "The water boiled, trees died, and birds fell from the sky. "
            "Brave Krishna jumped into the river and began playing in the water. "
            "Kaliya rose up and wrapped himself around Krishna. "
            "But Krishna freed Himself and began dancing on Kaliya's thousand hoods, "
            "beating them down with His divine feet. "
            "Exhausted and defeated, Kaliya vomited blood. "
            "His wives prayed to Krishna for mercy. "
            "Finally, Krishna forgave Kaliya and ordered him to leave the Yamuna and go to the ocean. "
            "The river was purified, and all the cowherd boys rejoiced."
        ),
        "teaching": "Evil has no power before the divine. The Lord dances upon our ego just as He danced on Kaliya."
    },
    {
        "title": "Govardhana Hill — Lifted by Krishna",
        "canto": "10",
        "chapter": "24-25",
        "summary": "Krishna lifted the Govardhana hill on His little finger to protect the villagers.",
        "narrative": (
            "The cowherds of Vrindavan prepared to worship Lord Indra for rain. "
            "Young Krishna told them to worship Govardhana hill instead, "
            "which provided them grass for cows and water from its streams. "
            "Angered, Indra sent devastating rainstorms to destroy Vrindavan. "
            "The villagers ran to Krishna for shelter. "
            "Krishna lifted Govardhana hill with His left hand, holding it like an umbrella. "
            "For seven days and seven nights, the villagers stayed under the hill. "
            "Indra's pride was shattered. He withdrew the storms and came to apologize. "
            "This pastime teaches that God is supreme over all celestial beings."
        ),
        "teaching": "Pride leads to downfall. The Lord protects those who surrender to Him with simple faith."
    },
    {
        "title": "Brahma Steals the Cowherd Boys",
        "canto": "10",
        "chapter": "13",
        "summary": "Lord Brahma tested Krishna by stealing His friends and calves.",
        "narrative": (
            "Brahma, the creator god, wanted to test Krishna's divinity. "
            "He stole the cowherd boys and calves and hid them away. "
            "When Krishna returned and found them missing, He immediately understood. "
            "The Lord then expanded Himself into multiple Krishnas, each playing the role "
            "of a cowherd boy and each accompanied by calves. "
            "For one year, the expanded Krishnas lived in Vrindavan. "
            "When Brahma finally returned, he was astonished — every single cowherd boy "
            "and calf was Vishnu Himself! Brahma fell at Krishna's feet and prayed for forgiveness."
        ),
        "teaching": "No one can test the Lord. He is the Supreme God, greater than even the greatest gods."
    },
    {
        "title": "Rasa Lila — The Dance of Divine Love",
        "canto": "10",
        "chapter": "29-33",
        "summary": "Krishna danced the Rasa Lila with the Gopis under the autumn moon.",
        "narrative": (
            "One autumn night, the full moon shone brilliantly over Vrindavan. "
            "The gopis, unable to bear Krishna's absence, left their homes and ran to the forest. "
            "They gathered on the banks of the Yamuna, their hearts filled with love for Krishna. "
            "Krishna danced with each gopi simultaneously, expanding Himself to be with every one. "
            "The sound of ankle bells, the sweet flute, and the ecstatic love created a divine atmosphere. "
            "This dance was not an ordinary dance — it was the ultimate expression of the soul's love for God. "
            "Through this pastime, Krishna shows that pure love is the highest form of devotion."
        ),
        "teaching": "The soul's deepest longing is to unite with the Divine. Pure love transcends all boundaries."
    },
    {
        "title": "Kamsa Killed — The Prophecy Fulfilled",
        "canto": "10",
        "chapter": "44",
        "summary": "Krishna killed Kamsa and freed His parents from prison.",
        "narrative": (
            "Kamsa arranged a grand wrestling match, inviting Krishna and Balarama to Mathura. "
            "He set up a conspiracy to kill them with a giant elephant and powerful wrestlers. "
            "Krishna killed the elephant with ease and defeated the wrestlers. "
            "Finally, Krishna confronted Kamsa on his throne. "
            "He grabbed Kamsa by his hair, dragged him into the arena, and killed him. "
            "Devaki and Vasudeva were freed from prison. "
            "Krishna blessed them and reestablished righteousness in Mathura. "
            "The prophecy was fulfilled — the Lord had destroyed the wicked and protected the good."
        ),
        "teaching": "Righteousness always triumphs. When evil reaches its peak, the Lord descends to restore balance."
    },
    {
        "title": "Sudama's Visit — True Friendship",
        "canto": "10",
        "chapter": "80-81",
        "summary": "Krishna's childhood friend Sudama visited Him in Dwarka.",
        "narrative": (
            "Sudama, a poor Brahmin, was Krishna's classmate in the ashram of Sandipani Muni. "
            "Urged by his wife, Sudama went to Dwarka to meet his wealthy friend Krishna. "
            "He carried only a handful of flattened rice as a gift, feeling too shy to offer it. "
            "But Krishna, the Lord of the universe, saw His friend's heart. "
            "He ran to greet Sudama, washed his feet, and asked about the gift. "
            "Krishna took one handful of the rice and ate it with great delight. "
            "He then gave Sudama unlimited wealth — not because of the gift, but because of his love. "
            "This story shows that God values our love, not our material offerings."
        ),
        "teaching": "True friendship with God transcends all material differences. Love is the only currency He accepts."
    },
    {
        "title": "Rukmini — Krishna Marries the Princess",
        "canto": "10",
        "chapter": "52-54",
        "summary": "Krishna married Princess Rukmini, who chose Him as her husband.",
        "narrative": (
            "Princess Rukmini was the daughter of King Bhishmaka of Vidarbha. "
            "She heard of Krishna and fell in love with Him, but her brother Rukmi "
            "arranged her marriage to Shishupala. "
            "Rukmini sent a Brahmin messenger to Krishna with a letter, begging Him to rescue her. "
            "On the wedding day, Krishna arrived, swept Rukmini into His chariot, "
            "and drove away as the enemy army watched helplessly. "
            "He married her in Dwarka, fulfilling her heart's desire. "
            "This divine marriage symbolizes the soul's union with the Supreme."
        ),
        "teaching": "When we sincerely call out to God, He answers. The Lord responds to the devotion of His devotees."
    },
    {
        "title": "Draupadi's Honor Saved",
        "canto": "mahabharata",
        "chapter": "sabha parva",
        "summary": "Krishna saved Draupadi when Duryodhana tried to disrobe her.",
        "narrative": (
            "After Yudhishthira lost the dice game, the Kauravas dragged Draupadi into the court. "
            "Dushasana tried to disrobe her in front of everyone. "
            "Draupadi cried out to Krishna, raising her hands in surrender. "
            "The Lord heard her prayer and supernaturally extended her sari. "
            "As Dushasana pulled, the cloth became endless. "
            "He grew tired, and mountains of cloth lay in the court, yet Draupadi remained covered. "
            "Everyone was astonished except Bhishma, Drona, and the elders who knew — "
            "this was the work of the Supreme Lord. "
            "Krishna later declared that he would be indebted to Draupadi for this call of love."
        ),
        "teaching": "The Lord protects the honor of those who surrender to Him. He answers every sincere prayer."
    },
    {
        "title": "The Bhagavad Gita — Krishna's Eternal Message",
        "canto": "6",
        "chapter": "bhishma parva",
        "summary": "Krishna taught the Bhagavad Gita to Arjuna on the battlefield of Kurukshetra.",
        "narrative": (
            "As the armies stood ready for battle, Arjuna was overwhelmed with despair. "
            "He saw his teachers, cousins, and elders on both sides and wanted to renounce the fight. "
            "Lord Krishna, as his charioteer, began the most profound discourse ever given. "
            "He taught about the immortality of the soul, the importance of duty, "
            "the path of devotion, and the ultimate reality of God. "
            "Arjuna, enlightened by Krishna's words, took up his bow and fought. "
            "The Bhagavad Gita is not just a historical dialogue — it is God's direct instruction for all humanity."
        ),
        "teaching": "In our moments of greatest confusion, the Gita is our guide. Krishna's message is eternal and universal."
    },
    {
        "title": "Narada Muni — The Divine Messenger",
        "canto": "1",
        "chapter": "5-6",
        "summary": "The story of Narada Muni and how he became a great devotee.",
        "narrative": (
            "Narada Muni, the celestial sage, was born as the son of a maidservant. "
            "As a child, he served great sages who visited his home. "
            "He listened to their conversations about the Lord and developed deep attachment to Krishna. "
            "After the sages left, the young boy continued to meditate on Krishna. "
            "One day, after drinking water, he sat under a tree and saw the Lord's divine vision. "
            "The Lord disappeared but left an unforgettable impression. "
            "When his mother died, Narada traveled the world singing the glories of the Lord. "
            "Thus, even a simple boy became the great sage Narada through devotion."
        ),
        "teaching": "Greatness comes through devotion, not birth. Anyone can reach the Lord through sincere love."
    },
    {
        "title": "Prahlada and Hiranyakashipu — Faith Against All Odds",
        "canto": "7",
        "chapter": "4-9",
        "summary": "Prahlada's unwavering faith in Vishnu despite his father's persecution.",
        "narrative": (
            "The demon king Hiranyakashipu hated Vishnu and forbade anyone from worshiping Him. "
            "But his own son, Prahlada, was a great devotee of Vishnu. "
            "The king tried every method to kill his son — poison, snakes, elephants, fire. "
            "Each time, Vishnu protected Prahlada. "
            "Finally, Hiranyakashipu asked, 'Where is your Vishnu? Is He in this pillar?' "
            "Prahlada said, 'He is everywhere.' "
            "The king struck the pillar, and out came Narasimha — half-man, half-lion — "
            "the fourth incarnation of Vishnu. He killed Hiranyakashipu at twilight, "
            "on the threshold, neither inside nor outside. "
            "Prahlada was saved and became king. "
            "Faith and devotion are more powerful than any material force."
        ),
        "teaching": "Faith in God can overcome any obstacle. The Lord always protects those who truly love Him."
    },
    {
        "title": "Dhruva — The Boy Who Won the Stars",
        "canto": "4",
        "chapter": "8-12",
        "summary": "Young Dhruva's determination to see the Lord made him a star.",
        "narrative": (
            "King Uttanapada had two wives. Young Dhruva, the son of the less favored wife, "
            "was insulted by his stepmother. He went to his father's throne but was rejected. "
            "His mother tried to console him, but Dhruva decided to seek the highest — the Lord Himself. "
            "He went to the forest and performed severe meditation for six months. "
            "The Lord appeared before him and blessed him. "
            "Dhruva, though a child, achieved what great sages could not. "
            "The Lord made Dhruva the Pole Star, shining forever in the sky. "
            "This story shows that sincere determination can move the Divine."
        ),
        "teaching": "No goal is too high for a determined heart. When we seek God with sincerity, He answers."
    },
    {
        "title": "Gajendra — The Elephant Who Cried to God",
        "canto": "8",
        "chapter": "2-4",
        "summary": "The story of King Gajendra who was saved by Vishnu from a crocodile.",
        "narrative": (
            "Gajendra was a great king who was reborn as an elephant due to a sage's curse. "
            "As Lord of the elephants, he lived in a beautiful forest. "
            "One day, while bathing in a lake, a crocodile caught his leg. "
            "For a thousand years, Gajendra struggled against the crocodile. "
            "Finally, exhausted and helpless, he surrendered completely to God. "
            "He cried out with one lotus flower in his trunk. "
            "The Lord heard his cry. Vishnu flew from Vaikuntha on Garuda, "
            "cut off the crocodile's head, and saved Gajendra. "
            "This shows that when we completely surrender, the Lord comes to our rescue."
        ),
        "teaching": "When all hope is lost, sincere surrender to God is the answer. He comes when we call."
    },
    {
        "title": "Vamana — The Dwarf Who Measured the Universe",
        "canto": "8",
        "chapter": "17-22",
        "summary": "Lord Vamana, the dwarf incarnation, asked for three steps of land.",
        "narrative": (
            "King Bali was a powerful demon who conquered all the worlds. "
            "The gods, led by Indra, sought Vishnu's help. "
            "The Lord incarnated as Vamana, a young dwarf Brahmin, "
            "and went to Bali's grand sacrifice. "
            "Bali, known for his charity, asked Vamana what he wanted. "
            "Vamana said, 'Three steps of land.' "
            "Despite warnings from his guru Shukracharya, Bali agreed. "
            "Vamana then expanded to a cosmic form. "
            "With one step, He covered the earth; with the second, the heavens. "
            "For the third step, Bali offered his own head. "
            "The Lord blessed Bali for his humility and made him king of the underworld. "
            "Pride is followed by a fall; humility brings divine grace."
        ),
        "teaching": "True greatness lies in humility. Even a demon who gives sincerely is blessed by the Lord."
    },
    {
        "title": "Krishna Teaches Uddhava — The Final Lesson",
        "canto": "11",
        "chapter": "6-29",
        "summary": "Before leaving the world, Krishna taught Uddhava the ultimate wisdom.",
        "narrative": (
            "As the time of Krishna's departure approached, His dear friend Uddhava "
            "begged Krishna for wisdom that would sustain him. "
            "Krishna then gave the Uddhava Gita — a vast spiritual discourse. "
            "He taught about the nature of the world, the path of yoga, "
            "the qualities of a true devotee, and the way to attain liberation. "
            "Krishna told Uddhava, 'Fix your mind on Me. Surrender to Me completely. "
            "I am the self of all beings. One who sees Me everywhere achieves the highest goal.' "
            "This teaching is considered the final and most intimate message of Krishna."
        ),
        "teaching": "The ultimate teaching is to see God everywhere and in everyone. Surrender is the final step."
    },
    {
        "title": "The Syamantaka Gem — Krishna's Innocence Proved",
        "canto": "10",
        "chapter": "56-57",
        "summary": "Krishna was wrongly accused of stealing the Syamantaka gem.",
        "narrative": (
            "King Satrajit received the divine Syamantaka gem from the sun god. "
            "The gem produced eight loads of gold daily. "
            "Satrajit's brother Prasena wore the gem and went hunting, but a lion killed him "
            "and took the gem. Jambavan, the bear king, killed the lion and took the gem. "
            "When Prasena didn't return, people accused Krishna of stealing the gem for greed. "
            "To clear His name, Krishna tracked the path, fought Jambavan for 28 days, "
            "and recovered the gem. He returned it to Satrajit, proving His innocence. "
            "Satrajit then offered Krishna his daughter Satyabhama in marriage."
        ),
        "teaching": "Even the Lord chooses to clear His name when wrongly accused. Truth always prevails."
    },
    {
        "title": "Krishna Returns to Vrindavan — The Reunion",
        "canto": "10",
        "chapter": "82",
        "summary": "Krishna returned to Vrindavan to meet His old friends and family.",
        "narrative": (
            "After many years in Mathura and Dwarka, Krishna returned to Vrindavan "
            "during a solar eclipse. The gopis saw Him from afar and their hearts overflowed. "
            "Mother Yashoda embraced Him, tears flowing. "
            "Nanda Maharaja, His father, was overjoyed. "
            "The cowherd boys danced around Him. "
            "But the gopis' love was deepest — they had never forgotten Krishna for a moment. "
            "Krishna spent time with everyone, and the separation made their love even sweeter. "
            "This reunion reminds us that divine love transcends time and space."
        ),
        "teaching": "True love for God never fades. Even separation becomes sweet when the heart is full of devotion."
    },
]


def get_daily_bhagavatam_story() -> Dict[str, Any]:
    """Pick a random unvisited Bhagavatam story every time."""
    total = len(BHAGAVATAM_STORIES)
    state = _load_state()
    bs = state["bhagavatam"]

    visited = set(bs.get("visited_indices", []))

    if len(visited) >= total:
        visited = set()
        bs["visited_indices"] = []

    pool = sorted(set(range(total)) - visited)
    idx = random.choice(pool)

    bs["visited_indices"].append(idx)
    bs["last_date"] = _today()

    st = state["stats"]
    st["total_stories_told"] = st.get("total_stories_told", 0) + 1
    _save_state(state)

    story = dict(BHAGAVATAM_STORIES[idx])
    story["progress"] = f"Story number {idx + 1} of {total}."
    story["index"] = idx
    return story


def _format_story_for_voice(story: dict) -> List[str]:
    """Convert a Bhagavatam story into spoken segments."""
    lines = [
        f"{story['progress']} Today's story from the Shrimad Bhagavatam, Canto {story['canto']}: {story['title']}.",
        story["summary"],
        story["narrative"],
        f"The teaching of this story: {story['teaching']}",
    ]
    return lines


def _format_story_for_console(story: dict) -> str:
    """Format a Bhagavatam story beautifully for console output."""
    sep = _c("gold", "─" * 60)
    lines = [
        "",
        sep,
        _c("gold", f"  📖  SHRIMAD BHAGAVATAM — {story['progress']}"),
        sep,
        _c("cyan", f"  Canto {story['canto']} | {story['title']}"),
        sep,
        "",
        _c("green", f"  📌  {story['summary']}"),
        "",
        _c("bold", "  ── Story ──"),
        _c("dim", f"  {story['narrative']}"),
        "",
        _c("bold", "  ── Teaching ──"),
        _c("purple", f"  💡  {story['teaching']}"),
        "",
        sep,
        "",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Proactive mood offer logic
# ═══════════════════════════════════════════════════════════════════

def should_offer_mood_shloka(probability: float = 0.3) -> Tuple[bool, str]:
    """Decide whether to proactively offer a mood-based shloka.

    Returns (should_offer, reason).
    Only offers once per day.
    """
    state = _load_state()
    ms = state["mood"]
    today = _today()

    # Only offer once per day
    if ms.get("last_offer_date") == today:
        return False, "already_offered_today"

    # Random chance
    if random.random() > probability:
        return False, "random_chance_missed"

    # Track
    ms["last_offer_date"] = today
    ms["total_offers"] = ms.get("total_offers", 0) + 1
    _save_state(state)

    return True, "today_is_your_day"


def record_mood_accept() -> None:
    """Record that the user accepted the mood offer."""
    state = _load_state()
    ms = state["mood"]
    ms["total_accepts"] = ms.get("total_accepts", 0) + 1
    _save_state(state)


# ═══════════════════════════════════════════════════════════════════
# Console colors — relaxed warm palette
# ═══════════════════════════════════════════════════════════════════

_C = {
    "gold": "\033[93m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "purple": "\033[95m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _c(code: str, text: str) -> str:
    """Wrap text in ANSI color, reset after."""
    return f"{_C.get(code, '')}{text}{_C['reset']}"


# ═══════════════════════════════════════════════════════════════════
# Master deliver — formats the full Gita experience
# ═══════════════════════════════════════════════════════════════════

OPENING_PHRASE = (
    "before go and work on or in Maya, "
    "lets take some guidance from Krishn ji."
)

def build_gita_delivery(detail: str = "full") -> str:
    """Build the complete daily Gita delivery string (console + voice friendly)."""
    result = get_daily_gita(detail=detail)

    chapter = result["chapter"]
    verse = result["verse"]

    heading = _c("gold", f"  📿  BHAGAVAD GITA  |  Chapter {chapter}, Verse {verse}")
    ch_name = _c("cyan", f"  {CHAPTER_NAMES_HINDI[chapter-1]}")
    sep = _c("dim", f"  {'─'*50}")

    lines = [
        _c("gold", f"\n{'='*60}"),
        _c("bold", f"  ॐ  {OPENING_PHRASE}"),
        _c("gold", f"{'='*60}\n"),
        heading,
        ch_name,
        sep,
    ]

    sanskrit = _clean_for_speech(result.get("sanskrit", ""))
    if sanskrit:
        lines.append(_c("cyan", f"\n  🕉  {sanskrit}\n"))
    translit = _clean_for_speech(result.get("transliteration", ""))
    if translit:
        lines.append(_c("dim", f"  🗣  {translit}\n"))
    if result["translation"]:
        lines.append(_c("green", f"  📖  Translation: {result['translation']}\n"))
    if result.get("purport") and detail == "full":
        lines.append(_c("purple", f"  💡  Explanation: {result['purport']}\n"))

    lines.append(sep)
    lines.append(_c("gold", "  🙏  Hare Krishna! May this wisdom guide your day.\n"))

    return "\n".join(lines)


def build_gita_voice_text(detail: str = "full") -> str:
    """Build the text for voice delivery (natural spoken language)."""
    result = get_daily_gita(detail=detail)
    lines = result.get("lines", [])

    if not lines:
        ch = result["chapter"]
        vs = result["verse"]
        lines = [f"Today's shloka is from chapter {ch}, verse {vs} of the Bhagavad Gita."]
        if result["translation"]:
            lines.append(result["translation"])

    prefix = f"Radhe Radhe! {OPENING_PHRASE}"
    suffix = "Hare Krishna! May Lord Krishna's wisdom guide you throughout your day."
    full = f"{prefix} {' '.join(lines)} {suffix}"
    return full


# ═══════════════════════════════════════════════════════════════════
# Quick info helpers
# ═══════════════════════════════════════════════════════════════════

def gita_stats() -> str:
    """Return current stats as a readable string."""
    state = _load_state()
    st = state["stats"]
    gs = state["gita"]
    bs = state["bhagavatam"]
    today = _today()

    gita_visited = len(gs.get("visited_verses", []))
    total_gita = sum(VERSES_PER_CHAPTER)
    gita_progress = f"{gita_visited}/{total_gita} verses seen"
    story_visited = len(bs.get("visited_indices", []))
    total_stories = len(BHAGAVATAM_STORIES)
    story_progress = f"{story_visited}/{total_stories} stories heard"

    msg = (
        f"Gita: {st.get('total_gita_told', 0)} total shlokas | "
        f"Bhagavatam: {st.get('total_stories_told', 0)} total stories | "
        f"Gita progress: {gita_progress} | "
        f"Story progress: {story_progress}"
    )
    return msg


def get_state() -> dict:
    """Return the full state dict (for debugging / display)."""
    return _load_state()


# ═══════════════════════════════════════════════════════════════════
# PDF reference (local source)
# ═══════════════════════════════════════════════════════════════════

PDF_SOURCE = os.environ.get(
    "AK_GITA_PDF",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "assets", "ignca_gita.pdf"),
)

def pdf_available() -> bool:
    """Check if the IGNCA PDF is available locally for offline use."""
    return os.path.isfile(PDF_SOURCE)


# ── Quick test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print(build_gita_delivery(detail="full"))
    print("\n" + "=" * 60)
    story = get_daily_bhagavatam_story()
    print(_format_story_for_console(story))
    print(gita_stats())
