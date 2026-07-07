"""Tests for the settings DB helpers and the /settings routes."""
from datetime import datetime, timedelta, timezone

import pytest

from backend import database as db
from backend.integrations import enable_banking as eb


def _ts(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


# ── DB helpers ───────────────────────────────────────────────────────────────

def test_app_settings_roundtrip():
    assert db.get_setting("display_name", "x") == "x"
    db.set_setting("display_name", "Dustin")
    db.set_setting("display_name", "Dustin A")        # upsert
    assert db.get_setting("display_name") == "Dustin A"
    assert db.get_all_settings()["display_name"] == "Dustin A"


def test_bank_connection_crud_with_accounts():
    cid = db.insert_bank_connection(
        provider="enable_banking", aspsp_name="Revolut", aspsp_country="DE",
        source_key="revolut", session_id="sess-1",
        accounts=[{"uid": "a1", "iban": "DE00...1234"}], expires_at="2026-09-01T00:00:00+00:00",
    )
    rows = db.list_bank_connections()
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["accounts"][0]["uid"] == "a1"        # JSON round-trips to a list
    db.set_bank_connection_status(cid, "expired")
    assert db.list_bank_connections()[0]["status"] == "expired"
    db.delete_bank_connection(cid)
    assert db.list_bank_connections() == []


def test_link_state_is_single_use():
    db.create_link_state("st-1", "enable_banking", "Revolut", "DE", "revolut", _ts(15))
    first = db.consume_link_state("st-1")
    assert first is not None and first["aspsp_name"] == "Revolut"
    assert db.consume_link_state("st-1") is None         # already consumed


def test_link_state_expired_is_rejected():
    db.create_link_state("st-old", "enable_banking", "Revolut", "DE", "revolut", _ts(-1))
    assert db.consume_link_state("st-old") is None


# ── Routes ───────────────────────────────────────────────────────────────────

def test_settings_requires_auth(client):
    assert client.get("/settings/integrations").status_code == 401


def test_integrations_status_exposes_no_secrets(auth_client, monkeypatch):
    r = auth_client.get("/settings/integrations")
    assert r.status_code == 200
    d = r.json()
    # Trade Republic is CSV-import-only: no credentials, reports import state only.
    assert d["trade_republic"]["import_only"] is True
    assert d["trade_republic"]["linked"] is False       # nothing imported yet
    assert isinstance(d["enable_banking"]["configured"], bool)
    assert d["banks"] == []
    # No credential-ish material must ever appear in this payload.
    body = r.text.lower()
    for leak in ("secret", "session_id", "private", "pin"):
        assert leak not in body


def test_integrations_masks_iban(auth_client):
    db.insert_bank_connection(
        provider="enable_banking", aspsp_name="Revolut", aspsp_country="DE",
        source_key="revolut", session_id="sess", accounts=[{"name": "Main", "iban": "DE89370400440532013000"}],
    )
    bank = auth_client.get("/settings/integrations").json()["banks"][0]
    assert bank["accounts"][0]["iban"] == "••••3000"     # only the last 4 are shown


def test_profile_roundtrip_and_ignores_unknown_keys(auth_client):
    assert auth_client.get("/settings/profile").json()["base_currency"] == "EUR"
    auth_client.post("/settings/profile", json={"base_currency": "USD", "evil": "x"})
    out = auth_client.get("/settings/profile").json()
    assert out["base_currency"] == "USD"
    assert "evil" not in out


def test_connect_requires_configuration(auth_client, monkeypatch):
    monkeypatch.setattr(eb, "is_configured", lambda: False)
    r = auth_client.post("/settings/banks/connect",
                         json={"aspsp_name": "Revolut", "country": "DE"})
    assert r.status_code == 400


def test_connect_then_complete_links_bank(auth_client, monkeypatch):
    captured = {}

    async def fake_start_auth(name, country, state):
        captured["state"] = state
        return "https://bank.example/consent"

    async def fake_complete(code):
        return ("sess-xyz", [{"uid": "a1", "name": "Main", "iban": "DE00...9999"}],
                "2026-09-01T00:00:00+00:00")

    async def noop():
        return None

    monkeypatch.setattr(eb, "is_configured", lambda: True)
    monkeypatch.setattr(eb, "start_auth", fake_start_auth)
    monkeypatch.setattr(eb, "complete_session", fake_complete)
    monkeypatch.setattr(eb, "sync_bank_connections", noop)

    r = auth_client.post("/settings/banks/connect",
                         json={"aspsp_name": "Deutsche Bank", "country": "DE"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://bank.example/consent"
    state = captured["state"]

    r2 = auth_client.post("/settings/banks/complete", json={"code": "the-code", "state": state})
    assert r2.status_code == 200
    assert r2.json()["accounts"] == 1

    banks = db.list_bank_connections()
    assert len(banks) == 1 and banks[0]["source_key"] == "deutsche_bank"

    # State is single-use: replaying the same state must now fail.
    again = auth_client.post("/settings/banks/complete", json={"code": "x", "state": state})
    assert again.status_code == 400


def test_complete_with_unknown_state_is_rejected(auth_client):
    r = auth_client.post("/settings/banks/complete", json={"code": "x", "state": "nope"})
    assert r.status_code == 400


# ── Sync status / Sync now ───────────────────────────────────────────────────

def test_integrations_reports_last_synced(auth_client):
    d = auth_client.get("/settings/integrations").json()
    assert set(d["last_synced"]) == {"positions", "balances", "transactions"}


def test_sync_now_runs_each_source_and_isolates_failures(auth_client, monkeypatch):
    import backend.integrations.revolut as rev
    import backend.integrations.enable_banking as eb2

    async def ok():
        return None

    async def boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr(rev, "sync_transactions", boom)
    monkeypatch.setattr(eb2, "sync_bank_connections", ok)

    r = auth_client.post("/settings/sync")
    assert r.status_code == 200
    res = r.json()["results"]
    assert res["banks"] == "ok"
    assert res["revolut"] == "error"               # one bad source doesn't fail the rest
    # Trade Republic is CSV-import-only, so it is NOT a sync source.
    assert "trade_republic_portfolio" not in res
    assert "last_synced" in r.json()


# ── Export ───────────────────────────────────────────────────────────────────

def test_export_transactions_csv(auth_client):
    db.insert_transaction(source="revolut", date="2026-06-01",
                          description="Coffee", category="food", amount=-3.5)
    r = auth_client.get("/settings/export/transactions.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "source,date,description,category,amount,currency"
    assert any("Coffee" in ln for ln in lines[1:])


def test_export_neutralises_spreadsheet_formula_injection(auth_client):
    # A description crafted to be a spreadsheet formula must be defused on export,
    # while a genuinely negative amount must stay numeric (not quoted/mangled).
    db.insert_transaction(source="manual", date="2026-06-01",
                          description="=2+5", category="food", amount=-9.0)
    r = auth_client.get("/settings/export/transactions.csv")
    assert r.status_code == 200
    target = [ln for ln in r.text.strip().splitlines() if "2+5" in ln][0]
    assert "'=2+5" in target          # formula lead defused with a leading apostrophe
    assert "-9.0" in target           # numeric amount untouched (still negative)


def test_export_all_json_has_no_secrets(auth_client):
    r = auth_client.get("/settings/export/all.json")
    assert r.status_code == 200
    d = r.json()
    for k in ("exported_at", "transactions", "positions", "balances", "limits"):
        assert k in d
    body = r.text.lower()
    for leak in ("password", "token_hash", "credential", "session"):
        assert leak not in body


# ── Trade Republic CSV import ─────────────────────────────────────────────────

def _import(auth_client, text, name="tr.csv"):
    return auth_client.post("/settings/import/transactions",
                            files={"file": (name, text.encode("utf-8"), "text/csv")})


def test_import_requires_auth(client):
    r = client.post("/settings/import/transactions",
                    files={"file": ("tr.csv", b"date,amount\n2026-01-01,1\n", "text/csv")})
    assert r.status_code == 401


def test_import_eu_format_semicolons_dates_and_signs(auth_client):
    # German dates, semicolon delimiter, European decimals, signed amount column.
    text = ("Date;Type;Description;Amount;Currency\r\n"
            "01.05.2026;Card;Coffee Shop;-3,50;EUR\r\n"
            "02.05.2026;Deposit;Salary;1.234,56;EUR\r\n")
    r = _import(auth_client, text)
    assert r.status_code == 200
    d = r.json()
    assert d["imported"] == 2 and d["skipped"] == 0 and d["errors"] == 0
    assert d["spending_rows"] == 1                      # the -3,50 outflow

    with db.connect() as conn:
        rows = {row["description"]: row for row in conn.execute(
            "SELECT description, date, amount, currency, source, category FROM transactions").fetchall()}
    assert rows["Coffee Shop"]["amount"] == -3.5        # sign preserved -> shows as spending
    assert rows["Coffee Shop"]["date"] == "2026-05-01"  # DD.MM.YYYY -> ISO
    assert rows["Coffee Shop"]["source"] == "trade_republic"
    assert rows["Coffee Shop"]["category"] == "Card"
    assert rows["Salary"]["amount"] == 1234.56          # 1.234,56 -> 1234.56


def test_import_is_idempotent(auth_client):
    text = "date,description,amount\n2026-05-01,Coffee,-3.50\n"
    assert _import(auth_client, text).json()["imported"] == 1
    again = _import(auth_client, text).json()
    assert again["imported"] == 0 and again["skipped"] == 1   # UNIQUE constraint dedupes


def test_import_inflow_outflow_split(auth_client):
    text = ("date,description,inflow,outflow\n"
            "2026-05-01,Buy AAPL,,100.00\n"
            "2026-05-02,Dividend,5.00,\n")
    r = _import(auth_client, text)
    assert r.status_code == 200 and r.json()["imported"] == 2
    with db.connect() as conn:
        amounts = {row["description"]: row["amount"] for row in conn.execute(
            "SELECT description, amount FROM transactions").fetchall()}
    assert amounts["Buy AAPL"] == -100.0                # outflow -> negative
    assert amounts["Dividend"] == 5.0                   # inflow -> positive


def test_import_rejects_file_without_date_or_amount(auth_client):
    r = _import(auth_client, "foo,bar\n1,2\n")
    assert r.status_code == 422
    assert "date" in r.json()["detail"].lower()


def test_import_skips_unparseable_rows_without_failing(auth_client):
    text = ("date,description,amount\n"
            "2026-05-01,Good,-1.00\n"
            "not-a-date,Bad date,-2.00\n"
            "2026-05-03,Bad amount,abc\n")
    d = _import(auth_client, text).json()
    assert d["imported"] == 1 and d["errors"] == 2 and d["total"] == 3


# ── Email-alert preferences ──────────────────────────────────────────────────

def test_alerts_settings_roundtrip(auth_client):
    base = auth_client.get("/settings/alerts").json()
    assert base["security_enabled"] is True and base["budget_enabled"] is True
    auth_client.post("/settings/alerts",
                     json={"security_enabled": False, "budget_enabled": True, "email_to": "me@example.com"})
    out = auth_client.get("/settings/alerts").json()
    assert out["security_enabled"] is False
    assert out["budget_enabled"] is True
    assert out["email_to"] == "me@example.com"
