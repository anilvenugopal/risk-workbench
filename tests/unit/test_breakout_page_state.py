"""Unit tests for the breakout page read model (spec 005 — FR-012/FR-013).

``page_state`` is what makes failure reporting durable-state-derived: it reads
the EDM's live and terminal ``run_breakout_*`` jobs and derives the in-flight
counters, the per-portfolio error lines that survive refresh and navigation, and
the completion banner. Nothing here is stored — every field is recomputed from
the job rows on each read (Article 2).

The queries are bounded by the job table's own uniqueness: one row per
(portfolio, dimension), revived on re-run, so a portfolio's error lines are
superseded by its next terminal run rather than accumulating.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.services.breakout_service import page_state
from db import execute_command
from tests.unit.breakout_rows import (
    mk_breakout_job,
    mk_edm,
    mk_generated_portfolio,
    mk_portfolio,
)

NOW = datetime(2026, 8, 5, 12, 0, 0)


_next_irp_id = iter(range(1000, 9999))


def _mk_edm(*, name: str = "night_edm") -> str:
    return mk_edm(name=name, now=NOW)


def _mk_portfolio(edm_id: str, *, name: str = "usfl_commercial") -> str:
    # irp_id is UNIQUE per EDM (uq_irp_portfolio_edm_irp), so every portfolio
    # this module makes takes the next one.
    return mk_portfolio(edm_id, name=name, irp_id=str(next(_next_irp_id)),
                        detail=None, as_of=None, now=NOW)


def _mk_generated(edm_id: str, source_id: str, *, dimension: str,
                  value: str) -> None:
    mk_generated_portfolio(edm_id, source_id, dimension=dimension, value=value,
                           irp_id=str(next(_next_irp_id)), now=NOW)


def _mk_job(portfolio_id: str, *, dimension: str = "lob",
            status: str = "succeeded", plan: list[str] = (),
            output: dict | str | None = None, error: str | None = None,
            updated: datetime = NOW) -> str:
    return mk_breakout_job(
        portfolio_id, dimension=dimension, status=status,
        input_data={"plan": [{"value": v, "name": f"p - {v}"} for v in plan]},
        output=output, error=error, now=NOW, updated=updated)


def _mk_follow_up(breakout_job_id: str, *, status: str) -> None:
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, attempt_count, inserted_at, updated_at) VALUES "
        "(:i, 'rwb_job', :r, 'backfill_edm_detail', :s, 1, :now, :now)",
        {"i": str(uuid.uuid4()), "r": breakout_job_id, "s": status,
         "now": NOW}, connection="WORKBENCH")


def _outcomes(*entries: dict) -> dict:
    return {"planned": len(entries), "created": 0, "adopted": 0,
            "skipped_existing": 0,
            "failed": sum(1 for e in entries if e["outcome"] == "failed"),
            "sub_portfolios": list(entries)}


def _ok(value: str) -> dict:
    return {"value": value, "name": f"p - {value}", "outcome": "created"}


def _bad(value: str, error: str) -> dict:
    return {"value": value, "name": f"p - {value}", "outcome": "failed",
            "error": error}


# ── in-flight (FR-012 progress) ──────────────────────────────────────────────────

def test_a_live_job_reports_progress_and_keeps_the_poll_alive(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, status="running", plan=["A", "B", "C"])
    # the worker upserts per entry, so two of three values already have rows
    _mk_generated(edm_id, source_id, dimension="lob", value="A")
    _mk_generated(edm_id, source_id, dimension="lob", value="B")
    # a row for another dimension does not count towards this run
    _mk_generated(edm_id, source_id, dimension="state", value="TX")

    state = page_state(edm_id)

    assert state.running is True
    flight = state.flights[source_id]
    assert (flight.dimension, flight.noun) == ("lob", "line of business")
    assert (flight.planned, flight.done) == (3, 2)


def test_no_breakout_jobs_reports_nothing(iteration2_db):
    edm_id = _mk_edm()
    _mk_portfolio(edm_id)

    state = page_state(edm_id)

    assert (state.running, state.banner, state.flights, state.errors) == (
        False, None, {}, {})


def test_a_terminal_job_stops_the_poll(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, status="succeeded", output=_outcomes(_ok("A")))

    state = page_state(edm_id)

    assert state.running is False
    assert state.flights == {}


# ── durable error lines (FR-012) ─────────────────────────────────────────────────

def test_failed_entries_become_error_lines_on_the_source_row(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, output=_outcomes(
        _ok("A"), _bad("MT", "selection returned zero accounts")))

    lines = page_state(edm_id).errors[source_id]

    assert len(lines) == 1
    assert (lines[0].dimension, lines[0].noun) == ("lob", "line of business")
    assert (lines[0].value, lines[0].name) == ("MT", "p - MT")
    assert lines[0].error == "selection returned zero accounts"


def test_a_job_that_failed_before_its_loop_reports_the_job_error(
        iteration2_db):
    # No per-entry outcomes exist — the selection read failed, or the plan was
    # unusable — so the line carries the job's own error with no value/name.
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, status="failed", output=None,
            error="account selection failed: DataBridge timeout")

    lines = page_state(edm_id).errors[source_id]

    assert (lines[0].value, lines[0].name) == ("", "")
    assert lines[0].error == "account selection failed: DataBridge timeout"


def test_a_failed_job_with_entry_outcomes_reports_the_entries_not_the_job(
        iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, status="failed",
            output=_outcomes(_bad("A", "RM 500"), _bad("B", "RM 503")),
            error="no sub-portfolio succeeded")

    lines = page_state(edm_id).errors[source_id]

    assert [line.error for line in lines] == ["RM 500", "RM 503"]


def test_a_successful_run_leaves_no_error_line(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, output=_outcomes(_ok("A"), _ok("B")))

    assert page_state(edm_id).errors == {}


def test_both_dimensions_of_one_portfolio_accumulate_into_one_list(
        iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, dimension="lob", output=_outcomes(_bad("A", "lob boom")))
    _mk_job(source_id, dimension="state",
            output=_outcomes(_bad("TX", "state boom")))

    lines = page_state(edm_id).errors[source_id]

    assert {(line.dimension, line.error) for line in lines} == {
        ("lob", "lob boom"), ("state", "state boom")}


def test_error_lines_are_keyed_per_portfolio(iteration2_db):
    edm_id = _mk_edm()
    first = _mk_portfolio(edm_id, name="book_one")
    second = _mk_portfolio(edm_id, name="book_two")
    _mk_job(first, output=_outcomes(_bad("A", "first boom")))
    _mk_job(second, output=_outcomes(_bad("B", "second boom")))

    errors = page_state(edm_id).errors

    assert [line.error for line in errors[first]] == ["first boom"]
    assert [line.error for line in errors[second]] == ["second boom"]


def test_another_edms_breakout_is_not_read(iteration2_db):
    edm_id = _mk_edm()
    other_edm = _mk_edm(name="other_edm")
    _mk_job(_mk_portfolio(other_edm), output=_outcomes(_bad("A", "not mine")))
    _mk_portfolio(edm_id)

    state = page_state(edm_id)

    assert (state.errors, state.banner) == ({}, None)


def test_unparseable_output_data_degrades_to_no_lines(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, status="succeeded", output="{not json")

    state = page_state(edm_id)

    assert state.errors == {}
    assert state.banner is None


# ── the completion banner (FR-013) ───────────────────────────────────────────────

def test_the_banner_shows_figures_filling_in_while_the_follow_up_runs(
        iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    jid = _mk_job(source_id, output=dict(_outcomes(_ok("A"), _ok("B")),
                                        created=2))
    _mk_follow_up(jid, status="running")

    banner = page_state(edm_id).banner

    assert banner is not None
    assert (banner.source_name, banner.noun) == ("usfl_commercial",
                                                 "line of business")
    assert (banner.created, banner.failed) == (2, 0)
    assert (banner.ok, banner.filling_in, banner.error) == (True, True, None)


def test_a_settled_successful_run_shows_no_banner(iteration2_db):
    # The figures have landed and nothing failed — there is nothing left to say.
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    jid = _mk_job(source_id, output=dict(_outcomes(_ok("A")), created=1))
    _mk_follow_up(jid, status="succeeded")

    assert page_state(edm_id).banner is None


def test_a_partial_failure_banner_survives_the_follow_up(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    jid = _mk_job(source_id, output=dict(
        _outcomes(_ok("A"), _bad("B", "RM 500")), created=1))
    _mk_follow_up(jid, status="succeeded")

    banner = page_state(edm_id).banner

    assert banner is not None
    assert (banner.created, banner.failed, banner.ok) == (1, 1, False)
    assert banner.filling_in is False


def test_a_job_level_failure_banner_carries_the_job_error(iteration2_db):
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, status="failed", output=None,
            error="EDM missing or has no exposureId — nothing created")

    banner = page_state(edm_id).banner

    assert banner is not None
    assert banner.ok is False
    assert banner.error == "EDM missing or has no exposureId — nothing created"


def test_the_banner_is_the_newest_terminal_run_of_the_edm(iteration2_db):
    edm_id = _mk_edm()
    older = _mk_portfolio(edm_id, name="older_book")
    newer = _mk_portfolio(edm_id, name="newer_book")
    _mk_job(older, output=_outcomes(_bad("A", "older boom")),
            updated=NOW - timedelta(minutes=5))
    _mk_job(newer, output=_outcomes(_bad("B", "newer boom")), updated=NOW)

    state = page_state(edm_id)

    assert state.banner is not None
    assert state.banner.source_name == "newer_book"
    # both portfolios keep their own durable lines regardless of which one the
    # banner names
    assert set(state.errors) == {older, newer}


def test_the_skipped_existing_count_reaches_the_banner(iteration2_db):
    # An idempotent re-run: nothing new created, one entry still failing.
    edm_id = _mk_edm()
    source_id = _mk_portfolio(edm_id)
    _mk_job(source_id, output=dict(
        _outcomes(_ok("A"), _bad("B", "RM 500")), created=0,
        skipped_existing=1, adopted=0))

    banner = page_state(edm_id).banner

    assert banner is not None
    assert (banner.created, banner.adopted, banner.skipped_existing,
            banner.failed) == (0, 0, 1, 1)
