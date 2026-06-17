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
    monkeypatch.setenv("TRADE_REPUBLIC_PHONE", "+490000")
    r = auth_client.get("/settings/integrations")
    assert r.status_code == 200
    d = r.json()
    assert d["trade_republic"]["phone_set"] is True
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
    import backend.integrations.trade_republic as tr
    import backend.integrations.revolut as rev
    import backend.integrations.enable_banking as eb2

    async def ok():
        return None

    async def boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr(tr, "sync_portfolio", ok)
    monkeypatch.setattr(tr, "sync_tr_transactions", ok)
    monkeypatch.setattr(rev, "sync_transactions", boom)
    monkeypatch.setattr(eb2, "sync_bank_connections", ok)

    r = auth_client.post("/settings/sync")
    assert r.status_code == 200
    res = r.json()["results"]
    assert res["trade_republic_portfolio"] == "ok"
    assert res["banks"] == "ok"
    assert res["revolut"] == "error"               # one bad source doesn't fail the rest
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


def test_export_all_json_has_no_secrets(auth_client):
    r = auth_client.get("/settings/export/all.json")
    assert r.status_code == 200
    d = r.json()
    for k in ("exported_at", "transactions", "positions", "balances", "limits"):
        assert k in d
    body = r.text.lower()
    for leak in ("password", "token_hash", "credential", "session"):
        assert leak not in body


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
