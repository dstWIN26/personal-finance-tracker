"""Overview aggregation over seeded TR + Revolut data."""
import datetime as dt

from backend.database import upsert_position, upsert_balance, insert_transaction


def _seed():
    # Invested: 10 @ 100 = 1000, +50 P&L
    upsert_position("ISIN1", "Test ETF", 10, 95.0, 100.0, 5.26, 50.0)
    # Cash: TR 200 + Revolut 300 = 500
    upsert_balance("trade_republic", 200.0)
    upsert_balance("revolut", 300.0)
    # Today's spend: -25
    today = dt.date.today().isoformat()
    insert_transaction("revolut", today, "Coffee", "dining", -25.0)
    insert_transaction("revolut", today, "Salary", "income", 1000.0)   # income ignored in spend


def test_overview_aggregates(auth_client):
    _seed()
    d = auth_client.get("/overview/").json()
    assert d["invested"] == 1000.0
    assert d["invested_pl"] == 50.0
    assert d["cash"] == 500.0
    assert d["net_worth"] == 1500.0
    assert d["today_spend"] == 25.0                 # only the negative txn, abs value
    assert {a["source"] for a in d["accounts"]} == {"trade_republic", "revolut"}
    assert d["allocation"] == {"invested": 1000.0, "cash": 500.0}


def test_overview_empty_is_zeroed(auth_client):
    d = auth_client.get("/overview/").json()
    assert d["net_worth"] == 0
    assert d["recent"] == []
