# Personal Finance Tracker — Master Orchestrator

## Project Goal
A self-hosted personal finance dashboard that pulls from Trade Republic and Revolut, tracks spending, surfaces portfolio performance, enforces budget limits, and sends email alerts — running for free (or near-free) forever.

---

## Agent Team

| Agent File | Responsibility | Status |
|---|---|---|
| `agents/ARCHITECT.md` | Stack decisions, system diagram, data model | Ready |
| `agents/BACKEND.md` | FastAPI + SQLite service, API routes | Ready |
| `agents/INTEGRATIONS.md` | Trade Republic (unofficial) + Revolut Open Banking | Ready |
| `agents/FRONTEND.md` | Dashboard UI — charts, filters, limit indicators | Ready |
| `agents/SECURITY.md` | Auth, HTTPS, secrets management, no data leaks | Ready |
| `agents/ALERTS.md` | Email warnings, spending limit logic, scheduler | Ready |
| `agents/DEPLOYMENT.md` | Free-tier deploy options, Docker setup | Ready |

---

## Execution Order

```
1. ARCHITECT     → defines data model + directory structure
2. INTEGRATIONS  → connects TR + Revolut, seeds the database
3. BACKEND       → builds the API layer on top of the data
4. ALERTS        → wires the spending-limit email system
5. FRONTEND      → dashboard consumes the API
6. SECURITY      → locks down auth + secrets before any deploy
7. DEPLOYMENT    → ships it to the free host of your choice
```

When running this with Claude Code, open each agent file and say:
> "Execute the tasks in agents/BACKEND.md — use the decisions from agents/ARCHITECT.md as ground truth."

---

## Platform Decision: App vs Website

**Recommendation: Self-hosted web app** (not a mobile app)

| | Self-Hosted Web | Mobile App |
|---|---|---|
| Cost | Free (Render/Railway) | Free to build, ~$99/yr Apple dev account |
| Access | Any device via browser | Phone only (unless cross-platform) |
| Security | You control everything | App store review, no server needed |
| APIs | TR/Revolut work server-side | CORS + OAuth complications |
| Updates | Instant (just redeploy) | App store review cycle |

**Winner:** Self-hosted web app on Render.com free tier. No credit card required, stays up 24/7 on the free plan for a hobby project.

---

## API Cost Summary

### Trade Republic
- **Official API:** Not publicly available (B2B only, contact required)
- **Unofficial API (pytr):** Free, reverse-engineered WebSocket protocol
- **Update cadence:** Fetch portfolio every **15 min**, transactions every **1 hour**
- **Rate limits:** Self-throttle to 1 req/sec to avoid blocks
- **Risk:** Unofficial — could break on TR app updates

### Revolut
- **Open Banking (PSD2):** Free via GoCardless/Nordigen
  - Free tier: unlimited requisitions, standard
  - Requires: Revolut account + one-time bank consent (90-day rolling)
- **Revolut Business API:** Free for business accounts
- **Update cadence:** Transactions every **1 hour**, balance every **30 min**
- **Rate limits:** GoCardless: 10 req/sec, daily quota varies by institution

### Total API cost: **€0/month**

---

## Free Hosting Options (Ranked)

1. **Render.com** — Free web service + free PostgreSQL (90 days) or use SQLite file
2. **Railway.app** — $5 free credit/month, covers a small app entirely
3. **Fly.io** — Free tier with 3 shared VMs, good for Docker
4. **Self-hosted Raspberry Pi** — One-time hardware cost (~€35), zero ongoing cost, full privacy

**Recommended for privacy:** Raspberry Pi + Cloudflare Tunnel (no open ports, no public IP)
**Recommended for ease:** Render.com free tier

---

## Security Summary (details in `agents/SECURITY.md`)

- All API keys in environment variables, never in code
- Single-user HTTP Basic Auth (or Authelia for full auth)
- HTTPS via Render's built-in SSL (or Let's Encrypt on Raspberry Pi)
- SQLite database never exposed publicly
- Cloudflare Tunnel as reverse proxy (Raspberry Pi option) — hides your home IP

---

## Tech Stack (final)

```
Backend:    Python 3.11 + FastAPI + APScheduler
Database:   SQLite (file-based, zero ops)
Frontend:   Vanilla HTML/CSS + Chart.js + Alpine.js (no build step)
Email:      Gmail SMTP (free) or Resend.com (3,000 emails/month free)
Deploy:     Render.com free tier OR Raspberry Pi + Cloudflare Tunnel
Container:  Docker (one command to run anywhere)
```
