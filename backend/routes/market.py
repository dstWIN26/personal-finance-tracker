"""Read-only market-data routes backing the Markets & Trading tabs.

All handlers are async and lean on the cached clients in
`backend.integrations.market`, so polling from the browser stays cheap and never
exceeds the upstream rate limits.
"""
from fastapi import APIRouter, Query

from backend.integrations import market

router = APIRouter(prefix="/market", tags=["market"])

# Whitelisted symbols the Trading watchlist may chart (defence against SSRF via
# the `symbol` param — only known indices/sectors/VIX are forwarded to Yahoo).
_ALLOWED = (
    {s["symbol"] for s in market.INDICES}
    | {s["symbol"] for s in market.SECTORS}
    | {market.VIX_SYMBOL}
)

_RANGE_INTERVAL = {
    "1d": "5m", "5d": "15m", "1mo": "1d", "3mo": "1d",
    "1y": "1wk", "max": "1mo",
}


@router.get("/indices")
async def indices():
    return await market.get_indices()


@router.get("/vix")
async def vix():
    return await market.get_vix() or {}


@router.get("/fx")
async def fx():
    """EUR-base daily FX rates, used to render amounts in the chosen base currency."""
    return await market.get_fx_rates()


@router.get("/quotes")
async def quotes(symbols: str = Query(..., description="Comma-separated symbols")):
    wanted = [s for s in (symbols.split(",")) if s in _ALLOWED]
    return await market.get_quotes(wanted)


@router.get("/bonds")
async def bonds():
    return await market.get_bonds()


@router.get("/heatmap")
async def heatmap():
    return await market.get_heatmap()


@router.get("/history")
async def history(
    symbol: str = Query(..., description="One whitelisted symbol"),
    range: str = Query("1mo"),
):
    if symbol not in _ALLOWED:
        return {"symbol": symbol, "candles": [], "meta": {}, "error": "symbol not allowed"}
    rng = range if range in _RANGE_INTERVAL else "1mo"
    return await market.get_history(symbol, rng=rng, interval=_RANGE_INTERVAL[rng])


@router.get("/watchlist")
async def watchlist():
    """Default Trading watchlist: the major indices + VIX, one quote each."""
    syms = [s["symbol"] for s in market.INDICES] + [market.VIX_SYMBOL]
    return await market.get_quotes(syms)
