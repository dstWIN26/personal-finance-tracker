"""Unit tests for the Enable Banking integration (offline, no network)."""
import base64
import json

import pytest

from backend.integrations import enable_banking as eb


def _decode_segment(seg: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


@pytest.fixture
def rsa_keyfile(tmp_path, monkeypatch):
    """Generate a throwaway RSA key and point the module at it."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / "eb_key.pem"
    path.write_bytes(pem)
    monkeypatch.setattr(eb, "APP_ID", "app-test-123")
    monkeypatch.setattr(eb, "KEYFILE", str(path))
    return pem


def test_is_configured(rsa_keyfile, monkeypatch):
    assert eb.is_configured() is True
    monkeypatch.setattr(eb, "APP_ID", "")
    assert eb.is_configured() is False


def test_build_jwt_structure_and_no_key_leak(rsa_keyfile):
    token = eb.build_jwt()
    header_b64, payload_b64, sig_b64 = token.split(".")
    header = _decode_segment(header_b64)
    payload = _decode_segment(payload_b64)

    assert header == {"typ": "JWT", "alg": "RS256", "kid": "app-test-123"}
    assert payload["iss"] == "enablebanking.com"
    assert payload["aud"] == "api.enablebanking.com"
    assert payload["exp"] > payload["iat"]
    # The private key material must never appear in the produced token.
    assert b"PRIVATE KEY" not in rsa_keyfile or "PRIVATE" not in token
    assert sig_b64  # signature present


def test_normalize_transaction_debit_is_negative():
    tx = {
        "transaction_amount": {"amount": "12.50", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-06-01T10:00:00Z",
        "remittance_information": ["REWE Berlin"],
    }
    date, desc, amount, currency = eb._normalize_transaction(tx)
    assert date == "2026-06-01"
    assert desc == "REWE Berlin"
    assert amount == -12.5
    assert currency == "EUR"


def test_normalize_transaction_credit_is_positive():
    tx = {
        "transaction_amount": {"amount": "2000", "currency": "EUR"},
        "credit_debit_indicator": "CRDT",
        "value_date": "2026-06-02",
        "creditor": {"name": "ACME GmbH"},
    }
    date, desc, amount, currency = eb._normalize_transaction(tx)
    assert date == "2026-06-02"
    assert amount == 2000.0
    assert desc == "ACME GmbH"


def test_pick_balance_prefers_closing():
    balances = [
        {"balance_type": "ITAV", "balance_amount": {"amount": "5"}},
        {"balance_type": "CLBD", "balance_amount": {"amount": "10"}},
    ]
    assert eb._pick_balance(balances)["balance_type"] == "CLBD"
    assert eb._pick_balance([]) is None


def test_redirect_url_defaults_to_origin(monkeypatch):
    monkeypatch.delenv("ENABLE_BANKING_REDIRECT_URL", raising=False)
    monkeypatch.setattr(eb.config, "RP_ORIGIN", "https://finance.example.com")
    assert eb.redirect_url() == "https://finance.example.com/settings/banks/callback"
