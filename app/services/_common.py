"""Shared low-level helpers reused across the entity/job services and their workers.

Consolidated here to remove copy-paste drift. Kept intentionally tiny: a naive-UTC
stamp, NULL-safe JSON/id coercions, the caller-or-own transaction context manager
the poller and workers share, and the race-safe snapshot upsert + stale-row prune
the JSON-cache services (irp_portfolio / irp_treaty) share.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text

from app.config import settings
from db import get_connection, is_unique_violation

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC timestamp — safe for DATETIME2 (no tz) and SQLite alike."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rm_base_url() -> str | None:
    """The Risk Modeler web UI's origin, ``https://<tenant>.<domain>`` — derived
    from ``RISK_MODELER_TENANT_NAME`` and the registrable domain of
    ``RISK_MODELER_BASE_URL`` (rms-ppe.com in the sandbox, rms.com in prod): the
    UI lives on the TENANT subdomain, never the API host. ``None`` when either
    is not configured (e.g. api-key auth deployments) — callers hide the link."""
    tenant = settings.risk_modeler_tenant_name.strip()
    api_host = urlsplit(settings.risk_modeler_base_url.strip()).hostname or ""
    domain = ".".join(api_host.rsplit(".", 2)[-2:]) if "." in api_host else ""
    if not tenant or not domain:
        return None
    return f"https://{tenant}.{domain}"


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
        if conn.execute(text(update_by_name), params).rowcount:
            return
        # Neither recovery update matched — e.g. SQL Server treats two NULL
        # irp_ids as equal, so a second id-less row violates the constraint yet
        # matches nothing by name. Re-raise: a loud, recoverable job failure
        # beats silently dropping the row.
        raise


def _snapshot_prune(conn, *, table: str, edm_id: Any,
                    seen: list[tuple[str | None, str]], now: Any) -> int:
    """Reconcile a snapshot table's row set against a SUCCESSFUL full Risk
    Modeler enumeration: soft-delete (``deleted_at``) this EDM's live rows the
    enumeration no longer returned (the entity was deleted in RM — its stale
    snapshot must not keep rendering with a fresh-looking ``as_of``), and clear
    ``deleted_at`` on pruned rows it returned again (RM-side delete/recreate).
    "Seen" mirrors ``_snapshot_upsert``'s identity resolution: an irp_id match
    OR a name match keeps the row. Never call this after a failed or partial
    enumeration — pruning is only valid against the full set. ``table`` is a
    module-supplied literal, never user input. Returns the rows pruned."""
    ids = [str(irp_id) for irp_id, _ in seen if irp_id is not None]
    names = [name for _, name in seen]
    params: dict[str, Any] = {"edm": str(edm_id), "now": now}
    params.update({f"i{i}": v for i, v in enumerate(ids)})
    params.update({f"n{i}": v for i, v in enumerate(names)})
    id_marks = [f":i{i}" for i in range(len(ids))]
    name_marks = [f":n{i}" for i in range(len(names))]

    kept: list[str] = []       # a live row stays when either identity matched
    stale: list[str] = []      # ... and is pruned when neither did
    if id_marks:
        kept.append(f"irp_id IN ({', '.join(id_marks)})")
        stale.append(f"(irp_id IS NULL OR irp_id NOT IN ({', '.join(id_marks)}))")
    if name_marks:
        kept.append(f"name IN ({', '.join(name_marks)})")
        stale.append(f"name NOT IN ({', '.join(name_marks)})")
    if kept:  # resurrect first so the caller's upserts see a live row to claim
        conn.execute(text(
            f"UPDATE {table} SET deleted_at = NULL, updated_at = :now "
            f"WHERE edm_id = :edm AND deleted_at IS NOT NULL "
            f"AND ({' OR '.join(kept)})"), params)
    stale_sql = ("".join(f" AND {c}" for c in stale)
                 if stale else "")  # RM returned nothing → prune every live row
    return conn.execute(text(
        f"UPDATE {table} SET deleted_at = :now, updated_at = :now "
        f"WHERE edm_id = :edm AND deleted_at IS NULL{stale_sql}"),
        params).rowcount


def _parse_json_dict(raw: Any, what: str) -> dict | None:
    """A stored JSON snapshot column parsed to a dict, ``None`` on NULL /
    unparseable / non-dict content — the read models render the graceful empty
    state instead of erroring (R2)."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("unparseable %s snapshot — rendering empty", what)
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["_utcnow", "_json", "_uid", "_txn", "_snapshot_upsert",
           "_snapshot_prune", "_parse_json_dict", "_rm_base_url"]
