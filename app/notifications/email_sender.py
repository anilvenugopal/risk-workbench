"""Email delivery via Microsoft Graph (app-only Mail.Send). See docs/EMAIL_NOTIFICATIONS.md."""

from __future__ import annotations

import httpx
import msal

from app.config import settings

_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
_token_cache = msal.TokenCache()


def _app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.mail_client_id,
        client_credential=settings.mail_client_secret,
        authority=f"https://login.microsoftonline.com/{settings.mail_tenant_id}",
        token_cache=_token_cache,
    )


def _access_token() -> str:
    app = _app()
    result = app.acquire_token_silent(_GRAPH_SCOPE, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=_GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph token acquisition failed: {result.get('error_description') or result.get('error')}"
        )
    return result["access_token"]


def _recipients(addresses: list[str]) -> list[dict]:
    return [{"emailAddress": {"address": a}} for a in addresses]


def send_email(
    to: list[str],
    subject: str,
    html_body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> None:
    """Send one HTML email as settings.mail_sender_address via Graph.

    Raises httpx.HTTPStatusError on a non-2xx Graph response. Callers in the
    worker/poller should catch and log, not let a mail failure fail the job
    it's reporting on.
    """
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": _recipients(to),
            "ccRecipients": _recipients(cc or []),
            "bccRecipients": _recipients(bcc or []),
        },
        "saveToSentItems": "false",
    }
    response = httpx.post(
        f"https://graph.microsoft.com/v1.0/users/{settings.mail_sender_address}/sendMail",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()