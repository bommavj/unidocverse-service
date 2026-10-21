"""
Morning brief — one short, specific list per organisation of what needs a person today.

Built from each enabled domain agent's collect() output (the same data the scheduled
domain reports use), plus the organisation's open approvals and any agent run that
failed. Every item names the person / policy / matter it is about, links to where it
can be handled, and is marked new if it wasn't in yesterday's brief.

Delivery: stored in morning_briefs, an in-app notification, an email to the
organisation's admins (when SMTP is set up) and a brief.ready webhook.
"""
import hashlib
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# list key in collect() → (section heading, severity, link). Lists not named here are skipped:
# snapshots and counts are context, not things to do.
SECTIONS: Dict[str, tuple] = {
    "lapsed":                        ("Lapsed policies — call the client", "critical", "/renewals"),
    "lapsed_policies_need_outreach": ("Lapsed policies — call the client", "critical", "/renewals"),
    "stalled_renewals":              ("Renewals with no action yet", "warning", "/renewals"),
    "stalled_renewals_no_action_yet": ("Renewals with no action yet", "warning", "/renewals"),
    "renewals":                      ("Renewals due in 90 days", "info", "/renewals"),
    "renewal_pipeline_next_90_days": ("Renewals due in 90 days", "info", "/renewals"),
    "coi_expirations":               ("Certificates expiring", "warning", "/insurance"),
    "coi_expiring_60_days":          ("Certificates expiring", "warning", "/insurance"),
    "quotes":                        ("Open quotes", "info", "/insurance"),
    "open_quotes":                   ("Open quotes", "info", "/insurance"),
    "already_expired":               ("Immigration documents already expired", "critical", "/immigration"),
    "already_expired_urgent":        ("Immigration documents already expired", "critical", "/immigration"),
    "expiring_90d":                  ("Immigration documents expiring in 90 days", "warning", "/immigration"),
    "expiring_next_90_days":         ("Immigration documents expiring in 90 days", "warning", "/immigration"),
    "immigration_expiring_90_days":  ("Immigration documents expiring in 90 days", "warning", "/immigration"),
    "deadline_alerts_14_days":       ("Legal deadlines in the next 14 days", "critical", "/legal"),
    "stalled_matters_30_days":       ("Matters with no activity for 30 days", "warning", "/legal"),
    "contracts_expiring_90_days":    ("Contracts expiring in 90 days", "warning", "/legal"),
    "high_risk_contracts":           ("High-risk contracts", "warning", "/legal"),
    "top_anomalies":                 ("Financial anomalies", "warning", "/finance"),
    "onboarding_tasks_overdue":      ("Overdue onboarding tasks", "warning", "/hr"),
    "incomplete_charts":             ("Incomplete patient charts", "warning", "/medical"),
    "stalled_closings":              ("Closings that have stalled", "warning", "/real-estate"),
    "customs_holds":                 ("Shipments held at customs", "critical", "/supply-chain"),
    "unreconciled_shipments":        ("Shipments not reconciled", "warning", "/supply-chain"),
    "high_risk_documents":           ("High-risk documents", "warning", "/compliance"),
    "overdue_approvals":             None,      # covered by the approvals section
    "pending_approvals":             None,
}
_SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}
_PER_SECTION = 15

_WHO = ("client", "person", "employee", "patient", "candidate", "buyer", "matter", "contract",
        "supplier", "address", "bol", "title", "task")
_WHAT = ("lob", "label", "visa_class", "policy_number", "cert_number", "quote_number", "carrier",
         "counterparty", "type", "doc_type", "stage", "missing", "status", "risk", "ref")
_WHEN = (("expired_on", "expired {}"), ("expires_on", "expires {}"), ("deadline", "due {}"),
         ("due_date", "due {}"), ("detected_on", "found {}"), ("opened_on", "opened {}"))
_MONEY = ("annual_premium", "amount", "price", "contract_value")


def _blank(v) -> bool:
    return v in (None, "", "—", [], {})


def _sentence(row: dict) -> Dict[str, Any]:
    who = next((str(row[k]) for k in _WHO if not _blank(row.get(k))), "Item")
    what = [str(row[k]) for k in _WHAT if not _blank(row.get(k)) and str(row[k]) != who][:3]
    when = next((tpl.format(row[k]) for k, tpl in _WHEN if not _blank(row.get(k))), None)
    days = next((f"{row[k]} {lbl}" for k, lbl in (("days_left", "days left"), ("days_lapsed", "days lapsed"),
                 ("days_expired", "days ago"), ("days_overdue", "days overdue"), ("days_stale", "days idle"),
                 ("days_open", "days open"), ("age_days", "days old")) if not _blank(row.get(k))), None)
    money = next((row[k] for k in _MONEY if isinstance(row.get(k), (int, float)) and row[k]), None)
    if isinstance(money, (int, float)):
        money = f"${money:,.2f}" + ("/yr" if "annual_premium" in row else "")
    detail = " · ".join(x for x in [", ".join(what), when, days and f"({days})", money] if x)
    return {"who": who, "detail": detail, "contact": row.get("contact") or row.get("email")}


def _key(section: str, row: dict) -> str:
    """Stable across days: identity fields only, never day counts."""
    ident = {k: row.get(k) for k in _WHO + ("policy_number", "label", "cert_number", "quote_number", "ref", "client_number")
             if not _blank(row.get(k))}
    return hashlib.sha1(f"{section}|{json.dumps(ident, sort_keys=True, default=str)}".encode()).hexdigest()[:16]


def _domain_items(db, tenant_id: str, domains: List[str]) -> tuple:
    from app.core.tenant import set_current_tenant_id
    from app.services.domain_agents.registry import get_agent_class
    set_current_tenant_id(tenant_id)
    items, problems = [], []
    for d in domains:
        cls = get_agent_class(d)
        if not cls:
            continue
        try:
            data = cls(db).collect()
        except Exception as e:
            db.rollback()
            problems.append({"domain": d, "text": f"The {d.replace('_', ' ')} check couldn't run this morning ({str(e)[:120]}). "
                                                  "Items from it may be missing below."})
            continue
        seen_sections = set()
        for key, rows in (data or {}).items():
            spec = SECTIONS.get(key)
            if not spec or not isinstance(rows, list):
                continue
            heading, sev, link = spec
            if heading in seen_sections:          # aliases of the same list
                continue
            seen_sections.add(heading)
            for r in rows[:_PER_SECTION]:
                if isinstance(r, dict):
                    items.append({"key": _key(heading, r), "domain": d, "section": heading, "severity": sev,
                                  "link": link, **_sentence(r)})
            if len(rows) > _PER_SECTION:
                items.append({"key": _key(heading, {"title": "more"}), "domain": d, "section": heading, "severity": sev,
                              "link": link, "who": f"+{len(rows) - _PER_SECTION} more", "detail": "", "more": True})
    return items, problems


def _approvals(db, tenant_id: str) -> List[dict]:
    try:
        rows = db.execute(text("""SELECT id, title, priority, created_at, requested_by FROM approval_requests
            WHERE tenant_id = :t AND status = 'pending' AND source <> 'agent' ORDER BY created_at LIMIT 20"""), {"t": tenant_id}).fetchall()
    except Exception:
        db.rollback()
        return []
    out = []
    for r in rows:
        age = (datetime.utcnow() - r.created_at).days if r.created_at else 0
        out.append({"key": f"appr-{r.id}", "domain": "approvals", "section": "Waiting for your approval",
                    "severity": "warning" if age >= 3 or r.priority in ("high", "urgent") else "info",
                    "link": f"/approvals?id={r.id}", "approval_id": r.id, "who": r.title,
                    "detail": " · ".join(x for x in [f"asked by {r.requested_by}" if r.requested_by else None,
                                                     f"waiting {age} days" if age else "asked today",
                                                     r.priority if r.priority in ("high", "urgent") else None] if x)})
    return out


def _failed_runs(db, tenant_id: str) -> List[dict]:
    try:
        rows = db.execute(text("""SELECT sector, task_name, error_message FROM agent_runs
            WHERE tenant_id::text = :t AND status = 'failed' AND started_at > now() - interval '1 day'
            ORDER BY started_at DESC LIMIT 5"""), {"t": tenant_id}).fetchall()
    except Exception:
        db.rollback()
        return []
    return [{"domain": r.sector, "text": f"{r.task_name or r.sector} didn't finish: "
             f"{(r.error_message or 'no reason recorded')[:160]}. The rest of the brief is unaffected."} for r in rows]


def ensure_table(db) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS morning_briefs (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, brief_date DATE NOT NULL,
        items JSONB NOT NULL DEFAULT '[]', problems JSONB NOT NULL DEFAULT '[]', counts JSONB NOT NULL DEFAULT '{}',
        delivered JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, brief_date))"""))
    db.commit()


def build(tenant_id: str, deliver: bool = True) -> Dict[str, Any]:
    """Build (or rebuild) today's brief for one organisation."""
    from app.core.database import SessionLocal
    from app.api.features_router import enabled_domains
    db = SessionLocal()
    try:
        ensure_table(db)
        domains = [d for d in enabled_domains(db, tenant_id) if d in SECTIONS or True]
        items, problems = _domain_items(db, tenant_id, domains)
        items = _approvals(db, tenant_id) + items
        problems += _failed_runs(db, tenant_id)

        prev = db.execute(text("""SELECT items FROM morning_briefs WHERE tenant_id = :t AND brief_date < CURRENT_DATE
            ORDER BY brief_date DESC LIMIT 1"""), {"t": tenant_id}).scalar()
        prev_keys = {i["key"] for i in (prev or [])} if prev is not None else None
        for i in items:
            i["new"] = prev_keys is not None and i["key"] not in prev_keys
        resolved = [] if prev_keys is None else [
            {"who": i["who"], "section": i["section"]} for i in prev
            if i["key"] not in {x["key"] for x in items} and not i.get("more")]

        items.sort(key=lambda i: (_SEV_ORDER.get(i["severity"], 3), not i.get("new"), i["section"]))
        seen, unique = set(), []                 # the same record listed by two checks: keep the most urgent
        for i in items:
            ident = (i["domain"], i["who"], i.get("detail", "").split(" · ")[0]) if not i.get("more") else (i["key"],)
            if ident not in seen:
                seen.add(ident)
                unique.append(i)
        items = unique
        counts = {"total": sum(1 for i in items if not i.get("more")),
                  "critical": sum(1 for i in items if i["severity"] == "critical" and not i.get("more")),
                  "new": sum(1 for i in items if i.get("new")), "resolved": len(resolved)}
        bid = str(uuid.uuid4())
        db.execute(text("""INSERT INTO morning_briefs (id, tenant_id, brief_date, items, problems, counts)
            VALUES (:id, :t, CURRENT_DATE, CAST(:i AS jsonb), CAST(:p AS jsonb), CAST(:c AS jsonb))
            ON CONFLICT (tenant_id, brief_date) DO UPDATE SET items = EXCLUDED.items, problems = EXCLUDED.problems,
              counts = EXCLUDED.counts, created_at = now()"""),
            {"id": bid, "t": tenant_id, "i": json.dumps(items, default=str),
             "p": json.dumps(problems), "c": json.dumps({**counts, "resolved_items": resolved[:20]})})
        db.commit()
        brief = get(db, tenant_id)
        if deliver:
            brief["delivered"] = _deliver(db, tenant_id, brief)
        return brief
    finally:
        db.close()


def get(db, tenant_id: str, day: Optional[date] = None) -> Optional[Dict[str, Any]]:
    ensure_table(db)
    r = db.execute(text("""SELECT id, brief_date, items, problems, counts, delivered, created_at FROM morning_briefs
        WHERE tenant_id = :t AND brief_date = coalesce(CAST(:d AS date), CURRENT_DATE)"""),
        {"t": tenant_id, "d": day}).mappings().fetchone()
    return dict(r) if r else None


# ── Delivery ────────────────────────────────────────────────────────────────

def _headline(c: dict) -> str:
    if not c.get("total"):
        return "Nothing needs you today."
    parts = [f"{c['total']} item{'s' if c['total'] != 1 else ''} need attention"]
    if c.get("critical"):
        parts.append(f"{c['critical']} urgent")
    if c.get("new"):
        parts.append(f"{c['new']} new since yesterday")
    return ", ".join(parts) + "."


def _html(brief: dict, app_url: str) -> str:
    from html import escape
    colors = {"critical": "#b42318", "warning": "#b54708", "info": "#475467"}
    out = [f"<h2 style='font-family:sans-serif'>Today's brief — {brief['brief_date']}</h2>",
           f"<p style='font-family:sans-serif'>{escape(_headline(brief['counts']))}</p>"]
    section = None
    for i in brief["items"]:
        if i["section"] != section:
            section = i["section"]
            out.append(f"<h3 style='font-family:sans-serif;color:{colors.get(i['severity'], '#475467')}'>{escape(section)}</h3>")
        new = " <b style='color:#1570ef'>NEW</b>" if i.get("new") else ""
        out.append(f"<div style='font-family:sans-serif;margin:4px 0'><a href='{app_url}{i['link']}'>{escape(i['who'])}</a>"
                   f" — {escape(i.get('detail') or '')}{new}</div>")
    for p in brief.get("problems") or []:
        out.append(f"<p style='font-family:sans-serif;color:#b42318'>⚠ {escape(p['text'])}</p>")
    return "\n".join(out)


def _deliver(db, tenant_id: str, brief: dict) -> Dict[str, Any]:
    done: Dict[str, Any] = {}
    c = brief["counts"]
    try:
        db.execute(text("""INSERT INTO notifications (id, doc_type, filename, severity, title, message, is_read, tenant_id,
            created_at, updated_at) VALUES (:id, 'morning_brief', '', :sev, :title, :msg, FALSE, CAST(:t AS uuid), now(), now())"""),
            {"id": str(uuid.uuid4()), "sev": "critical" if c.get("critical") else "info",
             "title": "Today's brief", "msg": _headline(c), "t": tenant_id})
        db.commit()
        done["notification"] = True
    except Exception as e:
        db.rollback()
        done["notification"] = f"failed: {e}"[:200]
    try:
        s = dict(db.execute(text("SELECT key, value FROM settings WHERE key IN ('smtp_host','smtp_port','smtp_user',"
                                 "'smtp_pass','smtp_from_name','app_url')")).fetchall())
        if s.get("smtp_host") and c.get("total"):
            from app.services.renewal_notification_service import RenewalEmailSender
            to = [r[0] for r in db.execute(text("""SELECT email FROM users WHERE role = 'admin' AND coalesce(email,'') <> ''
                AND (tenant_id IS NULL OR tenant_id::text = :t)"""), {"t": tenant_id})]
            sender = RenewalEmailSender(s["smtp_host"], int(s.get("smtp_port") or 587), s.get("smtp_user"),
                                        s.get("smtp_pass"), s.get("smtp_from_name") or "UniDocVerse")
            html = _html(brief, (s.get("app_url") or "http://localhost:7979").rstrip("/"))
            done["email"] = [a for a in to if sender.send(a, f"Today's brief: {_headline(c)}", html)]
    except Exception as e:
        done["email"] = f"failed: {e}"[:200]
    try:
        from app.services.webhooks import emit
        emit("brief.ready", {"date": str(brief["brief_date"]), "counts": c,
                             "items": [{k: i.get(k) for k in ("section", "severity", "who", "detail", "new")}
                                       for i in brief["items"]]}, tenant_id=tenant_id)
        done["webhook"] = True
    except Exception as e:
        done["webhook"] = f"failed: {e}"[:200]
    db.execute(text("UPDATE morning_briefs SET delivered = CAST(:d AS jsonb) WHERE id = :id"),
               {"d": json.dumps(done, default=str), "id": brief["id"]})
    db.commit()
    return done


def build_all() -> None:
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        tenants = [r[0] for r in db.execute(text("SELECT id::text FROM tenants WHERE is_active"))]
    finally:
        db.close()
    for t in tenants:
        try:
            b = build(t)
            logger.info(f"☀️ Morning brief {t[:8]}: {b['counts']}")
        except Exception as e:
            logger.error(f"❌ Morning brief failed for {t[:8]}: {e}")
