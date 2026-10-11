import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth_router import get_current_user
from app.models.policy import Policy
from app.models.client import Client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ams/policies", tags=["policies"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class PolicyCreate(BaseModel):
    client_id:        str
    policy_number:    str
    line_of_business: str
    carrier_name:     str
    carrier_code:     Optional[str]  = None
    premium_amount:   Optional[float] = None
    premium_freq:     str             = 'annual'
    payment_method:   Optional[str]  = None
    effective_date:   str
    expiry_date:      str
    renewal_date:     Optional[str]  = None
    coverage_summary: Optional[dict] = None
    status:           str             = 'active'
    renewal_status:   str             = 'not_started'
    renewal_notes:    Optional[str]  = None
    assigned_agent:   Optional[str]  = None
    notes:            Optional[str]  = None


class PolicyUpdate(BaseModel):
    policy_number:    Optional[str]   = None
    line_of_business: Optional[str]   = None
    carrier_name:     Optional[str]   = None
    carrier_code:     Optional[str]   = None
    premium_amount:   Optional[float] = None
    premium_freq:     Optional[str]   = None
    payment_method:   Optional[str]   = None
    effective_date:   Optional[str]   = None
    expiry_date:      Optional[str]   = None
    renewal_date:     Optional[str]   = None
    coverage_summary: Optional[dict]  = None
    status:           Optional[str]   = None
    renewal_status:   Optional[str]   = None
    renewal_notes:    Optional[str]   = None
    last_contacted_at: Optional[str]  = None
    assigned_agent:   Optional[str]   = None
    notes:            Optional[str]   = None


class RenewalStatusUpdate(BaseModel):
    renewal_status:    str
    renewal_notes:     Optional[str] = None
    last_contacted_at: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _policy_to_dict(policy: Policy, client: Client = None) -> dict:
    today = date.today()
    days_to_expiry = None
    if policy.expiry_date:
        days_to_expiry = (policy.expiry_date - today).days

    return {
        "id":               policy.id,
        "policy_number":    policy.policy_number,
        "client_id":        policy.client_id,
        "client_name":      client.display_name if client else None,
        "client_number":    client.client_number if client else None,
        "client_type":      client.client_type if client else None,
        "line_of_business": policy.line_of_business,
        "carrier_name":     policy.carrier_name,
        "carrier_code":     policy.carrier_code,
        "premium_amount":   float(policy.premium_amount) if policy.premium_amount else None,
        "premium_freq":     policy.premium_freq,
        "payment_method":   policy.payment_method,
        "effective_date":   policy.effective_date.isoformat() if policy.effective_date else None,
        "expiry_date":      policy.expiry_date.isoformat() if policy.expiry_date else None,
        "renewal_date":     policy.renewal_date.isoformat() if policy.renewal_date else None,
        "days_to_expiry":   days_to_expiry,
        "coverage_summary": policy.coverage_summary or {},
        "status":           policy.status,
        "renewal_status":   policy.renewal_status,
        "renewal_notes":    policy.renewal_notes,
        "last_contacted_at": policy.last_contacted_at.isoformat() if policy.last_contacted_at else None,
        "risk_score":       float(policy.risk_score) if policy.risk_score else None,
        "risk_flags":       policy.risk_flags or [],
        "ai_insights":      policy.ai_insights or [],
        "assigned_agent":   policy.assigned_agent,
        "source":           policy.source,
        "notes":            policy.notes,
        "created_by":       policy.created_by,
        "created_at":       policy.created_at.isoformat() if policy.created_at else None,
        "updated_at":       policy.updated_at.isoformat() if policy.updated_at else None,
    }


def _get_client(db: Session, client_id: str) -> Client:
    return db.query(Client).filter(Client.id == client_id).first()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def get_policies(
        skip:             int           = Query(0, ge=0),
        limit:            int           = Query(100, ge=1, le=200),
        search:           Optional[str] = None,
        client_id:        Optional[str] = None,
        line_of_business: Optional[str] = None,
        carrier_name:     Optional[str] = None,
        status:           Optional[str] = None,
        renewal_status:   Optional[str] = None,
        expiring_days:    Optional[int] = None,
        show_deleted:     bool          = Query(False),   # ← NEW
        db:               Session       = Depends(get_db)
):
    query = db.query(Policy)

    # ── Always filter deleted unless explicitly requested ──────────────────────
    if not show_deleted:
        query = query.filter(Policy.status != 'deleted')

    if search:
        term = f"%{search}%"
        query = query.filter(or_(
            Policy.policy_number.ilike(term),
            Policy.carrier_name.ilike(term),
        ))
    if client_id:
        query = query.filter(Policy.client_id == client_id)
    if line_of_business:
        query = query.filter(Policy.line_of_business == line_of_business)
    if carrier_name:
        query = query.filter(Policy.carrier_name.ilike(f"%{carrier_name}%"))
    if status:
        query = query.filter(Policy.status == status)
    if renewal_status:
        query = query.filter(Policy.renewal_status == renewal_status)
    if expiring_days is not None:
        today  = date.today()
        cutoff = today + timedelta(days=expiring_days)
        query  = query.filter(
            Policy.expiry_date >= today,
            Policy.expiry_date <= cutoff
        )

    policies = query.order_by(Policy.expiry_date.asc()).offset(skip).limit(limit).all()
    result   = [_policy_to_dict(p, _get_client(db, p.client_id)) for p in policies]
    return {"policies": result, "total": len(result)}


@router.post("")
async def create_policy(
        payload:      PolicyCreate,
        db:           Session = Depends(get_db),
        current_user: dict    = Depends(get_current_user),
):
    client = _get_client(db, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        policy = Policy(
            client_id        = payload.client_id,
            policy_number    = payload.policy_number,
            line_of_business = payload.line_of_business,
            carrier_name     = payload.carrier_name,
            carrier_code     = payload.carrier_code,
            premium_amount   = payload.premium_amount,
            premium_freq     = payload.premium_freq,
            payment_method   = payload.payment_method,
            effective_date   = date.fromisoformat(payload.effective_date),
            expiry_date      = date.fromisoformat(payload.expiry_date),
            renewal_date     = date.fromisoformat(payload.renewal_date) if payload.renewal_date else None,
            coverage_summary = payload.coverage_summary or {},
            status           = payload.status,
            renewal_status   = payload.renewal_status,
            renewal_notes    = payload.renewal_notes,
            assigned_agent   = payload.assigned_agent,
            notes            = payload.notes,
            source           = 'manual',
            tenant_id        = current_user["tenant_id"],
            created_by       = current_user["id"],
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
        logger.info(f"✅ Policy created: {policy.policy_number}")
        return _policy_to_dict(policy, client)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{policy_id}")
async def get_policy(policy_id: str, db: Session = Depends(get_db)):
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _policy_to_dict(policy, _get_client(db, policy.client_id))


@router.put("/{policy_id}")
async def update_policy(policy_id: str, payload: PolicyUpdate, db: Session = Depends(get_db)):
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    update_data = payload.dict(exclude_none=True)
    date_fields = {'effective_date', 'expiry_date', 'renewal_date'}
    dt_fields   = {'last_contacted_at'}

    for field, value in update_data.items():
        if field in date_fields and value:
            setattr(policy, field, date.fromisoformat(value))
        elif field in dt_fields and value:
            setattr(policy, field, datetime.fromisoformat(value))
        else:
            setattr(policy, field, value)

    db.commit()
    db.refresh(policy)
    logger.info(f"✅ Policy updated: {policy.policy_number}")
    return _policy_to_dict(policy, _get_client(db, policy.client_id))


@router.delete("/{policy_id}")
async def delete_policy(policy_id: str, db: Session = Depends(get_db)):
    """Soft-delete — sets status to 'deleted'. Hidden from UI by default."""
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    policy.status = 'deleted'          # ← was 'cancelled', now 'deleted'
    db.commit()
    logger.info(f"🗑️ Policy soft-deleted: {policy.policy_number}")
    return {"message": "Policy deleted", "id": policy_id}


@router.patch("/{policy_id}/restore")
async def restore_policy(policy_id: str, db: Session = Depends(get_db)):
    """Restore a soft-deleted policy back to active."""
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    if policy.status != 'deleted':
        raise HTTPException(status_code=400, detail="Policy is not deleted")

    policy.status = 'active'
    db.commit()
    db.refresh(policy)
    logger.info(f"↩️ Policy restored: {policy.policy_number}")
    return _policy_to_dict(policy, _get_client(db, policy.client_id))


@router.patch("/{policy_id}/renewal-status")
async def update_renewal_status(
        policy_id: str,
        payload:   RenewalStatusUpdate,
        db:        Session = Depends(get_db)
):
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    policy.renewal_status = payload.renewal_status
    if payload.renewal_notes:
        policy.renewal_notes = payload.renewal_notes
    if payload.last_contacted_at:
        policy.last_contacted_at = datetime.fromisoformat(payload.last_contacted_at)

    db.commit()
    db.refresh(policy)
    logger.info(f"🔄 Renewal status: {policy.policy_number} → {policy.renewal_status}")
    return _policy_to_dict(policy, _get_client(db, policy.client_id))


# ── Renewal Pipeline ──────────────────────────────────────────────────────────

@router.get("/pipeline/renewals")
async def get_renewal_pipeline(db: Session = Depends(get_db)):
    today  = date.today()
    day7   = today + timedelta(days=7)
    day30  = today + timedelta(days=30)
    day90  = today + timedelta(days=90)

    def _fetch(from_date, to_date):
        policies = db.query(Policy).filter(
            Policy.expiry_date >= from_date,
            Policy.expiry_date <= to_date,
            Policy.status.in_(['active', 'renewal_due'])
        ).order_by(Policy.expiry_date.asc()).all()
        return [_policy_to_dict(p, _get_client(db, p.client_id)) for p in policies]

    urgent  = _fetch(today, day7)
    soon    = _fetch(day7,  day30)
    planned = _fetch(day30, day90)
    all_exp = urgent + soon + planned

    return {
        "urgent":  {"policies": urgent,  "count": len(urgent),  "days": "0-7"},
        "soon":    {"policies": soon,    "count": len(soon),    "days": "8-30"},
        "planned": {"policies": planned, "count": len(planned), "days": "31-90"},
        "summary": {
            "total_expiring":        len(all_exp),
            "total_premium_at_risk": round(sum(p["premium_amount"] or 0 for p in all_exp), 2),
            "urgent_count":          len(urgent),
            "soon_count":            len(soon),
            "planned_count":         len(planned),
        }
    }