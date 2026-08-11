# app/services/analyzers/project_management/project_charter_analyzer.py

import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class ProjectCharterAnalyzer(BaseAnalyzer):
    """
    Analyzer for Project Charters:
    - Project id, sponsor, project manager
    - Objectives, scope, deliverables, high-level timeline
    - Budget, stakeholders, success criteria
    - Risk flags: missing scope, missing sponsor, unrealistic timeline
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📁 Project charter analysis: {file_path}")

        charter = self._extract_charter(file_path, text, metadata)

        metrics = self._charter_metrics(charter)
        timeline_metrics = self._timeline_metrics(charter)
        stakeholder_metrics = self._stakeholder_metrics(charter)
        risk_metrics = self._risk_checks(charter, metrics, timeline_metrics)

        insights = self._generate_insights(charter, metrics, timeline_metrics, stakeholder_metrics, risk_metrics)
        recommendations = self._generate_recommendations(charter, metrics, timeline_metrics, stakeholder_metrics, risk_metrics)

        result = {
            "type": "project_charter",
            "summary": self._generate_summary(charter, metrics),
            "confidence": 0.94,

            "project_id": charter.get("project_id"),
            "project_name": charter.get("project_name"),
            "sponsor": charter.get("sponsor"),
            "project_manager": charter.get("project_manager"),
            "start_date": charter.get("start_date"),
            "end_date": charter.get("end_date"),
            "currency": charter.get("currency", "USD"),

            **metrics,
            **timeline_metrics,
            **stakeholder_metrics,
            **risk_metrics,

            "objectives": charter.get("objectives", []),
            "scope": charter.get("scope", ""),
            "deliverables": charter.get("deliverables", []),
            "budget": charter.get("budget"),
            "stakeholders": charter.get("stakeholders", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_charter_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_charter(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_project_charter_parser import UniversalProjectCharterParser
            parser = UniversalProjectCharterParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Project charter parsing failed: {e}")
            parsed = {}

        return {
            "project_id": parsed.get("project_id") or metadata.get("project_id"),
            "project_name": parsed.get("project_name"),
            "sponsor": parsed.get("sponsor"),
            "project_manager": parsed.get("project_manager"),
            "start_date": parsed.get("start_date"),
            "end_date": parsed.get("end_date"),
            "objectives": parsed.get("objectives", []),
            "scope": parsed.get("scope", ""),
            "deliverables": parsed.get("deliverables", []),
            "budget": self.safe_float(parsed.get("budget")),
            "stakeholders": parsed.get("stakeholders", []),  # [{name, role, contact}]
            "currency": parsed.get("currency", "USD"),
        }

    # ---------------- METRICS ----------------

    def _charter_metrics(self, charter: Dict[str, Any]) -> Dict[str, Any]:
        objectives = charter.get("objectives", [])
        deliverables = charter.get("deliverables", [])
        budget = charter.get("budget", 0.0)
        return {
            "objective_count": len(objectives),
            "deliverable_count": len(deliverables),
            "budget": round(budget, 2),
        }

    def _timeline_metrics(self, charter: Dict[str, Any]) -> Dict[str, Any]:
        start = self.parse_date(charter.get("start_date"))
        end = self.parse_date(charter.get("end_date"))
        duration_days = None
        if start and end:
            duration_days = (end - start).days
        return {
            "start_date_parsed": charter.get("start_date"),
            "end_date_parsed": charter.get("end_date"),
            "duration_days": duration_days,
        }

    def _stakeholder_metrics(self, charter: Dict[str, Any]) -> Dict[str, Any]:
        stakeholders = charter.get("stakeholders", [])
        roles = {}
        for s in stakeholders:
            r = s.get("role", "unknown")
            roles[r] = roles.get(r, 0) + 1
        return {
            "stakeholder_count": len(stakeholders),
            "stakeholder_role_breakdown": roles,
        }

    # ---------------- RISK CHECKS ----------------

    def _risk_checks(self, charter: Dict[str, Any], metrics: Dict[str, Any], timeline_metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []

        if not charter.get("sponsor"):
            risks.append("Sponsor not specified; governance risk.")
        if not charter.get("scope"):
            risks.append("Project scope not defined; scope creep risk.")
        if metrics.get("deliverable_count", 0) == 0:
            risks.append("No deliverables listed; success criteria unclear.")
        duration = timeline_metrics.get("duration_days")
        if duration is None:
            risks.append("Start or end date missing; timeline unclear.")
        elif duration < 7:
            risks.append("Very short timeline; feasibility risk.")
        if metrics.get("budget", 0) == 0:
            risks.append("Budget not specified; funding risk.")
        return {
            "charter_risks": risks,
            "charter_risk_count": len(risks),
        }

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, charter: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        name = charter.get("project_name", "Project")
        sponsor = charter.get("sponsor", "Sponsor")
        return f"Project charter for {name} sponsored by {sponsor}."

    def _generate_insights(self, charter, metrics, timeline_metrics, stakeholder_metrics, risk_metrics) -> List[str]:
        insights: List[str] = []
        insights.append(f"Objectives: {metrics.get('objective_count', 0)}")
        insights.append(f"Deliverables: {metrics.get('deliverable_count', 0)}")
        if timeline_metrics.get("duration_days") is not None:
            insights.append(f"Planned duration: {timeline_metrics['duration_days']} days")
        insights.append(f"Stakeholders: {stakeholder_metrics.get('stakeholder_count', 0)}")
        if risk_metrics.get("charter_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['charter_risk_count']}.")
        return insights

    def _generate_recommendations(self, charter, metrics, timeline_metrics, stakeholder_metrics, risk_metrics) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risk_metrics.get("charter_risks", []):
            recs.append({
                "priority": "high" if "Sponsor" in r or "scope" in r.lower() else "medium",
                "category": "governance" if "sponsor" in r.lower() else "planning",
                "title": "Charter Issue",
                "description": r,
                "potential_savings": 0,
            })
        if metrics.get("deliverable_count", 0) == 0:
            recs.append({
                "priority": "high",
                "category": "definition",
                "title": "Define Deliverables",
                "description": "List measurable deliverables and acceptance criteria.",
                "potential_savings": 0,
            })
        if timeline_metrics.get("duration_days") is not None and timeline_metrics["duration_days"] < 30:
            recs.append({
                "priority": "medium",
                "category": "schedule",
                "title": "Validate Timeline",
                "description": "Validate feasibility of short timeline with stakeholders.",
                "potential_savings": 0,
            })
        return recs

    # ---------------- UI CONFIG ----------------

    def build_charter_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            self.metric_def("objective_count", "Objectives", True),
            self.metric_def("deliverable_count", "Deliverables", True),
            self.metric_def("stakeholder_count", "Stakeholders", True),
            self.metric_def("charter_risk_count", "Risk Flags", True),
        ]

        charts = [
            self.chart_def(
                "stakeholder_role_breakdown",
                "Stakeholder Roles",
                "stakeholder_role_breakdown",
                "bar",
                label_field="category",
                value_field="count",
            )
        ]

        tables = [
            self.table_def("objectives", "Objectives", "objectives"),
            self.table_def("deliverables", "Deliverables", "deliverables"),
            self.table_def("recommendations", "Recommendations", "recommendations"),
        ]

        sections = [
            self.section_def("summary", "Charter Summary", metrics=["objective_count", "deliverable_count", "stakeholder_count", "charter_risk_count"]),
            self.section_def("scope", "Scope & Deliverables", metrics=["deliverable_count"], charts=[], tables=["deliverables"]),
            self.section_def("stakeholders", "Stakeholders", metrics=["stakeholder_count"], charts=["stakeholder_role_breakdown"], tables=["objectives"]),
            self.section_def("risk", "Risks & Recommendations", metrics=["charter_risk_count"], tables=["recommendations"]),
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
