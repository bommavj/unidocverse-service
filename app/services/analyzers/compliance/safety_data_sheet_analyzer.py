import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class SafetyDataSheetAnalyzer(BaseAnalyzer):
    """
    Analyzer for Safety Data Sheets (SDS/MSDS):
    - Chemical name, CAS number, hazards, PPE, first aid
    - Exposure limits, storage instructions
    - Risk flags: missing hazard statements, missing PPE guidance
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"⚗️ SDS analysis: {file_path}")

        sds = self._extract_sds(file_path, text, metadata)
        metrics = self._metrics(sds)
        risks = self._risk_metrics(sds, metrics)
        insights = self._generate_insights(sds, metrics, risks)
        recommendations = self._generate_recommendations(sds, metrics, risks)

        result = {
            "type": "safety_data_sheet",
            "summary": self._generate_summary(sds),
            "confidence": 0.95,

            "chemical_name": sds.get("chemical_name"),
            "cas_number": sds.get("cas_number"),
            "manufacturer": sds.get("manufacturer"),
            **metrics,
            **risks,

            "sections": sds.get("sections", []),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_sds_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_sds(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_sds_parser import UniversalSDSParser
            parser = UniversalSDSParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ SDS parsing failed: {e}")
            parsed = {}

        return {
            "chemical_name": parsed.get("chemical_name"),
            "cas_number": parsed.get("cas_number"),
            "manufacturer": parsed.get("manufacturer"),
            "sections": parsed.get("sections", []),  # [{section_id, title, text}]
            "hazards": parsed.get("hazards", []),
            "ppe": parsed.get("ppe", []),
            "exposure_limits": parsed.get("exposure_limits", []),
        }

    def _metrics(self, sds: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "hazard_count": len(sds.get("hazards", [])),
            "ppe_count": len(sds.get("ppe", [])),
            "section_count": len(sds.get("sections", [])),
        }

    def _risk_metrics(self, sds: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks = []
        if metrics["hazard_count"] == 0:
            risks.append("No hazards detected; verify SDS completeness.")
        if metrics["ppe_count"] == 0:
            risks.append("No PPE guidance found.")
        if not sds.get("cas_number"):
            risks.append("CAS number missing.")
        return {"sds_risks": risks, "sds_risk_count": len(risks)}

    def _generate_summary(self, sds: Dict[str, Any]) -> str:
        chem = sds.get("chemical_name", "Chemical")
        cas = sds.get("cas_number", "N/A")
        return f"SDS for {chem} (CAS: {cas})."

    def _generate_insights(self, sds, metrics, risks) -> List[str]:
        insights = [
            f"Hazards listed: {metrics.get('hazard_count', 0)}",
            f"PPE recommendations: {metrics.get('ppe_count', 0)}",
        ]
        if risks.get("sds_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risks['sds_risk_count']}.")
        return insights

    def _generate_recommendations(self, sds, metrics, risks) -> List[Dict[str, Any]]:
        recs = []
        for r in risks.get("sds_risks", []):
            recs.append({"priority": "high", "category": "safety", "title": "SDS Issue", "description": r, "potential_savings": 0})
        return recs

    def build_sds_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "hazard_count", "label": "Hazards", "value_path": "hazard_count", "is_hero": True},
            {"key": "ppe_count", "label": "PPE Items", "value_path": "ppe_count", "is_hero": True},
            {"key": "sds_risk_count", "label": "Risk Flags", "value_path": "sds_risk_count", "is_hero": True},
        ]
        tables = [
            {"id": "sections", "name": "sections", "title": "SDS Sections", "data_path": "sections"},
            {"id": "sds_risks", "name": "sds_risks", "title": "Risks", "data_path": "sds_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]
        sections = [
            {"id": "summary", "title": "SDS Summary", "metrics": ["hazard_count", "ppe_count", "sds_risk_count"], "charts": [], "tables": []},
            {"id": "sections", "title": "Sections", "metrics": [], "charts": [], "tables": ["sections"]},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["sds_risk_count"], "charts": [], "tables": ["sds_risks", "recommendations"]},
        ]
        return {"hero_metrics": hero, "charts": [], "tables": tables, "sections": sections}
