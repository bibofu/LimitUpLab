from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import configure_runtime_environment, configured_cors_origins
from app.routers import agents, analysis, limit_up, market, stocks
from app.security import (
    AnonymousVisitorMiddleware,
    is_production_environment,
    validate_admin_security,
    validate_session_security,
)


configure_runtime_environment()
validate_session_security()
validate_admin_security()


app = FastAPI(
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(limit_up.router, prefix="/api/limit-up", tags=["limit-up"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
