"""Unit tests for the Risk Modeler metadata snapshot worker."""

from __future__ import annotations

from app.services.irp_gateway import (
    CurrencyEntry,
    CurrencySchemeEntry,
    CurrencySchemeVintageEntry,
    EventRateSchemeEntry,
    ModelProfileEntry,
)
from app.services.template_service import set_scheme_visibility
from app.workers import metadata_jobs
from db import execute


def _rows(table: str, order: str) -> list[dict]:
    return execute(f"SELECT * FROM {table} ORDER BY {order}", {},
                   connection="WORKBENCH")


def test_sync_populates_all_six_reference_tables(workbench_db, fake_irp):
    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    assert result.output == {
        "model_profiles": 3,
        "output_profiles": 1,
        "event_rate_schemes": 1,
        "currencies": 1,
        "currency_schemes": 2,
        "currency_scheme_vintages": 3,
    }
    assert [row["name"] for row in _rows("irp_model_profile", "irp_id")] == [
        "RMS Default RL25", "RMS Default HD", "Open profile"]
    assert _rows("irp_output_profile", "irp_id")[0]["name"] == "RMS Default Output"
    assert _rows("irp_event_rate_scheme", "irp_id")[0]["name"] == "RMS WS"
    assert _rows("irp_currency", "code")[0]["code"] == "USD"
    assert [row["code"] for row in _rows("irp_currency_scheme", "irp_id")] == [
        "RMS", "DT"]
    assert sorted(
        (row["vintage"], row["currency_scheme_code"])
        for row in _rows("irp_currency_scheme_vintage", "vintage")
    ) == [("RL23", "RMS"), ("RL24", "DT"), ("RL25", "RMS")]


def test_resync_updates_names_and_removes_vanished_rows(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    original_id = _rows("irp_model_profile", "irp_id")[0]["id"]
    fake_irp.model_profiles = [
        ModelProfileEntry(1, "Renamed RL25", "RL25", "WS", "NAWS")]
    fake_irp.currencies = [CurrencyEntry("CAD", "Canadian Dollar", "Canada", "$")]
    fake_irp.currency_schemes = [CurrencySchemeEntry(30, "RMS Scheme", "RMS")]

    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    profiles = _rows("irp_model_profile", "irp_id")
    assert [(row["irp_id"], row["name"]) for row in profiles] == [
        (1, "Renamed RL25")]
    assert profiles[0]["id"] == original_id
    assert [row["code"] for row in _rows("irp_currency", "code")] == ["CAD"]
    assert [row["code"] for row in _rows("irp_currency_scheme", "irp_id")] == [
        "RMS"]


def test_resync_preserves_workbench_scheme_visibility(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    set_scheme_visibility(20, False)
    fake_irp.event_rate_schemes = [
        EventRateSchemeEntry(20, "RMS WS renamed", "WS", "NAWS", "25.0", False)]

    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    scheme = _rows("irp_event_rate_scheme", "irp_id")[0]
    assert scheme["name"] == "RMS WS renamed"
    assert scheme["workbench_is_active"] == 0


def test_resync_replaces_currency_scheme_vintages_wholesale_including_duplicates(
    workbench_db, fake_irp,
):
    # Two duplicate (scheme, vintage) pairs — the API allows it (R13) and the
    # cache stores exactly what came back, never de-duplicated.
    fake_irp.currency_scheme_vintages = [
        CurrencySchemeVintageEntry("RL25", "RMS", "2025-05-28T00:00:00.000Z"),
        CurrencySchemeVintageEntry("RL25", "RMS", "2025-05-28T00:00:00.000Z"),
    ]
    metadata_jobs._sync_irp_metadata_body()
    assert len(_rows("irp_currency_scheme_vintage", "vintage")) == 2

    # A resync with a wholly different vintage set replaces every row — no
    # keyed upsert is possible since the API item carries no id.
    fake_irp.currency_scheme_vintages = [
        CurrencySchemeVintageEntry("RL26", "RMS", "2026-05-28T00:00:00.000Z"),
    ]
    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    rows = _rows("irp_currency_scheme_vintage", "vintage")
    assert [(row["vintage"], row["currency_scheme_code"]) for row in rows] == [
        ("RL26", "RMS")]


def test_sync_truncates_legacy_currency_names_to_risk_modeler_limit(
    workbench_db, fake_irp,
):
    fake_irp.currencies = [
        CurrencyEntry("LEG", "12345678901234567", "United States", "$")
    ]

    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "succeeded"
    assert _rows("irp_currency", "code")[0]["name"] == "1234567890123456"


_CACHE_TABLE_ORDERS = (
    ("irp_model_profile", "irp_id"),
    ("irp_output_profile", "irp_id"),
    ("irp_event_rate_scheme", "irp_id"),
    ("irp_currency", "code"),
    ("irp_currency_scheme", "irp_id"),
    ("irp_currency_scheme_vintage", "vintage"),
)


def test_fetch_failure_leaves_every_cached_table_unchanged(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    before = {table: _rows(table, order) for table, order in _CACHE_TABLE_ORDERS}
    fake_irp.model_profiles = []
    fake_irp.raise_on_reference_data = True

    result = metadata_jobs._sync_irp_metadata_body()

    assert result.status == "failed"
    assert "forced reference-data failure" in result.error_detail
    assert {
        table: _rows(table, order) for table, order in _CACHE_TABLE_ORDERS
    } == before
