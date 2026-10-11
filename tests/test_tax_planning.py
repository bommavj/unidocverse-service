"""Federal calculator vs hand-worked 2025 figures, and strategy pricing."""
import pytest

from app.services.tax.planning import compute_tax, strategies


def test_wages_only_single_2025():
    # 100,000 − 15,750 = 84,250 → 1,192.50 + 4,386.00 + 22% × 35,775 = 13,449.00
    t = compute_tax({"wages": 100000}, 2025, "single")
    assert t["taxable"] == 84250 and t["total"] == pytest.approx(13449.00)
    assert t["marginal_rate"] == 0.22


def test_long_term_gains_stack_on_ordinary():
    # ordinary taxable 24,250 → 2,671.50; gains 24,100 at 0% (to 48,350) + 5,900 at 15% = 885
    t = compute_tax({"wages": 40000, "lt_gain": 30000}, 2025, "single")
    assert t["ordinary_tax"] == pytest.approx(2671.50) and t["cg_tax"] == pytest.approx(885.00)


def test_self_employment_tax():
    # SE net 92,350 → 11,451.40 + 2,678.15 = 14,129.55; half deducted → taxable 77,185.23
    t = compute_tax({"se_income": 100000}, 2025, "single")
    assert t["se_tax"] == pytest.approx(14129.55, abs=0.01)
    assert t["total"] == pytest.approx(14129.55 + 11894.75, abs=0.02)


def test_niit_and_mfj():
    t = compute_tax({"interest": 300000}, 2025, "single")
    assert t["niit"] == pytest.approx(3800.00)
    assert compute_tax({"wages": 100000}, 2025, "mfj")["total"] < compute_tax({"wages": 100000}, 2025, "single")["total"]


def test_capital_loss_limit():
    t = compute_tax({"wages": 50000, "st_gain": -10000}, 2025, "single")
    assert t["agi"] == 47000


def test_strategies_priced():
    p = {"wages": 150000, "pretax_401k": 10000, "lt_gain": 20000, "se_income": 90000, "withholding": 20000}
    s = {x["strategy"]: x for x in strategies(p, 2026, "single")}
    k = s["Max out your 401(k)"]
    assert k["amount"] == 14500                                     # 24,500 − 10,000
    base = compute_tax(p, 2026, "single")["total"]
    after = compute_tax({**p, "wages": 135500, "pretax_401k": 24500}, 2026, "single")["total"]
    assert k["saving"] == pytest.approx(base - after)
    assert s["Elect S-corporation status for self-employment income"]["saving"] > 0
    assert "Pay estimated tax to the safe harbor" in s
