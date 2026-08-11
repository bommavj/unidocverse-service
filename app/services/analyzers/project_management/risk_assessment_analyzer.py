# app/services/analyzers/project_management/risk_assessment_analyzer.py

import logging
from typing import Dict, Any, List, Tuple

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class RiskAssessmentAnalyzer(BaseAnalyzer):
    """
    Analyzer for Risk Assessments:
    - Risk register extraction: id, description, likelihood, impact, owner, status, mitigation
    - Compute risk scores, top risks, open mitigations, residual risk
    - Provide recommendations and UI config
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"⚠️ Risk assessment analysis: {file_path}")

        ra = self._extract_risk_assessment(file_path, text, metadata)
        risks = ra.get("risks", [])
        normalized = self._normalize_risks(risks)
        scored = self._score_risks(normalized)
        aggregates = self._aggregate_metrics(scored)
        insights = self._generate_insights(ra, aggregates)
        recommendations = self._generate_recommendations(ra, scored, aggregates)

        result = {
            "type": "risk_assessment",
            "summary": self._generate_summary(ra, aggregates),
            "confidence": 0.95,

            "assessment_id": ra.get("assessment_id"),
            "project_id": ra.get("project_id"),
            "prepared_by": ra.get("prepared_by"),
            "prepared_date": ra.get("prepared_date"),

            "risks": scored,
            **aggregates,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_risk_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_risk_assessment(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_risk_parser import UniversalRiskParser
            parser = UniversalRiskParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Risk assessment parsing failed: {e}")
            parsed = {}

        return {
            "assessment_id": parsed.get("assessment_id") or metadata.get("assessment_id"),
            "project_id": parsed.get("project_id"),
            "prepared_by": parsed.get("prepared_by"),
            "prepared_date": parsed.get("prepared_date"),
            "risks": parsed.get("risks", []),  # [{id, description, likelihood, impact, owner, status, mitigation}]
        }

    # ---------------- NORMALIZATION & SCORING ----------------

    def _normalize_risks(self, risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in risks or []:
            likelihood = self._normalize_rating(r.get("likelihood"))
            impact = self._normalize_rating(r.get("impact"))
            owner = r.get("owner")
            status = (r.get("status") or "open").lower()
            out.append({
                "id": r.get("id"),
                "description": r.get("description"),
                "likelihood": likelihood,
                "impact": impact,
                "owner": owner,
                "status": status,
                "mitigation": r.get("mitigation"),
                "_raw": r,
            })
        return out

    def _normalize_rating(self, v) -> float:
        # Accept numeric or textual (low/medium/high) inputs
        try:
            if v is None:
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip().lower()
            mapping = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}
            if s in mapping:
                return mapping[s]
            return float(s)
        except Exception:
            return 0.0

    def _score_risks(self, risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scored = []
        for r in risks or []:
            score = round(r.get("likelihood", 0.0) * r.get("impact", 0.0), 2)
            severity = self._severity_from_score(score)
            r_copy = dict(r)
            r_copy["score"] = score
            r_copy["severity"] = severity
            scored.append(r_copy)
        # sort descending by score
        scored.sort(key=lambda x: x.get("score", 0), reverse=True)
        return scored

    def _severity_from_score(self, score: float) -> str:
        if score >= 9:
            return "critical"
        if score >= 6:
            return "high"
        if score >= 3:
            return "medium"
        return "low"

    # ---------------- AGGREGATES ----------------

    def _aggregate_metrics(self, scored: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(scored)
        open_count = sum(1 for r in scored if r.get("status") == "open")
        critical_count = sum(1 for r in scored if r.get("severity") == "critical")
        high_count = sum(1 for r in scored if r.get("severity") == "high")
        top_5 = scored[:5]
        avg_score = round(sum(r.get("score", 0) for r in scored) / total, 2) if total else 0.0
        return {
            "risk_count": total,
            "open_risk_count": open_count,
            "critical_risk_count": critical_count,
            "high_risk_count": high_count,
            "average_risk_score": avg_score,
            "top_5_risks": top_5,
        }

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, ra: Dict[str, Any], aggregates: Dict[str, Any]) -> str:
        pid = ra.get("project_id", "Project")
        return f"Risk assessment for {pid} with {aggregates.get('risk_count', 0)} risks."

    def _generate_insights(self, ra: Dict[str, Any], aggregates: Dict[str, Any]) -> List[str]:
        insights = []
        insights.append(f"Total risks: {aggregates.get('risk_count', 0)}")
        insights.append(f"Open risks: {aggregates.get('open_risk_count', 0)}")
        insights.append(f"Average risk score: {aggregates.get('average_risk_score', 0.0)}")
        if aggregates.get("critical_risk_count", 0) > 0:
            insights.append(f"Critical risks: {aggregates['critical_risk_count']}")
        return insights

    def _generate_recommendations(self, ra: Dict[str, Any], scored: List[Dict[str, Any]], aggregates: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        # Recommend immediate actions for critical risks
        for r in scored:
            if r.get("severity") == "critical" and r.get("status") == "open":
                recs.append({
                    "priority": "high",
                    "category": "mitigation",
                    "title": f"Immediate action for risk {r.get('id') or 'N/A'}",
                    "description": f"Risk '{r.get('description')}' scored {r.get('score')}. Assign owner {r.get('owner') or 'TBD'} and implement mitigation: {r.get('mitigation') or 'Define mitigation'}",
                    "potential_savings": 0,
                })
        # General recommendations
        if aggregates.get("open_risk_count", 0) > 0:
            recs.append({
                "priority": "medium",
                "category": "governance",
                "title": "Triage open risks",
                "description": "Prioritize open risks by score and assign owners with deadlines.",
                "potential_savings": 0,
            })
        return recs

    # ---------------- UI CONFIG ----------------

    def build_risk_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            self.metric_def("risk_count", "Total Risks", True),
            self.metric_def("open_risk_count", "Open Risks", True),
            self.metric_def("critical_risk_count", "Critical Risks", True),
            self.metric_def("average_risk_score", "Avg Risk Score", True),
        ]

        charts = [
            self.chart_def(
                "top_5_risks",
                "Top Risks",
                "top_5_risks",
                "bar",
                label_field="id",
                value_field="score",
            )
        ]

        tables = [
            self.table_def("risks", "Risk Register", "risks"),
            self.table_def("top_5_risks", "Top 5 Risks", "top_5_risks"),
            self.table_def("recommendations", "Recommendations", "recommendations"),
        ]

        sections = [
            self.section_def("summary", "Risk Summary", metrics=["risk_count", "open_risk_count", "critical_risk_count", "average_risk_score"]),
            self.section_def("register", "Risk Register", charts=["top_5_risks"], tables=["risks"]),
            self.section_def("recommendations", "Recommendations", tables=["recommendations"]),
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
