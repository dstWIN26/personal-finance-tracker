# Agent: QA_AUTO (Layer 1 — Automated Checks)

## Mission
Run automatically after each build agent completes. Block progression to the next agent if any check fails. Return the failing agent's name + the error so it can retry.

---

## When to Run
After each of: INTEGRATIONS, BACKEND, ALERTS, FRONTEND, SECURITY, DEPLOYMENT

---

## Checks to Run (in order)

### 1. Python Syntax Check
```bash
python3 -c "
import ast, sys, os
errors = []
for root, dirs, files in os.walk('backend'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path) as fh:
                    ast.parse(fh.read(), filename=path)
            except SyntaxError as e:
                errors.append(f'{path}: {e}')
                print(f'FAIL {path}: {e}')
if errors:
    sys.exit(1)
print('PASS: All Python files parse correctly')
"
```
**Pass criteria:** Exit code 0, no SyntaxError

### 2. Import Resolution Check
```bash
cd /path/to/project
PYTHONPATH=. python3 -c "
import backend.config
import backend.database
import backend.alerts
import backend.routes.spending
import backend.routes.portfolio
import backend.routes.limits
import backend.integrations.trade_republic
import backend.integrations.revolut
print('PASS: All modules import successfully')
"
```
**Pass criteria:** No ImportError or ModuleNotFoundError (note: pytr/httpx may not be installed locally — skip import if ModuleNotFoundError is for a third-party package)

### 3. Schema Consistency Check
Verify that `database.py` CREATE TABLE statements match ARCHITECT.md:
- [ ] `positions` table has columns: id, isin, name, quantity, buy_price, current_price, pl_pct, pl_eur, fetched_at
- [ ] `transactions` table has columns: id, source, date, description, category, amount, currency, raw_json
- [ ] `balances` table has columns: id, source, balance, currency, fetched_at
- [ ] `limits` table has columns: id, category (UNIQUE), amount, period
- [ ] `alerts_sent` table has columns: id, limit_id (FK), sent_at, threshold

### 4. Route Coverage Check
Verify these endpoints exist in the route files:
- [ ] GET /spending
- [ ] GET /spending/summary
- [ ] GET /spending/daily
- [ ] GET /spending/top-merchants
- [ ] GET /portfolio
- [ ] GET /portfolio/top
- [ ] GET /limits
- [ ] POST /limits
- [ ] DELETE /limits/{id}
- [ ] GET /limits/status

### 5. Security Check
- [ ] `.gitignore` contains: `.env`, `*.db`, `*.pem`, `__pycache__/`
- [ ] `.env.example` exists and contains NO real credentials (no 16+ char strings, no real phone numbers)
- [ ] `backend/main.py` applies `Depends(auth)` to all routers
- [ ] Auth uses `secrets.compare_digest` (not `==`)
- [ ] No hardcoded passwords, API keys, or tokens in any `.py` file

### 6. Frontend Integrity Check
- [ ] `frontend/index.html` exists and is valid HTML (no unclosed tags)
- [ ] `frontend/js/app.js` exists
- [ ] `frontend/css/style.css` exists
- [ ] All three CDN scripts referenced (Alpine.js, Chart.js)
- [ ] No inline credentials or API keys

### 7. Docker Build Check
```bash
docker build -t finance-tracker-test . 2>&1
```
**Pass criteria:** Exit code 0, image builds successfully

---

## Output Format

```
QA_AUTO REPORT — After [AGENT_NAME]
═══════════════════════════════════
[1] Python Syntax .......... PASS / FAIL
[2] Import Resolution ...... PASS / FAIL
[3] Schema Consistency ..... PASS / FAIL
[4] Route Coverage ......... PASS / FAIL
[5] Security ............... PASS / FAIL
[6] Frontend Integrity ..... PASS / FAIL
[7] Docker Build ........... PASS / FAIL
═══════════════════════════════════
RESULT: PASS (proceed) / FAIL (retry)

FAILURES:
- [check name]: [specific error message]
```

---

## On Failure
1. Do NOT proceed to the next agent
2. Return the error to the agent that just ran
3. The building agent retries with the error context
4. Max 3 retries per agent before escalating to human review
