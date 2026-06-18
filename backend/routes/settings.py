"""Settings & Profile endpoints (guarded — mounted behind require_session).

Two jobs:
  1. Report integration link status WITHOUT ever returning a secret or credential.
  2. Drive the bank-linking consent flow (Enable Banking) and store non-secret prefs.

Trade Republic linking is deliberately NOT exposed here: it needs a PIN + OTP and
saves an authenticated web session (cookies), so it stays a server-side CLI step
(tr_setup.py). This page only reports its status and shows the command.
"""
import os
import io
import csv
import re
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

from backend import alerts
from backend.integrations import enable_banking
from backend.integrations.trade_republic import COOKIES_FILE as TR_COOKIES
from backend.database import (
    connect,
    create_link_state,
    consume_link_state,
    insert_bank_connection,
    list_bank_connections,
    delete_bank_connection,
    get_all_settings,
    set_setting,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

# Consent-redirect state is single-use and short-lived.
_STATE_TTL_MIN = 15

# Profile keys we accept (anything else is ignored — no arbitrary writes).
_PROFILE_KEYS = {"display_name", "base_currency", "locale", "default_tab"}
_PROFILE_DEFAULTS = {
    "display_name": "",
    "base_currency": "EUR",
    "locale": "en-IE",
    "default_tab": "overview",
}


def _source_key(name: str) -> str:
    """Stable `source` slug for a bank (e.g. 'Deutsche Bank' → 'deutsche_bank')."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "bank"


def _mask_iban(iban: str | None) -> str | None:
    if not iban or len(iban) < 4:
        return None
    return "••••" + iban[-4:]


def _last_synced() -> dict:
    """Freshest data timestamp per kind, so Settings can show 'updated N ago'."""
    with connect() as conn:
        def m(sql):
            return conn.execute(sql).fetchone()[0]
        return {
            "positions": m("SELECT MAX(fetched_at) FROM positions"),
            "balances": m("SELECT MAX(fetched_at) FROM balances"),
            "transactions": m("SELECT MAX(date) FROM transactions"),
        }


# ── Integration status (no secrets) ──────────────────────────────────────────

@router.get("/integrations")
def integrations_status():
    banks = []
    for c in list_bank_connections():
        banks.append({
            "id": c["id"],
            "provider": c["provider"],
            "aspsp_name": c["aspsp_name"],
            "aspsp_country": c["aspsp_country"],
            "status": c["status"],
            "expires_at": c["expires_at"],
            "accounts": [
                {"name": a.get("name"), "iban": _mask_iban(a.get("iban")),
                 "currency": a.get("currency")}
                for a in c.get("accounts", [])
            ],
        })
    session_present = os.path.exists(TR_COOKIES)
    return {
        "trade_republic": {
            "phone_set": bool(os.getenv("TRADE_REPUBLIC_PHONE")),
            "session_present": session_present,
            "linked": session_present,
        },
        "enable_banking": {
            "configured": enable_banking.is_configured(),
            "redirect_url": enable_banking.redirect_url(),
        },
        "salt_edge_legacy": {
            "configured": bool(os.getenv("SALTEDGE_CONNECTION_ID")),
        },
        "banks": banks,
        "last_synced": _last_synced(),
    }


# ── Manual "Sync now" ────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_now():
    """Run every data sync immediately instead of waiting for the scheduler.

    Sources are synced one at a time (SQLite is single-writer) and each is time-
    boxed, so an unconfigured or slow provider can't hang the request. Providers
    that aren't set up degrade to a no-op inside their own sync function.
    """
    from backend.integrations.trade_republic import sync_portfolio, sync_tr_transactions
    from backend.integrations.revolut import sync_transactions as sync_revolut
    from backend.integrations.enable_banking import sync_bank_connections as sync_banks

    jobs = {
        "trade_republic_portfolio": sync_portfolio,
        "trade_republic_transactions": sync_tr_transactions,
        "revolut": sync_revolut,
        "banks": sync_banks,
    }
    results = {}
    for name, fn in jobs.items():
        try:
            await asyncio.wait_for(fn(), timeout=90)
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 — one bad source must not fail the rest
            logger.warning("manual sync '%s' failed: %s", name, type(exc).__name__)
            results[name] = "error"
    return {"results": results, "last_synced": _last_synced()}


# ── Data export (financial data only — never auth/secrets) ───────────────────

def _csv(columns: list[str], rows, filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow([r[c] for c in columns])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_LATEST_POSITIONS = (
    "SELECT isin, name, quantity, buy_price, current_price, pl_pct, pl_eur, fetched_at "
    "FROM positions WHERE id IN (SELECT MAX(id) FROM positions GROUP BY isin) ORDER BY name"
)


@router.get("/export/transactions.csv")
def export_transactions():
    with connect() as conn:
        rows = conn.execute(
            "SELECT source, date, description, category, amount, currency "
            "FROM transactions ORDER BY date DESC, id DESC"
        ).fetchall()
    return _csv(["source", "date", "description", "category", "amount", "currency"],
                rows, "transactions.csv")


@router.get("/export/positions.csv")
def export_positions():
    with connect() as conn:
        rows = conn.execute(_LATEST_POSITIONS).fetchall()
    return _csv(["isin", "name", "quantity", "buy_price", "current_price", "pl_pct", "pl_eur", "fetched_at"],
                rows, "portfolio.csv")


@router.get("/export/all.json")
def export_all():
    with connect() as conn:
        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "transactions": [dict(r) for r in conn.execute(
                "SELECT source, date, description, category, amount, currency FROM transactions ORDER BY date").fetchall()],
            "positions": [dict(r) for r in conn.execute(_LATEST_POSITIONS).fetchall()],
            "balances": [dict(r) for r in conn.execute(
                "SELECT source, balance, currency, fetched_at FROM balances ORDER BY fetched_at").fetchall()],
            "limits": [dict(r) for r in conn.execute(
                "SELECT category, amount, period FROM limits").fetchall()],
        }
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="finance-export.json"'})


# ── Email-alert preferences ──────────────────────────────────────────────────

@router.get("/alerts")
def get_alerts():
    saved = get_all_settings()
    return {
        "email_configured": alerts.email_configured(),
        "env_recipient": os.getenv("EMAIL_TO", ""),
        "security_enabled": saved.get("alerts_security_enabled", "1") != "0",
        "budget_enabled": saved.get("alerts_budget_enabled", "1") != "0",
        "email_to": saved.get("alerts_email_to", ""),
    }


@router.post("/alerts")
def save_alerts(prefs: dict):
    if "security_enabled" in prefs:
        set_setting("alerts_security_enabled", "1" if prefs["security_enabled"] else "0")
    if "budget_enabled" in prefs:
        set_setting("alerts_budget_enabled", "1" if prefs["budget_enabled"] else "0")
    if "email_to" in prefs:
        set_setting("alerts_email_to", str(prefs.get("email_to") or "").strip()[:200])
    return {"status": "ok"}


# ── Bank linking (Enable Banking consent flow) ───────────────────────────────

@router.get("/banks/aspsps")
async def banks_aspsps(country: str | None = None):
    if not enable_banking.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Enable Banking is not configured on the server yet.")
    try:
        return {"aspsps": await enable_banking.list_aspsps(country)}
    except Exception as exc:  # noqa: BLE001
        logger.error("Enable Banking aspsps failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Could not reach the bank directory.")


class ConnectIn(BaseModel):
    aspsp_name: str
    country: str


@router.post("/banks/connect")
async def banks_connect(body: ConnectIn):
    if not enable_banking.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Enable Banking is not configured on the server yet.")
    aspsp_name = body.aspsp_name.strip()
    country = body.country.strip().upper()
    if not aspsp_name or len(country) != 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid bank/country.")

    state = secrets.token_urlsafe(32)
    # Store in SQLite's own UTC timestamp format so `expires_at < CURRENT_TIMESTAMP`
    # compares correctly as a string (isoformat's 'T'/offset would sort wrong).
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MIN)).strftime(
        "%Y-%m-%d %H:%M:%S")
    create_link_state(state, enable_banking.PROVIDER, aspsp_name, country,
                       _source_key(aspsp_name), expires_at)
    try:
        url = await enable_banking.start_auth(aspsp_name, country, state)
    except Exception as exc:  # noqa: BLE001
        logger.error("Enable Banking start_auth failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Could not start bank authorisation.")
    return {"url": url}


class CompleteIn(BaseModel):
    code: str
    state: str


@router.post("/banks/complete")
async def banks_complete(body: CompleteIn):
    pending = consume_link_state(body.state)
    if pending is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Link request expired or invalid — please try again.")
    try:
        session_id, accounts, valid_until = await enable_banking.complete_session(body.code)
    except Exception as exc:  # noqa: BLE001
        logger.error("Enable Banking complete_session failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Could not finish linking the bank.")

    insert_bank_connection(
        provider=pending["provider"],
        aspsp_name=pending["aspsp_name"],
        aspsp_country=pending["aspsp_country"],
        source_key=pending["source_key"],
        session_id=session_id,
        accounts=accounts,
        status="active",
        expires_at=valid_until,
    )
    # Pull data immediately so the dashboard fills without waiting for the hourly job.
    asyncio.create_task(enable_banking.sync_bank_connections())
    return {"status": "ok", "accounts": len(accounts)}


@router.delete("/banks/{conn_id}")
def banks_unlink(conn_id: int):
    delete_bank_connection(conn_id)
    return {"status": "ok"}


# ── Profile / display preferences ────────────────────────────────────────────

@router.get("/profile")
def get_profile():
    saved = get_all_settings()
    return {k: saved.get(k, default) for k, default in _PROFILE_DEFAULTS.items()}


@router.post("/profile")
def save_profile(prefs: dict):
    for key, value in prefs.items():
        if key in _PROFILE_KEYS:
            set_setting(key, value)
    return {"status": "ok"}
