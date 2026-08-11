import os
import sys
import logging

# Set up logging to stdout
logging.basicConfig(level=logging.INFO)

# Add the project directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.services.parsers.docling_parser import DoclingParser

def main():
    path = "/Users/bommavj/Library/Application Support/UniDocVerse/uploads/8026afa5-00e2-43d3-acca-6673c26a05ed_Vijay Bomma_Resume.docx"
    parser = DoclingParser()
    print("Parsing with DoclingParser...")
    try:
        res = parser.parse(path, "Vijay Bomma_Resume.docx")
        print("Result:")
        print(f"Keys: {res.keys()}")
        print(f"Text Len: {len(res.get('text', ''))}")
        print(f"Text: {repr(res.get('text', '')[:200])}")
        print(f"Tables: {res.get('tables')}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
