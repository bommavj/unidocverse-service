"""
Tax Form Extractor
Extracts structured data from tax forms
"""

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class TaxFormExtractor:
    """Extract structured data from tax forms"""

    def extract(self, text: str, tables: List[Dict]) -> Dict[str, Any]:
        """Extract tax form-specific fields"""

        return {
            'form_type': self._extract_form_type(text),
            'tax_year': self._extract_tax_year(text),
            'taxpayer_name': self._extract_taxpayer_name(text),
            'ssn_ein': self._extract_ssn_or_ein(text),
            'filing_status': self._extract_filing_status(text),
            'income': self._extract_income_fields(text),
            'deductions': self._extract_deductions(text),
            'credits': self._extract_credits(text),
            'total_tax': self._extract_amount(text, 'total tax'),
            'refund_amount': self._extract_amount(text, 'refund'),
            'amount_owed': self._extract_amount(text, 'amount owed'),
        }

    def _extract_form_type(self, text: str) -> str:
        """Extract tax form type (1040, W-2, 1099, etc.)"""
        patterns = [
            r'Form\s+(1040|W-2|1099|1120|990)(?:\s*[-A-Z]*)?',
            r'(1040|W-2|1099|1120|990)(?:\s*[-A-Z]*)?',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _extract_tax_year(self, text: str) -> str:
        """Extract tax year"""
        patterns = [
            r'tax\s*year\s*(\d{4})',
            r'for\s*calendar\s*year\s*(\d{4})',
            r'(\d{4})\s*tax\s*return',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _extract_taxpayer_name(self, text: str) -> str:
        """Extract taxpayer name"""
        patterns = [
            r'name\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            r'taxpayer\s*name\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        return ''

    def _extract_ssn_or_ein(self, text: str) -> str:
        """Extract SSN or EIN"""
        patterns = [
            r'SSN\s*:?\s*(\d{3}-\d{2}-\d{4})',
            r'EIN\s*:?\s*(\d{2}-\d{7})',
            r'(\d{3}-\d{2}-\d{4})',  # SSN format
            r'(\d{2}-\d{7})',  # EIN format
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)

        return ''

    def _extract_filing_status(self, text: str) -> str:
        """Extract filing status"""
        statuses = [
            'single',
            'married filing jointly',
            'married filing separately',
            'head of household',
            'qualifying widow',
        ]

        text_lower = text.lower()
        for status in statuses:
            if status in text_lower:
                return status.title()

        return ''

    def _extract_income_fields(self, text: str) -> Dict[str, float]:
        """Extract income-related fields"""
        income = {}

        fields = {
            'wages': [r'wages?\s*(?:and\s*salaries?)?\s*:?\s*\$?([\d,]+\.?\d*)'],
            'interest': [r'interest\s*income\s*:?\s*\$?([\d,]+\.?\d*)'],
            'dividends': [r'dividends?\s*:?\s*\$?([\d,]+\.?\d*)'],
            'capital_gains': [r'capital\s*gains?\s*:?\s*\$?([\d,]+\.?\d*)'],
            'total_income': [
                r'total\s*income\s*:?\s*\$?([\d,]+\.?\d*)',
                r'adjusted\s*gross\s*income\s*:?\s*\$?([\d,]+\.?\d*)',
            ],
        }

        for field, patterns in fields.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    income[field] = self._parse_amount(match.group(1))
                    break

        return income

    def _extract_deductions(self, text: str) -> Dict[str, float]:
        """Extract deduction amounts"""
        deductions = {}

        fields = {
            'standard_deduction': [r'standard\s*deduction\s*:?\s*\$?([\d,]+\.?\d*)'],
            'itemized_deductions': [r'itemized\s*deductions?\s*:?\s*\$?([\d,]+\.?\d*)'],
            'total_deductions': [r'total\s*deductions?\s*:?\s*\$?([\d,]+\.?\d*)'],
        }

        for field, patterns in fields.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    deductions[field] = self._parse_amount(match.group(1))
                    break

        return deductions

    def _extract_credits(self, text: str) -> Dict[str, float]:
        """Extract tax credits"""
        credits = {}

        fields = {
            'child_tax_credit': [r'child\s*tax\s*credit\s*:?\s*\$?([\d,]+\.?\d*)'],
            'earned_income_credit': [r'earned\s*income\s*credit\s*:?\s*\$?([\d,]+\.?\d*)'],
            'education_credit': [r'education\s*credit\s*:?\s*\$?([\d,]+\.?\d*)'],
        }

        for field, patterns in fields.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    credits[field] = self._parse_amount(match.group(1))
                    break

        return credits

    def _extract_amount(self, text: str, field_name: str) -> float:
        """Extract monetary amount for specific field"""
        pattern = rf'{field_name}\s*:?\s*\$?([\d,]+\.?\d*)'
        match = re.search(pattern, text, re.IGNORECASE)
        return self._parse_amount(match.group(1)) if match else 0.0

    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount string to float"""
        try:
            return float(amount_str.replace(',', '').replace('$', ''))
        except:
            return 0.0