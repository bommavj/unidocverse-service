"""
Form I-765 (Application for Employment Authorization) auto-fill — edition 08/21/25.

Sources, in priority order: values staff entered on the case (imm_cases.form_data),
the client's intake answers, then the case itself. Field names are this edition's
AcroForm names; re-check them when USCIS publishes a new edition (`edition_ok`).
"""
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

from . import _acro

FORM = "I-765"
FORM_PDF = Path(__file__).with_name("i-765.pdf")
EDITION = "08/21/25"
P = "form1[0]"

TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 2 — applicant
    "app_family":        (f"{P}.Page1[0].Line1a_FamilyName[0]", "Family name", "Part 2. Applicant"),
    "app_given":         (f"{P}.Page1[0].Line1b_GivenName[0]", "Given name", "Part 2. Applicant"),
    "app_middle":        (f"{P}.Page1[0].Line1c_MiddleName[0]", "Middle name", "Part 2. Applicant"),
    "mail_care_of":      (f"{P}.Page2[0].Line4a_InCareofName[0]", "Mailing address — in care of", "Part 2. Mailing address"),
    "mail_street":       (f"{P}.Page2[0].Line4b_StreetNumberName[0]", "Mailing street number and name", "Part 2. Mailing address"),
    "mail_unit":         (f"{P}.Page2[0].Pt2Line5_AptSteFlrNumber[0]", "Mailing apt / ste / flr number", "Part 2. Mailing address"),
    "mail_city":         (f"{P}.Page2[0].Pt2Line5_CityOrTown[0]", "Mailing city", "Part 2. Mailing address"),
    "mail_state":        (f"{P}.Page2[0].Pt2Line5_State[0]", "Mailing state (2 letters)", "Part 2. Mailing address"),
    "mail_zip":          (f"{P}.Page2[0].Pt2Line5_ZipCode[0]", "Mailing ZIP code", "Part 2. Mailing address"),
    "a_number":          (f"{P}.Page2[0].Line7_AlienNumber[0]", "A-Number (if any)", "Part 2. Applicant"),
    "uscis_account":     (f"{P}.Page2[0].Line8_ElisAccountNumber[0]", "USCIS online account number (if any)", "Part 2. Applicant"),
    "ssn":               (f"{P}.Page2[0].Line12b_SSN[0]", "U.S. Social Security number (if known)", "Part 2. Applicant"),
    "citizenship":       (f"{P}.Page2[0].Line17a_CountryOfBirth[0]", "Country of citizenship", "Part 2. Applicant"),
    "city_birth":        (f"{P}.Page3[0].Line18a_CityTownOfBirth[0]", "City / town of birth", "Part 2. Applicant"),
    "state_birth":       (f"{P}.Page3[0].Line18b_CityTownOfBirth[0]", "State / province of birth", "Part 2. Applicant"),
    "country_birth":     (f"{P}.Page3[0].Line18c_CountryOfBirth[0]", "Country of birth", "Part 2. Applicant"),
    "dob":               (f"{P}.Page3[0].Line19_DOB[0]", "Date of birth (mm/dd/yyyy)", "Part 2. Applicant"),
    # Part 2 — travel and status
    "i94":               (f"{P}.Page3[0].Line20a_I94Number[0]", "I-94 number", "Part 2. Travel"),
    "passport":          (f"{P}.Page3[0].Line20b_Passport[0]", "Passport number", "Part 2. Travel"),
    "travel_doc":        (f"{P}.Page3[0].Line20c_TravelDoc[0]", "Travel document number (if any)", "Part 2. Travel"),
    "passport_country":  (f"{P}.Page3[0].Line20d_CountryOfIssuance[0]", "Passport country of issuance", "Part 2. Travel"),
    "passport_expiry":   (f"{P}.Page3[0].Line20e_ExpDate[0]", "Passport expiration (mm/dd/yyyy)", "Part 2. Travel"),
    "last_arrival":      (f"{P}.Page3[0].Line21_DateOfLastEntry[0]", "Date of last arrival (mm/dd/yyyy)", "Part 2. Travel"),
    "arrival_place":     (f"{P}.Page3[0].place_entry[0]", "Place of last arrival", "Part 2. Travel"),
    "status_at_arrival": (f"{P}.Page3[0].Line23_StatusLastEntry[0]", "Status at last arrival (e.g. H-4)", "Part 2. Travel"),
    "current_status":    (f"{P}.Page3[0].Line24_CurrentStatus[0]", "Current status (e.g. H-4)", "Part 2. Travel"),
    "sevis":             (f"{P}.Page3[0].Line26_SEVISnumber[0]", "SEVIS number (if any)", "Part 2. Travel"),
    # Item 27 — eligibility category, e.g. (c)(26) → c / 26 / blank
    "category_1":        (f"{P}.Page3[0].#area[1].section_1[0]", "Eligibility category — letter, e.g. c", "Part 2. Eligibility"),
    "category_2":        (f"{P}.Page3[0].#area[1].section_2[0]", "Eligibility category — number, e.g. 26", "Part 2. Eligibility"),
    "category_3":        (f"{P}.Page3[0].#area[1].section_3[0]", "Eligibility category — sub-item, e.g. iii", "Part 2. Eligibility"),
    "h1b_receipt":       (f"{P}.Page3[0].Line28_ReceiptNumber[0]", "(c)(26): principal's H-1B I-797 receipt number", "Part 2. Eligibility"),
    # Part 3 — contact (signature stays blank)
    "phone":             (f"{P}.Page4[0].Pt3Line3_DaytimePhoneNumber1[0]", "Daytime phone", "Part 3. Contact"),
    "mobile":            (f"{P}.Page4[0].Pt3Line4_MobileNumber1[0]", "Mobile phone", "Part 3. Contact"),
    "email":             (f"{P}.Page4[0].Pt3Line5_Email[0]", "Email", "Part 3. Contact"),
}

CHOICES = {
    "reason": {"label": "Applying for", "group": "Part 1. Reason", "required": True,
               "options": {"initial": (f"{P}.Page1[0].Part1_Checkbox", "1"),
                           "replacement": (f"{P}.Page1[0].Part1_Checkbox", "2"),
                           "renewal": (f"{P}.Page1[0].Part1_Checkbox", "3")}},
    # this edition's export values: Male = Y, Female = N
    "sex": {"label": "Sex", "group": "Part 2. Applicant", "required": True,
            "options": {"M": (f"{P}.Page2[0].Line9_Checkbox", "Y"), "F": (f"{P}.Page2[0].Line9_Checkbox", "N")}},
    "marital": {"label": "Marital status", "group": "Part 2. Applicant",
                "options": {s: (f"{P}.Page2[0].Line10_Checkbox", s) for s in ("Single", "Married", "Divorced", "Widowed")}},
}

CATEGORY_BY_CASE = {"h4_ead": "(c)(26)"}
REASON_BY_CASE = {"ead_renewal": "renewal"}

REQUIRED = ["app_family", "app_given", "mail_street", "mail_city", "mail_state", "mail_zip", "citizenship",
            "country_birth", "dob", "passport", "passport_country", "passport_expiry", "last_arrival",
            "status_at_arrival", "current_status", "category_1", "category_2", "phone"]


def _category(text) -> Tuple[str, ...]:
    """'(c)(26)' / 'c26' / 'C(17)(iii)' → ('c', '26', 'iii')."""
    m = re.match(r"\(?\s*([a-z])\s*\)?\s*\(?\s*(\d{1,2})\s*\)?\s*(?:\(?\s*([ivx]+)\s*\)?)?\s*$", str(text or ""), re.I)
    return (m.group(1).lower(), m.group(2), (m.group(3) or "").lower()) if m else ()


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    out = _acro.Collector()
    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in CHOICES:
            out.put(k, v, "entered")
    a = intake or {}
    out.name("app", a.get("full_name"))
    for key, src in (("dob", "date_of_birth"), ("passport_expiry", "passport_expiry"), ("last_arrival", "last_entry_date")):
        out.put(key, _acro.mdy(a.get(src)), "intake")
    for key, src in (("country_birth", "country_of_birth"), ("citizenship", "citizenship"), ("passport", "passport_number"),
                     ("a_number", "a_number"), ("i94", "i94_number"), ("phone", "phone"), ("email", "email"),
                     ("current_status", "current_status"), ("arrival_place", "last_entry_port")):
        out.put(key, a.get(src), "intake")
    if a.get("citizenship"):
        out.put("passport_country", a["citizenship"], "derived")
    ms = (a.get("marital_status") or "").strip().title()
    out.put("marital", ms if ms in CHOICES["marital"]["options"] else None, "intake")
    parts = _acro.addr(a.get("us_address"))
    for f in ("street", "unit", "city", "state", "zip"):
        out.put(f"mail_{f}", parts.get(f), "derived")
    ct = case.get("case_type")
    out.put("reason", REASON_BY_CASE.get(ct), "case")
    out.put("current_status", case.get("visa_class"), "case")
    if "current_status" in out:
        out.put("status_at_arrival", out["current_status"]["value"], "derived")
    cat = _category(CATEGORY_BY_CASE.get(ct))
    for i, v in enumerate(cat, 1):
        out.put(f"category_{i}", v, "case")
    out.name("app", case.get("beneficiary"))
    return dict(out)


def edition_ok() -> bool:
    return _acro.edition_ok(sys.modules[__name__])


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    return _acro.preview(sys.modules[__name__], case, intake, form_data)


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    return _acro.fill(sys.modules[__name__], case, intake, form_data)
