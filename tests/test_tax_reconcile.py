"""IRS form extraction and cross-document checks vs the synthetic tax file's answer key."""
import json
from pathlib import Path

import fitz
import pandas as pd
import pytest

from app.services.tax.irs_forms import classify_form, detect_forms, extract_forms, parse_w9
from app.services.tax.reconcile import check_returns, check_vendors, check_w2
from app.services.tax.reverse_audit import map_columns

FIX = Path(__file__).parent / "fixtures" / "tax_recon_demo" / "out"


def _text(name):
    return "\n".join(p.get_text() for p in fitz.open(FIX / name))


@pytest.fixture(scope="module")
def data():
    if not (FIX / "expected.json").exists():
        pytest.skip("run tests/fixtures/tax_recon_demo/generate.py first")
    forms, w9s = [], []
    for f in sorted(FIX.glob("*.pdf")):
        body = _text(f.name)
        for x in extract_forms(body):
            x["filename"] = f.name
            if x["form"] == "w9":
                w9s.append({**parse_w9(body), "filename": f.name})
            else:
                forms.append(x)
    df = pd.read_excel(FIX / "ap_vendor_payments_2025.xlsx")
    led = [{"filename": "ap_vendor_payments_2025.xlsx", "df": df, "cols": map_columns(df)}]
    return forms, w9s, led, json.loads((FIX / "expected.json").read_text())


def test_classify_headers(data):
    expect = {"W-2 2025 Acme Robotics.pdf": "w2", "1099-INT 2025 Golden Bank.pdf": "form_1099_int",
              "1099-DIV 2025 Vanguard.pdf": "form_1099_div", "1099-R 2025 Fidelity.pdf": "form_1099_r",
              "1099-NEC 2025 Jordan Rivera.pdf": "form_1099_nec", "K-1 2025 Riverside Partners.pdf": "schedule_k1",
              "Form 1040 2025 Jordan Rivera.pdf": "form_1040", "Schedule C 2025 Jordan Rivera.pdf": "form_1040",
              "W-9 Apex Legal Services LLP.pdf": "w9"}
    for name, t in expect.items():
        assert classify_form(_text(name)) == t, name


def test_k1_not_misread_as_1065():
    assert detect_forms("Schedule K-1 (Form 1065) 2025 Partner's Share") == ["k1"]


def test_boxes(data):
    forms = data[0]
    by = {f["filename"]: f for f in forms}
    assert by["W-2 2025 Acme Robotics.pdf"]["boxes"] == {
        "wages": 118400.0, "federal_wh": 17760.0, "ss_wages": 125000.0, "ss_tax": 7750.0,
        "medicare_wages": 125000.0, "medicare_tax": 1812.5}
    assert by["1099-INT 2025 Golden Bank.pdf"]["boxes"]["interest"] == 1842.37
    assert by["1099-R 2025 Fidelity.pdf"]["boxes"] == {"gross_distribution": 10000.0, "taxable_amount": 10000.0,
                                                       "federal_wh": 2000.0}
    assert by["K-1 2025 Riverside Partners.pdf"]["boxes"]["ordinary_income"] == 8250.0
    ret = by["Form 1040 2025 Jordan Rivera.pdf"]["boxes"]
    assert ret["wages"] == 139700.0 and ret["taxable_interest"] == 842.37 and ret["w2_withholding"] == 19890.0
    assert ret["1099_withholding"] == 2000.0 and ret["pensions_taxable"] == 10000.0


def test_w2_checks(data):
    forms, _, _, exp = data
    for f in forms:
        if f["form"] != "w2":
            continue
        got = [x for x in check_w2(f, []) if not x.get("info")]
        want = exp["w2_findings"][f["filename"]]
        assert len(got) == len(want), (f["filename"], got)
        for g, w in zip(got, want):
            assert w["check"] in g["check"] and g["amount"] == pytest.approx(w["amount"])


def test_returns_vs_information_returns(data):
    forms, _, _, exp = data
    got = {(x["line"], x["difference"]) for x in check_returns(forms)}
    assert got == {(w["line"], w["difference"]) for w in exp["return_findings"]}


def test_vendor_compliance(data):
    forms, w9s, led, exp = data
    res = {v["vendor"]: v for v in check_vendors(led, forms, w9s, 2025)}
    assert "Grainger" not in res                      # goods, not services
    for vendor, status in exp["vendors"].items():
        assert res[vendor]["status"].startswith(status), (vendor, res[vendor])
    assert sum(v["backup_withholding_exposure"] for v in res.values()) == pytest.approx(exp["backup_withholding_exposure"])
    assert any("≠ paid" in i for i in res["Harbor Medical Group Inc"]["issues"])
