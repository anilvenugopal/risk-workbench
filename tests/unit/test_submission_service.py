"""Unit tests for app/services/submission_service.py (US1–US5).

Runs on the SQLite unit tier via the ``iteration1_db`` fixture (registers a
SQLite WORKBENCH engine with the Iteration-1 schema + seeds). Covers the contract
test obligations: atomic create + initial ACTIVE event, My/All list + filters,
duplicate warning (name and attribute arms), event-sourced status + reopen +
read-only gate, CRM tag management, optimistic concurrency, and the absence of a
delete function.
"""

from __future__ import annotations

import time
import uuid
from datetime import date

import pytest

from app.services import edm_service, rdm_service
from app.services import submission_service as svc
from app.services.errors import (
    ConcurrencyConflict,
    SelfLinkError,
    SubmissionClosed,
    UnknownLinkError,
)
from app.services.submission_service import (
    ContractInput,
    ContractInvalid,
    ContractOwner,
    add_contract,
    cedant_suggestions,
    create_submission,
    find_similar,
    get_status_history,
    get_submission,
    list_contracts,
    list_submissions,
    reassign_owner,
    remove_contract,
    search_submissions_for_link,
    search_submissions_global,
    set_contract_status,
    set_statuses,
    update_contract,
    update_submission,
)
from app.workers import entity_jobs
from db import (
    SQLServerQueryError,
    execute,
    execute_command,
    execute_one,
    execute_scalar,
    is_unique_violation,
)

STALE = "1999-01-01 00:00:00.000000"  # a marker that can never match


def _mk(db, *, owner=None, name="TY2604_AmericanFamily", cedant="American Family",
        tt="per_risk_xol", inc=date(2026, 4, 1), ty=2026, confirmed=True,
        crm=None, contracts=None):
    # confirmed=True by default: test setup must always create its baseline row,
    # even when a look-alike already exists in a shared dev DB (an unconfirmed
    # create would short-circuit with a warning and write nothing). Tests that
    # specifically exercise the duplicate-warning path pass confirmed=False.
    # One contract by default, carrying ``tt`` and ``inc``; ``contracts=[]`` makes
    # a deal with none. The CRM ID is unique per call (FR-003: one CRM ID names
    # one contract across the Workbench) unless the test names it.
    if contracts is None:
        crm = crm or f"CRM-{uuid.uuid4().hex[:6]}"
        contracts = [ContractInput(crm_id=crm, treaty_type_code=tt, inception_date=inc)]
    res = create_submission(
        name=name, cedant_name=cedant, treaty_year=ty, contracts=contracts,
        actor_id=owner or db.user_a, confirmed=confirmed,
    )
    return res


def _add(db, sid, crm, *, tt="per_risk_xol", inc=date(2026, 4, 1), exp=None,
         status="IN_PROCESS"):
    return add_contract(
        submission_id=sid, actor_id=db.user_a,
        contract=ContractInput(crm_id=crm, treaty_type_code=tt, inception_date=inc,
                               expiration_date=exp, contract_status_code=status))


def _contract(sid, crm):
    return next(c for c in list_contracts(sid) if c.crm_id == crm)


def _marker(sid):
    return get_submission(sid).updated_at


def _bump():
    """Guarantee a strictly later app-supplied timestamp for the next write, so
    event history ordering (at DESC) is deterministic. Real transitions are
    seconds apart; the unit test compresses them, so nudge the clock."""
    time.sleep(0.01)


def _day(value):
    """The ISO day of a date column. The SQLite mirror stores dates as TEXT and
    reads them back as ``'2026-04-01'``; SQL Server reads the same column back as
    ``datetime.date(2026, 4, 1)``. This suite runs on both (tests/sqlserver/
    test_submission_service_integration.py re-collects it), so date assertions
    compare the day, not the driver's Python type."""
    return None if value is None else str(value)[:10]


# ── US1: create / get / cedant autocomplete ──────────────────────────────────

def test_create_writes_submission_and_initial_active_event(iteration1_db):
    res = _mk(iteration1_db)
    assert res.created is True and res.submission_id
    sub = get_submission(res.submission_id)
    assert sub is not None
    assert sub.status_code == "ACTIVE"
    assert sub.assigned_analyst_id == iteration1_db.user_a
    [contract] = sub.contracts
    assert contract.treaty_type_label == "Per Risk XOL"  # kind join populated
    assert contract.contract_status_label == "In Process"
    history = get_status_history(res.submission_id)
    assert len(history) == 1 and history[0].status_code == "ACTIVE"


def test_get_submission_unknown_id_returns_none(iteration1_db):
    assert get_submission("00000000-0000-0000-0000-000000000000") is None


def test_get_submission_has_no_access_restriction(iteration1_db):
    # Owned by B, still fully readable (no row-level security, FR-019).
    sid = _mk(iteration1_db, owner=iteration1_db.user_b).submission_id
    assert get_submission(sid).assigned_analyst_id == iteration1_db.user_b


def test_submission_entities_use_direct_associations_and_stored_counts(iteration2_db):
    first = _mk(iteration2_db, name="First").submission_id
    second = _mk(iteration2_db, name="Second", cedant="Second Re").submission_id
    edm_id = str(uuid.uuid4())
    rdm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, irp_id) "
        "VALUES (:id, 'SharedEDM', 'ready', 101)",
        {"id": edm_id}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_rdm (id, name, status, irp_id) "
        "VALUES (:id, 'SharedRDM', 'ready', 202)",
        {"id": rdm_id}, connection="WORKBENCH")
    for submission_id in (first, second):
        execute_command(
            "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
            {"s": submission_id, "e": edm_id}, connection="WORKBENCH")
        execute_command(
            "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
            {"s": submission_id, "r": rdm_id}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id) "
        "VALUES (:id, :edm, 'Portfolio', '301')",
        {"id": str(uuid.uuid4()), "edm": edm_id}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_analysis (id, rdm_id, irp_id, source_rdm_name, status_code) "
        "VALUES (:id, :rdm, '401', 'SharedRDM', 'ready')",
        {"id": str(uuid.uuid4()), "rdm": rdm_id}, connection="WORKBENCH")

    first_edms = svc.list_submission_edms(first)
    second_edms = svc.list_submission_edms(second)
    first_rdms = svc.list_submission_rdms(first)
    second_rdms = svc.list_submission_rdms(second)

    assert [(row.id, row.portfolio_count) for row in first_edms] == [(edm_id, 1)]
    assert [(row.id, row.portfolio_count) for row in second_edms] == [(edm_id, 1)]
    assert [(row.id, row.analysis_count) for row in first_rdms] == [(rdm_id, 1)]
    assert [(row.id, row.analysis_count) for row in second_rdms] == [(rdm_id, 1)]


def test_submission_entity_tables_sort_by_name_status_and_count(iteration2_db):
    submission_id = _mk(iteration2_db, name="Sorted entities").submission_id
    edm_ids = [str(uuid.uuid4()) for _ in range(3)]
    for edm_id, name, status in zip(
        edm_ids, ("BravoEDM", "AlphaEDM", "CharlieEDM"),
        ("ready", "importing", "error"), strict=True,
    ):
        execute_command(
            "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, :status)",
            {"id": edm_id, "name": name, "status": status},
            connection="WORKBENCH",
        )
        execute_command(
            "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
            {"s": submission_id, "e": edm_id}, connection="WORKBENCH",
        )
    for index in range(2):
        execute_command(
            "INSERT INTO irp_portfolio (id, edm_id, name, irp_id) "
            "VALUES (:id, :edm, :name, :irp)",
            {"id": str(uuid.uuid4()), "edm": edm_ids[0],
             "name": f"Portfolio{index}", "irp": str(index)},
            connection="WORKBENCH",
        )
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id) "
        "VALUES (:id, :edm, 'Portfolio', '3')",
        {"id": str(uuid.uuid4()), "edm": edm_ids[1]}, connection="WORKBENCH",
    )

    assert [row.name for row in svc.list_submission_edms(
        submission_id, sort="name", descending=True)] == [
            "CharlieEDM", "BravoEDM", "AlphaEDM"]
    assert [row.status for row in svc.list_submission_edms(
        submission_id, sort="status")] == ["error", "importing", "ready"]
    assert [row.portfolio_count for row in svc.list_submission_edms(
        submission_id, sort="count", descending=True)] == [2, 1, 0]

    rdm_ids = [str(uuid.uuid4()) for _ in range(2)]
    for rdm_id, name in zip(rdm_ids, ("SmallRDM", "LargeRDM"), strict=True):
        execute_command(
            "INSERT INTO irp_rdm (id, name, status) VALUES (:id, :name, 'ready')",
            {"id": rdm_id, "name": name}, connection="WORKBENCH",
        )
        execute_command(
            "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
            {"s": submission_id, "r": rdm_id}, connection="WORKBENCH",
        )
    execute_command(
        "INSERT INTO irp_analysis "
        "(id, rdm_id, irp_id, source_rdm_name, status_code) "
        "VALUES (:id, :rdm, '401', 'LargeRDM', 'ready')",
        {"id": str(uuid.uuid4()), "rdm": rdm_ids[1]}, connection="WORKBENCH",
    )

    assert [row.analysis_count for row in svc.list_submission_rdms(
        submission_id, sort="count", descending=True)] == [1, 0]


@pytest.mark.parametrize(
    ("sort", "descending", "expected"),
    [
        ("name", False, "e.name ASC, e.id ASC"),
        ("name", True, "e.name DESC, e.id ASC"),
        ("status", False, "e.status ASC, e.name ASC, e.id ASC"),
        ("count", True, "portfolio_count DESC, e.name ASC, e.id ASC"),
    ],
)
def test_submission_entity_table_order_uses_unique_columns(
    sort, descending, expected,
):
    assert svc._entity_table_order(
        sort, descending, entity_alias="e", count_alias="portfolio_count",
    ) == expected


def test_submission_import_creates_entity_association_and_provenance(
    iteration2_db, fake_irp, drive,
):
    submission_id = _mk(iteration2_db, name="Import target").submission_id

    edm = edm_service.import_edm(
        name="Imported_EDM", source_file_path=str(drive / "edm1.bak"),
        actor_id=iteration2_db.user_a, submission_id=submission_id)
    rdm = rdm_service.import_rdm(
        name="Imported_RDM", source_file_path=str(drive / "rdm1.mdf"),
        actor_id=iteration2_db.user_a, submission_id=submission_id)

    assert execute_scalar(
        "SELECT COUNT(*) FROM submission_edm WHERE submission_id=:s AND edm_id=:e",
        {"s": submission_id, "e": edm.entity_id}, connection="WORKBENCH") == 1
    assert execute_scalar(
        "SELECT COUNT(*) FROM submission_rdm WHERE submission_id=:s AND rdm_id=:r",
        {"s": submission_id, "r": rdm.entity_id}, connection="WORKBENCH") == 1
    heads = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_id IN (:e, :r) "
        "ORDER BY rwb_job_type",
        {"e": edm.entity_id, "r": rdm.entity_id}, connection="WORKBENCH")
    assert len(heads) == 2
    assert all(submission_id in row["input_data"] for row in heads)

    entity_jobs.run_pending()
    jobs = execute(
        "SELECT requested_from_submission_id, irp_edm_id, irp_rdm_id FROM irp_job "
        "WHERE requested_from_submission_id=:s ORDER BY irp_job_type",
        {"s": submission_id}, connection="WORKBENCH")
    assert len(jobs) == 2
    assert all(
        str(row["requested_from_submission_id"]).lower() == submission_id
        for row in jobs
    )
    assert sum(row["irp_edm_id"] is not None for row in jobs) == 1
    assert sum(row["irp_rdm_id"] is not None for row in jobs) == 1


def test_add_existing_candidates_exclude_related_and_deleted_entities(iteration2_db):
    submission_id = _mk(iteration2_db, name="Candidate target").submission_id
    available = str(uuid.uuid4())
    related = str(uuid.uuid4())
    deleted = str(uuid.uuid4())
    for entity_id, name, deleted_at in (
        (available, "AvailableEDM", None),
        (related, "RelatedEDM", None),
        (deleted, "DeletedEDM", "2026-01-01 00:00:00"),
    ):
        execute_command(
            "INSERT INTO irp_edm (id, name, status, deleted_at) "
            "VALUES (:id, :name, 'ready', :deleted)",
            {"id": entity_id, "name": name, "deleted": deleted_at},
            connection="WORKBENCH")
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
        {"s": submission_id, "e": related}, connection="WORKBENCH")

    page = svc.list_edm_candidates(submission_id, query="available", page=1)

    assert [row.id for row in page.rows] == [available]
    assert page.has_next is False


def test_add_existing_candidates_are_paginated(iteration2_db):
    submission_id = _mk(iteration2_db, name="Candidate pages").submission_id
    for index in range(svc.PAGE_SIZE + 1):
        execute_command(
            "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, 'ready')",
            {"id": str(uuid.uuid4()), "name": f"Candidate{index:03d}"},
            connection="WORKBENCH")

    # Scope to the inserted names: on the SQL Server tier the shared dev DB
    # already holds EDMs an earlier import created, and an unfiltered read would
    # count those too.
    first = svc.list_edm_candidates(submission_id, query="Candidate", page=1)
    second = svc.list_edm_candidates(submission_id, query="Candidate", page=2)

    assert len(first.rows) == svc.PAGE_SIZE and first.has_next is True
    assert len(second.rows) == 1 and second.has_next is False


def test_attach_existing_keeps_valid_selections_when_others_are_stale(iteration2_db):
    submission_id = _mk(iteration2_db, name="Attach target").submission_id
    valid = str(uuid.uuid4())
    already_related = str(uuid.uuid4())
    missing = str(uuid.uuid4())
    for entity_id, name in ((valid, "ValidRDM"), (already_related, "RelatedRDM")):
        execute_command(
            "INSERT INTO irp_rdm (id, name, status) VALUES (:id, :name, 'ready')",
            {"id": entity_id, "name": name}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
        {"s": submission_id, "r": already_related}, connection="WORKBENCH")

    result = svc.attach_rdms(
        submission_id=submission_id,
        rdm_ids=[valid, valid, already_related, missing, "invalid", "invalid"],
        actor_id=iteration2_db.user_a)

    assert result.attached_ids == [valid]
    assert result.stale_ids == ["invalid", already_related, missing]
    assert execute_scalar(
        "SELECT COUNT(*) FROM submission_rdm WHERE submission_id=:s",
        {"s": submission_id}, connection="WORKBENCH") == 2


def test_detach_removes_only_the_selected_submission_association(iteration2_db):
    first = _mk(iteration2_db, name="Detach first").submission_id
    second = _mk(iteration2_db, name="Detach second", cedant="Second Re").submission_id
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, 'SharedDetach', 'ready')",
        {"id": edm_id}, connection="WORKBENCH")
    for submission_id in (first, second):
        execute_command(
            "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
            {"s": submission_id, "e": edm_id}, connection="WORKBENCH")

    assert svc.detach_edm(submission_id=first, edm_id=edm_id) is True

    assert execute_one(
        "SELECT id FROM irp_edm WHERE id=:e", {"e": edm_id},
        connection="WORKBENCH") is not None
    assert execute_scalar(
        "SELECT COUNT(*) FROM submission_edm WHERE edm_id=:e",
        {"e": edm_id}, connection="WORKBENCH") == 1
    assert svc.list_submission_edms(second)[0].id == edm_id


def test_closed_submission_rejects_association_writes(iteration2_db, drive):
    submission_id = _mk(iteration2_db, name="Closed associations").submission_id
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, 'ClosedEDM', 'ready')",
        {"id": edm_id}, connection="WORKBENCH")
    set_statuses(
        submission_id=submission_id, modeling_status="COMPLETED", reason=None,
        expected_updated_at=_marker(submission_id), actor_id=iteration2_db.user_a)

    with pytest.raises(SubmissionClosed):
        svc.attach_edms(
            submission_id=submission_id, edm_ids=[edm_id],
            actor_id=iteration2_db.user_a)
    with pytest.raises(SubmissionClosed):
        svc.detach_edm(submission_id=submission_id, edm_id=edm_id)
    with pytest.raises(SubmissionClosed):
        edm_service.import_edm(
            name="ClosedImport", source_file_path=str(drive / "edm1.bak"),
            actor_id=iteration2_db.user_a, submission_id=submission_id)


def test_cedant_suggestions_distinct_and_sorted(iteration1_db):
    _mk(iteration1_db, name="A", cedant="Acme Mutual", tt="per_occurrence_cat_xol",
        inc=date(2026, 1, 1))
    _mk(iteration1_db, name="B", cedant="Acme Mutual", tt="aggregate_xol",
        inc=date(2026, 2, 1))   # same cedant, distinct attrs (no dup warning)
    _mk(iteration1_db, name="C", cedant="Acadia Re", tt="per_occurrence_cat_xol",
        inc=date(2026, 3, 1))
    _mk(iteration1_db, name="D", cedant="Beta Insurance", tt="stop_loss",
        inc=date(2026, 4, 1))
    # cedant_suggestions is a global DISTINCT with no owner scope, so restrict the
    # equality check to the cedants this test created — unrelated "Ac…" cedants in
    # a shared dev DB then can't fail it, while DISTINCT + sort order are still
    # verified (Acme Mutual appears once; Acadia sorts before Acme).
    out = cedant_suggestions("Ac")
    ours = [c for c in out if c in {"Acadia Re", "Acme Mutual"}]
    assert ours == ["Acadia Re", "Acme Mutual"]
    assert "Beta Insurance" not in out
    assert cedant_suggestions("") == []


def test_cedant_suggestions_match_anywhere_in_the_name(iteration1_db):
    # CR7: prefix matching never found "American Family Mutual" from "fam".
    _mk(iteration1_db, name="AF", cedant="American Family Mutual",
        tt="per_occurrence_cat_xol", inc=date(2026, 5, 1))
    assert "American Family Mutual" in cedant_suggestions("fam")


def test_cedant_suggestions_treat_wildcards_literally(iteration1_db):
    _mk(iteration1_db, name="Pct", cedant="50% Quota Co", tt="stop_loss",
        inc=date(2026, 6, 1))
    _mk(iteration1_db, name="Plain", cedant="Zeta Re", tt="stop_loss",
        inc=date(2026, 7, 1))
    out = cedant_suggestions("0%")
    assert "50% Quota Co" in out and "Zeta Re" not in out


def test_suggestions_ignore_a_one_character_term(iteration1_db):
    # A one-character LIKE '%a%' scans every submission for a menu the analyst
    # cannot read; both searches wait for the second character.
    _mk(iteration1_db, name="Solo", cedant="Solo Re", tt="stop_loss",
        inc=date(2026, 8, 1))
    assert cedant_suggestions("S") == []
    assert cedant_suggestions("  s  ") == []
    assert search_submissions_for_link("S") == []
    assert "Solo Re" in cedant_suggestions("So")


def test_suggestions_cap_the_row_count_in_the_query(iteration1_db):
    for index in range(6):
        _mk(iteration1_db, name=f"Capped {index}", cedant=f"Capped Re {index}",
            tt="stop_loss", inc=date(2026, 9, 1))
    assert len(cedant_suggestions("Capped Re", limit=3)) == 3
    assert len(search_submissions_for_link("Capped", limit=2)) == 2


# ── US2: list / filter / reassign ─────────────────────────────────────────────

def test_list_owner_predicate_is_not_an_access_gate(iteration1_db):
    a1 = _mk(iteration1_db, owner=iteration1_db.user_a, name="A1",
             cedant="Acme", inc=date(2026, 1, 1)).submission_id
    b1 = _mk(iteration1_db, owner=iteration1_db.user_b, name="B1",
             cedant="Beta", inc=date(2026, 2, 1)).submission_id
    # Owner filter is scoped to the (throwaway) owner, so exact-match is safe:
    # nothing else in the DB is owned by this freshly-created analyst.
    mine = {r.id for r in list_submissions(owner_ids=[iteration1_db.user_a]).rows}
    assert mine == {a1}
    # "All" (owner_ids=[]) must include BOTH owners' deals — that is the property
    # under test (no row-level scoping). Assert membership, not exact equality,
    # so unrelated deals already present in a shared dev DB don't fail the test.
    all_ids = {r.id for r in list_submissions(owner_ids=[]).rows}
    assert {a1, b1} <= all_ids  # All shows every deal regardless of owner


def test_list_filters_combine(iteration1_db):
    a = iteration1_db.user_a
    _mk(iteration1_db, owner=a, name="X", cedant="Acme", tt="per_occurrence_cat_xol",
        inc=date(2026, 1, 1), ty=2026)
    _mk(iteration1_db, owner=a, name="Y", cedant="Acme", tt="aggregate_xol",
        inc=date(2026, 6, 1), ty=2026)
    _mk(iteration1_db, owner=a, name="Z", cedant="Beta", tt="per_occurrence_cat_xol",
        inc=date(2025, 1, 1), ty=2025)

    # Scope every filter query to this test's throwaway owner so rows already
    # present in a shared dev DB can't skew the counts. owner_ids is itself just
    # another AND-predicate, so this still exercises filter combination.
    assert len(list_submissions(owner_ids=[a], cedant_name="Acme").rows) == 2
    assert len(list_submissions(
        owner_ids=[a], treaty_type_codes=["per_occurrence_cat_xol"]).rows) == 2
    assert len(list_submissions(owner_ids=[a], inception_date=date(2026, 6, 1)).rows) == 1
    assert len(list_submissions(owner_ids=[a], treaty_years=[2025]).rows) == 1
    # combined AND: Acme + per_occurrence_cat_xol → only X
    combo = list_submissions(
        owner_ids=[a], cedant_name="Acme", treaty_type_codes=["per_occurrence_cat_xol"]).rows
    assert len(combo) == 1 and combo[0].name == "X"


def test_list_search_by_name_ands_every_word(iteration1_db):
    """CR1/CR2 on the master list. Same rule as the "links to" picker, but the list's
    search box is name-only — cedant has its own field."""
    a = iteration1_db.user_a
    amfam = _mk(iteration1_db, owner=a, name="American Family Renewal",
                cedant="American Family Mutual", inc=date(2026, 5, 1)).submission_id
    ammod = _mk(iteration1_db, owner=a, name="American Modern Renewal",
                cedant="American Modern", inc=date(2026, 6, 1)).submission_id
    assert {r.id for r in list_submissions(
        owner_ids=[a], name="american family").rows} == {amfam}
    assert {r.id for r in list_submissions(
        owner_ids=[a], name="american").rows} == {amfam, ammod}
    # "mutual" is in a cedant and in no name, so the search box does not match it.
    assert list_submissions(owner_ids=[a], name="mutual").rows == []


def test_list_cedant_filter_matches_part_of_the_name(iteration1_db):
    """The cedant box is free text, so it has to match the way an analyst types it —
    a fragment, in whatever case. Exact equality returned nothing for "fam"."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, owner=a, name="Cedant partial",
              cedant="American Family Mutual").submission_id
    assert {r.id for r in list_submissions(owner_ids=[a], cedant_name="fam").rows} == {sid}
    assert {r.id for r in list_submissions(
        owner_ids=[a], cedant_name="american mutual").rows} == {sid}


def test_list_filter_by_owner_id(iteration1_db):
    """The Owner filter matches the assigned analyst's id, so two analysts sharing a
    display name stay apart."""
    a, b = iteration1_db.user_a, iteration1_db.user_b
    tag = uuid.uuid4().hex[:8]
    mine = _mk(iteration1_db, owner=a, name=f"Owned {tag} A").submission_id
    theirs = _mk(iteration1_db, owner=b, name=f"Owned {tag} B").submission_id
    execute_command(
        "UPDATE app_user SET display_name = 'Chris Doyle' WHERE id IN (:a, :b)",
        {"a": str(a), "b": str(b)}, connection="WORKBENCH")

    assert [r.id for r in list_submissions(
        name=f"Owned {tag}", owner_ids=[b]).rows] == [theirs]
    assert {r.id for r in list_submissions(
        name=f"Owned {tag}").rows} == {mine, theirs}


def test_list_owner_id_that_is_not_a_uuid_matches_nothing(iteration1_db):
    """A hand-typed ?owner=… reaches the uniqueidentifier comparison, which SQL
    Server refuses. It has to read as "no match", not as an error."""
    _mk(iteration1_db, owner=iteration1_db.user_a, name="Some deal")
    assert list_submissions(owner_ids=["not-a-uuid"]).rows == []


def test_list_filter_by_crm_ids_matches_whole_ids(iteration1_db):
    """P-10: exact, case-insensitive, trimmed; OR within the filter; a deal with
    two matching CRM IDs is still one row (EXISTS, not a join)."""
    a = iteration1_db.user_a
    tagged = _mk(iteration1_db, owner=a, name="Tagged deal", contracts=[]).submission_id
    other = _mk(iteration1_db, owner=a, name="Other deal", contracts=[]).submission_id
    _mk(iteration1_db, owner=a, name="Untagged deal", contracts=[])
    _add(iteration1_db, tagged, "CRM-12345")
    _add(iteration1_db, tagged, "CRM-4418")
    _add(iteration1_db, other, "CRM-1234")
    def rows(**kw):
        return [r.id for r in list_submissions(owner_ids=[a], **kw).rows]

    assert rows(crm_ids=["CRM-1234"]) == [other]          # "12345" is not "1234"
    assert rows(crm_ids=[" crm-12345 "]) == [tagged]      # case and whitespace ignored
    assert rows(crm_ids=["CRM-12345", "CRM-4418"]) == [tagged]
    assert rows(crm_ids=["CRM-4418", "CRM-1234"]) == [other, tagged]
    assert rows(crm_ids=["9999"]) == []
    assert rows(crm_ids=["CRM-4418"], name="Untagged") == []    # AND across filters


def test_list_rows_summarise_their_contracts(iteration1_db):
    """FR-007: CRM IDs in insertion order, distinct treaty types in kind order,
    the latest inception; a deal with no contract keeps the defaults."""
    a = iteration1_db.user_a
    tagged = _mk(iteration1_db, owner=a, name="Has contracts", contracts=[]).submission_id
    bare = _mk(iteration1_db, owner=a, name="No contracts", contracts=[]).submission_id
    _add(iteration1_db, tagged, "CRM-1", tt="stop_loss", inc=date(2026, 1, 1),
         status="WON")
    _bump()  # distinct inserted_at, so "oldest first" is deterministic here
    _add(iteration1_db, tagged, "CRM-2", tt="aggregate_xol", inc=date(2027, 1, 1))
    _bump()
    _add(iteration1_db, tagged, "CRM-3", tt="aggregate_xol", inc=date(2026, 6, 1),
         status="WON")
    rows = {r.id: r for r in list_submissions(owner_ids=[a]).rows}
    assert rows[tagged].crm_ids == ["CRM-1", "CRM-2", "CRM-3"]
    assert rows[tagged].treaty_type_labels == ["Aggregate XOL", "Stop Loss"]
    assert _day(rows[tagged].latest_inception_date) == "2027-01-01"
    assert rows[bare].crm_ids == [] and rows[bare].treaty_type_labels == []
    assert rows[bare].latest_inception_date is None


def test_list_filter_by_status(iteration1_db):
    a = iteration1_db.user_a
    active = _mk(iteration1_db, owner=a, name="Still active").submission_id
    done = _mk(iteration1_db, owner=a, name="Wrapped up",
               inc=date(2026, 7, 1)).submission_id
    set_statuses(submission_id=done, modeling_status="COMPLETED", reason="delivered",
                 expected_updated_at=_marker(done), actor_id=a)
    assert [r.id for r in list_submissions(
        owner_ids=[a], status_codes=["COMPLETED"]).rows] == [done]
    assert [r.id for r in list_submissions(
        owner_ids=[a], status_codes=["ACTIVE"]).rows] == [active]


def test_list_search_treats_a_wildcard_as_a_literal(iteration1_db):
    a = iteration1_db.user_a
    literal = _mk(iteration1_db, owner=a, name="100% quota share").submission_id
    _mk(iteration1_db, owner=a, name="100 quota share", inc=date(2026, 7, 1))
    assert [r.id for r in list_submissions(owner_ids=[a], name="100%").rows] == [literal]


def test_list_returns_one_page_at_a_time(iteration1_db):
    """Every read is capped at PAGE_SIZE, so ``_attach_contracts`` can never bind more
    ids than SQL Server accepts in one statement (2,100 bound parameters).

    Distinct cedants keep each create's look-alike check empty, and one shared
    inception date leaves the name as the only sort key, so the two pages are in a
    known order."""
    a = iteration1_db.user_a
    tag = uuid.uuid4().hex[:8]
    for i in range(svc.PAGE_SIZE + 2):
        _mk(iteration1_db, owner=a, name=f"{tag} deal {i:03d}",
            cedant=f"{tag} cedant {i:03d}", inc=date(2026, 4, 1))

    first = list_submissions(owner_ids=[a])
    assert len(first.rows) == svc.PAGE_SIZE
    assert first.page == 1 and first.has_next is True

    second = list_submissions(owner_ids=[a], page=2)
    assert [r.name for r in second.rows] == [
        f"{tag} deal {svc.PAGE_SIZE:03d}", f"{tag} deal {svc.PAGE_SIZE + 1:03d}"]
    assert second.page == 2 and second.has_next is False

    past_the_end = list_submissions(owner_ids=[a], page=3)
    assert past_the_end.rows == [] and past_the_end.has_next is False


def test_list_page_below_one_reads_the_first_page(iteration1_db):
    """A hand-typed ?page=0 must not reach the query as a negative offset."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, owner=a, name="Only deal").submission_id
    for page in (0, -5):
        result = list_submissions(owner_ids=[a], page=page)
        assert result.page == 1 and [r.id for r in result.rows] == [sid]


# ── D16: multi-value filters ─────────────────────────────────────────────────

def _four_mixed_deals(db):
    """One deal per (treaty type, treaty year, status) combination the tests below
    select on, so a filter that ORs too widely shows up as an extra row."""
    a = db.user_a
    made = {}
    for name, treaty_type, year in (
            ("Cat 2025", "per_occurrence_cat_xol", 2025),
            ("Cat 2026", "per_occurrence_cat_xol", 2026),
            ("Agg 2025", "aggregate_xol", 2025),
            ("Stop 2027", "stop_loss", 2027)):
        made[name] = _mk(db, owner=a, name=name, cedant=name, tt=treaty_type,
                         inc=date(year, 4, 1), ty=year).submission_id
    return a, made


def test_list_ors_within_a_multi_value_filter(iteration1_db):
    a, _ = _four_mixed_deals(iteration1_db)
    assert {r.name for r in list_submissions(
        owner_ids=[a], treaty_type_codes=["per_occurrence_cat_xol", "aggregate_xol"]).rows} == {
        "Cat 2025", "Cat 2026", "Agg 2025"}
    assert {r.name for r in list_submissions(
        owner_ids=[a], treaty_years=[2025, 2027]).rows} == {
        "Cat 2025", "Agg 2025", "Stop 2027"}


def test_list_ands_one_multi_value_filter_against_another(iteration1_db):
    a, _ = _four_mixed_deals(iteration1_db)
    assert {r.name for r in list_submissions(
        owner_ids=[a], treaty_type_codes=["per_occurrence_cat_xol", "aggregate_xol"],
        treaty_years=[2025, 2027]).rows} == {"Cat 2025", "Agg 2025"}


def test_list_treats_an_empty_list_as_no_filter(iteration1_db):
    a, _ = _four_mixed_deals(iteration1_db)
    assert len(list_submissions(
        owner_ids=[a], treaty_type_codes=[], treaty_years=[],
        status_codes=[]).rows) == 4


def test_list_filters_on_several_statuses(iteration1_db):
    a, made = _four_mixed_deals(iteration1_db)
    for name in ("Cat 2025", "Agg 2025"):
        set_statuses(submission_id=made[name], modeling_status="COMPLETED", reason=None,
                     expected_updated_at=_marker(made[name]), actor_id=a)
    set_statuses(submission_id=made["Cat 2026"], modeling_status="CANCELLED", reason=None,
                 expected_updated_at=_marker(made["Cat 2026"]), actor_id=a)
    assert {r.name for r in list_submissions(
        owner_ids=[a], status_codes=["COMPLETED", "CANCELLED"]).rows} == {
        "Cat 2025", "Agg 2025", "Cat 2026"}


def test_list_filters_on_several_owners(iteration1_db):
    a, b = iteration1_db.user_a, iteration1_db.user_b
    mine = _mk(iteration1_db, owner=a, name="Mine", cedant="Mine Re").submission_id
    theirs = _mk(iteration1_db, owner=b, name="Theirs",
                 cedant="Theirs Re").submission_id
    assert {r.id for r in list_submissions(owner_ids=[a, b]).rows} == {mine, theirs}
    # An id that is not a UUID binds NULL and matches nothing, without taking the
    # other owner's deals down with it.
    assert {r.id for r in list_submissions(
        owner_ids=[a, "not-a-uuid"]).rows} == {mine}


# ── D15: click-to-sort ───────────────────────────────────────────────────────

def _sorted_deals(db):
    """Three deals whose name, cedant, inception and treaty year each order them
    differently, so one ordering cannot pass for another."""
    a = db.user_a
    _mk(db, owner=a, name="Alpha", cedant="Zulu Re", inc=date(2026, 1, 1), ty=2026)
    _mk(db, owner=a, name="Bravo", cedant="Yankee Re", inc=date(2026, 3, 1), ty=2024)
    _mk(db, owner=a, name="Charlie", cedant="Xray Re", inc=date(2026, 2, 1), ty=2025)
    return a


@pytest.mark.parametrize(
    ("sort", "descending", "expected"),
    [
        ("name", False, ["Alpha", "Bravo", "Charlie"]),
        ("name", True, ["Charlie", "Bravo", "Alpha"]),
        ("cedant", False, ["Charlie", "Bravo", "Alpha"]),
        ("cedant", True, ["Alpha", "Bravo", "Charlie"]),
        ("inception", True, ["Bravo", "Charlie", "Alpha"]),
        ("inception", False, ["Alpha", "Charlie", "Bravo"]),
        ("year", True, ["Alpha", "Charlie", "Bravo"]),
        ("year", False, ["Bravo", "Charlie", "Alpha"]),
    ],
)
def test_list_sorts_on_each_whitelisted_column(
        iteration1_db, sort, descending, expected):
    a = _sorted_deals(iteration1_db)
    assert [r.name for r in list_submissions(
        owner_ids=[a], sort=sort, descending=descending).rows] == expected


@pytest.mark.parametrize("sort", ["status", "s.name; DROP TABLE submission", "", None])
def test_list_sort_outside_the_whitelist_is_rejected(iteration1_db, sort):
    """The key is looked up in SORT_COLUMNS, so nothing from the query string reaches
    the ORDER BY."""
    a = _sorted_deals(iteration1_db)
    with pytest.raises(KeyError):
        list_submissions(owner_ids=[a], sort=sort)


def test_list_defaults_to_newest_inception_first(iteration1_db):
    a = _sorted_deals(iteration1_db)
    assert [r.name for r in list_submissions(owner_ids=[a]).rows] == [
        "Bravo", "Charlie", "Alpha"]


def test_list_orders_on_the_latest_contract_inception(iteration1_db):
    """P-17: the sort key is an aggregate over the deal's contracts, and a deal
    with no contract is placed by its creation date — today, so ahead of last
    year's renewals and behind next year's."""
    a = iteration1_db.user_a
    layered = _mk(iteration1_db, owner=a, name="Layered", cedant="L Re",
                  inc=date(2025, 1, 1)).submission_id
    _add(iteration1_db, layered, "CRM-2", inc=date(2027, 1, 1))
    _mk(iteration1_db, owner=a, name="Next year", cedant="N Re", inc=date(2026, 12, 1))
    _mk(iteration1_db, owner=a, name="Last year", cedant="P Re", inc=date(2025, 6, 1))
    _mk(iteration1_db, owner=a, name="No contract", cedant="Z Re", contracts=[])
    assert [r.name for r in list_submissions(owner_ids=[a]).rows] == [
        "Layered", "Next year", "No contract", "Last year"]
    assert [r.name for r in list_submissions(
        owner_ids=[a], sort="inception", descending=False).rows] == [
        "Last year", "No contract", "Next year", "Layered"]


def test_list_breaks_a_sort_tie_the_same_way_on_every_page(iteration1_db):
    """Every deal here shares a treaty year, so the sorted column decides nothing and
    the tiebreaker decides the whole order. Without it the two pages could repeat a
    deal and skip another."""
    a = iteration1_db.user_a
    for i in range(svc.PAGE_SIZE + 2):
        _mk(iteration1_db, owner=a, name=f"deal {i:03d}", cedant=f"cedant {i:03d}",
            inc=date(2026, 4, 1), ty=2026)
    first = list_submissions(owner_ids=[a], sort="year", descending=True)
    second = list_submissions(owner_ids=[a], sort="year", descending=True, page=2)
    names = [r.name for r in first.rows] + [r.name for r in second.rows]
    assert names == sorted(names)
    assert len(set(names)) == svc.PAGE_SIZE + 2


def test_status_kinds_lists_every_status_in_display_order(iteration1_db):
    assert svc.status_kinds() == [
        ("ACTIVE", "Active"), ("COMPLETED", "Completed"), ("CANCELLED", "Cancelled")]


def test_reassign_owner_moves_my_view(iteration1_db):
    sid = _mk(iteration1_db, owner=iteration1_db.user_a).submission_id
    reassign_owner(submission_id=sid, new_owner_id=iteration1_db.user_b,
                   expected_updated_at=_marker(sid), actor_id=iteration1_db.user_a)
    assert get_submission(sid).assigned_analyst_id == iteration1_db.user_b
    assert list_submissions(owner_ids=[iteration1_db.user_a]).rows == []
    assert len(list_submissions(owner_ids=[iteration1_db.user_b]).rows) == 1
    # Still visible in the global ("everyone") list — assert the deal is present
    # rather than that it is the ONLY row, so a shared dev DB doesn't fail this.
    assert sid in {r.id for r in list_submissions(owner_ids=[]).rows}


def test_reassign_owner_stale_marker_conflicts(iteration1_db):
    sid = _mk(iteration1_db).submission_id
    with pytest.raises(ConcurrencyConflict):
        reassign_owner(submission_id=sid, new_owner_id=iteration1_db.user_b,
                       expected_updated_at=STALE, actor_id=iteration1_db.user_a)


# ── US3: status lifecycle (event-sourced) ─────────────────────────────────────

def test_status_transitions_reopen_and_history(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id  # event 1: ACTIVE
    _bump(); set_statuses(submission_id=sid, modeling_status="COMPLETED", reason="done",
                          expected_updated_at=_marker(sid), actor_id=a)
    assert get_submission(sid).status_code == "COMPLETED"
    _bump(); set_statuses(submission_id=sid, modeling_status="ACTIVE", reason=None,
                          expected_updated_at=_marker(sid), actor_id=a)  # reopen COMPLETED→ACTIVE
    _bump(); set_statuses(submission_id=sid, modeling_status="CANCELLED", reason="pulled",
                          expected_updated_at=_marker(sid), actor_id=a)
    _bump(); set_statuses(submission_id=sid, modeling_status="ACTIVE", reason=None,
                          expected_updated_at=_marker(sid), actor_id=a)  # reopen CANCELLED→ACTIVE
    assert get_submission(sid).status_code == "ACTIVE"

    history = get_status_history(sid)
    assert [e.status_code for e in history] == [
        "ACTIVE", "CANCELLED", "ACTIVE", "COMPLETED", "ACTIVE",  # newest first
    ]


def test_same_status_is_a_recorded_no_op(iteration1_db):
    sid = _mk(iteration1_db).submission_id
    _bump(); set_statuses(submission_id=sid, modeling_status="ACTIVE", reason=None,
                          expected_updated_at=_marker(sid), actor_id=iteration1_db.user_a)
    assert get_submission(sid).status_code == "ACTIVE"
    assert len(get_status_history(sid)) == 2  # ACTIVE (create) + ACTIVE (no-op)


def test_read_only_gate_blocks_mutations_when_closed(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
    set_statuses(submission_id=sid, modeling_status="COMPLETED", reason=None,
                 expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        reassign_owner(submission_id=sid, new_owner_id=iteration1_db.user_b,
                       expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, directory_path="/x")


def test_no_delete_function_exists(iteration1_db):
    # SC-005 / FR-014: there is no delete path for submissions.
    assert not hasattr(svc, "delete_submission")
    assert [n for n in dir(svc) if "delete" in n.lower() and not n.startswith("_")] == []


# ── Contracts (spec 017 as amended 2026-09-21: FR-003, FR-004, FR-005) ────────

def _row(crm, tt="per_risk_xol", inc=date(2027, 1, 1), exp=None, status="IN_PROCESS"):
    return ContractInput(crm_id=crm, treaty_type_code=tt, inception_date=inc,
                         expiration_date=exp, contract_status_code=status)


def test_create_with_no_contract_leaves_treaty_year_blank(iteration1_db):
    sid = _mk(iteration1_db, contracts=[], ty=None).submission_id
    sub = get_submission(sid)
    assert sub.contracts == [] and sub.treaty_year is None
    assert sub.client_id is None and sub.data_vintage is None


def test_create_writes_every_contract_row_in_one_transaction(iteration1_db):
    res = create_submission(
        name="TY2701_Allstate", cedant_name="Allstate", data_vintage="2026-06-30",
        contracts=[_row("A-1", "per_occurrence_cat_xol"),
                   _row("A-2", "aggregate_xol"),
                   _row("A-3", "top_and_drop", exp=date(2029, 12, 31), status="WON")],
        actor_id=iteration1_db.user_a, confirmed=True)
    sub = get_submission(res.submission_id)
    assert [c.crm_id for c in sub.contracts] == ["A-1", "A-2", "A-3"]
    assert [_day(c.expiration_date) for c in sub.contracts] == [
        "2027-12-31", "2027-12-31", "2029-12-31"]          # blank → inception + 1y − 1d
    assert [c.contract_status_code for c in sub.contracts] == [
        "IN_PROCESS", "IN_PROCESS", "WON"]
    assert sub.treaty_year == 2027 and _day(sub.data_vintage) == "2026-06-30"


@pytest.mark.parametrize(("rows", "index", "message"), [
    ([_row("A-1"), _row("  ")], 1, "Enter the CRM ID."),
    ([_row("A-1"), _row(" a-1 ")], 1, "A-1 is already a contract on this submission."),
    ([_row("A-1", tt="cat_xol")], 0, "Choose a treaty type from the list."),
    ([_row("A-1", status="HOLD")], 0, "Choose a contract status from the list."),
    ([_row("A-1", inc=None)], 0, "Enter the inception date."),
    ([_row("A-1", inc="yesterday")], 0, "Enter the dates as YYYY-MM-DD."),
])
def test_create_refuses_a_bad_contract_row_and_writes_nothing(
        iteration1_db, rows, index, message):
    with pytest.raises(ContractInvalid) as raised:
        create_submission(name="Refused", cedant_name="R Re", contracts=rows,
                          actor_id=iteration1_db.user_a, confirmed=True)
    assert raised.value.index == index and str(raised.value) == message
    assert raised.value.owner is None
    assert list_submissions(owner_ids=[iteration1_db.user_a], name="Refused").rows == []


def test_create_refuses_a_crm_id_that_is_a_contract_on_another_deal(iteration1_db):
    """FR-003 (note 33 D12-D14): the refusal names the row and the owning deal,
    whatever the case and whitespace of the typed value."""
    owner = _mk(iteration1_db, name="Owner deal", crm="X-1").submission_id
    with pytest.raises(ContractInvalid) as raised:
        create_submission(name="Second", cedant_name="S Re",
                          contracts=[_row("X-9"), _row(" x-1 ")],
                          actor_id=iteration1_db.user_a, confirmed=True)
    assert raised.value.index == 1
    assert str(raised.value) == "x-1 is already a contract on"
    assert raised.value.owner == ContractOwner(owner, "Owner deal")
    assert list_submissions(owner_ids=[iteration1_db.user_a], name="Second").rows == []


def test_add_and_edit_refuse_a_crm_id_another_deal_holds_but_a_row_keeps_its_own(
        iteration1_db):
    a = iteration1_db.user_a
    owner = _mk(iteration1_db, name="Owner deal", crm="X-1").submission_id
    sid = _mk(iteration1_db, name="Second", crm="Y-1").submission_id
    with pytest.raises(ContractInvalid) as raised:
        _add(iteration1_db, sid, "X-1")
    assert raised.value.owner == ContractOwner(owner, "Owner deal")
    assert raised.value.index == 0
    y1 = _contract(sid, "Y-1")
    with pytest.raises(ContractInvalid) as raised:
        update_contract(contract_id=y1.id, actor_id=a,
                        expected_updated_at=y1.updated_at, contract=_row("x-1"))
    assert raised.value.owner == ContractOwner(owner, "Owner deal")
    # The same-deal duplicate keeps its own wording and names no owner.
    _add(iteration1_db, sid, "Y-2")
    with pytest.raises(ContractInvalid) as raised:
        _add(iteration1_db, sid, " y-2 ")
    assert str(raised.value) == "Y-2 is already a contract on this submission."
    assert raised.value.owner is None
    # A row keeps its own CRM ID on edit, in any case.
    update_contract(contract_id=y1.id, actor_id=a, expected_updated_at=y1.updated_at,
                    contract=_row("y-1", "stop_loss"))
    assert _contract(sid, "y-1").treaty_type_code == "stop_loss"
    assert [c.crm_id for c in list_contracts(sid)] == ["y-1", "Y-2"]


@pytest.mark.parametrize("modeling_status", ["COMPLETED", "CANCELLED"])
def test_a_crm_id_on_a_closed_deal_still_blocks(iteration1_db, modeling_status):
    """Note 33 decision 3: the owner's Modeling status never frees its CRM IDs."""
    a = iteration1_db.user_a
    owner = _mk(iteration1_db, name="Closed deal", crm="Z-1").submission_id
    set_statuses(submission_id=owner, modeling_status=modeling_status, reason="done",
                 expected_updated_at=_marker(owner), actor_id=a)
    with pytest.raises(ContractInvalid) as raised:
        _mk(iteration1_db, name="Second", crm="Z-1")
    assert raised.value.owner == ContractOwner(owner, "Closed deal")


def test_the_crm_id_index_refuses_a_case_variant_written_around_the_service(
        iteration1_db):
    """``uq_contract_crm_id`` is the race catch behind the service lookup; the
    SQLite mirror's ``COLLATE NOCASE`` index must collide like SQL Server's
    case-insensitive default collation does."""
    sid = _mk(iteration1_db, crm="abc").submission_id
    with pytest.raises(SQLServerQueryError) as raised:
        execute_command(
            svc._CONTRACT_INSERT,
            {"id": str(uuid.uuid4()), "sid": sid, "crm_id": "ABC", "tt": "per_risk_xol",
             "inc": date(2026, 4, 1), "exp": date(2027, 3, 31), "status": "IN_PROCESS",
             "now": svc._utcnow(), "actor": iteration1_db.user_a},
            connection="WORKBENCH")
    assert is_unique_violation(raised.value)
    assert [c.crm_id for c in list_contracts(sid)] == ["abc"]


def test_expiration_default_handles_a_leap_day(iteration1_db):
    sid = _mk(iteration1_db, inc=date(2028, 2, 29)).submission_id
    assert _day(get_submission(sid).contracts[0].expiration_date) == "2029-02-27"


def test_add_update_and_remove_a_contract(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, contracts=[]).submission_id
    cid = _add(iteration1_db, sid, "T-100")
    _bump()
    _add(iteration1_db, sid, "T-200", tt="stop_loss")
    assert [c.crm_id for c in list_contracts(sid)] == ["T-100", "T-200"]
    update_contract(
        contract_id=cid, actor_id=a, expected_updated_at=_contract(sid, "T-100").updated_at,
        contract=_row("T-100", "aggregate_xol", inc=date(2027, 7, 1),
                      exp=date(2028, 6, 30), status="WON"))
    edited = _contract(sid, "T-100")
    assert edited.treaty_type_code == "aggregate_xol"
    assert _day(edited.inception_date) == "2027-07-01"
    assert _day(edited.expiration_date) == "2028-06-30"
    assert edited.contract_status_code == "IN_PROCESS"   # status is not the editor's
    remove_contract(contract_id=cid, actor_id=a)
    assert [c.crm_id for c in list_contracts(sid)] == ["T-200"]
    remove_contract(contract_id=cid, actor_id=a)          # already gone: no-op


def test_add_and_update_refuse_a_duplicate_crm_id_on_the_deal(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, contracts=[]).submission_id
    _add(iteration1_db, sid, "T-100")
    second = _add(iteration1_db, sid, "T-200")
    with pytest.raises(ContractInvalid):
        _add(iteration1_db, sid, " t-100 ")
    with pytest.raises(ContractInvalid):
        update_contract(contract_id=second, actor_id=a,
                        expected_updated_at=_contract(sid, "T-200").updated_at,
                        contract=_row("T-100"))
    # A row may keep its own CRM ID when edited.
    update_contract(contract_id=second, actor_id=a,
                    expected_updated_at=_contract(sid, "T-200").updated_at,
                    contract=_row("t-200", "stop_loss"))
    assert _contract(sid, "t-200").treaty_type_code == "stop_loss"


def test_contract_attribute_writes_are_gated_on_active_but_status_is_not(
        iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, contracts=[]).submission_id
    cid = _add(iteration1_db, sid, "T-100")
    set_statuses(submission_id=sid, modeling_status="COMPLETED", reason=None,
                 expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        _add(iteration1_db, sid, "T-200")
    with pytest.raises(SubmissionClosed):
        update_contract(contract_id=cid, actor_id=a,
                        expected_updated_at=_contract(sid, "T-100").updated_at,
                        contract=_row("T-100", "stop_loss"))
    with pytest.raises(SubmissionClosed):
        remove_contract(contract_id=cid, actor_id=a)
    set_contract_status(contract_id=cid, to_status="WON", actor_id=a,
                        expected_updated_at=_contract(sid, "T-100").updated_at)
    assert _contract(sid, "T-100").contract_status_code == "WON"
    assert get_submission(sid).status_code == "COMPLETED"
    assert len(get_status_history(sid)) == 2   # create + Completed; no contract event


def test_contract_status_stale_marker_conflicts_and_unknown_code_is_refused(
        iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, crm="CRM-1").submission_id
    cid = _contract(sid, "CRM-1").id
    with pytest.raises(ConcurrencyConflict):
        set_contract_status(contract_id=cid, to_status="WON",
                            expected_updated_at=STALE, actor_id=a)
    with pytest.raises(ValueError):
        set_contract_status(contract_id=cid, to_status="HOLD", actor_id=a,
                            expected_updated_at=_contract(sid, "CRM-1").updated_at)
    with pytest.raises(ConcurrencyConflict):
        update_contract(contract_id=cid, actor_id=a, expected_updated_at=STALE,
                        contract=_row("CRM-1"))
    assert _contract(sid, "CRM-1").contract_status_code == "IN_PROCESS"


def test_one_deal_holds_contracts_in_different_statuses(iteration1_db):
    """Note 32 D17: eight CRM IDs on one modeling package, some bound, some lost."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, contracts=[]).submission_id
    for crm in ("A-1", "A-2", "A-3"):
        _add(iteration1_db, sid, crm)
        _bump()  # distinct inserted_at, so the rows read back in entry order
    for crm, status in (("A-1", "WON"), ("A-2", "LOST")):
        set_contract_status(contract_id=_contract(sid, crm).id, to_status=status,
                            expected_updated_at=_contract(sid, crm).updated_at, actor_id=a)
    assert [c.contract_status_code for c in list_contracts(sid)] == [
        "WON", "LOST", "IN_PROCESS"]


# ── US5: non-unique identity / duplicate warning / edit guards ────────────────

def test_find_similar_name_and_attribute_arms(iteration1_db):
    first = _mk(iteration1_db, name="TY2604_Acme", cedant="Acme Mutual",
                tt="per_occurrence_cat_xol", inc=date(2026, 4, 1)).submission_id
    # find_similar is a global dedup lookup with no owner scope; assert our
    # planted row's presence/absence rather than exact result sets, so unrelated
    # look-alikes already in a shared dev DB don't fail the test.
    # name-match arm (different cedant, different contracts)
    by_name = find_similar(name="TY2604_Acme", cedant_name="Zzz",
                           contract_terms=[("stop_loss", date(2030, 1, 1))])
    assert first in {r.id for r in by_name}
    # attribute-match arm (different name): same cedant with a contract of the
    # same treaty type and inception as one of the posted rows
    by_attr = find_similar(
        name="Totally Different", cedant_name="Acme Mutual",
        contract_terms=[("stop_loss", date(2030, 1, 1)),
                        ("per_occurrence_cat_xol", date(2026, 4, 1))])
    assert first in {r.id for r in by_attr}
    # same cedant, no contract posted → name alone decides
    assert first not in {r.id for r in find_similar(
        name="Totally Different", cedant_name="Acme Mutual")}
    # genuinely new deal → our row is not a look-alike
    assert first not in {r.id for r in find_similar(
        name="Brand New", cedant_name="Nobody Re",
        contract_terms=[("stop_loss", date(2031, 1, 1))])}
    # exclude_id skips the row being renamed
    assert first not in {r.id for r in find_similar(
        name="TY2604_Acme", cedant_name="Acme Mutual",
        contract_terms=[("per_occurrence_cat_xol", date(2026, 4, 1))],
        exclude_id=first)}


def test_create_duplicate_warns_then_confirms(iteration1_db):
    first = _mk(iteration1_db, name="TY2604_Acme").submission_id
    res = _mk(iteration1_db, name="TY2604_Acme", confirmed=False)  # unconfirmed dup
    assert res.created is False and res.submission_id is None
    assert first in {w.id for w in res.warnings}  # our row flagged as a look-alike
    res2 = _mk(iteration1_db, name="TY2604_Acme", confirmed=True)
    assert res2.created is True and res2.submission_id


def test_update_rename_warns_then_confirms(iteration1_db):
    a = iteration1_db.user_a
    first = _mk(iteration1_db, name="Alpha", cedant="C1", tt="per_occurrence_cat_xol",
                inc=date(2026, 1, 1)).submission_id
    second = _mk(iteration1_db, name="Beta", cedant="C2", tt="stop_loss",
                 inc=date(2026, 2, 1)).submission_id
    # rename second → Alpha collides with first (name arm)
    r = update_submission(submission_id=second, expected_updated_at=_marker(second),
                          actor_id=a, name="Alpha")
    assert r.updated is False and first in {w.id for w in r.warnings}
    r2 = update_submission(submission_id=second, expected_updated_at=_marker(second),
                           actor_id=a, confirmed=True, name="Alpha")
    assert r2.updated is True
    assert get_submission(second).name == "Alpha"


def test_update_self_link_rejected(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
    with pytest.raises(SelfLinkError):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, links_to_submission_id=sid)


@pytest.mark.parametrize("link_value", [str(uuid.uuid4()), "not-a-uuid"])
def test_create_with_an_unknown_link_target_is_rejected(iteration1_db, link_value):
    # links_to_submission_id is a foreign key to submission.id, so an id naming no
    # deal has to be refused before the INSERT turns it into a driver error.
    with pytest.raises(UnknownLinkError):
        create_submission(
            name="Stale link", cedant_name="American Family",
            links_to_submission_id=link_value, actor_id=iteration1_db.user_a,
            confirmed=True)
    # Scoped to this test's throwaway owner, so the assertion is "the deal was not
    # written" rather than "a page of the list is the same length".
    assert list_submissions(
        owner_ids=[iteration1_db.user_a], name="Stale link").rows == []


@pytest.mark.parametrize("link_value", [str(uuid.uuid4()), "not-a-uuid"])
def test_update_to_an_unknown_link_target_is_rejected(iteration1_db, link_value):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Keeps its link").submission_id
    with pytest.raises(UnknownLinkError):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, links_to_submission_id=link_value)
    assert get_submission(sid).links_to_submission_id is None


def test_link_target_is_kept_across_an_edit_that_never_mentions_it(iteration1_db):
    # The merged value is re-checked on every update, so an untouched link must
    # still pass — the check reads the target, it does not require it to be resent.
    a = iteration1_db.user_a
    target = _mk(iteration1_db, name="Last year", inc=date(2025, 4, 1)).submission_id
    sid = _mk(iteration1_db, name="This year").submission_id
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, links_to_submission_id=target)
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, name="This year, renamed")
    assert get_submission(sid).links_to_submission_id == target


def test_treaty_year_defaults_to_the_earliest_contract_inception_year(iteration1_db):
    """P-20: the earliest contract's inception, not a designated one."""
    sid = create_submission(
        name="No year given", cedant_name="Y Re", treaty_year=None,
        contracts=[_row("A-1", inc=date(2027, 1, 1)), _row("A-2", inc=date(2026, 7, 1))],
        actor_id=iteration1_db.user_a, confirmed=True).submission_id
    assert get_submission(sid).treaty_year == 2026


def test_entered_treaty_year_survives_create_and_update(iteration1_db):
    # A December inception is often written into the following treaty year, so an
    # entered value must never be replaced by the derived one (CR5).
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Dec incept", inc=date(2026, 12, 15),
              ty=2027).submission_id
    assert get_submission(sid).treaty_year == 2027
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, treaty_year=2027, directory_path="/x")
    assert get_submission(sid).treaty_year == 2027


def test_clearing_treaty_year_on_update_refills_it_from_the_contracts(
        iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Cleared year", inc=date(2026, 4, 1),
              ty=2030).submission_id
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, treaty_year=None)
    assert get_submission(sid).treaty_year == 2026
    bare = _mk(iteration1_db, name="No contracts", contracts=[], ty=2030).submission_id
    update_submission(submission_id=bare, expected_updated_at=_marker(bare),
                      actor_id=a, confirmed=True, treaty_year=None)
    assert get_submission(bare).treaty_year is None


# ── "Links to" picker search (CR8) ───────────────────────────────────────────

def test_search_for_link_ands_every_term(iteration1_db):
    # CR2: "There must be 1000 companies that have American in the name."
    amfam = _mk(iteration1_db, name="TY2506_AmericanFamily",
                cedant="American Family Mutual", inc=date(2025, 6, 1)).submission_id
    amnat = _mk(iteration1_db, name="TY2501_AmericanNational",
                cedant="American National", inc=date(2025, 1, 1)).submission_id
    found = {row.id for row in search_submissions_for_link("american fam")}
    assert amfam in found and amnat not in found
    both = {row.id for row in search_submissions_for_link("american")}
    assert {amfam, amnat} <= both


def test_search_for_link_matches_every_word_however_many(iteration1_db):
    sid = _mk(iteration1_db, name="American Family Renewal",
              cedant="American Family Mutual", inc=date(2026, 4, 1)).submission_id
    matching = ["american", "family", "renewal", "mutual", "am", "fam", "ren"]
    assert sid in {r.id for r in search_submissions_for_link(" ".join(matching))}
    # The word past the ones that match still narrows the search — a term is never
    # searched on a prefix of its words, which would return deals the analyst's
    # last word rules out.
    with_one_miss = " ".join(matching + ["nomatch"])
    assert search_submissions_for_link(with_one_miss) == []


def test_search_for_link_matches_name_or_cedant(iteration1_db):
    sid = _mk(iteration1_db, name="Opaque code 9912",
              cedant="Zenith Mutual", inc=date(2026, 2, 1)).submission_id
    assert sid in {r.id for r in search_submissions_for_link("9912")}
    assert sid in {r.id for r in search_submissions_for_link("zenith")}


def test_search_for_link_excludes_the_submission_being_edited(iteration1_db):
    sid = _mk(iteration1_db, name="Sole Match Deal",
              cedant="Solo Re", inc=date(2026, 3, 1)).submission_id
    assert sid in {r.id for r in search_submissions_for_link("Sole Match")}
    assert search_submissions_for_link("Sole Match", exclude_id=sid) == []


def test_search_for_link_empty_term_returns_nothing(iteration1_db):
    _mk(iteration1_db, name="Anything", inc=date(2026, 8, 1))
    assert search_submissions_for_link("") == []
    assert search_submissions_for_link("   ") == []


# ── Global search provider (PRD §19) ─────────────────────────────────────────

def test_search_global_matches_name_or_cedant(iteration1_db):
    sid = _mk(iteration1_db, name="Coastal Re HO 2026",
              cedant="Coastal Re", inc=date(2026, 1, 1)).submission_id
    assert sid in {r.id for r in search_submissions_global("coastal")}
    assert sid in {r.id for r in search_submissions_global("HO 2026")}


def test_search_global_matches_tagged_crm_id(iteration1_db):
    sid = _mk(iteration1_db, name="Zenith Mutual 2026",
              cedant="Zenith Mutual", inc=date(2026, 1, 1), crm="CRM-9912").submission_id
    assert sid in {r.id for r in search_submissions_global("9912")}


def test_search_global_empty_term_returns_nothing(iteration1_db):
    _mk(iteration1_db, name="Anything", inc=date(2026, 8, 1))
    assert search_submissions_global("") == []
    assert search_submissions_global("   ") == []


def test_update_stale_marker_conflicts(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Unique Deal").submission_id
    with pytest.raises(ConcurrencyConflict):
        update_submission(submission_id=sid, expected_updated_at=STALE,
                          actor_id=a, confirmed=True, directory_path="/staging/x")


# ── spec 017 Phase 2: kind reads, Submission status default, the view ────────

def test_treaty_type_kinds_reads_the_eleven_codes_in_sort_order(iteration1_db):
    kinds = svc.treaty_type_kinds()
    assert [code for code, _ in kinds] == [
        "aggregate_xol", "aggregate_cat_xol", "risk_aggregate_xol",
        "per_occurrence_xol", "per_occurrence_cat_xol", "per_risk_xol", "stop_loss",
        "reinstatement_premium_protection", "second_third_fourth_event_risk_exposed",
        "top_and_drop", "top_and_aggregate"]
    assert dict(kinds)["top_and_drop"] == "Top & Drop"


def test_contract_status_kinds_reads_the_three_codes(iteration1_db):
    assert svc.contract_status_kinds() == [
        ("IN_PROCESS", "In Process"), ("WON", "Won"), ("LOST", "Lost")]


def test_view_emits_one_row_per_contract_and_none_for_a_deal_without(iteration1_db):
    a = iteration1_db.user_a
    tagged = _mk(iteration1_db, owner=a, name="Tagged", contracts=[],
                 ty=2026).submission_id
    bare = _mk(iteration1_db, owner=a, name="Bare", contracts=[]).submission_id
    _add(iteration1_db, tagged, "T-100", status="WON")
    _add(iteration1_db, tagged, "T-200", tt="stop_loss", inc=date(2026, 7, 1))
    # Scoped to this test's two deals: the view is global, and a shared dev DB
    # carries other analysts' rows.
    rows = execute(
        "SELECT * FROM v_contract WHERE submission_id IN (:tagged, :bare) "
        "ORDER BY crm_id",
        {"tagged": tagged, "bare": bare}, connection="WORKBENCH")
    assert [(r["submission_name"], r["crm_id"]) for r in rows] == [
        ("Tagged", "T-100"), ("Tagged", "T-200")]
    first = rows[0]
    # Read straight off the view, so the id is the driver's: SQL Server returns
    # uniqueidentifier UPPERCASE, while the service lowercases every id it hands out.
    assert str(first["submission_id"]).lower() == tagged
    assert first["treaty_type_code"] == "per_risk_xol"
    assert _day(first["inception_date"]) == "2026-04-01"
    assert _day(first["expiration_date"]) == "2027-03-31"
    assert first["contract_status_code"] == "WON"
    assert first["modeling_status_code"] == "ACTIVE"
    assert first["treaty_year"] == 2026 and first["cedant_name"] == "American Family"
    assert rows[1]["contract_status_code"] == "IN_PROCESS"


def test_filter_clauses_prefix_every_parameter_and_group_the_contract_ones(
        iteration1_db):
    clauses, params = svc.submission_filter_clauses(
        {"owner_ids": [iteration1_db.user_a], "name": "am fam", "cedant_name": "mutual",
         "crm_ids": ["T-1"], "treaty_type_codes": ["per_risk_xol"],
         "inception_date": "2026-04-01", "treaty_years": [2026],
         "status_codes": ["ACTIVE"], "contract_status_codes": ["WON"], "client_ids": [27],
         "in_force_as_of": date(2026, 6, 1)}, alias="x")
    assert all("x." in clause for clause in clauses)
    assert "s." not in " ".join(clauses)
    assert set(params) == {"owner0", "n0", "n1", "c0", "crm0", "tt0", "inc", "ty0",
                           "ms0", "cs0", "cl0", "won", "asof"}
    assert params["won"] == svc.WON == "WON"
    # P-18: every contract-level clause sits in the one EXISTS over contract.
    [exists] = [c for c in clauses if c.startswith("EXISTS (SELECT 1 FROM contract c")]
    for fragment in ("c.crm_id", "c.treaty_type_code", "c.inception_date = :inc",
                     "c.contract_status_code IN", "c.expiration_date >= :asof"):
        assert fragment in exists
    assert svc.submission_filter_clauses({}) == ([], {})


def test_list_filters_on_contract_status(iteration1_db):
    a = iteration1_db.user_a
    won = _mk(iteration1_db, owner=a, name="Won", crm="W-1").submission_id
    _mk(iteration1_db, owner=a, name="Open", inc=date(2026, 7, 1))
    set_contract_status(contract_id=_contract(won, "W-1").id, to_status="WON",
                        expected_updated_at=_contract(won, "W-1").updated_at, actor_id=a)
    assert [r.id for r in list_submissions(
        owner_ids=[a], contract_status_codes=["WON"]).rows] == [won]
    assert len(list_submissions(
        owner_ids=[a], contract_status_codes=["WON", "IN_PROCESS"]).rows) == 2


def test_contract_level_filters_are_met_by_one_contract_together(iteration1_db):
    """P-18: a Won Aggregate XOL beside an In Process Per Risk XOL does not
    satisfy "Per Risk XOL + Won"."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, owner=a, name="Mixed", contracts=[]).submission_id
    _add(iteration1_db, sid, "A-1", tt="aggregate_xol", status="WON")
    _add(iteration1_db, sid, "A-2", tt="per_risk_xol")
    def names(**kw):
        return [r.name for r in list_submissions(owner_ids=[a], **kw).rows]
    assert names(treaty_type_codes=["per_risk_xol"], contract_status_codes=["WON"]) == []
    assert names(treaty_type_codes=["aggregate_xol"], contract_status_codes=["WON"]) == [
        "Mixed"]
    assert names(crm_ids=["A-2"], contract_status_codes=["WON"]) == []
    assert names(crm_ids=["A-2"], treaty_type_codes=["per_risk_xol"]) == ["Mixed"]


# ── spec 017 US2: client on the deal ─────────────────────────────────────────

def test_client_id_is_stored_updated_and_filtered(iteration1_db, loss_clients):
    a = iteration1_db.user_a
    res = create_submission(
        name="With client", cedant_name="C", client_id=27, actor_id=a, confirmed=True)
    bare = _mk(iteration1_db, owner=a, name="Bare", inc=date(2026, 7, 1)).submission_id
    sub = get_submission(res.submission_id)
    assert sub.client_id == 27 and sub.client_name == "Travelers Corporate Cat"
    assert sub.client_display == "27 - Travelers Corporate Cat"
    assert [r.id for r in list_submissions(owner_ids=[a], client_ids=[27]).rows] == [
        res.submission_id]
    assert list_submissions(owner_ids=[a], client_ids=["x"]).rows == []
    update_submission(submission_id=res.submission_id, expected_updated_at=_marker(
        res.submission_id), actor_id=a, client_id=None)
    assert get_submission(res.submission_id).client_id is None
    assert get_submission(bare).client_display is None


# ── spec 017 US3: in force, per contract ─────────────────────────────────────

def _in_force(db, as_of):
    return {r.name for r in list_submissions(
        owner_ids=[db.user_a], in_force_as_of=as_of).rows}


def test_in_force_bounds_are_inclusive_and_a_deal_without_a_contract_never_qualifies(
        iteration1_db):
    a = iteration1_db.user_a
    bounded = _mk(iteration1_db, owner=a, name="Bounded", contracts=[]).submission_id
    _add(iteration1_db, bounded, "T-1", inc=date(2026, 1, 1), exp=date(2026, 12, 31),
         status="WON")
    _mk(iteration1_db, owner=a, name="No contract", cedant="Other", contracts=[])
    assert _in_force(iteration1_db, date(2026, 1, 1)) == {"Bounded"}
    assert _in_force(iteration1_db, date(2026, 12, 31)) == {"Bounded"}
    assert _in_force(iteration1_db, date(2025, 12, 31)) == set()
    assert _in_force(iteration1_db, date(2027, 1, 1)) == set()


def test_in_force_is_decided_per_contract(iteration1_db):
    """Note 32 D17: one contract bound and one lost on the same modeling package;
    the deal is in force on the bound one alone, and on its dates alone."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, owner=a, name="Layered", contracts=[]).submission_id
    _add(iteration1_db, sid, "Annual", inc=date(2026, 1, 1), exp=date(2026, 12, 31),
         status="LOST")
    _add(iteration1_db, sid, "Three-year", inc=date(2026, 1, 1), exp=date(2028, 12, 31),
         status="WON")
    assert _in_force(iteration1_db, date(2026, 6, 1)) == {"Layered"}
    assert _in_force(iteration1_db, date(2027, 6, 1)) == {"Layered"}   # the 3-year contract
    assert _in_force(iteration1_db, date(2029, 1, 1)) == set()
    set_contract_status(contract_id=_contract(sid, "Three-year").id, to_status="LOST",
                        expected_updated_at=_contract(sid, "Three-year").updated_at,
                        actor_id=a)
    assert _in_force(iteration1_db, date(2026, 6, 1)) == set()  # the Lost annual never counts


def test_in_force_needs_won(iteration1_db):
    a = iteration1_db.user_a
    for name, status in (("Lost", "LOST"), ("In process", "IN_PROCESS"), ("Won", "WON")):
        sid = _mk(iteration1_db, owner=a, name=name, cedant=name, contracts=[]).submission_id
        _add(iteration1_db, sid, f"T-{status}", inc=date(2026, 1, 1),
             exp=date(2026, 12, 31), status=status)
    assert _in_force(iteration1_db, date(2026, 6, 1)) == {"Won"}


def test_won_is_the_only_literal_the_in_force_rule_carries():
    """Article 3: the in-force rule names Won through one module constant."""
    import inspect

    source = inspect.getsource(svc)
    assert source.count('"WON"') == 1 and "'WON'" not in source
