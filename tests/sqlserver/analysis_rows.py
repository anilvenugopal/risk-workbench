"""Row makers for the spec-010 analysis-execution tests — raw INSERTs against
the WORKBENCH database the ``workbench_db`` fixture provisions, shared by the
gate/plan, worker, and poller modules (same shape as ``breakout_rows``).
"""

from __future__ import annotations

import uuid

from db import execute_command, execute_one


def seed_currency(scheme: str = "RMS", vintage: str = "RL25",
                  effective_date: str = "2025-05-28") -> None:
    """USD plus one scheme/vintage pair — the complete currency block the gate
    demands (FR-019/FR-020). USD is guarded so a test can seed a second scheme
    without violating the code's unique constraint."""
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


def seed_edm(name: str = "EDM One", status: str = "ready") -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, :status)",
        {"id": edm_id, "name": name, "status": status}, connection="WORKBENCH")
    return edm_id


def seed_portfolio(edm_id: str, name: str = "Portfolio A") -> str:
    portfolio_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, irp_id, name) "
        "VALUES (:id, :edm, :irp_id, :name)",
        {"id": portfolio_id, "edm": edm_id,
         "irp_id": str(uuid.uuid4().int % 2_000_000_000), "name": name},
        connection="WORKBENCH")
    return portfolio_id


def seed_template(name: str = "Template A", event_rate_scheme_name: str | None = None,
                  tags: list[str] | None = None) -> str:
    template_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO analysis_template (id, name, analysis_profile_name, "
        "output_profile_name, event_rate_scheme_name) "
        "VALUES (:id, :name, 'Profile', 'Output', :scheme)",
        {"id": template_id, "name": name, "scheme": event_rate_scheme_name},
        connection="WORKBENCH")
    for tag in tags or []:
        execute_command(
            "INSERT INTO analysis_template_tag (template_id, tag_name) "
            "VALUES (:t, :tag)", {"t": template_id, "tag": tag},
            connection="WORKBENCH")
    return template_id


def seed_suite(name: str, template_ids: list[str]) -> str:
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
