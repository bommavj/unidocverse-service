import logging
from typing import List, Dict, Any
import pdfplumber

logger = logging.getLogger(__name__)

# ── Ghostscript cross-platform fix ──────────────────────────
import os, sys, ctypes

def _load_ghostscript():
    """Force-load Ghostscript library for all platforms."""
    candidates = []

    if sys.platform == "darwin":
        # Apple Silicon + Intel Homebrew paths
        candidates = [
            "/opt/homebrew/lib/libgs.dylib",       # Apple Silicon
            "/usr/local/lib/libgs.dylib",           # Intel Mac
        ]
    elif sys.platform == "win32":
        # Windows — Ghostscript installs to Program Files
        import glob
        candidates = glob.glob("C:/Program Files/gs/*/bin/gsdll64.dll") + \
                     glob.glob("C:/Program Files/gs/*/bin/gsdll32.dll")
    else:
        # Linux
        candidates = [
            "/usr/lib/libgs.so",
            "/usr/lib/x86_64-linux-gnu/libgs.so.10",
            "/usr/lib/aarch64-linux-gnu/libgs.so.10",
        ]

    for lib_path in candidates:
        if os.path.exists(lib_path):
            try:
                ctypes.CDLL(lib_path)
                # Add parent dir to library path
                lib_dir = os.path.dirname(lib_path)
                if sys.platform == "darwin":
                    os.environ["DYLD_LIBRARY_PATH"] = lib_dir + ":" + os.environ.get("DYLD_LIBRARY_PATH", "")
                elif sys.platform != "win32":
                    os.environ["LD_LIBRARY_PATH"] = lib_dir + ":" + os.environ.get("LD_LIBRARY_PATH", "")
                return True
            except Exception:
                continue
    return False

_load_ghostscript()
# ─────────────────────────────────────────────────────────────

# Try importing Camelot (optional advanced extractor)
try:
    import camelot

    HAS_CAMELOT = True
except ImportError:
    HAS_CAMELOT = False


def extract_pdf_tables(file_path: str) -> List[Dict[str, Any]]:
    """
    Universal PDF table extraction with intelligent fallbacks.

    Strategy:
    1. Try pdfplumber (fast, works for 80% of PDFs)
    2. If results look poor, try Camelot lattice mode (bordered tables)
    3. If still poor, try Camelot stream mode (borderless tables)
    4. Return best result

    This handles:
    - Tax forms, financial statements, invoices
    - Contracts with embedded tables
    - Medical/insurance forms
    - Immigration documents
    - Spreadsheets exported to PDF
    """

    tables: List[Dict[str, Any]] = []

    try:
        logger.info(f"📄 Opening PDF for table extraction: {file_path}")

        # Method 1: pdfplumber (fast, good for most documents)
        pdfplumber_tables = extract_with_pdfplumber(file_path)

        # Evaluate quality
        quality = evaluate_extraction_quality(pdfplumber_tables)
        logger.info(f"📊 pdfplumber quality: {quality['score']:.2f} ({quality['reason']})")

        # If quality is good, use pdfplumber results
        if quality['score'] >= 0.7:
            logger.info(f"✅ Using pdfplumber results: {len(pdfplumber_tables)} tables")
            return pdfplumber_tables

        # Method 2: Camelot lattice (for bordered tables)
        if HAS_CAMELOT:
            logger.info("🔄 Trying Camelot lattice mode...")
            camelot_lattice = extract_with_camelot(file_path, flavor='lattice')
            lattice_quality = evaluate_extraction_quality(camelot_lattice)
            logger.info(f"📊 Camelot lattice quality: {lattice_quality['score']:.2f}")

            if lattice_quality['score'] > quality['score']:
                quality = lattice_quality
                tables = camelot_lattice

            # Method 3: Camelot stream (for borderless tables)
            logger.info("🔄 Trying Camelot stream mode...")
            camelot_stream = extract_with_camelot(file_path, flavor='stream')
            stream_quality = evaluate_extraction_quality(camelot_stream)
            logger.info(f"📊 Camelot stream quality: {stream_quality['score']:.2f}")

            if stream_quality['score'] > quality['score']:
                quality = stream_quality
                tables = camelot_stream

        # Use best result
        if not tables:
            tables = pdfplumber_tables

        logger.info(f"✅ Final extraction: {len(tables)} tables (quality: {quality['score']:.2f})")

        if not tables:
            logger.warning("⚠️ No tables found in entire PDF")

        return tables

    except Exception as e:
        logger.error(f"❌ PDF extraction failed: {e}")
        return []


def evaluate_extraction_quality(tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Evaluate quality of table extraction.

    Returns quality score (0.0 to 1.0) based on:
    - Number of tables found
    - Column diversity (not all single-column)
    - Row count per table
    - Data completeness
    """

    if not tables:
        return {"score": 0.0, "reason": "no tables found"}

    score = 0.0
    reasons = []

    # Factor 1: Number of tables (more is generally better)
    if len(tables) > 0:
        score += 0.2
        reasons.append(f"{len(tables)} tables")

    # Factor 2: Column diversity
    multi_column_tables = 0
    for t in tables:
        data = t.get("data", [])
        if data and len(data) > 0 and len(data[0]) > 1:
            multi_column_tables += 1

    if multi_column_tables > 0:
        column_ratio = multi_column_tables / len(tables)
        score += 0.3 * column_ratio
        reasons.append(f"{multi_column_tables}/{len(tables)} multi-column")

    # Factor 3: Average row count
    total_rows = sum(len(t.get("data", [])) for t in tables)
    avg_rows = total_rows / len(tables) if tables else 0

    if avg_rows > 3:
        score += 0.2
        reasons.append(f"avg {avg_rows:.1f} rows/table")

    # Factor 4: Data completeness (not too many empty cells)
    empty_cells = 0
    total_cells = 0

    for t in tables:
        data = t.get("data", [])
        for row in data:
            for cell in row:
                total_cells += 1
                if not cell or str(cell).strip() == "":
                    empty_cells += 1

    if total_cells > 0:
        completeness = 1 - (empty_cells / total_cells)
        score += 0.3 * completeness
        reasons.append(f"{completeness * 100:.1f}% complete")

    return {
        "score": min(score, 1.0),
        "reason": ", ".join(reasons)
    }


def extract_with_pdfplumber(file_path: str) -> List[Dict[str, Any]]:
    """
    Extract with pdfplumber using default settings.
    Works well for most documents without over-optimization.
    """

    tables: List[Dict[str, Any]] = []

    with pdfplumber.open(file_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                # Use default settings - simpler is often better for universal handling
                extracted = page.extract_tables()

                if not extracted:
                    logger.info(f"ℹ️ No tables found on page {page_number}")
                    continue

                for t in extracted:
                    if not t or len(t) == 0:
                        continue

                    tables.append({
                        "data": t,
                        "page": page_number
                    })

                    logger.info(
                        f"✅ Extracted table from page {page_number}: "
                        f"{len(t)} rows × {len(t[0]) if t else 0} cols"
                    )

            except Exception as e:
                logger.warning(
                    f"⚠️ Failed to extract tables from page {page_number}: {e}"
                )
                continue

    return tables


def extract_with_camelot(file_path: str, flavor: str = 'lattice') -> List[Dict[str, Any]]:
    """
    Extract with Camelot (requires camelot-py[cv]).
    flavor: 'lattice' for bordered tables, 'stream' for borderless
    """
    if not HAS_CAMELOT:
        return []

    # ── Ghostscript path fix for Homebrew macOS ──────────────────
    import os
    gs_path = "/opt/homebrew/bin/gs"
    if os.path.exists(gs_path):
        os.environ["PATH"]            = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
        os.environ["GHOSTSCRIPT_PATH"] = gs_path
    # ─────────────────────────────────────────────────────────────

    tables: List[Dict[str, Any]] = []

    try:
        if flavor == 'lattice':
            camelot_tables = camelot.read_pdf(
                file_path,
                pages='all',
                flavor='lattice'
            )
        else:
            camelot_tables = camelot.read_pdf(
                file_path,
                pages='all',
                flavor='stream'
            )

        for table in camelot_tables:
            df   = table.df
            data = df.values.tolist()

            if len(data) < 2:
                continue

            tables.append({
                "data": data,
                "page": table.page
            })

            logger.info(
                f"✅ Camelot ({flavor}) extracted table from page {table.page}: "
                f"{len(data)} rows × {len(data[0]) if data else 0} cols"
            )

    except Exception as e:
        logger.warning(f"⚠️ Camelot ({flavor}) extraction failed: {e}")

    return tables


# Keep existing helper functions
def extract_pdf_text(file_path: str) -> str:
    text = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text.append(t)
    return "\n".join(text)


import re


def extract_pdf_fields(text: str) -> dict:
    fields = {}

    patterns = {
        "case_number": r"(Receipt Number|Case Number)[:\s]+([A-Z]{3}\d{10})",
        "receipt_date": r"Receipt Date[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        "notice_date": r"Notice Date[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        "petitioner": r"Petitioner[:\s]+(.+)",
        "beneficiary": r"Beneficiary[:\s]+(.+)",
        "form_type": r"Form[:\s]+(I-\d+)",
        "service_center": r"(California|Vermont|Texas|Nebraska) Service Center"
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            fields[key] = m.group(m.lastindex)

    return fields


def ocr_pdf_to_text(file_path: str) -> str:
    from app.services.ocr.ocr_utils import ocr_pdf_best_effort
    return ocr_pdf_best_effort(file_path)

# import logging
# from typing import List, Dict, Any
# import pdfplumber
#
# logger = logging.getLogger(__name__)
#
#
# def extract_pdf_tables(file_path: str) -> List[Dict[str, Any]]:
#     """
#     Extract tables from a PDF using pdfplumber.
#
#     Returns a list of dicts:
#     [
#         {
#             "data": [ [row1], [row2], ... ],
#             "page": page_number
#         },
#         ...
#     ]
#
#     This output is intentionally simple so that normalize_tables()
#     can handle all cleanup, header fixing, and conversion.
#     """
#
#     tables: List[Dict[str, Any]] = []
#
#     try:
#         logger.info(f"📄 Opening PDF for table extraction: {file_path}")
#
#         with pdfplumber.open(file_path) as pdf:
#             for page_number, page in enumerate(pdf.pages, start=1):
#                 try:
#                     extracted = page.extract_tables()
#
#                     if not extracted:
#                         logger.info(f"ℹ️ No tables found on page {page_number}")
#                         continue
#
#                     for t in extracted:
#                         if not t or len(t) == 0:
#                             continue
#
#                         # pdfplumber returns list-of-lists
#                         tables.append({
#                             "data": t,
#                             "page": page_number
#                         })
#
#                         logger.info(
#                             f"✅ Extracted table from page {page_number}: "
#                             f"{len(t)} rows"
#                         )
#
#                 except Exception as e:
#                     logger.warning(
#                         f"⚠️ Failed to extract tables from page {page_number}: {e}"
#                     )
#                     continue
#
#         if not tables:
#             logger.warning("⚠️ No tables found in entire PDF")
#
#         return tables
#
#     except Exception as e:
#         logger.error(f"❌ PDF extraction failed: {e}")
#         return []
#
#
# import pdfplumber
#
#
# def extract_pdf_text(file_path: str) -> str:
#     text = []
#     with pdfplumber.open(file_path) as pdf:
#         for page in pdf.pages:
#             t = page.extract_text() or ""
#             text.append(t)
#     return "\n".join(text)
#
#
# import re
#
#
# def extract_pdf_fields(text: str) -> dict:
#     fields = {}
#
#     patterns = {
#         "case_number": r"(Receipt Number|Case Number)[:\s]+([A-Z]{3}\d{10})",
#         "receipt_date": r"Receipt Date[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
#         "notice_date": r"Notice Date[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
#         "petitioner": r"Petitioner[:\s]+(.+)",
#         "beneficiary": r"Beneficiary[:\s]+(.+)",
#         "form_type": r"Form[:\s]+(I-\d+)",
#         "service_center": r"(California|Vermont|Texas|Nebraska) Service Center"
#     }
#
#     for key, pattern in patterns.items():
#         m = re.search(pattern, text, re.IGNORECASE)
#         if m:
#             fields[key] = m.group(m.lastindex)
#
#     return fields
#
#
# import pytesseract
# from pdf2image import convert_from_path
#
#
# def ocr_pdf_to_text(file_path: str) -> str:
#     pages = convert_from_path(file_path)
#     text = []
#     for img in pages:
#         text.append(pytesseract.image_to_string(img))
#     return "\n".join(text)
