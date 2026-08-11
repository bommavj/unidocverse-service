"""Legal Matter Management — group documents into matters/cases."""
import uuid, json
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

router = APIRouter(prefix="/api/matters", tags=["matters"])

STAGES = ["intake", "investigation", "negotiation", "litigation", "settlement", "closed"]

class MatterCreate(BaseModel):
    title: str
    matter_type: str = "general"   # contract | litigation | compliance | ip | employment
    client_name: Optional[str] = None
    assigned_counsel: Optional[str] = None
    stage: str = "intake"
    priority: str = "normal"
    deadline: Optional[str] = None
    description: Optional[str] = None

class MatterUpdate(BaseModel):
    title: Optional[str] = None
    stage: Optional[str] = None
    assigned_counsel: Optional[str] = None
    priority: Optional[str] = None
    deadline: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

class MatterDocAdd(BaseModel):
    doc_id: str
    role: str = "supporting"   # primary | supporting | evidence | correspondence | filed

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS matters (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            matter_type TEXT DEFAULT 'general',
            client_name TEXT,
            assigned_counsel TEXT,
            stage TEXT DEFAULT 'intake',
            priority TEXT DEFAULT 'normal',
            deadline TEXT,
            description TEXT,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS matter_docs (
            id TEXT PRIMARY KEY,
            matter_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            role TEXT DEFAULT 'supporting',
            added_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(matter_id, doc_id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS matter_notes (
            id TEXT PRIMARY KEY,
            matter_id TEXT NOT NULL,
            author TEXT,
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def r2d(row): return dict(row._mapping) if row else None

@router.get("")
def list_matters(stage: Optional[str] = None, status: str = "open", db: Session = Depends(get_db)):
    ensure_tables(db)
    q = "SELECT * FROM matters WHERE status=:status"
    params: dict = {"status": status}
    if stage:
        q += " AND stage=:stage"; params["stage"] = stage
    q += " ORDER BY updated_at DESC"
    rows = db.execute(text(q), params).fetchall()
    matters = [r2d(r) for r in rows]
    for m in matters:
        count = db.execute(text("SELECT COUNT(*) FROM matter_docs WHERE matter_id=:id"), {"id": m["id"]}).scalar()
        m["doc_count"] = count
    return matters

@router.post("")
def create_matter(body: MatterCreate, db: Session = Depends(get_db)):
    ensure_tables(db)
    mid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO matters (id,title,matter_type,client_name,assigned_counsel,stage,priority,deadline,description,tenant_id)
        VALUES (:id,:title,:mt,:cn,:ac,:stage,:priority,:deadline,:desc,:tenant_id)
    """), {"id": mid, "title": body.title, "mt": body.matter_type, "cn": body.client_name,
           "ac": body.assigned_counsel, "stage": body.stage, "priority": body.priority,
           "deadline": body.deadline, "desc": body.description,
           "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID})
    db.commit()
    return {"matter": r2d(db.execute(text("SELECT * FROM matters WHERE id=:id"), {"id": mid}).fetchone())}

@router.get("/stages")
def get_stages(): return {"stages": STAGES}

@router.get("/{mid}")
def get_matter(mid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    row = db.execute(text("SELECT * FROM matters WHERE id=:id"), {"id": mid}).fetchone()
    if not row: raise HTTPException(404, "Not found")
    m = r2d(row)
    docs = db.execute(text("""
        SELECT md.*, d.filename, d.doc_type, d.summary, d.doc_metadata
        FROM matter_docs md JOIN documents d ON d.id=md.doc_id
        WHERE md.matter_id=:id ORDER BY md.added_at DESC
    """), {"id": mid}).fetchall()
    notes = db.execute(text("SELECT * FROM matter_notes WHERE matter_id=:id ORDER BY created_at DESC"), {"id": mid}).fetchall()
    m["documents"] = [r2d(d) for d in docs]
    m["notes"] = [r2d(n) for n in notes]
    return m

@router.patch("/{mid}")
def update_matter(mid: str, body: MatterUpdate, db: Session = Depends(get_db)):
    ensure_tables(db)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates: return {"ok": True}
    sets = ", ".join(f"{k}=:{k}" for k in updates)
    updates["id"] = mid; updates["updated_at"] = "NOW()"
    db.execute(text(f"UPDATE matters SET {sets}, updated_at=NOW() WHERE id=:id"), updates)
    db.commit()
    return {"ok": True}

@router.post("/{mid}/documents")
def add_document(mid: str, body: MatterDocAdd, db: Session = Depends(get_db)):
    ensure_tables(db)
    db.execute(text("""
        INSERT INTO matter_docs (id, matter_id, doc_id, role)
        VALUES (:id, :mid, :did, :role)
        ON CONFLICT (matter_id, doc_id) DO UPDATE SET role=:role
    """), {"id": str(uuid.uuid4()), "mid": mid, "did": body.doc_id, "role": body.role})
    db.execute(text("UPDATE matters SET updated_at=NOW() WHERE id=:id"), {"id": mid})
    db.commit()
    return {"ok": True}

@router.delete("/{mid}/documents/{doc_id}")
def remove_document(mid: str, doc_id: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    db.execute(text("DELETE FROM matter_docs WHERE matter_id=:mid AND doc_id=:did"), {"mid": mid, "did": doc_id})
    db.commit()
    return {"ok": True}

@router.post("/{mid}/notes")
def add_note(mid: str, author: str, note: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    db.execute(text("INSERT INTO matter_notes (id,matter_id,author,note) VALUES (:id,:mid,:author,:note)"),
               {"id": str(uuid.uuid4()), "mid": mid, "author": author, "note": note})
    db.commit()
    return {"ok": True}

@router.delete("/{mid}")
def delete_matter(mid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    db.execute(text("DELETE FROM matter_notes WHERE matter_id=:id"), {"id": mid})
    db.execute(text("DELETE FROM matter_docs WHERE matter_id=:id"), {"id": mid})
    db.execute(text("DELETE FROM matters WHERE id=:id"), {"id": mid})
    db.commit()
    return {"ok": True}
