"""
Form I-539 (Application to Extend/Change Nonimmigrant Status) auto-fill — edition 08/28/24.

Sources, in priority order: values staff entered on the case (imm_cases.form_data),
the client's intake answers, then the case itself. Field names are this edition's
AcroForm names; re-check them when USCIS publishes a new edition (`edition_ok`).
"""
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

from . import _acro

FORM = "I-539"
FORM_PDF = Path(__file__).with_name("i-539.pdf")
EDITION = "08/28/24"
P = "form1[0].#subform"

TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 1 — applicant
    "app_family":         (f"{P}[0].P1Line1a_FamilyName[0]", "Family name", "Part 1. Applicant"),
    "app_given":          (f"{P}[0].P1_Line1b_GivenName[0]", "Given name", "Part 1. Applicant"),
    "app_middle":         (f"{P}[0].P1_Line1c_MiddleName[0]", "Middle name", "Part 1. Applicant"),
    "a_number":           (f"{P}[0].Pt1Line2_AlienNumber[0]", "A-Number (if any)", "Part 1. Applicant"),
    "uscis_account":      (f"{P}[0].Pt1Line2_USCISOnlineAcctNumber[0]", "USCIS online account number (if any)", "Part 1. Applicant"),
    "mail_care_of":       (f"{P}[0].Part2_Item11_InCareOfName[0]", "Mailing address — in care of", "Part 1. Mailing address"),
    "mail_street":        (f"{P}[0].Part2_Item11_StreetName[0]", "Mailing street number and name", "Part 1. Mailing address"),
    "mail_unit":          (f"{P}[0].Part1_Item4_Number[0]", "Mailing apt / ste / flr number", "Part 1. Mailing address"),
    "mail_city":          (f"{P}[0].Part2_Item11_City[0]", "Mailing city", "Part 1. Mailing address"),
    "mail_state":         (f"{P}[0].Part2_Item11_State[0]", "Mailing state (2 letters)", "Part 1. Mailing address"),
    "mail_zip":           (f"{P}[0].Part2_Item11_ZipCode[0]", "Mailing ZIP code", "Part 1. Mailing address"),
    "phys_street":        (f"{P}[0].Part1_Item6_StreetName[0]", "Physical street number and name", "Part 1. Physical address"),
    "phys_unit":          (f"{P}[0].Part1_Item6_Number[0]", "Physical apt / ste / flr number", "Part 1. Physical address"),
    "phys_city":          (f"{P}[0].Part1_Item6_City[0]", "Physical city", "Part 1. Physical address"),
    "phys_state":         (f"{P}[0].Part1_Item6_State[0]", "Physical state (2 letters)", "Part 1. Physical address"),
    "phys_zip":           (f"{P}[0].Part1_Item6_ZipCode[0]", "Physical ZIP code", "Part 1. Physical address"),
    "country_birth":      (f"{P}[1].P1_Line6_CountryOfBirth[0]", "Country of birth", "Part 1. Applicant"),
    "citizenship":        (f"{P}[1].P1_Line7_CountryOfCitizenship[0]", "Country of citizenship", "Part 1. Applicant"),
    "dob":                (f"{P}[1].P1_Line8_DateOfBirth[0]", "Date of birth (mm/dd/yyyy)", "Part 1. Applicant"),
    "ssn":                (f"{P}[1].P1_Line9_SSN[0]", "U.S. Social Security number (if any)", "Part 1. Applicant"),
    "last_arrival":       (f"{P}[1].SupA_Line1i_DateOfArrival[0]", "Date of last arrival (mm/dd/yyyy)", "Part 1. Travel"),
    "i94":                (f"{P}[1].SupA_Line1j_ArrivalDeparture[0]", "I-94 number", "Part 1. Travel"),
    "passport":           (f"{P}[1].SupA_Line1k_Passport[0]", "Passport number", "Part 1. Travel"),
    "passport_country":   (f"{P}[1].SupA_Line1m_CountryOfIssuance[0]", "Passport country of issuance", "Part 1. Travel"),
    "passport_expiry":    (f"{P}[1].SupA_Line1n_ExpDate[0]", "Passport expiration (mm/dd/yyyy)", "Part 1. Travel"),
    "current_status":     (f"{P}[1].Pt1Line15a_NewStatus[0]", "Current nonimmigrant status (e.g. H-4)", "Part 1. Travel"),
    "status_expiry":      (f"{P}[1].SupA_Line1p_DateExpires[0]", "Current status expires (mm/dd/yyyy)", "Part 1. Travel"),
    # Part 2 / 3 — application and processing
    "total_people":       (f"{P}[1].P2_Line5b_TotalNumber[0]", "Total people in the application", "Part 2. Application"),
    "new_status":         (f"{P}[1].Pt2Line2a_NewStatus[0]", "Requested new status (change of status only)", "Part 2. Application"),
    "extend_until":       (f"{P}[1].P3_Line1a_DateExtended[0]", "Extend status until (mm/dd/yyyy)", "Part 3. Processing"),
    "related_receipt":    (f"{P}[2].P3_Line5_ReceiptNumber[0]", "Receipt number of the principal's petition", "Part 3. Processing"),
    "related_first":      (f"{P}[2].P3_Line4_NameofPetitioner[0]", "Principal beneficiary first name", "Part 3. Processing"),
    "related_last":       (f"{P}[2].P3_Line4_NameofPetitioner[1]", "Principal beneficiary last name", "Part 3. Processing"),
    "related_filed":      (f"{P}[2].P3_Line5_DateFiled[0]", "Principal's petition filed (mm/dd/yyyy)", "Part 3. Processing"),
    # Part 5 — contact (signature stays blank)
    "phone":              (f"{P}[4].P5_Line3_DaytimePhoneNumber[0]", "Daytime phone", "Part 5. Contact"),
    "mobile":             (f"{P}[4].P5_Line4_MobilePhoneNumber[0]", "Mobile phone", "Part 5. Contact"),
    "email":              (f"{P}[4].P5_Line5_EmailAddress[0]", "Email", "Part 5. Contact"),
    # Part 7 — preparer
    "preparer_family":    (f"{P}[5].P7_Line1a_PreparerFamilyName[0]", "Preparer family name", "Part 7. Preparer"),
    "preparer_given":     (f"{P}[5].P7_Line1b_PreparerGivenName[0]", "Preparer given name", "Part 7. Preparer"),
    "preparer_business":  (f"{P}[5].P7_Line2_BusinessName[0]", "Preparer business name", "Part 7. Preparer"),
}

_status = lambda v: re.sub(r"[^A-Z0-9]", "", v.upper())
COMBO_CODE = {"current_status": _status, "new_status": _status}

CHOICES = {
    "application_type": {"label": "I am applying for", "group": "Part 2. Application", "required": True,
                         "options": {"extension": (f"{P}[1].P2_checkbox", "B"), "change": (f"{P}[1].P2_checkbox", "C"),
                                     "reinstatement": (f"{P}[1].P2_checkbox", "A")}},
    "applicants": {"label": "Who is applying", "group": "Part 2. Application",
                   "options": {"only me": (f"{P}[1].P2_checkbox4", "D"), "me and family": (f"{P}[1].P2_checkbox4", "E")}},
}
TYPE_BY_CASE = {"h4_extension": "extension"}

REQUIRED = ["app_family", "app_given", "mail_street", "mail_city", "mail_state", "mail_zip", "country_birth",
            "citizenship", "dob", "last_arrival", "i94", "passport", "passport_country", "passport_expiry",
            "current_status", "status_expiry", "extend_until", "phone"]


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    out = _acro.Collector()
    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in CHOICES:
            out.put(k, v, "entered")
    a = intake or {}
    out.name("app", a.get("full_name"))
    for key, src in (("dob", "date_of_birth"), ("passport_expiry", "passport_expiry"),
                     ("status_expiry", "status_expiry"), ("last_arrival", "last_entry_date")):
        out.put(key, _acro.mdy(a.get(src)), "intake")
    for key, src in (("country_birth", "country_of_birth"), ("citizenship", "citizenship"), ("passport", "passport_number"),
                     ("a_number", "a_number"), ("i94", "i94_number"), ("phone", "phone"), ("email", "email"),
                     ("current_status", "current_status")):
        out.put(key, a.get(src), "intake")
    if a.get("citizenship"):
        out.put("passport_country", a["citizenship"], "derived")
    parts = _acro.addr(a.get("us_address"))
    for f in ("street", "unit", "city", "state", "zip"):
        out.put(f"mail_{f}", parts.get(f), "derived")
        out.put(f"phys_{f}", parts.get(f), "derived")
    if not (a.get("children") or a.get("spouse_name")):  # with family, co-applicants need I-539A — staff decide
        out.put("applicants", "only me", "derived")
        out.put("total_people", "1", "derived")
    out.put("application_type", TYPE_BY_CASE.get(case.get("case_type")), "case")
    out.put("current_status", case.get("visa_class"), "case")
    out.put("status_expiry", _acro.mdy(case.get("expiry_date")), "case")
    out.name("app", case.get("beneficiary"))
    return dict(out)


def edition_ok() -> bool:
    return _acro.edition_ok(sys.modules[__name__])


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    return _acro.preview(sys.modules[__name__], case, intake, form_data)


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    return _acro.fill(sys.modules[__name__], case, intake, form_data)
