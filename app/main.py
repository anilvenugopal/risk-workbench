"""FastAPI application entry point.

Architecture: FastAPI + Jinja2 + HTMX. Server-rendered; no SPA.
All handlers are plain `def` (sync). Only SSE endpoints use `async def`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from db.connection import dispose_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connectivity, register manifests, etc.
    # (expanded in Iteration 0)
    yield
    # Shutdown: return pooled connections cleanly.
    dispose_all()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
    # Disable interactive docs in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)

# Static assets — served by nginx in production; FastAPI serves them in dev
# for convenience.
app.mount("/static", StaticFiles(directory="app/static", check_dir=False), name="static")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": settings.app_version}
