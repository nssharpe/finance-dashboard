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

`run.bat` checks whether the dashboard is already answering on port 8765 before
it starts anything. If it is, it just opens the browser and exits. Without that
check a second launch starts a second server, and Windows lets both bind the
same port without complaining — you get two of them and no error message.

### Putting it on the Windows taskbar

If you use this daily, a taskbar button beats hunting for the folder:

```bash
python tools/make_icon.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
```

The first renders `app/static/favicon.svg` into a multi-size
`app/static/finance-dashboard.ico` (needs Pillow). The second creates
`Finance Dashboard.lnk` in the project root.

Then **drag the `.lnk` onto your taskbar**. On Windows 11 the right-click menu
offers only "Pin to Start" — dragging is the way to get a taskbar button.

Two details in `tools/make_shortcut.ps1` that look odd but are deliberate:

- The shortcut targets `cmd.exe` with `run.bat` as an argument rather than
  `run.bat` directly. Windows wants an executable target for a taskbar button;
  point one straight at a `.bat` and it tends to land in some other app's jump
  list instead of getting a button of its own.
- It opens minimized. The server console still needs to exist — that's where
  `Ctrl+C` stops it — but it doesn't need to be in your face.

Expect ~3 seconds between the click and the browser appearing; `run.bat` waits
for the server to bind before opening it. The console shows up as a separate
taskbar button rather than merging under the pinned icon, because grouping
follows the running process, not the shortcut. Both are cosmetic.

The generated `.lnk` is gitignored — it stores absolute paths, so committing one
would publish your username and folder layout. The two scripts derive their
paths from their own location and are safe to commit.

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
python -m app.importers.running_balance <checking-export.csv> --dry-run
```

Copy `accounts.example.json` to `accounts.local.json` and fill in each account's
SimpleFIN id, its name, and the export filename.

Banks don't agree on much, so parsers are organized by **export shape** rather
than by brand. Find the row matching what your download looks like:

| Parser | Your export looks like | Notes |
|---|---|---|
| `parse_debit_credit_csv` | CSV with separate `Debit` / `Credit` columns | the most common card shape; tolerates `Transaction Date` / `Posted Date` / `Date`, and reads a `Category` column when present |
| `parse_signed_amount_csv` | CSV with one already-signed `Amount` column | negative already means money out |
| `parse_monthly_csv_dir` | a *directory* of monthly CSVs | plus a pending feed that overlaps the newest month; de-duped on reference number |
| `parse_running_balance_csv` | CSV carrying a running-balance column | lives in `running_balance.py` — that column makes the import verifiable, see below |
| `parse_positive_charge_xlsx` | XLSX, header a few rows down | **a purchase is a positive number**; the parser negates it |
| `parse_paypal_pdfs` | a directory of monthly PayPal PDFs | each file holds two statements; only the account-level one is read |

Multiple cards on one account (a primary plus an authorized user) are all
imported — never filter by card number.

Most bank exports match one of these already. Try the closest shape first — a
new institution usually needs a config entry, not a new parser. See
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
  importers/         statement parsers by export shape, xlsx history, wallet CSV
  static/            vanilla JS frontend (no build step)
tests/
tools/               one-off dev scripts (taskbar icon and shortcut)
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
