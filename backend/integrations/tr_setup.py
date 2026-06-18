"""
One-time Trade Republic web-login pairing (pytr 0.4.9).

    python -m backend.integrations.tr_setup

Trade Republic retired the old device-keyfile login (it now rejects it with
CLIENT_VERSION_OUTDATED). This logs in via TR's *web* flow instead:

  1. Solves TR's AWS WAF anti-bot challenge in pure Python (no browser).
  2. Sends your phone + PIN, TR pushes a 4-digit code to your TR app.
  3. You enter the code; the authenticated web session (cookies) is saved to
     keys/tr_cookies.txt. Ongoing syncs resume that session — your PIN is NOT stored.

After pairing:
  - Remove TRADE_REPUBLIC_PIN from .env entirely (no longer needed).
  - Protect the cookies file (it IS the session): gitignored under keys/, chmod 600.
  - Web sessions expire after a while — if syncs start logging "session expired",
    just run this command again to re-pair.

If the container can't reach TR, run it on the host network:
    docker run --rm -it --network host --env-file .env \\
      -v "$PWD/keys:/app/keys" personal-finance-tracker-app \\
      python -m backend.integrations.tr_setup
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
    hint = ""
    if "CLIENT_VERSION_OUTDATED" in msg:
        hint = "   TR rejected the client version — pytr may need upgrading again.\n"
    elif "WAF" in msg or "challenge" in msg or "None" in msg:
        hint = ("   Couldn't solve TR's anti-bot challenge from this network. Retry; if it\n"
                "   persists from the server, TR may be blocking the datacenter IP.\n")
    elif isinstance(exc, (TimeoutError, OSError)):
        hint = ("   Network couldn't reach api.traderepublic.com. Run on the host network:\n"
                '     docker run --rm -it --network host --env-file .env \\\n'
                '       -v "$PWD/keys:/app/keys" personal-finance-tracker-app \\\n'
                "       python -m backend.integrations.tr_setup\n")
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
