"""Contract Lifecycle Management — stages, moves, metadata."""
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_scope import current_tenant

router = APIRouter(prefix="/api/clm", tags=["clm"])

CLM_STAGES = ["draft", "review", "pending_approval", "active", "expiring", "renewed", "terminated"]

# ── Pydantic schemas ────────────────────────────────────────────────────────

class CLMEntryCreate(BaseModel):
    doc_id: str
    stage: str = "draft"
    contract_value: Optional[float] = None
    counterparty: Optional[str] = None
    governing_law: Optional[str] = None
    notes: Optional[str] = None

class CLMEntryUpdate(BaseModel):
    stage: Optional[str] = None
    contract_value: Optional[float] = None
    counterparty: Optional[str] = None
    governing_law: Optional[str] = None
    notes: Optional[str] = None

class CLMStageMove(BaseModel):
    stage: str
    note: Optional[str] = None
    moved_by: Optional[str] = "system"

# ── DB helpers ──────────────────────────────────────────────────────────────

def ensure_table(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS clm_entries (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'draft',
            contract_value NUMERIC,
            counterparty TEXT,
            governing_law TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS clm_history (
            id TEXT PRIMARY KEY,
            clm_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            from_stage TEXT,
            to_stage TEXT NOT NULL,
            note TEXT,
            moved_by TEXT,
            moved_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def _t() -> str:
    return str(current_tenant())

def _own(clm_id: str, db: Session):
    """The current tenant's entry (its document belongs to the tenant), or 404."""
    row = db.execute(text("""
        SELECT c.*, d.filename, d.doc_type, d.summary
        FROM clm_entries c JOIN documents d ON d.id::text = c.doc_id
        WHERE c.id = :id AND d.tenant_id = :t
    """), {"id": clm_id, "t": _t()}).fetchone()
    if not row:
        raise HTTPException(404, "CLM entry not found")
    return row

def row_to_dict(row):
    if row is None:
        return None
    return dict(row._mapping)

# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("")
def list_entries(stage: Optional[str] = None, db: Session = Depends(get_db)):
    ensure_table(db)
    q = """
        SELECT c.*, d.filename, d.doc_type, d.summary
        FROM clm_entries c
        JOIN documents d ON d.id::text = c.doc_id
        WHERE d.tenant_id = :t
    """
    params = {"t": _t()}
    if stage:
        q += " AND c.stage = :stage"
        params["stage"] = stage
    q += " ORDER BY c.updated_at DESC"
    rows = db.execute(text(q), params).fetchall()
    return [row_to_dict(r) for r in rows]

@router.post("")
def create_entry(body: CLMEntryCreate, db: Session = Depends(get_db)):
    ensure_table(db)
    if body.stage not in CLM_STAGES:
        raise HTTPException(400, f"Unknown stage {body.stage!r}")
    if not db.execute(text("SELECT 1 FROM documents WHERE id::text=:d AND tenant_id=:t"), {"d": body.doc_id, "t": _t()}).fetchone():
        raise HTTPException(404, "Document not found")
    eid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO clm_entries (id, doc_id, stage, contract_value, counterparty, governing_law, notes)
        VALUES (:id, :doc_id, :stage, :cv, :cp, :gl, :notes)
    """), {"id": eid, "doc_id": body.doc_id, "stage": body.stage,
           "cv": body.contract_value, "cp": body.counterparty,
           "gl": body.governing_law, "notes": body.notes})
    # initial history
    db.execute(text("""
        INSERT INTO clm_history (id, clm_id, doc_id, from_stage, to_stage, note, moved_by)
        VALUES (:id, :cid, :did, NULL, :stage, 'Created', 'system')
    """), {"id": str(uuid.uuid4()), "cid": eid, "did": body.doc_id, "stage": body.stage})
    db.commit()
    row = db.execute(text("SELECT * FROM clm_entries WHERE id=:id"), {"id": eid}).fetchone()
    return {"entry": row_to_dict(row)}

@router.get("/stages")
def get_stages():
    return {"stages": CLM_STAGES}

@router.get("/{clm_id}")
def get_entry(clm_id: str, db: Session = Depends(get_db)):
    ensure_table(db)
    row = _own(clm_id, db)
    history = db.execute(text(
        "SELECT * FROM clm_history WHERE clm_id=:id ORDER BY moved_at ASC"
    ), {"id": clm_id}).fetchall()
    entry = row_to_dict(row)
    entry["history"] = [row_to_dict(h) for h in history]
    return entry

@router.patch("/{clm_id}/move")
def move_stage(clm_id: str, body: CLMStageMove, db: Session = Depends(get_db)):
    ensure_table(db)
    if body.stage not in CLM_STAGES:
        raise HTTPException(400, f"Unknown stage {body.stage!r}")
    row = _own(clm_id, db)
    from_stage = row._mapping["stage"]
    db.execute(text("""
        UPDATE clm_entries SET stage=:stage, updated_at=NOW() WHERE id=:id
    """), {"stage": body.stage, "id": clm_id})
    db.execute(text("""
        INSERT INTO clm_history (id, clm_id, doc_id, from_stage, to_stage, note, moved_by)
        VALUES (:id, :cid, :did, :fs, :ts, :note, :by)
    """), {"id": str(uuid.uuid4()), "cid": clm_id, "did": row._mapping["doc_id"],
           "fs": from_stage, "ts": body.stage, "note": body.note, "by": body.moved_by})
    db.commit()
    return {"ok": True, "from_stage": from_stage, "to_stage": body.stage}

@router.patch("/{clm_id}")
def update_entry(clm_id: str, body: CLMEntryUpdate, db: Session = Depends(get_db)):
    ensure_table(db)
    _own(clm_id, db)
    updates = {k: v for k, v in body.dict().items() if v is not None}   # keys come from CLMEntryUpdate
    if not updates:
        return {"ok": True}
    sets = ", ".join(f"{k}=:{k}" for k in updates)
    updates["id"] = clm_id
    updates["updated_at"] = datetime.utcnow()
    db.execute(text(f"UPDATE clm_entries SET {sets}, updated_at=:updated_at WHERE id=:id"), updates)
    db.commit()
    return {"ok": True}

@router.delete("/{clm_id}")
def delete_entry(clm_id: str, db: Session = Depends(get_db)):
    ensure_table(db)
    _own(clm_id, db)
    db.execute(text("DELETE FROM clm_history WHERE clm_id=:id"), {"id": clm_id})
    db.execute(text("DELETE FROM clm_entries WHERE id=:id"), {"id": clm_id})
    db.commit()
    return {"ok": True}
