"""
Zero-shot decision classifier (GLiClass) for the research agent.

Scores a question against a fixed label set in one encoder pass (~100ms CPU)
and returns calibrated-ish per-label scores. Callers fall back to their
keyword/LLM logic whenever this returns None.

Env:
  ZERO_SHOT_ROUTER=0            disable
  ZERO_SHOT_ROUTER_MODEL=...    override HF model id
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MODEL_ID = os.getenv("ZERO_SHOT_ROUTER_MODEL", "knowledgator/gliclass-base-v3.0")
_ENABLED = os.getenv("ZERO_SHOT_ROUTER", "1") != "0"
_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "zero_shot_router.jsonl"

_pipe = None
_load_failed = False
_lock = threading.Lock()


def _get_pipe():
    global _pipe, _load_failed
    if _pipe is not None or _load_failed or not _ENABLED:
        return _pipe
    with _lock:
        if _pipe is not None or _load_failed:
            return _pipe
        try:
            t0 = time.perf_counter()
            from gliclass import GLiClassModel, ZeroShotClassificationPipeline
            from transformers import AutoTokenizer
            from app.core.model_lock import MODEL_LOAD_LOCK
            with MODEL_LOAD_LOCK:
                model = GLiClassModel.from_pretrained(_MODEL_ID)
                tok = AutoTokenizer.from_pretrained(_MODEL_ID, add_prefix_space=True)
            _pipe = ZeroShotClassificationPipeline(
                model, tok, classification_type="multi-label", device="cpu", progress_bar=False,
                max_classes=64,
            )
            logger.info(f"🧭 Zero-shot router loaded {_MODEL_ID} in {time.perf_counter() - t0:.1f}s")
        except Exception as e:
            _load_failed = True
            logger.warning(f"⚠️ Zero-shot router unavailable ({e}) — keyword/LLM fallback only")
    return _pipe


def warm_up() -> None:
    """Load the model and run one inference so the first real question is fast."""
    if _get_pipe() is not None:
        score("warm up", {"a": "a", "b": "b"}, decision="warmup")


def score(text: str, labels: Dict[str, str], decision: str = "") -> Optional[List[Tuple[str, float]]]:
    """
    labels: {key: natural-language description}. Descriptions are what the
    model reads; keys are what callers get back.
    Returns [(key, score)] sorted high→low, or None if the model is unavailable.
    """
    pipe = _get_pipe()
    if pipe is None or not text or not labels:
        return None
    # "." is GLiClass's hierarchy separator — keep descriptions flat
    desc_to_key = {d.replace(".", ""): k for k, d in labels.items()}
    try:
        t0 = time.perf_counter()
        with _lock:  # pipeline is not guaranteed thread-safe
            raw = pipe(text, list(desc_to_key), threshold=0.0)[0]
        ranked = sorted(
            ((desc_to_key[r["label"]], float(r["score"])) for r in raw),
            key=lambda x: -x[1],
        )
        _log(decision, text, ranked, (time.perf_counter() - t0) * 1000)
        return ranked
    except Exception as e:
        logger.warning(f"⚠️ Zero-shot scoring failed: {e}")
        return None


def pick(
    ranked: Optional[List[Tuple[str, float]]],
    min_score: float = 0.5,
    min_margin: float = 0.15,
) -> Optional[str]:
    """
    Single-label decision. Scores are independent sigmoids (several labels can
    be high at once), so require both an absolute score and a lead over the
    runner-up; otherwise the question is ambiguous → None.
    """
    if not ranked:
        return None
    top_key, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= min_score and top - second >= min_margin:
        return top_key
    return None


def pick_multi(
    ranked: Optional[List[Tuple[str, float]]],
    min_score: float = 0.6,
    max_labels: int = 2,
) -> List[str]:
    """Multi-label decision for questions that genuinely span labels."""
    if not ranked:
        return []
    return [k for k, s in ranked[:max_labels] if s >= min_score]


def _log(decision: str, text: str, ranked: List[Tuple[str, float]], ms: float) -> None:
    # Prediction log → future fine-tuning data (SetFit / ModernBERT).
    if decision == "warmup":
        return
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "decision": decision,
                "text": text[:500],
                "top": [[k, round(s, 3)] for k, s in ranked[:5]],
                "ms": round(ms, 1),
            }) + "\n")
    except Exception:
        pass
