"""Opt-in GeoHaz sandbox round trip and terminal-response capture."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault(
    "SESSION_SECRET_KEY", "irp-test-secret-key-not-for-production")

from app.services import geohaz_service, irp_gateway  # noqa: E402

pytestmark = pytest.mark.irp

_TERMINAL = {"FINISHED", "FAILED", "CANCELLED"}


def _required_target(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"Set {name} to a small sandbox target before --run-irp.")
    return value


def test_geohaz_hazard_only_round_trip_captures_terminal_body():
    edm_name = _required_target("IRP_TEST_GEOHAZ_EDM_NAME")
    portfolio_name = _required_target("IRP_TEST_GEOHAZ_PORTFOLIO_NAME")
    version = os.environ.get("IRP_TEST_GEOHAZ_VERSION", "latest").strip()
    timeout = int(os.environ.get("IRP_TEST_GEOHAZ_TIMEOUT_SECS", "900"))

    submitted = irp_gateway.submit_geohaz(
        edm_name=edm_name,
        portfolio_name=portfolio_name,
        version=version,
        perils=["earthquake", "windstorm"],
        skip_prev_hazard=False,
        override_user_def=False,
    )
    layers = submitted.payload["settings"]["layers"]
    assert layers
    assert all(layer["type"] == "hazard" for layer in layers)

    deadline = time.monotonic() + timeout
    while True:
        result = irp_gateway.get_geohaz_job(submitted.irp_id)
        if result.status in _TERMINAL:
            break
        if time.monotonic() >= deadline:
            pytest.fail(
                f"GeoHaz job {submitted.irp_id} did not finish in {timeout}s.")
        time.sleep(10)

    capture_path = Path(os.environ.get(
        "IRP_TEST_GEOHAZ_CAPTURE_PATH", "/tmp/rwb-geohaz-terminal.json"))
    capture_path.write_text(
        json.dumps(result.result, indent=2, sort_keys=True), encoding="utf-8")

    assert result.status == "FINISHED", (
        f"GeoHaz job {submitted.irp_id} ended as {result.status}; "
        f"terminal body saved to {capture_path}.")
    assert geohaz_service.parse_layer_counts(
        json.dumps(result.result)) is not None, (
            "Update parse_layer_counts for the captured terminal body at "
            f"{capture_path}.")
