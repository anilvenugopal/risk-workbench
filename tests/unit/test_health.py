"""Unit tests for the /api/health endpoint.

Strategy: mount only the health router on a bare FastAPI app (no lifespan,
no DB, no Redis). Monkeypatch test_connection and redis so no external
services are needed. Assert on the JSON response shape and status code.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


def _make_app():
    from app.routers.health import router
    app = FastAPI()
    app.include_router(router)
    return app


class TestHealthEndpoint:
    def test_returns_200_always(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: True)
        import app.routers.health as h
        monkeypatch.setattr(h.settings, "redis_url", "redis://localhost:6379/0")

        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())

        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_response_has_required_keys(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: True)
        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())

        client = TestClient(_make_app(), raise_server_exceptions=False)
        data = client.get("/api/health").json()
        for key in ("status", "db_workbench", "db_exposure", "db_loss", "redis", "env"):
            assert key in data, f"missing key: {key}"

    def test_db_ok_when_connection_succeeds(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: True)
        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())

        data = TestClient(_make_app()).get("/api/health").json()
        assert data["db_workbench"] == "ok"
        assert data["db_exposure"] == "ok"
        assert data["db_loss"] == "ok"

    def test_db_error_when_connection_fails(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: False)
        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())

        data = TestClient(_make_app(), raise_server_exceptions=False).get("/api/health").json()
        assert data["db_workbench"].startswith("error")

    def test_db_error_when_connection_raises(self, monkeypatch):
        import app.routers.health as health_mod

        def _raise(name):
            raise RuntimeError("timeout")

        monkeypatch.setattr(health_mod, "test_connection", _raise)
        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())

        data = TestClient(_make_app(), raise_server_exceptions=False).get("/api/health").json()
        assert "RuntimeError" in data["db_workbench"]

    def test_redis_ok_when_ping_succeeds(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: True)
        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())

        data = TestClient(_make_app()).get("/api/health").json()
        assert data["redis"] == "ok"

    def test_redis_error_when_ping_raises(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: True)
        import redis as redis_lib

        class FailRedis:
            def ping(self):
                raise ConnectionError("refused")

        monkeypatch.setattr(redis_lib, "from_url", lambda url, **kw: FailRedis())

        data = TestClient(_make_app(), raise_server_exceptions=False).get("/api/health").json()
        assert data["redis"].startswith("error")

    def test_env_field_matches_app_env(self, monkeypatch):
        import app.routers.health as health_mod
        monkeypatch.setattr(health_mod, "test_connection", lambda name: True)
        import redis as redis_lib
        monkeypatch.setattr(redis_lib, "from_url",
                            lambda url, **kw: type("R", (), {"ping": lambda self: True})())
        import app.routers.health as h
        monkeypatch.setattr(h.settings, "app_env", "development")

        data = TestClient(_make_app()).get("/api/health").json()
        assert data["env"] == "development"
