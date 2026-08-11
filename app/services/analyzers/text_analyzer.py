from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
import re
import logging
from datetime import datetime
from collections import defaultdict
from app.services.analytics_auto_generator import analytics_auto_generator
from app.services.analyzers.base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class TextBasedAnalyzer(BaseAnalyzer):
    """
    Base class for analyzers that work primarily with text content
    Provides text extraction and pattern matching helpers
    """

    def extract_dates(self, text: str, limit: int = 10) -> List[str]:
        """
        Extract dates from text

        Args:
            text: Text to search
            limit: Maximum number of dates to return

        Returns:
            List of date strings found
        """
        date_patterns = [
            r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',  # MM/DD/YYYY or MM-DD-YY
            r'\d{4}[/-]\d{1,2}[/-]\d{1,2}',  # YYYY-MM-DD
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}',  # Month DD, YYYY
            r'\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}'  # DD Month YYYY
        ]

        dates = []
        for pattern in date_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            dates.extend(matches)

        # Return unique dates, limited
        unique_dates = list(dict.fromkeys(dates))[:limit]
        logger.debug(f"   📅 Extracted {len(unique_dates)} dates")
        return unique_dates

    def extract_amounts(self, text: str, limit: int = 20) -> List[float]:
        """
        Extract monetary amounts from text

        Args:
            text: Text to search
            limit: Maximum number of amounts to return

        Returns:
            List of monetary values found
        """
        pattern = r'\$\s*[\d,]+\.?\d*'
        matches = re.findall(pattern, text)

        amounts = []
        for match in matches:
            try:
                amount = float(match.replace('$', '').replace(',', '').strip())
                amounts.append(amount)
            except ValueError:
                continue

        logger.debug(f"   💰 Extracted {len(amounts)} amounts")
        return amounts[:limit]

    def extract_emails(self, text: str) -> List[str]:
        """
        Extract email addresses from text

        Returns:
            List of email addresses found
        """
        pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(pattern, text)

        logger.debug(f"   📧 Extracted {len(emails)} emails")
        return list(set(emails))  # Unique emails

    def extract_phone_numbers(self, text: str) -> List[str]:
        """
        Extract phone numbers from text

        Returns:
            List of phone numbers found
        """
        patterns = [
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # 555-555-5555
            r'\(\d{3}\)\s*\d{3}[-.]?\d{4}',  # (555) 555-5555
            r'\+\d{1,3}\s?\d{3,}[-.\s]?\d{3,}'  # International
        ]

        phones = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            phones.extend(matches)

        logger.debug(f"   📞 Extracted {len(phones)} phone numbers")
        return list(set(phones))

    def extract_urls(self, text: str) -> List[str]:
        """
        Extract URLs from text

        Returns:
            List of URLs found
        """
        pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(pattern, text)

        logger.debug(f"   🔗 Extracted {len(urls)} URLs")
        return list(set(urls))

    def extract_patterns(self, text: str, pattern: str, group: int = 0) -> List[str]:
        """
        Extract custom regex patterns from text

        Args:
            text: Text to search
            pattern: Regex pattern
            group: Capture group to extract (0 for full match)

        Returns:
            List of matches
        """
        matches = re.finditer(pattern, text, re.IGNORECASE)

        results = []
        for match in matches:
            if group > 0 and len(match.groups()) >= group:
                results.append(match.group(group))
            else:
                results.append(match.group(0))

        return results

    def find_section(self, text: str, section_name: str,
                     end_markers: Optional[List[str]] = None) -> Optional[str]:
        """
        Extract text from a specific section

        Args:
            text: Full document text
            section_name: Section header to find
            end_markers: Optional list of patterns that mark section end

        Returns:
            Section text or None
        """
        # Find section start
        section_pattern = rf'{re.escape(section_name)}[:\s]*\n'
        match = re.search(section_pattern, text, re.IGNORECASE)

        if not match:
            return None

        start_pos = match.end()

        # Find section end
        if end_markers:
            end_positions = []
            for marker in end_markers:
                end_match = re.search(marker, text[start_pos:], re.IGNORECASE)
                if end_match:
                    end_positions.append(start_pos + end_match.start())

            if end_positions:
                end_pos = min(end_positions)
                return text[start_pos:end_pos].strip()

        # Return rest of document if no end marker found
        return text[start_pos:].strip()

    def count_keywords(self, text: str, keywords: List[str]) -> Dict[str, int]:
        """
        Count occurrences of keywords in text

        Args:
            text: Text to search
            keywords: List of keywords

        Returns:
            Dict mapping keyword -> count
        """
        text_lower = text.lower()
        counts = {}

        for keyword in keywords:
            count = len(re.findall(r'\b' + re.escape(keyword.lower()) + r'\b', text_lower))
            if count > 0:
                counts[keyword] = count

        return counts

    def split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences"""
        # Simple sentence splitter
        sentences = re.split(r'[.!?]+\s+', text)
        return [s.strip() for s in sentences if len(s.strip()) > 10]

    def get_word_count(self, text: str) -> int:
        """Get word count"""
        words = re.findall(r'\b\w+\b', text)
        return len(words)


