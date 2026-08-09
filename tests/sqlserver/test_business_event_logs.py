"""Business-event log emissions (issue #28).

The logging framework slices give every record a unified format + correlation
context; these tests pin the *emission points* — the INFO lines that narrate
what the system actually did: analyst actions with the actor id, submit
successes with the RM job id, and the poller's chain enqueues. Auth-event
emissions are covered in tests/unit/test_auth_routes.py (same private-app
harness as the other auth tests).
"""

from __future__ import annotations

import logging

from app.poller import run as poller
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute

MS = sync.MemberSpec


def _build(drive, actor, edms, rdms):
    """save_package + save_and_sync a package; return its id."""
    members = [MS(kind="edm", name=n, source_file_path=str(drive / f))
               for n, f in edms]
    members += [MS(kind="rdm", name=n, source_file_path=str(drive / f))
                for n, f in rdms]
    res = sync.save_package(package_id=None, name="Pkg", members=members,
                            actor_id=actor)
    sync.save_and_sync(package_id=res.package_id, actor_id=actor)
    return res.package_id


def _messages(caplog, logger_name: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name]


def test_package_save_and_sync_log_the_analyst_action(
        iteration2_db, fake_irp, drive, caplog):
    a = iteration2_db.user_a
    with caplog.at_level(logging.INFO, logger="app.services.package_sync_service"):
        _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    msgs = _messages(caplog, "app.services.package_sync_service")
    assert any("created by analyst" in m and str(a) in m for m in msgs)
    assert any("sync requested by analyst" in m and "1 upload head(s)" in m
               for m in msgs)


def test_submit_success_logged_with_irp_id(iteration2_db, fake_irp, drive, caplog):
    _build(drive, iteration2_db.user_a,
           edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    with caplog.at_level(logging.INFO, logger="app.workers.package_jobs"):
        package_jobs.run_pending()
    msgs = _messages(caplog, "app.workers.package_jobs")
    assert any(m.startswith("import_edm submitted") and "irp_id=" in m
               for m in msgs)


def test_poller_logs_the_chained_head(iteration2_db, fake_irp, drive, caplog):
    _build(drive, iteration2_db.user_a,
           edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    package_jobs.run_pending()  # submit the import_edm
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='import_edm'",
                       {}, connection="WORKBENCH"):
        fake_irp.finish(str(row["irp_id"]))
    with caplog.at_level(logging.INFO, logger="app.poller.run"):
        poller.poll_once()
    msgs = _messages(caplog, "app.poller.run")
    assert any("chained upload_rdm head" in m for m in msgs)
