import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PDFParser:
    """Improved universal PDF parsing with smart OCR fallback."""

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        result = {
            "filename": filename,
            "raw_text": "",
            "tables": [],
            "images": [],
            "metadata": {},
            "parser_used": "pdfminer + pdfplumber",
            "confidence": 0.90
        }

        try:
            # 1) PDFMiner text
            try:
                from pdfminer.high_level import extract_text
                text = extract_text(file_path)
                if text and len(text.strip()) > 20:
                    result["raw_text"] = text
                    logger.info(f"PDFMiner extracted {len(text)} characters")
                else:
                    logger.warning("PDFMiner returned empty or tiny text")
            except Exception as e:
                logger.warning(f"PDFMiner failed: {e}")

            # 2) pdfplumber tables + images + metadata
            try:
                import pdfplumber
                with pdfplumber.open(file_path) as pdf:
                    meta = pdf.metadata or {}
                    result["metadata"] = {
                        "pages": len(pdf.pages),
                        "producer": meta.get("Producer"),
                        "creator": meta.get("Creator"),
                        "title": meta.get("Title"),
                        "subject": meta.get("Subject"),
                    }

                    all_tables = []
                    all_images = []

                    for page_num, page in enumerate(pdf.pages, start=1):
                        # tables
                        try:
                            tables = page.extract_tables()
                            if tables:
                                for idx, table in enumerate(tables):
                                    all_tables.append({
                                        "page": page_num,
                                        "table_index": idx,
                                        "data": table,
                                        "rows": len(table),
                                        "cols": len(table[0]) if table else 0
                                    })
                        except Exception as e:
                            logger.warning(f"Table extraction failed on page {page_num}: {e}")

                        # images
                        try:
                            for img in page.images:
                                all_images.append({
                                    "page": page_num,
                                    "x0": img.get("x0"),
                                    "y0": img.get("y0"),
                                    "width": img.get("width"),
                                    "height": img.get("height")
                                })
                        except Exception as e:
                            logger.warning(f"Image extraction failed on page {page_num}: {e}")

                    result["tables"] = all_tables
                    result["images"] = all_images
                    result["metadata"]["tables_found"] = len(all_tables)
                    result["metadata"]["images_found"] = len(all_images)

                    logger.info(
                        f"pdfplumber found {len(all_tables)} tables and {len(all_images)} images"
                    )
            except Exception as e:
                logger.warning(f"pdfplumber failed: {e}")

            # 3) Smart OCR trigger
            text_len = len(result["raw_text"].strip())
            scanned_pdf = result["metadata"].get("images_found", 0) > 50

            if scanned_pdf or text_len < 500:
                logger.info(
                    f"OCR triggered → scanned={scanned_pdf}, text_len={text_len}"
                )
                try:
                    import pytesseract
                    from pdf2image import convert_from_path

                    pages = convert_from_path(file_path, dpi=200)
                    ocr_text = []
                    for i, page_img in enumerate(pages):
                        try:
                            page_text = pytesseract.image_to_string(page_img)
                            ocr_text.append(page_text)
                        except Exception as e:
                            logger.warning(f"OCR failed on page {i+1}: {e}")

                    if ocr_text:
                        result["raw_text"] = "\n\n".join(ocr_text)
                        result["parser_used"] += " + OCR"
                        logger.info(f"OCR extracted {len(result['raw_text'])} characters")
                    else:
                        logger.warning("OCR produced no text")
                except Exception as e:
                    logger.warning(f"OCR fallback failed: {e}")

            # 4) Confidence scoring
            final_len = len(result["raw_text"].strip())
            if final_len > 5000:
                result["confidence"] = 0.95
            elif final_len > 1000:
                result["confidence"] = 0.85
            elif final_len > 200:
                result["confidence"] = 0.70
            else:
                result["confidence"] = 0.40

            return result

        except Exception as e:
            logger.error(f"PDF parsing failed: {e}")
            return {
                "filename": filename,
                "raw_text": "",
                "tables": [],
                "images": [],
                "metadata": {},
                "error": str(e),
                "parser_used": "hybrid",
                "confidence": 0.0
            }




# import logging
# from typing import Dict, Any, List
#
# logger = logging.getLogger(__name__)
#
# class PDFParser:
#     """Advanced PDF parsing with table extraction (PDFMiner + pdfplumber hybrid)"""
#
#     def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
#         """Parse PDF with text and table extraction"""
#         result = {
#             'raw_text': '',
#             'tables': [],
#             'images': [],
#             'metadata': {},
#             'parser_used': 'hybrid',
#             'confidence': 0.9
#         }
#
#         try:
#             # --- Step 1: Extract text using PDFMiner (better for digital PDFs) ---
#             try:
#                 from pdfminer.high_level import extract_text
#                 text = extract_text(file_path)
#                 if text:
#                     result['raw_text'] = text
#                     logger.info(f"✅ PDFMiner extracted {len(text)} characters")
#                 else:
#                     logger.warning("⚠️ PDFMiner extracted empty text")
#             except Exception as e:
#                 logger.warning(f"⚠️ PDFMiner failed: {e}")
#                 text = ""
#
#             # --- Step 2: Extract tables & images using pdfplumber ---
#             try:
#                 import pdfplumber
#                 with pdfplumber.open(file_path) as pdf:
#                     result['metadata'] = {
#                         'pages': len(pdf.pages),
#                         'producer': pdf.metadata.get('Producer'),
#                         'creator': pdf.metadata.get('Creator'),
#                     }
#
#                     all_tables = []
#                     images_list = []
#
#                     for page_num, page in enumerate(pdf.pages, 1):
#                         # Extract tables
#                         tables = page.extract_tables()
#                         if tables:
#                             for table_idx, table in enumerate(tables):
#                                 all_tables.append({
#                                     'page': page_num,
#                                     'table_index': table_idx,
#                                     'data': table,
#                                     'rows': len(table),
#                                     'cols': len(table[0]) if table else 0
#                                 })
#
#                         # Extract images
#                         images = page.images
#                         if images:
#                             images_list.extend([
#                                 {
#                                     'page': page_num,
#                                     'x0': img['x0'],
#                                     'y0': img['y0'],
#                                     'width': img['width'],
#                                     'height': img['height']
#                                 }
#                                 for img in images
#                             ])
#
#                     result['tables'] = all_tables
#                     result['images'] = images_list
#                     result['metadata']['tables_found'] = len(all_tables)
#                     result['metadata']['images_found'] = len(images_list)
#
#                     logger.info(f"✅ pdfplumber found {len(all_tables)} tables and {len(images_list)} images")
#             except Exception as e:
#                 logger.warning(f"⚠️ pdfplumber failed: {e}")
#
#             # --- Step 3: Optional OCR fallback if text is empty ---
#             if not result['raw_text'] or len(result['raw_text'].strip()) < 50:
#                 try:
#                     import pytesseract
#                     from pdf2image import convert_from_path
#                     pages = convert_from_path(file_path)
#                     ocr_text = []
#                     for i, page_img in enumerate(pages):
#                         ocr_page = pytesseract.image_to_string(page_img)
#                         ocr_text.append(ocr_page)
#                     result['raw_text'] = "\n\n".join(ocr_text)
#                     result['parser_used'] += " + OCR"
#                     logger.info(f"✅ OCR extracted {len(result['raw_text'])} characters")
#                 except Exception as e:
#                     logger.warning(f"⚠️ OCR failed: {e}")
#
#             return result
#
#         except Exception as e:
#             logger.error(f"❌ PDF parsing failed: {e}")
#             return {
#                 'raw_text': '',
#                 'error': str(e),
#                 'parser_used': 'hybrid',
#                 'confidence': 0.0
#             }
