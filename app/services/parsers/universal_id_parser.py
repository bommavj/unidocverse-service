# app/services/parsers/universal_id_parser.py

import logging
import re
from typing import Dict, Any, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalIDParser(BaseParser):
    """
    Heuristic parser for identification documents (passport, driver's license, national ID).
    Returns a dict with keys commonly used by IdentificationAnalyzer:
      - id_number
      - name
      - date_of_birth
      - expiry_date
      - nationality
      - document_type (passport, driver_license, id_card)
      - issuing_authority
      - photo_present (bool)
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["id_number"] = self._extract_id_number()
            parsed["name"] = self._extract_name()
            parsed["date_of_birth"] = self._extract_date_of_birth()
            parsed["expiry_date"] = self._extract_expiry_date()
            parsed["nationality"] = self._extract_nationality()
            parsed["document_type"] = self._extract_document_type()
            parsed["issuing_authority"] = self._extract_issuing_authority()
            parsed["photo_present"] = self._detect_photo()
            return parsed
        except Exception:
            logger.exception("UniversalIDParser.parse failed")
            return {
                "id_number": None,
                "name": None,
                "date_of_birth": None,
                "expiry_date": None,
                "nationality": None,
                "document_type": None,
                "issuing_authority": None,
                "photo_present": False,
            }

    # ---------- field extractors ----------

    def _extract_id_number(self) -> Optional[str]:
        # common labels: ID No, ID Number, Passport No, Passport Number, License No
        patterns = [
            r"\b(?:ID|ID\s*No\.?|ID\s*Number|Identification\s*No\.?|Identification\s*Number)[:\s]*([A-Z0-9\-]{4,30})\b",
            r"\b(?:Passport|Passport\s*No\.?|Passport\s*Number)[:\s]*([A-Z0-9\-]{4,30})\b",
            r"\b(?:License|Driver'?s\s+License|DL|DL\s*No\.?)[:\s]*([A-Z0-9\-]{4,30})\b",
            r"\b([A-Z0-9]{6,20}-?[A-Z0-9]{0,6})\b",  # fallback generic token
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return v.strip()
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_name(self) -> Optional[str]:
        # look for common phrases: "Name:", "Surname", "Given name", "Family name", "Holder"
        patterns = [
            r"\bName[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"\bSurname[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"\bGiven\s+Name[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"\bHolder[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: try MRZ (passport) extraction (two lines of 44 chars)
        mrz = self._extract_mrz()
        if mrz and mrz.get("names"):
            return mrz["names"]
        # fallback: top-of-doc prominent line
        top_lines = [ln.strip() for ln in self.text.splitlines()[:12] if ln.strip()]
        for ln in top_lines:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln):
                return self.normalize_whitespace(ln)
        return None

    def _extract_date_of_birth(self) -> Optional[str]:
        # common labels: DOB, Date of Birth, Birth Date
        patterns = [
            r"\bDOB[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"\bDate\s+of\s+Birth[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"\bBirth\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # MRZ fallback
        mrz = self._extract_mrz()
        if mrz and mrz.get("dob"):
            return mrz["dob"]
        # try to find any date that looks like a birth date (year between 1900 and 2010)
        m = re.search(r"\b(0?[1-9]|1[0-2])[\/\-](0?[1-9]|[12][0-9]|3[01])[\/\-](19|20)\d{2}\b", self.text)
        if m:
            parsed = self.parse_date(m.group(0))
            if parsed:
                return parsed
        return None

    def _extract_expiry_date(self) -> Optional[str]:
        patterns = [
            r"\bExpiry[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"\bExpires[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"\bExpiration\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"\bValid\s+Until[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # MRZ fallback
        mrz = self._extract_mrz()
        if mrz and mrz.get("expiry"):
            return mrz["expiry"]
        # try to find a date after keywords "valid until" or "expires"
        m = re.search(r"(?:valid until|expires|expiry|expiration)[:\s]*([A-Za-z0-9 ,\-\/]{6,40})", self.text, flags=re.IGNORECASE)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
        return None

    def _extract_nationality(self) -> Optional[str]:
        patterns = [
            r"\bNationality[:\s]*([A-Z][A-Za-z \-]{2,80})",
            r"\bCountry\s+of\s+Nationality[:\s]*([A-Z][A-Za-z \-]{2,80})",
            r"\bCountry[:\s]*([A-Z][A-Za-z \-]{2,80})",
        ]
        v = self.find_first(patterns)
        if v:
            return v.strip()
        # MRZ fallback (three-letter country code)
        mrz = self._extract_mrz()
        if mrz and mrz.get("nationality"):
            return mrz["nationality"]
        # try to find ISO country codes near top
        m = re.search(r"\b([A-Z]{3})\b", self.text.splitlines()[0] if self.text else "")
        if m:
            return m.group(1)
        return None

    def _extract_document_type(self) -> Optional[str]:
        # detect passport, driver's license, national id
        if re.search(r"\bPassport\b", self.text, flags=re.IGNORECASE):
            return "passport"
        if re.search(r"\bDriver'?s\s+License\b|\bDL\b", self.text, flags=re.IGNORECASE):
            return "driver_license"
        if re.search(r"\bIdentity\s+Card\b|\bID\s+Card\b|\bNational\s+ID\b", self.text, flags=re.IGNORECASE):
            return "id_card"
        # MRZ presence implies passport or travel document
        mrz = self._extract_mrz()
        if mrz:
            return "passport"
        return None

    def _extract_issuing_authority(self) -> Optional[str]:
        patterns = [
            r"Issued\s+by[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Issuing\s+Authority[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Authority[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Government\s+of\s+([A-Z][A-Za-z0-9 ,.&'/-]{2,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # top-of-doc fallback
        top = "\n".join(self.text.splitlines()[:6])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            return m.group(1).strip()
        return None

    def _detect_photo(self) -> bool:
        # Best-effort: look for words indicating a photo or image placeholder
        if re.search(r"\b(photo|photograph|picture|image|headshot)\b", self.text, flags=re.IGNORECASE):
            return True
        # some OCR outputs include "[photo]" or "<image>"
        if re.search(r"\[photo\]|\[picture\]|\<image\>|\<photo\>", self.text, flags=re.IGNORECASE):
            return True
        # otherwise unknown; return False (analyzer may use metadata or image presence)
        return False

    # ---------- MRZ (Machine Readable Zone) helper ----------

    def _extract_mrz(self) -> Optional[Dict[str, Any]]:
        """
        Attempt to parse passport MRZ (two lines of 44 chars or three lines for ID).
        Returns dict with keys: names, dob (ISO), expiry (ISO), nationality (3-letter), id_number
        """
        # find lines that look like MRZ (only A-Z, 0-9 and <)
        lines = [ln.strip() for ln in self.text.splitlines() if ln.strip()]
        mrz_lines = []
        for ln in lines:
            if re.fullmatch(r"[A-Z0-9<]{30,44}", ln.replace(" ", "")):
                mrz_lines.append(ln.replace(" ", ""))
        if not mrz_lines:
            # try to find two consecutive MRZ-like lines
            for i in range(len(lines) - 1):
                a = re.sub(r"\s+", "", lines[i])
                b = re.sub(r"\s+", "", lines[i + 1])
                if re.fullmatch(r"[A-Z0-9<]{30,44}", a) and re.fullmatch(r"[A-Z0-9<]{30,44}", b):
                    mrz_lines = [a, b]
                    break
        if not mrz_lines:
            return None

        try:
            # handle two-line passport MRZ (TD3) - 2 lines of 44 chars
            if len(mrz_lines) >= 2 and len(mrz_lines[0]) >= 30:
                line1 = mrz_lines[0]
                line2 = mrz_lines[1]
                # passport number usually at positions 0..9 on line2 for some formats; attempt common parsing
                # This is heuristic and tolerant
                mrz = {}
                # extract name from line1 (after document code and issuing country)
                # format: P<COUNTRYSURNAME<<GIVEN<NAMES
                name_part = None
                m_name = re.search(r"^[A-Z0-9<]{2}([A-Z]{3})([A-Z<]+)$", line1)
                if m_name:
                    name_part = line1[m_name.start(2):].strip("<")
                else:
                    # fallback: split on '<<'
                    if "<<" in line1:
                        name_part = line1.split("<<", 1)[1]
                if name_part:
                    names = name_part.replace("<", " ").strip()
                    mrz["names"] = " ".join([p for p in names.split() if p])
                # DOB: often at fixed position in line2 (positions 13-18 for TD3)
                if len(line2) >= 18:
                    dob_raw = line2[13:19]
                    # format YYMMDD - convert to YYYY-MM-DD heuristically
                    if re.fullmatch(r"\d{6}", dob_raw):
                        yy = int(dob_raw[0:2])
                        mm = int(dob_raw[2:4])
                        dd = int(dob_raw[4:6])
                        year = 1900 + yy if yy > 30 else 2000 + yy
                        try:
                            mrz["dob"] = f"{year:04d}-{mm:02d}-{dd:02d}"
                        except Exception:
                            mrz["dob"] = None
                # expiry: often at positions 21-26 on line2
                if len(line2) >= 26:
                    exp_raw = line2[21:27]
                    if re.fullmatch(r"\d{6}", exp_raw):
                        yy = int(exp_raw[0:2])
                        mm = int(exp_raw[2:4])
                        dd = int(exp_raw[4:6])
                        year = 1900 + yy if yy > 30 else 2000 + yy
                        try:
                            mrz["expiry"] = f"{year:04d}-{mm:02d}-{dd:02d}"
                        except Exception:
                            mrz["expiry"] = None
                # nationality: often at positions 10-12 on line2
                if len(line2) >= 11:
                    nat = line2[10:13]
                    if re.fullmatch(r"[A-Z]{3}", nat):
                        mrz["nationality"] = nat
                # id number: often at positions 0-9 on line2
                if len(line2) >= 10:
                    id_raw = line2[0:10].replace("<", "").strip()
                    if id_raw:
                        mrz["id_number"] = id_raw
                return mrz
        except Exception:
            logger.exception("MRZ parsing failed")
            return None

        return None
