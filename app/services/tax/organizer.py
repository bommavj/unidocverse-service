"""
Tax-year organizer: which tax forms arrived for a year, which are still
expected and why, and what each one brings onto the return. Rules only:

  - the same payer sent the same form last year        → expected again
  - pay stubs from an employer                          → a W-2 from them
  - bank / brokerage / mortgage statements              → 1099-INT / 1099-DIV, B / 1098
  - last year's 1040 shows interest, dividends, …       → the matching 1099s
  - 1099-NEC, K-1, 1099-B, 1098 received                → the schedules they need
  - each form's furnish date (tax_rules.yaml)           → "overdue — ask the payer"

Forms are read with irs_forms. A form issued BY the user's business to a vendor
isn't income to the user, so only forms addressed to the taxpayer count.
"""
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.payroll_facts import _gaps, _load_stubs
from app.services.tax.irs_forms import extract_forms
from app.services.tax.rules import rules

_LABEL = {"w2": "W-2", "k1": "Schedule K-1", "1098": "Form 1098", "1095": "Form 1095"}
_STMT_EXPECT = {   # statement doc type → forms it implies
    "bank_statement": [("1099_int", "if the account paid $10+ interest")],
    "brokerage_statement": [("1099_div", "dividends"), ("1099_b", "sales of securities")],
    "investment_statement": [("1099_div", "dividends"), ("1099_b", "sales of securities")],
    "mortgage_statement": [("1098", "mortgage interest")],
    "mortgage_document": [("1098", "mortgage interest")],
}
_RETURN_LINE_EXPECT = [("taxable_interest", "1099_int"), ("ordinary_dividends", "1099_div"),
                       ("pensions_taxable", "1099_r")]
_STOP = {"inc", "llc", "llp", "lp", "co", "corp", "corporation", "company", "the", "na", "bank", "of"}


def label(form: str) -> str:
    return _LABEL.get(form, "Form " + form.upper().replace("_", "-"))


def _key(name: Optional[str]) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _STOP]
    return " ".join(words[:3])


def _same(a: Optional[str], b: Optional[str]) -> bool:
    x, y = set(_key(a).split()), set(_key(b).split())
    return bool(x and y) and len(x & y) / len(x | y) >= 0.5


def _years_of(filename: str, facts: Dict, created) -> set:
    """All tax years a document covers — a Dec–Jan statement counts for both."""
    facts = facts or {}
    for k in ("pay_date", "tax_year"):
        m = re.search(r"(20\d{2})", str(facts.get(k) or ""))
        if m:
            return {int(m.group(1))}
    for k in ("statement_period", "period", "billing_period", "due_date", "period_end"):
        ys = {int(y) for y in re.findall(r"(20\d{2})", str(facts.get(k) or ""))}
        if ys:
            return set(range(min(ys), max(ys) + 1))
    tx = {int(m) for t in facts.get("transactions") or [] if isinstance(t, dict)
          for m in re.findall(r"(20\d{2})", str(t.get("date") or ""))}
    if tx:
        return set(range(min(tx), max(tx) + 1))
    ys = {int(y) for y in re.findall(r"(20\d{2})", filename or "")}
    return ys or ({created.year} if created else set())


def _issuer(facts: Dict) -> Optional[str]:
    for k in ("bank_name", "institution", "lender", "servicer", "broker", "issuer", "company", "vendor"):
        if (facts or {}).get(k):
            return str(facts[k])
    return None


def organize(docs: List[Dict], stubs: List[Dict], year: int, today: Optional[date] = None) -> Dict:
    """
    docs: [{filename, doc_type, raw_text, facts, created}] · stubs: _load_stubs output.
    Returns {received, expected, schedules, statements, taxpayer}.
    """
    today = today or date.today()
    ir = rules()["info_returns"]
    forms = []
    for d in docs:
        for f in extract_forms(d.get("raw_text") or ""):
            if f["form"] in ("w9", "w8ben", "w4", "w7", "1040_sched", "8949"):
                continue
            f["filename"] = d["filename"]
            forms.append(f)

    # Whose taxes: the most frequent recipient on income forms
    names = Counter(_key(f["recipient"]) for f in forms
                    if f["recipient"] and f["form"] not in ("1040", "1120", "1120s", "1065"))
    taxpayer = names.most_common(1)[0][0] if names else ""
    mine = [f for f in forms if not f["recipient"] or not taxpayer or _same(f["recipient"], taxpayer)
            or f["form"] in ("1040",)]

    by_year = defaultdict(list)
    for f in mine:
        if f.get("year"):
            by_year[f["year"]].append(f)
    received = [f for f in by_year.get(year, []) if f["form"] != "1040"]

    def have(form: str, payer: Optional[str] = None) -> bool:
        return any(f["form"] == form and (payer is None or not f["payer"] or _same(f["payer"], payer))
                   for f in received)

    expected: List[Dict] = []

    def expect(form: str, payer: Optional[str], why: str):
        if have(form, payer):
            return
        if any(e["form"] == form and (payer is None or _same(e["payer"], payer) or not e["payer"]) for e in expected):
            return
        mmdd = ir["furnish_by"].get(form)
        due = date(year + 1, int(mmdd[:2]), int(mmdd[3:])) if mmdd else None
        expected.append({"form": form, "payer": payer, "why": why, "due": due,
                         "overdue": bool(due and today > due)})

    # 1. Same payer, same form last year (or the year before)
    for prior in (year - 1, year - 2):
        for f in by_year.get(prior, []):
            if f["form"] == "1040":
                continue
            expect(f["form"], f["payer"], f"received from {f['payer'] or 'this payer'} for {prior} [{f['filename']}]")

    # 2. Employers on this year's pay stubs → W-2
    year_stubs = [s for s in stubs if s["pay_date"].year == year]
    employers = sorted({str(s["facts"].get("employer") or "").strip() for s in year_stubs} - {""})
    w2s = [f for f in received if f["form"] == "w2"]
    for emp in employers:
        # A scanned W-2 often has no readable employer name — with one employer, it counts
        unnamed = any(not f["payer"] for f in w2s)
        if len(employers) == 1 and unnamed:
            for f in w2s:
                f["payer"] = f["payer"] or f"{emp} (per pay stubs)"
        if not any(_same(f["payer"], emp) for f in w2s) and not (len(employers) == 1 and unnamed):
            expect("w2", emp, f"{year} pay stubs from {emp}")

    # 3. Statements → the forms the institution sends
    statements = []
    for d in docs:
        imp = _STMT_EXPECT.get(d.get("doc_type") or "")
        if imp and year in _years_of(d["filename"], d.get("facts"), d.get("created")):
            who = _issuer(d.get("facts"))
            statements.append(d["filename"])
            for form, what in imp:
                expect(form, who, f"{d.get('doc_type').replace('_', ' ')} on file ({what}) [{d['filename']}]")

    # 4. Last year's return reported this kind of income → expect the form again
    for f in by_year.get(year - 1, []):
        if f["form"] != "1040":
            continue
        for line, form in _RETURN_LINE_EXPECT:
            if (f["boxes"].get(line) or 0) > 0 and not have(form):
                expect(form, None, f"your {year - 1} Form 1040 reported ${f['boxes'][line]:,.2f} "
                                   f"({line.replace('_', ' ')}) [{f['filename']}]")

    # 5. Schedules the received forms bring onto the return
    schedules = []
    for form, sched in ir["needs_schedule"].items():
        src = [f for f in received if f["form"] == form]
        if src:
            schedules.append({"form": form, "schedule": sched, "from": sorted({f["filename"] for f in src})})

    return {"year": year, "taxpayer": taxpayer, "received": received, "expected": expected,
            "schedules": schedules, "statements": statements,
            "stub_gaps": _gaps([s["pay_date"] for s in year_stubs]) if year_stubs else [],
            "stub_count": len(year_stubs)}


def format_organizer(o: Dict) -> str:
    y = o["year"]
    L = [f"**Tax year {y} organizer**" + (f" — {o['taxpayer'].title()}" if o["taxpayer"] else ""), ""]
    L.append(f"**Received ({len(o['received'])})**")
    if o["received"]:
        L += ["", "| Form | From | Key amounts | File |", "|---|---|---|---|"]
        for f in sorted(o["received"], key=lambda f: f["form"]):
            amts = ", ".join(f"{k.replace('_', ' ')} ${v:,.2f}" for k, v in list(f["boxes"].items())[:3])
            L.append(f"| {label(f['form'])} | {f['payer'] or '—'} | {amts or '—'} | [{f['filename']}] |")
    else:
        L.append(f"No {y} tax forms found yet.")
    L.append("")
    L.append(f"**Still expected ({len(o['expected'])})**")
    if o["expected"]:
        L += ["", "| Form | From | Why we expect it | Due to you | Status |", "|---|---|---|---|---|"]
        for e in sorted(o["expected"], key=lambda e: (not e["overdue"], e["form"])):
            status = "⚠ overdue — ask the payer" if e["overdue"] else "not due yet"
            due = f"{e['due']:%b %d, %Y}" if e["due"] else "—"
            L.append(f"| {label(e['form'])} | {e['payer'] or '—'} | {e['why']} | {due} | {status} |")
    else:
        L.append("Nothing outstanding based on last year's forms, pay stubs and statements.")
    if o["stub_gaps"]:
        L += ["", "Pay stubs missing between " + "; ".join(f"{a:%b %d} and {b:%b %d}" for a, b in o["stub_gaps"])
              + " — needed to tie withholding to the W-2."]
    if o["schedules"]:
        L += ["", "**What these forms bring onto the return**", ""]
        for s in o["schedules"]:
            L.append(f"- {label(s['form'])} → {s['schedule']} (" + ", ".join(f"[{x}]" for x in s["from"]) + ")")
    L += ["", "_Based on the documents uploaded and payer deadlines in tax_rules.yaml. Not tax advice._"]
    return "\n".join(L)


def _load_docs(tenant_id: str) -> List[Dict]:
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT filename, doc_type, raw_text, extracted_facts, created_at FROM documents "
            "WHERE tenant_id = :t AND status = 'completed'"), {"t": tenant_id}).fetchall()
    finally:
        db.close()
    return [{"filename": r.filename, "doc_type": r.doc_type, "raw_text": r.raw_text,
             "facts": r.extracted_facts or {}, "created": r.created_at} for r in rows]


def answer_tax_organizer(tenant_id: str, year: int) -> Optional[Dict]:
    o = organize(_load_docs(tenant_id), _load_stubs(tenant_id, None), year)
    sources = sorted({f["filename"] for f in o["received"]} | set(o["statements"]))
    return {"answer": format_organizer(o), "sources": sources, "rows": len(o["received"]), "organizer": o}
