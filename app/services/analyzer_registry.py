"""
Analyzer Registry - Maps document types to analyzers
"""
from typing import Dict, Type, Optional
from app.services.analyzers.base_analyzer import BaseAnalyzer
from app.services.analyzers.financial.sales_analyzer import SalesAnalyzer
from app.services.analyzers.financial.bank_statement_analyzer import BankStatementAnalyzer
from app.services.analyzers.generic_analyzer import GenericAnalyzer

import logging

logger = logging.getLogger(__name__)

from app.services.document_registry import DOCUMENT_REGISTRY


def get_analyzer(document_type: str, ollama_client=None) -> BaseAnalyzer:
    """
    Unified analyzer routing using DOCUMENT_REGISTRY.
    Passes ollama_client only to analyzers that accept it.
    """
    import inspect

    def _instantiate(cls):
        sig = inspect.signature(cls.__init__)
        if "ollama_client" in sig.parameters:
            return cls(ollama_client=ollama_client)
        return cls()

    entry = DOCUMENT_REGISTRY.get(document_type)
    if entry and "analyzer" in entry:
        analyzer_class = entry["analyzer"]
        logger.info(f"📘 Analyzer selected from DOCUMENT_REGISTRY: {analyzer_class.__name__}")
        return _instantiate(analyzer_class)

    logger.info(f"⚠️ No analyzer in DOCUMENT_REGISTRY for {document_type}, using GenericAnalyzer")
    return _instantiate(GenericAnalyzer)


class AnalyzerRegistry:
    pass
