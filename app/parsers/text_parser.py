"""
Text file parser for plain text, markdown, CSV, JSON, XML
"""

import logging
from pathlib import Path
from typing import Dict, Any
import json
import csv
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


class TextParser:
    """Parse text-based files"""

    SUPPORTED_FORMATS = {
        '.txt', '.md', '.markdown',
        '.csv', '.tsv',
        '.json', '.jsonl',
        '.xml',
        '.log', '.yaml', '.yml',
        '.ini', '.conf', '.cfg'
    }

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the file"""
        ext = Path(filename).suffix.lower()
        return ext in self.SUPPORTED_FORMATS

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """Parse text file"""

        logger.info(f"📝 Parsing text file: {filename}")

        ext = Path(filename).suffix.lower()

        try:
            if ext in ['.csv', '.tsv']:
                return self._parse_csv(file_path, ext)
            elif ext in ['.json', '.jsonl']:
                return self._parse_json(file_path)
            elif ext == '.xml':
                return self._parse_xml(file_path)
            else:
                return self._parse_plain_text(file_path, ext)

        except Exception as e:
            logger.error(f"❌ Error parsing text file: {e}")
            return {
                'text': '',
                'metadata': {'error': str(e)},
                'confidence': 0.0
            }

    def _parse_plain_text(self, file_path: str, ext: str) -> Dict[str, Any]:
        """Parse plain text files"""

        # Try different encodings
        encodings = ['utf-8', 'latin-1', 'cp1252']

        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    text = f.read()

                logger.info(f"✅ Text file parsed: {len(text)} chars (encoding: {encoding})")

                return {
                    'text': text,
                    'metadata': {
                        'encoding': encoding,
                        'file_type': ext.lstrip('.'),
                        'line_count': text.count('\n') + 1
                    },
                    'confidence': 0.95
                }
            except UnicodeDecodeError:
                continue

        # If all encodings fail
        logger.warning("⚠️ Could not decode text file")
        return {
            'text': '',
            'metadata': {'error': 'Could not decode file'},
            'confidence': 0.0
        }

    def _parse_csv(self, file_path: str, ext: str) -> Dict[str, Any]:
        """Parse CSV/TSV files"""

        delimiter = '\t' if ext == '.tsv' else ','

        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows = list(reader)

        if not rows:
            return {'text': '', 'metadata': {}, 'confidence': 0.0}

        # First row as headers
        headers = rows[0]
        data_rows = rows[1:]

        # Create table structure
        table = {
            'columns': headers,
            'rows': len(data_rows),
            'data': rows
        }

        # Create text representation
        text_parts = [
            f"CSV Data: {len(data_rows)} rows × {len(headers)} columns",
            "",
            "Columns: " + ", ".join(headers),
            ""
        ]

        # Add sample rows
        for i, row in enumerate(data_rows[:10], 1):
            text_parts.append(f"Row {i}: " + " | ".join(row))

        if len(data_rows) > 10:
            text_parts.append(f"\n... and {len(data_rows) - 10} more rows")

        full_text = '\n'.join(text_parts)

        logger.info(f"✅ CSV parsed: {len(data_rows)} rows × {len(headers)} columns")

        return {
            'text': full_text,
            'tables': [table],
            'metadata': {
                'row_count': len(data_rows),
                'column_count': len(headers),
                'columns': headers
            },
            'confidence': 0.95
        }

    def _parse_json(self, file_path: str) -> Dict[str, Any]:
        """Parse JSON files"""

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Convert to text representation
        text = json.dumps(data, indent=2)

        logger.info(f"✅ JSON parsed: {len(text)} chars")

        return {
            'text': text,
            'metadata': {
                'json_keys': list(data.keys()) if isinstance(data, dict) else [],
                'data_type': type(data).__name__
            },
            'confidence': 0.90
        }

    def _parse_xml(self, file_path: str) -> Dict[str, Any]:
        """Parse XML files"""

        tree = ET.parse(file_path)
        root = tree.getroot()

        # Convert to text
        def xml_to_text(element, level=0):
            parts = []
            indent = "  " * level

            if element.text and element.text.strip():
                parts.append(f"{indent}{element.tag}: {element.text.strip()}")
            else:
                parts.append(f"{indent}{element.tag}")

            for child in element:
                parts.append(xml_to_text(child, level + 1))

            return '\n'.join(parts)

        text = xml_to_text(root)

        logger.info(f"✅ XML parsed: {len(text)} chars")

        return {
            'text': text,
            'metadata': {
                'root_tag': root.tag,
                'child_count': len(root)
            },
            'confidence': 0.90
        }