#!/usr/bin/env python3
"""
1,000 synthetic W-9s + vendor master for a fictional mid-market company, with
planted exceptions and an answer key (tests/test_vendor_audit.py).

  venv-arm64/bin/python tests/fixtures/vendor_audit_demo/generate.py [--n 1000]

All names and numbers are fictional; TINs are random but format-valid unless planted.
"""
import argparse
import json
import random
from collections import Counter
from datetime import date
from pathlib import Path

import fitz
import pandas as pd

OUT = Path(__file__).with_name("out")
random.seed(42)
LAST = ["Garcia", "Nguyen", "Patel", "Okafor", "Kowalski", "Haddad", "Silva", "Tanaka", "Murphy", "Rossi", "Larsen",
        "Dubois", "Mendez", "Kaur", "Ivanova", "Byrne", "Adeyemi", "Fischer", "Novak", "Castillo", "Lindqvist", "Moreau",
        "Yilmaz", "Abbott", "Quinn", "Varga", "Sato", "Bianchi", "Holm", "Petrov", "Reyes", "Chen", "Duarte", "Weber",
        "Kim", "Ortiz", "Brennan", "Sharma", "Lopez", "Ahmed"]
FIRST = ["Maria", "James", "Aisha", "Wei", "Sofia", "Daniel", "Priya", "Lucas", "Fatima", "Noah"]
TRADE = ["Plumbing", "Electric", "Consulting", "Janitorial", "Logistics", "Legal", "Design", "Staffing", "Roofing",
         "Security", "Landscaping", "Accounting", "Marketing", "Engineering", "Catering", "Printing", "Welding",
         "Hydraulics", "Signage", "Courier", "Analytics", "Translation", "Surveying", "Machining", "Glazing",
         "Paving", "Coatings", "Fencing", "Calibration", "Upholstery"]
SUFFIX = [("LLC", "Limited liability company"), ("Inc", "C corporation"), ("Corp", "S corporation"),
          ("Partners", "Partnership"), ("", "Individual/sole proprietor")]
VALID_EIN = ["10", "20", "27", "33", "45", "46", "52", "61", "71", "84", "88", "91", "94", "95"]
PLANT = {1: "Invalid TIN format", 2: "Invalid TIN format", 3: "Missing TIN", 4: "TIN type doesn't fit the entity",
         5: "Name doesn't match vendor master", 6: "W-9 not signed", 7: "No tax classification checked",
         8: "Outdated W-9 revision", 9: "Foreign address — W-8 likely required", 10: "Duplicate TIN across vendors",
         11: "W-9 not dated"}


def ein():
    return f"{random.choice(VALID_EIN)}-{random.randint(1000000, 9999999)}"


def ssn():
    return f"{random.randint(100, 665):03d}-{random.randint(1, 99):02d}-{random.randint(1, 9999):04d}"


def build(n: int):
    OUT.mkdir(exist_ok=True)
    forms_dir = OUT / "w9"
    forms_dir.mkdir(exist_ok=True)
    for f in forms_dir.glob("*.pdf"):
        f.unlink()
    names = [f"{l} {t}" for l in LAST for t in TRADE]
    random.shuffle(names)
    vendors, expected, bad = [], Counter(), set()
    tins = {}
    for i in range(n):
        suffix, cls = SUFFIX[i % len(SUFFIX)]
        name = f"{names[i]} {suffix}".strip()
        k = i % 50
        tin_kind = "SSN" if cls == "Individual/sole proprietor" else "EIN"
        tin = ssn() if tin_kind == "SSN" else ein()
        line1, line2 = name, ""
        signed, dated, rev, city, show_cls = True, True, "March 2024", "Sacramento, CA 95814", True
        if k == 1:
            tin_kind, tin = "EIN", f"07-{random.randint(1000000, 9999999)}"
        elif k == 2:
            cls, tin_kind, tin = "Individual/sole proprietor", "SSN", f"666-{random.randint(10, 99)}-{random.randint(1000, 9999)}"
        elif k == 3:
            tin = None
        elif k == 4:
            cls, tin_kind, tin = "C corporation", "SSN", ssn()
        elif k == 5:
            line1, line2 = f"{random.choice(FIRST)} {random.choice(LAST)}", name   # disregarded LLC: owner on line 1
            if set(line1.lower().split()) & set(name.lower().split()):
                line1 = "Jordan Whitfield"
        elif k == 6:
            signed = False
        elif k == 7:
            show_cls = False
        elif k == 8:
            rev = "December 2014"
        elif k == 9:
            city = "Toronto, ON M5V 2T6 Canada"
        elif k == 10:
            tin_kind, tin = tins[i - 10]                    # copies a clean vendor's TIN
            expected["Duplicate TIN across vendors"] += 0   # counted once per pair below
        elif k == 11:
            dated = False
        tins[i] = (tin_kind, tin)
        if k in PLANT and k != 10:
            expected[PLANT[k]] += 1
        if k == 10:
            expected["Duplicate TIN across vendors"] += 1
        lines = ["Form W-9", f"(Rev. {rev})", "Request for Taxpayer Identification Number and Certification",
                 "1 Name of entity/individual. An entry is required.", line1,
                 "2 Business name/disregarded entity name, if different from above", line2 or "",
                 "3a Check the appropriate box for federal tax classification:"]
        if show_cls:
            lines.append(f"[X] {cls}")
        lines += ["5 Address (number, street, and apt. or suite no.)", f"{100 + i} Industrial Way",
                  "6 City, state, and ZIP code", city]
        if tin:
            lines.append(("Social security number " if tin_kind == "SSN" else "Employer identification number ") + tin)
        lines.append(f"Signature of U.S. person: {line1}" if signed else "Signature of U.S. person:")
        if dated:
            lines.append(f"Date: {random.randint(1, 12)}/{random.randint(1, 28)}/2024")
        doc = fitz.open()
        page, y = doc.new_page(), 50
        for ln in lines:
            page.insert_text((40, y), ln, fontsize=9)
            y += 14
        doc.save(forms_dir / f"W9_{i:04d}.pdf")
        vendors.append([name, "Professional services", round(random.uniform(1500, 90000), 2), date(2025, 6, 30)])
        if k in PLANT and k != 8:                           # outdated revision isn't counted as recurring
            bad.add(name)
        if k == 10:
            bad.add(vendors[i - 10][0])                     # both vendors sharing the TIN
    # Vendors paid for services with no W-9 at all
    for j in range(n, n + n // 20):
        vendors.append([f"{names[j]} Services", "Contract services", round(random.uniform(800, 40000), 2), date(2025, 9, 30)])
        bad.add(vendors[-1][0])
        expected["Missing W-9"] += 1
    # Not reportable: below threshold, or goods
    for j in range(n + n // 20, n + n // 20 + 20):
        vendors.append([f"{names[j]} Supply", "Consulting", 450.00, date(2025, 3, 31)])
    for j in range(n + n // 20 + 20, n + n // 20 + 50):
        vendors.append([f"{names[j]} Materials", "Raw materials", 25000.00, date(2025, 3, 31)])
    rows = []
    for v in vendors:
        created = date(random.randint(2019, 2025), random.randint(1, 12), random.randint(1, 28))
        first = random.choice(FIRST)
        rows.append(v + [created, f"{first} {v[0].split()[0]}", f"ap@{v[0].split()[0].lower()}{len(rows)}.example.com"])
    df = pd.DataFrame(rows, columns=["Vendor", "Category", "Amount", "Date", "Vendor Created", "Contact", "Email"])
    df.to_excel(OUT / "vendor_master.xlsx", index=False)
    new = df[pd.to_datetime(df["Vendor Created"]).dt.year == 2025]
    old = df[pd.to_datetime(df["Vendor Created"]).dt.year < 2025]
    rate = lambda d: round(100 * d["Vendor"].isin(bad).sum() / len(d), 1)
    (OUT / "expected.json").write_text(json.dumps(dict(expected), indent=2))
    (OUT / "expected_recurrence.json").write_text(json.dumps(
        {"new_vendors": len(new), "new_with_exceptions": int(new["Vendor"].isin(bad).sum()), "new_rate": rate(new),
         "older_rate": rate(old), "resolicit": len(bad) + 20}, indent=2))   # + the 20 outdated-revision vendors
    return expected


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    print(json.dumps(build(ap.parse_args().n), indent=2))
