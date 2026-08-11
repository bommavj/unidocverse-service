"""
Production Bank Statement Parser with Summary Validation
"""
import re
import pdfplumber
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ProductionBankStatementParser:
    """
    Production parser with account summary validation
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.account_summary = {}

    def parse(self) -> Dict:
        """Parse bank statement with validation"""
        logger.info(f"🏦 Production parser starting: {self.file_path}")

        transactions = []

        try:
            with pdfplumber.open(self.file_path) as pdf:
                full_text = ""

                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text()
                    full_text += text + "\n"

                    if text:
                        lines = text.split('\n')

                        for line_num, line in enumerate(lines, 1):
                            if self._looks_like_transaction(line):
                                txn = self._parse_transaction_line(line, page_num, line_num)
                                if txn:
                                    transactions.append(txn)

                # Extract account summary from full text
                self.account_summary = self._extract_account_summary(full_text)

            # Remove duplicates
            transactions = self._remove_duplicates(transactions)

            # Sort by date
            transactions.sort(key=lambda x: x['date'])

            logger.info(f"✅ Extracted {len(transactions)} transactions")

            # Calculate totals
            total_debits = sum(t['debit'] for t in transactions)
            total_credits = sum(t['credit'] for t in transactions)

            logger.info(f"   📊 Extracted totals:")
            logger.info(f"      Deposits (credits): ${total_credits:,.2f}")
            logger.info(f"      Withdrawals (debits): ${total_debits:,.2f}")
            logger.info(f"      Net: ${total_credits - total_debits:,.2f}")

            # Validate against account summary
            if self.account_summary:
                logger.info(f"   📋 Account summary:")
                logger.info(f"      Beginning balance: ${self.account_summary.get('beginning_balance', 0):,.2f}")
                logger.info(f"      Deposits: ${self.account_summary.get('deposits', 0):,.2f}")
                logger.info(f"      Withdrawals: ${self.account_summary.get('withdrawals', 0):,.2f}")
                logger.info(f"      Service fees: ${self.account_summary.get('service_fees', 0):,.2f}")
                logger.info(f"      Ending balance: ${self.account_summary.get('ending_balance', 0):,.2f}")

                # Compare
                summary_deposits = self.account_summary.get('deposits', 0)
                summary_withdrawals = abs(self.account_summary.get('withdrawals', 0)) + abs(self.account_summary.get('service_fees', 0))

                deposit_diff = abs(total_credits - summary_deposits)
                withdrawal_diff = abs(total_debits - summary_withdrawals)

                if deposit_diff > 0.01:
                    logger.warning(f"   ⚠️  Deposit mismatch: Extracted ${total_credits:.2f} vs Summary ${summary_deposits:.2f} (diff: ${deposit_diff:.2f})")
                else:
                    logger.info(f"   ✅ Deposits match!")

                if withdrawal_diff > 0.01:
                    logger.warning(f"   ⚠️  Withdrawal mismatch: Extracted ${total_debits:.2f} vs Summary ${summary_withdrawals:.2f} (diff: ${withdrawal_diff:.2f})")
                else:
                    logger.info(f"   ✅ Withdrawals match!")

                # If mismatch is large, use summary totals as source of truth
                accuracy = self._calculate_accuracy(total_credits, total_debits, summary_deposits, summary_withdrawals)
                logger.info(f"   🎯 Extraction accuracy: {accuracy:.1%}")

            return {
                "transactions": transactions,
                "total_debits": total_debits,
                "total_credits": total_credits,
                "account_summary": self.account_summary,
                "validation": {
                    "extracted_deposits": total_credits,
                    "summary_deposits": self.account_summary.get('deposits', 0),
                    "extracted_withdrawals": total_debits,
                    "summary_withdrawals": abs(self.account_summary.get('withdrawals', 0)) + abs(self.account_summary.get('service_fees', 0)),
                    "deposit_match": abs(total_credits - self.account_summary.get('deposits', 0)) < 0.01,
                    "withdrawal_match": abs(total_debits - (abs(self.account_summary.get('withdrawals', 0)) + abs(self.account_summary.get('service_fees', 0)))) < 0.01
                }
            }

        except Exception as e:
            logger.error(f"Parser error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"transactions": [], "error": str(e)}

    def _extract_account_summary(self, text: str) -> Dict:
        """
        Extract account summary section
        """
        summary = {}

        # Beginning balance
        begin_match = re.search(r'Beginning balance.*?\$?([\d,]+\.\d{2})', text, re.IGNORECASE)
        if begin_match:
            summary['beginning_balance'] = self._parse_amount(begin_match.group(1))

        # Deposits and other additions
        deposits_match = re.search(r'Deposits and other additions.*?([\d,]+\.\d{2})', text, re.IGNORECASE)
        if deposits_match:
            summary['deposits'] = self._parse_amount(deposits_match.group(1))

        # Withdrawals and other subtractions
        withdrawals_match = re.search(r'Withdrawals and other subtractions.*?(-?[\d,]+\.\d{2})', text, re.IGNORECASE)
        if withdrawals_match:
            summary['withdrawals'] = self._parse_amount(withdrawals_match.group(1))

        # Service fees
        fees_match = re.search(r'Service fees.*?(-?[\d,]+\.\d{2})', text, re.IGNORECASE)
        if fees_match:
            summary['service_fees'] = self._parse_amount(fees_match.group(1))

        # Ending balance
        end_match = re.search(r'Ending balance.*?\$?([\d,]+\.\d{2})', text, re.IGNORECASE)
        if end_match:
            summary['ending_balance'] = self._parse_amount(end_match.group(1))

        return summary

    def _calculate_accuracy(self, extracted_credits: float, extracted_debits: float,
                           summary_credits: float, summary_debits: float) -> float:
        """Calculate extraction accuracy"""
        if summary_credits == 0 and summary_debits == 0:
            return 1.0

        credit_accuracy = 1.0 - (abs(extracted_credits - summary_credits) / summary_credits) if summary_credits > 0 else 1.0
        debit_accuracy = 1.0 - (abs(extracted_debits - summary_debits) / summary_debits) if summary_debits > 0 else 1.0

        return (credit_accuracy + debit_accuracy) / 2

    def _looks_like_transaction(self, line: str) -> bool:
        """Check if line looks like a transaction"""
        line = line.strip()

        # Too short
        if len(line) < 20:
            return False

        # Must have date at start
        if not re.match(r'^\d{1,2}[/-]\d{1,2}', line):
            return False

        # Must have an amount
        if not re.search(r'\d+\.\d{2}', line):
            return False

        # Skip headers and summary lines
        line_upper = line.upper()
        skip_keywords = [
            'DATE', 'DESCRIPTION', 'AMOUNT', 'BALANCE', 'DEBIT', 'CREDIT', 'TOTAL', 'SUBTOTAL',
            'BEGINNING', 'ENDING', 'ACCOUNT SUMMARY', 'DEPOSITS AND OTHER', 'WITHDRAWALS AND OTHER',
            'SERVICE FEE', 'CONTINUED', 'PAGE'
        ]

        if any(kw in line_upper for kw in skip_keywords):
            return False

        return True

    def _parse_transaction_line(self, line: str, page_num: int, line_num: int) -> Optional[Dict]:
        """Parse transaction line"""
        date_match = re.match(r'^(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\s+(.+)', line)

        if not date_match:
            return None

        date_str = date_match.group(1)
        rest = date_match.group(2)

        # Find all amounts
        amounts = re.findall(r'(-?[\d,]+\.\d{2})', rest)

        if not amounts:
            return None

        # Description
        first_amt_pos = rest.find(amounts[0])
        description = rest[:first_amt_pos].strip()

        if len(description) < 3:
            return None

        # Parse
        parsed_date = self._parse_date(date_str)
        if not parsed_date:
            return None

        parsed_amounts = [self._parse_amount(a) for a in amounts]
        debit, credit = self._determine_debit_credit(parsed_amounts, description)

        if debit == 0 and credit == 0:
            return None

        # ✅ Log first 5 transactions for debugging
        if line_num <= 50:  # First 50 lines
            logger.info(f"   Line {line_num}: {description[:40]}")
            logger.info(f"      Raw amounts: {amounts}")
            logger.info(f"      Classified: Debit=${debit:.2f}, Credit=${credit:.2f}")

        return {
            'date': parsed_date,
            'description': description,
            'debit': debit,
            'credit': credit,
            'amount': max(debit, credit),
            'page': page_num
        }

    # def _parse_transaction_line(self, line: str, page_num: int, line_num: int) -> Optional[Dict]:
    #     """Parse transaction line"""
    #     # Extract date
    #     date_match = re.match(r'^(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\s+(.+)', line)
    #
    #     if not date_match:
    #         return None
    #
    #     date_str = date_match.group(1)
    #     rest = date_match.group(2)
    #
    #     # Find all amounts
    #     amounts = re.findall(r'(-?[\d,]+\.\d{2})', rest)
    #
    #     if not amounts:
    #         return None
    #
    #     # Description is everything before first amount
    #     first_amt_pos = rest.find(amounts[0])
    #     description = rest[:first_amt_pos].strip()
    #
    #     if len(description) < 3:
    #         return None
    #
    #     # Parse date
    #     parsed_date = self._parse_date(date_str)
    #     if not parsed_date:
    #         return None
    #
    #     # Parse amounts
    #     parsed_amounts = [self._parse_amount(a) for a in amounts]
    #
    #     # Determine debit/credit
    #     debit, credit = self._determine_debit_credit(parsed_amounts, description)
    #
    #     if debit == 0 and credit == 0:
    #         return None
    #
    #     return {
    #         'date': parsed_date,
    #         'description': description,
    #         'debit': debit,
    #         'credit': credit,
    #         'amount': max(debit, credit),
    #         'page': page_num
    #     }

    # def _determine_debit_credit(self, amounts: List[float], description: str) -> tuple:
    #     """Determine which amount is debit vs credit"""
    #     if not amounts:
    #         return 0.0, 0.0
    #
    #     desc_lower = description.lower()
    #
    #     # Strong credit indicators
    #     strong_credit_keywords = [
    #         'deposit', 'direct dep', 'payroll', 'salary', 'wages',
    #         'refund', 'reimbursement', 'credit', 'interest earned',
    #         'transfer from', 'incoming', 'payment received', 'cashback',
    #         'robinhood', 'credits'  # Common deposit sources
    #     ]
    #
    #     # Strong debit indicators
    #     strong_debit_keywords = [
    #         'withdrawal', 'atm', 'debit card', 'purchase', 'payment',
    #         'bill pay', 'fee', 'charge', 'transfer to', 'check',
    #         'online banking payment', 'autopay', 'subscription', 'zelle'
    #     ]
    #
    #     is_strong_credit = any(kw in desc_lower for kw in strong_credit_keywords)
    #     is_strong_debit = any(kw in desc_lower for kw in strong_debit_keywords)
    #
    #     if len(amounts) == 1:
    #         # Single amount column
    #         amt = amounts[0]
    #
    #         if amt < 0:
    #             # Negative = debit
    #             return abs(amt), 0.0
    #         else:
    #             # Positive - check description
    #             if is_strong_credit:
    #                 return 0.0, abs(amt)
    #             elif is_strong_debit:
    #                 return abs(amt), 0.0
    #             else:
    #                 # Heuristic: larger amounts are more likely deposits
    #                 if amt > 500:
    #                     return 0.0, abs(amt)
    #                 else:
    #                     return abs(amt), 0.0
    #
    #     elif len(amounts) == 2:
    #         # Two amounts: debit and credit columns
    #         debit_amt = abs(amounts[0]) if amounts[0] != 0 else 0.0
    #         credit_amt = abs(amounts[1]) if amounts[1] != 0 else 0.0
    #
    #         # If both are non-zero, use description
    #         if debit_amt > 0 and credit_amt > 0:
    #             if is_strong_credit:
    #                 return 0.0, credit_amt
    #             elif is_strong_debit:
    #                 return debit_amt, 0.0
    #             else:
    #                 # Larger one based on typical pattern
    #                 if credit_amt > debit_amt:
    #                     return 0.0, credit_amt
    #                 else:
    #                     return debit_amt, 0.0
    #         else:
    #             # Only one is non-zero
    #             return debit_amt, credit_amt
    #
    #     elif len(amounts) >= 3:
    #         # Three or more: typically debit, credit, balance
    #         debit_amt = abs(amounts[0]) if amounts[0] != 0 else 0.0
    #         credit_amt = abs(amounts[1]) if amounts[1] != 0 else 0.0
    #
    #         if debit_amt > 0 and credit_amt > 0:
    #             if is_strong_credit:
    #                 return 0.0, credit_amt
    #             else:
    #                 return debit_amt, 0.0
    #         else:
    #             return debit_amt, credit_amt
    #
    #     return 0.0, 0.0

    def _determine_debit_credit(self, amounts: List[float], description: str) -> tuple:
        """Determine which amount is debit vs credit - FIXED LOGIC"""
        if not amounts:
            return 0.0, 0.0

        desc_lower = description.lower()

        # VERY specific credit indicators (deposits/income)
        credit_keywords = [
            'deposit', 'direct dep', 'payroll', 'salary', 'wages', 'ach credit',
            'refund', 'reimbursement', 'interest earned', 'interest credit',
            'transfer from', 'incoming transfer', 'received', 'cashback',
            'dividend', 'rebate', 'credit adjustment'
        ]

        # VERY specific debit indicators (withdrawals/expenses)
        debit_keywords = [
            'withdrawal', 'atm', 'debit card', 'purchase', 'pos',
            'payment', 'bill pay', 'autopay', 'online banking payment',
            'fee', 'charge', 'service charge', 'monthly fee',
            'transfer to', 'outgoing', 'zelle', 'venmo', 'paypal',
            'check', 'ach debit', 'card purchase', 'subscription'
        ]

        has_credit_keyword = any(kw in desc_lower for kw in credit_keywords)
        has_debit_keyword = any(kw in desc_lower for kw in debit_keywords)

        if len(amounts) == 1:
            # Single amount - use description ONLY
            amt = abs(amounts[0])

            if has_credit_keyword:
                return 0.0, amt
            elif has_debit_keyword:
                return amt, 0.0
            else:
                # No keyword - default to debit (most transactions are expenses)
                return amt, 0.0

        elif len(amounts) == 2:
            # Two columns: First is usually debit, second is credit
            debit_amt = abs(amounts[0])
            credit_amt = abs(amounts[1])

            # Only one should be non-zero in proper format
            if debit_amt > 0 and credit_amt == 0:
                # Non-zero in debit column = withdrawal
                return debit_amt, 0.0
            elif credit_amt > 0 and debit_amt == 0:
                # Non-zero in credit column = deposit
                return 0.0, credit_amt
            elif debit_amt > 0 and credit_amt > 0:
                # Both non-zero - this shouldn't happen, use keywords
                if has_credit_keyword:
                    return 0.0, credit_amt
                else:
                    return debit_amt, 0.0
            else:
                # Both zero
                return 0.0, 0.0

        elif len(amounts) >= 3:
            # Three columns: usually debit, credit, balance
            debit_amt = abs(amounts[0])
            credit_amt = abs(amounts[1])

            # Same logic as 2 columns
            if debit_amt > 0 and credit_amt == 0:
                return debit_amt, 0.0
            elif credit_amt > 0 and debit_amt == 0:
                return 0.0, credit_amt
            elif debit_amt > 0 and credit_amt > 0:
                if has_credit_keyword:
                    return 0.0, credit_amt
                else:
                    return debit_amt, 0.0
            else:
                return 0.0, 0.0

        return 0.0, 0.0


    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse date string"""
        formats = ['%m/%d/%Y', '%m/%d/%y', '%m/%d', '%d/%m/%Y', '%d/%m/%y']

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if '%Y' not in fmt and '%y' not in fmt:
                    dt = dt.replace(year=datetime.now().year)

                # Validate date
                if dt.year < 2000 or dt > datetime.now():
                    continue

                return dt.strftime('%Y-%m-%d')
            except:
                continue

        return None

    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount string"""
        try:
            cleaned = str(amount_str).replace(',', '').replace('$', '').replace('-', '').strip()
            return float(cleaned)
        except:
            return 0.0

    def _remove_duplicates(self, transactions: List[Dict]) -> List[Dict]:
        """Remove duplicate transactions"""
        seen = set()
        unique = []

        for txn in transactions:
            sig = f"{txn['date']}:{txn['description'][:30]}:{txn['amount']:.2f}"

            if sig not in seen:
                unique.append(txn)
                seen.add(sig)

        return unique