"""The January bulk update script against a live SQL Server (spec 017 FR-023).

Runs ``infra/scripts/bulk_update_contract_status.sql`` with each test's rows
as the whole of ``dbo.CRMContractStatus`` in the LOSS database (the summary
counts every source row, so the table is emptied first, as
``test_loss_export_procedure.py`` empties the mirror tables), and the script's
SOURCE line pointed at that database. Every test creates its own analyst and
submission and deletes them afterwards.

Run with:  pytest tests/sqlserver --run-sqlserver   (requires live SQL Server)
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.services import submission_service as svc
from db import execute, execute_command, get_connection, get_engine

pytestmark = pytest.mark.sqlserver

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "bulk_update_contract_status.sql"
SOURCE_TABLE = "rwb_loss.dbo.CRMContractStatus"
BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / "db" / "bootstrap"


@pytest.fixture(scope="module", autouse=True)
def crm_status_table():
    """CI creates rwb_loss empty; the mirror script is idempotent."""
    from db.scripts import execute_script_file  # noqa: PLC0415 — trusted DDL, test only
    execute_script_file(BOOTSTRAP_DIR / "loss_dev_mirror.sql", connection="LOSS")


def _run(rows: list[tuple[str, str]], *, dry_run: bool) -> list[list[dict]]:
    """Make ``rows`` the source table's content and run the script against the
    LOSS database the tier points at; one list of dicts per result set. The
    script's RAISERROR surfaces as the driver's exception."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert SOURCE_TABLE in script
    script = script.replace(
        SOURCE_TABLE, f"[{os.environ['MSSQL_LOSS_DATABASE']}].dbo.CRMContractStatus")
    script = script.replace("DECLARE @dry_run BIT = 1;",
                            f"DECLARE @dry_run BIT = {int(dry_run)};")
    with get_connection("LOSS") as conn, conn.begin():
        conn.execute(text("DELETE FROM dbo.CRMContractStatus"))
        conn.execute(text("INSERT INTO dbo.CRMContractStatus (CRMID, Status) "
                          "VALUES (:crm, :status)"),
                     [{"crm": crm, "status": status} for crm, status in rows])
    raw = get_engine("WORKBENCH").raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute(script)
        result_sets: list[list[dict]] = []
        while True:
            if cursor.description is not None:
                columns = [c[0] for c in cursor.description]
                result_sets.append([dict(zip(columns, r)) for r in cursor.fetchall()])
            if not cursor.nextset():
                break
        raw.commit()
        return result_sets
    finally:
        raw.close()


def _contracts(submission_id: str) -> dict[str, dict]:
    return {row["crm_id"]: row for row in execute(
        "SELECT crm_id, contract_status_code, updated_at, updated_by "
        "FROM contract WHERE submission_id = :id",
        {"id": submission_id}, connection="WORKBENCH")}


@pytest.fixture()
def deal() -> SimpleNamespace:
    """One analyst and one submission with two Open contracts, ``A`` and ``B``,
    both with CRM IDs no other row holds."""
    actor = str(uuid.uuid4())
    execute_command(
        "INSERT INTO app_user (id, email, display_name, password_hash, "
        "must_change_password, is_active) VALUES (:id, :email, 'Bulk Update Test', "
        "'not-a-hash', 0, 1)",
        {"id": actor, "email": f"bulk-{actor[:8]}@example.com"}, connection="WORKBENCH")
    tag = uuid.uuid4().hex[:8].upper()
    crm_a, crm_b = f"BULK-{tag}-A", f"BULK-{tag}-B"
    created = svc.create_submission(
        name=f"Bulk update {tag}", cedant_name="Bulk Test Cedant", treaty_year=2027,
        contracts=[svc.ContractInput(crm_a, "per_risk_xol", date(2027, 1, 1)),
                   svc.ContractInput(crm_b, "aggregate_xol", date(2027, 1, 1))],
        actor_id=actor, confirmed=True)
    submission_id = str(created.submission_id)
    yield SimpleNamespace(submission_id=submission_id, crm_a=crm_a, crm_b=crm_b, tag=tag)
    with get_connection("LOSS") as conn, conn.begin():
        conn.execute(text("DELETE FROM dbo.CRMContractStatus"))
    for table in ("contract", "submission_status_event"):
        execute_command(f"DELETE FROM {table} WHERE submission_id = :id",
                        {"id": submission_id}, connection="WORKBENCH")
    execute_command("DELETE FROM submission WHERE id = :id",
                    {"id": submission_id}, connection="WORKBENCH")
    execute_command("DELETE FROM app_user WHERE id = :id",
                    {"id": actor}, connection="WORKBENCH")


def test_dry_run_reports_the_changes_and_writes_nothing(deal):
    missing = f"BULK-{deal.tag}-MISSING"
    summary, problems, changes = _run(
        [(deal.crm_a.lower(), "Won"), (f"  {deal.crm_b}  ", "lost"), (missing, "Open")],
        dry_run=True)

    assert summary == [{"source_rows": 3, "to_update": 2, "already_at_status": 0,
                        "not_in_workbench": 1, "unknown_status": 0,
                        "duplicate_crm_ids": 0, "dry_run": True}]
    assert problems == []
    assert {(c["crm_id"], c["old_status"], c["new_status"]) for c in changes} == {
        (deal.crm_a.lower(), "OPEN", "WON"), (f"  {deal.crm_b}  ", "OPEN", "LOST")}
    after = _contracts(deal.submission_id)
    assert {crm: row["contract_status_code"] for crm, row in after.items()} == {
        deal.crm_a: "OPEN", deal.crm_b: "OPEN"}


def test_apply_sets_each_contract_to_the_status_beside_its_crm_id(deal):
    before = _contracts(deal.submission_id)

    summary, _, changes = _run([(deal.crm_a, "WON"), (deal.crm_b, "Lost")], dry_run=False)

    assert summary[0]["to_update"] == 2 and summary[0]["dry_run"] is False
    assert len(changes) == 2
    after = _contracts(deal.submission_id)
    assert after[deal.crm_a]["contract_status_code"] == "WON"
    assert after[deal.crm_b]["contract_status_code"] == "LOST"
    for crm_id in (deal.crm_a, deal.crm_b):
        assert after[crm_id]["updated_at"] > before[crm_id]["updated_at"]
        assert after[crm_id]["updated_by"] is None

    # A second run of the same source changes nothing.
    summary, _, changes = _run([(deal.crm_a, "won"), (deal.crm_b, "LOST")], dry_run=False)
    assert summary[0]["to_update"] == 0 and summary[0]["already_at_status"] == 2
    assert changes == []
    assert _contracts(deal.submission_id)[deal.crm_a]["updated_at"] == after[deal.crm_a]["updated_at"]


def test_unknown_status_writes_nothing(deal):
    with pytest.raises(Exception, match="Nothing written"):
        _run([(deal.crm_a, "Bound"), (deal.crm_b, "Lost")], dry_run=False)

    after = _contracts(deal.submission_id)
    assert after[deal.crm_a]["contract_status_code"] == "OPEN"
    assert after[deal.crm_b]["contract_status_code"] == "OPEN"


def test_crm_id_listed_twice_writes_nothing(deal):
    # The source's primary key refuses an exact duplicate; two spellings of one
    # CRM ID (a leading space) are two source rows and one contract.
    with pytest.raises(Exception, match="Nothing written"):
        _run([(deal.crm_a, "Won"), (f" {deal.crm_a}", "Lost"), (deal.crm_b, "Won")],
             dry_run=False)

    after = _contracts(deal.submission_id)
    assert after[deal.crm_a]["contract_status_code"] == "OPEN"
    assert after[deal.crm_b]["contract_status_code"] == "OPEN"
