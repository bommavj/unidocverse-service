"""
"Share your experience" (Help). The app never sends feedback by itself: the page shows the user
exactly what will be sent and posts it to unidocverse.com only when they press Send. This router
only supplies optional usage counts (no names, text or documents) and records the send locally.
"""
import os
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth_router import get_current_user
from app.api.pilot_router import measure
from app.core.audit import record
from app.core.database import get_db

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
APP_VERSION = os.getenv("UDV_VERSION", "0.1.7")


@router.get("/usage")
def usage(_: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Counts for the last 90 days — only included in feedback if the user ticks the box."""
    m = measure(db, date.today() - timedelta(days=90), date.today())
    keep = ("documents_processed", "pages_read", "questions_asked", "answers_citation_rate", "active_users")
    return {"app_version": APP_VERSION, "edition": os.getenv("UDV_EDITION", "desktop"),
            "usage": {k: m.get(k) for k in keep}}


class Sent(BaseModel):
    destination: str
    rating: int
    included_usage: bool
    quote_permission: str


@router.post("/sent")
def sent(body: Sent, user=Depends(get_current_user)):
    record("feedback.sent", actor=user["username"],
           detail=f"Feedback sent to {body.destination} ({body.rating}/5)",
           extra=body.model_dump())
    return {"ok": True}
