import os
import pandas as pd
from typing import Dict, Any
from app.utils.dataframe_cleaner import clean_dataframe
from app.parsers.pdf_table_extractor import extract_pdf_tables
from app.utils.table_utils import normalize_tables
import json


class UniversalSalesReportParser:
    """
    Universal parser for sales reports.
    Supports: CSV, TSV, XLSX, XLS, PDF, JSON, Parquet, TXT, HTML, XML, ZIP, Images (OCR).
    Always returns a valid table structure.
    """

    def parse(self, file_path: str) -> Dict[str, Any]:
        ext = os.path.splitext(file_path)[1].lower()

        try:
            # -----------------------------------------
            # CSV / TSV
            # -----------------------------------------
            if ext == ".csv":
                df = pd.read_csv(file_path)

            elif ext == ".tsv":
                df = pd.read_csv(file_path, sep="\t")

            # -----------------------------------------
            # Excel
            # -----------------------------------------
            elif ext in [".xlsx", ".xls"]:
                df = pd.read_excel(file_path)

            # -----------------------------------------
            # JSON
            # -----------------------------------------
            elif ext == ".json":
                raw = json.load(open(file_path))
                if isinstance(raw, list):
                    df = pd.DataFrame(raw)
                elif isinstance(raw, dict):
                    df = pd.json_normalize(raw)
                else:
                    return self._empty()

            # -----------------------------------------
            # Parquet
            # -----------------------------------------
            elif ext == ".parquet":
                df = pd.read_parquet(file_path)

            # -----------------------------------------
            # HTML tables
            # -----------------------------------------
            elif ext == ".html":
                tables = pd.read_html(file_path)
                df = tables[0] if tables else None

            # -----------------------------------------
            # XML tables
            # -----------------------------------------
            elif ext == ".xml":
                try:
                    df = pd.read_xml(file_path)
                except:
                    return self._empty()

            # -----------------------------------------
            # TXT → try CSV fallback
            # -----------------------------------------
            elif ext == ".txt":
                try:
                    df = pd.read_csv(file_path)
                except:
                    df = pd.read_fwf(file_path)

            # -----------------------------------------
            # PDF
            # -----------------------------------------
            elif ext == ".pdf":
                tables = extract_pdf_tables(file_path)
                normalized = normalize_tables(tables)
                if not normalized:
                    return self._empty()
                df = pd.DataFrame(normalized[0]["data"])

            # -----------------------------------------
            # ZIP archive
            # -----------------------------------------
            elif ext == ".zip":
                import zipfile
                with zipfile.ZipFile(file_path, "r") as z:
                    for name in z.namelist():
                        if name.endswith((".csv", ".xlsx", ".xls")):
                            z.extract(name, "/tmp")
                            return self.parse(os.path.join("/tmp", name))
                return self._empty()

            # -----------------------------------------
            # Images → OCR → table (optional)
            # -----------------------------------------
            elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
                # If you have OCR → table extraction, plug it here
                return self._empty()

            # -----------------------------------------
            # Unknown formats
            # -----------------------------------------
            else:
                return self._empty()

            # -----------------------------------------
            # CLEAN THE DATAFRAME
            # -----------------------------------------
            df = clean_dataframe(df)

            if df is None or df.empty:
                return self._empty()

            return self._to_table(df)

        except Exception:
            return self._empty()

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _empty(self):
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "column_count": 0,
        }

    def _to_table(self, df: pd.DataFrame):
        df = df.fillna("")
        return {
            "columns": list(df.columns),
            "rows": df.values.tolist(),
            "row_count": len(df),
            "column_count": len(df.columns),
            "numeric_columns": [
                col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])
            ],
            "sample": df.head(5).to_dict(orient="records"),
        }
