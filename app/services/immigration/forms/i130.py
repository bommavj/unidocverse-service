"""
Form I-130 (Petition for Alien Relative) auto-fill — edition 04/01/24.

The client's intake answers describe the beneficiary (the relative being petitioned
for); the petitioner (U.S. citizen or permanent resident) comes from the case and from
values staff enter. Parent, prior-spouse and child lists and the biographic section
(Part 3) are left for staff. Sources, in priority order: entered values, intake, case.
"""
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

from . import _acro

FORM = "I-130"
FORM_PDF = Path(__file__).with_name("i-130.pdf")
EDITION = "04/01/24"
P = "form1[0].#subform"

TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 2 — petitioner
    "pet_family":        (f"{P}[0].Pt2Line4a_FamilyName[0]", "Petitioner family name", "Part 2. Petitioner"),
    "pet_given":         (f"{P}[0].Pt2Line4b_GivenName[0]", "Petitioner given name", "Part 2. Petitioner"),
    "pet_middle":        (f"{P}[0].Pt2Line4c_MiddleName[0]", "Petitioner middle name", "Part 2. Petitioner"),
    "pet_a_number":      (f"{P}[0].#area[4].Pt2Line1_AlienNumber[0]", "Petitioner A-Number (9 digits, if any)", "Part 2. Petitioner"),
    "pet_ssn":           (f"{P}[0].Pt2Line11_SSN[0]", "Petitioner SSN", "Part 2. Petitioner"),
    "pet_city_birth":    (f"{P}[1].Pt2Line6_CityTownOfBirth[0]", "Petitioner city of birth", "Part 2. Petitioner"),
    "pet_country_birth": (f"{P}[1].Pt2Line7_CountryofBirth[0]", "Petitioner country of birth", "Part 2. Petitioner"),
    "pet_dob":           (f"{P}[1].Pt2Line8_DateofBirth[0]", "Petitioner date of birth (mm/dd/yyyy)", "Part 2. Petitioner"),
    "pet_street":        (f"{P}[1].Pt2Line10_StreetNumberName[0]", "Petitioner mailing street", "Part 2. Petitioner"),
    "pet_unit":          (f"{P}[1].Pt2Line10_AptSteFlrNumber[0]", "Petitioner apt / ste / flr number", "Part 2. Petitioner"),
    "pet_city":          (f"{P}[1].Pt2Line10_CityOrTown[0]", "Petitioner city", "Part 2. Petitioner"),
    "pet_state":         (f"{P}[1].Pt2Line10_State[0]", "Petitioner state (2 letters)", "Part 2. Petitioner"),
    "pet_zip":           (f"{P}[1].Pt2Line10_ZipCode[0]", "Petitioner ZIP code", "Part 2. Petitioner"),
    "pet_marriages":     (f"{P}[1].Pt2Line16_NumberofMarriages[0]", "Petitioner — times married", "Part 2. Petitioner"),
    "pet_marriage_date": (f"{P}[2].Pt2Line18_DateOfMarriage[0]", "Petitioner — date of current marriage", "Part 2. Petitioner"),
    "pet_phone":         (f"{P}[8].Pt6Line3_DaytimePhoneNumber[0]", "Petitioner daytime phone (10 digits)", "Part 6. Petitioner contact"),
    "pet_mobile":        (f"{P}[8].Pt6Line4_MobileNumber[0]", "Petitioner mobile phone (10 digits)", "Part 6. Petitioner contact"),
    "pet_email":         (f"{P}[8].Pt6Line5_Email[0]", "Petitioner email", "Part 6. Petitioner contact"),
    # Part 4 — beneficiary
    "bene_family":       (f"{P}[4].Pt4Line4a_FamilyName[0]", "Beneficiary family name", "Part 4. Beneficiary"),
    "bene_given":        (f"{P}[4].Pt4Line4b_GivenName[0]", "Beneficiary given name", "Part 4. Beneficiary"),
    "bene_middle":       (f"{P}[4].Pt4Line4c_MiddleName[0]", "Beneficiary middle name", "Part 4. Beneficiary"),
    "bene_a_number":     (f"{P}[4].#area[6].Pt4Line1_AlienNumber[0]", "Beneficiary A-Number (9 digits, if any)", "Part 4. Beneficiary"),
    "bene_ssn":          (f"{P}[4].Pt4Line3_SSN[0]", "Beneficiary SSN (if any)", "Part 4. Beneficiary"),
    "bene_city_birth":   (f"{P}[4].Pt4Line7_CityTownOfBirth[0]", "Beneficiary city of birth", "Part 4. Beneficiary"),
    "bene_country_birth": (f"{P}[4].Pt4Line8_CountryOfBirth[0]", "Beneficiary country of birth", "Part 4. Beneficiary"),
    "bene_dob":          (f"{P}[4].Pt4Line9_DateOfBirth[0]", "Beneficiary date of birth (mm/dd/yyyy)", "Part 4. Beneficiary"),
    "bene_street":       (f"{P}[4].Pt4Line11_StreetNumberName[0]", "Beneficiary street (physical address)", "Part 4. Beneficiary"),
    "bene_unit":         (f"{P}[4].Pt4Line11_AptSteFlrNumber[0]", "Beneficiary apt / ste / flr number", "Part 4. Beneficiary"),
    "bene_city":         (f"{P}[4].Pt4Line11_CityOrTown[0]", "Beneficiary city", "Part 4. Beneficiary"),
    "bene_state":        (f"{P}[4].Pt4Line11_State[0]", "Beneficiary state (2 letters)", "Part 4. Beneficiary"),
    "bene_zip":          (f"{P}[4].Pt4Line11_ZipCode[0]", "Beneficiary ZIP code", "Part 4. Beneficiary"),
    "bene_phone":        (f"{P}[4].Pt4Line14_DaytimePhoneNumber[0]", "Beneficiary daytime phone", "Part 4. Beneficiary"),
    "bene_mobile":       (f"{P}[5].Pt4Line15_MobilePhoneNumber[0]", "Beneficiary mobile phone", "Part 4. Beneficiary"),
    "bene_email":        (f"{P}[5].Pt4Line16_EmailAddress[0]", "Beneficiary email", "Part 4. Beneficiary"),
    "bene_marriages":    (f"{P}[5].Pt4Line17_NumberofMarriages[0]", "Beneficiary — times married", "Part 4. Beneficiary"),
    "bene_marriage_date": (f"{P}[5].Pt4Line19_DateOfMarriage[0]", "Beneficiary — date of current marriage", "Part 4. Beneficiary"),
    "bene_admission_class": (f"{P}[6].Pt4Line21a_ClassOfAdmission[0]", "Beneficiary arrived as (class of admission, e.g. H-4)", "Part 4. Beneficiary in the U.S."),
    "bene_i94":          (f"{P}[6].#area[8].Pt4Line21b_ArrivalDeparture[0]", "Beneficiary I-94 number", "Part 4. Beneficiary in the U.S."),
    "bene_last_arrival": (f"{P}[6].Pt4Line21c_DateOfArrival[0]", "Beneficiary date of arrival (mm/dd/yyyy)", "Part 4. Beneficiary in the U.S."),
    "bene_stay_expires": (f"{P}[6].Pt4Line21d_DateExpired[0]", "Authorized stay expires (mm/dd/yyyy or D/S)", "Part 4. Beneficiary in the U.S."),
    "bene_passport":     (f"{P}[6].Pt4Line22_PassportNumber[0]", "Beneficiary passport number", "Part 4. Beneficiary in the U.S."),
    "bene_passport_country": (f"{P}[6].Pt4Line24_CountryOfIssuance[0]", "Passport country of issuance", "Part 4. Beneficiary in the U.S."),
    "bene_passport_expiry": (f"{P}[6].Pt4Line25_ExpDate[0]", "Passport expiration (mm/dd/yyyy)", "Part 4. Beneficiary in the U.S."),
    "bene_employer":     (f"{P}[6].Pt4Line26_NameOfCompany[0]", "Beneficiary's current employer", "Part 4. Beneficiary in the U.S."),
}
COMBO_CODE = {"bene_admission_class": lambda v: re.sub(r"[^A-Z0-9]", "", v.upper()), "pet_state": str.upper,
              "bene_state": str.upper}

_y = lambda name: (f"{P}[{name[0]}].{name[1]}", "Y")
_MARITAL_PET = {s: _y((1, f"Pt2Line17_{s}")) for s in ("Single", "Married", "Divorced", "Widowed", "Separated", "Annulled")}
_MARITAL_BENE = {"Single": "SNM", "Married": "M", "Divorced": "D", "Widowed": "W", "Separated": "S", "Annulled": "A"}
CHOICES = {
    "relationship": {"label": "I am filing for my", "group": "Part 1. Relationship", "required": True,
                     "options": {"spouse": _y((0, "Pt1Line1_Spouse")), "parent": _y((0, "Pt1Line1_Parent")),
                                 "sibling": _y((0, "Pt1Line1_Siblings")), "child": _y((0, "Pt1Line1_Child"))}},
    "pet_status": {"label": "Petitioner is a", "group": "Part 2. Petitioner", "required": True,
                   "options": {"U.S. citizen": _y((2, "Pt2Line36_USCitizen")), "LPR": (f"{P}[2].Pt2Line36_LPR", "N")}},
    "pet_marital": {"label": "Petitioner marital status", "group": "Part 2. Petitioner", "options": _MARITAL_PET},
    "bene_sex": {"label": "Beneficiary sex", "group": "Part 4. Beneficiary", "required": True,
                 "options": {"M": _y((4, "Pt4Line9_Male")), "F": _y((4, "Pt4Line9_Female"))}},
    "bene_marital": {"label": "Beneficiary marital status", "group": "Part 4. Beneficiary",
                     "options": {k: (f"{P}[5].Pt4Line18_MaritalStatus", v) for k, v in _MARITAL_BENE.items()}},
}

REQUIRED = ["pet_family", "pet_given", "pet_dob", "pet_country_birth", "pet_street", "pet_city", "pet_state",
            "pet_zip", "pet_phone", "bene_family", "bene_given", "bene_dob", "bene_country_birth"]


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    out = _acro.Collector()
    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in CHOICES:
            out.put(k, v, "entered")
    a = intake or {}
    out.name("bene", a.get("full_name"))
    for key, src in (("bene_dob", "date_of_birth"), ("bene_passport_expiry", "passport_expiry"),
                     ("bene_last_arrival", "last_entry_date"), ("bene_stay_expires", "status_expiry")):
        out.put(key, _acro.mdy(a.get(src)), "intake")
    for key, src in (("bene_country_birth", "country_of_birth"), ("bene_passport", "passport_number"),
                     ("bene_i94", "i94_number"), ("bene_phone", "phone"), ("bene_email", "email"),
                     ("bene_employer", "employer"), ("bene_admission_class", "current_status")):
        out.put(key, a.get(src), "intake")
    if a.get("a_number"):
        out.put("bene_a_number", re.sub(r"\D", "", str(a["a_number"])), "intake")
    if a.get("citizenship"):
        out.put("bene_passport_country", a["citizenship"], "derived")
    parts = _acro.addr(a.get("us_address"))
    for f in ("street", "unit", "city", "state", "zip"):
        out.put(f"bene_{f}", parts.get(f), "derived")
    ms = (a.get("marital_status") or "").strip().title()
    out.put("bene_marital", ms if ms in _MARITAL_BENE else None, "intake")
    if a.get("marriage_date"):
        out.put("bene_marriage_date", _acro.mdy(a["marriage_date"]), "intake")
    # A married beneficiary's spouse is usually the petitioner — staff confirm via "relationship"
    rel = out.get("relationship", {}).get("value")
    spouse_case = rel == "spouse" or (not rel and ms == "Married")
    if spouse_case:
        out.put("relationship", "spouse", "derived")
        out.name("pet", a.get("spouse_name"))
        out.put("pet_dob", _acro.mdy(a.get("spouse_dob")), "derived")
        out.put("pet_marriage_date", _acro.mdy(a.get("marriage_date")), "derived")
        out.put("pet_marital", "Married", "derived")
        for f in ("street", "unit", "city", "state", "zip"):             # spouses usually share an address
            out.put(f"pet_{f}", parts.get(f), "derived")
    out.name("pet", case.get("petitioner"))
    out.name("bene", case.get("beneficiary"))
    for k in ("pet_phone", "pet_mobile"):
        if k in out:
            out[k]["value"] = re.sub(r"\D", "", out[k]["value"])[-10:]
    return dict(out)


def edition_ok() -> bool:
    return _acro.edition_ok(sys.modules[__name__])


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    return _acro.preview(sys.modules[__name__], case, intake, form_data)


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    return _acro.fill(sys.modules[__name__], case, intake, form_data)
