"""
Immigration suite: cases (management + tracking), CRM leads, intake questionnaires,
e-signature requests, reports, AI drafting and translation.

Everything is scoped to a tenant id passed in by the router. Client-facing links
(intake, signing) use random tokens; only their SHA-256 is stored.
"""
import hashlib
import json
import re
import secrets
import uuid
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from sqlalchemy import text

_RULES = Path(__file__).with_name("immigration_rules.yaml")
CASE_FIELDS = {"beneficiary", "petitioner", "case_type", "visa_class", "status", "receipt_number", "filed_date",
               "priority_date", "decision_date", "rfe_due_date", "expiry_date", "notes", "client_id"}
LEAD_STAGES = ["lead", "consult_scheduled", "consulted", "retained", "lost"]
LEAD_FIELDS = {"name", "email", "phone", "language", "source", "interest", "stage", "next_follow_up", "notes"}


@lru_cache(maxsize=1)
def rules() -> Dict:
    return yaml.safe_load(_RULES.read_text())


def _id() -> str:
    return str(uuid.uuid4())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row(r) -> Dict:
    d = dict(r._mapping)
    for k, v in d.items():
        if isinstance(v, (date, datetime)):
            d[k] = v.isoformat()
    return d


def _date(v) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _event(db, case_id: str, kind: str, msg: str, actor: Optional[str] = None):
    db.execute(text("INSERT INTO imm_case_events (id, case_id, kind, text, actor) VALUES (:id, :c, :k, :t, :a)"),
               {"id": _id(), "c": case_id, "k": kind, "t": msg, "a": actor})


# ── Cases ────────────────────────────────────────────────────────────────────

def _own_case(db, tenant: str, case_id: str) -> Dict:
    r = db.execute(text("SELECT * FROM imm_cases WHERE id = :id AND tenant_id = :t"), {"id": case_id, "t": tenant}).fetchone()
    if not r:
        raise LookupError("case")
    return _row(r)


def create_case(db, tenant: str, data: Dict, actor: Optional[str] = None) -> Dict:
    ct = data.get("case_type")
    spec = rules()["case_types"].get(ct)
    if not spec:
        raise ValueError(f"Unknown case type {ct!r}")
    if not (data.get("beneficiary") or "").strip():
        raise ValueError("Beneficiary name is required")
    cid = _id()
    vals = {k: data.get(k) for k in CASE_FIELDS}
    vals.update({"case_type": ct, "visa_class": data.get("visa_class") or spec.get("visa_class"),
                 "status": data.get("status") or "intake"})
    for k in ("filed_date", "priority_date", "decision_date", "rfe_due_date", "expiry_date"):
        vals[k] = _date(vals.get(k))
    db.execute(text(
        "INSERT INTO imm_cases (id, tenant_id, client_id, lead_id, beneficiary, petitioner, case_type, visa_class, status, "
        "receipt_number, filed_date, priority_date, decision_date, rfe_due_date, expiry_date, notes) VALUES "
        "(:id, :t, :client_id, :lead_id, :beneficiary, :petitioner, :case_type, :visa_class, :status, :receipt_number, "
        ":filed_date, :priority_date, :decision_date, :rfe_due_date, :expiry_date, :notes)"),
        {**vals, "id": cid, "t": tenant, "lead_id": data.get("lead_id")})
    for i, item in enumerate(spec.get("evidence", [])):
        db.execute(text("INSERT INTO imm_case_checklist (id, case_id, item, position) VALUES (:id, :c, :i, :p)"),
                   {"id": _id(), "c": cid, "i": item, "p": i})
    _event(db, cid, "status", f"Case opened: {spec['label']} ({spec['form']}) for {vals['beneficiary']}", actor)
    db.commit()
    match_checklist(db, tenant, cid)
    return get_case(db, tenant, cid)


def update_case(db, tenant: str, case_id: str, data: Dict, actor: Optional[str] = None) -> Dict:
    old = _own_case(db, tenant, case_id)
    unknown = set(data) - CASE_FIELDS - {"id", "tenant_id", "created_at", "updated_at"}
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    upd = {k: v for k, v in data.items() if k in CASE_FIELDS}
    if "status" in upd and upd["status"] not in rules()["statuses"]:
        raise ValueError(f"Unknown status {upd['status']!r}")
    if "case_type" in upd and upd["case_type"] not in rules()["case_types"]:
        raise ValueError(f"Unknown case type {upd['case_type']!r}")
    for k in ("filed_date", "priority_date", "decision_date", "rfe_due_date", "expiry_date"):
        if k in upd:
            upd[k] = _date(upd[k])
    if upd:
        sets = ", ".join(f"{k} = :{k}" for k in upd)          # keys come only from CASE_FIELDS
        db.execute(text(f"UPDATE imm_cases SET {sets}, updated_at = NOW() WHERE id = :id AND tenant_id = :t"),
                   {**upd, "id": case_id, "t": tenant})
        if "status" in upd and upd["status"] != old["status"]:
            _event(db, case_id, "status", f"Status: {old['status'].replace('_', ' ')} → {upd['status'].replace('_', ' ')}", actor)
        for k, label in (("receipt_number", "Receipt number"), ("filed_date", "Filed"), ("rfe_due_date", "RFE response due"),
                         ("decision_date", "Decision")):
            if k in upd and str(upd[k] or "") != str(old.get(k) or ""):
                _event(db, case_id, "filing" if k != "rfe_due_date" else "rfe", f"{label}: {upd[k]}", actor)
        db.commit()
    return get_case(db, tenant, case_id)


def deadlines(case: Dict, today: Optional[date] = None) -> List[Dict]:
    today = today or date.today()
    spec = rules()["case_types"].get(case["case_type"], {})
    out = []
    exp = _date(case.get("expiry_date"))
    if exp:
        win = spec.get("file_window_days")
        if win and case["status"] in ("intake", "preparing", "ready_to_file"):
            opens = exp - timedelta(days=win)
            out.append({"what": f"Filing window opens ({win} days before expiry)", "date": opens.isoformat(),
                        "days": (opens - today).days})
        out.append({"what": "Current status / document expires", "date": exp.isoformat(), "days": (exp - today).days})
    rfe = _date(case.get("rfe_due_date"))
    if rfe and case["status"] == "rfe":
        out.append({"what": "RFE response due at USCIS", "date": rfe.isoformat(), "days": (rfe - today).days})
    return sorted(out, key=lambda d: d["date"])


def get_case(db, tenant: str, case_id: str) -> Dict:
    c = _own_case(db, tenant, case_id)
    spec = rules()["case_types"].get(c["case_type"], {})
    c["case_label"], c["form"] = spec.get("label", c["case_type"]), spec.get("form")
    c["events"] = [_row(r) for r in db.execute(text(
        "SELECT * FROM imm_case_events WHERE case_id = :c ORDER BY at DESC"), {"c": case_id})]
    c["checklist"] = [_row(r) for r in db.execute(text(
        "SELECT * FROM imm_case_checklist WHERE case_id = :c ORDER BY position"), {"c": case_id})]
    c["deadlines"] = deadlines(c)
    c["intakes"] = [{k: v for k, v in _row(r).items() if k not in ("token_hash", "answers")} | {"answered": len(r.answers or {})}
                    for r in db.execute(text("SELECT * FROM imm_intakes WHERE case_id = :c ORDER BY created_at DESC"), {"c": case_id})]
    c["signatures"] = [{k: v for k, v in _row(r).items() if k != "token_hash"} for r in db.execute(text(
        "SELECT * FROM imm_signatures WHERE case_id = :c ORDER BY created_at DESC"), {"c": case_id})]
    c["tasks"] = list_tasks(db, tenant, case_id)
    c["has_task_template"] = c["case_type"] in task_templates()
    done = sum(1 for i in c["checklist"] if i["done"])
    c["progress"] = round(100 * done / len(c["checklist"])) if c["checklist"] else 0
    return c


# ── Tasks ────────────────────────────────────────────────────────────────────
TASK_ROLES = ("attorney", "paralegal", "client", "petitioner", "other")
_TASKS = Path(__file__).with_name("task_templates.yaml")


def _flatten(items) -> List[Dict]:
    out = []
    for i in items or []:
        out.extend(_flatten(i) if isinstance(i, list) else [i])
    return out


@lru_cache(maxsize=1)
def task_templates() -> Dict[str, List[Dict]]:
    raw = yaml.safe_load(_TASKS.read_text()).get("templates", {})
    return {k: _flatten(v) for k, v in raw.items()}


def list_tasks(db, tenant: str, case_id: str) -> List[Dict]:
    _own_case(db, tenant, case_id)
    rows = [_row(r) for r in db.execute(text(
        "SELECT * FROM imm_case_tasks WHERE case_id = :c AND tenant_id = :t ORDER BY done, position, due_date NULLS LAST"),
        {"c": case_id, "t": tenant})]
    today = date.today()
    for r in rows:
        d = _date(r.get("due_date"))
        r["days"] = (d - today).days if d else None
    return rows


def _clean_task(data: Dict) -> Dict:
    out = {}
    if "title" in data:
        t = (data.get("title") or "").strip()
        if not t:
            raise ValueError("Give the task a title")
        out["title"] = t[:300]
    if "role" in data:
        if data["role"] not in TASK_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(TASK_ROLES)}")
        out["role"] = data["role"]
    if "assignee" in data:
        out["assignee"] = (data.get("assignee") or "").strip()[:120] or None
    if "due_date" in data:
        out["due_date"] = _date(data["due_date"]) if data["due_date"] else None
        if data["due_date"] and not out["due_date"]:
            raise ValueError("Due date must be YYYY-MM-DD")
    if "done" in data:
        out["done"] = bool(data["done"])
    return out


def add_task(db, tenant: str, case_id: str, data: Dict, actor: Optional[str] = None) -> Dict:
    _own_case(db, tenant, case_id)
    t = _clean_task({"role": "paralegal", **data, "title": data.get("title")})
    pos = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM imm_case_tasks WHERE case_id = :c"), {"c": case_id}).scalar()
    tid = _id()
    db.execute(text("INSERT INTO imm_case_tasks (id, case_id, tenant_id, title, role, assignee, due_date, position, created_by) "
                    "VALUES (:id, :c, :t, :title, :role, :assignee, :due, :pos, :by)"),
               {"id": tid, "c": case_id, "t": tenant, "title": t["title"], "role": t["role"], "assignee": t.get("assignee"),
                "due": t.get("due_date"), "pos": pos, "by": actor})
    _event(db, case_id, "task", f"Task added: {t['title']}", actor)
    db.commit()
    return next(x for x in list_tasks(db, tenant, case_id) if x["id"] == tid)


def update_task(db, tenant: str, case_id: str, task_id: str, data: Dict, actor: Optional[str] = None) -> Dict:
    _own_case(db, tenant, case_id)
    cur = db.execute(text("SELECT * FROM imm_case_tasks WHERE id = :i AND case_id = :c AND tenant_id = :t"),
                     {"i": task_id, "c": case_id, "t": tenant}).fetchone()
    if not cur:
        raise LookupError("task")
    upd = _clean_task(data)
    if not upd:
        return next(x for x in list_tasks(db, tenant, case_id) if x["id"] == task_id)
    sets = ", ".join(f"{k} = :{k}" for k in upd)
    if "done" in upd:
        sets += ", done_at = CASE WHEN :done THEN now() ELSE NULL END, done_by = CASE WHEN :done THEN :by ELSE NULL END"
    db.execute(text(f"UPDATE imm_case_tasks SET {sets} WHERE id = :i AND tenant_id = :t"), {**upd, "i": task_id, "t": tenant, "by": actor})
    if "done" in upd and upd["done"] != cur.done:
        _event(db, case_id, "task", f"Task {'completed' if upd['done'] else 'reopened'}: {cur.title}", actor)
    db.commit()
    return next(x for x in list_tasks(db, tenant, case_id) if x["id"] == task_id)


def delete_task(db, tenant: str, case_id: str, task_id: str, actor: Optional[str] = None):
    _own_case(db, tenant, case_id)
    row = db.execute(text("DELETE FROM imm_case_tasks WHERE id = :i AND case_id = :c AND tenant_id = :t RETURNING title"),
                     {"i": task_id, "c": case_id, "t": tenant}).fetchone()
    if not row:
        raise LookupError("task")
    _event(db, case_id, "task", f"Task removed: {row.title}", actor)
    db.commit()


def apply_task_template(db, tenant: str, case_id: str, actor: Optional[str] = None, start: Optional[date] = None) -> Dict:
    """Add the case type's starter tasks. Tasks with the same title already on the case are skipped."""
    c = _own_case(db, tenant, case_id)
    tpl = task_templates().get(c["case_type"]) or task_templates().get("_default", [])
    have = {r.title.strip().lower() for r in db.execute(text("SELECT title FROM imm_case_tasks WHERE case_id = :c"), {"c": case_id})}
    start = start or date.today()
    pos = db.execute(text("SELECT COALESCE(MAX(position), 0) FROM imm_case_tasks WHERE case_id = :c"), {"c": case_id}).scalar()
    added = 0
    for item in tpl:
        if item["title"].strip().lower() in have:
            continue
        pos += 1
        added += 1
        db.execute(text("INSERT INTO imm_case_tasks (id, case_id, tenant_id, title, role, due_date, position, from_template, created_by) "
                        "VALUES (:id, :c, :t, :title, :role, :due, :pos, :tpl, :by)"),
                   {"id": _id(), "c": case_id, "t": tenant, "title": item["title"], "role": item.get("role", "paralegal"),
                    "due": start + timedelta(days=int(item.get("due", 7))), "pos": pos, "tpl": c["case_type"], "by": actor})
    if added:
        _event(db, case_id, "task", f"Task list applied: {added} task{'s' if added != 1 else ''} added", actor)
    db.commit()
    return {"added": added, "skipped": len(tpl) - added, "tasks": list_tasks(db, tenant, case_id)}


def list_cases(db, tenant: str, status: Optional[str] = None, q: Optional[str] = None) -> List[Dict]:
    sql = "SELECT * FROM imm_cases WHERE tenant_id = :t"
    p = {"t": tenant}
    if status:
        sql += " AND status = :s"
        p["s"] = status
    if q:
        sql += " AND (beneficiary ILIKE :q OR petitioner ILIKE :q OR receipt_number ILIKE :q OR visa_class ILIKE :q)"
        p["q"] = f"%{q}%"
    rows = [_row(r) for r in db.execute(text(sql + " ORDER BY updated_at DESC"), p)]
    prog = {r.case_id: (r.done, r.total) for r in db.execute(text(
        "SELECT case_id, COUNT(*) FILTER (WHERE done) AS done, COUNT(*) AS total FROM imm_case_checklist "
        "WHERE case_id = ANY(:ids) GROUP BY case_id"), {"ids": [r["id"] for r in rows] or [""]})}
    for r in rows:
        spec = rules()["case_types"].get(r["case_type"], {})
        r["case_label"], r["form"] = spec.get("label", r["case_type"]), spec.get("form")
        d, t = prog.get(r["id"], (0, 0))
        r["progress"] = round(100 * d / t) if t else 0
        dl = [x for x in deadlines(r) if x["days"] >= -365]
        r["next_deadline"] = min(dl, key=lambda x: x["days"]) if dl else None
    return rows


def delete_case(db, tenant: str, case_id: str):
    _own_case(db, tenant, case_id)
    db.execute(text("DELETE FROM imm_cases WHERE id = :id AND tenant_id = :t"), {"id": case_id, "t": tenant})
    db.commit()


def add_note(db, tenant: str, case_id: str, note: str, actor: Optional[str] = None):
    _own_case(db, tenant, case_id)
    _event(db, case_id, "note", note.strip()[:4000], actor)
    db.commit()


def toggle_check(db, tenant: str, case_id: str, item_id: str, done: bool):
    _own_case(db, tenant, case_id)
    db.execute(text("UPDATE imm_case_checklist SET done = :d WHERE id = :i AND case_id = :c"),
               {"d": done, "i": item_id, "c": case_id})
    db.commit()


# Checklist item keyword → document types that satisfy it
_EVIDENCE_TYPES = [
    (r"passport", ["passport", "passport_document"]),
    (r"i-797|approval notice", ["uscis_receipt", "i797", "immigration_receipt"]),
    (r"i-94", ["i94", "uscis_receipt"]),
    (r"pay stub", ["payroll", "pay_stub"]),
    (r"resume", ["resume"]),
    (r"birth certificate", ["birth_certificate"]),
    (r"tax return", ["tax_form", "tax_return", "form_1040", "w2"]),
    (r"visa", ["visa_document"]),
    (r"offer letter|support letter|employment verification", ["offer_letter", "employment_contract"]),
]


def match_checklist(db, tenant: str, case_id: str) -> int:
    """Tick checklist items the beneficiary's uploaded documents already cover (name must appear in the document)."""
    c = _own_case(db, tenant, case_id)
    names = [w for w in re.findall(r"[a-z]+", (c["beneficiary"] or "").lower()) if len(w) > 2]
    if not names:
        return 0
    docs = db.execute(text("SELECT id::text AS id, filename, doc_type, raw_text FROM documents "
                           "WHERE tenant_id = :t AND status = 'completed'"), {"t": tenant}).fetchall()
    mine = [d for d in docs if sum(n in ((d.raw_text or "") + " " + d.filename).lower() for n in names) >= min(2, len(names))]
    ticked = 0
    for item in db.execute(text("SELECT * FROM imm_case_checklist WHERE case_id = :c AND NOT done"), {"c": case_id}):
        for pat, types in _EVIDENCE_TYPES:
            if re.search(pat, item.item, re.I):
                doc = next((d for d in mine if d.doc_type in types), None)
                if doc:
                    db.execute(text("UPDATE imm_case_checklist SET done = TRUE, document_id = :d WHERE id = :i"),
                               {"d": doc.id, "i": item.id})
                    _event(db, case_id, "document", f"Checklist: “{item.item}” matched to {doc.filename}")
                    ticked += 1
                break
    db.commit()
    return ticked


def suggest_cases(db, tenant: str) -> List[Dict]:
    """Cases worth opening from the Visa Tracker: an expiring status with no open case for that person yet."""
    from app.services.immigration_tracker import tenant_records
    open_people = {(r.beneficiary or "").lower() for r in db.execute(text(
        "SELECT beneficiary FROM imm_cases WHERE tenant_id = :t AND status NOT IN ('approved','denied','withdrawn')"),
        {"t": tenant})}
    out = []
    for r in tenant_records(db, tenant):
        who = r.get("beneficiary_name")
        days = r.get("days_to_expiry")
        # long-expired documents were almost certainly replaced by a later filing — not a case to open
        if not who or days is None or days > 270 or days < -30 or who.lower() in open_people:
            continue
        cls = (r.get("visa_class") or "").upper()
        label = r.get("doc_label") or ""
        if "visa" in label.lower() and "approval" not in label.lower():
            continue                                             # visa stamps don't need a USCIS filing
        ct = ("h1b_extension" if cls == "H-1B" else "h4_extension" if cls == "H-4"
              else "passport_renewal" if "passport" in label.lower() else None)
        if ct:
            exp = min(d for d in (r.get("valid_until"), r.get("authorized_stay_until")) if d)
            out.append({"beneficiary": who, "case_type": ct, "case_label": rules()["case_types"][ct]["label"],
                        "visa_class": r.get("visa_class"), "petitioner": r.get("employer_sponsor"), "expiry_date": exp,
                        "days_to_expiry": days, "reason": f"{label} expires {exp} ({days} days)"})
    return out


# ── CRM leads ────────────────────────────────────────────────────────────────

def list_leads(db, tenant: str) -> List[Dict]:
    return [_row(r) for r in db.execute(text("SELECT * FROM imm_leads WHERE tenant_id = :t ORDER BY updated_at DESC"),
                                        {"t": tenant})]


def save_lead(db, tenant: str, data: Dict, lead_id: Optional[str] = None) -> Dict:
    unknown = set(data) - LEAD_FIELDS - {"id", "tenant_id", "created_at", "updated_at", "case_id"}
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    vals = {k: v for k, v in data.items() if k in LEAD_FIELDS}
    if "stage" in vals and vals["stage"] not in LEAD_STAGES:
        raise ValueError(f"Unknown stage {vals['stage']!r}")
    if "next_follow_up" in vals:
        vals["next_follow_up"] = _date(vals["next_follow_up"])
    if lead_id:
        if not db.execute(text("SELECT 1 FROM imm_leads WHERE id = :i AND tenant_id = :t"), {"i": lead_id, "t": tenant}).fetchone():
            raise LookupError("lead")
        if vals:
            sets = ", ".join(f"{k} = :{k}" for k in vals)
            db.execute(text(f"UPDATE imm_leads SET {sets}, updated_at = NOW() WHERE id = :i AND tenant_id = :t"),
                       {**vals, "i": lead_id, "t": tenant})
    else:
        if not (vals.get("name") or "").strip():
            raise ValueError("Name is required")
        lead_id = _id()
        cols = ["id", "tenant_id"] + list(vals)
        db.execute(text(f"INSERT INTO imm_leads ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"),
                   {**vals, "id": lead_id, "tenant_id": tenant})
    db.commit()
    return _row(db.execute(text("SELECT * FROM imm_leads WHERE id = :i"), {"i": lead_id}).fetchone())


def convert_lead(db, tenant: str, lead_id: str, case_type: Optional[str] = None, actor: Optional[str] = None) -> Dict:
    lead = db.execute(text("SELECT * FROM imm_leads WHERE id = :i AND tenant_id = :t"), {"i": lead_id, "t": tenant}).fetchone()
    if not lead:
        raise LookupError("lead")
    ct = case_type or lead.interest
    if ct not in rules()["case_types"]:
        raise ValueError("Choose a case type to open")
    case = create_case(db, tenant, {"beneficiary": lead.name, "case_type": ct, "lead_id": lead_id,
                                    "notes": lead.notes}, actor)
    db.execute(text("UPDATE imm_leads SET stage = 'retained', case_id = :c, updated_at = NOW() WHERE id = :i"),
               {"c": case["id"], "i": lead_id})
    db.commit()
    return case


def delete_lead(db, tenant: str, lead_id: str):
    db.execute(text("DELETE FROM imm_leads WHERE id = :i AND tenant_id = :t"), {"i": lead_id, "t": tenant})
    db.commit()


# ── Intake questionnaires ───────────────────────────────────────────────────

def intake_schema(case_type: str) -> List[Dict]:
    spec = rules()["case_types"][case_type]
    return [{"key": s, **rules()["intake_sections"][s]} for s in spec.get("intake_sections", [])]


def create_intake(db, tenant: str, case_id: str, language: str = "en", days_valid: int = 30) -> Dict:
    c = _own_case(db, tenant, case_id)
    if language not in rules()["languages"]:
        raise ValueError(f"Unsupported language {language!r}")
    token = secrets.token_urlsafe(32)
    iid = _id()
    db.execute(text("INSERT INTO imm_intakes (id, tenant_id, case_id, token_hash, case_type, language, expires_at) "
                    "VALUES (:id, :t, :c, :h, :ct, :l, :e)"),
               {"id": iid, "t": tenant, "c": case_id, "h": _hash(token), "ct": c["case_type"], "l": language,
                "e": datetime.utcnow() + timedelta(days=days_valid)})
    _event(db, case_id, "intake", f"Intake questionnaire sent ({rules()['languages'][language]})")
    db.commit()
    return {"id": iid, "token": token, "expires_in_days": days_valid}      # token is shown once, never stored


def intake_by_token(db, token: str) -> Optional[Dict]:
    r = db.execute(text("SELECT i.*, c.beneficiary FROM imm_intakes i JOIN imm_cases c ON c.id = i.case_id "
                        "WHERE i.token_hash = :h AND (i.expires_at IS NULL OR i.expires_at > NOW())"),
                   {"h": _hash(token)}).fetchone()
    return _row(r) if r else None


def submit_intake(db, token: str, answers: Dict, final: bool) -> Dict:
    it = intake_by_token(db, token)
    if not it or it["status"] == "submitted":
        raise LookupError("intake")
    allowed = {f["key"]: f for s in intake_schema(it["case_type"]) for f in s["fields"]}
    clean = {k: str(v)[:4000] for k, v in (answers or {}).items() if k in allowed and v not in (None, "")}
    if final:
        missing = [allowed[k]["label"] for k, f in allowed.items() if f.get("required") and not clean.get(k)]
        if missing:
            raise ValueError("Please answer: " + "; ".join(missing))
    db.execute(text("UPDATE imm_intakes SET answers = CAST(:a AS jsonb), status = :s, submitted_at = :at WHERE id = :id"),
               {"a": json.dumps(clean), "s": "submitted" if final else "in_progress",
                "at": datetime.utcnow() if final else None, "id": it["id"]})
    if final:
        _event(db, it["case_id"], "intake", f"Client submitted the intake questionnaire ({len(clean)} answers)", "client")
        # Fill empty case fields from the answers
        c = db.execute(text("SELECT * FROM imm_cases WHERE id = :c"), {"c": it["case_id"]}).fetchone()
        upd = {}
        if clean.get("employer") and not c.petitioner:
            upd["petitioner"] = clean["employer"]
        if clean.get("status_expiry") and not c.expiry_date:
            upd["expiry_date"] = _date(clean["status_expiry"])
        if upd:
            sets = ", ".join(f"{k} = :{k}" for k in upd)
            db.execute(text(f"UPDATE imm_cases SET {sets}, updated_at = NOW() WHERE id = :c"), {**upd, "c": it["case_id"]})
    db.commit()
    return {"status": "submitted" if final else "in_progress", "saved": len(clean)}


def intake_answers(db, tenant: str, intake_id: str) -> Dict:
    r = db.execute(text("SELECT * FROM imm_intakes WHERE id = :i AND tenant_id = :t"), {"i": intake_id, "t": tenant}).fetchone()
    if not r:
        raise LookupError("intake")
    d = {k: v for k, v in _row(r).items() if k != "token_hash"}
    d["schema"] = intake_schema(r.case_type)
    return d


def list_intakes(db, tenant: str) -> List[Dict]:
    return [{**{k: v for k, v in _row(r).items() if k not in ("token_hash", "answers")}, "answered": len(r.answers or {})}
            for r in db.execute(text("SELECT i.*, c.beneficiary FROM imm_intakes i JOIN imm_cases c ON c.id = i.case_id "
                                     "WHERE i.tenant_id = :t ORDER BY i.created_at DESC"), {"t": tenant})]


def intake_pdf(db, tenant: str, intake_id: str) -> bytes:
    """Form-ready data sheet: every answer under its section, for preparing the USCIS form."""
    import fitz
    it = intake_answers(db, tenant, intake_id)
    c = _own_case(db, tenant, it["case_id"])
    spec = rules()["case_types"][c["case_type"]]
    doc = fitz.open()
    page, y = doc.new_page(), 50

    def line(txt, size=10, bold=False, gap=15):
        nonlocal page, y
        if y > 780:
            page, y = doc.new_page(), 50
        page.insert_text((45, y), txt[:110], fontsize=size, fontname="helv" if not bold else "hebo")
        y += gap
    line(f"Intake data sheet — {spec['label']} ({spec['form']})", 14, True, 22)
    line(f"Beneficiary: {c['beneficiary']}    Submitted: {it.get('submitted_at') or 'not yet'}", 9, gap=20)
    answers = it.get("answers") or {}
    for sec in it["schema"]:
        line(sec["title"], 11, True, 17)
        for f in sec["fields"]:
            val = answers.get(f["key"], "—")
            for i, chunk in enumerate(str(val).splitlines() or ["—"]):
                line(f"   {f['label'] if i == 0 else '':<46} {chunk}", 9, gap=13)
        y += 6
    line("Prepared from the client's intake answers. Transfer to the current USCIS form edition and verify.", 8, gap=12)
    return doc.tobytes()


# ── E-signature ──────────────────────────────────────────────────────────────

def _doc_file(db, tenant: str, document_id: str):
    r = db.execute(text("SELECT id::text AS id, filename, file_path FROM documents WHERE id::text = :i AND tenant_id = :t"),
                   {"i": document_id, "t": tenant}).fetchone()
    if not r or not r.file_path or not Path(r.file_path).exists():
        raise LookupError("document")
    return r


def create_signature(db, tenant: str, case_id: str, title: str, signer_name: str, signer_email: Optional[str],
                     document_id: Optional[str], days_valid: int = 14) -> Dict:
    _own_case(db, tenant, case_id)
    if not title.strip() or not signer_name.strip():
        raise ValueError("Title and signer name are required")
    sha = None
    if document_id:
        sha = hashlib.sha256(Path(_doc_file(db, tenant, document_id).file_path).read_bytes()).hexdigest()
    token = secrets.token_urlsafe(32)
    sid = _id()
    db.execute(text("INSERT INTO imm_signatures (id, tenant_id, case_id, title, document_id, document_sha256, signer_name, "
                    "signer_email, token_hash, audit, expires_at) VALUES (:id, :t, :c, :ti, :d, :sha, :n, :e, :h, "
                    "CAST(:a AS jsonb), :x)"),
               {"id": sid, "t": tenant, "c": case_id, "ti": title.strip(), "d": document_id, "sha": sha,
                "n": signer_name.strip(), "e": signer_email, "h": _hash(token),
                "a": json.dumps([{"at": datetime.utcnow().isoformat(), "event": "created"}]),
                "x": datetime.utcnow() + timedelta(days=days_valid)})
    _event(db, case_id, "signature", f"Signature requested from {signer_name}: “{title}”")
    db.commit()
    return {"id": sid, "token": token, "expires_in_days": days_valid}


def signature_by_token(db, token: str) -> Optional[Dict]:
    r = db.execute(text("SELECT * FROM imm_signatures WHERE token_hash = :h"), {"h": _hash(token)}).fetchone()
    return _row(r) if r else None


def _audit(db, sid: str, event: str, ip: str, ua: str):
    db.execute(text("UPDATE imm_signatures SET audit = audit || CAST(:e AS jsonb) WHERE id = :i"),
               {"e": json.dumps([{"at": datetime.utcnow().isoformat(), "event": event, "ip": ip, "user_agent": ua[:300]}]),
                "i": sid})


def mark_viewed(db, token: str, ip: str, ua: str) -> Optional[Dict]:
    s = signature_by_token(db, token)
    if s and s["status"] == "sent":
        db.execute(text("UPDATE imm_signatures SET status = 'viewed' WHERE id = :i"), {"i": s["id"]})
        _audit(db, s["id"], "viewed", ip, ua)
        db.commit()
        s["status"] = "viewed"
    return s


def sign(db, token: str, typed_name: str, consent: bool, ip: str, ua: str, decline: bool = False) -> Dict:
    s = signature_by_token(db, token)
    if not s or s["status"] in ("signed", "declined", "voided"):
        raise LookupError("signature")
    if s.get("expires_at") and datetime.fromisoformat(s["expires_at"]) < datetime.utcnow():
        raise LookupError("signature")
    if decline:
        db.execute(text("UPDATE imm_signatures SET status = 'declined' WHERE id = :i"), {"i": s["id"]})
        _audit(db, s["id"], "declined", ip, ua)
        _event(db, s["case_id"], "signature", f"{s['signer_name']} declined to sign “{s['title']}”", "client")
        db.commit()
        return {"status": "declined"}
    if not consent:
        raise ValueError("Please agree to sign electronically")
    if " ".join(typed_name.split()).lower() != " ".join(s["signer_name"].split()).lower():
        raise ValueError(f"Type your full name exactly as: {s['signer_name']}")
    db.execute(text("UPDATE imm_signatures SET status = 'signed', signed_name = :n, signed_at = NOW() WHERE id = :i"),
               {"n": typed_name.strip(), "i": s["id"]})
    _audit(db, s["id"], "signed", ip, ua)
    _event(db, s["case_id"], "signature", f"{s['signer_name']} signed “{s['title']}”", "client")
    db.commit()
    return {"status": "signed"}


def signature_certificate(db, tenant: str, sig_id: str) -> bytes:
    """The signed document (if any) with a certificate page: who, when, hash, full audit trail."""
    import fitz
    r = db.execute(text("SELECT * FROM imm_signatures WHERE id = :i AND tenant_id = :t"), {"i": sig_id, "t": tenant}).fetchone()
    if not r:
        raise LookupError("signature")
    s = _row(r)
    out = fitz.open()
    if s["document_id"]:
        f = _doc_file(db, tenant, s["document_id"])
        if f.file_path.lower().endswith(".pdf"):
            with fitz.open(f.file_path) as src:
                out.insert_pdf(src)
    page, y = out.new_page(), 60
    rows = [("Signature certificate", 15), (f"Document: {s['title']}", 10), (f"Signer: {s['signer_name']}"
            + (f" <{s['signer_email']}>" if s.get("signer_email") else ""), 10),
            (f"Status: {s['status']}", 10), (f"Signed name: {s.get('signed_name') or '—'}", 10),
            (f"Signed at (UTC): {s.get('signed_at') or '—'}", 10),
            (f"Document SHA-256: {s.get('document_sha256') or 'no file attached'}", 8), ("", 8), ("Audit trail", 12)]
    rows += [(f"{a.get('at', '')}  {a.get('event', ''):8}  {a.get('ip') or ''}  {(a.get('user_agent') or '')[:60]}", 8)
             for a in (s.get("audit") or [])]
    for txt, size in rows:
        page.insert_text((50, y), txt[:120], fontsize=size)
        y += size + 8
    return out.tobytes()


def list_signatures(db, tenant: str) -> List[Dict]:
    return [{k: v for k, v in _row(r).items() if k != "token_hash"} for r in db.execute(text(
        "SELECT s.*, c.beneficiary FROM imm_signatures s LEFT JOIN imm_cases c ON c.id = s.case_id "
        "WHERE s.tenant_id = :t ORDER BY s.created_at DESC"), {"t": tenant})]


def void_signature(db, tenant: str, sig_id: str):
    db.execute(text("UPDATE imm_signatures SET status = 'voided' WHERE id = :i AND tenant_id = :t AND status <> 'signed'"),
               {"i": sig_id, "t": tenant})
    db.commit()


# ── Official form auto-fill ─────────────────────────────────────────────────

OFFICIAL_FORMS = {"I-129": {"case_types": {"h1b_extension", "h1b_transfer"}, "module": "i129"},
                  "I-539": {"case_types": {"h4_extension"}, "module": "i539"},
                  "I-765": {"case_types": {"h4_ead", "ead_renewal"}, "module": "i765"},
                  "I-140": {"case_types": {"i140"}, "module": "i140"},
                  "I-485": {"case_types": {"i485"}, "module": "i485"},
                  "I-130": {"case_types": {"i130"}, "module": "i130"},
                  "I-90": {"case_types": {"i90"}, "module": "i90"}}


def _form_module(form: str):
    import importlib
    spec = OFFICIAL_FORMS.get(form.upper())
    if not spec:
        raise ValueError(f"Auto-fill isn't available for {form}")
    return importlib.import_module(f"app.services.immigration.forms.{spec['module']}")


def _case_inputs(db, tenant: str, case_id: str):
    c = _own_case(db, tenant, case_id)
    r = db.execute(text("SELECT answers FROM imm_intakes WHERE case_id = :c AND tenant_id = :t AND answers <> '{}'::jsonb "
                        "ORDER BY (status = 'submitted') DESC, submitted_at DESC NULLS LAST, created_at DESC LIMIT 1"),
                   {"c": case_id, "t": tenant}).fetchone()
    fd = db.execute(text("SELECT form_data FROM imm_cases WHERE id = :c"), {"c": case_id}).scalar() or {}
    return c, (r.answers if r else {}), fd


def forms_for_case(db, tenant: str, case_id: str) -> List[str]:
    c = _own_case(db, tenant, case_id)
    return [f for f, spec in OFFICIAL_FORMS.items() if c["case_type"] in spec["case_types"]]


def form_preview(db, tenant: str, case_id: str, form: str) -> Dict:
    c, intake, fd = _case_inputs(db, tenant, case_id)
    return _form_module(form).preview(c, intake, fd)


def save_form_data(db, tenant: str, case_id: str, form: str, values: Dict) -> Dict:
    mod = _form_module(form)
    allowed = set(mod.TEXT_FIELDS) | set(getattr(mod, "CHOICES", {})) | {"sex", "current_status"}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    _own_case(db, tenant, case_id)
    clean = {k: str(v).strip()[:500] for k, v in values.items()}
    db.execute(text("UPDATE imm_cases SET form_data = COALESCE(form_data, '{}'::jsonb) || CAST(:v AS jsonb), "
                    "updated_at = NOW() WHERE id = :c AND tenant_id = :t"),
               {"v": json.dumps({k: v for k, v in clean.items() if v}), "c": case_id, "t": tenant})
    for k in [k for k, v in clean.items() if not v]:            # an emptied field falls back to intake / case data
        db.execute(text("UPDATE imm_cases SET form_data = form_data - :k WHERE id = :c AND tenant_id = :t"),
                   {"k": k, "c": case_id, "t": tenant})
    db.commit()
    return form_preview(db, tenant, case_id, form)


def form_pdf(db, tenant: str, case_id: str, form: str, actor: Optional[str] = None) -> bytes:
    c, intake, fd = _case_inputs(db, tenant, case_id)
    pdf, report = _form_module(form).fill(c, intake, fd)
    _event(db, case_id, "filing", f"Form {form.upper()} auto-filled: {report['filled']} of {report['total']} fields"
           + (f", {len(report['missing_required'])} required still blank" if report["missing_required"] else ""), actor)
    db.commit()
    return pdf


# ── Filing packet ────────────────────────────────────────────────────────────

def packet_sources(db, tenant: str, case_id: str) -> Dict:
    """What can go into this case's packet: built-in sections, intakes, and documents (case evidence first)."""
    c = get_case(db, tenant, case_id)
    matched = {i["document_id"]: i["item"] for i in c["checklist"] if i.get("document_id")}
    names = [w for w in re.findall(r"[a-z]+", (c["beneficiary"] or "").lower()) if len(w) > 2]
    docs = db.execute(text("SELECT id::text AS id, filename, doc_type, raw_text FROM documents "
                           "WHERE tenant_id = :t AND status = 'completed' ORDER BY created_at DESC"), {"t": tenant}).fetchall()
    rows = []
    for d in docs:
        hay = ((d.raw_text or "") + " " + d.filename).lower()
        rel = d.id in matched or (names and sum(n in hay for n in names) >= min(2, len(names)))
        rows.append({"type": "document", "id": d.id, "title": d.filename, "doc_type": d.doc_type,
                     "evidence_for": matched.get(d.id), "related": bool(rel)})
    rows.sort(key=lambda r: (r["evidence_for"] is None, not r["related"]))
    intakes = [{"type": "intake", "id": i["id"], "title": f"Intake questionnaire ({i['status']}, {i['answered']} answers)"}
               for i in c["intakes"] if i["answered"]]
    builtins = [{"type": "cover", "title": "Cover page"}, {"type": "toc", "title": "Table of contents"},
                {"type": "letter", "title": "Cover letter"}, {"type": "checklist", "title": "Evidence checklist"}]
    builtins += [{"type": "form", "id": f, "title": f"Form {f} (auto-filled)"} for f in forms_for_case(db, tenant, case_id)]
    return {"case": {k: c.get(k) for k in ("id", "beneficiary", "petitioner", "case_label", "form", "visa_class",
                                            "receipt_number")}, "builtins": builtins, "intakes": intakes, "documents": rows}


def _text_pages(doc, title: str, body: str, per_page: int = 52, width: int = 96):
    """Render plain text as wrapped lines across as many pages as needed."""
    import textwrap
    lines = []
    for para in (body or "—").splitlines():
        lines += textwrap.wrap(para, width) or [""]
    for n, start in enumerate(range(0, max(len(lines), 1), per_page)):
        page = doc.new_page(width=612, height=792)
        page.insert_text((54, 50), (title if n == 0 else f"{title} (continued)")[:90], fontsize=12, fontname="hebo")
        y = 78
        for ln in lines[start:start + per_page]:
            page.insert_text((54, y), ln, fontsize=9.5, fontname="helv")
            y += 13


def build_packet(db, tenant: str, case_id: str, items: List[Dict], preparer: Optional[str] = None,
                 letter_text: str = "", actor: Optional[str] = None) -> bytes:
    """
    One PDF in the given order. items: [{type: cover|toc|letter|checklist|intake|document, id?}].
    PDFs are inserted as-is, images become pages, other documents are rendered from their text.
    """
    import fitz
    c = get_case(db, tenant, case_id)
    if not items:
        raise ValueError("Choose at least one item for the packet")
    out = fitz.open()
    sections: List[tuple] = []           # (title, first page index)
    toc_at = None

    def start(title):
        sections.append((title, len(out)))

    for it in items:
        kind = it.get("type")
        if kind == "cover":
            start("Cover page")
            p = out.new_page()
            y = 150
            p.insert_text((72, y), "Filing packet", fontsize=26, fontname="hebo"); y += 40
            for label, val in (("Beneficiary", c["beneficiary"]), ("Petitioner / employer", c.get("petitioner")),
                               ("Case", c["case_label"]), ("Form", c.get("form")), ("Visa class", c.get("visa_class")),
                               ("Receipt number", c.get("receipt_number")), ("Prepared by", preparer or actor),
                               ("Date", date.today().strftime("%B %d, %Y"))):
                if val:
                    p.insert_text((72, y), f"{label}:", fontsize=11, fontname="hebo")
                    p.insert_text((230, y), str(val)[:70], fontsize=11, fontname="helv"); y += 22
        elif kind == "toc":
            start("Table of contents")
            toc_at = len(out)
            out.new_page()                                   # filled in at the end, once page numbers are known
        elif kind == "letter":
            if not letter_text.strip():
                raise ValueError("Write or draft the cover letter text first")
            start("Cover letter")
            _text_pages(out, "Cover letter", letter_text.strip())
        elif kind == "checklist":
            start("Evidence checklist")
            body = "\n".join(f"[{'x' if i['done'] else ' '}] {i['item']}" for i in c["checklist"]) or "No checklist items."
            _text_pages(out, f"Evidence checklist — {c['case_label']}", body)
        elif kind == "form":
            start(f"Form {it.get('id')}")
            with fitz.open(stream=form_pdf(db, tenant, case_id, it.get("id") or "", actor), filetype="pdf") as src:
                out.insert_pdf(src)
        elif kind == "intake":
            start("Intake questionnaire")
            with fitz.open(stream=intake_pdf(db, tenant, it["id"]), filetype="pdf") as src:
                out.insert_pdf(src)
        elif kind == "document":
            r = db.execute(text("SELECT filename, file_path, raw_text FROM documents WHERE id::text = :i AND tenant_id = :t"),
                           {"i": it.get("id"), "t": tenant}).fetchone()
            if not r:
                raise LookupError("document")
            start(r.filename)
            path = Path(r.file_path or "")
            ext = path.suffix.lower()
            try:
                if ext == ".pdf" and path.exists():
                    with fitz.open(path) as src:
                        out.insert_pdf(src)
                elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp") and path.exists():
                    with fitz.open(path) as img:
                        rect = img[0].rect
                        pdf_bytes = img.convert_to_pdf()
                    with fitz.open("pdf", pdf_bytes) as src:
                        page = out.new_page(width=612, height=792)
                        page.show_pdf_page(fitz.Rect(36, 36, 576, 756), src, 0, keep_proportion=True)
                else:
                    _text_pages(out, r.filename, (r.raw_text or "").strip() or "(no readable text)")
            except Exception:
                _text_pages(out, r.filename, (r.raw_text or "").strip() or "(this file could not be included)")
        else:
            raise ValueError(f"Unknown packet item {kind!r}")

    total = len(out)
    if toc_at is not None:
        p = out[toc_at]
        p.insert_text((72, 90), "Table of contents", fontsize=18, fontname="hebo")
        y = 130
        for title, first in sections:
            if title == "Table of contents":
                continue
            if y > 740:
                break
            p.insert_text((72, y), title[:70], fontsize=10.5, fontname="helv")
            p.insert_text((500, y), str(first + 1), fontsize=10.5, fontname="helv")
            y += 18
    for i, page in enumerate(out):
        page.insert_text((page.rect.width - 150, page.rect.height - 22), f"Page {i + 1} of {total}", fontsize=8,
                         fontname="helv", color=(0.35, 0.35, 0.35))
        page.insert_text((36, page.rect.height - 22), f"{c['beneficiary']} — {c.get('form') or ''}"[:60], fontsize=8,
                         fontname="helv", color=(0.35, 0.35, 0.35))
    out.set_toc([[1, t, f + 1] for t, f in sections])
    _event(db, case_id, "filing", f"Packet assembled: {len(sections)} sections, {total} pages", actor)
    db.commit()
    return out.tobytes(garbage=3, deflate=True)


# ── Reports ──────────────────────────────────────────────────────────────────

def report(db, tenant: str, today: Optional[date] = None) -> Dict:
    today = today or date.today()
    cases = list_cases(db, tenant)
    leads = list_leads(db, tenant)
    by = lambda key, rows: [{"key": k, "count": sum(1 for r in rows if r.get(key) == k)}
                            for k in sorted({r.get(key) for r in rows if r.get(key)})]
    upcoming = sorted(((c, d) for c in cases for d in deadlines(c, today) if 0 <= d["days"] <= 90),
                      key=lambda x: x[1]["days"])
    month = today.strftime("%Y-%m")
    sigs = list_signatures(db, tenant)
    retained = sum(1 for l in leads if l["stage"] == "retained")
    return {
        "totals": {"cases": len(cases), "open": sum(1 for c in cases if c["status"] not in ("approved", "denied", "withdrawn")),
                   "filed_this_month": sum(1 for c in cases if (c.get("filed_date") or "").startswith(month)),
                   "approved": sum(1 for c in cases if c["status"] == "approved"),
                   "rfe_open": sum(1 for c in cases if c["status"] == "rfe"),
                   "leads": len(leads), "retained": retained,
                   "conversion_pct": round(100 * retained / len(leads)) if leads else 0,
                   "signatures_pending": sum(1 for s in sigs if s["status"] in ("sent", "viewed"))},
        "by_status": by("status", cases), "by_type": by("case_label", cases), "pipeline": by("stage", leads),
        "upcoming": [{"case_id": c["id"], "beneficiary": c["beneficiary"], "case": c["case_label"], **d} for c, d in upcoming],
    }


def report_xlsx(db, tenant: str) -> bytes:
    import io
    import pandas as pd
    rep, cases, leads = report(db, tenant), list_cases(db, tenant), list_leads(db, tenant)
    labels = {"cases": "Cases", "open": "Open cases", "filed_this_month": "Filed this month", "approved": "Approved",
              "rfe_open": "Open RFEs", "leads": "Leads", "retained": "Retained", "conversion_pct": "Lead → retained (%)",
              "signatures_pending": "Signatures pending"}
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as xw:
        pd.DataFrame([(labels.get(k, k), v) for k, v in rep["totals"].items()] + [("Generated", date.today().isoformat())],
                     columns=["Measure", "Value"]).to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame([{"Beneficiary": c["beneficiary"], "Case": c["case_label"], "Form": c["form"], "Status": c["status"],
                       "Visa class": c.get("visa_class"), "Petitioner": c.get("petitioner"), "Receipt #": c.get("receipt_number"),
                       "Filed": c.get("filed_date"), "Status expires": c.get("expiry_date"), "Evidence %": c["progress"],
                       "Next deadline": (c["next_deadline"] or {}).get("what"), "Due": (c["next_deadline"] or {}).get("date")}
                      for c in cases]).to_excel(xw, sheet_name="Cases", index=False)
        pd.DataFrame([{"Beneficiary": u["beneficiary"], "Case": u["case"], "Deadline": u["what"], "Date": u["date"],
                       "Days left": u["days"]} for u in rep["upcoming"]]).to_excel(xw, sheet_name="Deadlines (90 days)", index=False)
        pd.DataFrame([{"Name": l["name"], "Stage": l["stage"], "Interested in": l.get("interest"), "Email": l.get("email"),
                       "Phone": l.get("phone"), "Next follow-up": l.get("next_follow_up"), "Source": l.get("source")}
                      for l in leads]).to_excel(xw, sheet_name="Leads", index=False)
        for name, key in (("By status", "by_status"), ("By type", "by_type"), ("Pipeline", "pipeline")):
            pd.DataFrame([{"Group": r["key"], "Count": r["count"]} for r in rep[key]]).to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def report_pdf(db, tenant: str) -> bytes:
    import fitz
    rep, cases = report(db, tenant), list_cases(db, tenant)
    doc = fitz.open()
    page, y = doc.new_page(), 50

    def line(t, size=10, bold=False, gap=None):
        nonlocal page, y
        if y > 790:
            page, y = doc.new_page(), 50
        page.insert_text((45, y), str(t)[:115], fontsize=size, fontname="hebo" if bold else "helv")
        y += gap or size + 6
    tt = rep["totals"]
    line("Immigration report", 16, True, 24)
    line(f"Generated {date.today():%B %d, %Y}", 9, gap=20)
    line(f"Open cases {tt['open']}  ·  Filed this month {tt['filed_this_month']}  ·  Open RFEs {tt['rfe_open']}  ·  "
         f"Approved {tt['approved']}  ·  Leads {tt['leads']} ({tt['conversion_pct']}% retained)  ·  "
         f"Signatures pending {tt['signatures_pending']}", 9, gap=22)
    line("Deadlines in the next 90 days", 12, True)
    for u in rep["upcoming"] or [None]:
        line("   Nothing due." if u is None else f"   {u['date']}  ({u['days']} days)  {u['beneficiary']} — {u['case']}: {u['what']}", 9)
    y += 8
    line("Cases", 12, True)
    for c in cases or [None]:
        if c is None:
            line("   No cases.", 9)
            continue
        nd = c["next_deadline"]
        line(f"   {c['beneficiary']} — {c['case_label']} ({c['form']}) · {c['status'].replace('_', ' ')} · evidence {c['progress']}%"
             + (f" · receipt {c['receipt_number']}" if c.get("receipt_number") else ""), 9)
        if nd:
            line(f"        next: {nd['what']} {nd['date']}", 8)
    y += 8
    for title, key in (("By status", "by_status"), ("By type", "by_type"), ("Lead pipeline", "pipeline")):
        line(title, 11, True)
        for r in rep[key] or [{"key": "—", "count": 0}]:
            line(f"   {str(r['key']).replace('_', ' ')}: {r['count']}", 9)
        y += 4
    return doc.tobytes()


# ── AI drafting and translation (local model) ───────────────────────────────

_DRAFT_KINDS = {
    "case_summary": "a concise internal case summary for the attorney: status, key dates, what is missing, risks",
    "client_update": "a short, plain-language email to the client explaining where their case stands and what they need to send",
    "support_letter": "an outline of an employer support letter for the petition (headings and bullet points, no invented facts)",
    "rfe_outline": "an outline for responding to a USCIS Request for Evidence: likely issues, evidence to gather, structure",
    "cover_letter": "a concise filing cover letter to USCIS: what is being filed and for whom, then a numbered list of "
                    "the enclosures exactly as given in the instructions; leave fee and signature details as [TO CONFIRM]",
}


def draft(db, tenant: str, case_id: str, kind: str, instructions: str = "") -> Dict:
    import ollama
    from app.core import config as app_config
    if kind not in _DRAFT_KINDS:
        raise ValueError(f"Unknown draft type {kind!r}")
    c = get_case(db, tenant, case_id)
    missing = [i["item"] for i in c["checklist"] if not i["done"]]
    have = [i["item"] for i in c["checklist"] if i["done"]]
    facts = (f"Case: {c['case_label']} ({c['form']}), visa class {c.get('visa_class') or '—'}\n"
             f"Beneficiary: {c['beneficiary']}\nPetitioner/employer: {c.get('petitioner') or '—'}\n"
             f"Status: {c['status']}\nReceipt: {c.get('receipt_number') or '—'}\nFiled: {c.get('filed_date') or '—'}\n"
             f"Current status expires: {c.get('expiry_date') or '—'}\nRFE due: {c.get('rfe_due_date') or '—'}\n"
             f"Deadlines: " + "; ".join(f"{d['what']} {d['date']}" for d in c["deadlines"]) + "\n"
             f"Evidence received: {', '.join(have) or 'none'}\nEvidence still missing: {', '.join(missing) or 'none'}\n"
             f"Recent notes: " + " | ".join(e["text"] for e in c["events"][:6]))
    prompt = (f"You are assisting an immigration legal team. Write {_DRAFT_KINDS[kind]}.\n"
              "Use ONLY the case facts below. If something is unknown, write [TO CONFIRM] — never invent dates, "
              "names, receipt numbers or legal citations.\n"
              + (f"Extra instructions: {instructions.strip()[:1000]}\n" if instructions.strip() else "")
              + f"\nCASE FACTS\n{facts}\n\nDRAFT:")
    resp = ollama.chat(model=app_config.model, messages=[{"role": "user", "content": prompt}],
                       options={"num_ctx": app_config.NUM_CTX, "temperature": 0.2, "num_predict": 900})
    return {"kind": kind, "draft": resp["message"]["content"].strip(),
            "notice": "AI draft for attorney review — verify every fact before use. Not legal advice."}


def translate(textval: str, target: str) -> Dict:
    import ollama
    from app.core import config as app_config
    langs = rules()["languages"]
    if target not in langs:
        raise ValueError(f"Unsupported language {target!r}")
    if not textval.strip():
        raise ValueError("Nothing to translate")
    resp = ollama.chat(model=app_config.model, messages=[{"role": "user", "content":
        f"Translate the text below into {langs[target]}. Keep names, numbers, dates and form numbers exactly as written. "
        f"Output only the translation.\n\n{textval.strip()[:6000]}"}],
        options={"num_ctx": app_config.NUM_CTX, "temperature": 0.0, "num_predict": 1800})
    return {"language": langs[target], "translation": resp["message"]["content"].strip(),
            "notice": "Machine translation (draft). USCIS requires a complete translation certified by a competent "
                      "translator for every foreign-language document (8 CFR 103.2(b)(3))."}


@lru_cache(maxsize=64)
def translated_labels(case_type: str, language: str) -> str:
    """Intake labels in the client's language (cached per case type + language)."""
    if language == "en":
        return ""
    labels = [s["title"] for s in intake_schema(case_type)] + [f["label"] for s in intake_schema(case_type) for f in s["fields"]]
    out = translate("\n".join(labels), language)["translation"]
    return out
