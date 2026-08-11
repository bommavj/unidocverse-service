# app/services/analyzers/education/diploma_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class DiplomaAnalyzer(BaseAnalyzer):
    """
    Analyzer for diplomas:
    - Graduate name, degree type, institution
    - Honors, conferral date
    - Risk flags: missing degree type, missing institution
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]):
        logger.info(f"🎓 Diploma analysis: {file_path}")

        d = self._extract_diploma(file_path, text, metadata)
        metrics = self._diploma_metrics(d)
        risk_metrics = self._risk_metrics(d)
        insights = self._generate_insights(d, metrics, risk_metrics)
        recommendations = self._generate_recommendations(d, metrics, risk_metrics)

        result = {
            "type": "diploma",
            "summary": self._generate_summary(d),
            "confidence": 0.94,

            "graduate_name": d.get("graduate_name"),
            "degree_type": d.get("degree_type"),
            "institution": d.get("institution"),
            "conferral_date": d.get("conferral_date"),
            "honors": d.get("honors"),

            **metrics,
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_diploma_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_diploma(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_diploma_parser import UniversalDiplomaParser
            parser = UniversalDiplomaParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Diploma parsing failed: {e}")
            parsed = {}

        return {
            "graduate_name": parsed.get("graduate_name"),
            "degree_type": parsed.get("degree_type"),
            "institution": parsed.get("institution"),
            "conferral_date": parsed.get("conferral_date"),
            "honors": parsed.get("honors"),
        }

    def _diploma_metrics(self, d):
        return {
            "has_honors": bool(d.get("honors")),
        }

    def _risk_metrics(self, d):
        risks = []

        if not d.get("degree_type"):
            risks.append("Degree type not specified.")

        if not d.get("institution"):
            risks.append("Institution not specified.")

        return {
            "diploma_risks": risks,
            "diploma_risk_count": len(risks),
        }

    def _generate_summary(self, d):
        name = d.get("graduate_name", "Graduate")
        degree = d.get("degree_type", "Degree")
        return f"Diploma awarded to {name} for {degree}."

    def _generate_insights(self, d, metrics, risk_metrics):
        insights = []

        if metrics["has_honors"]:
            insights.append(f"Honors: {d.get('honors')}")

        if risk_metrics["diploma_risk_count"] > 0:
            insights.append(f"Risks identified: {risk_metrics['diploma_risk_count']}.")

        return insights

    def _generate_recommendations(self, d, metrics, risk_metrics):
        recs = []

        for r in risk_metrics["diploma_risks"]:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Diploma Data Issue",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_diploma_ui_config(self, result):
        hero = [
            {"key": "degree_type", "label": "Degree Type", "value_path": "degree_type", "is_hero": True},
            {"key": "has_honors", "label": "Honors", "value_path": "has_honors", "is_hero": True},
            {"key": "diploma_risk_count", "label": "Risk Flags", "value_path": "diploma_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "diploma_risks", "name": "diploma_risks", "title": "Risks", "data_path": "diploma_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Diploma Summary", "metrics": ["degree_type", "has_honors"], "charts": [], "tables": []},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["diploma_risk_count"], "charts": [], "tables": ["diploma_risks", "recommendations"]},
        ]

        return {
            "hero_metrics": hero,
            "charts": [],
            "tables": tables,
            "sections": sections,
        }
