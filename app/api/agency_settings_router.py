# ── ADD to features_router.py (or create agency_settings_router.py) ──────────

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
from sqlalchemy.orm import Session
from app.core.database import get_db
from sqlalchemy import text

from app.services.renewal_notification_service import RenewalEmailSender

router = APIRouter(prefix="/api/agency/settings", tags=["agency-settings"])

# Keys stored in settings table
AGENCY_KEYS = [
    "agency_name",
    "agency_email",       # from address for outbound emails
    "agent_name",         # agent's full name shown in emails
    "agent_phone",
    "agent_license",
    "agency_address",
    "agency_website",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_pass",          # stored as-is (local machine — no cloud)
    "smtp_from_name",
    "renewal_notify_enabled",   # true/false
    "renewal_notify_days",      # "90,30,14,7"
    "calendar_sync_enabled",
    # Healthcare Settings
    "medical_notify_enabled",
    "medical_notify_days",
    "medical_pii_masking",
    # Finance Settings
    "finance_notify_enabled",
    "finance_notify_days",
    "finance_tolerance_pct",
    # Legal Settings
    "legal_notify_enabled",
    "legal_notify_days",
    "legal_default_law",
    # Real Estate Settings
    "real_estate_notify_enabled",
    "real_estate_notify_days",
    "real_estate_buffer",
    # HR Settings
    "hr_notify_enabled",
    "hr_notify_days",
    "hr_review_freq",
    # Supply Chain Settings
    "supply_chain_notify_enabled",
    "supply_chain_notify_days",
    "supply_chain_delay_threshold",
]

class SettingUpdate(BaseModel):
    key:   str
    value: str

class AgencySettingsUpdate(BaseModel):
    settings: Dict[str, str]


@router.get("")
async def get_agency_settings(db: Session = Depends(get_db)):
    """Get all agency settings as a flat dict."""
    rows = db.execute(text(
        "SELECT key, value FROM settings WHERE key = ANY(:keys)"
    ), {"keys": AGENCY_KEYS}).fetchall()

    result = {k: "" for k in AGENCY_KEYS}
    for row in rows:
        result[row.key] = row.value or ""

    # Mask SMTP password
    if result.get("smtp_pass"):
        result["smtp_pass_set"] = True
        result["smtp_pass"] = "••••••••••••••••"
    else:
        result["smtp_pass_set"] = False

    return result


@router.put("")
async def update_agency_settings(
    payload: AgencySettingsUpdate,
    db: Session = Depends(get_db)
):
    """Upsert multiple settings at once."""
    for key, value in payload.settings.items():
        if key not in AGENCY_KEYS:
            continue
        # Don't overwrite password if masked value sent
        if key == "smtp_pass" and value.startswith("•"):
            continue
        db.execute(text("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (:key, :value, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = :value, updated_at = NOW()
        """), {"key": key, "value": value})

    db.commit()
    return {"success": True, "updated": len(payload.settings)}


def safe_int(value, default=465):
    """Convert value to int safely. Returns default if empty or invalid."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default

@router.post("/test-email")
async def test_email(db: Session = Depends(get_db)):
    """Send a test email using current SMTP settings."""
    rows = db.execute(text(
        "SELECT key, value FROM settings WHERE key = ANY(:keys)"
    ), {"keys": ["smtp_host","smtp_port","smtp_user","smtp_pass",
                 "smtp_from_name","agency_email","agent_name"]}).fetchall()
    cfg = {r.key: r.value for r in rows}

    if not cfg.get("smtp_user") or not cfg.get("smtp_pass"):
        raise HTTPException(400, "SMTP not configured")

    import app.services.renewal_notification_service
    # Safe SMTP Sender Initialization
    sender = RenewalEmailSender(
        smtp_host=cfg.get("smtp_host", "smtp.gmail.com"),
        smtp_port=safe_int(cfg.get("smtp_port"), default=465),  # <-- Fixed
        username=cfg.get("smtp_user"),  # <-- Safer
        password=cfg.get("smtp_pass"),  # <-- Safer
        from_name=cfg.get("smtp_from_name") or cfg.get("agent_name", "UniDocVerse"),
    )
    success = sender.send(
        to_email  = cfg["smtp_user"],
        subject   = "✅ UniDocVerse — Email test successful",
        html_body = "<p>Your email settings are working correctly.</p>"
    )
    if success:
        return {"success": True, "message": f"Test email sent to {cfg['smtp_user']}"}
    raise HTTPException(500, "Failed to send test email")