# app/services/parsers/universal_parser.py

"""
Universal Document Parser
Handles ANY document type with intelligent routing, PyMuPDF PDF handling,
OCR fallback, and structured extraction.
"""

import logging
from typing import Dict, Any, List
from pathlib import Path
import re

import fitz  # PyMuPDF

from app.core import config
from app.parsers.extractors import (
    BankStatementExtractor,
    ReceiptExtractor,
    ContractExtractor,
    TaxFormExtractor,
    InvoiceExtractor,
)
from app.parsers.extractors.balance_sheet_extractor import BalanceSheetExtractor
from app.parsers.extractors.bill_extractor import BillExtractor
from app.parsers.extractors.credit_card_statement_executor import CreditCardStatementExtractor
from app.parsers.extractors.expense_report_extractor import ExpenseReportExtractor
from app.parsers.extractors.form_1099_extractor import Form1099Extractor
from app.parsers.extractors.income_statement_extractor import IncomeStatementExtractor
from app.parsers.extractors.legal_agreement_extractor import LegalAgreementExtractor
from app.parsers.extractors.medical_record_extractor import MedicalRecordExtractor
from app.parsers.extractors.paystub_extractor import PayStubExtractor
from app.parsers.extractors.purchase_order_extractor import PurchaseOrderExtractor
from app.parsers.extractors.tax_return_extractor import TaxReturnExtractor
from app.parsers.extractors.terms_of_service_extractor import TermsOfServiceExtractor
from app.parsers.extractors.title_extractor import TitleExtractor
from app.parsers.extractors.w2_extractor import W2Extractor

from app.parsers.office_parser import OfficeParser
from app.parsers.text_parser import TextParser
from app.parsers.image_parser import ImageParser
from app.parsers.email_parser import EmailParser
from app.parsers.web_parser import WebParser
from app.parsers.archive_parser import ArchiveParser
from app.parsers.extractors.text_table_extractor import TextTableExtractor

logger = logging.getLogger(__name__)


class UniversalParser:
    """
    Unified parser that handles all document types with PyMuPDF for PDFs
    and OCR fallback for hostile/vector PDFs.
    """

    SUPPORTED_FORMATS = {
        'pdf': ['pdf'],
        'office': ['docx', 'doc', 'xlsx', 'xls', 'xlsm', 'pptx', 'ppt', 'ods', 'odt', 'odp'],
        'text': ['txt', 'md', 'csv', 'tsv', 'json', 'xml', 'yaml', 'yml', 'log'],
        'image': ['jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp', 'webp', 'heic', 'heif', 'gif'],
        'email': ['eml', 'msg'],
        'web': ['html', 'htm', 'mhtml', 'mht'],
        'archive': ['zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'bz2'],
    }

    def __init__(self):
        self.text_table_extractor = TextTableExtractor()

        self.parsers = {
            'office': OfficeParser(),
            'text': TextParser(),
            'image': ImageParser(),
            'email': EmailParser(),
            'web': WebParser(),
            'archive': ArchiveParser(),
        }

    # -----------------------------
    # Corruption detector - IMPROVED
    # -----------------------------
    @staticmethod
    def _get_alphanumeric_ratio(text: str) -> float:
        """Get the ratio of alphanumeric characters to total (excluding whitespace)."""
        if not text:
            return 0.0
        text_no_ws = ''.join(text.split())
        if len(text_no_ws) == 0:
            return 0.0
        alnum_count = sum(1 for c in text_no_ws if c.isalnum())
        return alnum_count / len(text_no_ws)

    @staticmethod
    def _looks_corrupted(text: str) -> bool:
        """
        Detect if extracted text is corrupted, whitespace-only, or unusable.
        Catches vector PDFs, custom fonts, and encoding issues.
        """
        if not text:
            return True

        # Remove all whitespace for analysis
        text_no_ws = ''.join(text.split())

        # If less than 20 actual characters after removing whitespace, it's corrupted
        if len(text_no_ws) < 20:
            logger.warning(f"⚠️ Text has only {len(text_no_ws)} non-whitespace chars - marking as corrupted")
            return True

        # Check ratio of alphanumeric to total (excluding whitespace)
        alnum_count = sum(1 for c in text_no_ws if c.isalnum())
        if len(text_no_ws) > 0:
            alnum_ratio = alnum_count / len(text_no_ws)
            # If less than 30% alphanumeric, likely corrupted/encoded
            if alnum_ratio < 0.30:
                logger.warning(f"⚠️ Alphanumeric ratio {alnum_ratio:.2%} too low - marking as corrupted")
                return True

        # Check for excessive truly-junk characters (not common punctuation)
        bad = sum(1 for c in text if not (c.isalnum() or c in " .,:;/-\n\r\t()[]{}@#$%&*+=|\\\"'<>?!"))
        ratio = bad / max(1, len(text))
        if ratio > 0.20:
            logger.warning(f"⚠️ Junk character ratio {ratio:.2%} too high - marking as corrupted")
            return True

        return False

    # -----------------------------
    # Password-protected PDF check
    # -----------------------------
    @staticmethod
    def is_pdf_encrypted(file_path: str) -> bool:
        """Return True if the PDF requires a password to read."""
        try:
            doc = fitz.open(file_path)
            encrypted = doc.is_encrypted and not doc.authenticate("")
            doc.close()
            return encrypted
        except Exception:
            return False

    @staticmethod
    def unlock_pdf(file_path: str, password: str) -> bool:
        """Try to authenticate the PDF with the given password. Returns True on success."""
        try:
            doc = fitz.open(file_path)
            ok = doc.authenticate(password)
            doc.close()
            return bool(ok)
        except Exception:
            return False

    # -----------------------------
    # PyMuPDF PDF text extractor
    # -----------------------------
    def _parse_pdf_pymupdf(self, file_path: str, password: str = "") -> Dict[str, Any]:
        """
        Use PyMuPDF to extract text from PDF.
        If text looks corrupted or too sparse, we will OCR later.
        """
        doc = fitz.open(file_path)
        if doc.is_encrypted:
            doc.authenticate(password)
        texts: List[str] = []
        images: List[Dict[str, Any]] = []

        for page_index, page in enumerate(doc):
            page_text = page.get_text("text") or ""
            texts.append(page_text)

            # We don't extract embedded images structurally here; keep placeholder
            # If needed, you can extend this to extract images.
        full_text = "\n".join(texts)

        return {
            "text": full_text,
            "tables": [],  # tables will be inferred from text later if needed
            "images": images,
            "metadata": {
                "page_count": len(doc),
                "filename": Path(file_path).name,
            },
            "parser_used": "pdf_pymupdf",
            "confidence": 0.6,
        }

    # -----------------------------
    # Rasterize PDF pages with PyMuPDF
    # -----------------------------
    def _rasterize_pdf_to_images(self, pdf_path: str, dpi: int = 300) -> List[str]:
        """
        Convert each PDF page to a high-resolution PNG using PyMuPDF.
        Returns list of image file paths.
        """
        import tempfile
        import os

        doc = fitz.open(pdf_path)
        output_dir = tempfile.mkdtemp(prefix="unidocverse_pdf_")
        image_paths: List[str] = []

        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            img_path = os.path.join(output_dir, f"page_{i:03d}.png")
            pix.save(img_path)
            image_paths.append(img_path)

        return image_paths

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """
        Parse any document and return unified format.

        Returns:
            {
                'text': str,
                'tables': List[Dict],
                'images': List[Dict],
                'metadata': Dict,
                'structured_data': Dict,
                'entities': Dict,
                'document_type': str,
                'parser_used': str,
                'confidence': float,
                'warnings': List[str]
            }
        """
        try:
            logger.info(f"🔍 Parsing: {filename}")

            # Step 1: Detect format
            file_ext = Path(filename).suffix.lower().lstrip('.')
            if filename.lower().endswith('.tar.gz'):
                file_ext = 'tar.gz'

            parser_type = self._detect_parser_type(file_ext)
            logger.info(f"📄 Format: {file_ext} → Parser: {parser_type}")

            warnings: List[str] = []

            # Step 2: Route by parser type
            if parser_type == "pdf":
                # ---- Check for password protection first ----
                if self.is_pdf_encrypted(file_path):
                    logger.warning(f"🔒 PDF is password-protected: {filename}")
                    return {
                        "text": "",
                        "tables": [],
                        "images": [],
                        "metadata": {"filename": filename},
                        "structured_data": {},
                        "entities": {},
                        "document_type": "unknown",
                        "parser_used": "none",
                        "confidence": 0.0,
                        "warnings": [],
                        "password_required": True,
                    }

                # ---- PDF via PyMuPDF ----
                logger.info("📄 Using PyMuPDF for PDF parsing")
                raw_result = self._parse_pdf_pymupdf(file_path)
                text = raw_result.get("text", "") or ""
                tables = raw_result.get("tables", [])

                logger.info(f"📄 PyMuPDF extracted {len(text)} chars, {len(tables)} tables")

                # Corruption / hostility check → rasterize + OCR
                if self._looks_corrupted(text):
                    logger.warning("⚠️ PDF text appears corrupted/encoded/sparse — forcing rasterization + OCR")

                    # Log a sample of the corrupted text for debugging
                    text_sample = text[:200].replace('\n', '\\n').replace('\r', '\\r')
                    logger.debug(f"📋 Corrupted text sample: {text_sample}")

                    try:
                        image_paths = self._rasterize_pdf_to_images(file_path, dpi=300)
                        ocr_text_parts: List[str] = []

                        image_parser = ImageParser()
                        for img_path in image_paths:
                            ocr_result = image_parser.parse(img_path, Path(img_path).name)
                            ocr_text = ocr_result.get("text", "") or ""
                            if ocr_text:
                                ocr_text_parts.append(ocr_text)

                        ocr_text_full = "\n".join(ocr_text_parts)

                        # Compare quality, not just quantity
                        if ocr_text_full:
                            ocr_alnum_ratio = self._get_alphanumeric_ratio(ocr_text_full)
                            orig_alnum_ratio = self._get_alphanumeric_ratio(text)

                            logger.info(
                                f"📊 Quality comparison: Original={orig_alnum_ratio:.1%} alphanumeric, "
                                f"OCR={ocr_alnum_ratio:.1%} alphanumeric"
                            )

                            ocr_quality = not self._looks_corrupted(ocr_text_full)
                            original_quality = not self._looks_corrupted(text)

                            # Use OCR if it's better quality OR longer and reasonable quality
                            use_ocr = (
                                    (ocr_quality and not original_quality) or  # OCR good, original bad
                                    (ocr_quality and len(ocr_text_full) > len(text) * 0.5)  # OCR good and substantial
                            )

                            if use_ocr:
                                logger.info(
                                    f"✅ Using OCR result: {len(ocr_text_full)} chars "
                                    f"(original: {len(text)} chars, {orig_alnum_ratio:.1%} quality)"
                                )
                                raw_result["text"] = ocr_text_full
                                raw_result["parser_used"] = "pdf_pymupdf_ocr"
                                warnings.append("Used PyMuPDF rasterization + OCR due to corrupted/encoded text layer")
                            else:
                                logger.warning(
                                    f"⚠️ Keeping original text despite OCR: "
                                    f"OCR={len(ocr_text_full)} chars ({ocr_alnum_ratio:.1%} quality), "
                                    f"Original={len(text)} chars ({orig_alnum_ratio:.1%} quality)"
                                )
                                warnings.append("PDF text quality low but OCR also had issues - using original")
                        else:
                            logger.warning("⚠️ Rasterization + OCR produced no text")
                            warnings.append("PDF text appears corrupted and OCR failed to extract text")

                    except Exception as e:
                        logger.error(f"❌ Rasterization + OCR failed: {e}", exc_info=True)
                        warnings.append(f"Rasterization + OCR failed: {str(e)}")

            else:
                # ---- Non-PDF: use existing parsers ----
                parser = self.parsers.get(parser_type)
                if not parser:
                    return self._fallback_parse(file_path, filename)

                raw_result = parser.parse(file_path, filename)
                text = raw_result.get("text") or raw_result.get("raw_text", "") or ""
                tables = raw_result.get("tables", [])
                logger.info(f"📄 Primary parser '{parser_type}' extracted {len(text)} chars, {len(tables)} tables")

                # For images, we trust ImageParser; for others, no corruption check here.
                if parser_type == "image" and (self._looks_corrupted(text) or len(text) < 20):
                    warnings.append("Image text appears low-quality or corrupted")

            # Step 3: Normalize result
            normalized_result = self._normalize_result(raw_result, parser_type, filename, extra_warnings=warnings)

            # Step 4: Enrich (entities, inferred tables)
            enriched_result = self._enrich_result(normalized_result, filename)

            # Step 5: Detect semantic document type
            enriched_result['document_type'] = self._detect_document_type(enriched_result)

            # Step 6: Structured extraction
            enriched_result['structured_data'] = self._extract_structured_data(enriched_result)

            logger.info(f"✅ Parsed successfully: {enriched_result['document_type']}")
            return enriched_result

        except Exception as e:
            logger.error(f"❌ Parsing failed: {e}", exc_info=True)
            return {
                'text': '',
                'tables': [],
                'images': [],
                'metadata': {'error': str(e), 'filename': filename},
                'entities': {},
                'structured_data': {},
                'error': str(e),
                'document_type': 'unknown',
                'parser_used': 'none',
                'confidence': 0.0,
                'warnings': [str(e)],
            }

    def _detect_parser_type(self, file_ext: str) -> str:
        if file_ext in ['tar.gz', 'tgz']:
            return 'archive'
        for parser_type, extensions in self.SUPPORTED_FORMATS.items():
            if file_ext in extensions:
                return parser_type
        logger.warning(f"⚠️ Unknown extension: {file_ext}, using text parser")
        return 'text'

    def _normalize_result(
            self,
            result: Dict[str, Any],
            parser_type: str,
            filename: str,
            extra_warnings: List[str] = None,
    ) -> Dict[str, Any]:
        normalized = {
            'text': result.get('text') or result.get('raw_text', '') or '',
            'tables': result.get('tables', []),
            'images': result.get('images', []),
            'metadata': result.get('metadata', {}),
            'parser_used': result.get('parser_used', parser_type),
            'confidence': result.get('confidence', 0.5),
            'warnings': [],
        }

        if 'filename' not in normalized['metadata']:
            normalized['metadata']['filename'] = filename

        if result.get('error'):
            normalized['warnings'].append(result['error'])

        if len(normalized['text']) < 50:
            normalized['warnings'].append('Very little text extracted')

        if extra_warnings:
            normalized['warnings'].extend(extra_warnings)

        return normalized

    def _enrich_result(self, result: Dict[str, Any], filename: str) -> Dict[str, Any]:
        text = result.get('text', '') or ''

        if text:
            result['entities'] = self._extract_entities(text)
        else:
            result['entities'] = {}

        if text and not result.get('tables'):
            inferred_tables = self.text_table_extractor.extract(text)
            if inferred_tables:
                result['tables'] = inferred_tables
                result.setdefault('warnings', []).append('Tables inferred from text')

        return result

    def _extract_entities(self, text: str) -> Dict[str, List[str]]:
        entities = {
            'dates': [],
            'money': [],
            'emails': [],
            'phones': [],
            'organizations': [],
        }

        date_patterns = [
            r'\d{1,2}/\d{1,2}/\d{2,4}',
            r'\d{4}-\d{2}-\d{2}',
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}',
        ]
        for pattern in date_patterns:
            entities['dates'].extend(re.findall(pattern, text, re.IGNORECASE))
        entities['dates'] = list(set(entities['dates']))

        money_patterns = [
            r'\$[\d,]+\.?\d*',
            r'USD [\d,]+\.?\d*',
            r'[\d,]+\.?\d* USD',
            r'€[\d,]+\.?\d*',
            r'£[\d,]+\.?\d*',
        ]
        for pattern in money_patterns:
            entities['money'].extend(re.findall(pattern, text))
        entities['money'] = list(set(entities['money']))

        entities['emails'] = list(set(re.findall(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            text
        )))

        entities['phones'] = list(set(re.findall(
            r'\b(?:\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b',
            text
        )))

        return entities

    def _detect_document_type(self, result: Dict[str, Any]) -> str:
        text = result.get('text', '')[:3000]
        if not text or len(text) < 20:
            return 'unknown'

        text_lower = text.lower()

        if any(w in text_lower for w in
               ['account balance', 'beginning balance', 'ending balance', 'transaction history']):
            if len(result.get('tables', [])) > 0:
                return 'bank_statement'

        if any(w in text_lower for w in ['invoice number', 'bill to', 'invoice date', 'payment terms', 'amount due']):
            return 'invoice'

        if any(w in text_lower for w in ['receipt', 'total paid', 'change due', 'thank you for your purchase']):
            return 'receipt'

        if any(w in text_lower for w in
               ['this agreement', 'whereas', 'hereby agree', 'terms and conditions', 'party of the first part']):
            return 'contract'

        if any(w in text_lower for w in ['resume', 'curriculum vitae', 'work experience', 'education', 'skills']):
            return 'resume'

        if any(w in text_lower for w in ['certificate', 'hereby certify', 'awarded to', 'completion']):
            return 'certificate'

        if result.get('parser_used') == 'email':
            return 'email'

        if result.get('parser_used') == 'office' and len(result.get('tables', [])) > 0:
            return 'spreadsheet'

        if result.get('parser_used') in ('image', 'pdf_pymupdf_ocr'):
            return 'image_document'

        try:
            return self._ai_classify_document(text)
        except Exception as e:
            logger.warning(f"AI classification failed: {e}, using generic type")
            return 'document'

    def _ai_classify_document(self, text: str) -> str:
        try:
            import ollama

            prompt = f"""Analyze this document and classify its type.

Document content (first 500 chars):
{text[:500]}

Classify as ONE of these types:
invoice, bank_statement, receipt, contract, tax_form, medical_record, resume, letter, report, spreadsheet, certificate, transcript, email, other

Respond with ONLY the type name, nothing else."""

            client = ollama.Client()
            response = client.generate(
                model=config.model,
                prompt=prompt,
                options={"temperature": 0.1},
            )

            doc_type = response['response'].strip().lower()
            valid_types = [
                'invoice', 'bank_statement', 'receipt', 'contract', 'tax_form',
                'medical_record', 'resume', 'letter', 'report', 'spreadsheet',
                'certificate', 'transcript', 'email', 'other', 'document',
            ]

            if doc_type in valid_types:
                logger.info(f"🤖 AI classified as: {doc_type}")
                return doc_type
            logger.warning(f"Invalid AI response: {doc_type}")
            return 'document'

        except Exception as e:
            logger.error(f"AI classification error: {e}")
            return 'document'

    def _extract_structured_data(self, result: Dict[str, Any]) -> Dict[str, Any]:
        doc_type = result.get('document_type', 'unknown')
        text = result.get('text', '')
        tables = result.get('tables', [])

        extractors = {
            'invoice': InvoiceExtractor(),
            'bank_statement': BankStatementExtractor(),
            'receipt': ReceiptExtractor(),
            'tax_form': TaxFormExtractor(),
            'pay_stub': PayStubExtractor(),
            'credit_card_statement': CreditCardStatementExtractor(),
            'balance_sheet': BalanceSheetExtractor(),
            'income_statement': IncomeStatementExtractor(),
            'purchase_order': PurchaseOrderExtractor(),
            'bill': BillExtractor(),
            'expense_report': ExpenseReportExtractor(),

            'contract': ContractExtractor(),
            'legal_agreement': LegalAgreementExtractor(),
            'terms_of_service': TermsOfServiceExtractor(),

            'medical_record': MedicalRecordExtractor(),

            'tax_return': TaxReturnExtractor(),
            'w2': W2Extractor(),
            '1099': Form1099Extractor(),
            'title': TitleExtractor(),
        }

        extractor = extractors.get(doc_type)
        if extractor:
            try:
                return extractor.extract(text, tables)
            except Exception as e:
                logger.error(f"Structured extraction failed for {doc_type}: {e}")
                return {}

        return {}

    def _fallback_parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        logger.warning("⚠️ Unsupported format, using fallback text reader")
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
            return {
                'text': text,
                'tables': [],
                'images': [],
                'metadata': {'filename': filename},
                'parser_used': 'fallback',
                'confidence': 0.3,
                'warnings': ['Unsupported format, used text fallback'],
            }
        except Exception as e:
            logger.error(f"Fallback parse failed: {e}", exc_info=True)
            return {
                'text': '',
                'tables': [],
                'images': [],
                'metadata': {'filename': filename, 'error': str(e)},
                'error': 'Failed to read file',
                'parser_used': 'none',
                'confidence': 0.0,
                'warnings': [f'Failed to read file: {str(e)}'],
            }


# # app/services/parsers/universal_parser.py
#
#
# """
# Universal Document Parser
# Handles ANY document type with intelligent routing and extraction
# """
#
# import logging
# from typing import Dict, Any, List, Optional
# from pathlib import Path
# import mimetypes
# import re
# import json
#
# from app.parsers.extractors import (
#     BankStatementExtractor,
#     ReceiptExtractor,
#     ContractExtractor,
#     TaxFormExtractor,
#     InvoiceExtractor
# )
# from app.parsers.extractors.balance_sheet_extractor import BalanceSheetExtractor
# from app.parsers.extractors.bill_extractor import BillExtractor
# from app.parsers.extractors.credit_card_statement_executor import CreditCardStatementExtractor
# from app.parsers.extractors.expense_report_extractor import ExpenseReportExtractor
# from app.parsers.extractors.form_1099_extractor import Form1099Extractor
# from app.parsers.extractors.income_statement_extractor import IncomeStatementExtractor
# from app.parsers.extractors.legal_agreement_extractor import LegalAgreementExtractor
# from app.parsers.extractors.medical_record_extractor import MedicalRecordExtractor
# from app.parsers.extractors.paystub_extractor import PayStubExtractor
# from app.parsers.extractors.purchase_order_extractor import PurchaseOrderExtractor
# from app.parsers.extractors.tax_return_extractor import TaxReturnExtractor
# from app.parsers.extractors.terms_of_service_extractor import TermsOfServiceExtractor
# from app.parsers.extractors.title_extractor import TitleExtractor
# from app.parsers.extractors.w2_extractor import W2Extractor
#
# # Import all parsers
# from app.parsers.pdf_parser import PDFParser
# from app.parsers.office_parser import OfficeParser
# from app.parsers.text_parser import TextParser
# from app.parsers.image_parser import ImageParser
# from app.parsers.email_parser import EmailParser
# from app.parsers.web_parser import WebParser
# from app.parsers.archive_parser import ArchiveParser
# from app.parsers.extractors.text_table_extractor import TextTableExtractor
#
#
# logger = logging.getLogger(__name__)
#
#
# class UniversalParser:
#     """
#     Unified parser that handles all document types
#     """
#
#     # Supported formats
#     SUPPORTED_FORMATS = {
#         # Documents
#         'pdf': ['pdf'],
#         'office': ['docx', 'doc', 'xlsx', 'xls', 'xlsm', 'pptx', 'ppt', 'ods', 'odt', 'odp'],
#         'text': ['txt', 'md', 'csv', 'tsv', 'json', 'xml', 'yaml', 'yml', 'log'],
#         # Images (OCR needed)
#         'image': ['jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp', 'webp', 'heic', 'heif', 'gif'],
#         # Email
#         'email': ['eml', 'msg'],
#         # Web
#         'web': ['html', 'htm', 'mhtml', 'mht'],
#         # Archives
#         'archive': ['zip', 'tar', 'gz', 'tgz'],
#     }
#
#     def __init__(self):
#         self.text_table_extractor = TextTableExtractor()
#
#         self.parsers = {
#             'pdf': PDFParser(),
#             'office': OfficeParser(),
#             'text': TextParser(),
#             'image': ImageParser(),
#             'email': EmailParser(),
#             'web': WebParser(),
#             'archive': ArchiveParser(),
#         }
#
#     def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
#         """
#         Parse any document and return unified format
#
#         Returns:
#             {
#                 'text': str,                  # Full text content (normalized key)
#                 'tables': List[Dict],         # Extracted tables
#                 'images': List[Dict],         # Embedded images
#                 'metadata': Dict,             # File metadata
#                 'structured_data': Dict,      # Semantic extraction
#                 'entities': Dict,             # Named entities
#                 'document_type': str,         # Detected type
#                 'parser_used': str,           # Which parser
#                 'confidence': float,          # Extraction confidence
#                 'warnings': List[str]         # Any issues
#             }
#         """
#         try:
#             logger.info(f"🔍 Parsing: {filename}")
#
#             # Step 1: Detect format
#             file_ext = Path(filename).suffix.lower().lstrip('.')
#
#             # Handle double extensions like .tar.gz
#             if filename.lower().endswith('.tar.gz'):
#                 file_ext = 'tar.gz'
#
#             parser_type = self._detect_parser_type(file_ext)
#
#             logger.info(f"📄 Format: {file_ext} → Parser: {parser_type}")
#
#             # Step 2: Use specialized parser
#             parser = self.parsers.get(parser_type)
#             if not parser:
#                 return self._fallback_parse(file_path, filename)
#
#             raw_result = parser.parse(file_path, filename)
#
#             # Step 3: Normalize result format
#             normalized_result = self._normalize_result(raw_result, parser_type, filename)
#
#             # Step 4: Enrich with universal extraction
#             enriched_result = self._enrich_result(normalized_result, filename)
#
#             # Step 5: Detect semantic document type
#             enriched_result['document_type'] = self._detect_document_type(enriched_result)
#
#             # Step 6: Extract structured data based on type
#             enriched_result['structured_data'] = self._extract_structured_data(enriched_result)
#
#             logger.info(f"✅ Parsed successfully: {enriched_result['document_type']}")
#
#             return enriched_result
#
#         except Exception as e:
#             logger.error(f"❌ Parsing failed: {e}")
#             import traceback
#             logger.error(traceback.format_exc())
#
#             return {
#                 'text': '',
#                 'tables': [],
#                 'images': [],
#                 'metadata': {'error': str(e), 'filename': filename},
#                 'entities': {},
#                 'structured_data': {},
#                 'error': str(e),
#                 'document_type': 'unknown',
#                 'parser_used': 'none',
#                 'confidence': 0.0,
#                 'warnings': [str(e)]
#             }
#
#     def _detect_parser_type(self, file_ext: str) -> str:
#         """Detect which parser to use based on extension"""
#
#         # Handle compound extensions
#         if file_ext in ['tar.gz', 'tgz']:
#             return 'archive'
#
#         for parser_type, extensions in self.SUPPORTED_FORMATS.items():
#             if file_ext in extensions:
#                 return parser_type
#
#         logger.warning(f"⚠️ Unknown extension: {file_ext}, using text parser")
#         return 'text'  # Default fallback
#
#     def _normalize_result(self, result: Dict, parser_type: str, filename: str) -> Dict[str, Any]:
#         """
#         Normalize parser output to consistent format
#         Some parsers return 'text', others 'raw_text' - unify to 'text'
#         """
#
#         normalized = {
#             'text': result.get('text') or result.get('raw_text', ''),
#             'tables': result.get('tables', []),
#             'images': result.get('images', []),
#             'metadata': result.get('metadata', {}),
#             'parser_used': parser_type,
#             'confidence': result.get('confidence', 0.5),
#             'warnings': []
#         }
#
#         # Add filename to metadata if not present
#         if 'filename' not in normalized['metadata']:
#             normalized['metadata']['filename'] = filename
#
#         # Check for warnings
#         if result.get('error'):
#             normalized['warnings'].append(result['error'])
#
#         if len(normalized['text']) < 50:
#             normalized['warnings'].append('Very little text extracted')
#
#         return normalized
#
#     def _enrich_result(self, result: Dict, filename: str) -> Dict[str, Any]:
#         """Add universal enrichments"""
#
#         text = result.get('text', '')
#
#         # Entity extraction
#         if text:
#             result['entities'] = self._extract_entities(text)
#         else:
#             result['entities'] = {}
#
#         # 🔥 Infer tables from text if none found
#         if text and not result.get('tables'):
#             inferred_tables = self.text_table_extractor.extract(text)
#             if inferred_tables:
#                 result['tables'] = inferred_tables
#                 result.setdefault('warnings', []).append(
#                     'Tables inferred from text'
#                 )
#
#         return result
#
#     def _extract_entities(self, text: str) -> Dict[str, List[str]]:
#         """Extract named entities (dates, money, organizations, etc.)"""
#
#         entities = {
#             'dates': [],
#             'money': [],
#             'emails': [],
#             'phones': [],
#             'organizations': [],
#         }
#
#         # Dates (various formats)
#         date_patterns = [
#             r'\d{1,2}/\d{1,2}/\d{2,4}',
#             r'\d{4}-\d{2}-\d{2}',
#             r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}',
#         ]
#         for pattern in date_patterns:
#             entities['dates'].extend(re.findall(pattern, text, re.IGNORECASE))
#
#         # Remove duplicates
#         entities['dates'] = list(set(entities['dates']))
#
#         # Money amounts
#         money_patterns = [
#             r'\$[\d,]+\.?\d*',
#             r'USD [\d,]+\.?\d*',
#             r'[\d,]+\.?\d* USD',
#             r'€[\d,]+\.?\d*',
#             r'£[\d,]+\.?\d*',
#         ]
#         for pattern in money_patterns:
#             entities['money'].extend(re.findall(pattern, text))
#
#         entities['money'] = list(set(entities['money']))
#
#         # Emails
#         entities['emails'] = list(set(re.findall(
#             r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
#             text
#         )))
#
#         # Phone numbers (US format)
#         entities['phones'] = list(set(re.findall(
#             r'\b(?:\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b',
#             text
#         )))
#
#         return entities
#
#     def _detect_document_type(self, result: Dict) -> str:
#         """Detect semantic document type using keywords + AI"""
#
#         text = result.get('text', '')[:3000]  # First 3k chars
#
#         if not text or len(text) < 20:
#             return 'unknown'
#
#         # Quick keyword detection first (fast)
#         text_lower = text.lower()
#
#         # Bank statement indicators
#         if any(word in text_lower for word in
#                ['account balance', 'beginning balance', 'ending balance', 'transaction history']):
#             if len(result.get('tables', [])) > 0:
#                 return 'bank_statement'
#
#         # Invoice indicators
#         if any(word in text_lower for word in
#                ['invoice number', 'bill to', 'invoice date', 'payment terms', 'amount due']):
#             return 'invoice'
#
#         # Receipt indicators
#         if any(word in text_lower for word in ['receipt', 'total paid', 'change due', 'thank you for your purchase']):
#             return 'receipt'
#
#         # Contract indicators
#         if any(word in text_lower for word in
#                ['this agreement', 'whereas', 'hereby agree', 'terms and conditions', 'party of the first part']):
#             return 'contract'
#
#         # Resume indicators
#         if any(word in text_lower for word in ['resume', 'curriculum vitae', 'work experience', 'education', 'skills']):
#             return 'resume'
#
#         # Certificate indicators
#         if any(word in text_lower for word in ['certificate', 'hereby certify', 'awarded to', 'completion']):
#             return 'certificate'
#
#         # Email
#         if result.get('parser_used') == 'email':
#             return 'email'
#
#         # Spreadsheet
#         if result.get('parser_used') == 'office' and len(result.get('tables', [])) > 0:
#             return 'spreadsheet'
#
#         # Image/scan
#         if result.get('parser_used') == 'image':
#             return 'image_document'
#
#         # Use AI for complex detection (slower, fallback)
#         try:
#             return self._ai_classify_document(text)
#         except Exception as e:
#             logger.warning(f"AI classification failed: {e}, using generic type")
#             return 'document'
#
#     def _ai_classify_document(self, text: str) -> str:
#         """Use AI to classify document type"""
#
#         try:
#             import ollama
#
#             prompt = f"""Analyze this document and classify its type.
#
# Document content (first 500 chars):
# {text[:500]}
#
# Classify as ONE of these types:
# invoice, bank_statement, receipt, contract, tax_form, medical_record, resume, letter, report, spreadsheet, certificate, transcript, email, other
#
# Respond with ONLY the type name, nothing else."""
#
#             client = ollama.Client()
#             response = client.generate(
#                 model="phi3:mini",
#                 prompt=prompt,
#                 options={"temperature": 0.1}  # More deterministic
#             )
#
#             doc_type = response['response'].strip().lower()
#
#             # Validate response
#             valid_types = [
#                 'invoice', 'bank_statement', 'receipt', 'contract', 'tax_form',
#                 'medical_record', 'resume', 'letter', 'report', 'spreadsheet',
#                 'certificate', 'transcript', 'email', 'other', 'document'
#             ]
#
#             if doc_type in valid_types:
#                 logger.info(f"🤖 AI classified as: {doc_type}")
#                 return doc_type
#             else:
#                 logger.warning(f"Invalid AI response: {doc_type}")
#                 return 'document'
#
#         except Exception as e:
#             logger.error(f"AI classification error: {e}")
#             return 'document'
#
#     def _extract_structured_data(self, result: Dict) -> Dict[str, Any]:
#         """Extract structured data based on document type"""
#
#         doc_type = result.get('document_type', 'unknown')
#         text = result.get('text', '')
#         tables = result.get('tables', [])
#
#         # Map document types to extractors
#         # Map document types to extractors
#         extractors = {
#             # Financial Documents
#             'invoice': InvoiceExtractor(),
#             'bank_statement': BankStatementExtractor(),
#             'receipt': ReceiptExtractor(),
#             'tax_form': TaxFormExtractor(),
#             'pay_stub': PayStubExtractor(),
#             'credit_card_statement': CreditCardStatementExtractor(),
#             'balance_sheet': BalanceSheetExtractor(),
#             'income_statement': IncomeStatementExtractor(),
#             'purchase_order': PurchaseOrderExtractor(),
#             'bill': BillExtractor(),
#             'expense_report': ExpenseReportExtractor(),
#
#             # Legal Documents
#             'contract': ContractExtractor(),
#             'legal_agreement': LegalAgreementExtractor(),
#             'terms_of_service': TermsOfServiceExtractor(),
#             #'privacy_policy': PrivacyPolicyExtractor(),
#             #'nda': NDAExtractor(),
#             #'power_of_attorney': PowerOfAttorneyExtractor(),
#             #'court_filing': CourtFilingExtractor(),
#
#             # Medical Documents
#             'medical_record': MedicalRecordExtractor(),
#             #'lab_report': LabReportExtractor(),
#             #'prescription': PrescriptionExtractor(),
#             #'insurance_claim': InsuranceClaimExtractor(),
#             #'discharge_summary': DischargeSummaryExtractor(),
#             #'vaccination_record': VaccinationRecordExtractor(),
#
#             # Identity & Credentials
#             #'passport': PassportExtractor(),
#             #'drivers_license': DriversLicenseExtractor(),
#             # 'id_card': IDCardExtractor(),
#             #'birth_certificate': BirthCertificateExtractor(),
#             # 'social_security_card': SSNCardExtractor(),
#             # 'visa': VisaExtractor(),
#
#             # Real Estate
#             #'lease_agreement': LeaseAgreementExtractor(),
#             #'mortgage_document': MortgageExtractor(),
#             #'property_deed': PropertyDeedExtractor(),
#             # 'home_inspection': HomeInspectionExtractor(),
#             #'appraisal_report': AppraisalReportExtractor(),
#
#             # Business Documents
#             #'business_plan': BusinessPlanExtractor(),
#             #'resume': ResumeExtractor(),
#             #'job_description': JobDescriptionExtractor(),
#             #'whitepaper': WhitepaperExtractor(),
#             # 'proposal': ProposalExtractor(),
#             # 'memo': MemoExtractor(),
#             # 'letter': LetterExtractor(),
#             # 'pitch_deck': PitchDeckExtractor(),
#
#             # Academic
#             #'research_paper': ResearchPaperExtractor(),
#             #'thesis': ThesisExtractor(),
#             # 'transcript': TranscriptExtractor(),
#             #'certificate': CertificateExtractor(),
#             # 'diploma': DiplomaExtractor(),
#
#             # Insurance
#             #'insurance_policy': InsurancePolicyExtractor(),
#             #'claim_form': ClaimFormExtractor(),
#             # 'insurance_quote': InsuranceQuoteExtractor(),
#
#             # Government
#             'tax_return': TaxReturnExtractor(),
#             'w2': W2Extractor(),
#             '1099': Form1099Extractor(),
#             'title': TitleExtractor(),
#             # 'permit': PermitExtractor(),
#             # 'license': LicenseExtractor(),
#         }
#
#         # extractors = {
#         #     'invoice': InvoiceExtractor(),
#         #     'bank_statement': BankStatementExtractor(),
#         #     'receipt': ReceiptExtractor(),
#         #     'contract': ContractExtractor(),
#         #     'tax_form': TaxFormExtractor(),
#         # }
#
#         extractor = extractors.get(doc_type)
#         if extractor:
#             try:
#                 return extractor.extract(text, tables)
#             except Exception as e:
#                 logger.error(f"Structured extraction failed for {doc_type}: {e}")
#                 return {}
#
#         return {}
#
#     def _fallback_parse(self, file_path: str, filename: str) -> Dict[str, Any]:
#         """Fallback for unsupported formats"""
#
#         logger.warning(f"⚠️ Unsupported format, using fallback")
#
#         try:
#             # Try to read as text
#             with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
#                 text = f.read()
#
#             return {
#                 'text': text,
#                 'tables': [],
#                 'images': [],
#                 'metadata': {'filename': filename},
#                 'parser_used': 'fallback',
#                 'confidence': 0.3,
#                 'warnings': ['Unsupported format, used text fallback']
#             }
#         except Exception as e:
#             logger.error(f"Fallback parse failed: {e}")
#             return {
#                 'text': '',
#                 'tables': [],
#                 'images': [],
#                 'metadata': {'filename': filename, 'error': str(e)},
#                 'error': 'Failed to read file',
#                 'parser_used': 'none',
#                 'confidence': 0.0,
#                 'warnings': [f'Failed to read file: {str(e)}']
#             }
#
#
#
# # import pdfplumber
# # import pandas as pd
# # from typing import Dict, Any
# #
# # class UniversalParser:
# #     """
# #     Generic fallback parser for any document type.
# #     Extracts text, tables, and basic metadata.
# #     """
# #
# #     def parse(self, file_path: str) -> Dict[str, Any]:
# #         try:
# #             # CSV / Excel
# #             if file_path.lower().endswith((".csv", ".xlsx", ".xls")):
# #                 df = pd.read_csv(file_path) if file_path.endswith(".csv") else pd.read_excel(file_path)
# #                 return {
# #                     "text": "",
# #                     "tables": [{
# #                         "columns": list(df.columns),
# #                         "rows": df.fillna("").values.tolist()
# #                     }],
# #                     "metadata": {
# #                         "row_count": len(df),
# #                         "column_count": len(df.columns),
# #                     }
# #                 }
# #
# #             # PDF
# #             if file_path.lower().endswith(".pdf"):
# #                 with pdfplumber.open(file_path) as pdf:
# #                     text = "\n".join([page.extract_text() or "" for page in pdf.pages])
# #                     tables = []
# #                     for page in pdf.pages:
# #                         for t in page.extract_tables() or []:
# #                             tables.append({
# #                                 "columns": t[0],
# #                                 "rows": t[1:]
# #                             })
# #
# #                 return {
# #                     "text": text,
# #                     "tables": tables,
# #                     "metadata": {
# #                         "page_count": len(pdf.pages),
# #                         "table_count": len(tables),
# #                     }
# #                 }
# #
# #             # Fallback for unknown formats
# #             with open(file_path, "r", errors="ignore") as f:
# #                 text = f.read()
# #
# #             return {
# #                 "text": text,
# #                 "tables": [],
# #                 "metadata": {}
# #             }
# #
# #         except Exception as e:
# #             return {
# #                 "error": str(e),
# #                 "text": "",
# #                 "tables": [],
# #                 "metadata": {}
# #             }
