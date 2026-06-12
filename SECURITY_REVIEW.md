# Security Standards Review — Workstream C1

**Scope:** read-only audit of the deployed single-user app against a practical
security baseline for a personal finance app. **No code was changed.** Secrets were
not read or printed (only public placeholders already in `.env.example`).

**Method:** static read of `backend/`, `frontend/`, deploy config, and CI; full
`git`-history secret scan (object list + pickaxe). Dependency findings are from
public advisories for the pinned versions — no scanner was run in this environment
(see [Dependency check](#3-dependency-check) for the recommended follow-up).

**Verdict:** **0 FAIL · 7 PASS categories with caveats · 9 NEEDS-ATTENTION items.**
No security control is broken or missing; the open items are hardening and
dependency-freshness, none of them blockers. Highest-priority items: **D1** (stale
deps with multipart-DoS CVEs), **R2 / T7** (lockout integrity depends on the origin
firewall + permissive `forwarded-allow-ips`).

Legend: **PASS** = verified correct · **NEEDS-ATTENTION** = works but should be
improved/confirmed · **FAIL** = broken or missing.

---

## 1. Transport & headers

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| T1 | HTTPS-only + auto HTTP→HTTPS | **PASS** | `Caddyfile:8-11`, `docker-compose.yml:22-25` | Caddy issues/renews Let's Encrypt and auto-redirects HTTP→HTTPS for a named site. App port is never published (see T6). |
| T2 | HSTS | **PASS** | `backend/auth.py:315-316` | `max-age=63072000; includeSubDomains; preload`, only when `COOKIE_SECURE` (https). Note: `preload` is asserted but only takes effect if you submit the domain to hstspreload.org — optional. |
| T3 | CSP present & restrictive | **NEEDS-ATTENTION** | `backend/auth.py:298-306,310` | Good baseline (`default-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'self'`, `connect-src 'self'`). But `script-src` allows `'unsafe-eval'` (Alpine) and `style-src` allows `'unsafe-inline'`. Documented trade-off for a single-user, no-UGC app; acceptable but weakens XSS defence. Also missing explicit `object-src 'none'` (falls back to `default-src 'self'`, not `'none'`). |
| T4 | `X-Content-Type-Options: nosniff` | **PASS** | `backend/auth.py:311` | |
| T5 | Frame-ancestors none / clickjacking | **PASS** | `backend/auth.py:305,312` | Both CSP `frame-ancestors 'none'` and `X-Frame-Options: DENY`. Also `Referrer-Policy: no-referrer` and a restrictive `Permissions-Policy` (`auth.py:313-314`). |
| T6 | App not directly exposed | **PASS** | `docker-compose.yml:11-12` | App uses `expose` (internal network only), never `ports`. Only Caddy publishes 80/443/443udp. |
| T7 | Reverse-proxy header trust | **NEEDS-ATTENTION** | `Dockerfile:19-20` | uvicorn runs with `--forwarded-allow-ips "*"`, i.e. it trusts `X-Forwarded-*` from any upstream. Mitigated because the app port is unpublished (T6) so only Caddy can reach it — but if that ever changes, client-IP (and thus lockout, R2) becomes spoofable. Prefer scoping to the Docker/Caddy network range. |

**Cookies (verified PASS):** session + challenge cookies are `HttpOnly`,
`SameSite=Strict`, `Secure` on https, with the `__Host-` prefix on https —
`backend/auth.py:245-250` (session), `257-261` (challenge); flag/name derivation in
`backend/config.py:55-58`.

---

## 2. Secrets hygiene

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| S1 | `.gitignore` covers secrets | **PASS** | `.gitignore:2-19` | Covers `.env`, `.env.*` (with `!.env.example`), `*.pem`, `*.key`, `keys/`, `secrets/`, `*.db`/`*.sqlite*`, `data/`, `backups/`, `*.tar.gz.enc`, `*.save`. |
| S2 | CI secret scan active | **PASS** | `.github/workflows/ci.yml:26-39` | `secret-scan` job runs gitleaks v8.18.4 with `fetch-depth: 0` (full history) on every push + PR. |
| S3 | No secret ever committed (history) | **PASS** | git history scan (below) | No real `.env`, `*.pem`, `*.db`, `keys/`, or `data/` object exists in any commit. |
| S4 | Placeholder values in `.env.example` look secret-ish | **NEEDS-ATTENTION (informational)** | history commit `fc316c3`; current `.env.example` | Historic `.env.example` had `TRADE_REPUBLIC_PIN=1234` and `EMAIL_SMTP_PASSWORD=gmail-app-password` as **placeholders** (not real). Current file already softened these to a commented PIN and `xxxx-xxxx-xxxx-xxxx`. No action required, but gitleaks *could* flag `=1234` patterns; confirm the CI scan stays green. |

**Git-history secret scan (report only — no fix attempted):**
- Files ever *added* with secret-ish names: only `.env.example` and `scripts/backup.sh` — **no real secret file**.
- Object list (`git rev-list --all --objects`) for `.env`/`.pem`/`.key`/`*.db`/`keys/`/`data/`/`.save`: **none** (excluding `.env.example`).
- Pickaxe for PEM private keys, `SALTEDGE_SECRET=`, `EMAIL_SMTP_PASSWORD=`, `TRADE_REPUBLIC_PIN=` real values: only the **placeholder** hits in `.env.example` above.
- **Conclusion:** no credential has ever been committed. ✅

---

## 3. Dependency check

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| D1 | Multipart DoS CVEs in pinned deps | **NEEDS-ATTENTION** | `requirements.txt:7` (`python-multipart==0.0.9`), transitive `starlette 0.37.x` via `requirements.txt:1` (`fastapi==0.111.0`) | `python-multipart < 0.0.18` → **CVE-2024-53981** (DoS). `starlette < 0.40.0` → **CVE-2024-47874** (multipart DoS). Both are denial-of-service, not RCE, and the app exposes **no multipart/form endpoint** (auth uses JSON), so real-world exploitability is low — but they are known-vulnerable pins. Recommend bumping `python-multipart>=0.0.18` and `fastapi>=0.115` (pulls `starlette>=0.40`) on a dedicated branch with the test suite. **Do not auto-bump without review** (per guardrail #7). |
| D2 | `pytr` is unofficial + pinned | **PASS (with caveat)** | `requirements.txt:5` (`pytr==0.4.1`) | Version is pinned (good). It is an **unofficial** Trade Republic library — flag in any PR touching it and in `LINKING.md` (already noted in the plan's C3 caveat). |
| D3 | Other pins current enough | **PASS** | `requirements.txt` | `webauthn==2.7.1`, `argon2-cffi==25.1.0`, `httpx==0.27.0`, `apscheduler==3.10.4`, `uvicorn==0.29.0`, `python-dotenv==1.0.1` — no advisory known for these at the pinned versions. |
| D4 | No automated dependency monitoring | **NEEDS-ATTENTION** | (absent) | CI scans for *secrets* but not *vulnerable deps*. Recommend adding `pip-audit` to CI and/or enabling Dependabot so D1-type findings surface automatically. |

> The dependency findings are version-based (public advisories). For an authoritative,
> ongoing answer, run `pip-audit -r requirements.txt` — flagged as the D4 follow-up.

---

## 4. Input validation

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| I1 | SQL is parameterised everywhere | **PASS** | `backend/database.py:115-134`, `routes/spending.py`, `routes/limits.py`, `routes/portfolio.py`, `routes/overview.py`, `routes/auth.py`, `backend/auth.py`, `backend/alerts.py` | Every `execute()` uses `?` placeholders with a params list. No user value is string-formatted into SQL. |
| I2 | Dynamic `WHERE` builder | **PASS (note)** | `backend/routes/spending.py:18-51` | The `f"...WHERE {where}..."` interpolates only **hard-coded filter fragments** (`"date >= ?"` etc.); all user values go through bound params. Safe as written. Keep it that way — any future user-derived column/operator must not be interpolated. |
| I3 | SSRF guard on market params | **PASS** | `backend/routes/market.py:15-19,39,58` | `symbol`/`symbols` are filtered against a fixed `_ALLOWED` whitelist before reaching the upstream Yahoo client; `range` is whitelisted via `_RANGE_INTERVAL` (`market.py:21-24,60`). |
| I4 | Type validation on inputs | **PASS** | FastAPI signatures + `routes/limits.py:9-12` (Pydantic `LimitIn`) | Query/body params are typed (`Optional[str]`, `float`, `int`); FastAPI rejects mistyped input with 422. |
| I5 | Unbounded `LIMIT` parameter | **NEEDS-ATTENTION (minor)** | `backend/routes/spending.py:69,79-80` | `top-merchants?limit=` is an unbounded `int` passed straight to SQL `LIMIT ?`. Behind auth, so low risk, but cap it (e.g. `min(limit, 100)`) to avoid a large-result self-DoS. |

---

## 5. Rate limiting / lockout

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| R1 | Bootstrap-password lockout works | **PASS** | `backend/auth.py:88-100`, `routes/auth.py:64-77`; thresholds `config.py:61-62` | After `MAX_PW_FAILURES` (5) failures within `LOCKOUT_MINUTES` (15) per IP, `/auth/password` returns 429. Verified by existing tests (`tests/test_auth.py:75-91`). One alert e-mail fires at the transition only. |
| R2 | Lockout integrity vs. IP spoofing | **NEEDS-ATTENTION** | `backend/auth.py:209-221`, `scripts/cloudflare-firewall.sh` | Lockout keys on `_client_ip`, which trusts `CF-Connecting-IP` then `X-Forwarded-For`. This is only sound if the origin is firewalled to Cloudflare's ranges (so a direct-to-origin attacker can't spoof the header). **Confirm `scripts/cloudflare-firewall.sh` is applied in production** and the DNS record is Proxied. Code comment already documents this dependency. |
| R3 | Edge rate-limiting on `/auth/*` documented | **PASS (manual step)** | `DEPLOY.md:100-127` | The external rate-limit layer is documented as a Cloudflare WAF rule on `/auth/*`. It is a **manual dashboard step**, not enforced by the repo — verify it is actually configured on the live zone. |
| R4 | Passkey login not rate-limited | **PASS (by design)** | `routes/auth.py:88-137` | Passkey assertions aren't brute-forceable (hardware + user verification), so no per-attempt lockout is needed; R3 still covers volumetric abuse. |

---

## 6. Logging

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| L1 | Credentials never logged | **PASS** | `integrations/trade_republic.py:33-35,60-61`, `integrations/revolut.py:81` | Integration errors log only `type(exc).__name__` — never PIN, Salt Edge secret, or tracebacks. |
| L2 | PIN not persisted or logged | **PASS** | `integrations/tr_setup.py:30,47-48`, `integrations/trade_republic.py:18-30` | PIN read via `getpass`, used only for one-time pairing, never written to disk/DB/logs; keyfile `chmod 600`. |
| L3 | Session tokens never logged | **PASS** | `backend/auth.py` | Only `sha256(token)` is stored (DB), never the raw token; tokens aren't logged anywhere. |
| L4 | Auth failures log safely | **PASS (note)** | `routes/auth.py:124-126,177-179` | WebAuthn failures log the library exception string (no secret material). Fine; just be aware it can be verbose. |
| L5 | `/healthz` leaks exception string | **NEEDS-ATTENTION (minor)** | `backend/main.py:78-79` | On DB failure, the **unauthenticated** `/healthz` returns `str(e)` in the 503 body, which can reveal a filesystem path. Return a generic `"degraded"` and log the detail server-side instead. |

---

## Additional observations (beyond the C1 checklist)

These are good-practice items surfaced during the read; not part of the required
checklist, listed for the backlog.

| ID | Item | Status | Evidence | Notes |
|----|------|--------|----------|-------|
| X1 | Container runs as root | **NEEDS-ATTENTION** | `Dockerfile` (no `USER`) | The app process runs as root inside the container. Add a non-root `USER` and consider compose `no-new-privileges`, `cap_drop: [ALL]`, `read_only` for the app service. Defence-in-depth. |
| X2 | API docs disabled | **PASS** | `backend/main.py:48-49` | `docs_url=None, redoc_url=None` — no OpenAPI/Swagger surface. Good. |
| X3 | Doc drift in `CLAUDE.md` | **NEEDS-ATTENTION** | `CLAUDE.md:58,69,77,115` | `CLAUDE.md` still describes **HTTP Basic Auth**, `DASHBOARD_USERNAME/PASSWORD`, and GoCardless — contradicting the actual WebAuthn/Salt Edge implementation. Not a code vulnerability, but stale security docs are a hazard. (`README.md` was already corrected.) |
| X4 | Last-passkey lockout guard | **PASS** | `routes/auth.py:238-244` | Deleting the only passkey while password login is disabled is refused (409) — prevents self-lockout. Good. |

---

## Recommended follow-up order (for when you greenlight fixes)

1. **D1** — bump `python-multipart` (≥0.0.18) and `fastapi` (≥0.115 → starlette ≥0.40); run tests. *(own branch, own PR)*
2. **R2 / R3** — confirm on the live server: `scripts/cloudflare-firewall.sh` applied, DNS Proxied, Cloudflare `/auth/*` rate-limit rule present. *(ops verification, no code)*
3. **L5 + I5** — tiny hardening (generic `/healthz` error; cap `top-merchants` limit).
4. **T7 / X1** — scope `forwarded-allow-ips`; run container as non-root.
5. **D4** — add `pip-audit`/Dependabot to CI.
6. **X3** — refresh `CLAUDE.md` to match reality (auth = passkey, bank = Salt Edge).
7. **T3** — optional CSP tightening (`object-src 'none'`; revisit `unsafe-eval` only if Alpine usage allows).

> Per the work plan, **none of the above were applied** — this is C1 (read-only). Auth
> code (guardrail #4) changes, if any, must be isolated, justified, and fully tested.
