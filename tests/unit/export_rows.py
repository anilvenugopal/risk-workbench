"""Row makers for the spec-014 export tests: raw INSERTs against the SQLite
WORKBENCH (``iteration2_db``) and the SQLite loss mirror (``loss_db``)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from sqlalchemy import text

from db import execute_command, execute_one, get_connection

NOW = "2026-09-10 08:00:00"


def seed_submission(user_id: str, *, name: str = "TY2604_Deal", inception: str = "2026-04-01",
                    treaty_year: int | None = 2026, crm_ids: tuple[str, ...] = ("CRM-1",)) -> str:
    submission_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, assigned_analyst_id, name, cedant_name, treaty_type_code, "
        "inception_date, treaty_year, status_code, inserted_at, updated_at, inserted_by, "
        "updated_by) VALUES (:id, :u, :n, 'Cedant Co', 'cat_xol', :inc, :ty, 'ACTIVE', :now, "
        ":now, :u, :u)",
        {"id": submission_id, "u": user_id, "n": name, "inc": inception, "ty": treaty_year,
         "now": NOW}, connection="WORKBENCH")
    for index, crm_id in enumerate(crm_ids):
        execute_command(
            "INSERT INTO submission_crm_id (id, submission_id, crm_id, inserted_at, inserted_by) "
            "VALUES (:id, :s, :c, :at, :u)",
            {"id": str(uuid.uuid4()), "s": submission_id, "c": crm_id,
             "at": f"2026-01-01 00:00:0{index}", "u": user_id}, connection="WORKBENCH")
    return submission_id


def seed_edm_for(submission_id: str, name: str = "EDM One") -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, :n, 'ready', :now, :now)",
        {"id": edm_id, "n": name, "now": NOW}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id, inserted_at) VALUES (:s, :e, :now)",
        {"s": submission_id, "e": edm_id, "now": NOW}, connection="WORKBENCH")
    return edm_id


def seed_rdm_for(submission_id: str, name: str = "Broker RDM") -> str:
    rdm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_rdm (id, name, irp_id, status, inserted_at, updated_at) "
        "VALUES (:id, :n, 900, 'ready', :now, :now)",
        {"id": rdm_id, "n": name, "now": NOW}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id, inserted_at) VALUES (:s, :r, :now)",
        {"s": submission_id, "r": rdm_id, "now": NOW}, connection="WORKBENCH")
    return rdm_id


def loss_results(*codes: str) -> dict:
    return {"perspectives": {code: {"aal": 100.0, "std_dev": 10.0, "oep": {}, "aep": {}}
                             for code in codes}}


def seed_analysis(*, edm_id: str | None = None, rdm_id: str | None = None,
                  name: str = "CRE_Port_Template", full_name: str | None = "CRE_Port_Template long",
                  irp_id: str | None = "41958", irp_app_analysis_id: str | None = "41958",
                  perspectives: tuple[str, ...] | None = ("GU", "GR", "RL"),
                  currency: str | None = "USD", peril: str = "EQ", region: str = "NAEQ",
                  is_group: int = 0, inserted_at: str = NOW) -> str:
    analysis_id = str(uuid.uuid4())
    settings = {"perilCode": peril, "regionCode": region, "engineType": "DLM",
                "engineVersion": "RL25", "appAnalysisId": irp_app_analysis_id}
    if currency:
        settings["currencyCode"] = currency
    execute_command(
        "INSERT INTO irp_analysis (id, edm_id, rdm_id, irp_id, irp_app_analysis_id, name, "
        "full_name, status_code, settings_metadata, is_group, loss_results, inserted_at, "
        "updated_at) VALUES (:id, :edm, :rdm, :irp, :app, :n, :f, 'ready', :settings, :g, "
        ":results, :at, :at)",
        {"id": analysis_id, "edm": edm_id, "rdm": rdm_id, "irp": irp_id,
         "app": irp_app_analysis_id, "n": name, "f": full_name,
         "settings": json.dumps(settings), "g": is_group,
         "results": (json.dumps(loss_results(*perspectives)) if perspectives else None),
         "at": inserted_at}, connection="WORKBENCH")
    return analysis_id


def seed_client(client_id: int = 1, name: str = "Example Re", active: str = "Y") -> int:
    execute_command(
        "INSERT INTO dbo.Client (ClientID, ClientName, ActiveFlag) VALUES (:id, :n, :a)",
        {"id": client_id, "n": name, "a": active}, connection="LOSS")
    return client_id


def seed_manifest(*, export_id: str | None = None, submission_id: str | None = None,
                  irp_analysis_id: str | None = None, irp_app_analysis_id: int = 41958,
                  perspective_code: str = "GR", client_id: int = 1,
                  requested_by_email: str = "analyst.a@example.com",
                  requested_at: str = NOW, stage_status: str = "pending",
                  load_status: str = "pending", **columns) -> dict:
    """Insert one manifest row with sensible defaults; returns the row."""
    values = {
        "export_id": export_id or str(uuid.uuid4()),
        "requested_by_email": requested_by_email, "requested_at": requested_at,
        "requested_from_submission_id": submission_id or str(uuid.uuid4()),
        "irp_analysis_id": irp_analysis_id or str(uuid.uuid4()),
        "irp_analysis_irp_id": str(irp_app_analysis_id),
        "irp_app_analysis_id": irp_app_analysis_id, "analysis_name": "CRE_Port_Template",
        "analysis_description": "CRE_Port_Template long", "perspective_code": perspective_code,
        "client_id": client_id, "treaty_incept": "2026-04-01", "treaty_year": 2026,
        "crm_id": "CRM-1", "data_name": None, "data_vintage": None, "data_currency": "USD",
        "data_model_vendor": "RMS", "server": "https://rm.example",
        "irp_export_job_id": None, "loss_table_type": None, "engine_type": None,
        "data_model_version": None, "peril_code": "EQ", "region_code": "NAEQ",
        "zip_file": None, "stage_status": stage_status, "staged_at": None,
        "load_status": load_status, "loaded_at": None, "error_message": None,
        "data_id": None, "staged_row_count": None, "stochastic_row_count": None,
        "historical_row_count": None, "exp_value_raised_count": None,
        "std_dev_zeroed_count": None, "inserted_at": requested_at, "updated_at": requested_at,
    }
    values.update(columns)
    columns_sql = ", ".join(values)
    params_sql = ", ".join(f":{k}" for k in values)
    with get_connection("LOSS") as conn, conn.begin():
        conn.execute(text(
            f"INSERT INTO stage.rwb_loss_result_manifest ({columns_sql}) VALUES ({params_sql})"),
            values)
        manifest_id = conn.execute(text(
            "SELECT MAX(manifest_id) FROM stage.rwb_loss_result_manifest")).scalar()
    return manifest_row(manifest_id)


def manifest_row(manifest_id: int) -> dict:
    return execute_one("SELECT * FROM stage.rwb_loss_result_manifest WHERE manifest_id = :m",
                       {"m": manifest_id}, connection="LOSS")


def manifest_for(export_id: str, irp_analysis_id: str) -> dict:
    return execute_one(
        "SELECT * FROM stage.rwb_loss_result_manifest "
        "WHERE export_id = :e AND irp_analysis_id = :a",
        {"e": export_id, "a": irp_analysis_id}, connection="LOSS")


def seed_export_job(*, export_id: str, irp_analysis_id: str, irp_id: str, status: str = "QUEUED",
                    completed_at: str | datetime | None = None, edm_id: str | None = None,
                    result: dict | None = None) -> str:
    job_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_job (id, irp_edm_id, irp_analysis_id, export_id, irp_job_type, irp_id, "
        "status, last_completion_result, submission_attempt_count, submitted_at, completed_at, "
        "inserted_at, updated_at) VALUES (:id, :edm, :a, :e, 'export', :irp, :s, :res, 0, :now, "
        ":done, :now, :now)",
        {"id": job_id, "edm": edm_id, "a": irp_analysis_id, "e": export_id, "irp": irp_id,
         "s": status, "res": (json.dumps(result) if result else None), "now": NOW,
         "done": completed_at}, connection="WORKBENCH")
    return job_id


def rwb_jobs(rwb_job_type: str) -> list[dict]:
    return [dict(r) for r in __import__("db").execute(
        "SELECT * FROM rwb_job WHERE rwb_job_type = :t ORDER BY inserted_at, id",
        {"t": rwb_job_type}, connection="WORKBENCH")]


__all__ = [
    "NOW", "seed_submission", "seed_edm_for", "seed_rdm_for", "seed_analysis", "seed_client",
    "seed_manifest", "manifest_row", "manifest_for", "seed_export_job", "rwb_jobs",
    "loss_results", "date",
]
