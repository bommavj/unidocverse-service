import pdfplumber
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# NORMALIZATION HELPERS
# ---------------------------------------------------------
def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean headers, strip whitespace, convert numeric columns."""
    df = df.copy()

    # Clean headers
    df.columns = [
        str(col).strip() if col and str(col).strip() else f"Column_{i+1}"
        for i, col in enumerate(df.columns)
    ]

    # Clean rows
    df = df.replace(r'^\s*$', np.nan, regex=True)
    df = df.dropna(how='all')

    # Convert numeric columns
    for col in df.columns:
        if df[col].dtype == object:
            cleaned = df[col].astype(str).str.replace("$", "", regex=False)
            cleaned = cleaned.str.replace(",", "", regex=False)
            numeric = pd.to_numeric(cleaned, errors="coerce")

            if numeric.notna().sum() >= len(df) * 0.5:
                df[col] = numeric

    return df


def score_table(df: pd.DataFrame) -> float:
    """Score table quality: more rows, more numeric columns = better."""
    if df is None or df.empty:
        return 0

    numeric_cols = sum(df[col].dtype != object for col in df.columns)
    return len(df) * 2 + numeric_cols


# ---------------------------------------------------------
# UNIVERSAL EXTRACTOR (NO CAMEL0T, NO OCR)
# ---------------------------------------------------------
def extract_tables_universal(file_path: str) -> pd.DataFrame | None:
    """
    Universal PDF table extractor using ONLY:
    - analyzed_data tables (handled outside)
    - pdfplumber basic
    - pdfplumber advanced (text + lines)
    """

    all_tables = []

    # ---------------------------------------------------------
    # 1. pdfplumber basic
    # ---------------------------------------------------------
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for t in tables:
                        if len(t) > 1:
                            df = pd.DataFrame(t[1:], columns=t[0])
                            df = normalize_dataframe(df)
                            all_tables.append(df)
        logger.info(f"pdfplumber basic extracted {len(all_tables)} tables")
    except Exception as e:
        logger.warning(f"pdfplumber basic failed: {e}")

    # ---------------------------------------------------------
    # 2. pdfplumber advanced (line-based + text-based)
    # ---------------------------------------------------------
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                for strategy in [
                    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                    {"vertical_strategy": "text", "horizontal_strategy": "text"},
                ]:
                    tables = page.extract_tables(strategy)
                    if tables:
                        for t in tables:
                            if len(t) > 1:
                                df = pd.DataFrame(t[1:], columns=t[0])
                                df = normalize_dataframe(df)
                                all_tables.append(df)
        logger.info(f"pdfplumber advanced extracted {len(all_tables)} tables total")
    except Exception as e:
        logger.warning(f"pdfplumber advanced failed: {e}")

    # ---------------------------------------------------------
    # 3. Pick the best table
    # ---------------------------------------------------------
    if not all_tables:
        logger.warning("❌ No tables found by pdfplumber")
        return None

    best = max(all_tables, key=score_table)
    logger.info(f"✅ Universal extractor returning table: {best.shape[0]} rows × {best.shape[1]} columns")
    return best
