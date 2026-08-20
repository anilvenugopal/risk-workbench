"""Business-event log emissions for entity imports."""

from __future__ import annotations

import logging

from app.services import edm_service
from app.workers import entity_jobs


def test_submit_success_logged_with_irp_id(iteration2_db, fake_irp, drive, caplog):
    edm_service.import_edm(
        name="E1", source_file_path=str(drive / "edm1.bak"),
        actor_id=iteration2_db.user_a,
    )
    with caplog.at_level(logging.INFO, logger="app.workers.entity_jobs"):
        entity_jobs.run_pending()
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.workers.entity_jobs"
    ]
    assert any(message.startswith("import_edm submitted") and "irp_id=" in message
               for message in messages)
