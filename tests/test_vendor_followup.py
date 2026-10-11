"""New-vendor exception rate and the W-9 re-solicitation export vs the synthetic answer key."""
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from app.services.tax.vendor_audit import new_vendor_rate, resolicitation_list, run_folder, write_reports

FIX = Path(__file__).parent / "fixtures" / "vendor_audit_demo" / "out"


@pytest.fixture(scope="module")
def r():
    if not (FIX / "expected_recurrence.json").exists():
        pytest.skip("run tests/fixtures/vendor_audit_demo/generate.py first")
    return run_folder(FIX / "w9", FIX / "vendor_master.xlsx", 2025)


def test_new_vendor_rate(r):
    exp = json.loads((FIX / "expected_recurrence.json").read_text())
    nv = new_vendor_rate(r)
    assert nv["date_source"] == "vendor created date" and nv["unknown_dates"] == 0
    for k in ("new_vendors", "new_with_exceptions", "new_rate", "older_rate"):
        assert nv[k] == exp[k], k


def test_resolicitation(r):
    exp = json.loads((FIX / "expected_recurrence.json").read_text())
    rows = resolicitation_list(r, "Northwind Fabrication Inc")
    assert len(rows) == exp["resolicit"]
    assert len({x["vendor"] for x in rows}) == len(rows)                 # one row per vendor
    assert [x["priority"] for x in rows] == sorted((x["priority"] for x in rows), key=["high", "medium", "low"].index)
    foreign = [x for x in rows if "outside the U.S." in x["reasons"]]
    assert foreign and all(x["request"] == "Form W-8BEN / W-8BEN-E" for x in foreign)
    missing = next(x for x in rows if "no Form W-9 on file" in x["reasons"])
    assert missing["email"].endswith(".example.com") and "24%" in missing["message"]
    assert "fw9.pdf" in missing["message"] and "January 15, 2026" in missing["message"]


def test_reports_have_both_and_no_tins(r, tmp_path):
    xlsx, html = write_reports(r, tmp_path, "Northwind Fabrication Inc")
    csv = tmp_path / "W9_Resolicitation_Northwind_Fabrication_Inc_2025.csv"
    assert csv.exists() and len(pd.read_csv(csv)) == len(pd.read_excel(xlsx, sheet_name="Re-solicitation"))
    assert "Is it still happening?" in html.read_text()
    for text in (csv.read_text(), html.read_text()):
        assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{2}-\d{7}\b|\b\d{9}\b", text)
