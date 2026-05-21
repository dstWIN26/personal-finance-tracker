# Agent: ARCHITECT

## Mission
Define the system architecture, data model, and directory structure. All other agents treat decisions made here as ground truth.

---

## System Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     Browser (You)                       │
│              https://your-app.onrender.com              │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTPS + Basic Auth
┌───────────────────────▼─────────────────────────────────┐
│                  FastAPI Backend                         │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  /spending  │  │  /portfolio  │  │   /limits      │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
│  ┌──────────────────────────────────────────────────┐   │
│  │              APScheduler                         │   │
│  │  • Every 15min → fetch TR portfolio               │   │
│  │  • Every 1hr   → fetch TR transactions            │   │
│  │  • Every 30min → fetch Revolut balance            │   │
│  │  • Every 1hr   → fetch Revolut transactions       │   │
│  │  • Every 1hr   → check limits → send alerts       │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │              SQLite Database                      │   │
│  └──────────────────────────────────────────────────┘   │
└─────────┬───────────────────────┬───────────────────────┘
          │                       │
┌─────────▼──────────┐  ┌────────▼──────────────┐
│  Trade Republic    │  │  GoCardless (Revolut) │
│  WebSocket API     │  │  Open Banking PSD2    │
│  (unofficial pytr) │  │  (free, official)     │
└────────────────────┘  └───────────────────────┘
```

---

## Database Schema (SQLite)

### Table: `positions` (Trade Republic portfolio)
```sql
CREATE TABLE positions (
    id          INTEGER PRIMARY KEY,
    isin        TEXT NOT NULL,
    name        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    buy_price   REAL,           -- average buy price
    current_price REAL,
    pl_pct      REAL,           -- profit/loss percentage
    pl_eur      REAL,           -- profit/loss in EUR
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Table: `transactions` (combined TR + Revolut)
```sql
CREATE TABLE transactions (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,  -- 'trade_republic' or 'revolut'
    date        DATE NOT NULL,
    description TEXT,
    category    TEXT,
    amount      REAL NOT NULL,  -- negative = expense, positive = income
    currency    TEXT DEFAULT 'EUR',
    account_id  TEXT,
    raw_json    TEXT            -- store original payload for debugging
);
```

### Table: `balances`
```sql
CREATE TABLE balances (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    balance     REAL NOT NULL,
    currency    TEXT DEFAULT 'EUR',
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Table: `limits`
```sql
CREATE TABLE limits (
    id          INTEGER PRIMARY KEY,
    category    TEXT UNIQUE,    -- NULL means "total"
    amount      REAL NOT NULL,
    period      TEXT DEFAULT 'monthly'
);
```

### Table: `alerts_sent`
```sql
CREATE TABLE alerts_sent (
    id          INTEGER PRIMARY KEY,
    limit_id    INTEGER REFERENCES limits(id),
    sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    threshold   TEXT                -- '80pct' or '100pct'
);
```

---

## Directory Structure

```
personal-finance-tracker/
├── backend/
│   ├── main.py                  # FastAPI app entry point, scheduler setup
│   ├── database.py              # SQLite init, all table creation
│   ├── config.py                # Reads .env, validates required vars
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── trade_republic.py    # pytr WebSocket client
│   │   └── revolut.py           # GoCardless Open Banking client
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── spending.py          # GET /spending, /spending/categories
│   │   ├── portfolio.py         # GET /portfolio, /portfolio/top
│   │   └── limits.py            # GET/POST/DELETE /limits
│   └── alerts.py                # Email sending + limit checking
├── frontend/
│   ├── index.html               # Single-page dashboard
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── api.js               # fetch() wrappers for all backend routes
│       ├── charts.js            # Chart.js initialization
│       ├── filters.js           # Date/category filter logic
│       └── limits.js            # Limit UI + progress bars
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Key Architecture Decisions

### Why SQLite and not PostgreSQL?
- Zero ops: no database server to manage
- Render free tier gives you SQLite as a persistent disk
- The data volume (one user, two accounts) never justifies a separate DB server
- Switching to PostgreSQL later requires only changing the SQLAlchemy connection string

### Why no npm / no build step?
- Reduces complexity dramatically — one `docker-compose up` and you're done
- Chart.js and Alpine.js load from CDN in the HTML
- No Node.js required anywhere in the stack

### Why APScheduler inside FastAPI and not a separate cron?
- Single process = single Docker container = simpler deployment
- APScheduler persists jobs in memory — fine for a single-user app
- If the process restarts, jobs resume on next startup

### Why Basic Auth and not OAuth?
- This is a single-user personal app — OAuth is overkill
- Basic Auth over HTTPS is secure for personal use
- Credentials stored in env vars, never in DB or code
