# app/services/analyzers/legal/patent_application_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PatentApplicationAnalyzer(BaseAnalyzer):
    """
    Analyzer for patent applications:
    - Application number, inventor(s)
    - Claim count
    - Prior art references count
    - Risk flags: few claims, missing abstract/summary
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🧬 Advanced patent application analysis: {file_path}")

        app = self._extract_patent(file_path, text, metadata)
        metrics = self._patent_metrics(app)
        risk_metrics = self._risk_metrics(app, metrics)
        insights = self._generate_insights(app, metrics, risk_metrics)
        recommendations = self._generate_recommendations(app, metrics, risk_metrics)

        result = {
            "type": "patent_application",
            "summary": self._generate_summary(app),
            "confidence": 0.93,

            "patent_number": app.get("patent_number"),
            "application_number": app.get("application_number"),
            "inventor_name": app.get("inventor_name"),
            **metrics,
            **risk_metrics,

            "claims": app.get("claims", []),
            "prior_art_references": app.get("prior_art_references", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_patent_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_patent(self, file_path, text, metadata) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_patent_parser import UniversalPatentParser
            parser = UniversalPatentParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Patent application parsing failed: {e}")
            parsed = {}

        return {
            "patent_number": parsed.get("patent_number"),
            "application_number": parsed.get("application_number") or metadata.get("application_number"),
            "inventor_name": parsed.get("inventor_name"),
            "claims": parsed.get("claims", []),  # list of claim texts
            "prior_art_references": parsed.get("prior_art_references", []),
            "has_abstract": parsed.get("has_abstract"),
            "has_summary": parsed.get("has_summary"),
        }

    def _patent_metrics(self, app: Dict[str, Any]) -> Dict[str, Any]:
        claims = app.get("claims", [])
        refs = app.get("prior_art_references", [])

        return {
            "claim_count": len(claims),
            "prior_art_reference_count": len(refs),
        }

    def _risk_metrics(self, app: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []

        if metrics.get("claim_count", 0) < 5:
            risks.append("Low claim count; scope of protection may be narrow.")

        if not app.get("has_abstract"):
            risks.append("Abstract section appears missing or incomplete.")

        if not app.get("has_summary"):
            risks.append("Summary of the invention appears missing or incomplete.")

        return {
            "patent_risks": risks,
            "patent_risk_count": len(risks),
        }

    def _generate_summary(self, app: Dict[str, Any]) -> str:
        num = app.get("application_number", "N/A")
        inventor = app.get("inventor_name", "Inventor")
        return f"Patent application {num} filed by {inventor}."

    def _generate_insights(
        self,
        app: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(f"Claims: {metrics.get('claim_count', 0)}")
        insights.append(f"Prior art references: {metrics.get('prior_art_reference_count', 0)}")

        if risk_metrics.get("patent_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['patent_risk_count']}.")

        return insights

    def _generate_recommendations(
        self,
        app: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risk_metrics.get("patent_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review Patent Application Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_patent_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "claim_count", "label": "Claims", "value_path": "claim_count", "is_hero": True},
            {"key": "prior_art_reference_count", "label": "Prior Art References", "value_path": "prior_art_reference_count", "is_hero": True},
            {"key": "patent_risk_count", "label": "Risk Flags", "value_path": "patent_risk_count", "is_hero": True},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "claims", "name": "claims", "title": "Claims", "data_path": "claims"},
            {"id": "prior_art_references", "name": "prior_art_references", "title": "Prior Art References", "data_path": "prior_art_references"},
            {"id": "patent_risks", "name": "patent_risks", "title": "Risks", "data_path": "patent_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Patent Application Summary",
                "metrics": ["claim_count", "prior_art_reference_count", "patent_risk_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "claims",
                "title": "Claims",
                "metrics": [],
                "charts": [],
                "tables": ["claims"],
            },
            {
                "id": "prior_art",
                "title": "Prior Art References",
                "metrics": [],
                "charts": [],
                "tables": ["prior_art_references"],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["patent_risk_count"],
                "charts": [],
                "tables": ["patent_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
