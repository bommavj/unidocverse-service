"""
First-launch download of the search reranker (Starter edition).

The Starter DMG leaves out bge-reranker-v2-m3 (2.1 GB) to stay under GitHub's 2 GB file limit.
When no bundled copy exists and RERANKER_DOWNLOAD_URL is set, it is fetched once in the
background from the UniDocVerse download server (never a third party), checked against
RERANKER_SHA256, and unpacked into RERANKER_DOWNLOAD_DIR. A broken download resumes from
where it stopped. Until it is ready, search runs without reranking and answers say so.

Env (set by the desktop app):
  RERANKER_DOWNLOAD_URL   https://…/models/bge-reranker-v2-m3.tar.gz
  RERANKER_SHA256         expected checksum of that archive
  RERANKER_DOWNLOAD_DIR   folder that will hold bge-reranker-v2-m3/
"""
from __future__ import annotations

import hashlib
import logging
import os
import tarfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

NAME = "bge-reranker-v2-m3"
_status: Dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": None}
_started = False
_lock = threading.Lock()


def target_dir() -> Optional[Path]:
    d = os.getenv("RERANKER_DOWNLOAD_DIR")
    return Path(d) / NAME if d else None


def downloaded() -> bool:
    t = target_dir()
    return bool(t and (t / "model.safetensors").exists())


def enabled() -> bool:
    return bool(os.getenv("RERANKER_DOWNLOAD_URL") and os.getenv("RERANKER_SHA256") and os.getenv("RERANKER_DOWNLOAD_DIR"))


def status() -> Dict[str, Any]:
    if not enabled():
        return {"state": "bundled", "done": 0, "total": 0, "error": None}
    if downloaded():
        return {"state": "ready", "done": _status["total"], "total": _status["total"], "error": None}
    return dict(_status)


def start() -> None:
    """Begin the download in the background once per process (no-op when bundled or ready)."""
    global _started
    with _lock:
        if _started or not enabled() or downloaded():
            return
        _started = True
    threading.Thread(target=_run, name="reranker-download", daemon=True).start()


def _run() -> None:
    import httpx
    url, want = os.environ["RERANKER_DOWNLOAD_URL"], os.environ["RERANKER_SHA256"].strip().lower()
    root = target_dir().parent
    root.mkdir(parents=True, exist_ok=True)
    part = root / f"{NAME}.tar.gz.part"
    for attempt in range(1, 6):
        try:
            _status.update(state="downloading", error=None)
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            with httpx.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as r:
                if r.status_code == 416:                 # already complete
                    pass
                else:
                    r.raise_for_status()
                    if have and r.status_code != 206:    # server ignored Range: start over
                        have = 0
                    total = int(r.headers.get("content-length", 0)) + have
                    _status.update(total=total, done=have)
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            _status["done"] += len(chunk)
            _status["state"] = "verifying"
            h = hashlib.sha256()
            with open(part, "rb") as f:
                for block in iter(lambda: f.read(1 << 22), b""):
                    h.update(block)
            if h.hexdigest() != want:
                part.unlink(missing_ok=True)
                raise ValueError("checksum mismatch — download discarded")
            _status["state"] = "unpacking"
            tmp = root / f"{NAME}.unpack"
            with tarfile.open(part) as tar:
                tar.extractall(tmp, filter="data")
            src = tmp / NAME if (tmp / NAME).exists() else tmp
            src.rename(target_dir())
            part.unlink(missing_ok=True)
            if tmp.exists():
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
            _status.update(state="ready", error=None)
            logger.info("🔁 Reranker downloaded and verified")
            from app.services import hybrid_search
            hybrid_search.reload_reranker()
            return
        except Exception as e:
            _status.update(state="retrying", error=str(e)[:200])
            logger.warning(f"Reranker download attempt {attempt} failed: {e}")
            time.sleep(min(60, 5 * attempt))
    _status["state"] = "failed"
