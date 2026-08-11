from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class Form1099Extractor(BaseExtractor):
    """
    Extract data from IRS Form 1099 variants including:
    - 1099-MISC (Miscellaneous Income)
    - 1099-NEC (Nonemployee Compensation)
    - 1099-INT (Interest Income)
    - 1099-DIV (Dividends)
    - 1099-B (Broker transactions)
    - 1099-K (Payment card transactions)
    - 1099-R (Retirement distributions)
    - 1099-G (Government payments)
    - And other variants

    Each variant has different boxes and requirements.
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Determine 1099 variant
        form_variant = self._determine_1099_variant(text)

        # Extract basic metadata
        tax_year = self._extract_tax_year(text)
        corrected = self._is_corrected(text)

        # Extract payer information
        payer = self._extract_payer_info(text)

        # Extract recipient information
        recipient = self._extract_recipient_info(text)

        # Extract account information
        account_info = self._extract_account_info(text)

        # Extract amounts based on variant
        amounts = self._extract_amounts_by_variant(text, tables, form_variant)

        # Extract state information (if applicable)
        state_info = self._extract_state_info(text, tables)

        # Calculate totals
        totals = self._calculate_totals(amounts, form_variant)

        # Validation
        validation = self._validate_1099(amounts, form_variant, payer, recipient)

        return {
            # Metadata
            'document_type': 'form_1099',
            'form_variant': form_variant,
            'tax_year': tax_year,
            'is_corrected': corrected,
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Payer Information
            'payer': payer,
            'payer_name': payer.get('name'),
            'payer_tin': payer.get('tin'),
            'payer_address': payer.get('address'),
            'payer_phone': payer.get('phone'),

            # Recipient Information
            'recipient': recipient,
            'recipient_name': recipient.get('name'),
            'recipient_tin': recipient.get('tin'),
            'recipient_ssn': recipient.get('tin'),  # Alias
            'recipient_address': recipient.get('address'),

            # Account Information
            'account_number': account_info.get('account_number'),

            # Amounts (variant-specific)
            'amounts': amounts,

            # Common fields (if present)
            'federal_income_tax_withheld': amounts.get('federal_tax_withheld'),
            'state_tax_withheld': state_info[0].get('tax') if state_info else None,

            # State Information
            'state_info': state_info,

            # Totals
            'total_income': totals.get('total_income'),
            'total_tax_withheld': totals.get('total_tax_withheld'),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _determine_1099_variant(self, text: str) -> str:
        """Determine which 1099 variant this is"""
        text_upper = text.upper()

        # Check for specific variants
        if 'CORRECTED' in text_upper:
            # Still need to determine the base variant
            pass

        if '1099-NEC' in text_upper or 'NONEMPLOYEE COMPENSATION' in text_upper:
            return '1099-NEC'
        elif '1099-MISC' in text_upper or 'MISCELLANEOUS' in text_upper:
            return '1099-MISC'
        elif '1099-INT' in text_upper or 'INTEREST INCOME' in text_upper:
            return '1099-INT'
        elif '1099-DIV' in text_upper or 'DIVIDENDS' in text_upper:
            return '1099-DIV'
        elif '1099-B' in text_upper or 'PROCEEDS FROM BROKER' in text_upper:
            return '1099-B'
        elif '1099-K' in text_upper or 'PAYMENT CARD' in text_upper:
            return '1099-K'
        elif '1099-R' in text_upper or 'DISTRIBUTIONS FROM PENSIONS' in text_upper:
            return '1099-R'
        elif '1099-G' in text_upper or 'CERTAIN GOVERNMENT PAYMENTS' in text_upper:
            return '1099-G'
        elif '1099-S' in text_upper or 'REAL ESTATE TRANSACTIONS' in text_upper:
            return '1099-S'
        elif '1099-C' in text_upper or 'CANCELLATION OF DEBT' in text_upper:
            return '1099-C'
        elif '1099-A' in text_upper or 'ACQUISITION OR ABANDONMENT' in text_upper:
            return '1099-A'
        elif '1099-Q' in text_upper or 'QUALIFIED EDUCATION' in text_upper:
            return '1099-Q'
        elif '1099-SA' in text_upper or 'HSA, ARCHER MSA' in text_upper:
            return '1099-SA'
        else:
            return '1099-MISC'  # Default fallback

    def _extract_tax_year(self, text: str) -> Optional[int]:
        """Extract tax year"""
        patterns = [
            r'(?:Tax Year|Year|For)\s*:?\s*(\d{4})',
            r'20(\d{2})',
            r'Form 1099.*?(\d{4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                year_str = match.group(1)
                if len(year_str) == 2:
                    return 2000 + int(year_str)
                return int(year_str)

        return None

    def _is_corrected(self, text: str) -> bool:
        """Check if this is a corrected form"""
        return bool(re.search(r'CORRECTED|X.*?CORRECTED', text, re.IGNORECASE))

    def _extract_payer_info(self, text: str) -> Dict[str, Any]:
        """Extract payer (issuer) information"""
        payer = {}

        # Payer's TIN
        tin_patterns = [
            r"PAYER'S TIN.*?(\d{2}-\d{7})",
            r"Payer.*?TIN.*?(\d{2}-\d{7})",
            r"(\d{2}-\d{7})",  # First EIN-like pattern
        ]

        for pattern in tin_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                payer['tin'] = match.group(1)
                break

        # Payer's name
        name_patterns = [
            r"PAYER'S [Nn]ame.*?\n\s*([A-Z][^\n]+)",
            r"PAYER.*?\n\s*([A-Z][^\n]+)",
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                payer['name'] = match.group(1).strip()
                break

        # Payer's address
        address_section = self._extract_payer_address(text)
        if address_section:
            payer['address'] = address_section

        # Payer's phone (optional)
        phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
        if phone_match:
            payer['phone'] = phone_match.group(0)

        return payer

    def _extract_payer_address(self, text: str) -> Optional[str]:
        """Extract payer's address"""
        # Look for address after payer name
        pattern = r"PAYER'S name.*?\n.*?\n\s*([^\n]+(?:\n[^\n]+){0,2})"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        return None

    def _extract_recipient_info(self, text: str) -> Dict[str, Any]:
        """Extract recipient (taxpayer) information"""
        recipient = {}

        # Recipient's TIN (SSN or EIN)
        tin_patterns = [
            r"RECIPIENT'S TIN.*?(\d{3}-\d{2}-\d{4})",
            r"RECIPIENT'S TIN.*?(\d{2}-\d{7})",
            r"Recipient.*?TIN.*?(\d{3}-\d{2}-\d{4})",
        ]

        for pattern in tin_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                recipient['tin'] = match.group(1)
                break

        # Recipient's name
        name_patterns = [
            r"RECIPIENT'S [Nn]ame.*?\n\s*([A-Z][^\n]+)",
            r"RECIPIENT.*?\n\s*([A-Z][^\n]+)",
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                recipient['name'] = match.group(1).strip()
                break

        # Recipient's address
        address_section = self._extract_recipient_address(text)
        if address_section:
            recipient['address'] = address_section

        return recipient

    def _extract_recipient_address(self, text: str) -> Optional[str]:
        """Extract recipient's address"""
        pattern = r"RECIPIENT'S name.*?\n.*?\n\s*([^\n]+(?:\n[^\n]+){0,2})"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        return None

    def _extract_account_info(self, text: str) -> Dict[str, Any]:
        """Extract account number"""
        account = {}

        account_patterns = [
            r'Account [Nn]umber.*?([A-Z0-9-]+)',
            r'Acct.*?#?:?\s*([A-Z0-9-]+)',
        ]

        for pattern in account_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                account['account_number'] = match.group(1)
                break

        return account

    def _extract_amounts_by_variant(self, text: str, tables: List, variant: str) -> Dict[str, Any]:
        """Extract amounts based on 1099 variant"""

        if variant == '1099-NEC':
            return self._extract_1099_nec(text, tables)
        elif variant == '1099-MISC':
            return self._extract_1099_misc(text, tables)
        elif variant == '1099-INT':
            return self._extract_1099_int(text, tables)
        elif variant == '1099-DIV':
            return self._extract_1099_div(text, tables)
        elif variant == '1099-B':
            return self._extract_1099_b(text, tables)
        elif variant == '1099-K':
            return self._extract_1099_k(text, tables)
        elif variant == '1099-R':
            return self._extract_1099_r(text, tables)
        elif variant == '1099-G':
            return self._extract_1099_g(text, tables)
        else:
            return self._extract_generic_1099(text, tables)

    def _extract_1099_nec(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-NEC (Nonemployee Compensation) amounts"""
        amounts = {}

        # Box 1: Nonemployee compensation
        amounts['nonemployee_compensation'] = self._extract_box_amount(text, tables, '1', [
            'Nonemployee compensation',
        ])

        # Box 2: Payer made direct sales totaling $5,000 or more
        amounts['direct_sales'] = self._extract_box_checkbox(text, '2')

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        return amounts

    def _extract_1099_misc(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-MISC (Miscellaneous Income) amounts"""
        amounts = {}

        # Box 1: Rents
        amounts['rents'] = self._extract_box_amount(text, tables, '1', ['Rents'])

        # Box 2: Royalties
        amounts['royalties'] = self._extract_box_amount(text, tables, '2', ['Royalties'])

        # Box 3: Other income
        amounts['other_income'] = self._extract_box_amount(text, tables, '3', ['Other income'])

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        # Box 5: Fishing boat proceeds
        amounts['fishing_boat_proceeds'] = self._extract_box_amount(text, tables, '5', [
            'Fishing boat proceeds',
        ])

        # Box 6: Medical and health care payments
        amounts['medical_health_payments'] = self._extract_box_amount(text, tables, '6', [
            'Medical and health care payments',
        ])

        # Box 8: Substitute payments in lieu of dividends
        amounts['substitute_payments'] = self._extract_box_amount(text, tables, '8', [
            'Substitute payments',
        ])

        # Box 10: Crop insurance proceeds
        amounts['crop_insurance'] = self._extract_box_amount(text, tables, '10', [
            'Crop insurance proceeds',
        ])

        return amounts

    def _extract_1099_int(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-INT (Interest Income) amounts"""
        amounts = {}

        # Box 1: Interest income
        amounts['interest_income'] = self._extract_box_amount(text, tables, '1', [
            'Interest income',
        ])

        # Box 2: Early withdrawal penalty
        amounts['early_withdrawal_penalty'] = self._extract_box_amount(text, tables, '2', [
            'Early withdrawal penalty',
        ])

        # Box 3: Interest on U.S. Savings Bonds and Treasury obligations
        amounts['us_savings_bonds_interest'] = self._extract_box_amount(text, tables, '3', [
            'Interest on U.S. Savings Bonds',
        ])

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        # Box 5: Investment expenses
        amounts['investment_expenses'] = self._extract_box_amount(text, tables, '5', [
            'Investment expenses',
        ])

        # Box 6: Foreign tax paid
        amounts['foreign_tax_paid'] = self._extract_box_amount(text, tables, '6', [
            'Foreign tax paid',
        ])

        # Box 8: Tax-exempt interest
        amounts['tax_exempt_interest'] = self._extract_box_amount(text, tables, '8', [
            'Tax-exempt interest',
        ])

        return amounts

    def _extract_1099_div(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-DIV (Dividends) amounts"""
        amounts = {}

        # Box 1a: Total ordinary dividends
        amounts['ordinary_dividends'] = self._extract_box_amount(text, tables, '1a', [
            'Total ordinary dividends',
            'Ordinary dividends',
        ])

        # Box 1b: Qualified dividends
        amounts['qualified_dividends'] = self._extract_box_amount(text, tables, '1b', [
            'Qualified dividends',
        ])

        # Box 2a: Total capital gain distributions
        amounts['capital_gain_distributions'] = self._extract_box_amount(text, tables, '2a', [
            'Total capital gain distributions',
        ])

        # Box 3: Nondividend distributions
        amounts['nondividend_distributions'] = self._extract_box_amount(text, tables, '3', [
            'Nondividend distributions',
        ])

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        # Box 5: Section 199A dividends
        amounts['section_199a_dividends'] = self._extract_box_amount(text, tables, '5', [
            'Section 199A dividends',
        ])

        # Box 6: Foreign tax paid
        amounts['foreign_tax_paid'] = self._extract_box_amount(text, tables, '6', [
            'Foreign tax paid',
        ])

        return amounts

    def _extract_1099_b(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-B (Broker transactions) amounts"""
        amounts = {}

        # Box 1d: Proceeds
        amounts['proceeds'] = self._extract_box_amount(text, tables, '1d', [
            'Proceeds',
        ])

        # Box 1e: Cost or other basis
        amounts['cost_basis'] = self._extract_box_amount(text, tables, '1e', [
            'Cost or other basis',
        ])

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        # Box 1g: Gain or loss
        amounts['gain_or_loss'] = self._extract_box_amount(text, tables, '1g', [
            'Gain or (loss)',
        ])

        return amounts

    def _extract_1099_k(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-K (Payment card transactions) amounts"""
        amounts = {}

        # Box 1a: Gross amount of payment card/third party network transactions
        amounts['gross_amount'] = self._extract_box_amount(text, tables, '1a', [
            'Gross amount of payment card',
            'Gross amount',
        ])

        # Box 1b: Card not present transactions
        amounts['card_not_present'] = self._extract_box_amount(text, tables, '1b', [
            'Card not present transactions',
        ])

        # Box 3: Number of payment transactions
        amounts['number_of_transactions'] = self._extract_box_amount(text, tables, '3', [
            'Number of payment transactions',
        ])

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        return amounts

    def _extract_1099_r(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-R (Retirement distributions) amounts"""
        amounts = {}

        # Box 1: Gross distribution
        amounts['gross_distribution'] = self._extract_box_amount(text, tables, '1', [
            'Gross distribution',
        ])

        # Box 2a: Taxable amount
        amounts['taxable_amount'] = self._extract_box_amount(text, tables, '2a', [
            'Taxable amount',
        ])

        # Box 2b: Taxable amount not determined
        amounts['taxable_not_determined'] = self._extract_box_checkbox(text, '2b')

        # Box 3: Capital gain
        amounts['capital_gain'] = self._extract_box_amount(text, tables, '3', [
            'Capital gain',
        ])

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        # Box 7: Distribution code
        amounts['distribution_code'] = self._extract_box_code(text, '7')

        return amounts

    def _extract_1099_g(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract 1099-G (Government payments) amounts"""
        amounts = {}

        # Box 1: Unemployment compensation
        amounts['unemployment_compensation'] = self._extract_box_amount(text, tables, '1', [
            'Unemployment compensation',
        ])

        # Box 2: State or local income tax refunds
        amounts['state_tax_refunds'] = self._extract_box_amount(text, tables, '2', [
            'State or local income tax refunds',
        ])

        # Box 3: Box 2 amount is for tax year
        amounts['refund_tax_year'] = self._extract_box_year(text, '3')

        # Box 4: Federal income tax withheld
        amounts['federal_tax_withheld'] = self._extract_box_amount(text, tables, '4', [
            'Federal income tax withheld',
        ])

        # Box 6: Taxable grants
        amounts['taxable_grants'] = self._extract_box_amount(text, tables, '6', [
            'Taxable grants',
        ])

        return amounts

    def _extract_generic_1099(self, text: str, tables: List) -> Dict[str, Any]:
        """Generic extraction for unknown 1099 variants"""
        amounts = {}

        # Try to extract common boxes
        for box_num in range(1, 21):
            amount = self._extract_box_amount(text, tables, str(box_num), [])
            if amount:
                amounts[f'box_{box_num}'] = amount

        return amounts

    def _extract_box_amount(self, text: str, tables: List, box_number: str,
                            descriptions: List[str]) -> Optional[float]:
        """Generic box amount extraction"""

        # Try tables first
        for table in tables:
            for row in table:
                if len(row) < 2:
                    continue

                cell_text = str(row[0]).lower()

                # Check if this row matches the box
                if box_number in cell_text or any(desc.lower() in cell_text for desc in descriptions):
                    amount = self._parse_financial_value(str(row[1]))
                    if amount is not None:
                        return amount

        # Fallback to text extraction
        patterns = [
            rf'{box_number}\s+.*?\$?\s*([\d,]+\.?\d*)',
            rf'Box\s+{box_number}.*?\$?\s*([\d,]+\.?\d*)',
        ]

        for desc in descriptions:
            patterns.append(rf'{re.escape(desc)}.*?\$\s*([\d,]+\.?\d*)')

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return self._parse_financial_value(match.group(1))

        return None

    def _extract_box_checkbox(self, text: str, box_number: str) -> bool:
        """Extract checkbox value"""
        pattern = rf'{box_number}\s*[X✓✔]'
        return bool(re.search(pattern, text, re.IGNORECASE))

    def _extract_box_code(self, text: str, box_number: str) -> Optional[str]:
        """Extract code from box"""
        pattern = rf'{box_number}\s+([A-Z0-9]+)'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_box_year(self, text: str, box_number: str) -> Optional[int]:
        """Extract year from box"""
        pattern = rf'{box_number}\s+(\d{{4}})'
        match = re.search(pattern, text)
        return int(match.group(1)) if match else None

    def _extract_state_info(self, text: str, tables: List) -> List[Dict[str, Any]]:
        """Extract state tax information"""
        states = []

        # Boxes 15-17: State information
        state_pattern = r'(?:15|16|17)\s+([A-Z]{2})\s+.*?\$?\s*([\d,]+\.?\d*)'
        matches = re.finditer(state_pattern, text)

        state_data = {}
        for match in matches:
            if not state_data.get('state'):
                state_data['state'] = match.group(1)

            amount = self._parse_financial_value(match.group(2))
            if amount:
                if 'income' not in state_data:
                    state_data['income'] = amount
                elif 'tax' not in state_data:
                    state_data['tax'] = amount

        if state_data:
            states.append(state_data)

        return states

    def _calculate_totals(self, amounts: Dict, variant: str) -> Dict[str, Any]:
        """Calculate totals"""
        totals = {}

        # Total income (variant-specific)
        if variant == '1099-NEC':
            totals['total_income'] = amounts.get('nonemployee_compensation', 0)
        elif variant == '1099-INT':
            totals['total_income'] = amounts.get('interest_income', 0)
        elif variant == '1099-DIV':
            totals['total_income'] = amounts.get('ordinary_dividends', 0)
        elif variant == '1099-MISC':
            total = 0
            for key in ['rents', 'royalties', 'other_income']:
                total += amounts.get(key, 0) or 0
            totals['total_income'] = total

        # Total tax withheld
        totals['total_tax_withheld'] = amounts.get('federal_tax_withheld', 0) or 0

        return totals

    def _validate_1099(self, amounts: Dict, variant: str, payer: Dict, recipient: Dict) -> Dict[str, Any]:
        """Validate 1099 data"""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }

        # Check required fields
        if not payer.get('tin'):
            validation['errors'].append("Payer TIN missing")
            validation['is_valid'] = False

        if not recipient.get('tin'):
            validation['errors'].append("Recipient TIN missing")
            validation['is_valid'] = False

        # Variant-specific validation
        if variant == '1099-NEC':
            if not amounts.get('nonemployee_compensation'):
                validation['warnings'].append("Box 1 (Nonemployee compensation) is empty")

        elif variant == '1099-MISC':
            # At least one box should have an amount
            has_amount = any(amounts.get(key) for key in [
                'rents', 'royalties', 'other_income', 'fishing_boat_proceeds',
                'medical_health_payments', 'substitute_payments', 'crop_insurance'
            ])
            if not has_amount:
                validation['warnings'].append("No income amounts found")

        elif variant == '1099-K':
            # Reporting threshold: $20,000 and 200 transactions (pre-2024)
            gross = amounts.get('gross_amount', 0) or 0
            transactions = amounts.get('number_of_transactions', 0) or 0

            if gross < 20000 or transactions < 200:
                validation['warnings'].append(
                    f"Below reporting threshold: ${gross:,.2f} / {transactions} transactions"
                )

        validation['is_valid'] = len(validation['errors']) == 0

        return validation

    def _parse_financial_value(self, value_str: str) -> Optional[float]:
        """Parse financial values"""
        if not value_str:
            return None

        cleaned = re.sub(r'[^\d,.\-()]+', '', str(value_str))
        is_negative = '(' in value_str and ')' in value_str
        cleaned = cleaned.replace(',', '').replace('(', '').replace(')', '')

        try:
            value = float(cleaned)
            return -abs(value) if is_negative else value
        except ValueError:
            return None


# Example usage:
"""
extractor = Form1099Extractor()
result = extractor.extract(text, tables)

print(f"Form Variant: {result['form_variant']}")
print(f"Tax Year: {result['tax_year']}")
print(f"Corrected: {result['is_corrected']}")
print(f"\nPayer: {result['payer_name']}")
print(f"Payer TIN: {result['payer_tin']}")
print(f"\nRecipient: {result['recipient_name']}")
print(f"Recipient TIN: {result['recipient_tin']}")

print(f"\nAmounts:")
for key, value in result['amounts'].items():
    if isinstance(value, float):
        print(f"  {key}: ${value:,.2f}")
    else:
        print(f"  {key}: {value}")

print(f"\nTotal Income: ${result['total_income']:,.2f}")
print(f"Federal Tax Withheld: ${result['federal_income_tax_withheld']:,.2f}")

if result['state_info']:
    print(f"\nState: {result['state_info'][0].get('state')}")
    print(f"State Tax: ${result['state_info'][0].get('tax'):,.2f}")
"""