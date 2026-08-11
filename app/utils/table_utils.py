import logging
from typing import Dict, Any, List, Optional
import pandas as pd

logger = logging.getLogger(__name__)


def normalize_table(table):
    """
    Universal table normalizer.
    Accepts ANY table structure and returns a clean, consistent format:
    {
        "columns": [...],
        "data": [...]
    }
    """

    # ---------------------------------------------------------
    # 0. Reject obvious error objects
    # ---------------------------------------------------------
    if table is None:
        return None

    if isinstance(table, dict) and "error" in table:
        return None

    # ---------------------------------------------------------
    # 1. Already normalized format
    # ---------------------------------------------------------
    if isinstance(table, dict) and "columns" in table and "data" in table:
        cols = table["columns"]
        rows = table["data"]
        return {
            "columns": [str(c).strip() for c in cols],
            "data": rows
        }

    # ---------------------------------------------------------
    # 2. Format: {"columns": [...], "rows": [...]}
    # ---------------------------------------------------------
    if isinstance(table, dict) and "columns" in table and "rows" in table:
        cols = table["columns"]
        rows = table["rows"]
        return {
            "columns": [str(c).strip() for c in cols],
            "data": rows
        }

    # ---------------------------------------------------------
    # 3. Format: {"data": [...]}
    # ---------------------------------------------------------
    if isinstance(table, dict) and "data" in table:
        data = table["data"]

        # list-of-dicts
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            cols = list(data[0].keys())
            rows = [[row.get(col, "") for col in cols] for row in data]
            return {
                "columns": cols,
                "data": rows
            }

        # list-of-lists
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
            cols = [str(c).strip() for c in data[0]]
            rows = data[1:]
            return {
                "columns": cols,
                "data": rows
            }

    # ---------------------------------------------------------
    # 4. Raw list-of-dicts
    # ---------------------------------------------------------
    if isinstance(table, list) and len(table) > 0 and isinstance(table[0], dict):
        cols = list(table[0].keys())
        rows = [[row.get(col, "") for col in cols] for row in table]
        return {
            "columns": cols,
            "data": rows
        }

    # ---------------------------------------------------------
    # 5. Raw list-of-lists (pdfplumber/tabula)
    # ---------------------------------------------------------
    if isinstance(table, list) and len(table) > 0 and isinstance(table[0], list):
        header = table[0]
        rows = table[1:]
        cols = [str(c).strip() if c else f"Column_{i}" for i, c in enumerate(header)]
        return {
            "columns": cols,
            "data": rows
        }

    # ---------------------------------------------------------
    # 6. Pandas DataFrame
    # ---------------------------------------------------------
    try:
        import pandas as pd
        if isinstance(table, pd.DataFrame):
            cols = [str(c).strip() for c in table.columns]
            rows = table.fillna("").values.tolist()
            return {
                "columns": cols,
                "data": rows
            }
    except:
        pass

    # ---------------------------------------------------------
    # 7. Unknown format → reject
    # ---------------------------------------------------------
    return None




# def normalize_table(table: Any) -> Optional[Dict[str, Any]]:
#     """
#     Normalize table to standard format with proper error handling.
#
#     Supports:
#     - Dict with 'data' key (PDF parser)
#     - Dict with 'data' + 'columns' (already normalized)
#     - List of lists
#     - List of dicts
#     - pandas DataFrame
#     """
#
#     try:
#         # 🔥 EARLY EXIT FOR ERROR OBJECTS
#         if isinstance(table, dict) and "error" in table:
#             logger.error(f"❌ Upstream table extraction error: {table['error']}")
#             return None
#
#         # 🔥 EARLY EXIT FOR EMPTY / NONE
#         if table is None:
#             logger.warning("⚠️ Received None table")
#             return None
#
#         # Format 1: Already normalized dict
#         if isinstance(table, dict) and 'data' in table and 'columns' in table:
#             logger.info(f"✅ Table already normalized: {len(table.get('data', []))} rows")
#             return table
#
#         # Format 2: PDF parser dict with 'data' (list-of-lists)
#         if isinstance(table, dict) and 'data' in table:
#             table_data = table['data']
#
#             if not table_data or len(table_data) < 2:
#                 logger.warning("❌ Table data is empty or missing rows")
#                 return None
#
#             headers = table_data[0]
#             data_rows = table_data[1:]
#
#             clean_headers = []
#             for i, header in enumerate(headers):
#                 if header and str(header).strip():
#                     clean_headers.append(str(header).strip())
#                 else:
#                     clean_headers.append(f"Column_{i + 1}")
#
#             normalized_data = []
#             for row in data_rows:
#                 if not row or not any(cell for cell in row):
#                     continue
#
#                 row_dict = {}
#                 for header, cell in zip(clean_headers, row):
#                     if cell is not None and str(cell).strip():
#                         row_dict[header] = str(cell).strip()
#                     else:
#                         row_dict[header] = None
#
#                 if any(v is not None for v in row_dict.values()):
#                     normalized_data.append(row_dict)
#
#             if normalized_data:
#                 result = {
#                     "data": normalized_data,
#                     "columns": clean_headers,
#                     "row_count": len(normalized_data)
#                 }
#                 logger.info(f"✅ Normalized PDF table: {len(normalized_data)} rows × {len(clean_headers)} columns")
#                 return result
#             else:
#                 logger.warning("❌ No valid rows after PDF normalization")
#                 return None
#
#         # Format 3: List of dicts
#         if isinstance(table, list) and len(table) > 0 and isinstance(table[0], dict):
#             columns = list(table[0].keys())
#             result = {
#                 "data": table,
#                 "columns": columns,
#                 "row_count": len(table)
#             }
#             logger.info(f"✅ Table is list-of-dicts: {len(table)} rows × {len(columns)} columns")
#             return result
#
#         # Format 4: pandas DataFrame
#         if hasattr(table, 'to_dict'):
#             try:
#                 data = table.to_dict('records')
#                 columns = list(table.columns)
#                 result = {
#                     "data": data,
#                     "columns": columns,
#                     "row_count": len(table)
#                 }
#                 logger.info(f"✅ Converted DataFrame: {len(table)} rows × {len(columns)} columns")
#                 return result
#             except Exception as e:
#                 logger.error(f"❌ Failed to convert DataFrame: {str(e)}")
#                 return None
#
#         # Format 5: Raw list-of-lists
#         if isinstance(table, list) and len(table) > 1:
#             headers = table[0]
#             data_rows = table[1:]
#
#             clean_headers = []
#             for i, header in enumerate(headers):
#                 if header and str(header).strip():
#                     clean_headers.append(str(header).strip())
#                 else:
#                     clean_headers.append(f"Column_{i + 1}")
#
#             normalized_data = []
#             for row in data_rows:
#                 if row and any(cell for cell in row):
#                     row_dict = {}
#                     for header, cell in zip(clean_headers, row):
#                         if cell is not None and str(cell).strip():
#                             row_dict[header] = str(cell).strip()
#                         else:
#                             row_dict[header] = None
#
#                     if any(v is not None for v in row_dict.values()):
#                         normalized_data.append(row_dict)
#
#             if normalized_data:
#                 result = {
#                     "data": normalized_data,
#                     "columns": clean_headers,
#                     "row_count": len(normalized_data)
#                 }
#                 logger.info(f"✅ Normalized list-of-lists: {len(normalized_data)} rows × {len(clean_headers)} columns")
#                 return result
#             else:
#                 logger.warning("❌ No valid rows after list-of-lists normalization")
#                 return None
#
#         # Unknown format
#         logger.warning(f"❌ Unsupported table format: {type(table)}")
#         logger.warning(f"   Table preview: {str(table)[:200]}")
#         return None
#
#     except Exception as e:
#         logger.error(f"❌ Table normalization failed: {str(e)}")
#         import traceback
#         traceback.print_exc()
#         return None


def normalize_tables(tables: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize a list of tables safely.
    """
    normalized = []

    for i, table in enumerate(tables):
        logger.info(f"🔄 Normalizing table {i + 1}/{len(tables)}")

        if table is None:
            logger.warning(f"⚠️ Table {i + 1} is None — skipping")
            continue

        result = normalize_table(table)

        if result is None:
            logger.warning(f"⚠️ Table {i + 1} could not be normalized")
            continue

        normalized.append(result)

    logger.info(f"✅ Normalized {len(normalized)}/{len(tables)} tables")
    return normalized


def table_to_dataframe(table: Dict[str, Any]) -> Optional[pd.DataFrame]:
    """
    Convert normalized table to pandas DataFrame.
    """
    try:
        if not table or 'data' not in table:
            logger.error("❌ Table missing 'data' key")
            return None

        data = table['data']
        if not data:
            logger.warning("⚠️ Table data is empty")
            return None

        df = pd.DataFrame(data)

        # Clean numeric columns
        for col in df.columns:
            if df[col].dtype == 'object':
                cleaned = (
                    df[col]
                    .astype(str)
                    .str.strip()
                    .str.replace('$', '', regex=False)
                    .str.replace(',', '', regex=False)
                    .str.replace('None', '', regex=False)
                )

                numeric_series = pd.to_numeric(cleaned, errors='coerce')

                non_null_count = df[col].notna().sum()
                if non_null_count > 0:
                    numeric_ratio = numeric_series.notna().sum() / non_null_count
                    if numeric_ratio > 0.5:
                        df[col] = numeric_series

        logger.info(f"✅ Created DataFrame: {len(df)} rows × {len(df.columns)} columns")
        return df

    except Exception as e:
        logger.error(f"❌ Failed to convert table to DataFrame: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
