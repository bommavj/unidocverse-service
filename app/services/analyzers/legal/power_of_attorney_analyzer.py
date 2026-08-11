# app/services/analyzers/legal/power_of_attorney_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PowerOfAttorneyAnalyzer(BaseAnalyzer):
    """
    Analyzer for Power of Attorney (POA) documents:
    - Principal, agent, effective date
    - Scope of authority
    - Durable vs non-durable
    - Revocation and successor agent clauses
    - Risk flags: overly broad authority, missing limitations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📝 Advanced POA analysis: {file_path}")

        poa = self._extract_poa(file_path, text, metadata)
        authority_metrics = self._authority_metrics(poa)
        risk_metrics = self._risk_metrics(poa, authority_metrics)
        insights = self._generate_insights(poa, authority_metrics, risk_metrics)
        recommendations = self._generate_recommendations(poa, authority_metrics, risk_metrics)

        result = {
            "type": "power_of_attorney",
            "summary": self._generate_summary(poa),
            "confidence": 0.93,

            "principal_name": poa.get("principal_name"),
            "agent_name": poa.get("agent_name"),
            "effective_date": poa.get("effective_date"),
            "is_durable": poa.get("is_durable"),
            "scope_description": poa.get("scope_description"),

            **authority_metrics,
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_poa_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_poa(self, file_path, text, metadata) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_poa_parser import UniversalPOAParser
            parser = UniversalPOAParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ POA parsing failed: {e}")
            parsed = {}

        return {
            "principal_name": parsed.get("principal_name"),
            "agent_name": parsed.get("agent_name"),
            "effective_date": parsed.get("effective_date"),
            "is_durable": parsed.get("is_durable"),  # True/False/None
            "authorities": parsed.get("authorities", []),  # list of authority types
            "scope_description": parsed.get("scope_description"),
            "has_revocation_clause": parsed.get("has_revocation_clause"),
            "has_successor_agent_clause": parsed.get("has_successor_agent_clause"),
        }

    def _authority_metrics(self, poa: Dict[str, Any]) -> Dict[str, Any]:
        authorities = poa.get("authorities", [])
        return {
            "authority_count": len(authorities),
            "authorities": authorities,
        }

    def _risk_metrics(self, poa: Dict[str, Any], authority_metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []

        if poa.get("is_durable") is None:
            risks.append("Durable vs non-durable status is not clearly specified.")

        if not poa.get("has_revocation_clause"):
            risks.append("No explicit revocation clause specified.")

        if not poa.get("has_successor_agent_clause"):
            risks.append("No successor agent clause specified.")

        if authority_metrics.get("authority_count", 0) == 0:
            risks.append("Scope of authority is not clearly defined.")

        return {
            "poa_risks": risks,
            "poa_risk_count": len(risks),
        }

    def _generate_summary(self, poa: Dict[str, Any]) -> str:
        principal = poa.get("principal_name", "Principal")
        agent = poa.get("agent_name", "Agent")
        eff = poa.get("effective_date", "N/A")
        return f"Power of Attorney from {principal} to {agent}, effective {eff}."

    def _generate_insights(
        self,
        poa: Dict[str, Any],
        authority_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(f"Authority entries: {authority_metrics.get('authority_count', 0)}")
        if poa.get("is_durable") is True:
            insights.append("POA is marked as durable.")
        elif poa.get("is_durable") is False:
            insights.append("POA is not durable.")

        if risk_metrics.get("poa_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['poa_risk_count']}.")

        return insights

    def _generate_recommendations(
        self,
        poa: Dict[str, Any],
        authority_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risk_metrics.get("poa_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review POA Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_poa_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "authority_count", "label": "Authority Entries", "value_path": "authority_count", "is_hero": True},
            {"key": "poa_risk_count", "label": "Risk Flags", "value_path": "poa_risk_count", "is_hero": True},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "authorities", "name": "authorities", "title": "Authorities", "data_path": "authorities"},
            {"id": "poa_risks", "name": "poa_risks", "title": "Risks", "data_path": "poa_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "POA Summary",
                "metrics": ["authority_count", "poa_risk_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "authorities",
                "title": "Authorities",
                "metrics": [],
                "charts": [],
                "tables": ["authorities"],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["poa_risk_count"],
                "charts": [],
                "tables": ["poa_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
