"""SQL-Server-specific submission-service behaviors.

The service suite (``test_submission_service.py``, same tier) reads the
optimistic-concurrency marker via ``get_submission().updated_at`` — a native
``datetime``. The web flow instead renders that value into a hidden field as
``str(...)`` and submits it back as a **string**, so the string→DATETIME2
*match* (not just the always-mismatching stale-string arm) is exercised by the
dedicated test here, together with the ``uniqueidentifier`` conversion and FK
behaviors behind link validation and the suggest caps.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services import submission_service as svc
from app.services.errors import UnknownLinkError


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
    predicate and the ``uniqueidentifier`` comparison behind it: run each search
    against the real driver and check the cap holds."""
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
    ``uniqueidentifier``. An unchecked id hits one of two driver errors: an
    integrity error for a well-formed id naming no row, and a conversion error
    for text that is not a UUID. Both must be ``UnknownLinkError`` before the
    write."""
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
