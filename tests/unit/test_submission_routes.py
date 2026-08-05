"""Route tests for submission create/edit — the HTTP surface only.

Service behavior lives in ``test_submission_service.py``. These cover what only
the route decides:

  • required-field marking on ``GET /submissions/new`` (CR4);
  • per-field validation messages on ``POST /submissions``, with input preserved;
  • treaty-year range rejection, and a blank year filled from the inception date
    when the analyst never touches the field (CR5);
  • the 303 to the new deal, and CSRF rejection writing nothing;
  • the two typeahead menus, including the AND-combined "links to" search (CR7/CR8);
  • ``/submissions/new`` still resolving ahead of ``/submissions/{submission_id}``.

Harness: TestClient over the real router against the fixture SQLite engine
(``test_name_check_routes.py`` pattern, minus the monkeypatched services — these
tests want the real writes).
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import submission_service
from db import execute, execute_scalar


@pytest.fixture()
def client(iteration1_db) -> TestClient:
    """Router under test, with the fixture's Analyst A as the logged-in user."""
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import submissions
    from app.services.auth_service import CurrentUser

    user = CurrentUser(
        id=iteration1_db.user_a, email="analyst.a@example.com",
        display_name="Analyst A", session_id="s", role_codes=["analyst"],
        is_admin=False, must_change_password=False, entra_oid=None,
        is_active=True)

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.user = user
            return await call_next(request)

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(submissions.router)
    test_client = TestClient(app, follow_redirects=False)
    test_client.db = iteration1_db
    return test_client


def _csrf() -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def _payload(**overrides) -> dict:
    form = {
        "name": "TY2604_AmericanFamily",
        "cedant_name": "American Family Mutual",
        "treaty_type_code": "cat_xol",
        "inception_date": "2026-04-01",
        "treaty_year": "",
        "directory_path": "",
        "links_to_submission_id": "",
        "csrf_token": _csrf(),
    }
    form.update(overrides)
    return form


def _count() -> int:
    return execute_scalar("SELECT COUNT(*) FROM submission", {},
                          connection="WORKBENCH")


# ── CR4: required marking + per-field errors ─────────────────────────────────

def test_new_form_marks_the_required_fields(client):
    body = client.get("/submissions/new").text
    assert "wf-fields__legend" in body
    # Four required fields, each carrying its own marker.
    assert body.count('class="wf-field__req" title="Required"') == 4


def test_missing_name_returns_a_message_on_that_field(client):
    res = client.post("/submissions", data=_payload(name="   "))
    assert res.status_code == 200
    assert "Enter a name for this submission." in res.text
    assert "One field needs attention" in res.text
    assert _count() == 0


def test_validation_failure_preserves_what_the_analyst_typed(client):
    res = client.post("/submissions", data=_payload(
        name="", cedant_name="Zephyr Mutual", directory_path=r"\\share\deals\z"))
    assert "Zephyr Mutual" in res.text
    assert r"\\share\deals\z" in res.text


def test_several_bad_fields_are_all_reported(client):
    res = client.post("/submissions", data=_payload(
        name="", cedant_name="", inception_date=""))
    assert "3 fields need attention" in res.text
    assert "Enter a name for this submission." in res.text
    assert "Enter a cedant." in res.text
    assert "Enter an inception date." in res.text


def test_unparseable_inception_date_is_reported_as_invalid_not_missing(client):
    res = client.post("/submissions", data=_payload(inception_date="2026-13-45"))
    assert "Enter a valid date." in res.text
    assert _count() == 0


# ── CR5: treaty year ─────────────────────────────────────────────────────────

def test_blank_treaty_year_is_filled_from_the_inception_date(client):
    res = client.post("/submissions", data=_payload(
        inception_date="2026-04-01", treaty_year=""))
    assert res.status_code == 303
    sid = res.headers["location"].rsplit("/", 1)[-1]
    assert submission_service.get_submission(sid).treaty_year == 2026


def test_an_entered_treaty_year_is_kept(client):
    res = client.post("/submissions", data=_payload(
        name="Dec incept", inception_date="2026-12-15", treaty_year="2027"))
    sid = res.headers["location"].rsplit("/", 1)[-1]
    assert submission_service.get_submission(sid).treaty_year == 2027


@pytest.mark.parametrize("bad_year", ["1899", "3000", "not-a-year"])
def test_treaty_year_outside_the_allowed_range_is_rejected(client, bad_year):
    res = client.post("/submissions", data=_payload(treaty_year=bad_year))
    assert res.status_code == 200
    assert "Enter a year between 1900 and 2999." in res.text
    assert _count() == 0


# ── Create: redirect, CSRF, duplicate warning ────────────────────────────────

def test_valid_create_redirects_to_the_new_deal(client):
    res = client.post("/submissions", data=_payload())
    assert res.status_code == 303
    sid = res.headers["location"].rsplit("/", 1)[-1]
    assert res.headers["location"] == f"/submissions/{sid}"
    assert submission_service.get_submission(sid) is not None


def test_bad_csrf_token_writes_nothing(client):
    res = client.post("/submissions", data=_payload(csrf_token="forged"))
    assert res.status_code == 303
    assert res.headers["location"] == "/submissions/new"
    assert _count() == 0


def test_look_alike_creates_only_after_confirming(client):
    client.post("/submissions", data=_payload())
    res = client.post("/submissions", data=_payload())
    assert res.status_code == 200 and "dup-warn" in res.text
    assert _count() == 1
    confirmed = client.post("/submissions", data=_payload(confirmed="1"))
    assert confirmed.status_code == 303
    assert _count() == 2


# ── CR7/CR8: the two typeahead menus ─────────────────────────────────────────

def test_cedant_suggest_renders_menu_options(client):
    client.post("/submissions", data=_payload(cedant_name="American Family Mutual"))
    body = client.get("/submissions/cedant-suggest?cedant_name=fam").text
    assert 'data-value="American Family Mutual"' in body


def test_cedant_suggest_shows_the_empty_state_for_a_new_cedant(client):
    body = client.get("/submissions/cedant-suggest?cedant_name=Zephyr").text
    assert "No matching cedant yet" in body


def test_cedant_suggest_renders_nothing_for_an_empty_term(client):
    assert client.get("/submissions/cedant-suggest?cedant_name=").text.strip() == ""


def test_link_suggest_ands_the_terms_and_shows_deal_context(client):
    client.post("/submissions", data=_payload(
        name="TY2506_AmericanFamily", cedant_name="American Family Mutual",
        inception_date="2025-06-01"))
    client.post("/submissions", data=_payload(
        name="TY2501_AmericanNational", cedant_name="American National",
        inception_date="2025-01-01"))
    body = client.get("/submissions/link-suggest?links_to_search=american+fam").text
    assert "TY2506_AmericanFamily" in body
    assert "TY2501_AmericanNational" not in body
    assert "Cat XoL" in body  # each row carries cedant · treaty type · inception


def test_link_suggest_excludes_the_submission_being_edited(client):
    res = client.post("/submissions", data=_payload(name="Sole Match Deal"))
    sid = res.headers["location"].rsplit("/", 1)[-1]
    assert "Sole Match Deal" in client.get(
        "/submissions/link-suggest?links_to_search=Sole+Match").text
    assert "Sole Match Deal" not in client.get(
        f"/submissions/link-suggest?links_to_search=Sole+Match&links_to_exclude={sid}"
    ).text


def test_create_stores_the_chosen_link_and_the_detail_page_shows_its_name(client):
    first = client.post("/submissions", data=_payload(name="TY2506_AmericanFamily",
                                                      inception_date="2025-06-01"))
    target = first.headers["location"].rsplit("/", 1)[-1]
    second = client.post("/submissions", data=_payload(
        name="TY2606_AmericanFamily", links_to_submission_id=target))
    sid = second.headers["location"].rsplit("/", 1)[-1]
    assert submission_service.get_submission(sid).links_to_submission_id == target

    detail = client.get(f"/submissions/{sid}").text
    assert "links to" in detail
    # The uuid stays in the href; the analyst reads the linked deal's name.
    assert f'<a href="/submissions/{target}">\n          TY2506_AmericanFamily</a>' \
        in detail


def test_edit_form_prefills_the_linked_deal_by_name(client):
    first = client.post("/submissions", data=_payload(name="TY2506_AmericanFamily",
                                                      inception_date="2025-06-01"))
    target = first.headers["location"].rsplit("/", 1)[-1]
    second = client.post("/submissions", data=_payload(
        name="TY2606_AmericanFamily", links_to_submission_id=target))
    sid = second.headers["location"].rsplit("/", 1)[-1]

    body = client.get(f"/submissions/{sid}/edit").text
    assert "TY2506_AmericanFamily" in body
    assert f'name="links_to_exclude"\n                 value="{sid}"' in body \
        or f'value="{sid}"' in body


# ── Route ordering ───────────────────────────────────────────────────────────

def test_new_and_suggest_paths_resolve_ahead_of_the_detail_route(client):
    # A greedy /submissions/{submission_id} would swallow all three.
    assert "New submission" in client.get("/submissions/new").text
    assert client.get("/submissions/cedant-suggest?q=x").status_code == 200
    assert client.get("/submissions/link-suggest?q=x").status_code == 200


# ── Edit: self-link ──────────────────────────────────────────────────────────

def test_editing_a_deal_to_link_to_itself_is_rejected(client):
    res = client.post("/submissions", data=_payload(name="Self linker"))
    sid = res.headers["location"].rsplit("/", 1)[-1]
    submission = submission_service.get_submission(sid)
    edit = client.post(f"/submissions/{sid}", data=_payload(
        name="Self linker", links_to_submission_id=sid,
        updated_at=str(submission.updated_at), confirmed="1"))
    assert edit.status_code == 422
    assert "A submission cannot link to itself." in edit.text
    assert execute(
        "SELECT links_to_submission_id FROM submission WHERE id = :id",
        {"id": sid}, connection="WORKBENCH")[0]["links_to_submission_id"] is None
