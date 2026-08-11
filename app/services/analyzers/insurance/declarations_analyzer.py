# app/services/analyzers/insurance/declarations_analyzer.py
"""
Universal insurance declarations analyzer.

3-layer extraction pipeline:
  Layer 1: LLM (phi3:mini) — reads ANY carrier layout, no regex needed
  Layer 2: Table parsing   — extracts Docling markdown tables
  Layer 3: Regex fallback  — catches what layers 1+2 miss

Works for: GEICO, Stillwater, Travelers, Hartford, Chubb, Progressive,
           State Farm, Allstate, Nationwide, USAA, Liberty Mutual, etc.
Handles:   Homeowners, Auto, Renters, Condo declarations
"""

import json
import logging
import re
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SEISMIC_STATES  = {"CA","OR","WA","AK","NV","UT","HI","MT","WY","ID"}
WILDFIRE_STATES = {"CA","OR","WA","CO","NM","AZ","MT","ID","NV","UT"}

# Carrier name patterns — no hardcoded addresses
CARRIER_PATTERNS: Dict[str, List] = {
    "geico":         [re.compile(r"\bgeico\b|government\s+employees\s+insurance", re.I)],
    "stillwater":    [re.compile(r"stillwater\s+(?:insurance|property)", re.I)],
    "travelers":     [re.compile(r"travelers\s+(?:insurance|casualty|property|commercial)", re.I)],
    "hartford":      [re.compile(r"the\s+hartford|hartford\s+(?:fire|casualty|life)", re.I)],
    "chubb":         [re.compile(r"\bchubb\b", re.I)],
    "progressive":   [re.compile(r"\bprogressive\b", re.I)],
    "state_farm":    [re.compile(r"state\s+farm", re.I)],
    "allstate":      [re.compile(r"\ballstate\b", re.I)],
    "nationwide":    [re.compile(r"\bnationwide\b", re.I)],
    "usaa":          [re.compile(r"\busaa\b", re.I)],
    "liberty_mutual":[re.compile(r"liberty\s+mutual", re.I)],
    "farmers":       [re.compile(r"\bfarmers\s+insurance\b", re.I)],
    "lemonade":      [re.compile(r"\blemonade\b", re.I)],
    "amica":         [re.compile(r"\bamica\b", re.I)],
    "auto_owners":   [re.compile(r"auto[\s\-]?owners", re.I)],
    "erie":          [re.compile(r"\berie\s+insurance\b", re.I)],
    "cincinnati":    [re.compile(r"cincinnati\s+(?:financial|insurance)", re.I)],
    "hanover":       [re.compile(r"\bhanover\s+insurance\b", re.I)],
    "aegis":         [re.compile(r"\baegis\s+insurance\b", re.I)],
}


# ═══════════════════════════════════════════════════════════════════════════════
# SAFE TYPE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _amt(val) -> Optional[float]:
    """
    Safely convert ANY value to float.
    Handles: 50000  "50000"  "$50,000"  "50,000.00"  "1,100,000"
             None  "N/A"  "null"  "n/a"  ""  "$50,000/yr"
    LLM often returns strings with $ and commas — this handles all of them.
    """
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("", "null", "none", "n/a", "na", "-", "—", "not applicable"):
        return None
    # Remove currency symbols, commas, spaces
    s = re.sub(r"[$,\s]", "", s)
    # Remove trailing non-numeric suffixes like /yr, each, /mo
    s = re.sub(r"[^\d.\-].*$", "", s)
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _safe_fmt(val, decimals: int = 0) -> Optional[str]:
    """
    Safely format a value as $X,XXX string.
    Returns None if val cannot be converted — caller skips the field.
    """
    v = _amt(val)
    if v is None or v <= 0:
        return None
    fmt = f":,.{decimals}f"
    return f"${v:{fmt[1:]}}"


def _safe_float(val) -> float:
    """Return float or 0.0 — never raises."""
    v = _amt(val)
    return v if v is not None else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — LLM extraction (phi3:mini)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_via_llm(text: str) -> Dict[str, Any]:
    """
    Use phi3:mini to extract fields from ANY carrier layout.
    Returns raw dict — values may be strings, numbers, or null.
    All values get normalized by _merge_fields() afterward.
    """
    try:
        import ollama
        from app.core import config

        snippet = text[:3500].strip()

        prompt = f"""You are an insurance document data extractor.
Extract the following fields from the insurance declarations page below.
Return ONLY a valid JSON object. Use null for missing fields.
For numeric fields return numbers ONLY — no $ signs, no commas, no units.
Do NOT add explanations or markdown — pure JSON only.

Fields:
  policy_number       - policy number string
  named_insured       - full name of insured
  carrier_name        - insurance company name
  policy_type         - one of: homeowners, auto, renters, condo
  effective_date      - policy start date as MM/DD/YYYY
  expiration_date     - policy end date as MM/DD/YYYY
  annual_premium      - total annual premium as NUMBER ONLY (e.g. 1237.00)
  deductible          - deductible amount as NUMBER ONLY
  state               - 2-letter state code of insured address
  email               - email address of insured if present, else null
  address_line1       - street address ONLY (e.g. "3328 HARVEST GATE WAY") — no city/state/zip, else null
  city                - city name ONLY (e.g. "FOLSOM") — no state or zip, else null
  zip                 - zip/postal code of insured address, else null
  phone               - phone number of insured if present, else null
  vin                 - vehicle VIN if auto (17 chars), else null
  vehicle_year        - vehicle year if auto, else null
  vehicle_make        - vehicle make if auto, else null
  vehicle_model       - vehicle model if auto, else null
  coverage_a          - Coverage A dwelling limit as NUMBER ONLY, else null
  coverage_b          - Coverage B other structures as NUMBER ONLY, else null
  coverage_c          - Coverage C personal property as NUMBER ONLY, else null
  coverage_d          - Coverage D loss of use as NUMBER ONLY, else null
  coverage_e          - Coverage E personal liability as NUMBER ONLY, else null
  coverage_f          - Coverage F med pay as NUMBER ONLY, else null
  bodily_injury_pp    - bodily injury per person as NUMBER ONLY, else null
  bodily_injury_po    - bodily injury per occurrence as NUMBER ONLY, else null
  property_damage     - property damage liability as NUMBER ONLY, else null
  collision_ded       - collision deductible as NUMBER ONLY, else null
  comprehensive_ded   - comprehensive deductible as NUMBER ONLY, else null
  uninsured_pp        - uninsured motorist per person as NUMBER ONLY, else null
  mortgagee           - mortgagee or lienholder name string, else null
  earthquake_excluded - true or false

Document:
{snippet}

JSON:"""

        client = ollama.Client()
        resp   = client.generate(
            model=config.model,
            prompt=prompt,
            stream=False,
            options={"temperature": 0.0, "num_predict": 700},
        )

        raw = resp.get("response", "").strip()
        # Strip markdown fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$",           "", raw, flags=re.MULTILINE)

        m = re.search(r"\{[\s\S]+\}", raw)
        if not m:
            logger.warning("⚠ LLM returned no JSON block")
            return {}

        data = json.loads(m.group())

        # Sanitize ALL numeric fields — LLM sometimes returns "$50,000" despite instructions
        numeric_fields = [
            "annual_premium","deductible","coverage_a","coverage_b","coverage_c",
            "coverage_d","coverage_e","coverage_f","bodily_injury_pp","bodily_injury_po",
            "property_damage","collision_ded","comprehensive_ded","uninsured_pp",
        ]
        for f in numeric_fields:
            if f in data and data[f] is not None:
                data[f] = _amt(data[f])  # normalize to float or None

        filled = sum(1 for v in data.values() if v is not None)
        logger.info(f"✅ LLM extraction: {filled}/{len(data)} fields filled")
        return data

    except json.JSONDecodeError as e:
        logger.warning(f"⚠ LLM JSON parse error: {e}")
        return {}
    except Exception as e:
        logger.error(f"❌ LLM extraction failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — Docling markdown table parsing
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_from_tables(text: str) -> Dict[str, Any]:
    """
    Parse Docling markdown tables:
      | Policy Number     | 6100-63-19-33          |
      | Policy Period     | 12/07/2025 to 12/07/2026 |
      | Named Insured     | Vijay Kumar Bomma      |
    Works for any carrier that Docling converts to markdown tables.
    """
    fields: Dict[str, Any] = {}
    row_re = re.compile(r"\|\s*([^|\n]{2,60}?)\s*\|\s*([^|\n]{1,150}?)\s*\|")

    for m in row_re.finditer(text):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if not val or val in ("-","—","N/A","n/a",""):
            continue

        if "policy number" in key or "policy no" in key:
            fields.setdefault("policy_number", val)

        elif "named insured" in key or (key == "insured"):
            fields.setdefault("named_insured", val)

        elif "policy period" in key or "coverage period" in key or "policy term" in key:
            pm = re.search(
                r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\s+(?:to|through|\-)\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
                val, re.I
            )
            if pm:
                fields.setdefault("effective_date_raw",  pm.group(1))
                fields.setdefault("expiration_date_raw", pm.group(2))

        elif re.search(r"effective\s+date|eff\.?\s+date", key):
            fields.setdefault("effective_date_raw", val)

        elif re.search(r"expir\w*\s+date|exp\.?\s+date", key):
            fields.setdefault("expiration_date_raw", val)

        elif "total" in key and "premium" in key:
            v = _amt(val)
            if v and 50 < v < 500000:
                fields.setdefault("annual_premium", v)

        elif "premium" in key and "total" not in key:
            v = _amt(val)
            if v and 50 < v < 500000:
                fields.setdefault("annual_premium", v)

        elif re.search(r"\bded\w*\b|deductible", key):
            v = _amt(val)
            if v:
                fields.setdefault("deductible", v)

        elif re.search(r"\bvin\b|vehicle\s+id", key):
            if len(val) == 17:
                fields.setdefault("vin", val)

        elif re.search(r"coverage\s*a\b|dwelling\s+limit", key):
            v = _amt(val)
            if v and v > 10000:
                fields.setdefault("coverage_a", v)

        elif re.search(r"coverage\s*b\b|other\s+structure", key):
            v = _amt(val)
            if v:
                fields.setdefault("coverage_b", v)

        elif re.search(r"coverage\s*c\b|personal\s+property", key):
            v = _amt(val)
            if v:
                fields.setdefault("coverage_c", v)

        elif re.search(r"coverage\s*d\b|loss\s+of\s+use|additional\s+living", key):
            v = _amt(val)
            if v:
                fields.setdefault("coverage_d", v)

        elif re.search(r"coverage\s*e\b|personal\s+liability", key):
            v = _amt(val)
            if v and v >= 25000:
                fields.setdefault("coverage_e", v)

        elif re.search(r"coverage\s*f\b|med(?:ical)?\s+pay", key):
            v = _amt(val)
            if v:
                fields.setdefault("coverage_f", v)

        elif re.search(r"bodily\s+injury", key):
            parts = re.findall(r"[\d,]+", val)
            if parts:
                fields.setdefault("bodily_injury_pp", _amt(parts[0]))
            if len(parts) >= 2:
                fields.setdefault("bodily_injury_po", _amt(parts[1]))

        elif re.search(r"property\s+damage", key):
            v = _amt(val)
            if v:
                fields.setdefault("property_damage", v)

        elif re.search(r"collision", key):
            v = _amt(val)
            if v:
                fields.setdefault("collision_ded", v)

        elif re.search(r"comprehensive|other\s+than\s+collision", key):
            v = _amt(val)
            if v:
                fields.setdefault("comprehensive_ded", v)

        elif re.search(r"uninsured|underinsured|um/uim|um\b", key):
            parts = re.findall(r"[\d,]+", val)
            if parts:
                fields.setdefault("uninsured_pp", _amt(parts[0]))

        elif re.search(r"mortgagee|lienholder|loss\s+payee", key):
            fields.setdefault("mortgagee", val[:120])

    logger.info(f"✅ Table extraction: {len(fields)} fields")
    return fields


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — Regex fallback
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_via_regex(text: str) -> Dict[str, Any]:
    """Regex patterns as final fallback — catches what LLM and tables miss."""
    fields: Dict[str, Any] = {}

    # Policy number — multiple formats
    for pat in [
        re.compile(r"\b(\d{4}-\d{2}-\d{2}-\d{2})\b"),                             # GEICO
        re.compile(r"policy\s*(?:number|no\.?|#|num)?\s*[:\-]?\s*([A-Z]{0,4}\d{6,}[A-Z0-9\-]*)", re.I),
        re.compile(r"##\s*policy\s*(?:number|no\.?)?\s*([A-Z0-9\-]{6,})", re.I),
        re.compile(r"policy[:\s]+([A-Z]{1,3}\d{5,})", re.I),
    ]:
        m = pat.search(text)
        if m:
            fields.setdefault("policy_number", m.group(1).strip())
            break

    # Policy period: "12/07/2025 to 12/07/2026"
    m = re.search(
        r"policy\s+period\s*[:\-]?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})"
        r"\s+(?:to|through|\-)\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        text, re.I
    )
    if m:
        fields.setdefault("effective_date_raw",  m.group(1))
        fields.setdefault("expiration_date_raw", m.group(2))

    # Stacked block (Stillwater): EFFECTIVE DATE\n\nEXPIRATION DATE\n\nXX/XX/XXXX\n\nXX/XX/XXXX
    m = re.search(
        r"effective\s+date\s*\n+.*?expir\w*\s+date\s*\n+"
        r"\s*(\d{1,2}/\d{1,2}/\d{4})\s*\n+\s*(\d{1,2}/\d{1,2}/\d{4})",
        text, re.I
    )
    if m:
        fields.setdefault("effective_date_raw",  m.group(1))
        fields.setdefault("expiration_date_raw", m.group(2))

    # Inline effective / expiry
    m = re.search(r"effective\s*(?:date)?\s*[:\-]\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", text, re.I)
    if m:
        fields.setdefault("effective_date_raw", m.group(1))
    m = re.search(r"expir\w*\s*(?:date)?\s*[:\-]\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", text, re.I)
    if m:
        fields.setdefault("expiration_date_raw", m.group(1))

    # Named insured
    m = re.search(
        r"named\s+insured\s*:?\s*(?:named\s+insured\s*:)?\s*"
        r"([A-Z][A-Za-z\s&;\.]+?)(?:\s+\d{3,}|\n|$)",
        text, re.I
    )
    if m:
        name = m.group(1).strip().rstrip(".,;")
        if len(name) > 3:
            fields.setdefault("named_insured", name)

    # Premium
    for pat in [
        re.compile(r"total\s+(?:policy\s+)?premium\s*[:\|]?\s*\$?\s*([\d,]+(?:\.\d{2})?)", re.I),
        re.compile(r"premium\s*/\s*credit[\s\S]{0,800}?\n\s*([\d,]+\.\d{2})\s*\n", re.I),
        re.compile(r"(?:amount\s+due|total\s+amount)\s*[:\|]?\s*\$?\s*([\d,]+\.\d{2})", re.I),
    ]:
        m = pat.search(text)
        if m:
            v = _amt(m.group(1))
            if v and 50 < v < 500000:
                fields.setdefault("annual_premium", v)
                break

    # VIN — strict 17-char alphanumeric
    m = re.search(
        r"(?:vin|vehicle\s+identification\s*(?:number)?)\s*[:\-#]?\s*([A-HJ-NPR-Z0-9]{17})",
        text, re.I
    )
    if m:
        fields.setdefault("vin", m.group(1).strip())

    # Homeowners coverages
    m = re.search(r"coverage\s+a\s+coverage\s+b\s+\$\s+\$\s+\$\s+([\d,]+)\s+([\d,]+)", text, re.I)
    if m:
        fields.setdefault("coverage_a", _amt(m.group(1)))
        fields.setdefault("coverage_b", _amt(m.group(2)))

    # Deductible
    m = re.search(r"(?:all\s+peril|deductible)\s*[:\|]?\s*\$?\s*([\d,]+(?:\.\d{2})?)", text, re.I)
    if m:
        fields.setdefault("deductible", _amt(m.group(1)))

    # Bodily injury
    m = re.search(r"bodily\s+injury[^\n]*\$\s*([\d,]+)[^\n]*(?:\$|\/)?\s*([\d,]+)?", text, re.I)
    if m:
        fields.setdefault("bodily_injury_pp", _amt(m.group(1)))
        if m.group(2):
            fields.setdefault("bodily_injury_po", _amt(m.group(2)))

    # Property damage
    m = re.search(r"property\s+damage[^\n]*\$\s*([\d,]+)", text, re.I)
    if m:
        fields.setdefault("property_damage", _amt(m.group(1)))

    # State — prefer the one nearest a street address
    states = re.findall(r"\b([A-Z]{2})\s+\d{5}\b", text)
    if states:
        fields.setdefault("state", states[0])

    # Earthquake exclusion
    fields.setdefault("earthquake_excluded", bool(re.search(
        r"no\s+coverage[^\n]{0,60}earthquake|earthquake[^\n]{0,30}excluded",
        text, re.I
    )))

    logger.info(f"✅ Regex fallback: {len(fields)} fields")
    return fields


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE — LLM wins, tables fill gaps, regex is safety net
# ═══════════════════════════════════════════════════════════════════════════════

_LLM_TO_INTERNAL = {
    "policy_number":    "policy_number",
    "named_insured":    "named_insured",
    "carrier_name":     "carrier_name",
    "policy_type":      "policy_type",
    "effective_date":   "effective_date_raw",
    "expiration_date":  "expiration_date_raw",
    "annual_premium":   "annual_premium",
    "deductible":       "deductible",
    "state":            "state",
    "vin":              "vin",
    "vehicle_year":     "vehicle_year",
    "vehicle_make":     "vehicle_make",
    "vehicle_model":    "vehicle_model",
    "coverage_a":       "coverage_a",
    "coverage_b":       "coverage_b",
    "coverage_c":       "coverage_c",
    "coverage_d":       "coverage_d",
    "coverage_e":       "coverage_e",
    "coverage_f":       "coverage_f",
    "bodily_injury_pp": "bodily_injury_pp",
    "bodily_injury_po": "bodily_injury_po",
    "property_damage":  "property_damage",
    "collision_ded":    "collision_ded",
    "comprehensive_ded":"comprehensive_ded",
    "uninsured_pp":     "uninsured_pp",
    "mortgagee":        "mortgagee",
    "earthquake_excluded": "earthquake_excluded",
    "email": "email",
    "address_line1": "address_line1",
    "city": "city",
    "zip": "zip",
    "phone": "phone",
}

_SHORT_TO_LONG = {
    "coverage_a":       "coverage_a_dwelling",
    "coverage_b":       "coverage_b_other_structures",
    "coverage_c":       "coverage_c_personal_property",
    "coverage_d":       "coverage_d_loss_of_use",
    "coverage_e":       "coverage_e_personal_liability",
    "coverage_f":       "coverage_f_med_pay",
    "annual_premium":   "total_premium",
    "bodily_injury_pp": "bodily_injury_per_person",
    "bodily_injury_po": "bodily_injury_per_occurrence",
    "property_damage":  "property_damage_liability",
    "collision_ded":    "collision_deductible",
    "comprehensive_ded":"comprehensive_deductible",
    "uninsured_pp":     "uninsured_motorist_per_person",
}


def _merge_fields(llm: Dict, tables: Dict, regex: Dict, text: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}

    # Start with regex (lowest priority)
    for k, v in regex.items():
        if v is not None:
            merged[k] = v

    # Tables fill gaps
    for k, v in tables.items():
        if v is not None and k not in merged:
            merged[k] = v

    # LLM overwrites (highest priority) — normalize field names
    for llm_key, internal_key in _LLM_TO_INTERNAL.items():
        val = llm.get(llm_key)
        if val is not None and str(val).strip().lower() not in ("", "null", "none", "n/a"):
            merged[internal_key] = val

    # Normalize dates
    for raw_key in ("effective_date_raw", "expiration_date_raw"):
        raw = merged.get(raw_key)
        if raw:
            d = _parse_date(str(raw))
            if d:
                out_key = raw_key.replace("_raw", "")
                merged[out_key] = d.strftime("%m/%d/%Y")
                if raw_key == "expiration_date_raw":
                    merged["days_until_expiry"] = _days_until(d)

    # Add long-form aliases expected by insight engine + UI
    for short, long in _SHORT_TO_LONG.items():
        if short in merged and long not in merged:
            merged[long] = merged[short]

    # Carrier from text if LLM didn't return one
    if not merged.get("carrier_name"):
        c = _detect_carrier(text)
        if c:
            merged["carrier_name"] = c

    # Policy type default
    merged.setdefault("policy_type", _detect_policy_type(text))

    logger.info(
        f"📊 Merged: {len(merged)} fields | "
        f"policy={merged.get('policy_number','?')} | "
        f"type={merged.get('policy_type','?')} | "
        f"expiry={merged.get('expiration_date','?')} | "
        f"premium={merged.get('total_premium','?')} | "
        f"vin={merged.get('vin','N/A')}"
    )
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_carrier(text: str) -> Optional[str]:
    for carrier, patterns in CARRIER_PATTERNS.items():
        for p in patterns:
            if p.search(text):
                return carrier.replace("_", " ").title()
    return None


def _detect_policy_type(text: str) -> str:
    t = text.lower()
    auto_kw = ["vin", " auto ", "automobile", "collision", "comprehensive",
               "bodily injury", "uninsured motorist", "vehicle identification"]
    home_kw = ["homeowners", "dwelling", " residence", "coverage a", "renters", "condo"]
    if any(k in t for k in auto_kw):
        return "auto"
    if any(k in t for k in home_kw):
        return "homeowners"
    return "homeowners"


def _parse_date(val: str) -> Optional[date]:
    val = str(val).strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y",
                "%B %d, %Y", "%B %d %Y", "%b %d, %Y",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _days_until(d: date) -> int:
    return (d - date.today()).days


# ═══════════════════════════════════════════════════════════════════════════════
# INSIGHT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_insights(fields: Dict, text: str) -> Tuple[List[Dict], List[Dict]]:
    insights: List[Dict] = []
    alerts:   List[Dict] = []
    state       = str(fields.get("state", ""))
    policy_type = str(fields.get("policy_type", "homeowners"))

    # Expiry countdown
    days     = fields.get("days_until_expiry")
    exp_date = fields.get("expiration_date", "")
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = None

    if days is not None:
        if days < 0:
            alerts.append({"severity":"critical","icon":"🚨",
                "title":"Policy Expired",
                "description":f"Policy expired {abs(days)} days ago ({exp_date}). Client may be uninsured.",
                "action":"Bind replacement coverage immediately."})
        elif days <= 30:
            alerts.append({"severity":"critical","icon":"🚨",
                "title":f"Expiring in {days} Days",
                "description":f"Policy expires {exp_date}. Renewal must be bound immediately.",
                "action":"Contact client within 48 hours. Start renewal now."})
        elif days <= 60:
            alerts.append({"severity":"warning","icon":"⚠️",
                "title":f"Expiring in {days} Days",
                "description":f"Policy expires {exp_date}.",
                "action":"Schedule renewal review. Shop market if needed."})
        elif days <= 90:
            insights.append({"type":"info","icon":"📅",
                "title":f"Renewal in {days} Days",
                "description":f"Policy expires {exp_date}.",
                "action":"Schedule renewal review with client."})
        else:
            insights.append({"type":"ok","icon":"✅",
                "title":f"Policy Active — {days} Days Remaining",
                "description":f"Policy in force until {exp_date}.",
                "action":None})

    if policy_type == "auto":
        _auto_insights(fields, state, insights, alerts)
    else:
        _homeowners_insights(fields, text, state, insights, alerts)

    return insights, alerts


def _homeowners_insights(fields, text, state, insights, alerts):
    if fields.get("earthquake_excluded") and state in SEISMIC_STATES:
        alerts.append({"severity":"warning","icon":"🏔️",
            "title":"No Earthquake Coverage",
            "description":f"Policy excludes earthquake. {state} is in a seismic zone.",
            "action":"Recommend separate earthquake policy (CEA or private market)."})

    if state in WILDFIRE_STATES:
        insights.append({"type":"warning","icon":"🔥",
            "title":"Wildfire Risk Zone",
            "description":f"{state} has elevated wildfire risk. Verify Coverage A adequacy.",
            "action":"Review Coverage A limit vs. current rebuild cost."})

    if re.search(r"flood[^\n]{0,30}excluded|no\s+coverage[^\n]{0,30}flood", text, re.I):
        insights.append({"type":"info","icon":"🌊",
            "title":"No Flood Coverage",
            "description":"Flood damage is not covered.",
            "action":"Discuss flood risk and NFIP enrollment."})

    cov_a = _safe_float(fields.get("coverage_a_dwelling"))
    if cov_a > 0:
        insights.append({"type":"info","icon":"🏠",
            "title":f"Dwelling Coverage: ${cov_a:,.0f}",
            "description":"Verify rebuild cost estimate is current.",
            "action":"Run replacement cost estimator at renewal."})

    cov_e = _safe_float(fields.get("coverage_e_personal_liability"))
    if cov_e > 0:
        if cov_e < 300000:
            insights.append({"type":"warning","icon":"⚖️",
                "title":f"Low Liability: ${cov_e:,.0f}",
                "description":"Personal liability below $300K may be inadequate.",
                "action":"Recommend $500K or umbrella policy."})
        else:
            insights.append({"type":"ok","icon":"✅",
                "title":f"Liability: ${cov_e:,.0f}",
                "description":"Personal liability at a solid level.",
                "action":"Consider umbrella for additional protection."})

    if fields.get("mortgagee"):
        insights.append({"type":"info","icon":"🏦",
            "title":"Mortgagee on File",
            "description":f"{str(fields['mortgagee'])[:60]}.",
            "action":"Verify mortgagee clause is current at renewal."})


def _auto_insights(fields, state, insights, alerts):
    if fields.get("vin"):
        veh = " ".join(filter(None,[
            str(fields.get("vehicle_year","")),
            str(fields.get("vehicle_make","")),
            str(fields.get("vehicle_model",""))
        ])).strip()
        insights.append({"type":"info","icon":"🚗",
            "title":f"VIN: {fields['vin']}",
            "description":veh or "Vehicle identified",
            "action":None})

    bi = _safe_float(fields.get("bodily_injury_per_person"))
    if bi > 0:
        bio = _safe_float(fields.get("bodily_injury_per_occurrence"))
        label = f"${bi:,.0f}" + (f"/${bio:,.0f}" if bio > 0 else "")
        if bi < 100000:
            insights.append({"type":"warning","icon":"⚖️",
                "title":f"Low Bodily Injury: {label}",
                "description":"State minimum limits may be inadequate.",
                "action":"Recommend $100,000/$300,000 or higher."})
        else:
            insights.append({"type":"ok","icon":"✅",
                "title":f"Bodily Injury: {label}",
                "description":"Liability limits appear adequate.",
                "action":"Consider umbrella for additional protection."})

    if fields.get("mortgagee"):
        insights.append({"type":"info","icon":"🏦",
            "title":"Lienholder on File",
            "description":f"{str(fields['mortgagee'])[:60]}.",
            "action":"Notify lienholder of any coverage changes."})


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYZER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class DeclarationsAnalyzer:
    """
    Universal insurance declarations analyzer.
    LLM-first — works for any carrier without per-carrier regex.
    """

    def analyze(
        self,
        file_path: str = None,
        text: str = None,
        metadata: Dict[str, Any] = None,
        parsed: Dict[str, Any] = None,
        classification: Dict[str, Any] = None,
        **kwargs,
    ) -> Dict[str, Any]:

        if text is None and parsed:
            text = parsed.get("text", "") or ""
        text = text or ""

        # 3-layer extraction
        llm_fields   = _extract_via_llm(text)
        table_fields = _extract_from_tables(text)
        regex_fields = _extract_via_regex(text)
        fields       = _merge_fields(llm_fields, table_fields, regex_fields, text)

        insights, alerts = _generate_insights(fields, text)

        carrier       = _detect_carrier(text) or fields.get("carrier_name") or "Insurance Company"
        policy_type   = fields.get("policy_type", "homeowners")
        carrier_disp  = carrier.replace("_", " ").title()

        # Hero metrics — always use _safe_fmt to avoid crashes
        hero = []
        s = _safe_fmt(fields.get("total_premium"), 2)
        if s:
            hero.append({"label":"Annual Premium","value":s,"icon":"💰"})

        days = fields.get("days_until_expiry")
        if days is not None:
            try:
                days_int = int(days)
                hero.append({"label":"Days Until Renewal","value":str(max(days_int,0)),"icon":"📅",
                    "status":"critical" if days_int<=30 else "warning" if days_int<=60 else "ok"})
            except (TypeError, ValueError):
                pass

        if policy_type == "auto":
            if fields.get("vin"):
                hero.append({"label":"VIN","value":str(fields["vin"]),"icon":"🚗"})
            s = _safe_fmt(fields.get("bodily_injury_per_person"))
            if s:
                hero.append({"label":"Bodily Injury","value":s,"icon":"⚖️"})
        else:
            s = _safe_fmt(fields.get("coverage_a_dwelling"))
            if s:
                hero.append({"label":"Dwelling Coverage","value":s,"icon":"🏠"})
            s = _safe_fmt(fields.get("coverage_e_personal_liability"))
            if s:
                hero.append({"label":"Liability","value":s,"icon":"⚖️"})
            s = _safe_fmt(fields.get("deductible"))
            if s:
                hero.append({"label":"Deductible","value":s,"icon":"🛡️"})

        # Coverage table — _safe_fmt guards every value
        cov_table = []
        if policy_type == "auto":
            for label, key in [
                ("Bodily Injury (per person)",     "bodily_injury_per_person"),
                ("Bodily Injury (per occurrence)", "bodily_injury_per_occurrence"),
                ("Property Damage",                "property_damage_liability"),
                ("Collision Deductible",           "collision_deductible"),
                ("Comprehensive Deductible",       "comprehensive_deductible"),
                ("Uninsured Motorist",             "uninsured_motorist_per_person"),
                ("Medical Payments",               "medical_payments"),
            ]:
                s = _safe_fmt(fields.get(key))
                if s:
                    cov_table.append({"coverage": label, "limit": s})
        else:
            for label, key in [
                ("Coverage A — Dwelling",           "coverage_a_dwelling"),
                ("Coverage B — Other Structures",   "coverage_b_other_structures"),
                ("Coverage C — Personal Property",  "coverage_c_personal_property"),
                ("Coverage D — Loss of Use",        "coverage_d_loss_of_use"),
                ("Coverage E — Personal Liability", "coverage_e_personal_liability"),
                ("Coverage F — Med Pay",            "coverage_f_med_pay"),
                ("Deductible",                      "deductible"),
            ]:
                s = _safe_fmt(fields.get(key))
                if s:
                    cov_table.append({"coverage": label, "limit": s})

        # Summary
        insured    = str(fields.get("named_insured",   "Unknown"))
        policy_num = str(fields.get("policy_number",   "Unknown"))
        exp        = str(fields.get("expiration_date", "Unknown"))
        premium    = _safe_fmt(fields.get("total_premium"), 2)

        if policy_type == "auto":
            veh = " ".join(filter(None,[
                str(fields.get("vehicle_year","")),
                str(fields.get("vehicle_make","")),
                str(fields.get("vehicle_model",""))
            ])).strip()
            summary = (
                f"Auto insurance for {insured}. Policy {policy_num} "
                f"with {carrier_disp}, expires {exp}."
                + (f" Vehicle: {veh}." if veh else "")
                + (f" VIN: {fields['vin']}." if fields.get("vin") else "")
                + (f" Annual premium: {premium}." if premium else "")
            )
        else:
            summary = (
                f"Homeowners declarations for {insured}. Policy {policy_num} "
                f"with {carrier_disp}, expires {exp}."
                + (f" Annual premium: {premium}." if premium else "")
            )

        logger.info(
            f"✅ [DeclarationsAnalyzer] carrier={carrier_disp}, type={policy_type}, "
            f"policy={policy_num}, days={fields.get('days_until_expiry','?')}, "
            f"vin={fields.get('vin','N/A')}, premium={premium}, "
            f"insights={len(insights)}, alerts={len(alerts)}"
        )

        return {
            "type":                   "declarations_page",
            "carrier":                carrier,
            "carrier_display":        carrier_disp,
            "policy_type":            policy_type,
            "summary":                summary,
            "fields":                 fields,
            "hero_metrics":           hero,
            "coverage_table":         cov_table,
            "insights":               insights,
            "alerts":                 alerts,
            "has_advanced_analytics": True,
            "result_type":            "insurance_declarations",
            "ui_config": {
                "sections": [
                    {"id":"alerts",   "title":"⚠️ Action Required","type":"alerts",   "data":alerts,     "visible":len(alerts)>0},
                    {"id":"hero",     "title":"Policy Summary",     "type":"metrics",  "data":hero},
                    {"id":"coverage", "title":"Coverage Limits",    "type":"table",    "data":cov_table},
                    {"id":"insights", "title":"💡 Agent Insights",  "type":"insights", "data":insights},
                ]
            }
        }