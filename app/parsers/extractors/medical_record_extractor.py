from typing import List, Dict, Any


class MedicalRecordExtractor:
    """Extract patient info, diagnoses, medications, vitals"""

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        return {
            'patient_name': self._extract_pattern(text, r'Patient:?\s*(.+)'),
            'patient_id': self._extract_pattern(text, r'(?:Patient ID|MRN):?\s*([A-Z0-9-]+)'),
            'dob': self._extract_date(text, r'(?:DOB|Date of Birth):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
            'visit_date': self._extract_date(text,
                                             r'(?:Visit Date|Date of Service):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'),
            'provider': self._extract_pattern(text, r'Provider:?\s*(.+?)(?:,|MD|DO|NP|PA)'),
            'diagnoses': self._extract_diagnoses(text),
            'medications': self._extract_medications(text),
            'vitals': self._extract_vitals(text, tables),
            'chief_complaint': self._extract_chief_complaint(text),
            'assessment': self._extract_assessment(text),
        }