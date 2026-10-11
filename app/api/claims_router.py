"""Insurance Claims Tracker — bundle policy + claim docs into one case."""
import uuid, json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant_scope import current_tenant

router = APIRouter(prefix="/api/claims", tags=["claims"])

CLAIM_STAGES = ["reported", "under_review", "investigation", "negotiation", "settled", "closed", "denied"]

class ClaimCreate(BaseModel):
    title: str
    claim_number: Optional[str] = None
    policy_doc_id: Optional[str] = None
    claimant_name: Optional[str] = None
    incident_date: Optional[str] = None
    claim_type: str = "general"   # auto | property | liability | health | workers_comp
    estimated_value: Optional[float] = None
    adjuster: Optional[str] = None
    description: Optional[str] = None

class ClaimUpdate(BaseModel):
    stage: Optional[str] = None
    claim_number: Optional[str] = None
    claimant_name: Optional[str] = None
    adjuster: Optional[str] = None
    estimated_value: Optional[float] = None
    settled_value: Optional[float] = None
    description: Optional[str] = None
    status: Optional[str] = None

class ClaimDocAdd(BaseModel):
    doc_id: str
    role: str = "evidence"   # policy | claim_form | evidence | medical | correspondence | settlement

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            claim_number TEXT,
            policy_doc_id TEXT,
            claimant_name TEXT,
            incident_date TEXT,
            claim_type TEXT DEFAULT 'general',
            stage TEXT DEFAULT 'reported',
            estimated_value NUMERIC,
            settled_value NUMERIC,
            adjuster TEXT,
            description TEXT,
            status TEXT DEFAULT 'open',
            tenant_id TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS claim_docs (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            role TEXT DEFAULT 'evidence',
            added_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(claim_id, doc_id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS claim_timeline (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            event TEXT NOT NULL,
            note TEXT,
            actor TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("ALTER TABLE claims ADD COLUMN IF NOT EXISTS tenant_id TEXT"))
    db.commit()

def _t() -> str:
    return str(current_tenant())

def _own(cid: str, db: Session):
    row = db.execute(text("SELECT * FROM claims WHERE id=:id AND tenant_id=:t"), {"id": cid, "t": _t()}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    return row

def r2d(row): return dict(row._mapping) if row else None

def add_event(claim_id: str, event: str, note: str, db: Session, actor: str = "system"):
    db.execute(text("""
        INSERT INTO claim_timeline (id,claim_id,event,note,actor)
        VALUES (:id,:cid,:event,:note,:actor)
    """), {"id": str(uuid.uuid4()), "cid": claim_id, "event": event, "note": note, "actor": actor})
    db.commit()

@router.get("")
def list_claims(stage: Optional[str] = None, status: str = "open", db: Session = Depends(get_db)):
    ensure_tables(db)
    q = "SELECT * FROM claims WHERE status=:status AND tenant_id=:t"
    params: dict = {"status": status, "t": _t()}
    if stage: q += " AND stage=:stage"; params["stage"] = stage
    q += " ORDER BY updated_at DESC"
    rows = db.execute(text(q), params).fetchall()
    claims = [r2d(r) for r in rows]
    for c in claims:
        c["doc_count"] = db.execute(text("SELECT COUNT(*) FROM claim_docs WHERE claim_id=:id"), {"id": c["id"]}).scalar()
    return claims

@router.get("/stats")
def claim_stats(db: Session = Depends(get_db)):
    ensure_tables(db)
    stages = db.execute(text("SELECT stage, COUNT(*) as cnt, SUM(estimated_value) as total FROM claims "
                             "WHERE tenant_id=:t GROUP BY stage"), {"t": _t()}).fetchall()
    return {"by_stage": [r2d(r) for r in stages]}

@router.post("")
def create_claim(body: ClaimCreate, db: Session = Depends(get_db)):
    ensure_tables(db)
    cid = str(uuid.uuid4())
    cn = body.claim_number or f"CLM-{cid[:8].upper()}"
    db.execute(text("""
        INSERT INTO claims (id,title,claim_number,policy_doc_id,claimant_name,incident_date,
                            claim_type,estimated_value,adjuster,description,tenant_id)
        VALUES (:id,:title,:cn,:pdid,:claimant,:idate,:ctype,:ev,:adj,:desc,:t)
    """), {"id": cid, "t": _t(), "title": body.title, "cn": cn, "pdid": body.policy_doc_id,
           "claimant": body.claimant_name, "idate": body.incident_date, "ctype": body.claim_type,
           "ev": body.estimated_value, "adj": body.adjuster, "desc": body.description})
    add_event(cid, "created", f"Claim {cn} opened", db)
    db.commit()
    return {"claim": r2d(db.execute(text("SELECT * FROM claims WHERE id=:id"), {"id": cid}).fetchone())}

@router.get("/{cid}")
def get_claim(cid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    c = r2d(_own(cid, db))
    docs = db.execute(text("""
        SELECT cd.*, d.filename, d.doc_type, d.summary
        FROM claim_docs cd JOIN documents d ON d.id::text=cd.doc_id
        WHERE cd.claim_id=:id AND d.tenant_id=:t ORDER BY cd.added_at DESC
    """), {"id": cid, "t": _t()}).fetchall()
    timeline = db.execute(text("SELECT * FROM claim_timeline WHERE claim_id=:id ORDER BY created_at ASC"), {"id": cid}).fetchall()
    c["documents"] = [r2d(d) for d in docs]
    c["timeline"] = [r2d(t) for t in timeline]
    return c

@router.patch("/{cid}/stage")
def move_stage(cid: str, stage: str, note: Optional[str] = None, db: Session = Depends(get_db)):
    ensure_tables(db)
    if stage not in CLAIM_STAGES:
        raise HTTPException(400, f"Unknown stage {stage!r}")
    old = _own(cid, db)
    db.execute(text("UPDATE claims SET stage=:stage, updated_at=NOW() WHERE id=:id AND tenant_id=:t"),
               {"stage": stage, "id": cid, "t": _t()})
    add_event(cid, "stage_change", f"Stage: {r2d(old)['stage'] if old else '?'} → {stage}. {note or ''}", db, "user")
    db.commit()
    return {"ok": True}

@router.patch("/{cid}")
def update_claim(cid: str, body: ClaimUpdate, db: Session = Depends(get_db)):
    ensure_tables(db)
    _own(cid, db)
    updates = {k: v for k, v in body.dict().items() if v is not None}   # keys come from the ClaimUpdate model
    if not updates: return {"ok": True}
    if "stage" in updates and updates["stage"] not in CLAIM_STAGES:
        raise HTTPException(400, f"Unknown stage {updates['stage']!r}")
    sets = ", ".join(f"{k}=:{k}" for k in updates)
    updates.update({"id": cid, "t": _t()})
    db.execute(text(f"UPDATE claims SET {sets}, updated_at=NOW() WHERE id=:id AND tenant_id=:t"), updates)
    db.commit()
    return {"ok": True}

@router.post("/{cid}/documents")
def add_doc(cid: str, body: ClaimDocAdd, db: Session = Depends(get_db)):
    ensure_tables(db)
    _own(cid, db)
    if not db.execute(text("SELECT 1 FROM documents WHERE id::text=:d AND tenant_id=:t"), {"d": body.doc_id, "t": _t()}).fetchone():
        raise HTTPException(404, "Document not found")
    db.execute(text("""
        INSERT INTO claim_docs (id,claim_id,doc_id,role) VALUES (:id,:cid,:did,:role)
        ON CONFLICT (claim_id,doc_id) DO UPDATE SET role=:role
    """), {"id": str(uuid.uuid4()), "cid": cid, "did": body.doc_id, "role": body.role})
    add_event(cid, "doc_added", f"Document added as {body.role}", db)
    db.commit()
    return {"ok": True}

@router.delete("/{cid}")
def delete_claim(cid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    _own(cid, db)
    for t in ["claim_timeline","claim_docs","claims"]:
        db.execute(text(f"DELETE FROM {t} WHERE {'claim_id' if t!='claims' else 'id'}=:id"), {"id": cid})
    db.commit()
    return {"ok": True}
