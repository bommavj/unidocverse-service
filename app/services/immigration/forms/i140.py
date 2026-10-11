"""
Form I-140 (Immigrant Petition for Alien Workers) auto-fill — edition 06/07/24.

Sources, in priority order: values staff entered on the case (imm_cases.form_data),
the client's intake answers, then the case itself. Field names are this edition's
AcroForm names; re-check them when USCIS publishes a new edition (`edition_ok`).
"""
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

from . import _acro

FORM = "I-140"
FORM_PDF = Path(__file__).with_name("i-140.pdf")
EDITION = "06/07/24"
P = "form1[0].#subform"

TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 1 — petitioner
    "petitioner_company":  (f"{P}[0].Line2_CompanyName[0]", "Petitioner company or organization name", "Part 1. Petitioner"),
    "petitioner_fein":     (f"{P}[0].Pt1Line3_TaxNumber[0]", "Petitioner FEIN", "Part 1. Petitioner"),
    "petitioner_street":   (f"{P}[0].Line6b_StreetNumberName[0]", "Petitioner street number and name", "Part 1. Petitioner"),
    "petitioner_unit":     (f"{P}[0].Line6c_AptSteFlrNumber[0]", "Petitioner suite / floor number", "Part 1. Petitioner"),
    "petitioner_city":     (f"{P}[0].Line6d_CityOrTown[0]", "Petitioner city", "Part 1. Petitioner"),
    "petitioner_state":    (f"{P}[0].Line6e_State[0]", "Petitioner state (2 letters)", "Part 1. Petitioner"),
    "petitioner_zip":      (f"{P}[0].Line6f_ZipCode[0]", "Petitioner ZIP code", "Part 1. Petitioner"),
    # Part 3 — beneficiary
    "bene_family":         (f"{P}[1].Pt3Line1a_FamilyName[0]", "Beneficiary family name", "Part 3. Beneficiary"),
    "bene_given":          (f"{P}[1].Pt3Line1b_GivenName[0]", "Beneficiary given name", "Part 3. Beneficiary"),
    "bene_middle":         (f"{P}[1].Pt3Line1c_MiddleName[0]", "Beneficiary middle name", "Part 3. Beneficiary"),
    "bene_street":         (f"{P}[1].Line2b_StreetNumberName[0]", "Beneficiary street number and name", "Part 3. Beneficiary"),
    "bene_unit":           (f"{P}[1].Line2c_AptSteFlrNumber[0]", "Beneficiary apt / ste / flr number", "Part 3. Beneficiary"),
    "bene_city":           (f"{P}[1].Line2d_CityOrTown[0]", "Beneficiary city", "Part 3. Beneficiary"),
    "bene_state":          (f"{P}[1].Line2e_State[0]", "Beneficiary state (2 letters)", "Part 3. Beneficiary"),
    "bene_zip":            (f"{P}[1].Line2f_ZipCode[0]", "Beneficiary ZIP code", "Part 3. Beneficiary"),
    "bene_dob":            (f"{P}[1].Line5_DateOfBirth[0]", "Date of birth (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_city_birth":     (f"{P}[1].Line6_CityTownOfBirth[0]", "City / town of birth", "Part 3. Beneficiary"),
    "bene_state_birth":    (f"{P}[1].Line7_StateProvinceOfBirth[0]", "State / province of birth", "Part 3. Beneficiary"),
    "bene_country_birth":  (f"{P}[1].Line8_Country[0]", "Country of birth", "Part 3. Beneficiary"),
    "bene_citizenship":    (f"{P}[1].Line9_Country[0]", "Country of citizenship", "Part 3. Beneficiary"),
    "bene_a_number":       (f"{P}[1].Line11_Alien[0].Pt3Line8_AlienNumber[0]", "A-Number (if any)", "Part 3. Beneficiary"),
    "bene_ssn":            (f"{P}[1].Line12_SSN[0]", "U.S. Social Security number (if any)", "Part 3. Beneficiary"),
    "bene_last_arrival":   (f"{P}[1].Line13_DateOArrival[0]", "Date of last arrival (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_i94":            (f"{P}[1].Line14_I94Number[0].Line14a_ArrivalDeparture[0]", "I-94 number", "Part 3. Beneficiary"),
    "bene_passport":       (f"{P}[1].Line14b_Passport[0]", "Passport number", "Part 3. Beneficiary"),
    "bene_passport_country": (f"{P}[1].Line14d_CountryOfIssuance[0]", "Passport country of issuance", "Part 3. Beneficiary"),
    "bene_passport_expiry": (f"{P}[1].Line14e_ExpDate[0]", "Passport expiration (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_i94_expiry":     (f"{P}[1].Line14e_ExpDate[1]", "Stay expires per I-94 (mm/dd/yyyy or D/S)", "Part 3. Beneficiary"),
    "bene_status":         (f"{P}[1].Line15_CurrentNon[0]", "Current nonimmigrant status (e.g. H-1B)", "Part 3. Beneficiary"),
    # Part 4 — processing (consular post if applying abroad)
    "consulate_city":      (f"{P}[1].Line1a_CityorTown[0]", "Consulate city (if applying abroad)", "Part 4. Processing"),
    "consulate_country":   (f"{P}[1].Line1a_Country[0]", "Consulate country (if applying abroad)", "Part 4. Processing"),
    # Part 5 — petitioner's business
    "business_type":       (f"{P}[2].Line2a_TypeofBusiness[0]", "Type of business", "Part 5. Employer"),
    "date_established":    (f"{P}[2].Line2b_DateEstablished[0]", "Date established (mm/dd/yyyy)", "Part 5. Employer"),
    "us_employees":        (f"{P}[2].Line2c_NumberofEmployees[0]", "Current number of U.S. employees", "Part 5. Employer"),
    "gross_income":        (f"{P}[2].Line2d_GrossAnnualIncome[0]", "Gross annual income", "Part 5. Employer"),
    "net_income":          (f"{P}[2].Line2e_NetAnnualIncome[0]", "Net annual income", "Part 5. Employer"),
    "naics":               (f"{P}[2].Line2f[0].Line2f_NAICSCode[0]", "NAICS code", "Part 5. Employer"),
    "perm_case":           (f"{P}[2].Line2g_LaborCertification[0]", "PERM labor certification DOL case number", "Part 5. Employer"),
    "perm_filed":          (f"{P}[3].Line2h_LaborCertification[0]", "PERM filing date (mm/dd/yyyy)", "Part 5. Employer"),
    "perm_expiry":         (f"{P}[3].Line2i_LaborCertificationDate[0]", "PERM expiration date (mm/dd/yyyy)", "Part 5. Employer"),
    # Part 6 — proposed employment
    "job_title":           (f"{P}[3].Line1_JobTitle[0]", "Job title", "Part 6. Employment"),
    "soc_1":               (f"{P}[3].Line2_SOCCode1[0]", "SOC code — first 2 digits", "Part 6. Employment"),
    "soc_2":               (f"{P}[3].Line2_SOCCode2[0]", "SOC code — last 4 digits", "Part 6. Employment"),
    "job_description":     (f"{P}[3].Line3_JobDescription[0]", "Nontechnical job description", "Part 6. Employment"),
    "wages":               (f"{P}[3].Line8_Wages[0]", "Wages ($)", "Part 6. Employment"),
    "wages_per":           (f"{P}[3].Line8_Per[0]", "Wages per (hour, week, month or year)", "Part 6. Employment"),
    "work_street":         (f"{P}[3].Line9a_StreetNumberName[0]", "Work site street address", "Part 6. Employment"),
    "work_unit":           (f"{P}[3].Line9b_AptSteFlrNumber[0]", "Work site suite / floor", "Part 6. Employment"),
    "work_city":           (f"{P}[3].Line9c_CityOrTown[0]", "Work site city", "Part 6. Employment"),
    "work_state":          (f"{P}[3].Line9d_State[0]", "Work site state (2 letters)", "Part 6. Employment"),
    "work_zip":            (f"{P}[3].Line9e_ZipCode[0]", "Work site ZIP code", "Part 6. Employment"),
    # Part 7 — signatory (signature stays blank)
    "signatory_family":    (f"{P}[5].Part7_Item3a_FamilyName[0]", "Authorized signatory family name", "Part 7. Signatory"),
    "signatory_given":     (f"{P}[5].Part7_Item3b_GivenName[0]", "Authorized signatory given name", "Part 7. Signatory"),
    "signatory_title":     (f"{P}[5].Part7_Item4_Title[0]", "Authorized signatory title", "Part 7. Signatory"),
    "signatory_phone":     (f"{P}[5].Part7_Item5_DayPhone[0]", "Signatory daytime phone", "Part 7. Signatory"),
    "signatory_email":     (f"{P}[5].Part7_Item7_Email[0]", "Signatory email", "Part 7. Signatory"),
}

_T = lambda sub, v: (f"{P}[{sub}].prt2PetitionType", v)
CHOICES = {
    "petition_type": {"label": "Petition type", "group": "Part 2. Petition type", "required": True,
                      "options": {"EB-1A": _T(0, "a"), "EB-1B": _T(0, "b"), "EB-1C": _T(0, "c"), "EB-2": _T(0, "d"),
                                  "EB-2 NIW": _T(1, "h"), "EB-3 professional": _T(0, "e"),
                                  "EB-3 skilled": _T(1, "f"), "EB-3 other": _T(1, "g")}},
    "processing": {"label": "Beneficiary will", "group": "Part 4. Processing", "required": True,
                   "options": {"adjust status": (f"{P}[1].Line1b_Status", "1b"),
                               "apply abroad": (f"{P}[1].Line1a_Visa", "1a")}},
}

REQUIRED = ["petitioner_company", "petitioner_fein", "petitioner_street", "petitioner_city", "petitioner_state",
            "petitioner_zip", "bene_family", "bene_given", "bene_dob", "bene_country_birth", "bene_citizenship",
            "job_title", "wages", "wages_per", "work_street", "work_city", "work_state", "work_zip",
            "signatory_family", "signatory_given", "signatory_title"]


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    out = _acro.Collector()
    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in CHOICES:
            out.put(k, v, "entered")
    a = intake or {}
    out.name("bene", a.get("full_name"))
    for key, src in (("bene_dob", "date_of_birth"), ("bene_passport_expiry", "passport_expiry"),
                     ("bene_last_arrival", "last_entry_date")):
        out.put(key, _acro.mdy(a.get(src)), "intake")
    for key, src in (("bene_country_birth", "country_of_birth"), ("bene_citizenship", "citizenship"),
                     ("bene_passport", "passport_number"), ("bene_i94", "i94_number"),
                     ("bene_status", "current_status"), ("job_title", "job_title"), ("petitioner_company", "employer")):
        out.put(key, a.get(src), "intake")
    if a.get("a_number"):
        out.put("bene_a_number", re.sub(r"\D", "", str(a["a_number"])), "intake")
    if a.get("citizenship"):
        out.put("bene_passport_country", a["citizenship"], "derived")
    if a.get("annual_salary"):
        out.put("wages", re.sub(r"[^\d.]", "", str(a["annual_salary"])), "intake")
        out.put("wages_per", "year", "derived")
    for prefix, src in (("bene", "us_address"), ("work", "work_address")):
        parts = _acro.addr(a.get(src))
        for f in ("street", "unit", "city", "state", "zip"):
            out.put(f"{prefix}_{f}", parts.get(f), "derived")
    if out.get("bene_state"):                            # living in the U.S. → adjustment is the usual path
        out.put("processing", "adjust status", "derived")
    out.put("petitioner_company", case.get("petitioner"), "case")
    out.put("bene_status", case.get("visa_class"), "case")
    out.name("bene", case.get("beneficiary"))
    if "perm_case" in out:                                   # the form's box holds 15 characters: no dashes
        out["perm_case"]["value"] = re.sub(r"[\s-]", "", out["perm_case"]["value"]).upper()
    return dict(out)


def edition_ok() -> bool:
    return _acro.edition_ok(sys.modules[__name__])


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    return _acro.preview(sys.modules[__name__], case, intake, form_data)


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    return _acro.fill(sys.modules[__name__], case, intake, form_data)
