"""CSV importers for PayPal, Venmo, and Amazon order history.

Each public function takes a live sqlite3 connection and the decoded CSV text
(utf-8-sig decoded by the caller) and returns a summary dict.

None of these functions commit — the caller (api_import_csv) commits after
calling apply_overrides / apply_description_overrides.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import datetime, timezone

from app import db
from app.categorize import categorize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_amount(raw: str) -> float | None:
    """Parse a monetary string that may have $, commas, leading/trailing spaces."""
    if raw is None:
        return None
    cleaned = raw.strip().replace(",", "").replace("$", "").replace("+", "")
    # handle "- 5.00" style with space after sign
    cleaned = re.sub(r"^-\s+", "-", cleaned)
    if not cleaned or cleaned in ("N/A", "Not Applicable"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _mm_dd_yyyy_to_iso(date_str: str) -> str | None:
    """Convert MM/DD/YYYY to YYYY-MM-DD."""
    date_str = (date_str or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _iso_z_to_date(date_str: str) -> str | None:
    """Convert ISO8601 datetime (possibly with Z suffix) to YYYY-MM-DD."""
    date_str = (date_str or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Try stripping trailing Z or timezone offset
    date_str = re.sub(r"[Z+].*$", "", date_str.strip())
    try:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d")
    except ValueError:
        pass
    # Try just taking the date part
    if len(date_str) >= 10:
        candidate = date_str[:10]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
            return candidate
        except ValueError:
            pass
    return None


def _parse_any_date(date_str: str) -> str | None:
    """Try to parse date in any common format."""
    date_str = (date_str or "").strip()
    if not date_str:
        return None
    # ISO8601 with time
    if "T" in date_str or "Z" in date_str:
        return _iso_z_to_date(date_str)
    # Already ISO date
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str
    # US format
    return _mm_dd_yyyy_to_iso(date_str)


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------

# Transfer-type patterns for PayPal Type field
_PP_TRANSFER_TYPES = re.compile(
    r"deposit|transfer|withdrawal|add money|general card|bank deposit",
    re.IGNORECASE,
)


def import_paypal(conn: sqlite3.Connection, text: str) -> dict:
    """Import PayPal CSV export.

    Only processes rows with Status == 'Completed'.
    Upserts a single 'paypal_wallet' account; upserts each transaction.
    """
    reader = csv.DictReader(io.StringIO(text))

    # Collect all completed rows first so we can find the latest balance.
    completed_rows: list[dict] = []
    for row in reader:
        if (row.get("Status") or "").strip() != "Completed":
            continue
        completed_rows.append(row)

    if not completed_rows:
        return {"ok": True, "source": "paypal", "accounts": 0, "transactions": 0}

    # Find the chronologically latest row to extract current balance.
    def _row_sort_key(r: dict):
        date_part = _mm_dd_yyyy_to_iso(r.get("Date", "")) or ""
        time_part = (r.get("Time") or "").strip()
        return date_part + " " + time_part

    latest_row = max(completed_rows, key=_row_sort_key)
    raw_balance = (latest_row.get("Balance") or "").strip().replace(",", "")
    try:
        current_balance = float(raw_balance)
    except ValueError:
        current_balance = 0.0
    balance_date = _mm_dd_yyyy_to_iso(latest_row.get("Date", "")) or ""

    # Upsert the PayPal wallet account.
    db.upsert_account(
        conn,
        {
            "id": "paypal_wallet",
            "name": "PayPal",
            "org": "PayPal",
            "type": "depository",
            "currency": "USD",
            "balance": current_balance,
            "available_balance": None,
            "balance_date": balance_date,
            "last_seen": _now_iso(),
            "active": True,
        },
    )

    count = 0
    for row in completed_rows:
        txn_type = (row.get("Type") or "").strip()
        name = (row.get("Name") or "").strip()
        item_title = (row.get("Item Title") or "").strip()
        txn_id_raw = (row.get("Transaction ID") or "").strip()
        date_str = (row.get("Date") or "").strip()
        currency = (row.get("Currency") or "USD").strip()

        iso_date = _mm_dd_yyyy_to_iso(date_str)
        if not iso_date:
            continue

        # Use Total preferentially, fall back to Amount.
        amount_raw = (row.get("Total") or row.get("Amount") or "").strip()
        amount = _parse_amount(amount_raw)
        if amount is None:
            continue

        # Build description.
        description = name
        if item_title:
            description = f"{name} — {item_title}" if name else item_title

        # Transaction ID.
        if txn_id_raw:
            txn_id = "pp_" + txn_id_raw
        else:
            txn_id = db.synthetic_id(iso_date, "paypal", description, amount)

        # Category.
        if _PP_TRANSFER_TYPES.search(txn_type):
            category = "Transfer"
        else:
            category = categorize(description)

        db.upsert_transaction(
            conn,
            {
                "id": txn_id,
                "account_id": "paypal_wallet",
                "account_name": "PayPal",
                "posted": iso_date,
                "amount": amount,
                "description": description,
                "payee": name or None,
                "memo": item_title or None,
                "pending": False,
                "source": "paypal",
                "source_category": txn_type or None,
                "category": category,
            },
        )
        count += 1

    return {"ok": True, "source": "paypal", "accounts": 1, "transactions": count}


# ---------------------------------------------------------------------------
# Venmo
# ---------------------------------------------------------------------------

_VENMO_TRANSFER_TYPES = re.compile(
    r"transfer|standard transfer|cashout|add money",
    re.IGNORECASE,
)


def import_venmo(conn: sqlite3.Connection, text: str) -> dict:
    """Import Venmo CSV export.

    Venmo exports have preamble/footer junk rows. This function robustly
    locates the header row containing both 'Datetime' and 'Amount (total)'.
    """
    lines = text.splitlines()

    # Locate the header row index.
    header_idx: int | None = None
    for i, line in enumerate(lines):
        if "Datetime" in line and "Amount (total)" in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find Venmo header row")

    # Use DictReader on the lines from header onward.
    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))

    # We'll track running balance from Venmo's balance column if present.
    latest_balance: float | None = None
    latest_date: str | None = None

    count = 0
    for row in reader:
        # Skip rows without a usable ID (beginning/ending balance rows, etc.)
        row_id = (row.get("ID") or row.get("id") or "").strip()
        if not row_id:
            continue

        # Date
        datetime_str = (row.get("Datetime") or "").strip()
        iso_date = _parse_any_date(datetime_str)
        if not iso_date:
            continue

        # Amount
        amount_raw = (row.get("Amount (total)") or "").strip()
        amount = _parse_amount(amount_raw)
        if amount is None:
            continue

        # Description
        note = (row.get("Note") or "").strip()
        from_who = (row.get("From") or "").strip()
        to_who = (row.get("To") or "").strip()
        description = note
        if from_who or to_who:
            description = f"{note} ({from_who} → {to_who})" if note else f"{from_who} → {to_who}"

        # Category
        txn_type = (row.get("Type") or "").strip()
        if _VENMO_TRANSFER_TYPES.search(txn_type):
            category = "Transfer"
        else:
            category = categorize(description)

        txn_id = "vm_" + row_id

        db.upsert_transaction(
            conn,
            {
                "id": txn_id,
                "account_id": "venmo_wallet",
                "account_name": "Venmo",
                "posted": iso_date,
                "amount": amount,
                "description": description,
                "payee": from_who or to_who or None,
                "memo": note or None,
                "pending": False,
                "source": "venmo",
                "source_category": txn_type or None,
                "category": category,
            },
        )
        count += 1

        # Track latest balance for account upsert.
        # Some Venmo exports have a "Balance" column.
        balance_raw = (row.get("Balance") or "").strip()
        if balance_raw:
            b = _parse_amount(balance_raw)
            if b is not None and (latest_date is None or iso_date >= latest_date):
                latest_balance = b
                latest_date = iso_date

    # Upsert Venmo wallet account.
    db.upsert_account(
        conn,
        {
            "id": "venmo_wallet",
            "name": "Venmo",
            "org": "Venmo",
            "type": "depository",
            "currency": "USD",
            "balance": latest_balance,
            "available_balance": None,
            "balance_date": latest_date,
            "last_seen": _now_iso(),
            "active": True,
        },
    )

    return {"ok": True, "source": "venmo", "accounts": 1, "transactions": count}


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------

def _is_retail(fieldnames: list[str]) -> bool:
    """Detect retail format: has 'Total Owed' and 'Payment Instrument Type'."""
    fnames_lower = {f.strip().lower() for f in fieldnames}
    return "total owed" in fnames_lower and "payment instrument type" in fnames_lower


def _is_digital(fieldnames: list[str]) -> bool:
    """Detect digital format: has 'OurPrice' and 'DigitalOrderItemId'."""
    fnames_lower = {f.strip().lower() for f in fieldnames}
    return "ourprice" in fnames_lower and "digitalorderitemid" in fnames_lower


def _extract_last4(payment_str: str) -> str | None:
    """Extract the 4-digit card suffix from e.g. 'Visa - 1234'."""
    tokens = (payment_str or "").split()
    if tokens:
        last = tokens[-1].strip()
        if re.match(r"^\d{4}$", last):
            return last
    return None


def _parse_amazon_csv(text: str) -> tuple[str, list[dict]]:
    """Parse either Amazon retail or digital CSV.

    Returns (file_type, rows) where file_type is 'retail' or 'digital'
    and rows is a list of normalized dicts.
    """
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []

    if _is_retail(fieldnames):
        file_type = "retail"
        rows: list[dict] = []
        for row in reader:
            status = (row.get("Order Status") or "").strip()
            if status.lower() == "cancelled":
                continue
            order_date_raw = (row.get("Order Date") or "").strip()
            order_date = _iso_z_to_date(order_date_raw)
            if not order_date:
                continue
            total_owed_raw = (row.get("Total Owed") or "0").strip().lstrip("'")
            try:
                total_owed = float(total_owed_raw)
            except ValueError:
                total_owed = 0.0
            qty_raw = (row.get("Quantity") or "1").strip()
            try:
                quantity = int(qty_raw)
            except ValueError:
                quantity = 1
            asin = (row.get("ASIN") or "").strip() or None
            product_name = (row.get("Product Name") or "").strip() or None
            payment_str = (row.get("Payment Instrument Type") or "").strip()
            card_last4 = _extract_last4(payment_str)
            rows.append({
                "file_type": "retail",
                "order_id": None,
                "order_date": order_date,
                "asin": asin,
                "product_name": product_name,
                "quantity": quantity,
                "total_owed": total_owed,
                "card_last4": card_last4,
                "account_hint": payment_str or None,
            })
        return "retail", rows

    elif _is_digital(fieldnames):
        file_type = "digital"
        rows = []
        for row in reader:
            order_date_raw = (row.get("OrderDate") or "").strip()
            order_date = _iso_z_to_date(order_date_raw)
            if not order_date:
                continue
            price_raw = (row.get("OurPrice") or "0").strip()
            try:
                total_owed = float(price_raw)
            except ValueError:
                total_owed = 0.0
            asin = (row.get("ASIN") or "").strip() or None
            product_name = (row.get("ProductName") or "").strip() or None
            order_id = (row.get("OrderId") or "").strip() or None
            rows.append({
                "file_type": "digital",
                "order_id": order_id,
                "order_date": order_date,
                "asin": asin,
                "product_name": product_name,
                "quantity": 1,
                "total_owed": total_owed,
                "card_last4": None,
                "account_hint": None,
            })
        return "digital", rows

    else:
        raise ValueError(
            f"Cannot determine Amazon file type from columns: {fieldnames[:10]}"
        )


def _match_amazon_orders(conn: sqlite3.Connection) -> int:
    """Best-effort matching of amazon_items to transactions.

    Aggregates retail items by (order_date, card_last4) and tries to find
    a credit/depository transaction within ±5 days whose amount is within
    $0.05 of the negative aggregate total.

    Returns the number of items matched.
    """
    # Aggregate retail items with a card_last4 (digital items have no card_last4).
    agg_rows = conn.execute(
        """
        SELECT order_date, card_last4, SUM(total_owed) AS agg_total
        FROM amazon_items
        WHERE card_last4 IS NOT NULL
          AND matched_txn_id IS NULL
        GROUP BY order_date, card_last4
        """
    ).fetchall()

    matched_count = 0

    for agg in agg_rows:
        order_date = agg["order_date"]
        card_last4 = agg["card_last4"]
        agg_total = agg["agg_total"]
        target_amount = -round(agg_total, 2)  # expect a negative (debit) transaction

        # Find candidate transactions.
        candidates = conn.execute(
            """
            SELECT t.id, t.amount, t.posted, a.name AS account_name, t.account_id
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE (
                    t.account_id LIKE ? OR
                    (a.name IS NOT NULL AND a.name LIKE ?)
                  )
              AND ABS(t.amount - ?) < 0.06
              AND t.posted BETWEEN date(?, '-5 days') AND date(?, '+5 days')
            """,
            (
                f"%{card_last4}%",
                f"%{card_last4}%",
                target_amount,
                order_date,
                order_date,
            ),
        ).fetchall()

        if not candidates:
            continue

        # Pick the candidate closest in date to order_date.
        best = min(
            candidates,
            key=lambda r: abs(
                (datetime.strptime(r["posted"], "%Y-%m-%d") -
                 datetime.strptime(order_date, "%Y-%m-%d")).days
            ),
        )

        conn.execute(
            "UPDATE amazon_items SET matched_txn_id = ? "
            "WHERE order_date = ? AND card_last4 = ? AND matched_txn_id IS NULL",
            (best["id"], order_date, card_last4),
        )

        matched_count += conn.execute(
            "SELECT changes()"
        ).fetchone()[0]

    return matched_count


def import_amazon(conn: sqlite3.Connection, text: str) -> dict:
    """Import an Amazon order history CSV (retail or digital format).

    Inserts rows into the amazon_items table (does NOT create transactions).
    Attempts to match retail items to existing credit-card transactions.
    """
    file_type, rows = _parse_amazon_csv(text)

    inserted = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO amazon_items
                (order_id, order_date, asin, product_name, quantity,
                 total_owed, card_last4, account_hint, matched_txn_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                r["order_id"],
                r["order_date"],
                r["asin"],
                r["product_name"],
                r["quantity"],
                r["total_owed"],
                r["card_last4"],
                r["account_hint"],
            ),
        )
        inserted += 1

    matched = _match_amazon_orders(conn) if file_type == "retail" else 0

    return {
        "ok": True,
        "source": "amazon",
        "file_type": file_type,
        "items": inserted,
        "matched": matched,
    }
