"""
Universal JSON serialization utilities
Safely converts any Python object to JSON-serializable format
"""

import logging
from datetime import datetime, date, time
from decimal import Decimal
from typing import Any, Dict, List
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def make_json_serializable(obj: Any) -> Any:
    """
    Recursively convert any Python object to JSON-serializable format.

    Handles:
    - Pandas types (Timestamp, NaT, Series, DataFrame)
    - NumPy types (int64, float64, bool_, ndarray)
    - Python datetime objects
    - Decimal objects
    - Complex nested structures (dicts, lists)
    - Unknown types (converts to string)

    Args:
        obj: Any Python object

    Returns:
        JSON-serializable version of the object
    """

    # Handle None first
    if obj is None:
        return None

    # Handle NumPy arrays BEFORE pd.isna check
    if isinstance(obj, np.ndarray):
        return [make_json_serializable(item) for item in obj.tolist()]

    # Handle pandas Series/DataFrame BEFORE pd.isna check
    if isinstance(obj, pd.Series):
        return [make_json_serializable(item) for item in obj.tolist()]

    if isinstance(obj, pd.DataFrame):
        return [make_json_serializable(row) for row in obj.to_dict('records')]

    # Now safe to check pd.isna for scalar values
    try:
        if pd.isna(obj):
            return None
    except (ValueError, TypeError):
        # pd.isna fails on some objects, that's ok
        pass

    # Handle datetime-like objects
    if isinstance(obj, (pd.Timestamp, datetime, date, time)):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return str(obj)

    # Handle NumPy scalar types
    if isinstance(obj, (np.integer, np.signedinteger)):
        return int(obj)

    if isinstance(obj, (np.floating, np.complexfloating)):
        value = float(obj)
        # Handle NaN and Inf
        if np.isnan(value) or np.isinf(value):
            return None
        return value

    if isinstance(obj, np.bool_):
        return bool(obj)

    # Handle Decimal
    if isinstance(obj, Decimal):
        return float(obj)

    # Handle dictionaries - recursively clean all values
    if isinstance(obj, dict):
        cleaned = {}
        for key, value in obj.items():
            # Convert key to string
            try:
                clean_key = str(key)
            except:
                clean_key = "unknown_key"

            # Clean value
            cleaned[clean_key] = make_json_serializable(value)

        return cleaned

    # Handle lists/tuples - recursively clean all items
    if isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]

    # Handle sets
    if isinstance(obj, set):
        return [make_json_serializable(item) for item in obj]

    # Handle basic JSON-serializable types
    if isinstance(obj, (str, int, float, bool)):
        # Check for NaN in floats
        if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj

    # Handle bytes
    if isinstance(obj, bytes):
        try:
            return obj.decode('utf-8')
        except:
            return str(obj)

    # Fallback: convert unknown types to string
    try:
        str_value = str(obj)
        # Avoid giant string representations
        if len(str_value) > 1000:
            return f"{str_value[:1000]}... (truncated)"
        return str_value
    except Exception as e:
        logger.warning(f"Could not serialize object of type {type(obj)}: {e}")
        return None


def clean_metadata_for_json(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean entire metadata dictionary for JSON serialization.

    Args:
        metadata: Dictionary that may contain non-serializable objects

    Returns:
        Clean dictionary ready for JSON serialization
    """
    if not isinstance(metadata, dict):
        logger.warning(f"Expected dict, got {type(metadata)}")
        return {}

    return make_json_serializable(metadata)


def validate_json_serializable(obj: Any) -> bool:
    """
    Test if an object is JSON serializable.

    Args:
        obj: Object to test

    Returns:
        True if serializable, False otherwise
    """
    import json
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError) as e:
        logger.warning(f"Object not JSON serializable: {e}")
        return False