import os
from pathlib import Path
from threading import Lock

from sentence_transformers import SentenceTransformer


class EmbeddingModelFactory:
    """Thread-safe singleton factory for SentenceTransformer embedding model."""

    _instance: SentenceTransformer = None
    _lock = Lock()

    # Project root (…/unidocverse-service)
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

    # Local model path (single source of truth)
    MODEL_PATH = Path(
        os.getenv(
            "MODEL_PATH",
            PROJECT_ROOT / "models" / "all-mpnet-base-v2",
        )
    )

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        """
        Return a singleton SentenceTransformer instance.
        Model is loaded strictly from local disk (offline mode).
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    # Enforce offline mode
                    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

                    if not cls.MODEL_PATH.exists():
                        raise FileNotFoundError(
                            f"Embedding model not found at: {cls.MODEL_PATH}"
                        )

                    cls._instance = SentenceTransformer(
                        str(cls.MODEL_PATH)
                    )

        return cls._instance
