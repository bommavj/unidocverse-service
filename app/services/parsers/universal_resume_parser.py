# app/services/parsers/universal_resume_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalResumeParser(BaseParser):
    """
    Heuristic parser for resumes / CVs.
    Returns a dict with keys commonly used by resume analyzers:
      - name
      - email
      - phone
      - location
      - linkedin
      - github
      - website
      - summary
      - skills: list[str]
      - experience: list[{company, title, start_date, end_date, description}]
      - education: list[{institution, degree, start_date, end_date}]
      - certifications: list[str]
      - projects: list[{name, description}]
      - languages: list[str]
      - awards: list[str]
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["name"] = self._extract_name()
            parsed["email"] = self._extract_email()
            parsed["phone"] = self._extract_phone()
            parsed["location"] = self._extract_location()
            parsed["linkedin"] = self._extract_linkedin()
            parsed["github"] = self._extract_github()
            parsed["website"] = self._extract_website()

            parsed["summary"] = self._extract_summary()
            parsed["skills"] = self._extract_skills()
            parsed["experience"] = self._extract_experience()
            parsed["education"] = self._extract_education()
            parsed["certifications"] = self._extract_certifications()
            parsed["projects"] = self._extract_projects()
            parsed["languages"] = self._extract_languages()
            parsed["awards"] = self._extract_awards()

            return parsed

        except Exception:
            logger.exception("UniversalResumeParser.parse failed")
            return {
                "name": None,
                "email": None,
                "phone": None,
                "location": None,
                "linkedin": None,
                "github": None,
                "website": None,
                "summary": None,
                "skills": [],
                "experience": [],
                "education": [],
                "certifications": [],
                "projects": [],
                "languages": [],
                "awards": [],
            }

    # ---------- top-level identity ----------

    def _extract_name(self) -> Optional[str]:
        # Look for name at top: first 1–3 lines
        for ln in self.text.splitlines()[:5]:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln.strip()):
                return ln.strip()
        return None

    def _extract_email(self) -> Optional[str]:
        m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", self.text)
        return m.group(0) if m else None

    def _extract_phone(self) -> Optional[str]:
        m = re.search(r"(\+?\d[\d\-\(\) ]{7,}\d)", self.text)
        return m.group(1).strip() if m else None

    def _extract_location(self) -> Optional[str]:
        patterns = [
            r"Location[:\s]*([A-Za-z0-9 ,.'\-]{3,200})",
            r"Based in[:\s]*([A-Za-z0-9 ,.'\-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: city, state pattern
        m = re.search(r"\b([A-Za-z ]+,\s*[A-Za-z]{2,})\b", self.text)
        return m.group(1) if m else None

    def _extract_linkedin(self) -> Optional[str]:
        m = re.search(r"(https?://)?(www\.)?linkedin\.com/[A-Za-z0-9/_\-]+", self.text)
        return m.group(0) if m else None

    def _extract_github(self) -> Optional[str]:
        m = re.search(r"(https?://)?(www\.)?github\.com/[A-Za-z0-9_\-]+", self.text)
        return m.group(0) if m else None

    def _extract_website(self) -> Optional[str]:
        m = re.search(r"(https?://[A-Za-z0-9\./_\-]+)", self.text)
        return m.group(0) if m else None

    # ---------- summary ----------

    def _extract_summary(self) -> Optional[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Summary|Profile|Professional Summary|About Me)[:\s]*",
            end_regex=r"^\s*(Skills|Experience|Work History|Education|Projects|Certifications)"
        )
        if blocks:
            return self.normalize_whitespace(blocks[0][:2000])
        return None

    # ---------- skills ----------

    def _extract_skills(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Skills|Technical Skills|Core Skills|Key Skills)[:\s]*",
            end_regex=r"^\s*(Experience|Work History|Education|Projects|Certifications)"
        )
        if not blocks:
            return []

        blk = blocks[0]
        # split by commas, bullets, newlines
        parts = re.split(r"[\n,•\-]+", blk)
        skills = []
        for p in parts:
            p = p.strip()
            if len(p) > 1 and not re.match(r"^(Skills|Technical Skills)", p, flags=re.IGNORECASE):
                skills.append(self.normalize_whitespace(p))
        return skills

    # ---------- experience ----------

    def _extract_experience(self) -> List[Dict[str, Any]]:
        blocks = self.find_all_blocks(
            r"^\s*(Experience|Work Experience|Work History|Professional Experience)[:\s]*",
            end_regex=r"^\s*(Education|Skills|Projects|Certifications|Summary)"
        )
        if not blocks:
            return []

        blk = blocks[0]
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]

        items: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}

        date_re = r"(\b[A-Za-z]{3,9}\s+\d{4}\b|\b\d{4}\b|\d{1,2}/\d{4})"

        for ln in lines:
            # Company + Title line
            if re.match(r"^[A-Z][A-Za-z0-9 ,.&'/-]{3,200}$", ln):
                if current:
                    items.append(current)
                current = {"company": ln, "title": None, "start_date": None, "end_date": None, "description": ""}

            # Title line
            elif re.match(r"^[A-Za-z][A-Za-z0-9 ,.&'/-]{3,200}$", ln) and "company" in current and not current.get("title"):
                current["title"] = ln

            # Dates
            elif re.search(date_re, ln):
                m = re.findall(date_re, ln)
                if m:
                    if len(m) == 1:
                        current["start_date"] = self.parse_date(m[0])
                    elif len(m) >= 2:
                        current["start_date"] = self.parse_date(m[0])
                        current["end_date"] = self.parse_date(m[1])

            # Description lines
            else:
                if "description" in current:
                    current["description"] += " " + ln

        if current:
            items.append(current)

        # cleanup
        for it in items:
            it["description"] = self.normalize_whitespace(it.get("description", ""))[:2000]

        return items

    # ---------- education ----------

    def _extract_education(self) -> List[Dict[str, Any]]:
        blocks = self.find_all_blocks(
            r"^\s*(Education|Academic Background|Academics)[:\s]*",
            end_regex=r"^\s*(Experience|Skills|Projects|Certifications|Summary)"
        )
        if not blocks:
            return []

        blk = blocks[0]
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]

        items: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}

        date_re = r"(\b\d{4}\b|\b[A-Za-z]{3,9}\s+\d{4}\b)"

        for ln in lines:
            # Institution
            if re.match(r"^[A-Z][A-Za-z0-9 ,.&'/-]{3,200}$", ln):
                if current:
                    items.append(current)
                current = {"institution": ln, "degree": None, "start_date": None, "end_date": None}

            # Degree
            elif re.search(r"(Bachelor|Master|B\.Sc|M\.Sc|Ph\.D|MBA|BS|MS)", ln, flags=re.IGNORECASE):
                current["degree"] = ln

            # Dates
            elif re.search(date_re, ln):
                m = re.findall(date_re, ln)
                if m:
                    if len(m) == 1:
                        current["start_date"] = self.parse_date(m[0])
                    elif len(m) >= 2:
                        current["start_date"] = self.parse_date(m[0])
                        current["end_date"] = self.parse_date(m[1])

        if current:
            items.append(current)

        return items

    # ---------- certifications ----------

    def _extract_certifications(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Certifications|Licenses)[:\s]*",
            end_regex=r"^\s*(Education|Experience|Projects|Skills)"
        )
        if not blocks:
            return []
        blk = blocks[0]
        parts = re.split(r"[\n•\-]+", blk)
        return [self.normalize_whitespace(p) for p in parts if len(p.strip()) > 2]

    # ---------- projects ----------

    def _extract_projects(self) -> List[Dict[str, Any]]:
        blocks = self.find_all_blocks(
            r"^\s*(Projects|Portfolio)[:\s]*",
            end_regex=r"^\s*(Experience|Education|Skills|Certifications)"
        )
        if not blocks:
            return []

        blk = blocks[0]
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]

        items: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}

        for ln in lines:
            if re.match(r"^[A-Z][A-Za-z0-9 ,.&'/-]{3,200}$", ln):
                if current:
                    items.append(current)
                current = {"name": ln, "description": ""}
            else:
                if "description" in current:
                    current["description"] += " " + ln

        if current:
            items.append(current)

        for it in items:
            it["description"] = self.normalize_whitespace(it.get("description", ""))[:2000]

        return items

    # ---------- languages ----------

    def _extract_languages(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Languages)[:\s]*",
            end_regex=r"^\s*(Skills|Experience|Education|Projects)"
        )
        if not blocks:
            return []
        blk = blocks[0]
        parts = re.split(r"[\n,•\-]+", blk)
        return [p.strip() for p in parts if len(p.strip()) > 1]

    # ---------- awards ----------

    def _extract_awards(self) -> List[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Awards|Honors|Achievements)[:\s]*",
            end_regex=r"^\s*(Skills|Experience|Education|Projects)"
        )
        if not blocks:
            return []
        blk = blocks[0]
        parts = re.split(r"[\n•\-]+", blk)
        return [self.normalize_whitespace(p) for p in parts if len(p.strip()) > 2]
