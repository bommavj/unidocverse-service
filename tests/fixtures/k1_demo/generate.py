#!/usr/bin/env python3
"""
Synthetic partnership file, scored by tests/test_k1.py.

  venv-arm64/bin/python tests/fixtures/k1_demo/generate.py [--load]

Riverside Partners LP (fictional) 2025 Form 1065 + K-1s for three partners
(Jordan Rivera 10%, Maria Chen 50% + $40,000 guaranteed payments, Sam Patel 40%).
Planted:
  Sam Patel box 1 $30,000 (40% of $82,500 = $33,000) → K-1s short of Schedule K by $3,000
  Sam Patel Item L withdrawals $20,000 vs box 19 distributions $24,000
  Sam Patel ending capital $176,600 (rolls to $174,600)
  Jordan's Form 1040 (tax_recon_demo / cap_gains_demo): Schedule E line 32 $8,250 omits
  box 2 rental $1,200; box 9a long-term gain $2,000 missing from Schedule D line 12
"""
import sys
from pathlib import Path

import fitz

OUT = Path(__file__).with_name("out")
TENANT = "00000000-0000-0000-0000-0000000000a1"
ENTITY = "Riverside Partners LP"


def pdf(name, lines):
    doc = fitz.open()
    page, y = doc.new_page(), 50
    for ln in lines:
        page.insert_text((40, y), ln, fontsize=9)
        y += 14
    doc.save(OUT / name)


def k1(name, partner, pct, items, cap):
    lines = [f"Schedule K-1 (Form 1065) 2025 Partner's Share of Income, Deductions, Credits, etc.",
             "Partnership's name", ENTITY, "Partner's name", partner,
             f"J Partner's share of profit, loss, and capital: Profit {pct:.4f} % {pct:.4f} %"]
    lines += [f"{lbl} {v:,.2f}" for lbl, v in items]
    lines += ["L Partner's capital account analysis"] + [f"{lbl} {v:,.2f}" for lbl, v in cap]
    pdf(name, lines)


def build():
    OUT.mkdir(exist_ok=True)
    pdf("Form 1065 2025 Riverside Partners LP.pdf", [
        "Form 1065 U.S. Return of Partnership Income 2025", "Name of partnership", ENTITY,
        "Schedule K Partners' Distributive Share Items",
        "1 Ordinary business income (loss) 82,500.00", "2 Net rental real estate income (loss) 12,000.00",
        "4a Guaranteed payments for services 40,000.00", "4c Total guaranteed payments 40,000.00",
        "5 Interest income 3,000.00", "6a Ordinary dividends 1,500.00",
        "9a Net long-term capital gain (loss) 20,000.00",
        "14a Net earnings (loss) from self-employment 81,250.00",
        "19a Distributions of cash and marketable securities 60,000.00"])
    k1("K-1 2025 Riverside Partners.pdf", "Jordan Rivera", 10,
       [("1 Ordinary business income (loss)", 8250), ("2 Net rental real estate income (loss)", 1200),
        ("5 Interest income", 300), ("6a Ordinary dividends", 150), ("9a Net long-term capital gain (loss)", 2000),
        ("19 Distributions A", 6000)],
       [("Beginning capital account", 50000), ("Capital contributed during the year", 0),
        ("Current year net income (loss)", 11900), ("Withdrawals and distributions", 6000), ("Ending capital account", 55900)])
    k1("K-1 2025 Riverside Partners - Maria Chen.pdf", "Maria Chen", 50,
       [("1 Ordinary business income (loss)", 41250), ("2 Net rental real estate income (loss)", 6000),
        ("4a Guaranteed payments for services", 40000), ("5 Interest income", 1500), ("6a Ordinary dividends", 750),
        ("9a Net long-term capital gain (loss)", 10000), ("14 Self-employment earnings (loss) A", 81250),
        ("19 Distributions A", 30000)],
       [("Beginning capital account", 200000), ("Capital contributed during the year", 10000),
        ("Current year net income (loss)", 59500), ("Withdrawals and distributions", 30000), ("Ending capital account", 239500)])
    k1("K-1 2025 Riverside Partners - Sam Patel.pdf", "Sam Patel", 40,
       [("1 Ordinary business income (loss)", 30000), ("2 Net rental real estate income (loss)", 4800),
        ("5 Interest income", 1200), ("6a Ordinary dividends", 600), ("9a Net long-term capital gain (loss)", 8000),
        ("19 Distributions A", 24000)],
       [("Beginning capital account", 150000), ("Capital contributed during the year", 0),
        ("Current year net income (loss)", 44600), ("Withdrawals and distributions", 20000), ("Ending capital account", 176600)])


def load():
    import shutil
    from sqlalchemy import text
    from app.core.database import SessionLocal
    uploads = Path.home() / "Library/Application Support/UniDocVerse/uploads"
    db = SessionLocal()
    try:
        for f in sorted(OUT.glob("*.pdf")):
            dest = uploads / f"k1demo_{f.name}"
            shutil.copy(f, dest)
            raw = "\n".join(p.get_text() for p in fitz.open(f))
            db.execute(text("DELETE FROM documents WHERE tenant_id = :t AND filename = :n"), {"t": TENANT, "n": f.name})
            db.execute(text("INSERT INTO documents (filename, file_path, tenant_id, status, raw_text) "
                            "VALUES (:n, :p, :t, 'completed', :r)"), {"n": f.name, "p": str(dest), "t": TENANT, "r": raw})
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    build()
    if "--load" in sys.argv:
        load()
        print("loaded into", TENANT)
