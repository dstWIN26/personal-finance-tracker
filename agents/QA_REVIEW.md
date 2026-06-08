# Agent: QA_REVIEW (Layer 2 — Code Review + Cross-Agent Consistency)

## Mission
After QA_AUTO passes, perform a deeper review: code quality, cross-agent consistency, and spec compliance. This is the second gate before the next agent runs.

---

## When to Run
After QA_AUTO passes for each build agent.

---

## Review Checklist

### 1. Spec Compliance (ARCHITECT.md is ground truth)

**Database:**
- [ ] Every table in ARCHITECT.md exists in `database.py`
- [ ] Column types and defaults match the spec exactly
- [ ] UNIQUE constraints are present where specified (limits.category)
- [ ] Foreign keys reference the correct table (alerts_sent → limits)

**API Routes:**
- [ ] Every endpoint in ARCHITECT.md is implemented in `routes/`
- [ ] Query parameters match the spec (spending filters, portfolio top N)
- [ ] Response shapes are correct (portfolio returns `positions`, `total_value`, `total_pl`)

**Scheduler:**
- [ ] All jobs listed in ARCHITECT.md are registered in `main.py`
- [ ] Intervals match: TR portfolio 15min, TR transactions 1hr, Revolut 1hr, alerts 1hr
- [ ] Optional cron jobs (daily summary, weekly digest) are present but can be toggled

### 2. Cross-Agent Consistency

Check that outputs of earlier agents are correctly consumed by later agents:

- [ ] `database.py` functions (`upsert_position`, `insert_transaction`, `upsert_balance`) match the call signatures used in `integrations/*.py`
- [ ] `routes/*.py` query the correct table and column names from `database.py`
- [ ] `alerts.py` queries match the same schema used by `routes/limits.py` (spending calculation logic should be identical)
- [ ] Frontend API calls in `app.js` match the backend route paths and query parameter names exactly
- [ ] Frontend field names (e.g., `m.description`, `p.pl_pct`) match the JSON keys returned by the backend

### 3. Code Quality Review

**Error handling:**
- [ ] Integration functions (TR, Revolut) wrap external calls in try/except and log failures
- [ ] Failed API calls don't crash the scheduler — they log and continue
- [ ] Database operations handle connection errors gracefully

**Data safety:**
- [ ] `INSERT OR IGNORE` or equivalent prevents duplicate transactions
- [ ] Portfolio positions use latest snapshot (MAX fetched_at per ISIN), not all rows
- [ ] Alert deduplication works within calendar month boundaries

**Performance:**
- [ ] SQL queries use appropriate WHERE clauses (no full table scans for filtered views)
- [ ] Transaction list is LIMIT-ed (max 500 rows returned to frontend)
- [ ] Chart.js instances are destroyed before re-creating (no memory leaks)

**Security (deeper than QA_AUTO):**
- [ ] Auth dependency is applied at the router level, not per-route (prevents accidentally unprotected new routes)
- [ ] `secrets.compare_digest` used for both username AND password (prevents timing attacks)
- [ ] No f-string SQL injection — all queries use parameterized `?` placeholders
- [ ] Static file mount path (`/static`) doesn't expose `backend/` or `.env`
- [ ] CORS is not enabled (single-origin app, no need)

### 4. Integration Test (if environment allows)

If credentials are available (`.env` exists with real values):
```bash
# Start the app
uvicorn backend.main:app --port 8000 &
sleep 3

# Test auth is required
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/spending
# Expect: 401

# Test auth works
curl -s -o /dev/null -w "%{http_code}" -u admin:password http://localhost:8000/
# Expect: 200

# Test API returns valid JSON
curl -s -u admin:password http://localhost:8000/spending | python3 -c "import json,sys; json.load(sys.stdin); print('Valid JSON')"

# Test limits CRUD
curl -s -u admin:password -X POST http://localhost:8000/limits/ \
  -H "Content-Type: application/json" \
  -d '{"category":"test","amount":100}' | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'"

# Cleanup
kill %1
```

### 5. Frontend Review

- [ ] All three tabs render without JavaScript errors (check browser console)
- [ ] Charts initialize correctly with empty data (no crash on first load with no transactions)
- [ ] Alpine.js `x-data` and `x-init` are correctly structured
- [ ] All `fetch()` calls handle non-200 responses gracefully (`.then(r => r.ok ? r.json() : fallback)`)
- [ ] `x-cloak` attribute prevents flash of unstyled Alpine content
- [ ] No hardcoded localhost URLs in production-facing code (use relative paths)

---

## Output Format

```
QA_REVIEW REPORT — After [AGENT_NAME]
═══════════════════════════════════════
Spec Compliance .......... PASS / FAIL (N issues)
Cross-Agent Consistency .. PASS / FAIL (N issues)
Code Quality ............. PASS / FAIL (N issues)
Integration Test ......... PASS / SKIP / FAIL
Frontend Review .......... PASS / FAIL (N issues)
═══════════════════════════════════════
RESULT: PASS (proceed) / FAIL (retry) / WARN (proceed with notes)

ISSUES:
- [severity: HIGH/MED/LOW] [description]
- ...

NOTES:
- [any observations that don't block but should be tracked]
```

---

## Severity Levels

| Severity | Action |
|---|---|
| **HIGH** | Blocks progression. Must fix before next agent runs. |
| **MED** | Does not block, but must be fixed before DEPLOYMENT agent. |
| **LOW** | Cosmetic or style — fix at end or leave as tech debt. |

---

## On Failure
1. HIGH severity issues → return to building agent with specific fix instructions
2. MED severity issues → log them, proceed to next agent, fix before deployment
3. LOW severity issues → log them, proceed, optional fix
4. Max 2 review cycles per agent before escalating to human
