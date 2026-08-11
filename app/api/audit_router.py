"""Audit Trail — immutable event log for all document actions."""
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from datetime import datetime

router = APIRouter(prefix="/api/audit", tags=["audit"])

VALID_ACTIONS = [
    "upload", "view", "download", "edit", "delete",
    "approve", "reject", "comment", "share", "export",
    "clm_move", "workflow_run", "alert_triggered", "email_sent",
    "tag_added", "tag_removed", "linked", "unlinked"
]

class AuditEventCreate(BaseModel):
    doc_id: Optional[str] = None
    action: str
    actor: Optional[str] = "system"
    detail: Optional[str] = None
    ip: Optional[str] = None
    metadata: Optional[dict] = None

def ensure_table(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            doc_id TEXT,
            action TEXT NOT NULL,
            actor TEXT DEFAULT 'system',
            detail TEXT,
            ip TEXT,
            extra JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def row_to_dict(row):
    return dict(row._mapping) if row else None

def log_event(db: Session, doc_id: Optional[str], action: str,
              actor: str = "system", detail: str = "", extra: dict = None):
    """Call this from other routers to log an event."""
    ensure_table(db)
    import json
    db.execute(text("""
        INSERT INTO audit_log (id, doc_id, action, actor, detail, extra, tenant_id)
        VALUES (:id, :doc_id, :action, :actor, :detail, CAST(:extra AS jsonb), :tenant_id)
    """), {"id": str(uuid.uuid4()), "doc_id": doc_id, "action": action,
           "actor": actor, "detail": detail, "extra": json.dumps(extra or {}),
           "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID})
    db.commit()

@router.get("")
def list_events(
    doc_id: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    limit: int = Query(200, le=500),
    offset: int = 0,
    db: Session = Depends(get_db)
):
    ensure_table(db)
    conditions = []
    params: dict = {"limit": limit, "offset": offset}
    if doc_id:
        conditions.append("a.doc_id = :doc_id"); params["doc_id"] = doc_id
    if action:
        conditions.append("a.action = :action"); params["action"] = action
    if actor:
        conditions.append("a.actor ILIKE :actor"); params["actor"] = f"%{actor}%"
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = db.execute(text(f"""
        SELECT a.*, d.filename, d.doc_type
        FROM audit_log a
        LEFT JOIN documents d ON d.id = a.doc_id
        {where}
        ORDER BY a.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()
    total = db.execute(text(f"SELECT COUNT(*) FROM audit_log a {where}"), params).scalar()
    return {"events": [row_to_dict(r) for r in rows], "total": total}

@router.post("")
def create_event(body: AuditEventCreate, db: Session = Depends(get_db)):
    import json
    ensure_table(db)
    eid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO audit_log (id, doc_id, action, actor, detail, ip, extra, tenant_id)
        VALUES (:id, :doc_id, :action, :actor, :detail, :ip, CAST(:extra AS jsonb), :tenant_id)
    """), {"id": eid, "doc_id": body.doc_id, "action": body.action,
           "actor": body.actor, "detail": body.detail, "ip": body.ip,
           "extra": json.dumps(body.metadata or {}),
           "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID})
    db.commit()
    return {"ok": True, "id": eid}

@router.get("/summary")
def audit_summary(db: Session = Depends(get_db)):
    ensure_table(db)
    rows = db.execute(text("""
        SELECT action, COUNT(*) as cnt,
               MAX(created_at) as last_at
        FROM audit_log
        GROUP BY action ORDER BY cnt DESC
    """)).fetchall()
    actors = db.execute(text("""
        SELECT actor, COUNT(*) as cnt FROM audit_log
        GROUP BY actor ORDER BY cnt DESC LIMIT 10
    """)).fetchall()
    daily = db.execute(text("""
        SELECT DATE(created_at) as day, COUNT(*) as cnt
        FROM audit_log
        WHERE created_at >= NOW() - INTERVAL '30 days'
        GROUP BY day ORDER BY day ASC
    """)).fetchall()
    return {
        "by_action": [row_to_dict(r) for r in rows],
        "top_actors": [row_to_dict(r) for r in actors],
        "daily_30d": [row_to_dict(r) for r in daily],
    }

@router.get("/actions")
def get_actions():
    return {"actions": VALID_ACTIONS}
