"""Admin routes — user management (list, create, reset password, assign role, force-logout)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.auth.password import hash_password, validate_password_requirements
from db import execute, execute_command

router = APIRouter(prefix="/admin")


def _templates(request: Request):
    return request.app.state.templates


def _require_admin(request: Request):
    """Return current_user if admin, else redirect."""
    user = getattr(request.state, "user", None)
    if not user or not user.is_admin:
        return None, RedirectResponse("/", status_code=302)
    return user, None


# ── GET /admin/users ──────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
def user_list(request: Request):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    from app.nav import get_nav_context
    users = execute(
        """
        SELECT u.id, u.email, u.display_name, u.is_active, u.must_change_password,
               u.last_login_at, u.entra_oid,
               STRING_AGG(rk.code, ', ') WITHIN GROUP (ORDER BY rk.sort_order) AS roles
        FROM app_user u
        LEFT JOIN user_role ur ON ur.user_id = u.id
        LEFT JOIN role_kind rk ON rk.code = ur.role_code
        GROUP BY u.id, u.email, u.display_name, u.is_active, u.must_change_password,
                 u.last_login_at, u.entra_oid
        ORDER BY u.email
        """,
        {},
        connection="WORKBENCH",
    )
    nav = get_nav_context(current_user, "admin")
    return _templates(request).TemplateResponse(request, "admin/users.html", {
        "current_user": current_user,
        "nav": nav,
        "users": users,
    })


# ── GET /admin/users/new ──────────────────────────────────────────────────────

@router.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    from app.nav import get_nav_context
    nav = get_nav_context(current_user, "admin")
    all_roles = execute(
        "SELECT code, label FROM role_kind ORDER BY sort_order",
        {},
        connection="WORKBENCH",
    )
    return _templates(request).TemplateResponse(request, "admin/user_detail.html", {
        "current_user": current_user,
        "nav": nav,
        "edit_user": None,
        "all_roles": all_roles,
        "errors": [],
    })


# ── POST /admin/users/new ─────────────────────────────────────────────────────

@router.post("/users/new")
def create_user(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect

    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/admin/users", status_code=302)

    errors = validate_password_requirements(password)
    if errors:
        from app.nav import get_nav_context
        nav = get_nav_context(current_user, "admin")
        return _templates(request).TemplateResponse(request, "admin/user_detail.html", {
            "current_user": current_user,
            "nav": nav,
            "edit_user": None,
            "errors": errors,
        }, status_code=200)

    pw_hash = hash_password(password)
    execute_command(
        """
        INSERT INTO app_user (email, display_name, password_hash, must_change_password, is_active)
        VALUES (:email, :name, :pw, 1, 1)
        """,
        {"email": email, "name": display_name, "pw": pw_hash},
        connection="WORKBENCH",
    )
    return RedirectResponse("/admin/users", status_code=302)


# ── GET /admin/users/{id} ─────────────────────────────────────────────────────

@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(request: Request, user_id: str):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    from app.nav import get_nav_context
    rows = execute(
        """
        SELECT u.id, u.email, u.display_name, u.is_active, u.must_change_password,
               u.last_login_at, u.entra_oid,
               STRING_AGG(rk.code, ', ') WITHIN GROUP (ORDER BY rk.sort_order) AS roles
        FROM app_user u
        LEFT JOIN user_role ur ON ur.user_id = u.id
        LEFT JOIN role_kind rk ON rk.code = ur.role_code
        WHERE u.id = :id
        GROUP BY u.id, u.email, u.display_name, u.is_active, u.must_change_password,
                 u.last_login_at, u.entra_oid
        """,
        {"id": user_id},
        connection="WORKBENCH",
    )
    if not rows:
        return RedirectResponse("/admin/users", status_code=302)
    all_roles = execute(
        "SELECT code, label FROM role_kind ORDER BY sort_order",
        {},
        connection="WORKBENCH",
    )
    nav = get_nav_context(current_user, "admin")
    return _templates(request).TemplateResponse(request, "admin/user_detail.html", {
        "current_user": current_user,
        "nav": nav,
        "edit_user": rows[0],
        "all_roles": all_roles,
        "errors": [],
    })


# ── POST /admin/users/{id}/reset-password ─────────────────────────────────────

@router.post("/users/{user_id}/reset-password")
def reset_password(
    request: Request,
    user_id: str,
    new_password: str = Form(...),
    csrf_token: str = Form(...),
):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/admin/users/{user_id}", status_code=302)

    pw_hash = hash_password(new_password)
    execute_command(
        "UPDATE app_user SET password_hash = :pw, must_change_password = 1, "
        "updated_at = GETUTCDATE() WHERE id = :id",
        {"pw": pw_hash, "id": user_id},
        connection="WORKBENCH",
    )
    return RedirectResponse(f"/admin/users/{user_id}", status_code=302)


# ── POST /admin/users/{id}/assign-role ────────────────────────────────────────

@router.post("/users/{user_id}/assign-role")
def assign_role(
    request: Request,
    user_id: str,
    role_code: str = Form(...),
    csrf_token: str = Form(...),
):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/admin/users/{user_id}", status_code=302)

    # Idempotent: insert only if not already assigned
    execute_command(
        """
        IF NOT EXISTS (
            SELECT 1 FROM user_role WHERE user_id = :uid AND role_code = :role
        )
        INSERT INTO user_role (user_id, role_code, inserted_by)
        VALUES (:uid, :role, :by)
        """,
        {"uid": user_id, "role": role_code, "by": current_user.id},
        connection="WORKBENCH",
    )
    return RedirectResponse(f"/admin/users/{user_id}", status_code=302)


# ── POST /admin/users/{id}/force-logout ───────────────────────────────────────

@router.post("/users/{user_id}/force-logout")
def force_logout(
    request: Request,
    user_id: str,
    csrf_token: str = Form(...),
):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/admin/users/{user_id}", status_code=302)

    from app.services.auth_service import invalidate_all_sessions
    invalidate_all_sessions(user_id)
    return RedirectResponse(f"/admin/users/{user_id}", status_code=302)


# ── POST /admin/users/provision-oidc ─────────────────────────────────────────
# Pre-provision an OIDC user before they have ever signed in. Creates an
# app_user row with email + display_name and immediately assigns a role so the
# user lands in the shell (not access-pending) on first sign-in.
# The OIDC callback links the Entra OID to this record by email on first login.

@router.post("/users/provision-oidc")
def provision_oidc_user(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    role_code: str = Form(...),
    csrf_token: str = Form(...),
):
    current_user, redirect = _require_admin(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/admin/users", status_code=302)

    email = email.strip().lower()

    # Idempotent: if the email already exists, just ensure the role is assigned
    existing = execute(
        "SELECT id FROM app_user WHERE LOWER(email) = :email",
        {"email": email},
        connection="WORKBENCH",
    )
    if existing:
        user_id = str(existing[0]["id"])
    else:
        execute_command(
            """
            INSERT INTO app_user (email, display_name, must_change_password, is_active)
            VALUES (:email, :name, 0, 1)
            """,
            {"email": email, "name": display_name},
            connection="WORKBENCH",
        )
        row = execute(
            "SELECT id FROM app_user WHERE LOWER(email) = :email",
            {"email": email},
            connection="WORKBENCH",
        )
        user_id = str(row[0]["id"])

    # Assign role (idempotent)
    execute_command(
        """
        IF NOT EXISTS (
            SELECT 1 FROM user_role WHERE user_id = :uid AND role_code = :role
        )
        INSERT INTO user_role (user_id, role_code, inserted_by)
        VALUES (:uid, :role, :by)
        """,
        {"uid": user_id, "role": role_code, "by": current_user.id},
        connection="WORKBENCH",
    )
    return RedirectResponse(f"/admin/users/{user_id}", status_code=302)
