"""Re-run the unit-tier submission-service suite against a LIVE SQL Server.

Article 12 tier 2. The unit tests (``tests/unit/test_submission_service.py``)
run against a portable SQLite mirror; this module imports those exact test
functions and re-collects them here, backed by a SQL-Server-connected
``iteration1_db`` fixture. Same assertions, real driver + dialect — which is what
actually proves the paths the SQLite mirror cannot vouch for:

  * the ``updated_at`` optimistic-concurrency marker (a form-supplied string
    compared against a real DATETIME2 column),
  * ``LIKE`` collation in cedant autocomplete / find_similar,
  * status-history ``ORDER BY at DESC`` tie-breaking.

Because ``import *`` pulls in every ``test_*`` name, new unit tests added later
are automatically exercised here too — the seam stays closed as the suite grows.

Run with:  pytest tests/sqlserver --run-sqlserver   (requires live SQL Server)

Isolation model: each test gets two throwaway analysts (fresh UUIDs); the kind
tables are already seeded by the migration. Teardown deletes every row these
tests create — all of it traces back to the two analyst ids — so each test sees
only its own data (the global list/suggest/find_similar assertions depend on
that). A freshly rebuilt DB is the assumed clean starting point.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from db import execute_command, get_engine

# Re-collect the entire unit submission-service suite against the fixture below.
from tests.unit.test_submission_service import *  # noqa: F401,F403

pytestmark = pytest.mark.sqlserver


def _cleanup(user_a: str, user_b: str) -> None:
    """Delete, child-first, everything the reused tests created for these two
    analysts, then the analysts themselves. Best-effort ordering respects the
    real FKs (events/crm → submission → app_user)."""
    ids = {"a": user_a, "b": user_b}
    owned = ("SELECT id FROM submission "
             "WHERE assigned_analyst_id IN (:a, :b) OR inserted_by IN (:a, :b)")
    execute_command(
        f"DELETE FROM submission_status_event WHERE submission_id IN ({owned})",
        ids, connection="WORKBENCH")
    execute_command(
        f"DELETE FROM submission_crm_id WHERE submission_id IN ({owned})",
        ids, connection="WORKBENCH")
    execute_command(
        "DELETE FROM submission "
        "WHERE assigned_analyst_id IN (:a, :b) OR inserted_by IN (:a, :b)",
        ids, connection="WORKBENCH")
    execute_command("DELETE FROM app_user WHERE id IN (:a, :b)", ids,
                    connection="WORKBENCH")


@pytest.fixture()
def iteration1_db() -> SimpleNamespace:
    """SQL-Server-backed twin of the unit ``iteration1_db``: two throwaway
    analysts on the live WORKBENCH DB (kind tables already seeded by the
    migration). Overrides the SQLite fixture from the root conftest for the tests
    collected in this module. Real WORKBENCH engine → the service SQL hits SQL
    Server, not SQLite."""
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    for uid, tag in ((user_a, "A"), (user_b, "B")):
        execute_command(
            "INSERT INTO app_user (id, email, display_name, must_change_password, "
            "is_active) VALUES (:id, :email, :dn, 0, 1)",
            {"id": uid, "email": f"svc_{uid[:8]}@example.com",
             "dn": f"Svc Analyst {tag}"},
            connection="WORKBENCH")
    try:
        yield SimpleNamespace(engine=get_engine("WORKBENCH"),
                              user_a=user_a, user_b=user_b)
    finally:
        _cleanup(user_a, user_b)
