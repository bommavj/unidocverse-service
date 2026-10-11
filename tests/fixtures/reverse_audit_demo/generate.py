#!/usr/bin/env python3
"""
Synthetic AP purchase ledger for a fictional California manufacturer, with
known planted overpayments and underpayments, so the reverse-audit engine can
be scored against an answer key.

  venv-arm64/bin/python tests/fixtures/reverse_audit_demo/generate.py

Writes to tests/fixtures/reverse_audit_demo/out/:
  ap_purchase_ledger_2023_2025.xlsx
  expected.json        refund / exposure totals per planted issue, as of AS_OF
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

OUT = Path(__file__).with_name("out")
AS_OF = date(2026, 10, 6)
RATES = {"Sacramento": 0.0875, "San Francisco": 0.08625, "San Diego": 0.0775}
MFG = 0.039375
random.seed(11)


def claim_open(d: date) -> bool:
    """Quarter return due the last day of the following month; 3-year window."""
    qm = ((d.month - 1) // 3 + 1) * 3
    y, m = (d.year + 1, 1) if qm == 12 else (d.year, qm + 1)
    nxt = date(y + (m // 12), m % 12 + 1, 1)
    due = nxt - timedelta(days=1)
    try:
        deadline = due.replace(year=due.year + 3)
    except ValueError:
        deadline = due.replace(year=due.year + 3, day=28)
    return deadline >= AS_OF


def build():
    rows, key = [], {}
    n = 50000

    def add(d, vendor, desc, cat, city, state, amount, freight=0.0, labor=0.0, tax=0.0, use_tax=0.0, issue=None,
            refund=0.0, owed=0.0):
        nonlocal n
        n += 1
        rows.append([d, f"AP-{n}", vendor, desc, cat, city, state, round(amount, 2), round(freight, 2),
                     round(labor, 2), round(tax, 2), round(use_tax, 2)])
        if issue:
            k = key.setdefault(issue, {"lines": 0, "refund": 0.0, "expired_refund": 0.0, "owed": 0.0})
            k["lines"] += 1
            if refund:
                if claim_open(d):
                    k["refund"] = round(k["refund"] + round(refund, 2), 2)
                else:
                    k["expired_refund"] = round(k["expired_refund"] + round(refund, 2), 2)
            k["owed"] = round(k["owed"] + round(owed, 2), 2)

    d = date(2023, 1, 3)
    while d <= date(2025, 12, 31):
        for _ in range(random.randint(1, 3)):
            r = random.random()
            city = random.choice(list(RATES))
            rate = RATES[city]
            amt = round(random.uniform(200, 9000), 2)
            if r < 0.55:  # correctly taxed supplies
                add(d, random.choice(["Grainger", "Uline", "Staples Business", "Fastenal"]), "Shop supplies",
                    "Supplies", city, "CA", amt, tax=amt * rate)
            elif r < 0.62:
                add(d, "Nevada Steel Fabricators", "Steel delivered to Reno plant", "Raw materials", "Reno", "NV",
                    amt, tax=amt * 0.0875, issue="out_of_state", refund=round(amt * 0.0875, 2))
            elif r < 0.68:
                add(d, "Pacific Components", "Inventory for resale - sensors", "Inventory for resale", city, "CA",
                    amt, tax=amt * rate, issue="resale", refund=round(amt * rate, 2))
            elif r < 0.73:
                add(d, "CloudCAD Inc", "Software download - CAD license", "Software download", city, "CA",
                    amt, tax=amt * rate, issue="software", refund=round(amt * rate, 2))
            elif r < 0.79:
                big = round(random.uniform(40000, 250000), 2)
                add(d, "Haas Automation", "CNC milling machine", "Manufacturing equipment", "Sacramento", "CA",
                    big, tax=big * 0.0875, issue="manufacturing", refund=round(big * 0.0875, 2) - round(big * (0.0875 - MFG), 2))
            elif r < 0.85:
                fr = round(random.uniform(150, 1200), 2)
                add(d, "Bay Industrial Supply", "Pallet racking", "Equipment", "Sacramento", "CA", amt, freight=fr,
                    tax=(amt + fr) * 0.0875, issue="freight", refund=round((amt + fr) * 0.0875, 2) - round(amt * 0.0875, 2))
            elif r < 0.90:
                add(d, "Golden Gate Electric", "Electrical panels", "Equipment", "San Francisco", "CA", amt,
                    tax=amt * 0.0925, issue="rate", refund=round(amt * 0.0925, 2) - round(amt * 0.08625, 2))
            elif r < 0.94:
                add(d, "Sierra Packaging", "Corrugated boxes", "Supplies", "Sacramento", "CA", amt,
                    tax=amt * 0.0875, use_tax=amt * 0.0875, issue="double_use_tax", refund=round(amt * 0.0875, 2))
            else:
                add(d, "Midwest Tooling LLC", "Cutting tools (out-of-state vendor)", "Tools", "Sacramento", "CA",
                    amt, tax=0.0, issue="exposure", owed=round(amt * 0.0875, 2))
        d += timedelta(days=random.choice([1, 2, 3]))

    df = pd.DataFrame(rows, columns=["Invoice Date", "Invoice", "Vendor", "Description", "Category",
                                     "Ship-To City", "Ship-To State", "Amount", "Freight", "Labor",
                                     "Sales Tax Charged", "Use Tax Accrued"])
    return df, key


def main():
    OUT.mkdir(exist_ok=True)
    df, key = build()
    df.to_excel(OUT / "ap_purchase_ledger_2023_2025.xlsx", index=False)
    total = {"refund": round(sum(k["refund"] for k in key.values()), 2),
             "expired_refund": round(sum(k["expired_refund"] for k in key.values()), 2),
             "exposure": round(sum(k["owed"] for k in key.values()), 2),
             "lines": len(df), "spend": round(float(df["Amount"].sum()), 2)}
    (OUT / "expected.json").write_text(json.dumps({"as_of": str(AS_OF), "total": total, "issues": key}, indent=2))
    print(json.dumps(total, indent=2))


if __name__ == "__main__":
    main()
