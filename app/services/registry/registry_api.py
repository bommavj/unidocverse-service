# app/services/registry/registry_api.py

from typing import Dict, Any
import importlib
import pkgutil
import logging

logger = logging.getLogger(__name__)
DOCUMENT_REGISTRY: Dict[str, Dict[str, Any]] = {}

def register_document_entry(entry: Dict[str, Any]):
    """
    Called by domain modules to register themselves.
    entry must include: doc_type (str), parser (class), analyzer (class), version (optional), tags (optional)
    """
    doc_type = entry.get("doc_type")
    if not doc_type:
        raise ValueError("DOCUMENT_ENTRY must include 'doc_type'")
    existing = DOCUMENT_REGISTRY.get(doc_type)
    if existing:
        # merge with version awareness; prefer higher version or explicit override
        logger.info("Merging registry entry for %s", doc_type)
        existing.update(entry)
    else:
        DOCUMENT_REGISTRY[doc_type] = entry
    logger.info("Registered %s -> parser=%s analyzer=%s", doc_type, entry.get("parser"), entry.get("analyzer"))

def discover_and_register(package_name: str):
    """Import modules under package_name so they can call register_document_entry on import."""
    package = importlib.import_module(package_name)
    prefix = package.__name__ + "."
    for finder, name, ispkg in pkgutil.iter_modules(package.__path__, prefix):
        try:
            importlib.import_module(name)
        except Exception:
            logger.exception("Failed to import module %s during discovery", name)
