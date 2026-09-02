"""Analysis template and template-suite routes."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.routers._guards import require_admin
from app.services import rwb_job_service, template_service
from app.services._common import _rm_ui_root
from app.services.template_service import (
    TemplateInUseError,
    TemplateServiceError,
    TemplateValidationError,
    TemplateValues,
)
from app.workers import dispatch
from db import execute, execute_one

router = APIRouter()

# Metadata sync has no entity row to key its rwb_job to, so a fixed arbitrary
# requestor_id makes UNIQUE(requestor_type, requestor_id, rwb_job_type) treat
# every sync request as the same job head — at most one pending/running sync.
_METADATA_SYNC_REQUESTOR_ID = "00000000-0000-0000-0000-000000000009"

# rm_path is the tenant-relative path of this tab's Risk Modeler settings screen
# (joined to `_rm_ui_root()`, same tenant-subdomain rule as the EDM deep links
# in edm_service.py).
_METADATA_TABS = (
    {"key": "model-profiles", "label": "Model Profiles", "count_key": "model_profiles",
     "rm_path": "riskmodeler/datasources/model-settings/profiles"},
    {"key": "output-profiles", "label": "Output Profiles", "count_key": "output_profiles",
     "rm_path": "riskmodeler/datasources/model-settings/output"},
    {"key": "event-rate-schemes", "label": "Event Rate Schemes", "count_key": "event_rate_schemes",
     "rm_path": "riskmodeler/modelcomposer#event-rate-schemes"},
    {"key": "currencies", "label": "Currencies", "count_key": "currencies",
     "rm_path": "home/reference-data/currencies/currency"},
    {"key": "currency-schemes", "label": "Currency Schemes", "count_key": "currency_schemes",
     "rm_path": "home/reference-data/currencies/currency-schemes"},
)


def _templates(request: Request):
    return request.app.state.templates


def _page(request: Request, template: str, context: dict, *, status_code: int = 200):
    return _templates(request).TemplateResponse(
        request, template, context, status_code=status_code)


# ── Analysis Metadata (US1) ────────────────────────────────────────────────────

def _metadata_rows(tab: str, q: str) -> list[dict]:
    match = f"%{q.lower()}%"
    if tab == "output-profiles":
        return execute(
            """
            SELECT irp_id, name, rms_default
            FROM irp_output_profile
            WHERE LOWER(name) LIKE :q
            ORDER BY name
            """,
            {"q": match}, connection="WORKBENCH")
    if tab == "event-rate-schemes":
        return execute(
            """
            SELECT irp_id, name, peril_code, model_region_code,
                   model_version_code, is_hd, workbench_is_active
            FROM irp_event_rate_scheme
            WHERE LOWER(name) LIKE :q
               OR LOWER(COALESCE(peril_code, '')) LIKE :q
               OR LOWER(COALESCE(model_region_code, '')) LIKE :q
               OR LOWER(COALESCE(model_version_code, '')) LIKE :q
            ORDER BY name
            """,
            {"q": match}, connection="WORKBENCH")
    if tab == "currencies":
        return execute(
            """
            SELECT code, name, country_name, symbol
            FROM irp_currency
            WHERE LOWER(code) LIKE :q
               OR LOWER(name) LIKE :q
               OR LOWER(COALESCE(country_name, '')) LIKE :q
            ORDER BY code
            """,
            {"q": match}, connection="WORKBENCH")
    if tab == "currency-schemes":
        schemes = execute(
            """
            SELECT irp_id, name, code, anchor_currency_code, update_interval_days
            FROM irp_currency_scheme
            WHERE LOWER(name) LIKE :q
               OR LOWER(code) LIKE :q
            ORDER BY name
            """,
            {"q": match}, connection="WORKBENCH")
        vintages_by_scheme: dict[str, list[dict]] = {}
        for vintage in execute(
            """
            SELECT vintage, currency_scheme_code, effective_date
            FROM irp_currency_scheme_vintage
            ORDER BY effective_date DESC
            """,
            connection="WORKBENCH"):
            vintage["effective_date"] = str(vintage["effective_date"])[:10]
            vintages_by_scheme.setdefault(
                vintage["currency_scheme_code"], []).append(vintage)
        for scheme in schemes:
            scheme["vintages"] = vintages_by_scheme.get(scheme["code"], [])
        return schemes

    rows = execute(
        """
        SELECT irp_id, name, is_accumulation, software_version_code,
               peril, region, analysis_type
        FROM irp_model_profile
        WHERE LOWER(name) LIKE :q
           OR LOWER(COALESCE(software_version_code, '')) LIKE :q
           OR LOWER(COALESCE(peril, '')) LIKE :q
           OR LOWER(COALESCE(region, '')) LIKE :q
           OR LOWER(COALESCE(analysis_type, '')) LIKE :q
        ORDER BY name
        """,
        {"q": match}, connection="WORKBENCH")
    for row in rows:
        row["family"] = template_service.profile_family(
            row["is_accumulation"], row["software_version_code"]
        )
    return rows


def _metadata_rm_url(tab: str) -> str | None:
    rm_path = next(
        metadata_tab["rm_path"] for metadata_tab in _METADATA_TABS
        if metadata_tab["key"] == tab)
    base = _rm_ui_root()
    return f"{base}/{rm_path}" if base else None


def _metadata_context(request: Request) -> dict:
    requested_tab = request.query_params.get("tab", "model-profiles")
    valid_tabs = {tab["key"] for tab in _METADATA_TABS}
    tab = requested_tab if requested_tab in valid_tabs else "model-profiles"
    q = (request.query_params.get("q") or "").strip()
    counts = execute_one(
        """
        SELECT
          (SELECT COUNT(*) FROM irp_model_profile) AS model_profiles,
          (SELECT COUNT(*) FROM irp_output_profile) AS output_profiles,
          (SELECT COUNT(*) FROM irp_event_rate_scheme) AS event_rate_schemes,
          (SELECT COUNT(*) FROM irp_currency) AS currencies,
          (SELECT COUNT(*) FROM irp_currency_scheme) AS currency_schemes
        """,
        connection="WORKBENCH")
    latest_job = execute_one(
        """
        SELECT status_code, error_detail, completed_at, updated_at
        FROM rwb_job
        WHERE rwb_job_type = 'sync_irp_metadata'
        ORDER BY updated_at DESC
        """,
        connection="WORKBENCH")
    cache_sync = execute_one(
        """
        SELECT MAX(updated_at) AS last_synced_at
        FROM (
          SELECT updated_at FROM irp_model_profile
          UNION ALL SELECT updated_at FROM irp_output_profile
          UNION ALL SELECT updated_at FROM irp_event_rate_scheme
          UNION ALL SELECT updated_at FROM irp_currency
          UNION ALL SELECT updated_at FROM irp_currency_scheme
          UNION ALL SELECT updated_at FROM irp_currency_scheme_vintage
        ) AS metadata_updates
        """,
        connection="WORKBENCH")
    last_synced_at = cache_sync["last_synced_at"] if cache_sync else None
    if latest_job and latest_job["status_code"] == "succeeded":
        last_synced_at = latest_job["completed_at"]
    return {
        "tabs": _METADATA_TABS,
        "active_tab": tab,
        "active_tab_label": next(
            metadata_tab["label"] for metadata_tab in _METADATA_TABS
            if metadata_tab["key"] == tab),
        "active_tab_rm_url": _metadata_rm_url(tab),
        "q": q,
        "rows": _metadata_rows(tab, q),
        "counts": counts,
        "latest_job": latest_job,
        "last_synced_at": last_synced_at,
        "sync_message": request.query_params.get("sync"),
    }


# ── Administration (US2): suites/templates tabs ───────────────────────────────

def _admin_context(request: Request) -> dict:
    requested_tab = request.query_params.get("tab", "suites")
    tab = requested_tab if requested_tab in ("suites", "templates") else "suites"
    q = (request.query_params.get("q") or "").strip()
    suites = template_service.list_suites()
    all_templates = template_service.list_templates()
    matching = (
        [t for t in all_templates if q.lower() in t["name"].lower()]
        if tab == "templates" else []
    )
    return {
        "active_tab": tab,
        "q": q,
        "suites": suites,
        "suite_count": len(suites),
        "templates": matching,
        "template_count": len(all_templates),
    }


def _select_options(rows: list[dict], key: str, current_value: str | None) -> list[dict]:
    """Live cache rows for a `<select>`, marking the stored/submitted value as
    selected. If that value isn't among the live rows (FR-011 unresolved), a
    synthetic option carries it through instead of silently swapping in
    whatever option would otherwise render first — never a silent overwrite
    on save."""
    options = [dict(row) for row in rows]
    if not current_value:
        return options
    if any(option.get(key) == current_value for option in options):
        for option in options:
            option["selected"] = option.get(key) == current_value
        return options
    for option in options:
        option["selected"] = False
    return [{key: current_value, "unresolved": True, "selected": True}] + options


def _scheme_select_options(rows: list[dict], current_value: str | None) -> list[dict]:
    """_select_options for the event-rate-scheme select, reclassifying its
    synthetic stored-value option: the generic label says "not found in Risk
    Modeler", but a stored scheme can be absent from the live list while still
    cached — hidden by an admin (workbench_is_active = 0, FR-022) or belonging
    to another peril/region. Tag the hidden case; give the off-profile case its
    real peril/region so it renders like any resolved option."""
    options = _select_options(rows, "name", current_value)
    if not options or not options[0].get("unresolved"):
        return options
    cached = template_service.scheme_lookup(options[0]["name"])
    if cached is None:
        return options
    synthetic = options[0]
    del synthetic["unresolved"]
    if cached["workbench_is_active"]:
        synthetic["peril_code"] = cached["peril_code"]
        synthetic["model_region_code"] = cached["model_region_code"]
    else:
        synthetic["hidden"] = True
    return options


def _template_values_from_dict(values: dict) -> dict:
    """Normalize a `get_template()` row or a submitted form dict into the
    shape both the form template and `_template_form_context` expect —
    tags as a semicolon string rather than `get_template()`'s list."""
    normalized = dict(values)
    tags = normalized.get("tags")
    if isinstance(tags, list):
        normalized["tags"] = "; ".join(tags)
    return normalized


def _template_form_context(
    request: Request, *, mode: str, template: dict | None,
    form: dict | None, errors: list[str],
) -> dict:
    current_user = request.state.user
    values = _template_values_from_dict(
        form if form is not None else (template or {})
    )
    # R8 default: brand-new create (no form, no template yet) starts on
    # "Treat as unknown" — Jinja's Undefined is falsy, so an absent key would
    # otherwise render the "Skip location" option selected instead.
    if values.get("treat_construction_occupancy_as_unknown") is None:
        values["treat_construction_occupancy_as_unknown"] = True
    values.setdefault("min_loss_threshold", Decimal("1.00"))
    values.setdefault("num_max_loss_event", 1)
    profile_name = values.get("analysis_profile_name") or ""
    reference = template_service.reference_options()
    event_scheme_rows = (
        template_service.scheme_options(profile_name) if profile_name else []
    )
    return {
        "current_user": current_user,
        "nav": get_nav_context(current_user, "templates.suites"),
        "mode": mode,
        "template": template,
        "form": values,
        "errors": list(errors),
        "model_profile_options": _select_options(
            reference["model_profiles"], "name", values.get("analysis_profile_name")),
        "event_rate_scheme_options": _scheme_select_options(
            event_scheme_rows, values.get("event_rate_scheme_name")),
        "output_profile_options": _select_options(
            reference["output_profiles"], "name", values.get("output_profile_name")),
        "tag_names": template_service.list_tag_names(),
    }


def _parse_template_values(form: dict) -> tuple[TemplateValues | None, list[str]]:
    errors: list[str] = []
    try:
        threshold = Decimal(form["min_loss_threshold"] or "1.00")
    except InvalidOperation:
        threshold = Decimal("1.00")
        errors.append("Min loss threshold must be a number")
    try:
        max_events = int(form["num_max_loss_event"] or "1")
    except ValueError:
        max_events = 1
        errors.append("Max loss events must be a whole number")
    if errors:
        return None, errors
    values = TemplateValues(
        name=form["name"],
        analysis_profile_name=form["analysis_profile_name"],
        output_profile_name=form["output_profile_name"],
        event_rate_scheme_name=form["event_rate_scheme_name"] or None,
        min_loss_threshold=threshold,
        num_max_loss_event=max_events,
        franchise_deductible=form["franchise_deductible"],
        treat_construction_occupancy_as_unknown=form[
            "treat_construction_occupancy_as_unknown"],
    )
    return values, []


def _suite_form_context(
    request: Request, *, mode: str, suite: dict | None,
    form: dict | None, errors: list[str],
) -> dict:
    current_user = request.state.user
    if form is not None:
        name_value = form.get("name", "")
        selected_ids = {str(value).lower() for value in form.get("template_ids", [])}
    else:
        name_value = suite["name"] if suite else ""
        selected_ids = (
            {item["template_id"] for item in suite["items"]} if suite else set()
        )
    return {
        "current_user": current_user,
        "nav": get_nav_context(current_user, "templates.suites"),
        "mode": mode,
        "suite": suite,
        "name_value": name_value,
        "errors": list(errors),
        "templates": template_service.list_templates(),
        "selected_ids": selected_ids,
    }


# Literal template/metadata/administration routes precede the parameterized
# routes (EDM-router precedent): /templates, /templates/table,
# /templates/metadata*, /templates/analysis-templates/{new,scheme-options},
# /templates/suites/new all resolve before the
# `{template_id}` / `{suite_id}` routes further down this file.

@router.get("/templates", response_class=HTMLResponse)
def suites_page(request: Request):
    context = _admin_context(request)
    context.update({
        "current_user": request.state.user,
        "nav": get_nav_context(request.state.user, "templates.suites"),
    })
    return _page(request, "pages/templates.html", context)


@router.get("/templates/table", response_class=HTMLResponse)
def templates_table_fragment(request: Request):
    context = _admin_context(request)
    context["current_user"] = request.state.user
    return _page(request, "partials/templates_table.html", context)


@router.get("/templates/metadata", response_class=HTMLResponse)
def metadata_page(request: Request):
    context = _metadata_context(request)
    context.update({
        "current_user": request.state.user,
        "nav": get_nav_context(request.state.user, "templates.metadata"),
    })
    return _page(request, "pages/templates_metadata.html", context)


@router.get("/templates/metadata/table", response_class=HTMLResponse)
def metadata_table(request: Request):
    context = _metadata_context(request)
    context["current_user"] = request.state.user
    return _page(request, "partials/metadata_table.html", context)


@router.post("/templates/metadata/sync")
def sync_metadata(
    request: Request,
    csrf_token: Annotated[str, Form()],
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/templates/metadata", status_code=303)
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request",
        requestor_id=_METADATA_SYNC_REQUESTOR_ID,
        rwb_job_type="sync_irp_metadata",
        link_type="not_applicable", link_id=None,
        context_type=None, context_id=None,
        actor_id=request.state.user.id,
    )
    if job_id is None:
        return RedirectResponse(
            "/templates/metadata?sync=already-running", status_code=303)
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="sync_irp_metadata")
    return RedirectResponse("/templates/metadata?sync=queued", status_code=303)


@router.post("/templates/metadata/event-rate-schemes/{irp_id}/visibility")
def set_scheme_visibility_route(
    request: Request,
    irp_id: int,
    csrf_token: Annotated[str, Form()],
    is_active: Annotated[str | None, Form()] = None,
):
    _current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(
            "/templates/metadata?tab=event-rate-schemes", status_code=303)
    try:
        template_service.set_scheme_visibility(irp_id, is_active is not None)
    except TemplateServiceError:
        pass  # row vanished on a re-sync; the re-render below reflects it
    if request.headers.get("HX-Request"):
        context = _metadata_context(request)
        context["current_user"] = request.state.user
        return _page(request, "partials/metadata_table.html", context)
    return RedirectResponse(
        "/templates/metadata?tab=event-rate-schemes", status_code=303)


# ── Analysis template builder (US2) ────────────────────────────────────────────

@router.get("/templates/analysis-templates/new", response_class=HTMLResponse)
def new_template_form(request: Request):
    _current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    context = _template_form_context(
        request, mode="create", template=None, form=None, errors=[])
    return _page(request, "pages/analysis_template_form.html", context)


@router.get("/templates/analysis-templates/scheme-options", response_class=HTMLResponse)
def scheme_options_fragment(request: Request):
    profile = (request.query_params.get("profile") or "").strip()
    options = template_service.scheme_options(profile) if profile else []
    return _page(request, "partials/scheme_options.html", {"options": options})


def _template_form(
    name: Annotated[str, Form()] = "",
    analysis_profile_name: Annotated[str, Form()] = "",
    event_rate_scheme_name: Annotated[str, Form()] = "",
    output_profile_name: Annotated[str, Form()] = "",
    min_loss_threshold: Annotated[str, Form()] = "1.00",
    num_max_loss_event: Annotated[str, Form()] = "1",
    franchise_deductible: Annotated[str | None, Form()] = None,
    treat_construction_occupancy_as_unknown: Annotated[str, Form()] = "1",
    tags: Annotated[str, Form()] = "",
) -> dict:
    return {
        "name": name,
        "analysis_profile_name": analysis_profile_name,
        "event_rate_scheme_name": event_rate_scheme_name or None,
        "output_profile_name": output_profile_name,
        "min_loss_threshold": min_loss_threshold,
        "num_max_loss_event": num_max_loss_event,
        "franchise_deductible": franchise_deductible == "on",
        "treat_construction_occupancy_as_unknown":
            treat_construction_occupancy_as_unknown == "1",
        "tags": tags,
    }


@router.post("/templates/analysis-templates")
def create_template_route(
    request: Request,
    csrf_token: Annotated[str, Form()],
    form: Annotated[dict, Depends(_template_form)],
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/templates?tab=templates", status_code=303)

    values, errors = _parse_template_values(form)
    if values is not None:
        try:
            template_service.save_template(
                values, tags=form["tags"].split(";"), actor_id=current_user.id,
            )
        except TemplateValidationError as exc:
            errors = list(exc.errors)
        else:
            return RedirectResponse("/templates?tab=templates", status_code=303)

    context = _template_form_context(
        request, mode="create", template=None, form=form, errors=errors)
    return _page(request, "pages/analysis_template_form.html", context)


@router.get("/templates/analysis-templates/{template_id}", response_class=HTMLResponse)
def template_detail(request: Request, template_id: str):
    template = template_service.get_template(template_id)
    if template is None:
        return RedirectResponse("/templates?tab=templates", status_code=303)
    mode = "edit" if request.state.user.is_admin else "view"
    context = _template_form_context(
        request, mode=mode, template=template, form=None, errors=[])
    return _page(request, "pages/analysis_template_form.html", context)


@router.post("/templates/analysis-templates/{template_id}")
def update_template_route(
    request: Request,
    template_id: str,
    csrf_token: Annotated[str, Form()],
    form: Annotated[dict, Depends(_template_form)],
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(
            f"/templates/analysis-templates/{template_id}", status_code=303)

    values, errors = _parse_template_values(form)
    if values is not None:
        try:
            template_service.save_template(
                values, tags=form["tags"].split(";"), actor_id=current_user.id,
                template_id=template_id,
            )
        except TemplateValidationError as exc:
            errors = list(exc.errors)
        except TemplateServiceError:
            return RedirectResponse("/templates?tab=templates", status_code=303)
        else:
            return RedirectResponse("/templates?tab=templates", status_code=303)

    context = _template_form_context(
        request, mode="edit", template={"id": template_id, **form}, form=form,
        errors=errors)
    return _page(request, "pages/analysis_template_form.html", context)


@router.post("/templates/analysis-templates/{template_id}/delete")
def delete_template_route(
    request: Request,
    template_id: str,
    csrf_token: Annotated[str, Form()],
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(
            f"/templates/analysis-templates/{template_id}", status_code=303)
    try:
        template_service.delete_template(template_id, actor_id=current_user.id)
    except TemplateInUseError as exc:
        template = template_service.get_template(template_id)
        context = _template_form_context(
            request, mode="edit", template=template, form=None,
            errors=[str(exc)])
        return _page(request, "pages/analysis_template_form.html", context)
    except TemplateServiceError:
        pass
    return RedirectResponse("/templates?tab=templates", status_code=303)


@router.post("/templates/analysis-templates/{template_id}/duplicate")
def duplicate_template_route(
    request: Request,
    template_id: str,
    csrf_token: Annotated[str, Form()],
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(
            f"/templates/analysis-templates/{template_id}", status_code=303)
    try:
        new_id = template_service.duplicate_template(
            template_id, actor_id=current_user.id)
    except TemplateValidationError as exc:
        # Reference data drifted since the original was saved (e.g. its profile
        # re-synced as DLM with no stored scheme) — name the rule instead of
        # bouncing to the list with no copy and no message.
        template = template_service.get_template(template_id)
        context = _template_form_context(
            request, mode="edit", template=template, form=None,
            errors=list(exc.errors))
        return _page(request, "pages/analysis_template_form.html", context)
    except TemplateServiceError:
        return RedirectResponse("/templates?tab=templates", status_code=303)
    return RedirectResponse(f"/templates/analysis-templates/{new_id}", status_code=303)


# ── Suite builder (US2) ────────────────────────────────────────────────────────

@router.get("/templates/suites/new", response_class=HTMLResponse)
def new_suite_form(request: Request):
    _current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    context = _suite_form_context(
        request, mode="create", suite=None, form=None, errors=[])
    return _page(request, "pages/suite_form.html", context)


@router.post("/templates/suites")
def create_suite_route(
    request: Request,
    csrf_token: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
    template_ids: Annotated[list[str] | None, Form()] = None,
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/templates?tab=suites", status_code=303)
    ids = template_ids or []
    try:
        template_service.save_suite(name, ids, actor_id=current_user.id)
    except TemplateValidationError as exc:
        context = _suite_form_context(
            request, mode="create", suite=None,
            form={"name": name, "template_ids": ids}, errors=list(exc.errors),
        )
        return _page(request, "pages/suite_form.html", context)
    return RedirectResponse("/templates?tab=suites", status_code=303)


@router.get("/templates/suites/{suite_id}", response_class=HTMLResponse)
def suite_detail(request: Request, suite_id: str):
    suite = template_service.get_suite(suite_id)
    if suite is None:
        return RedirectResponse("/templates?tab=suites", status_code=303)
    mode = "edit" if request.state.user.is_admin else "view"
    context = _suite_form_context(
        request, mode=mode, suite=suite, form=None, errors=[])
    return _page(request, "pages/suite_form.html", context)


@router.post("/templates/suites/{suite_id}")
def update_suite_route(
    request: Request,
    suite_id: str,
    csrf_token: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
    template_ids: Annotated[list[str] | None, Form()] = None,
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/templates/suites/{suite_id}", status_code=303)
    ids = template_ids or []
    try:
        template_service.save_suite(
            name, ids, actor_id=current_user.id, suite_id=suite_id,
        )
    except TemplateValidationError as exc:
        context = _suite_form_context(
            request, mode="edit", suite={"id": suite_id, "name": name},
            form={"name": name, "template_ids": ids}, errors=list(exc.errors),
        )
        return _page(request, "pages/suite_form.html", context)
    except TemplateServiceError:
        return RedirectResponse("/templates?tab=suites", status_code=303)
    return RedirectResponse("/templates?tab=suites", status_code=303)


@router.post("/templates/suites/{suite_id}/delete")
def delete_suite_route(
    request: Request,
    suite_id: str,
    csrf_token: Annotated[str, Form()],
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/templates/suites/{suite_id}", status_code=303)
    try:
        template_service.delete_suite(suite_id, actor_id=current_user.id)
    except TemplateServiceError:
        pass
    return RedirectResponse("/templates?tab=suites", status_code=303)


@router.post("/templates/suites/{suite_id}/duplicate")
def duplicate_suite_route(
    request: Request,
    suite_id: str,
    csrf_token: Annotated[str, Form()],
):
    current_user, redirect = require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/templates/suites/{suite_id}", status_code=303)
    try:
        new_id = template_service.duplicate_suite(suite_id, actor_id=current_user.id)
    except TemplateValidationError as exc:
        suite = template_service.get_suite(suite_id)
        context = _suite_form_context(
            request, mode="edit", suite=suite, form=None,
            errors=list(exc.errors))
        return _page(request, "pages/suite_form.html", context)
    except TemplateServiceError:
        return RedirectResponse("/templates?tab=suites", status_code=303)
    return RedirectResponse(f"/templates/suites/{new_id}", status_code=303)


__all__ = ["router"]
