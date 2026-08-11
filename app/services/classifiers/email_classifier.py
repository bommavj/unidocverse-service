import re
import logging
from typing import Dict, Any, List
import joblib

logger = logging.getLogger(__name__)

# Optional ML artifacts
try:
    EMAIL_ML_MODEL = joblib.load("models/email_model.joblib")
    EMAIL_VECTORIZER = joblib.load("models/email_vectorizer.joblib")
    logger.info("Loaded email ML model and vectorizer")
except Exception:
    EMAIL_ML_MODEL = None
    EMAIL_VECTORIZER = None
    logger.info("Email ML model not found; running rule-first mode")

EMAIL_KEYWORDS = {
    "invoice": ["invoice attached", "invoice", "amount due", "please pay", "bill to"],
    "receipt": ["receipt", "payment received", "payment confirmation"],
    "purchase_order": ["purchase order", "po number", "order confirmation"],
    "contract": ["attached contract", "agreement", "signed agreement"],
}

EMAIL_REGEX = {
    "invoice": re.compile(r"\bINV[-_ ]?\d{3,}\b", re.I),
    "purchase_order": re.compile(r"\bPO[-_ ]?\d{3,}\b", re.I),
}

def _email_text(parsed: Dict[str, Any]) -> str:
    parts: List[str] = []
    headers = parsed.get("email_headers", {})
    if headers.get("subject"):
        parts.append(headers["subject"])
    if parsed.get("body"):
        parts.append(parsed["body"])
    # include attachment names
    for a in parsed.get("attachments", [])[:5]:
        parts.append(a.get("filename", ""))
    return " ".join(parts).lower()

class EmailClassifier:
    """
    Classifies emails using subject, body, headers, and attachment names.
    """

    def __init__(self, ml_model=EMAIL_ML_MODEL, vectorizer=EMAIL_VECTORIZER, rule_weight: float = 0.7):
        self.ml_model = ml_model
        self.vectorizer = vectorizer
        self.rule_weight = float(rule_weight)

    def _rule_scores(self, text: str) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for dtype, kws in EMAIL_KEYWORDS.items():
            count = sum(1 for k in kws if k in text)
            regex_match = 1 if EMAIL_REGEX.get(dtype) and EMAIL_REGEX[dtype].search(text) else 0
            scores[dtype] = min(1.0, (count * 0.4) + (regex_match * 0.9))
        return scores

    def _ml_scores(self, text: str) -> Dict[str, float]:
        if not self.ml_model or not self.vectorizer:
            return {}
        try:
            vec = self.vectorizer.transform([text])
            probs = self.ml_model.predict_proba(vec)[0]
            classes = list(self.ml_model.classes_)
            return dict(zip(classes, probs.tolist()))
        except Exception:
            logger.exception("Email ML prediction failed")
            return {}

    def _ensemble(self, rule: Dict[str, float], ml: Dict[str, float]) -> Dict[str, float]:
        final: Dict[str, float] = {}
        for dtype in set(list(rule.keys()) + list(ml.keys())):
            r = rule.get(dtype, 0.0)
            m = ml.get(dtype, 0.0)
            final[dtype] = (self.rule_weight * r) + ((1.0 - self.rule_weight) * m)
        return final

    def classify(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        text = _email_text(parsed)
        rule = self._rule_scores(text)
        ml = self._ml_scores(text)
        final = self._ensemble(rule, ml)

        if final:
            best_type, best_score = max(final.items(), key=lambda x: x[1])
        else:
            best_type, best_score = "email", 0.0

        matched = []
        for dtype, kws in EMAIL_KEYWORDS.items():
            found = [k for k in kws if k in text]
            if found:
                matched.append({"type": dtype, "keywords": found})

        return {
            "type": best_type,
            "confidence": float(best_score),
            "reasoning": "ensemble(rule+ml)" if ml else "rule_only",
            "matched_keywords": matched,
            "rule_scores": rule,
            "ml_scores": ml,
        }
