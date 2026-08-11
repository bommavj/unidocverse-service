import os
import sys

# Add the project directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.models.document import Document
from app.core.database import SessionLocal

def main():
    db = SessionLocal()
    try:
        docs = db.query(Document).all()
        print(f"Total documents in database: {len(docs)}")
        print(f"{'ID':<38} | {'Filename':<35} | {'Doc Type':<20} | {'Status':<10}")
        print("-" * 115)
        for doc in docs:
            meta = doc.doc_metadata or {}
            analyzed_data = meta.get("analyzed_data") or {}
            has_alerts = "alerts" in analyzed_data
            has_actions = "action_plan" in analyzed_data
            alert_count = len(analyzed_data.get("alerts", [])) if has_alerts else 0
            action_count = len(analyzed_data.get("action_plan", [])) if has_actions else 0
            
            print(f"{str(doc.id):<38} | {doc.filename[:35]:<35} | {doc.doc_type or 'None':<20} | {doc.status:<10} | Alerts: {alert_count} | Actions: {action_count}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
