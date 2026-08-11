# app/services/summary_engines.py

from typing import Dict

# -----------------------------
# BANK STATEMENT
# -----------------------------
def _generate_bank_statement_summary(analyzed_data: Dict) -> str:
    deposits = analyzed_data.get("total_credits", 0)
    withdrawals = analyzed_data.get("total_debits", 0)
    net_change = analyzed_data.get("net_change", 0)
    period_start = analyzed_data.get("statement_start_date", "N/A")
    period_end = analyzed_data.get("statement_end_date", "N/A")

    return (
        f"From {period_start} to {period_end}, the account shows total deposits of "
        f"${deposits:,.2f} and withdrawals of ${withdrawals:,.2f}, resulting in a "
        f"net change of ${net_change:,.2f}."
    )


def _extract_bank_statement_key_points(analyzed_data: Dict) -> list:
    return [
        f"Total deposits: ${analyzed_data.get('total_credits', 0):,.2f}",
        f"Total withdrawals: ${analyzed_data.get('total_debits', 0):,.2f}",
        f"Net change: ${analyzed_data.get('net_change', 0):,.2f}",
        f"Transactions: {analyzed_data.get('total_transactions', 0)}",
    ]


# -----------------------------
# GENERIC FALLBACK
# -----------------------------
def _generate_generic_summary(analyzed_data: Dict) -> str:
    if not analyzed_data:
        return "This document was processed, but no structured data was extracted."

    keys = list(analyzed_data.keys())
    return (
        f"This document contains {len(keys)} structured fields. "
        f"Key extracted fields include: {', '.join(keys[:8])}."
    )


def _extract_generic_key_points(analyzed_data: Dict) -> list:
    if not analyzed_data:
        return ["No structured data extracted."]

    points = []
    for k, v in list(analyzed_data.items())[:5]:
        points.append(f"{k}: {v}")
    return points


# -----------------------------
# SUMMARY REGISTRY
# -----------------------------
SUMMARY_REGISTRY = {
    "bank_statement": {
        "summary": _generate_bank_statement_summary,
        "keypoints": _extract_bank_statement_key_points,
    },
    "generic": {
        "summary": _generate_generic_summary,
        "keypoints": _extract_generic_key_points,
    },
}
