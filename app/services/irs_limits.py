"""
IRS Limits Loader
==================
Reads contribution limits and SS wage bases from irs_limits.json.
Zero hardcoding in source — all values live in the JSON config file.

To add a new tax year (each October when IRS publishes):
  1. Open resources/irs_limits.json
  2. Add a new year block under "contribution_limits" and "ss_wage_base"
  3. Restart app — no code changes needed

Drop at: app/services/irs_limits.py
JSON at: app/resources/irs_limits.json  (or configured via IRS_LIMITS_PATH env var)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ─── Config file location ─────────────────────────────────────────────────────

def _find_limits_file() -> Path:
    """
    Search for irs_limits.json in order of preference:
      1. IRS_LIMITS_PATH environment variable (override for testing)
      2. app/resources/ (bundled with app — production)
      3. Same directory as this file (development)
    """
    import os

    # Env override
    env_path = os.environ.get("IRS_LIMITS_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    # Walk up from this file looking for app/resources/
    here = Path(__file__).resolve().parent
    for parent in [here, here.parent, here.parent.parent]:
        candidate = parent / "resources" / "irs_limits.json"
        if candidate.exists():
            return candidate

    # Same directory fallback
    same_dir = here / "irs_limits.json"
    if same_dir.exists():
        return same_dir

    raise FileNotFoundError(
        "irs_limits.json not found. Expected at app/resources/irs_limits.json "
        "or set IRS_LIMITS_PATH environment variable."
    )


# ─── In-process cache (load once per app run) ─────────────────────────────────

_loaded: Optional[Dict] = None

def _load() -> Dict:
    global _loaded
    if _loaded is not None:
        return _loaded

    path = _find_limits_file()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Normalize year keys to int
    data["contribution_limits"] = {
        int(k): v for k, v in data.get("contribution_limits", {}).items()
    }
    data["ss_wage_base"] = {
        int(k): v for k, v in data.get("ss_wage_base", {}).items()
    }

    _loaded = data
    logger.info(
        f"✅ IRS limits loaded from {path} — "
        f"years: {sorted(data['contribution_limits'].keys())} "
        f"(last_updated: {data.get('_last_updated', 'unknown')})"
    )
    return data


def reload() -> None:
    """Force reload from disk — call after updating irs_limits.json at runtime."""
    global _loaded
    _loaded = None
    _load()


# ─── Public API ───────────────────────────────────────────────────────────────

def get_limits(year: Optional[int] = None) -> Dict[str, float]:
    """
    Return IRS contribution limits for the given tax year.

    If the year is not in the config (e.g. a future year not yet announced),
    returns the most recent known year's limits with a warning.

    Args:
        year: Tax year (default: current calendar year)

    Returns:
        Dict with keys: 401k, 401k_catchup, ira, ira_catchup,
                        hsa_single, hsa_family, fsa, dependent_fsa
    """
    if year is None:
        year = datetime.utcnow().year

    data   = _load()
    limits = data["contribution_limits"]

    if year in limits:
        return dict(limits[year])

    # Not found — use nearest available year
    available = sorted(limits.keys())
    if not available:
        raise RuntimeError("irs_limits.json has no contribution_limits entries")

    nearest = max(y for y in available if y <= year) if any(y <= year for y in available) \
              else min(available)

    logger.warning(
        f"IRS limits for {year} not in config — using {nearest}. "
        f"Update app/resources/irs_limits.json to add {year} limits."
    )
    return dict(limits[nearest])


def get_ss_wage_limit(year: Optional[int] = None) -> Optional[float]:
    """
    Return the Social Security wage base for the given year.

    Args:
        year: Tax year (default: current calendar year)

    Returns:
        SS wage base as float, or None if not found
    """
    if year is None:
        year = datetime.utcnow().year

    data     = _load()
    ss_bases = data.get("ss_wage_base", {})

    if year in ss_bases:
        return float(ss_bases[year])

    available = sorted(ss_bases.keys())
    if not available:
        return None

    nearest = max(y for y in available if y <= year) if any(y <= year for y in available) \
              else min(available)

    logger.warning(
        f"SS wage base for {year} not in config — using {nearest} (${ss_bases[nearest]:,}). "
        f"Update app/resources/irs_limits.json to add {year} SS wage base."
    )
    return float(ss_bases[nearest])


def get_tax_rates() -> Dict[str, float]:
    """Return FICA tax rates (these rarely change — update JSON if they do)."""
    data = _load()
    return {
        "ss_tax_rate":                   data.get("ss_tax_rate", 0.062),
        "medicare_tax_rate":             data.get("medicare_tax_rate", 0.0145),
        "additional_medicare_rate":      data.get("additional_medicare_rate", 0.009),
        "additional_medicare_threshold": data.get("additional_medicare_threshold", 200_000),
    }


def available_years() -> list:
    """Return all years present in the config file."""
    data = _load()
    return sorted(data["contribution_limits"].keys())