"""OFFLINE Hindi <-> English translator for AK — all data lives on D drive.

Everything is local:
  • bilingual word/phrase dictionary  -> D:\\YOUR_DATA_PATH\\Translations\\en-hi.tsv
  • translation cache + history DB    -> D:\\YOUR_DATA_PATH\\Translations\\trans.db

Every phrase you translate is cached in SQLite, so repeating it later is
instant — and it continues to work with zero internet, forever.

Translation method (offline, no neural models):
  exact phrase match  -> cached/saved dictionary answer
  otherwise           -> word-by-word lookup in the bilingual dictionary
Numbers (digits are handled in both directions) and simple phrases work well.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import get_data_dir

_TSV_SIGNATURE = "# AK offline translation vocabulary v2"

# ────────────────────────────────────────────────────────────────
#  Built-in curated bilingual vocabulary (English <-> Hindi).
#  Written into en-hi.tsv on first run, so "memory" stays on D drive.
#  Format kept plain:  english<TAB>hindi
# ────────────────────────────────────────────────────────────────

_LEXICON: List[Tuple[str, str]] = [
    # ── greetings & polite phrases ──
    ("hello", "नमस्ते"), ("hi", "नमस्ते"), ("namaste", "नमस्ते"),
    ("namaskar", "नमस्कार"), ("good morning", "सुप्रभात"),
    ("good afternoon", "नमस्ते"), ("good evening", "शुभ संध्या"),
    ("good night", "शुभ रात्रि"), ("good bye", "अलविदा"),
    ("bye", "अलविदा"), ("welcome", "स्वागत है"), ("welcome", "स्वागत"),
    ("please", "कृपया"), ("thank you", "धन्यवाद"), ("thanks", "धन्यवाद"),
    ("thank you very much", "बहुत धन्यवाद"), ("sorry", "माफ़ कीजिए"),
    ("i am sorry", "मुझे माफ़ कीजिए"), ("excuse me", "माफ़ कीजिए"),
    ("no problem", "कोई बात नहीं"), ("well done", "शाबाश"),
    ("good luck", "शुभकामनाएँ"), ("congratulations", "बधाई हो"),
    ("how are you", "आप कैसे हैं"), ("how are you", "आप कैसे हो"),
    ("i am fine", "मैं ठीक हूँ"), ("i am good", "मैं ठीक हूँ"),
    ("fine", "ठीक"), ("good", "अच्छा"), ("very good", "बहुत अच्छा"),
    ("what is your name", "आपका नाम क्या है"),
    ("my name is", "मेरा नाम है"), ("your name", "आपका नाम"),
    ("nice to meet you", "आपसे मिलकर खुशी हुई"),
    ("see you", "फिर मिलेंगे"), ("see you tomorrow", "कल मिलेंगे"),
    ("talk to you later", "बाद में बात करते हैं"),
    ("come here", "यहाँ आओ"), ("go there", "वहाँ जाओ"),
    ("come in", "अंदर आओ"), ("sit down", "बैठ जाओ"),
    ("wait", "रुको"), ("listen", "सुनो"), ("listen to me", "मेरी बात सुनो"),
    ("be quiet", "चुप रहो"), ("stop", "रुक जाओ"),
    ("i love you", "मैं तुमसे प्यार करता हूँ"),
    ("i love you", "मैं आपसे प्यार करती हूँ"),
    ("miss you", "तुम्हारी याद आती है"),
    ("i miss you", "मुझे तुम्हारी याद आती है"),
    ("help me", "मेरी मदद करो"), ("i need help", "मुझे मदद चाहिए"),
    ("sos", "मदद"), ("help", "मदद"),
    ("call me", "मुझे फोन करो"), ("call the police", "पुलिस को बुलाओ"),
    ("where is the hospital", "अस्पताल कहाँ है"),
    ("where is the bathroom", "शौचालय कहाँ है"),
    ("how much", "कितना"), ("how much is this", "यह कितने का है"),
    ("what is this", "यह क्या है"), ("what happened", "क्या हुआ"),
    ("what do you want", "आप क्या चाहते हैं"),
    ("i want water", "मुझे पानी चाहिए"),
    ("i am hungry", "मुझे भूख लगी है"),
    ("i am thirsty", "मुझे प्यास लगी है"),
    ("i am tired", "मैं थक गया हूँ"),
    ("i am happy", "मैं खुश हूँ"), ("i am sad", "मैं दुखी हूँ"),
    ("i am scared", "मुझे डर लग रहा है"),
    ("are you ok", "क्या तुम ठीक हो"),
    ("no", "नहीं"), ("yes", "हाँ"), ("ok", "ठीक है"), ("okay", "ठीक है"),
    ("maybe", "शायद"), ("lets go", "चलो चलते हैं"), ("come on", "चलो भी"),
    ("all right", "ठीक है"), ("of course", "बिल्कुल"), ("sure", "ज़रूर"),
    ("definitely", "निश्चित रूप से"), ("absolutely", "बिल्कुल"),
    ("who", "कौन"), ("what", "क्या"), ("where", "कहाँ"),
    ("when", "कब"), ("why", "क्यों"), ("how", "कैसे"),
    ("which", "कौन सा"), ("whose", "किसका"), ("how many", "कितने"),
    ("how long", "कितना लंबा"), ("how far", "कितनी दूर"),
    # ── pronouns ──
    ("i", "मैं"), ("you", "आप"), ("you", "तुम"), ("he", "वह"),
    ("she", "वह"), ("it", "यह"), ("we", "हम"), ("they", "वे"),
    ("me", "मुझे"), ("him", "उसे"), ("her", "उसे"), ("us", "हमें"),
    ("them", "उन्हें"), ("my", "मेरा"), ("mine", "मेरा"),
    ("your", "आपका"), ("your", "तुम्हारा"), ("his", "उसका"),
    ("her", "उसकी"), ("its", "इसका"), ("our", "हमारा"),
    ("their", "उनका"), ("myself", "मैं खुद"), ("yourself", "आप खुद"),
    ("everyone", "सब लोग"), ("somebody", "कोई"), ("some", "कुछ"),
    ("anyone", "कोई भी"), ("nobody", "कोई नहीं"), ("all", "सब"),
    ("everything", "सब कुछ"), ("nothing", "कुछ नहीं"), ("something", "कुछ"),
    ("anything", "कुछ भी"), ("today", "आज"), ("tomorrow", "कल"),
    ("yesterday", "कल"), ("now", "अभी"), ("later", "बाद में"),
    # ── numbers ──
    ("zero", "शून्य"), ("one", "एक"), ("two", "दो"), ("three", "तीन"),
    ("four", "चार"), ("five", "पाँच"), ("six", "छह"), ("seven", "सात"),
    ("eight", "आठ"), ("nine", "नौ"), ("ten", "दस"), ("eleven", "ग्यारह"),
    ("twelve", "बारह"), ("thirteen", "तेरह"), ("fourteen", "चौदह"),
    ("fifteen", "पंद्रह"), ("sixteen", "सोलह"), ("seventeen", "सत्रह"),
    ("eighteen", "अठारह"), ("nineteen", "उन्नीस"), ("twenty", "बीस"),
    ("thirty", "तीस"), ("forty", "चालीस"), ("fifty", "पचास"),
    ("sixty", "साठ"), ("seventy", "सत्तर"), ("eighty", "अस्सी"),
    ("ninety", "नब्बे"), ("hundred", "सौ"), ("thousand", "हज़ार"),
    ("lakh", "लाख"), ("crore", "करोड़"), ("first", "पहला"),
    ("second", "दूसरा"), ("third", "तीसरा"), ("last", "आख़िरी"),
    # ── days & time ──
    ("monday", "सोमवार"), ("tuesday", "मंगलवार"), ("wednesday", "बुधवार"),
    ("thursday", "गुरुवार"), ("friday", "शुक्रवार"), ("saturday", "शनिवार"),
    ("sunday", "रविवार"), ("week", "सप्ताह"), ("month", "महीना"),
    ("year", "साल"), ("day", "दिन"), ("night", "रात"), ("morning", "सुबह"),
    ("evening", "शाम"), ("afternoon", "दोपहर"), ("noon", "दोपहर"),
    ("midnight", "आधी रात"), ("time", "समय"), ("hour", "घंटा"),
    ("minute", "मिनट"), ("second", "सेकंड"), ("clock", "घड़ी"),
    ("o'clock", "बजे"), ("calendar", "कैलेंडर"),
    ("january", "जनवरी"), ("february", "फ़रवरी"), ("march", "मार्च"),
    ("april", "अप्रैल"), ("may", "मई"), ("june", "जून"), ("july", "जुलाई"),
    ("august", "अगस्त"), ("september", "सितंबर"), ("october", "अक्टूबर"),
    ("november", "नवंबर"), ("december", "दिसंबर"),
    # ── family ──
    ("family", "परिवार"), ("mother", "माँ"), ("father", "पिता"),
    ("parents", "माता-पिता"), ("brother", "भाई"), ("sister", "बहन"),
    ("son", "बेटा"), ("daughter", "बेटी"), ("child", "बच्चा"),
    ("children", "बच्चे"), ("baby", "बच्चा"), ("boy", "लड़का"),
    ("girl", "लड़की"), ("man", "आदमी"), ("woman", "औरत"),
    ("husband", "पति"), ("wife", "पत्नी"), ("grandfather", "दादा"),
    ("grandmother", "दादी"), ("uncle", "चाचा"), ("aunt", "चाची"),
    ("cousin", "चचेरा भाई"), ("friend", "दोस्त"), ("friends", "दोस्त"),
    ("neighbour", "पड़ोसी"), ("person", "व्यक्ति"), ("people", "लोग"),
    ("groom", "दूल्हा"), ("bride", "दुल्हन"),
    # ── body ──
    ("body", "शरीर"), ("head", "सिर"), ("face", "चेहरा"), ("eye", "आँख"),
    ("eyes", "आँखें"), ("ear", "कान"), ("ears", "कान"), ("nose", "नाक"),
    ("mouth", "मुँह"), ("tongue", "जीभ"), ("tooth", "दाँत"),
    ("teeth", "दाँत"), ("lips", "होंठ"), ("hair", "बाल"), ("neck", "गर्दन"),
    ("shoulder", "कंधा"), ("arm", "बाज़ू"), ("hand", "हाथ"),
    ("hands", "हाथ"), ("finger", "उंगली"), ("leg", "पैर"), ("legs", "पैर"),
    ("foot", "पैर"), ("feet", "पैर"), ("knee", "घुटना"), ("chest", "छाती"),
    ("stomach", "पेट"), ("heart", "दिल"), ("brain", "दिमाग़"),
    ("blood", "खून"), ("skin", "त्वचा"), ("bone", "हड्डी"),
    ("health", "स्वास्थ्य"), ("ill", "बीमार"), ("sick", "बीमार"),
    ("pain", "दर्द"), ("headache", "सिरदर्द"), ("fever", "बुखार"),
    ("cough", "खाँसी"), ("cold", "सर्दी"), ("medicine", "दवा"),
    ("doctor", "डॉक्टर"), ("nurse", "नर्स"), ("hospital", "अस्पताल"),
    ("hospital", "अस्पताल"), ("patient", "मरीज़"),
    # ── food ──
    ("food", "खाना"), ("water", "पानी"), ("milk", "दूध"), ("tea", "चाय"),
    ("coffee", "कॉफ़ी"), ("juice", "रस"), ("bread", "रोटी"), ("rice", "चावल"),
    ("chapati", "रोटी"), ("roti", "रोटी"), ("dal", "दाल"), ("dal", "दाल"),
    ("vegetable", "सब्ज़ी"), ("vegetables", "सब्ज़ियाँ"), ("fruit", "फल"),
    ("fruits", "फल"), ("mango", "आम"), ("apple", "सेब"), ("banana", "केला"),
    ("grape", "अंगूर"), ("orange", "संतरा"), ("lemon", "नींबू"),
    ("onion", "प्याज़"), ("potato", "आलू"), ("tomato", "टमाटर"),
    ("salt", "नमक"), ("sugar", "चीनी"), ("oil", "तेल"), ("ghee", "घी"),
    ("butter", "मक्खन"), ("cheese", "पनीर"), ("egg", "अंडा"),
    ("chicken", "मुर्ग़ा"), ("meat", "मांस"), ("fish", "मछली"),
    ("soup", "सूप"), ("sweet", "मीठा"), ("sweets", "मिठाई"),
    ("spicy", "तीखा"), ("breakfast", "नाश्ता"), ("lunch", "दोपहर का खाना"),
    ("dinner", "रात का खाना"), ("meal", "भोजन"), ("hungry", "भूखा"),
    ("thirsty", "प्यासा"), ("full", "भरा हुआ"), ("fish", "मछली"),
    ("cuisine", "पाक कला"), ("restaurant", "रेस्तरां"),
    # ── animals & nature ──
    ("animal", "जानवर"), ("animals", "जानवर"), ("dog", "कुत्ता"),
    ("cat", "बिल्ली"), ("cow", "गाय"), ("buffalo", "भैंस"), ("horse", "घोड़ा"),
    ("elephant", "हाथी"), ("lion", "शेर"), ("tiger", "बाघ"), ("monkey", "बंदर"),
    ("goat", "बकरी"), ("sheep", "भेड़"), ("hen", "मुर्गी"), ("bird", "पक्षी"),
    ("birds", "पक्षी"), ("crow", "कौआ"), ("peacock", "मोर"), ("snake", "साँप"),
    ("rat", "चूहा"), ("insect", "कीड़ा"), ("bees", "मधुमक्खियाँ"),
    ("nature", "प्रकृति"), ("earth", "पृथ्वी"), ("sky", "आकाश"),
    ("sun", "सूरज"), ("moon", "चाँद"), ("star", "तारा"), ("stars", "तारे"),
    ("rain", "बारिश"), ("cloud", "बादल"), ("clouds", "बादल"), ("wind", "हवा"),
    ("storm", "तूफ़ान"), ("thunder", "गड़गड़ाहट"), ("fire", "आग"),
    ("water", "पानी"), ("river", "नदी"), ("sea", "समुद्र"), ("ocean", "महासागर"),
    ("lake", "झील"), ("mountain", "पहाड़"), ("hill", "पहाड़ी"),
    ("forest", "जंगल"), ("tree", "पेड़"), ("trees", "पेड़"), ("flower", "फूल"),
    ("flowers", "फूल"), ("grass", "घास"), ("field", "खेत"), ("land", "ज़मीन"),
    ("soil", "मिट्टी"), ("stone", "पत्थर"), ("sand", "रेत"), ("weather", "मौसम"),
    ("climate", "जलवायु"), ("hot", "गरम"), ("cold", "ठंडा"), ("warm", "गरम"),
    ("cool", "ठंडा"), ("sunny", "धूप"), ("hot", "गर्म"),
    # ── home ──
    ("house", "घर"), ("home", "घर"), ("room", "कमरा"), ("kitchen", "रसोईघर"),
    ("bathroom", "शौचालय"), ("bedroom", "शयनकक्ष"), ("door", "दरवाज़ा"),
    ("window", "खिड़की"), ("wall", "दीवार"), ("roof", "छत"), ("floor", "फ़र्श"),
    ("bed", "बिस्तर"), ("chair", "कुर्सी"), ("table", "मेज़"), ("sofa", "सोफ़ा"),
    ("lamp", "दीपक"), ("light", "रोशनी"), ("candle", "मोमबत्ती"),
    ("clean", "साफ़"), ("dirty", "गंदा"), ("tidy", "सुव्यवस्थित"),
    ("garden", "बगीचा"), ("yard", "आँगन"), ("balcony", "बालकनी"),
    ("lock", "ताला"), ("key", "चाबी"), ("mirror", "दर्पण"),
    ("clothes", "कपड़े"), ("shirt", "कमीज़"), ("pant", "पतलून"),
    ("saree", "साड़ी"), ("shoe", "जूता"), ("shoes", "जूते"), ("hat", "टोपी"),
    ("towel", "तौलिया"), ("soap", "साबुन"), ("bucket", "बाल्टी"),
    # ── city & travel ──
    ("city", "शहर"), ("village", "गाँव"), ("town", "कस्बा"), ("country", "देश"),
    ("road", "सड़क"), ("street", "गली"), ("bridge", "पुल"), ("market", "बाज़ार"),
    ("shop", "दुकान"), ("store", "दुकान"), ("hotel", "होटल"), ("school", "स्कूल"),
    ("school", "विद्यालय"), ("college", "कॉलेज"), ("university", "विश्वविद्यालय"),
    ("office", "कार्यालय"), ("bank", "बैंक"), ("post office", "डाकघर"),
    ("station", "स्टेशन"), ("airport", "हवाई अड्डा"), ("bus", "बस"),
    ("bus stop", "बस स्टॉप"), ("car", "कार"), ("car", "गाड़ी"),
    ("taxi", "टैक्सी"), ("rickshaw", "रिक्शा"), ("train", "रेलगाड़ी"),
    ("train", "ट्रेन"), ("cycle", "साइकिल"), ("bike", "बाइक"), ("motorcycle", "मोटरसाइकिल"),
    ("truck", "ट्रक"), ("plane", "हवाई जहाज़"), ("ship", "जहाज़"),
    ("railway", "रेलवे"), ("ticket", "टिकट"), ("driver", "ड्राइवर"),
    ("travel", "यात्रा"), ("trip", "यात्रा"), ("journey", "सफ़र"),
    ("map", "नक्शा"), ("direction", "दिशा"), ("left", "बाएँ"), ("right", "दाएँ"),
    ("straight", "सीधा"), ("near", "पास"), ("far", "दूर"), ("distance", "दूरी"),
    ("here", "यहाँ"), ("there", "वहाँ"), ("park", "पार्क"), ("temple", "मंदिर"),
    ("mosque", "मस्जिद"), ("church", "गिरजाघर"), ("police", "पुलिस"),
    ("fire brigade", "दमकल"), ("ambulance", "एम्बुलेंस"), ("flag", "झंडा"),
    # ── common verbs ──
    ("go", "जाओ"), ("come", "आओ"), ("eat", "खाओ"), ("drink", "पियो"),
    ("sleep", "सोओ"), ("wake", "जागो"), ("run", "दौड़ो"), ("walk", "चलो"),
    ("sit", "बैठो"), ("stand", "खड़े हो जाओ"), ("speak", "बोलो"),
    ("talk", "बात करो"), ("say", "कहो"), ("tell", "बताओ"), ("ask", "पूछो"),
    ("answer", "जवाब दो"), ("read", "पढ़ो"), ("write", "लिखो"),
    ("write", "लिखना"), ("see", "देखो"), ("look", "देखो"), ("watch", "देखो"),
    ("hear", "सुनो"), ("buy", "ख़रीदो"), ("sell", "बेचो"), ("give", "दो"),
    ("take", "लो"), ("get", "पाओ"), ("make", "बनाओ"), ("do", "करो"),
    ("play", "खेलो"), ("work", "काम करो"), ("study", "पढ़ाई करो"),
    ("learn", "सीखो"), ("teach", "सिखाओ"), ("understand", "समझो"),
    ("know", "जानना"), ("think", "सोचो"), ("remember", "याद रखो"),
    ("forget", "भूल जाओ"), ("help", "मदद करो"), ("love", "प्यार"),
    ("like", "पसंद"), ("want", "चाहिए"), ("need", "चाहिए"),
    ("open", "खोलो"), ("close", "बंद करो"), ("start", "शुरू करो"),
    ("finish", "ख़त्म करो"), ("stop", "रोको"), ("begin", "शुरू करो"),
    ("call", "बुलाओ"), ("call", "फोन करो"), ("meet", "मिलो"),
    ("send", "भेजो"), ("bring", "लाओ"), ("put", "रखो"), ("keep", "रखो"),
    ("find", "ढूँढो"), ("search", "खोजो"), ("win", "जीतो"), ("lose", "हारो"),
    ("fight", "लड़ो"), ("sing", "गाओ"), ("dance", "नाचो"), ("cook", "पकाओ"),
    ("clean", "साफ़ करो"), ("wash", "धोओ"), ("wear", "पहनो"),
    ("swim", "तैरो"), ("drive", "चलाओ"), ("fly", "उड़ो"),
    ("live", "रहो"), ("stay", "रहो"), ("feel", "महसूस करो"),
    ("smile", "मुस्कुराओ"), ("cry", "रोओ"), ("laugh", "हँसो"),
    ("share", "बाँटो"), ("care", "परवाह करना"), ("hope", "आशा"),
    ("believe", "विश्वास करो"), ("wait", "इंतज़ार करो"),
    ("arrive", "पहुँचो"), ("leave", "छोड़ो"), ("return", "लौटो"),
    ("change", "बदलो"), ("repair", "मरम्मत करो"), ("build", "बनाओ"),
    ("break", "तोड़ो"), ("drop", "गिराओ"), ("catch", "पकड़ो"),
    ("throw", "फेंको"), ("choose", "चुनो"), ("try", "कोशिश करो"),
    ("practice", "अभ्यास करो"), ("rest", "आराम करो"),
    # ── connectors & quantity words ──
    ("many", "बहुत"), ("few", "कुछ"), ("more", "और"), ("less", "कम"),
    ("and", "और"), ("or", "या"), ("alot", "बहुत"), ("a lot", "बहुत"), ("very", "बहुत"), ("too", "भी"),
    ("also", "भी"), ("but", "लेकिन"), ("because", "क्योंकि"),
    ("if", "अगर"), ("then", "फिर"), ("only", "सिर्फ़"), ("again", "फिर"),
    ("never", "कभी नहीं"), ("always", "हमेशा"), ("often", "अक्सर"),
    ("sometimes", "कभी-कभी"), ("together", "साथ"), ("alone", "अकेला"),
    ("fast", "तेज़ी से"), ("slowly", "धीरे-धीरे"), ("quickly", "जल्दी से"),
    ("very much", "बहुत ज़्यादा"), ("a little", "थोड़ा सा"), ("little", "थोड़ा"),
    # ── common adjectives ──
    ("beautiful", "सुंदर"), ("pretty", "सुंदर"), ("handsome", "सुंदर"),
    ("ugly", "बदसूरत"), ("nice", "अच्छा"), ("bad", "बुरा"), ("evil", "बुरा"),
    ("big", "बड़ा"), ("small", "छोटा"), ("large", "बड़ा"), ("little", "थोड़ा"),
    ("old", "बूढ़ा"), ("young", "जवान"), ("new", "नया"), ("high", "ऊँचा"),
    ("low", "नीचा"), ("long", "लंबा"), ("short", "छोटा"), ("tall", "लंबा"),
    ("fast", "तेज़"), ("slow", "धीमा"), ("quick", "जल्दी"), ("early", "जल्दी"),
    ("late", "देर"), ("heavy", "भारी"), ("light", "हल्का"), ("soft", "नरम"),
    ("hard", "कठिन"), ("easy", "आसान"), ("difficult", "मुश्किल"),
    ("smart", "चालाक"), ("intelligent", "बुद्धिमान"), ("clever", "चतुर"),
    ("stupid", "बेवकूफ़"), ("kind", "दयालु"), ("cruel", "क्रूर"),
    ("brave", "बहादुर"), ("coward", "कायर"), ("strong", "मजबूत"),
    ("weak", "कमज़ोर"), ("healthy", "स्वस्थ"), ("sick", "बीमार"),
    ("rich", "अमीर"), ("poor", "गरीब"), ("happy", "खुश"), ("sad", "दुखी"),
    ("angry", "गुस्से में"), ("scared", "डरा हुआ"), ("afraid", "डरा हुआ"),
    ("tired", "थका हुआ"), ("hungry", "भूखा"), ("thirsty", "प्यासा"),
    ("busy", "व्यस्त"), ("free", "आज़ाद"), ("free", "खाली"), ("important", "महत्वपूर्ण"),
    ("famous", "प्रसिद्ध"), ("special", "विशेष"), ("common", "सामान्य"),
    ("usual", "सामान्य"), ("strange", "अजीब"), ("funny", "मज़ाकिया"),
    ("crazy", "पागल"), ("true", "सच"), ("false", "झूठा"), ("real", "असली"),
    ("fake", "नकली"), ("same", "समान"), ("different", "अलग"), ("equal", "बराबर"),
    ("right", "सही"), ("wrong", "गलत"), ("correct", "सही"), ("sure", "पक्का"),
    ("sure", "यक़ीन"), ("certain", "निश्चित"), ("possible", "संभव"),
    ("impossible", "असंभव"), ("ready", "तैयार"), ("sure", "निश्चित"),
    ("open", "खुला"), ("closed", "बंद"), ("full", "भरा"), ("empty", "खाली"),
    ("white", "सफ़ेद"), ("black", "काला"), ("red", "लाल"), ("green", "हरा"),
    ("blue", "नीला"), ("yellow", "पीला"), ("orange", "नारंगी"),
    ("purple", "बैंगनी"), ("pink", "गुलाबी"), ("brown", "भूरा"),
    ("grey", "भूरा"), ("color", "रंग"), ("colour", "रंग"),
    ("dark", "अंधेरा"), ("bright", "तेज़"), ("pretty", "सुंदर"),
    # ── common nouns ──
    ("name", "नाम"), ("place", "जगह"), ("thing", "चीज़"), ("things", "चीज़ें"),
    ("world", "दुनिया"), ("india", "भारत"), ("bharat", "भारत"),
    ("country", "देश"), ("state", "राज्य"), ("capital", "राजधानी"),
    ("language", "भाषा"), ("hindi", "हिंदी"), ("english", "अंग्रेज़ी"),
    ("word", "शब्द"), ("words", "शब्द"), ("sentence", "वाक्य"),
    ("letter", "अक्षर"), ("story", "कहानी"), ("movies", "फिल्में"),
    ("movie", "फिल्म"), ("film", "फिल्म"), ("song", "गाना"), ("songs", "गाने"),
    ("music", "संगीत"), ("dance", "नृत्य"), ("game", "खेल"),
    ("work", "काम"), ("job", "नौकरी"), ("business", "व्यापार"),
    ("money", "पैसा"), ("price", "कीमत"), ("cost", "क़ीमत"), ("rate", "दर"),
    ("discount", "छूट"), ("bill", "बिल"), ("credit card", "क्रेडिट कार्ड"),
    ("cash", "नकद"), ("change", "छुट्टा"), ("gift", "उपहार"),
    ("question", "सवाल"), ("answer", "जवाब"), ("idea", "विचार"),
    ("plan", "योजना"), ("dream", "सपना"), ("wish", "इच्छा"),
    ("news", "समाचार"), ("newspaper", "अख़बार"), ("book", "किताब"),
    ("books", "किताबें"), ("pen", "कलम"), ("paper", "कागज़"),
    ("page", "पृष्ठ"), ("notebook", "कॉपी"), ("bag", "थैला"),
    ("bag", "बैग"), ("umbrella", "छाता"), ("stick", "लाठी"), ("rope", "रस्सी"),
    ("phone", "फ़ोन"), ("mobile", "मोबाइल"), ("computer", "कंप्यूटर"),
    ("laptop", "लैपटॉप"), ("internet", "इंटरनेट"), ("email", "ईमेल"),
    ("message", "संदेश"), ("photo", "तस्वीर"), ("camera", "कैमरा"),
    ("video", "वीडियो"), ("tv", "टीवी"), ("television", "टेलीविज़न"),
    ("radio", "रेडियो"), ("electricity", "बिजली"), ("battery", "बैटरी"),
    ("water", "पानी"), ("air", "हवा"), ("gas", "गैस"), ("petrol", "पेट्रोल"),
    ("fuel", "ईंधन"), ("kitchen", "रसोई"), ("garden", "बगीचा"),
    ("market", "बाज़ार"), ("river", "नदी"), ("village", "गाँव"),
    ("festival", "त्योहार"), ("diwali", "दिवाली"), ("holi", "होली"),
    ("eid", "ईद"), ("new year", "नया साल"), ("birthday", "जन्मदिन"),
    ("marriage", "शादी"), ("wedding", "शादी"), ("party", "पार्टी"),
    ("celebration", "जश्न"), ("cricket", "क्रिकेट"), ("bollywood", "बॉलीवुड"),
    ("yoga", "योग"), ("ayurveda", "आयुर्वेद"),
    ("god", "भगवान"), ("temple", "मंदिर"), ("religion", "धर्म"),
    ("hindu", "हिंदू"), ("muslim", "मुस्लिम"), ("sikh", "सिख"),
    ("belief", "विश्वास"), ("soul", "आत्मा"), ("peace", "शांति"),
    ("power", "शक्ति"), ("strength", "ताक़त"), ("love", "प्यार"),
    ("hate", "नफ़रत"), ("truth", "सच्चाई"), ("lie", "झूठ"),
    ("respect", "सम्मान"), ("pride", "गर्व"), ("trust", "भरोसा"),
    ("duty", "कर्तव्य"), ("right", "अधिकार"), ("freedom", "आज़ादी"),
    ("country", "देश"), ("nation", "राष्ट्र"), ("government", "सरकार"),
    ("leader", "नेता"), ("soldier", "सैनिक"), ("king", "राजा"),
    ("queen", "रानी"), ("prince", "राजकुमार"), ("princess", "राजकुमारी"),
    ("hero", "नायक"), ("heroine", "नायिका"), ("god", "देवता"),
    ("school", "स्कूल"), ("teacher", "शिक्षक"), ("student", "छात्र"),
    ("college", "कॉलेज"), ("subject", "विषय"), ("exam", "परीक्षा"),
    ("result", "परिणाम"), ("marks", "अंक"), ("degree", "डिग्री"),
    ("knowledge", "ज्ञान"), ("wisdom", "ज्ञान"), ("skill", "कौशल"),
    ("science", "विज्ञान"), ("mathematics", "गणित"), ("history", "इतिहास"),
    ("geography", "भूगोल"), ("art", "कला"), ("culture", "संस्कृति"),
    ("society", "समाज"), ("politics", "राजनीति"), ("economy", "अर्थव्यवस्था"),
    ("family", "खानदान"), ("home", "घर"), ("life", "ज़िंदगी"),
    ("death", "मौत"), ("luck", "किस्मत"), ("destiny", "भाग्य"),
    ("time", "वक्त"), ("age", "उम्र"), ("youth", "जवानी"),
    ("childhood", "बचपन"), ("future", "भविष्य"), ("past", "अतीत"),
    ("present", "वर्तमान"), ("moment", "पल"), ("year", "वर्ष"),
    ("season", "मौसम"), ("spring", "वसंत"), ("monsoon", "मानसून"),
    ("winter", "सर्दी"), ("summer", "गर्मी"), ("autumn", "पतझड़"),
    ("rainy", "बरसाती"), ("temperature", "तापमान"), ("degree", "डिग्री"),
    ("percent", "प्रतिशत"), ("number", "संख्या"), ("numbers", "संख्याएँ"),
    ("distance", "दूरी"), ("speed", "गति"), ("weight", "वज़न"),
    ("measure", "माप"), ("size", "आकार"), ("shape", "आकृति"),
    ("colour", "रंग"), ("sound", "आवाज़"), ("noise", "शोर"),
    ("noise", "शोर"), ("smell", "गंध"), ("taste", "स्वाद"),
    ("touch", "स्पर्श"), ("sight", "दृष्टि"), ("smell", "गंध"),
    ("feeling", "भावना"), ("emotion", "भावना"), ("anger", "गुस्सा"),
    ("joy", "खुशी"), ("happiness", "खुशी"), ("sorrow", "दुख"),
    ("fear", "डर"), ("courage", "साहस"), ("effort", "प्रयास"),
    ("goal", "लक्ष्य"), ("success", "सफलता"), ("failure", "असफलता"),
    ("problem", "समस्या"), ("solution", "समाधान"), ("reason", "कारण"),
    ("cause", "वजह"), ("effect", "प्रभाव"), ("chance", "मौक़ा"),
    ("opportunity", "अवसर"), ("luck", "किस्मत"), ("risk", "जोखिम"),
    ("danger", "ख़तरा"), ("safety", "सुरक्षा"), ("law", "कानून"),
    ("rule", "नियम"), ("rules", "नियम"), ("order", "आदेश"),
    ("request", "अनुरोध"), ("gift", "तोहफ़ा"), ("present", "तोहफ़ा"),
]

# Devanagari digits <-> ASCII
_HI_DIGITS = {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
              "५": "5", "६": "6", "७": "7", "८": "8", "९": "9"}
_EN_DIGITS = {v: k for k, v in _HI_DIGITS.items()}

# tiny stop/hint words to drop in word-by-word mode
_EN_STOP = {"a", "an", "the", "am", "is", "are", "was", "were",
            "to", "of", "and", "or", "but", "for", "on", "in",
            "with", "at", "by", "from", "be", "been", "have",
            "has", "had", "do", "does", "did", "this", "that",
            "these", "those", "there", "it", "its"}


class Translator:
    """Offline Hindi<->English translator with a cache DB on the D drive."""

    def __init__(self):
        root = os.path.join(get_data_dir(), "Translations")
        os.makedirs(root, exist_ok=True)
        self.data_path = root
        self.db_path = os.path.join(root, "trans.db")
        self.tsv_path = os.path.join(root, "en-hi.tsv")
        self._en_hi: Dict[str, str] = {}
        self._hi_en: Dict[str, str] = {}
        self._init_db()
        self._load_dicts()
        self._write_tsv()

    # ── storage ──────────────────────────────────────────────────
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                " src TEXT NOT NULL, tgt TEXT NOT NULL, source TEXT NOT NULL,"
                " result TEXT NOT NULL, ts TEXT NOT NULL,"
                " UNIQUE(src, tgt, source))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS history ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " src TEXT NOT NULL, tgt TEXT NOT NULL, result TEXT NOT NULL,"
                " ts TEXT NOT NULL)"
            )

    def _write_tsv(self):
        """Persist the built-in vocabulary to the D drive so the data
        ('memory') lives there and can be extended by hand.  If the file
        is missing, or has an outdated signature, it is regenerated
        (the old copy is kept as .bak)."""
        need = (not os.path.exists(self.tsv_path)
                or (os.path.exists(self.tsv_path)
                    and _TSV_SIGNATURE not in open(self.tsv_path, encoding="utf-8").read(200)))
        if not need:
            return
        if os.path.exists(self.tsv_path):
            try:
                os.replace(self.tsv_path, self.tsv_path + ".bak")
            except Exception:
                pass
        lines = ["\t".join(pair) for pair in _LEXICON]
        with open(self.tsv_path, "w", encoding="utf-8") as fh:
            fh.write(_TSV_SIGNATURE + "\n")
            fh.write("# english<TAB>hindi — offline AK translation vocabulary\n")
            fh.write("\n".join(lines))

    def _add(self, en: str, hi: str):
        """Store a pair. For Hindi->English, prefer the shorter English gloss
        (so 'नमस्ते' -> 'namaste', not 'good afternoon')."""
        self._en_hi[en] = hi
        prev = self._hi_en.get(hi)
        if prev is None or len(en.split()) < len(prev.split()):
            self._hi_en[hi] = en

    def _load_dicts(self):
        try:
            with open(self.tsv_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "\t" not in line:
                        continue
                    en, hi = line.split("\t", 1)
                    en, hi = en.strip().lower(), hi.strip()
                    if not en or not hi:
                        continue
                    self._add(en, hi)
        except Exception:
            pass
        # fall back to the embedded lexicon if file load failed
        if not self._en_hi:
            for en, hi in _LEXICON:
                self._add(en.lower(), hi)

    # ── translation helpers ──────────────────────────────────────
    @staticmethod
    def _cache_key(text: str) -> str:
        return " ".join(text.split()).lower()

    def _text_patch(self, t: str) -> str:
        """Translate Devanagari digits to ASCII so both scripts work."""
        return "".join(_HI_DIGITS.get(ch, ch) for ch in t)

    def _num_to_hindi(self, value: int) -> str:
        ones = ["", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
                "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह",
                "सत्रह", "अठारह", "उन्नीस"]
        tens = ["", "", "बीस", "तीस", "चालीस", "पचास", "साठ", "सत्तर",
                "अस्सी", "नब्बे"]
        if value == 0:
            return "शून्य"
        if value < 20:
            return ones[value]
        if value < 100:
            return tens[value // 10] + (" " + ones[value % 10] if value % 10 else "")
        return str(value)

    _HI_NUM_WORDS = {"शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4,
                     "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9,
                     "दस": 10}

    def is_hindi(self, text: str) -> bool:
        return bool(re.search(r"[\u0900-\u097F]", text))

    # ── core translation ─────────────────────────────────────────
    def translate(self, text: str, target: str) -> Tuple[str, dict]:
        """Translate `text` to 'hindi' or 'english', fully offline.

        Returns (translated_text, info) where info may contain a cached flag.
        """
        text = text.strip()
        if not text:
            return "Please tell me something to translate.", {}
        tgt = target.lower()
        if tgt not in ("hindi", "english"):
            return ("I only translate between hindi and english locally. "
                    "Say: translate 'text' to hindi / to english.", {})

        src_lang = "english" if tgt == "hindi" else "hindi"
        if not self.is_hindi(text) and tgt == "hindi":
            src_lang = "english"
        elif self.is_hindi(text) and tgt == "english":
            src_lang = "hindi"

        ck = self._cache_key(text)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result, source FROM cache WHERE src=? AND tgt=? AND source=?",
                (ck, tgt, "offline"),
            ).fetchone()
        if row:
            return row[0], {"cached": True}

        result = self._translate_offline(text, tgt, src_lang)
        ts = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (src, tgt, source, result, ts) "
                "VALUES (?, ?, 'offline', ?, ?)",
                (ck, tgt, result, ts),
            )
            conn.execute(
                "INSERT INTO history (src, tgt, result, ts) VALUES (?, ?, ?, ?)",
                (text, tgt, result, ts),
            )
        return result, {"cached": False}

    def _translate_offline(self, text: str, tgt: str, src_lang: str) -> str:
        text = self._text_patch(text).strip()
        if not text:
            return ""
        # exact phrase from dictionary
        key = self._cache_key(text)
        if tgt == "hindi" and key in self._en_hi:
            return self._en_hi[key]
        if tgt == "english" and key in self._hi_en:
            return self._hi_en[key]

        if src_lang == "english":
            return self._en_to_hi(text)
        return self._hi_to_en(text)

    def _en_to_hi(self, text: str) -> str:
        toks = text.split()
        phrases = sorted((k for k in self._en_hi if " " in k),
                         key=lambda k: k.count(" "), reverse=True)
        out: List[str] = []
        i = 0
        while i < len(toks):
            matched = False
            for k in phrases:
                n = len(k.split())
                if i + n <= len(toks) and " ".join(toks[i:i + n]).lower() == k:
                    out.append(self._en_hi[k])
                    i += n
                    matched = True
                    break
            if matched:
                continue
            w = toks[i].lower()
            if w.isdigit():
                out.append(self._num_to_hindi(int(w)))
                i += 1
                continue
            mapped = self._en_hi.get(w)
            if mapped:
                out.append(mapped)
            elif w in _EN_STOP:
                pass
            else:
                out.append(toks[i])  # unknown — keep as-is
            i += 1
        return " ".join(out).strip() or "कोई शब्द नहीं मिले"

    def _hi_to_en(self, text: str) -> str:
        toks = text.split()
        phrases = sorted((k for k in self._hi_en if " " in k),
                         key=lambda k: k.count(" "), reverse=True)
        out: List[str] = []
        i = 0
        while i < len(toks):
            matched = False
            for k in phrases:
                n = len(k.split())
                if i + n <= len(toks) and " ".join(toks[i:i + n]) == k:
                    out.append(self._hi_en[k])
                    i += n
                    matched = True
                    break
            if matched:
                continue
            w = toks[i].lower()
            if w in _HI_NUM_WORDS:
                out.append(str(_HI_NUM_WORDS[w]))
            elif w in self._hi_en:
                out.append(self._hi_en[w])
            elif "".join(ch for ch in w if ch in "०१२३४५६७८९"):
                out.append(w)  # digit-ish unknown
            else:
                out.append(w)
            i += 1
        return " ".join(out).strip()

    # ── info ─────────────────────────────────────────────────────
    def count(self) -> int:
        return len(self._en_hi)

    def history(self, limit: int = 6) -> List[Tuple[str, str, str, str]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT src, tgt, result, ts FROM history ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return list(rows)
        except Exception:
            return []