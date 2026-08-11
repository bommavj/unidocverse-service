"""
Universal image parser with OCR support
Handles: JPG, PNG, GIF, BMP, TIFF, WEBP, HEIC
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from PIL import Image
import pytesseract
import os
import io

logger = logging.getLogger(__name__)

# # ⭐ Load Tesseract paths from Electron (main.js)
# TESSERACT_BINARY = os.getenv("TESSERACT_BINARY")
# TESSDATA_PREFIX = os.getenv("TESSDATA_PREFIX")
# if TESSDATA_PREFIX:
#     os.environ["TESSDATA_PREFIX"] = TESSDATA_PREFIX
#
# if TESSERACT_BINARY:
#     pytesseract.pytesseract.tesseract_cmd = TESSERACT_BINARY
#     OCR_AVAILABLE = True
# else:
#     OCR_AVAILABLE = False

# ✅ HEIF/HEIC Support - Register BEFORE any Image.open() calls
HEIF_SUPPORT = False
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
    logger.info("✅ HEIF/HEIC support enabled")
except ImportError as e:
    logger.warning(f"⚠️ HEIF/HEIC support not available: {e}")
except Exception as e:
    logger.error(f"❌ Error registering HEIF support: {e}")


class ImageParser:
    """Parse images and extract text using OCR"""

    SUPPORTED_FORMATS = {
        '.jpg', '.jpeg', '.png', '.gif', '.bmp',
        '.tiff', '.tif', '.webp', '.heic', '.heif'
    }

    def __init__(self):
        self.tesseract_available = self._check_tesseract()

    def _check_tesseract(self) -> bool:
        """Check if Tesseract OCR is available"""
        try:
            pytesseract.get_tesseract_version()
            logger.info("✅ Tesseract OCR available")
            return True
        except Exception as e:
            logger.warning(f"⚠️ Tesseract OCR not available: {e}")
            return False

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the file"""
        ext = Path(filename).suffix.lower()
        return ext in self.SUPPORTED_FORMATS

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """
        Parse image and extract text using OCR

        Returns:
            {
                'text': str,
                'metadata': dict,
                'images': list,
                'confidence': float
            }
        """

        logger.info(f"🖼️ Parsing image: {filename}")

        try:
            # ✅ HEIC/HEIF: Try to open with pillow-heif first
            ext = Path(filename).suffix.lower()
            image = None

            if ext in ['.heic', '.heif']:
                if not HEIF_SUPPORT:
                    raise Exception("HEIF/HEIC support not available. Install pillow-heif: pip install pillow-heif")

                logger.info("📱 Opening HEIC/HEIF file...")
                try:
                    # Read HEIF file and convert to PIL Image
                    heif_file = pillow_heif.read_heif(file_path)
                    image = Image.frombytes(
                        heif_file.mode,
                        heif_file.size,
                        heif_file.data,
                        "raw"
                    )
                    logger.info(f"✅ HEIC/HEIF opened: {image.size} {image.mode}")
                except Exception as heif_error:
                    logger.error(f"❌ HEIF error: {heif_error}")
                    raise
            else:
                # Open regular image formats
                image = Image.open(file_path)
                logger.info(f"✅ Image opened: {image.size} {image.mode}")

            # Extract metadata
            metadata = self._extract_metadata(image, filename)

            # Extract text using OCR
            text = ""
            ocr_confidence = 0.0

            if self.tesseract_available:
                text, ocr_confidence = self._extract_text_ocr(image)
            else:
                logger.warning("⚠️ OCR not available, no text extracted")

            # Generate thumbnail
            thumbnail_data = self._generate_thumbnail(image)

            result = {
                'text': text,
                'metadata': {
                    **metadata,
                    'ocr_confidence': ocr_confidence,
                    'ocr_enabled': self.tesseract_available,
                    'has_text': len(text) > 0,
                    'is_heif': ext in ['.heic', '.heif']
                },
                'images': [{
                    'type': 'original',
                    'width': image.width,
                    'height': image.height,
                    'format': image.format or ext.upper().lstrip('.'),
                    'mode': image.mode
                }],
                'thumbnail': thumbnail_data,
                'confidence': 0.9 if len(text) > 100 else 0.5
            }

            logger.info(f"✅ Image parsed: {len(text)} chars extracted")
            return result

        except Exception as e:
            logger.error(f"❌ Error parsing image: {e}")
            import traceback
            logger.error(traceback.format_exc())

            return {
                'text': '',
                'metadata': {
                    'error': str(e),
                    'filename': filename,
                    'error_type': type(e).__name__
                },
                'images': [],
                'confidence': 0.0
            }

    def _extract_metadata(self, image: Image.Image, filename: str) -> Dict[str, Any]:
        """Extract image metadata"""

        metadata = {
            'width': image.width,
            'height': image.height,
            'format': image.format or Path(filename).suffix.lstrip('.').upper(),
            'mode': image.mode,
            'size_bytes': 0
        }

        # Get EXIF data if available
        try:
            exif_data = image.getexif()
            if exif_data:
                # Common EXIF tags
                from PIL.ExifTags import TAGS
                exif_dict = {}
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    # Convert bytes to string if needed
                    if isinstance(value, bytes):
                        try:
                            value = value.decode('utf-8')
                        except:
                            value = str(value)
                    exif_dict[tag_name] = value

                metadata['exif'] = {
                    'DateTime': exif_dict.get('DateTime'),
                    'Make': exif_dict.get('Make'),
                    'Model': exif_dict.get('Model'),
                    'Orientation': exif_dict.get('Orientation')
                }
        except Exception as e:
            logger.debug(f"No EXIF data available: {e}")

        return metadata

    def _extract_text_ocr(self, image: Image.Image) -> tuple[str, float]:
        """Extract text from image using OCR"""

        try:
            # Convert to RGB if necessary
            if image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')

            # Run OCR
            ocr_data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                lang='eng'
            )

            # Extract text and confidence
            text_parts = []
            confidences = []

            for i, text in enumerate(ocr_data['text']):
                if text.strip():
                    text_parts.append(text)
                    conf = ocr_data['conf'][i]
                    if conf != -1:  # -1 means no confidence
                        confidences.append(conf)

            full_text = ' '.join(text_parts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            logger.info(f"📝 OCR extracted {len(full_text)} chars (confidence: {avg_confidence:.1f}%)")

            return full_text, avg_confidence / 100.0

        except Exception as e:
            logger.error(f"❌ OCR error: {e}")
            return "", 0.0

    def _generate_thumbnail(self, image: Image.Image, size: tuple = (200, 200)) -> Optional[bytes]:
        """Generate thumbnail"""

        try:
            # Create thumbnail
            thumb = image.copy()
            thumb.thumbnail(size, Image.Resampling.LANCZOS)

            # Save to bytes
            buffer = io.BytesIO()
            thumb.save(buffer, format='PNG')

            return buffer.getvalue()

        except Exception as e:
            logger.error(f"Error generating thumbnail: {e}")
            return None