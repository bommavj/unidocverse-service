import json
import logging
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.core import config
from app.services.analyzers.base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)
from app.services.mixins.tabular_loader_mixin import TabularLoaderMixin

class GenericAnalyzer(BaseAnalyzer, TabularLoaderMixin):
    """
    LLM-powered, customer-friendly, domain-agnostic dataset analyzer.

    - Structural profiling (rows, columns, completeness, duplicates)
    - Heuristic semantic roles
    - LLM semantic refinement + dataset purpose
    - LLM narrative + recommendations
    - LLM-guided UI config
    - Backed by Ollama Mistral 7B
    """

    def __init__(self, ollama_client: Optional[Any] = None) -> None:
        """
        ollama_client must expose:
            generate(model: str, prompt: str) -> dict with a "response" field.
        """
        self.ollama_client = ollama_client
        logger.info("✅ LLM-powered DatasetAnalyzer initialized")

    # ============================================================
    # PUBLIC API
    # ============================================================

    # ============================================================
    # DATA LOADING / PREP HELPERS
    # ============================================================

    def _pick_best_quality_column(
        self,
        quality_cols: List[str],
        numeric_cols: List[str]
    ) -> Optional[str]:
        """
        Choose the best quality metric column.
        Priority:
        1. LLM-identified quality columns
        2. First numeric column (fallback)
        """
        if quality_cols:
            return quality_cols[0]

        if numeric_cols:
            return numeric_cols[0]

        return None


    def _pick_best_popularity_column(
        self,
        popularity_cols: List[str],
        numeric_cols: List[str],
        quality_col: Optional[str]
    ) -> Optional[str]:
        """
        Choose the best popularity metric column.
        Priority:
        1. LLM-identified popularity columns
        2. Any numeric column that is NOT the quality column
        """
        if popularity_cols:
            return popularity_cols[0]

        for col in numeric_cols:
            if col != quality_col:
                return col

        return None

    def _load_dataframe(self, file_path: str, parsed: Optional[Dict[str, Any]]) -> pd.DataFrame:
        """
        Universal loader wrapper:
        - PDF → extract + normalize + merge tables
        - Parsed dicts → merge tables
        - CSV/TSV/XLSX/XLS with encoding fallback
        """
        return self._load_dataframe_universal(
            file_path=file_path,
            parsed=parsed,
            allow_pdf_tables=True
        )


    def _drop_unnamed_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        mask = ~df.columns.astype(str).str.startswith("Unnamed")
        return df.loc[:, mask]

    def _build_schema_sample(self, df: pd.DataFrame, max_rows: int = 5) -> List[Dict[str, Any]]:
        if df.empty:
            return []
        return df.head(max_rows).to_dict(orient="records")

    # ============================================================
    # SEMANTIC ROLE HEURISTICS
    # ============================================================

    def _infer_semantic_roles(self, columns: List[str]) -> Dict[str, str]:
        roles = {}
        for col in columns:
            name = col.lower()
            role = "unknown"

            if any(k in name for k in ["id", "uuid", "guid", "key", "code"]):
                role = "primary_id"
            if any(k in name for k in ["name", "title", "label"]):
                role = "entity_name"
            if any(k in name for k in ["rating", "score", "stars", "quality"]):
                role = "quality_score"
            if any(k in name for k in ["vote", "view", "count", "plays", "impression", "visits", "clicks", "sales", "amount"]):
                role = "popularity_metric"
            if any(k in name for k in ["date", "time", "timestamp"]):
                role = "date"

            roles[col] = role

        return roles

    # ============================================================
    # METRICS / DISTRIBUTIONS / SEGMENTS
    # ============================================================

    def _simple_numeric_stats(self, df: pd.DataFrame, col: Optional[str]) -> Optional[Dict[str, Any]]:
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

        def label(v):
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

        label_col = None
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
            {"label": str(row[label_col]) if label_col else "", "value": float(row[metric_col])}
            for _, row in s_sorted.iterrows()
        ]

    def _distribution_for_chart(self, df: pd.DataFrame, col: Optional[str], bins: int = 20) -> List[Dict[str, Any]]:
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
            {"range": f"{edges[i]:.2f}-{edges[i+1]:.2f}", "count": int(counts[i])}
            for i in range(len(counts))
        ]

    def _sample_entities(
        self,
        df: pd.DataFrame,
        entity_name_columns: List[str],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if not entity_name_columns or df.empty:
            return []
        cols = entity_name_columns[:3]
        return df[cols].head(limit).to_dict(orient="records")

    def _merge_semantic_roles(
        self,
        heuristic: Dict[str, str],
        llm_roles: Dict[str, str]
    ) -> Dict[str, str]:
        """
        Merge heuristic semantic roles with LLM-refined roles.
        LLM roles override heuristics only when:
        - the column exists
        - the LLM role is not None
        - the LLM role is not "unknown"
        """
        merged = dict(heuristic)

        for col, role in llm_roles.items():
            if col in merged and role and role != "unknown":
                merged[col] = role

        return merged



    def analyze(
        self,
        file_path: str,
        text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        parsed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info(f"📊 Dataset Analyzer: {file_path}")
        metadata = metadata or {}

        df = self._load_dataframe(file_path, parsed)
        df = self._drop_unnamed_columns(df)

        # ---------------------------------------------------------
        # FALLBACK: If this is NOT a dataset, use text summary mode
        # ---------------------------------------------------------
        total_rows = int(df.shape[0])
        total_columns = int(df.shape[1])

        # ---------------------------------------------------------
        # FALLBACK: Detect fake tables (newsletters, memos, HOA PDFs)
        # ---------------------------------------------------------
        nan_ratio = df.isna().sum().sum() / (total_rows * total_columns)
        all_object = all(dtype == "object" for dtype in df.dtypes)
        default_colnames = all(col in ["0", "1", "2", "3"] for col in df.columns.astype(str))

        if (
                df.empty
                or nan_ratio > 0.60
                or (all_object and default_colnames)
                or (len(df.select_dtypes(include=["number"]).columns) == 0)
        ):
            logger.info("📝 GenericAnalyzer fallback: Detected non-dataset document — using text summary mode")
            return self._fallback_text_summary(text)

        # 🚨 Prevent pivot/crosstab explosions
        for col in df.columns:
            if df[col].nunique() > 50000:
                logger.warning(f"⚠️ Column '{col}' has extremely high cardinality ({df[col].nunique()} unique).")


        total_rows = int(df.shape[0])
        total_columns = int(df.shape[1])

        if total_rows == 0 or total_columns == 0:
            result = self._empty_result()
            result["ui_config"] = self.build_dataset_ui_config(result, ui_hints={})
            result["has_advanced_analytics"] = False
            return result

        # -----------------------------
        # STRUCTURAL METRICS
        # -----------------------------
        memory_usage_mb = float(df.memory_usage(deep=True).sum() / (1024 ** 2))
        total_cells = int(total_rows * total_columns)
        missing_cells = int(df.isna().sum().sum())
        data_completeness_pct = float(
            0.0 if total_cells == 0 else (1.0 - missing_cells / total_cells) * 100.0
        )
        duplicate_rows = int(df.duplicated().sum())

        numeric_columns = df.select_dtypes(include=["number"]).columns.tolist()
        text_columns = df.select_dtypes(include=["object", "string"]).columns.tolist()
        date_columns = df.select_dtypes(include=["datetime64[ns]", "datetime64[ns, UTC]"]).columns.tolist()

        # -----------------------------
        # HEURISTIC SEMANTIC ROLES
        # -----------------------------
        heuristic_roles = self._infer_semantic_roles(df.columns.tolist())

        # -----------------------------
        # LLM: SCHEMA & PURPOSE ANALYSIS
        # -----------------------------
        schema_sample = self._build_schema_sample(df)
        llm_schema = self._llm_analyze_schema(
            columns=df.columns.tolist(),
            heuristic_roles=heuristic_roles,
            sample_rows=schema_sample,
        )

        semantic_columns = self._merge_semantic_roles(
            heuristic_roles, llm_schema.get("semantic_columns", {})
        )
        dataset_purpose = llm_schema.get("dataset_purpose", "generic_dataset")

        entity_name_columns = [
            c for c, role in semantic_columns.items() if role == "entity_name"
        ]
        primary_id_columns = [
            c for c, role in semantic_columns.items() if role == "primary_id"
        ]
        quality_cols = [
            c for c, role in semantic_columns.items()
            if role == "quality_score" and c in numeric_columns
        ]
        popularity_cols = [
            c for c, role in semantic_columns.items()
            if role == "popularity_metric" and c in numeric_columns
        ]

        quality_col = self._pick_best_quality_column(quality_cols, numeric_columns)
        popularity_col = self._pick_best_popularity_column(popularity_cols, numeric_columns, quality_col)

        # -----------------------------
        # METRICS FOR KEY COLUMNS
        # -----------------------------
        quality_stats = self._simple_numeric_stats(df, quality_col) if quality_col else None
        popularity_stats = self._simple_numeric_stats(df, popularity_col) if popularity_col else None

        segments = self._build_segments(df, quality_col, popularity_col)
        reliability = self._reliability_metrics(df, popularity_col)

        top_popular = self._top_entities(df, entity_name_columns, popularity_col)
        top_rated = self._top_entities(df, entity_name_columns, quality_col)

        quality_distribution = self._distribution_for_chart(df, quality_col) if quality_col else []
        popularity_distribution = self._distribution_for_chart(df, popularity_col) if popularity_col else []

        sample_entities = self._sample_entities(df, entity_name_columns, limit=10)

        # -----------------------------
        # LLM: NARRATIVE & RECOMMENDATIONS
        # -----------------------------
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

        # -----------------------------
        # RESULT OBJECT
        # -----------------------------
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
            "has_advanced_analytics": True,
        }

        # -----------------------------
        # LLM: UI CONFIG GUIDANCE
        # -----------------------------
        llm_ui_hints = self._llm_suggest_ui(
            dataset_purpose=dataset_purpose,
            semantic_columns=semantic_columns,
            has_quality=bool(quality_col),
            has_popularity=bool(popularity_col),
            has_segments=segments.get("available", False),
            has_reliability=reliability.get("available", False),
        )

        result["ui_config"] = self.build_dataset_ui_config(result, llm_ui_hints)
        return result

    # ============================================================
    # LLM CALL HELPERS (Ollama Mistral 7B + JSON SAFE)
    # ============================================================

    def _safe_json(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Try to parse JSON robustly:
        - First attempt direct json.loads
        - Then try to extract the first {...} block
        """
        if not text:
            return None
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                return json.loads(candidate)
            except Exception:
                return None
        return None

    def _call_llm_json(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.ollama_client:
            return None
        strict_prompt = (
            "You MUST respond with ONLY valid JSON. "
            "No explanations, no markdown, no comments, no text outside JSON. "
            "If unsure, return {}.\n\n" + prompt
        )
        try:
            resp = self.ollama_client.generate(
                model=config.model,
                prompt=strict_prompt,
            )
            raw = resp.get("response", "")
            parsed = self._safe_json(raw)
            if parsed is None:
                logger.warning(f"LLM JSON parse failed. Raw response: {raw[:500]}")
            return parsed
        except Exception as e:
            logger.warning(f"LLM JSON call failed: {e}")
            return None

    def _call_llm_text(self, prompt: str) -> Optional[str]:
        if not self.ollama_client:
            return None

        # 1. Primary attempt
        try:
            resp = self.ollama_client.generate(
                model=config.model,
                prompt=prompt,
            )
            text = resp.get("response", "").strip()
            if text and len(text.split()) > 5:
                return text
        except Exception as e:
            logger.warning(f"LLM text call failed: {e}")

        # 2. Retry with shorter prompt
        short_prompt = f"Summarize this document in 4–6 sentences:\n\n{prompt[:3000]}"
        try:
            resp = self.ollama_client.generate(
                model=config.model,
                prompt=short_prompt,
            )
            text = resp.get("response", "").strip()
            if text and len(text.split()) > 5:
                return text
        except Exception as e:
            logger.warning(f"LLM retry failed: {e}")

        # 3. Final fallback
        return None

    # ============================================================
    # LLM PROMPT IMPLEMENTATIONS (STRICT JSON)
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

    # ------------------------------------------------------------

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

        # fallback
        return (
            f"This dataset contains {total_rows:,} records across {total_columns:,} columns. "
            f"Data completeness is {data_completeness_pct:.1f}%. "
            "It includes measurable quality and popularity metrics with identifiable patterns. "
            "The dataset can be used for exploratory analysis and insight generation."
        )

    # ------------------------------------------------------------

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

    # ------------------------------------------------------------

    def _llm_suggest_ui(
        self,
        dataset_purpose: str,
        semantic_columns: Dict[str, str],
        has_quality: bool,
        has_popularity: bool,
        has_segments: bool,
        has_reliability: bool,
    ) -> Dict[str, Any]:

        payload = {
            "dataset_purpose": dataset_purpose,
            "semantic_columns": semantic_columns,
            "has_quality_metric": has_quality,
            "has_popularity_metric": has_popularity,
            "has_segments": has_segments,
            "has_reliability": has_reliability,
        }

        prompt = f"""
IMPORTANT: Respond ONLY with valid JSON. No markdown, no comments, no explanations.

You are designing a dashboard for a dataset.

Dataset details:
{json.dumps(payload, indent=2)}

Decide which sections should be shown:
- show_segments
- show_distributions
- show_entities
- show_sample_entities

Return JSON:
{{
  "show_segments": true/false,
  "show_distributions": true/false,
  "show_entities": true/false,
  "show_sample_entities": true/false
}}
"""

        resp = self._call_llm_json(prompt)
        if not resp:
            return {
                "show_segments": has_segments,
                "show_distributions": has_quality or has_popularity,
                "show_entities": True,
                "show_sample_entities": True,
            }

        return {
            "show_segments": bool(resp.get("show_segments", has_segments)),
            "show_distributions": bool(resp.get("show_distributions", has_quality or has_popularity)),
            "show_entities": bool(resp.get("show_entities", True)),
            "show_sample_entities": bool(resp.get("show_sample_entities", True)),
        }

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
                    "title": "Use Engagement Segments",
                    "description": (
                        "Leverage engagement segments (Low/Medium/High/Very High) "
                        "to create targeted analyses and product experiences."
                    ),
                }
            )

        if not recs:
            recs.append(
                {
                    "priority": "low",
                    "category": "general",
                    "title": "No Critical Issues Detected",
                    "description": "Dataset appears structurally sound with no major quality concerns.",
                }
            )

        priority_order = {"high": 0, "medium": 1, "low": 2}
        return sorted(recs, key=lambda r: priority_order.get(r["priority"], 3))

    # ============================================================
    # UI CONFIG (LLM-GUIDED, CUSTOMER-FRIENDLY)
    # ============================================================

    def build_dataset_ui_config(self, result: Dict[str, Any], ui_hints: Dict[str, Any]) -> Dict[str, Any]:
        show_segments = ui_hints.get("show_segments", True)
        show_distributions = ui_hints.get("show_distributions", True)
        show_entities = ui_hints.get("show_entities", True)
        show_sample_entities = ui_hints.get("show_sample_entities", True)

        # -----------------------------
        # HERO METRICS
        # -----------------------------
        hero_metrics = [
            {
                "key": "total_rows",
                "label": "Total Records",
                "value_path": "total_rows",
                "is_hero": True,
            },
            {
                "key": "total_columns",
                "label": "Columns",
                "value_path": "total_columns",
                "is_hero": True,
            },
            {
                "key": "data_completeness_pct",
                "label": "Data Completeness (%)",
                "value_path": "data_completeness_pct",
                "is_hero": True,
            },
        ]

        if result.get("reliability", {}).get("available"):
            hero_metrics.append(
                {
                    "key": "reliable_percentage",
                    "label": "Reliable Records (%)",
                    "value_path": "reliability.reliable_percentage",
                    "is_hero": True,
                }
            )

        if result.get("top_popular_entities"):
            hero_metrics.append(
                {
                    "key": "top_popular_title",
                    "label": "Most Popular Entity",
                    "value_path": "top_popular_entities.0.label",
                    "is_hero": False,
                }
            )

        # -----------------------------
        # CHARTS
        # -----------------------------
        charts: List[Dict[str, Any]] = []

        if show_segments and result.get("segments", {}).get("available"):
            charts.append(
                {
                    "id": "engagement_segments",
                    "type": "pie",
                    "title": "Engagement Segments",
                    "data_path": "segments.segments",
                    "label_field": "label",
                    "value_field": "count",
                }
            )

        if show_distributions and result.get("quality_distribution"):
            charts.append(
                {
                    "id": "quality_distribution",
                    "type": "bar",
                    "title": f"Distribution of {result.get('quality_metric_column')}",
                    "data_path": "quality_distribution",
                    "label_field": "range",
                    "value_field": "count",
                }
            )

        if show_distributions and result.get("popularity_distribution"):
            charts.append(
                {
                    "id": "popularity_distribution",
                    "type": "bar",
                    "title": f"Distribution of {result.get('popularity_metric_column')}",
                    "data_path": "popularity_distribution",
                    "label_field": "range",
                    "value_field": "count",
                }
            )

        # -----------------------------
        # TABLES
        # -----------------------------
        tables = [
            {
                "id": "recommendations",
                "name": "recommendations",
                "title": "Recommendations",
                "data_path": "recommendations",
            },
        ]

        if show_entities and result.get("top_popular_entities"):
            tables.append(
                {
                    "id": "top_popular_entities",
                    "name": "top_popular_entities",
                    "title": "Top by Popularity",
                    "data_path": "top_popular_entities",
                }
            )

        if show_entities and result.get("top_rated_entities"):
            tables.append(
                {
                    "id": "top_rated_entities",
                    "name": "top_rated_entities",
                    "title": "Top by Quality",
                    "data_path": "top_rated_entities",
                }
            )

        if show_sample_entities and result.get("sample_entities"):
            tables.append(
                {
                    "id": "sample_entities",
                    "name": "sample_entities",
                    "title": "Sample Entities",
                    "data_path": "sample_entities",
                }
            )

        # -----------------------------
        # SECTIONS
        # -----------------------------
        sections = [
            {
                "id": "summary",
                "title": "Dataset Summary",
                "metrics": [m["key"] for m in hero_metrics],
                "charts": [],
                "tables": ["recommendations"],
            },
        ]

        if show_segments and any(c["id"] == "engagement_segments" for c in charts):
            sections.append(
                {
                    "id": "segments",
                    "title": "Engagement Segments",
                    "metrics": [],
                    "charts": ["engagement_segments"],
                    "tables": [],
                }
            )

        if show_distributions and any(
            c["id"] in ["quality_distribution", "popularity_distribution"] for c in charts
        ):
            sections.append(
                {
                    "id": "distributions",
                    "title": "Key Distributions",
                    "metrics": [],
                    "charts": [
                        c["id"]
                        for c in charts
                        if c["id"] in ["quality_distribution", "popularity_distribution"]
                    ],
                    "tables": [],
                }
            )

        if show_entities and any(
            t["id"] in ["top_popular_entities", "top_rated_entities"] for t in tables
        ):
            sections.append(
                {
                    "id": "entities",
                    "title": "Top Entities",
                    "metrics": [],
                    "charts": [],
                    "tables": [
                        t["id"]
                        for t in tables
                        if t["id"] in ["top_popular_entities", "top_rated_entities"]
                    ],
                }
            )

        if show_sample_entities and any(t["id"] == "sample_entities" for t in tables):
            sections.append(
                {
                    "id": "sample",
                    "title": "Sample Records",
                    "metrics": [],
                    "charts": [],
                    "tables": ["sample_entities"],
                }
            )

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }

    # ============================================================
    # FALLBACK / EMPTY RESULT
    # ============================================================

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "type": "dataset",
            "summary": "No usable tabular data could be extracted from this file.",
            "confidence": 0.3,
            "insight_narratives": [
                "Unable to extract a usable dataset. The file may be empty, unstructured, or corrupted."
            ],
            "recommendations": [
                {
                    "priority": "high",
                    "category": "ingestion",
                    "title": "Check Source File",
                    "description": "Verify that the uploaded file contains a valid table or structured data.",
                }
            ],
        }

    def _fallback_text_summary(self, text: str) -> Dict[str, Any]:
        """
        Fallback for non-dataset documents (newsletters, memos, letters, HOA updates, etc.)
        Produces a clean, meaningful summary instead of dataset profiling.
        """
        prompt = f"""
    You are a helpful document summarizer.

    Write a clear, concise summary of the following document.
    Focus on:
    - main purpose
    - key announcements
    - important dates
    - actions required
    - people or organizations mentioned

    Document:
    {text[:6000]}

    Return ONLY the summary text.
    """

        summary = self._call_llm_text(prompt) or text[:500]

        return {
            "type": "document",
            "summary": summary,
            "insights": [],
            "alerts": [],
            "has_advanced_analytics": False,
        }

