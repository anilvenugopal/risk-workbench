"""FastAPI application entry point.

Architecture: FastAPI + Jinja2 + HTMX. Server-rendered; no SPA.
All handlers are plain `def` (sync). Only SSE endpoints use `async def`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.csrf import generate_csrf_token
from app.auth.middleware import SessionMiddleware
from app.config import settings
from db.connection import dispose_all, test_connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify WORKBENCH connectivity.
    if not test_connection("WORKBENCH"):
        raise RuntimeError("WORKBENCH database is not reachable at startup.")
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

# Static assets — served by nginx in production; FastAPI serves them in dev.
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Session middleware (must come before routers)
app.add_middleware(SessionMiddleware)

# ── Templates ──────────────────────────────────────────────────────────────
templates = Jinja2Templates(directory="app/templates")

# Inject globals available in every template
templates.env.globals["app_env"] = settings.app_env
templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
templates.env.globals["generate_csrf_token"] = generate_csrf_token

# Make templates available to routers via app state
app.state.templates = templates

# ── Routers ────────────────────────────────────────────────────────────────
from app.routers import auth, shell, health, admin  # noqa: E402

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(shell.router)


# ── Error handlers ─────────────────────────────────────────────────────────

def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@app.exception_handler(404)
async def not_found(request: Request, exc):
    current_user = getattr(request.state, "user", None)
    ctx = {
        "status_code": 404,
        "title": "Page Not Found",
        "detail": "The page you are looking for does not exist.",
        "is_htmx": _is_htmx(request),
        "current_user": current_user,
    }
    return templates.TemplateResponse(request, "base/error.html", ctx, status_code=404)


@app.exception_handler(500)
async def server_error(request: Request, exc):
    current_user = getattr(request.state, "user", None)
    ctx = {
        "status_code": 500,
        "title": "Internal Server Error",
        "detail": "Something went wrong. Please try again.",
        "is_htmx": _is_htmx(request),
        "current_user": current_user,
    }
    return templates.TemplateResponse(request, "base/error.html", ctx, status_code=500)
