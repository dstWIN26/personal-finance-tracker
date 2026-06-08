"""Live market data — keyless providers.

  * Yahoo Finance chart API  → indices, VIX, OHLC history, sparklines, ranges,
                               sector ETFs (for the heatmap)
  * US Treasury XML feed      → US par yield curve (2/5/10/30Y)
  * ECB SDW yield-curve API   → euro-area AAA (≈Bund) spot rates (2/5/10/30Y)

All upstreams are free and require no API key. Responses are cached in-process
with a short TTL so the dashboard can poll without hammering the providers.
Every function degrades gracefully: a failing upstream yields an empty/partial
payload and is logged, never raised to the route.
"""
import time
import asyncio
import logging
import csv
import io
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

# ── Symbol presets ────────────────────────────────────────────────────────────
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

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
TREASURY_XML = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)
ECB_YC = "https://data-api.ecb.europa.eu/service/data/YC"
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) finance-tracker/1.0"}

# ── Tiny in-process TTL cache ─────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, object]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


async def _cached(key: str, ttl: int, producer):
    """Return cached value for `key`, or await `producer()` and cache it.

    A per-key lock collapses concurrent misses into a single upstream fetch.
    """
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _CACHE.get(key)                       # re-check inside the lock
        if hit and hit[0] > time.monotonic():
            return hit[1]
        value = await producer()
        _CACHE[key] = (time.monotonic() + ttl, value)
        return value


# ── Yahoo ─────────────────────────────────────────────────────────────────────
async def _yahoo_chart(client: httpx.AsyncClient, symbol: str, rng: str, interval: str):
    r = await client.get(
        YAHOO_CHART + symbol,
        params={"range": rng, "interval": interval, "includePrePost": "false"},
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"]
    return result[0] if result else None


def _quote_from_chart(res: dict, name: str | None = None) -> dict | None:
    if not res:
        return None
    meta = res.get("meta", {})
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return None
    change = (price - prev) if prev is not None else None
    change_pct = (change / prev * 100) if (change is not None and prev) else None
    # Intraday closes → sparkline (drop nulls)
    spark: list[float] = []
    try:
        closes = res["indicators"]["quote"][0]["close"]
        spark = [c for c in closes if c is not None]
    except (KeyError, IndexError, TypeError):
        spark = []
    return {
        "symbol": meta.get("symbol"),
        "name": name or meta.get("shortName") or meta.get("longName") or meta.get("symbol"),
        "price": price,
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "currency": meta.get("currency"),
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "week52_high": meta.get("fiftyTwoWeekHigh"),
        "week52_low": meta.get("fiftyTwoWeekLow"),
        "volume": meta.get("regularMarketVolume"),
        "market_time": meta.get("regularMarketTime"),
        "spark": spark[-60:],
    }


async def _quotes(symbols: list[dict], rng="1d", interval="5m") -> list[dict]:
    """Fetch a batch of quotes concurrently. `symbols` = [{symbol,name}, ...]."""
    async with httpx.AsyncClient(timeout=15, headers=_UA) as client:
        async def one(item):
            try:
                res = await _yahoo_chart(client, item["symbol"], rng, interval)
                return _quote_from_chart(res, item.get("name"))
            except Exception as e:                      # noqa: BLE001 — degrade per symbol
                logger.warning("quote failed for %s: %s", item["symbol"], e)
                return None
        quotes = await asyncio.gather(*(one(s) for s in symbols))
    return [q for q in quotes if q]


# TTLs are sized so the scheduler warm-jobs (every 60s) keep these always-fresh:
# a user request between refreshes still hits a valid cache entry.
_QUOTE_TTL = 90


async def get_indices() -> list[dict]:
    return await _cached("indices", _QUOTE_TTL, lambda: _quotes(INDICES))


async def get_vix() -> dict | None:
    async def produce():
        rows = await _quotes([{"symbol": VIX_SYMBOL, "name": "VIX"}], rng="1mo", interval="1d")
        return rows[0] if rows else None
    return await _cached("vix", _QUOTE_TTL, produce)


async def get_quotes(symbols: list[str]) -> list[dict]:
    items = [{"symbol": s, "name": None} for s in symbols]
    key = "q:" + ",".join(sorted(symbols))
    return await _cached(key, _QUOTE_TTL, lambda: _quotes(items))


async def get_heatmap() -> list[dict]:
    async def produce():
        rows = await _quotes(SECTORS, rng="1d", interval="15m")
        return [{"name": r["name"], "symbol": r["symbol"], "change_pct": r["change_pct"]}
                for r in rows]
    return await _cached("heatmap", _QUOTE_TTL, produce)


async def get_history(symbol: str, rng: str = "1mo", interval: str = "1d") -> dict:
    """OHLCV series for candlestick charts."""
    key = f"h:{symbol}:{rng}:{interval}"

    async def produce():
        async with httpx.AsyncClient(timeout=15, headers=_UA) as client:
            try:
                res = await _yahoo_chart(client, symbol, rng, interval)
            except Exception as e:                      # noqa: BLE001
                logger.warning("history failed for %s: %s", symbol, e)
                return {"symbol": symbol, "candles": [], "meta": {}}
        if not res:
            return {"symbol": symbol, "candles": [], "meta": {}}
        ts = res.get("timestamp", []) or []
        q = (res.get("indicators", {}).get("quote", [{}]) or [{}])[0]
        o, h, l, c, v = (q.get(k, []) for k in ("open", "high", "low", "close", "volume"))
        candles = []
        for i, t in enumerate(ts):
            if i < len(c) and c[i] is not None and o[i] is not None:
                candles.append({
                    "t": t * 1000,                      # ms epoch for Chart.js time scale
                    "o": o[i], "h": h[i], "l": l[i], "c": c[i],
                    "v": (v[i] if i < len(v) else None),
                })
        meta = res.get("meta", {})
        return {
            "symbol": meta.get("symbol", symbol),
            "name": meta.get("shortName") or meta.get("longName") or symbol,
            "candles": candles,
            "meta": _quote_from_chart(res),
        }

    # Intraday ranges churn; daily ranges are stable → cache intraday shorter.
    ttl = 60 if interval.endswith(("m", "h")) else 300
    return await _cached(key, ttl, produce)


# ── US Treasury par yield curve ───────────────────────────────────────────────
# Atom/OData feed: <m:properties> (metadata ns) wraps <d:BC_*> fields (data ns).
_M = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
_D = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
_US_TENORS = {"2Y": "BC_2YEAR", "5Y": "BC_5YEAR", "10Y": "BC_10YEAR", "30Y": "BC_30YEAR"}


async def _us_bonds() -> dict:
    from datetime import date
    async with httpx.AsyncClient(timeout=20, headers=_UA) as client:
        r = await client.get(TREASURY_XML, params={
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value": str(date.today().year),
        })
        r.raise_for_status()
    root = ET.fromstring(r.content)
    latest_date, latest_props = None, None
    for props in root.iter(f"{_M}properties"):
        d = props.findtext(f"{_D}NEW_DATE")
        if d and (latest_date is None or d > latest_date):
            latest_date, latest_props = d, props
    tenors = {}
    if latest_props is not None:
        for label, tag in _US_TENORS.items():
            val = latest_props.findtext(f"{_D}{tag}")
            tenors[label] = float(val) if val not in (None, "") else None
    return {"region": "US", "date": (latest_date or "")[:10], "tenors": tenors}


# ── ECB euro-area AAA yield curve ─────────────────────────────────────────────
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
    """US + EU curves plus the two spreads macro watchers care about."""
    async def produce():
        us, eu = {}, {}
        try:
            us = await _us_bonds()
        except Exception as e:                          # noqa: BLE001
            logger.warning("US bonds failed: %s", e)
            us = {"region": "US", "date": "", "tenors": {}}
        try:
            eu = await _eu_bonds()
        except Exception as e:                          # noqa: BLE001
            logger.warning("EU bonds failed: %s", e)
            eu = {"region": "EU", "date": "", "tenors": {}}
        ust, eut = us.get("tenors", {}), eu.get("tenors", {})
        spreads = {
            "us_10y_minus_2y": _bp(ust.get("10Y"), ust.get("2Y")),   # 2s10s (inversion)
            "us_minus_eu_10y": _bp(ust.get("10Y"), eut.get("10Y")),  # transatlantic spread
        }
        return {"us": us, "eu": eu, "spreads": spreads}

    return await _cached("bonds", 1800, produce)        # yields update daily → 30 min TTL


# ── Scheduler jobs (called from main.py's APScheduler) ────────────────────────
async def refresh_quotes_cache():
    """Warm the live-quote caches (indices, VIX, heatmap, watchlist) every ~60s."""
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
    except Exception as e:                              # noqa: BLE001
        logger.warning("bond cache refresh failed: %s", e)
