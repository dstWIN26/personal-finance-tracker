"""Market-data parsing + cache (offline — no provider calls)."""
import asyncio

from backend.integrations import market
from backend.routes.market import _ALLOWED


def test_quote_from_chart_parses_meta():
    res = {
        "meta": {
            "symbol": "^GSPC", "regularMarketPrice": 5000.0, "chartPreviousClose": 4900.0,
            "regularMarketDayHigh": 5050.0, "regularMarketDayLow": 4950.0,
            "fiftyTwoWeekHigh": 5200.0, "fiftyTwoWeekLow": 4000.0, "currency": "USD",
        },
        "indicators": {"quote": [{"close": [4990.0, None, 5000.0]}]},
    }
    q = market._quote_from_chart(res, name="S&P 500")
    assert q["price"] == 5000.0
    assert round(q["change"], 2) == 100.0
    assert round(q["change_pct"], 4) == round(100 / 4900 * 100, 4)
    assert q["spark"] == [4990.0, 5000.0]          # nulls dropped
    assert q["name"] == "S&P 500"


def test_quote_from_chart_handles_missing_price():
    assert market._quote_from_chart({"meta": {}}) is None
    assert market._quote_from_chart(None) is None


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
    # A transient upstream failure (every symbol errors → empty payload) must not
    # overwrite the last-good value; the dashboard keeps showing stale data rather
    # than flapping into an error state.
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
    # No prior good value → an empty result is surfaced honestly (drives the
    # "Error" indicator only when data has genuinely never loaded).
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
