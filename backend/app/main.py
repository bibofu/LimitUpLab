from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import analysis, limit_up, market


app = FastAPI(
    title="LimitUpLab API",
    description="Research APIs for A-share limit-up events and short-term sentiment.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(limit_up.router, prefix="/api/limit-up", tags=["limit-up"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
