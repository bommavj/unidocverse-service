"""
Where did this document go? One view over every outbound connector.

  GET /api/integrations/summary            per system: connected, account name, sent / failed in 7 days, last send
  GET /api/integrations/sends?document_id= the send history (QuickBooks bills, Salesforce sends, webhook deliveries),
                                           newest first, each with a link that opens the record in the other system

Every user can see where their organisation's documents were sent; connecting systems stays admin-only.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

_QB_APP = {"sandbox": "https://app.sandbox.qbo.intuit.com", "production": "https://app.qbo.intuit.com"}


def _t() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


def _exists(db, table: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    t, out = _t(), {}
    if _exists(db, "qb_connections"):
        c = db.execute(text("SELECT company_name, environment, realm_id, refresh_token_enc FROM qb_connections WHERE tenant_id = :t"),
                       {"t": t}).mappings().fetchone()
        s = db.execute(text("""SELECT count(*) FILTER (WHERE status = 'posted' AND posted_at > now() - interval '7 days') sent,
            count(*) FILTER (WHERE status = 'failed' AND created_at > now() - interval '7 days') failed,
            max(posted_at) last FROM qb_pushes WHERE tenant_id = :t"""), {"t": t}).mappings().fetchone()
        out["quickbooks"] = {"configured": bool(c), "connected": bool(c and c["realm_id"] and c["refresh_token_enc"]),
                             "account": c["company_name"] if c else None, "sent_7d": s["sent"], "failed_7d": s["failed"],
                             "last_sent_at": s["last"]}
    if _exists(db, "sf_connections"):
        c = db.execute(text("SELECT org_name, instance_url, refresh_token_enc FROM sf_connections WHERE tenant_id = :t"),
                       {"t": t}).mappings().fetchone()
        s = db.execute(text("""SELECT count(*) FILTER (WHERE status = 'sent' AND sent_at > now() - interval '7 days') sent,
            count(*) FILTER (WHERE status = 'failed' AND created_at > now() - interval '7 days') failed,
            max(sent_at) last FROM sf_pushes WHERE tenant_id = :t"""), {"t": t}).mappings().fetchone()
        out["salesforce"] = {"configured": bool(c), "connected": bool(c and c["instance_url"] and c["refresh_token_enc"]),
                             "account": (c["org_name"] or c["instance_url"]) if c else None, "sent_7d": s["sent"],
                             "failed_7d": s["failed"], "last_sent_at": s["last"]}
    if _exists(db, "webhook_endpoints"):
        n = db.execute(text("SELECT count(*) FROM webhook_endpoints WHERE tenant_id = :t AND active"), {"t": t}).scalar()
        s = db.execute(text("""SELECT count(*) FILTER (WHERE status = 'delivered' AND created_at > now() - interval '7 days') sent,
            count(*) FILTER (WHERE status = 'failed' AND created_at > now() - interval '7 days') failed,
            max(delivered_at) last FROM webhook_deliveries WHERE tenant_id = :t"""), {"t": t}).mappings().fetchone()
        out["webhooks"] = {"configured": bool(n), "connected": bool(n), "account": f"{n} endpoint{'s' if n != 1 else ''}" if n else None,
                           "sent_7d": s["sent"], "failed_7d": s["failed"], "last_sent_at": s["last"]}
    return out


@router.get("/sends")
def sends(document_id: Optional[str] = None, system: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)):
    t, rows = _t(), []
    limit = max(1, min(limit, 200))
    doc_filter = " AND p.document_id = :d" if document_id else ""
    params = {"t": t, "d": document_id, "n": limit}
    if system in (None, "quickbooks") and _exists(db, "qb_pushes"):
        env = db.execute(text("SELECT environment FROM qb_connections WHERE tenant_id = :t"), {"t": t}).scalar() or "production"
        for r in db.execute(text(f"""SELECT p.id, p.document_id, d.filename, p.status, p.qb_bill_id, p.draft->>'vendor' vendor,
                p.draft->>'total' total, p.error, coalesce(p.approved_by, p.prepared_by) who, coalesce(p.posted_at, p.created_at) at
            FROM qb_pushes p LEFT JOIN documents d ON d.id::text = p.document_id
            WHERE p.tenant_id = :t AND p.status IN ('posted', 'failed'){doc_filter} ORDER BY at DESC LIMIT :n"""), params).mappings():
            rows.append({"system": "quickbooks", "id": f"qb-{r['id']}", "document_id": r["document_id"], "filename": r["filename"],
                         "status": "sent" if r["status"] == "posted" else "failed", "error": r["error"], "by": r["who"], "at": r["at"],
                         "target": f"Bill {r['qb_bill_id']} · {r['vendor']} · {r['total']}" if r["qb_bill_id"] else r["vendor"],
                         "url": f"{_QB_APP.get(env, _QB_APP['production'])}/app/bill?txnId={r['qb_bill_id']}" if r["qb_bill_id"] else None})
    if system in (None, "salesforce") and _exists(db, "sf_pushes"):
        inst = db.execute(text("SELECT instance_url FROM sf_connections WHERE tenant_id = :t"), {"t": t}).scalar()
        for r in db.execute(text(f"""SELECT p.id, p.document_id, d.filename, p.status, p.sf_account_id, p.draft->>'account_name' account,
                p.error, coalesce(p.approved_by, p.prepared_by) who, coalesce(p.sent_at, p.created_at) at
            FROM sf_pushes p LEFT JOIN documents d ON d.id::text = p.document_id
            WHERE p.tenant_id = :t AND p.status IN ('sent', 'failed'){doc_filter} ORDER BY at DESC LIMIT :n"""), params).mappings():
            rows.append({"system": "salesforce", "id": f"sf-{r['id']}", "document_id": r["document_id"], "filename": r["filename"],
                         "status": r["status"], "error": r["error"], "by": r["who"], "at": r["at"],
                         "target": f"Account · {r['account']}",
                         "url": f"{inst}/lightning/r/Account/{r['sf_account_id']}/view" if inst and r["sf_account_id"] else None})
    if system in (None, "webhooks") and _exists(db, "webhook_deliveries"):
        wf = " AND (w.payload->>'document_id' = :d OR w.payload->'data'->>'document_id' = :d)" if document_id else ""
        for r in db.execute(text(f"""SELECT w.id, w.event, w.status, w.response_code, w.attempts, e.url,
                coalesce(w.payload->>'document_id', w.payload->'data'->>'document_id') document_id, coalesce(w.delivered_at, w.created_at) at
            FROM webhook_deliveries w JOIN webhook_endpoints e ON e.id = w.endpoint_id
            WHERE w.tenant_id = :t{wf} ORDER BY at DESC LIMIT :n"""), params).mappings():
            rows.append({"system": "webhooks", "id": f"wh-{r['id']}", "document_id": r["document_id"], "filename": None,
                         "status": {"delivered": "sent", "failed": "failed"}.get(r["status"], "pending"),
                         "error": f"HTTP {r['response_code']} after {r['attempts']} attempts" if r["status"] == "failed" else None,
                         "by": "automatic", "at": r["at"], "target": f"{r['event']} → {r['url']}", "url": None})
    rows.sort(key=lambda r: str(r["at"] or ""), reverse=True)
    return {"sends": rows[:limit]}


def notify(tenant_id: str, ok: bool, system: str, filename: str, target: str, error: str = "") -> None:
    """Bell notification after a send, so the person who approved it (and their team) sees the outcome."""
    import uuid
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO notifications (id, doc_type, filename, severity, title, message, is_read, tenant_id,
            created_at, updated_at) VALUES (:id, 'integration', :f, :sev, :title, :msg, FALSE, CAST(:t AS uuid), now(), now())"""),
            {"id": str(uuid.uuid4()), "f": filename or "", "sev": "info" if ok else "critical",
             "title": f"Sent to {system}" if ok else f"{system} send failed",
             "msg": (f"{filename} → {target}" if ok else f"{filename}: {error}")[:500], "t": tenant_id})
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
