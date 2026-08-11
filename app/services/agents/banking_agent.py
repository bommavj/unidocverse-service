"""
Banking & Transactions Agent

Specializes in: bank statements, credit card statements, spending analysis,
merchant categorization, balance tracking, transfer history.

Tools:
  - query_transactions  : SQL on extracted_facts JSONB
  - search_chunks       : pgvector similarity search scoped to bank docs
  - summarize_spending  : aggregate spending by merchant/category/month
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy import text

from .base_agent import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

BANKING_DOC_TYPES = [
    "bank_statement", "credit_card_statement", "transaction_log",
]


class BankingAgent(BaseAgent):

    domain = "banking"

    system_prompt = """You are a Banking & Transactions specialist agent.
You analyze bank statements and credit card statements.
Focus on: transactions, merchant spending, balances, deposits, withdrawals, transfers.

When asked about spending, always use query_transactions first.
When asked about account terms or policies, use search_chunks.
When asked for a summary or comparison, use summarize_spending.

IMPORTANT — vague queries: if the user does not specify a time range or account,
answer based on ALL available data and begin your answer by stating the scope clearly.
Example: "Here are your top 5 merchants across all bank statements on file (all time):"
Do not ask for clarification — just answer and state the assumption.

Return JSON: {"thought": "...", "tool": "tool_name", "args": {...}}
When done: {"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def register_tools(self):
        self._tool("query_transactions", self._query_transactions)
        self._tool("search_chunks",      self._search_chunks)
        self._tool("summarize_spending", self._summarize_spending)

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _query_transactions(
        self,
        doc_ids: List[str] = None,
        merchant_filter: str = "",
        date_filter: str = "",
        limit: int = 50,
    ) -> str:
        """Query transaction rows from bank/credit card extracted_facts."""
        doc_ids = doc_ids or []
        params: Dict = {"limit": limit}

        params.update(self._tenant_params)
        doc_filter = f"AND d.tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter += f" AND d.id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        mfilter = f"AND t->>'merchant' ILIKE :merchant" if merchant_filter else ""
        if merchant_filter:
            params["merchant"] = f"%{merchant_filter}%"

        dfilter = ""
        if date_filter:
            dfilter = f"AND (t->>'date')::date >= :date_from"
            params["date_from"] = date_filter

        sql = text(f"""
            SELECT d.filename,
                   t->>'date'     AS date,
                   t->>'merchant' AS merchant,
                   (t->>'amount')::numeric AS amount,
                   t->>'type'     AS type
            FROM documents d,
                 jsonb_array_elements(d.extracted_facts->'transactions') t
            WHERE d.extracted_facts IS NOT NULL
              {doc_filter} {mfilter} {dfilter}
            ORDER BY (t->>'date')::date DESC NULLS LAST
            LIMIT :limit
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No transactions found."
            lines = ["| Date | Merchant | Amount | Type | Source |",
                     "|------|----------|--------|------|--------|"]
            total = 0.0
            for r in rows:
                amt = float(r.amount or 0)
                total += amt
                lines.append(
                    f"| {r.date} | {(r.merchant or '')[:35]} | ${amt:,.2f} | {r.type or '-'} | {r.filename} |"
                )
            lines.append(f"\n**Total: ${total:,.2f} across {len(rows)} transactions**")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[BankingAgent] query_transactions error: {e}")
            return f"Query failed: {e}"

    def _search_chunks(self, query: str, doc_ids: List[str] = None, top_k: int = 5) -> str:
        """Vector similarity search scoped to banking documents."""
        from app.services.document_react_agent import _do_execute_action
        result = _do_execute_action(
            "search_chunks",
            {"query": query, "doc_ids": doc_ids or [], "top_k": top_k,
             "doc_type_filter": BANKING_DOC_TYPES},
            self._db,
        )
        chunks = result if isinstance(result, list) else (result or {}).get("chunks", [])
        if not chunks:
            return "No relevant chunks found."
        return "\n\n".join(
            f"[{c.get('filename','')}] {c.get('chunk_text','')[:400]}"
            for c in chunks[:top_k]
        )

    def _summarize_spending(
        self,
        doc_ids: List[str] = None,
        group_by: str = "merchant",
        top_n: int = 10,
    ) -> str:
        """Aggregate spending grouped by merchant, month, or category."""
        doc_ids = doc_ids or []
        params: Dict = {"top_n": top_n}
        params.update(self._tenant_params)

        doc_filter = "AND d.tenant_id = :tenant_id"
        if doc_ids:
            placeholders = ", ".join(f":did_{i}" for i in range(len(doc_ids)))
            doc_filter = f"AND d.id IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"did_{i}"] = did

        if group_by == "month":
            group_expr = "TO_CHAR((t->>'date')::date, 'YYYY-MM')"
        elif group_by == "type":
            group_expr = "COALESCE(t->>'type', 'unknown')"
        else:
            group_expr = "COALESCE(t->>'merchant', 'Unknown')"

        sql = text(f"""
            SELECT {group_expr} AS label,
                   COUNT(*) AS cnt,
                   SUM((t->>'amount')::numeric) AS total
            FROM documents d,
                 jsonb_array_elements(d.extracted_facts->'transactions') t
            WHERE d.extracted_facts IS NOT NULL
              {doc_filter}
            GROUP BY label
            ORDER BY total DESC
            LIMIT :top_n
        """)

        try:
            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No spending data found."
            grand_total = sum(float(r.total or 0) for r in rows)
            lines = [f"**Top {top_n} by {group_by}:**\n"]
            for r in rows:
                pct = (float(r.total or 0) / grand_total * 100) if grand_total else 0
                lines.append(f"- {r.label}: ${float(r.total or 0):,.2f} ({pct:.1f}%)")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[BankingAgent] summarize_spending error: {e}")
            return f"Aggregation failed: {e}"
