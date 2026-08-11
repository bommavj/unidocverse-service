from app.services.analyzers.base_analyzer import BaseAnalyzer
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
import re
import logging
from datetime import datetime
from collections import defaultdict
from app.services.analytics_auto_generator import analytics_auto_generator

logger = logging.getLogger(__name__)

class TableBasedAnalyzer(BaseAnalyzer):
    """
    Base class for analyzers that work primarily with tabular data
    Provides table manipulation helpers
    """

    def find_column(self, table: Dict, keywords: List[str]) -> Optional[str]:
        """
        Find column header matching any of the keywords

        Args:
            table: Table dictionary with 'data' key
            keywords: List of keywords to search for (case-insensitive)

        Returns:
            Column name if found, None otherwise
        """
        data = table.get('data', [])
        if not data or len(data) < 1:
            return None

        headers = data[0]

        for header in headers:
            header_lower = str(header).lower()
            for keyword in keywords:
                if keyword.lower() in header_lower:
                    logger.debug(f"   🎯 Found column '{header}' matching '{keyword}'")
                    return header

        logger.debug(f"   ⏭️ No column found for keywords: {keywords}")
        return None

    def find_columns(self, table: Dict, keywords: List[str]) -> List[str]:
        """
        Find all columns matching any of the keywords

        Args:
            table: Table dictionary
            keywords: List of keywords to search for

        Returns:
            List of matching column names
        """
        data = table.get('data', [])
        if not data or len(data) < 1:
            return []

        headers = data[0]
        matching = []

        for header in headers:
            header_lower = str(header).lower()
            for keyword in keywords:
                if keyword.lower() in header_lower:
                    matching.append(header)
                    break

        return matching

    def get_column_data(self, table: Dict, column_name: str) -> List[Any]:
        """
        Extract all values from a specific column

        Args:
            table: Table dictionary
            column_name: Name of column to extract

        Returns:
            List of column values (excluding header)
        """
        data = table.get('data', [])
        if not data or len(data) < 2:
            return []

        headers = data[0]

        try:
            col_idx = headers.index(column_name)
            values = [row[col_idx] for row in data[1:] if len(row) > col_idx]
            logger.debug(f"   📊 Extracted {len(values)} values from column '{column_name}'")
            return values
        except (ValueError, IndexError) as e:
            logger.debug(f"   ⚠️ Column '{column_name}' not found: {e}")
            return []

    def get_row_count(self, table: Dict) -> int:
        """Get number of data rows (excluding header)"""
        data = table.get('data', [])
        return max(0, len(data) - 1)

    def get_column_count(self, table: Dict) -> int:
        """Get number of columns"""
        data = table.get('data', [])
        return len(data[0]) if data else 0

    def aggregate_column(self, table: Dict, column_name: str, operation: str = 'sum') -> Optional[float]:
        """
        Perform aggregation on numeric column

        Args:
            table: Table dictionary
            column_name: Column to aggregate
            operation: 'sum', 'avg', 'min', 'max', 'count'

        Returns:
            Aggregated value or None if column not numeric
        """
        values = self.get_column_data(table, column_name)

        if not values:
            return None

        # Convert to numeric, filter out non-numeric
        numeric_values = []
        for v in values:
            try:
                # Clean value (remove $, commas, etc.)
                cleaned = str(v).replace('$', '').replace(',', '').strip()
                numeric_values.append(float(cleaned))
            except (ValueError, AttributeError):
                continue

        if not numeric_values:
            return None

        if operation == 'sum':
            return sum(numeric_values)
        elif operation == 'avg' or operation == 'average':
            return sum(numeric_values) / len(numeric_values)
        elif operation == 'min':
            return min(numeric_values)
        elif operation == 'max':
            return max(numeric_values)
        elif operation == 'count':
            return len(numeric_values)
        else:
            logger.warning(f"⚠️ Unknown operation: {operation}")
            return None

    def group_by_column(self, table: Dict, group_column: str, value_column: str,
                        operation: str = 'sum') -> Dict[str, float]:
        """
        Group by one column and aggregate another

        Args:
            table: Table dictionary
            group_column: Column to group by
            value_column: Column to aggregate
            operation: Aggregation operation

        Returns:
            Dict mapping group_value -> aggregated_value
        """
        data = table.get('data', [])
        if not data or len(data) < 2:
            return {}

        headers = data[0]

        try:
            group_idx = headers.index(group_column)
            value_idx = headers.index(value_column)
        except ValueError:
            return {}

        # Group data
        groups = defaultdict(list)
        for row in data[1:]:
            if len(row) <= max(group_idx, value_idx):
                continue

            group_key = str(row[group_idx])

            try:
                value = float(str(row[value_idx]).replace('$', '').replace(',', '').strip())
                groups[group_key].append(value)
            except (ValueError, AttributeError):
                continue

        # Aggregate
        result = {}
        for key, values in groups.items():
            if operation == 'sum':
                result[key] = sum(values)
            elif operation in ['avg', 'average']:
                result[key] = sum(values) / len(values)
            elif operation == 'count':
                result[key] = len(values)
            elif operation == 'min':
                result[key] = min(values)
            elif operation == 'max':
                result[key] = max(values)

        return result