import logging
from datetime import datetime
from typing import Dict, Any, List
from collections import defaultdict

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class ContractAnalyzer(BaseAnalyzer):
    """
    Domain-specific analyzer for contracts.
    Extracts:
    - Parties, dates, signatures
    - Payment terms
    - Obligations (party A, party B)
    - Clauses (termination, confidentiality, liability, indemnity, governing law)
    - Risks & missing clauses
    - Renewal alerts
    - Recommendations & insights
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📄 Advanced contract analysis: {file_path}")

        contract = self._extract_contract(file_path, text, metadata)

        clause_metrics = self._clause_metrics(contract)
        obligation_metrics = self._obligation_metrics(contract)
        risk_metrics = self._risk_metrics(contract, clause_metrics)
        renewal_metrics = self._renewal_metrics(contract)

        insights = self._generate_insights(contract, clause_metrics, risk_metrics)
        recommendations = self._generate_recommendations(contract, clause_metrics, risk_metrics)

        result = {
            "type": "contract",
            "summary": self._generate_summary(contract),
            "confidence": 0.95,

            # Header
            "contract_id": contract.get("contract_id"),
            "party_a": contract.get("party_a"),
            "party_b": contract.get("party_b"),
            "effective_date": contract.get("effective_date"),
            "end_date": contract.get("end_date"),
            "renewal_terms": contract.get("renewal_terms"),
            "payment_terms": contract.get("payment_terms"),

            # Clauses
            "clauses": contract.get("clauses", []),
            **clause_metrics,

            # Obligations
            "obligations": contract.get("obligations", []),
            **obligation_metrics,

            # Risks
            **risk_metrics,

            # Renewal
            **renewal_metrics,

            # Insights
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_contract_ui_config(result)
        result["has_advanced_analytics"] = True

        return result

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_contract(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use a universal parser; normalize to stable schema.
        """
        try:
            from app.services.parsers.universal_contract_parser import UniversalContractParser
            parser = UniversalContractParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Contract parsing failed: {e}")
            parsed = {}

        return {
            "contract_id": parsed.get("contract_id"),
            "party_a": parsed.get("party_a"),
            "party_b": parsed.get("party_b"),
            "effective_date": parsed.get("effective_date"),
            "end_date": parsed.get("end_date"),
            "renewal_terms": parsed.get("renewal_terms"),
            "payment_terms": parsed.get("payment_terms"),

            "clauses": parsed.get("clauses", []),
            "obligations": parsed.get("obligations", []),
        }

    # ============================================================
    # METRICS
    # ============================================================

    def _clause_metrics(self, contract: Dict[str, Any]) -> Dict[str, Any]:
        clauses = contract.get("clauses", [])

        clause_types = defaultdict(int)
        for c in clauses:
            clause_types[c.get("type", "Unknown")] += 1

        required = {
            "confidentiality",
            "termination",
            "liability",
            "indemnity",
            "governing_law",
            "payment_terms",
        }

        present = {c.get("type") for c in clauses}
        missing = sorted(list(required - present))

        return {
            "clause_count": len(clauses),
            "clause_type_breakdown": {
                "available": True,
                "dimension": "Clause Type",
                "value_field": "count",
                "all_items": {k: v for k, v in clause_types.items()},
                "top_5": [{"category": k, "count": v} for k, v in list(clause_types.items())[:5]],
            },
            "missing_clauses": missing,
            "missing_clause_count": len(missing),
        }

    def _obligation_metrics(self, contract: Dict[str, Any]) -> Dict[str, Any]:
        obligations = contract.get("obligations", [])

        by_party = defaultdict(int)
        for o in obligations:
            by_party[o.get("party", "Unknown")] += 1

        return {
            "obligation_count": len(obligations),
            "obligation_breakdown": {
                "available": True,
                "dimension": "Party",
                "value_field": "count",
                "all_items": {k: v for k, v in by_party.items()},
                "top_5": [{"category": k, "count": v} for k, v in list(by_party.items())[:5]],
            },
        }

    def _risk_metrics(self, contract, clause_metrics):
        risks = []

        missing = clause_metrics.get("missing_clauses", [])

        if "indemnity" in missing:
            risks.append("Missing indemnity clause increases liability exposure.")

        if "confidentiality" in missing:
            risks.append("Missing confidentiality clause risks data leakage.")

        if "termination" in missing:
            risks.append("Missing termination clause creates exit risk.")

        return {
            "contract_risks": risks,
            "contract_risk_count": len(risks),
        }

    def _renewal_metrics(self, contract):
        end_date = contract.get("end_date")
        if not end_date:
            return {"days_until_expiry": None}

        try:
            end = datetime.fromisoformat(end_date).date()
            today = datetime.utcnow().date()
            delta = (end - today).days
            return {"days_until_expiry": delta}
        except Exception:
            return {"days_until_expiry": None}

    # ============================================================
    # SUMMARY & INSIGHTS
    # ============================================================

    def _generate_summary(self, contract):
        a = contract.get("party_a", "Party A")
        b = contract.get("party_b", "Party B")
        eff = contract.get("effective_date", "N/A")
        end = contract.get("end_date", "N/A")

        return f"Contract between {a} and {b}, effective from {eff} to {end}."

    def _generate_insights(self, contract, clause_metrics, risk_metrics):
        insights = []

        insights.append(f"Total clauses: {clause_metrics.get('clause_count', 0)}")
        insights.append(f"Missing clauses: {clause_metrics.get('missing_clause_count', 0)}")

        for r in risk_metrics.get("contract_risks", []):
            insights.append(f"Risk: {r}")

        return insights

    def _generate_recommendations(self, contract, clause_metrics, risk_metrics):
        recs = []

        for m in clause_metrics.get("missing_clauses", []):
            recs.append({
                "priority": "high",
                "category": "missing_clause",
                "title": f"Add {m.replace('_', ' ').title()} Clause",
                "description": f"The contract is missing a {m.replace('_', ' ')} clause.",
                "potential_savings": 0,
            })

        for r in risk_metrics.get("contract_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Mitigate Contract Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ============================================================
    # UI CONFIG (Angular-compatible)
    # ============================================================

    def build_contract_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "clause_count", "label": "Total Clauses", "value_path": "clause_count", "is_hero": True},
            {"key": "missing_clause_count", "label": "Missing Clauses", "value_path": "missing_clause_count", "is_hero": True},
            {"key": "obligation_count", "label": "Obligations", "value_path": "obligation_count", "is_hero": True},
            {"key": "contract_risk_count", "label": "Risks", "value_path": "contract_risk_count", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "days_until_expiry", "label": "Days Until Expiry", "value_path": "days_until_expiry", "is_hero": False},
        ]

        charts = [
            {
                "id": "clause_type_breakdown",
                "type": "bar",
                "title": "Clause Breakdown",
                "data_path": "clause_type_breakdown.all_items",
                "label_field": "category",
                "value_field": "count",
            },
            {
                "id": "obligation_breakdown",
                "type": "bar",
                "title": "Obligations by Party",
                "data_path": "obligation_breakdown.all_items",
                "label_field": "category",
                "value_field": "count",
            },
        ]

        tables = [
            {"id": "clauses", "name": "clauses", "title": "Clauses", "data_path": "clauses"},
            {"id": "obligations", "name": "obligations", "title": "Obligations", "data_path": "obligations"},
            {"id": "missing_clauses", "name": "missing_clauses", "title": "Missing Clauses", "data_path": "missing_clauses"},
            {"id": "contract_risks", "name": "contract_risks", "title": "Risks", "data_path": "contract_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Contract Summary",
                "metrics": ["clause_count", "missing_clause_count", "obligation_count", "contract_risk_count"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "clauses",
                "title": "Clauses",
                "metrics": [],
                "charts": ["clause_type_breakdown"],
                "tables": ["clauses", "missing_clauses"],
            },
            {
                "id": "obligations",
                "title": "Obligations",
                "metrics": [],
                "charts": ["obligation_breakdown"],
                "tables": ["obligations"],
            },
            {
                "id": "risk",
                "title": "Risk Analysis",
                "metrics": ["contract_risk_count", "days_until_expiry"],
                "charts": [],
                "tables": ["contract_risks"],
            },
            {
                "id": "recommendations",
                "title": "Recommendations",
                "metrics": [],
                "charts": [],
                "tables": ["recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
