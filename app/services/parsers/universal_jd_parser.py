# app/services/parsers/universal_jd_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalJDParser(BaseParser):
    """
    Heuristic parser for Job Descriptions (JD).
    Returns a dict with keys commonly used by HR / Talent analyzers:
      - job_title
      - department
      - company
      - location
      - employment_type
      - experience_required
      - education_required
      - salary_range
      - responsibilities: list[str]
      - qualifications: list[str]
      - skills: list[str]
      - benefits: list[str]
      - posting_date
      - job_id
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["job_title"] = self._extract_job_title()
            parsed["department"] = self._extract_department()
            parsed["company"] = self._extract_company()
            parsed["location"] = self._extract_location()
            parsed["employment_type"] = self._extract_employment_type()
            parsed["experience_required"] = self._extract_experience()
            parsed["education_required"] = self._extract_education()
            parsed["salary_range"] = self._extract_salary()
            parsed["responsibilities"] = self._extract_responsibilities()
            parsed["qualifications"] = self._extract_qualifications()
            parsed["skills"] = self._extract_skills()
            parsed["benefits"] = self._extract_benefits()
            parsed["posting_date"] = self._extract_posting_date()
            parsed["job_id"] = self._extract_job_id()

            return parsed

        except Exception:
            logger.exception("UniversalJDParser.parse failed")
            return {
                "job_title": None,
                "department": None,
                "company": None,
                "location": None,
                "employment_type": None,
                "experience_required": None,
                "education_required": None,
                "salary_range": None,
                "responsibilities": [],
                "qualifications": [],
                "skills": [],
                "benefits": [],
                "posting_date": None,
                "job_id": None,
            }

    # ---------- top-level fields ----------

    def _extract_job_title(self) -> Optional[str]:
        patterns = [
            r"Job\s*Title[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"Position[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"Title[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: first prominent line
        top = self.text.splitlines()[:5]
        for ln in top:
            if re.match(r"^[A-Z][A-Za-z0-9 ,.&'/-]{3,200}$", ln.strip()):
                return ln.strip()
        return None

    def _extract_department(self) -> Optional[str]:
        v = self.find_first([
            r"Department[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"Team[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_company(self) -> Optional[str]:
        v = self.find_first([
            r"Company[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"About\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ])
        if v:
            return self.normalize_whitespace(v)

        # fallback: top lines with Inc/LLC
        for ln in self.text.splitlines()[:10]:
            if re.search(r"\b(Inc|LLC|Ltd|Corporation|Corp|Company)\b", ln):
                return ln.strip()
        return None

    def _extract_location(self) -> Optional[str]:
        v = self.find_first([
            r"Location[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"Based\s+in[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_employment_type(self) -> Optional[str]:
        v = self.find_first([
            r"Employment\s*Type[:\s]*([A-Za-z ]{3,40})",
            r"Job\s*Type[:\s]*([A-Za-z ]{3,40})",
        ])
        return v.strip() if v else None

    def _extract_experience(self) -> Optional[str]:
        v = self.find_first([
            r"Experience[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"([0-9]{1,2}\+?\s+years?\s+of\s+experience[^\n]{0,80})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_education(self) -> Optional[str]:
        v = self.find_first([
            r"Education[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"(Bachelor[^\n]{0,80})",
            r"(Master[^\n]{0,80})",
            r"(Ph\.?D[^\n]{0,80})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_salary(self) -> Optional[str]:
        v = self.find_first([
            r"Salary[:\s]*([A-Za-z0-9 \$,\-–—]{3,200})",
            r"Compensation[:\s]*([A-Za-z0-9 \$,\-–—]{3,200})",
            r"Pay\s*Range[:\s]*([A-Za-z0-9 \$,\-–—]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    # ---------- list extractors ----------

    def _extract_responsibilities(self) -> List[str]:
        return self._extract_bullets("Responsibilities|Duties|What You Will Do")

    def _extract_qualifications(self) -> List[str]:
        return self._extract_bullets("Qualifications|Requirements|What You Bring")

    def _extract_skills(self) -> List[str]:
        return self._extract_bullets("Skills|Technical Skills|Preferred Skills")

    def _extract_benefits(self) -> List[str]:
        return self._extract_bullets("Benefits|Perks|What We Offer")

    def _extract_bullets(self, heading_regex: str) -> List[str]:
        blocks = self.find_all_blocks(
            rf"^\s*(?:{heading_regex})[:\s]*",
            end_regex=r"^\s*(?:Responsibilities|Qualifications|Skills|Benefits|Education|Experience|Salary|Compensation|Location|Company|About)"
        )
        items: List[str] = []
        if blocks:
            blk = blocks[0]
            lines = re.split(r"\n|•|- |\* ", blk)
            for ln in lines:
                ln = ln.strip()
                if len(ln) > 3 and not re.match(rf"^{heading_regex}", ln, flags=re.IGNORECASE):
                    items.append(self.normalize_whitespace(ln))
        return items

    # ---------- misc ----------

    def _extract_posting_date(self) -> Optional[str]:
        v = self.find_first([
            r"Posted[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Posting\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ])
        if v:
            return self.parse_date(v)
        return None

    def _extract_job_id(self) -> Optional[str]:
        v = self.find_first([
            r"Job\s*(?:ID|Req|Requisition|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
        ])
        if v:
            return v
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None
