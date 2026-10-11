"""
app/services/folder_watcher_service.py
Manages multiple folder watchers — lifecycle, state, reports
"""

import asyncio
import hashlib
import json
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.core.config import Settings

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# SUPPORTED_EXTENSIONS = {
#     ".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif",
#     ".csv", ".xlsx", ".xls", ".docx", ".doc", ".txt",
# }

MB               = 1024 * 1024
FILE_STABLE_SECS = 3
DRAIN_INTERVAL   = 2      # seconds between queue drain checks
MAX_RETRIES      = 3
RETRY_DELAY      = 5

BATCH_SIZE_TINY  = 10   # < 1 MB
BATCH_SIZE_SMALL = 5    # 1–10 MB
BATCH_SIZE_LARGE = 2    # > 10 MB


class WatcherStatus(str, Enum):
    IDLE    = "idle"
    ACTIVE  = "active"
    PAUSED  = "paused"
    ERROR   = "error"


# ── Watchdog handler ──────────────────────────────────────────────────────────

class _IngestHandler(FileSystemEventHandler):
    def __init__(self, pending: dict, lock: threading.Lock, processed_dir: str):
        self._pending      = pending
        self._lock         = lock
        self._processed_dir = processed_dir

    def _enqueue(self, path: str):
        p = Path(path)
        if p.suffix.lower() not in Settings.ALLOWED_EXTENSIONS:
            return
        # Don't watch processed/ or reports/ subdirs
        if "processed" in p.parts or "reports" in p.parts:
            return
        with self._lock:
            self._pending[path] = time.time()

    def on_created(self,  e): not e.is_directory and self._enqueue(e.src_path)
    def on_moved(self,    e): not e.is_directory and self._enqueue(e.dest_path)
    def on_modified(self, e): not e.is_directory and self._enqueue(e.src_path)


# ── Per-folder watcher ────────────────────────────────────────────────────────

class FolderWatcher:
    def __init__(self, config: dict, api_url: str, on_event=None):
        self.config       = config
        self.folder_id    = config["id"]
        self.watch_path   = Path(config["path"])
        self.api_url      = api_url.rstrip("/")
        self.on_event     = on_event   # async callback for SSE
        self.batch_size   = config.get("batch_size")
        self.max_workers  = config.get("max_workers", 3)
        self.status       = WatcherStatus.IDLE

        # State
        self._pending:    dict        = {}
        self._lock        = threading.Lock()
        self._seen_hashes: set        = set()
        self._observer    = None
        self._thread      = None
        self._stop_evt    = threading.Event()
        self._loop        = None

        # Dirs
        self.processed_dir = self.watch_path / "processed"
        self.reports_dir   = self.watch_path / "reports"
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # Stats (thread-safe via lock)
        self._stats_lock      = threading.Lock()
        self.files_processed  = 0
        self.files_pending    = 0
        self.last_batch_at    = None
        self.last_error       = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self.status == WatcherStatus.ACTIVE:
            return
        self._stop_evt.clear()
        self.status = WatcherStatus.ACTIVE

        # Bulk scan existing files first
        self._bulk_scan()

        # Start watchdog
        handler        = _IngestHandler(self._pending, self._lock, str(self.processed_dir))
        self._observer = Observer()
        self._observer.schedule(handler, str(self.watch_path), recursive=True)
        self._observer.start()

        # Start drain loop in background thread
        self._thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._thread.start()
        logger.info(f"👁  Watcher started: {self.watch_path}")
        self._emit("status_change", {"status": WatcherStatus.ACTIVE})

    def pause(self):
        self.status = WatcherStatus.PAUSED
        self._stop_evt.set()
        if self._observer:
            self._observer.stop()
        self._emit("status_change", {"status": WatcherStatus.PAUSED})
        logger.info(f"⏸  Watcher paused: {self.watch_path}")

    def stop(self):
        self._stop_evt.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        self.status = WatcherStatus.IDLE
        logger.info(f"🛑 Watcher stopped: {self.watch_path}")

    # ── bulk scan ─────────────────────────────────────────────────────────────

    def _bulk_scan(self):
        count = 0
        for p in self.watch_path.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in Settings.ALLOWED_EXTENSIONS:
                continue
            if "processed" in p.parts or "reports" in p.parts:
                continue
            with self._lock:
                self._pending[str(p)] = time.time() - FILE_STABLE_SECS - 1
            count += 1
        if count:
            logger.info(f"📂 Bulk scan found {count} existing files in {self.watch_path}")

    # ── drain loop ────────────────────────────────────────────────────────────

    def _drain_loop(self):
        while not self._stop_evt.is_set():
            stable = self._drain_stable()
            if stable:
                with self._stats_lock:
                    self.files_pending = len(stable)
                self._emit("batch_start", {"files_pending": len(stable)})
                self._process_batch_list(stable)
            time.sleep(DRAIN_INTERVAL)

    def _drain_stable(self) -> List[dict]:
        now    = time.time()
        stable = []
        with self._lock:
            for path in list(self._pending.keys()):
                p = Path(path)
                if not p.exists():
                    del self._pending[path]
                    continue
                try:
                    stat = p.stat()
                except OSError:
                    continue
                if stat.st_mtime > self._pending[path]:
                    self._pending[path] = stat.st_mtime
                    continue
                if now - self._pending[path] >= FILE_STABLE_SECS:
                    fhash = _file_hash(path)
                    if fhash in self._seen_hashes:
                        del self._pending[path]
                        continue
                    self._seen_hashes.add(fhash)
                    stable.append({"path": path, "size": stat.st_size})
                    del self._pending[path]
        return stable

    # ── batch processing ─────────────────────────────────────────────────────

    def _process_batch_list(self, files: List[dict]):
        # Determine batch size
        if self.batch_size:
            bs = self.batch_size
        else:
            avg_mb = (sum(f["size"] for f in files) / len(files)) / MB if files else 0
            bs = BATCH_SIZE_TINY if avg_mb < 1 else BATCH_SIZE_SMALL if avg_mb < 10 else BATCH_SIZE_LARGE

        batches = [files[i:i + bs] for i in range(0, len(files), bs)]
        logger.info(f"📦 {len(files)} files → {len(batches)} batch(es) of ≤{bs}")

        for batch in batches:
            self._process_one_batch(batch)

    def _process_one_batch(self, batch: List[dict]):
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + _short_id()
        started = datetime.now().isoformat()
        total_mb = sum(f["size"] for f in batch) / MB
        succeeded = 0
        failed = 0
        records = []

        logger.info(f"🚀 Batch {batch_id} — {len(batch)} files, {total_mb:.1f} MB")
        self._emit("batch_progress", {
            "batch_id": batch_id,
            "total": len(batch),
            "done": 0,
            "succeeded": 0,
            "failed": 0,
        })

        # Upload entire batch in one API call
        records = self._upload_batch_api(batch)
        done_count = 0
        for rec in records:
            done_count += 1
            if rec["status"] == "success":
                succeeded += 1
                self._post_process(rec["path"])
            else:
                failed += 1
                logger.error(f"  ✗ {rec['filename']}: {rec.get('error')}")

            self._emit("batch_progress", {
                "batch_id": batch_id,
                "total": len(batch),
                "done": done_count,
                "succeeded": succeeded,
                "failed": failed,
            })

        with self._stats_lock:
            self.files_processed += succeeded
            self.files_pending = max(0, self.files_pending - len(batch))
            self.last_batch_at = datetime.now().isoformat()

        report = {
            "batch_id": batch_id,
            "folder_id": self.folder_id,
            "started_at": started,
            "completed_at": datetime.now().isoformat(),
            "total": len(batch),
            "succeeded": succeeded,
            "failed": failed,
            "total_size_mb": round(total_mb, 2),
            "files": records,
        }
        self._save_report(report)
        self._emit("batch_complete", report)
        logger.info(f"✅ Batch {batch_id} — {succeeded} ok, {failed} failed")

    def _upload_batch_api(self, batch: List[dict]) -> List[dict]:
        """Upload entire batch in one call to /api/upload/batch."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                files = []
                handles = []
                for f in batch:
                    fh = open(f["path"], "rb")
                    handles.append(fh)
                    p = Path(f["path"])
                    files.append(("files", (p.name, fh, _mime(p))))

                resp = requests.post(
                    f"{self.api_url}/api/upload/batch",
                    files=files,
                    timeout=300,
                )
                for fh in handles:
                    fh.close()

                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                records = []
                for i, f in enumerate(batch):
                    p = Path(f["path"])
                    r = results[i] if i < len(results) else {}
                    status = r.get("status", "failed")
                    records.append({
                        "path": f["path"],
                        "filename": p.name,
                        "doc_id": r.get("id") or r.get("document_id"),
                        "size_mb": round(f["size"] / MB, 2),
                        "status": status,
                        "error": r.get("error"),
                        "processed_at": datetime.now().isoformat(),
                    })
                    logger.info(f"  {'✓' if status == 'success' else '✗'} '{p.name}' → {status}")
                return records

            except Exception as exc:
                logger.warning(f"  ⚠ Batch attempt {attempt}/{MAX_RETRIES}: {exc}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)

        return [{"path": f["path"], "filename": Path(f["path"]).name,
                 "doc_id": None, "size_mb": round(f["size"] / MB, 2),
                 "status": "failed", "error": "max retries exceeded"}
                for f in batch]



    def _upload_one(self, path: str, size: int) -> dict:
        p = Path(path)
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with open(path, "rb") as fh:
                    resp = requests.post(
                        f"{self.api_url}/api/documents/upload",
                        files={"file": (p.name, fh, _mime(p))},
                        timeout=120,
                    )
                resp.raise_for_status()
                doc    = resp.json()
                doc_id = doc.get("id") or doc.get("document_id")
                if not doc_id:
                    raise ValueError(f"No doc ID in response: {doc}")

                # Trigger processing
                proc = requests.post(
                    f"{self.api_url}/api/documents/{doc_id}/process",
                    timeout=300,
                )
                if proc.status_code not in (200, 201, 202, 409):
                    proc.raise_for_status()

                logger.info(f"  ✓ '{p.name}' → doc_id={doc_id}")
                return {"path": path, "filename": p.name, "doc_id": doc_id,
                        "size_mb": round(size / MB, 2), "status": "success",
                        "processed_at": datetime.now().isoformat()}

            except Exception as exc:
                logger.warning(f"  ⚠ Attempt {attempt}/{MAX_RETRIES} '{p.name}': {exc}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)

        return {"path": path, "filename": p.name, "doc_id": None,
                "size_mb": round(size / MB, 2), "status": "failed",
                "error": "max retries exceeded"}

    def _post_process(self, path: str):
        src  = Path(path)
        dest = self.processed_dir / src.name
        if dest.exists():
            dest = self.processed_dir / f"{src.stem}_{int(time.time())}{src.suffix}"
        try:
            shutil.copy2(str(src), str(dest))
            src.unlink()
        except Exception as exc:
            logger.warning(f"  ⚠ Post-process failed '{src.name}': {exc}")

    def _save_report(self, report: dict):
        rpath = self.reports_dir / f"batch_{report['batch_id']}.json"
        try:
            with open(rpath, "w") as fh:
                json.dump(report, fh, indent=2)
        except Exception as exc:
            logger.warning(f"  ⚠ Report save failed: {exc}")

    def _emit(self, event_type: str, data: dict):
        if not self.on_event:
            return
        payload = {"type": event_type, "folder_id": self.folder_id,
                   "ts": datetime.now().isoformat(), **data}
        try:
            asyncio.run_coroutine_threadsafe(
                self.on_event(self.folder_id, payload),
                self._get_loop(),
            )
        except Exception:
            pass

    def _get_loop(self):
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def get_status(self) -> dict:
        with self._stats_lock:
            return {
                "folder_id":       self.folder_id,
                "path":            str(self.watch_path),
                "label":           self.config.get("label", self.watch_path.name),
                "status":          self.status,
                "files_processed": self.files_processed,
                "files_pending":   self.files_pending,
                "last_batch_at":   self.last_batch_at,
                "last_error":      self.last_error,
            }


# ── FolderWatcherService (singleton) ─────────────────────────────────────────

class FolderWatcherService:
    """Manages all folder watchers. Persists config to JSON."""

    CONFIG_FILE = Path.home() / "Library" / "Application Support" / "UniDocVerse" / "watch_folders.json"

    def __init__(self, api_url: str = "http://localhost:7979"):
        self.api_url  = api_url
        self._configs: Dict[str, dict]         = {}
        self._watchers: Dict[str, FolderWatcher] = {}
        self._load_config()

    # ── config persistence ────────────────────────────────────────────────────

    def _load_config(self):
        try:
            if self.CONFIG_FILE.exists():
                with open(self.CONFIG_FILE) as fh:
                    self._configs = json.load(fh)
                logger.info(f"📂 Loaded {len(self._configs)} watch folder config(s)")
        except Exception as exc:
            logger.warning(f"Config load failed: {exc}")
            self._configs = {}

    def _save_config(self):
        try:
            self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_FILE, "w") as fh:
                json.dump(self._configs, fh, indent=2)
        except Exception as exc:
            logger.warning(f"Config save failed: {exc}")

    # ── folder management ─────────────────────────────────────────────────────

    def add_folder(self, folder_id: str, path: str, source_type: str = "local",
                   batch_size=None, max_workers: int = 3, label: str = "") -> dict:
        config = {
            "id":           folder_id,
            "path":         path,
            "label":        label or Path(path).name,
            "source_type":  source_type,
            "batch_size":   batch_size,
            "max_workers":  max_workers,
            "status":       WatcherStatus.IDLE,
            "added_at":     datetime.now().isoformat(),
            "files_processed": 0,
            "files_pending":   0,
            "last_batch_at":   None,
            "last_error":      None,
        }
        self._configs[folder_id] = config
        self._save_config()
        return config

    def remove_folder(self, folder_id: str):
        self._configs.pop(folder_id, None)
        self._watchers.pop(folder_id, None)
        self._save_config()

    def list_folders(self) -> List[dict]:
        result = []
        for fid, cfg in self._configs.items():
            w = self._watchers.get(fid)
            if w:
                live = w.get_status()
                cfg.update({
                    "status":          live["status"],
                    "files_processed": live["files_processed"],
                    "files_pending":   live["files_pending"],
                    "last_batch_at":   live["last_batch_at"],
                    "last_error":      live["last_error"],
                })
            result.append(cfg)
        return result

    def get_status(self, folder_id: str) -> Optional[dict]:
        w = self._watchers.get(folder_id)
        if w:
            return w.get_status()
        return self._configs.get(folder_id)

    # ── watcher lifecycle ─────────────────────────────────────────────────────

    async def start_watcher(self, folder_id: str):
        from app.api.watch_folders import broadcast_folder_event

        config = self._configs.get(folder_id)
        if not config:
            raise ValueError(f"Folder {folder_id} not configured")

        if folder_id in self._watchers:
            self._watchers[folder_id].stop()

        watcher = FolderWatcher(
            config   = config,
            api_url  = self.api_url,
            on_event = broadcast_folder_event,
        )
        self._watchers[folder_id] = watcher

        # Run blocking start in thread pool so we don't block FastAPI
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, watcher.start)

    def pause_watcher(self, folder_id: str):
        w = self._watchers.get(folder_id)
        if w:
            w.pause()

    def stop_watcher(self, folder_id: str):
        w = self._watchers.get(folder_id)
        if w:
            w.stop()

    def get_reports(self, folder_id: str, limit: int = 20) -> List[dict]:
        config = self._configs.get(folder_id)
        if not config:
            return []
        reports_dir = Path(config["path"]) / "reports"
        if not reports_dir.exists():
            return []
        reports = []
        for f in sorted(reports_dir.glob("batch_*.json"), reverse=True)[:limit]:
            try:
                with open(f) as fh:
                    reports.append(json.load(fh))
            except Exception:
                continue
        return reports


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_hash(path: str) -> str:
    import hashlib
    h = hashlib.md5()
    try:
        with open(path, "rb") as fh:
            h.update(fh.read(65536))
    except OSError:
        return path
    return h.hexdigest()


def _short_id() -> str:
    import hashlib, random
    return hashlib.md5(str(random.random()).encode()).hexdigest()[:6]


def _mime(path: Path) -> str:
    return {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".csv":  "text/csv",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls":  "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc":  "application/msword",
        ".txt":  "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")