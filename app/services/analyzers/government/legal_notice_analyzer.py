import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class LegalNoticeAnalyzer(BaseAnalyzer):
    """
    Analyzer for legal notices and filings:
    - Extracts case number, court, parties, filing date, deadlines
    - Flags missing service info, upcoming deadlines, jurisdiction mismatches
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📣 Legal notice analysis: {file_path}")

        notice = self._extract_notice(file_path, text, metadata)
        metrics = self._compute_metrics(notice)
        risks = self._risk_checks(notice, metrics)
        insights = self._generate_insights(notice, metrics, risks)
        recommendations = self._generate_recommendations(notice, metrics, risks)

        result = {
            "type": "legal_notice",
            "summary": self._generate_summary(notice),
            "confidence": 0.94,

            "case_number": notice.get("case_number"),
            "court": notice.get("court"),
            "plaintiff": notice.get("plaintiff"),
            "defendant": notice.get("defendant"),
            "filing_date": notice.get("filing_date"),
            "service_deadline": notice.get("service_deadline"),
            "jurisdiction": notice.get("jurisdiction"),

            **metrics,
            **risks,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_notice_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_notice(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_legal_notice_parser import UniversalLegalNoticeParser
            parser = UniversalLegalNoticeParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Legal notice parsing failed: {e}")
            parsed = {}

        return {
            "case_number": parsed.get("case_number") or metadata.get("case_number"),
            "court": parsed.get("court"),
            "plaintiff": parsed.get("plaintiff"),
            "defendant": parsed.get("defendant"),
            "filing_date": parsed.get("filing_date"),
            "service_deadline": parsed.get("service_deadline"),
            "jurisdiction": parsed.get("jurisdiction"),
            "service_method": parsed.get("service_method"),
        }

    # ---------------- METRICS ----------------

    def _compute_metrics(self, notice: Dict[str, Any]) -> Dict[str, Any]:
        from datetime import datetime
        days_to_service = None
        try:
            if notice.get("service_deadline"):
                sd = datetime.fromisoformat(notice["service_deadline"]).date()
                days_to_service = (sd - datetime.utcnow().date()).days
        except Exception:
            days_to_service = None

        completeness = 0
        fields = ["case_number", "court", "plaintiff", "defendant", "filing_date", "service_deadline"]
        present = sum(1 for f in fields if notice.get(f))
        completeness = round((present / len(fields)) * 100, 2)

        return {
            "days_to_service_deadline": days_to_service,
            "completeness_percent": completeness,
        }

    # ---------------- RISK CHECKS ----------------

    def _risk_checks(self, notice: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        days = metrics.get("days_to_service_deadline")
        if days is not None and days < 0:
            risks.append("Service deadline has passed.")
        if metrics.get("completeness_percent", 100) < 100:
            risks.append("Legal notice missing required fields.")
        if notice.get("jurisdiction") and notice.get("court") and notice.get("jurisdiction").lower() not in (notice.get("court") or "").lower():
            risks.append("Jurisdiction and court appear inconsistent.")
        return {
            "notice_risks": risks,
            "notice_risk_count": len(risks),
        }

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, notice: Dict[str, Any]) -> str:
        case = notice.get("case_number", "Case")
        court = notice.get("court", "Court")
        return f"Legal notice {case} filed in {court}."

    def _generate_insights(self, notice: Dict[str, Any], metrics: Dict[str, Any], risks: Dict[str, Any]) -> List[str]:
        insights: List[str] = []
        insights.append(f"Completeness: {metrics.get('completeness_percent', 0)}%")
        if metrics.get("days_to_service_deadline") is not None:
            insights.append(f"Days to service deadline: {metrics.get('days_to_service_deadline')}")
        if risks.get("notice_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risks['notice_risk_count']}.")
        return insights

    def _generate_recommendations(self, notice: Dict[str, Any], metrics: Dict[str, Any], risks: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risks.get("notice_risks", []):
            recs.append({
                "priority": "high" if "deadline" in r.lower() or "jurisdiction" in r.lower() else "medium",
                "category": "legal",
                "title": "Legal Notice Issue",
                "description": r,
                "potential_savings": 0,
            })
        if metrics.get("days_to_service_deadline") is not None and metrics["days_to_service_deadline"] <= 7 and metrics["days_to_service_deadline"] >= 0:
            recs.append({
                "priority": "high",
                "category": "process",
                "title": "Urgent Service Required",
                "description": "Service deadline approaching; prioritize service and documentation.",
                "potential_savings": 0,
            })
        return recs

    # ---------------- UI CONFIG ----------------

    def build_notice_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "completeness_percent", "label": "Completeness %", "value_path": "completeness_percent", "is_hero": True},
            {"key": "days_to_service_deadline", "label": "Days to Service", "value_path": "days_to_service_deadline", "is_hero": True},
            {"key": "notice_risk_count", "label": "Risk Flags", "value_path": "notice_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "notice_risks", "name": "notice_risks", "title": "Risks", "data_path": "notice_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Notice Summary", "metrics": ["completeness_percent", "days_to_service_deadline"], "charts": [], "tables": []},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["notice_risk_count"], "charts": [], "tables": ["notice_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": [], "tables": tables, "sections": sections}
