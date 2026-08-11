import json
import logging
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.core import config
from app.services.mixins.tabular_loader_mixin import TabularLoaderMixin
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class DatasetAnalyzer(BaseAnalyzer, TabularLoaderMixin):
    """
    Universal dataset analyzer.

    - Loads ANY tabular data (CSV, XLSX, PDF tables, parsed tables)
    - Cleans numerics
    - Infers semantic roles
    - Computes structural metrics
    - Generates LLM insights & recommendations
    - Auto-generates charts for ALL numeric columns
    - Delegates UI generation to BaseAnalyzer.add_ui_config()
    """

    def __init__(self, ollama_client: Optional[Any] = None) -> None:
        super().__init__()
        self.ollama_client = ollama_client
        logger.info("✅ DatasetAnalyzer initialized")

    def _normalize_matrix_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        For PDF-like matrices where columns are [0,1,2,...] and the real
        headers live in the first few rows, promote a header row and clean names.
        """
        if df.empty:
            return df

        # Only trigger when columns are 0..N (RangeIndex-like)
        if list(df.columns) != list(range(len(df.columns))):
            return df

        # Find a candidate header row: first row with at least 2 non-null cells
        header_idx: Optional[int] = None
        max_scan = min(10, len(df))
        for i in range(max_scan):
            non_null = df.iloc[i].notna().sum()
            if non_null >= 2:
                header_idx = i
                break

        if header_idx is None:
            return df

        header = df.iloc[header_idx].astype(str).str.strip()

        # Build cleaned column names
        new_cols: List[str] = []
        for i, h in enumerate(header):
            if not h or h.lower() in ("none", "nan"):
                new_cols.append(f"col_{i + 1}")
            else:
                # normalize: lower, underscores, no spaces
                clean = re.sub(r"\s+", "_", h.strip())
                clean = re.sub(r"[^\w_]", "", clean)
                if not clean:
                    clean = f"col_{i + 1}"
                new_cols.append(clean)

        df = df.iloc[header_idx + 1:].reset_index(drop=True)
        df.columns = new_cols

        logger.info(f"📌 NORMALIZED HEADERS: {new_cols}")
        return df

    def _flatten_nested_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Flatten columns containing dicts/lists intelligently:
        - Nested dicts with numeric values → expand into separate columns
        - Other nested structures → convert to JSON strings
        """
        import json

        columns_to_process = list(df.columns)
        columns_to_drop = []
        new_columns_dict = {}  # Store all new columns here

        for col in columns_to_process:
            # Check if any cell in the column contains a dict or list
            sample_values = df[col].dropna().head(10)

            if len(sample_values) == 0:
                continue

            first_value = sample_values.iloc[0]

            # CASE 1: Column contains dicts with consistent structure
            if isinstance(first_value, dict):
                # Check if all non-null values are dicts
                all_dicts = df[col].dropna().apply(lambda x: isinstance(x, dict)).all()

                if all_dicts:
                    # Try to expand into separate columns
                    try:
                        # Get all unique keys across all dicts
                        all_keys = set()
                        for val in df[col].dropna():
                            if isinstance(val, dict):
                                all_keys.update(val.keys())

                        # If we have keys, expand them
                        if all_keys:
                            logger.info(f"📦 Expanding nested dict column: {col} → {len(all_keys)} sub-columns")

                            # Create new columns for each key (store in dict, don't add to df yet)
                            for key in all_keys:
                                new_col_name = f"{col}_{key}"
                                new_col_data = df[col].apply(
                                    lambda x: x.get(key) if isinstance(x, dict) else None
                                )

                                # Try to convert to numeric if possible
                                if new_col_data.dtype == 'object':
                                    # Extract numeric values from strings like "460 kcal", "50 g"
                                    numeric_extracted = new_col_data.astype(str).str.extract(r'([\d.]+)', expand=False)
                                    new_col_data = pd.to_numeric(numeric_extracted, errors='coerce')

                                new_columns_dict[new_col_name] = new_col_data

                            # Mark original column for removal
                            columns_to_drop.append(col)
                            logger.info(f"✅ Marked nested column for removal: {col}")
                            continue

                    except Exception as e:
                        logger.warning(f"⚠️ Could not expand dict column {col}: {e}")
                        # Fall through to JSON string conversion

                # If expansion failed, convert to JSON string
                logger.info(f"📦 Flattening dict column to JSON: {col}")
                df[col] = df[col].apply(
                    lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x
                )

            # CASE 2: Column contains lists
            elif isinstance(first_value, list):
                logger.info(f"📦 Flattening list column to JSON: {col}")
                df[col] = df[col].apply(
                    lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x
                )

        # ✅ PERFORMANCE FIX: Add all new columns at once using pd.concat
        if new_columns_dict:
            logger.info(f"🚀 Adding {len(new_columns_dict)} expanded columns via pd.concat")
            new_df = pd.DataFrame(new_columns_dict, index=df.index)
            df = pd.concat([df, new_df], axis=1)

        # Drop original nested columns
        if columns_to_drop:
            df = df.drop(columns=columns_to_drop)
            logger.info(f"🗑️ Dropped {len(columns_to_drop)} original nested columns")

        return df

    # ============================================================
    # GENERALIZED CHART GENERATION
    # ============================================================

    def _calculate_optimal_bins(self, series: pd.Series, unique_count: int, total_count: int) -> int:
        """
        Calculate optimal number of bins for histogram.
        Uses multiple heuristics to determine best bin count.
        """
        # Sturges' rule
        sturges = int(np.ceil(np.log2(total_count) + 1))

        # Square root rule
        sqrt_rule = int(np.ceil(np.sqrt(total_count)))

        # Rice rule
        rice = int(np.ceil(2 * (total_count ** (1 / 3))))

        # Freedman-Diaconis rule
        q75, q25 = np.percentile(series, [75, 25])
        iqr = q75 - q25
        if iqr > 0:
            bin_width = 2 * iqr / (total_count ** (1 / 3))
            fd = int(np.ceil((series.max() - series.min()) / bin_width))
        else:
            fd = sturges

        # Use median of all methods, bounded between 10-50
        optimal = int(np.median([sturges, sqrt_rule, rice, fd]))
        return max(10, min(50, optimal))

    def _humanize_column_name(self, col: str) -> str:
        """
        Convert column names to human-readable titles.
        Examples:
          - nutrients_calories → Calories
          - total_time → Total Time
          - avg_sale_price → Average Sale Price
        """
        # Remove common prefixes
        col = re.sub(r'^(nutrients|metrics|data|value|field)_', '', col, flags=re.IGNORECASE)

        # Split on underscores and capitalize
        words = col.replace('_', ' ').split()

        # Capitalize each word
        humanized = ' '.join(word.capitalize() for word in words)

        # Handle special cases
        replacements = {
            'Id': 'ID',
            'Url': 'URL',
            'Api': 'API',
            'Sql': 'SQL',
            'Html': 'HTML',
            'Iot': 'IoT',
            'Ai': 'AI',
            'Ml': 'ML',
            'Kcal': 'kcal',
            'Kg': 'kg',
            'Mg': 'mg',
        }

        for old, new in replacements.items():
            humanized = re.sub(r'\b' + old + r'\b', new, humanized)

        return humanized

    def _generate_correlation_charts(
            self,
            df: pd.DataFrame,
            numeric_columns: List[str],
            max_correlations: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Generate scatter plots for interesting correlations.
        Only creates charts for correlations that are strong or potentially meaningful.
        """
        charts = []

        if len(numeric_columns) < 2:
            return charts

        # Calculate correlation matrix
        try:
            corr_matrix = df[numeric_columns].corr()
        except Exception:
            return charts

        # Find top correlations (excluding diagonal and duplicates)
        correlations = []
        for i, col1 in enumerate(numeric_columns):
            for j, col2 in enumerate(numeric_columns):
                if i < j:  # Upper triangle only
                    corr_val = corr_matrix.loc[col1, col2]
                    if pd.notna(corr_val) and abs(corr_val) > 0.3:  # Moderate correlation
                        correlations.append({
                            'col1': col1,
                            'col2': col2,
                            'correlation': abs(corr_val),
                            'direction': 'positive' if corr_val > 0 else 'negative'
                        })

        # Sort by correlation strength
        correlations.sort(key=lambda x: x['correlation'], reverse=True)

        # Create scatter plots for top correlations
        for corr in correlations[:max_correlations]:
            col1, col2 = corr['col1'], corr['col2']

            # Sample data points if too many (for performance)
            sample_df = df[[col1, col2]].dropna()
            if len(sample_df) > 1000:
                sample_df = sample_df.sample(1000, random_state=42)

            charts.append({
                "type": "scatter",
                "title": f"{self._humanize_column_name(col1)} vs {self._humanize_column_name(col2)}",
                "data": [
                    {"x": float(row[col1]), "y": float(row[col2])}
                    for _, row in sample_df.iterrows()
                ],
                "config": {
                    "xLabel": self._humanize_column_name(col1),
                    "yLabel": self._humanize_column_name(col2),
                    "correlation": round(corr['correlation'], 3),
                    "direction": corr['direction']
                }
            })

        return charts

    def _prioritize_charts(
            self,
            charts: List[Dict[str, Any]],
            max_charts: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Prioritize charts by "interestingness" to avoid overwhelming users.
        """
        if len(charts) <= max_charts:
            return charts

        scored_charts = []
        chart_types_seen = {}

        for chart in charts:
            score = 0
            chart_type = chart.get('type', 'unknown')

            type_penalty = chart_types_seen.get(chart_type, 0) * 0.2
            score -= type_penalty
            chart_types_seen[chart_type] = chart_types_seen.get(chart_type, 0) + 1

            data_count = len(chart.get('data', []))
            score += min(data_count / 100, 5)

            if chart_type == 'scatter':
                score += 3

            config = chart.get('config', {})
            stats = config.get('stats', {})
            if stats:
                std = stats.get('std', 0)
                mean = stats.get('mean', 1)
                if mean != 0:
                    cv = std / abs(mean)
                    score += min(cv * 2, 3)

            scored_charts.append((score, chart))

        scored_charts.sort(key=lambda x: x[0], reverse=True)
        return [chart for score, chart in scored_charts[:max_charts]]

    def _generate_charts_for_dataset(
            self,
            df: pd.DataFrame,
            numeric_columns: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Generalized chart generator for datasets.
        Automatically creates charts for ALL numeric columns based on their characteristics.
        NO HARDCODING - works for any dataset (recipes, sales, IoT, finance, etc.)
        """
        charts = []

        if not numeric_columns or df is None or df.empty:
            return charts

        logger.info(f"📊 Generating charts for {len(numeric_columns)} numeric columns")

        for col in numeric_columns:
            non_null_pct = (df[col].notna().sum() / len(df)) * 100
            if non_null_pct < 10:
                logger.info(f"⏭️ Skipping {col}: only {non_null_pct:.1f}% data")
                continue

            series = df[col].dropna()
            if len(series) == 0:
                continue

            unique_count = series.nunique()
            total_count = len(series)
            min_val = float(series.min())
            max_val = float(series.max())
            mean_val = float(series.mean())
            std_val = float(series.std()) if len(series) > 1 else 0

            # CASE 1: Very few unique values → Bar Chart
            if unique_count <= 10:
                value_counts = series.value_counts().sort_index()
                charts.append({
                    "type": "bar",
                    "title": self._humanize_column_name(col),
                    "data": [
                        {"label": str(val), "value": int(count)}
                        for val, count in value_counts.items()
                    ],
                    "config": {
                        "xLabel": self._humanize_column_name(col),
                        "yLabel": "Count",
                        "orientation": "vertical"
                    }
                })
                logger.info(f"📊 Created bar chart for {col} ({unique_count} unique values)")

            # CASE 2: Low variability → Skip
            elif mean_val != 0 and std_val < (abs(mean_val) * 0.1) and unique_count < 5:
                logger.info(f"⏭️ Skipping {col}: low variability (std={std_val:.2f}, mean={mean_val:.2f})")
                continue

            # CASE 3: Many unique values → Histogram
            else:
                bins = self._calculate_optimal_bins(series, unique_count, total_count)
                counts, edges = np.histogram(series, bins=bins)

                charts.append({
                    "type": "histogram",
                    "title": f"{self._humanize_column_name(col)} Distribution",
                    "data": [
                        {
                            "range": f"{edges[i]:.2f}-{edges[i + 1]:.2f}",
                            "count": int(counts[i]),
                            "midpoint": float((edges[i] + edges[i + 1]) / 2)
                        }
                        for i in range(len(counts))
                        if counts[i] > 0
                    ],
                    "config": {
                        "xLabel": self._humanize_column_name(col),
                        "yLabel": "Frequency",
                        "binCount": bins,
                        "stats": {
                            "min": min_val,
                            "max": max_val,
                            "mean": mean_val,
                            "median": float(series.median()),
                            "std": std_val
                        }
                    }
                })
                logger.info(f"📊 Created histogram for {col} ({unique_count} unique values, {bins} bins)")

        # Correlation charts
        if len(numeric_columns) >= 2:
            logger.info(f"🔍 Searching for correlations among {len(numeric_columns)} columns")
            correlation_charts = self._generate_correlation_charts(df, numeric_columns)
            if correlation_charts:
                logger.info(f"📊 Created {len(correlation_charts)} correlation charts")
            charts.extend(correlation_charts)

        # Limit chart count
        if len(charts) > 10:
            logger.info(f"🎯 Prioritizing {len(charts)} charts → top 10")
            charts = self._prioritize_charts(charts, max_charts=10)

        logger.info(f"✅ Generated {len(charts)} charts total")
        return charts

    # ============================================================
    # PUBLIC ENTRYPOINT
    # ============================================================

    def _ensure_fallback_semantic_roles(
            self,
            df: pd.DataFrame,
            semantic_columns: Dict[str, str],
            numeric_columns: List[str],
            text_columns: List[str],
    ) -> Dict[str, str]:
        """
        If LLM/heuristics couldn't infer roles, assign sensible defaults.
        """
        if not any(r == "entity_name" for r in semantic_columns.values()):
            if text_columns:
                col = text_columns[0]
                semantic_columns[col] = "entity_name"
                logger.info(f"📌 Fallback entity_name column: {col}")

        if not any(r == "popularity_metric" for r in semantic_columns.values()):
            if numeric_columns:
                col = numeric_columns[0]
                semantic_columns[col] = "popularity_metric"
                logger.info(f"📌 Fallback popularity_metric column: {col}")

        if not any(r == "quality_score" for r in semantic_columns.values()):
            if len(numeric_columns) > 1:
                col = numeric_columns[1]
                semantic_columns[col] = "quality_score"
                logger.info(f"📌 Fallback quality_score column: {col}")

        return semantic_columns

    def analyze(
            self,
            file_path: str,
            text: str = "",
            metadata: Optional[Dict[str, Any]] = None,
            parsed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        logger.info(f"📊 Dataset Analyzer: {file_path}")
        metadata = metadata or {}

        # 1) UNIVERSAL LOADER
        df = self._load_dataframe_universal(file_path, parsed=parsed, allow_pdf_tables=True)
        df = self._normalize_matrix_headers(df)
        df = self._drop_unnamed_columns(df)
        df = self._flatten_nested_columns(df)
        df = self._clean_numeric_like_columns(df)

        logger.info(f"📌 FINAL DF COLUMNS: {list(df.columns)}")
        logger.info(f"📌 SAMPLE ROWS:\n{df.head(10).to_string()}")

        total_rows, total_columns = df.shape

        if total_rows == 0 or total_columns == 0:
            logger.warning("⚠️ DatasetAnalyzer: empty dataframe after loading/cleaning")
            result = self._empty_result()
            return self.add_ui_config(result, document_type="dataset")

        # ============================================================
        # STRUCTURAL METRICS
        # ============================================================
        memory_usage_mb = float(df.memory_usage(deep=True).sum() / (1024 ** 2))
        total_cells = total_rows * total_columns
        missing_cells = int(df.isna().sum().sum())
        data_completeness_pct = (
            0.0 if total_cells == 0 else (1 - missing_cells / total_cells) * 100
        )
        duplicate_rows = int(df.duplicated().sum())

        numeric_columns = df.select_dtypes(include=["number"]).columns.tolist()
        text_columns = df.select_dtypes(include=["object", "string"]).columns.tolist()
        date_columns = df.select_dtypes(
            include=["datetime64[ns]", "datetime64[ns, UTC]"]
        ).columns.tolist()

        logger.info(f"📊 Detected {len(numeric_columns)} numeric columns: {numeric_columns[:10]}")

        # ============================================================
        # SEMANTIC ROLES
        # ============================================================
        heuristic_roles = self._infer_semantic_roles(df.columns.tolist())

        import pandas as _pd

        def safe_convert(x):
            if isinstance(x, _pd.Timestamp):
                return x.strftime("%Y-%m-%d")
            try:
                if _pd.isna(x):
                    return None
            except (TypeError, ValueError):
                pass
            return x

        df = df.apply(lambda col: col.map(safe_convert))

        schema_sample = self._build_schema_sample(df)
        llm_schema = self._llm_analyze_schema(
            columns=df.columns.tolist(),
            heuristic_roles=heuristic_roles,
            sample_rows=schema_sample,
        )

        semantic_columns = self._merge_semantic_roles(
            heuristic_roles, llm_schema.get("semantic_columns", {})
        )
        semantic_columns = self._ensure_fallback_semantic_roles(
            df=df,
            semantic_columns=semantic_columns,
            numeric_columns=numeric_columns,
            text_columns=text_columns,
        )

        dataset_purpose = llm_schema.get("dataset_purpose", "generic_dataset")

        entity_name_columns = [
            c for c, r in semantic_columns.items() if r == "entity_name"
        ]
        primary_id_columns = [
            c for c, r in semantic_columns.items() if r == "primary_id"
        ]
        quality_cols = [
            c for c, r in semantic_columns.items()
            if r == "quality_score" and c in numeric_columns
        ]
        popularity_cols = [
            c for c, r in semantic_columns.items()
            if r == "popularity_metric" and c in numeric_columns
        ]

        quality_col = self._pick_best_quality_column(quality_cols, numeric_columns)
        popularity_col = self._pick_best_popularity_column(
            popularity_cols, numeric_columns, quality_col
        )

        # ============================================================
        # METRICS
        # ============================================================
        quality_stats = self._simple_numeric_stats(df, quality_col)
        popularity_stats = self._simple_numeric_stats(df, popularity_col)

        segments = self._build_segments(df, quality_col, popularity_col)
        reliability = self._reliability_metrics(df, popularity_col)

        top_popular = self._top_entities(df, entity_name_columns, popularity_col)
        top_rated = self._top_entities(df, entity_name_columns, quality_col)

        quality_distribution = self._distribution_for_chart(df, quality_col)
        popularity_distribution = self._distribution_for_chart(df, popularity_col)

        # ✅ FIX: Return ALL columns for sample records (not just entity columns)
        sample_entities = self._sample_entities(df, entity_name_columns)

        # ============================================================
        # GENERALIZED CHART GENERATION
        # ============================================================
        auto_generated_charts = self._generate_charts_for_dataset(df, numeric_columns)

        # ============================================================
        # LLM INSIGHTS
        # ============================================================
        narrative = self._llm_generate_narrative(
            total_rows=total_rows,
            total_columns=total_columns,
            data_completeness_pct=data_completeness_pct,
            dataset_purpose=dataset_purpose,
            semantic_columns=semantic_columns,
            quality_col=quality_col,
            popularity_col=popularity_col,
            quality_stats=quality_stats,
            popularity_stats=popularity_stats,
            segments=segments,
            reliability=reliability,
            top_popular=top_popular,
            top_rated=top_rated,
        )

        llm_recommendations = self._llm_generate_recommendations(
            dataset_purpose=dataset_purpose,
            quality_col=quality_col,
            popularity_col=popularity_col,
            quality_stats=quality_stats,
            popularity_stats=popularity_stats,
            segments=segments,
            reliability=reliability,
        )

        if not llm_recommendations:
            llm_recommendations = self._heuristic_recommendations(
                popularity_col=popularity_col,
                popularity_stats=popularity_stats,
                reliability=reliability,
                segments=segments,
            )

        # ============================================================
        # RESULT OBJECT
        # ============================================================
        result: Dict[str, Any] = {
            "type": "dataset",
            "summary": narrative,
            "confidence": 0.92,
            "total_rows": total_rows,
            "total_columns": total_columns,
            "total_cells": total_cells,
            "memory_usage_mb": round(memory_usage_mb, 2),
            "data_completeness_pct": round(data_completeness_pct, 2),
            "missing_cells": missing_cells,
            "duplicate_rows": duplicate_rows,
            "numeric_columns": numeric_columns,
            "text_columns": text_columns,
            "date_columns": date_columns,
            "semantic_columns": semantic_columns,
            "dataset_purpose": dataset_purpose,
            "id_columns": primary_id_columns,
            "entity_name_columns": entity_name_columns,
            "quality_metric_column": quality_col,
            "popularity_metric_column": popularity_col,
            # ✅ Human-readable metric labels for frontend table headers
            "popularity_metric_label": self._humanize_column_name(popularity_col) if popularity_col else "Value",
            "quality_metric_label":    self._humanize_column_name(quality_col)    if quality_col    else "Value",
            "quality_stats": quality_stats,
            "popularity_stats": popularity_stats,
            "segments": segments,
            "reliability": reliability,
            "insight_narratives": [narrative],
            "recommendations": llm_recommendations,
            "top_popular_entities": top_popular,
            "top_rated_entities": top_rated,
            "quality_distribution": quality_distribution,
            "popularity_distribution": popularity_distribution,
            "sample_entities": sample_entities,
            # Auto-generated charts
            "auto_charts": auto_generated_charts,
            # Table preview for Data & Charts tab
            "column_names": df.columns.tolist(),
            "sample_rows": schema_sample,
        }

        # ============================================================
        # UNIVERSAL UI CONFIG
        # ============================================================
        return self.add_ui_config(result, document_type="dataset")

    # ============================================================
    # DATA CLEANING HELPERS
    # ============================================================

    def _drop_unnamed_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        mask = ~df.columns.astype(str).str.startswith("Unnamed")
        return df.loc[:, mask]

    def _build_schema_sample(
            self, df: pd.DataFrame, max_rows: int = 5
    ) -> List[Dict[str, Any]]:
        if df.empty:
            return []
        return df.head(max_rows).to_dict(orient="records")

    def _clean_numeric_like_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        def looks_numeric_series(s: pd.Series) -> bool:
            sample = s.dropna().astype(str).head(10)
            if sample.empty:
                return False
            numeric_like = 0
            for v in sample:
                cleaned = re.sub(r"[^\d\-\.,]", "", v)
                if re.search(r"\d", cleaned):
                    numeric_like += 1
            return numeric_like >= 2

        def clean_value(v: Any) -> Any:
            if pd.isna(v):
                return np.nan
            s = str(v)
            s = re.sub(r"[^\d\-\.,]", "", s)
            s = s.replace(" ", "")
            s = s.replace(",", "")
            return s

        for col in df.columns:
            if df[col].dtype == "object" or pd.api.types.is_string_dtype(df[col]):
                if looks_numeric_series(df[col]):
                    cleaned = df[col].apply(clean_value)
                    df[col] = pd.to_numeric(cleaned, errors="coerce")

        return df

    # ============================================================
    # SEMANTIC ROLE HEURISTICS
    # ============================================================

    def _infer_semantic_roles(self, columns: List[str]) -> Dict[str, str]:
        roles: Dict[str, str] = {}
        for col in columns:
            name = str(col).lower()
            role = "unknown"

            if any(k in name for k in ["id", "uuid", "guid", "key", "code"]):
                role = "primary_id"
            if any(k in name for k in ["name", "title", "label"]):
                role = "entity_name"
            if any(k in name for k in ["rating", "score", "stars", "quality"]):
                role = "quality_score"
            if any(
                    k in name
                    for k in [
                        "vote", "view", "count", "plays", "impression",
                        "visits", "clicks", "sales", "amount",
                    ]
            ):
                role = "popularity_metric"
            if any(k in name for k in ["date", "time", "timestamp"]):
                role = "date"

            roles[col] = role

        return roles

    # ============================================================
    # METRICS / DISTRIBUTIONS / SEGMENTS
    # ============================================================

    def _simple_numeric_stats(
            self, df: pd.DataFrame, col: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        if not col or col not in df.columns:
            return None
        s = df[col].dropna()
        if s.empty:
            return None
        return {
            "min": float(s.min()),
            "max": float(s.max()),
            "mean": float(s.mean()),
            "median": float(s.median()),
            "std": float(s.std(ddof=1)) if len(s) > 1 else 0.0,
        }

    def _build_segments(
            self,
            df: pd.DataFrame,
            quality_col: Optional[str],
            popularity_col: Optional[str],
    ) -> Dict[str, Any]:
        if not popularity_col or popularity_col not in df.columns:
            return {"available": False, "segments": []}

        s = df[popularity_col].dropna()
        if s.empty:
            return {"available": False, "segments": []}

        q50 = s.quantile(0.5)
        q80 = s.quantile(0.8)
        q95 = s.quantile(0.95)

        def label(v: float) -> str:
            if v <= q50:
                return "Low Engagement"
            elif v <= q80:
                return "Medium Engagement"
            elif v <= q95:
                return "High Engagement"
            return "Very High Engagement"

        labels = s.apply(label)
        counts = labels.value_counts().to_dict()
        total = len(s)

        segments = [
            {
                "label": seg,
                "count": int(count),
                "percentage": round((count / total) * 100, 1),
            }
            for seg, count in counts.items()
        ]

        return {"available": True, "metric": popularity_col, "segments": segments}

    def _reliability_metrics(
            self,
            df: pd.DataFrame,
            popularity_col: Optional[str],
            low_threshold: float = 20.0,
    ) -> Dict[str, Any]:
        if not popularity_col or popularity_col not in df.columns:
            return {"available": False}

        s = df[popularity_col].dropna()
        if s.empty:
            return {"available": False}

        total = len(s)
        low = int((s < low_threshold).sum())
        reliable = total - low

        return {
            "available": True,
            "metric": popularity_col,
            "threshold": low_threshold,
            "total_evaluated": total,
            "low_reliability_count": low,
            "reliable_count": reliable,
            "reliable_percentage": round((reliable / total) * 100, 1),
        }

    def _top_entities(
            self,
            df: pd.DataFrame,
            entity_name_columns: List[str],
            metric_col: Optional[str],
            top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        if not metric_col or metric_col not in df.columns:
            return []

        label_col: Optional[str] = None
        for c in entity_name_columns:
            if any(k in c.lower() for k in ["title", "name", "label"]):
                label_col = c
                break
        if not label_col and entity_name_columns:
            label_col = entity_name_columns[0]

        cols = [metric_col] + ([label_col] if label_col else [])
        s = df[cols].dropna(subset=[metric_col])

        if s.empty:
            return []

        s_sorted = s.sort_values(by=metric_col, ascending=False).head(top_n)

        return [
            {
                "label": str(row[label_col]) if label_col else "",
                "value": float(row[metric_col]),
            }
            for _, row in s_sorted.iterrows()
        ]

    def _distribution_for_chart(
            self, df: pd.DataFrame, col: Optional[str], bins: int = 20
    ) -> List[Dict[str, Any]]:
        if not col or col not in df.columns:
            return []
        s = df[col].dropna()
        if s.empty:
            return []

        min_val = float(s.min())
        max_val = float(s.max())
        if min_val == max_val:
            return [{"range": f"{min_val}-{max_val}", "count": len(s)}]

        counts, edges = np.histogram(s, bins=bins)
        return [
            {"range": f"{edges[i]:.2f}-{edges[i + 1]:.2f}", "count": int(counts[i])}
            for i in range(len(counts))
        ]

    def _sample_entities(
            self,
            df: pd.DataFrame,
            entity_name_columns: List[str],   # kept for compat, no longer filters columns
            limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Return a full-width sample (ALL columns, first N rows) for the
        Sample Records table. Previously this only returned the first 3
        entity-name columns which left the table nearly empty.
        """
        if df.empty:
            return []
        return df.head(limit).to_dict(orient="records")

    def _merge_semantic_roles(
            self, heuristic: Dict[str, str], llm_roles: Dict[str, str]
    ) -> Dict[str, str]:
        merged = dict(heuristic)
        for col, role in llm_roles.items():
            if col in merged and role and role != "unknown":
                merged[col] = role
        return merged

    def _pick_best_quality_column(
            self,
            quality_cols: List[str],
            numeric_cols: List[str],
    ) -> Optional[str]:
        if quality_cols:
            return quality_cols[0]
        if numeric_cols:
            return numeric_cols[0]
        return None

    def _pick_best_popularity_column(
            self,
            popularity_cols: List[str],
            numeric_cols: List[str],
            quality_col: Optional[str],
    ) -> Optional[str]:
        if popularity_cols:
            return popularity_cols[0]
        for col in numeric_cols:
            if col != quality_col:
                return col
        return None

    # ============================================================
    # LLM HELPERS
    # ============================================================

    def _safe_json(self, text: str) -> Optional[Any]:
        if not text:
            return None

        s = text.strip()
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        s = re.sub(r",\s*([}\]])", r"\1", s)

        candidates = []
        obj_match = re.search(r"\{.*\}", s, re.DOTALL)
        arr_match = re.search(r"\[.*\]", s, re.DOTALL)

        if obj_match:
            candidates.append(obj_match.group(0))
        if arr_match:
            candidates.append(arr_match.group(0))
        candidates.append(s)

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue

        return None

    def _call_llm_json(self, prompt: str) -> Optional[Any]:
        if not self.ollama_client:
            return None

        strict_prompt = (
                "You MUST respond with ONLY valid JSON. "
                "No explanations, no markdown, no comments, no text outside JSON. "
                "Do not wrap the JSON in code fences. "
                "If unsure, return {}.\n\n" + prompt
        )

        try:
            resp = self.ollama_client.generate(
                model=config.model,
                prompt=strict_prompt,
                options={"num_predict": 512},
            )
            raw = resp.get("response", "")
            parsed = self._safe_json(raw)

            if parsed is None:
                logger.warning(f"LLM JSON parse failed. Raw response: {raw[:500]}")
                return None

            return parsed

        except Exception as e:
            logger.warning(f"LLM JSON call failed: {e}")
            return None

    def _call_llm_text(self, prompt: str) -> Optional[str]:
        if not self.ollama_client:
            return None
        try:
            resp = self.ollama_client.generate(
                model=config.model,
                prompt=prompt,
                options={"num_predict": 300},
            )
            return resp.get("response", "").strip()
        except Exception as e:
            logger.warning(f"LLM text call failed: {e}")
            return None

    # ============================================================
    # LLM PROMPTS
    # ============================================================

    def _llm_analyze_schema(
            self,
            columns: List[str],
            heuristic_roles: Dict[str, str],
            sample_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = {
            "columns": columns,
            "heuristic_roles": heuristic_roles,
            "sample_rows": sample_rows,
        }

        prompt = f"""
IMPORTANT: Respond ONLY with valid JSON. No markdown, no comments, no explanations.

You are a senior data analyst. Analyze the dataset schema.

Input:
{json.dumps(payload, indent=2)}

Your tasks:
1. Assign a semantic role to each column:
   - primary_id
   - entity_name
   - quality_score
   - popularity_metric
   - date
   - unknown

2. Identify the dataset purpose:
   - movie_ratings
   - sales
   - HR
   - IoT
   - finance
   - generic_dataset

Return JSON:
{{
  "semantic_columns": {{"column_name": "role"}},
  "dataset_purpose": "..."
}}
"""
        resp = self._call_llm_json(prompt)
        if not resp:
            return {
                "semantic_columns": heuristic_roles,
                "dataset_purpose": "generic_dataset",
            }

        semantic_columns = resp.get("semantic_columns") or heuristic_roles
        dataset_purpose = resp.get("dataset_purpose") or "generic_dataset"

        return {
            "semantic_columns": semantic_columns,
            "dataset_purpose": dataset_purpose,
        }

    def _llm_generate_narrative(
            self,
            total_rows: int,
            total_columns: int,
            data_completeness_pct: float,
            dataset_purpose: str,
            semantic_columns: Dict[str, str],
            quality_col: Optional[str],
            popularity_col: Optional[str],
            quality_stats: Optional[Dict[str, Any]],
            popularity_stats: Optional[Dict[str, Any]],
            segments: Dict[str, Any],
            reliability: Dict[str, Any],
            top_popular: List[Dict[str, Any]],
            top_rated: List[Dict[str, Any]],
    ) -> str:

        payload = {
            "dataset_purpose": dataset_purpose,
            "total_rows": total_rows,
            "total_columns": total_columns,
            "data_completeness_pct": data_completeness_pct,
            "semantic_columns": semantic_columns,
            "quality_metric": {"column": quality_col, "stats": quality_stats},
            "popularity_metric": {"column": popularity_col, "stats": popularity_stats},
            "segments": segments,
            "reliability": reliability,
            "top_popular_entities": top_popular,
            "top_rated_entities": top_rated,
        }

        prompt = f"""
IMPORTANT: Respond ONLY with plain text. No JSON.

You are a business analyst explaining a dataset to a non-technical stakeholder.

Dataset details:
{json.dumps(payload, indent=2)}

Write a 4–6 sentence summary that explains:
- what the dataset contains
- key patterns or distributions
- what it might be useful for

Avoid technical jargon. Be clear, concise, and customer-friendly.
"""

        text = self._call_llm_text(prompt)
        if text:
            return text

        return (
            f"This dataset contains {total_rows:,} records across {total_columns:,} columns. "
            f"Data completeness is {data_completeness_pct:.1f}%. "
            "It includes measurable quality and popularity metrics with identifiable patterns. "
            "The dataset can be used for exploratory analysis and insight generation."
        )

    def _llm_generate_recommendations(
            self,
            dataset_purpose: str,
            quality_col: Optional[str],
            popularity_col: Optional[str],
            quality_stats: Optional[Dict[str, Any]],
            popularity_stats: Optional[Dict[str, Any]],
            segments: Dict[str, Any],
            reliability: Dict[str, Any],
    ) -> List[Dict[str, Any]]:

        payload = {
            "dataset_purpose": dataset_purpose,
            "quality_metric": {"column": quality_col, "stats": quality_stats},
            "popularity_metric": {"column": popularity_col, "stats": popularity_stats},
            "segments": segments,
            "reliability": reliability,
        }

        prompt = f"""
IMPORTANT: Respond ONLY with valid JSON. No markdown, no comments, no explanations.

You are a data consultant reviewing a dataset.

Dataset details:
{json.dumps(payload, indent=2)}

Suggest 3–5 actionable recommendations.
Each recommendation must include:
- title
- category (data_filtering, feature_engineering, segmentation, reporting, modeling)
- priority (high, medium, low)
- description (1–2 sentences)

Return JSON list:
[
  {{
    "title": "...",
    "category": "...",
    "priority": "...",
    "description": "..."
  }}
]
"""

        resp = self._call_llm_json(prompt)
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict) and "recommendations" in resp:
            return resp["recommendations"]

        return []

    # ============================================================
    # HEURISTIC RECOMMENDATIONS (FALLBACK)
    # ============================================================

    def _heuristic_recommendations(
            self,
            popularity_col: Optional[str],
            popularity_stats: Optional[Dict[str, Any]],
            reliability: Dict[str, Any],
            segments: Dict[str, Any],
    ) -> List[Dict[str, Any]]:

        recs: List[Dict[str, Any]] = []

        if reliability.get("available"):
            low = reliability["low_reliability_count"]
            total = reliability["total_evaluated"]
            if low > 0:
                recs.append(
                    {
                        "priority": "medium",
                        "category": "data_filtering",
                        "title": "Filter Low-Reliability Records",
                        "description": (
                            f"{low:,} out of {total:,} records have very low "
                            f"{reliability['metric']} (< {reliability['threshold']:.0f}). "
                            "Consider excluding them from critical analyses."
                        ),
                    }
                )

        if popularity_col and popularity_stats:
            recs.append(
                {
                    "priority": "low",
                    "category": "feature_engineering",
                    "title": "Normalize Long-Tail Metrics",
                    "description": (
                        f"{popularity_col} shows a long-tail distribution with a large gap between "
                        f"median ({popularity_stats['median']:.2f}) and max ({popularity_stats['max']:.2f}). "
                        "Consider log-scaling or bucketing for modeling and reporting."
                    ),
                }
            )

        if segments.get("available"):
            recs.append(
                {
                    "priority": "medium",
                    "category": "segmentation",
                    "title": "Segment-Based Analysis",
                    "description": (
                        f"The dataset has been segmented by {segments.get('metric', 'a key metric')}. "
                        "Analyze each segment separately to uncover distinct patterns and behaviors."
                    ),
                }
            )

        return recs