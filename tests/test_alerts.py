"""Email-alert gating + recipient override (no real SMTP)."""
from backend import alerts
from backend import database as db


def test_alert_enabled_defaults_true():
    assert alerts.alert_enabled("security") is True
    assert alerts.alert_enabled("budget") is True
    assert alerts.alert_enabled("unknown") is True


def test_alert_enabled_respects_setting():
    db.set_setting("alerts_budget_enabled", "0")
    assert alerts.alert_enabled("budget") is False
    db.set_setting("alerts_budget_enabled", "1")
    assert alerts.alert_enabled("budget") is True


def test_recipient_prefers_override(monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "env@example.com")
    assert alerts.recipient() == "env@example.com"
    db.set_setting("alerts_email_to", "override@example.com")
    assert alerts.recipient() == "override@example.com"


def test_budget_alerts_respect_toggle(monkeypatch):
    # check_and_alert isn't stubbed (captured_alerts only replaces the security
    # path), so we can drive the real gate with an actually-exceeded limit.
    from datetime import date
    sent = []
    monkeypatch.setattr(alerts, "email_configured", lambda: True)
    monkeypatch.setattr(alerts, "send_email", lambda *a, **k: sent.append(k.get("to")))
    with db.connect() as conn:
        conn.execute("INSERT INTO limits (category, amount, period) VALUES (NULL, 100, 'monthly')")
    db.insert_transaction(source="x", date=date.today().isoformat(),
                          description="big", category="food", amount=-200)
    db.set_setting("alerts_email_to", "me@example.com")

    db.set_setting("alerts_budget_enabled", "0")
    alerts.check_and_alert()
    assert sent == []                                  # disabled → nothing sent

    db.set_setting("alerts_budget_enabled", "1")
    alerts.check_and_alert()
    assert sent == ["me@example.com"]                  # enabled + over limit → sent to override
