"""Refresh-spam guard: PageGuard state machine + HTTP enforcement."""
from backend import ratelimit
from backend.ratelimit import PageGuard, OK, THROTTLE, LOCKOUT


class FakeClock:
    """Manually advanced monotonic clock for deterministic window tests."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def _guard(clock):
    # Spec: 5 loads / 5s OK; 6th → 3s buffer; >10 in window → 120s lockout.
    return PageGuard(window=5.0, burst=5, throttle_seconds=3.0,
                     lockout_at=10, lockout_seconds=120.0, clock=clock)


# ── State machine ─────────────────────────────────────────────────────────────
def test_burst_is_served_immediately():
    g = _guard(FakeClock())
    for _ in range(5):
        assert g.check("ip") == (OK, 0.0)


def test_sixth_load_in_window_is_throttled():
    g = _guard(FakeClock())
    for _ in range(5):
        g.check("ip")
    action, secs = g.check("ip")                # the 6th within 5s
    assert action == THROTTLE
    assert secs == 3.0


def test_sustained_spam_trips_lockout():
    g = _guard(FakeClock())
    actions = [g.check("ip")[0] for _ in range(11)]   # 11th in-window load
    assert actions[:5] == [OK] * 5
    assert THROTTLE in actions[5:10]
    assert actions[-1] == LOCKOUT
    # Once locked, every further load reports the remaining lockout time.
    action, secs = g.check("ip")
    assert action == LOCKOUT
    assert 0 < secs <= 120.0


def test_window_slides_so_slow_refreshes_never_throttle():
    clock = FakeClock()
    g = _guard(clock)
    for _ in range(20):                          # one load every 2s, forever
        assert g.check("ip") == (OK, 0.0)
        clock.advance(2.0)                       # > window/burst → always drains


def test_lockout_expires_after_its_window():
    clock = FakeClock()
    g = _guard(clock)
    for _ in range(11):
        g.check("ip")
    assert g.locked_remaining("ip") > 0
    clock.advance(120.1)
    assert g.locked_remaining("ip") == 0.0
    assert g.check("ip") == (OK, 0.0)            # fresh start after the timeout


def test_keys_are_independent():
    g = _guard(FakeClock())
    for _ in range(11):
        g.check("masher")
    assert g.locked_remaining("masher") > 0
    assert g.locked_remaining("bystander") == 0.0
    assert g.check("bystander") == (OK, 0.0)


def test_reset_clears_state():
    g = _guard(FakeClock())
    for _ in range(11):
        g.check("ip")
    g.reset("ip")
    assert g.locked_remaining("ip") == 0.0
    assert g.check("ip") == (OK, 0.0)


# ── HTTP enforcement ──────────────────────────────────────────────────────────
def test_refresh_lockout_redirects_to_login_and_kills_session(auth_client, monkeypatch):
    # Force an immediate lockout on the next page load, then refresh.
    monkeypatch.setattr(ratelimit, "page_guard",
                        PageGuard(burst=0, lockout_at=0, lockout_seconds=120))
    r = auth_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?locked=refresh"
    # Session was destroyed → the cookie no longer grants access.
    assert auth_client.get("/overview/").status_code == 401


def test_locked_ip_cannot_log_back_in(client, password, monkeypatch):
    # Simulate an active refresh lockout for this client, then try to sign in.
    monkeypatch.setattr(ratelimit, "page_guard",
                        PageGuard(lockout_seconds=120))
    ratelimit.page_guard._locked_until[_test_ip()] = \
        ratelimit.page_guard.clock() + 120
    r = client.post("/auth/password", json={"password": password})
    assert r.status_code == 429
    assert "refresh" in r.json()["detail"].lower()


def test_throttle_still_serves_the_page(auth_client, monkeypatch):
    # burst 0 + zero-delay buffer → THROTTLE path returns the page without waiting.
    monkeypatch.setattr(ratelimit, "page_guard",
                        PageGuard(burst=0, throttle_seconds=0.0, lockout_at=99))
    assert auth_client.get("/", follow_redirects=False).status_code == 200


def _test_ip():
    # The TestClient presents this socket peer; _client_ip falls back to it.
    return "testclient"
