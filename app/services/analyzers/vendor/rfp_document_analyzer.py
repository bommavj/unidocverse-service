# app/services/analyzers/vendor/rfp_document_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class RfpDocumentAnalyzer(BaseAnalyzer):
    """
    Analyzer for Request for Proposal documents:
    - RFP id, project name, deadlines, submission requirements
    - Evaluation criteria, mandatory qualifications, budget indications
    - Risk flags: ambiguous scope, missing evaluation criteria, unrealistic timelines
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📣 RFP analysis: {file_path}")

        rfp = self._extract_rfp(file_path, text, metadata)
        metrics = self._rfp_metrics(rfp)
        risk_metrics = self._risk_metrics(rfp, metrics)
        insights = self._generate_insights(rfp, metrics, risk_metrics)
        recommendations = self._generate_recommendations(rfp, metrics, risk_metrics)

        result = {
            "type": "rfp_document",
            "summary": self._generate_summary(rfp),
            "confidence": 0.93,

            "rfp_number": rfp.get("rfp_number"),
            "project_name": rfp.get("project_name"),
            "issue_date": rfp.get("issue_date"),
            "submission_deadline": rfp.get("submission_deadline"),
            "contact": rfp.get("contact"),

            **metrics,
            **risk_metrics,

            "requirements": rfp.get("requirements", []),
            "evaluation_criteria": rfp.get("evaluation_criteria", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_rfp_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_rfp(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_rfp_parser import UniversalRFPParser
            parser = UniversalRFPParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ RFP parsing failed: {e}")
            parsed = {}

        return {
            "rfp_number": parsed.get("rfp_number") or metadata.get("rfp_number"),
            "project_name": parsed.get("project_name"),
            "issue_date": parsed.get("issue_date"),
            "submission_deadline": parsed.get("submission_deadline"),
            "contact": parsed.get("contact"),
            "requirements": parsed.get("requirements", []),  # [{type, mandatory, description}]
            "evaluation_criteria": parsed.get("evaluation_criteria", []),  # [{criterion, weight}]
            "budget": parsed.get("budget"),
            "timeline": parsed.get("timeline"),
        }

    # Metrics
    def _rfp_metrics(self, rfp: Dict[str, Any]) -> Dict[str, Any]:
        reqs = rfp.get("requirements", [])
        mandatory = sum(1 for r in reqs if r.get("mandatory") is True)
        criteria = rfp.get("evaluation_criteria", [])
        total_weight = sum(self.safe_float(c.get("weight", 0)) for c in criteria)
        return {
            "requirement_count": len(reqs),
            "mandatory_requirement_count": mandatory,
            "evaluation_criteria_count": len(criteria),
            "evaluation_total_weight": round(total_weight, 2),
            "budget": rfp.get("budget"),
        }

    # Risk checks
    def _risk_metrics(self, rfp: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        if metrics.get("evaluation_criteria_count", 0) == 0:
            risks.append("No evaluation criteria provided.")
        if metrics.get("evaluation_total_weight", 0) and abs(metrics.get("evaluation_total_weight") - 100) > 1:
            risks.append("Evaluation criteria weights do not sum to 100.")
        if not rfp.get("submission_deadline"):
            risks.append("Submission deadline missing.")
        if rfp.get("timeline") and "weeks" in str(rfp.get("timeline")).lower():
            # heuristic: very short timelines flagged
            try:
                if "2 weeks" in str(rfp.get("timeline")).lower() or "1 week" in str(rfp.get("timeline")).lower():
                    risks.append("Short timeline may limit quality of responses.")
            except Exception:
                pass
        return {"rfp_risks": risks, "rfp_risk_count": len(risks)}

    # Summary, insights, recommendations
    def _generate_summary(self, rfp: Dict[str, Any]) -> str:
        proj = rfp.get("project_name", "Project")
        num = rfp.get("rfp_number", "N/A")
        return f"RFP {num} for {proj}."

    def _generate_insights(self, rfp: Dict[str, Any], metrics: Dict[str, Any], risk_metrics: Dict[str, Any]) -> List[str]:
        insights = []
        insights.append(f"Requirements: {metrics.get('requirement_count', 0)} ({metrics.get('mandatory_requirement_count', 0)} mandatory)")
        insights.append(f"Evaluation criteria: {metrics.get('evaluation_criteria_count', 0)}")
        if risk_metrics.get("rfp_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['rfp_risk_count']}.")
        return insights

    def _generate_recommendations(self, rfp: Dict[str, Any], metrics: Dict[str, Any], risk_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risk_metrics.get("rfp_risks", []):
            recs.append({"priority": "medium", "category": "clarity", "title": "RFP Issue", "description": r, "potential_savings": 0})
        if metrics.get("evaluation_criteria_count", 0) == 0:
            recs.append({"priority": "high", "category": "procurement", "title": "Define Evaluation Criteria", "description": "Add clear evaluation criteria and weights to enable fair scoring.", "potential_savings": 0})
        return recs

    # UI config
    def build_rfp_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "requirement_count", "label": "Requirements", "value_path": "requirement_count", "is_hero": True},
            {"key": "mandatory_requirement_count", "label": "Mandatory", "value_path": "mandatory_requirement_count", "is_hero": True},
            {"key": "evaluation_criteria_count", "label": "Criteria", "value_path": "evaluation_criteria_count", "is_hero": True},
            {"key": "rfp_risk_count", "label": "Risk Flags", "value_path": "rfp_risk_count", "is_hero": True},
        ]

        charts = []

        tables = [
            {"id": "requirements", "name": "requirements", "title": "Requirements", "data_path": "requirements"},
            {"id": "evaluation_criteria", "name": "evaluation_criteria", "title": "Evaluation Criteria", "data_path": "evaluation_criteria"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "RFP Summary", "metrics": ["requirement_count", "evaluation_criteria_count", "rfp_risk_count"], "charts": [], "tables": []},
            {"id": "requirements", "title": "Requirements", "metrics": ["mandatory_requirement_count"], "charts": [], "tables": ["requirements"]},
            {"id": "evaluation", "title": "Evaluation", "metrics": ["evaluation_criteria_count", "evaluation_total_weight"], "charts": [], "tables": ["evaluation_criteria", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
