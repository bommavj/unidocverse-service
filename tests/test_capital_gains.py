"""1099-B ↔ Form 8949 ↔ Schedule D vs the synthetic brokerage file's answer key."""
import json
from pathlib import Path

import fitz
import pytest

from app.services.tax.capital_gains import parse_1099b, parse_8949, parse_schedule_d, reconcile

FIX = Path(__file__).parent / "fixtures" / "cap_gains_demo" / "out"


def _t(name):
    return "\n".join(p.get_text() for p in fitz.open(FIX / name))


@pytest.fixture(scope="module")
def parsed():
    if not (FIX / "expected.json").exists():
        pytest.skip("run tests/fixtures/cap_gains_demo/generate.py first")
    return (parse_1099b(_t("Schwab 1099 Composite 2025.pdf")), parse_8949(_t("Form 8949 2025 Jordan Rivera.pdf")),
            parse_schedule_d(_t("Schedule D 2025 Jordan Rivera.pdf")))


def test_1099b(parsed):
    b = parsed[0]
    assert set(b) == {"A", "B", "D", "E"}
    assert b["A"] == {"proceeds": 52400.0, "basis": 49100.0, "wash": 1200.0, "basis_reported": True}
    assert b["B"]["basis_reported"] is False and b["E"]["proceeds"] == 12000.0
    assert b["D"]["basis"] == 61000.0


def test_8949_and_schedule_d(parsed):
    _, f, d = parsed
    assert f["A"] == {"proceeds": 52400.0, "basis": 49100.0, "adjustment": 0.0, "gain": 3300.0}
    assert f["D"]["basis"] == 63500.0 and "E" not in f
    assert d["8b"]["gain"] == 16750.0 and d["2"]["proceeds"] == 6000.0


def test_findings_match_answer_key(parsed):
    b, f, d = parsed
    r = reconcile([{"filename": "1099b", "data": b}], [{"filename": "8949", "data": f}],
                  [{"filename": "schedD", "data": d}])
    got = {f"{x['box']}|{x['type']}": x["amount"] for x in r["findings"]}
    assert got == json.loads((FIX / "expected.json").read_text())


def test_clean_return_has_no_findings(parsed):
    b, _, _ = parsed
    f = {box: {"proceeds": t["proceeds"], "basis": t["basis"], "adjustment": t["wash"],
               "gain": t["proceeds"] - t["basis"] + t["wash"]} for box, t in b.items()}
    f["B"]["basis"] = 5000.0; f["E"]["basis"] = 9000.0      # taxpayer documented noncovered basis
    r = reconcile([{"filename": "1099b", "data": b}], [{"filename": "8949", "data": f}], [])
    assert r["findings"] == []


def test_basis_understated_is_a_refund():
    b = {"D": {"proceeds": 10000.0, "basis": 7000.0, "wash": 0.0, "basis_reported": True}}
    f = {"D": {"proceeds": 10000.0, "basis": 4000.0, "adjustment": 0.0, "gain": 6000.0}}
    r = reconcile([{"filename": "b", "data": b}], [{"filename": "f", "data": f}], [])
    assert r["findings"][0]["type"] == "Basis lower than the broker reported" and r["findings"][0]["amount"] == 3000.0
