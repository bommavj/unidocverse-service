import logging
from typing import Any, Dict, Optional, List
import json
import pandas as pd

logger = logging.getLogger(__name__)


class TabularLoaderMixin:
    """
    Universal tabular loader:
    - PDF → extract ALL tables → normalize → merge vertically
    - Parsed dicts → merge ALL tables
    - CSV / TSV / XLSX / XLS / JSON / JSONL / Parquet / ODS with encoding fallback
    - Fallback: best-effort CSV with latin1 + on_bad_lines="skip"
    """

    # -----------------------------
    # INTERNAL HELPERS
    # -----------------------------

    def _read_csv_with_fallback(self, file_path: str) -> pd.DataFrame:
        encodings = ["utf-8", "utf-8-sig", "latin1", "cp1252"]
        seps = [","]

        for enc in encodings:
            for sep in seps:
                try:
                    logger.info(f"📥 Trying CSV read: enc={enc}, sep={repr(sep)}")
                    return pd.read_csv(
                        file_path,
                        encoding=enc,
                        sep=sep,
                        on_bad_lines="skip",
                        parse_dates=False,
                    )
                except UnicodeDecodeError:
                    logger.warning(f"⚠️ UnicodeDecodeError with encoding={enc}, sep={repr(sep)}")
                    continue
                except Exception as e:
                    logger.warning(f"⚠️ CSV read failed (enc={enc}, sep={repr(sep)}): {e}")
                    continue

        # Final brute-force fallback
        try:
            logger.warning("⚠️ Falling back to latin1 CSV with on_bad_lines='skip'")
            return pd.read_csv(
                file_path,
                encoding="latin1",
                on_bad_lines="skip",
                parse_dates=False,
            )
        except Exception as e:
            logger.error(f"❌ Final CSV fallback failed: {e}")
            return pd.DataFrame()

    def _load_json(self, file_path: str) -> pd.DataFrame:
        """Load JSON or JSONL files"""
        logger.info(f"📥 Loading JSON file: {file_path}")

        try:
            # Try JSONL first (one JSON object per line)
            if file_path.lower().endswith('.jsonl'):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = [json.loads(line) for line in f if line.strip()]
                return pd.DataFrame(data)

            # Regular JSON
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures
            if isinstance(data, list):
                # List of objects → DataFrame
                return pd.DataFrame(data)
            elif isinstance(data, dict):
                # Check if it's a dict with numeric/string keys (like "0", "1", "2")
                # This is common in pandas.to_json(orient='index') format
                keys = list(data.keys())
                if keys and all(str(k).isdigit() or isinstance(k, int) for k in keys[:10]):
                    logger.info(f"📊 Detected indexed dict format (keys: {keys[:5]}...)")
                    # Convert dict to list of values
                    records = [data[k] for k in sorted(keys, key=lambda x: int(x) if str(x).isdigit() else x)]
                    return pd.DataFrame(records)

                # Try to find a list inside the dict
                for key, value in data.items():
                    if isinstance(value, list) and len(value) > 0:
                        logger.info(f"📊 Found list in JSON key: '{key}'")
                        return pd.DataFrame(value)

                # Single object → single-row DataFrame
                logger.info("📊 Single JSON object → converting to single-row DataFrame")
                return pd.DataFrame([data])
            else:
                logger.warning(f"⚠️ Unsupported JSON structure: {type(data)}")
                return pd.DataFrame()

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON format: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"❌ Failed to load JSON: {e}")
            return pd.DataFrame()

    def _merge_dataframes(self, dfs: List[pd.DataFrame], source_label: str) -> pd.DataFrame:
        dfs = [df for df in dfs if isinstance(df, pd.DataFrame) and not df.empty]
        if not dfs:
            logger.warning(f"⚠️ No usable DataFrames to merge from {source_label}")
            return pd.DataFrame()

        merged = pd.concat(dfs, ignore_index=True, sort=False)

        # ✅ Ensure all column names are strings so downstream analyzers can safely call .lower()
        merged.columns = merged.columns.map(str)

        logger.info(
            f"📊 MERGED DATAFRAME from {source_label}: "
            f"{merged.shape[0]} rows × {merged.shape[1]} cols (from {len(dfs)} tables)"
        )
        return merged

    # -----------------------------
    # MAIN UNIVERSAL LOADER
    # -----------------------------

    def _load_dataframe_universal(
            self,
            file_path: str,
            parsed: Optional[Dict[str, Any]] = None,
            allow_pdf_tables: bool = True,
    ) -> pd.DataFrame:
        """
        Dataset-aware universal loader:
        - PDF → extract ALL tables → normalize → merge vertically
        - Parsed dicts → merge ALL tables
        - CSV / TSV / XLSX / XLS / JSON / JSONL / Parquet / ODS with encoding fallback
        - Fallback: best-effort CSV
        """

        lower = file_path.lower()

        # ============================================================
        # 1) PDF → extract tables OR extract text OR OCR fallback
        # ============================================================
        if allow_pdf_tables and lower.endswith(".pdf"):
            logger.info("📄 PDF detected — attempting table/text/OCR extraction")

            try:
                from app.parsers.pdf_table_extractor import extract_pdf_tables
                from app.parsers.pdf_table_extractor import extract_pdf_text
                from app.parsers.pdf_table_extractor import extract_pdf_fields
                from app.parsers.pdf_table_extractor import ocr_pdf_to_text
                from app.utils.table_utils import normalize_tables
            except Exception as e:
                logger.error(f"❌ PDF parser unavailable: {e}")
                return pd.DataFrame()

            # ------------------------------------------------------------
            # 1) Try TABLE extraction first
            # ------------------------------------------------------------
            try:
                raw_tables = extract_pdf_tables(file_path)
                if raw_tables:
                    normalized = normalize_tables(raw_tables) or []
                    dfs = []

                    for idx, t in enumerate(normalized, start=1):
                        try:
                            if isinstance(t, dict) and "data" in t:
                                df_t = pd.DataFrame(t["data"])
                            elif isinstance(t, pd.DataFrame):
                                df_t = t
                            else:
                                continue

                            if not df_t.empty:
                                dfs.append(df_t)
                                logger.info(
                                    f"📄 PDF table {idx}: "
                                    f"{df_t.shape[0]} rows × {df_t.shape[1]} cols"
                                )
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to convert PDF table {idx}: {e}")

                    merged_pdf = self._merge_dataframes(dfs, source_label="PDF tables")
                    if not merged_pdf.empty:
                        return merged_pdf

                logger.info("ℹ️ PDF has no tables — falling back to text extraction")

            except Exception as e:
                logger.error(f"❌ PDF table extraction failed: {e}")

            # ------------------------------------------------------------
            # 2) Try TEXT extraction (machine-readable PDFs)
            # ------------------------------------------------------------
            text = ""
            try:
                text = extract_pdf_text(file_path)
                if text and text.strip():
                    logger.info("📝 Returning PDF text as DataFrame")
                    return pd.DataFrame({"text": [text]})
            except Exception as e:
                logger.error(f"❌ PDF text extraction failed: {e}")

            # ------------------------------------------------------------
            # 3) Try FIELD extraction (H‑1B receipts, USCIS notices)
            # ------------------------------------------------------------
            try:
                if text:
                    fields = extract_pdf_fields(text)
                    if fields:
                        logger.info("📑 Returning structured PDF fields as DataFrame")
                        return pd.DataFrame([fields])
            except Exception as e:
                logger.error(f"❌ PDF field extraction failed: {e}")

            # ------------------------------------------------------------
            # 4) OCR fallback (scanned PDFs like H‑1B receipts)
            # ------------------------------------------------------------
            try:
                logger.info("🔍 Running OCR fallback for scanned PDF")
                ocr_text = ocr_pdf_to_text(file_path)

                if ocr_text and ocr_text.strip():
                    logger.info("📝 OCR extracted text — returning as DataFrame")
                    return pd.DataFrame({"text": [ocr_text]})

                # Try field extraction on OCR text
                fields = extract_pdf_fields(ocr_text)
                if fields:
                    logger.info("📑 Returning OCR‑extracted structured fields")
                    return pd.DataFrame([fields])

            except Exception as e:
                logger.error(f"❌ OCR failed: {e}")

            # ------------------------------------------------------------
            # Nothing usable
            # ------------------------------------------------------------
            logger.warning("⚠️ PDF contained no tables, no text, and OCR failed")
            return pd.DataFrame()

        # ============================================================
        # 2) PARSED TABLES → merge ALL tables
        # ============================================================
        if parsed and isinstance(parsed, dict):
            tables = parsed.get("tables") or parsed.get("table")
            if tables:
                if not isinstance(tables, list):
                    tables = [tables]

                dfs: List[pd.DataFrame] = []

                for idx, t in enumerate(tables, start=1):
                    if not isinstance(t, dict):
                        continue

                    try:
                        if "data" in t:
                            df_t = pd.DataFrame(t["data"])
                        elif "rows" in t and "columns" in t:
                            df_t = pd.DataFrame(t["rows"], columns=t["columns"])
                        else:
                            continue

                        if not df_t.empty:
                            dfs.append(df_t)
                            logger.info(
                                f"📦 Parsed table {idx}: "
                                f"{df_t.shape[0]} rows × {df_t.shape[1]} cols"
                            )
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to convert parsed table {idx}: {e}")

                merged_parsed = self._merge_dataframes(dfs, source_label="parsed tables")
                if not merged_parsed.empty:
                    return merged_parsed

        # ============================================================
        # 3) STANDARD FILE TYPES (CSV/TSV/Excel/JSON/Parquet/ODS) WITH FALLBACK
        # ============================================================
        try:
            # JSON / JSONL
            if lower.endswith(('.json', '.jsonl')):
                logger.info(f"📥 Loading JSON file: {file_path}")
                return self._load_json(file_path)

            # CSV
            if lower.endswith(".csv"):
                logger.info("📥 Loading CSV via universal loader")
                return self._read_csv_with_fallback(file_path)

            # TSV
            if lower.endswith(".tsv"):
                logger.info("📥 Loading TSV via universal loader")
                return pd.read_csv(file_path, sep="\t", on_bad_lines="skip", parse_dates=False)

            # Excel
            if lower.endswith((".xlsx", ".xls", ".xlsm")):
                logger.info("📥 Loading Excel via universal loader")
                return pd.read_excel(file_path)

            # Parquet
            if lower.endswith(".parquet"):
                logger.info("📥 Loading Parquet via universal loader")
                return pd.read_parquet(file_path)

            # ODS (OpenDocument Spreadsheet)
            if lower.endswith(".ods"):
                logger.info("📥 Loading ODS via universal loader")
                return pd.read_excel(file_path, engine='odf')

            # Unknown extension → best-effort CSV
            logger.info("📥 Unknown extension — attempting CSV with fallback")
            return self._read_csv_with_fallback(file_path)

        except Exception as e:
            logger.error(f"❌ Failed to read dataset file: {e}")
            return pd.DataFrame()

# import logging
# from typing import Any, Dict, Optional, List
#
# import pandas as pd
#
# logger = logging.getLogger(__name__)
#
#
# class TabularLoaderMixin:
#     """
#     Universal tabular loader:
#     - PDF → extract ALL tables → normalize → merge vertically
#     - Parsed dicts → merge ALL tables
#     - CSV / TSV / XLSX / XLS with encoding fallback
#     - Fallback: best-effort CSV with latin1 + on_bad_lines="skip"
#     """
#
#     # -----------------------------
#     # INTERNAL HELPERS
#     # -----------------------------
#
#     def _read_csv_with_fallback(self, file_path: str) -> pd.DataFrame:
#         encodings = ["utf-8", "utf-8-sig", "latin1", "cp1252"]
#         seps = [","]
#
#         for enc in encodings:
#             for sep in seps:
#                 try:
#                     logger.info(f"📥 Trying CSV read: enc={enc}, sep={repr(sep)}")
#                     return pd.read_csv(
#                         file_path,
#                         encoding=enc,
#                         sep=sep,
#                         on_bad_lines="skip",
#                     )
#                 except UnicodeDecodeError:
#                     logger.warning(f"⚠️ UnicodeDecodeError with encoding={enc}, sep={repr(sep)}")
#                     continue
#                 except Exception as e:
#                     logger.warning(f"⚠️ CSV read failed (enc={enc}, sep={repr(sep)}): {e}")
#                     continue
#
#         # Final brute-force fallback
#         try:
#             logger.warning("⚠️ Falling back to latin1 CSV with on_bad_lines='skip'")
#             return pd.read_csv(
#                 file_path,
#                 encoding="latin1",
#                 on_bad_lines="skip",
#             )
#         except Exception as e:
#             logger.error(f"❌ Final CSV fallback failed: {e}")
#             return pd.DataFrame()
#
#     def _merge_dataframes(self, dfs: List[pd.DataFrame], source_label: str) -> pd.DataFrame:
#         dfs = [df for df in dfs if isinstance(df, pd.DataFrame) and not df.empty]
#         if not dfs:
#             logger.warning(f"⚠️ No usable DataFrames to merge from {source_label}")
#             return pd.DataFrame()
#
#         merged = pd.concat(dfs, ignore_index=True, sort=False)
#
#         # ✅ Ensure all column names are strings so downstream analyzers can safely call .lower()
#         merged.columns = merged.columns.map(str)
#
#         logger.info(
#             f"📊 MERGED DATAFRAME from {source_label}: "
#             f"{merged.shape[0]} rows × {merged.shape[1]} cols (from {len(dfs)} tables)"
#         )
#         return merged
#
#     # -----------------------------
#     # MAIN UNIVERSAL LOADER
#     # -----------------------------
#
#     def _load_dataframe_universal(
#         self,
#         file_path: str,
#         parsed: Optional[Dict[str, Any]] = None,
#         allow_pdf_tables: bool = True,
#     ) -> pd.DataFrame:
#         """
#         Dataset-aware universal loader:
#         - PDF → extract ALL tables → normalize → merge vertically
#         - Parsed dicts → merge ALL tables
#         - CSV / TSV / XLSX / XLS with encoding fallback
#         - Fallback: best-effort CSV
#         """
#
#         lower = file_path.lower()
#
#         # ============================================================
#         # 1) PDF → extract + normalize + MERGE ALL TABLES
#         # ============================================================
#         # ============================================================
#         # 1) PDF → extract tables OR extract text if no tables
#         # ============================================================
#         # ============================================================
#         # 1) PDF → extract tables OR extract text OR OCR fallback
#         # ============================================================
#         if allow_pdf_tables and lower.endswith(".pdf"):
#             logger.info("📄 PDF detected — attempting table/text/OCR extraction")
#
#             try:
#                 from app.parsers.pdf_table_extractor import extract_pdf_tables
#                 from app.parsers.pdf_table_extractor import extract_pdf_text  # FIXED
#                 from app.parsers.pdf_table_extractor import extract_pdf_fields  # FIXED
#                 from app.parsers.pdf_table_extractor import ocr_pdf_to_text  # NEW
#                 from app.utils.table_utils import normalize_tables
#             except Exception as e:
#                 logger.error(f"❌ PDF parser unavailable: {e}")
#                 return pd.DataFrame()
#
#             # ------------------------------------------------------------
#             # 1) Try TABLE extraction first
#             # ------------------------------------------------------------
#             try:
#                 raw_tables = extract_pdf_tables(file_path)
#                 if raw_tables:
#                     normalized = normalize_tables(raw_tables) or []
#                     dfs = []
#
#                     for idx, t in enumerate(normalized, start=1):
#                         try:
#                             if isinstance(t, dict) and "data" in t:
#                                 df_t = pd.DataFrame(t["data"])
#                             elif isinstance(t, pd.DataFrame):
#                                 df_t = t
#                             else:
#                                 continue
#
#                             if not df_t.empty:
#                                 dfs.append(df_t)
#                                 logger.info(
#                                     f"📄 PDF table {idx}: "
#                                     f"{df_t.shape[0]} rows × {df_t.shape[1]} cols"
#                                 )
#                         except Exception as e:
#                             logger.warning(f"⚠️ Failed to convert PDF table {idx}: {e}")
#
#                     merged_pdf = self._merge_dataframes(dfs, source_label="PDF tables")
#                     if not merged_pdf.empty:
#                         return merged_pdf
#
#                 logger.info("ℹ️ PDF has no tables — falling back to text extraction")
#
#             except Exception as e:
#                 logger.error(f"❌ PDF table extraction failed: {e}")
#
#             # ------------------------------------------------------------
#             # 2) Try TEXT extraction (machine-readable PDFs)
#             # ------------------------------------------------------------
#             text = ""
#             try:
#                 text = extract_pdf_text(file_path)
#                 if text and text.strip():
#                     logger.info("📝 Returning PDF text as DataFrame")
#                     return pd.DataFrame({"text": [text]})
#             except Exception as e:
#                 logger.error(f"❌ PDF text extraction failed: {e}")
#
#             # ------------------------------------------------------------
#             # 3) Try FIELD extraction (H‑1B receipts, USCIS notices)
#             # ------------------------------------------------------------
#             try:
#                 if text:
#                     fields = extract_pdf_fields(text)
#                     if fields:
#                         logger.info("📑 Returning structured PDF fields as DataFrame")
#                         return pd.DataFrame([fields])
#             except Exception as e:
#                 logger.error(f"❌ PDF field extraction failed: {e}")
#
#             # ------------------------------------------------------------
#             # 4) OCR fallback (scanned PDFs like H‑1B receipts)
#             # ------------------------------------------------------------
#             try:
#                 logger.info("🔍 Running OCR fallback for scanned PDF")
#                 ocr_text = ocr_pdf_to_text(file_path)
#
#                 if ocr_text and ocr_text.strip():
#                     logger.info("📝 OCR extracted text — returning as DataFrame")
#                     return pd.DataFrame({"text": [ocr_text]})
#
#                 # Try field extraction on OCR text
#                 fields = extract_pdf_fields(ocr_text)
#                 if fields:
#                     logger.info("📑 Returning OCR‑extracted structured fields")
#                     return pd.DataFrame([fields])
#
#             except Exception as e:
#                 logger.error(f"❌ OCR failed: {e}")
#
#             # ------------------------------------------------------------
#             # Nothing usable
#             # ------------------------------------------------------------
#             logger.warning("⚠️ PDF contained no tables, no text, and OCR failed")
#             return pd.DataFrame()
#
#         # ============================================================
#         # 2) PARSED TABLES → merge ALL tables
#         # ============================================================
#         if parsed and isinstance(parsed, dict):
#             tables = parsed.get("tables") or parsed.get("table")
#             if tables:
#                 if not isinstance(tables, list):
#                     tables = [tables]
#
#                 dfs: List[pd.DataFrame] = []
#
#                 for idx, t in enumerate(tables, start=1):
#                     if not isinstance(t, dict):
#                         continue
#
#                     try:
#                         if "data" in t:
#                             df_t = pd.DataFrame(t["data"])
#                         elif "rows" in t and "columns" in t:
#                             df_t = pd.DataFrame(t["rows"], columns=t["columns"])
#                         else:
#                             continue
#
#                         if not df_t.empty:
#                             dfs.append(df_t)
#                             logger.info(
#                                 f"📦 Parsed table {idx}: "
#                                 f"{df_t.shape[0]} rows × {df_t.shape[1]} cols"
#                             )
#                     except Exception as e:
#                         logger.warning(f"⚠️ Failed to convert parsed table {idx}: {e}")
#
#                 merged_parsed = self._merge_dataframes(dfs, source_label="parsed tables")
#                 if not merged_parsed.empty:
#                     return merged_parsed
#
#         # ============================================================
#         # 3) STANDARD FILE TYPES (CSV/TSV/Excel) WITH FALLBACK
#         # ============================================================
#         try:
#             if lower.endswith(".csv"):
#                 logger.info("📥 Loading CSV via universal loader")
#                 return self._read_csv_with_fallback(file_path)
#
#             if lower.endswith(".tsv"):
#                 logger.info("📥 Loading TSV via universal loader")
#                 return pd.read_csv(file_path, sep="\t", on_bad_lines="skip")
#
#             if lower.endswith((".xlsx", ".xls")):
#                 logger.info("📥 Loading Excel via universal loader")
#                 return pd.read_excel(file_path)
#
#             # Unknown extension → best-effort CSV
#             logger.info("📥 Unknown extension — attempting CSV with fallback")
#             return self._read_csv_with_fallback(file_path)
#
#         except Exception as e:
#             logger.error(f"❌ Failed to read dataset file: {e}")
#             return pd.DataFrame()
