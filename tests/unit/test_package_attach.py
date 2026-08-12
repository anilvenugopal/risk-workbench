"""Attaching an already-imported EDM/RDM to a package (issue #22).

A standalone import (``POST /edms/import`` / ``/rdms/import``) lands with
``package_id = NULL`` and used to be a permanent orphan — unreachable from any
submission, since a submission reaches its entities only through a package. These
tests cover the candidate read, the attach itself, and the two chain-level
consequences that make an attach actually *work*:

  • ``save_and_sync`` applies the package's RDMs to a newly attached ``ready`` EDM
    without re-importing it, and the fan-out stays idempotent per (EDM, RDM) pair —
    the proof that attach needs no new sync logic (Article 5: the Risk Modeler
    consequence waits for the analyst's explicit Save & Sync click).
  • ``_upload_edm_body`` reads membership **live** instead of trusting the head's
    ``input_data`` snapshot, so a member detached while its import head is still
    pending does not have the package's RDMs applied to it anyway.

Attach takes ``ready`` entities only; detach is wider (a ``pending_import`` or
``error`` member must be able to leave) — see ``test_package_service`` for the
predicates themselves.

Attach and detach never touch Risk Modeler — that is the whole contract, and
``fake_irp.submits`` is asserted empty where it matters.
"""

from __future__ import annotations

import uuid

import pytest

from app.poller import run as poller
from app.services import (
    analysis_service, edm_service, package_service, rdm_service)
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute, execute_command, execute_one, execute_scalar

MS = sync.MemberSpec


def _package(drive, actor, *, name="P", edms=(), rdms=()):
    """save_package a package from shared-drive files; return its id."""
    members = [MS(kind="edm", name=n, source_file_path=str(drive / f))
               for n, f in edms]
    members += [MS(kind="rdm", name=n, source_file_path=str(drive / f))
                for n, f in rdms]
    return sync.save_package(package_id=None, name=name, members=members,
                             actor_id=actor).package_id


def _orphan(kind, name, *, status="ready", inserted_at=None):
    """A standalone entity row with no owning package — what the picker offers.
    Inserted directly so the test controls ``status`` without driving a worker.
    ``ready`` by default, because since issue #22 that is the only status the picker
    offers and the only one ``add_member`` accepts.
    ``inserted_at`` is explicit where a test asserts on order: the reads sort
    ``inserted_at DESC, name``, so leaving it NULL silently sorts by name instead."""
    table = "irp_edm" if kind == "edm" else "irp_rdm"
    mid = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, name, status, inserted_at) "
        "VALUES (:id, :n, :s, :at)",
        {"id": mid, "n": name, "s": status, "at": inserted_at},
        connection="WORKBENCH")
    return mid


def _member_ids(package_id, table):
    return {str(r["id"]) for r in execute(
        f"SELECT id FROM {table} WHERE package_id = :p AND deleted_at IS NULL",
        {"p": package_id}, connection="WORKBENCH")}


# ── the candidate read ────────────────────────────────────────────────────────────

def test_list_unattached_members_excludes_attached_and_soft_deleted(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    _package(drive, a, edms=[("InPkgE", "edm1.bak")], rdms=[("InPkgR", "rdm1.mdf")])
    free_edm = _orphan("edm", "FreeE")
    free_rdm = _orphan("rdm", "FreeR")
    soft_deleted = _orphan("edm", "SoftDeletedE")
    execute_command("UPDATE irp_edm SET deleted_at = :t WHERE id = :id",
                    {"t": "2026-01-01 00:00:00", "id": soft_deleted},
                    connection="WORKBENCH")

    page = sync.list_unattached_members()

    assert [(c.kind, c.id) for c in page.rows] == [
        ("edm", free_edm), ("rdm", free_rdm)]   # EDMs first, then RDMs
    assert page.rows[0].name == "FreeE"
    assert page.rows[0].status == "ready"
    assert (page.total, page.page, page.pages) == (2, 1, 1)
    assert fake_irp.submits == []               # a read, not a Risk Modeler touch


@pytest.mark.parametrize(
    "status", ["pending_import", "importing", "error", "delete_pending", "deleted", None])
def test_list_unattached_members_offers_only_ready_entities(
        iteration2_db, fake_irp, drive, status):
    """The picker offers ``ready`` and nothing else. An entity mid-import has no name
    in Risk Modeler yet, so a later Save & Sync would apply this package's RDMs against
    something that does not exist there — the ordering hazard is removed by never
    offering the candidate rather than by handling it downstream.

    A failed import is recovered by the library's own Retry, which lands it ``ready``
    and therefore attachable; it is deliberately not recoverable *by* attaching."""
    ready = _orphan("edm", "ReadyE")
    _orphan("edm", "NotReadyE", status=status)
    _orphan("rdm", "NotReadyR", status=status)

    page = sync.list_unattached_members()

    assert [c.id for c in page.rows] == [ready]
    assert page.total == 1


def test_list_unattached_members_filters_by_name(iteration2_db, fake_irp, drive):
    _orphan("edm", "Acme")
    match = _orphan("rdm", "AcmeResults")
    page = sync.list_unattached_members(name="Results")
    assert [c.id for c in page.rows] == [match]
    assert page.total == 1          # the count reflects the filter, not the library


# ── pagination (issue #22: the library will hold hundreds) ────────────────────────

def test_candidate_pages_slice_across_both_kinds(iteration2_db, fake_irp, drive):
    """EDMs then RDMs, newest-first within each — one continuous sequence that a page
    boundary may fall inside. Page 2 of a 5-item set with size 2 straddles the
    EDM→RDM seam, which is exactly where an off-by-one would hide."""
    edms = [_orphan("edm", f"E{i}", inserted_at=f"2026-01-0{i + 1} 00:00:00")
            for i in range(3)]
    rdms = [_orphan("rdm", f"R{i}", inserted_at=f"2026-01-0{i + 1} 00:00:00")
            for i in range(2)]
    expected = list(reversed(edms)) + list(reversed(rdms))   # newest-first per kind

    seen = []
    for n in (1, 2, 3):
        page = sync.list_unattached_members(page=n, page_size=2)
        assert (page.total, page.pages, page.page) == (5, 3, n)
        seen.extend(c.id for c in page.rows)
    assert seen == expected                      # every row once, in order, no gaps

    first = sync.list_unattached_members(page=1, page_size=2)
    assert (first.first, first.last) == (1, 2)
    last = sync.list_unattached_members(page=3, page_size=2)
    assert (last.first, last.last) == (5, 5)     # short final page


def test_candidate_page_clamps_out_of_range_and_empty(iteration2_db, fake_irp, drive):
    """A stale ``?page=`` must not 404 or render blank: the candidate set shrinks under
    the analyst whenever anyone else attaches something, so page 99 clamps to the last
    real page. An empty set is page 1 of 1 with 0–0 bounds, not a division by zero."""
    kept = _orphan("edm", "OnlyOne")
    page = sync.list_unattached_members(page=99, page_size=20)
    assert ([c.id for c in page.rows], page.page, page.pages) == ([kept], 1, 1)

    execute_command("UPDATE irp_edm SET deleted_at = :t WHERE id = :id",
                    {"t": "2026-01-01 00:00:00", "id": kept}, connection="WORKBENCH")
    empty = sync.list_unattached_members(page=3)
    assert (empty.rows, empty.total, empty.page, empty.pages) == ([], 0, 1, 1)
    assert (empty.first, empty.last) == (0, 0)


# ── the tray: picks stay known when they leave the page or the filter ─────────────

def test_resolve_picks_labels_ids_off_the_current_page_and_filter(
        iteration2_db, fake_irp, drive):
    """The tray's whole job (issue #22 review): a pick must stay nameable when a search
    or a page turn removes its row from the list. ``resolve_picks`` is asked about ids
    directly, so neither the filter nor the page can hide them."""
    acme = _orphan("edm", "ACME_RE_2026")
    results = _orphan("rdm", "ACME_RESULTS")
    _orphan("edm", "ZENITH")

    # a search that matches NEITHER pick still leaves both fully labelled
    assert [c.name for c in sync.list_unattached_members(name="ZENITH").rows] == ["ZENITH"]
    tray = sync.resolve_picks(edm_ids=[acme], rdm_ids=[results])
    assert {(c.kind, c.name) for c in tray} == {
        ("edm", "ACME_RE_2026"), ("rdm", "ACME_RESULTS")}


def test_resolve_picks_drops_vanished_ids_and_tolerates_empty(
        iteration2_db, fake_irp, drive):
    """An id that no longer resolves is dropped, not rendered as a chip the analyst
    cannot act on — the attach itself is the authority and reports skips by name."""
    live = _orphan("edm", "Live")
    gone = _orphan("edm", "Gone")
    execute_command("UPDATE irp_edm SET deleted_at = :t WHERE id = :id",
                    {"t": "2026-01-01 00:00:00", "id": gone}, connection="WORKBENCH")

    assert [c.id for c in sync.resolve_picks(
        edm_ids=[live, gone, str(uuid.uuid4())])] == [live]
    assert sync.resolve_picks() == []            # no ids → no queries, no error


def test_resolve_picks_labels_attached_members_too(iteration2_db, fake_irp, drive):
    """Deliberate: the tray labels by id without an ``IS NULL`` filter, so a pick that
    someone else attached in the meantime still gets a chip and a name. It is the
    attach that refuses it — and it can then name it in the skip banner."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("Taken", "edm1.bak")])
    taken = next(iter(_member_ids(pid, "irp_edm")))
    assert [c.name for c in sync.resolve_picks(edm_ids=[taken])] == ["Taken"]


# ── attach is pure bookkeeping ────────────────────────────────────────────────────

def test_attach_touches_nothing_in_risk_modeler(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    orphan = _orphan("rdm", "FreeR", status="ready")
    package_service.add_member(package_id=pid, member_id=orphan,
                               member_kind="rdm", actor_id=a)
    assert _member_ids(pid, "irp_rdm") == {orphan}
    assert fake_irp.submits == []
    assert execute_scalar("SELECT COUNT(*) FROM irp_job", {},
                          connection="WORKBENCH") == 0
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job", {},
                          connection="WORKBENCH") == 0
    # the entity keeps the status it already had — attach re-imports nothing
    assert execute_scalar("SELECT status FROM irp_rdm WHERE id = :id",
                          {"id": orphan}, connection="WORKBENCH") == "ready"


def test_detach_leaves_the_entity_live_in_the_library(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    orphan = _orphan("rdm", "FreeR", status="ready")
    package_service.add_member(package_id=pid, member_id=orphan,
                               member_kind="rdm", actor_id=a)
    package_service.remove_member(package_id=pid, member_id=orphan,
                                  member_kind="rdm", actor_id=a)
    row = execute_one("SELECT package_id, status, deleted_at FROM irp_rdm "
                      "WHERE id = :id", {"id": orphan}, connection="WORKBENCH")
    assert row["package_id"] is None      # back in the standalone library
    assert row["status"] == "ready"       # still ready in Risk Modeler
    assert row["deleted_at"] is None
    assert fake_irp.submits == []
    assert [c.id for c in sync.list_unattached_members().rows] == [orphan]


# ── the attach batch is partial, never all-or-nothing ─────────────────────────────

def test_attach_batch_keeps_good_picks_and_names_the_skips(
        iteration2_db, fake_irp, drive):
    """The reason the batch is not one transaction: one stale pick must not discard the
    good ones and make the analyst re-pick from scratch. ``attached`` counts what landed,
    ``skipped`` names what did not — by name, because it goes straight into a banner."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    other = _package(drive, a, name="Other", edms=[("Owned", "edm2.bak")])
    good_edm = _orphan("edm", "GoodE", status="ready")
    good_rdm = _orphan("rdm", "GoodR")
    taken = next(iter(_member_ids(other, "irp_edm")))   # already in another package
    leaving = _orphan("rdm", "LeavingR", status="delete_pending")
    # a candidate that was ready when the picker rendered and has since been superseded
    # by a re-import — the write predicate is the authority, not the rendered list
    in_flight = _orphan("edm", "InFlightE", status="importing")

    result = sync.attach_existing_members(
        package_id=pid, actor_id=a, picks=[
            sync.ExistingMember(kind="edm", id=good_edm),
            sync.ExistingMember(kind="edm", id=taken),
            sync.ExistingMember(kind="edm", id=in_flight),
            sync.ExistingMember(kind="rdm", id=good_rdm),
            sync.ExistingMember(kind="rdm", id=leaving),
        ])

    assert result.attached == 2
    assert sorted(result.skipped) == ["InFlightE", "LeavingR", "Owned"]  # named, not id'd
    assert _member_ids(pid, "irp_edm") >= {good_edm}
    assert _member_ids(pid, "irp_rdm") == {good_rdm}
    assert _member_ids(other, "irp_edm") == {taken}          # untouched by the skip
    assert fake_irp.submits == []                            # bookkeeping only


def test_attach_batch_is_idempotent_on_a_double_submit(iteration2_db, fake_irp, drive):
    """A double-submitted picker must not report a phantom failure for a member that is
    in fact attached — re-attaching what the package already owns counts as attached."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    orphan = _orphan("rdm", "FreeR")
    picks = [sync.ExistingMember(kind="rdm", id=orphan)]

    assert sync.attach_existing_members(
        package_id=pid, actor_id=a, picks=picks) == sync.AttachResult(1, [])
    assert sync.attach_existing_members(
        package_id=pid, actor_id=a, picks=picks) == sync.AttachResult(1, [])
    assert _member_ids(pid, "irp_rdm") == {orphan}


def test_attach_batch_runs_no_name_collision_check(iteration2_db, fake_irp, drive):
    """An attached entity is already in Risk Modeler under a name RM itself accepted, so
    a collision check would report it colliding with itself. Attach must not run one —
    proven by a candidate whose name the fake gateway reports as taken."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    orphan = _orphan("edm", "AlreadyInRM", status="ready")
    fake_irp.add_edm_name("AlreadyInRM")

    result = sync.attach_existing_members(
        package_id=pid, actor_id=a,
        picks=[sync.ExistingMember(kind="edm", id=orphan)])

    assert result == sync.AttachResult(1, [])
    assert orphan in _member_ids(pid, "irp_edm")


def test_attach_batch_with_no_picks_is_a_no_op(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    assert sync.attach_existing_members(
        package_id=pid, actor_id=a, picks=[]) == sync.AttachResult(0, [])


# ── the sync consequence: no new sync logic needed ────────────────────────────────

def test_attaching_a_ready_rdm_submits_nothing_to_risk_modeler(
        iteration2_db, fake_irp, drive):
    """The Article 5 payoff, now the whole story: an attached RDM is already
    imported in Risk Modeler, and it no longer has to be applied to the package's
    EDMs, so a Save & Sync after the attach submits nothing at all. The
    association it gains is package membership, which the EDM page reads."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])   # EDM-only
    sync.save_and_sync(package_id=pid, actor_id=a)
    package_jobs.run_pending()                            # submit the EDM import
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='import_edm'",
                       {}, connection="WORKBENCH"):
        fake_irp.finish(str(row["irp_id"]))
    poller.poll_once()                                    # EDM → ready
    assert execute_scalar("SELECT status FROM irp_edm WHERE package_id = :p",
                          {"p": pid}, connection="WORKBENCH") == "ready"

    orphan = _orphan("rdm", "FreeR")                      # already ready in RM
    package_service.add_member(package_id=pid, member_id=orphan,
                               member_kind="rdm", actor_id=a)
    sync.save_and_sync(package_id=pid, actor_id=a)
    package_jobs.run_pending()

    # neither member is resubmitted: both are ready
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job "
                          "WHERE rwb_job_type='upload_rdm'", {},
                          connection="WORKBENCH") == 0
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job "
                          "WHERE rwb_job_type='upload_edm'", {},
                          connection="WORKBENCH") == 1    # from the first sync
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm'", {},
        connection="WORKBENCH") == 0

    # the attach is what associates them: the EDM page now lists the RDM
    edm_id = next(iter(_member_ids(pid, "irp_edm")))
    assert [g.rdm_name for g in
            analysis_service.list_edm_analyses(edm_id=edm_id)] == ["FreeR"]


def test_upload_edm_uses_live_membership_over_the_head_snapshot(
        iteration2_db, fake_irp, drive):
    """``_upload_edm_body`` reads ``edm.package_id`` at run time instead of trusting the
    head's ``input_data`` snapshot, which ``ensure_pending_rwb_job`` does not rewrite on
    a still-pending head.

    Detach is the reachable way to desynchronise the two, now that attach takes only
    ``ready`` entities: a package member is detached while its ``upload_edm`` head is
    still pending, so the head still says ``package_id: P`` while the EDM says NULL. If
    the worker trusted the snapshot the ``import_edm`` job would carry P, and every
    package-scoped read — the card's job counts, the delete finalize — would count a
    job for an EDM that is no longer one of its members."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    sync.save_and_sync(package_id=pid, actor_id=a)        # enqueues the upload_edm head
    edm_id = next(iter(_member_ids(pid, "irp_edm")))
    assert f'"package_id": "{pid}"' in execute_scalar(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": edm_id}, connection="WORKBENCH")

    # the analyst removes it before the worker gets to the head
    package_service.remove_member(package_id=pid, member_id=edm_id,
                                  member_kind="edm", actor_id=a)

    package_jobs.run_pending()                            # the ORIGINAL pending head
    job = execute_one("SELECT package_id, irp_id FROM irp_job "
                      "WHERE irp_job_type='import_edm'", {}, connection="WORKBENCH")
    assert job["package_id"] is None                      # live membership won


def test_finalize_package_soft_deletes_an_attached_member_too(
        iteration2_db, fake_irp, drive):
    """A package owns its members (§5a): finalize's blanket
    ``WHERE package_id = :p`` soft-deletes every one of them, attached ones
    included. Recorded as intended behaviour, not a defect — a later reader must
    not mistake the widened membership for a bug and start scoping the cascade."""
    a = iteration2_db.user_a
    pid = _package(drive, a, edms=[("E1", "edm1.bak")])
    orphan = _orphan("rdm", "FreeR", status="ready")
    package_service.add_member(package_id=pid, member_id=orphan,
                               member_kind="rdm", actor_id=a)
    # a live member still in Risk Modeler holds finalize off (FR-021)
    assert sync.finalize_package(package_id=pid) is False

    execute_command("UPDATE irp_edm SET status = 'deleted' WHERE package_id = :p",
                    {"p": pid}, connection="WORKBENCH")
    execute_command("UPDATE irp_rdm SET status = 'deleted' WHERE package_id = :p",
                    {"p": pid}, connection="WORKBENCH")
    assert sync.finalize_package(package_id=pid) is True
    assert execute_scalar("SELECT deleted_at FROM irp_rdm WHERE id = :id",
                          {"id": orphan}, connection="WORKBENCH") is not None
