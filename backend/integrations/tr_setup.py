"""
One-time Trade Republic web-login pairing (pytr 0.4.9).

    python -m backend.integrations.tr_setup

RUN THIS ON A MACHINE WITH A BROWSER (e.g. your Mac), NOT the headless server.
Trade Republic hard-blocks the pure-Python anti-bot solver at its edge (405), so
pytr mints the AWS WAF token with a REAL headless browser (Playwright). On first
run pytr auto-downloads Chromium (~160 MB) — that's normal. The flow:

  1. Playwright loads TR's login page and obtains a WAF token TR accepts.
  2. Sends your phone + PIN; TR pushes a 4-digit code to your TR app.
  3. You enter the code; the authenticated web session (cookies) is saved to
     keys/tr_cookies.txt. Your PIN is NOT stored.

Then put the session on the server (the server needs NO browser — resuming the
session + the websocket data feed use the saved cookies alone):

    scp keys/tr_cookies.txt  you@your-server:~/personal-finance-tracker/keys/

After pairing:
  - Remove TRADE_REPUBLIC_PIN from .env entirely (no longer needed).
  - Protect the cookies file (it IS the session): gitignored under keys/, chmod 600.
  - Web sessions expire after a while — if syncs start logging "session expired",
    re-run this on your Mac and copy keys/tr_cookies.txt over again.
"""
import os
import socket
import getpass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# pytr's HTTP calls have no timeout, so a stalled route would hang forever with no
# feedback. A global socket timeout turns that into a fast, clear error instead.
socket.setdefaulttimeout(60)


def _explain(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    hint = ""
    if "CLIENT_VERSION_OUTDATED" in msg:
        hint = "   TR rejected the client version — pytr may need upgrading again.\n"
    elif "executable doesn't exist" in low or "playwright install" in low or "browsertype.launch" in low:
        hint = ("   Playwright's browser isn't installed. Install it once, then retry:\n"
                "     playwright install chromium\n")
    elif "405" in msg or "403" in msg or "waf" in low or "challenge" in low or "forbidden" in low:
        hint = ("   TR's edge blocked the login. This flow needs a REAL browser (Playwright)\n"
                "   on a normal residential network — run it on your Mac, NOT the headless\n"
                "   datacenter server. The server only needs the resulting cookies file.\n")
    elif isinstance(exc, (TimeoutError, OSError)):
        hint = "   Network couldn't reach api.traderepublic.com — check your connection and retry.\n"
    return f"\n❌ Trade Republic login failed: {type(exc).__name__}: {msg}\n{hint}"


def main():
    # Import here so the module loads even before deps are installed.
    from backend.integrations.trade_republic import _build_api, COOKIES_FILE

    if not os.getenv("TRADE_REPUBLIC_PHONE"):
        os.environ["TRADE_REPUBLIC_PHONE"] = input("Trade Republic phone (+49...): ").strip()
    # PIN is requested interactively and NOT persisted by this script.
    if not os.getenv("TRADE_REPUBLIC_PIN"):
        os.environ["TRADE_REPUBLIC_PIN"] = getpass.getpass("Trade Republic PIN (not stored): ").strip()

    Path(os.path.dirname(COOKIES_FILE) or ".").mkdir(parents=True, exist_ok=True)

    api = _build_api()

    print("\nSolving Trade Republic's anti-bot challenge and requesting a login code...")
    try:
        countdown = api.initiate_weblogin()          # WAF token + web login → code sent
    except Exception as exc:                         # noqa: BLE001
        raise SystemExit(_explain(exc))

    print(f"A 4-digit code was sent to your Trade Republic app (valid ~{countdown}s).")
    code = input("Enter the 4-digit code: ").strip()
    api.complete_weblogin(code)                      # verifies code + saves cookies

    # Lock down the cookies file (owner read/write only) — it is the live session.
    try:
        os.chmod(COOKIES_FILE, 0o600)
    except OSError:
        pass

    print(f"\n✅ Paired. Web session saved to {COOKIES_FILE} (chmod 600).")
    print("You can now REMOVE TRADE_REPUBLIC_PIN from your .env — it is no longer needed.")


if __name__ == "__main__":
    main()
