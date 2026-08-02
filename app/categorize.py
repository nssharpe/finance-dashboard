"""Canonical taxonomy + categorization engine.

Two entry points:

* ``categorize(description, source_category=None)`` - the single function used by
  both the SimpleFIN refresh and the historical importer. If a usable source
  category is supplied (Mint/Empower vocabularies) it is mapped via
  ``SOURCE_CATEGORY_MAP``; otherwise the regex rules in :mod:`app.rules` run
  against the description. Falls back to ``Uncategorized``.

The canonical taxonomy is the set of categories the 2025 workbook converged on.
``EXCLUDED_FROM_SPENDING`` are internal money movements that the analysis view
omits to avoid double-counting card spend against card payments, etc.
"""

import re

from .rules import CATEGORY_RULES

CANONICAL_CATEGORIES = [
    "Home",
    "Kids",
    "Travel",
    "Groceries",
    "Restaurants etc.",
    "Health & Fitness",
    "Bills & Utilities",
    "Shopping",
    "Pets",
    "Gifts & Donations",
    "Auto & Transport",
    "Entertainment",
    "Business Services/Shipping",
    "Education",
    "Fees & Charges",
    "Personal Care",
    "Taxes",
    "Financial",
    "Transfer",
    "Income",
    "Reimbursement",
    "Uncategorized",
]

# Internal money movement — excluded from spending totals in the Analysis view.
EXCLUDED_FROM_SPENDING = {"Transfer", "Income", "Reimbursement", "Financial"}

# Pre-compile rules once.
_COMPILED_RULES = [(re.compile(pat, re.IGNORECASE), cat) for pat, cat in CATEGORY_RULES]

# ---------------------------------------------------------------------------
# Source-category map: Mint (2015-2023) and Empower (2024) vocabularies ->
# canonical taxonomy. Keys are matched case-insensitively (see _norm).
# ---------------------------------------------------------------------------
_RAW_SOURCE_MAP = {
    # ---- Mint categories ----
    "Investments": "Financial",
    "Trade Commissions": "Financial",
    "Buy": "Financial",
    "Gymnastics Expenses": "Kids",
    "Gymnastics": "Kids",
    "Kids Activities": "Kids",
    "Kids Clothing": "Kids",
    "Summer Camp": "Kids",
    "After School Program": "Kids",
    "Preschool": "Kids",
    "Babysitter & Daycare": "Kids",
    "Toys": "Kids",
    "Sports": "Kids",
    "Credit Card Payment": "Transfer",
    "Transfer": "Transfer",
    "InterAccount Transf.": "Transfer",
    "Transfer for Cash Spending": "Transfer",
    "Cash & ATM": "Transfer",
    "Deposit": "Transfer",
    "Shopping": "Shopping",
    "Clothing": "Shopping",
    "Sporting Goods": "Shopping",
    "Electronics & Software": "Shopping",
    "Books": "Shopping",
    "Bike": "Shopping",
    "Furnishings": "Home",
    "Restaurants": "Restaurants etc.",
    "Fast Food": "Restaurants etc.",
    "Coffee Shops": "Restaurants etc.",
    "Food & Dining": "Restaurants etc.",
    "Alcohol & Bars": "Restaurants etc.",
    "Ice Cream": "Restaurants etc.",
    "Food Delivery": "Restaurants etc.",
    "Groceries": "Groceries",
    "Income": "Income",
    "Paycheck": "Income",
    "Interest Income": "Income",
    "Dividend & Cap Gains": "Income",
    "Bonus": "Income",
    "Rental Income": "Income",
    "Utilities": "Bills & Utilities",
    "Internet": "Bills & Utilities",
    "Mobile Phone": "Bills & Utilities",
    "Home Phone": "Bills & Utilities",
    "Television": "Entertainment",
    "Cable/Satellite": "Bills & Utilities",
    "Newspapers & Magazines": "Bills & Utilities",
    "Life Insurance": "Bills & Utilities",
    "Home Insurance": "Bills & Utilities",
    "Home Improvement": "Home",
    "Home Supplies": "Home",
    "Lawn & Garden": "Home",
    "Condo Fees": "Home",
    "Mortgage & Rent": "Home",
    "Gas & Fuel": "Auto & Transport",
    "Parking": "Auto & Transport",
    "Auto Payment": "Auto & Transport",
    "Charity": "Gifts & Donations",
    "Gift": "Gifts & Donations",
    "Gifts & Donations": "Gifts & Donations",
    "Pharmacy": "Health & Fitness",
    "Doctor": "Health & Fitness",
    "Gym": "Health & Fitness",
    "Eyecare": "Health & Fitness",
    "Physical Therapy": "Health & Fitness",
    "Air Travel": "Travel",
    "Rental Car & Taxi": "Travel",
    "Public Transportation": "Travel",
    "Hotel": "Travel",
    "Travel": "Travel",
    "Vacation": "Travel",
    "Ride Share": "Travel",
    "Movies & DVDs": "Entertainment",
    "Entertainment": "Entertainment",
    "Amusement": "Entertainment",
    "Arts": "Entertainment",
    "Music": "Entertainment",
    "Podcasts": "Entertainment",
    "Hobbies": "Entertainment",
    "Shipping": "Business Services/Shipping",
    "Business Services": "Business Services/Shipping",
    "Office Supplies": "Business Services/Shipping",
    "Bills & Utilities": "Bills & Utilities",
    "Taxes": "Taxes",
    "Property Tax": "Taxes",
    "State Tax": "Taxes",
    "Federal Tax": "Taxes",
    "Service Fee": "Fees & Charges",
    "Bank Fee": "Fees & Charges",
    "ATM Fee": "Fees & Charges",
    "Late Fee": "Fees & Charges",
    "Personal Care": "Personal Care",
    "Laundry": "Personal Care",
    "Pet Care/Boarding": "Pets",
    "Pet Food & Supplies": "Pets",
    "Veterinary": "Pets",
    "Dentist": "Health & Fitness",
    "COVID": "Health & Fitness",
    "Computer/Video Games": "Entertainment",
    "Condo Expenses": "Home",
    "Home Services": "Home",
    "School": "Education",
    "Tuition": "Education",
    "Misc Expenses": "Uncategorized",
    "Returned Purchase": "Shopping",
    "New Checks": "Fees & Charges",
    "Loan Interest": "Fees & Charges",
    "Other Reimbursable": "Reimbursement",
    "Reimbursement": "Reimbursement",
    "Work Expenses": "Reimbursement",
    "Financial": "Financial",
    "Education": "Education",
    "Uncategorized": "Uncategorized",
    "Hide from Budgets & Trends": "Uncategorized",
    "Health & Fitness": "Health & Fitness",
    "Home": "Home",
    "Kids": "Kids",
    # ---- Empower categories ----
    "Securities Trades": "Financial",
    "Investment Income": "Income",
    "Retirement Contributions": "Financial",
    "Rewards": "Financial",
    "Transfers": "Transfer",
    "Credit Card Payments": "Transfer",
    "Deposits": "Transfer",
    "Checks": "Transfer",
    "ATM/Cash": "Transfer",
    "Paychecks/Salary": "Income",
    "Other Income": "Income",
    "Interest": "Income",
    "Income - Trust": "Income",
    "General Merchandise": "Shopping",
    "Clothing/Shoes": "Shopping",
    "Electronics": "Shopping",
    "Bicycle": "Shopping",
    "Kids - Activities": "Kids",
    "Advisory Fee": "Fees & Charges",
    "Service Charges/Fees": "Fees & Charges",
    "Healthcare/Medical": "Health & Fitness",
    "Gasoline/Fuel": "Auto & Transport",
    "Automotive": "Auto & Transport",
    "Refunds & Reimbursements": "Reimbursement",
    "Expense Reimbursement": "Reimbursement",
    "Online Services": "Bills & Utilities",
    "Telephone": "Bills & Utilities",
    "Insurance": "Bills & Utilities",
    "Dues & Subscriptions": "Bills & Utilities",
    "Charitable Giving": "Gifts & Donations",
    "Gifts": "Gifts & Donations",
    "Postage & Shipping": "Business Services/Shipping",
    "Printing": "Business Services/Shipping",
    "Business Miscellaneous": "Business Services/Shipping",
    "Home - Condo Dues": "Home",
    "Other Expenses": "Uncategorized",
    # ---- 2025 consolidated already-canonical labels ----
    "Restaurants etc.": "Restaurants etc.",
    "Auto & Transport": "Auto & Transport",
    "Business Services/Shipping": "Business Services/Shipping",
    "Fees & Charges": "Fees & Charges",
    "Reimbursement/Reimbursed Expense": "Reimbursement",

    # ---- 2026 card-statement vocabularies -------------------------------
    # Chase, Capital One and Amex each ship their own Category column in the
    # downloaded CSV/XLSX. These are used only as a FALLBACK when the regex
    # rules find nothing (see app/importers/statements.py), so a coarse label
    # like "Merchandise" never overrides a tuned merchant rule.
    # Chase
    "Food & Drink": "Restaurants etc.",
    "Gas": "Auto & Transport",
    "Health & Wellness": "Health & Fitness",
    "Fees & Adjustments": "Fees & Charges",
    "Personal": "Shopping",
    "Automotive": "Auto & Transport",
    "Professional Services": "Business Services/Shipping",
    # Capital One
    "Dining": "Restaurants etc.",
    "Merchandise": "Shopping",
    "Phone/Cable": "Bills & Utilities",
    "Gas/Automotive": "Auto & Transport",
    "Payment/Credit": "Transfer",
    "Lodging": "Travel",
    "Health Care": "Health & Fitness",
    "Other Services": "Business Services/Shipping",
    "Fee": "Fees & Charges",
    # Amex ("Major-Minor" form)
    "Merchandise & Supplies-Groceries": "Groceries",
    "Merchandise & Supplies-General Retail": "Shopping",
    "Merchandise & Supplies-Mail Order": "Shopping",
    "Merchandise & Supplies-Wholesale Stores": "Groceries",
    "Transportation-Fuel": "Auto & Transport",
    "Transportation-Parking Charges": "Auto & Transport",
    "Business Services-Office Supplies": "Business Services/Shipping",
    "Business Services-Other Services": "Business Services/Shipping",
    "Business Services-Mailing & Shipping": "Business Services/Shipping",
    "Fees & Adjustments-Fees & Adjustments": "Fees & Charges",
    "Restaurant-Restaurant": "Restaurants etc.",
    "Restaurant-Bar & Café": "Restaurants etc.",
    "Travel-Airline": "Travel",
    "Travel-Lodging": "Travel",
    "Entertainment-General Attractions": "Entertainment",
}

# Normalize keys for case-insensitive lookup.
SOURCE_CATEGORY_MAP = {k.strip().lower(): v for k, v in _RAW_SOURCE_MAP.items()}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def categorize_description(description: str) -> str:
    """Run the regex ruleset against a description. Returns a canonical category."""
    text = description or ""
    for rule, category in _COMPILED_RULES:
        if rule.search(text):
            return category
    return "Uncategorized"


def map_source_category(source_category: str) -> str | None:
    """Map a raw Mint/Empower category to canonical, or None if unknown."""
    if not source_category:
        return None
    return SOURCE_CATEGORY_MAP.get(_norm(source_category))


def categorize(description: str, source_category: str | None = None) -> str:
    """Canonical category for a transaction.

    Prefers an explicit source category (Mint/Empower) when we recognize it,
    otherwise falls back to description-based regex rules.
    """
    mapped = map_source_category(source_category)
    if mapped:
        return mapped
    return categorize_description(description)
