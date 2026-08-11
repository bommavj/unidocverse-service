import os
import sys
import asyncio

# Add the project directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Import all models to register them with SQLAlchemy metadata
from app.models.client import Client
from app.models.policy import Policy
from app.models.document import Document
from app.core.database import SessionLocal
from app.services.processing_service import ProcessingService
from sqlalchemy.orm import Session

async def reprocess_document(db: Session, doc_id: str):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        print(f"❌ Document {doc_id} not found")
        return
    
    print(f"\n🔄 Reprocessing: {doc.filename}")
    print(f"  Old Doc Type: {doc.doc_type}")
    print(f"  Old Summary: {doc.summary[:100]}...")
    
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
    print(f"  New Status: {doc.status}")
    print(f"  New Doc Type: {doc.doc_type}")
    print(f"  New Text Len: {len(doc.raw_text) if doc.raw_text else 0} characters")
    print(f"  New Summary: {doc.summary}")
    print(f"  New Insights: {doc.doc_metadata.get('insights', [])}")
    print(f"  New Keywords count: {len(doc.doc_metadata.get('keywords', [])) if doc.doc_metadata else 0}")
    print("-" * 120)

async def main():
    db: Session = SessionLocal()
    try:
        # Reprocess the three flagged files
        flagged_filenames = [
            "Online-Store-Orders (1).xlsx",
            "ShoonyaTax_1807061_2024_Final Tax Document.pdf",
            "Vijay Bomma_Resume.docx"
        ]
        
        docs = db.query(Document).filter(Document.filename.in_(flagged_filenames)).all()
        print(f"Found {len(docs)} documents to reprocess.")
        
        for doc in docs:
            await reprocess_document(db, doc.id)
            
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
