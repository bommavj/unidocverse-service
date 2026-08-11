from typing import List, Dict, Any

class ResumeExtractor:
    """Extract contact info, experience, education, skills from resumes"""

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        return {
            'name': self._extract_name(text),
            'email': self._extract_email(text),
            'phone': self._extract_phone(text),
            'location': self._extract_location(text),
            'linkedin': self._extract_pattern(text, r'linkedin\.com/in/([^\s]+)'),
            'summary': self._extract_summary(text),
            'work_experience': self._extract_work_experience(text),
            'education': self._extract_education(text),
            'skills': self._extract_skills(text),
            'certifications': self._extract_certifications(text),
            'languages': self._extract_languages(text),
        }