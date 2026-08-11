import yaml
from pathlib import Path
from typing import Dict, Any, List, Tuple
import pandas as pd
import re
import logging

logger = logging.getLogger(__name__)


class SpecialistRouter:
    """
    Routes documents to Math Path (Pandas) or Logic Path (AI)
    Based on document type and content structure
    """

    def __init__(self, patterns_file: str = None):
        if patterns_file is None:
            # ✅ Use same path as pattern_loader
            patterns_file = Path(__file__).parent.parent / "config" / "document_patterns.yaml"

        self.config_path = Path(patterns_file)

        # ✅ Load config
        try:
            with open(self.config_path, 'r') as f:
                self.config = yaml.safe_load(f)

            self.document_types = self.config.get('patterns', {})  # ✅ Use 'patterns' key
            self.entity_patterns = self.config.get('entity_patterns', {})

            logger.info(f"✅ Specialist Router loaded {len(self.document_types)} document types")
            logger.info(f"✅ Specialist Router loaded {len(self.entity_patterns)} entity patterns")

        except Exception as e:
            logger.error(f"❌ Failed to load specialist router config: {e}")
            self.document_types = {}
            self.entity_patterns = {}

    def route_document(self, doc_type: str, metadata: Dict) -> str:
        """
        Determine processing path: math, logic, or hybrid

        Returns: "math", "logic", or "hybrid"
        """
        doc_config = self.document_types.get(doc_type, {})
        path = doc_config.get('processing_path', 'logic')

        # Override based on content
        tables = metadata.get('tables', [])
        has_numbers = self._has_numeric_data(tables)

        logger.info(f"🔀 Routing {doc_type}: path={path}, has_numbers={has_numbers}")

        if path == 'math' and not has_numbers:
            logger.warning(f"⚠️ {doc_type} marked as math but no numeric data, using logic")
            return 'logic'

        if path == 'logic' and has_numbers and len(tables) > 0:
            logger.info(f"💡 {doc_type} has tables, upgrading to hybrid")
            return 'hybrid'

        return path

    def _has_numeric_data(self, tables: List[Dict]) -> bool:
        """Check if tables contain numeric columns"""
        if not tables:
            return False

        for table in tables[:1]:  # Check first table
            data = table.get('data', [])
            if len(data) < 2:
                continue

            # Check if any column has numbers
            for col_idx in range(len(data[0])):
                sample_values = [row[col_idx] for row in data[1:6] if len(row) > col_idx]
                numeric_count = sum(1 for v in sample_values if self._is_numeric(v))
                if numeric_count >= 3:  # At least 3 numeric values
                    return True

        return False

    def _is_numeric(self, value) -> bool:
        """Check if value is numeric"""
        if value is None:
            return False

        try:
            float(str(value).replace(',', '').replace('$', ''))
            return True
        except (ValueError, AttributeError):
            return False

    def get_math_operations(self, doc_type: str) -> List[Dict]:
        """Get math operations for this document type"""
        doc_config = self.document_types.get(doc_type, {})
        operations = doc_config.get('math_operations', [])

        logger.info(f"📊 Math operations for {doc_type}: {operations}")

        return operations

    def get_entities_to_extract(self, doc_type: str) -> List[str]:
        """Get entity types to extract for linking"""
        doc_config = self.document_types.get(doc_type, {})
        entities = doc_config.get('entities_to_extract', [])

        logger.info(f"📎 Entities to extract for {doc_type}: {entities}")

        return entities


# Global instance
_router = None


def get_router() -> SpecialistRouter:
    global _router
    if _router is None:
        _router = SpecialistRouter()
    return _router