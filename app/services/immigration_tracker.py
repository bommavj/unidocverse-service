"""
Visa / immigration expiry tracking straight from uploaded documents — rules only.

Client-linked records (client_immigration) need a document linked to an agency
client; a person's own uploads (passports, I-797 approval notices) never got
there, so the tracker stayed empty. This reads the tenant's immigration
documents directly:

  I-797 notice  "Class: H1B", "Valid from 12/08/2023 to 11/28/2026" (petition),
                "VALID FROM … UNTIL 12/08/2026" (I-94 admission, incl. grace period),
                beneficiary, petitioner, receipt number (masked), case type (I-129 / I-539…)
  Passport      "Date of Expiry 20/03/2027" (DD/MM/YYYY) or the MRZ line

Nothing is guessed: a field that isn't in the text stays empty.
"""
import re
from datetime import date
from typing import Dict, List, Optional

IMMIGRATION_DOC_TYPES = ["uscis_receipt", "immigration_receipt", "immigration_document", "visa_document", "visa", "passport",
                         "passport_document", "i94", "ead", "i797", "h1b", "i140"]

_URGENCY_LABEL = {"expired": "🔴 Expired", "critical": "🔴 Critical", "high": "🟠 Expiring Soon",
                  "medium": "🟡 Renew Soon", "low": "🟢 Valid", "unknown": "⚪ Unknown"}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_RECEIPT_PREFIX = r"(?:IOE|EAC|WAC|LIN|SRC|MSC|NBC|YSC|IOE)"


def urgency(days: Optional[int]) -> str:
    """Same thresholds as ClientImmigration.urgency."""
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 7:
        return "critical"
    if days <= 30:
        return "high"
    if days <= 90:
        return "medium"
    return "low"


def _us_date(s: str) -> Optional[date]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None
    except ValueError:
        return None


def _passport_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
    try:
        if m:                                   # passports print day first (20/03/2027)
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})", s)
        if m and m.group(2).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None
    return None


def _mask(receipt: str) -> str:
    return f"{receipt[:3]}•••••••{receipt[-3:]}" if receipt else None


def _title(name: str) -> str:
    """'RAMAN, PRIYA LAKSHMI' → 'Priya Lakshmi Raman'."""
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        name = f"{first} {last}"
    return " ".join(w.capitalize() for w in name.split())


def _company(name: str) -> str:
    keep = {"LLC", "LLP", "LP", "INC", "CORP", "CO", "LTD", "PC", "PLLC", "USA", "US"}
    return " ".join(w if w.rstrip(".,") in keep else w.capitalize() for w in name.strip().split())


def parse_uscis(text: str) -> Dict:
    t = text or ""
    cls = re.search(r"\bClass:?\s*([A-Z]{1,2}-?\d[A-Z]?)\b", t)
    visa_class = None
    if cls:
        c = cls.group(1).upper().replace("-", "")
        visa_class = re.sub(r"^([A-Z]+)(\d)", r"\1-\2", c)          # H1B → H-1B, H4 → H-4
    pet = re.search(r"Valid from\s+(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})", t, re.I)
    i94 = re.search(r"VALID FROM\s+\d{1,2}/\d{1,2}/\d{4}\s+UNTIL\s+(\d{1,2}/\d{1,2}/\d{4})", t)
    bene = re.search(r"Beneficiary\s+(?:\[[^\]]*\]\s*)?([A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\- ]+?)\s*(?:\||\n|$)", t)
    petitioner = re.search(r"Petitioner:?\s+([A-Z][A-Z0-9&.,' ]{3,60}?)\s*(?:\||\n|$)", t)
    receipt = re.search(r"Receipt Number\s+(" + _RECEIPT_PREFIX + r"\d{10}|10E\d{10})", t)
    case = re.search(r"Case Type\s+[I1l]-?(\d{3})", t)
    notice = re.search(r"Notice Type:\s*([A-Za-z ]+?Notice)", t)
    form = f"I-{case.group(1)}" if case else None
    label = " ".join(x for x in ((notice.group(1) if notice else "USCIS notice"), f"({form})" if form else "") if x)
    rec = receipt.group(1).replace("10E", "IOE", 1) if receipt else None
    return {"doc_label": f"I-797 {label}" if notice else label,
            "visa_class": visa_class,
            "valid_from": _us_date(pet.group(1)) if pet else None,
            "valid_until": _us_date(pet.group(2)) if pet else None,
            "authorized_stay_until": _us_date(i94.group(1)) if i94 else None,
            "beneficiary_name": _title(bene.group(1)) if bene else None,
            "employer_sponsor": _company(petitioner.group(1)) if petitioner and "BLANK" not in petitioner.group(1) else None,
            "document_number": _mask(rec)}


def parse_passport(text: str) -> Dict:
    t = text or ""
    exp = None
    m = re.search(r"Date of Expiry\s*[:\-]?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", t, re.I)
    if m:
        exp = _passport_date(m.group(1))
    name = None
    mrz1 = re.search(r"P<([A-Z]{3})([A-Z<]+)", t)
    mrz2 = re.search(r"([A-Z0-9<]{9})\d([A-Z]{3})(\d{6})\d[MF<](\d{2})(\d{2})(\d{2})\d", t)
    if mrz1:
        parts = [p.replace("<", " ").strip() for p in mrz1.group(2).split("<<") if p.strip("<")]
        if parts:
            name = " ".join(w.capitalize() for w in (" ".join(parts[1:]) + " " + parts[0]).split())
    if not exp and mrz2:
        yy, mm, dd = int(mrz2.group(4)), int(mrz2.group(5)), int(mrz2.group(6))
        try:
            exp = date(2000 + yy, mm, dd)
        except ValueError:
            exp = None
    number = mrz2.group(1).replace("<", "") if mrz2 else None
    return {"doc_label": "Passport", "visa_class": None, "valid_from": None, "valid_until": exp,
            "authorized_stay_until": None, "beneficiary_name": name, "employer_sponsor": None,
            "document_number": (f"•••••{number[-3:]}" if number else None)}


def _dmy(s: str) -> Optional[date]:
    """'06JUN2024' / '06 JUN 2024' (visa foils print day, month, year)."""
    m = re.match(r"(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})", (s or "").strip())
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))) if m and m.group(2).lower() in _MONTHS else None
    except ValueError:
        return None


def parse_visa(text: str) -> Dict:
    """US visa foil: class, issue/expiration dates, name from the machine-readable line, annotation (petitioner)."""
    t = (text or "").replace("&lt;", "<")
    cls = re.search(r"Visa\s*Type\s*/?\s*Class\s*[A-Z]?\s+([A-Z]{1,2}-?\d?[A-Z]?)\b", t, re.I)
    visa_class = None
    if cls:
        c = cls.group(1).upper().replace("-", "")
        visa_class = re.sub(r"^([A-Z]+)(\d)", r"\1-\2", c)
    exp = re.search(r"Expiration\s*Date\s*[:\-]?\s*(\d{1,2}\s*[A-Za-z]{3}\s*\d{4})", t, re.I)
    iss = re.search(r"Issue\s*Date\s*[:\-]?\s*(\d{1,2}\s*[A-Za-z]{3}\s*\d{4})", t, re.I)
    mrz = re.search(r"V[NC]USA([A-Z]+)<<([A-Z<]+)", t)
    name = None
    if mrz:
        name = " ".join(w.capitalize() for w in (mrz.group(2).replace("<", " ").split() + [mrz.group(1)]))
    pn = re.search(r"PN\s*[-:]\s*([A-Z][A-Z0-9&.,' ]{3,60}?)(?:\s+P#|\n|$)", t)
    post = re.search(r"Issuing\s*Post\s*Name\s*\n+\s*([A-Z][A-Z ]{2,30})", t)
    return {"doc_label": "US visa" + (f" ({post.group(1).strip().title()})" if post else ""),
            "visa_class": visa_class, "valid_from": _dmy(iss.group(1)) if iss else None,
            "valid_until": _dmy(exp.group(1)) if exp else None, "authorized_stay_until": None,
            "beneficiary_name": name, "employer_sponsor": _company(pn.group(1)) if pn else None,
            "document_number": None}


def from_documents(rows: List, today: Optional[date] = None) -> List[Dict]:
    """rows: documents (id, filename, doc_type, raw_text, created_at) → tracker records, newest upload wins per duplicate."""
    today = today or date.today()
    out, seen = [], set()
    for r in rows:
        dtype = (r.doc_type or "").lower()
        text = r.raw_text or ""
        is_visa = "visa" in dtype or re.search(r"V[NC]USA[A-Z&<]|Visa\s*Type\s*/?\s*Class", text, re.I)
        p = parse_passport(text) if "passport" in dtype else parse_visa(text) if is_visa else parse_uscis(text)
        key = (p["doc_label"], p["beneficiary_name"] or r.filename, p["valid_until"], p["document_number"])
        if key in seen:                                   # same document uploaded twice
            continue
        seen.add(key)
        dates = [d for d in (p["valid_until"], p["authorized_stay_until"]) if d]
        exp = min(dates) if dates else None
        days = (exp - today).days if exp else None
        u = urgency(days)
        notes = None
        if exp is None:
            notes = "Expiry date not readable in this scan — check the document"
        elif is_visa and "passport" not in dtype:
            notes = ("Visa stamp: needed to re-enter the U.S., not to stay — status follows your I-797 / I-94"
                     if days is not None and days < 0 else "Visa stamp: needed to re-enter the U.S.")
        out.append({
            "id": f"doc:{r.id}", "client_id": None, "linked_document_id": str(r.id), "source_file": r.filename,
            "doc_type": dtype, **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()},
            "beneficiary_name": p["beneficiary_name"] or None,
            "issuing_authority": "USCIS" if "passport" not in dtype else None,
            "issue_date": None, "priority_date": None, "status": "active",
            "urgency": u, "urgency_label": _URGENCY_LABEL[u], "urgency_badge": u, "days_to_expiry": days,
            "notes": notes,
        })
    return out


def tenant_records(db, tenant_id: str) -> List[Dict]:
    """
    Every immigration expiry the tenant has: client-linked records (agency clients) plus the
    tenant's own uploaded passports / USCIS notices that aren't linked to a client record.
    The Visa Tracker and the daily Immigration report both use this, so they always agree.
    """
    from sqlalchemy import text
    from app.models.client_immigration import ClientImmigration
    from app.services.immigration_service import serialize
    client_ids = [r[0] for r in db.execute(text("SELECT id::text FROM clients WHERE tenant_id = :t"), {"t": tenant_id})]
    linked_recs = (db.query(ClientImmigration)
                   .filter(ClientImmigration.status == "active", ClientImmigration.client_id.in_(client_ids))
                   .all()) if client_ids else []
    rows = [serialize(r) for r in linked_recs]
    linked = {str(r.linked_document_id) for r in linked_recs if r.linked_document_id}
    docs = db.execute(text(
        "SELECT id, filename, doc_type, raw_text, created_at FROM documents "
        "WHERE tenant_id = :t AND status = 'completed' AND doc_type = ANY(:types) ORDER BY created_at DESC"),
        {"t": tenant_id, "types": IMMIGRATION_DOC_TYPES}).fetchall()
    rows += from_documents([d for d in docs if str(d.id) not in linked])
    return sorted(rows, key=lambda r: (r["days_to_expiry"] is None, r["days_to_expiry"] or 0))
