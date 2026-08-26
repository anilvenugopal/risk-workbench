"""Unit tests for the analysis-execution workers (spec 010, T022).

Covers ``execute_analysis_batch`` (per-item isolation, a template shared by two
suites submitting once per suite with each suite's own currency and a suffixed
name, resume skip after reclaim keyed on ``execution_item_no``, submission-failure
recording) and ``backfill_analysis_detail`` (resolution by exact submitted name).
"""

from __future__ import annotations

import json
import uuid

from app.services import analysis_execution_service as svc
from app.services import rwb_job_service
from app.workers import analysis_jobs
from app.workers.analysis_jobs import (STORED_RETURN_PERIODS,
                                       build_loss_results_extract)
from db import execute, execute_command, execute_one
from tests.unit.fakes.fake_irp import ep_elements, stats_rows


def _seed_currency(scheme="RMS", vintage="RL25", effective_date="2025-05-28"):
    if not execute_one("SELECT 1 FROM irp_currency WHERE code = 'USD'",
                       {}, connection="WORKBENCH"):
        execute_command(
            "INSERT INTO irp_currency (id, code, name) VALUES "
            "(:id, 'USD', 'US Dollar')",
            {"id": str(uuid.uuid4())}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_currency_scheme (id, code, name) VALUES (:id, :c, :c)",
        {"id": str(uuid.uuid4()), "c": scheme}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_currency_scheme_vintage (id, vintage, "
        "currency_scheme_code, effective_date) VALUES (:id, :v, :s, :e)",
        {"id": str(uuid.uuid4()), "v": vintage, "s": scheme, "e": effective_date},
        connection="WORKBENCH")


def _seed_edm(name="EDM One") -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, 'ready')",
        {"id": edm_id, "name": name}, connection="WORKBENCH")
    return edm_id


def _seed_portfolio(edm_id: str, name="Portfolio A") -> str:
    portfolio_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name) VALUES (:id, :edm, :name)",
        {"id": portfolio_id, "edm": edm_id, "name": name}, connection="WORKBENCH")
    return portfolio_id


def _seed_template(name="Template A") -> str:
    template_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO analysis_template (id, name, analysis_profile_name, "
        "output_profile_name) VALUES (:id, :name, 'Profile', 'Output')",
        {"id": template_id, "name": name}, connection="WORKBENCH")
    return template_id


def _seed_suite(name: str, template_ids: list[str]) -> str:
    suite_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO template_suite (id, name) VALUES (:id, :name)",
        {"id": suite_id, "name": name}, connection="WORKBENCH")
    for template_id in template_ids:
        execute_command(
            "INSERT INTO template_suite_item (id, suite_id, template_id) "
            "VALUES (:id, :suite, :template)",
            {"id": str(uuid.uuid4()), "suite": suite_id, "template": template_id},
            connection="WORKBENCH")
    return suite_id


def _analyses_for(edm_id: str) -> list[dict]:
    return execute(
        "SELECT id, name, full_name, status_code, execution_item_no, "
        "irp_portfolio_id, failure_reason FROM irp_analysis "
        "WHERE edm_id = :e ORDER BY execution_item_no",
        {"e": edm_id}, connection="WORKBENCH")


def _rwb_job_of(execution_id: str) -> dict:
    return execute_one(
        "SELECT id, status_code, output_data FROM rwb_job "
        "WHERE requestor_type = 'analyst_request' AND requestor_id = :e",
        {"e": execution_id}, connection="WORKBENCH")


def _run_execution(*, edm_id, portfolio_id, kind="template", suite_picks=None,
                   template_ids=None, actor_id) -> str:
    return svc.request_execution(
        edm_id=edm_id, kind=kind, portfolio_ids=[portfolio_id], treaty_names=[],
        suite_picks=suite_picks, template_ids=template_ids,
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        actor_id=actor_id)


# ── happy path + naming ───────────────────────────────────────────────────────

def test_batch_worker_submits_and_records_job(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    execution_id = _run_execution(
        edm_id=edm_id, portfolio_id=portfolio_id, template_ids=[template_id],
        actor_id=iteration2_db.user_a)

    n = analysis_jobs.run_pending(worker_id="w1")
    assert n == 1

    job = _rwb_job_of(execution_id)
    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"]) == {"submitted": 1, "submission_failed": 0}

    rows = _analyses_for(edm_id)
    assert len(rows) == 1
    assert rows[0]["name"] == "CRE_Portfolio A_Template A"
    assert rows[0]["full_name"] == "CRE_Portfolio A_Template A"
    assert rows[0]["status_code"] == "running"
    assert rows[0]["irp_portfolio_id"] == portfolio_id

    irp_job = execute_one(
        "SELECT status, irp_analysis_id, request_params FROM irp_job "
        "WHERE irp_analysis_id = :a", {"a": rows[0]["id"]}, connection="WORKBENCH")
    assert irp_job["status"] == "QUEUED"
    assert json.loads(irp_job["request_params"])["job_name"] == "CRE_Portfolio A_Template A"


def test_shared_template_across_two_suites_submits_twice_with_suffix_and_own_currency(
        iteration2_db, fake_irp):
    _seed_currency(scheme="RMS", vintage="RL25", effective_date="2025-05-28")
    _seed_currency(scheme="DT", vintage="RL24", effective_date="2024-05-28")
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template("Shared Template")
    suite1 = _seed_suite("Suite One", [template_id])
    suite2 = _seed_suite("Suite Two", [template_id])

    execution_id = svc.request_execution(
        edm_id=edm_id, kind="suite", portfolio_ids=[portfolio_id], treaty_names=[],
        suite_picks=[
            svc.SuitePick(suite_id=suite1, template_ids=[template_id],
                         currency_code="USD", currency_scheme="RMS",
                         currency_vintage="RL25"),
            svc.SuitePick(suite_id=suite2, template_ids=[template_id],
                         currency_code="USD", currency_scheme="DT",
                         currency_vintage="RL24"),
        ], actor_id=iteration2_db.user_a)

    analysis_jobs.run_pending(worker_id="w1")

    rows = _analyses_for(edm_id)
    assert len(rows) == 2
    names = sorted(r["name"] for r in rows)
    assert names == ["CRE_Portfolio A_Shared Template",
                     "CRE_Portfolio A_Shared Template_2"]
    submits = {s["job_name"]: s["currency"] for s in fake_irp.analysis_submits}
    assert submits["CRE_Portfolio A_Shared Template"]["scheme"] == "RMS"
    assert submits["CRE_Portfolio A_Shared Template_2"]["scheme"] == "DT"
    assert _rwb_job_of(execution_id)["status_code"] == "succeeded"


# ── per-item isolation + submission failure ──────────────────────────────────────

def test_one_item_failing_to_submit_never_stops_the_loop(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm()
    p1 = _seed_portfolio(edm_id, "Portfolio A")
    p2 = _seed_portfolio(edm_id, "Portfolio B")
    template_id = _seed_template("Template A")
    fake_irp.raise_on_submit_analysis_for.add("CRE_Portfolio A_Template A")

    execution_id = svc.request_execution(
        edm_id=edm_id, kind="template", portfolio_ids=[p1, p2], treaty_names=[],
        template_ids=[template_id], currency_code="USD", currency_scheme="RMS",
        currency_vintage="RL25", actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")

    job = _rwb_job_of(execution_id)
    assert job["status_code"] == "succeeded"  # not every item failed
    assert json.loads(job["output_data"]) == {"submitted": 1, "submission_failed": 1}

    rows = {r["name"]: r for r in _analyses_for(edm_id)}
    failed = rows["CRE_Portfolio A_Template A"]
    assert failed["status_code"] == "pending"
    assert failed["failure_reason"] and "forced analysis submit failure" in failed[
        "failure_reason"]
    ok = rows["CRE_Portfolio B_Template A"]
    assert ok["status_code"] == "running"

    failed_job = execute_one(
        "SELECT status, submission_attempt_count FROM irp_job "
        "WHERE irp_analysis_id = :a", {"a": failed["id"]}, connection="WORKBENCH")
    assert failed_job["status"] == "SUBMISSION FAILED"
    assert failed_job["submission_attempt_count"] == 1


def test_every_item_failing_to_submit_fails_the_rwb_job(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template("Template A")
    fake_irp.raise_on_submit_analysis_for.add("CRE_Portfolio A_Template A")

    execution_id = svc.request_execution(
        edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id], treaty_names=[],
        template_ids=[template_id], currency_code="USD", currency_scheme="RMS",
        currency_vintage="RL25", actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")

    job = _rwb_job_of(execution_id)
    assert job["status_code"] == "failed"


# ── resume after reclaim ─────────────────────────────────────────────────────────

def test_resume_skips_item_whose_analysis_already_has_a_job(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    execution_id = _run_execution(
        edm_id=edm_id, portfolio_id=portfolio_id, template_ids=[template_id],
        actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    assert len(fake_irp.analysis_submits) == 1

    # Reclaim: rwb_job back to pending, worker re-runs the same body.
    execute_command(
        "UPDATE rwb_job SET status_code = 'pending' WHERE requestor_id = :e",
        {"e": execution_id}, connection="WORKBENCH")
    n = analysis_jobs.run_pending(worker_id="w1")
    assert n == 1
    assert len(fake_irp.analysis_submits) == 1  # no duplicate RM submit
    assert len(_analyses_for(edm_id)) == 1


def test_resume_reuses_claimed_name_when_crash_left_no_irp_job(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    plan = {
        "execution_id": str(uuid.uuid4()), "edm_id": edm_id, "edm_name": "EDM One",
        "submission_id": None, "actor_id": iteration2_db.user_a, "treaty_names": [],
        "portfolios": [{"id": portfolio_id, "name": "Portfolio A"}],
        "items": [{
            "item_no": 0, "suite_id": None, "suite_name": None,
            "template_id": template_id, "template_name": "Template A",
            "analysis_profile_name": "Profile", "output_profile_name": "Output",
            "event_rate_scheme_name": None,
            "currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25",
                        "asOfDate": "2025-05-28"},
            "min_loss_threshold": 1.0, "num_max_loss_event": 1,
            "franchise_deductible": False,
            "treat_construction_occupancy_as_unknown": True, "tag_names": [],
        }],
    }
    # Simulate the crash: the analysis row is claimed (step 2) but no irp_job
    # exists yet (the worker died before step 4).
    claimed = analysis_jobs._claim_analysis(
        edm_id=edm_id, portfolio=plan["portfolios"][0], item=plan["items"][0],
        execution_id=plan["execution_id"], actor_id=iteration2_db.user_a)
    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'analyst_request', :rid, "
        "'execute_analysis_batch', 'pending', :input)",
        {"id": job_id, "rid": plan["execution_id"], "input": json.dumps(plan)},
        connection="WORKBENCH")

    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="execute_analysis_batch",
                          worker_id="w1")

    rows = _analyses_for(edm_id)
    assert len(rows) == 1
    assert rows[0]["id"] == claimed["id"]
    assert rows[0]["name"] == claimed["name"]
    assert rows[0]["status_code"] == "running"


# ── backfill_analysis_detail ──────────────────────────────────────────────────────

def test_backfill_resolves_by_exact_submitted_name(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm("EDM One")
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]

    fake_irp.add_own_analysis(name=analysis["name"], edm_name="EDM One",
                              analysis_id="9001",
                              exposure_resource_id="res-1",
                              exposure_resource_type="PORTFOLIO")

    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'irp_job', :rid, "
        "'backfill_analysis_detail', 'pending', :input)",
        {"id": job_id, "rid": str(uuid.uuid4()),
         "input": json.dumps({"analysis_id": analysis["id"]})},
        connection="WORKBENCH")
    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="backfill_analysis_detail",
                          worker_id="w1")

    updated = execute_one(
        "SELECT irp_id, status_code, exposure_resource_id FROM irp_analysis "
        "WHERE id = :id", {"id": analysis["id"]}, connection="WORKBENCH")
    assert updated["irp_id"] == "9001"
    assert updated["status_code"] == "ready"
    # copied from the resourceUri captured at submit (irp_job_resource) — never
    # re-resolved (T-02) — not the search-hit's own exposure_resource_id.
    assert updated["exposure_resource_id"] == f"/irp/analysis/1"


def test_backfill_resolve_failure_fails_the_rwb_job(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm("EDM One")
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]
    # no add_own_analysis seeded -> get_analysis_by_name raises

    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'irp_job', :rid, "
        "'backfill_analysis_detail', 'pending', :input)",
        {"id": job_id, "rid": str(uuid.uuid4()),
         "input": json.dumps({"analysis_id": analysis["id"]})},
        connection="WORKBENCH")
    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="backfill_analysis_detail",
                          worker_id="w1")

    job = execute_one("SELECT status_code, error_detail FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert job["status_code"] == "failed"
    assert job["error_detail"]
    still = execute_one("SELECT status_code FROM irp_analysis WHERE id = :id",
                        {"id": analysis["id"]}, connection="WORKBENCH")
    assert still["status_code"] == "running"  # untouched — "completed, details pending"


# ── retrieve_analysis_results: the extract builder (spec 011 US1, T-04/O-03) ─────

_FIVE = ["GR", "RL", "WX", "QS", "GU"]


def _gr_capture(pure_premium=38270.59, total_std_dev=2645726.19, base=1.0):
    return (stats_rows(analysis_id=1, perspective_code="GR",
                       exposure_resource_id=5, pure_premium=pure_premium,
                       total_std_dev=total_std_dev),
            ep_elements(analysis_id=1, perspective_code="GR",
                        exposure_resource_id=5, base=base))


def test_builder_looks_up_the_11_points_and_drops_tce():
    stats, ep = _gr_capture()
    doc = build_loss_results_extract(
        perspective_codes=_FIVE, results={"GR": (stats, ep)},
        settings={"engineType": "RL", "engineVersion": "23.0"},
        retrieved_at="2026-08-26T00:00:00Z")

    gr = doc["perspectives"]["GR"]
    assert set(gr["oep"]) == {str(rp) for rp in STORED_RETURN_PERIODS}
    # the fixture's loss = base·rp·factor (OEP 1, AEP 2, TCE 90/180): a leaked
    # TCE value or an interpolated point would be unmistakable
    assert gr["oep"]["10000"] == 10000.0
    assert gr["aep"]["10000"] == 20000.0
    assert gr["oep"]["5"] == 5.0
    assert gr["aal"] == 38270.59
    assert gr["std_dev"] == 2645726.19
    assert doc["engine_type"] == "RL"
    assert doc["engine_version"] == "23.0"
    assert doc["retrieved_at"] == "2026-08-26T00:00:00Z"


def test_builder_empty_perspective_is_explicit_null():
    stats, ep = _gr_capture()
    doc = build_loss_results_extract(
        perspective_codes=_FIVE, results={"GR": (stats, ep)},
        settings=None, retrieved_at="2026-08-26T00:00:00Z")
    assert set(doc["perspectives"]) == set(_FIVE)
    for code in ("RL", "WX", "QS", "GU"):
        assert doc["perspectives"][code] is None
    # engine fields absent from the metadata are stored as null, never omitted
    assert doc["engine_type"] is None
    assert doc["engine_version"] is None


def test_builder_takes_aal_and_std_dev_from_the_oep_stats_row_only():
    _, ep = _gr_capture()
    aep_only = stats_rows(analysis_id=1, perspective_code="GR",
                          exposure_resource_id=5, pure_premium=1.0,
                          total_std_dev=2.0, ep_type="AEP")
    doc = build_loss_results_extract(
        perspective_codes=_FIVE, results={"GR": (aep_only, ep)},
        settings=None, retrieved_at="t")
    gr = doc["perspectives"]["GR"]
    assert gr is not None          # EP rows exist — not an empty perspective
    assert gr["aal"] is None and gr["std_dev"] is None

    absent_fields = [{"epType": "OEP"}]
    doc = build_loss_results_extract(
        perspective_codes=_FIVE, results={"GR": (absent_fields, ep)},
        settings=None, retrieved_at="t")
    gr = doc["perspectives"]["GR"]
    assert gr["aal"] is None and gr["std_dev"] is None


def test_builder_holds_no_code_list_of_its_own():
    doc = build_loss_results_extract(
        perspective_codes=[*_FIVE, "XX"], results={},
        settings=None, retrieved_at="t")
    assert set(doc["perspectives"]) == {*_FIVE, "XX"}
    assert doc["perspectives"]["XX"] is None


# ── retrieve_analysis_results: the worker (FR-006/FR-007, T-03) ─────────────────


def _seed_finished_analysis(*, irp_id="9001", portfolio_irp_id="555",
                            settings=None, loss_results=None) -> str:
    edm_id = _seed_edm(f"EDM {uuid.uuid4().hex[:8]}")
    portfolio_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id) "
        "VALUES (:id, :edm, 'Portfolio A', :irp)",
        {"id": portfolio_id, "edm": edm_id, "irp": portfolio_irp_id},
        connection="WORKBENCH")
    analysis_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, edm_id, irp_portfolio_id, name, "
        "status_code, irp_id, settings_metadata, loss_results) "
        "VALUES (:id, :edm, :p, 'A', 'ready', :irp, :sm, :lr)",
        {"id": analysis_id, "edm": edm_id, "p": portfolio_id, "irp": irp_id,
         "sm": (json.dumps(settings) if settings else None),
         "lr": (json.dumps(loss_results) if loss_results else None)},
        connection="WORKBENCH")
    return analysis_id


def _run_retrieval(analysis_id: str) -> dict:
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_analysis", requestor_id=analysis_id,
        rwb_job_type="retrieve_analysis_results",
        input_data={"analysis_id": analysis_id})
    analysis_jobs.run_one(rwb_job_id=job_id,
                          rwb_job_type="retrieve_analysis_results",
                          worker_id="w1")
    return execute_one(
        "SELECT status_code, output_data, error_detail FROM rwb_job "
        "WHERE id = :id", {"id": job_id}, connection="WORKBENCH")


def _stored_extract(analysis_id: str):
    raw = execute_one("SELECT loss_results FROM irp_analysis WHERE id = :id",
                      {"id": analysis_id}, connection="WORKBENCH")["loss_results"]
    return json.loads(raw) if raw else None


def test_retrieval_stores_the_extract_and_reports_row_counts(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis(
        settings={"engineType": "DLM", "engineVersion": "23.0"})

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    output = json.loads(job["output_data"])
    assert output["perspectives_with_data"] == 2  # FakeIRP defaults: GR + GU
    assert output["stats_rows"] == {"GR": 1, "RL": 0, "WX": 0, "QS": 0, "GU": 1}
    doc = _stored_extract(analysis_id)
    assert doc["engine_type"] == "DLM"
    assert doc["perspectives"]["GR"]["aal"] == 38270.5904752427
    assert doc["perspectives"]["RL"] is None
    # 5 perspectives × (stats + ep), in kind-table order, against the portfolio pointer
    assert len(fake_irp.result_calls) == 10
    assert [c["perspective_code"] for c in fake_irp.result_calls] == [
        "GR", "GR", "RL", "RL", "WX", "WX", "QS", "QS", "GU", "GU"]
    assert all(c["exposure_resource_id"] == "555" for c in fake_irp.result_calls)


def test_retrieval_skips_when_results_already_stored(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis(
        loss_results={"perspectives": {"GR": None}})

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"]) == {"skipped": "results already stored"}
    assert fake_irp.result_calls == []


def test_retrieval_fails_without_rm_id(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis(irp_id=None)

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "failed"
    assert job["error_detail"] == "analysis has no RM id"
    assert fake_irp.result_calls == []


def test_retrieval_failure_leaves_extract_null_and_run_finished(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis()
    fake_irp.raise_on_analysis_results_for.add("WX")

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "failed"
    assert "WX" in job["error_detail"]
    assert _stored_extract(analysis_id) is None  # no partial write (T-04)
    still = execute_one("SELECT status_code FROM irp_analysis WHERE id = :id",
                        {"id": analysis_id}, connection="WORKBENCH")
    assert still["status_code"] == "ready"  # the run stays FINISHED (O-06)


def test_retrieval_rereads_metadata_when_the_pointer_is_missing(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis(portfolio_irp_id=None, settings=None)
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E",
                          analysis_id="9001", exposure_resource_id="777",
                          metadata={"engineType": "HD", "engineVersion": "3.0"})

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    doc = _stored_extract(analysis_id)
    # the re-read supplied both the pointer and the engine fields (T011)
    assert doc["engine_type"] == "HD"
    assert all(c["exposure_resource_id"] == "777" for c in fake_irp.result_calls)


def test_retrieval_fails_when_no_pointer_anywhere(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis(portfolio_irp_id=None)
    # nothing seeded → the metadata re-read returns no pointer either

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "failed"
    assert job["error_detail"] == "no exposure pointer"


def _retrieval_jobs_for(analysis_id: str) -> list[dict]:
    return execute(
        "SELECT id, status_code FROM rwb_job WHERE requestor_type = 'irp_analysis' "
        "AND requestor_id = :a AND rwb_job_type = 'retrieve_analysis_results'",
        {"a": analysis_id}, connection="WORKBENCH")


def test_backfill_success_chains_one_retrieval_and_a_refire_is_a_noop(
        iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm("EDM One")
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]
    fake_irp.add_own_analysis(name=analysis["name"], edm_name="EDM One",
                              analysis_id="9001")

    def _backfill():
        job_id = str(uuid.uuid4())
        execute_command(
            "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
            "status_code, input_data) VALUES (:id, 'irp_job', :rid, "
            "'backfill_analysis_detail', 'pending', :input)",
            {"id": job_id, "rid": str(uuid.uuid4()),
             "input": json.dumps({"analysis_id": analysis["id"]})},
            connection="WORKBENCH")
        analysis_jobs.run_one(rwb_job_id=job_id,
                              rwb_job_type="backfill_analysis_detail",
                              worker_id="w1")

    _backfill()
    assert len(_retrieval_jobs_for(analysis["id"])) == 1

    _backfill()  # re-fired trigger: the UNIQUE key makes the insert a no-op (FR-006)
    assert len(_retrieval_jobs_for(analysis["id"])) == 1


# ── submitted-settings snapshot at claim (T-09 / FR-022) ────────────────────────


def _plan_item(**overrides) -> dict:
    item = {
        "item_no": 0, "suite_id": None, "suite_name": None,
        "template_id": None, "template_name": "Template A",
        "analysis_profile_name": "Profile", "output_profile_name": "Output",
        "event_rate_scheme_name": None,
        "currency": {"code": "USD", "scheme": "RMS", "vintage": "RL25",
                    "asOfDate": "2025-05-28"},
        "min_loss_threshold": 1.0, "num_max_loss_event": 1,
        "franchise_deductible": False,
        "treat_construction_occupancy_as_unknown": True, "tag_names": [],
    }
    item.update(overrides)
    return item


def test_claim_snapshots_the_plan_item_and_a_resumed_claim_keeps_it(
        iteration2_db, fake_irp):
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    execution_id = str(uuid.uuid4())
    item = _plan_item(template_id=template_id)

    claimed = analysis_jobs._claim_analysis(
        edm_id=edm_id, portfolio={"id": portfolio_id, "name": "Portfolio A"},
        item=item, execution_id=execution_id, actor_id=iteration2_db.user_a)

    stored = execute_one(
        "SELECT submitted_settings FROM irp_analysis WHERE id = :id",
        {"id": claimed["id"]}, connection="WORKBENCH")["submitted_settings"]
    assert json.loads(stored) == item

    # a resumed claim (crash between claim and submit) reuses the row and never
    # rewrites the snapshot — approved plans are immutable (rule 8)
    edited = _plan_item(template_id=template_id, min_loss_threshold=99.0)
    again = analysis_jobs._claim_analysis(
        edm_id=edm_id, portfolio={"id": portfolio_id, "name": "Portfolio A"},
        item=edited, execution_id=execution_id, actor_id=iteration2_db.user_a)
    assert again["id"] == claimed["id"]
    kept = execute_one(
        "SELECT submitted_settings FROM irp_analysis WHERE id = :id",
        {"id": claimed["id"]}, connection="WORKBENCH")["submitted_settings"]
    assert json.loads(kept)["min_loss_threshold"] == 1.0
