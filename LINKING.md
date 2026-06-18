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

## Trade Republic (transactions) — CSV import

Trade Republic has no official API, and as of 2025 it **actively blocks** the
community `pytr` library at its edge (the login endpoint returns a hard `405`,
confirmed from both datacenter and residential IPs — there's no token, browser
fingerprint, or header that gets past it). So this app does **not** try to log in
to Trade Republic. Instead you **import your own exported transactions** — no
scraping, nothing against TR's terms, and it keeps working no matter how TR
changes their app.

### Step 1 — export your transactions from Trade Republic

In the Trade Republic **app** or at **app.traderepublic.com**, open your
**Transactions** view and use **Export** to download a **CSV** (some versions also
offer Excel/JSON — CSV is best here). If your version has no export button, a
community PDF→CSV converter of your monthly statement also works.

### Step 2 — import it in the app

1. Open the app, sign in, go to **Settings → Broker → Trade Republic**.
2. Click **Choose file**, pick the CSV, and press **Import CSV**.
3. You'll see e.g. *"Imported 142 new transaction(s), skipped 0 duplicate(s)."*
   The **Overview** and **Spending** tabs update immediately.

The importer matches columns by meaning (it understands English and German
headers, `;` or `,` delimiters, and European number/date formats like `1.234,56`
and `01.05.2026`), and the database **dedupes** automatically — so you can
re-import the same file, or overlapping date ranges, and only genuinely new rows
are added. Outflows become spending (negative), inflows become income (positive).

> Tip: re-export and re-import whenever you want fresh data — it's safe to repeat.
> If the import reports rows it "couldn't read", send the file's header row and I
> can extend the column matching.

**Note on portfolio holdings:** this imports cash/transaction history. Live
position prices still come from the Markets data feed; current TR *holdings* are
not derived from the CSV yet.

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
