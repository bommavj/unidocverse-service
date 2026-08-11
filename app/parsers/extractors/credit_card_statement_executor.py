from typing import List, Dict, Any

class CreditCardStatementExtractor:
    """Extract transactions, balances, payment info from CC statements"""

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        return {
            'account_number': self._extract_masked_account(text),
            'statement_date': self._extract_date(text, r'Statement Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
            'due_date': self._extract_date(text, r'Payment Due:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
            'previous_balance': self._extract_currency(text, r'Previous Balance:?\s*\$?([\d,]+\.?\d*)'),
            'new_balance': self._extract_currency(text, r'New Balance:?\s*\$?([\d,]+\.?\d*)'),
            'minimum_payment': self._extract_currency(text, r'Minimum Payment:?\s*\$?([\d,]+\.?\d*)'),
            'credit_limit': self._extract_currency(text, r'Credit Limit:?\s*\$?([\d,]+\.?\d*)'),
            'available_credit': self._extract_currency(text, r'Available Credit:?\s*\$?([\d,]+\.?\d*)'),
            'transactions': self._extract_transactions(tables),
            'total_charges': self._extract_currency(text, r'Total Charges:?\s*\$?([\d,]+\.?\d*)'),
            'total_payments': self._extract_currency(text, r'Total Payments:?\s*\$?([\d,]+\.?\d*)'),
        }