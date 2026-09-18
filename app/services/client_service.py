"""The repository client list, read from ``dbo.Client`` in ``rwb_loss`` (spec
017 T-06).

Read only, over the ``LOSS`` connection, through the ``db`` safe path (Article
7); the Workbench never creates or edits a client (P-04). Every function fails
open: when the repository cannot be reached the list is ``None`` and the names
are ``{}``, so the form says the list is unavailable and the submission still
saves (FR-009).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.exc import SQLAlchemyError

from app.services._common import _in_clause
from db import execute
from db.errors import SQLServerError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Client:
    id: int
    name: str | None

    @property
    def label(self) -> str:
        return display(self.id, self.name)


def _read(sql: str, params: dict[str, Any]) -> list[dict] | None:
    try:
        return execute(sql, params, connection="LOSS")
    except (SQLServerError, SQLAlchemyError) as exc:
        logger.warning("client list unavailable: %s", exc)
        return None


def list_clients() -> list[Client] | None:
    """Every repository client, active and retired alike (spec 014 P-18), in
    name order; ``None`` when the repository cannot be read."""
    rows = _read(
        "SELECT ClientID, ClientName FROM dbo.Client ORDER BY ClientName, ClientID",
        {})
    if rows is None:
        return None
    return [Client(id=int(row["ClientID"]), name=row["ClientName"]) for row in rows]


def client_names(ids: Iterable[int]) -> dict[int, str | None]:
    """The names of the given client ids in one query; ``{}`` when the repository
    cannot be read or no id is given."""
    wanted = sorted({int(value) for value in ids})
    if not wanted:
        return {}
    clause, params = _in_clause("ClientID", wanted, "c")
    rows = _read(f"SELECT ClientID, ClientName FROM dbo.Client WHERE {clause}", params)
    if rows is None:
        return {}
    return {int(row["ClientID"]): row["ClientName"] for row in rows}


def display(client_id: int | None, name: str | None) -> str | None:
    """``"27 - Travelers Corporate Cat"``, or ``"27 (name unavailable)"`` when
    the repository could not be read (P-05)."""
    if client_id is None:
        return None
    return f"{client_id} - {name}" if name else f"{client_id} (name unavailable)"
