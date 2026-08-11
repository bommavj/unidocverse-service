"""
renewal_router.py
=================
Renewal Pipeline API

Endpoints:
  GET  /api/ams/renewal/pipeline          — All renewals grouped by month
  GET  /api/ams/renewal/upcoming          — Renewals in next N days
  GET  /api/ams/renewal/calendar          — 12-month calendar view
  POST /api/ams/renewal/activity          — Log renewal activity
  PUT  /api/ams/renewal/policy/{id}       — Update renewal status
  GET  /api/ams/renewal/stats             — Renewal statistics
"""

import logging
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ams/renewal", tags=["renewal"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ActivityCreate(BaseModel):
    policy_id: str
    client_id: str
    activity_type: str
    title: Optional[str] = None
    notes: Optional[str] = None
    due_date: Optional[str] = None
    completed_by: Optional[str] = None

class RenewalStatusUpdate(BaseModel):
    renewal_status: str
    status: Optional[str] = None
    renewal_notes: Optional[str] = None


# ── Pipeline ──────────────────────────────────────────────────────────────────

@router.get("/pipeline")
def get_renewal_pipeline(
    days_ahead: int = 365,
    db: Session = Depends(get_db)
):
    """Get all renewals in the next N days, grouped by renewal_status."""
    today = date.today()
    end_date = today + timedelta(days=days_ahead)

    rows = db.execute(text("""
        SELECT
            p.*,
            c.first_name, c.last_name, c.business_name, c.client_type,
            c.email, c.phone, c.client_number,
            p.expiry_date - CURRENT_DATE as days_until_expiry,
            COUNT(ra.id) as activity_count,
            MAX(ra.created_at) as last_activity_at
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        LEFT JOIN renewal_activities ra ON ra.policy_id = p.id
        WHERE p.expiry_date BETWEEN :today AND :end_date
          AND p.status NOT IN ('cancelled', 'lapsed', 'non_renewed')
        GROUP BY p.id, c.id
        ORDER BY p.expiry_date ASC
    """), {"today": today, "end_date": end_date}).fetchall()

    # Group by renewal_status
    pipeline = {
        "not_started": [],
        "contacted": [],
        "in_progress": [],
        "complete": [],
        "lost": [],
        "non_renewed": [],
    }

    for row in rows:
        r = dict(row._mapping)
        r["display_name"] = (
            r["business_name"] if r["client_type"] == "commercial"
            else f"{r['first_name']} {r['last_name']}"
        ).strip()
        r["urgency"] = _get_urgency(r["days_until_expiry"])
        status = r.get("renewal_status") or "not_started"
        if status in pipeline:
            pipeline[status].append(r)

    return {
        "pipeline": pipeline,
        "total": len(rows),
        "summary": {
            k: len(v) for k, v in pipeline.items()
        }
    }


# ── Upcoming ──────────────────────────────────────────────────────────────────

@router.get("/upcoming")
def get_upcoming_renewals(
    days: int = 30,
    db: Session = Depends(get_db)
):
    """Get renewals due in next N days with urgency flags."""
    today = date.today()
    end_date = today + timedelta(days=days)

    rows = db.execute(text("""
        SELECT
            p.id, p.policy_number, p.carrier_name, p.line_of_business,
            p.premium_amount, p.expiry_date, p.renewal_status, p.status,
            p.expiry_date - CURRENT_DATE as days_until_expiry,
            c.first_name, c.last_name, c.business_name, c.client_type,
            c.email, c.phone, c.client_number
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        WHERE p.expiry_date BETWEEN :today AND :end_date
          AND p.status NOT IN ('cancelled', 'lapsed', 'non_renewed')
        ORDER BY p.expiry_date ASC
    """), {"today": today, "end_date": end_date}).fetchall()

    result = []
    for row in rows:
        r = dict(row._mapping)
        r["display_name"] = (
            r["business_name"] if r["client_type"] == "commercial"
            else f"{r['first_name']} {r['last_name']}"
        ).strip()
        r["urgency"] = _get_urgency(r["days_until_expiry"])
        result.append(r)

    return {
        "renewals": result,
        "total": len(result),
        "critical": sum(1 for r in result if r["urgency"] == "critical"),
        "urgent": sum(1 for r in result if r["urgency"] == "urgent"),
    }


# ── 12-Month Calendar ─────────────────────────────────────────────────────────

@router.get("/calendar")
def get_renewal_calendar(db: Session = Depends(get_db)):
    """Get renewals grouped by month for the next 12 months."""
    today = date.today()
    end_date = today + timedelta(days=365)

    rows = db.execute(text("""
        SELECT
            DATE_TRUNC('month', p.expiry_date) as month,
            COUNT(*) as policy_count,
            SUM(p.premium_amount) as total_premium,
            COUNT(CASE WHEN p.renewal_status = 'not_started' THEN 1 END) as not_started,
            COUNT(CASE WHEN p.renewal_status IN ('contacted','in_progress') THEN 1 END) as in_progress,
            COUNT(CASE WHEN p.renewal_status = 'complete' THEN 1 END) as complete,
            COUNT(CASE WHEN p.expiry_date - CURRENT_DATE <= 30 THEN 1 END) as urgent
        FROM policies p
        WHERE p.expiry_date BETWEEN :today AND :end_date
          AND p.status NOT IN ('cancelled', 'lapsed', 'non_renewed')
        GROUP BY DATE_TRUNC('month', p.expiry_date)
        ORDER BY month ASC
    """), {"today": today, "end_date": end_date}).fetchall()

    calendar = []
    for row in rows:
        r = dict(row._mapping)
        r["month_label"] = row.month.strftime("%B %Y") if row.month else ""
        r["month_key"] = row.month.strftime("%Y-%m") if row.month else ""
        r["total_premium"] = float(r["total_premium"] or 0)
        calendar.append(r)

    return {
        "calendar": calendar,
        "total_policies": sum(r["policy_count"] for r in calendar),
        "total_premium": sum(r["total_premium"] for r in calendar),
    }


# ── Log Activity ──────────────────────────────────────────────────────────────

@router.post("/activity")
def log_activity(activity: ActivityCreate, db: Session = Depends(get_db)):
    """Log a renewal activity (call, email, note, etc.)."""
    import uuid
    activity_id = str(uuid.uuid4())

    db.execute(text("""
        INSERT INTO renewal_activities
            (id, policy_id, client_id, activity_type, title, notes, due_date, completed_by)
        VALUES
            (:id, :pol_id, :cli_id, :type, :title, :notes, :due_date, :completed_by)
    """), {
        "id": activity_id,
        "pol_id": activity.policy_id,
        "cli_id": activity.client_id,
        "type": activity.activity_type,
        "title": activity.title,
        "notes": activity.notes,
        "due_date": activity.due_date,
        "completed_by": activity.completed_by,
    })

    # Update policy last_contacted_at
    if activity.activity_type in ("call", "email", "contacted"):
        db.execute(text("""
            UPDATE policies
            SET last_contacted_at = NOW(),
                renewal_status = CASE
                    WHEN renewal_status = 'not_started' THEN 'contacted'
                    ELSE renewal_status
                END
            WHERE id = :id
        """), {"id": activity.policy_id})

    db.commit()
    return {"activity_id": activity_id, "message": "Activity logged"}


# ── Update Renewal Status ─────────────────────────────────────────────────────

@router.put("/policy/{policy_id}")
def update_renewal_status(
    policy_id: str,
    update: RenewalStatusUpdate,
    db: Session = Depends(get_db)
):
    updates = ["renewal_status = :renewal_status"]
    params = {"id": policy_id, "renewal_status": update.renewal_status}

    if update.status:
        updates.append("status = :status")
        params["status"] = update.status
    if update.renewal_notes:
        updates.append("renewal_notes = :notes")
        params["notes"] = update.renewal_notes

    db.execute(text(f"""
        UPDATE policies SET {', '.join(updates)} WHERE id = :id
    """), params)
    db.commit()
    return {"message": "Renewal status updated"}


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def get_renewal_stats(db: Session = Depends(get_db)):
    """Overall renewal statistics."""
    stats = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 30)  as due_30,
            COUNT(*) FILTER (WHERE expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 60)  as due_60,
            COUNT(*) FILTER (WHERE expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 90)  as due_90,
            COUNT(*) FILTER (WHERE renewal_status = 'not_started'
                              AND expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 90)    as not_started_90,
            COUNT(*) FILTER (WHERE renewal_status = 'complete'
                              AND expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 90)    as complete_90,
            SUM(premium_amount) FILTER (
                WHERE expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 90)                as premium_at_risk_90
        FROM policies
        WHERE status NOT IN ('cancelled', 'lapsed', 'non_renewed')
    """)).fetchone()

    return dict(stats._mapping) if stats else {}


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_urgency(days: int) -> str:
    if days is None:
        return "unknown"
    if days <= 14:
        return "critical"
    if days <= 30:
        return "urgent"
    if days <= 60:
        return "warning"
    return "normal"


# ── ADD to renewal_router.py ──────────────────────────────────────────────────

from pydantic import BaseModel
from typing import Optional

class NotifyConfig(BaseModel):
    smtp_host:  str = "smtp.gmail.com"
    smtp_port:  int = 465
    username:   str          # agent's Gmail
    password:   str          # app password
    from_name:  Optional[str] = "Your Insurance Agent"
    dry_run:    bool = False  # if True, log but don't send


@router.post("/notify/run")
async def run_renewal_notifications(
    config: NotifyConfig,
    db: Session = Depends(get_db)
):
    """
    Manually trigger renewal email reminders.
    Call this daily via cron or from the dashboard.
    """
    from app.services.renewal_notification_service import (
        RenewalNotificationService, RenewalEmailSender
    )

    sender = None
    if not config.dry_run:
        sender = RenewalEmailSender(
            smtp_host = config.smtp_host,
            smtp_port = config.smtp_port,
            username  = config.username,
            password  = config.password,
            from_name = config.from_name or "Your Insurance Agent",
        )

    svc     = RenewalNotificationService(db, sender)
    results = svc.run()
    return {
        "status":   "completed",
        "dry_run":  config.dry_run,
        "results":  results,
    }


@router.get("/notify/preview")
async def preview_renewal_notifications(db: Session = Depends(get_db)):
    """
    Preview which clients would receive emails today — no emails sent.
    Use this to verify before running live.
    """
    from datetime import timedelta
    from sqlalchemy import text

    today    = date.today()
    thresholds = [90, 30, 14, 7]
    preview  = []

    for days in thresholds:
        target = today + timedelta(days=days)
        rows = db.execute(text("""
            SELECT p.policy_number, p.carrier_name, p.expiry_date,
                   p.expiry_date - CURRENT_DATE as days_remaining,
                   c.first_name || ' ' || COALESCE(c.last_name,'') as client_name,
                   c.email
            FROM policies p
            JOIN clients c ON c.id = p.client_id
            WHERE p.expiry_date BETWEEN :start AND :end
              AND p.status IN ('active','renewal_due')
              AND c.status != 'deleted'
        """), {
            "start": target - timedelta(days=1),
            "end":   target + timedelta(days=1),
        }).fetchall()

        for r in rows:
            preview.append({
                "threshold_days": days,
                **dict(r._mapping),
                "has_email": bool(r.email),
            })

    return {"preview": preview, "total": len(preview)}

# ── Ad-hoc renewal reminder ───────────────────────────────────────────────────

class AdHocReminderRequest(BaseModel):
    policy_id:         str
    client_email:      str
    client_name:       str
    policy_number:     str
    carrier_name:      str
    expiry_date:       str
    days_until_expiry: int

@router.post("/send-reminder")
def send_adhoc_reminder(
    payload: AdHocReminderRequest,
    db: Session = Depends(get_db)
):
    """Send an ad-hoc renewal reminder email for a specific policy."""
    from sqlalchemy import text as _text
    from app.services.renewal_notification_service import (
        RenewalEmailSender, _build_renewal_email
    )

    # Load SMTP config from settings
    rows = db.execute(_text(
        "SELECT key, value FROM settings WHERE key IN "
        "('smtp_host','smtp_port','smtp_user','smtp_pass','smtp_from_name','agent_name')"
    )).fetchall()
    cfg = {r.key: r.value for r in rows}

    import os
    smtp_user = os.getenv("RENEWAL_SMTP_USER") or cfg.get("smtp_user")
    smtp_pass = os.getenv("RENEWAL_SMTP_PASS") or cfg.get("smtp_pass")

    if not smtp_user or not smtp_pass:
        raise HTTPException(400, "SMTP not configured — set up email in Agency Settings")

    sender = RenewalEmailSender(
        smtp_host = cfg.get("smtp_host", "smtp.gmail.com"),
        smtp_port = int(cfg.get("smtp_port") or 465),
        username  = smtp_user,
        password  = smtp_pass,
        from_name = cfg.get("smtp_from_name") or cfg.get("agent_name") or "Your Agent",
    )

    subject, html = _build_renewal_email(
        client_name   = payload.client_name,
        policy_number = payload.policy_number,
        carrier       = payload.carrier_name,
        expiry_date   = payload.expiry_date,
        days          = payload.days_until_expiry,
    )

    ok = sender.send(payload.client_email, subject, html)
    if not ok:
        raise HTTPException(500, "Failed to send email — check SMTP settings")

    # Log activity
    import uuid
    db.execute(_text("""
        INSERT INTO renewal_activities
            (id, policy_id, activity_type, title, created_by)
        VALUES (:id, :pid, 'email', :title, 'agent_adhoc')
    """), {
        "id":    str(uuid.uuid4()),
        "pid":   payload.policy_id,
        "title": f"Ad-hoc reminder sent to {payload.client_email}",
    })
    db.commit()

    return {"success": True, "sent_to": payload.client_email}
