"""
One-time Trade Republic device pairing.

    python -m backend.integrations.tr_setup

This pairs THIS device with your Trade Republic account using your phone + PIN + the
4-digit OTP sent to your TR app. It writes a private keyfile to keys/tr_keyfile.pem.

After pairing:
  - All future syncs authenticate with the keyfile — NOT your PIN.
  - You can (and should) remove TRADE_REPUBLIC_PIN from your .env entirely.
  - Protect the keyfile: it is gitignored, and you should `chmod 600 keys/tr_keyfile.pem`.
  - To revoke access, reset paired devices in the Trade Republic app.
"""
import os
import socket
import getpass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KEYFILE = os.getenv("TRADE_REPUBLIC_KEYFILE", "keys/tr_keyfile.pem")

# pytr's reset calls hit api.traderepublic.com with `requests` and NO timeout, so a
# stalled route (e.g. a container with broken outbound egress) makes them hang
# forever with zero feedback. A global socket timeout turns that silent hang into a
# fast, clear error instead. Applies to every socket opened by this process.
socket.setdefaulttimeout(45)


def main():
    from pytr.api import TradeRepublicApi

    phone = os.getenv("TRADE_REPUBLIC_PHONE") or input("Trade Republic phone (+49...): ").strip()
    # PIN is requested interactively and NOT persisted by this script.
    pin = os.getenv("TRADE_REPUBLIC_PIN") or getpass.getpass("Trade Republic PIN (not stored): ").strip()

    Path(os.path.dirname(KEYFILE) or ".").mkdir(parents=True, exist_ok=True)

    api = TradeRepublicApi(phone_no=phone, pin=pin, keyfile=KEYFILE)

    print("\nRequesting device pairing — a 4-digit code will be sent to your TR app...")
    try:
        api.initiate_device_reset()
    except (TimeoutError, OSError) as exc:                 # socket.timeout subclasses these
        raise SystemExit(
            "\n❌ Could not reach Trade Republic (https://api.traderepublic.com).\n"
            f"   {type(exc).__name__}: {exc}\n"
            "   The container has no working outbound network. Pair on the host network:\n\n"
            '     docker run --rm -it --network host --env-file .env \\\n'
            '       -v "$PWD/keys:/app/keys" personal-finance-tracker-app \\\n'
            "       python -m backend.integrations.tr_setup\n"
        )

    token = input("Enter the 4-digit code from your Trade Republic app: ").strip()
    api.complete_device_reset(token)

    # Lock down the keyfile permissions (owner read/write only).
    try:
        os.chmod(KEYFILE, 0o600)
    except OSError:
        pass

    print(f"\n✅ Paired. Keyfile written to {KEYFILE} (chmod 600).")
    print("You can now REMOVE TRADE_REPUBLIC_PIN from your .env — it is no longer needed.")


if __name__ == "__main__":
    main()
