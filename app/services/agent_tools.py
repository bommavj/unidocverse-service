"""
Agent Tool Registry
Thin wrappers over existing services — phi3:mini calls these by name.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import text
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _db():
    return SessionLocal()


# ── Insurance tools ───────────────────────────────────────────────────────────

def get_policy_details(policy_id: str) -> Dict:
    db = _db()
    try:
        row = db.execute(text(
            "SELECT * FROM policies WHERE id::text = :id OR policy_number = :id LIMIT 1"
        ), {"id": policy_id}).fetchone()
        if not row:
            return {"error": f"Policy {policy_id} not found"}
        return dict(row._mapping)
    finally:
        db.close()


def list_expiring_policies(days: int = 30) -> List[Dict]:
    db = _db()
    try:
        rows = db.execute(text("""
            SELECT p.id, p.policy_number, p.line_of_business, p.carrier_name,
                   p.premium_amount, p.effective_date, p.expiry_date, p.renewal_date,
                   p.status, p.renewal_status,
                   COALESCE(c.business_name, c.first_name || ' ' || c.last_name) as client_name,
                   CASE
                     WHEN p.expiry_date < NOW() THEN 'expired'
                     WHEN p.expiry_date <= NOW() + INTERVAL '1 day' * :days THEN 'expiring_soon'
                     ELSE 'active'
                   END as urgency,
                   (p.expiry_date::date - CURRENT_DATE) as days_until_expiry
            FROM policies p
            LEFT JOIN clients c ON c.id::text = p.client_id::text
            WHERE p.expiry_date IS NOT NULL
              AND p.expiry_date <= NOW() + INTERVAL '1 day' * :days
            ORDER BY p.expiry_date ASC
            LIMIT 50
        """), {"days": days}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def run_gap_analysis(policy_id: str) -> Dict:
    db = _db()
    try:
        row = db.execute(text(
            "SELECT * FROM policies WHERE id::text = :id OR policy_number = :id LIMIT 1"
        ), {"id": policy_id}).fetchone()
        if not row:
            return {"error": "Policy not found"}
        p = dict(row._mapping)
        gaps = []
        meta = p.get("metadata") or {}
        coverage = str(meta.get("coverage_types", "")).lower()
        if "umbrella" not in coverage:
            gaps.append({"type": "umbrella", "risk": "medium", "recommendation": "Add $2M umbrella policy"})
        if "cyber" not in coverage:
            gaps.append({"type": "cyber_liability", "risk": "high", "recommendation": "Add cyber liability coverage"})
        return {"policy_id": policy_id, "gaps": gaps, "gap_count": len(gaps)}
    finally:
        db.close()


def get_client(client_id: str) -> Dict:
    db = _db()
    try:
        row = db.execute(text(
            "SELECT * FROM clients WHERE id::text = :id LIMIT 1"
        ), {"id": client_id}).fetchone()
        if not row:
            return {"error": f"Client {client_id} not found"}
        return dict(row._mapping)
    finally:
        db.close()


def list_expiring_cois(days: int = 30) -> List[Dict]:
    db = _db()
    try:
        rows = db.execute(text("""
            SELECT d.id, d.filename, d.created_at,
                   d.doc_metadata->>'expiry_date' as expiry_date,
                   COALESCE(c.business_name, c.first_name || ' ' || c.last_name) as client_name,
                   CASE
                     WHEN (d.doc_metadata->>'expiry_date')::date < CURRENT_DATE THEN 'expired'
                     ELSE 'expiring_soon'
                   END as urgency,
                   ((d.doc_metadata->>'expiry_date')::date - CURRENT_DATE) as days_until_expiry
            FROM documents d
            LEFT JOIN clients c ON c.id::text = d.client_id::text
            WHERE d.doc_type IN ('coi', 'certificate_of_insurance')
              AND (
                d.doc_metadata->>'expiry_date' IS NOT NULL
                AND (d.doc_metadata->>'expiry_date')::date <= CURRENT_DATE + INTERVAL '1 day' * :days
              )
            ORDER BY (d.doc_metadata->>'expiry_date')::date ASC
            LIMIT 20
        """), {"days": days}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


# ── HR tools ──────────────────────────────────────────────────────────────────

def list_expiring_contracts(days: int = 30) -> List[Dict]:
    db = _db()
    try:
        rows = db.execute(text("""
            SELECT d.id, d.filename, d.doc_metadata, d.created_at
            FROM documents d
            WHERE d.doc_type IN ('employment_contract','contract')
              AND (d.doc_metadata->>'expiry_date') IS NOT NULL
              AND (d.doc_metadata->>'expiry_date')::date <= NOW() + INTERVAL '1 day' * :days
            LIMIT 30
        """), {"days": days}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def check_onboarding_completeness(employee_name: str) -> Dict:
    db = _db()
    try:
        rows = db.execute(text("""
            SELECT doc_type FROM documents
            WHERE raw_text ILIKE :name
              AND doc_type IN ('id_document','employment_contract','tax_form','bank_form')
        """), {"name": f"%{employee_name}%"}).fetchall()
        found = {r._mapping["doc_type"] for r in rows}
        required = {"id_document", "employment_contract", "tax_form"}
        missing = required - found
        return {"employee": employee_name, "found": list(found), "missing": list(missing), "complete": len(missing) == 0}
    finally:
        db.close()


# ── Finance tools ─────────────────────────────────────────────────────────────

def detect_duplicate_invoice(invoice_ref: str) -> Dict:
    db = _db()
    try:
        rows = db.execute(text("""
            SELECT id, filename, created_at FROM documents
            WHERE raw_text ILIKE :ref AND doc_type IN ('invoice','bill')
            ORDER BY created_at DESC LIMIT 5
        """), {"ref": f"%{invoice_ref}%"}).fetchall()
        return {"reference": invoice_ref, "matches": [dict(r._mapping) for r in rows], "is_duplicate": len(rows) > 1}
    finally:
        db.close()


def flag_anomaly(description: str, amount: float = 0, average: float = 0) -> Dict:
    sigma = round((amount - average) / max(average * 0.3, 1), 2) if average else 0
    risk = "high" if abs(sigma) > 3 else "medium" if abs(sigma) > 2 else "low"
    return {"description": description, "amount": amount, "average": average, "sigma": sigma, "risk": risk, "flagged": risk in ("high", "medium")}


# ── System / cross-sector tools ───────────────────────────────────────────────

def search_documents(query: str, limit: int = 5) -> List[Dict]:
    from app.services.document_service import DocumentService
    db = _db()
    try:
        results = DocumentService.semantic_search(db, query, limit)
        return results
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        db.close()


def create_approval_request(run_id: str, title: str, description: str, proposed_action: Dict, reasoning: str) -> Dict:
    import uuid, json
    db = _db()
    try:
        aid = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO approval_requests
                (id, doc_id, title, description, approvers, status, priority, agent_run_id, agent_reasoning, proposed_action)
            VALUES
                (:id, '', :title, :desc, '["admin"]'::jsonb, 'pending', 'high',
                 :run_id, :reasoning, CAST(:action AS jsonb))
        """), {"id": aid, "title": title, "desc": description,
               "run_id": run_id, "reasoning": reasoning,
               "action": json.dumps(proposed_action)})
        db.execute(text(
            "UPDATE agent_runs SET approval_id = :aid, status = 'waiting_approval', updated_at = NOW() WHERE id = :rid"
        ), {"aid": aid, "rid": run_id})
        db.commit()
        return {"approval_id": aid, "status": "pending"}
    finally:
        db.close()


def write_audit_log(run_id: str, entry: str) -> Dict:
    logger.info(f"[AGENT AUDIT] run={run_id} | {entry}")
    return {"logged": True}


def final_answer(summary: str, actions_taken: List[str] = None) -> Dict:
    """Signals agent loop termination."""
    return {"__final__": True, "summary": summary, "actions_taken": actions_taken or []}


# ── Registry ──────────────────────────────────────────────────────────────────

TOOL_REGISTRY: Dict[str, Any] = {
    # Insurance
    "get_policy_details":      get_policy_details,
    "list_expiring_policies":  list_expiring_policies,
    "run_gap_analysis":        run_gap_analysis,
    "get_client":              get_client,
    "list_expiring_cois":      list_expiring_cois,
    # HR
    "list_expiring_contracts":      list_expiring_contracts,
    "check_onboarding_completeness": check_onboarding_completeness,
    # Finance
    "detect_duplicate_invoice": detect_duplicate_invoice,
    "flag_anomaly":             flag_anomaly,
    # System
    "search_documents":          search_documents,
    "create_approval_request":   create_approval_request,
    "write_audit_log":           write_audit_log,
    "final_answer":              final_answer,
}

TOOL_DESCRIPTIONS = """
Available tools (call by exact name, pass args as JSON):

INSURANCE:
- get_policy_details(policy_id) → policy info, expiry, coverage, client_id
- list_expiring_policies(days=30) → list of policies expiring soon
- run_gap_analysis(policy_id) → coverage gaps and recommendations
- get_client(client_id) → client name, email, contact info
- list_expiring_cois(days=30) → certificates of insurance expiring soon

HR:
- list_expiring_contracts(days=30) → employment contracts expiring soon
- check_onboarding_completeness(employee_name) → missing onboarding docs

FINANCE:
- detect_duplicate_invoice(invoice_ref) → check if invoice is duplicate
- flag_anomaly(description, amount, average) → risk assessment for anomalous amount

SYSTEM:
- search_documents(query, limit=5) → semantic search over all documents
- create_approval_request(run_id, title, description, proposed_action, reasoning) → pause and ask human
- write_audit_log(run_id, entry) → log an audit entry
- final_answer(summary, actions_taken=[]) → CALL THIS to finish the run
"""


def call_tool(name: str, args: Dict) -> Any:
    # Normalize tool name — phi3 sometimes uppercases or adds spaces
    normalized = name.strip().lower().replace(" ", "_").replace("-", "_")
    fn = TOOL_REGISTRY.get(name) or TOOL_REGISTRY.get(normalized)
    if not fn:
        return {"error": f"Unknown tool: {name}. Available: {list(TOOL_REGISTRY.keys())}"}
    try:
        import inspect
        sig = inspect.signature(fn)
        valid_params = set(sig.parameters.keys())
        filtered = {k: v for k, v in args.items() if k in valid_params}
        return fn(**filtered)
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return {"error": str(e)}
