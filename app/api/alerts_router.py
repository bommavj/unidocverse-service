# app/api/alerts_router.py
"""
Alerts endpoint — surfaces documents with upcoming or past expiry dates.
Queries analyzed_data.fields.days_until_expiry stored by specialist analyzers.
Works for any document type that produces expiry data (insurance, mortgage, contracts, etc.)
"""

import logging
import re
from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _severity(days: int) -> str:
    if days < 0:
        return "expired"
    elif days <= 30:
        return "critical"
    elif days <= 60:
        return "urgent"
    elif days <= 90:
        return "warning"
    return "ok"


def _severity_order(s: str) -> int:
    return {"expired": 0, "critical": 1, "urgent": 2, "warning": 3, "ok": 4}.get(s, 5)


_DATE_KEY = re.compile(r"(expir|due_date|valid_until|valid_to|renewal|lease_end|end_date|termination_date|"
                       r"next_due|installment_due|deadline)", re.I)
_LABELS = {"expir": "Expires", "due": "Due", "valid": "Valid until", "renew": "Renewal", "end": "Ends",
           "termination": "Terminates", "deadline": "Deadline"}


def _parse(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _label(key: str) -> str:
    k = key.lower()
    word = next((v for frag, v in _LABELS.items() if frag in k), "Date")
    return f"{word} ({key.replace('_', ' ')})"


def collect_deadlines(db: Session, tenant: str, horizon_days: int = 365, past_days: int = 365) -> list:
    """Every dated obligation the tenant's documents reveal, plus immigration expiries and tax filing deadlines."""
    today = date.today()
    out, seen = [], set()

    def add(doc_id, filename, doc_type, field, label, d: date, source: str, extra: Optional[dict] = None):
        days = (d - today).days
        if days > horizon_days or days < -past_days:
            return
        key = (filename, d)
        if key in seen:
            return
        seen.add(key)
        out.append({"id": doc_id or f"{source}:{filename}:{d}", "doc_id": doc_id, "filename": filename, "doc_type": doc_type,
                    "field": field, "label": label, "expiry_date": d.isoformat(), "due_date": d.isoformat(),
                    "days_until_expiry": days, "severity": _severity(days), "source": source,
                    "status_text": (f"Expired {-days} days ago" if days < 0 else "Due today" if days == 0
                                    else f"In {days} days"), **(extra or {})})

    rows = db.execute(text("""
        SELECT id::text AS id, filename, doc_type, doc_metadata, extracted_facts, summary
        FROM documents WHERE status = 'completed' AND tenant_id = :t
    """), {"t": tenant}).fetchall()
    for r in rows:
        meta = r.doc_metadata if isinstance(r.doc_metadata, dict) else {}
        analyzed = meta.get("analyzed_data") if isinstance(meta.get("analyzed_data"), dict) else {}
        fields = analyzed.get("fields") if isinstance(analyzed.get("fields"), dict) else {}
        candidates = []
        for k in ("expiration_date", "expiry_date", "policy_expiry"):
            if fields.get(k) or meta.get(k):
                candidates.append((k, fields.get(k) or meta.get(k)))
        facts = r.extracted_facts if isinstance(r.extracted_facts, dict) else {}
        candidates += [(k, v) for k, v in facts.items() if _DATE_KEY.search(k) and not isinstance(v, (dict, list))]
        for k, v in candidates:
            d = _parse(v)
            if d:
                add(r.id, r.filename, r.doc_type, k, _label(k), d, "document",
                    {"summary": (r.summary or "")[:150]})

    try:
        from app.services.immigration_tracker import tenant_records
        for rec in tenant_records(db, tenant):
            for k in ("valid_until", "authorized_stay_until"):
                d = _parse(rec.get(k))
                if d:
                    who = rec.get("beneficiary_name") or rec.get("source_file") or "—"
                    add(rec.get("linked_document_id"), rec.get("source_file") or rec.get("doc_label"), rec.get("doc_type"), k,
                        f"{rec.get('doc_label')} — {who}" + (" (I-94)" if k == "authorized_stay_until" else ""), d, "immigration")
    except Exception as e:
        logger.warning(f"Deadlines: immigration records skipped ({e})")

    try:
        from app.services.tax.rules import upcoming_deadlines
        for t in upcoming_deadlines(today, None, months=6):
            add(None, t["name"], "tax_deadline", "due_date", f"{t['authority']} filing deadline", t["due"], "tax")
    except Exception as e:
        logger.warning(f"Deadlines: tax calendar skipped ({e})")

    out.sort(key=lambda a: (_severity_order(a["severity"]), a["days_until_expiry"]))
    return out


def _tenant() -> str:
    from app.core.tenant_scope import current_tenant
    return str(current_tenant())


@router.get("/all")
def get_expiry_alerts(db: Session = Depends(get_db)):
    """Documents and obligations expiring within 90 days (or already expired) — the Alerts page."""
    alerts = [a for a in collect_deadlines(db, _tenant(), horizon_days=90, past_days=365)]
    counts = {"expired": 0, "critical": 0, "urgent": 0, "warning": 0}
    for a in alerts:
        if a["severity"] in counts:
            counts[a["severity"]] += 1
    return {"alerts": alerts, "counts": counts, "total": len(alerts), "as_of": date.today().isoformat()}


@router.get("/proactive")
def proactive(db: Session = Depends(get_db)):
    """Every dated obligation in the next year (and the past year's misses) — the Deadlines page."""
    alerts = collect_deadlines(db, _tenant())
    return {"alerts": alerts, "total": len(alerts), "as_of": date.today().isoformat()}
