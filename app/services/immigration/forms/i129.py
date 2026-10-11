"""
Form I-129 (Petition for a Nonimmigrant Worker) auto-fill — edition 09/09/26.

Fills the official USCIS AcroForm (forms/i-129.pdf) from, in priority order:
  1. values staff confirmed or entered on the case (imm_cases.form_data)
  2. the client's submitted intake answers
  3. the case itself (petitioner, visa class, receipt, case type)

Never filled: signatures, signature dates, the 2-D barcodes (Adobe Reader regenerates
those when the form is opened and saved). Nothing is invented — a value that isn't
in any source stays blank and is reported as missing; values derived by rule (a
full name split into family / given / middle) are reported for review.

Field names below are the form's own AcroForm names for this edition. When USCIS
publishes a new edition, re-check them: `edition_ok()` compares the edition date.
"""
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FORM_PDF = Path(__file__).with_name("i-129.pdf")
EDITION = "09/09/26"
P = "form1[0].#subform"

# key → (field name, label shown to staff, group)
TEXT_FIELDS: Dict[str, Tuple[str, str, str]] = {
    # Part 1 — petitioner
    "petitioner_company":   (f"{P}[0].Line3_CompanyorOrgName[0]", "Petitioner company or organization name", "Part 1. Petitioner"),
    "petitioner_street":    (f"{P}[0].Line7b_StreetNumberName[0]", "Petitioner street number and name", "Part 1. Petitioner"),
    "petitioner_unit":      (f"{P}[0].Line3_AptSteFlrNumber[0]", "Petitioner suite / floor number", "Part 1. Petitioner"),
    "petitioner_city":      (f"{P}[0].Line_CityTown[0]", "Petitioner city", "Part 1. Petitioner"),
    "petitioner_state":     (f"{P}[0].P1_Line3_State[0]", "Petitioner state (2 letters)", "Part 1. Petitioner"),
    "petitioner_zip":       (f"{P}[0].P1_Line3_ZipCode[0]", "Petitioner ZIP code", "Part 1. Petitioner"),
    "petitioner_phone":     (f"{P}[0].Line2_DaytimePhoneNumber1_Part8[0]", "Petitioner daytime phone", "Part 1. Petitioner"),
    "petitioner_email":     (f"{P}[0].Line9_EmailAddress[0]", "Petitioner email", "Part 1. Petitioner"),
    "petitioner_fein":      (f"{P}[0].TextField1[0]", "Federal Employer Identification Number (FEIN)", "Part 1. Petitioner"),
    # Part 2 — petition
    "classification":       (f"{P}[1].Part2_ClassificationSymbol[0]", "Requested classification symbol (e.g. H-1B)", "Part 2. Petition"),
    "prior_receipt":        (f"{P}[1].Line1_ReceiptNumber[0]", "Most recent prior petition receipt number", "Part 2. Petition"),
    # Part 3 — beneficiary
    "bene_family":          (f"{P}[1].Part3_Line2_FamilyName[0]", "Beneficiary family name", "Part 3. Beneficiary"),
    "bene_given":           (f"{P}[1].Part3_Line2_GivenName[0]", "Beneficiary given name", "Part 3. Beneficiary"),
    "bene_middle":          (f"{P}[1].Part3_Line2_MiddleName[0]", "Beneficiary middle name", "Part 3. Beneficiary"),
    "bene_dob":             (f"{P}[2].Line6_DateOfBirth[0]", "Date of birth (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_a_number":        (f"{P}[2].Line1_AlienNumber[0]", "A-Number (if any)", "Part 3. Beneficiary"),
    "bene_country_birth":   (f"{P}[2].Part3Line4_CountryOfBirth[0]", "Country of birth", "Part 3. Beneficiary"),
    "bene_citizenship":     (f"{P}[2].Part3Line4_CountryOfCitizenship[0]", "Country of citizenship", "Part 3. Beneficiary"),
    "bene_last_arrival":    (f"{P}[2].Part3Line5_DateofArrival[0]", "Date of last arrival (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_i94":             (f"{P}[2].Part3Line5_ArrivalDeparture[0]", "I-94 number", "Part 3. Beneficiary"),
    "bene_passport":        (f"{P}[2].Part3Line5_PassportorTravDoc[0]", "Passport number", "Part 3. Beneficiary"),
    "bene_passport_country": (f"{P}[2].Line_CountryOfIssuance[0]", "Passport country of issuance", "Part 3. Beneficiary"),
    "bene_passport_expiry": (f"{P}[2].Line11e_ExpDate[0]", "Passport expiration (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_status_expiry":   (f"{P}[2].Line11h_DateStatusExpires[0]", "Current status expires (mm/dd/yyyy)", "Part 3. Beneficiary"),
    "bene_street":          (f"{P}[2].Line8a_StreetNumberName[0]", "Beneficiary U.S. street address", "Part 3. Beneficiary"),
    "bene_city":            (f"{P}[2].Line8d_CityTown[0]", "Beneficiary city", "Part 3. Beneficiary"),
    "bene_state":           (f"{P}[2].Line8e_State[0]", "Beneficiary state (2 letters)", "Part 3. Beneficiary"),
    "bene_zip":             (f"{P}[2].Line8f_ZipCode[0]", "Beneficiary ZIP code", "Part 3. Beneficiary"),
    # Part 5 — employment
    "job_title":            (f"{P}[4].Part5_Q1_JobTitle[0]", "Job title", "Part 5. Employment"),
    "lca_number":           (f"{P}[4].Part5_Q2_LCAorETA[0]", "LCA case number", "Part 5. Employment"),
    "work_street":          (f"{P}[4].P5Line3a_StreetNumberName[0]", "Work site street address", "Part 5. Employment"),
    "work_city":            (f"{P}[4].P5Line3a_CityTown[0]", "Work site city", "Part 5. Employment"),
    "work_state":           (f"{P}[4].P5Line3a_State[0]", "Work site state (2 letters)", "Part 5. Employment"),
    "work_zip":             (f"{P}[4].P5Line3a_ZipCode[0]", "Work site ZIP code", "Part 5. Employment"),
    "hours_per_week":       (f"{P}[4].P5Line9_Hours[0]", "Hours per week", "Part 5. Employment"),
    "wages":                (f"{P}[4].Line8_Wages[0]", "Wages ($)", "Part 5. Employment"),
    "wages_per":            (f"{P}[4].Line8_Per[0]", "Wages per (hour, week, month or year)", "Part 5. Employment"),
    "employment_from":      (f"{P}[4].Part5_Q10_DateFrom[0]", "Intended employment from (mm/dd/yyyy)", "Part 5. Employment"),
    "employment_to":        (f"{P}[4].Part5_Q10_DateTo[0]", "Intended employment to (mm/dd/yyyy)", "Part 5. Employment"),
    "business_type":        (f"{P}[5].Part5Line12_TypeofBusiness[0]", "Type of business", "Part 5. Employment"),
    "year_established":     (f"{P}[5].P5Line13_YearEstablished[0]", "Year established", "Part 5. Employment"),
    "us_employees":         (f"{P}[5].P5Line14_NumberofEmployees[0]", "Current number of U.S. employees", "Part 5. Employment"),
    "gross_income":         (f"{P}[5].Line15_GrossAnnualIncome[0]", "Gross annual income", "Part 5. Employment"),
    "net_income":           (f"{P}[5].Line16_NetAnnualIncome[0]", "Net annual income", "Part 5. Employment"),
    # Part 7 / 8 — signatory and preparer (names only; signatures stay blank)
    "signatory_family":     (f"{P}[5].Line1a_PetitionerLastName[0]", "Authorized signatory family name", "Part 7. Signatory"),
    "signatory_given":      (f"{P}[5].Line1b_PetitionerFirstName[0]", "Authorized signatory given name", "Part 7. Signatory"),
    "signatory_title":      (f"{P}[5].Line1a_PetitionerLastName[1]", "Authorized signatory title", "Part 7. Signatory"),
    "preparer_family":      (f"{P}[6].Line_PreparerFamilyName[0]", "Preparer family name", "Part 8. Preparer"),
    "preparer_given":       (f"{P}[6].Line_PreparerGivenName[0]", "Preparer given name", "Part 8. Preparer"),
    "preparer_business":    (f"{P}[6].Line_BusinessName[0]", "Preparer business name", "Part 8. Preparer"),
}
# Dropdowns among the text keys above (value must be one of the form's choices)
COMBOS = {"petitioner_state", "bene_state", "work_state"}
STATUS_COMBO = f"{P}[2].Line11g_CurrentNon[0]"
# Part 2, Item 2 — basis for classification (checkbox per option)
BASIS = {"new": f"{P}[1].new[0]", "continuation": f"{P}[1].continuation[0]", "previous_change": f"{P}[1].previouschange[0]",
         "concurrent": f"{P}[1].concurrent[0]", "change_of_employer": f"{P}[1].change[0]", "amended": f"{P}[1].amended[0]"}
BASIS_BY_CASE = {"h1b_extension": "continuation", "h1b_transfer": "change_of_employer"}
# Part 2, Item 4 — requested action; C = extend the stay (beneficiary is in the U.S. in the same classification)
ACTION = f"{P}[1].P2Checkbox4"
ACTION_BY_CASE = {"h1b_extension": "C", "h1b_transfer": "C"}
SEX = f"{P}[2].Line1_Gender_P3"          # [0] = M, [1] = F

# Never touched
NEVER = re.compile(r"Signature|DateofSignature|DateOfSignature|BarCode", re.I)

REQUIRED = ["petitioner_company", "petitioner_street", "petitioner_city", "petitioner_state", "petitioner_zip",
            "petitioner_fein", "classification", "bene_family", "bene_given", "bene_dob", "bene_country_birth",
            "bene_citizenship", "bene_passport", "bene_passport_expiry", "job_title", "lca_number", "work_street",
            "work_city", "work_state", "work_zip", "wages", "wages_per", "employment_from", "employment_to",
            "signatory_family", "signatory_given", "signatory_title"]


def edition_ok() -> bool:
    import fitz
    with fitz.open(FORM_PDF) as d:
        return f"Edition {EDITION}" in " ".join(d[0].get_text().split())


def _mdy(v) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(2)):02d}/{int(m.group(3)):02d}/{m.group(1)}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}" if m else s


_US_ADDR = re.compile(r"^\s*(?P<street>[^,\n]+?)\s*(?:,\s*(?:apt|ste|suite|unit|#)\.?\s*(?P<unit>[\w-]+))?\s*[,\n]\s*"
                      r"(?P<city>[A-Za-z .'-]+?)\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?\s*$", re.I)


def _addr(text: Optional[str]) -> Dict[str, str]:
    m = _US_ADDR.match((text or "").strip())
    return {k: v.strip() for k, v in m.groupdict().items() if v} if m else {}


def gather(case: Dict, intake: Dict, form_data: Dict) -> Dict[str, Dict]:
    """key → {value, source: entered|intake|case|derived}. Only keys with a value are returned."""
    out: Dict[str, Dict] = {}

    def put(key, value, source):
        if value not in (None, "") and key not in out:
            out[key] = {"value": str(value).strip(), "source": source}

    for k, v in (form_data or {}).items():
        if k in TEXT_FIELDS or k in ("sex", "current_status"):
            put(k, v, "entered")
    a = intake or {}
    name = (a.get("full_name") or "").split()
    if len(name) >= 2:
        put("bene_given", name[0], "derived")
        put("bene_family", name[-1], "derived")
        if len(name) > 2:
            put("bene_middle", " ".join(name[1:-1]), "derived")
    for key, src in (("bene_dob", "date_of_birth"), ("bene_passport_expiry", "passport_expiry"),
                     ("bene_status_expiry", "status_expiry"), ("bene_last_arrival", "last_entry_date"),
                     ("employment_from", "employment_start")):
        put(key, _mdy(a.get(src)), "intake")
    for key, src in (("bene_country_birth", "country_of_birth"), ("bene_citizenship", "citizenship"),
                     ("bene_passport", "passport_number"), ("bene_a_number", "a_number"), ("bene_i94", "i94_number"),
                     ("job_title", "job_title"), ("petitioner_company", "employer")):
        put(key, a.get(src), "intake")
    if a.get("citizenship"):
        put("bene_passport_country", a["citizenship"], "derived")
    if a.get("annual_salary"):
        put("wages", re.sub(r"[^\d.]", "", str(a["annual_salary"])), "intake")
        put("wages_per", "year", "derived")
    put("current_status", a.get("current_status"), "intake")
    for prefix, src in (("bene", "us_address"), ("work", "work_address")):
        parts = _addr(a.get(src))
        for f in ("street", "city", "state", "zip"):
            put(f"{prefix}_{f}", parts.get(f), "derived")
    # case
    put("petitioner_company", case.get("petitioner"), "case")
    put("classification", case.get("visa_class"), "case")
    put("prior_receipt", case.get("receipt_number"), "case")
    put("current_status", case.get("visa_class") if case.get("case_type") in BASIS_BY_CASE else None, "case")
    put("bene_status_expiry", _mdy(case.get("expiry_date")), "case")
    if case.get("beneficiary") and "bene_family" not in out:
        parts = case["beneficiary"].split()
        if len(parts) >= 2:
            put("bene_given", parts[0], "derived")
            put("bene_family", parts[-1], "derived")
            if len(parts) > 2:
                put("bene_middle", " ".join(parts[1:-1]), "derived")
    return out


def preview(case: Dict, intake: Dict, form_data: Dict) -> Dict:
    vals = gather(case, intake, form_data)
    rows = [{"key": k, "label": lab, "group": grp, "required": k in REQUIRED, **vals.get(k, {"value": "", "source": None})}
            for k, (_, lab, grp) in TEXT_FIELDS.items()]
    rows += [{"key": "sex", "label": "Beneficiary sex (M or F)", "group": "Part 3. Beneficiary", "required": False,
              **vals.get("sex", {"value": "", "source": None})},
             {"key": "current_status", "label": "Current nonimmigrant status (e.g. H-1B)", "group": "Part 3. Beneficiary",
              "required": True, **vals.get("current_status", {"value": "", "source": None})}]
    basis = BASIS_BY_CASE.get(case.get("case_type"))
    missing = [r["label"] for r in rows if r["required"] and not r["value"]]
    return {"form": "I-129", "edition": EDITION, "edition_ok": edition_ok(), "fields": rows,
            "basis": basis, "requested_action": ACTION_BY_CASE.get(case.get("case_type")),
            "filled": sum(1 for r in rows if r["value"]), "total": len(rows), "missing_required": missing,
            "derived": [r["label"] for r in rows if r["source"] == "derived"]}


def _set_choice(doc, w, code: str) -> bool:
    """Select a drop-down option by its export code (PyMuPDF can't update lists whose code and label differ)."""
    import fitz
    codes = {(c[0] if isinstance(c, (list, tuple)) else c).strip() for c in w.choice_values}
    if code not in codes:
        return False
    doc.xref_set_key(w.xref, "V", fitz.get_pdf_str(code))
    doc.xref_set_key(w.xref, "AP", "null")          # drop the stale appearance; viewers redraw it
    return True


def fill(case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    """The official I-129 with every known value filled; returns (pdf bytes, preview report)."""
    import fitz
    vals = gather(case, intake, form_data)
    report = preview(case, intake, form_data)
    targets = {TEXT_FIELDS[k][0]: (k, v["value"]) for k, v in vals.items() if k in TEXT_FIELDS}
    basis = BASIS_BY_CASE.get(case.get("case_type"))
    action = ACTION_BY_CASE.get(case.get("case_type"))
    status = re.sub(r"[^A-Z0-9]", "", (vals.get("current_status", {}).get("value") or "").upper())
    sex = (vals.get("sex", {}).get("value") or "").upper()[:1]
    skipped = []
    doc = fitz.open(FORM_PDF)
    for page in doc:
        for w in page.widgets():
            name = w.field_name
            if NEVER.search(name):
                continue
            try:
                if name in targets:
                    key, value = targets[name]
                    if w.field_type_string == "ComboBox":
                        value = value.upper()
                        if not _set_choice(doc, w, value):
                            skipped.append(f"{TEXT_FIELDS[key][1]}: “{value}” is not one of the form's options")
                        continue
                    elif w.text_maxlen and len(value) > w.text_maxlen:
                        skipped.append(f"{TEXT_FIELDS[key][1]}: longer than the form allows ({w.text_maxlen})")
                        continue
                    w.field_value = value
                    w.update()
                elif name == STATUS_COMBO and status:
                    if not _set_choice(doc, w, status):
                        skipped.append(f"Current status “{status}” is not one of the form's codes")
                elif basis and name == BASIS[basis]:
                    w.field_value = w.on_state()
                    w.update()
                elif action and name.startswith(ACTION) and w.on_state() == action:
                    w.field_value = w.on_state()
                    w.update()
                elif sex in ("M", "F") and name.startswith(SEX) and w.on_state() == sex:
                    w.field_value = w.on_state()
                    w.update()
            except Exception as e:                      # one bad field must not lose the rest
                skipped.append(f"{name}: {e}")
    # Ask viewers to redraw field appearances (drop-downs are set at the data level)
    cat = doc.pdf_catalog()
    af = doc.xref_get_key(cat, "AcroForm")
    if af[0] == "xref":
        doc.xref_set_key(int(af[1].split()[0]), "NeedAppearances", "true")
    elif af[0] == "dict":
        doc.xref_set_key(cat, "AcroForm/NeedAppearances", "true")
    report["skipped"] = skipped
    return doc.tobytes(garbage=3, deflate=True), report
