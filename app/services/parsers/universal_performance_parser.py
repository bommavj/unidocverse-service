# app/services/parsers/universal_performance_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalPerformanceParser(BaseParser):
    """
    Heuristic parser for performance reviews / evaluations.
    Returns a dict with keys commonly used by HR / performance analyzers:
      - employee_name
      - employee_id
      - reviewer_name
      - reviewer_title
      - review_period_start
      - review_period_end
      - review_date
      - overall_rating
      - rating_scale
      - goals: list[{goal, rating, comments}]
      - competencies: list[{name, rating, comments}]
      - strengths: list[str]
      - areas_for_improvement: list[str]
      - summary_comments
      - next_steps
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["employee_name"] = self._extract_employee_name()
            parsed["employee_id"] = self._extract_employee_id()
            parsed["reviewer_name"], parsed["reviewer_title"] = self._extract_reviewer()
            parsed["review_period_start"], parsed["review_period_end"] = self._extract_review_period()
            parsed["review_date"] = self._extract_review_date()
            parsed["overall_rating"] = self._extract_overall_rating()
            parsed["rating_scale"] = self._extract_rating_scale()

            parsed["goals"] = self._extract_goals()
            parsed["competencies"] = self._extract_competencies()
            parsed["strengths"] = self._extract_strengths()
            parsed["areas_for_improvement"] = self._extract_areas_for_improvement()
            parsed["summary_comments"] = self._extract_summary_comments()
            parsed["next_steps"] = self._extract_next_steps()

            return parsed

        except Exception:
            logger.exception("UniversalPerformanceParser.parse failed")
            return {
                "employee_name": None,
                "employee_id": None,
                "reviewer_name": None,
                "reviewer_title": None,
                "review_period_start": None,
                "review_period_end": None,
                "review_date": None,
                "overall_rating": None,
                "rating_scale": None,
                "goals": [],
                "competencies": [],
                "strengths": [],
                "areas_for_improvement": [],
                "summary_comments": None,
                "next_steps": None,
            }

    # ---------- identity ----------

    def _extract_employee_name(self) -> Optional[str]:
        v = self.find_first([
            r"Employee\s*Name[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Review\s*for[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ])
        if v:
            return self.normalize_whitespace(v)

        # fallback: top lines
        for ln in self.text.splitlines()[:10]:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln.strip()):
                return ln.strip()
        return None

    def _extract_employee_id(self) -> Optional[str]:
        return self.find_first([
            r"Employee\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Emp\s*ID[:\s]*([A-Za-z0-9\-_/]+)",
        ])

    # ---------- reviewer ----------

    def _extract_reviewer(self) -> (Optional[str], Optional[str]):
        name = self.find_first([
            r"Reviewer[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Manager[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Supervisor[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ])
        title = self.find_first([
            r"Reviewer\s*Title[:\s]*([A-Za-z0-9 ,.'\-]{2,120})",
            r"Manager\s*Title[:\s]*([A-Za-z0-9 ,.'\-]{2,120})",
        ])
        return (self.normalize_whitespace(name) if name else None,
                self.normalize_whitespace(title) if title else None)

    # ---------- dates ----------

    def _extract_review_period(self) -> (Optional[str], Optional[str]):
        m = re.search(
            r"Review\s*Period[:\s]*([A-Za-z0-9 ,\-\/]{6,40})\s*(?:to|-)\s*([A-Za-z0-9 ,\-\/]{6,40})",
            self.text, flags=re.IGNORECASE
        )
        if m:
            return self.parse_date(m.group(1)), self.parse_date(m.group(2))

        # fallback: separate fields
        start = self.find_first([r"Period\s*Start[:\s]*([A-Za-z0-9 ,\-\/]{6,40})"])
        end = self.find_first([r"Period\s*End[:\s]*([A-Za-z0-9 ,\-\/]{6,40})"])
        return self.parse_date(start) if start else None, self.parse_date(end) if end else None

    def _extract_review_date(self) -> Optional[str]:
        v = self.find_first([
            r"Review\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ])
        return self.parse_date(v) if v else None

    # ---------- ratings ----------

    def _extract_overall_rating(self) -> Optional[str]:
        v = self.find_first([
            r"Overall\s*Rating[:\s]*([A-Za-z0-9\.\- ]{1,40})",
            r"Final\s*Rating[:\s]*([A-Za-z0-9\.\- ]{1,40})",
        ])
        return v.strip() if v else None

    def _extract_rating_scale(self) -> Optional[str]:
        v = self.find_first([
            r"Rating\s*Scale[:\s]*([A-Za-z0-9 ,.\-]{3,200})",
            r"Scale[:\s]*([A-Za-z0-9 ,.\-]{3,200})",
        ])
        return self.normalize_whitespace(v) if v else None

    # ---------- goals ----------

    def _extract_goals(self) -> List[Dict[str, Any]]:
        blocks = self.find_all_blocks(
            r"^\s*(Goals|Objectives|Key Results)[:\s]*",
            end_regex=r"^\s*(Competencies|Strengths|Areas|Summary|Overall|Rating)"
        )
        items: List[Dict[str, Any]] = []
        if not blocks:
            return items

        blk = blocks[0]
        lines = [ln.strip() for ln in re.split(r"\n|•|- |\* ", blk) if ln.strip()]

        for ln in lines:
            if re.match(r"^(Goals|Objectives)", ln, flags=re.IGNORECASE):
                continue
            rating = None
            m = re.search(r"\b([0-9](?:\.[0-9])?)\s*/\s*[0-9]\b", ln)
            if m:
                rating = m.group(1)
            items.append({
                "goal": self.normalize_whitespace(ln),
                "rating": rating,
                "comments": None,
            })
        return items

    # ---------- competencies ----------

    def _extract_competencies(self) -> List[Dict[str, Any]]:
        blocks = self.find_all_blocks(
            r"^\s*(Competencies|Skills|Core Competencies)[:\s]*",
            end_regex=r"^\s*(Goals|Strengths|Areas|Summary|Overall|Rating)"
        )
        items: List[Dict[str, Any]] = []
        if not blocks:
            return items

        blk = blocks[0]
        lines = [ln.strip() for ln in re.split(r"\n|•|- |\* ", blk) if ln.strip()]

        for ln in lines:
            if re.match(r"^(Competencies|Skills)", ln, flags=re.IGNORECASE):
                continue
            rating = None
            m = re.search(r"\b([0-9](?:\.[0-9])?)\s*/\s*[0-9]\b", ln)
            if m:
                rating = m.group(1)
            items.append({
                "name": self.normalize_whitespace(ln),
                "rating": rating,
                "comments": None,
            })
        return items

    # ---------- strengths / improvements ----------

    def _extract_strengths(self) -> List[str]:
        return self._extract_bullets("Strengths|What You Do Well|Highlights")

    def _extract_areas_for_improvement(self) -> List[str]:
        return self._extract_bullets("Areas for Improvement|Development Areas|Opportunities")

    def _extract_bullets(self, heading_regex: str) -> List[str]:
        blocks = self.find_all_blocks(
            rf"^\s*(?:{heading_regex})[:\s]*",
            end_regex=r"^\s*(Goals|Competencies|Summary|Overall|Rating|Next Steps)"
        )
        items: List[str] = []
        if not blocks:
            return items

        blk = blocks[0]
        lines = re.split(r"\n|•|- |\* ", blk)
        for ln in lines:
            ln = ln.strip()
            if len(ln) > 3 and not re.match(rf"^{heading_regex}", ln, flags=re.IGNORECASE):
                items.append(self.normalize_whitespace(ln))
        return items

    # ---------- summary ----------

    def _extract_summary_comments(self) -> Optional[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Summary|Manager Comments|Reviewer Comments)[:\s]*",
            end_regex=r"^\s*(Next Steps|Goals|Competencies|Strengths|Areas)"
        )
        if blocks:
            return self.normalize_whitespace(blocks[0][:2000])
        return None

    def _extract_next_steps(self) -> Optional[str]:
        blocks = self.find_all_blocks(
            r"^\s*(Next Steps|Development Plan|Action Plan)[:\s]*",
            end_regex=r"^\s*(Summary|Goals|Competencies|Strengths|Areas)"
        )
        if blocks:
            return self.normalize_whitespace(blocks[0][:2000])
        return None
