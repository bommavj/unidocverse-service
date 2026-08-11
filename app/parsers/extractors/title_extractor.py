from typing import Dict, Any, List, Optional
from datetime import datetime
import re
from decimal import Decimal
from app.parsers.extractors.base_extractor import BaseExtractor


class TitleExtractor(BaseExtractor):
    """
    Extract data from Vehicle/Property Title documents including:
    - Vehicle titles (car, motorcycle, boat, RV)
    - Property/Real estate titles
    - Title information (number, issue date, brand)
    - Owner information (current and previous)
    - Lien holder information
    - Vehicle/Property details
    - Odometer readings
    - Title brands (salvage, rebuilt, flood, etc.)
    - Transfer information
    """

    def extract(self, text: str, tables: List) -> Dict[str, Any]:
        """Main extraction method"""

        # Determine title type
        title_type = self._determine_title_type(text)

        # Extract basic metadata
        title_number = self._extract_title_number(text)
        issue_date = self._extract_issue_date(text)
        issue_state = self._extract_issue_state(text)

        # Extract title brands/status
        title_brands = self._extract_title_brands(text)

        # Extract owner information
        current_owner = self._extract_current_owner(text)
        previous_owner = self._extract_previous_owner(text)

        # Extract lien holder information
        lien_holder = self._extract_lien_holder(text)

        # Extract asset details based on type
        if title_type in ['vehicle', 'car', 'motorcycle', 'boat', 'rv']:
            asset_details = self._extract_vehicle_details(text, tables)
        else:
            asset_details = self._extract_property_details(text, tables)

        # Extract odometer (for vehicles)
        odometer = None
        if title_type in ['vehicle', 'car', 'motorcycle', 'rv']:
            odometer = self._extract_odometer(text)

        # Extract transfer information
        transfer_info = self._extract_transfer_info(text)

        # Extract purchase/sale information
        sale_info = self._extract_sale_info(text)

        # Validation
        validation = self._validate_title(title_number, current_owner, asset_details)

        return {
            # Metadata
            'document_type': 'title',
            'title_type': title_type,
            'title_number': title_number,
            'certificate_number': title_number,  # Alias
            'issue_date': issue_date,
            'issue_state': issue_state,
            'issuing_state': issue_state,  # Alias
            'extraction_timestamp': datetime.utcnow().isoformat(),

            # Title Status
            'title_brands': title_brands,
            'is_clean_title': len(title_brands) == 0,
            'is_salvage': 'salvage' in [b.lower() for b in title_brands],
            'is_rebuilt': 'rebuilt' in [b.lower() for b in title_brands],
            'is_flood_damage': 'flood' in [b.lower() for b in title_brands],
            'has_lien': lien_holder.get('name') is not None,

            # Owner Information
            'current_owner': current_owner,
            'owner_name': current_owner.get('name'),
            'owner_address': current_owner.get('address'),
            'previous_owner': previous_owner,

            # Lien Holder
            'lien_holder': lien_holder,
            'lien_holder_name': lien_holder.get('name'),
            'lien_date': lien_holder.get('date'),
            'lien_release_date': lien_holder.get('release_date'),

            # Asset Details
            'asset_details': asset_details,

            # Vehicle-specific (if applicable)
            'vin': asset_details.get('vin'),
            'make': asset_details.get('make'),
            'model': asset_details.get('model'),
            'year': asset_details.get('year'),
            'color': asset_details.get('color'),
            'body_style': asset_details.get('body_style'),
            'odometer_reading': odometer.get('reading') if odometer else None,
            'odometer_unit': odometer.get('unit') if odometer else None,

            # Property-specific (if applicable)
            'property_address': asset_details.get('address'),
            'parcel_number': asset_details.get('parcel_number'),
            'legal_description': asset_details.get('legal_description'),

            # Transfer Information
            'transfer_date': transfer_info.get('transfer_date'),
            'transfer_type': transfer_info.get('transfer_type'),
            'transferor': transfer_info.get('transferor'),
            'transferee': transfer_info.get('transferee'),

            # Sale Information
            'sale_date': sale_info.get('sale_date'),
            'sale_price': sale_info.get('sale_price'),
            'purchase_price': sale_info.get('sale_price'),  # Alias
            'odometer_at_sale': sale_info.get('odometer'),

            # Validation
            'validation': validation,

            # Raw data
            'tables_found': len(tables),
        }

    def _determine_title_type(self, text: str) -> str:
        """Determine type of title"""
        text_lower = text.lower()

        # Vehicle types
        if any(keyword in text_lower for keyword in ['motorcycle', 'motorbike']):
            return 'motorcycle'
        elif any(keyword in text_lower for keyword in ['boat', 'vessel', 'watercraft']):
            return 'boat'
        elif any(keyword in text_lower for keyword in ['rv', 'recreational vehicle', 'motorhome', 'camper']):
            return 'rv'
        elif any(keyword in text_lower for keyword in ['trailer']):
            return 'trailer'
        elif any(keyword in text_lower for keyword in ['vehicle', 'automobile', 'car', 'truck', 'vin']):
            return 'vehicle'

        # Property/Real estate
        elif any(keyword in text_lower for keyword in ['property', 'real estate', 'deed', 'parcel']):
            return 'property'

        return 'vehicle'  # Default

    def _extract_title_number(self, text: str) -> Optional[str]:
        """Extract title/certificate number"""
        patterns = [
            r'Title\s+(?:Number|#|No\.?):?\s*([A-Z0-9-]+)',
            r'Certificate\s+(?:Number|#|No\.?):?\s*([A-Z0-9-]+)',
            r'Certificate of Title:?\s*([A-Z0-9-]+)',
            r'Title\s+#?\s*([A-Z0-9]{5,})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def _extract_issue_date(self, text: str) -> Optional[str]:
        """Extract title issue/effective date"""
        patterns = [
            r'Issue\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Issued:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date\s+Issued:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Effective\s+Date:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'(\w+\s+\d{1,2},?\s+\d{4})',  # "January 30, 2024"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def _extract_issue_state(self, text: str) -> Optional[str]:
        """Extract issuing state"""
        patterns = [
            r'State:?\s*([A-Z]{2})\b',
            r'Issued\s+by:?\s*([A-Za-z\s]+?)\s+(?:Department|DMV)',
            r'([A-Z]{2})\s+(?:Department|Division)\s+of\s+Motor\s+Vehicles',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                state = match.group(1).strip()
                # Check if it's a valid 2-letter state code
                if len(state) == 2:
                    return state.upper()
                # Map state names to codes (simplified - add more as needed)
                state_map = {
                    'california': 'CA', 'texas': 'TX', 'florida': 'FL',
                    'new york': 'NY', 'pennsylvania': 'PA', 'illinois': 'IL',
                    'ohio': 'OH', 'georgia': 'GA', 'north carolina': 'NC',
                    'michigan': 'MI', 'new jersey': 'NJ', 'virginia': 'VA',
                }
                return state_map.get(state.lower())

        return None

    def _extract_title_brands(self, text: str) -> List[str]:
        """Extract title brands/status indicators"""
        brands = []

        brand_keywords = {
            'salvage': r'salvage',
            'rebuilt': r'rebuilt|reconstructed',
            'flood': r'flood\s+damage|water\s+damage',
            'hail': r'hail\s+damage',
            'fire': r'fire\s+damage',
            'junk': r'junk|scrap',
            'lemon': r'lemon\s+law|manufacturer\s+buyback',
            'theft': r'theft\s+recovery',
            'odometer': r'odometer\s+(?:rollback|discrepancy|not\s+actual)',
            'duplicate': r'duplicate',
            'export': r'export\s+only',
            'kit': r'kit\s+vehicle|assembled',
            'parts_only': r'parts\s+only',
        }

        for brand, pattern in brand_keywords.items():
            if re.search(pattern, text, re.IGNORECASE):
                brands.append(brand)

        # Check for explicit "CLEAN TITLE" or "CLEAR TITLE"
        if re.search(r'clean\s+title|clear\s+title', text, re.IGNORECASE) and not brands:
            return []

        return brands

    def _extract_current_owner(self, text: str) -> Dict[str, Any]:
        """Extract current owner information"""
        owner = {}

        # Owner name
        name_patterns = [
            r'(?:Owner|Registered Owner|Title Holder)[:\s]+([A-Z][^\n]+?)(?:\n|$)',
            r'Name[:\s]+([A-Z][A-Za-z\s.,]+?)(?:\n|Address)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                owner['name'] = match.group(1).strip()
                break

        # Owner address
        address_section = self._extract_owner_address_section(text)
        if address_section:
            owner['address'] = address_section

        return owner

    def _extract_owner_address_section(self, text: str) -> Optional[str]:
        """Extract owner address"""
        patterns = [
            r'(?:Owner|Registered)\s+Address[:\s]+([^\n]+(?:\n[^\n]+){0,2})',
            r'Address[:\s]+([^\n]+(?:\n[^\n]+){0,2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def _extract_previous_owner(self, text: str) -> Dict[str, Any]:
        """Extract previous owner information"""
        previous = {}

        # Previous owner name
        patterns = [
            r'Previous\s+Owner[:\s]+([A-Z][^\n]+?)(?:\n|$)',
            r'Former\s+Owner[:\s]+([A-Z][^\n]+?)(?:\n|$)',
            r'Sold\s+by[:\s]+([A-Z][^\n]+?)(?:\n|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                previous['name'] = match.group(1).strip()
                break

        return previous

    def _extract_lien_holder(self, text: str) -> Dict[str, Any]:
        """Extract lien holder information"""
        lien = {}

        # Lien holder name
        name_patterns = [
            r'Lien\s+Holder[:\s]+([A-Z][^\n]+?)(?:\n|$)',
            r'Lienholder[:\s]+([A-Z][^\n]+?)(?:\n|$)',
            r'Legal\s+Owner[:\s]+([A-Z][^\n]+?)(?:\n|$)',
            r'First\s+Lien\s+Holder[:\s]+([A-Z][^\n]+?)(?:\n|$)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                lien['name'] = match.group(1).strip()
                break

        # Check for "NONE" or "NO LIEN"
        if re.search(r'(?:Lien\s+Holder|Lienholder)[:\s]+(?:NONE|N/A|NO\s+LIEN)', text, re.IGNORECASE):
            return {}

        # Lien date
        lien_date_pattern = r'Lien\s+Date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(lien_date_pattern, text, re.IGNORECASE)
        if match:
            lien['date'] = match.group(1)

        # Lien release date
        release_pattern = r'(?:Lien\s+)?Release\s+Date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        match = re.search(release_pattern, text, re.IGNORECASE)
        if match:
            lien['release_date'] = match.group(1)

        return lien

    def _extract_vehicle_details(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract vehicle details"""
        details = {}

        # VIN (Vehicle Identification Number)
        vin_patterns = [
            r'VIN[:\s]*([A-HJ-NPR-Z0-9]{17})',
            r'Vehicle\s+Identification\s+Number[:\s]*([A-HJ-NPR-Z0-9]{17})',
        ]

        for pattern in vin_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['vin'] = match.group(1).upper()
                break

        # Year
        year_patterns = [
            r'Year[:\s]*(\d{4})',
            r'Model\s+Year[:\s]*(\d{4})',
            r'(\d{4})\s+(?:Make|Model)',
        ]

        for pattern in year_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['year'] = int(match.group(1))
                break

        # Make
        make_patterns = [
            r'Make[:\s]+([A-Z][A-Za-z]+)',
            r'Manufacturer[:\s]+([A-Z][A-Za-z]+)',
        ]

        for pattern in make_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['make'] = match.group(1).strip()
                break

        # Model
        model_patterns = [
            r'Model[:\s]+([A-Z][A-Za-z0-9\s-]+?)(?:\n|Year|Color)',
            r'Model\s+Name[:\s]+([A-Z][^\n]+)',
        ]

        for pattern in model_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['model'] = match.group(1).strip()
                break

        # Color
        color_patterns = [
            r'Color[:\s]+([A-Z][A-Za-z]+)',
            r'Primary\s+Color[:\s]+([A-Z][A-Za-z]+)',
        ]

        for pattern in color_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['color'] = match.group(1).strip()
                break

        # Body style/type
        body_patterns = [
            r'Body\s+(?:Style|Type)[:\s]+([A-Z][^\n]+)',
            r'Vehicle\s+Type[:\s]+([A-Z][^\n]+)',
        ]

        for pattern in body_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['body_style'] = match.group(1).strip()
                break

        return details

    def _extract_property_details(self, text: str, tables: List) -> Dict[str, Any]:
        """Extract property/real estate details"""
        details = {}

        # Property address
        address_patterns = [
            r'Property\s+Address[:\s]+([^\n]+)',
            r'Legal\s+Address[:\s]+([^\n]+)',
            r'Located\s+at[:\s]+([^\n]+)',
        ]

        for pattern in address_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['address'] = match.group(1).strip()
                break

        # Parcel number
        parcel_patterns = [
            r'Parcel\s+(?:Number|#|ID)[:\s]*([A-Z0-9-]+)',
            r'APN[:\s]*([A-Z0-9-]+)',
            r'Assessor.*?Parcel.*?([A-Z0-9-]+)',
        ]

        for pattern in parcel_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['parcel_number'] = match.group(1).strip()
                break

        # Legal description
        legal_pattern = r'Legal\s+Description[:\s]+(.+?)(?:\n\n|$)'
        match = re.search(legal_pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            details['legal_description'] = match.group(1).strip()[:500]

        return details

    def _extract_odometer(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract odometer reading"""
        odometer = {}

        # Odometer reading
        reading_patterns = [
            r'Odometer[:\s]*([\d,]+)\s*(miles?|km|kilometers?)',
            r'Mileage[:\s]*([\d,]+)\s*(miles?|km)',
            r'([\d,]+)\s*(miles?|km).*?odometer',
        ]

        for pattern in reading_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                reading_str = match.group(1).replace(',', '')
                odometer['reading'] = int(reading_str)
                odometer['unit'] = 'miles' if 'mile' in match.group(2).lower() else 'km'
                break

        # Odometer status
        if re.search(r'odometer.*?actual', text, re.IGNORECASE):
            odometer['status'] = 'actual'
        elif re.search(r'odometer.*?not\s+actual|exceeds\s+mechanical\s+limits', text, re.IGNORECASE):
            odometer['status'] = 'not_actual'
        elif re.search(r'odometer.*?exempt', text, re.IGNORECASE):
            odometer['status'] = 'exempt'

        return odometer if odometer else None

    def _extract_transfer_info(self, text: str) -> Dict[str, Any]:
        """Extract transfer information"""
        transfer = {}

        # Transfer date
        transfer_date_patterns = [
            r'Transfer\s+Date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date\s+of\s+Transfer[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Transferred\s+on[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in transfer_date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                transfer['transfer_date'] = match.group(1)
                break

        # Transfer type
        if re.search(r'gift|donation', text, re.IGNORECASE):
            transfer['transfer_type'] = 'gift'
        elif re.search(r'sale|purchase', text, re.IGNORECASE):
            transfer['transfer_type'] = 'sale'
        elif re.search(r'inheritance|estate', text, re.IGNORECASE):
            transfer['transfer_type'] = 'inheritance'

        # Transferor (seller/giver)
        transferor_pattern = r'(?:Transferor|Seller|From)[:\s]+([A-Z][^\n]+)'
        match = re.search(transferor_pattern, text, re.IGNORECASE)
        if match:
            transfer['transferor'] = match.group(1).strip()

        # Transferee (buyer/receiver)
        transferee_pattern = r'(?:Transferee|Buyer|To)[:\s]+([A-Z][^\n]+)'
        match = re.search(transferee_pattern, text, re.IGNORECASE)
        if match:
            transfer['transferee'] = match.group(1).strip()

        return transfer

    def _extract_sale_info(self, text: str) -> Dict[str, Any]:
        """Extract sale/purchase information"""
        sale = {}

        # Sale date
        sale_date_patterns = [
            r'Sale\s+Date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Date\s+of\s+Sale[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'Purchase\s+Date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        ]

        for pattern in sale_date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                sale['sale_date'] = match.group(1)
                break

        # Sale price
        price_patterns = [
            r'Sale\s+Price[:\s]*\$?\s*([\d,]+\.?\d*)',
            r'Purchase\s+Price[:\s]*\$?\s*([\d,]+\.?\d*)',
            r'Selling\s+Price[:\s]*\$?\s*([\d,]+\.?\d*)',
        ]

        for pattern in price_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                sale['sale_price'] = self._parse_financial_value(match.group(1))
                break

        # Odometer at sale
        sale_odo_pattern = r'Odometer.*?sale.*?([\d,]+)'
        match = re.search(sale_odo_pattern, text, re.IGNORECASE)
        if match:
            sale['odometer'] = int(match.group(1).replace(',', ''))

        return sale

    def _validate_title(self, title_number: Optional[str], owner: Dict,
                        asset_details: Dict) -> Dict[str, Any]:
        """Validate title document"""
        validation = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }

        # Check required fields
        if not title_number:
            validation['errors'].append("Title number missing")
            validation['is_valid'] = False

        if not owner.get('name'):
            validation['errors'].append("Owner name missing")
            validation['is_valid'] = False

        # Vehicle-specific validation
        if asset_details.get('vin'):
            vin = asset_details['vin']
            # Basic VIN validation (17 characters, no I, O, Q)
            if len(vin) != 17:
                validation['errors'].append(f"Invalid VIN length: {len(vin)} (should be 17)")
                validation['is_valid'] = False
            elif any(char in vin for char in ['I', 'O', 'Q']):
                validation['errors'].append("VIN contains invalid characters (I, O, or Q)")
                validation['is_valid'] = False

        # Property-specific validation
        if asset_details.get('parcel_number') and not asset_details.get('address'):
            validation['warnings'].append("Parcel number found but no address")

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
extractor = TitleExtractor()
result = extractor.extract(text, tables)

print(f"Title Type: {result['title_type']}")
print(f"Title Number: {result['title_number']}")
print(f"Issue Date: {result['issue_date']}")
print(f"Issuing State: {result['issue_state']}")

print(f"\nTitle Status:")
print(f"  Clean Title: {result['is_clean_title']}")
print(f"  Brands: {result['title_brands']}")
print(f"  Has Lien: {result['has_lien']}")

print(f"\nOwner: {result['owner_name']}")
if result['lien_holder_name']:
    print(f"Lien Holder: {result['lien_holder_name']}")

if result['vin']:
    print(f"\nVehicle Details:")
    print(f"  VIN: {result['vin']}")
    print(f"  Year: {result['year']}")
    print(f"  Make: {result['make']}")
    print(f"  Model: {result['model']}")
    print(f"  Color: {result['color']}")
    if result['odometer_reading']:
        print(f"  Odometer: {result['odometer_reading']:,} {result['odometer_unit']}")

if result['property_address']:
    print(f"\nProperty Details:")
    print(f"  Address: {result['property_address']}")
    print(f"  Parcel #: {result['parcel_number']}")

if result['sale_price']:
    print(f"\nSale Information:")
    print(f"  Sale Date: {result['sale_date']}")
    print(f"  Sale Price: ${result['sale_price']:,.2f}")
"""