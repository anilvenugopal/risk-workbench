"""Unit tests for the poller's ``analysis`` job type (spec 010, T023).

Covers the terminal handler (FINISHED enqueues ``backfill_analysis_detail``,
FAILED/CANCELLED extract a failure reason) and the ``_submission_retry`` batch
(backoff eligibility, in-place update on success, exhaustion → ``error``).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.poller import run as poller
from app.services import analysis_execution_service as svc
from app.workers import analysis_jobs
from db import execute, execute_command, execute_one


def _seed_currency():
    execute_command(
        "INSERT INTO irp_currency (id, code, name) VALUES (:id, 'USD', 'US Dollar')",
        {"id": str(uuid.uuid4())}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_currency_scheme (id, code, name) VALUES (:id, 'RMS', 'RMS')",
        {"id": str(uuid.uuid4())}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_currency_scheme_vintage (id, vintage, "
        "currency_scheme_code, effective_date) VALUES (:id, 'RL25', 'RMS', "
        "'2025-05-28')", {"id": str(uuid.uuid4())}, connection="WORKBENCH")


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


def _submitted_analysis(iteration2_db, fake_irp, edm_name="EDM One") -> dict:
    """Drive one analysis through a real submit so its irp_job/irp_analysis rows
    are realistic, and return the analysis row."""
    _seed_currency()
    edm_id = _seed_edm(edm_name)
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    svc.request_execution(
        edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
        treaty_names=[], template_ids=[template_id],
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = execute_one(
        "SELECT id, name FROM irp_analysis WHERE edm_id = :e",
        {"e": edm_id}, connection="WORKBENCH")
    irp_job = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_analysis_id = :a",
        {"a": analysis["id"]}, connection="WORKBENCH")
    return {"edm_id": edm_id, "edm_name": edm_name, "id": analysis["id"],
           "name": analysis["name"], "irp_id": irp_job["irp_id"]}


# ── terminal handler ──────────────────────────────────────────────────────────────

def test_finished_enqueues_backfill_analysis_detail(iteration2_db, fake_irp):
    a = _submitted_analysis(iteration2_db, fake_irp)
    fake_irp.finish(a["irp_id"], result={
        "tasks": [{"output": {"log": {"analysisId": "9001"}}}]})

    poller.poll_once()

    head = execute_one(
        "SELECT input_data FROM rwb_job WHERE rwb_job_type = 'backfill_analysis_detail'",
        {}, connection="WORKBENCH")
    assert head is not None
    input_data = json.loads(head["input_data"])
    assert input_data["analysis_id"] == a["id"]
    assert input_data["rm_analysis_id"] == "9001"
    status = execute_one("SELECT status_code FROM irp_analysis WHERE id = :id",
                         {"id": a["id"]}, connection="WORKBENCH")
    assert status["status_code"] == "running"  # untouched — backfill flips it to ready


def test_finished_without_analysis_id_passes_none(iteration2_db, fake_irp):
    a = _submitted_analysis(iteration2_db, fake_irp)
    fake_irp.finish(a["irp_id"])  # completion body with no tasks[].output.log

    poller.poll_once()

    head = execute_one(
        "SELECT input_data FROM rwb_job WHERE rwb_job_type = 'backfill_analysis_detail'",
        {}, connection="WORKBENCH")
    assert head is not None
    assert json.loads(head["input_data"])["rm_analysis_id"] is None


def test_failed_extracts_reason_and_flips_analysis_to_error(iteration2_db, fake_irp):
    a = _submitted_analysis(iteration2_db, fake_irp)
    fake_irp.fail(a["irp_id"], result={"errorMessage": "model version unsupported"})

    poller.poll_once()

    row = execute_one(
        "SELECT status_code, failure_reason FROM irp_analysis WHERE id = :id",
        {"id": a["id"]}, connection="WORKBENCH")
    assert row["status_code"] == "error"
    assert row["failure_reason"] == "model version unsupported"
    assert execute("SELECT 1 FROM rwb_job WHERE rwb_job_type = 'backfill_analysis_detail'",
                  {}, connection="WORKBENCH") == []


def test_cancelled_is_treated_as_a_failure(iteration2_db, fake_irp):
    a = _submitted_analysis(iteration2_db, fake_irp)
    fake_irp.jobs[a["irp_id"]] = "CANCELLED"
    fake_irp.results[a["irp_id"]] = {}

    poller.poll_once()

    row = execute_one("SELECT status_code, failure_reason FROM irp_analysis "
                      "WHERE id = :id", {"id": a["id"]}, connection="WORKBENCH")
    assert row["status_code"] == "error"
    assert row["failure_reason"]  # fallback summary, never blank


def test_failure_reason_prefers_first_task_error_message():
    body = {
        "status": "FAILED",
        "tasks": [
            {"taskId": "1", "output": {"summary": "", "errors": [
                {"message": "ENGINE-400:Exposure failed to process. "
                            "No valid locations for the peril region."}]}},
            {"taskId": "2", "output": {"errors": [
                {"message": "We encountered an error trying to run the job."}]}},
        ],
    }
    assert poller._analysis_failure_reason(body) == (
        "ENGINE-400:Exposure failed to process. "
        "No valid locations for the peril region.")


def test_failure_reason_skips_tasks_without_errors_then_falls_back():
    body = {"status": "FAILED",
            "tasks": [{"taskId": "1", "output": {"errors": []}},
                      {"taskId": "2"}]}
    assert poller._analysis_failure_reason(body) == "Risk Modeler status: FAILED"


def test_failed_with_nested_task_errors_stores_the_engine_message(
        iteration2_db, fake_irp):
    a = _submitted_analysis(iteration2_db, fake_irp)
    fake_irp.fail(a["irp_id"], result={
        "status": "FAILED",
        "tasks": [
            {"taskId": "1", "output": {"errors": [
                {"message": "ENGINE-400:Exposure failed to process."}]}},
            {"taskId": "2", "output": {"errors": [
                {"message": "Generic downstream message."}]}},
        ],
    })

    poller.poll_once()

    row = execute_one(
        "SELECT status_code, failure_reason FROM irp_analysis WHERE id = :id",
        {"id": a["id"]}, connection="WORKBENCH")
    assert row["status_code"] == "error"
    assert row["failure_reason"] == "ENGINE-400:Exposure failed to process."


# ── submission_retry batch (T-09) ────────────────────────────────────────────────

def _submission_failed_row(iteration2_db, fake_irp) -> dict:
    """One analysis whose submit was forced to fail — a SUBMISSION FAILED irp_job
    with request_params ready for the retry batch."""
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    fake_irp.raise_on_submit_analysis_for.add("CRE_Portfolio A_Template A")
    svc.request_execution(
        edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
        treaty_names=[], template_ids=[template_id],
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        actor_id=iteration2_db.user_a)
    analysis_jobs.run_pending(worker_id="w1")
    analysis = execute_one("SELECT id, name FROM irp_analysis WHERE edm_id = :e",
                          {"e": edm_id}, connection="WORKBENCH")
    job = execute_one(
        "SELECT id, submission_attempt_count FROM irp_job WHERE irp_analysis_id = :a",
        {"a": analysis["id"]}, connection="WORKBENCH")
    return {"edm_id": edm_id, "analysis_id": analysis["id"], "name": analysis["name"],
           "job_id": job["id"],
           "submission_attempt_count": job["submission_attempt_count"]}


def _age_completed_at(job_id: str, seconds_ago: int) -> None:
    then = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=seconds_ago)
    execute_command(
        "UPDATE irp_job SET completed_at = :t WHERE id = :id",
        {"t": then, "id": job_id}, connection="WORKBENCH")


def test_retry_not_yet_eligible_within_the_backoff_window(iteration2_db, fake_irp):
    row = _submission_failed_row(iteration2_db, fake_irp)
    _age_completed_at(row["job_id"], seconds_ago=1)  # far under the base backoff

    poller._submission_retry()

    job = execute_one("SELECT status, submission_attempt_count FROM irp_job "
                      "WHERE id = :id", {"id": row["job_id"]}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED"
    assert job["submission_attempt_count"] == 1
    assert len(fake_irp.analysis_submits) == 1  # no resubmit attempted


def test_retry_success_updates_the_row_in_place(iteration2_db, fake_irp):
    row = _submission_failed_row(iteration2_db, fake_irp)
    _age_completed_at(row["job_id"], seconds_ago=(
        settings.irp_submission_retry_base_secs
        * 2 ** row["submission_attempt_count"] + 5))
    fake_irp.raise_on_submit_analysis_for.discard("CRE_Portfolio A_Template A")

    poller._submission_retry()

    job = execute_one(
        "SELECT id, status, irp_id, submission_attempt_count FROM irp_job "
        "WHERE irp_analysis_id = :a", {"a": row["analysis_id"]}, connection="WORKBENCH")
    assert job["id"] == row["job_id"]  # updated in place — no new irp_job row
    assert job["status"] == "QUEUED"
    assert job["irp_id"] is not None
    assert job["submission_attempt_count"] == 2
    total_jobs = execute("SELECT id FROM irp_job WHERE irp_analysis_id = :a",
                        {"a": row["analysis_id"]}, connection="WORKBENCH")
    assert len(total_jobs) == 1
    analysis = execute_one("SELECT status_code, failure_reason FROM irp_analysis "
                           "WHERE id = :id", {"id": row["analysis_id"]},
                           connection="WORKBENCH")
    assert analysis["status_code"] == "running"
    assert analysis["failure_reason"] is None


def test_retry_failure_increments_attempts_without_reaching_max(iteration2_db, fake_irp):
    row = _submission_failed_row(iteration2_db, fake_irp)
    _age_completed_at(row["job_id"], seconds_ago=(
        settings.irp_submission_retry_base_secs
        * 2 ** row["submission_attempt_count"] + 5))
    # still forced to fail

    poller._submission_retry()

    job = execute_one("SELECT status, submission_attempt_count FROM irp_job "
                      "WHERE id = :id", {"id": row["job_id"]}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED"
    assert job["submission_attempt_count"] == 2
    analysis = execute_one("SELECT status_code FROM irp_analysis WHERE id = :id",
                           {"id": row["analysis_id"]}, connection="WORKBENCH")
    assert analysis["status_code"] == "pending"  # not yet exhausted


def test_retry_exhaustion_flips_analysis_to_error(iteration2_db, fake_irp):
    row = _submission_failed_row(iteration2_db, fake_irp)
    # Pre-set the attempt count to one below the max so this pass exhausts it.
    execute_command(
        "UPDATE irp_job SET submission_attempt_count = :n WHERE id = :id",
        {"n": settings.irp_submission_max_retries - 1, "id": row["job_id"]},
        connection="WORKBENCH")
    _age_completed_at(row["job_id"], seconds_ago=settings.irp_submission_retry_base_secs
                      * 2 ** (settings.irp_submission_max_retries - 1) + 5)

    poller._submission_retry()

    job = execute_one("SELECT status, submission_attempt_count FROM irp_job "
                      "WHERE id = :id", {"id": row["job_id"]}, connection="WORKBENCH")
    assert job["status"] == "SUBMISSION FAILED"
    assert job["submission_attempt_count"] == settings.irp_submission_max_retries
    analysis = execute_one("SELECT status_code FROM irp_analysis WHERE id = :id",
                           {"id": row["analysis_id"]}, connection="WORKBENCH")
    assert analysis["status_code"] == "error"


def test_retry_skips_a_soft_deleted_analysis(iteration2_db, fake_irp):
    # A failed-to-submit row the analyst deleted (P-19) must never be
    # resubmitted by the retry batch.
    row = _submission_failed_row(iteration2_db, fake_irp)
    _age_completed_at(row["job_id"], seconds_ago=10_000_000)
    fake_irp.raise_on_submit_analysis_for.discard("CRE_Portfolio A_Template A")
    execute_command(
        "UPDATE irp_analysis SET deleted_at = :n WHERE id = :i",
        {"n": datetime.now(timezone.utc).replace(tzinfo=None),
         "i": row["analysis_id"]}, connection="WORKBENCH")

    poller._submission_retry()

    assert len(fake_irp.analysis_submits) == 1  # only the original attempt


def test_retry_ignores_rows_already_at_the_max(iteration2_db, fake_irp):
    row = _submission_failed_row(iteration2_db, fake_irp)
    execute_command(
        "UPDATE irp_job SET submission_attempt_count = :n WHERE id = :id",
        {"n": settings.irp_submission_max_retries, "id": row["job_id"]},
        connection="WORKBENCH")
    _age_completed_at(row["job_id"], seconds_ago=10_000_000)

    poller._submission_retry()

    assert len(fake_irp.analysis_submits) == 1  # never resubmitted
