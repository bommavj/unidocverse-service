"""
First-launch model downloads (single macOS installer).

The installer leaves the large models out so one notarized DMG serves every edition. They are
fetched from the UniDocVerse download server (never a third party), checked against a pinned
SHA-256, and unpacked into the app's data folder. A broken download resumes where it stopped.

  reranker  bge-reranker-v2-m3 (search). Downloaded automatically; until it is ready, search
            runs without reranking and answers say so.
  answer    the built-in answer model (Ollama model files). Downloaded only when the user picks
            "Built-in model" on Home; users of their own Ollama or a cloud key never fetch it.

Env (set by the desktop app):
  RERANKER_DOWNLOAD_URL / RERANKER_SHA256 / RERANKER_DOWNLOAD_DIR  (dir holds bge-reranker-v2-m3/)
  ANSWER_MODEL_URL / ANSWER_MODEL_SHA256 / ANSWER_MODEL_DIR / ANSWER_MODEL_NAME
      (dir is Ollama's OLLAMA_MODELS folder; name is the model to switch to, e.g. phi3:mini)
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

NAME = "bge-reranker-v2-m3"


class _Pack:
    def __init__(self, key: str, env: str, folder: Optional[str], marker: Callable[[Path], bool],
                 on_ready: Optional[Callable[[], None]] = None):
        self.key, self.env, self.folder, self.marker, self.on_ready = key, env, folder, marker, on_ready
        self.status: Dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": None}
        self.started = False
        self.lock = threading.Lock()

    def url(self) -> Optional[str]:
        return os.getenv(f"{self.env}_URL")

    def sha(self) -> Optional[str]:
        return os.getenv(f"{self.env}_SHA256")

    def root(self) -> Optional[Path]:
        d = os.getenv(f"{self.env}_DIR")
        return Path(d) if d else None

    def target(self) -> Optional[Path]:
        r = self.root()
        return (r / self.folder if self.folder else r) if r else None

    def enabled(self) -> bool:
        return bool(self.url() and self.sha() and self.root())

    def ready(self) -> bool:
        t = self.target()
        return bool(t and self.marker(t))

    def state(self) -> Dict[str, Any]:
        if not self.enabled():
            return {"state": "bundled", "done": 0, "total": 0, "error": None}
        if self.ready():
            return {"state": "ready", "done": self.status["total"], "total": self.status["total"], "error": None}
        return dict(self.status)

    def start(self) -> None:
        with self.lock:
            if self.started or not self.enabled() or self.ready():
                return
            self.started = True
        threading.Thread(target=self._run, name=f"{self.key}-download", daemon=True).start()

    def _run(self) -> None:
        import httpx
        url, want = self.url(), self.sha().strip().lower()
        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        part = root / f".{self.key}.tar.gz.part"
        for attempt in range(1, 6):
            try:
                self.status.update(state="downloading", error=None)
                have = part.stat().st_size if part.exists() else 0
                headers = {"Range": f"bytes={have}-"} if have else {}
                with httpx.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as r:
                    if r.status_code != 416:                 # 416 = already complete
                        r.raise_for_status()
                        if have and r.status_code != 206:    # server ignored Range: start over
                            have = 0
                        self.status.update(total=int(r.headers.get("content-length", 0)) + have, done=have)
                        with open(part, "ab" if have else "wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                f.write(chunk)
                                self.status["done"] += len(chunk)
                self.status["state"] = "verifying"
                h = hashlib.sha256()
                with open(part, "rb") as f:
                    for block in iter(lambda: f.read(1 << 22), b""):
                        h.update(block)
                if h.hexdigest() != want:
                    part.unlink(missing_ok=True)
                    raise ValueError("checksum mismatch — download discarded")
                self.status["state"] = "unpacking"
                tmp = root / f".{self.key}.unpack"
                shutil.rmtree(tmp, ignore_errors=True)
                with tarfile.open(part) as tar:
                    tar.extractall(tmp, filter="data")
                if self.folder:                              # one model folder → move into place
                    src = tmp / self.folder if (tmp / self.folder).exists() else tmp
                    src.rename(self.target())
                else:                                        # Ollama tree → merge manifests/ and blobs/
                    for top in tmp.iterdir():
                        dest = root / top.name
                        for p in top.rglob("*"):
                            if p.is_file():
                                (dest / p.relative_to(top)).parent.mkdir(parents=True, exist_ok=True)
                                p.replace(dest / p.relative_to(top))
                shutil.rmtree(tmp, ignore_errors=True)
                part.unlink(missing_ok=True)
                self.status.update(state="ready", error=None)
                logger.info(f"⬇️ {self.key} model downloaded and verified")
                if self.on_ready:
                    self.on_ready()
                return
            except Exception as e:
                self.status.update(state="retrying", error=str(e)[:200])
                logger.warning(f"{self.key} download attempt {attempt} failed: {e}")
                time.sleep(min(60, 5 * attempt))
        self.status["state"] = "failed"
        self.started = False                                 # the user can try again


def _reranker_ready() -> None:
    from app.services import hybrid_search
    hybrid_search.reload_reranker()


def _answer_ready() -> None:
    """Use the built-in model for answers once it is on disk."""
    name = os.getenv("ANSWER_MODEL_NAME", "phi3:mini")
    try:
        from app.core import config as app_config
        app_config.set_model(name)                            # same as switching in Integrations; persisted
    except Exception as e:
        logger.warning(f"Built-in model downloaded but not selected: {e}")


RERANKER = _Pack("reranker", "RERANKER_DOWNLOAD", NAME, lambda t: (t / "model.safetensors").exists(), _reranker_ready)
ANSWER = _Pack("answer", "ANSWER_MODEL", None,
               lambda t: (t / "manifests").exists() and any((t / "manifests").rglob("*")), _answer_ready)


# Reranker API used by hybrid_search
def target_dir() -> Optional[Path]:
    return RERANKER.target()


def downloaded() -> bool:
    return RERANKER.ready()


def enabled() -> bool:
    return RERANKER.enabled()


def status() -> Dict[str, Any]:
    return RERANKER.state()


def start() -> None:
    RERANKER.start()
