"""
Credit Card Statement Analyzer
================================
Extraction + Intelligence for UniDocVerse credit card statement documents.

Supported issuers / formats:
  Chase (Sapphire, Freedom, Ink, Amazon)
  Bank of America (Cash Rewards, Travel Rewards, Premium Rewards)
  American Express (Gold, Platinum, Blue Cash, Delta, Hilton)
  Citibank (Double Cash, Premier, Custom Cash, AAdvantage)
  Capital One (Venture, Quicksilver, Savor)
  Discover (It, Miles)
  Wells Fargo (Active Cash, Autograph, Reflect)
  US Bank (Cash+, Altitude, Platinum)
  Generic / store cards / credit union cards

Architecture:
  CreditCardExtractor    — table-first + text fallback extraction
  CreditCardData         — clean dataclass, no business logic
  CreditCardIntelligence — advisory layer (spend analysis, utilization, rewards)
  CreditCardAnalyzer     — orchestrator called by DocumentAnalysisNode

Intelligence output:
  - Spend breakdown by category (dining, travel, gas, groceries, shopping, etc.)
  - Balance / utilization ratio vs credit limit
  - Minimum payment vs full payoff comparison (interest cost projection)
  - Payment due date alert
  - Rewards / points / cashback summary
  - Unusual charge detection (large single charges, duplicate merchants)
  - Month-over-month spend delta (if prior statement data available)
  - Effective interest rate and interest paid this period
  - Action plan (pay full balance, dispute charges, redeem rewards)

Drop at: app/services/analyzers/finance/credit_card_analyzer.py

Wire in:
  analyzer_registry.py:  "credit_card": CreditCardAnalyzer()
  document_registry.py:  "credit_card": {"parser": None, "analyzer": CreditCardAnalyzer}
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

# Known issuer name fragments → normalized issuer key
ISSUER_PATTERNS: List[Tuple[str, str]] = [
    ("chase",           "Chase"),
    ("jpmorgan",        "Chase"),
    ("bank of america", "Bank of America"),
    ("bankamerica",     "Bank of America"),
    ("american express","American Express"),
    ("amex",            "American Express"),
    ("citibank",        "Citibank"),
    ("citi ",           "Citibank"),
    ("capital one",     "Capital One"),
    ("discover",        "Discover"),
    ("wells fargo",     "Wells Fargo"),
    ("us bank",         "US Bank"),
    ("u.s. bank",       "US Bank"),
    ("synchrony",       "Synchrony"),
    ("barclays",        "Barclays"),
    ("td bank",         "TD Bank"),
    ("usaa",            "USAA"),
    ("navy federal",    "Navy Federal"),
    ("pnc",             "PNC"),
    ("regions",         "Regions"),
    ("truist",          "Truist"),
]

# Merchant description → spend category
# Ordered: more specific first
MERCHANT_CATEGORIES: List[Tuple[str, str]] = [
    # Dining
    ("restaurant",      "dining"),
    ("grubhub",         "dining"),
    ("doordash",        "dining"),
    ("uber eats",       "dining"),
    ("postmates",       "dining"),
    ("instacart",       "dining"),
    ("starbucks",       "dining"),
    ("mcdonald",        "dining"),
    ("chipotle",        "dining"),
    ("subway",          "dining"),
    ("pizza",           "dining"),
    ("sushi",           "dining"),
    ("cafe",            "dining"),
    ("coffee",          "dining"),
    ("diner",           "dining"),
    ("grill",           "dining"),
    ("bistro",          "dining"),
    ("tavern",          "dining"),
    ("bar & grill",     "dining"),
    ("eatery",          "dining"),
    # Groceries
    ("walmart",         "groceries"),
    ("whole foods",     "groceries"),
    ("trader joe",      "groceries"),
    ("kroger",          "groceries"),
    ("safeway",         "groceries"),
    ("publix",          "groceries"),
    ("wegmans",         "groceries"),
    ("aldi",            "groceries"),
    ("costco",          "groceries"),
    ("sam's club",      "groceries"),
    ("giant",           "groceries"),
    ("stop & shop",     "groceries"),
    ("market",          "groceries"),
    ("grocery",         "groceries"),
    ("supermarket",     "groceries"),
    # Gas / Auto
    ("chevron",         "gas"),
    ("shell",           "gas"),
    ("exxon",           "gas"),
    ("mobil",           "gas"),
    ("bp ",             "gas"),
    ("sunoco",          "gas"),
    ("marathon",        "gas"),
    ("speedway",        "gas"),
    ("gas station",     "gas"),
    ("fuel",            "gas"),
    ("auto zone",       "auto"),
    ("autozone",        "auto"),
    ("o'reilly auto",   "auto"),
    ("advance auto",    "auto"),
    ("jiffy lube",      "auto"),
    ("firestone",       "auto"),
    ("goodyear",        "auto"),
    ("pep boys",        "auto"),
    ("enterprise rent", "auto"),
    ("hertz",           "auto"),
    ("avis",            "auto"),
    # Travel
    ("airline",         "travel"),
    ("airways",         "travel"),
    ("united air",      "travel"),
    ("delta air",       "travel"),
    ("american air",    "travel"),
    ("southwest air",   "travel"),
    ("jetblue",         "travel"),
    ("spirit air",      "travel"),
    ("frontier air",    "travel"),
    ("alaska air",      "travel"),
    ("amtrak",          "travel"),
    ("marriott",        "travel"),
    ("hilton",          "travel"),
    ("hyatt",           "travel"),
    ("ihg",             "travel"),
    ("wyndham",         "travel"),
    ("airbnb",          "travel"),
    ("vrbo",            "travel"),
    ("expedia",         "travel"),
    ("priceline",       "travel"),
    ("booking.com",     "travel"),
    ("hotels.com",      "travel"),
    ("uber",            "travel"),
    ("lyft",            "travel"),
    # Shopping
    ("amazon",          "shopping"),
    ("target",          "shopping"),
    ("best buy",        "shopping"),
    ("home depot",      "shopping"),
    ("lowe's",          "shopping"),
    ("lowes",           "shopping"),
    ("ikea",            "shopping"),
    ("macy's",          "shopping"),
    ("macys",           "shopping"),
    ("nordstrom",       "shopping"),
    ("tj maxx",         "shopping"),
    ("marshall",        "shopping"),
    ("ross stores",     "shopping"),
    ("old navy",        "shopping"),
    ("gap ",            "shopping"),
    ("h&m",             "shopping"),
    ("zara",            "shopping"),
    ("ebay",            "shopping"),
    ("etsy",            "shopping"),
    ("wayfair",         "shopping"),
    ("chewy",           "shopping"),
    ("petco",           "shopping"),
    ("petsmart",        "shopping"),
    # Entertainment
    ("netflix",         "entertainment"),
    ("hulu",            "entertainment"),
    ("disney+",         "entertainment"),
    ("disney plus",     "entertainment"),
    ("hbo",             "entertainment"),
    ("spotify",         "entertainment"),
    ("apple music",     "entertainment"),
    ("pandora",         "entertainment"),
    ("youtube",         "entertainment"),
    ("amazon prime",    "entertainment"),
    ("amc ",            "entertainment"),
    ("regal",           "entertainment"),
    ("cinemark",        "entertainment"),
    ("ticketmaster",    "entertainment"),
    ("stubhub",         "entertainment"),
    ("xbox",            "entertainment"),
    ("playstation",     "entertainment"),
    ("steam",           "entertainment"),
    ("nintendo",        "entertainment"),
    # Utilities & Services
    ("electric",        "utilities"),
    ("utility",         "utilities"),
    ("water bill",      "utilities"),
    ("gas bill",        "utilities"),
    ("at&t",            "utilities"),
    ("verizon",         "utilities"),
    ("t-mobile",        "utilities"),
    ("comcast",         "utilities"),
    ("spectrum",        "utilities"),
    ("xfinity",         "utilities"),
    ("cox comm",        "utilities"),
    # Health
    ("pharmacy",        "health"),
    ("cvs",             "health"),
    ("walgreens",       "health"),
    ("rite aid",        "health"),
    ("hospital",        "health"),
    ("medical",         "health"),
    ("clinic",          "health"),
    ("dentist",         "health"),
    ("vision",          "health"),
    ("optometrist",     "health"),
    ("gym",             "health"),
    ("planet fitness",  "health"),
    ("24 hour fitness", "health"),
    ("equinox",         "health"),
    ("peloton",         "health"),
    # Education
    ("tuition",         "education"),
    ("university",      "education"),
    ("college",         "education"),
    ("udemy",           "education"),
    ("coursera",        "education"),
    ("chegg",           "education"),
    ("pearson",         "education"),
    # Insurance
    ("insurance",       "insurance"),
    ("geico",           "insurance"),
    ("progressive",     "insurance"),
    ("state farm",      "insurance"),
    ("allstate",        "insurance"),
    # Fees (card-specific)
    ("annual fee",      "fees"),
    ("late fee",        "fees"),
    ("foreign transaction", "fees"),
    ("cash advance fee","fees"),
    ("balance transfer","fees"),
    ("overlimit fee",   "fees"),
    ("returned payment","fees"),
    # Interest
    ("interest charge", "interest"),
    ("finance charge",  "interest"),
    ("purchase interest","interest"),
    ("cash adv interest","interest"),
    # Payments / credits
    ("payment",         "payment"),
    ("autopay",         "payment"),
    ("online payment",  "payment"),
    ("credit",          "credit"),
    ("refund",          "credit"),
    ("return",          "credit"),
    ("reversal",        "credit"),
    ("adjustment",      "credit"),
    # ATM / cash
    ("atm",             "cash_advance"),
    ("cash advance",    "cash_advance"),
    ("cash withdrawal", "cash_advance"),
]

# Category display metadata
CATEGORY_META: Dict[str, Dict[str, str]] = {
    "dining":       {"label": "Dining & Food Delivery", "icon": "restaurant",     "color": "#E8563A"},
    "groceries":    {"label": "Groceries",               "icon": "local_grocery_store", "color": "#4CAF50"},
    "gas":          {"label": "Gas & Fuel",              "icon": "local_gas_station",   "color": "#FF9800"},
    "auto":         {"label": "Auto & Transport",        "icon": "directions_car",      "color": "#795548"},
    "travel":       {"label": "Travel",                  "icon": "flight",              "color": "#2196F3"},
    "shopping":     {"label": "Shopping",                "icon": "shopping_bag",        "color": "#9C27B0"},
    "entertainment":{"label": "Entertainment",           "icon": "movie",               "color": "#F44336"},
    "utilities":    {"label": "Utilities & Phone",       "icon": "bolt",                "color": "#607D8B"},
    "health":       {"label": "Health & Wellness",       "icon": "favorite",            "color": "#E91E63"},
    "education":    {"label": "Education",               "icon": "school",              "color": "#3F51B5"},
    "insurance":    {"label": "Insurance",               "icon": "security",            "color": "#009688"},
    "fees":         {"label": "Fees",                    "icon": "receipt",             "color": "#FF5722"},
    "interest":     {"label": "Interest Charges",        "icon": "percent",             "color": "#D32F2F"},
    "payment":      {"label": "Payments",                "icon": "payment",             "color": "#43A047"},
    "credit":       {"label": "Credits & Refunds",       "icon": "undo",                "color": "#00897B"},
    "cash_advance": {"label": "Cash Advances",           "icon": "atm",                 "color": "#B71C1C"},
    "other":        {"label": "Other",                   "icon": "more_horiz",          "color": "#9E9E9E"},
}

# Utilization risk thresholds
UTILIZATION_GOOD     = 10.0   # < 10% — excellent
UTILIZATION_OK       = 30.0   # 10–30% — good
UTILIZATION_WARNING  = 50.0   # 30–50% — fair
UTILIZATION_HIGH     = 75.0   # 50–75% — high
                               # > 75%  — very high (risk)

# Row labels to skip during table scan
SKIP_LABELS = {
    "transaction", "transactions", "date", "description", "amount",
    "reference", "activity", "post date", "posting date", "trans date",
    "debit", "credit", "balance", "details", "merchant", "category",
    "page", "continued", "subtotal", "total transactions",
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Transaction:
    description:    str
    amount:         float                  # positive = charge, negative = payment/credit
    trans_date:     Optional[str]  = None
    post_date:      Optional[str]  = None
    category:       str            = "other"
    is_payment:     bool           = False
    is_credit:      bool           = False
    reference:      Optional[str]  = None


@dataclass
class CreditCardData:
    document_type:          str   = "credit_card"
    extraction_confidence:  float = 0.0
    missing_fields:         List[str] = field(default_factory=list)
    extraction_timestamp:   str   = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Issuer / card identity
    issuer:                 Optional[str]  = None
    card_name:              Optional[str]  = None
    card_last4:             Optional[str]  = None
    account_number_masked:  Optional[str]  = None

    # Cardholder
    cardholder_name:        Optional[str]  = None

    # Statement period
    statement_date:         Optional[str]  = None
    period_start:           Optional[str]  = None
    period_end:             Optional[str]  = None
    payment_due_date:       Optional[str]  = None

    # Balances
    previous_balance:       Optional[float] = None
    new_charges:            Optional[float] = None
    payments_credits:       Optional[float] = None
    fees:                   Optional[float] = None
    interest_charged:       Optional[float] = None
    new_balance:            Optional[float] = None
    statement_balance:      Optional[float] = None
    minimum_payment:        Optional[float] = None
    credit_limit:           Optional[float] = None
    available_credit:       Optional[float] = None

    # APR / rates
    purchase_apr:           Optional[float] = None
    cash_advance_apr:       Optional[float] = None
    penalty_apr:            Optional[float] = None

    # Rewards
    rewards_earned:         Optional[float] = None
    rewards_balance:        Optional[float] = None
    rewards_type:           Optional[str]   = None  # points | miles | cashback

    # Transactions
    transactions:           List[Transaction] = field(default_factory=list)

    # Derived (set by intelligence)
    utilization_pct:        Optional[float] = None
    total_charges:          Optional[float] = None
    total_payments:         Optional[float] = None
    days_until_due:         Optional[int]   = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_type":         self.document_type,
            "extraction_confidence": self.extraction_confidence,
            "missing_fields":        self.missing_fields,
            "extraction_timestamp":  self.extraction_timestamp,
            "issuer":                self.issuer,
            "card_name":             self.card_name,
            "card_last4":            self.card_last4,
            "account_number_masked": self.account_number_masked,
            "cardholder_name":       self.cardholder_name,
            "statement_date":        self.statement_date,
            "period_start":          self.period_start,
            "period_end":            self.period_end,
            "payment_due_date":      self.payment_due_date,
            "previous_balance":      self.previous_balance,
            "new_charges":           self.new_charges,
            "payments_credits":      self.payments_credits,
            "fees":                  self.fees,
            "interest_charged":      self.interest_charged,
            "new_balance":           self.new_balance,
            "statement_balance":     self.statement_balance,
            "minimum_payment":       self.minimum_payment,
            "credit_limit":          self.credit_limit,
            "available_credit":      self.available_credit,
            "purchase_apr":          self.purchase_apr,
            "cash_advance_apr":      self.cash_advance_apr,
            "penalty_apr":           self.penalty_apr,
            "rewards_earned":        self.rewards_earned,
            "rewards_balance":       self.rewards_balance,
            "rewards_type":          self.rewards_type,
            "utilization_pct":       self.utilization_pct,
            "total_charges":         self.total_charges,
            "total_payments":        self.total_payments,
            "days_until_due":        self.days_until_due,
            "transactions": [
                {
                    "description": t.description,
                    "amount":      t.amount,
                    "trans_date":  t.trans_date,
                    "post_date":   t.post_date,
                    "category":    t.category,
                    "is_payment":  t.is_payment,
                    "is_credit":   t.is_credit,
                    "reference":   t.reference,
                }
                for t in self.transactions
            ],
        }


# ─── Extractor ────────────────────────────────────────────────────────────────

class CreditCardExtractor:
    """
    Table-first extractor for credit card statements.

    Primary path:   Docling table rows → label + amounts
    Secondary path: Raw text regex patterns
    Transaction path: Multi-column table rows with date + description + amount
    """

    # ── Amount parsing ────────────────────────────────────────────────────────

    def _parse(self, raw: str) -> Optional[float]:
        if not raw:
            return None
        s = str(raw).strip().rstrip("*").strip()

        # CR / DR suffix (some issuers)
        is_credit = s.upper().endswith(" CR") or s.upper().endswith("CR")
        is_debit  = s.upper().endswith(" DR") or s.upper().endswith("DR")
        if is_credit or is_debit:
            s = re.sub(r'\s*[CcDd][Rr]$', '', s).strip()

        # Parenthetical negative
        negative = s.startswith('(') and s.endswith(')')
        if negative:
            s = s[1:-1].strip()

        s = s.lstrip('$').strip()
        if s.startswith('-'):
            negative = True
            s = s[1:].strip()

        if not s:
            return None

        plain = s.replace(',', '')
        try:
            v = float(plain)
            result = -v if negative else v
            # CR suffix = credit/negative in statement context
            return -abs(result) if is_credit else result
        except ValueError:
            return None

    # ── Categorization ────────────────────────────────────────────────────────

    def _categorize(self, description: str) -> str:
        dl = description.lower()
        for keyword, cat in MERCHANT_CATEGORIES:
            if keyword in dl:
                return cat
        return "other"

    def _is_payment_or_credit(self, description: str, amount: float) -> Tuple[bool, bool]:
        """Returns (is_payment, is_credit)"""
        dl = description.lower()
        is_pay = any(k in dl for k in (
            "payment", "autopay", "auto pay", "thank you",
            "online payment", "mobile payment", "check payment",
        ))
        is_cred = any(k in dl for k in (
            "refund", "return", "credit", "reversal", "adjustment",
            "cashback", "cash back", "reward redemption",
        )) or amount < 0
        return is_pay, is_cred and not is_pay

    # ── Table scanner: summary section ───────────────────────────────────────

    def _scan_summary_tables(self, tables: List, data: CreditCardData) -> None:
        """Extract balance summary, limits, APR, rewards from header tables."""
        for row in tables:
            if not row or len(row) < 2:
                continue

            label = ""
            rest  = []
            for i, cell in enumerate(row):
                s = str(cell).strip() if cell else ""
                if s and s.lower().strip() not in SKIP_LABELS and len(s) > 1:
                    label = s
                    rest  = [str(c).strip() if c else "" for c in row[i+1:]]
                    break

            if not label:
                continue

            ll = label.lower().strip()
            amounts = [self._parse(c) for c in rest if self._parse(c) is not None]
            if not amounts:
                continue

            v = amounts[0]

            # Previous balance
            if any(k in ll for k in ("previous balance", "previous statement balance", "prior balance")):
                if not data.previous_balance:
                    data.previous_balance = abs(v)

            # New charges / purchases
            elif any(k in ll for k in ("new charges", "purchases", "new purchases", "total new charges")):
                if not data.new_charges:
                    data.new_charges = abs(v)

            # Payments / credits
            elif any(k in ll for k in ("payments", "payments and credits", "payment and credits", "credits")):
                if not data.payments_credits:
                    data.payments_credits = abs(v)

            # Fees
            elif any(k in ll for k in ("fees charged", "total fees", "fee")):
                if not data.fees:
                    data.fees = abs(v)

            # Interest
            elif any(k in ll for k in ("interest charged", "finance charge", "interest charge", "total interest")):
                if not data.interest_charged:
                    data.interest_charged = abs(v)

            # New / current balance
            elif any(k in ll for k in ("new balance", "current balance", "balance due", "amount due")):
                if not data.new_balance and abs(v) > 0:
                    data.new_balance = abs(v)

            # Statement balance (may differ from new balance)
            elif "statement balance" in ll:
                if not data.statement_balance:
                    data.statement_balance = abs(v)

            # Minimum payment
            elif any(k in ll for k in ("minimum payment", "minimum payment due", "minimum due", "min payment")):
                if not data.minimum_payment:
                    data.minimum_payment = abs(v)

            # Credit limit
            elif any(k in ll for k in ("credit limit", "credit line", "total credit line")):
                if not data.credit_limit:
                    data.credit_limit = abs(v)

            # Available credit
            elif any(k in ll for k in ("available credit", "credit available", "remaining credit")):
                if not data.available_credit:
                    data.available_credit = abs(v)

            # APR
            elif any(k in ll for k in ("purchase apr", "purchases apr", "standard apr")):
                if not data.purchase_apr:
                    data.purchase_apr = abs(v)

            elif any(k in ll for k in ("cash advance apr", "cash apr")):
                if not data.cash_advance_apr:
                    data.cash_advance_apr = abs(v)

            elif "penalty apr" in ll:
                if not data.penalty_apr:
                    data.penalty_apr = abs(v)

            # Rewards
            elif any(k in ll for k in ("rewards earned", "points earned", "miles earned", "cash back earned")):
                if not data.rewards_earned:
                    data.rewards_earned = abs(v)

            elif any(k in ll for k in ("rewards balance", "points balance", "total points", "total miles", "available cash back")):
                if not data.rewards_balance:
                    data.rewards_balance = abs(v)

    # ── Table scanner: transactions ───────────────────────────────────────────

    def _scan_transaction_tables(self, tables: List, data: CreditCardData) -> None:
        """
        Detect and parse transaction rows.
        Transaction row heuristic: first cell looks like a date, last/penultimate cell is a dollar amount.
        """
        DATE_RE = re.compile(
            r'^\d{1,2}/\d{1,2}(?:/\d{2,4})?$'   # MM/DD or MM/DD/YY
            r'|^\d{4}-\d{2}-\d{2}$'               # YYYY-MM-DD
            r'|^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}',  # Mon DD
            re.IGNORECASE,
        )

        transactions: List[Transaction] = []

        for row in tables:
            if not row or len(row) < 2:
                continue

            cells = [str(c).strip() if c else "" for c in row]

            # Need at least one date-looking cell and one amount cell
            date_cells   = [i for i, c in enumerate(cells) if DATE_RE.match(c)]
            amount_cells = [i for i, c in enumerate(cells) if self._parse(c) is not None and i > 0]

            if not date_cells or not amount_cells:
                continue

            # Skip header-like rows
            label_lower = cells[0].lower()
            if label_lower in SKIP_LABELS or not cells[0]:
                continue

            trans_date = cells[date_cells[0]]
            post_date  = cells[date_cells[1]] if len(date_cells) > 1 else None

            # Description: longest non-date, non-amount cell between date and amount
            desc_candidates = [
                cells[i] for i in range(len(cells))
                if i not in date_cells
                and i not in amount_cells
                and len(cells[i]) > 2
                and not cells[i].isdigit()
            ]
            description = max(desc_candidates, key=len) if desc_candidates else cells[-2] if len(cells) > 2 else ""

            if not description or len(description) < 2:
                continue

            # Amount: last valid amount cell
            amount_raw = cells[amount_cells[-1]]
            amount = self._parse(amount_raw)
            if amount is None:
                continue

            # Reference number: cell that looks like a ref (all digits or alphanumeric, 6-20 chars)
            ref = None
            for c in cells:
                if re.match(r'^[A-Z0-9]{6,20}$', c.upper()) and c not in [trans_date, post_date]:
                    ref = c
                    break

            category = self._categorize(description)
            is_pay, is_cred = self._is_payment_or_credit(description, amount)

            if is_pay:
                category = "payment"
            elif is_cred and category == "other":
                category = "credit"

            transactions.append(Transaction(
                description=description,
                amount=amount,
                trans_date=trans_date,
                post_date=post_date,
                category=category,
                is_payment=is_pay,
                is_credit=is_cred,
                reference=ref,
            ))

        if transactions:
            data.transactions = transactions

    # ── Text fallbacks ────────────────────────────────────────────────────────

    def _text_identity(self, text: str, data: CreditCardData) -> None:
        tl = text.lower()

        # Issuer
        if not data.issuer:
            for fragment, issuer in ISSUER_PATTERNS:
                if fragment in tl:
                    data.issuer = issuer
                    break

        # Card last 4 digits
        if not data.card_last4:
            m = re.search(r'(?:ending|ending in|acct|account|card)\s*(?:in\s*)?[:#]?\s*[xX*]{0,12}(\d{4})\b', text, re.I)
            if not m:
                m = re.search(r'\b[xX*]{4}\s*[xX*]{4}\s*[xX*]{4}\s*(\d{4})\b', text)
            if not m:
                m = re.search(r'\b[xX*]{4,12}(\d{4})\b', text)
            if m:
                data.card_last4 = m.group(1)

        # Account number masked
        if not data.account_number_masked and data.card_last4:
            data.account_number_masked = f"****-****-****-{data.card_last4}"

        # Cardholder
        if not data.cardholder_name:
            for pat in [
                r'(?:account holder|cardholder|prepared for|name on account)\s*:?\s*([A-Z][A-Za-z\s\-\.]{4,40})',
                r'(?:dear|hello)\s+([A-Z][A-Za-z\s]{3,30}),',
            ]:
                m = re.search(pat, text, re.I)
                if m:
                    name = m.group(1).strip().title()
                    if 2 <= len(name.split()) <= 5:
                        data.cardholder_name = name
                        break

    def _text_dates(self, text: str, data: CreditCardData) -> None:
        DR = r'\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s+\d{4}'

        if not data.statement_date:
            for pat in [
                rf'statement\s+(?:date|closing\s+date|period\s+end(?:ing)?)\s*:?\s*({DR})',
                rf'closing\s+date\s*:?\s*({DR})',
            ]:
                m = re.search(pat, text, re.I)
                if m:
                    data.statement_date = m.group(1).strip()
                    break

        if not data.period_start:
            m = re.search(rf'(?:billing\s+period|statement\s+period|activity\s+from)\s*:?\s*({DR})', text, re.I)
            if not m:
                m = re.search(rf'(?:from|begin(?:ning)?)\s*:?\s*({DR})\s+(?:to|through|-)', text, re.I)
            if m:
                data.period_start = m.group(1).strip()

        if not data.period_end:
            m = re.search(rf'(?:to|through|-|–)\s*({DR})', text, re.I)
            if m:
                data.period_end = m.group(1).strip()

        if not data.payment_due_date:
            for pat in [
                rf'payment\s+due\s+date\s*:?\s*({DR})',
                rf'due\s+date\s*:?\s*({DR})',
                rf'pay\s+by\s*:?\s*({DR})',
                rf'minimum\s+payment\s+due.*?({DR})',
            ]:
                m = re.search(pat, text, re.I)
                if m:
                    data.payment_due_date = m.group(1).strip()
                    break

    def _text_balances(self, text: str, data: CreditCardData) -> None:
        """Text-based amount extraction when tables miss summary fields."""
        patterns = [
            ("new_balance",      [r'new\s+balance\s*\$?\s*([\d,\.]+)',
                                   r'balance\s+due\s*\$?\s*([\d,\.]+)']),
            ("minimum_payment",  [r'minimum\s+payment\s+due\s*\$?\s*([\d,\.]+)',
                                   r'minimum\s+due\s*\$?\s*([\d,\.]+)']),
            ("credit_limit",     [r'credit\s+limit\s*\$?\s*([\d,\.]+)',
                                   r'credit\s+line\s*\$?\s*([\d,\.]+)']),
            ("available_credit", [r'available\s+credit\s*\$?\s*([\d,\.]+)',
                                   r'credit\s+available\s*\$?\s*([\d,\.]+)']),
            ("purchase_apr",     [r'purchase\s+apr\s*[\:\ ]*\s*([\d\.]+)\s*%',
                                   r'variable\s+apr\s*[\:\ ]*\s*([\d\.]+)\s*%']),
            ("interest_charged", [r'(?:total\s+)?interest\s+charged\s*\$?\s*([\d,\.]+)']),
            ("fees",             [r'(?:total\s+)?fees\s+charged\s*\$?\s*([\d,\.]+)']),
        ]
        for attr, pats in patterns:
            if getattr(data, attr) is not None:
                continue
            for pat in pats:
                m = re.search(pat, text, re.I)
                if m:
                    v = self._parse(m.group(1))
                    if v is not None and v >= 0:
                        setattr(data, attr, v)
                        break

        # Rewards type detection
        if not data.rewards_type:
            tl = text.lower()
            if any(k in tl for k in ("cash back", "cashback", "cash rewards")):
                data.rewards_type = "cashback"
            elif any(k in tl for k in ("miles", "airline miles")):
                data.rewards_type = "miles"
            elif any(k in tl for k in ("points", "thank you points", "ultimate rewards", "membership rewards")):
                data.rewards_type = "points"

    def _text_transactions(self, text: str, data: CreditCardData) -> None:
        """
        Fallback transaction parser for text-only content.
        Looks for lines: date  description  amount
        """
        if data.transactions:
            return  # Already extracted from tables

        DATE_PAT = r'(\d{1,2}/\d{1,2}(?:/\d{2,4})?)'
        AMOUNT_PAT = r'(-?\$?[\d,]+\.\d{2})'
        # Pattern: date [post_date] description amount
        line_re = re.compile(
            rf'^{DATE_PAT}\s+(?:{DATE_PAT}\s+)?(.+?)\s+{AMOUNT_PAT}\s*$',
            re.MULTILINE,
        )

        transactions = []
        for m in line_re.finditer(text):
            trans_date  = m.group(1)
            post_date   = m.group(2)
            description = m.group(3).strip()
            amount      = self._parse(m.group(4))

            if amount is None or not description or len(description) < 2:
                continue
            if description.lower() in SKIP_LABELS:
                continue

            category = self._categorize(description)
            is_pay, is_cred = self._is_payment_or_credit(description, amount)
            if is_pay:
                category = "payment"
            elif is_cred and category == "other":
                category = "credit"

            transactions.append(Transaction(
                description=description,
                amount=amount,
                trans_date=trans_date,
                post_date=post_date,
                category=category,
                is_payment=is_pay,
                is_credit=is_cred,
            ))

        if transactions:
            data.transactions = transactions

    # ── Post-processing ───────────────────────────────────────────────────────

    def _derive(self, data: CreditCardData) -> None:
        """Fill gaps derivable from other fields."""
        # Compute totals from transactions if not extracted from summary
        charges  = [t.amount for t in data.transactions if not t.is_payment and not t.is_credit and t.amount > 0]
        payments = [abs(t.amount) for t in data.transactions if t.is_payment]
        credits  = [abs(t.amount) for t in data.transactions if t.is_credit and not t.is_payment]

        if charges:
            data.total_charges = round(sum(charges), 2)
        if payments:
            data.total_payments = round(sum(payments), 2)

        # Infer new_balance from components
        if not data.new_balance and data.previous_balance is not None:
            new_bal = (data.previous_balance or 0) + (data.new_charges or 0) - (data.payments_credits or 0)
            if new_bal > 0:
                data.new_balance = round(new_bal, 2)

        # Infer credit limit from available + balance
        if not data.credit_limit and data.available_credit and data.new_balance:
            data.credit_limit = round(data.new_balance + data.available_credit, 0)

        # Infer available credit
        if not data.available_credit and data.credit_limit and data.new_balance:
            data.available_credit = max(0.0, round(data.credit_limit - data.new_balance, 2))

        # Utilization
        balance = data.new_balance or data.statement_balance
        if data.credit_limit and balance:
            data.utilization_pct = round(balance / data.credit_limit * 100, 1)

        # Days until due
        if data.payment_due_date:
            try:
                due_dt = self._parse_date(data.payment_due_date)
                if due_dt:
                    delta = (due_dt - date.today()).days
                    data.days_until_due = delta
            except Exception:
                pass

        # new_charges from transaction totals if not in summary
        if not data.new_charges and data.total_charges:
            data.new_charges = data.total_charges

    def _parse_date(self, date_str: str) -> Optional[date]:
        formats = [
            "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d",
            "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _score(self, data: CreditCardData) -> None:
        critical = [
            ("new_balance",      data.new_balance),
            ("payment_due_date", data.payment_due_date),
            ("credit_limit",     data.credit_limit),
            ("transactions",     data.transactions or None),
        ]
        found = sum(1 for _, v in critical if v is not None)
        data.extraction_confidence = round(found / len(critical), 2)
        data.missing_fields = [n for n, v in critical if v is None]

    # ── Entry point ───────────────────────────────────────────────────────────

    def extract(self, raw_text: str, tables: Optional[List] = None) -> CreditCardData:
        text   = (raw_text or "").replace('\r\n', '\n').replace('\r', '\n')
        tables = tables or []
        data   = CreditCardData()

        # Two passes on tables: summary first, then transactions
        self._scan_summary_tables(tables, data)
        self._scan_transaction_tables(tables, data)

        # Text fallbacks
        self._text_identity(text, data)
        self._text_dates(text, data)
        self._text_balances(text, data)
        self._text_transactions(text, data)

        self._derive(data)
        self._score(data)

        logger.info(
            f"CreditCardExtractor: confidence={data.extraction_confidence:.0%} "
            f"balance={data.new_balance} txns={len(data.transactions)} "
            f"issuer={data.issuer} missing={data.missing_fields}"
        )
        return data


# ─── Intelligence Layer ───────────────────────────────────────────────────────

class CreditCardIntelligence:
    """
    Advisory layer — answers the questions every cardholder actually has:
      1. Where am I spending my money?
      2. How much is carrying this balance costing me?
      3. Is my credit utilization hurting my credit score?
      4. When is my payment due and what should I pay?
      5. Are there suspicious or unusual charges?
      6. What should I do next?
    """

    def analyze(self, data: CreditCardData) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        result.update(self._spend_analysis(data))
        result.update(self._balance_analysis(data))
        result.update(self._utilization_analysis(data))
        result.update(self._payment_analysis(data))
        result.update(self._interest_analysis(data))
        result.update(self._rewards_analysis(data))
        result.update(self._unusual_charges(data))
        result["action_plan"]     = self._actions(data, result)
        result["alerts"]          = self._alerts(data, result)
        result["payment_options"] = self._payment_options(data)
        return result

    # ── Spend analysis ────────────────────────────────────────────────────────

    def _spend_analysis(self, data: CreditCardData) -> Dict:
        charges = [t for t in data.transactions if not t.is_payment and not t.is_credit and t.amount > 0]

        if not charges:
            return {"spend_by_category": [], "top_merchants": [], "total_spend": 0.0}

        total = sum(t.amount for t in charges)

        # Category totals
        cat_totals: Dict[str, float] = {}
        for t in charges:
            cat_totals[t.category] = cat_totals.get(t.category, 0.0) + t.amount

        spend_by_category = []
        for cat, amount in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
            meta = CATEGORY_META.get(cat, CATEGORY_META["other"])
            spend_by_category.append({
                "category":    cat,
                "label":       meta["label"],
                "amount":      round(amount, 2),
                "pct":         round(amount / total * 100, 1),
                "color":       meta["color"],
                "icon":        meta["icon"],
            })

        # Top merchants (unique descriptions, sorted by total spend)
        merchant_totals: Dict[str, float] = {}
        for t in charges:
            desc = t.description[:40]
            merchant_totals[desc] = merchant_totals.get(desc, 0.0) + t.amount
        top_merchants = sorted(
            [{"merchant": k, "amount": round(v, 2)} for k, v in merchant_totals.items()],
            key=lambda x: x["amount"], reverse=True
        )[:10]

        return {
            "spend_by_category": spend_by_category,
            "top_merchants":     top_merchants,
            "total_spend":       round(total, 2),
        }

    # ── Balance analysis ──────────────────────────────────────────────────────

    def _balance_analysis(self, data: CreditCardData) -> Dict:
        balance = data.new_balance or data.statement_balance or 0.0
        return {
            "balance_summary": {
                "previous_balance":   data.previous_balance,
                "new_charges":        data.new_charges or data.total_charges,
                "payments_credits":   data.payments_credits or data.total_payments,
                "fees":               data.fees,
                "interest":           data.interest_charged,
                "new_balance":        balance,
                "minimum_payment":    data.minimum_payment,
                "credit_limit":       data.credit_limit,
                "available_credit":   data.available_credit,
            }
        }

    # ── Utilization analysis ──────────────────────────────────────────────────

    def _utilization_analysis(self, data: CreditCardData) -> Dict:
        if not data.utilization_pct or not data.credit_limit:
            return {"utilization_analysis": {"available": False}}

        pct = data.utilization_pct

        if pct < UTILIZATION_GOOD:
            rating = "excellent"
            message = f"Credit utilization is {pct:.1f}% — excellent for your credit score."
            color = "#43A047"
        elif pct < UTILIZATION_OK:
            rating = "good"
            message = f"Credit utilization is {pct:.1f}% — well within the recommended 30% threshold."
            color = "#7CB342"
        elif pct < UTILIZATION_WARNING:
            rating = "fair"
            message = f"Credit utilization is {pct:.1f}% — approaching the 30% recommended limit."
            color = "#FFB300"
        elif pct < UTILIZATION_HIGH:
            rating = "high"
            message = f"Utilization at {pct:.1f}% may be impacting your credit score. Paying down reduces impact."
            color = "#F4511E"
        else:
            rating = "very_high"
            message = f"Utilization at {pct:.1f}% is likely hurting your credit score significantly."
            color = "#C62828"

        # How much to pay to reach 30% / 10%
        balance = data.new_balance or data.statement_balance or 0.0
        target_30_pct = round(data.credit_limit * 0.30, 2)
        target_10_pct = round(data.credit_limit * 0.10, 2)
        pay_to_30 = max(0.0, round(balance - target_30_pct, 2))
        pay_to_10 = max(0.0, round(balance - target_10_pct, 2))

        return {
            "utilization_analysis": {
                "available":       True,
                "pct":             pct,
                "rating":          rating,
                "message":         message,
                "color":           color,
                "credit_limit":    data.credit_limit,
                "balance":         balance,
                "pay_to_30_pct":   pay_to_30 if pay_to_30 > 0 else None,
                "pay_to_10_pct":   pay_to_10 if pay_to_10 > 0 else None,
            }
        }

    # ── Payment analysis ──────────────────────────────────────────────────────

    def _payment_analysis(self, data: CreditCardData) -> Dict:
        balance = data.new_balance or data.statement_balance or 0.0

        urgency = "none"
        urgency_message = ""

        if data.days_until_due is not None:
            if data.days_until_due < 0:
                urgency = "overdue"
                urgency_message = f"Payment is {abs(data.days_until_due)} days OVERDUE. Pay immediately to avoid late fees and credit score impact."
            elif data.days_until_due == 0:
                urgency = "due_today"
                urgency_message = "Payment is due TODAY."
            elif data.days_until_due <= 3:
                urgency = "due_soon"
                urgency_message = f"Payment due in {data.days_until_due} days — pay now to avoid late fees."
            elif data.days_until_due <= 7:
                urgency = "due_this_week"
                urgency_message = f"Payment due in {data.days_until_due} days."
            else:
                urgency = "upcoming"
                urgency_message = f"Payment due in {data.days_until_due} days."

        return {
            "payment_analysis": {
                "balance":          balance,
                "minimum_payment":  data.minimum_payment,
                "payment_due_date": data.payment_due_date,
                "days_until_due":   data.days_until_due,
                "urgency":          urgency,
                "urgency_message":  urgency_message,
            }
        }

    # ── Interest analysis ─────────────────────────────────────────────────────

    def _interest_analysis(self, data: CreditCardData) -> Dict:
        balance = data.new_balance or data.statement_balance or 0.0
        apr     = data.purchase_apr
        min_pay = data.minimum_payment

        if not balance or not apr or balance <= 0:
            return {"interest_analysis": {"available": False}}

        monthly_rate = apr / 100 / 12

        # Months to pay off at minimum payment
        months_min = None
        total_interest_min = None
        if min_pay and min_pay > balance * monthly_rate:
            # Standard amortization: n = -log(1 - r*B/P) / log(1+r)
            import math
            try:
                n = -math.log(1 - monthly_rate * balance / min_pay) / math.log(1 + monthly_rate)
                months_min = math.ceil(n)
                total_interest_min = round(min_pay * months_min - balance, 2)
            except (ValueError, ZeroDivisionError):
                pass

        # Monthly interest cost at current balance
        monthly_interest = round(balance * monthly_rate, 2)

        # Daily interest cost
        daily_interest = round(balance * (apr / 100) / 365, 2)

        return {
            "interest_analysis": {
                "available":           True,
                "apr":                 apr,
                "balance":             balance,
                "monthly_interest":    monthly_interest,
                "daily_interest":      daily_interest,
                "months_min_payment":  months_min,
                "total_interest_min":  total_interest_min,
                "interest_this_period": data.interest_charged,
                "message": (
                    f"At {apr:.2f}% APR, carrying ${balance:,.2f} costs "
                    f"~${monthly_interest:,.2f}/month in interest."
                    + (f" Minimum-only payments would take {months_min} months "
                       f"and cost ${total_interest_min:,.2f} in interest."
                       if months_min and months_min < 600 else "")
                ),
            }
        }

    # ── Rewards analysis ──────────────────────────────────────────────────────

    def _rewards_analysis(self, data: CreditCardData) -> Dict:
        if not data.rewards_earned and not data.rewards_balance:
            return {"rewards_analysis": {"available": False}}

        rtype = data.rewards_type or "points"
        unit  = {"cashback": "cash back", "miles": "miles", "points": "points"}.get(rtype, "points")

        summary = {}
        if data.rewards_earned is not None:
            summary["earned_this_period"] = data.rewards_earned
            summary["unit"] = unit

            if rtype == "cashback":
                summary["earned_label"] = f"${data.rewards_earned:.2f} cash back earned this period"
            else:
                summary["earned_label"] = f"{data.rewards_earned:,.0f} {unit} earned this period"

        if data.rewards_balance is not None:
            summary["balance"] = data.rewards_balance
            if rtype == "cashback":
                summary["balance_label"] = f"${data.rewards_balance:.2f} available to redeem"
            else:
                summary["balance_label"] = f"{data.rewards_balance:,.0f} {unit} available"
                # Rough cash value estimate
                ppv = {"miles": 0.015, "points": 0.010}.get(rtype, 0.010)
                summary["estimated_value"] = round(data.rewards_balance * ppv, 2)
                summary["estimated_value_label"] = f"~${summary['estimated_value']:.2f} estimated value"

        return {"rewards_analysis": {"available": True, **summary}}

    # ── Unusual charge detection ──────────────────────────────────────────────

    def _unusual_charges(self, data: CreditCardData) -> Dict:
        charges = [t for t in data.transactions if not t.is_payment and not t.is_credit and t.amount > 0]
        if not charges:
            return {"unusual_charges": []}

        unusual = []
        amounts = [t.amount for t in charges]
        total   = sum(amounts)
        avg     = total / len(amounts) if amounts else 0

        # Large single charge (> 3× average or > $500)
        threshold = max(avg * 3, 500.0)
        for t in charges:
            if t.amount > threshold and t.amount > 100:
                unusual.append({
                    "type":        "large_charge",
                    "description": t.description,
                    "amount":      t.amount,
                    "date":        t.trans_date,
                    "message":     f"Unusually large charge: ${t.amount:,.2f}",
                })

        # Duplicate charges: same merchant + same amount on different dates
        seen: Dict[str, List] = {}
        for t in charges:
            key = f"{t.description[:20].lower().strip()}_{t.amount:.2f}"
            if key not in seen:
                seen[key] = []
            seen[key].append(t)

        for key, txns in seen.items():
            if len(txns) >= 2:
                # Only flag if different dates
                dates = set(t.trans_date for t in txns if t.trans_date)
                if len(dates) > 1:
                    unusual.append({
                        "type":        "potential_duplicate",
                        "description": txns[0].description,
                        "amount":      txns[0].amount,
                        "count":       len(txns),
                        "dates":       list(dates)[:3],
                        "message":     f"Possible duplicate charge: {txns[0].description[:30]} × {len(txns)}",
                    })

        # Cash advance (high APR warning)
        cash_advances = [t for t in charges if t.category == "cash_advance"]
        if cash_advances:
            total_ca = sum(t.amount for t in cash_advances)
            unusual.append({
                "type":    "cash_advance",
                "amount":  round(total_ca, 2),
                "count":   len(cash_advances),
                "message": f"Cash advance(s) detected: ${total_ca:,.2f} — subject to higher APR and fees.",
            })

        return {"unusual_charges": unusual}

    # ── Payment options ───────────────────────────────────────────────────────

    def _payment_options(self, data: CreditCardData) -> List[Dict]:
        balance = data.new_balance or data.statement_balance or 0.0
        if not balance:
            return []

        options = []

        # Option 1: Pay full balance
        options.append({
            "label":       "Pay Full Balance",
            "amount":      balance,
            "recommended": True,
            "description": "Avoid all interest charges. Best for your credit score.",
            "interest":    0.0,
        })

        # Option 2: Pay statement balance (if different from new balance)
        if data.statement_balance and data.statement_balance != balance and data.statement_balance > 0:
            options.append({
                "label":       "Pay Statement Balance",
                "amount":      data.statement_balance,
                "recommended": False,
                "description": "Avoids interest on purchases from last billing cycle.",
                "interest":    None,
            })

        # Option 3: Pay to 30% utilization
        util = data.utilization_analysis if hasattr(data, "utilization_analysis") else {}
        pay_to_30 = None
        if data.credit_limit:
            target = data.credit_limit * 0.30
            if balance > target:
                pay_to_30 = round(balance - target, 2)
                options.append({
                    "label":       "Pay to 30% Utilization",
                    "amount":      pay_to_30,
                    "recommended": False,
                    "description": f"Reduces balance to ${target:,.0f} — improves credit utilization.",
                    "interest":    None,
                })

        # Option 4: Minimum payment
        if data.minimum_payment:
            options.append({
                "label":       "Minimum Payment",
                "amount":      data.minimum_payment,
                "recommended": False,
                "description": "Avoids late fee but incurs interest on remaining balance.",
                "interest":    None,
            })

        return options

    # ── Actions ───────────────────────────────────────────────────────────────

    def _actions(self, data: CreditCardData, result: Dict) -> List[Dict]:
        actions = []
        balance = data.new_balance or data.statement_balance or 0.0

        # Payment urgency
        pa = result.get("payment_analysis", {})
        urgency = pa.get("urgency", "none")
        if urgency in ("overdue", "due_today"):
            actions.append({
                "priority": "high", "category": "payment",
                "action":   "Make payment immediately",
                "detail":   pa.get("urgency_message", ""),
            })
        elif urgency in ("due_soon", "due_this_week"):
            actions.append({
                "priority": "high", "category": "payment",
                "action":   f"Pay before {data.payment_due_date}",
                "detail":   pa.get("urgency_message", ""),
            })

        # High utilization
        ua = result.get("utilization_analysis", {})
        if ua.get("available") and ua.get("rating") in ("high", "very_high"):
            pay_to_30 = ua.get("pay_to_30_pct")
            if pay_to_30:
                actions.append({
                    "priority": "medium", "category": "credit_score",
                    "action":   f"Pay down ${pay_to_30:,.0f} to reach 30% utilization",
                    "detail":   "Reducing utilization below 30% improves your credit score.",
                    "amount":   pay_to_30,
                })

        # Interest cost
        ia = result.get("interest_analysis", {})
        if ia.get("available") and ia.get("monthly_interest", 0) > 20:
            monthly = ia["monthly_interest"]
            actions.append({
                "priority": "medium", "category": "interest",
                "action":   "Pay full balance to eliminate interest",
                "detail":   f"Carrying this balance costs ~${monthly:,.2f}/month in interest.",
                "monthly_savings": monthly,
            })

        # Unusual charges
        unusual = result.get("unusual_charges", [])
        for u in unusual:
            if u["type"] == "potential_duplicate":
                actions.append({
                    "priority": "high", "category": "dispute",
                    "action":   f"Review potential duplicate: {u['description'][:30]}",
                    "detail":   u["message"],
                })

        # Rewards balance (if available and high)
        ra = result.get("rewards_analysis", {})
        if ra.get("available") and ra.get("balance", 0) > 0:
            actions.append({
                "priority": "low", "category": "rewards",
                "action":   "Redeem rewards balance",
                "detail":   ra.get("balance_label", "Rewards available to redeem."),
            })

        return sorted(actions, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])

    # ── Alerts ────────────────────────────────────────────────────────────────

    def _alerts(self, data: CreditCardData, result: Dict) -> List[Dict]:
        alerts = []

        # Payment overdue
        if data.days_until_due is not None and data.days_until_due < 0:
            alerts.append({
                "type": "error", "title": "Payment Overdue",
                "message": f"Payment was due {abs(data.days_until_due)} days ago. Late fees may apply.",
            })

        # Due soon
        elif data.days_until_due is not None and 0 <= data.days_until_due <= 5:
            alerts.append({
                "type": "warning", "title": "Payment Due Soon",
                "message": f"Payment due in {data.days_until_due} days on {data.payment_due_date}.",
            })

        # High utilization
        ua = result.get("utilization_analysis", {})
        if ua.get("available") and ua.get("rating") in ("high", "very_high"):
            alerts.append({
                "type": "warning", "title": "High Credit Utilization",
                "message": ua.get("message", ""),
            })

        # Cash advance
        for u in result.get("unusual_charges", []):
            if u["type"] == "cash_advance":
                alerts.append({
                    "type": "warning", "title": "Cash Advance Detected",
                    "message": u["message"],
                })

        # Interest charges present
        if data.interest_charged and data.interest_charged > 0:
            alerts.append({
                "type": "info", "title": "Interest Charged This Period",
                "message": f"${data.interest_charged:,.2f} in interest charges this period. Paying full balance each month eliminates this.",
            })

        # Unusual / duplicate charges
        for u in result.get("unusual_charges", []):
            if u["type"] in ("large_charge", "potential_duplicate"):
                alerts.append({
                    "type": "info", "title": u["message"].split(":")[0],
                    "message": u["message"],
                })

        return alerts


# ─── Analyzer (called by DocumentAnalysisNode) ────────────────────────────────

class CreditCardAnalyzer:
    """
    Orchestrator. Called by UniDocVerse DocumentAnalysisNode.

    Register in analyzer_registry.py:
        "credit_card": CreditCardAnalyzer()

    Register in document_registry.py:
        "credit_card": {"parser": None, "analyzer": CreditCardAnalyzer}
    """

    def analyze(self, file_path: str, text: str = "",
                metadata: Optional[Dict] = None) -> Dict[str, Any]:
        logger.info(f"📊 CreditCardAnalyzer: {file_path}")
        metadata = metadata or {}

        # Flatten tables from metadata
        raw_tables = []
        for t in metadata.get("tables", []):
            raw_tables.extend(t.get("data", []))

        # Extract
        extractor = CreditCardExtractor()
        data      = extractor.extract(text, raw_tables)

        # Intelligence
        intelligence = CreditCardIntelligence().analyze(data)

        # Assemble result
        result: Dict[str, Any] = {
            "type":                   "credit_card",
            "has_advanced_analytics": True,
        }
        result.update(data.to_dict())
        result.update(intelligence)
        result["hero_metrics"] = self._hero_metrics(data)
        result["insights"]     = self._insights(data, intelligence)
        result["ui_config"]    = self._ui_config(data)

        logger.info(
            f"✅ CreditCardAnalyzer: balance={data.new_balance} "
            f"txns={len(data.transactions)} confidence={data.extraction_confidence:.0%}"
        )
        return result

    def _hero_metrics(self, data: CreditCardData) -> Dict:
        balance = data.new_balance or data.statement_balance
        return {
            "new_balance":      balance,
            "credit_limit":     data.credit_limit,
            "available_credit": data.available_credit,
            "utilization_pct":  data.utilization_pct,
            "minimum_payment":  data.minimum_payment,
            "payment_due_date": data.payment_due_date,
            "days_until_due":   data.days_until_due,
            "total_spend":      data.total_charges,
            "interest_charged": data.interest_charged,
            "rewards_earned":   data.rewards_earned,
        }

    def _insights(self, data: CreditCardData, intel: Dict) -> List[Dict]:
        insights = []
        balance = data.new_balance or data.statement_balance or 0.0

        # Spend summary
        total_spend = intel.get("total_spend", 0)
        if total_spend:
            top_cat = intel.get("spend_by_category", [])[:1]
            top_label = top_cat[0]["label"] if top_cat else "Other"
            top_pct   = top_cat[0]["pct"]   if top_cat else 0
            insights.append({
                "title":       "Spending Summary",
                "description": (
                    f"You spent ${total_spend:,.2f} this statement period. "
                    f"Largest category: {top_label} ({top_pct:.0f}% of spend)."
                ),
                "category": "summary",
            })

        # Utilization
        ua = intel.get("utilization_analysis", {})
        if ua.get("available"):
            insights.append({
                "title":       "Credit Utilization",
                "description": ua["message"],
                "category":    "credit_score",
            })

        # Interest
        ia = intel.get("interest_analysis", {})
        if ia.get("available") and ia.get("monthly_interest", 0) > 0:
            insights.append({
                "title":       "Interest Cost",
                "description": ia["message"],
                "category":    "interest",
            })

        # Payment due
        pa = intel.get("payment_analysis", {})
        if pa.get("urgency_message"):
            insights.append({
                "title":       "Payment Due",
                "description": pa["urgency_message"],
                "category":    "payment",
            })

        # Rewards
        ra = intel.get("rewards_analysis", {})
        if ra.get("available") and ra.get("earned_label"):
            insights.append({
                "title":       "Rewards",
                "description": ra["earned_label"] + (
                    f". Total available: {ra.get('balance_label', '')}" if ra.get("balance_label") else ""
                ),
                "category": "rewards",
            })

        return insights

    def _ui_config(self, data: CreditCardData) -> Dict:
        return {
            "document_type": "credit_card",
            "hero_metrics": [
                {"key": "new_balance",      "label": "Current Balance",    "format": "currency", "is_hero": True},
                {"key": "minimum_payment",  "label": "Minimum Payment",    "format": "currency", "is_hero": True},
                {"key": "credit_limit",     "label": "Credit Limit",       "format": "currency", "is_hero": True},
                {"key": "utilization_pct",  "label": "Utilization",        "format": "percent",  "is_hero": True},
                {"key": "available_credit", "label": "Available Credit",   "format": "currency", "is_hero": False},
                {"key": "total_spend",      "label": "Total Charges",      "format": "currency", "is_hero": False},
                {"key": "interest_charged", "label": "Interest Charged",   "format": "currency", "is_hero": False},
                {"key": "rewards_earned",   "label": f"Rewards Earned ({data.rewards_type or 'pts'})", "format": "number", "is_hero": False},
                {"key": "days_until_due",   "label": "Days Until Due",     "format": "number",   "is_hero": False},
            ],
            "charts": [
                {
                    "id": "spend_by_category", "title": "Spend by Category",
                    "type": "donut", "data_path": "spend_by_category",
                    "label_key": "label", "value_key": "amount",
                },
                {
                    "id": "top_merchants", "title": "Top Merchants",
                    "type": "bar", "data_path": "top_merchants",
                    "x_key": "merchant", "y_key": "amount", "color": "#2196F3",
                },
                {
                    "id": "utilization_gauge", "title": "Credit Utilization",
                    "type": "progress",
                    "data_path": "utilization_analysis",
                    "value_key": "pct", "max_key": None,  # max is 100
                    "color": "#FF9800",
                },
            ],
            "tables": [
                {
                    "id": "transactions_table", "title": "Transactions",
                    "data_path": "transactions",
                    "columns": [
                        {"key": "trans_date",   "label": "Date"},
                        {"key": "description",  "label": "Description"},
                        {"key": "category",     "label": "Category"},
                        {"key": "amount",       "label": "Amount",   "format": "currency"},
                    ],
                },
                {
                    "id": "payment_options_table", "title": "Payment Options",
                    "data_path": "payment_options",
                    "columns": [
                        {"key": "label",       "label": "Option"},
                        {"key": "amount",      "label": "Amount",      "format": "currency"},
                        {"key": "description", "label": "Why"},
                    ],
                },
            ],
            "sections": [
                {"id": "overview",      "title": "Account Summary",     "type": "metrics",  "metrics": ["new_balance", "minimum_payment", "credit_limit", "utilization_pct"]},
                {"id": "spend",         "title": "Spending Breakdown",   "type": "charts",   "charts":  ["spend_by_category", "top_merchants"]},
                {"id": "transactions",  "title": "Transactions",         "type": "tables",   "tables":  ["transactions_table"]},
                {"id": "utilization",   "title": "Credit Health",        "type": "charts",   "charts":  ["utilization_gauge"]},
                {"id": "payment",       "title": "Payment Options",      "type": "tables",   "tables":  ["payment_options_table"]},
                {"id": "actions",       "title": "Action Plan",          "type": "actions",  "data_path": "action_plan"},
            ],
        }