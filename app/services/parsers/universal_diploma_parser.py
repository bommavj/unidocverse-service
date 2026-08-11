# app/services/parsers/universal_diploma_parser.py

import logging
import re
from typing import Dict, Any, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalDiplomaParser(BaseParser):
    """
    Heuristic parser for diplomas and degree certificates.
    Returns a dict with keys used by DiplomaAnalyzer:
      - graduate_name
      - degree_type
      - institution
      - conferral_date
      - honors
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["graduate_name"] = self._extract_graduate_name()
            parsed["degree_type"] = self._extract_degree_type()
            parsed["institution"] = self._extract_institution()
            parsed["conferral_date"] = self._extract_conferral_date()
            parsed["honors"] = self._extract_honors()
            return parsed
        except Exception:
            logger.exception("UniversalDiplomaParser.parse failed")
            return {
                "graduate_name": None,
                "degree_type": None,
                "institution": None,
                "conferral_date": None,
                "honors": None,
            }

    # ---------- field extractors ----------

    def _extract_graduate_name(self) -> Optional[str]:
        # Common labels: "This certifies that", "awarded to", "has conferred upon"
        patterns = [
            r"(?:This certif(?:ies|y) that|Be it known that|Hereby certif(?:ies|y) that)[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"(?:awarded to|is awarded to|has conferred upon)[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"^([A-Z][A-Za-z ,.'\-]{3,120})\n(?:has been awarded|has been conferred|is hereby awarded)",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look for a prominent uppercase name near top (first 6 lines)
        top_lines = [ln.strip() for ln in self.text.splitlines()[:8] if ln.strip()]
        for ln in top_lines:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln):
                return self.normalize_whitespace(ln)
        return None

    def _extract_degree_type(self) -> Optional[str]:
        # Look for degree phrases: Bachelor of Science, B.S., Master of Arts, M.A., Doctor of Philosophy, Ph.D.
        patterns = [
            r"(Bachelor(?:'s)?\s+of\s+[A-Za-z]{2,20})",
            r"(Master(?:'s)?\s+of\s+[A-Za-z]{2,20})",
            r"(Doctor(?:ate| of Philosophy| of Medicine)|Doctor of Philosophy|Ph\.?D\.?)",
            r"\b(B\.?S\.?|B\.?A\.?|M\.?S\.?|M\.?A\.?|MBA|LLB|LLM|J\.?D\.?)\b",
            r"(Associate(?:'s)?\s+Degree)",
            r"(Diploma in [A-Za-z0-9 &,\-]{2,80})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: search for "degree of" or "awarded the degree"
        m = re.search(r"degree\s+of\s+([A-Za-z0-9 &,\-]{2,80})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_institution(self) -> Optional[str]:
        # Common labels: "University of X", "College", "Institute"
        patterns = [
            r"([A-Z][A-Za-z0-9 '&,\.-]{3,120}\sUniversity)",
            r"([A-Z][A-Za-z0-9 '&,\.-]{3,120}\sCollege)",
            r"([A-Z][A-Za-z0-9 '&,\.-]{3,120}\sInstitute)",
            r"(The\s+[A-Z][A-Za-z0-9 '&,\.-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: top-of-doc organization line
        top = "\n".join(self.text.splitlines()[:6])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            candidate = m.group(1).strip()
            # avoid returning a person's name as institution
            if len(candidate.split()) > 1 and any(k.lower() in candidate.lower() for k in ("university", "college", "institute", "school", "academy")):
                return candidate
        return None

    def _extract_conferral_date(self) -> Optional[str]:
        # Look for "Given this ___ day of Month, Year" or "Dated", "Conferral Date", "Date of conferral"
        patterns = [
            r"Given this\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
            r"Dated[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Conferral\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date of conferral[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"on this\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4})",
            r"(\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)",
            r"(\b\d{1,2}/\d{1,2}/\d{2,4}\b)",
            r"(\b\d{4}-\d{2}-\d{2}\b)",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: search entire doc for first date-like token
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
        return None

    def _extract_honors(self) -> Optional[str]:
        # Look for honors phrases: "with honors", "cum laude", "magna cum laude", "summa cum laude", "with distinction"
        patterns = [
            r"(summa\s+cum\s+laude)",
            r"(magna\s+cum\s+laude)",
            r"(cum\s+laude)",
            r"(with\s+honors)",
            r"(with\s+distinction)",
            r"(honors\s+in\s+[A-Za-z0-9 &,\-]{2,80})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: search near degree phrase for honors
        deg = self._extract_degree_type()
        if deg:
            # look within 120 chars after degree mention
            idx = self.text.lower().find(deg.lower())
            if idx >= 0:
                snippet = self.text[idx: idx + 200]
                m = re.search(r"(summa\s+cum\s+laude|magna\s+cum\s+laude|cum\s+laude|with\s+honors|with\s+distinction)", snippet, flags=re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        return None
