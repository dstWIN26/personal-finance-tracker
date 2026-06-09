"""Authentication endpoints (public router — gating is per-endpoint).

Flow:
  • POST /auth/password                 — one-time bootstrap login (until disabled)
  • POST /auth/passkey/login/begin      — usernameless passkey challenge
  • POST /auth/passkey/login/complete   — verify assertion → session
  • POST /auth/passkey/register/begin   — (session required) enrolment challenge
  • POST /auth/passkey/register/complete — store passkey, disable password
  • POST /auth/logout                   — destroy session
  • GET/DELETE /auth/credentials        — manage enrolled passkeys
  • GET  /auth/status                   — drive the login UI
"""
import logging

import webauthn
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
)
from fastapi import APIRouter, Request, Response, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import auth, config
from backend.database import connect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Single-user identity (stable across enrolments so passkeys group under one user).
USER_ID = b"finance-tracker-owner"
USER_NAME = "owner"


def _allow_credentials() -> list[PublicKeyCredentialDescriptor]:
    with connect() as conn:
        rows = conn.execute("SELECT credential_id FROM webauthn_credentials").fetchall()
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["credential_id"])) for r in rows]


@router.get("/status")
def auth_status(request: Request):
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM webauthn_credentials").fetchone()["n"]
    return {
        "authenticated": auth.is_authenticated(request),
        "password_enabled": auth.password_enabled(),
        "has_passkey": n > 0,
    }


# ── Password (bootstrap) ──────────────────────────────────────────────────────
class PwBody(BaseModel):
    password: str


@router.post("/password")
def password_login(request: Request, body: PwBody):
    auth._check_origin(request)
    ip = auth._client_ip(request)
    if auth.is_locked_out(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many failed attempts. Try again later.")
    if not auth.password_enabled():
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Password login is disabled — use your passkey.")
    if not auth.verify_password(body.password):
        auth.record_attempt(ip, False)
        # Alert exactly once, on the failure that trips the lockout.
        if auth.is_locked_out(ip):
            auth.notify("Account locked after failed sign-ins", request,
                        detail=f"{config.MAX_PW_FAILURES} failed bootstrap-password attempts",
                        warn=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password.")
    auth.record_attempt(ip, True)
    token = auth.create_session(request, old_token=request.cookies.get(config.SESSION_COOKIE))
    auth.notify("New sign-in (bootstrap password)", request,
                detail="One-time password login — enrol a passkey to finish setup")
    resp = JSONResponse({"ok": True, "next": "enroll"})
    auth.set_session_cookie(resp, token)
    return resp


# ── Passkey login ─────────────────────────────────────────────────────────────
@router.post("/passkey/login/begin")
def passkey_login_begin():
    opts = webauthn.generate_authentication_options(
        rp_id=config.RP_ID,
        allow_credentials=_allow_credentials() or None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    cid = auth.store_challenge("authenticate", bytes_to_base64url(opts.challenge))
    resp = Response(content=webauthn.options_to_json(opts), media_type="application/json")
    auth.set_challenge_cookie(resp, cid)
    return resp


@router.post("/passkey/login/complete")
async def passkey_login_complete(request: Request):
    challenge = auth.consume_challenge(request.cookies.get(config.CHALLENGE_COOKIE), "authenticate")
    if not challenge:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Challenge expired — please retry.")
    body = await request.json()
    cred_id = body.get("id") or body.get("rawId")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE credential_id = ?", (cred_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unrecognised passkey.")
    try:
        verification = webauthn.verify_authentication_response(
            credential=body,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=config.RP_ID,
            expected_origin=config.RP_ORIGIN,
            credential_public_key=base64url_to_bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"],
            require_user_verification=True,
        )
    except Exception as e:                                          # noqa: BLE001
        logger.warning("passkey auth failed: %s", e)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Passkey verification failed.")
    with connect() as conn:
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count = ?, last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (verification.new_sign_count, row["id"]),
        )
    token = auth.create_session(request, old_token=request.cookies.get(config.SESSION_COOKIE))
    auth.notify("New sign-in (passkey)", request, detail=f"Passkey: {row['label'] or 'unnamed'}")
    resp = JSONResponse({"ok": True})
    auth.set_session_cookie(resp, token)
    resp.delete_cookie(config.CHALLENGE_COOKIE, path="/")
    return resp


# ── Passkey enrolment (session required) ──────────────────────────────────────
@router.post("/passkey/register/begin")
def passkey_register_begin(_: None = Depends(auth.require_session)):
    opts = webauthn.generate_registration_options(
        rp_id=config.RP_ID,
        rp_name=config.RP_NAME,
        user_id=USER_ID,
        user_name=USER_NAME,
        user_display_name="Owner",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=_allow_credentials() or None,
    )
    cid = auth.store_challenge("register", bytes_to_base64url(opts.challenge))
    resp = Response(content=webauthn.options_to_json(opts), media_type="application/json")
    auth.set_challenge_cookie(resp, cid)
    return resp


@router.post("/passkey/register/complete")
async def passkey_register_complete(request: Request, _: None = Depends(auth.require_session)):
    challenge = auth.consume_challenge(request.cookies.get(config.CHALLENGE_COOKIE), "register")
    if not challenge:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Challenge expired — please retry.")
    payload = await request.json()
    credential = payload.get("credential", payload)
    label = (payload.get("label") or "Passkey")[:64]
    try:
        reg = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=config.RP_ID,
            expected_origin=config.RP_ORIGIN,
            require_user_verification=True,
        )
    except Exception as e:                                          # noqa: BLE001
        logger.warning("passkey registration failed: %s", e)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration verification failed.")
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO webauthn_credentials (credential_id, public_key, sign_count, label) "
            "VALUES (?, ?, ?, ?)",
            (bytes_to_base64url(reg.credential_id), bytes_to_base64url(reg.credential_public_key),
             reg.sign_count, label),
        )
    # Passkey-only model: now that a hardware passkey exists, kill the password.
    auth.disable_password()
    auth.notify("New passkey enrolled", request, detail=f"Label: {label}", warn=True)
    resp = JSONResponse({"ok": True, "password_disabled": True})
    resp.delete_cookie(config.CHALLENGE_COOKIE, path="/")
    return resp


# ── Session + credential management ───────────────────────────────────────────
@router.post("/logout")
def logout(request: Request):
    auth._check_origin(request)
    auth.destroy_session(request.cookies.get(config.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    auth.clear_session_cookie(resp)
    return resp


@router.get("/credentials")
def list_credentials(_: None = Depends(auth.require_session)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, label, created_at, last_used_at FROM webauthn_credentials ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/credentials/{cred_id}")
def delete_credential(cred_id: int, request: Request, _: None = Depends(auth.require_session)):
    auth._check_origin(request)
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM webauthn_credentials").fetchone()["n"]
        # Never strand the owner: refuse to remove the last passkey while password is off.
        if total <= 1 and not auth.password_enabled():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot remove your only passkey while password login is disabled.",
            )
        conn.execute("DELETE FROM webauthn_credentials WHERE id = ?", (cred_id,))
    return {"ok": True}
