# app/api/notifications_router.py
"""
Notifications endpoint.
GET  /api/notifications/unread  — badge count for bell icon
GET  /api/notifications         — full list for dropdown
POST /api/notifications/mark-read — mark all or specific as read
POST /api/notifications/run-check — manual trigger (for testing)
"""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/unread")
def get_unread_count(db: Session = Depends(get_db)):
    """Badge count for the bell icon. Polled every 60s by frontend."""
    try:
        row = db.execute(text("""
            SELECT COUNT(*) as count FROM notifications WHERE is_read = FALSE
        """)).fetchone()
        count = row.count if row else 0
        return {"unread": count}
    except Exception as e:
        logger.error(f"❌ Unread count failed: {e}")
        return {"unread": 0}


@router.get("")
def get_notifications(db: Session = Depends(get_db)):
    """Full notification list for dropdown/page."""
    try:
        rows = db.execute(text("""
            SELECT id, doc_id, doc_type, filename, severity,
                   title, message, expiry_date, days_until,
                   is_read, created_at, updated_at
            FROM notifications
            ORDER BY
                CASE severity
                    WHEN 'expired'  THEN 0
                    WHEN 'critical' THEN 1
                    WHEN 'urgent'   THEN 2
                    WHEN 'warning'  THEN 3
                    ELSE 4
                END,
                days_until ASC
            LIMIT 50
        """)).fetchall()

        notifications = []
        for row in rows:
            notifications.append({
                "id":          row.id,
                "doc_id":      row.doc_id,
                "doc_type":    row.doc_type,
                "filename":    row.filename,
                "severity":    row.severity,
                "title":       row.title,
                "message":     row.message,
                "expiry_date": row.expiry_date,
                "days_until":  row.days_until,
                "is_read":     row.is_read,
                "created_at":  row.created_at.isoformat() if row.created_at else None,
            })

        unread = sum(1 for n in notifications if not n["is_read"])
        return {"notifications": notifications, "unread": unread, "total": len(notifications)}

    except Exception as e:
        logger.error(f"❌ Get notifications failed: {e}")
        return {"notifications": [], "unread": 0, "total": 0}


@router.post("/mark-read")
def mark_read(request: dict, db: Session = Depends(get_db)):
    """Mark specific notification or all as read."""
    notification_id = request.get("id")
    try:
        if notification_id:
            db.execute(text("""
                UPDATE notifications SET is_read = TRUE WHERE id = :id
            """), {"id": notification_id})
        else:
            db.execute(text("UPDATE notifications SET is_read = TRUE"))
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}


@router.post("/run-check")
async def trigger_check():
    """Manual trigger for testing — runs the same job as the 6am scheduler."""
    from app.services.scheduler import run_expiry_check
    await run_expiry_check()
    return {"success": True, "message": "Expiry check completed"}