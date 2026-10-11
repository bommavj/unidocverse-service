"""K-1 reconciliation vs the synthetic partnership file (planted errors in tests/fixtures/k1_demo/generate.py)."""
from pathlib import Path

import fitz
import pytest

from app.services.tax.k1 import check_entity, check_recipient, parse_entity_return, parse_k1

FIX = Path(__file__).parent / "fixtures"


def _t(path):
    return "\n".join(p.get_text() for p in fitz.open(path))


@pytest.fixture(scope="module")
def data():
    out = FIX / "k1_demo" / "out"
    if not out.exists():
        pytest.skip("run tests/fixtures/k1_demo/generate.py first")
    ent = parse_entity_return(_t(out / "Form 1065 2025 Riverside Partners LP.pdf"))
    ent["filename"] = "1065"
    k1s = []
    for f in sorted(out.glob("K-1*.pdf")):
        k = parse_k1(_t(f)); k["filename"] = f.name; k1s.append(k)
    return ent, k1s


def test_parse(data):
    ent, k1s = data
    assert ent["entity"] == "Riverside Partners LP"
    assert ent["items"]["ordinary_income"] == 82500 and ent["items"]["distributions"] == 60000
    assert ent["items"]["se_earnings"] == 81250 and ent["items"]["guaranteed_payments"] == 40000
    maria = next(k for k in k1s if k["partner"] == "Maria Chen")
    assert maria["profit_pct"] == 50 and maria["items"]["guaranteed_payments"] == 40000
    assert maria["items"]["distributions"] == 30000 and maria["capital"]["ending"] == 239500
    assert maria["entity"] == "Riverside Partners LP"


def test_entity_tieout(data):
    ent, k1s = data
    got = {(f["type"], f["partner"], f["amount"]) for f in check_entity(ent, k1s)}
    assert got == {
        ("K-1s don't add up to Schedule K", "all", 3000.0),
        ("Allocation differs from profit share", "Sam Patel", -3000.0),
        ("Capital account doesn't roll forward", "Sam Patel", 2000.0),
        ("Withdrawals ≠ distributions", "Sam Patel", -4000.0),
    }


def test_recipient(data):
    _, k1s = data
    jordan = [k for k in k1s if k["partner"] == "Jordan Rivera"]
    returns = {"sched_e": _t(FIX / "tax_recon_demo/out/Schedule E 2025 Jordan Rivera.pdf"), "sched_e_file": "E",
               "sched_d": _t(FIX / "cap_gains_demo/out/Schedule D 2025 Jordan Rivera.pdf"), "sched_d_file": "D"}
    got = {(f["type"], f["amount"]) for f in check_recipient(jordan, returns)}
    assert got == {("Schedule E ≠ K-1 income", 1200.0), ("K-1 capital gain not on Schedule D", 2000.0)}


def test_se_required():
    k = {"filename": "k", "items": {"ordinary_income": 1000.0, "se_earnings": 50000.0}}
    got = {f["type"] for f in check_recipient([k], {})}
    assert "Schedule SE missing" in got and "Schedule E missing" in got
