import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class AuditReportAnalyzer(BaseAnalyzer):
    """
    Analyzer for audit reports:
    - Auditor, audit_id, scope, period
    - Findings, severity, remediation status
    - Risk scoring and recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🧾 Audit report analysis: {file_path}")

        audit = self._extract_audit(file_path, text, metadata)
        findings = audit.get("findings", [])
        normalized = self._normalize_findings(findings)
        metrics = self._metrics(normalized)
        risk = self._risk_metrics(normalized)
        insights = self._generate_insights(audit, metrics, risk)
        recommendations = self._generate_recommendations(audit, normalized, risk)

        result = {
            "type": "audit_report",
            "summary": self._generate_summary(audit, metrics),
            "confidence": 0.94,

            "audit_id": audit.get("audit_id"),
            "auditor_name": audit.get("auditor_name"),
            "scope": audit.get("scope"),
            "period_start": audit.get("period_start"),
            "period_end": audit.get("period_end"),

            "findings": normalized,
            **metrics,
            **risk,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_audit_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_audit(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_audit_parser import UniversalAuditParser
            parser = UniversalAuditParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Audit parsing failed: {e}")
            parsed = {}

        return {
            "audit_id": parsed.get("audit_id") or metadata.get("audit_id"),
            "auditor_name": parsed.get("auditor_name"),
            "scope": parsed.get("scope"),
            "period_start": parsed.get("period_start"),
            "period_end": parsed.get("period_end"),
            "findings": parsed.get("findings", []),  # [{id, title, severity, status, description}]
        }

    def _normalize_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for f in findings or []:
            sev = str(f.get("severity", "medium")).lower()
            status = str(f.get("status", "open")).lower()
            out.append({
                "id": f.get("id"),
                "title": f.get("title"),
                "severity": sev,
                "status": status,
                "description": f.get("description"),
                "_raw": f,
            })
        return out

    def _metrics(self, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(findings)
        open_count = sum(1 for f in findings if f.get("status") == "open")
        closed_count = total - open_count
        sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in findings:
            s = f.get("severity", "medium")
            if s in sev_counts:
                sev_counts[s] += 1
            else:
                sev_counts["medium"] += 1
        return {
            "finding_count": total,
            "open_findings": open_count,
            "closed_findings": closed_count,
            "severity_breakdown": sev_counts,
        }

    def _risk_metrics(self, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
        risks = []
        if any(f.get("severity") == "critical" for f in findings):
            risks.append("Critical findings present requiring immediate remediation.")
        if sum(1 for f in findings if f.get("status") == "open") > 10:
            risks.append("Large number of open findings; backlog risk.")
        return {
            "audit_risks": risks,
            "audit_risk_count": len(risks),
        }

    def _generate_summary(self, audit, metrics) -> str:
        aid = audit.get("audit_id", "N/A")
        total = metrics.get("finding_count", 0)
        return f"Audit {aid} covering {audit.get('scope','N/A')} with {total} findings."

    def _generate_insights(self, audit, metrics, risk) -> List[str]:
        insights = [
            f"Total findings: {metrics.get('finding_count', 0)}",
            f"Open findings: {metrics.get('open_findings', 0)}",
        ]
        if risk.get("audit_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk['audit_risk_count']}.")
        return insights

    def _generate_recommendations(self, audit, findings, risk) -> List[Dict[str, Any]]:
        recs = []
        if risk.get("audit_risks"):
            for r in risk["audit_risks"]:
                recs.append({"priority": "high", "category": "remediation", "title": "Audit Risk", "description": r, "potential_savings": 0})
        if findings:
            recs.append({"priority": "medium", "category": "process", "title": "Prioritize Open Findings", "description": "Triage and assign owners to open findings.", "potential_savings": 0})
        return recs

    def build_audit_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "finding_count", "label": "Findings", "value_path": "finding_count", "is_hero": True},
            {"key": "open_findings", "label": "Open Findings", "value_path": "open_findings", "is_hero": True},
            {"key": "audit_risk_count", "label": "Risk Flags", "value_path": "audit_risk_count", "is_hero": True},
        ]
        charts = []
        tables = [
            {"id": "findings", "name": "findings", "title": "Findings", "data_path": "findings"},
            {"id": "audit_risks", "name": "audit_risks", "title": "Risks", "data_path": "audit_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]
        sections = [
            {"id": "summary", "title": "Audit Summary", "metrics": ["finding_count", "open_findings", "audit_risk_count"], "charts": [], "tables": []},
            {"id": "findings", "title": "Findings", "metrics": [], "charts": [], "tables": ["findings"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["audit_risk_count"], "charts": [], "tables": ["audit_risks", "recommendations"]},
        ]
        return {"hero_metrics": hero, "charts": charts, "tables": tables, "sections": sections}
