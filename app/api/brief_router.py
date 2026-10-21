"""Morning brief — today's list of what needs a person, per organisation."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
from app.services import morning_brief

router = APIRouter(prefix="/api/brief", tags=["brief"])


def _t() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


@router.get("/today")
def today(db: Session = Depends(get_db)):
    return morning_brief.get(db, _t()) or morning_brief.build(_t(), deliver=False)


@router.post("/run")
def run_now(deliver: bool = False):
    return morning_brief.build(_t(), deliver=deliver)
