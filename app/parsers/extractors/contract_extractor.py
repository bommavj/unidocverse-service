"""
Contract Extractor
Extracts structured data from contracts
"""

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class ContractExtractor:
    """Extract structured data from contracts"""

    def extract(self, text: str, tables: List[Dict]) -> Dict[str, Any]:
        """Extract contract-specific fields"""

        return {
            'contract_title': self._extract_title(text),
            'parties': self._extract_parties(text),
            'effective_date': self._extract_date(text, 'effective'),
            'expiration_date': self._extract_date(text, 'expiration'),
            'term': self._extract_term(text),
            'payment_terms': self._extract_payment_terms(text),
            'termination_clause': self._extract_termination_clause(text),
            'governing_law': self._extract_governing_law(text),
            'signatures': self._extract_signatures(text),
            'key_obligations': self._extract_key_obligations(text),
        }

    def _extract_title(self, text: str) -> str:
        """Extract contract title"""
        lines = text.split('\n')
        for line in lines[:20]:  # Check first 20 lines
            line = line.strip()
            if 'agreement' in line.lower() or 'contract' in line.lower():
                return line
        return ''

    def _extract_parties(self, text: str) -> List[Dict[str, str]]:
        """Extract contracting parties"""
        parties = []

        # Pattern: "between X and Y" or "Party A: X"
        patterns = [
            r'between\s+([^,]+?)\s+(?:and|&)\s+([^,\n]+)',
            r'party\s+a\s*:?\s*([^\n]+)',
            r'party\s+b\s*:?\s*([^\n]+)',
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                for group in match.groups():
                    if group:
                        parties.append({'name': group.strip()})

        return parties[:2]  # Usually just 2 parties

    def _extract_date(self, text: str, date_type: str) -> str:
        """Extract specific date"""
        patterns = {
            'effective': [
                r'effective\s*date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'commencing\s*on\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            ],
            'expiration': [
                r'expiration\s*date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'expires?\s*on\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            ],
        }

        for pattern in patterns.get(date_type, []):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _extract_term(self, text: str) -> str:
        """Extract contract term/duration"""
        pattern = r'term\s*of\s*(\d+\s*(?:year|month|day)s?)'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else ''

    def _extract_payment_terms(self, text: str) -> str:
        """Extract payment terms"""
        # Find section with payment terms
        pattern = r'payment\s*terms?\s*:?\s*([^\n]{20,200})'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ''

    def _extract_termination_clause(self, text: str) -> str:
        """Extract termination conditions"""
        pattern = r'termination\s*[:.]?\s*([^\n]{20,300})'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ''

    def _extract_governing_law(self, text: str) -> str:
        """Extract governing law/jurisdiction"""
        pattern = r'governed\s*by\s*(?:the\s*)?laws?\s*of\s*([A-Za-z\s]+)'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ''

    def _extract_signatures(self, text: str) -> List[Dict[str, str]]:
        """Extract signature information"""
        signatures = []

        # Look for signature blocks
        pattern = r'(?:signed|signature)\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
        matches = re.finditer(pattern, text, re.IGNORECASE)

        for match in matches:
            signatures.append({'signer': match.group(1)})

        return signatures

    def _extract_key_obligations(self, text: str) -> List[str]:
        """Extract key obligations from contract"""
        obligations = []

        # Look for numbered or bulleted obligations
        patterns = [
            r'\d+\.\s*([^\n]+)',
            r'[-•]\s*([^\n]+)',
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text)
            for match in matches:
                obligation = match.group(1).strip()
                if len(obligation) > 20:  # Filter out short matches
                    obligations.append(obligation)

        return obligations[:10]  # Limit to 10 obligations