# Personal Finance Tracker

Self-hosted dashboard for Trade Republic portfolio + Revolut spending. Single Docker container, zero ongoing cost.

## Features

- **Spending** — monthly/daily charts by category, top merchants, full transaction list with filters
- **Portfolio** — TR positions, P&L per position, total value, best/worst performers
- **Budget Limits** — set per-category or total monthly limits, progress bars, email alerts at 80% and 100%
- **Email Alerts** — Gmail SMTP, deduped within calendar month, optional daily/weekly digests

---

## Quick Start

### 1. Clone & configure

```bash
git clone https://github.com/dstWIN26/personal-finance-tracker.git
cd personal-finance-tracker
cp .env.example .env
nano .env   # fill in your credentials
```

### 2. Trade Republic setup (one-time, PIN never stored)

```bash
pip install -r requirements.txt
python -m backend.integrations.tr_setup
# Enter your PIN + the 4-digit code from your TR app when prompted
```

This pairs the device and writes `keys/tr_keyfile.pem`. **After pairing, delete the
`TRADE_REPUBLIC_PIN` line from `.env`** — ongoing syncs authenticate with the keyfile,
not your PIN. To revoke access later, reset paired devices in the Trade Republic app.

### 3. Revolut setup via Salt Edge (one-time)

1. Sign up free at [salt edge → secrets](https://www.saltedge.com/clients/profile/secrets)
2. Copy your `App-id` and `Secret` into `.env` (`SALTEDGE_APP_ID`, `SALTEDGE_SECRET`)
3. Run the consent flow:

```bash
python -m backend.integrations.revolut_setup
```

Open the URL it prints, choose Revolut, log in, grant read-only consent. The script
prints your `SALTEDGE_CUSTOMER_ID` and `SALTEDGE_CONNECTION_ID` — add both to `.env`.
Salt Edge uses bank-level encryption, never stores your bank login, and consent is
revocable at any time.

### 4. Gmail App Password

1. Google Account → Security → 2-Step Verification (must be ON)
2. Search "App passwords" → Create → name it "Finance Tracker"
3. Copy the 16-char password → `EMAIL_SMTP_PASSWORD` in `.env`

### 5. Start the app

```bash
docker-compose up
```

Open [http://localhost:8000](http://localhost:8000) — log in with `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.

---

## Environment Variables

| Variable | Description |
|---|---|
| `TRADE_REPUBLIC_PHONE` | Your TR phone number (e.g. `+49XXXXXXXXXX`) |
| `TRADE_REPUBLIC_PIN` | **Optional** — only for one-time pairing; delete after `tr_setup.py` |
| `TRADE_REPUBLIC_KEYFILE` | Path to the device keyfile (default `keys/tr_keyfile.pem`) |
| `SALTEDGE_APP_ID` | Salt Edge App-id |
| `SALTEDGE_SECRET` | Salt Edge Secret |
| `SALTEDGE_CUSTOMER_ID` | Filled by `revolut_setup.py` |
| `SALTEDGE_CONNECTION_ID` | Filled by `revolut_setup.py` |
| `EMAIL_FROM` | Gmail address to send from |
| `EMAIL_TO` | Email address to receive alerts |
| `EMAIL_SMTP_PASSWORD` | Gmail App Password (16 chars) |
| `DASHBOARD_USERNAME` | Dashboard login username |
| `DASHBOARD_PASSWORD` | Dashboard login password (use 20+ chars) |
| `PORT` | HTTP port (default: `8000`) |
| `DB_PATH` | SQLite path (default: `finance.db`) |

Generate a strong dashboard password:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

---

## Security Model

How your financial credentials are handled:

- **Trade Republic PIN is never stored.** After one-time device pairing, auth uses a private keyfile (`keys/tr_keyfile.pem`). Delete `TRADE_REPUBLIC_PIN` from `.env` once paired. Revoke anytime by resetting paired devices in the TR app.
- **Revolut bank login is never seen by this app.** Salt Edge handles the bank connection; you only store an opaque connection ID. Revoke consent anytime in Salt Edge.
- **Nothing sensitive is exposed.** API docs are disabled, no route returns env vars or credentials, and integration errors are logged without tracebacks or secrets.
- **Secrets stay out of git.** `.env`, `*.pem`, `keys/`, and `*.db` are all gitignored.
- **Lock down file permissions:**
  ```bash
  chmod 600 .env keys/tr_keyfile.pem
  ```
- **Recommended:** enable full-disk encryption on the host (FileVault on macOS, LUKS on a Pi). On a single self-hosted box this protects secrets at rest better than app-level encryption, since the app would need the decryption key at runtime anyway.

---

## Development (no Docker)

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

---

## Deploy to Render.com (free)

1. Push to GitHub
2. [render.com](https://render.com) → New → Web Service → connect repo
3. Runtime: **Docker** | Branch: `main` | Region: Frankfurt
4. Add a **Disk** (Advanced): mount path `/app/data`, size 1 GB
5. Add all env vars from `.env.example` under **Environment**
6. Click **Create Web Service** — live in ~3 minutes at `https://your-app.onrender.com`

To keep it awake: add a free [UptimeRobot](https://uptimerobot.com) monitor pinging every 5 min.

---

## Deploy to Raspberry Pi + Cloudflare Tunnel

```bash
# Install Docker on Pi
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker pi

# Clone & start
git clone https://github.com/dstWIN26/personal-finance-tracker.git
cd personal-finance-tracker
cp .env.example .env && nano .env
docker-compose up -d
```

Then follow [agents/SECURITY.md](agents/SECURITY.md) to set up Cloudflare Tunnel for HTTPS without port forwarding.

---

## Architecture

```
Browser → HTTPS + Basic Auth → FastAPI → SQLite
                                    ↑
                              APScheduler
                          ┌─────────────────┐
                          │ TR portfolio 15m │
                          │ TR txns      60m │
                          │ Revolut      60m │
                          │ Limit check  60m │
                          └─────────────────┘
```

- **Backend**: Python 3.11, FastAPI, APScheduler, SQLite, httpx
- **Frontend**: Vanilla HTML + Alpine.js 3 + Chart.js 4 + Pico.css (no npm, CDN only)
- **Auth**: HTTP Basic Auth via `secrets.compare_digest` (timing-safe)
- **Data**: Trade Republic via `pytr` (unofficial WebSocket); Revolut via GoCardless Open Banking (official PSD2)

---

## Consent Renewal

GoCardless consent expires after 90 days. Re-run the setup script to renew:

```bash
docker-compose run app python -m backend.integrations.revolut_setup
```
