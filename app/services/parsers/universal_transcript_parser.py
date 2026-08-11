# app/services/parsers/universal_transcript_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalTranscriptParser(BaseParser):
    """
    Heuristic parser for academic transcripts.
    Returns a dict with keys commonly used by transcript analyzers:
      - student_name
      - student_id
      - institution
      - major
      - degree
      - conferral_date
      - term (e.g., Fall 2024)
      - gpa (cumulative)
      - courses: list of {code, title, credits, grade, term}
      - total_credits
      - honors
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["student_name"] = self._extract_student_name()
            parsed["student_id"] = self._extract_student_id()
            parsed["institution"] = self._extract_institution()
            parsed["major"] = self._extract_major()
            parsed["degree"] = self._extract_degree()
            parsed["conferral_date"] = self._extract_conferral_date()
            parsed["term"] = self._extract_term()
            parsed["gpa"] = self._extract_cumulative_gpa()
            parsed["courses"] = self._extract_courses()
            parsed["total_credits"] = self._compute_total_credits(parsed["courses"])
            parsed["honors"] = self._extract_honors()
            return parsed
        except Exception:
            logger.exception("UniversalTranscriptParser.parse failed")
            return {
                "student_name": None,
                "student_id": None,
                "institution": None,
                "major": None,
                "degree": None,
                "conferral_date": None,
                "term": None,
                "gpa": None,
                "courses": [],
                "total_credits": 0.0,
                "honors": None,
            }

    # ---------- top-level fields ----------

    def _extract_student_name(self) -> Optional[str]:
        patterns = [
            r"Student\s+Name[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"Name[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"Record\s+for[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look for prominent uppercase name near top (first 8 lines)
        top_lines = [ln.strip() for ln in self.text.splitlines()[:10] if ln.strip()]
        for ln in top_lines:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln):
                return self.normalize_whitespace(ln)
        return None

    def _extract_student_id(self) -> Optional[str]:
        patterns = [
            r"Student\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"ID[:\s]*([A-Za-z0-9\-_/]{4,20})",
            r"Record\s*ID[:\s]*([A-Za-z0-9\-_/]{4,20})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: common id patterns anywhere
        m = re.search(r"\b(Student\s*ID|SID|ID)[:\s]*([A-Za-z0-9\-_/]{4,20})\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(2).strip()
        return None

    def _extract_institution(self) -> Optional[str]:
        patterns = [
            r"([A-Z][A-Za-z0-9 '&,\.-]{3,120}\sUniversity)",
            r"([A-Z][A-Za-z0-9 '&,\.-]{3,120}\sCollege)",
            r"([A-Z][A-Za-z0-9 '&,\.-]{3,120}\sInstitute)",
            r"Official Transcript\s+of\s+([A-Z][A-Za-z0-9 '&,\.-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: top-of-doc line
        top = "\n".join(self.text.splitlines()[:6])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_major(self) -> Optional[str]:
        patterns = [
            r"Major[:\s]*([A-Za-z0-9 &,\-]{3,120})",
            r"Program[:\s]*([A-Za-z0-9 &,\-]{3,120})",
            r"Degree\s+Program[:\s]*([A-Za-z0-9 &,\-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # try to infer from degree line
        m = re.search(r"in\s+([A-Za-z0-9 &,\-]{3,80})\s*(?:major|program)?", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_degree(self) -> Optional[str]:
        patterns = [
            r"(Bachelor(?:'s)?\s+of\s+[A-Za-z]{2,20})",
            r"(Master(?:'s)?\s+of\s+[A-Za-z]{2,20})",
            r"(Doctor(?:ate| of Philosophy| of Medicine)|Ph\.?D\.?)",
            r"\b(B\.?S\.?|B\.?A\.?|M\.?S\.?|M\.?A\.?|MBA|LLB|LLM|J\.?D\.?)\b",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        return None

    def _extract_conferral_date(self) -> Optional[str]:
        patterns = [
            r"Conferral\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date\s+of\s+Conferral[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Degree\s+Conferred[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Conferred[:\s]*on\s*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: last date in document that looks like conferral
        m = re.search(r"(?:Conferred|Degree|Conferral).{0,40}?(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text, flags=re.IGNORECASE)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
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
        # infer from dates
        m = re.search(r"(Fall|Spring|Summer|Winter)\s+(\d{4})", self.text, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1).title()} {m.group(2)}"
        return None

    # ---------- GPA extraction ----------

    def _extract_cumulative_gpa(self) -> Optional[float]:
        # look for "Cumulative GPA: 3.75" or "GPA: 3.75"
        m = re.search(r"(?:Cumulative\s+GPA|Cumulative\s+Grade\s+Point\s+Average|GPA)[:\s]*([0-4]\.\d{1,3})", self.text, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                return None
        # sometimes reported as "Overall GPA 3.5"
        m2 = re.search(r"Overall\s+GPA[:\s]*([0-4]\.\d{1,3})", self.text, flags=re.IGNORECASE)
        if m2:
            try:
                return float(m2.group(1))
            except Exception:
                return None
        return None

    # ---------- courses extraction ----------

    def _extract_courses(self) -> List[Dict[str, Any]]:
        """
        Attempt to extract course rows. Common formats:
          - CODE  |  Title  | Credits  | Grade  | Term
          - Lines: CODE - Title (3.0) Grade: A Term: Fall 2024
          - Bulleted lists under term headings
        Returns list of {code, title, credits, grade, term}
        """
        courses: List[Dict[str, Any]] = []

        # 1) Table-like rows with pipes or multiple columns
        table_rows = re.findall(
            r"^([A-Z]{2,6}\s*\d{1,4}[A-Z]?)\s*(?:\||\-|\s{2,})\s*([^\|\n]{5,120}?)\s*(?:\||\-|\s{2,})\s*([\d\.]+)\s*(?:credits|cr|credit)?\s*(?:\||\-|\s{2,})\s*([A-F][\+\-]?|Pass|Fail|P|F|I|Incomplete)?(?:\s*(?:\||\-|\s{2,})\s*([A-Za-z]+\s*\d{4}))?",
            self.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if table_rows:
            for r in table_rows:
                code = r[0].strip()
                title = self.normalize_whitespace(r[1])
                credits = self.safe_float(r[2])
                grade = r[3].strip() if r[3] else None
                term = r[4].strip() if len(r) > 4 and r[4] else None
                courses.append({"code": code, "title": title, "credits": credits, "grade": grade, "term": term})
            if courses:
                return courses

        # 2) Lines like "CS101 - Intro to Programming (3.0) Grade: A Term: Fall 2024"
        line_pattern = re.compile(
            r"(?P<code>[A-Z]{2,6}\s*\d{1,4}[A-Z]?)\s*[-:]\s*(?P<title>.+?)\s*\(?\s*(?P<credits>[\d\.]+)\s*(?:credits|cr|credit)?\)?(?:.*?Grade[:\s]*(?P<grade>[A-F][\+\-]?|Pass|Fail|P|F|I|Incomplete))?(?:.*?Term[:\s]*(?P<term>[A-Za-z]+\s*\d{4}))?",
            flags=re.IGNORECASE,
        )
        for m in line_pattern.finditer(self.text):
            code = m.group("code").strip()
            title = self.normalize_whitespace(m.group("title"))
            credits = self.safe_float(m.group("credits"))
            grade = m.group("grade").strip() if m.group("grade") else None
            term = m.group("term").strip() if m.group("term") else None
            courses.append({"code": code, "title": title, "credits": credits, "grade": grade, "term": term})
        if courses:
            return courses

        # 3) Term blocks: find headings like "Fall 2024" and capture following lines as courses
        term_blocks = self.find_all_blocks(r"^\s*(Fall|Spring|Summer|Winter)\s+\d{4}\b[:\s]*", end_regex=r"^\s*(?:Fall|Spring|Summer|Winter)\s+\d{4}\b")
        if term_blocks:
            # term_blocks returns the content after the heading; we need to find headings with their content
            term_heading_iter = re.finditer(r"^\s*(Fall|Spring|Summer|Winter)\s+(\d{4})\b[:\s]*", self.text, flags=re.MULTILINE | re.IGNORECASE)
            headings = list(term_heading_iter)
            for idx, h in enumerate(headings):
                term_label = f"{h.group(1).title()} {h.group(2)}"
                start = h.end()
                end = headings[idx + 1].start() if idx + 1 < len(headings) else len(self.text)
                block = self.text[start:end]
                # extract lines that look like courses
                lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
                for ln in lines:
                    m = re.search(r"(?P<code>[A-Z]{2,6}\s*\d{1,4}[A-Z]?)\b.*?([\d\.]+)\s*(?:credits|cr|credit)", ln, flags=re.IGNORECASE)
                    if m:
                        code = m.group("code").strip()
                        credits = self.safe_float(m.group(2))
                        # title: text between code and credits
                        title = re.sub(rf"{re.escape(code)}", "", ln, count=1).strip()
                        title = re.sub(r"[\(\)\d\.]*credits.*", "", title, flags=re.IGNORECASE).strip()
                        courses.append({"code": code, "title": self.normalize_whitespace(title), "credits": credits, "grade": None, "term": term_label})
            if courses:
                return courses

        # 4) Fallback: find course codes anywhere and create minimal entries
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
            courses.append({"code": c_norm, "title": self.normalize_whitespace(title) if title else None, "credits": 0.0, "grade": None, "term": None})
        return courses

    def _compute_total_credits(self, courses: List[Dict[str, Any]]) -> float:
        try:
            return round(sum(self.safe_float(c.get("credits", 0)) for c in courses), 2)
        except Exception:
            return 0.0

    # ---------- honors extraction ----------

    def _extract_honors(self) -> Optional[str]:
        patterns = [
            r"(summa\s+cum\s+laude)",
            r"(magna\s+cum\s+laude)",
            r"(cum\s+laude)",
            r"(with\s+honors)",
            r"(with\s+distinction)",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look for "Honors" near degree/conferral lines
        m = re.search(r"(Honors[:\s]*[A-Za-z ,\-]{3,80})", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1).split