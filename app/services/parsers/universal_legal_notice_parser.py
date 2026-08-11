# app/services/parsers/universal_legal_notice_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalLegalNoticeParser(BaseParser):
    """
    Heuristic parser for legal notices and filings.
    Returns a dict with keys used by LegalNoticeAnalyzer:
      - case_number
      - court
      - plaintiff
      - defendant
      - filing_date
      - service_deadline
      - jurisdiction
      - service_method
      - parties: list of {role, name, contact}
      - summary: short extracted summary or caption
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["case_number"] = self._extract_case_number()
            parsed["court"] = self._extract_court()
            parsed["plaintiff"], parsed["defendant"] = self._extract_parties()
            parsed["parties"] = self._extract_parties_structured()
            parsed["filing_date"] = self._extract_filing_date()
            parsed["service_deadline"] = self._extract_service_deadline()
            parsed["jurisdiction"] = self._extract_jurisdiction()
            parsed["service_method"] = self._extract_service_method()
            parsed["summary"] = self._extract_summary()
            return parsed
        except Exception:
            logger.exception("UniversalLegalNoticeParser.parse failed")
            return {
                "case_number": None,
                "court": None,
                "plaintiff": None,
                "defendant": None,
                "parties": [],
                "filing_date": None,
                "service_deadline": None,
                "jurisdiction": None,
                "service_method": None,
                "summary": None,
            }

    # ---------- field extractors ----------

    def _extract_case_number(self) -> Optional[str]:
        patterns = [
            r"\bCase\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/\.]+)",
            r"\bDocket\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/\.]+)",
            r"\bCase\s+No[:\s]*([A-Za-z0-9\-_/\.]+)",
            r"\bCiv\.\s*No\.[:\s]*([A-Za-z0-9\-_/\.]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: look for patterns like "No. 12345" near top
        m = re.search(r"\bNo\.\s*([A-Za-z0-9\-_/\.]{3,40})", self.text)
        if m:
            return m.group(1).strip()
        return None

    def _extract_court(self) -> Optional[str]:
        patterns = [
            r"^(?:In\s+the\s+)?(Superior Court of [A-Za-z ,\-]+)",
            r"^(?:In\s+the\s+)?(United States District Court for the [A-Za-z ,\-]+)",
            r"^(?:In\s+the\s+)?(Circuit Court of [A-Za-z ,\-]+)",
            r"^(?:In\s+the\s+)?(Court of [A-Za-z ,\-]+)",
            r"^(?:Before the)\s+([A-Za-z0-9 ,\-\&]+Court[^\n]*)",
        ]
        # search near top of document
        top = "\n".join(self.text.splitlines()[:12])
        for p in patterns:
            m = re.search(p, top, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                return self.normalize_whitespace(m.group(1))
        # fallback: look for "County of X" or "State of Y"
        m2 = re.search(r"(County of [A-Za-z \-]+|State of [A-Za-z \-]+)", top, flags=re.IGNORECASE)
        if m2:
            return m2.group(1).strip()
        return None

    def _extract_parties(self) -> (Optional[str], Optional[str]):
        # Try common caption formats: "Plaintiff v. Defendant" or "Plaintiff vs Defendant"
        m = re.search(r"^([A-Z][A-Za-z0-9 ,\.\'&\-]{2,200})\s+v(?:\.|s|ersus)?\s+([A-Z][A-Za-z0-9 ,\.\'&\-]{2,200})", self.text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            p = self.normalize_whitespace(m.group(1))
            d = self.normalize_whitespace(m.group(2))
            return p, d
        # try "Plaintiff: X" and "Defendant: Y"
        plaintiff = self.find_first([r"Plaintiff[:\s]*([A-Z][A-Za-z0-9 ,\.\'&\-]{2,200})"])
        defendant = self.find_first([r"Defendant[:\s]*([A-Z][A-Za-z0-9 ,\.\'&\-]{2,200})"])
        if plaintiff or defendant:
            return plaintiff, defendant
        # fallback: look for "Petitioner" and "Respondent"
        petitioner = self.find_first([r"Petitioner[:\s]*([A-Z][A-Za-z0-9 ,\.\'&\-]{2,200})"])
        respondent = self.find_first([r"Respondent[:\s]*([A-Z][A-Za-z0-9 ,\.\'&\-]{2,200})"])
        if petitioner or respondent:
            return petitioner, respondent
        return None, None

    def _extract_parties_structured(self) -> List[Dict[str, Optional[str]]]:
        parties: List[Dict[str, Optional[str]]] = []
        # capture labeled blocks like "Plaintiff: Name\nAddress..."
        blocks = self.find_all_blocks(r"^\s*(Plaintiff|Defendant|Petitioner|Respondent|Applicant|Respondent)[:\s]*", end_regex=r"^\s*(?:Plaintiff|Defendant|Petitioner|Respondent|Court|Case|Docket|Date|Subject)")
        if blocks:
            # find labels and corresponding first line
            iter_labels = re.finditer(r"^\s*(Plaintiff|Defendant|Petitioner|Respondent|Applicant|Respondent)[:\s]*", self.text, flags=re.IGNORECASE | re.MULTILINE)
            for idx, it in enumerate(iter_labels):
                start = it.end()
                # determine end
                next_match = re.search(r"^\s*(Plaintiff|Defendant|Petitioner|Respondent|Applicant|Respondent)[:\s]*", self.text[start:], flags=re.IGNORECASE | re.MULTILINE)
                end = start + (next_match.start() if next_match else len(self.text[start:]))
                block = self.text[start:end].strip()
                name_line = block.splitlines()[0].strip() if block else None
                parties.append({"role": it.group(1).lower(), "name": name_line, "contact": None})
            if parties:
                return parties
        # fallback: try to extract from caption line
        p, d = self._extract_parties()
        if p:
            parties.append({"role": "plaintiff", "name": p, "contact": None})
        if d:
            parties.append({"role": "defendant", "name": d, "contact": None})
        return parties

    def _extract_filing_date(self) -> Optional[str]:
        # look for "Filed on", "Filed:", "Date Filed"
        patterns = [
            r"Filed[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Filed on[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date Filed[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Filed\s+(\d{4}-\d{2}-\d{2})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: first date in document that is near top
        top = "\n".join(self.text.splitlines()[:40])
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", top)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
        return None

    def _extract_service_deadline(self) -> Optional[str]:
        # look for "Service must be completed by", "Deadline", "Return by", "Respond by"
        patterns = [
            r"Service\s+(?:must\s+be\s+completed\s+by|by)[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Respond\s+by[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Return\s+by[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Deadline[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: look for "within X days" relative to filing date
        m = re.search(r"within\s+(\d{1,3})\s+days", self.text, flags=re.IGNORECASE)
        if m:
            try:
                days = int(m.group(1))
                fd = self._extract_filing_date()
                if fd:
                    from datetime import datetime, timedelta
                    inv = datetime.fromisoformat(fd)
                    return (inv + timedelta(days=days)).date().isoformat()
            except Exception:
                pass
        return None

    def _extract_jurisdiction(self) -> Optional[str]:
        # look for "Jurisdiction:" or "Under the jurisdiction of"
        patterns = [
            r"Jurisdiction[:\s]*([A-Za-z0-9 ,\-\&]{3,120})",
            r"Under the jurisdiction of[:\s]*([A-Za-z0-9 ,\-\&]{3,120})",
            r"Venue[:\s]*([A-Za-z0-9 ,\-\&]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: infer from court
        court = self._extract_court()
        if court:
            return court
        return None

    def _extract_service_method(self) -> Optional[str]:
        # look for "served by", "service by", "personal service", "mail", "publication"
        patterns = [
            r"Served\s+by[:\s]*([A-Za-z ,\-]{3,80})",
            r"Service\s+by[:\s]*([A-Za-z ,\-]{3,80})",
            r"Personal\s+service",
            r"Service\s+via[:\s]*([A-Za-z ,\-]{3,80})",
            r"Service\s+of\s+process\s+by[:\s]*([A-Za-z ,\-]{3,80})",
        ]
        for p in patterns:
            m = re.search(p, self.text, flags=re.IGNORECASE)
            if m:
                # if pattern is a simple phrase like "Personal service", return normalized phrase
                if not m.groups():
                    return m.group(0).strip().lower()
                return self.normalize_whitespace(m.group(1))
        # look for keywords
        if re.search(r"\b(personal service|mail|registered mail|certified mail|publication|electronic service|email|fax)\b", self.text, flags=re.IGNORECASE):
            m = re.search(r"\b(personal service|mail|registered mail|certified mail|publication|electronic service|email|fax)\b", self.text, flags=re.IGNORECASE)
            if m:
                return m.group(1).lower()
        return None

    def _extract_summary(self) -> Optional[str]:
        # try to capture the caption or first paragraph that summarizes the notice
        # caption often appears near top: "In the matter of ..." or "Notice of ..."
        m = re.search(r"^(Notice of [A-Za-z0-9 ,\-\&']{5,200})", self.text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return self.normalize_whitespace(m.group(1))
        # capture first non-empty paragraph up to 300 chars
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", self.text) if p.strip()]
        if paragraphs:
            return self.normalize_whitespace(paragraphs[0])[:400]
        return None
