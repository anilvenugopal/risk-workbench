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
    search_submissions_for_link,
    set_status,
    update_submission,
)
from app.services.errors import (
    ConcurrencyConflict,
    SelfLinkError,
    SubmissionClosed,
    UnknownLinkError,
)
from db import execute_scalar

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


def test_cedant_suggestions_distinct_and_sorted(iteration1_db):
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
    assert "Beta Insurance" not in out
    assert cedant_suggestions("") == []


def test_cedant_suggestions_match_anywhere_in_the_name(iteration1_db):
    # CR7: prefix matching never found "American Family Mutual" from "fam".
    _mk(iteration1_db, name="AF", cedant="American Family Mutual", tt="cat_xol",
        inc=date(2026, 5, 1))
    assert "American Family Mutual" in cedant_suggestions("fam")


def test_cedant_suggestions_treat_wildcards_literally(iteration1_db):
    _mk(iteration1_db, name="Pct", cedant="50% Quota Co", tt="surplus",
        inc=date(2026, 6, 1))
    _mk(iteration1_db, name="Plain", cedant="Zeta Re", tt="surplus",
        inc=date(2026, 7, 1))
    out = cedant_suggestions("0%")
    assert "50% Quota Co" in out and "Zeta Re" not in out


def test_suggestions_ignore_a_one_character_term(iteration1_db):
    # A one-character LIKE '%a%' scans every submission for a menu the analyst
    # cannot read; both searches wait for the second character.
    _mk(iteration1_db, name="Solo", cedant="Solo Re", tt="surplus",
        inc=date(2026, 8, 1))
    assert cedant_suggestions("S") == []
    assert cedant_suggestions("  s  ") == []
    assert search_submissions_for_link("S") == []
    assert "Solo Re" in cedant_suggestions("So")


def test_suggestions_cap_the_row_count_in_the_query(iteration1_db):
    for index in range(6):
        _mk(iteration1_db, name=f"Capped {index}", cedant=f"Capped Re {index}",
            tt="surplus", inc=date(2026, 9, 1))
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
    mine = {r.id for r in list_submissions(owner_id=iteration1_db.user_a).rows}
    assert mine == {a1}
    # "All" (owner=None) must include BOTH owners' deals — that is the property
    # under test (no row-level scoping). Assert membership, not exact equality,
    # so unrelated deals already present in a shared dev DB don't fail the test.
    all_ids = {r.id for r in list_submissions(owner_id=None).rows}
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
    assert len(list_submissions(owner_id=a, cedant_name="Acme").rows) == 2
    assert len(list_submissions(owner_id=a, treaty_type_code="cat_xol").rows) == 2
    assert len(list_submissions(owner_id=a, inception_date=date(2026, 6, 1)).rows) == 1
    assert len(list_submissions(owner_id=a, treaty_year=2025).rows) == 1
    # combined AND: Acme + cat_xol → only X
    combo = list_submissions(
        owner_id=a, cedant_name="Acme", treaty_type_code="cat_xol").rows
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
        owner_id=a, name="american family").rows} == {amfam}
    assert {r.id for r in list_submissions(
        owner_id=a, name="american").rows} == {amfam, ammod}
    # "mutual" is in a cedant and in no name, so the search box does not match it.
    assert list_submissions(owner_id=a, name="mutual").rows == []


def test_list_cedant_filter_matches_part_of_the_name(iteration1_db):
    """The cedant box is free text, so it has to match the way an analyst types it —
    a fragment, in whatever case. Exact equality returned nothing for "fam"."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, owner=a, name="Cedant partial",
              cedant="American Family Mutual").submission_id
    assert {r.id for r in list_submissions(owner_id=a, cedant_name="fam").rows} == {sid}
    assert {r.id for r in list_submissions(
        owner_id=a, cedant_name="american mutual").rows} == {sid}


def test_list_filter_by_owner_name(iteration1_db):
    """The Owner filter matches the assigned analyst's display name, word by word,
    so both a pick from the list and a typed fragment narrow the same way. The two
    tiers name their throwaway analysts differently, so read the name back."""
    a, b = iteration1_db.user_a, iteration1_db.user_b
    tag = uuid.uuid4().hex[:8]
    mine = _mk(iteration1_db, owner=a, name=f"Owned {tag} A").submission_id
    theirs = _mk(iteration1_db, owner=b, name=f"Owned {tag} B").submission_id
    name_b = execute_scalar(
        "SELECT display_name FROM app_user WHERE id = :id", {"id": str(b)},
        connection="WORKBENCH")

    last_word = name_b.split()[-1].lower()
    assert [r.id for r in list_submissions(
        name=f"Owned {tag}", owner_name=name_b).rows] == [theirs]
    assert [r.id for r in list_submissions(
        name=f"Owned {tag}", owner_name=last_word).rows] == [theirs]
    assert {r.id for r in list_submissions(
        name=f"Owned {tag}").rows} == {mine, theirs}


def test_list_filter_by_crm_id(iteration1_db):
    a = iteration1_db.user_a
    tagged = _mk(iteration1_db, owner=a, name="Tagged deal").submission_id
    _mk(iteration1_db, owner=a, name="Untagged deal", inc=date(2026, 7, 1))
    add_crm_id(submission_id=tagged, crm_id="CRM-4417", actor_id=a)
    add_crm_id(submission_id=tagged, crm_id="CRM-4418", actor_id=a)
    # A substring of either tag finds the deal, and finds it ONCE even though both
    # tags match — the predicate is EXISTS, not a join.
    assert [r.id for r in list_submissions(owner_id=a, crm_id="441").rows] == [tagged]
    assert [r.id for r in list_submissions(owner_id=a, crm_id="4418").rows] == [tagged]
    assert list_submissions(owner_id=a, crm_id="9999").rows == []


def test_list_rows_carry_their_crm_ids(iteration1_db):
    a = iteration1_db.user_a
    tagged = _mk(iteration1_db, owner=a, name="Has tags").submission_id
    untagged = _mk(iteration1_db, owner=a, name="No tags",
                   inc=date(2026, 7, 1)).submission_id
    add_crm_id(submission_id=tagged, crm_id="CRM-1", actor_id=a)
    _bump()  # distinct inserted_at, so "oldest tag first" is deterministic here
    add_crm_id(submission_id=tagged, crm_id="CRM-2", actor_id=a)
    rows = {r.id: r for r in list_submissions(owner_id=a).rows}
    assert rows[tagged].crm_ids == ["CRM-1", "CRM-2"]
    assert rows[untagged].crm_ids == []


def test_list_filter_by_status(iteration1_db):
    a = iteration1_db.user_a
    active = _mk(iteration1_db, owner=a, name="Still active").submission_id
    done = _mk(iteration1_db, owner=a, name="Wrapped up",
               inc=date(2026, 7, 1)).submission_id
    set_status(submission_id=done, to_status="COMPLETED", reason="delivered",
               expected_updated_at=_marker(done), actor_id=a)
    assert [r.id for r in list_submissions(
        owner_id=a, status_code="COMPLETED").rows] == [done]
    assert [r.id for r in list_submissions(
        owner_id=a, status_code="ACTIVE").rows] == [active]


def test_list_search_treats_a_wildcard_as_a_literal(iteration1_db):
    a = iteration1_db.user_a
    literal = _mk(iteration1_db, owner=a, name="100% quota share").submission_id
    _mk(iteration1_db, owner=a, name="100 quota share", inc=date(2026, 7, 1))
    assert [r.id for r in list_submissions(owner_id=a, name="100%").rows] == [literal]


def test_list_returns_one_page_at_a_time(iteration1_db):
    """Every read is capped at PAGE_SIZE, so ``_attach_crm_ids`` can never bind more
    ids than SQL Server accepts in one statement (2,100 bound parameters).

    Distinct cedants keep each create's look-alike check empty, and one shared
    inception date leaves the name as the only sort key, so the two pages are in a
    known order."""
    a = iteration1_db.user_a
    tag = uuid.uuid4().hex[:8]
    for i in range(svc.PAGE_SIZE + 2):
        _mk(iteration1_db, owner=a, name=f"{tag} deal {i:03d}",
            cedant=f"{tag} cedant {i:03d}", inc=date(2026, 4, 1))

    first = list_submissions(owner_id=a)
    assert len(first.rows) == svc.PAGE_SIZE
    assert first.page == 1 and first.has_next is True

    second = list_submissions(owner_id=a, page=2)
    assert [r.name for r in second.rows] == [
        f"{tag} deal {svc.PAGE_SIZE:03d}", f"{tag} deal {svc.PAGE_SIZE + 1:03d}"]
    assert second.page == 2 and second.has_next is False

    past_the_end = list_submissions(owner_id=a, page=3)
    assert past_the_end.rows == [] and past_the_end.has_next is False


def test_list_page_below_one_reads_the_first_page(iteration1_db):
    """A hand-typed ?page=0 must not reach the query as a negative offset."""
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, owner=a, name="Only deal").submission_id
    for page in (0, -5):
        result = list_submissions(owner_id=a, page=page)
        assert result.page == 1 and [r.id for r in result.rows] == [sid]


def test_status_kinds_lists_every_status_in_display_order(iteration1_db):
    assert svc.status_kinds() == [
        ("ACTIVE", "Active"), ("COMPLETED", "Completed"), ("CANCELLED", "Cancelled")]


def test_reassign_owner_moves_my_view(iteration1_db):
    sid = _mk(iteration1_db, owner=iteration1_db.user_a).submission_id
    reassign_owner(submission_id=sid, new_owner_id=iteration1_db.user_b,
                   expected_updated_at=_marker(sid), actor_id=iteration1_db.user_a)
    assert get_submission(sid).assigned_analyst_id == iteration1_db.user_b
    assert list_submissions(owner_id=iteration1_db.user_a).rows == []
    assert len(list_submissions(owner_id=iteration1_db.user_b).rows) == 1
    # Still visible in the global ("everyone") list — assert the deal is present
    # rather than that it is the ONLY row, so a shared dev DB doesn't fail this.
    assert sid in {r.id for r in list_submissions(owner_id=None).rows}


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


def test_create_stores_crm_ids_dropping_blanks_and_repeats(iteration1_db):
    res = create_submission(
        name="TY2604_CrmAtCreate", cedant_name="Acme Mutual",
        treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
        crm_ids=["CRM-1", " ", "CRM-2", " crm-1 "],
        actor_id=iteration1_db.user_a, confirmed=True,
    )
    assert {t.crm_id for t in list_crm_ids(res.submission_id)} == {"CRM-1", "CRM-2"}


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
            treaty_type_code="cat_xol", inception_date=date(2026, 4, 1),
            links_to_submission_id=link_value, actor_id=iteration1_db.user_a,
            confirmed=True)
    # Scoped to this test's throwaway owner, so the assertion is "the deal was not
    # written" rather than "a page of the list is the same length".
    assert list_submissions(
        owner_id=iteration1_db.user_a, name="Stale link").rows == []


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


def test_treaty_year_defaults_to_the_inception_year(iteration1_db):
    sid = _mk(iteration1_db, name="No year given", inc=date(2026, 4, 1),
              ty=None).submission_id
    assert get_submission(sid).treaty_year == 2026


def test_entered_treaty_year_survives_create_and_update(iteration1_db):
    # A December inception is often written into the following treaty year, so an
    # entered value must never be replaced by the derived one (CR5).
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Dec incept", inc=date(2026, 12, 15),
              ty=2027).submission_id
    assert get_submission(sid).treaty_year == 2027
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, treaty_year=2027,
                      inception_date=date(2026, 12, 20))
    assert get_submission(sid).treaty_year == 2027


def test_clearing_treaty_year_on_update_refills_it_from_the_inception_date(
        iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Cleared year", inc=date(2026, 4, 1),
              ty=2030).submission_id
    update_submission(submission_id=sid, expected_updated_at=_marker(sid),
                      actor_id=a, confirmed=True, treaty_year=None)
    assert get_submission(sid).treaty_year == 2026


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


def test_update_stale_marker_conflicts(iteration1_db):
    a = iteration1_db.user_a
    sid = _mk(iteration1_db, name="Unique Deal").submission_id
    with pytest.raises(ConcurrencyConflict):
        update_submission(submission_id=sid, expected_updated_at=STALE,
                          actor_id=a, confirmed=True, directory_path="/staging/x")
