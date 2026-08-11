# app/services/analyzers/legal/lease_agreement_analyzer.py

import logging
from typing import Dict, Any, List
from datetime import datetime
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class LeaseAgreementAnalyzer(BaseAnalyzer):
    """
    Analyzer for lease agreements:
    - Landlord, tenant, property address
    - Rent, deposit, term dates
    - Renewal and termination clauses
    - Late fee and escalation terms
    - Risk flags: missing clauses, unclear amounts
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🏠 Advanced lease agreement analysis: {file_path}")

        lease = self._extract_lease(file_path, text, metadata)
        monetary = self._monetary_metrics(lease)
        clause_metrics = self._clause_metrics(lease)
        risk_metrics = self._risk_metrics(lease, clause_metrics, monetary)
        insights = self._generate_insights(lease, monetary, clause_metrics, risk_metrics)
        recommendations = self._generate_recommendations(lease, monetary, clause_metrics, risk_metrics)

        result = {
            "type": "lease_agreement",
            "summary": self._generate_summary(lease, monetary),
            "confidence": 0.94,

            "lease_id": lease.get("lease_id"),
            "landlord": lease.get("landlord"),
            "tenant": lease.get("tenant"),
            "property_address": lease.get("property_address"),
            "start_date": lease.get("start_date"),
            "end_date": lease.get("end_date"),

            **monetary,
            **clause_metrics,
            **risk_metrics,

            "clauses": lease.get("clauses", []),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_lease_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ------------ EXTRACTION ------------

    def _extract_lease(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_lease_parser import UniversalLeaseParser
            parser = UniversalLeaseParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Lease parsing failed: {e}")
            parsed = {}

        return {
            "lease_id": parsed.get("lease_id") or metadata.get("lease_id"),
            "landlord": parsed.get("landlord"),
            "tenant": parsed.get("tenant"),
            "property_address": parsed.get("property_address"),
            "start_date": parsed.get("start_date"),
            "end_date": parsed.get("end_date"),
            "base_rent": self._safe_float(parsed.get("base_rent")),
            "security_deposit": self._safe_float(parsed.get("security_deposit")),
            "late_fee": self._safe_float(parsed.get("late_fee")),
            "rent_escalation": parsed.get("rent_escalation"),  # text/percentage
            "clauses": parsed.get("clauses", []),  # [{type, title, text}]
        }

    def _safe_float(self, v, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).replace(",", "").replace("$", "").strip()
            return float(s)
        except Exception:
            return default

    # ------------ METRICS / RISK ------------

    def _monetary_metrics(self, lease: Dict[str, Any]) -> Dict[str, Any]:
        rent = lease.get("base_rent", 0.0)
        deposit = lease.get("security_deposit", 0.0)
        late_fee = lease.get("late_fee", 0.0)

        return {
            "base_rent": round(rent, 2),
            "security_deposit": round(deposit, 2),
            "late_fee": round(late_fee, 2),
        }

    def _clause_metrics(self, lease: Dict[str, Any]) -> Dict[str, Any]:
        clauses = lease.get("clauses", [])
        clause_types: Dict[str, int] = {}
        for c in clauses:
            t = c.get("type", "unknown")
            clause_types[t] = clause_types.get(t, 0) + 1

        required = {
            "rent_payment",
            "late_fee",
            "security_deposit",
            "maintenance",
            "termination",
            "renewal",
            "utilities",
        }
        present = {c.get("type") for c in clauses}
        missing = sorted(list(required - present))

        return {
            "clause_count": len(clauses),
            "missing_clauses": missing,
            "missing_clause_count": len(missing),
            "clause_type_breakdown": {
                "available": True,
                "dimension": "Clause Type",
                "value_field": "count",
                "all_items": clause_types,
                "top_5": [
                    {"category": t, "count": n}
                    for t, n in list(clause_types.items())[:5]
                ],
            },
        }

    def _risk_metrics(
        self,
        lease: Dict[str, Any],
        clause_metrics: Dict[str, Any],
        monetary: Dict[str, Any],
    ) -> Dict[str, Any]:
        risks: List[str] = []

        missing = clause_metrics.get("missing_clauses", [])
        if "termination" in missing:
            risks.append("No clear termination clause.")
        if "renewal" in missing:
            risks.append("No clear renewal/extension terms.")
        if monetary.get("late_fee", 0) == 0:
            risks.append("Late fee not clearly specified.")
        if monetary.get("security_deposit", 0) == 0:
            risks.append("Security deposit not clearly specified.")

        return {
            "lease_risks": risks,
            "lease_risk_count": len(risks),
        }

    # ------------ SUMMARY / INSIGHTS ------------

    def _generate_summary(self, lease: Dict[str, Any], monetary: Dict[str, Any]) -> str:
        addr = lease.get("property_address", "Property")
        tenant = lease.get("tenant", "Tenant")
        rent = monetary.get("base_rent", 0.0)
        return f"Lease for {addr} to {tenant} with base rent of ${rent:,.2f}."

    def _generate_insights(
        self,
        lease: Dict[str, Any],
        monetary: Dict[str, Any],
        clause_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(f"Base rent: ${monetary.get('base_rent', 0.0):,.2f}")
        insights.append(f"Security deposit: ${monetary.get('security_deposit', 0.0):,.2f}")
        insights.append(f"Total clauses: {clause_metrics.get('clause_count', 0)}")
        insights.append(f"Missing clauses: {clause_metrics.get('missing_clause_count', 0)}")

        if risk_metrics.get("lease_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['lease_risk_count']}.")

        return insights

    def _generate_recommendations(
        self,
        lease: Dict[str, Any],
        monetary: Dict[str, Any],
        clause_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for m in clause_metrics.get("missing_clauses", []):
            recs.append({
                "priority": "high",
                "category": "missing_clause",
                "title": f"Add {m.replace('_', ' ').title()} Clause",
                "description": f"Lease is missing a {m.replace('_', ' ')} clause.",
                "potential_savings": 0,
            })

        for r in risk_metrics.get("lease_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review Lease Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ------------ UI CONFIG ------------

    def build_lease_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "base_rent", "label": "Base Rent", "value_path": "base_rent", "is_hero": True},
            {"key": "security_deposit", "label": "Security Deposit", "value_path": "security_deposit", "is_hero": True},
            {"key": "late_fee", "label": "Late Fee", "value_path": "late_fee", "is_hero": True},
            {"key": "lease_risk_count", "label": "Risk Flags", "value_path": "lease_risk_count", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "clause_count", "label": "Total Clauses", "value_path": "clause_count", "is_hero": False},
            {"key": "missing_clause_count", "label": "Missing Clauses", "value_path": "missing_clause_count", "is_hero": False},
        ]

        charts = [
            {
                "id": "clause_type_breakdown",
                "type": "bar",
                "title": "Clause Types",
                "data_path": "clause_type_breakdown.all_items",
                "label_field": "category",
                "value_field": "count",
            }
        ]

        tables = [
            {"id": "clauses", "name": "clauses", "title": "Clauses", "data_path": "clauses"},
            {"id": "missing_clauses", "name": "missing_clauses", "title": "Missing Clauses", "data_path": "missing_clauses"},
            {"id": "lease_risks", "name": "lease_risks", "title": "Risks", "data_path": "lease_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Lease Summary",
                "metrics": ["base_rent", "security_deposit", "late_fee", "lease_risk_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "clauses",
                "title": "Clauses",
                "metrics": ["clause_count", "missing_clause_count"],
                "charts": ["clause_type_breakdown"],
                "tables": ["clauses", "missing_clauses"],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["lease_risk_count"],
                "charts": [],
                "tables": ["lease_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
