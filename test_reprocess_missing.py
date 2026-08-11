"""
Reprocess remaining documents that are still missing alerts and action_plan.
Skips documents that already have alerts+actions from previous runs.
Adds a timeout to prevent getting stuck on problematic files.
"""
import os
import sys
import asyncio
import signal

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.models.client import Client
from app.models.policy import Policy
from app.models.document import Document
from app.core.database import SessionLocal
from app.services.processing_service import ProcessingService
from sqlalchemy.orm import Session

# Skip image/data_table types that tend to hang or are non-textual
SKIP_TYPES = {"data_table"}  # data_tables don't need alerts


async def reprocess_doc_with_timeout(db: Session, doc_id: str, index: int, total: int, timeout_secs=180):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        print(f"❌ [{index}/{total}] Document {doc_id} not found")
        return False

    if doc.doc_type in SKIP_TYPES:
        print(f"⏭️  [{index}/{total}] Skipping {doc.filename} (type: {doc.doc_type})")
        return True

    print(f"\n🔄 [{index}/{total}] Reprocessing: {doc.filename} ({doc.doc_type})")

    # Reset status
    doc.status = "pending"
    db.commit()

    try:
        await asyncio.wait_for(
            ProcessingService._process_async(doc.id, doc.file_path, doc.filename),
            timeout=timeout_secs
        )
    except asyncio.TimeoutError:
        print(f"  ⏰ TIMEOUT after {timeout_secs}s - skipping this document")
        # Mark it back as completed so it doesn't stay in pending
        db.refresh(doc)
        if doc.status == "pending" or doc.status == "processing":
            doc.status = "completed"
            db.commit()
        return False
    except Exception as e:
        print(f"  ❌ Reprocessing failed: {e}")
        return False

    # Fetch refreshed doc
    db.refresh(doc)
    meta = doc.doc_metadata or {}
    analyzed_data = meta.get("analyzed_data") or {}

    alerts = analyzed_data.get("alerts", [])
    action_plan = analyzed_data.get("action_plan", [])

    status_icon = "✅" if alerts and action_plan else "⚠️"
    print(f"  {status_icon} Status: {doc.status} | Alerts: {len(alerts)} | Actions: {len(action_plan)}")
    return True


async def main():
    db = SessionLocal()
    try:
        # Find all documents still missing alerts AND action_plan
        docs = db.query(Document).filter(Document.status == "completed").all()
        needs_reprocess = []
        for doc in docs:
            meta = doc.doc_metadata or {}
            analyzed_data = meta.get("analyzed_data") or {}
            alerts = analyzed_data.get("alerts", [])
            action_plan = analyzed_data.get("action_plan", [])
            if not alerts and not action_plan:
                needs_reprocess.append(str(doc.id))

        total = len(needs_reprocess)
        print(f"\n📋 Found {total} documents still needing reprocessing\n")

        if total == 0:
            print("✅ All documents already have alerts and action plans!")
            return

        success = 0
        skipped = 0
        failed = 0
        for i, doc_id in enumerate(needs_reprocess, 1):
            result = await reprocess_doc_with_timeout(db, doc_id, i, total, timeout_secs=180)
            if result:
                success += 1
            else:
                failed += 1

        print(f"\n{'='*80}")
        print(f"  REPROCESSING COMPLETE")
        print(f"  Success: {success}/{total} | Failed/Timeout: {failed}/{total}")
        print(f"{'='*80}\n")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
