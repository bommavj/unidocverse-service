# app/services/analyzers/image_analyzer.py

import logging
import os
import re
import json
from typing import Any, Dict, Optional, List
from PIL import Image

from app.core import config
from app.services.analyzers.base_analyzer import BaseAnalyzer
from app.core.vision_captioner import vision_captioner
import ollama

logger = logging.getLogger(__name__)
ollama_client = ollama.Client()

# Optional handwriting-capable OCR
try:
    from paddleocr import PaddleOCR
    _paddle_ocr = PaddleOCR(use_angle_cls=True, lang='en')
except Exception:
    _paddle_ocr = None


class ImageAnalyzer(BaseAnalyzer):
    """
    Universal image analyzer for all image types and content.

    Supports:
    - Photos (portraits, landscapes, objects, events)
    - Screenshots (applications, websites, messages)
    - Documents (scanned forms, receipts, letters, certificates)
    - Diagrams (flowcharts, org charts, technical drawings)
    - Charts/Graphs (bar charts, pie charts, line graphs)
    - Art/Design (logos, illustrations, designs)
    - Medical Images (X-rays, scans, reports, handwritten notes)
    - ID Documents (licenses, passports, cards)
    - Receipts/Invoices
    - And any other visual content

    Hybrid behavior:
    - Visual analysis: dimensions, format, colors, dominant features
    - Text extraction: OCR for any visible text (including handwriting when possible)
    - Vision captioning: natural-language description of the image
    - LLM semantic extraction: full-context structured fields + insights
    - Dashboard-ready: generates metrics, charts, and insights
    - Content classification: auto-detects what the image contains
    """

    def __init__(self):
        super().__init__()
        self.validations: List[Dict[str, Any]] = []
        self.fields: Dict[str, str] = {}
        self.image_metadata: Dict[str, Any] = {}

    # ------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------

    def _extract_text_with_ocr(self, file_path: str) -> str:
        """
        Extract text from image using multiple OCR engines.
        Includes handwriting-capable PaddleOCR when available.
        """
        # Method 0 — PaddleOCR (best for handwriting)
        if _paddle_ocr is not None:
            try:
                result = _paddle_ocr.ocr(file_path, cls=True)
                lines: List[str] = []
                for block in result:
                    for line in block:
                        text_part = line[1][0] if isinstance(line[1], (list, tuple)) else line[1]
                        if text_part:
                            lines.append(text_part)
                text = "\n".join(lines).strip()
                if text:
                    logger.info(f"🔍 PaddleOCR extracted {len(text)} chars from image")
                    return text
            except Exception as e:
                logger.debug(f"Method 0 (PaddleOCR) failed: {e}")

        # Method 1 — Use existing image parser
        try:
            from app.parsers.image_parser import parse_image
            result = parse_image(file_path)
            if result and "text" in result:
                return result["text"]
        except Exception as e:
            logger.debug(f"Method 1 (parse_image) failed: {e}")

        # Method 2 — universal OCR (auto-rotate, best engine)
        try:
            from app.services.ocr.ocr_utils import ocr_any
            text = ocr_any(file_path)
            if text:
                return text
        except Exception as e:
            logger.debug(f"Method 2 (ocr_any) failed: {e}")

        logger.warning("All OCR methods failed - no text extracted")
        return ""

    # ------------------------------------------------------------
    # VISION CAPTIONING
    # ------------------------------------------------------------

    def _generate_image_caption(self, file_path: str) -> str:
        """
        Generate a natural-language caption/description for the image
        using a local vision model (Moondream via VisionCaptioner).
        """
        try:
            caption = vision_captioner.caption_image(file_path)
            caption = (caption or "").strip()
            if caption:
                logger.info(f"🖼️ Vision caption generated: {caption[:120]}...")
            else:
                logger.info("🖼️ Vision captioner returned empty caption")
            return caption
        except Exception as e:
            logger.warning(f"⚠️ Vision captioning failed: {e}")
            return ""

    # ------------------------------------------------------------
    # SAFE JSON PARSER
    # ------------------------------------------------------------

    def _safe_json(self, raw: Any) -> Dict[str, Any]:
        """
        Safely parse JSON from an LLM response.
        Accepts either a dict or a JSON string.
        """
        if isinstance(raw, dict):
            return raw

        if not isinstance(raw, str):
            logger.warning("LLM response is not a string or dict; returning empty dict")
            return {}

        raw = raw.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw, flags=re.IGNORECASE).strip()
            raw = re.sub(r"```$", "", raw).strip()

        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Failed to parse LLM JSON response: {e}")
            return {}

    # ------------------------------------------------------------
    # LLM SEMANTIC ANALYSIS (phi3mini)
    # ------------------------------------------------------------

    def _llm_fallback_analysis(
        self,
        ocr_text: str,
        vision_caption: str,
        metadata: Dict[str, Any],
        current_fields: Dict[str, Any],
        current_summary: str,
        current_content_type: str,
    ) -> Dict[str, Any]:
        """
        Call Ollama (phi3mini via config.model) to semantically analyze the image
        and extract as much structured context as possible for ANY image type.
        """

        prompt = f"""
Return ONLY valid JSON. Do not include markdown, comments, or any text outside the JSON.

You are an expert universal image and document analyst.

You are given:
1) Image metadata (technical details)
2) OCR text extracted from the image (may be empty, noisy, or handwritten)
3) A vision-generated caption describing the visual content (may be generic)
4) A preliminary rule-based classification and summary
5) Some pre-extracted fields

Your job:
- Refine the content_type (high-level category of the image), such as:
  - Photo, Screenshot, Scanned Document, Medical Note, Medical Image, Receipt/Invoice,
    ID Document, Business Card, Chart/Diagram, Presentation Slide, Logo/Design, Meme/Social Media, etc.
- Improve the summary (1–3 concise sentences, dashboard-ready, non-redundant).
- Extract as many meaningful structured fields as possible from the OCR text and caption.

For medical notes (typed or handwritten), try to extract when present:
- patient_name, patient_age, patient_sex
- date
- chief_complaint, symptom_duration
- exam_findings
- anatomical_location, mass_size, mass_characteristics
- recommended_tests, risk_estimates, diagnosis_suspected, plan
- physician_name, hospital, license_number

For receipts/invoices:
- merchant, date, total, subtotal, tax, currency, items, payment_method

For IDs:
- name, id_number, birth_date, expiry_date, issuing_country, document_type

For business/official documents:
- parties, subject, key_dates, amounts, decisions, obligations, signatures, organizations, locations

For screenshots/web/app UIs:
- application_name, page_title, main_action, key_entities, error_messages, status

For photos with little or no text:
- scene_type, main_subjects, location_hint, time_of_day_hint, mood

Rules:
- Keep all extracted fields as simple key-value pairs (strings).
- Merge and improve upon the provided "Current fields" when useful.
- Do NOT invent obviously false details; if unsure, omit the field.
- Suggest insights (short cards) about the image and its content.
- Suggest alerts if anything looks problematic, risky, or low quality.
- Estimate an overall confidence score between 0 and 1.

Return ONLY a valid JSON object with the following keys:
- content_type: string
- summary: string
- fields: object (dict of key -> value, strings). Include both your new fields and any useful ones from "Current fields".
- insights: array of objects with keys: type, title, description, color, icon
- alerts: array of objects with keys: severity, title, message, field (optional)
- confidence: number between 0 and 1

Image metadata:
{json.dumps(metadata, indent=2)}

OCR text:
{ocr_text or "[NO TEXT EXTRACTED]"}

Vision caption:
{vision_caption or "[NO CAPTION AVAILABLE]"}

Current fields:
{json.dumps(current_fields, indent=2)}

Current summary:
{current_summary}

Current content_type:
{current_content_type}
"""

        logger.info("🧠 ImageAnalyzer: Calling Ollama LLM fallback (phi3mini) for enriched image analysis")

        try:
            response = ollama_client.generate(
                model=config.model,  # e.g., "phi3:mini"
                prompt=prompt,
            )
        except Exception as e:
            logger.warning(f"LLM fallback call failed: {e}")
            return {}

        raw = response.response if hasattr(response, "response") else response
        parsed = self._safe_json(raw)
        return parsed or {}

    # ------------------------------------------------------------
    # MAIN ANALYZE
    # ------------------------------------------------------------

    def analyze(
        self,
        file_path: str,
        text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        parsed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info(f"📄 ImageAnalyzer: {file_path}")
        metadata = metadata or {}

        # 1) Extract image metadata and visual properties
        self.image_metadata = self._extract_image_metadata(file_path)

        # 2) Vision captioning (semantic description of the image)
        vision_caption = self._generate_image_caption(file_path)
        if vision_caption:
            self.fields["vision_caption"] = vision_caption

        # 3) Get text from image (OCR)
        #full_text = (text or "").strip()

        full_text = self._extract_text_with_ocr(file_path).strip()

        if not full_text:
            try:
                full_text = self._extract_text_with_ocr(file_path).strip()
                if full_text:
                    logger.info(f"🔍 OCR extracted {len(full_text)} chars from image")
            except Exception as e:
                logger.warning(f"⚠️ OCR failed for image: {e}")

        # 4) Classify image content type
        content_type = self._classify_image_content(full_text, self.image_metadata, file_path)
        self.fields["content_type"] = content_type

        # 5) Extract structured fields based on content type (regex-based)
        self.fields.update(self._extract_content_fields(full_text, content_type))

        # 6) Build universal summary (pre-LLM)
        summary = self._build_summary(self.fields, self.image_metadata)

        # 7) Run validations
        self.validations = self._run_validations(self.fields, self.image_metadata)
        alerts = self._build_alerts(self.validations)

        # 8) Confidence heuristic
        confidence = self._estimate_confidence(self.fields, self.image_metadata, self.validations)

        # 9) Generate insights (pre-LLM)
        insights = self._generate_insights(self.fields, self.image_metadata, self.validations)

        # ------------------------------------------------------------
        # 10) LLM FALLBACK / ENRICHMENT (phi3mini) FOR ANY IMAGE
        # ------------------------------------------------------------

        text_length = len(full_text)
        needs_llm = True  # we want semantic enrichment for all images

        if needs_llm:
            logger.info("⚠️ ImageAnalyzer: Falling back to LLM analysis with OCR + vision caption")
            llm_result = self._llm_fallback_analysis(
                ocr_text=full_text,
                vision_caption=vision_caption,
                metadata=self.image_metadata,
                current_fields=self.fields,
                current_summary=summary,
                current_content_type=content_type,
            )

            if llm_result:
                # Merge / override with LLM-enhanced results
                new_content_type = llm_result.get("content_type")
                if isinstance(new_content_type, str) and new_content_type.strip():
                    content_type = new_content_type.strip()
                    self.fields["content_type"] = content_type

                new_summary = llm_result.get("summary")
                if isinstance(new_summary, str) and new_summary.strip():
                    summary = new_summary.strip()

                llm_fields = llm_result.get("fields") or {}
                if isinstance(llm_fields, dict):
                    for k, v in llm_fields.items():
                        self.fields[str(k)] = str(v)

                llm_insights = llm_result.get("insights") or []
                if isinstance(llm_insights, list):
                    insights.extend(llm_insights)

                llm_alerts = llm_result.get("alerts") or []
                if isinstance(llm_alerts, list):
                    alerts.extend(llm_alerts)

                llm_conf = llm_result.get("confidence")
                if isinstance(llm_conf, (int, float)):
                    confidence = max(0.0, min(1.0, (confidence + float(llm_conf)) / 2))

        rawtext = " ".join((full_text, summary, vision_caption))

        # 11) Build result object
        result: Dict[str, Any] = {
            "type": "image",
            "subtype": content_type.lower().replace(" ", "_"),
            "summary": summary,
            "confidence": confidence,
            "fields": self.fields,
            "image_metadata": self.image_metadata,
            "raw_text": rawtext,
            "cleaned_text": rawtext,
            "validations": self.validations,
            "alerts": alerts,
            "content_type": content_type,
            "insights": insights,
            "has_advanced_analytics": True,
            "ui_config": self._build_ui_config(self.fields, self.image_metadata, self.validations, insights),
        }

        return result

    # ------------------------------------------------------------
    # IMAGE METADATA EXTRACTION
    # ------------------------------------------------------------

    def _extract_image_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Extract technical metadata from the image file.
        """
        metadata = {
            "format": "unknown",
            "width": 0,
            "height": 0,
            "mode": "unknown",
            "size_bytes": 0,
            "megapixels": 0.0,
            "aspect_ratio": "unknown",
            "orientation": "unknown",
            "has_transparency": False,
            "color_depth": "unknown",
        }

        try:
            # Get file size
            metadata["size_bytes"] = os.path.getsize(file_path)

            # Open and analyze image
            with Image.open(file_path) as img:
                metadata["format"] = img.format or "unknown"
                metadata["width"] = img.width
                metadata["height"] = img.height
                metadata["mode"] = img.mode

                # Calculate megapixels
                metadata["megapixels"] = round((img.width * img.height) / 1_000_000, 2)

                # Determine aspect ratio
                if img.width > 0 and img.height > 0:
                    ratio = img.width / img.height
                    if 0.9 <= ratio <= 1.1:
                        metadata["aspect_ratio"] = "Square (1:1)"
                    elif 1.3 <= ratio <= 1.4:
                        metadata["aspect_ratio"] = "4:3"
                    elif 1.7 <= ratio <= 1.8:
                        metadata["aspect_ratio"] = "16:9"
                    elif ratio > 1.8:
                        metadata["aspect_ratio"] = "Panoramic"
                    elif ratio < 0.9:
                        metadata["aspect_ratio"] = "Portrait"
                    else:
                        metadata["aspect_ratio"] = f"{ratio:.2f}:1"

                # Determine orientation
                if img.width > img.height:
                    metadata["orientation"] = "Landscape"
                elif img.height > img.width:
                    metadata["orientation"] = "Portrait"
                else:
                    metadata["orientation"] = "Square"

                # Check for transparency
                metadata["has_transparency"] = img.mode in ("RGBA", "LA", "P")

                # Color depth
                if img.mode == "1":
                    metadata["color_depth"] = "1-bit (B&W)"
                elif img.mode == "L":
                    metadata["color_depth"] = "8-bit Grayscale"
                elif img.mode == "RGB":
                    metadata["color_depth"] = "24-bit RGB"
                elif img.mode == "RGBA":
                    metadata["color_depth"] = "32-bit RGBA"
                else:
                    metadata["color_depth"] = img.mode

                logger.info(f"✓ Image metadata: {metadata['width']}x{metadata['height']} {metadata['format']}")

        except Exception as e:
            logger.warning(f"⚠️ Failed to extract image metadata: {e}")

        return metadata

    # ------------------------------------------------------------
    # CONTENT CLASSIFICATION
    # ------------------------------------------------------------

    def _classify_image_content(self, text: str, metadata: Dict[str, Any], file_path: str) -> str:
        """
        Classify what type of content the image contains.
        """
        text_lower = text.lower()
        filename_lower = os.path.basename(file_path).lower()

        # Screenshot detection
        if any(k in filename_lower for k in ["screenshot", "screen_shot", "screen", "capture"]):
            return "Screenshot"

        if any(k in text_lower for k in ["screenshot", "screen capture", "snipping tool"]):
            return "Screenshot"

        # Document/Form detection
        if any(k in text_lower for k in ["form", "application", "agreement", "contract", "invoice",
                                         "receipt", "statement", "certificate", "license"]):
            return "Scanned Document"

        # ID/Card detection
        if any(k in text_lower for k in ["driver license", "driver's license", "passport", "id card",
                                         "identification", "social security"]):
            return "ID Document"

        # Receipt/Invoice detection
        if any(k in text_lower for k in ["total:", "subtotal:", "tax:", "amount due", "receipt #",
                                         "invoice #", "order #", "purchased"]):
            return "Receipt/Invoice"

        # Medical image / note detection
        if any(k in text_lower for k in ["patient", "hospital", "medical record", "md.", "m.d.", "clinic",
                                         "diagnosis", "chief complaint", "history of present illness",
                                         "physical exam", "impression", "plan"]):
            return "Medical Note"

        # Chart/Graph detection
        if any(k in text_lower for k in ["chart", "graph", "diagram", "figure", "axis", "legend",
                                         "bar chart", "pie chart", "line graph"]):
            return "Chart/Diagram"

        # Business card detection
        if any(k in text_lower for k in ["tel:", "email:", "phone:", "mobile:", "fax:"]):
            if len(text) < 500:
                return "Business Card"

        # Certificate detection
        if any(k in text_lower for k in ["certificate", "certification", "hereby certify",
                                         "awarded to", "in recognition"]):
            return "Certificate"

        # Presentation slide detection
        if any(k in text_lower for k in ["slide", "presentation", "agenda", "objectives"]):
            return "Presentation Slide"

        # Photo detection (minimal or no text)
        if len(text.strip()) < 50:
            aspect = metadata.get("aspect_ratio", "")
            if any(x in aspect for x in ["4:3", "16:9", "3:2", "Panoramic", "Portrait"]):
                return "Photo"

        # Logo/Design detection
        if metadata.get("has_transparency") and metadata.get("megapixels", 0) < 1.0:
            return "Logo/Design"

        # Meme/Social Media detection
        if any(k in filename_lower for k in ["meme", "funny", "lol", "viral"]):
            return "Meme/Social Media"

        # Default classifications based on content length
        if len(text.strip()) > 100:
            return "Document Image"
        elif len(text.strip()) > 0:
            return "Image with Text"
        else:
            return "Photo/Visual"

    # ------------------------------------------------------------
    # FIELD EXTRACTION (regex-based baseline)
    # ------------------------------------------------------------

    def _extract_content_fields(self, text: str, content_type: str) -> Dict[str, str]:
        """
        Extract structured fields based on content type using regex/statistics.
        LLM will later enrich and refine these.
        """
        fields: Dict[str, str] = {}

        # Text statistics
        fields["text_length"] = str(len(text))
        fields["word_count"] = str(len(text.split())) if text else "0"

        # Dates (flexible)
        date_patterns = [
            r'\b\d{1,2}\s*[-/\.]\s*\d{1,2}\s*[-/\.]\s*\d{2,4}\b',  # 12-6-2014, 12 / 6 / 14
            r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{2,4}\b',
            r'\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*,?\s+\d{2,4}\b',
        ]
        dates: List[str] = []
        for pattern in date_patterns:
            dates.extend(re.findall(pattern, text, flags=re.IGNORECASE))
        if dates:
            fields["date"] = dates[0]

        # Email addresses
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, text)
        if emails:
            fields["email"] = emails[0]

        # Phone numbers
        phone_pattern = r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
        phones = re.findall(phone_pattern, text)
        if phones:
            fields["phone"] = phones[0]

        # URLs
        url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        urls = re.findall(url_pattern, text)
        if urls:
            fields["url"] = urls[0]

        # Money amounts
        money_pattern = r'\$\s*[\d,]+\.?\d*'
        amounts = re.findall(money_pattern, text)
        if amounts:
            fields["amount"] = amounts[0]

        # Names (all caps words, likely names)
        name_pattern = r'\b[A-Z][A-Z\s]{2,}\b'
        names = re.findall(name_pattern, text)
        if names and len(names[0]) < 50:
            fields["name"] = names[0].strip()

        # Content-specific extraction
        if content_type == "Receipt/Invoice":
            total_pattern = r'total[:\s]+\$?\s*([\d,]+\.?\d*)'
            total_match = re.search(total_pattern, text, re.IGNORECASE)
            if total_match:
                fields["total"] = f"${total_match.group(1)}"

        return fields

    # ------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------

    def _build_summary(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> str:
        """
        Build a human-readable summary of the image.
        """
        content_type = fields.get("content_type", "Image")
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)
        format_type = metadata.get("format", "unknown")
        megapixels = metadata.get("megapixels", 0)

        summary_parts = [f"{content_type}"]

        # Add dimensions
        if width and height:
            summary_parts.append(f"{width}×{height}px")

        # Add megapixels if significant
        if megapixels >= 1.0:
            summary_parts.append(f"({megapixels}MP)")

        # Add format
        if format_type != "unknown":
            summary_parts.append(f"{format_type}")

        # Add text info if present
        text_length = int(fields.get("text_length", "0"))
        if text_length > 0:
            summary_parts.append(f"with {text_length} chars of text")

        # Add specific details based on content
        if fields.get("date"):
            summary_parts.append(f"dated {fields['date']}")

        if fields.get("amount"):
            summary_parts.append(f"amount: {fields['amount']}")

        return " • ".join(summary_parts)

    # ------------------------------------------------------------
    # VALIDATIONS
    # ------------------------------------------------------------

    def _run_validations(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Run quality checks on the image.
        """
        validations: List[Dict[str, Any]] = []

        def add(code: str, level: str, message: str, field: Optional[str] = None):
            validations.append({
                "code": code,
                "level": level,
                "message": message,
                "field": field,
            })

        # Image quality checks
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)

        # Resolution check
        if width < 800 or height < 600:
            add(
                code="low_resolution",
                level="warning",
                message=f"Image resolution ({width}×{height}) is relatively low. Text may be harder to read.",
                field="resolution",
            )

        # Very high resolution check
        if width > 10000 or height > 10000:
            add(
                code="very_high_resolution",
                level="info",
                message=f"Very high resolution image ({width}×{height}). Consider optimizing for faster loading.",
                field="resolution",
            )

        # File size check
        size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
        if size_mb > 10:
            add(
                code="large_file_size",
                level="warning",
                message=f"Large file size ({size_mb:.1f}MB). Consider compressing the image.",
                field="file_size",
            )

        # OCR text check
        text_length = int(fields.get("text_length", "0"))
        content_type = fields.get("content_type", "")

        if any(x in content_type for x in ["Document", "Receipt", "Certificate", "Medical"]):
            if text_length == 0:
                add(
                    code="no_text_extracted",
                    level="error",
                    message="No text could be extracted from this document image. Image quality may be too low.",
                    field="text",
                )
            elif text_length < 20:
                add(
                    code="minimal_text",
                    level="warning",
                    message=f"Only {text_length} characters extracted. OCR quality may be poor.",
                    field="text",
                )

        # Format check
        image_format = metadata.get("format", "unknown")
        if image_format in ["BMP", "TIFF"]:
            add(
                code="unoptimized_format",
                level="info",
                message=f"{image_format} format detected. Consider converting to JPEG or PNG for better compatibility.",
                field="format",
            )

        return validations

    def _build_alerts(self, validations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert validations into UI alerts.
        """
        alerts: List[Dict[str, Any]] = []

        for v in validations:
            if v["level"] == "error":
                alerts.append({
                    "severity": "high",
                    "title": "Critical Issue",
                    "message": v["message"],
                    "field": v.get("field"),
                })
            elif v["level"] == "warning":
                alerts.append({
                    "severity": "medium",
                    "title": "Quality Warning",
                    "message": v["message"],
                    "field": v.get("field"),
                })
            elif v["level"] == "info":
                alerts.append({
                    "severity": "low",
                    "title": "Information",
                    "message": v["message"],
                    "field": v.get("field"),
                })

        return alerts

    def _estimate_confidence(
        self,
        fields: Dict[str, str],
        metadata: Dict[str, Any],
        validations: List[Dict[str, Any]],
    ) -> float:
        """
        Estimate confidence in the analysis.
        """
        base = 0.9

        # Reduce confidence for quality issues
        for v in validations:
            if v["level"] == "error":
                base -= 0.3
            elif v["level"] == "warning":
                base -= 0.1

        # Increase confidence if we extracted useful fields
        if len(fields) > 5:
            base += 0.05

        return max(0.0, min(1.0, base))

    # ------------------------------------------------------------
    # INSIGHTS
    # ------------------------------------------------------------

    def _generate_insights(
        self,
        fields: Dict[str, str],
        metadata: Dict[str, Any],
        validations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate insights about the image.
        """
        insights: List[Dict[str, Any]] = []

        # Content type insight
        content_type = fields.get("content_type", "Unknown")
        insights.append({
            "type": "info",
            "title": f"Detected: {content_type}",
            "description": "Image content automatically classified",
            "color": "#4A90E2",
            "icon": "🖼️"
        })

        # Resolution insight
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)
        megapixels = metadata.get("megapixels", 0)

        if megapixels >= 5.0:
            insights.append({
                "type": "success",
                "title": "High Quality Image",
                "description": f"{width}×{height}px ({megapixels}MP) - Excellent resolution",
                "color": "#00C851",
                "icon": "✨"
            })
        elif megapixels >= 2.0:
            insights.append({
                "type": "info",
                "title": "Good Quality Image",
                "description": f"{width}×{height}px ({megapixels}MP) - Good resolution",
                "color": "#4A90E2",
                "icon": "📸"
            })

        # Text extraction insight
        text_length = int(fields.get("text_length", "0"))
        if text_length > 0:
            insights.append({
                "type": "success",
                "title": "Text Extracted",
                "description": f"Successfully extracted {text_length} characters via OCR",
                "color": "#00C851",
                "icon": "📝"
            })

        # File size insight
        size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
        if size_mb > 10:
            insights.append({
                "type": "warning",
                "title": "Large File",
                "description": f"File size is {size_mb:.1f}MB. Consider compressing for faster loading.",
                "color": "#FF8800",
                "icon": "📦"
            })

        return insights

    # ------------------------------------------------------------
    # UI CONFIG
    # ------------------------------------------------------------

    def _build_ui_config(
        self,
        fields: Dict[str, str],
        metadata: Dict[str, Any],
        validations: List[Dict[str, Any]],
        insights: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Build a simple UI configuration object for dashboards.
        """
        return {
            "layout": "image_detail",
            "sections": [
                {
                    "id": "summary",
                    "title": "Summary",
                    "type": "text",
                    "fields": ["summary", "content_type", "confidence"],
                },
                {
                    "id": "image_metadata",
                    "title": "Image Metadata",
                    "type": "key_value",
                    "data": metadata,
                },
                {
                    "id": "extracted_fields",
                    "title": "Extracted Fields",
                    "type": "key_value",
                    "data": fields,
                },
                {
                    "id": "insights",
                    "title": "Insights",
                    "type": "cards",
                    "data": insights,
                },
                {
                    "id": "alerts",
                    "title": "Alerts",
                    "type": "alerts",
                    "data": validations,
                },
            ],
        }


# # app/services/analyzers/image_analyzer.py
#
# import logging
# import os
# import re
# import json
# from typing import Any, Dict, Optional, List
# from PIL import Image
#
# from app.core import config
# from app.services.analyzers.base_analyzer import BaseAnalyzer
# import ollama
#
# logger = logging.getLogger(__name__)
# ollama_client = ollama.Client()
#
# class ImageAnalyzer(BaseAnalyzer):
#     """
#     Universal image analyzer for all image types and content.
#
#     Supports:
#     - Photos (portraits, landscapes, objects, events)
#     - Screenshots (applications, websites, messages)
#     - Documents (scanned forms, receipts, letters, certificates)
#     - Diagrams (flowcharts, org charts, technical drawings)
#     - Charts/Graphs (bar charts, pie charts, line graphs)
#     - Art/Design (logos, illustrations, designs)
#     - Medical Images (X-rays, scans, reports)
#     - ID Documents (licenses, passports, cards)
#     - Receipts/Invoices
#     - And any other visual content
#
#     Hybrid behavior:
#     - Visual analysis: dimensions, format, colors, dominant features
#     - Text extraction: OCR for any visible text
#     - Vision captioning: generate a natural-language description of the image
#     - LLM fallback: semantic summary, fields, insights when text is weak or missing
#     - Dashboard-ready: generates metrics, charts, and insights
#     - Content classification: auto-detects what the image contains
#     """
#
#     def __init__(self):
#         super().__init__()
#         self.validations: List[Dict[str, Any]] = []
#         self.fields: Dict[str, str] = {}
#         self.image_metadata: Dict[str, Any] = {}
#
#     # ------------------------------------------------------------
#     # OCR
#     # ------------------------------------------------------------
#
#     def _extract_text_with_ocr(self, file_path: str) -> str:
#         """
#         Extract text from image using available OCR methods.
#         Tries multiple approaches to find working OCR.
#         """
#         # Try method 1: Use existing image parser
#         try:
#             from app.parsers.image_parser import parse_image
#             result = parse_image(file_path)
#             if result and "text" in result:
#                 return result["text"]
#         except Exception as e:
#             logger.debug(f"Method 1 (parse_image) failed: {e}")
#
#         # Try method 2: Direct pytesseract
#         try:
#             import pytesseract
#             from PIL import Image as PILImage
#             img = PILImage.open(file_path)
#             text = pytesseract.image_to_string(img)
#             return text
#         except Exception as e:
#             logger.debug(f"Method 2 (pytesseract) failed: {e}")
#
#         # Try method 3: Use pdf OCR function (works for images too)
#         try:
#             from app.parsers.pdf_table_extractor import ocr_pdf_to_text
#             text = ocr_pdf_to_text(file_path)
#             return text
#         except Exception as e:
#             logger.debug(f"Method 3 (ocr_pdf_to_text) failed: {e}")
#
#         logger.warning("All OCR methods failed - no text extracted")
#         return ""
#
#     # ------------------------------------------------------------
#     # VISION CAPTIONING
#     # ------------------------------------------------------------
#
#     def _generate_image_caption(self, file_path: str) -> str:
#         """
#         Generate a natural-language caption/description for the image
#         using a local vision model (e.g., BLIP / LLaVA wrapper).
#
#         This does NOT replace OCR; it complements it for photos and
#         images with little or no text.
#         """
#         try:
#             # You can implement this wrapper however you like.
#             # Expected interface:
#             #   from app.core.vision_captioner import vision_captioner
#             #   caption = vision_captioner.caption_image(file_path)
#             from app.core.vision_captioner import vision_captioner
#
#             caption = vision_captioner.caption_image(file_path)
#             caption = (caption or "").strip()
#             if caption:
#                 logger.info(f"🖼️ Vision caption generated: {caption[:120]}...")
#             else:
#                 logger.info("🖼️ Vision captioner returned empty caption")
#             return caption
#         except Exception as e:
#             logger.warning(f"⚠️ Vision captioning failed: {e}")
#             return ""
#
#     # ------------------------------------------------------------
#     # SAFE JSON PARSER
#     # ------------------------------------------------------------
#
#     def _safe_json(self, raw: Any) -> Dict[str, Any]:
#         """
#         Safely parse JSON from an LLM response.
#         Accepts either a dict or a JSON string.
#         """
#         if isinstance(raw, dict):
#             return raw
#
#         if not isinstance(raw, str):
#             logger.warning("LLM response is not a string or dict; returning empty dict")
#             return {}
#
#         raw = raw.strip()
#
#         # Strip markdown fences if present
#         if raw.startswith("```"):
#             raw = re.sub(r"^```(json)?", "", raw, flags=re.IGNORECASE).strip()
#             raw = re.sub(r"```$", "", raw).strip()
#
#         try:
#             return json.loads(raw)
#         except Exception as e:
#             logger.warning(f"Failed to parse LLM JSON response: {e}")
#             return {}
#
#     # ------------------------------------------------------------
#     # LLM FALLBACK
#     # ------------------------------------------------------------
#
#     def _llm_fallback_analysis(
#         self,
#         ocr_text: str,
#         vision_caption: str,
#         metadata: Dict[str, Any],
#         current_fields: Dict[str, Any],
#         current_summary: str,
#         current_content_type: str,
#     ) -> Dict[str, Any]:
#         """
#         Call Ollama (mistral:7b) to semantically analyze the image
#         when deterministic analysis is weak or text is missing.
#         """
#
#         prompt = f"""
# Return ONLY valid JSON. Do not include markdown, comments, or any text outside the JSON.
#
# You are an expert image analyst.
#
# You are given:
# 1) Image metadata (technical details)
# 2) OCR text extracted from the image (may be empty or noisy)
# 3) A vision-generated caption describing the visual content (may be empty)
# 4) A preliminary rule-based classification and summary
#
# Your job:
# - Refine the content_type (high-level category of the image)
# - Improve the summary (1–3 concise sentences, dashboard-ready)
# - Suggest structured fields (key-value pairs) that are useful for a document intelligence dashboard
# - Suggest insights (short cards) about the image
# - Suggest alerts if anything looks problematic or low quality
# - Estimate an overall confidence score between 0 and 1
#
# Return ONLY a valid JSON object with the following keys:
# - content_type: string
# - summary: string
# - fields: object (dict of key -> value, strings)
# - insights: array of objects with keys: type, title, description, color, icon
# - alerts: array of objects with keys: severity, title, message, field (optional)
# - confidence: number between 0 and 1
#
# Image metadata:
# {json.dumps(metadata, indent=2)}
#
# OCR text:
# {ocr_text or "[NO TEXT EXTRACTED]"}
#
# Vision caption:
# {vision_caption or "[NO CAPTION AVAILABLE]"}
#
# Current fields:
# {json.dumps(current_fields, indent=2)}
#
# Current summary:
# {current_summary}
#
# Current content_type:
# {current_content_type}
# """
#
#         logger.info("🧠 ImageAnalyzer: Calling Ollama LLM fallback for image analysis")
#
#         try:
#             response = ollama_client.generate(
#                 model=config.model,
#                 prompt=prompt,
#             )
#         except Exception as e:
#             logger.warning(f"LLM fallback call failed: {e}")
#             return {}
#
#         # Your client returns a GenerateResponse object with `.response` as the text
#         raw = response.response if hasattr(response, "response") else response
#         parsed = self._safe_json(raw)
#         return parsed or {}
#
#     # ------------------------------------------------------------
#     # MAIN ANALYZE
#     # ------------------------------------------------------------
#
#     def analyze(
#         self,
#         file_path: str,
#         text: str = "",
#         metadata: Optional[Dict[str, Any]] = None,
#         parsed: Optional[Dict[str, Any]] = None,
#     ) -> Dict[str, Any]:
#         logger.info(f"📄 ImageAnalyzer: {file_path}")
#         metadata = metadata or {}
#
#         # 1) Extract image metadata and visual properties
#         self.image_metadata = self._extract_image_metadata(file_path)
#
#         # 2) Vision captioning (semantic description of the image)
#         vision_caption = self._generate_image_caption(file_path)
#         if vision_caption:
#             self.fields["vision_caption"] = vision_caption
#
#         # 3) Get text from image (OCR)
#         full_text = (text or "").strip()
#
#         if not full_text:
#             try:
#                 full_text = self._extract_text_with_ocr(file_path).strip()
#                 if full_text:
#                     logger.info(f"🔍 OCR extracted {len(full_text)} chars from image")
#             except Exception as e:
#                 logger.warning(f"⚠️ OCR failed for image: {e}")
#
#         # 4) Classify image content type
#         content_type = self._classify_image_content(full_text, self.image_metadata, file_path)
#         self.fields["content_type"] = content_type
#
#         # 5) Extract structured fields based on content type
#         self.fields.update(self._extract_content_fields(full_text, content_type))
#
#         # 6) Build universal summary
#         summary = self._build_summary(self.fields, self.image_metadata)
#
#         # 7) Run validations
#         self.validations = self._run_validations(self.fields, self.image_metadata)
#         alerts = self._build_alerts(self.validations)
#
#         # 8) Confidence heuristic
#         confidence = self._estimate_confidence(self.fields, self.image_metadata, self.validations)
#
#         # 9) Generate insights
#         insights = self._generate_insights(self.fields, self.image_metadata, self.validations)
#
#         # ------------------------------------------------------------
#         # 10) LLM FALLBACK TRIGGER: ANY IMAGE WITH NO TEXT
#         # ------------------------------------------------------------
#
#         text_length = len(full_text)
#         needs_llm = True
#
#         # # Your choice: fallback for any image with no text
#         # if text_length == 0:
#         #     needs_llm = True
#         #
#         # # Also fallback if heuristic confidence is low
#         # if confidence < 0.55:
#         #     needs_llm = True
#
#         if needs_llm:
#             logger.info("⚠️ ImageAnalyzer: Falling back to LLM analysis (mistral:7b) with vision caption")
#             llm_result = self._llm_fallback_analysis(
#                 ocr_text=full_text,
#                 vision_caption=vision_caption,
#                 metadata=self.image_metadata,
#                 current_fields=self.fields,
#                 current_summary=summary,
#                 current_content_type=content_type,
#             )
#
#             if llm_result:
#                 # Merge / override with LLM-enhanced results
#                 new_content_type = llm_result.get("content_type")
#                 if isinstance(new_content_type, str) and new_content_type.strip():
#                     content_type = new_content_type.strip()
#                     self.fields["content_type"] = content_type
#
#                 new_summary = llm_result.get("summary")
#                 if isinstance(new_summary, str) and new_summary.strip():
#                     summary = new_summary.strip()
#
#                 llm_fields = llm_result.get("fields") or {}
#                 if isinstance(llm_fields, dict):
#                     self.fields.update({k: str(v) for k, v in llm_fields.items()})
#
#                 llm_insights = llm_result.get("insights") or []
#                 if isinstance(llm_insights, list):
#                     insights.extend(llm_insights)
#
#                 llm_alerts = llm_result.get("alerts") or []
#                 if isinstance(llm_alerts, list):
#                     alerts.extend(llm_alerts)
#
#                 llm_conf = llm_result.get("confidence")
#                 if isinstance(llm_conf, (int, float)):
#                     confidence = max(0.0, min(1.0, (confidence + float(llm_conf)) / 2))
#
#         rawtext = " ".join((full_text, summary, vision_caption))
#
#         # 11) Build result object
#         result: Dict[str, Any] = {
#             "type": "image",
#             "subtype": content_type.lower().replace(" ", "_"),
#             "summary": summary,
#             "confidence": confidence,
#             "fields": self.fields,
#             "image_metadata": self.image_metadata,
#             # Prefer OCR text, then vision caption, then summary
#             "raw_text": rawtext,
#             "cleaned_text": rawtext,
#             "validations": self.validations,
#             "alerts": alerts,
#             "content_type": content_type,
#             "insights": insights,
#             "has_advanced_analytics": True,
#             "ui_config": self._build_ui_config(self.fields, self.image_metadata, self.validations, insights),
#         }
#
#         return result
#
#     # ------------------------------------------------------------
#     # IMAGE METADATA EXTRACTION
#     # ------------------------------------------------------------
#
#     def _extract_image_metadata(self, file_path: str) -> Dict[str, Any]:
#         """
#         Extract technical metadata from the image file.
#         """
#         metadata = {
#             "format": "unknown",
#             "width": 0,
#             "height": 0,
#             "mode": "unknown",
#             "size_bytes": 0,
#             "megapixels": 0.0,
#             "aspect_ratio": "unknown",
#             "orientation": "unknown",
#             "has_transparency": False,
#             "color_depth": "unknown",
#         }
#
#         try:
#             # Get file size
#             metadata["size_bytes"] = os.path.getsize(file_path)
#
#             # Open and analyze image
#             with Image.open(file_path) as img:
#                 metadata["format"] = img.format or "unknown"
#                 metadata["width"] = img.width
#                 metadata["height"] = img.height
#                 metadata["mode"] = img.mode
#
#                 # Calculate megapixels
#                 metadata["megapixels"] = round((img.width * img.height) / 1_000_000, 2)
#
#                 # Determine aspect ratio
#                 if img.width > 0 and img.height > 0:
#                     ratio = img.width / img.height
#                     if 0.9 <= ratio <= 1.1:
#                         metadata["aspect_ratio"] = "Square (1:1)"
#                     elif 1.3 <= ratio <= 1.4:
#                         metadata["aspect_ratio"] = "4:3"
#                     elif 1.7 <= ratio <= 1.8:
#                         metadata["aspect_ratio"] = "16:9"
#                     elif ratio > 1.8:
#                         metadata["aspect_ratio"] = "Panoramic"
#                     elif ratio < 0.9:
#                         metadata["aspect_ratio"] = "Portrait"
#                     else:
#                         metadata["aspect_ratio"] = f"{ratio:.2f}:1"
#
#                 # Determine orientation
#                 if img.width > img.height:
#                     metadata["orientation"] = "Landscape"
#                 elif img.height > img.width:
#                     metadata["orientation"] = "Portrait"
#                 else:
#                     metadata["orientation"] = "Square"
#
#                 # Check for transparency
#                 metadata["has_transparency"] = img.mode in ("RGBA", "LA", "P")
#
#                 # Color depth
#                 if img.mode == "1":
#                     metadata["color_depth"] = "1-bit (B&W)"
#                 elif img.mode == "L":
#                     metadata["color_depth"] = "8-bit Grayscale"
#                 elif img.mode == "RGB":
#                     metadata["color_depth"] = "24-bit RGB"
#                 elif img.mode == "RGBA":
#                     metadata["color_depth"] = "32-bit RGBA"
#                 else:
#                     metadata["color_depth"] = img.mode
#
#                 logger.info(f"✓ Image metadata: {metadata['width']}x{metadata['height']} {metadata['format']}")
#
#         except Exception as e:
#             logger.warning(f"⚠️ Failed to extract image metadata: {e}")
#
#         return metadata
#
#     # ------------------------------------------------------------
#     # CONTENT CLASSIFICATION
#     # ------------------------------------------------------------
#
#     def _classify_image_content(self, text: str, metadata: Dict[str, Any], file_path: str) -> str:
#         """
#         Classify what type of content the image contains.
#         """
#         text_lower = text.lower()
#         filename_lower = os.path.basename(file_path).lower()
#
#         # Screenshot detection
#         if any(k in filename_lower for k in ["screenshot", "screen_shot", "screen", "capture"]):
#             return "Screenshot"
#
#         if any(k in text_lower for k in ["screenshot", "screen capture", "snipping tool"]):
#             return "Screenshot"
#
#         # Document/Form detection
#         if any(k in text_lower for k in ["form", "application", "agreement", "contract", "invoice",
#                                          "receipt", "statement", "certificate", "license"]):
#             return "Scanned Document"
#
#         # ID/Card detection
#         if any(k in text_lower for k in ["driver license", "driver's license", "passport", "id card",
#                                          "identification", "social security"]):
#             return "ID Document"
#
#         # Receipt/Invoice detection
#         if any(k in text_lower for k in ["total:", "subtotal:", "tax:", "amount due", "receipt #",
#                                          "invoice #", "order #", "purchased"]):
#             return "Receipt/Invoice"
#
#         # Medical image detection
#         if any(k in text_lower for k in ["x-ray", "mri", "ct scan", "ultrasound", "radiology",
#                                          "patient", "hospital", "medical record"]):
#             return "Medical Image"
#
#         # Chart/Graph detection
#         if any(k in text_lower for k in ["chart", "graph", "diagram", "figure", "axis", "legend",
#                                          "bar chart", "pie chart", "line graph"]):
#             return "Chart/Diagram"
#
#         # Business card detection
#         if any(k in text_lower for k in ["tel:", "email:", "phone:", "mobile:", "fax:"]):
#             if len(text) < 500:  # Business cards typically have little text
#                 return "Business Card"
#
#         # Certificate detection
#         if any(k in text_lower for k in ["certificate", "certification", "hereby certify",
#                                          "awarded to", "in recognition"]):
#             return "Certificate"
#
#         # Presentation slide detection
#         if any(k in text_lower for k in ["slide", "presentation", "agenda", "objectives"]):
#             return "Presentation Slide"
#
#         # Photo detection (minimal or no text)
#         if len(text.strip()) < 50:
#             # Check aspect ratio for typical photo formats
#             aspect = metadata.get("aspect_ratio", "")
#             if any(x in aspect for x in ["4:3", "16:9", "3:2"]):
#                 return "Photo"
#
#         # Logo/Design detection (usually small, specific formats)
#         if metadata.get("has_transparency") and metadata.get("megapixels", 0) < 1.0:
#             return "Logo/Design"
#
#         # Meme/Social Media detection
#         if any(k in filename_lower for k in ["meme", "funny", "lol", "viral"]):
#             return "Meme/Social Media"
#
#         # Default classifications based on content length
#         if len(text.strip()) > 100:
#             return "Document Image"
#         elif len(text.strip()) > 0:
#             return "Image with Text"
#         else:
#             return "Photo/Visual"
#
#     # ------------------------------------------------------------
#     # FIELD EXTRACTION
#     # ------------------------------------------------------------
#
#     def _extract_content_fields(self, text: str, content_type: str) -> Dict[str, str]:
#         """
#         Extract structured fields based on content type.
#         """
#         fields: Dict[str, str] = {}
#
#         # Text statistics
#         fields["text_length"] = str(len(text))
#         fields["word_count"] = str(len(text.split())) if text else "0"
#
#         # Dates
#         date_pattern = r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b'
#         dates = re.findall(date_pattern, text)
#         if dates:
#             fields["date"] = dates[0]
#
#         # Email addresses
#         email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
#         emails = re.findall(email_pattern, text)
#         if emails:
#             fields["email"] = emails[0]
#
#         # Phone numbers
#         phone_pattern = r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
#         phones = re.findall(phone_pattern, text)
#         if phones:
#             fields["phone"] = phones[0]
#
#         # URLs
#         url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
#         urls = re.findall(url_pattern, text)
#         if urls:
#             fields["url"] = urls[0]
#
#         # Money amounts
#         money_pattern = r'\$\s*[\d,]+\.?\d*'
#         amounts = re.findall(money_pattern, text)
#         if amounts:
#             fields["amount"] = amounts[0]
#
#         # Names (all caps words, likely names)
#         name_pattern = r'\b[A-Z][A-Z\s]{2,}\b'
#         names = re.findall(name_pattern, text)
#         if names and len(names[0]) < 50:
#             fields["name"] = names[0].strip()
#
#         # Content-specific extraction
#         if content_type == "Receipt/Invoice":
#             # Try to extract total
#             total_pattern = r'total[:\s]+\$?\s*([\d,]+\.?\d*)'
#             total_match = re.search(total_pattern, text, re.IGNORECASE)
#             if total_match:
#                 fields["total"] = f"${total_match.group(1)}"
#
#         return fields
#
#     # ------------------------------------------------------------
#     # SUMMARY
#     # ------------------------------------------------------------
#
#     def _build_summary(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> str:
#         """
#         Build a human-readable summary of the image.
#         """
#         content_type = fields.get("content_type", "Image")
#         width = metadata.get("width", 0)
#         height = metadata.get("height", 0)
#         format_type = metadata.get("format", "unknown")
#         megapixels = metadata.get("megapixels", 0)
#
#         summary_parts = [f"{content_type}"]
#
#         # Add dimensions
#         if width and height:
#             summary_parts.append(f"{width}×{height}px")
#
#         # Add megapixels if significant
#         if megapixels >= 1.0:
#             summary_parts.append(f"({megapixels}MP)")
#
#         # Add format
#         if format_type != "unknown":
#             summary_parts.append(f"{format_type}")
#
#         # Add text info if present
#         text_length = int(fields.get("text_length", "0"))
#         if text_length > 0:
#             summary_parts.append(f"with {text_length} chars of text")
#
#         # Add specific details based on content
#         if fields.get("date"):
#             summary_parts.append(f"dated {fields['date']}")
#
#         if fields.get("amount"):
#             summary_parts.append(f"amount: {fields['amount']}")
#
#         return " • ".join(summary_parts)
#
#     # ------------------------------------------------------------
#     # VALIDATIONS
#     # ------------------------------------------------------------
#
#     def _run_validations(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
#         """
#         Run quality checks on the image.
#         """
#         validations: List[Dict[str, Any]] = []
#
#         def add(code: str, level: str, message: str, field: Optional[str] = None):
#             validations.append({
#                 "code": code,
#                 "level": level,
#                 "message": message,
#                 "field": field,
#             })
#
#         # Image quality checks
#         width = metadata.get("width", 0)
#         height = metadata.get("height", 0)
#
#         # Resolution check
#         if width < 800 or height < 600:
#             add(
#                 code="low_resolution",
#                 level="warning",
#                 message=f"Image resolution ({width}×{height}) is relatively low. Text may be harder to read.",
#                 field="resolution",
#             )
#
#         # Very high resolution check
#         if width > 10000 or height > 10000:
#             add(
#                 code="very_high_resolution",
#                 level="info",
#                 message=f"Very high resolution image ({width}×{height}). Consider optimizing for faster loading.",
#                 field="resolution",
#             )
#
#         # File size check
#         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
#         if size_mb > 10:
#             add(
#                 code="large_file_size",
#                 level="warning",
#                 message=f"Large file size ({size_mb:.1f}MB). Consider compressing the image.",
#                 field="file_size",
#             )
#
#         # OCR text check
#         text_length = int(fields.get("text_length", "0"))
#         content_type = fields.get("content_type", "")
#
#         if "Document" in content_type or "Receipt" in content_type or "Certificate" in content_type:
#             if text_length == 0:
#                 add(
#                     code="no_text_extracted",
#                     level="error",
#                     message="No text could be extracted from this document image. Image quality may be too low.",
#                     field="text",
#                 )
#             elif text_length < 20:
#                 add(
#                     code="minimal_text",
#                     level="warning",
#                     message=f"Only {text_length} characters extracted. OCR quality may be poor.",
#                     field="text",
#                 )
#
#         # Format check
#         image_format = metadata.get("format", "unknown")
#         if image_format in ["BMP", "TIFF"]:
#             add(
#                 code="unoptimized_format",
#                 level="info",
#                 message=f"{image_format} format detected. Consider converting to JPEG or PNG for better compatibility.",
#                 field="format",
#             )
#
#         return validations
#
#     def _build_alerts(self, validations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
#         """
#         Convert validations into UI alerts.
#         """
#         alerts: List[Dict[str, Any]] = []
#
#         for v in validations:
#             if v["level"] == "error":
#                 alerts.append({
#                     "severity": "high",
#                     "title": "Critical Issue",
#                     "message": v["message"],
#                     "field": v.get("field"),
#                 })
#             elif v["level"] == "warning":
#                 alerts.append({
#                     "severity": "medium",
#                     "title": "Quality Warning",
#                     "message": v["message"],
#                     "field": v.get("field"),
#                 })
#             elif v["level"] == "info":
#                 alerts.append({
#                     "severity": "low",
#                     "title": "Information",
#                     "message": v["message"],
#                     "field": v.get("field"),
#                 })
#
#         return alerts
#
#     def _estimate_confidence(
#         self,
#         fields: Dict[str, str],
#         metadata: Dict[str, Any],
#         validations: List[Dict[str, Any]],
#     ) -> float:
#         """
#         Estimate confidence in the analysis.
#         """
#         base = 0.9
#
#         # Reduce confidence for quality issues
#         for v in validations:
#             if v["level"] == "error":
#                 base -= 0.3
#             elif v["level"] == "warning":
#                 base -= 0.1
#
#         # Increase confidence if we extracted useful fields
#         if len(fields) > 5:
#             base += 0.05
#
#         return max(0.0, min(1.0, base))
#
#     # ------------------------------------------------------------
#     # INSIGHTS
#     # ------------------------------------------------------------
#
#     def _generate_insights(
#         self,
#         fields: Dict[str, str],
#         metadata: Dict[str, Any],
#         validations: List[Dict[str, Any]]
#     ) -> List[Dict[str, Any]]:
#         """
#         Generate insights about the image.
#         """
#         insights: List[Dict[str, Any]] = []
#
#         # Content type insight
#         content_type = fields.get("content_type", "Unknown")
#         insights.append({
#             "type": "info",
#             "title": f"Detected: {content_type}",
#             "description": "Image content automatically classified",
#             "color": "#4A90E2",
#             "icon": "🖼️"
#         })
#
#         # Resolution insight
#         width = metadata.get("width", 0)
#         height = metadata.get("height", 0)
#         megapixels = metadata.get("megapixels", 0)
#
#         if megapixels >= 5.0:
#             insights.append({
#                 "type": "success",
#                 "title": "High Quality Image",
#                 "description": f"{width}×{height}px ({megapixels}MP) - Excellent resolution",
#                 "color": "#00C851",
#                 "icon": "✨"
#             })
#         elif megapixels >= 2.0:
#             insights.append({
#                 "type": "info",
#                 "title": "Good Quality Image",
#                 "description": f"{width}×{height}px ({megapixels}MP) - Good resolution",
#                 "color": "#4A90E2",
#                 "icon": "📸"
#             })
#
#         # Text extraction insight
#         text_length = int(fields.get("text_length", "0"))
#         if text_length > 0:
#             insights.append({
#                 "type": "success",
#                 "title": "Text Extracted",
#                 "description": f"Successfully extracted {text_length} characters via OCR",
#                 "color": "#00C851",
#                 "icon": "📝"
#             })
#
#         # File size insight
#         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
#         if size_mb > 5:
#             insights.append({
#                 "type": "warning",
#                 "title": "Large File Size",
#                 "description": f"{size_mb:.1f}MB - Consider compressing for faster loading",
#                 "color": "#FFA726",
#                 "icon": "💾"
#             })
#
#         # Validation summary
#         errors = len([v for v in validations if v["level"] == "error"])
#         warnings = len([v for v in validations if v["level"] == "warning"])
#
#         if errors == 0 and warnings == 0:
#             insights.append({
#                 "type": "success",
#                 "title": "All Quality Checks Passed",
#                 "description": "Image meets quality standards",
#                 "color": "#00C851",
#                 "icon": "✅"
#             })
#         elif errors > 0:
#             insights.append({
#                 "type": "critical",
#                 "title": f"{errors} Quality Issue(s)",
#                 "description": "Review alerts for details",
#                 "color": "#DC3545",
#                 "icon": "⚠️"
#             })
#
#         return insights
#
#     # ------------------------------------------------------------
#     # DASHBOARD HELPERS
#     # ------------------------------------------------------------
#
#     def _calculate_quality_score(self, metadata: Dict[str, Any], validations: List[Dict[str, Any]]) -> float:
#         """
#         Calculate overall image quality score (0-100).
#         """
#         score = 100.0
#
#         # Deduct for validation issues
#         for v in validations:
#             if v["level"] == "error":
#                 score -= 30
#             elif v["level"] == "warning":
#                 score -= 15
#
#         return max(0.0, min(100.0, score))
#
#     def _fields_to_table_data(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
#         """
#         Convert fields and metadata to table rows.
#         """
#         table_data: List[Dict[str, Any]] = []
#
#         # Image properties
#         table_data.append({
#             "category": "Image Properties",
#             "label": "Format",
#             "value": metadata.get("format", "Unknown")
#         })
#
#         table_data.append({
#             "category": "Image Properties",
#             "label": "Dimensions",
#             "value": f"{metadata.get('width', 0)}×{metadata.get('height', 0)}px"
#         })
#
#         table_data.append({
#             "category": "Image Properties",
#             "label": "Megapixels",
#             "value": f"{metadata.get('megapixels', 0)}MP"
#         })
#
#         table_data.append({
#             "category": "Image Properties",
#             "label": "Orientation",
#             "value": metadata.get("orientation", "Unknown")
#         })
#
#         table_data.append({
#             "category": "Image Properties",
#             "label": "Aspect Ratio",
#             "value": metadata.get("aspect_ratio", "Unknown")
#         })
#
#         table_data.append({
#             "category": "Image Properties",
#             "label": "Color Depth",
#             "value": metadata.get("color_depth", "Unknown")
#         })
#
#         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
#         table_data.append({
#             "category": "Image Properties",
#             "label": "File Size",
#             "value": f"{size_mb:.2f}MB"
#         })
#
#         # Extracted fields
#         for key, value in fields.items():
#             if key not in ["content_type", "text_length", "word_count"] and value:
#                 table_data.append({
#                     "category": "Extracted Data",
#                     "label": key.replace("_", " ").title(),
#                     "value": value
#                 })
#
#         return table_data
#
#     # ------------------------------------------------------------
#     # UI CONFIG
#     # ------------------------------------------------------------
#
#     def _build_ui_config(
#         self,
#         fields: Dict[str, str],
#         metadata: Dict[str, Any],
#         validations: List[Dict[str, Any]],
#         insights: List[Dict[str, Any]]
#     ) -> Dict[str, Any]:
#         """
#         Build UI config for dashboard and document view.
#         """
#
#         width = metadata.get("width", 0)
#         height = metadata.get("height", 0)
#         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
#         quality_score = self._calculate_quality_score(metadata, validations)
#
#         return {
#             # Dashboard view
#             "hero_metrics": [
#                 {
#                     "key": "content_type",
#                     "label": "Content Type",
#                     "value": fields.get("content_type", "Image"),
#                     "icon": "🖼️",
#                     "color": "#4A90E2",
#                     "size": "large"
#                 },
#                 {
#                     "key": "quality",
#                     "label": "Quality Score",
#                     "value": f"{quality_score:.0f}/100",
#                     "icon": "⭐",
#                     "color": "#00C851" if quality_score >= 80 else "#FFA726",
#                     "size": "large"
#                 },
#                 {
#                     "key": "resolution",
#                     "label": "Resolution",
#                     "value": f"{width}×{height}",
#                     "icon": "📏",
#                     "color": "#AB47BC",
#                     "size": "medium"
#                 },
#                 {
#                     "key": "size",
#                     "label": "File Size",
#                     "value": f"{size_mb:.1f}MB",
#                     "icon": "💾",
#                     "color": "#FFA726",
#                     "size": "medium"
#                 }
#             ],
#
#             "charts": [
#                 {
#                     "id": "quality_breakdown",
#                     "type": "doughnut",
#                     "title": "Quality Assessment",
#                     "data": {
#                         "labels": ["Quality Score", "Deductions"],
#                         "datasets": [{
#                             "data": [quality_score, 100 - quality_score],
#                             "backgroundColor": ["#00C851", "#FF6B6B"]
#                         }]
#                     },
#                     "config": {
#                         "responsive": True,
#                         "maintainAspectRatio": False,
#                         "plugins": {
#                             "legend": {
#                                 "position": "bottom"
#                             }
#                         }
#                     },
#                     "height": "300px"
#                 }
#             ],
#
#             "tables": [
#                 {
#                     "id": "image_details",
#                     "title": "Image Details",
#                     "data": self._fields_to_table_data(fields, metadata),
#                     "columns": [
#                         {
#                             "field": "category",
#                             "header": "Category",
#                             "sortable": True
#                         },
#                         {
#                             "field": "label",
#                             "header": "Property",
#                             "sortable": True
#                         },
#                         {
#                             "field": "value",
#                             "header": "Value",
#                             "sortable": True
#                         }
#                     ],
#                     "config": {
#                         "paginator": True,
#                         "rows": 10,
#                         "showGridlines": True,
#                         "filterDelay": 0,
#                         "globalFilterFields": ["label", "value"]
#                     }
#                 }
#             ],
#
#             "insights": insights,
#
#             "layout": {
#                 "type": "image_analysis",
#                 "sections": [
#                     {
#                         "id": "top",
#                         "components": ["hero_metrics", "charts"]
#                     },
#                     {
#                         "id": "details",
#                         "components": ["tables", "insights", "alerts"]
#                     }
#                 ]
#             }
#         }
#
#
# # # app/services/analyzers/image_analyzer.py
# #
# # import logging
# # from typing import Any, Dict, Optional, List
# # from datetime import datetime
# # import os
# # import re
# # from PIL import Image
# # import io
# #
# # from app.services.analyzers.base_analyzer import BaseAnalyzer
# #
# # logger = logging.getLogger(__name__)
# #
# #
# # class ImageAnalyzer(BaseAnalyzer):
# #     """
# #     Universal image analyzer for all image types and content.
# #
# #     Supports:
# #     - Photos (portraits, landscapes, objects, events)
# #     - Screenshots (applications, websites, messages)
# #     - Documents (scanned forms, receipts, letters, certificates)
# #     - Diagrams (flowcharts, org charts, technical drawings)
# #     - Charts/Graphs (bar charts, pie charts, line graphs)
# #     - Art/Design (logos, illustrations, designs)
# #     - Medical Images (X-rays, scans, reports)
# #     - ID Documents (licenses, passports, cards)
# #     - Receipts/Invoices
# #     - And any other visual content
# #
# #     Hybrid behavior:
# #     - Visual analysis: dimensions, format, colors, dominant features
# #     - Text extraction: OCR for any visible text
# #     - Dashboard-ready: generates metrics, charts, and insights
# #     - Content classification: auto-detects what the image contains
# #     """
# #
# #     def __init__(self):
# #         super().__init__()
# #         self.validations: List[Dict[str, Any]] = []
# #         self.fields: Dict[str, str] = {}
# #         self.image_metadata: Dict[str, Any] = {}
# #
# #     def _extract_text_with_ocr(self, file_path: str) -> str:
# #         """
# #         Extract text from image using available OCR methods.
# #         Tries multiple approaches to find working OCR.
# #         """
# #         # Try method 1: Use existing image parser
# #         try:
# #             from app.parsers.image_parser import parse_image
# #             result = parse_image(file_path)
# #             if result and "text" in result:
# #                 return result["text"]
# #         except Exception as e:
# #             logger.debug(f"Method 1 (parse_image) failed: {e}")
# #
# #         # Try method 2: Direct pytesseract
# #         try:
# #             import pytesseract
# #             from PIL import Image
# #             img = Image.open(file_path)
# #             text = pytesseract.image_to_string(img)
# #             return text
# #         except Exception as e:
# #             logger.debug(f"Method 2 (pytesseract) failed: {e}")
# #
# #         # Try method 3: Use pdf OCR function (works for images too)
# #         try:
# #             from app.parsers.pdf_table_extractor import ocr_pdf_to_text
# #             text = ocr_pdf_to_text(file_path)
# #             return text
# #         except Exception as e:
# #             logger.debug(f"Method 3 (ocr_pdf_to_text) failed: {e}")
# #
# #         logger.warning("All OCR methods failed - no text extracted")
# #         return ""
# #
# #     def analyze(
# #             self,
# #             file_path: str,
# #             text: str = "",
# #             metadata: Optional[Dict[str, Any]] = None,
# #             parsed: Optional[Dict[str, Any]] = None,
# #     ) -> Dict[str, Any]:
# #         logger.info(f"📄 ImageAnalyzer: {file_path}")
# #         metadata = metadata or {}
# #
# #         # 1) Extract image metadata and visual properties
# #         self.image_metadata = self._extract_image_metadata(file_path)
# #
# #         # 2) Get text from image (OCR)
# #         full_text = (text or "").strip()
# #
# #         if not full_text:
# #             try:
# #                 # Try OCR extraction using multiple methods
# #                 full_text = self._extract_text_with_ocr(file_path).strip()
# #                 if full_text:
# #                     logger.info(f"🔍 OCR extracted {len(full_text)} chars from image")
# #             except Exception as e:
# #                 logger.warning(f"⚠️ OCR failed for image: {e}")
# #
# #         # 3) Classify image content type
# #         content_type = self._classify_image_content(full_text, self.image_metadata, file_path)
# #         self.fields["content_type"] = content_type
# #
# #         # 4) Extract structured fields based on content type
# #         self.fields.update(self._extract_content_fields(full_text, content_type))
# #
# #         # 5) Build universal summary
# #         summary = self._build_summary(self.fields, self.image_metadata)
# #
# #         # 6) Run validations
# #         self.validations = self._run_validations(self.fields, self.image_metadata)
# #         alerts = self._build_alerts(self.validations)
# #
# #         # 7) Confidence heuristic
# #         confidence = self._estimate_confidence(self.fields, self.image_metadata, self.validations)
# #
# #         # 8) Generate insights
# #         insights = self._generate_insights(self.fields, self.image_metadata, self.validations)
# #
# #         # 9) Build result object
# #         result: Dict[str, Any] = {
# #             "type": "image",
# #             "subtype": content_type.lower().replace(" ", "_"),
# #             "summary": summary,
# #             "confidence": confidence,
# #             "fields": self.fields,
# #             "image_metadata": self.image_metadata,
# #             "raw_text": full_text,
# #             "validations": self.validations,
# #             "alerts": alerts,
# #             "content_type": content_type,
# #             "insights": insights,
# #             "has_advanced_analytics": True,
# #             "ui_config": self._build_ui_config(self.fields, self.image_metadata, self.validations, insights),
# #         }
# #
# #         return result
# #
# #     # ------------------------------------------------------------
# #     # IMAGE METADATA EXTRACTION
# #     # ------------------------------------------------------------
# #
# #     def _extract_image_metadata(self, file_path: str) -> Dict[str, Any]:
# #         """
# #         Extract technical metadata from the image file.
# #         """
# #         metadata = {
# #             "format": "unknown",
# #             "width": 0,
# #             "height": 0,
# #             "mode": "unknown",
# #             "size_bytes": 0,
# #             "megapixels": 0.0,
# #             "aspect_ratio": "unknown",
# #             "orientation": "unknown",
# #             "has_transparency": False,
# #             "color_depth": "unknown",
# #         }
# #
# #         try:
# #             # Get file size
# #             metadata["size_bytes"] = os.path.getsize(file_path)
# #
# #             # Open and analyze image
# #             with Image.open(file_path) as img:
# #                 metadata["format"] = img.format or "unknown"
# #                 metadata["width"] = img.width
# #                 metadata["height"] = img.height
# #                 metadata["mode"] = img.mode
# #
# #                 # Calculate megapixels
# #                 metadata["megapixels"] = round((img.width * img.height) / 1_000_000, 2)
# #
# #                 # Determine aspect ratio
# #                 if img.width > 0 and img.height > 0:
# #                     ratio = img.width / img.height
# #                     if 0.9 <= ratio <= 1.1:
# #                         metadata["aspect_ratio"] = "Square (1:1)"
# #                     elif 1.3 <= ratio <= 1.4:
# #                         metadata["aspect_ratio"] = "4:3"
# #                     elif 1.7 <= ratio <= 1.8:
# #                         metadata["aspect_ratio"] = "16:9"
# #                     elif ratio > 1.8:
# #                         metadata["aspect_ratio"] = "Panoramic"
# #                     elif ratio < 0.9:
# #                         metadata["aspect_ratio"] = "Portrait"
# #                     else:
# #                         metadata["aspect_ratio"] = f"{ratio:.2f}:1"
# #
# #                 # Determine orientation
# #                 if img.width > img.height:
# #                     metadata["orientation"] = "Landscape"
# #                 elif img.height > img.width:
# #                     metadata["orientation"] = "Portrait"
# #                 else:
# #                     metadata["orientation"] = "Square"
# #
# #                 # Check for transparency
# #                 metadata["has_transparency"] = img.mode in ("RGBA", "LA", "P")
# #
# #                 # Color depth
# #                 if img.mode == "1":
# #                     metadata["color_depth"] = "1-bit (B&W)"
# #                 elif img.mode == "L":
# #                     metadata["color_depth"] = "8-bit Grayscale"
# #                 elif img.mode == "RGB":
# #                     metadata["color_depth"] = "24-bit RGB"
# #                 elif img.mode == "RGBA":
# #                     metadata["color_depth"] = "32-bit RGBA"
# #                 else:
# #                     metadata["color_depth"] = img.mode
# #
# #                 logger.info(f"✓ Image metadata: {metadata['width']}x{metadata['height']} {metadata['format']}")
# #
# #         except Exception as e:
# #             logger.warning(f"⚠️ Failed to extract image metadata: {e}")
# #
# #         return metadata
# #
# #     # ------------------------------------------------------------
# #     # CONTENT CLASSIFICATION
# #     # ------------------------------------------------------------
# #
# #     def _classify_image_content(self, text: str, metadata: Dict[str, Any], file_path: str) -> str:
# #         """
# #         Classify what type of content the image contains.
# #         """
# #         text_lower = text.lower()
# #         filename_lower = os.path.basename(file_path).lower()
# #
# #         # Screenshot detection
# #         if any(k in filename_lower for k in ["screenshot", "screen_shot", "screen", "capture"]):
# #             return "Screenshot"
# #
# #         if any(k in text_lower for k in ["screenshot", "screen capture", "snipping tool"]):
# #             return "Screenshot"
# #
# #         # Document/Form detection
# #         if any(k in text_lower for k in ["form", "application", "agreement", "contract", "invoice",
# #                                          "receipt", "statement", "certificate", "license"]):
# #             return "Scanned Document"
# #
# #         # ID/Card detection
# #         if any(k in text_lower for k in ["driver license", "driver's license", "passport", "id card",
# #                                          "identification", "social security"]):
# #             return "ID Document"
# #
# #         # Receipt/Invoice detection
# #         if any(k in text_lower for k in ["total:", "subtotal:", "tax:", "amount due", "receipt #",
# #                                          "invoice #", "order #", "purchased"]):
# #             return "Receipt/Invoice"
# #
# #         # Medical image detection
# #         if any(k in text_lower for k in ["x-ray", "mri", "ct scan", "ultrasound", "radiology",
# #                                          "patient", "hospital", "medical record"]):
# #             return "Medical Image"
# #
# #         # Chart/Graph detection
# #         if any(k in text_lower for k in ["chart", "graph", "diagram", "figure", "axis", "legend",
# #                                          "bar chart", "pie chart", "line graph"]):
# #             return "Chart/Diagram"
# #
# #         # Business card detection
# #         if any(k in text_lower for k in ["tel:", "email:", "phone:", "mobile:", "fax:"]):
# #             if len(text) < 500:  # Business cards typically have little text
# #                 return "Business Card"
# #
# #         # Certificate detection
# #         if any(k in text_lower for k in ["certificate", "certification", "hereby certify",
# #                                          "awarded to", "in recognition"]):
# #             return "Certificate"
# #
# #         # Presentation slide detection
# #         if any(k in text_lower for k in ["slide", "presentation", "agenda", "objectives"]):
# #             return "Presentation Slide"
# #
# #         # Photo detection (minimal or no text)
# #         if len(text.strip()) < 50:
# #             # Check aspect ratio for typical photo formats
# #             aspect = metadata.get("aspect_ratio", "")
# #             if any(x in aspect for x in ["4:3", "16:9", "3:2"]):
# #                 return "Photo"
# #
# #         # Logo/Design detection (usually small, specific formats)
# #         if metadata.get("has_transparency") and metadata.get("megapixels", 0) < 1.0:
# #             return "Logo/Design"
# #
# #         # Meme/Social Media detection
# #         if any(k in filename_lower for k in ["meme", "funny", "lol", "viral"]):
# #             return "Meme/Social Media"
# #
# #         # Default classifications based on content length
# #         if len(text.strip()) > 100:
# #             return "Document Image"
# #         elif len(text.strip()) > 0:
# #             return "Image with Text"
# #         else:
# #             return "Photo/Visual"
# #
# #     # ------------------------------------------------------------
# #     # FIELD EXTRACTION
# #     # ------------------------------------------------------------
# #
# #     def _extract_content_fields(self, text: str, content_type: str) -> Dict[str, str]:
# #         """
# #         Extract structured fields based on content type.
# #         """
# #         fields = {}
# #
# #         # Text statistics
# #         fields["text_length"] = str(len(text))
# #         fields["word_count"] = str(len(text.split())) if text else "0"
# #
# #         # Extract common patterns
# #
# #         # Dates
# #         date_pattern = r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b'
# #         dates = re.findall(date_pattern, text)
# #         if dates:
# #             fields["date"] = dates[0]
# #
# #         # Email addresses
# #         email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
# #         emails = re.findall(email_pattern, text)
# #         if emails:
# #             fields["email"] = emails[0]
# #
# #         # Phone numbers
# #         phone_pattern = r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
# #         phones = re.findall(phone_pattern, text)
# #         if phones:
# #             fields["phone"] = phones[0]
# #
# #         # URLs
# #         url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
# #         urls = re.findall(url_pattern, text)
# #         if urls:
# #             fields["url"] = urls[0]
# #
# #         # Money amounts
# #         money_pattern = r'\$\s*[\d,]+\.?\d*'
# #         amounts = re.findall(money_pattern, text)
# #         if amounts:
# #             fields["amount"] = amounts[0]
# #
# #         # Names (all caps words, likely names)
# #         name_pattern = r'\b[A-Z][A-Z\s]{2,}\b'
# #         names = re.findall(name_pattern, text)
# #         if names and len(names[0]) < 50:
# #             fields["name"] = names[0].strip()
# #
# #         # Content-specific extraction
# #         if content_type == "Receipt/Invoice":
# #             # Try to extract total
# #             total_pattern = r'total[:\s]+\$?\s*([\d,]+\.?\d*)'
# #             total_match = re.search(total_pattern, text, re.IGNORECASE)
# #             if total_match:
# #                 fields["total"] = f"${total_match.group(1)}"
# #
# #         return fields
# #
# #     # ------------------------------------------------------------
# #     # SUMMARY
# #     # ------------------------------------------------------------
# #
# #     def _build_summary(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> str:
# #         """
# #         Build a human-readable summary of the image.
# #         """
# #         content_type = fields.get("content_type", "Image")
# #         width = metadata.get("width", 0)
# #         height = metadata.get("height", 0)
# #         format_type = metadata.get("format", "unknown")
# #         megapixels = metadata.get("megapixels", 0)
# #
# #         summary_parts = [f"{content_type}"]
# #
# #         # Add dimensions
# #         if width and height:
# #             summary_parts.append(f"{width}×{height}px")
# #
# #         # Add megapixels if significant
# #         if megapixels >= 1.0:
# #             summary_parts.append(f"({megapixels}MP)")
# #
# #         # Add format
# #         if format_type != "unknown":
# #             summary_parts.append(f"{format_type}")
# #
# #         # Add text info if present
# #         text_length = int(fields.get("text_length", "0"))
# #         if text_length > 0:
# #             summary_parts.append(f"with {text_length} chars of text")
# #
# #         # Add specific details based on content
# #         if fields.get("date"):
# #             summary_parts.append(f"dated {fields['date']}")
# #
# #         if fields.get("amount"):
# #             summary_parts.append(f"amount: {fields['amount']}")
# #
# #         return " • ".join(summary_parts)
# #
# #     # ------------------------------------------------------------
# #     # VALIDATIONS
# #     # ------------------------------------------------------------
# #
# #     def _run_validations(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
# #         """
# #         Run quality checks on the image.
# #         """
# #         validations: List[Dict[str, Any]] = []
# #
# #         def add(code: str, level: str, message: str, field: Optional[str] = None):
# #             validations.append({
# #                 "code": code,
# #                 "level": level,
# #                 "message": message,
# #                 "field": field,
# #             })
# #
# #         # Image quality checks
# #         width = metadata.get("width", 0)
# #         height = metadata.get("height", 0)
# #
# #         # Resolution check
# #         if width < 800 or height < 600:
# #             add(
# #                 code="low_resolution",
# #                 level="warning",
# #                 message=f"Image resolution ({width}×{height}) is relatively low. Text may be harder to read.",
# #                 field="resolution",
# #             )
# #
# #         # Very high resolution check
# #         if width > 10000 or height > 10000:
# #             add(
# #                 code="very_high_resolution",
# #                 level="info",
# #                 message=f"Very high resolution image ({width}×{height}). Consider optimizing for faster loading.",
# #                 field="resolution",
# #             )
# #
# #         # File size check
# #         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
# #         if size_mb > 10:
# #             add(
# #                 code="large_file_size",
# #                 level="warning",
# #                 message=f"Large file size ({size_mb:.1f}MB). Consider compressing the image.",
# #                 field="file_size",
# #             )
# #
# #         # OCR text check
# #         text_length = int(fields.get("text_length", "0"))
# #         content_type = fields.get("content_type", "")
# #
# #         if "Document" in content_type or "Receipt" in content_type or "Certificate" in content_type:
# #             if text_length == 0:
# #                 add(
# #                     code="no_text_extracted",
# #                     level="error",
# #                     message="No text could be extracted from this document image. Image quality may be too low.",
# #                     field="text",
# #                 )
# #             elif text_length < 20:
# #                 add(
# #                     code="minimal_text",
# #                     level="warning",
# #                     message=f"Only {text_length} characters extracted. OCR quality may be poor.",
# #                     field="text",
# #                 )
# #
# #         # Format check
# #         image_format = metadata.get("format", "unknown")
# #         if image_format in ["BMP", "TIFF"]:
# #             add(
# #                 code="unoptimized_format",
# #                 level="info",
# #                 message=f"{image_format} format detected. Consider converting to JPEG or PNG for better compatibility.",
# #                 field="format",
# #             )
# #
# #         return validations
# #
# #     def _build_alerts(self, validations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
# #         """
# #         Convert validations into UI alerts.
# #         """
# #         alerts: List[Dict[str, Any]] = []
# #
# #         for v in validations:
# #             if v["level"] == "error":
# #                 alerts.append({
# #                     "severity": "high",
# #                     "title": "Critical Issue",
# #                     "message": v["message"],
# #                     "field": v.get("field"),
# #                 })
# #             elif v["level"] == "warning":
# #                 alerts.append({
# #                     "severity": "medium",
# #                     "title": "Quality Warning",
# #                     "message": v["message"],
# #                     "field": v.get("field"),
# #                 })
# #             elif v["level"] == "info":
# #                 alerts.append({
# #                     "severity": "low",
# #                     "title": "Information",
# #                     "message": v["message"],
# #                     "field": v.get("field"),
# #                 })
# #
# #         return alerts
# #
# #     def _estimate_confidence(
# #             self,
# #             fields: Dict[str, str],
# #             metadata: Dict[str, Any],
# #             validations: List[Dict[str, Any]],
# #     ) -> float:
# #         """
# #         Estimate confidence in the analysis.
# #         """
# #         base = 0.9
# #
# #         # Reduce confidence for quality issues
# #         for v in validations:
# #             if v["level"] == "error":
# #                 base -= 0.3
# #             elif v["level"] == "warning":
# #                 base -= 0.1
# #
# #         # Increase confidence if we extracted useful fields
# #         if len(fields) > 5:
# #             base += 0.05
# #
# #         return max(0.0, min(1.0, base))
# #
# #     # ------------------------------------------------------------
# #     # INSIGHTS
# #     # ------------------------------------------------------------
# #
# #     def _generate_insights(
# #             self,
# #             fields: Dict[str, str],
# #             metadata: Dict[str, Any],
# #             validations: List[Dict[str, Any]]
# #     ) -> List[Dict[str, Any]]:
# #         """
# #         Generate insights about the image.
# #         """
# #         insights = []
# #
# #         # Content type insight
# #         content_type = fields.get("content_type", "Unknown")
# #         insights.append({
# #             "type": "info",
# #             "title": f"Detected: {content_type}",
# #             "description": "Image content automatically classified",
# #             "color": "#4A90E2",
# #             "icon": "🖼️"
# #         })
# #
# #         # Resolution insight
# #         width = metadata.get("width", 0)
# #         height = metadata.get("height", 0)
# #         megapixels = metadata.get("megapixels", 0)
# #
# #         if megapixels >= 5.0:
# #             insights.append({
# #                 "type": "success",
# #                 "title": "High Quality Image",
# #                 "description": f"{width}×{height}px ({megapixels}MP) - Excellent resolution",
# #                 "color": "#00C851",
# #                 "icon": "✨"
# #             })
# #         elif megapixels >= 2.0:
# #             insights.append({
# #                 "type": "info",
# #                 "title": "Good Quality Image",
# #                 "description": f"{width}×{height}px ({megapixels}MP) - Good resolution",
# #                 "color": "#4A90E2",
# #                 "icon": "📸"
# #             })
# #
# #         # Text extraction insight
# #         text_length = int(fields.get("text_length", "0"))
# #         if text_length > 0:
# #             insights.append({
# #                 "type": "success",
# #                 "title": "Text Extracted",
# #                 "description": f"Successfully extracted {text_length} characters via OCR",
# #                 "color": "#00C851",
# #                 "icon": "📝"
# #             })
# #
# #         # File size insight
# #         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
# #         if size_mb > 5:
# #             insights.append({
# #                 "type": "warning",
# #                 "title": "Large File Size",
# #                 "description": f"{size_mb:.1f}MB - Consider compressing for faster loading",
# #                 "color": "#FFA726",
# #                 "icon": "💾"
# #             })
# #
# #         # Validation summary
# #         errors = len([v for v in validations if v["level"] == "error"])
# #         warnings = len([v for v in validations if v["level"] == "warning"])
# #
# #         if errors == 0 and warnings == 0:
# #             insights.append({
# #                 "type": "success",
# #                 "title": "All Quality Checks Passed",
# #                 "description": "Image meets quality standards",
# #                 "color": "#00C851",
# #                 "icon": "✅"
# #             })
# #         elif errors > 0:
# #             insights.append({
# #                 "type": "critical",
# #                 "title": f"{errors} Quality Issue(s)",
# #                 "description": "Review alerts for details",
# #                 "color": "#DC3545",
# #                 "icon": "⚠️"
# #             })
# #
# #         return insights
# #
# #     # ------------------------------------------------------------
# #     # DASHBOARD HELPERS
# #     # ------------------------------------------------------------
# #
# #     def _calculate_quality_score(self, metadata: Dict[str, Any], validations: List[Dict[str, Any]]) -> float:
# #         """
# #         Calculate overall image quality score (0-100).
# #         """
# #         score = 100.0
# #
# #         # Deduct for validation issues
# #         for v in validations:
# #             if v["level"] == "error":
# #                 score -= 30
# #             elif v["level"] == "warning":
# #                 score -= 15
# #
# #         return max(0.0, min(100.0, score))
# #
# #     def _fields_to_table_data(self, fields: Dict[str, str], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
# #         """
# #         Convert fields and metadata to table rows.
# #         """
# #         table_data = []
# #
# #         # Image properties
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "Format",
# #             "value": metadata.get("format", "Unknown")
# #         })
# #
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "Dimensions",
# #             "value": f"{metadata.get('width', 0)}×{metadata.get('height', 0)}px"
# #         })
# #
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "Megapixels",
# #             "value": f"{metadata.get('megapixels', 0)}MP"
# #         })
# #
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "Orientation",
# #             "value": metadata.get("orientation", "Unknown")
# #         })
# #
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "Aspect Ratio",
# #             "value": metadata.get("aspect_ratio", "Unknown")
# #         })
# #
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "Color Depth",
# #             "value": metadata.get("color_depth", "Unknown")
# #         })
# #
# #         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
# #         table_data.append({
# #             "category": "Image Properties",
# #             "label": "File Size",
# #             "value": f"{size_mb:.2f}MB"
# #         })
# #
# #         # Extracted fields
# #         for key, value in fields.items():
# #             if key not in ["content_type", "text_length", "word_count"] and value:
# #                 table_data.append({
# #                     "category": "Extracted Data",
# #                     "label": key.replace("_", " ").title(),
# #                     "value": value
# #                 })
# #
# #         return table_data
# #
# #     # ------------------------------------------------------------
# #     # UI CONFIG
# #     # ------------------------------------------------------------
# #
# #     def _build_ui_config(
# #             self,
# #             fields: Dict[str, str],
# #             metadata: Dict[str, Any],
# #             validations: List[Dict[str, Any]],
# #             insights: List[Dict[str, Any]]
# #     ) -> Dict[str, Any]:
# #         """
# #         Build UI config for dashboard and document view.
# #         """
# #
# #         width = metadata.get("width", 0)
# #         height = metadata.get("height", 0)
# #         megapixels = metadata.get("megapixels", 0)
# #         size_mb = metadata.get("size_bytes", 0) / (1024 * 1024)
# #         quality_score = self._calculate_quality_score(metadata, validations)
# #
# #         return {
# #             # Dashboard view
# #             "hero_metrics": [
# #                 {
# #                     "key": "content_type",
# #                     "label": "Content Type",
# #                     "value": fields.get("content_type", "Image"),
# #                     "icon": "🖼️",
# #                     "color": "#4A90E2",
# #                     "size": "large"
# #                 },
# #                 {
# #                     "key": "quality",
# #                     "label": "Quality Score",
# #                     "value": f"{quality_score:.0f}/100",
# #                     "icon": "⭐",
# #                     "color": "#00C851" if quality_score >= 80 else "#FFA726",
# #                     "size": "large"
# #                 },
# #                 {
# #                     "key": "resolution",
# #                     "label": "Resolution",
# #                     "value": f"{width}×{height}",
# #                     "icon": "📏",
# #                     "color": "#AB47BC",
# #                     "size": "medium"
# #                 },
# #                 {
# #                     "key": "size",
# #                     "label": "File Size",
# #                     "value": f"{size_mb:.1f}MB",
# #                     "icon": "💾",
# #                     "color": "#FFA726",
# #                     "size": "medium"
# #                 }
# #             ],
# #
# #             "charts": [
# #                 {
# #                     "id": "quality_breakdown",
# #                     "type": "doughnut",
# #                     "title": "Quality Assessment",
# #                     "data": {
# #                         "labels": ["Quality Score", "Deductions"],
# #                         "datasets": [{
# #                             "data": [quality_score, 100 - quality_score],
# #                             "backgroundColor": ["#00C851", "#FF6B6B"]
# #                         }]
# #                     },
# #                     "config": {
# #                         "responsive": True,
# #                         "maintainAspectRatio": False,
# #                         "plugins": {
# #                             "legend": {
# #                                 "position": "bottom"
# #                             }
# #                         }
# #                     },
# #                     "height": "300px"
# #                 }
# #             ],
# #
# #             "tables": [
# #                 {
# #                     "id": "image_details",
# #                     "title": "Image Details",
# #                     "data": self._fields_to_table_data(fields, metadata),
# #                     "columns": [
# #                         {
# #                             "field": "category",
# #                             "header": "Category",
# #                             "sortable": True
# #                         },
# #                         {
# #                             "field": "label",
# #                             "header": "Property",
# #                             "sortable": True
# #                         },
# #                         {
# #                             "field": "value",
# #                             "header": "Value",
# #                             "sortable": True
# #                         }
# #                     ],
# #                     "config": {
# #                         "paginator": True,
# #                         "rows": 10,
# #                         "showGridlines": True,
# #                         "filterDelay": 0,
# #                         "globalFilterFields": ["label", "value"]
# #                     }
# #                 }
# #             ],
# #
# #             "insights": insights,
# #
# #             "layout": {
# #                 "type": "responsive_grid",
# #                 "columns": 12,
# #                 "gap": 16,
# #                 "sections": [
# #                     {
# #                         "id": "overview",
# #                         "title": "Image Overview",
# #                         "components": ["hero_metrics"],
# #                         "grid": {"cols": 12, "rows": 2},
# #                         "order": 1
# #                     },
# #                     {
# #                         "id": "insights_section",
# #                         "title": "Key Insights",
# #                         "components": ["insights"],
# #                         "grid": {"cols": 12, "rows": 3},
# #                         "order": 2
# #                     },
# #                     {
# #                         "id": "quality_metrics",
# #                         "title": "Quality Metrics",
# #                         "components": ["charts"],
# #                         "grid": {"cols": 12, "rows": 4},
# #                         "order": 3
# #                     },
# #                     {
# #                         "id": "details",
# #                         "title": "Image Details",
# #                         "components": ["tables"],
# #                         "grid": {"cols": 12, "rows": 6},
# #                         "order": 4
# #                     }
# #                 ]
# #             },
# #
# #             # Document view
# #             "sections": [
# #                 {
# #                     "id": "image_summary",
# #                     "title": "Image Summary",
# #                     "type": "key_value",
# #                     "icon": "📋",
# #                     "fields": [
# #                         {"label": "Content Type", "key": "content_type"},
# #                         {"label": "Format", "value": metadata.get("format", "Unknown")},
# #                         {"label": "Dimensions", "value": f"{width}×{height}px"},
# #                         {"label": "Megapixels", "value": f"{megapixels}MP"},
# #                         {"label": "Orientation", "value": metadata.get("orientation", "Unknown")},
# #                         {"label": "File Size", "value": f"{size_mb:.2f}MB"},
# #                         {"label": "Text Extracted", "value": f"{fields.get('text_length', '0')} characters"},
# #                     ]
# #                 },
# #                 {
# #                     "id": "image_alerts",
# #                     "title": "Quality Alerts",
# #                     "type": "alerts",
# #                     "icon": "⚠️",
# #                     "field": "alerts",
# #                     "description": "Image quality checks and recommendations"
# #                 },
# #                 {
# #                     "id": "extracted_text",
# #                     "title": "Extracted Text (OCR)",
# #                     "type": "text",
# #                     "icon": "📄",
# #                     "field": "raw_text",
# #                     "description": "Text extracted from the image via OCR",
# #                     "config": {
# #                         "maxHeight": "400px",
# #                         "copyable": True,
# #                         "searchable": True
# #                     }
# #                 }
# #             ]
# #         }