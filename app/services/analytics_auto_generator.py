"""
Analytics Auto-Generator
AI-Powered automatic UI configuration generator for ANY document type
"""
import logging
import re
import json
from typing import Dict, Any, List
import ollama

from app.core import config

logger = logging.getLogger(__name__)


class AnalyticsAutoGenerator:
    """
    AI-Powered Universal Analytics Generator
    Automatically generates complete UI configurations for any document type
    """

    # ========================================
    # DOCUMENT TYPE TO SUITE MAPPING (60+ types)
    # ========================================

    DOCUMENT_SUITES = {
        # FINANCIAL SUITE (12 types)
        "sales_data": "financial",
        "bank_statement": "financial",
        "invoice": "financial",
        "receipt": "financial",
        "purchase_order": "financial",
        "expense_report": "financial",
        "credit_memo": "financial",
        "statement_of_work": "financial",
        "wire_transfer": "financial",
        "financial_report": "financial",
        "inventory": "financial",
        "spreadsheet": "financial",
        # LEGAL SUITE (7 types)
        "contract": "legal",
        "nda_document": "legal",
        "lease_agreement": "legal",
        "power_of_attorney": "legal",
        "will_testament": "legal",
        "patent_application": "legal",
        "trademark_registration": "legal",
        # HR SUITE (6 types)
        "resume": "hr",
        "payroll_stub": "hr",
        "offer_letter": "hr",
        "performance_review": "hr",
        "termination_letter": "hr",
        "job_description": "hr",
        # MEDICAL SUITE (2 types)
        "medical_report": "medical",
        "prescription": "medical",
        # INSURANCE SUITE (2 types)
        "insurance_policy": "insurance",
        "insurance_claim": "insurance",
        # EDUCATION SUITE (3 types)
        "transcript": "education",
        "diploma": "education",
        "enrollment_form": "education",
        # REAL ESTATE SUITE (4 types)
        "appraisal_report": "real_estate",
        "home_inspection": "real_estate",
        "mortgage_document": "real_estate",
        "property_deed": "real_estate",
        # LOGISTICS SUITE (4 types)
        "customs_declaration": "logistics",
        "delivery_receipt": "logistics",
        "bill_of_lading": "logistics",
        "packing_slip": "logistics",
        # COMPLIANCE SUITE (4 types)
        "audit_report": "compliance",
        "safety_data_sheet": "compliance",
        "certificate_of_insurance": "compliance",
        "certificate_of_compliance": "compliance",
        # VENDOR SUITE (2 types)
        "vendor_agreement": "vendor",
        "rfp_document": "vendor",
        # PROJECT MANAGEMENT SUITE (2 types)
        "project_charter": "project_management",
        "risk_assessment": "project_management",
        # QA SUITE (1 type)
        "test_report": "qa",
        # TAX SUITE (1 type)
        "tax_document": "tax",
        # OPERATIONS SUITE (2 types)
        "operations_report": "operations",
        "maintenance_log": "operations",
        # GOVERNMENT SUITE (3 types)
        "identification": "government",
        "permit": "government",
        "legal_notice": "government",
        # MARKETING SUITE (2 types)
        "marketing_brief": "marketing",
        "press_release": "marketing",
        # TECHNICAL SUITE (2 types)
        "technical_specification": "technical",
        "user_manual": "technical",
        # ADMINISTRATIVE SUITE (1 type)
        "meeting_minutes": "administrative",
        # GENERAL SUITE (5 types)
        "report_generic": "general",
        "memo": "general",
        "questionnaire": "general",
        "warranty_document": "general",
        "unknown": "general",
    }

    DOCUMENT_ICONS = {
        "financial": "💰",
        "legal": "⚖️",
        "hr": "👥",
        "medical": "🏥",
        "insurance": "🛡️",
        "education": "🎓",
        "real_estate": "🏠",
        "logistics": "📦",
        "compliance": "✅",
        "vendor": "🤝",
        "project_management": "📊",
        "qa": "🔍",
        "tax": "💵",
        "operations": "⚙️",
        "government": "🏛️",
        "marketing": "📢",
        "technical": "🔧",
        "administrative": "📋",
        "general": "📄",
        "dataset": "📊",
    }

    SUITE_NAMES = {
        "financial": "Financial",
        "legal": "Legal",
        "hr": "Human Resources",
        "medical": "Medical",
        "insurance": "Insurance",
        "education": "Education",
        "real_estate": "Real Estate",
        "logistics": "Logistics",
        "compliance": "Compliance",
        "vendor": "Vendor Management",
        "project_management": "Project Management",
        "qa": "Quality Assurance",
        "tax": "Tax",
        "operations": "Operations",
        "government": "Government",
        "marketing": "Marketing",
        "technical": "Technical",
        "administrative": "Administrative",
        "general": "General",
        "dataset": "Dataset",
    }

    SUITE_COLORS = {
        "dataset": {"primary": "#3b82f6", "secondary": "#2563eb", "accent": "#60a5fa"},
        "financial": {"primary": "#10b981", "secondary": "#059669", "accent": "#34d399"},
        "legal": {"primary": "#6366f1", "secondary": "#4f46e5", "accent": "#818cf8"},
        "hr": {"primary": "#f59e0b", "secondary": "#d97706", "accent": "#fbbf24"},
        "medical": {"primary": "#ef4444", "secondary": "#dc2626", "accent": "#f87171"},
        "insurance": {"primary": "#14b8a6", "secondary": "#0d9488", "accent": "#2dd4bf"},
        "education": {"primary": "#8b5cf6", "secondary": "#7c3aed", "accent": "#a78bfa"},
        "real_estate": {"primary": "#f97316", "secondary": "#ea580c", "accent": "#fb923c"},
        "logistics": {"primary": "#06b6d4", "secondary": "#0891b2", "accent": "#22d3ee"},
        "compliance": {"primary": "#84cc16", "secondary": "#65a30d", "accent": "#a3e635"},
        "vendor": {"primary": "#ec4899", "secondary": "#db2777", "accent": "#f472b6"},
        "project_management": {"primary": "#3b82f6", "secondary": "#2563eb", "accent": "#60a5fa"},
        "qa": {"primary": "#10b981", "secondary": "#059669", "accent": "#34d399"},
        "tax": {"primary": "#14b8a6", "secondary": "#0d9488", "accent": "#2dd4bf"},
        "operations": {"primary": "#64748b", "secondary": "#475569", "accent": "#94a3b8"},
        "government": {"primary": "#475569", "secondary": "#334155", "accent": "#64748b"},
        "marketing": {"primary": "#ec4899", "secondary": "#db2777", "accent": "#f472b6"},
        "technical": {"primary": "#06b6d4", "secondary": "#0891b2", "accent": "#22d3ee"},
        "administrative": {"primary": "#64748b", "secondary": "#475569", "accent": "#94a3b8"},
        "general": {"primary": "#6b7280", "secondary": "#4b5563", "accent": "#9ca3af"},
    }

    def __init__(self) -> None:
        try:
            self.ollama_client = ollama.Client()
            self.ai_enabled = True
            logger.info("✅ AI-powered analytics enabled (Ollama)")
        except Exception as e:
            logger.warning(f"⚠️ AI not available, using fallback: {e}")
            self.ai_enabled = False

    # ============================================================
    # PUBLIC ENTRYPOINT
    # ============================================================

    def generate_complete_analytics(self, analytics_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate complete UI configuration for ANY document type.
        Hybrid:
        - Dataset → dedicated dataset UI path
        - Others → generic inventory-based path
        """
        doc_type = analytics_data.get("type", "unknown")
        logger.info(f"🎨 Generating UI config for: {doc_type}")

        if doc_type == "dataset":
            return self._generate_dataset_ui(analytics_data)

        # ---------- Generic document path ----------
        self._infer_tables_from_text(analytics_data)

        inventory = self._inventory_data(analytics_data)
        suite = self._get_document_suite(doc_type)

        hero_metrics = self._generate_all_metrics_direct(inventory, analytics_data)
        charts = self._generate_all_charts_direct(inventory, analytics_data, suite)
        tables = self._generate_tables_from_breakdowns(inventory, analytics_data)

        inferred_tables = self._generate_tables_from_inferred_text(inventory)
        tables.extend(inferred_tables)

        sections = self._auto_generate_sections(inventory, charts, tables)
        insights_config = self._auto_generate_insights_config(analytics_data)
        alerts_config = self._auto_generate_alerts_config(analytics_data)
        layout = self._infer_dynamic_layout(len(hero_metrics), len(charts), len(sections))

        ui_config = {
            "dashboard_type": "advanced",
            "document_type": doc_type,
            "suite": suite,
            "icon": self.DOCUMENT_ICONS.get(suite, "📄"),
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
            "insights_config": insights_config,
            "alerts_config": alerts_config,
            "layout": layout,
        }

        logger.info(
            f"   ✅ Generated: {len(hero_metrics)} metrics, "
            f"{len(charts)} charts, {len(tables)} tables, {len(sections)} sections"
        )
        return ui_config

    # ============================================================
    # DATASET-SPECIFIC UI (RICH, BANK-STYLE)
    # ============================================================

    def _generate_dataset_ui(self, data: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("📊 Dataset mode: generating dataset-aware UI")

        hero_metrics = [
            {
                "key": "total_rows",
                "label": "Total Rows",
                "value_path": "total_rows",
                "format": "number",
                "icon": "📄",
                "color": "green",
                "is_hero": True,
                "priority": 1,
            },
            {
                "key": "total_columns",
                "label": "Total Columns",
                "value_path": "total_columns",
                "format": "number",
                "icon": "📐",
                "color": "purple",
                "is_hero": True,
                "priority": 2,
            },
            {
                "key": "data_completeness_pct",
                "label": "Data Completeness",
                "value_path": "data_completeness_pct",
                "format": "percentage",
                "icon": "✔️",
                "color": "blue",
                "is_hero": True,
                "priority": 3,
            },
        ]

        charts: List[Dict[str, Any]] = []

        # ✨ NEW: Use auto-generated charts first!
        if data.get("auto_charts"):
            logger.info(f"📊 Using {len(data['auto_charts'])} auto-generated charts")
            for idx, chart in enumerate(data["auto_charts"], start=1):
                charts.append({
                    "id": chart.get("type", "chart") + f"_{idx}",
                    "type": chart.get("type", "bar"),
                    "title": chart.get("title", "Chart"),
                    "data": chart.get("data", []),
                    "config": chart.get("config", {}),
                    "priority": idx,
                })

        # Fallback: Old manual charts if no auto_charts
        else:
            logger.info("📊 No auto_charts found, using fallback manual charts")

            if data.get("quality_distribution"):
                charts.append(
                    {
                        "id": "quality_distribution",
                        "type": "histogram",
                        "title": "Quality Distribution",
                        "data_path": "quality_distribution",
                        "priority": 1,
                    }
                )

            if data.get("popularity_distribution"):
                charts.append(
                    {
                        "id": "popularity_distribution",
                        "type": "histogram",
                        "title": "Popularity Distribution",
                        "data_path": "popularity_distribution",
                        "priority": 2,
                    }
                )

            if data.get("segments", {}).get("available"):
                charts.append(
                    {
                        "id": "engagement_segments",
                        "type": "bar",
                        "title": "Engagement Segments",
                        "data_path": "segments.segments",
                        "priority": 3,
                    }
                )

        tables: List[Dict[str, Any]] = []

        if data.get("top_popular_entities"):
            tables.append(
                {
                    "id": "top_popular_entities",
                    "title": "Top Popular Entities",
                    "data": data["top_popular_entities"],
                    "priority": 1,
                    "show_export": True,
                    "show_search": True,
                }
            )

        if data.get("top_rated_entities"):
            tables.append(
                {
                    "id": "top_rated_entities",
                    "title": "Top Rated Entities",
                    "data": data["top_rated_entities"],
                    "priority": 2,
                    "show_export": True,
                    "show_search": True,
                }
            )

        if data.get("sample_entities"):
            tables.append(
                {
                    "id": "sample_entities",
                    "title": "Sample Records",
                    "data": data["sample_entities"],
                    "priority": 3,
                    "show_export": True,
                    "show_search": True,
                }
            )

        # 👇 Generic fallback table for any dataset with a quality_distribution
        if not tables and data.get("quality_distribution"):
            tables.append(
                {
                    "id": "quality_bins",
                    "title": "Quality Bins",
                    "data": data["quality_distribution"],
                    "priority": 3,
                    "show_export": True,
                    "show_search": False,
                }
            )

        sections = [
            {
                "id": "overview",
                "title": "Dataset Overview",
                "description": "High-level summary of dataset structure, size, and completeness.",
                "priority": 1,
            },
            {
                "id": "entities",
                "title": "Entities",
                "description": "Top entities ranked by popularity and quality.",
                "priority": 2,
            },
            {
                "id": "distributions",
                "title": "Distributions",
                "description": "Value distributions for key metrics such as quality and popularity.",
                "priority": 3,
            },
            {
                "id": "segments",
                "title": "Segments",
                "description": "Engagement-based segmentation of records using popularity thresholds.",
                "priority": 4,
            },
        ]

        insights = data.get("insight_narratives", [])
        insights_config = {
            "show": bool(insights),
            "title": "Key Insights",
            "icon": "💡",
            "data_path": "insight_narratives",
        }

        alerts = data.get("alerts", [])
        alerts_config = {
            "show": bool(alerts),
            "title": "Alerts & Warnings",
            "icon": "⚠️",
            "data_path": "alerts" if alerts else None,
        }

        layout = {
            "hero_columns": 3,
            "chart_columns": 2,
            "spacing": "comfortable",
            "show_sections": True,
            "show_secondary_metrics": True,
        }

        ui_config = {
            "dashboard_type": "advanced",
            "document_type": "dataset",
            "suite": "dataset",
            "icon": self.DOCUMENT_ICONS.get("dataset", "📊"),
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
            "insights_config": insights_config,
            "alerts_config": alerts_config,
            "layout": layout,
        }

        logger.info(
            f"   📊 Dataset UI generated: "
            f"{len(hero_metrics)} metrics, {len(charts)} charts, "
            f"{len(tables)} tables, {len(sections)} sections"
        )
        return ui_config

    # def _generate_dataset_ui(self, data: Dict[str, Any]) -> Dict[str, Any]:
    #     logger.info("📊 Dataset mode: generating dataset-aware UI")
    #
    #     hero_metrics = [
    #         {
    #             "key": "total_rows",
    #             "label": "Total Rows",
    #             "value_path": "total_rows",
    #             "format": "number",
    #             "icon": "📄",
    #             "color": "green",
    #             "is_hero": True,
    #             "priority": 1,
    #         },
    #         {
    #             "key": "total_columns",
    #             "label": "Total Columns",
    #             "value_path": "total_columns",
    #             "format": "number",
    #             "icon": "📐",
    #             "color": "purple",
    #             "is_hero": True,
    #             "priority": 2,
    #         },
    #         {
    #             "key": "data_completeness_pct",
    #             "label": "Data Completeness",
    #             "value_path": "data_completeness_pct",
    #             "format": "percentage",
    #             "icon": "✔️",
    #             "color": "blue",
    #             "is_hero": True,
    #             "priority": 3,
    #         },
    #     ]
    #
    #     charts: List[Dict[str, Any]] = []
    #
    #     if data.get("quality_distribution"):
    #         charts.append(
    #             {
    #                 "id": "quality_distribution",
    #                 "type": "histogram",
    #                 "title": "Quality Distribution",
    #                 "data_path": "quality_distribution",
    #                 "priority": 1,
    #             }
    #         )
    #
    #     if data.get("popularity_distribution"):
    #         charts.append(
    #             {
    #                 "id": "popularity_distribution",
    #                 "type": "histogram",
    #                 "title": "Popularity Distribution",
    #                 "data_path": "popularity_distribution",
    #                 "priority": 2,
    #             }
    #         )
    #
    #     if data.get("segments", {}).get("available"):
    #         charts.append(
    #             {
    #                 "id": "engagement_segments",
    #                 "type": "bar",
    #                 "title": "Engagement Segments",
    #                 "data_path": "segments.segments",
    #                 "priority": 3,
    #             }
    #         )
    #
    #     tables: List[Dict[str, Any]] = []
    #
    #     if data.get("top_popular_entities"):
    #         tables.append(
    #             {
    #                 "id": "top_popular_entities",
    #                 "title": "Top Popular Entities",
    #                 "data": data["top_popular_entities"],
    #                 "priority": 1,
    #                 "show_export": True,
    #                 "show_search": True,
    #             }
    #         )
    #
    #     if data.get("top_rated_entities"):
    #         tables.append(
    #             {
    #                 "id": "top_rated_entities",
    #                 "title": "Top Rated Entities",
    #                 "data": data["top_rated_entities"],
    #                 "priority": 2,
    #                 "show_export": True,
    #                 "show_search": True,
    #             }
    #         )
    #
    #     if data.get("sample_entities"):
    #         tables.append(
    #             {
    #                 "id": "sample_entities",
    #                 "title": "Sample Records",
    #                 "data": data["sample_entities"],
    #                 "priority": 3,
    #                 "show_export": True,
    #                 "show_search": True,
    #             }
    #         )
    #
    #     # 👇 NEW: generic fallback table for any dataset with a quality_distribution
    #     if not tables and data.get("quality_distribution"):
    #         tables.append(
    #             {
    #                 "id": "quality_bins",
    #                 "title": "Quality Bins",
    #                 "data": data["quality_distribution"],
    #                 "priority": 3,
    #                 "show_export": True,
    #                 "show_search": False,
    #             }
    #         )
    #
    #     sections = [
    #         {
    #             "id": "overview",
    #             "title": "Dataset Overview",
    #             "description": "High-level summary of dataset structure, size, and completeness.",
    #             "priority": 1,
    #         },
    #         {
    #             "id": "entities",
    #             "title": "Entities",
    #             "description": "Top entities ranked by popularity and quality.",
    #             "priority": 2,
    #         },
    #         {
    #             "id": "distributions",
    #             "title": "Distributions",
    #             "description": "Value distributions for key metrics such as quality and popularity.",
    #             "priority": 3,
    #         },
    #         {
    #             "id": "segments",
    #             "title": "Segments",
    #             "description": "Engagement-based segmentation of records using popularity thresholds.",
    #             "priority": 4,
    #         },
    #     ]
    #
    #     insights = data.get("insight_narratives", [])
    #     insights_config = {
    #         "show": bool(insights),
    #         "title": "Key Insights",
    #         "icon": "💡",
    #         "data_path": "insight_narratives",
    #     }
    #
    #     alerts = data.get("alerts", [])
    #     alerts_config = {
    #         "show": bool(alerts),
    #         "title": "Alerts & Warnings",
    #         "icon": "⚠️",
    #         "data_path": "alerts" if alerts else None,
    #     }
    #
    #     layout = {
    #         "hero_columns": 3,
    #         "chart_columns": 2,
    #         "spacing": "comfortable",
    #         "show_sections": True,
    #         "show_secondary_metrics": True,
    #     }
    #
    #     ui_config = {
    #         "dashboard_type": "advanced",
    #         "document_type": "dataset",
    #         "suite": "dataset",
    #         "icon": self.DOCUMENT_ICONS.get("dataset", "📊"),
    #         "hero_metrics": hero_metrics,
    #         "charts": charts,
    #         "tables": tables,
    #         "sections": sections,
    #         "insights_config": insights_config,
    #         "alerts_config": alerts_config,
    #         "layout": layout,
    #     }
    #
    #     logger.info(
    #         f"   📊 Dataset UI generated: "
    #         f"{len(hero_metrics)} metrics, {len(charts)} charts, "
    #         f"{len(tables)} tables, {len(sections)} sections"
    #     )
    #     return ui_config

    # ============================================================
    # GENERIC HELPERS
    # ============================================================

    def _get_document_suite(self, doc_type: str) -> str:
        return self.DOCUMENT_SUITES.get(doc_type, "general")

    def _inventory_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        inventory = {"metrics": [], "breakdowns": [], "time_series": [], "tables": []}
        self._scan_object(data, path="", inventory=inventory, depth=0)
        logger.info(
            "📦 Data Inventory Results:\n"
            f"   Metrics: {len(inventory['metrics'])}\n"
            f"   Breakdowns: {len(inventory['breakdowns'])}\n"
            f"   Time Series: {len(inventory['time_series'])}\n"
            f"   Tables: {len(inventory['tables'])}"
        )
        return inventory

    def _scan_object(
        self,
        obj: Any,
        path: str,
        inventory: Dict[str, Any],
        depth: int = 0,
    ) -> None:
        if depth > 6:
            return

        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    inventory["metrics"].append(
                        {"key": k, "path": new_path, "type": "number", "value": v}
                    )
                elif isinstance(v, list):
                    if v and isinstance(v[0], dict):
                        inventory["tables"].append(
                            {"key": k, "path": new_path, "headers": list(v[0].keys())}
                        )
                self._scan_object(v, new_path, inventory, depth + 1)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                new_path = f"{path}[{idx}]"
                self._scan_object(item, new_path, inventory, depth + 1)

    def _generate_all_metrics_direct(
        self, inventory: Dict[str, Any], analytics_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        metrics: List[Dict[str, Any]] = []
        priority = 1
        for m in inventory.get("metrics", []):
            key = m["key"]
            label = self._humanize_key(key)
            metrics.append(
                {
                    "key": key,
                    "label": label,
                    "value_path": m["path"],
                    "format": "number",
                    "icon": "📊",
                    "color": "blue",
                    "category": "other",
                    "is_hero": priority <= 3,
                    "priority": priority,
                }
            )
            priority += 1
        logger.info(
            f"   📊 Generated {len(metrics)} total metrics "
            f"({len([m for m in metrics if m['is_hero']])} hero)"
        )
        return metrics

    def _generate_all_charts_direct(
        self, inventory: Dict[str, Any], analytics_data: Dict[str, Any], suite: str
    ) -> List[Dict[str, Any]]:
        # Minimal generic charts; datasets use dedicated path
        charts: List[Dict[str, Any]] = []
        return charts

    def _generate_tables_from_breakdowns(
        self, inventory: Dict[str, Any], analytics_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        tables: List[Dict[str, Any]] = []
        for t in inventory.get("tables", []):
            headers = t.get("headers") or []
            tables.append(
                {
                    "id": f"table_{t['key']}",
                    "name": t["key"],
                    "title": self._humanize_key(t["key"]),
                    "data_path": t["path"],
                    "columns": [
                        {
                            "field": h,
                            "label": self._humanize_key(h),
                            "type": "string",
                            "align": "left",
                        }
                        for h in headers
                    ],
                    "page_size": 10,
                    "show_export": True,
                    "show_search": True,
                    "priority": 1,
                }
            )
        return tables

    def _generate_tables_from_inferred_text(
        self, inventory: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        tables: List[Dict[str, Any]] = []
        for idx, table in enumerate(inventory.get("inferred_tables", []), start=1):
            headers = table.get("headers") or []
            rows = table.get("rows") or []
            columns = [
                {
                    "field": h.lower().replace(" ", "_"),
                    "label": h,
                    "type": "string",
                    "align": "left",
                }
                for h in headers
            ]
            data = [
                dict(
                    zip(
                        [h.lower().replace(" ", "_") for h in headers],
                        row,
                    )
                )
                for row in rows
            ]
            tables.append(
                {
                    "id": f"inferred_table_{idx}",
                    "name": f"inferred_tables_{idx}",
                    "title": f"Inferred Tables {idx}",
                    "data": data,
                    "columns": columns,
                    "page_size": min(len(data), 10) if data else 5,
                    "show_export": True,
                    "show_search": True,
                    "priority": idx,
                }
            )
        logger.info(f"   📋 Generated {len(tables)} inferred tables")
        return tables

    def _auto_generate_sections(
        self,
        inventory: Dict[str, Any],
        charts: List[Dict[str, Any]],
        tables: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        if charts:
            sections.append(
                {
                    "id": "charts",
                    "title": "Visualizations",
                    "description": "Automatically generated charts.",
                    "priority": 1,
                }
            )
        if tables:
            sections.append(
                {
                    "id": "tables",
                    "title": "Tables",
                    "description": "Extracted and inferred tables.",
                    "priority": 2,
                }
            )
        return sections

    def _auto_generate_insights_config(self, analytics_data: Dict[str, Any]) -> Dict[str, Any]:
        insights = analytics_data.get("insights", [])
        return {
            "show": bool(insights),
            "title": "Key Insights",
            "icon": "💡",
            "data_path": "insights",
        }

    def _auto_generate_alerts_config(self, analytics_data: Dict[str, Any]) -> Dict[str, Any]:
        alerts = analytics_data.get("alerts", [])
        return {
            "show": bool(alerts),
            "title": "Alerts & Warnings",
            "icon": "⚠️",
            "data_path": "alerts" if alerts else None,
        }

    def _infer_dynamic_layout(
        self, hero_count: int, chart_count: int, section_count: int
    ) -> Dict[str, Any]:
        return {
            "hero_columns": min(hero_count, 4) or 3,
            "chart_columns": 2,
            "spacing": "comfortable",
            "show_sections": section_count > 0,
            "show_secondary_metrics": True,
        }

    def _humanize_key(self, key: str) -> str:
        key = key.replace("_", " ")
        return key[:1].upper() + key[1:]

    # ============================================================
    # AI TABLE INFERENCE (DISABLED FOR DATASETS)
    # ============================================================

    def _infer_tables_from_text(self, analytics_data: Dict[str, Any]) -> None:
        if analytics_data.get("type") == "dataset":
            analytics_data["inferred_tables"] = []
            return

        if not self.ai_enabled:
            return

        text = (
            analytics_data.get("raw_text")
            or analytics_data.get("full_text")
            or analytics_data.get("summary")
        )
        if not text or len(text.strip()) < 20:
            return

        prompt = """
You are an information extraction engine.

Extract ALL tabular structures from the following text.

Return ONLY valid JSON in this format:

[
  {
    "type": "inferred",
    "headers": ["Column1", "Column2", ...],
    "rows": [
      ["value1", "value2", ...],
      ...
    ]
  }
]
Text:
""" + text

        try:
            response = self.ollama_client.generate(
                model=config.model,
                prompt=prompt,
            )
            raw_output = response.get("response", "").strip()

            match = re.search(r"```json(.*?)```", raw_output, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
            else:
                json_str = raw_output

            inferred = json.loads(json_str)
            if isinstance(inferred, list):
                analytics_data["inferred_tables"] = inferred
        except Exception as e:
            logger.warning(f"⚠️ Failed to infer tables from text: {e}")
            analytics_data["inferred_tables"] = []


analytics_auto_generator = AnalyticsAutoGenerator()
