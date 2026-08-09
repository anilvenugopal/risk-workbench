"""Route contract for GET /edms/{edm_id}/treaties.xlsx (spec 004 US2).

The services are monkeypatched: a missing EDM 404s, and a found EDM streams
the workbook bytes with the xlsx content type and a sanitized filename.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.routers import treaties
from app.services import edm_service, treaty_service

# ── route: GET /edms/{edm_id}/treaties.xlsx (the HTTP surface) ─────────────────────
# These are the database-free tier: the services are monkeypatched to pin their
# behavior, and the tests assert the route contract only. The workbook-builder
# flows that execute SQL live in tests/sqlserver/test_treaty_export.py.

def _route_client() -> TestClient:
    app = FastAPI()
    app.include_router(treaties.router)
    return TestClient(app)


def test_export_route_missing_edm_404s(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm", lambda edm_id: None)
    r = _route_client().get("/edms/nope/treaties.xlsx")
    assert r.status_code == 404


def test_export_route_streams_xlsx_with_safe_filename(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm",
                        lambda edm_id: SimpleNamespace(name='town"send: edm'))
    monkeypatch.setattr(treaty_service, "build_treaty_workbook",
                        lambda *, edm_id: b"xlsx-bytes")
    r = _route_client().get("/edms/edm-1/treaties.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    # quote/control/path characters are stripped from the download stem
    assert (r.headers["content-disposition"]
            == 'attachment; filename="town_send_ edm-treaties.xlsx"')
    assert r.content == b"xlsx-bytes"
