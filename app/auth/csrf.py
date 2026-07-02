"""CSRF protection — double-submit cookie pattern.

Token is generated server-side, stored in a signed cookie (rwb_csrf),
and must be submitted as a hidden form field on every state-changing request.

Uses itsdangerous.URLSafeTimedSerializer so tokens expire and are tamper-proof.
"""

from __future__ import annotations

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_SERIALIZER_SALT = b"rwb-csrf"
COOKIE_NAME = "rwb_csrf"
# Tokens are valid for 2 hours; forms should not be left open that long.
_MAX_AGE = 7200


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key)


def generate_csrf_token() -> str:
    """Generate a signed CSRF token.  Store this in both cookie and form field."""
    payload = secrets.token_hex(16)
    return _serializer().dumps(payload, salt=_SERIALIZER_SALT)


def validate_csrf_token(token: str | None) -> bool:
    """Return True iff the token is valid and not expired.

    Returns False (rather than raising) to keep handler code clean.
    """
    if not token:
        return False
    try:
        _serializer().loads(token, salt=_SERIALIZER_SALT, max_age=_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
