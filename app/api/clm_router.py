"""Contract Lifecycle Management — stages, moves, metadata."""
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import get_current_user
from app.core.audit import record as audit
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
    moved_by: Optional[str] = None          # ignored: the signed-in user moves it
    approvers: Optional[List[str]] = None   # for pending_approval; default: the organisation's admins

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
    db.execute(text("ALTER TABLE clm_entries ADD COLUMN IF NOT EXISTS approval_id TEXT"))
    db.execute(text("ALTER TABLE clm_entries ADD COLUMN IF NOT EXISTS end_date DATE"))
    db.commit()

def _t() -> str:
    return str(current_tenant())


_END_KEYS = ("end_date", "expiration_date", "expiry_date", "termination_date", "renewal_date", "contract_end_date")


def _end_date(db: Session, doc_id: str) -> Optional[str]:
    """The contract's end date from its extracted fields, if one was found."""
    facts = db.execute(text("SELECT extracted_facts FROM documents WHERE id::text = :d"), {"d": doc_id}).scalar() or {}
    for k in _END_KEYS:
        v = facts.get(k)
        if isinstance(v, str) and len(v) >= 10:
            try:
                return datetime.fromisoformat(v[:10]).date().isoformat()
            except ValueError:
                continue
    return None


def move_entry(db: Session, clm_id: str, stage: str, moved_by: str, note: Optional[str] = None) -> dict:
    """Move a contract to `stage` and record it in its history and the audit trail."""
    row = db.execute(text("SELECT stage, doc_id FROM clm_entries WHERE id = :id"), {"id": clm_id}).fetchone()
    if not row or row.stage == stage:
        return {"ok": bool(row), "from_stage": row.stage if row else None, "to_stage": stage}
    db.execute(text("UPDATE clm_entries SET stage=:stage, updated_at=NOW() WHERE id=:id"), {"stage": stage, "id": clm_id})
    db.execute(text("""INSERT INTO clm_history (id, clm_id, doc_id, from_stage, to_stage, note, moved_by)
        VALUES (:id, :cid, :did, :fs, :ts, :note, :by)"""),
        {"id": str(uuid.uuid4()), "cid": clm_id, "did": row.doc_id, "fs": row.stage, "ts": stage,
         "note": note, "by": moved_by})
    db.commit()
    audit("contract.stage_moved", actor=moved_by, doc_id=row.doc_id, detail=f"{row.stage} → {stage}",
          extra={"clm_id": clm_id, "note": note})
    return {"ok": True, "from_stage": row.stage, "to_stage": stage}

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
def create_entry(body: CLMEntryCreate, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_table(db)
    if body.stage not in CLM_STAGES:
        raise HTTPException(400, f"Unknown stage {body.stage!r}")
    if not db.execute(text("SELECT 1 FROM documents WHERE id::text=:d AND tenant_id=:t"), {"d": body.doc_id, "t": _t()}).fetchone():
        raise HTTPException(404, "Document not found")
    eid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO clm_entries (id, doc_id, stage, contract_value, counterparty, governing_law, notes, end_date)
        VALUES (:id, :doc_id, :stage, :cv, :cp, :gl, :notes, CAST(:end AS date))
    """), {"id": eid, "doc_id": body.doc_id, "stage": body.stage,
           "cv": body.contract_value, "cp": body.counterparty,
           "gl": body.governing_law, "notes": body.notes, "end": _end_date(db, body.doc_id)})
    # initial history
    db.execute(text("""
        INSERT INTO clm_history (id, clm_id, doc_id, from_stage, to_stage, note, moved_by)
        VALUES (:id, :cid, :did, NULL, :stage, 'Created', :by)
    """), {"id": str(uuid.uuid4()), "cid": eid, "did": body.doc_id, "stage": body.stage, "by": user["username"]})
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
def move_stage(clm_id: str, body: CLMStageMove, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_table(db)
    if body.stage not in CLM_STAGES:
        raise HTTPException(400, f"Unknown stage {body.stage!r}")
    row = _own(clm_id, db)._mapping
    result = move_entry(db, clm_id, body.stage, user["username"], body.note)
    if body.stage == "pending_approval":
        # Sign-off goes through Approvals; its decision moves the contract on (active / review)
        from app.api.approvals_router import create_request
        approvers = body.approvers or [r[0] for r in db.execute(text(
            "SELECT username FROM users WHERE tenant_id::text = :t AND role = 'admin' AND is_active"), {"t": _t()})]
        aid = create_request(db, _t(), row["doc_id"], f"Contract approval: {row['filename']}",
                             body.note or f"Approve {row['counterparty'] or 'this contract'} to make it active.",
                             approvers, user["username"], source="clm", priority="high", clm_id=clm_id)
        db.execute(text("UPDATE clm_entries SET approval_id = :a WHERE id = :id"), {"a": aid, "id": clm_id})
        db.commit()
        result["approval_id"] = aid
    return result


def advance_expiring(days_ahead: int = 60) -> int:
    """Nightly: active contracts ending within `days_ahead` days move to expiring."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        ensure_table(db)
        # Fill end dates found after the entry was created (facts extracted later)
        for r in db.execute(text("SELECT id, doc_id FROM clm_entries WHERE end_date IS NULL")).fetchall():
            end = _end_date(db, r.doc_id)
            if end:
                db.execute(text("UPDATE clm_entries SET end_date = CAST(:e AS date) WHERE id = :id"), {"e": end, "id": r.id})
        db.commit()
        due = db.execute(text("""SELECT c.id, d.tenant_id::text AS tenant_id, c.end_date FROM clm_entries c
            JOIN documents d ON d.id::text = c.doc_id
            WHERE c.stage = 'active' AND c.end_date IS NOT NULL AND c.end_date <= CURRENT_DATE + :n"""),
            {"n": days_ahead}).fetchall()
        from app.core.tenant import set_current_tenant_id
        for r in due:
            set_current_tenant_id(r.tenant_id)
            move_entry(db, r.id, "expiring", "system", f"Ends {r.end_date:%b %d, %Y}")
        return len(due)
    finally:
        db.close()


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
def delete_entry(clm_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_table(db)
    row = _own(clm_id, db)._mapping
    audit("contract.removed_from_lifecycle", actor=user["username"], doc_id=row["doc_id"],
          detail=row["filename"], extra={"clm_id": clm_id, "stage": row["stage"]})
    db.execute(text("DELETE FROM clm_history WHERE clm_id=:id"), {"id": clm_id})
    db.execute(text("DELETE FROM clm_entries WHERE id=:id"), {"id": clm_id})
    db.commit()
    return {"ok": True}
