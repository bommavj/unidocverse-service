import os
import sys

# Add the project directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.services.parsers.universal_parser import UniversalParser

def test_docx():
    path = "/Users/bommavj/Library/Application Support/UniDocVerse/uploads/8026afa5-00e2-43d3-acca-6673c26a05ed_Vijay Bomma_Resume.docx"
    print("Testing DOCX parsing:")
    parser = UniversalParser()
    try:
        res = parser.parse(path, "Vijay Bomma_Resume.docx")
        print(f"Keys: {res.keys()}")
        print(f"Parser Used: {res.get('parser_used')}")
        print(f"Text Len: {len(res.get('text', ''))}")
        print(f"Warnings: {res.get('warnings')}")
        print(f"Error: {res.get('error')}")
        print(f"Text Content: {repr(res.get('text', '')[:200])}")
    except Exception as e:
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()

def test_pdf():
    path = "/Users/bommavj/Library/Application Support/UniDocVerse/uploads/053856f1-a891-44dc-acbf-7a774202d6d4_ShoonyaTax_1807061_2024_Final Tax Document.pdf"
    print("\nTesting PDF parsing:")
    parser = UniversalParser()
    try:
        res = parser.parse(path, "ShoonyaTax_1807061_2024_Final Tax Document.pdf")
        print(f"Keys: {res.keys()}")
        print(f"Parser Used: {res.get('parser_used')}")
        print(f"Text Len: {len(res.get('text', ''))}")
        print(f"Warnings: {res.get('warnings')}")
        print(f"Error: {res.get('error')}")
        print(f"Text Content: {repr(res.get('text', '')[:200])}")
    except Exception as e:
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_docx()
    test_pdf()
