"""
Chart Engine — detects visualization intent from natural language and builds
Chart.js-ready data structures from extracted_facts JSONB.

Covers all domains: finance, payroll, medical, contracts, insurance, HR, supply chain.
"""
import re
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Visualization intent detection
# ---------------------------------------------------------------------------

_VIZ_KEYWORDS: Dict[str, List[str]] = {
    "pie":        ["pie chart", "pie graph", "donut", "breakdown", "distribution",
                   "proportion", "share", "composition", "split"],
    "line":       ["trend", "over time", "over months", "monthly trend", "timeline",
                   "history", "progress", "growth", "change over", "line graph",
                   "line chart", "over the year", "across months"],
    "horizontal": ["top ", "ranking", "rank", "highest", "most ", "largest",
                   "biggest", "leading", "leaderboard"],
    "bar":        ["bar chart", "bar graph", "column chart", "compare", "comparison",
                   "versus", " vs ", "by month", "by category", "by type",
                   "by merchant", "by vendor", "by doctor", "by provider",
                   "by counterparty", "by status", "by line"],
}

_GROUP_KEYWORDS: Dict[str, List[str]] = {
    # Finance
    "merchant":     ["by merchant", "by vendor", "by store", "by retailer",
                     "where i spent", "per merchant", "per vendor"],
    "month":        ["by month", "monthly", "per month", "over time", "over months",
                     "month by month", "each month", "across months"],
    "category":     ["by category", "by type", "categories", "per category"],
    "debit_credit": ["debit vs credit", "income vs expense", "deposits vs withdrawals"],
    # Payroll
    "deduction":    ["deductions", "withholdings", "taxes withheld", "deduction breakdown"],
    "earning_type": ["earnings breakdown", "earnings components", "income components",
                     "pay components"],
    # Medical
    "diagnosis":    ["diagnoses", "conditions", "by condition", "health conditions"],
    "provider":     ["by doctor", "by provider", "by hospital", "by clinic"],
    "drug":         ["by drug", "by medication", "prescriptions"],
    # Contract / Legal
    "counterparty": ["by party", "by counterparty", "by client", "by vendor"],
    "contract_type":["by contract type", "by agreement type"],
    "status":       ["by status", "by stage", "contract status"],
    # Insurance
    "lob":          ["by line of business", "by coverage", "by policy type", "by lob"],
    "claim_type":   ["by claim type", "claim breakdown", "type of claim"],
    # HR
    "skill":        ["skills", "by skill", "skill breakdown", "technologies"],
    "company":      ["by company", "by employer", "work history", "experience"],
    # Annual report
    "metric":       ["revenue vs", "income vs", "expenses vs", "financials",
                     "financial metrics", "kpi"],
}


def detect_viz_intent(question: str) -> Dict[str, Optional[str]]:
    """
    Detect visualization type and grouping dimension from natural language.
    Returns { viz_type: 'bar'|'pie'|'line'|'horizontal'|None, group_by: str|None }
    """
    q = question.lower()

    viz_type = None
    for vtype, keywords in _VIZ_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            viz_type = vtype
            break

    group_by = None
    for gtype, keywords in _GROUP_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            group_by = gtype
            break

    # Smart defaults when no explicit chart type but grouping detected
    if group_by and not viz_type:
        defaults = {
            "month": "line",
            "merchant": "horizontal",
            "category": "pie",
            "deduction": "pie",
            "earning_type": "pie",
            "diagnosis": "bar",
            "provider": "horizontal",
            "drug": "pie",
            "counterparty": "horizontal",
            "status": "pie",
            "lob": "pie",
            "claim_type": "pie",
            "skill": "horizontal",
            "company": "bar",
            "metric": "bar",
            "debit_credit": "pie",
        }
        viz_type = defaults.get(group_by, "bar")

    return {"viz_type": viz_type, "group_by": group_by}


# ---------------------------------------------------------------------------
# Chart palette — consistent colors across all chart types
# ---------------------------------------------------------------------------

_PALETTE = [
    "#6366f1", "#06b6d4", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
    "#3b82f6", "#a855f7", "#22c55e", "#eab308", "#f43f5e",
]


def _colors(n: int) -> List[str]:
    return [_PALETTE[i % len(_PALETTE)] for i in range(n)]


# ---------------------------------------------------------------------------
# Data builders per domain
# ---------------------------------------------------------------------------

def _build_from_transactions(rows: List[Any], group_by: str, viz_type: str) -> Dict:
    """Build chart data from transaction rows (CC + bank statements)."""
    groups: Dict[str, float] = defaultdict(float)

    for r in rows:
        if group_by == "month":
            date = str(r.date or "")
            key = date[:7] if len(date) >= 7 else date  # YYYY-MM
        elif group_by == "category":
            key = r.category or "Uncategorized"
        elif group_by == "debit_credit":
            key = r.type or "unknown"
        else:  # merchant (default)
            merchant = (r.merchant or "Unknown").split(" ")[0].title()
            key = merchant[:20]

        groups[key] += float(r.amount or 0)

    # Sort by value descending, cap at 15 for readability
    sorted_groups = sorted(groups.items(), key=lambda x: x[1], reverse=True)[:15]
    labels = [k for k, _ in sorted_groups]
    values = [round(v, 2) for _, v in sorted_groups]
    colors = _colors(len(labels))

    dataset = {
        "label": "Amount ($)",
        "data": values,
        "backgroundColor": colors if viz_type == "pie" else [c + "cc" for c in colors],
        "borderColor": colors,
        "borderWidth": 1,
        "fill": viz_type == "line",
        "tension": 0.4,
    }

    return {
        "type": "line" if viz_type == "line" else ("horizontalBar" if viz_type == "horizontal" else viz_type),
        "labels": labels,
        "datasets": [dataset],
        "options": {
            "responsive": True,
            "plugins": {
                "legend": {"display": viz_type == "pie"},
                "title": {"display": False},
            },
            "scales": {} if viz_type == "pie" else {
                "y": {"beginAtZero": True, "ticks": {"callback": "dollar"}},
            },
        },
    }


def _build_from_payroll(facts_list: List[Dict], group_by: str, viz_type: str) -> Dict:
    """Build chart from payroll/pay stub extracted facts."""
    if group_by == "deduction":
        groups: Dict[str, float] = defaultdict(float)
        for facts in facts_list:
            for d in (facts.get("deductions") or []):
                key = (d.get("type") or "Other").replace("_", " ").title()
                groups[key] += float(d.get("amount") or 0)
        labels = list(groups.keys())
        values = [round(v, 2) for v in groups.values()]
        return _simple_chart("pie", labels, values, "Deductions ($)")

    elif group_by == "earning_type":
        groups = defaultdict(float)
        for facts in facts_list:
            for e in (facts.get("earnings") or []):
                key = (e.get("type") or "Other").replace("_", " ").title()
                groups[key] += float(e.get("amount") or 0)
        labels = list(groups.keys())
        values = [round(v, 2) for v in groups.values()]
        return _simple_chart("pie", labels, values, "Earnings ($)")

    else:
        # Default: gross vs net over pay periods
        labels, gross_vals, net_vals = [], [], []
        for facts in sorted(facts_list, key=lambda f: f.get("pay_date") or ""):
            period = facts.get("pay_period") or facts.get("pay_date") or "Unknown"
            labels.append(period[:10])
            gross_vals.append(float(facts.get("gross_pay") or 0))
            net_vals.append(float(facts.get("net_pay") or 0))
        colors = _colors(2)
        return {
            "type": "line",
            "labels": labels,
            "datasets": [
                {"label": "Gross Pay", "data": gross_vals, "borderColor": colors[0],
                 "backgroundColor": colors[0] + "33", "tension": 0.4, "fill": False},
                {"label": "Net Pay", "data": net_vals, "borderColor": colors[1],
                 "backgroundColor": colors[1] + "33", "tension": 0.4, "fill": False},
            ],
            "options": {"responsive": True,
                       "scales": {"y": {"beginAtZero": True, "ticks": {"callback": "dollar"}}}},
        }


def _build_from_annual_report(facts_list: List[Dict], group_by: str) -> Dict:
    """Build chart from annual report extracted facts."""
    labels, rev_vals, exp_vals, cash_vals = [], [], [], []
    for facts in facts_list:
        fin = facts.get("financials") or {}
        year = str(facts.get("fiscal_year") or "Unknown")
        labels.append(year)
        rev_vals.append(float(fin.get("total_revenue") or 0) / 1e6)
        exp_vals.append(float(fin.get("operating_expenses") or 0) / 1e6)
        cash_vals.append(float(fin.get("cash_and_equivalents") or 0) / 1e6)

    colors = _colors(3)
    datasets = [
        {"label": "Revenue ($M)", "data": rev_vals, "backgroundColor": colors[0] + "cc",
         "borderColor": colors[0], "borderWidth": 1},
        {"label": "Expenses ($M)", "data": exp_vals, "backgroundColor": colors[1] + "cc",
         "borderColor": colors[1], "borderWidth": 1},
        {"label": "Cash ($M)", "data": cash_vals, "backgroundColor": colors[2] + "cc",
         "borderColor": colors[2], "borderWidth": 1},
    ]
    return {
        "type": "bar",
        "labels": labels,
        "datasets": datasets,
        "options": {"responsive": True,
                   "scales": {"y": {"beginAtZero": True}}},
    }


def _build_from_contracts(facts_list: List[Dict], group_by: str) -> Dict:
    """Build chart from contract extracted facts."""
    if group_by == "status":
        groups: Dict[str, int] = defaultdict(int)
        for f in facts_list:
            groups[f.get("contract_type") or "Unknown"] += 1
        return _simple_chart("pie", list(groups.keys()),
                             [float(v) for v in groups.values()], "Contracts")

    elif group_by == "counterparty":
        groups2: Dict[str, float] = defaultdict(float)
        for f in facts_list:
            for p in (f.get("parties") or []):
                groups2[p] += float(f.get("value") or 0)
        sorted_g = sorted(groups2.items(), key=lambda x: x[1], reverse=True)[:10]
        return _simple_chart("horizontalBar",
                             [k for k, _ in sorted_g],
                             [round(v, 2) for _, v in sorted_g], "Contract Value ($)")

    else:
        # Default: contract values as bar
        labels = []
        values = []
        for f in facts_list:
            parties = f.get("parties") or []
            label = parties[1] if len(parties) > 1 else "Unknown"
            labels.append(label[:20])
            values.append(float(f.get("value") or 0))
        return _simple_chart("bar", labels, values, "Contract Value ($)")


def _build_from_medical(facts_list: List[Dict], group_by: str) -> Dict:
    """Build chart from medical extracted facts."""
    if group_by == "diagnosis":
        groups: Dict[str, int] = defaultdict(int)
        for f in facts_list:
            for d in (f.get("diagnoses") or []):
                groups[d] += 1
        return _simple_chart("bar", list(groups.keys()),
                             [float(v) for v in groups.values()], "Frequency")

    elif group_by == "drug":
        groups2: Dict[str, int] = defaultdict(int)
        for f in facts_list:
            for p in (f.get("prescriptions") or []):
                groups2[p.get("drug") or "Unknown"] += 1
        return _simple_chart("pie", list(groups2.keys()),
                             [float(v) for v in groups2.values()], "Prescriptions")

    elif group_by == "provider":
        groups3: Dict[str, int] = defaultdict(int)
        for f in facts_list:
            prov = f.get("provider") or "Unknown"
            groups3[prov] += 1
        return _simple_chart("horizontalBar", list(groups3.keys()),
                             [float(v) for v in groups3.values()], "Visits")

    else:
        # Visits over time
        date_counts: Dict[str, int] = defaultdict(int)
        for f in facts_list:
            d = (f.get("visit_date") or "Unknown")[:7]
            date_counts[d] += 1
        sorted_dates = sorted(date_counts.items())
        return _simple_chart("line",
                             [k for k, _ in sorted_dates],
                             [float(v) for _, v in sorted_dates], "Visits")


def _simple_chart(chart_type: str, labels: List[str],
                  values: List[float], label: str) -> Dict:
    colors = _colors(len(labels))
    return {
        "type": chart_type,
        "labels": labels,
        "datasets": [{
            "label": label,
            "data": values,
            "backgroundColor": colors if chart_type == "pie" else [c + "cc" for c in colors],
            "borderColor": colors,
            "borderWidth": 1,
            "tension": 0.4,
            "fill": False,
        }],
        "options": {
            "responsive": True,
            "plugins": {"legend": {"display": chart_type == "pie"}},
            "scales": {} if chart_type == "pie" else {"y": {"beginAtZero": True}},
        },
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_TRANSACTION_DOC_TYPES = {"credit_card_statement", "bank_statement"}
_PAYROLL_DOC_TYPES = {"pay_stub", "payroll_report"}
_REPORT_DOC_TYPES = {"annual_report", "quarterly_report", "financial_statement"}
_CONTRACT_DOC_TYPES = {"contract", "legal"}
_MEDICAL_DOC_TYPES = {"medical", "medical_record"}


def build_chart_data(
    doc_type: str,
    group_by: Optional[str],
    viz_type: Optional[str],
    rows: Optional[List[Any]] = None,
    facts_list: Optional[List[Dict]] = None,
) -> Optional[Dict]:
    """
    Build Chart.js-ready data structure.

    For transaction docs: pass `rows` (SQL result rows).
    For all others: pass `facts_list` (extracted_facts dicts).

    Returns None if insufficient data to build a chart.
    """
    try:
        if doc_type in _TRANSACTION_DOC_TYPES:
            if not rows:
                return None
            return _build_from_transactions(rows, group_by or "merchant", viz_type or "bar")

        elif doc_type in _PAYROLL_DOC_TYPES:
            if not facts_list:
                return None
            return _build_from_payroll(facts_list, group_by or "gross_net", viz_type or "line")

        elif doc_type in _REPORT_DOC_TYPES:
            if not facts_list:
                return None
            return _build_from_annual_report(facts_list, group_by or "metric")

        elif doc_type in _CONTRACT_DOC_TYPES:
            if not facts_list:
                return None
            return _build_from_contracts(facts_list, group_by or "counterparty")

        elif doc_type in _MEDICAL_DOC_TYPES:
            if not facts_list:
                return None
            return _build_from_medical(facts_list, group_by or "month")

    except Exception as e:
        logger.warning(f"build_chart_data failed for doc_type={doc_type}: {e}")

    return None
