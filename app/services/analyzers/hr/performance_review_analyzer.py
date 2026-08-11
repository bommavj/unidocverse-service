# app/services/analyzers/hr/performance_review_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PerformanceReviewAnalyzer(BaseAnalyzer):
    """
    Analyzer for performance reviews:
    - Overall rating
    - Competency ratings
    - Goals completed / open
    - Sentiment of feedback (basic numeric score if provided by parser)
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📊 Advanced performance review analysis: {file_path}")

        review = self._extract_review(file_path, text, metadata)

        metrics = self._rating_metrics(review)
        goals_metrics = self._goals_metrics(review)
        insights = self._generate_insights(review, metrics, goals_metrics)
        recommendations = self._generate_recommendations(review, metrics, goals_metrics)

        result = {
            "type": "performance_review",
            "summary": self._generate_summary(review, metrics),
            "confidence": 0.9,

            "employee_id": review.get("employee_id"),
            "employee_name": review.get("employee_name"),
            "reviewer_name": review.get("reviewer_name"),
            "review_period": review.get("review_period"),

            **metrics,
            **goals_metrics,

            "competencies": review.get("competencies", []),
            "goals": review.get("goals", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_review_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_review(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_performance_parser import UniversalPerformanceParser
            parser = UniversalPerformanceParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Performance review parsing failed: {e}")
            parsed = {}

        return {
            "employee_id": parsed.get("employee_id"),
            "employee_name": parsed.get("employee_name"),
            "reviewer_name": parsed.get("reviewer_name"),
            "review_period": parsed.get("review_period"),
            "overall_rating": self._safe_float(parsed.get("overall_rating")),
            "rating_scale_max": self._safe_float(parsed.get("rating_scale_max", 5)),
            "competencies": parsed.get("competencies", []),  # [{name, rating, max_rating}]
            "goals": parsed.get("goals", []),  # [{title, status, progress}]
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

    def _rating_metrics(self, review: Dict[str, Any]) -> Dict[str, Any]:
        overall = review.get("overall_rating", 0.0)
        max_scale = review.get("rating_scale_max", 5.0)

        comps = review.get("competencies", [])
        comp_scores = [self._safe_float(c.get("rating", 0)) for c in comps]
        avg_comp = sum(comp_scores) / len(comp_scores) if comp_scores else 0.0

        return {
            "overall_rating": overall,
            "rating_scale_max": max_scale,
            "overall_rating_percent": round((overall / max_scale) * 100, 2) if max_scale else None,
            "average_competency_rating": round(avg_comp, 2),
        }

    def _goals_metrics(self, review: Dict[str, Any]) -> Dict[str, Any]:
        goals = review.get("goals", [])
        total = len(goals)
        completed = sum(1 for g in goals if str(g.get("status", "")).lower() in ["completed", "done"])
        in_progress = sum(1 for g in goals if str(g.get("status", "")).lower() in ["in progress", "ongoing"])
        not_started = total - completed - in_progress

        return {
            "goals_total": total,
            "goals_completed": completed,
            "goals_in_progress": in_progress,
            "goals_not_started": not_started,
        }

    def _generate_summary(self, review, metrics) -> str:
        name = review.get("employee_name", "Employee")
        rating = metrics.get("overall_rating", 0.0)
        max_scale = metrics.get("rating_scale_max", 5.0)
        return f"Performance review for {name} with an overall rating of {rating:.1f}/{max_scale:.1f}."

    def _generate_insights(self, review, metrics, goals_metrics) -> List[str]:
        insights = []

        insights.append(
            f"Overall rating: {metrics.get('overall_rating', 0.0):.1f} / {metrics.get('rating_scale_max', 5.0):.1f}"
        )
        insights.append(
            f"Average competency rating: {metrics.get('average_competency_rating', 0.0):.2f}"
        )

        if goals_metrics["goals_total"] > 0:
            insights.append(
                f"Goals: {goals_metrics['goals_completed']} completed, "
                f"{goals_metrics['goals_in_progress']} in progress, "
                f"{goals_metrics['goals_not_started']} not started."
            )

        return insights

    def _generate_recommendations(self, review, metrics, goals_metrics) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        overall = metrics.get("overall_rating", 0.0)
        max_scale = metrics.get("rating_scale_max", 5.0)

        if overall < max_scale * 0.6:
            recs.append({
                "priority": "high",
                "category": "performance",
                "title": "Performance Improvement Plan",
                "description": "Overall rating is below expected threshold; consider a structured development plan.",
                "potential_savings": 0,
            })

        if goals_metrics["goals_not_started"] > 0:
            recs.append({
                "priority": "medium",
                "category": "goals",
                "title": "Activate Pending Goals",
                "description": f"{goals_metrics['goals_not_started']} goals not yet started.",
                "potential_savings": 0,
            })

        return recs

    def build_review_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "overall_rating", "label": "Overall Rating", "value_path": "overall_rating", "is_hero": True},
            {"key": "overall_rating_percent", "label": "Rating (%)", "value_path": "overall_rating_percent", "is_hero": True},
            {"key": "average_competency_rating", "label": "Avg Competency Rating", "value_path": "average_competency_rating", "is_hero": True},
            {"key": "goals_total", "label": "Total Goals", "value_path": "goals_total", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "goals_completed", "label": "Goals Completed", "value_path": "goals_completed", "is_hero": False},
            {"key": "goals_in_progress", "label": "Goals In Progress", "value_path": "goals_in_progress", "is_hero": False},
            {"key": "goals_not_started", "label": "Goals Not Started", "value_path": "goals_not_started", "is_hero": False},
        ]

        charts: List[Dict[str, Any]] = []  # could add goal status donut later

        tables = [
            {"id": "competencies", "name": "competencies", "title": "Competency Ratings", "data_path": "competencies"},
            {"id": "goals", "name": "goals", "title": "Goals", "data_path": "goals"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Review Summary",
                "metrics": ["overall_rating", "overall_rating_percent", "average_competency_rating"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "competencies",
                "title": "Competencies",
                "metrics": [],
                "charts": [],
                "tables": ["competencies"],
            },
            {
                "id": "goals",
                "title": "Goals",
                "metrics": ["goals_total", "goals_completed", "goals_in_progress", "goals_not_started"],
                "charts": [],
                "tables": ["goals"],
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
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
