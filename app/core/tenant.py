"""
Tenant context management.

How it works:
  1. TenantMiddleware reads tenant_id from the JWT (or header in dev mode)
  2. Stores it in a Python contextvars.ContextVar — safe for async, no thread leakage
  3. get_db_with_tenant() yields a DB session with SET LOCAL app.current_tenant_id
     so PostgreSQL RLS policies fire automatically
  4. get_current_tenant_id() lets any layer read the current tenant without passing it around

Default tenant UUID (for local dev / migration):
  00000000-0000-0000-0000-000000000001
"""
import logging
from contextvars import ContextVar
from typing import Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Per-request context variable — safe across async boundaries
_current_tenant_id: ContextVar[str] = ContextVar(
    "current_tenant_id", default=DEFAULT_TENANT_ID
)


def get_current_tenant_id() -> str:
    """Return the tenant_id for the current request. Falls back to default tenant."""
    return _current_tenant_id.get()


def set_current_tenant_id(tenant_id: str) -> None:
    """Set the tenant_id for the current request context."""
    _current_tenant_id.set(tenant_id)


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Extracts tenant_id from the request and stores it in context.

    Resolution order:
      1. X-Tenant-ID header (dev/service-to-service)
      2. JWT claim 'tenant_id'  (production — decode from Authorization header)
      3. Default tenant         (local dev fallback)
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        tenant_id = DEFAULT_TENANT_ID

        # 1. Explicit header (dev / internal calls)
        header_tid = request.headers.get("X-Tenant-ID")
        if header_tid:
            tenant_id = header_tid

        # 2. JWT claim — decode without verifying signature here (auth middleware
        #    already verified it upstream). Just read the payload.
        else:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                try:
                    import base64, json as _json
                    token = auth.split(" ", 1)[1]
                    payload_b64 = token.split(".")[1]
                    # Pad base64
                    payload_b64 += "=" * (-len(payload_b64) % 4)
                    payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
                    jwt_tid = payload.get("tenant_id") or payload.get("tid")
                    if jwt_tid:
                        tenant_id = jwt_tid
                except Exception:
                    pass  # fall through to default

        set_current_tenant_id(tenant_id)
        response = await call_next(request)
        return response


def apply_tenant_to_db(db: Session, tenant_id: Optional[str] = None) -> None:
    """
    Set the PostgreSQL session variable so RLS policies fire.
    Call this after acquiring a DB session.
    """
    tid = tenant_id or get_current_tenant_id()
    try:
        db.execute(__import__("sqlalchemy").text(
            f"SET LOCAL app.current_tenant_id = '{tid}'"
        ))
    except Exception as e:
        logger.warning(f"[Tenant] Could not set app.current_tenant_id: {e}")


def get_tenant_filter(alias: str = "") -> str:
    """
    Return a SQL fragment for filtering by tenant_id.
    Usage: f"WHERE {get_tenant_filter('d')} AND ..."
    """
    prefix = f"{alias}." if alias else ""
    tid = get_current_tenant_id()
    return f"{prefix}tenant_id = '{tid}'"
