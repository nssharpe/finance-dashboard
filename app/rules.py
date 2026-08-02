"""Ordered regex categorization rules (description -> canonical category).

This is a deliberately **generic starter set**: large national merchants and the
money-movement patterns that look the same on everyone's statements. It will not
categorize your local coffee shop, your dentist, or your kid's gym — nothing here
knows anything about you.

Two ways to teach it your own merchants, in increasing order of convenience:

1. **Merchant rules in the UI** (recommended) — recategorize and rename a
   transaction, click "Create rule from this", and it applies to every matching
   transaction, past and future. Stored in your local database, never in code.
   See :mod:`app.merchant_rules`.
2. **``app/rules_local.py``** — for regex-shaped patterns a substring match can't
   express (lookaheads, alternations, word boundaries). Define
   ``LOCAL_CATEGORY_RULES`` there; it is gitignored and gets PREPENDED to this
   list, so your rules win on first-match. See ``rules_local.example.py``.

Rules are evaluated top-to-bottom and the FIRST match wins, so order matters:
specific merchant/spending rules come before the broad money-movement rules.

Every category returned here must be one of ``CANONICAL_CATEGORIES`` in
:mod:`app.categorize`; ``tests/test_rules.py`` enforces that.
"""

CATEGORY_RULES: list[tuple[str, str]] = [
    # --- Wallet de-dup (HIGHEST priority) ---------------------------------
    # If you import PayPal/Venmo as their own accounts, those wallet rows hold
    # the real, itemized spend. The matching card-side charge ("PAYPAL *STEAM")
    # is only the funding leg and must NOT be counted again, so every card/bank
    # -side PayPal/Venmo line maps to Transfer (excluded from spending).
    #
    # If you DON'T import the wallets, delete these two lines — otherwise that
    # spending vanishes entirely instead of being counted once.
    (r'\bPAYPAL\b', 'Transfer'),
    (r'\bVENMO\b', 'Transfer'),

    # --- Spending categories ----------------------------------------------
    # Groceries
    (r'WHOLE ?FOODS|WHOLEFDS|TRADER JOE|SAFEWAY|KROGER|PUBLIX|ALDI|LIDL|COSTCO'
     r"|BJ'?S WHOLESALE|SAM'?S CLUB|STOP.&.SHOP|GIANT FOOD|HANNAFORD|WEGMANS"
     r'|SPROUTS|H-E-B|MEIJER|WINCO|FOOD LION|HARRIS TEETER|ALBERTSONS'
     r'|7-ELEVEN|DOLLAR GENERAL|DOLLAR TREE|GROCERY|SUPERMARKET', 'Groceries'),

    # Travel
    (r'JETBLUE|DELTA AIR|DELTA \d|DELTA\.COM|AMERICAN AIR|AMERICAN \d|UNITED \d'
     r'|SOUTHWEST AIR|SPIRIT AIRL|FRONTIER AIR|ALASKA AIR|AER LINGUS|RYANAIR'
     r'|LUFTHANSA|AIR CANADA|AIRLINE|AIRBNB|VRBO|EXPEDIA|BOOKING\.COM'
     r'|PRICELINE|KAYAK|HOTEL|MOTEL|MARRIOTT|HILTON|HYATT|RESIDENCE INN'
     r'|HAMPTON INN|HOLIDAY INN|BEST WESTERN|AVIS|HERTZ|ENTERPRISE RENT'
     r'|NATIONAL CAR|BUDGET RENT|E-ZPASS|ETOLL|\bTOLL\b|AMTRAK|GREYHOUND'
     r'|LYFT|UBER\b|CURB.*TAXI|\bTAXI\b|TSA PRECHECK|GLOBAL ENTRY', 'Travel'),

    # Auto & Transport (fuel, parking, service)
    (r'SHELL |SUNOCO|EXXON|MOBIL\b|BP#|CITGO|GULF |CHEVRON|TEXACO|VALERO'
     r"|CIRCLE K|SPEEDWAY|WAWA|QUIKTRIP|LOVE'S|PILOT |FLYING J|RACETRAC"
     r'|JIFFY LUBE|VALVOLINE|MIDAS|FIRESTONE|DISCOUNT TIRE|AUTOZONE'
     r"|O'?REILLY AUTO|ADVANCE AUTO|NAPA AUTO|PARKING|PARKWHIZ|SPOTHERO"
     r'|\bDMV\b|CHARGEPOINT|ELECTRIFY AMERICA|SUPERCHARGER', 'Auto & Transport'),

    # Restaurants
    (r'STARBUCKS|DUNKIN|MCDONALD|BURGER KING|WENDY|CHICK-?FIL-?A|SUBWAY\b'
     r'|CHIPOTLE|QDOBA|PANERA|SHAKE SHACK|FIVE GUYS|IN-N-OUT|POPEYES|KFC\b'
     r'|TACO BELL|DOMINO|PIZZA HUT|PAPA JOHN|LITTLE CAESAR|DAIRY QUEEN'
     r'|BASKIN|BEN & JERRY|DOORDASH|GRUBHUB|UBER ?EATS|SEAMLESS|POSTMATES'
     r'|CAVIAR|TST\*|SQ \*|TOAST TAB|OPENTABLE|RESY'
     r'|RESTAURANT|PIZZERIA|PIZZA|BAKERY|CAFE|CAFFE|COFFEE|BAGEL|\bDELI\b'
     r'|\bGRILL\b|\bPUB\b|TAVERN|BREWERY|BREWING|DOUGHNUT|DONUT'
     r'|CREAMERY|ICE CR|FROZEN YOGURT|SUSHI|RAMEN|TACO|BURGER|SANDWICH'
     r'|STEAKHOUSE|BISTRO|DINER|EATERY', 'Restaurants etc.'),

    # Entertainment
    (r'NETFLIX|DISNEY|HULU|MAX\.COM|HBOMAX|PARAMOUNT\+|PEACOCK|APPLE TV'
     r'|SPOTIFY|PANDORA|YOUTUBEPREMIUM|AUDIBLE|TIDAL|SIRIUSXM'
     r'|STEAM|VALVE|NINTENDO|PLAYSTATION|XBOX|EPIC GAMES|HUMBLE ?BUNDLE?'
     r'|TWITCH|PATREON|KICKSTARTER|AMC |REGAL CINEMA|CINEMARK|CINEMA'
     r'|FANDANGO|TICKETMASTER|STUBHUB|EVENTBRITE|LIVE NATION'
     r'|MUSEUM|AQUARIUM|\bZOO\b|THEATER|THEATRE|BOWLING|ARCADE', 'Entertainment'),

    # Health & Fitness
    (r'\bCVS\b|WALGREENS|RITE AID|PHARMACY|\bDENTAL\b|DENTIST|ORTHODONT'
     r'|OPTOMETR|\bMEDICAL\b|\bCLINIC\b|HOSPITAL|HEALTHCARE|PHYSICAL THERAPY'
     r'|URGENT CARE|LABCORP|QUEST DIAGNOSTIC|ONE ?MEDICAL|TELADOC|GOODRX'
     r'|PLANET FITNESS|LA FITNESS|EQUINOX|ORANGETHEORY|CROSSFIT|\bYMCA\b'
     r'|PELOTON|CLASSPASS|\bGYM\b|FITNESS', 'Health & Fitness'),

    # Shopping
    (r'AMAZON|AMZN|TARGET|WAL-?MART|BEST BUY|\bIKEA\b|WAYFAIR'
     r'|HOME DEPOT|LOWES|ACE HARDWARE|MICHAELS|JOANN|HOBBY LOBBY|STAPLES'
     r'|OFFICE DEPOT|\bETSY\b|EBAY|ALIEXPRESS|TEMU|SHEIN'
     r'|\bREI\b|DICK.*SPORTING|ACADEMY SPORTS|BASS PRO|PATAGONIA'
     r'|NORDSTROM|MACY|KOHL|OLD NAVY|UNIQLO|ZARA'
     r'|SEPHORA|ULTA|BATH & BODY|TJ ?MAXX|MARSHALLS|HOMEGOODS'
     r'|APPLE STORE|GOOGLE ?STORE', 'Shopping'),

    # Bills & Utilities
    (r'COMCAST|XFINITY|SPECTRUM|VERIZON|AT&T|T-MOBILE|SPRINT'
     r'|CENTURYLINK|GOOGLE FI|MINT MOBILE|VISIBLE'
     r'|NATIONAL GRID|EVERSOURCE|CON ?EDISON|DUKE ENERGY|PG&E'
     r'|DOMINION ENERGY|XCEL ENERGY|WASTE MANAGEMENT'
     r'|\bINSURANCE\b|GEICO|PROGRESSIVE|STATE FARM|ALLSTATE'
     r'|DROPBOX|GITHUB|GOOGLE.*STORAGE|ICLOUD|MICROSOFT ?365|ADOBE'
     r'|OPENAI|ANTHROPIC|CLAUDE\.AI|NOTION|1PASSWORD|LASTPASS', 'Bills & Utilities'),

    # Home
    (r'\bHOA\b|CONDO|MORTGAGE|RENT PAYMENT|PROPERTY MGMT'
     r'|\bPLUMBING\b|ELECTRICIAN|\bHVAC\b|\bLAWN\b|LANDSCAP|PEST CONTROL'
     r'|CLEANING SERVICE|HANDYMAN', 'Home'),

    # Kids
    (r'GIRL SCOUTS|BOY SCOUTS|\bPTA\b|\bPTO\b|DAYCARE|PRESCHOOL|MONTESSORI'
     r'|SUMMER CAMP|\bTUITION\b|SCHOLASTIC|KUMON|MATHNASIUM|GYMNASTICS'
     r'|LITTLE LEAGUE|BUILD-A-BEAR|CHUCK E CHEESE', 'Kids'),

    # Pets
    (r'VETERINAR|\bVET\b|ANIMAL HOSPITAL|PETCO|PETSMART|CHEWY|\bKENNEL\b'
     r'|DOG ?WALK', 'Pets'),

    # Gifts & Donations
    (r'GOFUNDME|ACTBLUE|DONORSCHOOSE|\bDONATION\b|RED CROSS|UNITED WAY'
     r'|WIKIMEDIA|\bNPR\b|GIVEWELL|EVERY\.ORG|CHARITABLE|\bCHARITY\b'
     r'|SALVATION ARMY|GOODWILL', 'Gifts & Donations'),

    # --- Money movement (generic across every institution) -----------------
    # Taxes
    (r'\bIRS\b|USATAXPYMT|STATE TAX|FEDERAL TAX|DEPT OF REVENUE'
     r'|TAX PYMT|TAX REFUND|PROPERTY TAX|FRANCHISE TAX|TURBOTAX|H&R BLOCK', 'Taxes'),

    # Income
    (r'PAYROLL|DIRECT DEP|PAYCHECK|\bGUSTO\b|PAYCHEX|DIVIDEND'
     r'|DIV.*CAP GAIN|INTEREST (INCOME|EARNED|PAID)|RENTAL INCOME|\bBONUS\b'
     r'|UNEMPLOYMENT|SOCIAL SECURITY|SSA TREAS', 'Income'),

    # Reimbursement
    (r'REIMBURS|EXPENSE REPORT|CONCUR|EXPENSIFY', 'Reimbursement'),

    # Transfer (card payments, P2P, account transfers, ATM, checks)
    # NOTE: PayPal/Venmo are handled by the wallet de-dup rules at the top.
    (r'PAYMENT.*THANK|AUTOPAY|\bZELLE\b|CASH ?APP|APPLE CASH|GOOGLE PAY'
     r'|WIRE TYPE|\bTRANSFER\b|INST XFER|\bXFER\b|CRCARDPMT|CARD PMT'
     r'|ONLINE PAYMENT|MOBILE PAYMENT|BILL PAYMENT|E-?PAYMENT|ACH PMT'
     r'|ATM (WITHDRAW|DEPOSIT)|CASH WITHDRAWAL|CHECK \d|DES:ACH|DES:CRCARD'
     r'|DES:ECHECKPAY|MONEYLINE|OVERDRAFT PROTECTION', 'Transfer'),

    # Financial (rewards, brokerage, money handling)
    (r'CASHREWARD|STATEMENT CREDIT|CASH.*REDEMPTION|REDEEMED|YOUR CASH REWARD'
     r'|SECURITIES|BROKERAGE|MUTUAL FUND|REINVEST|TRADE COMMISSION|ADVISORY FEE'
     r'|RETIREMENT CONTRIB|\bVANGUARD\b|\bFIDELITY\b|SCHWAB|E\*?TRADE'
     r'|ROBINHOOD|BETTERMENT|WEALTHFRONT|COINBASE', 'Financial'),

    # Fees & Charges
    (r'FOREIGN TRANSACTION FEE|\bATM FEE\b|OVERDRAFT FEE|LATE FEE|ANNUAL FEE'
     r'|SERVICE CHARGE|MAINTENANCE FEE|WIRE FEE|RETURNED ITEM', 'Fees & Charges'),
]


# ---------------------------------------------------------------------------
# Optional personal ruleset.
#
# ``app/rules_local.py`` is gitignored. If it exists, its rules are PREPENDED so
# they beat everything above on first-match. Copy ``rules_local.example.py`` to
# get started. The app runs fine without it.
# ---------------------------------------------------------------------------
try:
    from app.rules_local import LOCAL_CATEGORY_RULES
except ImportError:  # no personal ruleset — generic rules only
    LOCAL_CATEGORY_RULES = []

CATEGORY_RULES = list(LOCAL_CATEGORY_RULES) + CATEGORY_RULES
