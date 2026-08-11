import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class MathProcessor:
    """
    Privacy-first mathematical analysis using Pandas
    NO AI HALLUCINATION - Pure calculations
    """

    def process_table(self, table: Dict, operations: List[Dict]) -> Dict[str, Any]:
        """
        Process table with specified math operations

        Args:
            table: Normalized table dict
            operations: List of operations from patterns.yml

        Returns:
            Dict of calculated metrics
        """
        try:
            # Convert to DataFrame
            df = self._table_to_dataframe(table)

            if df.empty:
                logger.warning("⚠️ Empty DataFrame, skipping math operations")
                return {}

            results = {}

            # Execute each operation
            for op_dict in operations:
                for operation, columns in op_dict.items():
                    result = self._execute_operation(df, operation, columns)
                    if result is not None:
                        key = f"{operation}_{columns[0]}"
                        results[key] = result
                        logger.info(f"  ✅ {key} = {result}")

            logger.info(f"✅ Math Path: Calculated {len(results)} metrics")
            return results

        except Exception as e:
            logger.error(f"❌ Math processing error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def _table_to_dataframe(self, table: Dict) -> pd.DataFrame:
        """Convert normalized table to pandas DataFrame"""
        data = table.get('data', [])

        if not data or len(data) < 2:
            return pd.DataFrame()

        # First row is headers
        headers = data[0]
        rows = data[1:]

        df = pd.DataFrame(rows, columns=headers)

        # Convert numeric columns (fix deprecation warning)
        for col in df.columns:
            # Clean the column values
            cleaned = df[col].astype(str).str.replace(',', '').str.replace('$', '').str.strip()

            # Try to convert to numeric
            try:
                df[col] = pd.to_numeric(cleaned)
                logger.debug(f"  📊 Column '{col}' converted to numeric")
            except (ValueError, TypeError):
                # Keep as string if conversion fails
                logger.debug(f"  📝 Column '{col}' kept as text")
                pass

        return df

    def _execute_operation(self, df: pd.DataFrame, operation: str, columns: List[str]) -> Any:
        """Execute a math operation on specified columns"""
        try:
            # Find matching columns (numeric only)
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

            if not numeric_cols:
                logger.warning(f"⚠️ No numeric columns found in table")
                return None

            # Find columns matching keywords
            matching_cols = []
            for col in numeric_cols:
                col_lower = str(col).lower()
                for keyword in columns:
                    if keyword.lower() in col_lower:
                        matching_cols.append(col)
                        break

            if not matching_cols:
                logger.debug(f"  ⏭️ No columns match keywords: {columns}")
                return None

            # Use first matching column
            col = matching_cols[0]
            logger.debug(f"  🎯 Using column: {col}")

            # Execute operation
            if operation == 'sum':
                result = float(df[col].sum())
                return round(result, 2)

            elif operation == 'average' or operation == 'mean':
                result = float(df[col].mean())
                return round(result, 2)

            elif operation == 'count':
                result = int(df[col].count())
                return result

            elif operation == 'min':
                result = float(df[col].min())
                return round(result, 2)

            elif operation == 'max':
                result = float(df[col].max())
                return round(result, 2)

            elif operation == 'median':
                result = float(df[col].median())
                return round(result, 2)

            elif operation == 'std':
                result = float(df[col].std())
                return round(result, 2)

            elif operation == 'balance':
                # For bank statements: ending = beginning + net
                if len(matching_cols) >= 2:
                    # Last value of last matching column
                    result = float(df[matching_cols[-1]].iloc[-1])
                    return round(result, 2)
                else:
                    # Just return last value
                    result = float(df[col].iloc[-1])
                    return round(result, 2)

            else:
                logger.warning(f"⚠️ Unknown operation: {operation}")
                return None

        except Exception as e:
            logger.error(f"❌ Operation {operation} failed on {columns}: {e}")
            return None


# Global instance
_math_processor = None


def get_math_processor() -> MathProcessor:
    global _math_processor
    if _math_processor is None:
        _math_processor = MathProcessor()
    return _math_processor