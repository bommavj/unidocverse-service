from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class ExpenseReportExtractor(BaseExtractor):
    """
    Extract data from Expense Reports including:
    - Report metadata and submitter info
    - Individual expense line items
    - Categories and business purposes
    - Receipt information
    - Approval workflow
    - Reimbursement details
    - Policy compliance
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Extract report metadata
        report_id = self._extract_report_id(text)
        report_date = self._extract_report_date(text)
        submission_date = self._extract_submission_date(text)

        # Extract employee/submitter information
        employee = self._extract_employee_info(text)

        # Extract department and cost center
        department_info = self._extract_department_info(text)

        # Extract expense period
        expense_period = self._extract_expense_period(text)

        # Extract expense line items
        expenses = self._extract_expenses(text, tables)

        # Extract totals by category
        category_totals = self._calculate_category_totals(expenses)

        # Extract payment/reimbursement information
        payment_info = self._extract_payment_info(text)

        # Extract approval information
        approval_info = self._extract_approval_info(text)

        # Calculate totals
        totals = self._calculate_totals(expenses, text, tables)

        # Extract policy violations/notes
        policy_info = self._extract_policy_info(text)

        # Validate expense report
        validation = self._validate_expense_report(expenses, totals)

        return {
            # Metadata
            'document_type': 'expense_report',
            'report_id': report_id,
            'report_number': report_id,  # Alias
            'report_date': report_date,
            'submission_date': submission_date,
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Employee Information
            'employee': employee,
            'submitter': employee,  # Alias
            'employee_id': employee.get('employee_id'),
            'employee_name': employee.get('name'),

            # Department/Cost Center
            'department': department_info.get('department'),
            'cost_center': department_info.get('cost_center'),
            'project_code': department_info.get('project_code'),
            'manager': department_info.get('manager'),

            # Expense Period
            'expense_period': expense_period,
            'period_start': expense_period.get('start') if expense_period else None,
            'period_end': expense_period.get('end') if expense_period else None,

            # Expenses
            'expenses': expenses,
            'expense_count': len(expenses),

            # Category Breakdown
            'category_totals': category_totals,
            'top_categories': self._get_top_categories(category_totals, top_n=5),

            # Payment Information
            'reimbursement_method': payment_info.get('method'),
            'bank_account': payment_info.get('bank_account'),
            'payment_status': payment_info.get('status'),
            'payment_date': payment_info.get('payment_date'),

            # Approval Workflow
            'status': approval_info.get('status'),
            'approved_by': approval_info.get('approved_by'),
            'approval_date': approval_info.get('approval_date'),
            'approver_comments': approval_info.get('comments'),
            'workflow_stage': approval_info.get('workflow_stage'),

            # Financial Totals
            'subtotal': totals.get('subtotal'),
            'non_reimbursable': totals.get('non_reimbursable'),
            'reimbursable': totals.get('reimbursable'),
            'tax_amount': totals.get('tax'),
            'total_amount': totals.get('total'),
            'currency': totals.get('currency', 'USD'),

            # Policy Compliance
            'policy_violations': policy_info.get('violations'),
            'warnings': policy_info.get('warnings'),
            'notes': policy_info.get('notes'),
            'out_of_policy_amount': policy_info.get('out_of_policy_amount'),

            # Receipt Status
            'receipts_attached': self._count_receipts(expenses),
            'missing_receipts': self._count_missing_receipts(expenses),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _extract_report_id(self, text: str) -> Optional[str]:
        """Extract expense report ID/number"""
        patterns = [
            r'Report\s+(?:ID|Number|#):?\s*([A-Z0-9-]+)',
            r'Expense\s+Report\s*(?:ID|Number|#)?:?\s*([A-Z0-9-]+)',
            r'ER\s*#?:?\s*([A-Z0-9-]+)',
            r'Reference\s*(?:Number)?:?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_report_date(self, text: str) -> Optional[str]:
        """Extract report creation date"""
        patterns = [
            r'Report\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Created:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_submission_date(self, text: str) -> Optional[str]:
        """Extract submission date"""
        patterns = [
            r'Submitted:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Submission\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date\s+Submitted:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_employee_info(self, text: str) -> Dict[str, Any]:
        """Extract employee/submitter information"""
        employee = {}

        # Employee name
        name_patterns = [
            r'Employee\s+Name:?\s*(.+?)(?:\n|$)',
            r'Submitted\s+[Bb]y:?\s*(.+?)(?:\n|$)',
            r'Name:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                employee['name'] = match.group(1).strip()
                break

        # Employee ID
        id_patterns = [
            r'Employee\s+(?:ID|Number):?\s*([A-Z0-9-]+)',
            r'EID:?\s*([A-Z0-9-]+)',
            r'Staff\s+ID:?\s*([A-Z0-9-]+)',
        ]

        for pattern in id_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                employee['employee_id'] = match.group(1).strip()
                break

        # Email
        employee['email'] = self._extract_email(text)

        # Phone
        employee['phone'] = self._extract_phone(text)

        # Title/Position
        title_patterns = [
            r'Title:?\s*(.+?)(?:\n|$)',
            r'Position:?\s*(.+?)(?:\n|$)',
            r'Job\s+Title:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in title_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                employee['title'] = match.group(1).strip()
                break

        return employee

    def _extract_department_info(self, text: str) -> Dict[str, Any]:
        """Extract department and organizational information"""
        dept_info = {}

        # Department
        dept_patterns = [
            r'Department:?\s*(.+?)(?:\n|$)',
            r'Dept:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in dept_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dept_info['department'] = match.group(1).strip()
                break

        # Cost Center
        cc_patterns = [
            r'Cost\s+Center:?\s*([A-Z0-9-]+)',
            r'CC:?\s*([A-Z0-9-]+)',
        ]

        for pattern in cc_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dept_info['cost_center'] = match.group(1).strip()
                break

        # Project Code
        project_patterns = [
            r'Project\s+Code:?\s*([A-Z0-9-]+)',
            r'Project:?\s*([A-Z0-9-]+)',
        ]

        for pattern in project_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dept_info['project_code'] = match.group(1).strip()
                break

        # Manager
        manager_patterns = [
            r'Manager:?\s*(.+?)(?:\n|$)',
            r'Approver:?\s*(.+?)(?:\n|$)',
            r'Supervisor:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in manager_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dept_info['manager'] = match.group(1).strip()
                break

        return dept_info

    def _extract_expense_period(self, text: str) -> Optional[Dict[str, str]]:
        """Extract expense reporting period"""
        period = {}

        # Pattern: "Expense Period: 01/01/2024 - 01/31/2024"
        pattern = r'(?:Expense|Report)\s+Period:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*(?:-|to)\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
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

        return period if period else None

    def _extract_expenses(self, text: str, tables: List) -> List[Dict[str, Any]]:
        """Extract individual expense line items"""
        expenses = []

        # Try to find expenses table
        for table in tables:
            if len(table) < 2:
                continue

            header = [str(cell).lower() for cell in table[0]]

            # Check if this looks like an expense table
            if not any(keyword in ' '.join(header) for keyword in
                       ['date', 'expense', 'category', 'amount', 'description']):
                continue

            # Find column indices
            col_indices = self._find_expense_column_indices(header)

            # Extract expenses from data rows
            for row in table[1:]:
                if len(row) < 2:
                    continue

                expense = self._parse_expense_item(row, col_indices)
                if expense and (expense.get('description') or expense.get('category')):
                    expenses.append(expense)

        # If no expenses in tables, try text extraction
        if not expenses:
            expenses = self._extract_expenses_from_text(text)

        return expenses

    def _find_expense_column_indices(self, header: List[str]) -> Dict[str, int]:
        """Find column indices for expense fields"""
        indices = {}

        for i, col in enumerate(header):
            col_lower = col.lower()

            if any(keyword in col_lower for keyword in ['date', 'expense date', 'transaction date']):
                indices['date'] = i
            elif any(keyword in col_lower for keyword in ['description', 'item', 'expense']):
                indices['description'] = i
            elif any(keyword in col_lower for keyword in ['category', 'type', 'expense type']):
                indices['category'] = i
            elif any(keyword in col_lower for keyword in ['merchant', 'vendor', 'supplier']):
                indices['merchant'] = i
            elif any(keyword in col_lower for keyword in ['location', 'city']):
                indices['location'] = i
            elif any(keyword in col_lower for keyword in ['amount', 'total', 'cost']):
                indices['amount'] = i
            elif any(keyword in col_lower for keyword in ['payment', 'method', 'card']):
                indices['payment_method'] = i
            elif any(keyword in col_lower for keyword in ['receipt', 'attached']):
                indices['receipt'] = i
            elif any(keyword in col_lower for keyword in ['business purpose', 'purpose', 'reason']):
                indices['business_purpose'] = i
            elif any(keyword in col_lower for keyword in ['billable', 'client', 'project']):
                indices['billable'] = i
            elif any(keyword in col_lower for keyword in ['reimbursable', 'reimbursement']):
                indices['reimbursable'] = i

        return indices

    def _parse_expense_item(self, row: List, col_indices: Dict[str, int]) -> Dict[str, Any]:
        """Parse a single expense line item"""
        expense = {
            'date': None,
            'description': None,
            'category': None,
            'merchant': None,
            'location': None,
            'amount': None,
            'payment_method': None,
            'receipt_attached': None,
            'business_purpose': None,
            'billable': None,
            'reimbursable': True,  # Default
        }

        for field, index in col_indices.items():
            if index < len(row):
                value = str(row[index]).strip()

                if field == 'amount':
                    expense[field] = self._parse_financial_value(value)
                elif field == 'receipt_attached':
                    expense[field] = self._parse_boolean(value)
                elif field == 'billable':
                    expense[field] = self._parse_boolean(value)
                elif field == 'reimbursable':
                    expense[field] = self._parse_boolean(value)
                else:
                    expense[field] = value if value else None

        return expense

    def _parse_boolean(self, value: str) -> Optional[bool]:
        """Parse boolean values from text"""
        if not value:
            return None

        value_lower = value.lower().strip()

        if value_lower in ['yes', 'y', 'true', '1', 'x', '✓', '✔']:
            return True
        elif value_lower in ['no', 'n', 'false', '0', '']:
            return False

        return None

    def _extract_expenses_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Fallback: Extract expenses from text"""
        expenses = []

        # Pattern: "01/15/2024 - Hotel - Marriott - $250.00"
        pattern = r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*[-:]\s*(.+?)\s*[-:]\s*(.+?)\s*[-:]\s*\$?([\d,]+\.?\d*)'

        matches = re.finditer(pattern, text)
        for match in matches:
            expense = {
                'date': match.group(1),
                'category': match.group(2).strip(),
                'description': match.group(3).strip(),
                'amount': self._parse_financial_value(match.group(4)),
            }
            expenses.append(expense)

        return expenses

    def _calculate_category_totals(self, expenses: List[Dict]) -> Dict[str, float]:
        """Calculate total spending by category"""
        category_totals = {}

        for expense in expenses:
            category = expense.get('category', 'Uncategorized')
            amount = expense.get('amount', 0) or 0

            if category in category_totals:
                category_totals[category] += amount
            else:
                category_totals[category] = amount

        # Sort by amount descending
        return dict(sorted(category_totals.items(), key=lambda x: x[1], reverse=True))

    def _get_top_categories(self, category_totals: Dict[str, float], top_n: int = 5) -> List[Dict[str, Any]]:
        """Get top N spending categories"""
        top_categories = []

        for category, total in list(category_totals.items())[:top_n]:
            top_categories.append({
                'category': category,
                'total': round(total, 2)
            })

        return top_categories

    def _extract_payment_info(self, text: str) -> Dict[str, Any]:
        """Extract payment/reimbursement information"""
        payment = {}

        # Payment method
        method_patterns = [
            r'Reimbursement\s+Method:?\s*(.+?)(?:\n|$)',
            r'Payment\s+Method:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in method_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                payment['method'] = match.group(1).strip()
                break

        # Bank account (masked)
        account_pattern = r'Account:?\s*([X*]{4,}[-\s]?\d{4})'
        match = re.search(account_pattern, text, re.IGNORECASE)
        if match:
            payment['bank_account'] = match.group(1).strip()

        # Payment status
        status_pattern = r'Payment\s+Status:?\s*(.+?)(?:\n|$)'
        match = re.search(status_pattern, text, re.IGNORECASE)
        if match:
            payment['status'] = match.group(1).strip()
        else:
            # Infer status
            if any(word in text.lower() for word in ['paid', 'reimbursed', 'processed']):
                payment['status'] = 'Paid'
            elif any(word in text.lower() for word in ['pending', 'processing']):
                payment['status'] = 'Pending'
            else:
                payment['status'] = 'Not Processed'

        # Payment date
        payment_date_pattern = r'(?:Payment|Reimbursement)\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(payment_date_pattern, text, re.IGNORECASE)
        if match:
            payment['payment_date'] = match.group(1).strip()

        return payment

    def _extract_approval_info(self, text: str) -> Dict[str, Any]:
        """Extract approval workflow information"""
        approval = {}

        # Status
        status_pattern = r'Status:?\s*(.+?)(?:\n|$)'
        match = re.search(status_pattern, text, re.IGNORECASE)
        if match:
            approval['status'] = match.group(1).strip()
        else:
            # Infer status
            if any(word in text.lower() for word in ['approved', 'accepted']):
                approval['status'] = 'Approved'
            elif any(word in text.lower() for word in ['rejected', 'denied']):
                approval['status'] = 'Rejected'
            elif any(word in text.lower() for word in ['pending', 'submitted', 'under review']):
                approval['status'] = 'Pending'
            else:
                approval['status'] = 'Draft'

        # Approved by
        approver_patterns = [
            r'Approved\s+[Bb]y:?\s*(.+?)(?:\n|$)',
            r'Approver:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in approver_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                approval['approved_by'] = match.group(1).strip()
                break

        # Approval date
        approval_date_pattern = r'Approval\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(approval_date_pattern, text, re.IGNORECASE)
        if match:
            approval['approval_date'] = match.group(1).strip()

        # Comments
        comment_patterns = [
            r'(?:Approver\s+)?Comments?:?\s*(.+?)(?:\n\n|$)',
            r'Notes?:?\s*(.+?)(?:\n\n|$)',
        ]

        for pattern in comment_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                approval['comments'] = match.group(1).strip()
                break

        # Workflow stage
        workflow_pattern = r'Workflow\s+Stage:?\s*(.+?)(?:\n|$)'
        match = re.search(workflow_pattern, text, re.IGNORECASE)
        if match:
            approval['workflow_stage'] = match.group(1).strip()

        return approval

    def _calculate_totals(self, expenses: List[Dict], text: str, tables: List) -> Dict[str, Any]:
        """Calculate total amounts"""
        totals = {}

        # Calculate from expenses
        if expenses:
            # Subtotal (all expenses)
            subtotal = sum(expense.get('amount', 0) or 0 for expense in expenses)
            totals['subtotal'] = round(subtotal, 2)

            # Reimbursable
            reimbursable = sum(
                expense.get('amount', 0) or 0
                for expense in expenses
                if expense.get('reimbursable', True)
            )
            totals['reimbursable'] = round(reimbursable, 2)

            # Non-reimbursable
            non_reimbursable = sum(
                expense.get('amount', 0) or 0
                for expense in expenses
                if not expense.get('reimbursable', True)
            )
            totals['non_reimbursable'] = round(non_reimbursable, 2)

        # Extract stated totals
        stated_subtotal = self._extract_amount(text, ['Subtotal', 'Sub-Total'])
        if stated_subtotal:
            totals['subtotal'] = stated_subtotal

        stated_reimbursable = self._extract_amount(text, [
            'Total Reimbursable', 'Reimbursable Amount', 'Amount to Reimburse'
        ])
        if stated_reimbursable:
            totals['reimbursable'] = stated_reimbursable

        # Tax
        totals['tax'] = self._extract_amount(text, ['Tax', 'Sales Tax', 'VAT', 'GST'])

        # Total
        totals['total'] = self._extract_amount(text, [
            'Total', 'Grand Total', 'Total Amount'
        ]) or totals.get('subtotal')

        # Currency
        totals['currency'] = self._extract_currency_type(text) or 'USD'

        return totals

    def _extract_policy_info(self, text: str) -> Dict[str, Any]:
        """Extract policy compliance information"""
        policy = {
            'violations': [],
            'warnings': [],
            'notes': None,
            'out_of_policy_amount': None
        }

        # Look for policy violations
        violation_keywords = [
            'policy violation', 'exceeds policy', 'out of policy',
            'not compliant', 'violates policy', 'policy breach'
        ]

        for keyword in violation_keywords:
            if keyword in text.lower():
                # Extract the context around the violation
                pattern = rf'({re.escape(keyword)}.{{0,100}})'
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    policy['violations'].append(match.group(1).strip())

        # Look for warnings
        warning_keywords = [
            'warning', 'missing receipt', 'requires justification',
            'needs approval', 'review required'
        ]

        for keyword in warning_keywords:
            if keyword in text.lower():
                pattern = rf'({re.escape(keyword)}.{{0,100}})'
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    policy['warnings'].append(match.group(1).strip())

        # Out of policy amount
        oop_amount = self._extract_amount(text, [
            'Out of Policy', 'Policy Exception', 'Over Limit'
        ])
        if oop_amount:
            policy['out_of_policy_amount'] = oop_amount

        # Notes
        notes_pattern = r'(?:Policy\s+)?Notes?:?\s*(.+?)(?:\n\n|$)'
        match = re.search(notes_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            policy['notes'] = match.group(1).strip()

        return policy

    def _count_receipts(self, expenses: List[Dict]) -> int:
        """Count expenses with receipts attached"""
        return sum(1 for expense in expenses if expense.get('receipt_attached'))

    def _count_missing_receipts(self, expenses: List[Dict]) -> int:
        """Count expenses missing receipts"""
        return sum(
            1 for expense in expenses
            if expense.get('receipt_attached') == False or
            (expense.get('receipt_attached') is None and expense.get('amount', 0) > 25)
        )

    def _validate_expense_report(self, expenses: List[Dict], totals: Dict) -> Dict[str, Any]:
        """Validate expense report"""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }

        # Check for expenses
        if not expenses:
            validation['errors'].append("No expenses found")
            validation['is_valid'] = False

        # Check for missing receipts (expenses over $25 typically require receipts)
        missing_receipts = []
        for i, expense in enumerate(expenses, 1):
            amount = expense.get('amount', 0) or 0
            receipt = expense.get('receipt_attached')

            if amount > 25 and receipt == False:
                missing_receipts.append(f"Line {i}: ${amount:.2f} missing receipt")

        if missing_receipts:
            validation['warnings'].extend(missing_receipts)

        # Check for missing business purposes
        missing_purposes = []
        for i, expense in enumerate(expenses, 1):
            if not expense.get('business_purpose'):
                missing_purposes.append(f"Line {i}: Missing business purpose")

        if missing_purposes and len(missing_purposes) > len(expenses) * 0.5:
            validation['warnings'].append(f"{len(missing_purposes)} expenses missing business purpose")

        # Validate totals
        if expenses and totals.get('subtotal'):
            calculated_total = sum(expense.get('amount', 0) or 0 for expense in expenses)
            stated_total = totals['subtotal']
            difference = abs(calculated_total - stated_total)

            if difference > 0.01:
                validation['warnings'].append(
                    f"Total mismatch: Calculated ${calculated_total:.2f} vs Stated ${stated_total:.2f}"
                )

        # Check for duplicate expenses (same date, merchant, amount)
        duplicates = self._find_duplicate_expenses(expenses)
        if duplicates:
            validation['warnings'].extend(duplicates)

        validation['is_valid'] = len(validation['errors']) == 0

        return validation

    def _find_duplicate_expenses(self, expenses: List[Dict]) -> List[str]:
        """Find potential duplicate expenses"""
        duplicates = []
        seen = {}

        for i, expense in enumerate(expenses, 1):
            key = (
                expense.get('date'),
                expense.get('merchant'),
                expense.get('amount')
            )

            if all(key) and key in seen:
                duplicates.append(
                    f"Possible duplicate: Line {i} matches Line {seen[key]}"
                )
            else:
                seen[key] = i

        return duplicates

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
extractor = ExpenseReportExtractor()
result = extractor.extract(text, tables)

print(f"Report ID: {result['report_id']}")
print(f"Employee: {result['employee_name']} ({result['employee_id']})")
print(f"Department: {result['department']}")
print(f"Period: {result['period_start']} to {result['period_end']}")
print(f"\nExpenses ({result['expense_count']} items):")
for expense in result['expenses']:
    print(f"  {expense['date']} - {expense['category']}: {expense['description']} - ${expense['amount']:.2f}")
print(f"\nTop Categories:")
for cat in result['top_categories']:
    print(f"  {cat['category']}: ${cat['total']:.2f}")
print(f"\nSubtotal: ${result['subtotal']:.2f}")
print(f"Reimbursable: ${result['reimbursable']:.2f}")
print(f"Non-Reimbursable: ${result['non_reimbursable']:.2f}")
print(f"Total: ${result['total_amount']:.2f}")
print(f"\nStatus: {result['status']}")
print(f"Approved By: {result['approved_by']}")
print(f"Payment Status: {result['payment_status']}")
print(f"\nReceipts Attached: {result['receipts_attached']}")
print(f"Missing Receipts: {result['missing_receipts']}")
if result['policy_violations']:
    print(f"\nPolicy Violations: {result['policy_violations']}")
"""