"""Completion-chaining + fan-in idempotency (US3, T032) — the Article-2 mandate.

The A21 backbone: an ``import_edm`` reaching ``FINISHED`` makes the poller enqueue
exactly one ``upload_rdm`` head (``requestor_type='irp_job'``, keyed to that finished
import job), which fans out to one apply per RDM of THAT EDM — a per-pair fan-out gated
on the target EDM's upload, never a global head. A repeated terminal trigger (re-poll)
must never double-enqueue (SC-014).
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


def test_import_edm_finished_enqueues_one_upload_rdm_fanning_out(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak")],
                 rdms=[("R1", "rdm1.mdf"), ("R2", "rdm2.mdf")])
    package_jobs.run_pending()                 # submit the EDM import
    _finish_all_import_edm(fake_irp)
    poller.poll_once()                         # chain the upload_rdm head

    heads = execute("SELECT id, requestor_type FROM rwb_job "
                    "WHERE rwb_job_type='upload_rdm'", {}, connection="WORKBENCH")
    assert len(heads) == 1
    assert heads[0]["requestor_type"] == "irp_job"  # keyed to the finished import job

    package_jobs.run_pending()                 # fan out to one apply per RDM
    applies = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' AND package_id=:p",
        {"p": pid}, connection="WORKBENCH")
    assert applies == 2  # one per RDM of the finished EDM


def test_repeated_terminal_trigger_never_double_enqueues(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    package_jobs.run_pending()
    _finish_all_import_edm(fake_irp)
    poller.poll_once()
    poller.poll_once()  # re-poll: the import_edm is still FINISHED
    heads = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
                           {}, connection="WORKBENCH")
    assert heads == 1  # idempotent on UNIQUE(requestor_type, requestor_id, rwb_job_type)


def test_retry_after_submit_failure_keeps_package_id_so_chain_fires(
        iteration2_db, fake_irp, drive):
    """Regression: a submit-side failure followed by a retry must NOT drop the EDM's
    package_id. The poller chains the RDM applies off ``import_edm.package_id``; a null
    there makes it silently skip the chain — the finished EDM reaches ``ready`` but no
    ``upload_rdm`` is ever enqueued (and thus no ``import_rdm``)."""
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])

    # First upload_edm submit never reaches Risk Modeler → SUBMISSION FAILED + EDM error.
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()
    edm_id = execute("SELECT id FROM irp_edm", {}, connection="WORKBENCH")[0]["id"]

    # Analyst retries the failed EDM; the resubmit must carry the package_id forward.
    fake_irp.raise_on_submit = False
    edm_service.retry_import(edm_id=edm_id, actor_id=a)
    package_jobs.run_pending()                 # resubmit — now succeeds
    _finish_all_import_edm(fake_irp)
    poller.poll_once()                         # chain the upload_rdm head

    finished = execute(
        "SELECT package_id FROM irp_job "
        "WHERE irp_job_type='import_edm' AND status='FINISHED'",
        {}, connection="WORKBENCH")
    assert finished[0]["package_id"] is not None  # the root cause: must stay scoped
    heads = execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
        {}, connection="WORKBENCH")
    assert heads == 1
    package_jobs.run_pending()
    applies = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' AND package_id=:p",
        {"p": pid}, connection="WORKBENCH")
    assert applies == 1


def test_poller_dispatches_the_chained_upload_rdm_head(
        iteration2_db, fake_irp, drive):
    """Regression: the poller runs in its own process, so it must itself deliver the
    heads it enqueues (``upload_rdm`` on ``import_edm`` FINISHED). Without the poller's
    dispatch sweep the row sits ``pending`` forever — no worker is ever woken — and the
    chain stalls with no ``import_rdm``."""
    a = iteration2_db.user_a
    sent: list[str] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(rwb_job_type))
    try:
        _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
        package_jobs.run_pending()             # submit the EDM import
        _finish_all_import_edm(fake_irp)
        sent.clear()                           # ignore the request-path upload_edm dispatch
        poller.poll_once()                     # enqueue the upload_rdm head AND deliver it
        assert "upload_rdm" in sent
    finally:
        dispatch.reset()


def test_per_pair_fanout_across_multiple_edms(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    pid = _build(drive, a, edms=[("E1", "edm1.bak"), ("E2", "edm2.bak")],
                 rdms=[("R1", "rdm1.mdf"), ("R2", "rdm2.mdf")])
    package_jobs.run_pending()                 # two import_edm submits
    _finish_all_import_edm(fake_irp)
    poller.poll_once()                         # one upload_rdm head per finished EDM
    heads = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
                           {}, connection="WORKBENCH")
    assert heads == 2  # one per EDM — gated on its own upload, not a global head
    package_jobs.run_pending()
    applies = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' AND package_id=:p",
        {"p": pid}, connection="WORKBENCH")
    assert applies == 4  # 2 EDMs × 2 RDMs — one apply per pair (SC-006)


def test_correlation_id_spans_the_whole_chain(iteration2_db, fake_irp, drive):
    """Issue #28 acceptance: ONE correlation id, stamped by the request-scoped
    context at save-and-sync time, is carried across every hop — request-path
    enqueue (upload_edm) → worker submit (import_edm irp_job) → poller chaining
    (upload_rdm head) → fan-out worker (import_rdm irp_job) → poller chaining
    again (backfill_rdm_analyses head). Grep that one id → the full lifecycle."""
    a = iteration2_db.user_a
    token = log_context.bind(correlation_id="chain-e2e")  # what the middleware does
    try:
        _build(drive, a, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")])
    finally:
        log_context.clear(token)

    package_jobs.run_pending()                 # worker: submit import_edm
    _finish_all_import_edm(fake_irp)
    poller.poll_once()                         # poller: chain the upload_rdm head
    package_jobs.run_pending()                 # worker: fan out → submit import_rdm
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type='import_rdm'",
                       {}, connection="WORKBENCH"):
        fake_irp.finish(str(row["irp_id"]))
    poller.poll_once()                         # poller: chain backfill_rdm_analyses
    package_jobs.run_pending()                 # worker: run the backfill

    rwb = execute("SELECT rwb_job_type, correlation_id FROM rwb_job", {},
                  connection="WORKBENCH")
    irp = execute("SELECT irp_job_type, correlation_id FROM irp_job", {},
                  connection="WORKBENCH")
    assert {r["rwb_job_type"] for r in rwb} == {
        "upload_edm", "upload_rdm", "backfill_rdm_analyses"}
    assert {r["irp_job_type"] for r in irp} == {"import_edm", "import_rdm"}
    assert {r["correlation_id"] for r in rwb} == {"chain-e2e"}
    assert {r["correlation_id"] for r in irp} == {"chain-e2e"}
