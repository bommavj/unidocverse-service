"""One lock for every model load. transformers/sentence-transformers can't
build models concurrently in one process — parallel loads (embeddings +
reranker + GLiClass warming up together) left tensors on the 'meta' device:
"Cannot copy out of meta tensor; no data!"."""
import threading

MODEL_LOAD_LOCK = threading.RLock()
