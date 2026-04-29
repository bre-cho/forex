"""
apps/api - FastAPI application entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.middleware import RequestIDMiddleware

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Forex API (env=%s)", settings.app_env)

    # Boot RuntimeRegistry
    try:
        from trading_core.runtime import RuntimeRegistry
        registry = RuntimeRegistry()
        app.state.registry = registry
    except ImportError:
        logger.warning("trading_core not available - RuntimeRegistry skipped")
        app.state.registry = None

    # Warm up Redis
    try:
        from app.core.cache import get_redis
        await get_redis()
        logger.info("Redis connected")
    except Exception as exc:
        logger.warning("Redis unavailable: %s", exc)

    if settings.sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env)

    yield

    logger.info("Shutting down Forex API")
    registry = getattr(app.state, "registry", None)
    if registry:
        await registry.stop_all()
    from app.core.cache import close_redis
    await close_redis()


app = FastAPI(
    title="Forex Trading Platform API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
from app.routers import (
    admin,
    analytics,
    auth,
    billing,
    broker_connections,
    bots,
    experiments,
    incidents,
    legacy,
    live_trading,
    notifications,
    public,
    risk_policy,
    signals,
    strategies,
    users,
    workspaces,
    ws,
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(workspaces.router)
app.include_router(broker_connections.router)
app.include_router(strategies.router)
app.include_router(bots.router)
app.include_router(signals.signals_router)
app.include_router(signals.orders_router)
app.include_router(signals.trades_router)
app.include_router(analytics.router)
app.include_router(notifications.router)
app.include_router(billing.router)
app.include_router(public.router)
app.include_router(admin.router)
app.include_router(ws.router)
app.include_router(incidents.router)
app.include_router(live_trading.router)
app.include_router(risk_policy.router)
app.include_router(experiments.router)
app.include_router(legacy.router)


@app.get("/health")
async def health():
    registry = getattr(app.state, "registry", None)
    runtime_health = {"total": 0, "running": 0, "paused": 0, "error": 0}
    if registry is not None and hasattr(registry, "list_all"):
        runtimes = registry.list_all()
        runtime_health["total"] = len(runtimes)
        for item in runtimes:
            status = item.get("status")
            if status in runtime_health:
                runtime_health[status] += 1
    return {
        "status": "ok",
        "version": "1.0.0",
        "env": settings.app_env,
        "runtime_health": runtime_health,
    }
