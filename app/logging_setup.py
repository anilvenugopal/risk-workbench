"""Shared logging setup — one module, three entry points (app / worker / poller).

Every process calls ``setup_logging("<process>")`` once at startup:

- ``app/main.py`` at module import — uvicorn configures its own logging *before*
  importing the app (verified on 0.49.0, including each ``--reload`` child), so
  this always runs last and wins;
- ``app/workers/entrypoint.py`` — dramatiq's fork has already ``basicConfig``-ed
  the root logger by then, hence adopt-or-create below;
- ``app/poller/run.py`` ``main()``.

Output is stdlib logging end-to-end: one root ``StreamHandler`` whose formatter
is either ``ConsoleFormatter`` (dev) or ``JsonFormatter`` (production / opt-in via
``LOG_FORMAT``), and whose ``ContextFilter`` stamps the bound ``app.log_context``
fields (correlation_id, rwb_job_id, …) onto every record — third-party records
(uvicorn, dramatiq, sqlalchemy, ``db/``) included, since they all propagate to
the same root handler.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app import log_context
from app.config import settings

_access_logger = logging.getLogger("app.access")

# LogRecord's own attribute names — bound context fields never overwrite these.
_RESERVED_RECORD_FIELDS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None))
) | {"message", "asctime", "process_name", "log_context"}


class ContextFilter(logging.Filter):
    """Stamps the process name and every bound log-context field onto the record.

    Attached to the root *handler* (not the logger) so it runs for propagated
    records from any module — logger-level filters only see records emitted
    through that logger itself.
    """

    def __init__(self, process_name: str) -> None:
        super().__init__()
        self._process_name = process_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.process_name = self._process_name
        ctx = log_context.get()
        record.log_context = ctx
        for key, value in ctx.items():
            if key not in _RESERVED_RECORD_FIELDS:
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; context fields flattened to top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "process": getattr(record, "process_name", None),
            "pid": record.process,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "log_context", None) or {})
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc_info"] = record.exc_text
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Readable single line: time, level, process:pid, logger, message, context."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, "%H:%M:%S"),
            f"{record.levelname:<7}",
            f"[{getattr(record, 'process_name', '-')}:{record.process}]",
            f"[{record.name}]",
            record.getMessage(),
        ]
        ctx = getattr(record, "log_context", None) or {}
        if ctx:
            parts.append(" ".join(f"{k}={v}" for k, v in ctx.items()))
        line = " ".join(parts)
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line


class RequestContextMiddleware:
    """Pure-ASGI (no starlette import) outermost middleware: binds the request's
    log context and emits one access-log line per request.

    - Correlation id: inbound ``X-Request-ID`` (reverse proxy) if present, else a
      fresh uuid4; echoed back as the ``X-Request-ID`` response header.
    - ``/static/*`` is skipped entirely (noise).
    - An exception escaping the inner app is logged as status 500 and re-raised —
      Starlette's ServerErrorMiddleware sits outside this one and renders the 500,
      so that response carries no ``X-Request-ID`` header (edge case, accepted).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"].startswith("/static/"):
            await self.app(scope, receive, send)
            return

        request_id = ""
        for key, value in scope.get("headers") or ():
            if key == b"x-request-id":
                request_id = value.decode("latin-1").strip()[:64]
                break
        if not request_id:
            request_id = str(uuid.uuid4())

        method = scope.get("method", "-")
        path = scope["path"]
        status: dict[str, int | None] = {"code": None}

        async def send_with_request_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        token = log_context.bind(correlation_id=request_id, method=method, path=path)
        started = time.monotonic()
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            status["code"] = 500
            raise
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            log_context.add(status=status["code"], duration_ms=duration_ms)
            _access_logger.info("%s %s -> %s", method, path, status["code"])
            log_context.clear(token)


_configured: str | None = None


def setup_logging(process_name: str) -> None:
    """Configure root logging for one process. Idempotent — the first caller wins
    (pytest imports ``app.main``, which calls this; later explicit calls no-op).

    Adopt-or-create: if the root already has a plain ``StreamHandler`` (dramatiq
    ``basicConfig``s one, pointed at its line-atomic multiprocess pipe, before the
    entrypoint imports us), keep it — swapping only formatter + filter — so worker
    output stays on dramatiq's pipe and nothing emits twice. Subclasses (e.g.
    pytest's capture handler) are deliberately not adopted.
    """
    global _configured
    if _configured is not None:
        return
    _configured = process_name

    level = getattr(logging, settings.log_level.upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    format_name = settings.log_format or ("json" if settings.is_production else "console")
    formatter = JsonFormatter() if format_name == "json" else ConsoleFormatter()

    root = logging.getLogger()
    handler = next((h for h in root.handlers if type(h) is logging.StreamHandler), None)
    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        root.addHandler(handler)
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter(process_name))
    root.setLevel(level)

    # Third-party loggers: strip private handlers so everything funnels through
    # the root handler (one format, context-stamped). Our access line replaces
    # uvicorn's (it carries the correlation id), hence WARNING.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


__all__ = [
    "ContextFilter",
    "JsonFormatter",
    "ConsoleFormatter",
    "RequestContextMiddleware",
    "setup_logging",
]
