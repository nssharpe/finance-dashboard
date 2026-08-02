# Merchant rules + open-sourcing — design

**Date:** 2026-08-02
**Status:** implemented

Two requests that turned out to solve each other: a way to recategorize and
rename every transaction sharing a label, and preparing the project to be
published without leaking the owner's financial life.

The connection: the existing regex ruleset in `app/rules.py` was 467 patterns
tuned to one household — the kids' gym, the family dentist, named individuals,
condo unit numbers distinguishing a rental from a residence. Publishing it meant
publishing a profile. Merchant rules move that knowledge out of code and into the
local database, which is exactly what makes the shipped ruleset publishable.

## Merchant rules

### Matching

Case-insensitive **substring** against `raw_description`. The create dialog
prefills the entire raw label, so leaving it untouched behaves like an exact
match; shortening it broadens the rule.

Rejected alternatives:
- *Exact label only* — a store-number change creates a new uncaught label.
- *Full regex* — too easy to silently recategorize hundreds of unrelated rows.

Ordering: `priority` ascending, then **longest pattern first**. The longest-
pattern tiebreak means a specific rule beats a general one without anyone
hand-managing priority numbers.

### Precedence

Lowest to highest:

1. `app/rules.py` / `app/rules_local.py` regex ruleset → writes `base_category`
2. `merchant_rules` → overwrite description and/or category
3. Per-transaction manual overrides → always win

A hand-fix on one row still beats a rule, which still beats the regex ruleset.

### Why `raw_description` and `base_category` exist

Two new columns on `transactions`, holding the untouched import-time values.
They exist so a rule can be **edited or deleted and cleanly reverted**.

The alternative — recomputing the category from scratch on revert — is wrong.
`statements.py` resolves categories rules-first-then-source-fallback, while
`categorize()` prefers the source category. A naive recompute would silently
change existing rows. Storing the original sidesteps the question entirely.

`apply_all()` therefore resets every row to its original before matching, and
callers re-apply the manual overrides afterwards.

### Surface

- `GET/POST/PATCH/DELETE /api/merchant-rules`, plus `POST /api/merchant-rules/preview`
- A ⚡ affordance on each transaction row, revealed on hover/focus
- A dialog with a live "matches N transactions" counter, warning above 200
- A **Merchant Rules** section in the Insights tab
- `merchant_rules.apply_all()` runs on every SimpleFIN refresh and CSV import, so
  saved rules apply to newly-arrived transactions automatically

## Open-sourcing

### Ruleset split

- `app/rules_local.py` — gitignored. The entire tuned personal ruleset, moved
  verbatim. Prepended to the generic set, so it wins on first-match.
- `app/rules.py` — shipped. A fresh generic starter set: national chains, common
  utilities, and the money-movement patterns that look the same on everyone's
  statements.

Moving the whole ruleset rather than hand-stripping "the identifying parts"
avoids a long series of judgment calls about whether, say, a neighborhood PTO is
identifying. Verified afterwards that no existing categorization changed — every
difference was `Uncategorized → something`, i.e. the generic set only adds
coverage.

### Account registry

`accounts.local.json` (gitignored) holds SimpleFIN account ids, account names,
aliases, and export filenames. `statements.py` and `bofa_checking.py` both read
it; parsers stay in code and are referenced by name. `accounts.example.json`
ships with placeholders.

The registry is shared, so `statements.py` skips entries whose parser belongs to
another importer while still rejecting genuinely unknown parser names — a typo
must not silently drop an account.

### Repo hygiene

MIT license; rewritten de-branded README; CONTRIBUTING focused on never
committing financial data; hardened `.gitignore`; `tests/` covering rule
precedence, sign conventions, and de-duplication; two CI workflows — the test
matrix, and a `no-secrets` job that fails the build if a data file or a SimpleFIN
access URL is ever committed.

## Incidental fix

`db.get_conn()` now passes `check_same_thread=False`. FastAPI runs sync endpoints
in a threadpool and closes the `get_db` generator on a *different* worker thread
than the one that opened the connection, raising `sqlite3.ProgrammingError`
during teardown. This was a pre-existing latent bug that surfaced intermittently
on any endpoint; each request has its own connection, so the flag is safe.
