# Contributing

Thanks for taking a look. This is a small, deliberately boring codebase — local
SQLite, a FastAPI backend, and vanilla JS with no build step.

## Never commit financial data

This is the one rule that really matters.

`.gitignore` already excludes `finance.db`, `config.json`, `accounts.local.json`,
`app/rules_local.py`, and every `.csv` / `.xlsx` / `.pdf` in the tree. Still:

- Run `git status` before every commit and read the list.
- **`config.json` holds a bearer credential.** Anyone with your SimpleFIN access
  URL can read your account data. If you ever commit it, rotate the token
  immediately — removing the file in a later commit does not help, the value is
  still in the history.
- Scrub real merchant names, amounts, and account numbers out of issue reports,
  test fixtures, and screenshots.
- Test fixtures under `tests/fixtures/` must be synthetic. Invent merchants.

## Setup

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
python -m app.main          # http://127.0.0.1:8765
```

Backend changes need a restart (`reload=False`). Frontend changes just need a
browser refresh.

## Tests

The suite deliberately concentrates on the things that break **silently** — the
failures you don't notice until your year-end numbers are wrong:

- **Rule precedence** — regex ruleset < merchant rule < manual override, and
  clean reversion when a rule is edited or deleted.
- **Sign conventions** — every institution disagrees about what a purchase looks
  like. Amex reports one as a positive number. Flip a sign and a year of
  spending silently becomes income.
- **De-duplication** — `db.synthetic_id` hashes `(date, account, description,
  amount)`, so a statement's genuinely repeated same-day charges collapse into
  one row unless the importer salts them by occurrence.

Please add a test alongside any parser or rule change.

## Adding a statement parser

1. Write `parse_<institution>(path) -> list[dict]` in `app/importers/statements.py`,
   returning `{posted, description, amount}` with **negative = money out**, plus
   `source_category` if the export has a category column.
2. Register it in the `PARSERS` dict.
3. Add a **synthetic** fixture under `tests/fixtures/` and a test asserting the
   sign of both a purchase and a payment. Getting this backwards is the single
   most common bug in this area.
4. Document the format in the README table.

Verify against real data with `--dry-run` before opening the PR, and say in the
description what match rate you saw against an overlapping window.

## Adding categorization rules

`app/rules.py` is a **generic starter set** — national chains and money-movement
patterns that look the same on everyone's statements. Please only add merchants
there that a stranger would also recognize.

Anything local to you belongs in `app/rules_local.py` (gitignored) or, better, as
a merchant rule in the UI.

Watch out for substrings: an unanchored `DELI` matches `FIDELITY`. Use `\b`
boundaries. `tests/test_rules.py` checks that every pattern compiles, that every
category is canonical, and that no pattern matches the empty string.

## Style

Match the surrounding code. Comments should explain *why*, especially where
something looks odd — most of the strange-looking code here is load-bearing and
has a real failure behind it.
