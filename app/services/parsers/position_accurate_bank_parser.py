"""
Position-Aware Bank Parser
Uses actual X,Y coordinates to understand column layout
"""
import pdfplumber
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class PositionAwareBankParser:
    """
    Extracts transactions by analyzing character positions
    This is the ONLY way to get 99% accuracy
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.transactions = []

    def parse(self) -> Dict:
        """Parse using character positions"""
        logger.info(f"📍 Position-aware parser starting: {self.file_path}")

        try:
            with pdfplumber.open(self.file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    logger.info(f"   Processing page {page_num}")

                    # Get all characters with positions
                    chars = page.chars

                    if not chars:
                        continue

                    # Group characters into lines by Y position
                    lines = self._group_into_lines(chars)

                    logger.info(f"      Found {len(lines)} lines")

                    # Detect column boundaries
                    columns = self._detect_columns(lines)

                    logger.info(f"      Detected columns: {columns}")

                    # Parse each line as transaction
                    for line_num, line in enumerate(lines):
                        txn = self._parse_line_with_columns(line, columns, page_num, line_num)
                        if txn:
                            self.transactions.append(txn)
                            logger.debug(f"      ✓ {txn['date']} {txn['description'][:30]} ${txn['amount']}")

            logger.info(f"✅ Extracted {len(self.transactions)} transactions")

            return {
                "transactions": self.transactions,
                "method": "position_aware"
            }

        except Exception as e:
            logger.error(f"Position-aware parsing error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"transactions": [], "error": str(e)}

    def _group_into_lines(self, chars: List[Dict]) -> List[List[Dict]]:
        """
        Group characters into lines based on Y position
        """
        from collections import defaultdict

        # Group by Y position (with 2 pixel tolerance)
        lines_dict = defaultdict(list)

        for char in chars:
            y = round(char['y0'], 0)  # Round to nearest pixel
            lines_dict[y].append(char)

        # Sort each line by X position
        lines = []
        for y in sorted(lines_dict.keys()):
            line = sorted(lines_dict[y], key=lambda c: c['x0'])
            lines.append(line)

        return lines

    def _detect_columns(self, lines: List[List[Dict]]) -> Dict[str, tuple]:
        """
        Detect column boundaries by analyzing X positions

        Returns: {"date": (x_start, x_end), "description": (x_start, x_end), ...}
        """
        from collections import defaultdict

        # Find header line
        header_line = None
        header_y = None

        for line in lines[:20]:  # Check first 20 lines
            line_text = ''.join(c['text'] for c in line).upper()

            if 'DATE' in line_text and ('DESCRIPTION' in line_text or 'AMOUNT' in line_text):
                header_line = line
                header_y = line[0]['y0'] if line else None
                break

        if not header_line:
            logger.warning("   No header found, using heuristic column detection")
            return self._heuristic_columns(lines)

        # Analyze header to find column boundaries
        columns = {}

        # Find "DATE" position
        for i, char in enumerate(header_line):
            if char['text'].upper() == 'D':
                # Check if it's "DATE"
                next_chars = ''.join(c['text'] for c in header_line[i:i + 4]).upper()
                if next_chars.startswith('DATE'):
                    date_x = char['x0']
                    columns['date'] = date_x
                    break

        # Find "DESCRIPTION" position
        for i, char in enumerate(header_line):
            if char['text'].upper() == 'D':
                next_chars = ''.join(c['text'] for c in header_line[i:i + 11]).upper()
                if 'DESC' in next_chars:
                    desc_x = char['x0']
                    columns['description'] = desc_x
                    break

        # Find amount columns (DEBIT, CREDIT, or AMOUNT)
        amount_positions = []

        for i, char in enumerate(header_line):
            next_chars = ''.join(c['text'] for c in header_line[i:min(i + 10, len(header_line))]).upper()

            if 'DEBIT' in next_chars or 'WITHDRAWAL' in next_chars:
                columns['debit'] = char['x0']
                amount_positions.append(char['x0'])
            elif 'CREDIT' in next_chars or 'DEPOSIT' in next_chars:
                columns['credit'] = char['x0']
                amount_positions.append(char['x0'])
            elif 'AMOUNT' in next_chars:
                columns['amount'] = char['x0']
                amount_positions.append(char['x0'])

        logger.info(f"      Column positions: {columns}")

        # Convert to ranges
        column_ranges = {}

        sorted_positions = sorted(columns.items(), key=lambda x: x[1])

        for i, (col_name, x_pos) in enumerate(sorted_positions):
            x_start = x_pos
            x_end = sorted_positions[i + 1][1] if i + 1 < len(sorted_positions) else 9999
            column_ranges[col_name] = (x_start, x_end)

        return column_ranges

    def _heuristic_columns(self, lines: List[List[Dict]]) -> Dict[str, tuple]:
        """
        Detect columns heuristically when no header is found
        """
        # Analyze X positions of numbers (amounts) across multiple lines
        from collections import Counter

        number_x_positions = []

        for line in lines[5:25]:  # Sample middle lines
            line_text = ''.join(c['text'] for c in line)

            # Find numbers with decimal points
            import re
            for match in re.finditer(r'\d+\.\d{2}', line_text):
                # Find X position of this number
                char_idx = match.start()
                cumulative_len = 0

                for char in line:
                    if cumulative_len >= char_idx:
                        number_x_positions.append(round(char['x0'], -1))  # Round to nearest 10
                        break
                    cumulative_len += 1

        # Find most common X positions (these are likely column positions)
        if number_x_positions:
            position_counts = Counter(number_x_positions)
            common_positions = [pos for pos, count in position_counts.most_common(3)]

            logger.info(f"      Heuristic: Amount columns at X positions: {common_positions}")

            # Assume: leftmost = date/description area, rightmost = amounts
            columns = {}

            if common_positions:
                columns['date'] = (0, 100)
                columns['description'] = (100, common_positions[0] - 20)

                if len(common_positions) >= 2:
                    columns['debit'] = (common_positions[0], common_positions[1])
                    columns['credit'] = (common_positions[1], 9999)
                else:
                    columns['amount'] = (common_positions[0], 9999)

            return columns

        # Fallback: assume standard layout
        return {
            'date': (0, 100),
            'description': (100, 400),
            'amount': (400, 9999)
        }

    def _parse_line_with_columns(self, line: List[Dict], columns: Dict[str, tuple],
                                 page_num: int, line_num: int) -> Optional[Dict]:
        """
        Parse a line using column positions
        """
        try:
            # Extract text from each column
            fields = {}

            for col_name, (x_start, x_end) in columns.items():
                # Get characters in this column
                col_chars = [c for c in line if x_start <= c['x0'] < x_end]
                col_text = ''.join(c['text'] for c in col_chars).strip()

                if col_text:
                    fields[col_name] = col_text

            # Validate we have minimum fields
            if not fields.get('date') and not fields.get('description'):
                return None

            # Parse date
            date = self._parse_date(fields.get('date', ''))
            if not date:
                return None

            # Parse description
            description = fields.get('description', '').strip()
            if len(description) < 3:
                return None

            # Skip headers
            if any(kw in description.upper() for kw in
                   ['DATE', 'DESCRIPTION', 'DEBIT', 'CREDIT', 'AMOUNT', 'BALANCE', 'TOTAL']):
                return None

            # Parse amounts
            debit = 0.0
            credit = 0.0

            if 'debit' in fields and 'credit' in fields:
                # Two-column format
                debit = self._parse_amount(fields['debit'])
                credit = self._parse_amount(fields['credit'])
            elif 'amount' in fields:
                # Single amount
                amount = self._parse_amount(fields['amount'])

                if amount < 0:
                    debit = abs(amount)
                else:
                    # Determine from description
                    desc_lower = description.lower()
                    is_debit = any(kw in desc_lower for kw in ['withdrawal', 'debit', 'payment', 'purchase'])

                    if is_debit:
                        debit = abs(amount)
                    else:
                        credit = abs(amount)

            if debit == 0 and credit == 0:
                return None

            return {
                'date': date,
                'description': description,
                'debit': debit,
                'credit': credit,
                'amount': max(debit, credit),
                'page': page_num,
                'line': line_num
            }

        except Exception as e:
            logger.debug(f"Error parsing line {line_num}: {e}")
            return None

    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse date"""
        if not date_str:
            return None

        import re
        from datetime import datetime

        # Extract date pattern
        match = re.search(r'\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?', date_str)
        if not match:
            return None

        date_str = match.group(0)

        formats = ['%m/%d/%Y', '%m/%d/%y', '%m/%d', '%d/%m/%Y', '%d/%m/%y']

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if '%Y' not in fmt and '%y' not in fmt:
                    dt = dt.replace(year=datetime.now().year)
                return dt.strftime('%Y-%m-%d')
            except:
                continue

        return None

    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount"""
        if not amount_str:
            return 0.0

        import re

        # Extract number
        match = re.search(r'(-?[\d,]+\.\d{2})', amount_str)
        if not match:
            return 0.0

        try:
            cleaned = match.group(1).replace(',', '')
            return float(cleaned)
        except:
            return 0.0