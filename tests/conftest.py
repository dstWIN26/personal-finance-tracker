"""Shared pytest fixtures.

Each test gets a fresh temp SQLite DB and a FastAPI app wired like main.py — but
without the scheduler / pytr / Salt Edge imports, so the suite runs offline and
fast. SMTP is stubbed so security alerts are captured, never actually sent.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point the DB at a per-test temp file and initialise the schema."""
    import backend.database as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def captured_alerts(monkeypatch):
    """Stub the SMTP send + run the dispatch synchronously, so security alerts are
    recorded (and asserted) instead of e-mailed."""
    calls = []
    monkeypatch.setattr(
        "backend.alerts.send_security_alert",
        lambda event, when, ip, ua, detail="", warn=False: calls.append(
            {"event": event, "warn": warn, "ip": ip}
        ),
    )
    monkeypatch.setattr("backend.auth._spawn", lambda target, args: target(*args))
    return calls


@pytest.fixture(autouse=True)
def reset_page_guard():
    """The refresh-spam guard is a module singleton — clear it around each test."""
    from backend import ratelimit
    ratelimit.page_guard.reset()
    yield
    ratelimit.page_guard.reset()


@pytest.fixture
def app():
    from backend import auth
    from backend.routes import (
        overview, spending, portfolio, limits, market, settings, auth as auth_routes,
    )
    application = FastAPI()
    application.add_middleware(auth.SecurityHeadersMiddleware)
    guard = [Depends(auth.require_session)]
    for r in (overview.router, spending.router, portfolio.router, limits.router,
              market.router, settings.router):
        application.include_router(r, dependencies=guard)
    application.include_router(auth_routes.router)

    @application.get("/")
    async def index(request: Request):
        # Mirrors backend.main.index so the refresh-spam guard is exercised here.
        import asyncio
        from backend import ratelimit, config
        if not auth.is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        action, secs = ratelimit.page_guard.check(auth._client_ip(request))
        if action == ratelimit.LOCKOUT:
            auth.destroy_session(request.cookies.get(config.SESSION_COOKIE))
            resp = RedirectResponse("/login?locked=refresh", status_code=303)
            auth.clear_session_cookie(resp)
            return resp
        if action == ratelimit.THROTTLE:
            await asyncio.sleep(secs)
        return {"ok": True}

    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def password():
    """Set a bootstrap password and return it."""
    from backend import auth
    pw = "correct horse battery staple 99"
    auth.set_password(pw)
    return pw


@pytest.fixture
def auth_client(client, password):
    """A TestClient already logged in via the bootstrap password."""
    assert client.post("/auth/password", json={"password": password}).status_code == 200
    return client


@pytest.fixture
def req():
    """Factory for a minimal Request-like object for unit-testing auth helpers."""
    def _make(cookies=None, method="GET", ip="1.2.3.4", ua="pytest"):
        return SimpleNamespace(
            headers={"user-agent": ua}, client=SimpleNamespace(host=ip),
            cookies=cookies or {}, method=method,
        )
    return _make
