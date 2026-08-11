# app/services/parsers/universal_policy_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalPolicyParser(BaseParser):
    """
    Heuristic parser for insurance policies (auto, home, renters, commercial, health, life, etc.).
    Returns a dict with keys commonly used by PolicyAnalyzer:
      - policy_number
      - policy_type
      - insured_name
      - insured_id
      - insurer_name
      - effective_date
      - expiration_date
      - premium
      - deductible
      - coverage_limits: list[{type, limit}]
      - endorsements: list[str]
      - exclusions: list[str]
      - agent_name
      - agent_contact
      - status
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["policy_number"] = self._extract_policy_number()
            parsed["policy_type"] = self._extract_policy_type()

            parsed["insured_name"] = self._extract_insured_name()
            parsed["insured_id"] = self._extract_insured_id()
            parsed["insurer_name"] = self._extract_insurer_name()

            parsed["effective_date"] = self._extract_effective_date()
            parsed["expiration_date"] = self._extract_expiration_date()

            parsed["premium"] = self._extract_premium()
            parsed["deductible"] = self._extract_deductible()

            parsed["coverage_limits"] = self._extract_coverage_limits()
            parsed["endorsements"] = self._extract_endorsements()
            parsed["exclusions"] = self._extract_exclusions()

            parsed["agent_name"] = self._extract_agent_name()
            parsed["agent_contact"] = self._extract_agent_contact()

            parsed["status"] = self._extract_status()

            return parsed

        except Exception:
            logger.exception("UniversalPolicyParser.parse failed")
            return {
                "policy_number": None,
                "policy_type": None,
                "insured_name": None,
                "insured_id": None,
                "insurer_name": None,
                "effective_date": None,
                "expiration_date": None,
                "premium": 0.0,
                "deductible": 0.0,
                "coverage_limits": [],
                "endorsements": [],
                "exclusions": [],
                "agent_name": None,
                "agent_contact": None,
                "status": None,
            }

    # ---------- identity ----------

    def _extract_policy_number(self) -> Optional[str]:
        v = self.find_first([
            r"Policy\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Certificate\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
        ])
        if v:
            return v
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_policy_type(self) -> Optional[str]:
        m = re.search(
            r"(auto|vehicle|homeowners?|renters?|property|commercial|liability|umbrella|health|medical|dental|vision|life|workers'? comp)",
            self.text,
            flags=re.IGNORECASE
        )
        return m.group(1).lower() if m else None

    def _extract_insured_name(self) -> Optional[str]:
        v = self.find_first([
            r"Insured[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
            r"Named\s+Insured[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
            r"Policyholder[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_insured_id(self) -> Optional[str]:
        return self.find_first([
            r"Insured\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ])

    def _extract_insurer_name(self) -> Optional[str]:
        v = self.find_first([
            r"Insurer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Issuer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Company[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ])
        if v:
            return self.normalize_whitespace(v)

        for ln in self.text.splitlines()[:15]:
            if re.search(r"\b(Insurance|Insurer|Assurance|Mutual|Indemnity|Underwriters)\b", ln, flags=re.IGNORECASE):
                return ln.strip()
        return None

    # ---------- dates ----------

    def _extract_effective_date(self) -> Optional[str]:
        v = self.find_first([
            r"Effective\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Coverage\s*Begins[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ])
        return self.parse_date(v) if v else None

    def _extract_expiration_date(self) -> Optional[str]:
        v = self.find_first([
            r"Expiration\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Coverage\s*Ends[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Policy\s*End[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ])
        return self.parse_date(v) if v else None

    # ---------- financials ----------

    def _extract_premium(self) -> float:
        v = self.find_first([
            r"Premium[:\s]*([\$\d,\.KkMm]+)",
            r"Total\s*Premium[:\s]*([\$\d,\.KkMm]+)",
        ])
        return self.safe_float(v) if v else 0.0

    def _extract_deductible(self) -> float:
        v = self.find_first([
            r"Deductible[:\s]*([\$\d,\.KkMm]+)",
        ])
        return self.safe_float(v) if v else 0.0

    # ---------- coverage ----------

    def _extract_coverage_limits(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        blocks = self.find_all_blocks(
            r"^\s*(Coverage|Coverage Limits|Limits of Liability)[:\s]*",
            end_regex=r"^\s*(Endorsements|Exclusions|Premium|Deductible|Policy)"
        )
        if not blocks:
            return items

        blk = blocks[0]
        row_re = re.compile(
            r"^(?P<type>[A-Za-z0-9 ,.'\-]{3,200})\s+(?P<limit>[\$\d,\.KkMm]+)",
            flags=re.MULTILINE
        )

        for m in row_re.finditer(blk):
            items.append({
                "type": self.normalize_whitespace(m.group("type")),
                "limit": self.safe_float(m.group("limit")),
            })

        return items

    # ---------- endorsements ----------

    def _extract_endorsements(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Endorsements|Riders|Amendments)[:\s]*",
            end_regex=r"^\s*(Exclusions|Coverage|Limits|Premium|Deductible)"
        )
        if not blocks:
            return []

        blk = blocks[0]
        parts = re.split(r"[\n•\-]+", blk)
        return [self.normalize_whitespace(p) for p in parts if len(p.strip()) > 3]

    # ---------- exclusions ----------

    def _extract_exclusions(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Exclusions|Not Covered|Exceptions)[:\s]*",
            end_regex=r"^\s*(Endorsements|Coverage|Limits|Premium|Deductible)"
        )
        if not blocks:
            return []

        blk = blocks[0]
        parts = re.split(r"[\n•\-]+", blk)
        return [self.normalize_whitespace(p) for p in parts if len(p.strip()) > 3]

    # ---------- agent ----------

    def _extract_agent_name(self) -> Optional[str]:
        v = self.find_first([
            r"Agent[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Broker[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_agent_contact(self) -> Optional[str]:
        m = re.search(r"(\+?\d[\d\-\(\) ]{7,}\d)", self.text)
        return m.group(1).strip() if m else None

    # ---------- status ----------

    def _extract_status(self) -> Optional[str]:
        m = re.search(r"(active|inactive|cancelled|expired|pending|bound)", self.text, flags=re.IGNORECASE)
        return m.group(1).lower() if m else None
