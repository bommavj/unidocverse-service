"""
Form I-485 (Application to Register Permanent Residence or Adjust Status) auto-fill — edition 09/18/26.

Covers the identity, address, travel, filing-category and contact sections. The
eligibility, public-charge and inadmissibility questions (Parts 8-9) are yes/no
attestations only the applicant can answer, so they are never pre-filled.

Sources, in priority order: values staff entered on the case (imm_cases.form_data),
the client's intake answers, then the case itself. Field names are this edition's
AcroForm names; re-check them when USCIS publishes a new edition (`edition_ok`).
"""
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

from . import _acro

FORM = "I-485"
FORM_PDF = Path(__file__).with_name("i-485.pdf")
EDITION = "09/18/26"
P = "form1[0].#subform"

TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 1 — applicant
    "app_family":        (f"{P}[0].Pt1Line1_FamilyName[0]", "Family name", "Part 1. Applicant"),
    "app_given":         (f"{P}[0].Pt1Line1_GivenName[0]", "Given name", "Part 1. Applicant"),
    "app_middle":        (f"{P}[0].Pt1Line1_MiddleName[0]", "Middle name", "Part 1. Applicant"),
    "dob":               (f"{P}[0].Pt1Line3_DOB[0]", "Date of birth (mm/dd/yyyy)", "Part 1. Applicant"),
    "a_number":          (f"{P}[0].Pt1Line4_AlienNumber[0]", "A-Number (9 digits, if any)", "Part 1. Applicant"),
    "city_birth":        (f"{P}[1].Pt1Line7_CityTownOfBirth[0]", "City / town of birth", "Part 1. Applicant"),
    "country_birth":     (f"{P}[1].Pt1Line7_CountryOfBirth[0]", "Country of birth", "Part 1. Applicant"),
    "citizenship":       (f"{P}[1].Pt1Line8_CountryofCitizenshipNationality[0]", "Country of citizenship", "Part 1. Applicant"),
    "uscis_account":     (f"{P}[1].Pt1Line9_USCISAccountNumber[0]", "USCIS online account number (if any)", "Part 1. Applicant"),
    "ssn":               (f"{P}[3].Pt1Line19_SSN[0]", "U.S. Social Security number (if any)", "Part 1. Applicant"),
    # Part 1 — last arrival
    "passport":          (f"{P}[1].Pt1Line10_PassportNum[0]", "Passport number used at last arrival", "Part 1. Last arrival"),
    "passport_expiry":   (f"{P}[1].Pt1Line10_ExpDate[0]", "Passport expiration (mm/dd/yyyy)", "Part 1. Last arrival"),
    "arrival_city":      (f"{P}[1].Pt1Line10_CityTown[0]", "Place of last arrival — city", "Part 1. Last arrival"),
    "arrival_state":     (f"{P}[1].Pt1Line10_State[0]", "Place of last arrival — state (2 letters)", "Part 1. Last arrival"),
    "last_arrival":      (f"{P}[1].Pt1Line10_DateofArrival[0]", "Date of last arrival (mm/dd/yyyy)", "Part 1. Last arrival"),
    "admitted_as":       (f"{P}[1].Pt1Line11_Admitted[0]", "Admitted as (e.g. H-1B temporary worker)", "Part 1. Last arrival"),
    "i94":               (f"{P}[2].P1Line12_I94[0]", "I-94 number", "Part 1. I-94"),
    "i94_family":        (f"{P}[2].P1Line12_FamilyName[0]", "Family name as on I-94", "Part 1. I-94"),
    "i94_given":         (f"{P}[2].P1Line13_GivenName[0]", "Given name as on I-94", "Part 1. I-94"),
    "i94_expiry":        (f"{P}[2].Pt1Line12_Date[0]", "Authorized stay expires per I-94 (mm/dd/yyyy or D/S)", "Part 1. I-94"),
    "i94_status":        (f"{P}[2].Pt1Line12_Status[0]", "Status on I-94 (e.g. H-1B)", "Part 1. I-94"),
    "current_status":    (f"{P}[2].Pt1Line14_Status[0]", "Current status, if changed since arrival", "Part 1. I-94"),
    "status_expiry":     (f"{P}[2].Pt1Line15_Date[0]", "Current status expires (mm/dd/yyyy or D/S)", "Part 1. I-94"),
    # Part 1 — addresses
    "phys_care_of":      (f"{P}[2].Part1_Item18_InCareOfName[0]", "Physical address — in care of", "Part 1. Address"),
    "phys_street":       (f"{P}[2].Pt1Line18_StreetNumberName[0]", "Physical street number and name", "Part 1. Address"),
    "phys_unit":         (f"{P}[2].Pt1Line18US_AptSteFlrNumber[0]", "Physical apt / ste / flr number", "Part 1. Address"),
    "phys_city":         (f"{P}[2].Pt1Line18_CityOrTown[0]", "Physical city", "Part 1. Address"),
    "phys_state":        (f"{P}[2].Pt1Line18_State[0]", "Physical state (2 letters)", "Part 1. Address"),
    "phys_zip":          (f"{P}[2].Pt1Line18_ZipCode[0]", "Physical ZIP code", "Part 1. Address"),
    "phys_since":        (f"{P}[2].Pt1Line18_Date[0]", "Living at this address since (mm/dd/yyyy)", "Part 1. Address"),
    # Part 2 — filing category
    "underlying_receipt": (f"{P}[4].Pt2Line2_Receipt[0]", "Receipt number of the underlying petition (I-140 / I-130)", "Part 2. Category"),
    "priority_date":     (f"{P}[4].Pt2Line2_Date[0]", "Priority date (mm/dd/yyyy)", "Part 2. Category"),
    "principal_family":  (f"{P}[4].Pt2Line2_FamilyName[0]", "Principal applicant family name (derivatives only)", "Part 2. Category"),
    "principal_given":   (f"{P}[4].Pt2Line2_GivenName[0]", "Principal applicant given name (derivatives only)", "Part 2. Category"),
    "principal_a_number": (f"{P}[4].Pt2Line2_AlienNumber[0]", "Principal applicant A-Number (derivatives only)", "Part 2. Category"),
    "principal_dob":     (f"{P}[4].Pt1Line2_DOB[0]", "Principal applicant date of birth (derivatives only)", "Part 2. Category"),
    # Part 10 — contact (signature stays blank)
    "phone":             (f"{P}[22].Pt3Line3_DaytimePhoneNumber1[0]", "Daytime phone (10 digits)", "Part 10. Contact"),
    "mobile":            (f"{P}[22].Pt3Line4_MobileNumber1[0]", "Mobile phone (10 digits)", "Part 10. Contact"),
    "email":             (f"{P}[22].Pt3Line5_Email[0]", "Email", "Part 10. Contact"),
}
# The A-Number repeats in every page header and in Part 1, Item 4
MIRRORS = {"a_number": r"\.Pt1Line4_AlienNumber\[\d+\]$"}

_EB = lambda i, v: (f"{P}[5].Pt2Line3b_CB140", v)
_FB = lambda v: (f"{P}[4].Pt2Line3a_CB", v)
CHOICES = {
    "applicant_type": {"label": "I am the", "group": "Part 2. Category", "required": True,
                       "options": {"principal": (f"{P}[4].Pt2Line2_CB", "1fA"), "derivative": (f"{P}[4].Pt2Line2_CB", "1fB")}},
    "category": {"label": "Filing category", "group": "Part 2. Category", "required": True,
                 "options": {"EB-1A": _EB(0, "3b1"), "EB-1B": _EB(1, "3b2"), "EB-1C": _EB(2, "3b3"), "EB-2": _EB(3, "3b4"),
                             "EB-2 NIW": _EB(7, "3b8"), "EB-3 professional": _EB(4, "3b5"), "EB-3 skilled": _EB(5, "3b6"),
                             "EB-3 other": _EB(6, "3b7"), "EB-5": (f"{P}[5].Pt2Line3b_CB526", "3b0"),
                             "spouse of USC": _FB("3a0"), "child of USC": _FB("3a1"), "parent of USC": _FB("3a2"),
                             "K-1 fiancé(e)": _FB("3a3"), "spouse of LPR": _FB("3a9"), "child of LPR": _FB("3a10")}},
    "sex": {"label": "Sex", "group": "Part 1. Applicant", "required": True,
            "options": {"M": (f"{P}[1].Pt1Line6_CB_Sex", "M"), "F": (f"{P}[1].Pt1Line6_CB_Sex", "F")}},
}
CATEGORY_BY_VISA = {"EB-1": None, "EB-2": "EB-2", "EB-3": "EB-3 professional", "EB-5": "EB-5"}

REQUIRED = ["app_family", "app_given", "dob", "country_birth", "citizenship", "passport", "last_arrival",
            "i94", "i94_status", "phys_street", "phys_city", "phys_state", "phys_zip", "underlying_receipt", "phone"]


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    out = _acro.Collector()
    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in CHOICES:
            out.put(k, v, "entered")
    a = intake or {}
    out.name("app", a.get("full_name"))
    for key, src in (("dob", "date_of_birth"), ("passport_expiry", "passport_expiry"), ("last_arrival", "last_entry_date"),
                     ("i94_expiry", "status_expiry")):
        out.put(key, _acro.mdy(a.get(src)), "intake")
    for key, src in (("country_birth", "country_of_birth"), ("citizenship", "citizenship"), ("passport", "passport_number"),
                     ("i94", "i94_number"), ("i94_status", "current_status"), ("phone", "phone"), ("email", "email")):
        out.put(key, a.get(src), "intake")
    if a.get("a_number"):
        out.put("a_number", re.sub(r"\D", "", str(a["a_number"])), "intake")
    if a.get("phone"):
        out["phone"] = {"value": re.sub(r"\D", "", out["phone"]["value"])[-10:], "source": out["phone"]["source"]}
    port = _acro.addr(f"x, {a['last_entry_port']} 00000") if a.get("last_entry_port") else {}
    out.put("arrival_city", port.get("city"), "derived")
    out.put("arrival_state", port.get("state"), "derived")
    parts = _acro.addr(a.get("us_address"))
    for f in ("street", "unit", "city", "state", "zip"):
        out.put(f"phys_{f}", parts.get(f), "derived")
    for k in ("family", "given"):
        if f"app_{k}" in out:
            out.put(f"i94_{k}", out[f"app_{k}"]["value"], "derived")
    out.put("applicant_type", "principal", "derived")              # derivatives are rarer — staff override
    out.put("category", CATEGORY_BY_VISA.get((case.get("visa_class") or "").upper()), "case")
    out.name("app", case.get("beneficiary"))
    return dict(out)


def edition_ok() -> bool:
    return _acro.edition_ok(sys.modules[__name__])


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    return _acro.preview(sys.modules[__name__], case, intake, form_data)


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    return _acro.fill(sys.modules[__name__], case, intake, form_data)
