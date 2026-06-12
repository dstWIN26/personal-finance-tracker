# Codebase Review — Workstream B1 (read-only)

**Scope:** architecture, auth behaviour, latency, redundancy, and multi-tenant
readiness of the single-user app. **No code changed.** Findings carry file/line
refs; each is tagged for the B2 follow-up. Auth findings are **flagged, not fixed**
(guardrail #4).

**Method:** static read of the full backend + deploy config; the offline `pytest`
suite (27 tests) was run to confirm a green baseline. No load test was run — latency
findings are reasoned from the code and noted as such.

**Headline:** the app is clean and small; nothing is architecturally broken. The
highest-value B2 prep is a **`current_user()` seam** (multi-tenant), two **DB
indexes** + **WAL**, and removing a **per-request session write**. One real defect
found: a **hardcoded `localhost` link in budget e-mails** (alerts.py:143).

---

## 1. Architecture map (request flow)

```
Browser
  → Cloudflare (edge) → Caddy (TLS, proxy)           Caddyfile / docker-compose.yml
    → uvicorn (--proxy-headers)                       Dockerfile:19-20
      → FastAPI app                                   backend/main.py:45
        → SecurityHeadersMiddleware (every response)  backend/auth.py:290-317
        → router dispatch
            protected routers: dependencies=guard     main.py:56-61
              guard = Depends(require_session)         auth.py:278-286
                → _check_origin (CSRF)                 auth.py:265-271
                → _valid_session (cookie → DB)         auth.py:191-206
            public auth router (self-gates per route)  main.py:64, routes/auth.py
        → handler
            → connect() per query (SQLite)            database.py:105-113
            → market.* cache (TTL) for /market/*      integrations/market.py:64-79
        → JSON response
```

Supporting:
- **Startup:** `validate()` + `init_db()` run at **import time** (`main.py:22-23`), not in `lifespan`; the scheduler + cache-warm run in `lifespan` (`main.py:25-43`).
- **Scheduler (in-process, 6 jobs):** TR portfolio 15m, TR txns 1h, Revolut 1h, limit-check 1h, market quotes 60s, bonds 30m (`main.py:28-34`).
- **Static/SPA:** `/static` mount, `/` and `/login` serve files; `/` redirects to `/login` when unauthenticated (`main.py:66-92`).

**Surprises worth noting (not bugs):**
- **S1 — every authenticated GET performs a DB write.** `_valid_session` does a sliding-expiry `UPDATE sessions SET last_seen…, expires_at…` on each call (`auth.py:202-205`). A read request mutates state and takes a write lock. → latency item L4, B2 candidate.
- **S2 — connection-per-query.** `connect()` opens and closes a fresh `sqlite3` connection for every query (`database.py:105-113`); no pooling, no WAL. A single request opens several (guard + handler). → latency item L5.
- **S3 — import-time side effects.** `init_db()`/`validate()` at import make the module non-trivial to import in tooling (the test suite works around this by building its own app in `conftest.py`).

---

## 2. Auth review (claims vs. behaviour)

Verified against what `README.md` advertises. **All core claims hold.** Auth code is
sacred — findings below are flagged for a careful, separately-reviewed change, not
touched in B1/B2 without explicit sign-off.

| Claim | Verdict | Evidence |
|---|---|---|
| Server-side sessions; only `sha256(token)` stored | ✅ | `auth.py:45-46,140-145`; `sessions.token_hash` `database.py:82-90` |
| Session rotated on every login | ✅ | `create_session(old_token)` destroys old `auth.py:132-135`; called on both logins `routes/auth.py:79,132` |
| Cookies `__Host-`/HttpOnly/SameSite=Strict/Secure | ✅ | `auth.py:245-250`; name/secure derivation `config.py:55-58` |
| Passkey-only after enrolment; password disabled | ✅ | `routes/auth.py:188` disables password on first passkey enrol |
| IP lockout on bootstrap password | ✅ | `auth.py:88-100`; 429 in `routes/auth.py:64-66`; tested |
| Origin/CSRF check on state change | ✅ (with note) | `_check_origin` `auth.py:265-271` |
| CSP/HSTS/nosniff/frame-DENY/referrer/permissions | ✅ | `auth.py:298-317` |

**Findings (flag only):**
- **F-A1 (perf/correctness):** sliding-expiry write on every request (`auth.py:202-205`) — see S1. Also means sessions have **no absolute max lifetime**; an active session refreshes forever. Consider an absolute cap separate from idle TTL.
- **F-A2 (CSRF depth):** `_check_origin` only rejects when an `Origin` header is **present and mismatched**; a request with **no `Origin`** passes (`auth.py:269-270`). Acceptable because cookies are `SameSite=Strict` + `__Host-`, but there's no `Referer` fallback. Document the reliance on SameSite.
- **F-A3 (observability):** `login_attempts` is written but never pruned; grows unbounded (minor; same family as the cleanup that *does* exist for `auth_challenges`, `auth.py:108`).

---

## 3. Latency review

Reasoned statically (no profiler run). Ordered by likely impact as data grows.

| ID | Path | Finding | B2 action |
|----|------|---------|-----------|
| **L1** | `/portfolio`, `/overview`, weekly digest | "latest snapshot per ISIN" does `INNER JOIN (SELECT isin, MAX(fetched_at) … GROUP BY isin)` over `positions`, which **keeps every historical snapshot** and has **no index** (`portfolio.py:9-16,25-31`, `overview.py:15-20`, `alerts.py:200-207`). Full scan + group-by on every call; degrades as history accrues. | Add index `positions(isin, fetched_at)`. |
| **L2** | `/spending*`, `/limits/status`, alerts | Filters use `strftime('%Y-%m', date) = ?` (`spending.py:62,76,91`, `limits.py:48,54`, `alerts.py:83,89`) — **non-sargable**, forces a scan; `transactions.date` has no standalone index. | Add index `transactions(date)` (and/or store month); avoid `strftime` on the column. |
| **L3** | external market data | On a **cache miss/cold cache**, the request triggers the upstream Yahoo/Treasury/ECB fetch on the request path (`market.py:64-79`). Mitigated by 60s/30m scheduler warming + warm-on-startup (`main.py:40-41`) and per-key locks, so steady-state requests hit cache. | Fine as-is; note the cold-start window. |
| **L4** | every authenticated request | Per-request session `UPDATE` (S1, `auth.py:204`) → write lock per request. | Throttle the `last_seen` write (e.g. only if >N min stale). *(auth code → careful.)* |
| **L5** | all DB access | New connection per query, no WAL (S2, `database.py:105-113`). | Enable `PRAGMA journal_mode=WAL` + `busy_timeout`; consider a per-request connection. |
| L6 | `/limits/status`, alerts | One query per limit in a loop (N+1, `limits.py:42-55`, `alerts.py:78-101`). Negligible at realistic N; listed for completeness. | Leave unless N grows. |

---

## 4. Redundancy audit

**Confirmed safe to remove — none.** Everything flagged below is either intentionally
dormant or a defect to fix, not dead code. Per guardrail, anything uncertain is
listed as a *candidate needing confirmation*, not slated for deletion.

| ID | Item | Classification |
|----|------|----------------|
| R1 | `send_daily_summary` / `send_weekly_portfolio` (`alerts.py:149,197`) imported (`main.py:15`) but their scheduler jobs are **commented out** (`main.py:36-37`). | **Dormant-by-design** (documented optional digests). **Do not delete.** |
| R2 | `CronTrigger` import (`main.py:9`) is only used by those commented-out jobs → currently unused import. | **Candidate (trivial):** remove or uncomment the digests. Confirm intent first. |
| R3 | "latest snapshot per ISIN" SQL is **duplicated 4×** (`portfolio.py` ×2, `overview.py`, `alerts.py`). | **Duplication, not dead.** B2: extract one helper (pairs well with L1's index). |
| R4 | `_send_limit_alert` budget e-mail hardcodes `http://localhost:8000` as the dashboard link (`alerts.py:143`). | **Defect (not redundancy):** wrong link in prod. Should use `config.RP_ORIGIN` like `send_security_alert` does (`alerts.py:60`). B2/quick-fix. |
| R5 | `frontend/js/app.js` (470 lines) not line-audited here. | **Candidate:** out of B1's backend scope; review during Workstream A. |

---

## 5. Multi-tenant gap analysis (future checklist)

Every place that assumes a single user. This list seeds `MULTI_TENANT_TODO.md` (B2).
**B2 builds only the seam — it does not enable any of this.**

| # | Single-user assumption | Location |
|---|---|---|
| M1 | One identity, hardcoded | `USER_ID = b"finance-tracker-owner"` `routes/auth.py:34` |
| M2 | One credential set for the whole app | `auth_state` is a singleton `CHECK (id = 1)` `database.py:66-71` |
| M3 | Passkeys/sessions have **no `user_id`** | `webauthn_credentials`, `sessions` `database.py:72-90` |
| M4 | Financial data is **global, unscoped** | `positions`, `transactions`, `balances`, `limits`, `alerts_sent` `database.py:23-63` — no `user_id` column; every query is account-wide |
| M5 | One brokerage | single `TRADE_REPUBLIC_KEYFILE` + `TRADE_REPUBLIC_PHONE` `integrations/trade_republic.py:11,26` |
| M6 | One bank connection | single `SALTEDGE_CONNECTION_ID` / `CUSTOMER_ID` `integrations/revolut.py:21`, `revolut_setup.py` |
| M7 | One alert recipient | single `EMAIL_TO` `alerts.py:16` |
| M8 | Scheduler syncs **the** account | jobs take no user arg `main.py:28-34` |
| M9 | Lockout keyed on IP only | `login_attempts` has no user `database.py:97-102` |

**The single highest-value B2 seam (per plan):** introduce a `current_user()` source
of truth, hard-wired to the one owner today, that **every DB query routes through** —
so future multi-tenancy is "make `current_user()` real + add `WHERE user_id=?`"
rather than rewriting every query. It must be a **provable no-op** for the current
user (M1–M9 unchanged in behaviour).

---

## Recommended B2 order (after this review is reviewed)

1. **`current_user()` seam** (M-series) — provable no-op; the most valuable prep. *(own branch)*
2. **DB indexes + WAL** (L1, L2, L5) — clear, measurable win, low risk.
3. **Extract the "latest per ISIN" helper** (R3) — removes duplication; do alongside #2.
4. **Fix the localhost e-mail link** (R4) — small correctness fix.
5. **Throttle the per-request session write** (L4/F-A1) — *auth code; isolated, justified, fully tested, separate sign-off.*
6. Tidy `CronTrigger`/digest intent (R2) — confirm with the human first.

> Nothing here was changed. Deletions in B2 must each trace back to a *confirmed*
> finding above; auth changes need their own reviewed PR.
