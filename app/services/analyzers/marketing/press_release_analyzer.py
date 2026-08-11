# app/services/analyzers/marketing/press_release_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PressReleaseAnalyzer(BaseAnalyzer):
    """
    Analyzer for press releases:
    - Extracts headline, dateline, lead paragraph, boilerplate, contact info
    - Checks for newsworthiness signals, missing contact, missing embargo info
    - Produces readability metrics, sentiment summary, and distribution recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📰 Press release analysis: {file_path}")

        pr = self._extract_press_release(file_path, text, metadata)
        metrics = self._compute_metrics(pr)
        risk_metrics = self._risk_metrics(pr, metrics)
        insights = self._generate_insights(pr, metrics, risk_metrics)
        recommendations = self._generate_recommendations(pr, metrics, risk_metrics)

        result = {
            "type": "press_release",
            "summary": self._generate_summary(pr),
            "confidence": 0.93,

            "headline": pr.get("headline"),
            "dateline": pr.get("dateline"),
            "lead": pr.get("lead"),
            "boilerplate": pr.get("boilerplate"),
            "contacts": pr.get("contacts", []),

            **metrics,
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_pr_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_press_release(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_press_release_parser import UniversalPressReleaseParser
            parser = UniversalPressReleaseParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Press release parsing failed: {e}")
            parsed = {}

        return {
            "headline": parsed.get("headline"),
            "dateline": parsed.get("dateline"),
            "lead": parsed.get("lead"),
            "body": parsed.get("body", ""),
            "boilerplate": parsed.get("boilerplate"),
            "contacts": parsed.get("contacts", []),  # [{name, title, email, phone}]
            "embargo": parsed.get("embargo"),
        }

    # Metrics
    def _compute_metrics(self, pr: Dict[str, Any]) -> Dict[str, Any]:
        body = pr.get("body", "")
        word_count = len(body.split())
        reading_level = self._flesch_kincaid(body)
        has_headline = bool(pr.get("headline"))
        has_contact = len(pr.get("contacts", [])) > 0
        return {
            "word_count": word_count,
            "reading_level": reading_level,
            "has_headline": has_headline,
            "has_contact": has_contact,
        }

    def _flesch_kincaid(self, text: str) -> float:
        # lightweight readability heuristic; real implementation should use a library
        words = max(len(text.split()), 1)
        sentences = max(text.count(".") + text.count("!") + text.count("?"), 1)
        syllables = max(sum(len(w) // 3 for w in text.split()), 1)
        # simplified FK formula
        score = 206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words)
        return round(score, 2)

    # Risk checks
    def _risk_metrics(self, pr: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        if not metrics.get("has_headline"):
            risks.append("Missing headline.")
        if not metrics.get("has_contact"):
            risks.append("No media contact provided.")
        if pr.get("embargo"):
            risks.append("Embargo present; ensure distribution timing is controlled.")
        if metrics.get("word_count", 0) > 800:
            risks.append("Press release is long; consider a shorter release and separate backgrounder.")
        return {"pr_risks": risks, "pr_risk_count": len(risks)}

    # Summary, insights, recommendations
    def _generate_summary(self, pr: Dict[str, Any]) -> str:
        headline = pr.get("headline") or "Press Release"
        return f"Press release: {headline}"

    def _generate_insights(self, pr: Dict[str, Any], metrics: Dict[str, Any], risk_metrics: Dict[str, Any]) -> List[str]:
        insights: List[str] = []
        insights.append(f"Word count: {metrics.get('word_count', 0)}")
        insights.append(f"Reading score: {metrics.get('reading_level')}")
        if risk_metrics.get("pr_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['pr_risk_count']}.")
        return insights

    def _generate_recommendations(self, pr: Dict[str, Any], metrics: Dict[str, Any], risk_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        if not metrics.get("has_contact"):
            recs.append({"priority": "high", "category": "distribution", "title": "Add Media Contact", "description": "Include at least one media contact with email and phone.", "potential_savings": 0})
        if metrics.get("word_count", 0) > 800:
            recs.append({"priority": "medium", "category": "editing", "title": "Shorten Release", "description": "Trim body and move background details to a separate document.", "potential_savings": 0})
        if pr.get("embargo"):
            recs.append({"priority": "medium", "category": "process", "title": "Confirm Embargo", "description": "Verify embargo date/time and coordinate distribution list.", "potential_savings": 0})
        return recs

    # UI config
    def build_pr_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "word_count", "label": "Word Count", "value_path": "word_count", "is_hero": True},
            {"key": "reading_level", "label": "Reading Score", "value_path": "reading_level", "is_hero": True},
            {"key": "pr_risk_count", "label": "Risk Flags", "value_path": "pr_risk_count", "is_hero": True},
        ]

        charts = []

        tables = [
            {"id": "contacts", "name": "contacts", "title": "Media Contacts", "data_path": "contacts"},
            {"id": "pr_risks", "name": "pr_risks", "title": "Risks", "data_path": "pr_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Press Release Summary", "metrics": ["word_count", "reading_level", "pr_risk_count"], "charts": [], "tables": []},
            {"id": "content", "title": "Content", "metrics": [], "charts": [], "tables": ["contacts"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["pr_risk_count"], "charts": [], "tables": ["pr_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
