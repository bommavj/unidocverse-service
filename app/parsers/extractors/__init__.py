"""
Document Extractors
Specialized extractors for different document types
"""

from app.parsers.extractors.invoice_extractor import InvoiceExtractor
from app.parsers.extractors.bank_statement_extractor import BankStatementExtractor
from app.parsers.extractors.receipt_extractor import ReceiptExtractor
from app.parsers.extractors.contract_extractor import ContractExtractor
from app.parsers.extractors.tax_form_extractor import TaxFormExtractor

__all__ = [
    'InvoiceExtractor',
    'BankStatementExtractor',
    'ReceiptExtractor',
    'ContractExtractor',
    'TaxFormExtractor',
]