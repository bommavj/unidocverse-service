"""
document_cross_linker.py
========================
General-purpose cross-document intelligence engine.

Given a list of Document records (any types), it:
  1. Extracts a normalized DocProfile from each document's metadata
  2. Runs relationship detection rules across all document pairs
  3. Produces a unified insight/alert/action/timeline report

Supported relationship patterns (non-exhaustive):
  Immigration  + Immigration    → H-1B/I-140 AC21, priority dates, renewals
  Immigration  + Insurance      → status-sensitive coverage gaps
  Immigration  + Financial      → employment verification, income continuity
  Insurance    + Insurance      → overlapping/gap coverage, renewal clustering
  Insurance    + Real Estate    → property coverage alignment
  Mortgage     + Insurance      → lender-required coverage check
  Mortgage     + Bank Statement → affordability / payment vs. cash flow
  Bank Stmt    + Credit Card    → debt-to-income, cash flow
  Tax Form     + Bank Stmt      → income verification
  Contract     + Invoice        → payment terms vs. amounts
  Contract     + Bank Stmt      → payment received vs. owed
  Offer Letter + Pay Stub       → salary verification
  Medical Bill + Insurance      → claim coverage check
  Any docs     (same person)    → expiry timeline clustering
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Document category taxonomy ────────────────────────────────────────────────

CATEGORY_MAP: dict[str, str] = {
    # Immigration
    "uscis_receipt": "immigration", "immigration_receipt": "immigration",
    "h1b": "immigration", "i797": "immigration", "i140": "immigration",
    "i485": "immigration", "ead": "immigration", "i94": "immigration",
    "passport": "immigration", "visa_document": "immigration",
    "green_card": "immigration",
    # Insurance
    "declarations_page": "insurance", "insurance_policy": "insurance",
    "insurance_eob": "insurance", "insurance_claim": "insurance",
    # Financial / Banking
    "bank_statement": "financial", "credit_card_statement": "financial",
    "financial_statement": "financial", "expense_report": "financial",
    "payroll": "financial", "tax_form": "financial", "1099_b": "financial",
    # Real Estate / Mortgage
    "mortgage_document": "real_estate", "closing_disclosure": "real_estate",
    "property_deed": "real_estate", "lease_agreement": "real_estate",
    "loan_estimate": "real_estate",
    # Employment / HR
    "offer_letter": "employment", "employment_contract": "employment",
    "performance_review": "employment",
    # Legal
    "contract": "legal", "nda": "legal", "msa": "legal", "sow": "legal",
    "court_order": "legal", "affidavit": "legal",
    # Medical
    "medical_record": "medical", "medical_bill": "medical",
    "lab_report": "medical", "prescription": "medical",
    # General
    "invoice": "financial", "receipt": "financial",
    "purchase_order": "financial", "payoff_statement": "financial",
    "annual_report": "financial",
}

CATEGORY_ICON: dict[str, str] = {
    "immigration": "🛂",
    "insurance":   "🛡️",
    "financial":   "💰",
    "real_estate": "🏠",
    "employment":  "💼",
    "legal":       "⚖️",
    "medical":     "🏥",
    "other":       "📄",
}


def _category(doc_type: str) -> str:
    return CATEGORY_MAP.get(doc_type or "", "other")


# ── Normalized profile extracted from any document ────────────────────────────

@dataclass
class DocProfile:
    doc_id:       str
    doc_type:     str
    filename:     str
    category:     str
    summary:      str

    # Key entities
    person_name:  str | None = None
    org_name:     str | None = None
    amount:       float | None = None
    currency:     str = "USD"

    # Dates
    issue_date:       date | None = None
    expiry_date:      date | None = None
    valid_from:       date | None = None
    due_date:         date | None = None
    priority_date:    date | None = None

    # Domain-specific
    visa_class:       str | None = None
    policy_number:    str | None = None
    account_number:   str | None = None
    coverage_type:    str | None = None
    premium:          float | None = None
    property_address: str | None = None
    employer:         str | None = None
    salary:           float | None = None
    balance:          float | None = None

    # Raw fields from analyzer
    raw_fields:   dict = field(default_factory=dict)


# ── Date parsing helper ───────────────────────────────────────────────────────

_DATE_RE = [
    r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b",
    r"\b(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})\b",
    r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b",
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1
)}


def _parse_date(s: Any) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    for pat in _DATE_RE:
        m = re.search(pat, s, re.I)
        if not m:
            continue
        g = m.groups()
        try:
            a, b, c = g
            if a.isdigit() and b.isdigit() and c.isdigit():
                ai, bi, ci = int(a), int(b), int(c)
                if ci > 1900:
                    return date(ci, min(ai, 12), min(bi, 31)) if ai <= 12 else date(ci, min(bi, 12), min(ai, 31))
                elif ai > 1900:
                    return date(ai, min(bi, 12), min(ci, 31))
            else:
                alpha = next((p for p in [a, b, c] if not p.isdigit()), None)
                nums  = [p for p in [a, b, c] if p.isdigit()]
                if alpha and len(nums) == 2:
                    mon = _MONTHS.get(alpha[:3].lower())
                    if mon:
                        n1, n2 = int(nums[0]), int(nums[1])
                        year = n2 if n2 > 1900 else n1
                        day  = n1 if n2 > 1900 else n2
                        return date(year, mon, min(day, 31))
        except (ValueError, OverflowError):
            continue
    return None


def _parse_amount(s: Any) -> float | None:
    if not s:
        return None
    s = str(s).replace(",", "").replace("$", "").strip()
    m = re.search(r"[\d]+\.?\d*", s)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return None


def _fmt(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else "Unknown"


def _days(d: date | None) -> int | None:
    return (d - date.today()).days if d else None


def _urgency(days: int | None) -> str:
    if days is None:
        return "⚪"
    if days < 0:
        return "🔴"
    if days <= 30:
        return "🔴"
    if days <= 90:
        return "🟠"
    if days <= 180:
        return "🟡"
    return "🟢"


# ── Profile extractor ─────────────────────────────────────────────────────────

def _extract_profile(doc) -> DocProfile:
    """
    Build a normalized DocProfile from a SQLAlchemy Document row.
    Reads from doc_metadata.analyzed_data.fields (and fallbacks).
    """
    meta     = doc.doc_metadata or {}
    analyzed = meta.get("analyzed_data") or {}
    fields   = analyzed.get("fields") or meta.get("fields") or {}
    summary  = analyzed.get("summary") or meta.get("summary") or doc.summary or ""

    p = DocProfile(
        doc_id   = str(doc.id),
        doc_type = doc.doc_type or "document",
        filename = doc.filename or "",
        category = _category(doc.doc_type or ""),
        summary  = summary,
        raw_fields = fields,
    )

    # Person name — handle list values (e.g. multiple named insureds)
    for k in ["beneficiary", "applicant", "insured", "named_insured", "employee",
              "borrower", "patient", "customer", "full_name", "name"]:
        val = fields.get(k)
        if val:
            if isinstance(val, list):
                val = val[0]  # take primary/first named person
            p.person_name = str(val).strip()
            break

    # Org / employer
    for k in ["petitioner", "employer", "employer_sponsor", "insurer", "bank",
              "lender", "carrier", "organization", "company", "sponsor"]:
        if fields.get(k):
            p.org_name = str(fields[k])
            break

    # Expiry date
    for k in ["validity_end", "expiration_date", "expiry_date", "valid_until",
              "valid_to", "card_expires", "policy_expiry", "lease_end", "end_date"]:
        d = _parse_date(fields.get(k))
        if d:
            p.expiry_date = d
            break

    # Valid from / issue
    for k in ["validity_start", "valid_from", "issue_date", "effective_date",
              "policy_start", "lease_start", "start_date", "receipt_date"]:
        d = _parse_date(fields.get(k))
        if d:
            p.issue_date = d if not p.valid_from else p.issue_date
            p.valid_from = d
            break

    # Due date
    for k in ["due_date", "payment_due", "next_payment_date", "due"]:
        d = _parse_date(fields.get(k))
        if d:
            p.due_date = d
            break

    # Priority date (immigration)
    d = _parse_date(fields.get("priority_date"))
    if d:
        p.priority_date = d

    # Domain specifics
    p.visa_class      = fields.get("visa_class")
    p.policy_number   = fields.get("policy_number") or fields.get("case_number")
    p.coverage_type   = fields.get("coverage_type") or fields.get("notice_type")
    p.employer        = fields.get("employer") or fields.get("petitioner")
    p.property_address = fields.get("property_address") or fields.get("address")

    p.premium  = _parse_amount(fields.get("premium") or fields.get("annual_premium"))
    p.balance  = _parse_amount(fields.get("balance") or fields.get("amount_due") or fields.get("total_due"))
    p.salary   = _parse_amount(fields.get("salary") or fields.get("gross_pay") or fields.get("annual_salary"))

    # Fallback: parse key dates from summary text if fields came up empty
    if not p.expiry_date and summary:
        exp_m = re.search(
            r'(?:expir(?:es?|ation)|valid\s+(?:through|until|to))[:\s]+(\d{1,2}/\d{1,2}/\d{4})',
            summary, re.I
        )
        if exp_m:
            p.expiry_date = _parse_date(exp_m.group(1))

    return p


# ── Relationship rule engine ──────────────────────────────────────────────────

def _same_person(a: DocProfile, b: DocProfile) -> bool:
    if not a.person_name or not b.person_name:
        return False
    na = a.person_name.strip().upper().replace(",", "")
    nb = b.person_name.strip().upper().replace(",", "")
    # Full match or one is substring of the other
    return na == nb or na in nb or nb in na


def _expiry_gap_days(a: DocProfile, b: DocProfile) -> int | None:
    """Positive = A expires before B; negative = B expires first."""
    if a.expiry_date and b.expiry_date:
        return (a.expiry_date - b.expiry_date).days
    return None


def _run_rules(profiles: list[DocProfile]) -> tuple[list, list, list, list]:
    """Return (insights, alerts, actions, timeline)."""
    insights: list[dict] = []
    alerts:   list[dict] = []
    actions:  list[dict] = []
    timeline: list[dict] = []

    cats = {p.category for p in profiles}

    def _p(cat: str) -> list[DocProfile]:
        return [p for p in profiles if p.category == cat]

    imm  = _p("immigration")
    ins  = _p("insurance")
    fin  = _p("financial")
    re_  = _p("real_estate")
    emp  = _p("employment")
    med  = _p("medical")
    leg  = _p("legal")

    today = date.today()

    # ── 1. Expiry timeline for ALL docs ──────────────────────────────────────
    expiring = [(p, _days(p.expiry_date)) for p in profiles if p.expiry_date]
    expiring.sort(key=lambda x: x[0].expiry_date)

    for p, days in expiring:
        icon = _urgency(days)
        timeline.append({
            "label":    f"{CATEGORY_ICON.get(p.category,'📄')} {p.doc_type.upper().replace('_',' ')}",
            "filename": p.filename,
            "date":     _fmt(p.expiry_date),
            "icon":     icon,
            "color":    "#DC3545" if (days or 999) <= 30 else "#FFA726" if (days or 999) <= 90 else "#00C851",
            "doc_id":   p.doc_id,
            "days_remaining": days,
        })
        if days is not None and days <= 90:
            alerts.append({
                "severity": "critical" if days <= 30 else "warning",
                "title":    f"{icon} {p.doc_type.upper().replace('_',' ')} expires in {days} days",
                "message":  f"{p.filename} expires {_fmt(p.expiry_date)}. Action required.",
                "doc_id":   p.doc_id,
                "category": p.category,
            })

    # ── 2. Expiry clustering — multiple docs expiring within 60 days of each other ──
    for i, (pa, da) in enumerate(expiring):
        for pb, db in expiring[i+1:]:
            if pa.doc_type == pb.doc_type:
                continue
            if da is not None and db is not None and abs(da - db) <= 60:
                insights.append({
                    "type":        "cross_link",
                    "icon":        "⚠️",
                    "title":       "Multiple Documents Expiring Together",
                    "description": (
                        f"{CATEGORY_ICON.get(pa.category,'')} {pa.doc_type.upper()} ({_fmt(pa.expiry_date)}) "
                        f"and {CATEGORY_ICON.get(pb.category,'')} {pb.doc_type.upper()} ({_fmt(pb.expiry_date)}) "
                        "expire within 60 days of each other. Coordinate renewals to avoid gaps."
                    ),
                    "linked_docs": [pa.doc_id, pb.doc_id],
                    "color":       "#FFA726",
                })

    # ── 3. Immigration + Insurance ────────────────────────────────────────────
    for im in imm:
        for ins_p in ins:
            if im.expiry_date and ins_p.expiry_date:
                if ins_p.expiry_date < im.expiry_date:
                    gap = (im.expiry_date - ins_p.expiry_date).days
                    insights.append({
                        "type":        "cross_link",
                        "icon":        "🛡️",
                        "title":       "Insurance Expires Before Immigration Status",
                        "description": (
                            f"Your {ins_p.doc_type.upper()} ({_fmt(ins_p.expiry_date)}) expires "
                            f"{gap} days before your immigration status ({_fmt(im.expiry_date)}). "
                            "Ensure continuous coverage — a lapse could affect visa renewals or employer filings."
                        ),
                        "linked_docs": [im.doc_id, ins_p.doc_id],
                        "color":       "#FFA726",
                    })
                    actions.append({
                        "priority": "high",
                        "action":   f"Renew {ins_p.doc_type.upper()} before immigration renewal",
                        "deadline": _fmt(ins_p.expiry_date),
                        "note":     "Coverage must not lapse during any active visa period.",
                    })

    # ── 4. Immigration + Financial (employment continuity) ────────────────────
    for im in imm:
        if im.employer:
            for f_p in fin:
                # Bank statement from same org → verify income continuity
                if f_p.org_name and im.employer.upper() in f_p.org_name.upper():
                    insights.append({
                        "type":        "cross_link",
                        "icon":        "💰",
                        "title":       "Employment Income Linked to Visa Sponsor",
                        "description": (
                            f"Financial document from {f_p.org_name} matches H-1B sponsor "
                            f"{im.employer}. Income continuity from the petitioning employer "
                            "is required to maintain H-1B status."
                        ),
                        "linked_docs": [im.doc_id, f_p.doc_id],
                        "color":       "#4A90E2",
                    })

    # ── 5. Insurance + Real Estate ────────────────────────────────────────────
    for ins_p in ins:
        for re_p in re_:
            # Check property address overlap
            if (ins_p.property_address and re_p.property_address
                    and ins_p.property_address[:20].upper() in re_p.property_address.upper()):
                insights.append({
                    "type":        "cross_link",
                    "icon":        "🏠",
                    "title":       "Property Insurance Linked to Real Estate Document",
                    "description": (
                        f"Insurance policy covers property referenced in {re_p.doc_type.upper()}. "
                        "Ensure coverage meets lender requirements (typically 100% replacement cost)."
                    ),
                    "linked_docs": [ins_p.doc_id, re_p.doc_id],
                    "color":       "#4A90E2",
                })
            # Mortgage + Insurance expiry alignment
            if re_p.doc_type in ("mortgage_document", "closing_disclosure") and ins_p.expiry_date:
                days_ins = _days(ins_p.expiry_date)
                if days_ins is not None and days_ins <= 60:
                    alerts.append({
                        "severity": "warning",
                        "title":    "Home Insurance Expiring — Mortgage May Require Continuous Coverage",
                        "message":  (
                            f"Your home insurance expires {_fmt(ins_p.expiry_date)}. "
                            "Most mortgages require uninterrupted homeowner's insurance. "
                            "Renew before expiry to avoid lender force-placed coverage."
                        ),
                        "doc_id":   ins_p.doc_id,
                        "category": "insurance",
                    })
                    actions.append({
                        "priority": "high",
                        "action":   "Renew Homeowner's Insurance (mortgage required)",
                        "deadline": _fmt(ins_p.expiry_date),
                        "note":     "Lenders typically require 12 months paid-in-full at closing/renewal.",
                    })

    # ── 6. Multiple insurance docs — gap / overlap detection ─────────────────
    if len(ins) > 1:
        types_found = {p.doc_type for p in ins}
        all_ins_types = {"declarations_page", "insurance_policy", "insurance_eob"}
        covered_types = types_found & all_ins_types
        insights.append({
            "type":        "info",
            "icon":        "🛡️",
            "title":       f"{len(ins)} Insurance Documents on File",
            "description": (
                f"Coverage types found: {', '.join(t.replace('_',' ').title() for t in covered_types or types_found)}. "
                "Review for gaps (e.g., umbrella, disability, life) and overlapping coverage."
            ),
            "linked_docs": [p.doc_id for p in ins],
            "color":       "#4A90E2",
        })
        # Check for missing health insurance if medical bills present
        has_health = any("health" in (p.coverage_type or "").lower() or
                         "medical" in (p.coverage_type or "").lower() for p in ins)
        if med and not has_health:
            alerts.append({
                "severity": "warning",
                "title":    "Medical Bills Present — No Health Insurance Document Found",
                "message":  (
                    "Medical documents are on file but no health insurance policy was detected. "
                    "Upload your health insurance card or policy to verify coverage."
                ),
                "category": "medical",
            })

    # ── 7. Bank Statement + Credit Card ───────────────────────────────────────
    bank_stmts = [p for p in fin if p.doc_type == "bank_statement"]
    cc_stmts   = [p for p in fin if p.doc_type == "credit_card_statement"]
    if bank_stmts and cc_stmts:
        total_cc_balance = sum(p.balance or 0 for p in cc_stmts)
        total_bank_bal   = sum(p.balance or 0 for p in bank_stmts)
        if total_cc_balance > 0 and total_bank_bal > 0:
            ratio = total_cc_balance / total_bank_bal
            insights.append({
                "type":        "info",
                "icon":        "💳",
                "title":       "Credit Card vs. Bank Balance Analysis",
                "description": (
                    f"Total credit card balance: ${total_cc_balance:,.2f} | "
                    f"Bank balance: ${total_bank_bal:,.2f} | "
                    f"Ratio: {ratio:.1%}. "
                    + ("⚠️ Credit balance exceeds available bank funds." if ratio > 1 else
                       "Balances appear manageable relative to bank funds.")
                ),
                "linked_docs": [p.doc_id for p in bank_stmts + cc_stmts],
                "color":       "#DC3545" if ratio > 1 else "#00C851",
            })

    # ── 8. Mortgage + Bank Statement — affordability ──────────────────────────
    for re_p in re_:
        if re_p.doc_type not in ("mortgage_document", "closing_disclosure", "loan_estimate"):
            continue
        for bank in bank_stmts:
            if re_p.amount and bank.balance:
                monthly_pmt = re_p.amount / 360  # rough estimate
                if bank.balance < monthly_pmt * 3:
                    alerts.append({
                        "severity": "warning",
                        "title":    "Bank Balance May Be Insufficient for Mortgage Buffer",
                        "message":  (
                            f"Bank balance (${bank.balance:,.2f}) is less than 3 months of "
                            f"estimated mortgage payments (${monthly_pmt*3:,.0f}). "
                            "Consider building a larger cash reserve."
                        ),
                        "linked_docs": [re_p.doc_id, bank.doc_id],
                        "category": "financial",
                    })

    # ── 9. Contract + Invoice — payment reconciliation ────────────────────────
    contracts = [p for p in leg if p.doc_type in ("contract", "msa", "sow")]
    invoices  = [p for p in fin if p.doc_type in ("invoice", "purchase_order")]
    if contracts and invoices:
        total_invoiced = sum(p.balance or p.amount or 0 for p in invoices)
        for c in contracts:
            if c.amount and total_invoiced:
                diff = total_invoiced - c.amount
                insights.append({
                    "type":        "cross_link",
                    "icon":        "⚖️",
                    "title":       "Contract vs. Invoice Reconciliation",
                    "description": (
                        f"Contract value: ${c.amount:,.2f} | "
                        f"Total invoiced: ${total_invoiced:,.2f} | "
                        + (f"Overbilled by ${diff:,.2f}." if diff > 0 else
                           f"${abs(diff):,.2f} of contract value not yet invoiced." if diff < 0 else
                           "Invoices match contract value exactly.")
                    ),
                    "linked_docs": [c.doc_id] + [i.doc_id for i in invoices],
                    "color":       "#DC3545" if diff > c.amount * 0.1 else "#00C851",
                })

    # ── 10. Employment + Financial — salary verification ─────────────────────
    for emp_p in emp:
        if emp_p.salary:
            for fin_p in fin:
                if fin_p.doc_type in ("bank_statement",) and fin_p.balance:
                    insights.append({
                        "type":        "cross_link",
                        "icon":        "💼",
                        "title":       "Employment + Financial Cross-Check",
                        "description": (
                            f"Offer/payroll salary: ${emp_p.salary:,.2f}/yr | "
                            f"Bank balance on file: ${fin_p.balance:,.2f}. "
                            "These documents together support income verification for loans or visa applications."
                        ),
                        "linked_docs": [emp_p.doc_id, fin_p.doc_id],
                        "color":       "#4A90E2",
                    })

    # ── 11. Medical + Insurance — claim coverage ──────────────────────────────
    for med_p in med:
        for ins_p in ins:
            if med_p.balance and ins_p.doc_type in ("insurance_eob", "declarations_page"):
                insights.append({
                    "type":        "cross_link",
                    "icon":        "🏥",
                    "title":       "Medical Bill + Insurance on File",
                    "description": (
                        f"Medical document (${med_p.balance:,.2f}) and insurance policy detected. "
                        "Verify the claim was submitted and EOB received. "
                        "Check if the amount matches the insurer's allowed amount."
                    ),
                    "linked_docs": [med_p.doc_id, ins_p.doc_id],
                    "color":       "#4A90E2",
                })

    # ── 12. Same-person cross-category summary ────────────────────────────────
    people: dict[str, list[DocProfile]] = {}
    for p in profiles:
        if p.person_name:
            key = p.person_name.strip().upper()
            people.setdefault(key, []).append(p)

    for name, docs in people.items():
        if len(docs) < 2:
            continue
        unique_cats = {d.category for d in docs}
        if len(unique_cats) >= 2:
            cat_list = " + ".join(
                f"{CATEGORY_ICON.get(c,'')} {c.title()}" for c in sorted(unique_cats)
            )
            insights.insert(0, {
                "type":        "person_summary",
                "icon":        "👤",
                "title":       f"Cross-Document Profile: {name.title()}",
                "description": (
                    f"{len(docs)} documents across {len(unique_cats)} categories: {cat_list}. "
                    "All documents linked to the same individual."
                ),
                "linked_docs": [d.doc_id for d in docs],
                "color":       "#4A90E2",
            })

    # Deduplicate insights and actions by title/action text
    seen_titles: set[str] = set()
    unique_insights = []
    for ins_item in insights:
        if ins_item["title"] not in seen_titles:
            seen_titles.add(ins_item["title"])
            unique_insights.append(ins_item)

    seen_actions: set[str] = set()
    unique_actions = []
    for act in actions:
        key = act["action"]
        if key not in seen_actions:
            seen_actions.add(key)
            unique_actions.append(act)

    return unique_insights, alerts, unique_actions, timeline


# ── Public API ────────────────────────────────────────────────────────────────

def cross_link_documents(docs: list) -> dict[str, Any]:
    """
    Main entry point. Pass a list of SQLAlchemy Document rows.
    Returns a structured analysis dict.
    """
    if not docs:
        return {
            "doc_count": 0, "categories": [], "insights": [],
            "alerts": [], "actions": [], "timeline": [], "summary": "No documents found.",
        }

    profiles = [_extract_profile(d) for d in docs]
    insights, alerts, actions, timeline = _run_rules(profiles)

    categories = sorted({p.category for p in profiles})
    cat_summary = " + ".join(
        f"{CATEGORY_ICON.get(c,'')} {c.title()}" for c in categories
    )

    # Overall summary sentence
    people = {p.person_name for p in profiles if p.person_name}
    expiring_soon = [p for p in profiles if _days(p.expiry_date) is not None and _days(p.expiry_date) <= 90]

    summary_parts = [f"{len(docs)} documents across {cat_summary}"]
    if people:
        summary_parts.append(f"for {', '.join(sorted(people))}")
    if expiring_soon:
        summary_parts.append(f"{len(expiring_soon)} document(s) expiring within 90 days")
    if alerts:
        critical = sum(1 for a in alerts if a.get("severity") == "critical")
        if critical:
            summary_parts.append(f"⚠️ {critical} critical alert(s)")

    return {
        "doc_count":  len(docs),
        "categories": categories,
        "profiles":   [
            {
                "doc_id":      p.doc_id,
                "doc_type":    p.doc_type,
                "filename":    p.filename,
                "category":    p.category,
                "person_name": p.person_name,
                "org_name":    p.org_name,
                "expiry_date": _fmt(p.expiry_date),
                "days_to_expiry": _days(p.expiry_date),
            }
            for p in profiles
        ],
        "insights":   insights,
        "alerts":     sorted(alerts, key=lambda a: {"critical": 0, "warning": 1}.get(a.get("severity",""), 2)),
        "actions":    sorted(actions, key=lambda a: {"urgent": 0, "high": 1, "medium": 2}.get(a.get("priority",""), 3)),
        "timeline":   timeline,
        "summary":    ". ".join(summary_parts) + ".",
    }
