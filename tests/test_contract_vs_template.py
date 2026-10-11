"""Contract vs. template: clause matching, key-term differences, missing and non-standard clauses, tenant scope."""
import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth_router import _create_token
from app.core.database import SessionLocal
from app.services.contract_compare import compare, key_terms, split_sections

DEMO = json.loads((Path(__file__).parent / "fixtures" / "demo_user.json").read_text())

TEMPLATE = """MASTER SERVICES AGREEMENT

1. Term and Renewal
This Agreement begins on the Effective Date and continues for an initial term of one (1) year. It renews automatically for successive one-year terms unless either party gives sixty (60) days written notice.

2. Payment
Customer shall pay each invoice within thirty (30) days of receipt. Late amounts accrue interest at 1.5% per month.

3. Limitation of Liability
Each party's total liability is capped at the fees paid in the twelve (12) months before the claim.

4. Confidentiality
Each party shall protect the other party's Confidential Information for five (5) years after termination.

5. Governing Law
This Agreement is governed by the laws of the State of California.

6. Insurance
Vendor shall maintain general liability insurance of at least $2,000,000 per occurrence.
"""

CONTRACT = """MASTER SERVICES AGREEMENT

1. Term and Renewal
This Agreement begins on the Effective Date and continues for an initial term of one (1) year. It renews automatically for successive one-year terms unless either party gives thirty (30) days written notice.

2. Payment
Customer shall pay each invoice within forty-five days of receipt. Late amounts accrue interest at 1.5% per month.

3. Limitation of Liability
Each party's total liability is capped at the fees paid in the twelve (12) months before the claim.

4. Confidentiality
Each party shall protect the other party's Confidential Information for five (5) years after termination.

5. Governing Law
This Agreement is governed by the laws of the State of Delaware.

6. Non-Solicitation
During the term, neither party shall solicit the other's employees.
"""


def _by(result):
    return {f["clause"]: f for f in result["findings"]}


def test_sections_and_terms():
    secs = split_sections(TEMPLATE)
    assert [s["heading"].split(" ", 1)[1] for s in secs[:2]] == ["Term and Renewal", "Payment"]
    kt = key_terms("renews automatically unless either party gives sixty (60) days notice; cap $2,000,000; 1.5% interest")
    assert "60 day" in kt["durations"] and kt["auto_renewal"] == ["yes"] and kt["percentages"] == ["1.5%"]


def test_findings():
    r = _by(compare(CONTRACT, TEMPLATE))
    assert r["liability"]["status"] == "match"
    assert r["confidentiality"]["status"] == "match"
    term = r["term"]
    assert term["status"] == "deviates"
    assert any(d["term"] == "Time period" and "60 day" in d["template"] and "30 day" in d["contract"] for d in term["differences"])
    law = r["governing_law"]
    assert law["status"] == "deviates" and law["differences"][0]["template"] == "California" and law["differences"][0]["contract"] == "Delaware"
    assert any(d["template"] == "30 day" and d["contract"] == "45 day" for d in r["payment"]["differences"])
    assert r["insurance"]["status"] == "missing"
    assert r["non_solicit"]["status"] == "additional"


def test_counts():
    res = compare(CONTRACT, TEMPLATE)
    assert res["counts"]["missing"] == 1 and res["counts"]["additional"] == 1
    assert res["needs_review"] == res["counts"]["deviates"] + 2


@pytest.fixture(scope="module")
def docs():
    db = SessionLocal()
    ids = []
    for name, body in (("ZZTEST contract.pdf", CONTRACT), ("ZZTEST template.pdf", TEMPLATE)):
        i = str(uuid.uuid4())
        db.execute(text("INSERT INTO documents (id, filename, file_path, status, raw_text, tenant_id) VALUES (:i, :f, '/tmp/x', 'completed', :t, :tn)"),
                   {"i": i, "f": name, "t": body, "tn": DEMO["tenant_id"]})
        ids.append(i)
    db.commit()
    yield ids
    db.execute(text("DELETE FROM documents WHERE id = ANY(:ids)"), {"ids": ids}); db.commit(); db.close()


def test_endpoint_and_tenant_scope(docs):
    from app.main import app
    client = TestClient(app)
    tok = client.post("/api/auth/login", json={"username": DEMO["username"], "password": DEMO["password"]}).json()["access_token"]
    body = {"document_id": docs[0], "template_document_id": docs[1]}
    r = client.post("/api/workflow/contract-vs-template", json=body, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["missing"] == 1 and r.json()["template"]["filename"] == "ZZTEST template.pdf"
    db = SessionLocal()
    a = db.execute(text("SELECT id, username, role, tenant_id FROM users WHERE tenant_id <> :t LIMIT 1"), {"t": DEMO["tenant_id"]}).fetchone()
    db.close()
    other = _create_token(a.id, a.username, a.role, timedelta(minutes=5), a.tenant_id)
    assert client.post("/api/workflow/contract-vs-template", json=body, headers={"Authorization": f"Bearer {other}"}).status_code == 404
