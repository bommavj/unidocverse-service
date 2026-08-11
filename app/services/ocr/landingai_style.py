# app/services/ocr/landingai_style.py
#
# EasyOCR with proper reading order reconstruction.
# EasyOCR returns bounding boxes — we sort by position to reconstruct
# correct top-to-bottom, column-aware reading order.
# This fixes the multi-column layout interleaving problem without
# any additional model downloads.

import os
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# MODEL CACHE PATH
# File location: <project_root>/app/services/ocr/landingai_style.py
# dirname x4 = <project_root>/
# ---------------------------------------------------------
_THIS_FILE        = os.path.abspath(__file__)
BASE_DIR          = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_THIS_FILE))))
EASYOCR_MODEL_DIR = os.path.join(BASE_DIR, "models", "easyocr")
os.makedirs(EASYOCR_MODEL_DIR, exist_ok=True)

# ---------------------------------------------------------
# LAZY SINGLETON
# ---------------------------------------------------------
_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        import torch

        if torch.backends.mps.is_available():
            gpu, device = True, "mps"
        elif torch.cuda.is_available():
            gpu, device = True, "cuda"
        else:
            gpu, device = False, "cpu"

        logger.info(f"🔧 Loading EasyOCR (device={device}, model_dir={EASYOCR_MODEL_DIR})")
        _reader = easyocr.Reader(
            ['en'],
            gpu=gpu,
            model_storage_directory=EASYOCR_MODEL_DIR,
            download_enabled=True,
            verbose=False,
        )
        logger.info(f"✅ EasyOCR ready on {device}")
    return _reader


# ---------------------------------------------------------
# READING ORDER RECONSTRUCTION
#
# EasyOCR returns bounding boxes as:
#   [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]  (top-left clockwise)
#
# Multi-column PDFs get interleaved because EasyOCR processes
# all text regions without respecting column boundaries.
#
# Fix: cluster text blocks into columns by X midpoint,
# then sort each column top-to-bottom.
# ---------------------------------------------------------

def _bbox_midpoint(bbox):
    """Return (x_mid, y_mid) of a bounding box."""
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _detect_columns(results, page_width):
    """
    Cluster text blocks into columns based on X midpoint.
    Returns list of column X boundaries [(x_start, x_end), ...]
    """
    if not results:
        return []

    # Get all X midpoints
    x_mids = [_bbox_midpoint(r[0])[0] for r in results]

    if not x_mids:
        return []

    # Simple column detection: if spread > 40% of page width,
    # assume multi-column layout
    x_range = max(x_mids) - min(x_mids)
    if x_range < page_width * 0.4:
        return [(0, page_width)]  # single column

    # Find column split point — largest gap in X midpoints
    x_sorted = sorted(set(int(x) for x in x_mids))
    max_gap   = 0
    split_x   = page_width // 2

    for i in range(1, len(x_sorted)):
        gap = x_sorted[i] - x_sorted[i-1]
        if gap > max_gap:
            max_gap = gap
            split_x = (x_sorted[i] + x_sorted[i-1]) // 2

    return [(0, split_x), (split_x, page_width)]


def _sort_by_reading_order(results, img_width):
    """
    Sort EasyOCR results into natural reading order:
    - Detect columns
    - Within each column, sort top-to-bottom
    - Return columns left-to-right
    """
    if not results:
        return []

    columns     = _detect_columns(results, img_width)
    col_buckets = [[] for _ in columns]

    for item in results:
        bbox = item[0]
        x_mid, y_mid = _bbox_midpoint(bbox)

        # Assign to column
        assigned = False
        for i, (x_start, x_end) in enumerate(columns):
            if x_start <= x_mid < x_end:
                col_buckets[i].append((y_mid, item))
                assigned = True
                break

        if not assigned:
            col_buckets[-1].append((y_mid, item))

    # Sort each column by Y (top to bottom)
    ordered = []
    for bucket in col_buckets:
        bucket.sort(key=lambda x: x[0])
        ordered.extend([item for _, item in bucket])

    return ordered


def extract_text_landingai_style(pdf_path: str) -> str:
    import fitz
    import cv2
    import numpy as np

    reader   = _get_reader()
    doc      = fitz.open(pdf_path)
    all_text = []

    for page_num, page in enumerate(doc):
        logger.info(f"   📄 OCR page {page_num + 1}/{len(doc)}")

        pix = page.get_pixmap(dpi=200)
        img = cv2.imdecode(
            np.frombuffer(pix.tobytes(), np.uint8),
            cv2.IMREAD_COLOR
        )
        img_width = img.shape[1]

        try:
            # Get raw results with bounding boxes
            results = reader.readtext(img, detail=1, paragraph=False)

            # Reconstruct correct reading order
            ordered = _sort_by_reading_order(results, img_width)

            # Extract text in correct order
            page_lines = []
            for (bbox, text, confidence) in ordered:
                text = text.strip()
                if text and confidence > 0.4:
                    page_lines.append(text)

            if page_lines:
                all_text.append("\n".join(page_lines))
                logger.info(f"   ✅ Page {page_num + 1}: {len(page_lines)} text blocks")

        except Exception as e:
            logger.warning(f"   ⚠️ OCR failed on page {page_num + 1}: {e}")
            continue

    extracted = "\n\n".join(all_text)
    logger.info(f"✅ OCR complete — {len(extracted)} chars extracted")
    return extracted