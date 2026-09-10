"""SQL Server integration tests for the Iteration-2 irp_job/rwb_job schema (T017).

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers:
  - the extended migration builds the irp_job / irp_job_resource / rwb_job /
    rwb_job_heartbeat / irp_analysis tables + the six kind tables, with FKs, the
    rwb_job + irp_analysis UNIQUE keys, and the §13 kind seeds present;
  - the atomic claim (UPDATE ... WHERE status_code='pending') returns rowcount 1
    then 0 under contention;
  - the idempotent chained insert on UNIQUE(requestor_type, requestor_id,
    rwb_job_type) absorbs a duplicate exactly once;
  - the CR-04a cancel and completion guards, and the monitoring read's
    submission EXISTS + heartbeat staleness predicate, on the real driver.
"""

from __future__ import annotations

import uuid

import pytest

from db import execute, execute_command, execute_scalar
from app.services.rwb_job_service import (cancel_rwb_job, claim_rwb_job,
                                          complete_rwb_job, enqueue_rwb_job,
                                          list_rwb_jobs_for_monitoring)
from app.workers.runtime import upsert_heartbeat

pytestmark = pytest.mark.sqlserver

JOB_TABLES = [
    "irp_job_type_kind", "irp_job_resource_type_kind", "rwb_job_type_kind",
    "rwb_job_requestor_type_kind", "rwb_job_status_kind", "irp_analysis_status_kind",
    "irp_job", "irp_job_resource", "rwb_job", "rwb_job_heartbeat", "irp_analysis",
]


def _table_exists(name: str) -> int:
    return execute_scalar(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_NAME = :n AND TABLE_SCHEMA = 'dbo'",
        {"n": name}, connection="WORKBENCH",
    )


# ── structure / seeds ─────────────────────────────────────────────────────────

class TestJobTablesMigration:
    @pytest.mark.parametrize("name", JOB_TABLES)
    def test_job_table_exists(self, name):
        assert _table_exists(name) == 1

    def test_irp_job_has_analysis_execution_columns(self):
        # spec 010: irp_analysis_id/request_params added by ALTER once
        # irp_analysis exists; irp_portfolio_id is inline (data-model §2).
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_job'", {}, connection="WORKBENCH")}
        assert {"irp_edm_id", "irp_rdm_id", "requested_from_submission_id",
                "status", "irp_portfolio_id", "irp_analysis_id",
                "request_params"} <= cols
        assert "package_id" not in cols

    def test_irp_job_irp_analysis_id_index_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE name = 'ix_irp_job_irp_analysis_id' "
            "AND object_id = OBJECT_ID('dbo.irp_job')",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_no_scope_column_on_job_tables(self):
        for table in ("irp_job", "rwb_job"):
            cols = {r["COLUMN_NAME"] for r in execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :t", {"t": table}, connection="WORKBENCH")}
            assert "customer_id" not in cols  # Article 6

    def test_irp_job_type_kind_seeds_have_no_entity_deletes(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM irp_job_type_kind", {}, connection="WORKBENCH")}
        assert {"import_edm", "import_rdm"} <= codes
        assert "delete_edm" not in codes
        assert "delete_rdm" not in codes

    def test_rwb_job_type_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_type_kind", {}, connection="WORKBENCH")}
        assert {"upload_edm", "upload_rdm", "backfill_rdm_analyses",
                "notify_analyst", "execute_analysis_batch",
                "finalize_analysis", "submit_results_export",
                "stage_results_export", "load_results_export"} <= codes
        assert {"delete_edm", "delete_rdm", "download_export_file",
                "push_results_to_loss_repo"}.isdisjoint(codes)

    def test_rwb_job_context_type_kind_has_result_export(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_context_type_kind", {}, connection="WORKBENCH")}
        assert "result_export" in codes

    def test_irp_job_export_id_column_and_index_present(self):
        col = execute_scalar(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_job' AND COLUMN_NAME = 'export_id' "
            "AND DATA_TYPE = 'uniqueidentifier' AND IS_NULLABLE = 'YES'",
            {}, connection="WORKBENCH")
        assert col == 1
        idx = execute_scalar(
            "SELECT COUNT(*) FROM sys.indexes WHERE name = 'ix_irp_job_export_id' "
            "AND object_id = OBJECT_ID('dbo.irp_job')", {}, connection="WORKBENCH")
        assert idx == 1

    def test_irp_analysis_status_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM irp_analysis_status_kind", {}, connection="WORKBENCH")}
        # 'pending' is the only in-flight value; progress while an analysis or
        # a group runs is irp_job.status (spec 010 data-model §6, spec 012 data-model).
        assert codes == {"pending", "ready", "error"}

    def test_irp_analysis_filtered_unique_indexes_present(self):
        # spec 010: uq_irp_analysis_rdm_irp is now a FILTERED unique index (not a
        # key constraint) — a plain UNIQUE would treat own-analysis rows' shared
        # NULL rdm_id/irp_id as colliding (data-model §1). Backfill idempotency
        # (§6a), the rerun-collision index on (edm_id, name) (T-05), and the
        # worker's resume key (execution_id, irp_portfolio_id, execution_item_no).
        for name in ("uq_irp_analysis_rdm_irp", "uq_irp_analysis_live_edm_name",
                     "uq_irp_analysis_execution_item"):
            n = execute_scalar(
                "SELECT COUNT(*) FROM sys.indexes "
                "WHERE name = :n AND object_id = OBJECT_ID('dbo.irp_analysis') "
                "AND is_unique = 1",
                {"n": name}, connection="WORKBENCH")
            assert n == 1, name

    def test_irp_analysis_full_name_holds_an_untruncated_name(self):
        # CRE_ + a 256-char portfolio name + _ + a 200-char template name; only
        # the submitted `name` is truncated (to Risk Modeler's 64).
        n = execute_scalar(
            "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_analysis' AND COLUMN_NAME = 'full_name'",
            {}, connection="WORKBENCH")
        assert n == 512

    def test_irp_analysis_origin_check_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.check_constraints "
            "WHERE name = 'ck_irp_analysis_origin' "
            "AND parent_object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert n == 1  # edm_id IS NOT NULL OR rdm_id IS NOT NULL (data-model §1)

    def test_irp_analysis_foreign_keys_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        # rdm_id, edm_id, status_code, irp_portfolio_id, analysis_template_id
        # (+ user FKs)
        assert n >= 5

    def test_irp_analysis_no_scope_column(self):
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_analysis'", {}, connection="WORKBENCH")}
        assert "customer_id" not in cols  # Article 6
        assert "package_id" not in cols
        assert {"rdm_id", "edm_id", "irp_id", "irp_app_analysis_id",
                "source_rdm_name", "deleted_at",
                "full_name", "irp_portfolio_id", "analysis_template_id",
                "execution_id", "execution_item_no", "failure_reason"} <= cols

    def test_irp_analysis_own_row_columns_nullable(self):
        # spec 010: rdm_id/source_rdm_name/irp_id must accept NULL for own-executed
        # rows (data-model §1) — CHECK ck_irp_analysis_origin is the only guard.
        cols = {r["COLUMN_NAME"]: r["IS_NULLABLE"] for r in execute(
            "SELECT COLUMN_NAME, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_analysis'", {}, connection="WORKBENCH")}
        assert cols["rdm_id"] == "YES"
        assert cols["source_rdm_name"] == "YES"
        assert cols["irp_id"] == "YES"

    def test_rwb_job_requestor_and_status_seeds(self):
        req = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_requestor_type_kind", {}, connection="WORKBENCH")}
        assert req == {"irp_job", "analyst_request", "rwb_job",
                       "breakout_group", "irp_analysis"}
        st = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_status_kind", {}, connection="WORKBENCH")}
        assert st == {"pending", "running", "succeeded", "failed", "cancelled"}

    def test_rwb_job_unique_constraint_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.key_constraints "
            "WHERE name = 'uq_rwb_job_requestor_type' "
            "AND parent_object_id = OBJECT_ID('dbo.rwb_job')",
            {}, connection="WORKBENCH")
        assert n == 1

    def test_rwb_job_foreign_keys_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.rwb_job')",
            {}, connection="WORKBENCH")
        assert n >= 3  # requestor_type, rwb_job_type, status_code (+ user FKs)

    def test_irp_job_foreign_keys_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.irp_job')",
            {}, connection="WORKBENCH")
        # submission, irp_edm, irp_rdm, irp_job_type, irp_portfolio_id,
        # irp_analysis_id (+ user FKs)
        assert n >= 6


# ── behavioral: atomic claim + idempotent chained insert ──────────────────────

@pytest.fixture()
def cleanup_rwb():
    ids: list[str] = []
    yield ids
    for rid in ids:
        execute_command("DELETE FROM rwb_job_heartbeat WHERE rwb_job_id IN "
                        "(SELECT id FROM rwb_job WHERE requestor_id = :r)",
                        {"r": rid}, connection="WORKBENCH")
        execute_command("DELETE FROM rwb_job WHERE requestor_id = :r",
                        {"r": rid}, connection="WORKBENCH")


class TestQueueBehavior:
    def test_atomic_claim_rowcount_one_then_zero(self, cleanup_rwb):
        rid = str(uuid.uuid4())
        cleanup_rwb.append(rid)
        job_id = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                                 rwb_job_type="upload_edm",
                                 link_type="not_applicable", link_id=None,
                                 context_type=None, context_id=None)
        assert job_id is not None
        assert claim_rwb_job(rwb_job_id=job_id, worker_id="w1") is True
        assert claim_rwb_job(rwb_job_id=job_id, worker_id="w2") is False

    def test_idempotent_chained_insert_absorbs_duplicate_once(self, cleanup_rwb):
        rid = str(uuid.uuid4())
        cleanup_rwb.append(rid)
        first = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                rwb_job_type="upload_rdm",
                                link_type="not_applicable", link_id=None,
                                context_type=None, context_id=None)
        dup = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                              rwb_job_type="upload_rdm",
                              link_type="not_applicable", link_id=None,
                              context_type=None, context_id=None)
        assert first is not None
        assert dup is None
        n = execute_scalar(
            "SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r "
            "AND rwb_job_type = 'upload_rdm'", {"r": rid}, connection="WORKBENCH")
        assert n == 1


# ── behavioral: the CR-04a cancel/completion guards and the monitoring read ───

def _queued(cleanup_rwb, rwb_job_type: str = "upload_edm") -> tuple[str, str]:
    rid = str(uuid.uuid4())
    cleanup_rwb.append(rid)
    job_id = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                             rwb_job_type=rwb_job_type,
                             link_type="not_applicable", link_id=None,
                             context_type=None, context_id=None)
    assert job_id is not None
    return job_id, rid


class TestCancelGuard:
    def test_cancel_matches_pending_and_failed_but_not_succeeded(self, cleanup_rwb):
        pending_id, _ = _queued(cleanup_rwb)
        assert cancel_rwb_job(rwb_job_id=pending_id) is True

        failed_id, _ = _queued(cleanup_rwb, "upload_rdm")
        claim_rwb_job(rwb_job_id=failed_id, worker_id="w1")
        complete_rwb_job(rwb_job_id=failed_id, status="failed", error_detail="boom")
        assert cancel_rwb_job(rwb_job_id=failed_id) is True

        done_id, _ = _queued(cleanup_rwb, "notify_analyst")
        claim_rwb_job(rwb_job_id=done_id, worker_id="w1")
        complete_rwb_job(rwb_job_id=done_id, status="succeeded")
        assert cancel_rwb_job(rwb_job_id=done_id) is False

    def test_cancel_matches_a_dead_running_row_but_not_a_live_one(self, cleanup_rwb):
        # The correlated heartbeat subquery inside the UPDATE is the part that
        # has to behave the same on SQL Server as on the SQLite unit tier.
        live_id, _ = _queued(cleanup_rwb)
        claim_rwb_job(rwb_job_id=live_id, worker_id="w1")
        upsert_heartbeat(rwb_job_id=live_id, worker_id="w1")
        assert cancel_rwb_job(rwb_job_id=live_id) is False

        dead_id, _ = _queued(cleanup_rwb, "upload_rdm")
        claim_rwb_job(rwb_job_id=dead_id, worker_id="w1")  # never heartbeated
        assert cancel_rwb_job(rwb_job_id=dead_id) is True


class TestCompletionGuard:
    def test_completion_does_not_revive_a_cancelled_row(self, cleanup_rwb):
        job_id, _ = _queued(cleanup_rwb)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
        assert cancel_rwb_job(rwb_job_id=job_id) is True

        complete_rwb_job(rwb_job_id=job_id, status="succeeded", output_data={"ok": 1})

        status = execute_scalar("SELECT status_code FROM rwb_job WHERE id = :id",
                                {"id": job_id}, connection="WORKBENCH")
        assert status == "cancelled"


class TestMonitoringRead:
    def test_unlinked_job_survives_the_submission_exists_predicate(self, cleanup_rwb):
        # The three-leg EXISTS ... UNION ALL over submission_edm/submission_rdm/
        # submission has to parse and run on SQL Server, and a not_applicable
        # link has to fall through it when no submission filter is set.
        job_id, _ = _queued(cleanup_rwb)

        rows = list_rwb_jobs_for_monitoring(rwb_job_ids=[job_id])

        # SQL Server hands UNIQUEIDENTIFIER back uppercase.
        assert [str(r["id"]).lower() for r in rows] == [job_id.lower()]
        assert rows[0]["is_dead"] == 0

    def test_owner_filter_excludes_a_job_with_no_submission(self, cleanup_rwb):
        job_id, _ = _queued(cleanup_rwb)

        rows = list_rwb_jobs_for_monitoring(rwb_job_ids=[job_id],
                                            owner_ids=[str(uuid.uuid4())])

        assert rows == []

    def test_is_dead_reads_true_for_a_running_row_with_no_heartbeat(self, cleanup_rwb):
        job_id, _ = _queued(cleanup_rwb)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")

        rows = list_rwb_jobs_for_monitoring(rwb_job_ids=[job_id])
        assert rows[0]["is_dead"] == 1

        upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
        rows = list_rwb_jobs_for_monitoring(rwb_job_ids=[job_id])
        assert rows[0]["is_dead"] == 0

    def test_dead_status_filter_selects_the_same_rows(self, cleanup_rwb):
        job_id, _ = _queued(cleanup_rwb)
        claim_rwb_job(rwb_job_id=job_id, worker_id="w1")  # never heartbeated

        ids = {str(r["id"]).lower()
               for r in list_rwb_jobs_for_monitoring(status_codes=["dead"])}

        assert job_id.lower() in ids
