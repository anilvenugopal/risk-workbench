from __future__ import annotations

import pytest

from app.services.geohaz_service import completion_summary


def _body(summary) -> dict:
    return {
        "status": "FINISHED",
        "details": {"summary": "GEOHAZ is successful"},
        "tasks": [{
            "name": "HAZARD",
            "output": {"summary": summary},
        }],
    }


def test_completion_summary_returns_task_output_summary():
    summary = (
        "For the Layer : EARTHQUAKE, with version 25.0 processed 142 Locations "
        "out of 142.For the Layer : WINDSTORM, with version 25.0 processed 0 "
        "Locations out of 142."
    )
    assert completion_summary(_body(summary)) == summary


@pytest.mark.parametrize("body", [
    None,
    {},
    {"status": "FINISHED"},
    {"details": {"summary": "GEOHAZ is successful"}},
    _body(None),
    _body(""),
])
def test_completion_summary_returns_none_when_summary_is_unavailable(body):
    assert completion_summary(body) is None
