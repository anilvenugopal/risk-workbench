"""Unit tests for the Execute Suite/Template gate + plan (spec 010, T021).

Covers the naming helpers (T-04/T-05), plan composition (per-suite currency,
item_no ordinals, submission tag, asOfDate from the chosen vintage), and the
validation gate (incomplete/cache-invalid currency, unknown ids).
"""

from __future__ import annotations

import json
import uuid

from app.services import analysis_execution_service as svc
from db import execute_command, execute_one


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


def _seed_edm(name="EDM One", status="ready") -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, :status)",
        {"id": edm_id, "name": name, "status": status}, connection="WORKBENCH")
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


def _seed_template(name="Template A", event_rate_scheme_name=None,
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


# ── naming helpers (T-04/T-05) ───────────────────────────────────────────────────

def test_build_full_name_is_portfolio_space_template():
    assert svc.build_full_name("US Southeast Wind", "US HU DLM v23") == (
        "US Southeast Wind US HU DLM v23")


def test_name_attempt_zero_has_no_suffix_and_clips_at_64():
    full = "x" * 80
    full_name, name = svc.name_attempt(full, 0)
    assert full_name == full
    assert name == full[:64]
    assert len(name) == 64


def test_name_attempt_suffix_re_clips_base_so_it_still_fits_64():
    full = "x" * 80
    full_name, name = svc.name_attempt(full, 1)
    assert full_name == full + " - 1"
    assert name == full[:64 - len(" - 1")] + " - 1"
    assert len(name) == 64


def test_name_attempt_suffix_survives_on_a_short_name():
    full_name, name = svc.name_attempt("Short Name", 2)
    assert full_name == "Short Name - 2"
    assert name == "Short Name - 2"


# ── plan composition ─────────────────────────────────────────────────────────────

def test_request_execution_composes_plan_with_per_suite_currency_and_tags(
        iteration2_db):
    _seed_currency(scheme="RMS", vintage="RL25", effective_date="2025-05-28")
    _seed_currency(scheme="DT", vintage="RL24", effective_date="2024-05-28")
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    t1 = _seed_template("Template A", tags=["Tag1"])
    t2 = _seed_template("Template B")
    suite1 = _seed_suite("Suite One", [t1])
    suite2 = _seed_suite("Suite Two", [t2])

    execution_id = svc.request_execution(
        edm_id=edm_id, kind="suite", portfolio_ids=[portfolio_id],
        treaty_names=[],
        suite_picks=[
            svc.SuitePick(suite_id=suite1, template_ids=[t1],
                         currency_code="USD", currency_scheme="RMS",
                         currency_vintage="RL25"),
            svc.SuitePick(suite_id=suite2, template_ids=[t2],
                         currency_code="USD", currency_scheme="DT",
                         currency_vintage="RL24"),
        ],
        actor_id=iteration2_db.user_a, submission_id=str(uuid.uuid4()),
        submission_name="Deal ABC")

    row = execute_one(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :id",
        {"id": execution_id}, connection="WORKBENCH")
    plan = json.loads(row["input_data"])
    assert plan["execution_id"] == execution_id
    assert [item["item_no"] for item in plan["items"]] == [0, 1]
    by_suite = {item["suite_id"]: item for item in plan["items"]}
    assert by_suite[suite1]["currency"] == {
        "code": "USD", "scheme": "RMS", "vintage": "RL25",
        "asOfDate": "2025-05-28"}
    assert by_suite[suite2]["currency"] == {
        "code": "USD", "scheme": "DT", "vintage": "RL24",
        "asOfDate": "2024-05-28"}
    # the template's own tags plus the submission's name (FR-021/T-20)
    assert by_suite[suite1]["tag_names"] == ["Tag1", "Deal ABC"]
    assert by_suite[suite2]["tag_names"] == ["Deal ABC"]


def test_request_execution_template_kind_uses_single_currency_block(iteration2_db):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template("Solo Template")

    execution_id = svc.request_execution(
        edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
        treaty_names=[], template_ids=[template_id],
        currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
        actor_id=iteration2_db.user_a)

    row = execute_one(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :id",
        {"id": execution_id}, connection="WORKBENCH")
    plan = json.loads(row["input_data"])
    assert len(plan["items"]) == 1
    item = plan["items"][0]
    assert item["suite_id"] is None
    assert item["currency"]["code"] == "USD"
    assert plan["submission_id"] is None
    assert item["tag_names"] == []  # no submission context, no template tags


# ── gate ──────────────────────────────────────────────────────────────────────────

def test_gate_rejects_incomplete_currency_block(iteration2_db):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()

    try:
        svc.request_execution(
            edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
            treaty_names=[], template_ids=[template_id],
            currency_code="USD", currency_scheme="", currency_vintage="",
            actor_id=iteration2_db.user_a)
        raise AssertionError("expected ExecutionGateError")
    except svc.ExecutionGateError as exc:
        assert any("currency" in e.lower() for e in exc.errors)


def test_gate_rejects_cache_invalid_vintage(iteration2_db):
    _seed_currency()  # RMS/RL25 only
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()

    try:
        svc.request_execution(
            edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
            treaty_names=[], template_ids=[template_id],
            currency_code="USD", currency_scheme="RMS",
            currency_vintage="NOT-A-REAL-VINTAGE",
            actor_id=iteration2_db.user_a)
        raise AssertionError("expected ExecutionGateError")
    except svc.ExecutionGateError as exc:
        assert any("vintage" in e.lower() for e in exc.errors)


def test_gate_rejects_edm_not_ready(iteration2_db):
    _seed_currency()
    edm_id = _seed_edm(status="pending_import")
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()

    try:
        svc.request_execution(
            edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
            treaty_names=[], template_ids=[template_id],
            currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
            actor_id=iteration2_db.user_a)
        raise AssertionError("expected ExecutionGateError")
    except svc.ExecutionGateError:
        pass


def test_gate_rejects_suite_kind_with_zero_templates_selected(iteration2_db):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    template_id = _seed_template()
    suite_id = _seed_suite("Suite One", [template_id])

    try:
        svc.request_execution(
            edm_id=edm_id, kind="suite", portfolio_ids=[portfolio_id],
            treaty_names=[],
            suite_picks=[svc.SuitePick(
                suite_id=suite_id, template_ids=[],  # every template deselected
                currency_code="USD", currency_scheme="RMS", currency_vintage="RL25")],
            actor_id=iteration2_db.user_a)
        raise AssertionError("expected ExecutionGateError")
    except svc.ExecutionGateError as exc:
        assert any("suite" in e.lower() for e in exc.errors)


def test_gate_rejects_template_foreign_to_its_suite(iteration2_db):
    _seed_currency()
    edm_id = _seed_edm()
    portfolio_id = _seed_portfolio(edm_id)
    t1 = _seed_template("In suite")
    t2 = _seed_template("Not in suite")
    suite_id = _seed_suite("Suite One", [t1])

    try:
        svc.request_execution(
            edm_id=edm_id, kind="suite", portfolio_ids=[portfolio_id],
            treaty_names=[],
            suite_picks=[svc.SuitePick(
                suite_id=suite_id, template_ids=[t2],
                currency_code="USD", currency_scheme="RMS", currency_vintage="RL25")],
            actor_id=iteration2_db.user_a)
        raise AssertionError("expected ExecutionGateError")
    except svc.ExecutionGateError as exc:
        assert any("belong" in e.lower() for e in exc.errors)
