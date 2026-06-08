# Linking your accounts

Do this **after** the app is deployed and you've logged in with your passkey
([DEPLOY.md](DEPLOY.md)). The dashboard runs without these; they just populate the
Overview / Spending / Portfolio tabs with your real data. Run the commands on the
**server** (they need the `keys/` and `.env` on the host).

---

## Trade Republic (portfolio + transactions)

Trade Republic has no official API, so the app uses the community `pytr` library
with **device pairing**: you pair once, a private keyfile is written, and every
later sync authenticates with that keyfile — your **PIN is never stored**.

1. Put your phone number in `.env`:
   ```
   TRADE_REPUBLIC_PHONE=+49XXXXXXXXXX
   ```
2. Pair the device (interactive — enter PIN + the 4-digit code TR sends to your app):
   ```bash
   docker compose run --rm app python -m backend.integrations.tr_setup
   ```
   This writes `keys/tr_keyfile.pem` (chmod 600, gitignored).
3. **Remove the PIN** — if you set `TRADE_REPUBLIC_PIN` anywhere, delete that line.
   Ongoing syncs use only the keyfile.
4. Restart so the scheduler picks it up:
   ```bash
   docker compose up -d
   ```
5. Within ~15 min the **Portfolio** tab and **Overview** net worth populate
   (portfolio syncs every 15 min, transactions hourly). To pull immediately,
   restart the app once more.

**Security notes**
- The keyfile is the credential — keep `keys/` private (`chmod 600`), it's gitignored.
- To revoke: reset paired devices in the Trade Republic app, and delete the keyfile.
- `pytr` is unofficial; syncs are throttled to ≤ 1 req/sec.

---

## Revolut (balances + spending) — optional

Uses the official **Salt Edge** Open Banking aggregator (free tier; read-only;
your bank login is never seen by this app).

1. Get free API credentials at <https://www.saltedge.com/clients/profile/secrets>
   and set in `.env`:
   ```
   SALTEDGE_APP_ID=...
   SALTEDGE_SECRET=...
   ```
2. Authorise Revolut (prints a URL — open it, pick Revolut, grant read-only consent):
   ```bash
   docker compose run --rm app python -m backend.integrations.revolut_setup
   ```
3. Add the two IDs it prints to `.env`:
   ```
   SALTEDGE_CUSTOMER_ID=...
   SALTEDGE_CONNECTION_ID=...
   ```
4. Restart: `docker compose up -d`. Balances + transactions sync hourly.

> Consent expires periodically (PSD2) — re-run step 2 when Revolut data stops
> updating.

---

## E-mail alerts — optional
Set `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_SMTP_PASSWORD` (Gmail App Password) in `.env`
to enable budget-limit alerts. Leave blank to disable.
