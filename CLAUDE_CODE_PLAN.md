 # Claude Code — Work Plan: Personal Finance Tracker

**Repo:** `github.com/dstWIN26/personal-finance-tracker` (PUBLIC)
**Stack:** FastAPI + SQLite + APScheduler · vanilla HTML/Alpine.js/Chart.js · Docker + Caddy · WebAuthn passkey auth
**Live at:** `https://finance.pftracker-cc-de.online` (Hetzner VPS, behind Cloudflare)
**Current state:** Deployed, single-user, working. Holds REAL Trade Republic + Revolut data.

This document is the source of truth for three workstreams. Work them **in order**. Do not start a later workstream until the earlier one is reviewed and merged.

---

## ⛔ Hard guardrails — read before doing anything

These are non-negotiable. They exist because this app holds real financial data and the repo is public.

1. **Never touch secrets.** Do not read, print, move, or commit `.env`, `keys/`, `*.pem`, `*.db`, or anything under `data/`. If a task seems to need a secret, stop and write a note in the PR instead.
2. **This repo is public.** Before every commit, assume the whole world will read it. No credentials, no real account numbers, no IPs in code, no tokens in tests or fixtures.
3. **Do NOT build multi-tenancy in this pass.** Workstream B prepares the *ground* for multi-user (clean seams, scoped queries) but does not flip the app to multi-user. Going multi-tenant with other people's brokerage data is a separate, deliberate decision with legal/GDPR implications. Refactor toward it; do not ship it.
4. **Auth and security code is sacred.** `backend/auth.py`, session handling, WebAuthn, and security headers may only be changed with extreme care, a clear written rationale per change, and full test coverage. Prefer leaving them alone.
5. **Work in small, reviewable branches.** One branch per logical change. Never force-push `main`. Every PR must explain *what* and *why*.
6. **Tests must pass.** `pytest` is offline and must stay green. Add tests for anything you change. Do not weaken or delete tests to make them pass.
7. **No new external dependencies** without flagging them explicitly in the PR with a one-line justification each.
8. **When unsure, stop and ask** (leave a `TODO(human):` note) rather than guessing on anything touching money, auth, or data.

---

## Workstream A — UX, design & interactivity

**Goal:** Make the dashboard cleaner, more responsive, and more pleasant to use — without changing what data it shows or how auth works.

### Scope (do)
- Audit the seven tabs (Overview, Spending, Portfolio, Markets, Trading, Limits, Security) for visual consistency: spacing, typography scale, colour usage, alignment, and a coherent component look.
- Improve interactivity: button states (hover/active/disabled/loading), clear loading skeletons or spinners while data fetches, graceful empty-states ("no transactions yet" rather than a blank panel), and friendly error states ("couldn't reach market data — retrying").
- Mobile/responsive pass: the dashboard should be usable on a phone (it currently targets desktop). Charts and tables must not overflow.
- Accessibility floor: visible keyboard focus, sufficient colour contrast, `aria-label`s on icon-only buttons, respect `prefers-reduced-motion`.
- Chart polish: consistent colours/legends across Chart.js instances; tooltips that are readable.

### Out of scope (don't)
- No backend/route changes for cosmetic reasons.
- No new data sources or tabs in this pass.
- No build step / npm toolchain unless explicitly approved — the project is intentionally CDN-only, no-build. Keep it that way.

### Acceptance criteria
- Every interactive control has visible hover/active/focus/disabled states.
- Every data panel has a loading state and an empty state.
- The dashboard is usable at 375px width with no horizontal overflow.
- No regression in load behaviour; no console errors.
- A short `CHANGELOG`-style note in the PR listing what changed visually.

### Suggested order
1. Establish/clean shared CSS tokens (colours, spacing, type scale) in `frontend/css/style.css`.
2. Apply tokens tab by tab, starting with Overview.
3. Add loading/empty/error states.
4. Responsive + accessibility pass last.

---

## Workstream B — Codebase review & multi-tenant *readiness* (refactor, not rebuild)

**Goal:** A thorough review that makes the code cleaner, faster, and structured so a future multi-tenant version is a smaller, safer step — while the app stays single-user today.

### B1. Review & report first (no code changes yet)
Produce `REVIEW.md` in the repo with findings, before refactoring:
- **Architecture map:** how a request flows from route → auth guard → DB/cache → response. Note anything surprising.
- **Auth review:** confirm sessions, rotation, cookie flags, lockout, CSRF/origin checks, and security headers actually behave as the README claims. List any gap as a finding (do not silently "fix" auth — flag it).
- **Latency review:** identify the slowest paths. Likely candidates: synchronous external API calls on the request path, missing DB indexes, N+1 SQL in `database.py`, cache TTLs, scheduler jobs blocking. Measure where feasible (note method).
- **Redundancy audit:** find *genuinely* dead or duplicated code — unused functions, copy-pasted logic, dead routes, unreferenced files. **Do not** remove things that merely look unused but are wired up via dynamic dispatch, scheduler registration, or templates. When in doubt, list it as "candidate, needs confirmation" rather than deleting.
- **Multi-tenant gap analysis:** list every place that assumes a single user (single keyfile path, global SQL with no user scope, single session owner, single Revolut connection, etc.). This list becomes the future multi-tenant checklist.

### B2. Safe refactors (after review is reviewed)
Only the low-risk, high-value items, each in its own branch:
- Remove confirmed-dead code (from B1, confirmed only).
- Add DB indexes where the review shows clear benefit.
- Move blocking external calls off the request path / into the scheduler + cache where they aren't already.
- Introduce a **`user_id` seam** without enabling multi-user: e.g., a single source of truth for "current user" (hard-coded to the one user today) that every DB query passes through, so future multi-tenancy is "make this real" rather than "rewrite every query." This is the single most valuable prep step. It must change *nothing* observable for the current single user.
- Tighten typing / add docstrings on the modules you touch.

### Out of scope (don't)
- No actual multi-user: no registration, no per-user keyfiles, no tenant tables wired live. Prepare the seam, stop there.
- No swapping SQLite for another DB in this pass (note it as a future option in `REVIEW.md` if warranted).
- No rewrites of working subsystems "for cleanliness" — minimal diffs.

### Acceptance criteria
- `REVIEW.md` exists and is specific (file + line references), not generic.
- Every deletion traces back to a confirmed B1 finding.
- `pytest` green; new tests cover refactored paths.
- The `user_id` seam is in place and provably no-op for the current user (test demonstrating identical behaviour).
- A clear, ordered `MULTI_TENANT_TODO.md` produced for the future — the things deliberately NOT done now.

---

## Workstream C — Security standards & linking Trade Republic

**Goal:** (1) Verify the app meets sensible security standards for a personal finance app, and (2) support the human in linking their real Trade Republic account safely. The actual account pairing is a **human-operated** step (needs the phone + PIN + 2FA) — Claude Code's job is to make that flow robust and well-documented, not to perform it.

### C1. Security standards review (Claude Code does this)
Produce `SECURITY_REVIEW.md` checking the app against a practical baseline:
- **Transport & headers:** HTTPS-only, HSTS, CSP correctness, `nosniff`, frame-ancestors none, secure cookie flags (`__Host-`, HttpOnly, SameSite=Strict). Confirm, don't assume.
- **Secrets hygiene:** confirm `.gitignore` covers `.env`, `keys/`, `*.pem`, `*.db`, `backups/`; confirm CI gitleaks scan is active; confirm no secret has ever been committed (scan history, report only).
- **Dependency check:** flag known-vulnerable pinned versions in `requirements.txt` (report; don't auto-bump anything security-critical without calling it out).
- **Input validation:** routes validate/parameterise all inputs; SQL is parameterised (no string-built queries) in `database.py`.
- **Rate limiting / lockout:** confirm the bootstrap-password lockout works and that Cloudflare edge rate-limiting on `/auth/*` is the documented external layer.
- **Logging:** ensure secrets / tokens / PINs are never logged.
- Output: a checklist with PASS / FAIL / NEEDS-ATTENTION per item, each with a file reference.

### C2. Make the Trade Republic integration robust (Claude Code does this)
In `backend/integrations/trade_republic.py` and `tr_setup`:
- Improve error handling for the pairing flow (clear messages for wrong PIN, expired 2FA, network failure, missing keyfile).
- Ensure the **PIN is never persisted or logged** (README says keyfile-only — verify and enforce).
- Ensure the keyfile path is read from config and that a missing/invalid keyfile degrades gracefully (app still boots, portfolio tab shows "not linked").
- Confirm `pytr` version is pinned; note in PR that it is an **unofficial** library (see human caveat below).
- Write/clean `LINKING.md` so the human steps below are exact for this repo.

### C3. Linking steps (the HUMAN performs these — do not automate)
> ⚠️ The keyfile this produces grants access to a real brokerage account. Treat it like a bank card. It lives only in `keys/` on the server, is gitignored, and is included in your encrypted backups.

> ⚠️ `pytr` is an **unofficial** Trade Republic interface. Using it is your own account and your own risk: TR could change their API or treat automated access as a terms violation. Understand that before linking real money.

1. SSH into the server and go to the repo: `ssh root@<server>` → `cd personal-finance-tracker`.
2. Run the one-time pairing (writes `keys/tr_keyfile.pem`):
   `docker compose run --rm app python -m backend.integrations.tr_setup`
3. Enter your TR phone number (`+49…`) and PIN when prompted; approve the 2FA on your phone.
4. In `.env`, set `TRADE_REPUBLIC_PHONE=+49…`, and **remove any PIN line** — the PIN must not be stored.
5. Restart: `docker compose up -d`.
6. Open the Portfolio tab and confirm positions load.
7. Make a fresh encrypted backup now that the keyfile exists (so the link survives a server loss):
   `BACKUP_PASSPHRASE='…' ./scripts/backup.sh`
8. (Revolut, separately, via Salt Edge — see `LINKING.md`.)

### Acceptance criteria
- `SECURITY_REVIEW.md` with a PASS/FAIL/NEEDS-ATTENTION checklist.
- TR integration handles the common failure modes with clear messages, never logs the PIN, and degrades gracefully when unlinked.
- `LINKING.md` is accurate for this repo and matches the human steps above.

---

## How to work (process for all three)

- **Branch naming:** `ws-a/…`, `ws-b/…`, `ws-c/…`.
- **Each PR contains:** what changed, why, how it was tested, and any `TODO(human:)` items.
- **Never** combine cosmetic and security changes in one PR.
- **Stop conditions** — open a question to the human instead of proceeding if a task would: require a secret, change auth behaviour, enable multi-user, add a build step, or delete code you cannot prove is dead.
- **Definition of done per workstream:** acceptance criteria met, tests green, review docs produced, and a short summary written for the human in plain language.

## Recommended sequence
1. **C1** (security review — read-only, highest value, lowest risk) →
2. **B1** (codebase review — read-only) →
3. **A** (UX improvements — visible wins, low risk) →
4. **B2** (safe refactors + multi-tenant seam) →
5. **C2** (harden TR integration) →
6. **C3** (human links the account).

Do the read-only reviews first so every later change is informed by them.
