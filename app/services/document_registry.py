"""
Central registry mapping document types → parser → analyzer.
This ensures classification → parsing → analysis is fully aligned.
"""
from app.services.analyzers.financial.credit_card_analyzer import CreditCardAnalyzer
from app.services.analyzers.financial.credit_memo_analyzer import CreditMemoAnalyzer
from app.services.analyzers.financial.expense_report_analyzer import ExpenseReportAnalyzer
from app.services.analyzers.financial.invoice_analyzer import InvoiceAnalyzer
from app.services.analyzers.financial.purchase_order_analyzer import PurchaseOrderAnalyzer
from app.services.analyzers.financial.receipt_analyzer import ReceiptAnalyzer
from app.services.analyzers.financial.sales_analyzer import SalesAnalyzer
from app.services.analyzers.financial.utility_bill_analyzer import UtilityBillAnalyzer
from app.services.analyzers.hr.pay_stub_analyzer import PayStubAnalyzer
from app.services.analyzers.image.image_analyzer import ImageAnalyzer
from app.services.analyzers.insurance.declarations_analyzer import DeclarationsAnalyzer
from app.services.analyzers.insurance.insurance_claim_analyzer import InsuranceClaimAnalyzer
from app.services.analyzers.insurance.insurance_policy_analyzer import InsurancePolicyAnalyzer
from app.services.analyzers.legal.immigration_receipt_analyzer import ImmigrationReceiptAnalyzer
from app.services.analyzers.real_estate.mortgage_document_analyzer import MortgageDocumentAnalyzer
from app.services.analyzers.real_estate.payoff_statement_analyzer import PayoffStatementAnalyzer
from app.services.analyzers.spreadsheet.dataset_analyzer import DatasetAnalyzer
from app.services.analyzers.tax.tax_document_analyzer import TaxFormAnalyzer
from app.services.parsers.universal_bank_parser import UniversalBankParser
from app.services.analyzers.financial.bank_statement_analyzer import BankStatementAnalyzer
from app.services.analyzers.medical.universal_medical_analyzer import UniversalMedicalRecordAnalyzer


from app.services.parsers.universal_invoice_parser import UniversalInvoiceParser
from app.services.parsers.universal_expense_report_parser import UniversalExpenseReportParser
from app.services.parsers.universal_credit_memo_parser import UniversalCreditMemoParser
from app.services.parsers.universal_parser import UniversalParser
from app.services.parsers.universal_purchase_order_parser import UniversalPurchaseOrderParser
from app.services.parsers.universal_sales_report_parser import UniversalSalesReportParser
from app.services.parsers.universal_payroll_parser import UniversalPayrollParser

from app.services.analyzers.generic_analyzer import GenericAnalyzer

# later: from app.services.analyzers.financial.invoice_analyzer import InvoiceAnalyzer
# later: from app.services.analyzers.financial.expense_report_analyzer import ExpenseReportAnalyzer
# etc.


DOCUMENT_REGISTRY = {
    # -------------------------------------------------
    # INSURANCE — DECLARATIONS & POLICIES
    # -------------------------------------------------
    "declarations_page": {  # ← NEW — Stillwater, Travelers, Hartford, etc.
        "parser": UniversalParser,
        "analyzer": DeclarationsAnalyzer,
    },
    "insurance_claim": {
        "parser": UniversalParser,
        "analyzer": InsuranceClaimAnalyzer,
    },

    # -------------------------------------------------
    # FINANCIAL – BANKING
    # -------------------------------------------------
    "bank_statement": {
        "parser": UniversalBankParser,
        "analyzer": BankStatementAnalyzer,
    },

    # -------------------------------------------------
    # FINANCIAL – TRANSACTIONAL DOCS (SPREADSHEET-FRIENDLY)
    # -------------------------------------------------
    "invoice": {
        "parser": UniversalParser,
        "analyzer": InvoiceAnalyzer,  # swap to InvoiceAnalyzer when ready
    },

    "utility_bill": {
        "parser": UniversalParser,
        "analyzer": UtilityBillAnalyzer,  # swap to UtilityBillAnalyzer when ready
    },

    "financial_statement": {
        "parser": UniversalParser,
        "analyzer": InvoiceAnalyzer,
    },
    "expense_report": {
        "parser": UniversalParser,
        "analyzer": ExpenseReportAnalyzer,  # ExpenseReportAnalyzer later
    },
    "credit_memo": {
        "parser": UniversalParser,
        "analyzer": CreditMemoAnalyzer,  # CreditMemoAnalyzer later
    },
    "purchase_order": {
        "parser": UniversalParser,
        "analyzer": PurchaseOrderAnalyzer,  # PurchaseOrderAnalyzer later
    },
    "sales_report": {
        "parser": UniversalParser,
        "analyzer": SalesAnalyzer,  # SalesReportAnalyzer later
    },
    "inventory_report": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,  # SalesReportAnalyzer later
    },
    "payroll_report": {
        "parser": UniversalParser,
        "analyzer": PayStubAnalyzer,  # PayrollAnalyzer later
    },
    "payroll": {
        "parser": UniversalParser,
        "analyzer": PayStubAnalyzer
    },

    # =========================================================================
    # OPERATIONAL DATA (INTERNAL BUSINESS)
    # =========================================================================
    "inventory": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "forecast_report": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "customer_list": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "product_catalog": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "employee_roster": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "order_history": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "transaction_log": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "contact_list": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "asset_register": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    # =========================================================================
    # DATASETS (REFERENCE/RESEARCH/EXTERNAL)
    # =========================================================================
    "data_table": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "movie_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "music_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "book_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "product_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "ratings_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "location_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "weather_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "sports_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "health_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "education_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "real_estate_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "stock_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "census_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "scientific_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "survey_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "event_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "generic_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    # -------------------------------------------------
    # GENERIC / FALLBACK TYPES
    # -------------------------------------------------
    "spreadsheet": {
        "parser": UniversalParser,  # or UniversalSpreadsheetParser if you have it
        "analyzer": DatasetAnalyzer,
    },
    "document": {
        "parser": UniversalParser,
        "analyzer": GenericAnalyzer,
    },
    "other": {
        "parser": UniversalParser,
        "analyzer": GenericAnalyzer,
    },

    # ==================== DATASETS (reference / research / tabular) ====================

    "dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "datasets": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "generic_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "movie_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "ratings_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "product_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "location_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "weather_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "sports_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "health_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "education_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "real_estate_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "stock_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "census_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "scientific_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "survey_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "event_dataset": {
        "parser": UniversalParser,
        "analyzer": DatasetAnalyzer,
    },

    "receipt": {
        "parser": UniversalParser,
        "analyzer": ReceiptAnalyzer,
    },

    "uscis_receipt": {
        "parser": UniversalParser,
        "analyzer": ImmigrationReceiptAnalyzer,
    },

    "immigration_receipt": {
        "parser": UniversalParser,
        "analyzer": ImmigrationReceiptAnalyzer,
    },

    "image": {
        "parser": UniversalParser,
        "analyzer": ImageAnalyzer,
    },

    "medical_record": {
        "analyzer": UniversalMedicalRecordAnalyzer,
        "extensions": [".csv", ".xlsx", ".xls", ".pdf", ".txt"],
        "priority": 10
    },

    "tax_form": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "1099_b": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "1099_div": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "1099_int": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "1099_misc": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "w2": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "tax_return": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    # These all route to TaxFormAnalyzer too
    # because payroll/W2 docs get misclassified as these types

    "government_form": {
        "parser": UniversalParser,
        "analyzer": TaxFormAnalyzer,
    },

    "credit_card_statement": {
        "parser": UniversalParser,
        "analyzer": CreditCardAnalyzer
    },

    "mortgage_document": {
        "parser": UniversalParser,
        "analyzer": MortgageDocumentAnalyzer,
    },
    "mortgage": {
        "parser": UniversalParser,
        "analyzer": MortgageDocumentAnalyzer,
    },
    "mortgage_statement": {
        "parser": UniversalParser,
        "analyzer": MortgageDocumentAnalyzer,
    },
    "loan_document": {
        "parser": UniversalParser,
        "analyzer": MortgageDocumentAnalyzer,
    },
    "loan_estimate": {
            "parser": UniversalParser,
            "analyzer": MortgageDocumentAnalyzer,
        },
    "closing_disclosure": {
        "parser": UniversalParser,
        "analyzer": MortgageDocumentAnalyzer,
    },
    "insurance_policy": {
        "parser": UniversalParser,
        "analyzer": InsurancePolicyAnalyzer,   # already exists
    },
    "payoff_statement": {
        "parser": UniversalParser,
        "analyzer": PayoffStatementAnalyzer,  # or PayoffStatementAnalyzer later
    },
}
