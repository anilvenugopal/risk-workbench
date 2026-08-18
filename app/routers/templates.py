"""Analysis template and template-suite routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from irp_integration.analysis_validation import classify_model_profile

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import rwb_job_service
from app.workers import dispatch
from db import execute, execute_one

router = APIRouter()

_METADATA_SYNC_REQUESTOR_ID = "00000000-0000-0000-0000-000000000009"
_METADATA_TABS = (
    {"key": "model-profiles", "label": "Model Profiles", "count_key": "model_profiles"},
    {"key": "output-profiles", "label": "Output Profiles", "count_key": "output_profiles"},
    {"key": "event-rate-schemes", "label": "Event Rate Schemes", "count_key": "event_rate_schemes"},
    {"key": "currencies", "label": "Currencies", "count_key": "currencies"},
)


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, nav_key: str):
    current_user = request.state.user
    return _templates(request).TemplateResponse(
        request,
        template,
        {"current_user": current_user,
         "nav": get_nav_context(current_user, nav_key)},
    )


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
                   model_version_code, is_hd
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

    rows = execute(
        """
        SELECT irp_id, name, is_accumulation, software_version_code,
               peril, region, analysis_type, rms_default
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
        row["family"] = (
            "Accumulation" if row["is_accumulation"]
            else classify_model_profile(row["software_version_code"] or "")
        )
    return rows


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
          (SELECT COUNT(*) FROM irp_currency) AS currencies
        """,
        connection="WORKBENCH")
    jobs = execute(
        """
        SELECT status_code, error_detail, completed_at, updated_at
        FROM rwb_job
        WHERE rwb_job_type = 'sync_irp_metadata'
        ORDER BY updated_at DESC
        """,
        connection="WORKBENCH")
    latest_job = jobs[0] if jobs else None
    cache_sync = execute_one(
        """
        SELECT MAX(updated_at) AS last_synced_at
        FROM (
          SELECT updated_at FROM irp_model_profile
          UNION ALL SELECT updated_at FROM irp_output_profile
          UNION ALL SELECT updated_at FROM irp_event_rate_scheme
          UNION ALL SELECT updated_at FROM irp_currency
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
        "q": q,
        "rows": _metadata_rows(tab, q),
        "counts": counts,
        "latest_job": latest_job,
        "last_synced_at": last_synced_at,
        "sync_message": request.query_params.get("sync"),
    }


# Literal template and metadata routes precede the parameterized routes added by
# the administration story.
@router.get("/templates", response_class=HTMLResponse)
def suites_page(request: Request):
    return _render(request, "pages/templates.html", "templates.suites")


@router.get("/templates/metadata", response_class=HTMLResponse)
def metadata_page(request: Request):
    context = _metadata_context(request)
    context.update({
        "current_user": request.state.user,
        "nav": get_nav_context(request.state.user, "templates.metadata"),
    })
    return _templates(request).TemplateResponse(
        request, "pages/templates_metadata.html", context)


@router.get("/templates/metadata/table", response_class=HTMLResponse)
def metadata_table(request: Request):
    return _templates(request).TemplateResponse(
        request, "partials/metadata_table.html", _metadata_context(request))


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
        actor_id=request.state.user.id,
    )
    if job_id is None:
        return RedirectResponse(
            "/templates/metadata?sync=already-running", status_code=303)
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="sync_irp_metadata")
    return RedirectResponse("/templates/metadata?sync=queued", status_code=303)


__all__ = ["router"]
