"""app/services/submission_service.py (US1–US5) against SQL Server.

Runs via the ``workbench_db`` fixture (empty test database + two analysts).
Covers the contract
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
    add_crm_id,
    cedant_suggestions,
    create_submission,
    find_similar,
    get_status_history,
    get_submission,
    list_crm_ids,
    list_submissions,
    reassign_owner,
    remove_crm_id,
    search_submissions_for_link,
    set_status,
    update_submission,
)
from app.workers import entity_jobs
from db import execute, execute_command, execute_one, execute_scalar

STALE = "1999-01-01 00:00:00.000000"  # a marker that can never match


def _mk(db, *, owner=None, name="TY2604_AmericanFamily", cedant="American Family",
        tt="cat_xol", inc=date(2026, 4, 1), ty=2026, confirmed=True):
    # confirmed=True by default: test setup must always create its baseline row,
    # even when a look-alike already exists in a shared dev DB (an unconfirmed
    # create would short-circuit with a warning and write nothing). Tests that
    # specifically exercise the duplicate-warning path pass confirmed=False.
    res = create_submission(
        name=name, cedant_name=cedant, treaty_type_code=tt, inception_date=inc,
        treaty_year=ty, actor_id=owner or db.user_a, confirmed=confirmed,
    )
    return res


def _marker(sid):
    return get_submission(sid).updated_at


def _bump():
    """Guarantee a strictly later app-supplied timestamp for the next write, so
    event history ordering (at DESC) is deterministic. Real transitions are
    seconds apart; the unit test compresses them, so nudge the clock."""
    time.sleep(0.01)


# ── US1: create / get / cedant autocomplete ──────────────────────────────────

def test_create_writes_submission_and_initial_active_event(workbench_db):
    res = _mk(workbench_db)
    assert res.created is True and res.submission_id
    sub = get_submission(res.submission_id)
    assert sub is not None
    assert sub.status_code == "ACTIVE"
    assert sub.assigned_analyst_id == workbench_db.user_a
    assert sub.treaty_type_label == "Cat XoL"  # kind join populated
    history = get_status_history(res.submission_id)
    assert len(history) == 1 and history[0].status_code == "ACTIVE"


def test_get_submission_unknown_id_returns_none(workbench_db):
    assert get_submission("00000000-0000-0000-0000-000000000000") is None


def test_get_submission_has_no_access_restriction(workbench_db):
    # Owned by B, still fully readable (no row-level security, FR-019).
    sid = _mk(workbench_db, owner=workbench_db.user_b).submission_id
    assert get_submission(sid).assigned_analyst_id == workbench_db.user_b


def test_submission_entities_use_direct_associations_and_stored_counts(workbench_db):
    first = _mk(workbench_db, name="First").submission_id
    second = _mk(workbench_db, name="Second", cedant="Second Re").submission_id
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


def test_submission_entity_tables_sort_by_name_status_and_count(workbench_db):
    submission_id = _mk(workbench_db, name="Sorted entities").submission_id
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
    workbench_db, fake_irp, drive,
):
    submission_id = _mk(workbench_db, name="Import target").submission_id

    edm = edm_service.import_edm(
        name="Imported_EDM", source_file_path=str(drive / "edm1.bak"),
        actor_id=workbench_db.user_a, submission_id=submission_id)
    rdm = rdm_service.import_rdm(
        name="Imported_RDM", source_file_path=str(drive / "rdm1.mdf"),
        actor_id=workbench_db.user_a, submission_id=submission_id)

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


def test_add_existing_candidates_exclude_related_and_deleted_entities(workbench_db):
    submission_id = _mk(workbench_db, name="Candidate target").submission_id
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


def test_add_existing_candidates_are_paginated(workbench_db):
    submission_id = _mk(workbench_db, name="Candidate pages").submission_id
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


def test_attach_existing_keeps_valid_selections_when_others_are_stale(workbench_db):
    submission_id = _mk(workbench_db, name="Attach target").submission_id
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
        actor_id=workbench_db.user_a)

    assert result.attached_ids == [valid]
    assert result.stale_ids == ["invalid", already_related, missing]
    assert execute_scalar(
        "SELECT COUNT(*) FROM submission_rdm WHERE submission_id=:s",
        {"s": submission_id}, connection="WORKBENCH") == 2


def test_detach_removes_only_the_selected_submission_association(workbench_db):
    first = _mk(workbench_db, name="Detach first").submission_id
    second = _mk(workbench_db, name="Detach second", cedant="Second Re").submission_id
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


def test_closed_submission_rejects_association_writes(workbench_db, drive):
    submission_id = _mk(workbench_db, name="Closed associations").submission_id
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, 'ClosedEDM', 'ready')",
        {"id": edm_id}, connection="WORKBENCH")
    set_status(
        submission_id=submission_id, to_status="COMPLETED", reason=None,
        expected_updated_at=_marker(submission_id), actor_id=workbench_db.user_a)

    with pytest.raises(SubmissionClosed):
        svc.attach_edms(
            submission_id=submission_id, edm_ids=[edm_id],
            actor_id=workbench_db.user_a)
    with pytest.raises(SubmissionClosed):
        svc.detach_edm(submission_id=submission_id, edm_id=edm_id)
    with pytest.raises(SubmissionClosed):
        edm_service.import_edm(
            name="ClosedImport", source_file_path=str(drive / "edm1.bak"),
            actor_id=workbench_db.user_a, submission_id=submission_id)


def test_cedant_suggestions_distinct_and_sorted(workbench_db):
    _mk(workbench_db, name="A", cedant="Acme Mutual", tt="cat_xol",
        inc=date(2026, 1, 1))
    _mk(workbench_db, name="B", cedant="Acme Mutual", tt="quota_share",
        inc=date(2026, 2, 1))   # same cedant, distinct attrs (no dup warning)
    _mk(workbench_db, name="C", cedant="Acadia Re", tt="cat_xol",
        inc=date(2026, 3, 1))
    _mk(workbench_db, name="D", cedant="Beta Insurance", tt="surplus",
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


def test_cedant_suggestions_match_anywhere_in_the_name(workbench_db):
    # CR7: prefix matching never found "American Family Mutual" from "fam".
    _mk(workbench_db, name="AF", cedant="American Family Mutual", tt="cat_xol",
        inc=date(2026, 5, 1))
    assert "American Family Mutual" in cedant_suggestions("fam")


def test_cedant_suggestions_treat_wildcards_literally(workbench_db):
    _mk(workbench_db, name="Pct", cedant="50% Quota Co", tt="surplus",
        inc=date(2026, 6, 1))
    _mk(workbench_db, name="Plain", cedant="Zeta Re", tt="surplus",
        inc=date(2026, 7, 1))
    out = cedant_suggestions("0%")
    assert "50% Quota Co" in out and "Zeta Re" not in out


def test_suggestions_ignore_a_one_character_term(workbench_db):
    # A one-character LIKE '%a%' scans every submission for a menu the analyst
    # cannot read; both searches wait for the second character.
    _mk(workbench_db, name="Solo", cedant="Solo Re", tt="surplus",
        inc=date(2026, 8, 1))
    assert cedant_suggestions("S") == []
    assert cedant_suggestions("  s  ") == []
    assert search_submissions_for_link("S") == []
    assert "Solo Re" in cedant_suggestions("So")


def test_suggestions_cap_the_row_count_in_the_query(workbench_db):
    for index in range(6):
        _mk(workbench_db, name=f"Capped {index}", cedant=f"Capped Re {index}",
            tt="surplus", inc=date(2026, 9, 1))
    assert len(cedant_suggestions("Capped Re", limit=3)) == 3
    assert len(search_submissions_for_link("Capped", limit=2)) == 2


# ── US2: list / filter / reassign ─────────────────────────────────────────────

def test_list_owner_predicate_is_not_an_access_gate(workbench_db):
    a1 = _mk(workbench_db, owner=workbench_db.user_a, name="A1",
             cedant="Acme", inc=date(2026, 1, 1)).submission_id
    b1 = _mk(workbench_db, owner=workbench_db.user_b, name="B1",
             cedant="Beta", inc=date(2026, 2, 1)).submission_id
    # Owner filter is scoped to the (throwaway) owner, so exact-match is safe:
    # nothing else in the DB is owned by this freshly-created analyst.
    mine = {r.id for r in list_submissions(owner_ids=[workbench_db.user_a]).rows}
    assert mine == {a1}
    # "All" (owner_ids=[]) must include BOTH owners' deals — that is the property
    # under test (no row-level scoping). Assert membership, not exact equality,
    # so unrelated deals already present in a shared dev DB don't fail the test.
    all_ids = {r.id for r in list_submissions(owner_ids=[]).rows}
    assert {a1, b1} <= all_ids  # All shows every deal regardless of owner


def test_list_filters_combine(workbench_db):
    a = workbench_db.user_a
    _mk(workbench_db, owner=a, name="X", cedant="Acme", tt="cat_xol",
        inc=date(2026, 1, 1), ty=2026)
    _mk(workbench_db, owner=a, name="Y", cedant="Acme", tt="quota_share",
        inc=date(2026, 6, 1), ty=2026)
    _mk(workbench_db, owner=a, name="Z", cedant="Beta", tt="cat_xol",
        inc=date(2025, 1, 1), ty=2025)

    # Scope every filter query to this test's throwaway owner so rows already
    # present in a shared dev DB can't skew the counts. owner_ids is itself just
    # another AND-predicate, so this still exercises filter combination.
    assert len(list_submissions(owner_ids=[a], cedant_name="Acme").rows) == 2
    assert len(list_submissions(owner_ids=[a], treaty_type_codes=["cat_xol"]).rows) == 2
    assert len(list_submissions(owner_ids=[a], inception_date=date(2026, 6, 1)).rows) == 1
    assert len(list_submissions(owner_ids=[a], treaty_years=[2025]).rows) == 1
    # combined AND: Acme + cat_xol → only X
    combo = list_submissions(
        owner_ids=[a], cedant_name="Acme", treaty_type_codes=["cat_xol"]).rows
    assert len(combo) == 1 and combo[0].name == "X"


def test_list_search_by_name_ands_every_word(workbench_db):
    """CR1/CR2 on the master list. Same rule as the "links to" picker, but the list's
    search box is name-only — cedant has its own field."""
    a = workbench_db.user_a
    amfam = _mk(workbench_db, owner=a, name="American Family Renewal",
                cedant="American Family Mutual", inc=date(2026, 5, 1)).submission_id
    ammod = _mk(workbench_db, owner=a, name="American Modern Renewal",
                cedant="American Modern", inc=date(2026, 6, 1)).submission_id
    assert {r.id for r in list_submissions(
        owner_ids=[a], name="american family").rows} == {amfam}
    assert {r.id for r in list_submissions(
        owner_ids=[a], name="american").rows} == {amfam, ammod}
    # "mutual" is in a cedant and in no name, so the search box does not match it.
    assert list_submissions(owner_ids=[a], name="mutual").rows == []


def test_list_cedant_filter_matches_part_of_the_name(workbench_db):
    """The cedant box is free text, so it has to match the way an analyst types it —
    a fragment, in whatever case. Exact equality returned nothing for "fam"."""
    a = workbench_db.user_a
    sid = _mk(workbench_db, owner=a, name="Cedant partial",
              cedant="American Family Mutual").submission_id
    assert {r.id for r in list_submissions(owner_ids=[a], cedant_name="fam").rows} == {sid}
    assert {r.id for r in list_submissions(
        owner_ids=[a], cedant_name="american mutual").rows} == {sid}


def test_list_filter_by_owner_id(workbench_db):
    """The Owner filter matches the assigned analyst's id, so two analysts sharing a
    display name stay apart."""
    a, b = workbench_db.user_a, workbench_db.user_b
    tag = uuid.uuid4().hex[:8]
    mine = _mk(workbench_db, owner=a, name=f"Owned {tag} A").submission_id
    theirs = _mk(workbench_db, owner=b, name=f"Owned {tag} B").submission_id
    execute_command(
        "UPDATE app_user SET display_name = 'Chris Doyle' WHERE id IN (:a, :b)",
        {"a": str(a), "b": str(b)}, connection="WORKBENCH")

    assert [r.id for r in list_submissions(
        name=f"Owned {tag}", owner_ids=[b]).rows] == [theirs]
    assert {r.id for r in list_submissions(
        name=f"Owned {tag}").rows} == {mine, theirs}


def test_list_owner_id_that_is_not_a_uuid_matches_nothing(workbench_db):
    """A hand-typed ?owner=… reaches the uniqueidentifier comparison, which SQL
    Server refuses. It has to read as "no match", not as an error."""
    _mk(workbench_db, owner=workbench_db.user_a, name="Some deal")
    assert list_submissions(owner_ids=["not-a-uuid"]).rows == []


def test_list_filter_by_crm_id(workbench_db):
    a = workbench_db.user_a
    tagged = _mk(workbench_db, owner=a, name="Tagged deal").submission_id
    _mk(workbench_db, owner=a, name="Untagged deal", inc=date(2026, 7, 1))
    add_crm_id(submission_id=tagged, crm_id="CRM-4417", actor_id=a)
    add_crm_id(submission_id=tagged, crm_id="CRM-4418", actor_id=a)
    # A substring of either tag finds the deal, and finds it ONCE even though both
    # tags match — the predicate is EXISTS, not a join.
    assert [r.id for r in list_submissions(owner_ids=[a], crm_id="441").rows] == [tagged]
    assert [r.id for r in list_submissions(owner_ids=[a], crm_id="4418").rows] == [tagged]
    assert list_submissions(owner_ids=[a], crm_id="9999").rows == []


def test_list_rows_carry_their_crm_ids(workbench_db):
    a = workbench_db.user_a
    tagged = _mk(workbench_db, owner=a, name="Has tags").submission_id
    untagged = _mk(workbench_db, owner=a, name="No tags",
                   inc=date(2026, 7, 1)).submission_id
    add_crm_id(submission_id=tagged, crm_id="CRM-1", actor_id=a)
    _bump()  # distinct inserted_at, so "oldest tag first" is deterministic here
    add_crm_id(submission_id=tagged, crm_id="CRM-2", actor_id=a)
    rows = {r.id: r for r in list_submissions(owner_ids=[a]).rows}
    assert rows[tagged].crm_ids == ["CRM-1", "CRM-2"]
    assert rows[untagged].crm_ids == []


def test_list_filter_by_status(workbench_db):
    a = workbench_db.user_a
    active = _mk(workbench_db, owner=a, name="Still active").submission_id
    done = _mk(workbench_db, owner=a, name="Wrapped up",
               inc=date(2026, 7, 1)).submission_id
    set_status(submission_id=done, to_status="COMPLETED", reason="delivered",
               expected_updated_at=_marker(done), actor_id=a)
    assert [r.id for r in list_submissions(
        owner_ids=[a], status_codes=["COMPLETED"]).rows] == [done]
    assert [r.id for r in list_submissions(
        owner_ids=[a], status_codes=["ACTIVE"]).rows] == [active]


def test_list_search_treats_a_wildcard_as_a_literal(workbench_db):
    a = workbench_db.user_a
    literal = _mk(workbench_db, owner=a, name="100% quota share").submission_id
    _mk(workbench_db, owner=a, name="100 quota share", inc=date(2026, 7, 1))
    assert [r.id for r in list_submissions(owner_ids=[a], name="100%").rows] == [literal]


def test_list_returns_one_page_at_a_time(workbench_db):
    """Every read is capped at PAGE_SIZE, so ``_attach_crm_ids`` can never bind more
    ids than SQL Server accepts in one statement (2,100 bound parameters).

    Distinct cedants keep each create's look-alike check empty, and one shared
    inception date leaves the name as the only sort key, so the two pages are in a
    known order."""
    a = workbench_db.user_a
    tag = uuid.uuid4().hex[:8]
    for i in range(svc.PAGE_SIZE + 2):
        _mk(workbench_db, owner=a, name=f"{tag} deal {i:03d}",
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


def test_list_page_below_one_reads_the_first_page(workbench_db):
    """A hand-typed ?page=0 must not reach the query as a negative offset."""
    a = workbench_db.user_a
    sid = _mk(workbench_db, owner=a, name="Only deal").submission_id
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
            ("Cat 2025", "cat_xol", 2025), ("Cat 2026", "cat_xol", 2026),
            ("Quota 2025", "quota_share", 2025), ("Surplus 2027", "surplus", 2027)):
        made[name] = _mk(db, owner=a, name=name, cedant=name, tt=treaty_type,
                         inc=date(year, 4, 1), ty=year).submission_id
    return a, made


def test_list_ors_within_a_multi_value_filter(workbench_db):
    a, _ = _four_mixed_deals(workbench_db)
    assert {r.name for r in list_submissions(
        owner_ids=[a], treaty_type_codes=["cat_xol", "quota_share"]).rows} == {
        "Cat 2025", "Cat 2026", "Quota 2025"}
    assert {r.name for r in list_submissions(
        owner_ids=[a], treaty_years=[2025, 2027]).rows} == {
        "Cat 2025", "Quota 2025", "Surplus 2027"}


def test_list_ands_one_multi_value_filter_against_another(workbench_db):
    a, _ = _four_mixed_deals(workbench_db)
    assert {r.name for r in list_submissions(
        owner_ids=[a], treaty_type_codes=["cat_xol", "quota_share"],
        treaty_years=[2025, 2027]).rows} == {"Cat 2025", "Quota 2025"}


def test_list_treats_an_empty_list_as_no_filter(workbench_db):
    a, _ = _four_mixed_deals(workbench_db)
    assert len(list_submissions(
        owner_ids=[a], treaty_type_codes=[], treaty_years=[],
        status_codes=[]).rows) == 4


def test_list_filters_on_several_statuses(workbench_db):
    a, made = _four_mixed_deals(workbench_db)
    for name in ("Cat 2025", "Quota 2025"):
        set_status(submission_id=made[name], to_status="COMPLETED", reason=None,
                   expected_updated_at=_marker(made[name]), actor_id=a)
    set_status(submission_id=made["Cat 2026"], to_status="CANCELLED", reason=None,
               expected_updated_at=_marker(made["Cat 2026"]), actor_id=a)
    assert {r.name for r in list_submissions(
        owner_ids=[a], status_codes=["COMPLETED", "CANCELLED"]).rows} == {
        "Cat 2025", "Quota 2025", "Cat 2026"}


def test_list_filters_on_several_owners(workbench_db):
    a, b = workbench_db.user_a, workbench_db.user_b
    mine = _mk(workbench_db, owner=a, name="Mine", cedant="Mine Re").submission_id
    theirs = _mk(workbench_db, owner=b, name="Theirs",
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
        workbench_db, sort, descending, expected):
    a = _sorted_deals(workbench_db)
    assert [r.name for r in list_submissions(
        owner_ids=[a], sort=sort, descending=descending).rows] == expected


@pytest.mark.parametrize("sort", ["status", "s.name; DROP TABLE submission", "", None])
def test_list_sort_outside_the_whitelist_is_rejected(workbench_db, sort):
    """The key is looked up in SORT_COLUMNS, so nothing from the query string reaches
    the ORDER BY."""
    a = _sorted_deals(workbench_db)
    with pytest.raises(KeyError):
        list_submissions(owner_ids=[a], sort=sort)


def test_list_defaults_to_newest_inception_first(workbench_db):
    a = _sorted_deals(workbench_db)
    assert [r.name for r in list_submissions(owner_ids=[a]).rows] == [
        "Bravo", "Charlie", "Alpha"]


def test_list_breaks_a_sort_tie_the_same_way_on_every_page(workbench_db):
    """Every deal here shares a treaty year, so the sorted column decides nothing and
    the tiebreaker decides the whole order. Without it the two pages could repeat a
    deal and skip another."""
    a = workbench_db.user_a
    for i in range(svc.PAGE_SIZE + 2):
        _mk(workbench_db, owner=a, name=f"deal {i:03d}", cedant=f"cedant {i:03d}",
            inc=date(2026, 4, 1), ty=2026)
    first = list_submissions(owner_ids=[a], sort="year", descending=True)
    second = list_submissions(owner_ids=[a], sort="year", descending=True, page=2)
    names = [r.name for r in first.rows] + [r.name for r in second.rows]
    assert names == sorted(names)
    assert len(set(names)) == svc.PAGE_SIZE + 2


def test_status_kinds_lists_every_status_in_display_order(workbench_db):
    assert svc.status_kinds() == [
        ("ACTIVE", "Active"), ("COMPLETED", "Completed"), ("CANCELLED", "Cancelled")]


def test_reassign_owner_moves_my_view(workbench_db):
    sid = _mk(workbench_db, owner=workbench_db.user_a).submission_id
    reassign_owner(submission_id=sid, new_owner_id=workbench_db.user_b,
                   expected_updated_at=_marker(sid), actor_id=workbench_db.user_a)
    assert get_submission(sid).assigned_analyst_id == workbench_db.user_b
    assert list_submissions(owner_ids=[workbench_db.user_a]).rows == []
    assert len(list_submissions(owner_ids=[workbench_db.user_b]).rows) == 1
    # Still visible in the global ("everyone") list — assert the deal is present
    # rather than that it is the ONLY row, so a shared dev DB doesn't fail this.
    assert sid in {r.id for r in list_submissions(owner_ids=[]).rows}


def test_reassign_owner_stale_marker_conflicts(workbench_db):
    sid = _mk(workbench_db).submission_id
    with pytest.raises(ConcurrencyConflict):
        reassign_owner(submission_id=sid, new_owner_id=workbench_db.user_b,
                       expected_updated_at=STALE, actor_id=workbench_db.user_a)


# ── US3: status lifecycle (event-sourced) ─────────────────────────────────────

def test_status_transitions_reopen_and_history(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db).submission_id  # event 1: ACTIVE
    _bump(); set_status(submission_id=sid, to_status="COMPLETED", reason="done",
                        expected_updated_at=_marker(sid), actor_id=a)
    assert get_submission(sid).status_code == "COMPLETED"
    _bump(); set_status(submission_id=sid, to_status="ACTIVE", reason=None,
                        expected_updated_at=_marker(sid), actor_id=a)  # reopen COMPLETED→ACTIVE
    _bump(); set_status(submission_id=sid, to_status="CANCELLED", reason="pulled",
                        expected_updated_at=_marker(sid), actor_id=a)
    _bump(); set_status(submission_id=sid, to_status="ACTIVE", reason=None,
                        expected_updated_at=_marker(sid), actor_id=a)  # reopen CANCELLED→ACTIVE
    assert get_submission(sid).status_code == "ACTIVE"

    history = get_status_history(sid)
    assert [e.status_code for e in history] == [
        "ACTIVE", "CANCELLED", "ACTIVE", "COMPLETED", "ACTIVE",  # newest first
    ]


def test_same_status_is_a_recorded_no_op(workbench_db):
    sid = _mk(workbench_db).submission_id
    _bump(); set_status(submission_id=sid, to_status="ACTIVE", reason=None,
                        expected_updated_at=_marker(sid), actor_id=workbench_db.user_a)
    assert get_submission(sid).status_code == "ACTIVE"
    assert len(get_status_history(sid)) == 2  # ACTIVE (create) + ACTIVE (no-op)


def test_read_only_gate_blocks_mutations_when_closed(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db).submission_id
    set_status(submission_id=sid, to_status="COMPLETED", reason=None,
               expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        reassign_owner(submission_id=sid, new_owner_id=workbench_db.user_b,
                       expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, directory_path="/x")


def test_no_delete_function_exists(workbench_db):
    # SC-005 / FR-014: there is no delete path for submissions.
    assert not hasattr(svc, "delete_submission")
    assert [n for n in dir(svc) if "delete" in n.lower() and not n.startswith("_")] == []


# ── US4: CRM tags (gated; append-only) ────────────────────────────────────────

def test_crm_add_remove_list(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db).submission_id
    assert list_crm_ids(sid) == []  # zero tags is valid
    t1 = add_crm_id(submission_id=sid, crm_id="CRM-1", actor_id=a)
    add_crm_id(submission_id=sid, crm_id="CRM-2", actor_id=a)
    assert {t.crm_id for t in list_crm_ids(sid)} == {"CRM-1", "CRM-2"}
    remove_crm_id(crm_tag_id=t1, actor_id=a)
    assert {t.crm_id for t in list_crm_ids(sid)} == {"CRM-2"}


def test_crm_blank_rejected_duplicates_are_silent_noops(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db).submission_id
    with pytest.raises(ValueError):
        add_crm_id(submission_id=sid, crm_id="   ", actor_id=a)
    assert list_crm_ids(sid) == []  # not stored
    # re-adding an existing tag is a no-op: no second row, no error, and the
    # id of the tag already on the deal comes back. Match is case-insensitive
    # and ignores surrounding whitespace.
    first = add_crm_id(submission_id=sid, crm_id="DUP", actor_id=a)
    assert add_crm_id(submission_id=sid, crm_id="DUP", actor_id=a) == first
    assert add_crm_id(submission_id=sid, crm_id="  dup ", actor_id=a) == first
    assert [t.crm_id for t in list_crm_ids(sid)] == ["DUP"]


def test_create_stores_crm_ids_dropping_blanks_and_repeats(workbench_db):
    res = create_submission(
        name="TY2604_CrmAtCreate", cedant_name="Acme Mutual",
        treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
        crm_ids=["CRM-1", " ", "CRM-2", " crm-1 "],
        actor_id=workbench_db.user_a, confirmed=True,
    )
    assert {t.crm_id for t in list_crm_ids(res.submission_id)} == {"CRM-1", "CRM-2"}


def test_crm_mutations_gated_when_closed(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db).submission_id
    t1 = add_crm_id(submission_id=sid, crm_id="CRM-1", actor_id=a)
    set_status(submission_id=sid, to_status="COMPLETED", reason=None,
               expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        add_crm_id(submission_id=sid, crm_id="CRM-2", actor_id=a)
    with pytest.raises(SubmissionClosed):
        remove_crm_id(crm_tag_id=t1, actor_id=a)


# ── US5: non-unique identity / duplicate warning / edit guards ────────────────

def test_find_similar_name_and_attribute_arms(workbench_db):
    first = _mk(workbench_db, name="TY2604_Acme", cedant="Acme Mutual",
                tt="cat_xol", inc=date(2026, 4, 1)).submission_id
    # find_similar is a global dedup lookup with no owner scope; assert our
    # planted row's presence/absence rather than exact result sets, so unrelated
    # look-alikes already in a shared dev DB don't fail the test.
    # name-match arm (different cedant/type/inception)
    by_name = find_similar(name="TY2604_Acme", cedant_name="Zzz",
                           treaty_type_code="stop_loss", inception_date=date(2030, 1, 1))
    assert first in {r.id for r in by_name}
    # attribute-match arm (different name)
    by_attr = find_similar(name="Totally Different", cedant_name="Acme Mutual",
                           treaty_type_code="cat_xol", inception_date=date(2026, 4, 1))
    assert first in {r.id for r in by_attr}
    # genuinely new deal → our row is not a look-alike
    assert first not in {r.id for r in find_similar(
        name="Brand New", cedant_name="Nobody Re",
        treaty_type_code="surplus", inception_date=date(2031, 1, 1))}
    # exclude_id skips the row being renamed
    assert first not in {r.id for r in find_similar(
        name="TY2604_Acme", cedant_name="Acme Mutual",
        treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
        exclude_id=first)}


def test_create_duplicate_warns_then_confirms(workbench_db):
    first = _mk(workbench_db, name="TY2604_Acme").submission_id
    res = _mk(workbench_db, name="TY2604_Acme", confirmed=False)  # unconfirmed dup
    assert res.created is False and res.submission_id is None
    assert first in {w.id for w in res.warnings}  # our row flagged as a look-alike
    res2 = _mk(workbench_db, name="TY2604_Acme", confirmed=True)
    assert res2.created is True and res2.submission_id


def test_update_rename_warns_then_confirms(workbench_db):
    a = workbench_db.user_a
    first = _mk(workbench_db, name="Alpha", cedant="C1", tt="cat_xol",
                inc=date(2026, 1, 1)).submission_id
    second = _mk(workbench_db, name="Beta", cedant="C2", tt="surplus",
                 inc=date(2026, 2, 1)).submission_id
    # rename second → Alpha collides with first (name arm)
    r = update_submission(submission_id=second, expected_updated_at=_marker(second),
                          actor_id=a, name="Alpha")
    assert r.updated is False and first in {w.id for w in r.warnings}
    r2 = update_submission(submission_id=second, expected_updated_at=_marker(second),
                           actor_id=a, confirmed=True, name="Alpha")
    assert r2.updated is True
    assert get_submission(second).name == "Alpha"


def test_update_self_link_rejected(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db).submission_id
    with pytest.raises(SelfLinkError):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, links_to_submission_id=sid)


@pytest.mark.parametrize("link_value", [str(uuid.uuid4()), "not-a-uuid"])
def test_create_with_an_unknown_link_target_is_rejected(workbench_db, link_value):
    # links_to_submission_id is a foreign key to submission.id, so an id naming no
    # deal has to be refused before the INSERT turns it into a driver error.
    with pytest.raises(UnknownLinkError):
        create_submission(
            name="Stale link", cedant_name="American Family",
            treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
            links_to_submission_id=link_value, actor_id=workbench_db.user_a,
            confirmed=True)
    # Scoped to this test's throwaway owner, so the assertion is "the deal was not
    # written" rather than "a page of the list is the same length".
    assert list_submissions(
        owner_ids=[workbench_db.user_a], name="Stale link").rows == []


@pytest.mark.parametrize("link_value", [str(uuid.uuid4()), "not-a-uuid"])
def test_update_to_an_unknown_link_target_is_rejected(workbench_db, link_value):
    a = workbench_db.user_a
    sid = _mk(workbench_db, name="Keeps its link").submission_id
    with pytest.raises(UnknownLinkError):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, links_to_submission_id=link_value)
    assert get_submission(sid).links_to_submission_id is None


def test_link_target_is_kept_across_an_edit_that_never_mentions_it(workbench_db):
    # The merged value is re-checked on every update, so an untouched link must
    # still pass — the check reads the target, it does not require it to be resent.
    a = workbench_db.user_a
    target = _mk(workbench_db, name="Last year", inc=date(2025, 4, 1)).submission_id
    sid = _mk(workbench_db, name="This year").submission_id
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, links_to_submission_id=target)
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, name="This year, renamed")
    assert get_submission(sid).links_to_submission_id == target


def test_treaty_year_defaults_to_the_inception_year(workbench_db):
    sid = _mk(workbench_db, name="No year given", inc=date(2026, 4, 1),
              ty=None).submission_id
    assert get_submission(sid).treaty_year == 2026


def test_entered_treaty_year_survives_create_and_update(workbench_db):
    # A December inception is often written into the following treaty year, so an
    # entered value must never be replaced by the derived one (CR5).
    a = workbench_db.user_a
    sid = _mk(workbench_db, name="Dec incept", inc=date(2026, 12, 15),
              ty=2027).submission_id
    assert get_submission(sid).treaty_year == 2027
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, treaty_year=2027,
                      inception_date=date(2026, 12, 20))
    assert get_submission(sid).treaty_year == 2027


def test_clearing_treaty_year_on_update_refills_it_from_the_inception_date(
        workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db, name="Cleared year", inc=date(2026, 4, 1),
              ty=2030).submission_id
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, treaty_year=None)
    assert get_submission(sid).treaty_year == 2026


# ── "Links to" picker search (CR8) ───────────────────────────────────────────

def test_search_for_link_ands_every_term(workbench_db):
    # CR2: "There must be 1000 companies that have American in the name."
    amfam = _mk(workbench_db, name="TY2506_AmericanFamily",
                cedant="American Family Mutual", inc=date(2025, 6, 1)).submission_id
    amnat = _mk(workbench_db, name="TY2501_AmericanNational",
                cedant="American National", inc=date(2025, 1, 1)).submission_id
    found = {row.id for row in search_submissions_for_link("american fam")}
    assert amfam in found and amnat not in found
    both = {row.id for row in search_submissions_for_link("american")}
    assert {amfam, amnat} <= both


def test_search_for_link_matches_every_word_however_many(workbench_db):
    sid = _mk(workbench_db, name="American Family Renewal",
              cedant="American Family Mutual", inc=date(2026, 4, 1)).submission_id
    matching = ["american", "family", "renewal", "mutual", "am", "fam", "ren"]
    assert sid in {r.id for r in search_submissions_for_link(" ".join(matching))}
    # The word past the ones that match still narrows the search — a term is never
    # searched on a prefix of its words, which would return deals the analyst's
    # last word rules out.
    with_one_miss = " ".join(matching + ["nomatch"])
    assert search_submissions_for_link(with_one_miss) == []


def test_search_for_link_matches_name_or_cedant(workbench_db):
    sid = _mk(workbench_db, name="Opaque code 9912",
              cedant="Zenith Mutual", inc=date(2026, 2, 1)).submission_id
    assert sid in {r.id for r in search_submissions_for_link("9912")}
    assert sid in {r.id for r in search_submissions_for_link("zenith")}


def test_search_for_link_excludes_the_submission_being_edited(workbench_db):
    sid = _mk(workbench_db, name="Sole Match Deal",
              cedant="Solo Re", inc=date(2026, 3, 1)).submission_id
    assert sid in {r.id for r in search_submissions_for_link("Sole Match")}
    assert search_submissions_for_link("Sole Match", exclude_id=sid) == []


def test_search_for_link_empty_term_returns_nothing(workbench_db):
    _mk(workbench_db, name="Anything", inc=date(2026, 8, 1))
    assert search_submissions_for_link("") == []
    assert search_submissions_for_link("   ") == []


def test_update_stale_marker_conflicts(workbench_db):
    a = workbench_db.user_a
    sid = _mk(workbench_db, name="Unique Deal").submission_id
    with pytest.raises(ConcurrencyConflict):
        update_submission(submission_id=sid, expected_updated_at=STALE,
                          actor_id=a, confirmed=True, directory_path="/staging/x")
