import os
import sys

# Add the project directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.document import Document

def main():
    db: Session = SessionLocal()
    try:
        docs = db.query(Document).filter(
            (Document.filename.like('%Resume%')) | 
            (Document.filename.like('%ShoonyaTax%'))
        ).all()
        
        for doc in docs:
            print(f"ID: {doc.id}")
            print(f"Filename: {doc.filename}")
            print(f"File Path: {doc.file_path}")
            exists = os.path.exists(doc.file_path)
            print(f"File exists on disk: {exists}")
            if exists:
                print(f"File size on disk: {os.path.getsize(doc.file_path)} bytes")
            print("-" * 50)
            
    finally:
        db.close()

if __name__ == "__main__":
    main()
