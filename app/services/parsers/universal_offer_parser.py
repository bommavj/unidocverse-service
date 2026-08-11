# app/services/parsers/universal_offer_parser.py

import logging
import re
from typing import Dict, Any, Optional, List

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalOfferParser(BaseParser):
    """
    Heuristic parser for employment offer letters.
    Returns a dict with keys commonly used by HR / onboarding analyzers:
      - candidate_name
      - job_title
      - company
      - start_date
      - location
      - employment_type
      - salary
      - bonus
      - equity
      - benefits: list[str]
      - manager
      - offer_date
      - job_id
      - contingencies: list[str]
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["candidate_name"] = self._extract_candidate_name()
            parsed["job_title"] = self._extract_job_title()
            parsed["company"] = self._extract_company()
            parsed["start_date"] = self._extract_start_date()
            parsed["location"] = self._extract_location()
            parsed["employment_type"] = self._extract_employment_type()
            parsed["salary"] = self._extract_salary()
            parsed["bonus"] = self._extract_bonus()
            parsed["equity"] = self._extract_equity()
            parsed["benefits"] = self._extract_benefits()
            parsed["manager"] = self._extract_manager()
            parsed["offer_date"] = self._extract_offer_date()
            parsed["job_id"] = self._extract_job_id()
            parsed["contingencies"] = self._extract_contingencies()

            return parsed

        except Exception:
            logger.exception("UniversalOfferParser.parse failed")
            return {
                "candidate_name": None,
                "job_title": None,
                "company": None,
                "start_date": None,
                "location": None,
                "employment_type": None,
                "salary": None,
                "bonus": None,
                "equity": None,
                "benefits": [],
                "manager": None,
                "offer_date": None,
                "job_id": None,
                "contingencies": [],
            }

    # ---------- top-level fields ----------

    def _extract_candidate_name(self) -> Optional[str]:
        patterns = [
            r"Dear\s+([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Candidate[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Offered\s+to[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: first prominent uppercase name near top
        for ln in self.text.splitlines()[:10]:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln.strip()):
                return ln.strip()
        return None

    def _extract_job_title(self) -> Optional[str]:
        patterns = [
            r"Position[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"Job\s*Title[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"role\s+as\s+([A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_company(self) -> Optional[str]:
        patterns = [
            r"Company[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"on\s+behalf\s+of\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"at\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})\s+we\s+are\s+pleased",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: look for Inc/LLC in top lines
        for ln in self.text.splitlines()[:12]:
            if re.search(r"\b(Inc|LLC|Ltd|Corporation|Corp|Company)\b", ln):
                return ln.strip()
        return None

    def _extract_start_date(self) -> Optional[str]:
        patterns = [
            r"Start\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"commence\s+employment\s+on\s+([A-Za-z0-9 ,\-\/]{6,40})",
            r"begin\s+on\s+([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        return self.parse_date(v) if v else None

    def _extract_location(self) -> Optional[str]:
        patterns = [
            r"Location[:\s]*([A-Za-z0-9 ,.&'/-]{3,200})",
            r"based\s+in\s+([A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_employment_type(self) -> Optional[str]:
        patterns = [
            r"Employment\s*Type[:\s]*([A-Za-z ]{3,40})",
            r"(full[- ]time|part[- ]time|contract|temporary|internship)",
        ]
        v = self.find_first(patterns)
        return v.strip() if v else None

    def _extract_salary(self) -> Optional[str]:
        patterns = [
            r"Salary[:\s]*([A-Za-z0-9 \$,\-–—]{3,200})",
            r"base\s+salary\s+of\s+([A-Za-z0-9 \$,\-–—]{3,200})",
            r"compensation\s+of\s+([A-Za-z0-9 \$,\-–—]{3,200})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_bonus(self) -> Optional[str]:
        patterns = [
            r"Bonus[:\s]*([A-Za-z0-9 \$,\-–—]{3,200})",
            r"eligible\s+for\s+an?\s+annual\s+bonus\s+of\s+([A-Za-z0-9 \$,\-–—]{3,200})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_equity(self) -> Optional[str]:
        patterns = [
            r"Equity[:\s]*([A-Za-z0-9 \$,\-–—%]{3,200})",
            r"stock\s+options?\s+of\s+([A-Za-z0-9 \$,\-–—%]{3,200})",
            r"RSU[s]?\s+grant\s+of\s+([A-Za-z0-9 \$,\-–—%]{3,200})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_benefits(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Benefits|Perks|What We Offer)[:\s]*",
            end_regex=r"^\s*(Compensation|Salary|Bonus|Equity|Start Date|Location|Manager|Contingencies|Conditions)"
        )
        items: List[str] = []
        if blocks:
            blk = blocks[0]
            lines = re.split(r"\n|•|- |\* ", blk)
            for ln in lines:
                ln = ln.strip()
                if len(ln) > 3 and not re.match(r"^(Benefits|Perks)", ln, flags=re.IGNORECASE):
                    items.append(self.normalize_whitespace(ln))
        return items

    def _extract_manager(self) -> Optional[str]:
        patterns = [
            r"report\s+to\s+([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Manager[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_offer_date(self) -> Optional[str]:
        patterns = [
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Offer\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        return self.parse_date(v) if v else None

    def _extract_job_id(self) -> Optional[str]:
        v = self.find_first([
            r"Job\s*(?:ID|Req|Requisition|Number|#)[:\s]*([A-Za-z0-9\-_/]+)"
        ])
        if v:
            return v

        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]

        return None

    def _extract_contingencies(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Contingencies|Conditions|Subject to)[:\s]*",
            end_regex=r"^\s*(Benefits|Salary|Bonus|Equity|Start Date|Manager)"
        )
        items: List[str] = []
        if blocks:
            blk = blocks[0]
            lines = re.split(r"\n|•|- |\* ", blk)
            for ln in lines:
                ln = ln.strip()
                if len(ln) > 3 and not re.match(r"^(Contingencies|Conditions)", ln, flags=re.IGNORECASE):
                    items.append(self.normalize_whitespace(ln))
        return items
