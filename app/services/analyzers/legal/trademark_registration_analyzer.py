# app/services/analyzers/legal/trademark_registration_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class TrademarkRegistrationAnalyzer(BaseAnalyzer):
    """
    Analyzer for trademark registrations/applications:
    - Registration/application number, owner, mark
    - Classes of goods/services
    - Use in commerce date
    - Risk flags: limited classes, missing use/intent info
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"™ Advanced trademark registration analysis: {file_path}")

        tm = self._extract_trademark(file_path, text, metadata)
        metrics = self._tm_metrics(tm)
        risk_metrics = self._risk_metrics(tm, metrics)
        insights = self._generate_insights(tm, metrics, risk_metrics)
        recommendations = self._generate_recommendations(tm, metrics, risk_metrics)

        result = {
            "type": "trademark_registration",
            "summary": self._generate_summary(tm),
            "confidence": 0.93,

            "registration_number": tm.get("registration_number"),
            "application_number": tm.get("application_number"),
            "owner_name": tm.get("owner_name"),
            "mark_text": tm.get("mark_text"),

            **metrics,
            **risk_metrics,

            "classes": tm.get("classes", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_trademark_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_trademark(self, file_path, text, metadata) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_trademark_parser import UniversalTrademarkParser
            parser = UniversalTrademarkParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Trademark parsing failed: {e}")
            parsed = {}

        return {
            "registration_number": parsed.get("registration_number"),
            "application_number": parsed.get("application_number") or metadata.get("application_number"),
            "owner_name": parsed.get("owner_name"),
            "mark_text": parsed.get("mark_text"),
            "classes": parsed.get("classes", []),  # [{class_code, description}]
            "first_use_date": parsed.get("first_use_date"),
            "first_use_in_commerce_date": parsed.get("first_use_in_commerce_date"),
            "filing_basis": parsed.get("filing_basis"),  # 1A, 1B, etc.
        }

    def _tm_metrics(self, tm: Dict[str, Any]) -> Dict[str, Any]:
        classes = tm.get("classes", [])

        return {
            "class_count": len(classes),
        }

    def _risk_metrics(self, tm: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []

        if metrics.get("class_count", 0) == 0:
            risks.append("No classes of goods/services detected.")

        if not tm.get("first_use_in_commerce_date") and tm.get("filing_basis") == "1A":
            risks.append("Use-in-commerce basis claimed but no use date detected.")

        return {
            "tm_risks": risks,
            "tm_risk_count": len(risks),
        }

    def _generate_summary(self, tm: Dict[str, Any]) -> str:
        mark = tm.get("mark_text", "Mark")
        owner = tm.get("owner_name", "Owner")
        reg = tm.get("registration_number") or tm.get("application_number", "N/A")
        return f"Trademark '{mark}' owned by {owner}, registration/application {reg}."

    def _generate_insights(
        self,
        tm: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(f"Classes of goods/services: {metrics.get('class_count', 0)}")

        if risk_metrics.get("tm_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['tm_risk_count']}.")

        return insights

    def _generate_recommendations(
        self,
        tm: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risk_metrics.get("tm_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review Trademark Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_trademark_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "class_count", "label": "Classes", "value_path": "class_count", "is_hero": True},
            {"key": "tm_risk_count", "label": "Risk Flags", "value_path": "tm_risk_count", "is_hero": True},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "classes", "name": "classes", "title": "Classes", "data_path": "classes"},
            {"id": "tm_risks", "name": "tm_risks", "title": "Risks", "data_path": "tm_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Trademark Summary",
                "metrics": ["class_count", "tm_risk_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "classes",
                "title": "Classes",
                "metrics": [],
                "charts": [],
                "tables": ["classes"],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["tm_risk_count"],
                "charts": [],
                "tables": ["tm_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
