"""Auth routes — login, logout, OIDC, change-password, access-pending."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.auth.middleware import COOKIE_NAME
from app.auth.password import hash_password, validate_password_requirements, verify_password
from app.config import settings
from app.services.auth_service import (
    create_session,
    get_user_by_email,
    get_user_by_oid,
    invalidate_session,
    link_entra_oid,
    log_attempt,
    update_last_login,
)
from db import execute_command

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def _safe_next(next_url: str | None) -> str:
    """Validate that next is a relative path — prevents open redirect."""
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    # Must be relative: no scheme, no netloc, starts with /
    if parsed.scheme or parsed.netloc:
        return "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ── GET /auth/login ───────────────────────────────────────────────────────────

@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None, error: str | None = None):
    user = getattr(request.state, "user", None)
    if user:
        return RedirectResponse("/", status_code=302)
    templates = _templates(request)
    return templates.TemplateResponse(request, "auth/login_page.html", {
        "error": error,
        "next": next,
        "email_value": None,
    })


# ── POST /auth/login ──────────────────────────────────────────────────────────

@router.post("/auth/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    next: str | None = None,
):
    templates = _templates(request)
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent", "")[:512]

    def fail(msg: str):
        log_attempt(email, "password", False, msg, ip, ua)
        return templates.TemplateResponse(request, "auth/login_page.html", {
            "error": "Invalid email or password.",
            "next": next,
            "email_value": email,
        }, status_code=200)

    if not validate_csrf_token(csrf_token):
        return fail("csrf_invalid")

    user = get_user_by_email(email)
    if not user:
        fail("account_not_found")  # still check hash to prevent timing oracle
        return fail("account_not_found")

    if not verify_password(password, user.get("password_hash")):
        return fail("invalid_password")

    if not user["is_active"]:
        return fail("account_inactive")

    session_id = create_session(str(user["id"]), ip, ua)
    log_attempt(email, "password", True, None, ip, ua)
    update_last_login(str(user["id"]))

    redirect_to = _safe_next(next)
    response = RedirectResponse(redirect_to, status_code=302)
    _set_session_cookie(response, session_id)
    return response


# ── POST /auth/logout ─────────────────────────────────────────────────────────

@router.post("/auth/logout")
def logout(
    request: Request,
    csrf_token: str = Form(...),
):
    current_user = getattr(request.state, "user", None)
    response = RedirectResponse("/auth/login", status_code=302)
    _clear_session_cookie(response)

    if not validate_csrf_token(csrf_token):
        return response

    if current_user and current_user.session_id:
        invalidate_session(current_user.session_id)

        # OIDC accounts: redirect to Entra logout endpoint
        if current_user.entra_oid and settings.oidc_auth_enabled:
            try:
                from app.auth.oidc import build_logout_url
                logout_url = build_logout_url()
                response = RedirectResponse(logout_url, status_code=302)
                _clear_session_cookie(response)
                return response
            except Exception:
                pass  # fallback to local logout

    return response


# ── GET /auth/oidc-login ──────────────────────────────────────────────────────

@router.get("/auth/oidc-login")
def oidc_login(request: Request):
    if not settings.oidc_auth_enabled:
        return RedirectResponse("/auth/login", status_code=302)
    from app.auth.oidc import initiate_flow
    from itsdangerous import URLSafeTimedSerializer

    flow = initiate_flow(settings.entra_redirect_uri)
    ser = URLSafeTimedSerializer(settings.session_secret_key)
    state_cookie = ser.dumps(flow, salt=b"oidc-state")

    response = RedirectResponse(flow["auth_uri"], status_code=302)
    response.set_cookie(
        key="rwb_oidc_state",
        value=state_cookie,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=300,
        path="/",
    )
    return response


# ── GET /auth/callback ────────────────────────────────────────────────────────

@router.get("/auth/callback")
def oidc_callback(request: Request):
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
    from app.auth.oidc import complete_flow
    from app.auth.provisioning import jit_provision_oidc_user

    def _abort(reason: str):
        response = RedirectResponse(f"/auth/login?error={reason}", status_code=302)
        response.delete_cookie("rwb_oidc_state", path="/")
        return response

    ser = URLSafeTimedSerializer(settings.session_secret_key)
    raw_cookie = request.cookies.get("rwb_oidc_state")
    if not raw_cookie:
        return _abort("state_missing")

    try:
        flow = ser.loads(raw_cookie, salt=b"oidc-state", max_age=300)
    except (BadSignature, SignatureExpired):
        return _abort("state_expired")

    try:
        claims = complete_flow(flow, dict(request.query_params))
    except Exception as exc:
        return _abort(f"token_exchange_failed: {exc}")

    oid = claims.get("oid") or claims.get("sub")
    email = claims.get("email") or claims.get("preferred_username")
    display_name = claims.get("name") or email

    if not email:
        return _abort("email_missing")

    ip = _client_ip(request)
    ua = request.headers.get("User-Agent", "")[:512]

    user = get_user_by_oid(oid)

    # Fall back to email lookup for admin-pre-provisioned accounts (no OID yet)
    if not user:
        pre = get_user_by_email(email)
        if pre and pre.get("entra_oid") is None:
            # Link the Entra OID to this pre-provisioned record
            link_entra_oid(str(pre["id"]), oid)
            user = get_user_by_oid(oid)  # reload with OID now set

    if user:
        update_last_login(str(user["id"]))
        log_attempt(email, "oidc", True, None, ip, ua)
        session_id = create_session(str(user["id"]), ip, ua)
        response = RedirectResponse("/", status_code=302)
        _set_session_cookie(response, session_id)
        response.delete_cookie("rwb_oidc_state", path="/")
        return response
    else:
        user_id = jit_provision_oidc_user(oid, email, display_name)
        log_attempt(email, "oidc", True, None, ip, ua)
        session_id = create_session(user_id, ip, ua)
        response = RedirectResponse("/auth/access-pending", status_code=302)
        _set_session_cookie(response, session_id)
        response.delete_cookie("rwb_oidc_state", path="/")
        return response


# ── GET /auth/access-pending ──────────────────────────────────────────────────

@router.get("/auth/access-pending", response_class=HTMLResponse)
def access_pending(request: Request):
    templates = _templates(request)
    current_user = getattr(request.state, "user", None)
    return templates.TemplateResponse(request, "auth/access_pending.html", {
        "current_user": current_user,
    })


# ── POST /auth/setup-profile ──────────────────────────────────────────────────
# JIT user setup: newly-provisioned OIDC user sets their display name and
# optionally a password (for fallback password access). Called from the
# access-pending page while the user waits for a role to be assigned.

@router.post("/auth/setup-profile", response_class=HTMLResponse)
def setup_profile(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(...),
):
    current_user = getattr(request.state, "user", None)
    templates = _templates(request)

    def fail(errors):
        return templates.TemplateResponse(request, "auth/access_pending.html", {
            "current_user": current_user,
            "setup_errors": errors,
            "setup_display_name": display_name,
        }, status_code=200)

    if not validate_csrf_token(csrf_token):
        return fail(["Request expired. Please try again."])

    if not current_user:
        return RedirectResponse("/auth/login", status_code=302)

    errors = []
    pw_hash = None
    if new_password:
        errs = validate_password_requirements(new_password)
        if new_password != confirm_password:
            errs.append("Passwords do not match.")
        if errs:
            return fail(errs)
        pw_hash = hash_password(new_password)

    if not display_name.strip():
        return fail(["Display name is required."])

    update_fields = "display_name = :name, updated_at = GETUTCDATE()"
    params: dict = {"name": display_name.strip(), "id": current_user.id}
    if pw_hash:
        update_fields += ", password_hash = :pw"
        params["pw"] = pw_hash

    execute_command(
        f"UPDATE app_user SET {update_fields} WHERE id = :id",
        params,
        connection="WORKBENCH",
    )

    return templates.TemplateResponse(request, "auth/access_pending.html", {
        "current_user": current_user,
        "setup_saved": True,
    })


# ── GET/POST /auth/change-password ────────────────────────────────────────────

@router.get("/auth/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    current_user = getattr(request.state, "user", None)
    if not current_user or not current_user.must_change_password:
        return RedirectResponse("/", status_code=302)
    templates = _templates(request)
    return templates.TemplateResponse(request, "auth/change_password.html", {
        "errors": [],
    })


@router.post("/auth/change-password", response_class=HTMLResponse)
def change_password_submit(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    current_user = getattr(request.state, "user", None)
    templates = _templates(request)

    def fail(errors):
        return templates.TemplateResponse(request, "auth/change_password.html", {
            "errors": errors,
        }, status_code=200)

    if not validate_csrf_token(csrf_token):
        return fail(["Request expired. Please try again."])

    if not current_user:
        return RedirectResponse("/auth/login", status_code=302)

    errors = validate_password_requirements(new_password)
    if new_password != confirm_password:
        errors.append("Passwords do not match.")
    if errors:
        return fail(errors)

    pw_hash = hash_password(new_password)
    execute_command(
        "UPDATE app_user SET password_hash = :pw, must_change_password = 0, "
        "updated_at = GETUTCDATE() WHERE id = :id",
        {"pw": pw_hash, "id": current_user.id},
        connection="WORKBENCH",
    )
    return RedirectResponse("/", status_code=302)
