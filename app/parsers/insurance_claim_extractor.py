# app/parsers/insurance_claim_extractor.py

import re
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


def extract_insurance_claim_fields(text: str) -> Dict[str, str]:
    """
    Enhanced field extraction for insurance claims.
    Supports all types of insurance claims from all providers worldwide.
    Handles OCR spacing issues and various text patterns.
    """
    fields = {}
    text_lower = text.lower()

    # ===================================
    # CLAIM NUMBER EXTRACTION
    # ===================================

    claim_number_patterns = [
        # Standard labeled patterns
        (r'claim\s+(?:number|#|no\.?)[:\s]+([A-Z0-9-]+)', 1),
        (r'claim\s+id[:\s]+([A-Z0-9-]+)', 1),
        (r'reference\s+(?:number|#)[:\s]+([A-Z0-9-]+)', 1),
        # Health insurance specific
        (r'claim\s+control\s+number[:\s]+([A-Z0-9-]+)', 1),
        (r'icn[:\s]+([A-Z0-9-]+)', 1),  # Internal Control Number
        # Generic patterns
        (r'\b([A-Z]{2,3}\d{8,})\b', 1),  # 2-3 letters + 8+ digits
        (r'\b(\d{10,15})\b', 1),  # 10-15 digit numbers
    ]

    for pattern, group_idx in claim_number_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            claim_num = match.group(group_idx).strip()
            if len(claim_num) >= 6:  # Valid claim numbers are at least 6 chars
                fields["claim_number"] = claim_num
                logger.info(f"✓ Extracted claim_number: {fields['claim_number']}")
                break

    # ===================================
    # CLAIM STATUS
    # ===================================

    status_patterns = [
        (r'claim\s+status[:\s]+([^\n]+)', 1),
        (r'status[:\s]+(approved|denied|pending|paid|processed|rejected|under review)', 1),
        (r'\b(approved|denied|pending|paid|processed|rejected)\b', 1),
    ]

    for pattern, group_idx in status_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            status = match.group(group_idx).strip()
            # Clean up status
            status = re.sub(r'\s+', ' ', status)
            if len(status) > 3 and len(status) < 50:
                fields["claim_status"] = status.title()
                logger.info(f"✓ Extracted claim_status: {fields['claim_status']}")
                break

    # ===================================
    # DATES
    # ===================================

    # Claim/Service Date
    date_patterns = [
        (r'claim\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', "claim_date"),
        (r'date\s+of\s+service[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', "service_date"),
        (r'service\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', "service_date"),
        (r'date\s+of\s+loss[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', "claim_date"),
        (r'incident\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', "claim_date"),
        (r'date\s+of\s+accident[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', "claim_date"),
    ]

    for pattern, field_name in date_patterns:
        if field_name not in fields:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                fields[field_name] = match.group(1).strip()
                logger.info(f"✓ Extracted {field_name}: {fields[field_name]}")

    # Process Date
    process_date_patterns = [
        r'processed\s+date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'date\s+processed[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ]

    for pattern in process_date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["process_date"] = match.group(1).strip()
            logger.info(f"✓ Extracted process_date: {fields['process_date']}")
            break

    # ===================================
    # CLAIMANT/PATIENT/INSURED
    # ===================================

    claimant_patterns = [
        (r'patient\s+name[:\s]+([A-Z][A-Za-z\s,.\'-]+)', 1),
        (r'claimant[:\s]+([A-Z][A-Za-z\s,.\'-]+)', 1),
        (r'insured[:\s]+([A-Z][A-Za-z\s,.\'-]+)', 1),
        (r'member\s+name[:\s]+([A-Z][A-Za-z\s,.\'-]+)', 1),
        (r'policyholder[:\s]+([A-Z][A-Za-z\s,.\'-]+)', 1),
        # Name in LASTNAME, FIRSTNAME format
        (r'name[:\s]+([A-Z]+,\s+[A-Z]+)', 1),
    ]

    for pattern, group_idx in claimant_patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            name = match.group(group_idx).strip()
            name = name.split('\n')[0].strip()
            # Remove common non-name words
            name = re.sub(r'\s+(Date|Number|ID|Address).*', '', name, flags=re.IGNORECASE)
            if len(name) > 3 and not re.match(r'^\d', name):
                # Store in both patient and claimant fields
                if "patient" in pattern.lower():
                    fields["patient"] = name
                else:
                    fields["claimant"] = name
                logger.info(f"✓ Extracted claimant/patient: {name}")
                break

    # ===================================
    # PROVIDER/INSURANCE COMPANY
    # ===================================

    provider_patterns = [
        (r'insurance\s+company[:\s]+([A-Z][A-Za-z\s&.,-]+)', 1),
        (r'carrier[:\s]+([A-Z][A-Za-z\s&.,-]+)', 1),
        (r'insurer[:\s]+([A-Z][A-Za-z\s&.,-]+)', 1),
        (r'plan\s+name[:\s]+([A-Z][A-Za-z\s&.,-]+)', 1),
    ]

    for pattern, group_idx in provider_patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            provider = match.group(group_idx).strip()
            provider = provider.split('\n')[0].strip()
            provider = re.sub(r'\s+(Address|Phone|Contact).*', '', provider, flags=re.IGNORECASE)
            if len(provider) > 3:
                fields["provider"] = provider
                logger.info(f"✓ Extracted provider: {fields['provider']}")
                break

    # ===================================
    # FINANCIAL AMOUNTS
    # ===================================

    amount_patterns = [
        # Claim amount
        (r'claim\s+amount[:\s]+\$?\s*([\d,]+\.?\d*)', "claim_amount"),
        (r'total\s+charge(?:s|d)?[:\s]+\$?\s*([\d,]+\.?\d*)', "total_charges"),
        (r'billed\s+amount[:\s]+\$?\s*([\d,]+\.?\d*)', "claim_amount"),

        # Approved/Allowed amount
        (r'approved\s+amount[:\s]+\$?\s*([\d,]+\.?\d*)', "approved_amount"),
        (r'allowed\s+amount[:\s]+\$?\s*([\d,]+\.?\d*)', "allowed_amount"),
        (r'covered\s+amount[:\s]+\$?\s*([\d,]+\.?\d*)', "approved_amount"),

        # Insurance paid
        (r'insurance\s+paid[:\s]+\$?\s*([\d,]+\.?\d*)', "insurance_paid"),
        (r'paid\s+by\s+insurance[:\s]+\$?\s*([\d,]+\.?\d*)', "insurance_paid"),
        (r'plan\s+paid[:\s]+\$?\s*([\d,]+\.?\d*)', "insurance_paid"),

        # Patient responsibility
        (r'patient\s+responsibility[:\s]+\$?\s*([\d,]+\.?\d*)', "patient_responsibility"),
        (r'you\s+owe[:\s]+\$?\s*([\d,]+\.?\d*)', "you_owe"),
        (r'member\s+responsibility[:\s]+\$?\s*([\d,]+\.?\d*)', "patient_responsibility"),
        (r'out[-\s]of[-\s]pocket[:\s]+\$?\s*([\d,]+\.?\d*)', "patient_responsibility"),

        # Deductible
        (r'deductible[:\s]+\$?\s*([\d,]+\.?\d*)', "deductible"),

        # Copay
        (r'co[-\s]?pay[:\s]+\$?\s*([\d,]+\.?\d*)', "copay"),

        # Coinsurance
        (r'co[-\s]?insurance[:\s]+\$?\s*([\d,]+\.?\d*)', "coinsurance"),
    ]

    for pattern, field_name in amount_patterns:
        if field_name not in fields:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount = match.group(1).strip()
                fields[field_name] = f"${amount}"
                logger.info(f"✓ Extracted {field_name}: {fields[field_name]}")

    # ===================================
    # POLICY NUMBER
    # ===================================

    policy_patterns = [
        r'policy\s+(?:number|#|no\.?)[:\s]+([A-Z0-9-]+)',
        r'policy\s+id[:\s]+([A-Z0-9-]+)',
        r'group\s+(?:number|#)[:\s]+([A-Z0-9-]+)',
    ]

    for pattern in policy_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["policy_number"] = match.group(1).strip()
            logger.info(f"✓ Extracted policy_number: {fields['policy_number']}")
            break

    # ===================================
    # MEMBER ID
    # ===================================

    member_id_patterns = [
        r'member\s+(?:id|number)[:\s]+([A-Z0-9-]+)',
        r'subscriber\s+(?:id|number)[:\s]+([A-Z0-9-]+)',
        r'patient\s+(?:id|number)[:\s]+([A-Z0-9-]+)',
    ]

    for pattern in member_id_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["member_id"] = match.group(1).strip()
            logger.info(f"✓ Extracted member_id: {fields['member_id']}")
            break

    # ===================================
    # DIAGNOSIS (for health claims)
    # ===================================

    diagnosis_patterns = [
        r'diagnosis[:\s]+([A-Z][A-Za-z\s,.-]+)',
        r'icd[-\s]?10[:\s]+([A-Z0-9.-]+)',
    ]

    for pattern in diagnosis_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            diagnosis = match.group(1).strip().split('\n')[0]
            if len(diagnosis) > 3:
                fields["diagnosis"] = diagnosis
                logger.info(f"✓ Extracted diagnosis: {fields['diagnosis']}")
                break

    # ===================================
    # PROCEDURE (for health claims)
    # ===================================

    procedure_patterns = [
        r'procedure[:\s]+([A-Z][A-Za-z\s,.-]+)',
        r'cpt\s+code[:\s]+([0-9]{5})',
    ]

    for pattern in procedure_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            procedure = match.group(1).strip().split('\n')[0]
            if len(procedure) > 2:
                fields["procedure"] = procedure
                logger.info(f"✓ Extracted procedure: {fields['procedure']}")
                break

    # ===================================
    # PROVIDER NAME (medical provider, not insurance)
    # ===================================

    medical_provider_patterns = [
        r'provider\s+name[:\s]+([A-Z][A-Za-z\s,.\'-]+)',
        r'rendering\s+provider[:\s]+([A-Z][A-Za-z\s,.\'-]+)',
        r'physician[:\s]+([A-Z][A-Za-z\s,.\'-]+)',
        r'doctor[:\s]+([A-Z][A-Za-z\s,.\'-]+)',
    ]

    for pattern in medical_provider_patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            provider_name = match.group(1).strip().split('\n')[0]
            provider_name = re.sub(r'\s+(Address|Phone|NPI).*', '', provider_name, flags=re.IGNORECASE)
            if len(provider_name) > 3:
                fields["medical_provider"] = provider_name
                logger.info(f"✓ Extracted medical_provider: {fields['medical_provider']}")
                break

    # ===================================
    # VIN (for auto claims)
    # ===================================

    vin_pattern = r'\b([A-HJ-NPR-Z0-9]{17})\b'
    match = re.search(vin_pattern, text)
    if match:
        fields["vin"] = match.group(1)
        logger.info(f"✓ Extracted vin: {fields['vin']}")

    # ===================================
    # LICENSE PLATE (for auto claims)
    # ===================================

    license_patterns = [
        r'license\s+plate[:\s]+([A-Z0-9-]+)',
        r'plate\s+(?:number|#)[:\s]+([A-Z0-9-]+)',
    ]

    for pattern in license_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields["license_plate"] = match.group(1).strip()
            logger.info(f"✓ Extracted license_plate: {fields['license_plate']}")
            break

    # ===================================
    # PROPERTY ADDRESS (for property claims)
    # ===================================

    property_patterns = [
        r'property\s+address[:\s]+([0-9]+\s+[A-Za-z\s,.-]+)',
        r'loss\s+location[:\s]+([0-9]+\s+[A-Za-z\s,.-]+)',
    ]

    for pattern in property_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            address = match.group(1).strip().split('\n')[0]
            if len(address) > 10:
                fields["property_address"] = address
                logger.info(f"✓ Extracted property_address: {fields['property_address']}")
                break

    # ===================================
    # DENIAL REASON (if claim is denied)
    # ===================================

    denial_patterns = [
        r'denial\s+reason[:\s]+([^\n]+)',
        r'reason\s+for\s+denial[:\s]+([^\n]+)',
        r'not\s+covered\s+because[:\s]+([^\n]+)',
    ]

    for pattern in denial_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            reason = match.group(1).strip()
            if len(reason) > 5 and len(reason) < 200:
                fields["denial_reason"] = reason
                logger.info(f"✓ Extracted denial_reason: {fields['denial_reason']}")
                break

    logger.info(f"📑 Total fields extracted: {len(fields)}")
    return fields