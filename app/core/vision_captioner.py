# app/core/vision_captioner.py

import logging
import ollama

logger = logging.getLogger(__name__)


class VisionCaptioner:
    """
    Wrapper around Moondream vision model for image captioning.
    """

    def __init__(self, model_name: str = "moondream"):
        self.model_name = model_name

    def caption_image(self, file_path: str) -> str:
        """
        Generate a caption for an image using Moondream.
        """
        try:
            with open(file_path, "rb") as f:
                img_bytes = f.read()

            response = ollama.generate(
                model=self.model_name,
                prompt="Describe this image in one concise sentence.",
                images=[img_bytes],
            )

            caption = response.get("response", "").strip()
            return caption

        except Exception as e:
            logger.warning(f"Vision captioning failed: {e}")
            return ""


vision_captioner = VisionCaptioner()
