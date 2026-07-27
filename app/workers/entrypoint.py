"""Dramatiq worker entrypoint — the stable CLI target.

Run the background worker with::

    dramatiq app.workers.entrypoint

Importing this module runs ``loader.bootstrap()``: it sets the broker, auto-discovers
every ``app/workers/*_jobs.py`` module (registering their actors), and wires the
dispatch seam so the worker's own follow-on enqueues are delivered immediately. New job
families are picked up automatically — this target never has to change.

Kept separate from ``app/workers/__init__.py`` on purpose: the request/test path does
``from app.workers import dispatch`` and must stay free of Dramatiq. Only this module
and the web app's lifespan pull the full worker stack in.
"""

from __future__ import annotations

from app.logging_setup import setup_logging
from app.workers import loader

# Before bootstrap so discovery logs are already formatted. Dramatiq has
# basicConfig-ed the root logger in this fork by now — setup adopts its handler
# (keeping the line-atomic multiprocess pipe) rather than adding a second one.
setup_logging("worker")
loader.bootstrap()
