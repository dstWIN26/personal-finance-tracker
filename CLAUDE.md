# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

This is a **specification and agent-instruction repository** for building a self-hosted personal finance tracker. The `agents/` folder contains complete implementation blueprints (with code snippets) but **no application code has been written yet**. The task is to implement the application by following the agent files in order.

## Build Execution Order

Read and execute agent files in this sequence:

```
1. agents/ARCHITECT.md    — data model + directory layout (ground truth for all other agents)
2. agents/INTEGRATIONS.md — Trade Republic (pytr WebSocket) + Revolut (GoCardless) clients
3. agents/BACKEND.md      — FastAPI routes, scheduler, database init, auth
4. agents/ALERTS.md       — spending-limit checker + email alert system
5. agents/FRONTEND.md     — single-page dashboard (no npm, CDN-only)
```

When executing a later agent, always treat `ARCHITECT.md` decisions as authoritative (schema, directory layout, tech choices).

## Running the Application

Once built:

```bash
# First-time Trade Republic auth (run once — saves session locally)
pytr login +49XXXXXXXXXX 1234

# First-time Revolut consent (generates a browser URL to authorize)
python -m backend.integrations.revolut_setup

# Start everything
docker-compose up

# Development (no Docker)
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

## Environment Variables

Copy `.env.example` to `.env`. All required vars:

```
TRADE_REPUBLIC_PHONE              # required
TRADE_REPUBLIC_PIN               # optional — only for one-time pairing, delete after
TRADE_REPUBLIC_KEYFILE=keys/tr_keyfile.pem
SALTEDGE_APP_ID, SALTEDGE_SECRET            # required
SALTEDGE_CUSTOMER_ID, SALTEDGE_CONNECTION_ID  # filled by revolut_setup.py
EMAIL_FROM, EMAIL_TO, EMAIL_SMTP_PASSWORD
DASHBOARD_USERNAME, DASHBOARD_PASSWORD
PORT=8000
DB_PATH=finance.db   # optional, defaults to finance.db
```

`backend/config.py` validates all required vars at startup and raises `RuntimeError` if any are missing.

## Architecture

### Request Flow
```
Browser → HTTPS + Basic Auth → FastAPI → SQLite
                                    ↑
                              APScheduler (background jobs)
                                    ↓
                    Trade Republic (pytr WebSocket) + GoCardless (Revolut)
```

### Backend (`backend/`)
- **`main.py`** — FastAPI app entry point; mounts routes, serves `frontend/` as static, starts APScheduler on startup. Auth is `HTTPBasic` via `secrets.compare_digest` against env vars — applied as a dependency on all routers.
- **`database.py`** — `init_db()` creates all tables; `connect()` is a context manager yielding a `sqlite3.Row`-factory connection with auto-commit.
- **`config.py`** — loads `.env` and validates required keys at import time.
- **`integrations/trade_republic.py`** — async functions using `pytr`; throttle to 1 req/sec. Auth via device **keyfile** (`keys/tr_keyfile.pem`), NOT the PIN. Pairing done once via `tr_setup.py`. Errors are logged without credentials/tracebacks.
- **`integrations/revolut.py`** — async `httpx` calls to the **Salt Edge** Account Information API (v6); paginates transactions per account, records balances, `categorize()` keyword-maps descriptions to categories. Consent set up via `revolut_setup.py`.
- **`routes/spending.py`** — `/spending`, `/spending/summary`, `/spending/daily`, `/spending/top-merchants`. Expenses are rows where `amount < 0`.
- **`routes/portfolio.py`** — `/portfolio` returns latest snapshot per ISIN (inner-join on `MAX(fetched_at)`); `/portfolio/top` returns best/worst by `pl_pct`.
- **`routes/limits.py`** — CRUD for budget limits; `/limits/status` joins live spending to each limit and returns `pct` (spent/limit × 100).
- **`alerts.py`** — `check_and_alert()` fires hourly; uses `alerts_sent` table to deduplicate within a calendar month (80% and 100% thresholds, one email per threshold per month).

### Scheduler Jobs (APScheduler, in-process)
| Job | Interval |
|---|---|
| TR portfolio sync | 15 min |
| TR transaction sync | 1 hr |
| Revolut transaction + balance sync | 1 hr |
| Limit check + email alerts | 1 hr |
| Daily summary email (optional) | Cron 21:00 |
| Weekly portfolio digest (optional) | Cron Mon 08:00 |

### Frontend (`frontend/`)
No build step. CDN dependencies: **Chart.js 4** + **Alpine.js 3**. Custom dark theme CSS (no framework).
- Design inspired by Revolut / Trade Republic / Scalable Capital: near-black `#0a0a0a` background, `#141414` cards, Inter font, white pill-button CTAs, green/red for gains/losses.
- `index.html` — single page with three Alpine-controlled tabs: Spending, Portfolio, Limits.
- `js/app.js` — `app()` function defines all Alpine state; fetches all backend endpoints on `init()` and re-fetches on filter changes.
- Charts are rendered by `renderCategoryChart()` and `renderDailyChart()` globals; each call destroys the previous Chart.js instance before creating a new one.

### Database Schema (SQLite)
- `positions` — TR portfolio snapshots; historical rows kept; latest per ISIN resolved at query time.
- `transactions` — unified TR + Revolut ledger; `source` field distinguishes them; `amount < 0` = expense.
- `balances` — time-series balance snapshots per source.
- `limits` — `category TEXT UNIQUE` (NULL = total monthly limit); upserted on conflict.
- `alerts_sent` — deduplication log; one row per `(limit_id, threshold, month)`.

## Key Design Decisions

- **SQLite over PostgreSQL** — zero ops for a single-user app; switching later requires only changing the SQLAlchemy connection string.
- **APScheduler inside FastAPI** — single process = single container; jobs resume on next startup after restart.
- **HTTP Basic Auth** — appropriate for a single-user personal app over HTTPS; credentials never stored in DB.
- **No npm** — Chart.js and Alpine.js load from CDN; no Node.js needed anywhere.
- **Trade Republic via `pytr`** — unofficial WebSocket reverse-engineering; session saved locally after one-time OTP. Rate-limit to 1 req/sec and avoid polling more often than every 5 minutes.
- **Revolut via Salt Edge** — official PSD2 Open Banking aggregator (free tier); chosen over GoCardless/Nordigen because the latter closed new registrations. Bank-level encryption, bank login never stored by us, consent revocable. Connect-session flow in `revolut_setup.py` writes `SALTEDGE_CUSTOMER_ID` + `SALTEDGE_CONNECTION_ID`; consent must be periodically re-authorized.
- **Trade Republic credentials** — PIN is never persisted. One-time device pairing (`tr_setup.py`) writes a private keyfile (`keys/`, gitignored, chmod 600) used for all subsequent logins. Credentials are never exposed by any route (API docs disabled) and never written to logs or the DB.
