"""
docling_parser.py
─────────────────────────────────────────────────────────────────────────────
Universal document parser using IBM Docling.

Drop this in as: app/services/parsers/docling_parser.py

OCR STRATEGY (adaptive per document):
  Three tiers based on what pdfplumber finds in the PDF:

  Tier 1 — TEXT-NATIVE (bank statements, invoices, tax forms):
    pdfplumber extracts rich text (>300 chars/page avg) AND file is not
    suspiciously large relative to extracted text.
    → do_ocr=False. Fast + accurate. No OCR degradation on clean text.

  Tier 2 — MIXED / FORM (filled forms, partially scanned docs):
    pdfplumber extracts some text (template labels) but density is low,
    OR the file is large relative to its extracted text (image content present).
    → do_ocr=True, force_full_page_ocr=True.
    Captures both printed template labels AND filled-in values.

  Tier 3 — SCANNED / IMAGE-ONLY (scanned receipts, photos of documents):
    pdfplumber extracts nothing or near-nothing (<50 chars/page).
    → do_ocr=True, force_full_page_ocr=True. Full OCR required.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DOCLING_FORMATS = {".pdf", ".docx", ".pptx", ".xlsx", ".html",
                   ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

# Thresholds for OCR tier detection
_CHARS_PER_PAGE_RICH  = 300   # above → text-native (skip OCR)
_CHARS_PER_PAGE_MIXED = 50    # below → scanned (must OCR); between → mixed/form
_SIZE_RATIO_THRESHOLD = 150   # file_bytes / extracted_chars; high = image-heavy
_FAST_PATH_MIN_PAGES  = 20    # text PDFs longer than this skip Docling


# =============================================================================
# OCR TIER DETECTION
# =============================================================================

def _detect_ocr_tier(file_path: str) -> str:
    """
    Probe a PDF with pdfplumber to decide the correct OCR strategy.
    Returns: 'text_native' | 'mixed_form' | 'scanned'
    """
    try:
        import fitz  # PyMuPDF — ~25x faster than pdfplumber for a text probe
        file_bytes  = Path(file_path).stat().st_size
        with fitz.open(file_path) as pdf:
            page_texts = [page.get_text("text") or "" for page in pdf]
        page_count  = len(page_texts)
        total_chars = sum(len(t) for t in page_texts)

        if page_count == 0:
            return "scanned"

        chars_per_page = total_chars / page_count
        size_ratio     = file_bytes / max(total_chars, 1)

        logger.info(
            f"🔎 OCR probe: {page_count}p | "
            f"{chars_per_page:.0f} chars/page | "
            f"{size_ratio:.0f} bytes/char"
        )

        if chars_per_page < _CHARS_PER_PAGE_MIXED:

            # ── Custom font / garbled text detection ─────────────────────────
            # CrawfordTech PDFs extract chars but they're garbage symbols.
            try:
                _sample = ''.join(t[:300] for t in page_texts[:2])
                _real = sum(1 for c in _sample if c.isalpha() or c.isdigit())
                if _real / max(len(_sample), 1) < 0.2 and len(_sample) > 50:
                    logger.warning(f'⚠️ Custom font encoding detected — forcing OCR')
                    return 'scanned'
            except Exception:
                pass
            return "scanned"

        if chars_per_page >= _CHARS_PER_PAGE_RICH:
            # Rich text layer. A large file per char means embedded images —
            # OCR those regions only; the text itself is already good.
            return "text_native" if size_ratio < _SIZE_RATIO_THRESHOLD else "rich_with_images"

        return "mixed_form"

    except Exception as e:
        logger.warning(f"⚠️ OCR probe failed ({e}) — defaulting to mixed_form")
        return "mixed_form"


# =============================================================================
# CONVERTER FACTORY
# =============================================================================

_CONVERTER_CACHE: dict = {}
_CONVERTER_LOCK = __import__("threading").Lock()
_CONVERT_LOCK = __import__("threading").Lock()


def _build_pdf_converter(ocr_tier: str):
    """One converter per OCR tier — building one reloads Docling's layout and
    table models (seconds each), which used to happen on every upload."""
    with _CONVERTER_LOCK:
        conv = _CONVERTER_CACHE.get(ocr_tier)
        if conv is None:
            conv = _CONVERTER_CACHE[ocr_tier] = _new_pdf_converter(ocr_tier)
        return conv


def _new_pdf_converter(ocr_tier: str):
    """
    Build a DocumentConverter with correct Docling API for the installed version.
    Tries the new PdfFormatOption API first, falls back to legacy format_options dict.
    """
    from docling.document_converter import DocumentConverter
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    opts = PdfPipelineOptions()

    if ocr_tier == "text_native":
        opts.do_ocr = False
    else:
        opts.do_ocr = True
        # Scans and filled forms need every page OCR'd (form values are often
        # only visible as images). Text-rich PDFs with images keep their text
        # layer and OCR bitmap regions only — full-page OCR on 100 text pages
        # re-reads everything and can degrade good text.
        opts.ocr_options.force_full_page_ocr = ocr_tier in ("scanned", "mixed_form")
        # Also OCR picture regions (catches image-embedded flyers)
        try:
            opts.do_picture_ocr = True
        except Exception:
            pass

    # ── Try new API: PdfFormatOption wrapper (Docling >= 2.x) ─────────────────
    try:
        from docling.document_converter import PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts)
            }
        )
        logger.info("✅ Converter built with PdfFormatOption API")
        return converter
    except (ImportError, Exception) as e:
        logger.debug(f"PdfFormatOption API not available: {e}")

    # ── Try legacy API: ConversionOptions (Docling 1.x) ───────────────────────
    try:
        from docling.document_converter import ConversionOptions
        converter = DocumentConverter(
            conversion_options=ConversionOptions(pipeline_options=opts)
        )
        logger.info("✅ Converter built with ConversionOptions API (legacy)")
        return converter
    except (ImportError, Exception) as e:
        logger.debug(f"ConversionOptions API not available: {e}")

    # ── Final fallback: plain DocumentConverter, set pipeline after init ───────
    try:
        converter = DocumentConverter()
        # Attempt to inject options via internal pipeline if accessible
        if hasattr(converter, '_pipeline_options'):
            converter._pipeline_options = opts
        logger.info("✅ Converter built with default API (options injection attempted)")
        return converter
    except Exception as e:
        logger.warning(f"⚠️ All converter APIs failed ({e}) — using bare DocumentConverter")
        return DocumentConverter()


# =============================================================================
# NUMERIC NORMALIZER
# =============================================================================

def _normalize_numeric_spaces(text: str) -> str:
    """
    Docling renders PDF currency/number tables with spaces instead of
    comma+period separators.  Examples from ADP/payroll PDFs:
        "$3 436 00"   →  "$3,436.00"
        "75 592 00"   →  "75,592.00"
        "-488 46"     →  "-488.46"
        "3 436 00"    →  "3,436.00"
        "dental: 387 88"  →  "dental: 387.88"

    Rules (applied in order, most specific first):
    1. Currency symbol prefix  ($ £ €)  — always normalise
    2. Negative amounts        -NNN NN  — clearly a deduction, not a count
    3. Large plain amounts     N NNN NN or larger (1+ thousands groups)
    4. Labelled small amounts  after ": " or "| "  NNN NN (3+2 digits)
    """
    import re

    def _fmt(whole: str, cents: str, sign: str = "", sym: str = "") -> str:
        # Re-insert thousands commas into the integer part
        w = ""
        for i, ch in enumerate(reversed(whole)):
            if i and i % 3 == 0:
                w = "," + w
            w = ch + w
        return f"{sign}{sym}{w}.{cents}"

    # 1. Currency symbol:  -?[$£€]  NNN[ NNN]* NN
    def fix_currency(m: re.Match) -> str:
        sign, sym, digits = m.group(1), m.group(2), m.group(3)
        parts = digits.split()
        if len(parts) < 2:
            return m.group(0)
        return _fmt("".join(parts[:-1]), parts[-1], sign, sym)

    text = re.sub(
        r'(-?)([\$\£\€])\s*(\d{1,3}(?:\s\d{3})*\s\d{2})\b',
        fix_currency, text,
    )

    # 2. Negative plain amounts: -NNN NN  (deductions — never ambiguous)
    def fix_negative(m: re.Match) -> str:
        digits = m.group(1)
        parts  = digits.split()
        if len(parts) < 2:
            return m.group(0)
        return _fmt("".join(parts[:-1]), parts[-1], sign="-")

    text = re.sub(
        r'(?<![.\d])-(\d{1,4}(?:\s\d{3})*\s\d{2})\b',
        fix_negative, text,
    )

    # 3. Large plain amounts: at least one 3-digit thousands group before cents
    #    e.g. "3 436 00", "75 592 00", "8 747 18"
    def fix_large(m: re.Match) -> str:
        digits = m.group(1)
        parts  = digits.split()
        if len(parts) < 2:
            return m.group(0)
        return _fmt("".join(parts[:-1]), parts[-1])

    text = re.sub(
        r'(?<![.\d-])(\d{1,3}(?:\s\d{3})+\s\d{2})(?!\d)',
        fix_large, text,
    )

    # 4. Labelled small amounts: "label: NNN NN" or "| NNN NN"
    #    Covers dental/vision/tax lines: "dental: 387 88" → "dental: 387.88"
    def fix_labelled(m: re.Match) -> str:
        prefix = m.group(1)   # ": " or "| "
        whole  = m.group(2)
        cents  = m.group(3)
        return f"{prefix}{whole}.{cents}"

    text = re.sub(
        r'([:\|]\s*)(\d{2,4})\s(\d{2})(?=\s|$|\|)',
        fix_labelled, text,
    )

    return text


# =============================================================================
# PARSER
# =============================================================================

class DoclingParser:
    """
    IBM Docling-powered universal document parser.

    Adaptive OCR strategy:
      - Text-native PDFs (bank statements, tax forms): OCR disabled — fast
      - Filled forms / mixed PDFs: force_full_page_ocr — captures filled values
      - Scanned / image PDFs: force_full_page_ocr — full OCR required
      - Non-PDF (docx, pptx, images): Docling default
    """

    def __init__(self):
        self._default_converter = None

    def _get_default_converter(self):
        if self._default_converter is None:
            try:
                from docling.document_converter import DocumentConverter
                self._default_converter = DocumentConverter()
                logger.info("✅ [DoclingParser] Default converter ready")
            except ImportError:
                self._default_converter = "unavailable"
        return self._default_converter

    # ── public API ────────────────────────────────────────────────────────────
    def parse(self, file_path: str, filename: str = "") -> dict[str, Any]:
        """
        Parse any document. Returns:
          {
            "text":     str,
            "tables":   list[dict],
            "metadata": dict,
            "type":     str,
          }
        """
        path = Path(file_path)
        ext  = path.suffix.lower()

        if not path.exists():
            logger.error(f"❌ [DoclingParser] File not found: {file_path}")
            return self._empty(ext)

        # Fast-path handled upstream by _parse_fast; only Docling-native or
        # unknown formats reach here.  For anything Docling cannot process,
        # delegate to ocr_any which covers every allowed format.
        if ext not in DOCLING_FORMATS:
            try:
                from app.services.ocr.ocr_utils import ocr_any
                logger.info(f"📄 [DoclingParser] Non-Docling format {ext!r} → ocr_any")
                text = ocr_any(file_path)
                return {
                    "text":     text,
                    "tables":   [],
                    "sheets":   [],
                    "metadata": {"format": ext.lstrip("."), "source": "ocr_any"},
                    "type":     "document",
                }
            except Exception as e:
                logger.error(f"❌ ocr_any fallback failed for {ext!r}: {e}")
                return self._empty(ext)

        try:
            from docling.document_converter import DocumentConverter  # noqa
        except ImportError:
            logger.warning("⚠️ Docling not installed — falling back to pdfplumber")
            return self._pdfplumber_fallback(file_path, ext)

        return self._parse_with_docling(file_path, ext)

    # ── core ──────────────────────────────────────────────────────────────────
    def _parse_with_docling(self, file_path: str, ext: str) -> dict[str, Any]:
        t0    = time.perf_counter()
        fname = Path(file_path).name
        logger.info(f"📄 [DoclingParser] Parsing: {fname}")

        searchable = None
        try:
            if ext == ".pdf":
                ocr_tier  = _detect_ocr_tier(file_path)
                if ocr_tier == "scanned":
                    # Deskew + Tesseract text layer, then parse as a text PDF
                    from app.services.ocr.ocrmypdf_layer import make_searchable
                    searchable = make_searchable(file_path)
                    if searchable:
                        file_path = searchable
                        ocr_tier  = "text_native"
                if ocr_tier in ("text_native", "rich_with_images"):
                    fast = self._fast_pdf_text(file_path)
                    if fast is not None:
                        return fast
                converter = _build_pdf_converter(ocr_tier)
            else:
                converter = self._get_default_converter()
                if converter == "unavailable":
                    return self._empty(ext)

            _t0_convert = time.perf_counter()
            # Converters are shared (cached) and Docling isn't safe to run
            # concurrently on one instance — parallel uploads deadlocked.
            with _CONVERT_LOCK:
                result = converter.convert(file_path)
            doc    = result.document
            logger.info(f"⏱️ [DoclingParser] converter.convert() took {time.perf_counter() - _t0_convert:.1f}s")

            text = _normalize_numeric_spaces(doc.export_to_markdown())

            tables = []
            for i, table in enumerate(doc.tables):
                try:
                    df = table.export_to_dataframe()
                    if df is None or df.empty:
                        continue
                    tables.append({
                        "name":    f"Table {i + 1}",
                        "columns": list(df.columns),
                        "data":    df.values.tolist(),
                        "rows":    len(df),
                        "cols":    len(df.columns),
                    })
                except Exception as te:
                    logger.debug(f"Table {i+1} export skipped: {te}")

            meta: dict[str, Any] = {}
            try:
                info = doc.meta
                if info:
                    for field in ("title", "author", "language", "description"):
                        val = getattr(info, field, None)
                        if val:
                            meta[field] = val
            except Exception:
                pass
            try:
                meta["page_count"] = len(list(doc.pages))
            except Exception:
                pass

            elapsed = time.perf_counter() - t0
            logger.info(
                f"✅ [DoclingParser] {fname} done in {elapsed:.2f}s — "
                f"{len(text):,} chars, {len(tables)} tables"
            )
            return {"text": text, "tables": tables, "metadata": meta, "type": ext.lstrip(".")}

        except Exception as e:
            logger.error(f"❌ [DoclingParser] Failed on {fname}: {e}", exc_info=True)
            if ext == ".pdf":
                return self._pdfplumber_fallback(file_path, ext)
            return self._empty(ext)
        finally:
            if searchable:
                Path(searchable).unlink(missing_ok=True)

    # ── pdfplumber safety net ─────────────────────────────────────────────────
    def _pdfplumber_fallback(self, file_path: str, ext: str) -> dict[str, Any]:
        logger.warning("⚠️ [DoclingParser] pdfplumber fallback active")
        try:
            import pdfplumber
            parts, tables = [], []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
                    for tbl in (page.extract_tables() or []):
                        if tbl:
                            tables.append({
                                "name":    f"Table {len(tables)+1}",
                                "columns": tbl[0] if tbl else [],
                                "data":    tbl[1:] if len(tbl) > 1 else [],
                                "rows":    len(tbl) - 1,
                                "cols":    len(tbl[0]) if tbl else 0,
                            })
            return {"text": "\n\n".join(parts), "tables": tables, "metadata": {}, "type": "pdf"}
        except Exception as e:
            logger.error(f"❌ pdfplumber also failed: {e}")
            return self._empty(ext)

    @staticmethod
    def _fast_pdf_text(file_path: str) -> Optional[dict[str, Any]]:
        """
        Large PDFs with a real text layer: read the text directly (<1s for 100+
        pages) instead of running Docling's per-page layout/table models
        (10+ min). Small PDFs return None and keep Docling — statement and
        pay-stub extractors rely on its Markdown tables, and it's fast there.
        """
        import fitz
        with fitz.open(file_path) as pdf:
            if len(pdf) <= _FAST_PATH_MIN_PAGES:
                return None
            t0 = time.perf_counter()
            parts = [f"## Page {i + 1}\n\n{(page.get_text('text') or '').strip()}"
                     for i, page in enumerate(pdf)]
            meta = {"page_count": len(pdf), "parser": "pymupdf_fast"}
            for key in ("title", "author"):
                if pdf.metadata and pdf.metadata.get(key):
                    meta[key] = pdf.metadata[key]
        text = _normalize_numeric_spaces("\n\n".join(parts))
        logger.info(f"⚡ [DoclingParser] Fast text path: {meta['page_count']} pages, "
                    f"{len(text):,} chars in {time.perf_counter() - t0:.1f}s")
        return {"text": text, "tables": [], "metadata": meta, "type": "pdf"}

    @staticmethod
    def _empty(ext: str) -> dict[str, Any]:
        return {"text": "", "tables": [], "metadata": {}, "type": ext.lstrip(".")}


# ── Alias keeps any existing `from ... import UniversalParser` working ────────
UniversalParser = DoclingParser