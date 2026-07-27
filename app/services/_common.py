"""Shared low-level helpers reused across the entity/job services and their workers.

Consolidated here to remove copy-paste drift. Kept intentionally tiny: a naive-UTC
stamp, NULL-safe JSON/id coercions, the caller-or-own transaction context manager
the poller and workers share, and the race-safe snapshot upsert the JSON-cache
services (irp_portfolio / irp_treaty) share.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from db import get_connection, is_unique_violation


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


def _snapshot_upsert(conn, params: dict, *, update_by_irp: str,
                     update_by_name: str, insert: str) -> None:
    """The idempotent JSON-snapshot upsert shared by ``portfolio_service`` and
    ``treaty_service`` (each keeps its own three SQL statements — same params:
    ``irp``/``name``/``edm``/``snap``/``asof``/``now``): overwrite by
    (edm_id, irp_id) first, fall back to the (edm_id, name) match for a row
    first written without its RM id, else insert. A concurrent backfill of the
    same EDM can win the insert race; absorb the UNIQUE(edm_id, irp_id)
    violation in a SAVEPOINT and overwrite in place — the constraint, not the
    pre-check, is the real dedup guarantee."""
    if params["irp"] is not None and conn.execute(
            text(update_by_irp), params).rowcount:
        return
    if conn.execute(text(update_by_name), params).rowcount:
        return
    try:
        with conn.begin_nested():
            conn.execute(text(insert), params)
    except Exception as exc:  # noqa: BLE001 — a UNIQUE race is a dedup hit
        if not is_unique_violation(exc):
            raise
        if params["irp"] is not None and conn.execute(
                text(update_by_irp), params).rowcount:
            return
        conn.execute(text(update_by_name), params)


__all__ = ["_utcnow", "_json", "_uid", "_txn", "_snapshot_upsert"]
