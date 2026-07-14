"""Worker bootstrap — auto-discover job actors and wire the dispatch seam.

Adding a new job family should mean **writing the actors, and nothing else**. This
module removes the two wiring sites that used to be required (and that failed
*silently* when forgotten):

  • **Discovery** — ``discover_jobs`` imports the broker (setting the Dramatiq global
    broker) then imports every ``app/workers/*_jobs.py`` module, which is what
    registers its ``@dramatiq.actor``s. A new ``foo_jobs.py`` is picked up with no CLI
    or Makefile change.
  • **Name-based dispatch** — ``rwb_job_type`` *is* the actor name, so ``_send``
    resolves the actor from the broker by name instead of a hand-maintained map.

Both the Dramatiq worker entrypoint (``app.workers.entrypoint``) and the web app's
lifespan call ``bootstrap()`` so their view of the actor set is identical. This module
imports Dramatiq, so it is imported **only** from those two startup paths — never from
the request/test import path (which reaches ``app.workers`` only for the lightweight
``dispatch`` seam).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

import dramatiq

from app.workers import dispatch

logger = logging.getLogger(__name__)


def discover_jobs() -> list[str]:
    """Import the broker, then every ``app/workers/*_jobs.py`` module so its actors
    register against the broker. Returns the discovered module names (for logging)."""
    from app.workers import broker  # noqa: PLC0415 — sets the global broker first
    _ = broker.redis_broker

    from app import workers as workers_pkg  # noqa: PLC0415
    discovered: list[str] = []
    for mod in pkgutil.iter_modules(workers_pkg.__path__):
        if mod.name.endswith("_jobs"):
            importlib.import_module(f"{workers_pkg.__name__}.{mod.name}")
            discovered.append(mod.name)
    logger.info("worker discovery: loaded job module(s) %s; actors=%s",
                discovered, sorted(dramatiq.get_broker().actors))
    return discovered


def _send(*, rwb_job_id: Any, rwb_job_type: str) -> None:
    """Dispatch by name: ``rwb_job_type`` == actor name. No hardcoded actor map, so a
    newly-discovered job type dispatches automatically. A miss is logged, not silent."""
    actor = dramatiq.get_broker().actors.get(rwb_job_type)
    if actor is None:
        logger.warning(
            "dispatch skipped: no actor registered for rwb_job_type %r — is its "
            "*_jobs module discovered?", rwb_job_type)
        return
    actor.send(rwb_job_id)


def setup_dispatch() -> None:
    """Wire the name-based Dramatiq sender into the enqueue seam."""
    dispatch.configure(_send)


def bootstrap() -> None:
    """Full worker/web startup: discover all job actors, then wire dispatch. Safe to
    call more than once (discovery re-imports are cached; ``configure`` is idempotent)."""
    discover_jobs()
    setup_dispatch()


__all__ = ["discover_jobs", "setup_dispatch", "bootstrap"]
