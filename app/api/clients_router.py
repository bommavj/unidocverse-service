import logging
from datetime import date
from typing import Optional
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth_router import get_current_user
from app.models.client import Client
from app.models.document import Document
from app.models.policy import Policy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ams/clients", tags=["clients"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ClientCreate(BaseModel):
    client_type:   str = 'personal'
    # personal
    first_name:    Optional[str] = None
    last_name:     Optional[str] = None
    date_of_birth: Optional[str] = None
    ssn_last4:     Optional[str] = None
    # commercial
    business_name: Optional[str] = None
    business_type: Optional[str] = None
    ein_last4:     Optional[str] = None
    # shared
    email:          Optional[str] = None
    phone:          Optional[str] = None
    mobile:         Optional[str] = None
    address_line1:  Optional[str] = None
    address_line2:  Optional[str] = None
    city:           Optional[str] = None
    state:          Optional[str] = None
    zip:            Optional[str] = None
    country:        str = 'US'
    assigned_agent: Optional[str] = None
    status:         str = 'active'
    notes:          Optional[str] = None
    tags:           Optional[list] = None
    avatar_color:   str = '#6366f1'


class ClientUpdate(BaseModel):
    client_type:    Optional[str] = None
    first_name:     Optional[str] = None
    last_name:      Optional[str] = None
    date_of_birth:  Optional[str] = None
    ssn_last4:      Optional[str] = None
    business_name:  Optional[str] = None
    business_type:  Optional[str] = None
    ein_last4:      Optional[str] = None
    email:          Optional[str] = None
    phone:          Optional[str] = None
    mobile:         Optional[str] = None
    address_line1:  Optional[str] = None
    address_line2:  Optional[str] = None
    city:           Optional[str] = None
    state:          Optional[str] = None
    zip:            Optional[str] = None
    country:        Optional[str] = None
    assigned_agent: Optional[str] = None
    status:         Optional[str] = None
    notes:          Optional[str] = None
    tags:           Optional[list] = None
    avatar_color:   Optional[str] = None


class ConfirmDocLink(BaseModel):
    confirmed: bool   # True = confirm, False = reject


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_to_dict(client: Client, policies=None, doc_count: int = 0) -> dict:
    """Serialize a Client model to a response dict."""
    return {
        "id":             client.id,
        "client_number":  client.client_number,
        "client_type":    client.client_type,
        "display_name":   client.display_name,
        # personal
        "first_name":     client.first_name,
        "last_name":      client.last_name,
        "date_of_birth":  client.date_of_birth.isoformat() if client.date_of_birth else None,
        "ssn_last4":      client.ssn_last4,
        # commercial
        "business_name":  client.business_name,
        "business_type":  client.business_type,
        "ein_last4":      client.ein_last4,
        # contact
        "email":          client.email,
        "phone":          client.phone,
        "mobile":         client.mobile,
        # address
        "address_line1":  client.address_line1,
        "address_line2":  client.address_line2,
        "city":           client.city,
        "state":          client.state,
        "zip":            client.zip,
        "country":        client.country,
        # agency
        "assigned_agent": client.assigned_agent,
        "status":         client.status,
        "source":         client.source,
        "notes":          client.notes,
        "tags":           client.tags or [],
        "avatar_color":   client.avatar_color,
        # stats
        "policy_count":   len(policies) if policies is not None else 0,
        "total_premium":  sum(float(p.premium_amount or 0) for p in (policies or [])),
        "nearest_renewal_days": min(
            ((p.expiry_date - __import__('datetime').date.today()).days
             for p in (policies or [])
             if p.expiry_date and (p.expiry_date - __import__('datetime').date.today()).days >= 0),
            default=None
        ),
        "doc_count":      doc_count,
        # audit
        "created_by":     client.created_by,
        "created_at":     client.created_at.isoformat() if client.created_at else None,
        "updated_at":     client.updated_at.isoformat() if client.updated_at else None,
    }


def _generate_client_number(db: Session) -> str:
    """Generate next CLI-XXXX number."""
    result = db.execute(text(
        "SELECT client_number FROM clients ORDER BY created_at DESC LIMIT 1"
    )).fetchone()
    if result:
        try:
            last_num = int(result[0].split('-')[1])
            return f"CLI-{(last_num + 1):04d}"
        except Exception:
            pass
    return "CLI-0001"


def _similarity_score(a: str, b: str) -> float:
    """Simple string similarity 0-1."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def get_clients(
        skip:        int           = Query(0, ge=0),
        limit:       int           = Query(100, ge=1, le=200),
        search:      Optional[str] = None,
        client_type: Optional[str] = None,
        status:      Optional[str] = None,
        db:          Session       = Depends(get_db)
):
    """List clients with optional search and filters."""
    query = db.query(Client).filter(Client.status != 'deleted')

    # Text search across name, email, business name
    if search:
        term = f"%{search}%"
        query = query.filter(or_(
            Client.first_name.ilike(term),
            Client.last_name.ilike(term),
            Client.business_name.ilike(term),
            Client.email.ilike(term),
            Client.client_number.ilike(term),
            Client.phone.ilike(term),
        ))

    if client_type:
        query = query.filter(Client.client_type == client_type)
    if status:
        query = query.filter(Client.status == status)

    clients = query.order_by(Client.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    for client in clients:
        # Get doc count
        doc_count = db.query(Document).filter(
            Document.client_id == client.id,
            Document.client_link_status == 'confirmed'
        ).count()
        # Get active policies for stats
        policies = db.query(Policy).filter(
            Policy.client_id == client.id,
            Policy.status.notin_(['deleted', 'cancelled'])
        ).all()
        result.append(_client_to_dict(client, policies=policies, doc_count=doc_count))

    return {"clients": result, "total": len(result)}


@router.post("")
async def create_client(
        payload:      ClientCreate,
        db:           Session = Depends(get_db),
        current_user: dict    = Depends(get_current_user),
):
    """Create a new client."""
    try:
        client_number = _generate_client_number(db)

        client = Client(
            client_number  = client_number,
            client_type    = payload.client_type,
            first_name     = payload.first_name,
            last_name      = payload.last_name,
            date_of_birth  = date.fromisoformat(payload.date_of_birth) if payload.date_of_birth else None,
            ssn_last4      = payload.ssn_last4,
            business_name  = payload.business_name,
            business_type  = payload.business_type,
            ein_last4      = payload.ein_last4,
            email          = payload.email,
            phone          = payload.phone,
            mobile         = payload.mobile,
            address_line1  = payload.address_line1,
            address_line2  = payload.address_line2,
            city           = payload.city,
            state          = payload.state,
            zip            = payload.zip,
            country        = payload.country,
            assigned_agent = payload.assigned_agent,
            status         = payload.status,
            notes          = payload.notes,
            tags           = payload.tags,
            avatar_color   = payload.avatar_color,
            source         = 'manual',
            tenant_id      = current_user["tenant_id"],
            created_by     = current_user["id"],
        )

        db.add(client)
        db.commit()
        db.refresh(client)

        logger.info(f"✅ Client created: {client.client_number} — {client.display_name}")
        return _client_to_dict(client)

    except Exception as e:
        logger.error(f"❌ Error creating client: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{client_id}")
async def get_client(
        client_id: str,
        db:        Session = Depends(get_db)
):
    """Get full client detail including policies and linked documents."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Linked policies
    from app.models.policy import Policy
    policies = db.query(Policy).filter(Policy.client_id == client_id).all()

    # Confirmed documents
    docs = db.query(Document).filter(
        Document.client_id == client_id,
        Document.client_link_status == 'confirmed'
    ).order_by(Document.created_at.desc()).all()

    doc_list = [
        {
            "id":         d.id,
            "filename":   d.filename,
            "doc_type":   d.doc_type,
            "file_size":  d.file_size,
            "status":     d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]

    policy_list = []
    from datetime import date as dt
    for p in policies:
        days = (p.expiry_date - dt.today()).days if p.expiry_date else None
        policy_list.append({
            "id":               p.id,
            "policy_number":    p.policy_number,
            "line_of_business": p.line_of_business,
            "carrier_name":     p.carrier_name,
            "premium_amount":   float(p.premium_amount) if p.premium_amount else None,
            "premium_freq":     p.premium_freq,
            "effective_date":   p.effective_date.isoformat() if p.effective_date else None,
            "expiry_date":      p.expiry_date.isoformat() if p.expiry_date else None,
            "status":           p.status,
            "renewal_status":   p.renewal_status,
            "days_to_expiry":   days,
            "risk_score":       float(p.risk_score) if p.risk_score else None,
            "risk_flags":       p.risk_flags or [],
        })

    result = _client_to_dict(client, policies=policies, doc_count=len(docs))
    result["policies"]  = policy_list
    result["documents"] = doc_list
    return result


@router.put("/{client_id}")
async def update_client(
        client_id: str,
        payload:   ClientUpdate,
        db:        Session = Depends(get_db)
):
    """Update client fields."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    update_data = payload.dict(exclude_none=True)
    for field, value in update_data.items():
        if field == 'date_of_birth' and value:
            setattr(client, field, date.fromisoformat(value))
        else:
            setattr(client, field, value)

    db.commit()
    db.refresh(client)
    logger.info(f"✅ Client updated: {client.client_number}")
    return _client_to_dict(client)


@router.delete("/{client_id}")
async def delete_client(
        client_id: str,
        db:        Session = Depends(get_db)
):
    """Soft-delete a client."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    client.status = 'deleted'
    db.commit()
    logger.info(f"🗑️ Client soft-deleted: {client.client_number}")
    return {"message": "Client deleted", "id": client_id}


# ── Document Linking ──────────────────────────────────────────────────────────

@router.get("/{client_id}/documents")
async def get_client_documents(
        client_id: str,
        status:    Optional[str] = None,   # confirmed | suggested | unlinked
        db:        Session       = Depends(get_db)
):
    """Get all documents linked or suggested for this client."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    query = db.query(Document).filter(
        or_(
            Document.client_id == client_id,
            Document.client_suggested_id == client_id
        )
    )
    if status:
        query = query.filter(Document.client_link_status == status)

    docs = query.order_by(Document.created_at.desc()).all()

    return {
        "client_id":    client_id,
        "client_name":  client.display_name,
        "documents": [
            {
                "id":                  d.id,
                "filename":            d.filename,
                "doc_type":            d.doc_type,
                "file_size":           d.file_size,
                "status":              d.status,
                "client_link_status":  d.client_link_status,
                "client_link_score":   float(d.client_link_score) if d.client_link_score else None,
                "created_at":          d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    }


@router.post("/{client_id}/suggest-docs")
async def suggest_documents_for_client(
        client_id: str,
        db:        Session = Depends(get_db)
):
    """
    AI auto-suggest: scan unlinked documents and suggest matches for this client.
    Uses fuzzy name matching on extracted document metadata.
    Confidence >= 0.75 → suggested, shown to agent for confirmation.
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Get all unlinked documents
    unlinked_docs = db.query(Document).filter(
        Document.client_link_status == 'unlinked',
        Document.status == 'completed'
    ).all()

    suggested = []
    client_name = client.display_name.lower()

    for doc in unlinked_docs:
        score = 0.0

        # Try matching against insured name in doc_metadata
        if doc.doc_metadata:
            insured = doc.doc_metadata.get("insured_name") or \
                      doc.doc_metadata.get("client_name") or \
                      doc.doc_metadata.get("policyholder") or ""

            if insured:
                score = _similarity_score(client_name, insured)

        # Fallback: match against filename
        if score < 0.5:
            filename_score = _similarity_score(
                client_name,
                doc.filename.replace('_', ' ').replace('-', ' ')
            )
            score = max(score, filename_score * 0.8)  # filename match weighted lower

        # Suggest if score is high enough
        if score >= 0.60:
            doc.client_suggested_id = client_id
            doc.client_link_status  = 'suggested'
            doc.client_link_score   = round(score, 2)
            suggested.append({
                "id":                 doc.id,
                "filename":           doc.filename,
                "doc_type":           doc.doc_type,
                "client_link_score":  round(score, 2),
                "client_link_status": 'suggested',
            })

    db.commit()

    logger.info(f"🔍 Suggested {len(suggested)} docs for client {client.client_number}")
    return {
        "client_id":      client_id,
        "client_name":    client.display_name,
        "suggested_count": len(suggested),
        "suggestions":    suggested
    }


@router.post("/{client_id}/confirm-doc/{doc_id}")
async def confirm_document_link(
        client_id:   str,
        doc_id:      str,
        payload:     ConfirmDocLink,
        confirmed_by: Optional[str] = Query(None),
        db:          Session         = Depends(get_db)
):
    """
    Agent confirms or rejects an AI document suggestion.
    confirmed=True  → links document to client
    confirmed=False → rejects suggestion, document stays unlinked
    """
    from datetime import datetime

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if payload.confirmed:
        doc.client_id           = client_id
        doc.client_link_status  = 'confirmed'
        doc.client_linked_at    = datetime.utcnow()
        doc.client_linked_by    = confirmed_by
        doc.client_suggested_id = None
        logger.info(f"✅ Doc {doc.filename} confirmed for client {client.client_number}")
    else:
        doc.client_link_status  = 'rejected'
        doc.client_suggested_id = None
        doc.client_link_score   = None
        logger.info(f"✗ Doc {doc.filename} rejected for client {client.client_number}")

    db.commit()

    return {
        "doc_id":    doc_id,
        "client_id": client_id,
        "status":    doc.client_link_status,
        "confirmed": payload.confirmed
    }


@router.post("/{client_id}/confirm-all-docs")
async def confirm_all_suggested_docs(
        client_id:    str,
        confirmed_by: Optional[str] = Query(None),
        db:           Session        = Depends(get_db)
):
    """
    Confirm ALL suggested documents for this client in one click.
    Agent reviews the list, then hits confirm all.
    """
    from datetime import datetime

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    suggested_docs = db.query(Document).filter(
        Document.client_suggested_id == client_id,
        Document.client_link_status == 'suggested'
    ).all()

    confirmed_ids = []
    for doc in suggested_docs:
        doc.client_id           = client_id
        doc.client_link_status  = 'confirmed'
        doc.client_linked_at    = datetime.utcnow()
        doc.client_linked_by    = confirmed_by
        doc.client_suggested_id = None
        confirmed_ids.append(doc.id)

    db.commit()

    logger.info(f"✅ Bulk confirmed {len(confirmed_ids)} docs for client {client.client_number}")
    return {
        "client_id":      client_id,
        "client_name":    client.display_name,
        "confirmed_count": len(confirmed_ids),
        "confirmed_ids":  confirmed_ids
    }

# ── ADD to clients_router.py ──────────────────────────────────────────────────
# Client notes / activity log endpoints

from pydantic import BaseModel
from typing import Optional
import uuid as _uuid

class NoteCreate(BaseModel):
    note_type:  str = "note"   # note, call, email, meeting, task
    title:      Optional[str] = None
    body:       str

NOTE_TYPE_ICONS = {
    "note":    "📋",
    "call":    "📞",
    "email":   "📧",
    "meeting": "🤝",
    "task":    "✅",
}

@router.get("/{client_id}/notes")
async def get_client_notes(
    client_id: str,
    db: Session = Depends(get_db)
):
    """Get all notes + auto-activities for a client."""
    # Manual notes
    notes = db.execute(text("""
        SELECT id, note_type, title, body, created_by, created_at
        FROM client_notes
        WHERE client_id = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": client_id}).fetchall()

    # Auto-activities from renewal pipeline
    activities = db.execute(text("""
        SELECT ra.id, ra.activity_type as note_type,
               ra.title, ra.created_by, ra.created_at
        FROM renewal_activities ra
        JOIN policies p ON p.id = ra.policy_id
        WHERE p.client_id = :cid
        ORDER BY ra.created_at DESC
        LIMIT 20
    """), {"cid": client_id}).fetchall()

    result = []
    for n in notes:
        r = dict(n._mapping)
        r["source"] = "manual"
        r["icon"]   = NOTE_TYPE_ICONS.get(r["note_type"], "📋")
        r["id"]     = str(r["id"])
        result.append(r)

    for a in activities:
        r = dict(a._mapping)
        r["source"] = "auto"
        r["icon"]   = NOTE_TYPE_ICONS.get(r["note_type"], "🔄")
        r["id"]     = str(r["id"])
        r["body"]   = None
        result.append(r)

    # Sort combined by date
    result.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return {"notes": result, "total": len(result)}


@router.post("/{client_id}/notes")
async def add_client_note(
    client_id:    str,
    payload:      NoteCreate,
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """Add a manual note or activity to a client."""
    note_id = str(_uuid.uuid4())
    by = current_user["username"]
    db.execute(text("""
        INSERT INTO client_notes
            (id, client_id, note_type, title, body, created_by)
        VALUES
            (:id, :cid, :type, :title, :body, :by)
    """), {
        "id":    note_id,
        "cid":   client_id,
        "type":  payload.note_type,
        "title": payload.title,
        "body":  payload.body,
        "by":    by,
    })
    db.commit()
    return {
        "id":         note_id,
        "note_type":  payload.note_type,
        "title":      payload.title,
        "body":       payload.body,
        "created_by": by,
        "icon":       NOTE_TYPE_ICONS.get(payload.note_type, "📋"),
        "source":     "manual",
    }


@router.delete("/{client_id}/notes/{note_id}")
async def delete_client_note(
    client_id: str,
    note_id:   str,
    db:        Session = Depends(get_db)
):
    """Delete a manual note."""
    db.execute(text("""
        DELETE FROM client_notes
        WHERE id = :id AND client_id = :cid
    """), {"id": note_id, "cid": client_id})
    db.commit()
    return {"deleted": True}