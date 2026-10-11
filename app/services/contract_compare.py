"""
Contract vs. standard template — clause-level comparison.

Both texts are split into sections, each section is classified into a standard clause type, and the
key terms in each matched pair (durations, money, percentages, governing law, auto-renewal, mutuality)
are compared. Every clause type in either document gets exactly one finding:

  match       — present in both, key terms equal and wording close
  deviates    — present in both, key terms differ (each difference listed) or wording diverges
  missing     — in the template, not found in the contract
  additional  — in the contract, not in the template (non-standard addition)

Deterministic: no model call, so the same pair always yields the same findings.
"""
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

# clause type → (label, keywords that identify it in a heading or opening sentence)
CLAUSES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "term":            ("Term and renewal", ("term of agreement", "term and renewal", "renewal", "initial term", "duration")),
    "termination":     ("Termination", ("termination", "terminate")),
    "payment":         ("Payment terms", ("payment", "fees", "invoice", "compensation", "pricing")),
    "liability":       ("Limitation of liability", ("limitation of liability", "liability cap", "limit of liability", "liability")),
    "indemnification": ("Indemnification", ("indemnif", "hold harmless")),
    "confidentiality": ("Confidentiality", ("confidential", "non-disclosure", "nondisclosure")),
    "ip":              ("Intellectual property", ("intellectual property", "ownership of work", "work product", "license grant")),
    "governing_law":   ("Governing law", ("governing law", "choice of law", "laws of the state")),
    "disputes":        ("Dispute resolution", ("dispute", "arbitration", "jurisdiction and venue", "venue")),
    "notices":         ("Notices", ("notices", "notice to")),
    "assignment":      ("Assignment", ("assignment", "assign")),
    "warranties":      ("Warranties", ("warrant", "representations")),
    "insurance":       ("Insurance", ("insurance",)),
    "force_majeure":   ("Force majeure", ("force majeure", "acts of god")),
    "data_protection": ("Data protection", ("data protection", "personal data", "privacy", "security breach")),
    "non_solicit":     ("Non-solicitation", ("non-solicit", "nonsolicit", "solicit")),
    "audit":           ("Audit rights", ("audit",)),
    "entire":          ("Entire agreement", ("entire agreement", "integration")),
}
_ORDER = list(CLAUSES)

_HEADING = re.compile(
    r"^\s*(?:(?:section|article|clause)\s+)?(?:\d+(?:\.\d+)*\.?|[ivxlc]+\.|[A-Z]\.)\s+[^\n]{2,90}$"   # 1. / 2.3 / IV. / A.
    r"|^\s*[A-Z][A-Z &/,'-]{3,80}$",                                                                 # ALL-CAPS heading
    re.M | re.I)

_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_NUMWORD = (r"(?:(?:" + "|".join(_TENS) + r")(?:[- ](?:" + "|".join(_UNITS) + r"))?|"
            + "|".join(list(_TEENS) + list(_UNITS)) + r")")
_DUR = re.compile(r"\b(\d{1,4}|" + _NUMWORD + r")\s*(?:\(\s*\d+\s*\))?\s*"
                  r"(business\s+days?|calendar\s+days?|days?|weeks?|months?|years?)\b", re.I)
_MONEY = re.compile(r"(?:\$|usd\s?)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|thousand|k|m))?\b", re.I)
_PCT = re.compile(r"\b\d+(?:\.\d+)?\s?%")
_LAW = re.compile(r"(?i:laws? of (?:the )?(?:state of |commonwealth of )?)([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?)")


def _words_to_int(w: str) -> int:
    w = w.lower().replace("-", " ").split()
    if len(w) == 2:
        return _TENS[w[0]] + _UNITS[w[1]]
    return _TENS.get(w[0]) or _TEENS.get(w[0]) or _UNITS[w[0]]


def split_sections(text: str) -> List[Dict]:
    """[{heading, body}] — falls back to paragraphs when the document has no headings."""
    text = (text or "").replace("\r", "")
    marks = [m for m in _HEADING.finditer(text) if len(m.group(0).split()) <= 12]
    if len(marks) < 2:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 40]
        return [{"heading": p.split("\n")[0][:80], "body": p} for p in paras]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if body:
            out.append({"heading": m.group(0).strip(), "body": body})
    return out


def classify(section: Dict) -> Optional[str]:
    head = section["heading"].lower()
    lead = section["body"][:240].lower()
    for key in _ORDER:                         # heading wins over body text
        if any(k in head for k in CLAUSES[key][1]):
            return key
    for key in _ORDER:
        if any(k in lead for k in CLAUSES[key][1]):
            return key
    return None


def _norm_dur(m) -> str:
    n = m.group(1)
    n = int(n) if n.isdigit() else _words_to_int(n)
    unit = re.sub(r"\s+", " ", m.group(2).lower()).rstrip("s")
    return f"{n} {unit}"


def key_terms(text: str) -> Dict[str, List[str]]:
    t = " ".join(text.split())
    return {
        "durations": sorted({_norm_dur(m) for m in _DUR.finditer(t)}),
        "amounts": sorted({re.sub(r"\s", "", m.group(0).lower()) for m in _MONEY.finditer(t)}),
        "percentages": sorted({m.group(0).replace(" ", "") for m in _PCT.finditer(t)}),
        "governing_law": sorted({m.group(1) for m in _LAW.finditer(t)}),
        "auto_renewal": ["yes"] if re.search(r"automatic(?:ally)? renew|auto-?renew|renews? automatically", t, re.I) else [],
        "mutual": ["yes"] if re.search(r"\bmutual(?:ly)?\b|each party|either party", t, re.I) else [],
    }


_TERM_LABELS = {"durations": "Time period", "amounts": "Amount", "percentages": "Percentage",
                "governing_law": "Governing law", "auto_renewal": "Automatic renewal", "mutual": "Applies to both parties"}


def _group(text: str) -> Dict[str, Dict]:
    groups: Dict[str, Dict] = {}
    for s in split_sections(text):
        k = classify(s)
        if not k:
            continue
        g = groups.setdefault(k, {"headings": [], "body": ""})
        g["headings"].append(s["heading"])
        g["body"] += ("\n\n" if g["body"] else "") + s["body"]
    return groups


def _snippet(s: str, n: int = 280) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def compare(contract_text: str, template_text: str) -> Dict:
    c, t = _group(contract_text), _group(template_text)
    findings = []
    for key in _ORDER:
        if key not in c and key not in t:
            continue
        label = CLAUSES[key][0]
        if key in t and key not in c:
            findings.append({"clause": key, "label": label, "status": "missing", "differences": [],
                             "template": _snippet(t[key]["body"]), "contract": None,
                             "template_section": t[key]["headings"][0], "contract_section": None})
            continue
        if key in c and key not in t:
            findings.append({"clause": key, "label": label, "status": "additional", "differences": [],
                             "template": None, "contract": _snippet(c[key]["body"]),
                             "template_section": None, "contract_section": c[key]["headings"][0]})
            continue
        ct, tt = key_terms(c[key]["body"]), key_terms(t[key]["body"])
        diffs = [{"term": _TERM_LABELS[k], "template": ", ".join(tt[k]) or "not stated", "contract": ", ".join(ct[k]) or "not stated"}
                 for k in _TERM_LABELS if ct[k] != tt[k]]
        sim = SequenceMatcher(None, " ".join(c[key]["body"].lower().split()), " ".join(t[key]["body"].lower().split())).ratio()
        status = "match" if not diffs and sim >= 0.6 else "deviates"
        if status == "deviates" and not diffs:
            diffs.append({"term": "Wording", "template": "standard wording", "contract": f"{round(sim * 100)}% similar to the template"})
        findings.append({"clause": key, "label": label, "status": status, "differences": diffs,
                         "similarity": round(sim, 2), "template": _snippet(t[key]["body"]), "contract": _snippet(c[key]["body"]),
                         "template_section": t[key]["headings"][0], "contract_section": c[key]["headings"][0]})
    counts = {s: sum(1 for f in findings if f["status"] == s) for s in ("match", "deviates", "missing", "additional")}
    return {"findings": findings, "counts": counts,
            "needs_review": counts["deviates"] + counts["missing"] + counts["additional"],
            "clauses_in_template": len(t), "clauses_in_contract": len(c)}
