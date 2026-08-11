# app/services/parsers/universal_termination_parser.py

import logging
import re
from typing import Dict, Any, Optional, List

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalTerminationParser(BaseParser):
    """
    Heuristic parser for employment termination / separation letters and notices.
    Returns a dict with keys commonly used by HR / compliance analyzers:
      - employee_name
      - employee_id
      - employer_name
      - manager_name
      - termination_date
      - last_working_day
      - notice_date
      - termination_type          (e.g., "with cause", "without cause", "layoff", "resignation", "mutual")
      - reason_summary
      - severance_pay
      - severance_duration
      - benefits_continuation
      - cobra_eligibility
      - equity_treatment
      - return_of_property
      - non_compete
      - non_solicit
      - confidentiality_reaffirmed
      - next_steps
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["employee_name"] = self._extract_employee_name()
            parsed["employee_id"] = self._extract_employee_id()
            parsed["employer_name"] = self._extract_employer_name()
            parsed["manager_name"] = self._extract_manager_name()

            parsed["termination_date"] = self._extract_termination_date()
            parsed["last_working_day"] = self._extract_last_working_day()
            parsed["notice_date"] = self._extract_notice_date()

            parsed["termination_type"] = self._extract_termination_type()
            parsed["reason_summary"] = self._extract_reason_summary()

            parsed["severance_pay"] = self._extract_severance_pay()
            parsed["severance_duration"] = self._extract_severance_duration()
            parsed["benefits_continuation"] = self._extract_benefits_continuation()
            parsed["cobra_eligibility"] = self._extract_cobra_eligibility()
            parsed["equity_treatment"] = self._extract_equity_treatment()

            parsed["return_of_property"] = self._extract_return_of_property()
            parsed["non_compete"] = self._extract_non_compete()
            parsed["non_solicit"] = self._extract_non_solicit()
            parsed["confidentiality_reaffirmed"] = self._extract_confidentiality_reaffirmed()

            parsed["next_steps"] = self._extract_next_steps()

            return parsed

        except Exception:
            logger.exception("UniversalTerminationParser.parse failed")
            return {
                "employee_name": None,
                "employee_id": None,
                "employer_name": None,
                "manager_name": None,
                "termination_date": None,
                "last_working_day": None,
                "notice_date": None,
                "termination_type": None,
                "reason_summary": None,
                "severance_pay": None,
                "severance_duration": None,
                "benefits_continuation": None,
                "cobra_eligibility": None,
                "equity_treatment": None,
                "return_of_property": None,
                "non_compete": None,
                "non_solicit": None,
                "confidentiality_reaffirmed": None,
                "next_steps": None,
            }

    # ---------- identity ----------

    def _extract_employee_name(self) -> Optional[str]:
        patterns = [
            r"Dear\s+([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Employee\s*Name[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"This letter is to inform you,?\s+([A-Z][A-Za-z ,.'\-]{2,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: first prominent name near top
        for ln in self.text.splitlines()[:10]:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln.strip()):
                return ln.strip()
        return None

    def _extract_employee_id(self) -> Optional[str]:
        return self.find_first([
            r"Employee\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]{3,40})",
            r"Emp\s*ID[:\s]*([A-Za-z0-9\-_/]{3,40})",
        ])

    def _extract_employer_name(self) -> Optional[str]:
        patterns = [
            r"on behalf of\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"from\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})\s+regarding your employment",
            r"Company[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        for ln in self.text.splitlines()[:12]:
            if re.search(r"\b(Inc|LLC|Ltd|Corporation|Corp|Company)\b", ln):
                return ln.strip()
        return None

    def _extract_manager_name(self) -> Optional[str]:
        patterns = [
            r"Your\s+manager[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Reporting\s+Manager[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"you will no longer report to\s+([A-Z][A-Za-z ,.'\-]{2,120})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    # ---------- dates ----------

    def _extract_termination_date(self) -> Optional[str]:
        patterns = [
            r"effective\s+([A-Za-z0-9 ,\-\/]{6,40})\s*,?\s*your employment",
            r"Termination\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Your employment will end on\s+([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        return self.parse_date(v) if v else None

    def _extract_last_working_day(self) -> Optional[str]:
        patterns = [
            r"last\s+day\s+of\s+employment\s+will\s+be\s+([A-Za-z0-9 ,\-\/]{6,40})",
            r"Last\s*Working\s*Day[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.parse_date(v)
        # fallback: use termination date if explicit last day missing
        return self._extract_termination_date()

    def _extract_notice_date(self) -> Optional[str]:
        patterns = [
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Notice\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        return self.parse_date(v) if v else None

    # ---------- termination characterization ----------

    def _extract_termination_type(self) -> Optional[str]:
        text_lower = self.text.lower()

        if re.search(r"\bfor cause\b|\bwith cause\b", text_lower):
            return "with_cause"
        if re.search(r"\bwithout cause\b|\bnot for cause\b", text_lower):
            return "without_cause"
        if re.search(r"\blayoff\b|\breduction in force\b|\brif\b|\bposition eliminated\b", text_lower):
            return "layoff"
        if re.search(r"\bresignation\b|\byour resignation\b|\bvoluntarily resign\b", text_lower):
            return "resignation"
        if re.search(r"\bmutual\b\s+(termination|separation|agreement)", text_lower):
            return "mutual"
        if re.search(r"\bseparation\b", text_lower):
            return "separation"

        return None

    def _extract_reason_summary(self) -> Optional[str]:
        # Try to capture a short reason paragraph
        patterns = [
            r"because\s+([^.]{10,300})\.",
            r"due to\s+([^.]{10,300})\.",
            r"as a result of\s+([^.]{10,300})\.",
            r"reason\s+for\s+(?:this\s+)?(termination|separation)\s+is\s+([^.]{10,300})\.",
        ]
        for p in patterns:
            m = re.search(p, self.text, flags=re.IGNORECASE)
            if m:
                # last group is the reason text
                reason = m.groups()[-1]
                return self.normalize_whitespace(reason)
        return None

    # ---------- severance / benefits / equity ----------

    def _extract_severance_pay(self) -> Optional[str]:
        patterns = [
            r"Severance\s*Pay[:\s]*([A-Za-z0-9 \$,\-–—]{3,200})",
            r"severance\s+payment\s+of\s+([A-Za-z0-9 \$,\-–—]{3,200})",
            r"severance\s+in\s+the\s+amount\s+of\s+([A-Za-z0-9 \$,\-–—]{3,200})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_severance_duration(self) -> Optional[str]:
        patterns = [
            r"severance\s+equivalent\s+to\s+([A-Za-z0-9 ,\-]{3,80})\s+of\s+pay",
            r"severance\s+for\s+a\s+period\s+of\s+([A-Za-z0-9 ,\-]{3,80})",
        ]
        v = self.find_first(patterns)
        return self.normalize_whitespace(v) if v else None

    def _extract_benefits_continuation(self) -> Optional[str]:
        # capture paragraph mentioning continuation of benefits
        m = re.search(r"(benefits\s+will\s+continue[^\n\.]{0,300})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        if re.search(r"\bcontinuation of benefits\b", self.text, flags=re.IGNORECASE):
            return "benefits_continuation_mentioned"
        return None

    def _extract_cobra_eligibility(self) -> Optional[str]:
        if re.search(r"\bCOBRA\b", self.text, flags=re.IGNORECASE):
            if re.search(r"\beligible\b|\bmay elect\b", self.text, flags=re.IGNORECASE):
                return "eligible"
            return "mentioned"
        return None

    def _extract_equity_treatment(self) -> Optional[str]:
        patterns = [
            r"(unvested\s+equity[^\n\.]{0,300})",
            r"(stock\s+options?[^\n\.]{0,300})",
            r"(RSU[s]?[^\n\.]{0,300})",
        ]
        for p in patterns:
            m = re.search(p, self.text, flags=re.IGNORECASE)
            if m:
                return self.normalize_whitespace(m.group(1))
        return None

    # ---------- obligations / covenants ----------

    def _extract_return_of_property(self) -> Optional[str]:
        if re.search(r"\breturn\b[^\n]{0,80}\b(company|corporate|client)\s+property\b", self.text, flags=re.IGNORECASE):
            return "required"
        if re.search(r"\bcompany\s+property\b", self.text, flags=re.IGNORECASE):
            return "mentioned"
        return None

    def _extract_non_compete(self) -> Optional[str]:
        if re.search(r"\bnon[- ]compete\b", self.text, flags=re.IGNORECASE):
            return "mentioned"
        if re.search(r"\bcompetition\b[^\n]{0,80}\brestriction\b", self.text, flags=re.IGNORECASE):
            return "mentioned"
        return None

    def _extract_non_solicit(self) -> Optional[str]:
        if re.search(r"\bnon[- ]solicit\b|\bnon[- ]solicitation\b", self.text, flags=re.IGNORECASE):
            return "mentioned"
        if re.search(r"\bsolicit\b[^\n]{0,80}\bclients?\b", self.text, flags=re.IGNORECASE):
            return "mentioned"
        return None

    def _extract_confidentiality_reaffirmed(self) -> Optional[str]:
        if re.search(r"\bconfidentiality\b|\bconfidential information\b", self.text, flags=re.IGNORECASE):
            if re.search(r"\bcontinue\b|\bremain\b|\bstill\b\s+bound\b", self.text, flags=re.IGNORECASE):
                return "reaffirmed"
            return "mentioned"
        return None

    # ---------- next steps / instructions ----------

    def _extract_next_steps(self) -> Optional[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Next Steps|Instructions|Please Note|You should|You must)[:\s]*",
            end_regex=r"^\s*(Sincerely|Regards|Best|Thank you|Signature)"
        )
        if blocks:
            return self.normalize_whitespace(blocks[0][:2000])

        # fallback: last paragraph
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", self.text) if p.strip()]
        if paragraphs:
            return self.normalize_whitespace(paragraphs[-1][:2000])
        return None
