"""
Early-adopter pilot report (Settings → Pilot report). Admin only.

Measures what the organisation did with UniDocVerse over a period — documents processed, pages
read, processing time, questions answered with citations — and adds the organisation's own
feedback (time per document before/after, accuracy, rating, testimonial, permission to publish).
Nothing is sent anywhere: the report is saved here and the organisation exports it to share.
"""
import json
import uuid
from datetime import date, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import require_admin
from app.core.audit import record
from app.core.database import get_db
from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id

router = APIRouter(prefix="/api/pilot", tags=["pilot"])
_READY = False


def _tid() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


def _ensure(db: Session) -> None:
    """Installs that haven't run V72 yet."""
    global _READY
    if _READY:
        return
    db.execute(text("SET LOCAL lock_timeout = '3s'"))
    db.execute(text("""CREATE TABLE IF NOT EXISTS pilot_reports (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL, checkpoint INTEGER NOT NULL,
        period_start DATE NOT NULL, period_end DATE NOT NULL, organization TEXT, contact TEXT,
        metrics JSONB NOT NULL DEFAULT '{}'::jsonb, feedback JSONB NOT NULL DEFAULT '{}'::jsonb,
        publish_permission BOOLEAN NOT NULL DEFAULT FALSE, created_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
    db.commit()
    _READY = True


def _one(db: Session, sql: str, p: Dict) -> Any:
    try:
        return db.execute(text(sql), p).scalar()
    except Exception:
        db.rollback()
        return None


def measure(db: Session, start: date, end: date) -> Dict[str, Any]:
    p = {"t": _tid(), "s": start, "e": end + timedelta(days=1)}
    docs = "FROM documents WHERE tenant_id::text = :t AND uploaded_at >= :s AND uploaded_at < :e"
    by_type = []
    try:
        by_type = [{"type": r[0] or "other", "count": r[1]} for r in db.execute(text(
            f"SELECT doc_type, count(*) {docs} AND status = 'completed' GROUP BY 1 ORDER BY 2 DESC LIMIT 12"), p)]
    except Exception:
        db.rollback()
    asked = _one(db, "SELECT count(*) FROM research_sessions WHERE tenant_id::text = :t AND created_at >= :s AND created_at < :e", p) or 0
    cited = _one(db, """SELECT count(*) FROM research_sessions WHERE tenant_id::text = :t AND created_at >= :s
        AND created_at < :e AND sources IS NOT NULL AND sources::text NOT IN ('[]', '{}', '')""", p) or 0
    not_found = _one(db, """SELECT count(*) FROM research_sessions WHERE tenant_id::text = :t AND created_at >= :s
        AND created_at < :e AND answer ILIKE '%not found in the documents%'""", p) or 0
    secs = _one(db, f"""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM processed_at - uploaded_at))
        {docs} AND processed_at > uploaded_at
        AND processed_at < uploaded_at + interval '1 day'""", p)   # reprocessing later moves processed_at
    return {
        "documents_uploaded": _one(db, f"SELECT count(*) {docs}", p) or 0,
        "documents_processed": _one(db, f"SELECT count(*) {docs} AND status = 'completed'", p) or 0,
        "documents_failed": _one(db, f"SELECT count(*) {docs} AND status = 'failed'", p) or 0,
        "pages_read": _one(db, f"""SELECT count(DISTINCT (c.document_id, c.page_number)) FROM document_chunks c
            JOIN documents d ON d.id = c.document_id WHERE d.tenant_id::text = :t AND d.uploaded_at >= :s
            AND d.uploaded_at < :e""", p) or 0,
        "median_processing_seconds": round(float(secs), 1) if secs is not None else None,
        "documents_by_type": by_type,
        "questions_asked": asked,
        "answers_with_citations": cited,
        "answers_citation_rate": round(100 * cited / asked, 1) if asked else None,
        "answers_not_found": not_found,
        "active_users": _one(db, """SELECT count(DISTINCT actor) FROM audit_log WHERE tenant_id::text = :t
            AND created_at >= :s AND created_at < :e AND actor NOT IN ('system', 'anonymous', 'api-token')""", p) or 0,
        "web_searches": _one(db, """SELECT count(*) FROM audit_log WHERE tenant_id::text = :t AND action = 'research.web_search'
            AND created_at >= :s AND created_at < :e""", p) or 0,
    }


def _period(checkpoint: int, start: Optional[date]) -> tuple:
    if checkpoint not in (30, 60, 90):
        raise HTTPException(400, "Checkpoint must be 30, 60 or 90 days")
    s = start or (date.today() - timedelta(days=checkpoint))
    return s, s + timedelta(days=checkpoint)


@router.get("/metrics")
def metrics(checkpoint: int = Query(30), start: Optional[date] = Query(None), _=Depends(require_admin),
            db: Session = Depends(get_db)):
    s, e = _period(checkpoint, start)
    return {"checkpoint": checkpoint, "period_start": s, "period_end": e, "metrics": measure(db, s, e)}


class ReportSave(BaseModel):
    checkpoint: int
    start: Optional[date] = None
    organization: str
    contact: Optional[str] = None
    feedback: Dict[str, Any] = {}
    publish_permission: bool = False


@router.post("/reports")
def save_report(body: ReportSave, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    s, e = _period(body.checkpoint, body.start)
    m = measure(db, s, e)
    fb = dict(body.feedback or {})
    try:   # time saved = processed documents × (minutes before − minutes with UniDocVerse)
        before, after = float(fb.get("minutes_per_doc_before")), float(fb.get("minutes_per_doc_after"))
        m["estimated_hours_saved"] = round(max(0.0, before - after) * m["documents_processed"] / 60, 1)
    except (TypeError, ValueError):
        pass
    rid = str(uuid.uuid4())
    db.execute(text("""INSERT INTO pilot_reports (id, tenant_id, checkpoint, period_start, period_end, organization,
        contact, metrics, feedback, publish_permission, created_by) VALUES (:id, :t, :c, :s, :e, :o, :ct,
        CAST(:m AS jsonb), CAST(:f AS jsonb), :pp, :by)"""),
        {"id": rid, "t": _tid(), "c": body.checkpoint, "s": s, "e": e, "o": body.organization.strip()[:200],
         "ct": (body.contact or "")[:200], "m": json.dumps(m, default=str), "f": json.dumps(fb, default=str),
         "pp": body.publish_permission, "by": user["username"]})
    db.commit()
    record("pilot.report", actor=user["username"], detail=f"{body.checkpoint}-day pilot report for {body.organization}",
           extra={"report_id": rid, "publish_permission": body.publish_permission})
    return get_report(rid, user, db)


@router.get("/reports")
def list_reports(_=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    rows = db.execute(text("""SELECT id, checkpoint, period_start, period_end, organization, publish_permission,
        created_by, created_at FROM pilot_reports WHERE tenant_id = :t ORDER BY created_at DESC"""), {"t": _tid()}).mappings()
    return {"reports": [dict(r) for r in rows]}


@router.get("/reports/{rid}")
def get_report(rid: str, _=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    r = db.execute(text("SELECT * FROM pilot_reports WHERE id::text = :id AND tenant_id = :t"),
                   {"id": rid, "t": _tid()}).mappings().fetchone()
    if not r:
        raise HTTPException(404, "Report not found")
    return {**dict(r), "app": "UniDocVerse"}
