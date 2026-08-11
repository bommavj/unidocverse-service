# app/services/analyzers/qa/certificate_of_compliance_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class CertificateOfComplianceAnalyzer(BaseAnalyzer):
    """
    Analyzer for certificates of compliance:
    - Certificate id, standard, issuer, effective date
    - Clauses or test results proving compliance
    - Risk flags: expired certificate, missing scope, nonconforming items
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"✅ Certificate of compliance analysis: {file_path}")

        cert = self._extract_certificate(file_path, text, metadata)
        evidence = cert.get("evidence", [])
        metrics = self._compute_metrics(evidence)
        risks = self._risk_checks(cert, metrics)
        insights = self._generate_insights(cert, metrics, risks)
        recommendations = self._generate_recommendations(cert, metrics, risks)

        result = {
            "type": "certificate_of_compliance",
            "summary": self._generate_summary(cert, metrics),
            "confidence": 0.94,

            "certificate_number": cert.get("certificate_number"),
            "standard": cert.get("standard"),
            "issuer": cert.get("issuer"),
            "effective_date": cert.get("effective_date"),
            "expiration_date": cert.get("expiration_date"),

            "evidence": evidence,
            **metrics,

            "compliance_risks": risks,
            "compliance_risk_count": len(risks),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_certificate_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # extraction
    def _extract_certificate(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_certificate_parser import UniversalCertificateParser
            parser = UniversalCertificateParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Certificate parsing failed: {e}")
            parsed = {}

        return {
            "certificate_number": parsed.get("certificate_number") or metadata.get("certificate_number"),
            "standard": parsed.get("standard"),
            "issuer": parsed.get("issuer"),
            "effective_date": parsed.get("effective_date"),
            "expiration_date": parsed.get("expiration_date"),
            "scope": parsed.get("scope"),
            "evidence": parsed.get("evidence", []),  # [{type, description, result, date}]
        }

    # metrics
    def _compute_metrics(self, evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(evidence)
        passing = sum(1 for e in evidence if str(e.get("result", "")).lower() in ("pass", "passed", "conform"))
        failing = total - passing
        last_test_dates = [e.get("date") for e in evidence if e.get("date")]
        return {
            "evidence_count": total,
            "evidence_passing": passing,
            "evidence_failing": failing,
            "last_evidence_dates": last_test_dates,
        }

    # risk checks
    def _risk_checks(self, cert: Dict[str, Any], metrics: Dict[str, Any]) -> List[str]:
        risks = []
        from datetime import datetime
        now = datetime.utcnow().date()
        exp = cert.get("expiration_date")
        try:
            if exp:
                exp_date = datetime.fromisoformat(exp).date()
                if exp_date < now:
                    risks.append("Certificate expired.")
        except Exception:
            pass
        if metrics.get("evidence_failing", 0) > 0:
            risks.append(f"{metrics['evidence_failing']} nonconforming evidence items.")
        if not cert.get("scope"):
            risks.append("Scope of certification not specified.")
        return risks

    # summary, insights, recommendations
    def _generate_summary(self, cert: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        std = cert.get("standard", "Standard")
        num = cert.get("certificate_number", "N/A")
        return f"Certificate {num} for {std} with {metrics.get('evidence_count', 0)} evidence items."

    def _generate_insights(self, cert, metrics, risks) -> List[str]:
        insights = [
            f"Evidence items: {metrics.get('evidence_count', 0)}",
            f"Passing evidence: {metrics.get('evidence_passing', 0)}",
        ]
        if risks:
            insights.append(f"Risks identified: {len(risks)}")
        return insights

    def _generate_recommendations(self, cert, metrics, risks) -> List[Dict[str, Any]]:
        recs = []
        for r in risks:
            recs.append({"priority": "high" if "expired" in r.lower() or "nonconforming" in r.lower() else "medium", "category": "compliance", "title": "Certificate Issue", "description": r, "potential_savings": 0})
        if metrics.get("evidence_failing", 0) > 0:
            recs.append({"priority": "high", "category": "quality", "title": "Address Nonconformance", "description": "Investigate failing evidence and remediate nonconforming items.", "potential_savings": 0})
        return recs

    # UI config
    def build_certificate_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "evidence_count", "label": "Evidence Items", "value_path": "evidence_count", "is_hero": True},
            {"key": "evidence_passing", "label": "Passing", "value_path": "evidence_passing", "is_hero": True},
            {"key": "compliance_risk_count", "label": "Risk Flags", "value_path": "compliance_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "evidence", "name": "evidence", "title": "Evidence", "data_path": "evidence"},
            {"id": "compliance_risks", "name": "compliance_risks", "title": "Risks", "data_path": "compliance_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Certificate Summary", "metrics": ["evidence_count", "evidence_passing", "compliance_risk_count"], "charts": [], "tables": []},
            {"id": "evidence", "title": "Evidence", "metrics": [], "charts": [], "tables": ["evidence"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["compliance_risk_count"], "charts": [], "tables": ["compliance_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": [], "tables": tables, "sections": sections}
