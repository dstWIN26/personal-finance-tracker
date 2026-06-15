"""Live market data — Financial Modeling Prep (FMP) + ECB.

  * FMP /v3/quote (batched)        → index/ETF/VIX quotes, change %, day/52w ranges
  * FMP /v3/historical-price-full  → daily OHLC for candlestick history + sparklines
  * FMP /v4/treasury               → US par yield curve (2/5/10/30Y)
  * ECB SDW yield-curve API        → euro-area AAA (≈Bund) spot rates (2/5/10/30Y)

Why FMP: Yahoo and Stooq both block/bot-challenge datacenter IPs, so we use a keyed
provider that allows server access. FMP needs a FREE api key (`FMP_API_KEY`). Its
free tier is *US-listed only*, so each index is represented by a US-listed ETF
proxy — SPY/QQQ/DIA for the US indices, and FEZ/EWG/EWU standing in for
Euro Stoxx 50 / DAX / FTSE. Data is end-of-day / delayed on the free tier.

Responses are cached in-process with a short TTL so the dashboard can poll without
hammering the provider (which caps the free tier at 250 calls/day). Every function
degrades gracefully: a failing/absent upstream yields an empty/partial payload and
is logged, never raised to the route. Without `FMP_API_KEY` the equity panels stay
empty and only ECB euro-area yields populate.
"""
import os
import time
import asyncio
import logging
import csv
import io
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# ── Symbol presets (app-facing symbols; kept stable for the route whitelist) ──
INDICES = [
    {"symbol": "^GSPC",     "name": "S&P 500"},
    {"symbol": "^NDX",      "name": "Nasdaq 100"},
    {"symbol": "^DJI",      "name": "Dow Jones"},
    {"symbol": "^GDAXI",    "name": "DAX"},
    {"symbol": "^STOXX50E", "name": "Euro Stoxx 50"},
    {"symbol": "^FTSE",     "name": "FTSE 100"},
]
VIX_SYMBOL = "^VIX"

# SPDR sector ETFs → divergent heatmap tiles.
SECTORS = [
    {"symbol": "XLK",  "name": "Tech"},
    {"symbol": "XLF",  "name": "Financials"},
    {"symbol": "XLE",  "name": "Energy"},
    {"symbol": "XLV",  "name": "Health"},
    {"symbol": "XLI",  "name": "Industrials"},
    {"symbol": "XLY",  "name": "Cons. Disc."},
    {"symbol": "XLP",  "name": "Cons. Staples"},
    {"symbol": "XLU",  "name": "Utilities"},
    {"symbol": "XLB",  "name": "Materials"},
    {"symbol": "XLRE", "name": "Real Estate"},
    {"symbol": "XLC",  "name": "Comm. Svcs"},
]

# App symbol → FMP ticker. US-listed ETF proxies let the (US-only) free tier cover
# the European indices too; sector ETFs and VIX map to themselves.
_FMP_TICKER = {
    "^GSPC": "SPY", "^NDX": "QQQ", "^DJI": "DIA",
    "^GDAXI": "EWG", "^STOXX50E": "FEZ", "^FTSE": "EWU",
    "^VIX": "^VIX",
}


def _fmp(symbol: str) -> str:
    return _FMP_TICKER.get(symbol, symbol)


_NAME = {
    **{s["symbol"]: s["name"] for s in INDICES},
    **{s["symbol"]: s["name"] for s in SECTORS},
    VIX_SYMBOL: "VIX",
}

FMP_BASE = os.getenv("FMP_BASE", "https://financialmodelingprep.com/api")
FMP_KEY = os.getenv("FMP_API_KEY", "")
ECB_YC = "https://data-api.ecb.europa.eu/service/data/YC"
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}


def is_configured() -> bool:
    """True when an FMP api key is set (equity panels need it; ECB does not)."""
    return bool(FMP_KEY)


# ── Tiny in-process TTL cache ─────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, object]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
# When an upstream is failing we keep serving the last-good value but re-try this
# often (seconds), so recovery is quick without per-request hammering.
_STALE_RETRY = 60


async def _cached(key: str, ttl: int, producer):
    """Return cached value for `key`, or await `producer()` and cache it.

    A per-key lock collapses concurrent misses into a single upstream fetch.

    Resilience: when an upstream fails the producer yields an empty/falsy payload.
    We never cache such a result and instead keep serving the last-known-good value
    (stale) so a transient hiccup doesn't flap the dashboard into an error state.
    A truly empty result only surfaces on a cold start where we have never had
    data. While serving stale we re-arm a short TTL so we retry the provider soon.
    """
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        fresh = _CACHE.get(key)                     # re-check inside the lock
        if fresh and fresh[0] > time.monotonic():
            return fresh[1]
        try:
            value = await producer()
        except Exception as e:                      # noqa: BLE001 — whole batch failed
            logger.warning("market producer for %r failed: %s", key, e)
            value = None
        if value:                                   # only cache real data
            _CACHE[key] = (time.monotonic() + ttl, value)
            return value
        if hit:                                     # serve last-good; retry soon
            _CACHE[key] = (time.monotonic() + min(ttl, _STALE_RETRY), hit[1])
            return hit[1]
        return value                                # cold start, no data yet


# ── FMP quotes ────────────────────────────────────────────────────────────────
def _to_float(v):
    """Coerce FMP numbers (which may arrive as int/float or '1.23%' strings)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _quote_from_fmp(d: dict | None, symbol: str, name: str | None = None) -> dict | None:
    """Map an FMP /v3/quote row to the dashboard's quote shape (unchanged keys)."""
    if not d:
        return None
    price = _to_float(d.get("price"))
    if price is None:
        return None
    return {
        "symbol": symbol,                           # app-facing symbol (frontend key)
        "name": name or d.get("name") or symbol,
        "price": price,
        "prev_close": _to_float(d.get("previousClose")),
        "change": _to_float(d.get("change")),
        "change_pct": _to_float(d.get("changesPercentage")),
        "currency": "USD",                          # US-listed proxies trade in USD
        "day_high": _to_float(d.get("dayHigh")),
        "day_low": _to_float(d.get("dayLow")),
        "week52_high": _to_float(d.get("yearHigh")),
        "week52_low": _to_float(d.get("yearLow")),
        "volume": d.get("volume"),
        "market_time": d.get("timestamp"),
        "spark": [],                                # FMP batch quote has no intraday series
    }


async def _fmp_quotes(items: list[dict]) -> list[dict]:
    """Fetch a batch of quotes in ONE call. `items` = [{symbol,name}, ...]."""
    if not FMP_KEY:
        return []
    by_ticker_item: dict[str, dict] = {}
    for it in items:
        by_ticker_item.setdefault(_fmp(it["symbol"]), it)
    tickers = ",".join(by_ticker_item.keys())
    async with httpx.AsyncClient(timeout=15, headers=_UA) as client:
        r = await client.get(f"{FMP_BASE}/v3/quote/{tickers}", params={"apikey": FMP_KEY})
        r.raise_for_status()
        rows = r.json()
    by_ticker = {row.get("symbol"): row for row in rows if isinstance(row, dict)}
    out = []
    for ticker, it in by_ticker_item.items():
        q = _quote_from_fmp(by_ticker.get(ticker), it["symbol"], it.get("name"))
        if q:
            out.append(q)
    return out


# TTL sized so the scheduler warm-job keeps the cache fresh and the browser's
# polling never triggers an upstream call (free tier = 250 requests/day).
_QUOTE_TTL = 960


async def get_indices() -> list[dict]:
    return await _cached("indices", _QUOTE_TTL, lambda: _fmp_quotes(INDICES))


async def get_vix() -> dict | None:
    async def produce():
        rows = await _fmp_quotes([{"symbol": VIX_SYMBOL, "name": "VIX"}])
        return rows[0] if rows else None
    return await _cached("vix", _QUOTE_TTL, produce)


async def get_quotes(symbols: list[str]) -> list[dict]:
    items = [{"symbol": s, "name": _NAME.get(s)} for s in symbols]
    key = "q:" + ",".join(sorted(symbols))
    return await _cached(key, _QUOTE_TTL, lambda: _fmp_quotes(items))


async def get_heatmap() -> list[dict]:
    async def produce():
        rows = await _fmp_quotes(SECTORS)
        return [{"name": r["name"], "symbol": r["symbol"], "change_pct": r["change_pct"]}
                for r in rows]
    return await _cached("heatmap", _QUOTE_TTL, produce)


# How far back to pull daily candles for each UI range (FMP free is EOD/daily, so
# the short intraday ranges just show recent daily candles).
_RANGE_DAYS = {"1d": 7, "5d": 14, "1mo": 31, "3mo": 93, "1y": 370, "max": None}


def _history_meta(candles: list[dict], symbol: str) -> dict:
    if not candles:
        return {}
    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else None
    price, pc = last["c"], (prev["c"] if prev else None)
    change = (price - pc) if pc is not None else None
    closes = [c["c"] for c in candles if c["c"] is not None]
    return {
        "symbol": symbol,
        "name": _NAME.get(symbol, symbol),
        "price": price,
        "prev_close": pc,
        "change": change,
        "change_pct": (change / pc * 100) if (change is not None and pc) else None,
        "day_high": last.get("h"),
        "day_low": last.get("l"),
        "week52_high": max(closes) if closes else None,
        "week52_low": min(closes) if closes else None,
    }


async def get_history(symbol: str, rng: str = "1mo", interval: str = "1d") -> dict:
    """Daily OHLC series for candlestick charts (free tier is EOD-only)."""
    key = f"h:{symbol}:{rng}"

    async def produce():
        if not FMP_KEY:
            return {"symbol": symbol, "name": _NAME.get(symbol, symbol), "candles": [], "meta": {}}
        params = {"apikey": FMP_KEY}
        days = _RANGE_DAYS.get(rng, 31)
        if days:
            params["from"] = str(date.today() - timedelta(days=days))
        async with httpx.AsyncClient(timeout=15, headers=_UA) as client:
            try:
                r = await client.get(
                    f"{FMP_BASE}/v3/historical-price-full/{_fmp(symbol)}", params=params)
                r.raise_for_status()
                hist = r.json().get("historical", []) or []
            except Exception as e:                  # noqa: BLE001
                logger.warning("history failed for %s: %s", symbol, e)
                return {"symbol": symbol, "name": _NAME.get(symbol, symbol), "candles": [], "meta": {}}
        candles = []
        for row in reversed(hist):                  # FMP returns newest-first
            o, c = _to_float(row.get("open")), _to_float(row.get("close"))
            if o is None or c is None:
                continue
            try:
                t = int(datetime.strptime(row["date"][:10], "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp() * 1000)
            except (KeyError, ValueError):
                continue
            candles.append({"t": t, "o": o, "h": _to_float(row.get("high")),
                            "l": _to_float(row.get("low")), "c": c, "v": row.get("volume")})
        return {
            "symbol": symbol,
            "name": _NAME.get(symbol, symbol),
            "candles": candles,
            "meta": _history_meta(candles, symbol),
        }

    return await _cached(key, 300, produce)


# ── US par yield curve (FMP /v4/treasury) ─────────────────────────────────────
async def _us_bonds() -> dict:
    if not FMP_KEY:
        return {"region": "US", "date": "", "tenors": {}}
    async with httpx.AsyncClient(timeout=20, headers=_UA) as client:
        r = await client.get(f"{FMP_BASE}/v4/treasury", params={"apikey": FMP_KEY})
        r.raise_for_status()
        rows = r.json()
    if not rows:
        return {"region": "US", "date": "", "tenors": {}}
    row = rows[0]                                   # newest-first
    tenors = {
        "2Y":  _to_float(row.get("year2")),
        "5Y":  _to_float(row.get("year5")),
        "10Y": _to_float(row.get("year10")),
        "30Y": _to_float(row.get("year30")),
    }
    return {"region": "US", "date": (row.get("date") or "")[:10], "tenors": tenors}


# ── ECB euro-area AAA yield curve (unchanged, keyless) ────────────────────────
_ECB_TENORS = ["2Y", "5Y", "10Y", "30Y"]


async def _ecb_tenor(client: httpx.AsyncClient, tenor: str) -> tuple[str, float | None, str | None]:
    series = f"B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor}"
    r = await client.get(f"{ECB_YC}/{series}", params={"lastNObservations": 1, "format": "csvdata"})
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows:
        return tenor, None, None
    row = rows[-1]
    val = row.get("OBS_VALUE")
    return tenor, (float(val) if val else None), row.get("TIME_PERIOD")


async def _eu_bonds() -> dict:
    async with httpx.AsyncClient(timeout=20, headers=_UA) as client:
        results = await asyncio.gather(
            *(_ecb_tenor(client, t) for t in _ECB_TENORS), return_exceptions=True
        )
    tenors, latest_date = {}, None
    for res in results:
        if isinstance(res, Exception):
            continue
        tenor, val, dt = res
        tenors[tenor] = val
        if dt and (latest_date is None or dt > latest_date):
            latest_date = dt
    return {"region": "EU", "date": latest_date or "", "tenors": tenors}


def _bp(a, b):
    """Spread in basis points, or None if either side is missing."""
    return round((a - b) * 100) if (a is not None and b is not None) else None


async def get_bonds() -> dict:
    """US (FMP) + EU (ECB) curves plus the two spreads macro watchers care about."""
    async def produce():
        us, eu = {}, {}
        try:
            us = await _us_bonds()
        except Exception as e:                      # noqa: BLE001
            logger.warning("US bonds failed: %s", e)
            us = {"region": "US", "date": "", "tenors": {}}
        try:
            eu = await _eu_bonds()
        except Exception as e:                      # noqa: BLE001
            logger.warning("EU bonds failed: %s", e)
            eu = {"region": "EU", "date": "", "tenors": {}}
        ust, eut = us.get("tenors", {}), eu.get("tenors", {})
        spreads = {
            "us_10y_minus_2y": _bp(ust.get("10Y"), ust.get("2Y")),   # 2s10s (inversion)
            "us_minus_eu_10y": _bp(ust.get("10Y"), eut.get("10Y")),  # transatlantic spread
        }
        return {"us": us, "eu": eu, "spreads": spreads}

    return await _cached("bonds", 1800, produce)    # yields update daily → 30 min TTL


# ── Scheduler jobs (called from main.py's APScheduler) ────────────────────────
async def refresh_quotes_cache():
    """Warm the live-quote caches (indices, VIX, heatmap, watchlist)."""
    if not FMP_KEY:
        logger.info("FMP_API_KEY not set — equity quotes idle (set it to populate Markets)")
        return
    watch = [s["symbol"] for s in INDICES] + [VIX_SYMBOL]
    results = await asyncio.gather(
        get_indices(), get_vix(), get_heatmap(), get_quotes(watch),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.warning("quote cache refresh partial failure: %s", r)


async def refresh_bonds_cache():
    """Warm the US + EU yield-curve cache (daily data → every ~30 min)."""
    try:
        await get_bonds()
    except Exception as e:                          # noqa: BLE001
        logger.warning("bond cache refresh failed: %s", e)
