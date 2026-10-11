"""Whole-word keyword matching. Plain `kw in text` matched "fica" inside
"certificate" and "add" inside "address", misrouting questions."""
import re
from functools import lru_cache
from typing import Iterable


@lru_cache(maxsize=4096)
def _pattern(term: str) -> re.Pattern:
    t = term.strip().lower()
    # Allow simple inflections: payment(s), charge(d), spend(ing)
    # Short terms ("cv", "po", "w2") must match exactly — "cvs" is not "cv"s
    suffix = "(?:s|es|ed|d|ing)?" if len(t) >= 4 else ""
    return re.compile(rf"(?<![a-z0-9]){re.escape(t)}{suffix}(?![a-z0-9])")


def has_term(text: str, term: str) -> bool:
    return bool(term.strip()) and bool(_pattern(term).search(text.lower()))


def any_term(text: str, terms: Iterable[str]) -> bool:
    low = text.lower()
    return any(t.strip() and _pattern(t).search(low) for t in terms)
