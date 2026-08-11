import yaml
from pathlib import Path
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class UniversalPatternLoader:
    """Load and manage document classification patterns from YAML"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            # Auto-detect config path
            config_path = Path(__file__).parent.parent / "config" / "document_patterns.yaml"

        self.config_path = Path(config_path)
        self.patterns = self._load_patterns()
        logger.info(f"✅ Loaded {len(self.patterns)} document pattern types")

    def _load_patterns(self) -> Dict:
        """Load patterns from YAML file"""
        try:
            if not self.config_path.exists():
                logger.error(f"❌ Config file not found: {self.config_path}")
                return {}

            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)

            patterns = config.get('patterns', {})
            logger.info(f"📋 Loaded patterns: {list(patterns.keys())}")
            return patterns

        except Exception as e:
            logger.error(f"❌ Failed to load patterns: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def get_all_types(self) -> List[str]:
        """Get all document types"""
        return list(self.patterns.keys())

    def get_pattern(self, doc_type: str) -> Dict:
        """Get pattern for specific document type"""
        return self.patterns.get(doc_type, {})

    def reload(self):
        """Reload patterns from file (hot-reload support)"""
        self.patterns = self._load_patterns()
        logger.info(f"🔄 Reloaded {len(self.patterns)} pattern types")


# Global instance (singleton)
_pattern_loader = None


def get_pattern_loader() -> UniversalPatternLoader:
    """Get global pattern loader instance"""
    global _pattern_loader
    if _pattern_loader is None:
        _pattern_loader = UniversalPatternLoader()
    return _pattern_loader