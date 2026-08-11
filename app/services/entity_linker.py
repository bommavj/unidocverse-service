import logging
import re
import yaml
from pathlib import Path
from typing import Dict, List, Set
from collections import defaultdict

from app.core.database import SessionLocal
from app.models.document import Document

logger = logging.getLogger(__name__)


class EntityLinker:
    """
    Links documents by extracting shared entities (Order IDs, Emails, etc.)
    Creates a knowledge graph of document relationships
    """

    def __init__(self, patterns_file: str = None):
        if patterns_file is None:
            patterns_file = Path(__file__).parent.parent / "config" / "document_patterns.yaml"

        with open(patterns_file, 'r') as f:
            config = yaml.safe_load(f)

        self.entity_patterns = config.get('entity_patterns', {})
        logger.info(f"✅ Loaded {len(self.entity_patterns)} entity extraction patterns")

    def extract_entities(self, text: str, entity_types: List[str]) -> Dict[str, List[str]]:
        """
        Extract specified entity types from text

        Args:
            text: Document text
            entity_types: List of entity types to extract

        Returns:
            Dict mapping entity_type -> list of found values
        """
        entities = {}

        for entity_type in entity_types:
            if entity_type not in self.entity_patterns:
                continue

            pattern_config = self.entity_patterns[entity_type]
            patterns = pattern_config.get('patterns', [])

            found_values = set()
            for pattern in patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for match in matches:
                    if match.groups():
                        found_values.add(match.group(1))
                    else:
                        found_values.add(match.group(0))

            if found_values:
                entities[entity_type] = list(found_values)

        return entities

    def link_documents(self, document_id: str, entities: Dict[str, List[str]]) -> List[str]:
        """
        Find related documents that share entities

        Returns:
            List of related document IDs
        """
        if not entities:
            return []

        db = SessionLocal()
        related_ids = set()

        try:
            # Search for documents with matching entities
            all_docs = db.query(Document).filter(Document.id != document_id).all()

            for doc in all_docs:
                doc_entities = doc.doc_metadata.get('extracted_entities', {})

                # Check for entity overlap
                for entity_type, values in entities.items():
                    doc_values = doc_entities.get(entity_type, [])

                    # If any value matches, it's related
                    if set(values) & set(doc_values):
                        related_ids.add(doc.id)
                        logger.info(f"🔗 Linked {document_id} → {doc.id} via {entity_type}")

            return list(related_ids)

        finally:
            db.close()

    def save_entity_links(self, document_id: str, entities: Dict, related_ids: List[str]):
        """Save extracted entities and links to database"""
        db = SessionLocal()

        try:
            doc = db.query(Document).filter(Document.id == document_id).first()

            if doc:
                if not doc.doc_metadata:
                    doc.doc_metadata = {}

                doc.doc_metadata['extracted_entities'] = entities
                doc.doc_metadata['related_documents'] = related_ids
                doc.doc_metadata['relationship_count'] = len(related_ids)

                db.commit()
                logger.info(f"✅ Saved {len(entities)} entity types, {len(related_ids)} links")

        finally:
            db.close()


def get_entity_linker() -> EntityLinker:
    return EntityLinker()