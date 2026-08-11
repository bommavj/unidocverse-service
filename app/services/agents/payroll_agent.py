"""
Payroll & HR Agent

Specializes in: pay stubs, W-2s, payroll reports, salary, deductions,
tax withholding, benefits, 401k contributions, FICA.

Tools:
  - get_earnings_summary  : gross/net pay, YTD totals
  - get_deductions        : all deduction line items
  - search_hr_docs        : vector search on HR/payroll docs
"""
import logging
from typing import Dict, List

from sqlalchemy import text

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

PAYROLL_DOC_TYPES = [
    "pay_stub", "payroll_report", "w2", "tax_form",
    "hr_document", "employee_roster",
]


class PayrollAgent(BaseAgent):

    domain = "payroll"

    system_prompt = """You are a Payroll & HR specialist agent.
You analyze pay stubs, W-2 forms, and payroll reports.
Focus on: gross pay, net pay, deductions, tax withholding, YTD totals, benefits.

When asked about earnings or salary, use get_earnings_summary.
When asked about deductions or withholding, use get_deductions.
When asked about HR policies or terms, use search_hr_docs.

IMPORTANT — vague queries: if the user does not specify a pay period or year,
answer based on ALL available pay stubs and state the scope at the start.
Example: "Based on all pay stubs on file (Jan–Dec 2025):"

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("get_earnings_summary", self._get_earnings_summary)
        self._tool("get_deductions",       self._get_deductions)
        self._tool("search_hr_docs",       self._search_hr_docs)

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _get_earnings_summary(self, doc_ids: List[str] = None) -> str:
        """Gross pay, net pay, and YTD totals from pay stubs and payroll reports."""
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
                   extracted_facts->>'pay_period_start'  AS period_start,
                   extracted_facts->>'pay_period_end'    AS period_end,
                   extracted_facts->>'gross_pay'         AS gross_pay,
                   extracted_facts->>'net_pay'           AS net_pay,
                   extracted_facts->>'ytd_gross'         AS ytd_gross,
                   extracted_facts->>'ytd_net'           AS ytd_net,
                   extracted_facts->>'employee_name'     AS employee_name
            FROM documents
            WHERE extracted_facts IS NOT NULL
              AND doc_type = ANY(ARRAY{PAYROLL_DOC_TYPES})
              {doc_filter}
            ORDER BY extracted_facts->>'pay_period_end' DESC NULLS LAST
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No payroll data found."
            lines = []
            for r in rows:
                period = f"{r.period_start} – {r.period_end}" if r.period_start else "Unknown period"
                lines.append(f"**{r.filename}** ({period})")
                if r.employee_name: lines.append(f"  Employee: {r.employee_name}")
                if r.gross_pay:     lines.append(f"  Gross Pay: {r.gross_pay}")
                if r.net_pay:       lines.append(f"  Net Pay: {r.net_pay}")
                if r.ytd_gross:     lines.append(f"  YTD Gross: {r.ytd_gross}")
                if r.ytd_net:       lines.append(f"  YTD Net: {r.ytd_net}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[PayrollAgent] get_earnings_summary error: {e}")
            return f"Earnings query failed: {e}"

    def _get_deductions(self, doc_ids: List[str] = None) -> str:
        """All deduction line items: taxes, insurance, 401k, other."""
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
                   d->>'name'   AS name,
                   d->>'amount' AS amount,
                   d->>'ytd'    AS ytd
            FROM documents,
                 jsonb_array_elements(extracted_facts->'deductions') d
            WHERE extracted_facts IS NOT NULL
              AND extracted_facts->'deductions' IS NOT NULL
              {doc_filter}
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No deduction data found."
            lines = ["| Deduction | Amount | YTD | Source |",
                     "|-----------|--------|-----|--------|"]
            for r in rows:
                lines.append(
                    f"| {r.name or '-'} | {r.amount or '-'} | {r.ytd or '-'} | {r.filename} |"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[PayrollAgent] get_deductions error: {e}")
            return f"Deductions query failed: {e}"

    def _search_hr_docs(self, query: str, doc_ids: List[str] = None, top_k: int = 5) -> str:
        """Vector similarity search scoped to HR and payroll documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": PAYROLL_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant HR document content found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:400]}"
            for c in chunks[:top_k]
        )
