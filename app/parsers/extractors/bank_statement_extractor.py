"""
Bank Statement Extractor
Extracts structured transaction data from bank statements
"""

import logging
import re
from typing import Dict, Any, List
from datetime import datetime
import calendar

logger = logging.getLogger(__name__)


class BankStatementExtractor:
    """Extract structured data from bank statements"""

    def extract(self, text: str, tables: List[Dict]) -> Dict[str, Any]:
        """Extract bank statement-specific fields"""

        return {
            'account_number': self._extract_account_number(text),
            'account_holder': self._extract_account_holder(text),
            'statement_period': self._extract_statement_period(text),
            'opening_balance': self._extract_balance(text, 'opening'),
            'closing_balance': self._extract_balance(text, 'closing'),
            'transactions': self._extract_transactions(text, tables),
            'total_credits': self._calculate_total_credits(text, tables),
            'total_debits': self._calculate_total_debits(text, tables),
            'bank_name': self._extract_bank_name(text),
        }

    def _extract_account_number(self, text: str) -> str:
        """Extract account number"""
        patterns = [
            r'account\s*(?:number|#)?\s*:?\s*([0-9\-]{8,20})',
            r'acct\s*(?:no|#)?\s*:?\s*([0-9\-]{8,20})',
            r'account:\s*([0-9\-]{8,20})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ''

    def _extract_account_holder(self, text: str) -> str:
        """Extract account holder name"""
        patterns = [
            r'account\s*holder\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            r'name\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ''

    def _extract_statement_period(self, text: str) -> Dict[str, str]:
        """Extract statement start and end dates"""
        patterns = [
            r'statement\s*period\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:to|-)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'from\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*to\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return {
                    'start_date': match.group(1),
                    'end_date': match.group(2)
                }

        return {'start_date': '', 'end_date': ''}

    def _extract_balance(self, text: str, balance_type: str) -> float:
        """Extract opening or closing balance"""
        patterns = {
            'opening': [
                r'opening\s*balance\s*:?\s*\$?([\d,]+\.?\d*)',
                r'beginning\s*balance\s*:?\s*\$?([\d,]+\.?\d*)',
            ],
            'closing': [
                r'closing\s*balance\s*:?\s*\$?([\d,]+\.?\d*)',
                r'ending\s*balance\s*:?\s*\$?([\d,]+\.?\d*)',
                r'final\s*balance\s*:?\s*\$?([\d,]+\.?\d*)',
            ]
        }

        for pattern in patterns.get(balance_type, []):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(',', '')
                try:
                    return float(amount_str)
                except:
                    pass

        return 0.0

    def _extract_transactions(self, text: str, tables: List[Dict]) -> List[Dict[str, Any]]:
        """Extract all transactions"""
        transactions = []

        # Try to extract from tables first (most reliable)
        if tables:
            transactions = self._extract_transactions_from_tables(tables)

        # If no transactions from tables, try text parsing
        if not transactions:
            transactions = self._extract_transactions_from_text(text)

        return transactions[:100]  # Limit to 100 transactions

    def _extract_transactions_from_tables(self, tables: List[Dict]) -> List[Dict[str, Any]]:
        """Extract transactions from table data"""
        transactions = []

        # Find the table that looks like transactions (has date, description, amount columns)
        for table in tables:
            data = table.get('data', [])
            if len(data) < 2:  # Need at least header + 1 row
                continue

            # Check if this looks like a transaction table
            header = [str(col).lower() for col in data[0]]
            has_date = any('date' in h for h in header)
            has_amount = any('amount' in h or 'debit' in h or 'credit' in h for h in header)

            if has_date and has_amount:
                # This looks like a transaction table
                for row in data[1:]:  # Skip header
                    if len(row) >= 2:
                        txn = self._parse_transaction_row(row, header)
                        if txn:
                            transactions.append(txn)

        return transactions

    def _parse_transaction_row(self, row: List, header: List[str]) -> Dict[str, Any]:
        """Parse a single transaction row"""
        try:
            # Find column indices
            date_idx = next((i for i, h in enumerate(header) if 'date' in h), 0)
            desc_idx = next((i for i, h in enumerate(header) if 'description' in h or 'detail' in h), 1)
            amount_idx = next((i for i, h in enumerate(header) if 'amount' in h or 'debit' in h or 'credit' in h), -1)

            date_str = str(row[date_idx]) if date_idx < len(row) else ''
            description = str(row[desc_idx]) if desc_idx < len(row) else ''
            amount_str = str(row[amount_idx]) if amount_idx < len(row) else '0'

            # Parse amount
            amount_str = re.sub(r'[^\d\.\-]', '', amount_str)
            amount = float(amount_str) if amount_str and amount_str != '-' else 0.0

            # Determine transaction type
            txn_type = 'credit' if amount > 0 else 'debit'

            return {
                'date': date_str,
                'description': description.strip(),
                'amount': abs(amount),
                'type': txn_type
            }

        except Exception as e:
            logger.debug(f"Failed to parse transaction row: {e}")
            return None

    def _extract_transactions_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Extract transactions from plain text (fallback)"""
        transactions = []

        # Pattern: Date Description Amount
        patterns = [
            # Pattern 1: MM/DD/YYYY Description $Amount
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+([A-Za-z0-9\s\-\#\*\&\.\,]+?)\s+(-?\$?[\d,]+\.\d{2})',

            # Pattern 2: DD-MMM-YY Description Amount
            r'(\d{1,2}-[A-Z]{3}-\d{2})\s+([A-Za-z0-9\s\-\#\*\&\.\,]+?)\s+(-?\$?[\d,]+\.\d{2})',

            # Pattern 3: YYYY-MM-DD Description Amount
            r'(\d{4}-\d{1,2}-\d{1,2})\s+([A-Za-z0-9\s\-\#\*\&\.\,]+?)\s+(-?\$?[\d,]+\.\d{2})',
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text, re.MULTILINE)
            for match in matches:
                try:
                    date_str, description, amount_str = match.groups()

                    # Parse amount
                    amount_str = amount_str.replace('$', '').replace(',', '').strip()

                    # Check for negative sign or parentheses (debit)
                    is_negative = amount_str.startswith('-') or amount_str.startswith('(')
                    amount_str = amount_str.replace('-', '').replace('(', '').replace(')', '')

                    amount = float(amount_str)
                    if is_negative:
                        amount = -amount

                    transactions.append({
                        'date': date_str,
                        'description': description.strip(),
                        'amount': abs(amount),
                        'type': 'credit' if amount > 0 else 'debit'
                    })
                except Exception as e:
                    logger.debug(f"Failed to parse transaction: {e}")
                    continue

        return transactions

    def _calculate_total_credits(self, text: str, tables: List[Dict]) -> float:
        """Calculate total credits"""
        transactions = self._extract_transactions(text, tables)
        return sum(txn['amount'] for txn in transactions if txn['type'] == 'credit')

    def _calculate_total_debits(self, text: str, tables: List[Dict]) -> float:
        """Calculate total debits"""
        transactions = self._extract_transactions(text, tables)
        return sum(txn['amount'] for txn in transactions if txn['type'] == 'debit')

    def _extract_bank_name(self, text: str) -> str:
        """Extract bank name"""
        # Common banks
        banks = [
            'Chase', 'Bank of America', 'Wells Fargo', 'Citibank', 'Capital One',
            'US Bank', 'PNC Bank', 'TD Bank', 'Bank of the West', 'Fifth Third',
            'HSBC', 'Barclays', 'Standard Chartered'
        ]

        text_upper = text.upper()
        for bank in banks:
            if bank.upper() in text_upper:
                return bank

        return ''