"""Unit tests for the Execute Suite/Template gate + plan (spec 010, T021).

Covers plan composition (per-suite currency, item_no ordinals, submission tag,
asOfDate from the chosen vintage) and the validation gate (incomplete/
cache-invalid currency, unknown ids). The naming helpers live with their only
caller, the worker.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.services import analysis_execution_service as svc
from db import execute_one
from tests.unit.analysis_rows import (
    seed_currency,
    seed_edm,
    seed_portfolio,
    seed_suite,
    seed_template,
)

# ── plan composition ─────────────────────────────────────────────────────────────

def test_request_execution_composes_plan_with_per_suite_currency_and_tags(
        iteration2_db):
    seed_currency(scheme="RMS", vintage="RL25", effective_date="2025-05-28")
    seed_currency(scheme="DT", vintage="RL24", effective_date="2024-05-28")
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    t1 = seed_template("Template A", tags=["Tag1"])
    t2 = seed_template("Template B")
    suite1 = seed_suite("Suite One", [t1])
    suite2 = seed_suite("Suite Two", [t2])

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
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template("Solo Template")

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
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()

    with pytest.raises(svc.ExecutionGateError) as exc_info:
        svc.request_execution(
            edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
            treaty_names=[], template_ids=[template_id],
            currency_code="USD", currency_scheme="", currency_vintage="",
            actor_id=iteration2_db.user_a)
    assert any("currency" in e.lower() for e in exc_info.value.errors)


def test_gate_rejects_cache_invalid_vintage(iteration2_db):
    seed_currency()  # RMS/RL25 only
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()

    with pytest.raises(svc.ExecutionGateError) as exc_info:
        svc.request_execution(
            edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
            treaty_names=[], template_ids=[template_id],
            currency_code="USD", currency_scheme="RMS",
            currency_vintage="NOT-A-REAL-VINTAGE",
            actor_id=iteration2_db.user_a)
    assert any("vintage" in e.lower() for e in exc_info.value.errors)


def test_gate_rejects_edm_not_ready(iteration2_db):
    seed_currency()
    edm_id = seed_edm(status="pending_import")
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()

    with pytest.raises(svc.ExecutionGateError):
        svc.request_execution(
            edm_id=edm_id, kind="template", portfolio_ids=[portfolio_id],
            treaty_names=[], template_ids=[template_id],
            currency_code="USD", currency_scheme="RMS", currency_vintage="RL25",
            actor_id=iteration2_db.user_a)


def test_gate_rejects_suite_kind_with_zero_templates_selected(iteration2_db):
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    template_id = seed_template()
    suite_id = seed_suite("Suite One", [template_id])

    with pytest.raises(svc.ExecutionGateError) as exc_info:
        svc.request_execution(
            edm_id=edm_id, kind="suite", portfolio_ids=[portfolio_id],
            treaty_names=[],
            suite_picks=[svc.SuitePick(
                suite_id=suite_id, template_ids=[],  # every template deselected
                currency_code="USD", currency_scheme="RMS", currency_vintage="RL25")],
            actor_id=iteration2_db.user_a)
    assert any("suite" in e.lower() for e in exc_info.value.errors)


def test_gate_rejects_template_foreign_to_its_suite(iteration2_db):
    seed_currency()
    edm_id = seed_edm()
    portfolio_id = seed_portfolio(edm_id)
    t1 = seed_template("In suite")
    t2 = seed_template("Not in suite")
    suite_id = seed_suite("Suite One", [t1])

    with pytest.raises(svc.ExecutionGateError) as exc_info:
        svc.request_execution(
            edm_id=edm_id, kind="suite", portfolio_ids=[portfolio_id],
            treaty_names=[],
            suite_picks=[svc.SuitePick(
                suite_id=suite_id, template_ids=[t2],
                currency_code="USD", currency_scheme="RMS", currency_vintage="RL25")],
            actor_id=iteration2_db.user_a)
    assert any("belong" in e.lower() for e in exc_info.value.errors)
