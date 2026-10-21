"""
Outbound webhooks: tell other systems (Zapier, Make, n8n, a customer's own app)
when something happens in UniDocVerse.

emit(event, data) queues one delivery per matching endpoint; a background worker
sends them and retries failures with backoff. Every request is signed:

    X-UDV-Event:     document.processed
    X-UDV-Delivery:  <delivery id>
    X-UDV-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, f"{t}.{raw body}")>

Receivers recompute the HMAC with their endpoint secret and reject anything older
than a few minutes.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text

logger = logging.getLogger(__name__)

EVENTS = {
    "document.processed":       "A document finished processing (type, summary, status)",
    "document.facts_extracted": "Structured fields were extracted from a document",
    "quickbooks.bill_created":  "A bill was posted to QuickBooks",
    "quickbooks.bill_failed":   "Posting a bill to QuickBooks failed",
    "salesforce.record_created": "A document was sent to a Salesforce Account",
    "salesforce.failed":        "Sending a document to Salesforce failed",
    "brief.ready":              "Today's brief was built (counts and items)",
}
RETRY_DELAYS = [60, 300, 1800, 7200, 21600]          # then give up: 6 attempts over ~8.5 hours
_TIMEOUT = 10


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def ensure_tables(db) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS webhook_endpoints (
        id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, url TEXT NOT NULL, description TEXT,
        secret_enc TEXT NOT NULL, events TEXT[] NOT NULL DEFAULT '{}', active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), created_by TEXT)"""))
    db.execute(text("""CREATE TABLE IF NOT EXISTS webhook_deliveries (
        id BIGSERIAL PRIMARY KEY, endpoint_id BIGINT NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL, event TEXT NOT NULL, payload JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(), response_code INTEGER,
        response_body TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), delivered_at TIMESTAMPTZ)"""))
    db.commit()


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, body: bytes, ts: Optional[int] = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def check_url(url: str) -> None:
    """Raises ValueError for URLs we won't call: non-HTTPS (except localhost for testing)
    and cloud metadata / link-local addresses."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("https", "http") or not host:
        raise ValueError("Use a full URL, e.g. https://hooks.example.com/udv")
    if p.scheme == "http" and host not in ("localhost", "127.0.0.1", "host.docker.internal"):
        raise ValueError("Webhook URLs must use https://")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, p.port or 443)}
    except socket.gaierror:
        raise ValueError(f"Can't resolve {host}")
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_link_local or str(ip) == "169.254.169.254" or ip.is_multicast or ip.is_unspecified:
            raise ValueError("That address isn't allowed for webhooks")


def emit(event: str, data: Dict[str, Any], tenant_id: Optional[str] = None) -> int:
    """Queue `event` for every active endpoint of the tenant subscribed to it. Never raises."""
    try:
        from app.core.tenant import get_current_tenant_id
        tid = tenant_id or get_current_tenant_id()
        db = _db()
        try:
            ensure_tables(db)
            eps = db.execute(text("""SELECT id FROM webhook_endpoints WHERE tenant_id = :t AND active
                AND (cardinality(events) = 0 OR :e = ANY(events))"""), {"t": tid, "e": event}).fetchall()
            payload = {"event": event, "created_at": datetime.now(timezone.utc).isoformat(), "data": data}
            for ep in eps:
                db.execute(text("""INSERT INTO webhook_deliveries (endpoint_id, tenant_id, event, payload)
                    VALUES (:id, :t, :e, CAST(:p AS jsonb))"""),
                    {"id": ep.id, "t": tid, "e": event, "p": json.dumps(payload, default=str)})
            db.commit()
        finally:
            db.close()
        if eps:
            _wake.set()
        return len(eps)
    except Exception as e:
        logger.warning(f"Webhook emit {event} failed: {e}")
        return 0


def deliver(delivery_id: int) -> Dict[str, Any]:
    """Send one delivery now; records the outcome and schedules a retry on failure."""
    import httpx
    from app.core.secret_box import decrypt
    db = _db()
    try:
        row = db.execute(text("""SELECT d.id, d.tenant_id, d.event, d.payload, d.attempts, e.url, e.secret_enc
            FROM webhook_deliveries d JOIN webhook_endpoints e ON e.id = d.endpoint_id WHERE d.id = :id"""),
            {"id": delivery_id}).fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        body = json.dumps(row.payload, separators=(",", ":"), default=str).encode()
        code, resp_body, ok = None, "", False
        try:
            check_url(row.url)
            r = httpx.post(row.url, content=body, timeout=_TIMEOUT, follow_redirects=False, headers={
                "Content-Type": "application/json", "User-Agent": "UniDocVerse-Webhooks/1.0",
                "X-UDV-Event": row.event, "X-UDV-Delivery": str(row.id),
                "X-UDV-Signature": sign(decrypt(row.secret_enc), body)})
            code, resp_body, ok = r.status_code, r.text[:1000], 200 <= r.status_code < 300
        except Exception as e:
            resp_body = f"{type(e).__name__}: {e}"[:1000]
        attempts = row.attempts + 1
        if ok:
            db.execute(text("""UPDATE webhook_deliveries SET status='delivered', attempts=:a, response_code=:c,
                response_body=:b, delivered_at=now() WHERE id=:id"""), {"a": attempts, "c": code, "b": resp_body, "id": row.id})
        elif attempts > len(RETRY_DELAYS):
            db.execute(text("""UPDATE webhook_deliveries SET status='failed', attempts=:a, response_code=:c,
                response_body=:b WHERE id=:id"""), {"a": attempts, "c": code, "b": resp_body, "id": row.id})
            from app.core.audit import record as audit
            audit("webhook.delivery_failed", detail=f"{row.event} → {row.url}",
                  extra={"delivery_id": row.id, "attempts": attempts, "status_code": code,
                         "response": resp_body[:300]}, tenant_id=row.tenant_id)
        else:
            db.execute(text("""UPDATE webhook_deliveries SET attempts=:a, response_code=:c, response_body=:b,
                next_attempt_at = now() + make_interval(secs => :delay) WHERE id=:id"""),
                {"a": attempts, "c": code, "b": resp_body, "delay": RETRY_DELAYS[attempts - 1], "id": row.id})
        db.commit()
        return {"ok": ok, "status_code": code, "response": resp_body[:200], "attempts": attempts}
    finally:
        db.close()


# ── Background sender ────────────────────────────────────────────────────────

_wake = threading.Event()
_started = False


def _loop() -> None:
    while True:
        try:
            db = _db()
            try:
                ensure_tables(db)
                due: List[int] = [r.id for r in db.execute(text(
                    "SELECT id FROM webhook_deliveries WHERE status='pending' AND next_attempt_at <= now() "
                    "ORDER BY next_attempt_at LIMIT 50"))]
            finally:
                db.close()
            for did in due:
                deliver(did)
        except Exception as e:
            logger.warning(f"Webhook sender: {e}")
        _wake.wait(timeout=15)
        _wake.clear()


def start_sender() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, name="udv-webhooks", daemon=True).start()
        logger.info("✅ Webhook sender started")
