# app/services/registry/loader.py

from app.services.registry.registry_api import discover_and_register, DOCUMENT_REGISTRY
import logging

logger = logging.getLogger(__name__)

def load_registry(parser_pkg: str = "app.services.parsers", analyzer_pkg: str = "app.services.analyzers"):
    # import all parser modules (they register themselves)
    discover_and_register(parser_pkg)
    discover_and_register(analyzer_pkg)
    logger.info("Final DOCUMENT_REGISTRY contains %d types", len(DOCUMENT_REGISTRY))
    return DOCUMENT_REGISTRY
