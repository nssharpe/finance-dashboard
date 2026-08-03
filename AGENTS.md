# AGENTS.md

Briefing for coding agents working in this repo. Humans should read it too — the
"silent failures" section is the accumulated list of things that have actually
gone wrong here.

Read this before editing. Most of the odd-looking code is load-bearing.

---

## 0. Hard rule: the financial data never leaves the machine

This repo sits next to a real person's complete financial history.

Reading it locally is fine and often the point — analyzing spending is what the
tool is *for*. The rule is about **what escapes**: nothing derived from real data
may end up in the repo, a commit, an issue, a PR, a doc, a test fixture, or any
message sent off the machine.

**Never commit, publish, or quote outside the local session:**

- `finance.db` and any `finance.db.bak-*` — every transaction they have ever had
- `config.json` — contains a **SimpleFIN access URL, which is a bearer
  credential**. Anyone holding it can read their bank data. Never print it, never
  paste it into a commit, an issue, or a log line. This one is off-limits even
  locally — there is no reason to display a credential.
- `accounts.local.json` — account identifiers and card suffixes
- `app/rules_local.py` — a personal ruleset that doubles as a map of someone's
  life (their doctor, their kid's school, their neighborhood)
- Anything under a year directory (`2025/`, `2026/`, …) or any `.csv` / `.xlsx` /
  `.pdf` — raw bank statements

All of the above are gitignored. **Do not add exceptions, and do not `git add -f`
them.** If you need example data, invent it.

When writing tests, fixtures, docs, commit messages, or issue text: invent
merchants and amounts. Never copy a real transaction description out of the
database — real descriptions contain names, cities, and partial account numbers.

Before any commit, run `git status` and actually read the output. A `no-secrets`
CI workflow is a backstop, not a substitute.

---

## 1. Run it

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q          # 50 tests, ~0.5s
python -m app.main                  # http://127.0.0.1:8765
```

Tests must pass in a **fresh clone** — with no `finance.db`, no
`accounts.local.json`, and no `app/rules_local.py`. That is what CI runs. If a
test only passes on the author's machine, it is wrong.

Backend changes need a server restart (`reload=False`). Frontend changes just
need a browser refresh.

---

## 2. Layout

```
app/
  main.py            FastAPI app; every HTTP endpoint
  db.py              schema, idempotent migrations, upserts
  categorize.py      canonical categories + source-category mapping
  rules.py           generic regex ruleset  (rules_local.py = personal, gitignored)
  merchant_rules.py  user-created rename/recategorize rules
  simplefin.py       SimpleFIN client
  importers/         statement CSV/XLSX/PDF, xlsx history, wallet CSV
  static/            vanilla JS, no build step
tests/
docs/superpowers/specs/   design docs
```

---

## 3. Silent failures — the things that matter

These do not raise. They produce a number that is wrong, and nobody notices until
the year-end totals don't add up. Every one of these has actually happened.

### Sign conventions
The database stores **negative = money out** for every account. The exports
disagree: some arrive pre-signed, some split `Debit`/`Credit` into two columns,
and **at least one reports a purchase as a positive number** (see
`parse_amex_xlsx`, which negates). Flip one and a year of spending silently
becomes income. Always assert the sign of *both* a purchase and a payment in
tests, and verify against existing data before trusting a new format.

### The de-duplication salt
`db.synthetic_id()` hashes only `(date, account, description, amount)`. A
statement legitimately repeats the same charge on the same day — three identical
$30 transfers is a real thing. Without an occurrence-index salt those collapse
into one row and the money silently disappears. `statements.insert()` sorts rows
first, then salts repeats, so ids don't depend on file read order (a refreshed
"pending transactions" export reorders rows). **Any new statement importer needs
the same guard.**

### Categorization precedence
Lowest to highest:

1. `rules.py` / `rules_local.py` regex ruleset → writes `base_category`
2. `merchant_rules` → overwrite description and/or category
3. per-transaction manual overrides → always win

`merchant_rules.apply_all()` resets every row to `raw_description` /
`base_category` before matching, which is what makes editing or deleting a rule
revert cleanly. **Do not "simplify" this by recomputing the category from
scratch**: `statements.py` resolves rules-first-then-source-fallback while
`categorize()` prefers the source category, so a recompute silently changes
existing rows. The originals are stored precisely to avoid that question.

Callers must re-apply `db.apply_overrides()` and
`db.apply_description_overrides()` after `apply_all()`, or manual fixes get lost.

### Unanchored regex alternatives
An unanchored `DELI` matches `FIDELITY`, which silently filed dividend income
under restaurants. Use `\b` boundaries. `tests/test_rules.py` checks that every
pattern compiles, that every category is canonical, and that none matches the
empty string — but it can't catch a pattern that merely matches too much.

### Wallet de-dup cuts both ways
`PAYPAL` and `VENMO` map to `Transfer` at the very top of the ruleset, because
the wallet import holds the real itemized spend and the card-side charge is only
the funding leg. **If the wallet is not imported, that spending vanishes
entirely** rather than being counted once. Check both sides before assuming a
card-side Transfer is correct.

### The `Uncategorized` bucket is counted as spending
`EXCLUDED_FROM_SPENDING` is `{Transfer, Income, Reimbursement, Financial}`.
`Uncategorized` is *not* in it, so a mis-parsed card payment landing there shows
up as a five-figure expense. Sanity-check large `Uncategorized` rows after any
import.

---

## 4. Gotchas that waste time

- `app/static/app.js` sets `const API = ''`, so **every fetch path must include
  the `/api` prefix explicitly**.
- The money formatter is `fmtCurrency`, not `fmtMoney`.
- `db.get_conn()` passes `check_same_thread=False` on purpose. FastAPI runs sync
  endpoints in a threadpool and closes the `get_db` generator on a *different*
  worker thread; without the flag, teardown raises `ProgrammingError` and the
  request 500s after the response. Each request has its own connection, so it's
  safe. Don't remove it.
- `fastapi.testclient.TestClient(app)` does **not** run startup events unless
  used as a context manager (`with TestClient(app) as c:`). Without it the schema
  is never created and you get `no such table: meta`.
- `db.init_db()` migrations must stay idempotent — they run on every boot.
- Frontend is vanilla JS with no build step and no bundler. Don't introduce one
  without asking.

---

## 5. Conventions

- **Contrast is a hard requirement, not polish.** Never place text on a
  same-or-similar background. Resolve CSS variables to their real values and
  check the foreground/background pair before finishing; aim for WCAG AA (≥4.5:1
  body text, ≥3:1 large text and UI). Watch hover, disabled, and dark-mode
  states.
- Comments explain **why**, especially where something looks strange. If you
  remove a guard, first find the failure it was protecting against.
- Match the surrounding style. No type annotations in JS, no build tooling.
- Add a test with any parser or rule change. Verify importers with `--dry-run`
  against real data and report the overlap match rate.

---

## 6. Adding a statement parser

1. Write `parse_<shape>(path) -> list[dict]` in `app/importers/statements.py`
   returning `{posted, description, amount}` with **negative = money out**, plus
   `source_category` when the export has a category column.
2. Register it in the `PARSERS` dict.
3. Add a **synthetic** fixture under `tests/fixtures/` and assert the sign of
   both a purchase and a payment.
4. Add the shape to the README table.

Verify with `--dry-run` first. Where the statement overlaps data already in the
database, a near-100% match rate is the evidence the parser and its sign are
right. A low rate means the parser is wrong, not that the data is missing.
