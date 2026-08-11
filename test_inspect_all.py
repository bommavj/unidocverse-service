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
        documents = db.query(Document).order_by(Document.filename).all()
        print(f"REPORT: Status of all {len(documents)} documents in the database")
        print("=" * 140)
        
        # Table columns: Filename | Status | Doc Type | Text Len | Sum Len | Insights | Keywords | Has Analytics | Warning/Hallucination Flag
        header = f"{'Filename':<45} | {'Status':<10} | {'Doc Type':<15} | {'Text':<6} | {'Sum':<6} | {'Ins':<3} | {'Kws':<3} | {'Analytics':<10} | {'Issues/Warnings'}"
        print(header)
        print("-" * 140)
        
        for doc in documents:
            filename = doc.filename
            if len(filename) > 42:
                filename = filename[:39] + "..."
            
            raw_len = len(doc.raw_text) if doc.raw_text else 0
            sum_len = len(doc.summary) if doc.summary else 0
            
            meta = doc.doc_metadata or {}
            insights_count = len(meta.get("insights", []))
            keywords_count = len(meta.get("keywords", []))
            
            # Check if specialized analytics were generated
            has_analytics = "No"
            if meta.get("analytics") or (meta.get("analyzed_data") and meta.get("analyzed_data").get("type") == "dataset"):
                has_analytics = "Dataset"
            elif doc.doc_type in ["invoice", "bank_statement", "credit_card_statement", "receipt", "utility_bill"]:
                # Check if specific analyzed_data is not empty
                analyzed_data = meta.get("analyzed_data", {}) or {}
                # If it has extracted keys other than generic type/summary
                extra_keys = [k for k in analyzed_data.keys() if k not in ["type", "summary", "alerts", "insights", "has_advanced_analytics"]]
                if extra_keys:
                    has_analytics = f"Yes ({doc.doc_type})"
            
            # Identify issues
            flags = []
            if raw_len == 0 and doc.status == "completed" and doc.doc_type != "image":
                flags.append("Empty Text")
            if "greenfuture" in str(doc.summary).lower() or "jane goodall" in str(doc.summary).lower():
                flags.append("HALLUCINATION (mock)")
            if doc.status == "failed":
                flags.append("Failed Status")
            if doc.doc_type == "purchase_order" and "Online-Store-Orders" in doc.filename:
                flags.append("Misclassified (PO)")
            if doc.doc_type == "document" and "Tax" in doc.filename:
                flags.append("Tax Misclassified")
                
            flags_str = ", ".join(flags) if flags else "OK"
            
            print(f"{filename:<45} | {doc.status:<10} | {str(doc.doc_type):<15} | {raw_len:<6} | {sum_len:<6} | {insights_count:<3} | {keywords_count:<3} | {has_analytics:<10} | {flags_str}")
            
        print("=" * 140)
        
        # Print detailed section for flagged documents
        print("\nDETAILED REVIEW OF FLAGGED DOCUMENTS:")
        print("=" * 140)
        flagged_count = 0
        for doc in documents:
            raw_len = len(doc.raw_text) if doc.raw_text else 0
            meta = doc.doc_metadata or {}
            is_hallucinated = "greenfuture" in str(doc.summary).lower() or "jane goodall" in str(doc.summary).lower()
            is_misclassified = (doc.doc_type == "purchase_order" and "Online-Store-Orders" in doc.filename)
            is_empty = (raw_len == 0 and doc.status == "completed" and doc.doc_type != "image")
            
            if is_hallucinated or is_misclassified or is_empty or doc.status == "failed":
                flagged_count += 1
                print(f"Flagged Document #{flagged_count}")
                print(f"  Filename: {doc.filename}")
                print(f"  ID: {doc.id}")
                print(f"  Doc Type: {doc.doc_type} (status: {doc.status})")
                print(f"  Extracted Text Length: {raw_len} characters")
                print(f"  Parser Used: {meta.get('parser_class', 'Unknown')}")
                print(f"  Analyzer Used: {meta.get('analyzer_used', 'Unknown')}")
                print(f"  Warnings/Quality Issues: {meta.get('quality_issues', [])}")
                print(f"  Errors List: {meta.get('errors', [])}")
                print(f"  Summary: {doc.summary}")
                print(f"  Keywords: {meta.get('keywords', [])}")
                print(f"  Analytics Keys: {list(meta.get('analyzed_data', {}).keys()) if meta.get('analyzed_data') else 'None'}")
                print("-" * 140)
                
    finally:
        db.close()

if __name__ == "__main__":
    main()
