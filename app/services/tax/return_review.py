"""
One-click return review: every rules engine against one tax year, one report.

  documents   organizer      — forms expected but missing / overdue
  W-2         reconcile      — W-2 arithmetic, W-2 vs pay stubs
  income      reconcile      — W-2 / 1099 / K-1 totals vs Form 1040 lines and Schedules C / E
  capital     capital_gains  — 1099-B vs Form 8949 / Schedule D
  K-1         k1             — the taxpayer's K-1s vs Schedules E / D / SE

Each finding is normalised to {area, severity, title, amount, impact, detail,
evidence}. Severity: high = the IRS would see a mismatch (notice risk) or tax is
miscomputed; opportunity = tax likely overpaid; medium = needs a document or a
reviewer's look. For reviewer sign-off — not a tax determination.
"""
import re
from collections import Counter
from datetime import date
from typing import Dict, List, Optional

_SEV_ORDER = {"high": 0, "opportunity": 1, "medium": 2, "info": 3}


def _f(area, severity, title, amount, impact, detail, evidence) -> Dict:
    return {"area": area, "severity": severity, "title": title, "amount": round(float(amount or 0), 2),
            "impact": impact, "detail": detail, "evidence": sorted({e for e in evidence or [] if e})}


def detect_year(tenant_id: str) -> int:
    from app.services.tax.reconcile import load_forms
    years = [f["year"] for f in load_forms(tenant_id)["forms"] if f["form"] == "1040" and f.get("year")]
    return max(years) if years else date.today().year - 1


def run_review(tenant_id: str, year: Optional[int] = None) -> Dict:
    from app.services.tax import capital_gains, k1
    from app.services.tax.organizer import _load_docs, organize
    from app.services.payroll_facts import _load_stubs
    from app.services.tax.reconcile import run_reconciliation

    year = year or detect_year(tenant_id)
    findings: List[Dict] = []
    checks: List[Dict] = []

    def check(name, n_findings, ran=True, note=""):
        checks.append({"check": name, "ran": ran, "findings": n_findings, "note": note})

    # Documents
    stubs = _load_stubs(tenant_id, None)
    org = organize(_load_docs(tenant_id), stubs, year)
    for e in org["expected"]:
        findings.append(_f("Documents", "medium", f"Missing {_label(e['form'])}" + (f" from {e['payer']}" if e["payer"] else ""),
                           0, "overdue — request from payer" if e["overdue"] else "not due yet",
                           f"Expected because: {e['why']}", []))
    check("Expected tax forms received", len(org["expected"]), note=f"{len(org['received'])} form(s) on file")

    # W-2 and income vs return
    rec = run_reconciliation(tenant_id, year, ("w2", "returns"))
    has_1040 = any(f["form"] == "1040" for f in rec["forms"])
    w2_n = 0
    for w in rec.get("w2") or []:
        for x in w["findings"]:
            if x.get("info"):
                findings.append(_f("W-2", "info", x["check"], 0, "note", x["detail"], [w["filename"]]))
                continue
            w2_n += 1
            findings.append(_f("W-2", "medium", x["check"], abs(x.get("amount") or 0),
                               "employer error — request a W-2c", x["detail"], [w["filename"]]))
    check("W-2 arithmetic and pay stubs", w2_n, ran=bool(rec.get("w2")), note=f"{len(rec.get('w2') or [])} W-2(s)")
    kr = k1.run(tenant_id, year)
    for x in rec.get("returns") or []:
        if kr["own_k1s"] and x["line"].startswith("Schedule E"):
            continue                     # the K-1 check reports this line with the per-box breakdown
        under = x["difference"] > 0
        findings.append(_f("Income", "high" if under else "medium", f"{x['line']}: return ≠ information returns",
                           abs(x["difference"]), "IRS notice risk (CP2000)" if under else "income may be double-counted",
                           f"Information returns ${x['reported']:,.2f}; return "
                           + (f"${x['on_return']:,.2f}" if x["on_return"] is not None else "—"), x["evidence"]))
    n_income = len([x for x in rec.get("returns") or [] if not (kr["own_k1s"] and x["line"].startswith("Schedule E"))])
    check("W-2 / 1099 / K-1 income on the return", n_income, ran=has_1040,
          note="" if has_1040 else "no Form 1040 uploaded")

    # Capital gains
    got = capital_gains.load(tenant_id, year)
    cg_ran = bool(got["1099b"] or got["8949"])
    cg = capital_gains.reconcile(got["1099b"], got["8949"], got["sched_d"]) if cg_ran else {"findings": []}
    for x in cg["findings"]:
        sev = ("opportunity" if ("overpaid" in x["effect"] or "refund" in x["effect"]) else
               "high" if ("CP2000" in x["effect"] or "underreported" in x["effect"] or "overstated" in x["effect"]) else "medium")
        findings.append(_f("Capital gains", sev, f"Box {x['box']}: {x['type']}", x["amount"], x["effect"],
                           x["detail"], x["evidence"]))
    check("1099-B vs Form 8949 / Schedule D", len(cg["findings"]), ran=cg_ran,
          note="" if cg_ran else "no 1099-B or Form 8949")

    # K-1s
    for x in kr["recipient"]:
        findings.append(_f("K-1", "high", x["type"], abs(x["amount"]), "income or SE tax misreported",
                           x["detail"], x["evidence"]))
    check("K-1s on Schedules E / D / SE", len(kr["recipient"]), ran=bool(kr["own_k1s"]),
          note=f"{len(kr['own_k1s'])} K-1(s)" if kr["own_k1s"] else "no K-1s")

    findings.sort(key=lambda f: (_SEV_ORDER[f["severity"]], -f["amount"]))
    sev = Counter(f["severity"] for f in findings)
    unreported = sum(f["amount"] for f in findings if f["severity"] == "high")
    opportunity = sum(f["amount"] for f in findings if f["severity"] == "opportunity")
    verdict = ("Return not uploaded — only the source documents were checked" if not has_1040
               else "Not ready to file" if sev["high"]
               else "Ready after review" if sev["medium"] or sev["opportunity"] else "Ready to file")
    return {"year": year, "taxpayer": org["taxpayer"], "verdict": verdict, "findings": findings, "checks": checks,
            "counts": dict(sev), "at_risk": round(unreported, 2), "opportunity": round(opportunity, 2),
            "has_return": has_1040}


def _label(form: str) -> str:
    from app.services.tax.organizer import label
    return label(form)


_ICON = {"high": "🔴", "opportunity": "🟢", "medium": "🟠", "info": "ℹ️"}


def format_review(r: Dict) -> str:
    who = f" — {r['taxpayer'].title()}" if r["taxpayer"] else ""
    c = r["counts"]
    L = [f"## {r['year']} return review{who}", "",
         f"**{r['verdict']}** · {c.get('high', 0)} high · {c.get('opportunity', 0)} savings · "
         f"{c.get('medium', 0)} to review", ""]
    L += ["| Amounts at issue | |", "|---|---:|",
          f"| Income / gain possibly missing or misreported (IRS would see it) | ${r['at_risk']:,.2f} |",
          f"| Possible overpayment (amend to recover) | ${r['opportunity']:,.2f} |",
          f"| Documents still expected | {sum(1 for f in r['findings'] if f['area'] == 'Documents')} |", ""]
    real = [f for f in r["findings"] if f["severity"] != "info"]
    if real:
        L += ["### Findings", "", "| | Area | Finding | Amount | Impact | Detail | Evidence |", "|---|---|---|---:|---|---|---|"]
        for f in real:
            amt = f"${f['amount']:,.2f}" if f["amount"] else "—"
            L.append(f"| {_ICON[f['severity']]} | {f['area']} | **{f['title']}** | {amt} | {f['impact']} | {f['detail']} | "
                     + ", ".join(f"[{e}]" for e in f["evidence"][:4]) + " |")
        L.append("")
    notes = [f for f in r["findings"] if f["severity"] == "info"]
    if notes:
        L += ["**Notes**"] + [f"- {f['title']} — {f['detail']}" for f in notes] + [""]
    L += ["### Checks run", "", "| Check | Result | |", "|---|---|---|"]
    for ch in r["checks"]:
        res = ("— not run" if not ch["ran"] else "✓ passed" if not ch["findings"] else f"⚠ {ch['findings']} finding(s)")
        L.append(f"| {ch['check']} | {res} | {ch['note']} |")
    L += ["", "_Rules from tax_rules.yaml, applied to the uploaded documents. A reviewer signs off; "
              "this is not a tax determination._"]
    return "\n".join(L)


_REVIEW_RE = re.compile(r"\b(review (my |the |this |our |a )?(\d{4} )?(tax |income tax |individual )?return|"
                        r"return review|is (my|the) (\d{4} )?(tax )?return (ready|correct|right)|ready to file|"
                        r"check (my |the )?(\d{4} )?(whole |entire )?(tax )?return)\b", re.I)


def answer_return_review(question: str, tenant_id: str) -> Optional[Dict]:
    if not _REVIEW_RE.search(question):
        return None
    ym = re.search(r"\b(20\d{2})\b", question)
    r = run_review(tenant_id, int(ym.group(1)) if ym else None)
    srcs = sorted({e for f in r["findings"] for e in f["evidence"]})
    return {"answer": format_review(r), "sources": srcs, "rows": len(r["findings"]), "review": r}
