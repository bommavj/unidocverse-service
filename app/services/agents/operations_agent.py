"""
Operations Agent

Specializes in: matters, claims, approvals, quotes, CLM pipeline,
jurisdiction documents, case management, workflow tracking.

Tools:
  - search_operations_docs  : vector search on operations documents
  - get_matters_summary     : active matters, status, jurisdiction
  - get_claims_pipeline     : claims by status, amount, handler
  - get_quotes_summary      : quotes, bind status, premium, expiry
  - get_approvals_status    : pending and completed approvals
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

OPERATIONS_DOC_TYPES = [
    "contract", "legal", "insurance_claim", "insurance_policy",
    "quote", "approval_document", "case_document", "jurisdiction_filing",
]


class OperationsAgent(BaseAgent):

    domain = "operations"

    system_prompt = """You are an Operations specialist agent.
You manage matters, claims, approvals, quotes, and CLM pipeline items.
Focus on: case status, claim amounts, quote expiry, approval workflows,
jurisdiction requirements, SLA compliance.


AVAILABLE TOOLS (only these exact names are valid):
- search_operations_docs
- get_matters_summary
- get_claims_pipeline
- get_quotes_summary
- get_approvals_status
- finish

STRICT RULES — you MUST follow these exactly:
1. Only call tools from the AVAILABLE TOOLS list above. Never call ask_user, search, or any unlisted tool name.
2. If information is not found after 2 searches, call finish with what you know.
3. Never ask the user for clarification — infer intent and proceed.

When asked about matters or cases, use get_matters_summary.
When asked about claims, use get_claims_pipeline.
When asked about quotes or renewals, use get_quotes_summary.
When asked about approvals or workflows, use get_approvals_status.
For specific document content, use search_operations_docs.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_operations_docs", self._search_operations_docs)
        self._tool("get_matters_summary",    self._get_matters_summary)
        self._tool("get_claims_pipeline",    self._get_claims_pipeline)
        self._tool("get_quotes_summary",     self._get_quotes_summary)
        self._tool("get_approvals_status",   self._get_approvals_status)

    def _search_operations_docs(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector search scoped to operations and case management documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": OPERATIONS_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant operations documents found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:500]}"
            for c in chunks[:top_k]
        )

    def _get_matters_summary(self, doc_ids: List[str] = None, status: str = "") -> str:
        """Active legal matters — case number, type, status, jurisdiction."""
        try:
            rows = self._db.execute(text("""
                SELECT id, title, matter_type, status, jurisdiction,
                       assigned_to, opened_date, closed_date
                FROM matters
                WHERE tenant_id = :tenant_id
                ORDER BY opened_date DESC
                LIMIT 20
            """), self._tenant_params).fetchall()
            if not rows:
                return "No matters found."
            lines = ["| Matter | Type | Status | Jurisdiction | Assigned To | Opened |",
                     "|--------|------|--------|-------------|-------------|--------|"]
            for r in rows:
                if status and status.lower() not in (r.status or "").lower():
                    continue
                lines.append(
                    f"| {r.title or r.id} | {r.matter_type or '-'} | "
                    f"{r.status or '-'} | {r.jurisdiction or '-'} | "
                    f"{r.assigned_to or '-'} | {r.opened_date or '-'} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[OperationsAgent] matters table may not exist: {e}")
            return self._search_operations_docs("matters cases jurisdiction status", doc_ids)

    def _get_claims_pipeline(self, doc_ids: List[str] = None, status: str = "") -> str:
        """Insurance claims by status, amount, and handler."""
        try:
            status_filter = f"AND status ILIKE :status" if status else ""
            params = {**self._tenant_params, **({"status": f"%{status}%"} if status else {})}
            rows = self._db.execute(text(f"""
                SELECT claim_number, policy_id, claimant_name, claim_type,
                       claimed_amount, status, assigned_to, filed_date, closed_date
                FROM claims
                WHERE tenant_id = :tenant_id {status_filter}
                ORDER BY filed_date DESC
                LIMIT 20
            """), params).fetchall()
            if not rows:
                return "No claims found."
            lines = ["| Claim # | Type | Claimant | Amount | Status | Handler | Filed |",
                     "|---------|------|----------|--------|--------|---------|-------|"]
            for r in rows:
                amt = f"${float(r.claimed_amount):,.2f}" if r.claimed_amount else "-"
                lines.append(
                    f"| {r.claim_number or '-'} | {r.claim_type or '-'} | "
                    f"{r.claimant_name or '-'} | {amt} | "
                    f"{r.status or '-'} | {r.assigned_to or '-'} | {r.filed_date or '-'} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[OperationsAgent] claims table error: {e}")
            return self._search_operations_docs("insurance claims filed amount status", doc_ids)

    def _get_quotes_summary(self, doc_ids: List[str] = None, status: str = "") -> str:
        """Insurance quotes — premium, bind status, expiry."""
        try:
            status_filter = f"AND status ILIKE :status" if status else ""
            params = {**self._tenant_params, **({"status": f"%{status}%"} if status else {})}
            rows = self._db.execute(text(f"""
                SELECT quote_number, client_id, coverage_type,
                       quoted_premium, status, valid_until, created_at
                FROM quotes
                WHERE tenant_id = :tenant_id {status_filter}
                ORDER BY created_at DESC
                LIMIT 20
            """), params).fetchall()
            if not rows:
                return "No quotes found."
            lines = ["| Quote # | Coverage | Premium | Status | Valid Until | Created |",
                     "|---------|----------|---------|--------|-------------|---------|"]
            for r in rows:
                prem = f"${float(r.quoted_premium):,.2f}" if r.quoted_premium else "-"
                lines.append(
                    f"| {r.quote_number or '-'} | {r.coverage_type or '-'} | "
                    f"{prem} | {r.status or '-'} | "
                    f"{r.valid_until or '-'} | {r.created_at or '-'} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[OperationsAgent] quotes table error: {e}")
            return self._search_operations_docs("quote premium coverage bind status", doc_ids)

    def _get_approvals_status(self, doc_ids: List[str] = None, status: str = "pending") -> str:
        """Pending and completed approval workflow items."""
        try:
            status_filter = f"AND status ILIKE :status" if status else ""
            params = {**self._tenant_params, **({"status": f"%{status}%"} if status else {})}
            rows = self._db.execute(text(f"""
                SELECT id, entity_type, entity_id, action_type,
                       requested_by, assigned_to, status,
                       requested_at, decided_at, notes
                FROM approvals
                WHERE tenant_id = :tenant_id {status_filter}
                ORDER BY requested_at DESC
                LIMIT 20
            """), params).fetchall()
            if not rows:
                return f"No approvals with status '{status}' found."
            lines = ["| Type | Entity | Action | Requested By | Assigned To | Status | Requested At |",
                     "|------|--------|--------|-------------|-------------|--------|-------------|"]
            for r in rows:
                lines.append(
                    f"| {r.entity_type or '-'} | {r.entity_id or '-'} | "
                    f"{r.action_type or '-'} | {r.requested_by or '-'} | "
                    f"{r.assigned_to or '-'} | {r.status or '-'} | {r.requested_at or '-'} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[OperationsAgent] approvals table error: {e}")
            return self._search_operations_docs("approval workflow pending decision", doc_ids)
