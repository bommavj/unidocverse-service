#!/usr/bin/env python3
"""
Synthetic 2025 brokerage file for "Jordan Rivera" (fictional), scored by tests/test_capital_gains.py.

  venv-arm64/bin/python tests/fixtures/cap_gains_demo/generate.py [--load]

Schwab composite 1099-B by 8949 box; the return's Form 8949 and Schedule D, with:
  A  wash sale $1,200 disallowed on the 1099-B but not added back on 8949
  B  noncovered lots reported with $0 basis (proceeds taxed in full)
  D  basis $63,500 on the return vs $61,000 reported by the broker (overstated $2,500)
  E  $12,000 of long-term noncovered proceeds missing from the return
"""
import json
import sys
from pathlib import Path

import fitz

OUT = Path(__file__).with_name("out")
TENANT = "00000000-0000-0000-0000-0000000000a1"


def pdf(name, lines):
    doc = fitz.open()
    page, y = doc.new_page(), 50
    for ln in lines:
        if y > 780:
            page, y = doc.new_page(), 50
        page.insert_text((40, y), ln, fontsize=9)
        y += 14
    doc.save(OUT / name)


def build():
    OUT.mkdir(exist_ok=True)
    pdf("Schwab 1099 Composite 2025.pdf", [
        "Form 1099-B Proceeds From Broker and Barter Exchange Transactions 2025",
        "PAYER'S name", "Charles Schwab & Co., Inc.", "RECIPIENT'S name", "Jordan Rivera",
        "SHORT-TERM TRANSACTIONS FOR COVERED TAX LOTS (Box A - basis reported to the IRS)",
        "1d Proceeds $52,400.00", "1e Cost or other basis $49,100.00", "1g Wash sale loss disallowed $1,200.00",
        "Realized gain or (loss) $4,500.00",
        "SHORT-TERM TRANSACTIONS FOR NONCOVERED TAX LOTS (Box B - basis not reported to the IRS)",
        "1d Proceeds $6,000.00", "Cost basis: not reported",
        "LONG-TERM TRANSACTIONS FOR COVERED TAX LOTS (Box D - basis reported to the IRS)",
        "1d Proceeds $80,250.00", "1e Cost or other basis $61,000.00", "Realized gain or (loss) $19,250.00",
        "LONG-TERM TRANSACTIONS FOR NONCOVERED TAX LOTS (Box E - basis not reported to the IRS)",
        "1d Proceeds $12,000.00", "Cost basis: not reported"])
    cols = "2 Totals. Add the amounts in columns (d), (e), (g), and (h)"
    pdf("Form 8949 2025 Jordan Rivera.pdf", [
        "Form 8949 Sales and Other Dispositions of Capital Assets 2025", "Jordan Rivera",
        "Part I Short-Term. Check Box A, B, or C below.", "[X] (A) Short-term transactions reported on Form(s) 1099-B showing basis was reported to the IRS",
        f"{cols}   52,400.00   49,100.00   0.00   3,300.00",
        "Part I Short-Term.", "[X] (B) Short-term transactions reported on Form(s) 1099-B showing basis wasn't reported to the IRS",
        f"{cols}   6,000.00   0.00   0.00   6,000.00",
        "Part II Long-Term.", "[X] (D) Long-term transactions reported on Form(s) 1099-B showing basis was reported to the IRS",
        f"{cols.replace('2 Totals', '4 Totals')}   80,250.00   63,500.00   0.00   16,750.00"])
    pdf("Schedule D 2025 Jordan Rivera.pdf", [
        "SCHEDULE D (Form 1040) 2025 Capital Gains and Losses", "Jordan Rivera",
        "1b Totals for all transactions reported on Form(s) 8949 with Box A checked   52,400.00   49,100.00   0.00   3,300.00",
        "2 Totals for all transactions reported on Form(s) 8949 with Box B checked   6,000.00   0.00   0.00   6,000.00",
        "8b Totals for all transactions reported on Form(s) 8949 with Box D checked   80,250.00   63,500.00   0.00   16,750.00"])
    exp = {"findings": {("A", "Wash-sale loss not added back"): 1200.0,
                        ("B", "Noncovered sales reported with $0 basis"): 6000.0,
                        ("D", "Basis higher than the broker reported"): 2500.0,
                        ("E", "Proceeds not reported"): 12000.0}}
    (OUT / "expected.json").write_text(json.dumps({f"{b}|{t}": v for (b, t), v in exp["findings"].items()}, indent=2))


def load():
    import shutil
    from sqlalchemy import text
    from app.core.database import SessionLocal
    uploads = Path.home() / "Library/Application Support/UniDocVerse/uploads"
    db = SessionLocal()
    try:
        for f in sorted(OUT.glob("*.pdf")):
            dest = uploads / f"capgains_{f.name}"
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
