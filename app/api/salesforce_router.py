"""Salesforce connector (Integrations → Salesforce). Admins only, except the OAuth
callback, which Salesforce's redirect reaches without an app session."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import require_admin
from app.core.database import get_db
from app.core.secret_box import encrypt
from app.core.tenant import get_current_tenant_id
from app.services import salesforce as sf

router = APIRouter(prefix="/api/salesforce", tags=["salesforce"])
public_router = APIRouter(prefix="/api/salesforce", tags=["salesforce"])

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:4200")


class AppIn(BaseModel):
    client_id: str
    client_secret: Optional[str] = None     # omit to keep the stored secret
    environment: str = "production"         # production | sandbox | https://….my.salesforce.com


class Approve(BaseModel):
    account_name: Optional[str] = None
    subject: Optional[str] = None


def _fail(e: Exception):
    raise HTTPException(400, str(e))


@router.get("/status")
def status(user=Depends(require_admin), db: Session = Depends(get_db)):
    sf.ensure_tables(db)
    c = sf.connection(db, get_current_tenant_id())
    return {"configured": bool(c), "connected": bool(c and c["instance_url"] and c["refresh_token_enc"]),
            "login_host": c["login_host"] if c else sf.LOGIN_HOSTS["production"], "client_id": c["client_id"] if c else "",
            "org_name": c["org_name"] if c else None, "user_name": c["user_name"] if c else None,
            "instance_url": c["instance_url"] if c else None, "redirect_uri": sf.redirect_uri()}


@router.put("/app")
def save_app(body: AppIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    sf.ensure_tables(db)
    try:
        host = sf.login_host(body.environment)
    except sf.SFError as e:
        _fail(e)
    tid = get_current_tenant_id()
    existing = sf.connection(db, tid)
    if not body.client_secret and not existing:
        raise HTTPException(400, "Enter the consumer secret")
    db.execute(text("""INSERT INTO sf_connections (tenant_id, login_host, client_id, client_secret_enc)
        VALUES (:t, :h, :c, :s) ON CONFLICT (tenant_id) DO UPDATE SET login_host = EXCLUDED.login_host,
        client_id = EXCLUDED.client_id, client_secret_enc = EXCLUDED.client_secret_enc, updated_at = now()"""),
        {"t": tid, "h": host, "c": body.client_id.strip(),
         "s": encrypt(body.client_secret.strip()) if body.client_secret else existing["client_secret_enc"]})
    db.commit()
    return {"success": True}


@router.get("/connect")
def connect(user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return {"url": sf.authorize_url(db, get_current_tenant_id())}
    except sf.SFError as e:
        _fail(e)


@public_router.get("/callback")
def callback(state: str = "", code: str = "", error: str = "", error_description: str = "",
             db: Session = Depends(get_db)):
    if error or not code:
        return RedirectResponse(f"{DASHBOARD_URL}/integrations?sf=error&reason={(error_description or error or 'cancelled')[:120]}")
    try:
        sf.ensure_tables(db)
        sf.complete_oauth(db, state, code)
        return RedirectResponse(f"{DASHBOARD_URL}/integrations?sf=connected")
    except Exception as e:
        return RedirectResponse(f"{DASHBOARD_URL}/integrations?sf=error&reason={str(e)[:120]}")


@router.post("/disconnect")
def disconnect(user=Depends(require_admin), db: Session = Depends(get_db)):
    sf.disconnect(db, get_current_tenant_id())
    return {"success": True}


@router.get("/documents")
def documents(user=Depends(require_admin), db: Session = Depends(get_db)):
    """Recent processed documents, and the latest draft or send for each."""
    sf.ensure_tables(db)
    rows = db.execute(text("""SELECT d.id::text id, d.filename, d.doc_type, d.created_at,
            p.id push_id, p.status, p.draft, p.checks, p.sf_account_id, p.error
        FROM documents d LEFT JOIN LATERAL (SELECT * FROM sf_pushes q WHERE q.document_id = d.id::text
            AND q.tenant_id = d.tenant_id::text ORDER BY q.id DESC LIMIT 1) p ON true
        WHERE d.tenant_id::text = :t AND d.status = 'completed'
        ORDER BY d.created_at DESC LIMIT 100"""), {"t": get_current_tenant_id()}).mappings()
    return {"documents": [dict(r) for r in rows]}


@router.post("/documents/prepare/{document_id}")
def prepare(document_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        sf.ensure_tables(db)
        return sf.prepare(db, get_current_tenant_id(), document_id, user.get("username"))
    except sf.SFError as e:
        _fail(e)


@router.post("/pushes/{push_id}/approve")
def approve(push_id: int, body: Approve, user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return sf.send(db, get_current_tenant_id(), push_id, user.get("username"),
                       {k: v for k, v in body.model_dump().items() if v})
    except sf.SFError as e:
        _fail(e)


@router.post("/pushes/{push_id}/discard")
def discard(push_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    db.execute(text("UPDATE sf_pushes SET status='discarded' WHERE id=:id AND tenant_id=:t AND status <> 'sent'"),
               {"id": push_id, "t": get_current_tenant_id()})
    db.commit()
    return {"success": True}
