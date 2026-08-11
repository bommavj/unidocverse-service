# app/services/analyzers/education/transcript_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class TranscriptAnalyzer(BaseAnalyzer):
    """
    Analyzer for academic transcripts:
    - Student info
    - GPA, credits earned, course count
    - Semester breakdown
    - Academic risks (low GPA, failed courses)
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]):
        logger.info(f"🎓 Transcript analysis: {file_path}")

        t = self._extract_transcript(file_path, text, metadata)

        metrics = self._academic_metrics(t)
        semester_metrics = self._semester_metrics(t)
        risk_metrics = self._risk_metrics(t, metrics)
        insights = self._generate_insights(t, metrics, risk_metrics)
        recommendations = self._generate_recommendations(t, metrics, risk_metrics)

        result = {
            "type": "transcript",
            "summary": self._generate_summary(t, metrics),
            "confidence": 0.94,

            "student_id": t.get("student_id"),
            "student_name": t.get("student_name"),
            "program": t.get("program"),
            "institution": t.get("institution"),

            **metrics,
            **semester_metrics,
            **risk_metrics,

            "courses": t.get("courses", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_transcript_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_transcript(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_transcript_parser import UniversalTranscriptParser
            parser = UniversalTranscriptParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Transcript parsing failed: {e}")
            parsed = {}

        return {
            "student_id": parsed.get("student_id"),
            "student_name": parsed.get("student_name"),
            "program": parsed.get("program"),
            "institution": parsed.get("institution"),
            "gpa": self.safe_float(parsed.get("gpa")),
            "credits_earned": self.safe_float(parsed.get("credits_earned")),
            "courses": parsed.get("courses", []),  # [{course, grade, credits, semester}]
        }

    # ---------------- METRICS ----------------

    def _academic_metrics(self, t):
        courses = t.get("courses", [])
        failed = [c for c in courses if str(c.get("grade", "")).upper() in ["F", "E"]]

        return {
            "gpa": t.get("gpa", 0.0),
            "credits_earned": t.get("credits_earned", 0.0),
            "course_count": len(courses),
            "failed_course_count": len(failed),
        }

    def _semester_metrics(self, t):
        semesters = {}
        for c in t.get("courses", []):
            sem = c.get("semester", "Unknown")
            semesters[sem] = semesters.get(sem, 0) + 1

        return {
            "semester_breakdown": semesters,
        }

    # ---------------- RISK ----------------

    def _risk_metrics(self, t, metrics):
        risks = []

        if metrics["gpa"] < 2.0:
            risks.append("Low GPA (below 2.0).")

        if metrics["failed_course_count"] > 0:
            risks.append(f"{metrics['failed_course_count']} failed courses detected.")

        return {
            "transcript_risks": risks,
            "transcript_risk_count": len(risks),
        }

    # ---------------- SUMMARY / INSIGHTS ----------------

    def _generate_summary(self, t, metrics):
        name = t.get("student_name", "Student")
        gpa = metrics.get("gpa", 0.0)
        return f"Transcript for {name} with GPA {gpa:.2f}."

    def _generate_insights(self, t, metrics, risk_metrics):
        insights = [
            f"GPA: {metrics['gpa']:.2f}",
            f"Credits earned: {metrics['credits_earned']}",
            f"Courses taken: {metrics['course_count']}",
        ]

        if risk_metrics["transcript_risk_count"] > 0:
            insights.append(f"Risks identified: {risk_metrics['transcript_risk_count']}.")

        return insights

    def _generate_recommendations(self, t, metrics, risk_metrics):
        recs = []

        for r in risk_metrics["transcript_risks"]:
            recs.append({
                "priority": "medium",
                "category": "academic",
                "title": "Academic Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ---------------- UI CONFIG ----------------

    def build_transcript_ui_config(self, result):
        hero = [
            {"key": "gpa", "label": "GPA", "value_path": "gpa", "is_hero": True},
            {"key": "credits_earned", "label": "Credits Earned", "value_path": "credits_earned", "is_hero": True},
            {"key": "course_count", "label": "Courses", "value_path": "course_count", "is_hero": True},
            {"key": "transcript_risk_count", "label": "Risk Flags", "value_path": "transcript_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "courses", "name": "courses", "title": "Courses", "data_path": "courses"},
            {"id": "transcript_risks", "name": "transcript_risks", "title": "Risks", "data_path": "transcript_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Transcript Summary", "metrics": ["gpa", "credits_earned", "course_count"], "charts": [], "tables": []},
            {"id": "courses", "title": "Courses", "metrics": [], "charts": [], "tables": ["courses"]},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["transcript_risk_count"], "charts": [], "tables": ["transcript_risks", "recommendations"]},
        ]

        return {
            "hero_metrics": hero,
            "charts": [],
            "tables": tables,
            "sections": sections,
        }
