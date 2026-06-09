"""Auth core + HTTP behaviour."""
from backend import auth


# ── Core helpers (unit) ──────────────────────────────────────────────────────
def test_password_set_and_verify():
    auth.set_password("a-very-long-password-123")
    assert auth.password_enabled()
    assert auth.verify_password("a-very-long-password-123")
    assert not auth.verify_password("wrong-password-xxxxxxx")


def test_disable_password_is_passkey_only():
    auth.set_password("a-very-long-password-123")
    auth.disable_password()
    assert not auth.password_enabled()
    assert not auth.verify_password("a-very-long-password-123")


def test_session_create_validate_rotate_destroy(req):
    t1 = auth.create_session(req())
    assert auth._valid_session(t1)
    # Rotation: a new login invalidates the previous token.
    t2 = auth.create_session(req(), old_token=t1)
    assert not auth._valid_session(t1)
    assert auth._valid_session(t2)
    auth.destroy_session(t2)
    assert not auth._valid_session(t2)


def test_lockout_is_per_ip():
    for _ in range(auth.config.MAX_PW_FAILURES):
        auth.record_attempt("9.9.9.9", False)
    assert auth.is_locked_out("9.9.9.9")
    assert not auth.is_locked_out("8.8.8.8")


def test_challenge_is_single_use():
    cid = auth.store_challenge("register", "challenge-bytes")
    assert auth.consume_challenge(cid, "register") == "challenge-bytes"
    assert auth.consume_challenge(cid, "register") is None          # already consumed
    cid2 = auth.store_challenge("register", "x")
    assert auth.consume_challenge(cid2, "authenticate") is None      # wrong kind


# ── HTTP layer ───────────────────────────────────────────────────────────────
def test_protected_route_requires_session(client):
    assert client.get("/overview/").status_code == 401


def test_root_redirects_anonymous(client):
    assert client.get("/", follow_redirects=False).status_code == 303


def test_password_login_grants_access(client, password):
    assert client.post("/auth/password", json={"password": password}).status_code == 200
    assert client.get("/overview/").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200


def test_security_headers_present(client):
    h = client.get("/auth/status").headers
    assert "content-security-policy" in h
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"


def test_status_reflects_state(client, password):
    s = client.get("/auth/status").json()
    assert s["authenticated"] is False
    assert s["password_enabled"] is True
    assert s["has_passkey"] is False


def test_bruteforce_triggers_lockout(client, password):
    for _ in range(auth.config.MAX_PW_FAILURES + 1):
        client.post("/auth/password", json={"password": "nope"})
    assert client.post("/auth/password", json={"password": "nope"}).status_code == 429


def test_login_emits_security_alert(client, password, captured_alerts):
    client.post("/auth/password", json={"password": password})
    assert any("sign-in" in c["event"].lower() for c in captured_alerts)


def test_lockout_emits_single_alert(client, password, captured_alerts):
    for _ in range(auth.config.MAX_PW_FAILURES + 3):
        client.post("/auth/password", json={"password": "nope"})
    lock_alerts = [c for c in captured_alerts if "lock" in c["event"].lower()]
    assert len(lock_alerts) == 1          # fired once, at the transition
    assert lock_alerts[0]["warn"] is True


def test_passkey_register_requires_session(client):
    assert client.post("/auth/passkey/register/begin").status_code == 401


def test_passkey_login_begin_is_public(client):
    r = client.post("/auth/passkey/login/begin")
    assert r.status_code == 200
    assert r.json()["userVerification"] == "required"


# ── Session management ───────────────────────────────────────────────────────
def test_sessions_list_flags_current(auth_client, req):
    auth.create_session(req(ip="2.2.2.2"))
    auth.create_session(req(ip="3.3.3.3"))
    rows = auth_client.get("/auth/sessions").json()
    assert len(rows) == 3
    assert sum(1 for s in rows if s["current"]) == 1


def test_revoke_others_keeps_current(auth_client, req):
    auth.create_session(req(ip="2.2.2.2"))
    auth.create_session(req(ip="3.3.3.3"))
    r = auth_client.post("/auth/sessions/revoke-others")
    assert r.json()["revoked"] == 2
    rows = auth_client.get("/auth/sessions").json()
    assert len(rows) == 1 and rows[0]["current"] is True
    # current session still works
    assert auth_client.get("/overview/").status_code == 200


def test_revoke_specific_session(auth_client, req):
    auth.create_session(req(ip="2.2.2.2"))
    rows = auth_client.get("/auth/sessions").json()
    other = next(s for s in rows if not s["current"])
    assert auth_client.request("DELETE", f"/auth/sessions/{other['id']}").status_code == 200
    assert auth_client.request("DELETE", "/auth/sessions/99999").status_code == 404
