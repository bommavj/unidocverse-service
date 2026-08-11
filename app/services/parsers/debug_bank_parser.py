"""
Debug Bank Statement Parser
Shows exactly what's being extracted at each step
"""
import pdfplumber
from typing import List, Dict, Optional
import logging
import json

logger = logging.getLogger(__name__)


class DebugBankStatementParser:
    """
    Parser with extensive debugging output
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.debug_info = []

    def parse(self) -> Dict:
        """Parse with full debugging"""
        logger.info(f"🔍 DEBUG PARSER STARTING")
        logger.info(f"📄 File: {self.file_path}")

        transactions = []

        try:
            with pdfplumber.open(self.file_path) as pdf:
                logger.info(f"📄 PDF has {len(pdf.pages)} pages")

                for page_num, page in enumerate(pdf.pages, 1):
                    logger.info(f"\n{'=' * 60}")
                    logger.info(f"PAGE {page_num}")
                    logger.info(f"{'=' * 60}")

                    # Get raw text
                    text = page.extract_text()

                    if text:
                        lines = text.split('\n')
                        logger.info(f"📝 {len(lines)} lines of text")

                        # Show first 30 lines
                        logger.info(f"\n--- First 30 lines ---")
                        for i, line in enumerate(lines[:30], 1):
                            logger.info(f"Line {i:3d}: {line}")

                        # Try to find transaction patterns
                        logger.info(f"\n--- Looking for transactions ---")

                        for line_num, line in enumerate(lines, 1):
                            # Check if line looks like transaction
                            if self._looks_like_transaction(line):
                                logger.info(f"\n✓ TRANSACTION LINE {line_num}:")
                                logger.info(f"  Raw: {line}")

                                # Try to parse it
                                parsed = self._parse_transaction_line(line, page_num, line_num)

                                if parsed:
                                    logger.info(f"  Date: {parsed.get('date')}")
                                    logger.info(f"  Description: {parsed.get('description')}")
                                    logger.info(f"  Debit: ${parsed.get('debit', 0):.2f}")
                                    logger.info(f"  Credit: ${parsed.get('credit', 0):.2f}")

                                    transactions.append(parsed)

                                    self.debug_info.append({
                                        'page': page_num,
                                        'line': line_num,
                                        'raw': line,
                                        'parsed': parsed
                                    })
                                else:
                                    logger.info(f"  ❌ Could not parse")

                    # Also try tables
                    tables = page.extract_tables()

                    if tables:
                        logger.info(f"\n📊 Found {len(tables)} table(s)")

                        for table_num, table in enumerate(tables, 1):
                            logger.info(f"\n--- Table {table_num} ---")
                            logger.info(f"Rows: {len(table)}")

                            if table and len(table) > 0:
                                logger.info(f"Header: {table[0]}")

                                # Show first 5 data rows
                                for i, row in enumerate(table[1:6], 1):
                                    logger.info(f"Row {i}: {row}")

            logger.info(f"\n{'=' * 60}")
            logger.info(f"SUMMARY")
            logger.info(f"{'=' * 60}")
            logger.info(f"✅ Total transactions found: {len(transactions)}")

            if transactions:
                total_debits = sum(t.get('debit', 0) for t in transactions)
                total_credits = sum(t.get('credit', 0) for t in transactions)

                logger.info(f"💰 Total debits: ${total_debits:.2f}")
                logger.info(f"💰 Total credits: ${total_credits:.2f}")
                logger.info(f"💰 Net: ${total_credits - total_debits:.2f}")

            # Save debug info to file
            debug_file = self.file_path.replace('.pdf', '_debug.json')
            with open(debug_file, 'w') as f:
                json.dump({
                    'file': self.file_path,
                    'transactions_found': len(transactions),
                    'debug_info': self.debug_info,
                    'transactions': transactions
                }, f, indent=2)

            logger.info(f"\n💾 Debug info saved to: {debug_file}")

            return {
                "transactions": transactions,
                "debug_file": debug_file
            }

        except Exception as e:
            logger.error(f"❌ Parser error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"transactions": [], "error": str(e)}

    def _looks_like_transaction(self, line: str) -> bool:
        """Check if line looks like a transaction"""
        import re

        line = line.strip()

        # Too short
        if len(line) < 20:
            return False

        # Must have date at start
        if not re.match(r'^\d{1,2}[/-]\d{1,2}', line):
            return False

        # Must have an amount (number with 2 decimals)
        if not re.search(r'\d+\.\d{2}', line):
            return False

        # Skip headers
        line_upper = line.upper()
        if any(kw in line_upper for kw in ['DATE', 'DESCRIPTION', 'AMOUNT', 'BALANCE', 'DEBIT', 'CREDIT']):
            return False

        return True

    def _parse_transaction_line(self, line: str, page_num: int, line_num: int) -> Optional[Dict]:
        """Parse transaction line"""
        import re
        from datetime import datetime

        # Extract date
        date_match = re.match(r'^(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\s+(.+)', line)

        if not date_match:
            return None

        date_str = date_match.group(1)
        rest = date_match.group(2)

        # Find all amounts
        amounts = re.findall(r'(-?[\d,]+\.\d{2})', rest)

        if not amounts:
            return None

        # Description is everything before first amount
        first_amt_pos = rest.find(amounts[0])
        description = rest[:first_amt_pos].strip()

        if len(description) < 3:
            return None

        # Parse date
        parsed_date = self._parse_date(date_str)

        # Parse amounts
        parsed_amounts = [self._parse_amount(a) for a in amounts]

        # Determine debit/credit
        debit = 0.0
        credit = 0.0

        if len(parsed_amounts) == 1:
            # Single amount
            amt = parsed_amounts[0]
            if amt < 0:
                debit = abs(amt)
            else:
                # Check description
                desc_lower = description.lower()
                if any(kw in desc_lower for kw in ['withdrawal', 'debit', 'payment', 'purchase', 'fee']):
                    debit = abs(amt)
                else:
                    credit = abs(amt)

        elif len(parsed_amounts) >= 2:
            # Assume: debit, credit (or debit, credit, balance)
            debit = abs(parsed_amounts[0]) if parsed_amounts[0] != 0 else 0
            credit = abs(parsed_amounts[1]) if parsed_amounts[1] != 0 else 0

        return {
            'date': parsed_date,
            'description': description,
            'debit': debit,
            'credit': credit,
            'amount': max(debit, credit),
            'page': page_num,
            'line': line_num,
            'raw_amounts': amounts
        }

    def _parse_date(self, date_str: str) -> str:
        """Parse date"""
        import re
        from datetime import datetime

        formats = ['%m/%d/%Y', '%m/%d/%y', '%m/%d']

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if '%Y' not in fmt and '%y' not in fmt:
                    dt = dt.replace(year=datetime.now().year)
                return dt.strftime('%Y-%m-%d')
            except:
                continue

        return date_str

    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount"""
        try:
            cleaned = amount_str.replace(',', '').replace('$', '').strip()
            return float(cleaned)
        except:
            return 0.0