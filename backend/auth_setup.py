"""One-time / break-glass bootstrap-password setup.

    python -m backend.auth_setup

Sets (or resets) the bootstrap password and re-enables password login. Normal
use: run once before first launch, log in at /login, enrol your passkey — after
which the password is disabled automatically. Re-run on the server to recover if
you ever lose every passkey (requires shell access to the host).
"""
import sys
import getpass

from backend.database import init_db
from backend import auth

MIN_LEN = 12


def main() -> None:
    init_db()
    print("── Finance Tracker · bootstrap password ──")
    print("Used ONCE to log in and enrol your passkey; then password login is disabled.\n")
    pw1 = getpass.getpass(f"New password (min {MIN_LEN} chars): ")
    if len(pw1) < MIN_LEN:
        sys.exit(f"✗ Password must be at least {MIN_LEN} characters.")
    if pw1 != getpass.getpass("Confirm password: "):
        sys.exit("✗ Passwords do not match.")
    auth.set_password(pw1)
    print("\n✓ Bootstrap password set and password login enabled.")
    print("  Start the app, open /login, sign in, then enrol your passkey.")


if __name__ == "__main__":
    main()
