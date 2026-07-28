"""Treaty service — EDM-level treaty detail: worker-side upsert + read + export (US2).

``irp_treaty`` is a thin identity/lineage record plus a **JSON snapshot cache**
column (``attributes`` — research R2): the ``backfill_edm_detail`` worker stores
Risk Modeler's full treaty attribute map verbatim (the documented ``search
treaties`` row — treatyType/attachmentLevel/attachmentPoint/…) and stamps
``as_of`` (FR-052); the web layer only ever reads the stored snapshot. The
upsert is **idempotent** on ``UNIQUE(edm_id, irp_id)`` with an ``(edm_id,
name)`` fallback (treaty names are unique within an EDM and analyses reference
treaties by name, DATA_MODEL §5) — a re-backfill overwrites in place (FR-004)
and prunes (soft-deletes) rows RM's enumeration no longer returns.

``build_treaty_workbook`` is the FR-024/R5 Excel export: a standard ``.xlsx``
built in-process (openpyxl) from **stored** detail only — one row per treaty,
columns = the union of attribute keys across the set. **No Risk Modeler call.**

Read-only this iteration (FR-025): no create/edit (the §5 ``create_treaty``
pass-through is a later concern). No row scoping anywhere (Article 6).
"""

from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.services._common import (
    _json,
    _parse_json_dict,
    _snapshot_prune,
    _snapshot_upsert,
    _txn,
    _uid,
    _utcnow,
)
from db import execute


def _humanize_key(key: str) -> str:
    """A Risk Modeler camelCase attribute key as a display label:
    ``occurrenceLimit`` → ``Occurrence Limit``."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ",
                    str(key)).replace("_", " ")
    return spaced[:1].upper() + spaced[1:]


def _display_value(value: Any) -> Any:
    """An RM attribute value shaped for DISPLAY: a sub-object collapses to its
    human label — ``code`` first (currency ``{code: USD}``), else the first
    non-empty ``*Name`` key (cedant ``{cedantId, cedantName}`` → the name, not
    the "id, name" values join) — and a list of sub-objects to a comma-joined
    label list (lobs → ``Lend, Prop``, never raw JSON). Scalars pass through
    untouched; the template owns number/boolean formatting."""
    if isinstance(value, dict):
        code = value.get("code")
        if isinstance(code, str) and code.strip():
            return code
        named = next((v for k, v in value.items()
                      if k.lower().endswith("name")
                      and isinstance(v, str) and v.strip()), None)
        if named is not None:
            return named
        scalars = [str(v) for v in value.values()
                   if isinstance(v, (str, int, float)) and str(v).strip()]
        return ", ".join(scalars) or None
    if isinstance(value, (list, tuple)):
        parts = [str(p) for p in (_display_value(v) for v in value)
                 if p not in (None, "")]
        return ", ".join(parts) or None
    return value


@dataclass
class TreatyRow:
    """One treaty on an EDM with its parsed snapshot (``None`` ⇒ not yet
    backfilled → the caller renders the graceful empty state)."""
    id: str
    edm_id: str
    name: str
    irp_id: str | None
    attributes: dict | None
    as_of: Any

    def attribute_items(self) -> list[tuple[str, Any]]:
        """The full attribute set as (display label, display value) pairs for
        the expanded grid (FR-021) — RM's camelCase keys humanized, sub-object/
        list values collapsed to their labels, RM's internal ``uri`` dropped.
        The Excel export does NOT use this — it stays verbatim."""
        return [(_humanize_key(k), _display_value(v))
                for k, v in (self.attributes or {}).items()
                if k.lower() != "uri"]


# The two in-place overwrite paths of the idempotent upsert — same pattern as
# portfolio_service: irp_id match primary (UNIQUE(edm_id, irp_id)), name match
# fallback for a row first written without its RM id (data-model §3).
_UPDATE_BY_IRP = """
    UPDATE irp_treaty
    SET name = :name, attributes = :snap, as_of = :asof, updated_at = :now
    WHERE edm_id = :edm AND irp_id = :irp
"""
_UPDATE_BY_NAME = """
    UPDATE irp_treaty
    SET irp_id = :irp, attributes = :snap, as_of = :asof, updated_at = :now
    WHERE edm_id = :edm AND name = :name
"""
_INSERT = """
    INSERT INTO irp_treaty (id, edm_id, name, irp_id, attributes, as_of,
        inserted_at, updated_at)
    VALUES (:id, :edm, :name, :irp, :snap, :asof, :now, :now)
"""


def upsert_treaty_detail(*, edm_id: Any, irp_id: str | None, name: str,
                         attributes: dict, as_of: Any, conn=None) -> None:
    """Worker-side (``backfill_edm_detail``). Insert the ``irp_treaty`` row or
    OVERWRITE ``attributes`` (verbatim JSON) + ``as_of`` in place — never a
    duplicate (R2/FR-004). Runs in the caller's transaction when ``conn`` is
    given, else in its own short one (Article 7)."""
    params = {
        "id": str(uuid.uuid4()), "edm": str(edm_id),
        "irp": (str(irp_id) if irp_id is not None else None), "name": name,
        "snap": _json(attributes), "asof": as_of, "now": _utcnow(),
    }
    with _txn(conn) as working:
        _snapshot_upsert(working, params, update_by_irp=_UPDATE_BY_IRP,
                         update_by_name=_UPDATE_BY_NAME, insert=_INSERT)


def prune_missing(*, edm_id: Any, seen: list[tuple[str | None, str]],
                  now: Any, conn=None) -> int:
    """Worker-side (``backfill_edm_detail``), only after a SUCCESSFUL treaty
    enumeration: soft-delete this EDM's rows RM no longer returned (deleted
    RM-side) and resurrect pruned rows it returned again. ``seen`` is the
    enumerated (irp_id, name) set. Returns the rows pruned."""
    with _txn(conn) as working:
        return _snapshot_prune(working, table="irp_treaty", edm_id=edm_id,
                               seen=seen, now=now)


def list_treaties(*, edm_id: Any) -> list[TreatyRow]:
    """Every treaty on an EDM (read model), each with its parsed ``attributes``
    (``None`` → graceful empty) for the expand/collapse view (FR-020/FR-021).
    Read-only (FR-025); no row scoping (Article 6)."""
    rows = execute(
        "SELECT id, edm_id, name, irp_id, attributes, as_of "
        "FROM irp_treaty WHERE edm_id = :e AND deleted_at IS NULL "
        "ORDER BY name",
        {"e": str(edm_id)}, connection="WORKBENCH")
    return [TreatyRow(
        id=_uid(r["id"]), edm_id=_uid(r["edm_id"]), name=r["name"],
        irp_id=r["irp_id"],
        attributes=_parse_json_dict(r["attributes"], "treaty attributes"),
        as_of=r["as_of"]) for r in rows]


def _cell(value: Any) -> Any:
    """An attribute value as an .xlsx-safe cell: scalars pass through; dicts/
    lists (currency/cedant/producer are objects in RM's schema) serialize to
    JSON text; None stays empty."""
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return json.dumps(value)


def build_treaty_workbook(*, edm_id: Any) -> bytes:
    """FR-024/R5: a standard ``.xlsx`` over the EDM's treaty set — one row per
    treaty; columns = 'Treaty'/'Treaty Id' identity + the UNION of attribute
    keys across the set in first-seen order (a wide/heterogeneous set exports
    cleanly; a treaty missing a key gets an empty cell). Reads STORED detail
    only — **no Risk Modeler call** (Article 11). Returns the workbook bytes
    for the router to stream as a download."""
    from openpyxl import Workbook  # noqa: PLC0415 — confine the dep here (R5)

    treaties = list_treaties(edm_id=edm_id)
    columns: list[str] = []
    seen: set[str] = set()
    for t in treaties:
        for key in (t.attributes or {}):
            if key not in seen:
                seen.add(key)
                columns.append(key)

    wb = Workbook()
    ws = wb.active
    ws.title = "Treaties"
    ws.append(["Treaty", "Treaty Id", *columns])
    for t in treaties:
        attrs = t.attributes or {}
        ws.append([t.name, t.irp_id, *(_cell(attrs.get(k)) for k in columns)])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = ["TreatyRow", "upsert_treaty_detail", "prune_missing",
           "list_treaties", "build_treaty_workbook"]
