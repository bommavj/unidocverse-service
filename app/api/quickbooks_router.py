"""QuickBooks Online connector (Settings → Integrations → QuickBooks). Admins only,
except the OAuth callback, which Intuit's redirect reaches without an app session."""
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
from app.services import quickbooks as qb

router = APIRouter(prefix="/api/quickbooks", tags=["quickbooks"])
public_router = APIRouter(prefix="/api/quickbooks", tags=["quickbooks"])

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:4200")


class AppIn(BaseModel):
    client_id: str
    client_secret: Optional[str] = None     # omit to keep the stored secret
    environment: str = "sandbox"


class AccountIn(BaseModel):
    expense_account_id: str


class Approve(BaseModel):
    vendor: Optional[str] = None
    total: Optional[float] = None
    bill_date: Optional[str] = None
    due_date: Optional[str] = None
    doc_number: Optional[str] = None


def _fail(e: Exception):
    raise HTTPException(400, str(e))


@router.get("/status")
def status(user=Depends(require_admin), db: Session = Depends(get_db)):
    qb.ensure_tables(db)
    c = qb.connection(db, get_current_tenant_id())
    return {"configured": bool(c), "connected": bool(c and c["realm_id"] and c["refresh_token_enc"]),
            "environment": c["environment"] if c else "sandbox", "client_id": c["client_id"] if c else "",
            "company_name": c["company_name"] if c else None, "expense_account_id": c["expense_account_id"] if c else None,
            "redirect_uri": qb.redirect_uri()}


@router.put("/app")
def save_app(body: AppIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    qb.ensure_tables(db)
    if body.environment not in qb.API_BASE:
        raise HTTPException(400, "Environment must be sandbox or production")
    tid = get_current_tenant_id()
    existing = qb.connection(db, tid)
    if not body.client_secret and not existing:
        raise HTTPException(400, "Enter the client secret")
    db.execute(text("""INSERT INTO qb_connections (tenant_id, environment, client_id, client_secret_enc)
        VALUES (:t, :e, :c, :s) ON CONFLICT (tenant_id) DO UPDATE SET environment = EXCLUDED.environment,
        client_id = EXCLUDED.client_id, client_secret_enc = EXCLUDED.client_secret_enc, updated_at = now()"""),
        {"t": tid, "e": body.environment, "c": body.client_id.strip(),
         "s": encrypt(body.client_secret.strip()) if body.client_secret else existing["client_secret_enc"]})
    db.commit()
    return {"success": True}


@router.get("/connect")
def connect(user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return {"url": qb.authorize_url(db, get_current_tenant_id())}
    except qb.QBError as e:
        _fail(e)


@public_router.get("/callback")
def callback(state: str = "", code: str = "", realmId: str = "", error: str = "", db: Session = Depends(get_db)):
    if error or not code:
        return RedirectResponse(f"{DASHBOARD_URL}/integrations?qb=error&reason={error or 'cancelled'}")
    try:
        qb.ensure_tables(db)
        qb.complete_oauth(db, state, code, realmId)
        return RedirectResponse(f"{DASHBOARD_URL}/integrations?qb=connected")
    except Exception as e:
        return RedirectResponse(f"{DASHBOARD_URL}/integrations?qb=error&reason={str(e)[:120]}")


@router.post("/disconnect")
def disconnect(user=Depends(require_admin), db: Session = Depends(get_db)):
    qb.disconnect(db, get_current_tenant_id())
    return {"success": True}


@router.get("/accounts")
def accounts(user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return {"accounts": qb.expense_accounts(db, get_current_tenant_id())}
    except qb.QBError as e:
        _fail(e)


@router.put("/expense-account")
def set_account(body: AccountIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    db.execute(text("UPDATE qb_connections SET expense_account_id = :a WHERE tenant_id = :t"),
               {"a": body.expense_account_id, "t": get_current_tenant_id()})
    db.commit()
    return {"success": True}


@router.get("/bills")
def bills(user=Depends(require_admin), db: Session = Depends(get_db)):
    """Invoices with extracted fields, and the latest draft or posting for each."""
    qb.ensure_tables(db)
    rows = db.execute(text("""SELECT d.id, d.filename, d.extracted_facts->>'vendor' vendor,
            d.extracted_facts->>'total_amount' total, d.extracted_facts->>'due_date' due_date,
            p.id push_id, p.status, p.draft, p.checks, p.qb_bill_id, p.error
        FROM documents d LEFT JOIN LATERAL (SELECT * FROM qb_pushes q WHERE q.document_id = d.id
            AND q.tenant_id = d.tenant_id::text ORDER BY q.id DESC LIMIT 1) p ON true
        WHERE d.tenant_id::text = :t AND d.doc_type = 'invoice' AND d.extracted_facts IS NOT NULL
        ORDER BY d.created_at DESC LIMIT 100"""), {"t": get_current_tenant_id()}).mappings()
    return {"bills": [dict(r) for r in rows]}


@router.post("/bills/prepare/{document_id}")
def prepare(document_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return qb.prepare(db, get_current_tenant_id(), document_id, user.get("username"))
    except qb.QBError as e:
        _fail(e)


@router.post("/bills/{push_id}/approve")
def approve(push_id: int, body: Approve, user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return qb.post(db, get_current_tenant_id(), push_id, user.get("username"),
                       {k: v for k, v in body.model_dump().items() if v not in (None, "")})
    except qb.QBError as e:
        _fail(e)


@router.post("/bills/{push_id}/discard")
def discard(push_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    db.execute(text("UPDATE qb_pushes SET status='discarded' WHERE id=:id AND tenant_id=:t AND status <> 'posted'"),
               {"id": push_id, "t": get_current_tenant_id()})
    db.commit()
    return {"success": True}
