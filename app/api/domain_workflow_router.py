"""
Domain Workflow Pipelines — stage-based kanban for all business domains.

GET  /api/domain-workflow/config                     — all domain configs
GET  /api/domain-workflow/{domain}/items             — items with stage counts
POST /api/domain-workflow/{domain}/items             — create item
PATCH /api/domain-workflow/{domain}/items/{item_id}/stage — advance stage
GET  /api/domain-workflow/{domain}/items/{item_id}/history — stage history
"""

import uuid
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/domain-workflow", tags=["domain-workflow"])

# ── Domain definitions ────────────────────────────────────────────────────────

DOMAIN_CONFIGS: Dict[str, Dict] = {
    "procurement": {
        "label": "Procurement / AP",
        "icon": "shopping-cart",
        "stages": [
            {"id": "po_created",       "label": "PO Created",       "color": "#6366f1"},
            {"id": "goods_received",   "label": "Goods Received",   "color": "#8b5cf6"},
            {"id": "invoice_received", "label": "Invoice Received", "color": "#a78bfa"},
            {"id": "three_way_match",  "label": "3-Way Match",      "color": "#f59e0b"},
            {"id": "discrepancy_review","label": "Discrepancy Review","color": "#ef4444"},
            {"id": "approved",         "label": "Approved",         "color": "#10b981"},
            {"id": "payment_scheduled","label": "Payment Scheduled","color": "#059669"},
        ],
        "fields": ["vendor", "amount", "po_number", "invoice_number", "due_date"],
        "hitl_stages": ["discrepancy_review"],
    },
    "clm": {
        "label": "Contract Lifecycle",
        "icon": "file-text",
        "stages": [
            {"id": "draft",            "label": "Draft",            "color": "#6366f1"},
            {"id": "review",           "label": "Review",           "color": "#8b5cf6"},
            {"id": "pending_approval", "label": "Pending Approval", "color": "#f59e0b"},
            {"id": "active",           "label": "Active",           "color": "#10b981"},
            {"id": "expiring",         "label": "Expiring",         "color": "#ef4444"},
            {"id": "renewed",          "label": "Renewed",          "color": "#059669"},
            {"id": "terminated",       "label": "Terminated",       "color": "#6b7280"},
        ],
        "fields": ["counterparty", "value", "governing_law", "expiry_date"],
        "hitl_stages": ["pending_approval"],
    },
    "claims": {
        "label": "Insurance Claims",
        "icon": "shield",
        "stages": [
            {"id": "reported",      "label": "Reported",       "color": "#6366f1"},
            {"id": "under_review",  "label": "Under Review",   "color": "#8b5cf6"},
            {"id": "investigation", "label": "Investigation",  "color": "#f59e0b"},
            {"id": "negotiation",   "label": "Negotiation",    "color": "#fb923c"},
            {"id": "settled",       "label": "Settled",        "color": "#10b981"},
            {"id": "closed",        "label": "Closed",         "color": "#6b7280"},
            {"id": "denied",        "label": "Denied",         "color": "#ef4444"},
        ],
        "fields": ["claimant", "policy_number", "estimated_value", "incident_date"],
        "hitl_stages": ["investigation", "negotiation"],
    },
    "renewals": {
        "label": "Insurance Renewals",
        "icon": "refresh-cw",
        "stages": [
            {"id": "not_started", "label": "Not Started", "color": "#6b7280"},
            {"id": "contacted",   "label": "Contacted",   "color": "#6366f1"},
            {"id": "in_progress", "label": "In Progress", "color": "#f59e0b"},
            {"id": "quoted",      "label": "Quoted",      "color": "#8b5cf6"},
            {"id": "complete",    "label": "Complete",    "color": "#10b981"},
            {"id": "lapsed",      "label": "Lapsed",      "color": "#ef4444"},
        ],
        "fields": ["policy_holder", "policy_number", "expiry_date", "premium"],
        "hitl_stages": [],
    },
    "payroll": {
        "label": "Payroll",
        "icon": "dollar-sign",
        "stages": [
            {"id": "timesheet_submitted", "label": "Timesheet Submitted", "color": "#6366f1"},
            {"id": "manager_approved",    "label": "Manager Approved",    "color": "#8b5cf6"},
            {"id": "payroll_run",         "label": "Payroll Run",         "color": "#f59e0b"},
            {"id": "paystub_generated",   "label": "Paystub Generated",   "color": "#a78bfa"},
            {"id": "exception_handling",  "label": "Exception Handling",  "color": "#ef4444"},
            {"id": "journal_posted",      "label": "Journal Posted",      "color": "#10b981"},
        ],
        "fields": ["employee", "pay_period", "gross_pay", "net_pay", "department"],
        "hitl_stages": ["exception_handling"],
    },
    "medical": {
        "label": "Medical / Healthcare",
        "icon": "heart",
        "stages": [
            {"id": "patient_registered",    "label": "Patient Registered",    "color": "#6366f1"},
            {"id": "eligibility_check",     "label": "Eligibility Check",     "color": "#8b5cf6"},
            {"id": "service_authorization", "label": "Service Authorization", "color": "#f59e0b"},
            {"id": "claim_submitted",       "label": "Claim Submitted",       "color": "#a78bfa"},
            {"id": "eob_processed",         "label": "EOB Processed",         "color": "#fb923c"},
            {"id": "denial_management",     "label": "Denial Management",     "color": "#ef4444"},
            {"id": "payment_posted",        "label": "Payment Posted",        "color": "#10b981"},
        ],
        "fields": ["patient", "provider", "procedure_code", "insurance_id", "amount"],
        "hitl_stages": ["service_authorization", "denial_management"],
    },
    "legal": {
        "label": "Legal Matter Management",
        "icon": "briefcase",
        "stages": [
            {"id": "intake",            "label": "Intake",            "color": "#6366f1"},
            {"id": "conflict_check",    "label": "Conflict Check",    "color": "#8b5cf6"},
            {"id": "engagement_letter", "label": "Engagement Letter", "color": "#f59e0b"},
            {"id": "discovery",         "label": "Discovery",         "color": "#a78bfa"},
            {"id": "billing_review",    "label": "Billing Review",    "color": "#fb923c"},
            {"id": "settlement",        "label": "Settlement",        "color": "#10b981"},
            {"id": "closed",            "label": "Closed",            "color": "#6b7280"},
        ],
        "fields": ["client", "matter_type", "attorney", "billing_rate", "open_date"],
        "hitl_stages": ["conflict_check", "billing_review"],
    },
    "real_estate": {
        "label": "Real Estate / Title",
        "icon": "home",
        "stages": [
            {"id": "offer",               "label": "Offer",              "color": "#6366f1"},
            {"id": "title_search",        "label": "Title Search",       "color": "#8b5cf6"},
            {"id": "inspection",          "label": "Inspection",         "color": "#f59e0b"},
            {"id": "commitment_letter",   "label": "Commitment Letter",  "color": "#a78bfa"},
            {"id": "clear_to_close",      "label": "Clear to Close",     "color": "#fb923c"},
            {"id": "closing_disclosure",  "label": "Closing Disclosure", "color": "#10b981"},
            {"id": "recorded",            "label": "Recorded",           "color": "#059669"},
        ],
        "fields": ["property_address", "buyer", "seller", "purchase_price", "closing_date"],
        "hitl_stages": ["title_search", "clear_to_close"],
    },
}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS domain_workflow_items (
            id          TEXT PRIMARY KEY,
            domain      TEXT NOT NULL,
            stage       TEXT NOT NULL,
            title       TEXT NOT NULL,
            fields      JSONB DEFAULT '{}',
            notes       TEXT,
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS domain_workflow_history (
            id          TEXT PRIMARY KEY,
            item_id     TEXT NOT NULL,
            domain      TEXT NOT NULL,
            from_stage  TEXT,
            to_stage    TEXT NOT NULL,
            note        TEXT,
            moved_by    TEXT DEFAULT 'user',
            moved_at    TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_dwi_domain ON domain_workflow_items(domain)
    """))
    db.commit()


def r2d(row) -> Dict:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k in ("fields",):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


# ── Schemas ────────────────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    title: str
    stage: Optional[str] = None
    fields: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class StageMove(BaseModel):
    stage: str
    note: Optional[str] = None
    moved_by: Optional[str] = "user"


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/config")
def get_config():
    """Return all domain pipeline configs (stages, colors, HITL flags)."""
    return {"domains": DOMAIN_CONFIGS}


@router.get("/{domain}/items")
def list_items(domain: str, stage: Optional[str] = None, db: Session = Depends(get_db)):
    if domain not in DOMAIN_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")
    ensure_tables(db)

    q = "SELECT * FROM domain_workflow_items WHERE domain=:domain"
    params: Dict = {"domain": domain}
    if stage:
        q += " AND stage=:stage"
        params["stage"] = stage
    q += " ORDER BY updated_at DESC"

    rows = [r2d(r) for r in db.execute(text(q), params).fetchall()]

    # Stage counts
    counts_raw = db.execute(text(
        "SELECT stage, COUNT(*) as cnt FROM domain_workflow_items WHERE domain=:domain GROUP BY stage"
    ), {"domain": domain}).fetchall()
    stage_counts = {r.stage: r.cnt for r in counts_raw}

    cfg = DOMAIN_CONFIGS[domain]
    stages_with_counts = [
        {**s, "count": stage_counts.get(s["id"], 0)}
        for s in cfg["stages"]
    ]

    return {
        "domain": domain,
        "config": cfg,
        "stages": stages_with_counts,
        "items": rows,
        "total": len(rows),
    }


@router.post("/{domain}/items")
def create_item(domain: str, body: ItemCreate, db: Session = Depends(get_db)):
    if domain not in DOMAIN_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")
    ensure_tables(db)

    cfg = DOMAIN_CONFIGS[domain]
    first_stage = cfg["stages"][0]["id"]
    stage = body.stage or first_stage

    item_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO domain_workflow_items (id, domain, stage, title, fields, notes)
        VALUES (:id, :domain, :stage, :title, :fields, :notes)
    """), {
        "id": item_id, "domain": domain, "stage": stage,
        "title": body.title,
        "fields": json.dumps(body.fields or {}),
        "notes": body.notes,
    })

    db.execute(text("""
        INSERT INTO domain_workflow_history (id, item_id, domain, from_stage, to_stage, note, moved_by)
        VALUES (:id, :item_id, :domain, NULL, :to_stage, 'Created', 'system')
    """), {"id": str(uuid.uuid4()), "item_id": item_id, "domain": domain, "to_stage": stage})

    db.commit()
    return {"id": item_id, "domain": domain, "stage": stage, "title": body.title}


@router.patch("/{domain}/items/{item_id}/stage")
def move_stage(domain: str, item_id: str, body: StageMove, db: Session = Depends(get_db)):
    if domain not in DOMAIN_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")
    ensure_tables(db)

    valid_stages = [s["id"] for s in DOMAIN_CONFIGS[domain]["stages"]]
    if body.stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage '{body.stage}'. Valid: {valid_stages}")

    row = db.execute(text(
        "SELECT stage FROM domain_workflow_items WHERE id=:id AND domain=:domain"
    ), {"id": item_id, "domain": domain}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    old_stage = row.stage
    db.execute(text("""
        UPDATE domain_workflow_items SET stage=:stage, updated_at=NOW() WHERE id=:id
    """), {"stage": body.stage, "id": item_id})

    db.execute(text("""
        INSERT INTO domain_workflow_history (id, item_id, domain, from_stage, to_stage, note, moved_by)
        VALUES (:id, :item_id, :domain, :from_stage, :to_stage, :note, :moved_by)
    """), {
        "id": str(uuid.uuid4()), "item_id": item_id, "domain": domain,
        "from_stage": old_stage, "to_stage": body.stage,
        "note": body.note, "moved_by": body.moved_by,
    })

    db.commit()
    logger.info(f"[{domain}] {item_id}: {old_stage} → {body.stage}")
    return {"item_id": item_id, "from_stage": old_stage, "to_stage": body.stage}


@router.get("/{domain}/items/{item_id}/history")
def get_history(domain: str, item_id: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    rows = db.execute(text("""
        SELECT * FROM domain_workflow_history WHERE item_id=:item_id ORDER BY moved_at ASC
    """), {"item_id": item_id}).fetchall()
    return {"item_id": item_id, "history": [r2d(r) for r in rows]}


@router.get("/{domain}/stats")
def get_stats(domain: str, db: Session = Depends(get_db)):
    if domain not in DOMAIN_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")
    ensure_tables(db)

    counts_raw = db.execute(text("""
        SELECT stage, COUNT(*) as cnt FROM domain_workflow_items
        WHERE domain=:domain GROUP BY stage
    """), {"domain": domain}).fetchall()

    stage_counts = {r.stage: r.cnt for r in counts_raw}
    cfg = DOMAIN_CONFIGS[domain]

    return {
        "domain": domain,
        "total": sum(stage_counts.values()),
        "by_stage": [
            {**s, "count": stage_counts.get(s["id"], 0)}
            for s in cfg["stages"]
        ],
    }
