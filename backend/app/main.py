"""FastAPI application entry point (section 14).

Startup:
  - create_all tables
  - seed users

CORS:
  - FRONTEND_ORIGINS env (comma-separated)
  - http://localhost:5173 always included
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base, engine, SessionLocal
from app.models import seed_users
from app.routes import audit, exceptions, metrics, orchestrate, runs
from app.schemas import HealthOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    Base.metadata.create_all(bind=engine)
    # Seed users
    db: Session = SessionLocal()
    try:
        seed_users(db)
    finally:
        db.close()
    yield


app = FastAPI(title="PrimeLedger API", version="0.1.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

_origins = [o.strip() for o in settings.FRONTEND_ORIGINS.split(",") if o.strip()]
if "http://localhost:5173" not in _origins:
    _origins.append("http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Health (not under /api)
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok")


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

app.include_router(runs.router, prefix="/api")
app.include_router(exceptions.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(orchestrate.router, prefix="/api")
