"""CLI to submit a dummy_wait or dummy_fail rwb_job for manual testing (CR-004).

Usage:
    python -m app.workers.dummy_submit wait  --label test-1 --seconds 60
    python -m app.workers.dummy_submit fail  --label test-2 --message "boom"

Each call enqueues a genuinely new row (a fresh requestor_id every time —
requestor_type/requestor_id/rwb_job_type is the dedup key, so a fixed
requestor_id would make the second submission of the same type a no-op).
Prints the new rwb_job_id so you can watch it on the monitoring page or query
it directly.

See docs/WORKER_ARCHITECTURE.md for the full manual test walkthrough (queue
isolation, cancel-while-pending, drain, kill-and-restart).
"""

from __future__ import annotations

import argparse
import sys
import uuid

from app.services.rwb_job_service import enqueue_rwb_job
from app.workers import dispatch, loader


def _submit(rwb_job_type: str, input_data: dict) -> str:
    dummy_id = str(uuid.uuid4())
    job_id = enqueue_rwb_job(
        requestor_type="analyst_request",
        requestor_id=dummy_id,
        rwb_job_type=rwb_job_type,
        link_type="not_applicable",
        link_id=None,
        context_type=None,
        context_id=None,
        input_data=input_data,
    )
    if job_id is None:
        raise RuntimeError(
            "enqueue returned None — dedup hit against an existing row. "
            "This shouldn't happen with a fresh requestor_id; if you see "
            "this, something upstream is reusing a requestor_id.")
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type=rwb_job_type)
    return job_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="job_type", required=True)

    wait_parser = sub.add_parser("wait", help="submit a dummy_wait job")
    wait_parser.add_argument("--label", default="unlabeled",
                              help="shown in logs and JobResult output")
    wait_parser.add_argument("--seconds", type=int, default=30,
                              help="how long the job sleeps before succeeding")

    fail_parser = sub.add_parser("fail", help="submit a dummy_fail job")
    fail_parser.add_argument("--label", default="unlabeled",
                              help="shown in logs and JobResult output")
    fail_parser.add_argument("--message", default="dummy_fail: intentional failure",
                              help="becomes the rwb_job's error_detail")

    args = parser.parse_args()

    # discover_jobs() must run before dispatch can resolve the actor by name —
    # same startup-only import boundary as app/workers/loader.py documents.
    loader.bootstrap()

    if args.job_type == "wait":
        job_id = _submit("dummy_wait", {"label": args.label, "seconds": args.seconds})
    else:
        job_id = _submit("dummy_fail", {"label": args.label, "message": args.message})

    print(job_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
