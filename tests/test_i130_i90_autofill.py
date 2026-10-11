"""I-130 / I-90 auto-fill: values land in the official fields, choices tick the right box, signatures untouched."""
import json
import uuid

import fitz
import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.immigration import suite
from app.services.immigration.forms import i90, i130

T1, T2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-000000000001"
TAG = f"ZZI130-{uuid.uuid4().hex[:5]}"
ANSWERS = {"full_name": "Priya Lakshmi Raman", "date_of_birth": "1990-04-02", "country_of_birth": "India",
           "citizenship": "India", "passport_number": "Z1234567", "passport_expiry": "2031-06-30",
           "i94_number": "12345678901", "last_entry_date": "2024-08-14", "current_status": "H-4",
           "a_number": "A-123456789", "phone": "(916) 555-0100", "marital_status": "Married",
           "spouse_name": "Arjun Raman", "spouse_dob": "1988-01-10", "marriage_date": "2015-05-20",
           "us_address": "100 Main St, Apt 4, Sacramento, CA 95814"}


@pytest.fixture(scope="module")
def cases():
    db = SessionLocal()
    made = {}
    for ct in ("i130", "i90"):
        c = suite.create_case(db, T1, {"beneficiary": f"{TAG} Priya Raman", "case_type": ct})
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


def test_editions_and_field_names_current():
    for mod in (i130, i90):
        assert mod.edition_ok()
        names = {w.field_name for p in fitz.open(mod.FORM_PDF) for w in p.widgets()}
        assert not [k for k, (f, _, _) in mod.TEXT_FIELDS.items() if f not in names]


def test_forms_offered_by_case_type(cases):
    db, made = cases
    assert suite.forms_for_case(db, T1, made["i130"]["id"]) == ["I-130"]
    assert suite.forms_for_case(db, T1, made["i90"]["id"]) == ["I-90"]


def test_i130_spouse_petition(cases):
    db, made = cases
    cid = made["i130"]["id"]
    suite.save_form_data(db, T1, cid, "I-130", {"bene_sex": "F", "pet_status": "U.S. citizen",
                                                 "pet_country_birth": "India", "pet_phone": "916-555-0199"})
    f = _fields(suite.form_pdf(db, T1, cid, "I-130"))
    P = "form1[0].#subform"
    assert f[f"{P}[0].Pt1Line1_Spouse[0]"] == "Y"
    assert f[f"{P}[0].Pt2Line4b_GivenName[0]"] == "Arjun"                 # petitioner = spouse
    assert f[f"{P}[2].Pt2Line36_USCitizen[0]"] == "Y"
    assert f[f"{P}[4].Pt4Line4a_FamilyName[0]"] == "Raman"
    assert f[f"{P}[4].Pt4Line9_Female[0]"] == "Y"
    assert f[f"{P}[5].Pt4Line18_MaritalStatus[4]"] == "M"
    assert f[f"{P}[6].Pt4Line21a_ClassOfAdmission[0]"] == "H4"
    assert f[f"{P}[8].Pt6Line3_DaytimePhoneNumber[0]"] == "9165550199"
    assert not any("Signature" in k for k in f)
    assert not suite.form_preview(db, T1, cid, "I-130")["missing_required"]


def test_i90_renewal(cases):
    db, made = cases
    cid = made["i90"]["id"]
    suite.save_form_data(db, T1, cid, "I-90", {"sex": "F", "admission_class": "E21", "city_birth": "Chennai",
                                               "admission_date": "05/01/2019"})
    f = _fields(suite.form_pdf(db, T1, cid, "I-90"))
    P = "form1[0].#subform"
    assert f[f"{P}[0].#area[1].P1_Line1_AlienNumber[0]"] == "123456789"
    assert f[f"{P}[0].P1_Line6e_State[0]"] == "CA"
    assert f[f"{P}[1].P2_checkbox1[0]"] == "1a"                            # permanent resident
    assert f[f"{P}[1].P2_checkbox2[1]"] == "2f"                            # card expiring
    assert f[f"{P}[1].P1_Line8_female[0]"] == "Y"
    assert f[f"{P}[3].P5_Line3_DaytimePhoneNumber[0]"] == "9165550100"
    assert not any("Signature" in k for k in f)
    rep = suite.form_preview(db, T1, cid, "I-90")
    assert not rep["missing_required"]


def test_other_tenant_blocked(cases):
    db, made = cases
    with pytest.raises(Exception):
        suite.form_preview(db, T2, made["i130"]["id"], "I-130")
