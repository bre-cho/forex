"""
apps/api - FastAPI application entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.core.middleware import RequestIDMiddleware
from app.core.registry import set_registry

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Forex API (env=%s)", settings.app_env)

    if settings.is_production and settings.enable_legacy_routes:
        raise RuntimeError("enable_legacy_routes must be false in production")

    # Boot RuntimeRegistry
    try:
        from trading_core.runtime import RuntimeRegistry
        registry = RuntimeRegistry()
        app.state.registry = registry
        set_registry(registry)
    except ImportError:
        logger.warning("trading_core not available - RuntimeRegistry skipped")
        app.state.registry = None
        set_registry(None)

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

    import asyncio as _asyncio
    app.state.daemon_enabled = bool(settings.enable_reconciliation_daemon)
    if app.state.daemon_enabled:
        # Start Unknown Order Daemon (P0.5)
        from app.workers.reconciliation_daemon import run_reconciliation_daemon

        _daemon_stop = _asyncio.Event()
        _daemon_task = _asyncio.create_task(
            run_reconciliation_daemon(stop_event=_daemon_stop),
            name="reconciliation_daemon",
        )
        app.state.daemon_stop = _daemon_stop
        app.state.daemon_task = _daemon_task
    else:
        app.state.daemon_stop = None
        app.state.daemon_task = None
        logger.info("Reconciliation daemon disabled by config")

    app.state.submit_outbox_recovery_enabled = bool(settings.enable_submit_outbox_recovery_worker)
    if app.state.submit_outbox_recovery_enabled:
        from app.workers.submit_outbox_recovery_worker import run_submit_outbox_recovery_worker

        _outbox_stop = _asyncio.Event()
        _outbox_task = _asyncio.create_task(
            run_submit_outbox_recovery_worker(stop_event=_outbox_stop),
            name="submit_outbox_recovery_worker",
        )
        app.state.submit_outbox_recovery_stop = _outbox_stop
        app.state.submit_outbox_recovery_task = _outbox_task
    else:
        app.state.submit_outbox_recovery_stop = None
        app.state.submit_outbox_recovery_task = None
        logger.info("Submit outbox recovery worker disabled by config")

    yield

    logger.info("Shutting down Forex API")
    # Stop daemon gracefully
    _daemon_stop = getattr(app.state, "daemon_stop", None)
    _daemon_task = getattr(app.state, "daemon_task", None)
    if _daemon_stop is not None:
        _daemon_stop.set()
    if _daemon_task is not None:
        try:
            await _asyncio.wait_for(_daemon_task, timeout=5.0)
        except (_asyncio.TimeoutError, _asyncio.CancelledError):
            _daemon_task.cancel()

    _outbox_stop = getattr(app.state, "submit_outbox_recovery_stop", None)
    _outbox_task = getattr(app.state, "submit_outbox_recovery_task", None)
    if _outbox_stop is not None:
        _outbox_stop.set()
    if _outbox_task is not None:
        try:
            await _asyncio.wait_for(_outbox_task, timeout=5.0)
        except (_asyncio.TimeoutError, _asyncio.CancelledError):
            _outbox_task.cancel()

    registry = getattr(app.state, "registry", None)
    if registry:
        await registry.stop_all()
    set_registry(None)
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
    live_trading,
    notifications,
    provider_certification,
    public,
    qa_parity,
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
app.include_router(provider_certification.router)
app.include_router(risk_policy.router)
app.include_router(experiments.router)
app.include_router(qa_parity.router)
if settings.enable_legacy_routes:
    from app.routers import legacy

    app.include_router(legacy.router)


@app.get("/health")
async def health():
    return await health_ready()


@app.get("/health/ready")
async def health_ready():
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


@app.get("/health/live")
async def health_live():
    daemon_task = getattr(app.state, "daemon_task", None)
    daemon_enabled = bool(getattr(app.state, "daemon_enabled", False))
    daemon_running = bool(daemon_task is not None and not daemon_task.done())
    outbox_task = getattr(app.state, "submit_outbox_recovery_task", None)
    outbox_enabled = bool(getattr(app.state, "submit_outbox_recovery_enabled", False))
    outbox_running = bool(outbox_task is not None and not outbox_task.done())
    return {
        "status": "ok",
        "env": settings.app_env,
        "daemon_enabled": daemon_enabled,
        "daemon_running": daemon_running,
        "submit_outbox_recovery_enabled": outbox_enabled,
        "submit_outbox_recovery_running": outbox_running,
        "legacy_routes_enabled": bool(settings.enable_legacy_routes),
    }


@app.get("/health/deep")
async def health_deep():
    db_ok = False
    redis_ok = False

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.warning("health/deep db check failed: %s", exc)

    try:
        from app.core.cache import get_redis

        redis = await get_redis()
        await redis.ping()
        redis_ok = True
    except Exception as exc:
        logger.warning("health/deep redis check failed: %s", exc)

    daemon_task = getattr(app.state, "daemon_task", None)
    daemon_enabled = bool(getattr(app.state, "daemon_enabled", False))
    daemon_running = bool(daemon_task is not None and not daemon_task.done())
    outbox_task = getattr(app.state, "submit_outbox_recovery_task", None)
    outbox_enabled = bool(getattr(app.state, "submit_outbox_recovery_enabled", False))
    outbox_running = bool(outbox_task is not None and not outbox_task.done())

    checks = {
        "db": db_ok,
        "redis": redis_ok,
        "reconciliation_daemon": (daemon_running if daemon_enabled else True),
        "submit_outbox_recovery_worker": (outbox_running if outbox_enabled else True),
        "legacy_routes_disabled": not bool(settings.enable_legacy_routes),
    }
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "env": settings.app_env,
        "checks": checks,
    }
