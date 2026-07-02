"""OIDC support via Microsoft Entra (MSAL auth-code flow)."""

from __future__ import annotations

import msal

from app.config import settings


def _app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.entra_client_id,
        client_credential=settings.entra_client_secret,
        authority=f"https://login.microsoftonline.com/{settings.entra_tenant_id}",
    )


def initiate_flow(redirect_uri: str) -> dict:
    """Start an auth-code flow; returns the flow dict to store in the session cookie."""
    return _app().initiate_auth_code_flow(
        scopes=["email"],
        redirect_uri=redirect_uri,
    )


def complete_flow(flow: dict, auth_response: dict) -> dict:
    """Exchange the callback params for tokens; return ID token claims dict."""
    result = _app().acquire_token_by_auth_code_flow(flow, auth_response)
    if "error" in result:
        raise ValueError(
            result.get("error_description") or result["error"]
        )
    id_token_claims = result.get("id_token_claims", {})
    if not (id_token_claims.get("email") or id_token_claims.get("preferred_username")):
        raise ValueError("email claim missing from ID token")
    return id_token_claims


def build_logout_url() -> str:
    """Return the Entra end-session URL with post_logout_redirect_uri."""
    from urllib.parse import quote
    post_logout = quote(
        f"{settings.entra_redirect_uri.rsplit('/auth/callback', 1)[0]}/auth/login"
    )
    return (
        f"https://login.microsoftonline.com/{settings.entra_tenant_id}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={post_logout}"
    )
