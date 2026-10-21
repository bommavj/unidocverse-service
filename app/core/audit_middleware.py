"""
Records every request a person makes through the API in audit_log — changes
and reads (opening a document, searching, asking the AI, exporting): who, what,
which document, the result, IP and client. Endpoints that need richer detail
(deletes, sign-ins, uploads, cloud calls, QuickBooks) also write their own events.

Not logged: background polling the dashboard does on its own (unread counts,
health, model status, licence checks) and static files. A person repeating the
same read within 5 minutes is logged once.

The row is written after the response is produced, in a worker thread, so it
doesn't hold up the request; a failed audit write is logged, never raised.
"""
import base64
import json
import logging
import uuid

import time
from typing import Dict

import anyio
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Self-referential, or written explicitly elsewhere (auth_router logs sign-ins)
_SKIP_ALWAYS = ("/api/audit", "/api/auth/login", "/api/auth/logout", "/api/auth/refresh", "/api/upload", "/mcp", "/static", "/assets")
# Reads the dashboard polls automatically — not user activity
_SKIP_READS = ("/health", "/api/notifications/unread", "/api/notifications", "/api/approvals/stats",
               "/api/models/get-model", "/api/models/list-models", "/api/license", "/api/features",
               "/api/auth/me", "/api/activation", "/api/stats", "/api/mcp/", "/api/agents/runs",
               "/api/watch-folders/folders", "/api/gmail/connections", "/api/insights/alerts",
               "/api/research/history", "/api/research/docs", "/api/llm-providers", "/api/webhooks",
               "/api/quickbooks/status", "/api/salesforce/status", "/docs", "/openapi.json", "/favicon")
_SEEN: Dict[tuple, float] = {}
_DEDUPE_SECONDS = 300
_DOC_PARAMS = ("doc_id", "document_id", "id")


def _claims(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else request.query_params.get("jwt", "")
    if token.count(".") != 2:
        return {}
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def _token_actor(request: Request) -> str:
    from app.core import api_tokens
    auth = request.headers.get("Authorization", "")
    return "api-token" if auth.startswith("Bearer ") and api_tokens.is_api_token(auth[7:].strip()) else "anonymous"


def _write(row: dict) -> None:
    try:
        from sqlalchemy import text
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("""INSERT INTO audit_log (id, doc_id, action, actor, detail, ip, extra, tenant_id)
                VALUES (:id, :doc_id, :action, :actor, :detail, :ip, CAST(:extra AS jsonb), :tenant_id)"""), row)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"❌ Audit write failed for {row.get('action')}: {e}")


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        is_change = request.method in _MUTATING
        if request.method == "OPTIONS" or path.startswith(_SKIP_ALWAYS) or not path.startswith("/api/"):
            return response
        if not is_change:
            if path.startswith(_SKIP_READS) or response.status_code == 304:
                return response
        try:
            from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
            claims = _claims(request)
            if not is_change:
                key = (claims.get("sub"), path, request.url.query.split("jwt=")[0])
                now = time.time()
                if now - _SEEN.get(key, 0) < _DEDUPE_SECONDS:
                    return response
                _SEEN[key] = now
                if len(_SEEN) > 20000:
                    _SEEN.clear()
            endpoint = request.scope.get("endpoint")
            params = request.scope.get("path_params") or {}
            doc_id = next((str(params[k]) for k in _DOC_PARAMS if k in params), None)
            action = f"api.{endpoint.__name__}" if endpoint else f"api.{request.method.lower()}"
            searched = {k: request.query_params[k][:500] for k in ("question", "q", "query", "search")
                        if request.query_params.get(k)}
            await anyio.to_thread.run_sync(_write, {
                "id": str(uuid.uuid4()), "doc_id": doc_id, "action": action,
                "actor": claims.get("username") or _token_actor(request),
                "detail": f"{request.method} {request.url.path}",
                "ip": request.client.host if request.client else None,
                "extra": json.dumps({"status": response.status_code, "method": request.method,
                                     "kind": "change" if is_change else "read", **searched,
                                     "path_params": {k: str(v) for k, v in params.items()},
                                     "user_agent": request.headers.get("user-agent", "")[:200],
                                     "outcome": "ok" if response.status_code < 400 else "failed"}),
                "tenant_id": claims.get("tenant_id") or get_current_tenant_id() or DEFAULT_TENANT_ID,
            })
        except Exception as e:
            logger.error(f"❌ Audit middleware: {e}")
        return response
