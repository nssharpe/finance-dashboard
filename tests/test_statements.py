"""Statement parsers: sign conventions and de-duplication.

Two things here are easy to break and hard to notice:

* **Signs.** The database stores negative = money out for every account, but the
  export shapes disagree — some arrive pre-signed, some split Debit/Credit across
  two columns, and the XLSX shape reports a purchase as a *positive* number. Flip
  one and a year of spending silently becomes income.
* **De-dup.** A statement legitimately repeats the same charge on the same day,
  and ``db.synthetic_id`` hashes only (date, account, description, amount) — so
  without an occurrence salt those rows collapse into one and money disappears.
"""

from pathlib import Path

import pytest

from app import db
from app.importers import statements as st

FIXTURES = Path(__file__).parent / "fixtures"


def _by_desc(rows):
    return {r["description"]: r for r in rows}


# ---------------------------------------------------------------------------
# Sign conventions
# ---------------------------------------------------------------------------

def test_debit_is_negative_credit_is_positive():
    rows = _by_desc(st.parse_debit_credit_csv(str(FIXTURES / "debit_credit.csv")))
    assert rows["COFFEE SHOP MAIN ST"]["amount"] == -12.50
    assert rows["AUTOPAY THANK YOU"]["amount"] == 400.00


def test_signed_amounts_pass_through():
    rows = _by_desc(st.parse_signed_amount_csv(str(FIXTURES / "signed_amount.csv")))
    assert rows["BOOKSTORE DOWNTOWN"]["amount"] == -44.45
    assert rows["AUTOMATIC PAYMENT - THANK"]["amount"] == 400.00


def test_debit_credit_handles_alternate_date_headers():
    rows = _by_desc(st.parse_debit_credit_csv(str(FIXTURES / "debit_credit_alt_headers.csv")))
    assert rows["LOCAL BISTRO"]["amount"] == -63.03
    assert rows["AUTOPAY PAYMENT RECEIVED"]["amount"] == 512.06


def test_all_card_numbers_on_an_account_are_kept():
    """Two card numbers, one account — neither may be filtered out."""
    rows = st.parse_debit_credit_csv(str(FIXTURES / "debit_credit_alt_headers.csv"))
    assert len(rows) == 3


def test_monthly_dir_amounts_pass_through_already_signed():
    rows = _by_desc(st.parse_monthly_csv_dir(str(FIXTURES / "monthly_dir")))
    assert rows["AIRLINE TICKET"]["amount"] == -372.59
    assert rows["PAYMENT - THANK YOU"]["amount"] == 25.50


def test_source_category_is_captured_when_the_export_has_one():
    rows = _by_desc(st.parse_signed_amount_csv(str(FIXTURES / "signed_amount.csv")))
    assert rows["CORNER MARKET"]["source_category"] == "Groceries"


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------

def test_monthly_dir_collapses_the_pending_feed_overlap():
    """The pending feed repeats the newest month; REF002 appears in both."""
    rows = st.parse_monthly_csv_dir(str(FIXTURES / "monthly_dir"))
    assert len(rows) == 3  # not 4
    assert sum(1 for r in rows if r["description"] == "AIRLINE TICKET") == 1


# ---------------------------------------------------------------------------
# Insert: the occurrence salt that keeps repeated same-day charges distinct
# ---------------------------------------------------------------------------

SPEC = {
    "account_id": "acct1",
    "account_name": "Test Card",
    "aliases": ["Test Card"],
    "source": "test_csv",
}


def test_identical_same_day_charges_are_all_kept(conn):
    """Three identical $30 charges on one day must stay three rows."""
    rows = [{"posted": "2026-03-09", "description": "APPLE CASH SENT", "amount": -30.0}
            for _ in range(3)]
    st.insert(conn, SPEC, rows)
    n = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE description = 'APPLE CASH SENT'"
    ).fetchone()[0]
    assert n == 3


def test_insert_is_idempotent(conn):
    rows = [{"posted": "2026-03-09", "description": "APPLE CASH SENT", "amount": -30.0}
            for _ in range(3)]
    st.insert(conn, SPEC, rows)
    st.insert(conn, SPEC, rows)
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert n == 3


def test_ids_do_not_depend_on_input_order(conn):
    """A refreshed pending-transactions file reorders rows; ids must not move."""
    rows = [
        {"posted": "2026-03-09", "description": "CHARGE A", "amount": -30.0},
        {"posted": "2026-03-09", "description": "CHARGE A", "amount": -30.0},
        {"posted": "2026-03-09", "description": "CHARGE B", "amount": -12.0},
    ]
    st.insert(conn, SPEC, rows)
    first = {r[0] for r in conn.execute("SELECT id FROM transactions")}

    conn.execute("DELETE FROM transactions")
    st.insert(conn, SPEC, list(reversed(rows)))
    second = {r[0] for r in conn.execute("SELECT id FROM transactions")}
    assert first == second


# ---------------------------------------------------------------------------
# Category resolution: rules first, statement label only as a fallback
# ---------------------------------------------------------------------------

def test_rules_beat_the_statement_category():
    row = {"description": "STARBUCKS STORE 1", "source_category": "Merchandise"}
    assert st._category_for(row) == "Restaurants etc."


def test_statement_category_fills_what_the_rules_miss():
    row = {"description": "ZZQQ UNKNOWN MERCHANT", "source_category": "Dining"}
    assert st._category_for(row) == "Restaurants etc."


def test_unknown_on_both_sides_stays_uncategorized():
    row = {"description": "ZZQQ UNKNOWN", "source_category": "Blah"}
    assert st._category_for(row) == "Uncategorized"


def test_forced_category_wins_over_everything():
    """PayPal funding legs are forced to Transfer so they never count as spend."""
    row = {"description": "STARBUCKS", "source_category": "Dining",
           "force_category": "Transfer"}
    assert st._category_for(row) == "Transfer"
