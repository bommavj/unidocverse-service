"""
Advanced Analytics V2 - Intelligent Dashboard Generator
WITH SEMANTIC COLUMN NAME UNDERSTANDING
Interprets column meanings like enterprise BI tools (Tableau, Power BI, Looker)
"""

import re
from collections import Counter
from typing import Dict, List, Any, Optional

import numpy as np
import pandas as pd


class ColumnIntelligence:
    """
    Understands what columns mean based on their names and values
    Similar to how Tableau/Power BI automatically recognizes dimensions and measures
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.column_semantics = self._analyze_all_columns()

    # ---------------------------------------------------------
    # SAFE NUNIQUE (Fix for "unhashable type: list/dict/set")
    # ---------------------------------------------------------
    def _safe_nunique(self, series: pd.Series) -> int:
        """
        Fully safe nunique that never triggers pandas mixed-type warnings.
        """
        try:
            return series.nunique(dropna=True)
        except Exception:
            try:
                return series.astype(str).nunique(dropna=True)
            except Exception:
                return len(set(str(x) for x in series.dropna()))

    # def _safe_nunique(self, series: pd.Series) -> int:
    #     try:
    #         return series.nunique()
    #     except TypeError:
    #         return series.astype(str).nunique()

    def _is_multi_value(self, series: pd.Series) -> bool:
        return series.apply(lambda x: isinstance(x, (list, dict, set))).any()

    # ---------------------------------------------------------
    # MAIN COLUMN ANALYZER
    # ---------------------------------------------------------
    def _analyze_all_columns(self) -> Dict[str, Dict[str, Any]]:
        semantics: Dict[str, Dict[str, Any]] = {}

        for col in self.df.columns:
            series = self.df[col]

            semantics[col] = {
                "original_name": col,
                "display_name": self._create_display_name(col),
                "semantic_type": self._detect_semantic_type(col),
                "data_type": str(series.dtype),
                "role": self._determine_role(col),
                "aggregation": self._suggest_aggregation(col),
                "format": self._suggest_format(col),
                "cardinality": self._safe_nunique(series),
                "has_nulls": series.isnull().any(),
                "sample_values": series.dropna().head(3).tolist(),
            }

        return semantics

    # ---------------------------------------------------------
    # DISPLAY NAME NORMALIZATION
    # ---------------------------------------------------------
    def _create_display_name(self, col: str) -> str:
        col = str(col)
        display = col.replace("_", " ").replace("-", " ")
        display = re.sub("([a-z])([A-Z])", r"\1 \2", display)

        abbreviations = {
            "qty": "Quantity",
            "amt": "Amount",
            "num": "Number",
            "avg": "Average",
            "min": "Minimum",
            "max": "Maximum",
            "std": "Standard Deviation",
            "pct": "Percentage",
            "yr": "Year",
            "mo": "Month",
            "dt": "Date",
            "id": "ID",
            "no": "Number",
            "desc": "Description",
            "cat": "Category",
            "subcat": "Subcategory",
            "prod": "Product",
            "cust": "Customer",
            "acct": "Account",
            "bal": "Balance",
            "txn": "Transaction",
            "rev": "Revenue",
            "dept": "Department",
            "emp": "Employee",
            "mgr": "Manager",
            "addr": "Address",
            "tel": "Telephone",
            "uom": "Unit of Measure",
        }

        words = display.lower().split()
        formatted = [abbreviations.get(w, w.title()) for w in words]
        return " ".join(formatted)

    # ---------------------------------------------------------
    # SEMANTIC TYPE DETECTION
    # ---------------------------------------------------------
    def _detect_semantic_type(self, col: str) -> str:
        col_lower = str(col).lower()
        series = self.df[col]

        if self._is_multi_value(series):
            return "multi_value"

        if any(
                k in col_lower
                for k in [
                    "date",
                    "time",
                    "year",
                    "month",
                    "day",
                    "timestamp",
                    "created",
                    "updated",
                    "modified",
                ]
        ):
            return "temporal"

        if any(
                k in col_lower
                for k in [
                    "country",
                    "state",
                    "city",
                    "zip",
                    "postal",
                    "region",
                    "address",
                    "location",
                    "lat",
                    "lon",
                ]
        ):
            return "geographic"

        if any(
                k in col_lower
                for k in [
                    "price",
                    "cost",
                    "amount",
                    "revenue",
                    "sales",
                    "payment",
                    "balance",
                    "salary",
                    "wage",
                    "fee",
                ]
        ):
            return "monetary"

        if any(
                k in col_lower
                for k in [
                    "quantity",
                    "count",
                    "number",
                    "qty",
                    "num",
                    "volume",
                    "units",
                    "items",
                ]
        ):
            return "quantity"

        if any(k in col_lower for k in ["rating", "score", "rank", "stars", "review"]):
            return "rating"

        if any(k in col_lower for k in ["percent", "percentage", "pct", "rate", "ratio"]):
            return "percentage"

        if any(
                k in col_lower
                for k in [
                    "category",
                    "type",
                    "class",
                    "group",
                    "segment",
                    "status",
                    "genre",
                    "department",
                ]
        ):
            return "category"

        if any(k in col_lower for k in ["id", "code", "key"]):
            if self._safe_nunique(series) / max(len(series), 1) > 0.9:
                return "identifier"

        if any(k in col_lower for k in ["name", "title", "label", "description"]):
            return "label"

        if any(k in col_lower for k in ["email", "phone", "contact", "tel", "mobile"]):
            return "contact"

        if any(k in col_lower for k in ["is_", "has_", "flag", "active", "enabled", "valid"]):
            return "boolean"

        return "generic"

    # ---------------------------------------------------------
    # ROLE DETECTION
    # ---------------------------------------------------------
    def _determine_role(self, col: str) -> str:
        dtype = self.df[col].dtype
        semantic = self._detect_semantic_type(col)

        if semantic in ["monetary", "quantity", "rating", "percentage"]:
            return "measure"

        if semantic in ["temporal", "geographic", "category", "label", "boolean", "multi_value"]:
            return "dimension"

        if semantic == "identifier":
            return "identifier"

        if pd.api.types.is_numeric_dtype(dtype) and dtype != bool:
            return "measure"

        return "dimension"

    # ---------------------------------------------------------
    # AGGREGATION SUGGESTION
    # ---------------------------------------------------------
    def _suggest_aggregation(self, col: str) -> str:
        semantic = self._detect_semantic_type(col)
        role = self._determine_role(col)

        if role != "measure":
            return "none"

        return {
            "monetary": "sum",
            "quantity": "sum",
            "rating": "average",
            "percentage": "average",
        }.get(semantic, "sum")

    # ---------------------------------------------------------
    # FORMAT SUGGESTION
    # ---------------------------------------------------------
    def _suggest_format(self, col: str) -> str:
        semantic = self._detect_semantic_type(col)

        return {
            "monetary": "currency",
            "percentage": "percentage",
            "rating": "decimal_1",
            "quantity": "number",
            "temporal": "date",
        }.get(semantic, "auto")

    # ---------------------------------------------------------
    # PUBLIC HELPERS
    # ---------------------------------------------------------
    def get_dimensions(self) -> List[str]:
        # Exclude multi_value dimensions from most visualizations
        return [
            c
            for c, info in self.column_semantics.items()
            if info["role"] == "dimension" and info["semantic_type"] != "multi_value"
        ]

    def get_measures(self) -> List[str]:
        return [c for c, info in self.column_semantics.items() if info["role"] == "measure"]

    def get_by_semantic_type(self, semantic_type: str) -> List[str]:
        return [c for c, info in self.column_semantics.items() if info["semantic_type"] == semantic_type]

    def get_best_dimension(self) -> Optional[str]:
        dims = self.get_dimensions()
        if not dims:
            return None

        scores: Dict[str, float] = {}
        for dim in dims:
            info = self.column_semantics[dim]
            score = 0.0

            if info["semantic_type"] == "category":
                score += 100

            card = info["cardinality"]
            if 3 <= card <= 50:
                score += 50 - abs(card - 10)

            if card > 100:
                score -= 50

            if not info["has_nulls"]:
                score += 10

            scores[dim] = score

        return max(scores, key=scores.get)

    def get_best_measure(self) -> Optional[str]:
        measures = self.get_measures()
        if not measures:
            return None

        priority = {
            "rating": 100,
            "monetary": 80,
            "quantity": 60,
            "percentage": 40,
        }

        scores = {m: priority.get(self.column_semantics[m]["semantic_type"], 20) for m in measures}
        return max(scores, key=scores.get)


class DashboardAnalyzer:
    """
    Intelligently generates dashboard visualizations based on column semantics
    """

    def _safe_mode(self, series: pd.Series) -> Optional[str]:
        series = series.dropna()

        if series.empty:
            return None

        try:
            values = series.astype(str).tolist()
            counter = Counter(values)
            most_common = counter.most_common(1)
            if most_common:
                return most_common[0][0]
        except Exception:
            pass

        return None

    # def _safe_mode(self, series: pd.Series) -> Optional[str]:
    #     """
    #     Fully safe mode extractor that never throws, even with mixed types,
    #     unhashable values, or empty results.
    #     """
    #     try:
    #         # Try normal mode first
    #         mode_val = series.mode(dropna=True)
    #         if len(mode_val) > 0:
    #             return str(mode_val.iloc[0])
    #     except Exception:
    #         pass
    #
    #     try:
    #         # Fallback: convert everything to string
    #         mode_val = series.astype(str).mode(dropna=True)
    #         if len(mode_val) > 0:
    #             return str(mode_val.iloc[0])
    #     except Exception:
    #         pass
    #
    #     return None

    def __init__(self, df: pd.DataFrame, metadata: Dict[str, Any] = None):
        self.df = df
        self.metadata = metadata or {}
        self.column_intelligence = ColumnIntelligence(df)
        self.document_type = self._detect_document_type()
        self.data_characteristics = self._analyze_data_characteristics()

    # ---------------------------------------------------------
    # PUBLIC ENTRY
    # ---------------------------------------------------------
    def analyze(self) -> Dict[str, Any]:
        """Generate complete dashboard analytics"""

        dashboard_meta = self._generate_dashboard_metadata()
        visualizations = self._intelligently_generate_visualizations()
        insights = self._generate_key_insights()
        statistical_summary = self._generate_statistical_summary()
        outliers = self._detect_outliers()
        segments = self._generate_segments()
        predictions = self._generate_predictions()

        return {
            "dashboard": dashboard_meta,
            "visualizations": visualizations,
            "insights": insights,
            "statistics": statistical_summary,
            "outliers": outliers,
            "segments": segments,
            "predictions": predictions,
            "data_quality": self._data_quality_summary(),
            "column_metadata": self.column_intelligence.column_semantics,
        }

    # ---------------------------------------------------------
    # DATA CHARACTERISTICS
    # ---------------------------------------------------------
    def _analyze_data_characteristics(self) -> Dict[str, Any]:
        """Analyze data structure to determine what visualizations make sense"""

        characteristics = {
            "has_time_series": len(self.column_intelligence.get_by_semantic_type("temporal")) > 0,
            "has_geography": len(self.column_intelligence.get_by_semantic_type("geographic")) > 0,
            "has_categories": len(self.column_intelligence.get_dimensions()) > 0,
            "has_measures": len(self.column_intelligence.get_measures()) > 0,
            "has_ratings": len(self.column_intelligence.get_by_semantic_type("rating")) > 0,
            "has_monetary": len(self.column_intelligence.get_by_semantic_type("monetary")) > 0,
            "dimensions": self.column_intelligence.get_dimensions(),
            "measures": self.column_intelligence.get_measures(),
            "recommended_viz_types": [],
        }

        if characteristics["has_time_series"] and characteristics["has_measures"]:
            characteristics["recommended_viz_types"].append("line_chart")

        if characteristics["has_categories"] and characteristics["has_measures"]:
            characteristics["recommended_viz_types"].append("bar_chart")

        if len(characteristics["measures"]) >= 2:
            characteristics["recommended_viz_types"].append("scatter_plot")

        if characteristics["has_categories"]:
            characteristics["recommended_viz_types"].append("pie_chart")

        if len(characteristics["dimensions"]) >= 2 and characteristics["has_measures"]:
            characteristics["recommended_viz_types"].append("grouped_bar")
            characteristics["recommended_viz_types"].append("heatmap")

        if characteristics["has_ratings"]:
            characteristics["recommended_viz_types"].extend(["box_plot", "gauge"])

        if characteristics["has_categories"]:
            characteristics["recommended_viz_types"].append("funnel")

        return characteristics

    # ---------------------------------------------------------
    # DOCUMENT TYPE DETECTION
    # ---------------------------------------------------------
    def _detect_document_type(self) -> str:
        """Auto-detect document type based on column semantics"""

        semantic_types = [
            info["semantic_type"] for info in self.column_intelligence.column_semantics.values()
        ]
        type_counts = Counter(semantic_types)

        col_names_lower = [str(col).lower() for col in self.df.columns]

        has_monetary = type_counts.get("monetary", 0) >= 1
        has_multiple_monetary = type_counts.get("monetary", 0) >= 2
        has_rating = type_counts.get("rating", 0) >= 1

        product_like = ("product", "item", "sku", "goods")
        customer_like = ("customer", "client", "account", "buyer")
        hr_like = ("employee", "salary", "department", "hr", "payroll")
        media_like = ("genre", "title", "movie", "film", "show")

        if has_multiple_monetary:
            return "financial"

        if has_rating and any(any(k in name for k in media_like) for name in col_names_lower):
            return "media"

        if has_monetary and (
                any(any(k in name for k in product_like) for name in col_names_lower)
                or any(any(k in name for k in customer_like) for name in col_names_lower)
        ):
            return "sales"

        if any(any(k in name for k in hr_like) for name in col_names_lower):
            return "hr"

        return "generic"

    # ---------------------------------------------------------
    # DASHBOARD METADATA
    # ---------------------------------------------------------
    def _generate_dashboard_metadata(self) -> Dict[str, Any]:
        """Generate contextual dashboard title and description"""

        best_dimension = self.column_intelligence.get_best_dimension()
        best_measure = self.column_intelligence.get_best_measure()

        dim_display = (
            self.column_intelligence.column_semantics[best_dimension]["display_name"]
            if best_dimension
            else "Categories"
        )
        measure_display = (
            self.column_intelligence.column_semantics[best_measure]["display_name"]
            if best_measure
            else "Metrics"
        )

        title = f"{dim_display} Analysis: {measure_display} Performance"
        description = (
            f"Comprehensive analysis of {dim_display} performance using "
            f"{measure_display} and other key metrics"
        )

        return {
            "title": title,
            "description": description,
            "record_count": len(self.df),
            "document_type": self.document_type,
            "primary_dimension": best_dimension,
            "primary_measure": best_measure,
        }

    # ---------------------------------------------------------
    # VIZ ORCHESTRATOR
    # ---------------------------------------------------------
    def _intelligently_generate_visualizations(self) -> List[Dict[str, Any]]:
        """Generate only relevant visualizations based on data"""

        visualizations: List[Dict[str, Any]] = []
        recommended = self.data_characteristics["recommended_viz_types"]

        viz_generators = {
            "bar_chart": self._create_dimension_by_metric_chart,
            "scatter_plot": self._create_correlation_scatter,
            "grouped_bar": self._create_secondary_dimension_chart,
            "line_chart": self._create_temporal_trend,
            "pie_chart": self._create_composition_chart,
            "heatmap": self._create_heatmap,
            "box_plot": self._create_box_plot,
            "funnel": self._create_funnel_chart,
            "gauge": self._create_gauge_chart,
        }

        for viz_type in recommended:
            if viz_type in viz_generators:
                try:
                    viz = viz_generators[viz_type]()
                    if viz:
                        visualizations.append(viz)
                except Exception as e:
                    print(f"Error generating {viz_type}: {e}")

        if not visualizations:
            visualizations.append(self._create_fallback_visualization())

        return visualizations

    # ---------------------------------------------------------
    # FALLBACK SUMMARY
    # ---------------------------------------------------------
    def _create_fallback_visualization(self) -> Dict[str, Any]:
        """Create a basic data summary when no specific viz applies"""

        summary_data: List[Dict[str, Any]] = []

        for col, info in self.column_intelligence.column_semantics.items():
            if info["role"] == "measure":
                col_series = pd.to_numeric(self.df[col], errors="coerce")
                non_null = col_series.dropna()
                summary_data.append(
                    {
                        "field": info["display_name"],
                        "original_field": col,
                        "type": info["semantic_type"],
                        "count": int(non_null.count()),
                        "mean": float(non_null.mean()) if len(non_null) > 0 else None,
                        "min": float(non_null.min()) if len(non_null) > 0 else None,
                        "max": float(non_null.max()) if len(non_null) > 0 else None,
                    }
                )
            else:
                series = self.df[col]
                try:
                    #mode_val = series.mode()
                    #top_value = str(mode_val.iloc[0]) if len(mode_val) > 0 else None
                    top_value = self._safe_mode(series)
                except TypeError:
                    #mode_val = series.astype(str).mode()
                    #top_value = str(mode_val.iloc[0]) if len(mode_val) > 0 else None
                    top_value = self._safe_mode(series)

                summary_data.append(
                    {
                        "field": info["display_name"],
                        "original_field": col,
                        "type": info["semantic_type"],
                        "unique": info["cardinality"],
                        "top_value": top_value,
                    }
                )

        return {
            "type": "data_summary",
            "title": "Data Overview",
            "subtitle": "Summary of all fields in your dataset",
            "data": summary_data,
            "config": {"show_details": True},
        }

    # ---------------------------------------------------------
    # BAR: DIMENSION BY METRIC
    # ---------------------------------------------------------
    def _create_dimension_by_metric_chart(self) -> Optional[Dict[str, Any]]:
        dimensions = self.data_characteristics["dimensions"]
        measures = self.data_characteristics["measures"]

        if not dimensions or not measures:
            return None

        primary_dim = self.column_intelligence.get_best_dimension()
        primary_measure = self.column_intelligence.get_best_measure()

        if not primary_dim or not primary_measure:
            return None

        agg_type = self.column_intelligence.column_semantics[primary_measure]["aggregation"]

        try:
            if agg_type == "average":
                agg_by_dim = (
                    self.df.groupby(primary_dim)[primary_measure]
                    .mean()
                    .sort_values(ascending=False)
                )
            else:
                agg_by_dim = (
                    self.df.groupby(primary_dim)[primary_measure]
                    .sum()
                    .sort_values(ascending=False)
                )
        except TypeError:
            # Fallback: coerce measure to numeric
            numeric_measure = pd.to_numeric(self.df[primary_measure], errors="coerce")
            if agg_type == "average":
                agg_by_dim = (
                    numeric_measure.groupby(self.df[primary_dim])
                    .mean()
                    .sort_values(ascending=False)
                )
            else:
                agg_by_dim = (
                    numeric_measure.groupby(self.df[primary_dim])
                    .sum()
                    .sort_values(ascending=False)
                )

        top_items = agg_by_dim.head(15)

        dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]
        measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]
        agg_display = "Average" if agg_type == "average" else "Total"

        return {
            "type": "horizontal_bar",
            "title": f"Top {dim_display} by {agg_display} {measure_display}",
            "subtitle": f"Showing top {len(top_items)} {dim_display.lower()} ranked by {measure_display.lower()}",
            "x_axis": f"{agg_display} {measure_display}",
            "y_axis": dim_display,
            "data": [
                {"label": str(label), "value": float(value)} for label, value in top_items.items()
            ],
            "config": {
                "orientation": "horizontal",
                "color": "#4A90E2",
                "show_values": True,
                "sort": "descending",
                "format": self.column_intelligence.column_semantics[primary_measure]["format"],
            },
        }

    # ---------------------------------------------------------
    # SCATTER: CORRELATION
    # ---------------------------------------------------------
    def _create_correlation_scatter(self) -> Optional[Dict[str, Any]]:
        measures = self.data_characteristics["measures"]

        if len(measures) < 2:
            return None

        measure1 = measures[0]
        measure2 = measures[1] if len(measures) > 1 else measures[0]

        if measure1 == measure2:
            return None

        scatter_data = self.df[[measure1, measure2]].copy()

        scatter_data[measure1] = pd.to_numeric(scatter_data[measure1], errors="coerce")
        scatter_data[measure2] = pd.to_numeric(scatter_data[measure2], errors="coerce")
        scatter_data = scatter_data.dropna()

        if len(scatter_data) < 3:
            return None

        correlation = scatter_data[measure1].corr(scatter_data[measure2])

        measure1_display = self.column_intelligence.column_semantics[measure1]["display_name"]
        measure2_display = self.column_intelligence.column_semantics[measure2]["display_name"]

        return {
            "type": "scatter",
            "title": f"{measure1_display} vs {measure2_display}",
            "subtitle": f"Correlation: {correlation:.2f}",
            "x_axis": measure1_display,
            "y_axis": measure2_display,
            "data": [
                        {"x": float(row[measure1]), "y": float(row[measure2])}
                        for _, row in scatter_data.iterrows()
                    ][:1000],
            "config": {
                "point_color": "#4A90E2",
                "point_size": 8,
                "show_trend_line": abs(correlation) > 0.3,
            },
            "insight": self._interpret_correlation(correlation, measure1_display, measure2_display),
        }

    # ---------------------------------------------------------
    # GROUPED BAR: SECONDARY DIMENSION
    # ---------------------------------------------------------
    def _create_secondary_dimension_chart(self) -> Optional[Dict[str, Any]]:
        dimensions = self.data_characteristics["dimensions"]

        if len(dimensions) < 2:
            return None

        primary_dim = dimensions[0]
        secondary_dim = dimensions[1]

        # Skip if any dimension is multi-value (dict/list/set)
        if self.column_intelligence._is_multi_value(self.df[secondary_dim]) or \
                self.column_intelligence._is_multi_value(self.df[primary_dim]):
            return None

        df = self.df[[secondary_dim, primary_dim]].dropna()

        # SAFE nunique for unhashable types
        def _safe_nunique(series: pd.Series) -> int:
            try:
                return series.nunique()
            except TypeError:
                return series.astype(str).nunique()

        unique_secondary = _safe_nunique(df[secondary_dim])
        unique_primary = _safe_nunique(df[primary_dim])

        if unique_secondary * unique_primary > 1_000_000:
            return None

        # Safe crosstab
        try:
            crosstab = pd.crosstab(df[secondary_dim], df[primary_dim])
        except TypeError:
            crosstab = pd.crosstab(df[secondary_dim].astype(str), df[primary_dim].astype(str))

        # Top 15 secondary categories (safe)
        try:
            top_secondary = df[secondary_dim].value_counts().head(15).index
        except TypeError:
            top_secondary = df[secondary_dim].astype(str).value_counts().head(15).index

        filtered_crosstab = crosstab.loc[top_secondary]

        data: List[Dict[str, Any]] = []
        for sec_val in filtered_crosstab.index:
            row_data: Dict[str, Any] = {"category": str(sec_val)}
            for prim_val in filtered_crosstab.columns:
                count = filtered_crosstab.loc[sec_val, prim_val]
                if count > 0:
                    row_data[str(prim_val)] = int(count)
            data.append(row_data)

        primary_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]
        secondary_display = self.column_intelligence.column_semantics[secondary_dim]["display_name"]

        if len(data) == 0:
            return None

        return {
            "type": "grouped_bar",
            "title": f"{primary_display} Distribution by {secondary_display}",
            "subtitle": f"Breakdown of {primary_display.lower()} across top {secondary_display.lower()}",
            "x_axis": secondary_display,
            "y_axis": f"{primary_display} Count",
            "data": data,
            "config": {
                "orientation": "vertical",
                "stack": False,
                "show_legend": True,
                "color_palette": "category10",
            },
        }

    # ---------------------------------------------------------
    # LINE: TEMPORAL TREND
    # ---------------------------------------------------------
    def _create_temporal_trend(self) -> Optional[Dict[str, Any]]:
        temporal_cols = self.column_intelligence.get_by_semantic_type("temporal")

        if not temporal_cols:
            return None

        time_col = temporal_cols[0]
        primary_measure = self.column_intelligence.get_best_measure()

        if not primary_measure:
            return None

        try:
            agg_type = self.column_intelligence.column_semantics[primary_measure]["aggregation"]

            df = self.df.copy()
            df[primary_measure] = pd.to_numeric(df[primary_measure], errors="coerce")
            df = df.dropna(subset=[primary_measure, time_col])

            if agg_type == "average":
                temporal_data = (
                    df.groupby(time_col)
                    .agg({primary_measure: ["mean", "count"]})
                    .reset_index()
                )
            else:
                temporal_data = (
                    df.groupby(time_col)
                    .agg({primary_measure: ["sum", "count"]})
                    .reset_index()
                )

            temporal_data.columns = ["time", "metric", "count"]
            temporal_data = temporal_data.sort_values("time")

            if len(temporal_data) < 2:
                return None

            time_display = self.column_intelligence.column_semantics[time_col]["display_name"]
            measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]
            agg_display = "Average" if agg_type == "average" else "Total"

            return {
                "type": "line",
                "title": f"{measure_display} Trend Over Time",
                "subtitle": f"{agg_display} {measure_display.lower()} and volume by {time_display.lower()}",
                "x_axis": time_display,
                "y_axis": f"{agg_display} {measure_display}",
                "data": [
                    {
                        "x": str(row["time"]),
                        "y": float(row["metric"]),
                        "count": int(row["count"]),
                    }
                    for _, row in temporal_data.iterrows()
                ],
                "config": {
                    "line_color": "#4A90E2",
                    "line_width": 3,
                    "show_points": True,
                    "show_area": False,
                    "format": self.column_intelligence.column_semantics[primary_measure]["format"],
                    "secondary_axis": {"metric": "count", "label": "Volume"},
                },
            }
        except Exception as e:
            print(f"Error creating temporal trend: {str(e)}")
            return None

    # ---------------------------------------------------------
    # PIE: COMPOSITION
    # ---------------------------------------------------------
    def _create_composition_chart(self) -> Optional[Dict[str, Any]]:
        primary_dim = self.column_intelligence.get_best_dimension()

        if not primary_dim:
            return None

        series = self.df[primary_dim]

        try:
            value_counts = series.value_counts()
        except TypeError:
            value_counts = series.astype(str).value_counts()

        if len(value_counts) < 3:
            return None

        if len(value_counts) > 10:
            top_10 = value_counts.head(10)
            others_count = value_counts[10:].sum()
            if others_count > 0:
                top_10["Others"] = others_count
            value_counts = top_10

        dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]

        total = value_counts.sum()

        return {
            "type": "pie",
            "title": f"{dim_display} Distribution",
            "subtitle": f"Breakdown of records by {dim_display.lower()}",
            "data": [
                {
                    "label": str(label),
                    "value": int(value),
                    "percentage": float(value / total * 100) if total > 0 else 0.0,
                }
                for label, value in value_counts.items()
            ],
            "config": {
                "show_percentage": True,
                "show_legend": True,
                "color_palette": "category20",
            },
        }

    # ---------------------------------------------------------
    # HEATMAP
    # ---------------------------------------------------------
    def _create_heatmap(self) -> Optional[Dict[str, Any]]:
        dimensions = self.data_characteristics["dimensions"]
        measures = self.data_characteristics["measures"]

        if len(dimensions) < 2 or not measures:
            return None

        primary_dim = dimensions[0]
        secondary_dim = dimensions[1]
        primary_measure = self.column_intelligence.get_best_measure()

        try:
            agg_type = self.column_intelligence.column_semantics[primary_measure]["aggregation"]
            agg_func = "mean" if agg_type == "average" else "sum"

            df = self.df[[secondary_dim, primary_dim, primary_measure]].copy()
            df[primary_measure] = pd.to_numeric(df[primary_measure], errors="coerce")
            df = df.dropna(subset=[secondary_dim, primary_dim, primary_measure])

            # Skip multi-value dimensions
            if self.column_intelligence._is_multi_value(df[secondary_dim]) or \
                    self.column_intelligence._is_multi_value(df[primary_dim]):
                return None

            def _safe_nunique(series: pd.Series) -> int:
                try:
                    return series.nunique()
                except TypeError:
                    return series.astype(str).nunique()

            unique_rows = _safe_nunique(df[secondary_dim])
            unique_cols = _safe_nunique(df[primary_dim])

            if unique_rows * unique_cols > 1_000_000:
                return None

            try:
                pivot = df.pivot_table(
                    values=primary_measure,
                    index=secondary_dim,
                    columns=primary_dim,
                    aggfunc=agg_func,
                )
            except TypeError:
                pivot = df.assign(
                    _sec=df[secondary_dim].astype(str),
                    _prim=df[primary_dim].astype(str),
                ).pivot_table(
                    values=primary_measure,
                    index="_sec",
                    columns="_prim",
                    aggfunc=agg_func,
                )

            if len(pivot.index) > 15:
                try:
                    top_rows = df[secondary_dim].value_counts().head(15).index
                except TypeError:
                    top_rows = df[secondary_dim].astype(str).value_counts().head(15).index
                pivot = pivot.loc[top_rows]

            if len(pivot.columns) > 15:
                pivot = pivot.iloc[:, :15]

            data: List[Dict[str, Any]] = []
            for row_name in pivot.index:
                for col_name in pivot.columns:
                    value = pivot.loc[row_name, col_name]
                    if pd.notna(value):
                        data.append(
                            {
                                "x": str(col_name),
                                "y": str(row_name),
                                "value": float(value),
                            }
                        )

            if len(data) < 3:
                return None

            primary_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]
            secondary_display = self.column_intelligence.column_semantics[secondary_dim]["display_name"]
            measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]
            agg_display = "Average" if agg_type == "average" else "Total"

            return {
                "type": "heatmap",
                "title": f"{measure_display} Heatmap: {primary_display} × {secondary_display}",
                "subtitle": f"Intensity shows {agg_display.lower()} {measure_display.lower()}",
                "x_axis": primary_display,
                "y_axis": secondary_display,
                "data": data,
                "config": {
                    "color_scale": "YlOrRd",
                    "show_values": True,
                    "format": self.column_intelligence.column_semantics[primary_measure]["format"],
                },
            }
        except Exception as e:
            print(f"Error creating heatmap: {str(e)}")
            return None

    # ---------------------------------------------------------
    # BOX PLOT
    # ---------------------------------------------------------
    def _create_box_plot(self) -> Optional[Dict[str, Any]]:
        rating_cols = self.column_intelligence.get_by_semantic_type("rating")

        if not rating_cols:
            return None

        rating_col = rating_cols[0]
        primary_dim = self.column_intelligence.get_best_dimension()

        if not primary_dim:
            return None

        try:
            df = self.df.copy()
            df[rating_col] = pd.to_numeric(df[rating_col], errors="coerce")

            try:
                top_categories = df[primary_dim].value_counts().head(10).index
            except TypeError:
                top_categories = df[primary_dim].astype(str).value_counts().head(10).index

            filtered_df = df[df[primary_dim].isin(top_categories)]

            data: List[Dict[str, Any]] = []
            for category in top_categories:
                cat_data = filtered_df[filtered_df[primary_dim] == category][rating_col].dropna()

                if len(cat_data) > 0:
                    data.append(
                        {
                            "category": str(category),
                            "min": float(cat_data.min()),
                            "q1": float(cat_data.quantile(0.25)),
                            "median": float(cat_data.median()),
                            "q3": float(cat_data.quantile(0.75)),
                            "max": float(cat_data.max()),
                            "mean": float(cat_data.mean()),
                            "count": int(len(cat_data)),
                        }
                    )

            if len(data) < 3:
                return None

            dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]
            rating_display = self.column_intelligence.column_semantics[rating_col]["display_name"]

            return {
                "type": "box_plot",
                "title": f"{rating_display} Distribution by {dim_display}",
                "subtitle": f"Shows spread and outliers of {rating_display.lower()} across {dim_display.lower()}",
                "x_axis": dim_display,
                "y_axis": rating_display,
                "data": data,
                "config": {"show_outliers": True, "color": "#4A90E2"},
            }
        except Exception as e:
            print(f"Error creating box plot: {str(e)}")
            return None

    # ---------------------------------------------------------
    # FUNNEL
    # ---------------------------------------------------------
    def _create_funnel_chart(self) -> Optional[Dict[str, Any]]:
        primary_dim = self.column_intelligence.get_best_dimension()

        if not primary_dim:
            return None

        try:
            series = self.df[primary_dim]
            try:
                value_counts = series.value_counts().head(7)
            except TypeError:
                value_counts = series.astype(str).value_counts().head(7)

            if len(value_counts) < 3:
                return None

            total = value_counts.sum()

            data: List[Dict[str, Any]] = []
            for idx, (label, count) in enumerate(value_counts.items()):
                data.append(
                    {
                        "stage": str(label),
                        "value": int(count),
                        "percentage": float(count / total * 100) if total > 0 else 0.0,
                        "conversion_rate": float(count / value_counts.iloc[0] * 100)
                        if idx > 0 and value_counts.iloc[0] > 0
                        else 100.0,
                    }
                )

            dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]

            return {
                "type": "funnel",
                "title": f"{dim_display} Volume Funnel",
                "subtitle": f"Distribution of {dim_display.lower()} from most to least common",
                "data": data,
                "config": {
                    "show_percentage": True,
                    "show_values": True,
                    "color_gradient": ["#4A90E2", "#7FB3D5", "#B4D7E8"],
                    "label_position": "inside",
                },
            }
        except Exception as e:
            print(f"Error creating funnel chart: {str(e)}")
            return None

    # ---------------------------------------------------------
    # GAUGE
    # ---------------------------------------------------------
    def _create_gauge_chart(self) -> Optional[Dict[str, Any]]:
        rating_cols = self.column_intelligence.get_by_semantic_type("rating")

        if not rating_cols:
            return None

        rating_col = rating_cols[0]

        try:
            series = pd.to_numeric(self.df[rating_col], errors="coerce").dropna()
            if len(series) == 0:
                return None

            avg_rating = series.mean()
            max_possible = series.max() if series.max() <= 10 else 10

            if max_possible == 0:
                return None

            percentage = (avg_rating / max_possible) * 100

            if percentage >= 80:
                performance = "Excellent"
                color = "#2ECC71"
            elif percentage >= 60:
                performance = "Good"
                color = "#4A90E2"
            elif percentage >= 40:
                performance = "Average"
                color = "#F39C12"
            else:
                performance = "Needs Improvement"
                color = "#E74C3C"

            rating_display = self.column_intelligence.column_semantics[rating_col]["display_name"]

            return {
                "type": "gauge",
                "title": f"Overall {rating_display} Performance",
                "subtitle": f"{performance} - {avg_rating:.2f} / {max_possible}",
                "data": {
                    "value": float(avg_rating),
                    "min": 0.0,
                    "max": float(max_possible),
                    "percentage": float(percentage),
                    "label": performance,
                },
                "config": {
                    "color": color,
                    "thresholds": [
                        {"value": 40, "color": "#E74C3C"},
                        {"value": 60, "color": "#F39C12"},
                        {"value": 80, "color": "#4A90E2"},
                        {"value": 100, "color": "#2ECC71"},
                    ],
                },
            }
        except Exception as e:
            print(f"Error creating gauge chart: {str(e)}")
            return None

    # ---------------------------------------------------------
    # STATISTICAL SUMMARY
    # ---------------------------------------------------------
    def _generate_statistical_summary(self) -> Dict[str, Any]:
        """Generate comprehensive statistical summary"""

        measures = self.data_characteristics["measures"]
        summaries: Dict[str, Any] = {}

        for col in measures:
            col_data = self.df[col].dropna()

            if len(col_data) == 0:
                continue

            if col_data.dtype == bool:
                continue

            try:
                col_data = pd.to_numeric(col_data, errors="coerce").dropna()
            except Exception:
                continue

            if len(col_data) == 0:
                continue

            try:
                display_name = self.column_intelligence.column_semantics[col]["display_name"]

                mean_val = col_data.mean()
                std_val = col_data.std()

                summaries[display_name] = {
                    "original_column": col,
                    "display_name": display_name,
                    "semantic_type": self.column_intelligence.column_semantics[col]["semantic_type"],
                    "mean": float(mean_val),
                    "median": float(col_data.median()),
                    "mode": float(col_data.mode()[0]) if len(col_data.mode()) > 0 else None,
                    "std_dev": float(std_val),
                    "variance": float(col_data.var()),
                    "min": float(col_data.min()),
                    "max": float(col_data.max()),
                    "range": float(col_data.max() - col_data.min()),
                    "q1": float(col_data.quantile(0.25)),
                    "q3": float(col_data.quantile(0.75)),
                    "iqr": float(col_data.quantile(0.75) - col_data.quantile(0.25)),
                    "skewness": float(col_data.skew()),
                    "kurtosis": float(col_data.kurtosis()),
                    "coefficient_of_variation": float((std_val / mean_val) * 100)
                    if mean_val != 0
                    else None,
                }
            except Exception as e:
                print(f"Skipping statistical summary for {col}: {str(e)}")
                continue

        return summaries

    # ---------------------------------------------------------
    # OUTLIERS
    # ---------------------------------------------------------
    def _detect_outliers(self) -> Dict[str, Any]:
        """Detect outliers using IQR method"""

        measures = self.data_characteristics["measures"]
        outlier_report: Dict[str, Any] = {}

        for col in measures:
            col_data = self.df[col].dropna()

            if len(col_data) < 10:
                continue

            if col_data.dtype == bool:
                continue

            try:
                col_data = pd.to_numeric(col_data, errors="coerce").dropna()
            except Exception:
                continue

            if len(col_data) < 10:
                continue

            try:
                q1 = col_data.quantile(0.25)
                q3 = col_data.quantile(0.75)
                iqr = q3 - q1

                lower_bound = q1 - 1.5 * iqr
                upper_bound = q3 + 1.5 * iqr

                outliers = col_data[(col_data < lower_bound) | (col_data > upper_bound)]

                if len(outliers) > 0:
                    display_name = self.column_intelligence.column_semantics[col]["display_name"]

                    outlier_report[display_name] = {
                        "original_column": col,
                        "display_name": display_name,
                        "count": int(len(outliers)),
                        "percentage": float(len(outliers) / len(col_data) * 100),
                        "lower_bound": float(lower_bound),
                        "upper_bound": float(upper_bound),
                        "outlier_values": [float(v) for v in outliers.head(10).values],
                        "severity": "high" if len(outliers) / len(col_data) > 0.1 else "moderate",
                    }
            except Exception as e:
                print(f"Skipping outlier detection for {col}: {str(e)}")
                continue

        return outlier_report

    # ---------------------------------------------------------
    # SEGMENTS
    # ---------------------------------------------------------
    def _generate_segments(self) -> List[Dict[str, Any]]:
        """Generate data segments/clusters"""

        primary_dim = self.column_intelligence.get_best_dimension()
        primary_measure = self.column_intelligence.get_best_measure()

        if not primary_dim or not primary_measure:
            return []

        try:
            df = self.df.copy()
            df[primary_measure] = pd.to_numeric(df[primary_measure], errors="coerce")
            df = df.dropna(subset=[primary_measure])

            segments: List[Dict[str, Any]] = []

            try:
                counts = df[primary_dim].value_counts()
            except TypeError:
                counts = df[primary_dim].astype(str).value_counts()

            overall_avg = df[primary_measure].mean()
            overall_median_count = counts.median()

            for category, count in counts.items():
                cat_data = df[df[primary_dim] == category][primary_measure].dropna()

                if len(cat_data) < 3:
                    continue

                avg_metric = cat_data.mean()

                if avg_metric > overall_avg and count > overall_median_count:
                    segment_type = "Champions"
                elif avg_metric > overall_avg:
                    segment_type = "Rising Stars"
                elif count > overall_median_count:
                    segment_type = "Volume Leaders"
                else:
                    segment_type = "Underperformers"

                dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]
                measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]

                segments.append(
                    {
                        "category": str(category),
                        "dimension_name": dim_display,
                        "metric_name": measure_display,
                        "segment_type": segment_type,
                        "avg_metric": float(avg_metric),
                        "count": int(count),
                        "metric_vs_avg": float(avg_metric - overall_avg),
                    }
                )

            segments.sort(key=lambda x: x["avg_metric"], reverse=True)

            return segments[:20]
        except Exception as e:
            print(f"Error generating segments: {str(e)}")
            return []

    # ---------------------------------------------------------
    # PREDICTIONS
    # ---------------------------------------------------------
    def _generate_predictions(self) -> Dict[str, Any]:
        """Generate predictive insights"""

        predictions: Dict[str, Any] = {}

        try:
            temporal_cols = self.column_intelligence.get_by_semantic_type("temporal")

            if not temporal_cols:
                return predictions

            time_col = temporal_cols[0]
            primary_measure = self.column_intelligence.get_best_measure()

            if not primary_measure:
                return predictions

            df = self.df.copy()
            df[primary_measure] = pd.to_numeric(df[primary_measure], errors="coerce")
            df = df.dropna(subset=[primary_measure, time_col])

            yearly_avg = df.groupby(time_col)[primary_measure].mean().sort_index()

            if len(yearly_avg) >= 3:
                years = yearly_avg.index.values
                values = yearly_avg.values

                try:
                    years_numeric = years.astype(float)
                except Exception:
                    # If time_col is not numeric (e.g., dates), use positional index
                    years_numeric = np.arange(len(values))

                slope, intercept = np.polyfit(years_numeric, values, 1)

                next_period_numeric = years_numeric[-1] + 1
                predicted_value = slope * next_period_numeric + intercept

                trend = (
                    "increasing"
                    if slope > 0.05
                    else "decreasing"
                    if slope < -0.05
                    else "stable"
                )

                time_display = self.column_intelligence.column_semantics[time_col]["display_name"]
                measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]

                predictions["trend_forecast"] = {
                    "metric": measure_display,
                    "time_dimension": time_display,
                    "next_period": str(next_period_numeric),
                    "predicted_value": float(predicted_value),
                    "trend": trend,
                    "slope": float(slope),
                    "confidence": "moderate",
                }
        except Exception as e:
            print(f"Error generating predictions: {str(e)}")

        return predictions

    # ---------------------------------------------------------
    # KEY INSIGHTS
    # ---------------------------------------------------------
    def _generate_key_insights(self) -> List[Dict[str, Any]]:
        """Generate key insights from the data"""

        insights: List[Dict[str, Any]] = []

        try:
            primary_dim = self.column_intelligence.get_best_dimension()
            primary_measure = self.column_intelligence.get_best_measure()

            if primary_dim and primary_measure:
                df = self.df.copy()
                df[primary_measure] = pd.to_numeric(df[primary_measure], errors="coerce")
                df = df.dropna(subset=[primary_measure])

                grouped = df.groupby(primary_dim)[primary_measure].mean()
                if len(grouped) > 0:
                    top_category = grouped.idxmax()
                    top_value = grouped.max()

                    dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]
                    measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]

                    insights.append(
                        {
                            "type": "top_performer",
                            "title": f"Highest {measure_display} {dim_display}",
                            "dimension": dim_display,
                            "metric": measure_display,
                            "value": str(top_category),
                            "metric_value": f"{top_value:.2f} avg {measure_display.lower()}",
                            "icon": "star",
                        }
                    )

            if primary_dim:
                series = self.df[primary_dim]
                try:
                    counts = series.value_counts()
                except TypeError:
                    counts = series.astype(str).value_counts()

                if len(counts) > 0:
                    most_common = counts.idxmax()
                    count = counts.max()

                    dim_display = self.column_intelligence.column_semantics[primary_dim]["display_name"]

                    insights.append(
                        {
                            "type": "volume_leader",
                            "title": f"Most Common {dim_display}",
                            "dimension": dim_display,
                            "value": str(most_common),
                            "metric_value": f"{count} items",
                            "icon": "trending_up",
                        }
                    )

            temporal_cols = self.column_intelligence.get_by_semantic_type("temporal")

            if temporal_cols:
                time_col = temporal_cols[0]
                min_time = self.df[time_col].min()
                max_time = self.df[time_col].max()

                time_display = self.column_intelligence.column_semantics[time_col]["display_name"]

                insights.append(
                    {
                        "type": "time_span",
                        "title": "Data Coverage",
                        "dimension": time_display,
                        "value": f"{min_time} - {max_time}",
                        "metric_value": f"{len(self.df[time_col].unique())} periods",
                        "icon": "calendar_today",
                    }
                )

            if primary_measure:
                df = self.df.copy()
                df[primary_measure] = pd.to_numeric(df[primary_measure], errors="coerce")
                non_null = df[primary_measure].dropna()

                if len(non_null) > 0:
                    avg_value = non_null.mean()
                    measure_display = self.column_intelligence.column_semantics[primary_measure]["display_name"]

                    insights.append(
                        {
                            "type": "overall_quality",
                            "title": f"Average {measure_display}",
                            "metric": measure_display,
                            "value": f"{avg_value:.2f}",
                            "metric_value": f"Based on {len(non_null)} items",
                            "icon": "analytics",
                        }
                    )

                    std_dev = non_null.std()
                    cv = (std_dev / avg_value) * 100 if avg_value != 0 else 0

                    consistency = "High" if cv < 15 else "Moderate" if cv < 30 else "Low"

                    insights.append(
                        {
                            "type": "consistency",
                            "title": f"{measure_display} Consistency",
                            "metric": measure_display,
                            "value": consistency,
                            "metric_value": f"CV: {cv:.1f}%",
                            "icon": "equalizer",
                        }
                    )
        except Exception as e:
            print(f"Error generating insights: {str(e)}")

        return insights

    # ---------------------------------------------------------
    # CORRELATION INTERPRETATION
    # ---------------------------------------------------------
    def _interpret_correlation(self, corr: float, x_col: str, y_col: str) -> str:
        """Generate insight text for correlation"""

        if abs(corr) < 0.3:
            return f"Weak correlation between {x_col} and {y_col}. These metrics appear independent."
        elif corr > 0.7:
            return f"Strong positive correlation. Higher {x_col} tends to associate with higher {y_col}."
        elif corr < -0.7:
            return f"Strong negative correlation. Higher {x_col} tends to associate with lower {y_col}."
        elif corr > 0.3:
            return f"Moderate positive correlation between {x_col} and {y_col}."
        else:
            return f"Moderate negative correlation between {x_col} and {y_col}."

    # ---------------------------------------------------------
    # DATA QUALITY
    # ---------------------------------------------------------
    def _data_quality_summary(self) -> Dict[str, Any]:
        """Summarize data quality metrics with safe duplicate detection"""

        try:
            duplicate_rows = int(self.df.duplicated().sum())
        except Exception:
            safe_df = self.df.copy()
            for col in safe_df.columns:
                if safe_df[col].apply(lambda x: isinstance(x, (list, dict, set))).any():
                    safe_df[col] = safe_df[col].astype(str)
            duplicate_rows = int(safe_df.duplicated().sum())

        total_cells = len(self.df) * len(self.df.columns)
        missing_cells = int(self.df.isnull().sum().sum())
        completeness_pct = float(100 * (1 - missing_cells / max(total_cells, 1)))

        return {
            "total_rows": len(self.df),
            "missing_cells": missing_cells,
            "duplicate_rows": duplicate_rows,
            "data_completeness_pct": round(completeness_pct, 2),
        }


import json


def deep_normalize_json(json_data: Any, parent_key: str = "", rows=None):
    """
    Recursively flattens ANY JSON into row-based records.
    Handles:
    - deeply nested dicts
    - lists of dicts
    - dicts of lists
    - mixed structures
    - irregular schemas
    """

    if rows is None:
        rows = []

    # If it's a list, iterate each item
    if isinstance(json_data, list):
        for item in json_data:
            deep_normalize_json(item, parent_key, rows)
        return rows

    # If it's a dict, flatten keys
    if isinstance(json_data, dict):
        flat = {}
        for key, value in json_data.items():
            new_key = f"{parent_key}.{key}" if parent_key else key

            if isinstance(value, (dict, list)):
                deep_normalize_json(value, new_key, rows)
            else:
                flat[new_key] = value

        if flat:
            rows.append(flat)

        return rows

    # Base case: primitive value
    rows.append({parent_key: json_data})
    return rows


def normalize_json_to_df(json_data: Any) -> pd.DataFrame:
    rows = deep_normalize_json(json_data)
    return pd.json_normalize(rows)


def fix_list_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detects if columns contain lists and transposes the DataFrame
    so that each list becomes a row instead of a column.
    """

    # Detect if EVERY column contains lists
    if all(df[col].apply(lambda x: isinstance(x, list)).all() for col in df.columns):
        # Determine number of rows
        max_len = max(len(df[col].iloc[0]) for col in df.columns)
        new_rows = []

        for i in range(max_len):
            row = {}
            for col in df.columns:
                try:
                    row[col] = df[col].iloc[0][i]
                except Exception:
                    row[col] = None
            new_rows.append(row)

        return pd.DataFrame(new_rows)

    return df




def generate_dashboard_analytics(df_or_json: Any, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    if isinstance(df_or_json, (dict, list)):
        df = normalize_json_to_df(df_or_json)

    elif isinstance(df_or_json, str):
        try:
            json_data = json.loads(df_or_json)
            df = normalize_json_to_df(json_data)
        except Exception:
            raise ValueError("String input is not valid JSON")

    elif isinstance(df_or_json, pd.DataFrame):
        df = df_or_json.copy()

    else:
        raise ValueError("Unsupported input type for analytics")

    df = fix_list_columns(df)
    analyzer = DashboardAnalyzer(df, metadata)
    return analyzer.analyze()




# def generate_dashboard_analytics(df: pd.DataFrame, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
#     """
#     Main entry point for dashboard-style analytics.
#     Returns intelligent, data-driven visualizations with semantic understanding.
#     """
#     analyzer = DashboardAnalyzer(df, metadata)
#     return analyzer.analyze()


# """
# Advanced Analytics V2 - Intelligent Dashboard Generator
# WITH SEMANTIC COLUMN NAME UNDERSTANDING
# Interprets column meanings like enterprise BI tools (Tableau, Power BI, Looker)
# """
#
# from typing import Dict, List, Any, Optional, Tuple
# import pandas as pd
# import numpy as np
# from collections import Counter, defaultdict
# import re
# from scipy import stats
#
#
# class ColumnIntelligence:
#     """
#     Understands what columns mean based on their names and values
#     Similar to how Tableau/Power BI automatically recognizes dimensions and measures
#     """
#
#     def __init__(self, df: pd.DataFrame):
#         self.df = df
#         self.column_semantics = self._analyze_all_columns()
#
#     def _analyze_all_columns(self) -> Dict[str, Dict[str, Any]]:
#         """Analyze each column to understand its semantic meaning"""
#
#         semantics = {}
#
#         for col in self.df.columns:
#             semantics[col] = {
#                 "original_name": col,
#                 "display_name": self._create_display_name(col),
#                 "semantic_type": self._detect_semantic_type(col),
#                 "data_type": str(self.df[col].dtype),
#                 "role": self._determine_role(col),
#                 "aggregation": self._suggest_aggregation(col),
#                 "format": self._suggest_format(col),
#                 "cardinality": self.df[col].nunique(),
#                 "has_nulls": self.df[col].isnull().any(),
#                 "sample_values": self.df[col].dropna().head(3).tolist()
#             }
#
#         return semantics
#
#     def _create_display_name(self, col: str) -> str:
#         """Convert column names to human-readable format"""
#
#         # ✅ FIX: Convert to string first (handles int columns)
#         col = str(col)
#
#         # Handle common patterns
#         display = col.replace('_', ' ').replace('-', ' ')
#
#         # Handle camelCase
#         display = re.sub('([a-z])([A-Z])', r'\1 \2', display)
#
#         # Handle abbreviations
#         abbreviations = {
#             'qty': 'Quantity',
#             'amt': 'Amount',
#             'num': 'Number',
#             'avg': 'Average',
#             'min': 'Minimum',
#             'max': 'Maximum',
#             'std': 'Standard Deviation',
#             'pct': 'Percentage',
#             'yr': 'Year',
#             'mo': 'Month',
#             'dt': 'Date',
#             'id': 'ID',
#             'no': 'Number',
#             'desc': 'Description',
#             'cat': 'Category',
#             'subcat': 'Subcategory',
#             'prod': 'Product',
#             'cust': 'Customer',
#             'acct': 'Account',
#             'bal': 'Balance',
#             'txn': 'Transaction',
#             'rev': 'Revenue',
#             'dept': 'Department',
#             'emp': 'Employee',
#             'mgr': 'Manager',
#             'addr': 'Address',
#             'tel': 'Telephone',
#             'qty': 'Quantity',
#             'uom': 'Unit of Measure'
#         }
#
#         words = display.lower().split()
#         formatted_words = []
#
#         for word in words:
#             if word in abbreviations:
#                 formatted_words.append(abbreviations[word])
#             else:
#                 formatted_words.append(word.title())
#
#         return ' '.join(formatted_words)
#
#     # def _create_display_name(self, col: str) -> str:
#     #     """Convert column names to human-readable format"""
#     #
#     #     # Handle common patterns
#     #     display = col.replace('_', ' ').replace('-', ' ')
#     #
#     #     # Handle camelCase
#     #     display = re.sub('([a-z])([A-Z])', r'\1 \2', display)
#     #
#     #     # Handle abbreviations
#     #     abbreviations = {
#     #         'qty': 'Quantity',
#     #         'amt': 'Amount',
#     #         'num': 'Number',
#     #         'avg': 'Average',
#     #         'min': 'Minimum',
#     #         'max': 'Maximum',
#     #         'std': 'Standard Deviation',
#     #         'pct': 'Percentage',
#     #         'yr': 'Year',
#     #         'mo': 'Month',
#     #         'dt': 'Date',
#     #         'id': 'ID',
#     #         'no': 'Number',
#     #         'desc': 'Description',
#     #         'cat': 'Category',
#     #         'subcat': 'Subcategory',
#     #         'prod': 'Product',
#     #         'cust': 'Customer',
#     #         'acct': 'Account',
#     #         'bal': 'Balance',
#     #         'txn': 'Transaction',
#     #         'rev': 'Revenue',
#     #         'dept': 'Department',
#     #         'emp': 'Employee',
#     #         'mgr': 'Manager',
#     #         'addr': 'Address',
#     #         'tel': 'Telephone',
#     #         'qty': 'Quantity',
#     #         'uom': 'Unit of Measure'
#     #     }
#     #
#     #     words = display.lower().split()
#     #     formatted_words = []
#     #
#     #     for word in words:
#     #         if word in abbreviations:
#     #             formatted_words.append(abbreviations[word])
#     #         else:
#     #             formatted_words.append(word.title())
#     #
#     #     return ' '.join(formatted_words)
#
#     def _detect_semantic_type(self, col: str) -> str:
#         """Detect what this column represents semantically"""
#
#         # ✅ FIX: Convert to string first
#         col_lower = str(col).lower()
#
#         # Temporal
#         if any(keyword in col_lower for keyword in
#                ['date', 'time', 'year', 'month', 'day', 'timestamp', 'created', 'updated', 'modified']):
#             return 'temporal'
#
#         # Geographic
#         if any(keyword in col_lower for keyword in
#                ['country', 'state', 'city', 'zip', 'postal', 'region', 'address', 'location', 'lat', 'lon', 'latitude',
#                 'longitude']):
#             return 'geographic'
#
#         # Monetary
#         if any(keyword in col_lower for keyword in
#                ['price', 'cost', 'amount', 'revenue', 'sales', 'payment', 'balance', 'salary', 'wage', 'fee', 'charge',
#                 'total', 'subtotal', 'tax']):
#             return 'monetary'
#
#         # Quantity/Count
#         if any(keyword in col_lower for keyword in
#                ['quantity', 'count', 'number', 'qty', 'num', 'volume', 'units', 'items']):
#             return 'quantity'
#
#         # Rating/Score
#         if any(keyword in col_lower for keyword in ['rating', 'score', 'rank', 'stars', 'review', 'feedback']):
#             return 'rating'
#
#         # Percentage
#         if any(keyword in col_lower for keyword in ['percent', 'percentage', 'pct', 'rate', 'ratio']):
#             return 'percentage'
#
#         # Category/Dimension
#         if any(keyword in col_lower for keyword in
#                ['category', 'type', 'class', 'group', 'segment', 'status', 'genre', 'department', 'division']):
#             return 'category'
#
#         # Identifier
#         if any(keyword in col_lower for keyword in ['id', 'code', 'key', 'number']) and self.df[col].nunique() / len(
#                 self.df) > 0.9:
#             return 'identifier'
#
#         # Name/Label
#         if any(keyword in col_lower for keyword in ['name', 'title', 'label', 'description']):
#             return 'label'
#
#         # Email/Contact
#         if any(keyword in col_lower for keyword in ['email', 'phone', 'contact', 'tel', 'mobile']):
#             return 'contact'
#
#         # Boolean/Flag
#         if any(keyword in col_lower for keyword in ['is_', 'has_', 'flag', 'active', 'enabled', 'valid']):
#             return 'boolean'
#
#         return 'generic'
#
#     def _determine_role(self, col: str) -> str:
#         """Determine if this is a dimension or measure"""
#
#         dtype = self.df[col].dtype
#         semantic_type = self._detect_semantic_type(col)
#
#         # Measures (numeric aggregatable)
#         if semantic_type in ['monetary', 'quantity', 'rating', 'percentage']:
#             return 'measure'
#
#         # Dimensions (categorical groupable)
#         if semantic_type in ['temporal', 'geographic', 'category', 'label', 'boolean']:
#             return 'dimension'
#
#         # Identifiers are neither
#         if semantic_type == 'identifier':
#             return 'identifier'
#
#         # Fallback to dtype
#         if pd.api.types.is_numeric_dtype(dtype) and dtype != bool:
#             return 'measure'
#         else:
#             return 'dimension'
#
#     def _suggest_aggregation(self, col: str) -> str:
#         """Suggest how this column should be aggregated"""
#
#         semantic_type = self._detect_semantic_type(col)
#         role = self._determine_role(col)
#
#         if role != 'measure':
#             return 'none'
#
#         aggregation_map = {
#             'monetary': 'sum',
#             'quantity': 'sum',
#             'rating': 'average',
#             'percentage': 'average'
#         }
#
#         return aggregation_map.get(semantic_type, 'sum')
#
#     def _suggest_format(self, col: str) -> str:
#         """Suggest display format for values"""
#
#         semantic_type = self._detect_semantic_type(col)
#
#         format_map = {
#             'monetary': 'currency',
#             'percentage': 'percentage',
#             'rating': 'decimal_1',
#             'quantity': 'number',
#             'temporal': 'date'
#         }
#
#         return format_map.get(semantic_type, 'auto')
#
#     def get_dimensions(self) -> List[str]:
#         """Get all dimension columns"""
#         return [col for col, info in self.column_semantics.items() if info['role'] == 'dimension']
#
#     def get_measures(self) -> List[str]:
#         """Get all measure columns"""
#         return [col for col, info in self.column_semantics.items() if info['role'] == 'measure']
#
#     def get_by_semantic_type(self, semantic_type: str) -> List[str]:
#         """Get columns by semantic type"""
#         return [col for col, info in self.column_semantics.items() if info['semantic_type'] == semantic_type]
#
#     def get_best_dimension(self) -> Optional[str]:
#         """Get the most appropriate primary dimension"""
#
#         dimensions = self.get_dimensions()
#
#         if not dimensions:
#             return None
#
#         # Scoring system
#         scores = {}
#
#         for dim in dimensions:
#             info = self.column_semantics[dim]
#             score = 0
#
#             # Prefer categories
#             if info['semantic_type'] == 'category':
#                 score += 100
#
#             # Prefer good cardinality (3-50)
#             cardinality = info['cardinality']
#             if 3 <= cardinality <= 50:
#                 score += 50 - abs(cardinality - 10)
#
#             # Penalize high cardinality
#             if cardinality > 100:
#                 score -= 50
#
#             # Prefer non-null
#             if not info['has_nulls']:
#                 score += 10
#
#             scores[dim] = score
#
#         return max(scores, key=scores.get)
#
#     def get_best_measure(self) -> Optional[str]:
#         """Get the most appropriate primary measure"""
#
#         measures = self.get_measures()
#
#         if not measures:
#             return None
#
#         # Priority: rating > monetary > quantity > generic
#         priority = {
#             'rating': 100,
#             'monetary': 80,
#             'quantity': 60,
#             'percentage': 40
#         }
#
#         scores = {}
#         for measure in measures:
#             info = self.column_semantics[measure]
#             scores[measure] = priority.get(info['semantic_type'], 20)
#
#         return max(scores, key=scores.get)
#
#
# class DashboardAnalyzer:
#     """
#     Intelligently generates dashboard visualizations based on column semantics
#     """
#
#     def __init__(self, df: pd.DataFrame, metadata: Dict[str, Any] = None):
#         self.df = df
#         self.metadata = metadata or {}
#         self.column_intelligence = ColumnIntelligence(df)
#         self.document_type = self._detect_document_type()
#         self.data_characteristics = self._analyze_data_characteristics()
#
#     def analyze(self) -> Dict[str, Any]:
#         """Generate complete dashboard analytics"""
#
#         dashboard_meta = self._generate_dashboard_metadata()
#         visualizations = self._intelligently_generate_visualizations()
#         insights = self._generate_key_insights()
#         statistical_summary = self._generate_statistical_summary()
#         outliers = self._detect_outliers()
#         segments = self._generate_segments()
#         predictions = self._generate_predictions()
#
#         return {
#             "dashboard": dashboard_meta,
#             "visualizations": visualizations,
#             "insights": insights,
#             "statistics": statistical_summary,
#             "outliers": outliers,
#             "segments": segments,
#             "predictions": predictions,
#             "data_quality": self._data_quality_summary(),
#             "column_metadata": self.column_intelligence.column_semantics
#         }
#
#     def _analyze_data_characteristics(self) -> Dict[str, Any]:
#         """Analyze data structure to determine what visualizations make sense"""
#
#         characteristics = {
#             "has_time_series": len(self.column_intelligence.get_by_semantic_type('temporal')) > 0,
#             "has_geography": len(self.column_intelligence.get_by_semantic_type('geographic')) > 0,
#             "has_categories": len(self.column_intelligence.get_dimensions()) > 0,
#             "has_measures": len(self.column_intelligence.get_measures()) > 0,
#             "has_ratings": len(self.column_intelligence.get_by_semantic_type('rating')) > 0,
#             "has_monetary": len(self.column_intelligence.get_by_semantic_type('monetary')) > 0,
#             "dimensions": self.column_intelligence.get_dimensions(),
#             "measures": self.column_intelligence.get_measures(),
#             "recommended_viz_types": []
#         }
#
#         # Determine recommended visualization types
#         if characteristics["has_time_series"] and characteristics["has_measures"]:
#             characteristics["recommended_viz_types"].append("line_chart")
#
#         if characteristics["has_categories"] and characteristics["has_measures"]:
#             characteristics["recommended_viz_types"].append("bar_chart")
#
#         if len(characteristics["measures"]) >= 2:
#             characteristics["recommended_viz_types"].append("scatter_plot")
#
#         if characteristics["has_categories"]:
#             characteristics["recommended_viz_types"].append("pie_chart")
#
#         if len(characteristics["dimensions"]) >= 2 and characteristics["has_measures"]:
#             characteristics["recommended_viz_types"].append("grouped_bar")
#             characteristics["recommended_viz_types"].append("heatmap")
#
#         if characteristics["has_ratings"]:
#             characteristics["recommended_viz_types"].extend(["box_plot", "gauge"])
#
#         if characteristics["has_categories"]:
#             characteristics["recommended_viz_types"].append("funnel")
#
#         return characteristics
#
#     def _detect_document_type(self) -> str:
#         """Auto-detect document type based on column semantics"""
#
#         semantic_types = [info['semantic_type'] for info in self.column_intelligence.column_semantics.values()]
#
#         type_counts = Counter(semantic_types)
#
#         # Financial/Banking detection
#         if type_counts.get('monetary', 0) >= 2:
#             return 'financial'
#
#         # Media/Rating detection
#         if type_counts.get('rating', 0) >= 1 and any(
#                 'genre' in col.lower() or 'title' in col.lower() for col in self.df.columns):
#             return 'media'
#
#         # Sales detection
#         if any('product' in col.lower() or 'customer' in col.lower() for col in self.df.columns) and type_counts.get(
#                 'monetary', 0) >= 1:
#             return 'sales'
#
#         # HR detection
#         if any('employee' in col.lower() or 'salary' in col.lower() or 'department' in col.lower() for col in
#                self.df.columns):
#             return 'hr'
#
#         return 'generic'
#
#     def _generate_dashboard_metadata(self) -> Dict[str, Any]:
#         """Generate contextual dashboard title and description"""
#
#         best_dimension = self.column_intelligence.get_best_dimension()
#         best_measure = self.column_intelligence.get_best_measure()
#
#         # Get display names
#         dim_display = self.column_intelligence.column_semantics[best_dimension][
#             'display_name'] if best_dimension else 'Categories'
#         measure_display = self.column_intelligence.column_semantics[best_measure][
#             'display_name'] if best_measure else 'Metrics'
#
#         title = f"{dim_display} Analysis: {measure_display} Performance"
#         description = f"Comprehensive analysis of {dim_display} performance using {measure_display} and other key metrics"
#
#         return {
#             "title": title,
#             "description": description,
#             "record_count": len(self.df),
#             "document_type": self.document_type,
#             "primary_dimension": best_dimension,
#             "primary_measure": best_measure
#         }
#
#     def _intelligently_generate_visualizations(self) -> List[Dict[str, Any]]:
#         """Generate only relevant visualizations based on data"""
#
#         visualizations = []
#         recommended = self.data_characteristics["recommended_viz_types"]
#
#         viz_generators = {
#             "bar_chart": self._create_dimension_by_metric_chart,
#             "scatter_plot": self._create_correlation_scatter,
#             "grouped_bar": self._create_secondary_dimension_chart,
#             "line_chart": self._create_temporal_trend,
#             "pie_chart": self._create_composition_chart,
#             "heatmap": self._create_heatmap,
#             "box_plot": self._create_box_plot,
#             "funnel": self._create_funnel_chart,
#             "gauge": self._create_gauge_chart
#         }
#
#         for viz_type in recommended:
#             if viz_type in viz_generators:
#                 viz = viz_generators[viz_type]()
#                 if viz:
#                     visualizations.append(viz)
#
#         if not visualizations:
#             visualizations.append(self._create_fallback_visualization())
#
#         return visualizations
#
#     def _create_fallback_visualization(self) -> Dict[str, Any]:
#         """Create a basic data summary when no specific viz applies"""
#
#         summary_data = []
#
#         for col, info in self.column_intelligence.column_semantics.items():
#             if info['role'] == 'measure':
#                 summary_data.append({
#                     "field": info['display_name'],
#                     "original_field": col,
#                     "type": info['semantic_type'],
#                     "count": int(self.df[col].notna().sum()),
#                     "mean": float(self.df[col].mean()) if len(self.df[col].dropna()) > 0 else None,
#                     "min": float(self.df[col].min()) if len(self.df[col].dropna()) > 0 else None,
#                     "max": float(self.df[col].max()) if len(self.df[col].dropna()) > 0 else None
#                 })
#             else:
#                 summary_data.append({
#                     "field": info['display_name'],
#                     "original_field": col,
#                     "type": info['semantic_type'],
#                     "unique": info['cardinality'],
#                     "top_value": str(self.df[col].mode()[0]) if len(self.df[col].mode()) > 0 else None
#                 })
#
#         return {
#             "type": "data_summary",
#             "title": "Data Overview",
#             "subtitle": "Summary of all fields in your dataset",
#             "data": summary_data,
#             "config": {
#                 "show_details": True
#             }
#         }
#
#     def _create_dimension_by_metric_chart(self) -> Optional[Dict[str, Any]]:
#         """Create top dimension by average metric chart"""
#
#         dimensions = self.data_characteristics["dimensions"]
#         measures = self.data_characteristics["measures"]
#
#         if not dimensions or not measures:
#             return None
#
#         # Get best dimension and measure
#         primary_dim = self.column_intelligence.get_best_dimension()
#         primary_measure = self.column_intelligence.get_best_measure()
#
#         if not primary_dim or not primary_measure:
#             return None
#
#         # Get aggregation type
#         agg_type = self.column_intelligence.column_semantics[primary_measure]['aggregation']
#
#         # Calculate aggregation
#         if agg_type == 'average':
#             agg_by_dim = self.df.groupby(primary_dim)[primary_measure].mean().sort_values(ascending=False)
#         else:  # sum
#             agg_by_dim = self.df.groupby(primary_dim)[primary_measure].sum().sort_values(ascending=False)
#
#         top_items = agg_by_dim.head(15)
#
#         # Get display names
#         dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#         measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#         agg_display = 'Average' if agg_type == 'average' else 'Total'
#
#         return {
#             "type": "horizontal_bar",
#             "title": f"Top {dim_display} by {agg_display} {measure_display}",
#             "subtitle": f"Showing top {len(top_items)} {dim_display.lower()} ranked by {measure_display.lower()}",
#             "x_axis": f"{agg_display} {measure_display}",
#             "y_axis": dim_display,
#             "data": [
#                 {"label": str(label), "value": float(value)}
#                 for label, value in top_items.items()
#             ],
#             "config": {
#                 "orientation": "horizontal",
#                 "color": "#4A90E2",
#                 "show_values": True,
#                 "sort": "descending",
#                 "format": self.column_intelligence.column_semantics[primary_measure]['format']
#             }
#         }
#
#     def _create_correlation_scatter(self) -> Optional[Dict[str, Any]]:
#         """Create scatter plot showing correlation between two metrics"""
#
#         measures = self.data_characteristics["measures"]
#
#         if len(measures) < 2:
#             return None
#
#         # Get two best measures
#         measure1 = measures[0]
#         measure2 = measures[1] if len(measures) > 1 else measures[0]
#
#         if measure1 == measure2:
#             return None
#
#         scatter_data = self.df[[measure1, measure2]].dropna()
#
#         if len(scatter_data) < 3:
#             return None
#
#         correlation = scatter_data[measure1].corr(scatter_data[measure2])
#
#         # Get display names
#         measure1_display = self.column_intelligence.column_semantics[measure1]['display_name']
#         measure2_display = self.column_intelligence.column_semantics[measure2]['display_name']
#
#         return {
#             "type": "scatter",
#             "title": f"{measure1_display} vs {measure2_display}",
#             "subtitle": f"Correlation: {correlation:.2f}",
#             "x_axis": measure1_display,
#             "y_axis": measure2_display,
#             "data": [
#                         {"x": float(row[measure1]), "y": float(row[measure2])}
#                         for _, row in scatter_data.iterrows()
#                     ][:1000],
#             "config": {
#                 "point_color": "#4A90E2",
#                 "point_size": 8,
#                 "show_trend_line": abs(correlation) > 0.3
#             },
#             "insight": self._interpret_correlation(correlation, measure1_display, measure2_display)
#         }
#
#     def _create_secondary_dimension_chart(self) -> Optional[Dict[str, Any]]:
#         """Create chart for secondary dimension"""
#
#         dimensions = self.data_characteristics["dimensions"]
#
#         if len(dimensions) < 2:
#             return None
#
#         primary_dim = dimensions[0]
#         secondary_dim = dimensions[1]
#
#         # --- SAFETY GUARD: prevent multi‑billion‑cell crosstab ---
#         df = self.df[[secondary_dim, primary_dim]].dropna()
#
#         unique_secondary = df[secondary_dim].nunique()
#         unique_primary = df[primary_dim].nunique()
#
#         # Hard stop if matrix would exceed 1M cells
#         if unique_secondary * unique_primary > 1_000_000:
#             return None
#
#         # Safe crosstab
#         crosstab = pd.crosstab(df[secondary_dim], df[primary_dim])
#
#         # Top 15 secondary categories
#         top_secondary = df[secondary_dim].value_counts().head(15).index
#         filtered_crosstab = crosstab.loc[top_secondary]
#
#         data = []
#         for sec_val in filtered_crosstab.index:
#             row_data = {"category": str(sec_val)}
#             for prim_val in filtered_crosstab.columns:
#                 count = filtered_crosstab.loc[sec_val, prim_val]
#                 if count > 0:
#                     row_data[str(prim_val)] = int(count)
#             data.append(row_data)
#
#         # Display names
#         primary_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#         secondary_display = self.column_intelligence.column_semantics[secondary_dim]['display_name']
#
#         return {
#             "type": "grouped_bar",
#             "title": f"{primary_display} Distribution by {secondary_display}",
#             "subtitle": f"Breakdown of {primary_display.lower()} across top {secondary_display.lower()}",
#             "x_axis": secondary_display,
#             "y_axis": f"{primary_display} Count",
#             "data": data,
#             "config": {
#                 "orientation": "vertical",
#                 "stack": False,
#                 "show_legend": True,
#                 "color_palette": "category10"
#             }
#         }
#
#     def _create_temporal_trend(self) -> Optional[Dict[str, Any]]:
#         """Create temporal trend chart if time data exists"""
#
#         temporal_cols = self.column_intelligence.get_by_semantic_type('temporal')
#
#         if not temporal_cols:
#             return None
#
#         time_col = temporal_cols[0]
#         primary_measure = self.column_intelligence.get_best_measure()
#
#         if not primary_measure:
#             return None
#
#         try:
#             agg_type = self.column_intelligence.column_semantics[primary_measure]['aggregation']
#
#             if agg_type == 'average':
#                 temporal_data = self.df.groupby(time_col).agg({
#                     primary_measure: ['mean', 'count']
#                 }).reset_index()
#             else:
#                 temporal_data = self.df.groupby(time_col).agg({
#                     primary_measure: ['sum', 'count']
#                 }).reset_index()
#
#             temporal_data.columns = ['time', 'metric', 'count']
#             temporal_data = temporal_data.sort_values('time')
#
#             if len(temporal_data) < 2:
#                 return None
#
#             # Get display names
#             time_display = self.column_intelligence.column_semantics[time_col]['display_name']
#             measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#             agg_display = 'Average' if agg_type == 'average' else 'Total'
#
#             return {
#                 "type": "line",
#                 "title": f"{measure_display} Trend Over Time",
#                 "subtitle": f"{agg_display} {measure_display.lower()} and volume by {time_display.lower()}",
#                 "x_axis": time_display,
#                 "y_axis": f"{agg_display} {measure_display}",
#                 "data": [
#                     {
#                         "x": str(row['time']),
#                         "y": float(row['metric']),
#                         "count": int(row['count'])
#                     }
#                     for _, row in temporal_data.iterrows()
#                 ],
#                 "config": {
#                     "line_color": "#4A90E2",
#                     "line_width": 3,
#                     "show_points": True,
#                     "show_area": False,
#                     "format": self.column_intelligence.column_semantics[primary_measure]['format'],
#                     "secondary_axis": {
#                         "metric": "count",
#                         "label": "Volume"
#                     }
#                 }
#             }
#         except Exception as e:
#             print(f"Error creating temporal trend: {str(e)}")
#             return None
#
#     def _create_composition_chart(self) -> Optional[Dict[str, Any]]:
#         """Create composition/distribution chart"""
#
#         primary_dim = self.column_intelligence.get_best_dimension()
#
#         if not primary_dim:
#             return None
#
#         value_counts = self.df[primary_dim].value_counts()
#
#         if len(value_counts) < 3:
#             return None
#
#         if len(value_counts) > 10:
#             top_10 = value_counts.head(10)
#             others_count = value_counts[10:].sum()
#
#             if others_count > 0:
#                 top_10['Others'] = others_count
#
#             value_counts = top_10
#
#         # Get display name
#         dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#
#         return {
#             "type": "pie",
#             "title": f"{dim_display} Distribution",
#             "subtitle": f"Breakdown of records by {dim_display.lower()}",
#             "data": [
#                 {"label": str(label), "value": int(value), "percentage": float(value / value_counts.sum() * 100)}
#                 for label, value in value_counts.items()
#             ],
#             "config": {
#                 "show_percentage": True,
#                 "show_legend": True,
#                 "color_palette": "category20"
#             }
#         }
#
#     def _create_heatmap(self) -> Optional[Dict[str, Any]]:
#         """Create heatmap for multi-dimensional analysis"""
#
#         dimensions = self.data_characteristics["dimensions"]
#         measures = self.data_characteristics["measures"]
#
#         if len(dimensions) < 2 or not measures:
#             return None
#
#         primary_dim = dimensions[0]
#         secondary_dim = dimensions[1]
#         primary_measure = self.column_intelligence.get_best_measure()
#
#         try:
#             agg_type = self.column_intelligence.column_semantics[primary_measure]['aggregation']
#             agg_func = 'mean' if agg_type == 'average' else 'sum'
#
#             # -------------------------------
#             # 🚨 SAFETY GUARD: prevent huge pivot
#             # -------------------------------
#             df = self.df[[secondary_dim, primary_dim, primary_measure]].dropna()
#
#             unique_rows = df[secondary_dim].nunique()
#             unique_cols = df[primary_dim].nunique()
#
#             # Hard stop if pivot would exceed 1M cells
#             if unique_rows * unique_cols > 1_000_000:
#                 return None
#             # -------------------------------
#
#             pivot = df.pivot_table(
#                 values=primary_measure,
#                 index=secondary_dim,
#                 columns=primary_dim,
#                 aggfunc=agg_func
#             )
#
#             # Limit rows
#             if len(pivot.index) > 15:
#                 top_rows = df[secondary_dim].value_counts().head(15).index
#                 pivot = pivot.loc[top_rows]
#
#             # Limit columns
#             if len(pivot.columns) > 15:
#                 pivot = pivot.iloc[:, :15]
#
#             data = []
#             for row_name in pivot.index:
#                 for col_name in pivot.columns:
#                     value = pivot.loc[row_name, col_name]
#                     if pd.notna(value):
#                         data.append({
#                             "x": str(col_name),
#                             "y": str(row_name),
#                             "value": float(value)
#                         })
#
#             if len(data) < 3:
#                 return None
#
#             # Display names
#             primary_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#             secondary_display = self.column_intelligence.column_semantics[secondary_dim]['display_name']
#             measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#             agg_display = 'Average' if agg_type == 'average' else 'Total'
#
#             return {
#                 "type": "heatmap",
#                 "title": f"{measure_display} Heatmap: {primary_display} × {secondary_display}",
#                 "subtitle": f"Intensity shows {agg_display.lower()} {measure_display.lower()}",
#                 "x_axis": primary_display,
#                 "y_axis": secondary_display,
#                 "data": data,
#                 "config": {
#                     "color_scale": "YlOrRd",
#                     "show_values": True,
#                     "format": self.column_intelligence.column_semantics[primary_measure]['format']
#                 }
#             }
#
#         except Exception as e:
#             print(f"Error creating heatmap: {str(e)}")
#             return None
#
#     def _create_box_plot(self) -> Optional[Dict[str, Any]]:
#         """Create box plot for distribution analysis"""
#
#         rating_cols = self.column_intelligence.get_by_semantic_type('rating')
#
#         if not rating_cols:
#             return None
#
#         rating_col = rating_cols[0]
#         primary_dim = self.column_intelligence.get_best_dimension()
#
#         if not primary_dim:
#             return None
#
#         try:
#             top_categories = self.df[primary_dim].value_counts().head(10).index
#             filtered_df = self.df[self.df[primary_dim].isin(top_categories)]
#
#             data = []
#             for category in top_categories:
#                 cat_data = filtered_df[filtered_df[primary_dim] == category][rating_col].dropna()
#
#                 if len(cat_data) > 0:
#                     data.append({
#                         "category": str(category),
#                         "min": float(cat_data.min()),
#                         "q1": float(cat_data.quantile(0.25)),
#                         "median": float(cat_data.median()),
#                         "q3": float(cat_data.quantile(0.75)),
#                         "max": float(cat_data.max()),
#                         "mean": float(cat_data.mean()),
#                         "count": int(len(cat_data))
#                     })
#
#             if len(data) < 3:
#                 return None
#
#             # Get display names
#             dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#             rating_display = self.column_intelligence.column_semantics[rating_col]['display_name']
#
#             return {
#                 "type": "box_plot",
#                 "title": f"{rating_display} Distribution by {dim_display}",
#                 "subtitle": f"Shows spread and outliers of {rating_display.lower()} across {dim_display.lower()}",
#                 "x_axis": dim_display,
#                 "y_axis": rating_display,
#                 "data": data,
#                 "config": {
#                     "show_outliers": True,
#                     "color": "#4A90E2"
#                 }
#             }
#         except Exception as e:
#             print(f"Error creating box plot: {str(e)}")
#             return None
#
#     def _create_funnel_chart(self) -> Optional[Dict[str, Any]]:
#         """Create funnel chart for hierarchical data"""
#
#         primary_dim = self.column_intelligence.get_best_dimension()
#
#         if not primary_dim:
#             return None
#
#         try:
#             value_counts = self.df[primary_dim].value_counts().head(7)
#
#             if len(value_counts) < 3:
#                 return None
#
#             total = value_counts.sum()
#
#             data = []
#             for idx, (label, count) in enumerate(value_counts.items()):
#                 data.append({
#                     "stage": str(label),
#                     "value": int(count),
#                     "percentage": float(count / total * 100),
#                     "conversion_rate": float(count / value_counts.iloc[0] * 100) if idx > 0 else 100.0
#                 })
#
#             # Get display name
#             dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#
#             return {
#                 "type": "funnel",
#                 "title": f"{dim_display} Volume Funnel",
#                 "subtitle": f"Distribution of {dim_display.lower()} from most to least common",
#                 "data": data,
#                 "config": {
#                     "show_percentage": True,
#                     "show_values": True,
#                     "color_gradient": ["#4A90E2", "#7FB3D5", "#B4D7E8"],
#                     "label_position": "inside"
#                 }
#             }
#         except Exception as e:
#             print(f"Error creating funnel chart: {str(e)}")
#             return None
#
#     def _create_gauge_chart(self) -> Optional[Dict[str, Any]]:
#         """Create gauge chart for performance metrics"""
#
#         rating_cols = self.column_intelligence.get_by_semantic_type('rating')
#
#         if not rating_cols:
#             return None
#
#         rating_col = rating_cols[0]
#
#         try:
#             avg_rating = self.df[rating_col].mean()
#             max_possible = self.df[rating_col].max() if self.df[rating_col].max() <= 10 else 10
#
#             percentage = (avg_rating / max_possible) * 100
#
#             if percentage >= 80:
#                 performance = "Excellent"
#                 color = "#2ECC71"
#             elif percentage >= 60:
#                 performance = "Good"
#                 color = "#4A90E2"
#             elif percentage >= 40:
#                 performance = "Average"
#                 color = "#F39C12"
#             else:
#                 performance = "Needs Improvement"
#                 color = "#E74C3C"
#
#             # Get display name
#             rating_display = self.column_intelligence.column_semantics[rating_col]['display_name']
#
#             return {
#                 "type": "gauge",
#                 "title": f"Overall {rating_display} Performance",
#                 "subtitle": f"{performance} - {avg_rating:.2f} / {max_possible}",
#                 "data": {
#                     "value": float(avg_rating),
#                     "min": 0,
#                     "max": float(max_possible),
#                     "percentage": float(percentage),
#                     "label": performance
#                 },
#                 "config": {
#                     "color": color,
#                     "thresholds": [
#                         {"value": 40, "color": "#E74C3C"},
#                         {"value": 60, "color": "#F39C12"},
#                         {"value": 80, "color": "#4A90E2"},
#                         {"value": 100, "color": "#2ECC71"}
#                     ]
#                 }
#             }
#         except Exception as e:
#             print(f"Error creating gauge chart: {str(e)}")
#             return None
#
#     def _generate_statistical_summary(self) -> Dict[str, Any]:
#         """Generate comprehensive statistical summary"""
#
#         measures = self.data_characteristics["measures"]
#
#         summaries = {}
#
#         for col in measures:
#             col_data = self.df[col].dropna()
#
#             if len(col_data) == 0:
#                 continue
#
#             if col_data.dtype == bool:
#                 continue
#
#             try:
#                 col_data = pd.to_numeric(col_data, errors='coerce').dropna()
#             except:
#                 continue
#
#             if len(col_data) == 0:
#                 continue
#
#             try:
#                 display_name = self.column_intelligence.column_semantics[col]['display_name']
#
#                 summaries[display_name] = {
#                     "original_column": col,
#                     "display_name": display_name,
#                     "semantic_type": self.column_intelligence.column_semantics[col]['semantic_type'],
#                     "mean": float(col_data.mean()),
#                     "median": float(col_data.median()),
#                     "mode": float(col_data.mode()[0]) if len(col_data.mode()) > 0 else None,
#                     "std_dev": float(col_data.std()),
#                     "variance": float(col_data.var()),
#                     "min": float(col_data.min()),
#                     "max": float(col_data.max()),
#                     "range": float(col_data.max() - col_data.min()),
#                     "q1": float(col_data.quantile(0.25)),
#                     "q3": float(col_data.quantile(0.75)),
#                     "iqr": float(col_data.quantile(0.75) - col_data.quantile(0.25)),
#                     "skewness": float(col_data.skew()),
#                     "kurtosis": float(col_data.kurtosis()),
#                     "coefficient_of_variation": float(
#                         (col_data.std() / col_data.mean()) * 100) if col_data.mean() != 0 else None
#                 }
#             except Exception as e:
#                 print(f"Skipping statistical summary for {col}: {str(e)}")
#                 continue
#
#         return summaries
#
#     def _detect_outliers(self) -> Dict[str, Any]:
#         """Detect outliers using IQR method"""
#
#         measures = self.data_characteristics["measures"]
#
#         outlier_report = {}
#
#         for col in measures:
#             col_data = self.df[col].dropna()
#
#             if len(col_data) < 10:
#                 continue
#
#             if col_data.dtype == bool:
#                 continue
#
#             try:
#                 col_data = pd.to_numeric(col_data, errors='coerce').dropna()
#             except:
#                 continue
#
#             if len(col_data) < 10:
#                 continue
#
#             try:
#                 q1 = col_data.quantile(0.25)
#                 q3 = col_data.quantile(0.75)
#                 iqr = q3 - q1
#
#                 lower_bound = q1 - 1.5 * iqr
#                 upper_bound = q3 + 1.5 * iqr
#
#                 outliers = col_data[(col_data < lower_bound) | (col_data > upper_bound)]
#
#                 if len(outliers) > 0:
#                     display_name = self.column_intelligence.column_semantics[col]['display_name']
#
#                     outlier_report[display_name] = {
#                         "original_column": col,
#                         "display_name": display_name,
#                         "count": len(outliers),
#                         "percentage": float(len(outliers) / len(col_data) * 100),
#                         "lower_bound": float(lower_bound),
#                         "upper_bound": float(upper_bound),
#                         "outlier_values": [float(v) for v in outliers.head(10).values],
#                         "severity": "high" if len(outliers) / len(col_data) > 0.1 else "moderate"
#                     }
#             except Exception as e:
#                 print(f"Skipping outlier detection for {col}: {str(e)}")
#                 continue
#
#         return outlier_report
#
#     def _generate_segments(self) -> List[Dict[str, Any]]:
#         """Generate data segments/clusters"""
#
#         primary_dim = self.column_intelligence.get_best_dimension()
#         primary_measure = self.column_intelligence.get_best_measure()
#
#         if not primary_dim or not primary_measure:
#             return []
#
#         try:
#             segments = []
#
#             for category in self.df[primary_dim].unique():
#                 cat_data = self.df[self.df[primary_dim] == category]
#
#                 if len(cat_data) < 3:
#                     continue
#
#                 avg_metric = cat_data[primary_measure].mean()
#                 count = len(cat_data)
#
#                 overall_avg = self.df[primary_measure].mean()
#                 overall_median_count = self.df[primary_dim].value_counts().median()
#
#                 if avg_metric > overall_avg and count > overall_median_count:
#                     segment_type = "Champions"
#                 elif avg_metric > overall_avg:
#                     segment_type = "Rising Stars"
#                 elif count > overall_median_count:
#                     segment_type = "Volume Leaders"
#                 else:
#                     segment_type = "Underperformers"
#
#                 dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#                 measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#
#                 segments.append({
#                     "category": str(category),
#                     "dimension_name": dim_display,
#                     "metric_name": measure_display,
#                     "segment_type": segment_type,
#                     "avg_metric": float(avg_metric),
#                     "count": int(count),
#                     "metric_vs_avg": float(avg_metric - overall_avg)
#                 })
#
#             segments.sort(key=lambda x: x['avg_metric'], reverse=True)
#
#             return segments[:20]
#         except Exception as e:
#             print(f"Error generating segments: {str(e)}")
#             return []
#
#     def _generate_predictions(self) -> Dict[str, Any]:
#         """Generate predictive insights"""
#
#         predictions = {}
#
#         try:
#             temporal_cols = self.column_intelligence.get_by_semantic_type('temporal')
#
#             if not temporal_cols:
#                 return predictions
#
#             time_col = temporal_cols[0]
#             primary_measure = self.column_intelligence.get_best_measure()
#
#             if not primary_measure:
#                 return predictions
#
#             yearly_avg = self.df.groupby(time_col)[primary_measure].mean().sort_index()
#
#             if len(yearly_avg) >= 3:
#                 years = yearly_avg.index.values
#                 values = yearly_avg.values
#
#                 slope, intercept = np.polyfit(years, values, 1)
#
#                 next_period = int(years[-1]) + 1
#                 predicted_value = slope * next_period + intercept
#
#                 trend = "increasing" if slope > 0.05 else "decreasing" if slope < -0.05 else "stable"
#
#                 time_display = self.column_intelligence.column_semantics[time_col]['display_name']
#                 measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#
#                 predictions["trend_forecast"] = {
#                     "metric": measure_display,
#                     "time_dimension": time_display,
#                     "next_period": next_period,
#                     "predicted_value": float(predicted_value),
#                     "trend": trend,
#                     "slope": float(slope),
#                     "confidence": "moderate"
#                 }
#         except Exception as e:
#             print(f"Error generating predictions: {str(e)}")
#
#         return predictions
#
#     def _generate_key_insights(self) -> List[Dict[str, Any]]:
#         """Generate key insights from the data"""
#
#         insights = []
#
#         try:
#             primary_dim = self.column_intelligence.get_best_dimension()
#             primary_measure = self.column_intelligence.get_best_measure()
#
#             if primary_dim and primary_measure:
#                 top_category = self.df.groupby(primary_dim)[primary_measure].mean().idxmax()
#                 top_value = self.df.groupby(primary_dim)[primary_measure].mean().max()
#
#                 dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#                 measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#
#                 insights.append({
#                     "type": "top_performer",
#                     "title": f"Highest {measure_display} {dim_display}",
#                     "dimension": dim_display,
#                     "metric": measure_display,
#                     "value": str(top_category),
#                     "metric_value": f"{top_value:.2f} avg {measure_display.lower()}",
#                     "icon": "star"
#                 })
#
#             if primary_dim:
#                 most_common = self.df[primary_dim].value_counts().idxmax()
#                 count = self.df[primary_dim].value_counts().max()
#
#                 dim_display = self.column_intelligence.column_semantics[primary_dim]['display_name']
#
#                 insights.append({
#                     "type": "volume_leader",
#                     "title": f"Most Common {dim_display}",
#                     "dimension": dim_display,
#                     "value": str(most_common),
#                     "metric_value": f"{count} items",
#                     "icon": "trending_up"
#                 })
#
#             temporal_cols = self.column_intelligence.get_by_semantic_type('temporal')
#
#             if temporal_cols:
#                 time_col = temporal_cols[0]
#                 min_time = self.df[time_col].min()
#                 max_time = self.df[time_col].max()
#
#                 time_display = self.column_intelligence.column_semantics[time_col]['display_name']
#
#                 insights.append({
#                     "type": "time_span",
#                     "title": "Data Coverage",
#                     "dimension": time_display,
#                     "value": f"{min_time} - {max_time}",
#                     "metric_value": f"{len(self.df[time_col].unique())} periods",
#                     "icon": "calendar_today"
#                 })
#
#             if primary_measure:
#                 avg_value = self.df[primary_measure].mean()
#                 measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#
#                 insights.append({
#                     "type": "overall_quality",
#                     "title": f"Average {measure_display}",
#                     "metric": measure_display,
#                     "value": f"{avg_value:.2f}",
#                     "metric_value": f"Based on {len(self.df)} items",
#                     "icon": "analytics"
#                 })
#
#             if primary_measure:
#                 std_dev = self.df[primary_measure].std()
#                 cv = (std_dev / self.df[primary_measure].mean()) * 100 if self.df[primary_measure].mean() != 0 else 0
#
#                 consistency = "High" if cv < 15 else "Moderate" if cv < 30 else "Low"
#                 measure_display = self.column_intelligence.column_semantics[primary_measure]['display_name']
#
#                 insights.append({
#                     "type": "consistency",
#                     "title": f"{measure_display} Consistency",
#                     "metric": measure_display,
#                     "value": consistency,
#                     "metric_value": f"CV: {cv:.1f}%",
#                     "icon": "equalizer"
#                 })
#         except Exception as e:
#             print(f"Error generating insights: {str(e)}")
#
#         return insights
#
#     def _interpret_correlation(self, corr: float, x_col: str, y_col: str) -> str:
#         """Generate insight text for correlation"""
#
#         if abs(corr) < 0.3:
#             return f"Weak correlation between {x_col} and {y_col}. These metrics appear independent."
#         elif corr > 0.7:
#             return f"Strong positive correlation. Higher {x_col} tends to associate with higher {y_col}."
#         elif corr < -0.7:
#             return f"Strong negative correlation. Higher {x_col} tends to associate with lower {y_col}."
#         elif corr > 0.3:
#             return f"Moderate positive correlation between {x_col} and {y_col}."
#         else:
#             return f"Moderate negative correlation between {x_col} and {y_col}."
#
#     def _data_quality_summary(self) -> Dict[str, Any]:
#         """Quick data quality summary"""
#
#         completeness = (self.df.notna().sum().sum() / (len(self.df) * len(self.df.columns))) * 100
#
#         return {
#             "completeness": float(completeness),
#             "record_count": len(self.df),
#             "field_count": len(self.df.columns),
#             "missing_cells": int(self.df.isna().sum().sum()),
#             "duplicate_rows": int(self.df.duplicated().sum())
#         }
#
#
# def generate_dashboard_analytics(df: pd.DataFrame, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
#     """
#     Main entry point for dashboard-style analytics.
#     Returns intelligent, data-driven visualizations with semantic understanding.
#     """
#     analyzer = DashboardAnalyzer(df, metadata)
#     return analyzer.analyze()
