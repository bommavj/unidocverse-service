"""
Vendor Tax Compliance Audit — the free "Audit Exception Report".

Runs locally over a folder of vendor tax forms (W-9 / W-8) and the client's
vendor master or AP export, and reports:

  - Missing W-9           paid vendors (services, ≥ 1099 threshold) with no form on file
  - Invalid TIN format    EIN prefix never issued, SSN area/group/serial invalid, placeholders
  - Missing TIN           W-9 with no SSN/EIN
  - TIN type mismatch     corporation / partnership certifying with an SSN
  - Name mismatch         W-9 line 1 / line 2 doesn't match the vendor master name
  - Duplicate TIN         one TIN on W-9s for differently named vendors
  - Conflicting TINs      one vendor with W-9s showing different TINs
  - Not signed / no date, stale revision, missing tax classification
  - Foreign address       likely needs a W-8BEN / W-8BEN-E instead
  - Backup-withholding exposure: 24% of payments to vendors without a valid TIN

Privacy: TINs live only in memory while a form is checked. The report shows the
last 4 digits; duplicates are found with a salted hash. Nothing is stored in the
database and nothing leaves the machine.

Format checks only — IRS TIN Matching (e-Services) confirms the name/TIN pair.
"""
import hashlib
import os
import re
import secrets
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.services.tax.irs_forms import _name_after
from app.services.tax.rules import by_year, rules

FORM_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
_SALT = secrets.token_bytes(16)          # per run — hashes can't be matched across runs
_CORP = {"inc", "co", "llc", "llp", "lp", "corp", "corporation", "company", "ltd", "the", "incorporated",
         "pllc", "pc", "pa", "dba", "and", "&"}

SEVERITY = {"Missing W-9": "high", "Invalid TIN format": "high", "Missing TIN": "high",
            "TIN type doesn't fit the entity": "high", "Conflicting TINs for one vendor": "high",
            "Duplicate TIN across vendors": "medium", "Name doesn't match vendor master": "medium",
            "W-9 not signed": "medium", "Foreign address — W-8 likely required": "medium",
            "No tax classification checked": "low", "W-9 not dated": "low", "Outdated W-9 revision": "low",
            "Unreadable form (scan needs OCR)": "low", "W-9 not matched to a vendor": "low",
            "IRS: name/TIN mismatch": "high", "IRS: TIN not issued": "high", "IRS: TIN missing or malformed": "high"}


def merge_irs_results(r: Dict, results: List[Dict]) -> Dict:
    """Add IRS TIN Matching outcomes to the audit: failures become exceptions and count toward exposure."""
    from app.services.tax.tin_matching import RESULT_CODES
    kind = {"3": "IRS: name/TIN mismatch", "2": "IRS: TIN not issued", "1": "IRS: TIN missing or malformed"}
    bw = rules()["info_returns"]["backup_withholding_rate"]
    rows = {v["vendor"]: v for v in r["vendors"]}
    for x in results:
        if x["code"] in kind:
            v = rows.get(x["vendor"])
            paid = v["paid"] if v else 0.0
            r["exceptions"].append({"exception": kind[x["code"]], "severity": "high", "vendor": x["vendor"],
                                    "detail": f"{RESULT_CODES[x['code']][0]} (W-9 line 1 “{x['w9_name']}”, TIN {x['tin_last4']}) "
                                              "— send a B-notice / re-solicit the W-9",
                                    "file": x["file"], "paid": paid})
            if v and v["reportable"] and not v["backup_withholding_exposure"]:
                v["backup_withholding_exposure"] = round(paid * bw, 2)
                v["status"] = "IRS mismatch"
    r["exceptions"].sort(key=lambda e: ({"high": 0, "medium": 1, "low": 2}[e["severity"]], -e["paid"]))
    r["exposure"] = round(sum(v["backup_withholding_exposure"] for v in r["vendors"]), 2)
    r["vendors_with_exceptions"] = len({e["vendor"] for e in r["exceptions"] if e["vendor"] != "—"})
    r["irs_results"] = results
    r["penalties"] = penalty_estimate(r, r.get("penalties", {}).get("hourly_rate"))
    return r


# ── TIN rules ────────────────────────────────────────────────────────────────

def validate_tin(raw: str, kind: str) -> Optional[str]:
    """None if the format is valid, else the reason."""
    t = rules()["tin"]
    d = re.sub(r"\D", "", raw or "")
    if len(d) != 9:
        return f"{len(d)} digits (needs 9)"
    if d in t["placeholders"] or len(set(d)) == 1:
        return "placeholder / sample number"
    if kind == "EIN":
        return None if d[:2] in t["ein_valid_prefixes"] else f"EIN prefix {d[:2]} is never issued by the IRS"
    area, group, serial = int(d[:3]), int(d[3:5]), int(d[5:])
    if 900 <= area <= 999:
        if any(lo <= group <= hi for lo, hi in t["itin_groups"]):
            return None                                     # ITIN
        return f"starts with 9 but group {d[3:5]} isn't a valid ITIN range"
    if area in (0, 666):
        return f"SSN area {d[:3]} is never issued"
    if group == 0:
        return "SSN group 00 is invalid"
    if serial == 0:
        return "SSN serial 0000 is invalid"
    return None


def _mask(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    return f"•••••{d[-4:]}" if len(d) >= 4 else "—"


def _hash(raw: str) -> str:
    return hashlib.sha256(_SALT + re.sub(r"\D", "", raw).encode()).hexdigest()[:16]


# ── Reading a W-9 / W-8 ─────────────────────────────────────────────────────

_SSN_RE = re.compile(r"(?:social\s*security\s*number|ssn)[^\d\n]{0,40}(\d{3}[-\s]?\d{2}[-\s]?\d{4})", re.I)
_EIN_RE = re.compile(r"(?:employer\s*identification\s*number|\bein\b)[^\d\n]{0,40}(\d{2}[-\s]?\d{7})", re.I)
_ANY_SSN = re.compile(r"\b(\d{3}-\d{2}-\d{4})\b")
_ANY_EIN = re.compile(r"\b(\d{2}-\d{7})\b")
_US_STATE = re.compile(r"\b(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|PR|RI|S[CD]|"
                       r"T[NX]|UT|V[AIT]|W[AIVY])\s+\d{5}(-\d{4})?\b")


def read_text(path: Path) -> str:
    """PDF text layer first; scans go through the OCR parser."""
    text = ""
    if path.suffix.lower() == ".pdf":
        import fitz
        try:
            with fitz.open(path) as doc:
                text = "\n".join(p.get_text() for p in doc)
        except Exception:
            text = ""
    if len(text.strip()) < 80:
        try:
            from app.services.parsers.docling_parser import DoclingParser
            text = DoclingParser().parse(str(path), path.name).get("text") or text
        except Exception:
            pass
    return text


def _date(s: str):
    d = pd.to_datetime(s, errors="coerce")
    return d.date() if pd.notna(d) else None


def parse_vendor_form(text: str, filename: str, keep_tin: bool = False) -> Dict:
    t = (text or "").replace("\\$", "$")
    head = t[:1200]
    kind = ("W-8" if re.search(r"\bw-?8\s*ben(-?e)?\b|certificate of (foreign status|status of beneficial owner)", head, re.I)
            else "W-9" if re.search(r"\bw-?9\b|request for taxpayer identification", head, re.I) else None)
    out = {"file": filename, "kind": kind, "readable": len(t.strip()) >= 80}
    if kind != "W-9":
        out["name"] = _name_after(r"name of (individual|organization)[^\n]*", t) if kind == "W-8" else None
        return out
    from app.services.tax.irs_forms import parse_w9
    w = parse_w9(t)
    ssn, ein = _SSN_RE.search(t), _EIN_RE.search(t)
    if not (ssn or ein):                                    # number printed without its label
        ssn, ein = _ANY_SSN.search(t), _ANY_EIN.search(t)
    tin_kind, tin = (("EIN", ein.group(1)) if ein else ("SSN", ssn.group(1)) if ssn else (None, None))
    if re.search(r"\[(SSN|EIN|TIN)[ _]?REDACTED\]", t, re.I) and not tin:
        tin_kind = "redacted"
    rev = re.search(r"\(rev\.?\s*(?:([a-z]+)\.?\s*)?(\d{4})\)", t, re.I)
    date_m = re.search(r"\bdate\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})", t, re.I)
    addr = _name_after(r"(?:6\s*)?city,?\s*state,?\s*and\s*zip\s*code", t) or ""
    foreign = bool(addr) and not _US_STATE.search(addr) and bool(re.search(r"[A-Za-z]{3,}", addr))
    out.update({"name": w["name"], "business_name": w["business_name"], "classification": w["classification"],
                "signed": w["signed"], "tin_kind": tin_kind,
                "tin_error": validate_tin(tin, tin_kind) if tin else None,
                "tin_hash": _hash(tin) if tin else None, "tin_last4": _mask(tin) if tin else "—",
                "revision_year": int(rev.group(2)) if rev else None,
                "revision": f"{(rev.group(1) or '').title()} {rev.group(2)}".strip() if rev else None,
                "dated": bool(date_m), "signed_on": _date(date_m.group(1)) if date_m else None,
                "address": addr, "foreign_address": foreign})
    if keep_tin and tin:
        out["_tin"] = re.sub(r"\D", "", tin)               # only for the IRS TIN Matching file; dropped after
    return out                                              # raw TIN goes out of scope here


# ── Vendor master / AP ──────────────────────────────────────────────────────

def _norm(name: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _CORP}


def _sim(a: str, b: str) -> float:
    x, y = _norm(a), _norm(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def load_vendors(path: Path, year: Optional[int] = None) -> Dict[str, Dict]:
    """Vendor master (one row per vendor) or AP ledger (payments) → {vendor: {paid, services}}."""
    df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    col = lambda *pats: next((c for p in pats for c in df.columns if re.search(p, str(c), re.I)), None)
    created_col = col(r"creat|since|set ?up|onboard|added|open(ed)? date")
    email_col = col(r"e-?mail")
    contact_col = col(r"contact|attention|attn")
    vcol = col(r"^vendor( name)?$", r"vendor|supplier|payee|name")
    acol = col(r"^amount$", r"paid|amount|total|ytd")
    dcol = next((c for c in df.columns if re.search(r"date", str(c), re.I) and c != created_col), None)
    ccol = col(r"category|description|type|account")
    if not vcol:
        raise ValueError(f"No vendor/supplier column in {path.name}")
    svc = rules()["info_returns"]["service_words"]
    out: Dict[str, Dict] = {}
    for _, r in df.iterrows():
        name = str(r[vcol]).strip()
        if not name or name.lower() == "nan":
            continue
        v = out.setdefault(name, {"paid": 0.0, "services": not ccol, "rows": 0, "since": None, "since_source": None,
                                  "email": None, "contact": None})
        d = pd.to_datetime(r[dcol], errors="coerce") if dcol else pd.NaT
        if created_col:
            c = pd.to_datetime(r[created_col], errors="coerce")
            if pd.notna(c):
                v["since"], v["since_source"] = c.date(), "vendor created date"
        elif pd.notna(d) and (v["since"] is None or d.date() < v["since"]):
            v["since"], v["since_source"] = d.date(), "first payment in the file"
        for key, cc in (("email", email_col), ("contact", contact_col)):
            if cc and str(r[cc]).strip() not in ("", "nan"):
                v[key] = str(r[cc]).strip()
        if dcol and year and pd.notna(d) and d.year != year:
            continue
        amt = pd.to_numeric(r[acol], errors="coerce") if acol else 0
        v["paid"] += 0.0 if pd.isna(amt) else float(amt)
        v["rows"] += 1
        if ccol and any(w in str(r[ccol]).lower() for w in svc):
            v["services"] = True
    return out


# ── Checks on one W-9 (batch audit and onboarding share these) ─────────────

def w9_issues(f: Dict) -> List[Tuple[str, str]]:
    tinr, w9r = rules()["tin"], rules()["w9"]
    out = []
    if f["tin_kind"] is None:
        out.append(("Missing TIN", "No SSN or EIN on the W-9"))
    elif f["tin_kind"] != "redacted" and f["tin_error"]:   # redacted before we saw it — can't judge format
        out.append(("Invalid TIN format", f"{f['tin_kind']} {f['tin_last4']}: {f['tin_error']}"))
    if f["tin_kind"] == "SSN" and f.get("classification") in tinr["ein_required"]:
        out.append(("TIN type doesn't fit the entity", f"{f['classification']} certified with an SSN — needs its EIN"))
    if not f["signed"]:
        out.append(("W-9 not signed", "No signature on the certification line (scanned signatures need a visual check)"))
    if not f["dated"]:
        out.append(("W-9 not dated", "No date next to the signature"))
    if not f.get("classification"):
        out.append(("No tax classification checked", "Line 3a has no box checked — 1099 exemption can't be confirmed"))
    if f.get("revision_year") and f["revision_year"] < w9r["stale_before_year"]:
        out.append(("Outdated W-9 revision", f"Rev. {f['revision']}; current is {w9r['current_revision']}"))
    if f.get("foreign_address"):
        out.append(("Foreign address — W-8 likely required", f"Address “{f['address']}” isn't a U.S. state/ZIP"))
    return out


# ── Audit ────────────────────────────────────────────────────────────────────

def audit(forms: List[Dict], vendors: Dict[str, Dict], year: int) -> Dict:
    threshold, _ = by_year(rules()["info_returns"]["nec_threshold"], year)
    bw = rules()["info_returns"]["backup_withholding_rate"]
    tinr, w9r = rules()["tin"], rules()["w9"]
    ex: List[Dict] = []

    def add(kind, vendor, detail, file=None, amount=0.0):
        ex.append({"exception": kind, "severity": SEVERITY[kind], "vendor": vendor, "detail": detail,
                   "file": file or "—", "paid": round(amount, 2)})

    w9s = [f for f in forms if f["kind"] == "W-9"]
    w8s = [f for f in forms if f["kind"] == "W-8"]
    # Match each W-9 to a vendor: line 1, then line 2 (DBA)
    by_vendor: Dict[str, List[Dict]] = defaultdict(list)
    for f in w9s:
        best, score, via = None, 0.0, None
        for v in vendors:
            for field in ("name", "business_name"):
                s = _sim(v, f.get(field) or "")
                if s > score:
                    best, score, via = v, s, field
        f["vendor"] = best if score >= 0.5 else None
        f["match"] = round(score, 2)
        f["matched_on"] = via
        if f["vendor"]:
            by_vendor[f["vendor"]].append(f)

    for f in forms:
        if not f["readable"]:
            add("Unreadable form (scan needs OCR)", "—", "No text could be read; re-scan or OCR", f["file"])
    for f in w9s:
        who = f["vendor"] or f.get("name") or f["file"]
        paid = vendors.get(f["vendor"], {}).get("paid", 0.0) if f["vendor"] else 0.0
        if not f["vendor"]:
            add("W-9 not matched to a vendor", f.get("name") or "—",
                f"Line 1 “{f.get('name') or '—'}”, line 2 “{f.get('business_name') or '—'}” — no vendor master entry", f["file"])
        # Only the DBA (line 2) matches, or line 1 is a loose match: the 1099 name/TIN pair would fail IRS matching
        elif f["matched_on"] == "business_name" or f["match"] < 0.75:
            add("Name doesn't match vendor master", who,
                f"Vendor master “{f['vendor']}”; W-9 line 1 “{f.get('name') or '—'}”"
                + (f", line 2 “{f['business_name']}”" if f.get("business_name") else "")
                + " — 1099s must use the line 1 name with the TIN, or IRS matching fails", f["file"], paid)
        for kind, detail in w9_issues(f):
            add(kind, who, detail, f["file"], paid)

    # Duplicates / conflicts by TIN hash
    by_tin: Dict[str, set] = defaultdict(set)
    for f in w9s:
        if f.get("tin_hash"):
            by_tin[f["tin_hash"]].add(f["vendor"] or f.get("name") or f["file"])
    for h, names in by_tin.items():
        if len({n for n in names}) > 1:
            last4 = next(f["tin_last4"] for f in w9s if f.get("tin_hash") == h)
            add("Duplicate TIN across vendors", ", ".join(sorted(names)),
                f"{len(names)} vendors share TIN {last4} — duplicate vendor records or a wrong TIN")
            ex[-1]["vendors"] = sorted(names)
    for v, fs in by_vendor.items():
        hashes = {f["tin_hash"] for f in fs if f.get("tin_hash")}
        if len(hashes) > 1:
            add("Conflicting TINs for one vendor", v, f"{len(fs)} W-9s with {len(hashes)} different TINs",
                ", ".join(f["file"] for f in fs), vendors[v]["paid"])

    # Missing W-9s and exposure
    w8_names = [f.get("name") or "" for f in w8s]
    rows = []
    for v, info in sorted(vendors.items(), key=lambda x: -x[1]["paid"]):
        fs = by_vendor.get(v, [])
        has_w8 = any(_sim(v, n) >= 0.5 for n in w8_names)
        reportable = info["services"] and info["paid"] >= threshold
        valid = any(f["tin_kind"] in ("SSN", "EIN", "redacted") and not f["tin_error"] for f in fs)
        if reportable and not fs and not has_w8:
            add("Missing W-9", v, f"${info['paid']:,.2f} paid for services in {year}; no W-9 or W-8 on file", amount=info["paid"])
        exposure = round(info["paid"] * bw, 2) if reportable and not has_w8 and not valid else 0.0
        status = ("W-8 on file" if has_w8 and not fs else "OK" if valid else "No W-9" if not fs else "Invalid / missing TIN")
        since, since_src = info.get("since"), info.get("since_source")
        if since is None:
            dated = [f["signed_on"] for f in fs if f.get("signed_on")]
            if dated:
                since, since_src = min(dated), "W-9 signature date"
        rows.append({"vendor": v, "since": since, "since_source": since_src, "email": info.get("email"),
                     "contact": info.get("contact"),
                     "paid": round(info["paid"], 2), "reportable": reportable, "status": status,
                     "w9_files": ", ".join(f["file"] for f in fs) or "—",
                     "tin": ", ".join(sorted({f["tin_last4"] for f in fs})) or "—",
                     "backup_withholding_exposure": exposure})

    ex.sort(key=lambda e: ({"high": 0, "medium": 1, "low": 2}[e["severity"]], -e["paid"]))
    return {"year": year, "threshold": threshold, "forms": len(forms), "w9": len(w9s), "w8": len(w8s),
            "vendors": rows, "exceptions": ex,
            "exposure": round(sum(r["backup_withholding_exposure"] for r in rows), 2),
            "vendors_with_exceptions": len({e["vendor"] for e in ex if e["vendor"] != "—"})}


# ── Recurrence: are NEW vendors still coming in broken? ─────────────────────

_RECUR_IGNORE = {"Unreadable form (scan needs OCR)", "W-9 not matched to a vendor", "Outdated W-9 revision"}


def _vendors_of(e: Dict) -> List[str]:
    """A duplicate-TIN exception names every vendor sharing the TIN."""
    if e.get("vendors"):
        return list(e["vendors"])
    return [e["vendor"]]


def new_vendor_rate(r: Dict, as_of=None, months: int = 12) -> Dict:
    """Exception rate for vendors added in the last `months` vs older vendors (as of year end by default)."""
    from datetime import date, timedelta
    as_of = as_of or date(r["year"], 12, 31)
    cutoff = as_of - timedelta(days=round(months * 30.44))
    bad = {n for e in r["exceptions"] if e["exception"] not in _RECUR_IGNORE for n in _vendors_of(e)}
    known = [v for v in r["vendors"] if v.get("since")]
    new = [v for v in known if cutoff < v["since"] <= as_of]
    old = [v for v in known if v["since"] <= cutoff]
    rate = lambda vs: round(100 * sum(1 for v in vs if v["vendor"] in bad) / len(vs), 1) if vs else None
    sources = sorted({v["since_source"] for v in known if v.get("since_source")})
    return {"as_of": as_of, "months": months, "new_vendors": len(new),
            "new_with_exceptions": sum(1 for v in new if v["vendor"] in bad), "new_rate": rate(new),
            "older_vendors": len(old), "older_rate": rate(old), "unknown_dates": len(r["vendors"]) - len(known),
            "date_source": ", ".join(sources) or "none"}


# ── Re-solicitation: one row per vendor, what to send and why ──────────────

_ASK = {  # exception → (what to request, reason in plain words)
    "Missing W-9": ("Form W-9", "we have no Form W-9 on file"),
    "Missing TIN": ("Form W-9", "the Form W-9 we have has no taxpayer identification number"),
    "Invalid TIN format": ("Form W-9", "the taxpayer identification number on your Form W-9 isn't a valid format"),
    "TIN type doesn't fit the entity": ("Form W-9", "your entity type requires an EIN, but an SSN was provided"),
    "Conflicting TINs for one vendor": ("Form W-9", "we have Forms W-9 with different taxpayer identification numbers"),
    "Duplicate TIN across vendors": ("Confirmation", "your taxpayer identification number also appears on another vendor record"),
    "Name doesn't match vendor master": ("Form W-9", "the name on line 1 of your Form W-9 differs from our records — "
                                                     "please confirm your legal name as filed with the IRS"),
    "W-9 not signed": ("Form W-9", "your Form W-9 isn't signed"),
    "W-9 not dated": ("Form W-9", "your Form W-9 isn't dated"),
    "No tax classification checked": ("Form W-9", "no federal tax classification is checked on line 3a"),
    "Outdated W-9 revision": ("Form W-9", "your Form W-9 is an outdated revision"),
    "Foreign address — W-8 likely required": ("Form W-8BEN / W-8BEN-E", "your address is outside the U.S."),
    "IRS: name/TIN mismatch": ("Form W-9 (B-notice)", "the IRS reports that your name and taxpayer identification number don't match"),
    "IRS: TIN not issued": ("Form W-9 (B-notice)", "the IRS reports that the taxpayer identification number isn't issued"),
    "IRS: TIN missing or malformed": ("Form W-9", "the IRS couldn't process the taxpayer identification number"),
}
_FORM_URL = {"Form W-9": "https://www.irs.gov/pub/irs-pdf/fw9.pdf",
             "Form W-9 (B-notice)": "https://www.irs.gov/pub/irs-pdf/fw9.pdf",
             "Form W-8BEN / W-8BEN-E": "https://www.irs.gov/pub/irs-pdf/fw8ben.pdf (individuals) · "
                                       "https://www.irs.gov/pub/irs-pdf/fw8bene.pdf (entities)",
             "Confirmation": ""}


def resolicitation_list(r: Dict, company: str, due=None) -> List[Dict]:
    from datetime import date
    due = due or date(r["year"] + 1, 1, 15)               # ahead of the Jan 31 1099-NEC deadline
    rows = {v["vendor"]: v for v in r["vendors"]}
    by: Dict[str, List[Dict]] = defaultdict(list)
    for e in r["exceptions"]:
        if e["exception"] in _ASK and e["vendor"] not in ("—", ""):
            for name in _vendors_of(e):
                by[name].append(e)
    out = []
    order = {"high": 0, "medium": 1, "low": 2}
    for vendor, es in by.items():
        v = rows.get(vendor, {})
        es = sorted(es, key=lambda e: order[e["severity"]])
        asks = [_ASK[e["exception"]] for e in es]
        form = next((a[0] for a in asks if a[0] != "Confirmation"), "Confirmation")
        if any(a[0].startswith("Form W-8") for a in asks):
            form = "Form W-8BEN / W-8BEN-E"
        reasons = list(dict.fromkeys(a[1] for a in asks))
        b_notice = any(e["exception"].startswith("IRS:") for e in es)
        greeting = v.get("contact") or vendor
        body = (f"Hello {greeting},\n\nBefore we file {r['year']} information returns, {company} needs "
                + ("an updated " if form != "Confirmation" else "a confirmation of your tax details for ")
                + (f"{form} for {vendor}" if form != "Confirmation" else vendor) + ", because "
                + "; ".join(reasons) + ".\n\n"
                + (f"Please complete, sign and date the current form ({_FORM_URL[form]}) and return it by {due:%B %d, %Y}."
                   if form != "Confirmation" else f"Please reply to confirm your legal name and taxpayer identification number by {due:%B %d, %Y}.")
                + ("\n\nIf we don't receive it, federal law requires us to withhold 24% (backup withholding) from future payments."
                   if any(e["severity"] == "high" for e in es) else "")
                + f"\n\nThank you,\n{company} Accounts Payable")
        out.append({"priority": es[0]["severity"], "vendor": vendor, "contact": v.get("contact") or "",
                    "email": v.get("email") or "", "request": form, "reasons": "; ".join(reasons),
                    "b_notice": "yes — follow the IRS B-notice procedure (Pub 1281)" if b_notice else "",
                    "paid": v.get("paid", 0.0), "due_by": due, "subject": f"{form} needed — {company}",
                    "message": body})
    return sorted(out, key=lambda x: (order[x["priority"]], -x["paid"]))


# Exceptions that make a filed 1099's name/TIN pair wrong (or the 1099 impossible to file correctly)
_PENALTY_KINDS = {"Missing W-9", "Missing TIN", "Invalid TIN format", "TIN type doesn't fit the entity",
                  "Conflicting TINs for one vendor", "Name doesn't match vendor master",
                  "IRS: name/TIN mismatch", "IRS: TIN not issued", "IRS: TIN missing or malformed"}


def penalty_estimate(r: Dict, hourly_rate: Optional[float] = None) -> Dict:
    """
    Three numbers instead of one scary one:
      penalty_if_filed_as_is  per-return 6721 + 6722 penalties at the after-Aug-1 tier for every
                              reportable vendor whose 1099 would carry a wrong or missing name/TIN
      worst_case              24% backup withholding the payer should have withheld (already in r)
      fix_cost                staff hours × rate to clear every exception before filing
    """
    R = rules()
    due_year = r["year"] + 1
    tiers, used = by_year(R["info_return_penalties"], due_year)
    per_return = tiers["after_aug_1"] * 2                        # IRS copy + payee copy
    reportable = {v["vendor"] for v in r["vendors"] if v["reportable"]}
    at_risk = sorted({e["vendor"] for e in r["exceptions"] if e["exception"] in _PENALTY_KINDS and e["vendor"] in reportable})
    hours_tbl = R["remediation_hours"]
    hours = sum(hours_tbl.get(e["exception"], hours_tbl["default"]) for e in r["exceptions"])
    rate = hourly_rate if hourly_rate is not None else R["remediation_hourly_rate"]
    early = tiers["within_30_days"] * 2
    return {
        "returns_due": due_year, "tier_year": used, "per_vendor": per_return, "vendors_at_risk": len(at_risk),
        "penalty_if_filed_as_is": round(len(at_risk) * per_return, 2),
        "penalty_if_corrected_in_30_days": round(len(at_risk) * early, 2),
        "worst_case_backup_withholding": r["exposure"],
        "fix_hours": round(hours, 1), "fix_cost": round(hours * rate, 2), "hourly_rate": rate,
        "assumptions": [
            f"{len(at_risk)} reportable vendors whose {r['year']} 1099-NEC would carry a missing, invalid or mismatched name/TIN",
            f"${tiers['after_aug_1']} per return (returns due {due_year}, not corrected by Aug 1) × 2 — IRS copy (IRC 6721) "
            "and payee copy (6722); intentional disregard not assumed",
            f"Fixed within 30 days of the deadline: ${tiers['within_30_days']} × 2 per vendor",
            "Penalties are often waived for reasonable cause when the payer solicited the TIN and followed B-notice "
            "procedures — the as-is figure assumes no waiver",
            "Worst case = 24% backup withholding on reportable payments to vendors without a valid TIN; the IRS can "
            "assess it against the payer, but usually only after B-notices are ignored",
            f"Fix cost = {hours:.1f} staff hours × ${rate:,.0f}/hr (hours per exception type in tax_rules.yaml)",
        ]}


def run_folder(forms_dir: Path, vendors_file: Optional[Path], year: int, workers: int = 8,
               tin_match_dir: Optional[Path] = None, stem: str = "client") -> Dict:
    files = sorted(p for p in Path(forms_dir).rglob("*") if p.suffix.lower() in FORM_EXTS)
    def one(p: Path) -> Dict:
        return parse_vendor_form(read_text(p), p.name, keep_tin=tin_match_dir is not None)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        forms = list(pool.map(one, files))
    vendors = load_vendors(vendors_file, year) if vendors_file else {
        (f.get("name") or f["file"]): {"paid": 0.0, "services": True, "rows": 0} for f in forms if f["kind"] == "W-9"}
    r = audit(forms, vendors, year)
    if tin_match_dir is not None:
        from app.services.tax.tin_matching import build_submission
        files_, map_path, n, skipped = build_submission([f for f in forms if f["kind"] == "W-9"], tin_match_dir, stem)
        r["tin_match"] = {"files": [str(x) for x in files_], "map": str(map_path), "records": n, "skipped": skipped}
    for f in forms:
        f.pop("_tin", None)                                 # full TINs leave memory with the submission file
    r["penalties"] = penalty_estimate(r)
    return r


# ── Onboarding: check one W-9 when a vendor is created ──────────────────────

def onboard_check(path: Path, vendor_name: Optional[str] = None) -> Dict:
    """
    pass   — nothing found
    review — medium issues (name mismatch, unsigned, foreign address…) — a person decides
    fail   — the TIN can't be used (missing / invalid / wrong type) or the form isn't a W-9
    """
    f = parse_vendor_form(read_text(path), path.name)
    checks: List[Dict] = []
    if f["kind"] == "W-8":
        return {"status": "review", "form": "W-8", "fields": {"name": f.get("name")},
                "checks": [{"check": "Form type", "ok": True,
                            "detail": "W-8 (foreign payee): no 1099; Chapter 3/4 withholding rules apply instead"}]}
    if f["kind"] != "W-9":
        return {"status": "fail", "form": None, "fields": {},
                "checks": [{"check": "Form type", "ok": False,
                            "detail": "Not a W-9 or W-8" if f["readable"] else "Unreadable — upload a clearer scan"}]}
    issues = dict(w9_issues(f))
    for kind in ("Missing TIN", "Invalid TIN format", "TIN type doesn't fit the entity", "W-9 not signed",
                 "W-9 not dated", "No tax classification checked", "Outdated W-9 revision",
                 "Foreign address — W-8 likely required"):
        checks.append({"check": kind.replace("Missing TIN", "TIN present").replace("Invalid TIN format", "TIN format"),
                       "ok": kind not in issues, "severity": SEVERITY[kind], "detail": issues.get(kind, "OK")})
    if vendor_name:
        s1, s2 = _sim(vendor_name, f.get("name") or ""), _sim(vendor_name, f.get("business_name") or "")
        ok = s1 >= 0.75
        checks.append({"check": "Name matches the vendor record", "ok": ok, "severity": SEVERITY["Name doesn't match vendor master"],
                       "detail": "OK" if ok else
                       (f"Vendor “{vendor_name}” matches line 2 (DBA) only; set up the 1099 name as line 1 “{f.get('name')}”"
                        if s2 >= 0.75 else f"Vendor “{vendor_name}” vs W-9 line 1 “{f.get('name') or '—'}”")})
    bad = [c for c in checks if not c["ok"]]
    status = "fail" if any(c["severity"] == "high" for c in bad) else "review" if bad else "pass"
    return {"status": status, "form": "W-9",
            "fields": {k: f.get(k) for k in ("name", "business_name", "classification", "tin_kind", "tin_last4",
                                            "revision", "address")},
            "checks": checks,
            "next_step": {"pass": "Approve the vendor; schedule IRS TIN Matching before the first 1099",
                          "review": "Route to AP review before approving",
                          "fail": "Request a corrected W-9 before paying — or start 24% backup withholding"}[status]}


# ── Report ───────────────────────────────────────────────────────────────────

def write_reports(r: Dict, out_dir: Path, company: str) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_") or "client"
    xlsx = out_dir / f"Audit_Exception_Report_{stem}_{r['year']}.xlsx"
    by_kind = pd.Series([e["exception"] for e in r["exceptions"]]).value_counts()
    summary = pd.DataFrame([
        ("Company", company), ("Payment year", r["year"]), ("Forms reviewed", r["forms"]),
        ("W-9s", r["w9"]), ("W-8s", r["w8"]), ("Vendors", len(r["vendors"])),
        ("Vendors with exceptions", r["vendors_with_exceptions"]), ("Exceptions", len(r["exceptions"])),
        ("1099-NEC threshold", r["threshold"])], columns=["Item", "Value"])
    resol = pd.DataFrame(resolicitation_list(r, company))
    nv = new_vendor_rate(r)
    r["_resol_count"], r["_new_vendor_rate"] = len(resol), nv
    summary = pd.concat([summary, pd.DataFrame([
        (f"Vendors added in the {nv['months']} months to {nv['as_of']:%b %d, %Y}", nv["new_vendors"]),
        ("…with an exception", f"{nv['new_with_exceptions']} ({nv['new_rate']}%)" if nv["new_rate"] is not None else "—"),
        ("Exception rate, older vendors", f"{nv['older_rate']}%" if nv["older_rate"] is not None else "—"),
        ("Vendor start date taken from", nv["date_source"]),
        ("Vendors to re-solicit", len(resol))], columns=["Item", "Value"])])
    pe = r.get("penalties") or penalty_estimate(r)
    summary = pd.concat([summary, pd.DataFrame([
        ("Penalty if 1099s are filed as-is", pe["penalty_if_filed_as_is"]),
        ("Penalty if corrected within 30 days", pe["penalty_if_corrected_in_30_days"]),
        ("Cost to fix before filing", pe["fix_cost"]),
        ("Worst case: backup withholding (24%)", pe["worst_case_backup_withholding"])]
        + [(f"Assumption {i}", a) for i, a in enumerate(pe["assumptions"], 1)], columns=["Item", "Value"])])
    if r.get("irs_results"):
        summary = pd.concat([summary, pd.DataFrame([
            ("IRS TIN Matching: records checked", len(r["irs_results"])),
            ("IRS TIN Matching: mismatches / not issued", sum(1 for x in r["irs_results"] if x["severity"] == "high"))],
            columns=["Item", "Value"])])
    with pd.ExcelWriter(xlsx) as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        by_kind.rename_axis("Exception").reset_index(name="Count").to_excel(xw, sheet_name="By type", index=False)
        pd.DataFrame(r["exceptions"]).to_excel(xw, sheet_name="Exceptions", index=False)
        pd.DataFrame(r["vendors"]).to_excel(xw, sheet_name="Vendors", index=False)
        if r.get("irs_results"):
            pd.DataFrame(r["irs_results"]).to_excel(xw, sheet_name="IRS TIN Matching", index=False)
        resol.to_excel(xw, sheet_name="Re-solicitation", index=False)
    resol.to_csv(out_dir / f"W9_Resolicitation_{stem}_{r['year']}.csv", index=False)
    html = out_dir / f"Audit_Exception_Report_{stem}_{r['year']}.html"
    html.write_text(_html(r, company, by_kind))
    return xlsx, html


def _recur_html(nv) -> str:
    if not nv or nv["new_rate"] is None:
        return ""
    old = f" — older vendors: {nv['older_rate']}%" if nv["older_rate"] is not None else ""
    return (f"<h2>Is it still happening?</h2><p><b>{nv['new_with_exceptions']} of {nv['new_vendors']}</b> vendors added in the "
            f"{nv['months']} months to {nv['as_of']:%b %d, %Y} (<b>{nv['new_rate']}%</b>) already have an exception{old}. "
            f"<span class='note'>Start dates from: {_esc(nv['date_source'])}.</span></p>")


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html(r: Dict, company: str, by_kind) -> str:
    pe = r.get("penalties") or penalty_estimate(r)
    sev_color = {"high": "#b42318", "medium": "#b54708", "low": "#475467"}
    rows = "".join(
        f"<tr><td><span class='sev' style='background:{sev_color[e['severity']]}'>{e['severity']}</span></td>"
        f"<td>{_esc(e['exception'])}</td><td>{_esc(e['vendor'])}</td><td class='n'>"
        f"{'$%s' % format(e['paid'], ',.2f') if e['paid'] else '—'}</td><td>{_esc(e['detail'])}</td><td>{_esc(e['file'])}</td></tr>"
        for e in r["exceptions"])
    kinds = "".join(f"<tr><td>{_esc(k)}</td><td class='n'>{v}</td></tr>" for k, v in by_kind.items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit Exception Report — {_esc(company)}</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#101828;background:#fff;margin:0;padding:32px 16px}}
main{{max-width:1100px;margin:auto}} h1{{font-size:24px;margin:0 0 4px}} .sub{{color:#475467;margin:0 0 24px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:24px}}
.kpi{{border:1px solid #e4e7ec;border-radius:8px;padding:12px 14px}} .kpi b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}
.kpi span{{color:#475467;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;margin-bottom:24px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #eaecf0;vertical-align:top}} th{{font-size:12px;color:#475467;text-transform:uppercase;letter-spacing:.04em}}
.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.sev{{color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;text-transform:uppercase}}
.note{{color:#475467;font-size:12px}}
</style></head><body><main>
<h1>Vendor Tax Compliance — Audit Exception Report</h1>
<p class="sub">{_esc(company)} · payments in {r['year']} · processed locally; full TINs never stored or shown</p>
<div class="kpis">
<div class="kpi"><span>Forms reviewed</span><b>{r['forms']:,}</b></div>
<div class="kpi"><span>Vendors</span><b>{len(r['vendors']):,}</b></div>
<div class="kpi"><span>With exceptions</span><b>{r['vendors_with_exceptions']:,}</b></div>
<div class="kpi"><span>Exceptions</span><b>{len(r['exceptions']):,}</b></div>
</div>
{_recur_html(r.get("_new_vendor_rate"))}
<h2>What it could cost</h2>
<div class="kpis">
<div class="kpi"><span>Penalty if filed as-is</span><b>${pe['penalty_if_filed_as_is']:,.0f}</b></div>
<div class="kpi"><span>If corrected within 30 days</span><b>${pe['penalty_if_corrected_in_30_days']:,.0f}</b></div>
<div class="kpi"><span>Cost to fix now</span><b>${pe['fix_cost']:,.0f}</b></div>
<div class="kpi"><span>Worst case (24% backup withholding)</span><b>${pe['worst_case_backup_withholding']:,.0f}</b></div>
</div>
<ul class="note">{''.join(f"<li>{_esc(a)}</li>" for a in pe['assumptions'])}</ul>
<h2>By type</h2><div class="wrap"><table><tr><th>Exception</th><th class="n">Count</th></tr>{kinds}</table></div>
<h2>Exceptions</h2><div class="wrap"><table><tr><th>Severity</th><th>Exception</th><th>Vendor</th><th class="n">Paid</th><th>Detail</th><th>File</th></tr>{rows}</table></div>
<p class="note">Format checks only: IRS TIN Matching confirms each name/TIN pair. Exposure = 24% backup withholding on
{r['year']} service payments ≥ ${r['threshold']:,} to vendors without a valid TIN on file. For review — not a tax determination.</p>
</main></body></html>"""
