"""Enqueue→worker dispatch seam (Article 10).

The SQL table **is** the queue: ``rwb_job_service.enqueue_rwb_job`` inserts the row
and the atomic claim is the real gate. Dispatching a Dramatiq message is only a
latency optimisation so an idle worker picks the row up immediately instead of on
the next poll; a missed dispatch is always recovered by the poller's reconciler.

Because dispatch is optional-for-correctness it lives behind an injection seam (the
same shape as ``irp_gateway.configure``): the real deployment wires the Dramatiq
sender at startup, while the unit tier leaves it unset (a no-op) and drives worker
bodies directly — so no test ever needs Redis.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class _Sender(Protocol):
    def __call__(self, *, rwb_job_id: str, rwb_job_type: str) -> None: ...


_sender: Callable[..., None] | None = None


def configure(sender: _Sender) -> None:
    """Install the active dispatcher (the real Dramatiq sender at app startup)."""
    global _sender
    _sender = sender


def reset() -> None:
    """Drop the active dispatcher (test teardown / stub-only runs)."""
    global _sender
    _sender = None


def dispatch(*, rwb_job_id: Any | None, rwb_job_type: str) -> None:
    """Notify the worker of a freshly-enqueued row. No-op when no sender is wired
    (unit tier) or when the enqueue was a dedup miss (``rwb_job_id is None``)."""
    if _sender is None or rwb_job_id is None:
        return
    try:
        _sender(rwb_job_id=str(rwb_job_id), rwb_job_type=rwb_job_type)
    except Exception:  # noqa: BLE001 — the reconciler is the safety net; never fail the request
        logger.exception("dispatch of rwb_job %s (%s) failed", rwb_job_id, rwb_job_type)


__all__ = ["configure", "reset", "dispatch"]
