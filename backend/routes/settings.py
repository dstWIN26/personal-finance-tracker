"""Settings & Profile endpoints (guarded — mounted behind require_session).

Two jobs:
  1. Report integration link status WITHOUT ever returning a secret or credential.
  2. Drive the bank-linking consent flow (Enable Banking) and store non-secret prefs.

Trade Republic linking is deliberately NOT exposed here: it needs a PIN + OTP and
writes a private keyfile, so it stays a server-side CLI step (tr_setup.py). This
page only reports its status and shows the command.
"""
import os
import re
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.integrations import enable_banking
from backend.integrations.trade_republic import KEYFILE as TR_KEYFILE
from backend.database import (
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
    keyfile_present = os.path.exists(TR_KEYFILE)
    return {
        "trade_republic": {
            "phone_set": bool(os.getenv("TRADE_REPUBLIC_PHONE")),
            "keyfile_present": keyfile_present,
            "linked": keyfile_present,
        },
        "enable_banking": {
            "configured": enable_banking.is_configured(),
            "redirect_url": enable_banking.redirect_url(),
        },
        "salt_edge_legacy": {
            "configured": bool(os.getenv("SALTEDGE_CONNECTION_ID")),
        },
        "banks": banks,
    }


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
