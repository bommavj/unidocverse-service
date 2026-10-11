"""IRS Bulk TIN Matching file (Pub 2108A) from the vendor audit, and merging results back."""
import re
import stat
from pathlib import Path

import pytest

from app.services.tax.tin_matching import irs_name, read_results
from app.services.tax.vendor_audit import merge_irs_results, run_folder, write_reports

FIX = Path(__file__).parent / "fixtures" / "vendor_audit_demo" / "out"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    if not (FIX / "expected.json").exists():
        pytest.skip("run tests/fixtures/vendor_audit_demo/generate.py first")
    out = tmp_path_factory.mktemp("tm")
    return run_folder(FIX / "w9", FIX / "vendor_master.xlsx", 2025, tin_match_dir=out, stem="Test"), out


def test_irs_name():
    assert irs_name("O'Brien & Sons, Inc.") == "O Brien & Sons Inc"
    assert len(irs_name("x" * 60)) == 40


def test_submission_file(run):
    r, out = run
    t = r["tin_match"]
    sub = Path(t["files"][0])
    assert stat.S_IMODE(sub.stat().st_mode) == 0o600
    lines = sub.read_text().splitlines()
    # 1,000 W-9s − 20 missing TIN − 40 invalid format = 940 records
    assert t["records"] == len(lines) == 940 and len(t["skipped"]) == 60
    for ln in lines:
        ttype, tin, name, acct = ln.split(";")
        assert ttype in "12" and re.fullmatch(r"\d{9}", tin) and 0 < len(name) <= 40 and re.fullmatch(r"V\d{6}", acct)
        assert re.fullmatch(r"[A-Za-z0-9&\- ]+", name)


def test_no_tins_outside_submission(run):
    r, out = run
    assert not re.search(r"\b\d{9}\b|\d{3}-\d{2}-\d{4}|\d{2}-\d{7}", Path(r["tin_match"]["map"]).read_text())
    xlsx, html = write_reports(r, out, "Test")
    assert not re.search(r"\b\d{9}\b|\d{3}-\d{2}-\d{4}\b", html.read_text())


def test_results_merge(run):
    r, out = run
    sub = Path(r["tin_match"]["files"][0]).read_text().splitlines()
    # Simulated IRS reply: every 100th record mismatches, every 250th not issued
    reply = out / "results.txt"
    reply.write_text("\n".join(f"{ln};{'3' if i % 100 == 7 else '2' if i % 250 == 11 else '0'}" for i, ln in enumerate(sub)))
    res = read_results([reply], Path(r["tin_match"]["map"]))
    assert len(res) == 940 and all("tin" not in k for x in res for k in x if k != "tin_last4")
    before = r["exposure"]
    merge_irs_results(r, res)
    kinds = [e["exception"] for e in r["exceptions"]]
    assert kinds.count("IRS: name/TIN mismatch") == 10 and kinds.count("IRS: TIN not issued") == 4
    assert r["exposure"] > before
    xlsx, _ = write_reports(r, out, "Test")
    import pandas as pd
    assert len(pd.read_excel(xlsx, sheet_name="IRS TIN Matching")) == 940
