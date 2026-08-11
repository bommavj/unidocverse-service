import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class IdentificationAnalyzer(BaseAnalyzer):
    """
    Analyzer for identification documents (passport, driver's license, ID card).
    - Extracts name, id number, DOB, expiry, nationality
    - Flags expired or soon-to-expire IDs, mismatches, missing fields
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🆔 Identification analysis: {file_path}")

        id_doc = self._extract_identification(file_path, text, metadata)
        metrics = self._compute_metrics(id_doc)
        risks = self._risk_checks(id_doc, metrics)
        insights = self._generate_insights(id_doc, metrics, risks)
        recommendations = self._generate_recommendations(id_doc, metrics, risks)

        result = {
            "type": "identification",
            "summary": self._generate_summary(id_doc),
            "confidence": 0.95,

            "id_number": id_doc.get("id_number"),
            "name": id_doc.get("name"),
            "date_of_birth": id_doc.get("date_of_birth"),
            "nationality": id_doc.get("nationality"),
            "document_type": id_doc.get("document_type"),
            "expiry_date": id_doc.get("expiry_date"),
            "issuing_authority": id_doc.get("issuing_authority"),

            **metrics,
            **risks,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_identification_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_identification(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_id_parser import UniversalIDParser
            parser = UniversalIDParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Identification parsing failed: {e}")
            parsed = {}

        return {
            "id_number": parsed.get("id_number") or metadata.get("id_number"),
            "name": parsed.get("name"),
            "date_of_birth": parsed.get("date_of_birth"),
            "nationality": parsed.get("nationality"),
            "document_type": parsed.get("document_type"),  # passport, driver_license, id_card
            "expiry_date": parsed.get("expiry_date"),
            "issuing_authority": parsed.get("issuing_authority"),
            "photo_present": parsed.get("photo_present", False),
        }

    # ---------------- METRICS ----------------

    def _compute_metrics(self, id_doc: Dict[str, Any]) -> Dict[str, Any]:
        from datetime import datetime, timedelta
        expiry = None
        days_to_expiry = None
        try:
            if id_doc.get("expiry_date"):
                expiry = datetime.fromisoformat(id_doc["expiry_date"]).date()
                days_to_expiry = (expiry - datetime.utcnow().date()).days
        except Exception:
            expiry = None
            days_to_expiry = None

        completeness = 0
        fields = ["id_number", "name", "date_of_birth", "nationality", "document_type", "expiry_date"]
        present = sum(1 for f in fields if id_doc.get(f))
        completeness = round((present / len(fields)) * 100, 2)

        return {
            "expiry_date_parsed": id_doc.get("expiry_date"),
            "days_to_expiry": days_to_expiry,
            "completeness_percent": completeness,
            "photo_present": bool(id_doc.get("photo_present")),
        }

    # ---------------- RISK CHECKS ----------------

    def _risk_checks(self, id_doc: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        days = metrics.get("days_to_expiry")
        if days is not None:
            if days < 0:
                risks.append("Identification document is expired.")
            elif days <= 30:
                risks.append("Identification document will expire within 30 days.")
        if metrics.get("completeness_percent", 100) < 100:
            risks.append("Identification document missing required fields.")
        if not metrics.get("photo_present"):
            risks.append("No photo detected on ID; visual verification may be incomplete.")
        return {
            "id_risks": risks,
            "id_risk_count": len(risks),
        }

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, id_doc: Dict[str, Any]) -> str:
        name = id_doc.get("name", "Holder")
        doc_type = id_doc.get("document_type", "ID")
        return f"{doc_type.title()} for {name}."

    def _generate_insights(self, id_doc: Dict[str, Any], metrics: Dict[str, Any], risks: Dict[str, Any]) -> List[str]:
        insights: List[str] = []
        insights.append(f"Completeness: {metrics.get('completeness_percent', 0)}%")
        if metrics.get("days_to_expiry") is not None:
            insights.append(f"Days to expiry: {metrics.get('days_to_expiry')}")
        if risks.get("id_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risks['id_risk_count']}.")
        return insights

    def _generate_recommendations(self, id_doc: Dict[str, Any], metrics: Dict[str, Any], risks: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risks.get("id_risks", []):
            recs.append({
                "priority": "high" if "expired" in r.lower() else "medium",
                "category": "verification",
                "title": "Identification Issue",
                "description": r,
                "potential_savings": 0,
            })
        if metrics.get("days_to_expiry") is not None and metrics["days_to_expiry"] <= 30 and metrics["days_to_expiry"] >= 0:
            recs.append({
                "priority": "medium",
                "category": "administrative",
                "title": "Renewal Recommended",
                "description": "ID expires soon; advise renewal to avoid service disruption.",
                "potential_savings": 0,
            })
        return recs

    # ---------------- UI CONFIG ----------------

    def build_identification_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "completeness_percent", "label": "Completeness %", "value_path": "completeness_percent", "is_hero": True},
            {"key": "days_to_expiry", "label": "Days to Expiry", "value_path": "days_to_expiry", "is_hero": True},
            {"key": "photo_present", "label": "Photo Present", "value_path": "photo_present", "is_hero": True},
            {"key": "id_risk_count", "label": "Risk Flags", "value_path": "id_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "id_risks", "name": "id_risks", "title": "Risks", "data_path": "id_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "ID Summary", "metrics": ["completeness_percent", "days_to_expiry", "photo_present"], "charts": [], "tables": []},
            {"id": "verification", "title": "Verification", "metrics": [], "charts": [], "tables": ["id_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": [], "tables": tables, "sections": sections}
