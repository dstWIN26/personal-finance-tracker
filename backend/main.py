import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import validate
from backend.database import init_db
from backend import auth
from backend.routes import spending, portfolio, limits, market, overview, auth as auth_routes
from backend.alerts import check_and_alert, send_daily_summary, send_weekly_portfolio
from backend.integrations.trade_republic import sync_portfolio, sync_tr_transactions
from backend.integrations.revolut import sync_transactions as sync_revolut_transactions
from backend.integrations.market import refresh_quotes_cache, refresh_bonds_cache

logging.basicConfig(level=logging.INFO)

validate()
init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(sync_portfolio,            "interval", minutes=15)
    scheduler.add_job(sync_tr_transactions,      "interval", hours=1)
    scheduler.add_job(sync_revolut_transactions, "interval", hours=1)
    scheduler.add_job(check_and_alert,           "interval", hours=1)
    # Market-data cache warming → keeps Markets/Trading tabs instant + within rate limits
    scheduler.add_job(refresh_quotes_cache, "interval", seconds=60, id="market_quotes")
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

# Auth router is public; each endpoint gates itself where needed.
app.include_router(auth_routes.router)

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/login")
def login_page():
    return FileResponse("frontend/login.html")


@app.get("/")
def index(request: Request):
    # Unauthenticated visitors are sent to the login page (no 401 for the SPA shell).
    if not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse("frontend/index.html")
