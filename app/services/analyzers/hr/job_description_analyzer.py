# app/services/analyzers/hr/job_description_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class JobDescriptionAnalyzer(BaseAnalyzer):
    """
    Analyzer for job descriptions:
    - Role, department, seniority
    - Required vs preferred skills
    - Responsibilities count
    - Gaps: missing salary, missing location, missing reporting line
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📌 Advanced job description analysis: {file_path}")

        jd = self._extract_jd(file_path, text, metadata)

        metrics = self._jd_metrics(jd)
        gaps = self._gap_checks(jd)
        insights = self._generate_insights(jd, metrics, gaps)
        recommendations = self._generate_recommendations(jd, metrics, gaps)

        result = {
            "type": "job_description",
            "summary": self._generate_summary(jd),
            "confidence": 0.9,

            "position_title": jd.get("position_title"),
            "department": jd.get("department"),
            "seniority_level": jd.get("seniority_level"),
            "location": jd.get("location"),

            **metrics,

            "required_skills": jd.get("required_skills", []),
            "preferred_skills": jd.get("preferred_skills", []),
            "responsibilities": jd.get("responsibilities", []),

            "jd_gaps": gaps,
            "jd_gap_count": len(gaps),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_jd_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_jd(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_jd_parser import UniversalJDParser
            parser = UniversalJDParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Job description parsing failed: {e}")
            parsed = {}

        return {
            "position_title": parsed.get("position_title"),
            "department": parsed.get("department"),
            "seniority_level": parsed.get("seniority_level"),
            "location": parsed.get("location"),
            "salary_range": parsed.get("salary_range"),
            "required_skills": parsed.get("required_skills", []),
            "preferred_skills": parsed.get("preferred_skills", []),
            "responsibilities": parsed.get("responsibilities", []),
            "reports_to": parsed.get("reports_to"),
        }

    def _jd_metrics(self, jd: Dict[str, Any]) -> Dict[str, Any]:
        req = jd.get("required_skills", [])
        pref = jd.get("preferred_skills", [])
        resp = jd.get("responsibilities", [])

        return {
            "required_skills_count": len(req),
            "preferred_skills_count": len(pref),
            "responsibilities_count": len(resp),
        }

    def _gap_checks(self, jd: Dict[str, Any]) -> List[str]:
        gaps: List[str] = []

        if not jd.get("salary_range"):
            gaps.append("Salary range not specified.")

        if not jd.get("location"):
            gaps.append("Location not specified.")

        if not jd.get("reports_to"):
            gaps.append("Reporting line (manager) not specified.")

        if not jd.get("required_skills"):
            gaps.append("Required skills not clearly listed.")

        if not jd.get("responsibilities"):
            gaps.append("Responsibilities section is missing or too short.")

        return gaps

    def _generate_summary(self, jd: Dict[str, Any]) -> str:
        title = jd.get("position_title", "Position")
        dept = jd.get("department", "Department")
        level = jd.get("seniority_level", "")
        level_part = f"{level} " if level else ""
        return f"Job description for {level_part}{title} in {dept}."

    def _generate_insights(self, jd, metrics, gaps) -> List[str]:
        insights = []

        insights.append(f"Required skills: {metrics.get('required_skills_count', 0)}")
        insights.append(f"Preferred skills: {metrics.get('preferred_skills_count', 0)}")
        insights.append(f"Responsibilities listed: {metrics.get('responsibilities_count', 0)}")

        if gaps:
            insights.append(f"Content gaps detected: {len(gaps)}")

        return insights

    def _generate_recommendations(self, jd, metrics, gaps) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for g in gaps:
            recs.append({
                "priority": "medium",
                "category": "completeness",
                "title": "Improve Job Description Clarity",
                "description": g,
                "potential_savings": 0,
            })

        return recs

    def build_jd_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "required_skills_count", "label": "Required Skills", "value_path": "required_skills_count", "is_hero": True},
            {"key": "preferred_skills_count", "label": "Preferred Skills", "value_path": "preferred_skills_count", "is_hero": True},
            {"key": "responsibilities_count", "label": "Responsibilities", "value_path": "responsibilities_count", "is_hero": True},
            {"key": "jd_gap_count", "label": "Content Gaps", "value_path": "jd_gap_count", "is_hero": True},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "required_skills", "name": "required_skills", "title": "Required Skills", "data_path": "required_skills"},
            {"id": "preferred_skills", "name": "preferred_skills", "title": "Preferred Skills", "data_path": "preferred_skills"},
            {"id": "responsibilities", "name": "responsibilities", "title": "Responsibilities", "data_path": "responsibilities"},
            {"id": "jd_gaps", "name": "jd_gaps", "title": "Gaps", "data_path": "jd_gaps"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Job Description Summary",
                "metrics": ["required_skills_count", "preferred_skills_count", "responsibilities_count", "jd_gap_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "skills",
                "title": "Skills",
                "metrics": [],
                "charts": [],
                "tables": ["required_skills", "preferred_skills"],
            },
            {
                "id": "responsibilities",
                "title": "Responsibilities",
                "metrics": [],
                "charts": [],
                "tables": ["responsibilities"],
            },
            {
                "id": "gaps",
                "title": "Gaps & Recommendations",
                "metrics": [],
                "charts": [],
                "tables": ["jd_gaps", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
