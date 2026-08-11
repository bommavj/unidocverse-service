"""
License Management API - Updated with Trial Support

Flow:
  1. First launch  → Electron calls POST /api/license/activate-trial (auto)
  2. Every startup → Electron calls GET  /api/license/status
  3. Trial expired → user requests license on website, uploads .udv.json
  4. Upload        → POST /api/license/upload (existing flow, unchanged)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.activation import (
    get_device_fingerprint,
    read_activation_file,
    write_activation_file,
    create_trial_activation,
    get_trial_status,
    call_cloudflare_activation,
)
from app.core.database import get_db
from app.license.license_verify import verify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/license", tags=["license"])


# ─────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────

class LicenseUpload(BaseModel):
    """Matches actual .udv.json format"""
    issued_to: str
    email: EmailStr
    plan: str
    issued_at: str
    expiry: Optional[str] = None
    version: int
    app: str
    signature: str


class LicenseRequest(BaseModel):
    name: str
    email: EmailStr
    organization: str
    use_case: str


# ─────────────────────────────────────────────────────────────
# NEW: POST /api/license/activate-trial
# Called automatically from Electron on first launch
# ─────────────────────────────────────────────────────────────

@router.post("/activate-trial")
async def activate_trial():
    """
    Auto-called on first launch by Electron main.js.
    Creates a 30-day local trial — no user input required.
    Safe to call on every launch; skips if already activated.
    """
    try:
        existing = read_activation_file()

        if existing:
            license_type = existing.get("license_type", "full")

            # Full license already active — don't touch it
            if license_type == "full":
                return {
                    "success": True,
                    "already_activated": True,
                    "license_type": "full",
                    "message": "Full license already active"
                }

            # Trial already exists — return its current status
            if license_type == "trial":
                status = get_trial_status(existing)
                return {"success": True, "already_activated": True, **status}

        # No activation file → create trial
        trial = create_trial_activation()
        status = get_trial_status(trial)
        logger.info(f"✅ Trial created — device: {trial['device_id'][:16]}... expiry: {trial['expiry_at']}")
        return {"success": True, "already_activated": False, **status}

    except Exception as e:
        logger.error(f"Trial activation error: {e}")
        raise HTTPException(status_code=500, detail=f"Trial activation failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# UPDATED: GET /api/license/status
# Returns trial or full license status with days_remaining
# ─────────────────────────────────────────────────────────────

@router.get("/status")
async def activation_status():
    """
    Called by Electron on every startup.

    Returns one of:
      { license_type: "none",  is_valid: false }                   → first launch, call activate-trial
      { license_type: "trial", is_valid: true,  days_remaining: N } → trial active
      { license_type: "trial", is_valid: false, days_remaining: 0 } → trial expired, show dialog
      { license_type: "full",  is_valid: true  }                   → full license active
    """
    try:
        activation = read_activation_file()

        # No activation file at all (should be rare — Electron calls activate-trial on first launch)
        if not activation:
            return {
                "license_type": "none",
                "is_valid": False,
                "is_active": False,
                "message": "No activation found."
            }

        license_type = activation.get("license_type", "full")

        # ── Trial ────────────────────────────────────────────
        if license_type == "trial":
            return get_trial_status(activation)

        # ── Full license ─────────────────────────────────────
        device_fp = get_device_fingerprint()

        if activation.get("device_id") != device_fp:
            return {
                "license_type": "full",
                "is_valid": False,
                "is_active": False,
                "message": "Activation mismatch: wrong device."
            }

        expiry = datetime.fromisoformat(activation["expiry_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        if now > expiry:
            return {
                "license_type": "full",
                "is_valid": False,
                "is_active": False,
                "expiry_at": activation["expiry_at"],
                "message": "License expired. Please upload a new license."
            }

        days_remaining = max(0, (expiry - now).days)
        return {
            "license_type": "full",
            "is_valid": True,
            "is_active": True,
            "expiry_at": activation["expiry_at"],
            "days_remaining": days_remaining,
            "message": "License active"
        }

    except Exception as e:
        logger.error(f"Status check error: {e}")
        return {
            "license_type": "unknown",
            "is_valid": False,
            "is_active": False,
            "message": str(e)
        }


# ─────────────────────────────────────────────────────────────
# UNCHANGED: POST /api/license/upload
# Uploads full .udv.json after trial expires
# ─────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_license(license_data: LicenseUpload, db: Session = Depends(get_db)):
    try:
        # 1. Verify RSA signature
        if not verify_signature(license_data.dict()):
            raise HTTPException(status_code=400, detail="Invalid license signature")

        # 2. Parse and check expiry
        expiry_dt = None
        if license_data.expiry:
            expiry_dt = datetime.fromisoformat(license_data.expiry.replace("Z", "+00:00"))
            if datetime.utcnow() > expiry_dt.replace(tzinfo=None):
                raise HTTPException(status_code=400, detail="License has expired")

        # 3. Device fingerprint + license key
        device_fp = get_device_fingerprint()
        license_key = license_data.signature[:32]

        # 4. Check existing DB record
        existing = db.execute(text("""
            SELECT id, expiry, is_active, version, device_id
            FROM licenses
            WHERE license_key = :license_key
            ORDER BY version DESC LIMIT 1
        """), {"license_key": license_key}).fetchone()

        now = datetime.utcnow()
        if existing:
            if existing.is_active and existing.expiry and existing.expiry > now:
                raise HTTPException(status_code=403, detail="License already activated and not expired")
            new_version = existing.version + 1
        else:
            new_version = 1

        # 5. Call Cloudflare activation
        result = call_cloudflare_activation(
            license_key=license_key,
            device_fp=device_fp,
            expiry=license_data.expiry,
            version=new_version
        )
        if not result.get("allowed"):
            raise HTTPException(status_code=403, detail=result.get("error", "Activation denied"))

        # 6. Write .activation file — overwrites trial
        write_activation_file({
            "license_type": "full",
            "license_key": license_key,
            "device_id": device_fp,
            "expiry_at": result["expiry_at"],
            "version": new_version
        })

        # 7. Update DB
        db.execute(text("UPDATE licenses SET is_active = FALSE WHERE license_key = :k"),
                   {"k": license_key})

        features = {"plan": license_data.plan, "version": license_data.version, "app": license_data.app}

        db.execute(text("""
            INSERT INTO licenses (
                organization, email, license_key, signature,
                expiry, features, is_active, activated_at, version, device_id
            ) VALUES (
                :organization, :email, :license_key, :signature,
                :expiry, :features, TRUE, NOW(), :version, :device_id
            )
        """), {
            "organization": license_data.issued_to,
            "email": license_data.email,
            "license_key": license_key,
            "signature": license_data.signature,
            "expiry": expiry_dt,
            "features": json.dumps(features),
            "version": new_version,
            "device_id": device_fp
        })
        db.commit()

        logger.info(f"✅ Full license activated — {license_data.issued_to} / {license_data.email}")
        return {
            "success": True,
            "message": "License activated successfully",
            "license_type": "full",
            "expiry": result["expiry_at"],
            "version": new_version,
            "features": features
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"License upload error: {e}")
        raise HTTPException(status_code=500, detail=f"License activation failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# NEW: POST /api/license/request
# Stores license request for manual approval
# ─────────────────────────────────────────────────────────────

@router.post("/request")
async def request_license(request: LicenseRequest, db: Session = Depends(get_db)):
    """
    User fills the form on the website after trial expires.
    You manually approve and email the .udv.json.
    """
    try:
        db.execute(text("""
            INSERT INTO license_requests (name, email, organization, use_case, requested_at)
            VALUES (:name, :email, :organization, :use_case, NOW())
        """), {
            "name": request.name,
            "email": request.email,
            "organization": request.organization,
            "use_case": request.use_case
        })
        db.commit()
        logger.info(f"📬 License request — {request.name} / {request.email}")
        return {
            "success": True,
            "message": "License request submitted. You will receive your license via email within 24 hours."
        }
    except Exception as e:
        db.rollback()
        logger.error(f"License request error: {e}")
        raise HTTPException(status_code=500, detail="License request failed")