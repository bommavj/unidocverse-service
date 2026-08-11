# app/services/parsers/universal_claim_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalClaimParser(BaseParser):
    """
    Heuristic parser for insurance claims (health, auto, property, liability, etc.).
    Returns a dict with keys commonly used by ClaimAnalyzer:
      - claim_id
      - claim_type
      - policy_number
      - insured_name
      - insured_id
      - claimant_name
      - claimant_id
      - date_of_loss
      - date_filed
      - loss_description
      - incident_location
      - amount_claimed
      - amount_approved
      - deductible
      - adjuster_name
      - adjuster_contact
      - status
      - line_items: list[{description, amount}]
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["claim_id"] = self._extract_claim_id()
            parsed["claim_type"] = self._extract_claim_type()
            parsed["policy_number"] = self._extract_policy_number()

            parsed["insured_name"] = self._extract_insured_name()
            parsed["insured_id"] = self._extract_insured_id()
            parsed["claimant_name"] = self._extract_claimant_name()
            parsed["claimant_id"] = self._extract_claimant_id()

            parsed["date_of_loss"] = self._extract_date_of_loss()
            parsed["date_filed"] = self._extract_date_filed()

            parsed["loss_description"] = self._extract_loss_description()
            parsed["incident_location"] = self._extract_incident_location()

            parsed["amount_claimed"] = self._extract_amount_claimed()
            parsed["amount_approved"] = self._extract_amount_approved()
            parsed["deductible"] = self._extract_deductible()

            parsed["adjuster_name"] = self._extract_adjuster_name()
            parsed["adjuster_contact"] = self._extract_adjuster_contact()

            parsed["status"] = self._extract_status()
            parsed["line_items"] = self._extract_line_items()

            return parsed

        except Exception:
            logger.exception("UniversalClaimParser.parse failed")
            return {
                "claim_id": None,
                "claim_type": None,
                "policy_number": None,
                "insured_name": None,
                "insured_id": None,
                "claimant_name": None,
                "claimant_id": None,
                "date_of_loss": None,
                "date_filed": None,
                "loss_description": None,
                "incident_location": None,
                "amount_claimed": 0.0,
                "amount_approved": 0.0,
                "deductible": 0.0,
                "adjuster_name": None,
                "adjuster_contact": None,
                "status": None,
                "line_items": [],
            }

    # ---------- identity ----------

    def _extract_claim_id(self) -> Optional[str]:
        v = self.find_first([
            r"Claim\s*(?:ID|No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Reference\s*(?:No\.?|#)[:\s]*([A-Za-z0-9\-_/]+)",
        ])
        if v:
            return v
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_claim_type(self) -> Optional[str]:
        m = re.search(r"(auto|vehicle|property|health|medical|dental|vision|liability|workers'? comp)", self.text, flags=re.IGNORECASE)
        return m.group(1).lower() if m else None

    def _extract_policy_number(self) -> Optional[str]:
        return self.find_first([
            r"Policy\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Policy[:\s]*([A-Za-z0-9\-_/]+)",
        ])

    def _extract_insured_name(self) -> Optional[str]:
        v = self.find_first([
            r"Insured[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
            r"Policyholder[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_insured_id(self) -> Optional[str]:
        return self.find_first([
            r"Insured\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ])

    def _extract_claimant_name(self) -> Optional[str]:
        v = self.find_first([
            r"Claimant[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
            r"Patient[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_claimant_id(self) -> Optional[str]:
        return self.find_first([
            r"Claimant\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Patient\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ])

    # ---------- dates ----------

    def _extract_date_of_loss(self) -> Optional[str]:
        v = self.find_first([
            r"Date\s*of\s*Loss[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Loss\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Incident\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ])
        return self.parse_date(v) if v else None

    def _extract_date_filed(self) -> Optional[str]:
        v = self.find_first([
            r"Date\s*Filed[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Filed[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ])
        return self.parse_date(v) if v else None

    # ---------- loss details ----------

    def _extract_loss_description(self) -> Optional[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Loss Description|Description of Loss|Incident Description)[:\s]*",
            end_regex=r"^\s*(Location|Amount|Claimant|Adjuster|Policy)"
        )
        if blocks:
            return self.normalize_whitespace(blocks[0][:2000])

        # fallback: first paragraph mentioning "loss" or "incident"
        m = re.search(r"(loss|incident)[^\n]{20,300}", self.text, flags=re.IGNORECASE)
        return self.normalize_whitespace(m.group(0)) if m else None

    def _extract_incident_location(self) -> Optional[str]:
        v = self.find_first([
            r"Location[:\s]*([A-Za-z0-9 ,.'\-]{3,200})",
            r"Incident\s*Location[:\s]*([A-Za-z0-9 ,.'\-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    # ---------- financials ----------

    def _extract_amount_claimed(self) -> float:
        v = self.find_first([
            r"Amount\s*Claimed[:\s]*([\$\d,\.KkMm]+)",
            r"Claim\s*Amount[:\s]*([\$\d,\.KkMm]+)",
        ])
        return self.safe_float(v) if v else 0.0

    def _extract_amount_approved(self) -> float:
        v = self.find_first([
            r"Amount\s*Approved[:\s]*([\$\d,\.KkMm]+)",
            r"Approved\s*Amount[:\s]*([\$\d,\.KkMm]+)",
        ])
        return self.safe_float(v) if v else 0.0

    def _extract_deductible(self) -> float:
        v = self.find_first([
            r"Deductible[:\s]*([\$\d,\.KkMm]+)",
        ])
        return self.safe_float(v) if v else 0.0

    # ---------- adjuster ----------

    def _extract_adjuster_name(self) -> Optional[str]:
        v = self.find_first([
            r"Adjuster[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
            r"Claims\s*Adjuster[:\s]*([A-Z][A-Za-z0-9 ,.'\-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    def _extract_adjuster_contact(self) -> Optional[str]:
        m = re.search(r"(\+?\d[\d\-\(\) ]{7,}\d)", self.text)
        return m.group(1).strip() if m else None

    # ---------- status ----------

    def _extract_status(self) -> Optional[str]:
        m = re.search(r"(approved|denied|pending|in review|closed|open)", self.text, flags=re.IGNORECASE)
        return m.group(1).lower() if m else None

    # ---------- line items ----------

    def _extract_line_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        blocks = self.find_all_blocks(
            r"^\s*(Line Items|Charges|Expenses|Services Rendered)[:\s]*",
            end_regex=r"^\s*(Totals|Amount|Adjuster|Policy|Claim)"
        )
        if not blocks:
            return items

        blk = blocks[0]
        row_re = re.compile(
            r"^(?P<desc>[A-Za-z0-9 ,.'\-]{3,200})\s+(?P<amt>[\$\d,\.KkMm]+)",
            flags=re.MULTILINE
        )

        for m in row_re.finditer(blk):
            desc = self.normalize_whitespace(m.group("desc"))
            amt = self.safe_float(m.group("amt"))
            items.append({"description": desc, "amount": round(amt, 2)})

        return items
