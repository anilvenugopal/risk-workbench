"""Unit tests for the Risk Modeler metadata snapshot worker."""

from __future__ import annotations

from app.services.irp_gateway import CurrencyEntry, ModelProfileEntry
from app.workers import metadata_jobs
from db import execute


def _rows(table: str, order: str) -> list[dict]:
    return execute(f"SELECT * FROM {table} ORDER BY {order}", {},
                   connection="WORKBENCH")


def test_sync_populates_all_four_reference_tables(iteration2_db, fake_irp):
    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    assert result.output == {
        "model_profiles": 3,
        "output_profiles": 1,
        "event_rate_schemes": 1,
        "currencies": 1,
    }
    assert [row["name"] for row in _rows("irp_model_profile", "irp_id")] == [
        "RMS Default RL25", "RMS Default HD", "Open profile"]
    assert _rows("irp_output_profile", "irp_id")[0]["name"] == "RMS Default Output"
    assert _rows("irp_event_rate_scheme", "irp_id")[0]["name"] == "RMS WS"
    assert _rows("irp_currency", "code")[0]["code"] == "USD"


def test_resync_updates_names_and_removes_vanished_rows(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    original_id = _rows("irp_model_profile", "irp_id")[0]["id"]
    fake_irp.model_profiles = [
        ModelProfileEntry(1, "Renamed RL25", "RL25", "WS", "NAWS")]
    fake_irp.currencies = [CurrencyEntry("CAD", "Canadian Dollar", "Canada", "$")]

    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    profiles = _rows("irp_model_profile", "irp_id")
    assert [(row["irp_id"], row["name"]) for row in profiles] == [
        (1, "Renamed RL25")]
    assert profiles[0]["id"] == original_id
    assert [row["code"] for row in _rows("irp_currency", "code")] == ["CAD"]


def test_fetch_failure_leaves_every_cached_table_unchanged(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    before = {
        table: _rows(table, order)
        for table, order in (
            ("irp_model_profile", "irp_id"),
            ("irp_output_profile", "irp_id"),
            ("irp_event_rate_scheme", "irp_id"),
            ("irp_currency", "code"),
        )
    }
    fake_irp.model_profiles = []
    fake_irp.raise_on_reference_data = True

    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "failed"
    assert "forced reference-data failure" in result.error_detail
    assert {
        table: _rows(table, order)
        for table, order in (
            ("irp_model_profile", "irp_id"),
            ("irp_output_profile", "irp_id"),
            ("irp_event_rate_scheme", "irp_id"),
            ("irp_currency", "code"),
        )
    } == before
