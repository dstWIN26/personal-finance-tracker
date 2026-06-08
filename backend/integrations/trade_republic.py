import asyncio
import os
import logging

from backend.database import upsert_position, insert_transaction

logger = logging.getLogger(__name__)

# Path to the device keyfile produced by one-time pairing (see tr_setup.py).
# This private key — NOT the PIN — is what authenticates ongoing syncs.
KEYFILE = os.getenv("TRADE_REPUBLIC_KEYFILE", "keys/tr_keyfile.pem")


def _build_api():
    """
    Construct a pytr client.

    Security model:
      - After one-time pairing (tr_setup.py), the keyfile authenticates logins.
      - The PIN is read only as an optional fallback for the initial pairing and
        is NOT required at runtime. Keep TRADE_REPUBLIC_PIN out of .env once paired.
      - Credentials are never logged, never returned by any route, never stored in the DB.
    """
    from pytr.api import TradeRepublicApi
    return TradeRepublicApi(
        phone_no=os.environ["TRADE_REPUBLIC_PHONE"],
        pin=os.getenv("TRADE_REPUBLIC_PIN", ""),  # blank once device is paired
        keyfile=KEYFILE,
        locale="en",
    )


def _safe_error(context: str, exc: Exception):
    """Log failures without leaking credentials or full tracebacks."""
    logger.error("%s failed: %s", context, type(exc).__name__)


async def sync_portfolio():
    """Fetch all TR positions and write the latest snapshot to the DB."""
    if not os.path.exists(KEYFILE):
        logger.warning("TR keyfile not found at %s — run tr_setup.py to pair the device", KEYFILE)
        return
    try:
        api = _build_api()
        await api.login()
        await asyncio.sleep(1)  # rate limit: 1 req/sec

        portfolio = await api.portfolio()
        for item in portfolio.get("positions", []):
            upsert_position(
                isin=item["instrumentId"],
                name=item.get("name", item["instrumentId"]),
                quantity=item.get("quantity", 0),
                buy_price=item.get("averageBuyIn"),
                current_price=item.get("currentPrice"),
                pl_pct=item.get("unrealizedPnlPct"),
                pl_eur=item.get("unrealizedPnl"),
            )
        logger.info("TR portfolio synced: %d positions", len(portfolio.get("positions", [])))
    except Exception as exc:
        _safe_error("TR portfolio sync", exc)


async def sync_tr_transactions():
    """Fetch the TR transaction timeline and insert new rows."""
    if not os.path.exists(KEYFILE):
        logger.warning("TR keyfile not found at %s — run tr_setup.py to pair the device", KEYFILE)
        return
    try:
        api = _build_api()
        await api.login()
        await asyncio.sleep(1)

        timeline = await api.timeline()
        for event in timeline.get("items", []):
            insert_transaction(
                source="trade_republic",
                date=event.get("timestamp", "")[:10],
                description=event.get("title", ""),
                category=event.get("eventType", "trade"),
                amount=float(event.get("amount", 0)),
                raw_json=str(event),
            )
        logger.info("TR transactions synced: %d events", len(timeline.get("items", [])))
    except Exception as exc:
        _safe_error("TR transaction sync", exc)
