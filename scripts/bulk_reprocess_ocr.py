#!/usr/bin/env python3
"""
scripts/bulk_reprocess_ocr.py

Bulk-reprocess all existing documents through the updated OCR pipeline
by calling the live API endpoints.

Usage:
  python scripts/bulk_reprocess_ocr.py [--dry-run] [--concurrency 3] [--doc-id <id>]
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import httpx

BASE_URL     = "http://localhost:8000"
USERNAME     = "admin"
PASSWORD     = "admin"
POLL_SLEEP   = 10   # seconds between status polls
POLL_TIMEOUT = 7200 # 2 hours max

# These extensions have no image content to OCR — skip them
_SKIP_EXTS = {".csv", ".tsv", ".txt", ".json", ".xml", ".yaml", ".yml", ".html", ".htm"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bulk_reprocess")


# ── auth ─────────────────────────────────────────────────────────────────────
def get_token() -> str:
    resp = httpx.post(f"{BASE_URL}/api/auth/login",
                      json={"username": USERNAME, "password": PASSWORD}, timeout=10)
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"Login failed: {resp.json()}")
    return token


# ── list documents (paginated) ────────────────────────────────────────────────
def list_all_docs(token: str, prefix: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    docs   = []
    cursor = None
    page   = 0
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = httpx.get(f"{BASE_URL}{prefix}", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("documents", data.get("items", data if isinstance(data, list) else []))
        docs.extend(batch)
        cursor = data.get("next_cursor")
        page  += 1
        log.info(f"  Page {page}: {len(batch)} docs (total so far: {len(docs)})")
        if not cursor or not batch:
            break
    return docs


def _doc_prefix(token: str) -> str:
    """Return the correct URL prefix for the documents router."""
    headers = {"Authorization": f"Bearer {token}"}
    for prefix in ("/documents", "/api/documents"):
        try:
            r = httpx.get(f"{BASE_URL}{prefix}?limit=1", headers=headers, timeout=10)
            if r.status_code < 400:
                return prefix
        except Exception:
            pass
    return "/documents"


# ── reprocess one ─────────────────────────────────────────────────────────────
async def reprocess_one(doc_id: str, filename: str, prefix: str, token: str, sem: asyncio.Semaphore):
    async with sem:
        headers = {"Authorization": f"Bearer {token}"}
        log.info(f"  ▶ {filename}")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE_URL}{prefix}/{doc_id}/reprocess",
                headers=headers,
            )
        if resp.status_code == 200:
            log.info(f"  ✅ Queued: {filename}")
        else:
            log.warning(f"  ⚠️  {filename} → {resp.status_code}: {resp.text[:120]}")


# ── poll until all done ───────────────────────────────────────────────────────
def poll_until_done(doc_ids: list[str], prefix: str, token: str):
    headers = {"Authorization": f"Bearer {token}"}
    remaining = set(doc_ids)
    deadline  = time.time() + POLL_TIMEOUT
    total     = len(remaining)
    done      = 0

    while remaining and time.time() < deadline:
        done_now = set()
        for doc_id in list(remaining):
            try:
                r = httpx.get(f"{BASE_URL}{prefix}/{doc_id}", headers=headers, timeout=10)
                if r.status_code == 200:
                    status   = r.json().get("status", "")
                    filename = r.json().get("filename", doc_id)
                    if status == "completed":
                        log.info(f"  ✅ Done: {filename}")
                        done_now.add(doc_id)
                        done += 1
                    elif status == "failed":
                        log.warning(f"  ❌ Failed: {filename}")
                        done_now.add(doc_id)
                        done += 1
            except Exception as e:
                log.debug(f"Poll error for {doc_id}: {e}")

        remaining -= done_now
        if remaining:
            log.info(f"  ⏳ Progress: {done}/{total} done, {len(remaining)} still processing …")
            time.sleep(POLL_SLEEP)

    if remaining:
        log.warning(f"  ⚠️  Timed out — {len(remaining)} documents still processing")
    else:
        log.info(f"  🎉 All {total} documents finished reprocessing")


# ── main ──────────────────────────────────────────────────────────────────────
async def main():
    ap = argparse.ArgumentParser(description="Bulk OCR reprocess via API")
    ap.add_argument("--dry-run",     action="store_true")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--doc-id",      default=None, help="Reprocess single document ID")
    args = ap.parse_args()

    log.info("Logging in …")
    token  = get_token()
    prefix = _doc_prefix(token)
    log.info(f"Using documents prefix: {prefix}")

    if args.doc_id:
        docs = [{"id": args.doc_id, "filename": args.doc_id, "status": "?"}]
    else:
        log.info("Fetching document list …")
        docs = list_all_docs(token, prefix)

    log.info(f"Total documents: {len(docs)}")

    to_process = []
    for doc in docs:
        filename = doc.get("filename", "")
        ext = Path(filename).suffix.lower()
        if ext in _SKIP_EXTS:
            log.debug(f"  SKIP (no OCR): {filename}")
            continue
        to_process.append(doc)

    skipped = len(docs) - len(to_process)
    log.info(f"Skipping {skipped} non-OCR files (CSV/TXT/JSON/…)")
    log.info(f"Will reprocess: {len(to_process)} documents")

    if args.dry_run:
        for d in to_process:
            log.info(f"  [DRY-RUN] {d.get('filename')}  [{d.get('status')}]")
        log.info("[DRY-RUN] — no changes made")
        return

    # Kick off reprocessing
    log.info(f"\nSubmitting reprocess requests (concurrency={args.concurrency}) …")
    sem   = asyncio.Semaphore(args.concurrency)
    tasks = [
        reprocess_one(doc["id"], doc.get("filename", doc["id"]), prefix, token, sem)
        for doc in to_process
    ]
    await asyncio.gather(*tasks)

    # Poll for completion
    log.info("\nWaiting for all documents to complete …")
    doc_ids = [doc["id"] for doc in to_process]
    poll_until_done(doc_ids, prefix, token)


if __name__ == "__main__":
    asyncio.run(main())
