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
from app.workers import analysis_jobs
from db import execute, execute_command, execute_one


def _seed_currency(scheme="RMS", vintage="RL25", effective_date="2025-05-28"):
    if not execute_one("SELECT 1 FROM irp_currency WHERE code = 'USD'",
                       {}, connection="WORKBENCH"):
        execute_command(
            "INSERT INTO irp_currency (id, code, name) VALUES "
            "(:id, 'USD', 'US Dollar')",
            {"id": str(uuid.uuid4())}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_currency_scheme (id, irp_id, code, name) "
        "VALUES (:id, :irp_id, :c, :c)",
        {"id": str(uuid.uuid4()), "irp_id": uuid.uuid4().int % 2_000_000_000,
         "c": scheme}, connection="WORKBENCH")
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
        "INSERT INTO irp_portfolio (id, edm_id, irp_id, name) "
        "VALUES (:id, :edm, :irp_id, :name)",
        {"id": portfolio_id, "edm": edm_id,
         "irp_id": str(uuid.uuid4().int % 2_000_000_000), "name": name},
        connection="WORKBENCH")
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
    assert rows[0]["name"] == "Portfolio A Template A"
    assert rows[0]["full_name"] == "Portfolio A Template A"
    assert rows[0]["status_code"] == "running"
    assert rows[0]["irp_portfolio_id"].lower() == portfolio_id

    irp_job = execute_one(
        "SELECT status, irp_analysis_id, request_params FROM irp_job "
        "WHERE irp_analysis_id = :a", {"a": rows[0]["id"]}, connection="WORKBENCH")
    assert irp_job["status"] == "QUEUED"
    assert json.loads(irp_job["request_params"])["job_name"] == "Portfolio A Template A"


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
    assert names == ["Portfolio A Shared Template",
                     "Portfolio A Shared Template - 1"]
    submits = {s["job_name"]: s["currency"] for s in fake_irp.analysis_submits}
    assert submits["Portfolio A Shared Template"]["scheme"] == "RMS"
    assert submits["Portfolio A Shared Template - 1"]["scheme"] == "DT"
    assert _rwb_job_of(execution_id)["status_code"] == "succeeded"


# ── per-item isolation + submission failure ──────────────────────────────────────

def test_one_item_failing_to_submit_never_stops_the_loop(iteration2_db, fake_irp):
    _seed_currency()
    edm_id = _seed_edm()
    p1 = _seed_portfolio(edm_id, "Portfolio A")
    p2 = _seed_portfolio(edm_id, "Portfolio B")
    template_id = _seed_template("Template A")
    fake_irp.raise_on_submit_analysis_for.add("Portfolio A Template A")

    execution_id = svc.request_execution(
        edm_id=edm_id, kind="template", portfolio_ids=[p1, p2], treaty_names=[],
        template_ids=[template_id], currency_code="USD", currency_scheme="RMS",
        currency_vintage="RL25", actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")

    job = _rwb_job_of(execution_id)
    assert job["status_code"] == "succeeded"  # not every item failed
    assert json.loads(job["output_data"]) == {"submitted": 1, "submission_failed": 1}

    rows = {r["name"]: r for r in _analyses_for(edm_id)}
    failed = rows["Portfolio A Template A"]
    assert failed["status_code"] == "pending"
    assert failed["failure_reason"] and "forced analysis submit failure" in failed[
        "failure_reason"]
    ok = rows["Portfolio B Template A"]
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
    fake_irp.raise_on_submit_analysis_for.add("Portfolio A Template A")

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
    assert rows[0]["id"].lower() == claimed["id"]
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
