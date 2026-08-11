import pdfplumber
from pathlib import Path
from typing import Dict, List
import PyPDF2


class PDFExtractor:
    """Extract text and metadata from PDF files"""

    @staticmethod
    def extract_text(file_path: Path) -> str:
        """Extract all text from PDF"""
        try:
            text_content = []

            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text()

                    if page_text:
                        text_content.append(f"\n--- Page {page_num} ---\n")
                        text_content.append(page_text)

            full_text = "\n".join(text_content)
            return full_text.strip() if full_text.strip() else ""

        except Exception as e:
            print(f"❌ PDF extraction error: {e}")
            return PDFExtractor._fallback_extract(file_path)

    @staticmethod
    def _fallback_extract(file_path: Path) -> str:
        """Fallback extraction using PyPDF2"""
        try:
            text_content = []

            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)

                for page_num, page in enumerate(pdf_reader.pages, 1):
                    text = page.extract_text()
                    if text:
                        text_content.append(f"\n--- Page {page_num} ---\n")
                        text_content.append(text)

            return "\n".join(text_content).strip()

        except Exception as e:
            print(f"❌ Fallback extraction failed: {e}")
            return ""

    @staticmethod
    def _safe_str(value) -> str:
        """Safely convert any value to string for JSON serialization"""
        if value is None:
            return ""
        try:
            # Handle PyPDF2 IndirectObject
            if hasattr(value, 'get_object'):
                value = value.get_object()
            return str(value)
        except:
            return ""

    @staticmethod
    def get_metadata(file_path: Path) -> Dict:
        """
        Extract PDF metadata (JSON-safe)

        Returns:
            Dictionary with metadata (all values converted to strings)
        """
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)

                # Safely extract metadata
                metadata = {
                    "page_count": len(pdf_reader.pages),
                }

                # Extract metadata if available
                if pdf_reader.metadata:
                    # Convert all metadata values to strings (avoid IndirectObject)
                    metadata["title"] = PDFExtractor._safe_str(pdf_reader.metadata.get('/Title'))
                    metadata["author"] = PDFExtractor._safe_str(pdf_reader.metadata.get('/Author'))
                    metadata["subject"] = PDFExtractor._safe_str(pdf_reader.metadata.get('/Subject'))
                    metadata["creator"] = PDFExtractor._safe_str(pdf_reader.metadata.get('/Creator'))
                    metadata["producer"] = PDFExtractor._safe_str(pdf_reader.metadata.get('/Producer'))

                # Remove empty values
                metadata = {k: v for k, v in metadata.items() if v}

                return metadata

        except Exception as e:
            print(f"❌ Metadata extraction error: {e}")
            return {"page_count": 0, "error": str(e)}

    @staticmethod
    def extract_tables(file_path: Path) -> List[List[List]]:
        """
        Extract tables from PDF

        Returns:
            List of tables (each table is list of rows)
        """
        try:
            tables = []

            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(page_tables)

            return tables

        except Exception as e:
            print(f"❌ Table extraction error: {e}")
            return []
