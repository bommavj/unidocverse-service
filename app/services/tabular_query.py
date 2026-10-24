"""
Exact analytics over spreadsheet/CSV documents for the research agent.

The LLM only *plans* (which columns to group by, which to measure, how to
aggregate) as JSON against the real schema; pandas computes the numbers.
No generated code is executed. Any failure returns None so the caller falls
back to chunk search.
"""
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

import ollama
from app.core import llm
import pandas as pd
from sqlalchemy import text

from app.core import config as app_config
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

TABULAR_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".parquet"}
_AGGS = {"sum", "mean", "count", "min", "max", "median", "nunique"}
_OPS = {"==", "!=", ">", ">=", "<", "<=", "contains"}
_MAX_ROWS_OUT = 50
_STOP = {
    "the", "a", "an", "of", "by", "and", "or", "for", "in", "on", "to", "me", "my", "show",
    "what", "which", "how", "is", "are", "was", "per", "each", "all", "analyze", "analyse",
    "give", "list", "total", "top", "data", "with", "from", "this", "that", "much", "many",
}

_ANALYTIC_RE = re.compile(
    r"\b(by|per|each|group|breakdown|total|sum|average|avg|mean|count|how many|how much|top|"
    r"bottom|highest|lowest|most|least|rank|compare|distribution|trends?|analy[sz]e|max|min)\b",
    re.IGNORECASE,
)
_OVERVIEW_RE = re.compile(
    r"\b(trends?|insights?|summar(y|ise|ize)|overview|highlights?|patterns?|key findings|"
    r"analy[sz]e|analysis|report|tell me about|what stands out)\b",
    re.IGNORECASE,
)


def is_analytic_question(question: str) -> bool:
    return bool(_ANALYTIC_RE.search(question) or _OVERVIEW_RE.search(question))


def is_overview_question(question: str) -> bool:
    # "trends and insights" with no explicit grouping ("by region") → overview
    return bool(_OVERVIEW_RE.search(question)) and not _group_terms(question)


def _words(s: str) -> set:
    parts = re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower())
    return {w.rstrip("s") for w in parts if w not in _STOP and len(w) > 1}


@lru_cache(maxsize=16)
def _load(path: str, mtime: float) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        return pd.read_csv(path, sep="\t" if ext == ".tsv" else ",", low_memory=False)
    if ext == ".parquet":
        return pd.read_parquet(path)
    df = pd.read_excel(path)
    skip = _title_rows(df)
    return pd.read_excel(path, header=skip) if skip else df


def _title_rows(df: pd.DataFrame) -> int:
    """Government and finance workbooks often put a title (and notes) above the real header:
    "Sales and Use Tax Monthly…" then "Calendar Year | Month | Cash Receipts". Return how many rows
    to skip so the first fully labelled row becomes the header (0 when the header is already first)."""
    def labelled(cells) -> bool:
        vals = [c for c in cells if pd.notna(c) and str(c).strip()]
        return len(vals) >= max(2, int(0.6 * len(cells))) and all(isinstance(c, str) for c in vals)
    unnamed = sum(str(c).startswith("Unnamed:") for c in df.columns)
    if unnamed < max(1, len(df.columns) // 2):
        return 0
    for i in range(min(10, len(df))):
        if labelled(list(df.iloc[i])):
            return i + 1
    return 0


def load_frame(path: str) -> pd.DataFrame:
    return _load(path, os.path.getmtime(path))


def find_tabular_docs(question: str, tenant_id: str, doc_ids: Optional[List[str]] = None) -> List[Dict]:
    """
    Tabular docs in scope, ranked by overlap between the question and each
    file's column names + filename. Keyed on file extension, not doc_type,
    so a classifier/planner type mismatch can't hide a spreadsheet.
    """
    db = SessionLocal()
    try:
        sql = ("SELECT id, filename, file_path FROM documents "
               "WHERE status = 'completed' AND tenant_id = :tenant_id AND file_path IS NOT NULL")
        params: Dict[str, Any] = {"tenant_id": tenant_id}
        if doc_ids:
            sql += " AND id::text = ANY(:doc_ids)"
            params["doc_ids"] = list(doc_ids)
        rows = db.execute(text(sql), params).fetchall()
    finally:
        db.close()

    q_words = _words(question)
    # How well the question names some *other* (non-table) document, e.g.
    # "california tax ..." → california-tax-info.pdf. A table only wins if the
    # question points at it at least as strongly.
    other_best = max((len(q_words & _words(r.filename)) for r in rows
                      if os.path.splitext(r.file_path)[1].lower() not in TABULAR_EXTS), default=0)
    ranked = []
    for r in rows:
        if os.path.splitext(r.file_path)[1].lower() not in TABULAR_EXTS or not os.path.exists(r.file_path):
            continue
        try:
            cols = [str(c) for c in load_frame(r.file_path).columns]
        except Exception as e:
            logger.warning(f"[tabular] could not read {r.filename}: {e}")
            continue
        col_words = set().union(*(_words(c) for c in cols)) if cols else set()
        name_hits = len(q_words & _words(r.filename))
        score = 2 * len(q_words & col_words) + name_hits
        # A file-name match only counts if it beats every other document: "taxable sales" must
        # not pull Product-Sales-Region.xlsx when "California" names california-tax-info.pdf
        # Unless the file is clearly named, the columns must cover a real share of the question: "sales tax revenue in
        # fiscal 2023-24 … General Fund" shares only "sales, tax" with a ledger and belongs to the report.
        col_share = len(q_words & col_words) / max(len(q_words), 1)
        if name_hits < 2 and col_share < 0.3:     # one shared word ("sales_ledger") is not naming the file
            continue
        if score > 0 and (name_hits > other_best or (other_best == 0 and name_hits == 0)):
            ranked.append({"id": str(r.id), "filename": r.filename, "path": r.file_path,
                           "score": score, "name_hits": name_hits})
    return sorted(ranked, key=lambda d: -d["score"])


_PLAN_PROMPT = """You translate a question about a table into a JSON query plan.

Table "{filename}" has {n_rows} rows. Columns (name: type):
{schema}

Sample rows:
{sample}

Question: "{question}"

Return ONLY JSON with this shape:
{{"group_by": [column names, 0-2 of them],
  "metric": column name or null,
  "agg": one of ["sum","mean","count","min","max","median","nunique"],
  "filters": [{{"column": name, "op": one of ["==","!=",">",">=","<","<=","contains"], "value": value}}],
  "sort": "desc" or "asc",
  "top_n": integer or null,
  "change": {{"column": period column, "from": value, "to": value}} or null}}

Rules:
- Use only the exact column names listed above.
- "sales", "revenue", "amount" mean the money column (e.g. a total/price column); "units" or "quantity" mean the quantity column.
- For "how many" questions use agg "count" and metric null.
- "by X and Y" means group_by [X, Y].
- For a change, drop, increase or growth "between A and B" (e.g. March 2020 → April 2020): set "change" on the
  period column with its "from" and "to" values exactly as they appear in the data (e.g. Month 3 → 4), put
  the other period parts in filters (e.g. Calendar Year == 2020), and sort "asc" for drops, "desc" for increases.
- "excluding X" / "except X" means a filter with op "!=" on X.
"""


def plan_query(question: str, df: pd.DataFrame, filename: str) -> Optional[Dict]:
    schema = "\n".join(f"- {c}: {df[c].dtype}" for c in df.columns)
    sample = df.head(3).to_string(index=False, max_colwidth=30)
    prompt = _PLAN_PROMPT.format(filename=filename, n_rows=len(df), schema=schema,
                                 sample=sample, question=question)
    try:
        resp = llm.chat(role="planner", 
            model=app_config.model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"temperature": 0.0, "num_predict": 300},
        )
        plan = json.loads(resp["message"]["content"])
    except Exception as e:
        logger.warning(f"[tabular] plan failed: {e}")
        return None
    try:
        return _validate(plan, df, question)
    except Exception as e:
        logger.warning(f"[tabular] invalid plan {plan}: {e}")
        return None


def _match_col(name: Any, df: pd.DataFrame) -> Optional[str]:
    if not isinstance(name, str):
        return None
    if name in df.columns:
        return name
    lower = {str(c).lower(): c for c in df.columns}
    return lower.get(name.strip().lower())


_BY_RE = re.compile(
    r"\b(?:by|per|each|across)\s+(.+?)(?=\s+(?:where|for|in|with|from|during|over|sorted|order|and top)\b|[?,.;]|$)",
    re.IGNORECASE,
)
# "by revenue" / "by total sales" name the measure to sort on, not a grouping
_MEASURE_WORDS = {"revenue", "sale", "amount", "value", "count", "price", "cost", "quantity",
                  "unit", "number", "volume", "profit", "spend", "spending", "income", "total"}


def _group_terms(question: str) -> List[set]:
    terms = []
    for m in _BY_RE.finditer(question):
        for part in re.split(r"\s+and\s+|,|/", m.group(1)):
            w = _words(part)
            if w and not w <= _MEASURE_WORDS:
                terms.append(w)
    return terms


def _check_grouping(group_by: List[str], question: str, df: pd.DataFrame) -> Optional[List[str]]:
    """
    Keep only groupings the user asked for. Every "by X" in the question must
    map to a column; an extra column the model added on its own is dropped.
    None → the question can't be answered from this table.
    """
    terms = _group_terms(question)
    if not terms:
        q = _words(question)
        return [c for c in group_by if _words(c) & q]
    kept = []
    for term in terms:
        match = next((c for c in group_by if _words(c) & term), None)
        if match is None:
            match = next((c for c in df.columns if _words(str(c)) & term), None)
        if match is None:
            return None
        if match not in kept:
            kept.append(match)
    return kept[:2]


def _validate(plan: Dict, df: pd.DataFrame, question: str = "") -> Optional[Dict]:
    if not isinstance(plan, dict):
        return None
    # Models sometimes nest the plan: {"query": {...}} / {"plan": {...}}
    if "group_by" not in plan and len(plan) == 1 and isinstance(next(iter(plan.values())), dict):
        plan = next(iter(plan.values()))
    def _first(v):
        return v[0] if isinstance(v, list) and v else v

    ch = plan.get("change")
    if isinstance(ch, dict) and _match_col(ch.get("column"), df) and ch.get("from") not in (None, "") \
            and ch.get("to") not in (None, ""):
        return _validate_change(plan, ch, df, question)

    agg = _first(plan.get("agg"))
    agg = agg.strip().lower() if isinstance(agg, str) else ""
    agg = {"avg": "mean", "average": "mean", "total": "sum"}.get(agg, agg)
    agg = agg if agg in _AGGS else "sum"
    raw_groups = [c for c in (_match_col(g, df) for g in (plan.get("group_by") or [])) if c]
    metric = _match_col(_first(plan.get("metric")), df)
    if agg not in ("count", "nunique") and (not metric or not pd.api.types.is_numeric_dtype(df[metric])):
        # Model put the measure in group_by or named it only in the question
        q_words = _words(question)
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        metric = next((c for c in raw_groups if c in numeric and _words(c) & q_words), None) \
            or next((c for c in numeric if _words(c) and _words(c) <= q_words), None)
    group_by = [c for c in raw_groups if c != metric]
    group_by = list(dict.fromkeys(group_by))[:2]
    if question:
        group_by = _check_grouping(group_by, question, df)
        if group_by is None:
            return None
    if agg not in ("count", "nunique"):
        if not metric or not pd.api.types.is_numeric_dtype(df[metric]):
            return None
    filters = []
    for f in plan.get("filters") or []:
        if (isinstance(f, dict) and _match_col(f.get("column"), df) and f.get("op") in _OPS
                and f.get("value") not in (None, "")):
            filters.append({"column": _match_col(f["column"], df), "op": f["op"], "value": f.get("value")})
    # Every column the question names must be used; a named yes/no column
    # ("returned", "is_active") is a condition → filter on its true value.
    if question:
        q_words = _words(question)
        used = set(group_by) | {metric} | {f["column"] for f in filters}
        for col in df.columns:
            cw = _words(str(col))
            if not cw or not cw <= q_words or col in used:
                continue
            truthy = _truthy_value(df[col])
            if truthy is None:
                return None
            filters.append({"column": col, "op": "==", "value": truthy})

        # A category value named in the question ("female", "East", "online")
        # is a condition the plan must honour.
        used = set(group_by) | {f["column"] for f in filters}
        for col in df.columns:
            if col in used or pd.api.types.is_numeric_dtype(df[col]):
                continue
            values = df[col].dropna().unique()
            if len(values) > 50:
                continue
            hits = [v for v in values if _words(str(v)) and _words(str(v)) <= q_words]
            if len(hits) == 1:
                # "excluding total aggregates" names the Total row to leave out, not to keep
                excluded = re.search(r"\b(exclud\w*|except|without|other than|not)\b[^.?;]{0,40}\b"
                                     + re.escape(str(hits[0]).split()[0]), question, re.I)
                filters.append({"column": col, "op": "!=" if excluded else "==", "value": hits[0]})
    top_n = plan.get("top_n")
    change = None
    return {
        "change": change,
        "group_by": group_by,
        "metric": metric,
        "agg": agg,
        "filters": filters,
        "sort": "asc" if plan.get("sort") == "asc" else "desc",
        "top_n": int(top_n) if isinstance(top_n, (int, float)) and top_n > 0 else None,
    }


_PCT_COL = re.compile(r"percent|pct|%|change|growth|ratio|rate", re.I)
_CODE_COL = re.compile(r"\b(code|id|naics|sic|key)\b", re.I)


def _validate_change(plan: Dict, ch: Dict, df: pd.DataFrame, question: str) -> Optional[Dict]:
    """A change between two periods: compare an amount column per category, never a column that
    already holds a percentage, never grouped by the period itself, and by a readable label rather
    than a code ("Business Type Category", not "Business Group Code")."""
    period = _match_col(ch["column"], df)
    q_words = _words(question)
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != period
               and not _PCT_COL.search(str(c)) and not _CODE_COL.search(str(c))]
    metric = _match_col(plan.get("metric"), df)
    if metric not in numeric:
        scored = sorted(numeric, key=lambda c: -len(_words(str(c)) & q_words))
        metric = scored[0] if scored and _words(str(scored[0])) & q_words else None
    if not metric:
        return None
    labels = [c for c in df.columns if c != period and not pd.api.types.is_numeric_dtype(df[c])]
    groups = [c for c in (_match_col(g, df) for g in (plan.get("group_by") or [])) if c and c != period]
    groups = [c for c in groups if c in labels]
    readable = [c for c in labels if not _CODE_COL.search(str(c))]
    if groups and _CODE_COL.search(str(groups[0])):
        twin = [c for c in readable if df[c].nunique() == df[groups[0]].nunique()]
        groups = twin[:1] or groups
    if not groups:
        groups = sorted(readable, key=lambda c: -df[c].nunique())[:1]
    if not groups:
        return None
    filters = []
    for f in plan.get("filters") or []:
        col = _match_col(f.get("column"), df) if isinstance(f, dict) else None
        if col and col != period and f.get("op") in _OPS and f.get("value") not in (None, ""):
            filters.append({"column": col, "op": f["op"], "value": f["value"]})
    # "excluding totals": drop aggregate rows by their label too ("Total", "ALL")
    if re.search(r"\b(exclud\w*|except|without)\b[^.?;]{0,40}\b(total|aggregate|all)", question, re.I):
        for c in labels:
            vals = {str(v).strip().lower() for v in df[c].dropna().unique()}
            for agg_label in ("total", "all"):
                if agg_label in vals and not any(f["column"] == c and str(f["value"]).lower() == agg_label for f in filters):
                    filters.append({"column": c, "op": "!=", "value": agg_label})
    top_n = plan.get("top_n")
    return {"group_by": groups[:1], "metric": metric, "agg": "sum", "filters": filters,
            "sort": "desc" if plan.get("sort") == "desc" and not re.search(r"\b(drop|declin|decreas|fall|fell|lowest|loss)", question, re.I) else "asc",
            "top_n": int(top_n) if isinstance(top_n, (int, float)) and top_n > 0 else None,
            "change": {"column": period, "from": _period_value(ch["from"], df[period]),
                       "to": _period_value(ch["to"], df[period])}}


def _truthy_value(series: pd.Series):
    values = set(series.dropna().unique())
    if not values or len(values) > 2:
        return None
    for v in values:
        if v is True or v == 1 or str(v).strip().lower() in ("yes", "y", "true"):
            return v
    return None


def execute(plan: Dict, df: pd.DataFrame) -> pd.DataFrame:
    for f in plan["filters"]:
        col, op, val = df[f["column"]], f["op"], f["value"]
        if op == "contains":
            df = df[col.astype(str).str.contains(str(val), case=False, na=False)]
            continue
        if pd.api.types.is_numeric_dtype(col):
            val = pd.to_numeric(val, errors="coerce")
        else:
            col, val = col.astype(str).str.strip().str.lower(), str(val).strip().lower()
        mask = {"==": col == val, "!=": col != val, ">": col > val,
                ">=": col >= val, "<": col < val, "<=": col <= val}[op]
        df = df[mask]

    if plan.get("change"):
        return _execute_change(plan, df)
    label = f"{plan['agg']}({plan['metric']})" if plan["metric"] else plan["agg"]
    if plan["group_by"]:
        grouped = df.groupby(plan["group_by"], dropna=False)
        series = grouped.size() if plan["agg"] == "count" else grouped[plan["metric"]].agg(plan["agg"])
        out = series.rename(label).reset_index()
    else:
        value = len(df) if plan["agg"] == "count" else df[plan["metric"]].agg(plan["agg"])
        out = pd.DataFrame({label: [value]})
    out = out.sort_values(label, ascending=plan["sort"] == "asc")
    if plan["top_n"]:
        out = out.head(plan["top_n"])
    return out


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def _period_value(v: Any, series: pd.Series) -> Any:
    """Match a period the planner named to the column's own values ("March" → 3 in a numeric Month column)."""
    if pd.api.types.is_numeric_dtype(series):
        if isinstance(v, str) and v.strip().lower()[:3] in {m[:3] for m in _MONTHS}:
            return next(i for m, i in _MONTHS.items() if m[:3] == v.strip().lower()[:3])
        return pd.to_numeric(v, errors="coerce")
    return str(v)


def _execute_change(plan: Dict, df: pd.DataFrame) -> pd.DataFrame:
    """Per-group change of a metric between two values of a period column, with the percentage."""
    ch, metric, agg = plan["change"], plan["metric"], plan["agg"]
    col = df[ch["column"]]
    keyed = col if pd.api.types.is_numeric_dtype(col) else col.astype(str).str.strip()
    a = df[keyed == ch["from"]].groupby(plan["group_by"], dropna=False)[metric].agg(agg)
    b = df[keyed == ch["to"]].groupby(plan["group_by"], dropna=False)[metric].agg(agg)
    out = pd.concat([a.rename(f"{ch['column']} {ch['from']}"), b.rename(f"{ch['column']} {ch['to']}")], axis=1).dropna()
    out = out[out.iloc[:, 0] != 0]
    out["change %"] = (out.iloc[:, 1] - out.iloc[:, 0]) / out.iloc[:, 0].abs() * 100
    out = out.reset_index().sort_values("change %", ascending=plan["sort"] == "asc")
    return out.head(plan["top_n"]) if plan["top_n"] else out


_LAYOUT_REF = re.compile(r"\b(chart|table|figure|exhibit|schedule|section|appendix|page)\s+[0-9IVX]+[A-Z]?\b|"
                         r"\b(the|this) (economic )?(analysis|report) section\b", re.IGNORECASE)


def answer_from_tables(question: str, tenant_id: str, doc_ids: Optional[List[str]] = None) -> Optional[Dict]:
    """Full path. Returns {answer, source, plan, rows} or None to fall back."""
    if not is_analytic_question(question):
        return None
    # "Chart 2", "Table 1", "the economic analysis section" point inside a report — not at a
    # spreadsheet, unless one is in the user's chosen scope
    if _LAYOUT_REF.search(question) and not doc_ids:
        return None
    docs = find_tabular_docs(question, tenant_id, doc_ids)
    if not docs:
        return None
    doc = docs[0]
    df = load_frame(doc["path"])
    # "Summarize / insights" is about a specific file — only when it's named or scoped.
    if is_overview_question(question) and (doc["name_hits"] or doc_ids):
        answer = overview(df, doc["filename"])
        if answer:
            return {"answer": answer, "source": doc["filename"], "plan": {"mode": "overview"}, "rows": len(df)}
    plan = plan_query(question, df, doc["filename"])
    if not plan:
        return None
    try:
        result = execute(plan, df)
    except Exception as e:
        logger.warning(f"[tabular] execute failed: {e} plan={plan}")
        return None
    if result.empty:
        return None

    shown = result.head(_MAX_ROWS_OUT)
    table = shown.to_markdown(index=False, floatfmt=",.2f")
    desc = f"**{plan['agg']}** of **{plan['metric'] or 'rows'}**"
    if plan["group_by"]:
        desc += " by " + " and ".join(f"**{g}**" for g in plan["group_by"])
    if plan.get("change"):
        desc += f", change from {plan['change']['column']} {plan['change']['from']} to {plan['change']['to']}"
    if plan["filters"]:
        desc += " where " + ", ".join(f"{f['column']} {f['op']} {f['value']}" for f in plan["filters"])
    answer = f"{desc} — computed over all {len(df):,} rows of [{doc['filename']}]\n\n{table}"
    if plan.get("change") and "change %" in result.columns and len(result):
        top = result.iloc[0]
        a, b = result.columns[-3], result.columns[-2]
        word = "drop" if top["change %"] < 0 else "increase"
        lead = (f"**Largest {word}: {top[plan['group_by'][0]]}, {top['change %']:+.2f}%** "
                f"({plan['metric']} {top[a]:,.0f} → {top[b]:,.0f}, {a} → {b}).")
        answer = lead + "\n\n" + answer
    if len(result) > _MAX_ROWS_OUT:
        answer += f"\n\n_Showing {_MAX_ROWS_OUT} of {len(result)} groups._"
    logger.info(f"[tabular] {doc['filename']} plan={plan} → {len(result)} rows")
    return {"answer": answer, "source": doc["filename"], "plan": plan, "rows": len(result),
            "chart": _chart(result, plan)}


_CHART_COLORS = ["#2563eb", "#0e9384", "#f79009", "#7a5af8", "#e04f16", "#15b79e", "#dd2590", "#667085"]


def _chart(result: pd.DataFrame, plan: Dict) -> Optional[Dict]:
    """Bar chart for a grouped result (one label column + one number column, 2–30 groups) — the
    Visualize button in Research. Anything else (single totals, wide tables) gets no chart."""
    if len(result.columns) != 2 or not 2 <= len(result) <= 30:
        return None
    label_col, value_col = result.columns
    if not pd.api.types.is_numeric_dtype(result[value_col]) or pd.api.types.is_numeric_dtype(result[label_col]):
        return None
    labels = [str(v) for v in result[label_col]]
    values = [round(float(v), 2) for v in result[value_col]]
    colors = [_CHART_COLORS[i % len(_CHART_COLORS)] for i in range(len(values))]
    return {"type": "bar", "labels": labels,
            "datasets": [{"label": f"{plan.get('agg', 'value')} of {plan.get('metric') or 'rows'}", "data": values,
                          "backgroundColor": [c + "cc" for c in colors], "borderColor": colors,
                          "borderWidth": 1, "borderRadius": 4}],
            "_group_col": str(label_col), "_metric_col": str(value_col)}


# ── Overview: deterministic trends & insights, no LLM ────────────────────────

_MONEY_HINTS = ("total", "revenue", "sales", "amount", "price", "cost", "value", "income", "spend", "profit")


def _pick_metric(df: pd.DataFrame) -> Optional[str]:
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
               and df[c].nunique() > 2 and not re.search(r"\bid\b|_id$|^id", str(c), re.I)]
    for hint in _MONEY_HINTS:
        for c in numeric:
            if hint in str(c).lower():
                return c
    floats = [c for c in numeric if pd.api.types.is_float_dtype(df[c])]
    return (floats or numeric or [None])[0]


def _pick_date(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    for c in df.columns:
        if df[c].dtype == object and re.search(r"date|month|period|time", str(c), re.I):
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().mean() > 0.9:
                df[c] = parsed
                return c
    return None


def _fmt(v: float) -> str:
    return f"{v:,.2f}" if abs(v) < 1e6 else f"{v:,.0f}"


def overview(df: pd.DataFrame, filename: str) -> Optional[str]:
    df = df.copy()
    metric = _pick_metric(df)
    date_col = _pick_date(df)
    categories = [c for c in df.columns
                  if df[c].dtype == object and 2 <= df[c].nunique() <= 20][:5]
    flags = [c for c in df.columns if c != metric and _truthy_value(df[c]) is not None]
    if not metric and not categories:
        return None

    lines = [f"**Overview of [{filename}]** — computed over all {len(df):,} rows"]
    insights = []

    if metric:
        total = df[metric].sum()
        lines.append(f"\n**{metric}:** total {_fmt(total)} · average {_fmt(df[metric].mean())} per row"
                     f" · median {_fmt(df[metric].median())}")

    if date_col and metric:
        d = df.dropna(subset=[date_col])
        span_days = (d[date_col].max() - d[date_col].min()).days
        freq, label = ("QE", "quarter") if span_days > 540 else ("ME", "month")
        series = d.set_index(date_col)[metric].resample(freq).sum()
        series = series[series > 0]
        if len(series) >= 2:
            fmt = (lambda t: f"{t.year} Q{(t.month - 1) // 3 + 1}") if label == "quarter" else (lambda t: t.strftime("%b %Y"))
            lines.append(f"\n**Trend by {label}** ({d[date_col].min():%b %Y} – {d[date_col].max():%b %Y})\n")
            lines.append(f"| {label.title()} | {metric} |\n|---|---:|")
            for t, v in series.items():
                lines.append(f"| {fmt(t)} | {_fmt(v)} |")
            first, last = series.iloc[0], series.iloc[-1]
            change = (last - first) / first * 100 if first else 0
            best, worst = series.idxmax(), series.idxmin()
            insights.append(f"{metric} went from {_fmt(first)} in {fmt(series.index[0])} to {_fmt(last)} in "
                            f"{fmt(series.index[-1])} ({change:+.1f}%).")
            insights.append(f"Best {label}: {fmt(best)} ({_fmt(series[best])}); weakest: {fmt(worst)} ({_fmt(series[worst])}).")
            years = d.groupby(d[date_col].dt.year)[metric].sum()
            if len(years) >= 2:
                insights.append("By year: " + ", ".join(f"{y}: {_fmt(v)}" for y, v in years.items()) + ".")

    for col in categories:
        if metric:
            g = df.groupby(col)[metric].sum().sort_values(ascending=False)
            share = g / g.sum() * 100
            lines.append(f"\n**{metric} by {col}**\n\n| {col} | {metric} | Share |\n|---|---:|---:|")
            for k, v in g.items():
                lines.append(f"| {k} | {_fmt(v)} | {share[k]:.1f}% |")
            spread = (g.iloc[0] - g.iloc[-1]) / g.iloc[-1] * 100 if g.iloc[-1] else 0
            insights.append(f"**{col}:** {g.index[0]} leads with {share.iloc[0]:.1f}%; {g.index[-1]} is lowest "
                            f"at {share.iloc[-1]:.1f}% ({spread:.0f}% gap).")
        else:
            vc = df[col].value_counts()
            insights.append(f"**{col}:** most common is {vc.index[0]} ({vc.iloc[0]:,} rows).")

    for col in flags[:3]:
        truthy = _truthy_value(df[col])
        rate = (df[col] == truthy).mean() * 100
        insights.append(f"**{col}:** {rate:.1f}% of rows ({(df[col] == truthy).sum():,}).")
        for cat in categories[:2]:
            by = (df[col] == truthy).groupby(df[cat]).mean().sort_values(ascending=False) * 100
            insights.append(f"  {col} rate is highest for {cat} = {by.index[0]} ({by.iloc[0]:.1f}%) "
                            f"and lowest for {by.index[-1]} ({by.iloc[-1]:.1f}%).")

    if insights:
        lines.insert(1, "\n**Key insights**\n" + "\n".join(f"- {i}" if not i.startswith("  ") else f"  - {i.strip()}"
                                                          for i in insights))
    return "\n".join(lines)
