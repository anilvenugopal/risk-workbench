"""Unit-test-level conftest.

Sets the minimum required env vars before any app module is imported,
so that `app.config.Settings()` (which runs at module load) does not fail
with a missing-field validation error in environments without a .env file.
"""

import os

# Must be set before any app.* import triggers Settings() at module level.
os.environ.setdefault("SESSION_SECRET_KEY", "unit-test-secret-key-not-for-production")

import uuid  # noqa: E402

import pytest  # noqa: E402

from db import execute_command  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_name_check_cache():
    """The name-collision TTL cache (issue #11) is module-level state — clear it
    around every test so a seeded collision can't leak into the next test."""
    from app.services import name_check

    name_check.clear_cache()
    yield
    name_check.clear_cache()


def edm_with_portfolios(count: int = 2) -> tuple[str, list[str]]:
    """Insert one ready EDM with ``count`` portfolios; return their ids.

    Shared by the geohaz service, route, worker, and poller tests.
    """
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'GeoHaz EDM', 'ready', '2026-08-13', '2026-08-13')",
        {"id": edm_id}, connection="WORKBENCH")
    portfolio_ids: list[str] = []
    for number in range(1, count + 1):
        portfolio_id = str(uuid.uuid4())
        portfolio_ids.append(portfolio_id)
        execute_command(
            "INSERT INTO irp_portfolio "
            "(id, edm_id, name, irp_id, inserted_at, updated_at) "
            "VALUES (:id, :edm, :name, :irp, '2026-08-13', '2026-08-13')",
            {
                "id": portfolio_id,
                "edm": edm_id,
                "name": f"Portfolio {number}",
                "irp": str(100 + number),
            },
            connection="WORKBENCH",
        )
    return edm_id, portfolio_ids
