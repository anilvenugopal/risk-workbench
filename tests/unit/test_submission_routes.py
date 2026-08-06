"""Route tests for the submission list and create/edit — the HTTP surface only.

Service behavior lives in ``test_submission_service.py``. These cover what only
the route decides:

  • required-field marking on ``GET /submissions/new`` (CR4);
  • per-field validation messages on ``POST /submissions``, with input preserved;
  • treaty-year range rejection, and a blank year filled from the inception date
    when the analyst never touches the field (CR5);
  • the 303 to the new deal, and CSRF rejection writing nothing;
  • the two typeahead menus, including the AND-combined "links to" search (CR7/CR8);
  • the list's eight filters — which query parameter feeds which predicate, the
    values echoed back into the inputs, the CRM column, and the two empty states
    (CR1–CR3).

Harness: TestClient over the real router against the fixture SQLite engine
(``test_name_check_routes.py`` pattern, minus the monkeypatched services — these
tests want the real writes).
"""

from __future__ import annotations

import re
import uuid

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
    assert res.status_code == 422
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
    assert res.status_code == 422
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
    # Focus stays in the input while the analyst arrows through the menu, so the
    # highlighted row can only be announced by id.
    assert 'id="cedant-menu-opt-0"' in body
    assert 'role="option"' in body


def test_the_form_renders_the_service_minimum_into_both_typeaheads(
        client, monkeypatch):
    # One number reaches four places: the two hx-trigger filters that withhold the
    # request, and the two Alpine components that drop a stale menu. Moving the
    # service constant has to move all four, so the test moves it — asserting
    # against the current value would pass against a hardcoded literal too.
    monkeypatch.setattr(submission_service, "MIN_SUGGEST_TERM", 4)
    body = client.get("/submissions/new").text
    assert body.count("this.value.trim().length>=4") == 2
    assert body.count("minTerm: 4") == 2


def test_cedant_suggest_shows_the_empty_state_for_a_new_cedant(client):
    body = client.get("/submissions/cedant-suggest?cedant_name=Zephyr").text
    assert "No matching cedant." in body


def test_link_suggest_shows_the_empty_state_when_nothing_matches(client):
    body = client.get("/submissions/link-suggest?links_to_search=Zephyr").text
    assert "No matching submission." in body


def test_cedant_suggest_renders_nothing_for_an_empty_term(client):
    assert client.get("/submissions/cedant-suggest?cedant_name=").text.strip() == ""


def test_suggest_routes_render_nothing_for_a_one_character_term(client):
    # The form's hx-trigger filter withholds the request; the routes hold the same
    # minimum for a hand-built call.
    assert client.get("/submissions/cedant-suggest?cedant_name=A").text.strip() == ""
    assert client.get("/submissions/link-suggest?links_to_search=A").text.strip() == ""


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
    # An exclude that is not a UUID excludes nothing — bound as-is it would raise a
    # conversion error against submission.id on SQL Server.
    assert "Sole Match Deal" in client.get(
        "/submissions/link-suggest?links_to_search=Sole+Match&links_to_exclude=x"
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
    assert re.search(
        rf'<a href="/submissions/{target}">\s*TY2506_AmericanFamily\s*</a>', detail)


def test_create_stores_the_comma_separated_crm_ids(client):
    res = client.post("/submissions", data=_payload(
        name="TY2606_CrmAtCreate", crm_ids="CRM-1, CRM-2"))
    sid = res.headers["location"].rsplit("/", 1)[-1]
    assert {t.crm_id for t in submission_service.list_crm_ids(sid)} == {
        "CRM-1", "CRM-2"}
    assert "CRM-2" in client.get(f"/submissions/{sid}").text


def test_edit_form_prefills_the_linked_deal_by_name(client):
    first = client.post("/submissions", data=_payload(name="TY2506_AmericanFamily",
                                                      inception_date="2025-06-01"))
    target = first.headers["location"].rsplit("/", 1)[-1]
    second = client.post("/submissions", data=_payload(
        name="TY2606_AmericanFamily", links_to_submission_id=target))
    sid = second.headers["location"].rsplit("/", 1)[-1]

    body = client.get(f"/submissions/{sid}/edit").text
    # The picker posts the linked deal's id and shows its name.
    assert f'value="{target}"' in body
    assert "TY2506_AmericanFamily" in body
    # link-suggest must not offer this deal as its own link.
    assert f'value="{sid}"' in body


# ── Unknown link target (see UnknownLinkError) ───────────────────────────────

@pytest.mark.parametrize("link_value", [str(uuid.uuid4()), "not-a-uuid"])
def test_creating_with_an_unknown_link_target_is_rejected(client, link_value):
    before = _count()
    res = client.post("/submissions", data=_payload(
        name="Stale link", links_to_submission_id=link_value))
    assert res.status_code == 422
    assert "That deal was not found" in res.text
    assert _count() == before


@pytest.mark.parametrize("link_value", [str(uuid.uuid4()), "not-a-uuid"])
def test_editing_to_an_unknown_link_target_is_rejected(client, link_value):
    res = client.post("/submissions", data=_payload(name="Keeps its link"))
    sid = res.headers["location"].rsplit("/", 1)[-1]
    submission = submission_service.get_submission(sid)
    edit = client.post(f"/submissions/{sid}", data=_payload(
        name="Keeps its link", links_to_submission_id=link_value,
        updated_at=str(submission.updated_at), confirmed="1"))
    assert edit.status_code == 422
    assert "That deal was not found" in edit.text
    assert execute(
        "SELECT links_to_submission_id FROM submission WHERE id = :id",
        {"id": sid}, connection="WORKBENCH")[0]["links_to_submission_id"] is None


def test_an_id_that_is_not_a_uuid_is_not_found_rather_than_an_error(client):
    assert client.get("/submissions/not-a-uuid").status_code == 404
    assert client.get("/submissions/not-a-uuid/edit").status_code == 404


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


# ── List: search, filters, CRM column (CR1–CR3) ──────────────────────────────

def _two_american_deals(client) -> None:
    client.post("/submissions", data=_payload(
        name="TY2506_AmericanFamily", cedant_name="American Family Mutual",
        inception_date="2025-06-01"))
    client.post("/submissions", data=_payload(
        name="TY2501_AmericanNational", cedant_name="American National",
        inception_date="2025-01-01"))


def test_list_search_narrows_by_name(client):
    _two_american_deals(client)
    body = client.get("/submissions?q=american+fam").text
    assert "TY2506_AmericanFamily" in body
    assert "TY2501_AmericanNational" not in body


def test_list_filter_narrows_by_crm_id(client):
    client.post("/submissions", data=_payload(name="Tagged deal",
                                              crm_ids="CRM-4417"))
    client.post("/submissions", data=_payload(name="Untagged deal",
                                              inception_date="2026-07-01"))
    body = client.get("/submissions?crm_id=441").text
    assert "Tagged deal" in body and "Untagged deal" not in body


def test_list_owner_filter_offers_every_active_user(client):
    client.post("/submissions", data=_payload(name="Deal owned by A"))
    body = client.get("/submissions").text
    # Every active user is a menu row, so the analyst picks instead of typing.
    assert 'data-name="Analyst A"' in body
    assert 'data-name="Analyst B"' in body
    assert "Deal owned by A" in client.get("/submissions?owner=Analyst+A").text
    assert "Deal owned by A" not in client.get("/submissions?owner=Analyst+B").text


def test_list_shows_each_deals_crm_ids(client):
    res = client.post("/submissions", data=_payload(
        name="Three tags", crm_ids="CRM-1, CRM-2, CRM-3"))
    assert res.status_code == 303
    body = client.get("/submissions").text
    # First tag in full; the rest collapse into a hoverable count.
    assert "CRM-1" in body and "+2 more" in body


def test_list_renders_every_status_and_shows_the_selected_one(client):
    body = client.get("/submissions?status=COMPLETED").text
    assert 'data-code="CANCELLED">Cancelled</button>' in body
    # The trigger, not the menu row, carries the applied label.
    assert '<span x-ref="label">Completed</span>' in body


def test_list_echoes_every_filter_back_into_its_input(client):
    body = client.get(
        "/submissions?q=amfam&cedant=mutual&crm_id=CRM-9&status=ACTIVE"
        "&owner=Analyst+B&treaty_type=cat_xol&inception=2026-04-01"
        "&treaty_year=2026").text
    for name, value in (("q", "amfam"), ("cedant", "mutual"),
                        ("crm_id", "CRM-9"), ("owner", "Analyst B"),
                        ("inception", "2026-04-01"), ("treaty_year", "2026")):
        assert f'name="{name}"' in body and f'value="{value}"' in body
    for name, value in (("status", "ACTIVE"), ("treaty_type", "cat_xol")):
        assert f'name="{name}" value="{value}"' in body
    assert '<span x-ref="label">Active</span>' in body
    assert '<span x-ref="label">Cat XoL</span>' in body


def test_filtered_empty_list_offers_to_clear_the_filters(client):
    _two_american_deals(client)
    filtered = client.get("/submissions?q=nothing+matches+this").text
    assert "clear-filters" in filtered and 'href="/submissions"' in filtered


def test_empty_list_with_no_filters_reads_as_empty_not_filtered(client):
    body = client.get("/submissions").text
    assert "No submissions yet." in body and "clear-filters" not in body


# ── List: the #sub-list fragment and the pager ───────────────────────────────

def _fill_a_page_and_a_bit(client, extra: int = 2) -> None:
    """PAGE_SIZE + ``extra`` deals, each with its own name and cedant so no create
    trips the look-alike warning."""
    for i in range(submission_service.PAGE_SIZE + extra):
        client.post("/submissions", data=_payload(
            name=f"Paged deal {i:03d}", cedant_name=f"Paged cedant {i:03d}"))


def test_a_request_targeting_sub_list_gets_the_table_alone(client):
    """The filter form and the pager both target #sub-list, and htmx keeps only
    that. Rebuilding the filter bar and the nav shell for each keystroke is the
    cost this branch removes."""
    client.post("/submissions", data=_payload(name="Fragment deal"))
    fragment = client.get("/submissions?q=fragment",
                          headers={"HX-Request": "true", "HX-Target": "sub-list"}).text
    assert 'id="sub-list"' in fragment and "Fragment deal" in fragment
    # No filter bar, no owner menu, no shell — the three things the full page
    # renders and htmx throws away.
    assert 'class="filters"' not in fragment
    assert 'data-name="Analyst B"' not in fragment
    assert "<html" not in fragment

    whole_page = client.get("/submissions?q=fragment").text
    assert 'class="filters"' in whole_page and "Fragment deal" in whole_page


def test_the_list_shows_one_page_with_a_next_link(client):
    _fill_a_page_and_a_bit(client)
    first = client.get("/submissions").text
    assert first.count('class="data-row"') == submission_service.PAGE_SIZE
    assert "Page 1" in first and "page=2" in first
    assert 'rel="prev"' not in first

    second = client.get("/submissions?page=2").text
    assert second.count('class="data-row"') == 2
    assert "Page 2" in second and 'rel="prev"' in second and 'rel="next"' not in second


def test_a_short_list_shows_no_pager(client):
    client.post("/submissions", data=_payload(name="Only deal"))
    assert "Page 1" not in client.get("/submissions").text


def test_pager_links_carry_the_applied_filters(client):
    _fill_a_page_and_a_bit(client)
    body = client.get("/submissions?q=paged&status=ACTIVE").text
    assert "q=paged" in body and "status=ACTIVE" in body and "page=2" in body
    # My Submissions pages within itself rather than bouncing to All.
    assert 'href="/submissions/mine?' in client.get("/submissions/mine?page=2").text


def test_a_page_number_that_is_not_a_number_reads_the_first_page(client):
    _fill_a_page_and_a_bit(client)
    body = client.get("/submissions?page=abc").text
    assert "Page 1" in body
    assert body.count('class="data-row"') == submission_service.PAGE_SIZE


def test_my_submissions_keeps_the_filters_on_its_own_route(client):
    _two_american_deals(client)
    body = client.get("/submissions/mine?q=american+fam").text
    assert "TY2506_AmericanFamily" in body
    assert "TY2501_AmericanNational" not in body
    # The form and the clear-filters link post back to /submissions/mine, so
    # filtering inside "My Submissions" never bounces the analyst to All.
    assert 'action="/submissions/mine"' in body
