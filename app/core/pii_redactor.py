"""
Centralized sensitive data redaction for all stored text and API responses.
Covers PII, PHI, credentials, immigration IDs, financial identifiers, and infra secrets.
Applied before writing AI-generated content to DB and before returning raw text via API.
"""
import re
from typing import Any

# ── Pattern registry ──────────────────────────────────────────────────────────
# Each entry: (compiled regex, replacement string)
_RULES: list[tuple[re.Pattern, str]] = [

    # ── PII ───────────────────────────────────────────────────────────────────
    # Alien / A-Number  (A213 347 644 / A-213347644)
    (re.compile(r'\bA[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{3}\b', re.I), '[A-NUMBER REDACTED]'),
    # SSN  (123-45-6789 / 123 45 6789)
    (re.compile(r'\b\d{3}[-\s]\d{2}[-\s]\d{4}\b'), '[SSN REDACTED]'),
    # EIN  (12-3456789)
    (re.compile(r'\b\d{2}-\d{7}\b'), '[EIN REDACTED]'),
    # US Passport  (A12345678)
    (re.compile(r'(?i)(passport\s*(?:#|no\.?|number)?\s*[:\s]*)[A-Z]{1,2}\d{7,9}'), r'\1[PASSPORT# REDACTED]'),
    # Date of birth in context
    (re.compile(r'(?i)(date\s+of\s+birth|dob|born\s+on|birth\s+date)\s*[:\s]+\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}'), r'\1: [DOB REDACTED]'),
    # Phone numbers  (+1-800-555-1234 / (800) 555-1234 / 800.555.1234)
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b'), '[PHONE REDACTED]'),
    # Driver's license in context
    (re.compile(r'(?i)(driver.?s?\s+license\s*(?:#|no\.?|number)?\s*[:\s]*)[A-Z]{1,2}\d{6,9}'), r'\1[DL# REDACTED]'),

    # ── Financial ─────────────────────────────────────────────────────────────
    # Credit / Debit card  (16-digit with separators)
    (re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'), '[CC# REDACTED]'),
    # CVV / CVC  (3-4 digit in context)
    (re.compile(r'(?i)(cvv|cvc|cvv2|security\s+code)\s*[:\s]*\d{3,4}\b'), r'\1: [CVV REDACTED]'),
    # Bank account numbers in context
    (re.compile(r'(?i)(account\s*(?:number|#|no\.?)\s*[:\s]*)\d{8,17}'), r'\1[ACCOUNT# REDACTED]'),
    # Routing numbers in context
    (re.compile(r'(?i)(routing\s*(?:number|#|no\.?)\s*[:\s]*)\d{9}'), r'\1[ROUTING# REDACTED]'),
    # IBAN  (GB29NWBK60161331926819 or GB29 NWBK 6016 1331 9268 19)
    (re.compile(r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}\s?[A-Z0-9]{2,4}\b'), '[IBAN REDACTED]'),
    # SWIFT / BIC — require keyword context to avoid false positives
    (re.compile(r'(?i)(swift|bic)\s*(?:code)?\s*[:\s]+[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?'), r'\1: [SWIFT REDACTED]'),
    # Credit score in context
    (re.compile(r'(?i)(credit\s+score|fico\s+score|vantagescore)\s*[:\s]*\d{3}\b'), r'\1: [CREDIT SCORE REDACTED]'),

    # ── Immigration extended ───────────────────────────────────────────────────
    # I-94 Admission Number  (11 digits)
    (re.compile(r'(?i)(i-?94\s*(?:admission|arrival|#|no\.?|number)?\s*[:\s]*)\d{11}'), r'\1[I-94# REDACTED]'),
    # SEVIS ID  (N + 9 or 10 digits)
    (re.compile(r'\bN\d{9,10}\b'), '[SEVIS-ID REDACTED]'),
    # EAD / Employment Authorization Card number
    (re.compile(r'(?i)(ead\s*(?:card)?\s*(?:#|no\.?|number)?\s*[:\s]*)[A-Z]{3}\d{10}'), r'\1[EAD# REDACTED]'),
    # USCIS Online Account Number  (13 digits)
    (re.compile(r'(?i)(uscis\s*(?:online\s+account|account)\s*(?:#|no\.?|number)?\s*[:\s]*)\d{13}'), r'\1[USCIS-ACCOUNT REDACTED]'),
    # Visa foil number  (8+ alphanumeric in visa context)
    (re.compile(r'(?i)(visa\s*(?:foil\s*)?(?:#|no\.?|number)\s*[:\s]*)[A-Z0-9]{8,14}'), r'\1[VISA# REDACTED]'),
    # USCIS receipt numbers  (EAC/WAC/LIN/SRC/NBC/MSC + digits)
    (re.compile(r'\b(?:EAC|WAC|LIN|SRC|NBC|MSC|IOE)[-\s]?\d{2}[-\s]?\d{3}[-\s]?\d{5}\b', re.I), '[RECEIPT# REDACTED]'),

    # ── Medical / PHI ─────────────────────────────────────────────────────────
    # NPI (National Provider Identifier)  — 10 digits in context
    (re.compile(r'(?i)(npi\s*(?:#|no\.?|number)?\s*[:\s]*)\d{10}\b'), r'\1[NPI REDACTED]'),
    # DEA number  (letter + letter + 7 digits)
    (re.compile(r'\b[A-Z]{2}\d{7}\b'), '[DEA# REDACTED]'),
    # Medical record number in context (matches "Medical Record #", "MRN:", "mrn:")
    (re.compile(r'(?i)((?:medical\s+record\s*(?:#|no\.?|number|mrn)?|mrn)\s*[:\s]*)[A-Z0-9]{6,12}'), r'\1[MRN REDACTED]'),
    # Medicare ID  (1EG4-TE5-MK72 style)
    (re.compile(r'\b[1-9][A-Z]{1}\d{2}-[A-Z]{2}\d-[A-Z]{2}\d{2}\b'), '[MEDICARE# REDACTED]'),
    # Medicaid in context
    (re.compile(r'(?i)(medicaid\s*(?:id|#|no\.?|number)?\s*[:\s]*)\d{8,12}'), r'\1[MEDICAID# REDACTED]'),

    # ── Legal ─────────────────────────────────────────────────────────────────
    # Court docket / case number  (e.g. 1:23-cv-01234)
    (re.compile(r'\b\d{1,2}:\d{2}-(?:cv|cr|mc|bk|ap)-\d{4,6}\b', re.I), '[CASE# REDACTED]'),
    # Bar number in context
    (re.compile(r'(?i)(bar\s*(?:#|no\.?|number)\s*[:\s]*)\d{5,8}'), r'\1[BAR# REDACTED]'),

    # ── Credentials / Secrets ─────────────────────────────────────────────────
    # AWS Access Key ID  (AKIA...)
    (re.compile(r'\b(AKIA|ASIA|AIPA|ANPA|ANVA|AROA)[A-Z0-9]{16}\b'), '[AWS-KEY REDACTED]'),
    # AWS Secret (40-char base64-ish in context)
    (re.compile(r'(?i)(aws.{0,20}secret.{0,20}[:\s]+)[A-Za-z0-9/+=]{40}'), r'\1[AWS-SECRET REDACTED]'),
    # Generic API key in context
    (re.compile(r'(?i)(api[_\-\s]?key\s*[=:\s]+)[A-Za-z0-9_\-]{20,64}'), r'\1[API-KEY REDACTED]'),
    # Bearer / Authorization tokens
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9_\-\.=]{20,}'), r'\1[TOKEN REDACTED]'),
    # Private key block
    (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', re.DOTALL), '[PRIVATE KEY REDACTED]'),
    # Password in context
    (re.compile(r'(?i)(password|passwd|pwd)\s*[=:\s]+\S{6,}'), r'\1: [PASSWORD REDACTED]'),
    # Generic secret / token in context
    (re.compile(r'(?i)(secret|token|auth.?key)\s*[=:\s]+[A-Za-z0-9_\-\.]{16,}'), r'\1: [SECRET REDACTED]'),
    # Connection strings with embedded credentials
    (re.compile(r'(?i)(postgres|mysql|mongodb|redis|amqp)://[^:]+:[^@]+@'), r'\1://[CREDENTIALS REDACTED]@'),

    # ── Infrastructure ────────────────────────────────────────────────────────
    # Private IPv4 ranges (RFC1918)
    (re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b'), '[PRIVATE-IP REDACTED]'),
]


def redact_text(text: str) -> str:
    """Apply all redaction rules to a plain string."""
    if not text:
        return text
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive data from strings inside dicts/lists."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_metadata(metadata: dict | None) -> dict | None:
    """Redact all sensitive data from a doc_metadata blob."""
    if not metadata:
        return metadata
    return redact_value(metadata)
