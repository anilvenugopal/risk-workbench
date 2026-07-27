"""Request/job-scoped log context — the fields every log line in a causal chain carries.

A tier binds a context once per unit of work (the app per HTTP request, a worker
per claimed ``rwb_job``, the poller per tracked ``irp_job``); ``ContextFilter``
(app/logging_setup.py) then stamps the bound fields onto every ``LogRecord``
emitted anywhere below — including ``db/``, uvicorn, and dramatiq records — so
no other module ever imports this to *benefit* from it.

The context is one mutable dict held in a ``ContextVar``. The dict is mutable on
purpose: ``BaseHTTPMiddleware`` runs its downstream in a child task whose context
is a *copy*, so a plain ``ContextVar.set`` inside it (e.g. in SessionMiddleware)
would be invisible to the outer middleware's access-log line. Mutating the shared
dict (``add``) crosses that task boundary; rebinding (``bind``) does not.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_context: ContextVar[dict[str, Any] | None] = ContextVar("rwb_log_context", default=None)


def bind(**fields: Any) -> Token:
    """Bind a fresh context for the current unit of work. Pair with ``clear``.

    ``None`` values are dropped (here and in ``add``): callers bind straight from
    nullable DB columns (e.g. a NULL ``correlation_id``), and an absent key beats
    ``correlation_id=None`` noise on every line."""
    return _context.set({k: v for k, v in fields.items() if v is not None})


def clear(token: Token) -> None:
    """Restore whatever context was bound before the matching ``bind``."""
    _context.reset(token)


def add(**fields: Any) -> None:
    """Merge fields into the current context in place. No-op when nothing is
    bound — a service that stamps context must still work when called outside
    any bound unit of work (scripts, tests)."""
    ctx = _context.get()
    if ctx is not None:
        ctx.update((k, v) for k, v in fields.items() if v is not None)


def get() -> dict[str, Any]:
    """A snapshot of the current context ({} when nothing is bound)."""
    ctx = _context.get()
    return dict(ctx) if ctx else {}


def correlation_id() -> str | None:
    """The bound chain id, if any — the default the job services persist."""
    ctx = _context.get()
    return ctx.get("correlation_id") if ctx else None


__all__ = ["bind", "clear", "add", "get", "correlation_id"]
