"""I-140 / I-485 auto-fill: values land in the official fields, choices tick the right box, signatures untouched."""
import json
import uuid

import fitz
import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.immigration import suite
from app.services.immigration.forms import i140, i485

T1, T2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-000000000001"
TAG = f"ZZI140-{uuid.uuid4().hex[:5]}"
ANSWERS = {"full_name": "Priya Lakshmi Raman", "date_of_birth": "1990-04-02", "country_of_birth": "India",
           "citizenship": "India", "passport_number": "Z1234567", "passport_expiry": "2031-06-30",
           "i94_number": "12345678901", "last_entry_date": "2024-08-14", "current_status": "H-1B",
           "a_number": "A-123456789", "phone": "(916) 555-0100", "last_entry_port": "Newark, NJ",
           "us_address": "100 Main St, Apt 4, Sacramento, CA 95814", "employer": "Acme Robotics LLC",
           "job_title": "Data Engineer", "annual_salary": "$145,000", "work_address": "2 Market St, Sacramento, CA 95814"}


@pytest.fixture(scope="module")
def cases():
    db = SessionLocal()
    made = {}
    for ct in ("i140", "i485"):
        c = suite.create_case(db, T1, {"beneficiary": f"{TAG} Priya Raman", "case_type": ct, "petitioner": "ACME ROBOTICS LLC"})
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
    assert i140.edition_ok() and i485.edition_ok()
    for mod in (i140, i485):
        names = {w.field_name for p in fitz.open(mod.FORM_PDF) for w in p.widgets()}
        assert not [k for k, (f, _, _) in mod.TEXT_FIELDS.items() if f not in names]


def test_forms_offered_by_case_type(cases):
    db, made = cases
    assert suite.forms_for_case(db, T1, made["i140"]["id"]) == ["I-140"]
    assert suite.forms_for_case(db, T1, made["i485"]["id"]) == ["I-485"]


def test_i140_fill(cases):
    db, made = cases
    cid = made["i140"]["id"]
    suite.save_form_data(db, T1, cid, "I-140", {"petition_type": "EB-2", "petitioner_fein": "123456789",
                                                 "perm_case": "G-100-24001-123456"})
    f = _fields(suite.form_pdf(db, T1, cid, "I-140"))
    P = "form1[0].#subform"
    assert f[f"{P}[0].Line2_CompanyName[0]"] == "Acme Robotics LLC"
    assert f[f"{P}[0].Pt1Line3_TaxNumber[0]"] == "123456789"
    assert f[f"{P}[0].prt2PetitionType[4]"] == "d"                      # EB-2 advanced degree
    assert f[f"{P}[1].Pt3Line1a_FamilyName[0]"] == "Raman"
    assert f[f"{P}[1].Line11_Alien[0].Pt3Line8_AlienNumber[0]"] == "123456789"
    assert f[f"{P}[1].Line1b_Status[0]"] == "1b"                        # adjusting in the U.S.
    assert f[f"{P}[2].Line2g_LaborCertification[0]"] == "G10024001123456"
    assert f[f"{P}[3].Line8_Wages[0]"] == "145000"
    assert f[f"{P}[3].Line9d_State[0]"] == "CA"
    assert not any("Signature" in k for k in f)


def test_i485_fill(cases):
    db, made = cases
    cid = made["i485"]["id"]
    suite.save_form_data(db, T1, cid, "I-485", {"category": "EB-2", "sex": "F", "underlying_receipt": "IOE0912345678"})
    f = _fields(suite.form_pdf(db, T1, cid, "I-485"))
    P = "form1[0].#subform"
    assert f[f"{P}[0].Pt1Line1_FamilyName[0]"] == "Raman"
    assert f[f"{P}[0].Pt1Line4_AlienNumber[0]"] == "123456789"
    assert sum(1 for k, v in f.items() if "Pt1Line4_AlienNumber" in k and v == "123456789") > 20   # every page header
    assert f[f"{P}[1].Pt1Line6_CB_Sex[0]"] == "F"
    assert f[f"{P}[1].Pt1Line10_State[0]"] == "NJ"
    assert f[f"{P}[2].Pt1Line18_State[0]"] == "CA"
    assert f[f"{P}[4].Pt2Line2_CB[0]"] == "1fA"                          # principal applicant
    assert f[f"{P}[5].Pt2Line3b_CB140[3]"] == "3b4"                      # EB-2
    assert f[f"{P}[22].Pt3Line3_DaytimePhoneNumber1[0]"] == "9165550100"
    assert not any("Signature" in k for k in f)
    assert not suite.form_preview(db, T1, cid, "I-485")["missing_required"]


def test_other_tenant_blocked(cases):
    db, made = cases
    with pytest.raises(Exception):
        suite.form_preview(db, T2, made["i485"]["id"], "I-485")
