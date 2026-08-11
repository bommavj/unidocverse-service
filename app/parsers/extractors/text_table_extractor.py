import re
from typing import List, Dict


class TextTableExtractor:
    """
    Converts unstructured text into inferred tables
    """

    def extract(self, text: str) -> List[Dict]:
        tables = []

        # 1. Pipe or CSV-like tables
        tables.extend(self._extract_delimited_tables(text))

        # 2. Key-value blocks
        tables.extend(self._extract_key_value_tables(text))

        # 3. Space-aligned tables (OCR/PDF)
        tables.extend(self._extract_aligned_tables(text))

        return tables

    def _extract_delimited_tables(self, text: str) -> List[Dict]:
        tables = []
        lines = [l for l in text.splitlines() if '|' in l or ',' in l]

        if len(lines) < 2:
            return tables

        delimiter = '|' if '|' in lines[0] else ','

        rows = [list(map(str.strip, line.split(delimiter))) for line in lines]

        headers = rows[0]
        data = rows[1:]

        tables.append({
            "type": "delimited",
            "headers": headers,
            "rows": data
        })

        return tables

    def _extract_key_value_tables(self, text: str) -> List[Dict]:
        pattern = r'^(.+?):\s*(.+)$'
        rows = []

        for line in text.splitlines():
            match = re.match(pattern, line.strip())
            if match:
                rows.append([match.group(1), match.group(2)])

        if len(rows) < 2:
            return []

        return [{
            "type": "key_value",
            "headers": ["Key", "Value"],
            "rows": rows
        }]

    def _extract_aligned_tables(self, text: str) -> List[Dict]:
        tables = []
        lines = [l for l in text.splitlines() if re.search(r'\s{2,}', l)]

        if len(lines) < 2:
            return tables

        rows = [re.split(r'\s{2,}', l.strip()) for l in lines]

        headers = rows[0]
        data = rows[1:]

        tables.append({
            "type": "aligned",
            "headers": headers,
            "rows": data
        })

        return tables
