# Linking your accounts

Do this **after** the app is deployed and you've logged in with your passkey
([DEPLOY.md](DEPLOY.md)). The dashboard runs without these; they just populate the
Overview / Spending / Portfolio tabs with your real data.

Most of this is now driven from the in-app **Settings** page (top-right): it shows
each provider's link status and, for banks, gives you **Connect** buttons that run
the consent flow. Trade Republic still needs a one-time pairing command on the
**server** (it asks for a PIN + OTP and writes a keyfile), and the one-time bank
aggregator setup (below) is also a server step — both need the `keys/` and `.env`
on the host.

---

## Trade Republic (portfolio + transactions) — using `pytr`

Trade Republic has no official API, so the app uses the community `pytr` library
with **device pairing**: you pair once, a private keyfile is written, and every
later sync authenticates with that keyfile — your **PIN is never stored**.

There are exactly **two pieces of information you type**, and they go in different places:

| Info | Where it goes | Stored? |
|---|---|---|
| Your **phone number** (`+49…`) | the **`.env`** file (one line) | yes (it's not secret) |
| Your **PIN** + the **4-digit code** TR pushes to your app | typed at the **prompt** when you run the pairing command | **no** — never written to disk |

### Step 1 — put your phone number in `.env`

Open the file `.env` in the project root (`personal-finance-tracker/.env`) in any
text editor and find the line that starts with `TRADE_REPUBLIC_PHONE`. Set it to
your real number (international format, no spaces):

```ini
TRADE_REPUBLIC_PHONE=+491701234567
```

If there is a `TRADE_REPUBLIC_PIN=` line, **leave it commented out / blank** — you do
*not* put your PIN in the file. (`.env` is gitignored, so nothing here is committed.)

### Step 2 — run the one-time pairing (you type your PIN + the code here)

Run **one** of the following from the project root. It will prompt for your PIN, then
for the 4-digit code Trade Republic sends to your phone app:

```bash
# Local (running with a virtualenv, no Docker):
python -m backend.integrations.tr_setup

# Or, if you run the app with Docker:
docker compose run --rm app python -m backend.integrations.tr_setup
```

On success it writes `keys/tr_keyfile.pem` (the credential) and prints
`✅ Paired.` Nothing you typed at the prompt is saved.

### Step 3 — restart so the scheduler picks it up

```bash
# Local:
uvicorn backend.main:app --reload      # (or however you start it)
# Docker:
docker compose up -d
```

Within ~15 min the **Portfolio** tab and **Overview** net worth populate (portfolio
syncs every 15 min, transactions hourly; restart once more to pull immediately).
The **Settings** page will then show Trade Republic as **Linked**.

**Security notes**
- The keyfile is the credential — keep `keys/` private (`chmod 600 keys/tr_keyfile.pem`); it's gitignored.
- To revoke: reset paired devices in the Trade Republic app, and delete the keyfile.
- `pytr` is unofficial; syncs are throttled to ≤ 1 req/sec.

---

## Bank accounts — Enable Banking (recommended, free)

This is the path for **Revolut, Deutsche Bank, and ~2,700 other EEA banks**. It uses
[**Enable Banking**](https://enablebanking.com), a licensed PSD2 aggregator that is
**free for personal use**. Read-only; your bank login is never seen by this app
(you authenticate on the bank's own page). We chose it because GoCardless/Nordigen
closed to new sign-ups in 2025 and Salt Edge no longer has a free live tier.

**One-time setup (server):**

1. Register an application in the Enable Banking Control Panel (free for personal
   use). Give it a name, and set the **redirect URL** to:
   ```
   https://<your-domain>/settings/banks/callback
   ```
2. Generate/download the application's **RSA private key** and save it on the server
   as `keys/enablebanking_private.pem` (`chmod 600` — it's gitignored). Put the
   application id in `.env`:
   ```
   ENABLE_BANKING_APP_ID=...
   ENABLE_BANKING_KEYFILE=keys/enablebanking_private.pem
   ```
3. Restart: `docker compose up -d`.

**Linking a bank (in-app, repeatable):** open **Settings** → "Connect Revolut" /
"Connect Deutsche Bank" (or "Other bank" to search the full list) → you're sent to
the bank to approve read-only access → back to the dashboard. Balances +
transactions then sync hourly.

> The private key is the credential — keep `keys/` private. Consent expires
> periodically (PSD2, ~90 days); when a bank shows **Re-authorise** in Settings,
> click **Re-link**.

---

## Revolut via Salt Edge — legacy alternative

The original integration (`backend/integrations/revolut.py`) used the **Salt Edge**
aggregator. It still works if you already have Salt Edge credentials, but Salt Edge
no longer offers a free live tier, so prefer Enable Banking above.

1. Get credentials at <https://www.saltedge.com/clients/profile/secrets> and set
   `SALTEDGE_APP_ID` / `SALTEDGE_SECRET` in `.env`.
2. Authorise Revolut (prints a URL — open it, pick Revolut, grant read-only consent):
   ```bash
   docker compose run --rm app python -m backend.integrations.revolut_setup
   ```
3. Add the printed `SALTEDGE_CUSTOMER_ID` / `SALTEDGE_CONNECTION_ID` to `.env`.
4. Restart: `docker compose up -d`. Balances + transactions sync hourly.

> Consent expires periodically (PSD2) — re-run step 2 when Revolut data stops
> updating.

---

## E-mail alerts — optional
Set `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_SMTP_PASSWORD` (Gmail App Password) in `.env`
to enable budget-limit alerts. Leave blank to disable.
