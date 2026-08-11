import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/features", tags=["features"])


class FeatureFlagUpdate(BaseModel):
    agency_mode: bool
    legal_mode: bool
    medical_mode: bool
    finance_mode: bool
    real_estate_mode: bool
    hr_mode: bool
    supply_chain_mode: bool
    compliance_mode: bool
    operations_mode: bool


# Database columns are verified on startup in app.core.database.init_db()


@router.get("")
async def get_features(db: Session = Depends(get_db)):
    """
    Returns enabled feature flags for the current installation.
    Called on login — Angular caches this for the session.
    """
    try:
        result = db.execute(
            text("SELECT agency_mode, legal_mode, medical_mode, finance_mode, real_estate_mode, hr_mode, supply_chain_mode, compliance_mode, operations_mode FROM settings LIMIT 1")
        ).fetchone()

        if result:
            agency_mode, legal_mode, medical_mode, finance_mode, real_estate_mode, hr_mode, supply_chain_mode, compliance_mode, operations_mode = result
        else:
            agency_mode, legal_mode, medical_mode, finance_mode, real_estate_mode, hr_mode, supply_chain_mode, compliance_mode, operations_mode = False, False, False, False, False, False, False, False, False

        return {
            "flags": {
                "agency_mode": agency_mode,
                "legal_mode": legal_mode,
                "medical_mode": medical_mode,
                "finance_mode": finance_mode,
                "real_estate_mode": real_estate_mode,
                "hr_mode": hr_mode,
                "supply_chain_mode": supply_chain_mode,
                "compliance_mode": compliance_mode,
                "operations_mode": operations_mode,
            }
        }
    except Exception as e:
        logger.error(f"❌ Error fetching feature flags: {e}")
        return {
            "flags": {
                "agency_mode": False,
                "legal_mode": False,
                "medical_mode": False,
                "finance_mode": False,
                "real_estate_mode": False,
                "hr_mode": False,
                "supply_chain_mode": False,
                "compliance_mode": False,
                "operations_mode": False,
            }
        }


@router.patch("")
async def update_features(
        payload: FeatureFlagUpdate,
        db:      Session = Depends(get_db)
):
    """
    Admin toggles feature flags via Settings page.
    Only admins should call this endpoint.
    """
    try:
        db.execute(
            text("""
                UPDATE settings SET
                    agency_mode = :agency,
                    legal_mode = :legal,
                    medical_mode = :medical,
                    finance_mode = :finance,
                    real_estate_mode = :real_estate,
                    hr_mode = :hr,
                    supply_chain_mode = :supply_chain,
                    compliance_mode = :compliance,
                    operations_mode = :operations
            """),
            {
                "agency": payload.agency_mode,
                "legal": payload.legal_mode,
                "medical": payload.medical_mode,
                "finance": payload.finance_mode,
                "real_estate": payload.real_estate_mode,
                "hr": payload.hr_mode,
                "supply_chain": payload.supply_chain_mode,
                "compliance": payload.compliance_mode,
                "operations": payload.operations_mode,
            }
        )
        db.commit()

        # Sync scheduled_agents enabled state with each flag
        flag_map = {
            "agency_mode":       payload.agency_mode,
            "legal_mode":        payload.legal_mode,
            "medical_mode":      payload.medical_mode,
            "finance_mode":      payload.finance_mode,
            "real_estate_mode":  payload.real_estate_mode,
            "hr_mode":           payload.hr_mode,
            "supply_chain_mode": payload.supply_chain_mode,
            "compliance_mode":   payload.compliance_mode,
            "operations_mode":   payload.operations_mode,
        }
        for flag_name, enabled in flag_map.items():
            db.execute(
                text("UPDATE scheduled_agents SET enabled = :enabled, updated_at = NOW() WHERE flag_name = :flag"),
                {"enabled": enabled, "flag": flag_name}
            )
        db.commit()

        logger.info(f"✅ Feature flags updated: agency={payload.agency_mode}, legal={payload.legal_mode}, medical={payload.medical_mode}, finance={payload.finance_mode}, real_estate={payload.real_estate_mode}, hr={payload.hr_mode}, supply_chain={payload.supply_chain_mode}")

        return {
            "flags": {
                "agency_mode": payload.agency_mode,
                "legal_mode": payload.legal_mode,
                "medical_mode": payload.medical_mode,
                "finance_mode": payload.finance_mode,
                "real_estate_mode": payload.real_estate_mode,
                "hr_mode": payload.hr_mode,
                "supply_chain_mode": payload.supply_chain_mode,
                "compliance_mode": payload.compliance_mode,
                "operations_mode": payload.operations_mode,
            },
            "message": "Feature flags updated successfully"
        }
    except Exception as e:
        logger.error(f"❌ Error updating feature flags: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))