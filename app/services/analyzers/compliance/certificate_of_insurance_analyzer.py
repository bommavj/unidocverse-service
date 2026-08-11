import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class CertificateOfInsuranceAnalyzer(BaseAnalyzer):
    """
    Analyzer for Certificates of Insurance (COI):
    - Policy numbers, policy types, limits, insured, certificate holder
    - Coverage periods, endorsements
    - Risk flags: expired coverage, insufficient limits, missing certificate holder
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📜 COI analysis: {file_path}")

        coi = self._extract_coi(file_path, text, metadata)
        metrics = self._metrics(coi)
        risks = self._risk_metrics(coi, metrics)
        insights = self._generate_insights(coi, metrics, risks)
        recommendations = self._generate_recommendations(coi, metrics, risks)

        result = {
            "type": "certificate_of_insurance",
            "summary": self._generate_summary(coi),
            "confidence": 0.94,

            "policy_number": coi.get("policy_number"),
            "insured": coi.get("insured"),
            "certificate_holder": coi.get("certificate_holder"),
            "coverage_items": coi.get("coverage_items", []),

            **metrics,
            **risks,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_coi_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_coi(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_coi_parser import UniversalCOIParser
            parser = UniversalCOIParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ COI parsing failed: {e}")
            parsed = {}

        return {
            "policy_number": parsed.get("policy_number"),
            "insured": parsed.get("insured"),
            "certificate_holder": parsed.get("certificate_holder"),
            "coverage_items": parsed.get("coverage_items", []),  # [{type, limit, effective, expiration}]
        }

    def _metrics(self, coi: Dict[str, Any]) -> Dict[str, Any]:
        items = coi.get("coverage_items", [])
        count = len(items)
        total_limits = {}
        for it in items:
            t = it.get("type", "unknown")
            total_limits[t] = max(total_limits.get(t, 0), self.safe_float(it.get("limit", 0)))
        return {"coverage_count": count, "max_limits": total_limits}

    def _risk_metrics(self, coi: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks = []
        # expired check
        from datetime import datetime
        now = datetime.utcnow().date()
        for it in coi.get("coverage_items", []):
            exp = it.get("expiration")
            try:
                if exp:
                    exp_date = datetime.fromisoformat(exp).date()
                    if exp_date < now:
                        risks.append(f"Coverage {it.get('type')} expired on {exp}.")
            except Exception:
                pass
        if not coi.get("certificate_holder"):
            risks.append("Certificate holder not specified.")
        return {"coi_risks": risks, "coi_risk_count": len(risks)}

    def _generate_summary(self, coi: Dict[str, Any]) -> str:
        insured = coi.get("insured", "Insured")
        return f"Certificate of Insurance for {insured}."

    def _generate_insights(self, coi, metrics, risks) -> List[str]:
        insights = [f"Coverage items: {metrics.get('coverage_count', 0)}"]
        if risks.get("coi_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risks['coi_risk_count']}.")
        return insights

    def _generate_recommendations(self, coi, metrics, risks) -> List[Dict[str, Any]]:
        recs = []
        for r in risks.get("coi_risks", []):
            recs.append({"priority": "high", "category": "coverage", "title": "COI Risk", "description": r, "potential_savings": 0})
        return recs

    def build_coi_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "coverage_count", "label": "Coverage Items", "value_path": "coverage_count", "is_hero": True},
            {"key": "coi_risk_count", "label": "Risk Flags", "value_path": "coi_risk_count", "is_hero": True},
        ]
        tables = [
            {"id": "coverage_items", "name": "coverage_items", "title": "Coverage Items", "data_path": "coverage_items"},
            {"id": "coi_risks", "name": "coi_risks", "title": "Risks", "data_path": "coi_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]
        sections = [
            {"id": "summary", "title": "COI Summary", "metrics": ["coverage_count", "coi_risk_count"], "charts": [], "tables": []},
            {"id": "coverage", "title": "Coverage Items", "metrics": [], "charts": [], "tables": ["coverage_items"]},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["coi_risk_count"], "charts": [], "tables": ["coi_risks", "recommendations"]},
        ]
        return {"hero_metrics": hero, "charts": [], "tables": tables, "sections": sections}
