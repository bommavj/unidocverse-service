"""Outbound webhooks (Settings → Integrations → Webhooks). Admins only."""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import require_admin
from app.core.database import get_db
from app.core.secret_box import encrypt
from app.core.tenant import get_current_tenant_id
from app.services import webhooks

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class EndpointIn(BaseModel):
    url: str
    description: Optional[str] = None
    events: List[str] = []          # empty = all events
    active: bool = True


def _own(db: Session, endpoint_id: int):
    row = db.execute(text("SELECT * FROM webhook_endpoints WHERE id = :id AND tenant_id = :t"),
                     {"id": endpoint_id, "t": get_current_tenant_id()}).mappings().fetchone()
    if not row:
        raise HTTPException(404, "Webhook not found")
    return row


def _validate(body: EndpointIn) -> None:
    try:
        webhooks.check_url(body.url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    unknown = [e for e in body.events if e not in webhooks.EVENTS]
    if unknown:
        raise HTTPException(400, f"Unknown events: {', '.join(unknown)}")


@router.get("")
def list_endpoints(user=Depends(require_admin), db: Session = Depends(get_db)):
    webhooks.ensure_tables(db)
    rows = db.execute(text("""SELECT e.id, e.url, e.description, e.events, e.active, e.created_at,
            (SELECT count(*) FROM webhook_deliveries d WHERE d.endpoint_id = e.id AND d.status = 'failed') failed,
            (SELECT max(d.delivered_at) FROM webhook_deliveries d WHERE d.endpoint_id = e.id) last_ok
        FROM webhook_endpoints e WHERE e.tenant_id = :t ORDER BY e.id"""), {"t": get_current_tenant_id()}).mappings()
    return {"events": webhooks.EVENTS,
            "endpoints": [{**dict(r), "created_at": r["created_at"].isoformat(),
                           "last_ok": r["last_ok"].isoformat() if r["last_ok"] else None} for r in rows]}


@router.post("")
def create_endpoint(body: EndpointIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    webhooks.ensure_tables(db)
    _validate(body)
    secret = webhooks.new_secret()
    eid = db.execute(text("""INSERT INTO webhook_endpoints (tenant_id, url, description, secret_enc, events, active, created_by)
        VALUES (:t, :u, :d, :s, :e, :a, :by) RETURNING id"""),
        {"t": get_current_tenant_id(), "u": body.url.strip(), "d": body.description, "s": encrypt(secret),
         "e": body.events, "a": body.active, "by": user.get("username")}).scalar()
    db.commit()
    # The secret is shown once; receivers use it to verify X-UDV-Signature
    return {"id": eid, "secret": secret}


@router.put("/{endpoint_id}")
def update_endpoint(endpoint_id: int, body: EndpointIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    _own(db, endpoint_id)
    _validate(body)
    db.execute(text("UPDATE webhook_endpoints SET url=:u, description=:d, events=:e, active=:a WHERE id=:id"),
               {"u": body.url.strip(), "d": body.description, "e": body.events, "a": body.active, "id": endpoint_id})
    db.commit()
    return {"success": True}


@router.post("/{endpoint_id}/rotate-secret")
def rotate_secret(endpoint_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    _own(db, endpoint_id)
    secret = webhooks.new_secret()
    db.execute(text("UPDATE webhook_endpoints SET secret_enc=:s WHERE id=:id"), {"s": encrypt(secret), "id": endpoint_id})
    db.commit()
    return {"secret": secret}


@router.delete("/{endpoint_id}")
def delete_endpoint(endpoint_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    _own(db, endpoint_id)
    db.execute(text("DELETE FROM webhook_endpoints WHERE id=:id"), {"id": endpoint_id})
    db.commit()
    return {"success": True}


@router.post("/{endpoint_id}/test")
def send_test(endpoint_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    ep = _own(db, endpoint_id)
    payload = {"event": "webhook.test", "data": {"message": "Test delivery from UniDocVerse", "endpoint_id": endpoint_id}}
    did = db.execute(text("""INSERT INTO webhook_deliveries (endpoint_id, tenant_id, event, payload)
        VALUES (:e, :t, 'webhook.test', CAST(:p AS jsonb)) RETURNING id"""),
        {"e": ep["id"], "t": ep["tenant_id"], "p": json.dumps(payload)}).scalar()
    db.commit()
    return webhooks.deliver(did)


@router.get("/{endpoint_id}/deliveries")
def deliveries(endpoint_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    _own(db, endpoint_id)
    rows = db.execute(text("""SELECT id, event, status, attempts, response_code, left(response_body, 300) response_body,
            created_at, delivered_at, next_attempt_at FROM webhook_deliveries WHERE endpoint_id = :e
        ORDER BY id DESC LIMIT 50"""), {"e": endpoint_id}).mappings()
    return {"deliveries": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]}


@router.post("/deliveries/{delivery_id}/redeliver")
def redeliver(delivery_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    row = db.execute(text("SELECT id FROM webhook_deliveries WHERE id = :id AND tenant_id = :t"),
                     {"id": delivery_id, "t": get_current_tenant_id()}).fetchone()
    if not row:
        raise HTTPException(404, "Delivery not found")
    db.execute(text("UPDATE webhook_deliveries SET status='pending', attempts=0, next_attempt_at=now() WHERE id=:id"),
               {"id": delivery_id})
    db.commit()
    return webhooks.deliver(delivery_id)
