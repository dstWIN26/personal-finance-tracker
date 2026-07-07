import os
import ipaddress
import logging
from dotenv import load_dotenv

load_dotenv()

# Nothing blocks boot anymore: login is configured via `auth_setup.py`, and the
# bank/broker integrations are optional so the app can be deployed first and
# linked afterwards. Trade Republic is CSV-import-only (no automated API).
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

# ── Trusting proxy headers for lockout keying ────────────────────────────────
# The password lockout is keyed on the client IP. CF-Connecting-IP / XFF are
# client-supplied and spoofable if the origin is reachable directly, so they are
# only trusted when we know the request truly came through Cloudflare:
#   false (default) — ignore forwarded headers; key on the real TCP peer. A
#                     direct-to-origin attacker rotating CF-Connecting-IP cannot
#                     split attempts across keys, so the lockout still trips.
#   true            — trust CF-Connecting-IP. Safe ONLY once the origin is locked
#                     to Cloudflare:  sudo ./scripts/cloudflare-firewall.sh
#   auto            — trust CF-Connecting-IP only when the TCP peer is a published
#                     Cloudflare address (uvicorn exposed to Cloudflare directly).
def _norm_trust(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return "true"
    return "auto" if v == "auto" else "false"

TRUST_CF_HEADERS = _norm_trust(os.getenv("TRUST_CF_HEADERS", "false"))

# Cloudflare edge ranges (https://www.cloudflare.com/ips/); used only by "auto".
# Static copy — these change rarely; the firewall script fetches the live list.
_CLOUDFLARE_CIDRS = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
]
CLOUDFLARE_NETS = [ipaddress.ip_network(c) for c in _CLOUDFLARE_CIDRS]
