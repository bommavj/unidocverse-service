"""
Chatbot session router.

Each bank customer gets an isolated session:
  - Session token ties together their uploaded documents
  - Questions only search within their own documents
  - Sessions expire after 2 hours of inactivity

Banks embed the widget on their site; the widget creates a session on first load
and sends the token with every upload and question.
"""

import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from app.api.auth_router import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])

SESSION_TTL_HOURS = 2


# ── Session creation ──────────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    token: str
    created_at: str
    expires_at: str


@router.post("/session", response_model=SessionResponse)
def create_session(db: Session = Depends(get_db)):
    """Create a new isolated chatbot session. Returns a token the client stores."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    tenant_id = get_current_tenant_id() or DEFAULT_TENANT_ID

    db.execute(text("""
        INSERT INTO chatbot_sessions (token, doc_ids, created_at, last_active_at, expires_at, tenant_id)
        VALUES (:token, '[]'::jsonb, :now, :now, :expires, :tenant_id)
    """), {"token": token, "now": now, "expires": expires, "tenant_id": tenant_id})
    db.commit()

    return {"token": token, "created_at": now.isoformat(), "expires_at": expires.isoformat()}


@router.get("/session/{token}")
def get_session(token: str, db: Session = Depends(get_db)):
    """Fetch session info — doc_ids uploaded so far, expiry."""
    row = _get_session_row(token, db)
    return {
        "token":          token,
        "doc_ids":        row.doc_ids or [],
        "created_at":     row.created_at.isoformat(),
        "last_active_at": row.last_active_at.isoformat(),
        "expires_at":     row.expires_at.isoformat(),
    }


# ── Document upload ───────────────────────────────────────────────────────────

@router.post("/upload/{token}")
async def chatbot_upload(token: str, file: UploadFile = File(...)):
    """
    Upload a document for this session.
    Stores the doc_id in the session so questions are scoped to it.
    """
    db = SessionLocal()
    try:
        _get_session_row(token, db)   # validates token + not expired

        from app.services.processing_service import ProcessingService
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        file_bytes = await file.read()
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                ThreadPoolExecutor(max_workers=1),
                ProcessingService.process_sync,
                file_bytes, file.filename, file.content_type,
            )
        except Exception as exc:
            logger.exception(f"[ChatbotUpload] process_sync failed: {exc}")
            raise HTTPException(500, str(exc))

        if result.get("status") == "error":
            raise HTTPException(500, result.get("error", "Processing failed"))

        doc_id = result.get("document_id") or result.get("id")
        if doc_id:
            db.execute(text("""
                UPDATE chatbot_sessions
                SET doc_ids = doc_ids || CAST(:new_id AS jsonb),
                    last_active_at = NOW()
                WHERE token = :token
            """), {"token": token, "new_id": f'"{doc_id}"'})
            db.commit()

        return {"status": "ok", "document_id": doc_id, "filename": file.filename}
    finally:
        db.close()


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_session_row(token: str, db: Session):
    row = db.execute(text("""
        SELECT token, doc_ids, created_at, last_active_at, expires_at
        FROM chatbot_sessions WHERE token = :token
    """), {"token": token}).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(410, "Session expired")
    # Touch last_active
    db.execute(text("UPDATE chatbot_sessions SET last_active_at = NOW() WHERE token = :t"), {"t": token})
    db.commit()
    return row


def get_session_doc_ids(token: str) -> list[str]:
    """Used by research router to scope queries to this session's documents."""
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT doc_ids FROM chatbot_sessions WHERE token = :token
        """), {"token": token}).fetchone()
        return list(row.doc_ids or []) if row else []
    finally:
        db.close()
