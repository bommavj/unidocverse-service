"""
Reverse sales & use tax audit — finds tax a business OVERPAID on its purchases
(refund claims) and tax it UNDERPAID (use-tax exposure), line by line:

  1. Tax charged on out-of-state deliveries
  2. Tax charged on purchases for resale
  3. Tax charged on nontaxable items (downloaded software, services)
  4. Manufacturing partial exemption not applied (R&TC 6377.1, 3.9375%)
  5. Tax charged on separately stated freight / installation labor
  6. Rate above the ship-to jurisdiction's rate
  7. Use tax self-accrued on a purchase the vendor already taxed (paid twice)
  8. Taxable purchase with no tax charged and no use tax accrued (exposure)

Rules and rates come from tax_rules.yaml. Each finding carries the invoice,
vendor, amounts and the rule — for reviewer sign-off, not a determination.
"""
import os
import re
from calendar import monthrange
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.tax.rules import rules

_TOL = 0.50  # dollars; ignore rounding noise


def _st() -> Dict:
    return rules()["sales_tax"]


def ca_rate(city: str, on: date) -> Optional[float]:
    """Combined rate for a CA city on a date; None if the city isn't in the table."""
    hist = _st()["ca_rates"].get((city or "").strip().lower())
    if not hist:
        return None
    rate = None
    for h in sorted(hist, key=lambda h: h["from"]):
        if h["from"] <= on:
            rate = h["rate"]
    return rate


def _category(cat: str) -> Optional[str]:
    c = (cat or "").lower()
    for kind, words in _st()["exempt_categories"].items():
        if any(w in c for w in words):
            return kind
    return None


def refund_deadline(on: date) -> date:
    """Quarterly return is due the last day of the month after the quarter; claim window runs from then."""
    q_end_month = ((on.month - 1) // 3 + 1) * 3
    y, m = (on.year + 1, 1) if q_end_month == 12 else (on.year, q_end_month + 1)
    due = date(y, m, monthrange(y, m)[1])
    yrs = _st()["refund_window_years"]
    return date(due.year + yrs, due.month, monthrange(due.year + yrs, due.month)[1])


# ── Ledger loading ───────────────────────────────────────────────────────────

_COLS = {
    "date": [r"^(invoice )?date$", r"date"],
    "vendor": [r"vendor|supplier|seller|payee"],
    "invoice": [r"invoice|inv\b|document no|po\b"],
    "description": [r"description|item|memo"],
    "category": [r"category|gl account|account|class"],
    "city": [r"ship.?to.?city|delivery city|city"],
    "state": [r"ship.?to.?state|delivery state|state"],
    "amount": [r"^(net |item |merchandise )?amount$", r"subtotal|net amount|amount"],
    "freight": [r"freight|shipping|delivery charge"],
    "labor": [r"labor|installation"],
    "tax": [r"sales tax|tax charged|tax paid|^tax$"],
    "use_tax": [r"use tax"],
}


def _find(df: pd.DataFrame, pats: List[str], taken: set) -> Optional[str]:
    for p in pats:
        for c in df.columns:
            if c not in taken and re.search(p, str(c).strip(), re.I):
                return c
    return None


def map_columns(df: pd.DataFrame) -> Optional[Dict[str, Optional[str]]]:
    cols, taken = {}, set()
    for key in ("use_tax", "tax", "freight", "labor", "date", "vendor", "invoice", "description",
                "category", "city", "state", "amount"):
        cols[key] = _find(df, _COLS[key], taken)
        if cols[key]:
            taken.add(cols[key])
    # A purchase ledger has a vendor, an amount and the tax charged
    return cols if cols["vendor"] and cols["amount"] and cols["tax"] and cols["date"] else None


def _ledgers(tenant_id: str) -> List[Dict]:
    from app.services.tabular_query import TABULAR_EXTS, load_frame
    db = SessionLocal()
    try:
        docs = db.execute(text("SELECT filename, file_path FROM documents "
                               "WHERE tenant_id = :t AND status = 'completed'"), {"t": tenant_id}).fetchall()
    finally:
        db.close()
    out = []
    for d in docs:
        if not d.file_path or os.path.splitext(d.file_path)[1].lower() not in TABULAR_EXTS:
            continue
        try:
            df = load_frame(d.file_path)
        except Exception:
            continue
        cols = map_columns(df)
        if cols:
            out.append({"filename": d.filename, "df": df, "cols": cols})
    return out


# ── Audit ────────────────────────────────────────────────────────────────────

def _num(v) -> float:
    try:
        f = float(str(v).replace(",", "").replace("$", "")) if v is not None and str(v).strip() else 0.0
        return 0.0 if f != f else f
    except ValueError:
        return 0.0


def audit_frame(df: pd.DataFrame, cols: Dict, filename: str = "ledger", today: Optional[date] = None) -> Dict:
    today = today or date.today()
    st = _st()
    mfg_cut = st["manufacturing_partial_exemption"]
    g = lambda row, k: row[cols[k]] if cols.get(k) else None

    refunds: List[Dict] = []
    exposure: List[Dict] = []
    lines = 0
    for _, row in df.iterrows():
        d = pd.to_datetime(g(row, "date"), errors="coerce")
        if pd.isna(d):
            continue
        d = d.date()
        lines += 1
        amount, tax = _num(g(row, "amount")), _num(g(row, "tax"))
        freight, labor, use_tax = _num(g(row, "freight")), _num(g(row, "labor")), _num(g(row, "use_tax"))
        state = str(g(row, "state") or "CA").strip().upper()[:2] or "CA"
        city = str(g(row, "city") or "")
        kind = _category(str(g(row, "category") or "") + " " + str(g(row, "description") or ""))
        base = {"date": d, "vendor": str(g(row, "vendor")), "invoice": str(g(row, "invoice") or ""),
                "description": str(g(row, "description") or g(row, "category") or ""),
                "amount": round(amount, 2), "tax_charged": round(tax, 2), "evidence": filename}

        # Expected tax on this line
        rate = ca_rate(city, d) if state == "CA" else 0.0
        rate_known = rate is not None
        if rate is None:
            rate = st["ca_base_rate"]          # unknown city: only the statewide floor is certain
        reasons, expected = [], 0.0
        if state != "CA":
            reasons.append(f"Delivered out of state ({state}) — not subject to CA tax")
        elif kind == "resale":
            reasons.append("Purchased for resale — exempt with a resale certificate (CDTFA-230)")
        elif kind == "nontaxable":
            reasons.append("Nontaxable item/service (e.g. electronically delivered software, services)")
        else:
            eff = rate - mfg_cut if kind == "manufacturing" else rate
            expected = amount * eff
            if kind == "manufacturing":
                reasons.append(f"Manufacturing partial exemption not applied (R&TC 6377.1, {mfg_cut:.4%})")
        if tax > 0 and expected > 0 and (freight or labor):
            # Tax computed on the extras too? (tax ≈ rate × (amount + extras))
            full = rate * (amount + freight + labor)
            if abs(tax - full) <= max(_TOL, 0.01 * full) or tax > rate * amount + _TOL:
                parts = [n for n, v in (("freight", freight), ("installation labor", labor)) if v]
                reasons.append(f"Tax charged on separately stated {' and '.join(parts)} (Reg. 1628 / 1546)")
        over = tax - expected
        if over > _TOL and rate_known and tax > 0 and not reasons and amount > 0:
            reasons.append(f"Rate charged {tax / amount:.3%} exceeds {city.title()} rate {rate:.3%}")
        elif over > _TOL and not reasons and not rate_known and tax / max(amount, 0.01) > 0.1075:
            reasons.append(f"Rate charged {tax / amount:.3%} exceeds the highest CA combined rate")

        deadline = refund_deadline(d)
        if over > _TOL and reasons:
            kind_label = ("Rate above the ship-to jurisdiction's rate" if reasons[0].startswith("Rate charged")
                          else reasons[0].split(" — ")[0].split(" (")[0])
            refunds.append({**base, "type": kind_label,
                            "reasons": reasons, "expected_tax": round(expected, 2),
                            "refund": round(over, 2), "claim_deadline": deadline,
                            "expired": deadline < today})
        if use_tax > _TOL and tax > _TOL:
            refunds.append({**base, "type": "Use tax accrued on a purchase the vendor already taxed",
                            "reasons": ["Vendor charged sales tax and use tax was also self-accrued — paid twice"],
                            "expected_tax": round(expected, 2), "refund": round(min(use_tax, tax), 2),
                            "claim_deadline": deadline, "expired": deadline < today})
        if state == "CA" and expected - tax - use_tax > _TOL and kind not in ("resale", "nontaxable"):
            exposure.append({**base, "type": "Use tax not paid", "expected_tax": round(expected, 2),
                             "owed": round(expected - tax - use_tax, 2),
                             "reasons": ["Taxable CA purchase; vendor charged no/low tax and no use tax accrued"]})

    open_r = [r for r in refunds if not r["expired"]]
    by_type: Dict[str, Dict] = {}
    for r in open_r:
        t = by_type.setdefault(r["type"], {"count": 0, "refund": 0.0})
        t["count"] += 1
        t["refund"] = round(t["refund"] + r["refund"], 2)
    return {"ledger": filename, "lines": lines, "refunds": refunds, "exposure": exposure,
            "by_type": by_type,
            "total_refund": round(sum(r["refund"] for r in open_r), 2),
            "expired_refund": round(sum(r["refund"] for r in refunds if r["expired"]), 2),
            "total_exposure": round(sum(e["owed"] for e in exposure), 2),
            "total_spend": round(float(pd.to_numeric(df[cols["amount"]], errors="coerce").fillna(0).sum()), 2)}


def run_reverse_audit(tenant_id: str, year: Optional[int] = None) -> Optional[Dict]:
    ledgers = _ledgers(tenant_id)
    if not ledgers:
        return None
    led = max(ledgers, key=lambda l: len(l["df"]))
    df, cols = led["df"], led["cols"]
    if year:
        dates = pd.to_datetime(df[cols["date"]], errors="coerce")
        df = df[dates.dt.year == year]
    return audit_frame(df, cols, led["filename"])


def format_reverse_audit(a: Dict, top: int = 15) -> str:
    L = [f"**Reverse sales & use tax audit** — {a['lines']:,} purchase lines, "
         f"${a['total_spend']:,.2f} of spend [{a['ledger']}]", "",
         f"**Refund opportunity: ${a['total_refund']:,.2f}** "
         f"({sum(1 for r in a['refunds'] if not r['expired'])} lines within the 3-year claim window)"]
    if a["expired_refund"]:
        L.append(f"Past the claim deadline (not recoverable): ${a['expired_refund']:,.2f}")
    L.append(f"**Use tax exposure: ${a['total_exposure']:,.2f}** ({len(a['exposure'])} lines)")
    L.append("")
    if a["by_type"]:
        L += ["| Refund category | Lines | Refund |", "|---|---:|---:|"]
        for t, v in sorted(a["by_type"].items(), key=lambda x: -x[1]["refund"]):
            L.append(f"| {t} | {v['count']} | ${v['refund']:,.2f} |")
        L.append("")
    open_r = sorted((r for r in a["refunds"] if not r["expired"]), key=lambda r: -r["refund"])
    if open_r:
        L += [f"**Largest refund lines** (top {min(top, len(open_r))} of {len(open_r)})", "",
              "| Date | Vendor | Invoice | Amount | Tax charged | Should be | Refund | Why | Claim by |",
              "|---|---|---|---:|---:|---:|---:|---|---|"]
        for r in open_r[:top]:
            L.append(f"| {r['date']:%m/%d/%Y} | {r['vendor']} | {r['invoice']} | ${r['amount']:,.2f} | "
                     f"${r['tax_charged']:,.2f} | ${r['expected_tax']:,.2f} | ${r['refund']:,.2f} | "
                     f"{'; '.join(r['reasons'])} | {r['claim_deadline']:%m/%d/%Y} |")
        L.append("")
    if a["exposure"]:
        L += ["**Use tax exposure** (largest)", "", "| Date | Vendor | Invoice | Amount | Owed |",
              "|---|---|---|---:|---:|"]
        for e in sorted(a["exposure"], key=lambda e: -e["owed"])[:8]:
            L.append(f"| {e['date']:%m/%d/%Y} | {e['vendor']} | {e['invoice']} | ${e['amount']:,.2f} | ${e['owed']:,.2f} |")
        L.append("")
    L.append("_Rates and exemptions from tax_rules.yaml (verify district rates with CDTFA). Refunds of tax paid "
             "to a vendor are claimed from the vendor or with the vendor's assignment; self-accrued use tax is "
             "claimed directly (CDTFA-101). For reviewer sign-off — not a tax determination._")
    return "\n".join(L)


_REVERSE_RE = re.compile(
    r"\b(reverse audit|overpa(id|y|yment)s?|over-?charged|refund (opportunit\w*|claims?)|tax refunds?|"
    r"recover\w* .{0,30}tax|(sales|use) tax .{0,30}(purchases?|vendors?|ap\b|accounts payable)|"
    r"purchases? .{0,30}(sales|use) tax|use tax (exposure|owed|accrual))", re.I)


def answer_reverse_audit(question: str, tenant_id: str) -> Optional[Dict]:
    if not _REVERSE_RE.search(question):
        return None
    ym = re.search(r"\b(20\d{2})\b", question)
    a = run_reverse_audit(tenant_id, int(ym.group(1)) if ym else None)
    if not a:
        return None
    return {"answer": format_reverse_audit(a), "sources": [a["ledger"]], "rows": a["lines"], "audit": a}
