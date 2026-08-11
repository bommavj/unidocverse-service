"""
Universal file classification and capability detection
Determines what features should be available for each file type
"""

import logging
from typing import Dict, List, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class FileCapabilities:
    """Defines what features are available for a file"""

    def __init__(
            self,
            has_tables: bool = False,
            has_text: bool = False,
            has_images: bool = False,
            supports_ocr: bool = False,
            supports_visualization: bool = False,
            supports_qa: bool = False,
            supports_extraction: bool = False,
            primary_content_type: str = "unknown"
    ):
        self.has_tables = has_tables
        self.has_text = has_text
        self.has_images = has_images
        self.supports_ocr = supports_ocr
        self.supports_visualization = supports_visualization
        self.supports_qa = supports_qa
        self.supports_extraction = supports_extraction
        self.primary_content_type = primary_content_type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_tables": self.has_tables,
            "has_text": self.has_text,
            "has_images": self.has_images,
            "supports_ocr": self.supports_ocr,
            "supports_visualization": self.supports_visualization,
            "supports_qa": self.supports_qa,
            "supports_extraction": self.supports_extraction,
            "primary_content_type": self.primary_content_type
        }


def detect_file_capabilities(
        filename: str,
        doc_type: str,
        metadata: Dict[str, Any],
        raw_text: str = ""
) -> FileCapabilities:
    """
    Intelligently detect what features should be available for this file

    Args:
        filename: Original filename
        doc_type: Classified document type
        metadata: Document metadata (includes tables, images, etc.)
        raw_text: Extracted text content

    Returns:
        FileCapabilities object
    """

    ext = Path(filename).suffix.lower()

    # Check what content we actually have
    has_tables = bool(metadata.get("tables"))
    has_text = bool(raw_text and len(raw_text) > 100)
    has_images = bool(metadata.get("images") or metadata.get("has_images"))

    # Spreadsheet files
    if ext in ['.xlsx', '.xls', '.csv', '.xlsm', '.ods']:
        return FileCapabilities(
            has_tables=has_tables,
            has_text=has_text,
            has_images=False,
            supports_ocr=False,
            supports_visualization=has_tables,  # Only if tables exist
            supports_qa=has_text,
            supports_extraction=True,
            primary_content_type="spreadsheet"
        )

    # Document files (rich text, good for Q&A)
    if ext in ['.pdf', '.docx', '.doc', '.txt', '.md', '.rtf', '.odt']:
        # For OCR-processed PDFs, text may be stored but not passed here
        # Trust has_text OR that doc_type was successfully classified (not unknown)
        qa_capable = has_text or (ext == '.pdf' and doc_type not in ('unknown', 'document', ''))
        return FileCapabilities(
            has_tables=has_tables,
            has_text=has_text,
            has_images=has_images,
            supports_ocr=ext == '.pdf' and not has_text,
            supports_visualization=has_tables,
            supports_qa=True,
            supports_extraction=True,
            primary_content_type="document"
        )

    # Presentation files
    if ext in ['.pptx', '.ppt', '.odp']:
        return FileCapabilities(
            has_tables=has_tables,
            has_text=has_text,
            has_images=True,
            supports_ocr=False,
            supports_visualization=has_tables,
            supports_qa=True,
            supports_extraction=True,
            primary_content_type="presentation"
        )

    # Image files
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.heic', '.heif']:
        return FileCapabilities(
            has_tables=has_tables,  # Could have extracted tables from OCR
            has_text=has_text,  # Could have OCR'd text
            has_images=True,
            supports_ocr=True,
            supports_visualization=has_tables,
            supports_qa=True,  # Only if OCR extracted text
            supports_extraction=has_text,
            primary_content_type="image"
        )

    # HTML/Web files
    if ext in ['.html', '.htm', '.mhtml']:
        return FileCapabilities(
            has_tables=has_tables,
            has_text=has_text,
            has_images=has_images,
            supports_ocr=False,
            supports_visualization=has_tables,
            supports_qa=True,
            supports_extraction=True,
            primary_content_type="web"
        )

    # Email files
    if ext in ['.eml', '.msg']:
        return FileCapabilities(
            has_tables=False,
            has_text=has_text,
            has_images=has_images,
            supports_ocr=False,
            supports_visualization=False,
            supports_qa=True,
            supports_extraction=True,
            primary_content_type="email"
        )

    # Structured data files
    if ext in ['.json', '.xml', '.yaml', '.yml']:
        return FileCapabilities(
            has_tables=has_tables,
            has_text=has_text,
            has_images=False,
            supports_ocr=False,
            supports_visualization=has_tables,
            supports_qa=True,
            supports_extraction=True,
            primary_content_type="structured_data"
        )

    # Unknown/Other - be conservative
    logger.warning(f"Unknown file type: {ext}")
    return FileCapabilities(
        has_tables=has_tables,
        has_text=has_text,
        has_images=has_images,
        supports_ocr=False,
        supports_visualization=has_tables and has_tables,
        supports_qa=True,
        supports_extraction=has_text or has_tables,
        primary_content_type="unknown"
    )