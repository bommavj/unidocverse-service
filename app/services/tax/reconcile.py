"""
Cross-document tax reconciliation — the mismatches that cost money:

  A. W-2 vs pay stubs   : W-2 arithmetic (SS 6.2% to the wage base, Medicare
                          1.45% + 0.9%), and the year's stub withholding vs
                          W-2 boxes 2/4/6 when the stubs cover the year
  B. Info returns vs 1040: W-2 / 1099 / K-1 totals vs the return's lines —
                          what IRS document matching (CP2000) compares
  C. Vendor 1099 compliance: AP payments per vendor vs W-9s on file and
                          1099-NECs issued; backup-withholding exposure (24%)

Forms are read with irs_forms (rules); every finding names its documents.
For reviewer sign-off — not a tax determination.
"""
import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.tax.irs_forms import extract_forms, parse_w9, detect_forms
from app.services.tax.reverse_audit import _num
from app.services.tax.rules import by_year, rules

_CORP_WORDS = {"inc", "co", "llc", "llp", "lp", "corp", "corporation", "company", "ltd", "the", "incorporated", "pllc"}


def _ir() -> Dict:
    return rules()["info_returns"]


def _tol() -> float:
    return float(_ir()["tolerance"])


def _norm(name: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _CORP_WORDS}


def _same_party(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y) and len(x & y) / len(x | y) >= 0.6


# ── Loading ──────────────────────────────────────────────────────────────────

def _docs(tenant_id: str) -> List:
    db = SessionLocal()
    try:
        return db.execute(text("SELECT id::text AS id, filename, file_path, raw_text FROM documents "
                               "WHERE tenant_id = :t AND status = 'completed'"), {"t": tenant_id}).fetchall()
    finally:
        db.close()


def load_forms(tenant_id: str) -> Dict[str, List]:
    """All IRS forms in the tenant's documents: {"forms": [...], "w9": [...]}"""
    forms, w9s = [], []
    for d in _docs(tenant_id):
        body = d.raw_text or ""
        if not body:
            continue
        for f in extract_forms(body):
            f["filename"] = d.filename
            if f["form"] == "w9":
                w9s.append({**parse_w9(body), "filename": d.filename})
            elif f["form"] not in ("w8ben", "w4", "w7"):
                forms.append(f)
    return {"forms": forms, "w9": w9s}


# ── A. W-2 vs pay stubs ──────────────────────────────────────────────────────

def check_w2(w2: Dict, stubs: List[Dict]) -> List[Dict]:
    """W-2 arithmetic, then stub totals when the stubs cover the whole year."""
    pr = rules()["payroll"]
    b, yr, out = w2["boxes"], w2.get("year") or 0, []
    tol = _tol()
    base, _ = by_year(pr["social_security"]["wage_base"], yr)
    if b.get("ss_wages") is not None and b["ss_wages"] > base + tol:
        out.append({"check": "SS wages above wage base", "detail":
                    f"Box 3 ${b['ss_wages']:,.2f} exceeds the {yr} wage base ${base:,.0f}", "amount": b["ss_wages"] - base})
    if b.get("ss_wages") is not None and b.get("ss_tax") is not None:
        exp = round(min(b["ss_wages"], base) * pr["social_security"]["rate"], 2)
        if abs(exp - b["ss_tax"]) > tol:
            out.append({"check": "Social Security tax ≠ 6.2% of box 3", "amount": round(b["ss_tax"] - exp, 2),
                        "detail": f"Box 4 ${b['ss_tax']:,.2f}; 6.2% × ${min(b['ss_wages'], base):,.2f} = ${exp:,.2f}"})
    if b.get("medicare_wages") is not None and b.get("medicare_tax") is not None:
        m = pr["medicare"]
        exp = round(b["medicare_wages"] * m["rate"] + max(0, b["medicare_wages"] - m["additional_threshold"]) * m["additional_rate"], 2)
        if abs(exp - b["medicare_tax"]) > tol:
            out.append({"check": "Medicare tax ≠ 1.45% (+0.9%) of box 5", "amount": round(b["medicare_tax"] - exp, 2),
                        "detail": f"Box 6 ${b['medicare_tax']:,.2f}; expected ${exp:,.2f}"})
    if b.get("wages") is not None and b.get("medicare_wages") is not None and b["wages"] > b["medicare_wages"] + tol:
        out.append({"check": "Box 1 wages above Medicare wages", "amount": round(b["wages"] - b["medicare_wages"], 2),
                    "detail": "Box 1 is normally ≤ box 5 (401(k) deferrals reduce box 1 only) — check for errors"})

    year_stubs = [s for s in stubs if s["pay_date"].year == yr]
    if not year_stubs:
        return out
    first, last = year_stubs[0]["pay_date"], year_stubs[-1]["pay_date"]
    covers = first <= date(yr, 1, 21) and last >= date(yr, 12, 10)
    if not covers:
        out.append({"check": "Pay stubs don't cover the year", "amount": 0.0, "info": True,
                    "detail": f"{len(year_stubs)} stub(s) from {first:%b %d} to {last:%b %d, %Y} — withholding "
                              f"can't be tied to the W-2 until all {yr} stubs are uploaded"})
        return out
    from app.services.tax.paystub_audit import _deductions, _find
    sums = defaultdict(float)
    for s in year_stubs:
        deds = _deductions(s["facts"])
        for key, pat in (("federal_wh", r"fed"), ("ss_tax", r"social|oasdi"), ("medicare_tax", r"medicare")):
            v = _find(deds, pat)
            if v is not None:
                sums[key] += abs(v)
    for key, label in (("federal_wh", "Federal withholding (box 2)"), ("ss_tax", "Social Security tax (box 4)"),
                       ("medicare_tax", "Medicare tax (box 6)")):
        if key in sums and b.get(key) is not None and abs(sums[key] - b[key]) > max(tol, 0.002 * b[key]):
            out.append({"check": f"{label} ≠ pay stubs", "amount": round(b[key] - sums[key], 2),
                        "detail": f"W-2 ${b[key]:,.2f}; {len(year_stubs)} stubs total ${sums[key]:,.2f}"})
    return out


# ── B. Information returns vs Form 1040 ──────────────────────────────────────

# (1040 line key, label, [(form, box), ...]) — the sources the IRS matches to each line
_MATCH_1040 = [
    ("wages", "Wages (line 1a)", [("w2", "wages")]),
    ("taxable_interest", "Taxable interest (line 2b)", [("1099_int", "interest"), ("1099_int", "us_bond_interest"), ("k1", "interest")]),
    ("ordinary_dividends", "Ordinary dividends (line 3b)", [("1099_div", "ordinary_dividends"), ("k1", "dividends")]),
    ("pensions_taxable", "Pensions/annuities taxable (line 5b)", [("1099_r", "taxable_amount")]),
    ("w2_withholding", "Withholding from W-2s (line 25a)", [("w2", "federal_wh")]),
    ("1099_withholding", "Withholding from 1099s (line 25b)",
     [(f, "federal_wh") for f in ("1099_nec", "1099_misc", "1099_int", "1099_div", "1099_b", "1099_r")]),
]
_MATCH_SCHED = [
    ("gross_receipts", "Schedule C gross receipts (line 1)", [("1099_nec", "nonemployee_comp")], "at_least"),
    ("partnership_scorp_income", "Schedule E partnership/S-corp income",
     [("k1", "ordinary_income"), ("k1", "rental_income"), ("k1", "guaranteed_payments")], "equal"),
]


def check_returns(forms: List[Dict]) -> List[Dict]:
    out = []
    tol = _tol()
    years = {f["year"] for f in forms if f["form"] == "1040" and f.get("year")}
    for yr in sorted(years):
        ret = [f for f in forms if f["form"] == "1040" and f["year"] == yr]
        sched = [f for f in forms if f["form"] == "1040_sched" and f["year"] == yr]
        # Only information returns issued TO this taxpayer (a business's own 1099-NECs to vendors don't count)
        headers = " ".join((f.get("header") or "") for f in ret + sched).lower()
        def _for_taxpayer(f):
            who = _norm(f.get("recipient") or "")
            return not who or not headers or all(w in headers for w in who)
        info = [f for f in forms if f["year"] == yr and f["form"] not in ("1040", "1040_sched") and _for_taxpayer(f)]
        line = {}
        for f in ret + sched:
            line.update({k: v for k, v in f["boxes"].items() if k not in line})

        def reported(sources):
            tot, docs = 0.0, []
            for form, box in sources:
                for f in info:
                    if f["form"] == form and f["boxes"].get(box):
                        tot += f["boxes"][box]
                        docs.append(f["filename"])
            return round(tot, 2), sorted(set(docs))

        rows = [(k, lbl, src, "equal") for k, lbl, src in _MATCH_1040] + list(_MATCH_SCHED)
        for key, label, sources, mode in rows:
            total, docs = reported(sources)
            if not docs:
                continue
            on_return = line.get(key)
            filed = [f["filename"] for f in ret + sched if key in f["boxes"]] or [f["filename"] for f in ret]
            if on_return is None:
                if mode == "at_least" and not sched:
                    out.append({"year": yr, "line": label, "reported": total, "on_return": None, "difference": total,
                                "detail": "Information returns report this income but no matching schedule was found",
                                "evidence": docs + filed})
                continue
            diff = round(total - on_return, 2)
            short = diff > tol if mode == "at_least" else abs(diff) > tol
            if short:
                out.append({"year": yr, "line": label, "reported": total, "on_return": on_return, "difference": diff,
                            "detail": ("Return shows less than the information returns — likely CP2000 notice"
                                       if diff > 0 else "Return shows more than the information returns"),
                            "evidence": docs + filed})
    return out


# ── C. Vendor 1099 / W-9 compliance from the AP ledger ──────────────────────

def _ap_ledgers(tenant_id: str) -> List[Dict]:
    from app.services.tax.reverse_audit import _ledgers
    return _ledgers(tenant_id)


def _is_service(row_text: str) -> bool:
    t = row_text.lower()
    return any(w in t for w in _ir()["service_words"])


def _looks_corporate(name: str) -> bool:
    return any(re.search(p, (name or "").lower()) for p in _ir()["corporate_suffix"])


def check_vendors(ledgers: List[Dict], forms: List[Dict], w9s: List[Dict], year: Optional[int] = None) -> List[Dict]:
    ir = _ir()
    issued = [f for f in forms if f["form"] in ("1099_nec", "1099_misc")]
    pay = defaultdict(lambda: {"amount": 0.0, "lines": 0, "ledgers": set(), "desc": set()})
    for led in ledgers:
        df, c = led["df"], led["cols"]
        dates = pd.to_datetime(df[c["date"]], errors="coerce")
        for i, row in df.iterrows():
            d = dates.loc[i]
            if pd.isna(d) or (year and d.year != year):
                continue
            desc = " ".join(str(row[c[k]]) for k in ("category", "description") if c.get(k))
            if not _is_service(desc):
                continue                       # goods/merchandise aren't 1099-NEC reportable
            key = (str(row[c["vendor"]]).strip(), d.year)
            pay[key]["amount"] += _num(row[c["amount"]]) + (_num(row[c["labor"]]) if c.get("labor") else 0.0)
            pay[key]["lines"] += 1
            pay[key]["ledgers"].add(led["filename"])
            pay[key]["desc"].add(desc.strip()[:40])

    out = []
    for (vendor, yr), p in sorted(pay.items(), key=lambda x: -x[1]["amount"]):
        threshold, _ = by_year(ir["nec_threshold"], yr)
        amount = round(p["amount"], 2)
        w9 = next((w for w in w9s if _same_party(vendor, w.get("name") or "") or
                   _same_party(vendor, w.get("business_name") or "")), None)
        nec = next((f for f in issued if f.get("year") == yr and _same_party(vendor, f.get("recipient") or "")), None)
        corp = (w9 and w9.get("classification") in ("C corporation", "S corporation")) or (not w9 and _looks_corporate(vendor))
        corp_exception = any(w in " ".join(p["desc"]).lower() for w in ir["corp_exception_words"])
        status, issues, exposure = "ok", [], 0.0
        if amount < threshold:
            status = "below threshold"
        elif corp and not corp_exception:
            status = "exempt (corporation)" + ("" if w9 else " — no W-9 to confirm")
        else:
            if not w9:
                issues.append("No W-9 on file")
            elif not w9["has_tin"]:
                issues.append("W-9 has no TIN")
            elif not w9["signed"]:
                issues.append("W-9 not signed")
            if issues:
                exposure = round(amount * ir["backup_withholding_rate"], 2)
            if not nec:
                issues.append(f"No 1099-NEC issued for {yr}")
            elif abs(nec["boxes"].get("nonemployee_comp", 0) - amount) > _tol():
                issues.append(f"1099-NEC box 1 ${nec['boxes'].get('nonemployee_comp', 0):,.2f} ≠ paid ${amount:,.2f}")
            status = "issue" if issues else "ok"
        out.append({"vendor": vendor, "year": yr, "paid": amount, "threshold": threshold, "status": status,
                    "issues": issues, "backup_withholding_exposure": exposure,
                    "w9": w9["filename"] if w9 else None, "form_1099": nec["filename"] if nec else None,
                    "evidence": sorted(p["ledgers"]) + [x for x in (w9 and w9["filename"], nec and nec["filename"]) if x]})
    return out


# ── Report ───────────────────────────────────────────────────────────────────

def run_reconciliation(tenant_id: str, year: Optional[int] = None, parts=("w2", "returns", "vendors")) -> Dict:
    loaded = load_forms(tenant_id)
    forms = [f for f in loaded["forms"] if not year or f.get("year") == year]
    res: Dict = {"forms": forms, "w9": loaded["w9"], "year": year}
    if "w2" in parts:
        from app.services.payroll_facts import _load_stubs
        stubs = _load_stubs(tenant_id, None)
        res["w2"] = [{"filename": f["filename"], "year": f["year"], "boxes": f["boxes"], "findings": check_w2(f, stubs)}
                     for f in forms if f["form"] == "w2"]
    if "returns" in parts:
        res["returns"] = check_returns(forms)
    if "vendors" in parts:
        res["vendors"] = check_vendors(_ap_ledgers(tenant_id), loaded["forms"], loaded["w9"], year)
    return res


def format_reconciliation(r: Dict) -> str:
    L = []
    counts = defaultdict(int)
    for f in r["forms"]:
        counts[f["form"]] += 1
    if counts:
        L += ["**IRS forms found:** " + ", ".join(f"{k.upper().replace('_', '-')} ×{v}" for k, v in sorted(counts.items())), ""]
    if r.get("returns") is not None:
        L.append("**Information returns vs Form 1040**")
        if not any(f["form"] == "1040" for f in r["forms"]):
            L.append("No Form 1040 found — upload the return to compare.")
        elif not r["returns"]:
            L.append("✓ Every W-2/1099/K-1 total matches the return.")
        else:
            L += ["", "| Year | Line | Info returns | On return | Difference | Evidence |", "|---|---|---:|---:|---:|---|"]
            for x in r["returns"]:
                on = f"${x['on_return']:,.2f}" if x["on_return"] is not None else "—"
                L.append(f"| {x['year']} | {x['line']} | ${x['reported']:,.2f} | {on} | ${x['difference']:,.2f} | "
                         + ", ".join(f"[{e}]" for e in x["evidence"]) + " |")
            L.append("")
            L.append(f"_Positive difference = income on information returns not on the return._")
        L.append("")
    if r.get("w2") is not None:
        L.append("**W-2 checks**")
        if not r["w2"]:
            L.append("No W-2 found.")
        for w in sorted(r["w2"], key=lambda w: -(w["year"] or 0)):
            b = w["boxes"]
            head = (f"- [{w['filename']}] {w['year']}: wages ${b.get('wages', 0):,.2f}, federal withheld "
                    f"${b.get('federal_wh', 0):,.2f}, SS ${b.get('ss_tax', 0):,.2f}, Medicare ${b.get('medicare_tax', 0):,.2f}")
            bad = [f for f in w["findings"] if not f.get("info")]
            L.append(head + (" — ✓ arithmetic checks out" if not bad else ""))
            for f in w["findings"]:
                diff = f" (difference ${f['amount']:+,.2f})" if f.get("amount") and not f.get("info") else ""
                L.append(f"  - {'ℹ' if f.get('info') else '⚠'} **{f['check']}** — {f['detail']}{diff}")
        L.append("")
    if r.get("vendors") is not None:
        v = r["vendors"]
        issues = [x for x in v if x["status"] == "issue"]
        exp = round(sum(x["backup_withholding_exposure"] for x in v), 2)
        L.append("**Vendor 1099 / W-9 compliance** (service payments in the AP ledger)")
        if not v:
            L.append("No service payments found in an AP ledger.")
        else:
            L.append(f"{len(v)} vendor-year(s) · **{len(issues)} with issues** · backup-withholding exposure "
                     f"**${exp:,.2f}** (24% of payments to vendors without a valid W-9)")
            L += ["", "| Vendor | Year | Paid | Status | Issues | Exposure | W-9 | 1099 |", "|---|---|---:|---|---|---:|---|---|"]
            order = {"issue": 0, "exempt (corporation) — no W-9 to confirm": 1}
            for x in sorted(v, key=lambda x: (order.get(x["status"], 2), -x["paid"])):
                L.append(f"| {x['vendor']} | {x['year']} | ${x['paid']:,.2f} | {x['status']} | {'; '.join(x['issues']) or '—'} | "
                         f"${x['backup_withholding_exposure']:,.2f} | {x['w9'] or '—'} | {x['form_1099'] or '—'} |")
            thr = sorted({(x['year'], x['threshold']) for x in v})
            L.append("")
            L.append("_1099-NEC threshold: " + ", ".join(f"{y}: ${t:,.0f}" for y, t in thr) +
                     ". Corporations are exempt except for legal and medical payments; confirm with W-9 line 3._")
        L.append("")
    L.append("_Computed from the documents with tax_rules.yaml. For reviewer sign-off — not a tax determination._")
    return "\n".join(L)


_RECON_RE = re.compile(
    r"\b(reconcil\w*|match\w*|compare|cross-?check|tie out|cp2000|mismatch\w*|missing (income|1099s?|w-?9s?)|"
    r"unreported income|backup withholding|1099 compliance|w-?9s?)\b", re.I)
_FORM_RE = re.compile(r"\b(w-?2s?|1099s?(-\w+)?|1098|k-?1s?|1040|w-?9s?|information returns?|tax return|vendors?)\b", re.I)


def answer_reconciliation(question: str, tenant_id: str) -> Optional[Dict]:
    checking = re.search(r"\b(check|verify|validate|audit|errors?|mistakes?|wrong|correct)\b", question, re.I)
    if not ((_RECON_RE.search(question) and _FORM_RE.search(question))
            or re.search(r"backup withholding|1099 compliance", question, re.I)
            or (checking and re.search(r"\b(w-?2s?|1099s?|k-?1s?|w-?9s?)\b", question, re.I))):
        return None
    if re.search(r"sales (and use )?tax|resale|cdtfa", question, re.I):
        return None                      # sales-tax returns → sales_tax_audit
    q = question.lower()
    parts = []
    if re.search(r"w-?9|vendor|backup withholding|1099 compliance|issue(d)? 1099|contractors?", q):
        parts.append("vendors")
    if re.search(r"1040|tax return|cp2000|unreported|information returns?|1099s?\b(?!.*vendor)|k-?1", q) and "vendors" not in parts:
        parts.append("returns")
    if re.search(r"w-?2|pay ?stubs?", q):
        parts.append("w2")
    if not parts:
        parts = ["w2", "returns", "vendors"]
    ym = re.search(r"\b(20\d{2})\b", question)
    r = run_reconciliation(tenant_id, int(ym.group(1)) if ym else None, tuple(parts))
    if not (r["forms"] or r.get("vendors")):
        return None
    srcs = sorted({f["filename"] for f in r["forms"]} | {e for v in r.get("vendors") or [] for e in v["evidence"]})
    return {"answer": format_reconciliation(r), "sources": srcs, "rows": len(r["forms"]), "reconciliation": r}
