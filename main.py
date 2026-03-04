"""
main.py – PayNPass Risk Intelligence Engine
============================================
FastAPI application factory.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import get_settings
from app.db.database import Base, engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create all DB tables on startup (use Alembic migrations in production)."""
    # Import all models so SQLAlchemy registers them before create_all
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="PayNPass Risk Intelligence Engine",
    description=(
        "Deterministic rule-based behavioral risk scoring for the "
        "PayNPass Scan & Pay platform."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Tighten origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else ["https://dashboard.paynpass.in"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "paynpass-risk-engine", "version": "1.0.0"}
