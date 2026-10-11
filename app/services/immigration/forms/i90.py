"""
Form I-90 (Application to Replace Permanent Resident Card) auto-fill — edition 01/20/25.

Sources, in priority order: values staff entered on the case (imm_cases.form_data),
the client's intake answers, then the case itself. The biographic section (Part 3:
height, weight, eye and hair colour) is left for staff. Field names are this edition's
AcroForm names; re-check them when USCIS publishes a new edition (`edition_ok`).
"""
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

from . import _acro

FORM = "I-90"
FORM_PDF = Path(__file__).with_name("i-90.pdf")
EDITION = "01/20/25"
P = "form1[0].#subform"

TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 1 — applicant
    "a_number":         (f"{P}[0].#area[1].P1_Line1_AlienNumber[0]", "A-Number (9 digits)", "Part 1. Applicant"),
    "uscis_account":    (f"{P}[0].P1_Line2_AcctIdentifier[0]", "USCIS online account number (if any)", "Part 1. Applicant"),
    "app_family":       (f"{P}[0].P1_Line3a_FamilyName[0]", "Family name", "Part 1. Applicant"),
    "app_given":        (f"{P}[0].P1_Line3b_GivenName[0]", "Given name", "Part 1. Applicant"),
    "app_middle":       (f"{P}[0].P1_Line3c_MiddleName[0]", "Middle name", "Part 1. Applicant"),
    "mail_care_of":     (f"{P}[0].P1_Line6a_InCareofName[0]", "Mailing address — in care of", "Part 1. Mailing address"),
    "mail_street":      (f"{P}[0].P1_Line6b_StreetNumberName[0]", "Mailing street number and name", "Part 1. Mailing address"),
    "mail_unit":        (f"{P}[0].P1_Line6c_AptSteFlrNumber[0]", "Mailing apt / ste / flr number", "Part 1. Mailing address"),
    "mail_city":        (f"{P}[0].P1_Line6d_CityOrTown[0]", "Mailing city", "Part 1. Mailing address"),
    "mail_state":       (f"{P}[0].P1_Line6e_State[0]", "Mailing state (2 letters)", "Part 1. Mailing address"),
    "mail_zip":         (f"{P}[0].P1_Line6f_ZipCode[0]", "Mailing ZIP code", "Part 1. Mailing address"),
    "dob":              (f"{P}[1].P1_Line9_DateOfBirth[0]", "Date of birth (mm/dd/yyyy)", "Part 1. Applicant"),
    "city_birth":       (f"{P}[1].P1_Line10_CityTownOfBirth[0]", "City / town of birth", "Part 1. Applicant"),
    "country_birth":    (f"{P}[1].P1_Line11_CountryofBirth[0]", "Country of birth", "Part 1. Applicant"),
    "mother_given":     (f"{P}[1].P1_Line12_MotherGivenName[0]", "Mother's given name", "Part 1. Applicant"),
    "father_given":     (f"{P}[1].P1_Line13_FatherGivenName[0]", "Father's given name", "Part 1. Applicant"),
    "admission_class":  (f"{P}[1].P1_Line14_ClassOfAdmission[0]", "Class of admission (from the green card, e.g. E21)", "Part 1. Applicant"),
    "admission_date":   (f"{P}[1].P1_Line15_DateOfAdmission[0]", "Date of admission / resident since (mm/dd/yyyy)", "Part 1. Applicant"),
    "ssn":              (f"{P}[1].P1_Line16_SSN[0]", "U.S. Social Security number (if any)", "Part 1. Applicant"),
    # Part 5 — contact (signature stays blank)
    "phone":            (f"{P}[3].P5_Line3_DaytimePhoneNumber[0]", "Daytime phone", "Part 5. Contact"),
    "mobile":           (f"{P}[3].P5_Line4_MobilePhoneNumber[0]", "Mobile phone", "Part 5. Contact"),
    "email":            (f"{P}[3].P5_Line5_EmailAddress[0]", "Email", "Part 5. Contact"),
}

_R = lambda v: (f"{P}[1].P2_checkbox2", v)
CHOICES = {
    "sex": {"label": "Sex", "group": "Part 1. Applicant", "required": True,
            "options": {"M": (f"{P}[1].P1_Line8_male", "Y"), "F": (f"{P}[1].P1_Line8_female", "Y")}},
    "resident_type": {"label": "My status is", "group": "Part 2. Application type", "required": True,
                      "options": {"LPR": (f"{P}[1].P2_checkbox1", "1a"), "commuter": (f"{P}[1].P2_checkbox1", "1b"),
                                  "conditional": (f"{P}[1].P2_checkbox1", "1c")}},
    "reason": {"label": "Reason (permanent residents, Section A)", "group": "Part 2. Application type", "required": True,
               "options": {"expiring": _R("2f"), "lost or stolen": _R("2a"), "never received": _R("2b"),
                           "mutilated": _R("2c"), "USCIS error": _R("2d"), "name changed": _R("2e")}},
}

REQUIRED = ["a_number", "app_family", "app_given", "mail_street", "mail_city", "mail_state", "mail_zip", "dob",
            "city_birth", "country_birth", "admission_class", "admission_date", "phone"]


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    out = _acro.Collector()
    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in CHOICES:
            out.put(k, v, "entered")
    a = intake or {}
    out.name("app", a.get("full_name"))
    out.put("dob", _acro.mdy(a.get("date_of_birth")), "intake")
    for key, src in (("country_birth", "country_of_birth"), ("phone", "phone"), ("email", "email")):
        out.put(key, a.get(src), "intake")
    if a.get("a_number"):
        out.put("a_number", re.sub(r"\D", "", str(a["a_number"])), "intake")
    parts = _acro.addr(a.get("us_address"))
    for f in ("street", "unit", "city", "state", "zip"):
        out.put(f"mail_{f}", parts.get(f), "derived")
    if case.get("case_type") == "i90":                   # renewal is the common I-90 filing; staff override for others
        out.put("resident_type", "LPR", "derived")
        out.put("reason", "expiring", "derived")
    out.name("app", case.get("beneficiary"))
    return dict(out)


def edition_ok() -> bool:
    return _acro.edition_ok(sys.modules[__name__])


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    return _acro.preview(sys.modules[__name__], case, intake, form_data)


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    return _acro.fill(sys.modules[__name__], case, intake, form_data)
