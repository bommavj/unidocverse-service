"""
app/core/tesseract_config.py
Tesseract OCR configuration for UniDocVerse
"""

import os
import sys
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def configure_tesseract():
    """
    Configure Tesseract OCR from environment variables.
    Must be called at application startup before any OCR operations.
    """

    tesseract_binary = os.getenv('TESSERACT_BINARY')
    tessdata_prefix = os.getenv('TESSDATA_PREFIX')

    log.info("🔍 Configuring Tesseract OCR...")

    # 1. Configure pytesseract binary path
    if tesseract_binary:
        tesseract_path = Path(tesseract_binary)

        if tesseract_path.exists():
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

                log.info(f"✅ Tesseract binary: {tesseract_binary}")

                # Verify it works
                try:
                    version = pytesseract.get_tesseract_version()
                    log.info(f"   Version: {version}")
                except Exception as ve:
                    log.warning(f"   Could not get version: {ve}")

            except ImportError:
                log.warning("⚠️  pytesseract not installed")
            except Exception as e:
                log.error(f"❌ Failed to configure pytesseract: {e}")
        else:
            log.error(f"❌ Tesseract binary not found: {tesseract_binary}")
    else:
        log.warning("⚠️  TESSERACT_BINARY environment variable not set")
        log.warning("   Trying system tesseract...")

        # Try to find system tesseract
        try:
            import shutil
            system_tess = shutil.which('tesseract')
            if system_tess:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = system_tess
                log.info(f"✅ Using system tesseract: {system_tess}")
            else:
                log.error("❌ Tesseract not found in system PATH")
        except Exception as e:
            log.error(f"❌ Could not find system tesseract: {e}")

    # 2. Configure TESSDATA_PREFIX
    if tessdata_prefix:
        tessdata_path = Path(tessdata_prefix)

        if tessdata_path.exists():
            os.environ['TESSDATA_PREFIX'] = str(tessdata_path)
            log.info(f"✅ TESSDATA_PREFIX: {tessdata_prefix}")

            # Check for language files
            eng_file = tessdata_path / "eng.traineddata"
            if eng_file.exists():
                log.info("   ✓ English language data found")
            else:
                log.warning("   ⚠️  English language data not found")

            # List available languages
            try:
                lang_files = list(tessdata_path.glob("*.traineddata"))
                if lang_files:
                    langs = [f.stem for f in lang_files]
                    log.info(f"   Available languages: {', '.join(langs)}")
                else:
                    log.warning("   No language files found in tessdata")
            except Exception as e:
                log.warning(f"   Could not list language files: {e}")
        else:
            log.error(f"❌ TESSDATA_PREFIX not found: {tessdata_prefix}")
    else:
        log.warning("⚠️  TESSDATA_PREFIX environment variable not set")

    # 3. Final verification
    try:
        import pytesseract
        from PIL import Image
        import io

        # Create a tiny test image
        test_img = Image.new('RGB', (100, 30), color='white')

        # Try OCR
        pytesseract.image_to_string(test_img)
        log.info("✅ Tesseract OCR verification successful")

    except Exception as e:
        log.error(f"❌ Tesseract OCR verification failed: {e}")
        log.error("   OCR features will not be available")


def is_tesseract_available():
    """Check if Tesseract is properly configured and available"""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except:
        return False