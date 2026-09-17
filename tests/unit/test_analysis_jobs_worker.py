"""Unit tests for the analysis-execution workers (spec 010, T022).

Covers ``execute_analysis_batch`` (per-item isolation, a template shared by two
suites submitting once per suite with each suite's own currency and a suffixed
name, resume skip after reclaim keyed on ``execution_item_no``, submission-failure
recording), ``finalize_analysis`` (detail resolved by RM's ``analysisId``), and
the naming helpers (T-04/T-05).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

from app.services import analysis_execution_service as svc
from app.services import analysis_service, irp_gateway, rwb_job_service
from app.workers import analysis_jobs
from app.workers.analysis_jobs import (STORED_RETURN_PERIODS,
                                       build_loss_results_extract)
from db import execute, execute_command, execute_one
from tests.unit.analysis_rows import (
    seed_currency,
    seed_edm,
    seed_portfolio,
    seed_suite,
    seed_template,
)
from tests.unit.fakes.fake_irp import ep_elements, stats_rows
from tests.unit.grouping_rows import seed_group, seed_submission
from tests.unit.run_details_fixtures import captured_run, detail


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


# ── naming helpers (T-04/T-05) ───────────────────────────────────────────────────

def test_build_full_name_is_cre_prefixed_underscore_delimited():
    assert analysis_jobs.build_full_name("US Southeast Wind", "US HU DLM v23") == (
        "CRE_US Southeast Wind_US HU DLM v23")


def test_name_attempt_zero_has_no_suffix_and_clips_at_64():
    full = "x" * 80
    full_name, name = analysis_jobs.name_attempt(full, 0)
    assert full_name == full
    assert name == full[:64]
    assert len(name) == 64


def test_name_attempt_suffix_re_clips_base_so_it_still_fits_64():
    full = "x" * 80
    full_name, name = analysis_jobs.name_attempt(full, 1)
    assert full_name == full + "_2"
    assert name == full[:64 - len("_2")] + "_2"
    assert len(name) == 64


def test_name_attempt_suffix_survives_on_a_short_name():
    full_name, name = analysis_jobs.name_attempt("Short Name", 2)
    assert full_name == "Short Name_3"
    assert name == "Short Name_3"


# ── happy path + naming ───────────────────────────────────────────────────────

def test_batch_worker_submits_and_records_job(iteration2_db, fake_irp):
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
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
    # `pending` until a terminal write: irp_job.status carries the progress.
    assert rows[0]["status_code"] == "pending"
    assert rows[0]["irp_portfolio_id"] == portfolio_id

    irp_job = execute_one(
        "SELECT status, irp_analysis_id, request_params FROM irp_job "
        "WHERE irp_analysis_id = :a", {"a": rows[0]["id"]}, connection="WORKBENCH")
    assert irp_job["status"] == "QUEUED"
    assert json.loads(irp_job["request_params"])["job_name"] == "CRE_Portfolio A_Template A"


def test_shared_template_across_two_suites_submits_twice_with_suffix_and_own_currency(
        iteration2_db, fake_irp):
    seed_currency(scheme="RMS", vintage="RL25", effective_date="2025-05-28")
    seed_currency(scheme="DT", vintage="RL24", effective_date="2024-05-28")
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template("Shared Template")
    suite1 = seed_suite("Suite One", [template_id])
    suite2 = seed_suite("Suite Two", [template_id])

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
    seed_currency()
    edm_id = seed_edm()
    p1 = seed_portfolio(edm_id, "Portfolio A")
    p2 = seed_portfolio(edm_id, "Portfolio B")
    template_id = seed_template("Template A")
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
    assert ok["status_code"] == "pending"
    assert ok["failure_reason"] is None

    failed_job = execute_one(
        "SELECT status, submission_attempt_count FROM irp_job "
        "WHERE irp_analysis_id = :a", {"a": failed["id"]}, connection="WORKBENCH")
    assert failed_job["status"] == "SUBMISSION FAILED"
    assert failed_job["submission_attempt_count"] == 1


def test_every_item_failing_to_submit_fails_the_rwb_job(iteration2_db, fake_irp):
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template("Template A")
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
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
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
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
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
        treaty_names=[], execution_id=plan["execution_id"],
        actor_id=iteration2_db.user_a)
    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'analyst_request', :rid, "
        "'edm', :edm, 'execution', :rid, "
        "'execute_analysis_batch', 'pending', :input)",
        {"id": job_id, "rid": plan["execution_id"], "edm": plan["edm_id"],
         "input": json.dumps(plan)},
        connection="WORKBENCH")

    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="execute_analysis_batch",
                          worker_id="w1")

    rows = _analyses_for(edm_id)
    assert len(rows) == 1
    assert rows[0]["id"] == claimed["id"]
    assert rows[0]["name"] == claimed["name"]
    assert rows[0]["status_code"] == "pending"


# ── finalize_analysis ─────────────────────────────────────────────────────────

def test_finalize_resolves_by_job_payload_analysis_id(iteration2_db, fake_irp):
    seed_currency()
    edm_id = seed_edm("EDM One")
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]

    fake_irp.add_analysis(analysis_id="9001", source_rdm_name="-",
                          exposure_name="EDM One",
                          exposure_resource_id="res-1",
                          exposure_resource_type="PORTFOLIO",
                          metadata={"appAnalysisId": 41867})

    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'irp_job', :rid, 'edm', :edm, "
        "'irp_analysis', :aid, 'finalize_analysis', 'pending', :input)",
        {"id": job_id, "rid": str(uuid.uuid4()), "edm": edm_id,
         "aid": analysis["id"],
         "input": json.dumps({"analysis_id": analysis["id"],
                              "rm_analysis_id": "9001"})},
        connection="WORKBENCH")
    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="finalize_analysis",
                          worker_id="w1")

    updated = execute_one(
        "SELECT irp_id, irp_app_analysis_id, status_code, exposure_resource_id "
        "FROM irp_analysis WHERE id = :id",
        {"id": analysis["id"]}, connection="WORKBENCH")
    assert updated["irp_id"] == "9001"
    assert updated["irp_app_analysis_id"] == "41867"
    assert updated["status_code"] == "ready"
    # exposure_resource_id belongs to broker rows (RM's numeric
    # exposureResourceId, R9/FR-036); the portfolio resourceUri stays in
    # irp_job_resource, where the submit put it.
    assert updated["exposure_resource_id"] is None
    resource = execute_one(
        "SELECT r.resource_uri FROM irp_job j "
        "JOIN irp_job_resource r ON r.irp_job_id = j.id "
        "WHERE j.irp_analysis_id = :id AND r.resource_type = 'portfolio'",
        {"id": analysis["id"]}, connection="WORKBENCH")
    assert resource["resource_uri"] == "/irp/analysis/1"


def _run_finalize(input_data: dict, edm_id: str) -> str:
    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'irp_job', :rid, 'edm', :edm, "
        "'irp_analysis', :aid, 'finalize_analysis', 'pending', :input)",
        {"id": job_id, "rid": str(uuid.uuid4()), "edm": edm_id,
         "aid": input_data["analysis_id"],
         "input": json.dumps(input_data)},
        connection="WORKBENCH")
    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="finalize_analysis",
                          worker_id="w1")
    return job_id


def _assert_finalize_failed(job_id, analysis_id, reason_fragment: str) -> None:
    job = execute_one("SELECT status_code, error_detail FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert job["status_code"] == "failed"
    assert job["error_detail"]
    # The irp_job already reads FINISHED, so the analysis has to end terminal too
    # or the EDM page's 3s poll never stops.
    ended = execute_one(
        "SELECT status_code, failure_reason FROM irp_analysis WHERE id = :id",
        {"id": analysis_id}, connection="WORKBENCH")
    assert ended["status_code"] == "error"
    assert reason_fragment in ended["failure_reason"]


def test_finalize_without_analysis_id_fails_the_rwb_job(iteration2_db, fake_irp):
    seed_currency()
    edm_id = seed_edm("EDM One")
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]

    # completion payload carried no tasks[].output.log.analysisId
    job_id = _run_finalize({"analysis_id": analysis["id"],
                            "rm_analysis_id": None}, edm_id)
    _assert_finalize_failed(job_id, analysis["id"], "no analysisId")


def test_finalize_metadata_failure_fails_the_rwb_job(iteration2_db, fake_irp):
    seed_currency()
    edm_id = seed_edm("EDM One")
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]
    fake_irp.raise_on_analysis_metadata = True

    job_id = _run_finalize({"analysis_id": analysis["id"],
                            "rm_analysis_id": "9001"}, edm_id)
    _assert_finalize_failed(job_id, analysis["id"], "analysis resolve failed")
    retained = execute_one(
        "SELECT irp_id FROM irp_analysis WHERE id = :id",
        {"id": analysis["id"]}, connection="WORKBENCH")
    assert retained["irp_id"] == "9001"


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


def test_builder_stores_null_for_a_point_the_hd_curve_omits():
    """An HD analysis returns a 12-point curve with no 2,000-year return period
    (research R3a); the other ten stored points are kept verbatim."""
    stats, ep = _gr_capture()
    hd_periods = [10000.0, 5000.0, 1000.0, 500.0, 250.0, 200.0,
                  100.0, 50.0, 25.0, 10.0, 5.0, 2.0]
    for element in ep:
        element["value"] = {"returnPeriods": hd_periods,
                            "positionValues": list(hd_periods)}

    doc = build_loss_results_extract(
        perspective_codes=_FIVE, results={"GR": (stats, ep)},
        settings={"engineType": "HD", "engineVersion": "HDv2.1"},
        retrieved_at="2026-09-03T00:00:00Z")

    gr = doc["perspectives"]["GR"]
    assert set(gr["oep"]) == {str(rp) for rp in STORED_RETURN_PERIODS}
    assert gr["oep"]["2000"] is None
    assert gr["aep"]["2000"] is None
    assert gr["oep"]["1000"] == 1000.0
    assert gr["oep"]["5000"] == 5000.0


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


# ── retrieve_analysis_results: the worker (FR-006/FR-007, T-03) ─────────────────


def _seed_finished_analysis(*, irp_id="9001", portfolio_irp_id="555",
                            settings=None, loss_results=None) -> str:
    edm_id = seed_edm(f"EDM {uuid.uuid4().hex[:8]}")
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
    row = execute_one("SELECT edm_id, rdm_id FROM irp_analysis WHERE id = :id",
                      {"id": analysis_id}, connection="WORKBENCH")
    link_type = "edm" if row["edm_id"] is not None else "rdm"
    link_id = row["edm_id"] if row["edm_id"] is not None else row["rdm_id"]
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_analysis", requestor_id=analysis_id,
        rwb_job_type="retrieve_analysis_results",
        link_type=link_type, link_id=link_id,
        context_type="irp_analysis", context_id=analysis_id,
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


# ── retrieve_analysis_results: broker pointer resolution (US2, T-03/O-02) ────────


def _seed_broker_analysis(*, irp_id="7001", exposure_resource_id=None,
                          settings=None) -> str:
    rdm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_rdm (id, name, status) VALUES (:id, 'R', 'ready')",
        {"id": rdm_id}, connection="WORKBENCH")
    analysis_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, rdm_id, irp_id, name, status_code, "
        "exposure_resource_id, settings_metadata) "
        "VALUES (:id, :rdm, :irp, 'B', 'ready', :x, :sm)",
        {"id": analysis_id, "rdm": rdm_id, "irp": irp_id,
         "x": exposure_resource_id,
         "sm": (json.dumps(settings) if settings else None)},
        connection="WORKBENCH")
    return analysis_id


def test_broker_retrieval_uses_the_stored_exposure_pointer(iteration2_db, fake_irp):
    analysis_id = _seed_broker_analysis(exposure_resource_id="888",
                                        settings={"engineType": "DLM"})

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    assert _stored_extract(analysis_id)["engine_type"] == "DLM"
    assert all(c["exposure_resource_id"] == "888" for c in fake_irp.result_calls)


def test_broker_retrieval_rereads_metadata_when_the_stored_pointer_is_null(
        iteration2_db, fake_irp):
    analysis_id = _seed_broker_analysis(exposure_resource_id=None, settings=None)
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E",
                          analysis_id="7001", exposure_resource_id="777",
                          metadata={"engineType": "HD", "engineVersion": "3.0"})

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    doc = _stored_extract(analysis_id)
    assert doc["engine_type"] == "HD"  # the re-read filled the engine fields too
    assert all(c["exposure_resource_id"] == "777" for c in fake_irp.result_calls)


def test_broker_retrieval_fails_when_no_pointer_anywhere(iteration2_db, fake_irp):
    analysis_id = _seed_broker_analysis(exposure_resource_id=None)

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "failed"
    assert job["error_detail"] == "no exposure pointer"
    assert _stored_extract(analysis_id) is None


def _retrieval_jobs_for(analysis_id: str) -> list[dict]:
    return execute(
        "SELECT id, status_code FROM rwb_job WHERE requestor_type = 'irp_analysis' "
        "AND requestor_id = :a AND rwb_job_type = 'retrieve_analysis_results'",
        {"a": analysis_id}, connection="WORKBENCH")


def test_finalize_success_chains_one_retrieval_and_a_refire_is_a_noop(
        iteration2_db, fake_irp):
    seed_currency()
    edm_id = seed_edm("EDM One")
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
    _run_execution(edm_id=edm_id, portfolio_id=portfolio_id,
                  template_ids=[template_id], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = _analyses_for(edm_id)[0]
    fake_irp.add_analysis(analysis_id="9001", source_rdm_name="-",
                          exposure_name="EDM One",
                          metadata={"appAnalysisId": 41867})

    _run_finalize({"analysis_id": analysis["id"], "rm_analysis_id": "9001"}, edm_id)
    assert len(_retrieval_jobs_for(analysis["id"])) == 1

    # re-fired trigger: the UNIQUE key makes the insert a no-op (FR-006)
    _run_finalize({"analysis_id": analysis["id"], "rm_analysis_id": "9001"}, edm_id)
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
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
    execution_id = str(uuid.uuid4())
    item = _plan_item(template_id=template_id)

    claimed = analysis_jobs._claim_analysis(
        edm_id=edm_id, portfolio={"id": portfolio_id, "name": "Portfolio A"},
        item=item, treaty_names=["PR1", "PR2"], execution_id=execution_id,
        actor_id=iteration2_db.user_a)

    stored = execute_one(
        "SELECT submitted_settings FROM irp_analysis WHERE id = :id",
        {"id": claimed["id"]}, connection="WORKBENCH")["submitted_settings"]
    # the plan item plus the treaty names the batch plan selected (T-05)
    assert json.loads(stored) == {**item, "treaty_names": ["PR1", "PR2"]}
    row = analysis_service._submitted_view(stored)
    assert row.construction_occupancy == "Treat as unknown"
    assert row.currency == "USD"

    # a resumed claim (crash between claim and submit) reuses the row and never
    # rewrites the snapshot — approved plans are immutable (rule 8)
    edited = _plan_item(template_id=template_id, min_loss_threshold=99.0)
    again = analysis_jobs._claim_analysis(
        edm_id=edm_id, portfolio={"id": portfolio_id, "name": "Portfolio A"},
        item=edited, treaty_names=["PR3"], execution_id=execution_id,
        actor_id=iteration2_db.user_a)
    assert again["id"] == claimed["id"]
    kept = execute_one(
        "SELECT submitted_settings FROM irp_analysis WHERE id = :id",
        {"id": claimed["id"]}, connection="WORKBENCH")["submitted_settings"]
    assert json.loads(kept)["min_loss_threshold"] == 1.0
    assert json.loads(kept)["treaty_names"] == ["PR1", "PR2"]


# ── Retry a failed retrieval (spec 011 FR-007, T-11) ─────────────────────────────


def test_retry_revives_the_failed_row_and_the_worker_stores_the_hd_curve(
        iteration2_db, fake_irp):
    """The live case: an HD analysis whose first retrieval failed on the missing
    2,000-year point (research R3a). Retry resets that rwb_job row and the
    unchanged worker stores the extract with ``null`` at 2,000."""
    from app.services import analysis_service

    analysis_id = _seed_finished_analysis(
        irp_id="9001", settings={"engineType": "HD", "engineVersion": "HDv2.1"})
    hd_periods = [10000.0, 5000.0, 1000.0, 500.0, 250.0, 200.0,
                  100.0, 50.0, 25.0, 10.0, 5.0, 2.0]
    ep = ep_elements(analysis_id=9001, perspective_code="GR",
                     exposure_resource_id=555, base=1.0)
    for element in ep:
        element["value"] = {"returnPeriods": hd_periods,
                            "positionValues": list(hd_periods)}
    fake_irp.set_analysis_results(
        analysis_id=9001, perspective_code="GR", ep=ep,
        stats=stats_rows(analysis_id=9001, perspective_code="GR",
                         exposure_resource_id=555, pure_premium=38270.59,
                         total_std_dev=2645726.19))
    failed_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, attempt_count, error_detail) VALUES (:id, 'irp_analysis', "
        ":rid, 'edm', (SELECT edm_id FROM irp_analysis WHERE id = :rid), "
        "'irp_analysis', :rid, "
        "'retrieve_analysis_results', 'failed', 1, '2000.0')",
        {"id": failed_id, "rid": analysis_id}, connection="WORKBENCH")

    job_id = analysis_service.retry_results_retrieval(
        analysis_id=analysis_id, actor_id=iteration2_db.user_a)
    assert job_id == failed_id
    assert analysis_jobs.run_one(rwb_job_id=job_id,
                                 rwb_job_type="retrieve_analysis_results",
                                 worker_id="w1") is True

    job = execute_one(
        "SELECT status_code, attempt_count, error_detail FROM rwb_job "
        "WHERE id = :id", {"id": job_id}, connection="WORKBENCH")
    assert job["status_code"] == "succeeded"
    assert job["attempt_count"] == 2
    assert job["error_detail"] is None
    gr = _stored_extract(analysis_id)["perspectives"]["GR"]
    assert gr["oep"]["2000"] is None and gr["aep"]["2000"] is None
    assert gr["oep"]["1000"] == 1000.0
    assert len([v for v in gr["oep"].values() if v is not None]) == 10
    assert [j["id"] for j in _retrieval_jobs_for(analysis_id)] == [job_id]


# ── finalize_analysis: the resolved run details (spec 015, T-07/FR-014) ─────────

def _submitted_own_analysis(iteration2_db) -> tuple[str, dict]:
    """One submitted own analysis, ready for ``finalize_analysis``."""
    seed_currency()
    edm_id = seed_edm("EDM One")
    _run_execution(edm_id=edm_id, portfolio_id=seed_portfolio(edm_id),
                   template_ids=[seed_template()], actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    return edm_id, _analyses_for(edm_id)[0]


def _stored_settings(analysis_id: str) -> dict | None:
    row = execute_one(
        "SELECT settings_metadata FROM irp_analysis WHERE id = :id",
        {"id": analysis_id}, connection="WORKBENCH")
    return json.loads(row["settings_metadata"]) if row["settings_metadata"] else None


def _finalize_capture(iteration2_db, fake_irp, name: str, *,
                      fan_out: int = 1, treaties=None) -> dict | None:
    """Finalize one own analysis seeded from capture ``name``; return the
    ``settings_metadata`` the worker stored."""
    edm_id, analysis = _submitted_own_analysis(iteration2_db)
    described = captured_run(name, fan_out=fan_out)
    if treaties is not None:
        described = replace(described, treaties=tuple(treaties))
    fake_irp.add_analysis(analysis_id="9001", source_rdm_name="-",
                          exposure_name="EDM One", metadata=detail(name),
                          run_details=described)
    _run_finalize({"analysis_id": analysis["id"], "rm_analysis_id": "9001"}, edm_id)
    return _stored_settings(analysis["id"])


def test_finalize_writes_the_resolved_partition_and_treaties(
        iteration2_db, fake_irp):
    stored = _finalize_capture(iteration2_db, fake_irp, "own_dlm", fan_out=23)

    assert stored["engineType"] == "DLM"          # RM's own keys untouched
    resolved = stored["resolved"]
    assert resolved["partitions"] == [{
        "region_code": "NA", "peril_code": "WS", "framework": "ELT",
        "event_rate_scheme": {"id": 739,
                              "name": "RMS 2025 Stochastic Event Rates"},
        "simulation_set": None}]
    assert [t["number"] for t in resolved["treaties"]] == ["PR1", "PR2"]
    assert resolved["treaties"][0] == {
        "id": 33833, "number": "PR1", "name": "PR1", "currency": "USD",
        "occurrence_limit": 1000000.0, "risk_limit": 250000.0,
        "attachment_point": 250000.0, "retention_amount": 0.0}
    assert resolved["captured_at"].endswith("Z")


def test_finalize_run_details_failure_leaves_resolved_absent_and_ready(
        iteration2_db, fake_irp):
    edm_id, analysis = _submitted_own_analysis(iteration2_db)
    fake_irp.add_analysis(analysis_id="9001", source_rdm_name="-",
                          exposure_name="EDM One",
                          metadata=detail("own_dlm"),
                          run_details=captured_run("own_dlm"))
    fake_irp.raise_on_describe_run = {"9001"}

    _run_finalize({"analysis_id": analysis["id"], "rm_analysis_id": "9001"}, edm_id)

    stored = _stored_settings(analysis["id"])
    assert stored["engineType"] == "DLM"      # the metadata write stands
    assert "resolved" not in stored
    row = execute_one("SELECT status_code FROM irp_analysis WHERE id = :id",
                      {"id": analysis["id"]}, connection="WORKBENCH")
    assert row["status_code"] == "ready"
    assert len(_retrieval_jobs_for(analysis["id"])) == 1


def test_finalize_on_an_hd_analysis_writes_the_plt_partition(
        iteration2_db, fake_irp):
    stored = _finalize_capture(iteration2_db, fake_irp, "own_hd")

    assert stored["resolved"]["partitions"] == [{
        "region_code": "NZ", "peril_code": "EQ", "framework": "PLT",
        "event_rate_scheme": None,
        "simulation_set": {"id": 12, "name": "RMS 2020 Time-Dependent Rates",
                           "periods": 1978459}}]


# ── spec 015 story 3: a group's partitions come from its own detail (T-03) ─────

def _partitions_of(name: str) -> list[dict] | None:
    return irp_gateway.group_partitions(detail(name))


def test_a_risk_modeler_made_mixed_group_lists_every_region_and_peril():
    # 5684003: the simulationSets property, one value per region and peril,
    # sorted by region code then peril code (P-06).
    partitions = _partitions_of("group_mixed_rm_made")

    assert [(p["region_code"], p["peril_code"], p["framework"])
            for p in partitions] == [("JP", "WS", "PLT"), ("NA", "EQ", "ELT"),
                                     ("NA", "WS", "ELT")]
    typhoon, earthquake, hurricane = partitions
    assert typhoon["event_rate_scheme"] is None
    assert typhoon["simulation_set"] == {
        "id": 15, "name": "RMS V2.0 Stochastic Event Rates - Typhoon Events Only",
        "periods": 50000}
    assert earthquake["event_rate_scheme"] == {
        "id": 163, "name": "RMS 17.0 NA   Stochastic Event Rates"}
    assert earthquake["simulation_set"]["id"] == 87
    assert hurricane["event_rate_scheme"]["id"] == 738
    assert hurricane["simulation_set"]["id"] == 146


def test_an_elt_group_lists_schemes_with_no_simulation_set():
    partitions = _partitions_of("group_elt_workbench_made")

    assert [(p["peril_code"], p["event_rate_scheme"]["id"], p["simulation_set"])
            for p in partitions] == [("EQ", 163, None), ("WS", 739, None)]


def test_a_plt_group_lists_one_simulation_set():
    [partition] = _partitions_of("group_plt_workbench_made")

    assert partition["event_rate_scheme"] is None
    assert partition["simulation_set"]["id"] == 14
    assert partition["simulation_set"]["periods"] == 50000


def test_a_broker_group_reads_as_a_group_whatever_is_group_says():
    # 5723350 arrives with isGroup false and groupType INGP (research finding).
    assert detail("broker_group_ingp")["isGroup"] is False
    [partition] = _partitions_of("broker_group_ingp")

    assert partition["event_rate_scheme"] == {
        "id": 578, "name": "RMS 2023 Stochastic Event Rates"}


def test_an_own_analysis_detail_carries_no_group_property():
    assert _partitions_of("own_dlm") is None
    assert _partitions_of("own_hd") is None


def _finalize_group(iteration2_db, fake_irp, name: str, *,
                    treaties=None, metadata=None) -> dict | None:
    """Finalize one group seeded from capture ``name``; return its stored
    ``settings_metadata``."""
    submission = seed_submission("Sub One")
    group_id = seed_group(submission, "CRE_Sub One_Group", status="pending",
                          irp_id=None)
    described = captured_run(name)
    if treaties is not None:
        described = replace(described, treaties=tuple(treaties))
    fake_irp.add_analysis(analysis_id="9500", source_rdm_name="-",
                          exposure_name="", is_group=True,
                          metadata=metadata if metadata is not None else detail(name),
                          run_details=described)
    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, input_data) VALUES (:id, 'irp_job', :rid, 'submission', "
        ":sub, 'irp_analysis', :aid, 'finalize_analysis', 'pending', :input)",
        {"id": job_id, "rid": str(uuid.uuid4()), "sub": submission,
         "aid": group_id,
         "input": json.dumps({"analysis_id": group_id,
                              "rm_analysis_id": "9500"})},
        connection="WORKBENCH")
    analysis_jobs.run_one(rwb_job_id=job_id, rwb_job_type="finalize_analysis",
                          worker_id="w1")
    return _stored_settings(group_id)


def test_finalize_on_a_group_writes_the_detail_partitions_and_its_treaties(
        iteration2_db, fake_irp):
    # The group's treaty search returns its members' rows, so the same treaty
    # id can come back once per member (P-07).
    described = captured_run("group_mixed_rm_made")
    stored = _finalize_group(iteration2_db, fake_irp, "group_mixed_rm_made",
                             treaties=described.treaties + described.treaties)

    resolved = stored["resolved"]
    assert [(p["region_code"], p["peril_code"]) for p in resolved["partitions"]] == [
        ("JP", "WS"), ("NA", "EQ"), ("NA", "WS")]
    # the detail's schemes, not the region facts the describe call returned:
    # its NA · WS row reports 739, the group grouped on 738
    assert resolved["partitions"][2]["event_rate_scheme"]["id"] == 738
    assert [t["number"] for t in resolved["treaties"]] == ["PR1", "PR2", "QS_JP"]


def test_a_group_whose_treaty_read_failed_keeps_its_partitions(
        iteration2_db, fake_irp):
    fake_irp.raise_on_describe_run = {"9500"}
    stored = _finalize_group(iteration2_db, fake_irp, "group_mixed_rm_made")

    resolved = stored["resolved"]
    assert len(resolved["partitions"]) == 3
    assert "treaties" not in resolved
    group = execute_one(
        "SELECT status_code FROM irp_analysis WHERE is_group = 1",
        {}, connection="WORKBENCH")
    assert group["status_code"] == "ready"


def test_a_group_detail_missing_a_region_code_still_reaches_ready(
        iteration2_db, fake_irp):
    # FR-014: a malformed detail blanks ``resolved`` on that analysis; it never
    # fails the finished run. The partition sort is what raises on a None code.
    malformed = detail("group_mixed_rm_made")
    prop, = [p for p in malformed["additionalProperties"]
             if p["key"] == "simulationSets"]
    prop["properties"][0]["value"].pop("regionCode")
    stored = _finalize_group(iteration2_db, fake_irp, "group_mixed_rm_made",
                             metadata=malformed)

    assert "resolved" not in stored
    group = execute_one(
        "SELECT status_code FROM irp_analysis WHERE is_group = 1",
        {}, connection="WORKBENCH")
    assert group["status_code"] == "ready"


# ── spec 015 story 4: the row records the treaties (T-05/FR-010/FR-014) ────────

def test_submit_records_the_selected_treaty_names_on_the_plan_item(
        iteration2_db, fake_irp):
    edm_id = seed_edm("EDM One")
    portfolio = {"id": seed_portfolio(edm_id), "name": "Portfolio A"}
    item = _plan_item(template_id=seed_template())

    analysis_jobs._submit_one(
        edm_id=edm_id, edm_name="EDM One", execution_id=str(uuid.uuid4()),
        portfolio=portfolio, item=item, treaty_names=["PR1", "PR2"],
        submission_id=None, actor_id=iteration2_db.user_a)

    stored = execute_one(
        "SELECT submitted_settings FROM irp_analysis WHERE edm_id = :e",
        {"e": edm_id}, connection="WORKBENCH")["submitted_settings"]
    assert json.loads(stored) == {**item, "treaty_names": ["PR1", "PR2"]}
    view = analysis_service._submitted_view(stored)
    assert view.construction_occupancy == "Treat as unknown"
    assert view.currency == "USD"


def test_finalize_writes_every_applied_treaty_with_its_terms(
        iteration2_db, fake_irp):
    stored = _finalize_capture(iteration2_db, fake_irp, "own_hd")

    # sorted by treaty number, not by the order Risk Modeler returned (P-06)
    assert [t["number"] for t in stored["resolved"]["treaties"]] == ["PR1", "PR2"]
    assert stored["resolved"]["treaties"][1] == {
        "id": 33808, "number": "PR2", "name": "PR2", "currency": "USD",
        "occurrence_limit": 1000000.0, "risk_limit": 500000.0,
        "attachment_point": 500000.0, "retention_amount": 0.0}


def test_finalize_on_a_run_with_no_treaties_writes_an_empty_list(
        iteration2_db, fake_irp):
    stored = _finalize_capture(iteration2_db, fake_irp, "own_dlm",
                               fan_out=23, treaties=[])

    # the read succeeded and the analysis applied none (FR-012)
    assert stored["resolved"]["treaties"] == []
