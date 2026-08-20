"""Re-run the unit-tier submission-service suite against a LIVE SQL Server.

Article 12 tier 2. The unit tests (``tests/unit/test_submission_service.py``)
run against a portable SQLite mirror; this module imports those exact test
functions and re-collects them here, backed by a SQL-Server-connected
``iteration1_db`` fixture. Same assertions, real driver + dialect — which is what
actually proves the paths the SQLite mirror cannot vouch for:

  * the ``updated_at`` optimistic-concurrency marker against a real DATETIME2
    column,
  * ``LIKE`` collation in cedant autocomplete / find_similar / the list's name and
    cedant search, and the ``ESCAPE '\'`` clause behind them,
  * the ``EXISTS`` CRM-tag predicate and the dynamic ``IN`` param set that attaches
    CRM ids to a page of list rows,
  * ``db.row_limit()`` emitting ``OFFSET/FETCH`` — the SQLite tier only ever runs
    the ``LIMIT`` branch — including the non-zero offset the list's second page
    reads through,
  * status-history ``ORDER BY at DESC`` tie-breaking.

Because ``import *`` pulls in every ``test_*`` name, new unit tests added later
are automatically exercised here too — the seam stays closed as the suite grows.

One path the reused suite does **not** cover, added explicitly below
(``test_string_marker_round_trips_against_datetime2``): the reused tests read the
concurrency marker via ``get_submission().updated_at``, which on SQL Server is a
native ``datetime`` — but the web flow renders that value into a hidden field as
``str(...)`` and submits it back as a **string**. So the string→DATETIME2 *match*
(not just the always-mismatching stale-string arm) is only exercised by the
dedicated test here.

Run with:  pytest tests/sqlserver --run-sqlserver   (requires live SQL Server)

Isolation model: each test gets two throwaway analysts (fresh UUIDs); the kind
tables are already seeded by the migration. Teardown deletes every row these
tests create — all of it traces back to the two analyst ids — so each test sees
only its own data (the global list/suggest/find_similar assertions depend on
that). A freshly rebuilt DB is the assumed clean starting point.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.services import submission_service as svc
from app.services.errors import UnknownLinkError
from db import execute, execute_command, get_engine

# Re-collect the entire unit submission-service suite against the fixture below.
from tests.unit.test_submission_service import *  # noqa: F401,F403

pytestmark = pytest.mark.sqlserver


def _cleanup(
    user_a: str,
    user_b: str,
    edm_ids: set[str],
    rdm_ids: set[str],
) -> None:
    """Delete the submissions and EDM/RDM rows created by one reused test."""
    ids = {"a": user_a, "b": user_b}
    owned = ("SELECT id FROM submission "
             "WHERE assigned_analyst_id IN (:a, :b) OR inserted_by IN (:a, :b)")
    for entity_id in edm_ids | rdm_ids:
        execute_command(
            "DELETE FROM rwb_job_heartbeat WHERE rwb_job_id IN "
            "(SELECT id FROM rwb_job WHERE requestor_id = :id)",
            {"id": entity_id}, connection="WORKBENCH")
        execute_command(
            "DELETE FROM rwb_job WHERE requestor_id = :id",
            {"id": entity_id}, connection="WORKBENCH")
    for edm_id in edm_ids:
        execute_command(
            "DELETE FROM irp_job_resource WHERE irp_job_id IN "
            "(SELECT id FROM irp_job WHERE irp_edm_id = :id)",
            {"id": edm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_job WHERE irp_edm_id = :id",
                        {"id": edm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_analysis WHERE edm_id = :id",
                        {"id": edm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_portfolio WHERE edm_id = :id",
                        {"id": edm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_treaty WHERE edm_id = :id",
                        {"id": edm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM submission_edm WHERE edm_id = :id",
                        {"id": edm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_edm WHERE id = :id",
                        {"id": edm_id}, connection="WORKBENCH")
    for rdm_id in rdm_ids:
        execute_command(
            "DELETE FROM irp_job_resource WHERE irp_job_id IN "
            "(SELECT id FROM irp_job WHERE irp_rdm_id = :id)",
            {"id": rdm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_job WHERE irp_rdm_id = :id",
                        {"id": rdm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_analysis WHERE rdm_id = :id",
                        {"id": rdm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM submission_rdm WHERE rdm_id = :id",
                        {"id": rdm_id}, connection="WORKBENCH")
        execute_command("DELETE FROM irp_rdm WHERE id = :id",
                        {"id": rdm_id}, connection="WORKBENCH")
    execute_command(
        f"DELETE FROM submission_status_event WHERE submission_id IN ({owned})",
        ids, connection="WORKBENCH")
    execute_command(
        f"DELETE FROM submission_crm_id WHERE submission_id IN ({owned})",
        ids, connection="WORKBENCH")
    # Clear the self-FK first: a test that linked two of these deals leaves one
    # row referencing another, and the DELETE below removes both.
    execute_command(
        "UPDATE submission SET links_to_submission_id = NULL "
        "WHERE assigned_analyst_id IN (:a, :b) OR inserted_by IN (:a, :b)",
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
    edm_ids_before = {
        str(row["id"])
        for row in execute("SELECT id FROM irp_edm", {}, connection="WORKBENCH")
    }
    rdm_ids_before = {
        str(row["id"])
        for row in execute("SELECT id FROM irp_rdm", {}, connection="WORKBENCH")
    }
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
        edm_ids_after = {
            str(row["id"])
            for row in execute("SELECT id FROM irp_edm", {}, connection="WORKBENCH")
        }
        rdm_ids_after = {
            str(row["id"])
            for row in execute("SELECT id FROM irp_rdm", {}, connection="WORKBENCH")
        }
        _cleanup(
            user_a,
            user_b,
            edm_ids_after - edm_ids_before,
            rdm_ids_after - rdm_ids_before,
        )


@pytest.fixture()
def iteration2_db(iteration1_db) -> SimpleNamespace:
    return iteration1_db


def test_string_marker_round_trips_against_datetime2(iteration1_db):
    """A form-supplied STRING marker must MATCH the DATETIME2 column (R1/FR-031).

    The browser never sends a ``datetime``: the detail/edit templates render
    ``submission.updated_at`` into a hidden field as ``str(...)`` and post it back
    as a string, which the service binds verbatim into ``WHERE updated_at =
    :expected``. The reused unit tests read the marker as ``get_submission()
    .updated_at`` — a native ``datetime`` on SQL Server — so they never exercise
    the string→DATETIME2 conversion the real flow depends on. If that conversion
    ever failed to round-trip, every marker-guarded write (edit, status, reassign)
    would raise a spurious ``ConcurrencyConflict`` in production.

    Here each write reads the marker and ``str()``s it exactly as Jinja does, then
    asserts the write is applied (no conflict) — covering all three guarded paths:
    the in-place UPDATE (update_submission), the event-sourced transaction
    (set_status), and reassign_owner.
    """
    a, b = iteration1_db.user_a, iteration1_db.user_b
    sid = svc.create_submission(
        name=f"MarkerDeal_{uuid.uuid4().hex[:8]}", cedant_name="Marker Cedant",
        treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
        actor_id=a, confirmed=True,
    ).submission_id

    def marker() -> str:
        """The hidden-field value the browser would submit: str(a datetime)."""
        value = svc.get_submission(sid).updated_at
        assert not isinstance(value, str), (
            "precondition: on SQL Server the marker is a native datetime; if it is "
            "already a string this test is not exercising the conversion it targets")
        return str(value)

    # 1) In-place UPDATE path — matches (does not 409) and applies the change.
    res = svc.update_submission(
        submission_id=sid, expected_updated_at=marker(), actor_id=a,
        confirmed=True, directory_path="/staging/marker")
    assert res.updated is True
    assert svc.get_submission(sid).directory_path == "/staging/marker"

    # 2) Event-sourced status transaction — same marker semantics inside conn.begin().
    svc.set_status(submission_id=sid, to_status="COMPLETED", reason=None,
                   expected_updated_at=marker(), actor_id=a)
    assert svc.get_submission(sid).status_code == "COMPLETED"

    # Reopen (reassign is gated to ACTIVE) — also a marker-guarded transition.
    svc.set_status(submission_id=sid, to_status="ACTIVE", reason=None,
                   expected_updated_at=marker(), actor_id=a)

    # 3) Reassign path.
    svc.reassign_owner(submission_id=sid, new_owner_id=b,
                       expected_updated_at=marker(), actor_id=a)
    assert svc.get_submission(sid).assigned_analyst_id == b


def test_the_suggest_queries_parse_and_cap_on_sql_server(iteration1_db):
    """``SELECT DISTINCT … ORDER BY … OFFSET/FETCH``, the ``s.id <> :exclude``
    predicate and the ``uniqueidentifier`` comparison behind it are all accepted by
    SQLite without proving anything about SQL Server. Run each search against the
    real driver and check the cap holds."""
    a = iteration1_db.user_a
    tag = uuid.uuid4().hex[:8]
    for index in range(4):
        svc.create_submission(
            name=f"CapDeal{tag}_{index}", cedant_name=f"CapCedant{tag} {index}",
            treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
            actor_id=a, confirmed=True)

    assert len(svc.cedant_suggestions(f"CapCedant{tag}", limit=2)) == 2
    assert len(svc.cedant_suggestions(f"CapCedant{tag}")) == 4

    assert len(svc.search_submissions_for_link(f"CapDeal{tag}", limit=2)) == 2
    assert len(svc.search_submissions_for_link(f"CapDeal{tag}")) == 4
    # The AND-combined multi-term form binds one parameter per word.
    assert len(svc.search_submissions_for_link(f"CapDeal{tag} CapCedant{tag}")) == 4

    # :exclude bound with a real id and with text that is not a UUID — the second
    # is what raises a conversion error against submission.id if it reaches the
    # driver unparsed.
    first = svc.search_submissions_for_link(f"CapDeal{tag}")[0]
    assert len(svc.search_submissions_for_link(
        f"CapDeal{tag}", exclude_id=first.id)) == 3
    assert len(svc.search_submissions_for_link(
        f"CapDeal{tag}", exclude_id="not-a-uuid")) == 4


def test_an_unknown_link_target_is_refused_before_the_foreign_key(iteration1_db):
    """``links_to_submission_id`` is a FK to ``submission.id`` and the column is
    ``uniqueidentifier``. SQLite enforces neither, so only this tier shows what an
    unchecked id does: an integrity error for a well-formed id naming no row, and a
    conversion error for text that is not a UUID. Both must be ``UnknownLinkError``
    before the write."""
    a = iteration1_db.user_a
    tag = uuid.uuid4().hex[:8]
    sid = svc.create_submission(
        name=f"LinkDeal{tag}", cedant_name=f"LinkCedant{tag}",
        treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
        actor_id=a, confirmed=True).submission_id

    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(UnknownLinkError):
            svc.create_submission(
                name=f"LinkDeal{tag}_stale", cedant_name=f"LinkCedant{tag}",
                treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
                links_to_submission_id=bad, actor_id=a, confirmed=True)
        with pytest.raises(UnknownLinkError):
            svc.update_submission(
                submission_id=sid, expected_updated_at=svc.get_submission(sid).updated_at,
                actor_id=a, confirmed=True, links_to_submission_id=bad)

    # An UPPERCASE id — which is how SQL Server reads uniqueidentifier back — still
    # names the same deal and is stored in the canonical lowercase form.
    target = svc.create_submission(
        name=f"LinkDeal{tag}_target", cedant_name=f"LinkCedant{tag}",
        treaty_type_code="cat_xol", inception_date=date(2025, 4, 1),
        actor_id=a, confirmed=True).submission_id
    svc.update_submission(
        submission_id=sid, expected_updated_at=svc.get_submission(sid).updated_at,
        actor_id=a, confirmed=True, links_to_submission_id=target.upper())
    assert svc.get_submission(sid).links_to_submission_id == target
