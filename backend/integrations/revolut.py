import httpx
import os
import logging
from backend.database import insert_transaction, upsert_balance

logger = logging.getLogger(__name__)

# Salt Edge Account Information API (v6)
SALTEDGE_BASE = "https://www.saltedge.com/api/v6"

def _headers() -> dict:
    return {
        "App-id": os.environ["SALTEDGE_APP_ID"],
        "Secret": os.environ["SALTEDGE_SECRET"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

async def sync_transactions():
    """Fetch Revolut transactions + balances for the linked Salt Edge connection."""
    connection_id = os.getenv("SALTEDGE_CONNECTION_ID", "")
    if not connection_id:
        logger.warning("SALTEDGE_CONNECTION_ID not set — run revolut_setup.py first")
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. List all accounts under this connection (handles multi-currency Revolut)
            r = await client.get(
                f"{SALTEDGE_BASE}/accounts",
                headers=_headers(),
                params={"connection_id": connection_id},
            )
            r.raise_for_status()
            accounts = r.json().get("data", [])

            total_tx = 0
            for acc in accounts:
                account_id = acc["id"]

                # Balance snapshot
                balance = acc.get("balance")
                if balance is not None:
                    upsert_balance(
                        source="revolut",
                        balance=float(balance),
                        currency=acc.get("currency_code", "EUR"),
                    )

                # 2. Paginate transactions for this account
                next_id = None
                while True:
                    params = {"connection_id": connection_id, "account_id": account_id}
                    if next_id:
                        params["from_id"] = next_id
                    r = await client.get(
                        f"{SALTEDGE_BASE}/transactions",
                        headers=_headers(),
                        params=params,
                    )
                    r.raise_for_status()
                    payload = r.json()
                    for tx in payload.get("data", []):
                        insert_transaction(
                            source="revolut",
                            date=tx.get("made_on", ""),
                            description=tx.get("description", ""),
                            category=categorize(tx),
                            amount=float(tx.get("amount", 0)),  # signed: <0 = expense
                            currency=tx.get("currency_code", "EUR"),
                            raw_json=str(tx),
                        )
                        total_tx += 1
                    next_id = payload.get("meta", {}).get("next_id")
                    if not next_id:
                        break

        logger.info("Revolut (Salt Edge) synced: %d transactions across %d accounts",
                    total_tx, len(accounts))
    except Exception as exc:
        logger.error("Revolut (Salt Edge) sync failed: %s", type(exc).__name__)

def categorize(tx: dict) -> str:
    """
    Keyword categorization on the transaction description.

    Note: Salt Edge also returns its own `category` field (tx['category']) which is
    often accurate. We use our own keyword map so categories stay consistent with the
    budget-limit taxonomy (food, transport, shopping, ...). The Salt Edge category is
    preserved in raw_json if you ever want to switch.
    """
    desc = (tx.get("description") or "").lower()
    rules = {
        "food":          ["restaurant", "cafe", "mcdonalds", "pizza", "sushi", "rewe", "edeka", "lidl", "aldi", "burger", "bakery"],
        "transport":     ["uber", "bolt", "bvg", "db ", "deutschebahn", "taxi", "fuel", "shell", "aral", "s-bahn", "u-bahn"],
        "shopping":      ["amazon", "zalando", "h&m", "zara", "dm ", "rossmann", "ikea", "saturn", "mediamarkt"],
        "health":        ["apotheke", "pharmacy", "gym", "fitness", "doctolib", "arzt", "krankenhaus"],
        "subscriptions": ["netflix", "spotify", "apple", "google", "youtube", "disney", "prime"],
        "travel":        ["airbnb", "booking", "ryanair", "lufthansa", "hotel", "hostel", "flug"],
        "income":        ["salary", "gehalt", "lohn", "transfer from"],
    }
    for category, keywords in rules.items():
        if any(k in desc for k in keywords):
            return category
    # Fall back to Salt Edge's own category if our rules find nothing
    return tx.get("category") or "other"
