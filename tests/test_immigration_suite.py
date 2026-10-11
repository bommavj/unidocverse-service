"""Immigration suite: cases, deadlines, CRM, intake and signing via their public links, certificate, reports, isolation."""
import uuid
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.immigration_suite_router import public
from app.core.database import SessionLocal
from app.services.immigration import suite

T1 = "00000000-0000-0000-0000-0000000000a1"
T2 = "00000000-0000-0000-0000-000000000001"
TAG = f"ZZTEST-{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    yield s
    s.rollback()
    s.execute(text("DELETE FROM imm_cases WHERE beneficiary LIKE :t"), {"t": f"{TAG}%"})
    s.execute(text("DELETE FROM imm_leads WHERE name LIKE :t"), {"t": f"{TAG}%"})
    s.commit()
    s.close()


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(public)
    return TestClient(app)


def _case(db, tenant=T1, **kw):
    return suite.create_case(db, tenant, {"beneficiary": f"{TAG} Priya Raman", "case_type": "h1b_extension", **kw})


def test_case_lifecycle(db):
    exp = (date.today() + timedelta(days=200)).isoformat()
    c = _case(db, expiry_date=exp, petitioner="Acme Robotics LLC")
    assert c["form"] == "I-129" and c["visa_class"] == "H-1B" and c["status"] == "intake"
    assert len(c["checklist"]) == len(suite.rules()["case_types"]["h1b_extension"]["evidence"])
    window = next(d for d in c["deadlines"] if d["what"].startswith("Filing window"))
    assert window["days"] == 20                                    # 200 days to expiry − 180-day window
    c = suite.update_case(db, T1, c["id"], {"status": "filed", "receipt_number": "IOE0123456789", "filed_date": date.today().isoformat()})
    kinds = [e["kind"] for e in c["events"]]
    assert c["status"] == "filed" and "filing" in kinds and any("intake → filed" in e["text"] for e in c["events"])
    with pytest.raises(ValueError):
        suite.update_case(db, T1, c["id"], {"status": "bogus"})
    with pytest.raises(ValueError):
        suite.update_case(db, T1, c["id"], {"status=1, tenant_id": "x"})       # field names are whitelisted
    suite.toggle_check(db, T1, c["id"], c["checklist"][0]["id"], True)
    assert suite.get_case(db, T1, c["id"])["progress"] > 0


def test_cases_are_tenant_private(db):
    c = _case(db)
    with pytest.raises(LookupError):
        suite.get_case(db, T2, c["id"])
    with pytest.raises(LookupError):
        suite.update_case(db, T2, c["id"], {"status": "filed"})
    assert c["id"] not in {x["id"] for x in suite.list_cases(db, T2)}


def test_crm_convert(db):
    lead = suite.save_lead(db, T1, {"name": f"{TAG} Wei Chen", "interest": "i485", "email": "wei@example.test"})
    lead = suite.save_lead(db, T1, {"stage": "consulted"}, lead["id"])
    assert lead["stage"] == "consulted"
    with pytest.raises(ValueError):
        suite.save_lead(db, T1, {"stage": "nope"}, lead["id"])
    case = suite.convert_lead(db, T1, lead["id"])
    assert case["case_type"] == "i485" and case["form"] == "I-485"
    assert next(l for l in suite.list_leads(db, T1) if l["id"] == lead["id"])["stage"] == "retained"


def test_intake_via_public_link(db, client):
    c = _case(db)
    r = suite.create_intake(db, T1, c["id"], "en")
    stored = db.execute(text("SELECT token_hash FROM imm_intakes WHERE id = :i"), {"i": r["id"]}).scalar()
    assert r["token"] not in stored                                # only the hash is stored
    page = client.get(f"/public/imm/intake/{r['token']}")
    assert page.status_code == 200 and "Full legal name" in page.text and "Current employer" in page.text
    bad = client.post(f"/public/imm/intake/{r['token']}", json={"answers": {"email": "x@y.z"}, "final": True})
    assert bad.status_code == 400 and "Full legal name" in bad.json()["detail"]
    ok = client.post(f"/public/imm/intake/{r['token']}", json={"final": True, "answers": {
        "full_name": "Priya Raman", "date_of_birth": "1990-04-02", "country_of_birth": "India", "citizenship": "India",
        "employer": "Acme Robotics LLC", "status_expiry": "2027-01-15", "not_a_field": "ignored"}})
    assert ok.status_code == 200
    case = suite.get_case(db, T1, c["id"])
    assert case["petitioner"] == "Acme Robotics LLC" and case["expiry_date"] == "2027-01-15"
    ans = suite.intake_answers(db, T1, r["id"])
    assert "not_a_field" not in ans["answers"] and ans["status"] == "submitted"
    assert suite.intake_pdf(db, T1, r["id"])[:4] == b"%PDF"
    assert client.get(f"/public/imm/intake/{r['token']}").text.count("Thank you") == 1
    assert client.get("/public/imm/intake/not-a-real-token").status_code == 404


def test_signature_via_public_link(db, client):
    c = _case(db)
    r = suite.create_signature(db, T1, c["id"], "Engagement letter", f"{TAG} Priya Raman", "p@example.test", None)
    link = f"/public/imm/sign/{r['token']}"
    assert client.get(link).status_code == 200
    assert client.post(link, json={"name": "Someone Else", "consent": True}).status_code == 400
    assert client.post(link, json={"name": f"{TAG} Priya Raman", "consent": False}).status_code == 400
    assert client.post(link, json={"name": f"{TAG}  priya raman", "consent": True}).status_code == 200
    assert "Signed" in client.get(link).text
    sig = next(s for s in suite.list_signatures(db, T1) if s["id"] == r["id"])
    assert sig["status"] == "signed" and [a["event"] for a in sig["audit"]] == ["created", "viewed", "signed"]
    assert suite.signature_certificate(db, T1, r["id"])[:4] == b"%PDF"
    with pytest.raises(LookupError):
        suite.signature_certificate(db, T2, r["id"])


def test_report(db):
    rep = suite.report(db, T1)
    assert rep["totals"]["cases"] >= 1 and {"by_status", "by_type", "pipeline", "upcoming"} <= set(rep)


def test_public_link_address(monkeypatch):
    from types import SimpleNamespace
    from app.api.immigration_suite_router import _public_link
    req = lambda base: SimpleNamespace(base_url=base)
    monkeypatch.delenv("UDV_PUBLIC_URL", raising=False)
    r = _public_link(req("http://localhost:8000/"), "/public/imm/intake/T")
    assert r["link"] == "http://localhost:8000/public/imm/intake/T" and not r["reachable"] and "UDV_PUBLIC_URL" in r["warning"]
    assert not _public_link(req("http://192.168.1.20:8000/"), "/x")["reachable"]
    monkeypatch.setenv("UDV_PUBLIC_URL", "https://app.example-firm.com/")
    r = _public_link(req("http://localhost:8000/"), "/public/imm/sign/T")
    assert r == {"link": "https://app.example-firm.com/public/imm/sign/T", "reachable": True}


def test_packet(db):
    import fitz
    c = _case(db)
    src = suite.packet_sources(db, T1, c["id"])
    assert [b["type"] for b in src["builtins"]] == ["cover", "toc", "letter", "checklist", "form"]   # + I-129 for H-1B cases
    doc = next((d for d in src["documents"] if d["title"].lower().endswith(".pdf")), None)
    items = [{"type": "cover"}, {"type": "toc"}, {"type": "letter"}, {"type": "checklist"}]
    if doc:
        items.append({"type": "document", "id": doc["id"]})
    pdf = suite.build_packet(db, T1, c["id"], items, preparer="Philip Martin", letter_text="Dear Officer,\n\n" + "Line. " * 400)
    with fitz.open(stream=pdf, filetype="pdf") as p:
        toc = p.get_toc()
        assert [t[1] for t in toc][:4] == ["Cover page", "Table of contents", "Cover letter", "Evidence checklist"]
        assert "Philip Martin" in p[0].get_text() and f"Page 1 of {len(p)}" in p[0].get_text()
        assert "Cover letter" in p[1].get_text()                        # the contents page lists the sections
        assert toc[3][2] > toc[2][2]                                     # a long letter spans several pages
    with pytest.raises(ValueError):
        suite.build_packet(db, T1, c["id"], [{"type": "letter"}])        # letter needs text
    with pytest.raises(LookupError):
        suite.build_packet(db, T2, c["id"], [{"type": "cover"}])         # another tenant's case
    assert any("Packet assembled" in e["text"] for e in suite.get_case(db, T1, c["id"])["events"])
