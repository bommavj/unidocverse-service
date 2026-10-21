"""Audit Trail — immutable event log for all document actions."""
import uuid
from typing import Optional
from fastapi import Request, APIRouter, Depends, Query
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
    conditions = ["a.tenant_id = :tenant"]
    params: dict = {"limit": limit, "offset": offset, "tenant": str(get_current_tenant_id() or DEFAULT_TENANT_ID)}
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
        LEFT JOIN documents d ON d.id::text = a.doc_id::text
        {where}
        ORDER BY a.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()
    total = db.execute(text(f"SELECT COUNT(*) FROM audit_log a {where}"), params).scalar()
    return {"events": [row_to_dict(r) for r in rows], "total": total}

@router.post("")
def create_event(body: AuditEventCreate, request: Request, db: Session = Depends(get_db)):
    # The actor is whoever is signed in, not what the client claims
    from app.core.audit_middleware import _claims
    body.actor = _claims(request).get("username") or "anonymous"
    body.ip = request.client.host if request.client else body.ip
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

def _t() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


@router.get("/summary")
def audit_summary(db: Session = Depends(get_db)):
    ensure_table(db)
    rows = db.execute(text("""
        SELECT action, COUNT(*) as cnt,
               MAX(created_at) as last_at
        FROM audit_log WHERE tenant_id = :t
        GROUP BY action ORDER BY cnt DESC
    """), {"t": _t()}).fetchall()
    actors = db.execute(text("""
        SELECT actor, COUNT(*) as cnt FROM audit_log WHERE tenant_id = :t
        GROUP BY actor ORDER BY cnt DESC LIMIT 10
    """), {"t": _t()}).fetchall()
    daily = db.execute(text("""
        SELECT DATE(created_at) as day, COUNT(*) as cnt
        FROM audit_log
        WHERE tenant_id = :t AND created_at >= NOW() - INTERVAL '30 days'
        GROUP BY day ORDER BY day ASC
    """), {"t": _t()}).fetchall()
    return {
        "by_action": [row_to_dict(r) for r in rows],
        "top_actors": [row_to_dict(r) for r in actors],
        "daily_30d": [row_to_dict(r) for r in daily],
    }

@router.get("/actions")
def get_actions():
    return {"actions": VALID_ACTIONS}


# ── Retention (Audit trail → Retention) ──────────────────────────────────────

from app.api.auth_router import require_admin  # noqa: E402

class RetentionUpdate(BaseModel):
    read_days: int
    change_days: int
    legal_hold: bool = False


@router.get("/retention")
def get_retention(db: Session = Depends(get_db), user=Depends(require_admin)):
    from app.services import audit_retention as ar
    ar.ensure_tables(db)
    pol = ar.policy(db, _t())
    size = db.execute(text("SELECT count(*) FROM audit_log WHERE tenant_id = CAST(:t AS uuid)"), {"t": _t()}).scalar()
    return {**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in pol.items()},
            "read_choices": ar.READ_CHOICES, "change_choices": ar.CHANGE_CHOICES, "rows": size}


@router.put("/retention")
def put_retention(body: RetentionUpdate, request: Request, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    from fastapi import HTTPException
    from app.core.audit import record
    from app.services import audit_retention as ar
    ar.ensure_tables(db)
    if body.read_days not in ar.READ_CHOICES or body.change_days not in ar.CHANGE_CHOICES:
        raise HTTPException(400, "Choose one of the offered retention periods")
    before = ar.policy(db, _t())
    db.execute(text("""INSERT INTO audit_retention (tenant_id, read_days, change_days, legal_hold, updated_by)
        VALUES (:t, :r, :c, :h, :u) ON CONFLICT (tenant_id) DO UPDATE SET read_days = EXCLUDED.read_days,
        change_days = EXCLUDED.change_days, legal_hold = EXCLUDED.legal_hold, updated_at = now(),
        updated_by = EXCLUDED.updated_by"""),
        {"t": _t(), "r": body.read_days, "c": body.change_days, "h": body.legal_hold, "u": user["username"]})
    db.commit()
    record("audit.retention_changed", actor=user["username"], ip=request.client.host if request.client else None,
           detail=f"Reads {body.read_days}d, changes {body.change_days or 'forever'}d, legal hold {'on' if body.legal_hold else 'off'}",
           extra={"before": {k: before[k] for k in ("read_days", "change_days", "legal_hold")},
                  "after": body.model_dump()})
    return {"success": True}


@router.post("/retention/run")
def run_retention(request: Request, user=Depends(require_admin)):
    from app.core.audit import record
    from app.services import audit_retention as ar
    result = ar.run(_t())
    record("audit.cleanup_run", actor=user["username"], detail="Retention cleanup run manually",
           ip=request.client.host if request.client else None, extra=result.get(_t(), {}))
    return result.get(_t(), {})


@router.get("/archives")
def list_archives(user=Depends(require_admin)):
    from app.services import audit_retention as ar
    folder = ar.ARCHIVE_ROOT / _t()
    files = sorted(folder.glob("*.jsonl.gz"), reverse=True) if folder.exists() else []
    return {"archives": [{"name": f.name, "size": f.stat().st_size,
                          "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()} for f in files]}


@router.get("/archives/{name}")
def download_archive(name: str, request: Request,
                     user=Depends(require_admin)):
    import re
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from app.core.audit import record
    from app.services import audit_retention as ar
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-(read|change)\.jsonl\.gz", name):
        raise HTTPException(400, "Invalid archive name")
    path = ar.ARCHIVE_ROOT / _t() / name
    if not path.exists():
        raise HTTPException(404, "Archive not found")
    record("audit.archive_downloaded", actor=user["username"], detail=name,
           ip=request.client.host if request.client else None)
    return FileResponse(path, media_type="application/gzip", filename=name)
