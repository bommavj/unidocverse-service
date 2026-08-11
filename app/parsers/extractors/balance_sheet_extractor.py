from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class BalanceSheetExtractor(BaseExtractor):
    """
    Extract financial data from Balance Sheets including:
    - Assets (Current & Non-Current)
    - Liabilities (Current & Long-Term)
    - Equity
    - Key financial ratios
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Extract basic metadata
        company_name = self._extract_company_name(text)
        report_date = self._extract_report_date(text)
        period = self._extract_period(text)
        currency = self._extract_currency_type(text)

        # Extract financial sections
        assets = self._extract_assets(text, tables)
        liabilities = self._extract_liabilities(text, tables)
        equity = self._extract_equity(text, tables)

        # Calculate totals and verify balance
        total_assets = assets.get('total_assets', 0)
        total_liabilities = liabilities.get('total_liabilities', 0)
        total_equity = equity.get('total_equity', 0)

        # Verify accounting equation: Assets = Liabilities + Equity
        is_balanced = self._verify_balance(total_assets, total_liabilities, total_equity)

        # Calculate key financial ratios
        ratios = self._calculate_ratios(assets, liabilities, equity)

        return {
            # Metadata
            'document_type': 'balance_sheet',
            'company_name': company_name,
            'report_date': report_date,
            'period': period,
            'currency': currency or 'USD',
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Financial Data
            'assets': assets,
            'liabilities': liabilities,
            'equity': equity,

            # Totals
            'total_assets': total_assets,
            'total_liabilities': total_liabilities,
            'total_equity': total_equity,

            # Validation
            'is_balanced': is_balanced,
            'balance_difference': abs(total_assets - (total_liabilities + total_equity)),

            # Financial Metrics
            'ratios': ratios,
            'working_capital': assets.get('current_assets', 0) - liabilities.get('current_liabilities', 0),

            # Raw data
            'tables_found': len(tables),
        }

    def _extract_company_name(self, text: str) -> Optional[str]:
        """Extract company name from balance sheet"""
        patterns = [
            r'(?:Balance Sheet|Statement of Financial Position)\s+(?:of|for)?\s*([A-Z][A-Za-z\s&.,Inc]+?)(?:\n|As of)',
            r'^([A-Z][A-Za-z\s&.,Inc]+?)\s+Balance Sheet',
            r'Company:\s*(.+?)(?:\n|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_report_date(self, text: str) -> Optional[str]:
        """Extract the date of the balance sheet"""
        patterns = [
            r'As of\s+(\w+\s+\d{1,2},?\s+\d{4})',
            r'(?:Date|Period Ended?):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'As at\s+(\d{1,2}\s+\w+\s+\d{4})',
            r'(?:December|January|February|March|April|May|June|July|August|September|October|November)\s+\d{1,2},?\s+\d{4}',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip() if match.lastindex else match.group(0).strip()
        return None

    def _extract_period(self, text: str) -> Optional[str]:
        """Extract fiscal period (Q1, Q2, Annual, etc.)"""
        patterns = [
            r'(Q[1-4]\s+\d{4})',
            r'(FY\s*\d{4})',
            r'(Fiscal Year\s+\d{4})',
            r'(Annual\s+Report\s+\d{4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_currency_type(self, text: str) -> Optional[str]:
        """Detect currency from text"""
        currency_patterns = {
            'USD': r'\$|USD|US\s*Dollar',
            'EUR': r'€|EUR|Euro',
            'GBP': r'£|GBP|Pound',
            'JPY': r'¥|JPY|Yen',
            'CAD': r'CAD|C\$',
            'AUD': r'AUD|A\$',
        }

        for currency, pattern in currency_patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                return currency
        return 'USD'  # Default

    def _extract_assets(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract all asset categories"""

        assets = {
            # Current Assets
            'cash_and_equivalents': self._extract_line_item(text, tables, [
                'Cash and Cash Equivalents',
                'Cash & Equivalents',
                'Cash',
            ]),
            'accounts_receivable': self._extract_line_item(text, tables, [
                'Accounts Receivable',
                'Trade Receivables',
                'Receivables',
            ]),
            'inventory': self._extract_line_item(text, tables, [
                'Inventory',
                'Inventories',
            ]),
            'marketable_securities': self._extract_line_item(text, tables, [
                'Marketable Securities',
                'Short-term Investments',
            ]),
            'prepaid_expenses': self._extract_line_item(text, tables, [
                'Prepaid Expenses',
                'Prepaid Assets',
            ]),
            'current_assets': self._extract_line_item(text, tables, [
                'Total Current Assets',
                'Current Assets',
            ]),

            # Non-Current Assets
            'property_plant_equipment': self._extract_line_item(text, tables, [
                'Property, Plant and Equipment',
                'Property, Plant & Equipment',
                'PP&E',
                'Fixed Assets',
            ]),
            'intangible_assets': self._extract_line_item(text, tables, [
                'Intangible Assets',
                'Goodwill and Intangible Assets',
            ]),
            'goodwill': self._extract_line_item(text, tables, [
                'Goodwill',
            ]),
            'long_term_investments': self._extract_line_item(text, tables, [
                'Long-term Investments',
                'Long Term Investments',
            ]),
            'deferred_tax_assets': self._extract_line_item(text, tables, [
                'Deferred Tax Assets',
            ]),
            'non_current_assets': self._extract_line_item(text, tables, [
                'Total Non-Current Assets',
                'Non-Current Assets',
                'Long-term Assets',
            ]),

            # Total
            'total_assets': self._extract_line_item(text, tables, [
                'Total Assets',
                'TOTAL ASSETS',
            ]),
        }

        # If total current assets not found, calculate it
        if not assets['current_assets']:
            assets['current_assets'] = sum(filter(None, [
                assets.get('cash_and_equivalents'),
                assets.get('accounts_receivable'),
                assets.get('inventory'),
                assets.get('marketable_securities'),
                assets.get('prepaid_expenses'),
            ]))

        return assets

    def _extract_liabilities(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract all liability categories"""

        liabilities = {
            # Current Liabilities
            'accounts_payable': self._extract_line_item(text, tables, [
                'Accounts Payable',
                'Trade Payables',
            ]),
            'short_term_debt': self._extract_line_item(text, tables, [
                'Short-term Debt',
                'Short Term Debt',
                'Current Portion of Long-term Debt',
            ]),
            'accrued_expenses': self._extract_line_item(text, tables, [
                'Accrued Expenses',
                'Accrued Liabilities',
            ]),
            'deferred_revenue': self._extract_line_item(text, tables, [
                'Deferred Revenue',
                'Unearned Revenue',
            ]),
            'current_liabilities': self._extract_line_item(text, tables, [
                'Total Current Liabilities',
                'Current Liabilities',
            ]),

            # Long-Term Liabilities
            'long_term_debt': self._extract_line_item(text, tables, [
                'Long-term Debt',
                'Long Term Debt',
            ]),
            'deferred_tax_liabilities': self._extract_line_item(text, tables, [
                'Deferred Tax Liabilities',
            ]),
            'pension_obligations': self._extract_line_item(text, tables, [
                'Pension Obligations',
                'Pension Liabilities',
            ]),
            'non_current_liabilities': self._extract_line_item(text, tables, [
                'Total Non-Current Liabilities',
                'Long-term Liabilities',
            ]),

            # Total
            'total_liabilities': self._extract_line_item(text, tables, [
                'Total Liabilities',
                'TOTAL LIABILITIES',
            ]),
        }

        return liabilities

    def _extract_equity(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract equity components"""

        equity = {
            'common_stock': self._extract_line_item(text, tables, [
                'Common Stock',
                'Share Capital',
            ]),
            'preferred_stock': self._extract_line_item(text, tables, [
                'Preferred Stock',
            ]),
            'additional_paid_in_capital': self._extract_line_item(text, tables, [
                'Additional Paid-in Capital',
                'Additional Paid in Capital',
                'APIC',
            ]),
            'retained_earnings': self._extract_line_item(text, tables, [
                'Retained Earnings',
                'Accumulated Earnings',
            ]),
            'treasury_stock': self._extract_line_item(text, tables, [
                'Treasury Stock',
            ]),
            'accumulated_other_comprehensive_income': self._extract_line_item(text, tables, [
                'Accumulated Other Comprehensive Income',
                'AOCI',
            ]),
            'total_equity': self._extract_line_item(text, tables, [
                'Total Equity',
                'Total Shareholders\' Equity',
                'Stockholders\' Equity',
                'TOTAL EQUITY',
            ]),
        }

        return equity

    def _extract_line_item(self, text: str, tables: List, search_terms: List[str]) -> Optional[float]:
        """
        Extract a specific line item value from text or tables
        Searches for multiple variations of the same term
        """

        # Try tables first (more structured)
        for table in tables:
            for row in table:
                if len(row) < 2:
                    continue

                label = str(row[0]).strip()

                # Check if label matches any search term
                for term in search_terms:
                    if term.lower() in label.lower():
                        # Try to extract number from second column
                        value = self._parse_financial_value(str(row[1]))
                        if value is not None:
                            return value

        # Fallback to text extraction
        for term in search_terms:
            # Pattern: "Line Item Name    $1,234,567" or "Line Item Name: 1,234,567"
            patterns = [
                rf'{re.escape(term)}\s*[:\s]+\$?\s*([\d,]+\.?\d*)',
                rf'{re.escape(term)}.*?\$\s*([\d,]+\.?\d*)',
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return self._parse_financial_value(match.group(1))

        return None

    def _parse_financial_value(self, value_str: str) -> Optional[float]:
        """Parse financial values, handling different formats"""
        if not value_str:
            return None

        # Clean the string
        cleaned = re.sub(r'[^\d,.\-()]+', '', str(value_str))

        # Handle parentheses as negative
        is_negative = '(' in value_str and ')' in value_str

        # Remove commas
        cleaned = cleaned.replace(',', '').replace('(', '').replace(')', '')

        try:
            value = float(cleaned)
            return -abs(value) if is_negative else value
        except ValueError:
            return None

    def _verify_balance(self, assets: float, liabilities: float, equity: float) -> bool:
        """Verify the accounting equation: Assets = Liabilities + Equity"""
        if not all([assets, liabilities, equity]):
            return False

        # Allow for small rounding differences (0.1%)
        difference = abs(assets - (liabilities + equity))
        tolerance = assets * 0.001

        return difference <= tolerance

    def _calculate_ratios(self, assets: Dict, liabilities: Dict, equity: Dict) -> Dict[str, Optional[float]]:
        """Calculate key financial ratios"""

        current_assets = assets.get('current_assets', 0)
        current_liabilities = liabilities.get('current_liabilities', 0)
        total_assets = assets.get('total_assets', 0)
        total_liabilities = liabilities.get('total_liabilities', 0)
        total_equity = equity.get('total_equity', 0)

        ratios = {}

        # Current Ratio
        if current_liabilities:
            ratios['current_ratio'] = round(current_assets / current_liabilities, 2)
        else:
            ratios['current_ratio'] = None

        # Quick Ratio (Acid Test)
        inventory = assets.get('inventory', 0)
        if current_liabilities:
            ratios['quick_ratio'] = round((current_assets - inventory) / current_liabilities, 2)
        else:
            ratios['quick_ratio'] = None

        # Debt-to-Equity Ratio
        if total_equity:
            ratios['debt_to_equity'] = round(total_liabilities / total_equity, 2)
        else:
            ratios['debt_to_equity'] = None

        # Debt-to-Assets Ratio
        if total_assets:
            ratios['debt_to_assets'] = round(total_liabilities / total_assets, 2)
        else:
            ratios['debt_to_assets'] = None

        # Equity Ratio
        if total_assets:
            ratios['equity_ratio'] = round(total_equity / total_assets, 2)
        else:
            ratios['equity_ratio'] = None

        return ratios


# Example usage in your pipeline:
"""
extractor = BalanceSheetExtractor()
result = extractor.extract(text, tables)

print(f"Company: {result['company_name']}")
print(f"Date: {result['report_date']}")
print(f"Total Assets: ${result['total_assets']:,.2f}")
print(f"Total Liabilities: ${result['total_liabilities']:,.2f}")
print(f"Total Equity: ${result['total_equity']:,.2f}")
print(f"Balanced: {result['is_balanced']}")
print(f"Current Ratio: {result['ratios']['current_ratio']}")
print(f"Debt-to-Equity: {result['ratios']['debt_to_equity']}")
"""