# app/services/classifiers/spreadsheet_type_classifier.py

import logging
from typing import Dict, Any, List, Optional
import pandas as pd

from app.core import config

logger = logging.getLogger(__name__)


class SpreadsheetClassifier:
    """
    Determines WHAT TYPE of spreadsheet (business docs vs datasets)
    Runs AFTER we know it's a spreadsheet
    """

    SPREADSHEET_TYPES = [
        # ==================== BUSINESS DOCUMENTS ====================
        "sales_report",
        "invoice",
        "purchase_order",
        "bank_statement",
        "payroll",
        "expense_report",
        "financial_statement",
        "credit_memo",
        "receipt",
        "packing_slip",

        # ==================== OPERATIONAL DATA ====================
        "inventory",
        "customer_list",
        "product_catalog",
        "employee_roster",
        "order_history",
        "transaction_log",
        "contact_list",
        "asset_register",

        # ==================== REFERENCE DATA / DATASETS ====================
        "movie_dataset",  # Movies, TV shows, ratings, reviews
        "music_dataset",  # Songs, albums, artists, streams
        "book_dataset",  # Books, authors, ratings, reviews
        "product_dataset",  # Product listings, specs, reviews
        "ratings_dataset",  # General ratings, reviews, scores
        "location_dataset",  # Cities, countries, coordinates, demographics
        "weather_dataset",  # Weather data, temperatures, forecasts
        "sports_dataset",  # Games, scores, statistics, teams
        "health_dataset",  # Medical data, fitness, nutrition
        "education_dataset",  # Schools, courses, grades, students
        "real_estate_dataset",  # Properties, prices, locations
        "stock_dataset",  # Stock prices, trading data
        "census_dataset",  # Population, demographics, statistics
        "scientific_dataset",  # Research data, measurements, experiments
        "survey_dataset",  # Survey responses, questionnaires
        "event_dataset",  # Events, schedules, attendees
        "generic_dataset",  # General reference/research data

        # ==================== FALLBACK ====================
        "data_table",  # Generic structured data
        "dataset",
        "datasets",
    ]

    def __init__(self):
        """Initialize with Ollama"""
        try:
            import ollama
            import os

            host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            self.client = ollama.Client(host=host)
            self.model = os.getenv("OLLAMA_MODEL", config.model)

            logger.info(f"✅ Spreadsheet type classifier initialized")
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            self.client = None

    def classify(self, file_path: str, parsed: Dict[str, Any] = None) -> Dict[str, Any]:
        """Classify spreadsheet type"""

        try:
            # Load data
            df = self._load_data(file_path)
            if df is None or df.empty:
                return self._fallback_classify(file_path, parsed)

            logger.info(f"📊 Analyzing spreadsheet: {len(df)} rows × {len(df.columns)} cols")

            # Build context
            context = self._build_context(df, file_path, parsed)

            # Use LLM if available
            if self.client:
                result = self._ask_llm(context)
            else:
                result = self._heuristic_classify(df, file_path, parsed)

            logger.info(f"✅ Spreadsheet type: {result['type']} ({result['confidence']:.2f})")
            return result

        except Exception as e:
            logger.error(f"❌ Classification failed: {e}", exc_info=True)
            return self._fallback_classify(file_path, parsed)

    def _load_data(self, file_path: str) -> Optional[pd.DataFrame]:
        """Load spreadsheet data (first 100 rows for speed)"""
        try:
            if file_path.endswith('.csv'):
                return pd.read_csv(file_path, nrows=100)
            elif file_path.endswith(('.xlsx', '.xls')):
                return pd.read_excel(file_path, nrows=100)
            elif file_path.endswith('.tsv'):
                return pd.read_csv(file_path, sep='\t', nrows=100)
            return None
        except Exception as e:
            logger.error(f"Load failed: {e}")
            return None

    def _build_context(self, df: pd.DataFrame, file_path: str, parsed: Dict) -> str:
        """Build context for classification"""
        parts = []

        # Filename
        filename = file_path.split('/')[-1] if '/' in file_path else file_path
        parts.append(f"Filename: {filename}")

        # Dimensions
        parts.append(f"Size: {len(df)} rows × {len(df.columns)} columns")

        # Column names
        cols = ", ".join(str(c) for c in df.columns[:20])
        parts.append(f"Columns: {cols}")

        # Sample data (first 5 rows)
        parts.append("\nSample Data:")
        for i in range(min(5, len(df))):
            row = df.iloc[i]
            row_str = " | ".join(str(v)[:50] for v in row[:10])
            parts.append(f"Row {i + 1}: {row_str}")

        # Data types summary
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        text_cols = df.select_dtypes(include=['object']).columns.tolist()
        date_cols = df.select_dtypes(include=['datetime']).columns.tolist()

        if numeric_cols:
            parts.append(f"\nNumeric columns: {', '.join(numeric_cols[:10])}")
        if text_cols:
            parts.append(f"Text columns: {', '.join(text_cols[:10])}")
        if date_cols:
            parts.append(f"Date columns: {', '.join(date_cols[:10])}")

        return "\n".join(parts)

    def _ask_llm(self, context: str) -> Dict[str, Any]:
        """Ask LLM to classify spreadsheet type"""

        prompt = f"""You are analyzing a spreadsheet to determine its type and purpose.

Classify it into ONE of these categories:

BUSINESS DOCUMENTS (transactional/financial):
- sales_report: Sales data, revenue by product/region/customer
- invoice: Billing, line items, amounts due
- purchase_order: Orders to vendors
- bank_statement: Banking transactions, balances
- payroll: Employee wages, hours, deductions
- expense_report: Business expenses, reimbursements
- financial_statement: Balance sheet, P&L

OPERATIONAL DATA (internal business):
- inventory: Stock levels, products, warehouses
- customer_list: Customer contacts, CRM data
- product_catalog: Product listings, prices, SKUs
- employee_roster: Employee directory, departments
- order_history: Order records, fulfillment
- transaction_log: System transactions, logs

DATASETS (reference/research/external):
- movie_dataset: Movies, TV, ratings, reviews (IMDB, Netflix, etc.)
- music_dataset: Songs, albums, artists, streams
- book_dataset: Books, authors, publications
- ratings_dataset: General ratings/reviews
- location_dataset: Cities, countries, geography
- weather_dataset: Weather, temperatures, forecasts
- sports_dataset: Games, scores, teams, players
- health_dataset: Medical, fitness, nutrition data
- stock_dataset: Stock prices, trading data
- census_dataset: Population, demographics
- scientific_dataset: Research, experiments, measurements
- generic_dataset: Other reference/research data

FALLBACK:
- data_table: Generic structured data (use only if nothing else fits)

Spreadsheet information:
{context}

Respond with ONLY JSON (no markdown, no explanation):
{{
    "type": "the_type",
    "confidence": 0.95,
    "reasoning": "Brief explanation"
}}

JSON:"""

        response = self.client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 200}
        )

        text = response['message']['content'].strip()

        # Extract JSON
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]

        import json
        result = json.loads(text)

        # Validate type
        doc_type = result.get("type", "data_table").lower()
        if doc_type not in self.SPREADSHEET_TYPES:
            logger.warning(f"Unknown type: {doc_type}, using data_table")
            doc_type = "data_table"

        return {
            "type": doc_type,
            "confidence": float(result.get("confidence", 0.8)),
            "reasoning": result.get("reasoning", "LLM classification")
        }

    def _heuristic_classify(self, df: pd.DataFrame, file_path: str, parsed: Dict) -> Dict[str, Any]:
        """Heuristic classification when LLM unavailable"""

        filename = file_path.lower()
        cols_str = " ".join(str(c).lower() for c in df.columns)

        # ==================== BUSINESS DOCUMENTS ====================
        # Sales report
        if (any(kw in cols_str for kw in ['sales', 'revenue', 'amount']) and
                any(kw in cols_str for kw in ['product', 'region', 'customer', 'territory'])):
            return {"type": "sales_report", "confidence": 0.85, "reasoning": "sales_columns"}

        # Invoice
        if any(kw in cols_str for kw in ['invoice', 'bill to', 'amount due', 'line item']):
            return {"type": "invoice", "confidence": 0.85, "reasoning": "invoice_columns"}

        # Payroll
        if any(kw in cols_str for kw in ['salary', 'wage', 'gross pay', 'net pay', 'deduction']):
            return {"type": "payroll", "confidence": 0.85, "reasoning": "payroll_columns"}

        # Bank statement
        if any(kw in cols_str for kw in ['transaction', 'balance', 'deposit', 'withdrawal', 'account']):
            return {"type": "bank_statement", "confidence": 0.85, "reasoning": "banking_columns"}

        # ==================== DATASETS ====================
        # Movie dataset
        if any(kw in cols_str for kw in ['title', 'movie', 'film', 'imdb', 'rating', 'genre']):
            return {"type": "movie_dataset", "confidence": 0.80, "reasoning": "movie_columns"}

        # Music dataset
        if any(kw in cols_str for kw in ['song', 'artist', 'album', 'track', 'spotify', 'stream']):
            return {"type": "music_dataset", "confidence": 0.80, "reasoning": "music_columns"}

        # Location dataset
        if any(kw in cols_str for kw in ['city', 'country', 'latitude', 'longitude', 'population', 'zip']):
            return {"type": "location_dataset", "confidence": 0.80, "reasoning": "location_columns"}

        # Sports dataset
        if any(kw in cols_str for kw in ['team', 'score', 'game', 'player', 'match', 'season']):
            return {"type": "sports_dataset", "confidence": 0.80, "reasoning": "sports_columns"}

        # Stock dataset
        if any(kw in cols_str for kw in ['stock', 'ticker', 'price', 'volume', 'open', 'close', 'high', 'low']):
            return {"type": "stock_dataset", "confidence": 0.80, "reasoning": "stock_columns"}

        # Generic ratings
        if any(kw in cols_str for kw in ['rating', 'score', 'review', 'votes']):
            return {"type": "ratings_dataset", "confidence": 0.70, "reasoning": "ratings_columns"}

        # ==================== FILENAME HINTS ====================
        if "sales" in filename or "revenue" in filename:
            return {"type": "sales_report", "confidence": 0.65, "reasoning": "filename"}
        if "invoice" in filename:
            return {"type": "invoice", "confidence": 0.65, "reasoning": "filename"}
        if "imdb" in filename or "movie" in filename:
            return {"type": "movie_dataset", "confidence": 0.65, "reasoning": "filename"}
        if "stock" in filename or "ticker" in filename:
            return {"type": "stock_dataset", "confidence": 0.65, "reasoning": "filename"}

        return {"type": "data_table", "confidence": 0.40, "reasoning": "no_match"}

    def _fallback_classify(self, file_path: str, parsed: Dict) -> Dict[str, Any]:
        """Fallback when analysis fails"""
        return {"type": "data_table", "confidence": 0.30, "reasoning": "fallback"}


# # app/services/classifiers/spreadsheet_classifier.py
#
# import re
# from typing import Dict, Any, List, Tuple
# import numpy as np
# import joblib
# import logging
#
# logger = logging.getLogger(__name__)
#
# # Try to load an ML model and vectorizer if present
# try:
#     ML_MODEL = joblib.load("models/spreadsheet_model.joblib")
#     VECTORIZER = joblib.load("models/spread_vectorizer.joblib")
#     logger.info("Loaded spreadsheet ML model and vectorizer")
# except Exception:
#     ML_MODEL = None
#     VECTORIZER = None
#     logger.info("No spreadsheet ML model found; running rule-only mode")
#
# # Keyword dictionaries and regexes for common spreadsheet doc types
# KEYWORDS = {
#     # ==================== FINANCIAL DOCUMENTS ====================
#     "invoice": [
#         "invoice number", "invoice date", "invoice total",
#         "bill to", "ship to", "sold to",
#         "amount due", "due date", "payment due",
#         "payment terms", "net 30", "net 60",
#         "remit to", "remittance",
#         "subtotal", "tax total", "invoice amount",
#         "itemized charges", "line items"
#     ],
#
#     "purchase_order": [
#         "purchase order", "po number", "po date",
#         "order number", "order date",
#         "vendor", "supplier", "ship to address",
#         "delivery date", "expected delivery",
#         "order total", "order quantity",
#         "requisition number", "buyer name"
#     ],
#
#     "credit_memo": [
#         "credit memo", "credit note", "credit amount",
#         "memo number", "memo date",
#         "refund", "refund amount",
#         "return authorization", "rma number",
#         "reason for credit", "original invoice"
#     ],
#
#     "bank_statement": [
#         "bank statement", "account statement", "monthly statement", "quarterly statement",
#         "account number", "account summary", "account balance", "account activity",
#         "routing number", "swift code", "iban",
#         "opening balance", "closing balance", "beginning balance", "ending balance",
#         "available balance", "current balance", "previous balance", "ledger balance",
#         "statement period", "statement date", "statement cycle", "closing date",
#         "deposits and", "withdrawals and", "debits and credits",
#         "overdraft fee", "nsf fee", "service fee", "maintenance fee", "monthly fee",
#         "returned item", "insufficient funds", "non-sufficient funds",
#         "total for this period", "year-to-date", "ytd balance", "ytd total",
#         "transaction history", "account activity", "transaction detail",
#         "checking account", "savings account", "money market account",
#         "direct deposit", "wire transfer", "ach transfer", "electronic transfer",
#         "atm withdrawal", "debit card", "check number",
#         "interest earned", "interest paid", "dividend",
#         "customer service:", "en español:", "for assistance",
#         "bank of america", "chase bank", "wells fargo", "citibank", "us bank",
#         "daily balance", "average balance", "minimum balance"
#     ],
#
#     "receipt": [
#         "receipt", "sales receipt", "customer receipt",
#         "transaction id", "transaction number", "confirmation number",
#         "total paid", "amount paid", "payment received",
#         "payment method", "paid by", "tender type",
#         "thank you for your purchase", "thank you for shopping",
#         "card ending", "card number", "last 4 digits",
#         "cash", "credit card", "debit card",
#         "change due", "change",
#         "store number", "register number", "cashier", "clerk",
#         "return policy", "exchange policy"
#     ],
#
#     "payroll": [
#         "payroll", "pay stub", "pay slip", "earnings statement", "wage statement",
#         "employee id", "employee number", "employee name",
#         "pay period", "pay date", "check date", "payment date",
#         "net pay", "gross pay", "total earnings",
#         "regular hours", "overtime hours", "hours worked",
#         "hourly rate", "salary", "wage rate",
#         "federal tax", "state tax", "local tax",
#         "fica", "social security", "medicare",
#         "tax withheld", "withholding", "deductions",
#         "401k", "retirement", "pension",
#         "health insurance", "dental insurance", "vision insurance",
#         "year-to-date", "ytd earnings", "ytd taxes",
#         "vacation hours", "sick hours", "pto"
#     ],
#
#     "expense_report": [
#         "expense report", "expense claim", "expense reimbursement",
#         "employee expense", "business expense", "travel expense",
#         "expense date", "date of expense",
#         "expense category", "expense type", "expense description",
#         "receipt attached", "supporting documentation",
#         "reimbursement", "reimbursement amount", "amount to reimburse",
#         "approved by", "manager approval", "approver",
#         "total expenses", "expense total",
#         "mileage", "per diem", "lodging", "accommodation",
#         "meals", "entertainment", "transportation",
#         "airfare", "hotel", "taxi", "uber", "rental car"
#     ],
#
#     "sales_report": [
#         "sales report", "sales summary", "sales analysis", "sales data", "sales metrics",
#         "revenue report", "revenue summary", "revenue breakdown", "revenue analysis",
#         "gross sales", "net sales", "total sales revenue",
#         "sales by region", "sales by product", "sales by category", "sales by territory",
#         "regional sales", "territory sales", "store performance", "branch sales",
#         "product mix", "product line", "sku analysis", "item performance",
#         "units sold", "quantity sold", "items sold", "volume sold",
#         "sales performance", "sales target", "sales forecast", "sales goal",
#         "top selling", "best seller", "top performer", "best performing",
#         "market share", "sales growth", "sales trend", "growth rate",
#         "sales channel", "distribution channel",
#         "quarterly sales", "monthly sales", "annual sales", "ytd sales",
#         "sales comparison", "year over year", "yoy growth"
#     ],
#
#     "financial_statement": [
#         "balance sheet", "statement of financial position",
#         "income statement", "profit and loss", "p&l statement", "statement of operations",
#         "cash flow statement", "statement of cash flows",
#         "assets", "total assets", "current assets", "fixed assets",
#         "liabilities", "total liabilities", "current liabilities", "long-term liabilities",
#         "shareholders equity", "stockholders equity", "retained earnings",
#         "revenue", "net revenue", "total revenue", "gross revenue",
#         "cost of goods sold", "cogs", "operating expenses",
#         "net income", "net profit", "net loss", "bottom line",
#         "ebitda", "operating income", "gross profit", "gross margin",
#         "accounts receivable", "accounts payable", "inventory",
#         "fiscal year", "fiscal quarter", "fy2024", "q1", "q2", "q3", "q4"
#     ],
#
#     "tax_form": [
#         "tax return", "tax form", "tax document",
#         "form w-2", "form w-4", "form w-9", "form 1099", "form 1040",
#         "schedule a", "schedule b", "schedule c", "schedule d",
#         "internal revenue service", "irs", "department of treasury",
#         "taxable income", "adjusted gross income", "agi",
#         "federal tax", "state tax", "tax withheld",
#         "tax refund", "amount owed", "balance due",
#         "filing status", "single", "married filing jointly", "head of household",
#         "exemptions", "dependents", "standard deduction", "itemized deduction",
#         "ein", "employer identification number",
#         "ssn", "social security number", "taxpayer id"
#     ],
#
#     # ==================== LEGAL & CONTRACTS ====================
#     "contract": [
#         "agreement", "contract", "this agreement",
#         "terms and conditions", "terms of service",
#         "party of the first part", "party of the second part",
#         "whereas", "witnesseth", "herein", "hereinafter", "hereby",
#         "effective date", "commencement date", "term of agreement",
#         "termination", "termination clause", "early termination",
#         "covenants", "obligations", "representations", "warranties",
#         "indemnification", "indemnify", "hold harmless",
#         "liability", "limitation of liability", "damages",
#         "governing law", "jurisdiction", "venue",
#         "arbitration", "dispute resolution", "mediation",
#         "entire agreement", "severability", "waiver",
#         "executed on", "signed on", "in witness whereof",
#         "signatures", "authorized signatory"
#     ],
#
#     "nda": [
#         "non-disclosure agreement", "nda", "confidentiality agreement",
#         "confidential information", "proprietary information", "trade secrets",
#         "receiving party", "disclosing party",
#         "permitted use", "permitted disclosure",
#         "non-disclosure", "confidentiality obligation",
#         "return of materials", "destruction of information",
#         "term of confidentiality", "confidentiality period"
#     ],
#
#     "lease_agreement": [
#         "lease agreement", "rental agreement", "tenancy agreement",
#         "landlord", "tenant", "lessee", "lessor",
#         "leased premises", "rental property", "unit number",
#         "monthly rent", "rent amount", "lease term",
#         "security deposit", "damage deposit", "last month rent",
#         "move-in date", "lease commencement", "lease expiration",
#         "utilities included", "pet policy", "parking",
#         "maintenance responsibilities", "repairs",
#         "lease renewal", "rent increase", "notice period"
#     ],
#
#     # ==================== HR DOCUMENTS ====================
#     "resume": [
#         "resume", "curriculum vitae", "cv",
#         "professional summary", "career objective", "personal statement",
#         "work experience", "employment history", "professional experience", "work history",
#         "education", "educational background", "academic qualifications",
#         "bachelor degree", "master degree", "phd", "doctorate", "diploma",
#         "skills", "technical skills", "core competencies", "proficiencies",
#         "certifications", "professional certifications", "licenses",
#         "achievements", "accomplishments", "awards",
#         "references available", "references upon request"
#     ],
#
#     "cover_letter": [
#         "cover letter", "letter of interest", "application letter",
#         "applying for", "position of interest", "job opening",
#         "i am writing to", "i am interested in",
#         "relevant experience", "qualifications", "skills and experience",
#         "why i am a good fit", "ideal candidate",
#         "available for interview", "look forward to hearing",
#         "attached resume", "please find my resume"
#     ],
#
#     "offer_letter": [
#         "offer letter", "employment offer", "job offer", "offer of employment",
#         "pleased to offer", "we are pleased to offer you",
#         "position", "title", "job title", "role",
#         "start date", "commencement date", "begin employment",
#         "annual salary", "base salary", "compensation package",
#         "benefits package", "health insurance", "paid time off",
#         "contingent upon", "subject to", "background check", "drug test",
#         "at-will employment", "employment at will",
#         "probationary period", "probation",
#         "please sign", "accept this offer", "offer acceptance",
#         "offer expires", "respond by"
#     ],
#
#     "employment_agreement": [
#         "employment agreement", "employment contract", "employee contract",
#         "employer", "employee", "employer-employee relationship",
#         "job duties", "responsibilities", "scope of work",
#         "work schedule", "working hours", "full-time", "part-time",
#         "compensation", "base pay", "bonus", "commission",
#         "benefits", "vacation", "sick leave", "holidays",
#         "confidentiality", "non-compete", "non-solicitation",
#         "intellectual property", "work product", "inventions",
#         "termination", "notice period", "severance"
#     ],
#
#     "performance_review": [
#         "performance review", "performance appraisal", "performance evaluation",
#         "employee evaluation", "annual review", "mid-year review",
#         "review period", "evaluation period",
#         "goals and objectives", "key performance indicators", "kpis",
#         "strengths", "areas for improvement", "development areas",
#         "meets expectations", "exceeds expectations", "needs improvement",
#         "rating", "performance rating", "overall score",
#         "manager comments", "employee comments", "feedback",
#         "development plan", "action items", "improvement plan"
#     ],
#
#     # ==================== CORRESPONDENCE ====================
#     "letter": [
#         "dear sir", "dear madam", "dear mr", "dear mrs", "dear ms", "dear dr",
#         "to whom it may concern",
#         "sincerely", "sincerely yours", "yours sincerely",
#         "regards", "best regards", "kind regards", "warm regards",
#         "respectfully", "yours faithfully", "yours truly",
#         "cc:", "bcc:", "re:", "subject:",
#         "enclosed please find", "attached herewith",
#         "thank you for your attention", "looking forward to"
#     ],
#
#     "memo": [
#         "memorandum", "internal memo", "company memo",
#         "to:", "from:", "date:", "re:", "subject:",
#         "internal communication", "internal announcement",
#         "please be advised", "for your information", "fyi",
#         "effective immediately", "please note",
#         "all staff", "all employees", "team members"
#     ],
#
#     "email": [
#         "from:", "to:", "cc:", "bcc:", "subject:",
#         "sent:", "date sent:", "received:",
#         "reply to:", "forward:",
#         "dear", "hi", "hello", "greetings",
#         "thanks", "thank you", "regards",
#         "attached", "attachment", "please find attached",
#         "meeting invite", "calendar invite"
#     ],
#
#     # ==================== SHIPPING & LOGISTICS ====================
#     "packing_slip": [
#         "packing slip", "packing list", "shipping manifest", "pick list",
#         "ship to address", "ship from address", "shipping address",
#         "tracking number", "tracking id", "tracking code",
#         "waybill", "airway bill", "shipping label",
#         "carrier", "shipper", "shipping method", "shipping service",
#         "shipped via", "freight carrier",
#         "package number", "box number", "carton", "pallet",
#         "weight", "shipping weight", "package weight",
#         "dimensions", "package dimensions",
#         "quantity shipped", "items shipped", "units shipped",
#         "delivery date", "estimated delivery", "expected arrival", "ship date",
#         "order number", "sales order", "customer order"  # Added context
#     ],
#
#     "delivery_note": [
#         "delivery note", "delivery receipt", "delivery confirmation",
#         "proof of delivery", "pod",
#         "delivered to", "received by", "recipient name",
#         "signature", "signed by", "delivery signature",
#         "delivered on", "delivery date", "delivery time",
#         "delivery address", "delivery location"
#     ],
#
#     "bill_of_lading": [
#         "bill of lading", "bol", "shipping document",
#         "consignor", "consignee", "notify party",
#         "port of loading", "port of discharge",
#         "vessel", "voyage number", "container number",
#         "freight terms", "freight prepaid", "freight collect",
#         "clean on board", "shipped on board"
#     ],
#
#     # ==================== INSURANCE ====================
#     "insurance_policy": [
#         "insurance policy", "policy document", "coverage document",
#         "policy number", "policy holder", "insured",
#         "beneficiary", "primary beneficiary", "contingent beneficiary",
#         "coverage", "coverage amount", "policy limits",
#         "premium", "monthly premium", "annual premium",
#         "effective date", "expiration date", "renewal date",
#         "deductible", "copay", "coinsurance",
#         "exclusions", "limitations", "covered services",
#         "insurance company", "insurer", "underwriter"
#     ],
#
#     "insurance_claim": [
#         "insurance claim", "claim form", "claim document",
#         "claim number", "claim date", "date of loss",
#         "claimant", "policyholder",
#         "incident description", "loss description", "cause of loss",
#         "claim amount", "amount claimed", "loss amount",
#         "supporting documents", "documentation required",
#         "claim status", "claim approved", "claim denied",
#         "settlement", "settlement amount", "claim payment",
#         "adjuster", "claims adjuster", "claim representative"
#     ],
#
#     # ==================== MEDICAL ====================
#     "medical_record": [
#         "medical record", "health record", "patient record",
#         "patient name", "patient id", "medical record number", "mrn",
#         "date of birth", "dob", "age", "gender",
#         "diagnosis", "diagnosis code", "icd code",
#         "treatment", "treatment plan", "procedure",
#         "medication", "prescription", "dosage",
#         "vital signs", "blood pressure", "temperature", "pulse", "heart rate",
#         "allergies", "medical allergies", "drug allergies",
#         "medical history", "past medical history", "family history",
#         "doctor", "physician", "provider", "attending physician",
#         "hospital", "clinic", "medical facility"
#     ],
#
#     "prescription": [
#         "prescription", "rx", "prescription order",
#         "prescriber", "physician", "doctor",
#         "patient name", "date of birth",
#         "medication", "drug name", "medication name",
#         "dosage", "strength", "quantity",
#         "directions", "instructions", "sig",
#         "refills", "number of refills",
#         "pharmacy", "dispense as written", "generic substitution",
#         "dea number", "npi number"
#     ],
#
#     "lab_results": [
#         "lab results", "laboratory results", "test results",
#         "specimen", "specimen type", "collection date",
#         "test name", "test code", "lab test",
#         "result", "value", "reference range", "normal range",
#         "abnormal", "out of range", "critical value",
#         "units", "measurement units",
#         "ordering physician", "performed by",
#         "lab name", "laboratory", "facility"
#     ],
#
#     # ==================== EDUCATIONAL ====================
#     "transcript": [
#         "transcript", "official transcript", "academic transcript",
#         "academic record", "grade report", "student record",
#         "student name", "student id", "student number",
#         "institution", "college", "university", "school",
#         "degree", "major", "program of study",
#         "course", "course name", "course number", "course code",
#         "credits", "credit hours", "semester hours",
#         "grade", "letter grade", "grade point",
#         "gpa", "grade point average", "cumulative gpa",
#         "semester", "term", "academic year",
#         "degree conferred", "graduation date", "date awarded",
#         "honors", "dean's list", "academic honors"
#     ],
#
#     "certificate": [
#         "certificate", "certificate of completion", "certificate of achievement",
#         "certification", "professional certification",
#         "diploma", "degree certificate",
#         "awarded to", "presented to", "this certifies that",
#         "has successfully completed", "has demonstrated",
#         "completion date", "date of completion", "issued on",
#         "valid until", "expiration date", "renewal date",
#         "certification number", "certificate number",
#         "authorized signature", "registrar", "dean"
#     ],
#
#     "syllabus": [
#         "syllabus", "course syllabus", "class syllabus",
#         "course description", "course overview",
#         "instructor", "professor", "teaching assistant",
#         "office hours", "contact information",
#         "course objectives", "learning outcomes", "learning goals",
#         "required textbook", "required materials", "course materials",
#         "grading policy", "grading criteria", "grade breakdown",
#         "attendance policy", "late policy", "academic integrity",
#         "weekly schedule", "course schedule", "class schedule",
#         "assignments", "exams", "midterm", "final exam"
#     ],
#
#     # ==================== REAL ESTATE ====================
#     "property_deed": [
#         "deed", "property deed", "title deed",
#         "warranty deed", "quitclaim deed", "grant deed",
#         "grantor", "grantee", "property owner",
#         "legal description", "property description", "parcel number",
#         "lot", "block", "subdivision",
#         "consideration", "purchase price", "property value",
#         "convey", "transfer", "grant",
#         "encumbrances", "liens", "easements",
#         "notary", "notary public", "acknowledgment",
#         "recorded", "recording date", "book", "page"
#     ],
#
#     "mortgage_document": [
#         "mortgage", "mortgage agreement", "deed of trust",
#         "mortgagor", "mortgagee", "lender", "borrower",
#         "loan amount", "principal amount", "loan balance",
#         "interest rate", "apr", "annual percentage rate",
#         "monthly payment", "principal and interest",
#         "loan term", "amortization", "maturity date",
#         "escrow", "escrow account", "property taxes", "insurance",
#         "prepayment", "prepayment penalty",
#         "default", "foreclosure", "acceleration clause"
#     ],
#
#     # ==================== REPORTS ====================
#     "report": [
#         "executive summary", "report summary",
#         "introduction", "background", "overview",
#         "methodology", "methods", "approach",
#         "findings", "results", "analysis", "data analysis",
#         "discussion", "interpretation",
#         "conclusions", "summary of findings",
#         "recommendations", "proposed actions", "next steps",
#         "appendix", "appendices", "supplementary materials",
#         "references", "bibliography", "citations",
#         "table of contents", "list of figures", "list of tables"
#     ],
#
#     "audit_report": [
#         "audit report", "audit findings", "audit results",
#         "independent auditor", "auditor's opinion",
#         "financial audit", "compliance audit", "internal audit",
#         "audit period", "audit scope", "audit objective",
#         "material weakness", "significant deficiency",
#         "audit findings", "observations", "recommendations",
#         "management response", "corrective action",
#         "unqualified opinion", "qualified opinion", "adverse opinion"
#     ],
#
#     "incident_report": [
#         "incident report", "accident report", "event report",
#         "date of incident", "time of incident", "location of incident",
#         "incident description", "what happened", "sequence of events",
#         "persons involved", "witnesses", "witness statements",
#         "injuries", "injury description", "medical treatment",
#         "property damage", "damage description",
#         "root cause", "contributing factors",
#         "corrective actions", "preventive measures",
#         "reported by", "report date", "supervisor notified"
#     ]
# }
#
# REGEX = {
#     # ==================== FINANCIAL DOCUMENTS ====================
#     "invoice": re.compile(
#         r"\b(INV|invoice)[-_ ]?\d{3,}\b|"
#         r"\b(bill\s*to|invoice\s*number|invoice\s*date|amount\s*due|due\s*date|"
#         r"remit\s*to|payment\s*terms|invoice\s*total)\b",
#         re.I
#     ),
#
#     "purchase_order": re.compile(
#         r"\b(PO|purchase.?order)[-_ ]?\d{3,}\b|"
#         r"\b(po\s*number|order\s*number|vendor|supplier|ship\s*to|"
#         r"delivery\s*date|order\s*date)\b",
#         re.I
#     ),
#
#     "credit_memo": re.compile(
#         r"\b(CM|credit.?memo)[-_ ]?\d{3,}\b|"
#         r"\b(credit\s*memo|credit\s*note|refund|return\s*authorization|"
#         r"credit\s*amount|memo\s*number)\b",
#         re.I
#     ),
#
#     "bank_statement": re.compile(
#         r"\b(account\s+(number|no\.?|#|summary|statement|activity|balance|holder)|"
#         r"statement\s+(period|date|cycle|closing)|"
#         r"(opening|closing|beginning|ending|available|current|previous|ledger)\s+balance|"
#         r"(deposits?|withdrawals?|debits?|credits?)\s+(and|total|for)|"
#         r"total\s+for\s+this\s+period|"
#         r"(direct|wire|ach|electronic)\s+(deposit|transfer)|"
#         r"atm\s+withdrawal|cash\s+withdrawal|"
#         r"overdraft(\s+fees?)?|nsf(\s+fees?)?|returned\s+item\s+fees?|"
#         r"service\s+fees|maintenance\s+fees?|monthly\s+fees?|"
#         r"interest\s+(earned|paid)|"
#
#         # ==================== UNITED STATES ====================
#         r"bank\s+of\s+america|bofa|boa\s+|"
#         r"chase\s+(bank)?|jpmorgan\s+chase|jp\s+morgan|"
#         r"wells\s+fargo|"
#         r"citi(bank|group)?|"
#         r"u\.?s\.?\s+bank|us\s+bank|"
#         r"pnc\s+(bank)?|"
#         r"capital\s+one|"
#         r"td\s+bank|td\s+ameritrade|"
#         r"bank\s+of\s+the\s+west|"
#         r"fifth\s+third\s+bank|53\s+bank|"
#         r"regions\s+bank|"
#         r"m&t\s+bank|"
#         r"key\s?bank|"
#         r"truist|bb&t|suntrust|"
#         r"huntington\s+bank|"
#         r"citizens\s+bank|"
#         r"american\s+express|amex|"
#         r"discover\s+bank|"
#         r"ally\s+bank|"
#         r"charles\s+schwab|"
#         r"navy\s+federal|"
#         r"usaa|"
#
#         # ==================== UNITED KINGDOM ====================
#         r"barclays|"
#         r"hsbc|"
#         r"lloyds\s+(bank|banking\s+group)?|"
#         r"natwest|national\s+westminster|"
#         r"royal\s+bank\s+of\s+scotland|rbs|"
#         r"santander\s+uk|"
#         r"nationwide|"
#         r"standard\s+chartered|"
#         r"metro\s+bank|"
#         r"virgin\s+money|"
#         r"co.?operative\s+bank|co.?op\s+bank|"
#         r"tsc\s+bank|"
#
#         # ==================== CANADA ====================
#         r"royal\s+bank\s+of\s+canada|rbc|"
#         r"toronto.?dominion|td\s+canada|"
#         r"bank\s+of\s+montreal|bmo|"
#         r"scotiabank|bank\s+of\s+nova\s+scotia|"
#         r"cibc|canadian\s+imperial|"
#         r"national\s+bank\s+of\s+canada|"
#         r"desjardins|"
#         r"tangerine\s+bank|"
#
#         # ==================== EUROPE ====================
#         # France
#         r"bnp\s+paribas|"
#         r"crédit\s+agricole|credit\s+agricole|"
#         r"société\s+générale|societe\s+generale|socgen|"
#         r"crédit\s+mutuel|credit\s+mutuel|"
#         r"banque\s+populaire|"
#         r"caisse\s+d'épargne|caisse\s+d[''']epargne|"
#
#         # Germany
#         r"deutsche\s+bank|"
#         r"commerzbank|"
#         r"unicredit\s+bank|hypovereinsbank|"
#         r"dz\s+bank|"
#         r"kfw|"
#         r"landesbank|"
#         r"sparkasse|"
#         r"postbank|"
#
#         # Spain
#         r"banco\s+santander|"
#         r"bbva|banco\s+bilbao\s+vizcaya|"
#         r"caixabank|la\s+caixa|"
#         r"banco\s+sabadell|"
#
#         # Italy
#         r"intesa\s+sanpaolo|"
#         r"unicredit\s+(banca)?|"
#         r"banco\s+bpm|"
#         r"ubi\s+banca|"
#         r"monte\s+dei\s+paschi|"
#
#         # Netherlands
#         r"ing\s+(bank|group)?|"
#         r"abn\s+amro|"
#         r"rabobank|"
#
#         # Switzerland
#         r"ubs|"
#         r"credit\s+suisse|"
#         r"julius\s+baer|"
#
#         # Nordics
#         r"nordea|"
#         r"danske\s+bank|"
#         r"swedbank|"
#         r"handelsbanken|svenska\s+handelsbanken|"
#         r"seb|skandinaviska\s+enskilda\s+banken|"
#         r"dnb|"
#
#         # ==================== ASIA-PACIFIC ====================
#         # China
#         r"industrial\s+and\s+commercial\s+bank\s+of\s+china|icbc|"
#         r"china\s+construction\s+bank|ccb|"
#         r"agricultural\s+bank\s+of\s+china|abc|"
#         r"bank\s+of\s+china|boc(?!\w)|"  # Negative lookahead to avoid "bocce"
#         r"bank\s+of\s+communications|bocom|"
#         r"china\s+merchants\s+bank|cmb|"
#         r"ping\s+an\s+bank|"
#
#         # Japan
#         r"mitsubishi\s+ufj|mufg|"
#         r"sumitomo\s+mitsui|smbc|"
#         r"mizuho|"
#         r"japan\s+post\s+bank|"
#         r"resona|"
#
#         # India
#         r"state\s+bank\s+of\s+india|sbi(?!\w)|"
#         r"hdfc\s+bank|"
#         r"icici\s+bank|"
#         r"axis\s+bank|"
#         r"punjab\s+national\s+bank|pnb|"
#         r"bank\s+of\s+baroda|"
#         r"canara\s+bank|"
#         r"kotak\s+mahindra|"
#         r"yes\s+bank|"
#         r"indusind\s+bank|"
#
#         # Singapore
#         r"dbs\s+bank|development\s+bank\s+of\s+singapore|"
#         r"ocbc|oversea.?chinese\s+banking|"
#         r"uob|united\s+overseas\s+bank|"
#
#         # Australia
#         r"commonwealth\s+bank|commbank|cba|"
#         r"westpac|"
#         r"anz|australia\s+and\s+new\s+zealand|"
#         r"nab|national\s+australia\s+bank|"
#
#         # Hong Kong
#         r"hang\s+seng|"
#         r"bank\s+of\s+east\s+asia|"
#
#         # South Korea
#         r"kb\s+kookmin|kookmin\s+bank|"
#         r"shinhan\s+bank|"
#         r"hana\s+bank|"
#         r"woori\s+bank|"
#
#         # ==================== MIDDLE EAST & AFRICA ====================
#         # UAE
#         r"emirates\s+nbd|"
#         r"first\s+abu\s+dhabi\s+bank|fab|"
#         r"abu\s+dhabi\s+commercial\s+bank|adcb|"
#         r"mashreq\s+bank|"
#
#         # Saudi Arabia
#         r"national\s+commercial\s+bank|ncb|"
#         r"al\s+rajhi\s+bank|"
#         r"samba\s+financial|"
#         r"riyad\s+bank|"
#
#         # South Africa
#         r"standard\s+bank|"
#         r"absa|"
#         r"first\s+national\s+bank|fnb|"
#         r"nedbank|"
#         r"capitec|"
#
#         # ==================== LATIN AMERICA ====================
#         # Brazil
#         r"banco\s+do\s+brasil|"
#         r"itaú|itau|"
#         r"bradesco|"
#         r"caixa\s+econômica|caixa\s+economica|"
#         r"santander\s+brasil|"
#
#         # Mexico
#         r"bbva\s+bancomer|bbva\s+mexico|"
#         r"banamex|citibanamex|"
#         r"banorte|"
#         r"hsbc\s+mexico|"
#
#         # ==================== INTERNATIONAL BANKS ====================
#         r"goldman\s+sachs|"
#         r"morgan\s+stanley|"
#         r"credit\s+suisse|"
#         r"barclays\s+bank|"
#         r"mizuho\s+bank|"
#         r"nomura|"
#         r"citigroup|"
#
#         # ==================== DIGITAL/NEOBANKS ====================
#         r"revolut|"
#         r"n26|"
#         r"chime\s+bank|"
#         r"monzo|"
#         r"starling\s+bank|"
#         r"marcus\s+by\s+goldman|"
#         r"sofi|"
#         r"varo\s+bank|"
#         r"current|"
#         r"simple\s+bank|"
#         r"aspiration|"
#
#         # ==================== GENERIC PATTERNS ====================
#         r"routing\s+number|"
#         r"(checking|savings|money\s+market)\s+(account)?|"
#         r"year.?to.?date|ytd\s+(balance|total)|"
#         r"daily\s+balance|average\s+balance|"
#         r"transaction\s+(history|detail|description)|"
#         r"customer\s+service:\s*\d|"
#         r"en\s+español:\s*\d|"
#         r"p\.o\.\s+box\s+\d+)\b",
#         re.I
#     ),
#
#
#     "payroll": re.compile(
#         r"\b(payroll|pay\s*stub|pay\s*slip|earnings\s*statement|"
#         r"net\s*pay|gross\s*pay|wage(s)?|salary|compensation|"
#         r"ytd|year.?to.?date|hours\s*worked|regular\s*hours|overtime|"
#         r"deduction(s)?|withholding|tax\s*withheld|federal\s*tax|state\s*tax|"
#         r"fica|social\s*security|medicare|401k|retirement|"
#         r"employee\s*id|pay\s*period|pay\s*date)\b",
#         re.I
#     ),
#
#     "expense_report": re.compile(
#         r"\b(expense\s*report|expense\s*claim|reimburs(e|ement)|"
#         r"expense\s*category|expense\s*type|expense\s*date|"
#         r"receipt|employee\s*expense|business\s*expense|"
#         r"travel\s*expense|meal\s*expense|lodging|mileage|"
#         r"per\s*diem|total\s*expenses?|approved\s*by)\b",
#         re.I
#     ),
#
#     "receipt": re.compile(
#         r"\b(receipt|transaction\s*id|confirmation\s*number|"
#         r"payment\s*method|total\s*paid|amount\s*paid|"
#         r"thank\s*you\s*for\s*your\s*purchase|"
#         r"card\s*ending|card\s*number|cash|credit|debit|"
#         r"store\s*number|register|cashier|"
#         r"subtotal|tax|total|change)\b",
#         re.I
#     ),
#
#     # ==================== SALES & REPORTS ====================
#     "sales_report": re.compile(
#         r"\b(sales\s+(report|summary|analysis|dashboard|overview|metrics|data)|"
#         r"revenue\s+(report|summary|analysis|breakdown|by\s+region|by\s+product)|"
#         r"(quarterly|monthly|annual|weekly|daily)\s+sales\s+(report|summary|data)|"
#         r"sales\s+by\s+(region|product|category|territory|channel|customer|rep|team)|"
#         r"total\s+sales\s+(revenue|amount|volume)|"
#         r"gross\s+sales\s+total|net\s+sales\s+total|"
#         r"(units|quantity|items)\s+sold\s+(report|summary|by)|"
#         r"top\s+(selling|performing)\s+(products?|items?|skus?)|"
#         r"best\s+sellers?\s+(report|list|summary)|"
#         r"sales\s+(performance|forecast|target|goal|pipeline|funnel)\s+(report|summary|analysis)|"
#         r"market\s+share\s+(report|analysis)|"
#         r"product\s+(mix|line)\s+(report|analysis|performance)|"
#         r"sku\s+analysis|sku\s+performance|"
#         r"sales\s+(team|rep|representative)\s+(performance|report|ranking)|"
#         r"commission\s+report|quota\s+attainment|territory\s+performance|"
#         r"yoy\s+sales|year\s+over\s+year\s+sales)\b",
#         re.I
#     ),
#
#     # ==================== LEGAL & CONTRACTS ====================
#     "contract": re.compile(
#         r"\b(agreement|contract|terms\s*and\s*conditions|"
#         r"party\s*of\s*the\s*(first|second)|whereas|"
#         r"herein|hereinafter|hereby|witnesseth|"
#         r"effective\s*date|termination|term\s*of|"
#         r"covenant(s)?|obligation(s)?|representation(s)?|"
#         r"indemnif(y|ication)|liability|damages|"
#         r"governing\s*law|jurisdiction|arbitration|"
#         r"executed\s*on|in\s*witness\s*whereof|signature(s)?)\b",
#         re.I
#     ),
#
#     # ==================== HR DOCUMENTS ====================
#     "resume": re.compile(
#         r"\b(resume|curriculum\s*vitae|cv|"
#         r"education|degree|bachelor|master|phd|diploma|"
#         r"work\s*experience|employment\s*history|professional\s*experience|"
#         r"skills?|competenc(y|ies)|proficienc(y|ies)|"
#         r"certification(s)?|license(s)?|training|"
#         r"objective|summary|profile|"
#         r"references?\s*available)\b",
#         re.I
#     ),
#
#     "offer_letter": re.compile(
#         r"\b(offer\s*letter|employment\s*offer|job\s*offer|"
#         r"position|title|role|responsibilities?|"
#         r"start\s*date|commencement\s*date|"
#         r"annual\s*salary|compensation|benefits?|"
#         r"contingent\s*upon|background\s*check|"
#         r"at.?will\s*employment|probation(ary)?|"
#         r"please\s*sign|accept\s*this\s*offer)\b",
#         re.I
#     ),
#
#     # ==================== CORRESPONDENCE ====================
#     "letter": re.compile(
#         r"\b(dear\s*(sir|madam|mr\.|mrs\.|ms\.|dr\.)|"
#         r"to\s*whom\s*it\s*may\s*concern|"
#         r"sincerely|regards|respectfully|"
#         r"yours\s*(truly|faithfully|sincerely)|"
#         r"best\s*regards|kind\s*regards|warm\s*regards|"
#         r"cc:|bcc:|re:|subject:)\b",
#         re.I
#     ),
#
#     "memo": re.compile(
#         r"\b(memorandum|memo|"
#         r"to:|from:|date:|re:|subject:|"
#         r"internal\s*communication|company\s*memo|"
#         r"please\s*be\s*advised|for\s*your\s*information|fyi)\b",
#         re.I
#     ),
#
#     # ==================== SHIPPING & LOGISTICS ====================
#     "packing_slip": re.compile(
#         r"\b(packing\s*slip|packing\s*list|shipping\s*manifest|"
#         r"tracking\s*number|waybill|bill\s*of\s*lading|"
#         r"ship\s*to|ship\s*from|carrier|shipper|"
#         r"weight|dimensions?|package(s)?|box(es)?|"
#         r"shipped\s*via|delivery\s*date|expected\s*delivery)\b",
#         re.I
#     ),
#
#     "delivery_note": re.compile(
#         r"\b(delivery\s*note|delivery\s*receipt|proof\s*of\s*delivery|pod|"
#         r"received\s*by|signature|delivered\s*on|delivery\s*address)\b",
#         re.I
#     ),
#
#     # ==================== INSURANCE & CLAIMS ====================
#     "insurance_claim": re.compile(
#         r"\b(insurance\s*claim|claim\s*number|policy\s*number|"
#         r"claimant|insured|beneficiary|"
#         r"date\s*of\s*loss|incident\s*date|loss\s*description|"
#         r"coverage|deductible|claim\s*amount|settlement)\b",
#         re.I
#     ),
#
#     "policy": re.compile(
#         r"\b(policy\s*number|insurance\s*policy|coverage|premium|"
#         r"policyholder|insured|beneficiary|"
#         r"effective\s*date|expiration\s*date|renewal\s*date|"
#         r"coverage\s*amount|limit(s)?|deductible)\b",
#         re.I
#     ),
#
#     # ==================== MEDICAL ====================
#     "medical_record": re.compile(
#         r"\b(medical\s*record|patient\s*name|patient\s*id|mrn|"
#         r"date\s*of\s*birth|dob|age|gender|"
#         r"diagnosis|treatment|medication(s)?|prescription|"
#         r"vital\s*signs|blood\s*pressure|temperature|pulse|"
#         r"allergies|medical\s*history|doctor|physician|provider)\b",
#         re.I
#     ),
#
#     # ==================== TAX DOCUMENTS ====================
#     "tax_form": re.compile(
#         r"\b(w-?2|w-?4|w-?9|1099|1040|schedule\s*[a-z]|"
#         r"form\s*\d{4}|irs|internal\s*revenue|"
#         r"tax\s*return|taxable\s*income|adjusted\s*gross\s*income|agi|"
#         r"federal\s*tax|state\s*tax|tax\s*withheld|"
#         r"ein|employer\s*identification|ssn|social\s*security\s*number|"
#         r"filing\s*status|exemption(s)?|dependent(s)?)\b",
#         re.I
#     ),
#
#     # ==================== REPORTS ====================
#     "report": re.compile(
#         r"\b(executive\s*summary|introduction|background|methodology|"
#         r"findings?|results?|conclusion(s)?|recommendation(s)?|"
#         r"analysis|overview|abstract|appendix|references?|"
#         r"table\s*of\s*contents|section|chapter)\b",
#         re.I
#     ),
#
#     "financial_statement": re.compile(
#         r"\b(balance\s*sheet|income\s*statement|profit\s*and\s*loss|p&l|"
#         r"cash\s*flow\s*statement|statement\s*of\s*operations|"
#         r"assets?|liabilities?|equity|shareholders?\s*equity|"
#         r"revenue|expenses?|net\s*income|ebitda|"
#         r"accounts\s*receivable|accounts\s*payable|"
#         r"fiscal\s*year|quarter|q[1-4]|fy\d{4})\b",
#         re.I
#     ),
#
#     # ==================== EDUCATIONAL ====================
#     "transcript": re.compile(
#         r"\b(transcript|academic\s*record|grade\s*report|"
#         r"student\s*id|student\s*name|gpa|grade\s*point\s*average|"
#         r"course|credits?|semester|term|year|"
#         r"degree\s*conferred|graduation\s*date|honors?|dean'?s\s*list)\b",
#         re.I
#     ),
#
#     "certificate": re.compile(
#         r"\b(certificate|certification|diploma|"
#         r"awarded\s*to|presented\s*to|this\s*certifies|"
#         r"completion|achievement|recognition|"
#         r"issued\s*on|valid\s*until|expiration)\b",
#         re.I
#     ),
# }
#
#
# def normalize_header(h: str) -> str:
#     """Normalize header to lowercase and remove special chars except spaces."""
#     if not h:
#         return ""
#     # Convert to lowercase and replace special chars with spaces
#     normalized = re.sub(r"[^\w\s]", " ", str(h).lower()).strip()
#     # Collapse multiple spaces
#     normalized = re.sub(r"\s+", " ", normalized)
#     return normalized
#
#
# def header_tokens(headers: List[str]) -> List[str]:
#     tokens = []
#     for h in headers:
#         tokens += normalize_header(h).split()
#     return tokens
#
#
# class SpreadsheetClassifier:
#     """
#     Hybrid spreadsheet classifier: rule layer + optional ML layer + ensemble.
#     Input: table dict with keys 'columns' and 'data'/'rows'.
#     Output: dict { type, confidence, reasoning, matched_keywords, rule_scores, ml_scores, feature_breakdown }.
#     """
#
#     def __init__(self, ml_model=ML_MODEL, vectorizer=VECTORIZER, rule_weight: float = 0.6):
#         self.ml_model = ml_model
#         self.vectorizer = vectorizer
#         self.rule_weight = float(rule_weight)
#
#     def rule_layer(self, headers: List[str], sample_rows: List[List], text_blob: str) -> Dict[str, float]:
#         """Rule layer that uses the full text blob (headers + rows + PDF text)"""
#         try:
#             scores = {}
#
#             for dtype, kws in KEYWORDS.items():
#                 # Count keyword matches in full text blob
#                 match_count = sum(1 for k in kws if k in text_blob)
#
#                 # Check regex
#                 regex_match = 0
#                 if REGEX.get(dtype):
#                     try:
#                         if REGEX[dtype].search(text_blob):
#                             regex_match = 1
#                     except:
#                         pass
#
#                 # Calculate score
#                 keyword_score = min(1.0, match_count * 0.15)
#                 regex_score = regex_match * 0.3
#
#                 scores[dtype] = min(1.0, keyword_score + regex_score)
#
#             logger.info(f"📊 Rule scores: {scores}")
#             return scores
#
#         except Exception as e:
#             logger.error(f"❌ Rule layer failed: {e}")
#             return {}
#
#     # def rule_layer(self, headers: List[str], sample_rows: List[List[Any]]) -> Dict[str, float]:
#     #     """Enhanced rule layer with better keyword matching."""
#     #     tokens = header_tokens(headers)
#     #
#     #     # Create text blob from headers and sample rows
#     #     text_blob = " ".join(tokens)
#     #
#     #     # Add sample data to text blob (first 5 cells of first 10 rows)
#     #     for row in sample_rows[:10]:
#     #         row_text = " ".join(str(cell).lower() for cell in row[:5] if cell)
#     #         text_blob += " " + row_text
#     #
#     #     scores = {}
#     #
#     #     for dtype, kws in KEYWORDS.items():
#     #         # Count keyword matches
#     #         match_count = sum(1 for k in kws if k in text_blob)
#     #
#     #         # Check regex match
#     #         regex_match = 0
#     #         if REGEX.get(dtype):
#     #             if REGEX[dtype].search(text_blob):
#     #                 regex_match = 1
#     #
#     #         # Calculate score
#     #         # Higher weight for more matches
#     #         keyword_score = min(1.0, match_count * 0.15)  # Each keyword adds 0.15 (cap at 1.0)
#     #         regex_score = regex_match * 0.3
#     #
#     #         scores[dtype] = min(1.0, keyword_score + regex_score)
#     #
#     #     logger.info(f"📊 Rule scores: {scores}")
#     #
#     #     return scores
#
#     def feature_engineering(self, headers: List[str], rows: List[List[Any]]) -> Dict[str, Any]:
#         num_cols = len(headers)
#         num_rows = max(0, len(rows))
#         numeric_cols = 0
#         sample = rows[:min(50, len(rows))]
#         for col_idx in range(num_cols):
#             col_vals = [r[col_idx] for r in sample if len(r) > col_idx]
#             if not col_vals:
#                 continue
#             numeric_count = sum(1 for v in col_vals if isinstance(v, (int, float, np.number)))
#             if numeric_count / max(1, len(col_vals)) > 0.6:
#                 numeric_cols += 1
#         has_currency = any("$" in str(cell) for row in sample for cell in row[:10])
#         header_text = " ".join(header_tokens(headers))
#         return {
#             "num_cols": num_cols,
#             "num_rows": num_rows,
#             "numeric_col_ratio": numeric_cols / max(1, num_cols),
#             "has_currency": float(bool(has_currency)),
#             "header_text": header_text,
#         }
#
#     def ml_layer(self, headers: List[str], rows: List[List[Any]]) -> Dict[str, float]:
#         if not self.ml_model or not self.vectorizer:
#             return {}
#         feats = self.feature_engineering(headers, rows)
#         vec = self.vectorizer.transform([feats["header_text"]])
#         try:
#             probs = self.ml_model.predict_proba(vec)[0]
#             classes = list(self.ml_model.classes_)
#             return dict(zip(classes, probs.tolist()))
#         except Exception as e:
#             logger.exception("ML model prediction failed: %s", e)
#             return {}
#
#     def ensemble(self, rule_scores: Dict[str, float], ml_scores: Dict[str, float]) -> Dict[str, float]:
#         final = {}
#         for dtype in set(list(rule_scores.keys()) + list(ml_scores.keys())):
#             r = rule_scores.get(dtype, 0.0)
#             m = ml_scores.get(dtype, 0.0)
#             final[dtype] = (self.rule_weight * r) + ((1.0 - self.rule_weight) * m)
#         return final
#
#     # app/services/classifiers/spreadsheet_classifier.py
#
#     def classify(self, table: Dict[str, Any], parsed: Dict[str, Any] = None, fmt: str = None) -> Dict[str, Any]:
#         """
#         Classify a spreadsheet table with full context.
#
#         Args:
#             table: The table/sheet to classify
#             parsed: Full parsed document (for context - text content, metadata, etc)
#             fmt: File format (pdf, xlsx, csv, etc)
#
#         Returns:
#             Classification result
#         """
#         # Validate table
#         if not isinstance(table, dict):
#             logger.error(f"❌ Invalid table type: {type(table)}")
#             return self._error_response(f"Expected dict, got {type(table)}")
#
#         # Extract headers
#         headers = None
#         for key in ['columns', 'headers', 'cols']:
#             candidate = table.get(key)
#             if candidate is not None and isinstance(candidate, (list, tuple)):
#                 headers = candidate
#                 logger.info(f"📊 Using '{key}' for headers ({len(candidate)} columns)")
#                 break
#
#         if headers is None:
#             headers = []
#
#         # Extract rows
#         rows = None
#         for key in ['data', 'rows']:
#             candidate = table.get(key)
#             if candidate is not None and isinstance(candidate, (list, tuple)):
#                 rows = candidate
#                 logger.info(f"📊 Using '{key}' for row data ({len(candidate)} rows)")
#                 break
#
#         if rows is None:
#             rows = []
#
#         # Extract headers from first row if needed
#         if not headers and rows and len(rows) > 0:
#             first_row = rows[0]
#             if isinstance(first_row, (list, tuple)):
#                 headers = [str(cell) if cell is not None else "" for cell in first_row]
#                 rows = rows[1:]
#                 logger.info(f"📋 Extracted headers from first row: {headers}")
#
#         if not headers and not rows:
#             logger.warning("⚠️ Empty table")
#             return {
#                 "type": "spreadsheet",
#                 "confidence": 0.2,
#                 "reasoning": "empty_table",
#                 "matched_keywords": [],
#                 "rule_scores": {},
#                 "ml_scores": {}
#             }
#
#         # ==================== ENHANCED CLASSIFICATION WITH CONTEXT ====================
#         try:
#             sample_rows = [row for row in rows[:200] if isinstance(row, (list, tuple))]
#
#             logger.info(f"📊 Classifying: {len(headers)} cols, {len(sample_rows)} rows")
#             logger.info(f"📋 Headers: {headers}")
#             if sample_rows:
#                 logger.info(f"📝 Sample row: {sample_rows[0]}")
#
#             # Build text blob from table AND parsed text content
#             text_blob = " ".join(header_tokens(headers))
#
#             # Add row content
#             for row in sample_rows[:10]:
#                 if isinstance(row, (list, tuple)):
#                     row_text = " ".join(str(cell).lower() for cell in row if cell is not None)
#                     text_blob += " " + row_text
#
#             # Add parsed text if available (THIS IS KEY FOR PDFS!)
#             if parsed and parsed.get("text"):
#                 pdf_text = str(parsed["text"])[:1000].lower()  # First 1000 chars
#                 text_blob += " " + pdf_text
#                 logger.info(f"📄 Added {len(pdf_text)} chars from PDF text")
#
#             # Calculate scores
#             rule_scores = self.rule_layer(headers, sample_rows, text_blob)
#             ml_scores = self.ml_layer(headers, sample_rows)
#             final_scores = self.ensemble(rule_scores, ml_scores)
#
#             logger.info(f"🎯 Final scores: {final_scores}")
#
#             # Pick best
#             if final_scores:
#                 best_type, best_score = max(final_scores.items(), key=lambda x: x[1])
#             else:
#                 best_type, best_score = "spreadsheet", 0.0
#
#             # Extract matched keywords
#             matched = []
#             for dtype, kws in KEYWORDS.items():
#                 found = [k for k in kws if k in text_blob]
#                 if found:
#                     matched.append({"type": dtype, "keywords": found})
#
#             logger.info(f"✅ Classification: {best_type} ({best_score:.3f})")
#             logger.info(f"📝 Matched keywords: {matched}")
#
#             return {
#                 "type": best_type,
#                 "confidence": float(best_score),
#                 "reasoning": "ensemble(rule+ml)" if ml_scores else "rule_only",
#                 "matched_keywords": matched,
#                 "rule_scores": rule_scores,
#                 "ml_scores": ml_scores
#             }
#
#         except Exception as e:
#             logger.error(f"❌ Classification failed: {e}", exc_info=True)
#             return self._error_response(str(e))
#
#
#     # def classify(self, table: Dict[str, Any]) -> Dict[str, Any]:
#     #     """
#     #     Classify a spreadsheet table with ultra-safe error handling.
#     #
#     #     Args:
#     #         table: Dict with 'columns'/'headers'/'cols' and 'data'/'rows' keys
#     #
#     #     Returns:
#     #         Dict with classification results
#     #     """
#     #     # ==================== INPUT VALIDATION ====================
#     #     if not isinstance(table, dict):
#     #         logger.error(f"❌ Invalid table type: {type(table)}")
#     #         return self._error_response(f"Expected dict, got {type(table)}")
#     #
#     #     # ==================== EXTRACT HEADERS ====================
#     #     # Try multiple key names
#     #     headers = None
#     #     for key in ['columns', 'headers', 'cols']:
#     #         candidate = table.get(key)
#     #         if candidate is not None:
#     #             # Check if it's actually a list (not an int count!)
#     #             if isinstance(candidate, (list, tuple)):
#     #                 headers = candidate
#     #                 logger.info(f"📊 Using '{key}' for headers ({len(candidate)} columns)")
#     #                 break
#     #             elif isinstance(candidate, int):
#     #                 # It's just a count, skip it
#     #                 logger.debug(f"⚠️ '{key}' is int ({candidate}), skipping")
#     #                 continue
#     #
#     #     if headers is None:
#     #         headers = []
#     #         logger.warning("⚠️ No column headers found")
#     #
#     #     # ==================== EXTRACT ROWS ====================
#     #     rows = None
#     #
#     #     # Try multiple key names, but validate they're actually lists
#     #     for key in ['data', 'rows']:
#     #         candidate = table.get(key)
#     #         if candidate is not None:
#     #             # Check if it's actually a list (not an int count!)
#     #             if isinstance(candidate, (list, tuple)):
#     #                 rows = candidate
#     #                 logger.info(f"📊 Using '{key}' for row data ({len(candidate)} rows)")
#     #                 break
#     #             elif isinstance(candidate, int):
#     #                 # It's just a count, skip it
#     #                 logger.debug(f"⚠️ '{key}' is int ({candidate}), skipping")
#     #                 continue
#     #
#     #     if rows is None:
#     #         rows = []
#     #         logger.warning("⚠️ No row data found")
#     #
#     #     # ==================== VALIDATE DATA ====================
#     #     if not headers and not rows:
#     #         logger.warning("⚠️ Empty table (no headers or rows)")
#     #         return {
#     #             "type": "spreadsheet",
#     #             "confidence": 0.2,
#     #             "reasoning": "empty_table",
#     #             "matched_keywords": [],
#     #             "rule_scores": {},
#     #             "ml_scores": {},
#     #             "feature_breakdown": {}
#     #         }
#     #
#     #     # ==================== EXTRACT HEADERS FROM FIRST ROW IF MISSING ====================
#     #     # If no headers but we have rows, use first row as headers
#     #     if not headers and rows and len(rows) > 0:
#     #         first_row = rows[0]
#     #         if isinstance(first_row, (list, tuple)):
#     #             headers = [str(cell) if cell is not None else "" for cell in first_row]
#     #             rows = rows[1:]  # Remove header row from data
#     #             logger.info(f"📋 Extracted headers from first row: {headers}")
#     #
#     #     # ==================== CLASSIFICATION ====================
#     #     try:
#     #         # Safe sample extraction
#     #         sample_rows = []
#     #         for row in rows[:200]:
#     #             if isinstance(row, (list, tuple)):
#     #                 sample_rows.append(row)
#     #
#     #         logger.info(f"📊 Classifying: {len(headers)} columns, {len(sample_rows)} sample rows")
#     #         logger.info(f"📋 Headers: {headers}")
#     #         logger.info(f"📝 Sample row: {sample_rows[0] if sample_rows else 'none'}")
#     #
#     #         # Calculate scores
#     #         rule_scores = self.rule_layer(headers, sample_rows)
#     #         ml_scores = self.ml_layer(headers, sample_rows)
#     #         final_scores = self.ensemble(rule_scores, ml_scores)
#     #
#     #         logger.info(f"🎯 Final scores: {final_scores}")
#     #
#     #         # Pick best
#     #         if final_scores:
#     #             best_type, best_score = max(final_scores.items(), key=lambda x: x[1])
#     #         else:
#     #             best_type, best_score = "spreadsheet", 0.0
#     #
#     #         # Extract matched keywords
#     #         header_blob = " ".join(header_tokens(headers))
#     #
#     #         # Also check row content for keywords
#     #         row_blob = ""
#     #         for row in sample_rows[:10]:
#     #             if isinstance(row, (list, tuple)):
#     #                 row_blob += " " + " ".join(str(cell).lower() for cell in row if cell is not None)
#     #
#     #         full_text_blob = header_blob + " " + row_blob
#     #
#     #         matched = []
#     #         for dtype, kws in KEYWORDS.items():
#     #             found = [k for k in kws if k in full_text_blob]
#     #             if found:
#     #                 matched.append({"type": dtype, "keywords": found})
#     #
#     #         logger.info(f"✅ Classification: {best_type} (confidence: {best_score:.3f})")
#     #         logger.info(f"📝 Matched keywords: {matched}")
#     #
#     #         return {
#     #             "type": best_type,
#     #             "confidence": float(best_score),
#     #             "reasoning": "ensemble(rule+ml)" if ml_scores else "rule_only",
#     #             "matched_keywords": matched,
#     #             "rule_scores": rule_scores,
#     #             "ml_scores": ml_scores,
#     #             "feature_breakdown": self._feature_engineering(headers, sample_rows)
#     #         }
#     #
#     #     except Exception as e:
#     #         logger.error(f"❌ Classification failed: {e}", exc_info=True)
#     #         return self._error_response(str(e))
#
#
#     # def classify(self, table: Dict[str, Any]) -> Dict[str, Any]:
#     #     """
#     #     Classify a spreadsheet table with enhanced debugging.
#     #
#     #     Args:
#     #         table: Dict with 'columns' and either 'data' or 'rows' keys
#     #
#     #     Returns:
#     #         Dict with classification results
#     #     """
#     #     # ==================== DEFENSIVE INPUT VALIDATION ====================
#     #     if not isinstance(table, dict):
#     #         logger.error(f"❌ Invalid table type: {type(table)}")
#     #         return {
#     #             "type": "spreadsheet",
#     #             "confidence": 0.1,
#     #             "reasoning": "error",
#     #             "matched_keywords": [],
#     #             "rule_scores": {},
#     #             "ml_scores": {},
#     #             "feature_breakdown": {},
#     #             "_error": f"Expected dict, got {type(table)}"
#     #         }
#     #
#     #     # ==================== EXTRACT HEADERS AND ROWS ====================
#     #     # Handle multiple possible key names
#     #     headers = table.get("columns") or table.get("headers") or table.get("cols") or []
#     #
#     #     # Try multiple keys for row data, prioritizing 'data' over 'rows'
#     #     # because 'rows' might be a count instead of actual data
#     #     rows = None
#     #     for key in ['data', 'rows']:
#     #         candidate = table.get(key)
#     #         if isinstance(candidate, (list, tuple)) and candidate:
#     #             rows = candidate
#     #             logger.info(f"📊 Using '{key}' for row data ({len(candidate)} rows)")
#     #             break
#     #
#     #     if rows is None:
#     #         rows = []
#     #
#     #     # Convert to list if needed
#     #     if not isinstance(headers, list):
#     #         headers = list(headers) if headers else []
#     #
#     #     if not isinstance(rows, (list, tuple)):
#     #         logger.error(f"❌ Invalid rows type: {type(rows)}")
#     #         logger.error(f"❌ Table keys: {list(table.keys())}")
#     #         for key in ['data', 'rows']:
#     #             if key in table:
#     #                 logger.error(f"❌ '{key}' type: {type(table.get(key))}")
#     #         return {
#     #             "type": "spreadsheet",
#     #             "confidence": 0.1,
#     #             "reasoning": "error",
#     #             "matched_keywords": [],
#     #             "rule_scores": {},
#     #             "ml_scores": {},
#     #             "feature_breakdown": {},
#     #             "_error": f"Expected list/tuple for rows, got {type(rows)}"
#     #         }
#     #
#     #     # Empty table handling
#     #     if not headers and not rows:
#     #         logger.warning("⚠️ Empty table (no headers or rows)")
#     #         return {
#     #             "type": "spreadsheet",
#     #             "confidence": 0.2,
#     #             "reasoning": "empty_table",
#     #             "matched_keywords": [],
#     #             "rule_scores": {},
#     #             "ml_scores": {},
#     #             "feature_breakdown": {}
#     #         }
#     #
#     #     # ==================== CLASSIFICATION ====================
#     #     sample_rows = rows[:200]
#     #
#     #     logger.info(f"📊 Classifying table: {len(headers)} columns, {len(rows)} rows")
#     #     logger.info(f"📋 Headers: {headers}")  # Log all headers for debugging
#     #
#     #     rule_scores = self.rule_layer(headers, sample_rows)
#     #     ml_scores = self.ml_layer(headers, sample_rows)
#     #     final_scores = self.ensemble(rule_scores, ml_scores)
#     #
#     #     logger.info(f"🎯 Final scores: {final_scores}")
#     #
#     #     # Pick best
#     #     if final_scores:
#     #         best_type, best_score = max(final_scores.items(), key=lambda x: x[1])
#     #     else:
#     #         best_type, best_score = "spreadsheet", 0.0
#     #
#     #     # Matched keywords for explainability
#     #     header_blob = " ".join(header_tokens(headers))
#     #     matched = []
#     #     for dtype, kws in KEYWORDS.items():
#     #         found = [k for k in kws if k in header_blob]
#     #         if found:
#     #             matched.append({"type": dtype, "keywords": found})
#     #
#     #     feature_breakdown = self.feature_engineering(headers, sample_rows)
#     #
#     #     logger.info(f"✅ Classification result: {best_type} (confidence: {best_score:.3f})")
#     #     logger.info(f"📝 Matched keywords: {matched}")
#     #
#     #     return {
#     #         "type": best_type,
#     #         "confidence": float(best_score),
#     #         "reasoning": "ensemble(rule+ml)" if ml_scores else "rule_only",
#     #         "matched_keywords": matched,
#     #         "rule_scores": rule_scores,
#     #         "ml_scores": ml_scores,
#     #         "feature_breakdown": feature_breakdown
#     #     }