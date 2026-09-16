"""Close a failed analysis (spec 014 T055, P-22): the route, the refusals it
shares with Retry, and what a closed row then offers.

Harness: the TestClient of tests/unit/export_client.py, as in test_export_routes.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

from db import execute_command, execute_one
from tests.unit.export_client import csrf, make_client, make_deal, make_export


@pytest.fixture()
def client(iteration2_db, loss_db) -> TestClient:
    return make_client(iteration2_db, loss_db)


@pytest.fixture()
def deal(client):
    return make_deal(client)


@pytest.fixture()
def export(client, deal):
    return make_export(client, deal)


def _fail(analysis_id: str, message: str = "Analysis not found") -> None:
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed', "
        "error_message = :e WHERE irp_analysis_id = :a",
        {"e": message, "a": analysis_id}, connection="LOSS")


def _close(client, export, analysis_id, *, query="", htmx=True):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(f"{export['url']}/analyses/{analysis_id}/close{query}",
                       data={"csrf_token": csrf()}, headers=headers)


def _manifest(analysis_id: str) -> dict:
    return execute_one(
        "SELECT * FROM stage.rwb_loss_result_manifest WHERE irp_analysis_id = :a",
        {"a": analysis_id}, connection="LOSS")


def test_close_stamps_the_row_and_rerenders_the_section_without_either_action(client, export):
    _fail(export["b"])

    response = _close(client, export, export["b"])

    assert response.status_code == 200
    assert 'id="submission-exports"' in response.text
    row = _manifest(export["b"])
    assert row["closed_at"] is not None and row["closed_by"] == "analyst.a@example.com"
    assert ">closed</span>" in response.text
    assert "Retry</button>" not in response.text and "Close</button>" not in response.text
    # the error stays on the row, with who closed it beside it
    assert "Analysis not found" in response.text
    assert "analyst.a@example.com</div>" in response.text


def test_closing_the_last_open_analysis_stops_the_polling(client, export):
    execute_command("UPDATE stage.rwb_loss_result_manifest SET stage_status = 'staged', "
                    "load_status = 'loaded', data_id = 4127 WHERE irp_analysis_id = :a",
                    {"a": export["a"]}, connection="LOSS")
    _fail(export["b"])

    response = _close(client, export, export["b"])

    assert 'hx-trigger="every 10s"' not in response.text


def test_close_keeps_the_filter_in_force(client, export):
    _fail(export["b"])

    response = _close(client, export, export["b"], query="?status=failed")

    assert '<option value="failed" selected>Failed</option>' in response.text
    # closed is no longer failed, so the Failed filter no longer holds the row
    assert "No analyses match this filter." in response.text


def test_close_is_refused_on_a_loaded_an_in_progress_and_an_already_closed_row(client, export):
    execute_command("UPDATE stage.rwb_loss_result_manifest SET stage_status = 'staged', "
                    "load_status = 'loaded', data_id = 4127 WHERE irp_analysis_id = :a",
                    {"a": export["a"]}, connection="LOSS")

    loaded = _close(client, export, export["a"])
    assert loaded.status_code == 409
    assert "Close refused: already loaded as data ID 4127." in loaded.text
    assert _manifest(export["a"])["closed_at"] is None

    queued = _close(client, export, export["b"])
    assert queued.status_code == 409
    assert 'id="submission-exports"' in queued.text  # the refusal banners in the section
    assert "Close refused: the analysis is queued, not failed." in queued.text

    _fail(export["b"])
    assert _close(client, export, export["b"]).status_code == 200
    again = _close(client, export, export["b"])
    assert again.status_code == 409
    assert "Close refused: the analysis is closed, not failed." in again.text


def test_retry_is_refused_on_a_closed_row(client, export):
    _fail(export["b"])
    _close(client, export, export["b"])

    response = client.post(f"{export['url']}/analyses/{export['b']}/retry",
                           data={"csrf_token": csrf()}, headers={"HX-Request": "true"})

    assert response.status_code == 409
    assert "Retry refused: the analysis is closed, not failed." in response.text


def test_close_404s_for_an_analysis_outside_this_export(client, export):
    assert _close(client, export, str(uuid.uuid4())).status_code == 404


def test_close_without_a_valid_csrf_token_changes_nothing(client, export):
    _fail(export["b"])

    response = client.post(f"{export['url']}/analyses/{export['b']}/close",
                           data={"csrf_token": "not-a-token"})

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/submissions/{export['submission_id']}#submission-exports")
    assert _manifest(export["b"])["closed_at"] is None
