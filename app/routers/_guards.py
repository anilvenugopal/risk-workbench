"""Route guards shared across routers."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse


def require_admin(request: Request):
    """Return (current_user, None) if admin, else (None, redirect-to-home)."""
    user = getattr(request.state, "user", None)
    if not user or not user.is_admin:
        return None, RedirectResponse("/", status_code=302)
    return user, None
