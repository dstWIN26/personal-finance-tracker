import sqlite3
import os
import json
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "finance.db")


def _ensure_dirs():
    """Create the DB directory (and the credentials dir) if missing, so a fresh
    deploy doesn't fail just because ./data or ./keys wasn't pre-created."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    key_dir = os.path.dirname(os.getenv("TRADE_REPUBLIC_COOKIES_FILE", "keys/tr_cookies.txt"))
    if key_dir:
        os.makedirs(key_dir, exist_ok=True)


def init_db():
    _ensure_dirs()
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id            INTEGER PRIMARY KEY,
            isin          TEXT NOT NULL,
            name          TEXT NOT NULL,
            quantity      REAL NOT NULL,
            buy_price     REAL,
            current_price REAL,
            pl_pct        REAL,
            pl_eur        REAL,
            fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY,
            source      TEXT NOT NULL,
            date        DATE NOT NULL,
            description TEXT,
            category    TEXT,
            amount      REAL NOT NULL,
            currency    TEXT DEFAULT 'EUR',
            raw_json    TEXT,
            UNIQUE(source, date, description, amount, currency)
        );
        CREATE TABLE IF NOT EXISTS balances (
            id          INTEGER PRIMARY KEY,
            source      TEXT NOT NULL,
            balance     REAL NOT NULL,
            currency    TEXT DEFAULT 'EUR',
            fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS limits (
            id       INTEGER PRIMARY KEY,
            category TEXT UNIQUE,
            amount   REAL NOT NULL,
            period   TEXT DEFAULT 'monthly'
        );
        CREATE TABLE IF NOT EXISTS alerts_sent (
            id         INTEGER PRIMARY KEY,
            limit_id   INTEGER REFERENCES limits(id),
            sent_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            threshold  TEXT
        );

        -- ── Auth (single-user: passkey-primary, password bootstrap only) ──
        CREATE TABLE IF NOT EXISTS auth_state (
            id               INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
            password_hash    TEXT,                                -- argon2id; NULL once disabled
            password_enabled INTEGER NOT NULL DEFAULT 1,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS webauthn_credentials (
            id             INTEGER PRIMARY KEY,
            credential_id  TEXT UNIQUE NOT NULL,    -- base64url
            public_key     TEXT NOT NULL,           -- base64url COSE key
            sign_count     INTEGER NOT NULL DEFAULT 0,
            transports     TEXT,
            label          TEXT,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at   TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY,
            token_hash  TEXT UNIQUE NOT NULL,        -- sha256(session token)
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  TIMESTAMP NOT NULL,
            user_agent  TEXT,
            ip          TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_challenges (
            cid         TEXT PRIMARY KEY,            -- random pointer held in a temp cookie
            kind        TEXT NOT NULL,               -- 'register' | 'authenticate'
            challenge   TEXT NOT NULL,               -- base64url
            expires_at  TIMESTAMP NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            id    INTEGER PRIMARY KEY,
            ts    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip    TEXT,
            ok    INTEGER
        );

        -- ── Linked banks (Open Banking aggregator, e.g. Enable Banking) ──
        -- One row per authorised bank connection. We store only the opaque
        -- aggregator session id and account references — never bank credentials.
        CREATE TABLE IF NOT EXISTS bank_connections (
            id            INTEGER PRIMARY KEY,
            provider      TEXT NOT NULL,            -- aggregator, e.g. 'enable_banking'
            aspsp_name    TEXT,                     -- bank as shown by the aggregator
            aspsp_country TEXT,
            source_key    TEXT NOT NULL,            -- the `source` written into transactions/balances
            session_id    TEXT,                     -- opaque aggregator session id
            accounts_json TEXT,                     -- JSON list of authorised account refs
            status        TEXT NOT NULL DEFAULT 'active',  -- active | expired | error
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at    TIMESTAMP
        );
        -- Short-lived, single-use CSRF/in-flight token for the consent redirect.
        CREATE TABLE IF NOT EXISTS bank_link_state (
            state         TEXT PRIMARY KEY,
            provider      TEXT NOT NULL,
            aspsp_name    TEXT,
            aspsp_country TEXT,
            source_key    TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at    TIMESTAMP NOT NULL
        );
        -- Non-secret display/profile preferences (single-user key/value store).
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """)

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def upsert_position(isin, name, quantity, buy_price, current_price, pl_pct, pl_eur):
    with connect() as conn:
        conn.execute("""
            INSERT INTO positions (isin, name, quantity, buy_price, current_price, pl_pct, pl_eur)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [isin, name, quantity, buy_price, current_price, pl_pct, pl_eur])

def insert_transaction(source, date, description, category, amount, currency="EUR", raw_json=None):
    with connect() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO transactions
                (source, date, description, category, amount, currency, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [source, date, description, category, amount, currency, raw_json])

def upsert_balance(source, balance, currency="EUR"):
    with connect() as conn:
        conn.execute("""
            INSERT INTO balances (source, balance, currency) VALUES (?, ?, ?)
        """, [source, balance, currency])


# ── Linked bank connections ──────────────────────────────────────────────────

def insert_bank_connection(provider, aspsp_name, aspsp_country, source_key,
                           session_id, accounts, status="active", expires_at=None):
    """Persist an authorised bank connection. `accounts` is a JSON-serialisable
    list of account references; credentials are never stored."""
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO bank_connections
                (provider, aspsp_name, aspsp_country, source_key,
                 session_id, accounts_json, status, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [provider, aspsp_name, aspsp_country, source_key,
              session_id, json.dumps(accounts or []), status, expires_at])
        return cur.lastrowid


def list_bank_connections():
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bank_connections ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["accounts"] = json.loads(d.pop("accounts_json") or "[]")
        except (ValueError, TypeError):
            d["accounts"] = []
        out.append(d)
    return out


def delete_bank_connection(conn_id):
    with connect() as conn:
        conn.execute("DELETE FROM bank_connections WHERE id = ?", [conn_id])


def set_bank_connection_status(conn_id, status):
    with connect() as conn:
        conn.execute("UPDATE bank_connections SET status = ? WHERE id = ?",
                     [status, conn_id])


# ── Single-use consent-redirect state (CSRF protection) ──────────────────────

def create_link_state(state, provider, aspsp_name, aspsp_country, source_key, expires_at):
    with connect() as conn:
        conn.execute("""
            INSERT INTO bank_link_state
                (state, provider, aspsp_name, aspsp_country, source_key, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [state, provider, aspsp_name, aspsp_country, source_key, expires_at])


def consume_link_state(state):
    """Atomically fetch-and-delete a link state. Returns the row dict if it
    exists and has not expired, else None (also clears expired rows)."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM bank_link_state WHERE expires_at < CURRENT_TIMESTAMP")
        row = conn.execute(
            "SELECT * FROM bank_link_state WHERE state = ?", [state]).fetchone()
        if row is not None:
            conn.execute("DELETE FROM bank_link_state WHERE state = ?", [state])
    return dict(row) if row is not None else None


# ── Profile / display preferences ────────────────────────────────────────────

def get_setting(key, default=None):
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", [key]).fetchone()
    return row["value"] if row is not None else default


def get_all_settings():
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    with connect() as conn:
        conn.execute("""
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, [key, str(value)])
