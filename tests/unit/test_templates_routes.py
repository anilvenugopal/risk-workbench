"""Route tests for the template/suite administration pages (US2, Phase 4).

Mirrors the minimal-app pattern used by test_templates_metadata_routes.py and
test_admin_routes.py: real Jinja2 templates, an injectable current_user, and
the sync worker to populate the reference cache the service layer validates
against.
"""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.auth.csrf import generate_csrf_token
from app.config import settings
from app.routers import templates
from app.services import template_service
from app.services.auth_service import CurrentUser
from app.templating import TEMPLATE_DIRS
from app.workers import metadata_jobs


def _make_user(**overrides) -> CurrentUser:
    defaults = dict(
        id="analyst-a", email="analyst@example.com", display_name="Analyst",
        session_id="sess-1", role_codes=["analyst"], is_admin=False,
        must_change_password=False, entra_oid=None, is_active=True,
    )
    defaults.update(overrides)
    return CurrentUser(**defaults)


def _make_admin(**overrides) -> CurrentUser:
    defaults = dict(
        id="admin-a", email="admin@example.com", display_name="Admin",
        session_id="sess-admin", role_codes=["admin"], is_admin=True,
        must_change_password=False, entra_oid=None, is_active=True,
    )
    defaults.update(overrides)
    return CurrentUser(**defaults)


class _InjectUser(BaseHTTPMiddleware):
    def __init__(self, app, user):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next):
        request.state.user = self._user
        return await call_next(request)


def _client(user: CurrentUser | None = None) -> TestClient:
    app = FastAPI()
    renderer = Jinja2Templates(directory=TEMPLATE_DIRS)
    renderer.env.globals["app_env"] = settings.app_env
    renderer.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    renderer.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    renderer.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = renderer
    app.add_middleware(_InjectUser, user=user or _make_admin())
    app.include_router(templates.router)
    return TestClient(app, follow_redirects=False)


def _flat(body: str) -> str:
    return re.sub(r"\s+", " ", body)


def _template_form(**overrides) -> dict:
    form = {
        "csrf_token": generate_csrf_token(),
        "name": "US Wind DLM",
        "analysis_profile_name": "RMS Default RL25",
        "event_rate_scheme_name": "RMS WS",
        "output_profile_name": "RMS Default Output",
        "min_loss_threshold": "1.00",
        "num_max_loss_event": "1",
        "treat_construction_occupancy_as_unknown": "1",
        "tags": "US; Wind",
    }
    form.update(overrides)
    return form


# ── Administration page: tabs keep suites and templates apart ─────────────────

def test_admin_page_has_separate_suites_and_templates_tabs(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_service.save_template(
        _values_for_service(), actor_id="admin-a",
    )

    body = _flat(_client().get("/templates").text)

    assert 'aria-label="Templates and suites"' in body
    assert "Suites" in body
    assert "Templates" in body
    # Default tab (suites) shows the suites table, not the templates table —
    # the two lists never appear together (user-directed 2026-08-19).
    assert "US Wind DLM" not in body


def test_templates_tab_shows_templates_not_suites(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(
        _values_for_service(), actor_id="admin-a",
    )
    template_service.save_suite(
        "US", [template_id], actor_id="admin-a",
    )

    body = _client().get("/templates/table?tab=templates").text

    assert "US Wind DLM" in body
    assert "DLM" in body


def test_suites_tab_shows_suites_not_templates(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(
        _values_for_service(), actor_id="admin-a",
    )
    template_service.save_suite(
        "US", [template_id], actor_id="admin-a",
    )

    body = _client().get("/templates/table?tab=suites").text

    assert ">US<" in body
    assert "US Wind DLM" not in body


def test_non_admin_sees_no_mutation_controls(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    body = _client(_make_user()).get("/templates").text

    assert "New template" not in body
    assert "New suite" not in body


def _values_for_service(**overrides):
    from decimal import Decimal

    from app.services.template_service import TemplateValues

    values = dict(
        name="US Wind DLM",
        analysis_profile_name="RMS Default RL25",
        output_profile_name="RMS Default Output",
        event_rate_scheme_name="RMS WS",
        min_loss_threshold=Decimal("1.00"),
        num_max_loss_event=1,
        franchise_deductible=False,
        treat_construction_occupancy_as_unknown=True,
    )
    values.update(overrides)
    return TemplateValues(**values)


# ── Template builder: create ───────────────────────────────────────────────────

def test_new_template_form_blocked_for_non_admin(iteration2_db, fake_irp):
    resp = _client(_make_user()).get("/templates/analysis-templates/new")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_direct_post_create_rejected_for_non_admin(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    resp = _client(_make_user()).post(
        "/templates/analysis-templates", data=_template_form(),
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert template_service.list_templates() == []


def test_create_template_redirects_to_templates_tab(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    resp = _client().post("/templates/analysis-templates", data=_template_form())

    assert resp.status_code == 303
    assert resp.headers["location"] == "/templates?tab=templates"
    [created] = template_service.list_templates()
    assert created["name"] == "US Wind DLM"
    assert created["tags"] == ["US", "Wind"]


def test_dlm_rejection_re_renders_form_naming_the_rule(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    resp = _client().post(
        "/templates/analysis-templates",
        data=_template_form(event_rate_scheme_name=""),
    )

    assert resp.status_code == 200
    assert "Event rate scheme is required for DLM analyses" in resp.text
    assert template_service.list_templates() == []


def test_duplicate_template_name_shows_form_error(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_service.save_template(_values_for_service())

    resp = _client().post("/templates/analysis-templates", data=_template_form())

    assert resp.status_code == 200
    assert "already exists" in resp.text
    assert len(template_service.list_templates()) == 1


def test_bad_csrf_on_create_does_not_save(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    resp = _client().post(
        "/templates/analysis-templates", data=_template_form(csrf_token="bad"),
    )

    assert resp.status_code == 303
    assert template_service.list_templates() == []


# ── Template detail / edit / delete ────────────────────────────────────────────

def test_template_detail_view_for_non_admin_has_no_edit_form(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    body = _client(_make_user()).get(
        f"/templates/analysis-templates/{template_id}"
    ).text

    assert "US Wind DLM" in body
    assert "<form" not in body


def test_template_detail_edit_form_for_admin_prefills_values(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    body = _client().get(f"/templates/analysis-templates/{template_id}").text

    assert 'value="US Wind DLM"' in body
    assert '<option value="RMS Default RL25" selected>' in body


def test_update_template_round_trip(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client().post(
        f"/templates/analysis-templates/{template_id}",
        data=_template_form(name="US Wind DLM Updated"),
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/templates?tab=templates"
    assert template_service.get_template(template_id)["name"] == "US Wind DLM Updated"


def test_delete_guard_names_referencing_suite(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    template_service.save_suite("US", [template_id])

    resp = _client().post(
        f"/templates/analysis-templates/{template_id}/delete",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 200
    assert "Template is used by: US" in resp.text
    assert template_service.get_template(template_id) is not None


def test_delete_template_succeeds_when_unreferenced(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client().post(
        f"/templates/analysis-templates/{template_id}/delete",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 303
    assert template_service.get_template(template_id) is None


def test_direct_delete_post_rejected_for_non_admin(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client(_make_user()).post(
        f"/templates/analysis-templates/{template_id}/delete",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 302
    assert template_service.get_template(template_id) is not None


# ── Duplicate-and-edit (P-12/FR-021) ────────────────────────────────────────────

def test_duplicate_template_route_redirects_to_the_copys_detail_page(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client().post(
        f"/templates/analysis-templates/{template_id}/duplicate",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 303
    [original, copy] = sorted(
        template_service.list_templates(), key=lambda t: t["name"],
    )
    assert copy["name"] == "US Wind DLM (copy)"
    assert resp.headers["location"] == f"/templates/analysis-templates/{copy['id']}"


def test_direct_duplicate_template_post_rejected_for_non_admin(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client(_make_user()).post(
        f"/templates/analysis-templates/{template_id}/duplicate",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 302
    assert len(template_service.list_templates()) == 1


def test_duplicate_surfaces_drift_validation_error_on_the_form(
    iteration2_db, fake_irp,
):
    # Saved while its profile was absent from the cache (pairing validation
    # short-circuits), then a sync lands the profile as DLM — the copy now
    # fails the scheme-required rule.
    template_id = template_service.save_template(
        _values_for_service(event_rate_scheme_name=None))
    metadata_jobs._sync_irp_metadata_body()

    resp = _client().post(
        f"/templates/analysis-templates/{template_id}/duplicate",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 200
    assert "Event rate scheme is required for DLM analyses" in resp.text
    assert len(template_service.list_templates()) == 1


def test_unresolved_badge_renders_on_detail_and_list(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM irp_model_profile WHERE name = 'RMS Default RL25'"
        )

    detail = _client().get(f"/templates/analysis-templates/{template_id}").text
    listing = _client().get("/templates/table?tab=templates").text

    assert "unresolved" in detail.lower()
    assert "unresolved" in listing.lower()


# ── Option fragments (scheme cascade, O17-9) ────────────────────────────────────

def test_scheme_options_fragment_prefills_exactly_one_match(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get(
        "/templates/analysis-templates/scheme-options?profile=RMS Default RL25"
    ).text

    assert '<option value="RMS WS" selected>' in body


def test_scheme_options_fragment_empty_for_blank_profile(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get(
        "/templates/analysis-templates/scheme-options?profile="
    ).text

    assert "Choose a scheme" in body
    assert "RMS WS" not in body


def test_scheme_options_populate_on_profile_change_alone(iteration2_db, fake_irp):
    """O17-9: the fragment the profile select's `hx-get` calls on `change`
    must return the matching scheme with no `q`/filter param at all — the
    cascade fires from picking a profile, never from typing into a filter
    box."""
    metadata_jobs._sync_irp_metadata_body()

    resp = _client().get(
        "/templates/analysis-templates/scheme-options",
        params={"profile": "RMS Default RL25"},
    )

    assert resp.status_code == 200
    assert '<option value="RMS WS" selected>' in resp.text


def test_edit_form_prefills_scheme_options_for_the_stored_profile(
    iteration2_db, fake_irp,
):
    """The builder's initial render shares the same query as the cascade
    fragment (T028), so an existing template's edit form already shows its
    scheme options — nothing waits on a profile re-selection."""
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    body = _client().get(f"/templates/analysis-templates/{template_id}").text

    assert '<option value="RMS WS" selected>' in body


def test_edit_form_keeps_a_saved_template_free_of_a_scheme(
    iteration2_db, fake_irp,
):
    """scheme_options() pre-selects a lone profile match on the profile-change
    cascade (FR-007), but a template saved without a scheme must reopen without
    one — otherwise the next save silently puts the scheme back."""
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service(
        name="US Wind HD", analysis_profile_name="RMS Default HD",
        event_rate_scheme_name=None,
    ))

    body = _client().get(f"/templates/analysis-templates/{template_id}").text

    assert "<option value=\"\" selected>Choose a scheme" in _flat(body)
    assert '<option value="RMS WS" selected>' not in body


def test_edit_form_labels_hidden_scheme_as_hidden_not_missing(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    template_service.set_scheme_visibility(20, False)

    body = _client().get(f"/templates/analysis-templates/{template_id}").text

    assert "RMS WS (hidden in Analysis Metadata)" in body
    assert "not found in Risk Modeler" not in body


def test_edit_form_shows_off_profile_scheme_with_its_real_peril_region(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE irp_event_rate_scheme "
            "SET peril_code = 'EQ', model_region_code = 'NAEQ' "
            "WHERE name = 'RMS WS'"
        )

    body = _client().get(f"/templates/analysis-templates/{template_id}").text

    assert "not found in Risk Modeler" not in body
    assert "RMS WS — EQ · NAEQ" in body


def test_edit_form_labels_scheme_missing_from_cache_as_not_found(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM irp_event_rate_scheme WHERE name = 'RMS WS'"
        )

    body = _client().get(f"/templates/analysis-templates/{template_id}").text

    assert "RMS WS (not found in Risk Modeler)" in body


# ── Suite builder ───────────────────────────────────────────────────────────────

def test_new_suite_form_has_search_box_over_the_template_picker(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_service.save_template(_values_for_service())

    body = _client().get("/templates/suites/new").text

    assert 'id="suite-item-filter"' in body
    assert "US Wind DLM" in body


def test_new_suite_form_blocked_for_non_admin(iteration2_db, fake_irp):
    resp = _client(_make_user()).get("/templates/suites/new")
    assert resp.status_code == 302


def test_create_suite_with_items_round_trip(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client().post("/templates/suites", data={
        "csrf_token": generate_csrf_token(), "name": "US", "template_ids": [template_id],
    })

    assert resp.status_code == 303
    assert resp.headers["location"] == "/templates?tab=suites"
    [suite] = template_service.list_suites()
    assert suite["name"] == "US"
    assert suite["item_count"] == 1


def test_suite_rejects_same_template_twice_via_form(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())

    resp = _client().post("/templates/suites", data={
        "csrf_token": generate_csrf_token(), "name": "US",
        "template_ids": [template_id, template_id],
    })

    assert resp.status_code == 200
    assert "only once" in resp.text
    assert template_service.list_suites() == []


def test_update_suite_item_add_and_remove_round_trip(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    first = template_service.save_template(_values_for_service())
    second = template_service.save_template(_values_for_service(
        name="US Wind HD", analysis_profile_name="RMS Default HD",
        event_rate_scheme_name=None,
    ))
    suite_id = template_service.save_suite("US", [first])

    add_resp = _client().post(f"/templates/suites/{suite_id}", data={
        "csrf_token": generate_csrf_token(), "name": "US",
        "template_ids": [first, second],
    })
    assert add_resp.status_code == 303
    assert template_service.get_suite(suite_id)["item_count"] == 2

    remove_resp = _client().post(f"/templates/suites/{suite_id}", data={
        "csrf_token": generate_csrf_token(), "name": "US", "template_ids": [second],
    })
    assert remove_resp.status_code == 303
    updated = template_service.get_suite(suite_id)
    assert updated["item_count"] == 1
    assert updated["items"][0]["template_id"] == second


def test_suite_detail_view_for_non_admin_has_no_edit_form(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    suite_id = template_service.save_suite("US", [template_id])

    body = _client(_make_user()).get(f"/templates/suites/{suite_id}").text

    assert "US Wind DLM" in body
    assert "<form" not in body


def test_delete_suite_succeeds_and_redirects(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    suite_id = template_service.save_suite("US", [template_id])

    resp = _client().post(
        f"/templates/suites/{suite_id}/delete", data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 303
    assert template_service.get_suite(suite_id) is None


def test_direct_suite_delete_post_rejected_for_non_admin(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    suite_id = template_service.save_suite("US", [template_id])

    resp = _client(_make_user()).post(
        f"/templates/suites/{suite_id}/delete", data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 302
    assert template_service.get_suite(suite_id) is not None


def test_duplicate_suite_route_redirects_to_the_copys_detail_page(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    suite_id = template_service.save_suite("US", [template_id])

    resp = _client().post(
        f"/templates/suites/{suite_id}/duplicate",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 303
    [original, copy] = sorted(
        template_service.list_suites(), key=lambda s: s["name"],
    )
    assert copy["name"] == "US (copy)"
    assert resp.headers["location"] == f"/templates/suites/{copy['id']}"


def test_direct_duplicate_suite_post_rejected_for_non_admin(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    suite_id = template_service.save_suite("US", [template_id])

    resp = _client(_make_user()).post(
        f"/templates/suites/{suite_id}/duplicate",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 302
    assert len(template_service.list_suites()) == 1


def test_duplicate_suite_surfaces_validation_error_on_the_form(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values_for_service())
    suite_id = template_service.save_suite("US", [template_id])
    # Simulate drift: the template soft-deleted out from under the suite —
    # the delete route's in-use guard normally prevents this.
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE analysis_template SET deleted_at = inserted_at"
        )

    resp = _client().post(
        f"/templates/suites/{suite_id}/duplicate",
        data={"csrf_token": generate_csrf_token()},
    )

    assert resp.status_code == 200
    assert "Every suite entry must reference a live analysis template" in resp.text
    assert len(template_service.list_suites()) == 1
