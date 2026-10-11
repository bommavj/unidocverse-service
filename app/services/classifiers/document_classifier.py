# app/services/classifiers/llm_classifier.py

import logging
from typing import Dict, Any, List, Optional, Tuple
import json
import os
import re

from app.core import config
from app.services.document_registry import DOCUMENT_REGISTRY

logger = logging.getLogger(__name__)


# ---------- Heuristic Rules (Layer 0) ----------
# Patterns that are conclusive — ONLY found in that document type.
# Credit card is checked BEFORE bank_statement because they share vocabulary.

# Heuristic rule format:
#   (doc_type, positive_patterns, min_positive_hits, negative_patterns, max_negative_hits)
# A rule fires only when positive_hits >= min_positive AND negative_hits <= max_negative.
# Negative patterns catch cross-type contamination (e.g. "homeowners" in property tax bills).
_HEURISTIC_RULES = [
    (
        "property_tax_bill",
        [
            re.compile(r"(secured|unsecured)\s+(property\s+)?tax\s+bill", re.I),
            re.compile(r"county\s+(of\s+)?\w+\s+(property\s+tax|tax\s+collector)", re.I),
            re.compile(r"assessor['\u2019]?s?\s+parcel\s+number", re.I),
            re.compile(r"parcel\s+number[\s:\|]+[\d\-]+", re.I),
            re.compile(r"(tax\s+rate\s+area|tra\s*[\d])", re.I),
            re.compile(r"(general\s+tax\s+levy|special\s+assessments?\s+total)", re.I),
            re.compile(r"(first\s+installment|second\s+installment)\s+(due|delinquent)", re.I),
            re.compile(r"property\s+tax\s+(payment|amount|due|bill)", re.I),
        ],
        2,  # any 2 = property tax bill
        [],  # no negatives needed — these patterns are unambiguous
        0,
    ),
    (
            "declarations_page",
            [
                re.compile(r"coverage\s+declaration", re.I),
                re.compile(r"named\s+insured", re.I),
                re.compile(r"total\s+policy\s+premium", re.I),
                re.compile(r"(homeowners|ho-?3|ho\s+3|dwelling\s+fire|renters\s+policy)", re.I),
                re.compile(r"(stillwater|travelers|hartford|geico|chubb|aegis|nationwide|state\s+farm|allstate|progressive)\s+insurance", re.I),
                re.compile(r"section\s+[i1]\s*[\-–]?\s*property\s+coverages", re.I),
                re.compile(r"section\s+[ii2]\s*[\-–]?\s*liability\s+coverages", re.I),
                # Coverage A/B/C/D/E/F with explicit insurance context (not standalone "dwelling")
                re.compile(r"coverage\s+[abcdef]\s*[\-–]?\s*(dwelling|other\s+structures|personal\s+property|loss\s+of\s+use|personal\s+liability|med\s+pay)", re.I),
            ],
            2,
            # Negative: property tax indicators invalidate a declarations_page match
            [
                re.compile(r"assessor['\u2019]?s?\s+parcel", re.I),
                re.compile(r"tax\s+rate\s+area", re.I),
                re.compile(r"(first|second)\s+installment\s+(due|delinquent)", re.I),
                re.compile(r"county\s+(of\s+)?\w+\s+(property\s+tax|tax\s+collector)", re.I),
                re.compile(r"parcel\s+number[\s:\|]+[\d\-]+", re.I),
            ],
            1,  # 1 negative hit is enough to reject the declarations_page match
    ),
    (
        "annual_report",
        [
            re.compile(r"\bform\s+10-k\b", re.I),
            re.compile(r"annual\s+report\s+pursuant\s+to\s+section", re.I),
            re.compile(r"securities\s+and\s+exchange\s+commission", re.I),
            re.compile(r"fiscal\s+year\s+ended", re.I),
            re.compile(r"consolidated\s+balance\s+sheet", re.I),
            re.compile(r"consolidated\s+statements?\s+of\s+(comprehensive\s+)?loss", re.I),
        ],
        2, [], 0,
    ),
    (
        "credit_card_statement",
        [
            re.compile(r"minimum\s+payment\s+(due|warning)", re.I),
            re.compile(r"(visa\s+signature|visa\s+platinum|mastercard|american\s+express|amex|discover\s+card)", re.I),
            re.compile(r"(total\s+credit\s+line|cash\s+credit\s+line)", re.I),
            re.compile(r"penalty\s+apr", re.I),
            re.compile(r"(purchases\s+and\s+adjustments|fees\s+charged|interest\s+charged)", re.I),
            re.compile(r"new\s+balance\s+total", re.I),
            re.compile(r"current\s+payment\s+due", re.I),
            re.compile(r"late\s+payment\s+warning", re.I),
            re.compile(r"\bAPR\b", re.I),
        ],
        2, [], 0,
    ),
    (
        "bank_statement",
        [
            re.compile(r"beginning\s+balance\s+on\s+\w+\s+\d+", re.I),
            re.compile(r"ending\s+balance\s+on\s+\w+\s+\d+", re.I),
            re.compile(r"deposits\s+and\s+other\s+additions", re.I),
            re.compile(r"withdrawals\s+and\s+other\s+subtractions", re.I),
            re.compile(r"\brouting\s+number\b", re.I),
            re.compile(r"(adv\s+plus\s+banking|advantage\s+banking)", re.I),
            re.compile(r"(beginning|opening)\s+balance", re.I),
            re.compile(r"(ending|closing)\s+balance", re.I),
            re.compile(r"daily\s+ledger\s+balance", re.I),
        ],
        2, [], 0,
    ),
    (
        "utility_bill",
        [
            re.compile(r"\b(kwh|kilowatt.?hour)\b", re.I),
            re.compile(r"\btherms?\b", re.I),
            re.compile(r"meter\s+(number|reading|id)", re.I),
            re.compile(r"(electric|gas|water|sewer)\s+(usage|consumption|charge)", re.I),
            re.compile(r"billing\s+period.{0,40}(kwh|therms?|meter)", re.I),
            re.compile(r"service\s+address", re.I),
        ],
        2, [], 0,
    ),
    (
        "invoice",
        [
            re.compile(r"invoice\s*(number|#|no\.?)\s*[:\-]?\s*\w+", re.I),
            re.compile(r"(amount|total)\s+due", re.I),
            re.compile(r"bill\s+to\s*:", re.I),
            re.compile(r"remit\s+to", re.I),
            re.compile(r"payment\s+terms", re.I),
        ],
        2, [], 0,
    ),
    (
        "purchase_order",
        [
            re.compile(r"purchase\s+order\s*(number|#|no\.?)", re.I),
            re.compile(r"p\.?o\.?\s*(number|#|no\.?)\s*[:\-]?\s*\w+", re.I),
            re.compile(r"requisition\s+number", re.I),
        ],
        1, [], 0,
    ),
    (
        "tax_form",
        [
            re.compile(r"\bw.?2\b", re.I),
            re.compile(r"\b1040\b", re.I),
            re.compile(r"\b1099.?\w*\b", re.I),
            re.compile(r"tax\s+year\s+\d{4}", re.I),
            re.compile(r"internal\s+revenue\s+service", re.I),
            re.compile(r"social\s+security\s+(wages|tax\s+withheld)", re.I),
            re.compile(r"federal\s+income\s+tax\s+withheld", re.I),
        ],
        1,
        # Exclude property tax bills — they share "tax" language but are a different type
        [re.compile(r"assessor['’]?s?\s+parcel", re.I),
         re.compile(r"(secured|unsecured)\s+tax\s+bill", re.I)],
        1,
    ),
    (
        "resume",
        [
            re.compile(r"(work\s+experience|professional\s+experience|employment\s+history)", re.I),
            re.compile(r"(references\s+available|curriculum\s+vitae|\bcv\b)", re.I),
            re.compile(r"(objective|summary|profile)\s*[\n\r]", re.I),
        ],
        2, [], 0,
    ),
    (
        "contract",
        [
            re.compile(r"(this\s+agreement|this\s+contract)\s+is\s+(entered|made|executed)", re.I),
            re.compile(r"\bwhereas\b", re.I),
            re.compile(r"in\s+witness\s+whereof", re.I),
            re.compile(r"governing\s+law", re.I),
            re.compile(r"(indemnif(y|ication)|hold\s+harmless)", re.I),
        ],
        2, [], 0,
    ),
    # ── Pay Stub / Payroll ───────────────────────────────────────────────────────
    (
        "pay_stub",
        [
            re.compile(r"(gross\s+pay|gross\s+earnings)", re.I),
            re.compile(r"(net\s+pay|take.?home\s+pay)", re.I),
            re.compile(r"\byt[dt]\b", re.I),  # YTD or YTT
            re.compile(r"(federal\s+income\s+tax|fica|social\s+security\s+tax|medicare\s+tax)", re.I),
            re.compile(r"(pay\s+period|pay\s+date|check\s+date)", re.I),
            re.compile(r"(employee\s+id|emp\.?\s*id|employee\s+number)", re.I),
            re.compile(r"(regular\s+hours|overtime\s+hours|hourly\s+rate)", re.I),
        ],
        3, [], 0,
    ),
    # ── Expense Report ──────────────────────────────────────────────────────────
    (
        "expense_report",
        [
            re.compile(r"expense\s+report", re.I),
            re.compile(r"(reimbursement|reimburse)", re.I),
            re.compile(r"(business\s+purpose|expense\s+category|expense\s+type)", re.I),
            re.compile(r"(submitted\s+by|employee\s+name|department)", re.I),
            re.compile(r"(receipt\s+attached|receipt\s+required|no\s+receipt)", re.I),
        ],
        2, [], 0,
    ),
    # ── Receipt ─────────────────────────────────────────────────────────────────
    (
        "receipt",
        [
            re.compile(r"(receipt\s+number|receipt\s*#|transaction\s+id)", re.I),
            re.compile(r"(subtotal|sub-total|sales\s+tax|total\s+paid)", re.I),
            re.compile(r"(thank\s+you\s+for\s+your\s+(purchase|order|visit))", re.I),
            re.compile(r"(cashier|register\s+#|store\s+#|order\s+confirmation)", re.I),
        ],
        2, [], 0,
    ),
    # ── Medical Record ──────────────────────────────────────────────────────────
    (
        "medical_record",
        [
            re.compile(r"(date\s+of\s+service|dos\s*:)", re.I),
            re.compile(r"(diagnosis|icd.?\d{1,2}\s*[:–])", re.I),
            re.compile(r"(procedure\s+code|cpt\s*[:–\s]\s*\d{5})", re.I),
            re.compile(r"(attending\s+physician|ordering\s+provider|rendering\s+provider)", re.I),
            re.compile(r"(patient\s+(name|dob|id|mrn)|medical\s+record\s+number)", re.I),
            re.compile(r"(discharge\s+summary|chief\s+complaint|assessment\s+and\s+plan)", re.I),
        ],
        2, [], 0,
    ),
    # ── Medical Bill / EOB ───────────────────────────────────────────────────────
    (
        "medical_bill",
        [
            re.compile(r"(explanation\s+of\s+benefits|eob)", re.I),
            re.compile(r"(amount\s+billed|insurance\s+paid|patient\s+(responsibility|owes))", re.I),
            re.compile(r"(deductible\s+(applied|met)|out.of.pocket)", re.I),
            re.compile(r"(service\s+date|claim\s+number|provider\s+name)", re.I),
        ],
        2, [], 0,
    ),
    # ── Insurance Policy ────────────────────────────────────────────────────────
    (
        "insurance_policy",
        [
            re.compile(r"policy\s+(number|no\.?|#)\s*[:\-]?\s*[\w\-]+", re.I),
            re.compile(r"(effective\s+date|policy\s+period)", re.I),
            re.compile(r"(exclusion|endorsement|rider|addendum)", re.I),
            re.compile(r"(insured|policyholder|named\s+insured)", re.I),
            re.compile(r"(deductible|premium|coverage\s+limit)", re.I),
        ],
        3,
        # Exclude if it looks like a declarations page (simpler summary) or property tax
        [re.compile(r"coverage\s+declaration", re.I),
         re.compile(r"assessor['’]?s?\s+parcel", re.I)],
        1,
    ),
    # ── Insurance Claim ─────────────────────────────────────────────────────────
    (
        "insurance_claim",
        [
            re.compile(r"(claim\s+(number|#|form)|claimant)", re.I),
            re.compile(r"(date\s+of\s+(loss|incident|accident))", re.I),
            re.compile(r"(adjuster|claim\s+adjuster|claims\s+representative)", re.I),
            re.compile(r"(loss\s+description|damage\s+description)", re.I),
        ],
        2, [], 0,
    ),
    # ── Mortgage Document ────────────────────────────────────────────────────────
    (
        "mortgage_document",
        [
            re.compile(r"(mortgage|deed\s+of\s+trust|trust\s+deed)", re.I),
            re.compile(r"(borrower|mortgagor|lender|mortgagee)", re.I),
            re.compile(r"(loan\s+amount|principal\s+amount|original\s+loan)", re.I),
            re.compile(r"(interest\s+rate|annual\s+percentage\s+rate|apr)", re.I),
            re.compile(r"(monthly\s+payment|amortization|balloon\s+payment)", re.I),
        ],
        3, [], 0,
    ),
    # ── Closing Disclosure ───────────────────────────────────────────────────────
    (
        "closing_disclosure",
        [
            re.compile(r"closing\s+disclosure", re.I),
            re.compile(r"(cash\s+to\s+close|funds\s+to\s+close)", re.I),
            re.compile(r"(loan\s+terms|loan\s+information|projected\s+payments)", re.I),
            re.compile(r"(closing\s+costs|loan\s+costs|other\s+costs)", re.I),
            re.compile(r"(hud-1|settlement\s+statement|good\s+faith\s+estimate)", re.I),
        ],
        2, [], 0,
    ),
    # ── Lease Agreement ──────────────────────────────────────────────────────────
    (
        "lease_agreement",
        [
            re.compile(r"(lease\s+agreement|rental\s+agreement)", re.I),
            re.compile(r"(landlord|lessor|property\s+owner)", re.I),
            re.compile(r"(tenant|lessee|renter)", re.I),
            re.compile(r"(monthly\s+rent|rental\s+amount|rent\s+due)", re.I),
            re.compile(r"(security\s+deposit|lease\s+term|tenancy)", re.I),
        ],
        3, [], 0,
    ),
    # ── USCIS / Immigration Receipt ──────────────────────────────────────────────
    (
        "uscis_receipt",
        [
            re.compile(r"(i-797|form\s+i.?797)", re.I),
            re.compile(r"(uscis|u\.s\.\s+citizenship\s+and\s+immigration)", re.I),
            re.compile(r"(receipt\s+notice|approval\s+notice|priority\s+date)", re.I),
            re.compile(r"(petition|application\s+type|case\s+type)", re.I),
        ],
        2, [], 0,
    ),
    # ── Government Form ──────────────────────────────────────────────────────────
    (
        "government_form",
        [
            re.compile(r"\bform\s+(i-9|ss-4|941|940|w-4|i-9)\b", re.I),
            re.compile(r"(department\s+of\s+(labor|treasury|homeland\s+security|state))", re.I),
            re.compile(r"(omb\s+control\s+number|omb\s+no\.)", re.I),
            re.compile(r"(social\s+security\s+administration|ssa\.gov)", re.I),
        ],
        2,
        # Exclude IRS income tax forms — those are tax_form
        [re.compile(r"(form\s+1040|form\s+w-2|form\s+1099)", re.I)],
        1,
    ),
    # ── NDA ──────────────────────────────────────────────────────────────────────
    (
        "nda",
        [
            re.compile(r"(non.?disclosure\s+agreement|nda|confidentiality\s+agreement)", re.I),
            re.compile(r"(confidential\s+information|proprietary\s+information)", re.I),
            re.compile(r"(disclosing\s+party|receiving\s+party)", re.I),
        ],
        2, [], 0,
    ),
    # ── Statement of Work / SOW ──────────────────────────────────────────────────
    (
        "sow",
        [
            re.compile(r"(statement\s+of\s+work|scope\s+of\s+work)", re.I),
            re.compile(r"(deliverables|milestones|acceptance\s+criteria)", re.I),
            re.compile(r"(project\s+timeline|project\s+schedule|completion\s+date)", re.I),
        ],
        2, [], 0,
    ),
    # ── Offer Letter ─────────────────────────────────────────────────────────────
    (
        "offer_letter",
        [
            re.compile(r"(offer\s+of\s+employment|employment\s+offer)", re.I),
            re.compile(r"(start\s+date|commencement\s+date|first\s+day)", re.I),
            re.compile(r"(base\s+salary|annual\s+salary|compensation\s+package)", re.I),
            re.compile(r"(at.?will\s+employment|contingent\s+on|background\s+check)", re.I),
        ],
        2, [], 0,
    ),
    # ── Payoff Statement ─────────────────────────────────────────────────────────
    (
        "payoff_statement",
        [
            re.compile(r"(payoff\s+(amount|quote|statement|figure))", re.I),
            re.compile(r"(good\s+through|valid\s+through|per\s+diem)", re.I),
            re.compile(r"(outstanding\s+balance|remaining\s+balance|loan\s+payoff)", re.I),
        ],
        2, [], 0,
    ),
    # ── Birth Certificate ────────────────────────────────────────────────────────
    (
        "birth_certificate",
        [
            re.compile(r"(certificate\s+of\s+(live\s+)?birth|birth\s+certificate)", re.I),
            re.compile(r"(date\s+of\s+birth|place\s+of\s+birth|city\s+of\s+birth)", re.I),
            re.compile(r"(county\s+of\s+birth|state\s+of\s+birth|country\s+of\s+birth)", re.I),
            re.compile(r"(father.{0,5}name|mother.{0,5}(maiden\s+)?name)", re.I),
            re.compile(r"(state\s+registrar|county\s+clerk|vital\s+(records?|statistics))", re.I),
        ],
        2, [], 0,
    ),
    # ── Passport ─────────────────────────────────────────────────────────────────
    (
        "passport",
        [
            re.compile(r"\bpassport\b", re.I),
            re.compile(r"(passport\s+(number|no\.?|#))", re.I),
            re.compile(r"(nationality|country\s+of\s+citizenship)", re.I),
            re.compile(r"(date\s+of\s+(issue|expiry|expiration))", re.I),
            re.compile(r"(place\s+of\s+birth|issuing\s+authority|issuing\s+country)", re.I),
        ],
        2, [], 0,
    ),
    # ── Driver's License ──────────────────────────────────────────────────────────
    (
        "drivers_license",
        [
            re.compile(r"(driver.{0,5}s?\s+license|driving\s+license)", re.I),
            re.compile(r"(license\s+(number|no\.?|class)|dl\s+(number|no\.?|class))", re.I),
            re.compile(r"(department\s+of\s+motor\s+vehicles|dmv)", re.I),
            re.compile(r"(endorsements|restrictions|class\s+[a-cm])", re.I),
        ],
        2, [], 0,
    ),
]

DOC_TYPES = [
    # Finance / Accounting
    "invoice", "purchase_order", "credit_memo", "bank_statement", "credit_card_statement",
    "inventory_report", "expense_report", "sales_report",
    "financial_statement", "forecast_report",

    # Receipts & Transactions
    "receipt", "payment_confirmation", "transaction_record",

    # Payroll / HR
    "payroll", "pay_stub", "resume", "offer_letter",
    "performance_review", "employment_contract",

    # Legal
    "contract", "legal_filing", "nda", "msa", "sow",
    "court_order", "affidavit", "subpoena",

    # Healthcare / Medical
    "medical_record", "medical_bill", "lab_report",
    "prescription", "eob_medical",

    # Government / Public Sector
    "tax_form", "uscis_receipt", "government_form", "passport_document",
    "visa_document", "public_notice",

    # Education
    "transcript", "education_certificate",
    "recommendation_letter", "student_record",

    # Real Estate
    "lease_agreement", "property_deed", "mortgage_document",
    "rent_statement", "hoa_statement",

    # Compliance / Audit
    "audit_report", "policy_document", "compliance_report",
    "risk_assessment", "sox_report",

    # Manufacturing / Operations
    "manufacturing_spec", "quality_report", "safety_document",
    "bom", "work_order", "maintenance_log",

    # Utilities / Telecom / Internet
    "utility_bill", "telecom_bill", "internet_bill",
    "water_bill", "gas_bill", "electric_bill",

    # Logistics / Supply Chain
    "packing_slip", "bill_of_lading", "shipping_label",
    "delivery_note", "freight_invoice",

    # Enterprise / General
    "spreadsheet", "document", "memo", "letter",
    "meeting_minutes", "project_plan", "business_proposal",
    "statement_of_work", "service_report", "timesheet", "newsletter",

    # SEC / Corporate Governance
    "proxy_statement", "annual_report", "sec_filing",

    "insurance_claim", "insurance_policy", "insurance_eob",
    "auto_insurance_claim", "health_insurance_claim",
    "declarations_page",
    "renewal_notice", "nonrenewal_notice", "cancellation_notice", "loss_run",
    "loan_estimate", "closing_disclosure", "payoff_statement", "payoff_quote", "mortgage_payoff",

    # Property / Tax
    "property_tax_bill",

    # Personal Identity Documents
    "birth_certificate", "passport", "drivers_license",
    "social_security_card", "marriage_certificate", "death_certificate",
    "vaccination_record", "naturalization_certificate",

    # Other common docs without dedicated filename patterns
    "explanation_of_benefits",
]



# ---------- Layer 2: Keywords ----------
# IMPORTANT: bank_statement keywords must NOT include generic words like
# "statement", "account", "transaction" that also appear on credit card statements.
# Each type should only list terms that are SPECIFIC to that type.

DOC_KEYWORDS: Dict[str, List[str]] = {

    "declarations_page": [
            "coverage declaration", "named insured", "expiration date", "effective date",
            "total policy premium", "policy number",
            "coverage a", "coverage b", "coverage c", "coverage d",
            "coverage e", "coverage f",
            "dwelling", "other structures", "personal property", "loss of use",
            "personal liability", "med pay to others",
            "deductible", "all peril",
            "homeowners", "ho-3", "ho 3",
            "section i", "section ii",
            "property coverages", "liability coverages",
            "named insured", "agent",
            "stillwater", "travelers", "hartford", "geico", "chubb",
            "aegis", "nationwide", "state farm", "allstate",
            "forms and endorsements",
            "mortgagee", "loan number",
            "no coverage", "earthquake",
        ],

    "annual_report": [
            "form 10-k", "annual report", "securities and exchange commission",
            "fiscal year ended", "consolidated balance sheets",
            "consolidated statements of comprehensive loss",
            "notes to consolidated financial statements",
            "management's discussion and analysis",
            "risk factors", "part i", "part ii", "part iv",
            "item 1a", "item 7", "item 8",
            "earnings per share", "stockholders equity",
            "accumulated deficit",
        ],

    # ── Credit card statement ─────────────────────────────────────────────────
    # Listed before bank_statement so scoring can differentiate
    "credit_card_statement": [
        "minimum payment due", "minimum payment warning", "total minimum payment",
        "current payment due", "new balance total",
        "total credit line", "cash credit line", "credit available",
        "annual percentage rate", "purchase apr", "cash advance apr",
        "penalty apr", "balance transfer apr",
        "fees charged", "interest charged",
        "purchases and adjustments", "payments and other credits",
        "late payment warning",
        "visa signature", "visa platinum", "mastercard",
        "american express", "discover card",
        "rewards points", "cash back", "statement credit",
        "account ending in", "card ending in",
        "credit card statement", "new balance", "previous balance",
        "credit limit", "available credit",
    ],

    # ── Bank statement ────────────────────────────────────────────────────────
    # Only terms exclusive to bank/checking/savings accounts
    "bank_statement": [
        "beginning balance", "ending balance",
        "deposits and other additions", "withdrawals and other subtractions",
        "opening balance", "closing balance",
        "routing number", "direct deposit",
        "overdraft fee", "nsf fee", "non-sufficient funds",
        "checking account", "savings account", "money market account",
        "daily balance", "average balance", "ledger balance",
        "total for this period", "year-to-date",
        "adv plus banking", "advantage banking",
        "wire transfer", "ach transfer",
        "atm withdrawal", "atm deposit",
        "check number", "check deposit",
        "interest earned",
    ],

    "invoice": [
        "invoice", "invoice number", "amount due", "bill to", "due date",
        "invoice date", "remit to", "subtotal", "balance due", "itemized",
        "payment terms", "purchase order", "po number",
    ],

    "purchase_order": [
        "purchase order", "po number", "purchase order number",
        "vendor", "buyer", "ship to", "bill to", "order date",
    ],

    "credit_memo": [
        "credit memo", "credit amount", "memo number",
        "credit issued", "credit note",
    ],

    "expense_report": [
        "expense report", "reimburs", "expense category",
        "employee expense", "expense date", "expense amount",
        "business purpose", "receipt attached",
    ],

    "receipt": [
        "receipt", "total paid", "payment method", "transaction id",
        "cashier", "change due", "thank you for your purchase",
        "order number",
    ],

    "payroll": [
        "payroll", "net pay", "gross pay", "salary", "pay period",
        "deductions", "withholding", "ytd", "earnings", "taxable wages",
        "employee id", "pay date",
    ],

    "sales_report": [
        "sales report", "revenue", "units sold", "region sales",
        "sales summary", "sales by", "sales performance",
    ],

    "inventory_report": [
        "inventory", "stock", "sku", "on hand", "warehouse",
        "inventory count", "inventory valuation", "reorder level",
    ],

    "financial_statement": [
        "balance sheet", "income statement", "cash flow",
        "equity", "assets", "liabilities", "retained earnings",
        "profit and loss", "p&l", "statement of cash flows",
    ],

    "tax_form": [
        "w-2", "1099", "1040", "tax year", "irs",
        "withholding", "taxpayer", "employer identification number",
        "social security number",
    ],

    "contract": [
        "agreement", "terms and conditions", "party", "whereas",
        "hereby", "effective date", "governing law", "indemnify",
        "confidentiality", "termination", "liability",
    ],

    "offer_letter": [
        "offer of employment", "position", "start date", "compensation",
        "benefits", "employment terms", "reporting to", "offer valid until",
    ],

    "letter": [
        "dear", "sincerely", "regards", "to whom it may concern",
        "subject", "reference", "yours faithfully",
    ],

    "memo": [
        "memo", "memorandum", "subject", "cc:", "internal communication",
        "date:", "from:", "to:",
    ],

    "packing_slip": [
        "packing slip", "ship to", "ship date", "carrier",
        "tracking number", "items shipped", "quantity shipped",
    ],

    "insurance_claim": [
        "claim number", "policy number", "loss date", "adjuster",
        "coverage", "claim summary", "insured", "claimant"
    ],

    "medical_record": [
        "patient", "diagnosis", "treatment", "icd-10", "provider",
        "medical history", "clinical notes", "prescription",
        "allergies", "vital signs",
    ],

    "transcript": [
        "course", "credits", "gpa", "semester", "term",
        "student id", "registrar", "grade", "academic record",
    ],

    "spreadsheet": [
        "sheet", "worksheet", "tab", "cell", "column", "row",
        "spreadsheet", "workbook",
    ],

    "forecast_report": [
        "forecast", "projection", "scenario", "baseline",
        "assumption", "trend analysis", "sensitivity analysis",
        "forecast period",
    ],

    "utility_bill": [
        "service address", "service location", "meter", "meter number",
        "usage", "consumption", "billing period", "service period",
        "kwh", "therms", "ccf", "gas usage",
        "electric usage", "electric service", "gas service",
        "total current charges", "delivery charges", "generation charges",
        "rate schedule", "tariff", "baseline usage",
        "meter read", "meter reading", "previous read", "current read",
        "pacific gas and electric", "pg&e", "pge",
        "southern california edison", "sce",
        "san diego gas & electric", "sdg&e",
        "consolidated edison", "coned", "national grid",
        "water usage", "sewer usage", "gallons used",
    ],

    "telecom_bill": [
        "mobile", "cellular", "wireless", "phone number",
        "voice plan", "data usage", "data plan", "minutes used",
        "sms", "text messages", "roaming", "international calls",
        "line access", "device payment", "equipment installment",
        "verizon", "att", "at&t", "t-mobile", "sprint",
    ],

    "internet_bill": [
        "broadband", "internet service", "internet plan", "bandwidth",
        "download speed", "upload speed", "router", "modem",
        "wi-fi", "wifi", "gateway", "data cap",
        "comcast", "xfinity", "spectrum", "charter",
        "cox communications", "centurylink", "frontier",
    ],

    "document": [
        "page", "section", "appendix", "document", "contents",
    ],

    "payoff_statement": [
        "payoff statement",
        "payoff quote",
        "good through",
        "per diem",
        "payoff amount",
        "total amount due",
        "loan number",
        "mortgage loan",
        "servicer",
        "wire instructions",
        "principal balance",
        "interest per diem",
    ],

    "property_tax_bill": [
        "secured property tax", "unsecured property tax", "annual secured",
        "assessor parcel number", "parcel number", "tax rate area",
        "first installment", "second installment", "delinquent date",
        "total amount due", "taxable value", "county tax collector",
        "general tax levy", "special assessments", "homeowners exemption",
        "fiscal year", "property tax bill",
    ],

    "birth_certificate": [
        "certificate of birth", "certificate of live birth", "birth certificate",
        "date of birth", "place of birth", "city of birth", "county of birth",
        "state file number", "certificate number", "registration number",
        "father name", "mother name", "maiden name",
        "state registrar", "county clerk", "vital records", "vital statistics",
        "registrar of vital statistics",
    ],

    "passport": [
        "passport", "passport number", "passport no",
        "nationality", "country of citizenship",
        "place of birth", "date of issue", "date of expiry", "date of expiration",
        "issuing authority", "issuing country",
        "united states of america", "bearer", "machine readable",
    ],

    "drivers_license": [
        "driver license", "drivers license", "driver's license",
        "license number", "license class", "dl number",
        "department of motor vehicles", "dmv",
        "endorsements", "restrictions",
        "expiration date", "date of birth", "address",
        "organ donor", "real id", "commercial driver",
    ],

    "social_security_card": [
        "social security", "social security number", "ssn",
        "social security administration", "ssa",
        "this number has been established for",
        "signature", "not valid for employment",
    ],

    "marriage_certificate": [
        "certificate of marriage", "marriage certificate", "marriage license",
        "groom", "bride", "husband", "wife", "spouse",
        "date of marriage", "place of marriage", "county clerk",
        "officiant", "witness", "solemnized",
    ],

    "death_certificate": [
        "certificate of death", "death certificate",
        "date of death", "cause of death", "place of death",
        "decedent", "deceased",
        "state registrar", "vital records", "funeral home",
        "manner of death", "certifying physician",
    ],

    "vaccination_record": [
        "vaccination", "immunization", "vaccine",
        "covid-19", "covid vaccine", "mmr", "hepatitis",
        "date administered", "dose", "lot number",
        "health department", "cdc card", "who yellow card",
    ],

}


# ---------- Layer 1: Structural ----------
# Same fix: bank_statement structural keywords must not overlap with credit card

STRUCTURAL_PATTERNS = {

    "uscis_receipt": {
        "min_tables": 0,
        "keywords_any": [
            "u.s. citizenship and immigration services",
            "uscis", "notice of action", "i-797", "i-129", "i-140", "i-485",
            "petition for a nonimmigrant worker", "petition for alien worker",
            "nonimmigrant worker", "h-1b", "h1b", "l-1a", "l-1b", "o-1",
            "receipt notice", "approval notice", "denial notice",
            "request for evidence", "service center",
            "department of homeland security",
        ],
    },

    "declarations_page": {
            "min_tables": 0,
            "keywords_any": [
                "coverage declaration", "named insured", "expiration date",
                "total policy premium", "coverage a", "coverage b",
                "coverage c", "coverage d", "coverage e",
                "dwelling", "personal property", "personal liability",
                "deductible", "homeowners", "section i", "section ii",
            ],
        },

    "annual_report": {
            "min_tables": 1,
            "keywords_any": [
                "form 10-k", "annual report", "fiscal year ended",
                "consolidated balance sheet", "management's discussion",
                "risk factors", "securities and exchange commission",
                "notes to consolidated financial statements",
            ],
        },

    # ── Credit card statement ─────────────────────────────────────────────────
    "credit_card_statement": {
        "min_tables": 0,
        "keywords_any": [
            "minimum payment due", "new balance total", "current payment due",
            "total credit line", "cash credit line",
            "purchases and adjustments", "fees charged", "interest charged",
            "late payment warning", "penalty apr",
            "visa signature", "mastercard", "american express",
        ],
    },

    # ── Bank statement ────────────────────────────────────────────────────────
    "bank_statement": {
        "min_tables": 1,
        "keywords_any": [
            "beginning balance", "ending balance",
            "deposits and other additions", "withdrawals and other subtractions",
            "routing number", "direct deposit",
            "overdraft fee", "nsf fee",
            "checking account", "savings account",
            "daily balance", "average balance",
        ],
    },

    "invoice": {
        "min_tables": 1,
        "requires_line_items": True,
        "keywords_any": [
            "invoice", "invoice number", "bill to", "amount due",
            "invoice date", "subtotal", "balance due", "remit to",
        ],
    },

    "purchase_order": {
        "min_tables": 1,
        "keywords_any": [
            "purchase order", "po number", "vendor", "ship to", "order date",
        ],
    },

    "credit_memo": {
        "min_tables": 1,
        "keywords_any": [
            "credit memo", "credit note", "credit amount", "memo number",
        ],
    },

    "financial_statement": {
        "min_tables": 1,
        "keywords_any": [
            "balance sheet", "income statement", "cash flow",
            "assets", "liabilities", "equity", "p&l",
        ],
    },

    "expense_report": {
        "min_tables": 1,
        "keywords_any": [
            "expense", "reimbursement", "expense category",
            "expense date", "expense amount",
        ],
    },

    "sales_report": {
        "min_tables": 1,
        "keywords_any": [
            "sales report", "revenue", "units sold",
            "sales summary", "sales by",
        ],
    },

    "inventory_report": {
        "min_tables": 1,
        "keywords_any": [
            "inventory", "stock", "sku", "on hand", "warehouse",
        ],
    },

    "payroll": {
        "min_tables": 1,
        "keywords_any": [
            "earnings", "deductions", "net pay", "gross pay",
            "pay period", "ytd", "withholding",
        ],
    },

    "resume": {
        "min_tables": 0,
        "keywords_any": [
            "experience", "skills", "education", "certifications",
            "professional summary", "work history",
        ],
    },

    "offer_letter": {
        "min_tables": 0,
        "keywords_any": [
            "offer of employment", "position", "start date",
            "compensation", "employment terms",
        ],
    },

    "performance_review": {
        "min_tables": 0,
        "keywords_any": [
            "performance review", "evaluation", "goals",
            "competencies", "rating scale",
        ],
    },

    "medical_record": {
        "min_tables": 0,
        "keywords_any": [
            "patient", "diagnosis", "treatment", "icd-10",
            "provider", "clinical notes", "vital signs",
        ],
    },

    "medical_bill": {
        "min_tables": 1,
        "keywords_any": [
            "cpt code", "icd-10", "procedure", "allowed amount",
            "copay", "coinsurance", "deductible",
        ],
    },

    "lab_report": {
        "min_tables": 1,
        "keywords_any": [
            "lab results", "reference range", "specimen",
            "hematology", "cbc", "cmp",
        ],
    },

    "insurance_claim": {
        "min_tables": 1,
        "keywords_any": [
            "claim number", "policy number", "loss date",
            "adjuster", "coverage", "claim summary",
        ],
    },

    "loan_estimate": {
            "min_tables": 0,
            "keywords_any": [
                "loan estimate", "loan term", "projected payments",
                "closing cost details", "comparisons", "loan costs",
                "other costs", "calculating cash to close",
                "rate lock", "save this loan estimate",
            ],
        },
        "closing_disclosure": {
            "min_tables": 0,
            "keywords_any": [
                "closing disclosure", "closing cost details",
                "cash to close", "summaries of transactions",
                "loan disclosures",
            ],
        },

    "insurance_policy": {
        "min_tables": 0,
        "keywords_any": [
            "policy number", "coverage limits",
            "endorsement", "binder", "beneficiary",
            "insured", "policyholder", "deductible",
        ],
        # NOTE: removed "premium" and "effective date" — too generic,
        # fires on mortgage documents (mortgage insurance premium, rate effective date)
    },

    "payoff_statement": {
        "min_tables": 0,
        "keywords_any": [
            "payoff statement",
            "good through",
            "per diem",
            "payoff amount",
            "loan number",
            "wire instructions",
        ],
    },

    "property_tax_bill": {
        "min_tables": 0,
        "keywords_any": [
            "secured property tax", "unsecured property tax",
            "assessor parcel number", "parcel number", "tax rate area",
            "first installment", "second installment",
            "total amount due", "county tax collector", "annual secured",
        ],
    },

    "birth_certificate": {
        "min_tables": 0,
        "keywords_any": [
            "certificate of birth", "certificate of live birth",
            "date of birth", "place of birth",
            "state registrar", "county clerk", "vital records",
            "father name", "mother name", "maiden name",
        ],
    },

    "passport": {
        "min_tables": 0,
        "keywords_any": [
            "passport", "passport number", "nationality",
            "date of issue", "date of expiry", "place of birth",
            "issuing authority", "country of citizenship",
        ],
    },

    "drivers_license": {
        "min_tables": 0,
        "keywords_any": [
            "driver license", "drivers license",
            "license number", "license class",
            "department of motor vehicles", "dmv",
            "endorsements", "restrictions", "organ donor",
        ],
    },

    "vaccination_record": {
        "min_tables": 0,
        "keywords_any": [
            "vaccination", "immunization", "vaccine",
            "date administered", "dose", "lot number", "cdc",
        ],
    },

    "insurance_eob": {
        "min_tables": 1,
        "keywords_any": [
            "explanation of benefits", "eob", "allowed amount",
            "not covered", "member responsibility",
        ],
    },

    "tax_form": {
        "min_tables": 0,
        "keywords_any": [
            "w-2", "1099", "1040", "tax year", "irs",
        ],
    },

    "government_form": {
        "min_tables": 0,
        "keywords_any": [
            "form", "department of", "bureau of",
            "official use only", "public notice",
        ],
    },

    "passport_document": {
        "min_tables": 0,
        "keywords_any": [
            "passport number", "issuing authority",
            "nationality", "visa type",
        ],
    },

    "transcript": {
        "min_tables": 1,
        "keywords_any": [
            "course", "credits", "gpa", "semester", "term",
            "grade", "academic record",
        ],
    },

    "education_certificate": {
        "min_tables": 0,
        "keywords_any": [
            "certificate", "diploma", "degree awarded",
            "continuing education", "ce credits",
        ],
    },

    "lease_agreement": {
        "min_tables": 0,
        "keywords_any": [
            "lease agreement", "tenant", "landlord",
            "rent due", "security deposit", "premises",
        ],
    },

    "property_deed": {
        "min_tables": 0,
        "keywords_any": [
            "grantor", "grantee", "legal description",
            "parcel number", "county recorder",
        ],
    },

    "mortgage_document": {
        "min_tables": 0,
        "keywords_any": [
            "mortgage", "deed of trust", "escrow",
            "impound", "lienholder", "loan number",
        ],
    },

    "audit_report": {
        "min_tables": 1,
        "keywords_any": [
            "audit finding", "noncompliance", "corrective action",
            "internal controls", "risk assessment", "sox",
        ],
    },

    "policy_document": {
        "min_tables": 0,
        "keywords_any": [
            "policy statement", "scope", "definitions",
            "responsibilities", "procedures",
        ],
    },

    "manufacturing_spec": {
        "min_tables": 0,
        "keywords_any": [
            "specification", "tolerance", "bill of materials",
            "assembly instructions", "revision number",
        ],
    },

    "quality_report": {
        "min_tables": 1,
        "keywords_any": [
            "quality inspection", "nonconformance",
            "defect rate", "qc report", "qa report",
        ],
    },

    "safety_document": {
        "min_tables": 0,
        "keywords_any": [
            "msds", "sds", "material safety data sheet",
            "hazard identification", "ppe required",
        ],
    },

    "utility_bill": {
        "min_tables": 1,
        "keywords_any": [
            "service address", "usage", "billing period",
            "service period", "meter", "kwh", "therms",
            "usage summary", "billing summary",
            "total current charges", "electric usage", "gas usage",
        ],
    },

    "telecom_bill": {
        "min_tables": 1,
        "keywords_any": [
            "data usage", "minutes", "sms", "text messages",
            "mobile number", "wireless", "call detail",
            "plan charges", "roaming",
        ],
    },

    "internet_bill": {
        "min_tables": 1,
        "keywords_any": [
            "internet service", "broadband", "download speed",
            "upload speed", "router rental", "modem rental",
            "wi-fi", "wifi",
        ],
    },

    "document": {
        "min_tables": 0,
        "keywords_any": ["page", "section", "appendix", "document"],
    },
}


# ---------- Layer 3: Regex ----------
# Added credit_card_statement; tightened bank_statement to avoid false positives

DOC_REGEX: Dict[str, re.Pattern] = {
    "declarations_page": re.compile(
            r"\b("
            r"coverage\s+declaration|"
            r"named\s+insured|"
            r"total\s+policy\s+premium|"
            r"homeowners\s+[0-9]\s+special\s+form|"
            r"coverage\s+[abcdef]\s*[\-\u2013]?\s*(dwelling|other\s+structures|personal\s+property)|"
            r"section\s+[i]+\s*[\-\u2013]?\s*property\s+coverages"
            r")\b",
            re.I
        ),

    "annual_report": re.compile(
            r"\b(form\s+10-k|annual\s+report|fiscal\s+year\s+ended|"
            r"consolidated\s+balance\s+sheet|securities\s+and\s+exchange\s+commission)\b",
            re.I
        ),
    "invoice":          re.compile(r"\b(invoice\s*#|inv[-_ ]?\d{3,}|invoice number)\b", re.I),
    "purchase_order":   re.compile(r"\b(po\s*#|po[-_ ]?\d{3,}|purchase order number)\b", re.I),
    "credit_memo":      re.compile(r"\b(credit memo|cm[-_ ]?\d{3,})\b", re.I),

    # Tightened: require "bank statement" or "account statement", NOT just "statement"
    "bank_statement":   re.compile(r"\b(bank\s+statement|account\s+statement|statement\s+of\s+account)\b", re.I),

    # NEW: credit card statement regex — any of these is conclusive
    "credit_card_statement": re.compile(
        r"\b("
        r"minimum\s+payment\s+due|"
        r"credit\s+card\s+statement|"
        r"total\s+credit\s+line|"
        r"cash\s+credit\s+line|"
        r"penalty\s+apr|"
        r"purchases\s+and\s+adjustments|"
        r"new\s+balance\s+total|"
        r"late\s+payment\s+warning"
        r")\b",
        re.I
    ),

    "tax_form":         re.compile(r"\b(1040|1099|w-2|w2)\b", re.I),
    "payroll":          re.compile(r"\b(pay period|net pay|gross pay|earnings statement|pay stub)\b", re.I),
    "transcript":       re.compile(r"\b(official transcript|unofficial transcript|gpa)\b", re.I),

    "utility_bill":     re.compile(
        r"\b(kwh|therms|service address|service agreement id|meter (no\.?|number|#)|usage summary|billing period)\b",
        re.I
    ),
    "telecom_bill":     re.compile(
        r"\b(data usage|minutes used|sms|text messages|call detail|mobile number|wireless plan)\b",
        re.I
    ),
    "internet_bill":    re.compile(
        r"\b(broadband|internet service|download speed|upload speed|router rental|modem rental|wi-?fi)\b",
        re.I
    ),
    "legal_contract":   re.compile(
        r"\b(agreement|hereinafter|whereas|witnesseth|indemnif(y|ication)|governing\s+law|non[- ]?disclosure|nda|msa)\b",
        re.I
    ),
    "legal_filing":     re.compile(
        r"\b(case\s+number|plaintiff|defendant|affidavit|motion\s+to|subpoena|deposition|summons)\b",
        re.I
    ),
    "financial_report": re.compile(
        r"\b(balance\s+sheet|income\s+statement|cash\s+flow|p&l|profit\s+and\s+loss|fiscal\s+year)\b",
        re.I
    ),
    "loan_document":    re.compile(
        r"\b(promissory\s+note|loan\s+agreement|principal\s+balance|amortization|collateral)\b",
        re.I
    ),
    "medical_bill":     re.compile(
        r"\b(cpt\s*code|icd[- ]?10|allowed\s+amount|copay|coinsurance|explanation\s+of\s+benefits|eob)\b",
        re.I
    ),
    "prescription":     re.compile(r"\b(rx\s*#|prescription|dosage|refill|pharmacy|dispense)\b", re.I),
    "lab_report":       re.compile(r"\b(lab\s+results|reference\s+range|specimen|hematology|cbc|cmp)\b", re.I),
    "loan_estimate": re.compile(
            r"\b(loan\s+estimate|closing\s+cost\s+details|projected\s+payments|"
            r"rate\s+lock|save\s+this\s+loan\s+estimate|calculating\s+cash\s+to\s+close)\b",
            re.I
        ),
    "closing_disclosure": re.compile(
            r"\b(closing\s+disclosure|cash\s+to\s+close|summaries\s+of\s+transactions|"
            r"loan\s+disclosures)\b",
            re.I
        ),
    "insurance_policy": re.compile(
        r"\b(policy\s*(no\.?|number)|coverage\s+limits|premium|endorsement|binder|beneficiary)\b",
        re.I
    ),
    "insurance_eob":    re.compile(
        r"\b(explanation\s+of\s+benefits|eob|allowed\s+amount|member\s+responsibility)\b",
        re.I
    ),
    "uscis_receipt": re.compile(
        r"\b(uscis|u\.s\.\s+citizenship\s+and\s+immigration|notice\s+of\s+action|"
        r"i-?797|i-?129|i-?140|i-?485|h-?1b|l-?1[ab]|nonimmigrant\s+worker|"
        r"petition\s+for\s+(?:a\s+)?(?:non)?immigrant|department\s+of\s+homeland\s+security|"
        r"approval\s+notice|receipt\s+notice|denial\s+notice|request\s+for\s+evidence|"
        r"(?:vermont|california|texas|nebraska|potomac)\s+service\s+center)\b",
        re.I
    ),
    "government_form":  re.compile(
        r"\b(form\s+\d{2,}|department\s+of|bureau\s+of|official\s+use\s+only|public\s+notice)\b",
        re.I
    ),
    "lease_agreement":  re.compile(
        r"\b(lease\s+agreement|tenant|landlord|rent\s+due|security\s+deposit|term\s+of\s+lease)\b",
        re.I
    ),
    "property_deed":    re.compile(
        r"\b(grantor|grantee|legal\s+description|parcel\s+number|county\s+recorder)\b",
        re.I
    ),
    "mortgage_document": re.compile(
        r"\b(mortgage|deed\s+of\s+trust|escrow|lienholder|loan\s+number)\b",
        re.I
    ),
    "audit_report":     re.compile(
        r"\b(audit\s+finding|non[- ]?compliance|corrective\s+action|internal\s+controls|sox|iso\s+9001)\b",
        re.I
    ),
    "manufacturing_spec": re.compile(
        r"\b(specification|tolerance|bom|bill\s+of\s+materials|assembly\s+instructions|revision\s+number)\b",
        re.I
    ),
    "safety_document":  re.compile(
        r"\b(msds|sds|material\s+safety\s+data\s+sheet|hazard\s+identification|ppe)\b",
        re.I
    ),
    "payoff_statement": re.compile(
        r"(payoff statement|payoff quote|good through|per diem|payoff amount|total amount due)",
        re.I
    ),
    "property_tax_bill": re.compile(
        r"\b(assessor.{0,5}parcel\s+number|secured\s+property\s+tax|unsecured\s+property\s+tax|"
        r"tax\s+rate\s+area|first\s+installment\s+due|second\s+installment\s+due|"
        r"annual\s+secured\s+bill|county\s+tax\s+collector)\b",
        re.I
    ),
    "birth_certificate": re.compile(
        r"\b(certificate\s+of\s+(live\s+)?birth|birth\s+certificate|"
        r"vital\s+(records?|statistics)|state\s+registrar|county\s+clerk.{0,30}birth|"
        r"date\s+of\s+birth.{0,50}place\s+of\s+birth)\b",
        re.I
    ),
    "passport": re.compile(
        r"\b(passport\s+(number|no\.?|#)|united\s+states\s+passport|"
        r"country\s+of\s+citizenship|date\s+of\s+(issue|expiry|expiration).{0,30}passport|"
        r"machine\s+readable\s+zone|issuing\s+authority.{0,30}passport)\b",
        re.I
    ),
    "drivers_license": re.compile(
        r"\b(driver.{0,5}s?\s+license|driving\s+license|"
        r"department\s+of\s+motor\s+vehicles|dmv.{0,30}license|"
        r"license\s+class\s+[a-cm]|dl\s+number|license\s+no\.?\s*[a-z0-9]+)\b",
        re.I
    ),
    "vaccination_record": re.compile(
        r"\b(vaccination\s+record|immunization\s+record|covid.{0,10}vaccine|"
        r"cdc\s+covid|vaccine\s+card|lot\s+number.{0,30}(dose|vaccine)|"
        r"date\s+administered)\b",
        re.I
    ),
}


def _safe_get_rows(table: Any) -> List:
    if not isinstance(table, dict):
        return []
    rows = table.get("data")
    if isinstance(rows, (list, tuple)) and rows:
        return rows
    rows = table.get("rows")
    if isinstance(rows, (list, tuple)) and rows:
        return rows
    return []


def _text_blob_from_parsed(parsed: Dict[str, Any]) -> str:
    parts: List[str] = []
    try:
        text = parsed.get("text")
        if text and isinstance(text, str):
            parts.append(text[:5000])

        tables = parsed.get("tables") or parsed.get("sheets") or []
        if isinstance(tables, (list, tuple)):
            for table in tables[:3]:
                if not isinstance(table, dict):
                    continue
                columns = table.get("columns") or table.get("headers") or table.get("cols") or []
                if isinstance(columns, (list, tuple)):
                    col_text = " ".join(str(c)[:100] for c in columns[:20] if c is not None)
                    if col_text:
                        parts.append(col_text)
                rows = _safe_get_rows(table)
                for row in rows[:5]:
                    if isinstance(row, (list, tuple)):
                        row_text = " ".join(str(cell)[:50] for cell in row[:10] if cell is not None)
                        if row_text:
                            parts.append(row_text)

        email_headers = parsed.get("email_headers")
        if email_headers and isinstance(email_headers, dict):
            header_text = " ".join(f"{k}:{v}" for k, v in email_headers.items() if k and v)
            if header_text:
                parts.append(header_text)

        metadata = parsed.get("metadata")
        if metadata and isinstance(metadata, dict):
            for key in ['title', 'subject', 'description']:
                value = metadata.get(key)
                if value and isinstance(value, str):
                    parts.append(value[:200])

    except Exception as e:
        logger.warning(f"⚠️ Text extraction had issues: {e}")

    result = "\n".join(parts).lower()
    if not result or len(result) < 10:
        logger.warning("⚠️ Very little text extracted for classification")
    else:
        logger.debug(f"📝 Extracted {len(result)} chars for classification")
    return result


import time as _time

# Module-level cache so all DocumentClassifier instances share one DB read per TTL window.
_MODEL_CACHE: dict = {"model": None, "ts": 0.0}
_MODEL_CACHE_TTL = 60.0  # seconds — refresh at most once per minute

def _get_cached_model() -> str:
    now = _time.monotonic()
    if _MODEL_CACHE["model"] is None or (now - _MODEL_CACHE["ts"]) > _MODEL_CACHE_TTL:
        _MODEL_CACHE["model"] = config.get_active_model()
        _MODEL_CACHE["ts"] = now
    return _MODEL_CACHE["model"]


class DocumentClassifier:
    """4-layer classifier: structural + keyword + regex + LLM (fallback)"""

    DOCUMENT_TYPES = DOC_TYPES

    def __init__(self):
        try:
            import ollama
            host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            self.client = ollama.Client(host=host)
            # Use env var only when explicitly set; otherwise read from DB with TTL cache
            self.model = os.getenv("OLLAMA_MODEL") or _get_cached_model()
            self.client.list()
            logger.info(f"✅ LLM classifier initialized with Ollama ({self.model})")
        except Exception as e:
            logger.error(f"Failed to initialize Ollama classifier: {e}")
            self.client = None
            self.model = None

    # ---------- Layer 0: Scored heuristic ----------

    def scored_heuristic_classify(self, text: str) -> Tuple[Optional[str], float]:
        """
        Returns (doc_type, confidence) or (None, 0.0).
        Rules are 5-tuples: (doc_type, pos_patterns, min_pos, neg_patterns, max_neg).
        A rule fires only when positive_hits >= min_pos AND negative_hits <= max_neg.
        """
        for rule in _HEURISTIC_RULES:
            doc_type, pos_patterns, min_pos, neg_patterns, max_neg = rule
            pos_hits = sum(1 for p in pos_patterns if p.search(text))
            if pos_hits < min_pos:
                continue
            neg_hits = sum(1 for p in neg_patterns if p.search(text))
            if neg_hits > max_neg:
                continue
            confidence = min(0.65 + (pos_hits / len(pos_patterns)) * 0.35, 0.99)
            return doc_type, confidence
        return None, 0.0

    # ---------- Corrections ----------

    def correct_misclassifications(self, predicted: str, text: str) -> str:
        t = text.lower()
        if predicted == "credit_memo" and "statement" in t:
            return "bank_statement"
        if predicted == "credit_memo" and "invoice" in t:
            return "invoice"
        return predicted

    # ---------- Layer 1: Structural ----------

    def _structural_score(self, parsed: Dict[str, Any], text: str) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        tables = parsed.get("tables") or parsed.get("sheets") or []
        num_tables = len(tables) if isinstance(tables, (list, tuple)) else 0

        for dtype, pattern in STRUCTURAL_PATTERNS.items():
            score = 0.0
            min_tables = pattern.get("min_tables", 0)
            if num_tables >= min_tables:
                score += 0.3
            kws = pattern.get("keywords_any", [])
            if kws:
                hits = sum(1 for k in kws if k.lower() in text)
                if hits > 0:
                    score += min(0.7, 0.2 * hits)
            scores[dtype] = min(1.0, score)
        return scores

    # ---------- Layer 2: Keywords ----------

    def _keyword_scores(self, text: str) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for dtype, kws in DOC_KEYWORDS.items():
            count = sum(1 for k in kws if k.lower() in text)
            scores[dtype] = min(1.0, count * 0.25)
        return scores

    # ---------- Layer 3: Regex ----------

    def _regex_scores(self, text: str) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for dtype, rx in DOC_REGEX.items():
            try:
                if rx.search(text):
                    scores[dtype] = 1.0
            except Exception:
                continue
        return scores

    # ---------- Layer 4: LLM (fallback) ----------

    def _build_context(self, table: Dict, parsed: Dict, fmt: str) -> str:
        parts = []
        if parsed and isinstance(parsed.get("metadata"), dict):
            filename = parsed["metadata"].get("filename", "")
            if filename:
                parts.append(f"Filename: {filename}")
        if fmt:
            parts.append(f"Format: {fmt}")

        headers = self._extract_headers(table)
        if headers:
            headers_str = ", ".join(str(h) for h in headers[:20])
            parts.append(f"Column Headers: {headers_str}")

        rows = self._extract_rows(table)
        if rows:
            sample = []
            for i, row in enumerate(rows[:5]):
                if isinstance(row, (list, tuple)):
                    row_str = " | ".join(str(c)[:50] for c in row[:10] if c is not None)
                    sample.append(f"Row {i + 1}: {row_str}")
            if sample:
                parts.append("Sample Data:\n" + "\n".join(sample))

        if parsed and parsed.get("text"):
            text = str(parsed["text"])[:800]
            parts.append(f"Text Content:\n{text}")

        return "\n\n".join(parts)

    def _ask_llm(self, context: str) -> Dict[str, Any]:
        if not self.client:
            return {"type": "document", "confidence": 0.4, "reasoning": "no_llm"}

        prompt = f"""
You are a strict document classifier.

RULES:
- Respond ONLY with valid JSON.
- No text outside JSON.
- Choose ONLY from the allowed types.
- If unsure, choose the closest type, but avoid being overly specific when the content is generic.

Allowed types:
{", ".join(DOC_TYPES)}

Document text:
{context}

Return JSON:
{{
  "type": "the_document_type",
  "confidence": 0.80,
  "reasoning": "Brief explanation"
}}
"""
        response = self.client.generate(model=self.model, prompt=prompt, options={"temperature": 0})
        text = response["response"].strip()

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            # Extract outer JSON object — handle nested braces
            start = text.find("{")
            if start >= 0:
                depth, end = 0, -1
                for i, c in enumerate(text[start:], start):
                    if c == "{": depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0: end = i + 1; break
                if end > start: text = text[start:end]
            result = json.loads(text)
            # Fix leading-zero confidence e.g. 00.95
            if "confidence" in result:
                try:
                    c = float(str(result["confidence"]).lstrip("0") or "0")
                    result["confidence"] = c / 100 if c > 1 else c
                except (ValueError, TypeError):
                    result["confidence"] = 0.5
        except json.JSONDecodeError:
            import re as _re
            type_m = _re.search(r'\"type\"\s*:\s*\"([^\"]+)\"', text)
            conf_m = _re.search(r'\"confidence\"\s*:\s*([0-9.]+)', text)
            if type_m:
                return {"type": type_m.group(1),
                        "confidence": float(conf_m.group(1)) if conf_m else 0.5,
                        "reasoning": "llm_parse_salvaged"}
            logger.error(f"Failed to parse Ollama response as JSON: {text}")
            return {"type": "document", "confidence": 0.4, "reasoning": "llm_parse_error"}

        return result

    # ---------- classify() — 4-layer ensemble ----------

    # Filename substring → (doc_type, confidence) — checked before all other layers.
    # Order matters: more specific patterns first.
    # Official form identifiers — more reliable than any keyword score.
    _CONTENT_SIGNATURES = [
        (r"CDTFA-?230|BOE-?230|\bresale certificate\b", "resale_certificate"),
        (r"CDTFA-?401|BOE-?401|sales and use tax return", "sales_tax_return"),
        # US visa foil: machine-readable line "VNUSA…" / "VCUSA…", or the printed field labels
        (r"\bV[NC]USA[A-Z<&]|visa\s*type\s*/?\s*class.{0,80}issue\s*date|issuing\s*post\s*name", "visa_document"),
    ]

    _FILENAME_TYPE_MAP = [
        (["resale certificate", "resale-certificate", "resale_certificate", "cdtfa-230", "cdtfa230"], "resale_certificate", 0.95),
        (["cdtfa-401", "cdtfa401", "sales and use tax return", "sales tax return", "sales-tax-return"], "sales_tax_return", 0.95),
        (["sales ledger", "sales_ledger", "sales-ledger", "sales journal", "sales_journal"], "sales_ledger", 0.92),
        # Specific phrases first. Bare "statement" is NOT a signal — pay stubs
        # ("earnings statement"), mortgage, credit card, brokerage and utility
        # documents are all "statements".
        (["property-tax", "property_tax", "secured-bill", "unsecured-bill", "tax-bill"], "property_tax_bill", 0.95),
        (["payroll", "paystub", "pay_stub", "pay-stub", "pay stub", "payslip",
          "earnings statement", "earnings_statement", "earnings-statement"], "pay_stub", 0.90),
        (["mortgage statement", "mortgage_statement", "mortgage-statement"], "mortgage_statement", 0.90),
        (["invoice"],                                  "invoice",               0.90),
        (["credit_card", "creditcard", "credit card", "credit-card"], "credit_card_statement", 0.90),
        (["bank statement", "bank_statement", "bank-statement", "bankstatement", "estmt",
          "checking statement", "savings statement"],  "bank_statement",        0.85),
        (["expense"],                                  "expense_report",        0.85),
        (["receipt"],                                  "receipt",               0.85),
        (["purchase_order", "purchase-order"],         "purchase_order",        0.85),
        (["w2", "w-2"],                                "w2",                    0.90),
        (["1099"],                                     "tax_form",              0.90),
        (["1040"],                                     "tax_return",            0.90),
        (["dec-page", "dec_page", "declarations"],     "declarations_page",     0.88),
        (["mortgage"],                                 "mortgage_document",     0.85),
        (["closing", "hud-1", "hud_1"],                "closing_disclosure",    0.85),
        (["i-797", "i797", "i-94", "i94"],             "uscis_receipt",         0.88),
        (["birth-certificate", "birth_certificate", "certificate-of-birth", "birth-record",
           "birthcert", "birth cert", "birth-cert", "birthcertificate"], "birth_certificate", 0.95),
        (["passport"],                                 "passport",              0.90),
        (["drivers-license", "driver-license", "drivers_license", "driver_license"], "drivers_license", 0.90),
    ]

    def _classify_by_filename(self, parsed: Dict) -> Tuple[Optional[str], float]:
        """Returns (doc_type, confidence) if filename is a strong signal, else (None, 0)."""
        filename = ""
        if isinstance(parsed, dict) and isinstance(parsed.get("metadata"), dict):
            filename = parsed["metadata"].get("filename", "").lower()
        if not filename:
            return None, 0.0
        for substrings, doc_type, conf in self._FILENAME_TYPE_MAP:
            if any(s in filename for s in substrings):
                logger.info(f"📄 Filename match: '{filename}' → {doc_type} ({conf:.2f})")
                return doc_type, conf
        return None, 0.0

    def classify(self, table: Dict[str, Any] = None, parsed: Dict[str, Any] = None, fmt: str = None) -> Dict[str, Any]:
        if not isinstance(parsed, dict):
            logger.error(f"❌ Invalid parsed type: {type(parsed)}")
            return self._fallback_classify(table, parsed, fmt)

        # Unmistakable form signatures in the content win outright.
        _sig_text = (_text_blob_from_parsed(parsed) or "")[:600]  # form header only — reports mention these forms too
        for pattern, sig_type in self._CONTENT_SIGNATURES:
            if re.search(pattern, _sig_text, re.I):
                return {"type": sig_type, "confidence": 0.97, "reasoning": "form_signature",
                        "matched_keywords": [], "rule_scores": {}, "ml_scores": {}}
        # IRS forms print their number in the header ("Form 1099-NEC", "Schedule K-1 (Form 1065)")
        from app.services.tax.irs_forms import classify_form
        irs_type = classify_form(_sig_text)
        if irs_type:
            return {"type": irs_type, "confidence": 0.96, "reasoning": "irs_form_signature",
                    "matched_keywords": [], "rule_scores": {}, "ml_scores": {}}

        # Filename is a strong hint (it rescues garbled scans), but readable
        # content that confidently says otherwise wins — users name files loosely.
        fn_type, fn_conf = self._classify_by_filename(parsed)
        if fn_conf >= 0.85:
            content = _text_blob_from_parsed(parsed) or ""
            c_type, c_conf = self.scored_heuristic_classify(content) if len(content) >= 200 else (None, 0.0)
            if c_type and c_type != fn_type and c_conf >= 0.9:
                logger.info(f"📄 Filename says {fn_type} but content says {c_type} ({c_conf:.2f}) — using content")
                return {"type": c_type, "confidence": c_conf, "reasoning": "content_over_filename",
                        "matched_keywords": [], "rule_scores": {}, "ml_scores": {}}
            return {"type": fn_type, "confidence": fn_conf, "reasoning": "filename_priority",
                    "matched_keywords": [], "rule_scores": {}, "ml_scores": {}}

        text = _text_blob_from_parsed(parsed)
        if not text or len(text) < 10:
            logger.warning("⚠️ Insufficient text for classification")
            return self._fallback_classify(table, parsed, fmt)

        # Layer 0: scored heuristic (returns type + confidence, not just type)
        heuristic_type, heuristic_conf = self.scored_heuristic_classify(text)

        # Layers 1–3 scores
        structural = self._structural_score(parsed, text)
        keyword    = self._keyword_scores(text)
        regex      = self._regex_scores(text)

        combined: Dict[str, float] = {}
        for dtype in DOC_TYPES:
            s = structural.get(dtype, 0.0)
            k = keyword.get(dtype, 0.0)
            r = regex.get(dtype, 0.0)
            combined[dtype] = (0.5 * s) + (0.3 * k) + (0.2 * r)

        best_type, best_score = max(combined.items(), key=lambda x: x[1])

        # If regex hits strongly, trust it
        if regex.get(best_type, 0.0) >= 0.9:
            final_type = best_type
            confidence = max(best_score, 0.9)
            reasoning  = "regex+structural+keyword"

        # If structural+keyword strong enough, skip LLM
        elif best_score >= 0.7:
            final_type = best_type
            confidence = best_score
            reasoning  = "structural+keyword"

        else:
            # Layer 4: LLM fallback
            context    = self._build_context(table, parsed, fmt)
            llm_result = self._ask_llm(context)
            llm_type   = llm_result.get("type", "document")
            llm_conf   = llm_result.get("confidence", 0.6)

            llm_type = self.correct_misclassifications(llm_type, text)
            if llm_type not in DOC_TYPES:
                llm_type_lower = llm_type.lower().replace(" ", "_").replace("-", "_")
                for valid_type in DOC_TYPES:
                    if llm_type_lower in valid_type or valid_type in llm_type_lower:
                        llm_type = valid_type
                        break
                else:
                    llm_type = "document"

            if best_score >= 0.4:
                final_type = best_type
                confidence = max(best_score, float(llm_conf) * 0.8)
                reasoning  = f"ensemble(structural+keyword+llm:{llm_type})"
            else:
                final_type = llm_type
                confidence = float(llm_conf)
                reasoning  = f"llm_fallback({llm_type})"

        # Layer 0 override — only fires when heuristic is confident AND pipeline is not.
        # This is a safety net, not a blunt override that stomps correct results.
        if heuristic_type and heuristic_type != final_type:
            if heuristic_conf >= 0.7 and confidence < 0.6:
                final_type = heuristic_type
                confidence = max(confidence, heuristic_conf)
                reasoning  = f"heuristic_override({heuristic_type}:{heuristic_conf:.2f})"

        logger.info(f"✅ Final classified as: {final_type} ({confidence:.2f}) via {reasoning}")

        return {
            "type":             final_type,
            "confidence":       float(confidence),
            "reasoning":        reasoning,
            "matched_keywords": [],
            "rule_scores": {
                "structural": structural,
                "keyword":    keyword,
                "regex":      regex,
            },
            "ml_scores": {},
        }

    # ---------- Helpers / Fallback ----------

    def _extract_headers(self, table: Dict) -> List[str]:
        if not table:
            return []
        for key in ['columns', 'headers', 'cols']:
            val = table.get(key)
            if isinstance(val, (list, tuple)):
                return [str(h) for h in val if h]
        rows = self._extract_rows(table)
        if rows and len(rows) > 0 and isinstance(rows[0], (list, tuple)):
            return [str(h) for h in rows[0] if h]
        return []

    def _extract_rows(self, table: Dict) -> List:
        if not table:
            return []
        for key in ['data', 'rows']:
            val = table.get(key)
            if isinstance(val, (list, tuple)):
                return val
        return []

    def _fallback_classify(self, table: Dict, parsed: Dict, fmt: str) -> Dict[str, Any]:
        logger.info("⚠️ Using fallback classification (no LLM / insufficient text)")
        filename = ""
        if parsed and isinstance(parsed.get("metadata"), dict):
            filename = parsed["metadata"].get("filename", "").lower()

        if filename:
            if "property-tax" in filename or "property_tax" in filename or "tax-bill" in filename or "secured-bill" in filename or "unsecured-bill" in filename:
                return {"type": "property_tax_bill", "confidence": 0.92, "reasoning": "filename_match"}
            if "invoice" in filename:
                return {"type": "invoice", "confidence": 0.75, "reasoning": "filename_match"}
            if "credit_card" in filename or "creditcard" in filename:
                return {"type": "credit_card_statement", "confidence": 0.75, "reasoning": "filename_match"}
            if "bank" in filename or "statement" in filename:
                return {"type": "bank_statement", "confidence": 0.75, "reasoning": "filename_match"}
            if "payroll" in filename or "paystub" in filename or "pay_stub" in filename:
                return {"type": "payroll", "confidence": 0.75, "reasoning": "filename_match"}
            if "expense" in filename:
                return {"type": "expense_report", "confidence": 0.75, "reasoning": "filename_match"}
            if "receipt" in filename:
                return {"type": "receipt", "confidence": 0.75, "reasoning": "filename_match"}
            if "po" in filename or "purchase" in filename:
                return {"type": "purchase_order", "confidence": 0.75, "reasoning": "filename_match"}

        if fmt in ["xlsx", "xls", "csv", "tsv"]:
            return {"type": "spreadsheet", "confidence": 0.4, "reasoning": "format_fallback"}
        if fmt in ["pdf", "docx", "doc"]:
            return {"type": "document", "confidence": 0.4, "reasoning": "format_fallback"}

        return {"type": "document", "confidence": 0.3, "reasoning": "unknown"}

# app/services/classifiers/llm_classifier.py

# import logging
# from typing import Dict, Any, List, Optional, Tuple
# import json
# import os
# import re
#
# from app.core import config
# from app.services.document_registry import DOCUMENT_REGISTRY
#
# logger = logging.getLogger(__name__)
#
#
# # ---------- Structural + Keyword + Regex Registries ----------
#    # Patterns that are conclusive — ONLY found in that document type
# _HEURISTIC_RULES = [
#     # ── Credit card statement ────────────────────────────────────────────────
#     # Checked BEFORE bank_statement because they share many words
#     (
#         "credit_card_statement",
#         [
#             re.compile(r"minimum\s+payment\s+(due|warning)", re.I),
#             re.compile(r"(visa\s+signature|visa\s+platinum|mastercard|american\s+express|amex|discover\s+card)",
#                        re.I),
#             re.compile(r"(total\s+credit\s+line|cash\s+credit\s+line)", re.I),
#             re.compile(r"penalty\s+apr", re.I),
#             re.compile(r"(purchases\s+and\s+adjustments|fees\s+charged|interest\s+charged)", re.I),
#             re.compile(r"new\s+balance\s+total", re.I),
#             re.compile(r"current\s+payment\s+due", re.I),
#             re.compile(r"late\s+payment\s+warning", re.I),
#             re.compile(r"\bAPR\b", re.I),
#         ],
#         2,  # min hits to qualify
#     ),
#
#     # ── Bank statement ───────────────────────────────────────────────────────
#     (
#         "bank_statement",
#         [
#             re.compile(r"beginning\s+balance\s+on\s+\w+\s+\d+", re.I),
#             re.compile(r"ending\s+balance\s+on\s+\w+\s+\d+", re.I),
#             re.compile(r"deposits\s+and\s+other\s+additions", re.I),
#             re.compile(r"withdrawals\s+and\s+other\s+subtractions", re.I),
#             re.compile(r"\brouting\s+number\b", re.I),
#             re.compile(r"(adv\s+plus\s+banking|advantage\s+banking)", re.I),
#             re.compile(r"(beginning|opening)\s+balance", re.I),
#             re.compile(r"(ending|closing)\s+balance", re.I),
#             re.compile(r"daily\s+ledger\s+balance", re.I),
#         ],
#         2,
#     ),
#
#     # ── Utility bill ─────────────────────────────────────────────────────────
#     (
#         "utility_bill",
#         [
#             re.compile(r"\b(kwh|kilowatt.?hour)\b", re.I),
#             re.compile(r"\btherms?\b", re.I),
#             re.compile(r"meter\s+(number|reading|id)", re.I),
#             re.compile(r"(electric|gas|water|sewer)\s+(usage|consumption|charge)", re.I),
#             re.compile(r"billing\s+period.{0,40}(kwh|therms?|meter)", re.I),
#             re.compile(r"service\s+address", re.I),
#         ],
#         2,
#     ),
#
#     # ── Invoice ──────────────────────────────────────────────────────────────
#     (
#         "invoice",
#         [
#             re.compile(r"invoice\s*(number|#|no\.?)\s*[:\-]?\s*\w+", re.I),
#             re.compile(r"(amount|total)\s+due", re.I),
#             re.compile(r"bill\s+to\s*:", re.I),
#             re.compile(r"remit\s+to", re.I),
#             re.compile(r"payment\s+terms", re.I),
#         ],
#         2,
#     ),
#
#     # ── Purchase order ───────────────────────────────────────────────────────
#     (
#         "purchase_order",
#         [
#             re.compile(r"purchase\s+order\s*(number|#|no\.?)", re.I),
#             re.compile(r"p\.?o\.?\s*(number|#|no\.?)\s*[:\-]?\s*\w+", re.I),
#             re.compile(r"requisition\s+number", re.I),
#         ],
#         1,
#     ),
#
#     # ── Tax form ─────────────────────────────────────────────────────────────
#     (
#         "tax_form",
#         [
#             re.compile(r"\bw.?2\b", re.I),
#             re.compile(r"\b1040\b", re.I),
#             re.compile(r"\b1099.?\w*\b", re.I),
#             re.compile(r"tax\s+year\s+\d{4}", re.I),
#             re.compile(r"internal\s+revenue\s+service", re.I),
#             re.compile(r"social\s+security\s+(wages|tax\s+withheld)", re.I),
#             re.compile(r"federal\s+income\s+tax\s+withheld", re.I),
#         ],
#         1,  # any one of these is conclusive for tax
#     ),
#
#     # ── Resume ───────────────────────────────────────────────────────────────
#     (
#         "resume",
#         [
#             re.compile(r"(work\s+experience|professional\s+experience|employment\s+history)", re.I),
#             re.compile(r"(references\s+available|curriculum\s+vitae|\bcv\b)", re.I),
#             re.compile(r"(objective|summary|profile)\s*[\n\r]", re.I),
#         ],
#         2,
#     ),
#
#     # ── Contract ─────────────────────────────────────────────────────────────
#     (
#         "contract",
#         [
#             re.compile(r"(this\s+agreement|this\s+contract)\s+is\s+(entered|made|executed)", re.I),
#             re.compile(r"\bwhereas\b", re.I),
#             re.compile(r"in\s+witness\s+whereof", re.I),
#             re.compile(r"governing\s+law", re.I),
#             re.compile(r"(indemnif(y|ication)|hold\s+harmless)", re.I),
#         ],
#         2,
#     ),
# ]
#
# DOC_TYPES = [
#     # Finance / Accounting
#     "invoice", "purchase_order", "credit_memo", "bank_statement","credit_card_statement",
#     "inventory_report", "expense_report", "sales_report",
#     "financial_statement", "forecast_report",
#
#     # Receipts & Transactions
#     "receipt", "payment_confirmation", "transaction_record",
#
#     # Payroll / HR
#     "payroll", "pay_stub", "resume", "offer_letter",
#     "performance_review", "employment_contract",
#
#     # Legal
#     "contract", "legal_filing", "nda", "msa", "sow",
#     "court_order", "affidavit", "subpoena",
#
#     # Insurance
#     "insurance_claim", "insurance_policy", "insurance_eob",
#     "auto_insurance_claim", "health_insurance_claim",
#
#     # Healthcare / Medical
#     "medical_record", "medical_bill", "lab_report",
#     "prescription", "eob_medical",
#
#     # Government / Public Sector
#     "tax_form", "government_form", "passport_document",
#     "visa_document", "public_notice",
#
#     # Education
#     "transcript", "education_certificate",
#     "recommendation_letter", "student_record",
#
#     # Real Estate
#     "lease_agreement", "property_deed", "mortgage_document",
#     "rent_statement", "hoa_statement",
#
#     # Compliance / Audit
#     "audit_report", "policy_document", "compliance_report",
#     "risk_assessment", "sox_report",
#
#     # Manufacturing / Operations
#     "manufacturing_spec", "quality_report", "safety_document",
#     "bom", "work_order", "maintenance_log",
#
#     # Utilities / Telecom / Internet
#     "utility_bill", "telecom_bill", "internet_bill",
#     "water_bill", "gas_bill", "electric_bill",
#
#     # Logistics / Supply Chain
#     "packing_slip", "bill_of_lading", "shipping_label",
#     "delivery_note", "freight_invoice",
#
#     # Enterprise / General
#     "spreadsheet", "document", "memo", "letter",
#     "meeting_minutes", "project_plan", "business_proposal",
#     "statement_of_work", "service_report", "timesheet",
# ]
#
#
#
# DOC_KEYWORDS: Dict[str, List[str]] = {
#     "invoice": [
#         "invoice", "invoice number", "amount due", "bill to", "due date",
#         "invoice date", "remit to", "subtotal", "balance due", "itemized",
#         "payment terms", "purchase order", "po number"
#     ],
#
#     "purchase_order": [
#         "purchase order", "po number", "purchase order number",
#         "vendor", "buyer", "ship to", "bill to", "order date"
#     ],
#
#     "credit_memo": [
#         "credit memo", "credit amount", "memo number",
#         "credit issued", "credit note"
#     ],
#
#     "bank_statement": [
#         "bank statement", "account balance", "transaction",
#         "opening balance", "closing balance", "deposits", "withdrawals",
#         "statement period", "available balance", "current balance",
#         "account number", "statement date"
#     ],
#
#     "expense_report": [
#         "expense report", "reimburs", "expense category",
#         "employee expense", "expense date", "expense amount",
#         "business purpose", "receipt attached"
#     ],
#
#     "receipt": [
#         "receipt", "total paid", "payment method", "transaction id",
#         "cashier", "change due", "thank you for your purchase",
#         "order number"
#     ],
#
#     "payroll": [
#         "payroll", "net pay", "gross pay", "salary", "pay period",
#         "deductions", "withholding", "ytd", "earnings", "taxable wages",
#         "employee id", "pay date"
#     ],
#
#     "sales_report": [
#         "sales report", "revenue", "units sold", "region sales",
#         "sales summary", "sales by", "sales performance"
#     ],
#
#     "inventory_report": [
#         "inventory", "stock", "sku", "on hand", "warehouse",
#         "inventory count", "inventory valuation", "reorder level"
#     ],
#
#     "financial_statement": [
#         "balance sheet", "income statement", "cash flow",
#         "equity", "assets", "liabilities", "retained earnings",
#         "profit and loss", "p&l", "statement of cash flows"
#     ],
#
#     "tax_form": [
#         "w-2", "1099", "1040", "tax year", "irs",
#         "withholding", "taxpayer", "employer identification number",
#         "social security number"
#     ],
#
#     "contract": [
#         "agreement", "terms and conditions", "party", "whereas",
#         "hereby", "effective date", "governing law", "indemnify",
#         "confidentiality", "termination", "liability"
#     ],
#
#     "offer_letter": [
#         "offer of employment", "position", "start date", "compensation",
#         "benefits", "employment terms", "reporting to", "offer valid until"
#     ],
#
#     "letter": [
#         "dear", "sincerely", "regards", "to whom it may concern",
#         "subject", "reference", "yours faithfully"
#     ],
#
#     "memo": [
#         "memo", "memorandum", "subject", "cc:", "internal communication",
#         "date:", "from:", "to:"
#     ],
#
#     "packing_slip": [
#         "packing slip", "ship to", "ship date", "carrier",
#         "tracking number", "items shipped", "quantity shipped"
#     ],
#
#     "insurance_claim": [
#         "claim number", "policy number", "loss date", "adjuster",
#         "coverage", "claim summary", "insured", "claimant"
#     ],
#
#     "medical_record": [
#         "patient", "diagnosis", "treatment", "icd-10", "provider",
#         "medical history", "clinical notes", "prescription",
#         "allergies", "vital signs"
#     ],
#
#     "transcript": [
#         "course", "credits", "gpa", "semester", "term",
#         "student id", "registrar", "grade", "academic record"
#     ],
#
#     "spreadsheet": [
#         "sheet", "worksheet", "tab", "cell", "column", "row",
#         "spreadsheet", "workbook"
#     ],
#
#     "forecast_report": [
#         "forecast", "projection", "scenario", "baseline",
#         "assumption", "trend analysis", "sensitivity analysis",
#         "forecast period"
#     ],
#
#     # ⭐ Deep, provider-aware utility bill coverage
#     "utility_bill": [
#         # Generic utility concepts
#         "service address", "service location", "meter", "meter number",
#         "usage", "consumption", "billing period", "service period",
#         "kwh", "kwh usage", "therms", "ccf", "gas usage",
#         "electric usage", "electric service", "gas service",
#         "total current charges", "current charges", "previous balance",
#         "delivery charges", "generation charges", "transmission charges",
#         "distribution charges", "energy charges", "supply charges",
#         "delivery service", "generation service",
#         "rate schedule", "tariff", "baseline usage",
#         "service agreement id", "sa id", "account number",
#         "statement date", "bill date", "due date",
#         "amount due", "total amount due", "pay by",
#         "billing summary", "account summary", "usage summary",
#         "usage history", "daily usage", "monthly usage",
#         "meter read", "meter reading", "previous read", "current read",
#         "read type", "actual read", "estimated read",
#         "service from", "service to", "service period from",
#         "service period to",
#
#         # PG&E specific
#         "pacific gas and electric", "pg&e", "pge", "p g and e",
#         "electric delivery", "gas delivery", "electric charges",
#         "gas charges", "generation credit", "delivery credit",
#         "climate credit", "power charge", "transmission charge",
#         "distribution charge", "high usage surcharge",
#
#         # Other US utilities (SCE, SDG&E, ConEd, etc.)
#         "southern california edison", "sce",
#         "san diego gas & electric", "sdg&e", "sdge",
#         "consolidated edison", "coned", "con edison",
#         "national grid", "duke energy", "dominion energy",
#         "georgia power", "fpl", "florida power & light",
#         "ladwp", "los angeles department of water and power",
#         "xcel energy", "aps", "srp", "salt river project",
#
#         # Water/sewer often bundled
#         "water usage", "sewer usage", "stormwater", "wastewater",
#         "gallons used", "cubic feet used"
#     ],
#
#     # ⭐ Telecom bills (mobile / phone)
#     "telecom_bill": [
#         "mobile", "cellular", "wireless", "phone number",
#         "voice plan", "data usage", "data plan", "minutes used",
#         "sms", "text messages", "roaming", "international calls",
#         "domestic calls", "call detail", "call summary",
#         "line access", "device payment", "equipment installment",
#         "activation fee", "regulatory fee", "911 fee",
#         "subscriber line charge", "federal universal service",
#         "state universal service",
#
#         # Major carriers
#         "verizon", "att", "at&t", "t-mobile", "tmobile",
#         "sprint", "us cellular", "cricket wireless",
#         "boost mobile", "metro pcs", "metro by t-mobile"
#     ],
#
#     # ⭐ Internet / cable bills
#     "internet_bill": [
#         "broadband", "internet service", "internet plan", "bandwidth",
#         "download speed", "upload speed", "router", "modem",
#         "wi-fi", "wifi", "gateway", "data cap", "overage",
#         "cable modem", "fiber", "dsl", "coaxial",
#         "equipment rental", "modem rental", "router rental",
#         "internet charges", "internet fee", "broadband fee",
#         "streaming", "tv package", "cable tv", "set-top box",
#
#         # Major ISPs
#         "comcast", "xfinity", "spectrum", "charter",
#         "cox communications", "centurylink", "lumen",
#         "frontier", "verizon fios", "fios", "att fiber",
#         "google fiber"
#     ],
#
#     "document": [
#         "page", "section", "appendix", "document", "contents"
#     ],
# }
#
# STRUCTURAL_PATTERNS = {
#
#     # =========================================================
#     # FINANCE / ACCOUNTING
#     # =========================================================
#     "invoice": {
#         "min_tables": 1,
#         "requires_line_items": True,
#         "keywords_any": [
#             "invoice", "invoice number", "bill to", "amount due",
#             "invoice date", "subtotal", "balance due", "remit to"
#         ],
#     },
#
#     "purchase_order": {
#         "min_tables": 1,
#         "keywords_any": [
#             "purchase order", "po number", "vendor", "ship to", "order date"
#         ],
#     },
#
#     "credit_memo": {
#         "min_tables": 1,
#         "keywords_any": [
#             "credit memo", "credit note", "credit amount", "memo number"
#         ],
#     },
#
#     "bank_statement": {
#         "min_tables": 1,
#         "keywords_any": [
#             "statement", "account number", "transaction",
#             "opening balance", "closing balance", "available balance"
#         ],
#     },
#
#     "financial_statement": {
#         "min_tables": 1,
#         "keywords_any": [
#             "balance sheet", "income statement", "cash flow",
#             "assets", "liabilities", "equity", "p&l"
#         ],
#     },
#
#     "expense_report": {
#         "min_tables": 1,
#         "keywords_any": [
#             "expense", "reimbursement", "expense category",
#             "expense date", "expense amount"
#         ],
#     },
#
#     "sales_report": {
#         "min_tables": 1,
#         "keywords_any": [
#             "sales report", "revenue", "units sold",
#             "sales summary", "sales by"
#         ],
#     },
#
#     "inventory_report": {
#         "min_tables": 1,
#         "keywords_any": [
#             "inventory", "stock", "sku", "on hand", "warehouse"
#         ],
#     },
#
#     # =========================================================
#     # PAYROLL / HR
#     # =========================================================
#     "payroll": {
#         "min_tables": 1,
#         "keywords_any": [
#             "earnings", "deductions", "net pay", "gross pay",
#             "pay period", "ytd", "withholding"
#         ],
#     },
#
#     "resume": {
#         "min_tables": 0,
#         "keywords_any": [
#             "experience", "skills", "education", "certifications",
#             "professional summary", "work history"
#         ],
#     },
#
#     "offer_letter": {
#         "min_tables": 0,
#         "keywords_any": [
#             "offer of employment", "position", "start date",
#             "compensation", "employment terms"
#         ],
#     },
#
#     "performance_review": {
#         "min_tables": 0,
#         "keywords_any": [
#             "performance review", "evaluation", "goals",
#             "competencies", "rating scale"
#         ],
#     },
#
#     # =========================================================
#     # HEALTHCARE / MEDICAL
#     # =========================================================
#     "medical_record": {
#         "min_tables": 0,
#         "keywords_any": [
#             "patient", "diagnosis", "treatment", "icd-10",
#             "provider", "clinical notes", "vital signs"
#         ],
#     },
#
#     "medical_bill": {
#         "min_tables": 1,
#         "keywords_any": [
#             "cpt code", "icd-10", "procedure", "allowed amount",
#             "copay", "coinsurance", "deductible"
#         ],
#     },
#
#     "lab_report": {
#         "min_tables": 1,
#         "keywords_any": [
#             "lab results", "reference range", "specimen",
#             "hematology", "cbc", "cmp"
#         ],
#     },
#
#     # =========================================================
#     # INSURANCE
#     # =========================================================
#     "insurance_claim": {
#         "min_tables": 1,
#         "keywords_any": [
#             "claim number", "policy number", "loss date",
#             "adjuster", "coverage", "claim summary"
#         ],
#     },
#
#     "insurance_policy": {
#         "min_tables": 0,
#         "keywords_any": [
#             "policy number", "coverage limits", "premium",
#             "endorsement", "effective date"
#         ],
#     },
#
#     "insurance_eob": {
#         "min_tables": 1,
#         "keywords_any": [
#             "explanation of benefits", "eob", "allowed amount",
#             "not covered", "member responsibility"
#         ],
#     },
#
#     # =========================================================
#     # GOVERNMENT / PUBLIC SECTOR
#     # =========================================================
#     "tax_form": {
#         "min_tables": 0,
#         "keywords_any": [
#             "w-2", "1099", "1040", "tax year", "irs"
#         ],
#     },
#
#     "government_form": {
#         "min_tables": 0,
#         "keywords_any": [
#             "form", "department of", "bureau of",
#             "official use only", "public notice"
#         ],
#     },
#
#     "passport_document": {
#         "min_tables": 0,
#         "keywords_any": [
#             "passport number", "issuing authority",
#             "nationality", "visa type"
#         ],
#     },
#
#     # =========================================================
#     # EDUCATION
#     # =========================================================
#     "transcript": {
#         "min_tables": 1,
#         "keywords_any": [
#             "course", "credits", "gpa", "semester", "term",
#             "grade", "academic record"
#         ],
#     },
#
#     "education_certificate": {
#         "min_tables": 0,
#         "keywords_any": [
#             "certificate", "diploma", "degree awarded",
#             "continuing education", "ce credits"
#         ],
#     },
#
#     # =========================================================
#     # REAL ESTATE
#     # =========================================================
#     "lease_agreement": {
#         "min_tables": 0,
#         "keywords_any": [
#             "lease agreement", "tenant", "landlord",
#             "rent due", "security deposit", "premises"
#         ],
#     },
#
#     "property_deed": {
#         "min_tables": 0,
#         "keywords_any": [
#             "grantor", "grantee", "legal description",
#             "parcel number", "county recorder"
#         ],
#     },
#
#     "mortgage_document": {
#         "min_tables": 0,
#         "keywords_any": [
#             "mortgage", "deed of trust", "escrow",
#             "impound", "lienholder", "loan number"
#         ],
#     },
#
#     # =========================================================
#     # COMPLIANCE / AUDIT
#     # =========================================================
#     "audit_report": {
#         "min_tables": 1,
#         "keywords_any": [
#             "audit finding", "noncompliance", "corrective action",
#             "internal controls", "risk assessment", "sox"
#         ],
#     },
#
#     "policy_document": {
#         "min_tables": 0,
#         "keywords_any": [
#             "policy statement", "scope", "definitions",
#             "responsibilities", "procedures"
#         ],
#     },
#
#     # =========================================================
#     # MANUFACTURING / OPERATIONS
#     # =========================================================
#     "manufacturing_spec": {
#         "min_tables": 0,
#         "keywords_any": [
#             "specification", "tolerance", "bill of materials",
#             "assembly instructions", "revision number"
#         ],
#     },
#
#     "quality_report": {
#         "min_tables": 1,
#         "keywords_any": [
#             "quality inspection", "nonconformance",
#             "defect rate", "qc report", "qa report"
#         ],
#     },
#
#     "safety_document": {
#         "min_tables": 0,
#         "keywords_any": [
#             "msds", "sds", "material safety data sheet",
#             "hazard identification", "ppe required"
#         ],
#     },
#
#     # =========================================================
#     # UTILITIES / TELECOM / INTERNET
#     # =========================================================
#     "utility_bill": {
#         "min_tables": 1,
#         "keywords_any": [
#             "service address", "usage", "billing period",
#             "service period", "meter", "kwh", "therms",
#             "usage summary", "billing summary", "account summary",
#             "total current charges", "service from", "service to",
#             "electric usage", "gas usage", "water usage"
#         ],
#     },
#
#     "telecom_bill": {
#         "min_tables": 1,
#         "keywords_any": [
#             "data usage", "minutes", "sms", "text messages",
#             "mobile number", "wireless", "call detail",
#             "plan charges", "roaming", "international calls"
#         ],
#     },
#
#     "internet_bill": {
#         "min_tables": 1,
#         "keywords_any": [
#             "internet service", "broadband", "download speed",
#             "upload speed", "router rental", "modem rental",
#             "wi-fi", "wifi", "internet charges", "fiber"
#         ],
#     },
#
#     # =========================================================
#     # GENERIC
#     # =========================================================
#     "document": {
#         "min_tables": 0,
#         "keywords_any": ["page", "section", "appendix", "document"],
#     },
# }
#
#
# DOC_REGEX = {
#     "invoice": re.compile(r"\b(invoice\s*#|inv[-_ ]?\d{3,}|invoice number)\b", re.I),
#
#     "purchase_order": re.compile(r"\b(po\s*#|po[-_ ]?\d{3,}|purchase order number)\b", re.I),
#
#     "credit_memo": re.compile(r"\b(credit memo|cm[-_ ]?\d{3,})\b", re.I),
#
#     "bank_statement": re.compile(r"\b(statement of account|account statement|bank statement)\b", re.I),
#
#     "tax_form": re.compile(r"\b(1040|1099|w-2|w2)\b", re.I),
#
#     "payroll": re.compile(r"\b(pay period|net pay|gross pay|earnings statement|pay stub)\b", re.I),
#
#     "transcript": re.compile(r"\b(official transcript|unofficial transcript|gpa)\b", re.I),
#
#     # Utility bill: strong signals
#     "utility_bill": re.compile(
#         r"\b(kwh|therms|service address|service agreement id|meter (no\.?|number|#)|usage summary|billing period)\b",
#         re.I
#     ),
#
#     # Telecom bill: phone/data specific
#     "telecom_bill": re.compile(
#         r"\b(data usage|minutes used|sms|text messages|call detail|mobile number|wireless plan)\b",
#         re.I
#     ),
#
#     # Internet bill: broadband-specific
#     "internet_bill": re.compile(
#         r"\b(broadband|internet service|download speed|upload speed|router rental|modem rental|wi-?fi)\b",
#         re.I
#     ),
# }
#
# DOC_REGEX.update({
#
#     # =========================================================
#     # LAW / LEGAL DOCUMENTS
#     # =========================================================
#     "legal_contract": re.compile(
#         r"\b("
#         r"agreement|contract|party\s+of\s+the\s+first\s+part|"
#         r"hereinafter|hereto|whereas|witnesseth|"
#         r"indemnif(y|ication)|governing\s+law|jurisdiction|"
#         r"non[- ]?disclosure|nda|confidentiality\s+agreement|"
#         r"master\s+service\s+agreement|msa|statement\s+of\s+work|sow"
#         r")\b",
#         re.I
#     ),
#
#     "legal_filing": re.compile(
#         r"\b("
#         r"case\s+number|court\s+of|plaintiff|defendant|"
#         r"affidavit|motion\s+to|order\s+of\s+the\s+court|"
#         r"subpoena|deposition|summons"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # FINANCE / ACCOUNTING
#     # =========================================================
#     "financial_report": re.compile(
#         r"\b("
#         r"balance\s+sheet|income\s+statement|cash\s+flow|"
#         r"p&l|profit\s+and\s+loss|equity\s+statement|"
#         r"financial\s+position|auditor|fiscal\s+year"
#         r")\b",
#         re.I
#     ),
#
#     "loan_document": re.compile(
#         r"\b("
#         r"promissory\s+note|loan\s+agreement|apr|interest\s+rate|"
#         r"principal\s+balance|amortization|collateral"
#         r")\b",
#         re.I
#     ),
#
#     "investment_statement": re.compile(
#         r"\b("
#         r"portfolio|holdings|dividends|capital\s+gains|"
#         r"brokerage\s+statement|mutual\s+fund|etf|nav"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # HEALTHCARE / MEDICAL
#     # =========================================================
#     "medical_bill": re.compile(
#         r"\b("
#         r"cpt\s*code|icd[- ]?10|diagnosis|procedure|"
#         r"provider\s+id|patient\s+account|explanation\s+of\s+benefits|eob|"
#         r"allowed\s+amount|adjustment|copay|coinsurance|deductible"
#         r")\b",
#         re.I
#     ),
#
#     "prescription": re.compile(
#         r"\b("
#         r"rx\s*#|prescription|dosage|refill|pharmacy|"
#         r"sig:|take\s+\d+|dispense"
#         r")\b",
#         re.I
#     ),
#
#     "lab_report": re.compile(
#         r"\b("
#         r"lab\s+results|reference\s+range|specimen|"
#         r"hematology|chemistry\s+panel|cbc|cmp"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # INSURANCE (HEALTH, AUTO, HOME, COMMERCIAL)
#     # =========================================================
#     "insurance_policy": re.compile(
#         r"\b("
#         r"policy\s*(no\.?|number)|coverage\s+limits|premium|"
#         r"endorsement|binder|effective\s+date|insured|beneficiary"
#         r")\b",
#         re.I
#     ),
#
#     "insurance_eob": re.compile(
#         r"\b("
#         r"explanation\s+of\s+benefits|eob|allowed\s+amount|"
#         r"not\s+covered|member\s+responsibility"
#         r")\b",
#         re.I
#     ),
#
#     "auto_insurance_claim": re.compile(
#         r"\b("
#         r"claim\s*(no\.?|number)|loss\s+date|adjuster|"
#         r"vehicle\s+identification\s+number|vin|collision|comprehensive"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # GOVERNMENT / PUBLIC SECTOR
#     # =========================================================
#     "government_form": re.compile(
#         r"\b("
#         r"form\s+\d{2,}|department\s+of|bureau\s+of|"
#         r"federal\s+register|official\s+use\s+only|"
#         r"public\s+notice|ordinance|resolution"
#         r")\b",
#         re.I
#     ),
#
#     "passport_document": re.compile(
#         r"\b("
#         r"passport\s+number|issuing\s+authority|nationality|"
#         r"place\s+of\s+birth|visa\s+type"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # EDUCATION
#     # =========================================================
#     "education_certificate": re.compile(
#         r"\b("
#         r"certificate\s+of\s+completion|diploma|degree\s+awarded|"
#         r"accreditation|continuing\s+education|ce\s+credits"
#         r")\b",
#         re.I
#     ),
#
#     "recommendation_letter": re.compile(
#         r"\b("
#         r"recommend|endorse|candidate|academic\s+performance|"
#         r"to\s+whom\s+it\s+may\s+concern"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # REAL ESTATE
#     # =========================================================
#     "lease_agreement": re.compile(
#         r"\b("
#         r"lease\s+agreement|tenant|landlord|rent\s+due|"
#         r"security\s+deposit|premises|term\s+of\s+lease"
#         r")\b",
#         re.I
#     ),
#
#     "property_deed": re.compile(
#         r"\b("
#         r"grantor|grantee|legal\s+description|parcel\s+number|"
#         r"recorded\s+in|county\s+recorder"
#         r")\b",
#         re.I
#     ),
#
#     "mortgage_document": re.compile(
#         r"\b("
#         r"mortgage|deed\s+of\s+trust|escrow|impound|"
#         r"lienholder|loan\s+number"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # HR / EMPLOYMENT
#     # =========================================================
#     "resume_ext": re.compile(
#         r"\b("
#         r"experience|skills|education|certifications|"
#         r"professional\s+summary|work\s+history"
#         r")\b",
#         re.I
#     ),
#
#     "offer_letter_ext": re.compile(
#         r"\b("
#         r"offer\s+of\s+employment|position|start\s+date|"
#         r"compensation|reporting\s+to"
#         r")\b",
#         re.I
#     ),
#
#     "performance_review": re.compile(
#         r"\b("
#         r"performance\s+review|evaluation|goals|competencies|"
#         r"rating\s+scale"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # COMPLIANCE / AUDIT
#     # =========================================================
#     "audit_report": re.compile(
#         r"\b("
#         r"audit\s+finding|non[- ]?compliance|corrective\s+action|"
#         r"internal\s+controls|risk\s+assessment|sox|iso\s+9001"
#         r")\b",
#         re.I
#     ),
#
#     "policy_document": re.compile(
#         r"\b("
#         r"policy\s+statement|scope|definitions|responsibilities|"
#         r"compliance\s+requirements|procedures"
#         r")\b",
#         re.I
#     ),
#
#     # =========================================================
#     # MANUFACTURING / OPERATIONS
#     # =========================================================
#     "manufacturing_spec": re.compile(
#         r"\b("
#         r"specification|tolerance|bom|bill\s+of\s+materials|"
#         r"assembly\s+instructions|revision\s+number"
#         r")\b",
#         re.I
#     ),
#
#     "quality_report": re.compile(
#         r"\b("
#         r"quality\s+inspection|nonconformance|defect\s+rate|"
#         r"qc\s+report|qa\s+report"
#         r")\b",
#         re.I
#     ),
#
#     "safety_document": re.compile(
#         r"\b("
#         r"msds|sds|material\s+safety\s+data\s+sheet|"
#         r"hazard\s+identification|ppe|required"
#         r")\b",
#         re.I
#     ),
#
# })
#
#
#
# # DOC_TYPES = [
# #     "invoice", "purchase_order", "credit_memo", "bank_statement", "inventory_report",
# #     "receipt", "payroll", "expense_report", "sales_report",
# #     "financial_statement", "tax_form", "contract", "resume",
# #     "offer_letter", "letter", "memo", "packing_slip",
# #     "insurance_claim", "medical_record", "transcript",
# #     "spreadsheet", "document", "forecast_report",
# #     "utility_bill", "telecom_bill", "internet_bill",
# # ]
# #
# # DOC_KEYWORDS: Dict[str, List[str]] = {
# #     "invoice": ["invoice", "invoice number", "amount due", "bill to", "due date"],
# #     "purchase_order": ["purchase order", "po number", "purchase order number"],
# #     "credit_memo": ["credit memo", "credit amount", "memo number"],
# #     "bank_statement": ["bank statement", "account balance", "transaction", "opening balance", "closing balance"],
# #     "expense_report": ["expense report", "reimburs", "expense category", "employee expense"],
# #     "receipt": ["receipt", "total paid", "payment method", "transaction id"],
# #     "payroll": ["payroll", "net pay", "gross pay", "salary", "pay period"],
# #     "sales_report": ["sales report", "revenue", "units sold", "region sales"],
# #     "inventory_report": ["inventory", "stock", "sku", "on hand", "warehouse"],
# #     "financial_statement": ["balance sheet", "income statement", "cash flow", "equity", "assets", "liabilities"],
# #     "tax_form": ["w-2", "1099", "1040", "tax year", "irs"],
# #     "contract": ["agreement", "terms and conditions", "party", "whereas", "hereby"],
# #     "offer_letter": ["offer of employment", "position", "start date", "compensation"],
# #     "letter": ["dear", "sincerely", "regards", "to whom it may concern"],
# #     "memo": ["memo", "memorandum", "subject", "cc:"],
# #     "packing_slip": ["packing slip", "ship to", "ship date", "carrier"],
# #     "insurance_claim": ["claim number", "policy number", "loss date", "adjuster"],
# #     "medical_record": ["patient", "diagnosis", "treatment", "icd-10", "provider"],
# #     "transcript": ["course", "credits", "gpa", "semester", "term"],
# #     "spreadsheet": ["sheet", "worksheet", "tab", "cell", "column"],
# #     "forecast_report": ["forecast", "projection", "scenario", "baseline", "assumption"],
# #     "utility_bill": ["service address", "meter", "usage", "billing period", "kwh", "therms"],
# #     "telecom_bill": ["mobile", "data usage", "minutes", "sms", "plan"],
# #     "internet_bill": ["broadband", "internet service", "bandwidth", "router"],
# #     "document": ["page", "section", "appendix"],  # generic catch-all
# # }
# #
# # DOC_REGEX = {
# #     "invoice": re.compile(r"\bINV[-_ ]?\d{3,}\b", re.I),
# #     "purchase_order": re.compile(r"\bPO[-_ ]?\d{3,}\b", re.I),
# #     "credit_memo": re.compile(r"\bCM[-_ ]?\d{3,}\b", re.I),
# #     "bank_statement": re.compile(r"\b(statement of account|account statement)\b", re.I),
# #     "tax_form": re.compile(r"\b(1040|1099|w-2)\b", re.I),
# #     "payroll": re.compile(r"\b(pay period|net pay|gross pay)\b", re.I),
# #     "transcript": re.compile(r"\bGPA\b", re.I),
# #     "utility_bill": re.compile(r"\b(kwh|therms|meter #|service agreement id)\b", re.I),
# # }
# #
# # STRUCTURAL_PATTERNS = {
# #     "invoice": {
# #         "min_tables": 1,
# #         "requires_line_items": True,
# #         "keywords_any": ["invoice", "bill to", "amount due"],
# #     },
# #     "bank_statement": {
# #         "min_tables": 1,
# #         "keywords_any": ["statement", "account number", "transaction"],
# #     },
# #     "payroll": {
# #         "min_tables": 1,
# #         "keywords_any": ["earnings", "deductions", "net pay"],
# #     },
# #     "utility_bill": {
# #         "min_tables": 1,
# #         "keywords_any": ["service address", "usage", "billing period", "meter"],
# #     },
# #     "transcript": {
# #         "min_tables": 1,
# #         "keywords_any": ["course", "credits", "gpa", "semester"],
# #     },
# #     # extend as you see more patterns
# # }
#
#
# def _safe_get_rows(table: Any) -> List:
#     if not isinstance(table, dict):
#         return []
#     rows = table.get("data")
#     if isinstance(rows, (list, tuple)) and rows:
#         return rows
#     rows = table.get("rows")
#     if isinstance(rows, (list, tuple)) and rows:
#         return rows
#     return []
#
#
# def _text_blob_from_parsed(parsed: Dict[str, Any]) -> str:
#     parts: List[str] = []
#     try:
#         text = parsed.get("text")
#         if text and isinstance(text, str):
#             parts.append(text[:5000])
#
#         tables = parsed.get("tables") or parsed.get("sheets") or []
#         if isinstance(tables, (list, tuple)):
#             for table in tables[:3]:
#                 if not isinstance(table, dict):
#                     continue
#                 columns = table.get("columns") or table.get("headers") or table.get("cols") or []
#                 if isinstance(columns, (list, tuple)):
#                     col_text = " ".join(str(c)[:100] for c in columns[:20] if c is not None)
#                     if col_text:
#                         parts.append(col_text)
#                 rows = _safe_get_rows(table)
#                 for row in rows[:5]:
#                     if isinstance(row, (list, tuple)):
#                         row_text = " ".join(str(cell)[:50] for cell in row[:10] if cell is not None)
#                         if row_text:
#                             parts.append(row_text)
#
#         email_headers = parsed.get("email_headers")
#         if email_headers and isinstance(email_headers, dict):
#             header_text = " ".join(f"{k}:{v}" for k, v in email_headers.items() if k and v)
#             if header_text:
#                 parts.append(header_text)
#
#         metadata = parsed.get("metadata")
#         if metadata and isinstance(metadata, dict):
#             for key in ['title', 'subject', 'description']:
#                 value = metadata.get(key)
#                 if value and isinstance(value, str):
#                     parts.append(value[:200])
#
#     except Exception as e:
#         logger.warning(f"⚠️ Text extraction had issues: {e}")
#
#     result = "\n".join(parts).lower()
#     if not result or len(result) < 10:
#         logger.warning("⚠️ Very little text extracted for classification")
#     else:
#         logger.debug(f"📝 Extracted {len(result)} chars for classification")
#     return result
#
#
# class DocumentClassifier:
#     """4-layer classifier: structural + keyword + regex + LLM (fallback)"""
#
#     DOCUMENT_TYPES = DOC_TYPES
#
#     def __init__(self):
#         try:
#             import ollama
#             host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
#             self.client = ollama.Client(host=host)
#             self.model = os.getenv("OLLAMA_MODEL", config.model)
#             self.client.list()
#             logger.info(f"✅ LLM classifier initialized with Ollama ({self.model})")
#         except Exception as e:
#             logger.error(f"Failed to initialize Ollama classifier: {e}")
#             self.client = None
#             self.model = None
#
#     # ---------- Heuristics / Corrections ----------
#
#     def heuristic_classify(self, text: str) -> Optional[str]:
#         t = text.lower()
#         if "billing period" in t and ("kwh" in t or "therms" in t or "meter" in t):
#             return "utility_bill"
#         if "statement" in t and "account" in t:
#             return "bank_statement"
#         if "invoice" in t or "bill to" in t:
#             return "invoice"
#         if "purchase order" in t or "po #" in t:
#             return "purchase_order"
#         if "w-2" in t or "1040" in t or "tax year" in t:
#             return "tax_form"
#         if "resume" in t and "experience" in t:
#             return "resume"
#         if "agreement" in t and "contract" in t:
#             return "contract"
#         return None
#
#     def correct_misclassifications(self, predicted: str, text: str) -> str:
#         t = text.lower()
#         if predicted == "credit_memo" and "statement" in t:
#             return "bank_statement"
#         if predicted == "credit_memo" and "invoice" in t:
#             return "invoice"
#         return predicted
#
#     # ---------- Layer 1: Structural ----------
#
#     def _structural_score(self, parsed: Dict[str, Any], text: str) -> Dict[str, float]:
#         scores: Dict[str, float] = {}
#         tables = parsed.get("tables") or parsed.get("sheets") or []
#         num_tables = len(tables) if isinstance(tables, (list, tuple)) else 0
#
#         for dtype, pattern in STRUCTURAL_PATTERNS.items():
#             score = 0.0
#             min_tables = pattern.get("min_tables", 0)
#             if num_tables >= min_tables:
#                 score += 0.3
#             kws = pattern.get("keywords_any", [])
#             if kws:
#                 hits = sum(1 for k in kws if k.lower() in text)
#                 if hits > 0:
#                     score += min(0.7, 0.2 * hits)
#             scores[dtype] = min(1.0, score)
#         return scores
#
#     # ---------- Layer 2: Keywords ----------
#
#     def _keyword_scores(self, text: str) -> Dict[str, float]:
#         scores: Dict[str, float] = {}
#         for dtype, kws in DOC_KEYWORDS.items():
#             count = sum(1 for k in kws if k.lower() in text)
#             scores[dtype] = min(1.0, count * 0.25)
#         return scores
#
#     # ---------- Layer 3: Regex ----------
#
#     def _regex_scores(self, text: str) -> Dict[str, float]:
#         scores: Dict[str, float] = {}
#         for dtype, rx in DOC_REGEX.items():
#             try:
#                 if rx.search(text):
#                     scores[dtype] = 1.0
#             except Exception:
#                 continue
#         return scores
#
#     # ---------- Layer 4: LLM (fallback) ----------
#
#     def _build_context(self, table: Dict, parsed: Dict, fmt: str) -> str:
#         parts = []
#         if parsed and isinstance(parsed.get("metadata"), dict):
#             filename = parsed["metadata"].get("filename", "")
#             if filename:
#                 parts.append(f"Filename: {filename}")
#         if fmt:
#             parts.append(f"Format: {fmt}")
#
#         headers = self._extract_headers(table)
#         if headers:
#             headers_str = ", ".join(str(h) for h in headers[:20])
#             parts.append(f"Column Headers: {headers_str}")
#
#         rows = self._extract_rows(table)
#         if rows:
#             sample = []
#             for i, row in enumerate(rows[:5]):
#                 if isinstance(row, (list, tuple)):
#                     row_str = " | ".join(str(c)[:50] for c in row[:10] if c is not None)
#                     sample.append(f"Row {i + 1}: {row_str}")
#             if sample:
#                 parts.append("Sample Data:\n" + "\n".join(sample))
#
#         if parsed and parsed.get("text"):
#             text = str(parsed["text"])[:800]
#             parts.append(f"Text Content:\n{text}")
#
#         return "\n\n".join(parts)
#
#     def _ask_llm(self, context: str) -> Dict[str, Any]:
#         if not self.client:
#             return {"type": "document", "confidence": 0.4, "reasoning": "no_llm"}
#
#         prompt = f"""
# You are a strict document classifier.
#
# RULES:
# - Respond ONLY with valid JSON.
# - No text outside JSON.
# - Choose ONLY from the allowed types.
# - If unsure, choose the closest type, but avoid being overly specific when the content is generic.
#
# Allowed types:
# {", ".join(DOC_TYPES)}
#
# Document text:
# {context}
#
# Return JSON:
# {{
#   "type": "the_document_type",
#   "confidence": 0.80,
#   "reasoning": "Brief explanation"
# }}
# """
#         response = self.client.generate(model=self.model, prompt=prompt, options={"temperature": 0})
#         text = response["response"].strip()
#
#         if "```json" in text:
#             text = text.split("```json")[1].split("```")[0].strip()
#         elif "```" in text:
#             text = text.split("```")[1].split("```")[0].strip()
#
#         try:
#             start = text.find("{")
#             end = text.rfind("}") + 1
#             if end == 0:
#                 text += "}"
#             if start >= 0 and end > start:
#                 text = text[start:end]
#             result = json.loads(text)
#         except json.JSONDecodeError:
#             logger.error(f"Failed to parse Ollama response as JSON: {text}")
#             return {"type": "document", "confidence": 0.4, "reasoning": "llm_parse_error"}
#
#         return result
#
#     # ---------- Public classify (4-layer ensemble + guardrails) ----------
#
#     def scored_heuristic_classify(self, text: str) -> Tuple[Optional[str], float]:
#         """
#         Replaces heuristic_classify().
#         Returns (doc_type, confidence) — confidence in [0.0, 1.0].
#         Returns (None, 0.0) if nothing fires with enough evidence.
#
#         Rules are checked in order; first type that hits its min_hits threshold wins.
#         Credit card is checked before bank_statement to avoid overlap mis-fires.
#         """
#         for doc_type, patterns, min_hits in _HEURISTIC_RULES:
#             hits = sum(1 for p in patterns if p.search(text))
#             if hits >= min_hits:
#                 # Confidence scales with how many patterns fired
#                 confidence = min(0.65 + (hits / len(patterns)) * 0.35, 0.99)
#                 return doc_type, confidence
#
#         return None, 0.0
#
#     def classify(self, table: Dict[str, Any] = None, parsed: Dict[str, Any] = None, fmt: str = None) -> Dict[str, Any]:
#         if not isinstance(parsed, dict):
#             logger.error(f"❌ Invalid parsed type: {type(parsed)}")
#             return self._fallback_classify(table, parsed, fmt)
#
#         text = _text_blob_from_parsed(parsed)
#         if not text or len(text) < 10:
#             logger.warning("⚠️ Insufficient text for classification")
#             return self._fallback_classify(table, parsed, fmt)
#
#         # Layer 0: scored heuristic (returns type + confidence, not just type)
#         heuristic_type, heuristic_conf = self.scored_heuristic_classify(text)
#
#         # Layer 1–3 scores
#         structural = self._structural_score(parsed, text)
#         keyword = self._keyword_scores(text)
#         regex = self._regex_scores(text)
#
#         combined: Dict[str, float] = {}
#         for dtype in DOC_TYPES:
#             s = structural.get(dtype, 0.0)
#             k = keyword.get(dtype, 0.0)
#             r = regex.get(dtype, 0.0)
#             combined[dtype] = (0.5 * s) + (0.3 * k) + (0.2 * r)
#
#         best_type, best_score = max(combined.items(), key=lambda x: x[1])
#
#         # If regex hits strongly, trust it
#         if regex.get(best_type, 0.0) >= 0.9:
#             final_type = best_type
#             confidence = max(best_score, 0.9)
#             reasoning = "regex+structural+keyword"
#
#         # If structural+keyword strong enough, no LLM
#         elif best_score >= 0.7:
#             final_type = best_type
#             confidence = best_score
#             reasoning = "structural+keyword"
#
#         else:
#             # LLM fallback
#             context = self._build_context(table, parsed, fmt)
#             llm_result = self._ask_llm(context)
#             llm_type = llm_result.get("type", "document")
#             llm_conf = llm_result.get("confidence", 0.6)
#             llm_reason = llm_result.get("reasoning", "llm")
#
#             llm_type = self.correct_misclassifications(llm_type, text)
#             if llm_type not in DOC_TYPES:
#                 llm_type_lower = llm_type.lower().replace(" ", "_").replace("-", "_")
#                 for valid_type in DOC_TYPES:
#                     if llm_type_lower in valid_type or valid_type in llm_type_lower:
#                         llm_type = valid_type
#                         break
#                 else:
#                     llm_type = "document"
#
#             # Blend structural+keyword with LLM
#             if best_score >= 0.4:
#                 final_type = best_type
#                 confidence = max(best_score, float(llm_conf) * 0.8)
#                 reasoning = f"ensemble(structural+keyword+llm:{llm_type})"
#             else:
#                 final_type = llm_type
#                 confidence = float(llm_conf)
#                 reasoning = f"llm_fallback({llm_type})"
#
#         # Heuristic override — only when heuristic is confident AND pipeline is not.
#         # Checked AFTER the pipeline so the scored heuristic acts as a safety net,
#         # not a blunt first-pass that stomps correct structural/regex results.
#         if heuristic_type and heuristic_type != final_type:
#             if heuristic_conf >= 0.7 and confidence < 0.6:
#                 final_type = heuristic_type
#                 confidence = max(confidence, heuristic_conf)
#                 reasoning = f"heuristic_override({heuristic_type}:{heuristic_conf:.2f})"
#             # else: pipeline was confident enough — trust it, ignore heuristic
#
#         logger.info(f"✅ Final classified as: {final_type} ({confidence:.2f}) via {reasoning}")
#
#         return {
#             "type": final_type,
#             "confidence": float(confidence),
#             "reasoning": reasoning,
#             "matched_keywords": [],
#             "rule_scores": {
#                 "structural": structural,
#                 "keyword": keyword,
#                 "regex": regex,
#             },
#             "ml_scores": {},
#         }


    # def classify(self, table: Dict[str, Any] = None, parsed: Dict[str, Any] = None, fmt: str = None) -> Dict[str, Any]:
    #     if not isinstance(parsed, dict):
    #         logger.error(f"❌ Invalid parsed type: {type(parsed)}")
    #         return self._fallback_classify(table, parsed, fmt)
    #
    #     text = _text_blob_from_parsed(parsed)
    #     if not text or len(text) < 10:
    #         logger.warning("⚠️ Insufficient text for classification")
    #         return self._fallback_classify(table, parsed, fmt)
    #
    #     # Layer 0: quick heuristic
    #     h = self.heuristic_classify(text)
    #     heuristic_type = h if h else None
    #
    #     # Layer 1–3 scores
    #     structural = self._structural_score(parsed, text)
    #     keyword = self._keyword_scores(text)
    #     regex = self._regex_scores(text)
    #
    #     combined: Dict[str, float] = {}
    #     for dtype in DOC_TYPES:
    #         s = structural.get(dtype, 0.0)
    #         k = keyword.get(dtype, 0.0)
    #         r = regex.get(dtype, 0.0)
    #         combined[dtype] = (0.5 * s) + (0.3 * k) + (0.2 * r)
    #
    #     best_type, best_score = max(combined.items(), key=lambda x: x[1])
    #
    #     # If regex hits strongly, trust it
    #     if regex.get(best_type, 0.0) >= 0.9:
    #         final_type = best_type
    #         confidence = max(best_score, 0.9)
    #         reasoning = "regex+structural+keyword"
    #     # If structural+keyword strong enough, no LLM
    #     elif best_score >= 0.7:
    #         final_type = best_type
    #         confidence = best_score
    #         reasoning = "structural+keyword"
    #     else:
    #         # LLM fallback
    #         context = self._build_context(table, parsed, fmt)
    #         llm_result = self._ask_llm(context)
    #         llm_type = llm_result.get("type", "document")
    #         llm_conf = llm_result.get("confidence", 0.6)
    #         llm_reason = llm_result.get("reasoning", "llm")
    #
    #         llm_type = self.correct_misclassifications(llm_type, text)
    #         if llm_type not in DOC_TYPES:
    #             llm_type_lower = llm_type.lower().replace(" ", "_").replace("-", "_")
    #             for valid_type in DOC_TYPES:
    #                 if llm_type_lower in valid_type or valid_type in llm_type_lower:
    #                     llm_type = valid_type
    #                     break
    #             else:
    #                 llm_type = "document"
    #
    #         # Blend structural+keyword with LLM
    #         if best_score >= 0.4:
    #             final_type = best_type
    #             confidence = max(best_score, float(llm_conf) * 0.8)
    #             reasoning = f"ensemble(structural+keyword+llm:{llm_type})"
    #         else:
    #             final_type = llm_type
    #             confidence = float(llm_conf)
    #             reasoning = f"llm_fallback({llm_type})"
    #
    #     # Heuristic override if very strong
    #     if heuristic_type and heuristic_type != final_type:
    #         if final_type == "document" or confidence < 0.6:
    #             final_type = heuristic_type
    #             confidence = max(confidence, 0.75)
    #             reasoning = f"heuristic_override({heuristic_type})"
    #
    #     logger.info(f"✅ Final classified as: {final_type} ({confidence:.2f})")
    #
    #     return {
    #         "type": final_type,
    #         "confidence": float(confidence),
    #         "reasoning": reasoning,
    #         "matched_keywords": [],  # can be filled if you want explainability
    #         "rule_scores": {
    #             "structural": structural,
    #             "keyword": keyword,
    #             "regex": regex,
    #         },
    #         "ml_scores": {},  # reserved
    #     }

    # ---------- Helpers / Fallback ----------

    def _extract_headers(self, table: Dict) -> List[str]:
        if not table:
            return []
        for key in ['columns', 'headers', 'cols']:
            val = table.get(key)
            if isinstance(val, (list, tuple)):
                return [str(h) for h in val if h]
        rows = self._extract_rows(table)
        if rows and len(rows) > 0 and isinstance(rows[0], (list, tuple)):
            return [str(h) for h in rows[0] if h]
        return []

    def _extract_rows(self, table: Dict) -> List:
        if not table:
            return []
        for key in ['data', 'rows']:
            val = table.get(key)
            if isinstance(val, (list, tuple)):
                return val
        return []

    def _fallback_classify(self, table: Dict, parsed: Dict, fmt: str) -> Dict[str, Any]:
        logger.info("⚠️ Using fallback classification (no LLM / insufficient text)")
        filename = ""
        if parsed and isinstance(parsed.get("metadata"), dict):
            filename = parsed["metadata"].get("filename", "").lower()

        if filename:
            if "property-tax" in filename or "property_tax" in filename or "tax-bill" in filename or "secured-bill" in filename or "unsecured-bill" in filename:
                return {"type": "property_tax_bill", "confidence": 0.92, "reasoning": "filename_match"}
            if "invoice" in filename:
                return {"type": "invoice", "confidence": 0.75, "reasoning": "filename_match"}
            if "bank" in filename or "statement" in filename:
                return {"type": "bank_statement", "confidence": 0.75, "reasoning": "filename_match"}
            if "payroll" in filename or "paystub" in filename or "pay_stub" in filename:
                return {"type": "payroll", "confidence": 0.75, "reasoning": "filename_match"}
            if "expense" in filename:
                return {"type": "expense_report", "confidence": 0.75, "reasoning": "filename_match"}
            if "receipt" in filename:
                return {"type": "receipt", "confidence": 0.75, "reasoning": "filename_match"}
            if "po" in filename or "purchase" in filename:
                return {"type": "purchase_order", "confidence": 0.75, "reasoning": "filename_match"}

        if fmt in ["xlsx", "xls", "csv", "tsv"]:
            return {"type": "spreadsheet", "confidence": 0.4, "reasoning": "format_fallback"}
        if fmt in ["pdf", "docx", "doc"]:
            return {"type": "document", "confidence": 0.4, "reasoning": "format_fallback"}

        return {"type": "document", "confidence": 0.3, "reasoning": "unknown"}


# inside document_specific_analysis_node

# #from app.services.classifiers.llm_classifier import DocumentClassifier
#
# classifier = DocumentClassifier()
#
# def is_analyzer_eligible(doc_type: str, parsed: Dict[str, Any]) -> bool:
#     text = (_text_blob_from_parsed(parsed) or "").lower()
#
#     if doc_type == "invoice":
#         return any(k in text for k in ["invoice", "bill to", "amount due"])
#     if doc_type == "utility_bill":
#         return any(k in text for k in ["billing period", "service address", "kwh", "therms", "meter"])
#     if doc_type == "bank_statement":
#         return any(k in text for k in ["statement", "account number", "transaction"])
#     # add more as needed
#
#     return True  # default: allow
#
#
# def document_specific_analysis_node(state: Dict[str, Any]) -> Dict[str, Any]:
#     parsed = state.get("parsed", {})
#     table = state.get("primary_table")  # or however you pass it
#     fmt = state.get("format")
#
#     cls_result = classifier.classify(table=table, parsed=parsed, fmt=fmt)
#     doc_type = cls_result["type"]
#     confidence = cls_result["confidence"]
#
#     if not is_analyzer_eligible(doc_type, parsed):
#         logger.warning(f"🚧 Analyzer guardrail: rejecting analyzer for type={doc_type}, falling back to generic")
#         doc_type = "document"
#
#     analyzer = DOCUMENT_REGISTRY.get(doc_type) or DOCUMENT_REGISTRY.get("document")
#
#     analysis = analyzer.analyze(parsed=parsed, classification=cls_result)
#     state["doc_type"] = doc_type
#     state["classification"] = cls_result
#     state["specific_analysis"] = analysis
#     return state




# # app/services/classifiers/llm_classifier.py
#
# import logging
# from typing import Dict, Any, List, Optional
# import json
# import os
#
# logger = logging.getLogger(__name__)
#
#
# class DocumentClassifier:
#     """Use LLM to classify documents intelligently"""
#
#     DOCUMENT_TYPES = [
#         "invoice", "purchase_order", "credit_memo", "bank_statement", "inventory_report",
#         "receipt", "payroll", "expense_report", "sales_report",
#         "financial_statement", "tax_form", "contract", "resume",
#         "offer_letter", "letter", "memo", "packing_slip",
#         "insurance_claim", "medical_record", "transcript",
#         "spreadsheet", "document", "forecast_report"
#     ]
#
#     def __init__(self):
#         """Initialize with Ollama"""
#         try:
#             import ollama
#
#             # Get Ollama host from environment or use default
#             host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
#
#             self.client = ollama.Client(host=host)
#
#             # Get model name from environment or use default
#             self.model = os.getenv("OLLAMA_MODEL", "mistral")
#
#             # Test connection
#             self.client.list()
#
#             logger.info(f"✅ LLM classifier initialized with Ollama ({self.model})")
#         except Exception as e:
#             logger.error(f"Failed to initialize Ollama classifier: {e}")
#             self.client = None
#             self.model = None
#
#     def heuristic_classify(text: str) -> Optional[str]:
#         t = text.lower()
#
#         if "billing period" in t or "statement" in t or "minimum payment due" in t:
#             return "bank_statement"
#
#         if "invoice" in t or "bill to" in t:
#             return "invoice"
#
#         if "purchase order" in t or "po #" in t:
#             return "purchase_order"
#
#         if "w-2" in t or "1040" in t or "tax year" in t:
#             return "tax_form"
#
#         if "resume" in t or "experience" in t:
#             return "resume"
#
#         if "agreement" in t or "contract" in t:
#             return "contract"
#
#         return None
#
#     def correct_misclassifications(self, predicted: str, text: str) -> str:
#         t = text.lower()
#
#         if predicted == "credit_memo" and "statement" in t:
#             return "bank_statement"
#
#         if predicted == "credit_memo" and "invoice" in t:
#             return "invoice"
#
#         return predicted
#
#     def classify(self, table: Dict[str, Any] = None, parsed: Dict[str, Any] = None, fmt: str = None) -> Dict[str, Any]:
#         """Classify using LLM"""
#
#         if not self.client:
#             logger.warning("LLM classifier not available, falling back")
#             return self._fallback_classify(table, parsed, fmt)
#
#         try:
#             # Build context for LLM
#             context = self._build_context(table, parsed, fmt)
#
#             logger.info(f"📤 Sending to Ollama ({self.model})")
#
#             # 1. Heuristic
#             #h = self.heuristic_classify(self, context)
#             #if h: return {"type": h, "confidence": 0.99, "reasoning": "Heuristic match"}
#             # 2. LLM
#             result = self._ask_llm(context)
#             # 3. Post-validation
#             # llm_result["type"] = corrected
#
#             # Ask LLM to classify
#             #result = self._ask_llm(context)
#
#             logger.info(f"✅ LLM classified as: {result['type']} ({result['confidence']:.2f})")
#             return result
#
#         except Exception as e:
#             logger.error(f"❌ LLM classification failed: {e}", exc_info=True)
#             return self._fallback_classify(table, parsed, fmt)
#
#     def _build_context(self, table: Dict, parsed: Dict, fmt: str) -> str:
#         """Build context string for LLM"""
#         parts = []
#
#         # Filename
#         if parsed and isinstance(parsed.get("metadata"), dict):
#             filename = parsed["metadata"].get("filename", "")
#             if filename:
#                 parts.append(f"Filename: {filename}")
#
#         # Format
#         if fmt:
#             parts.append(f"Format: {fmt}")
#
#         # Headers
#         headers = self._extract_headers(table)
#         if headers:
#             headers_str = ", ".join(str(h) for h in headers[:20])
#             parts.append(f"Column Headers: {headers_str}")
#
#         # Sample data (first 5 rows)
#         rows = self._extract_rows(table)
#         if rows:
#             sample = []
#             for i, row in enumerate(rows[:5]):
#                 if isinstance(row, (list, tuple)):
#                     row_str = " | ".join(str(c)[:50] for c in row[:10] if c is not None)
#                     sample.append(f"Row {i + 1}: {row_str}")
#             if sample:
#                 parts.append("Sample Data:\n" + "\n".join(sample))
#
#         # Text content (for PDFs)
#         if parsed and parsed.get("text"):
#             text = str(parsed["text"])[:800]
#             parts.append(f"Text Content:\n{text}")
#
#         return "\n\n".join(parts)
#
#     def _ask_llm(self, context: str) -> Dict[str, Any]:
#         """Ask Ollama to classify"""
#
#         prompt = f"""
# You are a strict document classifier.
#
# RULES:
# - Respond ONLY with valid JSON.
# - No text outside JSON.
# - Choose ONLY from the allowed types.
# - If unsure, choose the closest type.
#
# Allowed types:
# invoice, purchase_order, credit_memo, bank_statement, inventory_report, receipt,
# payroll, expense_report, sales_report, financial_statement, tax_form, contract,
# resume, offer_letter, letter, memo, packing_slip, insurance_claim, medical_record,
# transcript, spreadsheet, document
#
# Document text:
# {context}
#
# Return JSON:
# {{
#   "type": "the_document_type",
#   "confidence": 0.95,
#   "reasoning": "Brief explanation"
# }}
# """
#
#         response = self.client.generate(model=self.model, prompt=prompt, options={"temperature": 0})
#
#         # response = self.client.generate(
#         #     model=self.model,
#         #     messages=[{
#         #         "role": "user",
#         #         "content": prompt
#         #     }],
#         #     options={
#         #         "temperature": 0,
#         #         "num_predict": 200
#         #     }
#         # )
#
#         #text = response['message']['content'].strip()
#         text = response["response"].strip()
#
#         #logger.info(f"📥 Ollama response: {text[:200]}...")
#
#         # Extract JSON (handle markdown code blocks)
#         if "```json" in text:
#             text = text.split("```json")[1].split("```")[0].strip()
#         elif "```" in text:
#             text = text.split("```")[1].split("```")[0].strip()
#
#         # Sometimes LLMs add extra text, extract JSON only
#         try:
#             # Try to find JSON object in response
#             start = text.find("{")
#             end = text.rfind("}") + 1
#             if end == 0:
#                 text += '}'
#
#             if start >= 0 and end > start:
#                 text = text[start:end]
#
#             result = json.loads(text)
#         except json.JSONDecodeError as e:
#             logger.error(f"Failed to parse Ollama response as JSON: {text}")
#             raise
#
#         doc_type = self.correct_misclassifications(result["type"], context)
#
#         # Validate type
#         #doc_type = result.get("type", "document")
#         if doc_type not in self.DOCUMENT_TYPES:
#             logger.warning(f"Unknown type from LLM: {doc_type}, defaulting to closest match")
#             # Try to find closest match
#             doc_type_lower = doc_type.lower().replace(" ", "_").replace("-", "_")
#             for valid_type in self.DOCUMENT_TYPES:
#                 if doc_type_lower in valid_type or valid_type in doc_type_lower:
#                     doc_type = valid_type
#                     break
#             else:
#                 doc_type = "document"
#
#         confidence = result.get("confidence", 0.8)
#         if isinstance(confidence, str):
#             try:
#                 confidence = float(confidence.replace("%", "")) / 100 if "%" in confidence else float(confidence)
#             except:
#                 confidence = 0.8
#
#         return {
#             "type": doc_type,
#             "confidence": float(confidence),
#             "reasoning": result.get("reasoning", "LLM classification"),
#             "matched_keywords": [],
#             "rule_scores": {},
#             "ml_scores": {}
#         }
#
#     def _extract_headers(self, table: Dict) -> List[str]:
#         """Extract headers safely"""
#         if not table:
#             return []
#
#         for key in ['columns', 'headers', 'cols']:
#             val = table.get(key)
#             if isinstance(val, (list, tuple)):
#                 return [str(h) for h in val if h]
#
#         # Try first row
#         rows = self._extract_rows(table)
#         if rows and len(rows) > 0 and isinstance(rows[0], (list, tuple)):
#             return [str(h) for h in rows[0] if h]
#
#         return []
#
#     def _extract_rows(self, table: Dict) -> List:
#         """Extract rows safely"""
#         if not table:
#             return []
#
#         for key in ['data', 'rows']:
#             val = table.get(key)
#             if isinstance(val, (list, tuple)):
#                 return val
#
#         return []
#
#     def _fallback_classify(self, table: Dict, parsed: Dict, fmt: str) -> Dict[str, Any]:
#         """Simple fallback when LLM not available"""
#
#         logger.info("⚠️ Using fallback classification (no LLM)")
#
#         # Quick filename check
#         filename = ""
#         if parsed and isinstance(parsed.get("metadata"), dict):
#             filename = parsed["metadata"].get("filename", "").lower()
#
#         # Filename-based heuristics
#         if filename:
#             if "sales" in filename or "revenue" in filename:
#                 if any(x in filename for x in ["product", "region", "territory", "store"]):
#                     return {"type": "sales_report", "confidence": 0.75, "reasoning": "filename_match"}
#
#             if "invoice" in filename:
#                 return {"type": "invoice", "confidence": 0.75, "reasoning": "filename_match"}
#
#             if "bank" in filename or "statement" in filename:
#                 return {"type": "bank_statement", "confidence": 0.75, "reasoning": "filename_match"}
#
#             if "payroll" in filename or "paystub" in filename or "pay_stub" in filename:
#                 return {"type": "payroll", "confidence": 0.75, "reasoning": "filename_match"}
#
#             if "expense" in filename:
#                 return {"type": "expense_report", "confidence": 0.75, "reasoning": "filename_match"}
#
#             if "receipt" in filename:
#                 return {"type": "receipt", "confidence": 0.75, "reasoning": "filename_match"}
#
#             if "po" in filename or "purchase" in filename:
#                 return {"type": "purchase_order", "confidence": 0.75, "reasoning": "filename_match"}
#
#         # Format-based fallback
#         if fmt in ["xlsx", "xls", "csv", "tsv"]:
#             return {"type": "spreadsheet", "confidence": 0.4, "reasoning": "format_fallback"}
#
#         if fmt in ["pdf", "docx", "doc"]:
#             return {"type": "document", "confidence": 0.4, "reasoning": "format_fallback"}
#
#         return {"type": "document", "confidence": 0.3, "reasoning": "unknown"}
#
# # import re
# # import logging
# # from typing import Dict, Any, List
# # import joblib
# #
# # logger = logging.getLogger(__name__)
# #
# # # Optional ML artifacts (joblib). If absent, classifier runs rule-only.
# # try:
# #     DOC_ML_MODEL = joblib.load("models/document_model.joblib")
# #     DOC_VECTORIZER = joblib.load("models/document_vectorizer.joblib")
# #     logger.info("Loaded document ML model and vectorizer")
# # except Exception:
# #     DOC_ML_MODEL = None
# #     DOC_VECTORIZER = None
# #     logger.info("Document ML model not found; running rule-only mode")
# #
# # # Focused keywords and regexes for the primary financial types and generic fallback
# # DOC_KEYWORDS = {
# #     "invoice": ["invoice", "invoice number", "amount due", "bill to", "due date"],
# #     "expense_report": ["expense report", "reimburs", "expense category", "employee expense"],
# #     "credit_memo": ["credit memo", "credit amount", "memo number"],
# #     "purchase_order": ["purchase order", "po number", "purchase order number"],
# #     "sales_report": ["sales report", "revenue", "units sold", "region sales"],
# #     "bank_statement": ["bank statement", "account balance", "transaction", "opening balance", "closing balance"],
# #     "receipt": ["receipt", "total paid", "payment method", "transaction id"],
# #     "payroll": ["payroll", "net pay", "gross pay", "salary"],
# #     "contract": ["agreement", "terms and conditions", "party", "whereas"],
# #     "letter": ["dear", "sincerely", "regards", "to whom it may concern"],
# #     "resume": ["education", "experience", "skills", "work history"],
# #     "report": ["executive summary", "introduction", "conclusion", "findings"],
# # }
# #
# # DOC_REGEX = {
# #     "invoice": re.compile(r"\bINV[-_ ]?\d{3,}\b", re.I),
# #     "purchase_order": re.compile(r"\bPO[-_ ]?\d{3,}\b", re.I),
# #     "credit_memo": re.compile(r"\bCM[-_ ]?\d{3,}\b", re.I),
# # }
# #
# #
# # def _safe_get_rows(table: Any) -> List:
# #     """
# #     Safely extract rows from a table structure.
# #     Handles both 'data' and 'rows' keys, and checks if it's actually a list.
# #     """
# #     if not isinstance(table, dict):
# #         return []
# #
# #     # Try 'data' first (normalized format)
# #     rows = table.get("data")
# #     if isinstance(rows, (list, tuple)) and rows:
# #         return rows
# #
# #     # Try 'rows' second
# #     rows = table.get("rows")
# #     if isinstance(rows, (list, tuple)) and rows:
# #         return rows
# #
# #     # Nothing found
# #     return []
# #
# #
# # def _text_blob_from_parsed(parsed: Dict[str, Any]) -> str:
# #     """
# #     Extract text blob from parsed document.
# #     Ultra-safe: handles any structure, never crashes.
# #     """
# #     parts: List[str] = []
# #
# #     try:
# #         # 1. Direct text
# #         text = parsed.get("text")
# #         if text and isinstance(text, str):
# #             parts.append(text[:5000])  # Limit to prevent huge blobs
# #
# #         # 2. Tables/sheets
# #         tables = parsed.get("tables") or parsed.get("sheets") or []
# #
# #         if isinstance(tables, (list, tuple)):
# #             for table in tables[:3]:  # Max 3 tables to prevent bloat
# #                 if not isinstance(table, dict):
# #                     continue
# #
# #                 # Get columns/headers
# #                 columns = table.get("columns") or table.get("headers") or table.get("cols") or []
# #                 if isinstance(columns, (list, tuple)):
# #                     col_text = " ".join(str(c)[:100] for c in columns[:20] if c is not None)
# #                     if col_text:
# #                         parts.append(col_text)
# #
# #                 # Get rows safely
# #                 rows = _safe_get_rows(table)
# #                 for row in rows[:5]:  # Max 5 rows per table
# #                     if isinstance(row, (list, tuple)):
# #                         row_text = " ".join(str(cell)[:50] for cell in row[:10] if cell is not None)
# #                         if row_text:
# #                             parts.append(row_text)
# #
# #         # 3. Email headers
# #         email_headers = parsed.get("email_headers")
# #         if email_headers and isinstance(email_headers, dict):
# #             header_text = " ".join(f"{k}:{v}" for k, v in email_headers.items() if k and v)
# #             if header_text:
# #                 parts.append(header_text)
# #
# #         # 4. Metadata (sometimes contains useful info)
# #         metadata = parsed.get("metadata")
# #         if metadata and isinstance(metadata, dict):
# #             # Extract title, subject, etc
# #             for key in ['title', 'subject', 'description']:
# #                 value = metadata.get(key)
# #                 if value and isinstance(value, str):
# #                     parts.append(value[:200])
# #
# #     except Exception as e:
# #         logger.warning(f"⚠️ Text extraction had issues: {e}")
# #         # Don't fail completely - return what we have
# #
# #     # Join and normalize
# #     result = "\n".join(parts).lower()
# #
# #     # Log for debugging
# #     if not result or len(result) < 10:
# #         logger.warning("⚠️ Very little text extracted for classification")
# #     else:
# #         logger.debug(f"📝 Extracted {len(result)} chars for classification")
# #
# #     return result
# #
# #
# # class DocumentClassifier:
# #     """
# #     Classifies parsed documents (PDF, DOCX, PPTX, OCR'd images) into focused types.
# #     Input: parsed canonical dict with keys like 'text', 'tables', 'email_headers'.
# #     Output: { type, confidence, reasoning, matched_keywords, rule_scores, ml_scores }.
# #
# #     Ultra-safe: will never crash on bad data structures.
# #     """
# #
# #     def __init__(self, ml_model=DOC_ML_MODEL, vectorizer=DOC_VECTORIZER, rule_weight: float = 0.7):
# #         self.ml_model = ml_model
# #         self.vectorizer = vectorizer
# #         self.rule_weight = float(rule_weight)
# #
# #     def _rule_scores(self, text: str) -> Dict[str, float]:
# #         """Calculate rule-based scores from keywords and regex"""
# #         scores: Dict[str, float] = {}
# #
# #         for dtype, kws in DOC_KEYWORDS.items():
# #             # Count keyword matches
# #             count = sum(1 for k in kws if k in text)
# #
# #             # Check regex match
# #             regex_match = 0
# #             if DOC_REGEX.get(dtype):
# #                 try:
# #                     if DOC_REGEX[dtype].search(text):
# #                         regex_match = 1
# #                 except Exception:
# #                     pass
# #
# #             # Calculate score
# #             scores[dtype] = min(1.0, (count * 0.35) + (regex_match * 0.9))
# #
# #         return scores
# #
# #     def _ml_scores(self, text: str) -> Dict[str, float]:
# #         """Calculate ML-based scores if model available"""
# #         if not self.ml_model or not self.vectorizer:
# #             return {}
# #
# #         try:
# #             vec = self.vectorizer.transform([text])
# #             probs = self.ml_model.predict_proba(vec)[0]
# #             classes = list(self.ml_model.classes_)
# #             return dict(zip(classes, probs.tolist()))
# #         except Exception as e:
# #             logger.debug(f"ML prediction failed: {e}")
# #             return {}
# #
# #     def _ensemble(self, rule: Dict[str, float], ml: Dict[str, float]) -> Dict[str, float]:
# #         """Combine rule and ML scores"""
# #         final: Dict[str, float] = {}
# #         all_types = set(list(rule.keys()) + list(ml.keys()))
# #
# #         for dtype in all_types:
# #             r = rule.get(dtype, 0.0)
# #             m = ml.get(dtype, 0.0)
# #             final[dtype] = (self.rule_weight * r) + ((1.0 - self.rule_weight) * m)
# #
# #         return final
# #
# #     def classify(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
# #         """
# #         Classify a parsed document.
# #
# #         Args:
# #             parsed: Dict with 'text', 'tables', 'email_headers', etc.
# #
# #         Returns:
# #             Classification result with type, confidence, and details
# #         """
# #         # Validate input
# #         if not isinstance(parsed, dict):
# #             logger.error(f"❌ Invalid parsed type: {type(parsed)}")
# #             return {
# #                 "type": "document",
# #                 "confidence": 0.1,
# #                 "reasoning": "error",
# #                 "matched_keywords": [],
# #                 "rule_scores": {},
# #                 "ml_scores": {},
# #             }
# #
# #         # Extract text safely
# #         try:
# #             text = _text_blob_from_parsed(parsed)
# #         except Exception as e:
# #             logger.error(f"❌ Text extraction failed: {e}")
# #             return {
# #                 "type": "document",
# #                 "confidence": 0.1,
# #                 "reasoning": "extraction_error",
# #                 "matched_keywords": [],
# #                 "rule_scores": {},
# #                 "ml_scores": {},
# #             }
# #
# #         # Need minimum text to classify
# #         if not text or len(text) < 10:
# #             logger.warning("⚠️ Insufficient text for classification")
# #             return {
# #                 "type": "document",
# #                 "confidence": 0.2,
# #                 "reasoning": "insufficient_text",
# #                 "matched_keywords": [],
# #                 "rule_scores": {},
# #                 "ml_scores": {},
# #             }
# #
# #         # Calculate scores
# #         rule = self._rule_scores(text)
# #         ml = self._ml_scores(text)
# #         final = self._ensemble(rule, ml)
# #
# #         # Find best match
# #         if final:
# #             best_type, best_score = max(final.items(), key=lambda x: x[1])
# #         else:
# #             best_type, best_score = "document", 0.0
# #
# #         # Extract matched keywords for explainability
# #         matched = []
# #         for dtype, kws in DOC_KEYWORDS.items():
# #             found = [k for k in kws if k in text]
# #             if found:
# #                 matched.append({"type": dtype, "keywords": found})
# #
# #         logger.info(f"✅ Classified as: {best_type} (confidence: {best_score:.2f})")
# #
# #         return {
# #             "type": best_type,
# #             "confidence": float(best_score),
# #             "reasoning": "ensemble(rule+ml)" if ml else "rule_only",
# #             "matched_keywords": matched,
# #             "rule_scores": rule,
# #             "ml_scores": ml,
# #         }
