"""The db/connection.py query-timing hook (issue #28).

Every ``get_engine``-created engine gets before/after_cursor_execute listeners
that log statement duration at DEBUG on logger ``db.query`` — asserted here on
the real WORKBENCH engine.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from db import get_engine


def _run_select() -> None:
    with get_engine("WORKBENCH").connect() as conn:
        conn.execute(text("SELECT 1"))


def test_query_timing_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="db.query"):
        _run_select()
    records = [r for r in caplog.records if r.name == "db.query"]
    assert records
    message = records[-1].getMessage()
    assert "SELECT 1" in message
    assert "ms:" in message  # duration prefix


def test_query_timing_silent_above_debug(caplog):
    with caplog.at_level(logging.INFO, logger="db.query"):
        _run_select()
    assert [r for r in caplog.records if r.name == "db.query"] == []
