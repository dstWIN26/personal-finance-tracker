import asyncio
import os
import logging

from backend.database import upsert_position, insert_transaction

logger = logging.getLogger(__name__)

# Trade Republic moved off the old device-keyfile auth: pytr 0.4.9 logs in via the
# web flow and persists an authenticated *web session* (cookies), not a keyfile.
# We keep the cookies under keys/ (gitignored, chmod 600, mounted into the container)
# alongside the other credentials. Paired once via tr_setup.py; the PIN is never stored.
COOKIES_FILE = os.getenv("TRADE_REPUBLIC_COOKIES_FILE", "keys/tr_cookies.txt")


def _build_api():
    """
    Construct a pytr client for the cookie-based web session.

    Security model:
      - tr_setup.py performs a one-time web login (PIN + SMS/app code) and saves the
        session cookies to COOKIES_FILE. Ongoing syncs *resume* that session.
      - The PIN is only read during initial pairing and is NOT needed at runtime.
        Keep TRADE_REPUBLIC_PIN out of .env once paired.
      - `waf_token="awswaf"` solves TR's anti-bot challenge in pure Python (curl_cffi),
        so we don't have to bundle a headless browser. Credentials are never logged,
        never returned by any route, never stored in the DB.
    """
    from pytr.api import TradeRepublicApi
    return TradeRepublicApi(
        phone_no=os.environ["TRADE_REPUBLIC_PHONE"],
        pin=os.getenv("TRADE_REPUBLIC_PIN", ""),     # only used during pairing
        locale="en",
        save_cookies=True,
        cookies_file=COOKIES_FILE,
        waf_token="awswaf",
    )


def _safe_error(context: str, exc: Exception):
    """Log failures without leaking credentials or full tracebacks."""
    logger.error("%s failed: %s", context, type(exc).__name__)


def _num(v):
    """Coerce TR's number-ish values (often strings like '10.0') to float, or None."""
    if v is None:
        return None
    if isinstance(v, dict):                          # e.g. {"value": -12.3, "currency": "EUR"}
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _resume(api) -> bool:
    """Load the saved web session (sync I/O → run off the event loop). False if expired."""
    try:
        return bool(await asyncio.to_thread(api.resume_websession))
    except Exception as exc:                          # noqa: BLE001 — treat any failure as "not resumed"
        logger.warning("TR session resume failed: %s", type(exc).__name__)
        return False


async def _fetch_one(api, payload: dict, timeout: float = 20.0) -> dict:
    """Subscribe to one TR websocket topic, return its first answer, then unsubscribe."""
    sub_id = await api.subscribe(payload)
    try:
        while True:
            rid, _sub, data = await asyncio.wait_for(api.recv(), timeout)
            if rid == sub_id:
                return data
    finally:
        try:
            await api.unsubscribe(sub_id)
        except Exception:                             # noqa: BLE001 — best-effort cleanup
            pass


def _not_paired() -> bool:
    if not os.path.exists(COOKIES_FILE):
        logger.warning("TR not paired (no %s) — run tr_setup.py to log in", COOKIES_FILE)
        return True
    return False


async def sync_portfolio():
    """Fetch the TR portfolio and write the latest position snapshot to the DB."""
    if _not_paired():
        return
    api = _build_api()
    try:
        if not await _resume(api):
            logger.warning("TR web session expired — re-run tr_setup.py to re-pair")
            return
        data = await _fetch_one(api, {"type": "compactPortfolio"})
        positions = data.get("positions", []) if isinstance(data, dict) else []
        for item in positions:
            isin = item.get("instrumentId")
            if not isin:
                continue
            # compactPortfolio carries holdings + average buy-in; live price / P&L
            # would need a per-instrument ticker subscription (left for a follow-up),
            # so those degrade to None rather than blocking the snapshot.
            upsert_position(
                isin=isin,
                name=item.get("name") or isin,
                quantity=_num(item.get("netSize") or item.get("quantity")) or 0,
                buy_price=_num(item.get("averageBuyIn")),
                current_price=None,
                pl_pct=None,
                pl_eur=None,
            )
        logger.info("TR portfolio synced: %d positions", len(positions))
    except Exception as exc:                          # noqa: BLE001
        _safe_error("TR portfolio sync", exc)
    finally:
        try:
            await api.close()
        except Exception:                             # noqa: BLE001
            pass


async def sync_tr_transactions():
    """Fetch the TR transaction timeline and insert new rows (dedup via INSERT OR IGNORE)."""
    if _not_paired():
        return
    api = _build_api()
    try:
        if not await _resume(api):
            logger.warning("TR web session expired — re-run tr_setup.py to re-pair")
            return
        data = await _fetch_one(api, {"type": "timelineTransactions"})
        items = data.get("items", []) if isinstance(data, dict) else []
        inserted = 0
        for ev in items:
            amount = _num(ev.get("amount"))
            if amount is None:                        # skip non-cash timeline entries
                continue
            insert_transaction(
                source="trade_republic",
                date=(ev.get("timestamp") or "")[:10],
                description=ev.get("title", ""),
                category=ev.get("eventType", "trade"),
                amount=amount,
                raw_json=str(ev),
            )
            inserted += 1
        logger.info("TR transactions synced: %d cash events", inserted)
    except Exception as exc:                          # noqa: BLE001
        _safe_error("TR transaction sync", exc)
    finally:
        try:
            await api.close()
        except Exception:                             # noqa: BLE001
            pass
