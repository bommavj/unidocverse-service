from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal
from app.parsers.extractors.base_extractor import BaseExtractor


class TaxReturnExtractor(BaseExtractor):
    """
    Extract data from Tax Return documents including:
    - Form 1040 (Individual Income Tax Return)
    - Form 1040-SR (Senior)
    - Schedule A (Itemized Deductions)
    - Schedule B (Interest and Dividends)
    - Schedule C (Business Income)
    - Schedule D (Capital Gains)
    - Schedule E (Rental/Royalty Income)
    - State tax returns
    - Personal information
    - Income sources
    - Deductions and credits
    - Tax calculations
    - Refund/Amount owed
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Determine form type
        form_type = self._determine_form_type(text)

        # Extract tax year
        tax_year = self._extract_tax_year(text)

        # Extract filing status
        filing_status = self._extract_filing_status(text)

        # Extract taxpayer information
        taxpayer = self._extract_taxpayer_info(text)
        spouse = self._extract_spouse_info(text) if filing_status in ['married_filing_jointly',
                                                                      'married_filing_separately'] else None

        # Extract dependents
        dependents = self._extract_dependents(text, tables)

        # Extract income (lines 1-9)
        income = self._extract_income(text, tables, form_type)

        # Extract adjustments to income (Schedule 1)
        adjustments = self._extract_adjustments(text, tables)

        # Extract deductions (standard or itemized)
        deductions = self._extract_deductions(text, tables)

        # Extract credits
        credits = self._extract_credits(text, tables)

        # Extract taxes
        taxes = self._extract_taxes(text, tables)

        # Extract payments
        payments = self._extract_payments(text, tables)

        # Extract refund or amount owed
        refund_owed = self._extract_refund_or_owed(text, tables)

        # Calculate AGI and taxable income
        calculations = self._calculate_tax_values(income, adjustments, deductions)

        # Validation
        validation = self._validate_tax_return(taxpayer, income, calculations)

        return {
            # Metadata
            'document_type': 'tax_return',
            'form_type': form_type,
            'tax_year': tax_year,
            'filing_status': filing_status,
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Taxpayer Information
            'taxpayer': taxpayer,
            'taxpayer_name': taxpayer.get('name'),
            'taxpayer_ssn': taxpayer.get('ssn'),
            'taxpayer_occupation': taxpayer.get('occupation'),

            # Spouse Information
            'spouse': spouse,
            'spouse_name': spouse.get('name') if spouse else None,
            'spouse_ssn': spouse.get('ssn') if spouse else None,

            # Address
            'address': taxpayer.get('address'),

            # Dependents
            'dependents': dependents,
            'dependent_count': len(dependents),

            # Income (Lines 1-9)
            'income': income,
            'total_income': income.get('total_income'),
            'wages': income.get('wages'),
            'interest': income.get('interest'),
            'dividends': income.get('dividends'),
            'business_income': income.get('business_income'),
            'capital_gains': income.get('capital_gains'),
            'retirement_distributions': income.get('retirement_distributions'),
            'social_security': income.get('social_security'),

            # Adjustments to Income
            'adjustments': adjustments,
            'total_adjustments': adjustments.get('total_adjustments'),

            # AGI (Adjusted Gross Income)
            'agi': calculations.get('agi'),
            'adjusted_gross_income': calculations.get('agi'),  # Alias

            # Deductions
            'deductions': deductions,
            'deduction_type': deductions.get('type'),  # 'standard' or 'itemized'
            'total_deductions': deductions.get('total_deductions'),
            'standard_deduction': deductions.get('standard_deduction'),
            'itemized_deductions': deductions.get('itemized_total'),

            # Taxable Income
            'taxable_income': calculations.get('taxable_income'),

            # Credits
            'credits': credits,
            'total_credits': credits.get('total_credits'),
            'child_tax_credit': credits.get('child_tax_credit'),
            'earned_income_credit': credits.get('earned_income_credit'),

            # Tax
            'tax': taxes.get('total_tax'),
            'income_tax': taxes.get('income_tax'),
            'self_employment_tax': taxes.get('self_employment_tax'),
            'additional_taxes': taxes.get('additional_taxes'),

            # Payments
            'payments': payments,
            'total_payments': payments.get('total_payments'),
            'federal_withholding': payments.get('federal_withholding'),
            'estimated_tax_payments': payments.get('estimated_payments'),

            # Refund or Amount Owed
            'refund': refund_owed.get('refund'),
            'amount_owed': refund_owed.get('owed'),
            'refund_or_owed': 'refund' if refund_owed.get('refund') else 'owed',

            # Banking (for direct deposit)
            'routing_number': refund_owed.get('routing_number'),
            'account_number': refund_owed.get('account_number'),
            'account_type': refund_owed.get('account_type'),

            # Calculations
            'effective_tax_rate': calculations.get('effective_tax_rate'),
            'marginal_tax_rate': calculations.get('marginal_tax_rate'),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _determine_form_type(self, text: str) -> str:
        """Determine which tax form this is"""
        text_upper = text.upper()

        if '1040-SR' in text_upper:
            return '1040-SR'
        elif '1040-NR' in text_upper:
            return '1040-NR'
        elif '1040-X' in text_upper or 'AMENDED' in text_upper:
            return '1040-X'
        elif '1040' in text_upper or 'U.S. INDIVIDUAL INCOME TAX RETURN' in text_upper:
            return '1040'
        elif 'SCHEDULE A' in text_upper:
            return 'Schedule A'
        elif 'SCHEDULE B' in text_upper:
            return 'Schedule B'
        elif 'SCHEDULE C' in text_upper:
            return 'Schedule C'
        elif 'SCHEDULE D' in text_upper:
            return 'Schedule D'
        elif 'SCHEDULE E' in text_upper:
            return 'Schedule E'
        else:
            return '1040'  # Default

    def _extract_tax_year(self, text: str) -> Optional[int]:
        """Extract tax year"""
        patterns = [
            r'(?:Tax Year|For the year)\s*(\d{4})',
            r'Form 1040.*?(\d{4})',
            r'20(\d{2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                year_str = match.group(1)
                if len(year_str) == 2:
                    return 2000 + int(year_str)
                return int(year_str)

        return None

    def _extract_filing_status(self, text: str) -> Optional[str]:
        """Extract filing status"""
        status_map = {
            'single': r'Single',
            'married_filing_jointly': r'Married filing jointly',
            'married_filing_separately': r'Married filing separately',
            'head_of_household': r'Head of household',
            'qualifying_widow': r'Qualifying (?:widow|surviving spouse)',
        }

        for status, pattern in status_map.items():
            if re.search(rf'{pattern}.*?[X✓✔]', text, re.IGNORECASE):
                return status

        return None

    def _extract_taxpayer_info(self, text: str) -> Dict[str, Any]:
        """Extract primary taxpayer information"""
        taxpayer = {}

        # First name, Last name
        name_patterns = [
            r'(?:Your\s+)?(?:first|last)\s+name.*?\n\s*([A-Z][^\n]+)',
            r'Taxpayer.*?name.*?\n\s*([A-Z][^\n]+)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                taxpayer['name'] = match.group(1).strip()
                break

        # SSN
        ssn_patterns = [
            r'(?:Your social security number|SSN).*?(\d{3}-\d{2}-\d{4})',
            r'(\d{3}-\d{2}-\d{4})',  # First SSN-like pattern
        ]

        for pattern in ssn_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                taxpayer['ssn'] = match.group(1)
                break

        # Address
        address_section = self._extract_address_section(text, 'Home address')
        if address_section:
            taxpayer['address'] = address_section

        # Occupation
        occupation_pattern = r'(?:Your\s+)?occupation.*?\n\s*([A-Z][^\n]+)'
        match = re.search(occupation_pattern, text, re.IGNORECASE)
        if match:
            taxpayer['occupation'] = match.group(1).strip()

        return taxpayer

    def _extract_spouse_info(self, text: str) -> Dict[str, Any]:
        """Extract spouse information"""
        spouse = {}

        # Spouse name
        name_patterns = [
            r"Spouse'?s?\s+(?:first|last)\s+name.*?\n\s*([A-Z][^\n]+)",
            r'If joint return.*?spouse.*?\n\s*([A-Z][^\n]+)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                spouse['name'] = match.group(1).strip()
                break

        # Spouse SSN
        ssn_pattern = r"Spouse'?s?\s+social security number.*?(\d{3}-\d{2}-\d{4})"
        match = re.search(ssn_pattern, text, re.IGNORECASE)
        if match:
            spouse['ssn'] = match.group(1)

        # Spouse occupation
        occupation_pattern = r"Spouse'?s?\s+occupation.*?\n\s*([A-Z][^\n]+)"
        match = re.search(occupation_pattern, text, re.IGNORECASE)
        if match:
            spouse['occupation'] = match.group(1).strip()

        return spouse

    def _extract_address_section(self, text: str, label: str) -> Optional[str]:
        """Extract address section"""
        pattern = rf'{label}.*?\n\s*([^\n]+(?:\n[^\n]+){{0,2}})'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _extract_dependents(self, text: str, tables: List) -> List[Dict[str, Any]]:
        """Extract dependent information"""
        dependents = []

        # Look for dependents in tables
        for table in tables:
            for row in table:
                if len(row) < 3:
                    continue

                # Check if this looks like a dependent row
                # Format: Name, SSN, Relationship, Child Tax Credit checkbox
                row_text = ' '.join([str(cell) for cell in row])
                if re.search(r'\d{3}-\d{2}-\d{4}', row_text):
                    dependent = {
                        'name': str(row[0]).strip(),
                        'ssn': None,
                        'relationship': None,
                    }

                    # Extract SSN
                    ssn_match = re.search(r'(\d{3}-\d{2}-\d{4})', row_text)
                    if ssn_match:
                        dependent['ssn'] = ssn_match.group(1)

                    # Extract relationship
                    for cell in row:
                        cell_str = str(cell).lower()
                        if any(rel in cell_str for rel in ['son', 'daughter', 'child', 'parent', 'other']):
                            dependent['relationship'] = str(cell).strip()
                            break

                    if dependent['name']:
                        dependents.append(dependent)

        # Fallback: extract from text
        if not dependents:
            # Pattern: "Name SSN Relationship"
            pattern = r'([A-Z][a-z]+\s+[A-Z][a-z]+)\s+(\d{3}-\d{2}-\d{4})\s+(Son|Daughter|Child|Other)'
            matches = re.finditer(pattern, text, re.IGNORECASE)

            for match in matches:
                dependents.append({
                    'name': match.group(1),
                    'ssn': match.group(2),
                    'relationship': match.group(3),
                })

        return dependents

    def _extract_income(self, text: str, tables: List, form_type: str) -> Dict[str, Any]:
        """Extract income items (Lines 1-9)"""
        income = {}

        # Line 1: Wages, salaries, tips
        income['wages'] = self._extract_line_amount(text, tables, '1', [
            'Wages, salaries, tips',
            'Wages',
        ])

        # Line 2a: Tax-exempt interest
        income['tax_exempt_interest'] = self._extract_line_amount(text, tables, '2a', [
            'Tax-exempt interest',
        ])

        # Line 2b: Taxable interest
        income['interest'] = self._extract_line_amount(text, tables, '2b', [
            'Taxable interest',
        ])

        # Line 3a: Qualified dividends
        income['qualified_dividends'] = self._extract_line_amount(text, tables, '3a', [
            'Qualified dividends',
        ])

        # Line 3b: Ordinary dividends
        income['dividends'] = self._extract_line_amount(text, tables, '3b', [
            'Ordinary dividends',
        ])

        # Line 4a: IRA distributions
        income['ira_distributions_total'] = self._extract_line_amount(text, tables, '4a', [
            'IRA distributions',
        ])

        # Line 4b: Taxable IRA distributions
        income['ira_distributions_taxable'] = self._extract_line_amount(text, tables, '4b', [
            'Taxable amount',
        ])

        # Line 5a: Pensions and annuities
        income['pensions_total'] = self._extract_line_amount(text, tables, '5a', [
            'Pensions and annuities',
        ])

        # Line 5b: Taxable pensions
        income['retirement_distributions'] = self._extract_line_amount(text, tables, '5b', [
            'Taxable amount',
        ])

        # Line 6a: Social security benefits
        income['social_security_total'] = self._extract_line_amount(text, tables, '6a', [
            'Social security benefits',
        ])

        # Line 6b: Taxable social security
        income['social_security'] = self._extract_line_amount(text, tables, '6b', [
            'Taxable amount',
        ])

        # Line 7: Capital gain or loss
        income['capital_gains'] = self._extract_line_amount(text, tables, '7', [
            'Capital gain or (loss)',
        ])

        # Line 8: Other income (Schedule 1)
        income['other_income'] = self._extract_line_amount(text, tables, '8', [
            'Other income',
        ])

        # Business income (Schedule C)
        income['business_income'] = self._extract_line_amount(text, tables, 'Sch C', [
            'Business income',
        ])

        # Line 9: Total income
        income['total_income'] = self._extract_line_amount(text, tables, '9', [
            'Total income',
        ])

        # Calculate if not found
        if not income['total_income']:
            total = 0
            for key in ['wages', 'interest', 'dividends', 'capital_gains', 'retirement_distributions',
                        'social_security', 'business_income', 'other_income']:
                total += income.get(key, 0) or 0
            income['total_income'] = total if total > 0 else None

        return income

    def _extract_adjustments(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract adjustments to income (Schedule 1)"""
        adjustments = {}

        # Common adjustments
        adjustments['educator_expenses'] = self._extract_line_amount(text, tables, '10', [
            'Educator expenses',
        ])

        adjustments['hsa_deduction'] = self._extract_line_amount(text, tables, '13', [
            'Health savings account deduction',
        ])

        adjustments['self_employment_tax_deduction'] = self._extract_line_amount(text, tables, '15', [
            'Deductible part of self-employment tax',
        ])

        adjustments['self_employed_sep'] = self._extract_line_amount(text, tables, '16', [
            'Self-employed SEP',
        ])

        adjustments['student_loan_interest'] = self._extract_line_amount(text, tables, '21', [
            'Student loan interest deduction',
        ])

        # Total adjustments
        adjustments['total_adjustments'] = self._extract_line_amount(text, tables, '10z', [
            'Total adjustments',
        ])

        # Calculate if not found
        if not adjustments['total_adjustments']:
            total = sum(v for v in adjustments.values() if isinstance(v, (int, float)))
            adjustments['total_adjustments'] = total if total > 0 else None

        return adjustments

    def _extract_deductions(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract deductions (standard or itemized)"""
        deductions = {}

        # Check if itemized or standard
        if re.search(r'itemized\s+deductions.*?[X✓✔]', text, re.IGNORECASE):
            deductions['type'] = 'itemized'

            # Schedule A items
            deductions['medical_dental'] = self._extract_line_amount(text, tables, 'Sch A-4', [
                'Medical and dental expenses',
            ])

            deductions['state_local_taxes'] = self._extract_line_amount(text, tables, 'Sch A-5', [
                'State and local taxes',
                'SALT',
            ])

            deductions['mortgage_interest'] = self._extract_line_amount(text, tables, 'Sch A-8', [
                'Home mortgage interest',
            ])

            deductions['charitable'] = self._extract_line_amount(text, tables, 'Sch A-12', [
                'Gifts to charity',
                'Charitable contributions',
            ])

            deductions['itemized_total'] = self._extract_line_amount(text, tables, '12', [
                'Itemized deductions',
                'Total itemized deductions',
            ])

            deductions['total_deductions'] = deductions['itemized_total']
        else:
            deductions['type'] = 'standard'

            # Standard deduction
            deductions['standard_deduction'] = self._extract_line_amount(text, tables, '12', [
                'Standard deduction',
            ])

            deductions['total_deductions'] = deductions['standard_deduction']

        return deductions

    def _extract_credits(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract tax credits"""
        credits = {}

        # Child tax credit
        credits['child_tax_credit'] = self._extract_line_amount(text, tables, '19', [
            'Child tax credit',
        ])

        # Credit for other dependents
        credits['other_dependent_credit'] = self._extract_line_amount(text, tables, '19', [
            'Credit for other dependents',
        ])

        # Earned Income Credit
        credits['earned_income_credit'] = self._extract_line_amount(text, tables, '27', [
            'Earned income credit',
            'EIC',
        ])

        # Education credits
        credits['education_credits'] = self._extract_line_amount(text, tables, '20', [
            'Education credits',
        ])

        # Retirement savings contribution credit
        credits['retirement_savings_credit'] = self._extract_line_amount(text, tables, '21', [
            'Retirement savings contributions credit',
        ])

        # Total credits
        credits['total_credits'] = self._extract_line_amount(text, tables, '31', [
            'Total credits',
        ])

        return credits

    def _extract_taxes(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract tax amounts"""
        taxes = {}

        # Income tax
        taxes['income_tax'] = self._extract_line_amount(text, tables, '16', [
            'Tax',
            'Income tax',
        ])

        # Alternative minimum tax
        taxes['amt'] = self._extract_line_amount(text, tables, '17', [
            'Alternative minimum tax',
            'AMT',
        ])

        # Self-employment tax (Schedule 2)
        taxes['self_employment_tax'] = self._extract_line_amount(text, tables, '23', [
            'Self-employment tax',
        ])

        # Additional Medicare tax
        taxes['additional_medicare'] = self._extract_line_amount(text, tables, '23', [
            'Additional Medicare tax',
        ])

        # Total tax
        taxes['total_tax'] = self._extract_line_amount(text, tables, '24', [
            'Total tax',
        ])

        return taxes

    def _extract_payments(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract payments and credits"""
        payments = {}

        # Federal income tax withheld
        payments['federal_withholding'] = self._extract_line_amount(text, tables, '25a', [
            'Federal income tax withheld',
        ])

        # Estimated tax payments
        payments['estimated_payments'] = self._extract_line_amount(text, tables, '26', [
            'Estimated tax payments',
        ])

        # Earned income credit
        payments['eic_payment'] = self._extract_line_amount(text, tables, '27', [
            'Earned income credit',
        ])

        # Additional child tax credit
        payments['additional_ctc'] = self._extract_line_amount(text, tables, '28', [
            'Additional child tax credit',
        ])

        # American opportunity credit
        payments['american_opportunity_credit'] = self._extract_line_amount(text, tables, '29', [
            'American opportunity credit',
        ])

        # Total payments
        payments['total_payments'] = self._extract_line_amount(text, tables, '33', [
            'Total payments',
        ])

        return payments

    def _extract_refund_or_owed(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract refund or amount owed"""
        result = {}

        # Refund (Line 34)
        refund = self._extract_line_amount(text, tables, '34', [
            'Refund',
            'Amount you overpaid',
        ])

        # Amount owed (Line 37)
        owed = self._extract_line_amount(text, tables, '37', [
            'Amount you owe',
        ])

        if refund:
            result['refund'] = refund
        if owed:
            result['owed'] = owed

        # Direct deposit information
        routing_pattern = r'Routing\s+number.*?(\d{9})'
        match = re.search(routing_pattern, text, re.IGNORECASE)
        if match:
            result['routing_number'] = match.group(1)

        account_pattern = r'Account\s+number.*?(\d+)'
        match = re.search(account_pattern, text, re.IGNORECASE)
        if match:
            result['account_number'] = match.group(1)

        # Account type
        if re.search(r'Checking.*?[X✓✔]', text, re.IGNORECASE):
            result['account_type'] = 'checking'
        elif re.search(r'Savings.*?[X✓✔]', text, re.IGNORECASE):
            result['account_type'] = 'savings'

        return result

    def _extract_line_amount(self, text: str, tables: List, line_number: str,
                             descriptions: List[str]) -> Optional[float]:
        """Generic line amount extraction"""

        # Try tables first
        for table in tables:
            for row in table:
                if len(row) < 2:
                    continue

                cell_text = str(row[0]).lower()

                # Check if this row matches the line
                if line_number.lower() in cell_text or any(desc.lower() in cell_text for desc in descriptions):
                    amount = self._parse_financial_value(str(row[1]))
                    if amount is not None:
                        return amount

        # Fallback to text extraction
        patterns = [
            rf'{line_number}\s+.*?\$?\s*([\d,]+\.?\d*)',
            rf'Line\s+{line_number}.*?\$?\s*([\d,]+\.?\d*)',
        ]

        for desc in descriptions:
            patterns.append(rf'{re.escape(desc)}.*?\$\s*([\d,]+\.?\d*)')

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return self._parse_financial_value(match.group(1))

        return None

    def _calculate_tax_values(self, income: Dict, adjustments: Dict, deductions: Dict) -> Dict[str, Any]:
        """Calculate AGI, taxable income, and tax rates"""
        calc = {}

        # AGI = Total Income - Adjustments
        total_income = income.get('total_income', 0) or 0
        total_adjustments = adjustments.get('total_adjustments', 0) or 0
        calc['agi'] = total_income - total_adjustments

        # Taxable Income = AGI - Deductions
        total_deductions = deductions.get('total_deductions', 0) or 0
        calc['taxable_income'] = calc['agi'] - total_deductions

        # Effective tax rate (if we have tax and income)
        # This would be in the taxes section, but we can calculate here if needed

        return calc

    def _validate_tax_return(self, taxpayer: Dict, income: Dict, calculations: Dict) -> Dict[str, Any]:
        """Validate tax return data"""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }

        # Check required fields
        if not taxpayer.get('ssn'):
            validation['errors'].append("Taxpayer SSN missing")
            validation['is_valid'] = False

        if not taxpayer.get('name'):
            validation['errors'].append("Taxpayer name missing")
            validation['is_valid'] = False

        # Check income
        if not income.get('total_income'):
            validation['warnings'].append("Total income not found or is zero")

        # Check calculations
        agi = calculations.get('agi')
        taxable = calculations.get('taxable_income')

        if agi and taxable and taxable > agi:
            validation['warnings'].append("Taxable income exceeds AGI")

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
extractor = TaxReturnExtractor()
result = extractor.extract(text, tables)

print(f"Form Type: {result['form_type']}")
print(f"Tax Year: {result['tax_year']}")
print(f"Filing Status: {result['filing_status']}")

print(f"\nTaxpayer: {result['taxpayer_name']}")
print(f"SSN: {result['taxpayer_ssn']}")
if result['spouse_name']:
    print(f"Spouse: {result['spouse_name']}")

print(f"\nDependents: {result['dependent_count']}")

print(f"\nIncome:")
print(f"  Wages: ${result['wages']:,.2f}" if result['wages'] else "")
print(f"  Interest: ${result['interest']:,.2f}" if result['interest'] else "")
print(f"  Dividends: ${result['dividends']:,.2f}" if result['dividends'] else "")
print(f"  Total Income: ${result['total_income']:,.2f}")

print(f"\nAdjusted Gross Income (AGI): ${result['agi']:,.2f}")

print(f"\nDeductions:")
print(f"  Type: {result['deduction_type']}")
print(f"  Total: ${result['total_deductions']:,.2f}")

print(f"\nTaxable Income: ${result['taxable_income']:,.2f}")

print(f"\nCredits: ${result['total_credits']:,.2f}" if result['total_credits'] else "")
print(f"Tax: ${result['tax']:,.2f}" if result['tax'] else "")
print(f"Payments: ${result['total_payments']:,.2f}" if result['total_payments'] else "")

if result['refund']:
    print(f"\nRefund: ${result['refund']:,.2f}")
elif result['amount_owed']:
    print(f"\nAmount Owed: ${result['amount_owed']:,.2f}")
"""