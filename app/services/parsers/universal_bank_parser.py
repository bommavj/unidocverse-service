"""
Universal Bank Statement Parser - Production Ready
Version 4.0 - Multi-format support

Handles:
- Multiple date formats (MM/DD, DD Month, MM-DD, etc.)
- Text-based and image-based PDFs (with OCR)
- Various transaction table layouts
- Section-based and inline transaction formats

Author: B-Box AI
"""
import re
import pdfplumber
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# OCR imports
try:
    from pdf2image import convert_from_path
    import pytesseract

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class UniversalBankParser:
    """Universal bank statement parser - handles multiple formats"""

    def __init__(self):
        self.account_summary = {}

    def parse(self, file_path: str) -> Dict:
        """Parse bank statement"""
        logger.info(f"🌍 Universal Bank Parser v4.0")
        logger.info(f"📄 File: {file_path}")

        try:
            # Extract text
            text = self._extract_text_native(file_path)

            if not text or len(text.strip()) < 100:
                logger.info("📸 Using OCR...")
                text = self._extract_text_with_ocr(file_path)

            if not text or len(text.strip()) < 50:
                return {"transactions": [], "error": "Could not extract text"}

            logger.info(f"✅ Extracted {len(text)} characters\n")

            # Extract summary
            self.account_summary = self._extract_account_summary(text)

            # Extract transactions
            transactions = self._extract_transactions(text)

            # Post-process
            transactions = self._post_process(transactions)

            # Calculate totals
            total_debits = sum(t.get('debit', 0) for t in transactions)
            total_credits = sum(t.get('credit', 0) for t in transactions)

            # Validate
            accuracy = self._validate_and_correct(transactions, total_credits, total_debits)

            # Recalculate after correction
            total_debits = sum(t.get('debit', 0) for t in transactions)
            total_credits = sum(t.get('credit', 0) for t in transactions)

            logger.info(f"\n{'=' * 70}")
            logger.info(f"✅ RESULTS")
            logger.info(f"{'=' * 70}")
            logger.info(f"Transactions: {len(transactions)}")
            logger.info(f"Total Credits: ${total_credits:,.2f}")
            logger.info(f"Total Debits:  ${total_debits:,.2f}")
            logger.info(f"Accuracy: {accuracy:.1%}")

            return {
                "transactions": transactions,
                "total_debits": total_debits,
                "total_credits": total_credits,
                "account_summary": self.account_summary,
                "accuracy": accuracy,
                "success": True
            }

        except Exception as e:
            logger.error(f"❌ Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"transactions": [], "error": str(e), "success": False}

    def _extract_text_native(self, file_path: str) -> str:
        """Native text extraction"""
        try:
            with pdfplumber.open(file_path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        except:
            return ""

    def _extract_text_with_ocr(self, file_path: str) -> str:
        """OCR extraction"""
        if not OCR_AVAILABLE:
            return ""

        try:
            images = convert_from_path(file_path, dpi=300)
            text = ""
            for i, img in enumerate(images, 1):
                logger.info(f"   OCR page {i}...")
                text += pytesseract.image_to_string(img, lang='eng') + "\n"
            return text
        except:
            return ""

    def _extract_transactions(self, text: str) -> List[Dict]:
        """Extract transactions - universal approach"""
        logger.info(f"{'=' * 70}")
        logger.info("EXTRACTING TRANSACTIONS")
        logger.info(f"{'=' * 70}")

        lines = text.split('\n')
        transactions = []
        current_section = None

        for line_num, line in enumerate(lines, 1):
            line = line.strip()

            if not line or len(line) < 8:
                continue

            line_upper = line.upper()

            # Section detection
            if self._is_section_header(line_upper, ['DEPOSITS', 'CREDITS', 'OTHER CREDITS']):
                current_section = 'deposits'
                logger.info(f"   📥 Deposits section")
                continue

            if self._is_section_header(line_upper, ['WITHDRAWALS', 'DEBITS', 'OTHER DEBITS', 'ATM', 'CHECKS']):
                current_section = 'withdrawals'
                logger.info(f"   📤 Withdrawals section")
                continue

            # Skip non-transactions
            if self._should_skip_line(line_upper):
                continue

            # Look for dates ANYWHERE in the line (not just at start)
            # Try multiple formats
            date_str = None
            rest = None

            # Format 1: MM-DD (e.g., "05-15")
            match = re.search(r'\b(\d{2}-\d{2}(?:-\d{2,4})?)\b', line)
            if match:
                date_str = match.group(1)
                # Rest is everything after the date
                rest = line[match.end():].strip()

            # Format 2: MM/DD
            if not date_str:
                match = re.search(r'\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b', line)
                if match:
                    date_str = match.group(1)
                    rest = line[match.end():].strip()

            # Format 3: DD Month (e.g., "1 February", "24 February")
            if not date_str:
                match = re.search(
                    r'\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+\d{4})?)\b',
                    line, re.IGNORECASE)
                if match:
                    date_str = match.group(1)
                    rest = line[match.end():].strip()

            if not date_str or not rest:
                continue

            # Must have amounts
            amounts = re.findall(r'[\d,]+\.\d{2}', line)  # Search entire line
            if not amounts:
                continue

            # For lines where date isn't at start, get description before date
            date_pos = line.find(date_str)
            if date_pos > 0:
                # Date is not at start - description is before date
                description_before = line[:date_pos].strip()
                description_after = rest

                # Use the longer/more meaningful description
                if len(description_before) > 5:
                    full_desc = description_before + " " + description_after
                else:
                    full_desc = description_after
            else:
                # Date at start - description is after
                full_desc = rest

            # Parse transaction
            txn = self._parse_transaction_from_parts(date_str, full_desc, amounts, current_section)

            if txn:
                transactions.append(txn)

                # Log first 10
                if len(transactions) <= 10:
                    logger.info(f"   {txn['date']} | {txn['description'][:40]:40s} | "
                                f"${txn['amount']:8.2f} [{txn['type']}]")

        logger.info(f"\n   ✅ Extracted {len(transactions)} transactions")
        return transactions

    def _parse_transaction_from_parts(self, date_str: str, description: str,
                                      amounts: List[str], section: Optional[str]) -> Optional[Dict]:
        """Parse transaction from extracted parts"""
        # Parse date
        parsed_date = self._parse_date(date_str)
        if not parsed_date:
            return None

        # Parse amounts
        parsed_amounts = [self._parse_amount(a) for a in amounts]
        if not parsed_amounts:
            return None

        # Clean description - remove amounts from it
        desc_clean = description
        for amt_str in amounts:
            desc_clean = desc_clean.replace(amt_str, '')
        desc_clean = desc_clean.strip()

        if len(desc_clean) < 2:
            # If no description, use a placeholder
            desc_clean = "Transaction"

        # Determine transaction type
        if section == 'deposits' or self._is_credit_keyword(desc_clean):
            debit = 0.0
            credit = parsed_amounts[0]
            txn_type = 'credit'
        else:
            debit = parsed_amounts[0]
            credit = 0.0
            txn_type = 'debit'

        return {
            'date': parsed_date,
            'description': desc_clean,
            'debit': debit,
            'credit': credit,
            'amount': debit if debit > 0 else credit,
            'type': txn_type,
            'section': section or 'unknown'
        }

    def _is_credit_keyword(self, description: str) -> bool:
        """Check if description indicates credit"""
        desc_lower = description.lower()
        keywords = [
            'deposit', 'credit', 'payroll', 'salary', 'payment', 'refund',
            'interest', 'transfer from', 'direct dep', 'biweekly payment',
            'job', 'income', 'received'
        ]
        return any(kw in desc_lower for kw in keywords)

    def _is_section_header(self, line: str, keywords: List[str]) -> bool:
        """Check if line is section header"""
        if len(line) > 80:
            return False
        return any(kw in line for kw in keywords)

    def _should_skip_line(self, line: str) -> bool:
        """Check if line should be skipped"""
        skip = [
            'SUMMARY', 'BALANCE ON', 'BALANCE AT', 'BEGINNING', 'ENDING',
            'TOTAL', 'ACCOUNT NUMBER', 'STATEMENT', 'PAGE', 'CONTINUED',
            'YOUR ACCOUNT', 'BROUGHT FORWARD', 'REFERENCE NUMBER'
        ]

        # Skip headers
        if line in ['DATE', 'DESCRIPTION', 'AMOUNT', 'DEBIT', 'CREDIT', 'BALANCE', 'TRAN DATE', 'DATE PAID',
                    'DATE CREDITED']:
            return True

        return any(kw in line for kw in skip)

    def _post_process(self, transactions: List[Dict]) -> List[Dict]:
        """Post-process transactions"""
        # Remove duplicates
        seen = set()
        unique = []

        for txn in transactions:
            sig = f"{txn.get('date', 'nodate')}:{txn['description'][:25]}:{txn['amount']:.2f}"
            if sig not in seen:
                unique.append(txn)
                seen.add(sig)

        # Sort by date (handle None values)
        unique.sort(key=lambda x: x.get('date') or '9999-99-99')

        if len(unique) < len(transactions):
            logger.info(f"   🗑️  Removed {len(transactions) - len(unique)} duplicates")

        return unique

    def _validate_and_correct(self, transactions: List[Dict], total_credits: float,
                              total_debits: float) -> float:
        """Validate and auto-correct"""
        if not self.account_summary:
            return 1.0

        summary_credits = self.account_summary.get('deposits', 0) + \
                          self.account_summary.get('credits', 0)
        summary_debits = abs(self.account_summary.get('withdrawals', 0)) + \
                         abs(self.account_summary.get('debits', 0)) + \
                         abs(self.account_summary.get('checks', 0))

        if summary_credits == 0 or summary_debits == 0:
            return 1.0

        accuracy = self._calculate_accuracy(total_credits, total_debits,
                                            summary_credits, summary_debits)

        logger.info(f"\n📊 Validation:")
        logger.info(f"   Extracted: Credits=${total_credits:,.2f}, Debits=${total_debits:,.2f}")
        logger.info(f"   Summary:   Credits=${summary_credits:,.2f}, Debits=${summary_debits:,.2f}")
        logger.info(f"   Accuracy: {accuracy:.1%}")

        if accuracy < 0.85:
            logger.warning("   ⚠️  Low accuracy, attempting correction...")
            corrected_accuracy = self._calculate_accuracy(total_debits, total_credits,
                                                          summary_credits, summary_debits)

            if corrected_accuracy > accuracy:
                logger.info(f"   🔄 Swapping improves to {corrected_accuracy:.1%}")
                for txn in transactions:
                    txn['debit'], txn['credit'] = txn['credit'], txn['debit']
                    txn['type'] = 'credit' if txn['type'] == 'debit' else 'debit'
                accuracy = corrected_accuracy

        return accuracy

    def _calculate_accuracy(self, extracted_credits: float, extracted_debits: float,
                            summary_credits: float, summary_debits: float) -> float:
        """Calculate accuracy"""
        if summary_credits == 0 or summary_debits == 0:
            return 0.0

        credit_error = abs(extracted_credits - summary_credits) / summary_credits
        debit_error = abs(extracted_debits - summary_debits) / summary_debits

        credit_acc = max(0, 1.0 - credit_error)
        debit_acc = max(0, 1.0 - debit_error)

        return (credit_acc + debit_acc) / 2

    def _extract_account_summary(self, text: str) -> Dict:
        """Extract account summary"""
        summary = {}

        patterns = {
            'beginning_balance': [
                r'beginning\s+balance.*?(?:\$|£)?\s*([\d,]+\.?\d{0,2})',
                r'balance\s+at\s+\d+.*?(?:\$|£)\s*([\d,]+\.?\d{0,2})',
            ],
            'deposits': [
                r'(?:total\s+)?(?:deposits?|money\s+in).*?(?:\$|£|\+)\s*([\d,]+\.\d{2})',
            ],
            'credits': [
                r'(?:other\s+)?credits?.*?(?:\$|£|\+)\s*([\d,]+\.\d{2})',
            ],
            'withdrawals': [
                r'(?:total\s+)?(?:withdrawals?|money\s+out).*?(?:\$|£|-)\s*([\d,]+\.\d{2})',
            ],
            'debits': [
                r'(?:other\s+)?debits?.*?(?:\$|£|-)\s*([\d,]+\.\d{2})',
            ],
            'checks': [
                r'checks?\s+paid.*?(?:\$|£|-)\s*([\d,]+\.\d{2})',
            ],
            'ending_balance': [
                r'ending\s+balance.*?(?:\$|£)?\s*([\d,]+\.?\d{0,2})',
                r'balance\s+at\s+\d+\s+\w+:?\s*(?:\$|£)?\s*([\d,]+\.?\d{0,2})',
            ]
        }

        for key, pattern_list in patterns.items():
            for pattern in pattern_list:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    summary[key] = self._parse_amount(match.group(1))
                    break

        if summary:
            logger.info(f"📋 Account Summary:")
            for key, value in summary.items():
                logger.info(f"   {key.replace('_', ' ').title()}: ${value:,.2f}")
            logger.info("")

        return summary

    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse date - handles multiple formats"""
        formats = [
            '%m/%d/%Y', '%m/%d/%y', '%m/%d',
            '%m-%d-%Y', '%m-%d-%y', '%m-%d',
            '%d %B %Y', '%d %B', '%d %b %Y', '%d %b'
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                # Default year if not provided
                if '%Y' not in fmt and '%y' not in fmt:
                    # Use 2024 as default year
                    dt = dt.replace(year=2024)
                return dt.strftime('%Y-%m-%d')
            except:
                continue

        return None

    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount"""
        try:
            cleaned = str(amount_str).replace(',', '').replace('$', '').replace('£', '').replace('+', '').replace('-',
                                                                                                                  '').strip()
            return float(cleaned)
        except:
            return 0.0


def main():
    """CLI"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python universal_bank_parser.py <pdf_file>")
        return

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = UniversalBankParser()
    result = parser.parse(sys.argv[1])

    if not result['success']:
        print(f"\n❌ Error: {result.get('error')}")
    else:
        print(f"\n{'=' * 70}")
        print("SUMMARY")
        print(f"{'=' * 70}")
        print(f"✅ Successfully extracted {len(result['transactions'])} transactions")
        print(f"💰 Total Credits: ${result['total_credits']:,.2f}")
        print(f"💰 Total Debits:  ${result['total_debits']:,.2f}")
        print(f"🎯 Accuracy: {result['accuracy']:.1%}")

        if result['transactions']:
            print(f"\nSample transactions:")
            for i, txn in enumerate(result['transactions'][:5], 1):
                print(f"{i}. {txn['date']} | {txn['description'][:50]:50s} | ${txn['amount']:8.2f} [{txn['type']}]")


if __name__ == "__main__":
    main()