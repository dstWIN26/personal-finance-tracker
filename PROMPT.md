# Master Setup Prompt

Copy-paste this entire prompt into a fresh Claude Code session (or use it as your CLAUDE.md) to have an agent build the entire system from scratch.

---

## Prompt

```
Build me a self-hosted personal finance tracker web application with the following specifications.
Read agents/ARCHITECT.md first for the full system design, then execute each agent file in order:
INTEGRATIONS → BACKEND → ALERTS → FRONTEND → SECURITY → DEPLOYMENT.

## Core Requirements

### Data Sources
1. Trade Republic (unofficial pytr WebSocket API)
   - Fetch portfolio positions every 15 minutes
   - Fetch transaction history every 1 hour
   - Store: position name, ISIN, quantity, buy price, current price, P&L %, P&L €

2. Revolut (GoCardless Open Banking API)
   - Fetch transactions every 1 hour
   - Fetch account balance every 30 minutes
   - Store: date, merchant, category, amount, currency, balance

### Dashboard Features
1. Spending Overview
   - Monthly spending by category (bar chart)
   - Daily spending trend (line chart)
   - Top 10 merchants this month
   - Filter by: date range, category, account, amount range

2. Portfolio Performance
   - Top 5 best performing positions (by % gain)
   - Top 5 worst performing positions (by % loss)
   - Total portfolio value + total P&L
   - Allocation chart (pie/donut)

3. Budget Limits
   - Set a monthly spending limit per category (e.g., "Food: €400")
   - Set a total monthly spending limit
   - Progress bar showing used vs limit
   - Visual warning when >80% of limit reached (yellow)
   - Visual alert when >100% of limit reached (red)

4. Email Alerts
   - Triggered when any category exceeds its limit
   - Triggered when total monthly spending exceeds limit
   - Daily summary email (optional, configurable)
   - Weekly portfolio performance digest (optional)
   - Use Gmail SMTP or Resend.com

### Tech Stack
- Backend: Python 3.11, FastAPI, APScheduler, SQLite, httpx
- Frontend: Vanilla HTML/CSS, Chart.js, Alpine.js (no npm, no build step)
- Auth: HTTP Basic Auth (single user, credentials in env vars)
- Deploy: Docker container, Render.com free tier or Raspberry Pi

### Security Requirements
- Zero API keys in source code — all in .env file
- .env never committed to git (enforce via .gitignore)
- HTTPS only in production
- Single-user basic auth on all routes
- SQLite database file excluded from git

### Project Structure
personal-finance-tracker/
├── backend/
│   ├── main.py              # FastAPI app + scheduler
│   ├── database.py          # SQLite setup + models
│   ├── integrations/
│   │   ├── trade_republic.py
│   │   └── revolut.py
│   ├── routes/
│   │   ├── spending.py
│   │   ├── portfolio.py
│   │   └── limits.py
│   └── alerts.py            # Email sending logic
├── frontend/
│   ├── index.html           # Main dashboard
│   ├── css/style.css
│   └── js/
│       ├── charts.js
│       ├── filters.js
│       └── limits.js
├── .env.example             # Template (no real values)
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── requirements.txt

### Environment Variables Required
TRADE_REPUBLIC_PHONE=+49...
TRADE_REPUBLIC_PIN=1234
REVOLUT_CLIENT_ID=...
REVOLUT_PRIVATE_KEY_PATH=./keys/revolut.pem
GOCARDLESS_SECRET_ID=...
GOCARDLESS_SECRET_KEY=...
EMAIL_FROM=yourname@gmail.com
EMAIL_TO=yourname@gmail.com
EMAIL_SMTP_PASSWORD=gmail-app-password
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=your-secure-password
PORT=8000

### Deliverables
1. All source files written and working
2. Docker container that starts with: docker-compose up
3. .env.example with all required variables documented
4. Setup instructions in README.md covering:
   a. Getting Trade Republic credentials (pytr setup)
   b. Getting GoCardless API keys (5-minute signup)
   c. Setting up Gmail App Password
   d. First run + bank consent flow
   e. Deploying to Render.com (free)
   f. Deploying to Raspberry Pi with Cloudflare Tunnel

Start by reading ORCHESTRATOR.md, then execute each agent file in order.
```
