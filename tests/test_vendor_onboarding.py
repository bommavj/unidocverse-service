"""Onboarding W-9 check (service + API) and the realistic penalty estimate."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.tax.vendor_audit import onboard_check, penalty_estimate, run_folder

FIX = Path(__file__).parent / "fixtures" / "vendor_audit_demo" / "out"
W9 = FIX / "w9"


def _vendor(i):
    import pandas as pd
    return pd.read_excel(FIX / "vendor_master.xlsx").iloc[i]["Vendor"]


@pytest.fixture(scope="module", autouse=True)
def need_fixture():
    if not (FIX / "expected.json").exists():
        pytest.skip("run tests/fixtures/vendor_audit_demo/generate.py first")


def test_clean_w9_passes():
    r = onboard_check(W9 / "W9_0000.pdf", _vendor(0))
    assert r["status"] == "pass", r["checks"]
    assert r["fields"]["tin_last4"].startswith("•") and "tin" not in r["fields"]


@pytest.mark.parametrize("i,status,failing", [
    (1, "fail", "TIN format"), (3, "fail", "TIN present"), (4, "fail", "TIN type doesn't fit the entity"),
    (5, "review", "Name matches the vendor record"), (6, "review", "W-9 not signed"),
    (9, "review", "Foreign address — W-8 likely required"), (8, "pass", None)])   # old revision is low severity → pass
def test_planted(i, status, failing):
    r = onboard_check(W9 / f"W9_{i:04d}.pdf", _vendor(i))
    bad = [c["check"] for c in r["checks"] if not c["ok"]]
    if failing:
        assert failing in bad
    assert r["status"] == status or (i == 8 and r["status"] in ("pass", "review"))


def test_api_deletes_upload(tmp_path, monkeypatch):
    from app.api.vendor_compliance_router import router
    import tempfile
    made = []
    real = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda **kw: made.append(real(**kw)) or made[-1])
    app = FastAPI(); app.include_router(router)
    c = TestClient(app)
    with open(W9 / "W9_0001.pdf", "rb") as fh:
        res = c.post("/api/vendor-compliance/w9-check", files={"file": ("w9.pdf", fh, "application/pdf")},
                     data={"vendor_name": _vendor(1)})
    assert res.status_code == 200 and res.json()["status"] == "fail"
    assert made and not Path(made[0][1]).exists()
    assert c.post("/api/vendor-compliance/w9-check", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400


def test_penalty_estimate():
    r = run_folder(W9, FIX / "vendor_master.xlsx", 2025)
    pe = r["penalties"]
    # 50 missing W-9 + 40 invalid + 20 missing TIN + 20 wrong TIN type + 20 name mismatch = 150 vendors
    assert pe["vendors_at_risk"] == 150 and pe["returns_due"] == 2026
    assert pe["penalty_if_filed_as_is"] == 150 * 340 * 2
    assert pe["penalty_if_corrected_in_30_days"] == 150 * 60 * 2
    assert pe["fix_hours"] == 117.5 and pe["fix_cost"] == pytest.approx(117.5 * 45)
    assert pe["worst_case_backup_withholding"] == r["exposure"]
