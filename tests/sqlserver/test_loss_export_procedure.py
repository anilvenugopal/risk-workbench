"""SQL Server tier: ``stage.usp_load_elt_result`` against the dev mirror in rwb_loss.

Run with: make test-sql (inside linux-box) or make wsl-test-sql.

At module start this applies db/bootstrap/loss_dev_mirror.sql and
db/bootstrap/loss_schema.sql over the LOSS connection (both idempotent) and then
**truncates** the stage tables and the five mirror tables, dbo.Client and
dbo.Lookup_RMS_HistoricalRDS included. Re-run ``make bootstrap-loss`` afterwards
to restore the dev seeds. Never point MSSQL_LOSS_DATABASE at CIC's repository
while running this tier.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from app.workers.export_jobs import ELT_COLUMN_MAP
from db import execute, execute_one, execute_procedure, get_connection, upload_parquet

pytestmark = pytest.mark.sqlserver

BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / "db" / "bootstrap"
MODEL_VERSION = "25.0"


def _clear_tables() -> None:
    with get_connection("LOSS") as conn, conn.begin():
        for table in ("stage.rwb_loss_result_elt_data", "stage.rwb_loss_result_file",
                      "stage.rwb_loss_result_manifest", "dbo.RMS_HistoricalRDS",
                      "dbo.RMSELT", "dbo.Data", "dbo.Lookup_RMS_HistoricalRDS",
                      "dbo.Client"):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text(
            "INSERT INTO dbo.Client (ClientID, ClientName, ActiveFlag) "
            "VALUES (1, 'Test Client', 'Y')"))


@pytest.fixture(scope="module", autouse=True)
def loss_schema():
    from db.scripts import execute_script_file  # noqa: PLC0415 — trusted DDL, test only
    execute_script_file(BOOTSTRAP_DIR / "loss_dev_mirror.sql", connection="LOSS")
    execute_script_file(BOOTSTRAP_DIR / "loss_schema.sql", connection="LOSS")
    _clear_tables()


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    _clear_tables()


def _seed_lookup(*rows: tuple) -> None:
    """Each row: (EventID, Peril, Name, ModelVersion[, PCS])."""
    with get_connection("LOSS") as conn, conn.begin():
        for row in rows:
            event_id, peril, name, version = row[:4]
            pcs = row[4] if len(row) > 4 else None
            conn.execute(text(
                "INSERT INTO dbo.Lookup_RMS_HistoricalRDS "
                "(EventID, CatYear, Peril, [Type], [Name], [PCS#], ModelVersion) "
                "VALUES (:e, 2020, :p, 'HIST', :n, :pcs, :v)"
            ), {"e": event_id, "p": peril, "n": name, "pcs": pcs, "v": version})


def _manifest(**overrides) -> int:
    values = {
        "export_id": str(uuid.uuid4()), "requested_by_email": "analyst@example.com",
        "requested_from_submission_id": str(uuid.uuid4()),
        "irp_analysis_id": str(uuid.uuid4()), "irp_analysis_irp_id": "41958",
        "irp_app_analysis_id": 41958, "analysis_name": "CRE_Port_Template",
        "analysis_description": "CRE_Port_Template full", "perspective_code": "GR",
        "client_id": 1, "treaty_incept": date(2026, 1, 1), "treaty_year": 2026,
        "crm_id": "CRM-1", "data_name": "Data name", "data_vintage": date(2025, 12, 31),
        "data_currency": "USD", "data_model_vendor": "RMS", "server": "https://rm.example",
        "data_model_version": MODEL_VERSION, "stage_status": "staged",
        "load_status": "pending",
    }
    values.update(overrides)
    with get_connection("LOSS") as conn, conn.begin():
        return conn.execute(text(
            "INSERT INTO stage.rwb_loss_result_manifest (export_id, requested_by_email, "
            "requested_at, requested_from_submission_id, irp_analysis_id, "
            "irp_analysis_irp_id, irp_app_analysis_id, analysis_name, analysis_description, "
            "perspective_code, client_id, treaty_incept, treaty_year, crm_id, data_name, "
            "data_vintage, data_currency, data_model_vendor, [server], data_model_version, "
            "stage_status, load_status) "
            "OUTPUT INSERTED.manifest_id "
            "VALUES (:export_id, :requested_by_email, SYSUTCDATETIME(), "
            ":requested_from_submission_id, :irp_analysis_id, :irp_analysis_irp_id, "
            ":irp_app_analysis_id, :analysis_name, :analysis_description, "
            ":perspective_code, :client_id, :treaty_incept, :treaty_year, :crm_id, "
            ":data_name, :data_vintage, :data_currency, :data_model_vendor, :server, "
            ":data_model_version, :stage_status, :load_status)"
        ), values).scalar()


def _stage(tmp_path: Path, manifest_id: int, rows: list[tuple]) -> None:
    """Stage rows (event_id, loss, std_dev_i, std_dev_c, exp_value) through
    upload_parquet, the same call the stage worker makes."""
    with get_connection("LOSS") as conn, conn.begin():
        file_id = conn.execute(text(
            "INSERT INTO stage.rwb_loss_result_file (manifest_id, result_file, "
            "output_level, perspective_code, chunk_index) "
            "OUTPUT INSERTED.result_file_id VALUES (:m, 'ELT/Portfolio/GR/x_0.parquet', "
            "'Portfolio', 'GR', 0)"), {"m": manifest_id}).scalar()
    table = pa.table({
        "PortInfoId": pa.array([1] * len(rows), pa.int32()),
        "PortInfoName": ["Portfolio"] * len(rows),
        "PortInfoNum": ["P1"] * len(rows),
        "EventId": pa.array([r[0] for r in rows], pa.int32()),
        "Rate": [0.001] * len(rows),
        "Loss": [float(r[1]) for r in rows],
        "StdDevI": [float(r[2]) for r in rows],
        "StdDevC": [float(r[3]) for r in rows],
        "ExpValue": [float(r[4]) for r in rows],
    })
    path = tmp_path / f"{manifest_id}.parquet"
    pq.write_table(table, path)
    n = upload_parquet(path, "rwb_loss_result_elt_data", schema="stage",
                       extra_columns={"manifest_id": manifest_id, "result_file_id": file_id},
                       column_mapping=ELT_COLUMN_MAP, drop_unmapped_columns=True,
                       connection="LOSS")
    assert n == len(rows)


def _load(manifest_id: int) -> None:
    execute_procedure("stage.usp_load_elt_result", {"manifest_id": manifest_id},
                      connection="LOSS")


def _manifest_row(manifest_id: int) -> dict:
    return execute_one("SELECT * FROM stage.rwb_loss_result_manifest WHERE manifest_id = :m",
                       {"m": manifest_id}, connection="LOSS")


def _count(table: str) -> int:
    return execute_one(f"SELECT COUNT(*) AS n FROM {table}", {}, connection="LOSS")["n"]


# ── happy path ────────────────────────────────────────────────────────────────

def test_load_classifies_corrects_and_writes_the_three_targets(tmp_path):
    _seed_lookup((3001, "WS", "Historical storm", MODEL_VERSION, "PCS1"))
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [
        (1001, 100.0, -1.0, 2.0, 50.0),   # stochastic: exposure raised, std dev zeroed
        (1002, 10.0, 1.0, 1.0, 20.0),     # stochastic: untouched
        (3001, 500.0, -3.0, -4.0, 100.0), # historical: exposure raised, std dev kept
    ])

    _load(manifest_id)

    row = _manifest_row(manifest_id)
    assert row["load_status"] == "loaded"
    assert row["loaded_at"] is not None and row["error_message"] is None
    assert row["stochastic_row_count"] == 2
    assert row["historical_row_count"] == 1
    assert row["exp_value_raised_count"] == 2
    assert row["std_dev_zeroed_count"] == 1

    data = execute_one("SELECT * FROM dbo.Data", {}, connection="LOSS")
    assert data["DataID"] == row["data_id"]
    assert data["ClientID"] == 1 and data["AnalysisID"] == 41958
    assert data["Perspective"] == "GR" and data["DataModelVersion"] == MODEL_VERSION
    assert data["DataModelVendor"] == "RMS" and data["DataCurrency"] == "USD"
    assert data["Name"] == "CRE_Port_Template"
    assert data["Description"] == "CRE_Port_Template full"
    assert data["Server"] == "https://rm.example" and data["CRMID"] == "CRM-1"
    assert data["DataName"] == "Data name"
    assert str(data["TreatyIncept"]) == "2026-01-01"
    assert str(data["DataVintage"]) == "2025-12-31"

    elt = {r["EventID"]: r for r in execute(
        "SELECT * FROM dbo.RMSELT ORDER BY EventID", {}, connection="LOSS")}
    assert set(elt) == {1001, 1002}
    assert elt[1001]["DataID"] == row["data_id"]
    assert elt[1001]["Loss"] == 100.0 and elt[1001]["ExpValue"] == 100.0
    assert elt[1001]["StdDevI"] == 0.0 and elt[1001]["StdDevC"] == 2.0
    assert elt[1002]["ExpValue"] == 20.0 and elt[1002]["StdDevI"] == 1.0

    hist = execute_one("SELECT * FROM dbo.RMS_HistoricalRDS", {}, connection="LOSS")
    assert hist["DataID"] == row["data_id"] and hist["ClientID"] == 1
    assert hist["EventID"] == 3001 and hist["Loss"] == 500.0
    assert hist["Peril"] == "WS" and hist["ModelVersion"] == MODEL_VERSION
    assert hist["Type"] == "HIST" and hist["Event_Name"] == "Historical storm"
    assert hist["PCS"] == "PCS1" and hist["Perspective"] == "GR"
    assert hist["TreatyYear"] == "2026"
    assert str(hist["TreatyIncept"]).startswith("2026-01-01")
    assert hist["DataInforce"] == "2025-12-31"

    staged = {r["event_id"]: r for r in execute(
        "SELECT * FROM stage.rwb_loss_result_elt_data WHERE manifest_id = :m",
        {"m": manifest_id}, connection="LOSS")}
    assert staged[3001]["event_type"] == "historical"
    assert staged[3001]["std_dev_i"] == -3.0 and staged[3001]["std_dev_zeroed"] is False
    assert staged[3001]["exp_value"] == 500.0 and staged[3001]["exp_value_raised"] is True
    assert staged[1001]["std_dev_zeroed"] is True


def test_blank_vintage_and_year_load_as_null(tmp_path):
    _seed_lookup((3001, "WS", "Storm", MODEL_VERSION))
    manifest_id = _manifest(data_vintage=None, treaty_year=None)
    _stage(tmp_path, manifest_id, [(3001, 1.0, 0.0, 0.0, 1.0)])

    _load(manifest_id)

    hist = execute_one("SELECT TreatyYear, DataInforce FROM dbo.RMS_HistoricalRDS",
                       {}, connection="LOSS")
    assert hist["TreatyYear"] is None and hist["DataInforce"] is None


# ── preconditions ─────────────────────────────────────────────────────────────

def test_missing_lookup_model_version_fails_and_writes_nothing(tmp_path):
    _seed_lookup((3001, "WS", "Storm", "24.0"))
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [(1001, 1.0, 0.0, 0.0, 1.0)])

    with pytest.raises(Exception) as exc:
        _load(manifest_id)

    assert "no rows for model version 25.0" in str(exc.value)
    assert "(50002)" in str(exc.value)
    row = _manifest_row(manifest_id)
    assert row["load_status"] == "failed"
    assert "no rows for model version 25.0" in row["error_message"]
    assert row["data_id"] is None
    assert _count("dbo.Data") == 0 and _count("dbo.RMSELT") == 0
    assert _count("dbo.RMS_HistoricalRDS") == 0


def test_event_matching_two_lookup_rows_fails(tmp_path):
    _seed_lookup((3001, "WS", "Storm A", MODEL_VERSION), (3001, "EQ", "Quake A", MODEL_VERSION))
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [(3001, 1.0, 0.0, 0.0, 1.0), (1001, 1.0, 0.0, 0.0, 1.0)])

    with pytest.raises(Exception) as exc:
        _load(manifest_id)

    assert "event 3001 matches 2 historical lookup rows for model version 25.0" in str(exc.value)
    assert "(50003)" in str(exc.value)
    assert _manifest_row(manifest_id)["load_status"] == "failed"
    assert _count("dbo.Data") == 0


def test_second_call_raises_naming_the_data_id(tmp_path):
    _seed_lookup((3001, "WS", "Storm", MODEL_VERSION))
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [(1001, 1.0, 0.0, 0.0, 1.0)])
    _load(manifest_id)
    data_id = _manifest_row(manifest_id)["data_id"]

    with pytest.raises(Exception) as exc:
        _load(manifest_id)

    assert f"manifest {manifest_id} already loaded as data ID {data_id}" in str(exc.value)
    assert "(50001)" in str(exc.value)
    row = _manifest_row(manifest_id)
    assert row["load_status"] == "loaded" and row["data_id"] == data_id
    assert _count("dbo.Data") == 1 and _count("dbo.RMSELT") == 1


def test_pending_row_is_refused_and_left_alone(tmp_path):
    # The stage worker may still be running for this row: the refusal must not
    # stamp load_status = 'failed' over it.
    _seed_lookup((3001, "WS", "Storm", MODEL_VERSION))
    manifest_id = _manifest(stage_status="pending")

    with pytest.raises(Exception) as exc:
        _load(manifest_id)

    assert f"manifest {manifest_id} is not staged (stage_status pending)" in str(exc.value)
    assert "(50001)" in str(exc.value)
    row = _manifest_row(manifest_id)
    assert row["load_status"] == "pending" and row["error_message"] is None
    assert _count("dbo.Data") == 0


def test_unknown_manifest_is_refused():
    with pytest.raises(Exception) as exc:
        _load(999999)
    assert "manifest 999999 not found" in str(exc.value)


def test_call_inside_a_transaction_is_refused_and_keeps_the_callers_work(tmp_path):
    _seed_lookup((3001, "WS", "Storm", MODEL_VERSION))
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [(1001, 1.0, 0.0, 0.0, 1.0)])

    with get_connection("LOSS") as conn:
        tx = conn.begin()
        conn.execute(text("INSERT INTO dbo.Client (ClientID, ClientName, ActiveFlag) "
                          "VALUES (2, 'Caller work', 'Y')"))
        with pytest.raises(Exception) as exc:
            conn.execute(text("EXEC stage.usp_load_elt_result @manifest_id = :m"),
                         {"m": manifest_id})
        assert "must be called outside a transaction" in str(exc.value)
        assert "(50000)" in str(exc.value)
        # The caller's transaction is still open and holds its own row.
        assert conn.execute(text("SELECT @@TRANCOUNT")).scalar() == 1
        tx.commit()

    assert _count("dbo.Client") == 2
    row = _manifest_row(manifest_id)
    assert row["load_status"] == "pending"
    assert _count("dbo.Data") == 0


def test_target_write_failure_rolls_back_every_target_row(tmp_path):
    # The lookup's Peril is wider than RMS_HistoricalRDS.Peril varchar(5): the
    # historical insert fails after Data and RMSELT were written, and CATCH
    # rolls all three back.
    _seed_lookup((3001, "WINDSTORM", "Storm", MODEL_VERSION))
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [(1001, 1.0, 0.0, 0.0, 1.0), (3001, 2.0, 0.0, 0.0, 2.0)])

    with pytest.raises(Exception) as exc:
        _load(manifest_id)

    assert "truncated" in str(exc.value).lower()
    row = _manifest_row(manifest_id)
    assert row["load_status"] == "failed" and row["data_id"] is None
    assert "truncated" in row["error_message"].lower()
    assert _count("dbo.Data") == 0 and _count("dbo.RMSELT") == 0
    assert _count("dbo.RMS_HistoricalRDS") == 0
    staged = execute_one(
        "SELECT event_type FROM stage.rwb_loss_result_elt_data "
        "WHERE manifest_id = :m AND event_id = 3001", {"m": manifest_id}, connection="LOSS")
    assert staged["event_type"] is None  # the classification rolled back too


def test_failed_row_can_be_loaded_again_after_the_fix(tmp_path):
    manifest_id = _manifest()
    _stage(tmp_path, manifest_id, [(3001, 1.0, 0.0, 0.0, 1.0)])
    with pytest.raises(Exception):
        _load(manifest_id)
    assert _manifest_row(manifest_id)["load_status"] == "failed"

    _seed_lookup((3001, "WS", "Storm", MODEL_VERSION))
    _load(manifest_id)

    row = _manifest_row(manifest_id)
    assert row["load_status"] == "loaded" and row["historical_row_count"] == 1
    assert _count("dbo.Data") == 1
