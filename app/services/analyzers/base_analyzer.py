from abc import ABC, abstractmethod
import logging
from typing import Dict, Any, Optional
from app.utils.dataframe_cleaner import clean_dataframe

logger = logging.getLogger(__name__)


class BaseAnalyzer(ABC):
    """Base class for all document analyzers."""

    def clean_df(self, df):
        return clean_dataframe(df)

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def analyze(
        self,
        file_path: str,
        text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        parsed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze document and return structured data.

        Args:
            file_path: Path to the document
            text: Extracted text content (optional)
            metadata: Document metadata (optional)
            parsed: Parsed structures (tables, etc.) if available (optional)

        Returns:
            Analysis results with structured data.
        """
        raise NotImplementedError

    def add_ui_config(self, analytics_result: Dict, document_type: str) -> Dict:
        """
        Add UI configuration to analytics result.
        Fully automatic - works for ANY document type.
        """
        from app.services.analytics_auto_generator import analytics_auto_generator
        # Avoid regenerating on top of an existing ui_config
        analytics_result.pop("ui_config", None)
        try:
            ui_config = analytics_auto_generator.generate_complete_analytics(
                analytics_result
            )

            analytics_result["ui_config"] = ui_config
            analytics_result["has_advanced_analytics"] = True

            self.logger.info(
                f"✅ Generated UI config: "
                f"{len(ui_config.get('hero_metrics', []))} metrics, "
                f"{len(ui_config.get('charts', []))} charts, "
                f"{len(ui_config.get('tables', []))} tables, "
                f"{len(ui_config.get('sections', []))} sections"
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to generate UI config: {e}", exc_info=True)
            analytics_result["has_advanced_analytics"] = False

        return analytics_result
