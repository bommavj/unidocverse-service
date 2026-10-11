"""
Tax planning: project a year's federal tax from the documents, then price
strategies by re-running the same calculator with the change applied.

  compute_tax(profile, year)   federal income tax (ordinary + LTCG/qualified
                               stacking), SE tax, NIIT, additional Medicare
  build_profile(tenant, year)  from pay stubs (annualised) or the latest
                               year's W-2 / 1099 / K-1 / 1099-B forms
  strategies(profile, year)    401(k), HSA, loss harvesting, 0% gain room,
                               S-corp election, safe-harbor estimates, Roth room

Federal only; every rate and limit comes from tax_rules.yaml. Planning
estimates for a reviewer — not tax advice.
"""
import re
from copy import deepcopy
from datetime import date
from statistics import median
from typing import Dict, List, Optional, Tuple

from app.services.tax.rules import by_year, rules

STATUS_NAMES = {"single": "Single", "mfj": "Married filing jointly", "hoh": "Head of household"}

EMPTY = {"wages": 0.0, "ss_wages": None, "se_income": 0.0, "interest": 0.0, "ordinary_dividends": 0.0,
         "qualified_dividends": 0.0, "st_gain": 0.0, "lt_gain": 0.0, "other_ordinary": 0.0, "passive_income": 0.0,
         "above_line": 0.0, "salt": 0.0, "mortgage_interest": 0.0, "charity": 0.0,
         "pretax_401k": 0.0, "withholding": 0.0, "estimated_payments": 0.0,
         "prior_total_tax": None, "prior_agi": None, "deferrals_known": False}


def _fed() -> Dict:
    return rules()["federal"]


def _yr(table: Dict, year: int):
    v, used = by_year(table, year)
    return v


# ── Calculator ───────────────────────────────────────────────────────────────

def _bracket_tax(amount: float, brackets: List) -> Tuple[float, float]:
    tax, lower = 0.0, 0.0
    rate = brackets[0][1]
    for upper, r in brackets:
        top = float("inf") if upper is None else upper
        if amount > lower:
            tax += (min(amount, top) - lower) * r
            rate = r
        lower = top
    return tax, rate


def compute_tax(p: Dict, year: int, status: str = "single") -> Dict:
    F = _fed()
    p = {**EMPTY, **p}
    se = F["se_tax"]
    wage_base, _ = by_year(rules()["payroll"]["social_security"]["wage_base"], year)

    se_net = max(0.0, p["se_income"]) * se["net_factor"]
    ss_wages = p["wages"] if p["ss_wages"] is None else p["ss_wages"]
    se_tax = se["ss_rate"] * min(se_net, max(0.0, wage_base - ss_wages)) + se["medicare_rate"] * se_net
    half_se = se_tax / 2

    net_cap = p["st_gain"] + p["lt_gain"]
    cap_in_income = max(net_cap, -F["capital_loss_limit"])
    agi = (p["wages"] + p["se_income"] - half_se + p["interest"] + p["ordinary_dividends"] + cap_in_income
           + p["other_ordinary"] - p["above_line"])

    std = _yr(F["standard_deduction"], year)[status]
    sc = _yr(F["salt_cap"], year)
    salt_cap = max(sc["floor"], sc["cap"] - 0.30 * max(0.0, agi - sc["threshold"]))
    itemized = min(p["salt"], salt_cap) + p["mortgage_interest"] + p["charity"]
    deduction = max(std, itemized)
    taxable = max(0.0, agi - deduction)

    net_cg = max(0.0, min(p["lt_gain"], net_cap))
    pref = min(taxable, max(0.0, p["qualified_dividends"]) + net_cg)
    ordinary_taxable = taxable - pref
    brackets = _yr(F["brackets"], year)[status]
    ord_tax, marginal = _bracket_tax(ordinary_taxable, brackets)
    zero_top, twenty_from = _yr(F["ltcg"], year)[status]
    at0 = max(0.0, min(pref, zero_top - ordinary_taxable))
    at20 = max(0.0, ordinary_taxable + pref - max(twenty_from, ordinary_taxable))
    at15 = pref - at0 - at20
    cg_tax = 0.15 * at15 + 0.20 * at20

    nii = max(0.0, p["interest"] + p["ordinary_dividends"] + max(0.0, net_cap) + p["passive_income"])
    niit = F["niit"]["rate"] * min(nii, max(0.0, agi - F["niit"]["threshold"][status]))
    am = F["additional_medicare"]
    addl_medicare = am["rate"] * max(0.0, p["wages"] + se_net - am["threshold"][status])

    income_tax = ord_tax + cg_tax
    total = income_tax + se_tax + niit + addl_medicare
    paid = p["withholding"] + p["estimated_payments"]
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in {
        "agi": agi, "deduction": deduction, "deduction_type": "itemized" if itemized > std else "standard",
        "itemized": itemized, "standard": std, "taxable": taxable, "ordinary_taxable": ordinary_taxable,
        "preferential": pref, "ordinary_tax": ord_tax, "cg_tax": cg_tax, "se_tax": se_tax, "niit": niit,
        "addl_medicare": addl_medicare, "income_tax": income_tax, "total": total, "marginal_rate": marginal,
        "effective_rate": total / agi if agi > 0 else 0.0, "paid": paid, "balance_due": total - paid,
        "zero_rate_room": max(0.0, zero_top - taxable), "bracket_top": _bracket_top(ordinary_taxable, brackets),
    }.items()}


def _bracket_top(amount: float, brackets: List) -> Optional[float]:
    for upper, _ in brackets:
        if upper is None or amount < upper:
            return upper
    return None


# ── Profile from documents ───────────────────────────────────────────────────

def _stub_projection(stubs: List[Dict], year: int) -> Optional[Dict]:
    """Annualise this year's pay stubs: per-period amounts × pay periods per year."""
    from app.services.tax.paystub_audit import _deductions
    ys = [s for s in stubs if s["pay_date"].year == year and s["facts"].get("gross_pay")]
    if len(ys) < 2:
        return None
    gaps = [(b["pay_date"] - a["pay_date"]).days for a, b in zip(ys, ys[1:]) if (b["pay_date"] - a["pay_date"]).days > 0]
    period = median(gaps) if gaps else 14
    per_year = round(365 / period)
    n = len(ys)
    pre125 = rules()["payroll"]["pre_tax_for_fica"]
    tot = {"gross": 0.0, "fed": 0.0, "k401": 0.0, "s125": 0.0}
    for s in ys:
        tot["gross"] += float(s["facts"]["gross_pay"])
        for k, v in _deductions(s["facts"]).items():
            v = abs(v)
            if re.search(r"fed", k):
                tot["fed"] += v
            elif re.search(r"401k|403b|457", k) and not re.search(r"after|roth", k):
                tot["k401"] += v
            elif any(w in k for w in pre125):
                tot["s125"] += v
    scale = per_year / n
    gross, k401, s125 = tot["gross"] * scale, tot["k401"] * scale, tot["s125"] * scale
    last = ys[-1]
    ytd = last["facts"].get("ytd_gross")
    return {"wages": gross - k401 - s125, "ss_wages": gross - s125, "pretax_401k": k401,
            "withholding": tot["fed"] * scale, "employer": last["facts"].get("employer"),
            "basis": f"{n} pay stubs ({ys[0]['pay_date']:%b %d}–{last['pay_date']:%b %d, %Y}), "
                     f"{'weekly' if period <= 8 else 'biweekly' if period <= 16 else 'semi-monthly' if period <= 17 else 'monthly'} "
                     f"× {per_year} periods" + (f"; YTD gross ${float(ytd):,.0f}" if ytd else "")}


def _form_profile(forms: List[Dict], year: int) -> Tuple[Dict, List[str]]:
    from collections import Counter
    from app.services.tax.organizer import _key, _same
    p, used = dict(EMPTY), []
    # Only forms addressed to the taxpayer (not 1099-NECs their business issued to vendors)
    names = Counter(_key(f["recipient"]) for f in forms if f.get("recipient") and f["form"] not in ("1040",))
    who = names.most_common(1)[0][0] if names else ""
    yf = [f for f in forms if f.get("year") == year and (not who or not f.get("recipient") or _same(f["recipient"], who))]
    def add(key, val, fn):
        if val:
            p[key] += val
            used.append(fn)
    ss = 0.0
    for f in yf:
        b, fn = f["boxes"], f["filename"]
        if f["form"] == "w2":
            add("wages", b.get("wages", 0), fn); add("withholding", b.get("federal_wh", 0), fn)
            ss += b.get("ss_wages", 0)
        elif f["form"] == "1099_int":
            add("interest", b.get("interest", 0) + b.get("us_bond_interest", 0), fn)
            add("withholding", b.get("federal_wh", 0), fn)
        elif f["form"] == "1099_div":
            add("ordinary_dividends", b.get("ordinary_dividends", 0), fn)
            add("qualified_dividends", b.get("qualified_dividends", 0), fn)
            add("lt_gain", b.get("capital_gain_dist", 0), fn)
        elif f["form"] == "1099_nec":
            add("se_income", b.get("nonemployee_comp", 0), fn); add("withholding", b.get("federal_wh", 0), fn)
        elif f["form"] == "1099_r":
            add("other_ordinary", b.get("taxable_amount", 0), fn); add("withholding", b.get("federal_wh", 0), fn)
        elif f["form"] == "1098":
            add("mortgage_interest", b.get("mortgage_interest", 0), fn)
    if ss:
        p["ss_wages"] = ss
    return p, used


def build_profile(tenant_id: str, year: int) -> Dict:
    from app.services.payroll_facts import _load_stubs
    from app.services.tax import capital_gains, k1
    from app.services.tax.reconcile import load_forms
    forms = load_forms(tenant_id)["forms"]
    years = sorted({f["year"] for f in forms if f.get("year") and f["form"] != "1040"})
    base = year if year in years else max([y for y in years if y < year], default=None)
    notes: List[str] = []
    p, used = (_form_profile(forms, base) if base else (dict(EMPTY), []))
    carried = base and base != year
    if base:
        cg = capital_gains.load(tenant_id, base)
        if cg["1099b"]:
            r = capital_gains.reconcile(cg["1099b"], [], [])
            for box, t in r["broker"].items():
                if t["basis_reported"]:
                    p["st_gain" if box in "ABC" else "lt_gain"] += t["proceeds"] - t["basis"] + t["wash"]
            used += [d["filename"] for d in cg["1099b"]]
        for k in k1.run(tenant_id, base)["own_k1s"] or []:
            it = k["items"]
            p["other_ordinary"] += it.get("ordinary_income", 0) + it.get("guaranteed_payments", 0)
            p["passive_income"] += it.get("rental_re", 0) + it.get("other_rental", 0)
            p["other_ordinary"] += it.get("rental_re", 0) + it.get("other_rental", 0)
            p["interest"] += it.get("interest", 0); p["ordinary_dividends"] += it.get("ordinary_dividends", 0)
            p["qualified_dividends"] += it.get("qualified_dividends", 0)
            p["st_gain"] += it.get("st_gain", 0); p["lt_gain"] += it.get("lt_gain", 0)
            p["se_income"] += it.get("se_earnings", 0)
            used.append(k["filename"])
    # Pay stubs for the plan year replace W-2 wages
    stub = _stub_projection(_load_stubs(tenant_id, None), year)
    if stub:
        w2_wh = sum(f["boxes"].get("federal_wh", 0) for f in forms if f["form"] == "w2" and f.get("year") == base)
        p["withholding"] -= w2_wh                 # stub withholding replaces the W-2's, not adds to it
        p.update({k: stub[k] for k in ("wages", "ss_wages", "pretax_401k")})
        p["withholding"] += stub["withholding"]
        p["deferrals_known"] = True
        notes.append(f"Wages and withholding annualised from {stub['basis']}")
        used = [u for u in used if not any(f["filename"] == u and f["form"] == "w2" for f in forms)]
    if carried and used:
        notes.append(f"Other income carried forward from {base} forms ({len(set(used))}); {year} rates and limits applied")
    # Prior-year return → safe harbor
    prior = [f for f in forms if f["form"] == "1040" and f.get("year") == year - 1]
    if prior:
        p["prior_total_tax"] = prior[0]["boxes"].get("total_tax")
        p["prior_agi"] = prior[0]["boxes"].get("agi")
    return {"profile": p, "sources": sorted(set(used)), "notes": notes, "base_year": base,
            "has_data": bool(stub or used)}


# ── Strategies ───────────────────────────────────────────────────────────────

def _with(p: Dict, **changes) -> Dict:
    q = deepcopy(p)
    for k, v in changes.items():
        q[k] = v
    return q


def strategies(p: Dict, year: int, status: str) -> List[Dict]:
    F, out = _fed(), []
    p = {**EMPTY, **p}
    base = compute_tax(p, year, status)

    def saving(q):
        return round(base["total"] - compute_tax(q, year, status)["total"], 2)

    lim = F["limits"]
    # 1. 401(k)
    if p["wages"] > 0:
        cap = _yr(lim["401k"], year)
        room = max(0.0, min(cap - p["pretax_401k"], p["wages"]))
        if room > 100:
            s = saving(_with(p, wages=p["wages"] - room, pretax_401k=p["pretax_401k"] + room))
            known = p.get("deferrals_known")
            out.append({"strategy": "Max out your 401(k)", "saving": s, "amount": room,
                        "detail": (f"${p['pretax_401k']:,.0f} deferred of the ${cap:,.0f} {year} limit (pay stubs) — ${room:,.0f} more pre-tax"
                                   if known else f"Up to ${cap:,.0f} pre-tax in {year}; current deferrals unknown (upload pay stubs) — "
                                   "saving shown assumes none yet")
                                  + f" (plus ${_yr(lim['401k_catchup'], year):,.0f} catch-up if 50+)."})
    # 2. HSA
    hsa = _yr(lim["hsa_self"], year)
    s = saving(_with(p, above_line=p["above_line"] + hsa))
    out.append({"strategy": "Fund an HSA (if on an HSA-eligible health plan)", "saving": s, "amount": hsa,
                "detail": f"${hsa:,.0f} self-only (${_yr(lim['hsa_family'], year):,.0f} family) is deductible; "
                          "through payroll it also skips FICA."})
    # 3. Harvest losses against realised gains
    gains = p["st_gain"] + p["lt_gain"]
    harvest = max(0.0, gains) + F["capital_loss_limit"]
    if harvest > 0:
        st_off = min(harvest, max(0.0, p["st_gain"]) + F["capital_loss_limit"])
        q = _with(p, st_gain=p["st_gain"] - st_off, lt_gain=p["lt_gain"] - (harvest - st_off))
        out.append({"strategy": "Harvest capital losses", "saving": saving(q), "amount": harvest,
                    "detail": f"Losses up to ${harvest:,.0f} offset ${max(0.0, gains):,.0f} of realised gains "
                              f"plus ${F['capital_loss_limit']:,} of ordinary income (short-term gains first). "
                              "Watch the 30-day wash-sale rule."})
    # 4. 0% long-term gain room
    if base["zero_rate_room"] > 500:
        out.append({"strategy": "Realise long-term gains at 0%", "saving": round(base["zero_rate_room"] * 0.15, 2),
                    "amount": base["zero_rate_room"],
                    "detail": f"Taxable income is ${base['zero_rate_room']:,.0f} below the 0% capital-gains ceiling — "
                              "gains realised up to that amount are tax-free now (saving ~15% later)."})
    # 5. S-corp election for self-employment income
    if p["se_income"] >= 60000:
        salary = round(p["se_income"] * 0.5, -2)
        employer_fica = salary * 0.0765
        q = _with(p, se_income=0.0, wages=p["wages"] + salary,
                  ss_wages=(p["ss_wages"] if p["ss_wages"] is not None else p["wages"]) + salary,
                  other_ordinary=p["other_ordinary"] + p["se_income"] - salary - employer_fica)
        s = round(saving(q) - employer_fica, 2)
        if s > 0:
            out.append({"strategy": "Elect S-corporation status for self-employment income", "saving": s,
                        "amount": salary,
                        "detail": f"Pay a reasonable salary (assumed ${salary:,.0f}, 50%) and take the rest as "
                                  "distributions, free of SE tax. Net of employer FICA; before ~$2–3k/yr payroll "
                                  "and return-prep costs. Salary must be defensible."})
    # 6. Safe-harbor estimated tax
    sh = F["safe_harbor"]
    req = base["total"] * sh["current"]
    if p.get("prior_total_tax"):
        mult = sh["prior_high"] if (p.get("prior_agi") or 0) > sh["high_agi"] else sh["prior"]
        req = min(req, p["prior_total_tax"] * mult)
    short = req - base["paid"]
    if base["balance_due"] > 1000 and short > 0:
        out.append({"strategy": "Pay estimated tax to the safe harbor", "saving": None, "amount": round(short, 2),
                    "detail": f"Projected tax ${base['total']:,.0f}; withholding + estimates ${base['paid']:,.0f}. "
                              f"Pay ${short:,.0f} more (≈ ${short / 4:,.0f} per quarter, or raise W-4 withholding) "
                              "to avoid the underpayment penalty."})
    # 7. Roth conversion room
    top = base["bracket_top"]
    if top and base["marginal_rate"] <= 0.24:
        room = top - base["ordinary_taxable"]
        out.append({"strategy": f"Roth conversion up to the top of the {base['marginal_rate']:.0%} bracket", "saving": None,
                    "amount": round(room, 2),
                    "detail": f"${room:,.0f} could be converted at {base['marginal_rate']:.0%} (≈ ${room * base['marginal_rate']:,.0f} "
                              "tax now) for tax-free growth — worth it if your future rate is higher."})
    # 8. Itemize vs standard
    if base["deduction_type"] == "standard" and base["itemized"] > 0.7 * base["standard"]:
        out.append({"strategy": "Bunch deductions into alternate years", "saving": None, "amount": base["itemized"],
                    "detail": f"Itemized ${base['itemized']:,.0f} vs standard ${base['standard']:,.0f}: "
                              "prepaying two years of charity (or a donor-advised fund) lets you itemize every other year."})
    return sorted(out, key=lambda s: -(s["saving"] or 0))


# ── Answer ───────────────────────────────────────────────────────────────────

def _status(question: str, tenant_id: str) -> Tuple[str, str]:
    q = question.lower()
    if re.search(r"married|jointly|\bmfj\b|spouse|wife|husband", q):
        return "mfj", "from your question"
    if re.search(r"head of household|\bhoh\b", q):
        return "hoh", "from your question"
    if "single" in q:
        return "single", "from your question"
    from app.services.tax.reconcile import _docs
    for d in _docs(tenant_id):
        t = (d.raw_text or "")[:3000]
        if re.search(r"\bform 1040\b", t[:300], re.I):
            m = re.search(r"[\[(]\s*[xX☒✓■]\s*[\])]\s*(single|married filing jointly|head of household)", t, re.I)
            if m:
                return {"single": "single", "married filing jointly": "mfj", "head of household": "hoh"}[m.group(1).lower()], "from your Form 1040"
    return "single", "assumed — say 'married filing jointly' to change"


def format_plan(year: int, status: str, status_src: str, built: Dict, tax: Dict, strat: List[Dict]) -> str:
    p = built["profile"]
    L = [f"## {year} tax plan — {STATUS_NAMES[status]} ({status_src})", ""]
    if built["notes"]:
        L += [f"_{n}._" for n in built["notes"]] + [""]
    inc = [("Wages (box 1)", p["wages"]), ("Self-employment", p["se_income"]), ("Interest", p["interest"]),
           ("Dividends", p["ordinary_dividends"]), ("Short-term gains", p["st_gain"]), ("Long-term gains", p["lt_gain"]),
           ("Other (K-1, pensions)", p["other_ordinary"])]
    L += ["| Projection | |", "|---|---:|"] + [f"| {k} | ${v:,.0f} |" for k, v in inc if v]
    L += [f"| **Adjusted gross income** | **${tax['agi']:,.0f}** |",
          f"| Deduction ({tax['deduction_type']}) | −${tax['deduction']:,.0f} |",
          f"| Taxable income | ${tax['taxable']:,.0f} |",
          f"| Income tax (ordinary ${tax['ordinary_tax']:,.0f} + capital gains ${tax['cg_tax']:,.0f}) | ${tax['income_tax']:,.0f} |"]
    for k, lbl in (("se_tax", "Self-employment tax"), ("niit", "Net investment income tax (3.8%)"),
                   ("addl_medicare", "Additional Medicare tax (0.9%)")):
        if tax[k]:
            L.append(f"| {lbl} | ${tax[k]:,.0f} |")
    L += [f"| **Total federal tax** | **${tax['total']:,.0f}** |",
          f"| Withholding + estimates | ${tax['paid']:,.0f} |",
          f"| **{'Balance due' if tax['balance_due'] > 0 else 'Refund'}** | **${abs(tax['balance_due']):,.0f}** |",
          f"| Marginal / effective rate | {tax['marginal_rate']:.0%} / {tax['effective_rate']:.1%} |", ""]
    if strat:
        total = sum(s["saving"] or 0 for s in strat if s["saving"])
        L += [f"### Strategies — up to ${total:,.0f} of federal tax saved (not all may apply)", "",
              "| Strategy | Federal tax saved | Amount | How |", "|---|---:|---:|---|"]
        for s in strat:
            sv = f"${s['saving']:,.0f}" if s["saving"] else "—"
            L.append(f"| **{s['strategy']}** | {sv} | ${s['amount']:,.0f} | {s['detail']} |")
        L.append("")
    L.append("_Federal only; state tax, credits, AMT and QBI are not modelled. Rates and limits from tax_rules.yaml "
             "(verify with the IRS). Planning estimate for review — not tax advice._")
    return "\n".join(L)


_PLAN_RE = re.compile(r"\b(tax plan(ning)?|plan (my|our|for) (\d{4} )?tax|how (can|do|could) (i|we) (save|reduce|lower|cut)\b.{0,20}tax|"
                      r"(save|reduce|lower|cut|minimi[sz]e) (my |our )?(\d{4} )?(federal )?(income )?tax(es)?|"
                      r"tax (projection|estimate|strateg\w+)|project(ed)? (my |our )?(\d{4} )?(federal )?tax|"
                      r"how much (federal )?tax will (i|we) (owe|pay)|estimated tax payments?)\b", re.I)


def answer_tax_plan(question: str, tenant_id: str) -> Optional[Dict]:
    if not _PLAN_RE.search(question):
        return None
    ym = re.search(r"\b(20\d{2})\b", question)
    year = int(ym.group(1)) if ym else date.today().year
    status, src = _status(question, tenant_id)
    built = build_profile(tenant_id, year)
    if not built["has_data"]:
        return None
    tax = compute_tax(built["profile"], year, status)
    strat = strategies(built["profile"], year, status)
    return {"answer": format_plan(year, status, src, built, tax, strat), "sources": built["sources"],
            "rows": len(strat), "plan": {"tax": tax, "strategies": strat, "profile": built["profile"]}}
