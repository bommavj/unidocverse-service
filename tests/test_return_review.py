"""One-click review over the demo tenant (loaded by the tax_recon / cap_gains / k1 demo generators)."""
import pytest

from app.services.tax.return_review import answer_return_review, run_review

TENANT = "00000000-0000-0000-0000-0000000000a1"


@pytest.fixture(scope="module")
def review():
    r = run_review(TENANT, 2025)
    if not r["has_return"]:
        pytest.skip("load the demo fixtures with --load first")
    return r


def test_verdict_and_totals(review):
    assert review["verdict"] == "Not ready to file"
    # 12,000 + 3,000 + 2,500 + 2,000 + 1,300 + 1,200 (wash) + 1,200 (Schedule E, once) + 150
    assert review["at_risk"] == pytest.approx(23350.0)
    assert review["opportunity"] == pytest.approx(6000.0)


def test_no_double_counting(review):
    sched_e = [f for f in review["findings"] if "Schedule E" in f["title"]]
    assert len(sched_e) == 1 and sched_e[0]["area"] == "K-1"


def test_every_check_ran(review):
    assert all(c["ran"] for c in review["checks"])
    assert {c["check"]: c["findings"] for c in review["checks"]}["1099-B vs Form 8949 / Schedule D"] == 4


def test_routing():
    for q in ("review my 2025 tax return", "is my return ready to file?", "run a return review for 2025"):
        assert answer_return_review(q, TENANT), q
    for q in ("review the contract", "what is my tax return refund status"):
        assert answer_return_review(q, TENANT) is None, q
