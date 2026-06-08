import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "finance.db")

def init_db():
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
