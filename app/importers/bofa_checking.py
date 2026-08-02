"""Importer for Bank of America "Adv Plus Banking" checking CSV exports.

Why this exists
---------------
The DB had a hard gap from 2026-01-01 to 2026-03-19: the ``csv2025`` annual
import stops at 2025-12-31, and SimpleFIN was only connected on 2026-06-17 with
a ~90-day lookback (earliest live row 2026-03-20).  Nothing ever filled the
eleven weeks in between.  A BofA statement export covers that window, but it
also overlaps the two existing sources on either side, so a naive import would
duplicate months of history.

De-dup model
------------
BofA statement descriptions do not match what is already in the DB:

* ``csv2025`` rows (Dec 2025) were **masked** by the 2025 consolidation script
  (``CO ID:XXXXX90310``) and may carry hand-written annotations.
* ``simplefin`` rows (2026-03-20 onward) use SimpleFIN's own posted date, which
  can lag the statement date by a few days.

So matching is on ``(amount, date-window)`` against every historical spelling of
this one account (``BofA Checking`` / ``Adv Plus Banking`` /
its current name), never on description.  Each DB candidate is
consumed at most once so N identical repeat charges need N CSV rows to cover.

Run with ``--dry-run`` first; it reports match counts split by overlap window,
which is the signal that the matcher is behaving (the Dec and Mar-Jul windows
should come out ~fully matched).

Usage
-----
    python -m app.importers.bofa_checking <path-to-csv> [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from app import db
from app.categorize import categorize

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "accounts.local.json",
)


def _load_spec(key: str = "bofachecking") -> dict:
    """Read this importer's account entry from the local registry."""
    if os.path.exists(_REGISTRY_PATH):
        with open(_REGISTRY_PATH, encoding="utf-8") as fh:
            entry = json.load(fh).get(key)
        if entry:
            return entry
    raise RuntimeError(
        f"No {key!r} entry in accounts.local.json. "
        f"Copy accounts.example.json to accounts.local.json and fill it in."
    )


# The SimpleFIN account this CSV belongs to.  Linking rows to it (rather than
# leaving account_id NULL like the csv2025 import did) is what lets the
# net-worth / account-history charts reconstruct a continuous balance.
# Which account this importer writes to is personal configuration, so it lives
# in the gitignored ``accounts.local.json`` under the key ``bofachecking``.
# See ``accounts.example.json``.
_SPEC = _load_spec()

ACCOUNT_ID = _SPEC["account_id"]
ACCOUNT_NAME = _SPEC["account_name"]
SOURCE = _SPEC.get("source", "bofa_csv")

# Every name this account has gone by across earlier imports — a per-account
# query has to cover all of them or it will "discover" phantom missing rows.
ACCOUNT_ALIASES = tuple(_SPEC.get("aliases") or [ACCOUNT_NAME])

# SimpleFIN's posted date can drift from the statement date; csv2025 came from
# the same statement data so it lines up exactly, but one window is enough.
MATCH_WINDOW_DAYS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_amount(raw: str) -> float | None:
    cleaned = (raw or "").strip().replace(",", "").replace("$", "").replace("+", "")
    cleaned = re.sub(r"^-\s+", "-", cleaned)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_csv(text: str) -> list[dict]:
    """Return normalized rows from a BofA checking export.

    The file starts with a five-line summary block (beginning/ending balance,
    total credits/debits) before the real ``Date,Description,Amount,Running
    Bal.`` header.  Descriptions can contain embedded newlines inside quotes, so
    this parses with ``csv.reader`` over the whole text rather than by line.
    """
    rows = list(csv.reader(io.StringIO(text)))

    header_idx = None
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if len(cells) >= 3 and cells[0] == "Date" and cells[1] == "Description":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find the 'Date,Description,Amount' header row")

    out: list[dict] = []
    for row in rows[header_idx + 1:]:
        if len(row) < 3:
            continue
        date_raw, desc, amount_raw = row[0].strip(), row[1].strip(), row[2]
        try:
            posted = datetime.strptime(date_raw, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        amount = _parse_amount(amount_raw)
        # Drops the "Beginning balance as of ..." pseudo-row (blank amount) and
        # any trailing balance line.
        if amount is None or not desc:
            continue
        out.append({
            "posted": posted,
            "description": re.sub(r"\s+", " ", desc),
            "amount": round(amount, 2),
        })
    return out


def _existing_rows(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """All DB rows for this account (under any alias) in the date range."""
    placeholders = ",".join("?" for _ in ACCOUNT_ALIASES)
    sql = (
        "SELECT id, posted, amount, description, source FROM transactions "
        f"WHERE (account_id = ? OR account_name IN ({placeholders})) "
        "  AND posted BETWEEN ? AND ?"
    )
    params = (ACCOUNT_ID, *ACCOUNT_ALIASES, start, end)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def diff(conn: sqlite3.Connection, csv_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split CSV rows into (already-present, missing).

    Greedy one-to-one match on rounded amount within ``MATCH_WINDOW_DAYS``,
    preferring the candidate closest in date.  Each DB row is consumed once.
    """
    if not csv_rows:
        return [], []

    start = min(r["posted"] for r in csv_rows)
    end = max(r["posted"] for r in csv_rows)
    pad = timedelta(days=MATCH_WINDOW_DAYS)
    lo = (datetime.strptime(start, "%Y-%m-%d") - pad).strftime("%Y-%m-%d")
    hi = (datetime.strptime(end, "%Y-%m-%d") + pad).strftime("%Y-%m-%d")

    # Bucket existing rows by rounded amount for cheap lookup.
    by_amount: dict[float, list[dict]] = {}
    for row in _existing_rows(conn, lo, hi):
        by_amount.setdefault(round(row["amount"], 2), []).append(row)

    used: set[str] = set()
    matched: list[dict] = []
    missing: list[dict] = []

    # Exact-date matches first so a same-amount pair a few days apart cannot
    # steal the obviously-correct partner from an exact hit.
    for exact_only in (True, False):
        pending: list[dict] = []
        pool = csv_rows if exact_only else missing
        if not exact_only:
            missing = []
        for row in pool:
            target = datetime.strptime(row["posted"], "%Y-%m-%d")
            span = 0 if exact_only else MATCH_WINDOW_DAYS
            cands = [
                c for c in by_amount.get(row["amount"], [])
                if c["id"] not in used
                and abs((datetime.strptime(c["posted"], "%Y-%m-%d") - target).days) <= span
            ]
            if cands:
                best = min(
                    cands,
                    key=lambda c: abs(
                        (datetime.strptime(c["posted"], "%Y-%m-%d") - target).days
                    ),
                )
                used.add(best["id"])
                matched.append({**row, "matched_id": best["id"], "matched_source": best["source"]})
            else:
                pending.append(row)
        missing = pending

    return matched, missing


def insert(conn: sqlite3.Connection, rows: list[dict]) -> int:
    # A statement can legitimately carry the same charge more than once on one
    # day (three identical $30 Apple Cash sends, two identical $2.11 PayPal
    # lines).  synthetic_id() hashes only (date, account, description, amount),
    # so those would collapse into a single row and silently lose money.  Salt
    # the 2nd+ occurrence with its index within the group — stable across
    # re-runs because the group order comes from file order.
    seen: dict[tuple, int] = {}
    for row in rows:
        key = (row["posted"], row["description"], row["amount"])
        n = seen.get(key, 0)
        seen[key] = n + 1
        desc_key = row["description"] if n == 0 else f"{row['description']}#{n + 1}"
        db.upsert_transaction(
            conn,
            {
                "id": db.synthetic_id(
                    row["posted"], ACCOUNT_NAME, desc_key, row["amount"]
                ),
                "account_id": ACCOUNT_ID,
                "account_name": ACCOUNT_NAME,
                "posted": row["posted"],
                "amount": row["amount"],
                "description": row["description"],
                "payee": None,
                "memo": None,
                "pending": False,
                "source": SOURCE,
                "source_category": None,
                "category": categorize(row["description"]),
            },
        )
    return len(rows)


def import_file(conn: sqlite3.Connection, path: str, dry_run: bool = False) -> dict:
    csv_rows = parse_csv(_read_text(path))
    matched, missing = diff(conn, csv_rows)

    if not dry_run and missing:
        insert(conn, missing)
        db.apply_overrides(conn)
        db.apply_description_overrides(conn)
        conn.execute(
            "UPDATE accounts SET last_seen = ? WHERE id = ?", (_now_iso(), ACCOUNT_ID)
        )
        conn.commit()

    return {
        "ok": True,
        "source": SOURCE,
        "csv_rows": len(csv_rows),
        "already_present": len(matched),
        "inserted": 0 if dry_run else len(missing),
        "missing": missing,
        "matched": matched,
        "dry_run": dry_run,
    }


def _report(result: dict) -> None:
    """Print match counts split by overlap window — the sanity signal."""
    windows = [
        ("2025-12-01", "2025-12-31", "overlaps csv2025"),
        ("2026-01-01", "2026-03-19", "THE GAP - no prior source"),
        ("2026-03-20", "2026-12-31", "overlaps simplefin"),
    ]
    print(f"CSV rows parsed: {result['csv_rows']}")
    print(f"{'window':<26} {'csv':>5} {'present':>8} {'missing':>8}   note")
    for lo, hi, note in windows:
        n_csv = sum(1 for r in result["matched"] + result["missing"] if lo <= r["posted"] <= hi)
        n_have = sum(1 for r in result["matched"] if lo <= r["posted"] <= hi)
        n_miss = sum(1 for r in result["missing"] if lo <= r["posted"] <= hi)
        print(f"{lo}..{hi:<12} {n_csv:>5} {n_have:>8} {n_miss:>8}   {note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-missing", action="store_true")
    args = ap.parse_args(argv)

    conn = db.get_conn()
    db.init_db(conn)
    result = import_file(conn, args.csv_path, dry_run=args.dry_run)
    _report(result)
    if args.show_missing:
        for r in sorted(result["missing"], key=lambda r: r["posted"]):
            print(f"  {r['posted']}  {r['amount']:>12,.2f}  {r['description'][:90]}")
    print(("DRY RUN - nothing written. " if args.dry_run else "Inserted ")
          + f"{len(result['missing'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
