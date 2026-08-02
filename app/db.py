"""SQLite storage layer for the local finance dashboard.

One database file (``finance.db``) holds accounts, transactions, manual category
overrides, pre-2018 annual totals, and a small key/value meta table.

Design notes
------------
* Transactions are keyed by ``id``. For SimpleFIN rows that is the stable
  SimpleFIN transaction id; for historical imports it is a synthetic hash of
  ``(date, account, description, amount)`` so re-running an import never
  duplicates rows.
* Manual category fixes live in ``category_overrides`` and are re-applied after
  every refresh/import, so a re-pull never clobbers a human decision.
"""

import hashlib
import os
import sqlite3
from datetime import datetime, timezone

# Default DB path lives next to the project root (one level above app/).
DB_PATH = os.environ.get(
    "FINANCE_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finance.db"),
)

SCHEMA_VERSION = "1"


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with sensible defaults (row access by name, FK on).

    ``check_same_thread=False`` because FastAPI runs sync endpoints in a
    threadpool and closes the ``get_db`` generator on a *different* worker
    thread than the one that opened it, which otherwise raises
    ``ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread`` during teardown. Safe here: every request gets its own
    connection, so one is never shared between threads concurrently.
    """
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id                TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            org               TEXT,
            type              TEXT,              -- depository | credit | investment | manual | other
            currency          TEXT DEFAULT 'USD',
            balance           REAL,
            available_balance REAL,
            balance_date      TEXT,              -- ISO date
            last_seen         TEXT,              -- ISO datetime of last refresh that saw it
            active            INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id                  TEXT PRIMARY KEY,
            account_id          TEXT,
            account_name        TEXT,
            posted              TEXT NOT NULL,   -- ISO date (YYYY-MM-DD)
            amount              REAL NOT NULL,   -- signed: negative = money out, positive = money in
            description         TEXT,
            payee               TEXT,
            memo                TEXT,
            pending             INTEGER DEFAULT 0,
            source              TEXT,            -- simplefin | mint | empower | csv2025 | manual
            source_category     TEXT,            -- raw category from the source, if any
            category            TEXT,            -- canonical category (post-mapping/override)
            category_overridden INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_txn_posted   ON transactions(posted);
        CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
        CREATE INDEX IF NOT EXISTS idx_txn_account  ON transactions(account_id);

        CREATE TABLE IF NOT EXISTS category_overrides (
            txn_id   TEXT PRIMARY KEY,
            category TEXT NOT NULL
        );

        -- Annual category totals for years that have no transaction-level detail
        -- (2013-2017), seeded from the Summary sheet of the analysis workbook.
        CREATE TABLE IF NOT EXISTS annual_totals (
            year     INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount   REAL NOT NULL,
            PRIMARY KEY (year, category)
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS balance_snapshots (
            account_id  TEXT NOT NULL,
            date        TEXT NOT NULL,
            balance     REAL,
            PRIMARY KEY (account_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_date ON balance_snapshots(date);

        CREATE TABLE IF NOT EXISTS description_overrides (
            txn_id      TEXT PRIMARY KEY,
            description TEXT NOT NULL
        );

        -- User-created merchant rules: match a substring of raw_description and
        -- rename and/or recategorize every hit, past and future.
        -- See app/merchant_rules.py for the precedence model.
        CREATE TABLE IF NOT EXISTS merchant_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern     TEXT NOT NULL,       -- case-insensitive substring
            description TEXT,                -- replacement label (NULL = keep)
            category    TEXT,                -- canonical category (NULL = keep)
            priority    INTEGER DEFAULT 100, -- lower wins; ties -> longest pattern
            active      INTEGER DEFAULT 1,
            created     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_merchant_rules_active
            ON merchant_rules(active, priority);

        -- amazon_items: one row per (order_id, ASIN); synthetic PK to avoid
        -- collision risk when multiple ASINs share an order_id.
        CREATE TABLE IF NOT EXISTS amazon_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id        TEXT,
            order_date      TEXT,
            asin            TEXT,
            product_name    TEXT,
            quantity        INTEGER,
            total_owed      REAL,
            card_last4      TEXT,
            account_hint    TEXT,
            matched_txn_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_amazon_order_date ON amazon_items(order_date);
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column-add and one-time data-fix migrations.

    Called from ``init_db`` after the CREATE TABLE block.  Safe to run on
    every startup — each operation checks current state before acting.
    """
    # Add description_overridden column to transactions if absent.
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(transactions)").fetchall()
    }
    if "description_overridden" not in existing_cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN description_overridden INTEGER DEFAULT 0"
        )

    # raw_description / base_category are the untouched originals that merchant
    # rules revert to when edited or deleted (see app/merchant_rules.py). They
    # are backfilled from the current values: at migration time no merchant rule
    # has run yet, so description/category ARE the originals.
    if "raw_description" not in existing_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN raw_description TEXT")
        conn.execute(
            "UPDATE transactions SET raw_description = description "
            "WHERE raw_description IS NULL"
        )
    if "base_category" not in existing_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN base_category TEXT")
        conn.execute(
            "UPDATE transactions SET base_category = category WHERE base_category IS NULL"
        )

    # One-time account type fixes (idempotent: WHERE clause guards each run).
    conn.execute(
        "UPDATE accounts SET type='investment' "
        "WHERE org LIKE 'UBS One Source%' AND type != 'investment'"
    )
    conn.execute(
        "UPDATE accounts SET type='depository' "
        "WHERE name LIKE 'High Yield Savings%' "
        "  AND org='American Express' AND type != 'depository'"
    )


def synthetic_id(date: str, account: str, description: str, amount) -> str:
    """Stable id for imported rows that lack a native unique id."""
    key = f"{date}|{account}|{description}|{amount}".lower()
    return "syn_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------

def upsert_account(conn: sqlite3.Connection, acct: dict) -> None:
    """Insert or update an account by id (balances refresh, name preserved)."""
    conn.execute(
        """
        INSERT INTO accounts
            (id, name, org, type, currency, balance, available_balance,
             balance_date, last_seen, active)
        VALUES
            (:id, :name, :org, :type, :currency, :balance, :available_balance,
             :balance_date, :last_seen, 1)
        ON CONFLICT(id) DO UPDATE SET
            name              = excluded.name,
            org               = excluded.org,
            type              = excluded.type,
            currency          = excluded.currency,
            balance           = excluded.balance,
            available_balance = excluded.available_balance,
            balance_date      = excluded.balance_date,
            last_seen         = excluded.last_seen,
            active            = 1
        """,
        {
            "id": acct["id"],
            "name": acct.get("name", "Account"),
            "org": acct.get("org"),
            "type": acct.get("type"),
            "currency": acct.get("currency", "USD"),
            "balance": acct.get("balance"),
            "available_balance": acct.get("available_balance"),
            "balance_date": acct.get("balance_date"),
            "last_seen": acct.get("last_seen") or _now_iso(),
        },
    )


def upsert_transaction(conn: sqlite3.Connection, txn: dict) -> None:
    """Insert or update a transaction by id.

    On conflict we refresh the factual fields but never overwrite ``category``
    when the row has been manually overridden (``category_overridden = 1``).
    """
    conn.execute(
        """
        INSERT INTO transactions
            (id, account_id, account_name, posted, amount, description, payee,
             memo, pending, source, source_category, category, category_overridden,
             raw_description, base_category)
        VALUES
            (:id, :account_id, :account_name, :posted, :amount, :description, :payee,
             :memo, :pending, :source, :source_category, :category, 0,
             :description, :category)
        ON CONFLICT(id) DO UPDATE SET
            -- The untouched originals always take the incoming value; merchant
            -- rules are re-applied on top afterwards by apply_merchant_rules().
            raw_description = excluded.raw_description,
            base_category   = excluded.base_category,
            account_id      = excluded.account_id,
            account_name    = excluded.account_name,
            posted          = excluded.posted,
            amount          = excluded.amount,
            description     = CASE WHEN transactions.description_overridden = 1
                                   THEN transactions.description
                                   ELSE excluded.description END,
            payee           = excluded.payee,
            memo            = excluded.memo,
            pending         = excluded.pending,
            source          = excluded.source,
            source_category = excluded.source_category,
            category        = CASE WHEN transactions.category_overridden = 1
                                   THEN transactions.category
                                   ELSE excluded.category END
        """,
        {
            "id": txn["id"],
            "account_id": txn.get("account_id"),
            "account_name": txn.get("account_name"),
            "posted": txn["posted"],
            "amount": txn["amount"],
            "description": txn.get("description", ""),
            "payee": txn.get("payee"),
            "memo": txn.get("memo"),
            "pending": 1 if txn.get("pending") else 0,
            "source": txn.get("source"),
            "source_category": txn.get("source_category"),
            "category": txn.get("category", "Uncategorized"),
        },
    )


def set_override(conn: sqlite3.Connection, txn_id: str, category: str) -> None:
    """Record a manual category override and apply it immediately."""
    conn.execute(
        "INSERT INTO category_overrides(txn_id, category) VALUES (?, ?) "
        "ON CONFLICT(txn_id) DO UPDATE SET category = excluded.category",
        (txn_id, category),
    )
    conn.execute(
        "UPDATE transactions SET category = ?, category_overridden = 1 WHERE id = ?",
        (category, txn_id),
    )


def apply_overrides(conn: sqlite3.Connection) -> int:
    """Re-apply all stored overrides onto the transactions table.

    Called after every refresh/import. Returns the number of rows touched.
    """
    cur = conn.execute(
        """
        UPDATE transactions
           SET category = (SELECT category FROM category_overrides o
                            WHERE o.txn_id = transactions.id),
               category_overridden = 1
         WHERE id IN (SELECT txn_id FROM category_overrides)
        """
    )
    return cur.rowcount


def write_balance_snapshot(
    conn: sqlite3.Connection, account_id: str, date: str, balance
) -> None:
    """Insert or update a daily balance snapshot for an account.

    Parameters
    ----------
    account_id:
        The account's stable id.
    date:
        ISO date string (``YYYY-MM-DD``).
    balance:
        The balance figure to record; may be ``None``.
    """
    conn.execute(
        "INSERT INTO balance_snapshots(account_id, date, balance) VALUES (?, ?, ?) "
        "ON CONFLICT(account_id, date) DO UPDATE SET balance = excluded.balance",
        (account_id, date, balance),
    )


def get_balance_history(conn: sqlite3.Connection, account_id: str) -> list[dict]:
    """Return all balance snapshots for an account, ordered chronologically.

    Returns
    -------
    list of dicts with keys ``date`` (str, ISO) and ``balance`` (float | None).
    """
    rows = conn.execute(
        "SELECT date, balance FROM balance_snapshots "
        "WHERE account_id = ? ORDER BY date ASC",
        (account_id,),
    ).fetchall()
    return [{"date": r["date"], "balance": r["balance"]} for r in rows]


def set_description_override(
    conn: sqlite3.Connection, txn_id: str, description: str
) -> None:
    """Record a manual description override and apply it immediately.

    Mirrors ``set_override`` for category overrides.
    """
    conn.execute(
        "INSERT INTO description_overrides(txn_id, description) VALUES (?, ?) "
        "ON CONFLICT(txn_id) DO UPDATE SET description = excluded.description",
        (txn_id, description),
    )
    conn.execute(
        "UPDATE transactions SET description = ?, description_overridden = 1 WHERE id = ?",
        (description, txn_id),
    )


def apply_description_overrides(conn: sqlite3.Connection) -> int:
    """Re-apply all stored description overrides onto the transactions table.

    Called after every refresh/import.  Returns the number of rows touched.
    Mirrors ``apply_overrides`` for category overrides.
    """
    cur = conn.execute(
        """
        UPDATE transactions
           SET description = (SELECT description FROM description_overrides o
                               WHERE o.txn_id = transactions.id),
               description_overridden = 1
         WHERE id IN (SELECT txn_id FROM description_overrides)
        """
    )
    return cur.rowcount


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
