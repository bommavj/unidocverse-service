"""
hierarchical_doc_parser.py
==========================
Structural document parser that builds a rich DocumentTree from any PDF.

Architecture:
  Layer 1: Structure extraction (TOC, headings, page index)
  Layer 2: Table parsing (pdfplumber with column detection)
  Layer 3: Key-Value extraction (field: value pairs)
  Layer 4: Section hierarchy (nested content tree)
  Layer 5: Insurance-specific field extraction

Output: DocumentTree — a deterministic, auditable representation
        used by Commission Reconciliation, Renewal Pipeline,
        Policy Gap Analysis, Loss Run Intelligence, and COI Automation.

Usage:
    parser = HierarchicalDocParser()
    tree   = parser.parse(file_path, filename, doc_type)

    # Direct field lookup — no vectors needed
    renewal_date  = tree.get_field("renewal_date")
    premium       = tree.get_field("premium_amount")
    coverage_a    = tree.get_section("coverage a")
    commissions   = tree.get_table("commission_statement")
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PageBlock:
    """A block of text on a specific page with font metadata."""
    page: int
    text: str
    font_size: float = 0.0
    font_name: str = ""
    is_bold: bool = False
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0


@dataclass
class TocEntry:
    """A Table of Contents entry."""
    title: str
    page: int
    level: int = 1
    section_id: str = ""


@dataclass
class DocumentSection:
    """A hierarchical section of the document."""
    title: str
    level: int
    page: int
    content: str = ""
    subsections: List["DocumentSection"] = field(default_factory=list)
    kv_pairs: Dict[str, str] = field(default_factory=dict)
    tables: List[Dict] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a key-value pair from this section."""
        return self.kv_pairs.get(key.lower(), default)


@dataclass
class StructuredTable:
    """A structured table with typed columns."""
    name: str
    page: int
    headers: List[str]
    rows: List[Dict[str, Any]]
    raw: List[List[str]]
    table_type: str = "generic"  # commission, coverage, loss_run, premium

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "page": self.page,
            "headers": self.headers,
            "rows": self.rows,
            "table_type": self.table_type,
            "row_count": len(self.rows),
        }


@dataclass
class DocumentTree:
    """
    The complete hierarchical representation of a document.
    Provides direct field lookup without vector search.
    """
    filename: str
    doc_type: str
    page_count: int

    # Structure
    toc: List[TocEntry] = field(default_factory=list)
    sections: List[DocumentSection] = field(default_factory=list)
    tables: List[StructuredTable] = field(default_factory=list)
    page_index: Dict[int, List[str]] = field(default_factory=dict)

    # Extracted fields (flat KV store)
    fields: Dict[str, Any] = field(default_factory=dict)

    # Raw text fallback
    raw_text: str = ""

    # Confidence
    confidence: float = 0.0
    parse_method: str = ""

    def get_field(self, key: str, default: Any = None) -> Any:
        """
        Direct field lookup — deterministic, no vectors.
        Checks: fields dict → section KV pairs → regex on raw text
        """
        key_lower = key.lower().strip()

        # 1. Check flat fields dict
        if key_lower in self.fields:
            return self.fields[key_lower]

        # 2. Check all section KV pairs
        for section in self.sections:
            val = section.kv_pairs.get(key_lower)
            if val:
                return val
            for sub in section.subsections:
                val = sub.kv_pairs.get(key_lower)
                if val:
                    return val

        # 3. Regex fallback on raw text
        pattern = rf'{re.escape(key_lower)}[:\s]+([^\n]+)'
        match = re.search(pattern, self.raw_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        return default

    def get_section(self, title: str) -> Optional[DocumentSection]:
        """Find a section by title (fuzzy match)."""
        title_lower = title.lower()
        for section in self.sections:
            if title_lower in section.title.lower():
                return section
            for sub in section.subsections:
                if title_lower in sub.title.lower():
                    return sub
        return None

    def get_table(self, name: str) -> Optional[StructuredTable]:
        """Find a table by name or type."""
        name_lower = name.lower()
        for table in self.tables:
            if name_lower in table.name.lower() or name_lower == table.table_type:
                return table
        return None

    def get_tables_by_type(self, table_type: str) -> List[StructuredTable]:
        return [t for t in self.tables if t.table_type == table_type]

    def to_dict(self) -> Dict:
        return {
            "filename": self.filename,
            "doc_type": self.doc_type,
            "page_count": self.page_count,
            "confidence": self.confidence,
            "parse_method": self.parse_method,
            "fields": self.fields,
            "toc": [{"title": e.title, "page": e.page, "level": e.level} for e in self.toc],
            "sections": [self._section_to_dict(s) for s in self.sections],
            "tables": [t.to_dict() for t in self.tables],
            "page_count_index": len(self.page_index),
        }

    def _section_to_dict(self, s: DocumentSection) -> Dict:
        return {
            "title": s.title,
            "level": s.level,
            "page": s.page,
            "content_length": len(s.content),
            "kv_pairs": s.kv_pairs,
            "subsection_count": len(s.subsections),
            "table_count": len(s.tables),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# HEADING DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

# Patterns that indicate section headings in insurance/financial documents
HEADING_PATTERNS = [
    # Numbered sections: "1.", "1.1", "Section 1", "Article I"
    (r'^(section|article|part|chapter)\s+[\dIVXivx]+[\.\s]', 1),
    (r'^\d+\.\s+[A-Z]', 1),
    (r'^\d+\.\d+\s+[A-Z]', 2),
    # ALL CAPS lines (common in declarations pages)
    (r'^[A-Z][A-Z\s\-&/]{8,}$', 1),
    # Title Case lines ending with colon
    (r'^[A-Z][A-Za-z\s\-&/]{4,}:$', 2),
]

INSURANCE_SECTION_KEYWORDS = {
    "declarations": 1, "coverage": 1, "exclusions": 1, "conditions": 1,
    "definitions": 1, "endorsements": 1, "schedule": 1, "summary": 1,
    "premium": 1, "insured": 1, "policy period": 1, "renewal": 1,
    "commission": 1, "loss": 1, "claims": 1, "deductible": 1,
    "liability": 1, "property": 1,
}


def _is_heading(text: str, font_size: float = 0.0, avg_font_size: float = 10.0,
                is_bold: bool = False) -> Tuple[bool, int]:
    """Returns (is_heading, level)."""
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 120:
        return False, 0

    # Font-based detection
    if font_size > 0 and avg_font_size > 0:
        ratio = font_size / avg_font_size
        if ratio >= 1.4:
            return True, 1
        if ratio >= 1.2:
            return True, 2
        if is_bold and ratio >= 1.0:
            return True, 2

    # Pattern-based detection
    for pattern, level in HEADING_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True, level

    # Insurance keyword detection
    text_lower = text.lower()
    for kw, level in INSURANCE_SECTION_KEYWORDS.items():
        if text_lower.startswith(kw) and len(text) < 60:
            return True, level

    return False, 0


# ═══════════════════════════════════════════════════════════════════════════════
# KEY-VALUE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

# Insurance-specific field patterns
INSURANCE_KV_PATTERNS = {
    # Policy identifiers
    "policy_number":    [r'policy\s*(?:number|no|#)[:\s]+([A-Z0-9\-]+)', r'pol\s*#[:\s]+([A-Z0-9\-]+)'],
    "account_number":   [r'account\s*(?:number|no|#)[:\s]+([A-Z0-9\-]+)'],
    "certificate_number": [r'cert(?:ificate)?\s*(?:number|no|#)[:\s]+([A-Z0-9\-]+)'],

    # Dates
    "effective_date":   [r'effective\s+date[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
                         r'eff(?:ective)?[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})'],
    "expiration_date":  [r'expir(?:ation|y)?\s+date[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
                         r'exp(?:ires?)?[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})'],
    "renewal_date":     [r'renewal\s+date[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
                         r'renews?[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})'],
    "inception_date":   [r'inception\s+date[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})'],
    "issue_date":       [r'issue\s+date[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})'],

    # Parties
    "insured_name":     [r'(?:named\s+)?insured[:\s]+([A-Z][A-Za-z\s&.,\'-]+)',
                         r'policyholder[:\s]+([A-Z][A-Za-z\s&.,\'-]+)'],
    "carrier_name":     [r'(?:insurance\s+)?company[:\s]+([A-Z][A-Za-z\s&.,\'-]+)',
                         r'carrier[:\s]+([A-Z][A-Za-z\s&.,\'-]+)',
                         r'insurer[:\s]+([A-Z][A-Za-z\s&.,\'-]+)'],
    "agent_name":       [r'agent[:\s]+([A-Z][A-Za-z\s&.,\'-]+)',
                         r'producer[:\s]+([A-Z][A-Za-z\s&.,\'-]+)'],
    "broker_name":      [r'broker[:\s]+([A-Z][A-Za-z\s&.,\'-]+)'],

    # Financial
    "premium_amount":   [r'(?:annual\s+)?premium[:\s]+\$?([\d,]+\.?\d*)',
                         r'total\s+premium[:\s]+\$?([\d,]+\.?\d*)'],
    "deductible":       [r'deductible[:\s]+\$?([\d,]+\.?\d*)'],
    "commission_rate":  [r'commission[:\s]+(\d+\.?\d*)\s*%',
                         r'comm(?:ission)?\s+rate[:\s]+(\d+\.?\d*)\s*%'],
    "commission_amount":[r'commission\s+amount[:\s]+\$?([\d,]+\.?\d*)'],

    # Coverage
    "coverage_limit":   [r'(?:coverage\s+)?limit[:\s]+\$?([\d,]+\.?\d*)'],
    "policy_type":      [r'(?:policy|coverage)\s+type[:\s]+([A-Za-z\s&/\-]+)'],
    "line_of_business": [r'line\s+of\s+(?:business|coverage)[:\s]+([A-Za-z\s&/\-]+)'],

    # Loss run specific
    "loss_ratio":       [r'loss\s+ratio[:\s]+(\d+\.?\d*)\s*%?'],
    "total_incurred":   [r'total\s+incurred[:\s]+\$?([\d,]+\.?\d*)'],
    "total_paid":       [r'total\s+paid[:\s]+\$?([\d,]+\.?\d*)'],
    "claim_count":      [r'(?:total\s+)?claims?[:\s]+(\d+)',
                         r'number\s+of\s+claims?[:\s]+(\d+)'],
}


def extract_kv_pairs(text: str) -> Dict[str, str]:
    """
    Extract key-value pairs from text using insurance-specific patterns.
    Returns normalized field names → values.
    """
    result = {}

    for field_name, patterns in INSURANCE_KV_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1).strip()
                # Clean up value
                value = re.sub(r'\s+', ' ', value)
                value = value.rstrip('.,;:')
                if value and len(value) < 200:
                    result[field_name] = value
                    break

    # Also extract generic "Label: Value" pairs
    generic_pattern = r'^([A-Za-z][A-Za-z\s\/\-]{2,30})[:\s]{1,3}([^\n]{1,100})$'
    for match in re.finditer(generic_pattern, text, re.MULTILINE):
        key = match.group(1).strip().lower().replace(' ', '_').replace('/', '_')
        value = match.group(2).strip()
        if key and value and key not in result:
            result[key] = value

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TABLE CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════════

def classify_table(headers: List[str]) -> str:
    """Classify a table by its column headers."""
    headers_lower = [h.lower() for h in headers if h]
    header_str = " ".join(headers_lower)

    if any(k in header_str for k in ["commission", "comm", "earned", "written"]):
        return "commission"
    if any(k in header_str for k in ["loss", "claim", "incurred", "paid", "reserve"]):
        return "loss_run"
    if any(k in header_str for k in ["coverage", "limit", "deductible", "premium"]):
        return "coverage"
    if any(k in header_str for k in ["policy", "effective", "expir", "renewal", "insured"]):
        return "policy_schedule"
    if any(k in header_str for k in ["description", "amount", "total", "balance", "charge"]):
        return "financial"
    return "generic"


def parse_table_to_rows(raw_table: List[List[Optional[str]]]) -> Tuple[List[str], List[Dict]]:
    """Convert raw pdfplumber table to headers + list of dicts."""
    if not raw_table or len(raw_table) < 2:
        return [], []

    # First non-empty row as headers
    headers = []
    header_row_idx = 0
    for i, row in enumerate(raw_table):
        if row and any(cell for cell in row if cell and str(cell).strip()):
            headers = [str(c).strip() if c else f"col_{j}" for j, c in enumerate(row)]
            header_row_idx = i
            break

    if not headers:
        return [], []

    rows = []
    for row in raw_table[header_row_idx + 1:]:
        if not row or not any(cell for cell in row if cell and str(cell).strip()):
            continue
        row_dict = {}
        for j, cell in enumerate(row):
            if j < len(headers):
                key = headers[j] or f"col_{j}"
                row_dict[key] = str(cell).strip() if cell else ""
        if any(v for v in row_dict.values() if v):
            rows.append(row_dict)

    return headers, rows


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PARSER
# ═══════════════════════════════════════════════════════════════════════════════

class HierarchicalDocParser:
    """
    Builds a DocumentTree from a PDF using structural analysis.

    Layers:
    1. pdfplumber word-level extraction with font metadata
    2. Heading detection → section hierarchy
    3. Table extraction and classification
    4. KV pair extraction per section and globally
    5. Insurance-specific field normalization
    6. Page index construction
    """

    def parse(
        self,
        file_path: str,
        filename: str,
        doc_type: str = "unknown",
        raw_text: str = "",
    ) -> DocumentTree:
        """
        Parse a PDF into a DocumentTree.

        Args:
            file_path: Path to PDF file
            filename: Original filename
            doc_type: Document type hint (e.g. 'insurance_policy', 'commission_statement')
            raw_text: Pre-extracted raw text (from existing PDFParser) — avoids re-reading

        Returns:
            DocumentTree with full structural hierarchy
        """
        tree = DocumentTree(
            filename=filename,
            doc_type=doc_type,
            page_count=0,
            raw_text=raw_text,
        )

        try:
            import pdfplumber

            with pdfplumber.open(file_path) as pdf:
                tree.page_count = len(pdf.pages)
                page_blocks: List[PageBlock] = []
                all_tables: List[StructuredTable] = []

                # ── Pass 1: Extract all text blocks with font metadata ──────────
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_text_lines = []

                    try:
                        # Word-level extraction with font info
                        words = page.extract_words(
                            extra_attrs=["fontname", "size"],
                            use_text_flow=True,
                        )
                        for word in words:
                            block = PageBlock(
                                page=page_num,
                                text=word.get("text", ""),
                                font_size=float(word.get("size", 0) or 0),
                                font_name=word.get("fontname", ""),
                                is_bold="bold" in (word.get("fontname", "") or "").lower(),
                                x0=float(word.get("x0", 0)),
                                y0=float(word.get("top", 0)),
                                x1=float(word.get("x1", 0)),
                                y1=float(word.get("bottom", 0)),
                            )
                            page_blocks.append(block)
                    except Exception as e:
                        logger.debug(f"Word extraction failed page {page_num}: {e}")

                    # Page-level text for page index
                    try:
                        page_text = page.extract_text() or ""
                        page_text_lines = [l.strip() for l in page_text.split("\n") if l.strip()]
                        tree.page_index[page_num] = page_text_lines
                        if not tree.raw_text:
                            tree.raw_text += page_text + "\n\n"
                    except Exception as e:
                        logger.debug(f"Text extraction failed page {page_num}: {e}")

                    # ── Table extraction ──────────────────────────────────────
                    try:
                        tables = page.extract_tables()
                        for t_idx, raw_table in enumerate(tables or []):
                            headers, rows = parse_table_to_rows(raw_table)
                            if headers and rows:
                                t_type = classify_table(headers)
                                structured = StructuredTable(
                                    name=f"table_p{page_num}_{t_idx}",
                                    page=page_num,
                                    headers=headers,
                                    rows=rows,
                                    raw=raw_table,
                                    table_type=t_type,
                                )
                                all_tables.append(structured)
                    except Exception as e:
                        logger.debug(f"Table extraction failed page {page_num}: {e}")

                tree.tables = all_tables

                # ── Pass 2: TOC detection ──────────────────────────────────────
                tree.toc = self._extract_toc(pdf, tree.raw_text)

                # ── Pass 3: Section hierarchy from headings ────────────────────
                avg_font_size = self._compute_avg_font_size(page_blocks)
                tree.sections = self._build_section_hierarchy(
                    page_blocks, tree.page_index, avg_font_size
                )

                # ── Pass 4: Global KV extraction ──────────────────────────────
                tree.fields = extract_kv_pairs(tree.raw_text)

                # ── Pass 5: Insurance-specific normalization ───────────────────
                self._normalize_insurance_fields(tree)

                # ── Pass 6: Assign tables to sections ─────────────────────────
                self._assign_tables_to_sections(tree)

                tree.confidence = self._compute_confidence(tree)
                tree.parse_method = "hierarchical_pdfplumber"

        except ImportError:
            logger.warning("pdfplumber not available — falling back to text-only parsing")
            tree = self._text_only_parse(file_path, filename, doc_type, raw_text)

        except Exception as e:
            logger.error(f"HierarchicalDocParser failed: {e}", exc_info=True)
            # Still return partial tree with raw text
            if tree.raw_text:
                tree.fields = extract_kv_pairs(tree.raw_text)
                tree.parse_method = "partial_text_fallback"
                tree.confidence = 0.3

        logger.info(
            f"✅ HierarchicalDocParser: {tree.page_count} pages, "
            f"{len(tree.sections)} sections, {len(tree.tables)} tables, "
            f"{len(tree.fields)} fields, confidence={tree.confidence:.2f}"
        )
        return tree

    # ─────────────────────────────────────────────────────────────────────────
    # TOC EXTRACTION
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_toc(self, pdf: Any, raw_text: str) -> List[TocEntry]:
        """Extract Table of Contents if present."""
        toc_entries = []

        # Try pdfplumber/pypdf built-in TOC first
        try:
            # Some PDFs have embedded outlines
            outline = getattr(pdf, 'outline', None) or []
            if outline:
                for item in outline[:50]:
                    if hasattr(item, 'title'):
                        toc_entries.append(TocEntry(
                            title=item.title,
                            page=getattr(item, 'page', 0) or 0,
                            level=1,
                        ))
        except Exception:
            pass

        # Text-based TOC detection
        if not toc_entries:
            toc_pattern = r'^(.{5,60})\s*\.{2,}\s*(\d{1,3})\s*$'
            for match in re.finditer(toc_pattern, raw_text, re.MULTILINE):
                title = match.group(1).strip().rstrip('.')
                page = int(match.group(2))
                level = 1
                # Detect indent level by leading spaces
                leading = len(match.group(0)) - len(match.group(0).lstrip())
                if leading > 4:
                    level = 2
                if leading > 8:
                    level = 3
                toc_entries.append(TocEntry(title=title, page=page, level=level))

        return toc_entries[:100]  # Cap at 100 entries

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION HIERARCHY BUILDING
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_avg_font_size(self, blocks: List[PageBlock]) -> float:
        sizes = [b.font_size for b in blocks if b.font_size > 0]
        return sum(sizes) / len(sizes) if sizes else 10.0

    def _build_section_hierarchy(
        self,
        blocks: List[PageBlock],
        page_index: Dict[int, List[str]],
        avg_font_size: float,
    ) -> List[DocumentSection]:
        """Build nested section tree from text blocks."""
        sections: List[DocumentSection] = []
        current_section: Optional[DocumentSection] = None
        current_content_lines: List[str] = []

        # Group blocks by line (same y0 coordinate ± 2px)
        lines = self._group_blocks_to_lines(blocks)

        for line_text, font_size, is_bold, page in lines:
            is_hdg, level = _is_heading(line_text, font_size, avg_font_size, is_bold)

            if is_hdg:
                # Save previous section
                if current_section:
                    current_section.content = "\n".join(current_content_lines)
                    current_section.kv_pairs = extract_kv_pairs(current_section.content)

                new_section = DocumentSection(
                    title=line_text.strip(),
                    level=level,
                    page=page,
                )

                if level == 1 or not sections:
                    sections.append(new_section)
                    current_section = new_section
                else:
                    # Find parent section
                    parent = self._find_parent(sections, level)
                    if parent:
                        parent.subsections.append(new_section)
                    else:
                        sections.append(new_section)
                    current_section = new_section

                current_content_lines = []
            else:
                current_content_lines.append(line_text)

        # Save last section
        if current_section:
            current_section.content = "\n".join(current_content_lines)
            current_section.kv_pairs = extract_kv_pairs(current_section.content)

        # If no sections detected, create page-based sections
        if not sections:
            sections = self._fallback_page_sections(page_index)

        return sections

    def _group_blocks_to_lines(
        self, blocks: List[PageBlock]
    ) -> List[Tuple[str, float, bool, int]]:
        """Group word blocks into lines (text, font_size, is_bold, page)."""
        if not blocks:
            return []

        # Sort by page, then y0, then x0
        sorted_blocks = sorted(blocks, key=lambda b: (b.page, b.y0, b.x0))

        lines = []
        current_line_words = []
        current_y = None
        current_page = None
        LINE_TOLERANCE = 3.0

        for block in sorted_blocks:
            if (current_y is None
                    or abs(block.y0 - current_y) > LINE_TOLERANCE
                    or block.page != current_page):
                if current_line_words:
                    line_text = " ".join(w.text for w in current_line_words)
                    avg_size = sum(w.font_size for w in current_line_words) / len(current_line_words)
                    any_bold = any(w.is_bold for w in current_line_words)
                    lines.append((line_text, avg_size, any_bold, current_line_words[0].page))
                current_line_words = [block]
                current_y = block.y0
                current_page = block.page
            else:
                current_line_words.append(block)

        if current_line_words:
            line_text = " ".join(w.text for w in current_line_words)
            avg_size = sum(w.font_size for w in current_line_words) / len(current_line_words)
            any_bold = any(w.is_bold for w in current_line_words)
            lines.append((line_text, avg_size, any_bold, current_line_words[0].page))

        return lines

    def _find_parent(
        self, sections: List[DocumentSection], level: int
    ) -> Optional[DocumentSection]:
        """Find the most recent section at level-1."""
        target_level = level - 1
        for section in reversed(sections):
            if section.level == target_level:
                return section
            for sub in reversed(section.subsections):
                if sub.level == target_level:
                    return sub
        return sections[-1] if sections else None

    def _fallback_page_sections(
        self, page_index: Dict[int, List[str]]
    ) -> List[DocumentSection]:
        """Create one section per page when no headings are detected."""
        sections = []
        for page_num, lines in page_index.items():
            content = "\n".join(lines)
            section = DocumentSection(
                title=f"Page {page_num}",
                level=1,
                page=page_num,
                content=content,
                kv_pairs=extract_kv_pairs(content),
            )
            sections.append(section)
        return sections

    # ─────────────────────────────────────────────────────────────────────────
    # INSURANCE-SPECIFIC NORMALIZATION
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize_insurance_fields(self, tree: DocumentTree) -> None:
        """
        Post-process fields for insurance-specific normalization.
        Normalizes dates, currency values, and adds computed fields.
        """
        # Normalize dates to ISO format
        date_fields = ["effective_date", "expiration_date", "renewal_date",
                       "inception_date", "issue_date"]
        for f in date_fields:
            if f in tree.fields:
                tree.fields[f] = self._normalize_date(tree.fields[f])

        # Normalize currency fields
        currency_fields = ["premium_amount", "deductible", "commission_amount",
                           "coverage_limit", "total_incurred", "total_paid"]
        for f in currency_fields:
            if f in tree.fields:
                tree.fields[f] = self._normalize_currency(tree.fields[f])

        # Compute policy period if we have effective + expiration
        if "effective_date" in tree.fields and "expiration_date" in tree.fields:
            tree.fields["policy_period"] = (
                f"{tree.fields['effective_date']} to {tree.fields['expiration_date']}"
            )

        # Renewal date fallback: expiration = renewal for most policies
        if "renewal_date" not in tree.fields and "expiration_date" in tree.fields:
            tree.fields["renewal_date"] = tree.fields["expiration_date"]

        # Doc-type specific extraction
        if tree.doc_type in ("commission_statement", "commission"):
            self._extract_commission_fields(tree)
        elif tree.doc_type in ("loss_run", "loss_run_report"):
            self._extract_loss_run_fields(tree)
        elif tree.doc_type in ("insurance_policy", "declarations_page"):
            self._extract_policy_fields(tree)

    def _extract_commission_fields(self, tree: DocumentTree) -> None:
        """Extract commission-specific fields from tables and text."""
        for table in tree.tables:
            if table.table_type == "commission":
                # Compute totals from table rows
                total_written = 0.0
                total_earned = 0.0
                for row in table.rows:
                    for k, v in row.items():
                        k_lower = k.lower()
                        if "written" in k_lower or "written_premium" in k_lower:
                            total_written += self._parse_amount(v)
                        if "earned" in k_lower or "earned_commission" in k_lower:
                            total_earned += self._parse_amount(v)
                if total_written:
                    tree.fields["total_written_premium"] = str(total_written)
                if total_earned:
                    tree.fields["total_earned_commission"] = str(total_earned)
                break

    def _extract_loss_run_fields(self, tree: DocumentTree) -> None:
        """Extract loss run specific fields."""
        for table in tree.tables:
            if table.table_type == "loss_run":
                total_incurred = 0.0
                total_paid = 0.0
                claim_count = 0
                for row in table.rows:
                    claim_count += 1
                    for k, v in row.items():
                        k_lower = k.lower()
                        if "incurred" in k_lower:
                            total_incurred += self._parse_amount(v)
                        if "paid" in k_lower:
                            total_paid += self._parse_amount(v)
                if claim_count:
                    tree.fields["claim_count"] = str(claim_count)
                if total_incurred:
                    tree.fields["total_incurred"] = str(total_incurred)
                if total_paid:
                    tree.fields["total_paid"] = str(total_paid)
                break

    def _extract_policy_fields(self, tree: DocumentTree) -> None:
        """Extract declarations page specific fields."""
        # Look for coverage schedule table
        for table in tree.tables:
            if table.table_type == "coverage":
                coverages = []
                for row in table.rows:
                    coverage_item = {}
                    for k, v in row.items():
                        k_lower = k.lower()
                        if "coverage" in k_lower or "type" in k_lower:
                            coverage_item["type"] = v
                        elif "limit" in k_lower or "amount" in k_lower:
                            coverage_item["limit"] = self._parse_amount(v)
                        elif "deductible" in k_lower:
                            coverage_item["deductible"] = self._parse_amount(v)
                        elif "premium" in k_lower:
                            coverage_item["premium"] = self._parse_amount(v)
                    if coverage_item:
                        coverages.append(coverage_item)
                if coverages:
                    tree.fields["coverage_schedule"] = coverages
                break

    # ─────────────────────────────────────────────────────────────────────────
    # TABLE → SECTION ASSIGNMENT
    # ─────────────────────────────────────────────────────────────────────────

    def _assign_tables_to_sections(self, tree: DocumentTree) -> None:
        """Assign tables to the section on the same page."""
        for table in tree.tables:
            page = table.page
            # Find section on same page
            for section in tree.sections:
                if section.page == page:
                    section.tables.append(table.to_dict())
                    break
                for sub in section.subsections:
                    if sub.page == page:
                        sub.tables.append(table.to_dict())
                        break

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK: TEXT-ONLY PARSING
    # ─────────────────────────────────────────────────────────────────────────

    def _text_only_parse(
        self,
        file_path: str,
        filename: str,
        doc_type: str,
        raw_text: str,
    ) -> DocumentTree:
        """Minimal parsing when pdfplumber is unavailable."""
        tree = DocumentTree(
            filename=filename,
            doc_type=doc_type,
            page_count=0,
            raw_text=raw_text,
        )
        if raw_text:
            tree.fields = extract_kv_pairs(raw_text)
            # Build simple page index from double-newline separated pages
            for i, page_content in enumerate(raw_text.split("\x0c"), start=1):
                tree.page_index[i] = [l.strip() for l in page_content.split("\n") if l.strip()]
            tree.sections = self._fallback_page_sections(tree.page_index)
            tree.page_count = len(tree.page_index)
            tree.confidence = 0.5
            tree.parse_method = "text_only_fallback"
        return tree

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_confidence(self, tree: DocumentTree) -> float:
        score = 0.0
        if tree.page_count > 0:       score += 0.2
        if tree.sections:             score += 0.2
        if tree.tables:               score += 0.2
        if len(tree.fields) >= 3:     score += 0.2
        if tree.raw_text:             score += 0.1
        if tree.toc:                  score += 0.1
        return min(1.0, score)

    def _normalize_date(self, date_str: str) -> str:
        """Try to normalize date to MM/DD/YYYY format."""
        if not date_str:
            return date_str
        # Already ISO
        if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
            return date_str
        # MM/DD/YYYY or MM-DD-YYYY
        match = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', date_str)
        if match:
            m, d, y = match.groups()
            if len(y) == 2:
                y = "20" + y if int(y) < 50 else "19" + y
            return f"{m.zfill(2)}/{d.zfill(2)}/{y}"
        return date_str

    def _normalize_currency(self, value_str: str) -> str:
        """Remove $ and commas from currency strings."""
        if not value_str:
            return value_str
        cleaned = re.sub(r'[$,\s]', '', str(value_str))
        return cleaned

    def _parse_amount(self, value: str) -> float:
        """Parse a currency string to float."""
        if not value:
            return 0.0
        try:
            cleaned = re.sub(r'[$,\s%]', '', str(value))
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0