import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import validate
from backend.database import init_db
from backend import auth, config, ratelimit, assets
from backend.routes import spending, portfolio, limits, market, overview, settings, auth as auth_routes
from backend.alerts import check_and_alert, send_daily_summary, send_weekly_portfolio
from backend.integrations.revolut import sync_transactions as sync_revolut_transactions
from backend.integrations.enable_banking import sync_bank_connections
from backend.integrations.market import refresh_quotes_cache, refresh_bonds_cache

logging.basicConfig(level=logging.INFO)

validate()
init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    # Trade Republic is CSV-import-only (no automated API — TR blocks it and it
    # risks an account ban), so there are no TR sync jobs here.
    scheduler.add_job(sync_revolut_transactions, "interval", hours=1)
    scheduler.add_job(sync_bank_connections,     "interval", hours=1)
    scheduler.add_job(check_and_alert,           "interval", hours=1)
    # Market-data cache warming → keeps Markets/Trading tabs instant. Interval is
    # 15 min to stay within FMP's free tier (250 requests/day); the browser polls
    # the cache, not the upstream. Bonds update daily → 30 min.
    scheduler.add_job(refresh_quotes_cache, "interval", seconds=900, id="market_quotes")
    scheduler.add_job(refresh_bonds_cache,  "interval", minutes=30, id="market_bonds")
    # Optional digests — uncomment to enable
    # scheduler.add_job(send_daily_summary,   CronTrigger(hour=21, minute=0))
    # scheduler.add_job(send_weekly_portfolio, CronTrigger(day_of_week="mon", hour=8))
    scheduler.start()
    # Warm caches immediately so the first page load is fast.
    asyncio.create_task(refresh_quotes_cache())
    asyncio.create_task(refresh_bonds_cache())
    yield
    scheduler.shutdown()

app = FastAPI(
    title="Personal Finance Tracker",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# Hardening headers (CSP, HSTS on https, nosniff, frame-ancestors none, …).
app.add_middleware(auth.SecurityHeadersMiddleware)

# Protected app routers — every endpoint requires a valid session cookie.
guard = [Depends(auth.require_session)]
app.include_router(spending.router,  dependencies=guard)
app.include_router(portfolio.router, dependencies=guard)
app.include_router(limits.router,    dependencies=guard)
app.include_router(market.router,    dependencies=guard)
app.include_router(overview.router,  dependencies=guard)
app.include_router(settings.router,  dependencies=guard)

# Auth router is public; each endpoint gates itself where needed.
app.include_router(auth_routes.router)

# Asset URLs are content-versioned (?v=<hash>), so the browser/edge can keep a
# cached copy — but we still send `no-cache` so a *changed* file (new hash, same
# path) is always revalidated and never served stale after a deploy.
class _RevalidatingStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _RevalidatingStatic(directory="frontend"), name="static")

# HTML shells must never be cached, so the current asset version is always seen.
_NOCACHE = {"Cache-Control": "no-cache"}


def _html(filename: str) -> HTMLResponse:
    return HTMLResponse(assets.render(filename), headers=_NOCACHE)


@app.get("/healthz")
def healthz():
    """Unauthenticated liveness/readiness probe for Docker + uptime monitors.
    Verifies the process is up and the database is reachable."""
    try:
        from backend.database import connect
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "db": "ok"}
    except Exception:                                     # noqa: BLE001
        # Don't leak the exception detail (e.g. a filesystem path) on this
        # unauthenticated probe — log it server-side, return a generic status.
        logging.getLogger(__name__).exception("healthz DB check failed")
        return JSONResponse({"status": "degraded"}, status_code=503)


@app.get("/login")
def login_page():
    return _html("login.html")


@app.get("/settings/banks/callback")
def banks_callback():
    """Public bounce page for the bank consent redirect.

    The bank's cross-site redirect cannot carry the SameSite=Strict session
    cookie, so this tiny page (served without auth, reads no secrets) finishes
    the link with a same-origin, session-authenticated fetch in banks-callback.js.
    """
    return _html("banks-callback.html")


@app.get("/")
async def index(request: Request):
    # Unauthenticated visitors are sent to the login page (no 401 for the SPA shell).
    if not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    # Refresh-spam guard: a reload storm first gets a short buffer, then — if it
    # keeps up — the session is dropped and the owner is bounced back to login.
    action, secs = ratelimit.page_guard.check(auth._client_ip(request))
    if action == ratelimit.LOCKOUT:
        logging.getLogger(__name__).warning(
            "refresh-spam lockout for %s (%.0fs)", auth._client_ip(request), secs)
        auth.destroy_session(request.cookies.get(config.SESSION_COOKIE))
        resp = RedirectResponse("/login?locked=refresh", status_code=status.HTTP_303_SEE_OTHER)
        auth.clear_session_cookie(resp)
        return resp
    if action == ratelimit.THROTTLE:
        await asyncio.sleep(secs)
    return _html("index.html")
