"""Session middleware — validates rwb_session cookie on every request.

Attaches CurrentUser to request.state.user on valid sessions.
Returns HX-Redirect (HTTP 200) for HTMX requests, 302 for standard
requests when the session is expired or absent.

Gate order (checked in sequence):
  1. Session exists and is valid (idle + absolute + invalidated check)
  2. must_change_password gate → /auth/change-password
  3. Role gate → /auth/access-pending
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.services.auth_service import validate_session

# Paths that are accessible without a valid session
_PUBLIC_PATHS = frozenset([
    "/auth/login",
    "/auth/logout",
    "/auth/oidc-login",
    "/auth/callback",
    "/api/health",
])

# Paths accessible to authenticated users regardless of must_change_password
_CHANGE_PWD_EXEMPT = frozenset([
    "/auth/change-password",
    "/auth/logout",
])

# Paths accessible to authenticated users regardless of role assignment
_ROLE_GATE_EXEMPT = frozenset([
    "/auth/access-pending",
    "/auth/logout",
    "/auth/change-password",
])

COOKIE_NAME = "rwb_session"


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect_response(request: Request, url: str) -> Response:
    if _is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url=url, status_code=302)


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Static assets and public auth routes bypass session checks
        if path.startswith("/static/") or path in _PUBLIC_PATHS:
            return await call_next(request)

        session_id = request.cookies.get(COOKIE_NAME)
        if not session_id:
            return _redirect_response(request, f"/auth/login?next={path}")

        current_user = validate_session(session_id)
        if current_user is None:
            return _redirect_response(request, "/auth/login")

        # Gate 2: must_change_password
        if current_user.must_change_password and path not in _CHANGE_PWD_EXEMPT:
            return _redirect_response(request, "/auth/change-password")

        # Gate 3: no roles assigned (OIDC JIT user pending admin approval)
        if not current_user.role_codes and path not in _ROLE_GATE_EXEMPT:
            return _redirect_response(request, "/auth/access-pending")

        request.state.user = current_user
        return await call_next(request)
