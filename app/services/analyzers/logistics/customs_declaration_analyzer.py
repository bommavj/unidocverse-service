import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class CustomsDeclarationAnalyzer(BaseAnalyzer):
    """
    Analyzer for customs declarations:
    - Declaration number, importer/exporter, HS codes, country of origin
    - Declared values, duties, taxes, tariff classifications
    - Risk flags: missing HS codes, undervalued shipments, restricted items
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🌐 Customs declaration analysis: {file_path}")

        doc = self._extract_declaration(file_path, text, metadata)

        items = doc.get("line_items", [])
        # Normalize items: description, hs_code, quantity, unit_value, total_value, country_of_origin
        normalized = []
        for it in items or []:
            qty = self.safe_float(it.get("quantity", 1))
            unit = self.safe_float(it.get("unit_value", 0))
            total = self.safe_float(it.get("total_value", qty * unit))
            normalized.append({
                "description": it.get("description"),
                "hs_code": it.get("hs_code"),
                "quantity": qty,
                "unit_value": unit,
                "total_value": total,
                "country_of_origin": it.get("country_of_origin"),
                "_raw": it,
            })

        total_declared = round(sum(i["total_value"] for i in normalized), 2)
        item_count = len(normalized)

        hs_missing = [i for i in normalized if not i.get("hs_code")]
        undervalued = [i for i in normalized if i.get("unit_value", 0) < 0.5]  # heuristic threshold
        restricted = [i for i in normalized if self._is_restricted(i.get("description", ""))]

        insights = [
            f"Total declared value: ${total_declared:,.2f}",
            f"Line items: {item_count}",
        ]
        if hs_missing:
            insights.append(f"{len(hs_missing)} items missing HS codes.")
        if undervalued:
            insights.append(f"{len(undervalued)} items flagged as potentially undervalued.")
        if restricted:
            insights.append(f"{len(restricted)} items match restricted goods patterns.")

        recommendations = []
        if hs_missing:
            recommendations.append({
                "priority": "high",
                "category": "classification",
                "title": "Add HS Codes",
                "description": f"{len(hs_missing)} items are missing HS codes; classify to avoid delays.",
                "potential_savings": 0,
            })
        if undervalued:
            recommendations.append({
                "priority": "medium",
                "category": "valuation",
                "title": "Review Unit Values",
                "description": "Some unit values appear unusually low; verify invoice and customs value.",
                "potential_savings": 0,
            })
        if restricted:
            recommendations.append({
                "priority": "high",
                "category": "compliance",
                "title": "Restricted Items Detected",
                "description": "Items matching restricted categories found; confirm permits or remove from shipment.",
                "potential_savings": 0,
            })

        result = {
            "type": "customs_declaration",
            "summary": self._generate_summary(doc, total_declared),
            "confidence": 0.94,

            "declaration_number": doc.get("declaration_number"),
            "importer": doc.get("importer"),
            "exporter": doc.get("exporter"),
            "declaration_date": doc.get("declaration_date"),
            "country_of_export": doc.get("country_of_export"),
            "country_of_import": doc.get("country_of_import"),
            "currency": doc.get("currency", "USD"),

            "line_items": normalized,
            "total_declared_value": total_declared,
            "line_item_count": item_count,

            "hs_missing_count": len(hs_missing),
            "undervalued_count": len(undervalued),
            "restricted_item_count": len(restricted),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_customs_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_declaration(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_customs_parser import UniversalCustomsParser
            parser = UniversalCustomsParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Customs parsing failed: {e}")
            parsed = {}

        return {
            "declaration_number": parsed.get("declaration_number") or metadata.get("declaration_number"),
            "importer": parsed.get("importer"),
            "exporter": parsed.get("exporter"),
            "declaration_date": parsed.get("declaration_date"),
            "country_of_export": parsed.get("country_of_export"),
            "country_of_import": parsed.get("country_of_import"),
            "currency": parsed.get("currency", "USD"),
            "line_items": parsed.get("line_items", []),
        }

    # ---------------- HELPERS ----------------

    def _is_restricted(self, description: str) -> bool:
        desc = (description or "").lower()
        restricted_keywords = ["explosive", "weapon", "hazardous", "radioactive", "controlled substance", "chemical weapon"]
        return any(k in desc for k in restricted_keywords)

    def _generate_summary(self, doc: Dict[str, Any], total_declared: float) -> str:
        num = doc.get("declaration_number", "N/A")
        imp = doc.get("importer", "Importer")
        return f"Customs declaration {num} for {imp} with declared value ${total_declared:,.2f}."

    # ---------------- UI CONFIG ----------------

    def build_customs_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "total_declared_value", "label": "Declared Value", "value_path": "total_declared_value", "is_hero": True},
            {"key": "line_item_count", "label": "Line Items", "value_path": "line_item_count", "is_hero": True},
            {"key": "hs_missing_count", "label": "HS Codes Missing", "value_path": "hs_missing_count", "is_hero": True},
            {"key": "restricted_item_count", "label": "Restricted Items", "value_path": "restricted_item_count", "is_hero": True},
        ]

        charts = [
            {
                "id": "value_by_country",
                "type": "bar",
                "title": "Value by Country of Origin",
                "data_path": "line_items",
                "label_field": "country_of_origin",
                "value_field": "total_value",
            }
        ]

        tables = [
            {"id": "line_items", "name": "line_items", "title": "Line Items", "data_path": "line_items"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Declaration Summary", "metrics": ["total_declared_value", "line_item_count", "hs_missing_count"], "charts": [], "tables": []},
            {"id": "items", "title": "Line Items", "metrics": [], "charts": ["value_by_country"], "tables": ["line_items"]},
            {"id": "recommendations", "title": "Recommendations", "metrics": [], "charts": [], "tables": ["recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
