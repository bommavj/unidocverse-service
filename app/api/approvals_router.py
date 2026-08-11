"""Approval Workflows — create, route, approve/reject documents."""
import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

# ── Schemas ─────────────────────────────────────────────────────────────────

class ApprovalCreate(BaseModel):
    doc_id: str
    title: str
    description: Optional[str] = None
    approvers: List[str]           # list of email / user names
    due_date: Optional[str] = None
    priority: str = "normal"       # low | normal | high | urgent

class ApprovalDecision(BaseModel):
    decision: str                  # approve | reject | request_changes
    comment: Optional[str] = None
    approver: str

class ApprovalComment(BaseModel):
    author: str
    text: str

# ── DB helpers ───────────────────────────────────────────────────────────────

def ensure_table(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS approval_requests (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            approvers JSONB DEFAULT '[]',
            decisions JSONB DEFAULT '[]',
            comments JSONB DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT DEFAULT 'normal',
            due_date TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def row_to_dict(row):
    return dict(row._mapping) if row else None

# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("")
def list_approvals(status: Optional[str] = None, db: Session = Depends(get_db)):
    ensure_table(db)
    q = """
        SELECT a.*, d.filename, d.doc_type
        FROM approval_requests a
        LEFT JOIN documents d ON d.id = a.doc_id
    """
    params = {}
    if status:
        q += " WHERE a.status = :status"
        params["status"] = status
    q += " ORDER BY a.created_at DESC"
    rows = db.execute(text(q), params).fetchall()
    return [row_to_dict(r) for r in rows]

@router.post("")
def create_approval(body: ApprovalCreate, db: Session = Depends(get_db)):
    ensure_table(db)
    aid = str(uuid.uuid4())
    import json
    db.execute(text("""
        INSERT INTO approval_requests (id, doc_id, title, description, approvers, due_date, priority)
        VALUES (:id, :doc_id, :title, :desc, CAST(:approvers AS jsonb), :due_date, :priority)
    """), {"id": aid, "doc_id": body.doc_id, "title": body.title, "desc": body.description,
           "approvers": json.dumps(body.approvers), "due_date": body.due_date, "priority": body.priority})
    db.commit()
    row = db.execute(text("SELECT * FROM approval_requests WHERE id=:id"), {"id": aid}).fetchone()
    return {"request": row_to_dict(row)}

@router.get("/stats")
def approval_stats(db: Session = Depends(get_db)):
    ensure_table(db)
    rows = db.execute(text(
        "SELECT status, COUNT(*) as cnt FROM approval_requests GROUP BY status"
    )).fetchall()
    return {r._mapping["status"]: r._mapping["cnt"] for r in rows}

@router.get("/{aid}")
def get_approval(aid: str, db: Session = Depends(get_db)):
    ensure_table(db)
    row = db.execute(text("""
        SELECT a.*, d.filename, d.doc_type, d.summary
        FROM approval_requests a LEFT JOIN documents d ON d.id = a.doc_id
        WHERE a.id = :id
    """), {"id": aid}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    return row_to_dict(row)

@router.post("/{aid}/decide")
def decide(aid: str, body: ApprovalDecision, db: Session = Depends(get_db)):
    import json
    ensure_table(db)
    row = db.execute(text("SELECT * FROM approval_requests WHERE id=:id"), {"id": aid}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    d = row_to_dict(row)
    decisions = d.get("decisions") or []
    # remove previous decision by same approver
    decisions = [x for x in decisions if x.get("approver") != body.approver]
    decisions.append({"approver": body.approver, "decision": body.decision,
                       "comment": body.comment, "at": str(uuid.uuid4())[:8]})

    # compute overall status
    approvers = d.get("approvers") or []
    approved = [x for x in decisions if x["decision"] == "approve"]
    rejected = [x for x in decisions if x["decision"] == "reject"]
    if rejected:
        status = "rejected"
    elif len(approved) >= len(approvers):
        status = "approved"
    else:
        status = "pending"

    db.execute(text("""
        UPDATE approval_requests
        SET decisions=CAST(:dec AS jsonb), status=:status, updated_at=NOW()
        WHERE id=:id
    """), {"dec": json.dumps(decisions), "status": status, "id": aid})
    db.commit()
    return {"ok": True, "status": status, "decisions": decisions}

@router.post("/{aid}/comment")
def add_comment(aid: str, body: ApprovalComment, db: Session = Depends(get_db)):
    import json
    ensure_table(db)
    row = db.execute(text("SELECT * FROM approval_requests WHERE id=:id"), {"id": aid}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    d = row_to_dict(row)
    comments = d.get("comments") or []
    from datetime import datetime
    comments.append({"author": body.author, "text": body.text, "at": datetime.utcnow().isoformat()})
    db.execute(text("UPDATE approval_requests SET comments=CAST(:c AS jsonb) WHERE id=:id"),
               {"c": json.dumps(comments), "id": aid})
    db.commit()
    return {"ok": True}

@router.delete("/{aid}")
def delete_approval(aid: str, db: Session = Depends(get_db)):
    ensure_table(db)
    db.execute(text("DELETE FROM approval_requests WHERE id=:id"), {"id": aid})
    db.commit()
    return {"ok": True}
