import logging
from typing import Dict, Any, List

from app.parsers.extractors.insurance_policy_intelligence import InsurancePolicyIntelligence
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class InsurancePolicyAnalyzer(BaseAnalyzer):
    """
    Analyzer for insurance policies:
    - Policyholder, policy number, agent
    - Coverage types, limits, deductibles
    - Premiums (monthly/annual)
    - Risk flags: missing limits, high deductible, missing exclusions
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]):
        logger.info(f"🛡️ Insurance Policy analysis: {file_path}")

        policy = self._extract_policy(file_path, text, metadata)
        metrics = self._coverage_metrics(policy)
        risk_metrics = self._risk_metrics(policy, metrics)
        insights = self._generate_insights(policy, metrics, risk_metrics)
        recommendations = self._generate_recommendations(policy, metrics, risk_metrics)

        result = {
            "type": "insurance_policy",
            "summary": self._generate_summary(policy, metrics),
            "confidence": 0.94,

            "policy_number": policy.get("policy_number"),
            "policyholder_name": policy.get("policyholder_name"),
            "agent_name": policy.get("agent_name"),
            "effective_date": policy.get("effective_date"),
            "expiration_date": policy.get("expiration_date"),
            "policy_type": policy.get("policy_type"),

            **metrics,
            **risk_metrics,

            "coverage_items": policy.get("coverage_items", []),
            "exclusions": policy.get("exclusions", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        # 🔍 Intelligence layer
        try:
            intel = InsurancePolicyIntelligence().analyze(result)
            result["risk_intelligence"] = intel.to_dict()
            result["risk_intelligence_kv"] = intel.to_kv_list()
            result["has_advanced_analytics"] = True
        except Exception as e:
            logger.error(f"❌ Insurance risk intelligence failed: {e}")
            result["has_advanced_analytics"] = False

        result["ui_config"] = self.build_policy_ui_config(result)
        return result

        # result["ui_config"] = self.build_policy_ui_config(result)
        # result["has_advanced_analytics"] = True
        # return result

    @staticmethod
    def safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return float(value)
            v = str(value).replace(",", "").strip()
            return float(v) if v else default
        except Exception:
            return default

    # ---------------- EXTRACTION ----------------

    def _extract_policy(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_policy_parser import UniversalPolicyParser
            parser = UniversalPolicyParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Policy parsing failed: {e}")
            parsed = {}

        return {
            "policy_number": parsed.get("policy_number"),
            "policyholder_name": parsed.get("policyholder_name"),
            "agent_name": parsed.get("agent_name"),
            "effective_date": parsed.get("effective_date"),
            "expiration_date": parsed.get("expiration_date"),
            "policy_type": parsed.get("policy_type"),
            "premium_amount": self.safe_float(parsed.get("premium_amount")),
            "deductible": self.safe_float(parsed.get("deductible")),
            "coverage_items": parsed.get("coverage_items", []),  # [{type, limit}]
            "exclusions": parsed.get("exclusions", []),
        }

    # ---------------- METRICS ----------------

    def _coverage_metrics(self, policy):
        items = policy.get("coverage_items", [])
        total_limit = sum(self.safe_float(i.get("limit", 0)) for i in items)

        return {
            "coverage_count": len(items),
            "total_coverage_limit": round(total_limit, 2),
            "premium_amount": policy.get("premium_amount", 0.0),
            "deductible": policy.get("deductible", 0.0),
        }

    # ---------------- RISK ----------------

    def _risk_metrics(self, policy, metrics):
        risks = []

        if metrics["deductible"] > 2000:
            risks.append("High deductible may increase out-of-pocket costs.")

        if metrics["coverage_count"] == 0:
            risks.append("No coverage items detected.")

        if not policy.get("exclusions"):
            risks.append("No exclusions listed — may indicate incomplete policy details.")

        return {
            "policy_risks": risks,
            "policy_risk_count": len(risks),
        }

    # ---------------- SUMMARY / INSIGHTS ----------------

    def _generate_summary(self, policy, metrics):
        holder = policy.get("policyholder_name", "Policyholder")
        num = policy.get("policy_number", "N/A")
        prem = metrics.get("premium_amount", 0.0)
        return f"Insurance policy {num} for {holder} with premium ${prem:,.2f}."

    def _generate_insights(self, policy, metrics, risk_metrics):
        insights = [
            f"Coverage items: {metrics['coverage_count']}",
            f"Total coverage limit: ${metrics['total_coverage_limit']:,.2f}",
            f"Deductible: ${metrics['deductible']:,.2f}",
        ]

        if risk_metrics["policy_risk_count"] > 0:
            insights.append(f"Risks identified: {risk_metrics['policy_risk_count']}.")

        return insights

    def _generate_recommendations(self, policy, metrics, risk_metrics):
        recs = []

        for r in risk_metrics["policy_risks"]:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review Policy Risk",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ---------------- UI CONFIG ----------------

    def build_policy_ui_config(self, result):
        hero = [
            {"key": "premium_amount", "label": "Premium", "value_path": "premium_amount", "is_hero": True},
            {"key": "deductible", "label": "Deductible", "value_path": "deductible", "is_hero": True},
            {"key": "coverage_count", "label": "Coverage Items", "value_path": "coverage_count", "is_hero": True},
            {"key": "policy_risk_count", "label": "Risk Flags", "value_path": "policy_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "coverage_items", "name": "coverage_items", "title": "Coverage Items", "data_path": "coverage_items"},
            {"id": "exclusions", "name": "exclusions", "title": "Exclusions", "data_path": "exclusions"},
            {"id": "policy_risks", "name": "policy_risks", "title": "Risks", "data_path": "policy_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
            {
                "id": "risk_intelligence",
                "name": "risk_intelligence",
                "title": "Risk Intelligence",
                "data_path": "risk_intelligence_kv",
            },
        ]

        sections = [
            {"id": "summary", "title": "Policy Summary", "metrics": ["premium_amount", "deductible", "coverage_count"], "charts": [], "tables": []},
            {"id": "coverage", "title": "Coverage", "metrics": [], "charts": [], "tables": ["coverage_items", "exclusions"]},
            {
                "id": "risk",
                "title": "Risk & Recommendations",
                "metrics": ["policy_risk_count"],
                "charts": [],
                "tables": ["policy_risks", "risk_intelligence", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero,
            "charts": [],
            "tables": tables,
            "sections": sections,
        }


    # def build_policy_ui_config(self, result):
    #     hero = [
    #         {"key": "premium_amount", "label": "Premium", "value_path": "premium_amount", "is_hero": True},
    #         {"key": "deductible", "label": "Deductible", "value_path": "deductible", "is_hero": True},
    #         {"key": "coverage_count", "label": "Coverage Items", "value_path": "coverage_count", "is_hero": True},
    #         {"key": "policy_risk_count", "label": "Risk Flags", "value_path": "policy_risk_count", "is_hero": True},
    #     ]
    #
    #     tables = [
    #         {"id": "coverage_items", "name": "coverage_items", "title": "Coverage Items", "data_path": "coverage_items"},
    #         {"id": "exclusions", "name": "exclusions", "title": "Exclusions", "data_path": "exclusions"},
    #         {"id": "policy_risks", "name": "policy_risks", "title": "Risks", "data_path": "policy_risks"},
    #         {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
    #     ]
    #
    #     sections = [
    #         {"id": "summary", "title": "Policy Summary", "metrics": ["premium_amount", "deductible", "coverage_count"], "charts": [], "tables": []},
    #         {"id": "coverage", "title": "Coverage", "metrics": [], "charts": [], "tables": ["coverage_items", "exclusions"]},
    #         {"id": "risk", "title": "Risk & Recommendations", "metrics": ["policy_risk_count"], "charts": [], "tables": ["policy_risks", "recommendations"]},
    #     ]
    #
    #     return {
    #         "hero_metrics": hero,
    #         "charts": [],
    #         "tables": tables,
    #         "sections": sections,
    #     }
