"""Invoice-specific data extraction"""

import logging
from typing import Dict, Any, List
import re

logger = logging.getLogger(__name__)


class InvoiceExtractor:
    """Extract structured data from invoices"""

    def extract(self, text: str, tables: List[Dict]) -> Dict[str, Any]:
        """Extract invoice-specific fields"""

        return {
            'invoice_number': self._extract_invoice_number(text),
            'invoice_date': self._extract_date(text, 'invoice date'),
            'due_date': self._extract_date(text, 'due date'),
            'vendor': self._extract_vendor(text),
            'total_amount': self._extract_total(text),
            'line_items': self._extract_line_items(tables),
            'tax': self._extract_tax(text),
            'payment_terms': self._extract_payment_terms(text),
        }

    def _extract_invoice_number(self, text: str) -> str:
        """Extract invoice number"""
        patterns = [
            r'invoice\s*#?\s*:?\s*([A-Z0-9\-]+)',
            r'inv\s*#?\s*:?\s*([A-Z0-9\-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _extract_date(self, text: str, field_name: str) -> str:
        """Extract date fields"""
        pattern = rf'{field_name}\s*:?\s*(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else ''

    def _extract_vendor(self, text: str) -> str:
        """Extract vendor name (usually first company name in doc)"""
        lines = text.split('\n')[:10]
        for line in lines:
            if len(line.strip()) > 3 and line.strip()[0].isupper():
                return line.strip()
        return ''

    def _extract_total(self, text: str) -> float:
        """Extract total amount"""
        patterns = [
            r'total\s*:?\s*\$?([\d,]+\.?\d*)',
            r'amount\s*due\s*:?\s*\$?([\d,]+\.?\d*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(',', '')
                try:
                    return float(amount_str)
                except:
                    pass

        return 0.0

    def _extract_line_items(self, tables: List[Dict]) -> List[Dict]:
        """Extract line items from tables"""
        if not tables:
            return []

        largest_table = max(tables, key=lambda t: t.get('rows', 0) * t.get('cols', 0))
        data = largest_table.get('data', [])

        if len(data) < 2:
            return []

        items = []
        for row in data[1:]:
            if len(row) >= 2:
                items.append({
                    'description': str(row[0]) if len(row) > 0 else '',
                    'quantity': str(row[1]) if len(row) > 1 else '',
                    'price': str(row[2]) if len(row) > 2 else '',
                    'total': str(row[3]) if len(row) > 3 else '',
                })

        return items

    def _extract_tax(self, text: str) -> float:
        """Extract tax amount"""
        pattern = r'tax\s*:?\s*\$?([\d,]+\.?\d*)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(',', ''))
            except:
                pass
        return 0.0

    def _extract_payment_terms(self, text: str) -> str:
        """Extract payment terms"""
        pattern = r'payment terms?\s*:?\s*([^\n]+)'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ''

# """Invoice-specific data extraction"""
#
# import logging
# from typing import Dict, Any, List
# import re
#
# logger = logging.getLogger(__name__)
#
#
# class InvoiceExtractor:
#     """Extract structured data from invoices"""
#
#     def extract(self, text: str, tables: List[Dict]) -> Dict[str, Any]:
#         """Extract invoice-specific fields"""
#
#         return {
#             'invoice_number': self._extract_invoice_number(text),
#             'invoice_date': self._extract_date(text, 'invoice date'),
#             'due_date': self._extract_date(text, 'due date'),
#             'vendor': self._extract_vendor(text),
#             'total_amount': self._extract_total(text),
#             'line_items': self._extract_line_items(tables),
#             'tax': self._extract_tax(text),
#             'payment_terms': self._extract_payment_terms(text),
#         }
#
#     def _extract_invoice_number(self, text: str) -> str:
#         """Extract invoice number"""
#         patterns = [
#             r'invoice\s*#?\s*:?\s*([A-Z0-9\-]+)',
#             r'inv\s*#?\s*:?\s*([A-Z0-9\-]+)',
#         ]
#
#         for pattern in patterns:
#             match = re.search(pattern, text, re.IGNORECASE)
#             if match:
#                 return match.group(1)
#
#         return ''
#
#     def _extract_date(self, text: str, field_name: str) -> str:
#         """Extract date fields"""
#         pattern = rf'{field_name}\s*:?\s*(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})'
#         match = re.search(pattern, text, re.IGNORECASE)
#         return match.group(1) if match else ''
#
#     def _extract_vendor(self, text: str) -> str:
#         """Extract vendor name (usually first company name in doc)"""
#         lines = text.split('\n')[:10]  # Check first 10 lines
#         for line in lines:
#             if len(line.strip()) > 3 and line.strip()[0].isupper():
#                 return line.strip()
#         return ''
#
#     def _extract_total(self, text: str) -> float:
#         """Extract total amount"""
#         patterns = [
#             r'total\s*:?\s*\$?([\d,]+\.?\d*)',
#             r'amount\s*due\s*:?\s*\$?([\d,]+\.?\d*)',
#         ]
#
#         for pattern in patterns:
#             match = re.search(pattern, text, re.IGNORECASE)
#             if match:
#                 amount_str = match.group(1).replace(',', '')
#                 try:
#                     return float(amount_str)
#                 except:
#                     pass
#
#         return 0.0
#
#     def _extract_line_items(self, tables: List[Dict]) -> List[Dict]:
#         """Extract line items from tables"""
#         if not tables:
#             return []
#
#         # Usually the largest table is the line items
#         largest_table = max(tables, key=lambda t: t.get('rows', 0) * t.get('cols', 0))
#
#         # Convert to line items
#         data = largest_table.get('data', [])
#         if len(data) < 2:  # Need header + at least one row
#             return []
#
#         headers = data[0]
#         items = []
#
#         for row in data[1:]:
#             if len(row) >= 2:
#                 items.append({
#                     'description': str(row[0]) if len(row) > 0 else '',
#                     'quantity': str(row[1]) if len(row) > 1 else '',
#                     'price': str(row[2]) if len(row) > 2 else '',
#                     'total': str(row[3]) if len(row) > 3 else '',
#                 })
#
#         return items
#
#     def _extract_tax(self, text: str) -> float:
#         """Extract tax amount"""
#         pattern = r'tax\s*:?\s*\$?([\d,]+\.?\d*)'
#         match = re.search(pattern, text, re.IGNORECASE)
#         if match:
#             try:
#                 return float(match.group(1).replace(',', ''))
#             except:
#                 pass
#         return 0.0
#
#     def _extract_payment_terms(self, text: str) -> str:
#         """Extract payment terms"""
#         pattern = r'payment terms?\s*:?\s*([^\n]+)'
#         match = re.search(pattern, text, re.IGNORECASE)
#         return match.group(1).strip() if match else ''