"""Visa tracker from uploaded documents, and tenant isolation of immigration endpoints."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.immigration_tracker import from_documents, parse_passport, parse_uscis

I797 = """Receipt Number 10E8212286107 | Case Type 1129 - PETITION FOR A NONIMMIGRANT WORKER |
| Notice Date | Page | Beneficiary [A-NUMBER REDACTED] RAMAN, PRIYA LAKSHMI |
Petitioner: ELIASSEN GROUP LLC
Notice Type: Approval Notice
Class: H1B
Valid from 12/08/2023 to 11/28/2026
CLASS HIB
VALID FROM 12/08/2023 UNTIL 12/08/2026"""


def test_i797():
    p = parse_uscis(I797)
    assert p["visa_class"] == "H-1B" and p["beneficiary_name"] == "Priya Lakshmi Raman"
    assert p["employer_sponsor"] == "Eliassen Group LLC" and p["doc_label"] == "I-797 Approval Notice (I-129)"
    assert p["valid_until"] == date(2026, 11, 28) and p["authorized_stay_until"] == date(2026, 12, 8)
    assert p["document_number"].startswith("IOE") and "8212286" not in p["document_number"]   # masked


def test_passport_day_first_and_mrz():
    assert parse_passport("Date of Expiry 20/03/2027")["valid_until"] == date(2027, 3, 20)
    assert parse_passport("Date of Expiry 05 MAR 2030")["valid_until"] == date(2030, 3, 5)
    mrz = "P<INDRAMAN<<ARJUN<<<<<<<<<<<<<<<<<<<<<<<<<<<\nZ1234567<8IND1505203M2703209<<<<<<<<<<<<<<<0"
    p = parse_passport(mrz)
    assert p["valid_until"] == date(2027, 3, 20) and p["beneficiary_name"] == "Arjun Raman"
    assert parse_passport("Date of Expiry")["valid_until"] is None          # nothing invented


def test_urgency_uses_sooner_date_and_dedupes():
    rows = [SimpleNamespace(id=i, filename=f"n{i}.pdf", doc_type="uscis_receipt", raw_text=I797, created_at=None)
            for i in (1, 2)]
    out = from_documents(rows, today=date(2026, 10, 7))
    assert len(out) == 1                                            # same notice uploaded twice
    assert out[0]["days_to_expiry"] == 52 and out[0]["urgency"] == "medium"
    unread = from_documents([SimpleNamespace(id=3, filename="p.jpg", doc_type="passport", raw_text="Date of Expiry",
                                             created_at=None)], today=date(2026, 10, 7))
    assert unread[0]["urgency"] == "unknown" and "not readable" in unread[0]["notes"]


def test_other_tenants_clients_are_404():
    from sqlalchemy import text
    from app.api import immigration_router as r
    from app.core.database import SessionLocal
    from app.core.tenant import set_current_tenant_id
    db = SessionLocal()
    other = db.execute(text("SELECT id::text FROM clients WHERE tenant_id <> '00000000-0000-0000-0000-0000000000a1' "
                            "LIMIT 1")).scalar()
    if not other:
        pytest.skip("no client in another tenant to test against")
    set_current_tenant_id("00000000-0000-0000-0000-0000000000a1")
    try:
        for call in (lambda: r.list_client_immigration(other, db=db), lambda: r.cross_link(other, db=db)):
            with pytest.raises(HTTPException) as e:
                call()
            assert e.value.status_code == 404
    finally:
        set_current_tenant_id("00000000-0000-0000-0000-000000000001")
        db.close()
