# app/services/analyzers/real_estate/home_inspection_analyzer.py

import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class HomeInspectionAnalyzer(BaseAnalyzer):
    """
    Analyzer for home inspection reports:
    - Property address, inspector, inspection date
    - Findings categorized by severity and system (roof, foundation, electrical)
    - Repair cost estimates and total estimated remediation
    - Risk flags: safety issues, structural concerns, missing inspection date
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🔍 Home inspection analysis: {file_path}")

        insp = self._extract_inspection(file_path, text, metadata)

        findings = insp.get("findings", [])
        normalized = self._normalize_findings(findings)
        severity_metrics = self._severity_metrics(normalized)
        cost_metrics = self._cost_metrics(normalized)
        risk_metrics = self._risk_metrics(insp, normalized, severity_metrics)

        insights = self._generate_insights(insp, severity_metrics, cost_metrics, risk_metrics)
        recommendations = self._generate_recommendations(insp, normalized, risk_metrics)

        result = {
            "type": "home_inspection",
            "summary": self._generate_summary(insp, cost_metrics),
            "confidence": 0.95,

            "inspection_id": insp.get("inspection_id"),
            "inspector_name": insp.get("inspector_name"),
            "property_address": insp.get("property_address"),
            "inspection_date": insp.get("inspection_date"),

            "findings": normalized,
            **severity_metrics,
            **cost_metrics,
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_inspection_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_inspection(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_inspection_parser import UniversalInspectionParser
            parser = UniversalInspectionParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Inspection parsing failed: {e}")
            parsed = {}

        return {
            "inspection_id": parsed.get("inspection_id"),
            "inspector_name": parsed.get("inspector_name"),
            "property_address": parsed.get("property_address"),
            "inspection_date": parsed.get("inspection_date"),
            "findings": parsed.get("findings", []),  # [{system, description, severity, repair_cost}]
        }

    # Normalize findings
    def _normalize_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for f in findings or []:
            severity = str(f.get("severity", "minor")).lower()
            cost = self.safe_float(f.get("repair_cost", 0.0))
            normalized.append({
                "system": f.get("system", "General"),
                "description": f.get("description"),
                "severity": severity,
                "repair_cost": cost,
                "_raw": f,
            })
        return normalized

    def _severity_metrics(self, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
        counts = {"critical": 0, "major": 0, "minor": 0}
        for f in findings or []:
            s = f.get("severity", "minor")
            if s in counts:
                counts[s] += 1
            else:
                counts["minor"] += 1
        return {
            "critical_issues": counts["critical"],
            "major_issues": counts["major"],
            "minor_issues": counts["minor"],
            "total_issues": sum(counts.values()),
        }

    def _cost_metrics(self, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = sum(self.safe_float(f.get("repair_cost", 0)) for f in findings or [])
        avg = round(total / len(findings), 2) if findings else 0.0
        return {
            "total_repair_cost": round(total, 2),
            "average_repair_cost": avg,
        }

    def _risk_metrics(self, insp, findings, severity_metrics) -> Dict[str, Any]:
        risks = []
        if severity_metrics.get("critical_issues", 0) > 0:
            risks.append("Critical safety issues detected.")
        if not insp.get("inspection_date"):
            risks.append("Missing inspection date.")
        return {
            "inspection_risks": risks,
            "inspection_risk_count": len(risks),
        }

    # Summary, insights, recommendations
    def _generate_summary(self, insp, cost_metrics) -> str:
        addr = insp.get("property_address", "Property")
        total = cost_metrics.get("total_repair_cost", 0.0)
        return f"Inspection for {addr} with estimated repair cost ${total:,.2f}."

    def _generate_insights(self, insp, severity_metrics, cost_metrics, risk_metrics) -> List[str]:
        insights = []
        insights.append(f"Total issues: {severity_metrics.get('total_issues', 0)}")
        insights.append(f"Estimated repair cost: ${cost_metrics.get('total_repair_cost', 0.0):,.2f}")
        if risk_metrics.get("inspection_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['inspection_risk_count']}.")
        return insights

    def _generate_recommendations(self, insp, findings, risk_metrics) -> List[Dict[str, Any]]:
        recs = []
        if risk_metrics.get("inspection_risk_count", 0) > 0:
            recs.append({
                "priority": "high",
                "category": "safety",
                "title": "Address Critical Issues",
                "description": "Critical issues should be remediated before closing.",
                "potential_savings": 0,
            })
        if findings:
            recs.append({
                "priority": "medium",
                "category": "cost",
                "title": "Obtain Repair Estimates",
                "description": "Get contractor quotes for major repairs to refine cost estimates.",
                "potential_savings": 0,
            })
        return recs

    # UI config
    def build_inspection_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "total_repair_cost", "label": "Total Repair Cost", "value_path": "total_repair_cost", "is_hero": True},
            {"key": "critical_issues", "label": "Critical Issues", "value_path": "critical_issues", "is_hero": True},
            {"key": "major_issues", "label": "Major Issues", "value_path": "major_issues", "is_hero": True},
            {"key": "inspection_risk_count", "label": "Risk Flags", "value_path": "inspection_risk_count", "is_hero": True},
        ]

        charts = [
            {
                "id": "issues_by_system",
                "type": "bar",
                "title": "Issues by System",
                "data_path": "findings",
                "label_field": "system",
                "value_field": "repair_cost",
            }
        ]

        tables = [
            {"id": "findings", "name": "findings", "title": "Findings", "data_path": "findings"},
            {"id": "inspection_risks", "name": "inspection_risks", "title": "Risks", "data_path": "inspection_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Inspection Summary", "metrics": ["total_repair_cost", "total_issues", "critical_issues"], "charts": [], "tables": []},
            {"id": "findings", "title": "Findings", "metrics": [], "charts": ["issues_by_system"], "tables": ["findings"]},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["inspection_risk_count"], "charts": [], "tables": ["inspection_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
