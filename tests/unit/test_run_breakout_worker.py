"""Unit tests for the ``run_breakout_*`` worker (spec 005 T034 — FR-008/FR-010/
FR-011/FR-012/FR-013, T-10).

The worker EXECUTES the plan persisted at confirm — it never re-enumerates
values, re-reads the summary, or recomputes names (AGENTS.md rule 8 / R10).
Account ids are resolved once, before the loop; per-entry try/except isolates
failures; a zero-account selection creates nothing; adoption resolves on the
generated ``portfolioNumber`` with exactly one hit; partial success is success
with outcomes; completion idempotently enqueues ``backfill_edm_detail``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.services import breakout_service, portfolio_service
from app.services.irp_gateway import PortfolioHit
from app.workers import portfolio_jobs
from db import execute, execute_command, execute_one

ACTOR = None  # set per test from iteration2_db.user_a


def _mk_edm(name: str = "EDM", irp_id: int | None = 90001) -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, irp_id, status, inserted_at, updated_at) "
        "VALUES (:i, :n, :irp, 'ready', :now, :now)",
        {"i": edm_id, "n": name, "irp": irp_id, "now": datetime.utcnow()},
        connection="WORKBENCH")
    return edm_id


def _mk_source(edm_id: str, *, name: str = "usfl_commercial",
               irp_id: str | None = "1") -> str:
    pid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, inserted_at, "
        "updated_at) VALUES (:i, :e, :n, :irp, :now, :now)",
        {"i": pid, "e": edm_id, "n": name, "irp": irp_id,
         "now": datetime.utcnow()}, connection="WORKBENCH")
    return pid


def _plan_entry(value: str, *, name: str | None = None,
                number: str | None = None, accounts: int = 10,
                label: str | None = None) -> dict:
    return {"value": value, "label": label,
            "name": name or f"usfl_commercial - {value}",
            "number": number or f"P1-L-{value.upper().replace(' ', '')}",
            "accounts": accounts}


def _mk_job(edm_id: str, portfolio_id: str, actor_id, plan: list[dict] | object,
            dimension: str = "lob") -> str:
    jid = str(uuid.uuid4())
    input_data = {"edm_id": edm_id, "portfolio_id": portfolio_id,
                  "dimension": dimension, "actor_id": str(actor_id),
                  "plan": plan}
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, input_data, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, 'analyst_request', :r, :t, 'pending', :d, 0, :now, :now)",
        {"i": jid, "r": portfolio_id, "t": f"run_breakout_{dimension}",
         "d": json.dumps(input_data), "now": datetime.utcnow()},
        connection="WORKBENCH")
    return jid


def _run(jid: str, dimension: str = "lob") -> dict:
    assert portfolio_jobs.run_one(rwb_job_id=jid,
                                  rwb_job_type=f"run_breakout_{dimension}",
                                  worker_id="w1")
    return execute_one(
        "SELECT status_code, output_data, error_detail FROM rwb_job "
        "WHERE id = :i", {"i": jid}, connection="WORKBENCH")


def _rerun(jid: str, dimension: str = "lob") -> dict:
    execute_command(
        "UPDATE rwb_job SET status_code = 'pending', claimed_by = NULL, "
        "output_data = NULL, error_detail = NULL WHERE id = :i",
        {"i": jid}, connection="WORKBENCH")
    return _run(jid, dimension)


def _generated_rows(source_id: str) -> list[dict]:
    return execute(
        "SELECT name, irp_id, breakout_dimension_code, breakout_value, "
        "inserted_by FROM irp_portfolio WHERE source_portfolio_id = :s "
        "AND deleted_at IS NULL ORDER BY breakout_value",
        {"s": source_id}, connection="WORKBENCH")


def _backfill_heads() -> list[dict]:
    return execute(
        "SELECT requestor_type, requestor_id, status_code FROM rwb_job "
        "WHERE rwb_job_type = 'backfill_edm_detail'", {},
        connection="WORKBENCH")


def test_happy_path_creates_rows_with_lineage_and_enqueues_backfill(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"EQ Comm": [4, 5], "FLD Comm": [1, 2, 3]}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("EQ Comm"), _plan_entry("FLD Comm")])

    job = _run(jid)

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert (out["planned"], out["created"], out["failed"]) == (2, 2, 0)
    assert out["backfill_enqueued"] is True
    assert [o["outcome"] for o in out["sub_portfolios"]] == ["created"] * 2
    assert [o["accounts"] for o in out["sub_portfolios"]] == [2, 3]

    rows = _generated_rows(source_id)
    assert [(r["name"], r["breakout_value"]) for r in rows] == [
        ("usfl_commercial - EQ Comm", "EQ Comm"),
        ("usfl_commercial - FLD Comm", "FLD Comm")]
    assert all(r["breakout_dimension_code"] == "lob" for r in rows)
    assert all(r["inserted_by"] == iteration2_db.user_a for r in rows)  # FR-015
    assert all(r["irp_id"] for r in rows)

    # the completion enqueue keys on THIS breakout job row (FR-013)
    heads = _backfill_heads()
    assert [(h["requestor_type"], h["requestor_id"]) for h in heads] == [
        ("rwb_job", jid)]
    # ... and the selection ran ONCE, before the loop
    assert len(fake_irp.selection_calls) == 1
    assert fake_irp.selection_calls[0]["values"] == ["EQ Comm", "FLD Comm"]


def test_state_dimension_shares_the_worker_body(iteration2_db, fake_irp):
    # US2 (T046/FR-004): run_breakout_state runs the same body — the lineage
    # rows carry dimension 'state' with Admin1Code values (P-12), the
    # selection read is asked for the state dimension, and the RM description
    # names the Geography - State dimension label.
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"CA": [3], "TX": [1, 2]}
    plan = [_plan_entry("CA", number="P1-S-CA", label="CALIFORNIA"),
            _plan_entry("TX", number="P1-S-TX", label=None)]
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, plan,
                  dimension="state")

    job = _run(jid, dimension="state")

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert (out["planned"], out["created"], out["failed"]) == (2, 2, 0)
    rows = _generated_rows(source_id)
    assert [(r["breakout_dimension_code"], r["breakout_value"])
            for r in rows] == [("state", "CA"), ("state", "TX")]
    assert fake_irp.selection_calls[0]["dimension"] == "state"
    assert fake_irp.created_sub_portfolios[0]["description"] == (
        "Breakout of portfolio usfl_commercial by Geography - State: "
        "CA (CALIFORNIA)")


def test_worker_executes_persisted_plan_verbatim_and_reads_no_summary(
        iteration2_db, fake_irp):
    # The stored plan's names differ from anything a recompute would produce —
    # they run verbatim; the source portfolio row carries NO exposure_detail,
    # so any summary read would fail loudly (there is none to read).
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"TX": [1]}
    plan = [_plan_entry("TX", name="approved name nobody would recompute (7)",
                        number="P1-S-TX")]
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, plan,
                  dimension="lob")

    job = _run(jid)

    assert job["status_code"] == "succeeded"
    assert fake_irp.created_sub_portfolios[0]["name"] == (
        "approved name nobody would recompute (7)")
    assert fake_irp.created_sub_portfolios[0]["number"] == "P1-S-TX"
    assert fake_irp.summary_reads == []          # no summary read in the worker


def test_description_carries_source_dimension_and_value_untruncated(
        iteration2_db, fake_irp):
    # FR-010: the 40-character name truncates the source; the RM description
    # is where the untruncated lineage lives — composed HERE, in the worker.
    long_source = "TY2607 Meridian Cedant Commercial Book Alpha"   # 44 chars
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id, name=long_source)
    fake_irp.selection_by_value = {"General Liability": [1]}
    truncated = "TY2607 Meridian - General Liability"
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("General Liability", name=truncated,
                               number="P1-L-GENERA1B2C3D")])

    _run(jid)

    created = fake_irp.created_sub_portfolios[0]
    assert created["name"] == truncated
    assert created["description"] == (
        f"Breakout of portfolio {long_source} by Line of business: "
        f"General Liability")


def test_per_entry_isolation_one_failure_never_stops_the_loop(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1], "B": [2], "C": [3]}
    fake_irp.fail_create_for = {"usfl_commercial - B": "RM 500 mid-run"}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B"), _plan_entry("C")])

    job = _run(jid)

    assert job["status_code"] == "succeeded"     # partial success = success
    out = json.loads(job["output_data"])
    assert (out["created"], out["failed"]) == (2, 1)
    failed = next(o for o in out["sub_portfolios"] if o["outcome"] == "failed")
    assert failed["value"] == "B"
    assert "RM 500" in failed["error"]
    assert [r["breakout_value"] for r in _generated_rows(source_id)] == ["A", "C"]


def test_a_failing_lineage_write_fails_only_that_entry(
        iteration2_db, fake_irp):
    # The lineage write refuses to move a Risk Modeler portfolio between
    # breakout keys (portfolio_service._write_generated). That raise must fail
    # ONE sub-portfolio: before the loop guard covered the write, it aborted the
    # whole job after the RM portfolios had been created, and output_data was
    # lost with it.
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    other_source = _mk_source(edm_id, name="other_book", irp_id="2")
    # The fake hands out 431, 432, 433 in order, so entry B lands on 432 — a
    # portfolio already recorded as another source's "Z" breakout.
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, "
        "source_portfolio_id, breakout_dimension_code, breakout_value, "
        "inserted_at, updated_at) VALUES (:i, :e, 'other_book - Z', '432', "
        ":s, 'lob', 'Z', :now, :now)",
        {"i": str(uuid.uuid4()), "e": edm_id, "s": other_source,
         "now": datetime.utcnow()}, connection="WORKBENCH")
    fake_irp.selection_by_value = {"A": [1], "B": [2], "C": [3]}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B"), _plan_entry("C")])

    job = _run(jid)

    assert job["status_code"] == "succeeded"     # partial success = success
    out = json.loads(job["output_data"])
    assert (out["created"], out["failed"]) == (2, 1)
    failed = next(o for o in out["sub_portfolios"] if o["outcome"] == "failed")
    assert failed["value"] == "B"
    assert "already the lob=Z breakout" in failed["error"]
    # A and C persisted; B's RM portfolio stays (P-07 deletes nothing) and the
    # re-run adopts it on its number
    assert [r["breakout_value"] for r in _generated_rows(source_id)] == ["A", "C"]
    assert out["backfill_enqueued"] is True


def test_zero_account_selection_fails_entry_with_no_create_call(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1]}     # B resolves to nothing
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])

    job = _run(jid)

    out = json.loads(job["output_data"])
    failed = next(o for o in out["sub_portfolios"] if o["outcome"] == "failed")
    assert failed["value"] == "B"
    assert "zero accounts" in failed["error"]
    assert "Sync" in failed["error"]             # zero-match points at Sync
    # NO create call was made for B — no empty portfolio reaches RM (FR-008)
    assert [c["name"] for c in fake_irp.created_sub_portfolios] == [
        "usfl_commercial - A"]


def test_per_value_selection_read_error_fails_one_entry(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1]}
    fake_irp.selection_errors = {"B": "IRPAPIError: repeated page fingerprint"}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])

    job = _run(jid)

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    failed = next(o for o in out["sub_portfolios"] if o["outcome"] == "failed")
    assert failed["value"] == "B"
    assert "selection read failed" in failed["error"]
    assert [c["name"] for c in fake_irp.created_sub_portfolios] == [
        "usfl_commercial - A"]                   # never proceed on a short list


def test_short_membership_read_back_fails_the_entry_with_no_lineage_row(
        iteration2_db, fake_irp):
    # The add landed partially: the read-back count is 1 where 3 accounts were
    # selected. That entry fails (FR-008) and gets NO lineage row, so the
    # re-run adopts the created portfolio on its number and re-adds.
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1], "B": [2, 3, 4]}
    fake_irp.readback_counts = {"432": 1}        # B's created portfolio id
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])

    job = _run(jid)

    assert job["status_code"] == "succeeded"      # A succeeded → partial success
    out = json.loads(job["output_data"])
    assert (out["created"], out["failed"]) == (1, 1)
    failed = next(o for o in out["sub_portfolios"] if o["outcome"] == "failed")
    assert failed["value"] == "B"
    assert "holds 1 accounts" in failed["error"]
    assert [r["breakout_value"] for r in _generated_rows(source_id)] == ["A"]


def test_idempotent_rerun_skips_existing_and_creates_only_missing(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1], "B": [2]}
    fake_irp.fail_create_for = {"usfl_commercial - B": "transient RM failure"}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])
    _run(jid)
    assert [r["breakout_value"] for r in _generated_rows(source_id)] == ["A"]

    # the transient failure clears; the analyst re-requests → the SAME stored
    # plan runs again: A skipped by lineage, only B created, names identical
    fake_irp.fail_create_for = {}
    job = _rerun(jid)

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert (out["created"], out["skipped_existing"], out["failed"]) == (1, 1, 0)
    rows = _generated_rows(source_id)
    assert [r["breakout_value"] for r in rows] == ["A", "B"]
    assert [c["name"] for c in fake_irp.created_sub_portfolios] == [
        "usfl_commercial - A", "usfl_commercial - B"]  # A never re-created


def test_full_rerun_all_skipped_reads_as_success(iteration2_db, fake_irp):
    # `completed 0` semantics (W-9): a re-run that creates nothing new is a
    # healthy outcome, never a failure.
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1]}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, [_plan_entry("A")])
    _run(jid)

    job = _rerun(jid)

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert (out["created"], out["skipped_existing"]) == (0, 1)
    assert out["backfill_enqueued"] is True      # figures refresh again (FR-013)


def test_adopt_by_number_with_exactly_one_hit(iteration2_db, fake_irp):
    # RM already holds the sub-portfolio (create-then-crash) — the duplicate
    # name resolves by portfolioNumber, adopts, and re-runs the add to heal an
    # empty adoption (R7).
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1, 2]}
    fake_irp.taken_portfolio_names = {"usfl_commercial - A"}
    fake_irp.hits_by_number = {"P1-L-A": [
        PortfolioHit(irp_id="900", name="usfl_commercial - A")]}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, [_plan_entry("A")])

    job = _run(jid)

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert out["adopted"] == 1
    adopted = out["sub_portfolios"][0]
    assert adopted["irp_id"] == "900"
    assert adopted["accounts"] == 2
    # the heal ran unconditionally against the adopted portfolio
    assert fake_irp.populate_calls == [
        {"portfolio_irp_id": "900", "account_ids": [1, 2]}]
    rows = _generated_rows(source_id)
    assert [(r["irp_id"], r["inserted_by"]) for r in rows] == [
        ("900", iteration2_db.user_a)]


def test_adopt_with_zero_or_many_hits_fails_the_entry(iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1], "B": [2]}
    fake_irp.taken_portfolio_names = {"usfl_commercial - A",
                                      "usfl_commercial - B"}
    fake_irp.hits_by_number = {
        "P1-L-A": [],                                            # no owner
        "P1-L-B": [PortfolioHit(irp_id="900", name="x"),
                   PortfolioHit(irp_id="901", name="y")],        # ambiguous
    }
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])

    job = _run(jid)

    assert job["status_code"] == "failed"        # zero succeeded → fail
    out = json.loads(job["output_data"])
    assert out["failed"] == 2
    by_value = {o["value"]: o for o in out["sub_portfolios"]}
    assert "no portfolio carries number" in by_value["A"]["error"]
    assert "2 portfolios carry number" in by_value["B"]["error"]
    assert _generated_rows(source_id) == []      # nothing adopted arbitrarily
    assert fake_irp.populate_calls == []


def test_source_deleted_in_rm_fails_every_entry_with_no_rows(
        iteration2_db, fake_irp):
    # FR-012: the source was deleted in Risk Modeler between confirm and run —
    # the selection read fails, the job fails with the error recorded, and no
    # lineage row is written.
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.raise_on_selection_read = True
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, [_plan_entry("A")])

    job = _run(jid)

    assert job["status_code"] == "failed"
    assert "account selection failed" in job["error_detail"]
    assert _generated_rows(source_id) == []
    assert fake_irp.created_sub_portfolios == []
    assert _backfill_heads() == []               # nothing succeeded → no enqueue


def test_zero_success_fails_the_job(iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)               # selection resolves nothing
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])

    job = _run(jid)

    assert job["status_code"] == "failed"
    assert "no sub-portfolio succeeded" in job["error_detail"]
    out = json.loads(job["output_data"])
    assert out["failed"] == 2
    assert out["backfill_enqueued"] is False


def test_empty_or_unparseable_plan_fails_with_nothing_created(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    for i, bad_plan in enumerate(([], [{"value": "A"}], "not-a-list")):
        source_id = _mk_source(edm_id, name=f"source-{i}", irp_id=str(10 + i))
        jid = _mk_job(edm_id, source_id, iteration2_db.user_a, bad_plan)
        job = _run(jid)
        assert job["status_code"] == "failed"
        assert "approved plan" in job["error_detail"]
        assert _generated_rows(source_id) == []
    assert fake_irp.created_sub_portfolios == []
    assert fake_irp.selection_calls == []        # failed before any RM read


def test_missing_edm_or_source_fails_gracefully(iteration2_db, fake_irp):
    edm_id = _mk_edm(irp_id=None)                # EDM never got its exposureId
    source_id = _mk_source(edm_id)
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, [_plan_entry("A")])
    assert _run(jid)["status_code"] == "failed"

    edm_id = _mk_edm(name="EDM2")
    jid = _mk_job(edm_id, str(uuid.uuid4()), iteration2_db.user_a,
                  [_plan_entry("A")])            # portfolio row gone
    job = _run(jid)
    assert job["status_code"] == "failed"
    assert "source portfolio missing" in job["error_detail"]


def test_generated_portfolio_visible_to_list_before_backfill(
        iteration2_db, fake_irp):
    # The page's self-poll shows generated portfolios as they land: the row is
    # upserted immediately per entry, with NULL exposure_detail until the
    # auto-fired backfill fills figures in (graceful pending state).
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1]}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a, [_plan_entry("A")])
    _run(jid)

    rows = portfolio_service.list_portfolios(edm_id=edm_id)
    generated = next(r for r in rows if r.name == "usfl_commercial - A")
    assert generated.exposure_detail is None
    assert generated.irp_id


def test_backfill_enqueue_is_idempotent_while_one_is_queued(
        iteration2_db, fake_irp):
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1], "B": [2]}
    fake_irp.fail_create_for = {"usfl_commercial - B": "transient"}
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])
    _run(jid)
    assert len(_backfill_heads()) == 1

    # re-run while the enqueued backfill is still pending → no second head,
    # and backfill_enqueued still reads True (one IS queued)
    fake_irp.fail_create_for = {}
    job = _rerun(jid)
    heads = _backfill_heads()
    assert len(heads) == 1
    assert json.loads(job["output_data"])["backfill_enqueued"] is True


def test_audit_recoverable_from_job_row_and_generated_rows(
        iteration2_db, fake_irp):
    # US3 (T053/FR-015/P-08): after a partial-failure run every audited field
    # is recoverable with no audit-log table — actor from
    # input_data.actor_id, timestamp from the job row, source portfolio from
    # requestor_id, dimension from rwb_job_type, per-sub-portfolio outcomes
    # from output_data.sub_portfolios, and the confirming analyst from each
    # generated row's inserted_by.
    edm_id = _mk_edm()
    source_id = _mk_source(edm_id)
    fake_irp.selection_by_value = {"A": [1]}     # B → zero-match failure
    jid = _mk_job(edm_id, source_id, iteration2_db.user_a,
                  [_plan_entry("A"), _plan_entry("B")])
    _run(jid)

    job = execute_one(
        "SELECT requestor_id, rwb_job_type, input_data, output_data, "
        "updated_at FROM rwb_job WHERE id = :i",
        {"i": jid}, connection="WORKBENCH")
    assert json.loads(job["input_data"])["actor_id"] == str(
        iteration2_db.user_a)                            # 1. actor
    assert job["updated_at"] is not None                 # 2. timestamp
    assert job["requestor_id"] == source_id              # 3. source portfolio
    assert job["rwb_job_type"] == "run_breakout_lob"     # 4. dimension
    outcomes = json.loads(job["output_data"])["sub_portfolios"]
    assert {o["value"]: o["outcome"] for o in outcomes} == {
        "A": "created", "B": "failed"}                   # 5. outcomes
    rows = _generated_rows(source_id)
    assert [r["inserted_by"] for r in rows] == [
        iteration2_db.user_a]                            # 6. confirming analyst


def test_breakout_summarize_outcomes_shape_matches_data_model(
        iteration2_db, fake_irp):
    # data-model §4: output_data carries counts + per-entry detail; failures
    # carry error, successes carry irp_id + accounts.
    outcomes = [
        breakout_service.SubPortfolioOutcome(
            value="TX", name="s - TX", number="P1-S-TX", outcome="created",
            irp_id="431", accounts=220),
        breakout_service.SubPortfolioOutcome(
            value="MT", name="s - MT", number="P1-S-MT", outcome="failed",
            error="selection returned zero accounts"),
    ]
    out = breakout_service.summarize_outcomes(outcomes)
    assert out == {
        "planned": 2, "created": 1, "adopted": 0, "skipped_existing": 0,
        "failed": 1,
        "sub_portfolios": [
            {"value": "TX", "name": "s - TX", "number": "P1-S-TX",
             "outcome": "created", "irp_id": "431", "accounts": 220},
            {"value": "MT", "name": "s - MT", "number": "P1-S-MT",
             "outcome": "failed", "error": "selection returned zero accounts"},
        ],
    }
