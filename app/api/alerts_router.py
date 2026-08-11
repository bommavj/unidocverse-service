# app/api/alerts_router.py
"""
Alerts endpoint — surfaces documents with upcoming or past expiry dates.
Queries analyzed_data.fields.days_until_expiry stored by specialist analyzers.
Works for any document type that produces expiry data (insurance, mortgage, contracts, etc.)
"""

import logging
from datetime import date
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


@router.get("/all")
def get_expiry_alerts(db: Session = Depends(get_db)):
    """
    Returns all documents with expiry dates within 90 days (or already expired).
    Reads from doc_metadata JSONB field — no reprocessing needed.
    """
    try:
        # Query documents that have expiry data stored by specialist analyzers
        rows = db.execute(text("""
            SELECT
                id::text,
                filename,
                doc_type,
                created_at,
                updated_at,
                doc_metadata,
                summary
            FROM documents
            WHERE status = 'completed'
              AND doc_metadata IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 200
        """)).fetchall()

    except Exception as e:
        logger.error(f"❌ Alerts query failed: {e}")
        return {"alerts": [], "counts": {}, "error": str(e)}

    alerts = []
    today = date.today()

    for row in rows:
        metadata = row.doc_metadata or {}
        if not isinstance(metadata, dict):
            continue

        # Try multiple paths where expiry data may live
        expiry_date_str = None
        days_until = None

        # Path 1: analyzed_data.fields (DeclarationsAnalyzer, etc.)
        analyzed = metadata.get("analyzed_data") or {}
        fields = analyzed.get("fields") or {}

        if fields.get("expiration_date"):
            expiry_date_str = fields["expiration_date"]
            days_until = fields.get("days_until_expiry")

        # Path 2: direct metadata fields (some analyzers store at top level)
        if not expiry_date_str:
            expiry_date_str = (
                metadata.get("expiration_date")
                or metadata.get("expiry_date")
                or metadata.get("policy_expiry")
            )

        # Path 3: alerts array from analyzer (pre-computed)
        if not expiry_date_str:
            for alert in analyzed.get("alerts", []):
                if "expir" in alert.get("title", "").lower():
                    # Extract date from description if possible
                    import re
                    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", alert.get("description", ""))
                    if m:
                        expiry_date_str = m.group(1)
                    break

        if not expiry_date_str:
            continue

        # Calculate days_until if not already computed
        if days_until is None:
            try:
                from datetime import datetime
                for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
                    try:
                        exp = datetime.strptime(expiry_date_str, fmt).date()
                        days_until = (exp - today).days
                        break
                    except ValueError:
                        continue
            except Exception:
                continue

        if days_until is None:
            continue

        # Only surface within 90 days or expired
        if days_until > 90:
            continue

        severity = _severity(days_until)

        # Build human-readable status
        if days_until < 0:
            status_text = f"Expired {abs(days_until)} days ago"
        elif days_until == 0:
            status_text = "Expires TODAY"
        elif days_until == 1:
            status_text = "Expires tomorrow"
        else:
            status_text = f"Expires in {days_until} days"

        # Pull key coverage info for display
        coverage_info = {}
        if fields:
            if fields.get("total_premium"):
                coverage_info["premium"] = f"${fields['total_premium']:,.2f}"
            if fields.get("coverage_a_dwelling"):
                coverage_info["dwelling"] = f"${fields['coverage_a_dwelling']:,.0f}"
            if fields.get("named_insured"):
                coverage_info["insured"] = fields["named_insured"]
            if fields.get("policy_number"):
                coverage_info["policy_number"] = fields["policy_number"]

        alerts.append({
            "id": row.id,
            "filename": row.filename,
            "doc_type": row.doc_type,
            "expiry_date": expiry_date_str,
            "days_until_expiry": days_until,
            "severity": severity,
            "status_text": status_text,
            "coverage_info": coverage_info,
            "summary": (row.summary or "")[:150],
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })

    # Sort: expired first, then by days ascending
    alerts.sort(key=lambda a: (_severity_order(a["severity"]), a["days_until_expiry"]))

    # Count by severity
    counts = {"expired": 0, "critical": 0, "urgent": 0, "warning": 0}
    for a in alerts:
        s = a["severity"]
        if s in counts:
            counts[s] += 1

    logger.info(f"🔔 Alerts: {len(alerts)} documents expiring within 90 days")
    return {
        "alerts": alerts,
        "counts": counts,
        "total": len(alerts),
        "as_of": today.isoformat(),
    }