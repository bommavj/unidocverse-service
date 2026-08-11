import os
import sys
import asyncio

# Add the project directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.models.client import Client
from app.models.policy import Policy
from app.models.document import Document
from app.core.database import SessionLocal
from app.services.processing_service import ProcessingService
from sqlalchemy.orm import Session

async def reprocess_doc(db: Session, doc_id: str):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        print(f"❌ Document {doc_id} not found")
        return
        
    print(f"\n🔄 Reprocessing: {doc.filename} ({doc.doc_type})")
    
    # Reset status
    doc.status = "pending"
    db.commit()
    
    # Process synchronously
    try:
        await ProcessingService._process_async(doc.id, doc.file_path, doc.filename)
    except Exception as e:
        print(f"  ❌ Reprocessing failed: {e}")
        return
        
    # Fetch refreshed doc
    db.refresh(doc)
    meta = doc.doc_metadata or {}
    analyzed_data = meta.get("analyzed_data") or {}
    
    print(f"  New Status: {doc.status}")
    print(f"  New Doc Type: {doc.doc_type}")
    print(f"  Alerts ({len(analyzed_data.get('alerts', []))} items):")
    for idx, alert in enumerate(analyzed_data.get("alerts", []), start=1):
        print(f"    {idx}. [{alert.get('type')}] {alert.get('title')}: {alert.get('message')}")
        
    print(f"  Action Plan ({len(analyzed_data.get('action_plan', []))} items):")
    for idx, action in enumerate(analyzed_data.get("action_plan", []), start=1):
        print(f"    {idx}. [{action.get('priority')}] {action.get('action')}: {action.get('detail')} (Monthly Impact: {action.get('monthly_impact')})")
        
    ui_config = analyzed_data.get("ui_config") or {}
    has_action_table = any(t.get("id") in ("action_plan", "actions") for t in ui_config.get("tables", []))
    has_action_section = any(s.get("id") in ("action_plan", "actions") for s in ui_config.get("sections", []))
    has_alerts_config = "alerts_config" in ui_config
    
    print(f"  UI Config - Action Table: {has_action_table} | Action Section: {has_action_section} | Alerts Config: {has_alerts_config}")
    print("-" * 120)

async def main():
    db = SessionLocal()
    try:
        # Target IDs based on the check_db_docs output
        target_docs = [
            "b123ff9c-b867-4b27-b72a-9a59b428c358", # rental-agreement-telangana_71195_27 (lease_agreement)
            "8026afa5-00e2-43d3-acca-6673c26a05ed", # Vijay Bomma_Resume.docx (resume)
            "5546ff5e-6f53-43bc-8df8-0b148de99714", # 2025 W2.pdf (tax_form)
            "dfebd38b-fed4-48f2-ab0b-c3ce4e2ec360", # energy-stmt.pdf (utility_bill)
            "42e4a641-9636-41e8-bbac-eb05a06d269e", # Norris Crossing Inventory.pdf (invoice)
        ]
        
        for doc_id in target_docs:
            await reprocess_doc(db, doc_id)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
