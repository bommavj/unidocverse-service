"""Organizer: received vs expected forms, from prior-year payers, pay stubs, statements and last year's 1040."""
from datetime import date

from app.services.tax.organizer import organize


def form(title, year, payer, recipient, *boxes):
    return "\n".join([f"{title} {year}", "PAYER'S name", payer, "RECIPIENT'S name", recipient]
                     + [f"{b} ${v:,.2f}" for b, v in boxes])


def w2(year, employer, wages):
    return "\n".join([f"Form W-2 Wage and Tax Statement {year}", "c Employer's name", employer,
                      "e Employee's name", "Jordan Rivera", f"1 Wages, tips, other compensation {wages:,.2f}"])


DOCS = [
    # 2024
    {"filename": "W-2 2024 Acme.pdf", "raw_text": w2(2024, "Acme Robotics Inc", 110000)},
    {"filename": "1099-INT 2024 Golden.pdf", "raw_text": form("Form 1099-INT", 2024, "Golden Bank", "Jordan Rivera", ("1 Interest income", 900))},
    {"filename": "1099-DIV 2024 Vanguard.pdf", "raw_text": form("Form 1099-DIV", 2024, "Vanguard Brokerage", "Jordan Rivera", ("1a Total ordinary dividends", 2500))},
    {"filename": "1098 2024 Wells.pdf", "raw_text": form("Form 1098 Mortgage Interest Statement", 2024, "Wells Fargo Home Mortgage", "Jordan Rivera", ("1 Mortgage interest received", 14000))},
    {"filename": "1040 2024.pdf", "raw_text": "Form 1040 U.S. Individual Income Tax Return 2024\nJordan Rivera\n"
                                              "2b Taxable interest 2b 900.00\n3b Ordinary dividends 3b 2,500.00\n"
                                              "5a Pensions and annuities 5b Taxable amount 5b 4,000.00"},
    # 2025: Acme W-2 and Golden 1099-INT arrived; Vanguard, 1098 and pension 1099-R didn't
    {"filename": "W-2 2025 Acme.pdf", "raw_text": w2(2025, "Acme Robotics Inc", 118400)},
    {"filename": "1099-INT 2025 Golden.pdf", "raw_text": form("Form 1099-INT", 2025, "Golden Bank", "Jordan Rivera", ("1 Interest income", 1842.37))},
    {"filename": "1099-NEC 2025 Delta.pdf", "raw_text": form("Form 1099-NEC", 2025, "Delta Precision Manufacturing Inc", "Jordan Rivera", ("1 Nonemployee compensation", 15000))},
    # A 1099-NEC the user's business issued to a vendor — not the taxpayer's income
    {"filename": "1099-NEC 2025 Apex (issued).pdf", "raw_text": form("Form 1099-NEC", 2025, "Delta Precision Manufacturing Inc", "Apex Legal Services LLP", ("1 Nonemployee compensation", 48000))},
    {"filename": "Chase Dec 2025.pdf", "doc_type": "bank_statement", "raw_text": "",
     "facts": {"bank_name": "Chase", "statement_period": "12/01/2025 - 12/31/2025"}},
]
STUBS = [{"pay_date": date(2025, m, 15), "facts": {"employer": "Bay Analytics LLC"}} for m in range(1, 13)]


def run(today=date(2026, 2, 20)):
    return organize(DOCS, STUBS, 2025, today=today)


def _exp(o):
    return {(e["form"], e["payer"]) for e in o["expected"]}


def test_received_only_taxpayers_forms():
    o = run()
    assert o["taxpayer"] == "jordan rivera"
    assert sorted(f["filename"] for f in o["received"]) == ["1099-INT 2025 Golden.pdf", "1099-NEC 2025 Delta.pdf",
                                                            "W-2 2025 Acme.pdf"]


def test_expected_forms():
    e = _exp(run())
    assert ("1099_div", "Vanguard Brokerage") in e              # same payer last year
    assert ("1098", "Wells Fargo Home Mortgage") in e
    assert ("w2", "Bay Analytics LLC") in e                     # pay stubs, no W-2
    assert ("1099_r", None) in e                                # last year's 1040 line 5b
    assert ("1099_int", "Chase") in e                           # bank statement
    assert not any(f == "w2" and p and "acme" in p.lower() for f, p in e)
    assert not any(f == "1099_int" and p and "golden" in p.lower() for f, p in e)


def test_overdue_by_furnish_date():
    o = run(today=date(2026, 2, 20))
    due = {e["form"]: e["overdue"] for e in o["expected"]}
    assert due["1099_div"] is True and due["w2"] is True         # Jan 31
    o = run(today=date(2026, 1, 10))
    assert not any(e["overdue"] for e in o["expected"])


def test_schedules():
    s = {x["form"]: x["schedule"] for x in run()["schedules"]}
    assert "Schedule C" in s["1099_nec"]
