"""SimpleFIN client for the local finance dashboard.

Protocol overview
-----------------
1. User obtains a **Setup Token** from https://bridge.simplefin.org.
   The token is a base64-encoded one-time *claim URL*.
2. POST to the claim URL (empty body) → response body is the **Access URL**,
   e.g. ``https://USER:PASS@bridge.simplefin.org/simplefin``.
3. GET ``{access_url}/accounts`` (optionally ``?start-date=<epoch>&pending=1``)
   returns JSON with accounts + transactions.

All HTTP is done with ``urllib.request`` (no third-party deps).

Demo / testing
--------------
``DEMO_SETUP_TOKEN`` decodes to the public SimpleFIN demo claim URL.
Claim it once to get a demo access URL, then call ``fetch_normalized``.

    DEMO_SETUP_TOKEN = "aHR0cHM6Ly9iZXRhLWJyaWRnZS5zaW1wbGVmaW4ub3JnL3NpbXBsZWZpbi9jbGFpbS9kZW1v"
    # decodes to: https://beta-bridge.simplefin.org/simplefin/claim/demo
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timezone
from datetime import datetime as _dt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Public SimpleFIN demo base URL (no auth; use the demo claim token below to
#: obtain a real demo access URL).
DEMO_ACCESS_URL = "https://beta-bridge.simplefin.org/simplefin"

#: Demo setup token.  base64-decodes to:
#:   https://beta-bridge.simplefin.org/simplefin/claim/demo
DEMO_SETUP_TOKEN = (
    "aHR0cHM6Ly9iZXRhLWJyaWRnZS5zaW1wbGVmaW4ub3JnL3NpbXBsZWZpbi9jbGFpbS9kZW1v"
)

_USER_AGENT = "LocalFinanceDashboard/1.0 (urllib)"
_TIMEOUT = 60  # seconds

# Path to config.json at the project root (one level above this file's dir).
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)

# ---------------------------------------------------------------------------
# Account-type heuristics
# ---------------------------------------------------------------------------

_INVESTMENT_KEYWORDS = {
    "fidelity", "betterment", "vanguard", "etrade", "e*trade", "schwab",
    "401k", "401 k", "ira", "brokerage", "roth", "tsp", "retirement",
    "investment", "invest",
}
_CREDIT_KEYWORDS = {
    "credit", "card", "visa", "mastercard", "amex", "american express",
    "discover", "citi", "capital one", "chase sapphire", "venture",
}


def _infer_account_type(name: str, org: str | None, balance: float | None) -> str:
    """Return a coarse account type: 'credit', 'investment', or 'depository'."""
    haystack = (name + " " + (org or "")).lower()

    # UBS One Source brokerage accounts — check before the generic keyword loop
    # so they are not accidentally caught by a different branch.
    if "ubs" in haystack or "one source" in haystack:
        return "investment"

    for kw in _INVESTMENT_KEYWORDS:
        if kw in haystack:
            return "investment"

    for kw in _CREDIT_KEYWORDS:
        if kw in haystack:
            return "credit"

    # Negative balance strongly suggests a credit card liability.
    if balance is not None and balance < 0:
        return "credit"

    return "depository"


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _make_request(
    url: str,
    method: str = "GET",
    data: bytes | None = None,
) -> str:
    """Perform an HTTP request, injecting Basic Auth from URL userinfo.

    Credentials are stripped from the request URL and sent as an
    ``Authorization: Basic ...`` header instead (avoids urllib quirks with
    userinfo in URLs).

    Returns the decoded response body.  Raises ``ValueError`` on non-200.
    """
    parsed = urllib.parse.urlsplit(url)

    # Extract credentials; rebuild URL without them.
    username = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    clean_netloc = parsed.hostname or ""
    if parsed.port:
        clean_netloc = f"{clean_netloc}:{parsed.port}"
    clean_url = urllib.parse.urlunsplit(
        (parsed.scheme, clean_netloc, parsed.path, parsed.query, parsed.fragment)
    )

    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    req = urllib.request.Request(clean_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise ValueError(
            f"HTTP {exc.code} from {clean_url}: {body.decode('utf-8', errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Network error reaching {clean_url}: {exc.reason}") from exc

    return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Claim / config
# ---------------------------------------------------------------------------

def claim_access_url(setup_token: str) -> str:
    """Exchange a SimpleFIN Setup Token for a persistent Access URL.

    Parameters
    ----------
    setup_token:
        The base64-encoded one-time claim URL provided by SimpleFIN.

    Returns
    -------
    str
        The Access URL (contains embedded Basic Auth credentials).

    Raises
    ------
    ValueError
        If the token is malformed, the POST fails, or the response is empty.
    """
    setup_token = setup_token.strip()
    # Pad if necessary (base64 requires length % 4 == 0).
    padding = 4 - len(setup_token) % 4
    if padding != 4:
        setup_token += "=" * padding

    try:
        claim_url = base64.b64decode(setup_token).decode("utf-8").strip()
    except Exception as exc:
        raise ValueError(f"setup_token is not valid base64: {exc}") from exc

    if not claim_url.startswith("http"):
        raise ValueError(
            f"Decoded setup token does not look like a URL: {claim_url!r}"
        )

    # POST with empty body.
    access_url = _make_request(claim_url, method="POST", data=b"").strip()

    if not access_url:
        raise ValueError("Claim URL returned an empty response — token may already be used.")

    if not access_url.startswith("http"):
        raise ValueError(
            f"Claim response does not look like an access URL: {access_url!r}"
        )

    return access_url


def save_config(access_url: str) -> None:
    """Persist the Access URL to ``config.json`` at the project root."""
    existing: dict = {}
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass

    existing["access_url"] = access_url
    with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2)
        fh.write("\n")


def load_access_url() -> str | None:
    """Return the stored Access URL, or ``None`` if absent / blank."""
    if not os.path.exists(_CONFIG_PATH):
        return None
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    url = data.get("access_url", "")
    return url if url else None


def is_connected() -> bool:
    """Return ``True`` if a non-empty Access URL is stored in config.json."""
    return bool(load_access_url())


# ---------------------------------------------------------------------------
# Data normalisation helpers
# ---------------------------------------------------------------------------

def _epoch_to_iso(epoch: int | float | None) -> str | None:
    """Convert a UNIX epoch (seconds) to an ISO date string ``YYYY-MM-DD``."""
    if epoch is None:
        return None
    try:
        return _dt.fromtimestamp(int(epoch), tz=timezone.utc).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _parse_amount(value: str | float | int | None) -> float | None:
    """Parse a SimpleFIN money string/number to a Python float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _org_string(org) -> str | None:
    """Flatten the ``org`` field (dict or str) to a human-readable string."""
    if org is None:
        return None
    if isinstance(org, dict):
        return org.get("name") or org.get("domain") or None
    return str(org) or None


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_normalized(
    access_url: str,
    start_date: int | None = None,
) -> dict:
    """Fetch accounts and transactions from the SimpleFIN bridge.

    Parameters
    ----------
    access_url:
        The Access URL (with embedded credentials) returned by ``claim_access_url``.
    start_date:
        Optional UNIX epoch; if provided, only transactions on or after this
        date are returned (``?start-date=<epoch>&pending=1``).

    Returns
    -------
    dict with keys:

    ``accounts``
        List of normalised account dicts::

            {
              "id": str,
              "name": str,
              "org": str | None,        # human label: org name or domain
              "type": str,              # "depository" | "credit" | "investment"
              "currency": str,
              "balance": float | None,
              "available_balance": float | None,
              "balance_date": str | None,   # ISO date
              "transactions": [...],
            }

        Each transaction::

            {
              "id": str,
              "account_id": str,
              "account_name": str,
              "posted": str | None,     # ISO date
              "amount": float | None,   # signed; negative = money out
              "description": str,
              "payee": str,
              "memo": str,
              "pending": bool,
            }

    ``errors``
        List of error strings reported by the bridge (may be empty).
    """
    # Build the /accounts URL.
    base = access_url.rstrip("/")
    accounts_url = base + "/accounts"

    params: dict[str, str] = {"pending": "1"}
    if start_date is not None:
        params["start-date"] = str(int(start_date))

    query = urllib.parse.urlencode(params)
    full_url = f"{accounts_url}?{query}"

    raw_body = _make_request(full_url, method="GET")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Non-JSON response from SimpleFIN: {raw_body[:200]!r}") from exc

    errors: list = payload.get("errors") or []
    raw_accounts: list = payload.get("accounts") or []

    normalised_accounts: list[dict] = []

    for raw_acct in raw_accounts:
        acct_id = raw_acct.get("id", "")
        acct_name = raw_acct.get("name", "Account")
        currency = raw_acct.get("currency", "USD")
        balance = _parse_amount(raw_acct.get("balance"))
        available_balance = _parse_amount(raw_acct.get("available-balance"))
        balance_date = _epoch_to_iso(raw_acct.get("balance-date"))
        org = _org_string(raw_acct.get("org"))

        acct_type = _infer_account_type(acct_name, org, balance)

        # Normalise transactions.
        txns: list[dict] = []
        for raw_txn in raw_acct.get("transactions") or []:
            txns.append(
                {
                    "id": raw_txn.get("id", ""),
                    "account_id": acct_id,
                    "account_name": acct_name,
                    "posted": _epoch_to_iso(raw_txn.get("posted")),
                    "amount": _parse_amount(raw_txn.get("amount")),
                    "description": raw_txn.get("description", ""),
                    "payee": raw_txn.get("payee", ""),
                    "memo": raw_txn.get("memo", ""),
                    "pending": bool(raw_txn.get("pending", False)),
                }
            )

        normalised_accounts.append(
            {
                "id": acct_id,
                "name": acct_name,
                "org": org,
                "type": acct_type,
                "currency": currency,
                "balance": balance,
                "available_balance": available_balance,
                "balance_date": balance_date,
                "transactions": txns,
            }
        )

    return {"accounts": normalised_accounts, "errors": errors}
