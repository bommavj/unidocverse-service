import re
from typing import Optional


class BaseExtractor:
    """Base class with common extraction methods"""

    def _extract_pattern(self, text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _extract_currency(self, text: str, pattern: str) -> Optional[float]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = match.group(1).replace(',', '')
            return float(amount)
        return None

    def _extract_date(self, text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_email(self, text: str) -> Optional[str]:
        match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        return match.group(0) if match else None

    def _extract_phone(self, text: str) -> Optional[str]:
        match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
        return match.group(0) if match else None

    def _extract_masked_account(self, text: str) -> Optional[str]:
        """Extract account numbers like XXXX-XXXX-XXXX-1234"""
        match = re.search(r'[X*]{4}[-\s]?[X*]{4}[-\s]?[X*]{4}[-\s]?(\d{4})', text)
        return match.group(0) if match else None