"""
Medical EOB (Explanation of Benefits) Analyzer
================================================
Extraction + Intelligence for UniDocVerse medical EOB documents.

Supported payers / formats:
  UnitedHealthcare, Aetna, Cigna, BlueCross BlueShield (all regional plans),
  Humana, Kaiser Permanente, Anthem, Centene, Molina, WellCare,
  Medicare Summary Notice (MSN), Medicaid EOB, self-insured employer plans

Architecture:
  EOBExtractor       — table-first + text fallback extraction
  EOBData            — clean dataclass, no business logic
  EOBIntelligence    — advisory layer (cost analysis, deductible tracking, alerts)
  MedicalEOBAnalyzer — orchestrator called by DocumentAnalysisNode

Intelligence output:
  - Patient responsibility breakdown (deductible, copay, coinsurance, not covered)
  - Deductible status (YTD applied, remaining, met?)
  - Out-of-pocket maximum tracking
  - Billed vs allowed vs paid — savings from insurance negotiation
  - Per-service line itemization
  - Denial / remark code explanations (plain English)
  - Action plan (pay, appeal, HSA eligible, request itemized bill)
  - Tax / HSA-eligible expense tagging

Drop at: app/services/analyzers/health/medical_eob_analyzer.py

Wire in:
  analyzer_registry.py:  "medical_eob": MedicalEOBAnalyzer()
  document_registry.py:  "medical_eob": {"parser": None, "analyzer": MedicalEOBAnalyzer}
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

PAYER_PATTERNS: List[Tuple[str, str]] = [
    ("unitedhealthcare",    "UnitedHealthcare"),
    ("united health",       "UnitedHealthcare"),
    ("uhc",                 "UnitedHealthcare"),
    ("optum",               "UnitedHealthcare"),
    ("aetna",               "Aetna"),
    ("cigna",               "Cigna"),
    ("blue cross",          "BlueCross BlueShield"),
    ("bluecross",           "BlueCross BlueShield"),
    ("bcbs",                "BlueCross BlueShield"),
    ("anthem",              "Anthem"),
    ("humana",              "Humana"),
    ("kaiser",              "Kaiser Permanente"),
    ("centene",             "Centene"),
    ("molina",              "Molina Healthcare"),
    ("wellcare",            "WellCare"),
    ("medicare",            "Medicare"),
    ("medicaid",            "Medicaid"),
    ("tricare",             "TRICARE"),
    ("champva",             "CHAMPVA"),
    ("oscar",               "Oscar Health"),
    ("bright health",       "Bright Health"),
    ("ambetter",            "Ambetter"),
    ("coventry",            "Coventry Health"),
    ("healthnet",           "Health Net"),
    ("health net",          "Health Net"),
    ("highmark",            "Highmark"),
    ("geisinger",           "Geisinger"),
    ("caresource",          "CareSource"),
]

# CPT/procedure code → human-readable service description
CPT_DESCRIPTIONS: Dict[str, str] = {
    # Office visits
    "99201": "Office Visit – New Patient (minimal)",
    "99202": "Office Visit – New Patient (low complexity)",
    "99203": "Office Visit – New Patient (moderate complexity)",
    "99204": "Office Visit – New Patient (moderate-high complexity)",
    "99205": "Office Visit – New Patient (high complexity)",
    "99211": "Office Visit – Established Patient (minimal)",
    "99212": "Office Visit – Established Patient (low complexity)",
    "99213": "Office Visit – Established Patient (moderate complexity)",
    "99214": "Office Visit – Established Patient (moderate-high complexity)",
    "99215": "Office Visit – Established Patient (high complexity)",
    # Preventive care
    "99381": "Preventive Visit – New Patient (infant)",
    "99382": "Preventive Visit – New Patient (1-4 years)",
    "99383": "Preventive Visit – New Patient (5-11 years)",
    "99384": "Preventive Visit – New Patient (12-17 years)",
    "99385": "Preventive Visit – New Patient (18-39 years)",
    "99386": "Preventive Visit – New Patient (40-64 years)",
    "99387": "Preventive Visit – New Patient (65+ years)",
    "99391": "Preventive Visit – Established (infant)",
    "99392": "Preventive Visit – Established (1-4 years)",
    "99393": "Preventive Visit – Established (5-11 years)",
    "99394": "Preventive Visit – Established (12-17 years)",
    "99395": "Preventive Visit – Established (18-39 years)",
    "99396": "Preventive Visit – Established (40-64 years)",
    "99397": "Preventive Visit – Established (65+ years)",
    # Lab
    "80048": "Basic Metabolic Panel",
    "80053": "Comprehensive Metabolic Panel",
    "80061": "Lipid Panel",
    "85025": "Complete Blood Count (CBC)",
    "85027": "Complete Blood Count (CBC)",
    "84443": "Thyroid Stimulating Hormone (TSH)",
    "82947": "Glucose – Blood",
    "83036": "Hemoglobin A1c",
    "86900": "Blood Typing – ABO",
    "87086": "Urine Culture",
    "87210": "Wet Mount – Vaginal",
    "87340": "Hepatitis B Surface Antigen",
    "87880": "Strep Test – Rapid",
    # Radiology
    "71046": "Chest X-Ray (2 views)",
    "71045": "Chest X-Ray (1 view)",
    "72148": "MRI – Lumbar Spine",
    "72141": "MRI – Cervical Spine",
    "73721": "MRI – Lower Extremity Joint",
    "73223": "MRI – Upper Extremity Joint",
    "74177": "CT Scan – Abdomen/Pelvis",
    "74176": "CT Scan – Abdomen",
    "70553": "MRI – Brain with/without contrast",
    "76700": "Ultrasound – Abdomen",
    "76805": "Ultrasound – Obstetric",
    "77067": "Mammography – Bilateral (screening)",
    "77066": "Mammography – Bilateral (diagnostic)",
    # Surgery / procedures
    "43239": "Upper GI Endoscopy (EGD)",
    "45378": "Colonoscopy – Diagnostic",
    "45380": "Colonoscopy – with Biopsy",
    "45385": "Colonoscopy – Polypectomy",
    "27447": "Total Knee Arthroplasty",
    "27130": "Total Hip Arthroplasty",
    "29881": "Knee Arthroscopy – Meniscectomy",
    "19301": "Mastectomy – Partial",
    "47562": "Laparoscopic Cholecystectomy",
    "49505": "Inguinal Hernia Repair",
    # ER
    "99281": "Emergency Department Visit (minimal)",
    "99282": "Emergency Department Visit (low complexity)",
    "99283": "Emergency Department Visit (moderate complexity)",
    "99284": "Emergency Department Visit (moderate-high complexity)",
    "99285": "Emergency Department Visit (high complexity)",
    # Mental health
    "90791": "Psychiatric Diagnostic Evaluation",
    "90832": "Psychotherapy – 30 min",
    "90834": "Psychotherapy – 45 min",
    "90837": "Psychotherapy – 60 min",
    "90847": "Family Psychotherapy",
    "90853": "Group Psychotherapy",
    # Physical therapy
    "97010": "Hot/Cold Pack Therapy",
    "97012": "Traction",
    "97014": "Electrical Stimulation",
    "97035": "Ultrasound Therapy",
    "97110": "Therapeutic Exercises",
    "97140": "Manual Therapy",
    "97530": "Therapeutic Activities",
    "97150": "Therapeutic Procedure – Group",
    # Anesthesia
    "00100": "Anesthesia",
    "99100": "Anesthesia – Qualifying Circumstance (extreme age)",
    # Vaccines
    "90686": "Influenza Vaccine",
    "90700": "DTaP Vaccine",
    "90714": "Tetanus Toxoid Vaccine",
    "90734": "Meningococcal Vaccine",
    "90746": "Hepatitis B Vaccine",
    "90707": "MMR Vaccine",
    "90716": "Varicella Vaccine",
    "91300": "COVID-19 Vaccine",
}

# Remark / adjustment reason codes → plain English
REMARK_CODES: Dict[str, str] = {
    # CARC (Claim Adjustment Reason Codes)
    "1":   "Deductible amount",
    "2":   "Coinsurance amount",
    "3":   "Co-payment amount",
    "4":   "The service is not covered by this plan",
    "5":   "The procedure code / bill type is inconsistent with the place of service",
    "6":   "The procedure/revenue code is inconsistent with the patient's age",
    "7":   "The procedure/revenue code is inconsistent with the patient's gender",
    "8":   "The procedure code is inconsistent with the provider type",
    "9":   "The diagnosis is inconsistent with the patient's age",
    "10":  "The diagnosis is inconsistent with the patient's gender",
    "11":  "The diagnosis is inconsistent with the procedure",
    "12":  "The diagnosis is inconsistent with the provider type",
    "13":  "The date of death precedes the date of service",
    "14":  "The date of birth follows the date of service",
    "15":  "The authorization number is missing, invalid, or does not apply",
    "16":  "Claim/service lacks information or has submission/billing error(s)",
    "18":  "Duplicate claim/service",
    "19":  "Claim denied — this is a work-related injury",
    "20":  "Claim denied — this is for a condition covered by another payer",
    "21":  "Claim denied — this injury/illness is the liability of a third party",
    "22":  "This care may be covered by another payer per coordination of benefits",
    "23":  "The impact of prior payer(s) adjudication including payments and/or adjustments",
    "24":  "Charges are covered under a capitation agreement/negotiated fee schedule",
    "26":  "Expenses incurred prior to coverage",
    "27":  "Expenses incurred after coverage terminated",
    "29":  "The time limit for filing has expired",
    "31":  "Claim denied as patient cannot be identified as our insured",
    "32":  "Our records indicate the patient is not an eligible dependent",
    "33":  "Insured has no dependent coverage",
    "34":  "Insured has no coverage for newborns",
    "35":  "Lifetime benefit maximum has been reached",
    "36":  "Balance does not exceed co-payment amount",
    "37":  "Balance does not exceed deductible",
    "38":  "Services not provided or authorized by designated (network/primary care) providers",
    "39":  "Services denied at the time authorization/pre-certification was requested",
    "40":  "Charges do not meet qualifications for emergent/urgent care",
    "44":  "Prompt-pay discount",
    "45":  "Charge exceeds fee schedule/maximum allowable or contracted/legislated fee arrangement",
    "49":  "These are non-covered services because this is a routine exam or routine/preventive dental",
    "50":  "These are non-covered services because this is not deemed a 'medical necessity'",
    "51":  "These are non-covered services because this is a pre-existing condition",
    "52":  "The referring/prescribing/rendering provider is not eligible to refer/prescribe/order/perform the service billed",
    "54":  "Multiple physicians/assistants are not covered in this case",
    "55":  "Claim/service denied — procedure is inconsistent with the order of the service",
    "56":  "Claim denied — procedure code billed is not correct/valid for the service rendered",
    "57":  "This procedure requires prior authorization",
    "58":  "Treatment was deemed by the payer to have been rendered in an inappropriate or invalid place of service",
    "59":  "Processed based on multiple surgery rules or concurrent surgery rules",
    "60":  "Charges for outpatient services with this proximity to inpatient services are not covered",
    "61":  "Penalty for failure to obtain second surgical opinion",
    "66":  "Blood deductible",
    "69":  "Day outlier amount",
    "70":  "Cost outlier — adjustment to compensate for additional costs",
    "74":  "Indirect Medical Education Adjustment",
    "75":  "Direct Medical Education Adjustment",
    "76":  "Disproportionate Share Adjustment",
    "78":  "Non-Covered days/Room and Board excess over semiprivate rate",
    "85":  "Patient Interest Adjustment (for Prompt Pay)",
    "89":  "Professional fees removed from charges",
    "90":  "Ingredient cost adjustment",
    "91":  "Dispensing fee adjustment",
    "94":  "Processed in Excess of charges",
    "95":  "Plan procedures not followed",
    "96":  "Non-covered charge(s). At least one remark code must be provided",
    "97":  "The benefit for this service is included in the payment/allowance for another service",
    "100": "Payment made to patient/insured/responsible party/employer",
    "101": "Predetermination: anticipated payment upon completion of services",
    "102": "Major Medical Adjustment",
    "103": "Provider promotional discount (e.g., Senior citizen, Hardship)",
    "104": "Managed Care Withholding",
    "105": "Tax withholding",
    "106": "Patient payment option/election not in effect",
    "107": "The related or qualifying claim/service was not identified on this claim",
    "108": "Rent/purchase guidelines were not met",
    "109": "Claim/service not covered by this payer/contractor",
    "110": "Billing date predates service date",
    "111": "Not covered unless the provider accepts assignment",
    "112": "Service not furnished directly to the patient and/or not documented",
    "114": "Procedure/product not approved by the Food and Drug Administration",
    "115": "Claim/service not covered/reduced as patient invoiced more than amount allowed",
    "116": "The advance indemnification notice signed by the patient did not comply with requirements",
    "117": "Transportation is only covered to the closest facility that can provide the necessary care",
    "118": "ESRD network support adjustment",
    "119": "Benefit maximum for this time period or occurrence has been reached",
    "121": "Indemnification adjustment — patient is employed by the insurer",
    "122": "Psychiatric reduction",
    "125": "Submission/billing error(s). At least one remark code must be provided",
    "128": "Newborn's services are covered in the mother's Allowance",
    "129": "Prior processing information appears incorrect",
    "130": "Claim submission fee",
    "131": "Claim specific negotiated discount",
    "132": "Prearranged demonstration project adjustment",
    "133": "The disposition of this service line is pending further review",
    "134": "Technical fees removed from charges",
    "135": "Interim bills cannot be processed",
    "136": "Failure to follow prior authorization/referral procedures for service",
    "137": "Regulatory Surcharges, Assessments, Allowances or Health Related Taxes",
    "138": "Appeal procedures not followed or time limits not met",
    "139": "Contracted funding agreement — Subscriber is employed by the Provider of the services",
    "140": "Patient/Insured health identification number and name do not match",
    "141": "Claim adjustment because the claim spans eligible and ineligible periods of coverage",
    "142": "Claim adjusted by the monthly Medicaid patient liability amount",
    "143": "Portion of payment deferred",
    "144": "Incentive adjustment, e.g. preferred product/service",
    "146": "Diagnosis was invalid for the date(s) of service reported",
    "147": "Provider contracted/negotiated rate expired or not on file",
    "148": "Information from another provider was not available or was not required",
    "149": "Lifetime benefit maximum has been reached for this service/benefit category",
    "150": "Payment adjusted because the payer deems the information submitted does not support this level of service",
    "151": "Payment adjusted because the payer deems the information submitted does not support this many/frequency of services",
    "152": "Payment adjusted because the payer deems the information submitted does not support this length of service",
    "153": "Payment adjusted because the payer deems the information submitted does not support this dosage",
    "154": "Payment adjusted because the payer deems the information submitted does not support this day's supply",
    "155": "Not covered as the patient is not residing in a network/primary care service area",
    "157": "Service/procedure was provided as a result of an act of war",
    "158": "Service/procedure was provided outside of the United States",
    "159": "Service/procedure was provided as a result of terrorism",
    "160": "Injury/illness was the result of an activity that is a benefit exclusion",
    "161": "Provider performance bonus",
    "163": "Attachment referenced on the claim was not received",
    "164": "Attachment referenced on the claim was received but not valid",
    "165": "Revenue code and Procedure code do not match",
    "166": "These services were submitted after this plan's responsibility for Medicare crossover services ended",
    "167": "This (these) diagnosis(es) is (are) not covered. Note: Refer to the 835 Healthcare Policy Identification Segment",
    "168": "Service(s) have been considered under the patient's medical or dental plan",
    "169": "Alternate benefit has been provided",
    "170": "Payment is denied when performed/billed by this type of provider",
    "171": "Payment is denied when performed/billed by this type of provider in this type of facility",
    "172": "Payment is adjusted when performed/billed by a provider of this specialty",
    "173": "Service/equipment was not prescribed by a physician",
    "174": "Service was not prescribed prior to delivery",
    "175": "Prescription is incomplete",
    "176": "Prescription is not current",
    "177": "Patient has not met the required eligibility requirements",
    "178": "Patient has not met the required spend down requirements",
    "179": "Patient has not met the required waiting requirements",
    "180": "Patient has not met the required residency requirements",
    "181": "Procedure code was invalid on the date of service",
    "182": "Procedure modifier was invalid on the date of service",
    "183": "The referring provider is not eligible to refer the service billed",
    "184": "The prescribing/ordering provider is not eligible to prescribe/order the service billed",
    "185": "The rendering provider is not eligible to perform the service billed",
    "186": "Level of care change adjustment",
    "187": "Consumer Spending Account payments (includes but is not limited to Flexible Spending Account, Health Savings Account, Health Reimbursement Account, etc.)",
    "188": "This product/service is only covered when used according to FDA recommendations",
    "189": "Not covered when deemed experimental/investigational by the payer",
    "190": "Payment is included in the allowance for a Skilled Nursing Facility (SNF)",
    "191": "Not a work related injury/illness and thus not the liability of the workers' compensation carrier Note: If adjustment is at the Claim Level",
    "192": "Non standard adjustment code from paper remittance. Note: This code is to be used by providers/payers providing Coordination of Benefits information to another payer",
    "193": "Original payment decision is being maintained. Upon review, it was determined that this claim was processed correctly",
    "194": "Anesthesia performed by the operating physician",
    "195": "Refund issued to an erroneous priority payer for this claim/service",
    "196": "Claim/service denied based on prior authorization",
    "197": "Precertification/authorization/notification/pre-treatment absent",
    "198": "Precertification/authorization exceeded",
    "199": "Revenue code and revenue description do not match",
    "200": "Expenses incurred during lapse in coverage",
    "201": "Workers Compensation case settled. Patient is responsible for amount of this claim/service through WC 'Medicare set aside arrangement' or other agreement",
    "202": "Non-covered personal comfort or convenience services",
    "203": "Discontinued or reduced service",
    "204": "This service/equipment/drug is not covered under the patient's current benefit plan",
    "205": "Pharmacy discount card processing fee",
    "206": "National Drug Codes (NDC) not eligible for rebate, are unapproved drugs",
    "207": "Claim/service(s) are related to a settlement, judgment, award, or other payment for the same or similar condition",
    "208": "Claim is not covered by the plan. This is a pre-existing condition",
    "209": "Per regulatory or other agreement. The provider cannot collect this amount from the patient",
    "210": "Payment adjusted because pre-certification/authorization not obtained prior to service",
    "211": "National Drug Code (NDC) not eligible for rebate",
    "212": "Administrative surcharge",
    "213": "Non-covered — roommate services",
    "214": "Workers' Compensation claim adjudicated as non-compensable",
    "215": "Based on subrogation of a third party settlement",
    "216": "Based on the findings of a review organization",
    "217": "Based on payer reasonable and customary fees. No maximum allowable defined by legislation",
    "218": "The rendering provider is not in network. (HMO Network or Point of Service)",
    "219": "Quality of care issues",
    "220": "To obtain a copy of the remittance, contact payer",
    "221": "Missing or incorrect claim information",
    "222": "Exceeds the contracted maximum number of hours/days/units by this provider for this period",
    "223": "Adjustment code for mandated federal, state or local law/regulation that is not already covered by another code and is mandated before a new code can be created",
    "224": "Patient identification compromised by identity theft. Identity verification required for processing this and future claims",
    "225": "Penalty applied for failure to obtain provider clinical review prior to service",
    "226": "Information requested from the Billing/Rendering Provider was not provided or was insufficient/incomplete",
    "227": "Information requested from the patient/insured/responsible party was not provided or was insufficient/incomplete",
    "228": "Denied for failure of this provider, another provider or the subscriber to supply requested information to a previous payer for their adjudication of this claim",
    "229": "Partial charge amount not considered by Medicare due to the initial claim not being sent to the correct Medicare contractor",
    "231": "Mutually exclusive procedures cannot be done in the same day/setting",
    "232": "Institutional Transfer Amount",
    "233": "Services/charges related to the treatment of a hospital-acquired condition or preventable medical error",
    "234": "This procedure is not paid separately",
    "235": "Sales Tax",
    "236": "This procedure or procedure/modifier combination is not compatible with another procedure or procedure/modifier combination provided on the same day according to the National Correct Coding Initiative",
    "237": "Legislated/Regulatory Penalty",
    "238": "Claim spans eligible and ineligible periods of coverage, this is the reduction for the ineligible period",
    "239": "Claim adjusted because this procedure code was inappropriate for the setting in which the service was provided",
    "240": "The changes to this claim were a result of a re-pricing arrangement",
    "241": "Low Income Subsidy (LIS) Co-payment Amount",
    "242": "Services not provided by network/primary care providers",
    "243": "Services not authorized by network/primary care providers",
    "245": "Provider performance program withhold",
    "246": "This non-payable code is for required reporting only",
    "247": "Deductible for Professional service rendered in an Institutional setting and billed on an Institutional claim",
    "248": "Coinsurance for Professional service rendered in an Institutional setting and billed on an Institutional claim",
    "249": "This claim has been identified as a readmission",
    "250": "The attachment/other documentation that was received was the incorrect attachment/document",
    "251": "The attached/other documentation that was received was incomplete or illegible",
    "252": "An attachment/other documentation is required to adjudicate this claim/service",
    "253": "Sequestration — reduction in federal payment",
    "254": "Claim received by the medical plan, but benefits not available under this plan. Submit these services to the patient's Pharmacy plan for further consideration",
    "255": "The disposition of the related Property and Casualty claim (injury or illness) is pending due to litigation",
    "256": "Service not payable per managed care contract",
    "257": "The disposition of the related Property and Casualty claim (injury or illness) is pending",
    "258": "Action taken related to an adjustment code from the MSP Explanation of Benefits",
    "259": "Additional payment for Dental/Vision services rendered by a Medical Provider",
    "260": "Processed under Medicaid ACA Enhanced Fee Schedule",
    "261": "The procedure or service is inconsistent with the patient's history",
    "262": "Adjustment for delivery cost. To be used for pharmaceuticals only",
    "263": "Adjustment for shipping cost. To be used for pharmaceuticals only",
    "264": "Adjustment for postage cost. To be used for pharmaceuticals only",
    "265": "Claim received by the Dental plan, but benefits not available under this plan. Submit these services to the patient's Medical plan for further consideration",
    "266": "Adjustment for administrative cost",
    "267": "Claim was forwarded to another entity",
    "268": "Not covered. Additional information is included in the 835 loop 2300, NM1 segment, qualifier 77",
    "269": "Anesthesia units do not correspond to the billed time",
    "270": "Claim received by the Vision plan, but benefits not available under this plan. Submit these services to the patient's Medical plan for further consideration",
    "271": "Prior authorization/referral not required",
    "272": "Coverage/program guidelines were not met",
    "273": "Coverage/program guidelines were exceeded",
    "274": "Fee/Service not payable per agreement",
    "275": "Prior payer's (payers') patient responsibility (deductible, coinsurance, copayment) not covered",
    "276": "Services denied by the prior payer(s) are not covered by this payer",
    "277": "The disposition of the claim/service is undetermined during the premium payment grace period, per Health Insurance Exchange requirements",
    "278": "Performance program proficiency requirements not met",
    "279": "Services not provided by Preferred network providers. Note: This code is only used in the 837 transaction to convey coordination of benefits information when the secondary payer's cost avoidance policy applies",
    "280": "Total payments by the primary payer equal the patient's responsibility, not sending payment to secondary payer",
    "281": "Deductible waived per contractual agreement",
    "282": "Payment denied based on the Liability Coverage Medicare Set Aside Arrangement or other agreement",
    "283": "Referral not required",
    "284": "Third party resources exhausted",
    "285": "Claim spans an ineligibility period",
    "286": "Appeal time limits not met",
    "287": "Claim submitted in error; claim not for Medicare Advantage plan",
    "288": "Referral absent",
    "289": "Services not payable — both Part A and Part B cannot be billed at the same time for the same patient",
    "290": "Services are covered under a composite rate and cannot be billed separately",
    "291": "The claim may include payment for a condition or injury resulting from an automobile accident",
    "292": "The claim may include payment for a condition or injury resulting from a work related accident/illness",
    "293": "Payment denied because the patient refuses to authorize payment",
    "294": "Claim/service involves a never event",
    "295": "Claim/service involves an adverse event",
    "296": "ESRD claim spans an ineligibility period for the patient",
    "297": "Claim received by the dental plan, but benefits not available under this plan. Submit these services to the patient's Pharmacy plan for further consideration",
    "298": "Prior payer's (payers') adjudication amounts not used in determining this plan's liability",
    "299": "The situation or event causing the health issue or injury was not accidental. Liability coverage has been applied",
    "300": "Claim received by the Medical plan, but benefits not available under this plan. Submit these services to the patient's Vision plan for further consideration",
}

# Place of service codes
POS_CODES: Dict[str, str] = {
    "01": "Pharmacy",
    "02": "Telehealth",
    "03": "School",
    "04": "Homeless Shelter",
    "05": "Indian Health Service — Free-standing Facility",
    "06": "Indian Health Service — Provider-based Facility",
    "07": "Tribal 638 — Free-standing Facility",
    "08": "Tribal 638 — Provider-based Facility",
    "09": "Prison/Correctional Facility",
    "10": "Telehealth — patient at home",
    "11": "Office",
    "12": "Home",
    "13": "Assisted Living Facility",
    "14": "Group Home",
    "15": "Mobile Unit",
    "16": "Temporary Lodging",
    "17": "Walk-in Retail Health Clinic",
    "18": "Place of Employment — Worksite",
    "19": "Off-Campus Outpatient Hospital",
    "20": "Urgent Care Facility",
    "21": "Inpatient Hospital",
    "22": "On-Campus Outpatient Hospital",
    "23": "Emergency Room — Hospital",
    "24": "Ambulatory Surgical Center",
    "25": "Birthing Center",
    "26": "Military Treatment Facility",
    "31": "Skilled Nursing Facility",
    "32": "Nursing Facility",
    "33": "Custodial Care Facility",
    "34": "Hospice",
    "41": "Ambulance — Land",
    "42": "Ambulance — Air or Water",
    "49": "Independent Clinic",
    "50": "Federally Qualified Health Center",
    "51": "Inpatient Psychiatric Facility",
    "52": "Psychiatric Facility — Partial Hospitalization",
    "53": "Community Mental Health Center",
    "54": "Intermediate Care Facility",
    "55": "Residential Substance Abuse Treatment Facility",
    "56": "Psychiatric Residential Treatment Center",
    "57": "Non-Residential Substance Abuse Treatment Facility",
    "58": "Non-Residential Opioid Treatment Facility",
    "60": "Mass Immunization Center",
    "61": "Comprehensive Inpatient Rehabilitation Facility",
    "62": "Comprehensive Outpatient Rehabilitation Facility",
    "65": "End-Stage Renal Disease Treatment Facility",
    "71": "State or Local Public Health Clinic",
    "72": "Rural Health Clinic",
    "81": "Independent Laboratory",
    "99": "Other Place of Service",
}

SKIP_LABELS = {
    "service", "description", "date", "procedure", "code", "cpt",
    "billed", "charge", "allowed", "insurance", "paid", "plan paid",
    "not covered", "deductible", "copay", "coinsurance", "patient",
    "responsibility", "total", "subtotal", "continued", "page",
    "amount", "your responsibility", "see remark", "remark", "balance",
    "adjustment", "reason", "modifier", "qty", "units", "npi",
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ServiceLine:
    date_of_service:     Optional[str]   = None
    description:         str             = ""
    cpt_code:            Optional[str]   = None
    pos_code:            Optional[str]   = None
    pos_description:     Optional[str]   = None
    units:               Optional[int]   = None
    billed_amount:       Optional[float] = None
    allowed_amount:      Optional[float] = None
    not_covered:         Optional[float] = None
    deductible_applied:  Optional[float] = None
    copay:               Optional[float] = None
    coinsurance:         Optional[float] = None
    plan_paid:           Optional[float] = None
    patient_responsibility: Optional[float] = None
    remark_codes:        List[str]        = field(default_factory=list)
    remark_descriptions: List[str]        = field(default_factory=list)
    is_denied:           bool             = False
    denial_reason:       Optional[str]    = None
    is_hsa_eligible:     bool             = True  # default eligible; set False if cosmetic/non-covered


@dataclass
class EOBData:
    document_type:          str   = "medical_eob"
    extraction_confidence:  float = 0.0
    missing_fields:         List[str] = field(default_factory=list)
    extraction_timestamp:   str  = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Payer / plan
    payer_name:             Optional[str]  = None
    plan_name:              Optional[str]  = None
    group_number:           Optional[str]  = None
    member_id:              Optional[str]  = None

    # Patient / insured
    patient_name:           Optional[str]  = None
    insured_name:           Optional[str]  = None
    date_of_birth:          Optional[str]  = None

    # Provider
    provider_name:          Optional[str]  = None
    provider_npi:           Optional[str]  = None
    claim_number:           Optional[str]  = None

    # Dates
    date_of_service:        Optional[str]  = None
    date_received:          Optional[str]  = None
    date_processed:         Optional[str]  = None
    eob_date:               Optional[str]  = None

    # Claim-level totals
    total_billed:           Optional[float] = None
    total_allowed:          Optional[float] = None
    total_not_covered:      Optional[float] = None
    total_deductible:       Optional[float] = None
    total_copay:            Optional[float] = None
    total_coinsurance:      Optional[float] = None
    total_plan_paid:        Optional[float] = None
    total_patient_responsibility: Optional[float] = None

    # Deductible / OOP tracking (YTD)
    deductible_limit:       Optional[float] = None
    deductible_met:         Optional[float] = None
    deductible_remaining:   Optional[float] = None
    oop_limit:              Optional[float] = None
    oop_met:                Optional[float] = None
    oop_remaining:          Optional[float] = None

    # Service lines
    service_lines:          List[ServiceLine] = field(default_factory=list)

    # Flags
    has_denials:            bool = False
    is_hsa_eligible:        bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_type":          self.document_type,
            "extraction_confidence":  self.extraction_confidence,
            "missing_fields":         self.missing_fields,
            "extraction_timestamp":   self.extraction_timestamp,
            "payer_name":             self.payer_name,
            "plan_name":              self.plan_name,
            "group_number":           self.group_number,
            "member_id":              self.member_id,
            "patient_name":           self.patient_name,
            "insured_name":           self.insured_name,
            "date_of_birth":          self.date_of_birth,
            "provider_name":          self.provider_name,
            "provider_npi":           self.provider_npi,
            "claim_number":           self.claim_number,
            "date_of_service":        self.date_of_service,
            "date_received":          self.date_received,
            "date_processed":         self.date_processed,
            "eob_date":               self.eob_date,
            "total_billed":           self.total_billed,
            "total_allowed":          self.total_allowed,
            "total_not_covered":      self.total_not_covered,
            "total_deductible":       self.total_deductible,
            "total_copay":            self.total_copay,
            "total_coinsurance":      self.total_coinsurance,
            "total_plan_paid":        self.total_plan_paid,
            "total_patient_responsibility": self.total_patient_responsibility,
            "deductible_limit":       self.deductible_limit,
            "deductible_met":         self.deductible_met,
            "deductible_remaining":   self.deductible_remaining,
            "oop_limit":              self.oop_limit,
            "oop_met":                self.oop_met,
            "oop_remaining":          self.oop_remaining,
            "has_denials":            self.has_denials,
            "is_hsa_eligible":        self.is_hsa_eligible,
            "service_lines": [
                {
                    "date_of_service":      sl.date_of_service,
                    "description":          sl.description,
                    "cpt_code":             sl.cpt_code,
                    "pos_code":             sl.pos_code,
                    "pos_description":      sl.pos_description,
                    "units":                sl.units,
                    "billed_amount":        sl.billed_amount,
                    "allowed_amount":       sl.allowed_amount,
                    "not_covered":          sl.not_covered,
                    "deductible_applied":   sl.deductible_applied,
                    "copay":                sl.copay,
                    "coinsurance":          sl.coinsurance,
                    "plan_paid":            sl.plan_paid,
                    "patient_responsibility": sl.patient_responsibility,
                    "remark_codes":         sl.remark_codes,
                    "remark_descriptions":  sl.remark_descriptions,
                    "is_denied":            sl.is_denied,
                    "denial_reason":        sl.denial_reason,
                    "is_hsa_eligible":      sl.is_hsa_eligible,
                }
                for sl in self.service_lines
            ],
        }


# ─── Extractor ────────────────────────────────────────────────────────────────

class EOBExtractor:
    """
    Table-first extractor for EOB documents.

    Primary path:   Docling table rows → label + amounts
    Secondary path: Raw text regex patterns
    Service line path: Multi-column rows with date + CPT + amounts
    """

    def _parse(self, raw: str) -> Optional[float]:
        if not raw:
            return None
        s = str(raw).strip().rstrip("*").strip()
        negative = s.startswith('(') and s.endswith(')')
        if negative:
            s = s[1:-1].strip()
        s = s.lstrip('$').strip()
        if s.startswith('-'):
            negative = True
            s = s[1:].strip()
        if not s:
            return None
        try:
            return -float(s.replace(',', '')) if negative else float(s.replace(',', ''))
        except ValueError:
            return None

    def _parse_date(self, ds: str) -> Optional[date]:
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%b %d %Y"):
            try:
                return datetime.strptime(ds.strip(), fmt).date()
            except ValueError:
                continue
        return None

    # ── Table: summary section ────────────────────────────────────────────────

    def _scan_summary_tables(self, tables: List, data: EOBData) -> None:
        import re as _re
        AMOUNT_LIKE = _re.compile(r'^-?\$?[\d,]+\.\d{2}$')
        # Minimal skip set for summary tables — only true header/structural words
        SUMMARY_SKIP = {"service", "description", "date", "code", "cpt", "total",
                        "continued", "page", "subtotal", "modifier", "qty", "units",
                        "npi", "amount", "reason"}

        for row in tables:
            if not row or len(row) < 2:
                continue

            label = ""
            rest  = []
            for i, cell in enumerate(row):
                s = str(cell).strip() if cell else ""
                # Skip cells that look like amounts or are structural headers
                if s and s.lower().strip() not in SUMMARY_SKIP and len(s) > 1 and not AMOUNT_LIKE.match(s.replace(',', '')):
                    label = s
                    rest  = [str(c).strip() if c else "" for c in row[i+1:]]
                    break

            if not label:
                continue

            ll = label.lower().strip()
            amounts = [self._parse(c) for c in rest if self._parse(c) is not None]
            if not amounts:
                continue
            v = amounts[0]

            if any(k in ll for k in ("amount billed", "total billed", "billed amount", "charges")):
                if not data.total_billed:
                    data.total_billed = abs(v)
            elif any(k in ll for k in ("allowed amount", "eligible amount", "approved amount", "negotiated")):
                if not data.total_allowed:
                    data.total_allowed = abs(v)
            elif any(k in ll for k in ("not covered", "non-covered", "noncovered", "excluded")):
                if not data.total_not_covered:
                    data.total_not_covered = abs(v)
            elif any(k in ll for k in ("deductible limit", "annual deductible", "individual deductible", "family deductible")):
                if not data.deductible_limit:
                    data.deductible_limit = abs(v)
            elif any(k in ll for k in ("deductible met", "deductible applied", "deductible used", "applied to deductible", "deductible met ytd", "deductible ytd")):
                if not data.deductible_met:
                    data.deductible_met = abs(v)
            elif "deductible remaining" in ll or "remaining deductible" in ll:
                if not data.deductible_remaining:
                    data.deductible_remaining = abs(v)
            elif "deductible" in ll and "remaining" not in ll and "limit" not in ll and "met" not in ll and "annual" not in ll and "individual" not in ll:
                if not data.total_deductible:
                    data.total_deductible = abs(v)
            elif any(k in ll for k in ("copay", "co-pay", "co pay")):
                if not data.total_copay:
                    data.total_copay = abs(v)
            elif any(k in ll for k in ("coinsurance", "co-insurance")):
                if not data.total_coinsurance:
                    data.total_coinsurance = abs(v)
            elif any(k in ll for k in ("plan paid", "insurance paid", "benefit paid", "amount paid by plan", "paid by plan", "plan payment", "insurance payment")) or ll == "plan paid":
                if not data.total_plan_paid:
                    data.total_plan_paid = abs(v)
            elif any(k in ll for k in ("patient responsibility", "your responsibility", "amount you owe", "patient owes", "your share")):
                if not data.total_patient_responsibility:
                    data.total_patient_responsibility = abs(v)
            # OOP tracker
            elif any(k in ll for k in ("out-of-pocket limit", "out of pocket limit", "oop limit", "maximum out-of-pocket", "moop")):
                if not data.oop_limit:
                    data.oop_limit = abs(v)
            elif any(k in ll for k in ("out-of-pocket met", "oop met", "out of pocket met", "out-of-pocket applied")):
                if not data.oop_met:
                    data.oop_met = abs(v)
            elif any(k in ll for k in ("out-of-pocket remaining", "oop remaining", "remaining out-of-pocket")):
                if not data.oop_remaining:
                    data.oop_remaining = abs(v)

    # ── Table: service lines ──────────────────────────────────────────────────

    def _scan_service_lines(self, tables: List, data: EOBData) -> None:
        DATE_RE  = re.compile(r'^\d{1,2}/\d{1,2}(?:/\d{2,4})?$|^\d{4}-\d{2}-\d{2}$', re.I)
        CPT_RE   = re.compile(r'^\d{5}$|^[A-Z]\d{4}$')   # CPT or HCPCS
        AMNT_RE  = re.compile(r'^\$?-?\d{1,6}(?:,\d{3})*(?:\.\d{2})?$')
        REMARK_RE = re.compile(r'\b([A-Z]{1,2}\d{1,3}|[A-Z]{2,3}|CO-?\d+|PR-?\d+|OA-?\d+)\b')

        service_lines = []

        for row in tables:
            if not row or len(row) < 4:
                continue
            cells = [str(c).strip() if c else "" for c in row]

            # Need at least one date and one amount
            date_idxs   = [i for i, c in enumerate(cells) if DATE_RE.match(c)]
            # Exclude cells that are pure integers without decimals (CPT codes, POS codes, units)
            amount_idxs = [
                i for i, c in enumerate(cells)
                if AMNT_RE.match(c.replace(',','').replace('$','').strip())
                and '.' in c  # require decimal point for dollar amounts
            ]
            cpt_idxs    = [i for i, c in enumerate(cells) if CPT_RE.match(c)]

            if not date_idxs or not amount_idxs:
                continue

            # Skip header rows
            if cells[0].lower().strip() in SKIP_LABELS:
                continue

            dos = cells[date_idxs[0]]

            # Description: longest non-date, non-amount, non-CPT cell
            desc_candidates = [
                cells[i] for i in range(len(cells))
                if i not in date_idxs
                and i not in amount_idxs
                and i not in cpt_idxs
                and len(cells[i]) > 3
                and not cells[i].isdigit()
                and cells[i].lower() not in SKIP_LABELS
            ]
            description = max(desc_candidates, key=len) if desc_candidates else ""

            # CPT code
            cpt = cells[cpt_idxs[0]] if cpt_idxs else None
            if cpt and cpt in CPT_DESCRIPTIONS:
                description = description or CPT_DESCRIPTIONS[cpt]

            # Amounts — heuristic column order: billed, allowed, not_covered, deductible, copay, coinsurance, plan_paid, patient_resp
            # Exclude CPT/HCPCS cells from amounts (5-digit codes parse as valid floats)
            amounts = [self._parse(cells[i]) for i in amount_idxs
                       if i not in cpt_idxs and self._parse(cells[i]) is not None]

            sl = ServiceLine(
                date_of_service=dos,
                description=description or (CPT_DESCRIPTIONS.get(cpt, "") if cpt else ""),
                cpt_code=cpt,
            )

            # Assign amounts by position
            if len(amounts) >= 1: sl.billed_amount       = abs(amounts[0])
            if len(amounts) >= 2: sl.allowed_amount      = abs(amounts[1])
            if len(amounts) >= 3: sl.not_covered         = abs(amounts[2])
            if len(amounts) >= 4: sl.deductible_applied  = abs(amounts[3])
            if len(amounts) >= 5: sl.copay               = abs(amounts[4])
            if len(amounts) >= 6: sl.coinsurance         = abs(amounts[5])
            if len(amounts) >= 7: sl.plan_paid           = abs(amounts[6])
            if len(amounts) >= 8: sl.patient_responsibility = abs(amounts[7])

            # Compute patient responsibility if missing
            if sl.patient_responsibility is None:
                pr = sum(filter(None, [sl.deductible_applied, sl.copay, sl.coinsurance]))
                if pr > 0:
                    sl.patient_responsibility = round(pr, 2)

            # POS code
            for c in cells:
                if c in POS_CODES:
                    sl.pos_code = c
                    sl.pos_description = POS_CODES[c]
                    break

            # Remark codes
            for c in cells:
                for m in REMARK_RE.finditer(c):
                    code = m.group(1)
                    if code not in sl.remark_codes and len(code) <= 6:
                        sl.remark_codes.append(code)
                        desc_r = REMARK_CODES.get(code.lstrip("CO-").lstrip("PR-").lstrip("OA-"),
                                  REMARK_CODES.get(code, ""))
                        if desc_r:
                            sl.remark_descriptions.append(f"{code}: {desc_r}")

            # Denial detection
            if sl.plan_paid == 0 and sl.billed_amount and sl.billed_amount > 0:
                if sl.not_covered and sl.not_covered >= sl.billed_amount * 0.9:
                    sl.is_denied = True
                    sl.is_hsa_eligible = False
                    if sl.remark_descriptions:
                        sl.denial_reason = sl.remark_descriptions[0]

            service_lines.append(sl)

        if service_lines:
            data.service_lines = service_lines
            data.has_denials = any(sl.is_denied for sl in service_lines)

    # ── Text fallbacks ────────────────────────────────────────────────────────

    def _text_identity(self, text: str, data: EOBData) -> None:
        tl = text.lower()

        if not data.payer_name:
            for frag, name in PAYER_PATTERNS:
                if frag in tl:
                    data.payer_name = name
                    break

        # Member ID
        if not data.member_id:
            m = re.search(r'(?:member\s*id|member\s*number|id\s*number|subscriber\s*id)\s*[:#]?\s*([A-Z0-9\-]{5,20})', text, re.I)
            if m:
                data.member_id = m.group(1).strip()

        # Group number
        if not data.group_number:
            m = re.search(r'(?:group\s*(?:number|#|no\.?))\s*[:#]?\s*([A-Z0-9\-]{3,20})', text, re.I)
            if m:
                data.group_number = m.group(1).strip()

        # Claim number
        if not data.claim_number:
            m = re.search(r'(?:claim\s*(?:number|#|no\.?)|claim\s*id)\s*[:#]?\s*([A-Z0-9\-]{5,25})', text, re.I)
            if m:
                data.claim_number = m.group(1).strip()

        # Patient name
        if not data.patient_name:
            m = re.search(r'(?:patient|member|subscriber)(?:\s+name)?\s*[:#]?\s*([A-Z][A-Za-z\s\-\.]{4,40})', text, re.I)
            if m:
                name = m.group(1).strip().title()
                if 2 <= len(name.split()) <= 4:
                    data.patient_name = name

        # Provider name
        if not data.provider_name:
            m = re.search(r'(?:provider|physician|facility|rendering\s+provider)\s*[:#]?\s*([A-Z][A-Za-z\s\-\.]{4,50})', text, re.I)
            if m:
                data.provider_name = m.group(1).strip().title()[:50]

        # NPI
        if not data.provider_npi:
            m = re.search(r'\bNPI\s*[:#]?\s*(\d{10})\b', text, re.I)
            if m:
                data.provider_npi = m.group(1)

    def _text_dates(self, text: str, data: EOBData) -> None:
        DR = r'\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s+\d{4}'

        if not data.eob_date:
            for pat in [
                rf'(?:eob\s+date|statement\s+date|date\s+of\s+notice|date\s+issued)\s*[:#]?\s*({DR})',
                rf'(?:processed|adjudicated)\s+(?:on\s+)?({DR})',
            ]:
                m = re.search(pat, text, re.I)
                if m:
                    data.eob_date = m.group(1).strip()
                    break

        if not data.date_of_service:
            m = re.search(rf'(?:date(?:s)?\s+of\s+service|service\s+date)\s*[:#]?\s*({DR})', text, re.I)
            if m:
                data.date_of_service = m.group(1).strip()

        if not data.date_received:
            m = re.search(rf'(?:date\s+received|received\s+date)\s*[:#]?\s*({DR})', text, re.I)
            if m:
                data.date_received = m.group(1).strip()

    def _text_amounts(self, text: str, data: EOBData) -> None:
        patterns = [
            ("total_billed",           [r'(?:total\s+)?(?:amount\s+billed|billed\s+amount|charges?\s+billed)\s*\$?\s*([\d,\.]+)']),
            ("total_allowed",          [r'(?:total\s+)?(?:allowed\s+amount|eligible\s+amount|approved\s+amount)\s*\$?\s*([\d,\.]+)']),
            ("total_plan_paid",        [r'(?:plan|insurance)\s+(?:paid|payment)\s*\$?\s*([\d,\.]+)',
                                        r'benefit\s+paid\s*\$?\s*([\d,\.]+)']),
            ("total_patient_responsibility", [
                                        r'(?:patient|your)\s+responsibility\s*\$?\s*([\d,\.]+)',
                                        r'amount\s+you\s+owe\s*\$?\s*([\d,\.]+)',
                                        r'your\s+share\s*\$?\s*([\d,\.]+)']),
            ("total_deductible",       [r'(?:applied\s+to\s+)?deductible\s*\$?\s*([\d,\.]+)']),
            ("total_copay",            [r'co-?pay(?:ment)?\s*\$?\s*([\d,\.]+)']),
            ("total_coinsurance",      [r'co-?insurance\s*\$?\s*([\d,\.]+)']),
            ("deductible_limit",       [r'(?:annual|individual)\s+deductible\s+(?:limit|maximum)\s*\$?\s*([\d,\.]+)',
                                        r'deductible\s+(?:limit|maximum)\s*[:\$]?\s*([\d,\.]+)']),
            ("deductible_met",         [r'deductible\s+(?:met|applied|used|satisfied)\s*[:\$]?\s*([\d,\.]+)',
                                        r'(?:ytd\s+)?(?:applied\s+to\s+deductible|deductible\s+applied)\s*\$?\s*([\d,\.]+)']),
            ("deductible_remaining",   [r'deductible\s+remaining\s*[:\$]?\s*([\d,\.]+)',
                                        r'remaining\s+deductible\s*[:\$]?\s*([\d,\.]+)']),
            ("oop_limit",              [r'(?:out.of.pocket|oop)\s+(?:limit|maximum|max)\s*[:\$]?\s*([\d,\.]+)']),
            ("oop_met",                [r'(?:out.of.pocket|oop)\s+(?:met|applied|used)\s*[:\$]?\s*([\d,\.]+)',
                                        r'(?:ytd\s+)?(?:out.of.pocket|oop)\s+accumulation\s*[:\$]?\s*([\d,\.]+)']),
            ("oop_remaining",          [r'(?:out.of.pocket|oop)\s+remaining\s*[:\$]?\s*([\d,\.]+)',
                                        r'remaining\s+(?:out.of.pocket|oop)\s*[:\$]?\s*([\d,\.]+)']),
        ]
        for attr, pats in patterns:
            if getattr(data, attr) is not None:
                continue
            for pat in pats:
                m = re.search(pat, text, re.I)
                if m:
                    v = self._parse(m.group(1))
                    if v is not None and v >= 0:
                        setattr(data, attr, v)
                        break

    # ── Derivation ────────────────────────────────────────────────────────────

    def _derive(self, data: EOBData) -> None:
        # Aggregate from service lines if totals missing
        if data.service_lines:
            def sum_field(f):
                vals = [getattr(sl, f) for sl in data.service_lines if getattr(sl, f) is not None]
                return round(sum(vals), 2) if vals else None

            if not data.total_billed:           data.total_billed           = sum_field("billed_amount")
            if not data.total_allowed:          data.total_allowed          = sum_field("allowed_amount")
            if not data.total_not_covered:      data.total_not_covered      = sum_field("not_covered")
            if not data.total_deductible:       data.total_deductible       = sum_field("deductible_applied")
            if not data.total_copay:            data.total_copay            = sum_field("copay")
            if not data.total_coinsurance:      data.total_coinsurance      = sum_field("coinsurance")
            if not data.total_plan_paid:        data.total_plan_paid        = sum_field("plan_paid")
            if not data.total_patient_responsibility:
                data.total_patient_responsibility = sum_field("patient_responsibility")

        # Infer deductible remaining
        if data.deductible_limit and data.deductible_met and not data.deductible_remaining:
            data.deductible_remaining = max(0.0, round(data.deductible_limit - data.deductible_met, 2))

        # Infer OOP remaining
        if data.oop_limit and data.oop_met and not data.oop_remaining:
            data.oop_remaining = max(0.0, round(data.oop_limit - data.oop_met, 2))

        # Infer deductible_met from deductible_limit and remaining
        if data.deductible_limit and data.deductible_remaining and not data.deductible_met:
            data.deductible_met = max(0.0, round(data.deductible_limit - data.deductible_remaining, 2))

        # Infer patient responsibility from components
        if not data.total_patient_responsibility:
            parts = [data.total_deductible, data.total_copay, data.total_coinsurance]
            v = sum(p for p in parts if p)
            if v > 0:
                data.total_patient_responsibility = round(v, 2)

        # HSA eligibility: False if purely cosmetic/non-covered services
        if data.has_denials and data.total_not_covered:
            covered_ratio = ((data.total_allowed or 0) / (data.total_billed or 1))
            if covered_ratio < 0.1:
                data.is_hsa_eligible = False

    def _score(self, data: EOBData) -> None:
        critical = [
            ("total_patient_responsibility", data.total_patient_responsibility),
            ("total_plan_paid",              data.total_plan_paid),
            ("payer_name",                   data.payer_name),
            ("service_lines",                data.service_lines or None),
        ]
        found = sum(1 for _, v in critical if v is not None)
        data.extraction_confidence = round(found / len(critical), 2)
        data.missing_fields = [n for n, v in critical if v is None]

    def extract(self, raw_text: str, tables: Optional[List] = None) -> EOBData:
        text   = (raw_text or "").replace('\r\n', '\n').replace('\r', '\n')
        tables = tables or []
        data   = EOBData()

        self._scan_summary_tables(tables, data)
        self._scan_service_lines(tables, data)

        self._text_identity(text, data)
        self._text_dates(text, data)
        self._text_amounts(text, data)

        self._derive(data)
        self._score(data)

        logger.info(
            f"EOBExtractor: confidence={data.extraction_confidence:.0%} "
            f"patient_responsibility={data.total_patient_responsibility} "
            f"service_lines={len(data.service_lines)} "
            f"payer={data.payer_name} missing={data.missing_fields}"
        )
        return data


# ─── Intelligence Layer ───────────────────────────────────────────────────────

class EOBIntelligence:
    """
    Advisory layer — answers the real questions:
      1. What do I actually owe?
      2. Was anything denied, and should I appeal?
      3. How much of my deductible/OOP is left?
      4. How much did insurance negotiate down for me?
      5. What is HSA-eligible?
      6. What should I do next?
    """

    def analyze(self, data: EOBData) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        result.update(self._cost_breakdown(data))
        result.update(self._deductible_status(data))
        result.update(self._oop_status(data))
        result.update(self._negotiation_savings(data))
        result.update(self._denial_analysis(data))
        result.update(self._hsa_analysis(data))
        result["action_plan"] = self._actions(data, result)
        result["alerts"]      = self._alerts(data, result)
        return result

    # ── Cost breakdown ────────────────────────────────────────────────────────

    def _cost_breakdown(self, data: EOBData) -> Dict:
        pr = data.total_patient_responsibility or 0.0

        components = []
        if data.total_deductible and data.total_deductible > 0:
            components.append({"label": "Deductible",    "amount": data.total_deductible,   "pct": 0, "color": "#FF9800"})
        if data.total_copay and data.total_copay > 0:
            components.append({"label": "Copay",         "amount": data.total_copay,         "pct": 0, "color": "#2196F3"})
        if data.total_coinsurance and data.total_coinsurance > 0:
            components.append({"label": "Coinsurance",   "amount": data.total_coinsurance,   "pct": 0, "color": "#9C27B0"})
        if data.total_not_covered and data.total_not_covered > 0:
            components.append({"label": "Not Covered",   "amount": data.total_not_covered,   "pct": 0, "color": "#F44336"})

        if pr and components:
            for c in components:
                c["pct"] = round(c["amount"] / pr * 100, 1) if pr > 0 else 0

        return {
            "cost_breakdown": {
                "patient_responsibility": pr,
                "plan_paid":              data.total_plan_paid,
                "total_billed":           data.total_billed,
                "components":             components,
            }
        }

    # ── Deductible status ─────────────────────────────────────────────────────

    def _deductible_status(self, data: EOBData) -> Dict:
        if not data.deductible_limit and not data.deductible_met:
            return {"deductible_status": {"available": False}}

        limit     = data.deductible_limit
        met       = data.deductible_met or 0.0
        remaining = data.deductible_remaining or (max(0.0, limit - met) if limit else None)
        pct_met   = round(met / limit * 100, 1) if limit else None
        is_met    = remaining == 0 if remaining is not None else (met >= limit if limit else False)

        return {
            "deductible_status": {
                "available":  True,
                "limit":      limit,
                "met":        met,
                "remaining":  remaining,
                "pct_met":    pct_met,
                "is_met":     is_met,
                "this_claim": data.total_deductible,
                "message": (
                    "Deductible met! Future covered services go straight to coinsurance."
                    if is_met else
                    f"${remaining:,.2f} remaining on your deductible this year."
                    if remaining is not None else
                    f"${met:,.2f} applied to deductible so far this year."
                ),
            }
        }

    # ── OOP status ────────────────────────────────────────────────────────────

    def _oop_status(self, data: EOBData) -> Dict:
        if not data.oop_limit and not data.oop_met:
            return {"oop_status": {"available": False}}

        limit     = data.oop_limit
        met       = data.oop_met or 0.0
        remaining = data.oop_remaining or (max(0.0, limit - met) if limit else None)
        pct_met   = round(met / limit * 100, 1) if limit else None
        is_met    = remaining == 0 if remaining is not None else (met >= limit if limit else False)

        return {
            "oop_status": {
                "available":  True,
                "limit":      limit,
                "met":        met,
                "remaining":  remaining,
                "pct_met":    pct_met,
                "is_met":     is_met,
                "this_claim": data.total_patient_responsibility,
                "message": (
                    "Out-of-pocket maximum reached! Insurance covers 100% of covered services for the rest of the year."
                    if is_met else
                    f"${remaining:,.2f} remaining on your out-of-pocket maximum."
                    if remaining is not None else
                    f"${met:,.2f} applied toward your out-of-pocket maximum."
                ),
            }
        }

    # ── Negotiation savings ───────────────────────────────────────────────────

    def _negotiation_savings(self, data: EOBData) -> Dict:
        billed  = data.total_billed
        allowed = data.total_allowed

        if not billed or not allowed or billed <= 0:
            return {"negotiation_savings": {"available": False}}

        saved   = round(billed - allowed, 2)
        pct_off = round(saved / billed * 100, 1) if billed > 0 else 0

        return {
            "negotiation_savings": {
                "available":   True,
                "billed":      billed,
                "allowed":     allowed,
                "saved":       saved,
                "pct_off":     pct_off,
                "message": (
                    f"Insurance negotiated your bill down from ${billed:,.2f} to ${allowed:,.2f} "
                    f"— saving you ${saved:,.2f} ({pct_off:.0f}%) through contracted rates."
                ),
            }
        }

    # ── Denial analysis ───────────────────────────────────────────────────────

    def _denial_analysis(self, data: EOBData) -> Dict:
        denied_lines = [sl for sl in data.service_lines if sl.is_denied]

        if not denied_lines:
            return {"denial_analysis": {"has_denials": False}}

        total_denied = sum(sl.billed_amount or 0 for sl in denied_lines)
        denial_details = []
        for sl in denied_lines:
            detail = {
                "date":          sl.date_of_service,
                "service":       sl.description or sl.cpt_code or "Unknown service",
                "amount":        sl.billed_amount,
                "denial_reason": sl.denial_reason or "See remark code(s)",
                "remark_codes":  sl.remark_codes,
                "appealable":    self._is_appealable(sl),
            }
            denial_details.append(detail)

        return {
            "denial_analysis": {
                "has_denials":   True,
                "count":         len(denied_lines),
                "total_denied":  total_denied,
                "details":       denial_details,
                "message": (
                    f"{len(denied_lines)} service(s) were denied, totaling ${total_denied:,.2f}. "
                    f"Review denial reasons — some may be appealable."
                ),
            }
        }

    def _is_appealable(self, sl: ServiceLine) -> bool:
        """Services denied for medical necessity, prior auth, or billing errors are often appealable."""
        APPEALABLE_CODES = {
            "50",   # medical necessity
            "57",   # prior authorization required
            "15",   # auth number missing
            "16",   # billing error
            "197",  # precertification absent
            "125",  # submission/billing error
            "96",   # non-covered — often appealable with documentation
        }
        return any(c in APPEALABLE_CODES for c in sl.remark_codes)

    # ── HSA analysis ──────────────────────────────────────────────────────────

    def _hsa_analysis(self, data: EOBData) -> Dict:
        hsa_eligible_amount = 0.0
        hsa_lines = []

        for sl in data.service_lines:
            if sl.is_hsa_eligible and sl.patient_responsibility and sl.patient_responsibility > 0:
                hsa_eligible_amount += sl.patient_responsibility
                hsa_lines.append({
                    "service":      sl.description or sl.cpt_code or "Service",
                    "amount":       sl.patient_responsibility,
                    "date":         sl.date_of_service,
                })

        if not hsa_lines:
            eligible = data.total_patient_responsibility if data.is_hsa_eligible else 0.0
            if eligible and eligible > 0:
                hsa_eligible_amount = eligible

        return {
            "hsa_analysis": {
                "eligible_amount": round(hsa_eligible_amount, 2),
                "eligible":        data.is_hsa_eligible,
                "lines":           hsa_lines,
                "message": (
                    f"${hsa_eligible_amount:,.2f} of your patient responsibility is HSA/FSA eligible."
                    if hsa_eligible_amount > 0 else
                    "No HSA-eligible expenses on this claim."
                ),
            }
        }

    # ── Actions ───────────────────────────────────────────────────────────────

    def _actions(self, data: EOBData, result: Dict) -> List[Dict]:
        actions = []
        pr = data.total_patient_responsibility or 0.0

        # Pay your responsibility
        if pr > 0:
            actions.append({
                "priority": "high", "category": "payment",
                "action":   f"Pay ${pr:,.2f} patient responsibility",
                "detail":   "Contact your provider's billing office to arrange payment.",
                "amount":   pr,
            })

        # Appeal denials
        da = result.get("denial_analysis", {})
        if da.get("has_denials"):
            appealable = [d for d in da.get("details", []) if d.get("appealable")]
            if appealable:
                actions.append({
                    "priority": "high", "category": "appeal",
                    "action":   f"Appeal {len(appealable)} denied service(s)",
                    "detail":   "Denials for medical necessity or prior auth can often be overturned with provider documentation.",
                    "amount":   sum(d.get("amount", 0) for d in appealable),
                })

        # HSA reimbursement
        ha = result.get("hsa_analysis", {})
        if ha.get("eligible_amount", 0) > 0:
            actions.append({
                "priority": "medium", "category": "hsa",
                "action":   f"Reimburse ${ha['eligible_amount']:,.2f} from HSA/FSA",
                "detail":   "Keep this EOB as documentation for HSA reimbursement requests.",
                "amount":   ha["eligible_amount"],
            })

        # OOP nearly met
        oop = result.get("oop_status", {})
        if oop.get("available") and oop.get("remaining") and oop["remaining"] < pr:
            actions.append({
                "priority": "medium", "category": "benefits",
                "action":   "Verify out-of-pocket maximum application",
                "detail":   f"Your OOP remaining (${oop['remaining']:,.2f}) is less than the amount billed. Request a billing audit.",
            })

        # Request itemized bill (if large billed amount and no service lines)
        if not data.service_lines and data.total_billed and data.total_billed > 500:
            actions.append({
                "priority": "medium", "category": "documentation",
                "action":   "Request itemized bill from provider",
                "detail":   "For bills over $500, request a line-by-line itemization to check for billing errors.",
            })

        # File / save for taxes
        if data.total_patient_responsibility and data.total_patient_responsibility > 0:
            actions.append({
                "priority": "low", "category": "taxes",
                "action":   "Save EOB for tax records",
                "detail":   "Medical expenses may be deductible if they exceed 7.5% of your AGI. Keep all EOBs.",
            })

        return sorted(actions, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])

    # ── Alerts ────────────────────────────────────────────────────────────────

    def _alerts(self, data: EOBData, result: Dict) -> List[Dict]:
        alerts = []

        if data.has_denials:
            alerts.append({
                "type": "error", "title": "Services Denied",
                "message": result.get("denial_analysis", {}).get("message", "One or more services were denied."),
            })

        oop = result.get("oop_status", {})
        if oop.get("is_met"):
            alerts.append({
                "type": "success", "title": "Out-of-Pocket Maximum Reached",
                "message": oop["message"],
            })

        ded = result.get("deductible_status", {})
        if ded.get("is_met"):
            alerts.append({
                "type": "info", "title": "Deductible Met",
                "message": ded["message"],
            })
        elif ded.get("available") and ded.get("remaining") and ded["remaining"] < 200:
            alerts.append({
                "type": "info", "title": "Almost at Deductible",
                "message": f"Only ${ded['remaining']:,.2f} left on your deductible.",
            })

        ns = result.get("negotiation_savings", {})
        if ns.get("available") and ns.get("saved", 0) > 500:
            alerts.append({
                "type": "info", "title": "Large Negotiated Savings",
                "message": ns["message"],
            })

        ha = result.get("hsa_analysis", {})
        if ha.get("eligible_amount", 0) > 0:
            alerts.append({
                "type": "info", "title": "HSA-Eligible Expense",
                "message": ha["message"],
            })

        return alerts


# ─── Analyzer (called by DocumentAnalysisNode) ────────────────────────────────

class MedicalEOBAnalyzer:
    """
    Orchestrator. Called by UniDocVerse DocumentAnalysisNode.

    Register in analyzer_registry.py:
        "medical_eob": MedicalEOBAnalyzer()

    Register in document_registry.py:
        "medical_eob": {"parser": None, "analyzer": MedicalEOBAnalyzer}
    """

    def analyze(self, file_path: str, text: str = "",
                metadata: Optional[Dict] = None) -> Dict[str, Any]:
        logger.info(f"🏥 MedicalEOBAnalyzer: {file_path}")
        metadata = metadata or {}

        raw_tables = []
        for t in metadata.get("tables", []):
            raw_tables.extend(t.get("data", []))

        extractor    = EOBExtractor()
        data         = extractor.extract(text, raw_tables)
        intelligence = EOBIntelligence().analyze(data)

        result: Dict[str, Any] = {
            "type":                   "medical_eob",
            "has_advanced_analytics": True,
        }
        result.update(data.to_dict())
        result.update(intelligence)
        result["hero_metrics"] = self._hero_metrics(data)
        result["insights"]     = self._insights(data, intelligence)
        result["ui_config"]    = self._ui_config(data)

        logger.info(
            f"✅ MedicalEOBAnalyzer: patient_resp={data.total_patient_responsibility} "
            f"plan_paid={data.total_plan_paid} denials={data.has_denials} "
            f"confidence={data.extraction_confidence:.0%}"
        )
        return result

    def _hero_metrics(self, data: EOBData) -> Dict:
        return {
            "patient_responsibility": data.total_patient_responsibility,
            "plan_paid":              data.total_plan_paid,
            "total_billed":           data.total_billed,
            "total_allowed":          data.total_allowed,
            "deductible_applied":     data.total_deductible,
            "deductible_remaining":   data.deductible_remaining,
            "oop_remaining":          data.oop_remaining,
            "has_denials":            data.has_denials,
            "is_hsa_eligible":        data.is_hsa_eligible,
        }

    def _insights(self, data: EOBData, intel: Dict) -> List[Dict]:
        insights = []
        pr = data.total_patient_responsibility or 0.0

        # Cost summary
        insights.append({
            "title":       "Your Cost Summary",
            "description": (
                f"You owe ${pr:,.2f} for this claim. "
                + (f"Insurance paid ${data.total_plan_paid:,.2f}." if data.total_plan_paid else "")
            ),
            "category": "summary",
        })

        # Negotiation savings
        ns = intel.get("negotiation_savings", {})
        if ns.get("available"):
            insights.append({
                "title":       "Insurance Savings",
                "description": ns["message"],
                "category":    "savings",
            })

        # Deductible
        ded = intel.get("deductible_status", {})
        if ded.get("available"):
            insights.append({
                "title":       "Deductible Status",
                "description": ded["message"],
                "category":    "deductible",
            })

        # OOP
        oop = intel.get("oop_status", {})
        if oop.get("available"):
            insights.append({
                "title":       "Out-of-Pocket Status",
                "description": oop["message"],
                "category":    "oop",
            })

        # HSA
        ha = intel.get("hsa_analysis", {})
        if ha.get("eligible_amount", 0) > 0:
            insights.append({
                "title":       "HSA/FSA Eligible",
                "description": ha["message"],
                "category":    "hsa",
            })

        # Denials
        da = intel.get("denial_analysis", {})
        if da.get("has_denials"):
            insights.append({
                "title":       "Claim Denials",
                "description": da["message"],
                "category":    "denial",
            })

        return insights

    def _ui_config(self, data: EOBData) -> Dict:
        return {
            "document_type": "medical_eob",
            "hero_metrics": [
                {"key": "patient_responsibility", "label": "You Owe",              "format": "currency", "is_hero": True},
                {"key": "plan_paid",              "label": "Plan Paid",            "format": "currency", "is_hero": True},
                {"key": "total_billed",           "label": "Total Billed",         "format": "currency", "is_hero": True},
                {"key": "deductible_applied",     "label": "Applied to Deductible","format": "currency", "is_hero": False},
                {"key": "deductible_remaining",   "label": "Deductible Remaining", "format": "currency", "is_hero": False},
                {"key": "oop_remaining",          "label": "OOP Remaining",        "format": "currency", "is_hero": False},
                {"key": "has_denials",            "label": "Has Denials",          "format": "boolean",  "is_hero": False},
                {"key": "is_hsa_eligible",        "label": "HSA Eligible",         "format": "boolean",  "is_hero": False},
            ],
            "charts": [
                {
                    "id": "cost_breakdown", "title": "Cost Breakdown",
                    "type": "donut", "data_path": "cost_breakdown.components",
                    "label_key": "label", "value_key": "amount",
                },
                {
                    "id": "deductible_progress", "title": "Deductible Progress",
                    "type": "progress",
                    "data_path": "deductible_status",
                    "value_key": "pct_met",
                    "color": "#FF9800",
                },
                {
                    "id": "oop_progress", "title": "Out-of-Pocket Progress",
                    "type": "progress",
                    "data_path": "oop_status",
                    "value_key": "pct_met",
                    "color": "#9C27B0",
                },
            ],
            "tables": [
                {
                    "id": "service_lines_table", "title": "Service Details",
                    "data_path": "service_lines",
                    "columns": [
                        {"key": "date_of_service",      "label": "Date"},
                        {"key": "description",          "label": "Service"},
                        {"key": "billed_amount",        "label": "Billed",      "format": "currency"},
                        {"key": "allowed_amount",       "label": "Allowed",     "format": "currency"},
                        {"key": "deductible_applied",   "label": "Deductible",  "format": "currency"},
                        {"key": "copay",                "label": "Copay",       "format": "currency"},
                        {"key": "coinsurance",          "label": "Coinsurance", "format": "currency"},
                        {"key": "plan_paid",            "label": "Plan Paid",   "format": "currency"},
                        {"key": "patient_responsibility","label": "You Owe",    "format": "currency"},
                    ],
                },
                {
                    "id": "denial_details_table", "title": "Denied Services",
                    "data_path": "denial_analysis.details",
                    "columns": [
                        {"key": "date",          "label": "Date"},
                        {"key": "service",       "label": "Service"},
                        {"key": "amount",        "label": "Amount",        "format": "currency"},
                        {"key": "denial_reason", "label": "Reason"},
                        {"key": "appealable",    "label": "Appealable",    "format": "boolean"},
                    ],
                },
            ],
            "sections": [
                {"id": "overview",     "title": "Claim Summary",            "type": "metrics",  "metrics": ["patient_responsibility", "plan_paid", "total_billed"]},
                {"id": "cost",         "title": "Cost Breakdown",           "type": "charts",   "charts":  ["cost_breakdown"]},
                {"id": "services",     "title": "Service Details",          "type": "tables",   "tables":  ["service_lines_table"]},
                {"id": "deductible",   "title": "Deductible & OOP Status",  "type": "charts",   "charts":  ["deductible_progress", "oop_progress"]},
                {"id": "denials",      "title": "Denied Services",          "type": "tables",   "tables":  ["denial_details_table"],  "show_if": "has_denials"},
                {"id": "actions",      "title": "Action Plan",              "type": "actions",  "data_path": "action_plan"},
            ],
        }