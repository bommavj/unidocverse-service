from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class IncomeStatementExtractor(BaseExtractor):
    """
    Extract financial data from Income Statements (P&L) including:
    - Revenue streams
    - Cost of Goods Sold (COGS)
    - Operating expenses
    - Net income
    - Earnings per share (EPS)
    - Profitability metrics
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Extract basic metadata
        company_name = self._extract_company_name(text)
        period = self._extract_period(text)
        period_type = self._extract_period_type(text)
        currency = self._extract_currency_type(text)

        # Extract revenue components
        revenue = self._extract_revenue(text, tables)

        # Extract cost components
        costs = self._extract_costs(text, tables)

        # Extract operating expenses
        operating_expenses = self._extract_operating_expenses(text, tables)

        # Extract other income/expenses
        other_items = self._extract_other_items(text, tables)

        # Extract bottom line items
        profit_metrics = self._extract_profit_metrics(text, tables)

        # Extract EPS and shares
        eps_data = self._extract_eps_data(text, tables)

        # Calculate margins and ratios
        margins = self._calculate_margins(revenue, costs, operating_expenses, profit_metrics)

        # Verify calculations
        validation = self._validate_calculations(revenue, costs, operating_expenses, other_items, profit_metrics)

        return {
            # Metadata
            'document_type': 'income_statement',
            'company_name': company_name,
            'period': period,
            'period_type': period_type,  # 'quarterly', 'annual', 'ytd'
            'currency': currency or 'USD',
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Revenue
            'revenue': revenue,

            # Costs
            'costs': costs,

            # Operating Expenses
            'operating_expenses': operating_expenses,

            # Other Items
            'other_items': other_items,

            # Profit Metrics
            'profit_metrics': profit_metrics,

            # EPS Data
            'eps': eps_data,

            # Calculated Metrics
            'margins': margins,

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _extract_company_name(self, text: str) -> Optional[str]:
        """Extract company name from income statement"""
        patterns = [
            r'(?:Income Statement|Statement of Operations|P&L)\s+(?:of|for)?\s*([A-Z][A-Za-z\s&.,Inc]+?)(?:\n|For the)',
            r'^([A-Z][A-Za-z\s&.,Inc]+?)\s+(?:Consolidated\s+)?(?:Income Statement|Statement of Operations)',
            r'Company:\s*(.+?)(?:\n|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_period(self, text: str) -> Optional[str]:
        """Extract the reporting period"""
        patterns = [
            r'For the (?:year|period) ended?\s+(\w+\s+\d{1,2},?\s+\d{4})',
            r'(?:Three|Six|Nine|Twelve) Months Ended\s+(\w+\s+\d{1,2},?\s+\d{4})',
            r'For the (?:quarter|year) ended?\s+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Period:\s*(.+?)(?:\n|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_period_type(self, text: str) -> str:
        """Determine if quarterly, annual, or YTD"""
        text_lower = text.lower()

        if any(term in text_lower for term in ['three months', 'q1', 'q2', 'q3', 'q4', 'quarterly']):
            return 'quarterly'
        elif any(term in text_lower for term in ['twelve months', 'annual', 'year ended', 'fiscal year']):
            return 'annual'
        elif 'ytd' in text_lower or 'year to date' in text_lower:
            return 'ytd'
        else:
            return 'unknown'

    def _extract_revenue(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract all revenue line items"""

        revenue = {
            # Top line revenue
            'gross_revenue': self._extract_line_item(text, tables, [
                'Revenue',
                'Total Revenue',
                'Gross Revenue',
                'Net Sales',
                'Sales Revenue',
            ]),

            # Revenue components
            'product_revenue': self._extract_line_item(text, tables, [
                'Product Revenue',
                'Product Sales',
            ]),
            'service_revenue': self._extract_line_item(text, tables, [
                'Service Revenue',
                'Services',
            ]),
            'subscription_revenue': self._extract_line_item(text, tables, [
                'Subscription Revenue',
                'Recurring Revenue',
            ]),
            'licensing_revenue': self._extract_line_item(text, tables, [
                'Licensing Revenue',
                'License Fees',
            ]),

            # Deductions
            'returns_and_allowances': self._extract_line_item(text, tables, [
                'Returns and Allowances',
                'Sales Returns',
            ]),
            'discounts': self._extract_line_item(text, tables, [
                'Discounts',
                'Sales Discounts',
            ]),

            # Net revenue
            'net_revenue': self._extract_line_item(text, tables, [
                'Net Revenue',
                'Net Sales',
                'Revenue, Net',
            ]),
        }

        # If net revenue not found, use gross revenue
        if not revenue['net_revenue'] and revenue['gross_revenue']:
            revenue['net_revenue'] = revenue['gross_revenue']

        return revenue

    def _extract_costs(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract cost of goods sold and related items"""

        costs = {
            'cogs': self._extract_line_item(text, tables, [
                'Cost of Goods Sold',
                'Cost of Sales',
                'Cost of Revenue',
                'COGS',
            ]),

            # COGS Components
            'cost_of_products': self._extract_line_item(text, tables, [
                'Cost of Products',
                'Product Costs',
            ]),
            'cost_of_services': self._extract_line_item(text, tables, [
                'Cost of Services',
                'Service Costs',
            ]),

            # Gross Profit
            'gross_profit': self._extract_line_item(text, tables, [
                'Gross Profit',
                'Gross Income',
            ]),
        }

        return costs

    def _extract_operating_expenses(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract operating expense line items"""

        opex = {
            # Major categories
            'research_and_development': self._extract_line_item(text, tables, [
                'Research and Development',
                'R&D',
                'Research & Development',
            ]),
            'sales_and_marketing': self._extract_line_item(text, tables, [
                'Sales and Marketing',
                'Selling and Marketing',
                'Sales & Marketing',
            ]),
            'general_and_administrative': self._extract_line_item(text, tables, [
                'General and Administrative',
                'G&A',
                'General & Administrative',
            ]),

            # Specific expenses
            'salaries_and_wages': self._extract_line_item(text, tables, [
                'Salaries and Wages',
                'Employee Compensation',
            ]),
            'rent_and_occupancy': self._extract_line_item(text, tables, [
                'Rent',
                'Occupancy',
                'Rent and Occupancy',
            ]),
            'depreciation': self._extract_line_item(text, tables, [
                'Depreciation',
                'Depreciation Expense',
            ]),
            'amortization': self._extract_line_item(text, tables, [
                'Amortization',
                'Amortization Expense',
            ]),
            'depreciation_and_amortization': self._extract_line_item(text, tables, [
                'Depreciation and Amortization',
                'D&A',
            ]),

            # Total
            'total_operating_expenses': self._extract_line_item(text, tables, [
                'Total Operating Expenses',
                'Operating Expenses',
                'Total OpEx',
            ]),

            # Operating Income
            'operating_income': self._extract_line_item(text, tables, [
                'Operating Income',
                'Income from Operations',
                'Operating Profit',
                'EBIT',
            ]),
        }

        return opex

    def _extract_other_items(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract non-operating income and expenses"""

        other = {
            # Interest
            'interest_income': self._extract_line_item(text, tables, [
                'Interest Income',
                'Interest Revenue',
            ]),
            'interest_expense': self._extract_line_item(text, tables, [
                'Interest Expense',
                'Interest Cost',
            ]),
            'net_interest': self._extract_line_item(text, tables, [
                'Net Interest',
                'Interest Income (Expense)',
            ]),

            # Other income/expenses
            'other_income': self._extract_line_item(text, tables, [
                'Other Income',
                'Non-operating Income',
            ]),
            'other_expense': self._extract_line_item(text, tables, [
                'Other Expense',
                'Non-operating Expense',
            ]),
            'gain_on_sale_of_assets': self._extract_line_item(text, tables, [
                'Gain on Sale of Assets',
                'Asset Sale Gains',
            ]),
            'foreign_exchange_gain_loss': self._extract_line_item(text, tables, [
                'Foreign Exchange Gain (Loss)',
                'FX Gain (Loss)',
            ]),

            # Investment income
            'investment_income': self._extract_line_item(text, tables, [
                'Investment Income',
                'Income from Investments',
            ]),
        }

        return other

    def _extract_profit_metrics(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract bottom-line profit metrics"""

        profit = {
            # Pre-tax income
            'income_before_tax': self._extract_line_item(text, tables, [
                'Income Before Tax',
                'Income Before Income Tax',
                'Pretax Income',
                'EBT',
            ]),

            # Tax
            'income_tax_expense': self._extract_line_item(text, tables, [
                'Income Tax Expense',
                'Provision for Income Tax',
                'Income Taxes',
            ]),
            'effective_tax_rate': self._extract_percentage(text, [
                'Effective Tax Rate',
            ]),

            # Net income
            'net_income': self._extract_line_item(text, tables, [
                'Net Income',
                'Net Earnings',
                'Net Profit',
                'Profit for the Period',
            ]),
            'net_income_continuing_ops': self._extract_line_item(text, tables, [
                'Income from Continuing Operations',
                'Net Income from Continuing Operations',
            ]),
            'net_income_discontinued_ops': self._extract_line_item(text, tables, [
                'Income from Discontinued Operations',
                'Discontinued Operations',
            ]),

            # Comprehensive income
            'comprehensive_income': self._extract_line_item(text, tables, [
                'Comprehensive Income',
                'Total Comprehensive Income',
            ]),
        }

        return profit

    def _extract_eps_data(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract earnings per share data"""

        eps = {
            # Basic EPS
            'basic_eps': self._extract_line_item(text, tables, [
                'Basic EPS',
                'Basic Earnings Per Share',
                'EPS - Basic',
            ]),

            # Diluted EPS
            'diluted_eps': self._extract_line_item(text, tables, [
                'Diluted EPS',
                'Diluted Earnings Per Share',
                'EPS - Diluted',
            ]),

            # Share counts
            'weighted_average_shares_basic': self._extract_line_item(text, tables, [
                'Weighted Average Shares Outstanding - Basic',
                'Basic Shares Outstanding',
            ]),
            'weighted_average_shares_diluted': self._extract_line_item(text, tables, [
                'Weighted Average Shares Outstanding - Diluted',
                'Diluted Shares Outstanding',
            ]),

            # Dividends
            'dividends_per_share': self._extract_line_item(text, tables, [
                'Dividends Per Share',
                'DPS',
            ]),
        }

        return eps

    def _extract_line_item(self, text: str, tables: List, search_terms: List[str]) -> Optional[float]:
        """
        Extract a specific line item value from text or tables
        Searches for multiple variations of the same term
        """

        # Try tables first (more structured)
        for table in tables:
            for row in table:
                if len(row) < 2:
                    continue

                label = str(row[0]).strip()

                # Check if label matches any search term
                for term in search_terms:
                    if term.lower() in label.lower():
                        # Try to extract number from second column
                        value = self._parse_financial_value(str(row[1]))
                        if value is not None:
                            return value

        # Fallback to text extraction
        for term in search_terms:
            # Pattern: "Line Item Name    $1,234,567" or "Line Item Name: 1,234,567"
            patterns = [
                rf'{re.escape(term)}\s*[:\s]+\$?\s*([\d,]+\.?\d*)',
                rf'{re.escape(term)}.*?\$\s*([\d,]+\.?\d*)',
                rf'{re.escape(term)}\s+\(?([\d,]+\.?\d*)\)?',
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = self._parse_financial_value(match.group(1))
                    if value:
                        return value

        return None

    def _extract_percentage(self, text: str, search_terms: List[str]) -> Optional[float]:
        """Extract percentage values"""
        for term in search_terms:
            pattern = rf'{re.escape(term)}\s*[:\s]+([\d.]+)%?'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    def _parse_financial_value(self, value_str: str) -> Optional[float]:
        """Parse financial values, handling different formats"""
        if not value_str:
            return None

        # Clean the string
        cleaned = re.sub(r'[^\d,.\-()]+', '', str(value_str))

        # Handle parentheses as negative (accounting convention)
        is_negative = '(' in value_str and ')' in value_str

        # Remove commas and parentheses
        cleaned = cleaned.replace(',', '').replace('(', '').replace(')', '')

        try:
            value = float(cleaned)
            return -abs(value) if is_negative else value
        except ValueError:
            return None

    def _calculate_margins(self, revenue: Dict, costs: Dict, opex: Dict, profit: Dict) -> Dict[str, Optional[float]]:
        """Calculate profitability margins"""

        net_revenue = revenue.get('net_revenue') or revenue.get('gross_revenue', 0)
        gross_profit = costs.get('gross_profit', 0)
        operating_income = opex.get('operating_income', 0)
        net_income = profit.get('net_income', 0)

        margins = {}

        if net_revenue:
            # Gross Margin
            if gross_profit:
                margins['gross_margin'] = round((gross_profit / net_revenue) * 100, 2)
            elif costs.get('cogs') is not None:
                calculated_gross_profit = net_revenue - costs['cogs']
                margins['gross_margin'] = round((calculated_gross_profit / net_revenue) * 100, 2)
            else:
                margins['gross_margin'] = None

            # Operating Margin
            if operating_income:
                margins['operating_margin'] = round((operating_income / net_revenue) * 100, 2)
            else:
                margins['operating_margin'] = None

            # Net Margin
            if net_income:
                margins['net_margin'] = round((net_income / net_revenue) * 100, 2)
            else:
                margins['net_margin'] = None

            # EBITDA Margin (if D&A available)
            da = opex.get('depreciation_and_amortization', 0)
            if operating_income and da:
                ebitda = operating_income + da
                margins['ebitda_margin'] = round((ebitda / net_revenue) * 100, 2)
                margins['ebitda'] = ebitda
            else:
                margins['ebitda_margin'] = None
                margins['ebitda'] = None
        else:
            margins['gross_margin'] = None
            margins['operating_margin'] = None
            margins['net_margin'] = None
            margins['ebitda_margin'] = None
            margins['ebitda'] = None

        return margins

    def _validate_calculations(self, revenue: Dict, costs: Dict, opex: Dict,
                               other: Dict, profit: Dict) -> Dict[str, Any]:
        """Validate key calculations in the income statement"""

        validation = {
            'warnings': [],
            'errors': []
        }

        net_revenue = revenue.get('net_revenue') or revenue.get('gross_revenue', 0)
        cogs = costs.get('cogs', 0)
        gross_profit = costs.get('gross_profit', 0)
        total_opex = opex.get('total_operating_expenses', 0)
        operating_income = opex.get('operating_income', 0)
        income_before_tax = profit.get('income_before_tax', 0)
        tax_expense = profit.get('income_tax_expense', 0)
        net_income = profit.get('net_income', 0)

        # Check: Gross Profit = Revenue - COGS
        if net_revenue and cogs and gross_profit:
            calculated_gp = net_revenue - cogs
            difference = abs(calculated_gp - gross_profit)
            tolerance = net_revenue * 0.001  # 0.1% tolerance

            if difference > tolerance:
                validation['warnings'].append(
                    f"Gross Profit mismatch: Stated ${gross_profit:,.2f} vs Calculated ${calculated_gp:,.2f}"
                )

        # Check: Operating Income = Gross Profit - Operating Expenses
        if gross_profit and total_opex and operating_income:
            calculated_oi = gross_profit - total_opex
            difference = abs(calculated_oi - operating_income)
            tolerance = net_revenue * 0.001

            if difference > tolerance:
                validation['warnings'].append(
                    f"Operating Income mismatch: Stated ${operating_income:,.2f} vs Calculated ${calculated_oi:,.2f}"
                )

        # Check: Net Income = Income Before Tax - Tax Expense
        if income_before_tax and tax_expense and net_income:
            calculated_ni = income_before_tax - tax_expense
            difference = abs(calculated_ni - net_income)
            tolerance = net_revenue * 0.001

            if difference > tolerance:
                validation['warnings'].append(
                    f"Net Income mismatch: Stated ${net_income:,.2f} vs Calculated ${calculated_ni:,.2f}"
                )

        # Check for missing critical fields
        if not net_revenue:
            validation['errors'].append("Revenue not found")
        if not net_income:
            validation['warnings'].append("Net Income not found")

        validation['is_valid'] = len(validation['errors']) == 0

        return validation


# Example usage in your pipeline:
"""
extractor = IncomeStatementExtractor()
result = extractor.extract(text, tables)

print(f"Company: {result['company_name']}")
print(f"Period: {result['period']} ({result['period_type']})")
print(f"Revenue: ${result['revenue']['net_revenue']:,.2f}")
print(f"Gross Profit: ${result['costs']['gross_profit']:,.2f}")
print(f"Operating Income: ${result['operating_expenses']['operating_income']:,.2f}")
print(f"Net Income: ${result['profit_metrics']['net_income']:,.2f}")
print(f"\nMargins:")
print(f"  Gross Margin: {result['margins']['gross_margin']:.2f}%")
print(f"  Operating Margin: {result['margins']['operating_margin']:.2f}%")
print(f"  Net Margin: {result['margins']['net_margin']:.2f}%")
print(f"\nEPS (Diluted): ${result['eps']['diluted_eps']:.2f}")
"""