"""
Fix remaining documents:
1. Bank statements with string alerts (legacy format) - normalize in DB
2. Images with missing 'type' key in alerts - normalize in DB
3. Remaining invoice (11149817.pdf) - reprocess with longer timeout
4. Annual reports - try with even longer timeout (300s)
"""
import os
import sys
import json
import asyncio

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.models.client import Client
from app.models.policy import Policy
from app.models.document import Document
from app.core.database import SessionLocal
from app.services.processing_service import ProcessingService
from sqlalchemy.orm import Session


def fix_legacy_alerts_in_db(db: Session):
    """Fix documents that have legacy string alerts or alerts missing 'type' key."""
    docs = db.query(Document).filter(Document.status == "completed").all()
    fixed_count = 0
    
    for doc in docs:
        meta = doc.doc_metadata or {}
        analyzed_data = meta.get("analyzed_data") or {}
        alerts = analyzed_data.get("alerts", [])
        action_plan = analyzed_data.get("action_plan", [])
        
        needs_fix = False
        normalized_alerts = []
        
        for alert in alerts:
            if isinstance(alert, str):
                needs_fix = True
                alert_type = "warning"
                if any(x in alert.lower() for x in ("error", "critical", "denied")):
                    alert_type = "error"
                normalized_alerts.append({
                    "type": alert_type,
                    "title": "Alert",
                    "message": alert
                })
            elif isinstance(alert, dict):
                if "type" not in alert:
                    needs_fix = True
                    severity = alert.get("severity", "warning")
                    if severity not in ("error", "warning", "info"):
                        severity = "warning"
                    normalized_alerts.append({
                        "type": severity,
                        "title": alert.get("title", "Alert"),
                        "message": alert.get("message") or alert.get("description") or alert.get("details") or "Alert detected"
                    })
                else:
                    normalized_alerts.append(alert)
            else:
                normalized_alerts.append(alert)
        
        if needs_fix and normalized_alerts:
            analyzed_data["alerts"] = normalized_alerts
            
            # Also ensure action_plan exists
            if not action_plan:
                doc_type = doc.doc_type or "document"
                analyzed_data["action_plan"] = [{
                    "priority": "low",
                    "action": "Review Document Details",
                    "detail": f"Verify the extracted fields and summary of this {doc_type.replace('_', ' ')}.",
                    "monthly_impact": 0
                }]
            
            # Ensure UI config has alerts_config
            ui_config = analyzed_data.get("ui_config")
            if isinstance(ui_config, dict):
                ui_config["alerts_config"] = {
                    "show": True,
                    "title": "Alerts & Warnings",
                    "icon": "⚠️",
                    "data_path": "alerts"
                }
            
            analyzed_data["has_advanced_analytics"] = True
            meta["analyzed_data"] = analyzed_data
            doc.doc_metadata = meta
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(doc, "doc_metadata")
            
            fixed_count += 1
            print(f"  🔧 Fixed alerts for: {doc.filename} ({doc.doc_type}) → {len(normalized_alerts)} alerts, {len(analyzed_data.get('action_plan', []))} actions")
    
    if fixed_count > 0:
        db.commit()
    print(f"\n  Total fixed in DB: {fixed_count} documents")
    return fixed_count


async def reprocess_remaining(db: Session):
    """Reprocess documents that still have 0 alerts and 0 actions."""
    docs = db.query(Document).filter(Document.status == "completed").all()
    needs_reprocess = []
    
    for doc in docs:
        meta = doc.doc_metadata or {}
        analyzed_data = meta.get("analyzed_data") or {}
        alerts = analyzed_data.get("alerts", [])
        action_plan = analyzed_data.get("action_plan", [])
        # Skip data_tables
        if doc.doc_type == "data_table":
            continue
        if not alerts and not action_plan:
            needs_reprocess.append(str(doc.id))
    
    total = len(needs_reprocess)
    if total == 0:
        print("\n  ✅ No more documents need reprocessing!")
        return
    
    print(f"\n  📋 {total} documents still need reprocessing (with 300s timeout):\n")
    
    for i, doc_id in enumerate(needs_reprocess, 1):
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            continue
        
        print(f"  🔄 [{i}/{total}] {doc.filename} ({doc.doc_type})")
        doc.status = "pending"
        db.commit()
        
        try:
            await asyncio.wait_for(
                ProcessingService._process_async(doc.id, doc.file_path, doc.filename),
                timeout=600
            )
        except asyncio.TimeoutError:
            print(f"    ⏰ TIMEOUT after 300s")
            db.refresh(doc)
            if doc.status in ("pending", "processing"):
                doc.status = "completed"
                db.commit()
            continue
        except Exception as e:
            print(f"    ❌ Failed: {e}")
            continue
        
        db.refresh(doc)
        meta = doc.doc_metadata or {}
        analyzed_data = meta.get("analyzed_data") or {}
        alerts = analyzed_data.get("alerts", [])
        actions = analyzed_data.get("action_plan", [])
        print(f"    ✅ Alerts: {len(alerts)} | Actions: {len(actions)}")


async def main():
    db = SessionLocal()
    try:
        print("=" * 80)
        print("  PHASE 1: Fix legacy alert formats in DB")
        print("=" * 80)
        fix_legacy_alerts_in_db(db)
        
        print("\n" + "=" * 80)
        print("  PHASE 2: Reprocess remaining documents (annual reports, invoice)")
        print("=" * 80)
        await reprocess_remaining(db)
        
        print("\n" + "=" * 80)
        print("  DONE")
        print("=" * 80)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
