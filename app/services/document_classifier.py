import re
import logging
from typing import Dict, List, Tuple
from pathlib import Path

from app.services.pattern_loader import get_pattern_loader

logger = logging.getLogger(__name__)


class DocumentClassifier:
    """Universal document classifier using YAML-configured patterns"""

    def __init__(self, file_path: str, text: str):
        self.file_path = file_path
        self.filename = Path(file_path).name
        self.text = text
        self.text_lower = text.lower()
        self.loader = get_pattern_loader()

    def classify(self) -> Tuple[str, float, Dict]:
        """
        Classify document using configurable patterns

        Returns:
            (doc_type, confidence, details)
        """
        scores = {}
        details = {}

        # Score all document types
        for doc_type in self.loader.get_all_types():
            score, match_details = self._score_document_type(doc_type)
            scores[doc_type] = score
            details[doc_type] = match_details

        # Get best match
        if not scores or max(scores.values()) == 0:
            return "unknown", 0.0, {}

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]
        best_details = details[best_type]

        # Apply priority boost
        pattern = self.loader.get_pattern(best_type)
        priority = pattern.get('priority', 50)

        # Calculate final confidence with priority
        confidence = min(1.0, best_score * (priority / 100))

        logger.info(f"🎯 Best match: {best_type} (score: {best_score:.2f}, confidence: {confidence:.2%})")

        return best_type, confidence, best_details

    def _score_document_type(self, doc_type: str) -> Tuple[float, Dict]:
        """Score how well the document matches a specific type"""
        pattern = self.loader.get_pattern(doc_type)

        if not pattern:
            return 0.0, {}

        total_score = 0.0
        max_possible = 0.0
        matched_keywords = []
        matched_patterns = []
        matched_filename = False

        # 1. Filename matching (HIGHEST PRIORITY - 40% weight)
        filename_patterns = pattern.get('filename_patterns', [])
        if filename_patterns:
            max_possible += 40
            for fp in filename_patterns:
                if re.search(fp, self.filename, re.IGNORECASE):
                    total_score += 40
                    matched_filename = True
                    logger.debug(f"  ✅ Filename match: {fp}")
                    break

        # 2. Regex pattern matching (30% weight)
        regex_patterns = pattern.get('regex_patterns', [])
        if regex_patterns:
            max_possible += 30
            pattern_count = 0
            for p in regex_patterns:
                if re.search(p, self.text_lower, re.IGNORECASE):
                    pattern_count += 1
                    matched_patterns.append(p)

            if pattern_count > 0:
                pattern_score = (pattern_count / len(regex_patterns)) * 30
                total_score += pattern_score

        # 3. Keyword matching (20% weight)
        keywords = pattern.get('keywords', [])
        if keywords:
            max_possible += 20
            keyword_count = 0
            for kw in keywords:
                # ✅ FIX: Convert keyword to string first
                kw_str = str(kw).lower()
                if kw_str in self.text_lower:
                    keyword_count += 1
                    matched_keywords.append(kw_str)

            if keyword_count > 0:
                keyword_score = (keyword_count / len(keywords)) * 20
                total_score += keyword_score

        # 4. File extension check (10% bonus)
        file_extensions = pattern.get('file_extensions', [])
        if file_extensions:
            max_possible += 10
            if any(self.filename.lower().endswith(ext) for ext in file_extensions):
                total_score += 10

        # Normalize score (0-1)
        if max_possible > 0:
            normalized_score = total_score / max_possible
        else:
            normalized_score = 0.0

        details = {
            'matched_keywords': matched_keywords,
            'matched_patterns': matched_patterns,
            'matched_filename': matched_filename,
            'raw_score': total_score,
            'max_possible': max_possible,
            'normalized_score': normalized_score
        }

        return normalized_score, details

# import re
# from typing import Dict, Tuple
# import pdfplumber
# from pathlib import Path
#
#
# class DocumentClassifier:
#     """
#     Intelligently classify document types
#     """
#
#     # Document type signatures
#     DOCUMENT_SIGNATURES = {
#         'bank_statement': {
#             'keywords': [
#                 'account summary', 'beginning balance', 'ending balance',
#                 'deposits', 'withdrawals', 'bank statement', 'checking account',
#                 'savings account', 'account number', 'statement period',
#                 'debit', 'credit', 'available balance', 'transaction history'
#             ],
#             'required_patterns': [
#                 r'account\s+number',
#                 r'(beginning|ending|current)\s+balance',
#                 r'(deposit|withdrawal|transaction)'
#             ],
#             'confidence_threshold': 2  # Need at least 2 patterns
#         },
#         'invoice': {
#             'keywords': [
#                 'invoice', 'invoice number', 'invoice date', 'bill to',
#                 'subtotal', 'tax', 'total due', 'payment terms', 'due date',
#                 'quantity', 'unit price', 'amount'
#             ],
#             'required_patterns': [
#                 r'invoice\s+(number|#|no)',
#                 r'(total|amount)\s+due',
#                 r'bill\s+to'
#             ],
#             'confidence_threshold': 2
#         },
#         'receipt': {
#             'keywords': [
#                 'receipt', 'total', 'subtotal', 'tax', 'payment method',
#                 'card', 'cash', 'thank you', 'store'
#             ],
#             'required_patterns': [
#                 r'(total|subtotal)',
#                 r'(cash|card|credit)',
#             ],
#             'confidence_threshold': 2
#         },
#         'contract': {
#             'keywords': [
#                 'agreement', 'contract', 'party', 'whereas', 'hereby',
#                 'terms and conditions', 'effective date', 'signature',
#                 'witnesseth', 'covenant'
#             ],
#             'required_patterns': [
#                 r'(this\s+agreement|contract)',
#                 r'(party|parties)',
#                 r'(signature|executed)'
#             ],
#             'confidence_threshold': 2
#         },
#         'financial_statement': {
#             'keywords': [
#                 'balance sheet', 'income statement', 'cash flow',
#                 'assets', 'liabilities', 'equity', 'revenue', 'expenses',
#                 'net income', 'fiscal year'
#             ],
#             'required_patterns': [
#                 r'(assets|liabilities|equity)',
#                 r'(revenue|income|expenses)',
#             ],
#             'confidence_threshold': 2
#         },
#         'tax_document': {
#             'keywords': [
#                 'w-2', 'w-9', '1099', 'tax return', 'irs', 'federal',
#                 'withholding', 'taxable income', 'deduction'
#             ],
#             'required_patterns': [
#                 r'(w-2|w-9|1099|tax\s+return)',
#                 r'(taxable|withholding)',
#             ],
#             'confidence_threshold': 2
#         },
#         'resume': {
#             'keywords': [
#                 'experience', 'education', 'skills', 'employment',
#                 'objective', 'summary', 'certifications', 'references'
#             ],
#             'required_patterns': [
#                 r'(experience|employment)',
#                 r'(education|degree|university)',
#             ],
#             'confidence_threshold': 2
#         },
#         'letter': {
#             'keywords': [
#                 'dear', 'sincerely', 'regards', 'to whom it may concern',
#                 'yours truly', 'cc:', 'subject:'
#             ],
#             'required_patterns': [
#                 r'dear\s+',
#                 r'(sincerely|regards)',
#             ],
#             'confidence_threshold': 2
#         }
#     }
#
#     def __init__(self, file_path: str, raw_text: str = None):
#         self.file_path = file_path
#         self.raw_text = raw_text
#         self._extracted_text = None
#
#     def classify(self) -> Tuple[str, float, Dict]:
#         """
#         Classify document and return (type, confidence, details)
#         """
#         # Get text if not provided
#         if not self.raw_text:
#             self._extracted_text = self._extract_text()
#         else:
#             self._extracted_text = self.raw_text
#
#         if not self._extracted_text:
#             return ('unknown', 0.0, {})
#
#         # Normalize text
#         text_lower = self._extracted_text.lower()
#
#         # Score each document type
#         scores = {}
#         details = {}
#
#         for doc_type, signature in self.DOCUMENT_SIGNATURES.items():
#             score, matched = self._calculate_score(text_lower, signature)
#             scores[doc_type] = score
#             details[doc_type] = {
#                 'score': score,
#                 'matched_keywords': matched['keywords'],
#                 'matched_patterns': matched['patterns']
#             }
#
#         # Get best match
#         if not scores or max(scores.values()) == 0:
#             return ('other', 0.0, details)
#
#         best_type = max(scores, key=scores.get)
#         confidence = min(scores[best_type] / 10.0, 1.0)  # Normalize to 0-1
#
#         return (best_type, confidence, details[best_type])
#
#     def _calculate_score(self, text: str, signature: Dict) -> Tuple[int, Dict]:
#         """
#         Calculate match score for a document type
#         """
#         score = 0
#         matched_keywords = []
#         matched_patterns = []
#
#         # Check keywords
#         for keyword in signature['keywords']:
#             if keyword.lower() in text:
#                 score += 1
#                 matched_keywords.append(keyword)
#
#         # Check required patterns
#         pattern_matches = 0
#         for pattern in signature['required_patterns']:
#             if re.search(pattern, text, re.IGNORECASE):
#                 score += 3  # Patterns are more important
#                 pattern_matches += 1
#                 matched_patterns.append(pattern)
#
#         # Must meet threshold
#         if pattern_matches < signature['confidence_threshold']:
#             score = 0
#
#         return score, {
#             'keywords': matched_keywords,
#             'patterns': matched_patterns
#         }
#
#     def _extract_text(self) -> str:
#         """
#         Extract text from PDF
#         """
#         try:
#             text = ""
#             with pdfplumber.open(self.file_path) as pdf:
#                 # Only extract first 3 pages for classification
#                 for page in pdf.pages[:3]:
#                     text += page.extract_text() or ""
#             return text
#         except Exception as e:
#             print(f"Error extracting text: {e}")
#             return ""
#
#     def is_bank_statement(self) -> bool:
#         """
#         Quick check if document is a bank statement
#         """
#         doc_type, confidence, _ = self.classify()
#         return doc_type == 'bank_statement' and confidence > 0.5