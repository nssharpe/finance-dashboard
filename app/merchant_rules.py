"""User-created merchant rules: rename + recategorize by matching a label.

The regex ruleset in :mod:`app.rules` is code. This is its data-driven
counterpart: you fix one transaction in the UI, save it as a rule, and it
applies to every matching transaction — past and future — without editing
Python.

Matching
--------
Case-insensitive **substring** against the transaction's ``raw_description``.
The UI prefills the whole description, so leaving it untouched behaves like an
exact match; shortening ``SQ *BLUE BOTTLE 4471`` to ``BLUE BOTTLE`` broadens it
to catch every other store number.

Rules are ordered by ``priority`` ascending, then by **longest pattern first**.
The longest-pattern tiebreak means a specific rule beats a general one without
anybody having to hand-manage priority numbers.

Precedence (lowest to highest)
------------------------------
1. ``app/rules.py`` regex ruleset  -> writes ``transactions.base_category``
2. merchant rules (this module)    -> overwrite description and/or category
3. per-transaction manual overrides -> always win

That layering is why ``transactions`` carries ``raw_description`` and
``base_category``: they are the untouched originals, so editing or deleting a
rule can cleanly revert its rows. Recomputing the category from scratch instead
would be wrong — ``statements.py`` resolves categories rules-first-then-source
while ``categorize()`` prefers the source category, so a naive recompute would
silently change existing rows.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_rules(conn: sqlite3.Connection) -> list[dict]:
    """All rules, in the order they are evaluated, each with a match count."""
    rows = conn.execute(
        """
        SELECT r.*, (
            SELECT COUNT(*) FROM transactions t
             WHERE INSTR(UPPER(COALESCE(t.raw_description, t.description)),
                         UPPER(r.pattern)) > 0
        ) AS match_count
        FROM merchant_rules r
        ORDER BY r.priority ASC, LENGTH(r.pattern) DESC, r.id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_rule(conn: sqlite3.Connection, rule_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM merchant_rules WHERE id = ?", (rule_id,)).fetchone()
    return dict(row) if row else None


def create_rule(
    conn: sqlite3.Connection,
    pattern: str,
    description: str | None = None,
    category: str | None = None,
    priority: int = 100,
) -> int:
    pattern = (pattern or "").strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    if not (description or category):
        raise ValueError("a rule must set a description, a category, or both")
    cur = conn.execute(
        """
        INSERT INTO merchant_rules (pattern, description, category, priority, active, created)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (pattern, (description or "").strip() or None,
         (category or "").strip() or None, priority, _now_iso()),
    )
    return cur.lastrowid


def update_rule(conn: sqlite3.Connection, rule_id: int, **fields) -> None:
    allowed = {"pattern", "description", "category", "priority", "active"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "pattern" and not str(value).strip():
            raise ValueError("pattern must not be empty")
        sets.append(f"{key} = ?")
        params.append(int(value) if key in ("priority", "active") else str(value).strip() or None)
    if not sets:
        return
    params.append(rule_id)
    conn.execute(f"UPDATE merchant_rules SET {', '.join(sets)} WHERE id = ?", params)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))


# ---------------------------------------------------------------------------
# Preview & application
# ---------------------------------------------------------------------------

def preview(conn: sqlite3.Connection, pattern: str, limit: int = 8) -> dict:
    """How many transactions a pattern would touch, plus a few examples.

    Powers the live "matches N transactions" hint in the create-rule dialog, so
    a too-broad pattern is visible before it is saved rather than after.
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return {"count": 0, "samples": [], "total_amount": 0.0}

    row = conn.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total
          FROM transactions
         WHERE INSTR(UPPER(COALESCE(raw_description, description)), UPPER(?)) > 0
        """,
        (pattern,),
    ).fetchone()

    samples = conn.execute(
        """
        SELECT id, posted, amount, category,
               COALESCE(raw_description, description) AS raw_description,
               description
          FROM transactions
         WHERE INSTR(UPPER(COALESCE(raw_description, description)), UPPER(?)) > 0
         ORDER BY posted DESC
         LIMIT ?
        """,
        (pattern, limit),
    ).fetchall()

    return {
        "count": row["n"],
        "total_amount": round(row["total"], 2),
        "samples": [dict(s) for s in samples],
    }


def _ordered_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, pattern, description, category
          FROM merchant_rules
         WHERE active = 1
         ORDER BY priority ASC, LENGTH(pattern) DESC, id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def apply_all(conn: sqlite3.Connection) -> int:
    """Re-evaluate every transaction against the current rule set.

    Each row is first reset to its untouched ``raw_description`` /
    ``base_category``, then the first matching rule is applied. Resetting is what
    makes edits and deletes revert cleanly — without it a removed rule would
    leave its renames behind forever.

    Manually-overridden fields are left alone here; the caller re-applies
    ``apply_overrides`` / ``apply_description_overrides`` afterwards so a
    hand-fix on a single row still outranks a rule.

    Returns the number of transactions whose description or category changed.
    """
    rules = _ordered_rules(conn)
    compiled = [(r["pattern"].upper(), r["description"], r["category"]) for r in rules]

    rows = conn.execute(
        """
        SELECT id, description, category,
               COALESCE(raw_description, description) AS raw_description,
               COALESCE(base_category, category)      AS base_category,
               category_overridden, description_overridden
          FROM transactions
        """
    ).fetchall()

    changed = 0
    for row in rows:
        raw = row["raw_description"] or ""
        target_desc, target_cat = raw, row["base_category"]

        haystack = raw.upper()
        for pattern, new_desc, new_cat in compiled:
            if pattern in haystack:
                if new_desc:
                    target_desc = new_desc
                if new_cat:
                    target_cat = new_cat
                break  # first match wins

        # Never clobber a manual per-transaction fix.
        if row["description_overridden"]:
            target_desc = row["description"]
        if row["category_overridden"]:
            target_cat = row["category"]

        if target_desc != row["description"] or target_cat != row["category"]:
            conn.execute(
                "UPDATE transactions SET description = ?, category = ? WHERE id = ?",
                (target_desc, target_cat, row["id"]),
            )
            changed += 1

    return changed
