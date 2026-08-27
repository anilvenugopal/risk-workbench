"""Per-``rwb_job_type`` Dramatiq queue names (CR-004).

``rwb_actor`` pins each actor's ``queue_name`` to its own function name — no call
site ever sets ``queue_name`` explicitly, so ``queue_name == actor_name ==
rwb_job_type`` cannot drift. ``queue_names()`` reads the resulting list back off
the broker after ``discover_jobs()`` registers every ``*_jobs.py`` actor, so a new
job module is picked up automatically, with nothing to hand-maintain here.

Shell scripts and systemd tooling get the list via ``python -m app.workers.queues``
(one name per line) rather than hardcoding it.
"""

from __future__ import annotations

import sys

import dramatiq


def rwb_actor(fn=None, **kwargs):
    """``@dramatiq.actor``, with ``queue_name`` pinned to the function's own name."""
    def wrap(f):
        return dramatiq.actor(queue_name=f.__name__, **kwargs)(f)
    return wrap(fn) if fn else wrap


def queue_names() -> list[str]:
    """Every registered actor's name, sorted — one Dramatiq queue per job type."""
    from app.workers import loader  # noqa: PLC0415 — startup-only import boundary
    loader.discover_jobs()
    return sorted(dramatiq.get_broker().actors.keys())


def _main() -> None:
    for name in queue_names():
        print(name)


if __name__ == "__main__":
    sys.exit(_main())


__all__ = ["rwb_actor", "queue_names"]
