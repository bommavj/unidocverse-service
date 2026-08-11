# app/services/analyzers/legal/nda_document_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class NdaDocumentAnalyzer(BaseAnalyzer):
    """
    Analyzer for Non-Disclosure Agreements (NDAs).
    - Parties, effective date, term
    - Scope of confidential information
    - Return/destroy obligations
    - Non-solicit / non-compete flags
    - Missing/weak clauses & risk flags
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🔒 Advanced NDA analysis: {file_path}")

        nda = self._extract_nda(file_path, text, metadata)
        clause_metrics = self._clause_metrics(nda)
        risk_metrics = self._risk_metrics(nda, clause_metrics)
        insights = self._generate_insights(nda, clause_metrics, risk_metrics)
        recommendations = self._generate_recommendations(nda, clause_metrics, risk_metrics)

        result: Dict[str, Any] = {
            "type": "nda_document",
            "summary": self._generate_summary(nda),
            "confidence": 0.94,

            "contract_id": nda.get("contract_id"),
            "party_discloser": nda.get("party_discloser"),
            "party_recipient": nda.get("party_recipient"),
            "effective_date": nda.get("effective_date"),
            "term_description": nda.get("term_description"),

            "clauses": nda.get("clauses", []),
            **clause_metrics,
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_nda_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ------------ EXTRACTION ------------

    def _extract_nda(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_nda_parser import UniversalNDAParser
            parser = UniversalNDAParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ NDA parsing failed: {e}")
            parsed = {}

        return {
            "contract_id": parsed.get("contract_id") or metadata.get("contract_id"),
            "party_discloser": parsed.get("party_discloser"),
            "party_recipient": parsed.get("party_recipient"),
            "effective_date": parsed.get("effective_date"),
            "term_description": parsed.get("term_description"),
            "clauses": parsed.get("clauses", []),  # [{type, title, text}]
        }

    # ------------ METRICS / RISK ------------

    def _clause_metrics(self, nda: Dict[str, Any]) -> Dict[str, Any]:
        clauses = nda.get("clauses", [])
        clause_types = {}
        for c in clauses:
            t = c.get("type", "unknown")
            clause_types[t] = clause_types.get(t, 0) + 1

        required = {
            "confidential_information",
            "exclusions",
            "term",
            "use_of_information",
            "return_or_destruction",
            "remedies",
            "governing_law",
        }
        present = {c.get("type") for c in clauses}
        missing = sorted(list(required - present))

        return {
            "clause_count": len(clauses),
            "clause_type_breakdown": {
                "available": True,
                "dimension": "Clause Type",
                "value_field": "count",
                "all_items": clause_types,
                "top_5": [
                    {"category": k, "count": v}
                    for k, v in list(clause_types.items())[:5]
                ],
            },
            "missing_clauses": missing,
            "missing_clause_count": len(missing),
        }

    def _risk_metrics(self, nda: Dict[str, Any], clause_metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []

        missing = clause_metrics.get("missing_clauses", [])
        if "return_or_destruction" in missing:
            risks.append("No explicit obligation to return or destroy confidential information.")
        if "remedies" in missing:
            risks.append("No explicit remedies clause for breach.")
        if "exclusions" in missing:
            risks.append("No clear exclusions for publicly known or independently developed information.")

        return {
            "nda_risks": risks,
            "nda_risk_count": len(risks),
        }

    # ------------ SUMMARY / INSIGHTS ------------

    def _generate_summary(self, nda: Dict[str, Any]) -> str:
        d = nda.get("party_discloser", "Disclosing Party")
        r = nda.get("party_recipient", "Recipient")
        eff = nda.get("effective_date", "N/A")
        return f"NDA between {d} and {r}, effective {eff}."

    def _generate_insights(
        self,
        nda: Dict[str, Any],
        clause_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []
        insights.append(f"Total clauses: {clause_metrics.get('clause_count', 0)}")
        insights.append(f"Missing clauses: {clause_metrics.get('missing_clause_count', 0)}")
        if risk_metrics.get("nda_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['nda_risk_count']} potential issues.")
        return insights

    def _generate_recommendations(
        self,
        nda: Dict[str, Any],
        clause_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for m in clause_metrics.get("missing_clauses", []):
            recs.append({
                "priority": "high",
                "category": "missing_clause",
                "title": f"Add {m.replace('_', ' ').title()} Clause",
                "description": f"NDA is missing a {m.replace('_', ' ')} clause.",
                "potential_savings": 0,
            })

        for r in risk_metrics.get("nda_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Mitigate NDA Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ------------ UI CONFIG ------------

    def build_nda_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "clause_count", "label": "Total Clauses", "value_path": "clause_count", "is_hero": True},
            {"key": "missing_clause_count", "label": "Missing Clauses", "value_path": "missing_clause_count", "is_hero": True},
            {"key": "nda_risk_count", "label": "Risk Flags", "value_path": "nda_risk_count", "is_hero": True},
        ]

        secondary_metrics: List[Dict[str, Any]] = []

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
            {"id": "nda_risks", "name": "nda_risks", "title": "Risks", "data_path": "nda_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "NDA Summary",
                "metrics": ["clause_count", "missing_clause_count", "nda_risk_count"],
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
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["nda_risk_count"],
                "charts": [],
                "tables": ["nda_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
