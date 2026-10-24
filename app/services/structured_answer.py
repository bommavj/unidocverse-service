"""
Exact answers computed from structured data before any LLM planning:
pay-stub facts, then spreadsheet/CSV tables. Used by both the fast research
path and every LangGraph domain agent, so routing can't bypass it.
Returns None when neither applies — callers fall back to search.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def try_structured_answer(question: str, tenant_id: str, doc_ids: Optional[List[str]] = None) -> Optional[Dict]:
    from app.services.payroll_facts import answer_from_payroll_facts
    from app.services.tabular_query import answer_from_tables
    from app.services.tax import answer_tax_question

    for tool, fn in (("tax_rules", answer_tax_question),
                     ("query_payroll_facts", answer_from_payroll_facts),
                     ("query_table", answer_from_tables)):
        try:
            res = fn(question, tenant_id, doc_ids)
        except Exception as e:
            logger.warning(f"{tool} failed: {e} — falling back")
            continue
        if res:
            sources = res.get("sources") or ([res["source"]] if res.get("source") else [])
            return {"tool": tool, "answer": res["answer"], "sources": sources,
                    "detail": res.get("plan") or {"rows": res.get("rows")}, "chart": res.get("chart")}
    return None
