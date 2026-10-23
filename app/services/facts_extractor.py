"""
Document facts extractor — converts Docling markdown into structured JSONB.

One extractor per doc_type family. Each returns a dict that gets stored
in documents.extracted_facts. The dict shape is domain-specific but always
includes a top-level 'doc_type' key for routing.

Extraction is LLM-assisted (Mistral) with regex fallback for common patterns.
"""
import re
import json
import logging
from typing import Any, Dict, List, Optional

import ollama
from sqlalchemy import text

from app.core import config as app_config
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_model() -> str:
    return app_config.model


def _llm_extract(prompt: str, num_predict: int = 1200, upload: Optional[tuple] = None) -> Optional[Dict]:
    """Structured extraction prompt → parsed JSON. `upload=(filename, doc_type)` lets the
    organisation's cloud model do it when its policy allows that document type."""
    try:
        from app.core import llm
        resp = llm.chat(role="extraction", 
            model=_active_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_predict": num_predict, "temperature": 0.0},
            upload=upload,
        )
        raw = resp["message"]["content"].strip()
        raw = re.sub(r"```[a-z]*\s*", "", raw).strip("`").strip()
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
    except Exception as e:
        logger.warning(f"_llm_extract failed: {e}")
    return None


def _parse_amount(s: str) -> Optional[float]:
    """Parse '$1,234.56' or '(1234.56)' into float."""
    if not s:
        return None
    s = s.strip().replace(",", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()$€£¥ ")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _clean_entity_name(raw: str) -> str:
    """
    Normalize a raw merchant/vendor/provider/entity name across all domains.

    Strips bank ACH codes, OCR slash-artifacts, reference IDs, and trailing
    noise so that display names are human-readable across every domain:
      Banking:   "MY3TECH INC DES:PAYROLL ID:537099 INDN:NAMALA PPD" → "My3Tech Inc"
      Insurance: "N/ORTH AMERICAN L DES:INSURANCE ID:P60LB15091056"  → "North American Life"
      Medical:   "DR SMITH MD NPI:1234567 FAC:SUTTER"                → "Dr Smith MD"
      Legal:     "JONES & ASSOC LLC REF:INV-2024-0091"               → "Jones & Assoc LLC"
      HR/Payroll:"ELIASSEN GR/OUP L DES:PAYR/OLL ID:621075910851AF2"→ "Eliassen Group"
    """
    if not raw:
        return raw
    s = raw.strip()

    # 0. Strip leading slash (OCR artifact: "/online Banking" → "online Banking")
    s = re.sub(r'^/', '', s)

    # 1. Strip ACH/bank/medical field markers and everything after them
    s = re.split(
        r'\s+(?:DES:|INDN:|CO\s+ID:|TRACE:|NPI:|FAC:|Conf#|PPD|WEB|CCD|TEL|CTX)\b'
        r'|\s+ID:\w'           # "ID:123" — must be followed by word char to avoid "MY3TECH INC DES" false match
        r'|\s+REF:\w'
        r'|\s+#\d',            # " #000003908"
        s, maxsplit=1, flags=re.IGNORECASE
    )[0]

    # 2. Fix OCR forward-slash artifacts: "GR/OUP"→"GROUP", "Bank /OF"→"Bank OF"
    s = re.sub(r'([A-Za-z])/([A-Za-z])', r'\1\2', s)   # inline: "GR/OUP"
    s = re.sub(r'\s+/([A-Za-z])', r' \1', s)            # space+slash: " /OF" → " OF"

    # 3. Strip trailing lone uppercase letter (OCR truncated word): "Group L" → "Group"
    s = re.sub(r'\s+[A-Z]$', '', s)

    # 4. Strip trailing noise: phone numbers, dates, short numeric codes
    s = re.sub(r'\s+\d{7,}\S*$', '', s)          # long phone/ID numbers (7+ digits)
    s = re.sub(r'\s+\d{1,2}/\d{1,2}$', '', s)    # trailing dates "02/10"
    s = re.sub(r'\s+\d{2,4}$', '', s)             # trailing 2-4 digit codes

    # 5. Remove purely-numeric words (phone numbers, zip codes, IDs embedded in name)
    s = ' '.join(w for w in s.split() if not re.fullmatch(r'[\d\-\*\/\.]+', w))

    # 6. Cap at 4 words — real entity names are never longer
    words_pre = s.split()
    if len(words_pre) > 4:
        s = ' '.join(words_pre[:4])

    # 6. Collapse extra whitespace
    s = ' '.join(s.split())

    # 8. Title-case preserving known acronyms
    acronyms = {'ATM', 'LLC', 'INC', 'LTD', 'MD', 'HR', 'PO', 'ACH', 'EFT', 'CHK',
                'NPI', 'HMO', 'PPO', 'HSA', 'FSA', 'USA', 'US', 'CA', 'TX', 'NY',
                'ATM', 'PC', 'PA', 'DBA', 'CPA'}
    words = s.split()
    titled = []
    for w in words:
        up = w.upper()
        if up in acronyms or (2 <= len(w) <= 3 and w.isupper()):
            titled.append(up)
        else:
            titled.append(w[0].upper() + w[1:].lower() if w else w)
    s = ' '.join(titled)

    return s.strip() or raw.strip()


def _parse_date(s: str) -> Optional[str]:
    """Normalize common date formats to YYYY-MM-DD."""
    if not s:
        return None
    s = s.strip()
    # MM/DD/YYYY or MM/DD/YY
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        mo, dy, yr = m.groups()
        yr = f"20{yr}" if len(yr) == 2 else yr
        return f"{yr}-{int(mo):02d}-{int(dy):02d}"
    # YYYY-MM-DD already
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return s
    # Month DD, YYYY
    months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mn, dy, yr = m.groups()
        mo = months.get(mn[:3].lower())
        if mo:
            return f"{yr}-{mo:02d}-{int(dy):02d}"
    return None


# ---------------------------------------------------------------------------
# Extractors per doc_type family
# ---------------------------------------------------------------------------

_CC_TRANSACTION_PROMPT = """Extract ALL transactions from this credit card or bank statement markdown.

Return ONLY valid JSON, no explanation:
{{
  "statement_period": "<start date> to <end date>",
  "account_last4": "<last 4 digits or null>",
  "total_due": <number or null>,
  "minimum_due": <number or null>,
  "due_date": "<YYYY-MM-DD or null>",
  "transactions": [
    {{
      "date": "<MM/DD or MM/DD/YYYY>",
      "merchant": "<merchant name>",
      "amount": <positive number>,
      "type": "<debit or credit>",
      "category": "<category or null>"
    }}
  ]
}}

Document:
{text}
"""

_PAYROLL_PROMPT = """Extract earnings and deductions from this pay stub or payroll document.

Return ONLY valid JSON:
{{
  "employee_name": "<name or null>",
  "employer": "<company or null>",
  "pay_period": "<start> to <end>",
  "pay_date": "<YYYY-MM-DD or null>",
  "gross_pay": <number or null>,
  "net_pay": <number or null>,
  "earnings": [
    {{"type": "<e.g. base salary, overtime>", "hours": <number or null>, "amount": <number>}}
  ],
  "deductions": [
    {{"type": "<e.g. federal_tax, 401k, health>", "amount": <number>}}
  ],
  "ytd_gross": <number or null>,
  "ytd_net": <number or null>
}}

Document:
{text}
"""

_INVOICE_PROMPT = """Extract invoice details and line items from this invoice document.

Return ONLY valid JSON:
{{
  "invoice_number": "<string or null>",
  "vendor": "<vendor name or null>",
  "bill_to": "<customer name or null>",
  "invoice_date": "<YYYY-MM-DD or null>",
  "due_date": "<YYYY-MM-DD or null>",
  "po_number": "<string or null>",
  "line_items": [
    {{
      "description": "<item description>",
      "quantity": <number or null>,
      "unit_price": <number or null>,
      "total": <number>
    }}
  ],
  "subtotal": <number or null>,
  "tax": <number or null>,
  "total_amount": <number or null>
}}

Document:
{text}
"""

_ANNUAL_REPORT_PROMPT = """Extract key financial facts from this annual report or financial statement.

Return ONLY valid JSON:
{{
  "company": "<company name or null>",
  "fiscal_year": <year as integer or null>,
  "financials": {{
    "cash_and_equivalents": <number or null>,
    "total_revenue": <number or null>,
    "net_income": <number or null>,
    "net_loss": <number or null>,
    "total_assets": <number or null>,
    "total_debt": <number or null>,
    "operating_expenses": <number or null>
  }},
  "key_metrics": {{
    "cash_runway_months": <number or null>,
    "burn_rate_monthly": <number or null>,
    "employees": <number or null>
  }},
  "key_events": [
    {{"date": "<YYYY-MM or null>", "event": "<description>"}}
  ]
}}

Document:
{text}
"""

_CONTRACT_PROMPT = """Extract key terms and clauses from this contract or legal document.

Return ONLY valid JSON:
{{
  "parties": ["<party1>", "<party2>"],
  "contract_type": "<e.g. service agreement, NDA, lease>",
  "effective_date": "<YYYY-MM-DD or null>",
  "expiry_date": "<YYYY-MM-DD or null>",
  "value": <contract value as number or null>,
  "currency": "<USD or null>",
  "clauses": [
    {{"type": "<e.g. penalty, termination, confidentiality>", "description": "<brief description>"}}
  ],
  "notice_period_days": <number or null>,
  "governing_law": "<jurisdiction or null>"
}}

Document:
{text}
"""

_MEDICAL_PROMPT = """Extract key medical facts from this medical record or document.

Return ONLY valid JSON:
{{
  "patient_name": "<name or null>",
  "provider": "<doctor or facility or null>",
  "visit_date": "<YYYY-MM-DD or null>",
  "diagnoses": ["<diagnosis1>"],
  "prescriptions": [
    {{"drug": "<name>", "dosage": "<dosage>", "frequency": "<frequency>"}}
  ],
  "procedures": ["<procedure1>"],
  "follow_up_date": "<YYYY-MM-DD or null>",
  "notes": "<brief clinical notes or null>"
}}

Document:
{text}
"""

# Map doc_type → prompt template
_INSURANCE_PROMPT = """Extract insurance policy details from this document.

Return ONLY valid JSON:
{{
  "insurer": "<insurance company name or null>",
  "policy_number": "<string or null>",
  "policy_type": "<e.g. homeowners, auto, life, umbrella or null>",
  "named_insured": "<policyholder name or null>",
  "property_address": "<address or null>",
  "effective_date": "<YYYY-MM-DD or null>",
  "expiration_date": "<YYYY-MM-DD or null>",
  "premium": <annual premium number or null>,
  "deductible": <deductible number or null>,
  "coverage_limit": <dwelling/liability coverage limit number or null>,
  "coverages": [
    {{"name": "<coverage type>", "limit": <number or null>, "deductible": <number or null>}}
  ],
  "agent": "<agent name or null>",
  "agent_phone": "<phone or null>"
}}

Document:
{text}
"""

_W2_PROMPT = """Extract W-2 tax form details from this document.

Return ONLY valid JSON:
{{
  "employee_name": "<name or null>",
  "employee_ssn_last4": "<last 4 digits or null>",
  "employer": "<employer name or null>",
  "employer_ein": "<EIN or null>",
  "tax_year": <year number or null>,
  "wages_tips": <box 1 wages number or null>,
  "federal_income_tax_withheld": <box 2 number or null>,
  "social_security_wages": <box 3 number or null>,
  "social_security_tax_withheld": <box 4 number or null>,
  "medicare_wages": <box 5 number or null>,
  "medicare_tax_withheld": <box 6 number or null>,
  "state": "<state abbreviation or null>",
  "state_wages": <state wages number or null>,
  "state_income_tax": <state tax number or null>
}}

Document:
{text}
"""

_REAL_ESTATE_PROMPT = """Extract real estate or mortgage details from this document.

Return ONLY valid JSON:
{{
  "property_address": "<address or null>",
  "borrower_name": "<name or null>",
  "lender": "<lender name or null>",
  "loan_amount": <number or null>,
  "interest_rate": <percentage number or null>,
  "loan_type": "<e.g. 30-year fixed, ARM or null>",
  "monthly_payment": <number or null>,
  "closing_date": "<YYYY-MM-DD or null>",
  "property_value": <appraisal or purchase price or null>,
  "outstanding_balance": <remaining loan balance or null>,
  "escrow_amount": <monthly escrow or null>,
  "property_tax": <annual tax or null>
}}

Document:
{text}
"""

_COMPLIANCE_PROMPT = """Extract compliance and audit details from this document.

Return ONLY valid JSON:
{{
  "report_title": "<title or null>",
  "auditor": "<auditor or firm name or null>",
  "entity": "<organization being audited or null>",
  "audit_period": "<date range or null>",
  "audit_date": "<YYYY-MM-DD or null>",
  "overall_risk_rating": "<e.g. High/Medium/Low or null>",
  "findings_count": <number of findings or null>,
  "open_items": <number of open action items or null>,
  "critical_findings": ["<finding 1>", "<finding 2>"],
  "compliance_deadlines": ["<deadline 1>", "<deadline 2>"],
  "framework": "<e.g. SOC2, ISO 27001, HIPAA or null>"
}}

Document:
{text}
"""

_SUPPLY_CHAIN_PROMPT = """Extract supply chain and logistics details from this document.

Return ONLY valid JSON:
{{
  "document_type": "<e.g. invoice, purchase_order, bill_of_lading or null>",
  "vendor": "<vendor or supplier name or null>",
  "buyer": "<buyer or company name or null>",
  "po_number": "<PO number or null>",
  "invoice_number": "<invoice number or null>",
  "order_date": "<YYYY-MM-DD or null>",
  "delivery_date": "<YYYY-MM-DD or null>",
  "total_amount": <number or null>,
  "currency": "<USD, EUR, etc. or null>",
  "payment_terms": "<e.g. Net 30 or null>",
  "ship_to": "<address or null>",
  "tracking_number": "<tracking or null>",
  "line_items": [
    {{"description": "<item>", "quantity": <number or null>, "unit_price": <number or null>, "amount": <number or null>}}
  ]
}}

Document:
{text}
"""

_EOB_PROMPT = """Extract medical Explanation of Benefits (EOB) or medical record details.

Return ONLY valid JSON:
{{
  "patient_name": "<name or null>",
  "provider": "<doctor or facility name or null>",
  "insurer": "<insurance company or null>",
  "service_date": "<YYYY-MM-DD or null>",
  "diagnosis": "<diagnosis or null>",
  "procedure": "<procedure or service or null>",
  "billed_amount": <number or null>,
  "allowed_amount": <number or null>,
  "plan_paid": <number or null>,
  "patient_responsibility": <number or null>,
  "deductible_applied": <number or null>,
  "copay": <number or null>,
  "claim_number": "<string or null>",
  "medications": ["<medication 1>", "<medication 2>"]
}}

Document:
{text}
"""

_PROMPT_MAP: Dict[str, str] = {
    "credit_card_statement": _CC_TRANSACTION_PROMPT,
    "bank_statement":        _CC_TRANSACTION_PROMPT,
    "pay_stub":              _PAYROLL_PROMPT,
    "payroll_report":        _PAYROLL_PROMPT,
    "payroll":               _PAYROLL_PROMPT,
    "invoice":               _INVOICE_PROMPT,
    "purchase_order":        _INVOICE_PROMPT,
    "annual_report":         _ANNUAL_REPORT_PROMPT,
    "quarterly_report":      _ANNUAL_REPORT_PROMPT,
    "financial_statement":   _ANNUAL_REPORT_PROMPT,
    "contract":              _CONTRACT_PROMPT,
    "legal":                 _CONTRACT_PROMPT,
    "medical":               _MEDICAL_PROMPT,
    "medical_record":        _MEDICAL_PROMPT,
    # Insurance
    "insurance":             _INSURANCE_PROMPT,
    "insurance_policy":      _INSURANCE_PROMPT,
    "declarations_page":     _INSURANCE_PROMPT,
    "insurance_claim":       _INSURANCE_PROMPT,
    "insurance_certificate": _INSURANCE_PROMPT,
    # Tax / W2
    "w2":                    _W2_PROMPT,
    "tax_form":              _W2_PROMPT,
    # Legal (reuse _CONTRACT_PROMPT)
    "agreement":             _CONTRACT_PROMPT,
    "nda":                   _CONTRACT_PROMPT,
    "clm_contract":          _CONTRACT_PROMPT,
    "service_agreement":     _CONTRACT_PROMPT,
    "lease":                 _CONTRACT_PROMPT,
    # Medical
    "eob":                   _EOB_PROMPT,
    "lab_report":            _EOB_PROMPT,
    "prescription":          _EOB_PROMPT,
    "radiology_report":      _EOB_PROMPT,
    "discharge_summary":     _EOB_PROMPT,
    # Real estate
    "real_estate":           _REAL_ESTATE_PROMPT,
    "mortgage_document":     _REAL_ESTATE_PROMPT,
    "mortgage_statement":    _REAL_ESTATE_PROMPT,
    "closing_disclosure":    _REAL_ESTATE_PROMPT,
    "loan_document":         _REAL_ESTATE_PROMPT,
    "payoff_statement":      _REAL_ESTATE_PROMPT,
    "title_deed":            _REAL_ESTATE_PROMPT,
    "appraisal_report":      _REAL_ESTATE_PROMPT,
    # Compliance
    "audit_report":          _COMPLIANCE_PROMPT,
    "risk_assessment":       _COMPLIANCE_PROMPT,
    "compliance_report":     _COMPLIANCE_PROMPT,
    "regulatory_filing":     _COMPLIANCE_PROMPT,
    "soc_report":            _COMPLIANCE_PROMPT,
    "policy_document":       _COMPLIANCE_PROMPT,
    "terms_of_service":      _COMPLIANCE_PROMPT,
    "privacy_policy":        _COMPLIANCE_PROMPT,
    # Supply chain
    "bill_of_lading":        _SUPPLY_CHAIN_PROMPT,
    "customs_manifest":      _SUPPLY_CHAIN_PROMPT,
    "packing_list":          _SUPPLY_CHAIN_PROMPT,
    "delivery_note":         _SUPPLY_CHAIN_PROMPT,
    "vendor_agreement":      _SUPPLY_CHAIN_PROMPT,
    "inventory_report":      _SUPPLY_CHAIN_PROMPT,
    "order_history":         _SUPPLY_CHAIN_PROMPT,
    "asset_register":        _SUPPLY_CHAIN_PROMPT,
}


def _clean_unicode_escapes(text: str) -> str:
    """Replace Docling unicode escape sequences with their actual characters.
    Handles both 'uni002F' and '/uni002F' (where / is already the char).
    """
    # Remove the redundant leading char before uniXXXX when it matches the unicode value
    def replace(m):
        char = chr(int(m.group(2), 16))
        prefix = m.group(1) or ''
        # if prefix is the same char as the unicode value, drop the duplicate
        if prefix == char:
            return char
        return prefix + char
    return re.sub(r'([^a-zA-Z0-9]?)uni([0-9A-Fa-f]{4})', replace, text)


def _extract_transactions_regex(markdown_text: str, doc_type: str) -> Optional[Dict]:
    """
    Regex-based transaction extractor for credit card and bank statements.
    Parses pipe-delimited markdown tables produced by Docling.
    No LLM — deterministic, no hallucination.
    """
    # Clean unicode escapes from Docling OCR artifacts
    markdown_text = _clean_unicode_escapes(markdown_text)

    transactions = []

    # Pattern A: credit card — | date | post_date | description | ref | ref | amount | |
    pattern_cc = re.compile(
        r'\|\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*\|'
        r'\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*\|'
        r'\s*([^|]{3,80}?)\s*\|'
        r'\s*[^|]*\|'
        r'\s*[^|]*\|'
        r'\s*(-?\s*[\d,]+\.\d{2})\s*\|'
    )

    # Pattern B: bank statement — | date | description | amount |
    pattern_bank = re.compile(
        r'\|\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*\|'
        r'\s*([^|]{3,120}?)\s*\|'
        r'\s*(-?[\d,]+\.\d{2})\s*\|'
    )

    current_type = "debit"
    lines = markdown_text.splitlines()

    for line in lines:
        if any(h in line for h in ["Payments and Other Credits", "Deposits and other additions"]):
            current_type = "credit"
        elif any(h in line for h in ["Purchases and Adjustments", "Withdrawals and other subtractions"]):
            current_type = "debit"

        m = pattern_cc.search(line) or pattern_bank.search(line)
        if not m:
            continue

        raw_date, description, raw_amount = m.group(1), m.group(2).strip(), m.group(3).strip()

        # Skip header/total rows
        if any(skip in description.upper() for skip in ["TOTAL", "BALANCE", "MINIMUM", "PREVIOUS"]):
            continue

        date = _parse_date(raw_date)
        amount_str = raw_amount.replace(" ", "").replace(",", "")
        try:
            amount = abs(float(amount_str))
        except ValueError:
            continue

        transactions.append({
            "date": date or raw_date,
            "merchant": _clean_entity_name(description),
            "amount": amount,
            "type": current_type,
            "category": None,
        })

    if not transactions:
        return None

    # Extract statement period, due date, totals
    period_m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})\s*(?:to|-)\s*(\d{1,2}/\d{1,2}/\d{4})', markdown_text)
    due_m    = re.search(r'[Pp]ayment [Dd]ue [Dd]ate[:\s]+(\d{1,2}/\d{1,2}/\d{4})', markdown_text)
    total_m  = re.search(r'[Nn]ew [Bb]alance[:\s]+\$?([\d,]+\.\d{2})', markdown_text)

    # Infer statement year from period or any 4-digit year in the text
    stmt_year = None
    if period_m:
        stmt_year = period_m.group(2).split("/")[-1]  # end date year
    else:
        year_m = re.search(r'\b(20\d{2})\b', markdown_text)
        if year_m:
            stmt_year = year_m.group(1)

    # Backfill year on MM/DD dates
    if stmt_year:
        for t in transactions:
            d = t["date"]
            if d and re.match(r'^\d{1,2}/\d{1,2}$', d):
                t["date"] = _parse_date(f"{d}/{stmt_year}") or d

    return {
        "doc_type": doc_type,
        "statement_period": f"{period_m.group(1)} to {period_m.group(2)}" if period_m else None,
        "due_date": _parse_date(due_m.group(1)) if due_m else None,
        "total_due": float(total_m.group(1).replace(",", "")) if total_m else None,
        "transactions": transactions,
    }


def _split_pages(text: str, page_size: int = 4000) -> List[str]:
    """Split markdown by page markers or fixed size."""
    # Docling emits page markers like "<!-- Page 2 -->" or "## Page 2"
    page_markers = re.split(r'(?:<!--\s*[Pp]age\s*\d+\s*-->|^#{1,2}\s*[Pp]age\s*\d+)', text, flags=re.MULTILINE)
    if len(page_markers) > 1:
        return [p.strip() for p in page_markers if p.strip()]
    # fallback — fixed size windows with overlap
    pages = []
    start = 0
    while start < len(text):
        pages.append(text[start:start + page_size])
        start += page_size - 200
    return pages


def _merge_transaction_facts(pages_facts: List[Dict]) -> Dict:
    """Merge facts extracted from multiple pages into one dict."""
    merged: Dict[str, Any] = {}
    all_transactions: List[Dict] = []

    for facts in pages_facts:
        if not merged:
            merged = {k: v for k, v in facts.items() if k != "transactions"}
        # take non-null values from later pages if earlier pages had nulls
        for k, v in facts.items():
            if k == "transactions":
                continue
            if v is not None and merged.get(k) is None:
                merged[k] = v
        all_transactions.extend(facts.get("transactions") or [])

    # deduplicate transactions by (date, merchant, amount)
    seen = set()
    unique_txns = []
    for t in all_transactions:
        key = (t.get("date"), t.get("merchant"), t.get("amount"))
        if key not in seen and t.get("amount") is not None:
            seen.add(key)
            unique_txns.append(t)

    merged["transactions"] = unique_txns
    return merged


def extract_facts(doc_type: str, markdown_text: str, filename: Optional[str] = None) -> Optional[Dict]:
    """
    Extract structured facts from document markdown.
    For transaction documents, processes each page separately and merges
    to avoid truncating transaction lists.
    Returns a dict ready to store in extracted_facts JSONB.
    """
    prompt_template = _PROMPT_MAP.get(doc_type)
    if not prompt_template:
        logger.info(f"No extractor for doc_type={doc_type} — skipping facts extraction")
        return None

    is_transaction_doc = doc_type in ("credit_card_statement", "bank_statement")

    if is_transaction_doc:
        facts = _extract_transactions_regex(markdown_text, doc_type)
        if facts:
            facts = _clean_entity_fields(facts)
            n = len(facts.get("transactions") or [])
            logger.info(f"✅ Regex extracted {n} transactions for doc_type={doc_type}")
            return facts
        logger.warning(f"⚠ Regex extraction found no transactions for doc_type={doc_type}")

    # Non-transaction docs — single pass on first 6000 chars
    text_snippet = markdown_text[:6000]
    prompt = prompt_template.replace("{text}", text_snippet)
    facts = _llm_extract(prompt, num_predict=1200, upload=(filename or "document", doc_type))
    if facts:
        facts["doc_type"] = doc_type
        facts = _clean_entity_fields(facts)
        logger.info(f"✅ Extracted facts for doc_type={doc_type} — keys: {list(facts.keys())}")
    else:
        logger.warning(f"⚠ Facts extraction returned None for doc_type={doc_type}")
    return facts


def _clean_entity_fields(facts: Dict) -> Dict:
    """
    Walk all extracted facts and clean any entity-name field across every domain.
    Works generically: cleans any string field whose key suggests it's a name.
    """
    _NAME_KEYS = {
        "merchant", "vendor", "provider", "employer", "insurer", "carrier",
        "payee", "payer", "company", "organization", "facility", "hospital",
        "doctor", "physician", "attorney", "firm", "landlord", "tenant",
        "supplier", "contractor", "client", "counterparty",
    }
    if not isinstance(facts, dict):
        return facts
    cleaned = {}
    for k, v in facts.items():
        if k in _NAME_KEYS and isinstance(v, str):
            cleaned[k] = _clean_entity_name(v)
        elif k == "transactions" and isinstance(v, list):
            cleaned[k] = [
                {**tx, "merchant": _clean_entity_name(tx["merchant"])}
                if isinstance(tx, dict) and "merchant" in tx else tx
                for tx in v
            ]
        elif isinstance(v, dict):
            cleaned[k] = _clean_entity_fields(v)
        elif isinstance(v, list) and all(isinstance(i, dict) for i in v):
            cleaned[k] = [_clean_entity_fields(i) for i in v]
        else:
            cleaned[k] = v
    return cleaned


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

# Model output uses text for "no value" and for numbers; JSON queries need real nulls and numbers.
# Same rules as udv_clean_facts() in migration V59 (which backfills existing rows).
_FAKE_NULLS = {"null", "none", "n/a", "na", "", "unknown", "not found", "not available", "not provided", "-", "—"}
_MONEY_KEY = re.compile(r"amount|total|balance|wage|tax|pay(?!_?(?:date|period|frequency|ee|er))|price|value|income|"
                        r"withh|fee|cost|charge|gross|net|proceeds|basis|interest|dividend|compensation|ytd|escrow|"
                        r"principal|deduction|premium|rent|salary|bonus|refund|due|credit|debit|limit", re.I)
_ID_KEY = re.compile(r"number|_id$|^id$|ssn|ein|tin|zip|postal|account|routing|phone|last4|code|policy|receipt|"
                     r"invoice_no|reference|year|date|period|name|address|percent|rate", re.I)
_NUMERIC = re.compile(r"^\(?-?\$?\s*-?[\d,]+(\.\d+)?\)?$")


def normalize_facts(value, key: str = ""):
    """Fake-null strings → None at any depth; numeric text → number only for money keys
    (identifiers such as invoice numbers, SSN last-4 and ZIPs keep their leading zeros)."""
    if isinstance(value, dict):
        return {k: normalize_facts(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_facts(v, key) for v in value]
    if isinstance(value, str):
        v = value.strip()
        if v.lower() in _FAKE_NULLS:
            return None
        if key == "tax_year" and re.fullmatch(r"\d{4}", v):
            return int(v)
        if key and _MONEY_KEY.search(key) and not _ID_KEY.search(key) and _NUMERIC.match(v):
            neg = v.startswith("(") or "-" in v
            try:
                n = float(re.sub(r"[^\d.]", "", v))
                return -n if neg else n
            except ValueError:
                return v
        return v
    return value


def store_facts(document_id: str, facts: Dict) -> bool:
    """Store extracted facts into documents.extracted_facts JSONB column."""
    facts = normalize_facts(facts)
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE documents SET extracted_facts = CAST(:facts AS jsonb) WHERE id = :doc_id"),
            {"facts": json.dumps(facts), "doc_id": document_id}
        )
        db.commit()
        logger.info(f"✅ Stored extracted_facts for document {document_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to store facts for {document_id}: {e}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def extract_and_store(document_id: str, doc_type: str, markdown_text: str, filename: Optional[str] = None) -> bool:
    """Full pipeline: extract facts then persist. Returns True on success."""
    facts = extract_facts(doc_type, markdown_text, filename)
    if not facts:
        return False
    return store_facts(document_id, facts)
