# app/parsers/pdf_field_extractor.py

import re
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


def extract_pdf_fields(text: str) -> Dict[str, str]:
    """
    Enhanced field extraction for immigration documents.
    Supports multiple jurisdictions and document formats.
    Handles OCR spacing issues and various text patterns.
    """
    fields = {}
    text_lower = text.lower()

    # ===================================
    # RECEIPT/CASE NUMBER EXTRACTION
    # ===================================

    # Try to find receipt number with various patterns
    receipt_patterns = [
        # CRITICAL: Alphanumeric receipt numbers before form numbers (e.g., 10E0926449157 1129)
        (r'([A-Z0-9]{13,14})\s+\d{3,4}\s*-\s*(?:PETITION|APPLICATION)', 1),
        # Standard formats with labels
        (r'receipt\s+number[:\s]+([A-Z]{3}\d{10})', 1),
        (r'case\s+number[:\s]+([A-Z]{3}\d{10})', 1),
        (r'receipt\s*#[:\s]*([A-Z]{3}\d{10})', 1),
        # Generic 3-letter + 10-digit pattern
        (r'\b([A-Z]{3}\d{10})\b', 1),
        # Pure numeric receipt numbers (13-14 digits)
        (r'receipt\s+number[:\s]+(\d{13,14})', 1),
        # Line starting with 13-14 digits (common OCR pattern)
        (r'^\s*(\d{13,14})\s+\d{3,4}', 1, re.MULTILINE),
        # Specific pattern: digits followed by form number
        (r'\b(\d{13,14})\s+(?:\d{3,4}\s*-)', 1),
    ]

    for pattern_data in receipt_patterns:
        pattern = pattern_data[0]
        group_idx = pattern_data[1]
        flags = pattern_data[2] if len(pattern_data) > 2 else 0

        match = re.search(pattern, text, re.IGNORECASE | flags)
        if match:
            receipt_num = match.group(group_idx).strip()
            # Clean up any non-alphanumeric except hyphens
            receipt_num = re.sub(r'[^A-Z0-9-]', '', receipt_num.upper())
            if len(receipt_num) >= 10:  # Valid receipt numbers are at least 10 chars
                fields["case_number"] = receipt_num
                logger.info(f"✓ Extracted case_number: {fields['case_number']}")
                break

    # Canada IRCC patterns
    if not fields.get("case_number"):
        ircc_patterns = [
            r'application\s+number[:\s]+([A-Z]\d{9})',
            r'client\s+id[:\s]+(\d{4}-\d{4})',
        ]
        for pattern in ircc_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                fields["case_number"] = match.group(1).strip()
                break

    # ===================================
    # FORM TYPE EXTRACTION
    # ===================================

    form_patterns = [
        r'form\s+(I-\d+[A-Z]*)',  # I-797, I-129, I-140
        r'\b(I-\d+[A-Z]*)\b',
        r'\b(\d{3,4})\s*-\s*(?:PETITION|APPLICATION)',  # "1129 - PETITION"
        r'case\s+type.*?(\d{3,4})\s*-',  # Case Type line
    ]

    for pattern in form_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            form_val = match.group(1).strip()
            # If it's just numbers, prepend I-
            if re.match(r'^\d+$', form_val):
                form_val = f"I-{form_val}"
            fields["form_type"] = form_val
            logger.info(f"✓ Extracted form_type: {fields['form_type']}")
            break

    # ===================================
    # NOTICE TYPE
    # ===================================

    notice_patterns = [
        # Stop at "Class:", "Valid", or end of line — avoids bleed into next fields
        r'notice[-\s]?type[:\s]+((?:approval|receipt|denial|transfer|rejection|request\s+for\s+evidence|notice\s+of\s+intent)[^\n]{0,20})',
        r'(receipt\s+notice)',
        r'(approval\s+notice)',
        r'(denial\s+notice)',
        r'(request\s+for\s+evidence)',
        r'(notice\s+of\s+intent\s+to\s+deny)',
    ]

    for pattern in notice_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            notice = match.group(1).strip()
            # Truncate at "Class" or "Valid" keyword bleed
            notice = re.split(r'\s+(?:Class|Valid|From|Petitioner|Beneficiary)\b', notice, flags=re.I)[0].strip()
            fields["notice_type"] = notice
            logger.info(f"✓ Extracted notice_type: {fields['notice_type']}")
            break

    # ===================================
    # DATES
    # ===================================

    # Receipt Date
    receipt_date_patterns = [
        r'receipt\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'received\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'date\s+received[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'submission\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ]

    for pattern in receipt_date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["receipt_date"] = match.group(1).strip()
            logger.info(f"✓ Extracted receipt_date: {fields['receipt_date']}")
            break

    # Priority Date
    priority_patterns = [
        r'priority\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'priority[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ]

    for pattern in priority_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["priority_date"] = match.group(1).strip()
            logger.info(f"✓ Extracted priority_date: {fields['priority_date']}")
            break

    # Notice Date
    notice_date_patterns = [
        r'notice\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ]

    for pattern in notice_date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["notice_date"] = match.group(1).strip()
            logger.info(f"✓ Extracted notice_date: {fields['notice_date']}")
            break

    # ===================================
    # PETITIONER/SPONSOR
    # ===================================

    # Enhanced petitioner extraction
    petitioner_patterns = [
        # Standard labeled patterns
        (r'petitioner[:\s\n]+([A-Z][A-Z\s&.,\'-]+)', True),
        (r'sponsor[:\s\n]+([A-Z][A-Z\s&.,\'-]+)', True),
        (r'employer[:\s\n]+([A-Z][A-Z\s&.,\'-]+)', True),
        # Company name after dates (common in I-797 layout)
        (r'(?:priority\s+date|received\s+date)\s+petitioner[:\s\n]+([A-Z][A-Z\s&.,\'-]+)', True),
        # Standalone all-caps company name (2+ words, before an address)
        (r'\n([A-Z][A-Z\s&.,\'-]{10,})\n', False),
    ]

    for pattern, is_labeled in petitioner_patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE if is_labeled else re.MULTILINE)
        if match:
            petitioner = match.group(1).strip()
            # Clean up - take only first line if multiline
            petitioner = petitioner.split('\n')[0].strip()
            # Remove trailing punctuation or common words
            petitioner = re.sub(r'\s+(Notice|Page|Beneficiary|Date|clo|c/o).*', '', petitioner, flags=re.IGNORECASE)
            # Must be at least 3 chars and not start with a number
            if len(petitioner) > 3 and not re.match(r'^\d', petitioner):
                # Filter out common non-company text
                if not any(x in petitioner.upper() for x in
                           ['NOTICE OF ACTION', 'DEPARTMENT', 'CITIZENSHIP', 'IMMIGRATION']):
                    fields["petitioner"] = petitioner
                    logger.info(f"✓ Extracted petitioner: {fields['petitioner']}")
                    break

    # ===================================
    # BENEFICIARY/APPLICANT
    # ===================================

    def _is_name(s: str) -> bool:
        """Return True if s looks like a person name rather than an ID or junk."""
        s = s.strip()
        if not s or len(s) < 3:
            return False
        # Reject redaction tokens like [A-NUMBER REDACTED], [SSN REDACTED] etc.
        if s.startswith('[') or 'REDACTED' in s.upper():
            return False
        # Reject lines that start with digits or look like A-Numbers / receipt numbers
        if re.match(r'^[A-Z]?\d', s):
            return False
        # Reject all-digit or mostly-digit strings
        if re.search(r'\d{5,}', s):
            return False
        # Reject obvious non-name keywords
        bad = ['NOTICE OF ACTION', 'DEPARTMENT', 'CITIZENSHIP', 'IMMIGRATION',
               'PETITION', 'SERVICE CENTER', 'VALID', 'APPROVED', 'RECEIPT']
        if any(b in s.upper() for b in bad):
            return False
        # Must contain at least one alpha sequence of 2+ chars
        return bool(re.search(r'[A-Za-z]{2,}', s))

    # USCIS table layout: "Beneficiary  A213 347 644\nBOMMA, VIJAY KUMAR"
    # The A-Number sits between label and name — grab lines after the label,
    # strip any A-Number / identifier lines, and take the first name-looking line.
    beneficiary_label_match = re.search(
        r'(?:beneficiary|applicant)[:\s]*',
        text, re.IGNORECASE
    )
    if beneficiary_label_match:
        # Get everything from the end of the label onwards, split into lines
        after_label = text[beneficiary_label_match.end():]
        for candidate_line in after_label.split('\n')[:6]:
            candidate = candidate_line.strip()
            # Strip A-Number / SEVIS-ID that may share the line with the label
            candidate = re.sub(r'\bA[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{3}\b', '', candidate, flags=re.I).strip()
            candidate = re.sub(r'\bN\d{9,10}\b', '', candidate).strip()
            if _is_name(candidate):
                fields["beneficiary"] = candidate
                logger.info(f"✓ Extracted beneficiary (multi-line): {fields['beneficiary']}")
                break

    if not fields.get("beneficiary"):
        # Fallback patterns for single-line formats
        beneficiary_patterns = [
            (r'beneficiary[:\s]+([A-Z][A-Z\s,.\'-]{3,})', 1),
            (r'applicant[:\s]+([A-Z][A-Z\s,.\'-]{3,})', 1),
            # "NAME BOMMA, VIJAY KUMAR" — I-94 section label
            (r'(?<!\w)NAME\s+([A-Z][A-Z\s,.\'-]{3,})', 1),
            (r'Name\s+DOB.*?\n([A-Z]+,\s+[A-Z]+)', 1),
            (r'name[:\s]+([A-Z]+,\s+[A-Z]+)', 1),
        ]
        for pattern, group_idx in beneficiary_patterns:
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            if match:
                candidate = match.group(group_idx).strip().split('\n')[0].strip()
                candidate = re.sub(r'\s+(Notice|Page|Petitioner|Date).*', '', candidate, flags=re.I)
                if _is_name(candidate):
                    fields["beneficiary"] = candidate
                    logger.info(f"✓ Extracted beneficiary (fallback): {fields['beneficiary']}")
                    break

    # ===================================
    # SERVICE CENTER / AUTHORITY
    # ===================================

    service_center_patterns = [
        r'((?:California|Texas|Vermont|Nebraska|Potomac)\s+Service\s+Center)',
        r'service\s+center[:\s]+([^\n]+)',
        r'office[:\s]+([A-Z][A-Z\s]+(?:Service Center|Office))',
    ]

    for pattern in service_center_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["service_center"] = match.group(1).strip()
            logger.info(f"✓ Extracted service_center: {fields['service_center']}")
            break

    # ===================================
    # VISA CLASS
    # ===================================

    visa_class_patterns = [
        r'class\s+requested[:\s]+([A-Z0-9-]+)',
        r'visa\s+class[:\s]+([A-Z0-9-]+)',
        # "Class: H1B" / "CLASS H1B" — labeled field only, not body text
        r'(?:^|\n)\s*class[:\s]+([A-Z0-9-]{2,6})\b',
        # Known visa categories as explicit match (most reliable for body-text fallback)
        r'\b(H-?1B|H-?2A|H-?2B|H-?3|L-?1A|L-?1B|O-?1A?|O-?2|E-?1|E-?2|E-?3|TN-?1|TN-?2|J-?1|F-?1|B-?1|B-?2|EB-?[1-5])\b',
    ]

    for pattern in visa_class_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            visa_class = match.group(1).strip().upper()
            # Fix common OCR misreads: HIB -> H-1B, L1A -> L-1A etc.
            ocr_fixes = {'HIB': 'H-1B', 'H1B': 'H-1B', 'L1A': 'L-1A', 'L1B': 'L-1B',
                         'O1': 'O-1', 'E2': 'E-2', 'E3': 'E-3', 'TN1': 'TN-1', 'TN2': 'TN-2'}
            visa_class = ocr_fixes.get(visa_class, visa_class)
            visa_class = re.sub(r'([A-Za-z])(\d)', r'\1-\2', visa_class)
            visa_class = re.sub(r'-+', '-', visa_class)
            # Sanity check — must be a plausible visa category (2-6 chars)
            if 2 <= len(visa_class) <= 6:
                fields["visa_class"] = visa_class
                logger.info(f"✓ Extracted visa_class: {fields['visa_class']}")
                break

    # ===================================
    # AMOUNT RECEIVED
    # ===================================

    amount_patterns = [
        r'amount\s+received[:\s]+\$?([\d,]+\.?\d*)',
        r'fee[:\s]+\$?([\d,]+\.?\d*)',
    ]

    for pattern in amount_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = match.group(1).strip()
            fields["amount_received"] = f"${amount}"
            logger.info(f"✓ Extracted amount_received: {fields['amount_received']}")
            break

    # ===================================
    # ETA CASE NUMBER (Labor Certification)
    # ===================================

    eta_patterns = [
        r'eta\s+case\s+number[:\s]+([A-Z]-\d+-\d+)',
        r'labor\s+certification[:\s]+([A-Z]-\d+-\d+)',
    ]

    for pattern in eta_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["eta_case_number"] = match.group(1).strip()
            logger.info(f"✓ Extracted eta_case_number: {fields['eta_case_number']}")
            break

    # ===================================
    # VALIDITY PERIOD (for approvals)
    # ===================================

    # Date token: MM/DD/YYYY, MM-DD-YYYY, or "Jan 05, 2024" style
    _DATE = (
        r'(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
        r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})'
    )
    validity_patterns = [
        # "Valid from  12/08/2023 to 11/28/2026"  (extra spaces, no colon)
        rf'valid\s+from\s+({_DATE})\s+to\s+({_DATE})',
        # "Valid from: 12/08/2023 to 11/28/2026"
        rf'valid\s+from[:\s]+({_DATE})\s+to\s+({_DATE})',
        # "Validity Period: 02/06/2024 - 04/08/2027"
        rf'validity\s+period[:\s]+({_DATE})\s*[-–]\s*({_DATE})',
        # "Employment Authorized: Feb 06, 2024 to Apr 08, 2027"
        rf'(?:employment\s+authorized|authorized\s+to\s+work)[:\s]+({_DATE})\s+to\s+({_DATE})',
        # "Status valid: 02/06/2024 through 04/08/2027"
        rf'(?:status\s+valid|authorized)[:\s]+({_DATE})\s+(?:to|through|until)\s+({_DATE})',
        # "H-1B status from Feb 06, 2024 to Apr 08, 2027"
        rf'(?:h-?1b|l-?1[ab]|o-?1|tn|e-?[23])\s+status\s+from\s+({_DATE})\s+to\s+({_DATE})',
    ]

    for pattern in validity_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["validity_start"] = match.group(1).strip()
            fields["validity_end"] = match.group(2).strip()
            logger.info(f"✓ Extracted validity period: {fields['validity_start']} to {fields['validity_end']}")
            break

    # Also extract standalone expiration / valid-to date if only one date found
    if not fields.get("validity_end"):
        expiry_patterns = [
            rf'(?:expir(?:es?|ation)|valid\s+(?:through|until|to|thru))[:\s]+({_DATE})',
            rf'(?:authorized\s+until|authorized\s+through|status\s+expires)[:\s]+({_DATE})',
        ]
        for pattern in expiry_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                fields["validity_end"] = match.group(1).strip()
                logger.info(f"✓ Extracted validity_end (standalone): {fields['validity_end']}")
                break

    logger.info(f"📑 Total fields extracted: {len(fields)}")
    return fields