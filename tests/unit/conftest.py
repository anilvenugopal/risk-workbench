"""Unit-test-level conftest.

Sets the minimum required env vars before any app module is imported,
so that `app.config.Settings()` (which runs at module load) does not fail
with a missing-field validation error in environments without a .env file.
"""

import os

# Must be set before any app.* import triggers Settings() at module level.
os.environ.setdefault("SESSION_SECRET_KEY", "unit-test-secret-key-not-for-production")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_name_check_cache():
    """The name-collision TTL cache (issue #11) is module-level state — clear it
    around every test so a seeded collision can't leak into the next test."""
    from app.services import name_check

    name_check.clear_cache()
    yield
    name_check.clear_cache()
