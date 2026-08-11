"""Route tests for ``GET /browse`` — the live shared-drive listing fragment.

  • file mode (the EDM/RDM import pages and the package modal) lists files with
    ``source_paths`` checkboxes;
  • ``dirs_only=1`` (the submission form's directory picker) lists folders only,
    offers "Use this folder", and keeps the mode across navigation;
  • a seed path that no longer resolves falls back to the root instead of the
    error state, which renders no navigation.

Harness: TestClient over the real router (``test_name_check_routes.py`` pattern)
plus the ``drive`` fixture's on-disk shared drive.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.auth_service import CurrentUser
        request.state.user = CurrentUser(
            id="analyst-1", email="analyst@example.com", display_name="Analyst",
            session_id="s", role_codes=["analyst"], is_admin=False,
            must_change_password=False, entra_oid=None, is_active=True)
        return await call_next(request)


def _client() -> TestClient:
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import shared_drive

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(shared_drive.router)
    return TestClient(app, follow_redirects=False)


# ── File mode (unchanged) ────────────────────────────────────────────────────

def test_file_mode_lists_files_with_checkboxes(drive):
    body = _client().get("/browse").text
    assert 'name="source_paths"' in body
    assert "edm1.bak" in body
    assert "deals" in body            # folders still navigate in file mode
    assert "Use this folder" not in body


# ── Directory-picker mode ────────────────────────────────────────────────────

def test_dirs_only_lists_folders_and_offers_the_pick_button(drive):
    body = _client().get("/browse?dirs_only=1").text
    assert "deals" in body
    assert "edm1.bak" not in body
    assert 'name="source_paths"' not in body
    assert "Use this folder" in body
    assert f'data-select-dir="{drive}"' in body


def test_dirs_only_survives_navigation(drive):
    body = _client().get(f"/browse?dirs_only=1&path={drive / 'deals'}").text
    assert "zephyr" in body
    # Both the folder link and "↑ Up" stay in directory-picker mode.
    assert body.count("dirs_only=1") == 2


def test_dirs_only_reports_a_folder_with_no_subfolders(drive):
    body = _client().get(f"/browse?dirs_only=1&path={drive / 'deals' / 'zephyr'}").text
    assert "This folder has no subfolders." in body


# ── Stale seed paths ─────────────────────────────────────────────────────────

def test_a_path_that_no_longer_exists_falls_back_to_the_root(drive):
    body = _client().get(f"/browse?path={drive / 'moved-away'}").text
    assert "drive-browse__error" not in body
    assert "edm1.bak" in body


def test_a_path_outside_the_root_falls_back_to_the_root(drive, tmp_path):
    body = _client().get(f"/browse?path={tmp_path / 'elsewhere'}").text
    assert "drive-browse__error" not in body
    assert "edm1.bak" in body


def test_no_shared_drive_configured_still_reports_the_error(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "shared_drive_root", "")
    body = _client().get("/browse").text
    assert "drive-browse__error" in body
    assert "No shared drive is configured" in body
