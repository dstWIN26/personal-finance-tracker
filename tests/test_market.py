"""Market-data parsing + cache (offline — no provider calls)."""
import asyncio

from backend.integrations import market
from backend.routes.market import _ALLOWED


def test_to_float_coerces():
    assert market._to_float(1.5) == 1.5
    assert market._to_float("2.34") == 2.34
    assert market._to_float("-0.52%") == -0.52      # FMP sometimes returns "x%" strings
    assert market._to_float(None) is None
    assert market._to_float("n/a") is None


def test_fmp_symbol_mapping():
    # European indices are served via US-listed ETF proxies (free tier is US-only).
    assert market._fmp("^GSPC") == "SPY"
    assert market._fmp("^GDAXI") == "EWG"
    assert market._fmp("^STOXX50E") == "FEZ"
    assert market._fmp("^FTSE") == "EWU"
    assert market._fmp("XLK") == "XLK"              # sector ETFs map to themselves
    assert market._fmp("^VIX") == "^VIX"


def test_quote_from_fmp_parses():
    row = {
        "symbol": "SPY", "name": "SPDR S&P 500", "price": 600.0,
        "previousClose": 594.0, "change": 6.0, "changesPercentage": 1.01,
        "dayHigh": 601.0, "dayLow": 596.0, "yearHigh": 610.0, "yearLow": 500.0,
        "volume": 1234, "timestamp": 1700000000,
    }
    q = market._quote_from_fmp(row, symbol="^GSPC", name="S&P 500")
    assert q["symbol"] == "^GSPC"                    # app-facing symbol preserved
    assert q["name"] == "S&P 500"
    assert q["price"] == 600.0
    assert q["prev_close"] == 594.0
    assert q["change"] == 6.0
    assert q["change_pct"] == 1.01
    assert q["week52_high"] == 610.0 and q["week52_low"] == 500.0


def test_quote_from_fmp_handles_missing_price():
    assert market._quote_from_fmp({"symbol": "SPY"}, "^GSPC") is None
    assert market._quote_from_fmp(None, "^GSPC") is None


def test_history_meta_from_candles():
    candles = [
        {"t": 1, "o": 10, "h": 11, "l": 9, "c": 10},
        {"t": 2, "o": 10, "h": 12, "l": 10, "c": 11},
    ]
    m = market._history_meta(candles, "^GSPC")
    assert m["price"] == 11 and m["prev_close"] == 10
    assert m["change"] == 1
    assert m["week52_high"] == 11 and m["week52_low"] == 10
    assert market._history_meta([], "^GSPC") == {}


def test_us_bonds_empty_without_key(monkeypatch):
    # No FMP key → US curve degrades to empty WITHOUT any network call.
    monkeypatch.setattr(market, "FMP_KEY", "")
    out = asyncio.run(market._us_bonds())
    assert out == {"region": "US", "date": "", "tenors": {}}


def test_fmp_quotes_empty_without_key(monkeypatch):
    monkeypatch.setattr(market, "FMP_KEY", "")
    assert asyncio.run(market._fmp_quotes([{"symbol": "^GSPC", "name": "S&P 500"}])) == []


def test_bp_spread():
    assert market._bp(4.55, 4.17) == 38
    assert market._bp(2.50, 3.00) == -50
    assert market._bp(None, 1.0) is None
    assert market._bp(1.0, None) is None


def test_cache_collapses_calls_within_ttl():
    calls = {"n": 0}

    async def producer():
        calls["n"] += 1
        return "value"

    async def run():
        a = await market._cached("unit-test-key", 60, producer)
        b = await market._cached("unit-test-key", 60, producer)
        return a, b

    a, b = asyncio.run(run())
    assert a == b == "value"
    assert calls["n"] == 1                          # second call served from cache


def test_cache_serves_stale_when_producer_returns_empty():
    # A transient upstream failure (empty payload) must not overwrite the last-good
    # value; the dashboard keeps showing stale data rather than flapping to error.
    out = {"v": ["good"]}

    async def producer():
        return out["v"]

    async def run():
        first = await market._cached("stale-empty", 0, producer)   # ttl 0 → expires at once
        out["v"] = []                                              # upstream now empty
        second = await market._cached("stale-empty", 0, producer)
        return first, second

    first, second = asyncio.run(run())
    assert first == ["good"]
    assert second == ["good"]                       # served stale, not the empty []


def test_cache_serves_stale_when_producer_raises():
    out = {"v": "good", "boom": False}

    async def producer():
        if out["boom"]:
            raise RuntimeError("upstream 429")
        return out["v"]

    async def run():
        first = await market._cached("stale-raise", 0, producer)
        out["boom"] = True                                        # upstream now throws
        second = await market._cached("stale-raise", 0, producer)
        return first, second

    first, second = asyncio.run(run())
    assert first == "good"
    assert second == "good"                         # exception swallowed, stale served


def test_cache_returns_empty_on_cold_failure():
    async def producer():
        return []

    assert asyncio.run(market._cached("cold-fail", 0, producer)) == []


def test_symbol_whitelist_contains_known_indices():
    assert "^GSPC" in _ALLOWED
    assert "^VIX" in _ALLOWED
    assert "rm -rf" not in _ALLOWED


def test_history_rejects_unknown_symbol(auth_client):
    # Not in the whitelist → handler returns an error without hitting any provider.
    r = auth_client.get("/market/history", params={"symbol": "EVIL", "range": "1mo"})
    assert r.status_code == 200
    assert r.json().get("error") == "symbol not allowed"
