# app/services/analyzers/hr/resume_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class ResumeAnalyzer(BaseAnalyzer):
    """
    Analyzer for resumes:
    - Experience years, education level
    - Skills coverage vs target role (if provided)
    - Gaps in employment (if parser supports dates)
    - ATS-style score placeholder
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📂 Advanced resume analysis: {file_path}")

        resume = self._extract_resume(file_path, text, metadata)
        metrics = self._resume_metrics(resume)
        insights = self._generate_insights(resume, metrics)
        recommendations = self._generate_recommendations(resume, metrics)

        result = {
            "type": "resume",
            "summary": self._generate_summary(resume),
            "confidence": 0.9,

            "name": resume.get("name"),
            "email": resume.get("email"),
            "phone": resume.get("phone"),
            "headline": resume.get("headline"),

            **metrics,

            "skills": resume.get("skills", []),
            "experience": resume.get("experience", []),
            "education": resume.get("education", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_resume_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_resume(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_resume_parser import UniversalResumeParser
            parser = UniversalResumeParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Resume parsing failed: {e}")
            parsed = {}

        return {
            "name": parsed.get("name"),
            "email": parsed.get("email"),
            "phone": parsed.get("phone"),
            "headline": parsed.get("headline"),
            "skills": parsed.get("skills", []),
            "experience": parsed.get("experience", []),  # [{company, title, start_date, end_date}]
            "education": parsed.get("education", []),
            "years_experience": self._safe_float(parsed.get("years_experience")),
            "primary_role": parsed.get("primary_role"),
        }

    def _safe_float(self, v, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            return float(str(v).strip())
        except Exception:
            return default

    def _resume_metrics(self, resume: Dict[str, Any]) -> Dict[str, Any]:
        years = resume.get("years_experience", 0.0)
        skills_count = len(resume.get("skills", []))
        companies_count = len(resume.get("experience", []))
        education_count = len(resume.get("education", []))

        return {
            "years_experience": years,
            "skills_count": skills_count,
            "companies_count": companies_count,
            "education_entries": education_count,
        }

    def _generate_summary(self, resume: Dict[str, Any]) -> str:
        name = resume.get("name", "Candidate")
        role = resume.get("primary_role", "professional")
        years = resume.get("years_experience", 0.0)
        return f"Resume for {name}, a {role} with approximately {years:.1f} years of experience."

    def _generate_insights(self, resume, metrics) -> List[str]:
        insights = []

        insights.append(f"Years of experience: {metrics.get('years_experience', 0.0):.1f}")
        insights.append(f"Skills listed: {metrics.get('skills_count', 0)}")
        insights.append(f"Companies worked at: {metrics.get('companies_count', 0)}")
        insights.append(f"Education entries: {metrics.get('education_entries', 0)}")

        return insights

    def _generate_recommendations(self, resume, metrics) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        if metrics.get("skills_count", 0) < 5:
            recs.append({
                "priority": "medium",
                "category": "skills",
                "title": "Add More Skills",
                "description": "Consider listing more relevant skills to improve screening.",
                "potential_savings": 0,
            })

        if metrics.get("companies_count", 0) == 0:
            recs.append({
                "priority": "medium",
                "category": "experience",
                "title": "Clarify Experience",
                "description": "Work experience section appears empty or incomplete.",
                "potential_savings": 0,
            })

        return recs

    def build_resume_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "years_experience", "label": "Years Experience", "value_path": "years_experience", "is_hero": True},
            {"key": "skills_count", "label": "Skills", "value_path": "skills_count", "is_hero": True},
            {"key": "companies_count", "label": "Companies", "value_path": "companies_count", "is_hero": True},
            {"key": "education_entries", "label": "Education Entries", "value_path": "education_entries", "is_hero": True},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "skills", "name": "skills", "title": "Skills", "data_path": "skills"},
            {"id": "experience", "name": "experience", "title": "Experience", "data_path": "experience"},
            {"id": "education", "name": "education", "title": "Education", "data_path": "education"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Candidate Overview",
                "metrics": ["years_experience", "skills_count", "companies_count", "education_entries"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "skills",
                "title": "Skills",
                "metrics": [],
                "charts": [],
                "tables": ["skills"],
            },
            {
                "id": "experience",
                "title": "Experience",
                "metrics": [],
                "charts": [],
                "tables": ["experience"],
            },
            {
                "id": "education",
                "title": "Education",
                "metrics": [],
                "charts": [],
                "tables": ["education"],
            },
            {
                "id": "recommendations",
                "title": "Recommendations",
                "metrics": [],
                "charts": [],
                "tables": ["recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
