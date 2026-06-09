# Deploying (VPS + Caddy, automatic HTTPS)

This app is a single always-on container: FastAPI + an in-process scheduler +
SQLite + a Trade Republic keyfile on disk. It runs behind **Caddy**, which gets
and renews a Let's Encrypt certificate automatically. Login is a **hardware
passkey** (Touch ID) after a one-time bootstrap password.

## 0. Prerequisites
- A small VPS (e.g. Hetzner CX22 / DigitalOcean) running Linux with Docker + the
  Docker Compose plugin.
- A domain name with an **A/AAAA record pointing at the VPS IP** (e.g.
  `finance.yourdomain.com`). Passkeys and Secure cookies require real HTTPS, so a
  domain is mandatory — you cannot use a bare IP.
- Ports **80** and **443** open in the firewall.

## 1. Get the code + configure
```bash
git clone https://github.com/dstWIN26/personal-finance-tracker.git
cd personal-finance-tracker
cp .env.example .env
```
Edit `.env` and set **at minimum**:
```
RP_ID=finance.yourdomain.com
RP_NAME=Finance Tracker
RP_ORIGIN=https://finance.yourdomain.com
ACME_EMAIL=you@yourdomain.com
```
Leave the Trade Republic / Revolut / e-mail values as placeholders for now — the
app boots fine without them and you'll link them in step 5.

> `.env`, `keys/`, and `*.db` are gitignored. Never commit real secrets — this
> repository is public.

## 2. Set your one-time bootstrap password
```bash
docker compose run --rm app python -m backend.auth_setup
```
Enter a long password (≥ 12 chars). You'll use it **once**.

## 3. Launch
```bash
mkdir -p data keys
docker compose up -d --build
docker compose logs -f caddy   # watch the cert get issued
```

## 4. First login → enrol your passkey
1. Open `https://finance.yourdomain.com` on your Mac.
2. Sign in with the bootstrap password.
3. When prompted, **Enrol passkey (Touch ID)** and confirm with your fingerprint.
4. Done — the bootstrap password is now **permanently disabled**. From now on the
   only way in is your Mac's biometric passkey.
   - Add more devices later via **+ Passkey** in the top bar (while logged in).
   - **Lost every passkey?** Re-run step 2 on the server (shell access required)
     to re-enable a bootstrap password — the documented break-glass path.

## 5. Link Trade Republic (and Revolut) — do this after you're logged in
See **[LINKING.md](LINKING.md)** for the step-by-step pairing flow. In short:
```bash
# Pair the Trade Republic device once (writes keys/tr_keyfile.pem):
docker compose run --rm app python -m backend.integrations.tr_setup
# then set TRADE_REPUBLIC_PHONE in .env, remove the PIN line, and restart:
docker compose up -d
```

## Updating
```bash
git pull && docker compose up -d --build
```
The `data/` volume (DB, sessions, enrolled passkeys) and `keys/` persist across
rebuilds.

## Backups (do this — it's your only safety net)
`data/` holds your DB **and enrolled passkeys**; `keys/` holds the Trade Republic
device credential. Lose the box without a backup and you lose your financial
history *and* could be locked out. `scripts/backup.sh` makes an **encrypted**
snapshot and can ship it off-box.

```bash
# one-off, with an AES-256 passphrase you store somewhere safe (NOT on the server):
BACKUP_PASSPHRASE='a-long-random-passphrase' ./scripts/backup.sh

# nightly via cron (crontab -e) — optionally copy off-box with scp or rclone:
0 3 * * *  cd /home/dustin/personal-finance-tracker && \
  BACKUP_PASSPHRASE='...' BACKUP_SCP_DEST='user@backup-host:/srv/fts' ./scripts/backup.sh >> /var/log/fts-backup.log 2>&1
```
Snapshots land in `backups/` (gitignored), newest `BACKUP_KEEP` (default 14) kept.
Off-box options: `BACKUP_SCP_DEST=user@host:/path` and/or `BACKUP_RCLONE_DEST=remote:bucket/path`.

**Restore** (stop the app first):
```bash
docker compose down
BACKUP_PASSPHRASE='...' ./scripts/restore.sh backups/fts-backup-YYYYMMDD-HHMMSS.tar.gz.enc
docker compose up -d
```
Test a restore once now, while you still remember the passphrase — an untested
backup isn't a backup.

## Behind Cloudflare (recommended edge protection)
If you front the app with Cloudflare, it provides the DDoS protection, WAF, and
**edge rate-limiting** — so you do **not** need fail2ban or a Caddy `rate_limit`
module (behind a proxy those only ever see Cloudflare's IPs anyway). Do these
four things to make that protection real and non-bypassable:

1. **Proxy the record (orange cloud).** In Cloudflare DNS, the `finance` A/AAAA
   record must be **Proxied**, not "DNS only" (grey). Grey = traffic goes straight
   to your origin and you get **zero** Cloudflare protection. This is the make-or-break setting.
2. **SSL/TLS mode = Full (strict).** Cloudflare → origin stays encrypted and
   validated. Caddy's Let's Encrypt cert satisfies this and keeps auto-renewing
   through the proxy (ACME challenges still pass via Cloudflare).
3. **Lock the origin to Cloudflare's IPs** so nobody can skip Cloudflare by hitting
   your VPS IP directly. Docker-published ports bypass `ufw`, so use the provided
   iptables script (after `docker compose up`):
   ```bash
   sudo ./scripts/cloudflare-firewall.sh
   sudo apt install -y iptables-persistent && sudo netfilter-persistent save  # persist
   ```
4. **(Optional) Edge rate-limit on `/auth/*`.** In Cloudflare → Security → WAF →
   Rate limiting rules, add e.g. "≤ 20 requests/min/IP to `/auth/*`". This is the
   actual "edge rate-limiting", done at Cloudflare, not in the app.

The app already reads the true client IP from `CF-Connecting-IP` (for lockout +
login alerts), which is trustworthy precisely because of step 3.

> If your record is **DNS-only (grey)**, you have no edge protection — either flip
> it to Proxied (recommended), or ask for in-app/fail2ban rate-limiting instead.

## Security model (summary)
- **Passkey-only** login after setup (WebAuthn, user-verification required) —
  phishing-resistant, no shared secret to steal.
- Server-side sessions; the cookie holds a random token, the DB only its SHA-256.
  **Session ID is rotated on every login** and destroyed on logout.
- Cookies: HttpOnly, SameSite=Strict, Secure + `__Host-` prefix on HTTPS.
- argon2id password hashing; IP-based lockout after repeated failures.
- CSP / HSTS / nosniff / frame-ancestors-none headers on every response.
- Origin-checked state-changing requests (CSRF defence on top of SameSite).
- **Login alerts**: if e-mail is configured (`EMAIL_*`), you get a notification on
  every sign-in, new passkey enrolment, and account lockout — with the time, IP,
  and device. Best-effort and off the request path; if mail isn't set up it's
  silently skipped.
