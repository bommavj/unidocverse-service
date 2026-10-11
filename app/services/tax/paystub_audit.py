"""
Pay-stub arithmetic checks: recompute Social Security, Medicare and CA SDI from
gross pay and Section 125 deductions using tax_rules.yaml, and confirm
gross − deductions = net. Differences are flagged, never silently accepted.
"""
import re
from typing import Dict, List, Optional

from app.services.payroll_facts import _load_stubs, _num
from app.services.tax.rules import by_year, rules

_TOL_ABS = 0.05      # rounding across pay periods
_TOL_REL = 0.005


def _deductions(facts: Dict) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for d in facts.get("deductions") or []:
        if isinstance(d, dict):
            amt = _num(d.get("amount"))
            if amt is not None:
                key = str(d.get("type") or d.get("name") or "other").lower()
                out[key] = out.get(key, 0.0) + amt
    return out


def _find(deds: Dict[str, float], pattern: str) -> Optional[float]:
    hits = [v for k, v in deds.items() if re.search(pattern, k)]
    return sum(hits) if hits else None


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(_TOL_ABS, expected * _TOL_REL)


def audit_stub(stub: Dict) -> Dict:
    f, year = stub["facts"], stub["pay_date"].year
    cfg = rules()["payroll"]
    gross = _num(f.get("gross_pay"))
    net = _num(f.get("net_pay"))
    deds = _deductions(f)
    checks: List[Dict] = []

    if not gross:
        return {"stub": stub, "checks": [{"name": "Gross pay", "status": "missing", "detail": "No gross pay extracted"}]}

    sec125_words = "|".join(re.escape(w) for w in cfg["pre_tax_for_fica"])
    sec125 = sum(v for k, v in deds.items() if re.search(sec125_words, k) and not re.search(r"401|life", k))
    fica_wages = gross - sec125

    ss_rate = cfg["social_security"]["rate"]
    ss_base, ss_year = by_year(cfg["social_security"]["wage_base"], year)
    med_rate = cfg["medicare"]["rate"]
    sdi_rate, sdi_year = by_year(cfg["ca_sdi"]["rate"], year)
    ytd_gross = _num(f.get("ytd_gross"))
    prior_ytd = (ytd_gross - gross) if ytd_gross else None

    def check(name, actual, expected, basis):
        if actual is None:
            checks.append({"name": name, "status": "missing",
                           "detail": f"Not found on this stub's extracted data (expected ≈ ${expected:,.2f}) — verify the document"})
        elif _close(actual, expected):
            checks.append({"name": name, "status": "ok", "detail": f"${actual:,.2f} = {basis}"})
        else:
            checks.append({"name": name, "status": "mismatch",
                           "detail": f"${actual:,.2f} vs expected ${expected:,.2f} ({basis}) — difference ${actual - expected:+,.2f}"})

    ss_expected = round(fica_wages * ss_rate, 2)
    if prior_ytd is not None and prior_ytd >= ss_base:
        ss_expected = 0.0
    check("Social Security", _find(deds, r"social.?security|oasdi"), ss_expected,
          f"{ss_rate:.1%} × ${fica_wages:,.2f}")

    med_expected = round(fica_wages * med_rate, 2)
    if ytd_gross and ytd_gross > cfg["medicare"]["additional_threshold"]:
        over = min(fica_wages, ytd_gross - cfg["medicare"]["additional_threshold"])
        med_expected += round(over * cfg["medicare"]["additional_rate"], 2)
    check("Medicare", _find(deds, r"medicare"), med_expected, f"{med_rate:.2%} × ${fica_wages:,.2f}")

    sdi_actual = _find(deds, r"sdi|disab")
    if sdi_actual is not None or re.search(r"\bca\b|california", str(f).lower()):
        note = "" if sdi_year == year else f" (using {sdi_year} rate — {year} rate not in rules)"
        check("CA SDI", sdi_actual, round(fica_wages * sdi_rate, 2), f"{sdi_rate:.1%} × ${fica_wages:,.2f}{note}")

    if net is not None:
        total_ded = sum(deds.values())
        if _close(gross - total_ded, net):
            checks.append({"name": "Net pay", "status": "ok",
                           "detail": f"${gross:,.2f} − ${total_ded:,.2f} deductions = ${net:,.2f}"})
        else:
            checks.append({"name": "Net pay", "status": "mismatch",
                           "detail": f"gross ${gross:,.2f} − deductions ${total_ded:,.2f} = ${gross - total_ded:,.2f}, "
                                     f"but net shows ${net:,.2f} — a deduction may be missing from the extracted data"})
    return {"stub": stub, "checks": checks, "fica_wages": fica_wages, "sec125": sec125}


_ICON = {"ok": "✓", "mismatch": "⚠", "missing": "?"}


def answer_paystub_audit(tenant_id: str, doc_ids=None, date_range=None) -> Optional[Dict]:
    stubs = _load_stubs(tenant_id, doc_ids)
    if date_range:
        stubs = [s for s in stubs if date_range[0] <= s["pay_date"] <= date_range[1]]
    if not stubs:
        return None
    audits = [audit_stub(s) for s in stubs]
    issues = [(a, c) for a in audits for c in a["checks"] if c["status"] != "ok"]
    n_checks = sum(len(a["checks"]) for a in audits)

    lines = [f"Checked **{len(stubs)} pay stub(s)** ({stubs[0]['pay_date']:%b %d, %Y} – {stubs[-1]['pay_date']:%b %d, %Y}): "
             f"**{n_checks - len(issues)} of {n_checks} checks passed**.", ""]
    if issues:
        lines += ["**Needs attention**", ""]
        for a, c in issues:
            lines.append(f"- {_ICON[c['status']]} **{a['stub']['pay_date']:%b %d, %Y}** · {c['name']}: {c['detail']} "
                         f"([{a['stub']['filename']}])")
        lines.append("")
    else:
        lines += ["All withholding amounts match the expected rates and every stub's net pay adds up.", ""]

    names = [c["name"] for c in audits[0]["checks"]]
    lines += ["| Pay date | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for a in audits:
        cells = {c["name"]: _ICON[c["status"]] for c in a["checks"]}
        lines.append(f"| {a['stub']['pay_date']:%b %d, %Y} | " + " | ".join(cells.get(n, "–") for n in names) + " |")

    cfg = rules()["payroll"]
    years = sorted({s["pay_date"].year for s in stubs})
    rates = "; ".join(f"{y}: SS {cfg['social_security']['rate']:.1%}, Medicare {cfg['medicare']['rate']:.2%}, "
                      f"CA SDI {by_year(cfg['ca_sdi']['rate'], y)[0]:.1%}" for y in years)
    lines += ["", f"_Rules used — {rates}. FICA/SDI wages = gross − pre-tax health deductions (Section 125); "
                  f"401(k) deferrals don't reduce them. Tolerance ±$0.05 for rounding._"]
    return {"answer": "\n".join(lines), "sources": [s["filename"] for s in stubs], "rows": len(stubs)}
