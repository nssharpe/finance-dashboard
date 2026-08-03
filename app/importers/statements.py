"""Importers for downloaded bank and card statements.

Parsers are organized by **export shape**, not by institution — banks disagree
about layout, but the number of distinct shapes is small, and most exports match
one that already exists. Which accounts you have, and which shape each one uses,
lives in the gitignored ``accounts.local.json`` (copy ``accounts.example.json``).

    parse_debit_credit_csv      money-out and money-in in two columns
    parse_signed_amount_csv     one Amount column, already signed
    parse_monthly_csv_dir       a directory of monthly files + a pending feed
    parse_positive_charge_xlsx  XLSX where a purchase is a POSITIVE number
    parse_paypal_pdfs           PayPal monthly PDF statements

``running_balance.py`` holds one more shape — a CSV carrying a running balance,
which is worth a separate module because that column makes the import verifiable
against the statement's own opening and closing figures.

Sign convention
---------------
The database stores **negative = money out** for every account. Exports disagree,
so each parser normalizes: two-column shapes negate the debit, signed shapes pass
through, and the XLSX shape is **inverted** and must be negated. Verify a new
format against data you already have before trusting it.

De-dup
------
Match on ``(amount, +/- 7 days)`` against every name the account has used, never
on description — the same transaction is described differently by different
sources. Each existing row is consumed at most once. Where a statement overlaps
data you already have, a near-100% match rate is the proof that a parser and its
sign are right; that overlap window is reported per account by ``--dry-run``.

Usage
-----
    python -m app.importers.statements <statements-dir> [--dry-run] [--only card_a]
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
from app.categorize import categorize, map_source_category

# +/-7 days: a card's transaction date can trail the bank feed's posted date by
# several days (one real charge sat at exactly 6). Tested at 5/7/9 — 7 recovers
# that row and nothing else moves, so it is not loose enough to invent matches.
MATCH_WINDOW_DAYS = 7

# Window that overlaps live SimpleFIN data — used as the parser sanity check.
OVERLAP_START = "2026-03-20"
OVERLAP_END = "2026-07-31"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


def _amt(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = str(raw).strip().replace(",", "").replace("$", "").replace("+", "")
    cleaned = re.sub(r"^-\s+", "-", cleaned)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d")
    s = str(raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _clean(desc: str) -> str:
    return re.sub(r"\s+", " ", (desc or "").strip())


# ---------------------------------------------------------------------------
# Per-account parsers.  Each returns [{posted, description, amount}, ...]
# with amount already signed negative-is-out.
# ---------------------------------------------------------------------------

def parse_debit_credit_csv(path: str) -> list[dict]:
    """CSV with money-out and money-in split across two columns.

    The most common credit-card export shape. Tolerates the usual header
    variations: the date may be ``Transaction Date``, ``Posted Date`` or plain
    ``Date``, and a ``Category`` column is used when present.

    An export may carry several card numbers for one account (a primary plus an
    authorized user). Every row is imported — never filter by card number.
    """
    out = []
    for row in csv.DictReader(io.StringIO(_read_text(path))):
        posted = _date(
            row.get("Transaction Date") or row.get("Posted Date") or row.get("Date")
        )
        if not posted:
            continue
        debit, credit = _amt(row.get("Debit")), _amt(row.get("Credit"))
        if debit:
            amount = -abs(debit)
        elif credit:
            amount = abs(credit)
        else:
            continue
        out.append({"posted": posted, "description": _clean(row.get("Description")),
                    "amount": round(amount, 2),
                    "source_category": (row.get("Category") or "").strip() or None})
    return out


def parse_signed_amount_csv(path: str) -> list[dict]:
    """CSV with a single ``Amount`` column that is already signed.

    Negative already means money out, so amounts pass through untouched.
    """
    out = []
    for row in csv.DictReader(io.StringIO(_read_text(path))):
        posted = _date(row.get("Transaction Date") or row.get("Post Date"))
        amount = _amt(row.get("Amount"))
        if not posted or amount is None:
            continue
        out.append({"posted": posted, "description": _clean(row.get("Description")),
                    "amount": round(amount, 2),
                    "source_category": (row.get("Category") or "").strip() or None})
    return out


def parse_monthly_csv_dir(path: str) -> list[dict]:
    """Nine monthly files plus a live ``currentTransaction`` feed that overlaps
    the newest month.  All are concatenated; the reference number makes true
    duplicates identifiable, so they are collapsed here before de-dup."""
    rows: dict[tuple, dict] = {}
    for fname in sorted(os.listdir(path)):
        if not fname.lower().endswith(".csv"):
            continue
        for row in csv.DictReader(io.StringIO(_read_text(os.path.join(path, fname)))):
            posted = _date(row.get("Posted Date"))
            amount = _amt(row.get("Amount"))
            if not posted or amount is None:
                continue
            ref = (row.get("Reference Number") or "").strip()
            desc = _clean(row.get("Payee"))
            # Reference number is unique per charge, so it de-dupes the
            # month-file/currentTransaction overlap exactly.
            key = (ref, posted, round(amount, 2)) if ref else (desc, posted, round(amount, 2))
            rows[key] = {"posted": posted, "description": desc, "amount": round(amount, 2)}
    return list(rows.values())


def parse_positive_charge_xlsx(path: str) -> list[dict]:
    """XLSX export where a purchase is a POSITIVE number — negated to match the DB.

    The header sits a few rows below a title/account block, so it is located by
    searching for the Date/Description/Amount row rather than assumed.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    header_idx = None
    for i, row in enumerate(rows):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if "Date" in cells and "Description" in cells and "Amount" in cells:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"No header row found in {path}")
    header = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    idx = {name: header.index(name) for name in ("Date", "Description", "Amount")}
    cat_idx = header.index("Category") if "Category" in header else None

    out = []
    for row in rows[header_idx + 1:]:
        posted = _date(row[idx["Date"]])
        amount = _amt(row[idx["Amount"]])
        if not posted or amount is None:
            continue
        raw_cat = str(row[cat_idx]).strip() if cat_idx is not None and row[cat_idx] else None
        out.append({"posted": posted, "description": _clean(str(row[idx["Description"]])),
                    "amount": round(-amount, 2), "source_category": raw_cat})
    return out


# --- PayPal ---------------------------------------------------------------

# A card-funded PayPal purchase appears as TWO lines that net to zero: the
# purchase itself and the funding leg. Tag the funding legs Transfer so only the
# purchase carries spend — same semantics as the existing import_paypal().
_PP_TRANSFER = re.compile(
    r"General Credit Card Deposit|General Credit Card Withdrawal|User Initiated Withdrawal"
    r"|Bank Deposit to PP Account|General Withdrawal|Add Money|Transfer to Bank"
    r"|General Card Deposit|Account Hold|Reversal of General Account Hold",
    re.IGNORECASE,
)

# A transaction line. The year is \d{2,4} because pdfplumber sometimes breaks
# the date across the line wrap ("03/06/202" with the trailing "6" starting the
# next line) — _pp_reflow stitches those back together before matching.
_PP_LINE = re.compile(
    r"^(\d{2}/\d{2}/\d{2,4})\s+(.*?)\s+USD\s+"
    r"(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$"
)

# Lines that end a transaction's description block rather than continuing it.
#
# The funding-source block is matched generically — a bank's legal name followed
# by its account line — rather than by naming one issuer. Hardcoding a single
# bank means a PayPal account funded by any other one silently gets that bank's
# name appended to whatever merchant it happens to follow.
_PP_STOP = re.compile(
    r"^(ID:|Ref ID:|Page\b|USD\b|PayPal Balance|"
    r"[A-Z][A-Z .,&'-]{3,},? ?N\.A\.|BANK OF |[A-Z][A-Z .&'-]{3,} BANK\b|"
    r"(Checking|Savings|Credit Card|Debit Card) x-|"
    r"ACCOUNT STATEMENTS|Statement Period|DATE DESCRIPTION|ACCOUNT ACTIVITY|"
    r"PAYPAL|\*|Contact us|no later than|The difference)",
    re.IGNORECASE,
)


def _pp_reflow(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Stitch wrapped dates and collect each transaction's continuation lines.

    Returns [(transaction_line, [continuation lines]), ...].  PayPal wraps long
    merchant names onto the following line ("Express Checkout Payment: Some"
    / "Synchronized Swimming Club"), and sometimes wraps the date itself, so the
    merchant is unrecoverable without looking ahead.
    """
    records: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _PP_LINE.match(line)
        if not m:
            i += 1
            continue
        date_part = m.group(1)
        cont: list[str] = []
        i += 1
        # A 3-digit year means the final digit begins the next line.
        if len(date_part) == 9 and i < len(lines):
            nxt = lines[i].strip()
            if nxt and nxt[0].isdigit():
                line = line.replace(date_part, date_part + nxt[0], 1)
                rest = nxt[1:].strip()
                i += 1
                if rest and not _PP_STOP.match(rest):
                    cont.append(rest)
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or _PP_STOP.match(nxt) or _PP_LINE.match(nxt):
                break
            cont.append(nxt)
            i += 1
        records.append((line, cont))
    return records


def parse_paypal_pdfs(path: str) -> list[dict]:
    """Parse PayPal monthly PDF statements.

    Each PDF contains TWO statements — ``PAYPAL ACCOUNT`` (all activity) and
    ``PAYPAL BALANCE ACCOUNT`` (a subset, repeated).  Only the former is read,
    or every balance-funded row would be counted twice.  Note the section-header
    test must be anchored to end-of-line: the cover blurb contains the prose
    "PayPal Balance account statement - ..." which would otherwise trip it.
    """
    import pdfplumber

    out = []
    for fname in sorted(os.listdir(path)):
        if not fname.lower().endswith(".pdf"):
            continue
        with pdfplumber.open(os.path.join(path, fname)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)

        kept: list[str] = []
        in_account = False
        for line in text.split("\n"):
            stripped = line.strip()
            if re.match(r"^PAYPAL BALANCE ACCOUNT\s*$", stripped, re.I):
                in_account = False
                continue
            if re.match(r"^PAYPAL ACCOUNT\s*$", stripped, re.I):
                in_account = True
                continue
            if in_account:
                kept.append(line)

        for txn_line, cont in _pp_reflow(kept):
            m = _PP_LINE.match(txn_line.strip())
            if not m:
                continue
            posted, desc, _amount, _fees, total = m.groups()
            iso = _date(posted)
            value = _amt(total)
            if not iso or value is None:
                continue
            full = _clean(desc + " " + " ".join(cont))
            out.append({
                "posted": iso,
                "description": full,
                "amount": round(value, 2),
                "force_category": "Transfer" if _PP_TRANSFER.search(full) else None,
            })
    return out


# ---------------------------------------------------------------------------
# Account registry
# ---------------------------------------------------------------------------

# Parsers are code; which accounts you have is personal configuration.
# ``accounts.local.json`` maps a key to {account_id, account_name, aliases,
# source, file, parser} and is gitignored — see ``accounts.example.json``.
# accounts.local.json is shared by every importer, so it also holds entries
# whose parser lives elsewhere. Skip those rather than erroring — but still
# reject a genuinely unknown name so a typo doesn't silently drop an account.
_OTHER_MODULE_PARSERS = {"parse_running_balance_csv"}

PARSERS = {
    "parse_debit_credit_csv": parse_debit_credit_csv,
    "parse_signed_amount_csv": parse_signed_amount_csv,
    "parse_monthly_csv_dir": parse_monthly_csv_dir,
    "parse_positive_charge_xlsx": parse_positive_charge_xlsx,
    "parse_paypal_pdfs": parse_paypal_pdfs,
}

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "accounts.local.json",
)


def load_accounts(path: str | None = None) -> dict:
    """Read the account registry, resolving each ``parser`` name to a function."""
    path = path or _REGISTRY_PATH
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    accounts = {}
    for key, spec in raw.items():
        parser_name = spec.get("parser")
        if parser_name in _OTHER_MODULE_PARSERS:
            continue  # belongs to a different importer; not ours to run
        if parser_name not in PARSERS:
            raise ValueError(
                f"accounts.local.json: account {key!r} names unknown parser "
                f"{parser_name!r}. Valid: {sorted(PARSERS) + sorted(_OTHER_MODULE_PARSERS)}"
            )
        accounts[key] = {**spec, "parser": PARSERS[parser_name]}
    return accounts


ACCOUNTS = load_accounts()


# ---------------------------------------------------------------------------
# Diff / insert (shared)
# ---------------------------------------------------------------------------

def _existing(conn, spec, lo, hi) -> list[dict]:
    clauses, params = ["account_id = ?"], [spec["account_id"]]
    for alias in spec["aliases"]:
        if "%" in alias:
            clauses.append("account_name LIKE ?")
        else:
            clauses.append("account_name = ?")
        params.append(alias)
    sql = (
        "SELECT id, posted, amount, source FROM transactions "
        f"WHERE ({' OR '.join(clauses)}) AND posted BETWEEN ? AND ?"
    )
    return [dict(r) for r in conn.execute(sql, (*params, lo, hi)).fetchall()]


def diff(conn: sqlite3.Connection, spec: dict, rows: list[dict]):
    """Split rows into (already-present, missing) — see module docstring."""
    if not rows:
        return [], []
    pad = timedelta(days=MATCH_WINDOW_DAYS)
    lo = (datetime.strptime(min(r["posted"] for r in rows), "%Y-%m-%d") - pad).strftime("%Y-%m-%d")
    hi = (datetime.strptime(max(r["posted"] for r in rows), "%Y-%m-%d") + pad).strftime("%Y-%m-%d")

    by_amount: dict[float, list[dict]] = {}
    for row in _existing(conn, spec, lo, hi):
        by_amount.setdefault(round(row["amount"], 2), []).append(row)

    used: set[str] = set()
    matched, missing = [], list(rows)
    # Exact-date pass first so a same-amount row a few days off cannot steal an
    # obviously-correct partner from an exact hit.
    for span in (0, MATCH_WINDOW_DAYS):
        pending = []
        for row in missing:
            target = datetime.strptime(row["posted"], "%Y-%m-%d")
            cands = [
                c for c in by_amount.get(row["amount"], [])
                if c["id"] not in used
                and abs((datetime.strptime(c["posted"], "%Y-%m-%d") - target).days) <= span
            ]
            if cands:
                best = min(cands, key=lambda c: abs(
                    (datetime.strptime(c["posted"], "%Y-%m-%d") - target).days))
                used.add(best["id"])
                matched.append({**row, "matched_source": best["source"]})
            else:
                pending.append(row)
        missing = pending
    return matched, missing


def insert(conn: sqlite3.Connection, spec: dict, rows: list[dict]) -> int:
    """Insert rows, salting repeated same-day identical charges.

    Rows are sorted first so the occurrence index is a property of the data, not
    of file-read order — otherwise a refreshed ``currentTransaction`` file would
    mint different ids for the same charges and break idempotency.
    """
    rows = sorted(rows, key=lambda r: (r["posted"], r["description"], r["amount"]))
    seen: dict[tuple, int] = {}
    for row in rows:
        key = (row["posted"], row["description"], row["amount"])
        n = seen.get(key, 0)
        seen[key] = n + 1
        desc_key = row["description"] if n == 0 else f"{row['description']}#{n + 1}"
        db.upsert_transaction(conn, {
            "id": db.synthetic_id(row["posted"], spec["account_name"], desc_key, row["amount"]),
            "account_id": spec["account_id"],
            "account_name": spec["account_name"],
            "posted": row["posted"],
            "amount": row["amount"],
            "description": row["description"],
            "payee": None,
            "memo": None,
            "pending": False,
            "source": spec["source"],
            "source_category": row.get("source_category"),
            "category": _category_for(row),
        })
    return len(rows)


def _category_for(row: dict) -> str:
    """Description rules first, card-supplied category only as a fallback.

    Note this deliberately inverts ``categorize()``'s own precedence. That
    function prefers the source category, which is right for Mint/Empower's
    hand-curated labels — but a card's coarse "Merchandise"/"Other" would
    otherwise beat the tuned merchant rules (specific merchants, checks, card
    payments). So the rules run first and the statement label only fills what
    they leave Uncategorized.
    """
    if row.get("force_category"):
        return row["force_category"]
    by_rule = categorize(row["description"])
    if by_rule != "Uncategorized":
        return by_rule
    return map_source_category(row.get("source_category")) or "Uncategorized"


def import_account(conn, spec: dict, base_dir: str, dry_run: bool = False) -> dict:
    path = os.path.join(base_dir, spec["file"])
    if not os.path.exists(path):
        return {"key": spec["source"], "error": f"not found: {spec['file']}"}
    rows = spec["parser"](path)
    matched, missing = diff(conn, spec, rows)
    if not dry_run and missing:
        insert(conn, spec, missing)

    def _in_overlap(rs):
        return [r for r in rs if OVERLAP_START <= r["posted"] <= OVERLAP_END]

    ov_m, ov_x = len(_in_overlap(matched)), len(_in_overlap(missing))
    return {
        "rows": len(rows),
        "already_present": len(matched),
        "inserted": 0 if dry_run else len(missing),
        "missing": missing,
        "overlap_matched": ov_m,
        "overlap_total": ov_m + ov_x,
        "gap_rows": len([r for r in missing if r["posted"] < OVERLAP_START]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base_dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="comma-separated account keys")
    args = ap.parse_args(argv)

    keys = args.only.split(",") if args.only else list(ACCOUNTS)
    conn = db.get_conn()
    db.init_db(conn)

    print(f"{'account':<13} {'parsed':>7} {'present':>8} {'insert':>7} {'gap':>5}  "
          f"SimpleFIN-overlap match")
    total = 0
    for key in keys:
        res = import_account(conn, ACCOUNTS[key], args.base_dir, dry_run=args.dry_run)
        if res.get("error"):
            print(f"{key:<13} {res['error']}")
            continue
        ov = (f"{res['overlap_matched']}/{res['overlap_total']}"
              if res["overlap_total"] else "n/a")
        pct = (f" ({100*res['overlap_matched']//res['overlap_total']}%)"
               if res["overlap_total"] else "")
        print(f"{key:<13} {res['rows']:>7} {res['already_present']:>8} "
              f"{len(res['missing']):>7} {res['gap_rows']:>5}  {ov}{pct}")
        total += len(res["missing"])

    if not args.dry_run:
        db.apply_overrides(conn)
        db.apply_description_overrides(conn)
        conn.commit()
    print(("DRY RUN - nothing written. " if args.dry_run else "Inserted ") + f"{total} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
