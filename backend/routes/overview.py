"""Unified TR + Revolut home view backing the Overview tab.

Aggregates the existing `positions`, `balances` and `transactions` tables into a
single net-worth / allocation / accounts payload — no new data sources.
"""
from datetime import date

from fastapi import APIRouter
from backend.database import connect

router = APIRouter(prefix="/overview", tags=["overview"])


def _latest_positions(conn):
    return [dict(r) for r in conn.execute("""
        SELECT p.* FROM positions p
        INNER JOIN (
            SELECT isin, MAX(fetched_at) AS latest FROM positions GROUP BY isin
        ) l ON p.isin = l.isin AND p.fetched_at = l.latest
    """).fetchall()]


def _latest_balances(conn):
    """Most recent balance snapshot per source."""
    return [dict(r) for r in conn.execute("""
        SELECT b.* FROM balances b
        INNER JOIN (
            SELECT source, MAX(fetched_at) AS latest FROM balances GROUP BY source
        ) l ON b.source = l.source AND b.fetched_at = l.latest
    """).fetchall()]


@router.get("/")
def overview():
    with connect() as conn:
        positions = _latest_positions(conn)
        balances = _latest_balances(conn)

        invested = sum(p["quantity"] * (p["current_price"] or 0) for p in positions)
        invested_pl = sum(p["pl_eur"] or 0 for p in positions)
        cash = sum(b["balance"] or 0 for b in balances)
        net_worth = invested + cash

        # Cash split by source (TR cash vs Revolut balance).
        cash_by_source = {}
        for b in balances:
            cash_by_source[b["source"]] = cash_by_source.get(b["source"], 0) + (b["balance"] or 0)

        # Today's spending = sum of today's negative transactions.
        today = date.today().isoformat()
        today_spend = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
            "WHERE date = ? AND amount < 0", (today,)
        ).fetchone()["s"]

        # Net-worth trend: total balance snapshots over time (cash series proxy).
        trend = [dict(r) for r in conn.execute("""
            SELECT DATE(fetched_at) AS day, SUM(balance) AS total
            FROM balances
            GROUP BY DATE(fetched_at)
            ORDER BY day DESC LIMIT 30
        """).fetchall()][::-1]

        recent = [dict(r) for r in conn.execute(
            "SELECT id, source, date, description, category, amount, currency "
            "FROM transactions ORDER BY date DESC, id DESC LIMIT 8"
        ).fetchall()]

    accounts = [
        {
            "source": "trade_republic",
            "label": "Trade Republic",
            "invested": invested,
            "invested_pl": invested_pl,
            "cash": cash_by_source.get("trade_republic", 0),
        },
        {
            "source": "revolut",
            "label": "Revolut",
            "invested": 0,
            "invested_pl": 0,
            "cash": cash_by_source.get("revolut", 0),
        },
    ]

    return {
        "net_worth": net_worth,
        "invested": invested,
        "invested_pl": invested_pl,
        "cash": cash,
        "today_spend": abs(today_spend),
        "allocation": {"invested": invested, "cash": cash},
        "accounts": accounts,
        "trend": trend,
        "recent": recent,
    }
