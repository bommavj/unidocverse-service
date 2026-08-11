"""
Thumbnail Generation Service
"""

import logging
from pathlib import Path
from typing import Optional
import os

logger = logging.getLogger(__name__)


class ThumbnailService:
    """Generate thumbnails for documents"""

    THUMBNAIL_SIZE = (200, 280)  # Smaller, more elegant size (was 300x400)
    THUMBNAIL_DIR = "storage/thumbnails"

    @staticmethod
    def generate_thumbnail(file_path: str, doc_id: str) -> Optional[str]:
        """
        Generate thumbnail for document

        Returns:
            Path to thumbnail file or None
        """
        try:
            # Ensure thumbnail directory exists
            os.makedirs(ThumbnailService.THUMBNAIL_DIR, exist_ok=True)

            file_ext = Path(file_path).suffix.lower()
            thumbnail_path = f"{ThumbnailService.THUMBNAIL_DIR}/{doc_id}.png"

            logger.info(f"📸 Generating thumbnail for: {file_path} ({file_ext})")

            if file_ext == '.pdf':
                return ThumbnailService._generate_pdf_thumbnail(file_path, thumbnail_path)

            elif file_ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']:
                return ThumbnailService._generate_image_thumbnail(file_path, thumbnail_path)

            elif file_ext in ['.xlsx', '.xls', '.docx', '.doc', '.pptx', '.ppt']:
                return ThumbnailService._generate_office_thumbnail(file_ext, thumbnail_path)

            else:
                return ThumbnailService._generate_generic_thumbnail(file_ext, thumbnail_path)

        except Exception as e:
            logger.error(f"❌ Thumbnail generation failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    def _generate_pdf_thumbnail(file_path: str, output_path: str) -> Optional[str]:
        """Generate thumbnail from first page of PDF"""
        try:
            import pdfplumber
            from PIL import Image

            logger.info(f"📄 Generating PDF thumbnail...")

            with pdfplumber.open(file_path) as pdf:
                if len(pdf.pages) > 0:
                    page = pdf.pages[0]

                    # Convert to image
                    img = page.to_image(resolution=150)
                    pil_img = img.original

                    # Resize
                    pil_img.thumbnail(ThumbnailService.THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

                    # Save
                    pil_img.save(output_path, 'PNG')

                    logger.info(f"✅ PDF thumbnail saved: {output_path}")
                    return output_path

            return None

        except Exception as e:
            logger.error(f"❌ PDF thumbnail failed: {e}")
            return None

    @staticmethod
    def _generate_image_thumbnail(file_path: str, output_path: str) -> Optional[str]:
        """Generate thumbnail from image"""
        try:
            from PIL import Image

            logger.info(f"🖼️ Generating image thumbnail...")

            img = Image.open(file_path)
            img.thumbnail(ThumbnailService.THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            # Convert RGBA to RGB if necessary
            if img.mode == 'RGBA':
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[3])
                img = rgb_img

            img.save(output_path, 'PNG')

            logger.info(f"✅ Image thumbnail saved: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Image thumbnail failed: {e}")
            return None

    @staticmethod
    def _generate_office_thumbnail(file_ext: str, output_path: str) -> Optional[str]:
        """Generate icon for office documents"""
        try:
            from PIL import Image, ImageDraw, ImageFont

            logger.info(f"📊 Generating office thumbnail...")

            # Create blank image
            img = Image.new('RGB', ThumbnailService.THUMBNAIL_SIZE, color='white')
            draw = ImageDraw.Draw(img)

            # Colors for different file types
            colors = {
                '.xlsx': '#217346',
                '.xls': '#217346',
                '.docx': '#2B579A',
                '.doc': '#2B579A',
                '.pptx': '#D24726',
                '.ppt': '#D24726'
            }

            color = colors.get(file_ext, '#666666')

            # Draw colored rectangle
            margin = 50
            draw.rectangle(
                [(margin, margin),
                 (ThumbnailService.THUMBNAIL_SIZE[0] - margin, ThumbnailService.THUMBNAIL_SIZE[1] - margin)],
                fill=color,
                outline='#333',
                width=3
            )

            # Add text
            ext_text = file_ext.upper().lstrip('.')

            # Try to use a system font
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 60)
            except:
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
                except:
                    font = ImageFont.load_default()

            # Get text bounding box
            bbox = draw.textbbox((0, 0), ext_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # Center text
            x = (ThumbnailService.THUMBNAIL_SIZE[0] - text_width) // 2
            y = (ThumbnailService.THUMBNAIL_SIZE[1] - text_height) // 2

            draw.text((x, y), ext_text, fill='white', font=font)

            img.save(output_path, 'PNG')

            logger.info(f"✅ Office thumbnail saved: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Office thumbnail failed: {e}")
            return None

    @staticmethod
    def _generate_generic_thumbnail(file_ext: str, output_path: str) -> Optional[str]:
        """Generate generic document icon"""
        try:
            from PIL import Image, ImageDraw, ImageFont

            logger.info(f"📝 Generating generic thumbnail...")

            img = Image.new('RGB', ThumbnailService.THUMBNAIL_SIZE, color='#f5f5f5')
            draw = ImageDraw.Draw(img)

            # Draw document icon
            margin = 60
            draw.rectangle(
                [(margin, margin),
                 (ThumbnailService.THUMBNAIL_SIZE[0] - margin, ThumbnailService.THUMBNAIL_SIZE[1] - margin)],
                fill='white',
                outline='#999',
                width=3
            )

            # Draw lines (text representation)
            line_margin = margin + 30
            line_width = ThumbnailService.THUMBNAIL_SIZE[0] - 2 * margin - 60
            for i in range(5):
                y = margin + 60 + i * 40
                draw.rectangle(
                    [(line_margin, y), (line_margin + line_width, y + 10)],
                    fill='#ddd'
                )

            # Add file extension text
            ext_text = file_ext.upper().lstrip('.')

            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
            except:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), ext_text, font=font)
            text_width = bbox[2] - bbox[0]
            x = (ThumbnailService.THUMBNAIL_SIZE[0] - text_width) // 2
            y = ThumbnailService.THUMBNAIL_SIZE[1] - margin - 40

            draw.text((x, y), ext_text, fill='#666', font=font)

            img.save(output_path, 'PNG')

            logger.info(f"✅ Generic thumbnail saved: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Generic thumbnail failed: {e}")
            return None