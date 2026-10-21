"""
One call for writing the audit trail from anywhere — request handlers, background
processing, the model router, connectors. Never raises: a failed audit write is
logged, and the activity itself continues.

Actor conventions: a username for people, "api-token" for API keys, "system" for
background work, "anonymous" when nobody is signed in (e.g. a failed sign-in).
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def record(action: str, actor: str = "system", doc_id: Optional[str] = None, detail: str = "",
           extra: Optional[Dict[str, Any]] = None, tenant_id: Optional[str] = None,
           ip: Optional[str] = None) -> None:
    try:
        from sqlalchemy import text
        from app.core.database import SessionLocal
        from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
        db = SessionLocal()
        try:
            db.execute(text("""INSERT INTO audit_log (id, doc_id, action, actor, detail, ip, extra, tenant_id)
                VALUES (:id, :doc_id, :action, :actor, :detail, :ip, CAST(:extra AS jsonb), :tenant_id)"""),
                {"id": str(uuid.uuid4()), "doc_id": str(doc_id) if doc_id else None, "action": action,
                 "actor": actor or "system", "detail": (detail or "")[:500], "ip": ip,
                 "extra": json.dumps(extra or {}, default=str),
                 "tenant_id": tenant_id or get_current_tenant_id() or DEFAULT_TENANT_ID})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"❌ Audit write failed for {action}: {e}")
