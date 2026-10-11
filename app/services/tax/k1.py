"""
Schedule K-1 reconciliation — rules only, two directions.

Entity tie-out (the partnership's / S corporation's own file):
  - K-1s add up to Schedule K, line by line
  - each partner's share = profit % × Schedule K (guaranteed payments are allocated separately)
  - profit percentages total 100%
  - capital account rolls: beginning + contributed + net income ± other − withdrawals = ending
  - Item L withdrawals = box 19 distributions

Recipient tie-out (the partner's / shareholder's Form 1040):
  - box 1 + 2 + 3 + 4c − 12 (section 179) → Schedule E line 32
  - box 8 / 9a capital gains → Schedule D lines 5 / 12
  - box 14 code A self-employment earnings → Schedule SE

For reviewer sign-off — not a tax determination.
"""
import re
from collections import defaultdict
from typing import Dict, List, Optional

from app.services.tax.irs_forms import _VALUE, _money, _name_after, _year
from app.services.tax.rules import rules

# Same wording on Form 1065 / 1120-S Schedule K and on the K-1s
ITEMS = {
    "ordinary_income":     r"ordinary\s*business\s*income\s*\(?loss\)?",
    "rental_re":           r"net\s*rental\s*real\s*estate\s*income\s*\(?loss\)?",
    "other_rental":        r"other\s*net\s*rental\s*income\s*\(?loss\)?",
    "guaranteed_payments": r"(?:total\s*)?guaranteed\s*payments(?!\s*for\s*capital)(?:\s*for\s*services)?",
    "interest":            r"interest\s*income",
    "ordinary_dividends":  r"ordinary\s*dividends",
    "qualified_dividends": r"qualified\s*dividends",
    "royalties":           r"\broyalties\b",
    "st_gain":             r"net\s*short-?\s*term\s*capital\s*gain\s*\(?loss\)?",
    "lt_gain":             r"net\s*long-?\s*term\s*capital\s*gain\s*\(?loss\)?",
    "sec1231":             r"net\s*section\s*1231\s*gain\s*\(?loss\)?",
    "sec179":              r"section\s*179\s*deduction",
    "se_earnings":         r"self-?\s*employment\s*earnings\s*\(?loss\)?|net\s*earnings\s*\(?loss\)?\s*from\s*self-?\s*employment",
    # box 19 / Schedule K 19a — not Item L "withdrawals and distributions"
    "distributions":       r"\b19a?\s*distributions?(?:\s*of\s*cash\s*and\s*marketable\s*securities)?",
}
CAPITAL = {
    "beginning":   r"beginning\s*capital\s*account",
    "contributed": r"capital\s*contributed\s*during\s*the\s*year",
    "net_income":  r"current\s*year\s*net\s*income\s*\(?loss\)?",
    "other":       r"other\s*increase\s*\(?decrease\)?",
    "withdrawals": r"withdrawals\s*(?:and|&)\s*distributions",
    "ending":      r"ending\s*capital\s*account",
}
_ALLOCATED = ["ordinary_income", "rental_re", "other_rental", "interest", "ordinary_dividends",
              "royalties", "st_gain", "lt_gain", "sec1231"]
_LABELS = {"ordinary_income": "Ordinary business income (box 1)", "rental_re": "Net rental real estate (box 2)",
           "other_rental": "Other net rental (box 3)", "guaranteed_payments": "Guaranteed payments (box 4)",
           "interest": "Interest income (box 5)", "ordinary_dividends": "Ordinary dividends (box 6a)",
           "qualified_dividends": "Qualified dividends (box 6b)", "royalties": "Royalties (box 7)",
           "st_gain": "Net short-term capital gain (box 8)", "lt_gain": "Net long-term capital gain (box 9a)",
           "sec1231": "Section 1231 gain (box 10)", "sec179": "Section 179 deduction (box 12)",
           "se_earnings": "Self-employment earnings (box 14)", "distributions": "Distributions (box 19)"}


def _tol() -> float:
    return float(rules()["info_returns"]["tolerance"])


def _grab(text: str, label: str) -> Optional[float]:
    m = re.search(r"(?:" + label + r")" + _VALUE, text, re.I)
    return _money(m.group(1)) if m else None


def _items(text: str) -> Dict[str, float]:
    out = {}
    for k, pat in ITEMS.items():
        v = _grab(text, pat)
        if v is not None:
            out[k] = v
    return out


def parse_k1(text: str) -> Dict:
    text = (text or "").replace("\\$", "$")
    form = "1120s" if re.search(r"form\s*1120-?s|shareholder'?s\s*share", text[:800], re.I) else \
           "1041" if re.search(r"form\s*1041|beneficiary'?s\s*share", text[:800], re.I) else "1065"
    pct = re.search(r"profit\s*([\d.]+)\s*%\s*([\d.]+)\s*%", text, re.I) or \
          re.search(r"percentage\s*of\s*stock\s*ownership[^\d]{0,40}([\d.]+)\s*%()", text, re.I)
    end_pct = None
    if pct:
        end_pct = float(pct.group(2) or pct.group(1))
    cap = {k: v for k, pat in CAPITAL.items() if (v := _grab(text, pat)) is not None}
    return {"entity_form": form, "year": _year(text),
            "entity": _name_after(r"(?:partnership|corporation|estate or trust)'?s\s*name", text),
            "partner": _name_after(r"(?:partner|shareholder|beneficiary)'?s\s*name", text),
            "items": _items(text), "profit_pct": end_pct, "capital": cap}


def parse_entity_return(text: str) -> Dict:
    """Form 1065 / 1120-S: entity name and Schedule K totals."""
    text = (text or "").replace("\\$", "$")
    m = re.search(r"\bschedule\s*k\b(?!-)", text, re.I)
    section = text[m.start():] if m else text
    return {"entity_form": "1120s" if re.search(r"1120-?s", text[:600], re.I) else "1065",
            "year": _year(text),
            "entity": _name_after(r"name\s*of\s*(?:partnership|corporation)", text),
            "items": _items(section)}


# ── Entity side ──────────────────────────────────────────────────────────────

def _same(a, b) -> bool:
    from app.services.tax.reconcile import _same_party
    return _same_party(a or "", b or "")


def check_entity(entity: Dict, k1s: List[Dict]) -> List[Dict]:
    tol, out = _tol(), []
    sk = entity["items"]
    docs = [entity["filename"]] + [k["filename"] for k in k1s]
    for key, total in sk.items():
        if key in ("qualified_dividends",):
            continue
        s = round(sum(k["items"].get(key, 0.0) for k in k1s), 2)
        if abs(s - total) > tol:
            out.append({"type": "K-1s don't add up to Schedule K", "item": _LABELS.get(key, key),
                        "amount": round(total - s, 2), "partner": "all",
                        "detail": f"Schedule K ${total:,.2f}; {len(k1s)} K-1s total ${s:,.2f}", "evidence": docs})
    pcts = [k["profit_pct"] for k in k1s if k["profit_pct"] is not None]
    if len(pcts) == len(k1s) and k1s and abs(sum(pcts) - 100) > 0.01:
        out.append({"type": "Profit percentages don't total 100%", "item": "Item J", "amount": round(sum(pcts) - 100, 4),
                    "partner": "all", "detail": f"Ending profit shares total {sum(pcts):g}%", "evidence": docs})
    for k in k1s:
        who = k["partner"] or k["filename"]
        if k["profit_pct"] is not None:
            for key in _ALLOCATED:
                if key not in sk:
                    continue
                exp = round(sk[key] * k["profit_pct"] / 100, 2)
                got = k["items"].get(key, 0.0)
                if abs(got - exp) > max(tol, 0.005 * abs(exp)):
                    out.append({"type": "Allocation differs from profit share", "item": _LABELS[key],
                                "amount": round(got - exp, 2), "partner": who,
                                "detail": f"{k['profit_pct']:g}% × ${sk[key]:,.2f} = ${exp:,.2f}; K-1 shows ${got:,.2f} "
                                          "(confirm any special allocation in the agreement)",
                                "evidence": [entity["filename"], k["filename"]]})
        out += _capital_checks(k, who)
    return out


def _capital_checks(k: Dict, who: str) -> List[Dict]:
    tol, c, out = _tol(), k["capital"], []
    if {"beginning", "ending"} <= set(c):
        exp = round(c["beginning"] + c.get("contributed", 0) + c.get("net_income", 0) + c.get("other", 0)
                    - abs(c.get("withdrawals", 0)), 2)
        if abs(exp - c["ending"]) > tol:
            out.append({"type": "Capital account doesn't roll forward", "item": "Item L", "partner": who,
                        "amount": round(c["ending"] - exp, 2),
                        "detail": f"${c['beginning']:,.2f} + ${c.get('contributed', 0):,.2f} + ${c.get('net_income', 0):,.2f} "
                                  f"+ ${c.get('other', 0):,.2f} − ${abs(c.get('withdrawals', 0)):,.2f} = ${exp:,.2f}; "
                                  f"ending shows ${c['ending']:,.2f}", "evidence": [k["filename"]]})
    dist = k["items"].get("distributions")
    if dist is not None and "withdrawals" in c and abs(abs(c["withdrawals"]) - dist) > tol:
        out.append({"type": "Withdrawals ≠ distributions", "item": "Item L vs box 19", "partner": who,
                    "amount": round(abs(c["withdrawals"]) - dist, 2),
                    "detail": f"Item L withdrawals ${abs(c['withdrawals']):,.2f}; box 19 distributions ${dist:,.2f}",
                    "evidence": [k["filename"]]})
    return out


# ── Recipient side ───────────────────────────────────────────────────────────

def _line(text: str, line: str, label: str) -> Optional[float]:
    m = re.search(r"(?:^|\n)\s*" + line + r"\b[^\n]*?" + label + r"[^\n]*?(" + r"\(?-?[\d,]+\.\d{2}\)?" + r")\s*$",
                  text, re.I | re.M)
    return _money(m.group(1)) if m else None


def check_recipient(k1s: List[Dict], returns: Dict[str, str]) -> List[Dict]:
    """returns: {"sched_e": text, "sched_d": text, "sched_se": text} with their filenames under "<key>_file"."""
    tol, out = _tol(), []
    if not k1s:
        return out
    docs = [k["filename"] for k in k1s]
    tot = defaultdict(float)
    for k in k1s:
        for key, v in k["items"].items():
            tot[key] += v
    exp_e = round(tot["ordinary_income"] + tot["rental_re"] + tot["other_rental"] + tot["guaranteed_payments"]
                  - abs(tot["sec179"]), 2)
    e = returns.get("sched_e")
    if e is not None:
        got = _line(e, "32", r"total\s*partnership\s*and\s*s\s*corporation") or 0.0
        if abs(got - exp_e) > tol:
            out.append({"type": "Schedule E ≠ K-1 income", "item": "Schedule E line 32", "partner": "you",
                        "amount": round(exp_e - got, 2),
                        "detail": f"K-1 boxes 1+2+3+4 − 179 = ${exp_e:,.2f}; Schedule E line 32 ${got:,.2f}"
                                  + (" (a loss may be limited by basis, at-risk or passive rules — check Form 8582)" if exp_e < 0 else ""),
                        "evidence": docs + [returns.get("sched_e_file")]})
    elif exp_e:
        out.append({"type": "Schedule E missing", "item": "Schedule E Part II", "partner": "you", "amount": exp_e,
                    "detail": f"K-1 income ${exp_e:,.2f} needs Schedule E, Part II", "evidence": docs})
    d = returns.get("sched_d")
    for key, line, lbl in (("st_gain", "5", r"net\s*short-?term"), ("lt_gain", "12", r"net\s*long-?term")):
        if abs(tot[key]) <= tol:
            continue
        got = _line(d, line, lbl) if d else None
        if got is None or abs(got - tot[key]) > tol:
            out.append({"type": "K-1 capital gain not on Schedule D", "item": f"Schedule D line {line}", "partner": "you",
                        "amount": round(tot[key] - (got or 0.0), 2),
                        "detail": f"K-1 {_LABELS[key].lower()} ${tot[key]:,.2f}; Schedule D line {line} "
                                  + (f"${got:,.2f}" if got is not None else "blank"),
                        "evidence": docs + ([returns.get("sched_d_file")] if d else [])})
    se_k1 = tot["se_earnings"]
    if se_k1 > 400 - tol:
        se = returns.get("sched_se")
        if se is None:
            out.append({"type": "Schedule SE missing", "item": "Schedule SE", "partner": "you", "amount": se_k1,
                        "detail": f"K-1 box 14 self-employment earnings ${se_k1:,.2f} — self-employment tax applies",
                        "evidence": docs})
        else:
            got = _line(se, "2", r"schedule\s*k-?1|net\s*profit") or 0.0
            if got + tol < se_k1:
                out.append({"type": "Schedule SE understated", "item": "Schedule SE line 2", "partner": "you",
                            "amount": round(se_k1 - got, 2),
                            "detail": f"K-1 box 14 ${se_k1:,.2f}; Schedule SE line 2 ${got:,.2f}",
                            "evidence": docs + [returns.get("sched_se_file")]})
    return out


# ── Loading / answer ────────────────────────────────────────────────────────

def load(tenant_id: str, year: Optional[int] = None) -> Dict:
    from app.services.tax.reconcile import _docs
    k1s, entities, returns, taxpayer_hdr = [], [], {}, ""
    for d in _docs(tenant_id):
        body = (d.raw_text or "").replace("\\$", "$")
        head = body[:800]
        if not body or (year and _year(body) != year):
            continue
        if re.search(r"schedule\s*k-?1\b", head, re.I):
            k = parse_k1(body)
            k["filename"] = d.filename
            k1s.append(k)
        elif re.search(r"\bform\s*(1065|1120-?s)\b|return of partnership income|income tax return for an s corporation", head, re.I):
            e = parse_entity_return(body)
            e["filename"] = d.filename
            entities.append(e)
        else:
            for key, pat in (("sched_e", r"schedule\s*e\b\s*\(form\s*1040"), ("sched_d", r"schedule\s*d\b\s*\(form\s*1040"),
                             ("sched_se", r"schedule\s*se\b\s*\(form\s*1040")):
                if re.search(pat, head, re.I):
                    returns[key], returns[key + "_file"] = body, d.filename
            if re.search(r"\bform\s*1040\b", head, re.I) and not re.search(r"schedule", head[:120], re.I):
                taxpayer_hdr += head.lower()
    return {"k1s": k1s, "entities": entities, "returns": returns, "taxpayer_hdr": taxpayer_hdr}


def run(tenant_id: str, year: Optional[int] = None) -> Dict:
    got = load(tenant_id, year)
    entity_results = []
    for e in got["entities"]:
        mine = [k for k in got["k1s"] if (k["year"] == e["year"]) and (_same(k["entity"], e["entity"]) or not e["entity"])]
        entity_results.append({"entity": e, "k1s": mine, "findings": check_entity(e, mine)})
    hdr = got["taxpayer_hdr"]
    own = [k for k in got["k1s"] if hdr and k["partner"] and all(w in hdr for w in k["partner"].lower().split()[:2])]
    return {"entities": entity_results, "own_k1s": own,
            "recipient": check_recipient(own, got["returns"]) if own else [], "k1_count": len(got["k1s"])}


def format_k1(r: Dict) -> str:
    L = ["**Schedule K-1 reconciliation**", ""]
    for x in r["entities"]:
        e = x["entity"]
        L.append(f"**{e['entity'] or e['filename']}** — Form {e['entity_form'].upper().replace('S', '-S')} {e['year'] or ''}, "
                 f"{len(x['k1s'])} K-1(s) [{e['filename']}]")
        keys = [k for k in e["items"] if k != "qualified_dividends"]
        if keys:
            L += ["", "| Item | Schedule K | K-1 total |", "|---|---:|---:|"]
            for k in keys:
                s = sum(k1["items"].get(k, 0.0) for k1 in x["k1s"])
                flag = " ⚠" if abs(s - e["items"][k]) > _tol() else ""
                L.append(f"| {_LABELS.get(k, k)} | ${e['items'][k]:,.2f} | ${s:,.2f}{flag} |")
        if x["k1s"]:
            L += ["", "| Partner | Profit % | Box 1 | Distributions | Ending capital |", "|---|---:|---:|---:|---:|"]
            for k in x["k1s"]:
                L.append(f"| {k['partner'] or k['filename']} | {k['profit_pct'] if k['profit_pct'] is not None else '—'}% | "
                         f"${k['items'].get('ordinary_income', 0):,.2f} | ${k['items'].get('distributions', 0):,.2f} | "
                         f"${k['capital'].get('ending', 0):,.2f} |")
        L.append("")
        L += _findings_table(x["findings"]) or ["✓ K-1s tie to Schedule K, allocations match profit shares, capital accounts roll.", ""]
    if r["own_k1s"]:
        L.append(f"**Your K-1s on your return** ({len(r['own_k1s'])}: "
                 + ", ".join(k["entity"] or k["filename"] for k in r["own_k1s"]) + ")")
        L += _findings_table(r["recipient"]) or ["✓ K-1 income, capital gains and self-employment earnings are on the return.", ""]
    if not r["entities"] and not r["own_k1s"]:
        L.append(f"{r['k1_count']} K-1(s) found, but no Form 1065/1120-S or Form 1040 to tie them to — upload the return.")
    L.append("_K-1 items per IRS box numbering (Form 1065). For reviewer sign-off — not a tax determination._")
    return "\n".join(L)


def _findings_table(fs: List[Dict]) -> List[str]:
    if not fs:
        return []
    L = [f"**{len(fs)} finding(s)**", "", "| Finding | Who | Item | Amount | Detail |", "|---|---|---|---:|---|"]
    for f in sorted(fs, key=lambda f: -abs(f["amount"])):
        L.append(f"| **{f['type']}** | {f['partner']} | {f['item']} | ${f['amount']:,.2f} | {f['detail']} |")
    return L + [""]


_K1_RE = re.compile(r"\b(k-?1s?|schedule k\b|1065|1120-?s|capital accounts?|partners?'? (share|allocation|capital)|"
                    r"guaranteed payments)\b", re.I)


def answer_k1(question: str, tenant_id: str) -> Optional[Dict]:
    if not _K1_RE.search(question):
        return None
    ym = re.search(r"\b(20\d{2})\b", question)
    r = run(tenant_id, int(ym.group(1)) if ym else None)
    if not r["k1_count"]:
        return None
    srcs = sorted({x["entity"]["filename"] for x in r["entities"]} | {k["filename"] for x in r["entities"] for k in x["k1s"]}
                  | {k["filename"] for k in r["own_k1s"]})
    return {"answer": format_k1(r), "sources": srcs, "rows": r["k1_count"], "k1": r}
