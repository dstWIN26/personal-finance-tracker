# Agent: SECURITY

## Mission
Lock down the app so your financial data is only visible to you. Runs after BACKEND, before DEPLOYMENT.

---

## Threat Model (personal single-user app)

| Threat | Mitigation |
|---|---|
| Someone finds your URL and reads your data | HTTP Basic Auth on all routes |
| API keys leak into git | `.gitignore` + `.env.example` only |
| Database exposed publicly | SQLite is a local file, never served as a route |
| Traffic intercepted | HTTPS enforced (Render does this; Let's Encrypt on Pi) |
| Brute-force password attack | Use a 20+ char random password for `DASHBOARD_PASSWORD` |
| Home IP exposed (Raspberry Pi) | Cloudflare Tunnel — you connect out, no port forwarding needed |

---

## `.gitignore` (must include these)
```
# Secrets
.env
*.pem
*.key
keys/

# Database
*.db
*.sqlite
*.sqlite3

# Python
__pycache__/
*.pyc
.venv/
venv/
dist/
*.egg-info/

# OS
.DS_Store
Thumbs.db
```

---

## `.env.example` (commit this — no real values)
```bash
# Trade Republic (unofficial pytr)
TRADE_REPUBLIC_PHONE=+49XXXXXXXXXX
TRADE_REPUBLIC_PIN=1234

# GoCardless / Nordigen (get free at bankaccountdata.gocardless.com)
GOCARDLESS_SECRET_ID=your_secret_id_here
GOCARDLESS_SECRET_KEY=your_secret_key_here
REVOLUT_ACCOUNT_ID=                     # filled automatically on first run

# Email alerts (Gmail App Password recommended)
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@gmail.com
EMAIL_SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx  # Gmail App Password (16 chars)

# Dashboard login
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=change-this-to-something-long-and-random

# App config
PORT=8000
DB_PATH=finance.db
```

---

## Authentication (already in `backend/main.py`)
The HTTP Basic Auth implementation uses `secrets.compare_digest` to prevent timing attacks:
```python
def auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, os.environ["DASHBOARD_USERNAME"])
    ok_pass = secrets.compare_digest(credentials.password, os.environ["DASHBOARD_PASSWORD"])
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
```

**Generate a strong password:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

---

## HTTPS Setup

### Option A: Render.com (automatic)
Render provisions a free TLS certificate via Let's Encrypt automatically. Nothing to configure.

### Option B: Raspberry Pi + Cloudflare Tunnel
Cloudflare Tunnel is the right choice for home servers — it:
- Routes traffic through Cloudflare's edge (hides your home IP)
- Handles TLS termination (free certificate)
- Requires **no open ports** on your router
- Requires a free Cloudflare account (no domain purchase needed with `*.trycloudflare.com`)

```bash
# Install cloudflared on Raspberry Pi
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# Login and create tunnel
cloudflared login
cloudflared tunnel create finance-tracker
cloudflared tunnel route dns finance-tracker finance.yourdomain.com

# Config file: ~/.cloudflared/config.yml
cat > ~/.cloudflared/config.yml << EOF
tunnel: <your-tunnel-id>
credentials-file: /home/pi/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: finance.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
EOF

# Run as systemd service
cloudflared service install
systemctl enable cloudflared
systemctl start cloudflared
```

### Option C: Let's Encrypt on Pi (if you have a static IP)
```bash
sudo apt install certbot nginx
sudo certbot --nginx -d finance.yourdomain.com
# Certbot auto-renews every 90 days
```

---

## Secrets in Production (Render.com)
On Render, go to your service → **Environment** → add each key from `.env.example`.
Never put secrets in the Dockerfile, docker-compose.yml, or any committed file.

---

## Database Backup (optional but recommended)
```bash
# Cron job on Pi: backup SQLite daily to a local folder
0 2 * * * cp /app/finance.db /backups/finance-$(date +%Y%m%d).db
```

For Render: use Render's disk (persistent) — the file survives redeploys.
