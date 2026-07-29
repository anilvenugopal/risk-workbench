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
from datetime import date

import pytest

from app.services import submission_service as svc
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
    set_status,
    update_submission,
)
from app.services.errors import (
    ConcurrencyConflict,
    SelfRenewalError,
    SubmissionClosed,
)

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

def test_create_writes_submission_and_initial_active_event(iteration1_db):
    res = _mk(iteration1_db)
    assert res.created is True and res.submission_id
    sub = get_submission(res.submission_id)
    assert sub is not None
    assert sub.status_code == "ACTIVE"
    assert sub.assigned_analyst_id == iteration1_db.user_a
    assert sub.treaty_type_label == "Cat XoL"  # kind join populated
    history = get_status_history(res.submission_id)
    assert len(history) == 1 and history[0].status_code == "ACTIVE"


def test_get_submission_unknown_id_returns_none(iteration1_db):
    assert get_submission("00000000-0000-0000-0000-000000000000") is None


def test_get_submission_has_no_access_restriction(iteration1_db):
    # Owned by B, still fully readable (no row-level security, FR-019).
    sid = _mk(iteration1_db, owner=iteration1_db.user_b).submission_id
    assert get_submission(sid).assigned_analyst_id == iteration1_db.user_b


def test_cedant_suggestions_distinct_prefix(iteration1_db):
    _mk(iteration1_db, name="A", cedant="Acme Mutual", tt="cat_xol",
        inc=date(2026, 1, 1))
    _mk(iteration1_db, name="B", cedant="Acme Mutual", tt="quota_share",
        inc=date(2026, 2, 1))   # same cedant, distinct attrs (no dup warning)
    _mk(iteration1_db, name="C", cedant="Acadia Re", tt="cat_xol",
        inc=date(2026, 3, 1))
    _mk(iteration1_db, name="D", cedant="Beta Insurance", tt="surplus",
        inc=date(2026, 4, 1))
    # cedant_suggestions is a global DISTINCT with no owner scope, so restrict the
    # equality check to the cedants this test created — unrelated "Ac…" cedants in
    # a shared dev DB then can't fail it, while DISTINCT + sort order are still
    # verified (Acme Mutual appears once; Acadia sorts before Acme).
    out = cedant_suggestions("Ac")
    ours = [c for c in out if c in {"Acadia Re", "Acme Mutual"}]
    assert ours == ["Acadia Re", "Acme Mutual"]
    assert "Beta Insurance" not in out  # prefix filter excludes non-matches
    assert cedant_suggestions("") == []


# ── US2: list / filter / reassign ─────────────────────────────────────────────

def test_list_owner_predicate_is_not_an_access_gate(iteration1_db):
    a1 = _mk(iteration1_db, owner=iteration1_db.user_a, name="A1",
             cedant="Acme", inc=date(2026, 1, 1)).submission_id
    b1 = _mk(iteration1_db, owner=iteration1_db.user_b, name="B1",
             cedant="Beta", inc=date(2026, 2, 1)).submission_id
    # Owner filter is scoped to the (throwaway) owner, so exact-match is safe:
    # nothing else in the DB is owned by this freshly-created analyst.
    mine = {r.id for r in list_submissions(owner_id=iteration1_db.user_a)}
    assert mine == {a1}
    # "All" (owner=None) must include BOTH owners' deals — that is the property
    # under test (no row-level scoping). Assert membership, not exact equality,
    # so unrelated deals already present in a shared dev DB don't fail the test.
    all_ids = {r.id for r in list_submissions(owner_id=None)}
    assert {a1, b1} <= all_ids  # All shows every deal regardless of owner


def test_list_filters_combine(iteration1_db):
    a = iteration1_db.user_a
    _mk(iteration1_db, owner=a, name="X", cedant="Acme", tt="cat_xol",
        inc=date(2026, 1, 1), ty=2026)
    _mk(iteration1_db, owner=a, name="Y", cedant="Acme", tt="quota_share",
        inc=date(2026, 6, 1), ty=2026)
    _mk(iteration1_db, owner=a, name="Z", cedant="Beta", tt="cat_xol",
        inc=date(2025, 1, 1), ty=2025)

    # Scope every filter query to this test's throwaway owner so rows already
    # present in a shared dev DB can't skew the counts. owner_id is itself just
    # another AND-predicate, so this still exercises filter combination.
    assert len(list_submissions(owner_id=a, cedant_name="Acme")) == 2
    assert len(list_submissions(owner_id=a, treaty_type_code="cat_xol")) == 2
    assert len(list_submissions(owner_id=a, inception_date=date(2026, 6, 1))) == 1
    assert len(list_submissions(owner_id=a, treaty_year=2025)) == 1
    # combined AND: Acme + cat_xol → only X
    combo = list_submissions(owner_id=a, cedant_name="Acme", treaty_type_code="cat_xol")
    assert len(combo) == 1 and combo[0].name == "X"


def test_reassign_owner_moves_my_view(iteration1_db):
    sid = _mk(iteration1_db, owner=iteration1_db.user_a).submission_id
    reassign_owner(submission_id=sid, new_owner_id=iteration1_db.user_b,
                   expected_updated_at=_marker(sid), actor_id=iteration1_db.user_a)
    assert get_submission(sid).assigned_analyst_id == iteration1_db.user_b
    assert list_submissions(owner_id=iteration1_db.user_a) == []
    assert len(list_submissions(owner_id=iteration1_db.user_b)) == 1
    # Still visible in the global ("everyone") list — assert the deal is present
    # rather than that it is the ONLY row, so a shared dev DB doesn't fail this.
    assert sid in {r.id for r in list_submissions(owner_id=None)}


def test_reassign_owner_stale_marker_conflicts(iteration1_db):
    sid = _mk(iteration1_db).submission_id
    with pytest.raises(ConcurrencyConflict):
        reassign_owner(submission_id=sid, new_owner_id=iteration1_db.user_b,
                       expected_updated_at=STALE, actor_id=iteration1_db.user_a)


# ── US3: status lifecycle (event-sourced) ─────────────────────────────────────

def test_status_transitions_reopen_and_history(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id  # event 1: ACTIVE
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


def test_same_status_is_a_recorded_no_op(iteration1_db):
    sid = _mk(iteration1_db).submission_id
    _bump(); set_status(submission_id=sid, to_status="ACTIVE", reason=None,
                        expected_updated_at=_marker(sid), actor_id=iteration1_db.user_a)
    assert get_submission(sid).status_code == "ACTIVE"
    assert len(get_status_history(sid)) == 2  # ACTIVE (create) + ACTIVE (no-op)


def test_read_only_gate_blocks_mutations_when_closed(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
    set_status(submission_id=sid, to_status="COMPLETED", reason=None,
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


# ── US4: CRM tags (gated; append-only) ────────────────────────────────────────

def test_crm_add_remove_list(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
    assert list_crm_ids(sid) == []  # zero tags is valid
    t1 = add_crm_id(submission_id=sid, crm_id="CRM-1", actor_id=a)
    add_crm_id(submission_id=sid, crm_id="CRM-2", actor_id=a)
    assert {t.crm_id for t in list_crm_ids(sid)} == {"CRM-1", "CRM-2"}
    remove_crm_id(crm_tag_id=t1, actor_id=a)
    assert {t.crm_id for t in list_crm_ids(sid)} == {"CRM-2"}


def test_crm_blank_rejected_duplicates_are_silent_noops(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
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


def test_crm_mutations_gated_when_closed(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
    t1 = add_crm_id(submission_id=sid, crm_id="CRM-1", actor_id=a)
    set_status(submission_id=sid, to_status="COMPLETED", reason=None,
               expected_updated_at=_marker(sid), actor_id=a)
    with pytest.raises(SubmissionClosed):
        add_crm_id(submission_id=sid, crm_id="CRM-2", actor_id=a)
    with pytest.raises(SubmissionClosed):
        remove_crm_id(crm_tag_id=t1, actor_id=a)


# ── US5: non-unique identity / duplicate warning / edit guards ────────────────

def test_find_similar_name_and_attribute_arms(iteration1_db):
    first = _mk(iteration1_db, name="TY2604_Acme", cedant="Acme Mutual",
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


def test_create_duplicate_warns_then_confirms(iteration1_db):
    first = _mk(iteration1_db, name="TY2604_Acme").submission_id
    res = _mk(iteration1_db, name="TY2604_Acme", confirmed=False)  # unconfirmed dup
    assert res.created is False and res.submission_id is None
    assert first in {w.id for w in res.warnings}  # our row flagged as a look-alike
    res2 = _mk(iteration1_db, name="TY2604_Acme", confirmed=True)
    assert res2.created is True and res2.submission_id


def test_update_rename_warns_then_confirms(iteration1_db):
    a = iteration1_db.user_a
    first = _mk(iteration1_db, name="Alpha", cedant="C1", tt="cat_xol",
                inc=date(2026, 1, 1)).submission_id
    second = _mk(iteration1_db, name="Beta", cedant="C2", tt="surplus",
                 inc=date(2026, 2, 1)).submission_id
    # rename second → Alpha collides with first (name arm)
    r = update_submission(submission_id=second, expected_updated_at=_marker(second),
                          actor_id=a, name="Alpha")
    assert r.updated is False and first in {w.id for w in r.warnings}
    r2 = update_submission(submission_id=second, expected_updated_at=_marker(second),
                           actor_id=a, confirmed=True, name="Alpha")
    assert r2.updated is True
    assert get_submission(second).name == "Alpha"


def test_update_self_renewal_rejected(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db).submission_id
    with pytest.raises(SelfRenewalError):
        update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                          actor_id=a, renews_from_submission_id=sid)


def test_update_stale_marker_conflicts(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Unique Deal").submission_id
    with pytest.raises(ConcurrencyConflict):
        update_submission(submission_id=sid, expected_updated_at=STALE,
                          actor_id=a, confirmed=True, directory_path="/staging/x")
