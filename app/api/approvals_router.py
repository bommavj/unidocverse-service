"""
Approval Workflows — route documents and agent actions for sign-off.

Requests come from people (+ New Request), the research agent (write actions and
clarifications wait here for a person) and contract lifecycle entries moved to
pending_approval. Everything is scoped to the caller's organisation; the approver
is always the signed-in user, and must be one of the request's approvers (admins
can decide any request). A decision on a contract approval moves the contract:
approved → active, rejected / changes requested → review.
"""
import json
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import get_current_user
from app.core.audit import record as audit
from app.core.database import get_db
from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


# ── Schemas ─────────────────────────────────────────────────────────────────
class ApprovalCreate(BaseModel):
    doc_id: str
    title: str
    description: Optional[str] = None
    approvers: List[str]           # usernames or emails
    due_date: Optional[str] = None
    priority: str = "normal"       # low | normal | high | urgent


class ApprovalDecision(BaseModel):
    decision: str                  # approve | reject | request_changes
    comment: Optional[str] = None
    approver: Optional[str] = None  # ignored: the signed-in user decides


class ApprovalComment(BaseModel):
    text: str
    author: Optional[str] = None    # ignored: the signed-in user comments


# ── DB helpers ───────────────────────────────────────────────────────────────
def ensure_table(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS approval_requests (
            id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
            approvers JSONB DEFAULT '[]', decisions JSONB DEFAULT '[]', comments JSONB DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending', priority TEXT DEFAULT 'normal', due_date TEXT,
            created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())"""))
    for col in ("tenant_id TEXT", "requested_by TEXT", "source TEXT NOT NULL DEFAULT 'manual'",
                "clm_id TEXT", "expires_at TIMESTAMP"):
        db.execute(text(f"ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS {col}"))
    db.commit()


def row_to_dict(row):
    return dict(row._mapping) if row else None


def _t() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


def _own(db: Session, aid: str):
    row = db.execute(text("SELECT * FROM approval_requests WHERE id = :id AND tenant_id = :t"),
                     {"id": aid, "t": _t()}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    return row_to_dict(row)


def _identities(db: Session, user: dict) -> set:
    """Names a user may appear under in an approvers list."""
    u = db.execute(text("SELECT username, email, full_name FROM users WHERE id::text = :i"),
                   {"i": str(user["id"])}).fetchone()
    names = {user["username"].lower()}
    if u:
        names |= {(u.email or "").lower(), (u.full_name or "").lower()}
    return {n for n in names if n}


def create_request(db: Session, tenant_id: str, doc_id: str, title: str, description: str,
                   approvers: List[str], requested_by: str, source: str = "manual", priority: str = "normal",
                   due_date: Optional[str] = None, clm_id: Optional[str] = None,
                   expires_days: Optional[int] = None) -> str:
    """Shared by the API, the research agent and contract lifecycle."""
    ensure_table(db)
    aid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO approval_requests (id, doc_id, title, description, approvers, due_date, priority,
            tenant_id, requested_by, source, clm_id, expires_at)
        VALUES (:id, :doc_id, :title, :desc, CAST(:approvers AS jsonb), :due, :priority,
            :t, :by, :src, :clm, CASE WHEN :exp IS NULL THEN NULL ELSE NOW() + make_interval(days => :exp) END)
    """), {"id": aid, "doc_id": doc_id or "", "title": title, "desc": description,
           "approvers": json.dumps(approvers), "due": due_date, "priority": priority, "t": tenant_id,
           "by": requested_by, "src": source, "clm": clm_id, "exp": expires_days})
    db.commit()
    audit("approval.requested", actor=requested_by, doc_id=doc_id or None, detail=title,
          extra={"approval_id": aid, "approvers": approvers, "source": source, "clm_id": clm_id}, tenant_id=tenant_id)
    return aid


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("")
def list_approvals(status: Optional[str] = None, db: Session = Depends(get_db)):
    ensure_table(db)
    q = """SELECT a.*, d.filename, d.doc_type FROM approval_requests a
           LEFT JOIN documents d ON d.id = a.doc_id WHERE a.tenant_id = :t AND a.source <> 'agent'"""
    params = {"t": _t()}
    if status:
        q += " AND a.status = :status"
        params["status"] = status
    q += " ORDER BY a.created_at DESC"
    return [row_to_dict(r) for r in db.execute(text(q), params).fetchall()]


@router.post("")
def create_approval(body: ApprovalCreate, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.approvers:
        raise HTTPException(400, "Choose at least one approver")
    if body.doc_id and not db.execute(text("SELECT 1 FROM documents WHERE id::text = :d AND tenant_id::text = :t"),
                                      {"d": body.doc_id, "t": _t()}).fetchone():
        raise HTTPException(404, "Document not found")
    aid = create_request(db, _t(), body.doc_id, body.title, body.description, body.approvers,
                         user["username"], "manual", body.priority, body.due_date)
    return {"request": row_to_dict(db.execute(text("SELECT * FROM approval_requests WHERE id=:id"), {"id": aid}).fetchone())}


@router.get("/stats")
def approval_stats(db: Session = Depends(get_db)):
    ensure_table(db)
    rows = db.execute(text("SELECT status, COUNT(*) AS cnt FROM approval_requests WHERE tenant_id = :t AND source <> 'agent' GROUP BY status"),
                      {"t": _t()}).fetchall()
    return {r._mapping["status"]: r._mapping["cnt"] for r in rows}


@router.get("/{aid}")
def get_approval(aid: str, db: Session = Depends(get_db)):
    ensure_table(db)
    row = db.execute(text("""SELECT a.*, d.filename, d.doc_type, d.summary
        FROM approval_requests a LEFT JOIN documents d ON d.id = a.doc_id
        WHERE a.id = :id AND a.tenant_id = :t"""), {"id": aid, "t": _t()}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    return row_to_dict(row)


@router.post("/{aid}/decide")
def decide(aid: str, body: ApprovalDecision, request: Request,
           user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_table(db)
    if body.decision not in ("approve", "reject", "request_changes"):
        raise HTTPException(400, "Decision must be approve, reject or request_changes")
    d = _own(db, aid)
    if d["status"] in ("expired",):
        raise HTTPException(409, "This request has expired")
    approvers = [str(a).lower() for a in (d.get("approvers") or [])]
    me = _identities(db, user)
    if user.get("role") != "admin" and not (me & set(approvers)):
        raise HTTPException(403, "You're not an approver on this request")
    who = user["username"]
    decisions = [x for x in (d.get("decisions") or []) if str(x.get("approver", "")).lower() not in me]
    decisions.append({"approver": who, "decision": body.decision, "comment": body.comment,
                      "at": datetime.utcnow().isoformat()})

    approved = {str(x["approver"]).lower() for x in decisions if x["decision"] == "approve"}
    if any(x["decision"] == "reject" for x in decisions):
        status = "rejected"
    elif any(x["decision"] == "request_changes" for x in decisions):
        status = "changes_requested"
    elif len(approved) >= max(1, len(approvers)) or (user.get("role") == "admin" and d.get("source") in ("agent", "clarification")):
        status = "approved"
    else:
        status = "pending"

    db.execute(text("UPDATE approval_requests SET decisions=CAST(:dec AS jsonb), status=:status, updated_at=NOW() WHERE id=:id"),
               {"dec": json.dumps(decisions), "status": status, "id": aid})
    db.commit()
    audit(f"approval.{body.decision}", actor=who, doc_id=d.get("doc_id") or None, detail=d["title"],
          ip=request.client.host if request.client else None,
          extra={"approval_id": aid, "status": status, "comment": body.comment})

    # Contract approval → move the contract
    if d.get("clm_id") and status in ("approved", "rejected", "changes_requested"):
        from app.api.clm_router import move_entry
        move_entry(db, d["clm_id"], "active" if status == "approved" else "review", who,
                   f"Approval {status.replace('_', ' ')}" + (f": {body.comment}" if body.comment else ""))
    return {"ok": True, "status": status, "decisions": decisions}


@router.post("/{aid}/comment")
def add_comment(aid: str, body: ApprovalComment, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_table(db)
    d = _own(db, aid)
    comments = (d.get("comments") or []) + [{"author": user["username"], "text": body.text,
                                              "at": datetime.utcnow().isoformat()}]
    db.execute(text("UPDATE approval_requests SET comments=CAST(:c AS jsonb), updated_at=NOW() WHERE id=:id"),
               {"c": json.dumps(comments), "id": aid})
    db.commit()
    return {"ok": True}


@router.delete("/{aid}")
def delete_approval(aid: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_table(db)
    d = _own(db, aid)
    if user.get("role") != "admin" and d.get("requested_by") != user["username"]:
        raise HTTPException(403, "Only the requester or an admin can withdraw a request")
    db.execute(text("UPDATE approval_requests SET status='withdrawn', updated_at=NOW() WHERE id=:id"), {"id": aid})
    db.commit()
    audit("approval.withdrawn", actor=user["username"], doc_id=d.get("doc_id") or None, detail=d["title"],
          extra={"approval_id": aid})
    return {"ok": True}


def expire_stale() -> int:
    """Requests past their expiry → expired (runs nightly). Agent confirmations lapse after a day."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        ensure_table(db)
        rows = db.execute(text("""UPDATE approval_requests SET status='expired', updated_at=NOW()
            WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < NOW()
            RETURNING id, tenant_id, title, doc_id""")).fetchall()
        db.commit()
        for r in rows:
            audit("approval.expired", detail=r.title, doc_id=r.doc_id or None, tenant_id=r.tenant_id,
                  extra={"approval_id": r.id})
        return len(rows)
    finally:
        db.close()
