#!/usr/bin/env python3
"""
Synthetic sales-tax audit file for a fictional business, with known planted
issues, so the audit engine can be scored against an answer key.

  venv-arm64/bin/python tests/fixtures/audit_demo/generate.py

Writes to tests/fixtures/audit_demo/out/:
  sales_ledger_2025.xlsx                 every invoice for the year
  CDTFA-401 Sales and Use Tax Return 2025 Q1..Q4.pdf
  CDTFA-230 Resale Certificate - <customer>.pdf
  expected.json                          the answer key
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

import fitz
import pandas as pd

OUT = Path(__file__).with_name("out")
BUSINESS = "Sierra Office Supply LLC"
PERMIT = "SR KH 102-345678"
RATE = 0.0875  # combined rate printed on the return
random.seed(7)

RESALE_CUSTOMERS = {
    # name: (permit on certificate, certificate date, planted issue)
    "Golden State Distributors": ("SR EA 104-555123", date(2024, 11, 2), None),
    "Valley Traders Inc":        ("SR FH 107-222987", date(2025, 6, 15), "certificate_after_sales"),
    "Coastal Supply Co":         ("",                 date(2025, 1, 5),  "certificate_missing_permit"),
    "Bay Area Retail Co":        (None,               None,              "no_certificate"),
}
TAXABLE_CUSTOMERS = ["Walk-in customer", "Redwood Dental Group", "Folsom Unified School District",
                     "Capitol Law Partners", "Sunrise Cafe", "Delta Engineering"]
UNDERREPORT_Q3 = 18450.00


def quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def build_ledger():
    rows, n = [], 1000
    d = date(2025, 1, 2)
    while d.year == 2025:
        for _ in range(random.randint(0, 2)):
            n += 1
            if random.random() < 0.35:
                cust = random.choice(list(RESALE_CUSTOMERS))
                amt = round(random.uniform(800, 6500), 2)
                rows.append([d, f"INV-{n}", cust, RESALE_CUSTOMERS[cust][0] or "", "Resale", amt, 0.0])
            else:
                cust = random.choice(TAXABLE_CUSTOMERS)
                amt = round(random.uniform(40, 2200), 2)
                rows.append([d, f"INV-{n}", cust, "", "Taxable", amt, round(amt * RATE, 2)])
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["Date", "Invoice", "Customer", "Customer Permit", "Sale Type", "Amount", "Tax Collected"])


def pdf(path: Path, title: str, lines):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 60
    page.insert_text((50, y), title, fontsize=14); y += 30
    for line in lines:
        if y > 740:
            page = doc.new_page(width=612, height=792); y = 60
        page.insert_text((50, y), line, fontsize=10); y += 16
    doc.save(path)


def main():
    OUT.mkdir(exist_ok=True)
    ledger = build_ledger()
    ledger.to_excel(OUT / "sales_ledger_2025.xlsx", index=False)

    expected = {"business": BUSINESS, "rate": RATE, "quarters": {}, "resale_findings": {}}
    for q in range(1, 5):
        qd = ledger[ledger["Date"].map(quarter) == q]
        gross = round(qd["Amount"].sum(), 2)
        resale = round(qd[qd["Sale Type"] == "Resale"]["Amount"].sum(), 2)
        reported_gross = round(gross - (UNDERREPORT_Q3 if q == 3 else 0), 2)
        taxable = round(reported_gross - resale, 2)
        tax = round(taxable * RATE, 2)
        expected["quarters"][f"Q{q}"] = {"ledger_gross": gross, "reported_gross": reported_gross,
                                         "difference": round(gross - reported_gross, 2), "resale_claimed": resale}
        months = {1: "January 1 - March 31", 2: "April 1 - June 30", 3: "July 1 - September 30", 4: "October 1 - December 31"}[q]
        pdf(OUT / f"CDTFA-401 Sales and Use Tax Return 2025 Q{q}.pdf",
            "CDTFA-401  STATE, LOCAL, AND DISTRICT SALES AND USE TAX RETURN", [
                f"Seller: {BUSINESS}", f"Seller's permit number: {PERMIT}",
                f"Reporting period: {months}, 2025 (Q{q} 2025)", "",
                f"Line 1  Total (gross) sales ............................ ${reported_gross:,.2f}",
                "Line 2  Purchases subject to use tax .................. $0.00",
                f"Line 3  Total ............................................ ${reported_gross:,.2f}",
                f"Line 4  Sales to other retailers for purposes of resale  ${resale:,.2f}",
                "Line 5  Nontaxable sales of food products ............. $0.00",
                f"Line 11 Total taxable amount ............................ ${taxable:,.2f}",
                f"Combined tax rate ........................................ {RATE * 100:.2f}%",
                f"Total tax due ............................................ ${tax:,.2f}", "",
                "I hereby certify that this return is true and correct.",
                "Signature: J. Alvarez, Owner      Date: " + {1: "04/28/2025", 2: "07/29/2025", 3: "10/30/2025", 4: "01/29/2026"}[q],
            ])

    for cust, (permit, cdate, issue) in RESALE_CUSTOMERS.items():
        sales = ledger[(ledger["Customer"] == cust) & (ledger["Sale Type"] == "Resale")]
        if issue == "no_certificate":
            unsupported = sales["Amount"].sum()
        elif issue == "certificate_after_sales":
            unsupported = sales[sales["Date"] < cdate]["Amount"].sum()
        elif issue == "certificate_missing_permit":
            unsupported = sales["Amount"].sum()
        else:
            unsupported = 0.0
        expected["resale_findings"][cust] = {"issue": issue, "resale_sales": round(sales["Amount"].sum(), 2),
                                             "unsupported": round(unsupported, 2)}
        if permit is None:
            continue
        pdf(OUT / f"CDTFA-230 Resale Certificate - {cust}.pdf", "CDTFA-230  GENERAL RESALE CERTIFICATE", [
            f"I hold valid seller's permit number: {permit}",
            "I am engaged in the business of selling: office products and furniture",
            f"I am purchasing from: {BUSINESS}",
            "Description of property to be purchased: office supplies, paper, toner, furniture",
            "which I will resell in the regular course of business.", "",
            f"Name of purchaser: {cust}",
            f"Address of purchaser: {random.randint(100, 9999)} Main St, Sacramento, CA 95814",
            "Signature of purchaser: (signed)",
            f"Date: {cdate:%m/%d/%Y}",
        ])

    total_unsupported = sum(v["unsupported"] for v in expected["resale_findings"].values())
    expected["totals"] = {
        "underreported_sales": UNDERREPORT_Q3,
        "unsupported_resale": round(total_unsupported, 2),
        "tax_at_stake": round((UNDERREPORT_Q3 + total_unsupported) * RATE, 2),
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1, default=str))
    print(json.dumps(expected["totals"], indent=1))
    print(json.dumps(expected["resale_findings"], indent=1))


if __name__ == "__main__":
    main()
