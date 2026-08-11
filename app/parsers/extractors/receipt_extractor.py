"""
Receipt Extractor
Extracts structured data from receipts
"""

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class ReceiptExtractor:
    """Extract structured data from receipts"""

    def extract(self, text: str, tables: List[Dict]) -> Dict[str, Any]:
        """Extract receipt-specific fields"""

        return {
            'merchant_name': self._extract_merchant_name(text),
            'merchant_address': self._extract_merchant_address(text),
            'receipt_number': self._extract_receipt_number(text),
            'date': self._extract_date(text),
            'time': self._extract_time(text),
            'items': self._extract_line_items(text, tables),
            'subtotal': self._extract_amount(text, 'subtotal'),
            'tax': self._extract_amount(text, 'tax'),
            'total': self._extract_amount(text, 'total'),
            'payment_method': self._extract_payment_method(text),
            'tip': self._extract_amount(text, 'tip'),
        }

    def _extract_merchant_name(self, text: str) -> str:
        """Extract merchant/store name (usually first line)"""
        lines = text.split('\n')
        for line in lines[:10]:  # Check first 10 lines
            line = line.strip()
            if len(line) > 3 and line[0].isupper():
                return line
        return ''

    def _extract_merchant_address(self, text: str) -> str:
        """Extract merchant address"""
        # Look for address patterns
        pattern = r'(\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln))'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else ''

    def _extract_receipt_number(self, text: str) -> str:
        """Extract receipt/transaction number"""
        patterns = [
            r'receipt\s*#?\s*:?\s*([A-Z0-9\-]{4,20})',
            r'transaction\s*#?\s*:?\s*([A-Z0-9\-]{4,20})',
            r'order\s*#?\s*:?\s*([A-Z0-9\-]{4,20})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _extract_date(self, text: str) -> str:
        """Extract transaction date"""
        patterns = [
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
            r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _extract_time(self, text: str) -> str:
        """Extract transaction time"""
        pattern = r'(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else ''

    def _extract_line_items(self, text: str, tables: List[Dict]) -> List[Dict[str, Any]]:
        """Extract purchased items"""
        items = []

        # Try tables first
        if tables:
            for table in tables:
                data = table.get('data', [])
                for row in data[1:]:  # Skip header
                    if len(row) >= 2:
                        items.append({
                            'name': str(row[0]),
                            'price': self._parse_amount(str(row[-1]))
                        })

        # Fallback to text parsing
        if not items:
            # Pattern: Item name followed by price
            pattern = r'([A-Za-z0-9\s\-]+?)\s+\$?([\d,]+\.\d{2})'
            matches = re.finditer(pattern, text)
            for match in matches:
                name, price = match.groups()
                items.append({
                    'name': name.strip(),
                    'price': float(price.replace(',', ''))
                })

        return items[:50]  # Limit to 50 items

    def _extract_amount(self, text: str, field_name: str) -> float:
        """Extract monetary amount for specific field"""
        patterns = {
            'subtotal': [r'subtotal\s*:?\s*\$?([\d,]+\.\d{2})'],
            'tax': [r'tax\s*:?\s*\$?([\d,]+\.\d{2})'],
            'total': [
                r'total\s*:?\s*\$?([\d,]+\.\d{2})',
                r'amount\s*due\s*:?\s*\$?([\d,]+\.\d{2})',
            ],
            'tip': [r'tip\s*:?\s*\$?([\d,]+\.\d{2})'],
        }

        for pattern in patterns.get(field_name, []):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._parse_amount(match.group(1))

        return 0.0

    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount string to float"""
        try:
            return float(amount_str.replace(',', '').replace('$', ''))
        except:
            return 0.0

    def _extract_payment_method(self, text: str) -> str:
        """Extract payment method"""
        patterns = [
            r'(visa|mastercard|amex|discover|cash|debit|credit)',
            r'card\s*ending\s*in\s*(\d{4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''