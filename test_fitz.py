import os
import sys
import fitz

def main():
    path = "/Users/bommavj/Library/Application Support/UniDocVerse/uploads/053856f1-a891-44dc-acbf-7a774202d6d4_ShoonyaTax_1807061_2024_Final Tax Document.pdf"
    print(f"Opening: {path}")
    try:
        doc = fitz.open(path)
        print(f"Document: {doc}")
        print(f"Is Encrypted: {doc.is_encrypted}")
        print(f"Metadata: {doc.metadata}")
        
        # Try to authenticate
        if doc.is_encrypted:
            print("Document is encrypted. Trying empty password...")
            rc = doc.authenticate("")
            print(f"Authentication success: {rc}")
            
        print(f"Page Count: {len(doc)}")
        for i in range(len(doc)):
            try:
                page = doc.load_page(i)
                text = page.get_text("text")
                print(f"Page {i+1} Text Len: {len(text)}")
                print(f"Page {i+1} text sample: {repr(text[:100])}")
            except Exception as pe:
                print(f"Page {i+1} load failed: {pe}")
    except Exception as e:
        print(f"Open failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
