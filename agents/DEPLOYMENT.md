# Agent: DEPLOYMENT

## Mission
Package the app in Docker and deploy it for free. Two paths: Render.com (easiest) or Raspberry Pi (most private). Runs last.

---

## `Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## `docker-compose.yml`
```yaml
version: "3.9"
services:
  app:
    build: .
    ports:
      - "${PORT:-8000}:8000"
    env_file:
      - .env
    environment:
      - DB_PATH=/app/data/finance.db   # must point inside the mounted volume
    volumes:
      - ./data:/app/data      # SQLite persistence across restarts
    restart: unless-stopped
```

---

## Local Run (development)
```bash
# 1. Copy and fill in your secrets
cp .env.example .env
nano .env

# 2. First-time Revolut consent (run once)
docker-compose run app python -m backend.integrations.revolut_setup

# 3. Start the app
docker-compose up

# Open http://localhost:8000
```

---

## Deploy to Render.com (free, 5 minutes)

**What you get free:**
- 1 web service (512 MB RAM)
- 1 GB persistent disk
- Free TLS certificate
- Custom domain (optional)
- Auto-deploy from GitHub on push

**Steps:**
1. Push this repo to GitHub (see GitHub setup section below)
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Runtime:** Docker
   - **Branch:** main
   - **Region:** Frankfurt (eu-central, closest to DE)
5. Add a **Disk** (under Advanced): mount path `/app/data`, size 1 GB
6. Add all environment variables from `.env.example` under **Environment**
7. Click **Create Web Service** → wait ~3 minutes
8. Your app is live at `https://your-app-name.onrender.com`

**Cost: €0/month** (free tier — spins down after 15 min inactivity, but wakes in ~30 seconds)

To keep it always awake (optional): add an uptime monitor at https://uptimerobot.com (free, pings every 5 min)

---

## Deploy to Raspberry Pi (most private, zero ongoing cost)

**Hardware needed:** Raspberry Pi 4 (2GB+), microSD card, power supply (~€35-50 one-time)

```bash
# On Raspberry Pi: install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker pi

# Clone your repo
git clone https://github.com/yourusername/personal-finance-tracker.git
cd personal-finance-tracker

# Fill in secrets
cp .env.example .env
nano .env

# First-time Revolut consent
docker-compose run app python -m backend.integrations.revolut_setup

# Start
docker-compose up -d

# Auto-start on boot
sudo systemctl enable docker
```

Then set up Cloudflare Tunnel (see `agents/SECURITY.md`) to access it remotely with HTTPS.

**Cost: €0/month after hardware purchase.**

---

## GitHub (already connected — account: dstWIN26)

The repo is live at `https://github.com/dstWIN26/personal-finance-tracker`.
Render will auto-deploy every time you push to `main`.

To push a change:
```bash
git add .
git commit -m "your message"
git push
```

---

## Ongoing Costs Summary

| Item | Cost |
|---|---|
| Render.com free tier | €0/month |
| GoCardless / Nordigen (Revolut) | €0/month |
| Trade Republic unofficial API | €0/month |
| Gmail SMTP | €0/month |
| Cloudflare Tunnel | €0/month |
| Domain name (optional) | ~€10/year |
| Raspberry Pi (optional, one-time) | ~€50 |
| **Total running cost** | **€0/month** |

---

## Render Free Tier Limitations & Workarounds

| Limitation | Workaround |
|---|---|
| Spins down after 15 min inactivity | UptimeRobot free plan pings every 5 min |
| 512 MB RAM | FastAPI + SQLite easily fits in 200 MB |
| Slow cold start (~30s) | UptimeRobot prevents this |
| No persistent disk on free plan? | Add a 1 GB disk ($0.25/GB/month) — or use Fly.io |

**Fly.io alternative** (if Render disk cost is a concern):
```bash
# Install flyctl (no Homebrew needed)
# Download from: https://fly.io/docs/hands-on/install-flyctl/
# macOS: curl -L https://fly.io/install.sh | sh

fly auth login
fly launch            # auto-detects Dockerfile
fly volumes create finance_data --size 1
```
