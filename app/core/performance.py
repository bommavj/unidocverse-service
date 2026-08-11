"""
UniDocVerse Performance Layer

1. Embedding cache      — LRU on text hash, avoids re-encoding same text
2. Response cache       — TTL cache on (question, doc_type, lang) hash
3. LLM request queue    — asyncio queue, single Ollama worker, prevents pile-up
4. Upload job registry  — tracks background upload jobs with progress
"""
import asyncio
import hashlib
import logging
import time
import uuid
from collections import OrderedDict
from threading import Lock
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ── 1. Embedding cache ────────────────────────────────────────────────────────

class EmbeddingCache:
    """
    Thread-safe LRU cache for embedding vectors.
    Keyed by SHA-256 of the input text. Stores numpy arrays.
    """
    def __init__(self, maxsize: int = 1024):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def get(self, text: str):
        key = self._key(text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self.hits += 1
                return self._cache[key]
            self.misses += 1
            return None

    def set(self, text: str, vector):
        key = self._key(text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
            self._cache[key] = vector

    def stats(self) -> Dict:
        total = self.hits + self.misses
        return {
            "size": len(self._cache),
            "maxsize": self._maxsize,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0,
        }

    def clear(self):
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0


embedding_cache = EmbeddingCache(maxsize=1024)


def cached_encode(model, text: str):
    """
    Encode text using the embedding model, with LRU caching.
    Drop-in replacement for model.encode(text).
    """
    cached = embedding_cache.get(text)
    if cached is not None:
        return cached
    vector = model.encode(text)
    embedding_cache.set(text, vector)
    return vector


# ── 2. Response cache ─────────────────────────────────────────────────────────

class ResponseCache:
    """
    TTL-based in-memory cache for full LLM responses.
    Keyed by (question, doc_type, lang). Evicts on access after TTL.
    """
    def __init__(self, maxsize: int = 256, ttl_seconds: int = 300):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def _key(self, question: str, doc_type: str = "", lang: str = "en") -> str:
        raw = f"{question.strip().lower()}|{doc_type}|{lang}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    def get(self, question: str, doc_type: str = "", lang: str = "en") -> Optional[str]:
        key = self._key(question, doc_type, lang)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._cache[key]
                self.misses += 1
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            return value

    def set(self, question: str, response: str, doc_type: str = "", lang: str = "en"):
        key = self._key(question, doc_type, lang)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
            self._cache[key] = (response, time.monotonic())

    def stats(self) -> Dict:
        total = self.hits + self.misses
        return {
            "size": len(self._cache),
            "maxsize": self._maxsize,
            "ttl_seconds": self._ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0,
        }

    def invalidate(self, question: str, doc_type: str = "", lang: str = "en"):
        key = self._key(question, doc_type, lang)
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0


response_cache = ResponseCache(maxsize=256, ttl_seconds=300)


# ── 3. LLM Request Queue ──────────────────────────────────────────────────────

class LLMRequest:
    def __init__(self, prompt: str, model: str, options: Dict,
                 request_id: str = None):
        self.request_id = request_id or str(uuid.uuid4())
        self.prompt = prompt
        self.model = model
        self.options = options
        self.future: asyncio.Future = None
        self.enqueued_at = time.monotonic()

    @property
    def wait_time(self) -> float:
        return time.monotonic() - self.enqueued_at


class LLMQueue:
    """
    Single asyncio queue draining into one Ollama worker.
    Prevents concurrent inference pileup — callers await their future.
    Supports per-user depth limit to prevent monopolisation.
    """
    def __init__(self, maxsize: int = 50, per_session_limit: int = 3):
        self._queue: asyncio.Queue = None   # created in async context
        self._maxsize = maxsize
        self._per_session_limit = per_session_limit
        self._session_counts: Dict[str, int] = {}
        self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        self._worker_task: Optional[asyncio.Task] = None
        self._stats = {"processed": 0, "rejected": 0, "errors": 0}
        self._started = False

    def _get_queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self._maxsize)
        return self._queue

    async def start_worker(self):
        if self._started:
            return
        self._started = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("LLM queue worker started (maxsize=%d)", self._maxsize)

    async def _worker(self):
        import ollama as _ollama
        client = _ollama.Client()
        while True:
            try:
                req: LLMRequest = await self._get_queue().get()
                session_id = getattr(req, "session_id", "")
                try:
                    tokens = []
                    stream = client.generate(
                        model=req.model,
                        prompt=req.prompt,
                        stream=True,
                        options=req.options,
                    )
                    for chunk in stream:
                        token = chunk.get("response", "")
                        if token:
                            tokens.append(token)
                    result = "".join(tokens)
                    self._stats["processed"] += 1
                    if req.future and not req.future.done():
                        req.future.get_event_loop().call_soon_threadsafe(
                            req.future.set_result, result
                        )
                except Exception as e:
                    self._stats["errors"] += 1
                    logger.error("LLM worker error: %s", e)
                    if req.future and not req.future.done():
                        try:
                            req.future.get_event_loop().call_soon_threadsafe(
                                req.future.set_exception, e
                            )
                        except Exception:
                            pass
                finally:
                    if session_id in self._session_counts:
                        self._session_counts[session_id] = max(
                            0, self._session_counts[session_id] - 1
                        )
                    self._get_queue().task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("LLM queue worker outer error: %s", e)

    async def enqueue(
        self, prompt: str, model: str, options: Dict,
        session_id: str = ""
    ) -> asyncio.Future:
        """
        Add an inference request to the queue.
        Returns a Future that resolves to the full generated string.
        Raises QueueFullError if per-session limit exceeded.
        """
        q = self._get_queue()
        session_count = self._session_counts.get(session_id, 0)
        if session_id and session_count >= self._per_session_limit:
            self._stats["rejected"] += 1
            raise LLMQueueFullError(
                f"Too many pending requests for this session "
                f"({session_count}/{self._per_session_limit}). "
                f"Please wait for a response before sending more."
            )

        if q.full():
            self._stats["rejected"] += 1
            raise LLMQueueFullError(
                f"LLM queue is full ({self._maxsize} requests). "
                f"Please try again in a moment."
            )

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        req = LLMRequest(prompt=prompt, model=model, options=options)
        req.future = future
        req.session_id = session_id   # type: ignore

        self._session_counts[session_id] = session_count + 1
        await q.put(req)
        logger.debug("LLM queued: session=%s queue_depth=%d", session_id, q.qsize())
        return future

    def queue_depth(self) -> int:
        return self._get_queue().qsize() if self._queue else 0

    def stats(self) -> Dict:
        return {
            **self._stats,
            "queue_depth": self.queue_depth(),
            "maxsize": self._maxsize,
        }


class LLMQueueFullError(Exception):
    pass


llm_queue = LLMQueue(maxsize=50, per_session_limit=3)


# ── 4. Upload Job Registry ────────────────────────────────────────────────────

class JobStatus:
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"


class UploadJobRegistry:
    """
    In-memory registry of background upload jobs.
    Stores status + progress + result for each job_id.
    Jobs expire after 2 hours.
    """
    _TTL = 7200  # 2 hours

    def __init__(self):
        self._jobs: Dict[str, Dict] = {}
        self._lock = Lock()

    def create(self, filename: str, total_files: int = 1) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "job_id":      job_id,
                "status":      JobStatus.PENDING,
                "filename":    filename,
                "total":       total_files,
                "processed":   0,
                "results":     [],
                "error":       None,
                "created_at":  time.monotonic(),
                "updated_at":  time.monotonic(),
            }
        return job_id

    def update(self, job_id: str, **kwargs):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)
                self._jobs[job_id]["updated_at"] = time.monotonic()

    def get(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and time.monotonic() - job["created_at"] > self._TTL:
                del self._jobs[job_id]
                return None
            return dict(job) if job else None

    def evict_expired(self):
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._jobs.items()
                       if now - v["created_at"] > self._TTL]
            for k in expired:
                del self._jobs[k]

    def stats(self) -> Dict:
        with self._lock:
            statuses = [v["status"] for v in self._jobs.values()]
        return {
            "total_jobs": len(statuses),
            "pending":    statuses.count(JobStatus.PENDING),
            "running":    statuses.count(JobStatus.RUNNING),
            "done":       statuses.count(JobStatus.DONE),
            "failed":     statuses.count(JobStatus.FAILED),
        }


job_registry = UploadJobRegistry()


# ── 5. Performance stats endpoint helper ──────────────────────────────────────

def get_perf_stats() -> Dict:
    return {
        "embedding_cache": embedding_cache.stats(),
        "response_cache":  response_cache.stats(),
        "llm_queue":       llm_queue.stats(),
        "upload_jobs":     job_registry.stats(),
    }
