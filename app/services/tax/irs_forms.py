"""
IRS form recognition and box extraction — rules, not an LLM.

Every IRS form prints its number in the header and labels each numbered box
with fixed wording ("Wages, tips, other comp.", "Nonemployee compensation"),
so a form is found by its title and each box by its label followed by the
amount. OCR and Docling scramble the layout (box numbers between label and
value, copies B/C/2 repeated on one page), so a value is taken as the one most
often printed after its label.

  detect_forms(text)  → form kinds present, e.g. ["w2"] or ["1099_int", "1099_div", "1099_b"]
  extract_forms(text) → [{form, year, boxes{...}, payer, recipient}]
"""
import re
from collections import Counter
from typing import Dict, List, Optional

# Title patterns, most specific first (Schedule K-1 says "Form 1065"; schedules say "Form 1040").
FORM_TITLES = [
    ("k1",          r"schedule k-?1\b"),
    ("1040_sched",  r"\bschedule (?:[a-e]|se)\b\s*\(form 1040"),
    ("8949",        r"\bform 8949\b"),
    ("1099_nec",    r"\b1099-?nec\b|nonemployee compensation"),
    ("1099_misc",   r"\b1099-?misc\b|miscellaneous (information|income)"),
    ("1099_int",    r"\b1099-?int\b|interest income\b(?=.{0,200}\b(payer|recipient))"),
    ("1099_div",    r"\b1099-?div\b|dividends and distributions"),
    ("1099_b",      r"\b1099-?b\b|proceeds from broker"),
    ("1099_da",     r"\b1099-?da\b|digital asset proceeds"),
    ("1099_r",      r"\b1099-?r\b|distributions from pensions"),
    ("1099_k",      r"\b1099-?k\b|payment card and third party network"),
    ("1099_g",      r"\b1099-?g\b|certain government payments"),
    ("1098_t",      r"\b1098-?t\b|tuition statement"),
    ("1098_e",      r"\b1098-?e\b|student loan interest statement"),
    ("1098",        r"\bform 1098\b|mortgage interest statement"),
    ("1095",        r"\b1095-?[abc]\b|health (insurance marketplace|coverage) statement"),
    ("w2",          r"\bw-?2\b.{0,120}wage and tax statement|wage and tax statement"),
    ("w9",          r"\bform w-?9\b|request for taxpayer identification number"),
    ("w8ben",       r"\bw-?8ben(-?e)?\b|certificate of (foreign status|status of beneficial owner)"),
    ("w4",          r"\bform w-?4\b|employee'?s withholding certificate"),
    ("w7",          r"\bform w-?7\b|application for irs individual taxpayer"),
    ("1120s",       r"\bform 1120-?s\b|income tax return for an s corporation"),
    ("1120",        r"\bform 1120\b|u\.?s\.? corporation income tax return"),
    ("1065",        r"\bform 1065\b|return of partnership income"),
    ("1040",        r"\bform 1040(-?sr)?\b|u\.?s\.? individual income tax return"),
]

# Document type stored for each form (classifier and doc filters use these).
FORM_DOC_TYPES = {
    "w2": "w2", "w4": "w4", "w7": "w7", "w9": "w9", "w8ben": "w8ben",
    "k1": "schedule_k1", "1040": "form_1040", "1040_sched": "form_1040", "8949": "form_8949",
    "1120": "form_1120", "1120s": "form_1120s", "1065": "form_1065", "1095": "form_1095",
    "1098": "form_1098", "1098_t": "form_1098_t", "1098_e": "form_1098_e",
    **{k: f"form_{k}" for k in ("1099_nec", "1099_misc", "1099_int", "1099_div", "1099_b",
                                "1099_da", "1099_r", "1099_k", "1099_g")},
}

_S = r"\s*"
# Box label patterns per form (IRS wording; spaces optional — OCR drops them).
BOXES: Dict[str, Dict[str, str]] = {
    "w2": {
        "wages":          r"wages,?\s*tips,?\s*other\s*comp(?:ensation|\.)?",
        "federal_wh":     r"federal\s*income\s*tax\s*withheld",
        "ss_wages":       r"social\s*security\s*wages",
        "ss_tax":         r"social\s*security\s*tax\s*withheld",
        "medicare_wages": r"medicare\s*wages\s*and\s*tips",
        "medicare_tax":   r"medicare\s*tax\s*withheld",
        "state_wages":    r"state\s*wages,?\s*tips,?\s*etc\.?",
        "state_tax":      r"state\s*income\s*tax",
    },
    "1099_nec": {"nonemployee_comp": r"nonemployee\s*compensation",
                 "federal_wh": r"federal\s*income\s*tax\s*withheld"},
    "1099_misc": {"rents": r"\brents\b", "royalties": r"\broyalties\b", "other_income": r"other\s*income",
                  "federal_wh": r"federal\s*income\s*tax\s*withheld"},
    "1099_int": {"interest": r"(?<!tax-exempt )interest\s*income", "us_bond_interest": r"interest\s*on\s*u\.?\s*s\.?\s*savings\s*bonds",
                 "tax_exempt_interest": r"tax-?\s*exempt\s*interest", "federal_wh": r"federal\s*income\s*tax\s*withheld"},
    "1099_div": {"ordinary_dividends": r"total\s*ordinary\s*dividends", "qualified_dividends": r"qualified\s*dividends",
                 "capital_gain_dist": r"total\s*capital\s*gain\s*distr", "federal_wh": r"federal\s*income\s*tax\s*withheld"},
    "1099_b": {"proceeds": r"(?:total\s*)?proceeds", "cost_basis": r"cost\s*or\s*other\s*basis",
               "federal_wh": r"federal\s*income\s*tax\s*withheld"},
    "1099_r": {"gross_distribution": r"gross\s*distribution", "taxable_amount": r"taxable\s*amount(?!\s*not)",
               "federal_wh": r"federal\s*income\s*tax\s*withheld"},
    "1099_k": {"gross_payments": r"gross\s*amount\s*of\s*payment\s*card"},
    "1099_g": {"unemployment": r"unemployment\s*compensation", "state_refund": r"state\s*or\s*local\s*income\s*tax\s*refunds"},
    "1098": {"mortgage_interest": r"mortgage\s*interest\s*received", "points": r"points\s*paid"},
    "1098_t": {"tuition": r"payments\s*received\s*for\s*qualified\s*tuition"},
    "1098_e": {"student_loan_interest": r"student\s*loan\s*interest\s*received"},
    "k1": {"ordinary_income": r"ordinary\s*business\s*income", "rental_income": r"net\s*rental\s*real\s*estate\s*income",
           "guaranteed_payments": r"guaranteed\s*payments", "interest": r"interest\s*income",
           "dividends": r"ordinary\s*dividends"},
    # Form 1040 lines (2020+ layout)
    "1040": {
        "wages":           r"total\s*amount\s*from\s*form\(?s\)?\s*w-?2,?\s*box\s*1|wages,\s*salaries,\s*tips",
        "taxable_interest": r"taxable\s*interest",
        "ordinary_dividends": r"ordinary\s*dividends",
        "pensions_taxable": r"pensions\s*and\s*annuities.{0,60}?taxable\s*amount",
        "capital_gain":    r"capital\s*gain\s*or\s*\(?loss\)?",
        "schedule1_income": r"additional\s*income\s*from\s*schedule\s*1",
        "w2_withholding":  r"(?:25a\s*)?form\(?s\)?\s*w-?2(?!,?\s*box)",
        "1099_withholding": r"(?:25b\s*)?form\(?s\)?\s*1099",
        "total_income":    r"this\s*is\s*your\s*total\s*income",
        "agi":             r"this\s*is\s*your\s*adjusted\s*gross\s*income",
        "total_tax":       r"this\s*is\s*your\s*total\s*tax",
    },
    "1040_sched": {"gross_receipts": r"gross\s*receipts\s*or\s*sales",
                   "partnership_scorp_income": r"total\s*partnership\s*and\s*s\s*corporation\s*income"},
}

# label … [box number] … $amount — the box number sits between label and value in OCR text
_VALUE = r"[^\d$\n]{0,25}?(?:\b\d{1,2}[a-z]?\b\s+)?\$?\s*(\(?-?[\d,]+\.\d{2}\)?)"


def _money(s: str) -> float:
    neg = s.startswith("(") or s.startswith("-")
    v = float(s.strip("()-").replace(",", ""))
    return -v if neg else v


def detect_forms(text: str, header_only: bool = False) -> List[str]:
    body = (text or "")[:600] if header_only else (text or "")
    found = []
    for kind, pat in FORM_TITLES:
        if re.search(pat, body, re.I | re.S):
            found.append(kind)
    # Schedules and K-1s mention their parent form; keep the specific one
    if "k1" in found:
        found = [f for f in found if f not in ("1065", "1120s", "1040")]
    if "1040_sched" in found or "8949" in found:
        found = [f for f in found if f != "1040"]
    return found


def classify_form(text: str) -> Optional[str]:
    """Document type from the header, or None if no IRS form title is there."""
    kinds = detect_forms(text, header_only=True)
    return FORM_DOC_TYPES.get(kinds[0]) if kinds else None


def parse_boxes(text: str, form: str) -> Dict[str, float]:
    out = {}
    for key, label in BOXES.get(form, {}).items():
        vals = [m.group(1) for m in re.finditer("(?:" + label + ")" + _VALUE, text, re.I)]
        if vals:
            out[key] = _money(Counter(vals).most_common(1)[0][0])
    return out


def _year(text: str) -> Optional[int]:
    yrs = re.findall(r"\b(20[1-3]\d)\b", text[:2500])
    return int(Counter(yrs).most_common(1)[0][0]) if yrs else None


def _name_after(label: str, text: str) -> Optional[str]:
    m = re.search(label + r"[^\n:]*:?\s*\n?\s*([A-Z0-9][^\n]{2,80})", text, re.I)
    if not m:
        return None
    name = re.split(r"\s{2,}|,\s*\d|\b(?:street|address|tin)\b", m.group(1).strip(), maxsplit=1, flags=re.I)[0]
    name = name.strip(" -:")
    # OCR often puts the next label or a box letter where the name should be
    if (len(name) > 60 or not re.search(r"[A-Za-z]{3,}", name)
            or re.search(r"\b(your|adjusted|statement|copy|instructions|employer|payer|recipient)\b", name, re.I)):
        return None
    return name


def extract_forms(text: str) -> List[Dict]:
    text = (text or "").replace("\\$", "$")
    out = []
    for form in detect_forms(text):
        boxes = parse_boxes(text, form)
        if not boxes and form not in ("w9", "w8ben", "w4", "w7"):
            continue
        out.append({"form": form, "year": _year(text), "boxes": boxes,
                    # returns: keep the header to tell whose return it is
                    "header": text[:1500] if form in ("1040", "1040_sched") else None,
                    "payer": _name_after(r"(?:payer|employer|filer|partnership|corporation)'?s\s*name", text),
                    "recipient": _name_after(r"(?:recipient|employee|borrower|student|partner|shareholder)'?s\s*name", text)})
    return out


# ── W-9 ──────────────────────────────────────────────────────────────────────

_TIN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{2}-\d{7}\b|\[(?:SSN|EIN|TIN)[ _]?REDACTED\]", re.I)


def parse_w9(text: str) -> Dict:
    t = text or ""
    name = _name_after(r"(?:1\s*)?name\s*\(as shown on your income tax return\)|name of entity/individual", t)
    business = _name_after(r"business name/disregarded entity name", t)
    cls = None
    for label, pat in (("C corporation", r"[x☒✓■]\s*[\])]?\s*c\s*corporation"), ("S corporation", r"[x☒✓■]\s*[\])]?\s*s\s*corporation"),
                       ("Partnership", r"[x☒✓■]\s*[\])]?\s*partnership"), ("LLC", r"[x☒✓■]\s*[\])]?\s*limited liability"),
                       ("Individual/sole proprietor", r"[x☒✓■]\s*[\])]?\s*individual")):
        if re.search(pat, t, re.I):
            cls = label
            break
    # Text after the label on the same line (scanned signatures are images — those read as unsigned; verify)
    signed = bool(re.search(r"signature of\s*u\.?s\.? person[ \t]*[:\-]?[ \t]*(?!date\b)[A-Za-z/]\S{2,}", t, re.I))
    return {"name": name, "business_name": business, "classification": cls,
            "has_tin": bool(_TIN_RE.search(t)), "signed": signed}
