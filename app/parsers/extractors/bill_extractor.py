from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class BillExtractor(BaseExtractor):
    """
    Extract data from Bills/Invoices including:
    - Bill number and dates
    - Biller and customer information
    - Line items with charges
    - Payment information
    - Due dates and amounts
    - Utility bills, service bills, etc.
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Determine bill type
        bill_type = self._determine_bill_type(text)

        # Extract basic metadata
        bill_number = self._extract_bill_number(text)
        bill_date = self._extract_bill_date(text)
        due_date = self._extract_due_date(text)

        # Extract party information
        biller = self._extract_biller_info(text)
        customer = self._extract_customer_info(text)

        # Extract account information
        account_info = self._extract_account_info(text)

        # Extract billing period
        billing_period = self._extract_billing_period(text)

        # Extract line items/charges
        line_items = self._extract_line_items(text, tables, bill_type)

        # Extract payment information
        payment_info = self._extract_payment_info(text)

        # Calculate totals
        totals = self._calculate_totals(line_items, text, tables)

        # Extract late fees and penalties
        fees = self._extract_fees(text, tables)

        # Extract usage data (for utility bills)
        usage_data = None
        if bill_type in ['utility', 'electricity', 'water', 'gas', 'telecom']:
            usage_data = self._extract_usage_data(text, tables, bill_type)

        # Validation
        validation = self._validate_bill(totals, due_date)

        return {
            # Metadata
            'document_type': 'bill',
            'bill_type': bill_type,
            'bill_number': bill_number,
            'invoice_number': bill_number,  # Alias
            'bill_date': bill_date,
            'due_date': due_date,
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Parties
            'biller': biller,
            'service_provider': biller,  # Alias
            'customer': customer,

            # Account
            'account_number': account_info.get('account_number'),
            'customer_id': account_info.get('customer_id'),
            'service_address': account_info.get('service_address'),

            # Billing Period
            'billing_period': billing_period,
            'period_start': billing_period.get('start') if billing_period else None,
            'period_end': billing_period.get('end') if billing_period else None,

            # Line Items/Charges
            'line_items': line_items,
            'charges': line_items,  # Alias
            'item_count': len(line_items),

            # Payment Info
            'payment_methods': payment_info.get('methods'),
            'payment_due_date': due_date,
            'minimum_payment': payment_info.get('minimum_payment'),

            # Financials
            'previous_balance': totals.get('previous_balance'),
            'current_charges': totals.get('current_charges'),
            'adjustments': totals.get('adjustments'),
            'credits': totals.get('credits'),
            'subtotal': totals.get('subtotal'),
            'tax_amount': totals.get('tax'),
            'total_amount': totals.get('total'),
            'amount_due': totals.get('amount_due'),
            'currency': totals.get('currency', 'USD'),

            # Fees
            'late_fee': fees.get('late_fee'),
            'service_fee': fees.get('service_fee'),
            'reconnection_fee': fees.get('reconnection_fee'),

            # Usage (for utility bills)
            'usage_data': usage_data,

            # Status
            'status': self._determine_status(text, totals),
            'past_due': self._is_past_due(due_date, totals.get('previous_balance')),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _determine_bill_type(self, text: str) -> str:
        """Determine the type of bill"""
        text_lower = text.lower()

        # Utility bills
        if any(keyword in text_lower for keyword in ['electricity', 'electric bill', 'power bill', 'kwh']):
            return 'electricity'
        elif any(keyword in text_lower for keyword in ['water bill', 'water service', 'gallons', 'water usage']):
            return 'water'
        elif any(keyword in text_lower for keyword in ['gas bill', 'natural gas', 'therms', 'gas service']):
            return 'gas'
        elif any(keyword in text_lower for keyword in
                 ['internet', 'broadband', 'cable', 'phone service', 'mobile', 'data usage']):
            return 'telecom'

        # Professional services
        elif any(keyword in text_lower for keyword in ['legal services', 'attorney', 'law firm']):
            return 'legal'
        elif any(keyword in text_lower for keyword in ['medical bill', 'patient', 'diagnosis', 'procedure']):
            return 'medical'
        elif any(keyword in text_lower for keyword in ['dental', 'dentist']):
            return 'dental'
        elif any(keyword in text_lower for keyword in ['consulting', 'professional services']):
            return 'consulting'

        # Other
        elif any(keyword in text_lower for keyword in ['rent', 'lease', 'tenant']):
            return 'rent'
        elif any(keyword in text_lower for keyword in ['insurance', 'premium', 'policy']):
            return 'insurance'
        elif any(keyword in text_lower for keyword in ['subscription', 'membership']):
            return 'subscription'

        return 'general'

    def _extract_bill_number(self, text: str) -> Optional[str]:
        """Extract bill/invoice number"""
        patterns = [
            r'Bill\s*(?:Number|#|No\.?)?:?\s*([A-Z0-9-]+)',
            r'Invoice\s*(?:Number|#|No\.?)?:?\s*([A-Z0-9-]+)',
            r'Statement\s*(?:Number|#)?:?\s*([A-Z0-9-]+)',
            r'Account\s*Statement:?\s*([A-Z0-9-]+)',
            r'Reference\s*(?:Number|#)?:?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_bill_date(self, text: str) -> Optional[str]:
        """Extract bill issue date"""
        patterns = [
            r'Bill\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Invoice\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Statement\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Issue\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'(\w+\s+\d{1,2},?\s+\d{4})',  # "January 30, 2024"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_due_date(self, text: str) -> Optional[str]:
        """Extract payment due date"""
        patterns = [
            r'Due\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Payment\s+Due:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Pay\s+By:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Due\s+By:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Please\s+Pay\s+By:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_biller_info(self, text: str) -> Dict[str, Any]:
        """Extract biller/service provider information"""
        biller = {}

        # Find biller section (usually at top)
        lines = text.split('\n')[:15]  # Check first 15 lines

        for line in lines:
            # Company name often has Inc, LLC, etc.
            if any(suffix in line for suffix in ['Inc.', 'LLC', 'Corp', 'Ltd', 'Company']):
                biller['name'] = line.strip()
                break

        # Extract contact info
        biller['phone'] = self._extract_phone(text)
        biller['email'] = self._extract_email(text)
        biller['website'] = self._extract_website(text)
        biller['address'] = self._extract_biller_address(text)

        return biller

    def _extract_biller_address(self, text: str) -> Optional[str]:
        """Extract biller's address (usually in header)"""
        lines = text.split('\n')[:10]
        address_lines = []

        for line in lines:
            line = line.strip()
            # Look for address-like patterns
            if re.search(r'\d+.*(?:Street|St|Avenue|Ave|Road|Rd|Blvd|Lane|Drive)', line, re.IGNORECASE):
                address_lines.append(line)
            elif re.search(r'[A-Z]{2}\s+\d{5}', line):  # State + ZIP
                address_lines.append(line)

        return ', '.join(address_lines) if address_lines else None

    def _extract_website(self, text: str) -> Optional[str]:
        """Extract website URL"""
        pattern = r'(?:www\.)?[a-zA-Z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?'
        match = re.search(pattern, text)
        return match.group(0) if match else None

    def _extract_customer_info(self, text: str) -> Dict[str, Any]:
        """Extract customer/account holder information"""
        customer = {}

        # Find customer section
        customer_section = self._extract_section(text, [
            'Bill To', 'Customer', 'Account Holder', 'Name', 'Service For'
        ])

        if customer_section:
            lines = customer_section.split('\n')
            if lines:
                customer['name'] = lines[0].strip()

            customer['address'] = self._extract_address_from_section(customer_section)
            customer['phone'] = self._extract_phone(customer_section)
            customer['email'] = self._extract_email(customer_section)

        return customer

    def _extract_account_info(self, text: str) -> Dict[str, Any]:
        """Extract account details"""
        account = {}

        # Account number
        patterns = [
            r'Account\s+(?:Number|#|No\.?):?\s*([A-Z0-9-]+)',
            r'Account:?\s*([A-Z0-9-]+)',
            r'Acct\s*#:?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                account['account_number'] = match.group(1).strip()
                break

        # Customer ID
        patterns = [
            r'Customer\s+(?:ID|Number):?\s*([A-Z0-9-]+)',
            r'Customer:?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                account['customer_id'] = match.group(1).strip()
                break

        # Service address
        service_section = self._extract_section(text, ['Service Address', 'Service Location'])
        if service_section:
            account['service_address'] = service_section.strip()

        return account

    def _extract_billing_period(self, text: str) -> Optional[Dict[str, str]]:
        """Extract billing period dates"""
        period = {}

        # Pattern: "Billing Period: 01/01/2024 - 01/31/2024"
        pattern = r'Billing\s+Period:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*(?:-|to)\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            period['start'] = match.group(1)
            period['end'] = match.group(2)
            return period

        # Alternative: "From: ... To: ..."
        start_match = re.search(r'From:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', text, re.IGNORECASE)
        end_match = re.search(r'To:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', text, re.IGNORECASE)

        if start_match and end_match:
            period['start'] = start_match.group(1)
            period['end'] = end_match.group(1)
            return period

        # Service period
        pattern = r'Service\s+Period:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*(?:-|to)\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            period['start'] = match.group(1)
            period['end'] = match.group(2)
            return period

        return period if period else None

    def _extract_line_items(self, text: str, tables: List, bill_type: str) -> List[Dict[str, Any]]:
        """Extract charges/line items"""
        line_items = []

        # Try to find charges table
        for table in tables:
            if len(table) < 2:
                continue

            header = [str(cell).lower() for cell in table[0]]

            # Check if this looks like a charges table
            if not any(
                    keyword in ' '.join(header) for keyword in ['description', 'charge', 'item', 'service', 'amount']):
                continue

            # Find column indices
            col_indices = self._find_charge_column_indices(header)

            # Extract charges from data rows
            for row in table[1:]:
                if len(row) < 2:
                    continue

                charge = self._parse_charge_item(row, col_indices)
                if charge and charge.get('description'):
                    line_items.append(charge)

        # If no items in tables, try text extraction
        if not line_items:
            line_items = self._extract_charges_from_text(text, bill_type)

        return line_items

    def _find_charge_column_indices(self, header: List[str]) -> Dict[str, int]:
        """Find column indices for charge fields"""
        indices = {}

        for i, col in enumerate(header):
            col_lower = col.lower()

            if any(keyword in col_lower for keyword in ['description', 'item', 'service', 'charge type']):
                indices['description'] = i
            elif any(keyword in col_lower for keyword in ['date', 'from', 'start']):
                indices['date'] = i
            elif any(keyword in col_lower for keyword in ['quantity', 'qty', 'usage', 'units']):
                indices['quantity'] = i
            elif any(keyword in col_lower for keyword in ['rate', 'unit price', 'price']):
                indices['rate'] = i
            elif any(keyword in col_lower for keyword in ['amount', 'total', 'charge']):
                indices['amount'] = i

        return indices

    def _parse_charge_item(self, row: List, col_indices: Dict[str, int]) -> Dict[str, Any]:
        """Parse a single charge/line item"""
        charge = {
            'description': None,
            'date': None,
            'quantity': None,
            'rate': None,
            'amount': None,
        }

        for field, index in col_indices.items():
            if index < len(row):
                value = str(row[index]).strip()

                if field in ['quantity', 'rate', 'amount']:
                    charge[field] = self._parse_financial_value(value)
                else:
                    charge[field] = value if value else None

        return charge

    def _extract_charges_from_text(self, text: str, bill_type: str) -> List[Dict[str, Any]]:
        """Fallback: Extract charges from text"""
        charges = []

        # Common charge patterns
        patterns = [
            r'([A-Z][A-Za-z\s&]+?)\s+\$?([\d,]+\.?\d*)',
            r'(.+?)\s{2,}\$?([\d,]+\.?\d*)',
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text)
            for match in matches:
                description = match.group(1).strip()
                amount = self._parse_financial_value(match.group(2))

                # Filter out non-charge items
                if amount and amount > 0 and len(description) > 3:
                    if not any(skip in description.lower() for skip in ['total', 'subtotal', 'balance', 'due']):
                        charges.append({
                            'description': description,
                            'amount': amount
                        })

        return charges

    def _extract_payment_info(self, text: str) -> Dict[str, Any]:
        """Extract payment methods and instructions"""
        payment = {}

        # Payment methods
        methods = []
        if re.search(r'\b(?:online|web|internet)\s+payment', text, re.IGNORECASE):
            methods.append('online')
        if re.search(r'\b(?:check|cheque)', text, re.IGNORECASE):
            methods.append('check')
        if re.search(r'\b(?:credit card|debit card|card)', text, re.IGNORECASE):
            methods.append('card')
        if re.search(r'\b(?:ach|direct debit|bank transfer)', text, re.IGNORECASE):
            methods.append('ach')
        if re.search(r'\b(?:auto-?pay|automatic)', text, re.IGNORECASE):
            methods.append('autopay')

        payment['methods'] = methods if methods else None

        # Minimum payment
        min_payment = self._extract_amount(text, ['Minimum Payment', 'Minimum Due', 'Min Payment'])
        payment['minimum_payment'] = min_payment

        return payment

    def _calculate_totals(self, line_items: List[Dict], text: str, tables: List) -> Dict[str, Any]:
        """Calculate or extract total amounts"""
        totals = {}

        # Previous balance
        totals['previous_balance'] = self._extract_amount(text, [
            'Previous Balance', 'Prior Balance', 'Balance Forward', 'Opening Balance'
        ])

        # Current charges (sum of line items)
        if line_items:
            current_charges = sum(
                item.get('amount', 0) or 0
                for item in line_items
            )
            totals['current_charges'] = round(current_charges, 2)

        # Stated current charges
        stated_charges = self._extract_amount(text, [
            'Current Charges', 'New Charges', 'This Period Charges'
        ])
        if stated_charges:
            totals['current_charges'] = stated_charges

        # Payments/Credits
        totals['credits'] = self._extract_amount(text, [
            'Payments', 'Credits', 'Payments Received', 'Thank You'
        ])

        # Adjustments
        totals['adjustments'] = self._extract_amount(text, [
            'Adjustments', 'Other Charges', 'Misc Charges'
        ])

        # Subtotal
        totals['subtotal'] = self._extract_amount(text, [
            'Subtotal', 'Sub-Total'
        ])

        # Tax
        totals['tax'] = self._extract_amount(text, [
            'Tax', 'Sales Tax', 'GST', 'VAT'
        ])

        # Total/Amount Due
        totals['total'] = self._extract_amount(text, [
            'Total Amount', 'Total', 'Total Due', 'Grand Total'
        ])

        totals['amount_due'] = self._extract_amount(text, [
            'Amount Due', 'Total Amount Due', 'Please Pay', 'Pay This Amount'
        ]) or totals.get('total')

        # Currency
        totals['currency'] = self._extract_currency_type(text) or 'USD'

        return totals

    def _extract_fees(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract various fees and penalties"""
        fees = {}

        fees['late_fee'] = self._extract_amount(text, [
            'Late Fee', 'Late Charge', 'Late Payment Fee', 'Penalty'
        ])

        fees['service_fee'] = self._extract_amount(text, [
            'Service Fee', 'Service Charge', 'Monthly Fee'
        ])

        fees['reconnection_fee'] = self._extract_amount(text, [
            'Reconnection Fee', 'Reconnect Charge'
        ])

        fees['convenience_fee'] = self._extract_amount(text, [
            'Convenience Fee', 'Processing Fee'
        ])

        return fees

    def _extract_usage_data(self, text: str, tables: List, bill_type: str) -> Optional[Dict[str, Any]]:
        """Extract usage data for utility bills"""
        usage = {}

        if bill_type == 'electricity':
            usage['current_reading'] = self._extract_meter_reading(text, 'current')
            usage['previous_reading'] = self._extract_meter_reading(text, 'previous')
            usage['kwh_used'] = self._extract_usage_amount(text, ['kWh', 'kilowatt', 'usage'])
            usage['unit'] = 'kWh'

        elif bill_type == 'water':
            usage['current_reading'] = self._extract_meter_reading(text, 'current')
            usage['previous_reading'] = self._extract_meter_reading(text, 'previous')
            usage['gallons_used'] = self._extract_usage_amount(text, ['gallons', 'gal', 'water usage'])
            usage['unit'] = 'gallons'

        elif bill_type == 'gas':
            usage['current_reading'] = self._extract_meter_reading(text, 'current')
            usage['previous_reading'] = self._extract_meter_reading(text, 'previous')
            usage['therms_used'] = self._extract_usage_amount(text, ['therms', 'therm', 'gas usage'])
            usage['unit'] = 'therms'

        elif bill_type == 'telecom':
            usage['data_used'] = self._extract_usage_amount(text, ['GB', 'data usage', 'gigabytes'])
            usage['minutes_used'] = self._extract_usage_amount(text, ['minutes', 'min', 'talk time'])
            usage['messages_sent'] = self._extract_usage_amount(text, ['sms', 'messages', 'texts'])

        # Calculate usage if readings available
        if usage.get('current_reading') and usage.get('previous_reading'):
            usage['calculated_usage'] = usage['current_reading'] - usage['previous_reading']

        return usage if usage else None

    def _extract_meter_reading(self, text: str, reading_type: str) -> Optional[float]:
        """Extract meter reading (current or previous)"""
        patterns = [
            rf'{reading_type}\s+reading:?\s*([\d,]+\.?\d*)',
            rf'{reading_type}:?\s*([\d,]+\.?\d*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._parse_financial_value(match.group(1))
        return None

    def _extract_usage_amount(self, text: str, keywords: List[str]) -> Optional[float]:
        """Extract usage amount"""
        for keyword in keywords:
            pattern = rf'{re.escape(keyword)}[:\s]*([\d,]+\.?\d*)'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._parse_financial_value(match.group(1))
        return None

    def _extract_amount(self, text: str, labels: List[str]) -> Optional[float]:
        """Extract monetary amount for given labels"""
        for label in labels:
            patterns = [
                rf'{re.escape(label)}\s*[:\s]+\$?\s*([\d,]+\.?\d*)',
                rf'{re.escape(label)}.*?\$\s*([\d,]+\.?\d*)',
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return self._parse_financial_value(match.group(1))
        return None

    def _extract_section(self, text: str, headers: List[str]) -> Optional[str]:
        """Extract a section of text based on header keywords"""
        for header in headers:
            pattern = rf'{re.escape(header)}[:\s]+((?:.+\n?){{1,10}})'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_address_from_section(self, section: str) -> Optional[str]:
        """Extract address from section"""
        lines = section.split('\n')
        address_lines = []

        for line in lines:
            line = line.strip()
            if any(label in line.lower() for label in ['customer', 'account', 'name', 'email', 'phone']):
                continue
            if line and (re.search(r'\d', line) or ',' in line):
                address_lines.append(line)

        return ', '.join(address_lines) if address_lines else None

    def _determine_status(self, text: str, totals: Dict) -> str:
        """Determine bill status"""
        text_lower = text.lower()

        if any(word in text_lower for word in ['paid', 'payment received', 'thank you']):
            return 'paid'
        elif any(word in text_lower for word in ['overdue', 'past due', 'late']):
            return 'overdue'
        elif totals.get('amount_due', 0) > 0:
            return 'unpaid'
        else:
            return 'unknown'

    def _is_past_due(self, due_date: Optional[str], previous_balance: Optional[float]) -> bool:
        """Check if bill is past due"""
        if not due_date:
            return False

        try:
            # Parse due date and compare with today
            # Simplified - in production, use proper date parsing
            if previous_balance and previous_balance > 0:
                return True
        except:
            pass

        return False

    def _validate_bill(self, totals: Dict, due_date: Optional[str]) -> Dict[str, Any]:
        """Validate bill data"""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }

        # Check for required fields
        if not totals.get('amount_due') and not totals.get('total'):
            validation['errors'].append("No total amount found")
            validation['is_valid'] = False

        if not due_date:
            validation['warnings'].append("No due date found")

        # Validate calculations
        if totals.get('previous_balance') is not None and totals.get('current_charges') is not None:
            expected_total = totals['previous_balance'] + totals['current_charges']

            if totals.get('credits'):
                expected_total -= totals['credits']
            if totals.get('adjustments'):
                expected_total += totals['adjustments']
            if totals.get('tax'):
                expected_total += totals['tax']

            actual_total = totals.get('amount_due') or totals.get('total', 0)

            difference = abs(expected_total - actual_total)
            if difference > 0.01:
                validation['warnings'].append(
                    f"Total calculation mismatch: Expected ${expected_total:.2f} vs Stated ${actual_total:.2f}"
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
extractor = BillExtractor()
result = extractor.extract(text, tables)

print(f"Bill Type: {result['bill_type']}")
print(f"Bill Number: {result['bill_number']}")
print(f"Bill Date: {result['bill_date']}")
print(f"Due Date: {result['due_date']}")
print(f"Account: {result['account_number']}")
print(f"\nBiller: {result['biller']['name']}")
print(f"Customer: {result['customer']['name']}")
print(f"\nBilling Period: {result['period_start']} to {result['period_end']}")
print(f"\nCharges ({result['item_count']} items):")
for item in result['line_items']:
    print(f"  {item['description']}: ${item['amount']:.2f}")
print(f"\nPrevious Balance: ${result['previous_balance']:.2f}")
print(f"Current Charges: ${result['current_charges']:.2f}")
print(f"Credits: ${result['credits']:.2f}")
print(f"Tax: ${result['tax_amount']:.2f}")
print(f"Amount Due: ${result['amount_due']:.2f}")
print(f"\nStatus: {result['status']}")
if result['usage_data']:
    print(f"\nUsage: {result['usage_data']}")
"""