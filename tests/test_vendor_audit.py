"""Vendor compliance audit vs the 1,000-form synthetic answer key, plus TIN rules."""
import json
from pathlib import Path

import pytest

from app.services.tax.vendor_audit import run_folder, validate_tin, write_reports

FIX = Path(__file__).parent / "fixtures" / "vendor_audit_demo" / "out"


@pytest.mark.parametrize("tin,kind,ok", [
    ("84-1234567", "EIN", True), ("07-1234567", "EIN", False), ("00-1234567", "EIN", False),
    ("123-45-6789", "SSN", False),            # placeholder
    ("666-12-3456", "SSN", False), ("000-12-3456", "SSN", False), ("123-00-4567", "SSN", False),
    ("123-45-0000", "SSN", False), ("912-70-1234", "SSN", True),   # ITIN
    ("912-40-1234", "SSN", False), ("234-56-7891", "SSN", True), ("84-123456", "EIN", False)])
def test_tin_rules(tin, kind, ok):
    assert (validate_tin(tin, kind) is None) == ok


@pytest.fixture(scope="module")
def result():
    if not (FIX / "expected.json").exists():
        pytest.skip("run tests/fixtures/vendor_audit_demo/generate.py first")
    return run_folder(FIX / "w9", FIX / "vendor_master.xlsx", 2025)


def test_exceptions_match_answer_key(result):
    got = {}
    for e in result["exceptions"]:
        got[e["exception"]] = got.get(e["exception"], 0) + 1
    assert got == json.loads((FIX / "expected.json").read_text())


def test_no_full_tins_in_report(result, tmp_path):
    xlsx, html = write_reports(result, tmp_path, "Test Co")
    import re
    body = html.read_text()
    assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{2}-\d{7}\b", body)
    import pandas as pd
    for sheet in ("Exceptions", "Vendors"):
        text = pd.read_excel(xlsx, sheet_name=sheet).astype(str).to_string()
        assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{2}-\d{7}\b", text)


def test_exposure_only_reportable(result):
    rows = {v["vendor"]: v for v in result["vendors"]}
    assert all(v["backup_withholding_exposure"] == 0 for n, v in rows.items() if n.endswith(("Supply", "Materials")))
    assert result["exposure"] == pytest.approx(sum(v["backup_withholding_exposure"] for v in result["vendors"]))
