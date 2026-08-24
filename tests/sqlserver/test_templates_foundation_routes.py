"""Foundation tests for the template-suite router."""

from __future__ import annotations

import inspect

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import templates
from app.services.auth_service import CurrentUser


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = CurrentUser(
            user_id="test-user-id",
            email="analyst@example.com",
            display_name="Test Analyst",
            session_id="session-id",
            role_codes=["analyst"],
            is_admin=False,
            must_change_password=False,
            entra_oid=None,
        )
        return await call_next(request)


def _client() -> TestClient:
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory="app/templates")
    app.add_middleware(_InjectUser)
    app.include_router(templates.router)
    return TestClient(app)


def test_templates_placeholder_uses_template_suites_navigation(workbench_db):
    # The Phase-2 placeholder page (this test's original subject) was reworked
    # into the Phase-4 suites/templates administration page (T026), which
    # reads the template/suite tables — needs a registered WORKBENCH engine.
    response = _client().get("/templates")
    assert response.status_code == 200
    assert "Template Suites" in response.text
    assert "Analysis Metadata" in response.text


def test_templates_route_moved_out_of_shell_router():
    from app.routers import shell
    assert all(route.path != "/templates" for route in shell.router.routes)


def test_app_registers_templates_before_shell():
    from app import main
    source = inspect.getsource(main)
    assert source.index("app.include_router(template_routes.router)") < source.index(
        "app.include_router(shell.router)"
    )
