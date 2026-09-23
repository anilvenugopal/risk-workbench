"""The January bulk update script against a live SQL Server (spec 017 FR-023).

Runs ``infra/scripts/bulk_update_contract_status.sql`` with the rows between
its EXTRACT markers replaced by each test's extract. Every test creates its own
analyst and submission and deletes them afterwards.

Run with:  pytest tests/sqlserver --run-sqlserver   (requires live SQL Server)
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import submission_service as svc
from db import execute, execute_command, get_engine

pytestmark = pytest.mark.sqlserver

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "bulk_update_contract_status.sql"
EXTRACT_BLOCK = re.compile(r"-- ==== EXTRACT:.*?-- ==== END EXTRACT ====", re.S)


def _run(rows: list[tuple[str, str]], *, dry_run: bool) -> list[list[dict]]:
    """Run the script with ``rows`` as the extract; one list of dicts per result
    set. The script's RAISERROR surfaces as the driver's exception."""
    values = ",\n    ".join(f"('{crm_id}', '{status}')" for crm_id, status in rows)
    script = EXTRACT_BLOCK.sub(
        f"INSERT INTO #crm_status (crm_id, status) VALUES\n    {values};",
        SCRIPT.read_text(encoding="utf-8"))
    script = script.replace("DECLARE @dry_run BIT = 1;",
                            f"DECLARE @dry_run BIT = {int(dry_run)};")
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
    """One analyst and one submission with two In Process contracts, ``A`` and
    ``B``, both with CRM IDs no other row holds."""
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
    yield SimpleNamespace(submission_id=submission_id, crm_a=crm_a, crm_b=crm_b)
    for table in ("contract", "submission_status_event"):
        execute_command(f"DELETE FROM {table} WHERE submission_id = :id",
                        {"id": submission_id}, connection="WORKBENCH")
    execute_command("DELETE FROM submission WHERE id = :id",
                    {"id": submission_id}, connection="WORKBENCH")
    execute_command("DELETE FROM app_user WHERE id = :id",
                    {"id": actor}, connection="WORKBENCH")


def test_dry_run_reports_the_changes_and_writes_nothing(deal):
    missing = f"BULK-{uuid.uuid4().hex[:8]}-MISSING"
    summary, problems, skipped, changes = _run(
        [(deal.crm_a.lower(), "Won"), (f"  {deal.crm_b}  ", "lost"), (missing, "Won")],
        dry_run=True)

    assert summary == [{"extract_rows": 3, "to_update": 2, "already_at_status": 0,
                        "not_in_workbench": 1, "unknown_status": 0,
                        "duplicate_crm_ids": 0, "dry_run": True}]
    assert problems == []
    assert [row["crm_id"] for row in skipped] == [missing]
    assert {(c["crm_id"], c["old_status"], c["new_status"]) for c in changes} == {
        (deal.crm_a.lower(), "IN_PROCESS", "WON"), (f"  {deal.crm_b}  ", "IN_PROCESS", "LOST")}
    after = _contracts(deal.submission_id)
    assert {crm: row["contract_status_code"] for crm, row in after.items()} == {
        deal.crm_a: "IN_PROCESS", deal.crm_b: "IN_PROCESS"}


def test_apply_sets_each_contract_to_the_status_beside_its_crm_id(deal):
    before = _contracts(deal.submission_id)

    summary, _, _, changes = _run([(deal.crm_a, "WON"), (deal.crm_b, "Lost")], dry_run=False)

    assert summary[0]["to_update"] == 2 and summary[0]["dry_run"] is False
    assert len(changes) == 2
    after = _contracts(deal.submission_id)
    assert after[deal.crm_a]["contract_status_code"] == "WON"
    assert after[deal.crm_b]["contract_status_code"] == "LOST"
    for crm_id in (deal.crm_a, deal.crm_b):
        assert after[crm_id]["updated_at"] > before[crm_id]["updated_at"]
        assert after[crm_id]["updated_by"] is None

    # A second run of the same extract changes nothing.
    summary, _, _, changes = _run([(deal.crm_a, "won"), (deal.crm_b, "LOST")], dry_run=False)
    assert summary[0]["to_update"] == 0 and summary[0]["already_at_status"] == 2
    assert changes == []
    assert _contracts(deal.submission_id)[deal.crm_a]["updated_at"] == after[deal.crm_a]["updated_at"]


def test_unknown_status_writes_nothing(deal):
    with pytest.raises(Exception, match="Nothing written"):
        _run([(deal.crm_a, "Bound"), (deal.crm_b, "Lost")], dry_run=False)

    after = _contracts(deal.submission_id)
    assert after[deal.crm_a]["contract_status_code"] == "IN_PROCESS"
    assert after[deal.crm_b]["contract_status_code"] == "IN_PROCESS"


def test_crm_id_listed_twice_writes_nothing(deal):
    with pytest.raises(Exception, match="Nothing written"):
        _run([(deal.crm_a, "Won"), (deal.crm_a.lower(), "Lost"), (deal.crm_b, "Won")],
             dry_run=False)

    after = _contracts(deal.submission_id)
    assert after[deal.crm_a]["contract_status_code"] == "IN_PROCESS"
    assert after[deal.crm_b]["contract_status_code"] == "IN_PROCESS"
