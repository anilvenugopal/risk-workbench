"""Password hashing and validation helpers.

bcrypt cost factor 12 throughout. The null-hash guard in verify_password
ensures OIDC accounts (password_hash=NULL) cannot be brute-forced by
submitting any password.
"""

from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of plain at cost factor 12."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    """Return True iff plain matches hashed.

    Returns False immediately when hashed is None — OIDC accounts have
    password_hash=NULL and must not be accessible via password auth.
    """
    if hashed is None:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def validate_password_requirements(plain: str) -> list[str]:
    """Return a list of unmet requirement strings (empty = valid)."""
    errors = []
    if len(plain) < 12:
        errors.append("At least 12 characters")
    if not any(c.isupper() for c in plain):
        errors.append("At least one uppercase letter")
    if not any(c.islower() for c in plain):
        errors.append("At least one lowercase letter")
    if not any(c.isdigit() for c in plain):
        errors.append("At least one digit")
    return errors
