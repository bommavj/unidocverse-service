import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PropertyDeedAnalyzer(BaseAnalyzer):
    """
    Analyzer for property deeds:
    - Extracts grantor, grantee, legal description, parcel id, recording date
    - Flags: missing legal description, ambiguous ownership, encumbrances
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🏡 Property deed analysis: {file_path}")

        deed = self._extract_deed(file_path, text, metadata)
        metrics = self._compute_metrics(deed)
        risks = self._risk_checks(deed, metrics)
        insights = self._generate_insights(deed, metrics, risks)
        recommendations = self._generate_recommendations(deed, metrics, risks)

        result = {
            "type": "property_deed",
            "summary": self._generate_summary(deed),
            "confidence": 0.95,

            "deed_id": deed.get("deed_id"),
            "grantor": deed.get("grantor"),
            "grantee": deed.get("grantee"),
            "property_address": deed.get("property_address"),
            "parcel_id": deed.get("parcel_id"),
            "recording_date": deed.get("recording_date"),
            "legal_description": deed.get("legal_description"),
            "encumbrances": deed.get("encumbrances", []),

            **metrics,
            **risks,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_deed_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_deed(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_deed_parser import UniversalDeedParser
            parser = UniversalDeedParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Deed parsing failed: {e}")
            parsed = {}

        return {
            "deed_id": parsed.get("deed_id") or metadata.get("deed_id"),
            "grantor": parsed.get("grantor"),
            "grantee": parsed.get("grantee"),
            "property_address": parsed.get("property_address"),
            "parcel_id": parsed.get("parcel_id"),
            "recording_date": parsed.get("recording_date"),
            "legal_description": parsed.get("legal_description"),
            "encumbrances": parsed.get("encumbrances", []),  # [{type, description}]
        }

    # ---------------- METRICS ----------------

    def _compute_metrics(self, deed: Dict[str, Any]) -> Dict[str, Any]:
        legal_present = bool(deed.get("legal_description"))
        enc_count = len(deed.get("encumbrances", []))
        recording_date_parsed = deed.get("recording_date")
        return {
            "legal_description_present": legal_present,
            "encumbrance_count": enc_count,
            "recording_date_parsed": recording_date_parsed,
        }

    # ---------------- RISK CHECKS ----------------

    def _risk_checks(self, deed: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        if not metrics.get("legal_description_present"):
            risks.append("Legal description missing; property boundaries unclear.")
        if metrics.get("encumbrance_count", 0) > 0:
            risks.append(f"{metrics['encumbrance_count']} encumbrance(s) listed; review for liens or easements.")
        if not deed.get("grantee"):
            risks.append("Grantee (owner) not clearly identified.")
        return {
            "deed_risks": risks,
            "deed_risk_count": len(risks),
        }

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, deed: Dict[str, Any]) -> str:
        addr = deed.get("property_address", "Property")
        grantee = deed.get("grantee", "Grantee")
        return f"Deed transferring {addr} to {grantee}."

    def _generate_insights(self, deed: Dict[str, Any], metrics: Dict[str, Any], risks: Dict[str, Any]) -> List[str]:
        insights: List[str] = []
        insights.append(f"Encumbrances: {metrics.get('encumbrance_count', 0)}")
        insights.append(f"Legal description present: {metrics.get('legal_description_present')}")
        if risks.get("deed_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risks['deed_risk_count']}.")
        return insights

    def _generate_recommendations(self, deed: Dict[str, Any], metrics: Dict[str, Any], risks: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risks.get("deed_risks", []):
            recs.append({
                "priority": "high" if "missing" in r.lower() or "unclear" in r.lower() else "medium",
                "category": "title",
                "title": "Deed Issue",
                "description": r,
                "potential_savings": 0,
            })
        if metrics.get("encumbrance_count", 0) > 0:
            recs.append({
                "priority": "high",
                "category": "title",
                "title": "Resolve Encumbrances",
                "description": "Investigate listed encumbrances (liens, easements) before transfer.",
                "potential_savings": 0,
            })
        return recs

    # ---------------- UI CONFIG ----------------

    def build_deed_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "legal_description_present", "label": "Legal Description", "value_path": "legal_description_present", "is_hero": True},
            {"key": "encumbrance_count", "label": "Encumbrances", "value_path": "encumbrance_count", "is_hero": True},
            {"key": "deed_risk_count", "label": "Risk Flags", "value_path": "deed_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "encumbrances", "name": "encumbrances", "title": "Encumbrances", "data_path": "encumbrances"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Deed Summary", "metrics": ["legal_description_present", "encumbrance_count", "deed_risk_count"], "charts": [], "tables": []},
            {"id": "encumbrances", "title": "Encumbrances", "metrics": [], "charts": [], "tables": ["encumbrances"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["deed_risk_count"], "charts": [], "tables": ["recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": [], "tables": tables, "sections": sections}
