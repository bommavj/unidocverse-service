# app/services/parsers/base_parser.py

import re
from datetime import datetime
from typing import Optional, Dict, Any, List


class BaseParser:
    """
    Minimal base parser with common helpers used by Universal*Parser classes.
    Parsers should subclass this and implement parse() returning a dict.
    """

    def __init__(self, file_path: str, text: Optional[str] = None, raw_bytes: Optional[bytes] = None):
        self.file_path = file_path
        self.text = (text or "").strip()
        self.raw_bytes = raw_bytes

    # ---------- Text helpers ----------

    def normalize_whitespace(self, s: Optional[str]) -> str:
        if not s:
            return ""
        return re.sub(r"\s+", " ", s).strip()

    def find_first(self, patterns: List[str]) -> Optional[str]:
        """Return first regex group match for any pattern in patterns."""
        for p in patterns:
            m = re.search(p, self.text, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
        return None

    def find_all_blocks(self, start_regex: str, end_regex: Optional[str] = None) -> List[str]:
        """
        Extract blocks of text starting at start_regex until end_regex or blank line.
        Returns list of normalized blocks.
        """
        blocks = []
        for m in re.finditer(start_regex, self.text, flags=re.IGNORECASE | re.MULTILINE):
            start = m.end()
            if end_regex:
                end_m = re.search(end_regex, self.text[start:], flags=re.IGNORECASE | re.MULTILINE)
                end = start + (end_m.start() if end_m else len(self.text[start:]))
            else:
                # stop at two consecutive newlines or end of doc
                end_m = re.search(r"\n\s*\n", self.text[start:])
                end = start + (end_m.start() if end_m else len(self.text[start:]))
            block = self.text[start:end].strip()
            blocks.append(self.normalize_whitespace(block))
        return blocks

    def parse_date(self, s: Optional[str]) -> Optional[str]:
        """Try to parse a date-like string and return ISO date string (YYYY-MM-DD) or None."""
        if not s:
            return None
        s = s.strip()
        # common formats
        fmts = ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y"]
        for f in fmts:
            try:
                dt = datetime.strptime(s, f)
                return dt.date().isoformat()
            except Exception:
                continue
        # fallback: try to extract yyyy or mm/dd/yyyy with regex
        m = re.search(r"(\d{4})", s)
        if m:
            try:
                year = int(m.group(1))
                return f"{year}-01-01"
            except Exception:
                return None
        return None

    def safe_float(self, v) -> float:
        try:
            if v is None:
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).replace(",", "").replace("$", "").strip()
            return float(s)
        except Exception:
            return 0.0

    def kv_pairs_near(self, key_regex: str, window_chars: int = 200) -> List[str]:
        """
        Find occurrences of key_regex and return the nearby text (window_chars) after the match.
        Useful for extracting short values following labels.
        """
        out = []
        for m in re.finditer(key_regex, self.text, flags=re.IGNORECASE):
            start = m.end()
            snippet = self.text[start:start + window_chars]
            out.append(self.normalize_whitespace(snippet))
        return out
