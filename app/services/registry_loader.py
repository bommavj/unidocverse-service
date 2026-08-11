# app/services/registry_loader.py

import pkgutil
import importlib
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def discover_modules(package_name: str):
    """Yield module names under a package (non-recursive)."""
    package = importlib.import_module(package_name)
    prefix = package.__name__ + "."
    for finder, name, ispkg in pkgutil.iter_modules(package.__path__, prefix):
        yield name

def load_document_registry(parser_pkg: str = "app.services.parsers", analyzer_pkg: str = "app.services.analyzers") -> Dict[str, Dict[str, Any]]:
    """
    Auto-discover modules that export DOCUMENT_ENTRY and build DOCUMENT_REGISTRY.
    Each domain module should export DOCUMENT_ENTRY = { "doc_type": "...", "parser": ParserClass, "analyzer": AnalyzerClass, ... }
    """
    registry: Dict[str, Dict[str, Any]] = {}

    # discover parser modules
    for module_name in discover_modules(parser_pkg):
        try:
            mod = importlib.import_module(module_name)
            entry = getattr(mod, "DOCUMENT_ENTRY", None)
            if entry:
                doc_type = entry.get("doc_type")
                if doc_type:
                    registry.setdefault(doc_type, {}).update({ "parser": entry.get("parser"), **{k:v for k,v in entry.items() if k not in ('doc_type','parser')} })
                    logger.info("Registered parser for %s from %s", doc_type, module_name)
        except Exception as e:
            logger.exception("Failed to import parser module %s: %s", module_name, e)

    # discover analyzer modules
    for module_name in discover_modules(analyzer_pkg):
        try:
            mod = importlib.import_module(module_name)
            entry = getattr(mod, "DOCUMENT_ENTRY", None)
            if entry:
                doc_type = entry.get("doc_type")
                if doc_type:
                    registry.setdefault(doc_type, {}).update({ "analyzer": entry.get("analyzer"), **{k:v for k,v in entry.items() if k not in ('doc_type','analyzer')} })
                    logger.info("Registered analyzer for %s from %s", doc_type, module_name)
        except Exception as e:
            logger.exception("Failed to import analyzer module %s: %s", module_name, e)

    logger.info("DOCUMENT_REGISTRY loaded with %d entries", len(registry))
    return registry
