import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Nothing blocks boot anymore: login is configured via `auth_setup.py`, and the
# bank/broker integrations are optional so the app can be deployed first and
# linked to Trade Republic / Revolut afterwards.
REQUIRED: list[str] = []

# Warned-about-if-missing — the dashboard runs, but these integrations stay idle
# until configured (Revolut via Salt Edge, e-mail alerts).
RECOMMENDED = [
    "SALTEDGE_APP_ID",
    "SALTEDGE_SECRET",
    "EMAIL_FROM",
    "EMAIL_TO",
    "EMAIL_SMTP_PASSWORD",
]
# SALTEDGE_CUSTOMER_ID and SALTEDGE_CONNECTION_ID are filled in after running
# revolut_setup.py, so they are intentionally not in REQUIRED.

# Trade Republic: only the phone number is required at runtime.
# The PIN is NOT stored — after one-time web-login pairing, syncs resume the saved
# web session at keys/tr_cookies.txt. TRADE_REPUBLIC_PIN is optional and only
# consumed during initial pairing (tr_setup.py).
TR_REQUIRED = ["TRADE_REPUBLIC_PHONE"]

def validate():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")
    absent = [k for k in RECOMMENDED if not os.getenv(k)]
    if absent:
        logging.getLogger(__name__).warning(
            "Optional integrations not configured (dashboard still runs): %s", absent
        )
    if RP_ID == "localhost" and COOKIE_SECURE is False:
        logging.getLogger(__name__).warning(
            "RP_ID is 'localhost' — set RP_ID/RP_ORIGIN to your real domain in production "
            "(passkeys and Secure cookies require https)."
        )


# ── WebAuthn / session auth ──────────────────────────────────────────────────
# RP_ID is the registrable domain the passkey is bound to (no scheme/port).
# RP_ORIGIN is the full origin the browser sends. In production these MUST be
# your real domain (e.g. RP_ID=finance.example.com,
# RP_ORIGIN=https://finance.example.com). localhost defaults let dev work over http.
RP_ID = os.getenv("RP_ID", "localhost")
RP_NAME = os.getenv("RP_NAME", "Finance Tracker")
RP_ORIGIN = os.getenv("RP_ORIGIN", "http://localhost:8000")
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))

# Secure/__Host- cookies only on https, so local http dev still works.
COOKIE_SECURE = RP_ORIGIN.lower().startswith("https://")
SESSION_COOKIE = "__Host-fts_session" if COOKIE_SECURE else "fts_session"
CHALLENGE_COOKIE = "__Host-fts_chal" if COOKIE_SECURE else "fts_chal"

# Brute-force lockout for the bootstrap password.
MAX_PW_FAILURES = int(os.getenv("MAX_PW_FAILURES", "5"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
