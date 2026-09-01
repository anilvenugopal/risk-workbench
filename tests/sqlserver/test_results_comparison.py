"""SQL Server route tests for the comparison page (spec 013 T-01, FR-016/P-06).

The render-path and hand-typed-URL proofs run over the REAL service against the
WORKBENCH test database; every other comparison route test is database-free and
lives in tests/unit/test_results_comparison.py, whose ``_make_app`` this reuses.
"""

from __future__ import annotations

import json
import uuid

from starlette.testclient import TestClient

from tests.unit.test_results_comparison import _make_app


class TestNoRiskModelerCallInRenderPath:
    """FR-016: the whole render reads stored extracts — a poisoned gateway
    proves no Risk Modeler call serves the page."""

    def test_render_with_gateway_unreachable(self, workbench_db):
        from app.services import irp_gateway
        from db import execute_command

        class _Poison:
            def __getattr__(self, name):
                raise AssertionError(f"Risk Modeler call attempted: {name}")

        edm = str(uuid.uuid4())
        execute_command(
            "INSERT INTO irp_edm (id, name, status) VALUES (:id, 'E', 'ready')",
            {"id": edm}, connection="WORKBENCH")
        eleven = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)
        extract = {
            "engine_type": "DLM", "engine_version": "23.0",
            "perspectives": {"RL": {
                "aal": 100.0, "std_dev": 50.0,
                "oep": {str(rp): float(rp) for rp in eleven},
                "aep": {str(rp): float(rp) for rp in eleven}}}}
        ids = []
        for name in ("A", "B"):
            analysis = str(uuid.uuid4())
            ids.append(analysis)
            execute_command(
                "INSERT INTO irp_analysis (id, edm_id, name, full_name, "
                "status_code, submitted_settings, loss_results, execution_id, "
                "execution_item_no) VALUES (:id, :edm, :n, :n, 'ready', "
                ":sub, :loss, :x, 0)",
                {"id": analysis, "edm": edm, "n": name,
                 "sub": json.dumps({"currency": {"code": "USD"}}),
                 "loss": json.dumps(extract), "x": str(uuid.uuid4())},
                connection="WORKBENCH")

        irp_gateway.configure(_Poison())
        try:
            resp = TestClient(_make_app()).get(
                f"/results/comparison?pairs={ids[0]}:{ids[1]}")
        finally:
            irp_gateway.reset()

        assert resp.status_code == 200
        assert "% Chg" in resp.text
        assert "+0.0%" in resp.text


class TestHandTypedUrls:
    """P-06/T-01 route-level proof over the real service: garbage input is a
    render, never a 500."""

    def test_garbage_pairs_render_the_empty_state(self, workbench_db):
        resp = TestClient(_make_app()).get(
            "/results/comparison?pairs=garbage:junk,notauuid")

        assert resp.status_code == 200
        assert "A pair was dropped" in resp.text
        assert "No comparisons to display" in resp.text
