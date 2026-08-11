"""
doc_type_prompts.py

Master library of domain‑aware insight patterns for every document type.
Used by: key point generator, insight node, summary node, deep analysis.
"""

# ---------------------------------------------------------------------
# UNIVERSAL FALLBACK (used when doc_type not found)
# ---------------------------------------------------------------------
GENERIC_INSIGHTS = [
    "Main purpose of the document",
    "Important dates or deadlines",
    "Key people, entities, or organizations mentioned",
    "Actions required by the reader",
    "Important numbers, statistics, or facts",
    "Policy or rule changes",
    "Risks, warnings, or compliance notes",
    "Important links, references, or contact information",
    "Summary of major sections",
    "Overall conclusion or next steps"
]

# ---------------------------------------------------------------------
# DOMAIN‑SPECIFIC INSIGHT PATTERNS
# ---------------------------------------------------------------------
doc_type_insights_prompt = {

    # ================================================================
    # FINANCIAL DOCUMENTS
    # ================================================================
    "invoice": [
        "Total amount due and due date",
        "Outstanding balance or past‑due status",
        "Invoice number and billing period",
        "Vendor and customer details",
        "Payment terms (Net 30, Net 15, etc.)",
        "Late fee policy or penalties",
        "Discounts applied or early‑pay incentives",
        "Tax amounts and tax breakdown",
        "Line‑item totals and quantities",
        "Shipping or handling fees",
        "PO number or reference number",
        "Recurring billing indicators",
        "Service period or coverage dates",
        "Discrepancies or unusual charges",
        "Vendor bank details (if present)"
    ],

    "receipt": [
        "Total amount paid",
        "Payment method used",
        "Store or vendor name",
        "Itemized purchase list",
        "Taxes and fees",
        "Discounts applied",
        "Transaction date and time",
        "Store location or address",
        "Return or refund policy",
        "Loyalty or rewards information"
    ],

    "bank_statement": [
        "Starting and ending balance",
        "Total deposits and withdrawals",
        "Largest single deposit",
        "Largest single withdrawal",
        "Recurring transactions detected",
        "Unusual or suspicious activity",
        "Overdraft fees or penalties",
        "ATM withdrawals summary",
        "Interest earned",
        "Subscription or autopay detection",
        "Daily average balance",
        "Foreign transaction fees"
    ],

    "credit_card_statement": [
        "Statement balance and due date",
        "Minimum payment required",
        "APR and interest charges",
        "Largest purchases",
        "Recurring subscriptions",
        "Cash advance activity",
        "Late fees or penalties",
        "Rewards earned",
        "Credit limit and available credit",
        "Fraud‑like transactions"
    ],

    "purchase_order": [
        "PO number and date",
        "Buyer and vendor details",
        "Itemized list of goods/services",
        "Quantities and unit prices",
        "Shipping terms",
        "Delivery date",
        "Approval signatures",
        "Budget or cost center references"
    ],

    "expense_report": [
        "Total reimbursable amount",
        "Employee name and department",
        "Expense categories",
        "Policy violations",
        "Missing receipts",
        "Duplicate expenses",
        "Mileage or travel details",
        "Approval status"
    ],

    "utility_bill": [
        "Total amount due",
        "Usage summary (kWh, gallons, etc.)",
        "Billing period",
        "Rate plan details",
        "Late fees",
        "Previous balance",
        "Service address",
        "Meter readings",
        "Peak vs off‑peak usage"
    ],

    # ================================================================
    # INSURANCE DOCUMENTS
    # ================================================================
    "insurance_policy": [
        "Coverage limits",
        "Deductibles",
        "Policy period",
        "Named insured",
        "Key exclusions",
        "Endorsements",
        "Premium amount",
        "Liability limits",
        "Property coverage details",
        "Claim filing instructions"
    ],

    "insurance_claim": [
        "Claim number",
        "Claim status",
        "Loss date",
        "Cause of loss",
        "Coverage determination",
        "Approved vs denied items",
        "Payout amount",
        "Adjuster notes",
        "Required documentation"
    ],

    "declarations_page": [
        "Policy number",
        "Coverage limits",
        "Deductibles",
        "Premium summary",
        "Named insured",
        "Property address",
        "Endorsements",
        "Liability coverage",
        "Effective and expiration dates"
    ],

    # ================================================================
    # REAL ESTATE / MORTGAGE
    # ================================================================
    "mortgage_document": [
        "Loan amount and interest rate",
        "Loan term and amortization schedule",
        "Monthly payment breakdown",
        "Escrow requirements",
        "Prepayment penalties",
        "Late fee structure",
        "Property address",
        "Borrower and lender names",
        "Adjustable rate terms",
        "Balloon payment details"
    ],

    "closing_disclosure": [
        "Loan terms summary",
        "Cash to close",
        "Closing costs",
        "APR and finance charges",
        "Escrow details",
        "Projected payments",
        "Loan costs vs other costs",
        "Seller credits",
        "Prepaids and escrow deposits"
    ],

    # ================================================================
    # TAX DOCUMENTS
    # ================================================================
    "tax_form": [
        "Taxpayer identity",
        "Income summary",
        "Deductions claimed",
        "Tax credits",
        "Refund or amount owed",
        "Withholding summary",
        "Filing status",
        "Dependents",
        "Important IRS codes"
    ],

    # ================================================================
    # MEDICAL DOCUMENTS
    # ================================================================
    "medical_record": [
        "Primary diagnosis",
        "Secondary diagnoses",
        "Treatment plan",
        "Medications prescribed",
        "Allergies",
        "Lab results outside normal range",
        "Imaging findings",
        "Follow‑up recommendations",
        "Vital signs summary",
        "Provider notes"
    ],

    # ================================================================
    # LEGAL / IMMIGRATION
    # ================================================================
    "immigration_receipt": [
        "Receipt number",
        "Case type",
        "Filing date",
        "Applicant name",
        "Service center",
        "Next steps",
        "Processing timelines",
        "Important notices"
    ],

    # ================================================================
    # NEWSLETTERS / COMMUNITY DOCS
    # ================================================================
    "newsletter": [
        "Upcoming events and dates",
        "Deadlines for homeowner actions",
        "Board meeting dates",
        "Election announcements",
        "Policy or rule changes",
        "Community reminders",
        "New vendors or services",
        "Safety or security updates",
        "Maintenance schedules",
        "Amenity updates"
    ],

    # ================================================================
    # DATASETS / SPREADSHEETS
    # ================================================================
    "dataset": [
        "Row count and column count",
        "Missing value percentage",
        "Most common categories",
        "Outliers detected",
        "Numeric summary statistics",
        "Correlation insights",
        "Duplicate row detection",
        "Data quality score",
        "Anomalies or inconsistencies",
        "Trend patterns"
    ],


    # ================================================================
    # GENERIC FALLBACK
    # ================================================================
    "document": GENERIC_INSIGHTS,
    "other": GENERIC_INSIGHTS,
}


doc_type_summary_prompt = {
    "invoice": [
        "Summarize total amount due, due date, and invoice number",
        "Summarize vendor and customer details",
        "Summarize line items and charges",
        "Summarize taxes, fees, and discounts",
        "Summarize payment terms and outstanding balance"
    ],
    "bank_statement": [
        "Summarize starting and ending balances",
        "Summarize deposits and withdrawals",
        "Summarize recurring transactions",
        "Summarize fees and interest",
        "Summarize unusual or suspicious activity"
    ],
    "newsletter": [
        "Summarize upcoming events and dates",
        "Summarize deadlines and announcements",
        "Summarize board or committee updates",
        "Summarize policy or rule changes",
        "Summarize community reminders"
    ],
    "mortgage_document": [
        "Summarize loan amount, rate, and term",
        "Summarize monthly payment structure",
        "Summarize escrow and insurance details",
        "Summarize prepayment or late fee rules",
        "Summarize borrower and lender obligations"
    ],
    "medical_record": [
        "Summarize diagnoses",
        "Summarize treatment plan",
        "Summarize medications",
        "Summarize lab or imaging findings",
        "Summarize follow‑up instructions"
    ],
    "dataset": [
        "Summarize dataset size and structure",
        "Summarize missing values",
        "Summarize key metrics",
        "Summarize patterns or trends",
        "Summarize anomalies or outliers"
    ],
    "document": [
        "Summarize main topic",
        "Summarize key facts",
        "Summarize important dates",
        "Summarize actions required",
        "Summarize overall conclusion"
    ]
}
