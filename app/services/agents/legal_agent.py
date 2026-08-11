"""
Legal & Contracts Agent

Specializes in: contracts, NDAs, leases, service agreements, clauses,
obligations, termination terms, governing law, liability.

Tools:
  - search_contract_terms  : vector search on legal documents
  - extract_key_clauses    : pull specific clause types from extracted_facts
  - get_contract_metadata  : parties, dates, jurisdiction, value
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

LEGAL_DOC_TYPES = [
    "contract", "legal", "agreement", "lease", "nda",
    "clm_contract", "service_agreement",
]


class LegalAgent(BaseAgent):

    domain = "legal"

    system_prompt = """You are a Legal & Contracts specialist agent.
You analyze contracts, NDAs, service agreements, and legal documents.
Focus on: key clauses, obligations, termination rights, liability, governing law, payment terms.

When asked about specific terms or clauses, use search_contract_terms.
When asked about parties, dates, or contract value, use get_contract_metadata.
When asked to extract specific clause types, use extract_key_clauses.

Always cite the source document and section when referencing legal text.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_contract_terms", self._search_contract_terms)
        self._tool("get_contract_metadata", self._get_contract_metadata)
        self._tool("extract_key_clauses",   self._extract_key_clauses)

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _search_contract_terms(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector similarity search scoped to legal and contract documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": LEGAL_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant contract text found."
        return "\n\n".join(
            f"[{c.get('filename','')} §{c.get('section_title','')}] {c.get('chunk_text','')[:600]}"
            for c in chunks[:top_k]
        )

    def _get_contract_metadata(self, doc_ids: List[str] = None) -> str:
        """Extract contract parties, dates, value, and jurisdiction."""
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
                   extracted_facts->>'parties'           AS parties,
                   extracted_facts->>'effective_date'    AS effective_date,
                   extracted_facts->>'expiration_date'   AS expiration_date,
                   extracted_facts->>'contract_value'    AS contract_value,
                   extracted_facts->>'governing_law'     AS governing_law,
                   extracted_facts->>'contract_type'     AS contract_type
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{LEGAL_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No contract metadata found."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.contract_type:   lines.append(f"  Type: {r.contract_type}")
                if r.parties:         lines.append(f"  Parties: {r.parties}")
                if r.effective_date:  lines.append(f"  Effective: {r.effective_date}")
                if r.expiration_date: lines.append(f"  Expires: {r.expiration_date}")
                if r.contract_value:  lines.append(f"  Value: {r.contract_value}")
                if r.governing_law:   lines.append(f"  Governing Law: {r.governing_law}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[LegalAgent] get_contract_metadata error: {e}")
            return f"Metadata query failed: {e}"

    def _extract_key_clauses(
        self,
        clause_type: str,
        doc_ids: List[str] = None,
    ) -> str:
        """Search for a specific clause type: termination, liability, payment, confidentiality, etc."""
        query_map = {
            "termination":     "termination clause notice period early exit",
            "liability":       "limitation of liability indemnification damages",
            "payment":         "payment terms invoice due date late fees",
            "confidentiality": "confidentiality NDA non-disclosure proprietary",
            "renewal":         "auto renewal evergreen term extension",
            "dispute":         "dispute resolution arbitration governing law jurisdiction",
        }
        query = query_map.get(clause_type.lower(), clause_type)
        return self._search_contract_terms(query=query, doc_ids=doc_ids, top_k=4)
