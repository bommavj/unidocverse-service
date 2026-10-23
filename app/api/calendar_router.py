"""
calendar_router.py
==================
Aggregates events from all document types, policies, activities
into a unified calendar feed.

GET /api/ams/calendar/events?month=2026-05
GET /api/ams/calendar/events?start=2026-05-01&end=2026-05-31
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ams/calendar", tags=["calendar"])


# ── Event color map by type ───────────────────────────────────────────────────
EVENT_COLORS = {
    "policy_expiry":       "#dc2626",   # red   — urgent
    "renewal_reminder":    "#d97706",   # amber — warning
    "policy_effective":    "#16a34a",   # green — good
    "commission":          "#7c3aed",   # purple
    "loss_run":            "#0891b2",   # cyan
    "coi_expiry":          "#dc2626",   # red
    "activity":            "#6366f1",   # indigo
    "document":            "#64748b",   # slate — generic doc date
    "tax_deadline":        "#dc2626",   # red
    "contract_expiry":     "#d97706",   # amber
    "invoice_due":         "#d97706",   # amber
    "mortgage":            "#0891b2",   # cyan
}


def _parse_date(val) -> Optional[date]:
    """Safely parse any date value."""
    if not val:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        s = str(val).strip()[:10]
        return date.fromisoformat(s)
    except Exception:
        return None


def _in_range(d: Optional[date], start: date, end: date) -> bool:
    return d is not None and start <= d <= end


@router.get("/events")
async def get_calendar_events(
    month:  Optional[str] = Query(None, description="YYYY-MM format"),
    start:  Optional[str] = Query(None, description="YYYY-MM-DD"),
    end:    Optional[str] = Query(None, description="YYYY-MM-DD"),
    db:     Session       = Depends(get_db)
):
    """
    Return all calendar events in a date range.
    Aggregates from: policies, documents, renewal_activities, commission_statements.
    """
    # Resolve date range
    if month:
        y, m   = int(month[:4]), int(month[5:7])
        rstart = date(y, m, 1)
        # Last day of month
        if m == 12:
            rend = date(y + 1, 1, 1) - timedelta(days=1)
        else:
            rend = date(y, m + 1, 1) - timedelta(days=1)
        # Include 90-day lookahead for renewal reminders
        rend_extended = rend + timedelta(days=90)
    elif start and end:
        rstart        = date.fromisoformat(start)
        rend          = date.fromisoformat(end)
        rend_extended = rend
    else:
        today         = date.today()
        rstart        = date(today.year, today.month, 1)
        rend          = today + timedelta(days=90)
        rend_extended = rend

    events = []

    # ── 1. Policy dates ───────────────────────────────────────────────────────
    policies = db.execute(text("""
        SELECT
            p.id, p.policy_number, p.carrier_name, p.line_of_business,
            p.expiry_date, p.effective_date, p.renewal_date,
            p.premium_amount, p.status, p.renewal_status,
            c.id as client_id,
            c.first_name || ' ' || COALESCE(c.last_name,'') as client_name,
            c.client_number
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        WHERE c.status != 'deleted'
          AND p.tenant_id::text = :tenant_id
          AND p.status NOT IN ('deleted','cancelled')
          AND (
            p.expiry_date    BETWEEN :start AND :end_ext
            OR p.effective_date BETWEEN :start AND :end
            OR p.renewal_date   BETWEEN :start AND :end_ext
          )
        ORDER BY p.expiry_date ASC
    """), {"start": rstart, "end": rend, "end_ext": rend_extended, "tenant_id": get_current_tenant_id()}).fetchall()

    for p in policies:
        r = dict(p._mapping)
        name = r["client_name"].strip() or r["client_number"]

        # Expiry event
        if _in_range(_parse_date(r["expiry_date"]), rstart, rend_extended):
            days_left = (r["expiry_date"] - date.today()).days if r["expiry_date"] else None
            events.append({
                "id":         f"expiry-{r['id']}",
                "date":       str(r["expiry_date"]),
                "type":       "policy_expiry",
                "title":      f"Policy Expires: {r['policy_number']}",
                "subtitle":   f"{name} · {r['carrier_name']} · ${float(r['premium_amount'] or 0):,.0f}/yr",
                "color":      EVENT_COLORS["policy_expiry"],
                "urgent":     days_left is not None and days_left <= 14,
                "client_id":  str(r["client_id"]),
                "client_name": name,
                "policy_id":  str(r["id"]),
                "days_away":  days_left,
                "meta": {
                    "policy_number":    r["policy_number"],
                    "carrier":          r["carrier_name"],
                    "line_of_business": r["line_of_business"],
                    "renewal_status":   r["renewal_status"],
                    "premium":          float(r["premium_amount"] or 0),
                }
            })

        # Renewal reminders — virtual events at 90/30/14/7 days before expiry
        if r["expiry_date"]:
            for days_before in [90, 30, 14, 7]:
                reminder_date = r["expiry_date"] - timedelta(days=days_before)
                if _in_range(reminder_date, rstart, rend_extended):
                    events.append({
                        "id":         f"reminder-{r['id']}-{days_before}",
                        "date":       str(reminder_date),
                        "type":       "renewal_reminder",
                        "title":      f"Renewal Reminder ({days_before}d): {r['policy_number']}",
                        "subtitle":   f"{name} · expires {r['expiry_date']}",
                        "color":      EVENT_COLORS["renewal_reminder"],
                        "urgent":     days_before <= 14,
                        "client_id":  str(r["client_id"]),
                        "client_name": name,
                        "policy_id":  str(r["id"]),
                        "days_away":  days_before,
                        "meta": {
                            "policy_number": r["policy_number"],
                            "carrier":       r["carrier_name"],
                        }
                    })

        # Effective date
        if _in_range(_parse_date(r["effective_date"]), rstart, rend):
            events.append({
                "id":         f"effective-{r['id']}",
                "date":       str(r["effective_date"]),
                "type":       "policy_effective",
                "title":      f"Policy Started: {r['policy_number']}",
                "subtitle":   f"{name} · {r['carrier_name']}",
                "color":      EVENT_COLORS["policy_effective"],
                "urgent":     False,
                "client_id":  str(r["client_id"]),
                "client_name": name,
                "policy_id":  str(r["id"]),
            })

    # ── 2. Document-extracted dates (any doc type) ────────────────────────────
    docs = db.execute(text("""
        SELECT
            d.id, d.filename, d.doc_type, d.doc_metadata,
            d.client_id,
            c.first_name || ' ' || COALESCE(c.last_name,'') as client_name,
            c.client_number
        FROM documents d
        LEFT JOIN clients c ON c.id = d.client_id
        WHERE d.status = 'completed'
          AND d.tenant_id::text = :tenant_id
          AND d.doc_metadata IS NOT NULL
          AND d.doc_metadata != '{}'
    """), {"tenant_id": get_current_tenant_id()}).fetchall()

    # Date fields to extract per doc type
    DATE_FIELDS = {
        "commission_statement": [
            ("statement_date",    "commission",      "Commission Statement"),
            ("payment_due_date",  "invoice_due",     "Payment Due"),
        ],
        "loss_run": [
            ("period_start",      "loss_run",        "Loss Run Period Start"),
            ("period_end",        "loss_run",        "Loss Run Period End"),
        ],
        "tax_form": [
            ("tax_period_end",    "tax_deadline",    "Tax Period End"),
            ("filing_deadline",   "tax_deadline",    "Tax Filing Deadline"),
            ("due_date",          "tax_deadline",    "Tax Due Date"),
        ],
        "invoice": [
            ("invoice_date",      "document",        "Invoice Date"),
            ("due_date",          "invoice_due",     "Invoice Due"),
        ],
        "contract": [
            ("start_date",        "document",        "Contract Start"),
            ("end_date",          "contract_expiry", "Contract Expires"),
            ("renewal_date",      "renewal_reminder","Contract Renewal"),
        ],
        "lease_agreement": [
            ("start_date",        "document",        "Lease Start"),
            ("end_date",          "contract_expiry", "Lease Expires"),
        ],
        "mortgage_document": [
            ("closing_date",      "mortgage",        "Mortgage Closing"),
            ("first_payment_date","mortgage",        "First Payment Due"),
        ],
        "medical_record": [
            ("appointment_date",  "document",        "Appointment"),
            ("follow_up_date",    "document",        "Follow-up"),
        ],
        "bank_statement": [
            ("statement_date",    "document",        "Bank Statement"),
        ],
        "declarations_page": [],  # handled via policies table above
    }

    for doc in docs:
        r        = dict(doc._mapping)
        meta     = r.get("doc_metadata") or {}
        fields   = (meta.get("analyzed_data") or {}).get("fields") or {}
        doc_type = (r.get("doc_type") or "").lower()
        name     = (r.get("client_name") or "").strip() or r.get("client_number") or "Unknown"

        date_map = DATE_FIELDS.get(doc_type, [])
        # Also check generic fields for any doc type
        generic_fields = [
            ("expiry_date",     "document",  "Expires"),
            ("due_date",        "invoice_due","Due"),
            ("end_date",        "contract_expiry","Ends"),
            ("deadline",        "document",  "Deadline"),
        ]
        all_fields = date_map + generic_fields

        for field_name, event_type, label in all_fields:
            val = fields.get(field_name)
            d   = _parse_date(val)
            if d and _in_range(d, rstart, rend_extended):
                events.append({
                    "id":           f"doc-{r['id']}-{field_name}",
                    "date":         str(d),
                    "type":         event_type,
                    "title":        f"{label}: {r['filename']}",
                    "subtitle":     name,
                    "color":        EVENT_COLORS.get(event_type, EVENT_COLORS["document"]),
                    "urgent":       event_type in ("policy_expiry","tax_deadline","invoice_due")
                                    and (d - date.today()).days <= 14,
                    "client_id":    str(r["client_id"]) if r.get("client_id") else None,
                    "client_name":  name,
                    "document_id":  str(r["id"]),
                    "doc_type":     doc_type,
                })

    # ── 3. Renewal activities (logged calls, emails, meetings) ────────────────
    activities = db.execute(text("""
        SELECT
            ra.id, ra.activity_type, ra.title, ra.created_at,
            ra.policy_id,
            p.policy_number,
            c.id as client_id,
            c.first_name || ' ' || COALESCE(c.last_name,'') as client_name
        FROM renewal_activities ra
        LEFT JOIN policies p ON p.id = ra.policy_id
        LEFT JOIN clients  c ON c.id = p.client_id
        WHERE ra.created_at::date BETWEEN :start AND :end
          AND p.tenant_id::text = :tenant_id
        ORDER BY ra.created_at DESC
    """), {"start": rstart, "end": rend, "tenant_id": get_current_tenant_id()}).fetchall()

    for a in activities:
        r    = dict(a._mapping)
        name = (r.get("client_name") or "").strip() or "Unknown"
        events.append({
            "id":           f"activity-{r['id']}",
            "date":         str(r["created_at"])[:10],
            "type":         "activity",
            "title":        r["title"] or r["activity_type"],
            "subtitle":     f"{name} · {r.get('policy_number','') or ''}",
            "color":        EVENT_COLORS["activity"],
            "urgent":       False,
            "client_id":    str(r["client_id"]) if r.get("client_id") else None,
            "client_name":  name,
            "policy_id":    str(r["policy_id"]) if r.get("policy_id") else None,
        })

    # ── 4. Planner tasks (To-dos with due dates) ──────────────────────────────
    try:
        tasks = db.execute(text("""
            SELECT id, title, priority, due_date, is_completed
            FROM planner_tasks
            WHERE due_date::date BETWEEN :start AND :end
        """), {"start": rstart, "end": rend}).fetchall()

        for t in tasks:
            r = dict(t._mapping)
            events.append({
                "id":           f"task-{r['id']}",
                "date":         str(r["due_date"])[:10],
                "type":         "activity",
                "title":        f"Task: {r['title']}",
                "subtitle":     f"Priority: {r['priority'].upper()} · Completed: {r['is_completed']}",
                "color":        "#6366f1" if not r['is_completed'] else "#94a3b8",
                "urgent":       r['priority'] == 'high' and not r['is_completed'],
                "task_id":      str(r["id"]),
                "is_completed": r["is_completed"],
            })
    except Exception as e:
        logger.warning(f"Could not retrieve planner tasks for calendar: {e}")

    # ── Sort and return ───────────────────────────────────────────────────────
    events.sort(key=lambda e: e["date"])

    # Group by date for frontend convenience
    by_date: dict = {}
    for ev in events:
        by_date.setdefault(ev["date"], []).append(ev)

    return {
        "events":   events,
        "by_date":  by_date,
        "total":    len(events),
        "range":    {"start": str(rstart), "end": str(rend)},
    }