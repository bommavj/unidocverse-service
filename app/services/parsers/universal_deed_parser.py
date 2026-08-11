# app/services/parsers/universal_deed_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalDeedParser(BaseParser):
    """
    Heuristic parser for property deeds.
    Returns a dict with keys used by PropertyDeedAnalyzer:
      - deed_id
      - grantor
      - grantee
      - property_address
      - parcel_id
      - recording_date
      - legal_description
      - encumbrances: list of {type, description, amount (optional)}
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["deed_id"] = self._extract_deed_id()
            parsed["grantor"] = self._extract_grantor()
            parsed["grantee"] = self._extract_grantee()
            parsed["property_address"] = self._extract_property_address()
            parsed["parcel_id"] = self._extract_parcel_id()
            parsed["recording_date"] = self._extract_recording_date()
            parsed["legal_description"] = self._extract_legal_description()
            parsed["encumbrances"] = self._extract_encumbrances()
            return parsed
        except Exception:
            logger.exception("UniversalDeedParser.parse failed")
            return {
                "deed_id": None,
                "grantor": None,
                "grantee": None,
                "property_address": None,
                "parcel_id": None,
                "recording_date": None,
                "legal_description": None,
                "encumbrances": [],
            }

    # ---------- top-level extractors ----------

    def _extract_deed_id(self) -> Optional[str]:
        patterns = [
            r"Deed\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Instrument\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Recording\s*No[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: look for "Book X Page Y" pattern
        m = re.search(r"Book\s+(\d+)\s+Page\s+(\d+)", self.text, flags=re.IGNORECASE)
        if m:
            return f"Book{m.group(1)}-Page{m.group(2)}"
        # filename fallback
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_grantor(self) -> Optional[str]:
        patterns = [
            r"Grantor[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Seller[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"From[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # try to capture "This deed, made by X" phrasing
        m = re.search(r"(?:This\s+deed[, ]+made\s+by|made\s+this\s+day\s+by)\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_grantee(self) -> Optional[str]:
        patterns = [
            r"Grantee[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Buyer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        m = re.search(r"granted\s+to\s+([A-Z][A-Za-z0-9 ,.&'/-]{3,200})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_property_address(self) -> Optional[str]:
        # look for "Property Address", "Premises", or "located at"
        patterns = [
            r"Property\s+Address[:\s]*([A-Za-z0-9\.,#\-\s]{5,200})",
            r"Premises[:\s]*([A-Za-z0-9\.,#\-\s]{5,200})",
            r"located\s+at[:\s]*([A-Za-z0-9\.,#\-\s]{5,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v.split("\n")[0])
        # fallback: try to find an address-like line (street number + street)
        m = re.search(r"\d{1,5}\s+[A-Za-z0-9\.\- ]{3,120}\,\s*[A-Za-z ]{2,40}\s*\d{5}", self.text)
        if m:
            return m.group(0).strip()
        return None

    def _extract_parcel_id(self) -> Optional[str]:
        patterns = [
            r"Parcel\s*(?:ID|No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Assessor'?s\s*Parcel\s*Number[:\s]*([A-Za-z0-9\-_/]+)",
            r"APN[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: any long numeric token that looks like APN
        m = re.search(r"\b\d{3,12}\b", self.text)
        if m:
            return m.group(0)
        return None

    def _extract_recording_date(self) -> Optional[str]:
        patterns = [
            r"Recorded[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Recording\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Filed[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: first date in document
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
        return None

    def _extract_legal_description(self) -> Optional[str]:
        # capture block under "Legal Description" heading
        blocks = self.find_all_blocks(r"^\s*Legal\s+Description\b[:\s]*", end_regex=r"^\s*(?:Parcel|Encumbrances|Grantor|Grantee|Recording|Witness|Signature)")
        if blocks:
            # return first block, trimmed
            return blocks[0][:4000]  # limit length
        # fallback: look for "All that certain parcel" or "Beginning at"
        m = re.search(r"(All that certain[^\n]{0,800}|Beginning at[^\n]{0,800})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(0))
        # final fallback: try to extract the paragraph that contains "lot" and "block" or "section"
        m2 = re.search(r"([^\n]{0,400}\b(Lot|Block|Section|Subdivision|Tract)\b[^\n]{0,400})", self.text, flags=re.IGNORECASE)
        if m2:
            return self.normalize_whitespace(m2.group(0))
        return None

    def _extract_encumbrances(self) -> List[Dict[str, Any]]:
        encs: List[Dict[str, Any]] = []
        # 1) Look for an "Encumbrances" or "Liens" section
        blocks = self.find_all_blocks(r"^\s*(Encumbrances|Liens|Easements|Restrictions)\b[:\s]*", end_regex=r"^\s*(?:Grantor|Grantee|Recording|Signature|Witness|Legal Description)")
        if blocks:
            text_block = blocks[0]
            # split into lines or semicolon-separated items
            parts = re.split(r"\n\s*[-•]\s+|\n\s*\d+[.)]\s+|;\s+|\n", text_block)
            for p in parts:
                p = self.normalize_whitespace(p)
                if not p:
                    continue
                # try to extract type and amount if present
                typ = None
                amt = None
                # common encumbrance keywords
                if re.search(r"\blien\b", p, flags=re.IGNORECASE):
                    typ = "lien"
                elif re.search(r"\beasement\b", p, flags=re.IGNORECASE):
                    typ = "easement"
                elif re.search(r"\brestriction\b|\covenant\b", p, flags=re.IGNORECASE):
                    typ = "restriction"
                # amount
                m_amt = re.search(r"([\$]?[\d,\.KkMm]+)", p)
                if m_amt:
                    amt = self.safe_float(m_amt.group(1))
                encs.append({"type": typ or "encumbrance", "description": p[:1000], "amount": amt})
            if encs:
                return encs

        # 2) Try to find inline mentions like "subject to a mortgage in the amount of $X"
        inline_matches = re.findall(r"(mortgage|lien|easement|restriction|covenant)[^\.\n]{0,200}", self.text, flags=re.IGNORECASE)
        if inline_matches:
            # iterate sentences and capture those containing keywords
            sentences = re.split(r"[.\n]", self.text)
            for s in sentences:
                if any(k.lower() in s.lower() for k in ("mortgage", "lien", "easement", "restriction", "covenant")):
                    desc = self.normalize_whitespace(s)
                    m_amt = re.search(r"([\$]?[\d,\.KkMm]+)", s)
                    amt = self.safe_float(m_amt.group(1)) if m_amt else None
                    typ = None
                    if "mortgage" in s.lower() or "lien" in s.lower():
                        typ = "lien"
                    elif "easement" in s.lower():
                        typ = "easement"
                    elif "restriction" in s.lower() or "covenant" in s.lower():
                        typ = "restriction"
                    encs.append({"type": typ or "encumbrance", "description": desc[:1000], "amount": amt})
            if encs:
                return encs

        # 3) Table-like rows: Type | Description | Amount
        table_rows = re.findall(r"^([A-Za-z ]{3,40})\s*\|\s*([^\|]{5,200})\s*\|\s*([\$\d,\.KkMm]+)", self.text, flags=re.MULTILINE)
        if table_rows:
            for r in table_rows:
                typ = r[0].strip().lower()
                desc = self.normalize_whitespace(r[1])
                amt = self.safe_float(r[2])
                encs.append({"type": typ, "description": desc, "amount": amt})
            if encs:
                return encs

        # final fallback: return empty list
        return []

