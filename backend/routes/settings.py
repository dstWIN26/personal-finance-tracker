"""Settings & Profile endpoints (guarded — mounted behind require_session).

Two jobs:
  1. Report integration link status WITHOUT ever returning a secret or credential.
  2. Drive the bank-linking consent flow (Enable Banking) and store non-secret prefs.

Trade Republic has no usable automated API (TR blocks unofficial logins and it
risks an account ban), so TR is CSV-import-only: the user exports their transactions
from the TR app/web and uploads them here (see import_transactions). Imported rows
are stored with source="trade_republic" like any other, so the rest of the app
(overview, spending, portfolio) treats them uniformly.
"""
import os
import io
import csv
import re
import json
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

from backend import alerts
from backend.integrations import enable_banking
from backend.database import (
    connect,
    create_link_state,
    consume_link_state,
    insert_bank_connection,
    list_bank_connections,
    delete_bank_connection,
    get_all_settings,
    set_setting,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

# Consent-redirect state is single-use and short-lived.
_STATE_TTL_MIN = 15

# Profile keys we accept (anything else is ignored — no arbitrary writes).
_PROFILE_KEYS = {"display_name", "base_currency", "locale", "default_tab"}
_PROFILE_DEFAULTS = {
    "display_name": "",
    "base_currency": "EUR",
    "locale": "en-IE",
    "default_tab": "overview",
}


def _source_key(name: str) -> str:
    """Stable `source` slug for a bank (e.g. 'Deutsche Bank' → 'deutsche_bank')."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "bank"


def _mask_iban(iban: str | None) -> str | None:
    if not iban or len(iban) < 4:
        return None
    return "••••" + iban[-4:]


def _last_synced() -> dict:
    """Freshest data timestamp per kind, so Settings can show 'updated N ago'."""
    with connect() as conn:
        def m(sql):
            return conn.execute(sql).fetchone()[0]
        return {
            "positions": m("SELECT MAX(fetched_at) FROM positions"),
            "balances": m("SELECT MAX(fetched_at) FROM balances"),
            "transactions": m("SELECT MAX(date) FROM transactions"),
        }


# ── Integration status (no secrets) ──────────────────────────────────────────

@router.get("/integrations")
def integrations_status():
    banks = []
    for c in list_bank_connections():
        banks.append({
            "id": c["id"],
            "provider": c["provider"],
            "aspsp_name": c["aspsp_name"],
            "aspsp_country": c["aspsp_country"],
            "status": c["status"],
            "expires_at": c["expires_at"],
            "accounts": [
                {"name": a.get("name"), "iban": _mask_iban(a.get("iban")),
                 "currency": a.get("currency")}
                for a in c.get("accounts", [])
            ],
        })
    with connect() as conn:
        tr_txns = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE source = 'trade_republic'"
        ).fetchone()[0]
    return {
        "trade_republic": {
            "import_only": True,          # CSV import only — no automated API
            "transactions": tr_txns,
            "linked": tr_txns > 0,        # "linked" == some data has been imported
        },
        "enable_banking": {
            "configured": enable_banking.is_configured(),
            "redirect_url": enable_banking.redirect_url(),
        },
        "salt_edge_legacy": {
            "configured": bool(os.getenv("SALTEDGE_CONNECTION_ID")),
        },
        "banks": banks,
        "last_synced": _last_synced(),
    }


# ── Manual "Sync now" ────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_now():
    """Run every data sync immediately instead of waiting for the scheduler.

    Sources are synced one at a time (SQLite is single-writer) and each is time-
    boxed, so an unconfigured or slow provider can't hang the request. Providers
    that aren't set up degrade to a no-op inside their own sync function.

    Trade Republic is not included: it is CSV-import-only (no automated API), so
    its data arrives via POST /settings/import/transactions, not a sync job.
    """
    from backend.integrations.revolut import sync_transactions as sync_revolut
    from backend.integrations.enable_banking import sync_bank_connections as sync_banks

    jobs = {
        "revolut": sync_revolut,
        "banks": sync_banks,
    }
    results = {}
    for name, fn in jobs.items():
        try:
            await asyncio.wait_for(fn(), timeout=90)
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 — one bad source must not fail the rest
            logger.warning("manual sync '%s' failed: %s", name, type(exc).__name__)
            results[name] = "error"
    return {"results": results, "last_synced": _last_synced()}


# ── Data export (financial data only — never auth/secrets) ───────────────────

# A cell that begins with any of these can be executed as a formula when the CSV
# is opened in Excel / LibreOffice / Google Sheets (CSV "formula injection"), e.g.
# a transaction description of `=HYPERLINK(...)` or `@SUM(...)`. We neutralise such
# cells by prefixing a single quote so the spreadsheet treats them as plain text.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Defuse spreadsheet-formula cells. Only strings are guarded — numbers and
    None pass through unchanged, so negative amounts stay numeric (not quoted)."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_LEAD:
        return "'" + value
    return value


def _csv(columns: list[str], rows, filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow([_csv_safe(r[c]) for c in columns])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_LATEST_POSITIONS = (
    "SELECT isin, name, quantity, buy_price, current_price, pl_pct, pl_eur, fetched_at "
    "FROM positions WHERE id IN (SELECT MAX(id) FROM positions GROUP BY isin) ORDER BY name"
)


@router.get("/export/transactions.csv")
def export_transactions():
    with connect() as conn:
        rows = conn.execute(
            "SELECT source, date, description, category, amount, currency "
            "FROM transactions ORDER BY date DESC, id DESC"
        ).fetchall()
    return _csv(["source", "date", "description", "category", "amount", "currency"],
                rows, "transactions.csv")


@router.get("/export/positions.csv")
def export_positions():
    with connect() as conn:
        rows = conn.execute(_LATEST_POSITIONS).fetchall()
    return _csv(["isin", "name", "quantity", "buy_price", "current_price", "pl_pct", "pl_eur", "fetched_at"],
                rows, "portfolio.csv")


@router.get("/export/all.json")
def export_all():
    with connect() as conn:
        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "transactions": [dict(r) for r in conn.execute(
                "SELECT source, date, description, category, amount, currency FROM transactions ORDER BY date").fetchall()],
            "positions": [dict(r) for r in conn.execute(_LATEST_POSITIONS).fetchall()],
            "balances": [dict(r) for r in conn.execute(
                "SELECT source, balance, currency, fetched_at FROM balances ORDER BY fetched_at").fetchall()],
            "limits": [dict(r) for r in conn.execute(
                "SELECT category, amount, period FROM limits").fetchall()],
        }
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="finance-export.json"'})


# ── Import (Trade Republic / generic CSV → transactions) ─────────────────────
# Trade Republic actively blocks automated login, so instead of scraping we import
# the user's OWN exported transactions file (TR app/web → Transactions → Export, or
# a community PDF→CSV converter). Columns are matched by meaning rather than a fixed
# schema, because every exporter labels them slightly differently.

_MAX_IMPORT_BYTES = 8 * 1024 * 1024     # plenty for many years of transactions
_MAX_IMPORT_ROWS = 50_000

# Header aliases (lowercased) — English + German + common converter labels.
_COL_DATE = {"date", "datum", "timestamp", "time", "datetime", "value date", "valuedate",
             "booking date", "buchungstag", "completed date", "date completed", "valuta"}
_COL_AMOUNT = {"amount", "betrag", "value", "wert", "total", "amount (eur)", "betrag (eur)",
               "amount in eur", "net amount"}
_COL_IN = {"inflow", "inflows", "credit", "eingang", "money in", "deposit", "gutschrift"}
_COL_OUT = {"outflow", "outflows", "debit", "ausgang", "money out", "withdrawal", "belastung"}
_COL_DESC = {"description", "beschreibung", "title", "titel", "name", "note", "notes",
             "reference", "verwendungszweck", "details", "detail", "payee", "merchant", "subtitle"}
_COL_TYPE = {"type", "typ", "category", "kategorie", "transaction type", "art",
             "event type", "eventtype"}
_COL_CCY = {"currency", "währung", "waehrung", "ccy"}


def _pick(headers_lower: dict, aliases: set):
    """Return the original header whose lowercased form is in `aliases`, else None."""
    for low, orig in headers_lower.items():
        if low in aliases:
            return orig
    return None


def _to_amount(raw):
    """Parse a money string to float, tolerant of locale: '1.234,56', '1234.56',
    '-12,30', '€ 1.000,00', '1,000.00', '(12.30)' (negative). None if unparseable."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = re.sub(r"[^0-9,.\-]", "", s)        # strip currency symbols / spaces / NBSP
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s and "." in s:               # both present: the LAST separator is decimal
        if s.rfind(",") > s.rfind("."):     # European 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:                               # US 1,234.56
            s = s.replace(",", "")
    elif "," in s:                          # only commas
        if s.count(",") == 1 and len(s.split(",")[1]) in (1, 2):
            s = s.replace(",", ".")         # decimal comma
        else:
            s = s.replace(",", "")          # thousands
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _to_date(raw):
    """Parse common date formats to 'YYYY-MM-DD'. None if unparseable."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)          # ISO (optionally with time)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", s)   # D.M.Y / D/M/Y (EU: day-first)
    if m:
        d, mo, y = m.groups()
        y = "20" + y if len(y) == 2 else y
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            return None
    if re.fullmatch(r"\d{10}(\d{3})?", s):              # epoch seconds or millis
        ts = int(s)
        if len(s) == 13:
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    return None


@router.post("/import/transactions")
async def import_transactions(file: UploadFile = File(...)):
    """Import a Trade Republic (or generic) transactions CSV under
    source='trade_republic'. Columns are matched by meaning, locale number/date
    formats are handled, and the table's UNIQUE constraint dedupes — so importing
    the same file twice is safe (already-present rows are skipped). The file is
    parsed in memory, never written to disk, and its contents are never logged.
    """
    raw = await file.read()
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 8 MB).")
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The file is empty.")
    try:
        text = raw.decode("utf-8-sig")                  # also strips a UTF-8 BOM
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        first = (sample.splitlines() or [""])[0]
        delim = max(",;\t", key=first.count) if first else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Could not read a header row.")
    headers_lower = {(h or "").strip().lower(): h for h in reader.fieldnames}

    col = {k: _pick(headers_lower, a) for k, a in (
        ("date", _COL_DATE), ("amount", _COL_AMOUNT), ("inflow", _COL_IN),
        ("outflow", _COL_OUT), ("description", _COL_DESC), ("type", _COL_TYPE),
        ("currency", _COL_CCY))}
    detected = {**col, "delimiter": delim}

    if not col["date"] or not (col["amount"] or col["inflow"] or col["outflow"]):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Couldn't find a date and an amount column. Headers found: "
            + ", ".join(h for h in reader.fieldnames if h))

    imported = skipped = errors = total = negatives = 0
    with connect() as conn:
        for row in reader:
            total += 1
            if total > _MAX_IMPORT_ROWS:
                break
            try:
                date = _to_date(row.get(col["date"]))
                if col["amount"]:
                    amount = _to_amount(row.get(col["amount"]))
                else:
                    inflow = _to_amount(row.get(col["inflow"])) if col["inflow"] else 0.0
                    outflow = _to_amount(row.get(col["outflow"])) if col["outflow"] else 0.0
                    amount = (inflow or 0.0) - abs(outflow or 0.0)
                if date is None or amount is None:
                    errors += 1
                    continue
                description = ((str(row.get(col["description"]) or "").strip()
                               if col["description"] else "")[:300]) or "Trade Republic"
                category = (str(row.get(col["type"]) or "").strip() if col["type"] else "") or None
                ccy = row.get(col["currency"]) if col["currency"] else None
                currency = str(ccy).strip().upper()[:3] if ccy else "EUR"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO transactions "
                    "(source, date, description, category, amount, currency, raw_json) "
                    "VALUES ('trade_republic', ?, ?, ?, ?, ?, ?)",
                    [date, description, category, amount, currency, json.dumps(row)])
                if cur.rowcount:
                    imported += 1
                    if amount < 0:
                        negatives += 1
                else:
                    skipped += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the whole import
                errors += 1

    logger.info("TR CSV import: %d imported, %d duplicates, %d errors, %d rows",
                imported, skipped, errors, total)
    return {"imported": imported, "skipped": skipped, "errors": errors,
            "total": total, "spending_rows": negatives, "detected": detected}


# ── Email-alert preferences ──────────────────────────────────────────────────

@router.get("/alerts")
def get_alerts():
    saved = get_all_settings()
    return {
        "email_configured": alerts.email_configured(),
        "env_recipient": os.getenv("EMAIL_TO", ""),
        "security_enabled": saved.get("alerts_security_enabled", "1") != "0",
        "budget_enabled": saved.get("alerts_budget_enabled", "1") != "0",
        "email_to": saved.get("alerts_email_to", ""),
    }


@router.post("/alerts")
def save_alerts(prefs: dict):
    if "security_enabled" in prefs:
        set_setting("alerts_security_enabled", "1" if prefs["security_enabled"] else "0")
    if "budget_enabled" in prefs:
        set_setting("alerts_budget_enabled", "1" if prefs["budget_enabled"] else "0")
    if "email_to" in prefs:
        set_setting("alerts_email_to", str(prefs.get("email_to") or "").strip()[:200])
    return {"status": "ok"}


# ── Bank linking (Enable Banking consent flow) ───────────────────────────────

@router.get("/banks/aspsps")
async def banks_aspsps(country: str | None = None):
    if not enable_banking.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Enable Banking is not configured on the server yet.")
    try:
        return {"aspsps": await enable_banking.list_aspsps(country)}
    except Exception as exc:  # noqa: BLE001
        logger.error("Enable Banking aspsps failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Could not reach the bank directory.")


class ConnectIn(BaseModel):
    aspsp_name: str
    country: str


@router.post("/banks/connect")
async def banks_connect(body: ConnectIn):
    if not enable_banking.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Enable Banking is not configured on the server yet.")
    aspsp_name = body.aspsp_name.strip()
    country = body.country.strip().upper()
    if not aspsp_name or len(country) != 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid bank/country.")

    state = secrets.token_urlsafe(32)
    # Store in SQLite's own UTC timestamp format so `expires_at < CURRENT_TIMESTAMP`
    # compares correctly as a string (isoformat's 'T'/offset would sort wrong).
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MIN)).strftime(
        "%Y-%m-%d %H:%M:%S")
    create_link_state(state, enable_banking.PROVIDER, aspsp_name, country,
                       _source_key(aspsp_name), expires_at)
    try:
        url = await enable_banking.start_auth(aspsp_name, country, state)
    except Exception as exc:  # noqa: BLE001
        logger.error("Enable Banking start_auth failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Could not start bank authorisation.")
    return {"url": url}


class CompleteIn(BaseModel):
    code: str
    state: str


@router.post("/banks/complete")
async def banks_complete(body: CompleteIn):
    pending = consume_link_state(body.state)
    if pending is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Link request expired or invalid — please try again.")
    try:
        session_id, accounts, valid_until = await enable_banking.complete_session(body.code)
    except Exception as exc:  # noqa: BLE001
        logger.error("Enable Banking complete_session failed: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Could not finish linking the bank.")

    insert_bank_connection(
        provider=pending["provider"],
        aspsp_name=pending["aspsp_name"],
        aspsp_country=pending["aspsp_country"],
        source_key=pending["source_key"],
        session_id=session_id,
        accounts=accounts,
        status="active",
        expires_at=valid_until,
    )
    # Pull data immediately so the dashboard fills without waiting for the hourly job.
    asyncio.create_task(enable_banking.sync_bank_connections())
    return {"status": "ok", "accounts": len(accounts)}


@router.delete("/banks/{conn_id}")
def banks_unlink(conn_id: int):
    delete_bank_connection(conn_id)
    return {"status": "ok"}


# ── Profile / display preferences ────────────────────────────────────────────

@router.get("/profile")
def get_profile():
    saved = get_all_settings()
    return {k: saved.get(k, default) for k, default in _PROFILE_DEFAULTS.items()}


@router.post("/profile")
def save_profile(prefs: dict):
    for key, value in prefs.items():
        if key in _PROFILE_KEYS:
            set_setting(key, value)
    return {"status": "ok"}
