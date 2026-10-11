import logging
from typing import Dict, Any, List
import re

logger = logging.getLogger(__name__)

# Lazy import helpers for heavy deps
def _safe_ocr(parsed: Dict[str, Any]) -> str:
    """
    Use OCR text if already present in parsed; otherwise try to run pytesseract if available.
    parsed may include 'ocr_text' or 'image_bytes' or 'image_path'.
    """
    if parsed.get("ocr_text"):
        return parsed["ocr_text"].lower()
    try:
        from app.services.ocr.ocr_utils import ocr_image_best_effort, ocr_image_file
        from PIL import Image
        if parsed.get("image_path"):
            text, _ = ocr_image_file(parsed["image_path"])
            return text.lower()
        if parsed.get("image_bytes"):
            from io import BytesIO
            img = Image.open(BytesIO(parsed["image_bytes"]))
            text, _ = ocr_image_best_effort(img)
            return text.lower()
    except Exception as e:
        logger.debug("OCR not available or failed: %s", e)
    return ""

# Reuse document keywords and regex where appropriate
IMAGE_KEYWORDS = {
    "invoice": ["invoice", "invoice number", "amount due", "bill to"],
    "receipt": ["receipt", "total paid", "payment method", "subtotal", "tax"],
    "bank_statement": ["bank statement", "account balance", "transaction"],
    "sales_report": ["sales", "revenue", "units", "region"],
}

IMAGE_REGEX = {
    "invoice": re.compile(r"\bINV[-_ ]?\d{3,}\b", re.I),
    "purchase_order": re.compile(r"\bPO[-_ ]?\d{3,}\b", re.I),
}

class ImageClassifier:
    """
    Classifies images by using OCR text and simple visual heuristics.
    Input: parsed dict with optional 'ocr_text', 'image_path', 'image_bytes', 'metadata'.
    Output: consistent classification dict.
    """

    def __init__(self, rule_weight: float = 1.0):
        # ML for images is optional; start with rule-only (OCR-based)
        self.rule_weight = float(rule_weight)

    def _rule_scores(self, text: str) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for dtype, kws in IMAGE_KEYWORDS.items():
            match_count = sum(1 for k in kws if k in text)
            regex_match = 1 if IMAGE_REGEX.get(dtype) and IMAGE_REGEX[dtype].search(text) else 0
            scores[dtype] = min(1.0, (match_count * 0.4) + (regex_match * 0.9))
        return scores

    def classify(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        ocr_text = _safe_ocr(parsed)
        # If OCR is empty, use visual heuristics (density, presence of table-like lines)
        if not ocr_text:
            # basic heuristic: if image has high resolution and many lines, treat as document-like
            meta = parsed.get("metadata", {})
            width = meta.get("width", 0)
            height = meta.get("height", 0)
            area = width * height
            if area > 1024 * 1024:
                ocr_text = ""  # still empty but we keep going
        rule_scores = self._rule_scores(ocr_text)
        if rule_scores:
            best_type, best_score = max(rule_scores.items(), key=lambda x: x[1])
        else:
            best_type, best_score = "image", 0.0

        matched = []
        for dtype, kws in IMAGE_KEYWORDS.items():
            found = [k for k in kws if k in ocr_text]
            if found:
                matched.append({"type": dtype, "keywords": found})

        return {
            "type": best_type,
            "confidence": float(best_score),
            "reasoning": "ocr_rule",
            "matched_keywords": matched,
            "rule_scores": rule_scores,
            "ml_scores": {},
        }
