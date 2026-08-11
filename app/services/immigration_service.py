"""
Immigration document service.

Responsibilities:
  1. Parse raw analyzer output → structured ClientImmigration record
  2. Upsert into client_immigration when a doc is linked to a client
  3. Compute expiry alerts for the alert scheduler
  4. Daily scanner that fires alerts at 90/60/30/7/1 day thresholds
"""
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.client_immigration import ClientImmigration

logger = logging.getLogger(__name__)

# ── Doc-type classification ───────────────────────────────────────────────────

_DOC_TYPE_MAP = {
    # H-series
    r"h[-\s]?1b?":                      ("h1b",       "H-1B Approval Notice"),
    r"h[-\s]?2a":                        ("h2a",       "H-2A Agricultural Worker"),
    r"h[-\s]?2b":                        ("h2b",       "H-2B Temporary Non-Ag Worker"),
    r"h[-\s]?4":                         ("h4",        "H-4 Dependent"),
    # L-series
    r"l[-\s]?1a":                        ("l1a",       "L-1A Intracompany Manager"),
    r"l[-\s]?1b":                        ("l1b",       "L-1B Intracompany Specialist"),
    r"l[-\s]?1\b":                       ("l1",        "L-1 Intracompany Transfer"),
    # O-series
    r"o[-\s]?1":                         ("o1",        "O-1 Extraordinary Ability"),
    # TN
    r"\btn\b":                           ("tn",        "TN NAFTA Professional"),
    # F/J
    r"f[-\s]?1":                         ("f1",        "F-1 Student"),
    r"i[-\s]?20":                        ("i20",       "I-20 Student Authorization"),
    r"j[-\s]?1":                         ("j1",        "J-1 Exchange Visitor"),
    r"ds[-\s]?2019":                     ("ds2019",    "DS-2019 J-1 Authorization"),
    # Work Authorization
    r"ead|employment\s+authorization":   ("ead",       "EAD Work Authorization Card"),
    r"i[-\s]?766":                       ("ead",       "EAD (Form I-766)"),
    # I-94
    r"i[-\s]?94":                        ("i94",       "I-94 Arrival/Departure Record"),
    # Green Card / LPR
    r"green\s+card|lawful\s+permanent|lpr|i[-\s]?551": ("green_card", "Green Card (LPR)"),
    # Passport
    r"passport":                         ("passport",  "Passport"),
    # B visas
    r"b[-\s]?1\s*/\s*b[-\s]?2|b1/b2|b[-\s]?2": ("b1b2", "B-1/B-2 Visitor Visa"),
    # Approval / Receipt notices
    r"i[-\s]?797":                       ("i797",      "Form I-797 Notice of Action"),
    r"i[-\s]?140":                       ("i140",      "I-140 Immigrant Petition"),
    r"i[-\s]?485":                       ("i485",      "I-485 Adjustment of Status"),
    r"receipt\s+notice|notice\s+of\s+action": ("i797", "USCIS Notice of Action"),
}

# Alert thresholds in days before expiry
ALERT_THRESHOLDS = [90, 60, 30, 7, 1]

URGENCY_LABEL = {
    "expired":  ("🔴 Expired",   "expired"),
    "critical": ("🔴 Critical",  "critical"),
    "high":     ("🟠 Expiring Soon", "high"),
    "medium":   ("🟡 Renew Soon", "medium"),
    "low":      ("🟢 Valid",      "low"),
    "unknown":  ("⚪ Unknown",    "unknown"),
}


# ── Type detection ────────────────────────────────────────────────────────────

def detect_doc_type(text: str, fields: dict) -> tuple[str, str]:
    """Return (doc_type_code, human_label) from raw text + extracted fields."""
    combined = (text + " " + " ".join(str(v) for v in fields.values())).lower()
    for pattern, (code, label) in _DOC_TYPE_MAP.items():
        if re.search(pattern, combined, re.I):
            return code, label
    return "other", "Immigration Document"


# ── Date parsing ──────────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b",          # MM/DD/YYYY
    r"\b(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})\b",           # YYYY-MM-DD
    r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b",           # Month DD, YYYY
    r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b",             # DD Month YYYY
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1
)}


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    s = s.strip()
    for pat in _DATE_PATTERNS:
        m = re.search(pat, s, re.I)
        if not m:
            continue
        g = m.groups()
        try:
            if len(g) == 3:
                a, b, c = g
                if a.isdigit() and b.isdigit() and c.isdigit():
                    ai, bi, ci = int(a), int(b), int(c)
                    if ci > 1900:                        # YYYY last
                        if ai > 12:                      # DD/MM/YYYY
                            return date(ci, bi, ai)
                        return date(ci, ai, bi) if ai <= 12 else date(ci, bi, ai)
                    elif ai > 1900:                      # YYYY first
                        return date(ai, bi, ci)
                else:
                    # Month name present
                    parts = [a, b, c]
                    alpha = next((p for p in parts if not p.isdigit()), None)
                    nums  = [p for p in parts if p.isdigit()]
                    if alpha and len(nums) == 2:
                        mon = _MONTHS.get(alpha[:3].lower())
                        if mon:
                            n1, n2 = int(nums[0]), int(nums[1])
                            year = n2 if n2 > 1900 else n1
                            day  = n1 if n2 > 1900 else n2
                            return date(year, mon, day)
        except (ValueError, OverflowError):
            continue
    return None


# ── Field mapping from analyzer output ───────────────────────────────────────

_EXPIRY_KEYS = [
    "expiration_date", "valid_until", "authorized_until",
    "valid_to", "expiry", "expiry_date", "date_of_expiry",
    "card_expires", "document_expiry", "validity_end",
]
_ADMIT_UNTIL_KEYS = [
    "admit_until", "admitted_until", "authorized_stay_until",
    "stay_until", "authorized_to", "i94_until", "depart_by",
]
_ISSUE_KEYS = [
    "issue_date", "issued_date", "issuance_date", "date_issued",
    "notice_date", "approval_date",
]
_VALID_FROM_KEYS = [
    "valid_from", "validity_begin", "effective_date", "start_date",
    "authorized_from",
]
_PRIORITY_KEYS = [
    "priority_date", "pd",
]


def _first_date(fields: dict, keys: list[str]) -> Optional[date]:
    for k in keys:
        val = fields.get(k)
        if val:
            d = _parse_date(str(val))
            if d:
                return d
    return None


def _employer(fields: dict) -> Optional[str]:
    for k in ["employer", "petitioner", "employer_name", "sponsor", "organization"]:
        if fields.get(k):
            return str(fields[k])
    return None


def _beneficiary(fields: dict) -> Optional[str]:
    for k in ["beneficiary", "applicant", "worker", "alien_name", "employee_name",
              "student_name", "full_name", "name"]:
        if fields.get(k):
            return str(fields[k])
    return None


def _doc_number(fields: dict) -> Optional[str]:
    for k in ["receipt_number", "document_number", "case_number", "alien_number",
              "uscis_number", "i94_number", "card_number", "passport_number"]:
        if fields.get(k):
            return str(fields[k])
    return None


# ── Main upsert ───────────────────────────────────────────────────────────────

def upsert_immigration_from_doc(
    db: Session,
    client_id: str,
    document_id: str,
    analyzer_result: dict,
) -> Optional[ClientImmigration]:
    """
    Called after ImmigrationReceiptAnalyzer runs on a doc linked to a client.
    Creates or updates the client_immigration record.
    """
    try:
        fields: dict = analyzer_result.get("fields", {}) or {}
        raw_text: str = analyzer_result.get("text", "") or ""

        doc_type, doc_label = detect_doc_type(raw_text, fields)
        visa_class = fields.get("visa_class") or fields.get("classification") or fields.get("visa_type")

        rec = db.query(ClientImmigration).filter_by(
            client_id=client_id,
            linked_document_id=document_id,
        ).first()

        if not rec:
            rec = ClientImmigration(id=str(uuid.uuid4()))
            db.add(rec)

        rec.client_id           = client_id
        rec.linked_document_id  = document_id
        rec.doc_type            = doc_type
        rec.doc_label           = doc_label
        rec.document_number     = _doc_number(fields)
        rec.visa_class          = visa_class
        rec.beneficiary_name    = _beneficiary(fields)
        rec.employer_sponsor    = _employer(fields)
        rec.issuing_authority   = fields.get("authority") or fields.get("issuing_office")
        rec.issue_date          = _first_date(fields, _ISSUE_KEYS)
        rec.valid_from          = _first_date(fields, _VALID_FROM_KEYS)
        rec.valid_until         = _first_date(fields, _EXPIRY_KEYS)
        rec.authorized_stay_until = _first_date(fields, _ADMIT_UNTIL_KEYS)
        rec.priority_date       = _first_date(fields, _PRIORITY_KEYS)
        rec.raw_extracted       = analyzer_result
        rec.status              = "active"

        # Auto-mark expired
        exp = rec.primary_expiry
        if exp and exp < date.today():
            rec.status = "expired"

        db.commit()
        db.refresh(rec)
        logger.info(f"✅ Immigration record upserted: client={client_id} type={doc_type} expiry={rec.valid_until}")
        return rec

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Failed to upsert immigration record: {e}")
        return None


# ── Serializer ────────────────────────────────────────────────────────────────

def serialize(rec: ClientImmigration) -> dict:
    days = rec.days_to_expiry
    urgency_key = rec.urgency
    label, badge = URGENCY_LABEL.get(urgency_key, ("⚪ Unknown", "unknown"))
    return {
        "id":                   rec.id,
        "client_id":            rec.client_id,
        "linked_document_id":   rec.linked_document_id,
        "doc_type":             rec.doc_type,
        "doc_label":            rec.doc_label or rec.doc_type.upper(),
        "document_number":      rec.document_number,
        "visa_class":           rec.visa_class,
        "beneficiary_name":     rec.beneficiary_name,
        "employer_sponsor":     rec.employer_sponsor,
        "issuing_authority":    rec.issuing_authority,
        "issue_date":           rec.issue_date.isoformat()      if rec.issue_date      else None,
        "valid_from":           rec.valid_from.isoformat()      if rec.valid_from      else None,
        "valid_until":          rec.valid_until.isoformat()     if rec.valid_until     else None,
        "authorized_stay_until":rec.authorized_stay_until.isoformat() if rec.authorized_stay_until else None,
        "priority_date":        rec.priority_date.isoformat()   if rec.priority_date   else None,
        "status":               rec.status,
        "urgency":              urgency_key,
        "urgency_label":        label,
        "urgency_badge":        badge,
        "days_to_expiry":       days,
        "notes":                rec.notes,
        "created_at":           rec.created_at.isoformat() if rec.created_at else None,
        "updated_at":           rec.updated_at.isoformat() if rec.updated_at else None,
    }


# ── Alert scanner (called by daily scheduler) ─────────────────────────────────

def scan_expiry_alerts(db: Session) -> list[dict]:
    """
    Scan all active immigration records. Return list of alert dicts to be
    persisted / emailed. Called once daily from scheduler.py.
    """
    today = date.today()
    alerts = []

    records = (
        db.query(ClientImmigration)
        .filter(ClientImmigration.status == "active")
        .all()
    )

    for rec in records:
        exp = rec.primary_expiry
        if not exp:
            continue
        days = (exp - today).days

        # Auto-expire
        if days < 0 and rec.status == "active":
            rec.status = "expired"
            db.add(rec)

        # Fire alerts at threshold days
        for threshold in ALERT_THRESHOLDS:
            if days == threshold:
                alerts.append({
                    "client_id":    rec.client_id,
                    "immigration_id": rec.id,
                    "doc_label":    rec.doc_label or rec.doc_type.upper(),
                    "document_number": rec.document_number,
                    "beneficiary":  rec.beneficiary_name,
                    "expiry_date":  exp.isoformat(),
                    "days_left":    days,
                    "urgency":      rec.urgency,
                    "message": (
                        f"{rec.doc_label or rec.doc_type.upper()} "
                        f"{'for ' + rec.beneficiary_name if rec.beneficiary_name else ''} "
                        f"expires in {days} day{'s' if days != 1 else ''}."
                    ).strip(),
                })

    try:
        db.commit()
    except Exception:
        db.rollback()

    logger.info(f"🗓 Immigration scan: {len(alerts)} alerts triggered today")
    return alerts
