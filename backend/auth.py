"""Single-user authentication core.

Model (chosen by the owner): **passkey-only after setup**.
  1. A one-time bootstrap password (argon2id) is set via `auth_setup.py`.
  2. The owner logs in once with it and enrols a WebAuthn passkey (Touch ID).
  3. On first passkey enrolment the password is permanently disabled — from then
     on only the hardware passkey (with user verification / biometrics) can log in.

Sessions are server-side: the cookie carries a random 256-bit token, the DB only
stores its SHA-256. The session id is **rotated on every successful login**
(fixation defence) and destroyed on logout. Cookies are HttpOnly + SameSite=Strict
and, on https, Secure with the `__Host-` prefix.

Break-glass recovery (documented): re-run `python -m backend.auth_setup` on the
server to re-enable the password — this requires shell access to the host.
"""
import os
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request, HTTPException, status
from fastapi.responses import Response
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from starlette.middleware.base import BaseHTTPMiddleware

from backend.database import connect
from backend import config

logger = logging.getLogger(__name__)

# OWASP-aligned argon2id parameters (sensible 2024+ defaults from the library).
_ph = PasswordHasher()

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Bootstrap password ────────────────────────────────────────────────────────
def set_password(password: str) -> None:
    """Create/replace the bootstrap password and (re)enable password login."""
    h = _ph.hash(password)
    with connect() as conn:
        conn.execute(
            "INSERT INTO auth_state (id, password_hash, password_enabled) VALUES (1, ?, 1) "
            "ON CONFLICT(id) DO UPDATE SET password_hash = excluded.password_hash, "
            "password_enabled = 1",
            (h,),
        )


def password_enabled() -> bool:
    with connect() as conn:
        row = conn.execute("SELECT password_enabled FROM auth_state WHERE id = 1").fetchone()
    return bool(row and row["password_enabled"])


def disable_password() -> None:
    with connect() as conn:
        conn.execute("UPDATE auth_state SET password_enabled = 0, password_hash = NULL WHERE id = 1")


def verify_password(password: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT password_hash, password_enabled FROM auth_state WHERE id = 1"
        ).fetchone()
    if not row or not row["password_enabled"] or not row["password_hash"]:
        return False
    try:
        _ph.verify(row["password_hash"], password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


# ── Lockout (password brute-force) ────────────────────────────────────────────
def record_attempt(ip: str, ok: bool) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO login_attempts (ip, ok) VALUES (?, ?)", (ip, 1 if ok else 0))


def is_locked_out(ip: str) -> bool:
    cutoff = (_now() - timedelta(minutes=config.LOCKOUT_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE ip = ? AND ok = 0 AND ts >= ?",
            (ip, cutoff),
        ).fetchone()["n"]
    return n >= config.MAX_PW_FAILURES


# ── Challenges (WebAuthn) ─────────────────────────────────────────────────────
def store_challenge(kind: str, challenge_b64: str) -> str:
    cid = secrets.token_urlsafe(24)
    exp = (_now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        conn.execute("DELETE FROM auth_challenges WHERE expires_at < ?",
                     (_now().strftime("%Y-%m-%d %H:%M:%S"),))
        conn.execute(
            "INSERT INTO auth_challenges (cid, kind, challenge, expires_at) VALUES (?, ?, ?, ?)",
            (cid, kind, challenge_b64, exp),
        )
    return cid


def consume_challenge(cid: str, kind: str) -> str | None:
    """Return the challenge for `cid`/`kind` exactly once, then delete it."""
    if not cid:
        return None
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        row = conn.execute(
            "SELECT challenge FROM auth_challenges WHERE cid = ? AND kind = ? AND expires_at >= ?",
            (cid, kind, now),
        ).fetchone()
        conn.execute("DELETE FROM auth_challenges WHERE cid = ?", (cid,))
    return row["challenge"] if row else None


# ── Sessions ──────────────────────────────────────────────────────────────────
def create_session(request: Request, old_token: str | None = None) -> str:
    """Issue a fresh session token, rotating away any previous one."""
    if old_token:
        destroy_session(old_token)
    token = secrets.token_urlsafe(32)
    exp = (_now() + timedelta(hours=config.SESSION_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    ua = request.headers.get("user-agent", "")[:255]
    ip = _client_ip(request)
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, expires_at, user_agent, ip) VALUES (?, ?, ?, ?)",
            (_sha256(token), exp, ua, ip),
        )
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))


def _valid_session(token: str | None) -> bool:
    if not token:
        return False
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE token_hash = ? AND expires_at >= ?",
            (_sha256(token), now),
        ).fetchone()
        if not row:
            return False
        # Sliding expiry: extend on activity.
        new_exp = (_now() + timedelta(hours=config.SESSION_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE sessions SET last_seen = CURRENT_TIMESTAMP, expires_at = ? WHERE id = ?",
                     (new_exp, row["id"]))
    return True


def _client_ip(request: Request) -> str:
    # Behind Caddy, the real client IP is in X-Forwarded-For (first hop).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


# ── Cookie helpers ────────────────────────────────────────────────────────────
def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True, secure=config.COOKIE_SECURE, samesite="strict", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(config.SESSION_COOKIE, path="/")


def set_challenge_cookie(response: Response, cid: str) -> None:
    response.set_cookie(
        config.CHALLENGE_COOKIE, cid,
        max_age=300, httponly=True, secure=config.COOKIE_SECURE, samesite="strict", path="/",
    )


# ── FastAPI dependencies ──────────────────────────────────────────────────────
def _check_origin(request: Request) -> None:
    """CSRF defence for state-changing requests: Origin must match RP_ORIGIN."""
    if request.method in _SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != config.RP_ORIGIN.rstrip("/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bad origin")


def is_authenticated(request: Request) -> bool:
    return _valid_session(request.cookies.get(config.SESSION_COOKIE))


def require_session(request: Request) -> None:
    """Gate dependency for all protected routers/endpoints."""
    _check_origin(request)
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )


# ── Security headers ──────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds hardening headers to every response.

    CSP permits the app's CDN dependencies (Chart.js/Alpine via jsDelivr, Google
    Fonts). Alpine evaluates expressions at runtime, so 'unsafe-eval' is required
    for script-src; this is an accepted trade-off for a single-user app with no
    third-party/user-generated content. All other vectors stay locked down.
    """
    CSP = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net 'unsafe-eval'; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self.CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if config.COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        return response
