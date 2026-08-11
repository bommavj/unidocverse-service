ROLE_BY_TYPE = {
    # FINANCIAL
    "invoice": "You are an invoice and billing analyst.",
    "receipt": "You are a receipt and transaction analyst.",
    "bank_statement": "You are a bank statement and financial transaction analyst.",
    "credit_card_statement": "You are a credit card statement analyst.",
    "purchase_order": "You are a purchase order and procurement analyst.",
    "expense_report": "You are an expense report analyst.",
    "utility_bill": "You are a utility bill analyst.",
    "payroll": "You are a payroll and compensation analyst.",
    "pay_stub": "You are a payroll and compensation analyst.",

    # INSURANCE
    "insurance_policy": "You are an insurance policy and coverage analyst.",
    "insurance_claim": "You are an insurance claim analyst.",
    "declarations_page": "You are an insurance declarations page analyst.",

    # REAL ESTATE / MORTGAGE
    "mortgage_document": "You are a mortgage and loan document analyst.",
    "mortgage_statement": "You are a mortgage statement analyst.",
    "closing_disclosure": "You are a closing disclosure analyst.",
    "loan_estimate": "You are a loan estimate analyst.",
    "payoff_statement": "You are a payoff statement analyst.",

    # TAX
    "tax_form": "You are a tax form analyst.",
    "1099_b": "You are a tax form analyst.",
    "1099_div": "You are a tax form analyst.",
    "1099_int": "You are a tax form analyst.",
    "1099_misc": "You are a tax form analyst.",
    "w2": "You are a tax form analyst.",

    # MEDICAL
    "medical_record": "You are a medical record and clinical summary analyst.",

    # LEGAL
    "immigration_receipt": "You are an immigration receipt and case-status analyst.",
    "uscis_receipt": "You are an immigration receipt and case-status analyst.",

    # DATASETS
    "dataset": "You are a dataset and tabular data analyst.",
    "spreadsheet": "You are a dataset and tabular data analyst.",
    "data_table": "You are a dataset and tabular data analyst.",

    # IMAGES
    "image": "You are an image content analyst.",

    # GENERIC
    "document": "You are a general document analyst.",
    "other": "You are a general document analyst.",
}

def get_role_for_doc_type(doc_type: str) -> str:
    return ROLE_BY_TYPE.get(doc_type, "You are a document analyst.")