# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Working a planned task?** [`CLAUDE_CODE_PLAN.md`](CLAUDE_CODE_PLAN.md) is the
> source of truth for the current workstreams (security review, codebase review +
> multi-tenant readiness, TR linking) and the **hard guardrails** — read it first.
> [`README.md`](README.md) is the architecture/setup overview. [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md)
> and [`REVIEW.md`](REVIEW.md) are the latest read-only audits.

> **Planning multi-user?** The app is **single-user by design**. Converting to
> multi-tenant is an architectural change (per-user data scoping, encrypted per-user
> credentials, Postgres) — see [`MULTI_USER_PLAN.md`](MULTI_USER_PLAN.md) and the
> multi-tenant gap analysis in `REVIEW.md`. **Do not enable multi-user without an
> explicit, separate decision.**

## What This Repository Is

A **built and deployed** self-hosted personal finance tracker (FastAPI + SQLite +
APScheduler; vanilla HTML/Alpine.js/Chart.js; Docker + Caddy, behind Cloudflare). It
holds **real** Trade Republic + Revolut data for one owner. The `agents/` folder
contains the original implementation blueprints and is kept as historical spec — the
application now exists under `backend/` and `frontend/`.

## Guardrails (summary — full text in CLAUDE_CODE_PLAN.md)

1. **Never touch secrets.** `.env`, `keys/`, `*.pem`, `*.db`, `data/` are off-limits and gitignored. The repo is **public**.
2. **Auth/security code is sacred.** `backend/auth.py`, sessions, WebAuthn, security headers — change only with a written rationale and full test coverage.
3. **Do not build multi-tenancy** in normal work; prepare seams only.
4. **Tests must stay green** (`pytest`, offline). Add tests for what you change; never weaken them.
5. **Small, reviewable changes**; flag any new dependency.

## Running the Application

```bash
# Local dev (no Docker)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # localhost defaults work over http for dev
python -m backend.auth_setup         # set a one-time bootstrap password (≥12 chars)
uvicorn backend.main:app --reload --port 8000
# then open http://localhost:8000, log in with the password, and ENROL A PASSKEY
# (the password is permanently disabled once a passkey exists)

# Tests
pip install -r requirements-dev.txt && pytest

# Production: see DEPLOY.md (VPS + Caddy + Cloudflare). Link accounts: see LINKING.md.
```

## Authentication (passkey-only)

- **No username/password env var.** Login is a **WebAuthn passkey** (Touch ID / Hello).
  A one-time bootstrap password (argon2id, set via `backend.auth_setup`) is used once
  to log in and enrol the first passkey, then **permanently disabled**.
- Server-side sessions: cookie holds a random token, DB stores only its SHA-256; the
  session id is **rotated on every login**. Cookies are `__Host-`/HttpOnly/SameSite=Strict/Secure.
- IP lockout on bootstrap-password brute force; Origin/CSRF checks; CSP/HSTS/nosniff
  headers on every response. All in `backend/auth.py` — **treat as sacred**.

## Environment Variables

Copy `.env.example` to `.env`. Nothing blocks boot (`backend/config.py` warns, doesn't
raise) so the app can deploy first and link accounts later.

```
# Login / WebAuthn (passkey)
RP_ID, RP_ORIGIN, RP_NAME            # your domain (localhost for dev); passkeys bind to RP_ID
SESSION_TTL_HOURS, ACME_EMAIL        # session lifetime; Let's Encrypt email (Caddy)
MAX_PW_FAILURES, LOCKOUT_MINUTES     # bootstrap-password lockout (defaults 5 / 15)
# Trade Republic (keyfile auth; PIN never stored)
TRADE_REPUBLIC_PHONE                 # required for portfolio sync
TRADE_REPUBLIC_PIN                   # ONLY for one-time pairing — delete after tr_setup
TRADE_REPUBLIC_KEYFILE=keys/tr_keyfile.pem
# Market data (Markets/Trading tabs) — Financial Modeling Prep, free tier
FMP_API_KEY                          # free key; blank → equity panels idle (ECB yields still show)
# Bank accounts via Enable Banking (free PSD2 aggregator; RSA-key/JWT auth)
ENABLE_BANKING_APP_ID                 # registered application id
ENABLE_BANKING_KEYFILE=keys/enablebanking_private.pem   # RSA private key (the credential)
ENABLE_BANKING_REDIRECT_URL          # optional; defaults to RP_ORIGIN + /settings/banks/callback
# Revolut via Salt Edge — LEGACY alternative (no free live tier)
SALTEDGE_APP_ID, SALTEDGE_SECRET
SALTEDGE_CUSTOMER_ID, SALTEDGE_CONNECTION_ID   # filled by revolut_setup.py
# Email alerts (optional, Gmail App Password)
EMAIL_FROM, EMAIL_TO, EMAIL_SMTP_PASSWORD
PORT=8000
DB_PATH=finance.db                   # compose sets /app/data/finance.db
```

## Architecture

### Request flow
```
Browser → Cloudflare (edge) → Caddy (TLS) → uvicorn → FastAPI → SQLite
  passkey login, __Host- session cookie        │
  SecurityHeadersMiddleware on every response   ├── APScheduler (in-process)
  protected routers guarded by require_session  └── market cache (TTL) for /market/*
```

### Backend (`backend/`)
- **`main.py`** — app wiring; mounts routers behind `Depends(auth.require_session)`; serves `frontend/`; starts APScheduler in `lifespan`; `/healthz` (DB ping); `/` redirects to `/login` when unauthenticated. `docs_url`/`redoc_url` disabled.
- **`auth.py`** — WebAuthn passkeys, sessions (rotation, sliding expiry), lockout, Origin/CSRF, `SecurityHeadersMiddleware`, security-alert dispatch. **Sacred.**
- **`auth_setup.py`** — CLI to set the one-time bootstrap password.
- **`database.py`** — `init_db()` creates all tables (incl. auth tables); `connect()` is a context manager yielding a `sqlite3.Row` connection with auto-commit; `_ensure_dirs()` creates `data/`+`keys/`.
- **`config.py`** — loads `.env`; `validate()` **warns** (does not raise) on missing optional integrations; derives WebAuthn/cookie settings.
- **`integrations/trade_republic.py`** — `pytr` (unofficial WebSocket); auth via device **keyfile**, NOT the PIN; paired once via `tr_setup.py`; errors logged without credentials. Degrades gracefully when the keyfile is absent.
- **`integrations/revolut.py`** — async `httpx` to the **Salt Edge** Account Information API v6 (legacy); paginates transactions, records balances, `categorize()` keyword-maps descriptions. Consent via `revolut_setup.py`.
- **`integrations/enable_banking.py`** — free PSD2 aggregator (Revolut, Deutsche Bank, ~2,700 EEA banks). Auth = RS256 JWT signed locally with the RSA keyfile (no shared secret; signed via `cryptography`, already a dep). Redirect/consent linking driven from the Settings page; `sync_bank_connections()` pulls balances+transactions into the unified tables. Bank credentials never touch the app; only an opaque session id + account refs are stored (`bank_connections`). Degrades gracefully when unconfigured.
- **`integrations/market.py`** — live data via **FMP** (`FMP_API_KEY`, free tier) + **ECB** (keyless). Yahoo/Stooq block datacenter IPs, so FMP is used server-side; its free tier is US-listed only, so indices are shown via US-listed ETF proxies (SPY/QQQ/DIA, and FEZ/EWG/EWU for Euro Stoxx/DAX/FTSE). US bonds from FMP `/v4/treasury`, euro-area from ECB. In-process TTL cache; scheduler warm-job keeps it fresh; degrades gracefully (empty panels, no key → equities idle, ECB still shows). Data is EOD/delayed on the free tier.
- **`routes/`** — `auth`, `overview`, `spending`, `portfolio`, `market`, `limits`, `settings`. Market routes whitelist symbols (SSRF guard). `settings` reports link status (never secrets), drives Enable Banking consent, and stores non-secret profile prefs (`app_settings`). The bank consent return URL `GET /settings/banks/callback` is a **public** bounce page (in `main.py`) because the SameSite=Strict cookie isn't sent on the bank's cross-site redirect; it completes the link via a same-origin authenticated fetch.
- **`alerts.py`** — budget-limit checker (80%/100%, deduped per month) + security-event emails. Best-effort; no-ops if email unconfigured.

### Scheduler jobs (APScheduler, in-process)
| Job | Interval |
|---|---|
| TR portfolio sync | 15 min |
| TR transaction sync | 1 hr |
| Revolut (Salt Edge, legacy) sync | 1 hr |
| Enable Banking bank sync | 1 hr |
| Budget-limit check + alerts | 1 hr |
| Market quote cache warm | 15 min (FMP free tier = 250 req/day) |
| Bond-yield cache warm | 30 min |
| Daily/weekly digests | optional (commented out in `main.py`) |

### Frontend (`frontend/`)
No build step; **CDN-only** (Chart.js 4 + financial plugin + luxon, Alpine.js 3,
`webauthn.js`). Dark theme, custom CSS. `index.html` has tabs — **Overview,
Spending, Portfolio, Markets, Trading, Limits** plus header-reached **Security,
Settings** (link/manage accounts) and **Profile** (display prefs) — driven by `js/app.js`
(`app()` Alpine state; fetch-on-init, re-fetch on filter change; chart renderers
destroy the prior Chart.js instance before recreating). `login.html`/`js/login.js`
handle passkey + bootstrap-password login.

### Database schema (SQLite)
`positions` (TR snapshots; latest-per-ISIN resolved at query time), `transactions`
(unified across all sources; `amount < 0` = expense), `balances` (time-series per source),
`limits` (per-category or total monthly), `alerts_sent` (dedup log). Linking:
`bank_connections` (opaque aggregator session id + account refs — no credentials),
`bank_link_state` (single-use consent CSRF token), `app_settings` (key/value display
prefs). Auth tables: `auth_state` (singleton), `webauthn_credentials`, `sessions`,
`auth_challenges`, `login_attempts`. All queries are parameterised.

## Key Design Decisions

- **SQLite over Postgres** — zero ops for a single-user app.
- **APScheduler inside FastAPI** — single process = single container; jobs resume on restart.
- **Passkey-only WebAuthn** (not Basic Auth) — phishing-resistant, no shared secret; session id rotated on every login.
- **No npm** — Chart.js / Alpine.js / webauthn helpers load from CDN.
- **Trade Republic via `pytr`** — unofficial; PIN never persisted, device keyfile (gitignored, chmod 600) used for all syncs; rate-limit ~1 req/sec.
- **Revolut via Salt Edge** — official PSD2 aggregator (free tier); bank login never stored by us; only an opaque connection ID is kept; consent periodically re-authorised.
- **Market data via FMP (free key) + ECB** — Yahoo/Stooq block datacenter IPs, so quotes use FMP server-side (indices via US-listed ETF proxies); cached in-process and scheduler-warmed. Euro-area yields stay keyless via ECB.
