"""Unit tests for app/log_context.py and app/logging_setup.py (issue #28).

Strategy: the context filter and formatters are exercised on hand-built
LogRecords (including records from a ``db.*`` logger — proving ``db/`` needs no
import to be enriched); ``setup_logging`` runs against a snapshot-and-restored
root logger; the middleware runs in a minimal FastAPI app via TestClient
(pattern: tests/unit/test_auth_routes.py) with a private capture handler on the
``app.access`` logger.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import uuid

import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app import log_context, logging_setup
from app.config import settings
from app.logging_setup import (
    ConsoleFormatter,
    ContextFilter,
    JsonFormatter,
    RequestContextMiddleware,
    setup_logging,
)


class _Capture(logging.Handler):
    """Collects records after running them through a ContextFilter — the same
    enrichment the real root handler applies."""

    def __init__(self, process_name: str = "test") -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(ContextFilter(process_name))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _make_record(logger_name: str = "db.connection", msg: str = "hello",
                 level: int = logging.INFO, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(logger_name, level, __file__, 1, msg, (), exc_info)


# ── log_context ───────────────────────────────────────────────────────────────

class TestLogContext:
    def test_bind_get_clear(self):
        token = log_context.bind(correlation_id="c1", foo=1)
        try:
            assert log_context.get() == {"correlation_id": "c1", "foo": 1}
            assert log_context.correlation_id() == "c1"
        finally:
            log_context.clear(token)
        assert log_context.get() == {}
        assert log_context.correlation_id() is None

    def test_add_merges_in_place(self):
        token = log_context.bind(correlation_id="c1")
        try:
            log_context.add(user_id="u1")
            assert log_context.get() == {"correlation_id": "c1", "user_id": "u1"}
        finally:
            log_context.clear(token)

    def test_add_without_bind_is_noop(self):
        log_context.add(user_id="u1")  # must not raise
        assert log_context.get() == {}

    def test_nested_bind_restores_previous(self):
        outer = log_context.bind(correlation_id="outer")
        inner = log_context.bind(correlation_id="inner")
        assert log_context.correlation_id() == "inner"
        log_context.clear(inner)
        assert log_context.correlation_id() == "outer"
        log_context.clear(outer)

    def test_get_returns_snapshot(self):
        token = log_context.bind(a=1)
        try:
            log_context.get()["a"] = 999
            assert log_context.get()["a"] == 1
        finally:
            log_context.clear(token)


# ── ContextFilter ─────────────────────────────────────────────────────────────

class TestContextFilter:
    def test_stamps_context_onto_foreign_module_record(self):
        # A db/-originated record is enriched without db/ importing anything.
        token = log_context.bind(correlation_id="c1", rwb_job_id="j1")
        try:
            record = _make_record("db.connection")
            assert ContextFilter("app").filter(record) is True
        finally:
            log_context.clear(token)
        assert record.process_name == "app"
        assert record.correlation_id == "c1"
        assert record.rwb_job_id == "j1"
        assert record.log_context == {"correlation_id": "c1", "rwb_job_id": "j1"}

    def test_reserved_record_fields_not_clobbered(self):
        token = log_context.bind(name="evil", message="evil", levelname="evil")
        try:
            record = _make_record("app.x")
            ContextFilter("app").filter(record)
        finally:
            log_context.clear(token)
        assert record.name == "app.x"
        assert record.levelname == "INFO"

    def test_no_context_bound(self):
        record = _make_record()
        ContextFilter("poller").filter(record)
        assert record.process_name == "poller"
        assert record.log_context == {}


# ── Formatters ────────────────────────────────────────────────────────────────

class TestJsonFormatter:
    def _format(self, record) -> dict:
        return json.loads(JsonFormatter().format(record))

    def test_emits_parseable_json_with_context(self):
        token = log_context.bind(correlation_id="c1", duration_ms=12.5)
        try:
            record = _make_record("app.workers.runtime", "job done")
            ContextFilter("worker").filter(record)
        finally:
            log_context.clear(token)
        payload = self._format(record)
        assert payload["message"] == "job done"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.workers.runtime"
        assert payload["process"] == "worker"
        assert payload["correlation_id"] == "c1"
        assert payload["duration_ms"] == 12.5
        assert "ts" in payload and "pid" in payload

    def test_exception_text_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = _make_record("app.x", "failed", logging.ERROR, sys.exc_info())
        ContextFilter("app").filter(record)
        assert "ValueError: boom" in self._format(record)["exc_info"]

    def test_non_serializable_context_value_stringified(self):
        some_id = uuid.uuid4()
        token = log_context.bind(some_id=some_id)
        try:
            record = _make_record()
            ContextFilter("app").filter(record)
        finally:
            log_context.clear(token)
        assert self._format(record)["some_id"] == str(some_id)


class TestConsoleFormatter:
    def test_line_contains_fields_and_context_suffix(self):
        token = log_context.bind(correlation_id="c1")
        try:
            record = _make_record("app.access", "GET / -> 200")
            ContextFilter("app").filter(record)
        finally:
            log_context.clear(token)
        line = ConsoleFormatter().format(record)
        assert "INFO" in line
        assert "[app.access]" in line
        assert "GET / -> 200" in line
        assert "correlation_id=c1" in line

    def test_exception_appended_after_line(self):
        try:
            raise RuntimeError("kaput")
        except RuntimeError:
            record = _make_record("app.x", "failed", logging.ERROR, sys.exc_info())
        ContextFilter("app").filter(record)
        line = ConsoleFormatter().format(record)
        assert "failed" in line.splitlines()[0]
        assert "RuntimeError: kaput" in line


# ── setup_logging ─────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_logging(monkeypatch):
    """Reset the configured flag and run against an emptied, snapshot-restored
    root logger so setup_logging can be exercised from scratch."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_third = {}
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        lg = logging.getLogger(name)
        saved_third[name] = (lg.handlers[:], lg.level, lg.propagate)
    monkeypatch.setattr(logging_setup, "_configured", None)
    root.handlers = []
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    for name, (handlers, level, propagate) in saved_third.items():
        lg = logging.getLogger(name)
        lg.handlers = handlers
        lg.setLevel(level)
        lg.propagate = propagate


class TestSetupLogging:
    def test_creates_root_handler_and_tames_third_party(self, fresh_logging):
        setup_logging("app")
        root = logging.getLogger()
        handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
        assert len(handlers) == 1
        assert isinstance(handlers[0].formatter, (ConsoleFormatter, JsonFormatter))
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert logging.getLogger(name).handlers == []
            assert logging.getLogger(name).propagate is True
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING

    def test_idempotent(self, fresh_logging):
        setup_logging("app")
        count = len(logging.getLogger().handlers)
        setup_logging("worker")
        assert len(logging.getLogger().handlers) == count

    def test_adopts_existing_plain_stream_handler(self, fresh_logging):
        # dramatiq basicConfigs a plain StreamHandler (its multiprocess pipe)
        # before the entrypoint imports us — adopt it, never add a second one.
        existing = logging.StreamHandler(io.StringIO())
        logging.getLogger().addHandler(existing)
        setup_logging("worker")
        # pytest's own LogCaptureHandler (a StreamHandler subclass) comes and goes
        # on the root logger per test phase — assert on plain StreamHandlers only.
        plain = [h for h in logging.getLogger().handlers
                 if type(h) is logging.StreamHandler]
        assert plain == [existing]
        assert isinstance(existing.formatter, (ConsoleFormatter, JsonFormatter))

    def test_log_level_normalized_case_insensitively(self, fresh_logging, monkeypatch):
        monkeypatch.setattr(settings, "log_level", "debug")
        setup_logging("app")
        assert logging.getLogger().level == logging.DEBUG

    def test_invalid_log_level_falls_back_to_info(self, fresh_logging, monkeypatch):
        monkeypatch.setattr(settings, "log_level", "nonsense")
        setup_logging("app")
        assert logging.getLogger().level == logging.INFO

    def test_json_format_selected(self, fresh_logging, monkeypatch):
        monkeypatch.setattr(settings, "log_format", "json")
        setup_logging("app")
        handler = next(h for h in logging.getLogger().handlers
                       if type(h) is logging.StreamHandler)
        assert isinstance(handler.formatter, JsonFormatter)


# ── RequestContextMiddleware ──────────────────────────────────────────────────

def _build_app(stamp_user: bool = False) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"correlation_id": log_context.correlation_id(),
                "context": log_context.get()}

    @app.get("/static/thing.css")
    def static_thing():
        return {"correlation_id": log_context.correlation_id()}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaput")

    if stamp_user:
        class _StampUser(BaseHTTPMiddleware):
            """The SessionMiddleware pattern: add() from inside BaseHTTPMiddleware."""
            async def dispatch(self, request, call_next):
                log_context.add(user_id="analyst-1")
                return await call_next(request)

        app.add_middleware(_StampUser)
    app.add_middleware(RequestContextMiddleware)  # added last → outermost
    return app


@pytest.fixture()
def access_capture():
    logger = logging.getLogger("app.access")
    cap = _Capture("app")
    saved_level = logger.level
    logger.addHandler(cap)
    logger.setLevel(logging.INFO)
    yield cap
    logger.removeHandler(cap)
    logger.setLevel(saved_level)


class TestRequestContextMiddleware:
    def test_generates_and_echoes_request_id(self, access_capture):
        resp = TestClient(_build_app()).get("/ping")
        request_id = resp.headers["x-request-id"]
        uuid.UUID(request_id)  # generated ids are uuid-shaped
        assert resp.json()["correlation_id"] == request_id  # endpoint saw the bound id
        record = access_capture.records[-1]
        assert record.correlation_id == request_id
        assert record.method == "GET"
        assert record.path == "/ping"
        assert record.status == 200
        assert record.duration_ms >= 0

    def test_inbound_request_id_honored(self):
        resp = TestClient(_build_app()).get(
            "/ping", headers={"X-Request-ID": "proxy-abc-123"})
        assert resp.headers["x-request-id"] == "proxy-abc-123"
        assert resp.json()["correlation_id"] == "proxy-abc-123"

    def test_static_paths_skipped(self, access_capture):
        resp = TestClient(_build_app()).get("/static/thing.css")
        assert "x-request-id" not in resp.headers
        assert resp.json()["correlation_id"] is None
        assert access_capture.records == []

    def test_add_inside_base_http_middleware_reaches_access_line(self, access_capture):
        TestClient(_build_app(stamp_user=True)).get("/ping")
        assert access_capture.records[-1].user_id == "analyst-1"

    def test_unhandled_exception_logged_as_500_and_reraised(self, access_capture):
        with pytest.raises(RuntimeError):
            TestClient(_build_app()).get("/boom")
        assert access_capture.records[-1].status == 500
