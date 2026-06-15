"""
Enable Banking integration — free, PSD2 Open Banking aggregator.

Why Enable Banking: it is free for personal use, covers ~2,700 EEA banks
(including Revolut and Deutsche Bank), and authenticates with an RSA private key
(RS256 JWT) rather than a shared secret — which fits this app's "secrets live in
keys/ on the server, never in the browser or DB" model.

Security model (mirrors trade_republic.py):
  - The application id is public-ish config; the RSA private key lives in keys/
    (gitignored, chmod 600) and is the real credential. It is never logged, never
    returned by any route, never stored in the DB.
  - Bank login credentials are NEVER seen by this app: linking is a redirect/consent
    flow handled by the bank. We persist only the opaque aggregator session id and
    account references (see database.bank_connections).
  - Degrades gracefully: every entrypoint no-ops with a warning when unconfigured.

Docs: https://enablebanking.com/docs/api/reference/
"""
import os
import json
import time
import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx

from backend import config
from backend.database import (
    insert_transaction,
    upsert_balance,
    list_bank_connections,
    set_bank_connection_status,
)
from backend.integrations.revolut import categorize

logger = logging.getLogger(__name__)

PROVIDER = "enable_banking"
BASE = os.getenv("ENABLE_BANKING_BASE", "https://api.enablebanking.com")
APP_ID = os.getenv("ENABLE_BANKING_APP_ID", "")
KEYFILE = os.getenv("ENABLE_BANKING_KEYFILE", "keys/enablebanking_private.pem")
# JWT iss/aud are fixed by Enable Banking.
_JWT_ISS = "enablebanking.com"
_JWT_AUD = "api.enablebanking.com"
# How long to request consent for (PSD2 caps this; banks may grant less).
CONSENT_DAYS = int(os.getenv("ENABLE_BANKING_CONSENT_DAYS", "90"))

# Small TTL cache for the (large, slow-changing) bank list.
_aspsp_cache: dict = {}
_ASPSP_TTL = 6 * 3600


def is_configured() -> bool:
    """True when both the application id and the private keyfile are present."""
    return bool(APP_ID) and os.path.exists(KEYFILE)


def redirect_url() -> str:
    """The whitelisted consent return URL. Defaults to RP_ORIGIN + callback path."""
    return os.getenv(
        "ENABLE_BANKING_REDIRECT_URL",
        config.RP_ORIGIN.rstrip("/") + "/settings/banks/callback",
    )


# ── RS256 JWT (signed locally with the private key; no shared secret) ────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_jwt(ttl: int = 3600) -> str:
    """Build a short-lived RS256 JWT for the Authorization header.

    Signed with the RSA private key at KEYFILE. The key material never leaves
    this function and is never logged.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    header = {"typ": "JWT", "alg": "RS256", "kid": APP_ID}
    now = int(time.time())
    payload = {"iss": _JWT_ISS, "aud": _JWT_AUD, "iat": now, "exp": now + ttl}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    with open(KEYFILE, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {build_jwt()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── Interactive linking (called from routes/settings.py) ─────────────────────

async def list_aspsps(country: str | None = None) -> list[dict]:
    """List supported banks (optionally filtered by 2-letter country)."""
    key = (country or "").upper()
    cached = _aspsp_cache.get(key)
    if cached and (time.time() - cached[0]) < _ASPSP_TTL:
        return cached[1]
    params = {"country": key} if key else {}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE}/aspsps", headers=_headers(), params=params)
        r.raise_for_status()
        aspsps = r.json().get("aspsps", [])
    # Keep only display-relevant fields; never cache anything sensitive.
    trimmed = [
        {"name": a.get("name"), "country": a.get("country"), "logo": a.get("logo")}
        for a in aspsps
        if a.get("name")
    ]
    _aspsp_cache[key] = (time.time(), trimmed)
    return trimmed


async def start_auth(aspsp_name: str, country: str, state: str) -> str:
    """Begin consent for a bank; returns the URL to send the user's browser to."""
    valid_until = (datetime.now(timezone.utc) + timedelta(days=CONSENT_DAYS)).isoformat()
    body = {
        "access": {"valid_until": valid_until},
        "aspsp": {"name": aspsp_name, "country": country},
        "state": state,
        "redirect_url": redirect_url(),
        "psu_type": "personal",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE}/auth", headers=_headers(), json=body)
        r.raise_for_status()
        return r.json()["url"]


async def complete_session(code: str) -> tuple[str, list[dict], str | None]:
    """Exchange the consent `code` for a session.

    Returns (session_id, accounts, valid_until). `accounts` is a list of dicts
    with at least a `uid`; we keep name/iban for display only.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE}/sessions", headers=_headers(), json={"code": code})
        r.raise_for_status()
        data = r.json()
    session_id = data.get("session_id", "")
    accounts = []
    for acc in data.get("accounts", []):
        if isinstance(acc, dict):
            accounts.append({
                "uid": acc.get("uid"),
                "name": acc.get("name") or acc.get("product"),
                "iban": (acc.get("account_id") or {}).get("iban"),
                "currency": acc.get("currency"),
            })
        else:  # some banks return bare uid strings
            accounts.append({"uid": acc})
    valid_until = (data.get("access") or {}).get("valid_until")
    return session_id, accounts, valid_until


# ── Scheduled sync (called by the APScheduler job in main.py) ────────────────

def _normalize_transaction(tx: dict) -> tuple[str, str, float, str]:
    """Map an Enable Banking transaction to (date, description, signed_amount, currency)."""
    amt = tx.get("transaction_amount") or {}
    try:
        amount = float(amt.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0.0
    # Enable Banking reports positive magnitudes + a debit/credit indicator.
    if (tx.get("credit_debit_indicator") or "").upper() == "DBIT":
        amount = -abs(amount)
    else:
        amount = abs(amount)
    currency = amt.get("currency", "EUR")
    date = tx.get("booking_date") or tx.get("value_date") or ""
    remittance = tx.get("remittance_information") or []
    description = (
        " ".join(remittance) if isinstance(remittance, list) else str(remittance)
    ) or (tx.get("creditor") or {}).get("name") or (tx.get("debtor") or {}).get("name") or ""
    return date[:10], description.strip(), amount, currency


def _pick_balance(balances: list[dict]) -> dict | None:
    """Prefer a closing/available balance; fall back to the first one."""
    if not balances:
        return None
    by_type = {(b.get("balance_type") or "").upper(): b for b in balances}
    for preferred in ("CLBD", "ITAV", "XPCD"):
        if preferred in by_type:
            return by_type[preferred]
    return balances[0]


async def _sync_one(client: httpx.AsyncClient, conn: dict) -> int:
    """Sync balances + transactions for a single linked connection. Returns tx count."""
    source = conn["source_key"]
    total = 0
    for acc in conn.get("accounts", []):
        uid = acc.get("uid") if isinstance(acc, dict) else acc
        if not uid:
            continue

        # Balance snapshot
        rb = await client.get(f"{BASE}/accounts/{uid}/balances", headers=_headers())
        rb.raise_for_status()
        bal = _pick_balance(rb.json().get("balances", []))
        if bal:
            bamt = bal.get("balance_amount") or {}
            try:
                upsert_balance(
                    source=source,
                    balance=float(bamt.get("amount", 0)),
                    currency=bamt.get("currency", "EUR"),
                )
            except (TypeError, ValueError):
                pass

        # Transactions (paginated via continuation_key)
        continuation = None
        while True:
            params = {"continuation_key": continuation} if continuation else {}
            rt = await client.get(
                f"{BASE}/accounts/{uid}/transactions", headers=_headers(), params=params
            )
            rt.raise_for_status()
            payload = rt.json()
            for tx in payload.get("transactions", []):
                date, description, amount, currency = _normalize_transaction(tx)
                if not date:
                    continue
                insert_transaction(
                    source=source,
                    date=date,
                    description=description,
                    category=categorize({"description": description}),
                    amount=amount,
                    currency=currency,
                    raw_json=json.dumps(tx),
                )
                total += 1
            continuation = payload.get("continuation_key")
            if not continuation:
                break
    return total


async def sync_bank_connections():
    """Fetch balances + transactions for every active Enable Banking connection."""
    if not is_configured():
        logger.info("Enable Banking not configured — skipping bank sync")
        return
    connections = [c for c in list_bank_connections()
                   if c.get("provider") == PROVIDER and c.get("status") == "active"]
    if not connections:
        return
    async with httpx.AsyncClient(timeout=30) as client:
        for conn in connections:
            try:
                n = await _sync_one(client, conn)
                logger.info("Enable Banking synced %d tx for %s",
                            n, conn.get("aspsp_name"))
            except httpx.HTTPStatusError as exc:
                # 401/403 typically means the consent expired and must be re-authorised.
                if exc.response.status_code in (401, 403):
                    set_bank_connection_status(conn["id"], "expired")
                    logger.warning("Enable Banking consent expired for %s",
                                   conn.get("aspsp_name"))
                else:
                    set_bank_connection_status(conn["id"], "error")
                    logger.error("Enable Banking sync failed (HTTP %s) for %s",
                                 exc.response.status_code, conn.get("aspsp_name"))
            except Exception as exc:  # noqa: BLE001
                set_bank_connection_status(conn["id"], "error")
                logger.error("Enable Banking sync failed: %s", type(exc).__name__)
