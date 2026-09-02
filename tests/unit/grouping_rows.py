"""Row makers for the spec-012 grouping tests — raw INSERTs against the SQLite
WORKBENCH the ``iteration2_db`` fixture registers (same shape as
``analysis_rows``), shared by the gate/plan, worker, and poller modules.
"""

from __future__ import annotations

import json
import uuid

from db import execute_command


def seed_submission(name: str = "Sub One") -> str:
    submission_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, name, cedant_name, status_code) "
        "VALUES (:id, :name, 'Cedant', 'active')",
        {"id": submission_id, "name": name}, connection="WORKBENCH")
    return submission_id


def link_submission_edm(submission_id: str, edm_id: str) -> None:
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
        {"s": submission_id, "e": edm_id}, connection="WORKBENCH")


def seed_own_analysis(edm_id: str, name: str, status: str = "ready",
                      settings: dict | None = None) -> str:
    analysis_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, edm_id, name, full_name, status_code, "
        "is_group, settings_metadata, inserted_at) "
        "VALUES (:id, :edm, :name, :name, :status, 0, :settings, :now)",
        {"id": analysis_id, "edm": edm_id, "name": name, "status": status,
         "settings": (json.dumps(settings) if settings else None),
         "now": "2026-08-27T00:00:00"}, connection="WORKBENCH")
    return analysis_id


def seed_broker_analysis(submission_id: str, name: str,
                         irp_id: str = "70001",
                         rdm_name: str = "RDM One") -> str:
    rdm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_rdm (id, name, status) VALUES (:id, :name, 'ready')",
        {"id": rdm_id, "name": rdm_name}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
        {"s": submission_id, "r": rdm_id}, connection="WORKBENCH")
    analysis_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, rdm_id, irp_id, name, status_code, "
        "is_group, inserted_at) "
        "VALUES (:id, :rdm, :irp, :name, 'ready', 0, :now)",
        {"id": analysis_id, "rdm": rdm_id, "irp": irp_id, "name": name,
         "now": "2026-08-27T00:00:00"}, connection="WORKBENCH")
    return analysis_id


def seed_group(submission_id: str, name: str, status: str = "ready",
               members: list[dict] | None = None) -> str:
    """``members`` seeds the approved compose plan the worker stores verbatim in
    ``submitted_settings`` — plan-shaped entries, as
    ``grouping_service.request_grouping`` writes them."""
    group_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, submission_id, name, full_name, "
        "status_code, is_group, submitted_settings, inserted_at) "
        "VALUES (:id, :sub, :name, :name, :status, 1, :plan, :now)",
        {"id": group_id, "sub": submission_id, "name": name, "status": status,
         "plan": (json.dumps({"members": members})
                  if members is not None else None),
         "now": "2026-08-27T00:00:00"}, connection="WORKBENCH")
    return group_id
