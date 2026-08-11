# app/services/analyzers/legal/will_testament_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class WillTestamentAnalyzer(BaseAnalyzer):
    """
    Analyzer for Last Will & Testament:
    - Testator, executor
    - Beneficiaries and bequests (simplified structure)
    - Guardianship clause presence
    - Residuary clause presence
    - Risk flags: missing executor, missing guardianship, missing residuary
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📜 Advanced Will & Testament analysis: {file_path}")

        will = self._extract_will(file_path, text, metadata)
        bequest_metrics = self._bequest_metrics(will)
        risk_metrics = self._risk_metrics(will, bequest_metrics)
        insights = self._generate_insights(will, bequest_metrics, risk_metrics)
        recommendations = self._generate_recommendations(will, bequest_metrics, risk_metrics)

        result = {
            "type": "will_testament",
            "summary": self._generate_summary(will),
            "confidence": 0.93,

            "testator_name": will.get("testator_name"),
            "executor_name": will.get("executor_name"),

            **bequest_metrics,
            **risk_metrics,

            "beneficiaries": will.get("beneficiaries", []),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_will_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_will(self, file_path, text, metadata) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_will_parser import UniversalWillParser
            parser = UniversalWillParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Will parsing failed: {e}")
            parsed = {}

        return {
            "testator_name": parsed.get("testator_name"),
            "executor_name": parsed.get("executor_name"),
            "beneficiaries": parsed.get("beneficiaries", []),  # [{name, share, specific_bequest}]
            "has_guardianship_clause": parsed.get("has_guardianship_clause"),
            "has_residuary_clause": parsed.get("has_residuary_clause"),
        }

    def _bequest_metrics(self, will: Dict[str, Any]) -> Dict[str, Any]:
        beneficiaries = will.get("beneficiaries", [])
        count = len(beneficiaries)

        return {
            "beneficiary_count": count,
        }

    def _risk_metrics(self, will: Dict[str, Any], bequest_metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []

        if not will.get("executor_name"):
            risks.append("Executor not clearly named.")

        if bequest_metrics.get("beneficiary_count", 0) == 0:
            risks.append("No beneficiaries listed.")

        if not will.get("has_guardianship_clause"):
            risks.append("No guardianship clause identified (if dependents exist).")

        if not will.get("has_residuary_clause"):
            risks.append("No residuary clause; remaining estate allocation may be unclear.")

        return {
            "will_risks": risks,
            "will_risk_count": len(risks),
        }

    def _generate_summary(self, will: Dict[str, Any]) -> str:
        name = will.get("testator_name", "Testator")
        ex = will.get("executor_name", "Executor")
        return f"Last Will & Testament of {name}, with {ex} named as executor."

    def _generate_insights(
        self,
        will: Dict[str, Any],
        bequest_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(f"Beneficiaries: {bequest_metrics.get('beneficiary_count', 0)}")

        if risk_metrics.get("will_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['will_risk_count']}.")

        return insights

    def _generate_recommendations(
        self,
        will: Dict[str, Any],
        bequest_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risk_metrics.get("will_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review Will Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_will_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "beneficiary_count", "label": "Beneficiaries", "value_path": "beneficiary_count", "is_hero": True},
            {"key": "will_risk_count", "label": "Risk Flags", "value_path": "will_risk_count", "is_hero": True},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "beneficiaries", "name": "beneficiaries", "title": "Beneficiaries", "data_path": "beneficiaries"},
            {"id": "will_risks", "name": "will_risks", "title": "Risks", "data_path": "will_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Will Summary",
                "metrics": ["beneficiary_count", "will_risk_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "beneficiaries",
                "title": "Beneficiaries",
                "metrics": [],
                "charts": [],
                "tables": ["beneficiaries"],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["will_risk_count"],
                "charts": [],
                "tables": ["will_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
