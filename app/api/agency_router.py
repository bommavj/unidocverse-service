import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date

from app.core.database import get_db
from app.api.auth_router import get_current_user
from app.models.document import Document
from app.models.client import Client
from app.models.policy import Policy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agency", tags=["agency"])


@router.post("/create-from-document/{doc_id}")
async def create_from_document(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Extract client + policy data from a processed declarations page document
    and create records in the database.
    Called when agent clicks 'Create Client & Policy' from the document banner.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.doc_type != 'declarations_page':
        raise HTTPException(status_code=400, detail="Document is not a declarations page")

    meta = doc.doc_metadata or {}

    # ── Extract fields from analyzer output ──────────────────────────────────
    fields          = meta.get("analyzed_data").get("fields", {})
    carrier_display = meta.get("analyzed_data").get("carrier_display") or meta.get("carrier") or "Unknown Carrier"

    named_insured   = fields.get("named_insured", "")
    policy_number   = fields.get("policy_number", "")
    effective_date  = fields.get("effective_date_parsed") or fields.get("effective_date")
    expiry_date     = fields.get("expiration_date_parsed") or fields.get("expiration_date")
    total_premium   = fields.get("total_premium")
    premium_freq    = fields.get("premium_freq", "annual")
    coverage_a      = fields.get("coverage_a_dwelling")
    coverage_e      = fields.get("coverage_e_personal_liability")
    deductible      = fields.get("deductible")
    state           = fields.get("state", "")

    if not named_insured and not policy_number:
        raise HTTPException(
            status_code=422,
            detail="Could not extract enough data from this document. Ensure it has been fully processed."
        )

    # ── Parse insured name into first/last ────────────────────────────────────
    # named_insured may be a list, a stringified list, or a plain string
    import re as _re
    raw_name = named_insured[0] if isinstance(named_insured, list) else str(named_insured)
    # Strip Python list artifacts like ['...'] or ['...',
    raw_name = _re.sub(r"^\[?['\"]?|['\"]?\]?$", "", raw_name.strip())
    # Take only the first person (before & or comma)
    raw_name = _re.split(r"[&,]", raw_name)[0].strip().title()
    # Guard against section headers / junk (no real name has > 4 words or < 2 chars)
    name_parts = raw_name.split()
    if len(name_parts) > 4 or len(raw_name) < 2:
        raise HTTPException(status_code=422, detail=f"Could not parse a valid insured name from: {named_insured!r}")
    first_name = name_parts[0] if name_parts else raw_name
    last_name  = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    # ── Generate client number ────────────────────────────────────────────────
    result = db.execute(text(
        "SELECT client_number FROM clients ORDER BY created_at DESC LIMIT 1"
    )).fetchone()
    if result:
        try:
            last_num = int(result[0].split('-')[1])
            client_number = f"CLI-{(last_num + 1):04d}"
        except Exception:
            client_number = "CLI-0001"
    else:
        client_number = "CLI-0001"

    # ── Create client ─────────────────────────────────────────────────────────
    client = Client(
        client_number  = client_number,
        client_type    = 'personal',
        first_name     = first_name,
        last_name      = last_name,
        state          = state,
        status         = 'active',
        source         = 'document_ai',
        avatar_color   = '#6366f1',
        notes          = f"Auto-created from document: {doc.filename}",
        tenant_id      = current_user["tenant_id"],
        created_by     = current_user["id"],
    )
    db.add(client)
    db.flush()  # get client.id before creating policy

    # ── Determine line of business ────────────────────────────────────────────
    text_lower = (doc.raw_text or "").lower()
    if coverage_a:
        lob = 'homeowners'
    elif 'auto' in text_lower or 'vehicle' in text_lower:
        lob = 'auto'
    elif 'commercial' in text_lower or 'business' in text_lower:
        lob = 'commercial_property'
    elif 'umbrella' in text_lower:
        lob = 'umbrella'
    else:
        lob = 'other'

    # ── Parse dates ───────────────────────────────────────────────────────────
    def _to_date(val):
        if val is None:
            return date.today()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
                try:
                    from datetime import datetime
                    return datetime.strptime(val, fmt).date()
                except Exception:
                    pass
        return date.today()

    eff  = _to_date(effective_date)
    exp  = _to_date(expiry_date)

    # ── Build coverage summary ────────────────────────────────────────────────
    coverage_summary = {}
    if coverage_a:  coverage_summary["dwelling"]  = coverage_a
    if coverage_e:  coverage_summary["liability"] = coverage_e
    if deductible:  coverage_summary["deductible"] = deductible
    if fields.get("coverage_b_other_structures"):
        coverage_summary["other_structures"] = fields["coverage_b_other_structures"]
    if fields.get("coverage_c_personal_property"):
        coverage_summary["personal_property"] = fields["coverage_c_personal_property"]

    # ── Create policy ─────────────────────────────────────────────────────────
    policy = Policy(
        client_id        = client.id,
        policy_number    = policy_number or f"UNKNOWN-{doc_id[:8]}",
        line_of_business = lob,
        carrier_name     = carrier_display,
        premium_amount   = total_premium,
        premium_freq     = premium_freq,
        effective_date   = eff,
        expiry_date      = exp,
        coverage_summary = coverage_summary,
        status           = 'active',
        renewal_status   = 'not_started',
        source           = 'document_ai',
        notes            = f"Auto-created from document: {doc.filename}",
        tenant_id        = current_user["tenant_id"],
        created_by       = current_user["id"],
    )
    db.add(policy)
    db.flush()

    # ── Link document to client + policy ──────────────────────────────────────
    from datetime import datetime
    doc.client_id           = client.id
    doc.policy_id           = policy.id
    doc.client_link_status  = 'confirmed'
    doc.client_linked_at    = datetime.utcnow()
    doc.client_linked_by    = 'document_ai'

    db.commit()

    logger.info(
        f"✅ Created client {client.client_number} ({client.display_name}) "
        f"and policy {policy.policy_number} from doc {doc.filename}"
    )

    return {
        "client": {
            "id":             client.id,
            "client_number":  client.client_number,
            "display_name":   client.display_name,
        },
        "policy": {
            "id":               policy.id,
            "policy_number":    policy.policy_number,
            "line_of_business": policy.line_of_business,
            "carrier_name":     policy.carrier_name,
            "premium_amount":   float(policy.premium_amount) if policy.premium_amount else None,
            "expiry_date":      policy.expiry_date.isoformat() if policy.expiry_date else None,
        },
        "message": f"Client {client.display_name} and policy {policy.policy_number} created successfully"
    }


@router.get("/preview-from-document/{doc_id}")
async def preview_from_document(
    doc_id: str,
    db: Session = Depends(get_db)
):
    """
    Preview what client + policy data will be extracted from a document
    before actually creating the records. Called when banner is shown.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.doc_type != 'declarations_page':
        return {"eligible": False, "reason": "Not a declarations page"}

    # Already linked?
    if doc.client_link_status == 'confirmed' and doc.client_id:
        return {"eligible": False, "reason": "Already linked to a client"}

    meta   = doc.doc_metadata or {}
    fields = meta.get("analyzed_data").get("fields", {})

    named_insured   = fields.get("named_insured", "")
    policy_number   = fields.get("policy_number", "")
    expiry_date     = fields.get("expiration_date", "")
    total_premium   = fields.get("total_premium")
    carrier_display = meta.get("analyzed_data").get("carrier_display") or meta.get("carrier") or "Unknown Carrier"
    days_to_expiry  = fields.get("days_until_expiry")

    if not named_insured and not policy_number:
        return {"eligible": False, "reason": "Not enough data extracted"}

    return {
        "eligible":      True,
        "doc_id":        doc_id,
        "filename":      doc.filename,
        "named_insured": named_insured,
        "policy_number": policy_number,
        "carrier":       carrier_display,
        "expiry_date":   expiry_date,
        "premium":       total_premium,
        "days_to_expiry": days_to_expiry,
    }