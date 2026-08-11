"""
AI-Powered transaction categorization - no hardcoded merchants needed
Uses LLM to intelligently categorize any transaction
"""
import re
import logging
import ollama
from typing import Dict, List

from app.core import config

logger = logging.getLogger(__name__)


class AITransactionCategorizer:
    """
    Uses LLM to intelligently categorize transactions
    No hardcoded merchant lists - works with any merchant worldwide
    """

    CATEGORIES = [
        'Groceries', 'Dining', 'Gas', 'Shopping', 'Entertainment',
        'Transportation', 'Utilities', 'Healthcare', 'Insurance',
        'Banking', 'Subscription', 'Income', 'Transfer', 'ATM',
        'Bill Payment', 'Housing', 'Education', 'Personal Care',
        'Fitness', 'Travel', 'Other'
    ]

    def __init__(self):
        self.ollama_client = ollama.Client()
        self._cache = {}

    def categorize(self, description: str, amount: float, is_debit: bool) -> Dict:
        """
        Categorize transaction using AI

        Note: Caching handled internally with a dict, not lru_cache
        """
        # Check cache first
        cache_key = f"{description}:{is_debit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Quick pattern matching for obvious cases (fast path)
        quick_category = self._quick_categorize(description)
        if quick_category:
            result = {
                'category': quick_category,
                'merchant': self._extract_merchant(description),
                'confidence': 0.85,
                'method': 'pattern'
            }
            self._cache[cache_key] = result
            return result

        # Use AI for complex cases
        try:
            category = self._ai_categorize(description, amount, is_debit)
            result = {
                'category': category,
                'merchant': self._extract_merchant(description),
                'confidence': 0.90,
                'method': 'ai'
            }
            self._cache[cache_key] = result
            return result
        except Exception as e:
            logger.error(f"AI categorization failed: {e}")
            result = {
                'category': 'Other',
                'merchant': self._extract_merchant(description),
                'confidence': 0.3,
                'method': 'fallback'
            }
            return result


    def _quick_categorize(self, description: str) -> str:
        """
        Fast pattern matching for obvious transactions
        Avoids AI calls for clear-cut cases
        """
        desc_lower = description.lower()

        # ATM
        if re.search(r'\batm\b|cash\s+withdraw', desc_lower):
            return 'ATM'

        # Transfer/Payment apps
        if re.search(r'zelle|venmo|paypal|cash\s*app', desc_lower):
            return 'Transfer'

        # Direct deposit
        if re.search(r'direct\s+dep|payroll|salary|wages', desc_lower):
            return 'Income'

        # Banking fees
        if re.search(r'fee|service\s+charge|overdraft|maintenance', desc_lower):
            return 'Banking'

        # Check payments
        if re.search(r'check\s+#?\d+|chk\s+\d+', desc_lower):
            return 'Bill Payment'

        # Interest
        if re.search(r'interest\s+(earned|paid|credit)', desc_lower):
            return 'Income' if 'earned' in desc_lower or 'credit' in desc_lower else 'Banking'

        return None

    def _ai_categorize(self, description: str, amount: float, is_debit: bool) -> str:
        """
        Use AI to categorize transaction
        Handles complex merchant names and ambiguous cases
        """
        transaction_type = "withdrawal/purchase" if is_debit else "deposit/credit"

        prompt = f"""Categorize this bank transaction into ONE category.

Transaction: "{description}"
Amount: ${amount:.2f}
Type: {transaction_type}

Categories:
- Groceries: supermarkets, food stores, grocery shopping
- Dining: restaurants, cafes, fast food, food delivery
- Gas: gas stations, fuel purchases
- Shopping: retail stores, online shopping, clothing, electronics
- Entertainment: streaming services, movies, games, concerts
- Transportation: uber, lyft, parking, tolls, public transit
- Utilities: electric, water, internet, phone, cable
- Healthcare: pharmacy, doctor, hospital, medical
- Insurance: health, car, home, life insurance
- Banking: fees, charges, interest payments
- Subscription: recurring services (Netflix, Spotify, gym memberships)
- Income: salary, wages, payments received, refunds
- Transfer: transfers between accounts, Zelle, Venmo, PayPal
- ATM: cash withdrawals
- Bill Payment: bill pay transactions, loan payments
- Housing: rent, mortgage, home maintenance
- Education: tuition, books, school fees
- Personal Care: salon, spa, beauty products
- Fitness: gym, sports, fitness equipment
- Travel: hotels, flights, vacation expenses
- Other: anything that doesn't fit above

Think about common patterns:
- "AMZN MKTP US" = Amazon = Shopping
- "SQ *COFFEE SHOP" = Square payment = Dining
- "WM SUPERCENTER" = Walmart = Groceries
- "SHELL OIL" = Gas
- "UBER *TRIP" = Transportation
- "*NETFLIX.COM" = Entertainment/Subscription
- "ACH CREDIT SALARY" = Income

Return ONLY the category name, nothing else. No explanation."""

        response = self.ollama_client.generate(
            model=config.model,
            prompt=prompt,
            options={
                "temperature": 0.1,  # Low temperature for consistent categorization
                "num_predict": 20  # Short response
            }
        )

        category = response['response'].strip()

        # Clean up response
        category = re.sub(r'^[^\w]+', '', category)  # Remove leading symbols
        category = category.split('\n')[0].strip()  # Take first line only
        category = re.sub(r'[.,:;!?]$', '', category)  # Remove trailing punctuation

        # Validate category
        if category not in self.CATEGORIES:
            # Try to match partial
            for valid_cat in self.CATEGORIES:
                if valid_cat.lower() in category.lower():
                    category = valid_cat
                    break
            else:
                logger.warning(f"Invalid category '{category}' from AI, using 'Other'")
                category = 'Other'

        logger.info(f"   AI: '{description[:40]}' → '{category}'")

        return category

    def _extract_merchant(self, description: str) -> str:
        """
        Extract clean merchant name from transaction description
        """
        # Remove common prefixes
        desc = re.sub(r'^(debit card|purchase|pos|pymt|payment|sale)\s+', '', description, flags=re.IGNORECASE)

        # Remove reference numbers and symbols
        desc = re.sub(r'[#*]\w+', '', desc)
        desc = re.sub(r'\s+\d{2}/\d{2}.*$', '', desc)  # Remove trailing dates
        desc = re.sub(r'\s+\d{4,}.*$', '', desc)  # Remove long numbers

        # Clean up
        desc = ' '.join(desc.split())  # Remove extra spaces

        # Take first 2-3 meaningful words
        words = [w for w in desc.split() if len(w) > 2][:3]
        merchant = ' '.join(words)

        return merchant.strip() or description[:30]

    def categorize_batch(self, transactions: List[Dict]) -> List[Dict]:
        """
        Categorize multiple transactions efficiently
        Groups similar transactions to reduce AI calls

        Args:
            transactions: List of transaction dicts with 'description' field

        Returns:
            Same list with 'category' and 'merchant' added to each transaction
        """
        logger.info(f"📊 Batch categorizing {len(transactions)} transactions...")

        # Group by similar descriptions (first 3 words)
        groups = {}
        for txn in transactions:
            desc = txn.get('description', '')
            if not desc:
                continue

            # Create signature from first 3 words
            signature = ' '.join(desc.split()[:3]).lower()

            if signature not in groups:
                groups[signature] = []
            groups[signature].append(txn)

        logger.info(f"   Grouped into {len(groups)} unique transaction types")

        # Categorize one transaction per group
        results = []
        categorized_count = 0

        for signature, group in groups.items():
            # Categorize first transaction in group
            first_txn = group[0]

            try:
                category_info = self.categorize(
                    first_txn.get('description', ''),
                    first_txn.get('debit', 0) + first_txn.get('credit', 0),
                    first_txn.get('debit', 0) > 0
                )

                # Apply category to all transactions in group
                for txn in group:
                    txn['category'] = category_info['category']
                    txn['merchant'] = category_info['merchant']
                    results.append(txn)
                    categorized_count += 1

            except Exception as e:
                logger.error(f"Error categorizing group '{signature}': {e}")
                # Add with fallback category
                for txn in group:
                    txn['category'] = 'Other'
                    txn['merchant'] = self._extract_merchant(txn.get('description', ''))
                    results.append(txn)

        logger.info(f"✅ Categorized {categorized_count} transactions using {len(groups)} AI calls")

        return results

    def get_category_summary(self, transactions: List[Dict]) -> Dict[str, float]:
        """
        Get spending summary by category

        Args:
            transactions: List of categorized transactions

        Returns:
            {'Groceries': 450.23, 'Dining': 234.56, ...}
        """
        summary = {}

        for txn in transactions:
            category = txn.get('category', 'Other')
            amount = txn.get('debit', 0)  # Only count expenses

            if amount > 0:
                summary[category] = summary.get(category, 0) + amount

        # Sort by amount descending
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    def get_merchant_summary(self, transactions: List[Dict], top_n: int = 10) -> List[Dict]:
        """
        Get top merchants by spending

        Args:
            transactions: List of categorized transactions
            top_n: Number of top merchants to return

        Returns:
            [{'merchant': 'Walmart', 'total': 450.23, 'count': 5}, ...]
        """
        merchants = {}

        for txn in transactions:
            merchant = txn.get('merchant', 'Unknown')
            amount = txn.get('debit', 0)

            if amount > 0:
                if merchant not in merchants:
                    merchants[merchant] = {'total': 0, 'count': 0}

                merchants[merchant]['total'] += amount
                merchants[merchant]['count'] += 1

        # Convert to list and sort
        result = [
            {
                'merchant': merchant,
                'total': round(data['total'], 2),
                'count': data['count']
            }
            for merchant, data in merchants.items()
        ]

        return sorted(result, key=lambda x: x['total'], reverse=True)[:top_n]


# Singleton instance
_categorizer = None


def get_categorizer() -> AITransactionCategorizer:
    """
    Get singleton categorizer instance

    Returns:
        AITransactionCategorizer instance
    """
    global _categorizer
    if _categorizer is None:
        _categorizer = AITransactionCategorizer()
        logger.info("✅ AI Transaction Categorizer initialized")
    return _categorizer