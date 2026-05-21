# Agent: BACKEND

## Mission
Build the FastAPI service: database init, all API routes, and the scheduler. Runs after INTEGRATIONS has been completed.

---

## `requirements.txt`
```
fastapi==0.111.0
uvicorn[standard]==0.29.0
apscheduler==3.10.4
httpx==0.27.0
pytr==0.4.1
python-dotenv==1.0.1
python-multipart==0.0.9
```

---

## `backend/config.py`
```python
import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED = [
    "TRADE_REPUBLIC_PHONE",
    "TRADE_REPUBLIC_PIN",
    "GOCARDLESS_SECRET_ID",
    "GOCARDLESS_SECRET_KEY",
    "REVOLUT_ACCOUNT_ID",
    "EMAIL_FROM",
    "EMAIL_TO",
    "EMAIL_SMTP_PASSWORD",
    "DASHBOARD_USERNAME",
    "DASHBOARD_PASSWORD",
]

def validate():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")
```

---

## `backend/database.py`
```python
import sqlite3, os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "finance.db")

def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id            INTEGER PRIMARY KEY,
            isin          TEXT NOT NULL,
            name          TEXT NOT NULL,
            quantity      REAL NOT NULL,
            buy_price     REAL,
            current_price REAL,
            pl_pct        REAL,
            pl_eur        REAL,
            fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY,
            source      TEXT NOT NULL,
            date        DATE NOT NULL,
            description TEXT,
            category    TEXT,
            amount      REAL NOT NULL,
            currency    TEXT DEFAULT 'EUR',
            raw_json    TEXT
        );
        CREATE TABLE IF NOT EXISTS balances (
            id          INTEGER PRIMARY KEY,
            source      TEXT NOT NULL,
            balance     REAL NOT NULL,
            currency    TEXT DEFAULT 'EUR',
            fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS limits (
            id       INTEGER PRIMARY KEY,
            category TEXT UNIQUE,
            amount   REAL NOT NULL,
            period   TEXT DEFAULT 'monthly'
        );
        CREATE TABLE IF NOT EXISTS alerts_sent (
            id         INTEGER PRIMARY KEY,
            limit_id   INTEGER REFERENCES limits(id),
            sent_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            threshold  TEXT
        );
        """)

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def upsert_position(isin, name, quantity, buy_price, current_price, pl_pct, pl_eur):
    with connect() as conn:
        conn.execute("""
            INSERT INTO positions (isin, name, quantity, buy_price, current_price, pl_pct, pl_eur)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [isin, name, quantity, buy_price, current_price, pl_pct, pl_eur])

def insert_transaction(source, date, description, category, amount, currency="EUR", raw_json=None):
    with connect() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO transactions
                (source, date, description, category, amount, currency, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [source, date, description, category, amount, currency, raw_json])

def upsert_balance(source, balance, currency="EUR"):
    with connect() as conn:
        conn.execute("""
            INSERT INTO balances (source, balance, currency) VALUES (?, ?, ?)
        """, [source, balance, currency])
```

---

## `backend/routes/spending.py`
```python
from fastapi import APIRouter, Query
from backend.database import connect
from typing import Optional

router = APIRouter(prefix="/spending", tags=["spending"])

@router.get("/")
def get_spending(
    start: Optional[str] = None,
    end: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
):
    filters, params = ["amount < 0"], []
    if start:        filters.append("date >= ?");    params.append(start)
    if end:          filters.append("date <= ?");    params.append(end)
    if category:     filters.append("category = ?"); params.append(category)
    if source:       filters.append("source = ?");   params.append(source)
    if min_amount:   filters.append("ABS(amount) >= ?"); params.append(min_amount)
    if max_amount:   filters.append("ABS(amount) <= ?"); params.append(max_amount)

    where = " AND ".join(filters)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE {where} ORDER BY date DESC LIMIT 500",
            params
        ).fetchall()
    return [dict(r) for r in rows]

@router.get("/summary")
def spending_summary(month: Optional[str] = None):
    """Return total spending per category for the given month (YYYY-MM)."""
    if not month:
        from datetime import date
        month = date.today().strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("""
            SELECT category, SUM(ABS(amount)) as total
            FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
            GROUP BY category
            ORDER BY total DESC
        """, [month]).fetchall()
    return [dict(r) for r in rows]

@router.get("/top-merchants")
def top_merchants(month: Optional[str] = None, limit: int = 10):
    if not month:
        from datetime import date
        month = date.today().strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("""
            SELECT description, SUM(ABS(amount)) as total, COUNT(*) as count
            FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
            GROUP BY description
            ORDER BY total DESC
            LIMIT ?
        """, [month, limit]).fetchall()
    return [dict(r) for r in rows]

@router.get("/daily")
def daily_trend(month: Optional[str] = None):
    if not month:
        from datetime import date
        month = date.today().strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("""
            SELECT date, SUM(ABS(amount)) as total
            FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
            GROUP BY date
            ORDER BY date
        """, [month]).fetchall()
    return [dict(r) for r in rows]
```

---

## `backend/routes/portfolio.py`
```python
from fastapi import APIRouter
from backend.database import connect

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

@router.get("/")
def get_portfolio():
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.*
            FROM positions p
            INNER JOIN (
                SELECT isin, MAX(fetched_at) as latest
                FROM positions GROUP BY isin
            ) latest ON p.isin = latest.isin AND p.fetched_at = latest.latest
        """).fetchall()
    positions = [dict(r) for r in rows]
    total_value = sum(p["quantity"] * (p["current_price"] or 0) for p in positions)
    total_pl    = sum(p["pl_eur"] or 0 for p in positions)
    return {"positions": positions, "total_value": total_value, "total_pl": total_pl}

@router.get("/top")
def top_performers(n: int = 5):
    with connect() as conn:
        latest = conn.execute("""
            SELECT p.* FROM positions p
            INNER JOIN (SELECT isin, MAX(fetched_at) as latest FROM positions GROUP BY isin) l
            ON p.isin = l.isin AND p.fetched_at = l.latest
        """).fetchall()
    positions = [dict(r) for r in latest]
    best  = sorted(positions, key=lambda p: p["pl_pct"] or 0, reverse=True)[:n]
    worst = sorted(positions, key=lambda p: p["pl_pct"] or 0)[:n]
    return {"best": best, "worst": worst}
```

---

## `backend/routes/limits.py`
```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.database import connect

router = APIRouter(prefix="/limits", tags=["limits"])

class LimitIn(BaseModel):
    category: Optional[str] = None  # None = total spending limit
    amount: float
    period: str = "monthly"

@router.get("/")
def get_limits():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM limits").fetchall()
    return [dict(r) for r in rows]

@router.post("/")
def set_limit(limit: LimitIn):
    with connect() as conn:
        conn.execute("""
            INSERT INTO limits (category, amount, period)
            VALUES (?, ?, ?)
            ON CONFLICT(category) DO UPDATE SET amount=excluded.amount
        """, [limit.category, limit.amount, limit.period])
    return {"status": "ok"}

@router.delete("/{limit_id}")
def delete_limit(limit_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM limits WHERE id = ?", [limit_id])
    return {"status": "ok"}

@router.get("/status")
def limits_status():
    """Return each limit with current usage and percentage."""
    from datetime import date
    month = date.today().strftime("%Y-%m")
    with connect() as conn:
        limits = conn.execute("SELECT * FROM limits").fetchall()
        result = []
        for lim in limits:
            if lim["category"]:
                spent = conn.execute("""
                    SELECT COALESCE(SUM(ABS(amount)), 0) as total
                    FROM transactions
                    WHERE amount < 0 AND category = ? AND strftime('%Y-%m', date) = ?
                """, [lim["category"], month]).fetchone()["total"]
            else:
                spent = conn.execute("""
                    SELECT COALESCE(SUM(ABS(amount)), 0) as total
                    FROM transactions
                    WHERE amount < 0 AND strftime('%Y-%m', date) = ?
                """, [month]).fetchone()["total"]
            result.append({
                **dict(lim),
                "spent": spent,
                "pct": round(spent / lim["amount"] * 100, 1) if lim["amount"] else 0,
            })
    return result
```

---

## `backend/main.py`
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import secrets, os

from backend.config import validate
from backend.database import init_db
from backend.routes import spending, portfolio, limits
from backend.alerts import check_and_alert
from backend.integrations.trade_republic import sync_portfolio, sync_tr_transactions
from backend.integrations.revolut import sync_transactions as sync_revolut_transactions

validate()
init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(sync_portfolio,           "interval", minutes=15)
    scheduler.add_job(sync_tr_transactions,     "interval", hours=1)
    scheduler.add_job(sync_revolut_transactions,"interval", hours=1)
    scheduler.add_job(check_and_alert,          "interval", hours=1)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="Personal Finance Tracker", lifespan=lifespan,
              docs_url=None, redoc_url=None)
security = HTTPBasic()

def auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, os.environ["DASHBOARD_USERNAME"])
    ok_pass = secrets.compare_digest(credentials.password, os.environ["DASHBOARD_PASSWORD"])
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username

app.include_router(spending.router,   dependencies=[Depends(auth)])
app.include_router(portfolio.router,  dependencies=[Depends(auth)])
app.include_router(limits.router,     dependencies=[Depends(auth)])

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
def index(_=Depends(auth)):
    return FileResponse("frontend/index.html")
```
