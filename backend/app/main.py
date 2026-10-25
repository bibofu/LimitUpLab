from contextlib import asynccontextmanager, closing, suppress
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import configure_runtime_environment, configured_cors_origins
from app.routers import agents, analysis, consolidation, limit_up, market, memories, stocks
from app.security import (
    AnonymousVisitorMiddleware,
    is_production_environment,
    validate_admin_security,
    validate_session_security,
)


configure_runtime_environment()
validate_session_security()
validate_admin_security()


@asynccontextmanager
async def lifespan(application):
    from app.database import connect, initialize_database
    from app.services.agent_tracing import configure_agent_tracer, shutdown_agent_tracer
    from app.services.durable_runtime_host import DurableRuntimeHost
    from app.database import get_database_url
    from app.services.agent_run_recovery import supervise_run_recovery

    configure_agent_tracer()
    host = None
    recovery = None
    try:
        with closing(connect()) as db:
            initialize_database(db)
        host = await asyncio.to_thread(lambda: DurableRuntimeHost(get_database_url()).start())
        recovery = asyncio.create_task(supervise_run_recovery())
        yield
    finally:
        if recovery is not None:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery
        if host is not None:
            await asyncio.to_thread(host.close)
        shutdown_agent_tracer()


app = FastAPI(
    lifespan=lifespan,
    title="LimitUpLab API",
    description="Research APIs for A-share limit-up events and first-board evaluation.",
    version="0.1.0",
    docs_url=None if is_production_environment() else "/docs",
    redoc_url=None if is_production_environment() else "/redoc",
    openapi_url=None if is_production_environment() else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AnonymousVisitorMiddleware)


# Return the lightweight service health response; this endpoint does not execute a business query.
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Verify storage/schema readiness without creating or migrating data."""
    from app.database import connect_readonly, initialize_database
    try:
        with closing(connect_readonly()) as db:
            initialize_database(db)
        return {"status": "ok", "database": "postgresql"}
    except Exception as error:
        raise HTTPException(503, "Database is not ready") from error


app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(limit_up.router, prefix="/api/limit-up", tags=["limit-up"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(memories.router, prefix="/api/agents", tags=["memories"])
app.include_router(consolidation.router, prefix="/api/strategies", tags=["strategies"])
