"""
Insurance Agent

Specializes in: policies, premiums, coverage limits, claims, renewals,
declarations pages, endorsements, exclusions.

Tools:
  - search_policy_terms  : vector search scoped to insurance docs
  - get_coverage_summary : extracts coverage amounts from extracted_facts
  - list_claims          : queries claim records
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

INSURANCE_DOC_TYPES = [
    "insurance_policy", "declarations_page", "insurance_claim",
    "insurance", "insurance_certificate", "eob",
]


class InsuranceAgent(BaseAgent):

    domain = "insurance"

    system_prompt = """You are an Insurance specialist agent.
You analyze insurance policies, declarations pages, and claims documents.
Focus on: coverage limits, premiums, deductibles, exclusions, renewal dates, claim status.

When asked about coverage or terms, use search_policy_terms.
When asked about amounts or limits, use get_coverage_summary.
When asked about claims, use list_claims.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_policy_terms",  self._search_policy_terms)
        self._tool("get_coverage_summary", self._get_coverage_summary)
        self._tool("list_claims",          self._list_claims)

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _search_policy_terms(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector similarity search scoped to insurance policy documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": INSURANCE_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant policy text found."
        return "\n\n".join(
            f"[{c.get('filename','')} p.{c.get('page_number','')}] {c.get('chunk_text','')[:500]}"
            for c in chunks[:top_k]
        )

    def _get_coverage_summary(self, doc_ids: List[str] = None) -> str:
        """Extract coverage limits, premiums, and deductibles from extracted_facts."""
        doc_ids = doc_ids or []
        params: Dict = {}

        params.update(self._tenant_params)
        doc_filter = "AND tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        sql = text(f"""
            SELECT filename,
                   extracted_facts->>'policy_number'    AS policy_number,
                   extracted_facts->>'insurer'          AS insurer,
                   extracted_facts->>'premium'          AS premium,
                   extracted_facts->>'deductible'       AS deductible,
                   extracted_facts->>'coverage_limit'   AS coverage_limit,
                   extracted_facts->>'effective_date'   AS effective_date,
                   extracted_facts->>'expiration_date'  AS expiration_date
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{INSURANCE_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No insurance coverage data found in extracted facts."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.insurer:        lines.append(f"  Insurer: {r.insurer}")
                if r.policy_number:  lines.append(f"  Policy #: {r.policy_number}")
                if r.premium:        lines.append(f"  Premium: {r.premium}")
                if r.deductible:     lines.append(f"  Deductible: {r.deductible}")
                if r.coverage_limit: lines.append(f"  Coverage Limit: {r.coverage_limit}")
                if r.effective_date: lines.append(f"  Effective: {r.effective_date}")
                if r.expiration_date:lines.append(f"  Expires: {r.expiration_date}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[InsuranceAgent] get_coverage_summary error: {e}")
            return f"Coverage query failed: {e}"

    def _list_claims(self, doc_ids: List[str] = None) -> str:
        """List insurance claims from extracted_facts."""
        doc_ids = doc_ids or []
        params: Dict = {}

        params.update(self._tenant_params)
        doc_filter = "AND tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        sql = text(f"""
            SELECT filename,
                   c->>'claim_number'  AS claim_number,
                   c->>'date'          AS date,
                   c->>'amount'        AS amount,
                   c->>'status'        AS status,
                   c->>'description'   AS description
            FROM documents,
                 jsonb_array_elements(extracted_facts->'claims') c
            WHERE extracted_facts IS NOT NULL
              AND extracted_facts->'claims' IS NOT NULL
              {doc_filter}
            ORDER BY (c->>'date') DESC NULLS LAST
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No claims data found."
            lines = ["| Claim # | Date | Amount | Status | Source |",
                     "|---------|------|--------|--------|--------|"]
            for r in rows:
                lines.append(
                    f"| {r.claim_number or '-'} | {r.date or '-'} | "
                    f"{r.amount or '-'} | {r.status or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[InsuranceAgent] list_claims error: {e}")
            return f"Claims query failed: {e}"
