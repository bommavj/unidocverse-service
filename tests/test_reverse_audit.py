"""Reverse audit engine vs the synthetic manufacturer ledger's answer key."""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.services.tax.reverse_audit import audit_frame, ca_rate, map_columns, refund_deadline

FIX = Path(__file__).parent / "fixtures" / "reverse_audit_demo" / "out"


@pytest.fixture(scope="module")
def result():
    if not (FIX / "expected.json").exists():
        pytest.skip("run tests/fixtures/reverse_audit_demo/generate.py first")
    df = pd.read_excel(FIX / "ap_purchase_ledger_2023_2025.xlsx")
    expected = json.loads((FIX / "expected.json").read_text())
    a = audit_frame(df, map_columns(df), "ap_purchase_ledger_2023_2025.xlsx",
                    today=date.fromisoformat(expected["as_of"]))
    return a, expected


def test_columns_mapped():
    df = pd.read_excel(FIX / "ap_purchase_ledger_2023_2025.xlsx", nrows=2)
    c = map_columns(df)
    assert c["tax"] == "Sales Tax Charged" and c["use_tax"] == "Use Tax Accrued"
    assert c["city"] == "Ship-To City" and c["state"] == "Ship-To State" and c["amount"] == "Amount"


def test_totals_match_answer_key(result):
    a, exp = result
    assert a["lines"] == exp["total"]["lines"]
    assert a["total_refund"] == pytest.approx(exp["total"]["refund"], abs=1.0)
    assert a["expired_refund"] == pytest.approx(exp["total"]["expired_refund"], abs=1.0)
    assert a["total_exposure"] == pytest.approx(exp["total"]["exposure"], abs=1.0)


def test_no_false_positives_on_correct_lines(result):
    a, _ = result
    flagged_vendors = {r["vendor"] for r in a["refunds"]} | {e["vendor"] for e in a["exposure"]}
    assert not flagged_vendors & {"Grainger", "Uline", "Staples Business", "Fastenal"}


def test_rules():
    assert ca_rate("Los Angeles", date(2025, 3, 31)) == 0.095
    assert ca_rate("Los Angeles", date(2025, 4, 1)) == 0.0975
    assert ca_rate("Nowhere", date(2025, 1, 1)) is None
    # Q1 2023 return due 4/30/2023 → claim by 4/30/2026
    assert refund_deadline(date(2023, 2, 10)) == date(2026, 4, 30)
    assert refund_deadline(date(2025, 11, 5)) == date(2029, 1, 31)
