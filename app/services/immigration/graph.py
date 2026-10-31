"""
Immigration document graph (docs/design/immigration-document-graph.md).

Every immigration document becomes a node with its key facts (name, date of birth, A-number,
receipt number, I-94 number, passport number, class, valid dates). Edges link nodes that refer to
each other (same receipt or I-94 number, an approval and the status it grants, a newer document
replacing an older one). Checks compare facts across a person's nodes and raise mismatches.

The lawyer has the last word: a corrected fact wins over the extracted one (both are kept), an edge
can be confirmed, rejected or re-pointed, and a check can be waived with a reason. Every action is
written to imm_graph_log. Rebuilding never undoes a lawyer's decision.
"""

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PATTERNS = Path(__file__).with_name("patterns.yaml")
IMMIGRATION_TYPES = {"uscis_receipt", "uscis_notice", "immigration_receipt", "i797", "i94", "i20", "ds2019",
                     "i130", "i140", "i485", "i751", "i765", "i90", "n400", "i129f", "i131", "i539", "i821d",
                     "i589", "i864", "i360", "i526", "ead", "ead_card", "green_card", "permanent_resident_card",
                     "naturalization_certificate", "passport", "passport_document", "visa", "visa_document",
                     "immigration", "lca", "perm", "marriage_certificate", "birth_certificate"}
IDENTITY_FIELDS = {"name": "warning", "dob": "blocker", "a_number": "blocker", "passport_no": "blocker"}
FIELD_LABEL = {"name": "Name", "dob": "Date of birth", "a_number": "A-number", "passport_no": "Passport number",
               "receipt": "Receipt number", "i94_number": "I-94 number", "visa_class": "Class",
               "valid_from": "Valid from", "valid_to": "Valid to", "entry_date": "Date of entry"}
TYPE_LABEL = {"passport": "Passport", "i94": "I-94 record", "visa": "Visa stamp", "i797_approval": "I-797 approval",
              "i797_receipt": "I-797 receipt", "i797_rfe": "RFE", "i797_appointment": "Appointment notice",
              "ead": "EAD card", "green_card": "Green card", "lca": "LCA", "perm": "PERM",
              "status_period": "Status", "i140_approval": "I-140 approval", "i20": "I-20",
              "ds2019": "DS-2019", "marriage_certificate": "Marriage certificate", "birth_certificate": "Birth certificate"}
RELATIONS = ("spouse", "child", "parent", "fiance", "relative")
_EXPIRY_TYPES = ("passport", "i94", "ead", "green_card", "status_period", "visa")


def _id() -> str:
    return uuid.uuid4().hex[:16]


def _patterns_file() -> Dict:
    return yaml.safe_load(_PATTERNS.read_text()) or {}


def patterns() -> Dict:
    return _patterns_file().get("patterns", {})


def dependents() -> Dict[str, str]:
    """Dependent class → principal class (H-4 → H-1B …)."""
    return _patterns_file().get("dependents", {})


def norm_class(c: Optional[str]) -> str:
    """'H1B' / 'h-1b' → 'H-1B'; 'TD' stays."""
    c = re.sub(r"\s", "", str(c or "")).upper()
    return re.sub(r"^([A-Z])-?(\d)", r"\1-\2", c)


# ── Fact extraction ──────────────────────────────────────────────────────────

_DATE = r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2} [A-Z][a-z]{2,8},? \d{4}|[A-Z][a-z]{2,8} \d{1,2},? \d{4})"


def _parse_date(s: str, dayfirst: bool = False) -> Optional[date]:
    s = (s or "").strip().replace(",", "")
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        d, mo = (a, b) if (dayfirst or a > 12) else (b, a)
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _labelled(raw: str, labels: str, pattern: str) -> Optional[str]:
    m = re.search(rf"(?:{labels})\s*[:#]?\s*\n*\s*{pattern}", raw, re.I)
    return m.group(1).strip() if m else None


def node_type(doc_type: str, raw: str, fields: Dict, form: str) -> str:
    head = (raw or "")[:600].upper()
    ft = str(fields.get("form_type") or "").upper()
    if form:
        return f"form_{form}"
    if doc_type in ("passport", "passport_document") or "PASSPORT" in head[:120]:
        return "passport"
    if ft == "I-94" or "I-94 ARRIVAL" in head or "ARRIVAL/DEPARTURE RECORD" in head or doc_type == "i94":
        return "i94"
    if "REQUEST FOR EVIDENCE" in head or ft == "I-797E":
        return "i797_rfe"
    if "APPOINTMENT" in head and "I-797" in head:
        return "i797_appointment"
    if "APPROVAL NOTICE" in head or ft == "I-797A" or "APPROVAL NOTICE" in str(fields.get("notice_type", "")).upper():
        return "i140_approval" if re.search(r"Case Type\s*I-140", raw or "", re.I) else "i797_approval"
    if "RECEIPT NOTICE" in head or ft == "I-797C":
        return "i797_receipt"
    if doc_type in ("visa", "visa_document"):
        return "visa"
    if doc_type in ("ead", "ead_card") or "EMPLOYMENT AUTHORIZATION" in head:
        return "ead"
    if doc_type in ("green_card", "permanent_resident_card") or "PERMANENT RESIDENT" in head:
        return "green_card"
    if doc_type == "i20" or "FORM I-20" in head or "CERTIFICATE OF ELIGIBILITY FOR NONIMMIGRANT STUDENT" in head:
        return "i20"
    if doc_type == "ds2019" or "DS-2019" in head:
        return "ds2019"
    if doc_type == "marriage_certificate" or "MARRIAGE" in head[:200] and "CERTIFICATE" in head[:200]:
        return "marriage_certificate"
    if doc_type == "birth_certificate" or "BIRTH" in head[:200] and "CERTIFICATE" in head[:200]:
        return "birth_certificate"
    if "ETA-9035" in head or "LABOR CONDITION" in head:
        return "lca"
    if "ETA-9089" in head:
        return "perm"
    return doc_type or "document"


def extract_facts(ntype: str, raw: str, fields: Dict, name: str) -> Dict[str, Any]:
    raw = raw or ""
    dayfirst = ntype == "passport"                       # passports print day-first outside the US
    f: Dict[str, Any] = {}
    if name:
        f["name"] = re.sub(r"\s*\((?:fictional|sample)\)", "", name, flags=re.I).strip()
    f["receipt"] = (_labelled(raw, r"Receipt (?:Number|No\.?|#)", r"([A-Z]{3}\d{10})")
                    or (re.search(r"\b((?:EAC|WAC|LIN|SRC|MSC|NBC|IOE|YSC|NSC|TSC|VSC|CSC)\d{10})\b", raw) or [None, None])[1])
    f["a_number"] = _labelled(raw, r"A-?Number|Alien (?:Registration )?Number|USCIS ?#|A#", r"A?-?(\d{3}[- ]?\d{3}[- ]?\d{3}|\d{8})")
    f["i94_number"] = _labelled(raw, r"I-94 (?:Number|No\.?|Record Number)|Admission \(I-94\) Record Number", r"(\d{9}[A-Z0-9]{2})")
    f["visa_class"] = (fields.get("visa_class") if fields.get("visa_class") not in (None, "OF") else None) or \
        _labelled(raw, r"Class of Admission|Class|Visa Type|Category", r"(?-i:([A-Z]{1,2}-?\d[A-Z]?|[A-Z]{2,3}\d?))\b")
    if f["a_number"]:
        f["a_number"] = re.sub(r"\D", "", f["a_number"])
    dob = _labelled(raw, r"Date of Birth|Birth Date|DOB", _DATE)
    valid_from = _labelled(raw, r"Valid From|Date of Issue|Issue Date|Card Valid From", _DATE)
    valid_to = (_labelled(raw, r"Valid To|Valid Until|Admit Until Date|Admit Until|Date of Expiry|Expiry Date|Expires|Card Expires", _DATE)
                or fields.get("validity_end"))
    entry = _labelled(raw, r"(?:Most Recent )?Date of Entry", _DATE)
    if ntype == "passport":
        pn = re.search(r"\b([A-Z]{1,2}\d{7,8})\b", raw)
        f["passport_no"] = pn.group(1) if pn else None
        dates = re.findall(r"\b(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})\b", raw)
        if not dob and len(dates) >= 3 and re.search(r"Date of Birth.*Date of Issue.*Date of Expiry", raw, re.S | re.I):
            dob, valid_from, valid_to = dates[0], dates[1], dates[2]
    for k, v in (("dob", dob), ("valid_from", valid_from), ("valid_to", valid_to), ("entry_date", entry)):
        d = _parse_date(v, dayfirst) if isinstance(v, str) else None
        f[k] = d.isoformat() if d else None
    return {k: v for k, v in f.items() if v}


def _norm(field: str, v: Any) -> str:
    s = str(v or "").lower().strip()
    if field == "name":
        parts = sorted(w for w in re.split(r"[\s,]+", s) if len(w.strip(".")) > 1)
        return " ".join(parts)
    return re.sub(r"[\s\-]", "", s)


def person_key(client_id: Optional[str], name: str) -> str:
    if client_id:
        return f"client:{client_id}"
    parts = [w for w in re.split(r"[\s,]+", (name or "").lower()) if len(w.strip(".")) > 1]
    return "name:" + "|".join(sorted(parts)) if parts else ""


def value(node: Dict, field: str):
    """The fact as the lawyer sees it: a correction wins over the extracted value."""
    f = (node.get("facts") or {}).get(field)
    return f.get("value") if isinstance(f, dict) else f


# ── Build ────────────────────────────────────────────────────────────────────

def _doc_node(db: Session, tenant: str, d) -> Optional[Dict]:
    from app.services.immigration_workflow_service import (_clean_name, _name_from_filename, _name_from_pdf_form,
                                                           _name_from_text, _person_name, form_id)
    meta = d.doc_metadata or {}
    fields = ((meta.get("analyzed_data") or {}).get("fields")) or {}
    raw = d.raw_text or ""
    form = form_id(raw, d.filename or "")
    form_name = _name_from_pdf_form(d.file_path or "", form)
    ntype = node_type((d.doc_type or "").lower(), raw, fields, form if form_name else "")
    name = form_name or _person_name(fields) or _name_from_text(raw) or _name_from_filename(d.filename or "")
    if "," in (name or ""):
        last, first = [p.strip() for p in name.split(",", 1)]
        name = f"{first} {last}"
    name = _clean_name(name or "") or ""
    name = " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w for w in name.split())   # "MEHTA" → "Mehta"
    facts = extract_facts(ntype, raw, fields, name)
    return {"node_type": ntype, "document_id": str(d.id), "client_id": str(d.client_id) if d.client_id else None,
            "label": TYPE_LABEL.get(ntype) or (re.sub(r"^form_([in])(\d+)(\w*)$", lambda m: f"Form {m.group(1).upper()}-{m.group(2)}{m.group(3).upper()}", ntype)
                                               if ntype.startswith("form_") else ntype.replace("_", " ").capitalize()),
            "facts": facts, "filename": d.filename}


def rebuild(db: Session, tenant: str) -> Dict[str, int]:
    """(Re)build every immigration node, edge and check for the organisation. Lawyer decisions survive."""
    docs = db.execute(text("""
        SELECT id, filename, doc_type, raw_text, doc_metadata, file_path, client_id FROM documents
        WHERE tenant_id = :t AND status = 'completed' AND LOWER(COALESCE(doc_type,'')) = ANY(:types)"""),
        {"t": tenant, "types": list(IMMIGRATION_TYPES)}).fetchall()
    seen = set()
    for d in docs:
        n = _doc_node(db, tenant, d)
        if not n:
            continue
        pk = person_key(n["client_id"], value(n, "name") or n["facts"].get("name", ""))
        row = db.execute(text("SELECT id, facts FROM imm_nodes WHERE tenant_id = :t AND document_id = :d"),
                         {"t": tenant, "d": n["document_id"]}).fetchone()
        facts = {k: {"value": v, "extracted": v} for k, v in n["facts"].items()}
        if row:
            old = row.facts or {}
            for k, fv in old.items():                      # keep the lawyer's corrections
                if isinstance(fv, dict) and fv.get("set_by"):
                    facts[k] = {**fv, "extracted": facts.get(k, {}).get("extracted")}
            nid = row.id
            db.execute(text("""UPDATE imm_nodes SET node_type=:nt, label=:l, person_key=:pk, client_id=:c,
                               facts=CAST(:f AS jsonb), valid_from=:vf, valid_to=:vt, updated_at=now() WHERE id=:id"""),
                       {"nt": n["node_type"], "l": n["label"], "pk": pk, "c": n["client_id"], "f": json.dumps(facts),
                        "vf": _fv(facts, "valid_from"), "vt": _fv(facts, "valid_to"), "id": nid})
        else:
            nid = _id()
            db.execute(text("""INSERT INTO imm_nodes (id, tenant_id, person_key, client_id, node_type, document_id, label,
                               facts, valid_from, valid_to) VALUES (:id, :t, :pk, :c, :nt, :d, :l, CAST(:f AS jsonb), :vf, :vt)"""),
                       {"id": nid, "t": tenant, "pk": pk, "c": n["client_id"], "nt": n["node_type"], "d": n["document_id"],
                        "l": n["label"], "f": json.dumps(facts), "vf": _fv(facts, "valid_from"), "vt": _fv(facts, "valid_to")})
        seen.add(nid)
    db.commit()
    people = [r.person_key for r in db.execute(text(
        "SELECT DISTINCT person_key FROM imm_nodes WHERE tenant_id = :t AND person_key <> ''"), {"t": tenant})]
    counts = {"nodes": len(seen), "people": len(people), "edges": 0, "checks": 0}
    for pk in people:
        e, _ = _link_and_check(db, tenant, pk)
        counts["edges"] += e
    counts["family"] = propose_family(db, tenant)
    for pk in people:
        counts["checks"] += run_checks(db, tenant, pk)
    db.commit()
    return counts


def _fv(facts: Dict, k: str):
    v = (facts.get(k) or {}).get("value")
    return v or None


def _nodes(db: Session, tenant: str, pk: str) -> List[Dict]:
    return [dict(r._mapping) for r in db.execute(text(
        "SELECT * FROM imm_nodes WHERE tenant_id = :t AND person_key = :pk ORDER BY valid_to NULLS LAST"),
        {"t": tenant, "pk": pk})]


def _edge(db, tenant, a, b, etype, state, basis, conf=1.0) -> int:
    r = db.execute(text("""
        INSERT INTO imm_edges (id, tenant_id, from_node, to_node, edge_type, state, confidence, basis)
        VALUES (:id, :t, :a, :b, :e, :s, :c, :basis)
        ON CONFLICT (tenant_id, from_node, to_node, edge_type) DO NOTHING"""),
        {"id": _id(), "t": tenant, "a": a, "b": b, "e": etype, "s": state, "c": conf, "basis": basis})
    return r.rowcount or 0


def _link_and_check(db: Session, tenant: str, pk: str) -> Tuple[int, int]:
    nodes = [n for n in _nodes(db, tenant, pk) if n["node_type"] != "status_period"]
    made = 0
    # Exact keys: same receipt number, same I-94 number → the documents refer to each other
    for key, label in (("receipt", "receipt number"), ("i94_number", "I-94 number")):
        by = {}
        for n in nodes:
            v = value(n, key)
            if v:
                by.setdefault(_norm(key, v), []).append(n)
        for v, group in by.items():
            for a in group:
                for b in group:
                    if a["id"] < b["id"]:
                        made += _edge(db, tenant, a["id"], b["id"], "references", "confirmed",
                                      f"Same {label} {value(a, key)}")
    # Approvals and I-94s grant a status period (derived node)
    for n in nodes:
        if n["node_type"] in ("i797_approval", "i94", "ead", "green_card") and value(n, "valid_to"):
            kind = {"i94": "Authorized stay (I-94)", "ead": "Work authorization (EAD)",
                    "green_card": "Permanent resident"}.get(n["node_type"], f"{value(n, 'visa_class') or 'Status'} status")
            sid = _status_node(db, tenant, pk, n, kind)
            made += _edge(db, tenant, n["id"], sid, "grants", "confirmed", f"{n['label']} valid to {value(n, 'valid_to')}")
    # A newer document of the same type supersedes the older one (proposed: the lawyer confirms)
    by_type: Dict[str, List[Dict]] = {}
    for n in nodes:
        if value(n, "valid_to"):
            by_type.setdefault(n["node_type"], []).append(n)
    for group in by_type.values():
        group.sort(key=lambda n: value(n, "valid_to"))
        for older, newer in zip(group, group[1:]):
            made += _edge(db, tenant, newer["id"], older["id"], "supersedes", "proposed",
                          f"Newer {newer['label'].lower()} (to {value(newer, 'valid_to')})", 0.7)
    return made, run_checks(db, tenant, pk)


def _status_node(db, tenant, pk, src: Dict, kind: str) -> str:
    row = db.execute(text("""SELECT n.id FROM imm_nodes n JOIN imm_edges e ON e.to_node = n.id
                             WHERE e.from_node = :s AND e.edge_type = 'grants' AND n.tenant_id = :t"""),
                     {"s": src["id"], "t": tenant}).fetchone()
    facts = {"kind": {"value": kind}, "valid_from": {"value": value(src, "valid_from") or value(src, "entry_date")},
             "valid_to": {"value": value(src, "valid_to")}}
    if row:
        db.execute(text("UPDATE imm_nodes SET facts = CAST(:f AS jsonb), valid_from=:vf, valid_to=:vt, label=:l, updated_at=now() WHERE id=:id"),
                   {"f": json.dumps(facts), "vf": facts["valid_from"]["value"], "vt": facts["valid_to"]["value"], "l": kind, "id": row.id})
        return row.id
    sid = _id()
    db.execute(text("""INSERT INTO imm_nodes (id, tenant_id, person_key, client_id, node_type, label, facts, valid_from, valid_to)
                       VALUES (:id, :t, :pk, :c, 'status_period', :l, CAST(:f AS jsonb), :vf, :vt)"""),
               {"id": sid, "t": tenant, "pk": pk, "c": src.get("client_id"), "l": kind, "f": json.dumps(facts),
                "vf": facts["valid_from"]["value"], "vt": facts["valid_to"]["value"]})
    return sid


# ── Checks ───────────────────────────────────────────────────────────────────

def _fmt(d: Optional[str]) -> str:
    try:
        return date.fromisoformat(d).strftime("%-d %b %Y")
    except Exception:
        return d or "—"


def run_checks(db: Session, tenant: str, pk: str, today: Optional[date] = None) -> int:
    today = today or date.today()
    nodes = _nodes(db, tenant, pk)
    docs = [n for n in nodes if n["node_type"] != "status_period"]
    found: Dict[str, Dict] = {}

    def add(key, rule, severity, message, node_ids, details=None):
        found[f"{pk}|{key}"] = {"rule": rule, "severity": severity, "message": message,
                                "node_ids": node_ids, "details": details or {}}

    # 1. Identity: the same fact must read the same on every document
    for field, sev in IDENTITY_FIELDS.items():
        vals: Dict[str, List[Dict]] = {}
        for n in docs:
            v = value(n, field)
            if v:
                vals.setdefault(_norm(field, v), []).append(n)
        if len(vals) > 1:
            options = [{"value": value(g[0], field), "nodes": [x["id"] for x in g],
                        "documents": [x["label"] for x in g]} for g in vals.values()]
            add(f"identity:{field}", "identity_mismatch", sev,
                f"{FIELD_LABEL[field]} differs: " + " vs ".join(f"“{o['value']}” ({', '.join(o['documents'])})" for o in options),
                [i for o in options for i in o["nodes"]], {"field": field, "options": options})

    # 2. The I-94 date governs the stay; flag when it differs from the approval
    i94s = [n for n in docs if n["node_type"] == "i94" and value(n, "valid_to")]
    apps = [n for n in docs if n["node_type"] == "i797_approval" and value(n, "valid_to")]
    if i94s and apps:
        i94, app = i94s[-1], apps[-1]
        if value(i94, "valid_to") != value(app, "valid_to"):
            add("i94_vs_approval", "i94_vs_approval", "warning",
                f"I-94 admit-until {_fmt(value(i94, 'valid_to'))} differs from the approval's valid-to "
                f"{_fmt(value(app, 'valid_to'))}. The I-94 date is the one that limits the stay.",
                [i94["id"], app["id"]])

    # 3. Passport must outlast the status (admission is often limited to passport validity)
    pps = [n for n in docs if n["node_type"] == "passport" and value(n, "valid_to")]
    statuses = [n for n in nodes if n["node_type"] == "status_period" and value(n, "valid_to")]
    if pps and statuses:
        pp = max(pps, key=lambda n: value(n, "valid_to"))
        longest = max(statuses, key=lambda n: value(n, "valid_to"))
        if value(pp, "valid_to") < value(longest, "valid_to"):
            add("passport_before_status", "passport_before_status", "warning",
                f"Passport expires {_fmt(value(pp, 'valid_to'))}, before {longest['label']} ends "
                f"{_fmt(value(longest, 'valid_to'))}. Renew it; a later admission may be limited to the passport's validity.",
                [pp["id"], longest["id"]])

    # 4. Expired / expiring within 180 days (only the newest document of each type counts)
    newest: Dict[str, Dict] = {}
    for n in nodes:
        if n["node_type"] in _EXPIRY_TYPES and value(n, "valid_to"):
            k = n["label"] if n["node_type"] == "status_period" else n["node_type"]
            if k not in newest or value(n, "valid_to") > value(newest[k], "valid_to"):
                newest[k] = n
    for k, n in newest.items():
        days = (date.fromisoformat(value(n, "valid_to")) - today).days
        if days < 0:
            add(f"expired:{k}", "expired", "blocker", f"{n['label']} expired {_fmt(value(n, 'valid_to'))}.", [n["id"]])
        elif days <= 180:
            add(f"expiring:{k}", "expiring", "warning",
                f"{n['label']} expires {_fmt(value(n, 'valid_to'))} — in {days} days.", [n["id"]], {"days": days})

    # 5. What the person's open matters still need (attorney-reviewed patterns count as requirements)
    have = {n["node_type"] for n in docs}
    pats = patterns()
    for case in _cases_for(db, tenant, pk, docs):
        p = pats.get(case["case_type"]) or {}
        missing = [t for t in p.get("requires", []) if t not in have]
        if missing:
            names = ", ".join(TYPE_LABEL.get(t, t.replace("_", " ")) for t in missing)
            add(f"missing:{case['id']}", "missing_document", "warning" if p.get("reviewed") else "info",
                f"{case['label']} needs: {names}." + ("" if p.get("reviewed") else " (Suggested list — attorney review pending.)"),
                [], {"case_id": case["id"], "missing": missing})

    _family_checks(db, tenant, pk, nodes, add, today)
    return _save_checks(db, tenant, pk, found)


def _cases_for(db, tenant, pk, docs) -> List[Dict]:
    from app.services.immigration.suite import rules
    ct = rules().get("case_types", {})
    client = pk[7:] if pk.startswith("client:") else None
    names = {_norm("name", value(n, "name")) for n in docs if value(n, "name")}
    out = []
    for r in db.execute(text("SELECT id, case_type, beneficiary, client_id FROM imm_cases WHERE tenant_id = :t "
                             "AND status NOT IN ('approved','denied','withdrawn')"), {"t": tenant}):
        if (client and str(r.client_id or "") == client) or _norm("name", r.beneficiary) in names:
            out.append({"id": r.id, "case_type": r.case_type, "label": (ct.get(r.case_type) or {}).get("label", r.case_type)})
    return out


def _save_checks(db, tenant, pk, found: Dict[str, Dict]) -> int:
    existing = {r.check_key: r for r in db.execute(text(
        "SELECT id, check_key, state FROM imm_checks WHERE tenant_id = :t AND person_key = :pk"), {"t": tenant, "pk": pk})}
    for key, c in found.items():
        if key in existing:
            # a waived check stays waived; a resolved one that comes back reopens
            db.execute(text("""UPDATE imm_checks SET rule=:r, severity=:s, message=:m, node_ids=CAST(:n AS jsonb),
                               details=CAST(:d AS jsonb), state=CASE WHEN state='resolved' THEN 'open' ELSE state END,
                               updated_at=now() WHERE id=:id"""),
                       {"r": c["rule"], "s": c["severity"], "m": c["message"], "n": json.dumps(c["node_ids"]),
                        "d": json.dumps(c["details"]), "id": existing[key].id})
        else:
            db.execute(text("""INSERT INTO imm_checks (id, tenant_id, person_key, check_key, rule, severity, message, node_ids, details)
                               VALUES (:id, :t, :pk, :k, :r, :s, :m, CAST(:n AS jsonb), CAST(:d AS jsonb))"""),
                       {"id": _id(), "t": tenant, "pk": pk, "k": key, "r": c["rule"], "s": c["severity"], "m": c["message"],
                        "n": json.dumps(c["node_ids"]), "d": json.dumps(c["details"])})
    for key, r in existing.items():
        if key not in found and r.state == "open":
            db.execute(text("UPDATE imm_checks SET state='resolved', updated_at=now() WHERE id=:id"), {"id": r.id})
    return len(found)


# ── Family ───────────────────────────────────────────────────────────────────

def person_class(nodes: List[Dict]) -> str:
    """The person's current class, from the newest approval or I-94 (e.g. 'H-4')."""
    best = None
    for n in nodes:
        if n["node_type"] in ("i797_approval", "i94") and value(n, "visa_class") and value(n, "valid_to"):
            if not best or value(n, "valid_to") > value(best, "valid_to"):
                best = n
    return norm_class(value(best, "visa_class")) if best else ""


def status_until(nodes: List[Dict]) -> Optional[str]:
    """When the person's own status ends: the I-94 if there is one (it governs), else the latest approval."""
    i94 = [value(n, "valid_to") for n in nodes if n["node_type"] == "i94" and value(n, "valid_to")]
    if i94:
        return max(i94)
    st = [value(n, "valid_to") for n in nodes if n["node_type"] == "i797_approval" and value(n, "valid_to")]
    return max(st) if st else None


def family_links(db: Session, tenant: str, pk: str, states=("proposed", "confirmed")) -> List[Dict]:
    rows = db.execute(text("""SELECT * FROM imm_family WHERE tenant_id = :t AND (person_a = :pk OR person_b = :pk)
                              AND state = ANY(:st) ORDER BY created_at"""), {"t": tenant, "pk": pk, "st": list(states)})
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["other"] = d["person_b"] if d["person_a"] == pk else d["person_a"]
        out.append(d)
    return out


def _pk_for_name(db: Session, tenant: str, name: str) -> Optional[str]:
    want = _norm("name", name)
    if not want:
        return None
    for r in db.execute(text("SELECT person_key, facts FROM imm_nodes WHERE tenant_id = :t AND person_key <> '' AND facts ? 'name'"),
                        {"t": tenant}):
        if _norm("name", value({"facts": r.facts}, "name")) == want:
            return r.person_key
    return None


def _propose(db, tenant, a, b, relation, basis) -> int:
    if not a or not b or a == b:
        return 0
    r = db.execute(text("""INSERT INTO imm_family (id, tenant_id, person_a, person_b, relation, state, basis)
                           VALUES (:id, :t, :a, :b, :r, 'proposed', :basis)
                           ON CONFLICT (tenant_id, LEAST(person_a, person_b), GREATEST(person_a, person_b)) DO NOTHING"""),
                   {"id": _id(), "t": tenant, "a": a, "b": b, "r": relation, "basis": basis})
    return r.rowcount or 0


_SPOUSE_LABELS = r"Husband|Groom|Bride|Wife|Spouse(?: ?[12AB])?|Party(?: ?[12AB])?|Name of (?:Husband|Wife|Groom|Bride)"


def _form_part_names(path: str) -> Dict[str, str]:
    """Names per form part in a filled USCIS PDF ({'Pt2': 'Daniel Ramirez', 'Pt4': 'Sofia Ramirez'})."""
    out: Dict[str, Dict[str, str]] = {}
    if not path or not path.lower().endswith(".pdf"):
        return {}
    try:
        import fitz
        with fitz.open(path) as d:
            for page in d:
                for w in page.widgets() or []:
                    leaf, v = (w.field_name or "").split(".")[-1], (w.field_value or "").strip()
                    pm = re.match(r"(?:Pt|Part|P)(\d+)", leaf)
                    kind = "last" if re.search(r"_(FamilyName|LastName)\[", leaf) else "first" if re.search(r"_(GivenName|FirstName)\[", leaf) else ""
                    if v and kind and pm:
                        out.setdefault(f"Pt{pm.group(1)}", {}).setdefault(kind, v)
    except Exception:
        return {}
    return {p: f"{n['first']} {n['last']}".title() for p, n in out.items() if n.get("first") and n.get("last")}


def propose_family(db: Session, tenant: str) -> int:
    """Suggest family links from documents; the lawyer confirms. Never overrides a decision."""
    from app.services.immigration_workflow_service import _clean_name
    made = 0
    rows = db.execute(text("""SELECT n.person_key, n.node_type, d.raw_text, d.file_path FROM imm_nodes n
                              JOIN documents d ON d.id::text = n.document_id
                              WHERE n.tenant_id = :t AND n.node_type IN ('marriage_certificate', 'form_i130', 'form_i129f')"""),
                      {"t": tenant}).fetchall()
    for r in rows:
        if r.node_type == "marriage_certificate":
            word = rf"(?!(?:{_SPOUSE_LABELS}|Date|Place)\b)[A-Z][A-Za-z'\-]+"     # a name word that is not the next label
            names = [_clean_name(m.group(1)) for m in re.finditer(rf"(?:{_SPOUSE_LABELS})\s*[:\-]?\s*({word}(?: {word}){{1,3}})", r.raw_text or "")]
            keys = [k for k in dict.fromkeys(_pk_for_name(db, tenant, n) for n in names if n) if k]
            if len(keys) >= 2:
                made += _propose(db, tenant, keys[0], keys[1], "spouse", "Both named on the marriage certificate")
        else:
            parts = _form_part_names(r.file_path)
            pet, ben = (parts.get("Pt2"), parts.get("Pt4")) if r.node_type == "form_i130" else (parts.get("Pt1"), parts.get("Pt2"))
            a, b = _pk_for_name(db, tenant, pet or ""), _pk_for_name(db, tenant, ben or "")
            rel = "relative" if r.node_type == "form_i130" else "fiance"
            made += _propose(db, tenant, a, b, rel, f"Petitioner and beneficiary on {'Form I-130' if rel == 'relative' else 'Form I-129F'}")
    # A dependent status and its principal status with the same family name
    deps = dependents()
    people: Dict[str, Tuple[str, str]] = {}
    for pk in [x.person_key for x in db.execute(text("SELECT DISTINCT person_key FROM imm_nodes WHERE tenant_id=:t AND person_key <> ''"), {"t": tenant})]:
        nodes = _nodes(db, tenant, pk)
        name = next((value(n, "name") for n in nodes if value(n, "name")), "")
        people[pk] = (person_class(nodes), (name.split() or [""])[-1].lower())
    for pk, (cls, last) in people.items():
        principal = deps.get(cls)
        if not principal or not last:
            continue
        for qk, (qcls, qlast) in people.items():
            if qk != pk and qcls == principal and qlast == last:
                made += _propose(db, tenant, pk, qk, "spouse", f"{cls} and {principal} with the same family name")
    db.commit()
    return made


def link_family(db: Session, tenant: str, a: str, b: str, relation: str, reason: str, actor: str) -> Dict:
    reason = _require_reason(reason)
    if relation not in RELATIONS:
        raise ValueError(f"Relation must be one of: {', '.join(RELATIONS)}")
    if not a or not b or a == b:
        raise ValueError("Choose two different people")
    db.execute(text("""INSERT INTO imm_family (id, tenant_id, person_a, person_b, relation, state, basis, decided_by, decided_at, reason)
                       VALUES (:id, :t, :a, :b, :r, 'confirmed', 'Linked by lawyer', :by, now(), :reason)
                       ON CONFLICT (tenant_id, LEAST(person_a, person_b), GREATEST(person_a, person_b))
                       DO UPDATE SET relation = :r, state = 'confirmed', decided_by = :by, decided_at = now(), reason = :reason"""),
               {"id": _id(), "t": tenant, "a": a, "b": b, "r": relation, "by": actor, "reason": reason})
    _log(db, tenant, a, "link_family", b, None, {"relation": relation}, reason, actor)
    _log(db, tenant, b, "link_family", a, None, {"relation": relation}, reason, actor)
    run_checks(db, tenant, a); run_checks(db, tenant, b)
    db.commit()
    return person_graph(db, tenant, a)


def decide_family(db: Session, tenant: str, link_id: str, decision: str, reason: Optional[str], actor: str,
                  relation: Optional[str] = None, viewer: Optional[str] = None) -> Dict:
    row = db.execute(text("SELECT * FROM imm_family WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": link_id}).fetchone()
    if not row:
        raise LookupError("family link")
    if decision not in ("confirm", "reject"):
        raise ValueError("Decision must be confirm or reject")
    if decision == "reject":
        reason = _require_reason(reason)
    rel = relation if relation in RELATIONS else row.relation
    state = "confirmed" if decision == "confirm" else "rejected"
    db.execute(text("UPDATE imm_family SET state=:s, relation=:r, decided_by=:a, decided_at=now(), reason=:reason WHERE id=:id"),
               {"s": state, "r": rel, "a": actor, "reason": reason, "id": link_id})
    for me, other in ((row.person_a, row.person_b), (row.person_b, row.person_a)):
        _log(db, tenant, me, f"{decision}_family", other, {"state": row.state, "relation": row.relation},
             {"state": state, "relation": rel}, reason, actor)
    run_checks(db, tenant, row.person_a); run_checks(db, tenant, row.person_b)
    db.commit()
    return person_graph(db, tenant, viewer or row.person_a)


def _family_checks(db: Session, tenant: str, pk: str, nodes: List[Dict], add, today: date):
    deps = dependents()
    mine = person_class(nodes)
    my_until = status_until(nodes)
    linked = [l for l in family_links(db, tenant, pk, ("confirmed",))]
    principal_found = False
    for l in linked:
        other = _nodes(db, tenant, l["other"])
        if not other:
            continue
        oname = next((value(n, "name") for n in other if value(n, "name")), "the family member")
        ocls, ountil = person_class(other), status_until(other)
        if mine and deps.get(mine) == ocls:              # I depend on them
            principal_found = True
            if ountil and my_until and my_until > ountil:
                add(f"family:outlasts:{l['other']}", "dependent_outlasts_principal", "warning",
                    f"{mine} status runs to {_fmt(my_until)}, past {oname}'s {ocls} ({_fmt(ountil)}). "
                    f"A dependent status ends with the principal's.", [], {"other": l["other"]})
            if ountil:
                days = (date.fromisoformat(ountil) - today).days
                if days < 0:
                    add(f"family:principal_expired:{l['other']}", "principal_expired", "blocker",
                        f"{oname}'s {ocls} ended {_fmt(ountil)}. This {mine} status depends on it.", [], {"other": l["other"]})
                elif days <= 180:
                    add(f"family:principal_expiring:{l['other']}", "principal_expiring", "warning",
                        f"{oname}'s {ocls} ends {_fmt(ountil)} — in {days} days. This {mine} status depends on it; "
                        f"extend both together.", [], {"other": l["other"], "days": days})
        elif ocls and deps.get(ocls) == mine and my_until:   # they depend on me
            days = (date.fromisoformat(my_until) - today).days
            if days <= 180:
                add(f"family:dependent_affected:{l['other']}", "dependent_affected", "info",
                    f"{oname}'s {ocls} depends on this {mine} status, which ends {_fmt(my_until)}.", [], {"other": l["other"]})
    if mine in deps and not principal_found:
        add("family:no_principal", "no_principal", "info",
            f"In {mine} status, but no {deps[mine]} principal is linked. Link the family member so both are checked together.", [])
    # what the matter needs from the principal
    pats = patterns()
    for case in _cases_for(db, tenant, pk, [n for n in nodes if n["node_type"] != "status_period"]):
        need = (pats.get(case["case_type"]) or {}).get("principal") or []
        if not need:
            continue
        principals = [l["other"] for l in linked]
        have = {n["node_type"] for o in principals for n in _nodes(db, tenant, o)}
        missing = [t for t in need if t not in have]
        if missing:
            names = ", ".join(TYPE_LABEL.get(t, t.replace("_", " ")) for t in missing)
            who = "the linked principal" if principals else "a linked principal (none linked yet)"
            add(f"missing_principal:{case['id']}", "missing_principal_document", "info",
                f"{case['label']} needs from {who}: {names}. (Suggested — attorney review pending.)", [],
                {"case_id": case["id"], "missing": missing})


# ── Read ─────────────────────────────────────────────────────────────────────

def person_graph(db: Session, tenant: str, pk: str) -> Dict:
    nodes = _nodes(db, tenant, pk)
    ids = [n["id"] for n in nodes]
    edges = [dict(r._mapping) for r in db.execute(text(
        "SELECT * FROM imm_edges WHERE tenant_id = :t AND from_node = ANY(:ids) ORDER BY created_at"), {"t": tenant, "ids": ids})] if ids else []
    checks = [dict(r._mapping) for r in db.execute(text("""
        SELECT * FROM imm_checks WHERE tenant_id = :t AND person_key = :pk
        ORDER BY CASE state WHEN 'open' THEN 0 WHEN 'waived' THEN 1 ELSE 2 END,
                 CASE severity WHEN 'blocker' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, created_at"""), {"t": tenant, "pk": pk})]
    log = [dict(r._mapping) for r in db.execute(text(
        "SELECT * FROM imm_graph_log WHERE tenant_id = :t AND person_key = :pk ORDER BY at DESC LIMIT 50"), {"t": tenant, "pk": pk})]
    name = next((value(n, "name") for n in nodes if value(n, "name")), "")
    family = []
    for l in family_links(db, tenant, pk):
        other = _nodes(db, tenant, l["other"])
        family.append({**l, "name": next((value(n, "name") for n in other if value(n, "name")), ""),
                       "class": person_class(other), "status_until": status_until(other)})
    # a dependent can't stay past the principal: the earlier date is the real limit
    cls, until, limited_by = person_class(nodes), status_until(nodes), None
    principal = dependents().get(cls)
    for f in family:
        if principal and f["state"] == "confirmed" and f["class"] == principal and f["status_until"] \
                and (not until or f["status_until"] < until):
            until, limited_by = f["status_until"], f"{f['name']}'s {principal}"
    return {"person_key": pk, "name": name, "class": cls, "status_until": until, "limited_by": limited_by,
            "nodes": nodes, "edges": edges, "checks": checks, "family": family, "log": log}


def people(db: Session, tenant: str) -> List[Dict]:
    rows = db.execute(text("""
        SELECT n.person_key, MIN(n.client_id) AS client_id, COUNT(*) FILTER (WHERE n.node_type <> 'status_period') AS documents,
               (SELECT COUNT(*) FROM imm_checks c WHERE c.tenant_id = :t AND c.person_key = n.person_key AND c.state = 'open'
                  AND c.severity = 'blocker') AS blockers,
               (SELECT COUNT(*) FROM imm_checks c WHERE c.tenant_id = :t AND c.person_key = n.person_key AND c.state = 'open'
                  AND c.severity = 'warning') AS warnings
        FROM imm_nodes n WHERE n.tenant_id = :t AND n.person_key <> '' GROUP BY n.person_key"""), {"t": tenant}).fetchall()
    out = []
    for r in rows:
        nm = db.execute(text("SELECT facts FROM imm_nodes WHERE tenant_id=:t AND person_key=:pk AND facts ? 'name' LIMIT 1"),
                        {"t": tenant, "pk": r.person_key}).fetchone()
        out.append({"person_key": r.person_key, "client_id": r.client_id, "name": value({"facts": nm.facts}, "name") if nm else "",
                    "documents": r.documents, "blockers": r.blockers, "warnings": r.warnings})
    return sorted(out, key=lambda p: (-p["blockers"], -p["warnings"], p["name"] or ""))


# ── Lawyer actions ───────────────────────────────────────────────────────────

def _log(db, tenant, pk, action, target, before, after, reason, actor):
    db.execute(text("""INSERT INTO imm_graph_log (tenant_id, person_key, action, target, before, after, reason, actor)
                       VALUES (:t, :pk, :a, :tg, CAST(:b AS jsonb), CAST(:af AS jsonb), :r, :actor)"""),
               {"t": tenant, "pk": pk, "a": action, "tg": target, "b": json.dumps(before, default=str),
                "af": json.dumps(after, default=str), "r": reason, "actor": actor or "unknown"})


def _require_reason(reason: Optional[str]) -> str:
    if not reason or len(reason.strip()) < 3:
        raise ValueError("Give a short reason — it is kept in the case history")
    return reason.strip()


def correct_fact(db: Session, tenant: str, node_id: str, field: str, new_value: Any, reason: str, actor: str) -> Dict:
    reason = _require_reason(reason)
    row = db.execute(text("SELECT * FROM imm_nodes WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": node_id}).fetchone()
    if not row:
        raise LookupError("node")
    if field not in FIELD_LABEL:
        raise ValueError(f"Unknown field '{field}'")
    if field in ("dob", "valid_from", "valid_to", "entry_date") and new_value:
        d = _parse_date(str(new_value))
        if not d:
            raise ValueError("Use a date such as 2029-09-30")
        new_value = d.isoformat()
    facts = dict(row.facts or {})
    before = facts.get(field)
    facts[field] = {"value": new_value, "extracted": (before or {}).get("extracted") if isinstance(before, dict) else before,
                    "set_by": actor, "set_at": datetime.utcnow().isoformat(), "reason": reason}
    db.execute(text("UPDATE imm_nodes SET facts=CAST(:f AS jsonb), valid_from=:vf, valid_to=:vt, updated_at=now() WHERE id=:id"),
               {"f": json.dumps(facts), "vf": _fv(facts, "valid_from"), "vt": _fv(facts, "valid_to"), "id": node_id})
    _log(db, tenant, row.person_key, "correct_fact", node_id, {field: before}, {field: facts[field]}, reason, actor)
    _link_and_check(db, tenant, row.person_key)
    db.commit()
    return person_graph(db, tenant, row.person_key)


def decide_edge(db: Session, tenant: str, edge_id: str, decision: str, reason: Optional[str], actor: str,
                to_node: Optional[str] = None) -> Dict:
    row = db.execute(text("SELECT e.*, n.person_key FROM imm_edges e JOIN imm_nodes n ON n.id = e.from_node "
                          "WHERE e.tenant_id=:t AND e.id=:id"), {"t": tenant, "id": edge_id}).fetchone()
    if not row:
        raise LookupError("link")
    if decision not in ("confirm", "reject", "relink"):
        raise ValueError("Decision must be confirm, reject or relink")
    if decision != "confirm":
        reason = _require_reason(reason)
    before = {"state": row.state, "to_node": row.to_node}
    if decision == "relink":
        if not to_node:
            raise ValueError("Choose the document to link to")
        db.execute(text("UPDATE imm_edges SET state='rejected', decided_by=:a, decided_at=now(), reason=:r WHERE id=:id"),
                   {"a": actor, "r": reason, "id": edge_id})
        db.execute(text("""INSERT INTO imm_edges (id, tenant_id, from_node, to_node, edge_type, state, confidence, basis, decided_by, decided_at, reason)
                           VALUES (:id, :t, :a, :b, :e, 'confirmed', 1.0, 'Linked by lawyer', :by, now(), :r)
                           ON CONFLICT (tenant_id, from_node, to_node, edge_type) DO UPDATE SET state='confirmed', decided_by=:by, decided_at=now(), reason=:r"""),
                   {"id": _id(), "t": tenant, "a": row.from_node, "b": to_node, "e": row.edge_type, "by": actor, "r": reason})
        after = {"state": "confirmed", "to_node": to_node}
    else:
        state = "confirmed" if decision == "confirm" else "rejected"
        db.execute(text("UPDATE imm_edges SET state=:s, decided_by=:a, decided_at=now(), reason=:r WHERE id=:id"),
                   {"s": state, "a": actor, "r": reason, "id": edge_id})
        after = {"state": state}
    _log(db, tenant, row.person_key, f"{decision}_link", edge_id, before, after, reason, actor)
    db.commit()
    return person_graph(db, tenant, row.person_key)


def decide_check(db: Session, tenant: str, check_id: str, decision: str, reason: Optional[str], actor: str) -> Dict:
    row = db.execute(text("SELECT * FROM imm_checks WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": check_id}).fetchone()
    if not row:
        raise LookupError("check")
    if decision not in ("waive", "reopen"):
        raise ValueError("Decision must be waive or reopen")
    reason = _require_reason(reason) if decision == "waive" else reason
    state = "waived" if decision == "waive" else "open"
    db.execute(text("UPDATE imm_checks SET state=:s, decided_by=:a, decided_at=now(), reason=:r, updated_at=now() WHERE id=:id"),
               {"s": state, "a": actor, "r": reason, "id": check_id})
    _log(db, tenant, row.person_key, f"{decision}_check", check_id, {"state": row.state}, {"state": state}, reason, actor)
    db.commit()
    return person_graph(db, tenant, row.person_key)


def resolve_identity(db: Session, tenant: str, check_id: str, chosen: Any, reason: str, actor: str) -> Dict:
    """'Use the passport value': write the chosen value onto every document of the person that differs."""
    reason = _require_reason(reason)
    row = db.execute(text("SELECT * FROM imm_checks WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": check_id}).fetchone()
    if not row or row.rule != "identity_mismatch":
        raise LookupError("check")
    field = (row.details or {}).get("field")
    for n in _nodes(db, tenant, row.person_key):
        v = value(n, field)
        if v and _norm(field, v) != _norm(field, chosen):
            correct_fact(db, tenant, n["id"], field, chosen, reason, actor)
    return person_graph(db, tenant, row.person_key)
