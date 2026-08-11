import os
import sys

# Add the project directory to sys.path so we can import app modules
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.document import Document

def main():
    db: Session = SessionLocal()
    try:
        documents = db.query(Document).order_by(Document.filename).all()
        print(f"Total documents: {len(documents)}")
        print("=" * 120)
        for doc in documents:
            raw_text = doc.raw_text
            raw_len = len(raw_text) if raw_text is not None else 0
            
            # Print if raw_len is 0 or low, or status is completed but text is empty
            if raw_len < 100 or doc.filename == "Vijay Bomma_Resume.docx" or "ShoonyaTax" in doc.filename or "Online-Store" in doc.filename:
                print(f"ID: {doc.id}")
                print(f"Filename: {doc.filename}")
                print(f"Mime: {doc.mime_type}")
                print(f"Status: {doc.status}")
                print(f"Raw Text Len: {raw_len}")
                print(f"Raw Text Sample: {repr(raw_text[:200]) if raw_text else 'None'}")
                print(f"Doc Type: {doc.doc_type}")
                print(f"Summary Sample: {repr(doc.summary[:200]) if doc.summary else 'None'}")
                print(f"Metadata Keys: {list(doc.doc_metadata.keys()) if doc.doc_metadata else 'None'}")
                if doc.doc_metadata:
                    print(f"  - parser_class: {doc.doc_metadata.get('parser_class')}")
                    print(f"  - analyzer_used: {doc.doc_metadata.get('analyzer_used')}")
                    print(f"  - errors: {doc.doc_metadata.get('errors')}")
                    print(f"  - quality_issues: {doc.doc_metadata.get('quality_issues')}")
                    print(f"  - classification: {doc.doc_metadata.get('classification')}")
                    # Print sheet/table info if present
                    if 'tables' in doc.doc_metadata and doc.doc_metadata['tables']:
                        print(f"  - Tables count: {len(doc.doc_metadata['tables'])}")
                print("-" * 80)
    finally:
        db.close()

if __name__ == "__main__":
    main()
