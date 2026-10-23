"""
Document Research Agent — Question Reasoner + Deterministic Pipeline

Flow:
  1. QuestionReasoner  — LLM understands intent, extracts entities, generates search queries
  2. DocumentFilter    — narrows doc scope using reasoner hints (date, doc_type, keywords)
  3. MultiSearch       — runs all reasoner-generated queries, merges results
  4. AskDocument       — deep-reads top unique docs
  5. Synthesize        — LLM writes final answer from gathered context
"""
import json
import logging
import os
import re

from app.utils.text_match import has_term, any_term
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import ollama
from app.core import llm
import yaml

from app.core.database import SessionLocal
from app.config.model_factory import EmbeddingModelFactory
from app.core import config as app_config
from app.core.config import NUM_CTX
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

# ── Load research hints from YAML config ─────────────────────────────────────
_HINTS_PATH = Path(__file__).parent / "research_hints.yaml"

def _load_hints() -> Dict:
    try:
        with open(_HINTS_PATH, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load research_hints.yaml: {e}")
        return {"doc_types": {}, "field_synonyms": {}, "months": {}, "scoring": {}}

_HINTS: Dict = _load_hints()

def _doc_type_keywords() -> Dict[str, List[str]]:
    return {k: [str(kw) for kw in v.get("keywords", [])] for k, v in _HINTS.get("doc_types", {}).items()}

def _scoring() -> Dict[str, int]:
    defaults = {"date_month_and_year": 3, "date_partial": 2, "doc_type_match": 2,
                "field_in_filename": 1, "named_subject_match": 30,
                "named_subject_partial": 10, "min_score_to_include": 1}
    return {**defaults, **_HINTS.get("scoring", {})}

def _month_aliases() -> Dict[str, Dict]:
    """Returns {full_name: {abbr: str, num: str}} from hints file."""
    return _HINTS.get("months", {})

_embedding_model = None
_example_embeddings: Optional[np.ndarray] = None
_raw_examples: List[Dict] = []

# ── Research examples — dynamic few-shot library ──────────────────────────────
_EXAMPLES_PATH = Path(__file__).parent / "research_examples.yaml"


def _load_raw_examples() -> List[Dict]:
    try:
        with open(_EXAMPLES_PATH, "r") as f:
            return yaml.safe_load(f) or []
    except Exception as e:
        logger.warning(f"Failed to load research_examples.yaml: {e}")
        return []


_raw_examples = _load_raw_examples()


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModelFactory.get_model()
    return _embedding_model


def _get_example_embeddings() -> np.ndarray:
    global _example_embeddings
    if _example_embeddings is None and _raw_examples:
        try:
            model = _get_embedding_model()
            questions = [ex["q"] for ex in _raw_examples]
            _example_embeddings = model.encode(questions, convert_to_numpy=True, normalize_embeddings=True)
            logger.info(f"Embedded {len(questions)} few-shot examples")
        except Exception as e:
            logger.warning(f"Failed to embed examples: {e}")
            _example_embeddings = np.zeros((0, 768), dtype=np.float32)
    return _example_embeddings if _example_embeddings is not None else np.zeros((0, 768), dtype=np.float32)


def _select_examples(question: str, n: int = 8) -> str:
    """Return the top-N most similar examples as a formatted string for the prompt."""
    if not _raw_examples:
        return ""
    embs = _get_example_embeddings()
    if embs.shape[0] == 0:
        return ""
    try:
        model = _get_embedding_model()
        q_emb = model.encode([question], convert_to_numpy=True, normalize_embeddings=True)[0]
        scores = embs @ q_emb  # cosine similarity (embeddings are L2-normalized)
        top_idx = np.argsort(scores)[::-1][:n]
        lines = []
        for i in top_idx:
            ex = _raw_examples[i]
            lines.append(f'Q: "{ex["q"]}"\n{ex["a"]}')
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning(f"Few-shot selection failed: {e}")
        return ""


def _active_model() -> str:
    return app_config.model


_CITATION_NOISE_RE = re.compile(
    r'\s*##[^|\n]*\|\s*Document\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

def _decode_llm_text(text: str) -> str:
    """Decode literal \\uXXXX escape sequences and strip internal chunk-header noise."""
    if not text:
        return text
    # Strip internal "## Document | Document <uuid>" markers the LLM echoes back
    text = _CITATION_NOISE_RE.sub('', text)
    if '\\u' not in text:
        return text
    try:
        return re.sub(
            r'\\u([0-9a-fA-F]{4})',
            lambda m: chr(int(m.group(1), 16)),
            text
        )
    except Exception:
        return text


# Warm up example embeddings at startup so first query doesn't pay the cost
import threading as _threading

def _warmup_embeddings():
    try:
        _get_example_embeddings()
    except Exception:
        pass

_threading.Thread(target=_warmup_embeddings, daemon=True).start()


# ── Question Reasoner ─────────────────────────────────────────────────────────

REASONER_PROMPT_TEMPLATE = """You are a document question analyst. Analyze the user's question and return a JSON plan.

Intents:
- field_extract   : asking for a specific field/value from a document (e.g. gross pay, balance, date)
- aggregate       : asking for a sum/total/count across multiple documents or time periods
- compare         : comparing values between two or more documents, time periods, or entities
- multi_period    : aggregate or compare across an explicit date range (e.g. Jan–Jun, Q1 vs Q2 vs Q3)
- trend           : asking about change over time (highest, lowest, increasing, decreasing, pattern)
- rank            : ordering or top-N without a time component (e.g. top 5 vendors, largest deduction)
- list            : enumerate all instances of something (e.g. list all deductions, all transactions over $1000)
- summarize       : condense or give an overview of a document or set of documents
- info_retrieve   : open-ended factual question, clause lookup, definition, explanation
- verify          : check if values match, totals add up, or a condition holds (e.g. does invoice match PO?)
- calculate       : derive a new value from existing fields (e.g. effective tax rate, % of gross pay)
- exists_check    : ask whether something is present (e.g. is there a penalty clause? do I have docs from 2024?)
- anomaly         : find unusual, unexpected, or out-of-range values (e.g. any charges look wrong?)
- forecast        : estimate or project future values based on historical data
- cross_doc       : requires combining facts from multiple different document types
- create_client      : user wants to create/add a new client or contact (e.g. "create a client", "add a new client", "register client John Dave")
- create_policy      : user wants to create/add a new insurance policy for a client (e.g. "create auto insurance", "add home policy", "issue a life policy for John")
- create_claim       : user wants to file/create a new insurance claim (e.g. "file a claim", "create a claim for policy X", "add auto claim")
- create_quote       : user wants to generate/create an insurance quote (e.g. "create a quote", "get a quote for home insurance", "add a quote for John")
- create_matter      : user wants to create a legal matter (e.g. "create a matter", "open a litigation", "add contract matter")
- create_clm         : user wants to add a contract to the CLM pipeline (e.g. "add contract to CLM", "create CLM entry", "track this contract")
- create_approval    : user wants to create an approval request for a document (e.g. "request approval", "create approval for this doc", "send for review")
- create_immigration : user wants to add an immigration document record (e.g. "add visa record", "create immigration entry", "track H1B")
- create_note        : user wants to create a personal note (e.g. "add a note", "create a note about X", "save this as a note")
- create_task        : user wants to create a planner task (e.g. "add a task", "create a reminder", "schedule a task for Friday")
- update_client      : user wants to update/edit client info (e.g. "update John Dave's phone", "change client address", "edit client email")
- update_policy      : user wants to update an existing policy (e.g. "update policy status to active", "change expiry date", "update carrier for John's policy")
- update_claim       : user wants to update a claim (e.g. "update claim status to settled", "change claim adjuster", "set claim value to 5000")
- update_quote       : user wants to update a quote (e.g. "update quote stage", "change quote LOB to home", "edit quote notes")
- update_matter      : user wants to update a legal matter (e.g. "move matter to discovery stage", "update matter priority to high", "change counsel")
- update_clm         : user wants to update a contract in CLM (e.g. "move contract to review", "update CLM stage", "change counterparty")
- update_immigration : user wants to update an immigration record (e.g. "update visa expiry", "change immigration status", "extend H1B until 2027")
- update_note        : user wants to update an existing note (e.g. "edit note", "update my note about John", "change note content")
- update_task        : user wants to update a planner task (e.g. "mark task complete", "update task priority", "change due date")
- delete_request     : user wants to delete, remove, or permanently erase any record (e.g. "delete client", "remove policy", "erase claim", "drop this record")

Return ONLY valid JSON, no markdown, no explanation:
{
  "intent": "<one of the intents above>",
  "field": "<the specific field or concept being asked about, or null>",
  "field_synonyms": ["<alternative names for the field in documents>"],
  "doc_type_hint": "<pay_stub|payroll_report|bank_statement|credit_card_statement|invoice|purchase_order|expense_report|receipt|financial_statement|credit_memo|sales_report|contract|annual_report|quarterly_report|proxy|tax_form|w2|tax_return|property_tax_bill|government_form|insurance|declarations_page|insurance_policy|insurance_claim|medical|medical_record|real_estate|mortgage_document|mortgage_statement|closing_disclosure|loan_document|payoff_statement|legal|hr_document|immigration|uscis_receipt|utility_bill|investment|inventory_report|employee_roster|order_history|transaction_log|asset_register|customer_list|product_catalog|forecast_report|dataset|movie_dataset|music_dataset|book_dataset|product_dataset|ratings_dataset|location_dataset|weather_dataset|sports_dataset|health_dataset|education_dataset|real_estate_dataset|stock_dataset|census_dataset|scientific_dataset|survey_dataset|event_dataset|spreadsheet|image|birth_certificate|passport|drivers_license|social_security_card|marriage_certificate|death_certificate|vaccination_record|naturalization_certificate|general>",
  "date_hint": {
    "type": "<none|specific_month|date_range|year|relative>",
    "months": ["<month names like May, January>"],
    "years": ["<4-digit years>"],
    "range_description": "<human description if range, else null>"
  },
  "needs_aggregation": true or false,
  "aggregation_op": "<sum|count|max|min|avg|list|compare|rank|verify|calculate|exists|anomaly|forecast|null>",
  "extracted_entities": {
    "first_name": "<extracted first name or null>",
    "last_name": "<extracted last name or null>",
    "phone": "<extracted phone/mobile number or null>",
    "address": "<extracted street address or null>",
    "city": "<extracted city or null>",
    "state": "<extracted state or null>",
    "zip": "<extracted zip code or null>",
    "email": "<extracted email or null>",
    "line_of_business": "<auto|home|life|commercial|health|umbrella|workers_comp|property|liability or null>",
    "carrier_name": "<extracted carrier/insurer name or null>",
    "policy_number": "<extracted policy number or null>",
    "premium_amount": "<extracted premium amount as number or null>",
    "effective_date": "<extracted effective date as YYYY-MM-DD or null>",
    "expiry_date": "<extracted expiry date as YYYY-MM-DD or null>",
    "claim_type": "<auto|property|liability|health|workers_comp|general or null>",
    "claimant_name": "<extracted claimant name or null>",
    "incident_date": "<extracted incident date as YYYY-MM-DD or null>",
    "estimated_value": "<extracted claim/contract value as number or null>",
    "matter_type": "<contract|litigation|compliance|ip|employment|general or null>",
    "matter_title": "<extracted matter/case title or null>",
    "assigned_counsel": "<extracted attorney/counsel name or null>",
    "priority": "<low|normal|high|urgent or null>",
    "deadline": "<extracted deadline date as YYYY-MM-DD or null>",
    "counterparty": "<extracted counterparty/opposing party name or null>",
    "doc_id": "<extracted document ID or null>",
    "approvers": "<comma-separated list of approver names or null>",
    "visa_class": "<extracted visa type e.g. H1B, F1, L1 or null>",
    "beneficiary_name": "<extracted beneficiary name or null>",
    "employer_sponsor": "<extracted employer/sponsor name or null>",
    "valid_until": "<extracted visa/document expiry date as YYYY-MM-DD or null>",
    "note_title": "<extracted note title or null>",
    "note_content": "<extracted note content or null>",
    "task_title": "<extracted task/reminder title or null>",
    "due_date": "<extracted due/reminder date as YYYY-MM-DD or null>"
  },
  "named_subject": "<full name of the person, company, or entity the question is specifically about — e.g. 'Vijay Bomma', 'Acme Corp'. null if the question is about documents in general>",
  "search_queries": [
    "<primary search query using field names as they appear in documents>",
    "<alternative phrasing using synonyms>",
    "<third variation focusing on different angle>"
  ],
  "reasoning": "<one sentence explaining what this question is really asking>"
}

IMPORTANT: search_queries must use FIELD NAMES that appear in documents (e.g. "gross pay", "net pay", "federal tax"), NOT rephrasings of the user's question.
IMPORTANT: If the question is about a specific person or entity (e.g. "Vijay Bomma documents"), set named_subject to that name. The system will use it to filter out irrelevant documents.

Examples:

{examples}

Now analyze this question:
Q: "{question}"
"""


# Keyword-based doc_type overrides applied AFTER the LLM reasons.
# If the user's question contains these words, we force the doc_type_hint
# regardless of what the LLM returned — because small models often miss this.
# ── Question-keyword → doc_type_hint overrides ───────────────────────────────
# Applied AFTER the LLM reasoner so small-model errors can't lose the doc type.
# Order matters: first match wins. More specific phrases go first.
_DOC_TYPE_KEYWORD_OVERRIDES: List[tuple] = [
    # ── HR / People ──────────────────────────────────────────────────────────
    ({"resume", "cv", "curriculum vitae", "curriculum-vitae", "linkedin profile",
      "candidate profile", "job application", "applicant"}, "hr_document"),
    ({"offer letter", "offer-letter", "employment letter", "onboarding",
      "employee handbook", "performance review", "termination letter",
      "severance", "job description", "jd "}, "hr_document"),
    ({"employee roster", "employee list", "headcount", "staff list",
      "org chart", "org-chart", "directory"}, "employee_roster"),

    # ── Payroll / Pay ─────────────────────────────────────────────────────────
    ({"pay stub", "paystub", "pay-stub", "paycheck", "payslip",
      "earnings statement", "wage statement", "salary slip"}, "pay_stub"),
    ({"payroll report", "payroll summary", "payroll register"}, "payroll_report"),

    # ── Banking / Finance ────────────────────────────────────────────────────
    ({"bank statement", "bank-statement", "account statement",
      "checking statement", "savings statement"}, "bank_statement"),
    ({"credit card statement", "credit-card statement", "card statement",
      "visa statement", "mastercard statement", "amex statement"}, "credit_card_statement"),
    ({"invoice", "billing statement", "bill from", "vendor bill",
      "accounts payable", "accounts receivable"}, "invoice"),
    ({"purchase order", "po number", "p.o. number", "procurement order"}, "purchase_order"),
    ({"expense report", "expense-report", "expense claim",
      "reimbursement", "travel expense", "business expense"}, "expense_report"),
    ({"financial statement", "income statement", "profit and loss",
      "p&l", "balance sheet", "cash flow statement"}, "financial_statement"),
    ({"credit memo", "credit note", "refund memo"}, "credit_memo"),
    ({"sales report", "sales summary", "revenue report"}, "sales_report"),
    ({"receipt", "payment receipt", "cash receipt", "store receipt"}, "receipt"),
    ({"transaction log", "transaction history", "transaction record"}, "transaction_log"),
    ({"inventory report", "stock report", "inventory count"}, "inventory_report"),
    ({"order history", "order summary", "purchase history"}, "order_history"),
    ({"asset register", "asset list", "fixed assets", "asset inventory"}, "asset_register"),
    ({"customer list", "customer directory", "client list"}, "customer_list"),
    ({"product catalog", "product list", "price list", "catalogue"}, "product_catalog"),
    ({"forecast report", "revenue forecast", "budget forecast",
      "projection report"}, "forecast_report"),

    # ── Corporate / Investor Relations ───────────────────────────────────────
    ({"annual report", "10-k", "10k", "form 10-k", "yearly report"}, "annual_report"),
    ({"quarterly report", "10-q", "10q", "quarterly earnings",
      "q1 report", "q2 report", "q3 report", "q4 report"}, "quarterly_report"),
    ({"proxy statement", "proxy filing", "def 14a", "proxy", "lexicon proxy"}, "proxy"),
    ({"investment statement", "portfolio statement", "brokerage statement",
      "401k statement", "retirement statement", "ira statement"}, "investment"),

    # ── Insurance ────────────────────────────────────────────────────────────
    ({"declarations page", "declaration page", "dec page", "dec sheet",
      "policy declarations", "geico", "geico policy", "declaration pg"}, "declarations_page"),
    ({"insurance policy", "policy document", "policy schedule",
      "certificate of insurance", "coi"}, "insurance_policy"),
    ({"insurance claim", "claim form", "loss notice", "claim report",
      "fnol", "first notice of loss"}, "insurance_claim"),
    ({"insurance", "coverage", "insured", "policyholder"}, "insurance"),

    # ── Legal / CLM ──────────────────────────────────────────────────────────
    ({"contract", "agreement", "msa", "master service agreement",
      "sow", "statement of work", "nda", "non-disclosure",
      "service agreement", "licensing agreement", "lease agreement"}, "contract"),
    ({"legal document", "legal filing", "court filing", "motion", "brief",
      "complaint", "affidavit", "subpoena", "deposition"}, "legal"),

    # ── Tax ──────────────────────────────────────────────────────────────────
    ({"property tax", "property tax bill", "property tax payment", "secured tax bill",
      "unsecured tax bill", "county tax bill", "parcel tax", "tax installment",
      "first installment", "second installment", "property tax due"}, "property_tax_bill"),
    ({"w2", "w-2", "w 2"}, "w2"),
    ({"tax return", "1040", "1040ez", "tax filing"}, "tax_return"),
    ({"tax form", "1099", "1099-misc", "1099-nec", "k-1",
      "tax document", "tax statement"}, "tax_form"),
    ({"government form", "government document", "federal form",
      "state form", "ssa", "social security"}, "government_form"),
    ({"birth certificate", "certificate of birth", "date of birth", "born on",
      "birthday", "dob", "birth record", "vital record",
      "born", "place of birth", "birth place", "birthplace"}, "birth_certificate"),
    ({"passport", "travel document", "passport number", "us passport",
      "passport expiry", "passport valid"}, "passport"),
    ({"driver license", "drivers license", "driver's license", "dl number",
      "license number", "dmv", "motor vehicle"}, "drivers_license"),
    ({"social security card", "social security number", "ssn card"}, "social_security_card"),
    ({"marriage certificate", "marriage license", "wedding certificate",
      "marriage record", "married on", "date of marriage"}, "marriage_certificate"),
    ({"death certificate", "death record", "date of death", "cause of death",
      "decedent"}, "death_certificate"),
    ({"vaccination record", "vaccination card", "immunization record",
      "covid vaccine", "vaccine card", "cdc card", "shot record"}, "vaccination_record"),
    ({"naturalization certificate", "citizenship certificate",
      "naturalized citizen"}, "naturalization_certificate"),

    # ── Medical / Health ─────────────────────────────────────────────────────
    ({"medical record", "patient record", "clinical note",
      "discharge summary", "visit notes", "soap notes",
      "emr", "ehr", "health record"}, "medical_record"),
    ({"medical document", "medical report", "lab report",
      "radiology", "prescription", "rx", "explanation of benefits",
      "eob", "medical bill", "hospital bill"}, "medical"),

    # ── Real Estate ──────────────────────────────────────────────────────────
    ({"closing disclosure", "hud-1", "hud 1", "settlement statement",
      "closing statement"}, "closing_disclosure"),
    ({"mortgage statement", "mortgage account", "escrow statement"}, "mortgage_statement"),
    ({"mortgage", "deed of trust", "home loan", "refinance"}, "mortgage_document"),
    ({"loan estimate", "good faith estimate", "gfe"}, "loan_estimate"),
    ({"payoff statement", "mortgage payoff", "loan payoff"}, "payoff_statement"),
    ({"loan document", "loan agreement", "promissory note", "note payable"}, "loan_document"),
    ({"real estate", "property document", "title", "deed", "escrow",
      "home purchase", "home sale", "listing"}, "real_estate"),

    # ── Immigration ──────────────────────────────────────────────────────────
    ({"uscis receipt", "receipt notice", "i-797", "i797"}, "uscis_receipt"),
    ({"immigration receipt", "case receipt", "approval notice"}, "immigration_receipt"),
    ({"visa", "i-94", "i-20", "h1b", "h-1b", "h1-b", "l1", "l-1",
      "f1", "f-1", "opt", "stem opt", "green card", "uscis",
      "immigration", "work permit", "ead"}, "immigration"),

    # ── Utility ──────────────────────────────────────────────────────────────
    ({"utility bill", "electric bill", "gas bill", "water bill",
      "internet bill", "phone bill", "cable bill"}, "utility_bill"),

    # ── Datasets ─────────────────────────────────────────────────────────────
    ({"movie dataset", "film dataset", "movie data"}, "movie_dataset"),
    ({"music dataset", "song dataset", "music data"}, "music_dataset"),
    ({"book dataset", "book data", "library dataset"}, "book_dataset"),
    ({"product dataset", "product data", "ecommerce dataset"}, "product_dataset"),
    ({"ratings dataset", "review dataset", "rating data"}, "ratings_dataset"),
    ({"location dataset", "geo dataset", "places dataset"}, "location_dataset"),
    ({"weather dataset", "weather data", "climate dataset"}, "weather_dataset"),
    ({"sports dataset", "sports data", "athlete dataset"}, "sports_dataset"),
    ({"health dataset", "healthcare dataset", "clinical dataset"}, "health_dataset"),
    ({"education dataset", "school dataset", "academic dataset"}, "education_dataset"),
    ({"real estate dataset", "property dataset", "housing dataset"}, "real_estate_dataset"),
    ({"stock dataset", "stock data", "market data", "price data"}, "stock_dataset"),
    ({"census dataset", "census data", "demographic dataset"}, "census_dataset"),
    ({"scientific dataset", "research dataset", "experiment data"}, "scientific_dataset"),
    ({"survey dataset", "survey data", "questionnaire data"}, "survey_dataset"),
    ({"event dataset", "event log", "clickstream", "analytics data"}, "event_dataset"),
    ({"spreadsheet", "excel file", "xlsx", "google sheet"}, "spreadsheet"),

    # ── Education ────────────────────────────────────────────────────────────
    ({"transcript", "academic transcript", "grade report", "gpa",
      "college transcript", "university transcript", "school record"}, "transcript"),
    ({"diploma", "degree", "certificate of completion", "graduation",
      "education certificate", "academic certificate"}, "education_certificate"),
    ({"student record", "enrollment record", "student file"}, "student_record"),
    ({"recommendation letter", "letter of recommendation", "reference letter"}, "recommendation_letter"),

    # ── Logistics / Supply Chain ─────────────────────────────────────────────
    ({"packing slip", "packing list", "shipment packing"}, "packing_slip"),
    ({"bill of lading", "bol", "shipping bill", "freight bill of lading"}, "bill_of_lading"),
    ({"shipping label", "shipping tag", "package label"}, "shipping_label"),
    ({"delivery note", "delivery receipt", "proof of delivery", "pod"}, "delivery_note"),
    ({"freight invoice", "freight bill", "shipping invoice",
      "carrier invoice", "shipping charge"}, "freight_invoice"),
    ({"shipping", "shipment", "tracking number", "carrier",
      "dispatch", "logistics"}, "packing_slip"),

    # ── Operations / Manufacturing ───────────────────────────────────────────
    ({"work order", "work-order", "job order", "service order",
      "maintenance order", "repair order"}, "work_order"),
    ({"maintenance log", "maintenance record", "service log",
      "maintenance report"}, "maintenance_log"),
    ({"safety data sheet", "sds", "msds", "material safety",
      "hazmat", "chemical safety"}, "safety_document"),
    ({"quality report", "qc report", "qa report", "inspection report",
      "nonconformance", "defect report"}, "quality_report"),
    ({"manufacturing spec", "bill of materials", "bom",
      "assembly instructions", "production spec"}, "manufacturing_spec"),

    # ── Corporate / Compliance ───────────────────────────────────────────────
    ({"audit report", "audit finding", "internal audit",
      "external audit", "sox audit"}, "audit_report"),
    ({"compliance report", "compliance document", "regulatory report"}, "compliance_report"),
    ({"risk assessment", "risk report", "risk register"}, "risk_assessment"),
    ({"policy document", "company policy", "hr policy",
      "it policy", "security policy"}, "policy_document"),
    ({"sec filing", "10-k", "10-q", "8-k", "securities filing"}, "sec_filing"),
]

# ── Domain groups — every doc_type tagged with its group ─────────────────────
# When doc_type_hint is set, documents in a DIFFERENT group score 0 (hard excluded).
_DOMAIN_GROUPS: Dict[str, str] = {
    # Payroll
    "pay_stub":               "payroll",
    "payroll_report":         "payroll",
    # Banking / Finance
    "bank_statement":         "finance",
    "credit_card_statement":  "finance",
    "invoice":                "finance",
    "purchase_order":         "finance",
    "expense_report":         "finance",
    "financial_statement":    "finance",
    "credit_memo":            "finance",
    "sales_report":           "finance",
    "receipt":                "finance",
    "transaction_log":        "finance",
    "order_history":          "finance",
    "asset_register":         "finance",
    "customer_list":          "finance",
    "product_catalog":        "finance",
    "inventory_report":       "finance",
    "forecast_report":        "finance",
    # Corporate / IR
    "annual_report":          "corporate",
    "quarterly_report":       "corporate",
    "proxy":                  "corporate",
    "investment":             "corporate",
    # Tax
    "tax_form":               "tax",
    "resale_certificate":     "tax",
    "sales_tax_return":       "tax",
    "sales_ledger":           "tax",
    "w2":                     "tax",
    "tax_return":             "tax",
    "government_form":        "tax",
    "birth_certificate":          "personal_id",
    "passport":                   "personal_id",
    "drivers_license":            "personal_id",
    "social_security_card":       "personal_id",
    "marriage_certificate":       "personal_id",
    "death_certificate":          "personal_id",
    "naturalization_certificate": "personal_id",
    "vaccination_record":         "personal_id",
    "property_tax_bill":          "tax",
    # HR
    "hr_document":            "hr",
    "employee_roster":        "hr",
    # Insurance
    "insurance_policy":       "insurance",
    "declarations_page":      "insurance",
    "insurance_claim":        "insurance",
    "insurance":              "insurance",
    # Legal / CLM
    "contract":               "legal",
    "legal":                  "legal",
    # Medical
    "medical_record":         "medical",
    "medical":                "medical",
    # Real Estate
    "mortgage_document":      "realestate",
    "mortgage_statement":     "realestate",
    "closing_disclosure":     "realestate",
    "loan_document":          "realestate",
    "loan_estimate":          "realestate",
    "payoff_statement":       "realestate",
    "real_estate":            "realestate",
    # Immigration
    "immigration":            "immigration",
    "uscis_receipt":          "immigration",
    "immigration_receipt":    "immigration",
    # Utility
    "utility_bill":           "utility",
    # Datasets (all datasets belong to the same broad group so they don't
    # bleed into real documents, but they also don't block each other)
    "dataset":                "dataset",
    "movie_dataset":          "dataset",
    "music_dataset":          "dataset",
    "book_dataset":           "dataset",
    "product_dataset":        "dataset",
    "ratings_dataset":        "dataset",
    "location_dataset":       "dataset",
    "weather_dataset":        "dataset",
    "sports_dataset":         "dataset",
    "health_dataset":         "dataset",
    "education_dataset":      "dataset",
    "real_estate_dataset":    "dataset",
    "stock_dataset":          "dataset",
    "census_dataset":         "dataset",
    "scientific_dataset":     "dataset",
    "survey_dataset":         "dataset",
    "event_dataset":          "dataset",
    # Other
    "spreadsheet":            "spreadsheet",
    "data_table":             "dataset",     # CSV/Excel files ingested as data_table
    "image":                  "image",
    # Communications / misc
    "transcript":             "communication",
    "email":                  "communication",
    "letter":                 "communication",
    "newsletter":             "communication",
    "report":                 "communication",
    "memo":                   "communication",
    "meeting_minutes":        "communication",
    "recommendation_letter":  "communication",
    "public_notice":          "communication",
    "project_plan":           "communication",
    "business_proposal":      "communication",
    "service_report":         "communication",
    "timesheet":              "communication",

    # Finance (additional)
    "credit_card_statement":  "finance",
    "bank_statement":         "finance",
    "financial_statement":    "finance",
    "credit_memo":            "finance",
    "expense_report":         "finance",
    "invoice":                "finance",
    "purchase_order":         "finance",
    "receipt":                "finance",
    "payment_confirmation":   "finance",
    "transaction_record":     "finance",
    "payoff_quote":           "finance",

    # Payroll (additional)
    "payroll":                "payroll",
    "pay_stub":               "payroll",
    "offer_letter":           "payroll",
    "performance_review":     "payroll",
    "employment_contract":    "payroll",
    "resume":                 "payroll",

    # Insurance (additional)
    "auto_insurance_claim":   "insurance",
    "health_insurance_claim": "insurance",
    "insurance_eob":          "insurance",
    "eob_medical":            "insurance",
    "explanation_of_benefits":"insurance",
    "renewal_notice":         "insurance",
    "nonrenewal_notice":      "insurance",
    "cancellation_notice":    "insurance",
    "loss_run":               "insurance",

    # Legal (additional)
    "legal_filing":           "legal",
    "affidavit":              "legal",
    "court_order":            "legal",
    "subpoena":               "legal",
    "nda":                    "legal",
    "msa":                    "legal",
    "sow":                    "legal",
    "statement_of_work":      "legal",

    # Medical (additional)
    "medical_bill":           "medical",
    "lab_report":             "medical",
    "prescription":           "medical",

    # Real Estate (additional)
    "lease_agreement":        "realestate",
    "property_deed":          "realestate",
    "rent_statement":         "realestate",
    "hoa_statement":          "realestate",
    "mortgage_payoff":        "realestate",

    # Tax (additional)
    "visa_document":          "immigration",
    "passport_document":      "personal_id",

    # Utility (additional)
    "telecom_bill":           "utility",
    "internet_bill":          "utility",
    "water_bill":             "utility",
    "gas_bill":               "utility",
    "electric_bill":          "utility",

    # Education
    "education_certificate":  "education",
    "student_record":         "education",

    # Corporate / SEC (additional)
    "proxy_statement":        "corporate",
    "sec_filing":             "corporate",
    "audit_report":           "corporate",
    "compliance_report":      "corporate",
    "risk_assessment":        "corporate",
    "sox_report":             "corporate",
    "policy_document":        "corporate",

    # Operations / Manufacturing
    "manufacturing_spec":     "operations",
    "quality_report":         "operations",
    "safety_document":        "operations",
    "bom":                    "operations",
    "work_order":             "operations",
    "maintenance_log":        "operations",

    # Logistics / Supply Chain
    "packing_slip":           "logistics",
    "bill_of_lading":         "logistics",
    "shipping_label":         "logistics",
    "delivery_note":          "logistics",
    "freight_invoice":        "logistics",
}

# ── Filename keyword → group (fallback when db_type is null/general) ──────────
# Used by _score_doc when the database doc_type field isn't set accurately.
_FILENAME_GROUP_KEYWORDS: List[tuple] = [
    # (set of filename substrings,  group)
    ({"pay", "stub", "payslip", "paycheck", "payroll", "salary", "wage", "paystub", "earnings"}, "payroll"),
    ({"bank", "statement", "checking", "savings", "account"}, "finance"),
    ({"credit", "card", "visa", "mastercard", "amex", "discover"}, "finance"),
    ({"invoice", "bill", "receipt", "expense", "purchase", "order", "po_"}, "finance"),
    ({"annual", "10k", "10-k", "yearly"}, "corporate"),
    ({"quarterly", "10q", "10-q", "q1", "q2", "q3", "q4"}, "corporate"),
    ({"investment", "portfolio", "brokerage", "401k", "ira", "retirement"}, "corporate"),
    ({"w2", "w-2", "1099", "tax", "1040", "irs"}, "tax"),
    ({"resume", "cv", "curriculum", "vitae", "offer", "onboarding", "handbook", "performance"}, "hr"),
    ({"roster", "employee", "staff", "headcount", "directory"}, "hr"),
    ({"policy", "declaration", "dec_page", "certificate", "coi", "insurance"}, "insurance"),
    ({"claim", "fnol", "loss", "adjuster"}, "insurance"),
    ({"contract", "agreement", "nda", "msa", "sow", "lease"}, "legal"),
    ({"medical", "patient", "clinical", "discharge", "emr", "ehr", "rx", "eob", "lab"}, "medical"),
    ({"mortgage", "closing", "escrow", "deed", "title", "hud", "loan", "payoff"}, "realestate"),
    ({"visa", "uscis", "i94", "i-94", "i20", "i-20", "immigration", "h1b", "greencard", "opt"}, "immigration"),
    ({"utility", "electric", "gas", "water", "internet", "cable", "phone_bill"}, "utility"),
    ({"dataset", "data_", "_data", "movies", "songs", "books", "ratings", "census", "stock", "weather"}, "dataset"),
    ({"birthcert", "birth-cert", "birth_cert", "birth-certificate", "birth_certificate",
      "certificate-of-birth", "certificate_of_birth", "birth-record"}, "personal_id"),
    ({"passport"}, "personal_id"),
    ({"drivers-license", "driver-license", "drivers_license", "driver_license",
      "dl_", "_dl", "dmv"}, "personal_id"),
    ({"social-security", "social_security", "ssn_card", "ss_card"}, "personal_id"),
    ({"marriage-certificate", "marriage_certificate", "marriage-license"}, "personal_id"),
    ({"death-certificate", "death_certificate"}, "personal_id"),
    ({"vaccination", "immunization", "vaccine-card", "cdc-card"}, "personal_id"),
    ({"property-tax", "property_tax", "secured-bill", "unsecured-bill", "tax-bill"}, "tax"),
]


# ── Keyword-based intent pre-detection ───────────────────────────────────────
# Catches action intents reliably before the LLM call so phi3:mini parsing
# failures never silently downgrade an action to info_retrieve.
_INTENT_KEYWORD_RULES: List[tuple] = [
    # Rules are evaluated in order — more specific rules must come first.
    # (intent, required_verbs, required_objects)

    # ── High-specificity: notes / tasks — must precede create_client ─────────
    ("create_note",       ["add", "create", "write", "log", "append"],
                          ["note", "comment", "memo"]),
    ("create_task",       ["add", "create", "schedule", "assign", "remind"],
                          ["task", "reminder", "to-do", "todo"]),

    # ── Immigration — must precede create_matter (both use "case") ────────────
    ("create_immigration",["create", "add", "file", "submit", "open", "new"],
                          ["immigration", "visa", "h1b", "h-1b", "i-94", "i94",
                           "green card", "ead", "i-140", "i-485"]),

    # ── Insurance actions ─────────────────────────────────────────────────────
    ("create_claim",      ["create", "file", "open", "submit", "new", "report"],
                          ["claim", "loss report", "loss notice"]),
    ("create_policy",     ["create", "add", "write", "new", "bind", "issue"],
                          ["policy", "insurance policy", "coverage"]),
    ("create_quote",      ["create", "add", "generate", "new", "run", "get"],
                          ["quote", "quotation"]),

    # ── Legal ─────────────────────────────────────────────────────────────────
    ("create_matter",     ["create", "open", "add", "new"],
                          ["matter", "legal matter", "case file", "legal case"]),

    # ── Generic client / contact ──────────────────────────────────────────────
    ("create_client",     ["create", "add", "register", "new"],
                          ["client", "customer", "contact"]),

    # ── Updates ───────────────────────────────────────────────────────────────
    ("update_policy",     ["update", "change", "modify", "edit", "set", "amend"],
                          ["policy", "coverage"]),
    ("update_claim",      ["update", "change", "modify", "close", "reopen", "set"],
                          ["claim"]),
]


def _predetect_action_intent(question: str) -> Optional[str]:
    """Return an action intent if keywords unambiguously match, else None."""
    q = question.lower()
    for intent, verbs, objects in _INTENT_KEYWORD_RULES:
        if any_term(q, verbs) and any_term(q, objects):
            return intent
    return None


def _apply_doc_type_overrides(plan: Dict, question: str) -> Dict:
    """
    Post-process the LLM plan: if the question explicitly names a document type
    (resume, pay stub, contract…) force the correct doc_type_hint.
    Also injects the right search_queries for that doc type.
    """
    q_lower = question.lower()
    forced_type, method = None, None
    for trigger_words, doc_type in _DOC_TYPE_KEYWORD_OVERRIDES:
        if any_term(q_lower, trigger_words):
            forced_type, method = doc_type, "keyword match"
            break

    if not forced_type and plan.get("intent", "info_retrieve") == "info_retrieve":
        # No keyword hit ("how long is my travel document valid?") → zero-shot.
        from app.services.classifiers import zero_shot_router as zsr
        forced_type, method = _zero_shot_doc_type(question)

    if forced_type:
        if plan.get("doc_type_hint", "general") != forced_type:
            logger.info(f"DocTypeOverride: '{plan.get('doc_type_hint')}' → '{forced_type}' ({method})")
        plan["doc_type_hint"] = forced_type
        # Inject domain seeds as extra search queries so the vector search is anchored
        seeds = _DOMAIN_QUERY_SEEDS.get(forced_type, [])
        named = (plan.get("named_subject") or "").strip()
        existing = set(plan.get("search_queries", []))
        for seed in seeds[:3]:
            q = f"{named} {seed}".strip() if named else seed
            if q not in existing:
                plan.setdefault("search_queries", []).append(q)
    return plan


# Short natural descriptions per _DOMAIN_GROUPS group — the model reads these.
# ~15 labels keeps GLiClass accurate; 90+ flat doc types saturates every score.
_GROUP_ZS_LABELS: Dict[str, str] = {
    "personal_id":   "passport, birth certificate, driver's license or ID card",
    "immigration":   "green card, visa, USCIS case or immigration interview",
    "payroll":       "pay stub, salary or payroll",
    "finance":       "bank statement, credit card or invoice",
    "tax":           "tax return or W-2",
    "insurance":     "insurance policy or claim",
    "medical":       "medical record, doctor visit or prescription",
    "realestate":    "mortgage, home loan or property",
    "legal":         "contract, lease or legal agreement",
    "utility":       "utility bill like electricity, water or phone",
    "hr":            "resume or employee records",
    "education":     "school transcript or certificate",
    "logistics":     "shipping or delivery documents",
    "corporate":     "company annual or quarterly report",
    "operations":    "work order, maintenance or quality report",
    "general":       "greeting or general question",
}


_SUPERVISOR_TO_GROUPS: Dict[str, set] = {
    "banking":      {"finance"},
    "insurance":    {"insurance"},
    "payroll":      {"payroll", "hr", "tax"},
    "legal":        {"legal", "immigration"},
    "medical":      {"medical"},
    "financial":    {"corporate", "finance", "tax"},
    "real_estate":  {"realestate", "utility", "tax"},
    "supply_chain": {"logistics", "finance", "operations"},
    "compliance":   {"legal", "corporate", "operations"},
    "operations":   {"operations", "logistics"},
}


def _zero_shot_doc_type(question: str):
    """
    Two-stage: pick the document group (filtering is group-level via
    _DOMAIN_GROUPS), then the closest doc type inside it for query seeds.
    Returns (doc_type, method) or (None, None) when not confident.
    """
    from app.services.classifiers import zero_shot_router as zsr

    groups = {g: d for g, d in _GROUP_ZS_LABELS.items()
              if g == "general" or any(grp == g for grp in _DOMAIN_GROUPS.values())}
    ranked = zsr.score(question, groups, decision="doc_group")
    group = zsr.pick(ranked)
    if not group or group == "general":
        return None, None

    # Keywords are precise when they fire; the zero-shot guess may only refine
    # them, never contradict them ("patient discharged" is medical, not personal_id).
    from app.services.supervisor_agent import _classify_fast
    kw_domains = [d for d in _classify_fast(question) if d != "general"]
    if kw_domains:
        allowed = set().union(*(_SUPERVISOR_TO_GROUPS.get(d, set()) for d in kw_domains))
        if group not in allowed:
            logger.info(f"DocType zero-shot '{group}' disagrees with keyword domains {kw_domains} — ignored")
            return None, None

    members = {dt: dt.replace("_", " ") for dt, g in _DOMAIN_GROUPS.items() if g == group}
    if len(members) == 1:
        return next(iter(members)), f"zero-shot group={group} {ranked[0][1]:.2f}"
    inner = zsr.score(question, members, decision="doc_type")
    doc_type = inner[0][0] if inner else next(iter(members))
    return doc_type, f"zero-shot group={group} {ranked[0][1]:.2f}"


_DECOMPOSE_PROMPT = """Break this question into focused sub-questions for document retrieval.
Output one sub-question per line. Max 6 lines. Each sub-question must be under 15 words.
Cover every distinct topic in the original question.
Output ONLY the sub-questions — no numbering, no bullets, no explanation.

Question: {question}

Sub-questions:"""


def _decompose_query(question: str) -> List[str]:
    """
    Use phi3:mini to split a long multi-part query into focused retrieval sub-questions.
    Falls back to sentence-boundary splitting if LLM fails or is slow.
    Always includes the original question as the first entry.
    """
    # Only decompose if the query is long enough to warrant it
    if len(question) <= 300:
        return [question]

    try:
        resp = llm.chat(role="planner", 
            model=_active_model(),
            messages=[{"role": "user", "content": _DECOMPOSE_PROMPT.replace("{question}", question)}],
            options={"num_ctx": NUM_CTX, "num_predict": 200, "temperature": 0.0},
            timeout=15,
        )
        raw = resp["message"]["content"].strip()
        print(f"subqueries :: {raw}")
        lines = [l.strip().lstrip("-•*0123456789.) ") for l in raw.splitlines() if len(l.strip()) > 15]
        sub_questions = list(dict.fromkeys(lines[:6]))  # dedupe, cap at 6
        if len(sub_questions) >= 2:
            logger.info(f"_decompose_query: split into {len(sub_questions)} sub-questions")
            return [question] + sub_questions
    except Exception as e:
        logger.warning(f"_decompose_query LLM failed: {e} — falling back to sentence split")

    # Sentence-boundary fallback
    sentences = [s.strip() for s in question.replace("?", "?.").split(".") if len(s.strip()) > 30]
    return list(dict.fromkeys([question] + sentences[:5]))


def _extract_named_subject_from_question(question: str) -> Optional[str]:
    """
    Regex fallback to extract a company/vendor/person name from patterns like:
      'whats my bill on FINECUTS SALON'
      'invoice from Acme Corp'
      'payment to City Plumbing'
      'amount due for XYZ Inc'
    Returns the extracted name, or None if not found.
    """
    # Match "on/from/to/for/by/of <CAPITALIZED WORDS>" — company names are usually title-case or ALL-CAPS
    patterns = [
        r'\b(?:on|from|to|for|by|of)\s+([A-Z][A-Za-z0-9&\'\.\-\s]{2,40}?)(?:\s*[?,.]|$)',
        r'\b([A-Z]{2,}(?:\s+[A-Z]{2,})+)\b',   # ALL-CAPS multi-word: "FINECUTS SALON"
    ]
    for pat in patterns:
        m = re.search(pat, question)
        if m:
            candidate = m.group(1).strip()
            # Reject common non-entity words
            skip = {"THE", "A", "AN", "MY", "OUR", "THE", "THIS", "THAT", "WHAT", "WHICH", "WHO"}
            if candidate.upper() in skip or len(candidate) <= 2:
                continue
            # "from Oct 2025 to May 2026", "for March", "to Dec 31" are dates, not names
            if _DATE_PHRASE_RE.match(candidate):
                continue
            return candidate
    return None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_PHRASE_RE = re.compile(
    r"^(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?|q[1-4]|fy\s*\d{2,4}|\d{1,4}"
    r"|last|this|next|past|previous|today|yesterday|tomorrow|week|month|year|quarter)\b",
    re.IGNORECASE,
)


def _month_num(name: str) -> Optional[int]:
    return _MONTHS.get(str(name).strip().lower()[:3])


def _date_range_from_hint(date_hint: Dict) -> Optional[tuple]:
    """
    Turn the planner's date_hint into an inclusive (from, to) ISO date pair.
    Handles ranges that cross a year boundary ("Oct 2025 to May 2026") and
    uses the real last day of the month.
    """
    import calendar
    from datetime import date

    if not isinstance(date_hint, dict):
        return None
    hint_type = date_hint.get("type", "none")
    years = [int(y) for y in (date_hint.get("years") or []) if str(y).isdigit()]
    months = [m for m in (_month_num(x) for x in (date_hint.get("months") or [])) if m]

    if hint_type == "relative":
        m = re.search(r"(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?",
                      date_hint.get("range_description") or "", re.IGNORECASE)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2).lower()
        days = {"day": 1, "week": 7, "month": 31, "year": 366}[unit] * n
        today = date.today()
        return (date.fromordinal(today.toordinal() - days).isoformat(), today.isoformat())

    if not years:
        return None
    if not months:
        return (f"{min(years)}-01-01", f"{max(years)}-12-31")

    # months arrive in the user's order: first = start, last = end
    start_y, start_m = years[0], months[0]
    end_y, end_m = years[-1], months[-1]
    if (end_y, end_m) < (start_y, start_m):
        end_y = start_y + 1 if end_m < start_m else start_y
    last_day = calendar.monthrange(end_y, end_m)[1]
    return (f"{start_y}-{start_m:02d}-01", f"{end_y}-{end_m:02d}-{last_day:02d}")


_TXN_GENERIC_WORDS = {
    "payment", "payments", "transaction", "transactions", "transfer", "transfers",
    "spending", "spend", "spent", "total", "totals", "charge", "charges", "amount",
    "amounts", "purchase", "purchases", "made", "count", "number", "sum", "all",
    "my", "the", "of", "at", "on", "to", "from", "in", "for", "i", "debit", "debits",
    "credit", "credits", "expense", "expenses", "money", "bill", "bills", "fee", "fees",
}


def _transaction_term(plan: Dict, question: str) -> Optional[str]:
    """
    The specific merchant/channel the user asked about ("Zelle payments" → "zelle").
    None when the question is about all transactions ("total spending").
    """
    subject = (plan.get("named_subject") or "").strip()
    if subject and not _DATE_PHRASE_RE.match(subject):
        return subject
    field = plan.get("field") or ""
    words = [w for w in re.findall(r"[A-Za-z0-9&']+", field) if w.lower() not in _TXN_GENERIC_WORDS]
    return " ".join(words) if words else None


def _best_txn_term(term: str, tenant_id: str, doc_ids=None) -> Optional[str]:
    """The phrase if it occurs in transaction merchants, else its most specific word that does."""
    from sqlalchemy import text as _t
    candidates = [term] + sorted({w for w in term.split() if len(w) > 2}, key=len, reverse=True)
    db = SessionLocal()
    try:
        for cand in candidates:
            hit = db.execute(_t(
                "SELECT 1 FROM documents d, jsonb_array_elements(d.extracted_facts->'transactions') t "
                "WHERE d.tenant_id = :tid AND d.extracted_facts IS NOT NULL "
                "AND (t->>'merchant' ILIKE :c OR t->>'category' ILIKE :c) LIMIT 1"),
                {"tid": tenant_id, "c": f"%{cand}%"}).first()
            if hit:
                return cand
    finally:
        db.close()
    return None


def _transaction_direction(question: str) -> Optional[str]:
    """'debit' for money the user paid out, 'credit' for money received, else None."""
    q = question.lower()
    # "who received / who got / recipient" asks about the payee → money sent out
    if re.search(r"\bwho (received|got)\b|\bto whom\b|\brecipients?\b|\bpayees?\b|\bsent to\b", q):
        return "debit"
    outgoing = re.search(r"\b(i|we)\s+(made|paid|sent|spent|spend|pay|send|transferred)\b"
                         r"|\bpaid to\b|\bsent to\b|\bpayments? (made|to)\b|\bspending\b|\bspent\b", q)
    incoming = re.search(r"\b(received|receive|got paid|deposits?|deposited|credited|incoming|refunds?)\b"
                         r"|\bpayments? from\b", q)
    if outgoing and not incoming:
        return "debit"
    if incoming and not outgoing:
        return "credit"
    return None


def _normalize_plan(plan: Dict) -> Dict:
    """Coerce LLM plan fields to their expected types so downstream code never crashes."""
    def _to_str(v, default="") -> str:
        if v is None:
            return default
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return str(v)

    def _to_list(v) -> list:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v]
        return [str(v)]

    plan["intent"]        = _to_str(plan.get("intent"), "info_retrieve")
    plan["field"]         = _to_str(plan.get("field"), "")
    plan["doc_type_hint"] = _to_str(plan.get("doc_type_hint"), "general")
    plan["named_subject"] = _to_str(plan.get("named_subject"), "") or None
    plan["reasoning"]     = _to_str(plan.get("reasoning"), "")

    # aggregation_op: keep as list if list, coerce scalar to list, None stays None
    agg = plan.get("aggregation_op")
    if isinstance(agg, str):
        plan["aggregation_op"] = [agg]
    elif not isinstance(agg, list):
        plan["aggregation_op"] = None

    # field_synonyms must be a list of strings
    plan["field_synonyms"] = _to_list(plan.get("field_synonyms"))

    # search_queries must be a list of strings
    plan["search_queries"] = _to_list(plan.get("search_queries"))

    # date_hint must be a dict
    dh = plan.get("date_hint")
    if not isinstance(dh, dict):
        plan["date_hint"] = {"type": "none", "months": [], "years": [], "range_description": None}
    else:
        plan["date_hint"].setdefault("type", "none")
        plan["date_hint"].setdefault("months", [])
        plan["date_hint"].setdefault("years", [])
        plan["date_hint"].setdefault("range_description", None)

    return plan


def reason_question(question: str) -> Dict:
    """
    Run the question through the LLM to produce a structured research plan.
    Returns the plan dict, or a safe fallback if LLM fails.
    """
    # Fast path: keyword-based intent detection for action commands.
    # These are unambiguous (verb + object) — no need to burn an LLM call.
    _fast_intent = _predetect_action_intent(question)
    if _fast_intent:
        # Infer doc_type_hint from action intent so downstream domain filtering
        # is correct (e.g. create_client for "auto insurance" → insurance, not hr_document)
        _intent_doc_type: Dict[str, str] = {
            "create_client":      "general",          # client has no single doc type
            "create_policy":      "insurance_policy",
            "create_claim":       "insurance_claim",
            "create_quote":       "insurance",
            "create_matter":      "legal",
            "create_note":        "general",
            "create_task":        "general",
            "create_immigration": "immigration",
            "update_policy":      "insurance_policy",
            "update_claim":       "insurance_claim",
        }
        _doc_hint = _intent_doc_type.get(_fast_intent, "general")
        logger.info(f"QuestionReasoner: fast-path intent={_fast_intent} doc_type={_doc_hint} (keyword match)")
        plan: Dict = {
            "intent": _fast_intent,
            "field": None,
            "field_synonyms": [],
            "doc_type_hint": _doc_hint,
            "date_hint": {"type": "none", "months": [], "years": [], "range_description": None},
            "needs_aggregation": False,
            "aggregation_op": None,
            "search_queries": [question],
            "named_subject": None,
            "reasoning": f"Keyword pre-detection matched intent '{_fast_intent}'.",
        }
        return plan

    examples = _select_examples(question, n=8)
    prompt = REASONER_PROMPT_TEMPLATE.replace("{examples}", examples).replace("{question}", question)
    try:
        resp = llm.chat(role="planner", 
            model=_active_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX, "num_predict": 800, "temperature": 0.0},
        )
        raw = resp["message"]["content"].strip()
        raw = re.sub(r"```[a-z]*\s*", "", raw).strip("`").strip()
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            plan = json.loads(raw[start:end])
            plan = _normalize_plan(plan)
            # Guard: if LLM returned a write intent but the question is clearly
            # analytical/retrieval, downgrade to info_retrieve to avoid HITL.
            _WRITE_INTENTS = {
                "create_policy", "create_claim", "create_quote", "create_client",
                "create_matter", "update_policy", "update_claim", "create_approval",
            }
            _RETRIEVAL_SIGNALS = {
                "get", "show", "find", "list", "compare", "what", "how much",
                "tell me", "details", "summary", "gap", "difference", "review",
                "analyze", "analyse", "check", "explain", "describe",
            }
            if plan.get("intent") in _WRITE_INTENTS:
                q_lower = question.lower()
                if any_term(q_lower, _RETRIEVAL_SIGNALS):
                    logger.info(
                        f"QuestionReasoner: downgrading write intent "
                        f"'{plan['intent']}' → info_retrieve (retrieval signals detected)"
                    )
                    plan["intent"] = "info_retrieve"
            # If LLM missed named_subject, try regex extraction as fallback
            if not plan.get("named_subject"):
                plan["named_subject"] = _extract_named_subject_from_question(question)
            plan = _apply_doc_type_overrides(plan, question)
            # For long multi-part queries, only decompose if the reasoner
            # produced fewer than 3 search queries (it may have already split well)
            if len(question) > 300 and len(plan.get("search_queries", [])) < 3:
                plan["search_queries"] = _decompose_query(question)
            logger.info(f"QuestionReasoner: intent={plan.get('intent')} doc_type={plan.get('doc_type_hint')} named_subject={plan.get('named_subject')} queries={len(plan.get('search_queries', []))}")
            return plan
    except Exception as e:
        logger.warning(f"QuestionReasoner failed: {e} — using fallback plan")

    # Keyword pre-detection: if the LLM succeeded above, also check if an
    # action intent should override a weak LLM result.
    # (fall-through only reached when LLM parsing failed)
    predetected = _predetect_action_intent(question)
    search_queries = _decompose_query(question)

    fallback = {
        "intent": predetected or "info_retrieve",
        "field": None,
        "field_synonyms": [],
        "doc_type_hint": "general",
        "date_hint": {"type": "none", "months": [], "years": [], "range_description": None},
        "needs_aggregation": False,
        "aggregation_op": None,
        "search_queries": search_queries,
        "named_subject": _extract_named_subject_from_question(question),
        "reasoning": "Fallback — treating as general information retrieval",
    }
    return _normalize_plan(_apply_doc_type_overrides(fallback, question))


# ── Document scoring against ALL plan hints ───────────────────────────────────

def _score_doc(filename: str, db_doc_type: str, doc_metadata: Dict, plan: Dict) -> int:
    """
    Score a document against all hints from the reasoner plan.
    All keyword maps and weights come from research_hints.yaml — no hard-coding here.

    Signals (additive, weights from scoring section of hints file):
      date_month_and_year : month AND year both match doc_metadata OR filename
      date_partial        : only month OR only year matches
      doc_type_match      : doc_type_hint keywords match filename or db doc_type
      field_in_filename   : field or synonym appears in filename
    """
    weights   = _scoring()
    doc_types = _doc_type_keywords()
    fn        = (filename or "").lower()
    db_type   = (db_doc_type or "").lower()
    meta      = doc_metadata or {}
    # Generic containers (CSV/Excel) can hold any kind of data — always include
    # them in search scope with a baseline score so vector search can decide.
    _generic_container_db_types = {"data_table", "spreadsheet", "dataset"}
    score     = 1 if db_type in _generic_container_db_types else 0

    # ── Signal 1: Date hints ──────────────────────────────────────────────────
    # Check doc_metadata first (extracted at ingest — most reliable),
    # then fall back to filename substring matching.
    date_hint = plan.get("date_hint", {})
    date_type = date_hint.get("type", "none")
    if date_type != "none":
        months = [m.lower() for m in date_hint.get("months", [])]
        years  = date_hint.get("years", [])

        # Expand month names to all aliases using hints file.
        # month_aliases keys are capitalized ("May"); months list is lowercased —
        # compare case-insensitively so "may" matches "May".
        month_aliases = _month_aliases()
        expanded_months = set(months)
        for m in months:
            for full, info in month_aliases.items():
                full_lower = full.lower()
                abbr = (info.get("abbr") or "").lower()
                num  = str(info.get("num") or "")
                if m == full_lower or m == abbr or m == num:
                    expanded_months.update([abbr, num, full_lower, full])
                    # Also add zero-padded and non-padded numeric forms
                    if num:
                        expanded_months.add(num.zfill(2))
                        expanded_months.add(num.lstrip("0") or "0")

        # doc_metadata match (pay_period = "2026-05", pay_period_month = "may", pay_period_year = "2026")
        meta_period = meta.get("pay_period", "")          # "2026-05"
        meta_month  = meta.get("pay_period_month", "")    # "may"
        meta_year   = meta.get("pay_period_year", "")     # "2026"

        # Extract the numeric month from meta_period for suffix matching
        import re as _re
        meta_period_mm = ""
        _pm = _re.match(r'^20\d{2}[-/](\d{2})$', meta_period)
        if _pm:
            meta_period_mm = _pm.group(1)  # "05"

        meta_month_hit = bool(months) and (
            meta_month in expanded_months
            or (meta_period_mm and meta_period_mm in expanded_months)
            or (meta_period_mm and str(int(meta_period_mm)) in expanded_months)
        )
        meta_year_hit  = bool(years) and meta_year in [str(y) for y in years]

        # Filename fallback
        fn_month_hit = bool(months) and any(tok in fn for tok in expanded_months)
        fn_year_hit  = bool(years)  and any(str(y) in fn for y in years)

        month_hit = meta_month_hit or fn_month_hit
        year_hit  = meta_year_hit  or fn_year_hit

        if month_hit and year_hit:
            score += weights["date_month_and_year"]
        elif month_hit or year_hit:
            score += weights["date_partial"]

    # ── Signal 2: Document type hint (from hints file) ────────────────────────
    doc_type_hint = plan.get("doc_type_hint", "general")
    if doc_type_hint and doc_type_hint != "general":
        keywords = doc_types.get(doc_type_hint, [])
        if any(kw in fn or kw in db_type for kw in keywords):
            score += weights["doc_type_match"]

    # ── Signal 3: Field / synonym presence in filename ────────────────────────
    field    = (plan.get("field") or "").lower()
    synonyms = [s.lower() for s in plan.get("field_synonyms", [])]
    if any(term in fn for term in ([field] if field else []) + synonyms):
        score += weights["field_in_filename"]

    # ── Signal 4: Named-subject boost / exclusion ────────────────────────────
    # If the question is specifically about a named entity (e.g. "FINECUTS SALON"),
    # boost docs whose filename/metadata contain that name, and hard-exclude docs
    # that clearly belong to a DIFFERENT named entity (e.g. have another org name
    # in their filename when a specific org was requested).
    named_subject = (plan.get("named_subject") or "").strip()
    if named_subject:
        ns_tokens = [t.lower() for t in named_subject.split() if len(t) > 1]
        fn_and_meta = fn + " " + " ".join(str(v).lower() for v in meta.values() if v)
        matched_tokens = sum(1 for t in ns_tokens if t in fn_and_meta)
        if matched_tokens == len(ns_tokens):
            # All tokens found → strong boost
            score += weights.get("named_subject_match", 30)
        elif matched_tokens > 0:
            # Partial match → small boost
            score += weights.get("named_subject_partial", 10)
        # No match is not an exclusion — entity name may only appear in chunk text

    # ── Signal 5: Domain conflict penalty ─────────────────────────────────────
    # If we have a specific doc_type_hint and the document clearly belongs to a
    # DIFFERENT domain group, hard-exclude it (score 0).
    # This prevents pay stubs from appearing in resume searches, contracts from
    # appearing in medical searches, etc.
    # Exception: data_table/spreadsheet/dataset docs are generic containers (CSV,
    # Excel) that can hold any kind of data — never hard-exclude them based on
    # doc_type conflict alone; let vector search decide their relevance.
    doc_type_hint = plan.get("doc_type_hint", "general")
    _GENERIC_CONTAINER_GROUPS = {"dataset", "spreadsheet"}
    if doc_type_hint and doc_type_hint != "general":
        target_group = _DOMAIN_GROUPS.get(doc_type_hint)
        doc_group = _DOMAIN_GROUPS.get(db_type)
        # Generic containers (CSV, spreadsheet) only pass through if the TARGET is
        # also a data domain. A tax/finance/medical query must not pull in CSV files.
        if doc_group in _GENERIC_CONTAINER_GROUPS and target_group not in _GENERIC_CONTAINER_GROUPS:
            return 0  # Hard exclude — data container irrelevant to non-data query
        if doc_group not in _GENERIC_CONTAINER_GROUPS:
            if target_group:
                # 1. Try the database doc_type field (most reliable)
                # 2. Fallback: infer group from filename keyword patterns
                if doc_group is None:
                    for kw_set, grp in _FILENAME_GROUP_KEYWORDS:
                        if any(kw in fn for kw in kw_set):
                            doc_group = grp
                            break
                # 3. If we identified a group and it conflicts → exclude
                if doc_group is not None and doc_group != target_group:
                    return 0  # Hard exclude — wrong domain

    return score






def _domain_doc_types(target_group: str) -> List[str]:
    """Return all doc_type strings that belong to a given domain group."""
    return [dt for dt, grp in _DOMAIN_GROUPS.items() if grp == target_group]


def _filter_docs_by_plan(plan: Dict, user_doc_ids: List[str]) -> List[str]:
    """
    Score documents against the plan's hints — fully SQL-side for scale.

    Strategy (avoids full table scan at million-doc scale):
      1. If doc_type_hint is set → SQL WHERE doc_type = ANY(domain_types) to fetch
         only candidates in the right domain. O(index_scan) not O(table_scan).
      2. Date and named_subject signals applied in Python only over the candidate set,
         which is already small (same domain).
      3. Falls back to filename-keyword candidates when domain match returns nothing.
      4. generic containers (spreadsheet/dataset) always included when query is data-domain.
    """
    from sqlalchemy import text
    date_hint     = plan.get("date_hint", {})
    date_type     = date_hint.get("type", "none")
    doc_type      = plan.get("doc_type_hint", "general")
    named_subject = (plan.get("named_subject") or "").strip()
    has_signals   = (
        date_type != "none"
        or (doc_type and doc_type != "general")
        or plan.get("field")
        or bool(named_subject)
    )
    domain_specific = doc_type and doc_type != "general"

    if not has_signals:
        return list(user_doc_ids)

    tenant_id = get_current_tenant_id() or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        # ── Build user_doc_ids filter fragment ────────────────────────────────
        uid_filter = ""
        uid_params: Dict[str, Any] = {"tenant_id": tenant_id}
        if user_doc_ids:
            placeholders = ", ".join(f":uid_{i}" for i in range(len(user_doc_ids)))
            uid_filter = f"AND id::text IN ({placeholders})"
            for i, uid in enumerate(user_doc_ids):
                uid_params[f"uid_{i}"] = uid

        # ── Pass 1: SQL-side domain filter (no Python table scan) ─────────────
        candidate_rows = []
        if domain_specific:
            target_group = _DOMAIN_GROUPS.get(doc_type)
            if target_group:
                # Collect all doc_types in this domain group + generic containers when relevant
                domain_types = _domain_doc_types(target_group)
                _generic_container_groups = {"dataset", "spreadsheet"}
                if target_group in _generic_container_groups:
                    domain_types += list(_domain_doc_types("dataset")) + list(_domain_doc_types("spreadsheet"))
                    domain_types = list(set(domain_types))

                if domain_types:
                    type_placeholders = ", ".join(f":dt_{i}" for i in range(len(domain_types)))
                    dt_params = {f"dt_{i}": t for i, t in enumerate(domain_types)}
                    sql = text(f"""
                        SELECT id, filename, doc_type, doc_metadata
                        FROM documents
                        WHERE status = 'completed'
                          AND tenant_id = :tenant_id
                          AND doc_type IN ({type_placeholders})
                          {uid_filter}
                        LIMIT 2000
                    """)
                    candidate_rows = db.execute(sql, {**dt_params, **uid_params}).fetchall()
                    logger.info(f"DocFilter[SQL]: tenant={tenant_id} domain={target_group} types={len(domain_types)} → {len(candidate_rows)} candidates")

        # ── Pass 2: Fallback — scan all docs for this tenant ─────────────────
        if not candidate_rows:
            sql = text(f"""
                SELECT id, filename, doc_type, doc_metadata
                FROM documents
                WHERE status = 'completed'
                  AND tenant_id = :tenant_id
                  {uid_filter}
                LIMIT 5000
            """)
            candidate_rows = db.execute(sql, uid_params).fetchall()
            logger.info(f"DocFilter[fallback]: tenant={tenant_id} scanning {len(candidate_rows)} docs")

        # ── Pass 3: Python scoring over the (now small) candidate set ─────────
        scored: List[tuple] = []
        for row in candidate_rows:
            s = _score_doc(row.filename, row.doc_type, row.doc_metadata or {}, plan)
            if s > 0:
                scored.append((str(row.id), s, row.filename))

        min_score = _scoring().get("min_score_to_include", 1)
        scored = [(doc_id, sc, fn) for doc_id, sc, fn in scored if sc >= min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        result_ids = [doc_id for doc_id, _, _ in scored]

        logger.info(
            f"DocFilter: date_type={date_type} doc_type={doc_type} min_score={min_score} → "
            f"{len(result_ids)} docs: {[(fn, sc) for _, sc, fn in scored[:5]]}"
        )
        if not result_ids and domain_specific:
            return []
        return result_ids if result_ids else list(user_doc_ids)
    finally:
        db.close()


# ── Search ────────────────────────────────────────────────────────────────────

_MIN_CHUNK_SIMILARITY = 0.10   # global default floor — retrieve broadly, let LLM judge
_MIN_CHUNK_TEXT_LEN   = 20     # skip chunks that are basically empty after OCR



def _search(query: str, doc_ids: List[str] = None, top_k: int = 8,
            required_text: str = None, min_similarity: float = None) -> List[Dict]:
    """
    Vector similarity search over document_chunks.
    required_text: if set, only return chunks that literally contain this string (case-insensitive).
                   Use for named entities — embedding alone can't distinguish "FINECUTS SALON"
                   from other dollar-amount text.
    min_similarity: override the floor (default: _MIN_CHUNK_SIMILARITY).
    """
    from sqlalchemy import text
    tenant_id = get_current_tenant_id() or DEFAULT_TENANT_ID
    db = SessionLocal()
    # Use the passed floor when it was explicitly set; otherwise use the global floor.
    # Explicit overrides (e.g. for personal ID docs) can go below the global minimum.
    floor = min_similarity if min_similarity is not None else _MIN_CHUNK_SIMILARITY
    try:
        vec = _get_embedding_model().encode(query).tolist()
        doc_filter   = ""
        keyword_filter = ""
        params: Dict[str, Any] = {"vec": str(vec), "threshold": floor, "limit": top_k, "tenant_id": tenant_id}
        if doc_ids:
            placeholders = ", ".join(f":doc_{i}" for i in range(len(doc_ids)))
            doc_filter = f"AND d.id::text IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"doc_{i}"] = did
        if required_text:
            keyword_filter = "AND c.chunk_text ILIKE :kw"
            params["kw"] = f"%{required_text}%"

        sql = text(f"""
            SELECT
                c.document_id,
                c.chunk_text,
                d.filename,
                d.doc_type,
                1 - (c.embedding <=> CAST(:vec AS vector(768))) AS similarity
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL
              AND d.status = 'completed'
              AND d.tenant_id = :tenant_id
              AND vector_dims(c.embedding) = 768
              AND 1 - (c.embedding <=> CAST(:vec AS vector(768))) >= :threshold
              {doc_filter}
              {keyword_filter}
            ORDER BY similarity DESC
            LIMIT :limit
        """)
        rows = db.execute(sql, params).fetchall()
        results = []
        for r in rows:
            txt = (r.chunk_text or "").strip()
            if len(txt) < _MIN_CHUNK_TEXT_LEN:
                continue  # drop near-empty OCR artefacts
            results.append({
                "doc_id":   str(r.document_id),
                "filename": r.filename,
                "text":     txt[:600],
                "similarity": round(float(r.similarity), 3),
            })
        return results
    except Exception as e:
        logger.error(f"search error: {e}")
        return []
    finally:
        db.close()


_DOMAIN_QUERY_SEEDS: Dict[str, List[str]] = {
    # HR / Resume
    "hr_document":      ["work experience employment history", "education degree certification", "skills expertise technologies", "job title position role", "achievements accomplishments projects"],
    "employee_roster":  ["employee name department position", "hire date salary grade"],
    # Finance
    "pay_stub":         ["gross pay net pay deductions", "federal tax state tax withholding", "pay period earnings YTD"],
    "payroll_report":   ["payroll total wages hours", "overtime deductions net pay"],
    "bank_statement":   ["account balance deposits withdrawals", "transaction date amount description"],
    "credit_card_statement": ["charges payments statement balance", "merchant amount date"],
    "invoice":          ["invoice number amount due date", "line items total tax vendor"],
    "purchase_order":   ["PO number vendor items ordered", "unit price total amount delivery"],
    "expense_report":   ["expense category amount receipt", "reimbursement total business purpose"],
    "financial_statement": ["revenue expenses net income assets liabilities", "balance sheet income statement cash flow"],
    # Insurance
    "insurance_policy": ["policy number insured name coverage", "deductible premium limit effective expiry"],
    "declarations_page": ["coverage limits deductible premium", "insured property named insured policy period"],
    "insurance_claim":  ["claim number claimant incident date", "damage amount adjuster status settlement"],
    "insurance":        ["policy coverage premium deductible", "insured beneficiary carrier"],
    # Legal / CLM
    "contract":         ["parties effective date term obligations", "termination clause penalty indemnification"],
    "legal":            ["agreement parties obligations representations", "governing law jurisdiction dispute"],
    # Medical
    "medical_record":   ["patient name diagnosis treatment", "medication dosage physician date of service"],
    "medical":          ["diagnosis symptoms treatment medications", "patient doctor hospital date"],
    # Real Estate
    "mortgage_document": ["loan amount interest rate term", "borrower property address lender"],
    "closing_disclosure": ["purchase price loan amount closing costs", "cash to close prepaid items"],
    "real_estate":      ["property address square footage", "purchase price seller buyer closing date"],
    # Immigration
    "immigration":      ["visa class beneficiary employer sponsor", "priority date receipt number approval date"],
    "uscis_receipt":    ["receipt number petition type beneficiary", "case status approval date"],
    # Annual / Quarterly reports
    "annual_report":    ["revenue net income earnings per share", "total assets liabilities equity growth"],
    "quarterly_report": ["quarterly revenue income expenses", "EPS guidance outlook segment"],
    # Tax
    "property_tax_bill": ["total amount due first installment second installment", "taxable value parcel number bill number", "property tax annual secured"],
    "tax_form":         ["taxable income tax withheld refund", "filing status wages deductions credits"],
    "w2":               ["wages tips federal income tax withheld", "employer EIN social security medicare"],
    # Personal Identity
    "birth_certificate": ["date of birth place of birth full name", "state registrar vital records certificate number"],
    "passport":          ["passport number nationality date of expiry", "issuing authority country of citizenship place of birth"],
    "drivers_license":   ["license number class restrictions expiration", "date of birth address department of motor vehicles"],
    "social_security_card": ["social security number name card", "ssa employment eligibility"],
    "marriage_certificate": ["date of marriage parties names officiant", "county clerk certificate number place of marriage"],
    "death_certificate": ["date of death cause of death decedent", "certifying physician place of death certificate number"],
    "vaccination_record": ["vaccine name date administered dose lot number", "provider clinic covid immunization"],
    "naturalization_certificate": ["date of naturalization certificate number", "former nationality date of birth court"],
    # Education
    "transcript":        ["course name grade credits GPA semester", "academic record student ID registrar"],
    "education_certificate": ["certificate diploma degree awarded date", "institution program completion"],
    "student_record":    ["student name enrollment status academic standing", "program degree graduation date"],
    "recommendation_letter": ["recommender name candidate qualifications", "relationship skills assessment"],
    # Logistics
    "packing_slip":      ["items shipped quantity weight package", "ship to ship from tracking carrier"],
    "bill_of_lading":    ["shipper consignee freight carrier", "weight dimensions commodity freight charges"],
    "shipping_label":    ["tracking number carrier address ship to", "weight dimensions barcode"],
    "delivery_note":     ["items delivered quantity recipient signature", "delivery date proof of delivery"],
    "freight_invoice":   ["freight charges weight miles carrier", "shipper consignee invoice number"],
    # Operations
    "work_order":        ["work order number task description technician", "priority status completion date"],
    "maintenance_log":   ["maintenance date service performed equipment", "technician next service due"],
    "safety_document":   ["hazard identification ppe required procedure", "chemical name risk level emergency"],
    "quality_report":    ["defect rate inspection result pass fail", "nonconformance corrective action"],
    "manufacturing_spec": ["specification tolerance material revision", "bill of materials assembly dimensions"],
    # Corporate / Compliance
    "audit_report":      ["audit finding noncompliance risk control", "recommendation corrective action management response"],
    "compliance_report": ["regulatory requirement compliance status", "risk violation remediation deadline"],
    "risk_assessment":   ["risk likelihood impact severity", "mitigation controls residual risk"],
    "policy_document":   ["policy scope purpose definitions", "responsibilities procedures exceptions"],
    "sec_filing":        ["form 10-k 10-q 8-k revenue income", "securities exchange commission filing period"],
    # General
    "general":          [],
}

# Confidence is checked on the answer, not on search scores: reranker scores for
# short questions against table rows sit at 0.3-0.4 even when the right row was
# found, so a score threshold flagged correct answers as "low confidence".
_NUMBER_RE    = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")
_NOT_FOUND_RE = re.compile(r"\[NOT FOUND\]|\bnot (?:found|mentioned|provided|available)\b|"
                           r"\bdo(?:es)? not (?:contain|include|mention)\b|\bno information\b", re.I)


def _digits(s: str) -> str:
    return re.sub(r"[^\d.\-]", "", s).strip(".")


_REDACTION_RE = re.compile(r"\[([A-Z0-9#][A-Z0-9#/ \-]{0,30} REDACTED)\]")


def _unbracket_redactions(txt: str) -> str:
    """[PHONE REDACTED] looks like a [filename] citation; the model cited it as a source."""
    return _REDACTION_RE.sub(r"‹\1›", txt)


def _strip_citations(answer: str) -> str:
    """Numbers inside [filename] citations (072-4230-…-2025.pdf) aren't answer values."""
    return re.sub(r"\[[^\]\n]*\]", " ", answer)


def _has_number(num: str, body: str) -> bool:
    """Whole-number match: 97.98 must not match inside 1297.98."""
    return re.search(r"(?<![\d.])-?" + re.escape(num.lstrip("-")) + r"(?![\d])", body) is not None


def _derived(missing: List[str], answer: str, source: str) -> Dict[str, str]:
    """Figures the answer computed from figures that ARE in the source: a difference, a sum,
    or a percentage of two of them (e.g. a year-over-year change). Returns {figure: how}."""
    def val(x):
        try:
            return float(_digits(x))
        except ValueError:
            return None
    found = []
    for raw in dict.fromkeys(_NUMBER_RE.findall(_strip_citations(answer))):
        n = _digits(raw)
        v = val(raw)
        if v is not None and len(n.replace("-", "").replace(".", "")) >= 2 and _has_number(n, source):
            found.append((raw.strip(), v))
    found = found[:25]
    out: Dict[str, str] = {}
    for m in missing:
        target = val(m)
        if target is None:
            continue
        tol = max(0.011, abs(target) * 0.0005)
        for i, (ra, a) in enumerate(found):
            for rb, b in found[i + 1:]:
                for how, v in ((f"{ra} − {rb}", a - b), (f"{rb} − {ra}", b - a), (f"{ra} + {rb}", a + b),
                               (f"{ra} ÷ {rb}", a / b * 100 if b else None), (f"{rb} ÷ {ra}", b / a * 100 if a else None),
                               (f"change {rb} → {ra}", (a - b) / b * 100 if b else None),
                               (f"change {ra} → {rb}", (b - a) / a * 100 if a else None)):
                    if v is not None and abs(abs(v) - abs(target)) <= (tol if "÷" not in how and "change" not in how else 0.06):
                        out[m] = how
                        break
                if m in out:
                    break
            if m in out:
                break
    return out


def _grounding_note(answer: str, context: str) -> str:
    """Empty when every figure in the answer appears in the source text."""
    if not answer or answer.startswith("Error generating answer"):
        return ""
    if _NOT_FOUND_RE.search(answer):
        return ("\n\n---\n⚠️ *Not found in the documents that were read. If you know which "
                "document has it, select that document and ask again.*")
    source = re.sub(r"(?<=\d),(?=\d)", "", context)          # 1,673.01 → 1673.01
    missing = []
    for raw in dict.fromkeys(_NUMBER_RE.findall(_strip_citations(answer))):
        num = _digits(raw)
        if len(num.replace("-", "").replace(".", "")) < 2:   # skip list markers, single digits
            continue
        if not _has_number(num, source):
            missing.append(raw.strip())
    calculated = []
    if missing:
        calc = _derived(missing, answer, source)
        calculated = [f"{m} = {how}" for m, how in calc.items()]
        missing = [m for m in missing if m not in calc]
    note = ("\n\n---\n🧮 *Calculated from figures in the document: " + "; ".join(calculated[:4]) + "*") if calculated else ""
    if missing:
        return note + ("\n\n---\n⚠️ *Check these values — they don't appear in the source documents: "
                       + ", ".join(missing[:5]) + "*")
    return note + _citation_check(answer, context)


_DOC_HEADER_RE = re.compile(r"^\[([^\]\n]+)\]:?\s*$|^\[([^\]\n]+)\]:", re.M)


def _figures(text: str) -> set:
    return {n for n in (_digits(x) for x in _NUMBER_RE.findall(_strip_citations(text)))
            if len(n.replace("-", "").replace(".", "")) >= 2}


def _cascade_answer(prompt: str, num_predict: int, sources, context: str, question: str = "") -> Tuple[str, str]:
    """
    Final answer, returning (answer, note).

    No cloud model active → one local call, as before.
    Cloud model active (cascade): the local model answers first; if its answer passes the
    source check (every figure appears in the documents, and it isn't "not found") it is
    used and nothing leaves the machine. Otherwise the question escalates to the cloud
    model — consent, never-send types, masking and budget still apply — and the cloud
    answer is verified against the source, with any disagreement with the local answer shown.
    Each cascade decision is logged locally (llm_cascade_outcomes) as labelled data.
    """
    msgs = [{"role": "user", "content": prompt}]
    opts = {"num_ctx": NUM_CTX, "num_predict": num_predict, "temperature": 0.10}
    docs = sorted(sources)

    def run(cloud: bool) -> str:
        r = llm.chat(role="answer", model=_active_model(), messages=msgs, options=opts, cloud=cloud, documents=docs)
        logger.info(f"Synthesis ({llm.last_route()[0]}): prompt={r.get('prompt_eval_count')} tok, "
                    f"answer={r.get('eval_count')} tok, stop={r.get('done_reason')}")
        return _decode_llm_text(r["message"]["content"].strip())

    if not llm.cloud_for("answer") or llm.decision() == "local":
        a = run(False)
        return a, _grounding_note(a, context)
    if llm.decision() == "ok" or not llm.policy().get("cascade_local_first", True):
        a = run(True)
        note = _verify_note(a, context, None)
        llm.log_cascade(question, docs, "cloud" if llm.last_route()[0] == "cloud" else "local",
                        local_reason="skipped", escalated=True, cloud_answer=a,
                        cloud_verified=_verified(a, context))
        return a, note

    local = run(False)
    reason = _local_reason(local, context)
    if reason == "not_found" and not _documents_cover(question, context):
        # The search only found loosely related documents. A cloud model reading the same
        # documents can't find what isn't there, so nothing is sent — say so instead.
        missing = _distinctive_terms(question)
        llm.log_cascade(question, docs, "local", local_answer=local, local_passed=False, local_reason="not_covered")
        return local, (f"\n\n---\n*None of the documents searched mention {', '.join(missing[:3])}. "
                       "Nothing was sent to the cloud. Upload the document that covers this and ask again.*"
                       if missing else "\n\n---\n*The documents searched don't cover this. Nothing was sent to the cloud.*")
    if reason in ("verified", "no_figures"):
        llm.log_cascade(question, docs, "local", local_answer=local, local_passed=True, local_reason=reason)
        return local, ("\n\n---\n✓ *Answered by the local model and checked against the source — nothing was sent "
                       "to the cloud.*" if reason == "verified" else
                       "\n\n---\n✓ *Answered by the local model — nothing was sent to the cloud.*")
    logger.info(f"Cascade: local answer {reason} — escalating to the cloud model")
    try:
        a = run(True)
    except llm.CloudConsentRequired as c:
        llm.log_cascade(question, docs, "consent_requested", local_answer=local, local_passed=False,
                        local_reason=reason, escalated=True)
        # Keep what's done: the user's choice then runs only the final step
        c.resume = llm.park({"prompt": prompt, "num_predict": num_predict, "sources": docs, "context": context,
                             "question": question, "local": local, "local_reason": reason})
        raise
    differ = sorted(_figures(local) - _figures(a)) if reason == "ungrounded" else []
    llm.log_cascade(question, docs, "cloud" if llm.last_route()[0] == "cloud" else "local",
                    local_answer=local, local_passed=False, local_reason=reason, escalated=True,
                    cloud_answer=a, cloud_verified=_verified(a, context), disagreement=differ or None)
    return a, _verify_note(a, context, local)


_COMMON = set("""what which when where who whom whose why how does did do is are was were the this that these those
and or for from with into onto about over under between show list give tell find total amount amounts much many my our
your their his her its all any each every per year years month months date dates payment payments document documents
file files report reports page pages summary detail details please number numbers value values cities counties""".split())


def _distinctive_terms(question: str) -> List[str]:
    """Words in the question that a relevant document must contain: names, codes and
    hyphenated terms first (Bradley-Burns, I-797, W-2), then other uncommon words."""
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*[A-Za-z0-9]", question or "")
    strong = [t for t in toks if "-" in t or (re.search(r"\d", t) and re.search(r"[A-Za-z]", t))]
    if strong:
        return list(dict.fromkeys(strong))
    words = [t for t in toks if len(t) >= 4 and t.lower() not in _COMMON]
    return list(dict.fromkeys(words))


def _documents_cover(question: str, context: str) -> bool:
    """Do the retrieved excerpts mention what the question is about? Hyphenated / coded
    terms must appear (allowing a space for the hyphen); otherwise at least half the
    uncommon words must."""
    terms = _distinctive_terms(question)
    if not terms:
        return True
    low = (context or "").lower()
    def has(t: str) -> bool:
        t = t.lower()
        return t in low or t.replace("-", " ") in low or t.replace("-", "") in low
    if any("-" in t or re.search(r"\d", t) for t in terms):
        return any(has(t) for t in terms)
    return sum(has(t) for t in terms) * 2 >= len(terms)


def finish_parked(p: Dict[str, Any], use_cloud: bool) -> Tuple[str, str, List[str]]:
    """Complete a question parked at the consent step: (answer, note, sources).
    Cloud: one cloud call with the same prompt, verified against the same passages.
    Local: the local answer already written, with its source check."""
    if not use_cloud:
        return p["local"], _grounding_note(p["local"], p["context"]), p["sources"]
    r = llm.chat(role="answer", model=_active_model(), messages=[{"role": "user", "content": p["prompt"]}],
                 options={"num_ctx": NUM_CTX, "num_predict": p["num_predict"], "temperature": 0.10},
                 cloud=True, documents=p["sources"])
    a = _decode_llm_text(r["message"]["content"].strip())
    differ = sorted(_figures(p["local"]) - _figures(a)) if p.get("local_reason") == "ungrounded" else []
    llm.log_cascade(p["question"], p["sources"], "cloud" if llm.last_route()[0] == "cloud" else "local",
                    local_answer=p["local"], local_passed=False, local_reason=p.get("local_reason"), escalated=True,
                    cloud_answer=a, cloud_verified=_verified(a, p["context"]), disagreement=differ or None)
    return a, _verify_note(a, p["context"], p["local"]), p["sources"]


def _parts_block(question: str) -> str:
    """Conquer: a numbered checklist of the question's parts, so each one is answered."""
    try:
        from app.services.hybrid_search import question_parts
        parts = question_parts(question)
    except Exception:
        return ""
    if len(parts) < 2:
        return ""
    return ("\nThis question has " + str(len(parts)) + " parts. Answer EACH part under its own number, "
            "with the exact figure and its [filename]; write [NOT FOUND] only for a part the excerpts don't cover:\n"
            + "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1)))


_CHART_REF_RE = re.compile(r"\b(chart|figure|graph)\s+(\d{1,3}[A-Za-z]?)\b", re.I)


def _chart_gaps(question: str, context: str) -> List[str]:
    """Charts the question names ("Chart 2") whose values aren't in the passages. Chart values
    exist only when the vision model read the chart at upload (a CHART DATA block)."""
    gaps = []
    for kind, num in dict.fromkeys(_CHART_REF_RE.findall(question or "")):
        read = re.search(rf"CHART DATA[\s\S]*?\b{kind}\s+{re.escape(num)}\b", context or "", re.I)
        if not read:
            gaps.append(f"{kind.title()} {num}")
    return gaps


def _chart_gap_instruction(question: str, context: str) -> str:
    gaps = _chart_gaps(question, context)
    if not gaps:
        return ""
    return (f"\nIMPORTANT: The values of {', '.join(gaps)} are drawn in an image and are NOT in the excerpts. "
            "Use only figures written in the excerpts. For anything only the chart shows, say it is not "
            "available in the text. Never estimate, count or rank chart items you cannot see.")


def _vision_ready() -> Tuple[bool, str]:
    """(installed?, model) for the Vision role."""
    try:
        from app.services.vlm_forms import VLM_MODEL
        model = llm.local_model_for("vision", VLM_MODEL)
        return (model in llm._installed_local()), model
    except Exception:
        return False, "gemma4:latest"


def _chart_gap_note(question: str, context: str) -> str:
    gaps = _chart_gaps(question, context)
    if not gaps:
        return ""
    ok, model = _vision_ready()
    how = ("Re-process this document (Documents → 🔄 on its row) so the vision model reads its charts."
           if ok else
           f"For complete chart and form values, install the vision model — in Terminal run "
           f"`ollama pull {model.split(':')[0]}` (about 10 GB; needs 16 GB of memory or more) — then re-process "
           f"this document. See Help → Recommended models.")
    return (f"\n\n---\n📊 *{', '.join(gaps)} is a chart image whose values haven't been read, so this answer "
            f"uses only what the text says. {how}*")


def _local_reason(answer: str, context: str) -> str:
    """verified | no_figures | not_found | ungrounded — the label a decision model would learn."""
    if _NOT_FOUND_RE.search(answer):
        return "not_found"
    if _grounding_note(answer, context):
        return "ungrounded"
    return "verified" if _figures(answer) else "no_figures"


def _verified(answer: str, context: str) -> bool:
    return bool(_figures(answer)) and not _grounding_note(answer, context)


def _verify_note(answer: str, context: str, local_answer: Optional[str]) -> str:
    route, label = llm.last_route()
    note = _grounding_note(answer, context)
    if not note and route == "cloud":
        note = (f"\n\n---\n✓ *Answered by {label}. Every figure in this answer appears in the source.*"
                if _figures(answer) else f"\n\n---\n*Answered by {label}.*")
    elif route == "cloud":
        note = f"\n\n---\n*Answered by {label}.*" + note.replace("\n\n---\n", "\n\n", 1)
    if local_answer and not _NOT_FOUND_RE.search(local_answer):
        differ = sorted(_figures(local_answer) - _figures(answer))
        if differ and _figures(answer):
            note += (f"\n\n⚖️ *The local model gave different figures ({', '.join(differ[:4])}), which don't match "
                     f"the source; the answer above was checked against the documents.*")
    return note


def _citation_check(answer: str, context: str) -> str:
    """Streamed answers can't be edited: name the right source after the text."""
    fix = _citation_fix(answer, context)
    return f"\n\n📄 *Source: [{fix[1]}]*" if fix else ""


def _citation_fix(answer: str, context: str):
    """(wrong_cited, right_doc) when the answer's figures are only in an uncited document."""
    heads = list(_DOC_HEADER_RE.finditer(context))
    if len(heads) < 2:
        return None
    docs: Dict[str, str] = {}
    for i, h in enumerate(heads):
        name = h.group(1) or h.group(2)
        end = heads[i + 1].start() if i + 1 < len(heads) else len(context)
        docs[name] = docs.get(name, "") + re.sub(r"(?<=\d),(?=\d)", "", context[h.end():end])
    nums = [n for n in (_digits(x) for x in dict.fromkeys(_NUMBER_RE.findall(_strip_citations(answer))))
            if len(n.replace("-", "").replace(".", "")) >= 2]
    if not nums:
        return None
    cited = [d for d in docs if d in answer]
    has_all = [d for d, body in docs.items() if all(_has_number(n, body) for n in nums)]
    if cited and not any(d in has_all for d in cited) and len(has_all) == 1:
        return cited[0], has_all[0]
    return None

# Maps query keywords → doc_type values in the DB.
# When the query explicitly names a document type, we do a direct doc_type SQL
# lookup to pull those docs' chunks first, bypassing cosine-sim ranking entirely.
# This fixes the "birth certificate OCR chunks have low sim to 'birth certificate'"
# problem — the chunk text has names/dates but not the document type label itself.
_QUERY_DOCTYPE_KEYWORDS: List[tuple] = [
    # personal identity
    (["birth certificate", "birth cert"],               "birth_certificate"),
    (["passport"],                                       "passport"),
    (["driver", "driving license", "driver's license"], "drivers_license"),
    (["social security card", "ssn card"],              "social_security_card"),
    (["marriage certificate", "marriage cert"],          "marriage_certificate"),
    (["death certificate", "death cert"],               "death_certificate"),
    (["vaccination record", "immunization record", "vaccine record"], "vaccination_record"),
    (["naturalization certificate", "naturalization"],  "naturalization_certificate"),
    # immigration
    (["visa document", "visa letter"],                  "visa_document"),
    (["uscis receipt", "i-797", "receipt notice"],      "uscis_receipt"),
    (["green card", "permanent resident"],              "immigration"),
    (["h-1b", "h1b"],                                   "immigration"),
    # financial
    (["pay stub", "paystub", "pay slip"],               "pay_stub"),
    (["bank statement", "bank stmt"],                   "bank_statement"),
    (["credit card statement"],                         "credit_card_statement"),
    (["w-2", "w2 form", "wage statement"],              "w2"),
    (["1099"],                                          "tax_form"),
    (["tax return", "tax form"],                        "tax_form"),
    (["property tax", "tax bill"],                      "property_tax_bill"),
    (["invoice"],                                       "invoice"),
    (["expense report"],                                "expense_report"),
    # insurance
    (["insurance policy", "policy document"],           "insurance_policy"),
    (["declarations page", "dec page", "dec sheet"],    "declarations_page"),
    (["explanation of benefits", "eob"],                "explanation_of_benefits"),
    (["insurance claim", "claim form"],                 "insurance_claim"),
    # real estate / mortgage
    (["mortgage statement", "mortgage document"],       "mortgage_document"),
    (["closing disclosure", "closing statement"],       "closing_disclosure"),
    (["property deed", "deed of trust"],                "property_deed"),
    (["lease agreement", "rental agreement"],           "lease_agreement"),
    # medical
    (["medical record", "medical report"],              "medical_record"),
    (["medical bill", "hospital bill"],                 "medical_bill"),
    (["lab report", "lab result"],                      "lab_report"),
    (["prescription"],                                  "prescription"),
    # employment / HR
    (["offer letter"],                                  "offer_letter"),
    (["employment contract", "employment agreement"],   "employment_contract"),
    (["resume", "cv ", "curriculum vitae"],             "resume"),
    # corporate
    (["annual report"],                                 "annual_report"),
    (["quarterly report"],                              "quarterly_report"),
    (["utility bill", "electricity bill", "gas bill", "water bill"], "utility_bill"),
    # education
    (["transcript", "grade report"],                    "transcript"),
]


def _doctype_from_query(question: str) -> Optional[str]:
    """Extract an explicit doc_type from query text using keyword matching."""
    q = question.lower()
    for phrases, doc_type in _QUERY_DOCTYPE_KEYWORDS:
        if any(p in q for p in phrases):
            return doc_type
    return None


def _fetch_chunks_by_doctype(doc_type: str, doc_ids: List[str], top_k: int = 20) -> List[Dict]:
    """Pull chunks from docs classified as doc_type, bypassing cosine-sim floor."""
    from sqlalchemy import text
    tenant_id = get_current_tenant_id() or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        doc_filter = ""
        params: Dict[str, Any] = {"doc_type": doc_type, "tenant_id": tenant_id, "limit": top_k}
        if doc_ids:
            placeholders = ", ".join(f":doc_{i}" for i in range(len(doc_ids)))
            doc_filter = f"AND d.id::text IN ({placeholders})"
            for i, did in enumerate(doc_ids):
                params[f"doc_{i}"] = did
        sql = text(f"""
            SELECT
                dc.chunk_text           AS text,
                d.id::text              AS doc_id,
                d.filename              AS filename,
                d.doc_type              AS doc_type,
                0.99::float             AS similarity
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            WHERE d.doc_type = :doc_type
              AND d.status = 'completed'
              AND d.tenant_id = :tenant_id
              {doc_filter}
            ORDER BY dc.chunk_index ASC
            LIMIT :limit
        """)
        rows = db.execute(sql, params).fetchall()
        return [{"text": r.text, "doc_id": r.doc_id, "filename": r.filename,
                 "doc_type": r.doc_type, "similarity": r.similarity} for r in rows]
    except Exception as e:
        logger.warning(f"_fetch_chunks_by_doctype failed: {e}")
        return []
    finally:
        db.close()


_NAME_STOP = {"what", "when", "where", "which", "who", "how", "can", "could", "please", "show", "tell", "give",
              "provide", "california", "department", "annual", "report", "document", "details", "summary", "about"}


def _question_names(question: str) -> List[str]:
    words = (re.sub(r"['’]s$", "", w) for w in re.findall(r"\b[A-Z][A-Za-z0-9&'’-]{3,}\b", question or ""))
    return [w for w in dict.fromkeys(words) if len(w) > 3 and w.lower() not in _NAME_STOP]


def _mentions(text: str, names: List[str]) -> bool:
    return any(re.search(r"\b" + re.escape(n) + r"\b", text or "", re.I) for n in names)


def _doctype_family(doc_type: str) -> List[str]:
    """A generic type covers the specific ones the classifier now stores (tax_form → form_1099_int, …),
    and siblings sharing a stem (mortgage_document ↔ mortgage_statement, pay_stub ↔ payroll)."""
    if doc_type == "tax_form":
        from app.services.tax.irs_forms import FORM_DOC_TYPES
        return ["tax_form"] + sorted({t for t in FORM_DOC_TYPES.values() if t != "w2"})
    stem = doc_type.split("_")[0]
    family = {doc_type} | {t for t in _known_doc_types() if t.split("_")[0] == stem}
    if stem in ("pay", "payroll", "paystub"):
        family |= {"pay_stub", "payroll", "payroll_report"}
    return sorted(family)


_DOC_TYPES_CACHE: Dict[str, Any] = {"at": 0.0, "types": set()}


def _known_doc_types() -> set:
    if time.time() - _DOC_TYPES_CACHE["at"] > 300:
        try:
            from sqlalchemy import text
            from app.core.database import SessionLocal
            db = SessionLocal()
            try:
                _DOC_TYPES_CACHE["types"] = {r[0] for r in db.execute(text(
                    "SELECT DISTINCT doc_type FROM documents WHERE doc_type IS NOT NULL"))}
            finally:
                db.close()
            _DOC_TYPES_CACHE["at"] = time.time()
        except Exception:
            pass
    return _DOC_TYPES_CACHE["types"]


def _multi_search(plan: Dict, doc_ids: List[str], prefer_doc_ids: Optional[List[str]] = None) -> List[Dict]:
    """
    Domain-aware multi-query search.
    1. Anchors queries to named_subject when present.
    2. Injects domain seed queries based on doc_type_hint so the vector space
       is pulled toward the right section of each document type.
    3. Merges, deduplicates, re-ranks by similarity.
    """
    queries = list(plan.get("search_queries", []))
    named_subject = (plan.get("named_subject") or "").strip()
    doc_type      = (plan.get("doc_type_hint") or "general").lower()

    # Anchor primary query to the named subject so the embedding search targets them
    if named_subject and queries:
        queries[0] = f"{named_subject} {queries[0]}"
        # Add a bare name query as a fallback pass
        queries.insert(1, named_subject)

    # Inject domain-specific seed queries (up to 2 most relevant seeds)
    seeds = _DOMAIN_QUERY_SEEDS.get(doc_type, [])
    if not seeds:
        # Try partial match (e.g. "insurance_claim" → try "insurance" too)
        for key in _DOMAIN_QUERY_SEEDS:
            if key in doc_type or doc_type in key:
                seeds = _DOMAIN_QUERY_SEEDS[key]
                break
    for seed in seeds[:2]:
        q = f"{named_subject} {seed}" if named_subject else seed
        if q not in queries:
            queries.append(q)

    if not queries:
        return []

    seen_texts: set = set()
    merged: List[Dict] = []

    # Pass 0: doc-type keyword bridge.
    # When the query explicitly names a document type ("birth certificate", "passport",
    # "pay stub", etc.), fetch ALL chunks from docs of that type directly via SQL.
    # This bypasses cosine-sim entirely — OCR chunks contain data (names, dates) but
    # NOT the document-type label, so semantic search systematically misses them.
    question_text = plan.get("_question", queries[0] if queries else "")
    # Hard type limit only when the user named the type ("passport", "pay stub");
    # the planner's guess is a preference — it guessed "annual_report" for the
    # CDTFA report (stored as government_form) and hid the right document.
    explicit_doctype = _doctype_from_query(question_text)
    if not explicit_doctype and doc_type not in ("general", ""):
        explicit_doctype = doc_type if doc_type in {dt for _, dt in _QUERY_DOCTYPE_KEYWORDS} else None
    # Hybrid retrieval: vector + keyword candidates → RRF → cross-encoder rerank.
    # A named doc type narrows the pool; ranking still picks the passages
    # (the old shortcut returned the first 30 chunks — useless for a 10-K).
    from app.services.hybrid_search import hybrid_search
    q = question_text or queries[0]
    # Documents the user (or the conversation) chose are the scope — a type guess
    # on top of that only hides them (CDTFA report is government_form, not tax_form).
    doc_types = _doctype_family(explicit_doctype) if explicit_doctype and not doc_ids else None
    scope = list(doc_ids or []) or list(prefer_doc_ids or [])
    # Documents whose filename shares words with the question ("Sacramento County
    # property tax bill" → Sacramento-County-Property-Tax-….pdf) rank first.
    by_name = _filename_matches(q)
    plan["_named_docs"] = by_name
    if scope and not doc_ids and by_name:
        scope = list(dict.fromkeys(scope + by_name))   # a filter guess must not hide a named document
    # A type named in the question ranks those documents first but never hides the
    # rest: "mortgage statement" mapped to mortgage_document and hid the statement
    # stored as mortgage_statement. Stored types are guesses too.
    results = hybrid_search(q, queries, doc_ids=scope or None, prefer_doc_ids=by_name or None,
                            prefer_doc_types=doc_types, keep_doc_ids=by_name or None, top_n=8)
    if not results and scope and not doc_ids:
        results = hybrid_search(q, queries, top_n=8)   # filter's guess was wrong; never drop a chosen scope
    elif doc_types and results:
        plan["_doctype_bridged"] = True  # tells _synthesize to skip name/relevance filters

    # Misfiled documents: the scoped passages don't mention a specific name from
    # the question ("City of Trinidad") — e.g. the CDTFA report is stored as
    # government_form, not annual_report. Search everything once; switch only if
    # that finds the name.
    names = _question_names(q)
    if names and (doc_types or scope) and not any(_mentions(r["text"], names) for r in results) and not doc_ids:
        wide = hybrid_search(q, queries, top_n=8)
        if any(_mentions(r["text"], names) for r in wide):
            logger.info(f"Scoped results lacked {names}; using unscoped search that mentions them")
            results = wide
            plan["_doctype_bridged"] = False
    logger.info(f"Hybrid search: {len(results)} passages (doc_types={doc_types}) "
                f"top={[(r['filename'][:30], r['similarity']) for r in results[:3]]}")
    return results


_NAME_STOP = {"what", "when", "where", "which", "does", "with", "from", "that", "this", "have", "were",
              "much", "many", "amount", "total", "show", "tell", "give", "about", "document", "documents",
              "file", "pdf", "statement", "form", "bill", "report", "2023", "2024", "2025", "2026"}


def _name_words(s: str) -> set:
    # "H-1B" and "H1B", "I-129" and "I129" are the same code
    s = re.sub(r"\b([a-z]{1,2})-(\d[a-z0-9]{0,3})\b", r"\1\2", s.lower())
    return {w for w in re.findall(r"[a-z0-9]+", s)
            if (len(w) >= 4 or (len(w) >= 2 and re.search(r"\d", w) and re.search(r"[a-z]", w)))
            and w not in _NAME_STOP}


def _filename_matches(question: str) -> List[str]:
    """Doc ids whose filename shares ≥2 distinctive words with the question, or one
    form/visa code (h1b, i129, w2) — codes like that name the document."""
    words = _name_words(question)
    if not words:
        return []
    try:
        from sqlalchemy import text
        from app.core.database import SessionLocal
        from app.core.tenant import get_current_tenant_id
        db = SessionLocal()
        try:
            rows = db.execute(text("SELECT id::text AS id, filename FROM documents "
                                   "WHERE tenant_id = :t AND status = 'completed'"),
                              {"t": get_current_tenant_id()}).fetchall()
        finally:
            db.close()
    except Exception:
        return []
    hits = []
    for r in rows:
        shared = words & _name_words(r.filename or "")
        has_code = any(re.search(r"\d", w) for w in shared)
        if len(shared) >= 2 or has_code:
            hits.append((len(shared) + has_code, r.id))
    return [i for _, i in sorted(hits, reverse=True)[:3]]


# Whole-document reading. Search picks the documents; the model then reads each
# one in full instead of a few ranked fragments. Fragments lose table rows and
# let a keyword match in an unrelated document ("PARCEL NUMBER" on a tax bill)
# outrank the real answer ("Parcels 97.98" on an invoice).
# NUM_CTX 8192 tokens ≈ 14k chars of document text after prompt + answer room.
_FULL_DOC_BUDGET_CHARS = int(os.getenv("FULL_DOC_BUDGET_CHARS", "18000"))
_FULL_DOC_MAX_DOCS     = 3


def _expand_to_full_documents(results: List[Dict], plan: Dict) -> List[Dict]:
    if not results:
        return results
    ranked_ids: List[str] = []
    for r in results:
        did = str(r.get("doc_id") or "")
        if did and did not in ranked_ids:
            ranked_ids.append(did)
    if not ranked_ids:
        return results

    try:
        from sqlalchemy import text
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            rows = db.execute(text("""
                SELECT id::text AS id, filename, doc_type, raw_text
                FROM documents WHERE id::text = ANY(:ids)
            """), {"ids": ranked_ids[:6]}).fetchall()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Full-document expansion skipped: {e}")
        return results
    by_id = {r.id: r for r in rows}

    full: List[Dict] = []
    used = 0
    for did in ranked_ids:
        doc = by_id.get(did)
        body = (doc.raw_text or "").strip() if doc else ""
        if not body or len(body) > _FULL_DOC_BUDGET_CHARS - used:
            continue                     # too big for the budget: keep its passages instead
        best = max(float(r.get("similarity") or 0) for r in results if str(r.get("doc_id")) == did)
        full.append({"doc_id": did, "filename": doc.filename, "doc_type": doc.doc_type,
                     "text": body, "similarity": best, "chunk_index": -1, "full_document": True})
        used += len(body)
        if len(full) >= _FULL_DOC_MAX_DOCS:
            break
    if not full:
        return results

    kept = {f["doc_id"] for f in full}
    named = set(plan.get("_named_docs") or [])
    if named & kept:
        # The question named this document and it's read whole: other documents'
        # passages only distract (a 290k-char tax guide buried the actual tax bill).
        full = [f for f in full if f["doc_id"] in named] + [f for f in full if f["doc_id"] not in named]
        plan["_full_docs"] = True
        logger.info(f"Full-document context (named): {[f['filename'][:30] for f in full]}")
        return [f for f in full if f["doc_id"] in named]
    # Passages from documents too large to include whole still go in, after the full ones
    extra = [r for r in results if str(r.get("doc_id")) not in kept]
    plan["_full_docs"] = True
    logger.info(f"Full-document context: {[f['filename'][:30] for f in full]} ({used:,} chars) "
                f"+ {len(extra)} passages from larger documents")
    return full + extra


# ── Ask single document ───────────────────────────────────────────────────────

_PAGE_TRAVERSAL_PATTERNS = (
    "page by page", "page-by-page", "all pages", "every page",
    "full document", "entire document", "whole document",
    "section by section", "chapter by chapter", "all sections",
)


def _ask_document(doc_id: str, question: str) -> str:
    from sqlalchemy import text
    db = SessionLocal()
    try:
        q_lower = question.lower()
        is_traversal = any(p in q_lower for p in _PAGE_TRAVERSAL_PATTERNS)

        if is_traversal:
            # Structural traversal: fetch ALL chunks ordered by page, not by similarity.
            # Paginate in batches of 60 chunks to stay within context window.
            rows = db.execute(text("""
                SELECT chunk_text, page_number, chunk_index
                FROM document_chunks
                WHERE document_id = :doc_id
                  AND chunk_text IS NOT NULL
                ORDER BY page_number ASC, chunk_index ASC
                LIMIT 120
            """), {"doc_id": doc_id}).fetchall()
        else:
            vec = _get_embedding_model().encode(question).tolist()
            rows = db.execute(text("""
                SELECT c.chunk_text, 1 - (c.embedding <=> CAST(:vec AS vector(768))) AS sim
                FROM document_chunks c
                WHERE c.document_id = :doc_id
                  AND c.embedding IS NOT NULL
                  AND vector_dims(c.embedding) = 768
                  AND 1 - (c.embedding <=> CAST(:vec AS vector(768))) >= 0.15
                ORDER BY sim DESC
                LIMIT 8
            """), {"vec": str(vec), "doc_id": doc_id}).fetchall()

        if not rows:
            return "No relevant content found for this question in the document."

        if is_traversal:
            # Group by page_number for a cleaner context block
            from collections import defaultdict
            pages: dict = defaultdict(list)
            for r in rows:
                pages[r.page_number].append(r.chunk_text)
            context_parts = []
            for pg in sorted(pages.keys()):
                context_parts.append(f"=== Page {pg} ===\n" + "\n".join(pages[pg]))
            context = "\n\n".join(context_parts)[:18000]
        else:
            context = "\n\n".join(r.chunk_text for r in rows if r.chunk_text)[:5000]

        if is_traversal:
            prompt = f"""You are a precise document analyst. The user wants a complete page-by-page summary.

RULES:
1. Write one ## Page N heading for each page in the excerpts, in order.
2. Under each heading, list every topic, key fact, figure, table, and section found on that page.
3. Do NOT skip any pages present in the excerpts.
4. If a page has only boilerplate or no notable content, write: "No significant content."
5. Do NOT add any information not present in the excerpts.

Question: {question}

Document excerpts (ordered by page):
{context}

Page-by-page breakdown:"""
        else:
            prompt = f"""You are a precise document analyst.
Answer the question using ONLY the document excerpts provided below.

STRICT RULES:
1. Every fact you state MUST appear verbatim in the excerpts. No outside knowledge.
2. If the answer is not in the excerpts, write exactly: [NOT FOUND IN DOCUMENT]
3. Do NOT infer, guess, or generalize beyond what is explicitly stated.
4. Copy numbers, dates, and names exactly as they appear — no rounding or paraphrasing.
5. Do not describe or explain what the document "typically" contains.

Question: {question}

Document excerpts:
{context}

Answer:"""
        resp = llm.chat(role="answer", 
            model=_active_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX, "num_predict": 2400 if is_traversal else 500, "temperature": 0.1},
        )
        return resp["message"]["content"].strip()
    except Exception as e:
        logger.error(f"ask_document error: {e}")
        return f"Error querying document: {str(e)}"
    finally:
        db.close()


# ── Synthesize ────────────────────────────────────────────────────────────────

# Domain-specific extraction instructions injected into the synthesis prompt.
# These tell the LLM exactly what fields to look for per document type so it
# never free-associates from unrelated passages.
_DOMAIN_EXTRACT_HINTS: Dict[str, str] = {
    # ── HR / Resume ──────────────────────────────────────────────────────────
    "hr_document": (
        "This is a resume / HR document. Extract and present:\n"
        "  • Full name, current title, contact details\n"
        "  • Employment history: each role → company, title, dates, key responsibilities\n"
        "  • Education: degree, institution, graduation year\n"
        "  • Skills, technologies, certifications\n"
        "  • Notable achievements or projects\n"
        "Only include facts that appear verbatim in the excerpts."
    ),
    "employee_roster": (
        "This is an employee roster. Extract: employee name, department, job title, "
        "hire date, salary grade. Only facts present in the excerpts."
    ),
    # ── Finance ──────────────────────────────────────────────────────────────
    "pay_stub": (
        "This is a pay stub. Extract:\n"
        "  • Employee name, pay period, pay date\n"
        "  • Gross pay, all deductions (federal tax, state tax, FICA, health, 401k, other)\n"
        "  • Net pay, YTD totals\n"
        "State exact dollar amounts. Do not estimate."
    ),
    "payroll_report": (
        "This is a payroll report. Extract: total payroll, number of employees paid, "
        "gross wages, total deductions, net pay, pay period. Show per-employee breakdowns if present."
    ),
    "bank_statement": (
        "This is a bank statement. Extract:\n"
        "  • Account number (last 4 digits), statement period\n"
        "  • Opening balance, closing balance\n"
        "  • Total deposits, total withdrawals\n"
        "  • Notable individual transactions with date, description, amount"
    ),
    "credit_card_statement": (
        "This is a credit card statement. Extract: statement date, previous balance, "
        "payments, new charges, closing balance, minimum payment due, due date. "
        "List top individual charges by amount."
    ),
    "invoice": (
        "This is an invoice. Extract: invoice number, vendor name, bill-to, invoice date, "
        "due date, line items with quantity/unit price, subtotal, tax, total amount due."
    ),
    "purchase_order": (
        "This is a purchase order. Extract: PO number, vendor, buyer, order date, "
        "delivery date, line items with quantities and prices, total value."
    ),
    "expense_report": (
        "This is an expense report. Extract: employee name, report period, each expense "
        "with date/category/amount/description, total claimed, approval status."
    ),
    "financial_statement": (
        "This is a financial statement. Extract:\n"
        "  • Revenue / Net Sales\n"
        "  • Gross profit, Operating income, Net income\n"
        "  • Total assets, Total liabilities, Equity\n"
        "  • Cash and cash equivalents\n"
        "  • Period covered and year-over-year change if present"
    ),
    "annual_report": (
        "This is an annual report. Extract: company name, fiscal year, total revenue, "
        "net income, EPS, major business segments, key risks, board/executive highlights, "
        "dividend information. Stay strictly within what the excerpts state."
    ),
    "quarterly_report": (
        "This is a quarterly report. Extract: quarter and year, revenue, net income, "
        "EPS, guidance for next quarter, segment performance, notable one-time items."
    ),
    # ── Insurance ────────────────────────────────────────────────────────────
    "insurance_policy": (
        "This is an insurance policy. Extract:\n"
        "  • Policy number, named insured, policy period (effective → expiry)\n"
        "  • Line of business / coverage type\n"
        "  • Coverage limits per occurrence and aggregate\n"
        "  • Deductible amounts\n"
        "  • Premium amount and payment schedule\n"
        "  • Carrier / insurer name\n"
        "  • Exclusions or endorsements mentioned"
    ),
    "declarations_page": (
        "This is a declarations page. Extract: policy number, insured name, property address, "
        "coverage types, limits, deductibles, premium, policy period, carrier."
    ),
    "insurance_claim": (
        "This is an insurance claim. Extract: claim number, claimant name, policy number, "
        "incident date, claim type, description of loss, estimated/settled amount, "
        "adjuster name, current status."
    ),
    "insurance": (
        "This is an insurance document. Extract all policy identifiers, coverage details, "
        "limits, premiums, dates, and parties involved."
    ),
    # ── Legal / CLM ──────────────────────────────────────────────────────────
    "contract": (
        "This is a contract. Extract:\n"
        "  • Parties (full legal names)\n"
        "  • Effective date and term/expiry\n"
        "  • Key obligations of each party\n"
        "  • Payment terms / contract value\n"
        "  • Termination clauses\n"
        "  • Penalty / indemnification clauses\n"
        "  • Governing law and jurisdiction"
    ),
    "legal": (
        "This is a legal document. Extract: parties, dates, key rights and obligations, "
        "any monetary amounts, deadlines, and governing jurisdiction. "
        "Quote specific clause language where relevant."
    ),
    # ── Medical ──────────────────────────────────────────────────────────────
    "medical_record": (
        "This is a medical record. Extract: patient name, date of service, provider, "
        "chief complaint, diagnosis (ICD codes if present), treatment plan, "
        "medications prescribed with dosage, follow-up instructions."
    ),
    "medical": (
        "This is a medical document. Extract: patient details, diagnosis, treatment, "
        "medications, dates, and ordering physician."
    ),
    # ── Real Estate ──────────────────────────────────────────────────────────
    "mortgage_document": (
        "This is a mortgage document. Extract: borrower name, property address, "
        "loan amount, interest rate, loan type (fixed/ARM), term, lender name, "
        "origination date, monthly payment."
    ),
    "closing_disclosure": (
        "This is a closing disclosure. Extract: purchase price, loan amount, "
        "interest rate, closing costs breakdown, cash to close, prepaid items, "
        "seller credits, lender name, closing date."
    ),
    "real_estate": (
        "This is a real estate document. Extract: property address, buyer, seller, "
        "purchase price, closing date, key terms and contingencies."
    ),
    # ── Immigration ──────────────────────────────────────────────────────────
    "immigration": (
        "This is an immigration document. Extract: beneficiary name, petitioner/sponsor, "
        "visa class, priority date, receipt number, USCIS office, approval/denial date, "
        "validity period, authorized stay."
    ),
    "uscis_receipt": (
        "This is a USCIS receipt notice. Extract: receipt number, petition type, "
        "beneficiary, petitioner, filing date, expected processing time, case status."
    ),
    # ── Tax ──────────────────────────────────────────────────────────────────
    "birth_certificate": (
        "This is a birth certificate or vital record. Extract:\n"
        "  • Full name of the person\n"
        "  • Date of birth (exact date)\n"
        "  • Place of birth (city, county, state, country)\n"
        "  • Father's name, Mother's name (maiden name)\n"
        "  • Certificate/registration number\n"
        "  • Issuing authority and date issued\n"
        "State the date of birth exactly as it appears in the document."
    ),
    "passport": (
        "This is a passport. Extract: full name, date of birth, nationality, "
        "passport number, issue date, expiry date, place of birth, issuing country."
    ),
    "drivers_license": (
        "This is a driver's license. Extract: full name, date of birth, address, "
        "license number, issue date, expiry date, class/restrictions."
    ),
    "property_tax_bill": (
        "This is a county property tax bill. Extract:\n"
        "  • Parcel number, tax rate area, bill number\n"
        "  • Property address (situs)\n"
        "  • Taxable value (land + improvements)\n"
        "  • First installment: amount and due/delinquent date\n"
        "  • Second installment: amount and due/delinquent date\n"
        "  • Total amount due\n"
        "  • Fiscal year covered\n"
        "NOTE: A property tax bill shows the amount BILLED/DUE for that tax year. "
        "When the user asks about 'last paid property tax' or 'property tax payment', "
        "report the total amount due on the bill as the property tax amount. "
        "The bill amount IS the property tax — state it directly."
    ),
    "tax_form": (
        "This is a tax form. Extract: taxpayer name, tax year, filing status, "
        "total income, adjustments, taxable income, total tax, withholding, "
        "refund or amount owed."
    ),
    "w2": (
        "This is a W-2. Extract: employee name, SSN (last 4), employer name, EIN, "
        "wages/tips/other compensation, federal income tax withheld, "
        "social security wages, Medicare wages, state wages and tax withheld."
    ),
    "social_security_card": (
        "This is a Social Security card. Extract: full name on card, "
        "Social Security Number (if visible — may be masked), "
        "any employment eligibility restrictions (e.g. 'not valid for employment')."
    ),
    "marriage_certificate": (
        "This is a marriage certificate or license. Extract: "
        "full names of both parties (groom/bride/spouses), date of marriage, "
        "place of marriage (city, county, state), officiant name, witness names, "
        "certificate/license number, issuing authority."
    ),
    "death_certificate": (
        "This is a death certificate. Extract: decedent's full name, date of death, "
        "place of death (city, county, state), cause of death (if present), "
        "manner of death, certifying physician, "
        "certificate number, issuing authority and date issued."
    ),
    "vaccination_record": (
        "This is a vaccination or immunization record. Extract: "
        "patient name, date of birth, each vaccine name and type, "
        "date(s) administered, dose number, lot number, "
        "administering provider/clinic, and any notes about upcoming doses."
    ),
    "naturalization_certificate": (
        "This is a naturalization or citizenship certificate. Extract: "
        "full name, date of naturalization, certificate number, "
        "former nationality, date and place of birth, "
        "issuing court and USCIS district."
    ),
    # ── General fallback ─────────────────────────────────────────────────────
    "general": "",
}


def _domain_extract_hint(doc_type_hint: str) -> str:
    """Return the domain-specific extraction instruction for a given doc_type_hint."""
    hint = _DOMAIN_EXTRACT_HINTS.get(doc_type_hint, "")
    if hint:
        return hint
    # Partial match
    for key, val in _DOMAIN_EXTRACT_HINTS.items():
        if key in doc_type_hint or doc_type_hint in key:
            return val
    return ""


def _intent_instruction(intent: str, field: str, aggregation) -> str:
    """Map intent + aggregation to a precise synthesis instruction."""
    # Normalize: LLM sometimes returns a list e.g. ["rank"] instead of "rank"
    if isinstance(aggregation, list):
        aggregation = aggregation[0] if aggregation else None
    fl = f" for '{field}'" if field else ""
    m = {
        ("aggregate", "sum"):     f"TASK: Compute the TOTAL{fl} by summing all values found across documents. Show each value with its source, then the grand total.",
        ("aggregate", "count"):   f"TASK: COUNT every instance of {field or 'the item'} across all documents. List each one and give the final count.",
        ("aggregate", "avg"):     f"TASK: Calculate the AVERAGE{fl}. List each value with its source, show the formula, and state the average.",
        ("aggregate", "max"):     f"TASK: Find the MAXIMUM{fl} across all documents. State which document it came from and the exact value.",
        ("aggregate", "min"):     f"TASK: Find the MINIMUM{fl} across all documents. State which document it came from and the exact value.",
        ("aggregate", "list"):    f"TASK: LIST every instance of {field or 'the item'} found — one bullet per entry with source document and value.",
        ("compare", None):        f"TASK: COMPARE{fl} across documents. For each document list its value(s) separately, then summarize differences and similarities.",
        ("multi_period", None):   f"TASK: Break down{fl} BY PERIOD (month/quarter/year). Show each period separately, then the overall pattern.",
        ("trend", None):          f"TASK: Describe the TREND{fl}. Identify highest and lowest values, which period they belong to, and the direction of change.",
        ("rank", None):           f"TASK: RANK results{fl} from highest to lowest. Number each entry with its source document.",
        ("summarize", None):      "TASK: Write a concise SUMMARY. Use one bullet-pointed section per document covering its key facts, then a brief overall conclusion.",
        ("verify", None):         f"TASK: VERIFY whether the condition holds{fl}. State YES or NO clearly, show the values found, and explain any discrepancy.",
        ("calculate", None):      f"TASK: CALCULATE{fl}. Show the formula, input values from the documents, and the result.",
        ("exists_check", None):   f"TASK: Answer YES or NO — does {field or 'the item'} exist in the documents? If yes, state where and the exact value.",
        ("anomaly", None):        f"TASK: Find ANOMALIES{fl}. Flag outliers, mismatches, or values that differ significantly. Explain why each is unusual.",
        ("forecast", None):       f"TASK: PROJECT{fl} into the future based on historical data. Show the historical pattern and your projection with reasoning.",
        ("cross_doc", None):      "TASK: COMBINE information across multiple document types to answer. Extract the relevant fact from each document type, then synthesize a unified answer.",
        ("info_retrieve", None):  f"TASK: Answer the factual question{fl} precisely. Quote or cite specific text where possible.",
        ("field_extract", None):  f"TASK: Extract the exact value of '{field or 'the requested field'}' from the documents. State the value and which document it came from.",
        ("list", None):           f"TASK: LIST all instances of {field or 'the requested item'} found across documents, with source and value for each.",
    }
    key = (intent, aggregation)
    if key in m:
        return m[key]
    # Fallback: try without aggregation
    key2 = (intent, None)
    return m.get(key2, f"TASK: Answer the question about {field or 'the topic'} using only what is stated in the documents.")


def _format_instruction(question: str) -> tuple:
    """Detect the user's requested output format. Returns (instruction_str, num_predict)."""
    q = question.lower()
    if any(p in q for p in ("page by page", "page-by-page", "all pages", "every page",
                              "full document", "entire document", "whole document",
                              "section by section", "chapter by chapter")):
        return (
            "FORMAT: Write one ## Page N or ## Section heading per chunk/page found. "
            "Under each heading list every key fact, figure, table summary, and topic on that page. "
            "Cover ALL pages sequentially from first to last — do not skip any. "
            "If a page has no notable content write 'No significant content.'",
            2400
        )
    # "as a table" asks for a table; "Table 2" names one in the document
    if re.search(r"\b(as|in|into|make|show|give)\s+(me\s+)?(a\s+)?table\b|\btabular\b|\bgrid\b|\bspreadsheet\b", q):
        return (
            "FORMAT: Output a markdown table whose header row names the real columns "
            "(for example | Item | FY 2023-24 | FY 2022-23 |), then a --- separator row. "
            "One row per document or period. Include all numeric values.", 1100
        )
    if any(w in q for w in ("json", "object", "dict", "key value", "key-value")):
        return (
            "FORMAT: Output ONLY valid JSON — an array of objects, one per document. "
            "Numeric fields as numbers, dates as strings. No prose outside the JSON block.", 1100
        )
    if any(w in q for w in ("csv", "comma separated", "comma-separated", "excel")):
        return (
            "FORMAT: Output data as CSV with a header row. Wrap in a ```csv code block.", 1000
        )
    if any(w in q for w in ("detailed report", "full report", "summary report")):
        return (
            "FORMAT: Write a structured report: ## Summary → ## Details (one sub-section per "
            "document) → ## Key Findings → ## Recommendations. Use headers and bullets.", 1400
        )
    if any(w in q for w in ("bullet", "bullet point", "list", "enumerate", "all the", "every ")):
        return ("FORMAT: Use a bullet-point list with one item per row/entry in the excerpts. "
                "Include EVERY entry from the relevant table or list, in order — do not stop early, "
                "skip rows, or summarize them. Keep each item short: name and value.", 3000)
    if any(w in q for w in ("compare", "comparison", "side by side", "vs", "versus")):
        return (
            "FORMAT: Present a side-by-side markdown comparison table, one column per document "
            "and one row per metric.", 1100
        )
    # Long multi-part questions need more tokens
    if len(question) > 400:
        # Count distinct sub-topics by question marks as a proxy
        num_parts = max(1, question.count("?"))
        token_budget = min(2400, 1200 + num_parts * 200)
        return (
            "FORMAT: Write a separate ## Section for each part of the question. "
            "Include every numeric value, date, and percentage found in the documents. "
            "Cite [filename] after every fact. Write [NOT FOUND] if data is absent.",
            token_budget
        )
    return ("", 900)


def _key_lines(question: str, parts: List[str], window: int = 160, limit: int = 6) -> str:
    """
    Snippets around capitalised names from the question ("Trinidad") found in the
    passages. Small models miss a name buried in pages of table text; a short
    highlighted block up front fixes that without changing what is cited.
    """
    names = [w for w in dict.fromkeys(re.sub(r"'s\b", "", m) for m in re.findall(r"\b[A-Z][A-Za-z0-9&'-]{3,}\b", question))
             if w.lower() not in {"what", "when", "where", "which", "who", "how", "can", "could", "please",
                                  "show", "tell", "give", "provide", "california", "department", "annual",
                                  "report", "document", "details", "summary", "about"}]
    snippets: List[str] = []
    ranking = _rank_table_pairs(question, parts)
    if ranking:
        snippets.append(ranking)
    for part in parts:
        src = re.match(r"\[([^\]]+)\]:", part)
        for name in names:
            for m in re.finditer(r"\b" + re.escape(name) + r"\b", part, re.I):
                a, b = max(0, m.start() - window), min(len(part), m.end() + window)
                snip = " ".join(part[a:b].split())
                if all(snip[:60] not in x for x in snippets):
                    snippets.append(f"- …{snip}… [{src.group(1) if src else 'excerpt'}]")
                if len(snippets) >= limit:
                    return "\n".join(snippets)

    # Term windows: the spot where most of the question's words meet a figure
    # ("Of the 7.25% uniform statewide rate…") — otherwise the model grabs a
    # nearby number from a table row (3.9375% General Fund share).
    terms = [w for w in dict.fromkeys(re.findall(r"[a-z][a-z0-9'-]{2,}", question.lower()))
             if w not in _KEY_STOP and w.rstrip("'s") not in _KEY_STOP]
    if len(terms) >= 2 and len(snippets) < limit:
        scored = []
        for part in parts:
            src = re.match(r"\[([^\]]+)\]:", part)
            low = part.lower()
            for t in terms:
                for m in re.finditer(r"\b" + re.escape(t), low):
                    a, b = max(0, m.start() - 90), min(len(part), m.end() + 90)
                    win = low[a:b]
                    hits = sum(1 for x in terms if re.search(r"\b" + re.escape(x), win))
                    near = low[max(0, m.start() - 40):m.end() + 40]
                    fig = 2 if re.search(r"\d[\d,.]*\s?%|\$\s?\d", near) else 0
                    if hits >= 2:
                        scored.append((hits + fig, " ".join(part[a:b].split()), src.group(1) if src else "excerpt"))
        for _, snip, fn in sorted(scored, key=lambda x: -x[0]):
            if len(snippets) >= limit:
                break
            if all(snip[30:90] not in x for x in snippets):
                snippets.append(f"- …{snip}… [{fn}]")
    return "\n".join(snippets)


_SUPERLATIVE_RE = re.compile(r"\b(most|highest|largest|biggest|greatest|top|maximum|max|least|lowest|"
                             r"smallest|fewest|minimum|min|bottom|rank|ranking|ranked)\b", re.I)
_LOW_RE = re.compile(r"\b(least|lowest|smallest|fewest|minimum|min|bottom)\b", re.I)
_PAIR_RE = re.compile(r"\b([A-Z][A-Za-z.'&-]+(?: [A-Z][A-Za-z.'&-]+){0,3})\s+\$\s?(\d[\d,]*(?:\.\d+)?)")


def _rank_table_pairs(question: str, parts: List[str]) -> str:
    """
    "Which county received the most…": a 7B model can't scan 58 table rows for
    the maximum (it answered Mendocino). Rank the name → $amount rows of the
    passage with the most such rows in code and hand the model the result.
    """
    if not _SUPERLATIVE_RE.search(question):
        return ""
    best, best_fn = [], ""
    for part in parts:
        pairs = []
        for name, amt in _PAIR_RE.findall(part):
            try:
                pairs.append((name.strip(), float(amt.replace(",", ""))))
            except ValueError:
                pass
        if len(pairs) > len(best):
            src = re.match(r"\[([^\]]+)\]", part)
            best, best_fn = pairs, src.group(1) if src else "excerpt"
    if len(best) < 5:
        return ""
    rows = sorted(dict(best).items(), key=lambda x: x[1], reverse=not _LOW_RE.search(question))[:5]
    order = "lowest" if _LOW_RE.search(question) else "highest"
    listed = "; ".join(f"{n} ${v:,.0f}" for n, v in rows)
    return f"- RANKED ({order} first, computed from all {len(dict(best))} rows of the table): {listed} [{best_fn}]"


_KEY_STOP = {"what", "when", "where", "which", "who", "whom", "how", "can", "could", "would", "should",
             "please", "show", "tell", "give", "provide", "list", "down", "the", "and", "for", "are",
             "was", "were", "is", "does", "did", "has", "have", "with", "from", "this", "that", "about",
             "into", "of", "in", "on", "my", "our", "your", "you", "me", "much", "many", "any", "all",
             "california", "document", "report", "annual", "details", "summary", "according"}


def _synthesize(question: str, plan: Dict, steps: List[Dict], page: int = 0) -> Dict:
    """
    Domain-aware, anti-hallucination synthesizer.

    Pipeline:
      1. Collect chunks from all search steps.
      2. Filter by named_subject (discard chunks that don't mention the person/entity).
      3. Detect confidence level from similarity scores.
      4. Build a prompt that combines:
           - universal grounding rules (no hallucination)
           - subject-specific constraints (if named_subject set)
           - domain extraction template (what fields to pull for this doc type)
           - intent instruction (what to compute / present)
           - format instruction (table / JSON / bullets / report)
      5. Call the LLM, return answer + sources.
    """
    named_subject: str    = (plan.get("named_subject") or "").strip()
    doc_type_hint: str    = (plan.get("doc_type_hint") or "general").lower()
    intent: str           = plan.get("intent", "info_retrieve")
    field: str            = plan.get("field") or ""
    aggregation: Optional[str] = plan.get("aggregation_op")
    reasoning: str        = plan.get("reasoning", "")

    # ── 1. Collect + filter chunks ──────────────────────────────────────────
    # Named-subject filtering works at DOCUMENT level, not chunk level.
    #
    # Why: a resume has the person's name only in the header chunk.
    # Skills, experience, and education chunks don't repeat the name —
    # filtering per-chunk would drop exactly the content we need.
    #
    # Strategy:
    #   Pass 1 — collect all chunks, group by filename.
    #   Pass 2 — if named_subject is set AND doc_type is "general" (unscoped),
    #             keep only documents where AT LEAST ONE chunk mentions the name.
    #             If doc_type is specific (hr_document, pay_stub, etc.) the
    #             domain-conflict exclusion in _score_doc already removed wrong
    #             documents upstream — trust it and keep all chunks.

    name_tokens = [t.lower() for t in named_subject.split() if len(t) > 1] if named_subject else []
    specific_domain = doc_type_hint and doc_type_hint != "general"

    # Pass 1: collect everything grouped by filename
    by_doc: Dict[str, List[dict]] = {}
    raw_chunks: List[dict] = []

    for step in steps:
        obs = step.get("observation")
        if not obs:
            continue
        if isinstance(obs, list):
            for item in obs[:14]:
                if not isinstance(item, dict):
                    continue
                fn  = item.get("filename", "")
                txt = item.get("text", "")
                sim = float(item.get("similarity", 0))
                full = bool(item.get("full_document"))
                if fn and txt:
                    by_doc.setdefault(fn, []).append({"fn": fn, "txt": txt, "sim": sim})
                    raw_chunks.append({"fn": fn, "txt": txt, "sim": sim, "full": full})
        elif isinstance(obs, str) and len(obs) > 20:
            raw_chunks.append({"fn": "", "txt": obs, "sim": 0.0})

    # Pass 2: decide which documents to admit
    def _doc_mentions_subject(chunks: List[dict]) -> bool:
        if not name_tokens:
            return True
        # Require ALL tokens for 1-2 token names (e.g. "Vijay Bomma" → need both).
        # For longer names (≥3 tokens) allow one miss. This prevents "John Doe"
        # matching documents that only mention a different "John".
        required = min(len(name_tokens), 2) if len(name_tokens) <= 3 else len(name_tokens) - 1
        all_text = " ".join(c["txt"].lower() for c in chunks)
        hits = sum(1 for tok in name_tokens if tok in all_text)
        return hits >= required

    # Admit all docs by default. Only filter by name when the search is completely
    # unscoped (no doc_type, not bridged) to avoid excluding the right document.
    doctype_bridged = plan.get("_doctype_bridged", False)
    admitted_docs: set = set(by_doc.keys())

    if name_tokens and not specific_domain and not doctype_bridged:
        # Unscoped: keep only docs where at least one chunk mentions the subject
        admitted_docs = {fn for fn, chunks in by_doc.items() if _doc_mentions_subject(chunks)}
        # If filter removed everything, admit all (better to answer than to refuse)
        if not admitted_docs:
            admitted_docs = set(by_doc.keys())

    context_parts: List[str] = []
    sources: set = set()
    all_similarities: List[float] = []

    for item in raw_chunks:
        fn, txt, sim = item["fn"], item["txt"], item["sim"]
        if fn and fn not in admitted_docs:
            continue
        clean_txt = _unbracket_redactions(_CITATION_NOISE_RE.sub('', txt))
        # Whole documents were budgeted by _expand_to_full_documents; passages cap at 5k
        if not item.get("full"):
            clean_txt = clean_txt[:5000]
        context_parts.append(f"[{fn}]: {clean_txt}" if fn else clean_txt)
        if fn:
            sources.add(fn)
        all_similarities.append(sim)

    # ── 2. Direct extraction shortcut ──────────────────────────────────────
    # For field_extract + named_subject (e.g. "bill on FINECUTS SALON"),
    # scan chunk text with regex instead of trusting the LLM to parse dense
    # transaction rows. Produces a precise answer instantly.
    if intent == "field_extract" and named_subject and context_parts:
        _amount_pat = re.compile(
            r'(?i)' + re.escape(named_subject) +
            r'.{0,120}?(?:amount|total|charge|bill|price|cost|due|balance)\s*[:\-]?\s*\$?([\d,]+(?:\.\d{1,2})?)',
            re.DOTALL
        )
        _rev_pat = re.compile(
            r'(?i)(?:amount|total|charge|bill|price|cost|due|balance)\s*[:\-]?\s*\$?([\d,]+(?:\.\d{1,2})?)' +
            r'.{0,120}?' + re.escape(named_subject),
            re.DOTALL
        )
        _direct_hits = []
        _direct_sources = set()
        for item in raw_chunks:
            fn, txt = item["fn"], item["txt"]
            if fn and fn not in admitted_docs:
                continue
            if named_subject.lower() not in txt.lower():
                continue
            for pat in (_amount_pat, _rev_pat):
                for m in pat.finditer(txt):
                    _direct_hits.append(m.group(1).replace(",", ""))
                    if fn:
                        _direct_sources.add(fn)
        if _direct_hits:
            # Deduplicate amounts and format answer
            _seen_amts = list(dict.fromkeys(_direct_hits))
            if len(_seen_amts) == 1:
                _ans = f"Your bill at **{named_subject}** is **${_seen_amts[0]}**."
            else:
                _ans = f"Found {len(_seen_amts)} charge(s) at **{named_subject}**:\n" + \
                       "\n".join(f"- **${a}**" for a in _seen_amts)
            return {"answer": _ans, "sources": sorted(_direct_sources)}

    # ── 3. No context → precise "not found" message ─────────────────────────
    if not context_parts:
        if named_subject:
            return {
                "answer": (
                    f"⚠️ No information found about **{named_subject}** in the uploaded documents.\n\n"
                    "The documents in the system do not appear to contain any content related to "
                    f"this person or entity. Please upload the relevant document (e.g. their resume, "
                    "policy, or record) and try again."
                ),
                "sources": [],
            }
        return {
            "answer": (
                "⚠️ The documents do not contain enough information to answer this question. "
                "Please upload the relevant documents and try again."
            ),
            "sources": [],
        }

    # ── 3. Confidence is judged on the answer itself (see _grounding_note) ──

    # ── 4. Build prompt layers ───────────────────────────────────────────────

    # 4a. Universal grounding rules — always present
    grounding = (
        "RULES (non-negotiable):\n"
        "1. Use ONLY the document excerpts provided below. Do not use outside knowledge.\n"
        "2. Every fact you state MUST be traceable to a specific excerpt. Cite the filename.\n"
        "3. If a fact is not explicitly stated in the excerpts, write [NOT FOUND] — never guess.\n"
        "4. Do NOT infer relationships. If a document does not mention the person or entity "
        "being asked about, do not use it.\n"
        "5. Numbers must be copied exactly as they appear. Do not round or estimate.\n"
        "6. NEVER include URLs, hyperlinks, or citations to external websites. "
        "NEVER reference YouTube, Wikipedia, government sites, or any source not in the excerpts."
    )

    # 4b. Subject constraint
    subject_block = ""
    if named_subject:
        subject_block = (
            f"\nSUBJECT CONSTRAINT: This question is specifically about '{named_subject}'.\n"
            f"  • Only use excerpts that explicitly contain the name '{named_subject}'.\n"
            "  • If an excerpt does not mention this name, skip it entirely.\n"
            "  • Do not associate any document with this person unless their name appears in it."
        )

    # 4c. Domain extraction template
    # Direct lookups ("what was my ending balance") must answer the question, not
    # follow a generic per-document checklist — a 7B model obeys the checklist.
    is_lookup = intent == "field_extract" or bool(re.match(
        r"\s*(what|when|where|who|whose|which|how much|how many)\b", question, re.I)) and intent not in (
        "aggregate", "summarize", "compare", "list", "trend", "rank", "multi_period")
    domain_hint = "" if is_lookup else _domain_extract_hint(doc_type_hint)
    domain_block = f"\nDOMAIN EXTRACTION GUIDE:\n{domain_hint}" if domain_hint else ""
    lookup_block = ("\nTHIS IS A DIRECT LOOKUP: answer ONLY the question asked, in 1-3 sentences. "
                    "Quote the exact value (number, date, name) as written in the excerpt and cite it. "
                    "Do not summarize the document or list unrelated items.") if is_lookup else ""

    # 4d. Intent instruction
    intent_block = f"\n{_intent_instruction(intent, field, aggregation)}"

    # 4e. Format instruction
    format_str, num_predict = _format_instruction(question)
    format_block = f"\nOUTPUT FORMAT: {format_str}" if format_str else ""

    # ── 5. Assemble and call LLM ────────────────────────────────────────────
    context = "\n\n".join(context_parts[:18])
    # Highlighting helps inside long passage lists; with whole documents it only
    # pulls the model toward a keyword match in the wrong document.
    key_lines = "" if plan.get("_full_docs") else _key_lines(question, context_parts[:18])
    if key_lines:
        context = "KEY LINES (exact matches for names in the question — read these first):\n" + key_lines + "\n\n" + context

    page_instruction = (
        f"\nCONTINUATION (page {page + 1}): These are ADDITIONAL document excerpts not shown "
        "previously. Extract ONLY new facts and details from these excerpts. Do NOT repeat any "
        "high-level summaries or introductory statements already provided. Skip any information "
        "that would appear in a typical summary — focus on specific numbers, dates, names, "
        "clause details, and line-item data found in these deeper sections.\n"
    ) if page > 0 else ""

    prompt = f"""{grounding}{subject_block}{domain_block}{intent_block}{lookup_block}{page_instruction}{format_block}

Question: {question}
Research intent: {reasoning}

--- DOCUMENT EXCERPTS (most relevant first) ---
{context}
--- END EXCERPTS ---

Question (answer this exactly): {question}{_parts_block(question)}{_chart_gap_instruction(question, context)}
Answer ({"cite [filename] once after the list" if "EVERY entry" in format_str else "cite [filename] for every fact"}, write [NOT FOUND] for missing data):"""

    try:
        answer, verify_note = _cascade_answer(prompt, num_predict, sources, context, question)
        if "*None of the documents searched mention" in verify_note or "don't cover this" in verify_note:
            sources = set()
        answer = re.sub(r"(?m)^[ \t]*[*\-•][ \t]*\n", "", answer)   # empty bullets the model emits between items
        # Non-streamed answer can be corrected in place: a figure cited to the wrong
        # document gets the right citation, and Sources lists only that document.
        fix = _citation_fix(answer, context)
        if fix:
            answer = answer.replace(fix[0], fix[1])
            sources = {fix[1]}
        answer += verify_note + _chart_gap_note(question, context)
    except llm.CloudConsentRequired:
        raise
    except Exception as e:
        answer = f"Error generating answer: {e}"

    for note in llm.request_notes():
        answer += f"\n\n🔒 *{note}*"
    return {"answer": answer, "sources": sorted(sources)}


_DATASET_DOC_TYPES = {
    "product_dataset", "sales_report", "inventory_report", "order_history",
    "transaction_log", "customer_list", "forecast_report", "dataset",
    "movie_dataset", "music_dataset", "book_dataset", "ratings_dataset",
    "location_dataset", "weather_dataset", "sports_dataset", "health_dataset",
    "education_dataset", "real_estate_dataset", "stock_dataset", "census_dataset",
    "scientific_dataset", "survey_dataset", "event_dataset", "spreadsheet",
    "data_table",  # CSV/Excel files ingested as data_table
}

# Per-domain column hints: maps question keywords → (group_col_candidates, metric_col_candidates)
_DOMAIN_COL_HINTS: List[tuple] = [
    # ── Sales / Product ──────────────────────────────────────────────────────
    ({"product", "item", "sku"},             (["Product", "Item", "SKU", "Name"],           ["TotalPrice", "Revenue", "Sales", "Amount", "Total"])),
    ({"category"},                            (["Category", "Type", "ProductCategory"],      ["TotalPrice", "Revenue", "Sales", "Amount"])),
    ({"region", "area", "zone"},             (["Region", "Area", "Zone", "Territory"],      ["TotalPrice", "Revenue", "Sales", "Amount", "Total"])),
    ({"who", "salesperson", "sales rep", "sales person", "agent", "person", "rep", "employee", "staff", "associate"}, (["Salesperson", "SalesRep", "SalesPerson", "Agent", "Rep", "Employee", "Name", "Person", "Staff", "Associate", "Seller"], ["TotalPrice", "Revenue", "Sales", "Amount", "Total"])),
    ({"store", "location", "outlet"},        (["StoreLocation", "Store", "Location"],        ["TotalPrice", "Revenue", "Sales", "Amount", "Total"])),
    ({"customer type", "customertype"},      (["CustomerType", "CustomerSegment", "Segment"],["TotalPrice", "Revenue", "Amount"])),
    ({"payment", "payment method"},          (["PaymentMethod", "Payment", "Method"],        ["TotalPrice", "Amount"])),
    ({"quantity", "units", "volume"},        (["Product", "Item", "Name"],                   ["Quantity", "Units", "Volume", "Count"])),
    # ── Movies / Entertainment ────────────────────────────────────────────────
    ({"genre"},                              (["Genre"],                                     ["Rating", "Score", "Revenue", "Gross", "Budget", "Count"])),
    ({"director"},                           (["Director"],                                  ["Rating", "Score", "Revenue", "Gross", "Budget"])),
    ({"year", "release"},                    (["Year", "ReleaseYear", "Release Year"],       ["Rating", "Score", "Revenue", "Gross", "Budget", "Count"])),
    ({"language"},                           (["Language"],                                  ["Rating", "Score", "Count"])),
    ({"country"},                            (["Country"],                                   ["Rating", "Score", "Revenue", "Count"])),
    ({"actor", "cast", "star"},              (["Actor", "Cast", "Star"],                     ["Rating", "Score", "Revenue"])),
    ({"studio"},                             (["Studio", "Production", "Company"],           ["Revenue", "Gross", "Budget", "Count"])),
    # ── Music ────────────────────────────────────────────────────────────────
    ({"artist", "singer", "band"},           (["Artist", "Singer", "Band", "Performer"],     ["Streams", "Plays", "Revenue", "Sales", "Rating"])),
    ({"album"},                              (["Album"],                                     ["Streams", "Plays", "Revenue", "Sales"])),
    ({"playlist"},                           (["Playlist"],                                  ["Streams", "Count", "Followers"])),
    # ── Books ────────────────────────────────────────────────────────────────
    ({"author"},                             (["Author", "Writer"],                          ["Rating", "Reviews", "Sales", "Price"])),
    ({"publisher"},                          (["Publisher"],                                 ["Rating", "Sales", "Count"])),
    # ── Health / Medical ─────────────────────────────────────────────────────
    ({"diagnosis", "condition", "disease"},  (["Diagnosis", "Condition", "Disease"],         ["Count", "Frequency", "Cost", "Amount"])),
    # ── HR / Employee ────────────────────────────────────────────────────────
    ({"department", "dept", "headcount", "team size"},   (["Department", "Dept", "Division", "Team"],                    ["Salary", "Count", "Headcount", "Revenue", "Cost", "Amount"])),
    ({"salary", "wage", "compensation", "pay", "average salary", "avg salary"}, (["Department", "Dept", "Division", "JobTitle", "Role", "Level"], ["Salary", "Wage", "Pay", "Compensation"])),
    ({"gender", "sex", "diversity", "gender distribution"}, (["Gender", "Sex"],                                           ["Count", "Headcount", "Salary"])),
    ({"tenure", "years of service", "seniority"},        (["Tenure", "YearsOfService", "YearsAtCompany", "Experience"],  ["Count", "Average", "Salary"])),
    ({"hired", "hire date", "start date", "joining"},    (["HireDate", "StartDate", "JoinDate", "DateJoined", "Year"],   ["Count"])),
    ({"job title", "role", "position", "level"},         (["JobTitle", "Role", "Position", "Level", "Title"],            ["Salary", "Count"])),
    ({"doctor", "provider", "physician"},    (["Doctor", "Provider", "Physician"],           ["Count", "Revenue", "Cost"])),
    # ── Real Estate ──────────────────────────────────────────────────────────
    ({"neighborhood", "city", "district"},   (["Neighborhood", "City", "District", "Area"], ["Price", "SalePrice", "ListPrice", "Value"])),
    ({"property type", "house type"},        (["PropertyType", "HouseType", "Type"],         ["Price", "SalePrice", "ListPrice"])),
    # ── Education ────────────────────────────────────────────────────────────
    ({"grade", "subject", "course"},         (["Grade", "Subject", "Course"],               ["Score", "Grade", "Count", "Enrollment"])),
    ({"school", "university"},               (["School", "University", "Institution"],       ["Score", "Count", "Enrollment", "Rating"])),
    # ── Weather / Environment ─────────────────────────────────────────────────
    ({"month", "season"},                    (["Month", "Season"],                           ["Temperature", "Rainfall", "Humidity", "Value"])),
    # ── Sports ───────────────────────────────────────────────────────────────
    ({"team"},                               (["Team"],                                      ["Score", "Wins", "Points", "Goals", "Revenue"])),
    ({"player", "athlete"},                  (["Player", "Athlete", "Name"],                 ["Score", "Goals", "Points", "Salary"])),
    ({"sport"},                              (["Sport"],                                     ["Revenue", "Count", "Score"])),
    # ── Stock / Finance dataset ───────────────────────────────────────────────
    ({"company", "ticker"},                  (["Company", "Ticker", "Symbol"],               ["Price", "Close", "Open", "Volume", "MarketCap"])),
    ({"sector", "industry"},                 (["Sector", "Industry"],                        ["Price", "MarketCap", "Revenue", "Count"])),
    # ── General fallback ─────────────────────────────────────────────────────
    ({"name"},                               (["Name", "Title", "Label"],                    ["Count", "Value", "Amount", "Score", "Total"])),
    ({"type"},                               (["Type", "Category"],                          ["Count", "Value", "Amount", "Total"])),
]

_METRIC_KEYWORDS: List[tuple] = [
    # (question_keywords, metric_col_candidates, label)
    ({"revenue", "sales", "total sales"},    ["TotalPrice","Revenue","Sales","Total","Amount"],          "Total Sales ($)"),
    ({"total price", "totalprice"},          ["TotalPrice","Total"],                                     "Total Price ($)"),
    ({"quantity", "units", "volume"},        ["Quantity","Units","Volume","Count"],                      "Units Sold"),
    ({"rating", "score", "stars"},           ["Rating","Score","Stars","Rank"],                          "Avg Rating"),
    ({"revenue", "gross", "box office"},     ["Revenue","Gross","BoxOffice","Earnings"],                 "Revenue ($)"),
    ({"budget"},                             ["Budget","Cost"],                                          "Budget ($)"),
    ({"price"},                              ["Price","SalePrice","ListPrice","UnitPrice","Close"],      "Price ($)"),
    ({"streams", "plays", "listens"},        ["Streams","Plays","Listens","Count"],                      "Streams"),
    ({"temperature", "temp"},               ["Temperature","Temp","AvgTemp"],                           "Temperature"),
    ({"count", "number", "how many"},        ["Count","Frequency","Number","Total"],                     "Count"),
    ({"salary", "wage", "pay"},              ["Salary","Wage","Pay","Compensation"],                     "Salary ($)"),
    ({"points", "wins", "goals"},            ["Points","Wins","Goals","Score"],                          "Points"),
    ({"enrollment", "students"},             ["Enrollment","Students","Count"],                          "Enrollment"),
    ({"discount"},                           ["Discount","DiscountPct"],                                 "Discount"),
]


def _fetch_all_doc_chunks(doc_ids: List[str]) -> List[Dict]:
    """Fetch ALL chunk texts for the given document ids directly from DB (bypasses vector search limit)."""
    if not doc_ids:
        return []
    try:
        from sqlalchemy import text as _sql_text
        db = SessionLocal()
        placeholders = ",".join(f":id{i}" for i in range(len(doc_ids)))
        params = {f"id{i}": did for i, did in enumerate(doc_ids)}
        rows = db.execute(_sql_text(f"""
            SELECT dc.chunk_text, dc.document_id, d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.document_id IN ({placeholders})
              AND dc.chunk_text IS NOT NULL
            ORDER BY dc.chunk_index
        """), params).fetchall()
        db.close()
        return [{"text": r.chunk_text, "filename": r.filename, "doc_id": str(r.document_id)} for r in rows]
    except Exception as e:
        logger.warning(f"_fetch_all_doc_chunks failed: {e}")
        return []


def _collect_chunks(steps: List[Dict]) -> List[Dict]:
    """Collect all chunks from search/ask steps."""
    chunks: List[Dict] = []
    for s in steps:
        # steps use "tool" field; legacy code may use "action"
        tool_name = s.get("tool") or s.get("action") or ""
        if tool_name in ("search_documents", "ask_document"):
            obs = s.get("observation") or {}
            if isinstance(obs, list):
                chunks.extend(obs)
            else:
                chunks.extend(obs.get("chunks") or obs.get("results") or [])
    return chunks


def _parse_tabular_chunks(chunks: List[Dict]) -> tuple:
    """
    Parse pipe-delimited, CSV, or Key: Value tabular text from chunks.
    Returns (header: List[str], rows: List[Dict]) with all parsed data rows.
    """
    import re as _re
    header: List[str] = []
    all_rows: List[Dict] = []

    # Pattern: "Key: Value Key2: Value2 ..." on a single line
    _kv_line_re = _re.compile(r'([A-Za-z][A-Za-z0-9 _]*):\s*([^:]+?)(?=\s+[A-Za-z][A-Za-z0-9 _]*:|$)')

    for ch in chunks:
        text = ch.get("chunk_text") or ch.get("text") or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        chunk_header: List[str] = []
        for line in lines:
            # ── Pipe-delimited ────────────────────────────────────────────────
            if "|" in line:
                cols = [c.strip() for c in line.split("|")]
                cols = [c for c in cols if c]
                if not cols:
                    continue
                is_header = sum(1 for c in cols if _re.match(r"^[A-Za-z][A-Za-z0-9 _/()-]*$", c)) >= len(cols) * 0.7
                if is_header and not chunk_header:
                    chunk_header = cols
                    if not header:
                        header = cols
                    continue
                if not chunk_header:
                    continue
                if cols == chunk_header:
                    continue
                if len(cols) < len(chunk_header):
                    continue
                row = dict(zip(chunk_header, cols[:len(chunk_header)]))
                all_rows.append(row)

            # ── Key: Value line (e.g. "Date: Feb 23 Region: East ...") ────────
            elif ": " in line and not line.startswith("#"):
                matches = _kv_line_re.findall(line)
                if len(matches) >= 2:
                    row = {k.strip(): v.strip() for k, v in matches}
                    if not chunk_header:
                        chunk_header = list(row.keys())
                        if not header:
                            header = chunk_header
                    elif not header:
                        header = chunk_header
                    if row:
                        all_rows.append(row)

            # ── Plain CSV ─────────────────────────────────────────────────────
            elif "," in line and not any(x in line for x in [".", "-", "/"]):
                cols = [c.strip().strip('"') for c in line.split(",")]
                cols = [c for c in cols if c]
                if not cols:
                    continue
                is_header = sum(1 for c in cols if _re.match(r"^[A-Za-z][A-Za-z0-9 _/()-]*$", c)) >= len(cols) * 0.7
                if is_header and not chunk_header:
                    chunk_header = cols
                    if not header:
                        header = cols
                    continue
                if not chunk_header:
                    continue
                if cols == chunk_header:
                    continue
                if len(cols) < len(chunk_header):
                    continue
                row = dict(zip(chunk_header, cols[:len(chunk_header)]))
                all_rows.append(row)

    return header, all_rows


def _llm_resolve_columns(question: str, header: List[str], sample_row: Dict) -> Dict:
    """
    Ask the LLM to map an arbitrary question to the right group_col, metric_col, and
    aggregation function given the actual column names and a sample value row.
    Returns dict: {group_col, metric_col, agg, want_chart, viz_type}
    Falls back to empty dict on any error so callers can continue with keyword matching.
    """
    if not header:
        return {}
    sample_preview = ", ".join(f"{k}={repr(v)[:30]}" for k, v in list(sample_row.items())[:8])
    prompt = (
        f"Dataset columns: {header}\n"
        f"Sample row: {sample_preview}\n"
        f"Question: \"{question}\"\n\n"
        "Reply with ONLY a JSON object — no explanation:\n"
        '{"group_col": "<col or null>", "metric_col": "<col or null>", '
        '"agg": "sum|avg|count|count_distinct", '
        '"want_chart": true|false, '
        '"viz_type": "bar|line|pie|horizontalBar"}'
    )
    try:
        resp = llm.chat(role="planner", 
            model=_active_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX, "num_predict": 120, "temperature": 0.0},
            timeout=12,
        )
        raw = resp["message"]["content"].strip()
        # Extract JSON
        import re as _jre
        m = _jre.search(r'\{.*?\}', raw, _jre.DOTALL)
        if m:
            import json as _json
            result = _json.loads(m.group())
            logger.info(f"_llm_resolve_columns: {result}")
            return result
    except Exception as e:
        logger.warning(f"_llm_resolve_columns failed: {e}")
    return {}


def _resolve_column(candidates: List[str], header: List[str]) -> Optional[str]:
    """Find first candidate that exists (case-insensitive) in header."""
    header_lower = {h.lower(): h for h in header}
    for c in candidates:
        match = header_lower.get(c.lower())
        if match:
            return match
    return None


def _build_chart_from_chunks(question: str, plan: Dict, steps: List[Dict]) -> Optional[Dict]:
    """
    Build a Chart.js chart from tabular chunks across all 10 domains.
    Works for any pipe-delimited/CSV dataset: product, movie, music, book,
    health, real estate, education, weather, sports, stock, etc.
    """
    import re as _re_n
    from collections import defaultdict

    q_lower = question.lower()
    all_chunks = _collect_chunks(steps)
    if not all_chunks:
        return None

    header, rows = _parse_tabular_chunks(all_chunks)

    # If we got very few rows from vector search (< 50), fetch ALL chunks from DB
    # so aggregation queries ("monthly trend", "by product") have full data coverage.
    # When multiple docs are in scope, pick the one whose header best matches the question
    # rather than mixing schemas across documents (which corrupts column resolution).
    if len(rows) < 50:
        doc_ids_in_steps = list({ch.get("doc_id") or "" for ch in all_chunks if ch.get("doc_id")})
        if doc_ids_in_steps:
            if len(doc_ids_in_steps) == 1:
                all_db_chunks = _fetch_all_doc_chunks(doc_ids_in_steps)
                if all_db_chunks:
                    header_db, rows_db = _parse_tabular_chunks(all_db_chunks)
                    if rows_db:
                        header = header_db or header
                        rows = rows_db
            else:
                # Multiple docs — score each by how many header columns appear in the question
                best_doc, best_score, best_header, best_rows = None, -1, header, rows
                for did in doc_ids_in_steps:
                    chunks_i = _fetch_all_doc_chunks([did])
                    if not chunks_i:
                        continue
                    h_i, r_i = _parse_tabular_chunks(chunks_i)
                    if not h_i or not r_i:
                        continue
                    score = sum(1 for col in h_i if col.lower() in q_lower or any(kw in col.lower() for kw in q_lower.split()))
                    # Also boost score for larger datasets (more rows = richer data)
                    score = score * 10 + min(len(r_i), 500)
                    if score > best_score:
                        best_score, best_doc = score, did
                        best_header, best_rows = h_i, r_i
                if best_rows:
                    header, rows = best_header, best_rows
                    logger.info(f"_build_chart_from_chunks: multi-doc, selected doc {best_doc} (score={best_score}, {len(rows)} rows)")
            logger.info(f"_build_chart_from_chunks: full-fetch {len(rows)} rows from DB")

    if not header or not rows:
        return None

    logger.info(f"_build_chart_from_chunks: {len(rows)} rows, header={header[:6]}")

    # ── 1. Detect group_col from question keywords ────────────────────────────
    group_col: Optional[str] = None

    # Check for time-based grouping first ("monthly", "trend", "over time", "by month/year")
    _time_kws = {"monthly", "month", "trend", "over time", "over 2", "yearly", "year", "quarterly", "weekly", "by date"}
    if any(kw in q_lower for kw in _time_kws):
        group_col = _resolve_column(["Date", "OrderDate", "Month", "Year", "Quarter", "Week",
                                     "DeliveryDate", "CreatedAt", "Timestamp", "Period"], header)

    if not group_col:
        for kw_set, (grp_candidates, _) in _DOMAIN_COL_HINTS:
            if any(kw in q_lower for kw in kw_set):
                group_col = _resolve_column(grp_candidates, header)
                if group_col:
                    break

    # ── 2. Detect metric_col and label from question keywords ─────────────────
    metric_col: Optional[str] = None
    metric_label = "Value"
    for kw_set, met_candidates, label in _METRIC_KEYWORDS:
        if any(kw in q_lower for kw in kw_set):
            metric_col = _resolve_column(met_candidates, header)
            if metric_col:
                metric_label = label
                break

    # ── 2b. If group_col still not resolved, also check metric keyword table for group ─
    # e.g. "sales by product" → group_col should be Product even without domain hint match
    if not group_col:
        for kw_set, (grp_cands, _met_cands) in _DOMAIN_COL_HINTS:
            if any(kw in q_lower for kw in kw_set):
                group_col = _resolve_column(grp_cands, header)
                if group_col:
                    break

    # ── 2c. "per <X>" / "by <X>" → resolve group_col from the X term ─────────
    if not group_col:
        _per_m = _re_n.search(r'\b(?:per|by|across|for each)\s+([a-z][a-z ]{1,20})', q_lower)
        if _per_m:
            _per_term = _per_m.group(1).strip()
            for kw_set, (grp_cands, _) in _DOMAIN_COL_HINTS:
                if any(kw in _per_term for kw in kw_set) or any(_per_term in kw for kw in kw_set):
                    group_col = _resolve_column(grp_cands, header)
                    if group_col:
                        break
            # Direct fuzzy match against header columns
            if not group_col:
                group_col = _resolve_column([_per_term.title(), _per_term.replace(" ", "").title()], header)

    # ── 3. Auto-detect from header if still unresolved ───────────────────────
    if not group_col or not metric_col:
        # Classify each column: numeric or categorical
        numeric_cols: List[str] = []
        categorical_cols: List[str] = []
        sample = rows[:50]
        for col in header:
            vals = [r.get(col, "") for r in sample if r.get(col, "").strip()]
            numeric_count = 0
            for v in vals:
                try:
                    float(v.replace(",", "").replace("$", "").replace("%", ""))
                    numeric_count += 1
                except (ValueError, AttributeError):
                    pass
            if vals and numeric_count / len(vals) >= 0.7:
                numeric_cols.append(col)
            elif vals:
                # Only use categorical if cardinality is reasonable (< 50 unique values)
                unique = len({r.get(col, "") for r in sample})
                if 2 <= unique <= 50:
                    categorical_cols.append(col)

        if not group_col and categorical_cols:
            group_col = categorical_cols[0]
        if not metric_col and numeric_cols:
            # Prefer columns with "total", "price", "amount", "count" in name
            for nc in numeric_cols:
                if any(kw in nc.lower() for kw in ["total", "price", "amount", "revenue", "sales", "count", "score", "rating"]):
                    metric_col = nc
                    metric_label = nc
                    break
            if not metric_col:
                metric_col = numeric_cols[0]
                metric_label = metric_col

    # ── 3b. LLM fallback — handles any question keyword matching can't cover ───
    if not group_col or not metric_col:
        sample_row = rows[0] if rows else {}
        llm_cols = _llm_resolve_columns(question, header, sample_row)
        if llm_cols:
            if not llm_cols.get("want_chart", True):
                logger.info("_build_chart_from_chunks: LLM says no chart needed — deferring to text synthesis")
                return None
            gc = llm_cols.get("group_col")
            mc = llm_cols.get("metric_col")
            if gc and gc in header:
                group_col = gc
            if mc and mc in header:
                metric_col = mc
                metric_label = metric_col
            # Override agg flags from LLM
            _llm_agg = llm_cols.get("agg", "sum")
            if _llm_agg == "avg":
                is_avg   = True
                is_count = False
            elif _llm_agg in ("count", "count_distinct"):
                is_count = True
                is_avg   = False
            # Override viz type from LLM if not already set by keyword
            _llm_viz = llm_cols.get("viz_type")
            if _llm_viz:
                viz_type_from_llm = _llm_viz  # applied after sort step
            logger.info(f"_build_chart_from_chunks LLM resolved: group={group_col} metric={metric_col} agg={_llm_agg}")

    if not group_col or not metric_col:
        logger.info(f"_build_chart_from_chunks: could not resolve columns (group={group_col}, metric={metric_col})")
        return None

    # ── 4. Aggregate ──────────────────────────────────────────────────────────
    import re as _re
    aggregated: Dict[str, float] = defaultdict(float)
    count_map: Dict[str, int] = defaultdict(int)
    is_count = any(kw in q_lower for kw in ["how many", "count", "number of", "frequency", "unique", "distinct", "headcount"])
    is_avg   = not is_count and any(kw in q_lower for kw in ["average", "avg", "mean", "per ", "rating", "score"])
    is_time_col = group_col and any(kw in group_col.lower() for kw in ["date", "month", "year", "quarter", "week", "time"])

    # Extract top-N / bottom-N limit from question ("top 5", "bottom 10", "5 products")
    _top_n_match    = _re_n.search(r'\btop\s+(\d+)\b', q_lower)
    _bottom_n_match = _re_n.search(r'\bbottom\s+(\d+)\b', q_lower)
    _result_limit   = int((_top_n_match or _bottom_n_match).group(1)) if (_top_n_match or _bottom_n_match) else 15
    _is_bottom      = bool(_bottom_n_match) or any(kw in q_lower for kw in ["bottom", "least", "lowest", "worst", "fewest"])

    _MONTH_ABBR = {"january":"Jan","february":"Feb","march":"Mar","april":"Apr",
                   "may":"May","june":"Jun","july":"Jul","august":"Aug",
                   "september":"Sep","october":"Oct","november":"Nov","december":"Dec"}

    _MONTH_FULL_ABBR = {
        "january":"Jan","february":"Feb","march":"Mar","april":"Apr","may":"May","june":"Jun",
        "july":"Jul","august":"Aug","september":"Sep","october":"Oct","november":"Nov","december":"Dec",
        "jan":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","jun":"Jun","jul":"Jul","aug":"Aug",
        "sep":"Sep","oct":"Oct","nov":"Nov","dec":"Dec",
    }

    def _normalise_date_group(raw: str) -> str:
        """Convert any date string → 'Mon YYYY' for monthly grouping."""
        s = raw.strip()
        # YYYY-MM-DD or YYYY-MM
        m = _re.match(r"(\d{4})-(\d{2})", s)
        if m:
            _MONTHS_LIST = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            return f"{_MONTHS_LIST[int(m.group(2))]} {m.group(1)}"
        # Full or abbreviated month name: "February 23, 2023" / "Jan 18, 2025" / "Feb 2023"
        m2 = _re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december"
            r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b[,\s]+(?:\d{1,2}[,\s]+)?(\d{4})",
            s.lower()
        )
        if m2:
            return f"{_MONTH_FULL_ABBR[m2.group(1)]} {m2.group(2)}"
        # MM/DD/YYYY or DD/MM/YYYY
        m3 = _re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
        if m3:
            _MONTHS_LIST = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            month_idx = int(m3.group(1))
            if 1 <= month_idx <= 12:
                return f"{_MONTHS_LIST[month_idx]} {m3.group(3)}"
        return s

    # Detect year filter from question (e.g. "over 2023", "in 2024", "for 2022")
    import re as _re2
    _year_filter: Optional[str] = None
    _year_m = _re2.search(r'\b(20\d{2})\b', question)
    if _year_m and is_time_col:
        _year_filter = _year_m.group(1)
        logger.info(f"_build_chart: filtering to year={_year_filter}")

    for row in rows:
        grp_raw = row.get(group_col, "").strip()
        if not grp_raw or grp_raw == group_col:
            continue
        grp = _normalise_date_group(grp_raw) if is_time_col else grp_raw
        # Apply year filter when question specifies a year
        if _year_filter and is_time_col and _year_filter not in grp:
            continue
        if is_count:
            aggregated[grp] += 1
            count_map[grp] += 1
        else:
            val_str = row.get(metric_col, "").strip().replace(",", "").replace("$", "").replace("%", "")
            try:
                aggregated[grp] += float(val_str)
                count_map[grp] += 1
            except (ValueError, TypeError):
                pass

    if not aggregated or len(aggregated) < 2:
        return None

    if is_avg:
        aggregated = {k: round(v / count_map[k], 2) for k, v in aggregated.items()}
    if is_count:
        metric_label = f"Count of {group_col}"

    # ── 5. Detect chart type from question ────────────────────────────────────
    viz_type_from_llm: Optional[str] = None  # may be set in 3b
    viz_type = "bar"
    if any(kw in q_lower for kw in ["pie", "donut", "distribution", "breakdown", "proportion", "share"]):
        viz_type = "pie"
    elif any(kw in q_lower for kw in ["line", "trend", "over time", "timeline", "growth"]):
        viz_type = "line"
    elif any(kw in q_lower for kw in ["top", "bottom", "ranking", "highest", "lowest", "most", "least", "largest", "leading", "fewest", "worst"]):
        viz_type = "horizontalBar"

    # ── 6. Sort and cap ───────────────────────────────────────────────────────
    # Time-series: sort chronologically when group_col is time-based
    _MONTH_IDX = {
        "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
        "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
        "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,
        "sep":9,"oct":10,"nov":11,"dec":12,
    }
    is_time_group = is_time_col or (group_col and any(kw in group_col.lower() for kw in ["month","year","date","quarter","week"]))
    if is_time_group:
        def _time_sort_key(item):
            import re as _re2
            k = item[0].lower().strip()
            # YYYY-MM or YYYY-MM-DD → sort as integer YYYYMM
            m = _re2.match(r"(\d{4})-(\d{2})", k)
            if m:
                return int(m.group(1)) * 100 + int(m.group(2))
            # "Jan 2023", "February 2023" etc.
            yr = _re2.search(r"\d{4}", k)
            yr_val = int(yr.group()) if yr else 0
            for mon, idx in _MONTH_IDX.items():
                if mon in k:
                    return yr_val * 100 + idx
            # Plain year
            if yr:
                return yr_val * 100
            # Quarter: Q1 2023 → 202301
            q = _re2.search(r"q(\d)[^0-9]*(\d{4})", k)
            if q:
                return int(q.group(2)) * 100 + int(q.group(1)) * 3
            return 0
        sorted_items = sorted(aggregated.items(), key=_time_sort_key)[:15]
        viz_type = viz_type if viz_type != "bar" else "line"  # default to line for time
    else:
        sorted_items = sorted(aggregated.items(), key=lambda x: x[1], reverse=not _is_bottom)[:_result_limit]

    if viz_type_from_llm and viz_type == "bar":
        viz_type = viz_type_from_llm

    labels = [k for k, _ in sorted_items]
    values = [round(v, 2) for _, v in sorted_items]

    _PALETTE = ["#6366f1","#06b6d4","#10b981","#f59e0b","#ef4444",
                "#8b5cf6","#ec4899","#14b8a6","#f97316","#84cc16",
                "#3b82f6","#a855f7","#22c55e","#eab308","#f43f5e"]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(labels))]

    dataset = {
        "label": metric_label,
        "data": values,
        "backgroundColor": colors if viz_type == "pie" else [c + "cc" for c in colors],
        "borderColor": colors,
        "borderWidth": 1,
        "borderRadius": 4 if viz_type not in ("pie", "line") else 0,
        "fill": viz_type == "line",
        "tension": 0.4,
    }

    return {
        "type": "line" if viz_type == "line" else ("horizontalBar" if viz_type == "horizontalBar" else viz_type),
        "labels": labels,
        "datasets": [dataset],
        "_group_col": group_col,
        "_metric_col": metric_col,
    }


# ── Key-value extractor for non-tabular text chunks (PDF/Word) ────────────────
# Handles insurance, HR, payroll, medical, real-estate, legal domains where
# chunks are paragraph text, not pipe-delimited tables.

_KV_PATTERNS = [
    # "Label: $1,234.56" or "Label: 1,234"
    r"([A-Za-z][A-Za-z0-9 /()&-]{2,40})\s*:\s*\$?([\d,]+\.?\d*)",
    # "Label — $1,234" dash variant
    r"([A-Za-z][A-Za-z0-9 /()&-]{2,40})\s*[—–-]\s*\$?([\d,]+\.?\d*)",
    # "$1,234 Label" (amount-first)
    r"\$\s*([\d,]+\.?\d*)\s+([A-Za-z][A-Za-z0-9 /()&-]{2,40})",
]

def _extract_kv_from_chunks(chunks: List[Dict], question: str) -> Optional[Dict]:
    """
    Extract key-value pairs from unstructured text chunks (PDF/Word docs).
    Returns Chart.js bar chart if enough distinct numeric values found.
    """
    import re as _re
    from collections import defaultdict

    q_lower = question.lower()
    aggregated: Dict[str, float] = defaultdict(float)

    for ch in chunks:
        text = ch.get("chunk_text") or ch.get("text") or ""
        for pattern in _KV_PATTERNS:
            for m in _re.finditer(pattern, text, _re.IGNORECASE):
                groups = m.groups()
                if len(groups) == 2:
                    # Determine which group is label vs value
                    try:
                        val = float(groups[0].replace(",", ""))
                        label = groups[1].strip()
                    except ValueError:
                        label = groups[0].strip()
                        try:
                            val = float(groups[1].replace(",", ""))
                        except ValueError:
                            continue
                    # Skip clause/section/paragraph numbers and trivially small values
                    skip_labels = {"section", "article", "clause", "paragraph", "page", "item",
                                   "exhibit", "schedule", "appendix", "part", "chapter", "step"}
                    if label and val >= 10 and label.lower().split()[0] not in skip_labels:
                        aggregated[label] += val

    if len(aggregated) < 2:
        return None

    # Filter: keep only items whose labels loosely relate to question keywords
    q_words = set(q_lower.split())
    # If question is specific, filter; if vague ("show me all"), keep all
    if len(q_words) > 3:
        scored = {k: v for k, v in aggregated.items()
                  if any(w in k.lower() for w in q_words if len(w) > 3)}
        if len(scored) >= 2:
            aggregated = scored  # type: ignore

    sorted_items = sorted(aggregated.items(), key=lambda x: x[1], reverse=True)[:12]
    if len(sorted_items) < 2:
        return None

    labels = [k for k, _ in sorted_items]
    values = [round(v, 2) for _, v in sorted_items]

    viz_type = "pie" if any(kw in q_lower for kw in ["breakdown", "distribution", "proportion", "share", "pie"]) else "bar"

    _PALETTE = ["#6366f1","#06b6d4","#10b981","#f59e0b","#ef4444",
                "#8b5cf6","#ec4899","#14b8a6","#f97316","#84cc16",
                "#3b82f6","#a855f7","#22c55e","#eab308","#f43f5e"]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(labels))]

    return {
        "type": viz_type,
        "labels": labels,
        "datasets": [{
            "label": "Amount ($)",
            "data": values,
            "backgroundColor": colors if viz_type == "pie" else [c + "cc" for c in colors],
            "borderColor": colors,
            "borderWidth": 1,
            "borderRadius": 4 if viz_type == "bar" else 0,
        }],
        "_group_col": "label",
        "_metric_col": "value",
    }


def _synthesize_stream(question: str, plan: Dict, steps: List[Dict], page: int = 0, prior_qa: List[Dict] = None):
    """Streaming version of _synthesize — yields token strings then a final sources dict."""
    named_subject   = (plan.get("named_subject") or "").strip()
    doc_type_hint   = (plan.get("doc_type_hint") or "general").lower()
    intent          = plan.get("intent", "info_retrieve")
    reasoning       = plan.get("reasoning", "")
    format_str, num_predict = _format_instruction(question)

    # Re-collect context (same as _synthesize)
    all_chunks: List[Dict] = []
    for s in steps:
        if s.get("action") in ("search_documents", "ask_document"):
            obs = s.get("observation") or {}
            chunks = obs.get("chunks") or obs.get("results") or []
            all_chunks.extend(chunks)

    sources: List[str] = []
    context_parts: List[str] = []
    seen_chunk_ids: set = set()
    for ch in all_chunks:
        cid = ch.get("id") or ch.get("chunk_id") or ""
        if cid and cid in seen_chunk_ids:
            continue
        if cid:
            seen_chunk_ids.add(cid)
        fn = ch.get("filename", "unknown")
        if fn not in sources:
            sources.append(fn)
        raw_txt = ch.get('chunk_text') or ch.get('text', '')
        context_parts.append(f"[{fn}]\n{_unbracket_redactions(_CITATION_NOISE_RE.sub('', raw_txt))}")

    context = "\n\n".join(context_parts[:20]) or "No relevant document excerpts found."
    key_lines = "" if plan.get("_full_docs") else _key_lines(question, [p.replace("]\n", "]: ", 1) for p in context_parts[:20]])
    if key_lines:
        context = "KEY LINES (exact matches for names in the question — read these first):\n" + key_lines + "\n\n" + context
    _intent = plan.get("intent", "")
    is_lookup = _intent == "field_extract" or (bool(re.match(
        r"\s*(what|when|where|who|whose|which|how much|how many)\b", question, re.I)) and _intent not in (
        "aggregate", "summarize", "compare", "list", "trend", "rank", "multi_period"))
    lookup_block = ("5. THIS IS A DIRECT LOOKUP: answer ONLY the question asked, in 1-3 sentences. Quote the "
                    "exact value (number, date, name) as written and cite it. Do not summarize unrelated items.\n"
                    ) if is_lookup else ""

    prior_qa_block = ""
    if prior_qa:
        lines = []
        for item in prior_qa[-4:]:  # last 4 turns max to stay within context
            lines.append(f"Q: {item.get('q', '')}\nA: {item.get('a', '')}")
        prior_qa_block = "\n\n--- PRIOR CONVERSATION ---\n" + "\n\n".join(lines) + "\n--- END PRIOR CONVERSATION ---\n"

    cite_rule = ("Cite [filename] once after the list, not on every row. Write [NOT FOUND] for missing data."
                 if "EVERY entry" in format_str else
                 "Cite [filename] for every fact. Write [NOT FOUND] for missing data.")
    prompt = f"""Answer the following question using ONLY the document excerpts below.
RULES (non-negotiable):
1. Use ONLY the excerpts provided. Do not use outside knowledge.
2. {cite_rule}
3. NEVER include URLs, hyperlinks, or citations to external websites.
4. NEVER reference YouTube, Wikipedia, government sites, or any source not in the excerpts.
{lookup_block}{format_str}
{prior_qa_block}
Question: {question}

--- DOCUMENT EXCERPTS (most relevant first) ---
{context}
--- END EXCERPTS ---

Question (answer this exactly): {question}{_parts_block(question)}{_chart_gap_instruction(question, context)}
Answer:"""

    if llm.cloud_for("answer"):
        # Cascade needs the whole local answer before deciding, so it isn't token-streamed
        try:
            answer, verify_note = _cascade_answer(prompt, num_predict, sources, context, question)
            if "*None of the documents searched mention" in verify_note or "don't cover this" in verify_note:
                sources = set()
        except llm.CloudConsentRequired:
            raise
        except Exception as e:
            answer, verify_note = f"Error generating answer: {e}", ""
        for i in range(0, len(answer), 40):
            yield ("token", answer[i:i + 40])
        if verify_note:
            yield ("token", verify_note)
        chart_note = _chart_gap_note(question, context)
        if chart_note:
            yield ("token", chart_note)
        for n in llm.request_notes():
            yield ("token", f"\n\n🔒 *{n}*")
        yield ("sources", sorted(sources))
        return

    try:
        stream = llm.chat(role="answer", 
            model=_active_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX, "num_predict": num_predict, "temperature": 0.10},
            stream=True,
        )
        answer_parts: List[str] = []
        for chunk in stream:
            token = (chunk.get("message") or {}).get("content") or ""
            if token:
                answer_parts.append(token)
                yield ("token", token)
        note = _grounding_note("".join(answer_parts), context) + _chart_gap_note(question, context)
        if note:
            yield ("token", note)
        for n in llm.request_notes():
            yield ("token", f"\n\n🔒 *{n}*")
    except llm.CloudConsentRequired:
        raise
    except Exception as e:
        yield ("token", f"Error generating answer: {e}")

    yield ("sources", sorted(sources))


# ── Main entry point ──────────────────────────────────────────────────────────

# ── Action intents that trigger real API calls instead of search ──────────────
_ACTION_INTENTS = {
    # create
    "create_client", "create_policy", "create_claim", "create_quote",
    "create_matter", "create_clm", "create_approval",
    "create_immigration", "create_note", "create_task",
    # update
    "update_client", "update_policy", "update_claim", "update_quote",
    "update_matter", "update_clm", "update_immigration",
    "update_note", "update_task",
    # delete — intercepted, never executed
    "delete_request",
}

_BACKEND_BASE = os.environ.get("BACKEND_URL", "http://localhost:7979")


def _http_get(path: str) -> Dict:
    """Make a GET request to the backend API and return the JSON response."""
    url = f"{_BACKEND_BASE}{path}"
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body}")


def _http_patch(path: str, payload: Dict) -> Dict:
    """Make a PATCH request to the backend API and return the JSON response."""
    url = f"{_BACKEND_BASE}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body}")


def _http_put(path: str, payload: Dict) -> Dict:
    """Make a PUT request to the backend API and return the JSON response."""
    url = f"{_BACKEND_BASE}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body}")


def _lookup_record_id(resource: str, name: str) -> Optional[str]:
    """
    Try to find a record ID by searching the resource list by name.
    resource: 'clients' | 'policies' | 'claims' | 'quotes' | 'matters' | 'clm' | 'immigration'
    """
    route_map = {
        "clients":     "/api/ams/clients",
        "policies":    "/api/ams/policies",
        "claims":      "/api/claims",
        "quotes":      "/api/quotes",
        "matters":     "/api/matters",
        "clm":         "/api/clm",
        "immigration": "/api/immigration",
    }
    path = route_map.get(resource)
    if not path:
        return None
    try:
        data = _http_get(f"{path}?search={urllib.parse.quote(name)}&limit=5")
        items = data.get("items") or data.get("clients") or data.get("policies") or data.get("claims") or data.get("quotes") or data.get("matters") or data.get("entries") or data.get("records") or []
        if items:
            return str(items[0].get("id", ""))
    except Exception:
        pass
    return None


def _http_post(path: str, payload: Dict) -> Dict:
    """Make a POST request to the backend API and return the JSON response."""
    url = f"{_BACKEND_BASE}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body}")


def _do_execute_action(intent: str, entities: Dict, db=None) -> Dict:
    """
    Dispatch an action intent to the appropriate backend endpoint.
    Returns {"success": bool, "result": dict, "message": str}
    """
    e = entities or {}

    if intent == "create_client":
        first = (e.get("first_name") or "").strip()
        last  = (e.get("last_name") or "").strip()
        if not first or not last:
            full = f"{first} {last}".strip()
            parts = full.split()
            first = parts[0] if parts else "Unknown"
            last  = " ".join(parts[1:]) if len(parts) > 1 else "Unknown"
        payload = {
            "client_type":   "personal",
            "first_name":    first,
            "last_name":     last,
            "mobile":        e.get("phone"),
            "address_line1": e.get("address"),
            "city":          e.get("city"),
            "state":         e.get("state"),
            "zip":           e.get("zip"),
            "email":         e.get("email"),
            "status":        "active",
        }
        result = _http_post("/api/clients", payload)
        return {
            "success": True,
            "result": result,
            "message": f"Client **{first} {last}** created successfully. Client number: `{result.get('client_number', 'N/A')}`",
        }

    if intent == "create_policy":
        import datetime as _dt
        today = _dt.date.today()
        one_year = (today.replace(year=today.year + 1)).isoformat()

        client_id = e.get("client_id")

        # If no client_id but we have a name, create the client first
        if not client_id and (e.get("first_name") or e.get("last_name")):
            first = (e.get("first_name") or "").strip()
            last  = (e.get("last_name") or "").strip()
            if not first or not last:
                full = f"{first} {last}".strip()
                parts = full.split()
                first = parts[0] if parts else "Unknown"
                last  = " ".join(parts[1:]) if len(parts) > 1 else "Unknown"
            client_payload = {
                "client_type":   "personal",
                "first_name":    first,
                "last_name":     last,
                "mobile":        e.get("phone"),
                "address_line1": e.get("address"),
                "city":          e.get("city"),
                "state":         e.get("state"),
                "zip":           e.get("zip"),
                "email":         e.get("email"),
                "status":        "active",
            }
            client_result = _http_post("/api/clients", client_payload)
            client_id = str(client_result.get("id", ""))
            client_name = f"{first} {last}"
        else:
            client_name = "client"

        if not client_id:
            return {
                "success": False,
                "result": {},
                "message": "Could not determine client — please provide client name or client ID.",
            }

        lob = e.get("line_of_business") or "auto"
        policy_payload = {
            "client_id":        client_id,
            "line_of_business": lob,
            "carrier_name":     e.get("carrier_name") or "TBD",
            "policy_number":    e.get("policy_number") or f"POL-{int(time.time())}",
            "premium_amount":   e.get("premium_amount"),
            "premium_freq":     "annual",
            "effective_date":   e.get("effective_date") or today.isoformat(),
            "expiry_date":      e.get("expiry_date") or one_year,
            "status":           "active",
            "renewal_status":   "not_started",
        }
        result = _http_post("/api/policies", policy_payload)
        return {
            "success": True,
            "result": result,
            "message": f"**{lob.title()} insurance policy** created successfully for **{client_name}**.\n- Policy number: `{result.get('policy_number')}`\n- Effective: {policy_payload['effective_date']} → Expires: {policy_payload['expiry_date']}",
        }

    if intent == "create_claim":
        import datetime as _dt
        claimant = e.get("claimant_name") or f"{e.get('first_name','')} {e.get('last_name','')}".strip() or "Unknown"
        claim_type = e.get("claim_type") or "general"
        payload = {
            "title":           e.get("matter_title") or f"{claim_type.title()} Claim — {claimant}",
            "claim_number":    e.get("policy_number"),
            "claimant_name":   claimant,
            "claim_type":      claim_type,
            "incident_date":   e.get("incident_date") or _dt.date.today().isoformat(),
            "estimated_value": e.get("estimated_value"),
            "description":     e.get("note_content") or "Claim filed via Research Agent",
        }
        result = _http_post("/api/claims", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**{claim_type.title()} claim** filed successfully for **{claimant}**.\n- Claim number: `{result.get('claim_number', result.get('id', 'N/A'))}`",
        }

    if intent == "create_quote":
        import datetime as _dt
        today = _dt.date.today()
        lob = e.get("line_of_business") or "auto"
        state = e.get("state") or "CA"
        payload = {
            "client_name":      f"{e.get('first_name','')} {e.get('last_name','')}".strip() or None,
            "lob":              lob,
            "state":            state,
            "effective_date":   e.get("effective_date") or today.isoformat(),
            "expiry_date":      e.get("expiry_date"),
            "notes":            e.get("note_content"),
        }
        result = _http_post("/api/quotes", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**{lob.title()} insurance quote** created.\n- Quote number: `{result.get('quote_number', result.get('id', 'N/A'))}`\n- State: {state}",
        }

    if intent == "create_matter":
        import datetime as _dt
        title = e.get("matter_title") or f"{(e.get('matter_type') or 'General').title()} Matter"
        payload = {
            "title":            title,
            "matter_type":      e.get("matter_type") or "general",
            "client_name":      f"{e.get('first_name','')} {e.get('last_name','')}".strip() or None,
            "assigned_counsel": e.get("assigned_counsel"),
            "priority":         e.get("priority") or "normal",
            "deadline":         e.get("deadline"),
            "description":      e.get("note_content"),
            "stage":            "intake",
        }
        result = _http_post("/api/matters", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**Legal matter** created: *{title}*\n- Type: {payload['matter_type'].title()}\n- Stage: Intake\n- Priority: {payload['priority'].title()}",
        }

    if intent == "create_clm":
        doc_id = e.get("doc_id")
        if not doc_id:
            return {
                "success": False,
                "result": {},
                "message": "A document ID is required to add a contract to the CLM pipeline. Please specify which document.",
            }
        payload = {
            "doc_id":          doc_id,
            "stage":           "draft",
            "contract_value":  e.get("estimated_value"),
            "counterparty":    e.get("counterparty"),
            "governing_law":   e.get("state"),
            "notes":           e.get("note_content"),
        }
        result = _http_post("/api/clm", payload)
        return {
            "success": True,
            "result": result,
            "message": f"Contract added to **CLM pipeline** at stage *Draft*.\n- Counterparty: {payload['counterparty'] or 'TBD'}\n- Value: {payload['contract_value'] or 'TBD'}",
        }

    if intent == "create_approval":
        doc_id = e.get("doc_id")
        if not doc_id:
            return {
                "success": False,
                "result": {},
                "message": "A document ID is required to create an approval request. Please specify which document to approve.",
            }
        approvers_raw = e.get("approvers") or ""
        approvers = [a.strip() for a in approvers_raw.split(",") if a.strip()] or ["admin"]
        payload = {
            "doc_id":      doc_id,
            "title":       e.get("matter_title") or "Approval Request",
            "description": e.get("note_content"),
            "approvers":   approvers,
            "due_date":    e.get("deadline"),
            "priority":    e.get("priority") or "normal",
        }
        result = _http_post("/api/approvals", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**Approval request** created.\n- Approvers: {', '.join(approvers)}\n- Priority: {payload['priority'].title()}",
        }

    if intent == "create_immigration":
        name = e.get("beneficiary_name") or f"{e.get('first_name','')} {e.get('last_name','')}".strip()
        payload = {
            "doc_type":          e.get("visa_class") or "visa",
            "visa_class":        e.get("visa_class"),
            "beneficiary_name":  name,
            "employer_sponsor":  e.get("employer_sponsor"),
            "valid_until":       e.get("valid_until") or e.get("expiry_date"),
            "status":            "active",
            "notes":             e.get("note_content"),
        }
        result = _http_post(f"/api/immigration/client/{e.get('client_id', 'unknown')}", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**Immigration record** created for **{name}**.\n- Visa/Doc type: {payload['visa_class'] or payload['doc_type']}\n- Valid until: {payload['valid_until'] or 'N/A'}",
        }

    if intent == "create_note":
        payload = {
            "title":   e.get("note_title") or "Note",
            "content": e.get("note_content") or "",
            "doc_id":  e.get("doc_id"),
        }
        result = _http_post("/api/personal/notes", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**Note** saved: *{payload['title']}*",
        }

    if intent == "create_task":
        payload = {
            "title":    e.get("task_title") or e.get("note_title") or "New Task",
            "priority": e.get("priority") or "medium",
            "due_date": e.get("due_date") or e.get("deadline"),
        }
        result = _http_post("/api/personal/planner", payload)
        return {
            "success": True,
            "result": result,
            "message": f"**Task** created: *{payload['title']}*\n- Priority: {payload['priority'].title()}\n- Due: {payload['due_date'] or 'No due date'}",
        }

    # ── UPDATE INTENTS ──────────────────────────────────────────────────────────
    if intent == "update_client":
        name = f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or e.get("client_name", "")
        client_id = e.get("client_id") or _lookup_record_id("clients", name)
        if not client_id:
            return {"success": False, "result": {}, "message": f"Could not find client '{name}'. Please check the name and try again."}
        payload = {k: v for k, v in {
            "first_name": e.get("first_name"), "last_name": e.get("last_name"),
            "email": e.get("email"), "phone": e.get("phone"),
            "address": e.get("address"), "city": e.get("city"),
            "state": e.get("state"), "zip_code": e.get("zip"),
        }.items() if v is not None}
        result = _http_put(f"/api/ams/clients/{client_id}", payload)
        return {"success": True, "result": result, "message": f"**Client** updated successfully: *{name}*"}

    if intent == "update_policy":
        name = f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or e.get("client_name", "")
        policy_id = e.get("policy_id") or _lookup_record_id("policies", e.get("policy_number") or name)
        if not policy_id:
            return {"success": False, "result": {}, "message": f"Could not find policy for '{name or e.get('policy_number')}'. Please check and try again."}
        payload = {k: v for k, v in {
            "carrier_name": e.get("carrier_name"), "line_of_business": e.get("line_of_business"),
            "premium_amount": e.get("premium_amount"), "effective_date": e.get("effective_date"),
            "expiry_date": e.get("expiry_date"), "policy_number": e.get("policy_number"),
            "status": e.get("status"),
        }.items() if v is not None}
        result = _http_put(f"/api/ams/policies/{policy_id}", payload)
        return {"success": True, "result": result, "message": f"**Policy** updated successfully."}

    if intent == "update_claim":
        claim_id = e.get("claim_id") or _lookup_record_id("claims", e.get("claimant_name") or "")
        if not claim_id:
            return {"success": False, "result": {}, "message": "Could not find the claim. Please provide more details."}
        payload = {k: v for k, v in {
            "status": e.get("status"), "claim_type": e.get("claim_type"),
            "estimated_value": e.get("estimated_value"), "incident_date": e.get("incident_date"),
            "claimant_name": e.get("claimant_name"),
        }.items() if v is not None}
        result = _http_patch(f"/api/claims/{claim_id}", payload)
        return {"success": True, "result": result, "message": f"**Claim** updated successfully."}

    if intent == "update_quote":
        quote_id = e.get("quote_id") or _lookup_record_id("quotes", f"{e.get('first_name', '')} {e.get('last_name', '')}".strip())
        if not quote_id:
            return {"success": False, "result": {}, "message": "Could not find the quote. Please provide more details."}
        payload = {k: v for k, v in {
            "status": e.get("status"), "line_of_business": e.get("line_of_business"),
            "premium_amount": e.get("premium_amount"), "carrier_name": e.get("carrier_name"),
            "notes": e.get("note_content"),
        }.items() if v is not None}
        result = _http_patch(f"/api/quotes/{quote_id}", payload)
        return {"success": True, "result": result, "message": "**Quote** updated successfully."}

    if intent == "update_matter":
        matter_id = e.get("matter_id") or _lookup_record_id("matters", e.get("matter_title") or "")
        if not matter_id:
            return {"success": False, "result": {}, "message": "Could not find the matter. Please provide the matter title or ID."}
        payload = {k: v for k, v in {
            "status": e.get("status"), "priority": e.get("priority"),
            "matter_type": e.get("matter_type"), "assigned_counsel": e.get("assigned_counsel"),
            "deadline": e.get("deadline") or e.get("due_date"), "counterparty": e.get("counterparty"),
        }.items() if v is not None}
        result = _http_patch(f"/api/matters/{matter_id}", payload)
        return {"success": True, "result": result, "message": f"**Matter** updated successfully."}

    if intent == "update_clm":
        clm_id = e.get("clm_id") or _lookup_record_id("clm", e.get("matter_title") or e.get("counterparty") or "")
        if not clm_id:
            return {"success": False, "result": {}, "message": "Could not find the contract in CLM. Please provide more details."}
        new_stage = e.get("status") or e.get("clm_stage")
        if new_stage:
            result = _http_patch(f"/api/clm/{clm_id}/move", {"stage": new_stage})
            return {"success": True, "result": result, "message": f"**Contract** moved to stage: *{new_stage}*"}
        payload = {k: v for k, v in {
            "counterparty": e.get("counterparty"), "valid_until": e.get("valid_until") or e.get("expiry_date"),
            "priority": e.get("priority"),
        }.items() if v is not None}
        result = _http_patch(f"/api/clm/{clm_id}", payload)
        return {"success": True, "result": result, "message": "**Contract** updated in CLM pipeline."}

    if intent == "update_immigration":
        record_id = e.get("immigration_id") or _lookup_record_id("immigration", e.get("beneficiary_name") or "")
        if not record_id:
            return {"success": False, "result": {}, "message": "Could not find the immigration record. Please provide the beneficiary name or record ID."}
        payload = {k: v for k, v in {
            "visa_class": e.get("visa_class"), "valid_until": e.get("valid_until") or e.get("expiry_date"),
            "status": e.get("status"), "employer_sponsor": e.get("employer_sponsor"),
        }.items() if v is not None}
        result = _http_patch(f"/api/immigration/{record_id}", payload)
        return {"success": True, "result": result, "message": "**Immigration record** updated successfully."}

    if intent == "update_note":
        note_id = e.get("note_id") or _lookup_record_id("notes", e.get("note_title") or "")
        if not note_id:
            return {"success": False, "result": {}, "message": "Could not find the note. Please provide the note title or ID."}
        payload = {k: v for k, v in {
            "title": e.get("note_title"), "content": e.get("note_content"),
        }.items() if v is not None}
        result = _http_put(f"/api/personal/notes/{note_id}", payload)
        return {"success": True, "result": result, "message": "**Note** updated successfully."}

    if intent == "update_task":
        task_id = e.get("task_id") or _lookup_record_id("tasks", e.get("task_title") or "")
        if not task_id:
            return {"success": False, "result": {}, "message": "Could not find the task. Please provide the task title or ID."}
        payload = {k: v for k, v in {
            "title": e.get("task_title"), "priority": e.get("priority"),
            "due_date": e.get("due_date") or e.get("deadline"), "status": e.get("status"),
        }.items() if v is not None}
        result = _http_put(f"/api/personal/planner/{task_id}", payload)
        return {"success": True, "result": result, "message": "**Task** updated successfully."}

    return {"success": False, "result": {}, "message": f"Unknown action intent: {intent}"}


def _action_preview(intent: str, entities: Dict) -> str:
    """Return a human-readable one-line summary of what the action will do."""
    e = entities or {}
    name = f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or e.get("client_name") or e.get("beneficiary_name") or e.get("claimant_name") or ""
    lob  = e.get("line_of_business") or ""
    op   = "Create" if intent.startswith("create_") else "Update"
    kind = intent.split("_", 1)[1].replace("_", " ").title()
    parts = [f"{op} {kind}"]
    if name:  parts.append(f"for **{name}**")
    if lob:   parts.append(f"({lob})")
    extra_fields = {k: v for k, v in e.items() if v and k not in ("first_name", "last_name", "client_name", "line_of_business")}
    if extra_fields:
        field_strs = [f"{k}: {v}" for k, v in list(extra_fields.items())[:4]]
        parts.append("— " + ", ".join(field_strs))
    return " ".join(parts)


def _execute_action(intent: str, entities: Dict) -> Dict:
    """
    HITL-aware action dispatcher.
    If HITL is enabled → create an approval request and return hitl=True.
    If HITL is off     → execute immediately via _do_execute_action().
    """
    if intent == "delete_request":
        return {
            "success": False,
            "result": {},
            "message": (
                "🚫 **Delete is not available through the Research Agent.**\n\n"
                "I'm sorry, I cannot delete records. "
                "Please go to the relevant page in the application and delete it manually from there."
            ),
        }

    from app.mcp_server.server import hitl_enabled
    if hitl_enabled():
        try:
            from app.services.agent_tools import create_approval_request
            import uuid as _uuid
            run_id  = str(_uuid.uuid4())
            preview = _action_preview(intent, entities)
            result  = create_approval_request(
                run_id=run_id,
                title=f"Research Agent: {preview}",
                description=f"The Research Agent wants to **{preview}**. Review the extracted details below and approve or reject.",
                proposed_action={"intent": intent, "entities": entities},
                reasoning="User issued a natural-language command that resolved to an action intent.",
            )
            approval_id = result.get("approval_id")
            return {
                "success":     False,
                "hitl":        True,
                "approval_id": approval_id,
                "intent":      intent,
                "entities":    entities,
                "preview":     preview,
                "result":      {},
                "message":     f"⏳ **Awaiting your approval** — {preview}\n\nPlease confirm or cancel below.",
            }
        except Exception as hitl_err:
            logger.warning(f"HITL gate failed ({hitl_err}); falling through to direct execution")

    return _do_execute_action(intent, entities)


_PAGE_SIZE = 18  # chunks per page

# ── Follow-up chunk cache ─────────────────────────────────────────────────────
# Keyed by frozenset of doc_ids → list of raw chunk dicts.
# Avoids re-fetching from the DB on every follow-up question for the same dataset.
# TTL: evicted when the cache exceeds 20 entries (LRU-style drop of oldest).
import collections as _collections
_FOLLOWUP_CHUNK_CACHE: "collections.OrderedDict[frozenset, List[Dict]]" = _collections.OrderedDict()
_FOLLOWUP_CACHE_MAX = 20

def _cached_fetch_chunks(doc_ids: List[str]) -> List[Dict]:
    key = frozenset(doc_ids)
    if key in _FOLLOWUP_CHUNK_CACHE:
        _FOLLOWUP_CHUNK_CACHE.move_to_end(key)
        logger.info(f"_cached_fetch_chunks: HIT for {len(doc_ids)} doc(s) — skipping DB")
        return _FOLLOWUP_CHUNK_CACHE[key]
    chunks = _fetch_all_doc_chunks(doc_ids)
    _FOLLOWUP_CHUNK_CACHE[key] = chunks
    _FOLLOWUP_CHUNK_CACHE.move_to_end(key)
    if len(_FOLLOWUP_CHUNK_CACHE) > _FOLLOWUP_CACHE_MAX:
        _FOLLOWUP_CHUNK_CACHE.popitem(last=False)
    logger.info(f"_cached_fetch_chunks: MISS — fetched {len(chunks)} chunks for {len(doc_ids)} doc(s)")
    return chunks


def run_research_agent(
    question: str,
    doc_ids: List[str] = None,
    page: int = 0,
    stream_answer: bool = False,
    context_doc_ids: List[str] = None,   # follow-up: reuse matched docs from prior search
    prior_qa: List[Dict] = None,          # follow-up: [{q, a}, ...] conversation history
) -> Dict:
    """
    Reasoning-first research pipeline:
      Step 0 — QuestionReasoner understands intent, generates search queries
      Step 1 — DocumentFilter narrows scope using date/type hints from the plan
      Step 2 — MultiSearch runs all queries, merges results
      Step 3 — Synthesize writes final answer from chunk context with intent-aware instructions
    """
    steps: List[Dict] = []
    iteration = 0

    def make_step(thought: str, tool: str, args: Dict, observation: Any, latency_ms: int) -> Dict:
        nonlocal iteration
        iteration += 1
        return {
            "iteration": iteration,
            "thought":   thought,
            "tool":      tool,
            "args":      args,
            "observation": observation,
            "latency_ms":  latency_ms,
        }

    # ── Step 0: Understand the question ──────────────────────────────────────
    is_followup = bool(context_doc_ids)

    # ── Follow-up short-circuit ───────────────────────────────────────────────
    # When context_doc_ids are known we skip ALL intent routing (SQL, action,
    # dataset paths) and go straight to direct chunk fetch + chart/synthesis.
    # This must happen before any other branching.
    if is_followup:
        t0 = time.time()
        steps.append(make_step(
            thought=f"Follow-up: reusing {len(context_doc_ids)} matched document(s) — skipping search",
            tool="reason_question",
            args={"follow_up": True, "context_docs": len(context_doc_ids)},
            observation={"reused_docs": context_doc_ids},
            latency_ms=0,
        ))
        chunks = _cached_fetch_chunks(list(context_doc_ids))
        steps.append(make_step(
            thought=f"Fetched {len(chunks)} chunks directly from known documents",
            tool="search_documents",
            args={"doc_ids": list(context_doc_ids), "mode": "direct_fetch"},
            observation={"chunks": chunks[:5], "total": len(chunks)},
            latency_ms=int((time.time() - t0) * 1000),
        ))
        plan = {"intent": "info_retrieve", "doc_type_hint": "general",
                "date_hint": {"type": "none"}, "search_queries": [question]}
        chart_data = _build_chart_from_chunks(question, plan, steps)
        sources = list({ch.get("filename", "") for ch in chunks if ch.get("filename")})
        return {
            "question":        question,
            "plan":            plan,
            "steps":           steps,
            "answer":          "",
            "sources":         sources,
            "iterations":      len(steps),
            "chart_data":      chart_data,
            "matched_doc_ids": list(context_doc_ids),
            "_plan":           plan,
        }

    # ── Traversal short-circuit ───────────────────────────────────────────────
    # "page by page", "full document", etc. on a single selected doc:
    # skip similarity search — fetch chunks in page_number order with SQL OFFSET,
    # then synthesize with the LLM. Returns has_more so the frontend can paginate.
    _TRAVERSAL_CHUNKS_PER_PAGE = 20   # ~10 doc-pages per UI page — fits comfortably in 2400 tokens
    q_lower = question.lower()
    _is_traversal = any(p in q_lower for p in _PAGE_TRAVERSAL_PATTERNS)
    if _is_traversal and doc_ids and len(doc_ids) == 1:
        t0 = time.time()
        from sqlalchemy import text as _text
        doc_id = doc_ids[0]
        offset = page * _TRAVERSAL_CHUNKS_PER_PAGE
        _db = SessionLocal()
        try:
            rows = _db.execute(_text("""
                SELECT chunk_text, page_number, chunk_index
                FROM document_chunks
                WHERE document_id = :doc_id
                  AND chunk_text IS NOT NULL
                ORDER BY page_number ASC, chunk_index ASC
                LIMIT :lim OFFSET :off
            """), {"doc_id": doc_id, "lim": _TRAVERSAL_CHUNKS_PER_PAGE + 1, "off": offset}).fetchall()
        finally:
            _db.close()

        has_more = len(rows) > _TRAVERSAL_CHUNKS_PER_PAGE
        rows = rows[:_TRAVERSAL_CHUNKS_PER_PAGE]

        from collections import defaultdict as _dd
        pages_map: dict = _dd(list)
        for r in rows:
            pages_map[r.page_number].append(r.chunk_text)
        context_parts = []
        for pg in sorted(pages_map.keys()):
            context_parts.append(f"=== Page {pg} ===\n" + "\n".join(pages_map[pg]))
        context = "\n\n".join(context_parts)[:18000]

        steps.append(make_step(
            thought=f"Traversal query: fetching chunks {offset}–{offset+len(rows)} in page order",
            tool="search_documents",
            args={"doc_id": doc_id, "mode": "traversal", "page": page, "offset": offset},
            observation={"chunk_count": len(rows), "has_more": has_more},
            latency_ms=int((time.time() - t0) * 1000),
        ))

        prompt = (
            f"You are a precise document analyst. Summarize each page's content.\n\n"
            f"RULES:\n"
            f"1. Write ## Page N for EVERY page present in the excerpts, in order — no exceptions.\n"
            f"2. Under each heading list every section title, key fact, number, date, and table.\n"
            f"3. Keep each page summary to 3–6 bullet points maximum.\n"
            f"4. If a page only has a title or images, write the title and 'No body text.'\n"
            f"5. Only use information from the excerpts — no outside knowledge.\n"
            f"6. IMPORTANT: cover every ## Page N heading — do not stop early.\n\n"
            f"Document excerpts (ordered by page):\n{context}\n\nPage-by-page breakdown:"
        )
        resp = llm.chat(role="planner", 
            model=_active_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX, "num_predict": 2400, "temperature": 0.05},
        )
        answer = resp["message"]["content"].strip()

        return {
            "question":        question,
            "plan":            {"intent": "info_retrieve", "doc_type_hint": "annual_report"},
            "steps":           steps,
            "answer":          answer,
            "sources":         [doc_id],
            "iterations":      len(steps),
            "matched_doc_ids": [doc_id],
            "has_more":        has_more,
            "page":            page,
        }

    # ── Structured data first: pay-stub facts, spreadsheets/CSVs ──────────────
    # Code computes the answer; runs before the LLM planner (saves 30-40s).
    from app.services.structured_answer import try_structured_answer
    t0 = time.time()
    structured = try_structured_answer(question, get_current_tenant_id() or DEFAULT_TENANT_ID, doc_ids)
    if structured:
        steps.append(make_step(
            thought=f"Computing the answer from structured data ({structured['tool']})",
            tool=structured["tool"],
            args=structured["detail"],
            observation={"sources": structured["sources"]},
            latency_ms=int((time.time() - t0) * 1000),
        ))
        return {
            "question":   question,
            "plan":       {"intent": structured["tool"]},
            "steps":      steps,
            "answer":     structured["answer"],
            "sources":    structured["sources"],
            "iterations": len(steps),
        }

    # ── New query: full ReAct loop ────────────────────────────────────────────
    if True:
        t0 = time.time()
        plan = reason_question(question)
        print(f"{plan}")
        steps.append(make_step(
            thought=f"Understanding question intent: {plan.get('reasoning', '')}",
            tool="reason_question",
            args={"intent": plan.get("intent"), "field": plan.get("field"), "doc_type": plan.get("doc_type_hint"), "date": plan.get("date_hint", {}).get("type")},
            observation={
                "intent":         plan.get("intent"),
                "field":          plan.get("field"),
                "synonyms":       plan.get("field_synonyms", []),
                "date_hint":      plan.get("date_hint"),
                "search_queries": plan.get("search_queries", []),
                "reasoning":      plan.get("reasoning"),
            },
            latency_ms=int((time.time() - t0) * 1000),
        ))
        logger.info(f"Plan: intent={plan.get('intent')} queries={plan.get('search_queries')}")

    # ── Action dispatch: create/add intents hit real API endpoints ────────────
    intent = plan.get("intent", "")
    if intent in _ACTION_INTENTS:
        entities = plan.get("extracted_entities") or {}
        t0 = time.time()
        try:
            action_result = _execute_action(intent, entities)
        except Exception as action_err:
            action_result = {
                "success": False,
                "result": {},
                "message": f"Action failed: {action_err}",
            }
        steps.append(make_step(
            thought=f"Executing action: {intent} with extracted entities",
            tool=intent,
            args=entities,
            observation=action_result,
            latency_ms=int((time.time() - t0) * 1000),
        ))
        answer = action_result["message"]
        if not action_result["success"]:
            answer = f"⚠️ {answer}\n\nPlease check the details and try again, or create the record manually."
        return {
            "question":   question,
            "plan":       plan,
            "steps":      steps,
            "answer":     answer,
            "sources":    [],
            "iterations": len(steps),
        }

    # ── Structured facts query: aggregate/list/trend on transaction documents ──
    _TRANSACTION_INTENTS = {"aggregate", "list", "trend", "rank", "calculate", "compare", "create_chart",
                            "summarize", "info_retrieve", "multi_period"}
    # Also trigger SQL path when question has clear spending/transaction keywords
    _spending_kws = {"spending", "spend", "expense", "transaction", "merchant", "charge",
                     "debit", "credit", "category", "payment", "statement"}
    if any_term(question, _spending_kws):
        intent = intent if intent in _TRANSACTION_INTENTS else "aggregate"
    _TRANSACTION_DOC_TYPES = {"credit_card_statement", "bank_statement"}
    doc_type_hint = plan.get("doc_type_hint", "general")
    date_hint = plan.get("date_hint", {})

    # When doc_type is general and a spending keyword is present, treat as transaction query.
    # Do NOT override for non-financial queries (e.g. insurance comparison, legal review).
    _NON_TRANSACTION_KWS = {
        "insurance", "policy", "claim", "coverage", "premium", "deductible",
        "legal", "contract", "lease", "medical", "payroll", "w2", "pay stub",
        "gap", "audit", "compliance",
    }
    _effective_doc_type = doc_type_hint
    _has_spending_kws = any_term(question, _spending_kws)
    _has_non_txn_kws  = any_term(question, _NON_TRANSACTION_KWS)
    if doc_type_hint == "general" and intent in _TRANSACTION_INTENTS and _has_spending_kws and not _has_non_txn_kws:
        _effective_doc_type = "credit_card_statement"  # allow SQL path; query spans all types

    logger.info(f"query_transactions check: intent={intent} doc_type={doc_type_hint} match={intent in _TRANSACTION_INTENTS and _effective_doc_type in _TRANSACTION_DOC_TYPES}")
    if intent in _TRANSACTION_INTENTS and _effective_doc_type in _TRANSACTION_DOC_TYPES:
        t0 = time.time()
        try:
            from sqlalchemy import text as sql_text
            import json as _json

            date_range = _date_range_from_hint(date_hint)
            term = _transaction_term(plan, question)
            direction = _transaction_direction(question)

            filters = ["d.extracted_facts IS NOT NULL", "d.tenant_id = :tenant_id"]
            params: dict = {"tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID}
            if date_range:
                filters.append("(t->>'date')::date BETWEEN :date_from AND :date_to")
                params["date_from"], params["date_to"] = date_range
            if term:
                # "zelle recipient" won't match a merchant; fall back to the single
                # term that actually occurs in the data ("zelle").
                term = _best_txn_term(term, params["tenant_id"], doc_ids) or term
                filters.append("(t->>'merchant' ILIKE :term OR t->>'category' ILIKE :term)")
                params["term"] = f"%{term}%"
            if direction:
                filters.append("t->>'type' = :direction")
                params["direction"] = direction
            if doc_ids:
                filters.append("d.id::text = ANY(:doc_ids)")
                params["doc_ids"] = list(doc_ids)
            date_filter = f"{date_range[0]}..{date_range[1]}" if date_range else ""
            merchant_filter = term or ""

            logger.info(f"query_transactions SQL: range={date_range} term={term!r} direction={direction} doc_ids={doc_ids!r}")
            db = SessionLocal()
            rows = db.execute(sql_text(f"""
                SELECT
                  d.filename,
                  t->>'date'     AS date,
                  t->>'merchant' AS merchant,
                  t->>'type'     AS type,
                  COALESCE(t->>'category', t->>'type', 'Other') AS category,
                  (t->>'amount')::numeric AS amount
                FROM documents d,
                     jsonb_array_elements(d.extracted_facts->'transactions') t
                WHERE {" AND ".join(filters)}
                ORDER BY (t->>'date')::date
            """), params).fetchall()
            coverage = db.execute(sql_text(f"""
                SELECT MIN((t->>'date')::date), MAX((t->>'date')::date), COUNT(DISTINCT d.id)
                FROM documents d,
                     jsonb_array_elements(d.extracted_facts->'transactions') t
                WHERE d.extracted_facts IS NOT NULL AND d.tenant_id = :tenant_id
                  {"AND d.id::text = ANY(:doc_ids)" if doc_ids else ""}
            """), {k: v for k, v in params.items() if k in ("tenant_id", "doc_ids")}).first()
            if not rows and direction:
                # Direction is inferred from wording and can be wrong — retry without it.
                db = SessionLocal()
                _f = [f for f in filters if "t->>'type'" not in f]
                _p = {k: v for k, v in params.items() if k != "direction"}
                rows = db.execute(sql_text(f"""
                    SELECT d.filename, t->>'date' AS date, t->>'merchant' AS merchant, t->>'type' AS type,
                           COALESCE(t->>'category', t->>'type', 'Other') AS category,
                           (t->>'amount')::numeric AS amount
                    FROM documents d, jsonb_array_elements(d.extracted_facts->'transactions') t
                    WHERE {" AND ".join(_f)}
                    ORDER BY (t->>'date')::date
                """), _p).fetchall()
                db.close()
            logger.info(f"query_transactions: {len(rows)} rows returned")

            if rows:
                total = sum(r.amount for r in rows if r.amount)
                display_rows = rows[:100]

                # Markdown table
                table_lines = [
                    "| Date | Merchant | Amount | Type | Source |",
                    "|------|----------|--------|------|--------|",
                ]
                for r in display_rows:
                    merchant = (r.merchant or "Unknown")[:45]
                    table_lines.append(
                        f"| {r.date} | {merchant} | ${r.amount:,.2f} | {r.type or '-'} | {r.filename} |"
                    )

                table = "\n".join(table_lines)
                summary = f"Found **{len(rows)} transaction(s)** totaling **${total:,.2f}**"
                if len(rows) > 100:
                    summary += f" (showing first 100 of {len(rows)})"

                if coverage and coverage[0]:
                    summary += (
                        f"\n\n_Data available: {coverage[0]:%b %d, %Y} – {coverage[1]:%b %d, %Y} "
                        f"({coverage[2]} statement(s)). Periods outside this range aren't counted._"
                    )

                _q = question.lower()
                _want_max = bool(re.search(r"\b(largest|biggest|highest|max(imum)?|most expensive|top)\b", _q))
                _want_min = bool(re.search(r"\b(smallest|lowest|min(imum)?|cheapest|least)\b", _q))
                if (_want_max or _want_min) and rows:
                    pick = (max if _want_max else min)(rows, key=lambda r: r.amount or 0)
                    summary = (f"The {'largest' if _want_max else 'smallest'} is **${pick.amount:,.2f}** — "
                               f"{pick.merchant} on {pick.date} ([{pick.filename}]).\n\n" + summary)
                answer = f"{summary}\n\n{table}"

                # Chart detection — build Chart.js data when user asks for visual
                chart_data = None
                try:
                    from app.services.chart_engine import detect_viz_intent, build_chart_data
                    viz = detect_viz_intent(question)
                    if viz["viz_type"] or viz["group_by"]:
                        chart_data = build_chart_data(
                            doc_type=_effective_doc_type,
                            group_by=viz["group_by"],
                            viz_type=viz["viz_type"],
                            rows=rows,
                        )
                    logger.info(f"chart_engine: viz={viz} chart_data={'built' if chart_data else 'None'}")
                except Exception as chart_err:
                    logger.warning(f"chart_engine failed (non-fatal): {chart_err}")

                obs = {
                    "transaction_count": len(rows),
                    "total_amount": float(total),
                    "source": "extracted_facts_sql",
                }

                steps.append(make_step(
                    thought=f"Querying extracted_facts JSONB for {intent} on {doc_type_hint}",
                    tool="query_transactions",
                    args={"date_filter": date_filter, "merchant_filter": merchant_filter},
                    observation=obs,
                    latency_ms=int((time.time() - t0) * 1000),
                ))
                return {
                    "question":   question,
                    "plan":       plan,
                    "steps":      steps,
                    "answer":     answer,
                    "sources":    list({r.filename for r in rows}),
                    "iterations": len(steps),
                    "chart_data": chart_data,
                }
        except Exception as sql_err:
            logger.warning(f"query_transactions failed: {sql_err} — falling back to vector search")

    # ── Query rescue: vague compare/cross_doc with no useful field ────────────
    # When the LLM produces queries that mirror the question verbatim ("compare
    # these statements") rather than document field names, inject domain-aware
    # queries based on the doc_type_hint so the vector search actually hits chunks.
    _FALLBACK_QUERIES: Dict[str, List[str]] = {
        # ── Payroll ───────────────────────────────────────────────────────────
        "pay_stub": [
            "gross pay gross earnings gross wages gross compensation",
            "net pay take home net check net wages direct deposit",
            "federal income tax state tax social security medicare fica oasdi",
            "deductions withholdings pre-tax post-tax total withheld",
            "medical insurance health dental vision 401k retirement benefit",
            "pay period pay date hours worked regular overtime",
        ],
        "payroll_report": [
            "payroll gross wages total compensation labor cost",
            "net pay direct deposit employee earnings summary",
            "federal state tax withholding fica medicare",
            "deductions benefits 401k health insurance",
        ],
        # ── Banking ──────────────────────────────────────────────────────────
        "bank_statement": [
            "ending balance closing balance available balance current balance",
            "total deposits credits direct deposits incoming transfers",
            "total withdrawals debits payments outgoing transfers",
            "transactions checks ach wire fees charges",
            "opening balance beginning balance statement period",
        ],
        "credit_card_statement": [
            "statement balance minimum payment due date",
            "purchases charges credits returns payments",
            "credit limit available credit interest rate APR",
            "rewards points cash back earned redeemed",
            "previous balance new charges payment received",
        ],
        # ── Invoicing / AP ───────────────────────────────────────────────────
        "invoice": [
            "invoice total amount due grand total balance due",
            "line items unit price quantity subtotal",
            "payment terms due date net 30 net 60 payment date",
            "vendor supplier billing address invoice number",
            "tax vat gst discount freight shipping charges",
        ],
        "purchase_order": [
            "purchase order PO number line items quantity",
            "unit price extended price total order amount",
            "vendor supplier ship to billing address",
            "delivery date required date payment terms",
            "part number SKU description unit of measure",
        ],
        "expense_report": [
            "expense total reimbursement amount",
            "expense category date merchant description",
            "travel lodging meals mileage per diem",
            "employee name department project code",
        ],
        "receipt": [
            "receipt total amount paid payment method",
            "merchant store date time transaction",
            "items purchased quantity price subtotal tax",
        ],
        "credit_memo": [
            "credit memo amount credit note refund",
            "original invoice adjustment reason",
            "credit balance applied amount credited",
        ],
        "financial_statement": [
            "revenue gross profit operating income net income",
            "total assets total liabilities stockholders equity",
            "operating cash flow capital expenditure free cash flow",
            "earnings per share EPS EBITDA gross margin",
        ],
        "sales_report": [
            "total sales revenue gross sales net sales",
            "units sold quantity by product region",
            "quota attainment ytd sales commission earned",
            "month over month growth sales target achieved",
        ],
        # ── Insurance ────────────────────────────────────────────────────────
        "insurance": [
            "premium coverage deductible out of pocket maximum",
            "plan paid insurance covered patient responsibility copay coinsurance",
            "policy number group number member id claim number",
            "benefit summary covered services excluded services",
            "policy expiration renewal date effective date coverage period",
        ],
        "declarations_page": [
            "policy number insured named insured property address",
            "coverage amount dwelling personal property liability",
            "premium total annual effective date expiration date",
            "deductible hurricane wind flood earthquake",
        ],
        "insurance_policy": [
            "policy terms coverage definitions exclusions",
            "endorsement rider amendment coverage limit",
            "insuring agreement conditions declarations",
        ],
        "insurance_claim": [
            "claim number date of loss claimant",
            "claim amount paid settled adjuster",
            "EOB explanation of benefits remittance",
            "procedure code diagnosis code billed allowed paid",
        ],
        # ── Corporate / SEC filings ──────────────────────────────────────────
        "annual_report": [
            "total revenue net sales gross revenue",
            "net income net loss operating income earnings per share EPS",
            "total assets total liabilities stockholders equity",
            "operating cash flow free cash flow capital expenditure",
            "gross margin operating margin net margin EBITDA",
            "risk factors material risks business outlook guidance",
        ],
        "quarterly_report": [
            "quarterly revenue net income operating income",
            "Q1 Q2 Q3 Q4 earnings per share",
            "year over year growth quarter over quarter",
            "guidance raised lowered maintained outlook",
        ],
        "proxy": [
            "executive compensation salary bonus equity awards",
            "board of directors nominees election vote",
            "shareholder proposal resolution say on pay",
            "audit committee compensation committee governance",
        ],
        # ── Tax ──────────────────────────────────────────────────────────────
        "tax_form": [
            "adjusted gross income AGI taxable income",
            "federal income tax withheld tax refund tax owed",
            "wages salaries tips reported income",
            "deductions exemptions credits standard deduction itemized",
            "W2 1099 NEC MISC INT DIV self employment income",
        ],
        "w2": [
            "wages tips compensation box 1 federal income tax box 2",
            "social security wages box 3 medicare wages box 5",
            "state wages income tax box 16 box 17",
            "401k box 12 code D health insurance W2",
        ],
        "tax_return": [
            "adjusted gross income AGI total income taxable income",
            "standard deduction itemized deduction",
            "tax liability credits refund amount owed",
            "schedule A B C D self employment",
        ],
        "government_form": [
            "form number instructions fields required",
            "agency department federal state",
            "signature date applicant information",
        ],
        "property_tax_bill": [
            "parcel number APN total tax amount due date",
            "first installment second installment delinquent date",
            "assessed value land value improvement value",
            "tax rate area general tax levy special assessments",
            "property address owner name exemptions",
        ],
        # ── Medical ──────────────────────────────────────────────────────────
        "medical": [
            "total charges billed amount insurance paid patient owes",
            "diagnosis procedure code CPT ICD billing code",
            "date of service provider name facility charges",
            "deductible met out of pocket paid coinsurance copay",
            "prescription drug dosage medication cost",
            "lab results test values normal range",
        ],
        "medical_record": [
            "patient name DOB diagnosis condition",
            "physician provider attending date of visit",
            "vitals blood pressure heart rate temperature weight",
            "medications prescribed dosage frequency",
            "lab results CBC metabolic panel imaging findings",
            "discharge summary follow up instructions",
        ],
        # ── Real estate & lending ────────────────────────────────────────────
        "real_estate": [
            "purchase price sale price property address",
            "monthly payment principal interest taxes insurance PITI",
            "closing costs escrow down payment earnest money",
            "lease term rent amount security deposit",
            "square footage bedrooms bathrooms lot size",
        ],
        "mortgage_document": [
            "loan amount interest rate term years monthly payment",
            "principal balance amortization schedule",
            "borrower lender property address loan number",
            "origination date closing date first payment date",
        ],
        "mortgage_statement": [
            "current balance principal interest escrow payment",
            "next payment due amount due statement date",
            "principal paid interest paid year to date",
            "escrow balance property tax insurance paid",
        ],
        "closing_disclosure": [
            "closing costs loan amount interest rate APR",
            "cash to close down payment prepaid items",
            "lender credits origination charges title fees",
            "total closing costs cash due at closing",
        ],
        "loan_document": [
            "loan amount interest rate monthly payment",
            "loan term maturity date APR total interest",
            "borrower co-borrower lender servicer",
        ],
        "payoff_statement": [
            "payoff amount good through date per diem",
            "outstanding principal interest accrued fees",
        ],
        # ── Legal ────────────────────────────────────────────────────────────
        "legal": [
            "plaintiff defendant case number court",
            "complaint motion judgment order ruling",
            "effective date parties obligations terms",
            "damages liability settlement amount",
        ],
        "contract": [
            "effective date start date end date term duration",
            "payment terms milestones deliverables obligations",
            "termination clause breach notice period",
            "parties involved agreement scope of work",
            "governing law jurisdiction dispute resolution arbitration",
        ],
        # ── HR ───────────────────────────────────────────────────────────────
        "hr_document": [
            "employee name title department start date",
            "salary base pay compensation bonus equity",
            "performance rating goals objectives review",
            "offer letter employment terms benefits",
            "termination separation severance notice",
        ],
        # ── Immigration ──────────────────────────────────────────────────────
        "immigration": [
            "visa expiration authorized stay until admission until",
            "i-94 arrival departure record status",
            "passport number date of birth nationality citizenship",
            "visa type H1B F1 O1 green card EAD work authorization",
            "i-20 DS-2019 petition approval notice",
        ],
        "uscis_receipt": [
            "receipt notice I-797 receipt number priority date",
            "petition type I-140 I-485 I-131 I-765",
            "beneficiary petitioner employer applicant",
            "case status approval notice valid period",
        ],
        "immigration_receipt": [
            "i-94 admission record authorized stay",
            "i-20 program start end date SEVIS",
            "EAD card employment authorization valid through",
        ],
        # ── Utility ──────────────────────────────────────────────────────────
        "utility_bill": [
            "account number billing period due date amount due",
            "usage kilowatt hours kWh therms gallons",
            "current charges previous balance payments credits",
            "meter reading consumption rate tariff",
        ],
        # ── Investment ───────────────────────────────────────────────────────
        "investment": [
            "account value portfolio balance total assets",
            "holdings positions shares price market value",
            "dividends interest income capital gains",
            "realized unrealized gain loss",
            "mutual fund ETF stock bond allocation",
        ],
        # ── Operational / internal ───────────────────────────────────────────
        "inventory_report": [
            "SKU part number item description quantity on hand",
            "reorder point reorder quantity stock level",
            "warehouse location bin storage unit cost",
        ],
        "forecast_report": [
            "forecast budget projection target",
            "revenue cost headcount quarterly annual",
            "variance actual vs plan over under",
        ],
        "employee_roster": [
            "employee name ID title department hire date",
            "manager reporting location headcount FTE",
        ],
        "order_history": [
            "order number date status shipped delivered",
            "customer product quantity amount total",
        ],
        "transaction_log": [
            "transaction ID date amount type debit credit",
            "journal entry ledger general ledger balance",
        ],
        "asset_register": [
            "asset ID name category acquisition cost",
            "depreciation book value useful life",
        ],
        "customer_list": [
            "customer name account ID contact email",
            "company address phone region account type",
        ],
        "product_catalog": [
            "product name SKU part number price",
            "description category vendor availability",
        ],
        # ── Datasets ─────────────────────────────────────────────────────────
        "dataset": ["column field value record total count average"],
        "movie_dataset": ["title genre year director rating box office revenue"],
        "music_dataset": ["song title artist album genre streams plays"],
        "book_dataset": ["title author ISBN genre year rating publisher"],
        "product_dataset": ["product name price category brand rating reviews"],
        "ratings_dataset": ["rating score review sentiment user item"],
        "stock_dataset": ["ticker symbol open high low close volume market cap"],
        "health_dataset": ["patient age gender BMI condition diagnosis outcome"],
        "weather_dataset": ["temperature precipitation humidity wind speed date"],
        "sports_dataset": ["team player game score stats wins losses"],
        "real_estate_dataset": ["address price sqft bedrooms bathrooms sold date"],
        "education_dataset": ["student grade score enrollment school district"],
        "census_dataset": ["population age income household county city"],
        "scientific_dataset": ["sample measurement experiment result p-value"],
        "survey_dataset": ["question response rating respondent NPS satisfaction"],
        "event_dataset": ["event type timestamp user session pageview conversion"],
        "location_dataset": ["city state country latitude longitude zip code"],
        "spreadsheet": ["row column cell value total sum average count"],
        # ── General fallback ─────────────────────────────────────────────────
        "general": [
            "total amount balance gross net earnings income",
            "date period statement summary key figures",
            "deductions payments charges credits balance",
            "name account reference number identifier",
            "total due paid outstanding remaining",
        ],
    }
    _VAGUE_INTENTS = {"compare", "cross_doc", "info_retrieve", "summarize", "anomaly", "multi_period"}
    _queries = plan.get("search_queries", [])
    _field   = plan.get("field")
    _intent  = plan.get("intent", "info_retrieve")
    # Detect vague queries: all queries contain words from the original question
    # and none contain domain-specific financial terms
    _domain_terms = {
        # ── Finance / Payroll ──────────────────────────────────────────────────
        "pay", "gross", "net", "wages", "earnings", "salary", "compensation",
        "deduction", "withholding", "fica", "401k", "medicare", "oasdi",
        "balance", "deposit", "withdrawal", "credit", "debit", "transaction",
        "amount", "total", "invoice", "due", "payment", "charge", "fee",
        "premium", "coverage", "deductible", "copay", "coinsurance", "claim",
        "income", "revenue", "profit", "loss", "equity", "asset", "liability",
        "tax", "refund", "agi", "exemption", "withhold", "filing",
        "principal", "interest", "mortgage", "rent", "lease", "escrow",
        # ── HR / Employment ────────────────────────────────────────────────────
        "employee", "employer", "job", "title", "position", "department",
        "hire", "termination", "resignation", "severance", "onboarding",
        "performance", "review", "appraisal", "rating", "goal", "kpi",
        "leave", "vacation", "pto", "sick", "fmla", "benefit", "enrollment",
        "headcount", "roster", "org", "reporting", "manager", "supervisor",
        # ── Legal / Compliance ─────────────────────────────────────────────────
        "contract", "agreement", "clause", "term", "obligation", "liability",
        "milestone", "deliverable", "warranty", "indemnification", "breach",
        "termination", "jurisdiction", "governing", "arbitration", "dispute",
        "nda", "confidentiality", "intellectual", "property", "ip", "license",
        "regulation", "compliance", "policy", "procedure", "audit", "penalty",
        "statute", "law", "court", "verdict", "settlement", "judgment",
        # ── Medical / Healthcare ───────────────────────────────────────────────
        "diagnosis", "procedure", "icd", "cpt", "billed", "prescription",
        "medication", "dosage", "drug", "pharmacy", "formulary",
        "lab", "test", "result", "value", "range", "specimen",
        "patient", "provider", "physician", "facility", "hospital", "clinic",
        "insurance", "eob", "prior", "authorization", "referral", "specialist",
        "symptom", "treatment", "therapy", "surgery", "discharge", "admission",
        # ── Real Estate / Property ─────────────────────────────────────────────
        "property", "address", "parcel", "zoning", "appraisal", "assessment",
        "purchase", "sale", "closing", "escrow", "title", "deed",
        "square", "footage", "bedroom", "bathroom", "lot", "acre",
        "hoa", "maintenance", "inspection", "contingency", "earnest",
        "amortization", "ltv", "piti", "down", "payment", "refinance",
        # ── Immigration / Visa ─────────────────────────────────────────────────
        "visa", "passport", "nationality", "citizenship", "status",
        "i-94", "i-20", "ds-2019", "i-140", "i-485", "ead", "petition",
        "admission", "expiration", "authorized", "stay", "departure",
        "sponsor", "alien", "beneficiary", "priority", "date", "consulate",
        "h1b", "h4", "f1", "j1", "o1", "l1", "tn", "eb1", "eb2", "eb3",
        # ── Supply Chain / Logistics ───────────────────────────────────────────
        "purchase", "order", "po", "sku", "part", "quantity", "shipment",
        "vendor", "supplier", "procurement", "fulfillment", "warehouse",
        "tracking", "carrier", "freight", "delivery", "lead", "time",
        "inventory", "stock", "reorder", "backorder", "receipt", "goods",
        "incoterm", "customs", "tariff", "duty", "manifest", "packing",
        # ── Insurance (non-financial fields) ──────────────────────────────────
        "policy", "renewal", "endorsement", "exclusion", "rider", "limit",
        "peril", "loss", "subrogation", "adjuster", "underwriter", "binder",
        "certificate", "coi", "additional", "insured", "named", "beneficiary",
        # ── Utilities / Telecom ────────────────────────────────────────────────
        "usage", "kwh", "therm", "gallon", "meter", "reading", "consumption",
        "tariff", "rate", "tier", "plan", "data", "minutes", "overage",
        "account", "billing", "cycle", "service", "disconnect", "reconnect",
        # ── General document fields ────────────────────────────────────────────
        "date", "period", "name", "reference", "number", "identifier",
        "summary", "report", "statement", "record", "document", "form",
        "signature", "notary", "effective", "expiry", "version", "revision",
    }
    _queries_have_domain_terms = any(
        any(ft in q.lower() for ft in _domain_terms) for q in _queries
    )
    if _intent in _VAGUE_INTENTS and not _field and not _queries_have_domain_terms:
        _doc_hint = plan.get("doc_type_hint", "general")
        _fallback = _FALLBACK_QUERIES.get(_doc_hint) or _FALLBACK_QUERIES["general"]
        logger.info(f"Query rescue: replacing vague queries with domain queries for {_doc_hint}")
        plan["search_queries"] = _fallback
        if not plan.get("field_synonyms"):
            plan["field_synonyms"] = _fallback[0].split()

    # ── Step 0b: Pandas numeric filter shortcut for data_table docs ─────────────
    # For queries like "how many rows where col > N", vector search gives wrong
    # counts (only sees sampled chunks). Run pandas directly on the CSV file.
    # Match patterns like:
    #   "greater than 3000"  → op immediately before number
    #   "greater than sqft_lot 3000"  → column name between op and number
    #   "sqft_lot > 3000"  → symbolic operator
    _CMP_WORDS = r'(?:greater\s+than|more\s+than|larger\s+than|above|over|less\s+than|smaller\s+than|below|under|equal\s+to|equals?|at\s+least|at\s+most)'
    _numeric_pat = re.compile(
        r'(' + _CMP_WORDS + r')(?:\s+\S+)?\s+(\d[\d,]*(?:\.\d+)?)'   # word op, optional col, number
        r'|(\S+)\s*([><=!]+)\s*(\d[\d,]*(?:\.\d+)?)',                  # col op_symbol number
        re.IGNORECASE,
    )
    _nm = _numeric_pat.search(question)
    if _nm and not is_followup:
        if _nm.group(1):   # word-form match
            _op_word = _nm.group(1).lower()
            _num_val_str = _nm.group(2).replace(',', '')
        else:              # symbol-form match
            _op_word = _nm.group(4)
            _num_val_str = _nm.group(5).replace(',', '')
        _num_val = float(_num_val_str)
        _op_word_l = _op_word.lower()
        if any(w in _op_word_l for w in ('greater', 'more', 'larger', 'above', 'over', '>')):
            _pandas_op = '>'
        elif any(w in _op_word_l for w in ('less', 'smaller', 'below', 'under', '<')):
            _pandas_op = '<'
        elif '>=' in _op_word_l or 'at least' in _op_word_l:
            _pandas_op = '>='
        elif '<=' in _op_word_l or 'at most' in _op_word_l:
            _pandas_op = '<='
        else:
            _pandas_op = '=='

        # Find all data_table docs in scope
        _db_dt = SessionLocal()
        try:
            _dt_rows = _db_dt.execute(__import__('sqlalchemy').text(
                "SELECT id, filename, file_path FROM documents WHERE status='completed' AND doc_type IN ('data_table','spreadsheet','dataset')"
            )).fetchall()
        except Exception:
            _dt_rows = []
        finally:
            _db_dt.close()

        _scope_ids = set(doc_ids or [])
        _pandas_answers = []
        for _dtr in _dt_rows:
            if _scope_ids and str(_dtr.id) not in _scope_ids:
                continue
            _fp = _dtr.file_path
            if not _fp or not __import__('os').path.exists(_fp):
                continue
            try:
                import pandas as _pd
                _ext = __import__('os').path.splitext(_fp)[1].lower()
                if _ext in ('.csv', '.tsv', '.txt'):
                    _df = _pd.read_csv(_fp, low_memory=False)
                elif _ext in ('.xlsx', '.xls'):
                    _df = _pd.read_excel(_fp)
                else:
                    continue

                # Find column matching words in question (best-effort)
                _q_words = set(re.sub(r'[^a-z0-9_]', ' ', question.lower()).split())
                _best_col = None
                _best_score = 0
                for _col in _df.columns:
                    _col_words = set(re.sub(r'[^a-z0-9_]', '_', _col.lower()).replace('_', ' ').split())
                    _score = len(_q_words & _col_words)
                    if _score > _best_score:
                        _best_score = _score
                        _best_col = _col

                if _best_col is None or _best_score == 0:
                    continue

                # Coerce column to numeric; skip if can't
                _series = _pd.to_numeric(_df[_best_col], errors='coerce')
                if _series.isna().all():
                    continue

                if _pandas_op == '>':
                    _filtered = _df[_series > _num_val]
                elif _pandas_op == '<':
                    _filtered = _df[_series < _num_val]
                elif _pandas_op == '>=':
                    _filtered = _df[_series >= _num_val]
                elif _pandas_op == '<=':
                    _filtered = _df[_series <= _num_val]
                else:
                    _filtered = _df[_series == _num_val]

                _count = len(_filtered)
                _pandas_answers.append({
                    "filename": _dtr.filename,
                    "column": _best_col,
                    "op": _pandas_op,
                    "threshold": _num_val,
                    "count": _count,
                    "total": len(_df),
                })
            except Exception as _pe:
                logger.warning(f"Pandas numeric filter failed for {_dtr.filename}: {_pe}")

        if _pandas_answers:
            _ans_lines = []
            _src_names = []
            _chart_labels = []
            _chart_values = []
            for _pa in _pandas_answers:
                _op_label = {'>' : 'greater than', '<': 'less than', '>=': 'at least', '<=': 'at most', '==': 'equal to'}.get(_pa['op'], _pa['op'])
                _ans_lines.append(
                    f"**{_pa['filename']}**: {_pa['count']} out of {_pa['total']} rows have `{_pa['column']}` {_op_label} {_pa['threshold']:g}"
                )
                _src_names.append(_pa['filename'])
                _chart_labels.append(_pa['filename'])
                _chart_values.append(_pa['count'])
            _ans_text = "\n".join(_ans_lines)
            _chart_data = {
                "type": "bar",
                "labels": _chart_labels,
                "datasets": [{"label": f"Count ({_pandas_op} {_num_val:g})", "data": _chart_values}],
            } if _chart_labels else None
            logger.info(f"PandasShortcut: answered numeric filter query directly ({len(_pandas_answers)} docs)")
            return {
                "answer": _ans_text,
                "sources": _src_names,
                "steps": steps,
                "plan": plan,
                "has_more": False,
                "context_doc_ids": [],
                "chart_data": _chart_data,
            }

    # ── Step 1: Filter documents by date/type/domain hints ───────────────────
    # Run ANY time we have a meaningful signal — date, doc_type, or field.
    # Previously this only ran on date hints, which meant domain exclusion
    # (e.g. blocking pay stubs from a resume query) was never applied.
    # Follow-up: skip filtering entirely — reuse doc ids from the prior turn.
    if is_followup:
        effective_doc_ids = list(context_doc_ids)
    else:
        effective_doc_ids = list(doc_ids or [])
    date_hint    = plan.get("date_hint", {})
    doc_type_hit = plan.get("doc_type_hint", "general")
    has_filter_signal = not is_followup and (
        (date_hint.get("type") != "none" and (date_hint.get("months") or date_hint.get("years")))
        or (doc_type_hit and doc_type_hit != "general")
        or plan.get("field")
    )
    # Safety: if LLM guessed a "corporate" doc_type (quarterly_report, annual_report)
    # but the question has clear dataset/tabular signals, reset to general to avoid
    # incorrectly excluding data_table/spreadsheet documents from the search scope.
    _corporate_types = {"quarterly_report", "annual_report", "proxy", "financial_statement"}
    _dataset_signals = {"trend", "monthly", "product", "sales", "show", "chart", "group by",
                        "by month", "by product", "by region", "by category", "visualization"}
    if doc_type_hit in _corporate_types and any(kw in question.lower() for kw in _dataset_signals):
        logger.info(f"DocTypeGuard: resetting '{doc_type_hit}' → 'general' (dataset signal in question)")
        plan["doc_type_hint"] = "general"
        doc_type_hit = "general"
        has_filter_signal = bool(date_hint.get("months") or date_hint.get("years"))

    if has_filter_signal:
        plan["_question"] = question   # consumed by _filter_docs_by_plan for personal-Q detection
        t0 = time.time()
        filtered = _filter_docs_by_plan(plan, effective_doc_ids)
        if filtered:
            effective_doc_ids = filtered
            hint_desc = (
                date_hint.get("range_description") or
                (", ".join(date_hint.get("months", [])) if date_hint.get("months") else None) or
                doc_type_hit or
                plan.get("field") or "type/date hint"
            )
            steps.append(make_step(
                thought=f"Narrowing to {len(filtered)} document(s) matching: {hint_desc}",
                tool="list_documents",
                args={"doc_type": doc_type_hit, "date_hint": date_hint, "matched": len(filtered)},
                observation={"matched_docs": filtered[:10], "total": len(filtered)},
                latency_ms=int((time.time() - t0) * 1000),
            ))

    # ── Step 2: Multi-query semantic search ───────────────────────────────────
    # Quick guard: skip embedding entirely if no documents exist in the DB.
    t0 = time.time()
    from sqlalchemy import text as _sa_text
    _db_check = SessionLocal()
    try:
        _doc_count = _db_check.execute(
            _sa_text("SELECT COUNT(*) FROM documents WHERE status='completed'")
        ).scalar()
    except Exception:
        _doc_count = 1  # assume docs exist on any DB error; let search handle it
    finally:
        _db_check.close()

    if _doc_count == 0:
        return {
            "question":   question,
            "answer": "No documents have been uploaded yet. Please upload documents first, then ask your question.",
            "sources": [],
            "steps": steps,
            "plan": plan,
            "iterations": iteration,
        }

    # Only the user's own selection is a hard scope; documents narrowed by the
    # date/type filter (or a follow-up's prior docs) are a preference.
    search_results = _multi_search(plan, list(doc_ids or []),
                                   prefer_doc_ids=effective_doc_ids if effective_doc_ids != list(doc_ids or []) else None)

    # Fallback A: unscoped — only when the domain filter found zero candidates.
    # Keeping scoped prevents cross-domain bleed (lexicon-annual in a resume query).
    if not search_results and not effective_doc_ids:
        logger.info("No doc scope and no results — retrying unscoped")
        search_results = _multi_search(plan, [])

    # Fallback A2: filtered docs returned no semantic hits — fetch chunks directly
    # from the filtered set (e.g. W2 with heavily redacted chunk_text).
    if not search_results and effective_doc_ids:
        logger.info(f"Filtered docs {effective_doc_ids} returned no semantic hits — falling back to direct chunk fetch")
        _fa2_db = SessionLocal()
        try:
            from sqlalchemy import text as _sa_text_a2
            _fa2_vec = _get_embedding_model().encode(question).tolist()
            _fa2_phs = ", ".join(f":fa2doc_{i}" for i in range(len(effective_doc_ids)))
            _fa2_params: Dict[str, Any] = {"vec": str(_fa2_vec), "limit": 14}
            for i, did in enumerate(effective_doc_ids):
                _fa2_params[f"fa2doc_{i}"] = did
            _fa2_rows = _fa2_db.execute(_sa_text_a2(f"""
                SELECT c.document_id, c.chunk_text, d.filename, d.doc_type,
                       1 - (c.embedding <=> CAST(:vec AS vector(768))) AS similarity
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  AND d.status = 'completed'
                  AND vector_dims(c.embedding) = 768
                  AND d.id::text IN ({_fa2_phs})
                ORDER BY similarity DESC
                LIMIT :limit
            """), _fa2_params).fetchall()
            search_results = [
                {"doc_id": str(r.document_id), "filename": r.filename,
                 "text": (r.chunk_text or "")[:600],
                 "similarity": round(float(r.similarity), 3)}
                for r in _fa2_rows
            ]
            logger.info(f"Fallback A2 direct fetch returned {len(search_results)} chunks from filtered docs")
        except Exception as _fa2_err:
            logger.warning(f"Fallback A2 failed: {_fa2_err}")
        finally:
            _fa2_db.close()

    # Fallback B: low-threshold direct chunk fetch — fires when:
    #   • The user explicitly selected one or more documents (doc_ids non-empty), AND
    #   • Semantic search still returned nothing (generic questions like "analyze this
    #     document" or "summarize" produce query vectors that may not clear 0.25 against
    #     any specific document's chunks).
    # In this case we know exactly which documents to read, so bypass the embedding
    # threshold and take the top chunks by raw similarity (no floor).
    if not search_results and doc_ids:
        logger.info(f"User-selected docs {doc_ids} returned no semantic hits — falling back to direct chunk fetch")
        _fb_db = SessionLocal()
        try:
            from sqlalchemy import text as _sa_text2
            _fb_vec = _get_embedding_model().encode(question).tolist()
            _placeholders = ", ".join(f":fbdoc_{i}" for i in range(len(doc_ids)))
            _fb_params: Dict[str, Any] = {"vec": str(_fb_vec), "limit": 14}
            for i, did in enumerate(doc_ids):
                _fb_params[f"fbdoc_{i}"] = did
            _fb_rows = _fb_db.execute(_sa_text2(f"""
                SELECT c.document_id, c.chunk_text, d.filename, d.doc_type,
                       1 - (c.embedding <=> CAST(:vec AS vector(768))) AS similarity
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  AND d.status = 'completed'
                  AND vector_dims(c.embedding) = 768
                  AND d.id::text IN ({_placeholders})
                ORDER BY similarity DESC
                LIMIT :limit
            """), _fb_params).fetchall()
            search_results = [
                {"doc_id": str(r.document_id), "filename": r.filename,
                 "text": (r.chunk_text or "")[:600],
                 "similarity": round(float(r.similarity), 3)}
                for r in _fb_rows
            ]
            logger.info(f"Direct chunk fallback returned {len(search_results)} chunks")
        except Exception as _fb_err:
            logger.warning(f"Direct chunk fallback failed: {_fb_err}")
        finally:
            _fb_db.close()

    # First page: read the chosen documents whole; "load more" pages keep passages
    if page == 0:
        search_results = _expand_to_full_documents(search_results, plan)

    # Paginate: slice the chunk window for this page, expose has_more
    chunk_start = page * _PAGE_SIZE
    chunk_end   = chunk_start + _PAGE_SIZE
    has_more    = len(search_results) > chunk_end
    page_chunks = search_results[chunk_start:chunk_end]

    steps.append(make_step(
        thought=f"Searching with {len(plan.get('search_queries', []))} query variants for '{plan.get('field') or question[:60]}'",
        tool="search_documents",
        args={"queries": plan.get("search_queries", []), "doc_scope": len(effective_doc_ids), "page": page},
        observation=page_chunks[:12],
        latency_ms=int((time.time() - t0) * 1000),
    ))
    logger.info(f"Multi-search returned {len(search_results)} chunks total, serving page {page} ({len(page_chunks)} chunks)")

    # ── Step 3: Synthesize final answer ───────────────────────────────────────
    if stream_answer:
        # For dataset doc types with viz intent, build chart from chunks directly
        # (the LLM cannot aggregate hundreds of raw rows)
        _viz_intents = {"aggregate", "create_chart", "trend", "rank", "compare", "multi_period"}
        _dt = plan.get("doc_type_hint", "general")
        _intent = plan.get("intent", "")
        _stream_chart = None
        _all_chunks = _collect_chunks(steps)
        _sql_only_types = {"credit_card_statement", "bank_statement"}

        # Always try tabular parsing first — chunks may be pipe-delimited Excel/CSV
        # regardless of what doc_type the LLM guessed (e.g. "quarterly_report" for a
        # product dataset query mentioning "trend 2023").
        if _all_chunks:
            _stream_chart = _build_chart_from_chunks(question, plan, steps)

        # If tabular parsing yielded nothing and this is a viz/aggregate intent,
        # try key-value extraction from unstructured text chunks (PDF/Word docs).
        if not _stream_chart and _intent in _viz_intents and _dt not in _sql_only_types and _all_chunks:
            _stream_chart = _extract_kv_from_chunks(_all_chunks, question)

        # Collect matched doc ids from list_documents step or search results
        _matched_ids: List[str] = list(effective_doc_ids)
        if not _matched_ids:
            for _s in steps:
                if _s.get("tool") == "list_documents":
                    _matched_ids = (_s.get("observation") or {}).get("matched_docs") or []
                    break
        if not _matched_ids:
            _matched_ids = list({c.get("doc_id","") for c in _all_chunks if c.get("doc_id")})

        return {
            "question":        question,
            "_plan":           plan,
            "steps":           steps,
            "answer":          "",
            "sources":         [],
            "iterations":      len(steps),
            "page":            page,
            "has_more":        has_more,
            "chart_data":      _stream_chart,
            "matched_doc_ids": _matched_ids,
        }

    t0 = time.time()
    synth = _synthesize(question, plan, steps, page=page)
    steps.append(make_step(
        thought="Synthesizing findings into a final answer",
        tool="final_answer",
        args={"answer": synth["answer"], "sources": synth["sources"]},
        observation=None,
        latency_ms=int((time.time() - t0) * 1000),
    ))

    return {
        "question":        question,
        "plan":            plan,
        "steps":           steps,
        "answer":          synth["answer"],
        "sources":         synth["sources"],
        "iterations":      len(steps),
        "page":            page,
        "has_more":        has_more,
        "matched_doc_ids": effective_doc_ids,
    }
