"""SQL Server integration tests for the Iteration-2 irp_job/rwb_job schema (T017).

Run with: pytest tests/sqlserver --run-sqlserver  (requires live SQL Server)

Covers:
  - the extended migration builds the irp_job / irp_job_resource / rwb_job /
    rwb_job_heartbeat / irp_analysis tables + the six kind tables, with FKs, the
    rwb_job + irp_analysis UNIQUE keys, and the §13 kind seeds present;
  - the atomic claim (UPDATE ... WHERE status_code='pending') returns rowcount 1
    then 0 under contention;
  - the idempotent chained insert on UNIQUE(requestor_type, requestor_id,
    rwb_job_type) absorbs a duplicate exactly once.
"""

from __future__ import annotations

import uuid

import pytest

from db import execute, execute_command, execute_scalar
from app.services.rwb_job_service import claim_rwb_job, enqueue_rwb_job

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

    def test_irp_job_has_no_portfolio_fk_column(self):
        # irp_portfolio does not exist yet — irp_job is created WITHOUT it (§2 note).
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_job'", {}, connection="WORKBENCH")}
        assert "irp_portfolio_id" not in cols
        assert {"irp_edm_id", "irp_rdm_id", "package_id", "status"} <= cols

    def test_no_scope_column_on_job_tables(self):
        for table in ("irp_job", "rwb_job"):
            cols = {r["COLUMN_NAME"] for r in execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :t", {"t": table}, connection="WORKBENCH")}
            assert "customer_id" not in cols  # Article 6

    def test_irp_job_type_kind_seeds_have_no_delete_rdm(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM irp_job_type_kind", {}, connection="WORKBENCH")}
        assert {"import_edm", "import_rdm", "delete_edm"} <= codes
        assert "delete_rdm" not in codes  # RDM delete is synchronous (R6)

    def test_rwb_job_type_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_type_kind", {}, connection="WORKBENCH")}
        assert {"upload_edm", "upload_rdm", "backfill_rdm_analyses", "delete_rdm",
                "delete_edm", "notify_analyst"} <= codes  # backfill new this iter (D2)

    def test_irp_analysis_status_kind_seeds(self):
        codes = {r["code"] for r in execute(
            "SELECT code FROM irp_analysis_status_kind", {}, connection="WORKBENCH")}
        assert codes == {"pending", "running", "ready", "error"}  # D2, data-model §6

    def test_irp_analysis_unique_constraint_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.key_constraints "
            "WHERE name = 'uq_irp_analysis_pair' "
            "AND parent_object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert n == 1  # UNIQUE(rdm_id, edm_id, irp_id) — backfill idempotency (§6a)

    def test_irp_analysis_foreign_keys_present(self):
        n = execute_scalar(
            "SELECT COUNT(*) FROM sys.foreign_keys "
            "WHERE parent_object_id = OBJECT_ID('dbo.irp_analysis')",
            {}, connection="WORKBENCH")
        assert n >= 4  # rdm_id, edm_id, package_id, status_code (+ user FKs)

    def test_irp_analysis_no_scope_column(self):
        cols = {r["COLUMN_NAME"] for r in execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'irp_analysis'", {}, connection="WORKBENCH")}
        assert "customer_id" not in cols  # Article 6
        assert {"rdm_id", "edm_id", "irp_id", "source_rdm_name", "deleted_at"} <= cols

    def test_rwb_job_requestor_and_status_seeds(self):
        req = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_requestor_type_kind", {}, connection="WORKBENCH")}
        assert req == {"irp_job", "analyst_request", "rwb_job"}
        st = {r["code"] for r in execute(
            "SELECT code FROM rwb_job_status_kind", {}, connection="WORKBENCH")}
        assert st == {"pending", "running", "succeeded", "failed"}

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
        assert n >= 4  # package, irp_edm, irp_rdm, irp_job_type (+ user FKs)


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
                                 rwb_job_type="upload_edm")
        assert job_id is not None
        assert claim_rwb_job(rwb_job_id=job_id, worker_id="w1") is True
        assert claim_rwb_job(rwb_job_id=job_id, worker_id="w2") is False

    def test_idempotent_chained_insert_absorbs_duplicate_once(self, cleanup_rwb):
        rid = str(uuid.uuid4())
        cleanup_rwb.append(rid)
        first = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                rwb_job_type="upload_rdm")
        dup = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                              rwb_job_type="upload_rdm")
        assert first is not None
        assert dup is None
        n = execute_scalar(
            "SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r "
            "AND rwb_job_type = 'upload_rdm'", {"r": rid}, connection="WORKBENCH")
        assert n == 1
