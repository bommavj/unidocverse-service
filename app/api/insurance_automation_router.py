"""
Insurance Workflow Automation Engine.
Rules: When [trigger] + [condition] → [action]
Runs on a schedule and on key events (quote created, stage changed, etc.)
"""
import uuid, json, logging
from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import HTTPException, APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant_scope import scope_sql, tenant_params
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/insurance/automations", tags=["insurance-automation"])

# ── Trigger types ─────────────────────────────────────────────────────────────
TRIGGERS = {
    "quote_created":           "Quote Created",
    "quote_stage_changed":     "Quote Stage Changed",
    "carrier_no_response":     "Carrier No Response (N days)",
    "quote_expiring":          "Quote Expiring in N Days",
    "policy_expiring":         "Policy Expiring in N Days",
    "carrier_quote_received":  "Carrier Quote Received",
    "quote_below_minimum":     "Quote Below Jurisdiction Minimum",
    "quote_comparing":         "Quote Enters Comparing Stage",
    "quote_bound":             "Quote Bound (Policy Created)",
    "renewal_due":             "Renewal Due in N Days",
}

# ── Action types ──────────────────────────────────────────────────────────────
ACTIONS = {
    "notify_agent":          "Notify Assigned Agent",
    "send_email_client":     "Send Email to Client",
    "send_email_carrier":    "Send Email to Carrier Underwriter",
    "move_stage":            "Move Quote to Stage",
    "flag_compliance":       "Flag for Compliance Review",
    "create_renewal_quote":  "Auto-Create Renewal Quote",
    "add_carrier":           "Add Preferred Carrier to Quote",
    "create_task":           "Create Follow-up Task",
    "send_comparison":       "Send Carrier Comparison to Client",
    "block_bind":            "Block Binding Until Reviewed",
}

# ── Pre-built rule templates ───────────────────────────────────────────────────
TEMPLATES = [
    {
        "name": "Auto Follow-Up After 3 Days",
        "description": "If a quote has been sent to carriers but no response in 3 days, alert the agent.",
        "trigger": "carrier_no_response",
        "trigger_config": {"days": 3},
        "condition": {"lob": None, "stage": "sent"},
        "action": "notify_agent",
        "action_config": {"message": "Quote {quote_number} has no carrier response after 3 days. Follow up with carriers."},
        "enabled": True,
    },
    {
        "name": "Renewal Quote 90 Days Before Expiry",
        "description": "Automatically create a new quote 90 days before a policy expires.",
        "trigger": "policy_expiring",
        "trigger_config": {"days": 90},
        "condition": {"status": "active"},
        "action": "create_renewal_quote",
        "action_config": {"assign_to_original_agent": True, "add_same_carriers": True},
        "enabled": True,
    },
    {
        "name": "Client Acknowledgment on New Quote",
        "description": "Send client an email when a new quote request is created.",
        "trigger": "quote_created",
        "trigger_config": {},
        "condition": {},
        "action": "send_email_client",
        "action_config": {
            "subject": "We received your insurance quote request — {quote_number}",
            "body": "Hi {client_name},\n\nThank you for requesting a quote. We're shopping {lob} coverage for you in {state}. We'll have options ready for your review within 1-2 business days.\n\nQuote Reference: {quote_number}\n\nBest regards,\nYour Agent",
        },
        "enabled": True,
    },
    {
        "name": "Send Comparison When All Carriers Respond",
        "description": "When a quote enters Comparing stage, email the client a summary of options.",
        "trigger": "quote_comparing",
        "trigger_config": {},
        "condition": {},
        "action": "send_comparison",
        "action_config": {
            "subject": "Your Insurance Options Are Ready — {quote_number}",
            "body": "Hi {client_name},\n\nGreat news! We've received quotes from {carrier_count} carriers for your {lob} coverage in {state}.\n\nBest option: {best_carrier} at {best_premium}/yr\n\nYour agent will contact you shortly to review the options.\n\nQuote: {quote_number}",
        },
        "enabled": True,
    },
    {
        "name": "Block Bind if Below State Minimum",
        "description": "If a carrier quote is below jurisdiction minimums, flag it and block binding.",
        "trigger": "quote_below_minimum",
        "trigger_config": {},
        "condition": {},
        "action": "flag_compliance",
        "action_config": {"message": "Carrier limits are below {state} minimums. Review before binding."},
        "enabled": True,
    },
    {
        "name": "Welcome Email on Bind",
        "description": "Send the client a welcome email with policy number when a quote is bound.",
        "trigger": "quote_bound",
        "trigger_config": {},
        "condition": {},
        "action": "send_email_client",
        "action_config": {
            "subject": "Your policy is bound — {policy_number}",
            "body": "Hi {client_name},\n\nCongratulations! Your {lob} policy is now active.\n\nPolicy Number: {policy_number}\nCarrier: {bound_carrier}\nEffective: {effective_date}\n\nPlease keep this number for your records.\n\nThank you for choosing us!",
        },
        "enabled": True,
    },
    {
        "name": "30-Day Renewal Reminder to Client",
        "description": "Email client 30 days before policy expiry.",
        "trigger": "policy_expiring",
        "trigger_config": {"days": 30},
        "condition": {"status": "active"},
        "action": "send_email_client",
        "action_config": {
            "subject": "Your policy renews in 30 days — action required",
            "body": "Hi {client_name},\n\nYour {lob} policy ({policy_number}) with {carrier_name} renews on {expiry_date}.\n\nYour agent will contact you soon to review your options. If you have any questions, please don't hesitate to reach out.\n\nBest regards,\nYour Agency",
        },
        "enabled": True,
    },
    {
        "name": "15-Day Expiry Urgent Alert",
        "description": "Alert agent urgently when a quote expires in 15 days without binding.",
        "trigger": "quote_expiring",
        "trigger_config": {"days": 15},
        "condition": {"stage_not": "bound"},
        "action": "notify_agent",
        "action_config": {"message": "URGENT: Quote {quote_number} expires in 15 days and is not yet bound.", "priority": "high"},
        "enabled": True,
    },
    {
        "name": "Carrier Submission Email on New Quote",
        "description": "When a quote is created, auto-generate submission email drafts for preferred carriers.",
        "trigger": "quote_created",
        "trigger_config": {},
        "condition": {"lob": "auto"},
        "action": "send_email_carrier",
        "action_config": {
            "subject": "New Business Submission — {lob} — {state} — {quote_number}",
            "body": "Dear Underwriter,\n\nPlease find below a new business submission:\n\nQuote: {quote_number}\nLine: {lob}\nState: {state}\nClient: {client_name}\nEffective: {effective_date}\nRisk: {risk_summary}\n\nPlease provide your best rate at your earliest convenience.\n\nThank you",
        },
        "enabled": False,
    },
]

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS insurance_automation_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            trigger_type TEXT NOT NULL,
            trigger_config JSONB DEFAULT '{}',
            condition_config JSONB DEFAULT '{}',
            action_type TEXT NOT NULL,
            action_config JSONB DEFAULT '{}',
            enabled BOOLEAN DEFAULT TRUE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            run_count INTEGER DEFAULT 0,
            last_run_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("ALTER TABLE insurance_automation_rules ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)"))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS insurance_automation_log (
            id TEXT PRIMARY KEY,
            rule_id TEXT NOT NULL,
            rule_name TEXT,
            entity_type TEXT,
            entity_id TEXT,
            triggered_at TIMESTAMP DEFAULT NOW(),
            action_taken TEXT,
            result TEXT DEFAULT 'ok',
            detail TEXT,
            error TEXT
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS insurance_notifications (
            id TEXT PRIMARY KEY,
            agent TEXT,
            quote_id TEXT,
            policy_id TEXT,
            priority TEXT DEFAULT 'normal',
            message TEXT NOT NULL,
            read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def r2d(row): return dict(row._mapping) if row else None


def _tid() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


def _own_rule(rid: str, db: Session):
    """The current tenant's rule, or 404 — another tenant's rule id looks the same as a missing one."""
    row = db.execute(text("SELECT * FROM insurance_automation_rules WHERE id=:id AND tenant_id = :tid"),
                     {"id": rid, "tid": _tid()}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    return row


def _enabled_rules(db: Session, all_tenants: bool = False) -> list:
    """Enabled rules of the current tenant (the scheduler passes all_tenants; each rule still only
    touches its own tenant's quotes and policies)."""
    sql = "SELECT * FROM insurance_automation_rules WHERE enabled=TRUE"
    if all_tenants:
        return [r2d(r) for r in db.execute(text(sql)).fetchall()]
    return [r2d(r) for r in db.execute(text(sql + " AND tenant_id = :tid"), {"tid": _tid()}).fetchall()]

def log_run(rule_id: str, rule_name: str, entity_type: str, entity_id: str,
            action: str, result: str, detail: str, db: Session, error: str = None):
    db.execute(text("""
        INSERT INTO insurance_automation_log
          (id, rule_id, rule_name, entity_type, entity_id, action_taken, result, detail, error)
        VALUES (:id,:rid,:rname,:et,:eid,:action,:result,:detail,:error)
    """), {"id": str(uuid.uuid4()), "rid": rule_id, "rname": rule_name,
           "et": entity_type, "eid": entity_id, "action": action,
           "result": result, "detail": detail, "error": error})
    db.execute(text("UPDATE insurance_automation_rules SET run_count=run_count+1, last_run_at=NOW() WHERE id=:id"),
               {"id": rule_id})
    db.commit()

def fill_template(template: str, ctx: dict) -> str:
    for k, v in ctx.items():
        template = template.replace(f"{{{k}}}", str(v or ""))
    return template

# ── Action executors ──────────────────────────────────────────────────────────

def exec_notify_agent(rule: dict, entity: dict, db: Session):
    cfg = rule.get("action_config") or {}
    msg = fill_template(cfg.get("message", "Automation triggered: {quote_number}"), entity)
    db.execute(text("""
        INSERT INTO insurance_notifications (id, agent, quote_id, policy_id, priority, message)
        VALUES (:id,:agent,:qid,:pid,:prio,:msg)
    """), {
        "id": str(uuid.uuid4()),
        "agent": entity.get("assigned_agent") or entity.get("agent") or "all",
        "qid":  entity.get("id") if entity.get("quote_number") else None,
        "pid":  entity.get("id") if entity.get("policy_number") else None,
        "prio": cfg.get("priority", "normal"),
        "msg":  msg,
    })
    db.commit()
    return f"Notified agent: {msg[:80]}"

def exec_send_email(rule: dict, entity: dict, db: Session, to: str = "client"):
    cfg = rule.get("action_config") or {}
    subject = fill_template(cfg.get("subject", "Update on your quote"), entity)
    body    = fill_template(cfg.get("body", ""), entity)
    # Store in notification log — real email would call smtp/sendgrid here
    db.execute(text("""
        INSERT INTO insurance_notifications (id, agent, quote_id, policy_id, priority, message)
        VALUES (:id,'email_queue',:qid,:pid,'normal',:msg)
    """), {
        "id": str(uuid.uuid4()),
        "qid": entity.get("id") if entity.get("quote_number") else None,
        "pid": entity.get("id") if entity.get("policy_number") else None,
        "msg": f"EMAIL TO {to.upper()}: {subject} | {body[:200]}",
    })
    db.commit()
    return f"Email queued to {to}: {subject}"

def exec_flag_compliance(rule: dict, entity: dict, db: Session):
    cfg = rule.get("action_config") or {}
    msg = fill_template(cfg.get("message", "Compliance review required"), entity)
    qid = entity.get("id")
    if qid and entity.get("quote_number"):
        db.execute(text("UPDATE quotes SET notes=COALESCE(notes,'') || :flag WHERE id=:id"),
                   {"flag": f"\n⚠️ COMPLIANCE FLAG: {msg}", "id": qid})
        db.commit()
    return f"Flagged: {msg}"

def exec_create_renewal_quote(rule: dict, policy: dict, db: Session):
    cfg = rule.get("action_config") or {}
    new_id  = str(uuid.uuid4())
    new_num = f"QT-{new_id[:8].upper()}"
    eff = policy.get("expiry_date")
    exp = str((datetime.strptime(eff, "%Y-%m-%d") + timedelta(days=365)).date()) if eff else None
    _tid = str(rule.get("tenant_id") or policy.get("tenant_id") or get_current_tenant_id() or DEFAULT_TENANT_ID)
    db.execute(text("""
        INSERT INTO quotes (id, quote_number, client_id, lob, state, country,
                            effective_date, expiry_date, notes, assigned_agent, risk_data, tenant_id)
        SELECT :id, :qnum, client_id, line_of_business, 'US', 'US',
               :eff, :exp,
               'Auto-created renewal quote from policy ' || policy_number,
               assigned_agent, coverage_summary, :tenant_id
        FROM policies WHERE id=:pid
    """), {"id": new_id, "qnum": new_num, "eff": eff, "exp": exp, "pid": policy["id"], "tenant_id": _tid})

    if cfg.get("add_same_carriers"):
        db.execute(text("""
            INSERT INTO quote_carriers (id, quote_id, carrier_name, carrier_code)
            SELECT gen_random_uuid()::text, :qid, carrier_name, carrier_code
            FROM policies WHERE id=:pid
        """), {"qid": new_id, "pid": policy["id"]})
    db.commit()
    return f"Renewal quote {new_num} created"

def exec_move_stage(rule: dict, entity: dict, db: Session):
    cfg   = rule.get("action_config") or {}
    stage = cfg.get("stage", "comparing")
    qid   = entity.get("id")
    if qid:
        db.execute(text("UPDATE quotes SET stage=:stage, updated_at=NOW() WHERE id=:id"),
                   {"stage": stage, "id": qid})
        db.commit()
    return f"Stage moved to {stage}"

def dispatch_action(rule: dict, entity: dict, entity_type: str, db: Session) -> str:
    action = rule.get("action_type")
    if action == "notify_agent":         return exec_notify_agent(rule, entity, db)
    if action == "send_email_client":    return exec_send_email(rule, entity, db, "client")
    if action == "send_email_carrier":   return exec_send_email(rule, entity, db, "carrier")
    if action == "send_comparison":      return exec_send_email(rule, entity, db, "client (comparison)")
    if action == "flag_compliance":      return exec_flag_compliance(rule, entity, db)
    if action == "create_renewal_quote": return exec_create_renewal_quote(rule, entity, db)
    if action == "move_stage":           return exec_move_stage(rule, entity, db)
    if action == "block_bind":           return exec_flag_compliance(rule, entity, db)
    if action == "create_task":          return exec_notify_agent(rule, entity, db)
    return "action not implemented"

# ── Rule evaluators ───────────────────────────────────────────────────────────

def eval_quote_rules(rules: list, db: Session) -> list:
    results = []
    now = datetime.utcnow()

    for rule in rules:
        trigger = rule["trigger_type"]
        tcfg    = rule.get("trigger_config") or {}
        cond    = rule.get("condition_config") or {}

        try:
            if trigger == "carrier_no_response":
                days = int(tcfg.get("days", 3))
                cutoff = now - timedelta(days=days)
                rows = db.execute(text("""
                    SELECT q.* FROM quotes q
                    WHERE q.tenant_id = :tid AND q.stage='sent' AND q.updated_at < :cutoff
                    AND NOT EXISTS (
                        SELECT 1 FROM quote_carriers qc
                        WHERE qc.quote_id=q.id AND qc.annual_premium IS NOT NULL
                    )
                """), {"cutoff": cutoff, "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    detail = dispatch_action(rule, e, "quote", db)
                    log_run(rule["id"], rule["name"], "quote", e["id"], rule["action_type"], "ok", detail, db)
                    results.append({"rule": rule["name"], "entity": e.get("quote_number"), "detail": detail})

            elif trigger == "quote_expiring":
                days = int(tcfg.get("days", 15))
                target = (now + timedelta(days=days)).date()
                rows = db.execute(text("""
                    SELECT * FROM quotes
                    WHERE tenant_id = :tid AND expiry_date::date = :target AND stage NOT IN ('bound','lost','expired')
                """), {"target": str(target), "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    if cond.get("stage_not") and e.get("stage") == cond["stage_not"]:
                        continue
                    detail = dispatch_action(rule, e, "quote", db)
                    log_run(rule["id"], rule["name"], "quote", e["id"], rule["action_type"], "ok", detail, db)
                    results.append({"rule": rule["name"], "entity": e.get("quote_number"), "detail": detail})

            elif trigger == "quote_comparing":
                rows = db.execute(text("""
                    SELECT q.* FROM quotes q
                    LEFT JOIN insurance_automation_log l
                      ON l.rule_id=:rid AND l.entity_id=q.id::text
                    WHERE q.tenant_id = :tid AND q.stage='comparing' AND l.id IS NULL
                """), {"rid": rule["id"], "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    carriers = db.execute(text(
                        "SELECT COUNT(*) FROM quote_carriers WHERE quote_id=:id AND annual_premium IS NOT NULL"
                    ), {"id": e["id"]}).scalar()
                    best = db.execute(text(
                        "SELECT carrier_name, annual_premium FROM quote_carriers WHERE quote_id=:id ORDER BY annual_premium ASC LIMIT 1"
                    ), {"id": e["id"]}).fetchone()
                    e["carrier_count"] = carriers
                    e["best_carrier"] = r2d(best)["carrier_name"] if best else "TBD"
                    e["best_premium"] = f"${r2d(best)['annual_premium']:,.0f}" if best and r2d(best)["annual_premium"] else "TBD"
                    detail = dispatch_action(rule, e, "quote", db)
                    log_run(rule["id"], rule["name"], "quote", e["id"], rule["action_type"], "ok", detail, db)
                    results.append({"rule": rule["name"], "entity": e.get("quote_number"), "detail": detail})

            elif trigger == "quote_created":
                rows = db.execute(text("""
                    SELECT q.* FROM quotes q
                    LEFT JOIN insurance_automation_log l
                      ON l.rule_id=:rid AND l.entity_id=q.id::text
                    WHERE q.tenant_id = :tid AND l.id IS NULL
                    ORDER BY q.created_at DESC
                """), {"rid": rule["id"], "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    detail = dispatch_action(rule, e, "quote", db)
                    log_run(rule["id"], rule["name"], "quote", e["id"], rule["action_type"], "ok", str(detail), db)
                    results.append({"rule": rule["name"], "entity": e.get("quote_number"), "detail": str(detail)})

            elif trigger == "quote_bound":
                rows = db.execute(text("""
                    SELECT q.* FROM quotes q
                    LEFT JOIN insurance_automation_log l
                      ON l.rule_id=:rid AND l.entity_id=q.id::text
                    WHERE q.tenant_id = :tid AND q.stage='bound' AND l.id IS NULL
                """), {"rid": rule["id"], "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    pol = db.execute(text("SELECT * FROM policies WHERE id=:id AND tenant_id = :tid"),
                                     {"id": e.get("bound_policy_id"), "tid": rule["tenant_id"]}).fetchone()
                    if pol: e.update(r2d(pol))
                    detail = dispatch_action(rule, e, "quote", db)
                    log_run(rule["id"], rule["name"], "quote", e["id"], rule["action_type"], "ok", detail, db)
                    results.append({"rule": rule["name"], "entity": e.get("quote_number"), "detail": detail})

            elif trigger == "quote_below_minimum":
                rows = db.execute(text("""
                    SELECT q.*, qc.annual_premium, qc.coverage_details, qc.carrier_name,
                           q.jurisdiction_minimums
                    FROM quotes q
                    JOIN quote_carriers qc ON qc.quote_id=q.id
                    LEFT JOIN insurance_automation_log l
                      ON l.rule_id=:rid AND l.entity_id=qc.id::text
                    WHERE q.tenant_id = :tid AND qc.annual_premium IS NOT NULL AND l.id IS NULL
                """), {"rid": rule["id"], "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    mins = e.get("jurisdiction_minimums") or {}
                    cov  = e.get("coverage_details") or {}
                    violated = False
                    for field, min_val in mins.items():
                        if min_val and cov.get(field) and float(cov[field]) < float(min_val):
                            violated = True; break
                    if violated:
                        detail = dispatch_action(rule, e, "carrier_quote", db)
                        log_run(rule["id"], rule["name"], "carrier_quote", e["id"], rule["action_type"], "ok", detail, db)
                        results.append({"rule": rule["name"], "entity": e.get("quote_number"), "detail": detail})

        except Exception as ex:
            logger.error(f"Automation rule {rule['name']} failed: {ex}")
            results.append({"rule": rule["name"], "error": str(ex)})

    return results

def eval_policy_rules(rules: list, db: Session) -> list:
    results = []
    now = datetime.utcnow()

    for rule in rules:
        trigger = rule["trigger_type"]
        tcfg    = rule.get("trigger_config") or {}
        cond    = rule.get("condition_config") or {}

        try:
            if trigger in ("policy_expiring", "renewal_due"):
                days   = int(tcfg.get("days", 90))
                target = (now + timedelta(days=days)).date()
                rows = db.execute(text("""
                    SELECT p.* FROM policies p
                    LEFT JOIN insurance_automation_log l
                      ON l.rule_id=:rid AND l.entity_id=p.id::text
                    WHERE p.tenant_id = :tid AND p.expiry_date::date = :target
                      AND p.status IN ('active','renewal_due')
                      AND l.id IS NULL
                """), {"rid": rule["id"], "target": str(target), "tid": rule["tenant_id"]}).fetchall()
                for row in rows:
                    e = r2d(row)
                    if cond.get("status") and e.get("status") not in (cond["status"] if isinstance(cond["status"], list) else [cond["status"]]):
                        continue
                    detail = dispatch_action(rule, e, "policy", db)
                    log_run(rule["id"], rule["name"], "policy", e["id"], rule["action_type"], "ok", detail, db)
                    results.append({"rule": rule["name"], "entity": e.get("policy_number"), "detail": detail})

        except Exception as ex:
            logger.error(f"Policy automation rule {rule['name']} failed: {ex}")
            results.append({"rule": rule["name"], "error": str(ex)})

    return results

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/triggers")
def get_triggers(): return {"triggers": TRIGGERS}

@router.get("/actions")
def get_actions(): return {"actions": ACTIONS}

@router.get("/templates")
def get_templates(): return {"templates": TEMPLATES}

@router.post("/seed-templates")
def seed_templates(db: Session = Depends(get_db)):
    ensure_tables(db)
    seeded = []
    for t in TEMPLATES:
        existing = db.execute(text("SELECT id FROM insurance_automation_rules WHERE name=:n AND tenant_id = :tid"),
                              {"n": t["name"], "tid": _tid()}).fetchone()
        if not existing:
            rid = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO insurance_automation_rules
                  (id, name, description, trigger_type, trigger_config, condition_config,
                   action_type, action_config, enabled, tenant_id)
                VALUES (:id,:name,:desc,:trigger,CAST(:tc AS jsonb),CAST(:cc AS jsonb),:action,CAST(:ac AS jsonb),:enabled,:tid)
            """), {
                "id": rid, "tid": _tid(), "name": t["name"], "desc": t.get("description"),
                "trigger": t["trigger"], "tc": json.dumps(t.get("trigger_config", {})),
                "cc": json.dumps(t.get("condition", {})),
                "action": t["action"], "ac": json.dumps(t.get("action_config", {})),
                "enabled": t.get("enabled", True),
            })
            seeded.append(t["name"])
    db.commit()
    return {"seeded": len(seeded), "rules": seeded}

@router.get("")
def list_rules(db: Session = Depends(get_db)):
    ensure_tables(db)
    rows = db.execute(text("SELECT * FROM insurance_automation_rules WHERE tenant_id = :tid ORDER BY created_at"),
                      {"tid": _tid()}).fetchall()
    return [r2d(r) for r in rows]

@router.post("")
def create_rule(body: dict, db: Session = Depends(get_db)):
    ensure_tables(db)
    rid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO insurance_automation_rules
          (id, name, description, trigger_type, trigger_config, condition_config, action_type, action_config, enabled, tenant_id)
        VALUES (:id,:name,:desc,:trigger,CAST(:tc AS jsonb),CAST(:cc AS jsonb),:action,CAST(:ac AS jsonb),:enabled,:tid)
    """), {
        "id": rid, "tid": _tid(), "name": body["name"], "desc": body.get("description"),
        "trigger": body["trigger_type"],
        "tc":      json.dumps(body.get("trigger_config", {})),
        "cc":      json.dumps(body.get("condition_config", {})),
        "action":  body["action_type"],
        "ac":      json.dumps(body.get("action_config", {})),
        "enabled": body.get("enabled", True),
    })
    db.commit()
    return r2d(_own_rule(rid, db))

_EDITABLE = {"name", "description", "trigger_type", "trigger_config", "condition_config",
             "action_type", "action_config", "enabled"}


@router.patch("/{rid}")
def update_rule(rid: str, body: dict, db: Session = Depends(get_db)):
    ensure_tables(db)
    _own_rule(rid, db)
    unknown = set(body) - _EDITABLE - {"id", "created_at", "updated_at", "tenant_id", "run_count", "last_run_at"}
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown field(s): {', '.join(sorted(unknown))}")
    updates = {k: v for k, v in body.items() if k in _EDITABLE}   # column names come only from this list
    if not updates: return {"ok": True}
    set_parts = []
    for k in updates:
        if k in ("trigger_config", "condition_config", "action_config"):
            set_parts.append(f"{k}=CAST(:{k} AS jsonb)")
            updates[k] = json.dumps(updates[k]) if isinstance(updates[k], (dict, list)) else updates[k]
        else:
            set_parts.append(f"{k}=:{k}")
    updates.update({"id": rid, "tid": _tid()})
    db.execute(text(f"UPDATE insurance_automation_rules SET {', '.join(set_parts)}, updated_at=NOW() "
                    f"WHERE id=:id AND tenant_id = :tid"), updates)
    db.commit()
    return {"ok": True}

@router.delete("/{rid}")
def delete_rule(rid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    _own_rule(rid, db)
    db.execute(text("DELETE FROM insurance_automation_rules WHERE id=:id AND tenant_id = :tid"), {"id": rid, "tid": _tid()})
    db.commit()
    return {"ok": True}

@router.post("/run")
def run_automations(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger all enabled automation rules. Called by scheduler or manually."""
    ensure_tables(db)
    rules = _enabled_rules(db)

    def _run():
        quote_triggers  = {"carrier_no_response","quote_expiring","quote_comparing","quote_bound","quote_below_minimum"}
        policy_triggers = {"policy_expiring","renewal_due"}
        q_rules = [r for r in rules if r["trigger_type"] in quote_triggers]
        p_rules = [r for r in rules if r["trigger_type"] in policy_triggers]
        qr = eval_quote_rules(q_rules, db)
        pr = eval_policy_rules(p_rules, db)
        logger.info(f"Automation run complete: {len(qr)+len(pr)} actions")

    background_tasks.add_task(_run)
    return {"queued": len(rules), "message": "Automation run started in background"}

@router.post("/run/sync")
def run_automations_sync(db: Session = Depends(get_db)):
    """Synchronous run for testing."""
    ensure_tables(db)
    rules = _enabled_rules(db)
    quote_triggers  = {"carrier_no_response","quote_expiring","quote_comparing","quote_bound","quote_below_minimum"}
    policy_triggers = {"policy_expiring","renewal_due"}
    results = (
        eval_quote_rules( [r for r in rules if r["trigger_type"] in quote_triggers],  db) +
        eval_policy_rules([r for r in rules if r["trigger_type"] in policy_triggers], db)
    )
    return {"actions_taken": len(results), "results": results}

@router.post("/{rule_id}/run")
def run_single_rule(rule_id: str, db: Session = Depends(get_db)):
    """Run a single automation rule by ID (synchronous)."""
    ensure_tables(db)
    rule = r2d(_own_rule(rule_id, db))
    quote_triggers  = {"carrier_no_response","quote_expiring","quote_comparing","quote_bound","quote_below_minimum","quote_created"}
    policy_triggers = {"policy_expiring","renewal_due"}
    results = []
    if rule["trigger_type"] in quote_triggers:
        results = eval_quote_rules([rule], db)
    elif rule["trigger_type"] in policy_triggers:
        results = eval_policy_rules([rule], db)
    return {"actions_taken": len(results), "results": results}

@router.get("/log")
def get_log(limit: int = 100, db: Session = Depends(get_db)):
    ensure_tables(db)
    rows = db.execute(text(scope_sql(
        "SELECT * FROM insurance_automation_log ORDER BY triggered_at DESC LIMIT :lim")),
        tenant_params({"lim": limit})).fetchall()
    return [r2d(r) for r in rows]

@router.get("/notifications")
def get_notifications(agent: Optional[str] = None, unread_only: bool = False, db: Session = Depends(get_db)):
    ensure_tables(db)
    q = "SELECT * FROM insurance_notifications WHERE 1=1"
    p: dict = {}
    if agent: q += " AND (agent=:agent OR agent='all')"; p["agent"] = agent
    if unread_only: q += " AND read=FALSE"
    q += " ORDER BY created_at DESC LIMIT 50"
    rows = db.execute(text(scope_sql(q)), tenant_params(p)).fetchall()
    return [r2d(r) for r in rows]

@router.post("/notifications/{nid}/read")
def mark_read(nid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    db.execute(text(scope_sql(
        "UPDATE insurance_notifications SET read=TRUE WHERE id=:id "
        "AND id IN (SELECT n.id FROM insurance_notifications n)")), tenant_params({"id": nid}))
    db.commit()
    return {"ok": True}

# ── Event hooks (called from other routers on state changes) ──────────────────

async def on_quote_event(event: str, quote_id: str, db: Session):
    """Called on quote_created, quote_bound. Fires matching instant-trigger rules."""
    ensure_tables(db)
    quote = r2d(db.execute(text("SELECT * FROM quotes WHERE id=:id"), {"id": quote_id}).fetchone())
    if not quote: return
    rules = [r2d(r) for r in db.execute(text("""
        SELECT * FROM insurance_automation_rules
        WHERE trigger_type=:event AND enabled=TRUE AND tenant_id = :tid
    """), {"event": event, "tid": quote.get("tenant_id")}).fetchall()]
    if not rules: return
    for rule in rules:
        try:
            detail = dispatch_action(rule, quote, "quote", db)
            log_run(rule["id"], rule["name"], "quote", quote_id, rule["action_type"], "ok", detail, db)
        except Exception as ex:
            log_run(rule["id"], rule["name"], "quote", quote_id, rule["action_type"], "error", "", db, str(ex))


@router.get("/scheduler/status")
def get_scheduler_status():
    """Return last/next automation run times for the UI."""
    from app.services.scheduler import _last_automation_run, _next_automation_run
    from datetime import datetime, timedelta
    return {
        "last_run":    _last_automation_run.get("at"),
        "last_actions": _last_automation_run.get("actions", 0),
        "next_run":    _next_automation_run.get("at"),
        "interval_minutes": 60,
    }
