#!/usr/bin/env python3
"""
Synthetic tax file with planted mismatches, scored by tests/test_tax_reconcile.py.

  venv-arm64/bin/python tests/fixtures/tax_recon_demo/generate.py          # write files
  venv-arm64/bin/python tests/fixtures/tax_recon_demo/generate.py --load   # + add to the demo tenant

Taxpayer "Jordan Rivera", 2025 (fictional):
  W-2 Acme Robotics (correct), W-2 Bay Analytics (Social Security tax $36 too high),
  1099-INT, 1099-DIV, 1099-NEC, 1099-R, K-1 (Form 1065), Form 1040, Schedules C and E.
  Planted: 1040 line 2b interest $1,000 short; Schedule C gross receipts $3,000 short.
Business "Delta Precision Manufacturing Inc" (fictional) 2025 vendor payments:
  AP ledger + W-9s + 1099-NECs issued.
  Planted: Nguyen Consulting (no W-9, no 1099), Bright Janitorial (W-9 unsigned,
  no 1099), Harbor Medical Group Inc (1099 amount wrong), Summit IT Services Inc
  (corporation, exempt), Lopez Design Studio (below threshold), Apex Legal (clean).
"""
import json
import sys
from datetime import date
from pathlib import Path

import fitz
import pandas as pd

OUT = Path(__file__).with_name("out")
TENANT = "00000000-0000-0000-0000-0000000000a1"
YEAR = 2025


def pdf(name: str, lines):
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for ln in lines:
        page.insert_text((50, y), ln, fontsize=10)
        y += 16
    doc.save(OUT / name)


def w2(name, employer, wages, fed, ssw, sst, mw, mt):
    pdf(name, [f"Form W-2 Wage and Tax Statement {YEAR}", "Copy B To Be Filed With Employee's FEDERAL Tax Return",
               "c Employer's name, address, and ZIP code", employer, "e Employee's name", "Jordan Rivera",
               f"1 Wages, tips, other compensation {wages:,.2f}", f"2 Federal income tax withheld {fed:,.2f}",
               f"3 Social security wages {ssw:,.2f}", f"4 Social security tax withheld {sst:,.2f}",
               f"5 Medicare wages and tips {mw:,.2f}", f"6 Medicare tax withheld {mt:,.2f}"])


def info_return(name, title, payer, recipient, boxes):
    pdf(name, [f"{title} {YEAR}", "PAYER'S name, street address, city", payer, "RECIPIENT'S name", recipient]
        + [f"{b} ${v:,.2f}" for b, v in boxes])


def w9(name, legal, cls, tin, signer):
    lines = ["Form W-9 (Rev. March 2024)", "Request for Taxpayer Identification Number and Certification",
             "1 Name of entity/individual. An entry is required.", legal,
             "3a Check the appropriate box for federal tax classification:", f"[X] {cls}"]
    if tin:
        lines.append(f"Employer identification number {tin}")
    lines.append(f"Signature of U.S. person: {signer}" if signer else "Signature of U.S. person:")
    lines.append("Date: 01/15/2025")
    pdf(name, lines)


def build():
    OUT.mkdir(exist_ok=True)
    # Taxpayer
    w2("W-2 2025 Acme Robotics.pdf", "Acme Robotics Inc", 118400, 17760, 125000, 7750.00, 125000, 1812.50)
    w2("W-2 2025 Bay Analytics.pdf", "Bay Analytics LLC", 21300, 2130, 22000, 1400.00, 22000, 319.00)
    info_return("1099-INT 2025 Golden Bank.pdf", "Form 1099-INT Interest Income", "Golden Bank", "Jordan Rivera",
                [("1 Interest income", 1842.37)])
    info_return("1099-DIV 2025 Vanguard.pdf", "Form 1099-DIV Dividends and Distributions", "Vanguard Brokerage",
                "Jordan Rivera", [("1a Total ordinary dividends", 3105.40), ("1b Qualified dividends", 2880.12)])
    info_return("1099-NEC 2025 Jordan Rivera.pdf", "Form 1099-NEC Nonemployee Compensation",
                "Delta Precision Manufacturing Inc", "Jordan Rivera", [("1 Nonemployee compensation", 15000.00)])
    info_return("1099-R 2025 Fidelity.pdf", "Form 1099-R Distributions From Pensions, Annuities", "Fidelity Investments",
                "Jordan Rivera", [("1 Gross distribution", 10000.00), ("2a Taxable amount", 10000.00),
                                  ("4 Federal income tax withheld", 2000.00)])
    pdf("K-1 2025 Riverside Partners.pdf", [f"Schedule K-1 (Form 1065) {YEAR} Partner's Share of Income, Deductions, Credits",
                                            "Partnership's name", "Riverside Partners LP", "Partner's name", "Jordan Rivera",
                                            "1 Ordinary business income (loss) 8,250.00"])
    pdf("Form 1040 2025 Jordan Rivera.pdf", [
        f"Form 1040 U.S. Individual Income Tax Return {YEAR}", "Jordan Rivera",
        "1a Total amount from Form(s) W-2, box 1   1a   139,700.00",
        "2b Taxable interest   2b   842.37",
        "3b Ordinary dividends   3b   3,105.40",
        "5a Pensions and annuities 10,000.00 5b Taxable amount   5b   10,000.00",
        "8 Additional income from Schedule 1, line 10   8   20,250.00",
        "9 Add lines 1z, 2b, 3b, 4b, 5b, 6b, 7, and 8. This is your total income   9   173,897.77",
        "25a Form(s) W-2   25a   19,890.00",
        "25b Form(s) 1099   25b   2,000.00"])
    pdf("Schedule C 2025 Jordan Rivera.pdf", [f"SCHEDULE C (Form 1040) {YEAR} Profit or Loss From Business",
                                              "Jordan Rivera", "1 Gross receipts or sales   1   12,000.00"])
    pdf("Schedule E 2025 Jordan Rivera.pdf", [f"SCHEDULE E (Form 1040) {YEAR} Supplemental Income and Loss",
                                              "Jordan Rivera", "32 Total partnership and S corporation income   32   8,250.00"])

    # Business vendor payments
    rows = []
    def pay(vendor, desc, cat, amounts, start_month=1):
        for i, a in enumerate(amounts):
            m = (start_month + i - 1) % 12 + 1
            rows.append([date(YEAR, m, 15), f"VP-{len(rows) + 9001}", vendor, desc, cat, "Sacramento", "CA",
                         a, 0.0, 0.0, 0.0, 0.0])
    pay("Apex Legal Services LLP", "Legal services - contracts", "Professional services", [12000] * 4, 2)
    pay("Nguyen Consulting", "Process consulting", "Consulting", [7500] * 3, 3)
    pay("Bright Janitorial", "Janitorial service", "Facilities service", [800] * 12)
    pay("Summit IT Services Inc", "IT support retainer", "IT services", [2500] * 12)
    pay("Lopez Design Studio", "Logo design", "Design", [450], 6)
    pay("Harbor Medical Group Inc", "Employee medical exams", "Medical services", [1500] * 4, 4)
    pay("Grainger", "Shop supplies", "Supplies", [5000] * 10)
    pd.DataFrame(rows, columns=["Invoice Date", "Invoice", "Vendor", "Description", "Category", "Ship-To City",
                                "Ship-To State", "Amount", "Freight", "Labor", "Sales Tax Charged",
                                "Use Tax Accrued"]).to_excel(OUT / "ap_vendor_payments_2025.xlsx", index=False)
    w9("W-9 Apex Legal Services LLP.pdf", "Apex Legal Services LLP", "Partnership", "84-1234567", "Maria Apex")
    w9("W-9 Bright Janitorial.pdf", "Bright Janitorial", "Individual/sole proprietor", "123-45-6789", None)
    w9("W-9 Harbor Medical Group Inc.pdf", "Harbor Medical Group Inc", "C corporation", "94-7654321", "Dr. Lee Harbor")
    payer = "Delta Precision Manufacturing Inc"
    info_return("1099-NEC 2025 Apex Legal Services LLP.pdf", "Form 1099-NEC Nonemployee Compensation", payer,
                "Apex Legal Services LLP", [("1 Nonemployee compensation", 48000.00)])
    info_return("1099-NEC 2025 Harbor Medical Group Inc.pdf", "Form 1099-NEC Nonemployee Compensation", payer,
                "Harbor Medical Group Inc", [("1 Nonemployee compensation", 5000.00)])

    expected = {
        "w2_findings": {"W-2 2025 Bay Analytics.pdf": [{"check": "Social Security tax", "amount": 36.00}],
                        "W-2 2025 Acme Robotics.pdf": []},
        "return_findings": [{"line": "Taxable interest (line 2b)", "difference": 1000.00},
                            {"line": "Schedule C gross receipts (line 1)", "difference": 3000.00}],
        "vendors": {"Apex Legal Services LLP": "ok", "Nguyen Consulting": "issue", "Bright Janitorial": "issue",
                    "Harbor Medical Group Inc": "issue", "Summit IT Services Inc": "exempt",
                    "Lopez Design Studio": "below threshold"},
        "backup_withholding_exposure": round((22500 + 9600) * 0.24, 2),
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2))
    return expected


def load():
    """Add the files to the demo tenant with their PDF text, as the fast parsing path would store it."""
    import shutil
    from sqlalchemy import text
    from app.core.database import SessionLocal
    uploads = Path.home() / "Library/Application Support/UniDocVerse/uploads"
    db = SessionLocal()
    try:
        for f in sorted(OUT.iterdir()):
            if f.suffix not in (".pdf", ".xlsx"):
                continue
            dest = uploads / f"taxrecon_{f.name}"
            shutil.copy(f, dest)
            raw = "\n".join(p.get_text() for p in fitz.open(f)) if f.suffix == ".pdf" else None
            db.execute(text("DELETE FROM documents WHERE tenant_id = :t AND filename = :n"), {"t": TENANT, "n": f.name})
            db.execute(text("INSERT INTO documents (filename, file_path, tenant_id, status, raw_text) "
                            "VALUES (:n, :p, :t, 'completed', :r)"), {"n": f.name, "p": str(dest), "t": TENANT, "r": raw})
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(build(), indent=2)[:400])
    if "--load" in sys.argv:
        load()
        print("loaded into", TENANT)
