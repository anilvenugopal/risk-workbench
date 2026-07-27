"""The db/connection.py query-timing hook (issue #28).

``get_engine``-created engines get before/after_cursor_execute listeners that
log statement duration at DEBUG on logger ``db.query``. Override engines
(``register_engine`` — the unit tier's SQLite) bypass engine creation, so the
listeners are exercised here on a scratch engine instrumented directly.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text

from db.connection import _attach_query_timing


def _run_select(engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def test_query_timing_logged_at_debug(caplog):
    engine = create_engine("sqlite:///:memory:")
    _attach_query_timing(engine)
    try:
        with caplog.at_level(logging.DEBUG, logger="db.query"):
            _run_select(engine)
    finally:
        engine.dispose()
    records = [r for r in caplog.records if r.name == "db.query"]
    assert records
    message = records[-1].getMessage()
    assert "SELECT 1" in message
    assert "ms:" in message  # duration prefix


def test_query_timing_silent_above_debug(caplog):
    engine = create_engine("sqlite:///:memory:")
    _attach_query_timing(engine)
    try:
        with caplog.at_level(logging.INFO, logger="db.query"):
            _run_select(engine)
    finally:
        engine.dispose()
    assert [r for r in caplog.records if r.name == "db.query"] == []
