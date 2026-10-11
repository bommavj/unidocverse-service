"""I-129 auto-fill: values land in the official form's fields; nothing invented; signatures untouched."""
import json
import uuid

import fitz
import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.immigration import suite
from app.services.immigration.forms import i129

T1, T2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-000000000001"
TAG = f"ZZI129-{uuid.uuid4().hex[:5]}"


@pytest.fixture(scope="module")
def case():
    db = SessionLocal()
    c = suite.create_case(db, T1, {"beneficiary": f"{TAG} Priya Raman", "case_type": "h1b_extension",
                                   "petitioner": "ACME ROBOTICS LLC", "receipt_number": "IOE0912345678",
                                   "expiry_date": "2027-01-15"})
    answers = {"full_name": "Priya Lakshmi Raman", "date_of_birth": "1990-04-02", "country_of_birth": "India",
               "citizenship": "India", "passport_number": "Z1234567", "passport_expiry": "2031-06-30",
               "i94_number": "12345678901", "last_entry_date": "2024-08-14", "current_status": "H-1B",
               "us_address": "100 Main St, Apt 4, Sacramento, CA 95814", "employer": "Acme Robotics LLC",
               "job_title": "Data Engineer", "annual_salary": "$145,000", "work_address": "2 Market St, Sacramento, CA 95814"}
    db.execute(text("INSERT INTO imm_intakes (id, tenant_id, case_id, token_hash, case_type, answers, status) "
                    "VALUES (:i, :t, :c, :h, 'h1b_extension', CAST(:a AS jsonb), 'submitted')"),
               {"i": str(uuid.uuid4()), "t": T1, "c": c["id"], "h": uuid.uuid4().hex, "a": json.dumps(answers)})
    db.commit()
    yield db, c
    db.execute(text("DELETE FROM imm_cases WHERE id = :c"), {"c": c["id"]}); db.commit(); db.close()


def _fields(pdf: bytes) -> dict:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        out = {}
        for p in d:
            for w in p.widgets():
                v = w.field_value
                if w.field_type_string == "ComboBox":           # read the stored value, not the drawn one
                    raw = d.xref_get_key(w.xref, "V")
                    v = raw[1].strip("()") if raw[0] == "string" else v
                out[w.field_name] = v
        return out


def test_edition_matches():
    assert i129.edition_ok()


def test_preview_sources_and_missing(case):
    db, c = case
    pv = suite.form_preview(db, T1, c["id"], "I-129")
    by = {f["key"]: f for f in pv["fields"]}
    assert by["bene_family"]["value"] == "Raman" and by["bene_family"]["source"] == "derived"
    assert by["bene_middle"]["value"] == "Lakshmi"
    assert by["bene_dob"] == {**by["bene_dob"], "value": "04/02/1990", "source": "intake"}
    assert by["classification"]["value"] == "H-1B" and by["classification"]["source"] == "case"
    assert by["bene_city"]["value"] == "Sacramento" and by["bene_state"]["value"] == "CA"
    assert "Federal Employer Identification Number (FEIN)" in pv["missing_required"]
    assert pv["basis"] == "continuation" and pv["requested_action"] == "C"


def test_fill_writes_official_fields(case):
    db, c = case
    suite.save_form_data(db, T1, c["id"], "I-129", {"petitioner_fein": "84-1234567", "lca_number": "I-200-26123-456789",
                                                    "sex": "F", "petitioner_state": "ca"})
    f = _fields(suite.form_pdf(db, T1, c["id"], "I-129"))
    P = "form1[0].#subform"
    assert f[f"{P}[1].Part3_Line2_FamilyName[0]"] == "Raman"
    assert f[f"{P}[2].Line6_DateOfBirth[0]"] == "04/02/1990"
    assert f[f"{P}[0].TextField1[0]"] == "84-1234567"
    assert f[f"{P}[4].Part5_Q2_LCAorETA[0]"] == "I-200-26123-456789"
    assert f[f"{P}[4].Line8_Wages[0]"] == "145000" and f[f"{P}[4].Line8_Per[0]"] == "year"
    assert f[f"{P}[2].Line11g_CurrentNon[0]"] == "H1B"
    assert f[f"{P}[0].P1_Line3_State[0]"] == "CA"
    assert f[f"{P}[1].continuation[0]"] not in ("Off", "", None)
    assert f[f"{P}[1].new[0]"] in ("Off", "", None)
    assert f[f"{P}[1].P2Checkbox4[2]"] not in ("Off", "", None)          # C — extend the stay
    assert f[f"{P}[2].Line1_Gender_P3[1]"] not in ("Off", "", None)       # F
    for name, val in f.items():
        if "Signature" in name or "DateofSignature" in name:
            assert val in ("", None, "Off"), name


def test_rejects_unknown_fields_and_other_tenant(case):
    db, c = case
    with pytest.raises(ValueError):
        suite.save_form_data(db, T1, c["id"], "I-129", {"Line_Signature": "x"})
    with pytest.raises(LookupError):
        suite.form_preview(db, T2, c["id"], "I-129")
    with pytest.raises(ValueError):
        suite.form_preview(db, T1, c["id"], "N-400")


def test_packet_includes_form(case):
    db, c = case
    src = suite.packet_sources(db, T1, c["id"])
    assert any(b["type"] == "form" and b["id"] == "I-129" for b in src["builtins"])
    pdf = suite.build_packet(db, T1, c["id"], [{"type": "cover"}, {"type": "form", "id": "I-129"}])
    with fitz.open(stream=pdf, filetype="pdf") as d:
        assert len(d) == 1 + 38 and [t[1] for t in d.get_toc()] == ["Cover page", "Form I-129"]
