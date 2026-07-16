"""Dramatiq broker — the wake-up/dispatch signal for the rwb_job queue.

Article 10: the ``rwb_job`` SQL table is the queue *of record*; Dramatiq (Redis)
is only the low-latency mechanism that wakes the single worker. A worker actor
still claims its row atomically (``UPDATE ... WHERE status_code='pending'``), so a
lost or duplicated Redis message can never double-execute or lose work — the row
is authoritative and the poller's reconciler re-dispatches stragglers.

Import this module for its side effect of registering the broker as the Dramatiq
default; actors in ``app/workers/`` are declared against it.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.config import settings

redis_broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(redis_broker)

__all__ = ["redis_broker"]
