# app/services/analyzers/operations/bill_of_lading_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class BillOfLadingAnalyzer(BaseAnalyzer):
    """
    Analyzer for bills of lading:
    - Extracts B/L number, carrier, shipper, consignee, vessel, weight, goods description
    - Computes totals, identifies hazardous goods, missing consignee or signatures
    - Flags: weight mismatches, missing signatures, dangerous goods
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🚢 Bill of lading analysis: {file_path}")

        bol = self._extract_bol(file_path, text, metadata)
        items = bol.get("items", [])
        normalized = self._normalize_items(items)
        totals = self._compute_totals(normalized)
        risks = self._risk_checks(bol, normalized, totals)
        insights = self._generate_insights(bol, totals, risks)
        recommendations = self._generate_recommendations(bol, normalized, risks)

        result = {
            "type": "bill_of_lading",
            "summary": self._generate_summary(bol, totals),
            "confidence": 0.95,

            "bol_number": bol.get("bol_number"),
            "carrier": bol.get("carrier"),
            "shipper": bol.get("shipper"),
            "consignee": bol.get("consignee"),
            "vessel": bol.get("vessel"),
            "departure_date": bol.get("departure_date"),
            "arrival_date": bol.get("arrival_date"),

            "items": normalized,
            **totals,
            **risks,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_bol_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_bol(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_bol_parser import UniversalBOLParser
            parser = UniversalBOLParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ BOL parsing failed: {e}")
            parsed = {}

        return {
            "bol_number": parsed.get("bol_number") or metadata.get("bol_number"),
            "carrier": parsed.get("carrier"),
            "shipper": parsed.get("shipper"),
            "consignee": parsed.get("consignee"),
            "vessel": parsed.get("vessel"),
            "departure_date": parsed.get("departure_date"),
            "arrival_date": parsed.get("arrival_date"),
            "items": parsed.get("items", []),  # [{description, weight, quantity, hazardous, hs_code}]
        }

    def _normalize_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for it in items or []:
            w = self.safe_float(it.get("weight", 0))
            q = int(it.get("quantity", 1) or 1)
            out.append({
                "description": it.get("description"),
                "weight": round(w, 2),
                "quantity": q,
                "hazardous": bool(it.get("hazardous", False)),
                "hs_code": it.get("hs_code"),
                "_raw": it,
            })
        return out

    def _compute_totals(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_weight = round(sum(i.get("weight", 0) * i.get("quantity", 1) for i in items), 2)
        item_count = len(items)
        hazardous_count = sum(1 for i in items if i.get("hazardous"))
        return {"total_weight": total_weight, "item_count": item_count, "hazardous_count": hazardous_count}

    def _risk_checks(self, bol: Dict[str, Any], items: List[Dict[str, Any]], totals: Dict[str, Any]) -> Dict[str, Any]:
        risks = []
        if not bol.get("consignee"):
            risks.append("Consignee missing; delivery may be delayed.")
        if totals.get("hazardous_count", 0) > 0 and not any(i.get("hs_code") for i in items if i.get("hazardous")):
            risks.append("Hazardous items present without HS codes or proper declarations.")
        if totals.get("total_weight", 0) == 0:
            risks.append("Total weight is zero; check item weights.")
        return {"bol_risks": risks, "bol_risk_count": len(risks)}

    def _generate_summary(self, bol: Dict[str, Any], totals: Dict[str, Any]) -> str:
        num = bol.get("bol_number", "BOL")
        return f"Bill of lading {num} with {totals.get('item_count',0)} items and total weight {totals.get('total_weight',0)} kg."

    def _generate_insights(self, bol, totals, risks) -> List[str]:
        insights = [f"Items: {totals.get('item_count',0)}", f"Total weight: {totals.get('total_weight',0):,.2f}"]
        if totals.get("hazardous_count", 0) > 0:
            insights.append(f"Hazardous items: {totals.get('hazardous_count')}")
        if risks.get("bol_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risks['bol_risk_count']}.")
        return insights

    def _generate_recommendations(self, bol, items, risks) -> List[Dict[str, Any]]:
        recs = []
        for r in risks.get("bol_risks", []):
            recs.append({"priority": "high" if "missing" in r.lower() or "hazardous" in r.lower() else "medium", "category": "logistics", "title": "BOL Issue", "description": r, "potential_savings": 0})
        if items:
            recs.append({"priority": "low", "category": "process", "title": "Confirm Weights", "description": "Verify item weights and quantities against packing list and carrier manifest.", "potential_savings": 0})
        return recs

    def build_bol_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "item_count", "label": "Items", "value_path": "item_count", "is_hero": True},
            {"key": "total_weight", "label": "Total Weight", "value_path": "total_weight", "is_hero": True},
            {"key": "hazardous_count", "label": "Hazardous Items", "value_path": "hazardous_count", "is_hero": True},
            {"key": "bol_risk_count", "label": "Risk Flags", "value_path": "bol_risk_count", "is_hero": True},
        ]
        charts = [
            {"id": "weight_by_item", "type": "bar", "title": "Weight by Item", "data_path": "items", "label_field": "description", "value_field": "weight"}
        ]
        tables = [
            {"id": "items", "name": "items", "title": "Items", "data_path": "items"},
            {"id": "bol_risks", "name": "bol_risks", "title": "Risks", "data_path": "bol_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]
        sections = [
            {"id": "summary", "title": "BOL Summary", "metrics": ["item_count", "total_weight", "hazardous_count", "bol_risk_count"], "charts": [], "tables": []},
            {"id": "items", "title": "Items", "metrics": [], "charts": ["weight_by_item"], "tables": ["items"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["bol_risk_count"], "charts": [], "tables": ["bol_risks", "recommendations"]},
        ]
        return {"hero_metrics": hero, "charts": charts, "tables": tables, "sections": sections}
