"""FastAPI backend for the local personal-finance dashboard.

Binds to 127.0.0.1:8765.  Every request gets its own SQLite connection (WAL
mode means concurrent readers never block the occasional writer).

Run:
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
or:
    python app/main.py
"""

from __future__ import annotations

import os
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db
from app import merchant_rules
from app.categorize import CANONICAL_CATEGORIES, EXCLUDED_FROM_SPENDING, categorize
from app import simplefin
from app.importers.xlsx_history import import_all
from app.importers import csv_accounts as _csv_accounts

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Personal Finance Dashboard", version="1.0.0")

_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

# Mount static files if the directory exists.
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Startup: ensure DB schema exists
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup() -> None:
    conn = db.get_conn()
    try:
        db.init_db(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-request DB dependency
# ---------------------------------------------------------------------------

def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a fresh SQLite connection; close it when the request is done."""
    conn = db.get_conn()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helper: _now_iso
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# SPA root
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> HTMLResponse:
    if _INDEX_HTML.exists():
        return FileResponse(str(_INDEX_HTML), media_type="text/html")
    return HTMLResponse(
        "<html><body><h2>Finance Dashboard</h2>"
        "<p>Frontend not yet built. API is live at <a href='/docs'>/docs</a>.</p>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------

@app.get("/api/status")
def api_status(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    connected = simplefin.is_connected()
    last_refresh = db.get_meta(conn, "last_refresh")

    accounts_count = conn.execute(
        "SELECT COUNT(*) FROM accounts WHERE active = 1"
    ).fetchone()[0]

    transactions_count = conn.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0]

    year_row = conn.execute(
        "SELECT MIN(CAST(strftime('%Y', posted) AS INTEGER)), "
        "       MAX(CAST(strftime('%Y', posted) AS INTEGER)) "
        "FROM transactions"
    ).fetchone()
    year_min: int | None = year_row[0]
    year_max: int | None = year_row[1]

    # Extend min/max with annual_totals years.
    at_row = conn.execute(
        "SELECT MIN(year), MAX(year) FROM annual_totals"
    ).fetchone()
    if at_row[0] is not None:
        year_min = min(year_min, at_row[0]) if year_min else at_row[0]
        year_max = max(year_max, at_row[1]) if year_max else at_row[1]

    return {
        "connected": connected,
        "last_refresh": last_refresh,
        "accounts_count": accounts_count,
        "transactions_count": transactions_count,
        "year_min": year_min,
        "year_max": year_max,
    }


# ---------------------------------------------------------------------------
# GET /api/categories
# ---------------------------------------------------------------------------

@app.get("/api/categories")
def api_categories() -> dict:
    return {"categories": list(CANONICAL_CATEGORIES)}


# ---------------------------------------------------------------------------
# GET /api/accounts
# ---------------------------------------------------------------------------

# Desired group order and human labels.
_GROUP_ORDER = ["depository", "credit", "investment"]
_GROUP_LABELS = {
    "depository": "Cash",
    "credit": "Credit Cards",
    "investment": "Investments",
}


@app.get("/api/accounts")
def api_accounts(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    last_refresh = db.get_meta(conn, "last_refresh")

    rows = conn.execute(
        "SELECT id, name, org, type, balance, available_balance, currency, "
        "       balance_date, active "
        "FROM accounts WHERE active = 1 "
        "ORDER BY type, name"
    ).fetchall()

    # Bucket by type.
    buckets: dict[str, list[dict]] = defaultdict(list)
    net_worth = 0.0
    for r in rows:
        acct = {
            "id": r["id"],
            "name": r["name"],
            "org": r["org"],
            "type": r["type"],
            "balance": r["balance"],
            "available_balance": r["available_balance"],
            "currency": r["currency"],
            "balance_date": r["balance_date"],
            "active": bool(r["active"]),
        }
        buckets[r["type"] or "other"].append(acct)
        net_worth += r["balance"] or 0.0

    # Build ordered group list.
    seen: set[str] = set()
    groups: list[dict] = []

    for acct_type in _GROUP_ORDER:
        if acct_type in buckets:
            accts = buckets.pop(acct_type)
            groups.append({
                "type": acct_type,
                "label": _GROUP_LABELS.get(acct_type, acct_type.title()),
                "total": round(sum(a["balance"] or 0.0 for a in accts), 2),
                "accounts": accts,
            })
            seen.add(acct_type)

    # Remaining types in alphabetical order.
    for acct_type in sorted(buckets.keys()):
        accts = buckets[acct_type]
        groups.append({
            "type": acct_type,
            "label": acct_type.title(),
            "total": round(sum(a["balance"] or 0.0 for a in accts), 2),
            "accounts": accts,
        })

    return {
        "net_worth": round(net_worth, 2),
        "last_refresh": last_refresh,
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# GET /api/transactions
# ---------------------------------------------------------------------------

_SORT_MAP = {
    "date_desc":   "posted DESC, id DESC",
    "date_asc":    "posted ASC,  id ASC",
    "amount_desc": "amount DESC, posted DESC",
    "amount_asc":  "amount ASC,  posted DESC",
}


@app.get("/api/transactions")
def api_transactions(
    start: str | None = None,
    end: str | None = None,
    account_id: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "date_desc",
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    where_clauses: list[str] = []
    params: list = []

    if start:
        where_clauses.append("posted >= ?")
        params.append(start)
    if end:
        where_clauses.append("posted <= ?")
        params.append(end)
    if account_id:
        where_clauses.append("account_id = ?")
        params.append(account_id)
    if category:
        where_clauses.append("category = ?")
        params.append(category)
    if q:
        where_clauses.append("(description LIKE ? OR CAST(amount AS TEXT) LIKE ?)")
        params.append(f"%{q}%")
        params.append(f"%{q}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    order_sql = _SORT_MAP.get(sort, _SORT_MAP["date_desc"])

    # Total count + sum over all matching rows.
    agg_row = conn.execute(
        f"SELECT COUNT(*), SUM(amount) FROM transactions {where_sql}",
        params,
    ).fetchone()
    total: int = agg_row[0] or 0
    total_sum: float = agg_row[1] or 0.0

    # Paginated rows. has_amazon flags rows with matched itemized Amazon detail.
    # No table alias: where_sql/order_sql columns are unambiguous against the
    # single transactions table; the EXISTS subquery uses its own alias.
    rows = conn.execute(
        f"SELECT id, posted, account_name, description, amount, category, source, pending, "
        f"       description_overridden, "
        f"       COALESCE(raw_description, description) AS raw_description, "
        f"       EXISTS(SELECT 1 FROM amazon_items a WHERE a.matched_txn_id = transactions.id) AS has_amazon "
        f"FROM transactions {where_sql} "
        f"ORDER BY {order_sql} "
        f"LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return {
        "total": total,
        "sum": round(total_sum, 2),
        "transactions": [
            {
                "id": r["id"],
                "posted": r["posted"],
                "account_name": r["account_name"],
                "description": r["description"],
                # The label as imported — the create-rule dialog matches on this,
                # not on a display name a rule may already have replaced.
                "raw_description": r["raw_description"],
                "amount": r["amount"],
                "category": r["category"],
                "source": r["source"],
                "pending": bool(r["pending"]),
                "description_overridden": bool(r["description_overridden"]),
                "has_amazon": bool(r["has_amazon"]),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# POST /api/transactions/{txn_id}/category
# ---------------------------------------------------------------------------

class CategoryBody(BaseModel):
    category: str


@app.post("/api/transactions/{txn_id}/category")
def set_category(
    txn_id: str,
    body: CategoryBody,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    if body.category not in CANONICAL_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category: {body.category!r}. "
                   f"Valid: {CANONICAL_CATEGORIES}",
        )
    db.set_override(conn, txn_id, body.category)
    conn.commit()
    return {"ok": True, "category": body.category}


# ---------------------------------------------------------------------------
# POST /api/transactions/bulk-category
# ---------------------------------------------------------------------------

class BulkCategoryBody(BaseModel):
    txn_ids: list[str]
    category: str


@app.post("/api/transactions/bulk-category")
def bulk_set_category(
    body: BulkCategoryBody,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    if body.category not in CANONICAL_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category: {body.category!r}. "
                   f"Valid: {CANONICAL_CATEGORIES}",
        )
    for tid in body.txn_ids:
        db.set_override(conn, tid, body.category)
    conn.commit()
    return {"ok": True, "count": len(body.txn_ids)}


# ---------------------------------------------------------------------------
# Merchant rules — /api/merchant-rules
#
# A rule matches a case-insensitive substring of raw_description and renames
# and/or recategorizes every hit, past and future. See app/merchant_rules.py
# for the precedence model.
# ---------------------------------------------------------------------------

class MerchantRuleBody(BaseModel):
    pattern: str
    description: str | None = None
    category: str | None = None
    priority: int = 100


class MerchantRulePatch(BaseModel):
    pattern: str | None = None
    description: str | None = None
    category: str | None = None
    priority: int | None = None
    active: int | None = None


class PreviewBody(BaseModel):
    pattern: str


def _validate_category(category: str | None) -> None:
    if category and category not in CANONICAL_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category: {category!r}. Valid: {CANONICAL_CATEGORIES}",
        )


def _reapply(conn: sqlite3.Connection) -> int:
    """Re-run merchant rules, then re-assert manual per-transaction overrides.

    Order matters: merchant_rules.apply_all resets each row to its untouched
    original before matching, so the manual overrides have to go back on top.
    """
    changed = merchant_rules.apply_all(conn)
    db.apply_overrides(conn)
    db.apply_description_overrides(conn)
    conn.commit()
    return changed


@app.get("/api/merchant-rules")
def api_merchant_rules(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"rules": merchant_rules.list_rules(conn)}


@app.post("/api/merchant-rules/preview")
def api_merchant_rule_preview(
    body: PreviewBody,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return merchant_rules.preview(conn, body.pattern)


@app.post("/api/merchant-rules")
def api_create_merchant_rule(
    body: MerchantRuleBody,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    _validate_category(body.category)
    try:
        rule_id = merchant_rules.create_rule(
            conn, body.pattern, body.description, body.category, body.priority
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    changed = _reapply(conn)
    return {"ok": True, "id": rule_id, "applied": changed}


@app.patch("/api/merchant-rules/{rule_id}")
def api_update_merchant_rule(
    rule_id: int,
    body: MerchantRulePatch,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not merchant_rules.get_rule(conn, rule_id):
        raise HTTPException(status_code=404, detail=f"No rule {rule_id}")
    _validate_category(body.category)
    try:
        merchant_rules.update_rule(conn, rule_id, **body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    changed = _reapply(conn)
    return {"ok": True, "applied": changed}


@app.delete("/api/merchant-rules/{rule_id}")
def api_delete_merchant_rule(
    rule_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not merchant_rules.get_rule(conn, rule_id):
        raise HTTPException(status_code=404, detail=f"No rule {rule_id}")
    merchant_rules.delete_rule(conn, rule_id)
    # apply_all resets every row to its original first, so deleting a rule
    # reverts exactly the transactions it had renamed.
    changed = _reapply(conn)
    return {"ok": True, "reverted": changed}


# ---------------------------------------------------------------------------
# GET /api/analysis
# ---------------------------------------------------------------------------

@app.get("/api/analysis")
def api_analysis(
    start_year: int | None = None,
    end_year: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    spending_cats = [c for c in CANONICAL_CATEGORIES if c not in EXCLUDED_FROM_SPENDING]

    # ---- Determine year range ----
    # Min/max from transactions.
    txn_yr = conn.execute(
        "SELECT MIN(CAST(strftime('%Y', posted) AS INTEGER)), "
        "       MAX(CAST(strftime('%Y', posted) AS INTEGER)) "
        "FROM transactions"
    ).fetchone()
    at_yr = conn.execute(
        "SELECT MIN(year), MAX(year) FROM annual_totals"
    ).fetchone()

    all_mins = [y for y in [txn_yr[0], at_yr[0]] if y is not None]
    all_maxs = [y for y in [txn_yr[1], at_yr[1]] if y is not None]

    data_min = min(all_mins) if all_mins else None
    data_max = max(all_maxs) if all_maxs else None

    y_min = max(start_year, data_min) if (start_year and data_min) else (start_year or data_min)
    y_max = min(end_year,   data_max) if (end_year   and data_max) else (end_year   or data_max)

    if y_min is None or y_max is None:
        return {"years": [], "categories": [], "matrix": {}, "totals_by_year": {}}

    years = list(range(y_min, y_max + 1))

    # ---- Annual totals rows (2013-2017) ----
    # amount is already a positive spending figure.
    placeholders = ",".join("?" * len(years))
    at_rows = conn.execute(
        f"SELECT year, category, amount FROM annual_totals "
        f"WHERE year IN ({placeholders})",
        years,
    ).fetchall()

    # ---- Transaction-derived spending (2018+) ----
    excluded_placeholders = ",".join("?" * len(EXCLUDED_FROM_SPENDING))
    txn_rows = conn.execute(
        f"SELECT CAST(strftime('%Y', posted) AS INTEGER) AS yr, "
        f"       category, "
        f"       -SUM(amount) AS spending "
        f"FROM transactions "
        f"WHERE CAST(strftime('%Y', posted) AS INTEGER) IN ({placeholders}) "
        f"  AND category NOT IN ({excluded_placeholders}) "
        f"GROUP BY yr, category",
        years + list(EXCLUDED_FROM_SPENDING),
    ).fetchall()

    # ---- Build matrix ----
    # matrix[category][str(year)] = amount
    matrix: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for r in at_rows:
        if r["category"] in spending_cats:
            matrix[r["category"]][str(r["year"])] += round(r["amount"], 2)

    for r in txn_rows:
        cat = r["category"]
        if cat in spending_cats:
            matrix[cat][str(r["yr"])] += round(r["spending"], 2)

    # Round final values.
    final_matrix: dict[str, dict[str, float]] = {}
    for cat, yr_map in matrix.items():
        final_matrix[cat] = {yr: round(v, 2) for yr, v in yr_map.items()}

    # ---- Totals by year ----
    totals_by_year: dict[str, float] = {}
    for yr in years:
        s = 0.0
        for yr_map in final_matrix.values():
            s += yr_map.get(str(yr), 0.0)
        totals_by_year[str(yr)] = round(s, 2)

    # ---- Sort categories by total desc ----
    cat_totals = {
        cat: sum(yr_map.values())
        for cat, yr_map in final_matrix.items()
    }
    sorted_cats = sorted(final_matrix.keys(), key=lambda c: -cat_totals.get(c, 0.0))

    return {
        "years": years,
        "categories": sorted_cats,
        "matrix": final_matrix,
        "totals_by_year": totals_by_year,
    }


# ---------------------------------------------------------------------------
# POST /api/refresh
# ---------------------------------------------------------------------------

@app.post("/api/refresh")
def api_refresh(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    if not simplefin.is_connected():
        return {
            "ok": False,
            "connected": False,
            "message": "No SimpleFIN access configured.",
        }

    access_url = simplefin.load_access_url()

    # Choose start_date window.
    last_refresh = db.get_meta(conn, "last_refresh")
    if last_refresh is None:
        # First-ever refresh: go back ~2 years.
        start_dt = datetime.now(timezone.utc) - timedelta(days=365 * 2)
    else:
        # Subsequent: ~120 days back for overlap/safety.
        start_dt = datetime.now(timezone.utc) - timedelta(days=120)
    start_epoch = int(start_dt.timestamp())

    data = simplefin.fetch_normalized(access_url, start_date=start_epoch)

    accounts_seen = 0
    transactions_seen = 0
    today_date = datetime.now(timezone.utc).astimezone().date().isoformat()

    for acct in data["accounts"]:
        db.upsert_account(
            conn,
            {
                "id": acct["id"],
                "name": acct["name"],
                "org": acct.get("org"),
                "type": acct.get("type"),
                "currency": acct.get("currency", "USD"),
                "balance": acct.get("balance"),
                "available_balance": acct.get("available_balance"),
                "balance_date": acct.get("balance_date"),
                "last_seen": _now_iso(),
                "active": True,
            },
        )
        db.write_balance_snapshot(conn, acct["id"], today_date, acct.get("balance"))
        accounts_seen += 1

        for txn in acct.get("transactions", []):
            cat = categorize(txn.get("description", ""), source_category=None)
            db.upsert_transaction(
                conn,
                {
                    "id": txn["id"],
                    "account_id": txn.get("account_id"),
                    "account_name": txn.get("account_name"),
                    "posted": txn.get("posted"),
                    "amount": txn.get("amount"),
                    "description": txn.get("description", ""),
                    "payee": txn.get("payee"),
                    "memo": txn.get("memo"),
                    "pending": txn.get("pending", False),
                    "source": "simplefin",
                    "source_category": None,
                    "category": cat,
                },
            )
            transactions_seen += 1

    # Merchant rules first, then manual overrides on top — this is what makes a
    # saved rule apply automatically to newly-pulled transactions.
    merchant_rules.apply_all(conn)
    db.apply_overrides(conn)
    db.apply_description_overrides(conn)
    now = _now_iso()
    db.set_meta(conn, "last_refresh", now)
    conn.commit()

    return {
        "ok": True,
        "connected": True,
        "accounts": accounts_seen,
        "transactions_seen": transactions_seen,
        "errors": data.get("errors", []),
        "last_refresh": now,
    }


# ---------------------------------------------------------------------------
# POST /api/setup
# ---------------------------------------------------------------------------

class SetupBody(BaseModel):
    setup_token: str


@app.post("/api/setup")
def api_setup(body: SetupBody) -> dict:
    try:
        access_url = simplefin.claim_access_url(body.setup_token)
        simplefin.save_config(access_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "message": str(exc)})
    return {"ok": True, "message": "Connected."}


# ---------------------------------------------------------------------------
# POST /api/import-history
# ---------------------------------------------------------------------------

@app.post("/api/import-history")
def api_import_history() -> dict:
    try:
        summary = import_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"ok": False, "message": str(exc)})
    return summary


# ---------------------------------------------------------------------------
# Balance history helper
# ---------------------------------------------------------------------------

def _account_balance_series(
    conn: sqlite3.Connection, account_row
) -> list[tuple[str, float]]:
    """Return [(iso_date, balance), ...] ascending for a single account.

    For depository/credit accounts the history is reconstructed from
    transactions by working backward from the current balance.
    For investment (and other) accounts, balance_snapshots are used.
    """
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    acct_id = account_row["id"]
    acct_type = (account_row["type"] or "").lower()
    current_balance = account_row["balance"] or 0.0

    if acct_type in ("depository", "credit"):
        txn_rows = conn.execute(
            "SELECT posted, amount FROM transactions "
            "WHERE account_id = ? AND posted IS NOT NULL "
            "ORDER BY posted ASC",
            (acct_id,),
        ).fetchall()

        if not txn_rows:
            return [(today, current_balance)]

        # sum of all transaction amounts
        total = sum(r["amount"] for r in txn_rows)
        # balance before any transactions = current_balance - total
        start_balance = current_balance - total

        # Group amounts by date and compute running balance at end of each date.
        from collections import OrderedDict
        daily: dict[str, float] = {}
        for r in txn_rows:
            d = r["posted"]
            daily[d] = daily.get(d, 0.0) + r["amount"]

        series: list[tuple[str, float]] = []
        running = start_balance
        for d in sorted(daily.keys()):
            running += daily[d]
            series.append((d, round(running, 2)))

        # Ensure last point exactly matches current_balance (floating-point guard).
        if series:
            last_date, _ = series[-1]
            series[-1] = (last_date, round(current_balance, 2))

        return series

    else:
        # Investment / other: use snapshots.
        snapshots = db.get_balance_history(conn, acct_id)
        if snapshots:
            return [(s["date"], s["balance"] or 0.0) for s in snapshots]
        return [(today, current_balance)]


# ---------------------------------------------------------------------------
# Helper: build date axis and downsample
# ---------------------------------------------------------------------------

# Floor for history charts. A few source rows have a null posted date that
# collapses to the UNIX epoch (1970-01-01); ignore anything before this when
# building a chart axis so one bad row can't stretch the whole timeline.
_HISTORY_FLOOR = "2005-01-01"


def _downsample_axis(dates: list[str]) -> list[str]:
    """Reduce a dense date list to at most ~500 points.

    If > 500 dates: keep one per calendar month (the last date present in
    each year-month) plus the final date.
    """
    if len(dates) <= 500:
        return dates

    by_month: dict[str, str] = {}
    for d in dates:
        ym = d[:7]  # "YYYY-MM"
        by_month[ym] = d  # last date in each month wins (dates are sorted)

    reduced = sorted(by_month.values())
    # Always include the final date.
    if dates[-1] not in reduced:
        reduced.append(dates[-1])
    return reduced


def _fill_series(
    series: list[tuple[str, float]], axis: list[str]
) -> list[float]:
    """Forward-fill (and backward-fill if axis starts before series) a series
    onto the given date axis.

    * Before the first series date: use the first series value (flat backward).
    * After each date in the series: carry forward until a newer date appears.
    """
    if not series:
        return [0.0] * len(axis)

    result: list[float] = []
    s_idx = 0
    n = len(series)

    for ax_date in axis:
        # Advance pointer while next series date <= ax_date.
        while s_idx + 1 < n and series[s_idx + 1][0] <= ax_date:
            s_idx += 1
        # If ax_date is before the first series point, use first value.
        if ax_date < series[0][0]:
            result.append(series[0][1])
        else:
            result.append(series[s_idx][1])

    return result


# ---------------------------------------------------------------------------
# GET /api/networth/history
# ---------------------------------------------------------------------------

@app.get("/api/networth/history")
def api_networth_history(
    start: str | None = None,
    end: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()

    rows = conn.execute(
        "SELECT id, name, type, balance FROM accounts WHERE active = 1"
    ).fetchall()

    if not rows:
        return {"dates": [], "net_worth": [], "by_type": {"depository": [], "credit": [], "investment": []}}

    # Compute per-account series and collect all dates.
    all_dates_set: set[str] = {today}
    account_series: list[tuple[dict, list[tuple[str, float]]]] = []
    for r in rows:
        s = _account_balance_series(conn, r)
        account_series.append((dict(r), s))
        for d, _ in s:
            all_dates_set.add(d)

    # Build sorted date axis, optionally filtered.
    axis = sorted(d for d in all_dates_set if d >= _HISTORY_FLOOR)
    if start:
        axis = [d for d in axis if d >= start]
    if end:
        axis = [d for d in axis if d <= end]
    if not axis:
        return {"dates": [], "net_worth": [], "by_type": {"depository": [], "credit": [], "investment": []}}

    axis = _downsample_axis(axis)

    # Accumulate by type.
    _TYPE_BUCKET = {"depository": "depository", "credit": "credit", "investment": "investment"}
    by_type: dict[str, list[float]] = {
        "depository": [0.0] * len(axis),
        "credit":     [0.0] * len(axis),
        "investment": [0.0] * len(axis),
    }
    net_worth_arr = [0.0] * len(axis)

    for acct, series in account_series:
        acct_type = (acct.get("type") or "").lower()
        bucket = _TYPE_BUCKET.get(acct_type, None)
        filled = _fill_series(series, axis)
        for i, v in enumerate(filled):
            net_worth_arr[i] = round(net_worth_arr[i] + v, 2)
            if bucket:
                by_type[bucket][i] = round(by_type[bucket][i] + v, 2)

    return {
        "dates": axis,
        "net_worth": [round(v, 2) for v in net_worth_arr],
        "by_type": {k: [round(v, 2) for v in vs] for k, vs in by_type.items()},
    }


# ---------------------------------------------------------------------------
# GET /api/accounts/{account_id}/history
# ---------------------------------------------------------------------------

@app.get("/api/accounts/{account_id}/history")
def api_account_history(
    account_id: str,
    start: str | None = None,
    end: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()

    row = conn.execute(
        "SELECT id, name, type, balance FROM accounts WHERE id = ? AND active = 1",
        (account_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")

    series = _account_balance_series(conn, row)

    # Build date axis from series dates + today.
    all_dates = sorted(d for d in ({d for d, _ in series} | {today}) if d >= _HISTORY_FLOOR)
    if start:
        all_dates = [d for d in all_dates if d >= start]
    if end:
        all_dates = [d for d in all_dates if d <= end]
    if not all_dates:
        return {
            "account": dict(row),
            "dates": [],
            "balances": [],
        }

    axis = _downsample_axis(all_dates)
    balances = _fill_series(series, axis)

    return {
        "account": {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "balance": row["balance"],
        },
        "dates": axis,
        "balances": [round(v, 2) for v in balances],
    }


# ---------------------------------------------------------------------------
# POST /api/transactions/{txn_id}/description
# ---------------------------------------------------------------------------

class DescriptionBody(BaseModel):
    description: str


@app.post("/api/transactions/{txn_id}/description")
def set_description(
    txn_id: str,
    body: DescriptionBody,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    description = body.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="description must not be empty")
    db.set_description_override(conn, txn_id, description)
    conn.commit()
    return {"ok": True, "description": description}


# ---------------------------------------------------------------------------
# GET /api/transactions/{txn_id}/amazon-items
# ---------------------------------------------------------------------------

@app.get("/api/transactions/{txn_id}/amazon-items")
def api_amazon_items(
    txn_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Itemized Amazon order detail matched to a card transaction (#1)."""
    rows = conn.execute(
        "SELECT order_date, asin, product_name, quantity, total_owed, card_last4 "
        "FROM amazon_items WHERE matched_txn_id = ? ORDER BY order_date, product_name",
        (txn_id,),
    ).fetchall()
    return {
        "items": [
            {
                "order_date": r["order_date"],
                "asin": r["asin"],
                "product_name": r["product_name"],
                "quantity": r["quantity"],
                "total_owed": r["total_owed"],
                "card_last4": r["card_last4"],
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# POST /api/import-csv
# ---------------------------------------------------------------------------

_CSV_SOURCES = {"paypal", "venmo", "amazon"}


@app.post("/api/import-csv")
def api_import_csv(
    source: str = Form(...),
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    # NOTE: keep this a *sync* endpoint. The get_db dependency yields a SQLite
    # connection bound to the thread it was created on; FastAPI runs sync
    # endpoints and their sync dependencies in the same threadpool thread, so
    # the connection is used on its own thread. An async endpoint would run on
    # the event loop while the connection lives in a threadpool thread, raising
    # "SQLite objects created in a thread can only be used in that same thread".
    if source not in _CSV_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source {source!r}. Valid: {sorted(_CSV_SOURCES)}",
        )

    raw = file.file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Could not decode file: {exc}")

    try:
        if source == "paypal":
            summary = _csv_accounts.import_paypal(conn, text)
        elif source == "venmo":
            summary = _csv_accounts.import_venmo(conn, text)
        else:  # amazon
            summary = _csv_accounts.import_amazon(conn, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    merchant_rules.apply_all(conn)
    db.apply_overrides(conn)
    db.apply_description_overrides(conn)
    conn.commit()
    return summary


# ---------------------------------------------------------------------------
# Merchant normalization (shared by subscriptions + merchants endpoints)
# ---------------------------------------------------------------------------

def _normalize_merchant(desc: str) -> str:
    """Strip order IDs / suffixes so recurring charges from the same merchant
    group consistently — without over-collapsing distinct merchants.

    Note on '*': processors (SQ*, TST*, MED*, SP*) put the *merchant* after the
    star, so we only drop the part after '*' when it looks like an order id
    (short and containing a digit). "MED*HARVARD MEDICAL" is kept intact;
    "AMZN MKTP US*2X4AB1" becomes "AMZN MKTP US".
    """
    d = (desc or '').upper().strip()

    if '*' in d:
        head, _, tail = d.partition('*')
        head, tail = head.strip(), tail.strip()
        if head and re.search(r'\d', tail) and len(tail.split()) <= 2:
            d = head  # tail is an order id → drop it

    # Drop trailing tokens that are pure numbers, "#123", or id-like (contain a digit).
    d = re.sub(r'\s+#?\d+$', '', d)
    d = re.sub(r'\s+[A-Z0-9]*\d[A-Z0-9]*$', '', d)
    return ' '.join(d.split())


def _merchant_search_term(norm_name: str) -> str:
    """A short, contiguous substring of the merchant name suitable for a
    description LIKE search — stops at ACH field markers (DES:, ID:, CO ID) and
    caps at 4 words so it still matches descriptions with irregular whitespace.
    """
    out: list[str] = []
    for w in norm_name.split():
        if ':' in w:           # ACH field marker like DES:, ID:
            break
        out.append(w)
        if len(out) >= 4:
            break
    return ' '.join(out) if out else norm_name


# Short, generic leading tokens that aren't distinctive on their own (payment
# processor / channel prefixes) — extend the history probe to a second word.
_GENERIC_PREFIX = {'SP', 'TST', 'SQ', 'MED', 'POS', 'ACH', 'WWW', 'THE', 'REC'}


def _history_probe(norm_name: str) -> str:
    """A robust stem for checking whether a merchant appears earlier in history,
    tolerant of cross-era description differences (e.g. SimpleFIN
    "NETFLIX.COM LOS GATOS CA" vs an older import's "Netflix"). Uses the leading
    alphanumeric stem of the first token, extended to a second word when the
    first is a short/generic processor prefix.
    """
    toks = norm_name.split()
    if not toks:
        return ''
    m = re.match(r'[A-Z0-9&]+', toks[0])
    stem = m.group(0) if m else toks[0]
    if (stem in _GENERIC_PREFIX or len(stem) < 4) and len(toks) > 1:
        m2 = re.match(r'[A-Z0-9&]+', toks[1])
        stem = stem + ' ' + (m2.group(0) if m2 else toks[1])
    return stem


# ---------------------------------------------------------------------------
# GET /api/subscriptions
# ---------------------------------------------------------------------------

@app.get("/api/subscriptions")
def api_subscriptions(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    from datetime import date as _date

    today = datetime.now(timezone.utc).astimezone().date()
    lookback = (today - timedelta(days=400)).isoformat()

    excluded = list(EXCLUDED_FROM_SPENDING)
    ph = ','.join('?' * len(excluded))

    rows = conn.execute(
        f"""SELECT description, posted, amount, category
            FROM transactions
            WHERE posted >= ? AND amount < 0
            AND category NOT IN ({ph})
            ORDER BY posted""",
        [lookback] + excluded,
    ).fetchall()

    # Group by normalized merchant
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        key = _normalize_merchant(r['description'])
        if key:
            groups[key].append({
                'posted': r['posted'],
                'amount': abs(r['amount']),
                'description': r['description'],
                'category': r['category'],
            })

    subscriptions = []

    for key, charges in groups.items():
        charges.sort(key=lambda x: x['posted'])

        # Aggregate same-day charges (e.g. two kids on same plan)
        daily_amt: dict[str, float] = defaultdict(float)
        daily_desc: dict[str, str] = {}
        daily_cat: dict[str, str] = {}
        for c in charges:
            d = c['posted']
            daily_amt[d] += c['amount']
            daily_desc[d] = c['description']
            daily_cat[d] = c['category']

        dated = sorted(daily_amt.keys())
        # Need at least 3 occurrences to credibly call something recurring (two
        # points is a single interval — too easy to hit by coincidence).
        if len(dated) < 3:
            continue

        amounts = [round(daily_amt[d], 2) for d in dated]

        date_objs = []
        for d in dated:
            try:
                date_objs.append(_date.fromisoformat(d[:10]))
            except Exception:
                continue
        if len(date_objs) < 3:
            continue

        intervals = [(date_objs[i + 1] - date_objs[i]).days for i in range(len(date_objs) - 1)]

        def _is_cadence(ivs: list[int], lo: int, hi: int) -> bool:
            return all(lo <= iv <= hi for iv in ivs)

        if _is_cadence(intervals, 6, 16):
            cadence, multiplier, max_dormant = 'biweekly', 2.17, 25
        elif _is_cadence(intervals, 18, 45):
            cadence, multiplier, max_dormant = 'monthly', 1.0, 55
        elif _is_cadence(intervals, 75, 105):
            cadence, multiplier, max_dormant = 'quarterly', 1 / 3, 125
        elif _is_cadence(intervals, 300, 400):
            cadence, multiplier, max_dormant = 'annual', 1 / 12, 420
        else:
            continue

        current_amount = amounts[-1]
        monthly_cost = current_amount * multiplier
        last_date = date_objs[-1]
        first_date = date_objs[0]
        days_since_last = (today - last_date).days

        # Amount stability: a real subscription bills a consistent amount.
        mean_amt = sum(amounts) / len(amounts)
        cv = (statistics.pstdev(amounts) / mean_amt) if mean_amt > 0 else 1.0
        stable = cv <= 0.25

        # Reject coincidentally-recurring merchants (a restaurant or hardware
        # store visited ~monthly): keep only charges that read like a real
        # recurring bill — a stable amount, or one large enough to matter.
        if not stable and monthly_cost < 50:
            continue

        # Price change: latest charge vs the median of the earlier ones, with a
        # threshold (>$1 and >5%) so variable utility bills don't always flag.
        prior = amounts[:-1]
        median_prior = statistics.median(prior) if prior else current_amount
        price_changed = (
            median_prior > 0
            and abs(current_amount - median_prior) > max(1.0, 0.05 * median_prior)
        )

        inactive = days_since_last > max_dormant
        # Genuinely new only if this merchant has no earlier history at all
        # (tolerant of cross-era description differences) AND first appeared
        # recently. Guards against flagging long-standing subscriptions as new
        # just because live data started a few months ago.
        is_new = False
        if (today - first_date).days <= 120:
            probe = _history_probe(key)
            if len(probe.replace(' ', '')) >= 4:
                earlier = conn.execute(
                    "SELECT 1 FROM transactions "
                    "WHERE posted < ? AND UPPER(description) LIKE ? LIMIT 1",
                    (first_date.isoformat(), f'%{probe}%'),
                ).fetchone()
                is_new = earlier is None

        subscriptions.append({
            'name': key,
            'search': _merchant_search_term(key),
            'display_name': daily_desc[dated[-1]],
            'category': daily_cat[dated[-1]],
            'cadence': cadence,
            'current_amount': round(current_amount, 2),
            'monthly_cost': round(monthly_cost, 2),
            'last_date': last_date.isoformat(),
            'first_date': first_date.isoformat(),
            'charge_count': len(dated),
            'inactive': inactive,
            'is_new': is_new,
            'price_changed': price_changed,
            'days_since_last': days_since_last,
        })

    # Drop stale "inactive" entries that are really the older-format history of
    # a still-active subscription (different description era → separate group).
    active_probes = {
        _history_probe(s['name'])
        for s in subscriptions
        if not s['inactive'] and _history_probe(s['name'])
    }
    subscriptions = [
        s for s in subscriptions
        if not (s['inactive'] and _history_probe(s['name']) in active_probes)
    ]

    subscriptions.sort(key=lambda s: (s['inactive'], -s['monthly_cost']))
    total_monthly = sum(s['monthly_cost'] for s in subscriptions if not s['inactive'])

    return {
        'subscriptions': subscriptions,
        'total_monthly': round(total_monthly, 2),
        'total_annual': round(total_monthly * 12, 2),
    }


# ---------------------------------------------------------------------------
# GET /api/anomalies
# ---------------------------------------------------------------------------

@app.get("/api/anomalies")
def api_anomalies(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    today = datetime.now(timezone.utc).astimezone().date()
    today_iso = today.isoformat()

    current_start = (today - timedelta(days=30)).isoformat()
    prior_start   = (today - timedelta(days=120)).isoformat()
    prior_end     = (today - timedelta(days=31)).isoformat()
    recent_start  = (today - timedelta(days=45)).isoformat()
    history_start = (today - timedelta(days=225)).isoformat()

    excluded = list(EXCLUDED_FROM_SPENDING)
    ph = ','.join('?' * len(excluded))

    # Category totals: current 30 days
    current_cats = conn.execute(
        f"""SELECT category, -SUM(amount) as total
            FROM transactions
            WHERE posted >= ? AND posted <= ?
            AND amount < 0 AND category NOT IN ({ph})
            GROUP BY category""",
        [current_start, today_iso] + excluded,
    ).fetchall()

    # Category monthly avg: prior 90 days
    prior_cats = conn.execute(
        f"""SELECT category, -SUM(amount) / 3.0 as monthly_avg
            FROM transactions
            WHERE posted >= ? AND posted <= ?
            AND amount < 0 AND category NOT IN ({ph})
            GROUP BY category""",
        [prior_start, prior_end] + excluded,
    ).fetchall()

    prior_map = {r['category']: r['monthly_avg'] for r in prior_cats}

    spikes = []
    for r in current_cats:
        cat, current = r['category'], r['total']
        avg = prior_map.get(cat, 0)
        if avg > 50 and current > avg * 1.4 and (current - avg) > 100:
            spikes.append({
                'category': cat,
                'current_30d': round(current, 2),
                'prior_monthly_avg': round(avg, 2),
                'pct_change': round((current - avg) / avg * 100, 1),
            })
    spikes.sort(key=lambda s: -(s['current_30d'] - s['prior_monthly_avg']))

    # New merchants: last 45 days but not in prior 6 months
    recent_descs: set[str] = {
        r['description']
        for r in conn.execute(
            f"""SELECT DISTINCT description FROM transactions
                WHERE posted >= ? AND amount < 0
                AND category NOT IN ({ph})""",
            [recent_start] + excluded,
        ).fetchall()
    }
    prior_descs: set[str] = {
        r['description']
        for r in conn.execute(
            "SELECT DISTINCT description FROM transactions WHERE posted >= ? AND posted < ?",
            [history_start, recent_start],
        ).fetchall()
    }

    # Merchant "stems" seen anytime before the recent window. Lets us suppress
    # merchants that only look new because their live (SimpleFIN-era)
    # description differs from an older import's format — e.g. an established
    # utility or membership. Tolerant of those cross-era format differences.
    prior_probes: set[str] = set()
    for r in conn.execute(
        "SELECT DISTINCT description FROM transactions WHERE posted < ?",
        [recent_start],
    ).fetchall():
        p = _history_probe(_normalize_merchant(r['description']))
        if len(p.replace(' ', '')) >= 4:
            prior_probes.add(p)

    new_merchants: list[dict] = []
    for desc in sorted(recent_descs - prior_descs):
        probe = _history_probe(_normalize_merchant(desc))
        if len(probe.replace(' ', '')) >= 4 and probe in prior_probes:
            continue  # established merchant, just a new description format
        txn_rows = conn.execute(
            f"""SELECT posted, amount, category FROM transactions
                WHERE description = ? AND posted >= ?
                AND amount < 0 AND category NOT IN ({ph})
                ORDER BY posted""",
            [desc, recent_start] + excluded,
        ).fetchall()
        if not txn_rows:
            continue
        new_merchants.append({
            'description': desc,
            'first_seen': txn_rows[0]['posted'],
            'category': txn_rows[0]['category'],
            'total_spent': round(sum(abs(r['amount']) for r in txn_rows), 2),
            'count': len(txn_rows),
        })
    new_merchants.sort(key=lambda m: -m['total_spent'])
    new_merchants = new_merchants[:25]

    # Large single transactions (> $1,000, last 45 days)
    large_txns = conn.execute(
        f"""SELECT id, posted, description, amount, category, account_name
            FROM transactions
            WHERE posted >= ? AND amount < -1000
            AND category NOT IN ({ph})
            ORDER BY amount ASC LIMIT 20""",
        [recent_start] + excluded,
    ).fetchall()

    return {
        'as_of': today_iso,
        'period_days': 30,
        'category_spikes': spikes,
        'new_merchants': new_merchants,
        'large_transactions': [
            {
                'id': r['id'],
                'posted': r['posted'],
                'description': r['description'],
                'amount': r['amount'],
                'category': r['category'],
                'account_name': r['account_name'],
            }
            for r in large_txns
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/merchants
# ---------------------------------------------------------------------------

@app.get("/api/merchants")
def api_merchants(
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
    limit: int = 50,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    excluded = list(EXCLUDED_FROM_SPENDING)
    ph = ','.join('?' * len(excluded))

    where: list[str] = [f"category NOT IN ({ph})", "amount < 0"]
    params: list = list(excluded)

    if start:
        where.append("posted >= ?")
        params.append(start)
    if end:
        where.append("posted <= ?")
        params.append(end)
    if category:
        where.append("category = ?")
        params.append(category)

    where_sql = "WHERE " + " AND ".join(where)

    rows = conn.execute(
        f"""SELECT description, category,
                   -SUM(amount) as total,
                   COUNT(*) as count,
                   MIN(posted) as first_date,
                   MAX(posted) as last_date,
                   -MIN(amount) as max_single
            FROM transactions {where_sql}
            GROUP BY description, category
            ORDER BY total DESC LIMIT ?""",
        params + [limit],
    ).fetchall()

    return {
        'merchants': [
            {
                'description': r['description'],
                'category': r['category'],
                'total': round(r['total'], 2),
                'count': r['count'],
                'first_date': r['first_date'],
                'last_date': r['last_date'],
                'max_single': round(r['max_single'], 2),
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )
