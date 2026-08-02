"""Merchant rules: matching, precedence, and clean reversion.

The precedence chain is the part most likely to break silently, so most of these
tests pin it down: regex ruleset < merchant rule < manual per-transaction fix.
"""

import pytest

from app import db, merchant_rules as mr


def _row(conn, txn_id):
    return dict(conn.execute(
        "SELECT description, category, raw_description, base_category "
        "FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_rule_renames_and_recategorizes_all_matches(conn, add_txn):
    a = add_txn("QUICKMART 644 WESTFIELD")
    b = add_txn("QUICKMART 644 WESTFIELD", posted="2026-02-01")
    mr.create_rule(conn, "QUICKMART", "QuickMart Westfield", "Groceries")
    assert mr.apply_all(conn) == 2
    for tid in (a, b):
        assert _row(conn, tid)["description"] == "QuickMart Westfield"
        assert _row(conn, tid)["category"] == "Groceries"


def test_matching_is_case_insensitive_substring(conn, add_txn):
    tid = add_txn("pos debit quickmart 812 easton")
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)
    assert _row(conn, tid)["category"] == "Groceries"


def test_non_matching_transactions_are_untouched(conn, add_txn):
    tid = add_txn("WHOLE FOODS MKT", category="Groceries")
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Restaurants etc.")
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "WHOLE FOODS MKT"
    assert _row(conn, tid)["category"] == "Groceries"


def test_rule_may_set_category_only(conn, add_txn):
    tid = add_txn("QUICKMART 644")
    mr.create_rule(conn, "QUICKMART", None, "Groceries")
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QUICKMART 644"  # label preserved
    assert _row(conn, tid)["category"] == "Groceries"


def test_rule_may_set_description_only(conn, add_txn):
    tid = add_txn("QUICKMART 644", category="Travel")
    mr.create_rule(conn, "QUICKMART", "QuickMart", None)
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QuickMart"
    assert _row(conn, tid)["category"] == "Travel"  # category preserved


def test_rule_requires_a_pattern_and_an_effect(conn):
    with pytest.raises(ValueError):
        mr.create_rule(conn, "", "x", "Groceries")
    with pytest.raises(ValueError):
        mr.create_rule(conn, "QUICKMART", None, None)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_longest_pattern_wins_on_equal_priority(conn, add_txn):
    """The specific rule must beat the general one with no priority juggling."""
    tid = add_txn("QUICKMART 644 WESTFIELD")
    mr.create_rule(conn, "QUICKMART", "Generic QuickMart", "Shopping")
    mr.create_rule(conn, "QUICKMART 644 WESTFIELD", "QuickMart Westfield", "Groceries")
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QuickMart Westfield"
    assert _row(conn, tid)["category"] == "Groceries"


def test_lower_priority_number_wins_over_longer_pattern(conn, add_txn):
    tid = add_txn("QUICKMART 644 WESTFIELD")
    mr.create_rule(conn, "QUICKMART 644 WESTFIELD", "Long", "Shopping", priority=100)
    mr.create_rule(conn, "QUICKMART", "Short but urgent", "Groceries", priority=1)
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "Short but urgent"


def test_inactive_rules_do_not_apply(conn, add_txn):
    tid = add_txn("QUICKMART 644")
    rule_id = mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.update_rule(conn, rule_id, active=0)
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QUICKMART 644"


# ---------------------------------------------------------------------------
# Precedence against manual per-transaction overrides
# ---------------------------------------------------------------------------

def test_manual_category_override_beats_a_rule(conn, add_txn):
    tid = add_txn("QUICKMART 644")
    db.set_override(conn, tid, "Travel")
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)
    db.apply_overrides(conn)
    assert _row(conn, tid)["category"] == "Travel"
    assert _row(conn, tid)["description"] == "QuickMart"  # rename still applies


def test_manual_description_override_beats_a_rule(conn, add_txn):
    tid = add_txn("QUICKMART 644")
    db.set_description_override(conn, tid, "My own label")
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)
    db.apply_description_overrides(conn)
    assert _row(conn, tid)["description"] == "My own label"
    assert _row(conn, tid)["category"] == "Groceries"


# ---------------------------------------------------------------------------
# Reversion — the reason raw_description / base_category exist
# ---------------------------------------------------------------------------

def test_deleting_a_rule_reverts_its_transactions(conn, add_txn):
    tid = add_txn("QUICKMART 644", category="Shopping")
    rule_id = mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QuickMart"

    mr.delete_rule(conn, rule_id)
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QUICKMART 644"
    assert _row(conn, tid)["category"] == "Shopping"


def test_editing_a_rule_reverts_rows_it_no_longer_matches(conn, add_txn):
    keep = add_txn("QUICKMART 644", category="Shopping")
    drop = add_txn("WALMART 219", category="Shopping")
    # "A" is in both QUICKMART and WALMART — deliberately far too broad.
    rule_id = mr.create_rule(conn, "A", "Everything with an A", "Groceries")
    mr.apply_all(conn)
    assert _row(conn, drop)["description"] == "Everything with an A"

    mr.update_rule(conn, rule_id, pattern="QUICKMART")
    mr.apply_all(conn)
    assert _row(conn, keep)["description"] == "Everything with an A"
    assert _row(conn, drop)["description"] == "WALMART 219"
    assert _row(conn, drop)["category"] == "Shopping"


def test_raw_description_is_never_mutated(conn, add_txn):
    tid = add_txn("QUICKMART 644")
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)
    mr.apply_all(conn)  # idempotent — must not rewrite raw from the renamed value
    assert _row(conn, tid)["raw_description"] == "QUICKMART 644"


def test_reimport_does_not_lose_the_rule(conn, add_txn):
    """A refresh upserts the raw label back; rules must re-apply on top."""
    tid = add_txn("QUICKMART 644")
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)

    add_txn("QUICKMART 644", txn_id=tid)  # same id — simulates a re-pull
    assert _row(conn, tid)["description"] == "QUICKMART 644"  # reset by upsert
    mr.apply_all(conn)
    assert _row(conn, tid)["description"] == "QuickMart"


def test_rule_applies_to_newly_arrived_transactions(conn, add_txn):
    mr.create_rule(conn, "QUICKMART", "QuickMart", "Groceries")
    mr.apply_all(conn)
    new = add_txn("QUICKMART 812 EASTON", posted="2026-06-01")
    mr.apply_all(conn)
    assert _row(conn, new)["description"] == "QuickMart"


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def test_preview_counts_without_changing_anything(conn, add_txn):
    tid = add_txn("QUICKMART 644", amount=-5.45)
    add_txn("QUICKMART 812", amount=-43.48, posted="2026-02-02")
    result = mr.preview(conn, "QUICKMART")
    assert result["count"] == 2
    assert result["total_amount"] == pytest.approx(-48.93)
    assert _row(conn, tid)["description"] == "QUICKMART 644"


def test_preview_of_empty_pattern_matches_nothing(conn, add_txn):
    add_txn("QUICKMART 644")
    assert mr.preview(conn, "  ")["count"] == 0
