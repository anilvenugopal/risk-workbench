"""Shared low-level helpers reused across the entity/job services and their workers.

Consolidated here to remove copy-paste drift. Kept intentionally tiny: a naive-UTC
stamp, NULL-safe JSON/id coercions, and the caller-or-own transaction context manager
the poller and workers share.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from db import get_connection


def _utcnow() -> datetime:
    """Naive UTC timestamp — safe for DATETIME2 (no tz) and SQLite alike."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _uid(value: Any) -> str | None:
    """Normalize a UUID/id to a lowercase string (``None`` passes through).

    App-generated ids come from ``uuid4()`` (lowercase); SQL Server's
    ``uniqueidentifier`` reads them back UPPERCASE. Lowercasing every id the
    service hands out keeps app-generated, bound, and read-back ids
    byte-identical, so Python-side equality (dedup sets, "is this the selected
    row?" checks, redirect URLs) is stable across both backends. SQL Server
    compares ``uniqueidentifier`` case-insensitively so lookups are unaffected,
    and the SQLite unit tier stores strings verbatim so this is a no-op there."""
    return None if value is None else str(value).lower()


@contextmanager
def _txn(conn):
    """Yield a working connection: reuse the caller's (no new transaction, so a worker/
    poller can span ``irp_job`` + ``rwb_job`` in one transaction) or open our own
    ``get_connection("WORKBENCH") + begin()`` when none was supplied."""
    if conn is not None:
        yield conn
    else:
        with get_connection("WORKBENCH") as owned:
            with owned.begin():
                yield owned


__all__ = ["_utcnow", "_json", "_uid", "_txn"]
