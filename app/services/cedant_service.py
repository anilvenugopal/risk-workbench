"""The admin-maintained cedant list (issue 129). A submission references one
cedant by id, so a rename shows on every submission."""

from __future__ import annotations

from dataclasses import dataclass

from app.services._common import _uid
from db import execute


@dataclass(frozen=True)
class Cedant:
    id: str
    name: str
    is_active: bool


def list_cedants(*, include_inactive: bool) -> list[Cedant]:
    rows = execute(
        "SELECT id, name, is_active FROM cedant"
        + ("" if include_inactive else " WHERE is_active = 1")
        + " ORDER BY name",
        {}, connection="WORKBENCH",
    )
    return [Cedant(id=_uid(row["id"]), name=row["name"], is_active=bool(row["is_active"]))
            for row in rows]
