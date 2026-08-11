"""
Web/HTML parser for HTML files and web archives
"""

import logging
from pathlib import Path
from typing import Dict, Any
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class WebParser:
    """Parse HTML and web files"""

    SUPPORTED_FORMATS = {'.html', '.htm', '.mhtml', '.mht'}

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the file"""
        ext = Path(filename).suffix.lower()
        return ext in self.SUPPORTED_FORMATS

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """Parse HTML file and extract content"""

        logger.info(f"🌐 Parsing web file: {filename}")

        try:
            # Read file
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                html_content = f.read()

            # Parse with BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')

            # Extract metadata
            metadata = {}

            # Title
            title = soup.find('title')
            if title:
                metadata['title'] = title.get_text(strip=True)

            # Meta tags
            meta_description = soup.find('meta', attrs={'name': 'description'})
            if meta_description:
                metadata['description'] = meta_description.get('content', '')

            meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
            if meta_keywords:
                metadata['keywords'] = meta_keywords.get('content', '')

            # Remove script and style elements
            for script in soup(['script', 'style', 'nav', 'footer', 'header']):
                script.decompose()

            # Extract text
            text = soup.get_text(separator='\n', strip=True)

            # Clean up text
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)

            # Extract links
            links = []
            for a in soup.find_all('a', href=True):
                links.append({
                    'text': a.get_text(strip=True),
                    'href': a['href']
                })

            metadata['link_count'] = len(links)

            # Extract images
            images = []
            for img in soup.find_all('img'):
                images.append({
                    'src': img.get('src', ''),
                    'alt': img.get('alt', '')
                })

            metadata['image_count'] = len(images)

            # Extract tables
            tables = []
            for table_idx, table in enumerate(soup.find_all('table')):
                rows = []
                for tr in table.find_all('tr'):
                    cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                    if cells:
                        rows.append(cells)

                if rows:
                    tables.append({
                        'table_index': table_idx,
                        'data': rows
                    })

            metadata['table_count'] = len(tables)

            logger.info(
                f"✅ HTML parsed: {len(text)} chars, {len(links)} links, {len(images)} images, {len(tables)} tables")

            return {
                'text': text,
                'tables': tables,
                'metadata': metadata,
                'confidence': 0.90
            }

        except Exception as e:
            logger.error(f"❌ Error parsing HTML: {e}")
            import traceback
            logger.error(traceback.format_exc())

            return {
                'text': '',
                'metadata': {'error': str(e)},
                'confidence': 0.0
            }