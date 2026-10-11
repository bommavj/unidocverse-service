"""
Tax domain answers computed from rules and extracted facts:
  - reverse audit of purchases    ("find sales tax overpaid on purchases")
  - W-2 / 1099 / K-1 vs 1040 and vendor W-9 compliance ("reconcile my 1099s with my return")
  - 1099-B vs Form 8949 / Schedule D       ("reconcile my 1099-B with my 8949")
  - K-1s vs Schedule K / capital accounts / the partner's return ("tie out the K-1s")
  - one-click return review: every check below for one tax year ("review my 2025 return")
  - tax planning: projection + priced strategies ("how can I lower my 2026 taxes?")
  - pay-stub withholding checks   ("verify / check my pay stubs")
  - tax deadlines                 ("when is my property tax due")
  - tax-year organizer            ("what tax documents am I missing for 2025")
Returns None when the question isn't one of these, so callers fall back.
"""
import re
from datetime import date
from typing import Dict, List, Optional

from app.utils.text_match import any_term

_VERIFY = ["verify", "check", "audit", "validate", "correct", "accurate", "error", "mistake", "wrong",
           "right amount", "properly", "match"]
_STUB = ["pay stub", "paystub", "payslip", "paycheck", "withholding", "withheld", "deduction", "payroll"]
_DEADLINE_RE = re.compile(r"\b(deadlines?|due dates?|tax calendar|when (is|are|do|does|should)\b.*\b(due|file|pay)|"
                          r"last day to (file|pay)|by when)\b", re.I)
_TAX_RE = re.compile(r"\b(tax|taxes|return|estimated|w-?2|1099|property|sales|payroll|941|de ?9)\b", re.I)
_ORGANIZER_RE = re.compile(r"\b(missing|checklist|organi[sz]e|what (tax )?(documents|forms)|need for (my )?tax|"
                           r"tax (documents|forms|paperwork))\b", re.I)

_TOPIC_WORDS = {
    "property": ["property"], "estimated": ["estimated", "quarterly estimate"],
    "sales": ["sales tax", "sales and use", "cdtfa", "seller"],
    "payroll": ["941", "de 9", "de9", "payroll tax"], "w2": ["w-2", "w2", "1099"],
    "income": ["income tax", "1040", "540", "tax return", "file my taxes", "filing"],
}


def _topics(question: str) -> List[str]:
    q = question.lower()
    return [t for t, words in _TOPIC_WORDS.items() if any(w in q for w in words)]


def answer_tax_question(question: str, tenant_id: str, doc_ids: Optional[List[str]] = None) -> Optional[Dict]:
    from app.services.payroll_facts import date_range_from_question

    from app.services.tax.reverse_audit import answer_reverse_audit
    reverse = answer_reverse_audit(question, tenant_id)
    if reverse:
        return reverse

    from app.services.tax.planning import answer_tax_plan
    plan = answer_tax_plan(question, tenant_id)
    if plan:
        return plan

    from app.services.tax.return_review import answer_return_review
    review = answer_return_review(question, tenant_id)
    if review:
        return review

    from app.services.tax.k1 import answer_k1
    k1 = answer_k1(question, tenant_id)
    if k1:
        return k1

    from app.services.tax.capital_gains import answer_capital_gains
    cg = answer_capital_gains(question, tenant_id)
    if cg:
        return cg

    from app.services.tax.reconcile import answer_reconciliation
    recon = answer_reconciliation(question, tenant_id)
    if recon:
        return recon

    from app.services.tax.sales_tax_audit import answer_sales_tax_audit
    audit = answer_sales_tax_audit(question, tenant_id)
    if audit:
        return audit

    if any_term(question, _VERIFY) and any_term(question, _STUB):
        from app.services.tax.paystub_audit import answer_paystub_audit
        return answer_paystub_audit(tenant_id, doc_ids, date_range_from_question(question))

    if _DEADLINE_RE.search(question) and _TAX_RE.search(question):
        return _answer_deadlines(question)

    if _ORGANIZER_RE.search(question) and re.search(r"\btax", question, re.I):
        from app.services.tax.organizer import answer_tax_organizer
        m = re.search(r"\b(20\d{2})\b", question)
        return answer_tax_organizer(tenant_id, int(m.group(1)) if m else date.today().year - 1)
    return None


def _answer_deadlines(question: str) -> Optional[Dict]:
    from app.services.tax.rules import upcoming_deadlines
    today = date.today()
    topics = _topics(question)
    items = upcoming_deadlines(today, topics or None)
    if not items:
        return None
    scope = ", ".join(topics) if topics else "all tracked"
    lines = [f"**Upcoming tax deadlines** ({scope}), from {today:%b %d, %Y}:", "",
             "| Due | Deadline | Agency | Days left |", "|---|---|---|---:|"]
    for it in items[:12]:
        label = it["name"] + (f" — tax year {it['tax_year']}" if it.get("tax_year_offset") else "")
        lines.append(f"| {it['due']:%a %b %d, %Y} | {label} | {it['authority']} | {(it['due'] - today).days} |")
    lines += ["", "_Weekend due dates are moved to the next business day; state holidays and disaster "
                  "extensions are not included — confirm with the agency._"]
    return {"answer": "\n".join(lines), "sources": [], "rows": len(items)}
