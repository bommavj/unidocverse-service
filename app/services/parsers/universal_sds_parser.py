# app/services/parsers/universal_sds_parser.py

import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalSDSParser(BaseParser):
    """
    Heuristic parser for Safety Data Sheets (SDS / MSDS).
    Returns a dict with keys used by SafetyDataSheetAnalyzer:
      - chemical_name
      - cas_number
      - manufacturer
      - sections: [{section_id, title, text}]
      - hazards: list of hazard statements
      - ppe: list of PPE recommendations
      - exposure_limits: [{substance, limit, unit, basis}]
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["chemical_name"] = self._extract_chemical_name()
            parsed["cas_number"] = self._extract_cas_number()
            parsed["manufacturer"] = self._extract_manufacturer()
            parsed["sections"] = self._extract_sections()
            parsed["hazards"] = self._extract_hazards(parsed["sections"])
            parsed["ppe"] = self._extract_ppe(parsed["sections"])
            parsed["exposure_limits"] = self._extract_exposure_limits(parsed["sections"])
            return parsed
        except Exception:
            logger.exception("UniversalSDSParser.parse failed")
            return {
                "chemical_name": None,
                "cas_number": None,
                "manufacturer": None,
                "sections": [],
                "hazards": [],
                "ppe": [],
                "exposure_limits": [],
            }

    # ---------- top-level fields ----------

    def _extract_chemical_name(self) -> Optional[str]:
        # Common labels: Product name, Chemical name
        patterns = [
            r"Product\s+Name[:\s]*([A-Za-z0-9\-\s\(\)\/,\.]{3,200})",
            r"Chemical\s+Name[:\s]*([A-Za-z0-9\-\s\(\)\/,\.]{3,200})",
            r"Trade\s+Name[:\s]*([A-Za-z0-9\-\s\(\)\/,\.]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return v.strip()
        # fallback: first non-empty line (often product name)
        for ln in self.text.splitlines():
            ln = ln.strip()
            if ln and len(ln) < 120 and len(ln.split()) <= 8:
                # avoid picking headings like "SAFETY DATA SHEET"
                if re.search(r"safety data sheet|msds|material safety data", ln, flags=re.I):
                    continue
                return self.normalize_whitespace(ln)
        return None

    def _extract_cas_number(self) -> Optional[str]:
        # CAS format: 123-45-6
        m = re.search(r"\b(\d{1,7}-\d{2}-\d)\b", self.text)
        if m:
            return m.group(1)
        # sometimes labeled "CAS No." or "CAS#"
        patterns = [r"CAS\s*(?:No\.?|#)[:\s]*([\d\-]{5,15})", r"CAS\s*Number[:\s]*([\d\-]{5,15})"]
        v = self.find_first(patterns)
        if v:
            # sanitize
            m2 = re.search(r"(\d{1,7}-\d{2}-\d)", v)
            return m2.group(1) if m2 else v.strip()
        return None

    def _extract_manufacturer(self) -> Optional[str]:
        patterns = [
            r"Manufacturer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Supplier[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Company[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Produced by[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return v.strip()
        # fallback: top-of-doc lines
        top = "\n".join(self.text.splitlines()[:8])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,80})\n", top)
        if m:
            return m.group(1).strip()
        return None

    # ---------- sections extraction ----------

    def _extract_sections(self) -> List[Dict[str, Any]]:
        """
        SDS commonly uses numbered sections (1. Identification, 2. Hazard(s) identification, etc.)
        This method attempts to split the document into sections by numeric headings or by known section titles.
        Returns list of {section_id, title, text}
        """
        sections: List[Dict[str, Any]] = []

        # 1) Try numeric section headings like "1. Identification" or "1 Identification"
        numeric_pattern = re.compile(r"^\s*(\d{1,2})[.\)]\s*([A-Za-z][A-Za-z0-9 \-/&'()]{2,80})\s*$", flags=re.MULTILINE)
        matches = list(numeric_pattern.finditer(self.text))
        if matches:
            for idx, m in enumerate(matches):
                sec_id = m.group(1).strip()
                title = m.group(2).strip()
                start = m.end()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(self.text)
                body = self.normalize_whitespace(self.text[start:end])
                sections.append({"section_id": sec_id, "title": title, "text": body})
            return sections

        # 2) Try common SDS headings (case-insensitive)
        common_titles = [
            "Identification",
            "Hazard(s) identification",
            "Composition/information on ingredients",
            "First-aid measures",
            "Fire-fighting measures",
            "Accidental release measures",
            "Handling and storage",
            "Exposure controls/personal protection",
            "Physical and chemical properties",
            "Stability and reactivity",
            "Toxicological information",
            "Ecological information",
            "Disposal considerations",
            "Transport information",
            "Regulatory information",
            "Other information",
        ]
        # build regex to find any of these headings
        title_regex = r"^\s*(?:{})\b[:\s]*".format("|".join([re.escape(t) for t in common_titles]))
        # find all heading positions
        heading_iter = list(re.finditer(title_regex, self.text, flags=re.IGNORECASE | re.MULTILINE))
        if heading_iter:
            for idx, h in enumerate(heading_iter):
                title_raw = h.group(0).strip()
                # extract the matched title text (strip trailing colon)
                title = re.sub(r"[:\s]*$", "", title_raw, flags=re.I).strip()
                start = h.end()
                end = heading_iter[idx + 1].start() if idx + 1 < len(heading_iter) else len(self.text)
                body = self.normalize_whitespace(self.text[start:end])
                sections.append({"section_id": str(idx + 1), "title": title, "text": body})
            return sections

        # 3) Fallback: split by two or more newlines into blocks and attempt to label them heuristically
        blocks = [b.strip() for b in re.split(r"\n\s*\n", self.text) if b.strip()]
        for i, b in enumerate(blocks):
            # take first line as title candidate if short
            first_line = b.splitlines()[0].strip()
            title = first_line if len(first_line) < 80 and len(first_line.split()) <= 8 else f"Block {i+1}"
            sections.append({"section_id": str(i + 1), "title": title, "text": self.normalize_whitespace(b)})
        return sections

    # ---------- hazards extraction ----------

    def _extract_hazards(self, sections: List[Dict[str, Any]]) -> List[str]:
        hazards: List[str] = []

        # 1) Look for a section with "Hazard" in the title
        for s in sections:
            if re.search(r"hazard", s.get("title", ""), flags=re.I):
                # split into sentences or bullet lines
                parts = re.split(r"\n\s*[-•]\s+|\n|;\s+|\.\s+", s.get("text", ""))
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    # common hazard statement keywords
                    if re.search(r"hazard|danger|warning|harmful|toxic|flammable|corrosive|irritant", p, flags=re.I):
                        hazards.append(self.normalize_whitespace(p))
                if hazards:
                    return hazards

        # 2) Search whole doc for H-statements (H200, H300) or phrases like "Danger" / "Warning"
        hstatements = re.findall(r"\b(H\d{3}\b[:\s\-]*[A-Za-z0-9 ,\-\(\)]+)", self.text, flags=re.I)
        for h in hstatements:
            hazards.append(self.normalize_whitespace(h))
        if hazards:
            return hazards

        # 3) Look for lines starting with "Danger" or "Warning"
        for m in re.finditer(r"^(Danger|Warning)[:\s\-]*(.+)$", self.text, flags=re.I | re.MULTILINE):
            hazards.append(self.normalize_whitespace(m.group(0)))
        return hazards

    # ---------- PPE extraction ----------

    def _extract_ppe(self, sections: List[Dict[str, Any]]) -> List[str]:
        ppe_list: List[str] = []

        # 1) Look for "Exposure controls/personal protection" or similar section
        for s in sections:
            if re.search(r"exposure controls|personal protection|personal protective equipment|ppe", s.get("title", ""), flags=re.I):
                text = s.get("text", "")
                # common PPE items
                candidates = re.findall(r"(glove[s]?|safety glasses|goggles|face shield|respirator|apron|protective clothing|boots|mask|hearing protection)", text, flags=re.I)
                for c in candidates:
                    item = c.strip().lower()
                    if item not in ppe_list:
                        ppe_list.append(item)
                # also parse lines like "Wear: gloves, goggles, respirator"
                wear_match = re.search(r"(Wear|Use|Recommended)[:\s]*(.+)", text, flags=re.I)
                if wear_match:
                    parts = re.split(r",|\band\b|/|;", wear_match.group(2))
                    for p in parts:
                        p = p.strip().lower()
                        if p and p not in ppe_list:
                            ppe_list.append(p)
                if ppe_list:
                    return ppe_list

        # 2) Search whole doc for PPE keywords
        candidates = re.findall(r"(glove[s]?|safety glasses|goggles|face shield|respirator|apron|protective clothing|boots|mask|hearing protection)", self.text, flags=re.I)
        for c in candidates:
            item = c.strip().lower()
            if item not in ppe_list:
                ppe_list.append(item)
        return ppe_list

    # ---------- exposure limits extraction ----------

    def _extract_exposure_limits(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract exposure limits from 'Exposure controls' or 'Composition' sections.
        Returns list of {substance, limit, unit, basis}
        """
        limits: List[Dict[str, Any]] = []

        # helper to parse tokens like "OSHA PEL 8 hr TWA 50 ppm" or "TLV 5 mg/m3"
        def parse_limit_token(token: str) -> Optional[Dict[str, Any]]:
            token = token.strip()
            # common patterns: number + unit (ppm, mg/m3, mg/m^3)
            m = re.search(r"(\d+(?:\.\d+)?)\s*(ppm|mg/m3|mg/m\^3|mg/m³|µg/m3|ug/m3|mg/m3)", token, flags=re.I)
            if m:
                return {"limit": float(m.group(1)), "unit": m.group(2), "basis": token}
            # patterns with /m3 variants
            m2 = re.search(r"(\d+(?:\.\d+)?)\s*(mg|µg|ug)\s*/\s*m3", token, flags=re.I)
            if m2:
                return {"limit": float(m2.group(1)), "unit": m2.group(2) + "/m3", "basis": token}
            # ppm without number (rare)
            return None

        # 1) Look in exposure/controls section
        for s in sections:
            if re.search(r"exposure controls|exposure limits|occupational exposure|exposure", s.get("title", ""), flags=re.I):
                text = s.get("text", "")
                # find lines with "PEL", "TLV", "OEL", "TWA", "STEL"
                for line in re.split(r"\n", text):
                    if re.search(r"\b(PEL|TLV|OEL|TWA|STEL|Ceiling)\b", line, flags=re.I):
                        # try to extract substance name at start
                        parts = re.split(r":|-", line, maxsplit=1)
                        if len(parts) == 2:
                            substance = parts[0].strip()
                            token = parts[1].strip()
                        else:
                            # fallback: search for known tokens
                            substance = None
                            token = line
                        parsed = parse_limit_token(token)
                        if parsed:
                            limits.append({
                                "substance": substance or "unknown",
                                "limit": parsed["limit"],
                                "unit": parsed["unit"],
                                "basis": parsed["basis"],
                            })
                if limits:
                    return limits

        # 2) Search whole doc for PEL/TLV patterns
        for m in re.finditer(r"([A-Za-z0-9\-\s\/\(\)]+?)[:\s]{0,3}(?:PEL|TLV|OEL|TWA|STEL)[:\s\-]*([^\n;]{1,80})", self.text, flags=re.I):
            substance = m.group(1).strip()
            token = m.group(2).strip()
            parsed = parse_limit_token(token)
            if parsed:
                limits.append({
                    "substance": substance,
                    "limit": parsed["limit"],
                    "unit": parsed["unit"],
                    "basis": parsed["basis"],
                })
        if limits:
            return limits

        # 3) Fallback: look for numeric + unit patterns near chemical names
        for m in re.finditer(r"([A-Za-z0-9\-\s\/\(\)]+?)[:\s]{0,3}(\d+(?:\.\d+)?\s*(ppm|mg/m3|mg/m\^3|mg/m³))", self.text, flags=re.I):
            substance = m.group(1).strip()
            val = float(m.group(2).split()[0])
            unit = m.group(3)
            limits.append({"substance": substance, "limit": val, "unit": unit, "basis": m.group(2)})
        return limits

