"""Business-event log emissions for entity imports and breakouts."""

from __future__ import annotations

import logging

from app.services import edm_service
from app.workers import entity_jobs


def _messages(caplog, logger_name: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name]


def test_submit_success_logged_with_irp_id(workbench_db, fake_irp, drive, caplog):
    edm_service.import_edm(
        name="E1", source_file_path=str(drive / "edm1.bak"),
        actor_id=workbench_db.user_a,
    )
    with caplog.at_level(logging.INFO, logger="app.workers.entity_jobs"):
        entity_jobs.run_pending()
    messages = _messages(caplog, "app.workers.entity_jobs")
    assert any(message.startswith("import_edm submitted") and "irp_id=" in message
               for message in messages)


def test_breakout_request_and_run_emit_business_events(
        workbench_db, fake_irp, caplog):
    # Spec 005 US3 (T052/T054 — FR-015/P-08): the confirm logs the analyst
    # request with the sub-portfolio count; the worker logs each
    # sub-portfolio's created/adopted/failed line and the completion summary,
    # every line carrying the actor id from input_data.
    from app.services import breakout_service
    from app.workers import portfolio_jobs
    from tests.sqlserver.breakout_rows import (
        AS_OF,
        RM_STAMP,
        mk_edm,
        mk_portfolio,
    )

    edm_id = mk_edm()
    pid = mk_portfolio(edm_id)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    fake_irp.selection_by_value = {"EQ Comm": [1]}   # FLD Comm → zero-match
    a = workbench_db.user_a

    with caplog.at_level(logging.INFO, logger="app.services.breakout_service"):
        jid = breakout_service.request_breakout(edm_id, pid, "lob", AS_OF,
                                                a).job_id
    msgs = _messages(caplog, "app.services.breakout_service")
    assert any(f"breakout lob requested for portfolio {pid}" in m
               and str(a) in m and "n_sub_portfolios=2" in m for m in msgs)

    with caplog.at_level(logging.INFO, logger="app.workers.portfolio_jobs"):
        assert portfolio_jobs.run_one(rwb_job_id=jid,
                                      rwb_job_type="run_breakout_lob")
    wmsgs = _messages(caplog, "app.workers.portfolio_jobs")
    assert any("usfl_commercial - EQ Comm created" in m and str(a) in m
               for m in wmsgs)
    assert any("usfl_commercial - FLD Comm failed" in m
               and "zero accounts" in m and str(a) in m for m in wmsgs)
    assert any("breakout lob completed" in m and str(a) in m
               and "1 created" in m and "1 failed of 2 planned" in m
               for m in wmsgs)
