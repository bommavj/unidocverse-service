# app/services/analyzers/vendor/vendor_agreement_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class VendorAgreementAnalyzer(BaseAnalyzer):
    """
    Analyzer for vendor and supplier agreements:
    - Vendor identity, contract id, term, pricing, SLAs
    - Payment terms, termination, renewal, liability caps
    - Risk flags: missing SLA, auto-renew, unfavorable payment terms
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🤝 Vendor agreement analysis: {file_path}")

        agreement = self._extract_agreement(file_path, text, metadata)
        clause_metrics = self._clause_metrics(agreement)
        monetary = self._monetary_metrics(agreement)
        risk_metrics = self._risk_metrics(agreement, clause_metrics, monetary)
        insights = self._generate_insights(agreement, clause_metrics, monetary, risk_metrics)
        recommendations = self._generate_recommendations(agreement, clause_metrics, monetary, risk_metrics)

        result = {
            "type": "vendor_agreement",
            "summary": self._generate_summary(agreement, monetary),
            "confidence": 0.94,

            "contract_id": agreement.get("contract_id"),
            "vendor_id": agreement.get("vendor_id"),
            "vendor_name": agreement.get("vendor_name"),
            "effective_date": agreement.get("effective_date"),
            "expiration_date": agreement.get("expiration_date"),
            "term_months": agreement.get("term_months"),

            **clause_metrics,
            **monetary,
            **risk_metrics,

            "clauses": agreement.get("clauses", []),
            "pricing_terms": agreement.get("pricing_terms", {}),
            "slas": agreement.get("slas", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_vendor_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_agreement(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_vendor_parser import UniversalVendorParser
            parser = UniversalVendorParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Vendor agreement parsing failed: {e}")
            parsed = {}

        return {
            "contract_id": parsed.get("contract_id") or metadata.get("contract_id"),
            "vendor_id": parsed.get("vendor_id"),
            "vendor_name": parsed.get("vendor_name"),
            "effective_date": parsed.get("effective_date"),
            "expiration_date": parsed.get("expiration_date"),
            "term_months": parsed.get("term_months"),
            "clauses": parsed.get("clauses", []),  # [{type, title, text}]
            "pricing_terms": parsed.get("pricing_terms", {}),  # {currency, base_fee, recurring_fee, escalation}
            "slas": parsed.get("slas", []),  # [{metric, target, remedy}]
            "payment_terms": parsed.get("payment_terms"),
        }

    # Clause metrics
    def _clause_metrics(self, agreement: Dict[str, Any]) -> Dict[str, Any]:
        clauses = agreement.get("clauses", [])
        types = {}
        for c in clauses:
            t = c.get("type", "other")
            types[t] = types.get(t, 0) + 1

        required = {"termination", "renewal", "liability", "confidentiality", "payment_terms", "service_levels"}
        present = {c.get("type") for c in clauses}
        missing = sorted(list(required - present))

        return {
            "clause_count": len(clauses),
            "clause_type_breakdown": types,
            "missing_clauses": missing,
            "missing_clause_count": len(missing),
        }

    # Monetary metrics
    def _monetary_metrics(self, agreement: Dict[str, Any]) -> Dict[str, Any]:
        pt = agreement.get("pricing_terms", {})
        base = self.safe_float(pt.get("base_fee", 0))
        recurring = self.safe_float(pt.get("recurring_fee", 0))
        escalation = pt.get("escalation", None)
        return {
            "base_fee": round(base, 2),
            "recurring_fee": round(recurring, 2),
            "escalation": escalation,
            "payment_terms": agreement.get("payment_terms"),
        }

    # Risk metrics
    def _risk_metrics(self, agreement: Dict[str, Any], clause_metrics: Dict[str, Any], monetary: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        if clause_metrics.get("missing_clause_count", 0) > 0:
            risks.append("Missing key contractual clauses.")
        if monetary.get("payment_terms") and "net" in str(monetary.get("payment_terms")).lower():
            # flag long net terms
            try:
                if "net" in str(monetary.get("payment_terms")).lower():
                    # heuristic: if net 60 or more
                    if "net 60" in str(monetary.get("payment_terms")).lower() or "net 90" in str(monetary.get("payment_terms")).lower():
                        risks.append("Long payment terms may impact cash flow.")
            except Exception:
                pass
        if agreement.get("slas") and any(not s.get("remedy") for s in agreement.get("slas", [])):
            risks.append("Some SLAs lack explicit remedies or credits.")
        if agreement.get("expiration_date") is None and agreement.get("term_months") is None:
            risks.append("No clear expiration or term defined; auto-renew risk.")
        return {
            "vendor_risks": risks,
            "vendor_risk_count": len(risks),
        }

    # Summary, insights, recommendations
    def _generate_summary(self, agreement: Dict[str, Any], monetary: Dict[str, Any]) -> str:
        vendor = agreement.get("vendor_name", "Vendor")
        base = monetary.get("base_fee", 0.0)
        return f"Vendor agreement with {vendor}. Base fee ${base:,.2f}."

    def _generate_insights(self, agreement, clause_metrics, monetary, risk_metrics) -> List[str]:
        insights = []
        insights.append(f"Total clauses: {clause_metrics.get('clause_count', 0)}")
        insights.append(f"Base fee: ${monetary.get('base_fee', 0.0):,.2f}")
        if risk_metrics.get("vendor_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['vendor_risk_count']}.")
        return insights

    def _generate_recommendations(self, agreement, clause_metrics, monetary, risk_metrics) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for m in clause_metrics.get("missing_clauses", []):
            recs.append({
                "priority": "high",
                "category": "missing_clause",
                "title": f"Add {m.replace('_',' ').title()} Clause",
                "description": f"Agreement missing {m.replace('_',' ')} clause.",
                "potential_savings": 0,
            })
        for r in risk_metrics.get("vendor_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Mitigate Vendor Risk",
                "description": r,
                "potential_savings": 0,
            })
        # Pricing recommendation example
        if monetary.get("escalation") and isinstance(monetary.get("escalation"), str) and "cpi" in monetary.get("escalation").lower():
            recs.append({
                "priority": "low",
                "category": "cost",
                "title": "Review Escalation Clause",
                "description": "Escalation tied to CPI; consider caps to limit future increases.",
                "potential_savings": 0,
            })
        return recs

    # UI config
    def build_vendor_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "clause_count", "label": "Total Clauses", "value_path": "clause_count", "is_hero": True},
            {"key": "missing_clause_count", "label": "Missing Clauses", "value_path": "missing_clause_count", "is_hero": True},
            {"key": "base_fee", "label": "Base Fee", "value_path": "base_fee", "is_hero": True},
            {"key": "vendor_risk_count", "label": "Risk Flags", "value_path": "vendor_risk_count", "is_hero": True},
        ]

        charts = [
            {
                "id": "clause_type_breakdown",
                "type": "bar",
                "title": "Clause Types",
                "data_path": "clause_type_breakdown",
                "label_field": "category",
                "value_field": "count",
            }
        ]

        tables = [
            {"id": "clauses", "name": "clauses", "title": "Clauses", "data_path": "clauses"},
            {"id": "slas", "name": "slas", "title": "Service Level Agreements", "data_path": "slas"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Agreement Summary", "metrics": ["base_fee", "recurring_fee", "term_months", "vendor_risk_count"], "charts": [], "tables": []},
            {"id": "clauses", "title": "Clauses", "metrics": ["clause_count", "missing_clause_count"], "charts": ["clause_type_breakdown"], "tables": ["clauses"]},
            {"id": "slas", "title": "SLAs & Pricing", "metrics": [], "charts": [], "tables": ["slas", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
