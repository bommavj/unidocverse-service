"""
Exact pay-stub answers from documents.extracted_facts — code does the maths,
the LLM is not involved. Returns None whenever the question isn't about pay
stubs or there's no structured data, so callers fall back to search.
"""
import calendar
import logging
import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from app.core.database import SessionLocal
from app.utils.text_match import any_term

logger = logging.getLogger(__name__)

PAYROLL_DOC_TYPES = ["pay_stub", "payroll", "payroll_report"]

_PAYROLL_TERMS = [
    "pay stub", "paystub", "payslip", "pay slip", "paycheck", "pay check", "payroll",
    "salary", "earnings statement", "wage", "gross pay", "net pay", "take home",
    "withholding", "withheld", "deduction", "401k", "401(k)", "fica", "medicare",
    "social security", "sdi",
]

_TAX_RE = re.compile(r"tax|fica|oasdi|medicare|social.?security|sdi|withholding", re.I)
_RETIRE_RE = re.compile(r"401|403|457|ira|retire|pension", re.I)
_BENEFIT_RE = re.compile(r"medical|dental|vision|life|health|hsa|fsa|ins|disab|ltd|std|commuter", re.I)

# What each common deduction is for — shown so the user knows why it was taken.
_EXPLAIN = [
    (r"federal", "Federal income tax", "Income tax withheld for the IRS, based on your W-4 and pay."),
    (r"social.?security|oasdi", "Social Security tax", "6.2% of wages (FICA), funds Social Security retirement/disability."),
    (r"medicare", "Medicare tax", "1.45% of wages (FICA), funds Medicare health insurance."),
    (r"sdi|disab", "State disability insurance (SDI)", "State-mandated short-term disability and paid family leave insurance."),
    (r"state|ca_|ny_|il_", "State income tax", "Income tax withheld for your state."),
    (r"local|city|county", "Local income tax", "City/county income tax."),
    (r"401k_after|after.?tax", "401(k) after-tax", "Retirement contribution taken after taxes (not tax-deductible now)."),
    (r"roth", "Roth 401(k)", "Retirement contribution after tax; qualified withdrawals are tax-free."),
    (r"401|403|457", "401(k)", "Pre-tax retirement contribution (lowers taxable wages)."),
    (r"medical", "Medical insurance", "Your share of the employer health plan premium."),
    (r"dental", "Dental insurance", "Your share of the dental plan premium."),
    (r"vision", "Vision insurance", "Your share of the vision plan premium."),
    (r"life", "Life insurance", "Group life insurance premium."),
    (r"hsa", "HSA", "Health Savings Account contribution (pre-tax)."),
    (r"fsa", "FSA", "Flexible Spending Account contribution (pre-tax)."),
]

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_TOKEN = rf"(?:(?:{_MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|(?:{_MONTHS})[a-z]*\.?\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{4}})"


def is_payroll_question(question: str) -> bool:
    return any_term(question, _PAYROLL_TERMS)


# ── Date range from the question ─────────────────────────────────────────────

def _parse_bound(token: str, end: bool) -> Optional[date]:
    from dateutil import parser as dp
    token = token.strip().rstrip(",")
    try:
        if re.fullmatch(r"\d{4}", token):
            y = int(token)
            return date(y, 12, 31) if end else date(y, 1, 1)
        has_day = bool(re.search(r"\d{1,2}(st|nd|rd|th)?,?\s+\d{4}|/|-\d{2}$", token))
        d = dp.parse(token, default=dp.parse("2000-01-01")).date()
        if has_day:
            return d
        return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1]) if end else date(d.year, d.month, 1)
    except (ValueError, OverflowError):
        return None


def date_range_from_question(question: str) -> Optional[Tuple[date, date]]:
    q = question.lower()
    m = re.search(rf"(?:from|between)\s+({_DATE_TOKEN})\s+(?:to|and|through|thru|until|till|-)\s+({_DATE_TOKEN})", q)
    if m:
        a, b = _parse_bound(m.group(1), False), _parse_bound(m.group(2), True)
        if a and b:
            return (a, b) if a <= b else (b, a)
    m = re.search(r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?\b", q)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"day": 1, "week": 7, "month": 31, "year": 366}[unit] * n
        today = date.today()
        return date.fromordinal(today.toordinal() - days), today
    if re.search(r"\b(ytd|year to date|this year)\b", q):
        today = date.today()
        return date(today.year, 1, 1), today
    m = re.search(rf"\b(?:in|for|during|of)\s+({_DATE_TOKEN})\b", q)
    if m:
        a, b = _parse_bound(m.group(1), False), _parse_bound(m.group(1), True)
        if a and b:
            return a, b
    return None


# ── Data ─────────────────────────────────────────────────────────────────────

def _load_stubs(tenant_id: str, doc_ids: Optional[List[str]]) -> List[Dict]:
    db = SessionLocal()
    try:
        sql = ("SELECT id, filename, extracted_facts FROM documents "
               "WHERE tenant_id = :tenant_id AND extracted_facts IS NOT NULL "
               "AND (doc_type = ANY(:types) OR extracted_facts ? 'gross_pay')")
        params = {"tenant_id": tenant_id, "types": PAYROLL_DOC_TYPES}
        if doc_ids:
            sql += " AND id::text = ANY(:doc_ids)"
            params["doc_ids"] = list(doc_ids)
        rows = db.execute(text(sql), params).fetchall()
    finally:
        db.close()

    stubs = []
    for r in rows:
        f = r.extracted_facts or {}
        pay_date = None
        for key in ("pay_date", "check_date", "pay_period_end"):
            if f.get(key):
                pay_date = _parse_bound(str(f[key]), False) if re.fullmatch(r"\d{4}", str(f[key])) else _to_date(f[key])
                if pay_date:
                    break
        if not pay_date:
            continue
        stubs.append({"filename": r.filename, "pay_date": pay_date, "facts": f})
    return sorted(stubs, key=lambda s: s["pay_date"])


def _gaps(dates: List[date]) -> List[Tuple[date, date]]:
    """Intervals clearly longer than the usual pay cadence (weekly/biweekly/…)."""
    if len(dates) < 3:
        return []
    intervals = sorted((b - a).days for a, b in zip(dates, dates[1:]))
    usual = intervals[len(intervals) // 2]
    return [(a, b) for a, b in zip(dates, dates[1:]) if usual and (b - a).days > usual * 1.5]


def _to_date(v) -> Optional[date]:
    from dateutil import parser as dp
    try:
        return dp.parse(str(v)).date()
    except (ValueError, OverflowError):
        return None


def _num(v) -> Optional[float]:
    try:
        return abs(float(str(v).replace("$", "").replace(",", "")))
    except (TypeError, ValueError):
        return None


def _label(raw: str) -> Tuple[str, str]:
    for pat, name, why in _EXPLAIN:
        if re.search(pat, raw, re.I):
            return name, why
    return raw.replace("_", " ").strip().title(), ""


def _category(raw: str) -> str:
    if _RETIRE_RE.search(raw):
        return "Retirement"
    if _TAX_RE.search(raw):
        return "Taxes"
    if _BENEFIT_RE.search(raw):
        return "Benefits"
    return "Other"


def _focus(question: str) -> Optional[str]:
    q = question.lower()
    if re.search(r"401|retire|403|pension", q):
        return "Retirement"
    if re.search(r"\btax|withh|fica|medicare|social security|sdi", q):
        return "Taxes"
    if re.search(r"insurance|medical|dental|vision|benefit", q):
        return "Benefits"
    return None


# ── Answer ───────────────────────────────────────────────────────────────────

def answer_from_payroll_facts(question: str, tenant_id: str, doc_ids: Optional[List[str]] = None) -> Optional[Dict]:
    if not is_payroll_question(question):
        return None
    stubs = _load_stubs(tenant_id, doc_ids)
    if not stubs:
        return None

    rng = date_range_from_question(question)
    in_range = [s for s in stubs if not rng or rng[0] <= s["pay_date"] <= rng[1]]
    all_first, all_last = stubs[0]["pay_date"], stubs[-1]["pay_date"]

    scope = (f"**{len(in_range)} pay stub(s)**" +
             (f" paid between {rng[0]:%b %d, %Y} and {rng[1]:%b %d, %Y}" if rng else "") +
             (f" ({in_range[0]['pay_date']:%b %d, %Y} – {in_range[-1]['pay_date']:%b %d, %Y})" if in_range else ""))
    coverage = f"_Pay stubs on file: {len(stubs)}, paid {all_first:%b %d, %Y} – {all_last:%b %d, %Y}._"
    if not in_range:
        return {"answer": f"No pay stubs were paid between {rng[0]:%b %d, %Y} and {rng[1]:%b %d, %Y}.\n\n{coverage}",
                "sources": [], "rows": 0}

    focus = _focus(question)
    totals: Dict[str, float] = defaultdict(float)
    stubs_with: Dict[str, set] = defaultdict(set)
    cat_of: Dict[str, str] = {}
    gross = net = 0.0
    per_stub = []
    for idx, s in enumerate(in_range):
        f = s["facts"]
        gross += _num(f.get("gross_pay")) or 0
        net += _num(f.get("net_pay")) or 0
        stub_row = {"date": s["pay_date"], "gross": _num(f.get("gross_pay")), "net": _num(f.get("net_pay")), "by_cat": defaultdict(float)}
        for d in f.get("deductions") or []:
            if not isinstance(d, dict):
                continue
            raw = str(d.get("type") or d.get("name") or d.get("description") or "other")
            amt = _num(d.get("amount"))
            if amt is None:
                continue
            name, _ = _label(raw)
            totals[name] += amt
            stubs_with[name].add(idx)
            cat_of[name] = _category(raw)
            stub_row["by_cat"][cat_of[name]] += amt
        per_stub.append(stub_row)

    shown = [n for n in totals if not focus or cat_of[n] == focus]
    if not shown:
        return None

    lines = [f"Based on {scope}:", ""]
    cats = [focus] if focus else ["Taxes", "Retirement", "Benefits", "Other"]
    for cat in cats:
        names = sorted((n for n in shown if cat_of[n] == cat), key=lambda n: -totals[n])
        if not names:
            continue
        cat_total = sum(totals[n] for n in names)
        lines += [f"**{cat}: ${cat_total:,.2f}**", "",
                  "| Deduction | Total | Per pay stub | What it is |", "|---|---:|---:|---|"]
        for n in names:
            why = next((w for p, nm, w in _EXPLAIN if nm == n), "")
            lines.append(f"| {n} | ${totals[n]:,.2f} | ${totals[n] / len(stubs_with[n]):,.2f} | {why} |")
        lines.append("")

    lines += [f"**Gross pay:** ${gross:,.2f} · **Net pay:** ${net:,.2f}"
              + (f" · Taxes are {sum(totals[n] for n in totals if cat_of[n] == 'Taxes') / gross * 100:.1f}% of gross" if gross else ""),
              "", "**Per pay stub**", ""]
    cols = cats
    lines.append("| Pay date | Gross | " + " | ".join(cols) + " | Net |")
    lines.append("|---|---:|" + "---:|" * len(cols) + "---:|")
    for r in per_stub:
        lines.append(f"| {r['date']:%b %d, %Y} | {'$%s' % format(r['gross'], ',.2f') if r['gross'] else '-'} | "
                     + " | ".join(f"${r['by_cat'].get(c, 0):,.2f}" for c in cols)
                     + f" | {'$%s' % format(r['net'], ',.2f') if r['net'] else '-'} |")
    lines += ["", coverage]
    gaps = _gaps([s["pay_date"] for s in in_range])
    if gaps:
        lines.append("_Missing pay stubs? No stubs on file between " +
                     "; ".join(f"{a:%b %d} and {b:%b %d, %Y}" for a, b in gaps) +
                     " — totals only cover the stubs uploaded._")
    if rng and (rng[0] < all_first or rng[1] > all_last):
        lines.append(f"_Part of the requested period has no pay stubs on file._")

    logger.info(f"[payroll_facts] {len(in_range)} stubs, range={rng}, focus={focus}")
    return {"answer": "\n".join(lines), "sources": [s["filename"] for s in in_range], "rows": len(in_range)}
