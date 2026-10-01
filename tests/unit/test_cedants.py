"""The cedant list at /cedants (issue 129), over the real router and
cedant_service against the fixture SQLite engine."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.auth.csrf import generate_csrf_token
from app.services import cedant_service
from app.services.auth_service import CurrentUser
from tests.unit.conftest import cedant_id


@pytest.fixture()
def client(iteration2_db) -> TestClient:
    from app.config import settings
    from app.routers import cedants
    from app.templating import TEMPLATE_DIRS

    user = CurrentUser(
        id=iteration2_db.user_a, email="analyst@example.com", display_name="Analyst",
        session_id="s", role_codes=["analyst"], is_admin=False,
        must_change_password=False, entra_oid=None, is_active=True)

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.user = user
            return await call_next(request)

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(cedants.router)
    return TestClient(app, follow_redirects=False)


def _post(client: TestClient, path: str, **form):
    return client.post(path, data={"csrf_token": generate_csrf_token(), **form})


def _names() -> dict[str, bool]:
    return {c.name: c.is_active for c in cedant_service.list_cedants(include_inactive=True)}


def test_the_page_lists_cedants_under_the_submissions_sidebar(client):
    cedant_id("Acme Re")
    cedant_id("Heritage Casualty", is_active=False)
    body = client.get("/cedants").text
    assert 'href="/submissions">List</a>' in body
    assert 'href="/cedants">Cedants</a>' in body
    assert "Acme Re" in body and "Heritage Casualty" in body
    assert "/reactivate" in body


def test_the_empty_list_says_so(client):
    assert "No cedants yet." in client.get("/cedants").text


def test_search_keeps_names_containing_every_word_in_any_order(client):
    cedant_id("American Family Mutual")
    cedant_id("American Re")
    cedant_id("Family_Re", is_active=False)

    body = client.get("/cedants", params={"q": "fam american"}).text
    assert "American Family Mutual" in body
    assert "American Re" not in body and "Family_Re" not in body
    assert 'value="fam american"' in body

    body = client.get("/cedants", params={"q": "y_r"}).text
    assert "Family_Re" in body and "American Family Mutual" not in body

    assert 'No cedants match "zurich"' in client.get("/cedants", params={"q": "zurich"}).text


def test_add_trims_the_name_and_refuses_a_case_insensitive_duplicate(client):
    assert _post(client, "/cedants", name="  Acme Re  ").status_code == 303
    assert _names() == {"Acme Re": True}

    response = _post(client, "/cedants", name="ACME RE")
    assert response.status_code == 422
    assert "A cedant named &#34;Acme Re&#34; already exists." in response.text
    assert 'value="ACME RE"' in response.text
    assert _names() == {"Acme Re": True}


def test_adding_an_inactive_cedants_name_says_to_reactivate_it(client):
    cedant_id("Heritage Casualty", is_active=False)
    response = _post(client, "/cedants", name="heritage casualty")
    assert response.status_code == 422
    assert "exists but is inactive — reactivate it instead." in response.text


@pytest.mark.parametrize("name, message", [
    ("   ", "Enter a cedant name."),
    ("x" * 256, "A cedant name is at most 255 characters."),
])
def test_add_refuses_a_blank_or_long_name(client, name, message):
    response = _post(client, "/cedants", name=name)
    assert response.status_code == 422
    assert message in response.text
    assert _names() == {}


def test_rename_shows_the_new_name_and_refuses_a_taken_one(client):
    acme = cedant_id("Acme Re")
    cedant_id("Northfield Mutual")

    assert _post(client, f"/cedants/{acme}/rename",
                 name="Acme Reinsurance").status_code == 303
    assert _names() == {"Acme Reinsurance": True, "Northfield Mutual": True}

    response = _post(client, f"/cedants/{acme}/rename", name="northfield mutual")
    assert response.status_code == 422
    assert "A cedant named &#34;Northfield Mutual&#34; already exists." in response.text
    assert 'value="northfield mutual"' in response.text
    assert _names() == {"Acme Reinsurance": True, "Northfield Mutual": True}


def test_rename_to_its_own_name_in_another_case_is_allowed(client):
    acme = cedant_id("Acme Re")
    assert _post(client, f"/cedants/{acme}/rename", name="ACME Re").status_code == 303
    assert _names() == {"ACME Re": True}


def test_deactivate_leaves_the_create_picker_and_reactivate_restores_it(client):
    from app.routers.submissions import _cedant_options

    acme = cedant_id("Acme Re")
    assert _post(client, f"/cedants/{acme}/deactivate").status_code == 303
    assert _names() == {"Acme Re": False}
    assert [c.id for c in _cedant_options(None)] == []

    assert _post(client, f"/cedants/{acme}/reactivate").status_code == 303
    assert [c.id for c in _cedant_options(None)] == [acme]


def test_a_bad_csrf_token_writes_nothing(client):
    response = client.post("/cedants", data={"csrf_token": "bad", "name": "Acme Re"})
    assert response.status_code == 303
    assert _names() == {}
