"""
locale_detector.py — Fast, regex-based document locale/country detector.

Detects locale from raw document text using currency symbols, phone patterns,
date formats, postal codes, state abbreviations, and country/city names.

Returns a dict:
    {
        "locale":     "en-US",   # BCP47 tag
        "country":    "US",      # ISO 3166
        "date_order": "MDY",     # MDY | DMY | YMD
        "currency":   "USD",     # ISO 4217
        "confidence": 0.9        # 0.0 – 1.0
    }
"""

from __future__ import annotations

import re
from typing import Dict, Any

# ──────────────────────────────────────────────────────────────────────────────
# Helper tables
# ──────────────────────────────────────────────────────────────────────────────

_COUNTRY_META: Dict[str, Dict[str, str]] = {
    # country_code → {locale, date_order, currency}
    "US": {"locale": "en-US", "date_order": "MDY", "currency": "USD"},
    "CA": {"locale": "en-CA", "date_order": "DMY", "currency": "CAD"},
    "AU": {"locale": "en-AU", "date_order": "DMY", "currency": "AUD"},
    "GB": {"locale": "en-GB", "date_order": "DMY", "currency": "GBP"},
    "FR": {"locale": "fr-FR", "date_order": "DMY", "currency": "EUR"},
    "DE": {"locale": "de-DE", "date_order": "DMY", "currency": "EUR"},
    "IT": {"locale": "it-IT", "date_order": "DMY", "currency": "EUR"},
    "ES": {"locale": "es-ES", "date_order": "DMY", "currency": "EUR"},
    "PT": {"locale": "pt-PT", "date_order": "DMY", "currency": "EUR"},
    "NL": {"locale": "nl-NL", "date_order": "DMY", "currency": "EUR"},
    "EU": {"locale": "en-EU", "date_order": "DMY", "currency": "EUR"},
    "JP": {"locale": "ja-JP", "date_order": "YMD", "currency": "JPY"},
    "CN": {"locale": "zh-CN", "date_order": "YMD", "currency": "CNY"},
    "KR": {"locale": "ko-KR", "date_order": "YMD", "currency": "KRW"},
    "IN": {"locale": "en-IN", "date_order": "DMY", "currency": "INR"},
    "BR": {"locale": "pt-BR", "date_order": "DMY", "currency": "BRL"},
    "RU": {"locale": "ru-RU", "date_order": "DMY", "currency": "RUB"},
    "TR": {"locale": "tr-TR", "date_order": "DMY", "currency": "TRY"},
    "ID": {"locale": "id-ID", "date_order": "DMY", "currency": "IDR"},
    "SE": {"locale": "sv-SE", "date_order": "YMD", "currency": "SEK"},
    "NO": {"locale": "nb-NO", "date_order": "DMY", "currency": "NOK"},
    "DK": {"locale": "da-DK", "date_order": "DMY", "currency": "DKK"},
    "PH": {"locale": "en-PH", "date_order": "MDY", "currency": "PHP"},
}

_DEFAULT = {
    "locale": "en-US",
    "country": "US",
    "date_order": "MDY",
    "currency": "USD",
    "confidence": 0.0,
}

# ──────────────────────────────────────────────────────────────────────────────
# Signal patterns
# ──────────────────────────────────────────────────────────────────────────────

# 1. Currency symbols  → candidate countries (in priority order, most specific first)
_CURRENCY_SIGNALS = [
    (re.compile(r'R\$'),                     ["BR"]),           # Brazilian real (before R)
    (re.compile(r'₹'),                       ["IN"]),
    (re.compile(r'₩'),                       ["KR"]),
    (re.compile(r'₽'),                       ["RU"]),
    (re.compile(r'₺'),                       ["TR"]),
    (re.compile(r'Rp\s?\d'),                 ["ID"]),
    (re.compile(r'£'),                       ["GB"]),
    (re.compile(r'€'),                       ["EU", "DE", "FR", "IT", "ES"]),
    (re.compile(r'¥'),                       ["JP", "CN"]),
    (re.compile(r'\bkr\b', re.I),           ["SE", "NO", "DK"]),
    (re.compile(r'\$'),                      ["US", "CA", "AU"]),
]

# 2. Phone prefixes
_PHONE_SIGNALS = [
    (re.compile(r'\+1[\s\-\(]'),             ["US", "CA"]),
    (re.compile(r'\+44[\s\-\(]'),            ["GB"]),
    (re.compile(r'\+91[\s\-\(]'),            ["IN"]),
    (re.compile(r'\+49[\s\-\(]'),            ["DE"]),
    (re.compile(r'\+33[\s\-\(]'),            ["FR"]),
    (re.compile(r'\+81[\s\-\(]'),            ["JP"]),
    (re.compile(r'\+86[\s\-\(]'),            ["CN"]),
    (re.compile(r'\+61[\s\-\(]'),            ["AU"]),
    (re.compile(r'\+55[\s\-\(]'),            ["BR"]),
    (re.compile(r'\+7[\s\-\(]'),             ["RU"]),
    (re.compile(r'\+82[\s\-\(]'),            ["KR"]),
    (re.compile(r'\+62[\s\-\(]'),            ["ID"]),
    (re.compile(r'\+90[\s\-\(]'),            ["TR"]),
    (re.compile(r'\+46[\s\-\(]'),            ["SE"]),
    (re.compile(r'\+47[\s\-\(]'),            ["NO"]),
    (re.compile(r'\+45[\s\-\(]'),            ["DK"]),
]

# 3. Date patterns
# US: MM/DD/YYYY  (unambiguous only when month > 12 in second position can't hold)
_DATE_US_PATTERN    = re.compile(r'\b(0?[1-9]|1[0-2])/(1[3-9]|2[0-9]|3[01])/\d{4}\b')
# UK/EU: DD/MM/YYYY  (day in 1st pos > 12)
_DATE_DMY_PATTERN   = re.compile(r'\b(1[3-9]|2[0-9]|3[01])/(0?[1-9]|1[0-2])/\d{4}\b')
# ISO: YYYY-MM-DD
_DATE_ISO_PATTERN   = re.compile(r'\b\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b')

# 4. Postal / ZIP patterns
_ZIP_US      = re.compile(r'\b\d{5}(?:-\d{4})?\b')
_POSTCODE_GB = re.compile(r'\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b')
_PIN_IN      = re.compile(r'\b[1-9]\d{5}\b')

# 5. US state abbreviations
_US_STATES = re.compile(
    r'\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO'
    r'|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY'
    r'|DC)\b'
)

# 6. Country/city name keywords (lower-cased match)
_COUNTRY_KEYWORDS: Dict[str, list[str]] = {
    "US": ["united states", "usa", "u.s.a", "new york", "los angeles", "chicago",
           "houston", "phoenix", "philadelphia", "san antonio", "san diego",
           "washington d.c", "washington dc"],
    "GB": ["united kingdom", "england", "scotland", "wales", "london", "manchester",
           "birmingham", "glasgow", "edinburgh", "liverpool"],
    "CA": ["canada", "toronto", "vancouver", "montreal", "ottawa", "calgary"],
    "AU": ["australia", "sydney", "melbourne", "brisbane", "perth", "adelaide"],
    "IN": ["india", "mumbai", "delhi", "bangalore", "hyderabad", "chennai",
           "kolkata", "ahmedabad", "pune"],
    "DE": ["germany", "deutschland", "berlin", "munich", "hamburg", "frankfurt",
           "cologne", "stuttgart"],
    "FR": ["france", "paris", "lyon", "marseille", "toulouse", "bordeaux"],
    "JP": ["japan", "tokyo", "osaka", "nagoya", "sapporo", "fukuoka"],
    "CN": ["china", "beijing", "shanghai", "guangzhou", "shenzhen", "chengdu"],
    "KR": ["south korea", "korea", "seoul", "busan", "incheon", "daegu"],
    "BR": ["brazil", "brasil", "sao paulo", "rio de janeiro", "brasilia",
           "salvador", "fortaleza"],
    "RU": ["russia", "moscow", "saint petersburg", "novosibirsk", "yekaterinburg"],
    "TR": ["turkey", "istanbul", "ankara", "izmir", "bursa"],
    "ID": ["indonesia", "jakarta", "surabaya", "bandung", "medan"],
    "SE": ["sweden", "stockholm", "gothenburg", "malmo"],
    "NO": ["norway", "oslo", "bergen", "stavanger"],
    "DK": ["denmark", "copenhagen", "aarhus"],
    "IT": ["italy", "italia", "rome", "milan", "naples", "turin", "florence"],
    "ES": ["spain", "espana", "madrid", "barcelona", "seville", "valencia"],
}

# ──────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────────────

def _vote(scores: Dict[str, float], countries: list, weight: float) -> None:
    """Add weight to each candidate country."""
    for c in countries:
        scores[c] = scores.get(c, 0.0) + weight


def detect_locale(raw_text: str) -> Dict[str, Any]:
    """
    Detect locale info from raw document text.

    Returns:
        {
            "locale":     str,   # BCP47
            "country":    str,   # ISO 3166
            "date_order": str,   # MDY | DMY | YMD
            "currency":   str,   # ISO 4217
            "confidence": float  # 0.0 – 1.0
        }
    """
    if not raw_text or not raw_text.strip():
        return dict(_DEFAULT)

    # Work on a truncated slice for speed (first 8 KB is usually sufficient)
    text = raw_text[:8192]
    text_lower = text.lower()

    scores: Dict[str, float] = {}

    # ── 1. Currency symbols (weight 0.35) ─────────────────────────────────────
    for pattern, countries in _CURRENCY_SIGNALS:
        if pattern.search(text):
            _vote(scores, countries, 0.35)

    # ── 2. Phone patterns (weight 0.30) ───────────────────────────────────────
    for pattern, countries in _PHONE_SIGNALS:
        if pattern.search(text):
            _vote(scores, countries, 0.30)

    # ── 3. Date patterns (weight 0.15) ────────────────────────────────────────
    if _DATE_US_PATTERN.search(text):
        _vote(scores, ["US"], 0.15)
    if _DATE_DMY_PATTERN.search(text):
        _vote(scores, ["GB", "AU", "IN", "EU", "FR", "DE"], 0.10)
    if _DATE_ISO_PATTERN.search(text):
        _vote(scores, ["JP", "CN", "KR", "SE"], 0.10)

    # ── 4. Postal codes (weight 0.20) ─────────────────────────────────────────
    if _ZIP_US.findall(text):
        _vote(scores, ["US"], 0.20)
    if _POSTCODE_GB.search(text):
        _vote(scores, ["GB"], 0.25)
    if _PIN_IN.search(text):
        _vote(scores, ["IN"], 0.15)

    # ── 5. US state abbreviations (weight 0.15) ───────────────────────────────
    state_hits = _US_STATES.findall(text)
    if len(state_hits) >= 2:
        _vote(scores, ["US"], 0.20)
    elif len(state_hits) == 1:
        _vote(scores, ["US"], 0.10)

    # ── 6. Country/city name keywords (weight 0.25) ───────────────────────────
    for country, keywords in _COUNTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                _vote(scores, [country], 0.25)
                break  # count each country only once per pass

    # ── Resolve winner ────────────────────────────────────────────────────────
    if not scores:
        return dict(_DEFAULT)

    winner = max(scores, key=lambda c: scores[c])
    total  = sum(scores.values()) or 1.0
    # Confidence: winner's fraction of total votes, capped at 0.97
    raw_conf = scores[winner] / total
    confidence = round(min(raw_conf, 0.97), 2)

    meta = _COUNTRY_META.get(winner, _COUNTRY_META["US"])

    return {
        "locale":     meta["locale"],
        "country":    winner,
        "date_order": meta["date_order"],
        "currency":   meta["currency"],
        "confidence": confidence,
    }
