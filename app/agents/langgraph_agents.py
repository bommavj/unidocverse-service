# ============================================================
# MUST BE FIRST — BEFORE ANY layoutparser / paddleocr imports
# ============================================================
import os

os.environ["USE_PADDLEX"] = "0"
os.environ["PADDLEOCR_USE_PADDLEX"] = "0"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "true"
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_allocator_strategy"] = "naive_best_fit"

# At the top of the file, add:
import calendar
import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable, Dict, Optional

import ollama
from sqlalchemy import text

from app.agents.prompt_roles import ROLE_BY_TYPE
from app.agents.state import DocumentState
from app.config.model_factory import EmbeddingModelFactory
from app.core import config
from app.services.parsers.docling_parser import DoclingParser
from app.services.parsers.universal_parser import UniversalParser
from app.services.pdf_extractor import PDFExtractor

logger = logging.getLogger(__name__)

# Initialize services
pdf_extractor = PDFExtractor()

embedding_model = EmbeddingModelFactory.get_model()

ollama_client = ollama.Client(timeout=60)  # 60s hard cap — prevents pipeline hangs on slow LLM calls

SummaryFn = Callable[[Dict], str]
KeyPointsFn = Callable[[Dict], list]


# ============================================================================
# AGENT NODES (LangGraph functions)
# ============================================================================

def ingest_node(state: DocumentState) -> Dict[str, Any]:
    """Node 1: Ingest and validate file"""
    logger.info(f"🤖 [IngestNode] Processing: {state['filename']}")

    try:
        file_path = Path(state["file_path"])

        if not file_path.exists():
            return {
                "errors": [{
                    "node": "ingest",
                    "error": f"File not found: {state['file_path']}",
                    "timestamp": datetime.utcnow().isoformat()
                }],
                "processing_complete": True
            }

        # Get file metadata
        file_size = file_path.stat().st_size
        file_ext = file_path.suffix.lower()

        needs_ocr = False

        logger.info(f"✅ [IngestNode] Ingested {file_size} bytes")

        # Read file
        with open(file_path, "rb") as f:
            content = f.read()

        logger.info(f"✅ [IngestNode] Ingested {len(content)} bytes")

        file_path = state.get("file_path")
        filename = state.get("filename", "")

        fmt = ""
        if filename and '.' in filename:
            fmt = filename.rsplit('.', 1)[-1].lower()

        return {
            "file_size": file_size,
            "file_type": file_ext,
            "needs_ocr": needs_ocr,
            "format": fmt,
            "doc_metadata": {
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "ingestion_time": datetime.utcnow().isoformat()
            }
        }

    except Exception as e:
        logger.error(f"❌ [IngestNode] Error: {e}")
        return {
            "errors": [{
                "node": "ingest",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }]
        }


def _parse_fast(file_path: str, fmt: str) -> dict:
    """
    Fast-path parser for tabular and data formats.
    Bypasses Docling entirely — uses pandas / stdlib.

    Returns the same shape as DoclingParser.parse():
        {text, tables, sheets, metadata}
    """
    import json
    from pathlib import Path

    path = Path(file_path)

    try:
        # ── CSV / TSV ────────────────────────────────────────────
        if fmt in ("csv", "tsv"):
            import pandas as pd

            sep = "\t" if fmt == "tsv" else ","
            # Sniff large files — only read first 10k rows for preview
            df = pd.read_csv(path, sep=sep, low_memory=False, nrows=10_000)
            total_rows = sum(1 for _ in open(path, encoding="utf-8", errors="ignore")) - 1

            table = {
                "name": path.stem,
                "columns": list(df.columns),
                "data": df.head(500).values.tolist(),  # cap at 500 rows for state
                "rows": total_rows,
            }

            # Text preview for LLM (first 20 rows as pipe-delimited)
            preview_lines = [" | ".join(str(c) for c in df.columns)]
            for _, row in df.head(20).iterrows():
                preview_lines.append(" | ".join(str(v) for v in row))
            text = (
                    f"CSV file: {path.name}\n"
                    f"Total rows: {total_rows:,} | Columns: {len(df.columns)}\n\n"
                    + "\n".join(preview_lines)
            )

            return {
                "text": text,
                "tables": [table],
                "sheets": [table],
                "metadata": {
                    "format": fmt,
                    "total_rows": total_rows,
                    "columns": list(df.columns),
                    "column_count": len(df.columns),
                    "dtypes": {col: str(dt) for col, dt in df.dtypes.items()},
                    "file_size": path.stat().st_size,
                    "fast_parse": True,
                }
            }

        # ── XLSX / XLS ───────────────────────────────────────────
        elif fmt in ("xlsx", "xls", "xlsm", "xlsb"):
            import pandas as pd

            xl = pd.ExcelFile(path)
            tables = []
            sheets = []
            text_parts = [f"Excel file: {path.name} | Sheets: {len(xl.sheet_names)}"]

            for sheet_name in xl.sheet_names:
                df = xl.parse(sheet_name, nrows=10_000)
                # Convert date/datetime columns to ISO strings so they're JSON-serializable
                for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
                    df[col] = df[col].dt.strftime("%Y-%m-%d").where(df[col].notna(), other=None)
                table = {
                    "name": sheet_name,
                    "columns": list(df.columns),
                    "data": df.head(500).where(df.head(500).notna(), other=None).values.tolist(),
                    "rows": len(df),
                }
                tables.append(table)
                sheets.append(table)

                preview = [f"\nSheet: {sheet_name} ({len(df)} rows × {len(df.columns)} cols)"]
                preview.append(" | ".join(str(c) for c in df.columns))
                for _, row in df.head(5).iterrows():
                    preview.append(" | ".join(str(v) for v in row))
                text_parts.extend(preview)

            return {
                "text": "\n".join(text_parts),
                "tables": tables,
                "sheets": sheets,
                "metadata": {
                    "format": fmt,
                    "sheet_names": xl.sheet_names,
                    "sheet_count": len(xl.sheet_names),
                    "file_size": path.stat().st_size,
                    "fast_parse": True,
                }
            }

        # ── JSON ─────────────────────────────────────────────────
        elif fmt == "json":
            with open(path, encoding="utf-8", errors="ignore") as f:
                data = json.load(f)

            # If it's a list of dicts, treat as a table
            tables = []
            if isinstance(data, list) and data and isinstance(data[0], dict):
                import pandas as pd
                df = pd.DataFrame(data[:10_000])
                table = {
                    "name": path.stem,
                    "columns": list(df.columns),
                    "data": df.head(500).values.tolist(),
                    "rows": len(data),
                }
                tables = [table]

            text = (
                    f"JSON file: {path.name}\n"
                    f"Type: {type(data).__name__} | "
                    f"{'Records: ' + str(len(data)) if isinstance(data, list) else 'Keys: ' + str(list(data.keys())[:10])}\n\n"
                    + json.dumps(data, indent=2)[:3000]
            )

            return {
                "text": text,
                "tables": tables,
                "sheets": tables,
                "metadata": {
                    "format": fmt,
                    "data_type": type(data).__name__,
                    "file_size": path.stat().st_size,
                    "fast_parse": True,
                }
            }

        # ── TXT / PLAIN TEXT ─────────────────────────────────────
        elif fmt == "txt":
            text = path.read_text(encoding="utf-8", errors="ignore")
            return {
                "text": text[:50_000],  # cap at 50k chars
                "tables": [],
                "sheets": [],
                "metadata": {
                    "format": fmt,
                    "char_count": len(text),
                    "file_size": path.stat().st_size,
                    "fast_parse": True,
                }
            }

        # ── MARKDOWN / XML / YAML / HTML / JSONL ─────────────────
        elif fmt in ("md", "markdown", "xml", "yaml", "yml", "html", "htm",
                     "mhtml", "jsonl", "rst", "ini", "cfg", "toml", "sql", "log"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            return {
                "text": text[:50_000],
                "tables": [],
                "sheets": [],
                "metadata": {"format": fmt, "char_count": len(text), "fast_parse": True}
            }

        # ── PARQUET / AVRO ────────────────────────────────────────
        elif fmt in ("parquet", "avro"):
            try:
                import pyarrow.parquet as pq
                table = pq.read_table(file_path)
                df_pq = table.to_pandas()
                text = (
                    f"Parquet file: {path.name}\n"
                    f"Rows: {len(df_pq):,} | Columns: {len(df_pq.columns)}\n\n"
                    + df_pq.head(20).to_string(index=False)
                )
                tbl = {
                    "name": path.stem,
                    "columns": list(df_pq.columns),
                    "data": df_pq.head(500).values.tolist(),
                    "rows": len(df_pq),
                }
            except Exception as _pq_err:
                logger.warning(f"Parquet read failed: {_pq_err}")
                text = ""
                tbl = {}
            return {
                "text": text,
                "tables": [tbl] if tbl else [],
                "sheets": [tbl] if tbl else [],
                "metadata": {"format": fmt, "fast_parse": True}
            }

        # ── EML / MSG / MBOX / RTF — delegate to ocr_any ─────────
        elif fmt in ("eml", "msg", "mbox", "rtf", "epub", "svg",
                     "zip", "tar", "gz"):
            from app.services.ocr.ocr_utils import ocr_any
            text = ocr_any(file_path)
            return {
                "text": text[:50_000],
                "tables": [],
                "sheets": [],
                "metadata": {"format": fmt, "char_count": len(text), "fast_parse": True}
            }

    except Exception as e:
        logger.error(f"❌ [_parse_fast] {fmt} parse failed: {e}", exc_info=True)
        # Fall back to an empty result — parse_node will handle it
        return {
            "text": "",
            "tables": [],
            "sheets": [],
            "metadata": {"format": fmt, "error": str(e), "fast_parse": True}
        }

    # Fallback for any unhandled fmt — delegate to ocr_any rather than silently returning empty
    try:
        from app.services.ocr.ocr_utils import ocr_any
        text = ocr_any(file_path)
        if text:
            return {"text": text[:50_000], "tables": [], "sheets": [], "metadata": {"format": fmt}}
    except Exception:
        pass
    return {"text": "", "tables": [], "sheets": [], "metadata": {"format": fmt}}


def normalize_dates_in_text(text: str) -> str:
    # Convert YYYY-MM-DD → "June 14 2023"
    # FIXED:
    def repl(match):
        y, m, d = match.group(1), match.group(2), match.group(3)
        try:
            month_int = int(m)
            if not 1 <= month_int <= 12:
                return match.group(0)  # return original if out of range
            month_name = calendar.month_name[month_int]
            return f"{month_name} {int(d)}, {y}"
        except (ValueError, IndexError):
            return match.group(0)  # return original on any error

    return re.sub(r"(\d{4})-(\d{2})-(\d{2})", repl, text)


def parse_node(state: DocumentState) -> Dict[str, Any]:
    import pandas as pd
    import os

    reprocess_count = state.get("reprocess_count", 0)
    _t0_parse = time.perf_counter()
    logger.info(f"🤖 [ParseNode] Parsing document (attempt {reprocess_count + 1})")

    try:
        from app.services.document_registry import DOCUMENT_REGISTRY
        from app.utils.json_utils import make_json_serializable
        from app.utils.table_utils import normalize_table

        file_path = state.get("file_path", "")
        filename = state.get("filename", "")
        fmt = state.get("file_type", "")

        if not fmt and filename and '.' in filename:
            fmt = filename.rsplit('.', 1)[-1].lower()

        # -------------------------------
        # Stage 1: Universal parsing
        # -------------------------------
        _FAST_FORMATS = {
            "csv", "tsv", "xlsx", "xls", "xlsm", "xlsb", "json", "txt",
            "md", "markdown", "xml", "yaml", "yml", "html", "htm", "mhtml",
            "jsonl", "rst", "ini", "cfg", "toml", "sql", "log",
            "parquet", "avro",
            "eml", "msg", "mbox", "rtf", "epub", "svg", "zip", "tar", "gz",
        }
        fmt_clean = fmt.lstrip(".")

        if fmt_clean in _FAST_FORMATS:
            parsed_data = _parse_fast(file_path, fmt_clean)
        else:
            parser = DoclingParser()
            parsed_data = parser.parse(file_path, filename)

        text = parsed_data.get("text", "")
        logger.info(f"🔍 Docling raw text (first 300): {repr(text[:300])}")
        raw_tables = parsed_data.get("tables", []) or parsed_data.get("sheets", [])
        parse_metadata = parsed_data.get("metadata", {})

        # ── Pre-classification garbled text check ─────────────────────────────
        # Must run BEFORE classification so the LLM gets clean text.
        # CrawfordTech/mainframe PDFs produce <20% alphanumeric chars.
        def _is_garbled(t: str) -> bool:
            if not t or len(t) < 20: return False
            import re as _re
            # &lt; is the MRZ filler character '<' encoded by Docling — NOT garble.
            # Remove MRZ-filler entities before counting so passports don't false-fire.
            t_no_mrz = _re.sub(r'&lt;', '', t)
            # Strip remaining HTML entities and tags
            clean = _re.sub(r'&[a-z]+;|&#\d+;|<!--.*?-->', ' ', t_no_mrz)
            clean = _re.sub(r'<[^>]+>', ' ', clean)
            real = sum(1 for c in clean if c.isalpha() or c.isdigit())
            ratio = real / max(len(clean), 1)
            # Flag HTML-entity–heavy text (mainframe / CrawfordTech PDFs) but
            # only if the entities are NOT the MRZ filler we already stripped.
            entity_count = len(_re.findall(r'&[a-z]+;', t_no_mrz))
            entity_ratio = entity_count * 5 / max(len(t_no_mrz), 1)
            return ratio < 0.25 or entity_ratio > 0.15

        if fmt_clean == "pdf" and _is_garbled(text[:500]):
            logger.warning("⚠️ Garbled text detected — running OCR before classification")
            try:
                from app.services.ocr.ocr_utils import ocr_pdf_best_effort
                ocr_text = ocr_pdf_best_effort(file_path)
                # Only replace Docling's extraction if OCR actually got more text.
                # Never discard a longer Docling result for a shorter OCR result.
                if ocr_text and len(ocr_text) > len(text):
                    text = ocr_text
                    parsed_data["text"] = ocr_text
                    logger.info(f"✅ Pre-classification OCR: {len(ocr_text)} chars (replaced Docling)")
                elif ocr_text:
                    logger.info(f"⏭️ Pre-classification OCR ({len(ocr_text)} chars) shorter than Docling ({len(text)}) — keeping Docling")
                else:
                    logger.warning("⚠️ OCR returned no text")
            except Exception as _ocr_pre_err:
                logger.error(f"❌ Pre-classification OCR failed: {_ocr_pre_err}")

        # -------------------------------
        # Stage 2: Classification
        # -------------------------------
        classify_state = {
            "format": fmt,
            "parsed": {
                "text": text,
                "tables": raw_tables,
                "sheets": raw_tables,
                "metadata": parse_metadata,
                "type": parsed_data.get("type", "unknown")
            },
            "file_path": file_path,
            "filename": filename
        }

        classified_state = classify_node(classify_state)
        classification = classified_state.get("classification", {})
        doc_type = classification.get("type", "unknown")

        # -------------------------------
        # Stage 3: Specialized parser
        # -------------------------------
        registry_entry = DOCUMENT_REGISTRY.get(doc_type, {})
        parser_cls = registry_entry.get("parser")
        used_specialized_parser = False

        if parser_cls and parser_cls != UniversalParser:
            try:
                specialized_parser = parser_cls()
                specialized_data = specialized_parser.parse(file_path)

                if isinstance(specialized_data, dict):
                    text = specialized_data.get("text", text)
                    raw_tables = (
                            specialized_data.get("tables")
                            or specialized_data.get("sheets")
                            or raw_tables
                    )
                    parse_metadata = specialized_data.get("metadata", parse_metadata)

                used_specialized_parser = True
            except Exception as e:
                logger.error(f"❌ Specialized parser failed: {e}", exc_info=True)

        # -------------------------------
        # Stage 4: Normalize tables
        # -------------------------------
        normalized_tables = []
        for raw_table in raw_tables:
            if isinstance(raw_table, dict):
                try:
                    normalized = normalize_table(raw_table)
                    if normalized:
                        normalized_tables.append(normalized)
                except Exception as e:
                    logger.error(f"❌ Table normalization failed: {e}")

        normalized_tables = make_json_serializable(normalized_tables)

        # ---------------------------------------------------------
        # ⭐ LandingAI-style OCR fallback (platform-aware)
        # ---------------------------------------------------------
        def is_fake_table(tables):
            if not tables:
                return True
            try:
                df = pd.DataFrame(tables[0].get("data", []), columns=tables[0].get("columns", []))
                nan_ratio = df.isna().sum().sum() / max(1, (df.shape[0] * df.shape[1]))
                default_cols = all(c in ["0", "1", "2", "3"] for c in df.columns.astype(str))
                if df.empty or (df.shape[1] <= 3 and nan_ratio > 0.60) or default_cols:
                    return True
                return False
            except Exception:
                return True

        def has_image_only_content(file_path: str) -> bool:
            import fitz
            doc = fitz.open(file_path)
            for page in doc:
                text = page.get_text().strip()
                images = page.get_images(full=True)
                if images and len(text) < 300:
                    return True
            return False

        OCR_ENABLED = os.getenv("ENABLE_OCR") == "true"

        def has_any_images(fp: str) -> bool:
            try:
                import fitz
                doc = fitz.open(fp)
                return any(page.get_images(full=True) for page in doc)
            except Exception:
                return False

        def _is_garbled(t: str) -> bool:
            """Detect CrawfordTech/custom-font encoded PDFs that produce garbage text.
            Uses letter+digit ratio — garbled text has <20% real characters.
            MRZ filler &lt; (passport zone) is stripped first so passports don't false-fire."""
            if not t or len(t) < 20:
                return False
            import re as _re2
            t_clean = _re2.sub(r'&lt;', '', t)  # strip MRZ filler before ratio check
            real_chars = sum(1 for c in t_clean if c.isalpha() or c.isdigit())
            return (real_chars / max(len(t_clean), 1)) < 0.2

        text_is_garbled = _is_garbled(text[:500] if text else "")
        if text_is_garbled:
            logger.warning("⚠️ Garbled text detected (custom font encoding) — forcing Tesseract OCR")

        def _docling_text_sufficient(fp: str, extracted_text: str) -> bool:
            """Return True if Docling already got enough text per page to skip OCR."""
            try:
                import fitz
                page_count = len(fitz.open(fp))
                if page_count == 0:
                    return False
                chars_per_page = len(extracted_text) / page_count
                if chars_per_page > 1500:
                    logger.info(f"⏭️ OCR skipped — Docling has {chars_per_page:.0f} chars/page (threshold: 1500)")
                    return True
            except Exception:
                pass
            return False

        text_sufficient = not text_is_garbled and _docling_text_sufficient(file_path, text)

        if (OCR_ENABLED and has_any_images(file_path) and not text_sufficient) or text_is_garbled:
            logger.info("🧠 OCR triggered — extracting text from rendered page images")
            try:
                from app.services.ocr.ocr_utils import ocr_pdf_best_effort
                ocr_text = ocr_pdf_best_effort(file_path)
            except Exception as _ocr_err:
                logger.error(f"❌ All OCR failed: {_ocr_err}")
                ocr_text = ""

            if ocr_text:
                if text_is_garbled:
                    # Replace garbled text entirely — don't merge garbage with good OCR
                    text = ocr_text
                    state["parsed"]["text"] = ocr_text
                    logger.info(f"✅ Replaced garbled text with OCR ({len(ocr_text)} chars)")

                else:
                    # Merge — Docling has clean text layer, OCR has image content
                    text = text + "\n\n" + ocr_text
                    logger.info(f"✅ Merged: Docling({len(text)} chars) + OCR({len(ocr_text)} chars)")

            #normalized_tables = []

        # -------------------------------
        # Stage 5: Generate text from tables (preview only — keeps LLM context manageable)
        # -------------------------------
        if normalized_tables:
            table_texts = []
            for table in normalized_tables:
                cols = table.get("columns", [])
                data = table.get("data", [])[:20]  # max 20 rows for text representation
                df = pd.DataFrame(data, columns=cols)
                for col in df.columns:
                    df[col] = df[col].apply(lambda v: f"{col}: {v}")
                table_texts.append(df.to_string(index=False))

            full_table_text = "\n\n".join(table_texts)
            text = (text or "") + "\n\n" + full_table_text

        # -------------------------------
        # Stage 6: Save into state
        # -------------------------------
        text = normalize_dates_in_text(text)

        state["raw_text"] = text
        state["clean_text"] = " ".join(text.split())
        state["doc_metadata"] = {
            "tables": normalized_tables,
            "parse_info": parse_metadata,
            "used_specialized_parser": used_specialized_parser,
            "parser_class": parser_cls.__name__ if parser_cls else "UniversalParser",
            "table_count": len(normalized_tables),
            "text_length": len(text)
        }
        state["classification"] = classification
        state["format"] = fmt
        state["reprocess_count"] = reprocess_count + 1

        logger.info(f"⏱️ [ParseNode] done in {time.perf_counter() - _t0_parse:.1f}s")
        return state

    except Exception as e:
        logger.error(f"❌ [ParseNode] Critical error: {e}", exc_info=True)
        state["raw_text"] = ""
        state["clean_text"] = ""
        state["doc_metadata"] = {"tables": [], "parse_info": {}, "error": str(e)}
        state["classification"] = {"type": "unknown", "confidence": 0.0, "method": "error"}
        state["errors"] = state.get("errors", []) + [{
            "node": "parse",
            "error": str(e),
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }]
        state["reprocess_count"] = reprocess_count + 1
        return state

        # def parse_node(state: DocumentState) -> Dict[str, Any]:
        #     """
        #     Generic parse node that handles all document types.
        #     """
        #     import pandas as pd  # ⭐ REQUIRED for table → CSV conversion
        #
        #     reprocess_count = state.get("reprocess_count", 0)
        #     logger.info(f"🤖 [ParseNode] Parsing document (attempt {reprocess_count + 1})")
        #
        #     try:
        #         from app.services.document_registry import DOCUMENT_REGISTRY
        #         from app.utils.json_utils import make_json_serializable
        #         from app.utils.table_utils import normalize_table
        #
        #         file_path = state.get("file_path", "")
        #         filename = state.get("filename", "")
        #         fmt = state.get("file_type", "")
        #
        #         if not fmt and filename and '.' in filename:
        #             fmt = filename.rsplit('.', 1)[-1].lower()
        #
        #         logger.info(f"📄 Parsing: {filename}")
        #         logger.info(f"📋 Format: {fmt}")
        #         logger.info(f"📂 Path: {file_path}")
        #
        #         logger.info("🧩 Stage 1: Universal parsing")
        #
        #         _FAST_FORMATS = {"csv", "tsv", "xlsx", "xls", "xlsm", "xlsb", "json", "txt"}
        #         fmt_clean = fmt.lstrip(".")
        #
        #         if fmt_clean in _FAST_FORMATS:
        #             logger.info(f"⚡ Fast-path for {fmt_clean} — skipping Docling")
        #             parsed_data = _parse_fast(file_path, fmt_clean)
        #         else:
        #             parser = DoclingParser()
        #             parsed_data = parser.parse(file_path, filename)
        #
        #         text = parsed_data.get("text", "")
        logger.info(f"🔍 Docling raw text (first 300): {repr(text[:300])}")


#         raw_tables = parsed_data.get("tables", []) or parsed_data.get("sheets", [])
#         parse_metadata = parsed_data.get("metadata", {})
#
#         logger.info(f"✅ Parsed: {len(raw_tables)} table(s), {len(text)} chars of text")
#
#         # -------------------------------
#         # Stage 2: Classification
#         # -------------------------------
#         classify_state = {
#             "format": fmt,
#             "parsed": {
#                 "text": text,
#                 "tables": raw_tables,
#                 "sheets": raw_tables,
#                 "metadata": parse_metadata,
#                 "type": parsed_data.get("type", "unknown")
#             },
#             "file_path": file_path,
#             "filename": filename
#         }
#
#         classified_state = classify_node(classify_state)
#         classification = classified_state.get("classification", {})
#         doc_type = classification.get("type", "unknown")
#         confidence = classification.get("confidence", 0.0)
#         method = classification.get("method", "unknown")
#
#         logger.info(f"✅ Classified: {doc_type} ({confidence:.2f}, method={method})")
#
#         # -------------------------------
#         # Stage 3: Specialized parser
#         # -------------------------------
#         used_specialized_parser = False
#         registry_entry = DOCUMENT_REGISTRY.get(doc_type, {})
#         parser_cls = registry_entry.get("parser")
#
#         if parser_cls and parser_cls != UniversalParser:
#             logger.info(f"🔄 Re-parsing with {parser_cls.__name__}")
#             try:
#                 specialized_parser = parser_cls()
#                 specialized_data = specialized_parser.parse(file_path)
#
#                 if isinstance(specialized_data, dict):
#                     text = specialized_data.get("text", text) or text
#                     raw_tables = (
#                         specialized_data.get("tables")
#                         or specialized_data.get("sheets")
#                         or raw_tables
#                     )
#                     parse_metadata = specialized_data.get("metadata", parse_metadata)
#
#                 used_specialized_parser = True
#                 logger.info(f"✅ Specialized parser: {len(raw_tables)} tables, {len(text)} chars")
#
#             except Exception as e:
#                 logger.error(f"❌ Specialized parser failed: {e}", exc_info=True)
#                 logger.info("🔄 Falling back to universal parser")
#
#         # -------------------------------
#         # Stage 4: Normalize tables
#         # -------------------------------
#         logger.info("📊 Stage 4: Normalizing tables")
#         normalized_tables = []
#
#         for i, raw_table in enumerate(raw_tables):
#             if not isinstance(raw_table, dict):
#                 logger.warning(f"⚠️ Table {i+1} is {type(raw_table)}, skipping")
#                 continue
#
#             try:
#                 normalized = normalize_table(raw_table)
#                 if normalized:
#                     normalized_tables.append(normalized)
#                     logger.info(f"   → Table {i+1}: {len(normalized['columns'])} cols × {len(normalized['data'])} rows")
#             except Exception as e:
#                 logger.error(f"❌ Table {i+1} normalization failed: {e}")
#
#         normalized_tables = make_json_serializable(normalized_tables)
#         parse_metadata = make_json_serializable(parse_metadata)
#
#         logger.info(f"✅ Normalized: {len(normalized_tables)} table(s)")
#
#         # -------------------------------
#         # Stage 5: ALWAYS generate text from tables
#         # -------------------------------
#         if normalized_tables:
#             logger.info("📝 Stage 5: Generating FULL text from tables (forced)")
#
#             table_texts = []
#             for table in normalized_tables:
#                 df = pd.DataFrame(table.get("data", []), columns=table.get("columns", []))
#
#                 # ⭐ CORRECT FIX: inject column names into each cell
#                 for col in df.columns:
#                     df[col] = df[col].apply(lambda v: f"{col}: {v}")
#
#                 table_texts.append(df.to_string(index=False))
#
#             full_table_text = "\n\n".join(table_texts)
#             text = (text or "") + "\n\n" + full_table_text
#
#             logger.info(f"✅ Generated {len(full_table_text)} chars from {len(normalized_tables)} table(s)")
#         else:
#             logger.info("ℹ️ No tables found — using existing text")
#
#         # -------------------------------
#         # Stage 6: Save into state
#         # -------------------------------
#         text = normalize_dates_in_text(text)
#
#         current_metadata = state.get("doc_metadata", {})
#         current_metadata.update({
#             "tables": normalized_tables,
#             "parse_info": parse_metadata,
#             "used_specialized_parser": used_specialized_parser,
#             "parser_class": parser_cls.__name__ if parser_cls else "UniversalParser",
#             "table_count": len(normalized_tables),
#             "text_length": len(text)
#         })
#
#         logger.info("✅ [ParseNode] Complete")
#         logger.info(f"   📊 Tables: {len(normalized_tables)}")
#         logger.info(f"   📝 Text: {len(text)} chars")
#         logger.info(f"   🏷️ Type: {doc_type} ({confidence:.2f})")
#
#         state["raw_text"] = text
#         state["clean_text"] = " ".join(text.split())
#         state["doc_metadata"] = current_metadata
#         state["classification"] = classification
#         state["format"] = fmt
#         state["reprocess_count"] = reprocess_count + 1
#
#         return state
#
#     except Exception as e:
#         logger.error(f"❌ [ParseNode] Critical error: {e}", exc_info=True)
#
#         state["raw_text"] = ""
#         state["clean_text"] = ""
#         state["doc_metadata"] = {
#             "tables": [],
#             "parse_info": {},
#             "error": str(e)
#         }
#         state["classification"] = {
#             "type": "unknown",
#             "confidence": 0.0,
#             "method": "error"
#         }
#         state["errors"] = state.get("errors", []) + [{
#             "node": "parse",
#             "error": str(e),
#             "timestamp": __import__('datetime').datetime.now().isoformat()
#         }]
#         state["reprocess_count"] = reprocess_count + 1
#
#         return state


def cleanup_node(state: DocumentState) -> Dict[str, Any]:
    """Node 3: Clean and normalize text"""
    _t0_cleanup = time.perf_counter()
    logger.info(f"🤖 [CleanupNode] Cleaning text")

    try:
        raw_text = state.get("raw_text", "")

        if not raw_text:
            return {
                "cleaned_text": "",
                "classification": state.get("classification"),
                "format": state.get("format")
            }

        text = raw_text
        original_length = len(text)

        text = text.replace('\x00', '')
        text = re.sub(r' +', ' ', text)
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = re.sub(r'Page \d+(/\d+)?', '', text, flags=re.IGNORECASE)
        text = text.strip()

        logger.info(f"✅ [CleanupNode] Cleaned: {original_length} → {len(text)} chars in {time.perf_counter() - _t0_cleanup:.1f}s")

        current_metadata = state.get("doc_metadata", {})
        current_metadata["original_length"] = original_length
        current_metadata["cleaned_length"] = len(text)

        return {
            "cleaned_text": text,
            "doc_metadata": current_metadata,
            "classification": state.get("classification"),
            "format": state.get("format")
        }

    except Exception as e:
        logger.error(f"❌ [CleanupNode] Error: {e}")
        return {
            "cleaned_text": state.get("raw_text", ""),
            "classification": state.get("classification"),
            "format": state.get("format"),
            "errors": state.get("errors", []) + [{
                "node": "cleanup",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }]
        }


def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic document classification node.
    """
    _t0_classify = time.perf_counter()
    parsed = state.get("parsed", {})
    fmt = state.get("format", "").lower().strip()

    has_sheets = bool(parsed.get("sheets") or parsed.get("tables"))
    has_text = bool(parsed.get("text"))

    classification = {
        "type": "unknown",
        "confidence": 0.0,
        "method": "none",
        "explain": {}
    }

    logger.info(f"🔍 Classifying format={fmt}, has_sheets={has_sheets}, has_text={has_text}")
    results = []

    # ── Format-based pre-classification shortcuts ──────────────────────────
    _fmt_clean = fmt.lstrip(".")

    _IMAGE_FMTS = {"jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp",
                   "heic", "heif"}
    _EMAIL_FMTS = {"eml", "msg", "mbox"}
    _ARCHIVE_FMTS = {"zip", "tar", "gz", "7z", "rar"}
    _DATA_FMTS = {"parquet", "avro", "sqlite", "db"}
    _CODE_FMTS = {"py", "js", "ts", "java", "cpp", "c", "cs", "go", "rb",
                  "sh", "bash", "sql"}

    if _fmt_clean in _IMAGE_FMTS or fmt in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg", ".heic", ".heif"):
        # Try filename-first: a birth cert / passport / ID uploaded as image should be classified correctly
        from app.services.classifiers.document_classifier import DocumentClassifier
        _dc_img = DocumentClassifier()
        _img_parsed = parsed if isinstance(parsed, dict) else {"metadata": {}, "tables": [], "text": ""}
        if isinstance(_img_parsed.get("metadata"), dict) and not _img_parsed["metadata"].get("filename"):
            _img_parsed["metadata"]["filename"] = state.get("filename", "")
        fn_type, fn_conf = _dc_img._classify_by_filename(_img_parsed)
        if fn_conf >= 0.85:
            classification.update({"type": fn_type, "confidence": fn_conf, "reasoning": "filename_priority",
                                    "matched_keywords": [], "rule_scores": {}, "ml_scores": {}})
            logger.info(f"📄 Image-format doc reclassified by filename: {fn_type} ({fn_conf:.2f})")
        else:
            logger.info("📸 Image format detected - using image classification")
            classification.update({"type": "image", "confidence": 0.9, "method": "format_detection"})
        logger.info(f"✅ Final: {classification['type']} ({classification['confidence']:.2f})")
        state["classification"] = classification
        return state

    if _fmt_clean in _EMAIL_FMTS:
        classification.update({"type": "email", "confidence": 0.95, "method": "format_detection"})
        logger.info(f"✉️ Email format {_fmt_clean!r} → type=email")
        state["classification"] = classification
        return state

    if _fmt_clean in _ARCHIVE_FMTS:
        classification.update({"type": "archive", "confidence": 0.95, "method": "format_detection"})
        logger.info(f"📦 Archive format {_fmt_clean!r} → type=archive")
        state["classification"] = classification
        return state

    if _fmt_clean in _DATA_FMTS:
        classification.update({"type": "dataset", "confidence": 0.95, "method": "format_detection"})
        logger.info(f"📊 Data format {_fmt_clean!r} → type=dataset")
        state["classification"] = classification
        return state

    if _fmt_clean in _CODE_FMTS:
        classification.update({"type": "code", "confidence": 0.95, "method": "format_detection"})
        logger.info(f"💻 Code format {_fmt_clean!r} → type=code")
        state["classification"] = classification
        return state

    if has_text:
        try:
            from app.services.classifiers.document_classifier import DocumentClassifier
            dc = DocumentClassifier()
            # Inject original filename so _classify_by_filename can use it
            if isinstance(parsed.get("metadata"), dict) and not parsed["metadata"].get("filename"):
                parsed["metadata"]["filename"] = state.get("filename", "")
            if len(parsed["tables"]) > 0:
                result = dc.classify(parsed["tables"][0], parsed, fmt)
            else:
                result = dc.classify(parsed["tables"], parsed, fmt)
            if result["type"] == "spreadsheet":
                result["confidence"] = 0.9

            results.append(result)
        except Exception as e:
            logger.warning(f"⚠️ Text classifier failed: {e}")

    if has_sheets:
        try:
            sheets = parsed.get("sheets") or parsed.get("tables") or []
            from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
            sc = SpreadsheetClassifier()

            for sheet in sheets:
                result = sc.classify(file_path=state.get("file_path"), parsed=parsed)
                results.append(result)
        except Exception as e:
            logger.warning(f"⚠️ Table classifier failed: {e}")

    valid_results = [r for r in results if r.get("confidence", 0) >= 0.3]
    if valid_results:
        classification.update(max(valid_results, key=lambda x: x["confidence"]))
    else:
        classification.update({"type": "document", "confidence": 0.3, "method": "fallback"})

    logger.info(
        f"✅ Final: {classification['type']} ({classification['confidence']:.2f}) "
        f"in {time.perf_counter() - _t0_classify:.1f}s"
    )

    state["classification"] = classification

    # Persist doc_type immediately so it's visible even if a later node fails.
    document_id = state.get("document_id")
    if document_id and classification.get("type") not in (None, "unknown", "document"):
        try:
            _db = SessionLocal()
            try:
                _db.execute(
                    text("UPDATE documents SET doc_type = :t WHERE id = :id"),
                    {"t": classification["type"], "id": document_id},
                )
                _db.commit()
            finally:
                _db.close()
        except Exception as _e:
            logger.warning(f"⚠️ classify_node: early doc_type write failed: {_e}")

    return state


def extract_clean_or_raw_text(state):
    """
    Deterministic text extraction.
    Priority:
    1. state["clean_text"]
    2. state["raw_text"]
    3. analyzed_data.cleaned_text
    4. analyzed_data.raw_text
    5. parsed.text
    6. summary (last resort)
    """

    # 1. Clean text from ParseNode
    clean = state.get("clean_text")
    if clean:
        return clean

    # 2. Raw text from ParseNode
    raw = state.get("raw_text")
    if raw:
        return raw

    # 3. Analyzer outputs
    analyzed = state.get("analyzed_data", {}) or {}
    if analyzed.get("cleaned_text"):
        return analyzed["cleaned_text"]
    if analyzed.get("raw_text"):
        return analyzed["raw_text"]

    # 4. Parsed text
    parsed = state.get("parsed", {})
    if parsed and parsed.get("text"):
        return parsed["text"]

    # 5. Summary (fallback)
    summary = state.get("summary")
    if summary:
        return summary

    return ""


# def extract_clean_or_raw_text(state):
#     text1 = ""
#     if "analyzed_data" in state:
#         if "raw_text" in state.get("analyzed_data"):
#             text1 = state.get("analyzed_data", "").get("raw_text")
#         elif "cleaned_text" in state.get("analyzed_data"):
#             text1 = state.get("analyzed_data", "").get("cleaned_text")
#     text2 = state.get("cleaned_text") or state.get("raw_text")
#     text = " ".join((text2, text1))
#     return text


# ============================================================================
# COMBINED LLM CALL — replaces _call_summary + _call_key_points + insight_node
# ============================================================================

# def _call_analyze_combined(text: str, doc_type: str) -> dict:
#     """
#     Single phi3:mini call returning summary + key_points + insights.
#     ~8-12 seconds vs the old 25 seconds (3 separate calls).
#     """
#     prompt = f"""Analyze this {doc_type} document. Return ONLY valid JSON, no markdown, no explanation.
#
# Document:
# {text[:6000]}
#
# Return this exact JSON structure:
# {{
#   "summary": "2-3 sentence summary of key facts, numbers, dates",
#   "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
#   "insights": [
#     {{"title": "Insight 1", "description": "brief description", "category": "analysis"}},
#     {{"title": "Insight 2", "description": "brief description", "category": "analysis"}},
#     {{"title": "Insight 3", "description": "brief description", "category": "analysis"}}
#   ]
# }}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={
#             "temperature": 0.1,
#             "num_predict": 800,
#             "num_ctx": 2048,
#         }
#     )
#
#     raw = response["response"].strip()
#
#     # Strip markdown fences if model ignores the instruction
#     if raw.startswith("```"):
#         raw = raw.split("```")[1]
#         if raw.startswith("json"):
#             raw = raw[4:]
#         raw = raw.strip()
#     if raw.endswith("```"):
#         raw = raw[:-3].strip()
#
#     # Fix phi3:mini occasionally emitting malformed keys like "description:" -> "description"
#     raw = re.sub(r'"(\w+):"\s*:', r'"\1":', raw)
#
#     #logger.info(f"🔍 [phi3 raw] {raw[:300]!r}")
#
#     try:
#         result = json.loads(raw)
#     except json.JSONDecodeError:
#         # Truncated JSON — salvage what we can via regex
#         summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
#         kp_matches = []
#         if '"key_points"' in raw:
#             kp_section = raw.split('"key_points"')[1].split('"insights"')[0]
#             kp_matches = re.findall(r'"([^"]{10,})"', kp_section)[:5]
#         result = {
#             "summary": summary_match.group(1) if summary_match else raw[:500],
#             "key_points": kp_matches,
#             "insights": []
#         }
#         logger.warning(
#             f"⚠️ [phi3] JSON truncated — salvaged summary ({len(result['summary'])} chars), {len(kp_matches)} key points")
#
#     return result

def _call_analyze_combined(text: str, doc_type: str, lang: Optional[str] = None) -> dict:
    """
    Single phi3:mini call returning summary + key_points + insights.
    Domain-aware, pattern-anchored, JSON-stable.
    ~8-12 seconds vs 25 seconds (3 separate calls).
    """

    # 1. Persona
    from app.agents.prompt_roles import get_role_for_doc_type
    role = get_role_for_doc_type(doc_type)

    # 2. Patterns
    from app.agents.doc_type_prompts import (
        doc_type_insights_prompt,
        doc_type_summary_prompt,
        GENERIC_INSIGHTS
    )

    kp_patterns = doc_type_insights_prompt.get(doc_type, GENERIC_INSIGHTS)
    summary_patterns = doc_type_summary_prompt.get(doc_type, GENERIC_INSIGHTS)

    # 3. Rotate patterns (first 5 for KP, first 3 for insights, first 5 for summary)
    kp_examples = kp_patterns[:5] if len(kp_patterns) >= 5 else (kp_patterns + GENERIC_INSIGHTS)[:5]
    insight_examples = kp_patterns[:3] if len(kp_patterns) >= 3 else (kp_patterns + GENERIC_INSIGHTS)[:3]
    summary_examples = summary_patterns[:5] if len(summary_patterns) >= 5 else (summary_patterns + GENERIC_INSIGHTS)[:5]

    lang_instruction = ""
    if lang == "es":
        lang_instruction = "CRITICAL: You MUST write the summary paragraphs, key points, and insight titles/descriptions in Spanish. Responde en español."
    elif lang == "fr":
        lang_instruction = "CRITICAL: You MUST write the summary paragraphs, key points, and insight titles/descriptions in French. Réponds en français."
    elif lang == "de":
        lang_instruction = "CRITICAL: You MUST write the summary paragraphs, key points, and insight titles/descriptions in German. Antworte auf Deutsch."

    # 4. Build prompt
    prompt = f"""
{role}

Analyze this {doc_type} document and return a structured summary, key points, and insights.

{lang_instruction}

Document:
{text[:6000]}

Return ONLY valid JSON:
{{
  "summary": [
    "{summary_examples[0]}",
    "{summary_examples[1]}",
    "{summary_examples[2]}",
    "{summary_examples[3]}",
    "{summary_examples[4]}"
  ],
  "key_points": [
    "{kp_examples[0]}",
    "{kp_examples[1]}",
    "{kp_examples[2]}",
    "{kp_examples[3]}",
    "{kp_examples[4]}"
  ],
  "insights": [
    {{"title": "{insight_examples[0]}", "description": "2-3 sentences with specific details", "category": "analysis"}},
    {{"title": "{insight_examples[1]}", "description": "2-3 sentences with specific details", "category": "analysis"}},
    {{"title": "{insight_examples[2]}", "description": "2-3 sentences with specific details", "category": "analysis"}}
  ]
}}
"""

    # 5. LLM call
    response = ollama_client.generate(
        model=config.model,
        prompt=prompt,
        options={
            "temperature": 0.1,
            "num_predict": 800,
            "num_ctx": 2048,
        }
    )

    raw = response["response"].strip()

    # 6. Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    # 7. Fix malformed keys
    raw = re.sub(r'"(\w+):"\s*:', r'"\1":', raw)

    # 8. Parse JSON safely
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # salvage partial output
        summary_match = re.search(r'"summary"\s*:\s*\[(.*?)\]', raw, re.S)
        kp_match = re.search(r'"key_points"\s*:\s*\[(.*?)\]', raw, re.S)

        summary = summary_match.group(1).split("\n") if summary_match else []
        key_points = re.findall(r'"([^"]{5,})"', kp_match.group(1)) if kp_match else []

        return {
            "summary": summary[:5],
            "key_points": key_points[:5],
            "insights": []
        }


def analyze_node(state: DocumentState) -> Dict[str, Any]:
    """
    Node 5: Generate summary, key_points, AND insights in ONE phi3:mini call.
    Replaces the old parallel _call_summary + _call_key_points threads (17s)
    AND the separate insight_node LLM call (10s).
    Total: ~10s instead of ~27s.
    """
    logger.info("🤖 [AnalyzeNode] Analyzing document (single combined LLM call)")

    text = extract_clean_or_raw_text(state)
    classification = state.get("classification", {})
    doc_type = classification.get("type", "document")

    # ← ADD THIS BLOCK
    # If a specialist analyzer already ran and produced a summary, skip LLM
    analyzed_data = state.get("analyzed_data", {}) or {}
    specialist_summary = analyzed_data.get("summary")
    if specialist_summary:
        logger.info(f"⏭️ [AnalyzeNode] Skipping LLM — specialist summary exists for {doc_type}")
        return {
            "summary": specialist_summary,
            "key_points": [],
            "insights": state.get("insights", []),  # preserve specialist insights
        }
    # ← END BLOCK

    if not text:
        return {"summary": "", "key_points": [], "insights": []}

    t0 = time.perf_counter()
    logger.info(f"📊 [AnalyzeNode] Calling {config.model} once for summary+keypoints+insights")

    lang = state.get("language") or "en"

    try:
        result = _call_analyze_combined(text, doc_type, lang=lang)
    except Exception as e:
        logger.error(f"❌ [AnalyzeNode] Combined call failed: {e}")
        result = {"summary": text[:500], "key_points": [], "insights": []}

    elapsed = time.perf_counter() - t0

    summary = result.get("summary", "")
    key_points = result.get("key_points", [])
    insights = result.get("insights", [])

    if not isinstance(key_points, list):
        key_points = []
    if not isinstance(insights, list):
        insights = []

    # Ensure insights have the correct shape
    validated_insights = []
    for ins in insights[:3]:
        if isinstance(ins, dict) and "title" in ins:
            validated_insights.append({
                "title": ins.get("title", ""),
                "description": ins.get("description", ""),
                "category": ins.get("category", "analysis")
            })
        elif isinstance(ins, str) and ins:
            validated_insights.append({
                "title": "Insight",
                "description": ins,
                "category": "analysis"
            })

    if not summary:
        summary = text[:500] + "..." if text else ""

    logger.info(
        f"✅ [AnalyzeNode] Done in {elapsed:.2f}s — "
        f"summary ({len(summary)} chars), "
        f"{len(key_points)} key points, "
        f"{len(validated_insights)} insights"
    )

    return {
        "summary": summary,
        "key_points": key_points[:5],
        "insights": validated_insights,
    }


# def search_prep_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 6: Generate embeddings and keywords"""
#     logger.info(f"🤖 [SearchPrepNode] Generating embeddings")
#
#     try:
#         text = extract_clean_or_raw_text(state)
#
#         if not text or len(text) < 50:
#             logger.warning("⚠️ Text too short for meaningful embedding")
#             return {
#                 "embedding": None,
#                 "keywords": []
#             }
#
#         text_for_embedding = text[:8000]
#         embedding = embedding_model.encode(text_for_embedding)
#
#         logger.info(f"🔍 [SearchPrepNode] Embedding shape: {embedding.shape}")
#         logger.info(f"🔍 [SearchPrepNode] Embedding dtype: {embedding.dtype}")
#
#         stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
#                       'of', 'with', 'is', 'was', 'are', 'were', 'be', 'been', 'being'}
#
#         words = re.findall(r'\b[a-z]{3,}\b', text.lower())
#         words = [w for w in words if w not in stop_words]
#         counter = Counter(words)
#         keywords = [word for word, count in counter.most_common(50)]
#
#         embedding_list = embedding.tolist()
#
#         logger.info(f"✅ [SearchPrepNode] Generated {len(embedding_list)}-dim embedding (768-dim, high quality)")
#         logger.info(f"✅ [SearchPrepNode] Extracted {len(keywords)} keywords")
#
#         return {
#             "embedding": embedding_list,
#             "keywords": keywords
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [SearchPrepNode] Error: {e}")
#         return {
#             "embedding": None,
#             "keywords": [],
#             "errors": [{
#                 "node": "search_prep",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }


from typing import List

import re
from typing import List

def split_into_chunks(
    text: str,
    max_chars: int = 2000,
    overlap: int = 200,
    row_threshold: int = 20,
    comma_threshold: int = 20
) -> List[str]:
    if not text or not text.strip():
        return []

    cleaned = re.sub(r"[ \t]+", " ", text).strip()

    raw_lines = cleaned.split("\n")
    lines = [ln.strip() for ln in raw_lines if ln.strip()]

    newline_count = max(len(raw_lines) - 1, 0)
    comma_count = cleaned.count(",")
    avg_line_len = sum(len(l) for l in lines) / max(len(lines), 1)

    looks_like_table = (
        (newline_count >= row_threshold or comma_count >= comma_threshold)
        and avg_line_len > 15
    )

    chunks = []

    if looks_like_table:
        current = []
        current_len = 0
        row_overlap = 3

        for row in lines:
            row_len = len(row) + 1

            if current and current_len + row_len > max_chars:
                chunk = "\n".join(current).strip()
                if chunk:
                    chunks.append(chunk)

                current = current[-row_overlap:] if row_overlap else []
                current_len = sum(len(r) + 1 for r in current)

            current.append(row)
            current_len += row_len

        if current:
            chunk = "\n".join(current).strip()
            if chunk:
                chunks.append(chunk)

        return chunks

    if len(lines) > 1 and avg_line_len < 20:
        cleaned = " ".join(lines)

    if re.search(r"[.!?]\s+", cleaned):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
        current = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence) + 1

            if current and current_len + sentence_len > max_chars:
                chunk = " ".join(current).strip()
                if chunk:
                    chunks.append(chunk)

                carry = []
                carry_len = 0
                for s in reversed(current):
                    s_len = len(s) + 1
                    if carry_len + s_len <= overlap:
                        carry.insert(0, s)
                        carry_len += s_len
                    else:
                        break

                current = carry
                current_len = len(" ".join(current)) + (1 if current else 0)

            current.append(sentence)
            current_len = len(" ".join(current)) + 1

        if current:
            chunk = " ".join(current).strip()
            if chunk:
                chunks.append(chunk)

        return chunks

    start = 0
    n = len(cleaned)

    step = max(1, max_chars - overlap)

    while start < n:
        end = min(start + max_chars, n)
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start += step

    return chunks


# def split_into_chunks(
#         text: str,
#         max_chars: int = 2000,
#         overlap: int = 200,
#         row_threshold: int = 20,
#         comma_threshold: int = 20
# ) -> list[str]:
#     """
#     Universal chunker for ALL document types.
#     Automatically detects:
#     - spreadsheets / CSV / tables  (long lines + newlines)
#     - prose / paragraphs / contracts
#     - OCR output (short lines, many newlines → treated as prose)
#     """
#
#     # Normalize whitespace but KEEP newlines for table detection
#     cleaned = re.sub(r"[ \t]+", " ", text).strip()
#
#     newline_count = cleaned.count("\n")
#     comma_count = cleaned.count(",")
#     lines = cleaned.split("\n")
#     avg_line_len = sum(len(l) for l in lines) / max(len(lines), 1)
#
#     # ---------------------------------------------------------
#     # 1. TABLE DETECTION
#     # Only treat as table if lines are substantial (avg >20 chars)
#     # OCR output has many short lines (1-3 words) — NOT tables
#     # ---------------------------------------------------------
#     looks_like_table = (
#             (newline_count > row_threshold or comma_count > comma_threshold)
#             and avg_line_len > 20
#     )
#
#     if looks_like_table:
#         rows = cleaned.split("\n")
#         chunks = []
#         current = []
#         ROW_OVERLAP = 3  # carry forward 3 rows, not 200
#
#         for row in rows:
#             current.append(row)
#             if sum(len(r) for r in current) > max_chars:
#                 chunks.append("\n".join(current))
#                 current = current[-ROW_OVERLAP:]
#
#         if current:
#             chunks.append("\n".join(current))
#
#         return chunks
#
#     # ---------------------------------------------------------
#     # 2. PROSE / OCR — join short lines into paragraphs first,
#     # then do sentence-aware chunking
#     # ---------------------------------------------------------
#     # Join OCR single-word lines into flowing text
#     if avg_line_len < 20:
#         cleaned = " ".join(l.strip() for l in lines if l.strip())
#
#     if re.search(r"[.!?]\s", cleaned):
#         sentences = re.split(r"(?<=[.!?])\s+", cleaned)
#         chunks = []
#         current = []
#
#         for sentence in sentences:
#             if len(" ".join(current)) + len(sentence) > max_chars:
#                 chunks.append(" ".join(current))
#                 # carry forward last N sentences for overlap
#                 overlap_chars = 0
#                 carried = []
#                 for s in reversed(current):
#                     if overlap_chars + len(s) < overlap:
#                         carried.insert(0, s)
#                         overlap_chars += len(s)
#                     else:
#                         break
#                 current = carried
#             current.append(sentence)
#
#         if current:
#             chunks.append(" ".join(current))
#
#         return chunks
#
#     # ---------------------------------------------------------
#     # 3. FALLBACK: character-based
#     # ---------------------------------------------------------
#     chunks = []
#     start = 0
#     n = len(cleaned)
#
#     while start < n:
#         logger.info(f"🧩 [ChunkNode Start] {start}")
#         end = min(start + max_chars, n)
#         logger.info(f"🧩 [ChunkNode End] {end}")
#         chunks.append(cleaned[start:end])
#         logger.info(f"🧩 [Chunks] {len(chunks)}")
#         start = end - overlap if overlap else end
#
#     return chunks


def _mask_code_blocks(text: str):
    """
    Replace content inside fenced code blocks (``` ... ```) with blank lines
    so heading regexes don't accidentally match `#` inside code.
    Returns (masked_text, list of (start, original_content)) for restoration.
    """
    import re as _re
    fence_pattern = _re.compile(r'```.*?```', _re.DOTALL)
    replacements = []

    def _replace(m):
        placeholder = '\n' * m.group(0).count('\n')
        replacements.append((m.start(), m.group(0)))
        return placeholder

    masked = fence_pattern.sub(_replace, text)
    return masked, replacements


def _split_into_sections(text: str) -> List[tuple]:
    """
    Universal section splitter for ALL document types.

    Returns list of (section_index, section_label, section_text).

    Detection priority:
      1. ## headings only (not ### which stays as body) — Docling emits these
         for PDF, DOCX, PPTX, XLSX, HTML, images. Code blocks are masked first
         so `#` inside ``` fences is never mistaken for a heading.
      2. Sheet: <name> markers — fast-path Excel/CSV parser
      3. --- Page N --- markers — PDFExtractor fallback path
      4. Whole document as one section (TXT, JSON, plain blobs)
    """
    import re as _re

    # --- Markdown ## heading split (code-block safe) ---
    masked, _ = _mask_code_blocks(text)
    # Only split on ## (H2) — H1 is document title, H3+ stays as body content
    heading_pattern = _re.compile(r'^#{2}\s+(.+)$', _re.MULTILINE)
    heading_matches = list(heading_pattern.finditer(masked))

    if heading_matches:
        sections = []
        # Capture any preamble before first ## as an intro section
        preamble = text[:heading_matches[0].start()].strip()
        if preamble:
            sections.append((0, "Introduction", preamble))

        for idx, match in enumerate(heading_matches):
            label = match.group(1).strip()
            start = match.end()
            end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(text)
            # Use original text (not masked) so code blocks are preserved in chunks
            content = text[start:end].strip()
            if content:
                sections.append((idx + 1, label, content))

        if sections:
            return sections

    # --- Sheet: <name> split (Excel / CSV fast-path) ---
    sheet_pattern = _re.compile(r'^Sheet:\s*(.+)$', _re.MULTILINE | _re.IGNORECASE)
    sheet_matches = list(sheet_pattern.finditer(text))
    if sheet_matches:
        sections = []
        for idx, match in enumerate(sheet_matches):
            label = match.group(1).strip()
            start = match.end()
            end = sheet_matches[idx + 1].start() if idx + 1 < len(sheet_matches) else len(text)
            content = text[start:end].strip()
            if content:
                sections.append((idx + 1, f"Sheet: {label}", content))
        if sections:
            return sections

    # --- Page N markers (PDFExtractor fallback path) ---
    page_pattern = _re.compile(r'---\s*Page\s+(\d+)\s*---', _re.IGNORECASE)
    page_parts = page_pattern.split(text)
    if len(page_parts) > 1:
        sections = []
        i = 1
        while i < len(page_parts) - 1:
            page_num = int(page_parts[i])
            content = page_parts[i + 1].strip()
            if content:
                sections.append((page_num, f"Page {page_num}", content))
            i += 2
        if sections:
            return sections

    # --- Fallback: whole document as one section ---
    return [(1, "Document", text.strip())]


def _build_context_chunk(section_label: str, document_id: str, chunk_text: str) -> str:
    """Prepend section context to a chunk so the LLM sees provenance at retrieval time."""
    return f"## {section_label} | Document {document_id}\n\n{chunk_text}"


_CLASSIFY_PROMPT = """Classify this document. Return ONLY valid JSON, no explanation.

{
  "doc_subtype": "<pay_stub|bank_statement|invoice|contract|annual_report|quarterly_report|tax_form|insurance_eob|medical_record|legal|real_estate|immigration|utility_bill|expense_report|other>",
  "issuer": "<company or organization name, or null>",
  "recipient": "<person or company name, or null>"
}

Only classify what is clearly visible. Use null for anything uncertain.

Document header:
{header}
"""

# ── Deterministic fact extractors (run across ALL chunk text) ─────────────────

_MONTH_NUM = {
    'january':'01','february':'02','march':'03','april':'04','may':'05','june':'06',
    'july':'07','august':'08','september':'09','october':'10','november':'11','december':'12'
}

# Matches: $3,436.00 · $3,436 · 3436.00 · 3436 (plain int) · 3,436
# NOTE: Docling space-separated format ("$3 436 00") is normalised to
# "$3,436.00" by _normalize_numeric_spaces() at ingestion time, so this
# regex only needs to handle standard comma/dot separated or plain integers.
_AMOUNT_VALUE = r'[\$\£\€]?\s*([\d]{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+\.\d{1,2}|\d+)'

# Loaded once from research_hints.yaml — no hardcoded patterns in Python
_HINTS_PATH_AGENTS = Path(__file__).parent.parent / "services" / "research_hints.yaml"

def _load_scan_patterns():
    try:
        import yaml
        with open(_HINTS_PATH_AGENTS) as f:
            h = yaml.safe_load(f)
        date_pats       = [(e["pattern"], e["field"]) for e in h.get("date_patterns", [])]
        amount_pats     = [(e["pattern"], e["field"]) for e in h.get("amount_patterns", [])]
        identifier_pats = [(e["pattern"], e["field"]) for e in h.get("identifier_patterns", [])]
        entity_pats     = [(e["pattern"], e["field"]) for e in h.get("entity_patterns", [])]
        return date_pats, amount_pats, identifier_pats, entity_pats
    except Exception as e:
        logger.warning(f"Could not load research_hints.yaml patterns: {e}")
        return [], [], [], []

_PERIOD_LABELS, _AMOUNT_LABELS, _IDENTIFIER_LABELS, _ENTITY_LABELS = _load_scan_patterns()


def _parse_any_date(text: str) -> tuple:
    """
    Try to parse a date string in any common format.
    Returns (yyyy_str, mm_str) or (None, None).
    """
    # YYYY-MM-DD or YYYY/MM/DD (check first so 4-digit year isn't misread as day/month)
    m = re.match(r'^(20\d{2})[/\-](\d{1,2})[/\-]\d{1,2}$', text.strip())
    if m:
        return m.group(1), m.group(2).zfill(2)
    # YYYY-MM (no day — metadata period format)
    m = re.match(r'^(20\d{2})[/\-](\d{2})$', text.strip())
    if m:
        return m.group(1), m.group(2)
    # DD/MM/YYYY or MM/DD/YYYY — disambiguate by value range
    m = re.match(r'^(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})$', text.strip())
    if m:
        a, b, yyyy = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12:
            # a must be day, b must be month
            month = b
        else:
            # Assume MDY (most common in US payroll docs); a is month
            month = a
        return yyyy, str(month).zfill(2)
    # "May 2026" or "May 11, 2026"
    m = re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(?:\d{1,2},?\s+)?(20\d{2})$',
                 text.strip(), re.IGNORECASE)
    if m:
        return m.group(2), _MONTH_NUM[m.group(1).lower()]
    return None, None


def _scan_facts(all_chunk_text: str) -> Dict:
    """
    Scan ALL chunk text with deterministic patterns.
    No LLM involved — zero hallucination risk.
    Captures dates and amounts wherever they appear in the document.
    """
    facts: Dict = {}

    # ── Date extraction ───────────────────────────────────────────────────────
    # For each labeled date field, look for "Label: <date>" on the same or next line
    date_val_pat = (
        r'[\s:]+'
        r'(\d{1,2}[/\-]\d{1,2}[/\-]20\d{2}'          # MM/DD/YYYY or DD/MM/YYYY
        r'|20\d{2}[/\-]\d{1,2}[/\-]\d{1,2}'           # YYYY-MM-DD
        r'|20\d{2}[/\-]\d{2}'                          # YYYY-MM
        r'|(?:January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(?:\d{1,2},?\s+)?20\d{2})'               # "May 29, 2026" or "May 2026"
    )
    for label_pat, field_name in _PERIOD_LABELS:
        if field_name in facts:
            continue
        m = re.search(label_pat + date_val_pat, all_chunk_text, re.IGNORECASE)
        if m:
            yyyy, mm = _parse_any_date(m.group(1))
            if yyyy and mm:
                facts[field_name] = f"{yyyy}-{mm}"

    # ── Determine canonical period from extracted dates ────────────────────────
    # Priority: pay_date > period_end > period_start > due_date
    for key in ('pay_date', 'period_end', 'period_start', 'due_date'):
        if key in facts:
            period = facts[key]  # already "YYYY-MM"
            yyyy, mm = period.split('-')
            facts['pay_period']       = period
            facts['pay_period_month'] = datetime(int(yyyy), int(mm), 1).strftime('%B').lower()
            facts['pay_period_year']  = yyyy
            break

    # Fallback: bare 4-digit year scan (annual docs, tax forms)
    if 'pay_period' not in facts:
        year_m = re.search(r'\b(20\d{2})\b', all_chunk_text)
        if year_m:
            facts['pay_period_year'] = year_m.group(1)

    # ── Amount extraction ─────────────────────────────────────────────────────
    key_amounts: Dict = {}
    for label_pat, field_name in _AMOUNT_LABELS:
        if field_name in key_amounts:
            continue
        m = re.search(label_pat + _AMOUNT_VALUE, all_chunk_text, re.IGNORECASE)
        if m:
            try:
                raw = m.group(1).replace(',', '').strip()
                val = float(raw)
                if val > 0:
                    key_amounts[field_name] = val
            except ValueError:
                pass

    if key_amounts:
        facts['key_amounts'] = key_amounts

    # ── Identifier extraction ─────────────────────────────────────────────────
    identifiers: Dict = {}
    _id_val_pat = r'([\w\-\/]{3,40})'
    for label_pat, field_name in _IDENTIFIER_LABELS:
        if field_name in identifiers:
            continue
        m = re.search(label_pat + _id_val_pat, all_chunk_text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                identifiers[field_name] = val

    if identifiers:
        facts['identifiers'] = identifiers

    # ── Entity / party extraction ─────────────────────────────────────────────
    entities: Dict = {}
    _entity_val_pat = r'(.{2,60}?)(?:\n|$)'
    for label_pat, field_name in _ENTITY_LABELS:
        if field_name in entities:
            continue
        m = re.search(label_pat + _entity_val_pat, all_chunk_text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and len(val) >= 2:
                entities[field_name] = val

    if entities:
        facts['entities'] = entities

    return facts


def _extract_doc_metadata(all_chunk_text: str) -> Dict:
    """
    Two-pass metadata extraction:
      Pass 1 — LLM classifies doc type from first 300 chars (header only).
               Never asked for facts → no hallucination.
      Pass 2 — Deterministic regex scans ALL chunk text for dates and amounts.
               Covers full document regardless of length.
    Results merged into one doc_metadata dict stored once at ingest time.
    """
    import json

    meta: Dict = {}

    # ── Pass 1: LLM classification (header only, 300 chars) ──────────────────
    header = all_chunk_text[:300].strip()
    if header:
        try:
            prompt = _CLASSIFY_PROMPT.replace("{header}", header)
            resp = ollama.chat(
                model=config.model,
                messages=[{"role": "user", "content": prompt}],
                options={"num_predict": 120, "temperature": 0.0},
            )
            raw = resp["message"]["content"].strip()
            raw = re.sub(r"```[a-z]*\s*", "", raw).strip("`").strip()
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start != -1 and end > start:
                classified = json.loads(raw[start:end])
                meta.update({k: v for k, v in classified.items() if v})
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}")

    # ── Pass 2: Deterministic fact extraction (full document) ─────────────────
    facts = _scan_facts(all_chunk_text)
    meta.update(facts)

    logger.info(f"Extracted metadata: subtype={meta.get('doc_subtype')} period={meta.get('pay_period')} amounts={list(meta.get('key_amounts', {}).keys())}")
    return meta


def chunk_and_embed_node(state: DocumentState) -> Dict[str, Any]:
    _t0_chunk_total = time.perf_counter()
    logger.info("🧩 [ChunkNode] Chunking + embedding (section-aware Markdown)")

    from app.core.database import SessionLocal
    from app.models.document import DocumentChunk

    document_id = state["document_id"]

    # Delete any existing chunks first — reprocess must not create duplicates
    _del_db = SessionLocal()
    try:
        _del_db.execute(
            text("DELETE FROM document_chunks WHERE document_id = :id"),
            {"id": document_id}
        )
        _del_db.commit()
    except Exception as _del_err:
        logger.warning(f"⚠️ Could not clear old chunks for {document_id}: {_del_err}")
    finally:
        _del_db.close()

    doc_text = extract_clean_or_raw_text(state)

    if not doc_text:
        logger.warning("⚠️ [ChunkNode] No text available for chunking")
        # Flag as low-quality so the user knows this document is unsearchable
        try:
            from app.core.database import SessionLocal as _SL_flag
            _db_flag = _SL_flag()
            _db_flag.execute(
                text("UPDATE documents SET doc_metadata = jsonb_set(COALESCE(doc_metadata,'{}')::jsonb, '{ocr_quality}', '\"empty\"') WHERE id = :id"),
                {"id": document_id}
            )
            _db_flag.commit()
            _db_flag.close()
        except Exception:
            pass
        return {"chunk_count": 0, "chunks": []}

    # Flag low-text documents so users know search quality may be poor
    _text_len = len(doc_text.strip())
    if _text_len < 100:
        logger.warning(f"⚠️ [ChunkNode] Very short text ({_text_len} chars) — OCR may have failed")
        try:
            from app.core.database import SessionLocal as _SL_flag2
            _db_flag2 = _SL_flag2()
            _db_flag2.execute(
                text("UPDATE documents SET doc_metadata = jsonb_set(COALESCE(doc_metadata,'{}')::jsonb, '{ocr_quality}', '\"low\"') WHERE id = :id"),
                {"id": document_id}
            )
            _db_flag2.commit()
            _db_flag2.close()
        except Exception:
            pass

    # Update stage: chunking
    try:
        from app.core.database import SessionLocal as _SL_stage
        _db_stage = _SL_stage()
        _db_stage.execute(
            text("UPDATE documents SET processing_stage = 'chunking' WHERE id = :id"),
            {"id": document_id}
        )
        _db_stage.commit()
        _db_stage.close()
    except Exception:
        pass

    # Decode literal \uXXXX escape sequences emitted by some PDF parsers (e.g. Docling)
    # e.g. "F/uni004FLS/uni004FM" → "FOLSOM", "Am/uni006Funt" → "Amount"
    import re as _re_chunk
    if '\\u' in doc_text or '/uni' in doc_text:
        doc_text = _re_chunk.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), doc_text)
        doc_text = _re_chunk.sub(r'/uni([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), doc_text)

    # Split into logical sections (works for ALL file types)
    sections = _split_into_sections(doc_text)
    logger.info(f"📑 [ChunkNode] {len(sections)} sections detected")

    # Sub-chunk each section, prefix every chunk with section context
    chunk_records: List[tuple] = []  # (section_index, section_label, raw_chunk, md_chunk)
    for sec_idx, sec_label, sec_text in sections:
        sub_chunks = split_into_chunks(sec_text)
        for sub in sub_chunks:
            md = _build_context_chunk(sec_label, document_id, sub)
            chunk_records.append((sec_idx, sec_label, sub, md))

    logger.info(f"🧩 [ChunkNode] {len(chunk_records)} total chunks across {len(sections)} sections")
    _t_after_split = time.perf_counter()
    logger.info(f"⏱️ [ChunkNode] text-splitting done in {_t_after_split - _t0_chunk_total:.1f}s")

    # Update stage: embedding
    try:
        from app.core.database import SessionLocal as _SL_emb
        _db_emb = _SL_emb()
        _db_emb.execute(
            text("UPDATE documents SET processing_stage = 'embedding' WHERE id = :id"),
            {"id": document_id}
        )
        _db_emb.commit()
        _db_emb.close()
    except Exception:
        pass

    # Embed the UNREDACTED text — preserves retrieval quality (entity names match queries)
    _t0_embed = time.perf_counter()
    texts_to_embed = [md for _, _, _, md in chunk_records]
    embeddings = embedding_model.encode(texts_to_embed)
    logger.info(f"⏱️ [ChunkNode] embedding {len(texts_to_embed)} chunks done in {time.perf_counter() - _t0_embed:.1f}s")

    from app.core.pii_redactor import redact_text
    from app.core.tenant import DEFAULT_TENANT_ID

    db = SessionLocal()
    try:
        # Resolve tenant_id from the document row (ContextVar not available in background threads)
        _doc_row = db.execute(
            text("SELECT tenant_id FROM documents WHERE id = :id"),
            {"id": document_id}
        ).fetchone()
        _tenant_id = (_doc_row.tenant_id if _doc_row and _doc_row.tenant_id else None) or DEFAULT_TENANT_ID

        for i, ((sec_idx, sec_label, raw_chunk, md_chunk), chunk_embedding) in enumerate(
            zip(chunk_records, embeddings)
        ):
            # Redact PII from the stored text — this is what the LLM sees at retrieval time.
            # The embedding uses the original so similarity search still works correctly.
            redacted_chunk = redact_text(md_chunk)

            db.add(DocumentChunk(
                document_id=document_id,
                chunk_index=i,
                chunk_text=redacted_chunk,
                page_number=sec_idx,
                chunk_metadata={"section_label": sec_label},  # raw_chunk excluded — never surfaced
                embedding=chunk_embedding.tolist(),
                tenant_id=_tenant_id
            ))

        _t0_dbwrite = time.perf_counter()
        db.commit()

        # Searchable-first: chunks are in, so the document can be searched now.
        # Summaries/insights/facts keep running and are filled in afterwards.
        if chunk_records:
            db.execute(text("""
                UPDATE documents
                   SET status = 'completed', processing_stage = 'enriching',
                       raw_text = COALESCE(raw_text, :raw)
                 WHERE id = :id AND status = 'processing'
            """), {"id": document_id, "raw": redact_text(doc_text.replace("\x00", ""))})
            db.commit()
            logger.info(f"🔎 Document {document_id} searchable — enrichment continues in background")
        logger.info(
            f"✅ Stored {len(chunk_records)} chunks for document {document_id} "
            f"(DB write {time.perf_counter() - _t0_dbwrite:.1f}s, "
            f"total chunk+embed {time.perf_counter() - _t0_chunk_total:.1f}s)"
        )

        # Extract structured date metadata from all chunks and persist to doc_metadata
        all_text = "\n".join(raw for _, _, raw, _ in chunk_records)
        structured = _extract_doc_metadata(all_text)
        if structured:
            doc = db.execute(
                text("SELECT doc_metadata FROM documents WHERE id = :id"),
                {"id": document_id}
            ).fetchone()
            existing = (doc.doc_metadata or {}) if doc else {}
            merged = {**existing, **structured}
            db.execute(
                text("UPDATE documents SET doc_metadata = :m WHERE id = :id"),
                {"m": json.dumps(merged), "id": document_id}
            )
            db.commit()
            logger.info(f"📅 Extracted doc metadata for {document_id}: {structured}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Failed to store chunks: {e}")
        raise

    finally:
        db.close()

    return {
        "chunk_count": len(chunk_records),
        "chunks": [md for _, _, _, md in chunk_records],
    }


def search_prep_node(state: DocumentState) -> Dict[str, Any]:
    """
    SearchPrepNode:
    - Extracts clean text
    - Generates keywords
    - DOES NOT generate document embeddings
    """
    logger.info("🤖 [SearchPrepNode] Preparing search metadata")

    try:
        text = extract_clean_or_raw_text(state)

        if not text or len(text) < 50:
            logger.warning("⚠️ Text too short for keyword extraction")
            return {"keywords": []}

        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'is', 'was', 'are', 'were', 'be', 'been', 'being'
        }

        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        words = [w for w in words if w not in stop_words]

        counter = Counter(words)
        keywords = [word for word, count in counter.most_common(50)]

        logger.info(f"✅ [SearchPrepNode] Extracted {len(keywords)} keywords")

        return {"keywords": keywords}

    except Exception as e:
        logger.error(f"❌ [SearchPrepNode] Error: {e}")
        return {
            "keywords": [],
            "errors": [{
                "node": "search_prep",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }]
        }


# def search_prep_node(state: DocumentState) -> Dict[str, Any]:
#     """
#     SearchPrepNode:
#     - Extracts clean text
#     - Generates keywords
#     - DOES NOT generate document embeddings
#       (document embeddings are built later from chunk embeddings)
#     """
#     logger.info("🤖 [SearchPrepNode] Preparing search metadata")
#
#     try:
#         text = extract_clean_or_raw_text(state)
#
#         if not text or len(text) < 50:
#             logger.warning("⚠️ Text too short for keyword extraction")
#             return {"keywords": []}
#
#         stop_words = {
#             'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
#             'of', 'with', 'is', 'was', 'are', 'were', 'be', 'been', 'being'
#         }
#
#         words = re.findall(r'\b[a-z]{3,}\b', text.lower())
#         words = [w for w in words if w not in stop_words]
#
#         counter = Counter(words)
#         keywords = [word for word, count in counter.most_common(50)]
#
#         logger.info(f"✅ [SearchPrepNode] Extracted {len(keywords)} keywords")
#
#         return {"keywords": keywords}
#
#     except Exception as e:
#         logger.error(f"❌ [SearchPrepNode] Error: {e}")
#         return {
#             "keywords": [],
#             "errors": [{
#                 "node": "search_prep",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }


def quality_check_node(state: DocumentState) -> Dict[str, Any]:
    """Node: Check document quality"""
    logger.info(f"🤖 [QualityCheckNode] Checking quality")

    try:
        text = state.get("cleaned_text", "")
        doc_metadata = state.get("doc_metadata", {})

        quality_issues = []

        if len(text) < 100:
            quality_issues.append({
                "issue": "Text too short",
                "severity": "warning",
                "details": f"Only {len(text)} characters extracted"
            })

        classification = state.get("classification", {})
        if not classification or classification.get("confidence", 0) < 0.3:
            quality_issues.append({
                "issue": "Low classification confidence",
                "severity": "warning",
                "details": f"Confidence: {classification.get('confidence', 0):.2%}"
            })

        errors = state.get("errors", [])
        if errors:
            quality_issues.append({
                "issue": "Parsing errors detected",
                "severity": "critical",
                "details": f"{len(errors)} errors found"
            })

        logger.info(f"✅ [QualityCheckNode] Found {len(quality_issues)} quality issues")

        return {
            "quality_issues": quality_issues
        }

    except Exception as e:
        logger.error(f"❌ [QualityCheckNode] Error: {e}")
        return {
            "quality_issues": [{
                "issue": "Quality check failed",
                "severity": "warning",
                "details": str(e)
            }]
        }


# def insight_node(state: DocumentState) -> Dict[str, Any]:
#     """
#     Node 8: Insights already generated in analyze_node — zero LLM cost.
#     This is now a no-op passthrough.
#     """
#     existing_insights = state.get("insights", [])
#
#     if existing_insights:
#         logger.info(f"✅ [InsightNode] Using {len(existing_insights)} insights from AnalyzeNode (no LLM call)")
#         return {"insights": existing_insights}
#
#     # Fallback: build a minimal insight from summary without any LLM call
#     summary = state.get("summary", "")
#     classification = state.get("classification", {})
#     doc_type = classification.get("type", "document")
#
#     if summary:
#         fallback = [{
#             "title": f"{doc_type.replace('_', ' ').title()} Analysis",
#             "description": summary[:200],
#             "category": "analysis"
#         }]
#         logger.info("✅ [InsightNode] Generated fallback insight from summary (no LLM call)")
#         return {"insights": fallback}
#
#     logger.info("⏭️ [InsightNode] No summary available, returning empty insights")
#     return {"insights": []}


# def insight_node(state: DocumentState) -> Dict[str, Any]:
#     # Prefer specialist analyzer insights over LLM insights
#     analyzed_data = state.get("analyzed_data", {}) or {}
#     specialist_insights = []
#     for alert in analyzed_data.get("alerts", []):
#         specialist_insights.append({
#             "title": alert.get("title"),
#             "description": alert.get("description"),
#             "category": "alert",
#             "action": alert.get("action"),
#             "icon": alert.get("icon"),
#         })
#     for insight in analyzed_data.get("insights", []):
#         specialist_insights.append({
#             "title": insight.get("title"),
#             "description": insight.get("description"),
#             "category": insight.get("type", "info"),
#             "action": insight.get("action"),
#             "icon": insight.get("icon"),
#         })
#     if specialist_insights:
#         logger.info(f"✅ [InsightNode] Using {len(specialist_insights)} specialist insights")
#         return {"insights": specialist_insights}
#
#     # Fall back to LLM insights from AnalyzeNode
#     existing_insights = state.get("insights", [])
#     if existing_insights:
#         logger.info(f"✅ [InsightNode] Using {len(existing_insights)} insights from AnalyzeNode")
#         return {"insights": existing_insights}
#
#     summary = state.get("summary", "")
#     classification = state.get("classification", {})
#     doc_type = classification.get("type", "document")
#     if summary:
#         return {"insights": [{
#             "title": f"{doc_type.replace('_', ' ').title()} Analysis",
#             "description": summary[:200],
#             "category": "analysis"
#         }]}
#     return {"insights": []}


def insight_node(state: DocumentState) -> Dict[str, Any]:
    analyzed_data = state.get("analyzed_data", {}) or {}

    def normalize_insight(insight, default_category="info"):
        # String insight
        if isinstance(insight, str):
            return {
                "title": insight,
                "description": "",
                "category": default_category,
                "action": None,
                "icon": "💡",
            }

        # Dict insight
        if isinstance(insight, dict):
            return {
                "title": insight.get("title", "Insight"),
                "description": insight.get("description", ""),
                "category": insight.get("type", default_category),
                "action": insight.get("action"),
                "icon": insight.get("icon", "💡"),
            }

        # Unexpected type (None, int, list, etc.)
        return {
            "title": str(insight),
            "description": "",
            "category": default_category,
            "action": None,
            "icon": "💡",
        }

    # -----------------------------
    # 1. Specialist insights first
    # -----------------------------
    specialist_insights = []

    # Alerts (always dicts, but normalize anyway)
    for alert in analyzed_data.get("alerts", []):
        specialist_insights.append(normalize_insight(alert, default_category="alert"))

    # Insights (may be dicts or strings)
    for insight in analyzed_data.get("insights", []):
        specialist_insights.append(normalize_insight(insight, default_category="info"))

    if specialist_insights:
        logger.info(f"✅ [InsightNode] Using {len(specialist_insights)} specialist insights")
        return {"insights": specialist_insights}

    # -----------------------------
    # 2. Fallback to LLM insights
    # -----------------------------
    existing_insights = state.get("insights", [])
    if existing_insights:
        normalized = [normalize_insight(i) for i in existing_insights]
        logger.info(f"✅ [InsightNode] Using {len(normalized)} insights from AnalyzeNode")
        return {"insights": normalized}

    # -----------------------------
    # 3. Fallback to summary → insight
    # -----------------------------
    summary = state.get("summary", "")
    classification = state.get("classification", {})
    doc_type = classification.get("type", "document")

    if summary:
        return {"insights": [{
            "title": f"{doc_type.replace('_', ' ').title()} Analysis",
            "description": summary[:200],
            "category": "analysis",
            "icon": "💡",
        }]}

    return {"insights": []}


def metrics_node(state: DocumentState) -> Dict[str, Any]:
    """Node 9: Calculate metrics"""
    logger.info(f"🤖 [MetricsNode] Calculating metrics")

    try:
        text = extract_clean_or_raw_text(state)

        tables = state.get("tables", [])
        classification = state.get("classification", {})

        metrics = {}

        if text:
            word_count = len(text.split())
            metrics["word_count"] = word_count
            metrics["char_count"] = len(text)
            metrics["estimated_read_time"] = f"{word_count // 200} min"

        metrics["tables_found"] = len(tables)

        if classification:
            metrics["document_type"] = classification.get("type", "Unknown")
            metrics["classification_confidence"] = classification.get("confidence", 0)

        metrics["processing_complete"] = True
        metrics["has_errors"] = len(state.get("errors", [])) > 0

        logger.info(f"✅ [MetricsNode] Calculated {len(metrics)} metrics")

        return {"metrics": metrics}

    except Exception as e:
        logger.error(f"❌ [MetricsNode] Error: {e}")
        return {
            "metrics": {},
            "errors": [{
                "node": "metrics",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }]
        }


def sanitize_text(text: str) -> str:
    """Normalize unicode characters to ASCII-safe equivalents."""
    if not text:
        return text
    import unicodedata
    replacements = {
        '\u2019': "'", '\u2018': "'",
        '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '--',
        '\u2026': '...',
        '\u00a0': ' ',
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)

    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')


import numpy as np


def normalize_embedding(value):
    if value is None:
        return None

    if isinstance(value, list):
        return value

    if isinstance(value, (tuple, np.ndarray)):
        return list(value)

    if isinstance(value, str):
        try:
            return json.loads(value)
        except:
            pass

    try:
        return json.loads(str(value))
    except:
        return None


def build_document_embedding(document_id: str, db):
    """
    Build a document-level embedding by pooling all chunk embeddings.
    """
    sql = text("""
        SELECT embedding
        FROM document_chunks
        WHERE document_id = :doc_id
          AND embedding IS NOT NULL
        ORDER BY chunk_index ASC
    """)

    rows = db.execute(sql, {"doc_id": document_id}).fetchall()
    chunk_embeddings = [
        normalize_embedding(row.embedding)
        for row in rows
        if normalize_embedding(row.embedding) is not None
    ]

    if not chunk_embeddings:
        logger.warning(f"⚠️ No chunk embeddings found for document {document_id}")
        return None

    import numpy as np
    pooled = np.mean(np.array(chunk_embeddings, dtype=float), axis=0)
    logger.info(f"📘 Built document embedding from {len(chunk_embeddings)} chunks")

    return pooled.tolist()


def finalize_node(state: DocumentState) -> Dict[str, Any]:
    logger.info("🤖 [FinalizeNode] Saving to database")

    # Short-circuit: password-protected — just mark status and return
    if state.get("password_required"):
        try:
            from app.core.database import SessionLocal
            from app.models.document import Document
            db = SessionLocal()
            doc = db.query(Document).filter(Document.id == state.get("document_id")).first()
            if doc:
                doc.status = "password_required"
                doc.doc_metadata = {"errors": ["password_required"], "password_required": True}
                db.commit()
            db.close()
        except Exception as e:
            logger.error(f"❌ [FinalizeNode] Failed to set password_required status: {e}")
        logger.warning(f"🔒 [FinalizeNode] Saved as password_required")
        return {**state, "processing_complete": True}

    try:
        from app.core.database import SessionLocal
        from app.models.document import Document
        from datetime import datetime
        from app.utils.json_utils import clean_metadata_for_json, validate_json_serializable
        from app.utils.file_classifier import detect_file_capabilities

        document_id = state.get("document_id")
        if not document_id:
            raise Exception("document_id missing from state")

        db = SessionLocal()
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            db.close()
            raise Exception(f"Document {document_id} not found")

        classification = state.get("classification", {})
        insights = state.get("insights", [])
        key_points = state.get("key_points", [])
        keywords = state.get("keywords", [])
        metrics = state.get("metrics", {})
        quality_issues = state.get("quality_issues", [])
        errors = state.get("errors", [])
        analyzed_data = state.get("analyzed_data", {})

        doc_metadata_from_state = state.get("doc_metadata", {})
        tables = (
                doc_metadata_from_state.get("tables")
                or analyzed_data.get("structured_data", {}).get("tables")
                or []
        )

        filename = state.get("filename", "")
        doc_type = classification.get("type", "unknown")

        # ── Safe string conversion ────────────────────────────────────────────
        def _to_str(val):
            if isinstance(val, list):
                return " ".join(str(v) for v in val)
            if val is None:
                return ""
            return str(val)

        raw_text = _to_str(state.get("cleaned_text", state.get("raw_text", "")))
        summary = _to_str(state.get("summary", ""))

        capabilities = detect_file_capabilities(
            filename=filename,
            doc_type=doc_type,
            metadata=doc_metadata_from_state,
            raw_text=raw_text
        )

        complete_metadata = {
            "classification": classification,
            "insights": insights,
            "key_points": key_points,
            "keywords": keywords,
            "metrics": metrics,
            "quality_issues": quality_issues,
            "errors": errors,
            "analyzed_data": analyzed_data,
            "capabilities": capabilities.to_dict(),
            "analytics": analyzed_data if analyzed_data.get("type") == "dataset" else None,
        }

        complete_metadata = clean_metadata_for_json(complete_metadata)
        if not validate_json_serializable(complete_metadata):
            raise Exception("Metadata is not JSON serializable")

        doc.raw_text = sanitize_text(raw_text)
        doc.summary = sanitize_text(summary)
        doc.doc_type = doc_type
        doc.status = "completed"
        doc.processed_at = datetime.utcnow()
        doc.doc_metadata = complete_metadata

        # ── Build document embedding ──────────────────────────────────────────
        try:
            doc_embedding = build_document_embedding(document_id, db)
            if doc_embedding:
                doc.embedding = [float(x) for x in doc_embedding]
                logger.info(f"🔵 [FinalizeNode] Document embedding saved ({len(doc_embedding)} dims)")
            else:
                logger.warning("⚠️ [FinalizeNode] No document embedding generated")
        except Exception as e:
            logger.error(f"❌ [FinalizeNode] Failed to build document embedding: {e}")

        db.commit()
        db.refresh(doc)
        # ⚠️  DO NOT call db.close() here — AMS workflow needs the session

        logger.info("✅ [FinalizeNode] Document saved successfully!")
        db.close()  # ← close AFTER AMS is done

        state["status"] = "completed"
        return state

    except Exception as e:
        logger.error(f"❌ [FinalizeNode] Error: {e}", exc_info=True)
        return {
            "errors": state.get("errors", []) + [{"node": "finalize", "error": str(e)}],
            "status": "failed"
        }


def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
    """
    Node: Document-Specific Analysis
    Performs specialized analysis based on document type with guardrails.
    """
    _t0_analysis = time.perf_counter()
    logger.info("📊 [DocumentAnalysisNode] Starting document-specific analysis...")

    try:
        classification = state.get("classification", {})
        doc_type = classification.get("type", "unknown")
        logger.info(f"Document classfied as :: {doc_type}")
        confidence = classification.get("confidence", 0.0)

        file_path = state.get("file_path", "")
        raw_text = extract_clean_or_raw_text(state)
        metadata = state.get("doc_metadata", {})

        logger.info(f"   📄 Document Type: {doc_type} (confidence: {confidence:.2f})")
        logger.info(f"   📁 File Path: {file_path}")
        logger.info(f"   📝 Text Length: {len(raw_text)} characters")

        from app.services.analyzer_registry import get_analyzer, DOCUMENT_REGISTRY

        def analyzer_is_eligible(doc_type: str, text: str) -> bool:
            t = text.lower()

            RULES = {
                "invoice": ["invoice", "bill to", "amount due", "subtotal", "invoice number", "payment terms"],
                "bank_statement": ["statement", "account", "balance", "transaction", "deposits", "withdrawals"],
                "payroll": ["gross pay", "net pay", "deductions", "pay period", "ytd earnings", "pay stub"],
                "utility_bill": ["billing period", "usage", "meter", "kwh", "therms", "account number",
                                 "service address"],
                "purchase_order": ["purchase order", "po number", "vendor", "ship to", "quantity", "unit price"],
                "receipt": ["receipt", "total", "payment method", "subtotal", "cashier", "transaction id"],
                "tax_form": ["w-2", "1099", "1040", "irs", "federal income tax", "wages tips", "ein"],
                "contract": ["agreement", "party", "terms", "hereby", "whereas", "obligations", "governing law"],
                "resume": ["experience", "education", "skills", "work history", "references", "objective"],
                "transcript": ["course", "credits", "gpa", "semester", "grade", "cumulative", "academic record"],
                # Mortgage subtypes — ordered most specific first
                "loan_estimate": ["loan estimate", "projected payments", "save this loan estimate",
                                  "calculating cash to close", "services you cannot shop for",
                                  "services you can shop for", "origination charges"],
                "closing_disclosure": ["closing disclosure", "cash to close", "summaries of transactions",
                                       "due from borrower at closing", "confirm receipt"],
                "mortgage_statement": ["loan statement", "principal balance", "escrow balance",
                                       "interest paid year to date", "taxes paid year to date",
                                       "payment due date", "next payment"],
                "mortgage_document": ["deed of trust", "promissory note", "mortgagor", "mortgagee",
                                      "lien", "foreclosure"],
                "mortgage": ["mortgage", "loan amount", "interest rate", "monthly payment",
                             "amortization", "borrower"],
                # Insurance — tightened, removed "premium" and "effective date" (too generic)
                "insurance_policy": ["policy number", "policyholder", "named insured", "beneficiary",
                                     "binder", "declarations page", "coverage period", "policy effective"],
                "insurance_claim": ["claim number", "loss date", "adjuster", "claimant", "date of loss"],
                "insurance_eob": ["explanation of benefits", "eob", "allowed amount", "member responsibility"],
                # Medical
                "medical_record": ["patient", "diagnosis", "treatment", "physician", "medical history"],
                "prescription": ["rx", "prescription", "dosage", "refill", "pharmacy", "dispense"],
                "lab_report": ["lab results", "reference range", "specimen", "hematology", "cbc"],
                # Legal
                "contract": ["agreement", "party", "terms", "hereby", "whereas", "obligations"],
                "lease_agreement": ["lease", "tenant", "landlord", "rent", "security deposit", "premises"],
                "nda": ["confidential", "non-disclosure", "proprietary", "trade secret"],
                # HR
                "resume": ["experience", "education", "skills", "work history", "references"],
                "offer_letter": ["offer of employment", "start date", "compensation", "position", "at-will"],
                "pay_stub": ["gross pay", "net pay", "deductions", "pay period", "ytd"],
                "newsletter": [
                    "newsletter",
                    "community association",
                    "hoa",
                    "annual meeting",
                    "board election",
                    "candidate night",
                    "homeowner",
                    "russell ranch",
                    "calendar of events",
                    "landscape update",
                    "security update"
                ]

            }

            if doc_type not in RULES:
                return True

            hits = sum(1 for k in RULES[doc_type] if k in t)
            return hits >= 1

        if confidence >= 0.80:
            logger.info(f"🛂 Guardrail bypassed: high-confidence {doc_type}")
        else:
            if not analyzer_is_eligible(doc_type, raw_text):
                logger.warning(f"🚧 Guardrail: doc_type '{doc_type}' rejected — insufficient structural signals")
                doc_type = "document"

        analyzer = get_analyzer(doc_type, ollama_client)
        logger.info(f"   🔍 Using analyzer: {analyzer.__class__.__name__}")

        analysis_results = analyzer.analyze(
            file_path=file_path,
            text=raw_text,
            metadata={
                **metadata,
                "classified_type": doc_type,
                "classification_confidence": confidence,
                "file_name": state.get("filename", ""),
                "file_type": state.get("format", "")
            }
        )

        logger.info(f"   ✅ Analysis complete in {time.perf_counter() - _t0_analysis:.1f}s")
        logger.info(f"   📊 Result type: {analysis_results.get('type', 'unknown')}")
        logger.info(f"   🎨 Has advanced analytics: {analysis_results.get('has_advanced_analytics', False)}")
        logger.info(f"   💡 Insights: {len(analysis_results.get('insights', []))}")
        logger.info(f"   ⚠️ Alerts: {len(analysis_results.get('alerts', []))}")

        updated_metadata = {
            **metadata,
            "specialized_analysis": True,
            "analyzer_used": analyzer.__class__.__name__,
            "has_advanced_analytics": analysis_results.get("has_advanced_analytics", False)
        }

        # return {
        #     "analyzed_data": analysis_results,
        #     "doc_metadata": updated_metadata,
        #     "classification": classification,
        #     "format": state.get("format")
        # }

        # Build insights from specialist analyzer output
        # Combines alerts (urgent) + insights (advisory) into unified list
        # This overwrites phi3:mini insights when a specialist analyzer ran
        # Build insights from specialist analyzer output
        # Combines alerts (urgent) + insights (advisory) into unified list
        specialist_insights = []

        # Normalize alerts (always dicts)
        for alert in analysis_results.get("alerts", []):
            if isinstance(alert, dict):
                specialist_insights.append({
                    "title": alert.get("title", "Alert"),
                    "description": alert.get("description", ""),
                    "category": "alert",
                    "action": alert.get("action"),
                    "severity": alert.get("severity", "warning"),
                    "icon": alert.get("icon", "⚠️"),
                })
            else:
                # Fallback for malformed alert
                specialist_insights.append({
                    "title": str(alert),
                    "description": "",
                    "category": "alert",
                    "action": None,
                    "severity": "warning",
                    "icon": "⚠️",
                })

        # Normalize insights (may be dicts or strings)
        for insight in analysis_results.get("insights", []):
            if isinstance(insight, str):
                # Simple string insight
                specialist_insights.append({
                    "title": insight,
                    "description": "",
                    "category": "info",
                    "action": None,
                    "icon": "💡",
                })
            elif isinstance(insight, dict):
                # Structured insight
                specialist_insights.append({
                    "title": insight.get("title", "Insight"),
                    "description": insight.get("description", ""),
                    "category": insight.get("type", "info"),
                    "action": insight.get("action"),
                    "icon": insight.get("icon", "💡"),
                })
            else:
                # Unexpected type (None, int, list, etc.)
                specialist_insights.append({
                    "title": str(insight),
                    "description": "",
                    "category": "info",
                    "action": None,
                    "icon": "💡",
                })

        result = {
            "analyzed_data": analysis_results,
            "doc_metadata": updated_metadata,
        }

        # Only override phi3:mini insights if specialist produced something
        if specialist_insights:
            result["insights"] = specialist_insights
            logger.info(
                f"✅ [DocumentAnalysisNode] Specialist insights override: "
                f"{len(specialist_insights)} items "
                f"({len(analysis_results.get('alerts', []))} alerts + "
                f"{len(analysis_results.get('insights', []))} insights)"
            )

        specialist_summary = analysis_results.get("summary")
        if specialist_summary:
            result["summary"] = specialist_summary

        return result

    except Exception as e:
        logger.error(f"❌ [DocumentAnalysisNode] Error: {e}", exc_info=True)

        classification = state.get("classification", {})
        doc_type = classification.get("type", "unknown")

        return {
            "analyzed_data": {
                "type": doc_type,
                "error": str(e),
                "summary": f"Analysis failed: {str(e)}",
                "has_advanced_analytics": False
            },
            "doc_metadata": state.get("doc_metadata", {}),
            "errors": [{
                "node": "document_analysis",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }]
        }


def math_analysis_node(state: DocumentState) -> Dict[str, Any]:
    """Node: Math Path - Privacy-first numerical analysis"""
    logger.info(f"🤖 [MathAnalysisNode] Processing via Math Path")

    try:
        from app.agents.specialist_router import get_router
        from app.services.math_processor import get_math_processor

        doc_type = state.get("classification", {}).get("type", "unknown")
        metadata = state.get("doc_metadata", {})
        tables = metadata.get("tables", [])

        if not tables:
            logger.info("⏭️ No tables, skipping math analysis")
            return {}

        router = get_router()
        processor = get_math_processor()

        operations = router.get_math_operations(doc_type)

        if not operations:
            logger.info("⏭️ No math operations defined for this type")
            return {}

        math_results = {}
        for i, table in enumerate(tables):
            results = processor.process_table(table, operations)
            math_results[f"table_{i + 1}"] = results

        current_metadata = state.get("doc_metadata", {})
        current_metadata["math_analysis"] = math_results
        current_metadata["analysis_method"] = "math_path"

        logger.info(f"✅ [MathAnalysisNode] Completed calculations")

        return {
            "doc_metadata": current_metadata
        }

    except Exception as e:
        logger.error(f"❌ [MathAnalysisNode] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {}


def entity_linking_node(state: DocumentState) -> Dict[str, Any]:
    """Node: Extract entities and link documents"""
    logger.info(f"🤖 [EntityLinkingNode] Extracting entities and linking")

    try:
        from app.agents.specialist_router import get_router
        from app.services.entity_linker import get_entity_linker

        document_id = state.get("document_id")
        doc_type = state.get("classification", {}).get("type", "unknown")
        text = state.get("cleaned_text", state.get("raw_text", ""))

        router = get_router()
        linker = get_entity_linker()

        entity_types = router.get_entities_to_extract(doc_type)

        if not entity_types:
            logger.info("⏭️ No entities configured for this document type")
            return {}

        entities = linker.extract_entities(text, entity_types)

        if not entities:
            logger.info("⏭️ No entities found")
            return {}

        logger.info(f"📎 Extracted entities: {entities}")

        related_ids = linker.link_documents(document_id, entities)

        linker.save_entity_links(document_id, entities, related_ids)

        logger.info(f"✅ [EntityLinkingNode] Linked to {len(related_ids)} documents")

        return {
            "doc_metadata": {
                **state.get("doc_metadata", {}),
                "extracted_entities": entities,
                "related_documents": related_ids
            }
        }

    except Exception as e:
        logger.error(f"❌ [EntityLinkingNode] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {}


# ============================================================================
# LEGACY HELPER FUNCTIONS (kept for backward compatibility)
# ============================================================================

def _extract_bank_statement_key_points(analyzed_data: Dict) -> list:
    deposits = analyzed_data.get("total_credits", 0)
    withdrawals = analyzed_data.get("total_debits", 0)
    net_change = analyzed_data.get("net_change", 0)
    health_score = analyzed_data.get("financial_health_score", 0)
    health_status = analyzed_data.get("financial_health_status", "Unknown")
    total_txns = analyzed_data.get("total_transactions", analyzed_data.get("all_transactions_count", 0))

    return [
        f"Total deposits: ${deposits:,.2f}",
        f"Total withdrawals: ${withdrawals:,.2f}",
        f"Net change: ${net_change:,.2f}",
        f"Financial health: {health_score}/100 - {health_status}",
        f"Transactions processed: {total_txns}"
    ]


def _generate_bank_statement_summary(analyzed_data: Dict) -> str:
    account_holder = analyzed_data.get("account_holder", "Account Holder")
    period_start = analyzed_data.get("statement_start_date", "N/A")
    period_end = analyzed_data.get("statement_end_date", "N/A")
    deposits = analyzed_data.get("total_credits", 0)
    withdrawals = analyzed_data.get("total_debits", 0)
    net_change = analyzed_data.get("net_change", 0)
    num_transactions = analyzed_data.get("total_transactions", analyzed_data.get("all_transactions_count", 0))

    return (
        f"For the period of {period_start} to {period_end}, "
        f"the account statement for {account_holder} shows "
        f"a total deposit of ${deposits:,.2f} and "
        f"a total withdrawal of ${withdrawals:,.2f}, "
        f"resulting in a net change of ${net_change:,.2f}. "
        f"A total of {num_transactions} transactions were processed during this period."
    )


def _generate_generic_summary(analyzed_data: Dict) -> str:
    if not analyzed_data:
        return "This document has been processed, but no structured data was extracted."
    keys = list(analyzed_data.keys())
    return (
        f"This document contains {len(keys)} structured fields extracted during analysis. "
        f"Key fields include: {', '.join(keys[:8])}. "
        f"The extracted data provides an overview of the document's main attributes and content."
    )


def _extract_generic_key_points(analyzed_data: Dict) -> list:
    if not analyzed_data:
        return ["No structured data was extracted from this document."]
    key_points = []
    for k, v in list(analyzed_data.items())[:5]:
        if isinstance(v, (list, dict)):
            v = str(v)
        key_points.append(f"{k}: {v}")
    return key_points


# def _call_deep_summary(text: str, doc_type: str) -> str:
#     prompt = f"""You are a financial document analyst. Write a detailed summary of this {doc_type}.
#
# Document:
# {text[:12000]}
#
# Return ONLY valid JSON:
# {{"summary": "Comprehensive 5-8 sentence summary covering all key facts, amounts, dates, parties, and notable patterns"}}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={"temperature": 0.2, "num_predict": 600, "num_ctx": 4096}
#     )
#     raw = _clean_raw(response["response"].strip())
#     try:
#         return json.loads(raw).get("summary", raw[:600])
#     except json.JSONDecodeError:
#         m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
#         return m.group(1) if m else raw[:600]
#
#
# def _call_deep_insights(text: str, doc_type: str) -> list:
#     prompt = f"""You are a financial document analyst. Generate 5 detailed insights for this {doc_type}.
#
# Document:
# {text[:12000]}
#
# Return ONLY valid JSON:
# {{"insights": [
#   {{"title": "...", "description": "detailed 2-3 sentence description", "category": "analysis"}},
#   {{"title": "...", "description": "detailed 2-3 sentence description", "category": "analysis"}},
#   {{"title": "...", "description": "detailed 2-3 sentence description", "category": "analysis"}},
#   {{"title": "...", "description": "detailed 2-3 sentence description", "category": "analysis"}},
#   {{"title": "...", "description": "detailed 2-3 sentence description", "category": "analysis"}}
# ]}}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={"temperature": 0.2, "num_predict": 1000, "num_ctx": 4096}
#     )
#     raw = _clean_raw(response["response"].strip())
#     try:
#         return json.loads(raw).get("insights", [])
#     except json.JSONDecodeError:
#         return []
#
#
# def _call_deep_key_points(text: str, doc_type: str) -> list:
#     prompt = f"""You are a financial document analyst. Extract 7 detailed key points from this {doc_type}.
#
# Document:
# {text[:12000]}
#
# Return ONLY valid JSON:
# {{"key_points": ["detailed point 1", "detailed point 2", "detailed point 3", "detailed point 4", "detailed point 5", "detailed point 6", "detailed point 7"]}}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={"temperature": 0.2, "num_predict": 600, "num_ctx": 4096}
#     )
#     raw = _clean_raw(response["response"].strip())
#     try:
#         return json.loads(raw).get("key_points", [])
#     except json.JSONDecodeError:
#         return []


# ── Chunking config ───────────────────────────────────────────────────────
CHUNK_SIZE = 3000  # ~750 tokens — fits comfortably in 2048 ctx with prompt overhead


def _get_chunk(text: str, chunk_index: int) -> tuple[str, int, int]:
    """Returns (chunk_text, chunk_index_used, total_chunks)."""
    chunks = [text[i:i + CHUNK_SIZE] for i in range(0, max(len(text), 1), CHUNK_SIZE)]
    total = len(chunks)
    idx = chunk_index % total  # wrap around — infinite generation
    return chunks[idx], idx, total


# ── Summary ───────────────────────────────────────────────────────────────
# def _call_deep_summary(text: str, doc_type: str, chunk_index: int = 0) -> str:
#     chunk, idx, total = _get_chunk(text, chunk_index)
#
#     prompt = f"""You are a financial document analyst. Write a detailed summary of this {doc_type} (section {idx + 1} of {total}).
#
# {chunk}
#
# Return ONLY valid JSON:
# {{"summary": "5-8 sentence summary covering key facts, amounts, dates, parties, and patterns found in this section"}}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={"temperature": 0.2, "num_predict": 400, "num_ctx": 2048}
#     )
#     raw = _clean_raw(response["response"].strip())
#     try:
#         return json.loads(raw).get("summary", raw[:400])
#     except json.JSONDecodeError:
#         m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
#         return m.group(1) if m else raw[:400]

import json
import re


# def _call_deep_summary(text: str, doc_type: str, chunk_index: int = 0) -> str:
#     chunk, idx, total = _get_chunk(text, chunk_index)
#
#     prompt = f"""You are a document analyst. Write a detailed summary of this {doc_type} (section {idx + 1} of {total}).
#
# {chunk}
#
# Return ONLY valid JSON:
# {{"summary": ["sentence 1", "sentence 2", "sentence 3", "sentence 4", "sentence 5"]}}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={"temperature": 0.2, "num_predict": 800, "num_ctx": 2048}
#     )
#     raw = _clean_raw(response["response"].strip())
#
#     # 1) Parse outer JSON: {"summary": ...}
#     try:
#         outer = json.loads(raw)
#         inner_raw = outer.get("summary", "")
#     except json.JSONDecodeError:
#         inner_raw = raw  # fallback
#
#     # 2) Normalize inner value into prose
#     def _summ_to_text(val) -> str:
#         # list of sentences → join
#         if isinstance(val, list):
#             return " ".join(str(x).strip() for x in val if x)
#         # dict → fallback to JSON pretty-print or some custom formatting
#         if isinstance(val, dict):
#             return json.dumps(val, ensure_ascii=False, indent=2)
#         # plain string, maybe contains JSON
#         if isinstance(val, str):
#             s = val.strip()
#             if s.startswith("{") and s.endswith("}"):
#                 try:
#                     obj = json.loads(s)
#                     return _summ_to_text(obj)
#                 except json.JSONDecodeError:
#                     return s
#             return s
#         # anything else
#         return str(val)
#
#     # If outer JSON parsing failed, try regex as last resort
#     if isinstance(inner_raw, str) and not inner_raw:
#         m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
#         if m:
#             extracted = bytes(m.group(1), "utf-8").decode("unicode_escape")
#             inner_raw = extracted
#
#     return _summ_to_text(inner_raw)


# ── Insights ──────────────────────────────────────────────────────────────
# def _call_deep_insights(text: str, doc_type: str, chunk_index: int = 0) -> list:
#     chunk, idx, total = _get_chunk(text, chunk_index)
#
#     prompt = f"""You are a financial document analyst. Generate 3 insights from this {doc_type} (section {idx + 1} of {total}).
#
# {chunk}
#
# Return ONLY valid JSON:
# {{"insights": [
#   {{"title": "...", "description": "2-3 sentences with specific amounts/dates from this section", "category": "analysis"}},
#   {{"title": "...", "description": "2-3 sentences with specific amounts/dates from this section", "category": "analysis"}},
#   {{"title": "...", "description": "2-3 sentences with specific amounts/dates from this section", "category": "analysis"}}
# ]}}"""
#
#     response = ollama_client.generate(
#         model=config.model,
#         prompt=prompt,
#         options={"temperature": 0.2, "num_predict": 500, "num_ctx": 2048}
#     )
#     raw = _clean_raw(response["response"].strip())
#     try:
#         return json.loads(raw).get("insights", [])
#     except json.JSONDecodeError:
#         return []


def _call_deep_summary(text: str, doc_type: str, chunk_index: int = 0, lang: Optional[str] = None) -> str:
    chunk, idx, total = _get_chunk(text, chunk_index)

    # 1. Persona based on doc_type
    from app.agents.prompt_roles import get_role_for_doc_type
    role = get_role_for_doc_type(doc_type)

    # 2. Summary patterns based on doc_type
    from app.agents.doc_type_prompts import doc_type_summary_prompt, GENERIC_INSIGHTS
    patterns = doc_type_summary_prompt.get(doc_type, GENERIC_INSIGHTS)

    # 3. Rotate patterns per chunk (5 per chunk)
    start = (chunk_index * 5) % len(patterns)
    example_patterns = patterns[start:start + 5]

    # wrap around if needed
    if len(example_patterns) < 5:
        example_patterns += patterns[:5 - len(example_patterns)]

    lang_instruction = ""
    if lang == "es":
        lang_instruction = "CRITICAL: You MUST write the summary in Spanish. Responde en español."
    elif lang == "fr":
        lang_instruction = "CRITICAL: You MUST write the summary in French. Réponds en français."
    elif lang == "de":
        lang_instruction = "CRITICAL: You MUST write the summary in German. Antworte auf Deutsch."

    # 4. Build prompt
    prompt = f"""
{role}

Write a detailed summary of this {doc_type} (section {idx + 1} of {total}).

{lang_instruction}

{chunk}

Return ONLY valid JSON:
{{
  "summary": [
    "{example_patterns[0]}",
    "{example_patterns[1]}",
    "{example_patterns[2]}",
    "{example_patterns[3]}",
    "{example_patterns[4]}"
  ]
}}
"""

    # 5. LLM call
    response = ollama_client.generate(
        model=config.model,
        prompt=prompt,
        options={"temperature": 0.2, "num_predict": 800, "num_ctx": 2048}
    )

    raw = _clean_raw(response["response"].strip())

    # 6. Parse JSON safely
    try:
        outer = json.loads(raw)
        inner_raw = outer.get("summary", "")
    except json.JSONDecodeError:
        inner_raw = raw

    # 7. Normalize into prose
    def _summ_to_text(val) -> str:
        if isinstance(val, list):
            return " ".join(str(x).strip() for x in val if x)
        if isinstance(val, dict):
            return json.dumps(val, ensure_ascii=False, indent=2)
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    return _summ_to_text(json.loads(s))
                except:
                    return s
            return s
        return str(val)

    return _summ_to_text(inner_raw)


def _call_deep_insights(text: str, doc_type: str, chunk_index: int = 0, lang: Optional[str] = None) -> list:
    chunk, idx, total = _get_chunk(text, chunk_index)

    # 1. Persona based on doc_type
    from app.agents.prompt_roles import get_role_for_doc_type
    role = get_role_for_doc_type(doc_type)

    # 2. Insight patterns based on doc_type
    from app.agents.doc_type_prompts import doc_type_insights_prompt, GENERIC_INSIGHTS
    patterns = doc_type_insights_prompt.get(doc_type, GENERIC_INSIGHTS)

    # 3. Rotate patterns per chunk (3 per chunk)
    start = (chunk_index * 3) % len(patterns)
    example_patterns = patterns[start:start + 3]

    # wrap around if needed
    if len(example_patterns) < 3:
        example_patterns += patterns[:3 - len(example_patterns)]

    lang_instruction = ""
    if lang == "es":
        lang_instruction = "CRITICAL: You MUST write all insight titles and descriptions in Spanish. Responde en español."
    elif lang == "fr":
        lang_instruction = "CRITICAL: You MUST write all insight titles and descriptions in French. Réponds en français."
    elif lang == "de":
        lang_instruction = "CRITICAL: You MUST write all insight titles and descriptions in German. Antworte auf Deutsch."

    # 4. Build prompt
    prompt = f"""
{role}

Generate 3 insights from this {doc_type} (section {idx + 1} of {total}).

{lang_instruction}

{chunk}

Return ONLY valid JSON:
{{
  "insights": [
    {{"title": "{example_patterns[0]}", "description": "2-3 sentences with specific details from this section", "category": "analysis"}},
    {{"title": "{example_patterns[1]}", "description": "2-3 sentences with specific details from this section", "category": "analysis"}},
    {{"title": "{example_patterns[2]}", "description": "2-3 sentences with specific details from this section", "category": "analysis"}}
  ]
}}
"""

    # 5. LLM call
    response = ollama_client.generate(
        model=config.model,
        prompt=prompt,
        options={"temperature": 0.2, "num_predict": 500, "num_ctx": 2048}
    )

    raw = _clean_raw(response["response"].strip())

    # 6. Safe JSON load
    try:
        return json.loads(raw).get("insights", [])
    except json.JSONDecodeError:
        return []


# ── Key Points ────────────────────────────────────────────────────────────
def _call_deep_key_points(text: str, doc_type: str, chunk_index: int = 0, lang: Optional[str] = None) -> list:
    chunk, idx, total = _get_chunk(text, chunk_index)

    from app.agents.prompt_roles import get_role_for_doc_type

    role = get_role_for_doc_type(doc_type)

    from app.agents.doc_type_prompts import doc_type_insights_prompt, GENERIC_INSIGHTS

    patterns = doc_type_insights_prompt.get(doc_type, GENERIC_INSIGHTS)

    # rotate patterns per chunk
    start = (chunk_index * 5) % len(patterns)
    example_patterns = patterns[start:start + 5]

    # wrap around if needed
    if len(example_patterns) < 5:
        example_patterns += patterns[:5 - len(example_patterns)]

    lang_instruction = ""
    if lang == "es":
        lang_instruction = "CRITICAL: You MUST write all key points in Spanish. Responde en español."
    elif lang == "fr":
        lang_instruction = "CRITICAL: You MUST write all key points in French. Réponds en français."
    elif lang == "de":
        lang_instruction = "CRITICAL: You MUST write all key points in German. Antworte auf Deutsch."

    prompt = f"""
    {role}

    Extract 5 key points from this {doc_type} (section {idx + 1} of {total}).

    {lang_instruction}

    {chunk}

    Return ONLY valid JSON:
    {{
      "key_points": [
        "{example_patterns[0]}",
        "{example_patterns[1]}",
        "{example_patterns[2]}",
        "{example_patterns[3]}",
        "{example_patterns[4]}"
      ]
    }}
    """

    response = ollama_client.generate(
        model=config.model,
        prompt=prompt,
        options={"temperature": 0.2, "num_predict": 400, "num_ctx": 2048}
    )
    raw = _clean_raw(response["response"].strip())
    try:
        return json.loads(raw).get("key_points", [])
    except json.JSONDecodeError:
        return []


def _clean_raw(raw: str) -> str:
    """Strip markdown fences and fix malformed keys."""
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    return re.sub(r'"(\w+):"\s*:', r'"\1":', raw)

# # At the top of the file, add:
#
# import logging
# import re
# from collections import Counter
# from datetime import datetime
# from pathlib import Path
# from typing import Any
# from typing import Callable, Dict
#
# import ollama
#
# from app.agents.state import DocumentState
# from app.config.model_factory import EmbeddingModelFactory
# from app.core import config
# from app.services.parsers.universal_parser import UniversalParser
# from app.services.pdf_extractor import PDFExtractor
#
# logger = logging.getLogger(__name__)
#
# # Initialize services
# pdf_extractor = PDFExtractor()
#
# #embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
# # ✅ NEW: Much better model!
# #embedding_model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")  # 768-dim, MUCH better quality
#
# embedding_model = EmbeddingModelFactory.get_model()
#
# ollama_client = ollama.Client()
#
# SummaryFn = Callable[[Dict], str]
# KeyPointsFn = Callable[[Dict], list]
#
#
# # ============================================================================
# # AGENT NODES (LangGraph functions)
# # ============================================================================
#
# def ingest_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 1: Ingest and validate file"""
#     logger.info(f"🤖 [IngestNode] Processing: {state['filename']}")
#
#     try:
#         file_path = Path(state["file_path"])
#
#         if not file_path.exists():
#             return {
#                 "errors": [{
#                     "node": "ingest",
#                     "error": f"File not found: {state['file_path']}",
#                     "timestamp": datetime.utcnow().isoformat()
#                 }],
#                 "processing_complete": True
#             }
#
#         # Get file metadata
#         file_size = file_path.stat().st_size
#         file_ext = file_path.suffix.lower()
#
#         # Determine if OCR needed (in real world, check if PDF is scanned)
#         needs_ocr = False  # Can add logic to detect scanned PDFs
#
#         logger.info(f"✅ [IngestNode] Ingested {file_size} bytes")
#
#         # Read file
#         with open(file_path, "rb") as f:
#             content = f.read()
#
#         logger.info(f"✅ [IngestNode] Ingested {len(content)} bytes")
#
#         file_path = state.get("file_path")
#         filename = state.get("filename", "")
#
#         # Extract format from filename
#         fmt = ""
#         if filename and '.' in filename:
#             fmt = filename.rsplit('.', 1)[-1].lower()
#
#         return {
#             "file_size": file_size,
#             "file_type": file_ext,
#             "needs_ocr": needs_ocr,
#             "format": fmt,
#             "doc_metadata": {
#                 "file_size_mb": round(file_size / (1024 * 1024), 2),
#                 "ingestion_time": datetime.utcnow().isoformat()
#             }
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [IngestNode] Error: {e}")
#         return {
#             "errors": [{
#                 "node": "ingest",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }
#
#
# def parse_node(state: DocumentState) -> Dict[str, Any]:
#     """
#     Generic parse node that handles all document types.
#
#     Process:
#     1. Parse with UniversalParser to extract content
#     2. Classify based on parsed content
#     3. Optionally re-parse with specialized parser
#     4. Normalize tables for storage
#     5. Generate text fallback if needed
#     6. Return complete parsed state
#     """
#     reprocess_count = state.get("reprocess_count", 0)
#     logger.info(f"🤖 [ParseNode] Parsing document (attempt {reprocess_count + 1})")
#
#     try:
#         # ==================== IMPORTS ====================
#         from app.services.document_registry import DOCUMENT_REGISTRY
#         from app.utils.json_utils import make_json_serializable
#         from app.utils.table_utils import normalize_table
#
#         # ==================== EXTRACT STATE INFO ====================
#         file_path = state.get("file_path", "")
#         filename = state.get("filename", "")
#         fmt = state.get("file_type", "")
#
#         # Extract format from filename if not provided
#         if not fmt and filename and '.' in filename:
#             fmt = filename.rsplit('.', 1)[-1].lower()
#
#         logger.info(f"📄 Parsing: {filename}")
#         logger.info(f"📋 Format: {fmt}")
#         logger.info(f"📂 Path: {file_path}")
#
#         # ==================== STAGE 1: UNIVERSAL PARSE ====================
#         logger.info("🧩 Stage 1: Universal parsing")
#
#         parser = UniversalParser()
#         parsed_data = parser.parse(file_path, filename)
#
#         # Extract components
#         text = parsed_data.get("text", "")
#         raw_tables = parsed_data.get("tables", []) or parsed_data.get("sheets", [])
#         parse_metadata = parsed_data.get("metadata", {})
#
#         logger.info(f"✅ Parsed: {len(raw_tables)} table(s), {len(text)} chars of text")
#
#         # ==================== STAGE 2: CLASSIFY ====================
#         logger.info("🔍 Stage 2: Classification")
#
#         # Prepare state for classification
#         classify_state = {
#             "format": fmt,
#             "parsed": {
#                 "text": text,
#                 "tables": raw_tables,
#                 "sheets": raw_tables,
#                 "metadata": parse_metadata,
#                 "type": parsed_data.get("type", "unknown")
#             }
#         }
#         classify_state["file_path"] = state.get("file_path", "")
#         classify_state["filename"] = state.get("filename", "")
#         if state.get("format", ""):
#             classify_state["format"] = state.get("format", "")
#         else:
#             classify_state["format"] = fmt
#         # Call classify_node directly (it's in the same file)
#         classified_state = classify_node(classify_state)  # ← No import needed!
#
#         classification = classified_state.get("classification", {})
#         doc_type = classification.get("type", "unknown")
#         confidence = classification.get("confidence", 0.0)
#         method = classification.get("method", "unknown")
#
#         logger.info(f"✅ Classified: {doc_type} (confidence: {confidence:.2f}, method: {method})")
#
#         # ==================== STAGE 3: SPECIALIZED PARSE (IF NEEDED) ====================
#         used_specialized_parser = False
#         registry_entry = DOCUMENT_REGISTRY.get(doc_type, {})
#         parser_cls = registry_entry.get("parser")
#
#         if parser_cls and parser_cls != UniversalParser:
#             logger.info(f"🔄 Stage 3: Re-parsing with {parser_cls.__name__}")
#
#             try:
#                 # Initialize specialized parser
#                 specialized_parser = parser_cls()
#                 specialized_data = specialized_parser.parse(file_path)
#
#                 # Handle different return formats
#                 if isinstance(specialized_data, dict):
#                     if "text" in specialized_data or "tables" in specialized_data or "sheets" in specialized_data:
#                         text = specialized_data.get("text", text) or text
#                         raw_tables = (
#                                 specialized_data.get("tables") or
#                                 specialized_data.get("sheets") or
#                                 raw_tables
#                         )
#                         parse_metadata = specialized_data.get("metadata", parse_metadata)
#
#                     elif "columns" in specialized_data and ("data" in specialized_data or "rows" in specialized_data):
#                         logger.info("📦 Specialized parser returned single table, wrapping it")
#                         raw_tables = [specialized_data]
#
#                     used_specialized_parser = True
#                     logger.info(f"✅ Specialized parser: {len(raw_tables)} table(s), {len(text)} chars")
#
#             except Exception as e:
#                 logger.error(f"❌ Specialized parser failed: {e}", exc_info=True)
#                 logger.info("🔄 Falling back to UniversalParser results")
#         else:
#             logger.info("ℹ️ Stage 3: No specialized parser needed")
#
#         # ==================== STAGE 4: NORMALIZE TABLES ====================
#         logger.info("📊 Stage 4: Normalizing tables")
#         normalized_tables = []
#
#         for i, raw_table in enumerate(raw_tables):
#             if not isinstance(raw_table, dict):
#                 logger.warning(f"⚠️ Table {i + 1} is {type(raw_table)}, skipping")
#                 continue
#
#             try:
#                 logger.info(f"   Normalizing table {i + 1}/{len(raw_tables)}")
#                 normalized = normalize_table(raw_table)
#
#                 # 🔥 SAFETY GUARD: normalize_table() may return None
#                 if normalized is None:
#                     logger.warning(f"⚠️ Table {i + 1} normalization returned None — skipping")
#                     continue
#
#                 cols = normalized.get("columns", [])
#                 data = normalized.get("data", [])
#                 logger.info(f"   → {len(cols)} columns × {len(data)} rows")
#
#                 normalized_tables.append(normalized)
#
#             except Exception as e:
#                 logger.error(f"❌ Table {i + 1} normalization failed: {e}")
#
#         # Serialize
#         try:
#             normalized_tables = make_json_serializable(normalized_tables)
#             parse_metadata = make_json_serializable(parse_metadata)
#         except Exception as e:
#             logger.error(f"❌ JSON serialization failed: {e}")
#
#         logger.info(f"✅ Normalized: {len(normalized_tables)} table(s)")
#
#         # ==================== STAGE 5: GENERATE TEXT FALLBACK ====================
#         if (not text or len(text) < 100) and normalized_tables:
#             logger.info("📝 Stage 5: Generating text from tables")
#             text_parts = []
#
#             for i, table in enumerate(normalized_tables):
#                 table_text = f"\n{'=' * 60}\n"
#                 table_text += f"TABLE {i + 1}\n"
#                 table_text += f"{'=' * 60}\n\n"
#
#                 columns = table.get("columns", [])
#                 if columns:
#                     table_text += f"Columns ({len(columns)}): {', '.join(str(c) for c in columns[:20])}"
#                     if len(columns) > 20:
#                         table_text += f" ... and {len(columns) - 20} more"
#                     table_text += "\n\n"
#
#                 data = table.get("data", [])
#                 if data:
#                     sample_size = min(10, len(data))
#                     table_text += f"Sample Data (showing {sample_size} of {len(data)} rows):\n\n"
#
#                     for row_idx in range(sample_size):
#                         if row_idx < len(data):
#                             row = data[row_idx]
#                             row_cells = [str(cell)[:50] if cell else "" for cell in row[:10]]
#                             row_text = " | ".join(row_cells)
#                             table_text += f"Row {row_idx + 1}: {row_text}\n"
#
#                     if len(data) > sample_size:
#                         table_text += f"\n... ({len(data) - sample_size} more rows)\n"
#
#                 text_parts.append(table_text)
#
#             text = "\n\n".join(text_parts)
#             logger.info(f"✅ Generated {len(text)} chars from {len(normalized_tables)} table(s)")
#         else:
#             logger.info("ℹ️ Stage 5: Using existing text content")
#
#         # ==================== STAGE 6: UPDATE STATE ====================
#         logger.info("📦 Stage 6: Preparing final state")
#
#         current_metadata = state.get("doc_metadata", {})
#         current_metadata.update({
#             "tables": normalized_tables,
#             "parse_info": parse_metadata,
#             "used_specialized_parser": used_specialized_parser,
#             "parser_class": parser_cls.__name__ if parser_cls else "UniversalParser",
#             "table_count": len(normalized_tables),
#             "text_length": len(text)
#         })
#
#         logger.info("✅ [ParseNode] Complete")
#         logger.info(f"   📊 Tables: {len(normalized_tables)}")
#         logger.info(f"   📝 Text: {len(text)} chars")
#         logger.info(f"   🏷️ Type: {doc_type} ({confidence:.2f})")
#
#         return {
#             "raw_text": text,
#             "doc_metadata": current_metadata,
#             "classification": classification,
#             "format": fmt,
#             "reprocess_count": reprocess_count + 1
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [ParseNode] Critical error: {e}", exc_info=True)
#
#         return {
#             "raw_text": "",
#             "doc_metadata": {
#                 "tables": [],
#                 "parse_info": {},
#                 "error": str(e)
#             },
#             "classification": {
#                 "type": "unknown",
#                 "confidence": 0.0,
#                 "method": "error"
#             },
#             "format": state.get("format", ""),
#             "errors": state.get("errors", []) + [{
#                 "node": "parse",
#                 "error": str(e),
#                 "timestamp": __import__('datetime').datetime.now().isoformat()
#             }],
#             "reprocess_count": reprocess_count + 1
#         }
#
#
# # def parse_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node: Parse document content"""
# #
# #     reprocess_count = state.get("reprocess_count", 0)
# #     logger.info(f"🤖 [ParseNode] Parsing document (attempt {reprocess_count + 1})")
# #
# #     try:
# #         from app.parsers.universal_parser import UniversalParser
# #         from app.utils.json_utils import make_json_serializable
# #         from app.utils.table_utils import normalize_table  # ✅ Import normalizer
# #
# #         file_path = state.get("file_path", "")
# #         filename = state.get("filename", "")
# #
# #         logger.info(f"📄 Parsing file: {filename} at {file_path}")
# #
# #         # Parse with universal parser
# #         parser = UniversalParser()
# #         parsed_data = parser.parse(file_path, filename)
# #
# #         text = parsed_data.get("text", "")
# #         raw_tables = parsed_data.get("tables", [])
# #         parse_metadata = parsed_data.get("metadata", {})
# #
# #         # ✅ NORMALIZE ALL TABLES to consistent format
# #         normalized_tables = []
# #         for i, raw_table in enumerate(raw_tables):
# #             logger.info(f"📊 Normalizing table {i + 1}")
# #             logger.info(f"   Raw table keys: {list(raw_table.keys())}")
# #
# #             normalized = normalize_table(raw_table)
# #
# #             logger.info(f"   Normalized: {len(normalized['columns'])} cols × {normalized['rows']} rows")
# #
# #             normalized_tables.append(normalized)
# #
# #         # ✅ CLEAN for JSON serialization
# #         normalized_tables = make_json_serializable(normalized_tables)
# #         parse_metadata = make_json_serializable(parse_metadata)
# #
# #         logger.info(f"✅ Normalized and cleaned {len(normalized_tables)} tables")
# #
# #         # If no text but have tables, extract text from tables
# #         if (not text or len(text) < 100) and normalized_tables:
# #             logger.info(f"📝 Extracting text from {len(normalized_tables)} table(s)")
# #             text_parts = []
# #
# #             for i, table in enumerate(normalized_tables):
# #                 table_text = f"\n=== Table {i + 1} ===\n"
# #
# #                 # Add columns
# #                 columns = table.get("columns", [])
# #                 if columns:
# #                     table_text += "Columns: " + ", ".join(str(c) for c in columns) + "\n\n"
# #
# #                 # Add sample rows (first 10)
# #                 data = table.get("data", [])
# #                 if data and len(data) > 1:  # Skip header row
# #                     for row_idx, row in enumerate(data[1:11], 1):
# #                         row_text = " | ".join(str(cell) if cell is not None else "" for cell in row)
# #                         table_text += f"Row {row_idx}: {row_text}\n"
# #
# #                     if len(data) > 11:
# #                         table_text += f"\n... and {len(data) - 11} more rows\n"
# #
# #                 text_parts.append(table_text)
# #
# #             text = "\n\n".join(text_parts)
# #             logger.info(f"✅ Generated {len(text)} characters from tables")
# #
# #         logger.info(f"📄 Extracted {len(text)} characters")
# #         logger.info(f"📊 Extracted {len(normalized_tables)} tables")
# #
# #         # Store normalized tables in metadata
# #         current_metadata = state.get("doc_metadata", {})
# #         current_metadata["tables"] = normalized_tables
# #         current_metadata["parse_info"] = parse_metadata
# #
# #         return {
# #             "raw_text": text,
# #             "doc_metadata": current_metadata,
# #             "reprocess_count": reprocess_count + 1
# #         }
# #
# #     except Exception as e:
# #         logger.error(f"❌ [ParseNode] Error: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #         return {
# #             "errors": state.get("errors", []) + [{"node": "parse", "error": str(e)}],
# #             "reprocess_count": reprocess_count + 1
# #         }
#
#
# def cleanup_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 3: Clean and normalize text"""
#     logger.info(f"🤖 [CleanupNode] Cleaning text")
#
#     try:
#         raw_text = state.get("raw_text", "")
#
#         if not raw_text:
#             return {
#                 "cleaned_text": "",
#                 "classification": state.get("classification"),  # ✅ Preserve classification
#                 "format": state.get("format")  # ✅ Preserve format
#             }
#
#         text = raw_text
#         original_length = len(text)
#
#         # Remove NULL bytes
#         text = text.replace('\x00', '')
#
#         # Remove multiple spaces
#         text = re.sub(r' +', ' ', text)
#
#         # Normalize line breaks
#         text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
#
#         # Remove page markers
#         text = re.sub(r'Page \d+(/\d+)?', '', text, flags=re.IGNORECASE)
#
#         # Trim
#         text = text.strip()
#
#         logger.info(f"✅ [CleanupNode] Cleaned: {original_length} → {len(text)} chars")
#
#         # Merge with existing doc_metadata
#         current_metadata = state.get("doc_metadata", {})
#         current_metadata["original_length"] = original_length
#         current_metadata["cleaned_length"] = len(text)
#
#         return {
#             "cleaned_text": text,
#             "doc_metadata": current_metadata,
#             "classification": state.get("classification"),  # ✅ Preserve classification
#             "format": state.get("format")  # ✅ Preserve format
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [CleanupNode] Error: {e}")
#         return {
#             "cleaned_text": state.get("raw_text", ""),
#             "classification": state.get("classification"),  # ✅ Preserve even on error
#             "format": state.get("format"),
#             "errors": state.get("errors", []) + [{
#                 "node": "cleanup",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }
#
#
# def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
#     """
#     Deterministic document classification node.
#     """
#     parsed = state.get("parsed", {})
#     fmt = state.get("format", "").lower().strip()
#
#     has_sheets = bool(parsed.get("sheets") or parsed.get("tables"))
#     has_text = bool(parsed.get("text"))
#
#     classification = {
#         "type": "unknown",
#         "confidence": 0.0,
#         "method": "none",
#         "explain": {}
#     }
#
#     logger.info(f"🔍 Classifying format={fmt}, has_sheets={has_sheets}, has_text={has_text}")
#     results = []
#
#     # ========================================
#     # 🆕 ADD IMAGE CLASSIFICATION HERE
#     # ========================================
#     if fmt in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg", ".heic", ".heif"):
#         logger.info("📸 Image format detected - using image classification")
#         classification.update({
#             "type": "image",
#             "confidence": 0.9,
#             "method": "format_detection"
#         })
#         logger.info(f"✅ Final: {classification['type']} ({classification['confidence']:.2f})")
#         state["classification"] = classification
#         return state
#
#     if has_text:
#         try:
#             from app.services.classifiers.document_classifier import DocumentClassifier
#             dc = DocumentClassifier()
#             if len(parsed["tables"]) > 0:
#                 result = dc.classify(parsed["tables"][0], parsed, fmt)
#             else:
#                 result = dc.classify(parsed["tables"], parsed, fmt)
#             if result["type"] == "spreadsheet":
#                 result["confidence"] = 0.9
#
#             results.append(result)
#         except Exception as e:
#             logger.warning(f"⚠️ Text classifier failed: {e}")
#
#     if has_sheets:
#         try:
#             sheets = parsed.get("sheets") or parsed.get("tables") or []
#             from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
#             sc = SpreadsheetClassifier()
#
#             for sheet in sheets:
#                 result = sc.classify(file_path=state.get("file_path"), parsed=parsed)
#                 results.append(result)
#         except Exception as e:
#             logger.warning(f"⚠️ Table classifier failed: {e}")
#
#     valid_results = [r for r in results if r.get("confidence", 0) >= 0.3]
#     if valid_results:
#         classification.update(max(valid_results, key=lambda x: x["confidence"]))
#     else:
#         classification.update({"type": "document", "confidence": 0.3, "method": "fallback"})
#
#     # # ==================== SPREADSHEETS ====================
#     # if fmt in ("xlsx", "xls", "xlsm", "xlsb", "csv", "tsv", "ods"):
#     #     if not has_sheets:
#     #         classification.update({"type": "spreadsheet", "confidence": 0.3, "method": "format_only"})
#     #     else:
#     #         sheets = parsed.get("sheets") or parsed.get("tables") or []
#     #         from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
#     #         sc = SpreadsheetClassifier()
#     #
#     #         sheet_results = []
#     #         for idx, sheet in enumerate(sheets):
#     #             # Pass sheet AND full parsed context
#     #             result = sc.classify(table=sheet, parsed=parsed, fmt=fmt)
#     #             result["_sheet_index"] = idx
#     #             sheet_results.append(result)
#     #             logger.info(f"   Sheet {idx}: {result['type']} ({result['confidence']:.2f})")
#     #
#     #         best = max(sheet_results, key=lambda r: (r["confidence"], -r["_sheet_index"]))
#     #
#     #         classification.update({
#     #             "type": best["type"] if best["confidence"] >= 0.5 else "spreadsheet",
#     #             "confidence": best["confidence"],
#     #             "method": "spreadsheet_classifier"
#     #         })
#     #
#     # # ==================== PDF ====================
#     # elif fmt == "pdf":
#     #     logger.info("📄 PDF - analyzing text and tables")
#     #
#     #     results = []
#     #
#     #     # Text classifier
#     #     if has_text:
#     #         try:
#     #             from app.services.classifiers.document_classifier import DocumentClassifier
#     #             dc = DocumentClassifier()
#     #             result = dc.classify(parsed["tables"][0], parsed, fmt)
#     #             results.append(result)
#     #             logger.info(f"   [Text] {result['type']} ({result['confidence']:.2f})")
#     #         except Exception as e:
#     #             logger.warning(f"⚠️ Text classifier failed: {e}")
#     #
#     #     # Table classifier
#     #     if has_sheets:
#     #         try:
#     #             sheets = parsed.get("sheets") or parsed.get("tables") or []
#     #             from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
#     #             sc = SpreadsheetClassifier()
#     #
#     #             for idx, sheet in enumerate(sheets):
#     #                 # Pass sheet AND full context
#     #                 result = sc.classify(table=sheet, parsed=parsed, fmt=fmt)
#     #                 results.append(result)
#     #                 logger.info(f"   [Table {idx}] {result['type']} ({result['confidence']:.2f})")
#     #         except Exception as e:
#     #             logger.warning(f"⚠️ Table classifier failed: {e}")
#     #
#     #     # Pick best
#     #     valid_results = [r for r in results if r.get("confidence", 0) >= 0.3]
#     #     if valid_results:
#     #         best = max(valid_results, key=lambda x: x["confidence"])
#     #         classification.update(best)
#     #         logger.info(f"   ✅ Selected: {classification['type']} ({classification['confidence']:.2f})")
#     #     else:
#     #         classification.update({"type": "document", "confidence": 0.3, "method": "fallback"})
#     #
#     # # ==================== WORD DOCS ====================
#     # elif fmt in ("docx", "doc", "odt", "rtf"):
#     #     results = []
#     #
#     #     if has_text:
#     #         try:
#     #             from app.services.classifiers.document_classifier import DocumentClassifier
#     #             dc = DocumentClassifier()
#     #             result = dc.classify(parsed)
#     #             results.append(result)
#     #         except Exception as e:
#     #             logger.warning(f"⚠️ Text classifier failed: {e}")
#     #
#     #     if has_sheets:
#     #         try:
#     #             sheets = parsed.get("sheets") or parsed.get("tables") or []
#     #             from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
#     #             sc = SpreadsheetClassifier()
#     #
#     #             for sheet in sheets:
#     #                 result = sc.classify(table=sheet, parsed=parsed, fmt=fmt)
#     #                 results.append(result)
#     #         except Exception as e:
#     #             logger.warning(f"⚠️ Table classifier failed: {e}")
#     #
#     #     valid_results = [r for r in results if r.get("confidence", 0) >= 0.3]
#     #     if valid_results:
#     #         classification.update(max(valid_results, key=lambda x: x["confidence"]))
#     #     else:
#     #         classification.update({"type": "document", "confidence": 0.3, "method": "fallback"})
#     #
#     # # ==================== TEXT FILES ====================
#     # elif fmt in ("txt", "text", "md", "markdown"):
#     #     from app.services.classifiers.document_classifier import DocumentClassifier
#     #     dc = DocumentClassifier()
#     #     result = dc.classify(parsed)
#     #
#     #     fallback = "text_document" if fmt in ("txt", "text") else "markdown_document"
#     #     classification.update({
#     #         "type": result["type"] if result["confidence"] >= 0.5 else fallback,
#     #         "confidence": result["confidence"],
#     #         "method": "document_classifier"
#     #     })
#     #
#     # # ==================== OTHER FORMATS ====================
#     # elif fmt in ("pptx", "ppt", "odp"):
#     #     classification.update({"type": "presentation", "confidence": 0.7, "method": "format"})
#     # elif fmt in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff"):
#     #     classification.update({"type": "image", "confidence": 0.7, "method": "format"})
#     # elif fmt in ("eml", "msg", "mbox"):
#     #     classification.update({"type": "email", "confidence": 0.7, "method": "format"})
#     # elif fmt in ("zip", "rar", "7z", "tar", "gz"):
#     #     classification.update({"type": "archive", "confidence": 0.95, "method": "format"})
#     # else:
#     #     classification.update({"type": "unknown", "confidence": 0.1, "method": "none"})
#
#     logger.info(f"✅ Final: {classification['type']} ({classification['confidence']:.2f})")
#
#     state["classification"] = classification
#     return state
#
#
# # def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
# #     """
# #     Deterministic document classification node.
# #     Must be called AFTER parsing to get sheet/table data.
# #
# #     Smart routing:
# #     - Spreadsheets (xlsx, csv) → SpreadsheetClassifier
# #     - PDFs/Docs WITH structured tables → Try SpreadsheetClassifier first, fall back to DocumentClassifier
# #     - PDFs/Docs WITHOUT tables → DocumentClassifier
# #     - Images → ImageClassifier
# #     - Emails → EmailClassifier
# #     """
# #     parsed = state.get("parsed", {})
# #     fmt = state.get("format", "").lower().strip()
# #
# #     # Check if we have parsed data
# #     has_sheets = bool(parsed.get("sheets") or parsed.get("tables"))
# #     has_text = bool(parsed.get("text"))
# #
# #     classification = {
# #         "type": "unknown",
# #         "confidence": 0.0,
# #         "method": "none",
# #         "explain": {}
# #     }
# #
# #     logger.info(f"🔍 Classifying format={fmt}, has_sheets={has_sheets}, has_text={has_text}")
# #
# #     # ==================== PURE SPREADSHEET FORMATS ====================
# #     if fmt in ("xlsx", "xls", "xlsm", "xlsb", "csv", "tsv", "ods", "spreadsheet"):
# #         if not has_sheets:
# #             classification.update({
# #                 "type": "spreadsheet",
# #                 "confidence": 0.3,
# #                 "method": "format_only",
# #                 "explain": {"reason": "Excel format but no sheet data found"}
# #             })
# #         else:
# #             sheets = parsed.get("sheets") or parsed.get("tables") or []
# #             logger.info(f"📊 Found {len(sheets)} sheets to classify")
# #
# #             from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
# #             sc = SpreadsheetClassifier()
# #             sheet_results = []
# #
# #             for idx, sheet in enumerate(sheets):
# #                 result = sc.classify(state)
# #                 result["_sheet_index"] = idx
# #                 sheet_results.append(result)
# #                 logger.info(f"   Sheet {idx}: {result['type']} (confidence: {result['confidence']:.2f})")
# #
# #             best = max(
# #                 sheet_results,
# #                 key=lambda r: (r["confidence"], -r["_sheet_index"])
# #             )
# #
# #             classification.update({
# #                 "type": best["type"] if best["confidence"] >= 0.5 else "spreadsheet",
# #                 "confidence": best["confidence"],
# #                 "method": "spreadsheet_classifier",
# #                 "explain": {
# #                     "sheets_analyzed": len(sheet_results),
# #                     "selected_sheet": best["_sheet_index"],
# #                     "selection_reason": f"confidence={best['confidence']:.3f}, sheet={best['_sheet_index']}"
# #                 }
# #             })
# #
# #     # ==================== DOCUMENT FORMATS (PDF, DOCX, etc) ====================
# #     elif fmt in ("pdf", "docx", "doc", "odt", "rtf", "txt", "text", "md", "markdown"):
# #
# #         # SMART ROUTING: If document has tables, try both classifiers and pick best
# #         if has_sheets and fmt in ("pdf", "docx", "doc", "odt"):
# #             logger.info(f"📊 Document with tables - trying both classifiers")
# #
# #             # Try spreadsheet classifier (for bank statements, financial tables, etc)
# #             sheets = parsed.get("sheets") or parsed.get("tables") or []
# #             spreadsheet_result = None
# #
# #             try:
# #                 from app.services.classifiers.spreadsheet_classifier import SpreadsheetClassifier
# #                 sc = SpreadsheetClassifier()
# #                 sheet_results = []
# #
# #                 for idx, sheet in enumerate(sheets):
# #                     result = sc.classify(state)
# #                     result["_sheet_index"] = idx
# #                     sheet_results.append(result)
# #                     logger.info(
# #                         f"   [SpreadsheetClassifier] Sheet {idx}: {result['type']} (confidence: {result['confidence']:.2f})")
# #
# #                 if sheet_results:
# #                     best = max(
# #                         sheet_results,
# #                         key=lambda r: (r["confidence"], -r["_sheet_index"])
# #                     )
# #                     spreadsheet_result = {
# #                         "type": best["type"],
# #                         "confidence": best["confidence"],
# #                         "method": "spreadsheet_classifier",
# #                         "explain": {
# #                             "sheets_analyzed": len(sheet_results),
# #                             "selected_sheet": best["_sheet_index"]
# #                         }
# #                     }
# #             except Exception as e:
# #                 logger.warning(f"⚠️ SpreadsheetClassifier failed: {e}")
# #
# #             # Try document classifier (for contracts, letters, reports, etc)
# #             document_result = None
# #             try:
# #                 from app.services.classifiers.document_classifier import DocumentClassifier
# #                 dc = DocumentClassifier()
# #                 document_result = dc.classify(state)
# #                 logger.info(
# #                     f"   [DocumentClassifier] {document_result['type']} (confidence: {document_result['confidence']:.2f})")
# #             except Exception as e:
# #                 logger.warning(f"⚠️ DocumentClassifier failed: {e}")
# #
# #             # PICK THE BEST RESULT
# #             candidates = []
# #             if spreadsheet_result and spreadsheet_result["confidence"] >= 0.4:
# #                 candidates.append(spreadsheet_result)
# #             if document_result and document_result["confidence"] >= 0.3:
# #                 candidates.append(document_result)
# #
# #             if candidates:
# #                 # Use the one with highest confidence
# #                 best_result = max(candidates, key=lambda x: x["confidence"])
# #                 classification.update(best_result)
# #                 logger.info(
# #                     f"   ✅ Selected: {classification['type']} (confidence: {classification['confidence']:.2f}, method: {classification['method']})")
# #             else:
# #                 # Both failed or low confidence - use document as fallback
# #                 fallback_type = "document" if fmt in ("pdf", "docx", "doc", "odt") else "text_document"
# #                 classification.update({
# #                     "type": fallback_type,
# #                     "confidence": 0.3,
# #                     "method": "fallback",
# #                     "explain": {"reason": "Both classifiers had low confidence"}
# #                 })
# #
# #         else:
# #             # No tables OR plain text format - use document classifier only
# #             from app.services.classifiers.document_classifier import DocumentClassifier
# #             dc = DocumentClassifier()
# #             result = dc.classify(state)
# #
# #             fallback_type = "document"
# #             if fmt in ("txt", "text"):
# #                 fallback_type = "text_document"
# #             elif fmt in ("md", "markdown"):
# #                 fallback_type = "markdown_document"
# #
# #             classification.update({
# #                 "type": result["type"] if result["confidence"] >= 0.5 else fallback_type,
# #                 "confidence": result["confidence"],
# #                 "method": "document_classifier",
# #                 "explain": result
# #             })
# #
# #     # ==================== PRESENTATION FORMATS ====================
# #     elif fmt in ("pptx", "ppt", "odp", "key", "presentation"):
# #         classification.update({
# #             "type": "presentation",
# #             "confidence": 0.7,
# #             "method": "format_detection"
# #         })
# #
# #     # ==================== IMAGE FORMATS ====================
# #     elif fmt in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "heif",
# #                  "tiff", "tif", "svg", "ico", "image"):
# #         try:
# #             from app.services.classifiers.image_classifier import ImageClassifier
# #             ic = ImageClassifier()
# #             result = ic.classify(state)
# #
# #             classification.update({
# #                 "type": result["type"],
# #                 "confidence": result["confidence"],
# #                 "method": "image_classifier",
# #                 "explain": result
# #             })
# #         except Exception as e:
# #             logger.error(f"❌ ImageClassifier failed: {e}")
# #             classification.update({
# #                 "type": "image",
# #                 "confidence": 0.5,
# #                 "method": "fallback"
# #             })
# #
# #     # ==================== EMAIL FORMATS ====================
# #     elif fmt in ("eml", "msg", "mbox", "email", "emlx"):
# #         classification.update({
# #             "type": "email",
# #             "confidence": 0.7,
# #             "method": "format_detection"
# #         })
# #
# #     # ==================== ARCHIVE FORMATS ====================
# #     elif fmt in ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "archive"):
# #         classification.update({
# #             "type": "archive",
# #             "confidence": 0.95,
# #             "method": "format_detection"
# #         })
# #
# #     # ==================== CODE/DATA FORMATS ====================
# #     elif fmt in ("py", "js", "ts", "java", "cpp", "c", "h", "cs", "rb", "go",
# #                  "rs", "php", "html", "css", "json", "xml", "yaml", "yml"):
# #         classification.update({
# #             "type": "code_file",
# #             "confidence": 0.95,
# #             "method": "format_detection"
# #         })
# #
# #     # ==================== MEDIA FORMATS ====================
# #     elif fmt in ("mp3", "wav", "flac", "aac", "ogg", "m4a", "audio"):
# #         classification.update({
# #             "type": "audio",
# #             "confidence": 0.95,
# #             "method": "format_detection"
# #         })
# #
# #     elif fmt in ("mp4", "avi", "mov", "wmv", "mkv", "webm", "video"):
# #         classification.update({
# #             "type": "video",
# #             "confidence": 0.95,
# #             "method": "format_detection"
# #         })
# #
# #     # ==================== FALLBACK ====================
# #     else:
# #         classification.update({
# #             "type": "unknown",
# #             "confidence": 0.1,
# #             "method": "no_handler",
# #             "explain": {"reason": f"No classifier for format '{fmt}'"}
# #         })
# #
# #     logger.info(f"✅ Classification complete: {classification['type']} (confidence: {classification['confidence']:.2f})")
# #
# #     # Store classification in state
# #     state["classification"] = classification
# #
# #     return state
#
# # def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
# #     """
# #     Deterministic document classification node.
# #     Always returns the same doc_type for the same input.
# #
# #     Supports all major document formats with fallback handling.
# #     """
# #     parsed = state.get("parsed", {})
# #     fmt = state.get("format", "").lower().strip()
# #     classification = {
# #         "type": "unknown",
# #         "confidence": 0.0,
# #         "method": "none",
# #         "explain": {}
# #     }
# #
# #     # ==================== SPREADSHEET FORMATS ====================
# #     if fmt in ("xlsx", "xls", "xlsm", "xlsb", "csv", "tsv", "ods", "spreadsheet"):
# #         sheets = parsed.get("sheets") or parsed.get("tables") or []
# #
# #         if not sheets:
# #             classification.update({
# #                 "type": "spreadsheet",
# #                 "confidence": 0.2,
# #                 "method": "format_only",
# #                 "explain": {"reason": "No sheet data available"}
# #             })
# #         else:
# #             # Classify each sheet deterministically
# #             sc = SpreadsheetClassifier()
# #             sheet_results = []
# #
# #             for idx, sheet in enumerate(sheets):
# #                 result = sc.classify(sheet)
# #                 result["_sheet_index"] = idx  # Track original position
# #                 sheet_results.append(result)
# #
# #             # Deterministic selection: highest confidence, then earliest sheet
# #             best = max(
# #                 sheet_results,
# #                 key=lambda r: (r["confidence"], -r["_sheet_index"])
# #             )
# #
# #             classification.update({
# #                 "type": best["type"] if best["confidence"] >= 0.5 else "spreadsheet",
# #                 "confidence": best["confidence"],
# #                 "method": "spreadsheet_classifier",
# #                 "explain": {
# #                     "sheets": sheet_results,
# #                     "selected_sheet": best["_sheet_index"],
# #                     "selection_reason": f"confidence={best['confidence']:.3f}, sheet={best['_sheet_index']}"
# #                 }
# #             })
# #
# #     # ==================== DOCUMENT FORMATS ====================
# #     elif fmt in ("pdf", "docx", "doc", "odt", "rtf", "txt", "text", "md", "markdown"):
# #         dc = DocumentClassifier()
# #         result = dc.classify(parsed)
# #
# #         # Determine fallback type based on format
# #         fallback_type = "document"
# #         if fmt in ("txt", "text"):
# #             fallback_type = "text_document"
# #         elif fmt in ("md", "markdown"):
# #             fallback_type = "markdown_document"
# #
# #         classification.update({
# #             "type": result["type"] if result["confidence"] >= 0.5 else fallback_type,
# #             "confidence": result["confidence"],
# #             "method": "document_classifier",
# #             "explain": result
# #         })
# #
# #     # ==================== PRESENTATION FORMATS ====================
# #     elif fmt in ("pptx", "ppt", "odp", "key", "presentation"):
# #         dc = DocumentClassifier()  # Can handle presentations too
# #         result = dc.classify(parsed)
# #
# #         classification.update({
# #             "type": result["type"] if result["confidence"] >= 0.5 else "presentation",
# #             "confidence": result["confidence"],
# #             "method": "presentation_classifier",
# #             "explain": result
# #         })
# #
# #     # ==================== IMAGE FORMATS ====================
# #     elif fmt in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "heif",
# #                  "tiff", "tif", "svg", "ico", "image"):
# #         ic = ImageClassifier()
# #         result = ic.classify(parsed)
# #
# #         classification.update({
# #             "type": result["type"],
# #             "confidence": result["confidence"],
# #             "method": "image_classifier",
# #             "explain": result
# #         })
# #
# #     # ==================== EMAIL FORMATS ====================
# #     elif fmt in ("eml", "msg", "mbox", "email", "emlx"):
# #         ec = EmailClassifier()
# #         result = ec.classify(parsed)
# #
# #         classification.update({
# #             "type": result["type"],
# #             "confidence": result["confidence"],
# #             "method": "email_classifier",
# #             "explain": result
# #         })
# #
# #     # ==================== ARCHIVE FORMATS ====================
# #     elif fmt in ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "archive"):
# #         classification.update({
# #             "type": "archive",
# #             "confidence": 0.9,
# #             "method": "format_detection",
# #             "explain": {"format": fmt, "reason": "Archive format detected"}
# #         })
# #
# #     # ==================== CODE/SCRIPT FORMATS ====================
# #     elif fmt in ("py", "js", "ts", "java", "cpp", "c", "h", "cs", "rb", "go",
# #                  "rs", "php", "html", "css", "scss", "json", "xml", "yaml", "yml",
# #                  "sh", "bash", "sql", "r", "matlab", "ipynb", "code"):
# #         classification.update({
# #             "type": "code_file",
# #             "confidence": 0.95,
# #             "method": "format_detection",
# #             "explain": {
# #                 "language": fmt,
# #                 "reason": f"Code file format ({fmt}) detected"
# #             }
# #         })
# #
# #     # ==================== DATA FORMATS ====================
# #     elif fmt in ("json", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf"):
# #         dc = DocumentClassifier()
# #         result = dc.classify(parsed)
# #
# #         classification.update({
# #             "type": result["type"] if result["confidence"] >= 0.5 else "data_file",
# #             "confidence": max(result["confidence"], 0.8),
# #             "method": "data_format_classifier",
# #             "explain": result
# #         })
# #
# #     # ==================== AUDIO FORMATS ====================
# #     elif fmt in ("mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "audio"):
# #         classification.update({
# #             "type": "audio",
# #             "confidence": 0.9,
# #             "method": "format_detection",
# #             "explain": {"format": fmt, "reason": "Audio format detected"}
# #         })
# #
# #     # ==================== VIDEO FORMATS ====================
# #     elif fmt in ("mp4", "avi", "mov", "wmv", "flv", "mkv", "webm", "m4v", "video"):
# #         classification.update({
# #             "type": "video",
# #             "confidence": 0.9,
# #             "method": "format_detection",
# #             "explain": {"format": fmt, "reason": "Video format detected"}
# #         })
# #
# #     # ==================== CAD/DESIGN FORMATS ====================
# #     elif fmt in ("dwg", "dxf", "stl", "obj", "fbx", "blend", "3ds", "cad", "design"):
# #         classification.update({
# #             "type": "cad_design",
# #             "confidence": 0.9,
# #             "method": "format_detection",
# #             "explain": {"format": fmt, "reason": "CAD/Design format detected"}
# #         })
# #
# #     # ==================== DATABASE FORMATS ====================
# #     elif fmt in ("db", "sqlite", "sql", "mdb", "accdb", "database"):
# #         classification.update({
# #             "type": "database",
# #             "confidence": 0.9,
# #             "method": "format_detection",
# #             "explain": {"format": fmt, "reason": "Database format detected"}
# #         })
# #
# #     # ==================== FALLBACK ====================
# #     else:
# #         # Try document classifier as last resort
# #         dc = DocumentClassifier()
# #         result = dc.classify(parsed)
# #
# #         classification.update({
# #             "type": result["type"] if result["confidence"] >= 0.5 else "unknown",
# #             "confidence": result["confidence"],
# #             "method": "document_classifier_fallback",
# #             "explain": {
# #                 **result,
# #                 "original_format": fmt,
# #                 "reason": f"Unknown format '{fmt}', attempted document classification"
# #             }
# #         })
# #
# #     # Store classification in state
# #     state["classification"] = classification
# #
# #     # ==================== ROUTER LOGIC ====================
# #     # Pick parser/analyzer from registry based on doc_type
# #     doc_type = classification["type"]
# #     entry = DOCUMENT_REGISTRY.get(doc_type, {})
# #     parser_cls = entry.get("parser")
# #     analyzer_cls = entry.get("analyzer")
# #
# #     # Fallback to generic implementations if not in registry
# #     if not parser_cls:
# #         from app.services.parsers.universal_parser import UniversalParser
# #         parser_cls = UniversalParser
# #
# #     if not analyzer_cls:
# #         from app.services.analyzers.generic_analyzer import GenericAnalyzer
# #         analyzer_cls = GenericAnalyzer
# #
# #     state["router"] = {
# #         "parser": parser_cls,
# #         "analyzer": analyzer_cls,
# #         "registry_entry": entry,
# #         "debug_info": {
# #             "doc_type": doc_type,
# #             "original_format": fmt,
# #             "confidence": classification["confidence"],
# #             "method": classification["method"]
# #         }
# #     }
# #
# #     return state
#
#
# # def analyze_node(state: DocumentState) -> Dict[str, Any]:
# #     logger.info("🤖 [AnalyzeNode] Generating summary + key points")
# #
# #     analyzed_data = state.get("analyzed_data", {})
# #     doc_type = state.get("classification", {}).get("type", "generic")
# #     logger.info(f"   📄 analyze_node what doc type is it: {doc_type}")
# #     # Pick the correct summary engine
# #     #entry = SUMMARY_REGISTRY.get(doc_type, SUMMARY_REGISTRY["generic"])
# #
# #    # summary_fn = entry["summary"]
# #     #keypoints_fn = entry["keypoints"]
# #
# #     summary = summary_fn(analyzed_data)
# #     key_points = keypoints_fn(analyzed_data)
# #     logger.info(f"   📄 Found summary Type")
# #     logger.info(f"   📁 Found key_points")
# #
# #     return {
# #         "summary": summary,
# #         "key_points": key_points,
# #         "classification": state.get("classification"),  # ✅ Preserve classification
# #         "format": state.get("format")
# #     }
#
#
# # def analyze_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node 5: Generate summary and extract key points"""
# #     logger.info(f"🤖 [AnalyzeNode] Analyzing document")
# #
# #     # ✅ DEBUG: Log entire state
# #     logger.info(f"📦 State keys available: {list(state.keys())}")
# #     logger.info(f"📦 analyzed_data present: {'analyzed_data' in state}")
# #
# #     try:
# #         text = state.get("cleaned_text") or state.get("raw_text", "")
# #         classification = state.get("classification", {})
# #         doc_type = classification.get("type", "document")
# #
# #         # ✅ Get analyzed data if available
# #         analyzed_data = state.get("analyzed_data", {})
# #
# #         # ✅ DEBUG: Log what we got
# #         logger.info(f"📊 analyzed_data type: {type(analyzed_data)}")
# #         logger.info(f"📊 analyzed_data keys: {list(analyzed_data.keys()) if analyzed_data else 'EMPTY'}")
# #         logger.info(f"📊 analyzed_data is truthy: {bool(analyzed_data)}")
# #
# #         if not text:
# #             return {
# #                 "summary": "",
# #                 "key_points": []
# #             }
# #
# #         # ✅ For bank statements, use validated account summary
# #         # if doc_type == "bank_statement" and analyzed_data:
# #         #     summary = _generate_bank_statement_summary(analyzed_data)
# #         #     key_points = _extract_bank_statement_key_points(analyzed_data)
# #         #
# #         #     return {
# #         #         "summary": summary,
# #         #         "key_points": key_points
# #         #     }
# #         logger.info(f"📊 analyzed_data summarize started")
# #
# #         # For other document types, use raw text (existing logic)
# #         prompt = f"""Summarize this {doc_type} in 150 words or less.
# # Focus on key facts, numbers, dates, and main points.
# #
# # Document:
# # {text[:15000]}
# #
# # Provide a clear, concise summary:"""
# #
# #         response = ollama_client.generate(
# #             model="phi3:mini",
# #             prompt=prompt
# #         )
# #
# #         summary = response['response'].strip()
# #
# #         logger.info(f"📊 analyzed_data summarize completed")
# #
# #         logger.info(f"📊 analyzed_data extracting keypoints started")
# #
# #         # Extract key points
# #         key_points_prompt = f"""Extract 5 key points from this document.
# # Return as a simple numbered list.
# #
# # Document:
# # {text[:10000]}
# #
# # Key Points:"""
# #
# #         kp_response = ollama_client.generate(
# #             model="phi3:mini",
# #             prompt=key_points_prompt
# #         )
# #
# #         # Parse key points
# #         key_points = []
# #         for line in kp_response['response'].split('\n'):
# #             line = line.strip().lstrip('0123456789.-*• ')
# #             if line and len(line) > 10:
# #                 key_points.append(line)
# #
# #         key_points = key_points[:5]
# #         logger.info(f"📊 analyzed_data extracting keypoints completed")
# #
# #         logger.info(f"✅ [AnalyzeNode] Generated summary ({len(summary)} chars), {len(key_points)} key points")
# #
# #         return {
# #             "summary": summary,
# #             "key_points": key_points
# #         }
# #
# #     except Exception as e:
# #         logger.error(f"❌ [AnalyzeNode] Error: {e}")
# #         return {
# #             "summary": text[:500] + "..." if text else "",
# #             "key_points": [],
# #             "errors": [{
# #                 "node": "analyze",
# #                 "error": str(e),
# #                 "timestamp": datetime.utcnow().isoformat()
# #             }]
# #         }
#
#
#
# # from concurrent.futures import ThreadPoolExecutor, as_completed
# # import time
# #
# # # Module-level executor — reuse across calls, avoid thread spawn overhead
# # _EXECUTOR = ThreadPoolExecutor(max_workers=3)
# #
# #
# # def _call_summary(text: str, doc_type: str) -> tuple[str, str]:
# #     """Run summary generation in a thread."""
# #     prompt = f"""Summarize this {doc_type} in 150 words or less.
# # Focus on key facts, numbers, dates, and main points.
# #
# # Document:
# # {text[:15000]}
# #
# # Provide a clear, concise summary:"""
# #
# #     response = ollama_client.generate(model="phi3:mini", prompt=prompt)
# #     return ("summary", response["response"].strip())
# #
# #
# # def _call_key_points(text: str) -> tuple[str, list[str]]:
# #     """Run key point extraction in a thread."""
# #     prompt = f"""Extract 5 key points from this document.
# # Return as a simple numbered list.
# #
# # Document:
# # {text[:10000]}
# #
# # Key Points:"""
# #
# #     response = ollama_client.generate(model="phi3:mini", prompt=prompt)
# #
# #     key_points = []
# #     for line in response["response"].split("\n"):
# #         line = line.strip().lstrip("0123456789.-*• ")
# #         if line and len(line) > 10:
# #             key_points.append(line)
# #
# #     return ("key_points", key_points[:5])
# #
# #
# # def analyze_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node 5: Generate summary and extract key points — parallel LLM calls."""
# #     logger.info("🤖 [AnalyzeNode] Analyzing document")
# #
# #     text = state.get("cleaned_text") or state.get("raw_text", "")
# #     classification = state.get("classification", {})
# #     doc_type = classification.get("type", "document")
# #
# #     if not text:
# #         return {"summary": "", "key_points": []}
# #
# #     logger.info("📊 [AnalyzeNode] Launching parallel LLM calls...")
# #     t0 = time.perf_counter()
# #
# #     # Submit both tasks simultaneously
# #     futures = {
# #         _EXECUTOR.submit(_call_summary, text, doc_type): "summary",
# #         _EXECUTOR.submit(_call_key_points, text): "key_points",
# #     }
# #
# #     results = {"summary": "", "key_points": []}
# #     errors = []
# #
# #     # Collect as they finish — no fixed ordering dependency
# #     for future in as_completed(futures):
# #         task_name = futures[future]
# #         try:
# #             label, value = future.result()
# #             results[label] = value
# #             logger.info(f"📊 [AnalyzeNode] '{label}' completed")
# #         except Exception as e:
# #             logger.error(f"❌ [AnalyzeNode] '{task_name}' failed: {e}")
# #             errors.append({
# #                 "node": "analyze",
# #                 "task": task_name,
# #                 "error": str(e),
# #                 "timestamp": datetime.utcnow().isoformat(),
# #             })
# #
# #     elapsed = time.perf_counter() - t0
# #     logger.info(
# #         f"✅ [AnalyzeNode] Done in {elapsed:.2f}s — "
# #         f"summary ({len(results['summary'])} chars), "
# #         f"{len(results['key_points'])} key points"
# #     )
# #
# #     if errors:
# #         results["errors"] = errors
# #
# #     # Fallback: if summary failed entirely, use truncated raw text
# #     if not results["summary"]:
# #         results["summary"] = text[:500] + "..." if text else ""
# #
# #     return results
#
#
# from concurrent.futures import ThreadPoolExecutor, as_completed
# import time
#
# _EXECUTOR = ThreadPoolExecutor(max_workers=3)
#
# #{text[:15000]}
# #{text[:10000]}
# def _call_summary(text: str, doc_type: str) -> tuple[str, str]:
#     logger.info(f"📊 [summary] thread started at {time.perf_counter():.2f}")
#     prompt = f"""Summarize this {doc_type} in 150 words or less.
# Focus on key facts, numbers, dates, and main points.
#
# Document:
# {text[:1500]}
#
# Provide a clear, concise summary:"""
#
#     response = ollama_client.generate(model=config.model, prompt=prompt)
#     logger.info(f"📊 [summary] thread done at {time.perf_counter():.2f}")
#     return ("summary", response["response"].strip())
#
#
# def _call_key_points(text: str) -> tuple[str, list[str]]:
#     logger.info(f"📊 [key_points] thread started at {time.perf_counter():.2f}")
#     prompt = f"""Extract 5 key points from this document.
# Return as a simple numbered list.
#
# Document:
# {text[:1000]}
#
# Key Points:"""
#
#     response = ollama_client.generate(model=config.model, prompt=prompt)
#     logger.info(f"📊 [key_points] thread done at {time.perf_counter():.2f}")
#
#     key_points = []
#     for line in response["response"].split("\n"):
#         line = line.strip().lstrip("0123456789.-*• ")
#         if line and len(line) > 10:
#             key_points.append(line)
#
#     return ("key_points", key_points[:5])
#
#
# def analyze_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 5: Generate summary and extract key points — parallel LLM calls."""
#     logger.info("🤖 [AnalyzeNode] Analyzing document")
#
#     text = extract_clean_or_raw_text(state)
#
#     classification = state.get("classification", {})
#     doc_type = classification.get("type", "document")
#
#     if not text:
#         return {"summary": "", "key_points": []}
#
#     t0 = time.perf_counter()
#     logger.info(f"📊 [AnalyzeNode] Submitting threads at {t0:.2f}")
#
#     futures = {
#         _EXECUTOR.submit(_call_summary, text, doc_type): "summary",
#         _EXECUTOR.submit(_call_key_points, text): "key_points",
#     }
#
#     results = {"summary": "", "key_points": []}
#     errors = []
#
#     for future in as_completed(futures):
#         task_name = futures[future]
#         try:
#             label, value = future.result()
#             results[label] = value
#             logger.info(f"📊 [AnalyzeNode] '{label}' completed at {time.perf_counter():.2f}")
#         except Exception as e:
#             logger.error(f"❌ [AnalyzeNode] '{task_name}' failed: {e}")
#             errors.append({
#                 "node": "analyze",
#                 "task": task_name,
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat(),
#             })
#
#     elapsed = time.perf_counter() - t0
#     logger.info(
#         f"✅ [AnalyzeNode] Done in {elapsed:.2f}s — "
#         f"summary ({len(results['summary'])} chars), "
#         f"{len(results['key_points'])} key points"
#     )
#
#     if errors:
#         results["errors"] = errors
#     if not results["summary"]:
#         results["summary"] = text[:500] + "..." if text else ""
#
#     return results
#
#
# def extract_clean_or_raw_text(state):
#     text1 = ""
#     if "analyzed_data" in state:
#         if "raw_text" in state.get("analyzed_data"):
#             text1 = state.get("analyzed_data", "").get("raw_text")
#         elif "cleaned_text" in state.get("analyzed_data"):
#             text1 = state.get("analyzed_data", "").get("cleaned_text")
#     text2 = state.get("cleaned_text") or state.get("raw_text")
#     text = " ".join((text2, text1))
#     return text
#
#
# def _extract_bank_statement_key_points(analyzed_data: Dict) -> list:
#     deposits = analyzed_data.get("total_credits", 0)
#     withdrawals = analyzed_data.get("total_debits", 0)
#     net_change = analyzed_data.get("net_change", 0)
#
#     health_score = analyzed_data.get("financial_health_score", 0)
#     health_status = analyzed_data.get("financial_health_status", "Unknown")
#
#     total_txns = analyzed_data.get("total_transactions", analyzed_data.get("all_transactions_count", 0))
#
#     return [
#         f"Total deposits: ${deposits:,.2f}",
#         f"Total withdrawals: ${withdrawals:,.2f}",
#         f"Net change: ${net_change:,.2f}",
#         f"Financial health: {health_score}/100 - {health_status}",
#         f"Transactions processed: {total_txns}"
#     ]
#
#
# def _generate_bank_statement_summary(analyzed_data: Dict) -> str:
#     # Account info (optional)
#     account_holder = analyzed_data.get("account_holder", "Account Holder")
#
#     # Statement period (optional)
#     period_start = analyzed_data.get("statement_start_date", "N/A")
#     period_end = analyzed_data.get("statement_end_date", "N/A")
#
#     # Flat metrics (REAL keys from your analyzer)
#     deposits = analyzed_data.get("total_credits", 0)
#     withdrawals = analyzed_data.get("total_debits", 0)
#     net_change = analyzed_data.get("net_change", 0)
#     num_transactions = analyzed_data.get("total_transactions", analyzed_data.get("all_transactions_count", 0))
#
#     summary = (
#         f"For the period of {period_start} to {period_end}, "
#         f"the account statement for {account_holder} shows "
#         f"a total deposit of ${deposits:,.2f} and "
#         f"a total withdrawal of ${withdrawals:,.2f}, "
#         f"resulting in a net change of ${net_change:,.2f}. "
#         f"A total of {num_transactions} transactions were processed during this period."
#     )
#
#     return summary
#
#
# def _generate_generic_summary(analyzed_data: Dict) -> str:
#     """
#     Generic fallback summary for any document type.
#     Produces a clean, high‑level description based on available structured fields.
#     """
#
#     if not analyzed_data:
#         return "This document has been processed, but no structured data was extracted."
#
#     keys = list(analyzed_data.keys())
#
#     # Describe the document based on its structured fields
#     return (
#         f"This document contains {len(keys)} structured fields extracted during analysis. "
#         f"Key fields include: {', '.join(keys[:8])}. "
#         f"The extracted data provides an overview of the document's main attributes and content."
#     )
#
#
# def _extract_generic_key_points(analyzed_data: Dict) -> list:
#     """
#     Generic fallback key points for any document type.
#     Returns the first few structured fields as bullet points.
#     """
#
#     if not analyzed_data:
#         return ["No structured data was extracted from this document."]
#
#     key_points = []
#
#     # Show up to 5 meaningful fields
#     for k, v in list(analyzed_data.items())[:5]:
#         # Convert lists/dicts to readable strings
#         if isinstance(v, (list, dict)):
#             v = str(v)
#
#         key_points.append(f"{k}: {v}")
#
#     return key_points
#
#
# # def _generate_bank_statement_summary(analyzed_data: Dict) -> str:
# #     """
# #     Generate summary using ACTUAL data that exists
# #     """
# #     # ✅ Use transaction_summary which HAS the data
# #     transaction_summary = analyzed_data.get("transaction_summary", {})
# #     account_info = analyzed_data.get("account_information", {})
# #     statement_period = analyzed_data.get("statement_period", {})
# #     transactions = analyzed_data.get("transactions", [])
# #
# #     account_holder = account_info.get("account_holder", "Account Holder")
# #     period_start = statement_period.get("start_date", "N/A")
# #     period_end = statement_period.get("end_date", "N/A")
# #
# #     # ✅ Get from transaction_summary (which HAS the correct data!)
# #     deposits = transaction_summary.get("total_credits", 0)
# #     withdrawals = transaction_summary.get("total_debits", 0)
# #     num_transactions = transaction_summary.get("total_transactions", len(transactions))
# #     net_change = transaction_summary.get("net_change", 0)
# #
# #     # Get fees from fee_analysis
# #     fee_analysis = analyzed_data.get("fee_analysis", {})
# #     service_fees = fee_analysis.get("total_fees", 0)
# #
# #     # Calculate ending balance
# #     balances = analyzed_data.get("balances", {})
# #     ending_balance = balances.get("ending_balance", 0)
# #
# #     # If no ending balance, calculate it
# #     if ending_balance == 0 and balances.get("beginning_balance", 0) > 0:
# #         ending_balance = balances.get("beginning_balance", 0) + net_change
# #
# #     summary = (
# #         f"For the period of {period_start} to {period_end}, "
# #         f"the account statement for {account_holder} shows "
# #         f"a total deposit of ${deposits:,.2f} and "
# #         f"a total withdrawal of ${withdrawals:,.2f}, "
# #         f"resulting in a net change of ${net_change:,.2f}. "
# #         f"A total of {num_transactions} transactions were processed during this period."
# #     )
# #
# #     return summary
#
#
# # def _extract_bank_statement_key_points(analyzed_data: Dict) -> list:
# #     """Extract key points from actual data"""
# #     transaction_summary = analyzed_data.get("transaction_summary", {})
# #     financial_health = analyzed_data.get("financial_health", {})
# #
# #     deposits = transaction_summary.get("total_credits", 0)
# #     withdrawals = transaction_summary.get("total_debits", 0)
# #     net_change = transaction_summary.get("net_change", 0)
# #
# #     health_score = financial_health.get("score", 0)
# #     health_status = financial_health.get("status", "Unknown")
# #
# #     key_points = [
# #         f"Total deposits: ${deposits:,.2f}",
# #         f"Total withdrawals: ${withdrawals:,.2f}",
# #         f"Net change: ${net_change:,.2f}",
# #         f"Financial health: {health_score}/100 - {health_status}",
# #         f"Transactions processed: {transaction_summary.get('total_transactions', 0)}"
# #     ]
# #
# #     return key_points
#
#
# # def _generate_bank_statement_summary(analyzed_data: Dict) -> str:
# #     """
# #     Generate summary using VALIDATED bank statement data - NO AI HALLUCINATION
# #     """
# #     try:
# #         # Extract validated data
# #         account_info = analyzed_data.get("account_information", {})
# #         account_summary = analyzed_data.get("account_summary", {})
# #         statement_period = analyzed_data.get("statement_period", {})
# #         transactions = analyzed_data.get("transactions", [])
# #
# #         account_holder = account_info.get("account_holder", "Account Holder")
# #         period_start = statement_period.get("start_date", "N/A")
# #         period_end = statement_period.get("end_date", "N/A")
# #
# #         # ✅ CRITICAL: Log what we actually have
# #         logger.info(f"📊 Building summary from:")
# #         logger.info(f"   account_summary: {account_summary}")
# #         logger.info(f"   statement_period: {statement_period}")
# #
# #         # Get values from account_summary
# #         beginning_balance = account_summary.get("beginning_balance", 0)
# #         deposits = account_summary.get("deposits", 0)
# #         withdrawals = abs(account_summary.get("withdrawals", 0))
# #         service_fees = abs(account_summary.get("service_fees", 0))
# #         ending_balance = account_summary.get("ending_balance", 0)
# #
# #         total_withdrawals = withdrawals + service_fees
# #         num_transactions = len(transactions)
# #
# #         # ✅ NO AI - Just use a simple template with FACTS
# #         summary = (
# #             f"For the period of {period_start} to {period_end}, "
# #             f"the account statement for {account_holder} shows "
# #             f"a total deposit of ${deposits:,.2f} and "
# #             f"a total withdrawal of ${total_withdrawals:,.2f} "
# #             f"(including ${service_fees:,.2f} in service fees), "
# #             f"resulting in an ending balance of ${ending_balance:,.2f}. "
# #             f"A total of {num_transactions} transactions were processed during this period."
# #         )
# #
# #         logger.info(f"✅ Generated summary: {summary}")
# #
# #         return summary
# #
# #     except Exception as e:
# #         logger.error(f"❌ Summary generation failed: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #         return "Summary generation failed."
# #
# #
# # def _extract_bank_statement_key_points(analyzed_data: Dict) -> list:
# #     """
# #     Extract key points - NO AI HALLUCINATION
# #     """
# #     try:
# #         account_summary = analyzed_data.get("account_summary", {})
# #         financial_health = analyzed_data.get("financial_health", {})
# #
# #         deposits = account_summary.get("deposits", 0)
# #         withdrawals = abs(account_summary.get("withdrawals", 0))
# #         service_fees = abs(account_summary.get("service_fees", 0))
# #         ending_balance = account_summary.get("ending_balance", 0)
# #         beginning_balance = account_summary.get("beginning_balance", 0)
# #
# #         total_withdrawals = withdrawals + service_fees
# #         net_change = deposits - total_withdrawals
# #
# #         health_status = financial_health.get("status", "Unknown")
# #         health_score = financial_health.get("score", 0)
# #
# #         # ✅ Simple, factual key points
# #         key_points = [
# #             f"Beginning balance: ${beginning_balance:,.2f}",
# #             f"Total deposits: ${deposits:,.2f}",
# #             f"Total withdrawals: ${total_withdrawals:,.2f} (includes ${service_fees:,.2f} in fees)",
# #             f"Net change: ${net_change:,.2f}",
# #             f"Ending balance: ${ending_balance:,.2f}"
# #         ]
# #
# #         return key_points
# #
# #     except Exception as e:
# #         logger.error(f"❌ Key points extraction failed: {e}")
# #         return []
#
#
# # def _generate_bank_statement_summary(analyzed_data: Dict) -> str:
# #     """
# #     Generate summary using VALIDATED bank statement data
# #     """
# #     try:
# #         # Extract validated data
# #         # account_info = analyzed_data.get("account_information", {})
# #         # account_summary = analyzed_data.get("account_summary", {})
# #         # statement_period = analyzed_data.get("statement_period", {})
# #         # transactions = analyzed_data.get("transactions", [])
# #         #
# #         # account_holder = account_info.get("account_holder", "Account Holder")
# #         # period_start = statement_period.get("start_date", "N/A")
# #         # period_end = statement_period.get("end_date", "N/A")
# #         #
# #         # # ✅ Use account_summary (validated from PDF)
# #         # beginning_balance = account_summary.get("beginning_balance", 0)
# #         # deposits = account_summary.get("deposits", 0)
# #         # withdrawals = abs(account_summary.get("withdrawals", 0))
# #         # service_fees = abs(account_summary.get("service_fees", 0))
# #         # ending_balance = account_summary.get("ending_balance", 0)
# #
# #         # ✅ LOG what we're receiving
# #         logger.info(f"📊 Generating summary from analyzed_data")
# #         logger.info(f"   Keys available: {list(analyzed_data.keys())}")
# #
# #         # Extract validated data
# #         account_info = analyzed_data.get("account_information", {})
# #         account_summary = analyzed_data.get("account_summary", {})
# #         statement_period = analyzed_data.get("statement_period", {})
# #         transactions = analyzed_data.get("transactions", [])
# #
# #         # ✅ LOG extracted values
# #         logger.info(f"   Account summary: {account_summary}")
# #         logger.info(f"   Statement period: {statement_period}")
# #         logger.info(f"   Transaction count: {len(transactions)}")
# #
# #         account_holder = account_info.get("account_holder", "Account Holder")
# #         period_start = statement_period.get("start_date", "N/A")
# #         period_end = statement_period.get("end_date", "N/A")
# #
# #         # ✅ Use account_summary
# #         beginning_balance = account_summary.get("beginning_balance", 0)
# #         deposits = account_summary.get("deposits", 0)
# #         withdrawals = abs(account_summary.get("withdrawals", 0))
# #         service_fees = abs(account_summary.get("service_fees", 0))
# #         ending_balance = account_summary.get("ending_balance", 0)
# #
# #         # ✅ LOG the values we're using
# #         logger.info(f"   📋 Values being sent to AI:")
# #         logger.info(f"      Beginning: ${beginning_balance:,.2f}")
# #         logger.info(f"      Deposits: ${deposits:,.2f}")
# #         logger.info(f"      Withdrawals: ${withdrawals:,.2f}")
# #         logger.info(f"      Service Fees: ${service_fees:,.2f}")
# #         logger.info(f"      Ending: ${ending_balance:,.2f}")
# #
# #         total_withdrawals = withdrawals + service_fees
# #         num_transactions = len(transactions)
# #
# #         # ✅ Generate summary with AI using ONLY validated data
# #         prompt = f"""Generate a concise 2-3 sentence summary of this bank statement.
# #
# # **USE ONLY THESE EXACT NUMBERS:**
# #
# # Account: {account_holder}
# # Period: {period_start} to {period_end}
# # Beginning Balance: ${beginning_balance:,.2f}
# # Total Deposits: ${deposits:,.2f}
# # Total Withdrawals: ${withdrawals:,.2f}
# # Service Fees: ${service_fees:,.2f}
# # Ending Balance: ${ending_balance:,.2f}
# # Total Transactions: {num_transactions}
# #
# # Write a professional summary using ONLY these exact numbers. Do not make up or calculate different numbers:"""
# #
# #         response = ollama_client.generate(
# #             model="phi3:mini",
# #             prompt=prompt,
# #             options={"temperature": 0.1}  # Very low for accuracy
# #         )
# #
# #         return response['response'].strip()
# #
# #     except Exception as e:
# #         logger.error(f"Bank statement summary generation failed: {e}")
# #         # Fallback to simple text
# #         return f"Bank statement for {account_holder} from {period_start} to {period_end}. Total deposits: ${deposits:,.2f}, Total withdrawals: ${total_withdrawals:,.2f}, Ending balance: ${ending_balance:,.2f}."
#
#
# # def _extract_bank_statement_key_points(analyzed_data: Dict) -> list:
# #     """
# #     Extract key points from validated bank statement data
# #     """
# #     try:
# #         account_summary = analyzed_data.get("account_summary", {})
# #         financial_health = analyzed_data.get("financial_health", {})
# #         insights = analyzed_data.get("insights", [])
# #         recommendations = analyzed_data.get("recommendations", [])
# #
# #         deposits = account_summary.get("deposits", 0)
# #         withdrawals = abs(account_summary.get("withdrawals", 0))
# #         service_fees = abs(account_summary.get("service_fees", 0))
# #         ending_balance = account_summary.get("ending_balance", 0)
# #
# #         total_withdrawals = withdrawals + service_fees
# #         net_change = deposits - total_withdrawals
# #
# #         health_status = financial_health.get("status", "Unknown")
# #         health_score = financial_health.get("score", 0)
# #
# #         key_points = [
# #             f"Total deposits: ${deposits:,.2f} and total withdrawals: ${total_withdrawals:,.2f} (including ${service_fees:,.2f} in fees)",
# #             f"Net change: {'Saved' if net_change > 0 else 'Spent'} ${abs(net_change):,.2f} during statement period",
# #             f"Ending balance: ${ending_balance:,.2f}",
# #             f"Financial health score: {health_score}/100 - {health_status}"
# #         ]
# #
# #         # Add top insight if available
# #         if insights:
# #             key_points.append(insights[0])
# #
# #         # Add top recommendation if available
# #         if recommendations and len(recommendations) > 0:
# #             top_rec = recommendations[0]
# #             key_points.append(f"Recommendation: {top_rec.get('description', top_rec.get('title', ''))}")
# #
# #         return key_points[:5]
# #
# #     except Exception as e:
# #         logger.error(f"Key points extraction failed: {e}")
# #         return []
#
#
# def search_prep_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 6: Generate embeddings and keywords"""
#     logger.info(f"🤖 [SearchPrepNode] Generating embeddings")
#
#     try:
#         text = extract_clean_or_raw_text(state)
#
#         if not text or len(text) < 50:
#             logger.warning("⚠️ Text too short for meaningful embedding")
#             return {
#                 "embedding": None,
#                 "keywords": []
#             }
#
#         # Generate embedding (768-dim)
#         text_for_embedding = text[:8000]  # Increased from 5000 (more context)
#         embedding = embedding_model.encode(text_for_embedding)
#
#         logger.info(f"🔍 [SearchPrepNode] Embedding shape: {embedding.shape}")
#         logger.info(f"🔍 [SearchPrepNode] Embedding dtype: {embedding.dtype}")
#
#         # Extract keywords
#         stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
#                       'of', 'with', 'is', 'was', 'are', 'were', 'be', 'been', 'being'}
#
#         words = re.findall(r'\b[a-z]{3,}\b', text.lower())
#         words = [w for w in words if w not in stop_words]
#         counter = Counter(words)
#         keywords = [word for word, count in counter.most_common(50)]  # Increased from 30
#
#         # Convert to list for JSON serialization
#         embedding_list = embedding.tolist()
#
#         logger.info(f"✅ [SearchPrepNode] Generated {len(embedding_list)}-dim embedding (768-dim, high quality)")
#         logger.info(f"✅ [SearchPrepNode] Extracted {len(keywords)} keywords")
#
#         return {
#             "embedding": embedding_list,
#             "keywords": keywords
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [SearchPrepNode] Error: {e}")
#         return {
#             "embedding": None,
#             "keywords": [],
#             "errors": [{
#                 "node": "search_prep",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }
#
#
# def quality_check_node(state: DocumentState) -> Dict[str, Any]:
#     """Node: Check document quality"""
#     logger.info(f"🤖 [QualityCheckNode] Checking quality")
#
#     try:
#         text = state.get("cleaned_text", "")
#         doc_metadata = state.get("doc_metadata", {})
#
#         quality_issues = []
#
#         # Check 1: Text length
#         if len(text) < 100:
#             quality_issues.append({
#                 "issue": "Text too short",
#                 "severity": "warning",
#                 "details": f"Only {len(text)} characters extracted"
#             })
#
#         # Check 2: Missing classification
#         classification = state.get("classification", {})
#         if not classification or classification.get("confidence", 0) < 0.3:
#             quality_issues.append({
#                 "issue": "Low classification confidence",
#                 "severity": "warning",
#                 "details": f"Confidence: {classification.get('confidence', 0):.2%}"
#             })
#
#         # Check 3: Parsing errors
#         errors = state.get("errors", [])
#         if errors:
#             quality_issues.append({
#                 "issue": "Parsing errors detected",
#                 "severity": "critical",
#                 "details": f"{len(errors)} errors found"
#             })
#
#         logger.info(f"✅ [QualityCheckNode] Found {len(quality_issues)} quality issues")
#
#         return {
#             "quality_issues": quality_issues
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [QualityCheckNode] Error: {e}")
#         return {
#             "quality_issues": [{
#                 "issue": "Quality check failed",
#                 "severity": "warning",
#                 "details": str(e)
#             }]
#         }
#
#
# def insight_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 8: Generate insights"""
#     logger.info(f"🤖 [InsightNode] Generating insights")
#
#     try:
#         summary = state.get("summary", "")
#         classification = state.get("classification", {})
#
#         if not summary or len(summary) < 50:
#             return {"insights": []}
#
#         doc_type = classification.get("type", "document")
#
#         prompt = f"""Analyze this {doc_type} and provide 3 key insights or recommendations.
#
# Document summary: {summary[:1000]}
#
# Provide exactly 3 insights in this format:
# 1. [Insight title]: [Brief description]
# 2. [Insight title]: [Brief description]
# 3. [Insight title]: [Brief description]"""
#
#         response = ollama_client.generate(
#             model=config.model,
#             prompt=prompt
#         )
#
#         # Parse insights
#         insights = []
#         for line in response['response'].split('\n'):
#             line = line.strip()
#             if line and line[0].isdigit() and ':' in line:
#                 insight = line.lstrip('0123456789. ').strip()
#                 title, description = insight.split(':', 1)
#                 insights.append({
#                     "title": title.strip(),
#                     "description": description.strip(),
#                     "category": "analysis"
#                 })
#
#         insights = insights[:3]
#
#         logger.info(f"✅ [InsightNode] Generated {len(insights)} insights")
#
#         return {"insights": insights}
#
#     except Exception as e:
#         logger.error(f"❌ [InsightNode] Error: {e}")
#         return {
#             "insights": [],
#             "errors": [{
#                 "node": "insight",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }
#
#
# def metrics_node(state: DocumentState) -> Dict[str, Any]:
#     """Node 9: Calculate metrics"""
#     logger.info(f"🤖 [MetricsNode] Calculating metrics")
#
#     try:
#         text = extract_clean_or_raw_text(state)
#
#         tables = state.get("tables", [])
#         classification = state.get("classification", {})
#
#         metrics = {}
#
#         if text:
#             word_count = len(text.split())
#             metrics["word_count"] = word_count
#             metrics["char_count"] = len(text)
#             metrics["estimated_read_time"] = f"{word_count // 200} min"
#
#         metrics["tables_found"] = len(tables)
#
#         if classification:
#             metrics["document_type"] = classification.get("type", "Unknown")
#             metrics["classification_confidence"] = classification.get("confidence", 0)
#
#         metrics["processing_complete"] = True
#         metrics["has_errors"] = len(state.get("errors", [])) > 0
#
#         logger.info(f"✅ [MetricsNode] Calculated {len(metrics)} metrics")
#
#         return {"metrics": metrics}
#
#     except Exception as e:
#         logger.error(f"❌ [MetricsNode] Error: {e}")
#         return {
#             "metrics": {},
#             "errors": [{
#                 "node": "metrics",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }
#
#
# def sanitize_text(text: str) -> str:
#     """Normalize unicode characters to ASCII-safe equivalents."""
#     if not text:
#         return text
#     import unicodedata
#     # Normalize smart quotes and common unicode to ASCII equivalents
#     replacements = {
#         '\u2019': "'", '\u2018': "'",  # smart single quotes
#         '\u201c': '"', '\u201d': '"',  # smart double quotes
#         '\u2013': '-', '\u2014': '--',  # en/em dash
#         '\u2026': '...',  # ellipsis
#         '\u00a0': ' ',  # non-breaking space
#     }
#     for char, replacement in replacements.items():
#         text = text.replace(char, replacement)
#
#     # Fallback: normalize and encode safely
#     return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
#
# def finalize_node(state: DocumentState) -> Dict[str, Any]:
#     logger.info("🤖 [FinalizeNode] Saving to database")
#
#     try:
#         from app.core.database import SessionLocal
#         from app.models.document import Document
#         from datetime import datetime
#         from app.utils.json_utils import clean_metadata_for_json, validate_json_serializable
#         from app.utils.file_classifier import detect_file_capabilities
#
#         # ---------------------------------------------------------
#         # 1. Validate document ID
#         # ---------------------------------------------------------
#         document_id = state.get("document_id")
#         if not document_id:
#             raise Exception("document_id missing from state")
#
#         db = SessionLocal()
#         doc = db.query(Document).filter(Document.id == document_id).first()
#         if not doc:
#             db.close()
#             raise Exception(f"Document {document_id} not found")
#
#         # ---------------------------------------------------------
#         # 2. Extract pipeline outputs
#         # ---------------------------------------------------------
#         classification = state.get("classification", {})
#         insights = state.get("insights", [])
#         key_points = state.get("key_points", [])
#         keywords = state.get("keywords", [])
#         metrics = state.get("metrics", {})
#         quality_issues = state.get("quality_issues", [])
#         errors = state.get("errors", [])
#
#         # Analyzer output
#         analyzed_data = state.get("analyzed_data", {})
#
#         # ---------------------------------------------------------
#         # 4. Extract tables
#         # ---------------------------------------------------------
#         doc_metadata_from_state = state.get("doc_metadata", {})
#         tables = (
#                 doc_metadata_from_state.get("tables")
#                 or analyzed_data.get("structured_data", {}).get("tables")
#                 or []
#         )
#
#         # ---------------------------------------------------------
#         # 5. Detect capabilities
#         # ---------------------------------------------------------
#         filename = state.get("filename", "")
#         doc_type = classification.get("type", "unknown")
#         raw_text = state.get("cleaned_text", state.get("raw_text", ""))
#
#         capabilities = detect_file_capabilities(
#             filename=filename,
#             doc_type=doc_type,
#             metadata=doc_metadata_from_state,
#             raw_text=raw_text
#         )
#
#         # ---------------------------------------------------------
#         # 6. Build final metadata object (CLEAN + COLLAPSED)
#         # ---------------------------------------------------------
#         complete_metadata = {
#             "classification": classification,
#             "insights": insights,
#             "key_points": key_points,
#             "keywords": keywords,
#             "metrics": metrics,
#             "quality_issues": quality_issues,
#             "errors": errors,
#             # Analyzer output (optional legacy)
#             "analyzed_data": analyzed_data,
#             # Capabilities
#             "capabilities": capabilities.to_dict(),
#         }
#
#         # ---------------------------------------------------------
#         # 7. Clean + validate JSON
#         # ---------------------------------------------------------
#         complete_metadata = clean_metadata_for_json(complete_metadata)
#         if not validate_json_serializable(complete_metadata):
#             raise Exception("Metadata is not JSON serializable")
#
#         # ---------------------------------------------------------
#         # 8. Save document
#         # ---------------------------------------------------------
#         doc.raw_text = sanitize_text(raw_text)
#         doc.summary = sanitize_text(state.get("summary", ""))
#         doc.doc_type = doc_type
#         doc.status = "completed"
#         doc.processed_at = datetime.utcnow()
#         doc.doc_metadata = complete_metadata
#
#         # Embedding
#         embedding = state.get("embedding")
#         if embedding:
#             doc.embedding = [float(x) for x in embedding]
#
#         db.commit()
#         db.refresh(doc)
#         db.close()
#
#         logger.info("✅ [FinalizeNode] Document saved successfully!")
#
#         # ---------------------------------------------------------
#         # 9. Return updated state
#         # ---------------------------------------------------------
#         state["status"] = "completed"
#         return state
#
#     except Exception as e:
#         logger.error(f"❌ [FinalizeNode] Error: {e}")
#         return {
#             "errors": state.get("errors", []) + [{"node": "finalize", "error": str(e)}],
#             "status": "failed"
#         }
#
#
# def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
#     """
#     Node: Document-Specific Analysis
#     Performs specialized analysis based on document type with guardrails.
#     """
#     logger.info("📊 [DocumentAnalysisNode] Starting document-specific analysis...")
#
#     try:
#         # -----------------------------
#         # 1. Extract classification
#         # -----------------------------
#         classification = state.get("classification", {})
#         doc_type = classification.get("type", "unknown")
#         confidence = classification.get("confidence", 0.0)
#
#         file_path = state.get("file_path", "")
#         raw_text = extract_clean_or_raw_text(state)
#         metadata = state.get("doc_metadata", {})
#
#         logger.info(f"   📄 Document Type: {doc_type} (confidence: {confidence:.2f})")
#         logger.info(f"   📁 File Path: {file_path}")
#         logger.info(f"   📝 Text Length: {len(raw_text)} characters")
#
#         # -----------------------------
#         # 2. Analyzer Guardrails
#         # -----------------------------
#         from app.services.analyzer_registry import get_analyzer, DOCUMENT_REGISTRY
#
#         def analyzer_is_eligible(doc_type: str, text: str) -> bool:
#             t = text.lower()
#
#             RULES = {
#                 "invoice": ["invoice", "bill to", "amount due", "subtotal"],
#                 "bank_statement": ["statement", "account", "balance", "transaction"],
#                 "payroll": ["gross pay", "net pay", "deductions", "pay period"],
#                 "utility_bill": ["billing period", "usage", "meter", "kwh", "therms"],
#                 "purchase_order": ["purchase order", "po number", "vendor"],
#                 "receipt": ["receipt", "total", "payment method"],
#                 "tax_form": ["w-2", "1099", "1040", "irs"],
#                 "contract": ["agreement", "party", "terms", "hereby"],
#                 "resume": ["experience", "education", "skills"],
#                 "transcript": ["course", "credits", "gpa", "semester"],
#             }
#
#             if doc_type not in RULES:
#                 return True  # generic types always allowed
#
#             hits = sum(1 for k in RULES[doc_type] if k in t)
#             return hits >= 1  # must match at least one anchor
#
#         # -----------------------------
#         # 3. Validate analyzer choice (multi-signal guardrail)
#         # -----------------------------
#
#         # 1. High-confidence classification → trust the classifier
#         if confidence >= 0.80:
#             logger.info(f"🛂 Guardrail bypassed: high-confidence {doc_type}")
#         else:
#             # 2. Medium/low confidence → require structural signals
#             if not analyzer_is_eligible(doc_type, raw_text):
#                 logger.warning(f"🚧 Guardrail: doc_type '{doc_type}' rejected — insufficient structural signals")
#                 doc_type = "document"  # fallback to generic analyzer
#
#         analyzer = get_analyzer(doc_type)
#         logger.info(f"   🔍 Using analyzer: {analyzer.__class__.__name__}")
#
#         # -----------------------------
#         # 4. Perform analysis
#         # -----------------------------
#         analysis_results = analyzer.analyze(
#             file_path=file_path,
#             text=raw_text,
#             metadata={
#                 **metadata,
#                 "classified_type": doc_type,
#                 "classification_confidence": confidence,
#                 "file_name": state.get("filename", ""),
#                 "file_type": state.get("format", "")
#             }
#         )
#
#         logger.info("   ✅ Analysis complete")
#         logger.info(f"   📊 Result type: {analysis_results.get('type', 'unknown')}")
#         logger.info(f"   🎨 Has advanced analytics: {analysis_results.get('has_advanced_analytics', False)}")
#         logger.info(f"   💡 Insights: {len(analysis_results.get('insights', []))}")
#         logger.info(f"   ⚠️ Alerts: {len(analysis_results.get('alerts', []))}")
#
#         # -----------------------------
#         # 5. Update metadata
#         # -----------------------------
#         updated_metadata = {
#             **metadata,
#             "specialized_analysis": True,
#             "analyzer_used": analyzer.__class__.__name__,
#             "has_advanced_analytics": analysis_results.get("has_advanced_analytics", False)
#         }
#
#         return {
#             "analyzed_data": analysis_results,
#             "doc_metadata": updated_metadata,
#             "classification": classification,
#             "format": state.get("format")
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [DocumentAnalysisNode] Error: {e}", exc_info=True)
#
#         classification = state.get("classification", {})
#         doc_type = classification.get("type", "unknown")
#
#         return {
#             "analyzed_data": {
#                 "type": doc_type,
#                 "error": str(e),
#                 "summary": f"Analysis failed: {str(e)}",
#                 "has_advanced_analytics": False
#             },
#             "doc_metadata": state.get("doc_metadata", {}),
#             "classification": classification,
#             "format": state.get("format"),
#             "errors": state.get("errors", []) + [{
#                 "node": "document_analysis",
#                 "error": str(e),
#                 "timestamp": datetime.utcnow().isoformat()
#             }]
#         }
#
#
# # def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
# #     """
# #     Node: Document-Specific Analysis
# #     Performs specialized analysis based on document type
# #     """
# #     logger.info("📊 [DocumentAnalysisNode] Starting document-specific analysis...")
# #
# #     try:
# #         # ✅ Get classification from state (set by parse_node)
# #         classification = state.get("classification", {})
# #         doc_type = classification.get("type", "unknown")
# #         confidence = classification.get("confidence", 0.0)
# #
# #         file_path = state.get("file_path", "")
# #         #raw_text = state.get("cleaned_text") or state.get("raw_text", "")
# #         raw_text = extract_clean_or_raw_text(state)
# #
# #         metadata = state.get("doc_metadata", {})
# #
# #         logger.info(f"   📄 Document Type: {doc_type} (confidence: {confidence:.2f})")
# #         logger.info(f"   📁 File Path: {file_path}")
# #         logger.info(f"   📝 Text Length: {len(raw_text)} characters")
# #
# #         # Import analyzer registry
# #         from app.services.analyzer_registry import get_analyzer
# #
# #         # Get appropriate analyzer
# #         logger.info(f"   🔍 Getting analyzer for doc_type: {doc_type}")
# #         analyzer = get_analyzer(doc_type)
# #         logger.info(f"   🔍 Using analyzer: {analyzer.__class__.__name__}")
# #
# #         # Perform analysis (synchronous)
# #         analysis_results = analyzer.analyze(
# #             file_path=file_path,
# #             text=raw_text,
# #             metadata={
# #                 **metadata,
# #                 'classified_type': doc_type,
# #                 'classification_confidence': confidence,
# #                 'file_name': state.get('filename', ''),
# #                 'file_type': state.get('format', '')
# #             }
# #         )
# #
# #         logger.info(f"   ✅ Analysis complete")
# #         logger.info(f"   📊 Result type: {analysis_results.get('type', 'unknown')}")
# #
# #         has_advanced = analysis_results.get('has_advanced_analytics', False)
# #         logger.info(f"   🎨 Has advanced analytics: {has_advanced}")
# #         logger.info(f"   💡 Insights: {len(analysis_results.get('insights', []))}")
# #         logger.info(f"   ⚠️  Alerts: {len(analysis_results.get('alerts', []))}")
# #
# #         # Update metadata
# #         updated_metadata = {
# #             **state.get("doc_metadata", {}),
# #             "specialized_analysis": True,
# #             "analyzer_used": analyzer.__class__.__name__,
# #             "has_advanced_analytics": has_advanced
# #         }
# #
# #         # ✅ Return with preserved classification
# #         return {
# #             "analyzed_data": analysis_results,
# #             "doc_metadata": updated_metadata,
# #             "classification": classification,  # ✅ Preserve classification
# #             "format": state.get("format")  # ✅ Preserve format
# #         }
# #
# #     except Exception as e:
# #         logger.error(f"❌ [DocumentAnalysisNode] Error: {e}")
# #         logger.error("Traceback:", exc_info=True)
# #
# #         # ✅ Return error state with preserved classification
# #         classification = state.get("classification", {})
# #         doc_type = classification.get("type", "unknown")
# #
# #         return {
# #             "analyzed_data": {
# #                 "type": doc_type,
# #                 "error": str(e),
# #                 "summary": f"Analysis failed: {str(e)}",
# #                 "has_advanced_analytics": False
# #             },
# #             "doc_metadata": state.get("doc_metadata", {}),
# #             "classification": classification,  # ✅ Preserve classification even on error
# #             "format": state.get("format"),
# #             "errors": state.get("errors", []) + [{
# #                 "node": "document_analysis",
# #                 "error": str(e),
# #                 "timestamp": datetime.utcnow().isoformat()
# #             }]
# #         }
#
#
# # def finalize_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node: Finalize and save to database"""
# #     logger.info(f"🤖 [FinalizeNode] Saving to database")
# #
# #     try:
# #         from app.core.database import SessionLocal
# #         from app.models.document import Document
# #         from datetime import datetime
# #         from app.utils.json_utils import clean_metadata_for_json, validate_json_serializable
# #         from app.utils.file_classifier import detect_file_capabilities  # ✅ Import
# #
# #         document_id = state.get("document_id")
# #
# #         if not document_id:
# #             raise Exception("document_id missing from state")
# #
# #         logger.info(f"💾 [FinalizeNode] Saving document: {document_id}")
# #
# #         db = SessionLocal()
# #         doc = db.query(Document).filter(Document.id == document_id).first()
# #
# #         if not doc:
# #             db.close()
# #             raise Exception(f"Document {document_id} not found")
# #
# #         # Get all data from state
# #         doc_metadata_from_state = state.get("doc_metadata", {})
# #         classification = state.get("classification", {})
# #         insights = state.get("insights", [])
# #         key_points = state.get("key_points", [])
# #         keywords = state.get("keywords", [])
# #         metrics = state.get("metrics", {})
# #         quality_issues = state.get("quality_issues", [])
# #         errors = state.get("errors", [])
# #
# #         # Get tables
# #         tables = doc_metadata_from_state.get("tables", [])
# #
# #         # ✅ DETECT FILE CAPABILITIES
# #         filename = state.get("filename", "")
# #         doc_type = classification.get("type", "unknown")
# #         raw_text = state.get("cleaned_text", state.get("raw_text", ""))
# #
# #         capabilities = detect_file_capabilities(
# #             filename=filename,
# #             doc_type=doc_type,
# #             metadata=doc_metadata_from_state,
# #             raw_text=raw_text
# #         )
# #
# #         logger.info(f"✅ Detected capabilities: {capabilities.primary_content_type}")
# #         logger.info(f"   - Visualization: {capabilities.supports_visualization}")
# #         logger.info(f"   - Q&A: {capabilities.supports_qa}")
# #         logger.info(f"   - Tables: {capabilities.has_tables}")
# #
# #         analytics = state.get("analytics", {})
# #         ui_config = state.get("ui_config", {})
# #
# #         # Build complete metadata
# #         complete_metadata = {
# #             "tables": tables,
# #             "classification": classification,
# #             "insights": insights,
# #             "key_points": key_points,
# #             "keywords": keywords,
# #             "metrics": metrics,
# #             "quality_issues": quality_issues,
# #             "errors": errors,
# #             "analytics": analytics,
# #             "ui_config": ui_config,
# #             "capabilities": capabilities.to_dict()  # ✅ Add capabilities
# #         }
# #
# #         # Merge with doc_metadata
# #         for key, value in doc_metadata_from_state.items():
# #             if key not in complete_metadata:
# #                 complete_metadata[key] = value
# #
# #         # Ensure JSON serializable
# #         complete_metadata = clean_metadata_for_json(complete_metadata)
# #
# #         if not validate_json_serializable(complete_metadata):
# #             logger.error(f"❌ Metadata still not JSON serializable after cleaning!")
# #             raise Exception("Metadata is not JSON serializable")
# #
# #         logger.info(f"✅ Metadata is JSON serializable")
# #
# #         # Log what we're saving
# #         logger.info(f"💾 Saving document with:")
# #         logger.info(f"   - Tables: {len(tables)}")
# #         logger.info(f"   - Insights: {len(insights)}")
# #         logger.info(f"   - Key points: {len(key_points)}")
# #         logger.info(f"   - Keywords: {len(keywords)}")
# #         logger.info(f"   - Primary type: {capabilities.primary_content_type}")
# #
# #         if tables:
# #             for i, table in enumerate(tables):
# #                 logger.info(f"   - Table {i + 1}: {table.get('rows', 0)} rows × {len(table.get('columns', []))} cols")
# #
# #         # Update document
# #         doc.raw_text = raw_text
# #         doc.summary = state.get("summary", "")
# #         doc.doc_type = doc_type
# #         doc.status = "completed"
# #         doc.processed_at = datetime.utcnow()
# #
# #         # Save embedding (flexible size now)
# #         embedding = state.get("embedding")
# #         if embedding and isinstance(embedding, list) and len(embedding) > 0:
# #             doc.embedding = [float(x) for x in embedding]
# #             logger.info(f"   - Embedding: saved ({len(embedding)} dims)")
# #
# #         # Merge analyzed_data into complete_metadata BEFORE saving
# #         complete_metadata.setdefault("analyzed_data", state.get("analyzed_data", {}))
# #
# #         # Save metadata
# #         doc.doc_metadata = complete_metadata
# #
# #         db.commit()
# #
# #         # Verify
# #         db.refresh(doc)
# #         saved_tables = doc.doc_metadata.get("tables", []) if doc.doc_metadata else []
# #         logger.info(f"✅ Verified: Document has {len(saved_tables)} tables after commit")
# #
# #         db.close()
# #
# #         logger.info(f"✅ [FinalizeNode] Document saved successfully!")
# #
# #         return {"status": "completed"}
# #
# #     except Exception as e:
# #         logger.error(f"❌ [FinalizeNode] Error: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #         return {
# #             "errors": state.get("errors", []) + [{"node": "finalize", "error": str(e)}],
# #             "status": "failed"
# #         }
#
#
# # def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
# #     logger.info("🤖 [DocumentAnalysisNode] Running document-specific analysis")
# #
# #     try:
# #         from app.services.analyzer_registry import get_analyzer_registry
# #
# #         classification = state.get("classification", {})
# #         doc_type = classification.get("type", "unknown")
# #         confidence = classification.get("confidence", 0.0)
# #         file_path = state.get("file_path", "")
# #         raw_text = state.get("cleaned_text") or state.get("raw_text", "")
# #         metadata = state.get("doc_metadata", {})
# #
# #         # Extract parsed tables (from UniversalParser → ParseNode)
# #         tables = metadata.get("tables", [])
# #
# #         registry = get_analyzer_registry()
# #
# #         # ---------------------------------------------------------
# #         # CASE 0: If tables exist → ALWAYS use them for data_table/spreadsheet
# #         # ---------------------------------------------------------
# #         if tables and doc_type in ["data_table", "spreadsheet"]:
# #             logger.info("📊 Using parsed tables directly for data_table/spreadsheet")
# #
# #             state["analytics"] = {
# #                 "metrics": {},
# #                 "tables": tables,
# #                 "charts": [],
# #                 "insights": metadata.get("insights", []),
# #             }
# #             return state
# #
# #         # ---------------------------------------------------------
# #         # CASE 1: No specialized analyzer → fallback
# #         # ---------------------------------------------------------
# #         if not registry.has_analyzer(doc_type):
# #             logger.info(f"⏭️ No specialized analyzer for '{doc_type}' — using fallback")
# #
# #             state["analytics"] = {
# #                 "metrics": {},
# #                 "tables": tables,
# #                 "charts": [],
# #                 "insights": metadata.get("insights", []),
# #             }
# #             return state
# #
# #         # ---------------------------------------------------------
# #         # CASE 2: Low confidence → fallback
# #         # ---------------------------------------------------------
# #         if confidence < 0.5:
# #             logger.warning(f"⚠️ Low confidence ({confidence:.1%}), using fallback")
# #
# #             state["analytics"] = {
# #                 "metrics": {},
# #                 "tables": tables,
# #                 "charts": [],
# #                 "insights": metadata.get("insights", []),
# #             }
# #             return state
# #
# #         # ---------------------------------------------------------
# #         # CASE 3: Run specialized analyzer
# #         # ---------------------------------------------------------
# #         analyzer = registry.get(doc_type)
# #         analysis_results = analyzer.analyze(
# #             file_path=file_path,
# #             text=raw_text,
# #             metadata=metadata
# #         )
# #
# #         if not analysis_results:
# #             logger.warning(f"❌ {doc_type} analyzer returned no results — using fallback")
# #
# #             state["analytics"] = {
# #                 "metrics": {},
# #                 "tables": tables,
# #                 "charts": [],
# #                 "insights": metadata.get("insights", []),
# #             }
# #             return state
# #
# #         # ---------------------------------------------------------
# #         # SUCCESS — Unified analytics only
# #         # ---------------------------------------------------------
# #         # analytics = analysis_results.get("analytics", {})
# #         #
# #         # # Ensure tables always exist
# #         # if "tables" not in analytics:
# #         #     analytics["tables"] = tables
# #         #
# #         # # Ensure insights always exist
# #         # if "insights" not in analytics:
# #         #     analytics["insights"] = metadata.get("insights", [])
# #         #
# #         # # Ensure metrics always exist
# #         # if "metrics" not in analytics:
# #         #     analytics["metrics"] = {}
# #         #
# #         # # Ensure charts always exist
# #         # if "charts" not in analytics:
# #         #     analytics["charts"] = []
# #
# #         state["analyzed_data"] = analysis_results
# #
# #         return state
# #
# #     except Exception as e:
# #         logger.error(f"❌ [DocumentAnalysisNode] Error: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #
# #         # Fallback on error
# #         state["analytics"] = {
# #             "metrics": {},
# #             "tables": metadata.get("tables", []),
# #             "charts": [],
# #             "insights": metadata.get("insights", []),
# #         }
# #         return state
#
#
# # def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node 5: Run document-specific analysis (bank statements, invoices, etc.)"""
# #     logger.info(f"🤖 [DocumentAnalysisNode] Running document-specific analysis")
# #
# #     try:
# #         from app.services.analyzer_registry import get_analyzer_registry
# #
# #         doc_type = state.get("classification", {}).get("type", "unknown")
# #         file_path = state.get("file_path", "")
# #         text = state.get("cleaned_text", state.get("raw_text", ""))
# #         metadata = state.get("doc_metadata", {})
# #
# #         # Get registry
# #         registry = get_analyzer_registry()
# #
# #         # Extract parsed tables (from UniversalParser → ParseNode)
# #         tables = metadata.get("tables", [])
# #
# #         # Check if analyzer exists for this document type
# #         if not registry.has_analyzer(doc_type):
# #             logger.info(f"⏭️ No specialized analyzer for '{doc_type}'")
# #             return {}
# #
# #         # Get the analyzer, then call analyze()
# #         analyzer = registry.get(doc_type)
# #
# #         logger.info(f"📊 Running {analyzer.__class__.__name__} for {doc_type}")
# #
# #         # Call the analyzer's analyze method
# #         analysis_results = analyzer.analyze(
# #             file_path=file_path,
# #             text=text,
# #             metadata=metadata
# #         )
# #
# #         # ✅ GENERIC: Dynamically merge ALL keys from analysis results
# #         current_metadata = state.get("doc_metadata", {})
# #
# #         # Add analyzer metadata
# #         current_metadata["specialized_analysis"] = True
# #         current_metadata["analyzer_used"] = analyzer.__class__.__name__
# #         current_metadata["analyzer_type"] = analysis_results.get("type", doc_type)
# #
# #         # ✅ Dynamically merge all analysis results (not hardcoded)
# #         # This works for ANY document type with ANY structure
# #         for key, value in analysis_results.items():
# #             if value is not None and key not in ['type']:  # Skip 'type' as we handle it separately
# #                 current_metadata[key] = value
# #
# #         # Log what was merged
# #         analysis_keys = [k for k in analysis_results.keys() if k != 'type']
# #         logger.info(f"✅ [DocumentAnalysisNode] Merged {len(analysis_keys)} analysis keys")
# #         logger.info(f"   📋 Keys: {', '.join(analysis_keys[:10])}{'...' if len(analysis_keys) > 10 else ''}")
# #
# #         # Log specific metrics if available
# #         if 'transactions' in analysis_results:
# #             logger.info(f"   📊 Transactions: {len(analysis_results.get('transactions', []))}")
# #         if 'key_metrics' in analysis_results:
# #             logger.info(f"   📈 Key Metrics: {len(analysis_results.get('key_metrics', {}))}")
# #         if 'insights' in analysis_results:
# #             logger.info(f"   💡 Insights: {len(analysis_results.get('insights', []))}")
# #         if 'structured_data' in analysis_results:
# #             logger.info(f"   📦 Structured Data: {len(analysis_results.get('structured_data', {}))}")
# #
# #         # ---------------------------------------------------------
# #         # SUCCESS — Unified analytics only
# #         # ---------------------------------------------------------
# #         analytics = analysis_results.get("analytics", {})
# #
# #         # Ensure tables always exist
# #         if "tables" not in analytics:
# #             analytics["tables"] = tables
# #
# #         # Ensure insights always exist
# #         if "insights" not in analytics:
# #             analytics["insights"] = metadata.get("insights", [])
# #
# #         # Ensure metrics always exist
# #         if "metrics" not in analytics:
# #             analytics["metrics"] = {}
# #
# #         # Ensure charts always exist
# #         if "charts" not in analytics:
# #             analytics["charts"] = []
# #
# #         return {
# #             "analyzed_data": analysis_results,  # Keep full results in analyzed_data
# #             "doc_metadata": current_metadata,  # Dynamically merged metadata
# #             "analytics": analytics
# #         }
# #
# #     except Exception as e:
# #         logger.error(f"❌ [DocumentAnalysisNode] Error: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #         return {}
#
# # def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node: Route to document-specific analyzer based on classification"""
# #     logger.info(f"🤖 [DocumentAnalysisNode] Running document-specific analysis")
# #
# #     try:
# #         from app.services.analyzer_registry import get_analyzer_registry
# #
# #         classification = state.get("classification", {})
# #         doc_type = classification.get("type", "unknown")
# #         confidence = classification.get("confidence", 0.0)
# #         file_path = state.get("file_path", "")
# #         raw_text = state.get("cleaned_text") or state.get("raw_text", "")
# #         metadata = state.get("doc_metadata", {})
# #
# #         # Get registry
# #         registry = get_analyzer_registry()
# #
# #         # Check if analyzer exists
# #         if not registry.has_analyzer(doc_type):
# #             logger.info(f"⏭️ No specialized analyzer for '{doc_type}'")
# #             return {}
# #
# #         # Only analyze if decent confidence
# #         if confidence < 0.5:
# #             logger.warning(f"⚠️ Low confidence ({confidence:.1%}), skipping specialized analysis")
# #             return {}
# #
# #         logger.info(f"🔍 Running {doc_type} analyzer (confidence: {confidence:.1%})...")
# #
# #         # ✅ FIX: Get the analyzer from registry, then call analyze()
# #         analyzer = registry.get(doc_type)
# #
# #         if not analyzer:
# #             logger.error(f"❌ Analyzer for '{doc_type}' not found in registry")
# #             return {}
# #
# #         # ✅ FIX: Call analyzer.analyze() with correct parameters
# #         # analysis_results = analyzer.analyze(
# #         #     file_path=file_path,
# #         #     text=raw_text,
# #         #     metadata=metadata
# #         # )
# #
# #         analyzer = registry.get(doc_type)
# #         analysis_results = analyzer.analyze(file_path=file_path,
# #             text=raw_text,
# #             metadata=metadata)
# #         state["analyzed_data"] = analysis_results
# #
# #
# #         # ✅ DEBUG: Log what we're returning
# #         logger.info(f"🔍 Analysis returned {len(analysis_results)} keys: {list(analysis_results.keys())[:10]}")
# #
# #         if not analysis_results:
# #             logger.warning(f"❌ {doc_type} analyzer returned no results")
# #             return {}
# #
# #         # ✅ LOG specific analytics
# #         if "transactions" in analysis_results:
# #             logger.info(f"   📊 Transactions: {len(analysis_results.get('transactions', []))}")
# #         if "account_information" in analysis_results:
# #             logger.info(f"   🏦 Account info: {analysis_results['account_information']}")
# #         if "top_merchants" in analysis_results:
# #             logger.info(f"   🛍️ Top merchants: {len(analysis_results.get('top_merchants', []))}")
# #
# #         # Update state with results
# #         current_metadata = state.get("doc_metadata", {})
# #
# #         # ✅ Merge ALL analysis results into metadata (generic for all doc types)
# #         for key, value in analysis_results.items():
# #             if value is not None:
# #                 current_metadata[key] = value
# #
# #         # Also keep a copy under doc-specific key for backwards compatibility
# #         current_metadata[f"{doc_type}_analytics"] = analysis_results
# #
# #         # Extract key points from analysis
# #         key_points = state.get("key_points", [])
# #         insights = state.get("insights", [])
# #
# #         # Add document-specific insights
# #         doc_insights = analysis_results.get("insights", [])
# #         if isinstance(doc_insights, list):
# #             for insight in doc_insights[:3]:
# #                 if isinstance(insight, dict):
# #                     insights.append(insight)
# #                 elif isinstance(insight, str):
# #                     insights.append({
# #                         "title": doc_type.replace('_', ' ').title(),
# #                         "description": str(insight),
# #                         "category": doc_type
# #                     })
# #
# #         logger.info(f"✅ {doc_type} analysis complete")
# #         logger.info(f"   📦 Metadata now has {len(current_metadata)} keys")
# #
# #         result = {
# #             "analyzed_data": analysis_results,  # Full analysis results
# #             "doc_metadata": current_metadata,  # Merged into metadata
# #             "key_points": key_points,
# #             "insights": insights
# #         }
# #
# #         # ✅ DEBUG: Confirm return value
# #         logger.info(f"✅ Returning {len(result)} keys: {list(result.keys())}")
# #
# #         return result
# #
# #     except Exception as e:
# #         logger.error(f"❌ [DocumentAnalysisNode] Error: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #
# #         return {
# #             "analyzed_data": {},
# #             "errors": [{
# #                 "node": "document_specific_analysis",
# #                 "error": str(e),
# #                 "timestamp": datetime.utcnow().isoformat()
# #             }]
# #         }
#
#
# def math_analysis_node(state: DocumentState) -> Dict[str, Any]:
#     """Node: Math Path - Privacy-first numerical analysis"""
#     logger.info(f"🤖 [MathAnalysisNode] Processing via Math Path")
#
#     try:
#         from app.agents.specialist_router import get_router
#         from app.services.math_processor import get_math_processor
#
#         doc_type = state.get("classification", {}).get("type", "unknown")
#         metadata = state.get("doc_metadata", {})
#         tables = metadata.get("tables", [])
#
#         if not tables:
#             logger.info("⏭️ No tables, skipping math analysis")
#             return {}
#
#         router = get_router()
#         processor = get_math_processor()
#
#         # Get operations for this document type
#         operations = router.get_math_operations(doc_type)
#
#         if not operations:
#             logger.info("⏭️ No math operations defined for this type")
#             return {}
#
#         # Process each table
#         math_results = {}
#         for i, table in enumerate(tables):
#             results = processor.process_table(table, operations)
#             math_results[f"table_{i + 1}"] = results
#
#         # Update metadata
#         current_metadata = state.get("doc_metadata", {})
#         current_metadata["math_analysis"] = math_results
#         current_metadata["analysis_method"] = "math_path"
#
#         logger.info(f"✅ [MathAnalysisNode] Completed calculations")
#
#         return {
#             "doc_metadata": current_metadata
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [MathAnalysisNode] Error: {e}")
#         import traceback
#         logger.error(traceback.format_exc())
#         return {}
#
#
# def entity_linking_node(state: DocumentState) -> Dict[str, Any]:
#     """Node: Extract entities and link documents"""
#     logger.info(f"🤖 [EntityLinkingNode] Extracting entities and linking")
#
#     try:
#         from app.agents.specialist_router import get_router
#         from app.services.entity_linker import get_entity_linker
#
#         document_id = state.get("document_id")
#         doc_type = state.get("classification", {}).get("type", "unknown")
#         text = state.get("cleaned_text", state.get("raw_text", ""))
#
#         router = get_router()
#         linker = get_entity_linker()
#
#         # Get entity types to extract for this document type
#         entity_types = router.get_entities_to_extract(doc_type)
#
#         if not entity_types:
#             logger.info("⏭️ No entities configured for this document type")
#             return {}
#
#         # Extract entities
#         entities = linker.extract_entities(text, entity_types)
#
#         if not entities:
#             logger.info("⏭️ No entities found")
#             return {}
#
#         logger.info(f"📎 Extracted entities: {entities}")
#
#         # Find related documents
#         related_ids = linker.link_documents(document_id, entities)
#
#         # Save to database
#         linker.save_entity_links(document_id, entities, related_ids)
#
#         logger.info(f"✅ [EntityLinkingNode] Linked to {len(related_ids)} documents")
#
#         return {
#             "doc_metadata": {
#                 **state.get("doc_metadata", {}),
#                 "extracted_entities": entities,
#                 "related_documents": related_ids
#             }
#         }
#
#     except Exception as e:
#         logger.error(f"❌ [EntityLinkingNode] Error: {e}")
#         import traceback
#         logger.error(traceback.format_exc())
#         return {}
#
# # def document_specific_analysis_node(state: DocumentState) -> Dict[str, Any]:
# #     """Node: Route to document-specific analyzer based on classification"""
# #     logger.info(f"🤖 [DocumentAnalysisNode] Running document-specific analysis")
# #
# #     try:
# #         from app.services.analyzer_registry import get_analyzer_registry
# #
# #         classification = state.get("classification", {})
# #         doc_type = classification.get("type", "unknown")
# #         confidence = classification.get("confidence", 0.0)
# #         file_path = state.get("file_path", "")
# #         raw_text = state.get("cleaned_text") or state.get("raw_text", "")
# #         metadata = state.get("doc_metadata", {})
# #
# #         # Get registry
# #         registry = get_analyzer_registry()
# #
# #         # Check if analyzer exists
# #         if not registry.has_analyzer(doc_type):
# #             logger.info(f"⏭️ No specialized analyzer for '{doc_type}'")
# #             return {}
# #
# #         # Only analyze if decent confidence
# #         if confidence < 0.5:
# #             logger.warning(f"⚠️ Low confidence ({confidence:.1%}), skipping specialized analysis")
# #             return {}
# #
# #         logger.info(f"🔍 Running {doc_type} analyzer (confidence: {confidence:.1%})...")
# #
# #         # Run analyzer
# #         analysis_results = registry.analyze(
# #             doc_type=doc_type,
# #             file_path=file_path,
# #             raw_text=raw_text,
# #             metadata=metadata
# #         )
# #
# #         if not analysis_results:
# #             logger.warning(f"❌ {doc_type} analyzer returned no results")
# #             return {}
# #
# #         # Update state with results
# #         current_metadata = state.get("doc_metadata", {})
# #         current_metadata[f"{doc_type}_analytics"] = analysis_results
# #
# #         # Extract key points from analysis
# #         key_points = state.get("key_points", [])
# #         insights = state.get("insights", [])
# #
# #         # Add document-specific insights
# #         doc_insights = analysis_results.get("insights", [])
# #         if isinstance(doc_insights, list):
# #             for insight in doc_insights[:3]:
# #                 if isinstance(insight, dict):
# #                     insights.append(insight)
# #                 else:
# #                     insights.append({
# #                         "title": doc_type.replace('_', ' ').title(),
# #                         "description": str(insight),
# #                         "category": doc_type
# #                     })
# #
# #         logger.info(f"✅ {doc_type} analysis complete")
# #
# #         return {
# #             "doc_metadata": current_metadata,
# #             "key_points": key_points,
# #             "insights": insights
# #         }
# #
# #     except Exception as e:
# #         logger.error(f"❌ [DocumentAnalysisNode] Error: {e}")
# #         import traceback
# #         logger.error(traceback.format_exc())
# #
# #         return {
# #             "errors": [{
# #                 "node": "document_specific_analysis",
# #                 "error": str(e),
# #                 "timestamp": datetime.utcnow().isoformat()
# #             }]
# #         }
# # def ui_config_node(state: DocumentState):
# #     return UIConfigNode().run(state)
