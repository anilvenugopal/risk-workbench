"""Completion-chaining + fan-in idempotency (US3, T032) — the Article-2 mandate.

Save-and-sync enqueues an ``upload_edm`` head per EDM and an ``upload_rdm`` head per
RDM in the same pass: the RDM imports standalone, so no RDM work waits on an EDM and
the poller chains none. What the poller still chains off a terminal ``irp_job`` is the
backfills — ``backfill_edm_detail`` on ``import_edm`` FINISHED, ``backfill_rdm_analyses``
on ``import_rdm`` FINISHED — and a repeated terminal trigger (re-poll) must never
double-enqueue (SC-014).
"""

from __future__ import annotations

from app import log_context
from app.poller import run as poller
from app.services import edm_service
from app.services import package_sync_service as sync
from app.workers import dispatch, package_jobs
from db import execute, execute_scalar

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


def _finish_all_import_edm(fake):
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='import_edm'",
                       {}, connection="WORKBENCH"):
        fake.finish(str(row["irp_id"]))


def test_sync_submits_edm_and_rdm_imports_together(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak")],
                 rdms=[("R1", "rdm1.mdf"), ("R2", "rdm2.mdf")])
    heads = execute("SELECT rwb_job_type, requestor_type FROM rwb_job "
                    "WHERE rwb_job_type IN ('upload_edm', 'upload_rdm')",
                    {}, connection="WORKBENCH")
    assert len(heads) == 3                     # 1 EDM + 2 RDMs, enqueued at sync
    assert {h["requestor_type"] for h in heads} == {"analyst_request"}

    package_jobs.run_pending()                 # one pass submits all three
    jobs = execute(
        "SELECT irp_job_type FROM irp_job WHERE package_id=:p", {"p": pid},
        connection="WORKBENCH")
    kinds = [j["irp_job_type"] for j in jobs]
    assert kinds.count("import_edm") == 1
    assert kinds.count("import_rdm") == 2      # never waited on the EDM


def test_repeated_terminal_trigger_never_double_enqueues(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    package_jobs.run_pending()
    _finish_all_import_edm(fake_irp)
    poller.poll_once()
    poller.poll_once()  # re-poll: the import_edm is still FINISHED
    heads = execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='backfill_edm_detail'",
        {}, connection="WORKBENCH")
    assert heads == 1  # idempotent on UNIQUE(requestor_type, requestor_id, rwb_job_type)


def test_retry_after_submit_failure_keeps_package_id(iteration2_db, fake_irp, drive):
    """Regression: a submit-side failure followed by a retry must NOT drop the EDM's
    package_id. Everything scoped to the package — the card's job counts, the delete
    finalize — reads ``irp_job.package_id``, so a null there orphans the job."""
    a = iteration2_db.user_a
    _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])

    # First upload_edm submit never reaches Risk Modeler → SUBMISSION FAILED + EDM error.
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()
    edm_id = execute("SELECT id FROM irp_edm", {}, connection="WORKBENCH")[0]["id"]

    # Analyst retries the failed EDM; the resubmit must carry the package_id forward.
    fake_irp.raise_on_submit = False
    edm_service.retry_import(edm_id=edm_id, actor_id=a)
    package_jobs.run_pending()                 # resubmit — now succeeds
    _finish_all_import_edm(fake_irp)
    poller.poll_once()

    finished = execute(
        "SELECT package_id FROM irp_job "
        "WHERE irp_job_type='import_edm' AND status='FINISHED'",
        {}, connection="WORKBENCH")
    assert finished[0]["package_id"] is not None  # the root cause: must stay scoped


def test_poller_dispatches_the_chained_backfill_head(iteration2_db, fake_irp, drive):
    """Regression: the poller runs in its own process, so it must itself deliver the
    heads it enqueues (``backfill_edm_detail`` on ``import_edm`` FINISHED). Without the
    poller's dispatch sweep the row sits ``pending`` forever — no worker is ever woken —
    and the EDM's portfolio/treaty detail never lands."""
    a = iteration2_db.user_a
    sent: list[str] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(rwb_job_type))
    try:
        _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
        package_jobs.run_pending()             # submit the EDM import
        _finish_all_import_edm(fake_irp)
        sent.clear()                           # ignore the request-path dispatches
        poller.poll_once()                     # enqueue the backfill head AND deliver it
        assert "backfill_edm_detail" in sent
    finally:
        dispatch.reset()


def test_members_import_independently(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak"), ("E2", "edm2.bak")],
                 rdms=[("R1", "rdm1.mdf"), ("R2", "rdm2.mdf")])
    package_jobs.run_pending()                 # one pass covers every member
    kinds = [j["irp_job_type"] for j in execute(
        "SELECT irp_job_type FROM irp_job WHERE package_id=:p", {"p": pid},
        connection="WORKBENCH")]
    # 2 EDMs + 2 RDMs — four imports, not the old 2×2 apply grid (SC-006 superseded).
    assert kinds.count("import_edm") == 2
    assert kinds.count("import_rdm") == 2


def test_correlation_id_spans_the_whole_chain(iteration2_db, fake_irp, drive):
    """Issue #28 acceptance: ONE correlation id, stamped by the request-scoped
    context at save-and-sync time, is carried across every hop — request-path
    enqueue (upload_edm + upload_rdm) → worker submits (import_edm / import_rdm
    irp_job) → poller chaining (backfill_edm_detail + backfill_rdm_analyses heads).
    Grep that one id → the full lifecycle."""
    a = iteration2_db.user_a
    token = log_context.bind(correlation_id="chain-e2e")  # what the middleware does
    try:
        _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    finally:
        log_context.clear(token)

    package_jobs.run_pending()                 # worker: submit import_edm + import_rdm
    _finish_all_import_edm(fake_irp)
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='import_rdm'",
                       {}, connection="WORKBENCH"):
        fake_irp.finish(str(row["irp_id"]))
    poller.poll_once()                         # poller: chain both backfill heads
    package_jobs.run_pending()                 # worker: run the backfills

    rwb = execute("SELECT rwb_job_type, correlation_id FROM rwb_job", {},
                  connection="WORKBENCH")
    irp = execute("SELECT irp_job_type, correlation_id FROM irp_job", {},
                  connection="WORKBENCH")
    assert {r["rwb_job_type"] for r in rwb} == {
        "upload_edm", "upload_rdm", "backfill_edm_detail", "backfill_rdm_analyses"}
    assert {r["irp_job_type"] for r in irp} == {"import_edm", "import_rdm"}
    assert {r["correlation_id"] for r in rwb} == {"chain-e2e"}
    assert {r["correlation_id"] for r in irp} == {"chain-e2e"}
