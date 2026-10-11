"""
app/services/ocr/ocr_utils.py

Central OCR preprocessing utilities used by every OCR call site.

Handles all scanned document types correctly at scale:
- Rotation detection & auto-correction  (upside-down passports, sideways IDs, etc.)
- Image mode normalisation              (RGBA / P / LA → RGB)
- Contrast normalisation                (underexposed / overexposed scans)
- Adaptive binarisation                 (improves Tesseract on faint ink)
- High-DPI OCR config                   (LSTM engine, auto-PSM)

All functions accept a PIL Image and return text + confidence.
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
_TESSERACT_BIN = os.getenv("TESSERACT_BINARY") or os.getenv("TESSERACT_PATH", "tesseract")

# Minimum confidence to keep a word (Tesseract 0–100 scale)
_MIN_WORD_CONF = 20

# If alphanumeric ratio in extracted text is below this, text is garbled
_GARBLE_THRESHOLD = 0.25


# ---------------------------------------------------------------------------
# INTERNAL: image preprocessing
# ---------------------------------------------------------------------------
def _to_rgb(image) -> "PIL.Image.Image":
    from PIL import Image
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return image


def _normalise_contrast(image) -> "PIL.Image.Image":
    """Apply CLAHE-style contrast normalisation via PIL ImageOps."""
    from PIL import ImageOps
    try:
        if image.mode == "L":
            return ImageOps.equalize(image)
        # Equalise each channel then merge
        from PIL import Image
        r, g, b = image.split()
        eq = Image.merge("RGB", [ImageOps.equalize(c) for c in (r, g, b)])
        # Blend 50% equalised / 50% original to avoid over-processing
        from PIL import ImageChops
        return Image.blend(image, eq, 0.5)
    except Exception:
        return image


def _detect_and_rotate(image) -> "PIL.Image.Image":
    """
    Use Tesseract OSD to detect the page orientation and rotate if needed.
    Handles 90 / 180 / 270 ° rotations (the most common scan errors).
    Returns the (possibly rotated) image.
    """
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_BIN
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        angle = osd.get("rotate", 0)
        if angle and angle != 0:
            logger.info(f"🔄 OSD detected {angle}° rotation — correcting")
            image = image.rotate(angle, expand=True)
    except Exception as e:
        logger.debug(f"OSD skipped ({e})")
    return image


def preprocess_for_ocr(image) -> "PIL.Image.Image":
    """
    Full preprocessing pipeline applied before any OCR engine.
    Safe to call on any PIL Image from any source (PDF page, JPG, PNG, HEIC…).
    """
    image = _to_rgb(image)
    image = _detect_and_rotate(image)
    image = _normalise_contrast(image)
    return image


# ---------------------------------------------------------------------------
# TESSERACT OCR
# ---------------------------------------------------------------------------
def tesseract_ocr_page(image, lang: str = "eng") -> Tuple[str, float]:
    """
    Run Tesseract LSTM OCR on a single PIL Image page.
    Returns (text, avg_confidence 0–1).
    Applies full preprocessing automatically.
    """
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_BIN

    image = preprocess_for_ocr(image)

    # LSTM engine (oem 1), auto page-segmentation (psm 3), 300 DPI hint
    config = "--oem 1 --psm 3 -c tessedit_do_invert=0"

    try:
        data = pytesseract.image_to_data(
            image,
            lang=lang,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
        words, confs = [], []
        for word, conf in zip(data["text"], data["conf"]):
            if str(word).strip():
                words.append(word)
                if conf != -1:
                    confs.append(int(conf))

        text = " ".join(words)
        avg_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text, avg_conf
    except Exception as e:
        logger.warning(f"Tesseract error: {e}")
        return "", 0.0


def tesseract_ocr_pdf(file_path: str, dpi: int = 300, lang: str = "eng") -> str:
    """
    Render every page of a PDF at *dpi* and OCR with Tesseract.
    Auto-rotates each page. Returns concatenated text.
    """
    from pdf2image import convert_from_path
    pages = convert_from_path(file_path, dpi=dpi)
    results = []
    for i, page_img in enumerate(pages):
        text, conf = tesseract_ocr_page(page_img, lang=lang)
        if text:
            results.append(text)
            logger.info(f"   Tesseract p{i+1}: {len(text)} chars conf={conf:.2f}")
    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# EASYOCR with rotation + reading-order reconstruction
# ---------------------------------------------------------------------------
def easyocr_ocr_page(image, reader=None) -> str:
    """
    Run EasyOCR on a single PIL Image page.
    Applies preprocessing (incl. rotation) first.
    Returns text in correct reading order.
    """
    import numpy as np
    from app.services.ocr.landingai_style import _sort_by_reading_order

    image = preprocess_for_ocr(image)

    # PIL → numpy BGR for EasyOCR
    img_np = np.array(image)

    if reader is None:
        from app.services.ocr.landingai_style import _get_reader
        reader = _get_reader()

    img_width = img_np.shape[1]
    results = reader.readtext(img_np, detail=1, paragraph=False)
    ordered = _sort_by_reading_order(results, img_width)

    lines = [text.strip() for (_, text, conf) in ordered if text.strip() and conf > 0.3]
    return "\n".join(lines)


def easyocr_ocr_pdf(file_path: str, dpi: int = 200) -> str:
    """
    Render every page of a PDF and OCR with EasyOCR.
    Each page is auto-rotated before OCR. Returns concatenated text.
    """
    import fitz
    import cv2
    import numpy as np
    from app.services.ocr.landingai_style import _get_reader, _sort_by_reading_order

    reader = _get_reader()
    doc = fitz.open(file_path)
    all_text = []

    for page_num, page in enumerate(doc):
        logger.info(f"   EasyOCR page {page_num + 1}/{len(doc)}")
        pix = page.get_pixmap(dpi=dpi)
        img = cv2.imdecode(
            np.frombuffer(pix.tobytes(), np.uint8),
            cv2.IMREAD_COLOR,
        )

        # Rotate via Tesseract OSD (PIL round-trip)
        from PIL import Image
        pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        pil_rotated = _detect_and_rotate(pil_img)
        if pil_rotated is not pil_img:
            img = cv2.cvtColor(np.array(pil_rotated), cv2.COLOR_RGB2BGR)

        img_width = img.shape[1]
        try:
            results = reader.readtext(img, detail=1, paragraph=False)
            ordered = _sort_by_reading_order(results, img_width)
            lines = [t.strip() for (_, t, c) in ordered if t.strip() and c > 0.3]
            if lines:
                all_text.append("\n".join(lines))
        except Exception as e:
            logger.warning(f"   EasyOCR page {page_num + 1} failed: {e}")

    return "\n\n".join(all_text)


# ---------------------------------------------------------------------------
# BEST-EFFORT OCR (auto engine selection)
# ---------------------------------------------------------------------------
def ocr_pdf_best_effort(file_path: str) -> str:
    """
    Try Tesseract (higher quality, LSTM) then EasyOCR as fallback.
    Returns the result with more alphanumeric content.
    """
    try:
        text = tesseract_ocr_pdf(file_path, dpi=300)
        if _quality_ok(text):
            logger.info(f"✅ Tesseract succeeded: {len(text)} chars")
            return text
        logger.warning("⚠️ Tesseract low-quality — trying EasyOCR")
    except Exception as e:
        logger.warning(f"⚠️ Tesseract failed ({e}) — trying EasyOCR")

    try:
        text = easyocr_ocr_pdf(file_path)
        logger.info(f"✅ EasyOCR succeeded: {len(text)} chars")
        return text
    except Exception as e:
        logger.error(f"❌ All OCR failed: {e}")
        return ""


def ocr_image_best_effort(image) -> Tuple[str, float]:
    """
    Try Tesseract first then EasyOCR as fallback for a single PIL Image.
    Returns (text, confidence).
    """
    text, conf = tesseract_ocr_page(image)
    if _quality_ok(text):
        return text, conf

    logger.warning("⚠️ Tesseract low quality on image — trying EasyOCR")
    try:
        easy_text = easyocr_ocr_page(image)
        if len(easy_text) > len(text):
            return easy_text, 0.7  # EasyOCR does not expose confidence here
    except Exception as e:
        logger.warning(f"EasyOCR fallback failed: {e}")

    return text, conf


def _quality_ok(text: str) -> bool:
    """Return True if text looks like real content (not garbled OCR output)."""
    if not text or len(text.strip()) < 50:
        return False
    real = sum(1 for c in text if c.isalnum())
    return (real / len(text)) >= _GARBLE_THRESHOLD


# ---------------------------------------------------------------------------
# IMAGE FILE OCR (any image format: JPG, PNG, TIFF, BMP, WEBP, HEIC, …)
# ---------------------------------------------------------------------------
def ocr_image_file(file_path: str) -> Tuple[str, float]:
    """
    OCR a single image file of any format.
    Auto-rotates, normalises contrast, tries Tesseract then EasyOCR.
    """
    from PIL import Image

    # HEIC/HEIF support
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    image = Image.open(file_path)
    return ocr_image_best_effort(image)


# ---------------------------------------------------------------------------
# MULTI-PAGE TIFF
# ---------------------------------------------------------------------------
def ocr_tiff(file_path: str) -> str:
    """OCR every frame of a multi-page TIFF (fax, scanner output, medical imaging)."""
    from PIL import Image
    results = []
    try:
        img = Image.open(file_path)
        frame = 0
        while True:
            try:
                img.seek(frame)
                text, _ = ocr_image_best_effort(img.copy())
                if text:
                    results.append(text)
                frame += 1
            except EOFError:
                break
    except Exception as e:
        logger.error(f"TIFF OCR failed: {e}")
    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# OFFICE DOCUMENTS — embedded image OCR (DOCX, XLSX, PPTX)
# ---------------------------------------------------------------------------
def ocr_office_images(file_path: str) -> str:
    """
    Extract all embedded images from a DOCX/XLSX/PPTX (they're ZIP archives)
    and OCR each one. Returns concatenated text from all images found.
    Useful for scanned documents attached as Word files, ID cards pasted into
    spreadsheets, forms with image-based fields, etc.
    """
    import zipfile
    import io
    from PIL import Image

    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".emf", ".wmf"}
    results = []

    try:
        with zipfile.ZipFile(file_path) as zf:
            image_entries = [
                n for n in zf.namelist()
                if any(n.lower().endswith(ext) for ext in image_exts)
                # DOCX images live in word/media/, PPTX in ppt/media/, XLSX in xl/media/
                and ("media/" in n or "image" in n.lower())
            ]
            for entry in image_entries:
                try:
                    data = zf.read(entry)
                    image = Image.open(io.BytesIO(data))
                    text, conf = ocr_image_best_effort(image)
                    if text and conf > 0.2:
                        logger.info(f"   Office image OCR [{entry}]: {len(text)} chars")
                        results.append(text)
                except Exception as e:
                    logger.debug(f"   Skipped {entry}: {e}")
    except Exception as e:
        logger.error(f"Office image OCR failed: {e}")

    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# EMAIL — inline image OCR (EML / MSG attachments)
# ---------------------------------------------------------------------------
def ocr_email_images(file_path: str) -> str:
    """
    Parse an EML file, extract all image attachments and inline images,
    OCR each one, return concatenated text.
    """
    import email
    import io
    from PIL import Image

    results = []
    try:
        with open(file_path, "rb") as f:
            msg = email.message_from_bytes(f.read())

        for part in msg.walk():
            ct = part.get_content_type()
            if ct.startswith("image/"):
                try:
                    data = part.get_payload(decode=True)
                    if not data:
                        continue
                    image = Image.open(io.BytesIO(data))
                    text, conf = ocr_image_best_effort(image)
                    if text and conf > 0.2:
                        results.append(text)
                except Exception as e:
                    logger.debug(f"Email image part skipped: {e}")
    except Exception as e:
        logger.error(f"Email OCR failed: {e}")

    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# UNIVERSAL ENTRY POINT — auto-detect format and OCR appropriately
# ---------------------------------------------------------------------------
_IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic", ".heif", ".gif"}
_TIFF_EXTS    = {".tiff", ".tif"}
_PDF_EXTS     = {".pdf"}
_OFFICE_EXTS  = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".xlsm",
                  ".odp", ".ods", ".odt", ".wps"}
_EMAIL_EXTS   = {".eml", ".msg", ".mbox"}
# Plain-text formats — read directly, no OCR needed
_TEXT_EXTS    = {".txt", ".md", ".rtf", ".csv", ".tsv", ".json", ".jsonl",
                  ".xml", ".html", ".htm", ".mhtml", ".yaml", ".yml",
                  ".log", ".rst", ".ini", ".cfg", ".toml", ".sql"}
_DATA_EXTS    = {".parquet", ".avro"}
_SVG_EXTS     = {".svg"}
_ARCHIVE_EXTS = {".zip", ".gz", ".tar"}
_EPUB_EXTS    = {".epub"}


# ---------------------------------------------------------------------------
# TEXT-BASED FORMATS (no OCR — extract text directly)
# ---------------------------------------------------------------------------
def extract_text_direct(file_path: str) -> str:
    """Read plain-text formats directly without OCR."""
    from pathlib import Path
    ext = Path(file_path).suffix.lower()

    # RTF — strip control words
    if ext == ".rtf":
        try:
            from striprtf.striprtf import rtf_to_text
            with open(file_path, "r", errors="replace") as f:
                return rtf_to_text(f.read())
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"RTF parse failed: {e}")

    # Parquet / Avro — convert to readable text via pyarrow
    if ext in _DATA_EXTS:
        try:
            import pyarrow.parquet as pq
            table = pq.read_table(file_path)
            return table.to_pandas().to_string(index=False)
        except Exception as e:
            logger.warning(f"Parquet read failed: {e}")
            return ""

    # SVG — extract text elements
    if ext in _SVG_EXTS:
        try:
            import re
            with open(file_path, "r", errors="replace") as f:
                content = f.read()
            texts = re.findall(r'<text[^>]*>(.*?)</text>', content, re.DOTALL)
            return " ".join(t.strip() for t in texts if t.strip())
        except Exception as e:
            logger.warning(f"SVG text extraction failed: {e}")
            return ""

    # EPUB — extract all chapter text
    if ext in _EPUB_EXTS:
        try:
            import zipfile, re
            results = []
            with zipfile.ZipFile(file_path) as zf:
                for name in zf.namelist():
                    if name.endswith((".html", ".xhtml", ".htm")):
                        html = zf.read(name).decode("utf-8", errors="replace")
                        text = re.sub(r'<[^>]+>', ' ', html)
                        text = re.sub(r'\s+', ' ', text).strip()
                        if text:
                            results.append(text)
            return "\n\n".join(results)
        except Exception as e:
            logger.warning(f"EPUB extraction failed: {e}")
            return ""

    # Generic plain-text fallback
    try:
        with open(file_path, "r", errors="replace") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"Direct text read failed for {file_path}: {e}")
        return ""


# ---------------------------------------------------------------------------
# MSG (Outlook) — text + embedded image OCR
# ---------------------------------------------------------------------------
def ocr_msg_file(file_path: str) -> str:
    """Extract text and OCR any embedded images from an Outlook .msg file."""
    import io
    from PIL import Image

    results = []
    try:
        import extract_msg
        msg = extract_msg.Message(file_path)
        body = msg.body or ""
        if body.strip():
            results.append(body.strip())
        # Attachments
        for att in (msg.attachments or []):
            try:
                data = att.data
                if not data:
                    continue
                # Try as image
                try:
                    image = Image.open(io.BytesIO(data))
                    text, conf = ocr_image_best_effort(image)
                    if text and conf > 0.2:
                        results.append(text)
                    continue
                except Exception:
                    pass
                # Try as nested document (PDF, DOCX, etc.)
                import tempfile, os
                suffix = (att.longFilename or att.shortFilename or "att.bin")
                suffix = os.path.splitext(suffix)[1].lower() or ".bin"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                try:
                    nested = ocr_any(tmp_path)
                    if nested:
                        results.append(nested)
                finally:
                    os.unlink(tmp_path)
            except Exception as e:
                logger.debug(f"MSG attachment skipped: {e}")
    except Exception as e:
        logger.error(f"MSG extraction failed: {e}")

    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# MBOX — multi-message mailbox
# ---------------------------------------------------------------------------
def ocr_mbox_file(file_path: str) -> str:
    """Extract text from all messages in an mbox file."""
    import mailbox, io
    from PIL import Image

    results = []
    try:
        mbox = mailbox.mbox(file_path)
        for msg in mbox:
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        results.append(payload.decode("utf-8", errors="replace"))
                elif ct.startswith("image/"):
                    try:
                        data = part.get_payload(decode=True)
                        image = Image.open(io.BytesIO(data))
                        text, conf = ocr_image_best_effort(image)
                        if text and conf > 0.2:
                            results.append(text)
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"MBOX extraction failed: {e}")

    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# ARCHIVE — extract and OCR contents
# ---------------------------------------------------------------------------
def ocr_archive(file_path: str) -> str:
    """Extract a ZIP/tar archive and OCR every file inside it."""
    import zipfile, tarfile, tempfile, os, shutil

    results = []
    tmp_dir = tempfile.mkdtemp()
    try:
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path) as zf:
                zf.extractall(tmp_dir)
        elif tarfile.is_tarfile(file_path):
            with tarfile.open(file_path) as tf:
                tf.extractall(tmp_dir)
        else:
            return ""

        for root, _, files in os.walk(tmp_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    text = ocr_any(fpath)
                    if text:
                        results.append(f"[{fname}]\n{text}")
                except Exception as e:
                    logger.debug(f"Archive member {fname} skipped: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# UNIVERSAL ENTRY POINT — auto-detect format and OCR appropriately
# ---------------------------------------------------------------------------
def ocr_any(file_path: str) -> str:
    """
    Universal OCR / text-extraction entry point.
    Detects file type and routes to the best strategy.
    Returns extracted text (empty string on total failure).

    Covers every format allowed by UniDocVerse:
      PDF (digital + scanned)              → Tesseract LSTM + EasyOCR fallback
      Images (JPG/PNG/BMP/WEBP/HEIC/GIF)  → Tesseract → EasyOCR
      Multi-page TIFF (fax, scanner)       → frame-by-frame OCR
      Office (DOCX/XLSX/PPTX/XLS/ODT…)   → embedded image OCR
      Email EML                            → MIME walk, image OCR
      Email MSG (Outlook)                  → extract_msg + attachment OCR
      MBOX (mailbox dump)                  → all messages + image OCR
      RTF                                  → striprtf → plain text
      SVG                                  → text element extraction
      EPUB                                 → chapter HTML → plain text
      Parquet/Avro                         → pyarrow → tabular text
      Archive (ZIP/TAR/GZ)                 → extract + recurse
      Plain text (TXT/MD/CSV/JSON/XML/…)  → direct read
    """
    from pathlib import Path
    ext = Path(file_path).suffix.lower()

    if ext in _PDF_EXTS:
        return ocr_pdf_best_effort(file_path)

    if ext in _TIFF_EXTS:
        return ocr_tiff(file_path)

    if ext in _IMAGE_EXTS:
        text, _ = ocr_image_file(file_path)
        return text

    if ext in _OFFICE_EXTS:
        return ocr_office_images(file_path)

    if ext == ".msg":
        return ocr_msg_file(file_path)

    if ext == ".mbox":
        return ocr_mbox_file(file_path)

    if ext == ".eml":
        return ocr_email_images(file_path)

    if ext in _ARCHIVE_EXTS:
        return ocr_archive(file_path)

    if ext in _EPUB_EXTS or ext in _SVG_EXTS or ext in _TEXT_EXTS or ext in _DATA_EXTS:
        return extract_text_direct(file_path)

    # Unknown — try image first, then PDF renderer, then direct read
    logger.warning(f"Unknown extension {ext!r} — trying all extraction paths")
    try:
        text, conf = ocr_image_file(file_path)
        if _quality_ok(text):
            return text
    except Exception:
        pass
    try:
        return ocr_pdf_best_effort(file_path)
    except Exception:
        pass
    return extract_text_direct(file_path)
