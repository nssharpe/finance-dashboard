"""Guardrails on the regex ruleset itself.

These catch the two ways a contributor can silently break categorization: an
invalid regex (crashes at import) and a category name that isn't canonical
(silently creates a phantom bucket the analysis view ignores).
"""

import re

import pytest

from app.categorize import CANONICAL_CATEGORIES, categorize
from app.rules import CATEGORY_RULES


def test_every_pattern_compiles():
    for pattern, _ in CATEGORY_RULES:
        re.compile(pattern, re.IGNORECASE)


def test_every_category_is_canonical():
    bad = {cat for _, cat in CATEGORY_RULES if cat not in CANONICAL_CATEGORIES}
    assert not bad, f"non-canonical categories in rules.py: {sorted(bad)}"


def test_no_pattern_matches_the_empty_string():
    """A pattern that matches '' would swallow every uncategorized row."""
    greedy = [p for p, _ in CATEGORY_RULES if re.search(p, "", re.IGNORECASE)]
    assert not greedy, f"patterns matching the empty string: {greedy}"


@pytest.mark.parametrize("description,expected", [
    ("WHOLE FOODS MKT 10259", "Groceries"),
    ("TRADER JOE'S #512", "Groceries"),
    ("STARBUCKS STORE 08214", "Restaurants etc."),
    ("NETFLIX.COM LOS GATOS CA", "Entertainment"),
    ("AMAZON MKTPL*5N8TO2V61", "Shopping"),
    ("DELTA 00623966561584 DELTA.COM CA", "Travel"),
    ("SHELL SERVICE STATION", "Auto & Transport"),
    ("WALGREENS #4517", "Health & Fitness"),
    ("AUTOMATIC PAYMENT - THANK YOU", "Transfer"),
    ("MASS INST DES:PAYROLL ID:659", "Income"),
    ("FOREIGN TRANSACTION FEE", "Fees & Charges"),
])
def test_generic_merchants_categorize(description, expected):
    assert categorize(description) == expected


def test_unknown_merchant_falls_through_to_uncategorized():
    assert categorize("ZZQQ LOCAL SHOP 4471") == "Uncategorized"


def test_wallet_dedup_outranks_merchant_rules():
    """A PayPal-funded charge must be Transfer, not the merchant's category —
    otherwise it is counted twice (once on the card, once in the wallet)."""
    assert categorize("PAYPAL *STEAM GAMES 4029357733") == "Transfer"
    assert categorize("VENMO DES:PAYMENT ID:123") == "Transfer"


def test_source_category_is_preferred_when_recognized():
    assert categorize("ZZQQ UNKNOWN", "Groceries") == "Groceries"


def test_unrecognized_source_category_falls_back_to_rules():
    assert categorize("STARBUCKS STORE 1", "Blah Nonsense") == "Restaurants etc."
