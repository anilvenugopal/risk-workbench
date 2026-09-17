"""Health check endpoint for the application and its backing services."""

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

    workbench = _check_db("WORKBENCH")
    redis = _check_redis()
    ready = workbench == "ok" and redis == "ok"
    return JSONResponse({
        "status": "ok" if ready else "error",
        "db_workbench": workbench,
        "db_exposure": _check_db("EXPOSURE"),
        "db_loss": _check_db("LOSS"),
        "redis": redis,
        "env": settings.app_env,
    }, status_code=200 if ready else 503)
