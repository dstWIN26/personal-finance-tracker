# Agent: INTEGRATIONS

## Mission
Implement the Trade Republic and Revolut data-fetching clients. This agent runs second (after ARCHITECT) because all other agents depend on having real data flowing into SQLite.

---

## Trade Republic Integration

### What's available
Trade Republic has **no official public API**. The community has reverse-engineered their WebSocket protocol. The best library is `pytr` (Python Trade Republic).

- **Repo:** https://github.com/marzzzello/pytr
- **Install:** `pip install pytr`
- **Requires:** Your TR phone number + PIN (the same ones you use in the app)
- **Auth:** TR sends a 4-digit OTP to your phone on first run — you enter it once, and the session is saved locally

### Setup Steps
```bash
# First-time authentication (run once, saves session)
pytr login +49XXXXXXXXXX 1234
# Enter the 4-digit OTP from your TR app when prompted
```

### What to fetch
```python
# backend/integrations/trade_republic.py

import asyncio
from pytr.api import TradeRepublicApi
from backend.database import upsert_position, insert_transaction
import os

async def sync_portfolio():
    """Fetch all positions and update the database."""
    api = TradeRepublicApi(
        phone_no=os.environ["TRADE_REPUBLIC_PHONE"],
        pin=os.environ["TRADE_REPUBLIC_PIN"],
        locale="en"
    )
    await api.login()

    portfolio = await api.portfolio()
    for item in portfolio["positions"]:
        upsert_position(
            isin=item["instrumentId"],
            name=item["name"],
            quantity=item["quantity"],
            buy_price=item["averageBuyIn"],
            current_price=item["currentPrice"],
            pl_pct=item["unrealizedPnlPct"],
            pl_eur=item["unrealizedPnl"],
        )

async def sync_tr_transactions():
    """Fetch transaction history and store new ones."""
    api = TradeRepublicApi(
        phone_no=os.environ["TRADE_REPUBLIC_PHONE"],
        pin=os.environ["TRADE_REPUBLIC_PIN"],
    )
    await api.login()

    timeline = await api.timeline()
    for event in timeline["items"]:
        insert_transaction(
            source="trade_republic",
            date=event["timestamp"][:10],
            description=event.get("title", ""),
            category=event.get("eventType", "trade"),
            amount=event.get("amount", 0),
            raw_json=str(event),
        )
```

### Rate limiting
- Add `await asyncio.sleep(1)` between requests
- Do NOT run more often than every 5 minutes
- Recommended: portfolio every 15 min, transactions every 60 min

---

## Revolut Integration

> **⚠️ UPDATED DECISION (supersedes the GoCardless content below):**
> GoCardless Bank Account Data (Nordigen) **closed new registrations** and is sunsetting
> its free tier. This project now uses **Salt Edge** (Account Information API v6) instead —
> a free-tier PSD2 aggregator that supports Revolut. See `backend/integrations/revolut.py`
> and `revolut_setup.py` for the implementation. Auth = `App-id` + `Secret` headers; flow =
> create customer → connect session (`POST /connections/connect`) → user authorizes in the
> Salt Edge widget → list connections/accounts → paginate `GET /transactions`. Env vars:
> `SALTEDGE_APP_ID`, `SALTEDGE_SECRET`, `SALTEDGE_CUSTOMER_ID`, `SALTEDGE_CONNECTION_ID`.
> The GoCardless section below is kept for historical reference only.

### What's available (historical — GoCardless)
Revolut supports **Open Banking (PSD2)** via GoCardless (formerly Nordigen). This is free, official, and requires no special approval — just a free GoCardless account.

**GoCardless Nordigen** acts as the middleware between you and Revolut's bank feeds.

### Setup Steps (5 minutes)
1. Go to https://bankaccountdata.gocardless.com → Sign up free
2. Create API credentials → copy `SECRET_ID` and `SECRET_KEY`
3. On first run, your app will generate a "requisition link" — open it in your browser, log into Revolut, grant consent (valid 90 days, then renew)
4. After consent, your `ACCOUNT_ID` is saved and fetching is automatic

### Environment Variables
```
GOCARDLESS_SECRET_ID=your_secret_id
GOCARDLESS_SECRET_KEY=your_secret_key
REVOLUT_ACCOUNT_ID=         # filled automatically on first run
```

### Implementation
```python
# backend/integrations/revolut.py

import httpx
import os
from datetime import datetime, timedelta
from backend.database import insert_transaction, upsert_balance

NORDIGEN_BASE = "https://bankaccountdata.gocardless.com/api/v2"

async def get_token() -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{NORDIGEN_BASE}/token/new/", json={
            "secret_id": os.environ["GOCARDLESS_SECRET_ID"],
            "secret_key": os.environ["GOCARDLESS_SECRET_KEY"],
        })
        r.raise_for_status()
        return r.json()["access"]

async def sync_transactions():
    token = await get_token()
    account_id = os.environ["REVOLUT_ACCOUNT_ID"]
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient() as client:
        # Transactions
        r = await client.get(
            f"{NORDIGEN_BASE}/accounts/{account_id}/transactions/",
            headers=headers,
        )
        r.raise_for_status()
        for tx in r.json()["transactions"]["booked"]:
            insert_transaction(
                source="revolut",
                date=tx["bookingDate"],
                description=tx.get("remittanceInformationUnstructured", ""),
                category=categorize(tx),
                amount=float(tx["transactionAmount"]["amount"]),
                raw_json=str(tx),
            )

        # Balance
        r = await client.get(
            f"{NORDIGEN_BASE}/accounts/{account_id}/balances/",
            headers=headers,
        )
        r.raise_for_status()
        balances = r.json()["balances"]
        available = next(b for b in balances if b["balanceType"] == "interimAvailable")
        upsert_balance(
            source="revolut",
            balance=float(available["balanceAmount"]["amount"]),
            currency=available["balanceAmount"]["currency"],
        )

def categorize(tx: dict) -> str:
    """Simple keyword-based categorization."""
    desc = (tx.get("remittanceInformationUnstructured") or "").lower()
    rules = {
        "food":        ["restaurant", "cafe", "mcdonalds", "pizza", "sushi", "rewe", "edeka", "lidl", "aldi"],
        "transport":   ["uber", "bolt", "bvg", "db ", "deutschebahn", "taxi", "fuel", "shell", "aral"],
        "shopping":    ["amazon", "zalando", "h&m", "zara", "dm ", "rossmann"],
        "health":      ["apotheke", "pharmacy", "gym", "fitness", "doctolib"],
        "subscriptions": ["netflix", "spotify", "apple", "google", "youtube"],
        "travel":      ["airbnb", "booking", "ryanair", "lufthansa", "hotel"],
        "income":      ["salary", "gehalt", "lohn", "transfer from"],
    }
    for category, keywords in rules.items():
        if any(k in desc for k in keywords):
            return category
    return "other"
```

### First-Run Consent Flow
```python
# backend/integrations/revolut_setup.py
# Run this ONCE to authorize your Revolut account

async def setup_revolut_consent():
    token = await get_token()
    async with httpx.AsyncClient() as client:
        # Find Revolut in the institution list
        r = await client.get(
            f"{NORDIGEN_BASE}/institutions/?country=DE",  # or GB, LT, etc.
            headers={"Authorization": f"Bearer {token}"},
        )
        revolut_id = next(i["id"] for i in r.json() if "REVOLUT" in i["name"].upper())

        # Create requisition
        r = await client.post(f"{NORDIGEN_BASE}/requisitions/", 
            headers={"Authorization": f"Bearer {token}"},
            json={
                "redirect": "http://localhost:8000/revolut-callback",
                "institution_id": revolut_id,
                "reference": "personal-finance-tracker",
            }
        )
        data = r.json()
        print(f"\n>>> Open this URL in your browser to authorize Revolut:\n{data['link']}\n")
        # After authorizing, the account ID is at data['accounts'][0]
```

---

## Scheduler Setup (in main.py)
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.integrations.trade_republic import sync_portfolio, sync_tr_transactions
from backend.integrations.revolut import sync_transactions as sync_revolut_transactions
from backend.alerts import check_and_alert

scheduler = AsyncIOScheduler()
scheduler.add_job(sync_portfolio,           "interval", minutes=15)
scheduler.add_job(sync_tr_transactions,     "interval", hours=1)
scheduler.add_job(sync_revolut_transactions,"interval", hours=1)
scheduler.add_job(check_and_alert,          "interval", hours=1)
scheduler.start()
```

---

## Data Freshness vs API Cost

| Source | Job | Interval | Calls/day | Notes |
|---|---|---|---|---|
| Trade Republic | Portfolio | 15 min | 96 | WebSocket, count as 1 req each |
| Trade Republic | Transactions | 60 min | 24 | Full timeline each time |
| Revolut | Transactions | 60 min | 24 | GoCardless free: no daily limit |
| Revolut | Balance | 30 min | 48 | Very cheap call |
| Limit check | Alert logic | 60 min | 24 | Local only, no external call |

**Total external API calls per day: ~192** — well within all free tiers.
