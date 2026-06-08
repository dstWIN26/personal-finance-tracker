"""
One-time Revolut authorization via Salt Edge (Account Information API v6).

    python -m backend.integrations.revolut_setup

Flow:
  1. Creates (or reuses) a Salt Edge customer.
  2. Opens a "connect session" and prints a URL — open it, pick Revolut, log in,
     and grant read-only consent.
  3. After you finish, press Enter; the script finds your connection + account IDs
     and prints the env vars to add to .env.

Requires SALTEDGE_APP_ID and SALTEDGE_SECRET in .env (get them free at
https://www.saltedge.com/clients/profile/secrets).
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

SALTEDGE_BASE = "https://www.saltedge.com/api/v6"

def _headers() -> dict:
    return {
        "App-id": os.environ["SALTEDGE_APP_ID"],
        "Secret": os.environ["SALTEDGE_SECRET"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

async def setup():
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Create (or reuse) a customer
        customer_id = os.getenv("SALTEDGE_CUSTOMER_ID", "")
        if not customer_id:
            r = await client.post(
                f"{SALTEDGE_BASE}/customers",
                headers=_headers(),
                json={"data": {"identifier": "personal-finance-tracker"}},
            )
            if r.status_code == 409:
                # already exists — fetch it
                r = await client.get(f"{SALTEDGE_BASE}/customers", headers=_headers())
                r.raise_for_status()
                customer_id = r.json()["data"][0]["id"]
            else:
                r.raise_for_status()
                customer_id = r.json()["data"]["id"]
            print(f"Customer ID: {customer_id}")

        # 2. Create a connect session
        r = await client.post(
            f"{SALTEDGE_BASE}/connections/connect",
            headers=_headers(),
            json={"data": {
                "customer_id": customer_id,
                "consent": {"scopes": ["account_details", "transactions_details"]},
                "attempt": {"return_to": "http://localhost:8000"},
            }},
        )
        r.raise_for_status()
        connect_url = r.json()["data"]["connect_url"]

        print(f"\n>>> Open this URL, choose Revolut, log in, and grant consent:\n{connect_url}\n")
        input("Press Enter after you have finished authorizing in your browser...")

        # 3. Find the new connection
        r = await client.get(
            f"{SALTEDGE_BASE}/connections",
            headers=_headers(),
            params={"customer_id": customer_id},
        )
        r.raise_for_status()
        connections = r.json().get("data", [])
        if not connections:
            print("No connection found. Make sure you completed the authorization.")
            return
        connection_id = connections[-1]["id"]

        # Show accounts for confirmation
        r = await client.get(
            f"{SALTEDGE_BASE}/accounts",
            headers=_headers(),
            params={"connection_id": connection_id},
        )
        r.raise_for_status()
        accounts = r.json().get("data", [])

        print(f"\n✅ Connected! Found {len(accounts)} account(s):")
        for a in accounts:
            print(f"   - {a.get('name', a['id'])} ({a.get('currency_code', '?')})")

        print(f"\nAdd these to your .env file:")
        print(f"SALTEDGE_CUSTOMER_ID={customer_id}")
        print(f"SALTEDGE_CONNECTION_ID={connection_id}")

if __name__ == "__main__":
    asyncio.run(setup())
