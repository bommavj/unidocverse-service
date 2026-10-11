"""I-539 / I-765 auto-fill: values land in the official fields, choices tick the right box, signatures untouched."""
import json
import uuid

import fitz
import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.immigration import suite
from app.services.immigration.forms import i539, i765

T1, T2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-000000000001"
TAG = f"ZZI539-{uuid.uuid4().hex[:5]}"
ANSWERS = {"full_name": "Priya Lakshmi Raman", "date_of_birth": "1990-04-02", "country_of_birth": "India",
           "citizenship": "India", "passport_number": "Z1234567", "passport_expiry": "2031-06-30",
           "i94_number": "12345678901", "last_entry_date": "2024-08-14", "current_status": "H-4",
           "marital_status": "Married", "phone": "9165550100",
           "us_address": "100 Main St, Apt 4, Sacramento, CA 95814"}


@pytest.fixture(scope="module")
def cases():
    db = SessionLocal()
    made = {}
    for ct in ("h4_extension", "h4_ead"):
        c = suite.create_case(db, T1, {"beneficiary": f"{TAG} Priya Raman", "case_type": ct, "expiry_date": "2027-01-15"})
        db.execute(text("INSERT INTO imm_intakes (id, tenant_id, case_id, token_hash, case_type, answers, status) "
                        "VALUES (:i, :t, :c, :h, :ct, CAST(:a AS jsonb), 'submitted')"),
                   {"i": str(uuid.uuid4()), "t": T1, "c": c["id"], "h": uuid.uuid4().hex, "ct": ct, "a": json.dumps(ANSWERS)})
        made[ct] = c
    db.commit()
    yield db, made
    for c in made.values():
        db.execute(text("DELETE FROM imm_cases WHERE id = :c"), {"c": c["id"]})
    db.commit(); db.close()


def _fields(pdf: bytes) -> dict:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        out = {}
        for p in d:
            for w in p.widgets():
                v = d.xref_get_key(w.xref, "V")[1].strip("()/") if w.field_type_string == "ComboBox" else w.field_value
                if v not in (None, "", "Off"):
                    out[w.field_name] = v
        return out


def test_editions_current():
    assert i539.edition_ok() and i765.edition_ok()


def test_forms_offered_by_case_type(cases):
    db, made = cases
    assert suite.forms_for_case(db, T1, made["h4_extension"]["id"]) == ["I-539"]
    assert suite.forms_for_case(db, T1, made["h4_ead"]["id"]) == ["I-765"]


def test_i539_fill(cases):
    db, made = cases
    cid = made["h4_extension"]["id"]
    suite.save_form_data(db, T1, cid, "I-539", {"extend_until": "01/15/2030"})
    f = _fields(suite.form_pdf(db, T1, cid, "I-539"))
    P = "form1[0].#subform"
    assert f[f"{P}[0].P1Line1a_FamilyName[0]"] == "Raman"
    assert f[f"{P}[1].P1_Line8_DateOfBirth[0]"] == "04/02/1990"
    assert f[f"{P}[0].Part2_Item11_State[0]"] == "CA"
    assert f[f"{P}[1].Pt1Line15a_NewStatus[0]"] == "H4"
    assert f[f"{P}[1].P3_Line1a_DateExtended[0]"] == "01/15/2030"
    assert f[f"{P}[1].P2_checkbox[0]"] == "B"                       # extension of stay
    assert not any("Signature" in k for k in f)
    rep = suite.form_preview(db, T1, cid, "I-539")
    assert not rep["missing_required"]


def test_i765_fill_h4_ead(cases):
    db, made = cases
    cid = made["h4_ead"]["id"]
    suite.save_form_data(db, T1, cid, "I-765", {"reason": "initial", "sex": "F", "h1b_receipt": "WAC2412345678"})
    f = _fields(suite.form_pdf(db, T1, cid, "I-765"))
    P = "form1[0]"
    assert f[f"{P}.Page3[0].#area[1].section_1[0]"] == "c"
    assert f[f"{P}.Page3[0].#area[1].section_2[0]"] == "26"
    assert f[f"{P}.Page3[0].Line28_ReceiptNumber[0]"] == "WAC2412345678"
    assert f[f"{P}.Page1[0].Part1_Checkbox[0]"] == "1"               # initial
    assert f[f"{P}.Page2[0].Line9_Checkbox[0]"] == "N"               # Female (this edition's export value)
    assert f[f"{P}.Page2[0].Line10_Checkbox[3]"] == "Married"
    assert f[f"{P}.Page2[0].Pt2Line5_State[0]"] == "CA"
    assert not any("Signature" in k for k in f)


def test_bad_choice_and_unknown_key(cases):
    db, made = cases
    cid = made["h4_ead"]["id"]
    with pytest.raises(ValueError):
        suite.save_form_data(db, T1, cid, "I-765", {"not_a_field": "x"})
    _, rep = i765.fill({"case_type": "h4_ead"}, {}, {"reason": "bogus"})
    assert any("bogus" in s for s in rep["skipped"])


def test_other_tenant_blocked(cases):
    db, made = cases
    with pytest.raises(Exception):
        suite.form_preview(db, T2, made["h4_ead"]["id"], "I-765")
