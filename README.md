# Personal Finance Tracker

Self-hosted, single-user dashboard for **Trade Republic** portfolio, **Revolut**
spending, and **live market data** (indices, VIX, US + European bond yields).
One always-on Docker container — FastAPI + SQLite + an in-process scheduler —
behind Caddy (automatic HTTPS) and, optionally, Cloudflare. Login is a **hardware
passkey** (Touch ID / Windows Hello), not a password.

> **Status:** built and running. Single-user by design. Making it multi-tenant is
> a real architectural change — see [`MULTI_USER_PLAN.md`](MULTI_USER_PLAN.md).

---

## Asking Claude about this project

This README is written to be dropped into a Claude session as the entry point for
questions about **design, setup, and architecture**. The fastest way to give Claude
full context:

- **Whole repo:** open the project in Claude Code (`claude` in the repo root) and ask
  directly — it reads files on demand. [`CLAUDE.md`](CLAUDE.md) is loaded automatically.
- **Just the docs:** paste this README plus the relevant file from the
  [Documentation map](#documentation-map) below.

Good questions to ask, and where the answer lives:

| You want to understand… | Read / point Claude at |
|---|---|
| Overall design & data flow | [Architecture](#architecture) (this file) |
| What runs where, what changes vs. stays static | [File map](#file-map--what-changes-what-stays-static) |
| How login / sessions / passkeys work | [Security model](#security-model) + `backend/auth.py` |
| How to deploy it for real | [`DEPLOY.md`](DEPLOY.md) |
| Putting it behind Cloudflare | [Deployment](#deployment) + `DEPLOY.md` → "Behind Cloudflare" |
| Linking Trade Republic / Revolut | [`LINKING.md`](LINKING.md) |
| Where market numbers come from | [Data providers](#data-providers) |
| Turning it multi-user | [`MULTI_USER_PLAN.md`](MULTI_USER_PLAN.md) |

---

## Features (the dashboard tabs)

| Tab | What it shows |
|---|---|
| **Overview** | Net worth, invested vs. cash, today's spend, allocation donut, net-worth sparkline, recent activity |
| **Spending** | Monthly/daily charts by category, top merchants, full transaction list with filters (Revolut) |
| **Portfolio** | Trade Republic positions, P&L per position, total value, best/worst performers |
| **Markets** | Live index quotes + sparklines, VIX, sector heatmap, watchlist |
| **Trading** | Candlestick charts, US & European bond yield curves, VIX, the biggest indices |
| **Limits** | Per-category / total monthly budget limits, progress bars, email alerts at 80% & 100% |
| **Security** | Active sessions (view + revoke), enrol an additional passkey, sign out |

---

## Architecture

```
                         Cloudflare (optional edge: WAF, rate-limit, DDoS)
                                          │  proxied, CF-Connecting-IP
                                          ▼
  Browser ──HTTPS──►  Caddy (auto Let's Encrypt)  ──►  FastAPI app (uvicorn)
  passkey login                                            │
  __Host- session cookie                                   ├── SQLite  (data/finance.db)
                                                           └── APScheduler (in-process)
                                                                 ├ Revolut sync
                                                                 ├ market quotes  ~60s
                                                                 ├ bond yields    ~30m
                                                                 └ budget-limit check
```

- **Backend:** Python 3.11, FastAPI, APScheduler, SQLite, httpx. No ORM — direct SQL in `backend/database.py`.
- **Frontend:** vanilla HTML + Alpine.js 3 + Chart.js 4 (+ financial/candlestick plugin, luxon). **No npm, no build step** — everything via CDN.
- **Auth:** WebAuthn passkeys (`py_webauthn`), argon2id for the one-time bootstrap password, server-side sessions with rotation. See [Security model](#security-model).
- **Deploy:** one Docker image; `docker-compose.yml` runs the app + Caddy. Persistent state lives in the `data/` and `keys/` volumes only.

### Data flow

1. The scheduler refreshes broker/bank/market data on timers into SQLite + an in-process TTL cache.
2. Browser requests hit FastAPI routes (`/overview`, `/spending`, `/portfolio`, `/market`, `/limits`), all gated by `Depends(auth.require_session)`.
3. Routes read from SQLite / cache and return JSON; Alpine renders, Chart.js draws.
4. Email alerts (budget thresholds, new-device sign-ins) are best-effort and off the request path.

---

## File map — what changes, what stays static

```
backend/
  main.py            ← app wiring, scheduler jobs, /healthz, router guards   (changes when adding features)
  config.py          ← env-var parsing + WebAuthn/session settings          (rarely; edit env, not this)
  database.py        ← schema + SQL + _ensure_dirs()                        (changes with the data model)
  auth.py            ← WebAuthn, sessions, lockout, security headers, alerts (security-critical; rarely)
  auth_setup.py      ← CLI: set one-time bootstrap password                 (static)
  alerts.py          ← email: budget + security notifications               (rarely)
  routes/            ← HTTP endpoints, one file per area                    (changes often)
    auth.py  overview.py  spending.py  portfolio.py  market.py  limits.py
  integrations/      ← external data clients                                (changes when a provider changes)
    revolut.py  enable_banking.py  market.py  *_setup.py   (no trade_republic — TR is CSV import)

frontend/            ← single-page dashboard, no build step                 (changes often: UI work)
  index.html  login.html  css/style.css  js/app.js  js/login.js  js/webauthn.js

deploy / ops         ← infra, mostly static                                 (touch only when deploying/hardening)
  Dockerfile  docker-compose.yml  Caddyfile  .env.example
  scripts/backup.sh  scripts/restore.sh  scripts/cloudflare-firewall.sh
  .github/workflows/ci.yml

tests/               ← pytest, offline (no network, temp DB, stubbed SMTP)  (changes with the code)
docs (*.md)          ← see Documentation map below
```

**Runtime state that must be backed up and is gitignored:** `data/` (SQLite DB + enrolled
passkeys + sessions) and `keys/` (the Enable Banking RSA private key). `.env` holds
secrets. None of these are in git.

---

## Data providers

| Data | Source | Key needed? |
|---|---|---|
| Indices, VIX, quotes, candlesticks, heatmap | Yahoo Finance chart API | No (keyless) |
| US Treasury yields | US Treasury par-yield XML feed | No |
| European yields | ECB Statistical Data Warehouse | No |
| Portfolio / TR transactions | Trade Republic **CSV export** (uploaded in Settings) | No API — manual import |
| Spending (transactions) | Revolut via **Salt Edge** AIS API v6 | App-id + Secret |
| Email alerts | Gmail SMTP (App Password) | App Password |

Market data is keyless and cached in-process; the broker/bank links are the only
parts that need credentials, and both are optional — the app boots and the market
tabs work with nothing configured.

---

## Quick start (local dev, no Docker)

```bash
git clone https://github.com/dstWIN26/personal-finance-tracker.git
cd personal-finance-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # defaults (RP_ID=localhost) work over http for dev
python -m backend.auth_setup  # set a one-time bootstrap password (≥ 12 chars)
uvicorn backend.main:app --reload --port 8000
```

Open <http://localhost:8000>, sign in with the bootstrap password, then **enrol a
passkey** when prompted. After enrolment the password is permanently disabled and
the only way in is your device biometric.

> Linking Trade Republic / Revolut is a separate one-time step — see [`LINKING.md`](LINKING.md).
> The market tabs work immediately without any linking.

---

## Deployment

**Production target: a small VPS + Docker + Caddy, fronted by Cloudflare.** This app is
a *stateful, always-on server* — it keeps SQLite on disk and runs
background scheduler jobs — so it needs a persistent filesystem and a long-lived
process. Full walkthrough in **[`DEPLOY.md`](DEPLOY.md)** (provisioning, passkey
enrolment, backups, and the "Behind Cloudflare" hardening steps).

```bash
# on the VPS, after pointing a Proxied (orange-cloud) DNS record at it:
git clone https://github.com/dstWIN26/personal-finance-tracker.git
cd personal-finance-tracker && cp .env.example .env   # set RP_ID / RP_ORIGIN / ACME_EMAIL
docker compose run --rm app python -m backend.auth_setup
mkdir -p data keys && docker compose up -d --build
sudo ./scripts/cloudflare-firewall.sh                 # lock origin to Cloudflare IPs
```

### Why not Cloudflare Pages / Workers?

You can't host this app *on* Cloudflare Pages or Workers as-is. Pages serves static
sites; Workers run short-lived JS/WASM isolates with **no persistent filesystem, no
always-on process, and no native-Python runtime** — but this app needs all three
(on-disk SQLite, the APScheduler background jobs, and native deps like
`argon2-cffi`/`httpx`). Cloudflare's role here is the **edge in front of the VPS**:
your purchased domain + Cloudflare nameservers/DNS are exactly what you point
(Proxied) at the origin server. See the chat/answer and `DEPLOY.md` for detail.

---

## Environment variables

There is **no username/password env var** — login is a passkey set up via
`backend.auth_setup`.

| Variable | Description |
|---|---|
| `RP_ID` | Registrable domain the passkey binds to, no scheme/port (e.g. `finance.example.com`). `localhost` for dev. |
| `RP_ORIGIN` | Full origin the browser sends (e.g. `https://finance.example.com`). |
| `RP_NAME` | Display name shown during passkey enrolment. |
| `SESSION_TTL_HOURS` | Session lifetime (default `12`). |
| `ACME_EMAIL` | Email for Let's Encrypt (Caddy) renewal notices. |
| `MAX_PW_FAILURES` / `LOCKOUT_MINUTES` | Bootstrap-password brute-force lockout (defaults `5` / `15`). |
| _(Trade Republic)_ | No env vars — TR is CSV-import-only (Settings → Trade Republic). |
| `SALTEDGE_APP_ID` / `SALTEDGE_SECRET` | Salt Edge credentials for Revolut. |
| `SALTEDGE_CUSTOMER_ID` / `SALTEDGE_CONNECTION_ID` | Filled in by `revolut_setup`. |
| `EMAIL_FROM` / `EMAIL_TO` / `EMAIL_SMTP_PASSWORD` | Gmail alerts (App Password). Optional. |
| `PORT` / `DB_PATH` | HTTP port (`8000`) and SQLite path (default `finance.db` → `data/`). |

Everything except `RP_*` is optional at boot — the dashboard runs and warns about
idle integrations rather than failing.

---

## Security model

- **Passkey-only login** after setup (WebAuthn, user-verification required) —
  phishing-resistant, no shared secret to steal. One-time bootstrap password
  (argon2id) is disabled the moment a passkey is enrolled.
- **Server-side sessions**: the cookie holds a random token; the DB stores only its
  SHA-256. The **session ID is rotated on every login** and destroyed on logout.
  View and revoke active sessions from the Security tab.
- **Cookies**: HttpOnly, SameSite=Strict, Secure + `__Host-` prefix on HTTPS.
- **IP lockout** after repeated bootstrap-password failures. The client IP is taken
  from the real TCP peer by default; set `TRUST_CF_HEADERS=true` (only once the origin
  is firewalled to Cloudflare) to key it on `CF-Connecting-IP` instead — a spoofed
  header can't bypass the lockout when trust is off.
- **Headers** on every response: CSP, HSTS, nosniff, frame-ancestors none.
- **Origin-checked** state-changing requests (CSRF defence on top of SameSite).
- **Secrets stay out of git**: `.env`, `*.pem`, `keys/`, `*.db`, and `backups/` are
  gitignored. This repo is public — never commit real secrets.
- **Credential handling**: Trade Republic uses no credentials at all (CSV import only); Revolut bank
  login is never seen by this app (Salt Edge holds it; you store an opaque ID).
- **Login alerts**: if email is configured, you're notified on every sign-in, new
  passkey enrolment, and lockout — with time, IP, and device.

---

## Testing & CI

```bash
pip install -r requirements-dev.txt
pytest                       # offline: temp DB per test, stubbed SMTP, no network
```

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the test
suite on Python 3.11 and a **gitleaks** secret scan on every push.

`GET /healthz` pings the DB and returns 503 if it can't (used by the Docker
`HEALTHCHECK`).

---

## Backups

`data/` (DB + passkeys) and `keys/` (TR credential) are your only irreplaceable state.
`scripts/backup.sh` makes an **AES-256 encrypted** snapshot (optionally shipped
off-box via scp/rclone); `scripts/restore.sh` recovers it. Set up the nightly cron in
[`DEPLOY.md`](DEPLOY.md) and test a restore once. An untested backup isn't a backup.

---

## Documentation map

| File | Purpose |
|---|---|
| [`README.md`](README.md) | This file — design, setup, architecture entry point |
| [`CLAUDE.md`](CLAUDE.md) | Guidance auto-loaded by Claude Code in this repo |
| [`DEPLOY.md`](DEPLOY.md) | VPS + Caddy + Cloudflare deployment, backups, security summary |
| [`LINKING.md`](LINKING.md) | Pairing Trade Republic + Revolut after deploy |
| [`MULTI_USER_PLAN.md`](MULTI_USER_PLAN.md) | Reference plan for going multi-tenant |
| [`agents/`](agents/) | Original implementation blueprints (historical spec) |
