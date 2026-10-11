"""
API tokens — long-lived bearer credentials for programs.

Format: udv_<8-char prefix>_<secret>. Only sha256(token) is stored. A token acts as the user who
created it, inside that user's tenant; 'read' tokens may only make GET/HEAD/OPTIONS requests.
"""
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import text

PREFIX = "udv_"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_cache: Dict[str, tuple] = {}          # hash → (expires_at_monotonic, row-or-None)
_CACHE_SECONDS = 30


def is_api_token(value: Optional[str]) -> bool:
    return bool(value) and value.startswith(PREFIX)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(db, tenant_id: str, user_id: int, name: str, scope: str = "read", expires_days: Optional[int] = None) -> Dict:
    if scope not in ("read", "write"):
        raise ValueError("scope must be 'read' or 'write'")
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("Give the token a name")
    prefix = secrets.token_hex(4)
    raw = f"{PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    expires = datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None
    row = db.execute(text(
        "INSERT INTO api_tokens (tenant_id, user_id, name, prefix, token_hash, scope, expires_at) "
        "VALUES (:t, :u, :n, :p, :h, :s, :e) RETURNING id, created_at"),
        {"t": tenant_id, "u": user_id, "n": name, "p": prefix, "h": _hash(raw), "s": scope, "e": expires}).fetchone()
    db.commit()
    return {"id": str(row.id), "name": name, "prefix": f"{PREFIX}{prefix}", "scope": scope,
            "created_at": row.created_at.isoformat(), "expires_at": expires.isoformat() if expires else None,
            "token": raw}


def list_for(db, tenant_id: str, user_id: Optional[int] = None):
    q = ("SELECT t.id, t.name, t.prefix, t.scope, t.created_at, t.expires_at, t.last_used_at, t.revoked_at, u.username "
         "FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.tenant_id = :t")
    params = {"t": tenant_id}
    if user_id is not None:
        q += " AND t.user_id = :u"
        params["u"] = user_id
    rows = db.execute(text(q + " ORDER BY t.created_at DESC"), params).fetchall()
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        status = "revoked" if r.revoked_at else "expired" if (r.expires_at and r.expires_at < now) else "active"
        out.append({"id": str(r.id), "name": r.name, "prefix": f"{PREFIX}{r.prefix}", "scope": r.scope, "owner": r.username,
                    "status": status, "created_at": r.created_at.isoformat(),
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None})
    return out


def revoke(db, tenant_id: str, token_id: str, user_id: Optional[int] = None) -> bool:
    q = "UPDATE api_tokens SET revoked_at = now() WHERE id = :i AND tenant_id = :t AND revoked_at IS NULL"
    params = {"i": token_id, "t": tenant_id}
    if user_id is not None:
        q += " AND user_id = :u"
        params["u"] = user_id
    n = db.execute(text(q), params).rowcount
    db.commit()
    _cache.clear()
    return n > 0


def resolve(raw: str):
    """Return the active token row (id, tenant_id, user_id, scope) or None. Cached briefly; records last use."""
    if not is_api_token(raw):
        return None
    h = _hash(raw)
    hit = _cache.get(h)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT id, tenant_id, user_id, scope FROM api_tokens WHERE token_hash = :h AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > now())"), {"h": h}).fetchone()
        if row:
            db.execute(text("UPDATE api_tokens SET last_used_at = now() WHERE id = :i"), {"i": row.id})
            db.commit()
        _cache[h] = (time.monotonic() + _CACHE_SECONDS, row)
        return row
    finally:
        db.close()
