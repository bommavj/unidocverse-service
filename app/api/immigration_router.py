"""
Immigration API — CRUD for client immigration records + expiry dashboard.
"""
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.client import Client
from app.models.client_immigration import ClientImmigration
from app.services.immigration_service import serialize, scan_expiry_alerts, URGENCY_LABEL
from app.services.immigration_cross_linker import reason as cross_link_reason

router = APIRouter(prefix="/api/immigration", tags=["immigration"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ImmigrationCreate(BaseModel):
    doc_type:               str
    doc_label:              Optional[str] = None
    document_number:        Optional[str] = None
    visa_class:             Optional[str] = None
    beneficiary_name:       Optional[str] = None
    employer_sponsor:       Optional[str] = None
    issuing_authority:      Optional[str] = None
    issue_date:             Optional[str] = None
    valid_from:             Optional[str] = None
    valid_until:            Optional[str] = None
    authorized_stay_until:  Optional[str] = None
    priority_date:          Optional[str] = None
    status:                 str = "active"
    notes:                  Optional[str] = None
    linked_document_id:     Optional[str] = None


class ImmigrationUpdate(ImmigrationCreate):
    doc_type: Optional[str] = None


def _parse(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ── GET /api/immigration/client/{client_id} ───────────────────────────────────

@router.get("/client/{client_id}")
def list_client_immigration(client_id: str, db: Session = Depends(get_db)):
    client = db.query(Client).filter_by(id=client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")

    records = (
        db.query(ClientImmigration)
        .filter_by(client_id=client_id)
        .order_by(ClientImmigration.valid_until.asc().nullslast())
        .all()
    )

    serialized = [serialize(r) for r in records]

    # Summary stats
    expired  = sum(1 for r in records if r.urgency == "expired")
    critical = sum(1 for r in records if r.urgency == "critical")
    high     = sum(1 for r in records if r.urgency == "high")
    active   = sum(1 for r in records if r.status == "active")

    return {
        "client_id": client_id,
        "records": serialized,
        "summary": {
            "total":    len(records),
            "active":   active,
            "expired":  expired,
            "critical": critical,
            "high":     high,
        },
    }


# ── POST /api/immigration/client/{client_id} ──────────────────────────────────

@router.post("/client/{client_id}", status_code=201)
def create_immigration(
    client_id: str,
    body: ImmigrationCreate,
    db: Session = Depends(get_db),
):
    if not db.query(Client).filter_by(id=client_id).first():
        raise HTTPException(404, "Client not found")

    rec = ClientImmigration(
        id                    = str(uuid.uuid4()),
        client_id             = client_id,
        doc_type              = body.doc_type,
        doc_label             = body.doc_label or body.doc_type.upper(),
        document_number       = body.document_number,
        visa_class            = body.visa_class,
        beneficiary_name      = body.beneficiary_name,
        employer_sponsor      = body.employer_sponsor,
        issuing_authority     = body.issuing_authority,
        issue_date            = _parse(body.issue_date),
        valid_from            = _parse(body.valid_from),
        valid_until           = _parse(body.valid_until),
        authorized_stay_until = _parse(body.authorized_stay_until),
        priority_date         = _parse(body.priority_date),
        status                = body.status,
        notes                 = body.notes,
        linked_document_id    = body.linked_document_id,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return serialize(rec)


# ── PATCH /api/immigration/{record_id} ───────────────────────────────────────

@router.patch("/{record_id}")
def update_immigration(
    record_id: str,
    body: ImmigrationUpdate,
    db: Session = Depends(get_db),
):
    rec = db.query(ClientImmigration).filter_by(id=record_id).first()
    if not rec:
        raise HTTPException(404, "Record not found")

    fields = body.model_dump(exclude_unset=True)
    date_fields = {"issue_date", "valid_from", "valid_until",
                   "authorized_stay_until", "priority_date"}
    for k, v in fields.items():
        if k in date_fields:
            setattr(rec, k, _parse(v))
        else:
            setattr(rec, k, v)

    db.commit()
    db.refresh(rec)
    return serialize(rec)


# ── DELETE /api/immigration/{record_id} ──────────────────────────────────────

@router.delete("/{record_id}", status_code=204)
def delete_immigration(record_id: str, db: Session = Depends(get_db)):
    rec = db.query(ClientImmigration).filter_by(id=record_id).first()
    if not rec:
        raise HTTPException(404, "Record not found")
    db.delete(rec)
    db.commit()


# ── GET /api/immigration/expiry-dashboard ─────────────────────────────────────

@router.get("/expiry-dashboard")
def expiry_dashboard(db: Session = Depends(get_db)):
    """All active records grouped by urgency — for the agency-wide expiry view."""
    records = (
        db.query(ClientImmigration)
        .filter(ClientImmigration.status == "active")
        .order_by(ClientImmigration.valid_until.asc().nullslast())
        .all()
    )

    buckets = {"expired": [], "critical": [], "high": [], "medium": [], "low": [], "unknown": []}
    for r in records:
        buckets.setdefault(r.urgency, []).append(serialize(r))

    return {
        "buckets": buckets,
        "counts":  {k: len(v) for k, v in buckets.items()},
        "total":   len(records),
    }


# ── POST /api/immigration/run-scan ───────────────────────────────────────────

@router.post("/run-scan")
def trigger_scan(db: Session = Depends(get_db)):
    """Manually trigger the daily expiry scan (admin use)."""
    alerts = scan_expiry_alerts(db)
    return {"alerts_triggered": len(alerts), "alerts": alerts}


# ── GET /api/immigration/cross-link/{client_id} ────────────────────────────────

@router.get("/cross-link/{client_id}")
def cross_link(client_id: str, db: Session = Depends(get_db)):
    """
    Cross-document immigration reasoning for a client.
    Groups all immigration records by beneficiary, then reasons across
    H-1B + I-140 + I-485 + EAD to produce combined insights, timeline,
    alerts, and recommended actions.
    """
    recs = (
        db.query(ClientImmigration)
        .filter(ClientImmigration.client_id == client_id)
        .all()
    )
    if not recs:
        raise HTTPException(404, "No immigration records found for this client")

    # Group by beneficiary name (normalize whitespace + case)
    groups: dict[str, list[ClientImmigration]] = {}
    for r in recs:
        key = (r.beneficiary_name or "unknown").strip().upper()
        groups.setdefault(key, []).append(r)

    analyses = []
    for bene_key, group_recs in groups.items():
        analysis = cross_link_reason(group_recs)
        analysis["records"] = [serialize(r) for r in group_recs]
        analyses.append(analysis)

    # Sort: most urgent first
    def _urgency_sort(a: dict) -> int:
        severities = [al.get("severity", "info") for al in a.get("alerts", [])]
        if "critical" in severities:
            return 0
        if "warning" in severities:
            return 1
        return 2

    analyses.sort(key=_urgency_sort)

    return {
        "client_id":    client_id,
        "beneficiaries": len(analyses),
        "analyses":     analyses,
    }


# ── GET /api/immigration/cross-link-by-doc ────────────────────────────────────

@router.get("/cross-link-by-doc/{document_id}")
def cross_link_by_doc(document_id: str, db: Session = Depends(get_db)):
    """
    Given any immigration document ID, find all records for the same
    beneficiary (across any client) and return cross-linked analysis.
    Useful when docs are not yet linked to a client.
    """
    anchor = db.query(ClientImmigration).filter(
        ClientImmigration.linked_document_id == document_id
    ).first()

    if not anchor:
        # Try to find any records with matching beneficiary from documents table
        from app.models.document import Document
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")

        # Pull beneficiary name from doc metadata
        meta = doc.doc_metadata or {}
        analyzed = meta.get("analyzed_data") or {}
        fields = analyzed.get("fields") or {}
        bene_name = fields.get("beneficiary") or fields.get("applicant")
        if not bene_name:
            return {
                "document_id": document_id,
                "message": "Document has no cross-linked immigration records and beneficiary name could not be determined.",
                "analysis": cross_link_reason([]),
            }

        # Find immigration records with same beneficiary name
        bene_upper = bene_name.strip().upper()
        all_recs = db.query(ClientImmigration).all()
        matched = [r for r in all_recs if (r.beneficiary_name or "").strip().upper() == bene_upper]

        return {
            "document_id":    document_id,
            "beneficiary":    bene_name,
            "matched_records": len(matched),
            "analysis":        cross_link_reason(matched),
            "records":         [serialize(r) for r in matched],
        }

    # Anchor found — load all records for same client + beneficiary
    recs = (
        db.query(ClientImmigration)
        .filter(
            ClientImmigration.client_id == anchor.client_id,
        )
        .all()
    )
    bene_key = (anchor.beneficiary_name or "").strip().upper()
    group = [r for r in recs if (r.beneficiary_name or "").strip().upper() == bene_key]

    analysis = cross_link_reason(group)
    analysis["records"] = [serialize(r) for r in group]

    return {
        "document_id": document_id,
        "client_id":   anchor.client_id,
        "analysis":    analysis,
    }
