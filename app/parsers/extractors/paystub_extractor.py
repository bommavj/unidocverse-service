from typing import List, Dict, Any


class PayStubExtractor:
    """Extract earnings, deductions, YTD totals from pay stubs"""

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        return {
            'employee_name': self._extract_pattern(text, r'Employee:?\s*(.+)'),
            'employer': self._extract_pattern(text, r'Employer:?\s*(.+)'),
            'pay_period': self._extract_date_range(text),
            'gross_pay': self._extract_currency(text, r'Gross Pay:?\s*\$?([\d,]+\.?\d*)'),
            'net_pay': self._extract_currency(text, r'Net Pay:?\s*\$?([\d,]+\.?\d*)'),
            'deductions': self._extract_deductions(text, tables),
            'ytd_earnings': self._extract_currency(text, r'YTD Gross:?\s*\$?([\d,]+\.?\d*)'),
            'pay_date': self._extract_date(text, r'Pay Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
        }