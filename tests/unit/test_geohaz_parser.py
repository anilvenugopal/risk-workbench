from __future__ import annotations

import json

import pytest

from app.services.geohaz_service import parse_layer_counts


def _body(layers) -> str:
    return json.dumps({
        "status": "FINISHED",
        "details": {"summary": {"layers": layers}},
    })


def test_parse_layer_counts_returns_each_layer_count():
    assert parse_layer_counts(_body([
        {"name": "earthquake", "locationsLookedUp": 18},
        {"name": "windstorm", "locationsLookedUp": 12},
    ])) == {"earthquake": 18, "windstorm": 12}


def test_parse_layer_counts_keeps_zero_as_a_value():
    assert parse_layer_counts(_body([
        {"name": "earthquake", "locationsLookedUp": 0},
    ])) == {"earthquake": 0}


@pytest.mark.parametrize("body", [
    None,
    "",
    "not json",
    json.dumps({"status": "FINISHED"}),
    json.dumps({"details": {"summary": {"layers": []}}}),
    _body([
        {"name": "earthquake", "locationsLookedUp": 18},
        {"name": "windstorm"},
    ]),
    _body([{"name": "earthquake", "locationsLookedUp": True}]),
])
def test_parse_layer_counts_returns_none_for_missing_or_malformed_detail(body):
    assert parse_layer_counts(body) is None
