# Finance Dashboard

A personal finance dashboard that runs **entirely on your own machine**. No cloud
account, no subscription, no telemetry. Your transactions live in a SQLite file
next to the code, and the only network calls are the ones you make directly to
your own bank aggregator.

Built to replace the annual "export everything to a spreadsheet and squint at it"
ritual with something that stays current on its own.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What it does

- **Pulls balances and transactions** from every account you connect, via
  [SimpleFIN](https://beta-bridge.simplefin.org/) (~$1.50/month, works with most
  US banks).
- **Categorizes automatically** with deterministic regex rules — no LLM, no
  guessing, no "we've re-categorized 400 of your transactions" surprises.
- **Merchant rules**: fix one transaction, and every matching transaction — past
  and future — follows. Renames and recategorizes in one step.
- **Net worth over time**, per-section and per-account, reconstructed backwards
  from transactions for cash and credit accounts.
- **Year-over-year spending analysis** by category.
- **Insights**: recurring-subscription detection, an anomaly digest (category
  spikes, new merchants, large charges), and top merchants.
- **Statement importers** for when your aggregator's history doesn't reach far
  enough back — CSV, XLSX and PDF exports from several major banks.

## Privacy

This matters more than the feature list, so it's worth being precise:

- Everything is stored in `finance.db`, a local SQLite file. Nothing is uploaded.
- The server binds to `127.0.0.1` only — it is not reachable from your network.
- `finance.db`, `config.json`, `accounts.local.json`, `app/rules_local.py` and
  every `.csv` / `.xlsx` / `.pdf` in the tree are **gitignored by default**, so
  the obvious accidents are prevented for you.
- There is no analytics, crash reporting, or update check of any kind.

The one outbound connection is to SimpleFIN, using the access URL you provide.

---

## Quick start

```bash
git clone https://github.com/nssharpe/finance-dashboard.git
cd finance-dashboard
pip install -r requirements.txt
python -m app.main
```

Then open <http://127.0.0.1:8765>. On Windows you can double-click `run.bat`
instead, which installs dependencies and opens the browser for you.

### Connecting your accounts

1. Create a SimpleFIN Bridge account and connect your banks there.
2. Copy your **Setup Token**.
3. Paste it into the banner at the top of the dashboard and click **Connect**.

The token is exchanged once for a long-lived access URL, which is written to
`config.json`. **That URL is a bearer credential — treat it like a password.**
It is gitignored; see `config.example.json` for the shape.

SimpleFIN returns roughly 90 days of history per pull, and the database
accumulates more every time you hit **Refresh**.

---

## Categorization

Three layers, each overriding the one before it:

| Layer | Where it lives | Scope |
|---|---|---|
| 1. Regex ruleset | `app/rules.py`, `app/rules_local.py` | Every transaction |
| 2. Merchant rules | `merchant_rules` table (UI) | Everything matching a pattern |
| 3. Manual override | `category_overrides` (UI) | One transaction |

**Start with merchant rules** — they're the ergonomic path and need no code.
Recategorize and rename a transaction, click the ⚡ that appears on the row, and
you get a dialog prefilled with that transaction's original label. Shorten the
pattern to broaden the rule; a live counter tells you how many transactions it
will touch before you commit. Manage them under **Insights → Merchant Rules**.

Deleting a rule cleanly restores the original label and category, because the
untouched values are kept in `raw_description` and `base_category` rather than
being recomputed.

**`app/rules.py`** ships a generic starter set: national chains, common utilities
and money-movement patterns. It knows nothing about you. For regex-shaped
patterns a substring can't express, copy `rules_local.example.py` to
`app/rules_local.py` — it's gitignored and its rules take priority.

Canonical category names are defined in `app/categorize.py`.

---

## Importing statements

Useful when you need history from before your aggregator's window, or to fill a
gap. Two importers, both **idempotent** — re-running never duplicates.

```bash
python -m app.importers.statements <statements-dir> --dry-run
python -m app.importers.bofa_checking <checking-export.csv> --dry-run
```

Copy `accounts.example.json` to `accounts.local.json` and fill in each account's
SimpleFIN id, its name, and the export filename.

Banks don't agree on much, so parsers are organized by **export shape** rather
than by brand. Find the row matching what your download looks like:

| Parser | Export shape | Handles |
|---|---|---|
| `parse_citi` | CSV with separate `Debit` / `Credit` columns | the common two-column layout |
| `parse_capital_one` | CSV, `Debit`/`Credit` + a card-number column | multiple cards on one account — all are kept, never filtered |
| `parse_chase` | CSV with one pre-signed `Amount` column | negative already means money out |
| `parse_bofa_card_dir` | a *directory* of monthly CSVs | plus a live pending feed that overlaps the newest month; de-duped on reference number |
| `parse_bofa_checking` | CSV with a running-balance column | lets you verify the import by chaining balances — see below |
| `parse_amex_xlsx` | XLSX, header a few rows down | **a purchase is a positive number**; the parser negates it |
| `parse_paypal_pdfs` | a directory of monthly PDF statements | each file contains two statements; only the account-level one is read |

These are the shapes implemented so far, named after the institution each was
first written against. Most exports from other banks match one of them already —
try the closest shape before writing a new parser, and see
[CONTRIBUTING.md](CONTRIBUTING.md) if none fit.

**Always `--dry-run` first.** It reports, per account, how many rows already
exist and how many are new — split out by date window. Where the statement
overlaps data you already have, a near-100% match rate is your evidence the
parser and its sign convention are correct. A low rate means something is wrong
with the parser, not with your data.

De-duplication matches on **amount within a ±7 day window**, never on
description — the same transaction is described differently by different sources.
Each existing row is consumed at most once, so N identical repeat charges need N
statement rows to be considered already-present.

If your export carries opening and closing balances, chain them: start from the
opening balance, add every transaction, and confirm you land on the closing
balance. That single check catches both missed rows and duplicated ones.

---

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

The test suite covers the parts that fail silently rather than loudly: rule
precedence and reversion, sign conventions per statement format, and the
de-duplication logic. Please add a test with any parser or rule change.

```
app/
  main.py            FastAPI app and all HTTP endpoints
  db.py              schema, migrations, upserts
  rules.py           generic regex ruleset  (rules_local.py = yours, gitignored)
  categorize.py      canonical categories + source-category mapping
  merchant_rules.py  user-created rename/recategorize rules
  simplefin.py       SimpleFIN client
  importers/         xlsx history, statement CSV/XLSX/PDF, wallet CSV
  static/            vanilla JS frontend (no build step)
tests/
```

Backend changes need a server restart — the app runs with `reload=False`.
Frontend changes just need a browser refresh.

## Working on this with an AI assistant

[`AGENTS.md`](AGENTS.md) is a briefing for coding agents: how to run and test,
the invariants that break *silently* if you get them wrong, and a hard rule about
never touching the local financial data. Claude Code, Codex, Cursor and others
read it automatically; `CLAUDE.md` points at the same file.

If you use an assistant here, read `AGENTS.md` yourself too — the "silent
failure" list is the accumulated set of things that have actually gone wrong.

The running app also serves an OpenAPI spec at `/openapi.json` and interactive
docs at `/docs`, which is usually the fastest way to give an agent an accurate
picture of the API.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

**Never commit real financial data.** The `.gitignore` blocks the usual paths,
but check `git status` before committing, and scrub any transaction descriptions
out of issue reports and test fixtures.

## Credits

Built by [Nate Sharpe](https://github.com/nssharpe) with
[Claude Code](https://claude.com/claude-code). Much of the implementation was
written by Claude (Opus 5) in collaboration — the commit history carries
`Co-Authored-By` trailers showing which changes.

## License

MIT — see [LICENSE](LICENSE).
