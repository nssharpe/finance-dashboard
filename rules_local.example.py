"""Example personal ruleset. Copy to ``app/rules_local.py`` to use it.

``app/rules.py`` ships a generic starter set covering large national merchants.
Anything specific to you — your dentist, your kid's gym, the coffee shop on your
corner — belongs here instead, where it stays out of version control
(``app/rules_local.py`` is gitignored).

**Most of the time you don't need this file.** Use merchant rules in the UI:
recategorize a transaction, click the ⚡ button, and it applies to every matching
transaction past and future. Reach for this file only when you need something a
substring match can't express — alternations, word boundaries, lookaheads.

Rules here are PREPENDED to the generic set, so they win on first-match. They are
evaluated top to bottom and the first hit wins, so put specific patterns above
general ones. Every category must be one of ``CANONICAL_CATEGORIES`` in
``app/categorize.py``.
"""

LOCAL_CATEGORY_RULES: list[tuple[str, str]] = [
    # Word boundaries: match "DELI" the word, not the "deli" inside "FIDELITY".
    (r'\bDELI\b|CORNER DELI|MARIO\'?S SUBS', 'Restaurants etc.'),

    # Alternation for a merchant that appears under several descriptors.
    (r'RIVERSIDE GYMNASTICS|RIVERSIDE GYM CTR|RGC ?INVOICE', 'Kids'),

    # Lookaheads: a check whose memo names a rental property is reimbursed,
    # while every other condo-dues check is a real household expense. Ordering
    # matters — the specific rule has to come first.
    (r'CHECK \d(?=.*UNIT 4B)(?=.*CONDO)', 'Reimbursement'),
    (r'CHECK \d.*CONDO', 'Home'),

    # A local utility the generic ruleset can't know about.
    (r'GREENFIELD MUNICIPAL|GMLD ?BILL', 'Bills & Utilities'),
]
