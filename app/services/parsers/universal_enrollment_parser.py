# app/services/parsers/universal_enrollment_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalEnrollmentParser(BaseParser):
    """
    Heuristic parser for enrollment forms and enrollment confirmations.
    Returns a dict with keys commonly used by enrollment analyzers:
      - student_name
      - student_id
      - program
      - enrollment_date
      - status
      - term
      - courses: list of {code, title, credits, grade}
      - total_credits
      - gpa
      - contact: {email, phone, address}
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["student_name"] = self._extract_student_name()
            parsed["student_id"] = self._extract_student_id()
            parsed["program"] = self._extract_program()
            parsed["term"] = self._extract_term()
            parsed["enrollment_date"] = self._extract_enrollment_date()
            parsed["status"] = self._extract_status()
            parsed["courses"] = self._extract_courses()
            parsed["total_credits"] = self._compute_total_credits(parsed["courses"])
            parsed["gpa"] = self._extract_gpa()
            parsed["contact"] = self._extract_contact()
            return parsed
        except Exception:
            logger.exception("UniversalEnrollmentParser.parse failed")
            return {
                "student_name": None,
                "student_id": None,
                "program": None,
                "term": None,
                "enrollment_date": None,
                "status": None,
                "courses": [],
                "total_credits": 0.0,
                "gpa": None,
                "contact": {},
            }

    # ---------- top-level fields ----------

    def _extract_student_name(self) -> Optional[str]:
        patterns = [
            r"Student\s+Name[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"Name[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"Registrant[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"This certifies that[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look near top lines for a likely name (first non-empty line with capitalized words)
        top_lines = [ln.strip() for ln in self.text.splitlines()[:8] if ln.strip()]
        for ln in top_lines:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln):
                return self.normalize_whitespace(ln)
        return None

    def _extract_student_id(self) -> Optional[str]:
        patterns = [
            r"Student\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"ID[:\s]*([A-Za-z0-9\-_/]{4,20})",
            r"Student\s+Number[:\s]*([0-9\-]{4,20})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: look for common id patterns anywhere
        m = re.search(r"\b(SID|Student\s*ID|Student\s*No\.?)[:\s]*([A-Za-z0-9\-_/]{4,20})\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(2).strip()
        return None

    def _extract_program(self) -> Optional[str]:
        patterns = [
            r"Program[:\s]*([A-Za-z0-9 &,\-]{3,120})",
            r"Major[:\s]*([A-Za-z0-9 &,\-]{3,120})",
            r"Course of Study[:\s]*([A-Za-z0-9 &,\-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look for "Enrolled in" phrase
        m = re.search(r"Enrolled\s+in[:\s]*([A-Za-z0-9 &,\-]{3,120})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_term(self) -> Optional[str]:
        patterns = [
            r"Term[:\s]*([A-Za-z0-9 \-]{3,40})",
            r"Academic\s+Term[:\s]*([A-Za-z0-9 \-]{3,40})",
            r"Semester[:\s]*([A-Za-z0-9 \-]{3,40})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # try to infer from dates
        m = re.search(r"(Fall|Spring|Summer|Winter)\s+(\d{4})", self.text, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1).title()} {m.group(2)}"
        return None

    def _extract_enrollment_date(self) -> Optional[str]:
        patterns = [
            r"Enrollment\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date\s+of\s+Enrollment[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Enrolled[:\s]*on\s*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: first date in document
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
        return None

    def _extract_status(self) -> Optional[str]:
        patterns = [
            r"Status[:\s]*(enrolled|pending|withdrawn|completed|active|inactive|deferred)",
            r"Enrollment\s+Status[:\s]*(enrolled|pending|withdrawn|completed|active|inactive|deferred)",
        ]
        v = self.find_first(patterns)
        if v:
            return v.lower()
        # look for keywords
        if re.search(r"\b(enrolled|registered|admitted)\b", self.text, flags=re.IGNORECASE):
            return "enrolled"
        if re.search(r"\b(pending|awaiting)\b", self.text, flags=re.IGNORECASE):
            return "pending"
        if re.search(r"\b(withdrawn|cancelled|cancelled)\b", self.text, flags=re.IGNORECASE):
            return "withdrawn"
        return None

    # ---------- courses extraction ----------

    def _extract_courses(self) -> List[Dict[str, Any]]:
        """
        Attempt to extract course rows. Common formats:
          - Table rows: CODE  |  Title  | Credits  | Grade
          - Lines: CODE - Title (3 credits) Grade: A
          - Bulleted lists
        Returns list of {code, title, credits, grade}
        """
        courses: List[Dict[str, Any]] = []

        # 1) Table-like rows with pipes or multiple columns
        table_rows = re.findall(
            r"^([A-Z]{2,6}\s*\d{1,4}[A-Z]?)\s*(?:\||\-|\s{2,})\s*([^\|\n]{5,120}?)\s*(?:\||\-|\s{2,})\s*([\d\.]+)\s*(?:credits|cr|credit)?\s*(?:\||\-|\s{2,})?\s*([A-F][\+\-]?|Pass|Fail|P|F|Incomplete)?",
            self.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if table_rows:
            for r in table_rows:
                code = r[0].strip()
                title = self.normalize_whitespace(r[1])
                credits = self.safe_float(r[2])
                grade = r[3].strip() if r[3] else None
                courses.append({"code": code, "title": title, "credits": credits, "grade": grade})
            if courses:
                return courses

        # 2) Lines like "CS101 - Introduction to Programming (3 credits) Grade: A"
        line_pattern = re.compile(
            r"(?P<code>[A-Z]{2,6}\s*\d{1,4}[A-Z]?)\s*[-:]\s*(?P<title>.+?)\s*\(?\s*(?P<credits>[\d\.]+)\s*(?:credits|cr|credit)?\)?(?:.*?Grade[:\s]*(?P<grade>[A-F][\+\-]?|Pass|Fail|P|F|Incomplete))?",
            flags=re.IGNORECASE,
        )
        for m in line_pattern.finditer(self.text):
            code = m.group("code").strip()
            title = self.normalize_whitespace(m.group("title"))
            credits = self.safe_float(m.group("credits"))
            grade = m.group("grade").strip() if m.group("grade") else None
            courses.append({"code": code, "title": title, "credits": credits, "grade": grade})
        if courses:
            return courses

        # 3) Bulleted or numbered course lists under headings like "Courses" or "Enrolled Courses"
        blocks = self.find_all_blocks(r"^\s*(?:Courses|Enrolled Courses|Registered Courses|Course List)[:\s]*", end_regex=r"^\s*(?:Total|Summary|GPA|Contact|Notes)")
        if blocks:
            for blk in blocks:
                lines = [ln.strip() for ln in re.split(r"\n|;|-•|•", blk) if ln.strip()]
                for ln in lines:
                    # try to extract code and credits
                    m = re.search(r"(?P<code>[A-Z]{2,6}\s*\d{1,4}[A-Z]?)\b.*?([\d\.]+)\s*(?:credits|cr|credit)", ln, flags=re.IGNORECASE)
                    code = None
                    credits = 0.0
                    grade = None
                    title = ln
                    if m:
                        code = m.group("code").strip()
                        credits = self.safe_float(m.group(2))
                        title = re.sub(r"\(?[\d\.]+\s*(?:credits|cr|credit)\)?", "", ln, flags=re.IGNORECASE).strip()
                    # grade
                    g = re.search(r"Grade[:\s]*([A-F][\+\-]?|Pass|Fail|P|F|Incomplete)", ln, flags=re.IGNORECASE)
                    if g:
                        grade = g.group(1).strip()
                    courses.append({"code": code, "title": self.normalize_whitespace(title), "credits": credits, "grade": grade})
            if courses:
                return courses

        # 4) Fallback: look for course codes anywhere and create minimal entries
        codes = re.findall(r"\b([A-Z]{2,6}\s*\d{3,4}[A-Z]?)\b", self.text)
        seen = set()
        for c in codes:
            c_norm = c.strip()
            if c_norm in seen:
                continue
            seen.add(c_norm)
            # try to capture a short title after the code
            m = re.search(rf"{re.escape(c_norm)}\s*[-:]\s*([A-Za-z0-9 \-\,\(\)\/]{{3,80}})", self.text)
            title = m.group(1).strip() if m else None
            courses.append({"code": c_norm, "title": self.normalize_whitespace(title) if title else None, "credits": 0.0, "grade": None})
        return courses

    def _compute_total_credits(self, courses: List[Dict[str, Any]]) -> float:
        try:
            return round(sum(self.safe_float(c.get("credits", 0)) for c in courses), 2)
        except Exception:
            return 0.0

    # ---------- GPA and contact ----------

    def _extract_gpa(self) -> Optional[float]:
        # look for "GPA: 3.75" or "Cumulative GPA"
        m = re.search(r"(?:GPA|Cumulative\s+GPA)[:\s]*([0-4]\.\d{1,3})", self.text, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                return None
        # sometimes reported as "Grade Point Average: 3.5"
        m2 = re.search(r"Grade\s+Point\s+Average[:\s]*([0-4]\.\d{1,3})", self.text, flags=re.IGNORECASE)
        if m2:
            try:
                return float(m2.group(1))
            except Exception:
                return None
        return None

    def _extract_contact(self) -> Dict[str, Optional[str]]:
        contact = {"email": None, "phone": None, "address": None}
        # email
        m = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", self.text)
        if m:
            contact["email"] = m.group(1).strip()
        # phone (various formats)
        m2 = re.search(r"(\+?\d{1,3}[\s\-\.]?)?(\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4})", self.text)
        if m2:
            contact["phone"] = (m2.group(1) or "") + m2.group(2)
            contact["phone"] = contact["phone"].strip()
        # address: look for "Address:" block or lines near contact heading
        blocks = self.find_all_blocks(r"^\s*(?:Address|Mailing Address|Contact Address)[:\s]*", end_regex=r"^\s*(?:Phone|Email|E-mail|Contact)")
        if blocks:
            contact["address"] = blocks[0].split("\n")[0].strip()
        else:
            # fallback: try to capture a postal-like line
            m3 = re.search(r"\d{1,5}\s+[A-Za-z0-9\.\- ]{5,120}\,\s*[A-Za-z]{2,}\s*\d{5}", self.text)
            if m3:
                contact["address"] = m3.group(0).strip()
        return contact
