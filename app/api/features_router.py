"""
Module switches (Settings → Features), stored per organisation.

Each tenant has its own row in tenant_features; the sidebar, top-bar search and page tabs are built
from these flags, so an organisation only sees the modules it uses. Tenants without a row inherit the
legacy installation-wide values from `settings` once, so existing installs keep what they had.
"""
import json
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import get_current_user, require_admin
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/features", tags=["features"])

FLAGS = ("agency_mode", "immigration_mode", "legal_mode", "medical_mode", "finance_mode", "real_estate_mode",
         "hr_mode", "supply_chain_mode", "compliance_mode", "operations_mode")
LEGACY = [f for f in FLAGS if f != "immigration_mode"]      # columns that exist on the old `settings` row


class FeatureFlagUpdate(BaseModel):
    agency_mode: Optional[bool] = None
    immigration_mode: Optional[bool] = None
    legal_mode: Optional[bool] = None
    medical_mode: Optional[bool] = None
    finance_mode: Optional[bool] = None
    real_estate_mode: Optional[bool] = None
    hr_mode: Optional[bool] = None
    supply_chain_mode: Optional[bool] = None
    compliance_mode: Optional[bool] = None
    operations_mode: Optional[bool] = None


def _ensure(db: Session) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS tenant_features (
        tenant_id TEXT PRIMARY KEY, flags JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT)"""))


def _legacy(db: Session) -> Dict[str, bool]:
    try:
        row = db.execute(text(f"SELECT {', '.join(LEGACY)} FROM settings LIMIT 1")).fetchone()
    except Exception:
        db.rollback()
        row = None
    flags = {f: bool(getattr(row, f, False)) if row else False for f in LEGACY}
    flags["immigration_mode"] = flags["hr_mode"]           # immigration used to ride on the HR switch
    return flags


def flags_for(db: Session, tenant_id: str) -> Dict[str, bool]:
    _ensure(db)
    row = db.execute(text("SELECT flags FROM tenant_features WHERE tenant_id = :t"), {"t": tenant_id}).fetchone()
    base = _legacy(db) if not row else {}
    stored = (row.flags if row else {}) or {}
    if isinstance(stored, str):
        stored = json.loads(stored)
    out = {f: bool(stored.get(f, base.get(f, False))) for f in FLAGS}
    if row and "immigration_mode" not in stored:
        out["immigration_mode"] = out["hr_mode"]
    return out


def _sync_scheduled_agents(db: Session) -> None:
    """Scheduled agents are installation-wide: keep each one on while any organisation still uses its module."""
    rows = db.execute(text("SELECT flags FROM tenant_features")).fetchall()
    for flag in FLAGS:
        on = any(bool((r.flags or {}).get(flag)) for r in rows)
        db.execute(text("UPDATE scheduled_agents SET enabled = :e, updated_at = NOW() WHERE flag_name = :f"), {"e": on, "f": flag})


@router.get("")
def get_features(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """The signed-in organisation's module switches. Called on login; the dashboard caches them."""
    tenant = str(user.get("tenant_id") or get_current_tenant_id())
    flags = flags_for(db, tenant)
    db.commit()
    return {"flags": flags}


@router.patch("")
def update_features(payload: FeatureFlagUpdate, db: Session = Depends(get_db), admin: dict = Depends(require_admin)):
    """Admins only. Changes apply to the admin's own organisation; fields left out keep their value."""
    tenant = str(admin.get("tenant_id") or get_current_tenant_id())
    flags = flags_for(db, tenant)
    flags.update({k: v for k, v in payload.dict().items() if v is not None})
    try:
        db.execute(text("""
            INSERT INTO tenant_features (tenant_id, flags, updated_at, updated_by)
            VALUES (:t, CAST(:f AS jsonb), now(), :u)
            ON CONFLICT (tenant_id) DO UPDATE SET flags = EXCLUDED.flags, updated_at = now(), updated_by = EXCLUDED.updated_by
        """), {"t": tenant, "f": json.dumps(flags), "u": admin.get("username")})
        _sync_scheduled_agents(db)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Feature flags update failed: {e}")
        raise HTTPException(status_code=500, detail="Could not save feature settings")
    logger.info(f"Feature flags for tenant {tenant} updated by {admin.get('username')}: {flags}")
    return {"flags": flags, "message": "Feature settings saved"}
