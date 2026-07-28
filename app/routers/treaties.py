"""Treaty routes — the EDM-level treaty Excel export (spec 004 US2, FR-024).

One authenticated **GET** returning a file download built from **stored**
treaty detail (``treaty_service.build_treaty_workbook``) — no Risk Modeler
call on the request path (Article 11), no state change (no CSRF), no row
scoping (Article 6).
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import Response

from app.services import edm_service, treaty_service

router = APIRouter()

_XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument"
               ".spreadsheetml.sheet")


def _safe_filename(name: str) -> str:
    """A header-safe download stem: drop quote/control/path characters an EDM
    name could carry; never empty."""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name or "").strip()
    return cleaned or "edm"


@router.get("/edms/{edm_id}/treaties.xlsx")
def export_treaties(edm_id: str):
    """Download the EDM's full treaty set as one ``.xlsx`` (FR-024/SC-004).
    Reads stored detail only; a treaty set not yet backfilled exports as an
    empty (header-only) workbook rather than erroring."""
    edm = edm_service.get_edm(edm_id)
    if edm is None:
        return Response(status_code=404, content="That EDM does not exist.")
    data = treaty_service.build_treaty_workbook(edm_id=edm_id)
    filename = f"{_safe_filename(edm.name)}-treaties.xlsx"
    return Response(
        content=data,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
