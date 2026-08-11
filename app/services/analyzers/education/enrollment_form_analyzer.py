# app/services/analyzers/education/enrollment_form_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class EnrollmentFormAnalyzer(BaseAnalyzer):
    """
    Analyzer for enrollment/registration forms:
    - Student info, semester, selected courses
    - Missing required fields
    - Risk flags: missing email, missing student ID, empty course list
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]):
        logger.info(f"📝 Enrollment form analysis: {file_path}")

        f = self._extract_form(file_path, text, metadata)
        metrics = self._form_metrics(f)
        risk_metrics = self._risk_metrics(f)
        insights = self._generate_insights(f, metrics, risk_metrics)
        recommendations = self._generate_recommendations(f, metrics, risk_metrics)

        result = {
            "type": "enrollment_form",
            "summary": self._generate_summary(f),
            "confidence": 0.94,

            "student_id": f.get("student_id"),
            "student_name": f.get("student_name"),
            "email": f.get("email"),
            "semester": f.get("semester"),

            **metrics,
            **risk_metrics,

            "selected_courses": f.get("selected_courses", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_enrollment_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_form(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_enrollment_parser import UniversalEnrollmentParser
            parser = UniversalEnrollmentParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Enrollment form parsing failed: {e}")
            parsed = {}

        return {
            "student_id": parsed.get("student_id"),
            "student_name": parsed.get("student_name"),
            "email": parsed.get("email"),
            "semester": parsed.get("semester"),
            "selected_courses": parsed.get("selected_courses", []),
        }

    def _form_metrics(self, f):
        return {
            "course_count": len(f.get("selected_courses", [])),
        }

    def _risk_metrics(self, f):
        risks = []

        if not f.get("student_id"):
            risks.append("Student ID missing.")

        if not f.get("email"):
            risks.append("Email missing.")

        if len(f.get("selected_courses", [])) == 0:
            risks.append("No courses selected.")

        return {
            "enrollment_risks": risks,
            "enrollment_risk_count": len(risks),
        }

    def _generate_summary(self, f):
        name = f.get("student_name", "Student")
        sem = f.get("semester", "N/A")
        return f"Enrollment form for {name} for semester {sem}."

    def _generate_insights(self, f, metrics, risk_metrics):
        insights = [
            f"Courses selected: {metrics['course_count']}",
        ]

        if risk_metrics["enrollment_risk_count"] > 0:
            insights.append(f"Risks identified: {risk_metrics['enrollment_risk_count']}.")

        return insights

    def _generate_recommendations(self, f, metrics, risk_metrics):
        recs = []

        for r in risk_metrics["enrollment_risks"]:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Enrollment Issue",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_enrollment_ui_config(self, result):
        hero = [
            {"key": "course_count", "label": "Courses", "value_path": "course_count", "is_hero": True},
            {"key": "enrollment_risk_count", "label": "Risk Flags", "value_path": "enrollment_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "selected_courses", "name": "selected_courses", "title": "Selected Courses", "data_path": "selected_courses"},
            {"id": "enrollment_risks", "name": "enrollment_risks", "title": "Risks", "data_path": "enrollment_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Enrollment Summary", "metrics": ["course_count"], "charts": [], "tables": []},
            {"id": "courses", "title": "Courses", "metrics": [], "charts": [], "tables": ["selected_courses"]},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["enrollment_risk_count"], "charts": [], "tables": ["enrollment_risks", "recommendations"]},
        ]

        return {
            "hero_metrics": hero,
            "charts": [],
            "tables": tables,
            "sections": sections,
        }
