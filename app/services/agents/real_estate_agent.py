"""
Real Estate & Mortgages Agent

Specializes in: property appraisals, title deeds, closing disclosures,
mortgage statements, lease agreements, loan documents.

Tools:
  - search_property_docs   : vector search on real estate documents
  - get_mortgage_summary   : loan balance, rate, payment schedule
  - get_property_details   : address, appraisal value, ownership
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

REAL_ESTATE_DOC_TYPES = [
    "real_estate", "mortgage_document", "mortgage_statement",
    "closing_disclosure", "loan_document", "payoff_statement",
    "lease", "title_deed", "appraisal_report",
]


class RealEstateAgent(BaseAgent):

    domain = "real_estate"

    system_prompt = """You are a Real Estate & Mortgage specialist agent.
You analyze property documents, mortgage statements, closing disclosures, and leases.
Focus on: property value, loan balance, interest rate, monthly payment, equity,
lease terms, closing costs, title information.

When asked about property or ownership, use get_property_details.
When asked about mortgage or loan details, use get_mortgage_summary.
When asked about specific terms or clauses, use search_property_docs.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("search_property_docs",  self._search_property_docs)
        self._tool("get_mortgage_summary",  self._get_mortgage_summary)
        self._tool("get_property_details",  self._get_property_details)

    def _search_property_docs(self, query: str, doc_ids: List[str] = None, top_k: int = 6) -> str:
        """Vector search scoped to real estate and mortgage documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": REAL_ESTATE_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant property documents found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:500]}"
            for c in chunks[:top_k]
        )

    def _get_mortgage_summary(self, doc_ids: List[str] = None) -> str:
        """Loan balance, interest rate, monthly payment, and maturity date."""
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
                   extracted_facts->>'lender'              AS lender,
                   extracted_facts->>'loan_number'         AS loan_number,
                   extracted_facts->>'original_loan'       AS original_loan,
                   extracted_facts->>'outstanding_balance' AS outstanding_balance,
                   extracted_facts->>'interest_rate'       AS interest_rate,
                   extracted_facts->>'monthly_payment'     AS monthly_payment,
                   extracted_facts->>'maturity_date'       AS maturity_date,
                   extracted_facts->>'escrow_balance'      AS escrow_balance
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{REAL_ESTATE_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No mortgage data found."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.lender:               lines.append(f"  Lender: {r.lender}")
                if r.loan_number:          lines.append(f"  Loan #: {r.loan_number}")
                if r.original_loan:        lines.append(f"  Original Loan: {r.original_loan}")
                if r.outstanding_balance:  lines.append(f"  Outstanding Balance: {r.outstanding_balance}")
                if r.interest_rate:        lines.append(f"  Interest Rate: {r.interest_rate}")
                if r.monthly_payment:      lines.append(f"  Monthly Payment: {r.monthly_payment}")
                if r.maturity_date:        lines.append(f"  Maturity Date: {r.maturity_date}")
                if r.escrow_balance:       lines.append(f"  Escrow Balance: {r.escrow_balance}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[RealEstateAgent] get_mortgage_summary error: {e}")
            return f"Mortgage query failed: {e}"

    def _get_property_details(self, doc_ids: List[str] = None) -> str:
        """Property address, appraised value, ownership, and type."""
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
                   extracted_facts->>'property_address'  AS address,
                   extracted_facts->>'appraised_value'   AS appraised_value,
                   extracted_facts->>'property_type'     AS property_type,
                   extracted_facts->>'owner_name'        AS owner_name,
                   extracted_facts->>'purchase_price'    AS purchase_price,
                   extracted_facts->>'closing_date'      AS closing_date,
                   extracted_facts->>'lot_size'          AS lot_size,
                   extracted_facts->>'sqft'              AS sqft
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{REAL_ESTATE_DOC_TYPES})
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No property details found."
            lines = []
            for r in rows:
                lines.append(f"**{r.filename}**")
                if r.address:         lines.append(f"  Address: {r.address}")
                if r.property_type:   lines.append(f"  Type: {r.property_type}")
                if r.owner_name:      lines.append(f"  Owner: {r.owner_name}")
                if r.appraised_value: lines.append(f"  Appraised Value: {r.appraised_value}")
                if r.purchase_price:  lines.append(f"  Purchase Price: {r.purchase_price}")
                if r.closing_date:    lines.append(f"  Closing Date: {r.closing_date}")
                if r.sqft:            lines.append(f"  Sq Ft: {r.sqft}")
                if r.lot_size:        lines.append(f"  Lot Size: {r.lot_size}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[RealEstateAgent] get_property_details error: {e}")
            return f"Property query failed: {e}"
