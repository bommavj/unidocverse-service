"""
UniDocVerse MCP Server — full endpoint coverage

Every major API domain is exposed as an MCP tool so Claude Desktop, Cursor,
Zed, Continue and any other MCP client can drive the entire platform.

HITL: when hitl_enabled=True (default), write/action tools that would
modify data or trigger external comms create an approval_request instead
of executing directly, and surface in the Agent Queue for human review.

Transport: SSE at /mcp/sse  (mounted in main.py via mcp.sse_app())
"""
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from app.core.guardrails import (
    run_tool_guardrails, requires_hitl, GuardrailViolation,
    guard_input, guard_output, _audit,
    sanitize_mcp_result, guard_mcp_tool_args,
)

logger = logging.getLogger(__name__)

# Internal base URL — same process, loopback is fine
_BASE = "http://localhost:8000"

# ── Settings store ────────────────────────────────────────────────────────────
_SETTINGS_PATH = Path(__file__).parent / "mcp_settings.json"


def _load_settings() -> Dict:
    if _SETTINGS_PATH.exists():
        try:
            return json.loads(_SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {"hitl_enabled": True, "enabled_clients": []}


def save_mcp_settings(data: Dict):
    _SETTINGS_PATH.write_text(json.dumps(data, indent=2))


def hitl_enabled() -> bool:
    return _load_settings().get("hitl_enabled", True)


# ── HTTP helpers — all results pass through MCP guardrail sanitizer ───────────
# _safe() strips raw_text/embedding fields, redacts PII in known sensitive
# fields, and truncates strings >4000 chars before the result reaches Claude.

def _get(path: str, params: Dict = None, session_id: str = "") -> Any:
    try:
        r = httpx.get(f"{_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        return _safe(r.json(), session_id)
    except httpx.HTTPStatusError as e:
        return {"error": str(e), "status": e.response.status_code}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: Dict = None, session_id: str = "") -> Any:
    try:
        r = httpx.post(f"{_BASE}{path}", json=body or {}, timeout=30)
        r.raise_for_status()
        return _safe(r.json(), session_id)
    except httpx.HTTPStatusError as e:
        return {"error": str(e), "status": e.response.status_code}
    except Exception as e:
        return {"error": str(e)}


def _put(path: str, body: Dict = None, session_id: str = "") -> Any:
    try:
        r = httpx.put(f"{_BASE}{path}", json=body or {}, timeout=30)
        r.raise_for_status()
        return _safe(r.json(), session_id)
    except Exception as e:
        return {"error": str(e)}


def _patch(path: str, body: Dict = None, session_id: str = "") -> Any:
    try:
        r = httpx.patch(f"{_BASE}{path}", json=body or {}, timeout=30)
        r.raise_for_status()
        return _safe(r.json(), session_id)
    except Exception as e:
        return {"error": str(e)}


def _delete(path: str, session_id: str = "") -> Any:
    try:
        r = httpx.delete(f"{_BASE}{path}", timeout=30)
        r.raise_for_status()
        return _safe(r.json(), session_id)
    except Exception as e:
        return {"error": str(e)}


def _hitl_gate(title: str, description: str, proposed_action: Dict, reasoning: str,
               session_id: str = "") -> Dict:
    """Create an approval request and surface it in Agent Queue."""
    from app.services.agent_tools import create_approval_request, write_audit_log
    run_id = str(uuid.uuid4())
    result = create_approval_request(
        run_id=run_id, title=title, description=description,
        proposed_action=proposed_action, reasoning=reasoning,
    )
    write_audit_log(run_id, f"MCP HITL gate: {title}")
    _audit("hitl.gate_triggered", session_id,
           {"title": title, "approval_id": result.get("approval_id")})
    return {
        "hitl": True,
        "approval_id": result.get("approval_id"),
        "status": "pending_human_review",
        "message": "Action queued for human approval in Agent Queue dashboard.",
    }


def _tool_entry(tool_name: str, session_id: str = "", role: str = "agent",
                args: Dict = None) -> None:
    """
    Call at the top of every MCP tool function.
    Runs Layer 4 guardrails (role, rate limit, combo) + Layer 1 arg injection check.
    """
    try:
        run_tool_guardrails(tool_name, role=role, session_id=session_id)
        if args:
            guard_mcp_tool_args(tool_name, args, session_id)
    except GuardrailViolation as e:
        raise ValueError(f"[Guardrail:{e.rule}] {e.message}") from e


def _safe(result: Any, session_id: str = "") -> Any:
    """Sanitize any tool result before it reaches the MCP client (Claude, Cursor, etc.)."""
    return sanitize_mcp_result(result, session_id)


# ── FastMCP instance ──────────────────────────────────────────────────────────
mcp = FastMCP(
    name="UniDocVerse",
    instructions=(
        "You are connected to UniDocVerse — a full-stack document intelligence platform. "
        "Use the tools below to search documents, query clients/policies/claims/quotes, "
        "run analytics, manage approvals and agent runs, and access all verticals "
        "(Insurance, HR, Finance, Legal, Medical, Real Estate, Supply Chain, Claims). "
        "For write/action tools, prefer calling request_approval first when HITL is enabled."
    ),
)


# ════════════════════════════════════════════════════════════════════════════════
# DOCUMENTS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def search_documents(query: str, limit: int = 10) -> Any:
    """Semantic / natural-language search across all uploaded documents."""
    from app.services.agent_tools import search_documents as _s
    return _safe(_s(query, limit))


@mcp.tool()
def ask_document(doc_id: str, question: str) -> Any:
    """Ask a question about a specific document using AI."""
    return _post(f"/api/documents/{doc_id}/ask", {"question": question})


@mcp.tool()
def ask_global(question: str, context: Optional[str] = None) -> Any:
    """Ask a question across all documents (global Q&A)."""
    body: Dict = {"question": question}
    if context:
        body["context"] = context
    return _post("/api/documents/ask", body)


@mcp.tool()
def get_document(doc_id: str) -> Any:
    """Get metadata and extracted text for a document by ID."""
    return _get(f"/api/documents/{doc_id}")


@mcp.tool()
def list_documents(limit: int = 20, offset: int = 0, doc_type: Optional[str] = None) -> Any:
    """List all documents with optional type filter."""
    params: Dict = {"limit": limit, "offset": offset}
    if doc_type:
        params["doc_type"] = doc_type
    return _get("/api/documents", params)


@mcp.tool()
def get_similar_documents(doc_id: str, limit: int = 5) -> Any:
    """Find documents semantically similar to the given document."""
    return _get(f"/api/search/documents/{doc_id}/similar", {"limit": limit})


@mcp.tool()
def get_suggested_questions(doc_id: str) -> Any:
    """Get AI-suggested questions to ask about a document."""
    return _get(f"/api/search/documents/{doc_id}/suggested-questions")


@mcp.tool()
def get_document_analytics(doc_id: str) -> Any:
    """Get detailed analytics and insights extracted from a document."""
    return _get(f"/api/analytics/{doc_id}/analytics")


@mcp.tool()
def get_analytics_dashboard(doc_id: Optional[str] = None) -> Any:
    """Get the analytics dashboard — pass doc_id for document-level, omit for global."""
    if doc_id:
        return _get(f"/api/analytics/{doc_id}/dashboard")
    return _get("/api/analytics/dashboard")


@mcp.tool()
def get_analytics_summary() -> Any:
    """Get a high-level analytics summary across all documents."""
    return _get("/api/analytics/summary")


@mcp.tool()
def get_document_risk(doc_id: str) -> Any:
    """Get the risk score and risk factors for a specific document."""
    return _get(f"/api/analytics/{doc_id}/analytics")


@mcp.tool()
def list_risk_scores(limit: int = 20) -> Any:
    """List all documents with their computed risk scores."""
    return _get("/api/analytics/summary")


# ════════════════════════════════════════════════════════════════════════════════
# CLIENTS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_clients(search: Optional[str] = None, limit: int = 30) -> Any:
    """List all clients, optionally filtered by name search."""
    params: Dict = {"limit": limit}
    if search:
        params["search"] = search
    return _get("/api/clients", params)


@mcp.tool()
def get_client_profile(client_id: str) -> Any:
    """Get full profile for a client including contact info and metadata."""
    return _get(f"/api/clients/{client_id}")


@mcp.tool()
def create_client(
    first_name: str,
    last_name: str,
    email: str,
    client_type: str = "individual",
    business_name: Optional[str] = None,
    phone: Optional[str] = None,
) -> Any:
    """Create a new client record."""
    if hitl_enabled():
        return _hitl_gate(
            title=f"Create client: {first_name} {last_name}",
            description=f"New {client_type} client with email {email}",
            proposed_action={"first_name": first_name, "last_name": last_name, "email": email,
                             "client_type": client_type, "business_name": business_name, "phone": phone},
            reasoning="Creating a new client record in UniDocVerse."
        )
    return _post("/api/clients", {
        "first_name": first_name, "last_name": last_name, "email": email,
        "client_type": client_type, "business_name": business_name, "phone": phone,
    })


@mcp.tool()
def get_client_documents(client_id: str) -> Any:
    """Get all documents associated with a client."""
    return _get(f"/api/clients/{client_id}/documents")


@mcp.tool()
def get_client_notes(client_id: str) -> Any:
    """Get all notes for a client."""
    return _get(f"/api/clients/{client_id}/notes")


@mcp.tool()
def add_client_note(client_id: str, content: str, note_type: str = "general") -> Any:
    """Add a note to a client's record."""
    if hitl_enabled():
        return _hitl_gate(
            title=f"Add note to client {client_id}",
            description=content[:120],
            proposed_action={"client_id": client_id, "content": content, "note_type": note_type},
            reasoning="Adding a client note."
        )
    return _post(f"/api/clients/{client_id}/notes", {"content": content, "note_type": note_type})


# ════════════════════════════════════════════════════════════════════════════════
# INSURANCE — POLICIES
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_policies(
    status: Optional[str] = None,
    client_id: Optional[str] = None,
    limit: int = 30,
) -> Any:
    """List insurance policies with optional status/client filter."""
    params: Dict = {"limit": limit}
    if status:
        params["status"] = status
    if client_id:
        params["client_id"] = client_id
    return _get("/api/policies", params)


@mcp.tool()
def get_policy(policy_id: str) -> Any:
    """Get full details for a policy by ID or policy number."""
    from app.services.agent_tools import get_policy_details
    return _safe(get_policy_details(policy_id))


@mcp.tool()
def policy_gap_analysis(policy_id: str) -> Any:
    """Run a coverage gap analysis and get recommendations for a policy."""
    from app.services.agent_tools import run_gap_analysis
    return _safe(run_gap_analysis(policy_id))


@mcp.tool()
def list_expiring_policies(days: int = 30) -> Any:
    """List policies expiring within N days."""
    from app.services.agent_tools import list_expiring_policies as _l
    return _safe(_l(days))


@mcp.tool()
def get_renewal_pipeline() -> Any:
    """Get the full renewal pipeline with all policies in various renewal stages."""
    return _get("/api/renewals/pipeline")


@mcp.tool()
def get_upcoming_renewals(days: int = 60) -> Any:
    """Get policies with renewals coming up in the next N days."""
    return _get("/api/renewals/upcoming", {"days": days})


@mcp.tool()
def get_renewal_stats() -> Any:
    """Get renewal KPIs: on-time rate, revenue at risk, pipeline value."""
    return _get("/api/renewals/stats")


@mcp.tool()
def send_renewal_reminder(policy_id: str, message: Optional[str] = None) -> Any:
    """Send a renewal reminder to the client for a specific policy."""
    if hitl_enabled():
        return _hitl_gate(
            title=f"Send renewal reminder for policy {policy_id}",
            description=message or "Standard renewal reminder email",
            proposed_action={"policy_id": policy_id, "message": message},
            reasoning="Sending an external email communication to a client."
        )
    return _post("/api/renewals/send-reminder", {"policy_id": policy_id, "message": message})


@mcp.tool()
def list_expiring_cois(days: int = 30) -> Any:
    """List certificates of insurance expiring soon."""
    from app.services.agent_tools import list_expiring_cois as _l
    return _safe(_l(days))


# ════════════════════════════════════════════════════════════════════════════════
# INSURANCE — QUOTES
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_quotes(stage: Optional[str] = None, limit: int = 20) -> Any:
    """List all quotes, optionally filtered by stage (new/quoted/bound/lost)."""
    params: Dict = {"limit": limit}
    if stage:
        params["stage"] = stage
    return _get("/api/quotes", params)


@mcp.tool()
def get_quote(quote_id: str) -> Any:
    """Get full details for a quote including carriers and coverage."""
    return _get(f"/api/quotes/{quote_id}")


@mcp.tool()
def get_quote_stats() -> Any:
    """Get quote pipeline stats: win rate, average premium, by stage."""
    return _get("/api/quotes/stats")


@mcp.tool()
def bind_quote(quote_id: str, notes: Optional[str] = None) -> Any:
    """Bind (convert) a quote to an active policy."""
    if hitl_enabled():
        return _hitl_gate(
            title=f"Bind quote {quote_id}",
            description=notes or "Bind accepted quote to active policy",
            proposed_action={"quote_id": quote_id, "notes": notes},
            reasoning="Binding a quote creates a new active policy and triggers billing."
        )
    return _post(f"/api/quotes/{quote_id}/bind", {"notes": notes})


# ════════════════════════════════════════════════════════════════════════════════
# CLAIMS / CLM
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_claims(status: Optional[str] = None, limit: int = 20) -> Any:
    """List all claims, optionally filtered by status."""
    params: Dict = {"limit": limit}
    if status:
        params["status"] = status
    return _get("/api/claims", params)


@mcp.tool()
def get_claim(claim_id: str) -> Any:
    """Get full details for a claim."""
    return _get(f"/api/claims/{claim_id}")


@mcp.tool()
def get_claims_stats() -> Any:
    """Get claims KPIs: open count, average settlement time, denial rate."""
    return _get("/api/claims/stats")


@mcp.tool()
def list_clm_pipeline(sector: Optional[str] = None) -> Any:
    """Get the CLM (Claims Lifecycle Management) pipeline board."""
    return _get("/api/clm", {"sector": sector} if sector else {})


@mcp.tool()
def move_clm_stage(clm_id: str, stage: str) -> Any:
    """Move a CLM item to a new stage in the pipeline."""
    if hitl_enabled():
        return _hitl_gate(
            title=f"Move CLM {clm_id} to stage '{stage}'",
            description=f"Update lifecycle stage for claim {clm_id}",
            proposed_action={"clm_id": clm_id, "stage": stage},
            reasoning="Changing a claim stage may trigger notifications and SLA timers."
        )
    return _patch(f"/api/clm/{clm_id}/move", {"stage": stage})


# ════════════════════════════════════════════════════════════════════════════════
# APPROVALS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_approvals(status: Optional[str] = "pending", limit: int = 20) -> Any:
    """List approval requests, filtered by status (pending/approved/rejected)."""
    params: Dict = {}
    if status:
        params["status"] = status
    if limit:
        params["limit"] = limit
    return _get("/api/approvals", params)


@mcp.tool()
def get_approval(approval_id: str) -> Any:
    """Get details for a specific approval request including proposed action."""
    return _get(f"/api/approvals/{approval_id}")


@mcp.tool()
def get_approval_stats() -> Any:
    """Get approval queue stats: pending count, avg decision time."""
    return _get("/api/approvals/stats")


@mcp.tool()
def decide_approval(approval_id: str, decision: str, comment: Optional[str] = None) -> Any:
    """
    Approve or reject an approval request.
    decision: 'approve' | 'reject'
    """
    return _post(f"/api/approvals/{approval_id}/decide", {"decision": decision, "comment": comment})


@mcp.tool()
def request_approval(
    title: str,
    description: str,
    proposed_action: Dict,
    reasoning: str,
) -> Any:
    """
    Create an approval request in the Agent Queue for human review.
    Use before any irreversible or external action.
    """
    return _hitl_gate(title=title, description=description,
                      proposed_action=proposed_action, reasoning=reasoning)


# ════════════════════════════════════════════════════════════════════════════════
# AGENT RUNS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_agent_runs(
    sector: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> Any:
    """List agent runs with optional sector/status filter."""
    params: Dict = {"limit": limit}
    if sector:
        params["sector"] = sector
    if status:
        params["status"] = status
    return _get("/api/agents/runs", params)


@mcp.tool()
def get_agent_run(run_id: str) -> Any:
    """Get details for a specific agent run including full reasoning trail and tool calls."""
    return _get(f"/api/agents/runs/{run_id}")


@mcp.tool()
def get_agent_run_stats() -> Any:
    """Get agent run stats by status: thinking, waiting_approval, completed, failed."""
    return _get("/api/agents/runs/stats")


@mcp.tool()
def start_agent_run(
    sector: str,
    task_name: str,
    instruction: Optional[str] = None,
    approval_level: str = "external",
) -> Any:
    """
    Start a new autonomous agent run via the phi3:mini ReAct loop.
    sector: insurance | hr | finance | legal | medical | real-estate | supply-chain | clm
    approval_level: external | all | irreversible | none
    """
    return _post("/api/agents/runs", {
        "sector": sector, "task_name": task_name,
        "instruction": instruction, "approval_level": approval_level,
        "trigger_type": "mcp",
    })


@mcp.tool()
def resume_agent_run(run_id: str, decision: str, comment: Optional[str] = None) -> Any:
    """Resume a paused agent run after human decision. decision: 'approve' | 'reject'"""
    return _post(f"/api/agents/runs/{run_id}/resume", {"decision": decision, "comment": comment})


@mcp.tool()
def cancel_agent_run(run_id: str) -> Any:
    """Cancel a running or waiting agent run."""
    return _post(f"/api/agents/runs/{run_id}/cancel", {})


# ════════════════════════════════════════════════════════════════════════════════
# HR
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_expiring_contracts(days: int = 30) -> Any:
    """List employment contracts expiring within N days."""
    from app.services.agent_tools import list_expiring_contracts as _l
    return _safe(_l(days))


@mcp.tool()
def check_onboarding(employee_name: str) -> Any:
    """Check which onboarding documents are missing for an employee."""
    from app.services.agent_tools import check_onboarding_completeness
    return _safe(check_onboarding_completeness(employee_name))


# ════════════════════════════════════════════════════════════════════════════════
# FINANCE
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def detect_duplicate_invoice(invoice_ref: str) -> Any:
    """Check if an invoice reference has been processed more than once."""
    from app.services.agent_tools import detect_duplicate_invoice as _d
    return _safe(_d(invoice_ref))


@mcp.tool()
def flag_financial_anomaly(description: str, amount: float, average: float) -> Any:
    """Assess risk level for a transaction amount vs. historical average."""
    from app.services.agent_tools import flag_anomaly
    return _safe(flag_anomaly(description, amount, average))


@mcp.tool()
def run_three_way_match(invoice_id: str, po_id: str, receipt_id: str) -> Any:
    """Run a 3-way match between invoice, purchase order, and goods receipt."""
    return _post("/api/workflow/three-way-match", {
        "invoice_id": invoice_id, "po_id": po_id, "receipt_id": receipt_id,
    })


# ════════════════════════════════════════════════════════════════════════════════
# LEGAL — MATTERS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_matters(status: Optional[str] = None, limit: int = 20) -> Any:
    """List legal matters/cases, optionally filtered by status."""
    params: Dict = {"limit": limit}
    if status:
        params["status"] = status
    return _get("/api/matters", params)


@mcp.tool()
def get_matter(matter_id: str) -> Any:
    """Get full details for a legal matter including documents and notes."""
    return _get(f"/api/matters/{matter_id}")


@mcp.tool()
def add_matter_note(matter_id: str, content: str) -> Any:
    """Add a note to a legal matter."""
    return _post(f"/api/matters/{matter_id}/notes", {"content": content})


# ════════════════════════════════════════════════════════════════════════════════
# ANALYTICS / STATS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_platform_stats() -> Any:
    """Get global platform stats: total documents, types, recent activity."""
    return _get("/api/stats/summary")


@mcp.tool()
def get_activity_timeline(days: int = 30) -> Any:
    """Get document upload and processing activity over time."""
    return _get("/api/stats/activity-timeline", {"days": days})


@mcp.tool()
def get_top_document_types() -> Any:
    """Get the most common document types in the system."""
    return _get("/api/stats/top-types")


# ════════════════════════════════════════════════════════════════════════════════
# AUDIT
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_audit_log(limit: int = 50, action_type: Optional[str] = None) -> Any:
    """Get the audit log of all system actions."""
    params: Dict = {"limit": limit}
    if action_type:
        params["action_type"] = action_type
    return _get("/api/audit", params)


@mcp.tool()
def get_audit_summary() -> Any:
    """Get an audit summary: most active users, top action types."""
    return _get("/api/audit/summary")


# ════════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_notifications(unread_only: bool = False) -> Any:
    """Get system notifications, optionally only unread ones."""
    if unread_only:
        return _get("/api/notifications/unread")
    return _get("/api/notifications")


# ════════════════════════════════════════════════════════════════════════════════
# PERSONAL — NOTES & PLANNER
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_daily_briefing() -> Any:
    """Get today's AI-generated briefing: renewals due, pending approvals, open tasks."""
    return _get("/api/personal/briefing")


@mcp.tool()
def get_my_notes() -> Any:
    """Get personal notes."""
    return _get("/api/personal/notes")


@mcp.tool()
def add_personal_note(title: str, content: str) -> Any:
    """Add a personal note."""
    return _post("/api/personal/notes", {"title": title, "content": content})


@mcp.tool()
def get_planner_tasks(status: Optional[str] = None) -> Any:
    """Get personal planner tasks, optionally filtered by status."""
    params: Dict = {}
    if status:
        params["status"] = status
    return _get("/api/personal/planner", params)


@mcp.tool()
def add_planner_task(
    title: str,
    due_date: Optional[str] = None,
    priority: str = "normal",
    notes: Optional[str] = None,
) -> Any:
    """Add a task to the personal planner."""
    return _post("/api/personal/planner", {
        "title": title, "due_date": due_date, "priority": priority, "notes": notes,
    })


# ════════════════════════════════════════════════════════════════════════════════
# HITL / MCP SYSTEM
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_mcp_status() -> Dict:
    """Check MCP server status, HITL mode, and list all registered tools."""
    s = _load_settings()
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    return {
        "server": "UniDocVerse MCP",
        "hitl_enabled": s.get("hitl_enabled", True),
        "enabled_clients": s.get("enabled_clients", []),
        "tool_count": len(tool_names),
        "tools": tool_names,
    }
