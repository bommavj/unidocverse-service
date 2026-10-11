"""
Sales & use tax audit engine — the two costliest CDTFA audit findings:

  1. Books vs returns  : ledger sales per quarter vs total sales on each return
  2. Resale support    : sales claimed "for resale" vs resale certificates on
                         file (missing, invalid permit, dated after the sale)

Everything is computed from the documents; each finding carries its evidence
(source files, rows, amounts). Output is for auditor review, not a determination.
"""
import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from app.core.database import SessionLocal

_MONEY = r"\$?\s*(-?[\d,]+\.\d{2})"
_RETURN_SIG = re.compile(r"sales and use tax return|cdtfa-?401", re.I)
_CERT_SIG = re.compile(r"resale certificate|cdtfa-?230", re.I)
_PERMIT_RE = re.compile(r"\b([A-Z]{2}\s*[A-Z]{2}\s*)?\d{2,3}-?\d{6}\b")
_CORP_WORDS = {"inc", "co", "llc", "corp", "corporation", "company", "ltd", "the", "incorporated"}


# ── Document loading ─────────────────────────────────────────────────────────

def _docs(tenant_id: str) -> List:
    db = SessionLocal()
    try:
        return db.execute(text(
            "SELECT id, filename, file_path, raw_text FROM documents "
            "WHERE tenant_id = :t AND status = 'completed'"), {"t": tenant_id}).fetchall()
    finally:
        db.close()


def _amount(label_re: str, body: str) -> Optional[float]:
    m = re.search(label_re + r"[^\n$\d-]*" + _MONEY, body, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def parse_return(filename: str, body: str) -> Optional[Dict]:
    body = body.replace("\\$", "$")
    qm = re.search(r"\bQ([1-4])\s*(20\d{2})\b", body) or re.search(r"\bQ([1-4])\b.*?(20\d{2})", filename)
    if not qm:
        return None
    rate_m = re.search(r"tax rate[^\d]*([\d.]+)\s*%", body, re.I)
    return {
        "filename": filename,
        "year": int(qm.group(2)), "quarter": int(qm.group(1)),
        "gross": _amount(r"total \(?gross\)? sales", body),
        "resale": _amount(r"sales to other retailers for purposes of resale", body),
        "taxable": _amount(r"total taxable amount", body),
        "rate": float(rate_m.group(1)) / 100 if rate_m else None,
        "tax_due": _amount(r"total tax due", body),
    }


def parse_certificate(filename: str, body: str) -> Dict:
    # Stop at the next "Label:" — OCR/Docling often merges form lines into one paragraph.
    name = re.search(r"name of purchaser:?\s*(.+?)(?=\s+(?:address|signature|date|title|phone|telephone|"
                     r"seller|description|i am|i hold|permit)\b[^:\n]{0,30}:|\s*[\n|]|$)", body, re.I)
    permit_line = re.search(r"seller'?s permit number:?\s*([^\n|]*)", body, re.I)
    permit = _PERMIT_RE.search(permit_line.group(1)) if permit_line else None
    dm = re.search(r"\bdate:?\s*(\d{1,2}/\d{1,2}/\d{4})", body, re.I)
    signed = bool(re.search(r"signature of purchaser:?\s*\S", body, re.I))
    cdate = None
    if dm:
        mo, dd, yy = (int(x) for x in dm.group(1).split("/"))
        cdate = date(yy, mo, dd)
    return {"filename": filename, "purchaser": name.group(1).strip() if name else None,
            "permit": permit.group(0) if permit else None, "date": cdate, "signed": signed}


def _find_col(df: pd.DataFrame, *patterns) -> Optional[str]:
    for pat in patterns:
        for c in df.columns:
            if re.search(pat, str(c), re.I):
                return c
    return None


def load_ledger(doc) -> Optional[Dict]:
    from app.services.tabular_query import TABULAR_EXTS, load_frame
    import os
    if not doc.file_path or os.path.splitext(doc.file_path)[1].lower() not in TABULAR_EXTS:
        return None
    try:
        df = load_frame(doc.file_path)
    except Exception:
        return None
    cols = {"date": _find_col(df, r"^date$", r"date"), "customer": _find_col(df, r"customer|purchaser|buyer"),
            "amount": _find_col(df, r"^amount$", r"amount|total|sale"), "type": _find_col(df, r"sale.?type|type|category")}
    if not all(cols.values()):
        return None
    df = df.copy()
    df[cols["date"]] = pd.to_datetime(df[cols["date"]], errors="coerce")
    df[cols["amount"]] = pd.to_numeric(df[cols["amount"]], errors="coerce")
    df = df.dropna(subset=[cols["date"], cols["amount"]])
    return {"filename": doc.filename, "df": df, "cols": cols}


def _norm(name: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _CORP_WORDS}


def _match_cert(customer: str, certs: List[Dict]) -> Optional[Dict]:
    target = _norm(customer)
    best, best_score = None, 0.0
    for c in certs:
        names = _norm(c["purchaser"])
        if not names or not target:
            continue
        score = len(names & target) / len(names | target)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.6 else None


# ── Audit ────────────────────────────────────────────────────────────────────

def run_audit(tenant_id: str, year: Optional[int] = None) -> Optional[Dict]:
    returns, certs, ledgers = [], [], []
    for d in _docs(tenant_id):
        body = d.raw_text or ""
        header = body[:600]  # form title/number sit in the header; reports that merely mention forms don't count
        if _RETURN_SIG.search(header):
            r = parse_return(d.filename, body)
            if r:
                returns.append(r)
        elif _CERT_SIG.search(header):
            certs.append(parse_certificate(d.filename, body))
        else:
            led = load_ledger(d)
            if led:
                ledgers.append(led)
    if not ledgers or not (returns or certs):
        return None
    led = max(ledgers, key=lambda l: len(l["df"]))
    df, c = led["df"], led["cols"]
    if year:
        df = df[df[c["date"]].dt.year == year]
        returns = [r for r in returns if r["year"] == year]

    findings: List[Dict] = []
    default_rate = next((r["rate"] for r in returns if r["rate"]), None)

    # 1. Books vs returns
    quarters = []
    df_q = df.assign(_q=df[c["date"]].dt.quarter, _y=df[c["date"]].dt.year)
    for r in sorted(returns, key=lambda r: (r["year"], r["quarter"])):
        rows = df_q[(df_q["_y"] == r["year"]) & (df_q["_q"] == r["quarter"])]
        books = round(float(rows[c["amount"]].sum()), 2)
        diff = round(books - (r["gross"] or 0), 2)
        quarters.append({"period": f"{r['year']} Q{r['quarter']}", "books": books, "reported": r["gross"],
                         "difference": diff, "return": r["filename"], "rate": r["rate"] or default_rate})
        if r["gross"] is not None and diff > 1.0:
            findings.append({"type": "Unreported sales", "period": f"{r['year']} Q{r['quarter']}",
                             "amount": diff, "rate": r["rate"] or default_rate,
                             "detail": f"Ledger shows ${books:,.2f} of sales; return reports ${r['gross']:,.2f}.",
                             "evidence": [led["filename"], r["filename"]]})

    # 2. Resale support
    resale = df[df[c["type"]].astype(str).str.contains("resale", case=False, na=False)]
    customers = []
    for cust, rows in resale.groupby(c["customer"]):
        total = round(float(rows[c["amount"]].sum()), 2)
        cert = _match_cert(str(cust), certs)
        issue, unsupported = None, 0.0
        if cert is None:
            issue, unsupported = "No resale certificate on file", total
        elif not cert["permit"]:
            issue, unsupported = "Certificate has no valid seller's permit number", total
        elif not cert["signed"]:
            issue, unsupported = "Certificate is not signed", total
        elif cert["date"]:
            early = rows[rows[c["date"]].dt.date < cert["date"]]
            if len(early):
                unsupported = round(float(early[c["amount"]].sum()), 2)
                issue = (f"{len(early)} sale(s) before the certificate date ({cert['date']:%m/%d/%Y}); "
                         f"first sale {early[c['date']].min():%m/%d/%Y}")
        customers.append({"customer": str(cust), "resale_sales": total, "invoices": len(rows),
                          "certificate": cert["filename"] if cert else None,
                          "permit": cert["permit"] if cert else None,
                          "issue": issue, "unsupported": round(unsupported, 2)})
        if issue:
            findings.append({"type": "Unsupported sales for resale", "period": str(year or "all"),
                             "customer": str(cust), "amount": round(unsupported, 2), "rate": default_rate,
                             "detail": issue, "evidence": [led["filename"]] + ([cert["filename"]] if cert else [])})

    # Resale deduction on returns vs resale in the books (consistency)
    for r in returns:
        rows = resale[(resale[c["date"]].dt.year == r["year"]) & (resale[c["date"]].dt.quarter == r["quarter"])]
        booked = round(float(rows[c["amount"]].sum()), 2)
        if r["resale"] is not None and abs(booked - r["resale"]) > 1.0:
            findings.append({"type": "Resale deduction differs from books", "period": f"{r['year']} Q{r['quarter']}",
                             "amount": round(r["resale"] - booked, 2), "rate": r["rate"] or default_rate,
                             "detail": f"Return claims ${r['resale']:,.2f} resale; ledger shows ${booked:,.2f}.",
                             "evidence": [led["filename"], r["filename"]]})

    for f in findings:
        f["tax"] = round(f["amount"] * f["rate"], 2) if f.get("rate") and f["amount"] > 0 else 0.0

    return {"ledger": led["filename"], "returns": returns, "certificates": certs,
            "quarters": quarters, "customers": customers, "findings": findings,
            "total_amount": round(sum(f["amount"] for f in findings if f["amount"] > 0), 2),
            "total_tax": round(sum(f["tax"] for f in findings), 2),
            "rows": len(df), "year": year}


def format_audit(a: Dict) -> str:
    L = [f"**Sales & use tax audit** — {a['rows']:,} ledger rows, {len(a['returns'])} return(s), "
         f"{len(a['certificates'])} resale certificate(s)", "",
         f"**{len(a['findings'])} finding(s): ${a['total_amount']:,.2f} of sales at issue · "
         f"estimated tax ${a['total_tax']:,.2f}**", ""]
    if a["findings"]:
        L += ["| # | Finding | Period / customer | Amount | Est. tax | Evidence |", "|---|---|---|---:|---:|---|"]
        for i, f in enumerate(sorted(a["findings"], key=lambda f: -f["amount"]), 1):
            who = f.get("customer") or f["period"]
            L.append(f"| {i} | **{f['type']}** — {f['detail']} | {who} | ${f['amount']:,.2f} | ${f['tax']:,.2f} | "
                     + ", ".join(f"[{e}]" for e in f["evidence"]) + " |")
        L.append("")
    if a["quarters"]:
        L += ["**Books vs returns**", "", "| Quarter | Ledger sales | Reported | Difference |", "|---|---:|---:|---:|"]
        for q in a["quarters"]:
            flag = " ⚠" if q["difference"] > 1 else ""
            L.append(f"| {q['period']} | ${q['books']:,.2f} | ${(q['reported'] or 0):,.2f} | ${q['difference']:,.2f}{flag} |")
        L.append("")
    if a["customers"]:
        L += ["**Sales for resale — certificate support**", "",
              "| Customer | Resale sales | Certificate | Status | Unsupported |", "|---|---:|---|---|---:|"]
        for c in sorted(a["customers"], key=lambda c: -c["unsupported"]):
            L.append(f"| {c['customer']} | ${c['resale_sales']:,.2f} ({c['invoices']} inv.) | "
                     f"{c['certificate'] or '—'} | {'⚠ ' + c['issue'] if c['issue'] else '✓ supported'} | "
                     f"${c['unsupported']:,.2f} |")
        L.append("")
    L.append("_Estimated tax uses the combined rate shown on each return. Findings are for auditor review "
             "and are not a tax determination._")
    return "\n".join(L)


_AUDIT_RE = re.compile(
    r"\b(audit|resale certificates?|unreported sales|under-?report\w*|unsupported (sales|resale)|"
    r"reconcil\w* .{0,30}sales tax|sales tax .{0,20}(review|check|reconcil\w*)|"
    r"(books?|ledger|records?)\b.{0,40}\b(sales (and use )?tax )?returns?|"
    r"returns?\b.{0,40}\b(books?|ledger))\b", re.I)


def answer_sales_tax_audit(question: str, tenant_id: str) -> Optional[Dict]:
    if not _AUDIT_RE.search(question):
        return None
    ym = re.search(r"\b(20\d{2})\b", question)
    a = run_audit(tenant_id, int(ym.group(1)) if ym else None)
    if not a:
        return None
    sources = sorted({e for f in a["findings"] for e in f["evidence"]} | {a["ledger"]})
    return {"answer": format_audit(a), "sources": sources, "rows": a["rows"], "audit": a}
