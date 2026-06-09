# Multi-User Migration Plan

> **Purpose.** Reference for converting this **single-user** personal finance
> tracker into a **multi-tenant** app (target: ~5 trusted users, designed to scale
> further). Read this before touching auth, the schema, or the integrations.
>
> **Status:** PLAN ONLY — not yet implemented. The app today is single-user.
>
> **Golden rule for the whole migration:** every row of financial data belongs to
> exactly one `user_id`, and **no query may return a row the requesting user
> doesn't own.** A single missing `WHERE user_id = ?` leaks one person's finances
> to another. Treat tenant isolation as the #1 correctness *and* security concern.

---

## 1. Current single-user assumptions (what must change)

Verified against the code — these are the hard-coded "one owner" points:

| Where | Assumption | File |
|---|---|---|
| `auth_state` table | singleton row, `CHECK (id = 1)` | `backend/database.py` |
| WebAuthn identity | `USER_ID = b"finance-tracker-owner"`, `USER_NAME = "owner"` | `backend/routes/auth.py` |
| `webauthn_credentials`, `sessions` | no `user_id` column | `backend/database.py` |
| `positions`, `transactions`, `balances`, `limits`, `alerts_sent` | no `user_id` column | `backend/database.py` |
| Trade Republic creds | one shared keyfile + phone from `.env` | `backend/integrations/trade_republic.py` |
| Revolut/Salt Edge creds | one shared `APP_ID`/`SECRET`/`CONNECTION_ID` from `.env` | `backend/integrations/revolut.py` |
| Scheduler | global jobs sync "the" account, not per-user | `backend/main.py` |
| Datastore | SQLite, single writer | `backend/database.py` |
| Routes | `Depends(require_session)` proves *authenticated*, not *which user* | `backend/main.py`, all routers |

**Conclusion:** this is a new architecture, not a feature add. Budget accordingly.

---

## 2. Target architecture

```
Browser ──HTTPS(Caddy)──> FastAPI
                            │  session cookie → user_id (every request)
                            ├── routes scoped by user_id  ───────────┐
                            ├── per-user scheduler jobs               │
                            ▼                                         ▼
                       Postgres (row-per-user)            Encrypted per-user secrets
                       all tables carry user_id           (TR keyfile, Salt Edge tokens)
```

Key shifts: **SQLite → Postgres**, **global creds → per-user encrypted creds**,
**global scheduler → per-user jobs**, **`require_session` → `current_user`**.

---

## 3. Data model

### 3.1 New `users` table
```sql
CREATE TABLE users (
    id            TEXT PRIMARY KEY,          -- uuid4
    email         TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'member',  -- 'admin' | 'member'
    status        TEXT NOT NULL DEFAULT 'active',  -- 'invited' | 'active' | 'disabled'
    created_at    TIMESTAMPTZ DEFAULT now()
);
```

### 3.2 Add `user_id` to every owned table
`positions, transactions, balances, limits, alerts_sent, webauthn_credentials,
sessions, login_attempts, auth_challenges` → add
`user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE` and index it.
Replace the singleton `auth_state` with **per-user** credential state (see §4).

### 3.3 Per-user integration credentials (encrypted at rest)
```sql
CREATE TABLE user_integrations (
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,             -- 'trade_republic' | 'revolut'
    secret_enc   BYTEA NOT NULL,            -- AES-GCM ciphertext (TR keyfile / Salt Edge tokens)
    meta_json    TEXT,                      -- non-secret: phone (masked), connection_id, status
    updated_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, provider)
);
```
- Encrypt with a server-held master key (`SECRETS_MASTER_KEY` env, 32 bytes) using
  AES-256-GCM (use the `cryptography` library — add to `requirements.txt`).
- **TR keyfile stops being a file on disk** → it becomes `secret_enc`, decrypted
  into a temp/in-memory keyfile only for the duration of a sync.
- Never log or return these; never put them in `.env` once multi-user.

### 3.4 Migration of existing single-user data
Write a one-shot script: create the first `users` row (the current owner, `admin`),
then `UPDATE ... SET user_id = <owner>` on every existing table; move the existing
`auth_state`/`keys/tr_keyfile.pem`/Salt Edge `.env` values into `user_integrations`.

---

## 4. Auth & identity

1. **Registration / invite flow.** Admin invites by email → `users` row `status='invited'`
   + a single-use, expiring invite token → invitee opens link, enrols a passkey,
   `status='active'`. No open self-signup (keeps it to ~5 trusted people).
   - The current **bootstrap-password** path becomes the **invite-acceptance** path
     (or drop passwords entirely and accept the invite token as the enrolment proof).
2. **Per-user WebAuthn.** `USER_ID`/`USER_NAME` (`routes/auth.py`) become the user's
   real `id`/`email`. `webauthn_credentials` rows carry `user_id`.
   - **Usernameless login still works**: look up the credential by its `credential_id`,
     which maps to a `user_id` — so the passkey itself selects the tenant.
3. **Sessions carry identity.** `sessions.user_id` is set at login. Add a
   `current_user(request) -> User` dependency that resolves the cookie → session →
   user, replacing bare `require_session`. **All routers depend on `current_user`.**
4. **Authorization.** `member` sees only their own data; `admin` can manage users
   (invite/disable/delete) but **not** view others' financial data unless explicitly
   designed to. Add an `admin_only` dependency for the user-management endpoints.
5. **Lockout & challenges** become per-user/per-IP (`login_attempts`, `auth_challenges`
   gain `user_id` where applicable).

---

## 5. Tenant isolation (the critical part)

- **Single choke point:** add a `db.for_user(user_id)` helper or a thin repository
  layer so queries can't be written without a `user_id`. Prefer this over relying on
  every author remembering `WHERE user_id = ?`.
- **Defense in depth:** if staying on Postgres, consider **Row-Level Security (RLS)**
  policies keyed on a per-request `SET app.user_id` — the DB refuses cross-tenant rows
  even if app code forgets.
- **Every existing query in `routes/*.py` and `integrations/*.py` must be audited**
  and scoped. This is the bulk of the review effort.
- **Tests:** for every endpoint, add a "user A cannot read/modify user B's data"
  test (expect 404/403). Make these mandatory in CI.

---

## 6. Integrations, per user

- **Trade Republic (`pytr`).** Each user pairs their own device (`tr_setup` becomes a
  logged-in, per-user flow that writes encrypted creds to `user_integrations`, not a
  shared keyfile). **Scale risk:** `pytr` is unofficial and rate-limited; 5 users ×
  polling must stay well under limits — stagger jobs, keep ≥5-min intervals, throttle
  to ≤1 req/s per user. Accept that pytr breakage affects all users at once.
- **Revolut (Salt Edge).** Salt Edge models each end-user as a **customer**; create one
  Salt Edge customer per app user, store that user's `customer_id`/`connection_id` in
  `user_integrations`. Consent + re-consent flows are per user.
- **Liability:** you are now custodian of 5 people's bank/broker access. Document
  consent, data retention, and a clean **account-deletion** path (revoke Salt Edge
  consent, delete TR creds, cascade-delete rows). This has **GDPR implications** —
  treat it seriously before onboarding anyone but yourself.

---

## 7. Datastore: SQLite → Postgres

- SQLite is one-writer; concurrent users + the scheduler will hit write-lock
  contention. Move to **Postgres** (or keep SQLite only for a 2–3 person hobby
  deployment with WAL mode + accepting the risk).
- Keep the `connect()` context-manager seam in `database.py` so call sites barely
  change; swap the driver (`psycopg`/SQLAlchemy) behind it. Add `DATABASE_URL` env.
- Add real **migrations** (Alembic) — `CREATE TABLE IF NOT EXISTS` won't carry you
  through schema evolution with live user data.
- Update `docker-compose.yml` to add a `postgres` service + volume; update `DEPLOY.md`.

---

## 8. Scheduler

- Replace the four global jobs in `main.py` with **per-user** jobs (loop over active
  users, or one job that iterates users internally). Market-data warming stays global
  (it's user-independent — keep it shared).
- Guard against one user's failing sync (bad TR creds) breaking others — isolate
  per-user `try/except`, surface status in `user_integrations.meta_json`.

---

## 9. Security additions required for multi-user

| Item | Why |
|---|---|
| **Edge rate-limiting / WAF** (Cloudflare, or Caddy `rate_limit` + fail2ban) | Public, multi-user surface invites abuse. Still pending from single-user plan. |
| **Per-user + per-IP rate limits** on auth + API | One user can't exhaust/abuse shared resources. |
| **Secrets manager / KMS** for `SECRETS_MASTER_KEY` | Don't keep the master key that decrypts everyone's bank creds in plaintext `.env`. |
| **Audit log** (login, enrol, cred change, data export, admin actions) | Forensics + accountability across users. |
| **CSP without `unsafe-eval`** (or accept risk) | Multi-user raises XSS stakes; consider Alpine CSP build. |
| **CI secret-scan + tenant-isolation tests** | Public repo + cross-tenant risk. Pending from single-user plan. |
| **Encrypted backups, per-user recoverable** | `scripts/backup.sh` exists but is whole-DB; ensure per-user export/delete for GDPR. |

---

## 10. Phased delivery (suggested order)

1. **Phase 0 — Postgres + migrations.** Swap datastore behind `connect()`, add Alembic,
   no behaviour change. Ship, verify single-user still works on Postgres.
2. **Phase 1 — users table + identity.** Add `users`, `user_id` columns (nullable →
   backfill owner → NOT NULL), `current_user` dependency, scope **every** query.
   Add the cross-tenant test matrix. **This is the big, risky phase.**
3. **Phase 2 — per-user auth.** Invite flow, per-user WebAuthn, admin role, sessions
   carry `user_id`.
4. **Phase 3 — per-user integrations.** `user_integrations` (encrypted), per-user
   TR/Revolut setup flows, per-user scheduler jobs.
5. **Phase 4 — hardening & ops.** Rate limiting/WAF, audit log, secrets manager,
   per-user backup/export/delete, GDPR docs.

Each phase ships independently and keeps the app working for the existing owner.

---

## 11. Concrete file-change checklist

- `backend/database.py` — `users`, `user_integrations`, `user_id` on all tables,
  drop `auth_state` singleton; move to Alembic migrations; Postgres driver.
- `backend/auth.py` — `current_user`/`admin_only` deps; sessions/credentials/lockout
  keyed by `user_id`; AES-GCM secret helpers.
- `backend/routes/auth.py` — real `user_id`/`email` in WebAuthn; invite + admin
  user-management endpoints; per-user session list.
- `backend/routes/{overview,spending,portfolio,limits,market}.py` — scope every query
  by `current_user`.
- `backend/integrations/{trade_republic,revolut}.py` + `*_setup.py` — per-user creds
  from `user_integrations`, encrypted; no shared `.env` creds.
- `backend/main.py` — per-user scheduler jobs; `current_user` wiring.
- `backend/config.py` — `DATABASE_URL`, `SECRETS_MASTER_KEY`; drop single-user env.
- `frontend/` — invite/accept page, admin user-management UI, per-user integration
  linking UI.
- `docker-compose.yml` / `DEPLOY.md` / `LINKING.md` — Postgres service, per-user setup.
- `tests/` — cross-tenant isolation matrix (mandatory), per-user integration tests.

---

## 12. Risks & honest caveats

- **Tenant data leakage** is the dominant risk — over-invest in §5 (isolation) and its tests.
- **You become a custodian of others' bank/broker credentials** → real legal/GDPR
  liability. Get comfortable with that (and `pytr`'s unofficial status) before
  onboarding anyone beyond yourself.
- **`pytr` at 5× scale** can break for everyone simultaneously; it's the weakest link.
- This plan is written from the current architecture; **re-verify each table/route
  before implementing a phase** — the code will have moved on.
```
