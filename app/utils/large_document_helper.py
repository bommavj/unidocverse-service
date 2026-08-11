import re
import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

class LargeDocumentScanner:
    """
    Utility to safely and efficiently scan very large documents (e.g. 1000+ pages / megabytes of text).
    Splits text into overlapping chunks to prevent catastrophic regex backtracking and high memory usage.
    """

    def __init__(self, text: str, chunk_size: int = 20000, overlap: int = 2000):
        self.text = text or ""
        self.chunk_size = chunk_size
        self.overlap = overlap

    def get_chunks(self):
        """Generator yielding overlapping chunks of the document text."""
        length = len(self.text)
        if length <= self.chunk_size:
            yield self.text
            return

        start = 0
        while start < length:
            end = min(length, start + self.chunk_size)
            yield self.text[start:end]
            if end == length:
                break
            start += self.chunk_size - self.overlap

    def find_regex(self, pattern: str, default: str = "") -> str:
        """
        Runs a regex search over overlapping chunks. 
        Returns the first match group 1, or default if not found.
        """
        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            for chunk in self.get_chunks():
                match = compiled.search(chunk)
                if match:
                    # Return group 1 if it exists, otherwise full match
                    return match.group(1).strip() if match.groups() else match.group(0).strip()
        except Exception as e:
            logger.error(f"Error in LargeDocumentScanner.find_regex: {e}")
        return default

    def extract_context(self, keyword_pattern: str, window_before: int = 50, window_after: int = 200) -> Optional[str]:
        """
        Finds the first occurrence of a keyword pattern and returns its surrounding text window context.
        """
        try:
            compiled = re.compile(keyword_pattern, re.IGNORECASE)
            for chunk in self.get_chunks():
                match = compiled.search(chunk)
                if match:
                    start = max(0, match.start() - window_before)
                    end = min(len(chunk), match.end() + window_after)
                    return chunk[start:end].strip()
        except Exception as e:
            logger.error(f"Error in LargeDocumentScanner.extract_context: {e}")
        return None

    def find_all_numbers(self, pattern: str) -> List[float]:
        """
        Finds all numbers matching a pattern (e.g. prices or lab values) across the document.
        """
        numbers = []
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            for chunk in self.get_chunks():
                matches = compiled.finditer(chunk)
                for m in matches:
                    val_str = m.group(1) if m.groups() else m.group(0)
                    try:
                        # Clean and convert to float
                        cleaned = re.sub(r'[^\d\.\-]', '', val_str)
                        if cleaned:
                            numbers.append(float(cleaned))
                    except ValueError:
                        continue
        except Exception as e:
            logger.error(f"Error in LargeDocumentScanner.find_all_numbers: {e}")
        return numbers
