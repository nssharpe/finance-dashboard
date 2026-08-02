"""Shared fixtures. Every test runs against a throwaway database."""

import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    """An initialized, empty database in a temp dir."""
    connection = db.get_conn(str(tmp_path / "test.db"))
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def add_txn(conn):
    """Insert a transaction with sensible defaults; returns its id."""
    counter = {"n": 0}

    def _add(description, category="Uncategorized", amount=-10.0,
             posted="2026-01-15", txn_id=None, source="test"):
        counter["n"] += 1
        tid = txn_id or f"t{counter['n']}"
        db.upsert_transaction(conn, {
            "id": tid,
            "account_id": "acct1",
            "account_name": "Test Account",
            "posted": posted,
            "amount": amount,
            "description": description,
            "source": source,
            "category": category,
        })
        return tid

    return _add
