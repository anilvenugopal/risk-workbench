"""Health check endpoint — reports status of all backing services.

Always returns HTTP 200. Never includes credentials or stack traces.
Exempt from session middleware (registered before SessionMiddleware).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from db.connection import test_connection

router = APIRouter()


@router.get("/api/health")
def health(request: Request):
    def _check_db(conn_name: str) -> str:
        try:
            return "ok" if test_connection(conn_name) else "error: connection failed"
        except Exception as exc:
            return f"error: {type(exc).__name__}"

    def _check_redis() -> str:
        try:
            import redis as redis_lib
            r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
            r.ping()
            return "ok"
        except Exception as exc:
            return f"error: {type(exc).__name__}"

    return JSONResponse({
        "status": "ok",
        "db_workbench": _check_db("WORKBENCH"),
        "db_exposure": _check_db("EXPOSURE"),
        "db_loss": _check_db("LOSS"),
        "redis": _check_redis(),
        "env": settings.app_env,
    })
