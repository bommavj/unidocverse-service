from typing import List, Dict, Any


class LeaseAgreementExtractor:
    """Extract lease terms, parties, property details"""

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        return {
            'landlord': self._extract_party(text, 'landlord'),
            'tenant': self._extract_party(text, 'tenant'),
            'property_address': self._extract_address(text),
            'lease_start': self._extract_date(text, r'(?:Lease Start|Commencement):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
            'lease_end': self._extract_date(text, r'(?:Lease End|Expiration):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
            'monthly_rent': self._extract_currency(text, r'Monthly Rent:?\s*\$?([\d,]+\.?\d*)'),
            'security_deposit': self._extract_currency(text, r'Security Deposit:?\s*\$?([\d,]+\.?\d*)'),
            'lease_term_months': self._extract_number(text, r'(?:Term|Duration):?\s*(\d+)\s*months?'),
            'utilities_included': self._extract_utilities(text),
            'pet_policy': self._extract_pet_policy(text),
        }