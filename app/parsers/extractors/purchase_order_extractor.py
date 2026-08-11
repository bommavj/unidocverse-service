from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal

from app.parsers.extractors.base_extractor import BaseExtractor


class PurchaseOrderExtractor(BaseExtractor):
    """
    Extract data from Purchase Orders including:
    - PO number and dates
    - Buyer and supplier information
    - Line items with quantities and prices
    - Shipping and billing addresses
    - Terms and conditions
    - Total amounts
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Extract PO metadata
        po_number = self._extract_po_number(text)
        po_date = self._extract_po_date(text)
        required_date = self._extract_required_date(text)

        # Extract party information
        buyer = self._extract_buyer_info(text)
        supplier = self._extract_supplier_info(text)

        # Extract addresses
        ship_to = self._extract_ship_to_address(text)
        bill_to = self._extract_bill_to_address(text)

        # Extract line items (the core of PO)
        line_items = self._extract_line_items(text, tables)

        # Extract payment and delivery terms
        terms = self._extract_terms(text)

        # Extract shipping information
        shipping = self._extract_shipping_info(text)

        # Calculate totals
        totals = self._calculate_totals(line_items, text, tables)

        # Extract approval/status information
        approval = self._extract_approval_info(text)

        # Validate PO
        validation = self._validate_po(line_items, totals)

        return {
            # Metadata
            'document_type': 'purchase_order',
            'po_number': po_number,
            'po_date': po_date,
            'required_date': required_date,
            'delivery_date': required_date,  # Alias
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Parties
            'buyer': buyer,
            'supplier': supplier,
            'vendor': supplier,  # Alias

            # Addresses
            'ship_to_address': ship_to,
            'bill_to_address': bill_to,

            # Line Items
            'line_items': line_items,
            'item_count': len(line_items),

            # Terms
            'payment_terms': terms.get('payment_terms'),
            'delivery_terms': terms.get('delivery_terms'),
            'incoterms': terms.get('incoterms'),

            # Shipping
            'shipping_method': shipping.get('method'),
            'shipping_carrier': shipping.get('carrier'),
            'tracking_number': shipping.get('tracking'),

            # Financials
            'subtotal': totals.get('subtotal'),
            'tax_amount': totals.get('tax'),
            'shipping_cost': totals.get('shipping'),
            'discount_amount': totals.get('discount'),
            'total_amount': totals.get('total'),
            'currency': totals.get('currency', 'USD'),

            # Approval
            'status': approval.get('status'),
            'approved_by': approval.get('approved_by'),
            'approval_date': approval.get('approval_date'),
            'requisition_number': approval.get('requisition_number'),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _extract_po_number(self, text: str) -> Optional[str]:
        """Extract purchase order number"""
        patterns = [
            r'PO\s*(?:Number|#|No\.?)?:?\s*([A-Z0-9-]+)',
            r'Purchase\s+Order\s*(?:Number|#|No\.?)?:?\s*([A-Z0-9-]+)',
            r'Order\s*(?:Number|#|No\.?)?:?\s*([A-Z0-9-]+)',
            r'P\.O\.\s*(?:Number|#)?:?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_po_date(self, text: str) -> Optional[str]:
        """Extract PO issue date"""
        patterns = [
            r'PO\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Order\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Issue\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'(\w+\s+\d{1,2},?\s+\d{4})',  # "January 30, 2024"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_required_date(self, text: str) -> Optional[str]:
        """Extract required/delivery date"""
        patterns = [
            r'Required\s+(?:By|Date):?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Delivery\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Need\s+By:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Expected\s+Delivery:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_buyer_info(self, text: str) -> Dict[str, Any]:
        """Extract buyer/customer information"""
        buyer = {}

        # Find buyer section
        buyer_section = self._extract_section(text, [
            'Buyer', 'Customer', 'Purchaser', 'Ordered By', 'Bill To'
        ])

        if buyer_section:
            buyer['name'] = self._extract_company_name(buyer_section)
            buyer['contact_person'] = self._extract_contact_person(buyer_section)
            buyer['email'] = self._extract_email(buyer_section)
            buyer['phone'] = self._extract_phone(buyer_section)
            buyer['address'] = self._extract_address_from_section(buyer_section)

        return buyer

    def _extract_supplier_info(self, text: str) -> Dict[str, Any]:
        """Extract supplier/vendor information"""
        supplier = {}

        # Find supplier section
        supplier_section = self._extract_section(text, [
            'Supplier', 'Vendor', 'Seller', 'Ship From', 'Sold By'
        ])

        if supplier_section:
            supplier['name'] = self._extract_company_name(supplier_section)
            supplier['contact_person'] = self._extract_contact_person(supplier_section)
            supplier['email'] = self._extract_email(supplier_section)
            supplier['phone'] = self._extract_phone(supplier_section)
            supplier['address'] = self._extract_address_from_section(supplier_section)
            supplier['vendor_id'] = self._extract_vendor_id(text)

        return supplier

    def _extract_vendor_id(self, text: str) -> Optional[str]:
        """Extract vendor/supplier ID"""
        patterns = [
            r'Vendor\s+(?:ID|#|Number):?\s*([A-Z0-9-]+)',
            r'Supplier\s+(?:ID|#|Number):?\s*([A-Z0-9-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_ship_to_address(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract shipping address"""
        section = self._extract_section(text, ['Ship To', 'Shipping Address', 'Deliver To'])
        if section:
            return self._parse_address(section)
        return None

    def _extract_bill_to_address(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract billing address"""
        section = self._extract_section(text, ['Bill To', 'Billing Address', 'Invoice To'])
        if section:
            return self._parse_address(section)
        return None

    def _extract_section(self, text: str, headers: List[str]) -> Optional[str]:
        """Extract a section of text based on header keywords"""
        for header in headers:
            # Look for header followed by content until next major header or blank lines
            pattern = rf'{re.escape(header)}[:\s]+((?:.+\n?){{1,10}})'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_company_name(self, section: str) -> Optional[str]:
        """Extract company name from a text section"""
        lines = section.split('\n')
        for line in lines:
            line = line.strip()
            # Company names often appear first or contain Inc, LLC, Corp, etc.
            if any(suffix in line for suffix in ['Inc', 'LLC', 'Corp', 'Ltd', 'Company', 'Co.']):
                return line
            # Or just take the first substantial line
            if len(line) > 5 and not re.match(r'^\d', line):
                return line
        return None

    def _extract_contact_person(self, section: str) -> Optional[str]:
        """Extract contact person name"""
        patterns = [
            r'(?:Contact|Attn|Attention):?\s*(.+)',
            r'(?:Name):?\s*(.+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, section, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_address_from_section(self, section: str) -> Optional[str]:
        """Extract full address from section"""
        # Simple approach: take all lines that look like address components
        lines = section.split('\n')
        address_lines = []

        for line in lines:
            line = line.strip()
            # Skip headers and labels
            if any(label in line.lower() for label in ['buyer', 'supplier', 'vendor', 'contact', 'email', 'phone']):
                continue
            # Keep lines that look like addresses
            if line and (re.search(r'\d', line) or ',' in line):
                address_lines.append(line)

        return ', '.join(address_lines) if address_lines else None

    def _parse_address(self, section: str) -> Dict[str, Any]:
        """Parse address into components"""
        address = {
            'full_address': section.strip(),
            'street': None,
            'city': None,
            'state': None,
            'zip_code': None,
            'country': None,
        }

        # Extract ZIP code
        zip_match = re.search(r'\b(\d{5}(?:-\d{4})?)\b', section)
        if zip_match:
            address['zip_code'] = zip_match.group(1)

        # Extract state (2-letter code)
        state_match = re.search(r'\b([A-Z]{2})\s+\d{5}', section)
        if state_match:
            address['state'] = state_match.group(1)

        # Try to extract city (word before state)
        city_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),?\s+[A-Z]{2}\s+\d{5}', section)
        if city_match:
            address['city'] = city_match.group(1)

        return address

    def _extract_line_items(self, text: str, tables: List) -> List[Dict[str, Any]]:
        """Extract purchase order line items from tables"""
        line_items = []

        # Try to find line items table
        for table in tables:
            if len(table) < 2:  # Need at least header + 1 row
                continue

            # Check if this looks like a line items table
            header = [str(cell).lower() for cell in table[0]]

            if not any(keyword in ' '.join(header) for keyword in ['item', 'description', 'quantity', 'qty', 'price']):
                continue

            # Find column indices
            col_indices = self._find_column_indices(header)

            # Extract items from data rows
            for row in table[1:]:
                if len(row) < 2:
                    continue

                item = self._parse_line_item(row, col_indices)
                if item and item.get('description'):  # Only add if we got meaningful data
                    line_items.append(item)

        # If no items found in tables, try text-based extraction
        if not line_items:
            line_items = self._extract_line_items_from_text(text)

        return line_items

    def _find_column_indices(self, header: List[str]) -> Dict[str, int]:
        """Find column indices for common line item fields"""
        indices = {}

        for i, col in enumerate(header):
            col_lower = col.lower()

            if any(keyword in col_lower for keyword in ['item', 'part', 'sku', 'product']):
                indices['item_number'] = i
            elif any(keyword in col_lower for keyword in ['description', 'desc']):
                indices['description'] = i
            elif any(keyword in col_lower for keyword in ['qty', 'quantity', 'ordered']):
                indices['quantity'] = i
            elif any(keyword in col_lower for keyword in ['uom', 'unit', 'each']):
                indices['unit'] = i
            elif any(keyword in col_lower for keyword in ['unit price', 'price', 'rate']):
                indices['unit_price'] = i
            elif any(keyword in col_lower for keyword in ['total', 'amount', 'line total', 'extended']):
                indices['line_total'] = i
            elif any(keyword in col_lower for keyword in ['delivery', 'ship', 'req']):
                indices['delivery_date'] = i

        return indices

    def _parse_line_item(self, row: List, col_indices: Dict[str, int]) -> Dict[str, Any]:
        """Parse a single line item row"""
        item = {
            'line_number': None,
            'item_number': None,
            'description': None,
            'quantity': None,
            'unit': None,
            'unit_price': None,
            'line_total': None,
            'delivery_date': None,
        }

        # Extract fields based on column indices
        for field, index in col_indices.items():
            if index < len(row):
                value = str(row[index]).strip()

                if field in ['quantity', 'unit_price', 'line_total']:
                    item[field] = self._parse_financial_value(value)
                else:
                    item[field] = value if value else None

        # If line total not provided, calculate it
        if item['quantity'] and item['unit_price'] and not item['line_total']:
            item['line_total'] = round(item['quantity'] * item['unit_price'], 2)

        return item

    def _extract_line_items_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Fallback: Extract line items from text using patterns"""
        line_items = []

        # Pattern: "Item: Description Qty: 10 Price: $100"
        pattern = r'(?:Item|Product):\s*(.+?)\s+(?:Qty|Quantity):\s*(\d+)\s+(?:Price|Rate):\s*\$?([\d,]+\.?\d*)'

        matches = re.finditer(pattern, text, re.IGNORECASE)
        for i, match in enumerate(matches, 1):
            item = {
                'line_number': i,
                'description': match.group(1).strip(),
                'quantity': int(match.group(2)),
                'unit_price': self._parse_financial_value(match.group(3)),
            }
            item['line_total'] = item['quantity'] * item['unit_price']
            line_items.append(item)

        return line_items

    def _extract_terms(self, text: str) -> Dict[str, Any]:
        """Extract payment and delivery terms"""
        terms = {}

        # Payment terms
        payment_patterns = [
            r'Payment\s+Terms?:?\s*(.+?)(?:\n|$)',
            r'Terms?:?\s*(Net\s+\d+|COD|Due\s+on\s+Receipt|[A-Z][a-z\s]+)(?:\n|$)',
        ]

        for pattern in payment_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                terms['payment_terms'] = match.group(1).strip()
                break

        # Delivery terms
        delivery_patterns = [
            r'Delivery\s+Terms?:?\s*(.+?)(?:\n|$)',
            r'Shipping\s+Terms?:?\s*(.+?)(?:\n|$)',
        ]

        for pattern in delivery_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                terms['delivery_terms'] = match.group(1).strip()
                break

        # Incoterms (FOB, CIF, etc.)
        incoterms_pattern = r'\b(FOB|CIF|EXW|FCA|CPT|CIP|DAP|DPU|DDP)\b'
        match = re.search(incoterms_pattern, text, re.IGNORECASE)
        if match:
            terms['incoterms'] = match.group(1).upper()

        return terms

    def _extract_shipping_info(self, text: str) -> Dict[str, Any]:
        """Extract shipping method and carrier information"""
        shipping = {}

        # Shipping method
        method_pattern = r'Shipping\s+Method:?\s*(.+?)(?:\n|$)'
        match = re.search(method_pattern, text, re.IGNORECASE)
        if match:
            shipping['method'] = match.group(1).strip()

        # Carrier
        carrier_pattern = r'Carrier:?\s*(.+?)(?:\n|$)'
        match = re.search(carrier_pattern, text, re.IGNORECASE)
        if match:
            shipping['carrier'] = match.group(1).strip()

        # Tracking number