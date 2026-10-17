"""
Searchable-PDF pre-pass for scanned PDFs, using OCRmyPDF.

OCRmyPDF deskews and auto-rotates each page, runs Tesseract and writes an
invisible text layer under the page images. The result reads as a normal
text PDF, so Docling can parse it without its own OCR (faster, and Tesseract
on a deskewed page beats OCR on a crooked scan).

Returns None whenever OCRmyPDF or its tools (tesseract, and ghostscript on
older OCRmyPDF versions) are unavailable or the run fails; callers then keep
their existing OCR path.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Large scans: OCRmyPDF is ~1-3 s/page; past this, leave it to Docling's OCR
_MAX_PAGES = int(os.getenv("OCRMYPDF_MAX_PAGES", "200"))


def make_searchable(file_path: str, language: str = "eng") -> Optional[str]:
    """OCR a scanned PDF into a temp searchable PDF. Caller deletes the file."""
    if os.getenv("OCRMYPDF_DISABLED") == "1":
        return None
    try:
        import ocrmypdf
    except ImportError:
        logger.info("OCRmyPDF not installed — using Docling OCR")
        return None

    try:
        import fitz
        with fitz.open(file_path) as pdf:
            if len(pdf) > _MAX_PAGES:
                logger.info(f"OCRmyPDF skipped: {len(pdf)} pages > {_MAX_PAGES}")
                return None
    except Exception:
        pass

    fd, out_path = tempfile.mkstemp(suffix=".ocr.pdf")
    os.close(fd)
    t0 = time.perf_counter()
    try:
        ocrmypdf.ocr(
            file_path,
            out_path,
            language=language,
            deskew=True,
            rotate_pages=True,
            skip_text=True,          # keep pages that already have text as-is
            output_type="pdf",       # no PDF/A conversion → no Ghostscript step
            optimize=0,
            progress_bar=False,
            use_threads=True,        # threads, not processes: safe in the frozen desktop app
        )
        logger.info(f"🔤 OCRmyPDF text layer added in {time.perf_counter() - t0:.1f}s")
        return out_path
    except Exception as e:
        # MissingDependencyError (no tesseract/gs), EncryptedPdfError, etc.
        logger.warning(f"⚠️ OCRmyPDF failed ({type(e).__name__}: {e}) — using Docling OCR")
        Path(out_path).unlink(missing_ok=True)
        return None
