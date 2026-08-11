# app/services/parsers/universal_coi_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalCOIParser(BaseParser):
    """
    Heuristic parser for Certificates of Insurance (COI).
    Returns a dict with keys used by CertificateOfInsuranceAnalyzer:
      - policy_number
      - insured
      - certificate_holder
      - coverage_items: list of {type, limit, effective, expiration, insurer}
      - endorsements
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["policy_number"] = self._extract_policy_number()
            parsed["insured"] = self._extract_insured()
            parsed["certificate_holder"] = self._extract_certificate_holder()
            parsed["insurer"] = self._extract_insurer()
            parsed["coverage_items"] = self._extract_coverage_items()
            parsed["endorsements"] = self._extract_endorsements()
            return parsed
        except Exception:
            logger.exception("UniversalCOIParser.parse failed")
            return {
                "policy_number": None,
                "insured": None,
                "certificate_holder": None,
                "insurer": None,
                "coverage_items": [],
                "endorsements": [],
            }

    # ---------- field extractors ----------

    def _extract_policy_number(self) -> Optional[str]:
        patterns = [
            r"Policy\s*(?:Number|No\.?|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Policy\s*[:\s]*([A-Za-z0-9\-_/]+)",
            r"Policy\s*No[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: look for "Policy:" near top
        top = "\n".join(self.text.splitlines()[:20])
        m = re.search(r"Policy[:\s]*([A-Za-z0-9\-_/]+)", top, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    def _extract_insured(self) -> Optional[str]:
        patterns = [
            r"Named Insured[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Insured[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Insured Name[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: look for "Insured" heading block
        blocks = self.find_all_blocks(r"^\s*Insured\b[:\s]*", end_regex=r"^\s*(?:Certificate Holder|Producer|Insurer|Policy)")
        if blocks:
            return blocks[0].split("\n")[0][:200].strip()
        return None

    def _extract_certificate_holder(self) -> Optional[str]:
        patterns = [
            r"Certificate Holder[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Certificate Holder Name[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Holder[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # try to find "Certificate Holder" block
        blocks = self.find_all_blocks(r"^\s*Certificate Holder\b[:\s]*", end_regex=r"^\s*(?:Coverage|Producer|Insurer|Policy)")
        if blocks:
            return blocks[0].split("\n")[0][:200].strip()
        return None

    def _extract_insurer(self) -> Optional[str]:
        patterns = [
            r"Producer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Insurer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Insurance Company[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # top-of-doc fallback
        top = "\n".join(self.text.splitlines()[:6])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,80})\n", top)
        if m:
            return m.group(1).strip()
        return None

    # ---------- coverage extraction ----------

    def _extract_coverage_items(self) -> List[Dict[str, Any]]:
        """
        Attempt multiple strategies to extract coverage items:
         - Table-like rows (Type | Limit | Effective | Expiration)
         - Lines like "Type: Commercial General Liability  Limit: $1,000,000  Effective: 2024-01-01  Expiration: 2025-01-01"
         - Common COI labels (General Liability, Auto, Workers' Comp, Umbrella)
        """
        items: List[Dict[str, Any]] = []

        # 1) Table-like rows with pipes or multiple columns
        table_rows = re.findall(
            r"^([A-Za-z &'/-]{3,80})\s*\|\s*([\$\d,\.KkMm]+)\s*\|\s*([A-Za-z0-9 ,/-]{4,30})\s*\|\s*([A-Za-z0-9 ,/-]{4,30})",
            self.text,
            flags=re.MULTILINE,
        )
        if table_rows:
            for r in table_rows:
                typ = r[0].strip()
                limit = self.safe_float(r[1])
                eff = self.parse_date(r[2].strip())
                exp = self.parse_date(r[3].strip())
                items.append({"type": typ, "limit": limit, "effective": eff, "expiration": exp, "insurer": self._extract_insurer()})
            if items:
                return items

        # 2) Inline labeled lines
        inline_pattern = re.compile(
            r"(?P<type>[A-Za-z &'/-]{3,80})[:\s]+(?:Limit[:\s]*?(?P<limit>[\$\d,\.KkMm]+))?(?:.*?Effective[:\s]*?(?P<eff>[A-Za-z0-9 ,/-]{4,30}))?(?:.*?Expir(?:y|ation)[:\s]*?(?P<exp>[A-Za-z0-9 ,/-]{4,30}))?",
            flags=re.IGNORECASE,
        )
        for m in inline_pattern.finditer(self.text):
            typ = m.group("type").strip()
            limit_raw = m.group("limit")
            eff_raw = m.group("eff")
            exp_raw = m.group("exp")
            if typ and (limit_raw or eff_raw or exp_raw):
                limit = self.safe_float(limit_raw) if limit_raw else 0.0
                eff = self.parse_date(eff_raw) if eff_raw else None
                exp = self.parse_date(exp_raw) if exp_raw else None
                items.append({"type": typ, "limit": limit, "effective": eff, "expiration": exp, "insurer": self._extract_insurer()})
        if items:
            return items

        # 3) Look for common coverage headings and capture nearby lines
        coverage_headings = [
            r"Commercial General Liability",
            r"General Liability",
            r"Automobile Liability",
            r"Auto Liability",
            r"Workers'? Compensation",
            r"Employers'? Liability",
            r"Umbrella",
            r"Professional Liability",
            r"Errors and Omissions",
            r"Property",
        ]
        for h in coverage_headings:
            # find heading and capture following 1-3 lines
            pattern = rf"({h})[:\s]*\n(.{{0,300}})"
            m = re.search(pattern, self.text, flags=re.IGNORECASE)
            if m:
                typ = m.group(1).strip()
                snippet = self.normalize_whitespace(m.group(2))
                # try to extract limit and dates from snippet
                limit = self._find_money_in_text(snippet)
                eff = self._find_date_in_text(snippet)
                exp = self._find_date_in_text(snippet, after=True)
                items.append({"type": typ, "limit": limit, "effective": eff, "expiration": exp, "insurer": self._extract_insurer()})
        if items:
            return items

        # 4) Fallback: search for lines containing "Limit" and a coverage keyword
        lines = [ln.strip() for ln in self.text.splitlines() if ln.strip()]
        for ln in lines:
            if "limit" in ln.lower() and any(k.lower() in ln.lower() for k in ["liability", "auto", "workers", "umbrella", "property", "professional", "errors"]):
                typ = ln.split("Limit")[0].strip()[:80]
                limit = self._find_money_in_text(ln)
                eff = self._find_date_in_text(ln)
                exp = self._find_date_in_text(ln, after=True)
                items.append({"type": typ or "coverage", "limit": limit, "effective": eff, "expiration": exp, "insurer": self._extract_insurer()})
        return items

    # ---------- endorsements ----------

    def _extract_endorsements(self) -> List[str]:
        blocks = self.find_all_blocks(r"^\s*Endorsement[s]?\b[:\s]*", end_regex=r"^\s*(?:Remarks|Producer|Insurer|Signature|Authorized Representative)")
        if blocks:
            # split by semicolon or newline bullets
            parts = re.split(r"\n\s*[-•]\s+|;\s+|\n", blocks[0])
            return [self.normalize_whitespace(p) for p in parts if p.strip()]
        # try to find "Additional Insured" lines
        ai = re.findall(r"(Additional Insured[:\s]*[A-Za-z0-9 ,.&'/-]{3,200})", self.text, flags=re.IGNORECASE)
        return [a.strip() for a in ai] if ai else []

    # ---------- small helpers ----------

    def _find_money_in_text(self, text: str) -> float:
        # find first money-like token ($1,000,000 or 1,000,000 or 1M)
        m = re.search(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", text)
        if m:
            return self.safe_float(m.group(1))
        return 0.0

    def _find_date_in_text(self, text: Optional[str], after: bool = False) -> Optional[str]:
        """
        If text is provided, try to find a date-like substring and parse it.
        If text is None, search the whole document; 'after' toggles searching later dates.
        """
        search_area = text or self.text
        # common date patterns
        date_patterns = [
            r"\b(\d{4}-\d{2}-\d{2})\b",
            r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
            r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
            r"\b([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})\b",
        ]
        for p in date_patterns:
            m = re.search(p, search_area)
            if m:
                parsed = self.parse_date(m.group(1))
                if parsed:
                    return parsed
        # if searching whole doc and after=True, try to find a second date after the first
        if not text and after:
            all_dates = re.findall(r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", self.text)
            if len(all_dates) >= 2:
                return self.parse_date(all_dates[1])
        return None
